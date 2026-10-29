"""Shared leases and independent browser flow control; no camera or NPU."""

from contextlib import ExitStack
from copy import deepcopy
import threading
import unittest

from starlette.applications import Starlette
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from radxa_duck.vision_observer import VisionObserver, _frame_header, create_observer_routes
from radxa_duck.vision_service import PreviewFrame, VisionServiceError
from test_vision_routes import FakeVision


ORIGIN = {'origin': 'http://testserver'}


class VisionObserverTests(unittest.TestCase):
    def setUp(self):
        self.raw = FakeVision()
        self.service = VisionObserver(self.raw)
        self.addCleanup(self.service.close)

    def app(self, ack_timeout=1.0):
        app = Starlette(routes=create_observer_routes({'http://testserver'}, ack_timeout=ack_timeout))
        app.state.vision_observer = self.service
        return app

    def connect(self, client):
        return client.websocket_connect('/api/vision/observe', headers=ORIGIN)

    @staticmethod
    def until(socket, predicate):
        for _ in range(15):
            value = socket.receive_json()
            if predicate(value):
                return value
        raise AssertionError('No matching observer message')

    def command(self, socket, kind, request_id):
        socket.send_json({'type': 'preview.' + kind, 'request_id': request_id})
        return self.until(socket, lambda v: v.get('request_id') == request_id)

    def frame(self, socket):
        value = self.until(socket, lambda v: v['type'] == 'frame')
        jpeg = socket.receive_bytes()
        self.assertEqual(len(jpeg), value['jpeg_bytes'])
        return value

    @staticmethod
    def ack(socket, frame, displayed=True):
        socket.send_json({'type': 'ack', 'subscription_id': frame['subscription_id'],
                          'frame_id': frame['frame_id'], 'displayed': displayed})

    def test_read_only_subscription_and_disconnect_never_start_camera(self):
        with TestClient(self.app()) as client:
            self.assertTrue(client.get('/api/vision/observer/status').json()['available'])
            with self.connect(client) as socket:
                state = socket.receive_json()
                self.assertFalse(state['camera_active'])
                self.assertFalse(state['preview_owned'])
            self.assertEqual(self.raw.start_calls, [])

    def test_shared_leases_use_one_camera_and_last_release_stops_capture(self):
        self.service.acquire('brain', detection=True)
        self.service.acquire('browser-a')
        self.service.acquire('browser-b')
        owner = self.raw.owner
        self.assertEqual(len(self.raw.start_calls), 1)
        self.service.release('browser-a')
        self.assertEqual(self.raw.owner, owner)
        self.service.release('brain')
        self.assertEqual(self.raw.owner, owner)
        self.service.release('browser-b')
        self.assertIsNone(self.raw.owner)

    def test_two_preview_owners_disconnect_independently_and_deduplicate_requests(self):
        with TestClient(self.app()) as client:
            with self.connect(client) as first:
                first.receive_json()
                response = self.command(first, 'start', 'start-once')
                self.assertTrue(response['preview_owned'])
                with self.connect(client) as second:
                    self.assertFalse(second.receive_json()['preview_owned'])
                    self.assertTrue(self.command(second, 'start', 'second-start')['preview_owned'])
                    self.assertEqual(self.command(first, 'start', 'start-once'), response)
                    self.assertEqual(len(self.raw.start_calls), 1)
                self.assertIsNotNone(self.raw.owner)
                self.assertTrue(self.service.snapshot()['camera_active'])
            self.assertTrue(self.raw.stopped.wait(1))
            self.assertIsNone(self.raw.owner)
            with self.connect(client) as reconnected:
                self.assertFalse(reconnected.receive_json()['preview_owned'])
                self.assertIsNone(self.raw.owner)

    def test_observer_close_and_preview_stop_do_not_release_brain_lease(self):
        self.service.acquire('brain', detection=True)
        with TestClient(self.app()) as client, self.connect(client) as socket:
            initial = socket.receive_json()
            self.assertTrue(initial['camera_active'])
            self.command(socket, 'start', 'borrow-camera')
            state = self.command(socket, 'stop', 'release-camera')
            self.assertTrue(state['camera_active'])
            self.assertFalse(state['preview_owned'])
        self.assertIsNotNone(self.raw.owner)
        self.assertTrue(self.service.snapshot('brain')['preview_owned'])

    def test_slow_observer_does_not_block_newest_frame_for_another_subscriber(self):
        self.service.acquire('brain')
        with TestClient(self.app()) as client, self.connect(client) as slow, self.connect(client) as fast:
            slow.receive_json()
            fast.receive_json()
            self.raw.publish(1)
            slow_frame, fast_frame = self.frame(slow), self.frame(fast)
            self.ack(fast, fast_frame)
            self.raw.publish(2)
            self.raw.publish(3)
            latest = self.frame(fast)
            self.assertEqual(latest['frame_id'], fast_frame['frame_id'] + 1)
            self.ack(fast, latest)
            self.ack(slow, slow_frame, displayed=False)
            newest_slow = self.frame(slow)
            self.assertEqual(newest_slow['frame_id'], slow_frame['frame_id'] + 1)
            self.ack(slow, newest_slow)
            self.assertEqual(len(self.raw.start_calls), 1)

    def test_ack_timeout_releases_only_subscription_lease(self):
        self.service.acquire('brain')
        with TestClient(self.app(ack_timeout=.05)) as client, self.connect(client) as socket:
            socket.receive_json()
            self.command(socket, 'start', 'temporary')
            self.raw.publish(1)
            self.frame(socket)
            with self.assertRaises(WebSocketDisconnect):
                self.until(socket, lambda _: False)
        self.assertIsNotNone(self.raw.owner)
        self.assertEqual(self.service._leases, {'brain'})

    def test_restart_discards_pending_frame_and_frame_ids_never_reset(self):
        with TestClient(self.app()) as client, self.connect(client) as socket:
            socket.receive_json()
            self.command(socket, 'start', 'start-first')
            self.raw.publish(1)
            old = self.frame(socket)
            stopped = self.command(socket, 'stop', 'stop-first')
            self.assertFalse(stopped['camera_active'])
            self.command(socket, 'start', 'start-second')
            self.raw.publish(1)
            current = self.frame(socket)
            self.assertGreater(current['frame_id'], old['frame_id'])
            self.ack(socket, old)
            self.ack(socket, current)

    def test_legacy_capture_cannot_be_taken_over_and_maintenance_drops_leases(self):
        self.raw.start('legacy-owner')
        with self.assertRaises(VisionServiceError):
            self.service.acquire('browser')
        self.assertEqual(self.raw.owner, 'legacy-owner')
        self.raw.stop('legacy-owner')
        self.service.acquire('brain')
        self.service.suspend()
        self.assertFalse(self.service.snapshot()['available'])
        self.assertEqual(self.service._leases, set())
        self.assertIsNone(self.raw.owner)
        self.service.resume()
        self.assertTrue(self.service.snapshot()['available'])
        self.assertIsNone(self.raw.owner)

    def test_origin_limits_malformed_commands_and_duplicate_id_conflicts(self):
        with TestClient(self.app()) as client:
            for headers in ({}, {'origin': 'http://other.invalid'}):
                with self.assertRaises(WebSocketDisconnect), client.websocket_connect('/api/vision/observe', headers=headers):
                    pass
            with ExitStack() as stack:
                sockets = [stack.enter_context(self.connect(client)) for _ in range(4)]
                for socket in sockets:
                    socket.receive_json()
                with self.assertRaises(WebSocketDisconnect), self.connect(client):
                    pass
                socket = sockets[0]
                for raw in ('[]', '{"type":"preview.start","request_id":"x","request_id":"y"}', '{"type":"start"}'):
                    socket.send_text(raw)
                    self.assertEqual(self.until(socket, lambda v: v['type'] == 'error')['code'], 'invalid_request')
                self.command(socket, 'start', 'operation')
                conflict = self.command(socket, 'stop', 'operation')
                self.assertEqual(conflict['code'], 'conflict')
                self.assertTrue(self.service.snapshot()['camera_active'])

    def test_metadata_validation_rejects_invalid_geometry_and_nonfinite_values(self):
        self.service.acquire('brain')
        raw = self.raw.publish(1, result={'detections': [{'label': 'person', 'score': .9, 'box_xyxy': [1, 2, 100, 200]}]})
        valid = _frame_header(raw, 'subscriber', 1)
        self.assertEqual(valid['detections'][0]['label'], 'person')
        for field, value in (('score', float('nan')), ('score', True), ('label', '\ud800'),
                             ('box_xyxy', [1, 2, 700, 300]), ('box_xyxy', [2, 2, 1, 3])):
            header = deepcopy(raw.header)
            header['result']['detections'][0][field] = value
            with self.subTest(field=field), self.assertRaises(VisionServiceError):
                _frame_header(PreviewFrame(header, raw.jpeg), 'subscriber', 1)


if __name__ == '__main__':
    unittest.main()
