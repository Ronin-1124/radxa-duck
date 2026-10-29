"""New console through server lifespan, with real stores and simulated devices."""

from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from radxa_duck.app_service import AppService
from radxa_duck.cloud_config import CloudConfigStore
from radxa_duck.identity import DEFAULT_PROFILE
from radxa_duck.server import create_app
from test_app_service import Capture, Output
from test_brain import ContextCloud
from test_server import FakeVision


WRITE = {'Origin': 'http://testserver', 'X-Radxa-Duck-Request': '1'}


class ConsoleRouteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.mic, self.camera = Capture(), FakeVision()
        self.mic.close = lambda: None
        self.store = CloudConfigStore(self.root)
        self.store.save({'base_url': 'https://example.org/v1', 'model': 'test', 'api_key': 'private-test-key'}, expected_revision=0)
        self.cloud = ContextCloud()
        manager = SimpleNamespace(cloud=self.store, device=None, close=self.store.close)
        def apps(speech, vision, **kwargs):
            return AppService(speech, vision, cloud=self.cloud, cloud_config=self.store,
                              output=Output(speech), context_provider=kwargs['context_provider'])
        self.app = create_app(Path('model'), Path('vad'), origins=('http://testserver',),
                              service_factory=lambda *_: self.mic,
                              vision_service_factory=lambda *a, **kw: self.camera,
                              management_state_dir=self.root, management_factory=lambda *_: manager,
                              apps_factory=apps)
        self.client = TestClient(self.app).__enter__()
        self.addCleanup(self.cleanup)
        self.counter = 0
        self.wait(lambda s: s['state'] != 'starting')

    def cleanup(self):
        self.cloud.release.set()
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def command(self, action, **extra):
        self.counter += 1
        status = self.client.get('/api/brain/status').json()
        return self.client.post('/api/brain/control', json={'request_id': f'http-{self.counter}', 'action': action,
                                'expected_instance_id': status['instance_id'], 'expected_revision': status['revision'], **extra},
                                headers=WRITE)

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = self.client.get('/api/brain/status').json()
            if predicate(state):
                return state
            threading.Event().wait(.01)
        self.fail(str(state))

    def test_new_reads_match_contract_without_starting_capture_or_cloud(self):
        for path, packet_type in (('/api/brain/status', 'brain_status'), ('/api/identity', 'identity_config'),
                                  ('/api/vision/observer/status', 'vision_observer_info')):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['schema_version'], 1)
            self.assertEqual(response.json()['type'], packet_type)
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertEqual(response.headers['content-type'], 'application/json')
            self.assertNotIn('private-test-key', response.text)
        self.assertIsNone(self.mic.owner)
        self.assertIsNone(self.camera.owner)
        self.assertEqual(self.cloud.calls, [])

    def test_brain_subscription_disconnect_does_not_stop_resident_capture(self):
        self.assertEqual(self.command('enable_microphone').status_code, 200)
        self.wait(lambda s: s['microphone']['active'])
        owner = self.mic.owner
        for _ in range(2):
            with self.client.websocket_connect('/api/brain/events', headers={'origin': 'http://testserver'}) as socket:
                self.assertEqual(socket.receive_json()['type'], 'brain_status')
            self.assertEqual(self.mic.owner, owner)
            self.assertEqual(self.client.get('/api/brain/status').json()['activity'], 'listening')
        self.assertEqual(self.command('disable_microphone').status_code, 200)
        self.wait(lambda s: not s['microphone']['active'])

    def test_events_are_read_only_and_reject_bad_origins(self):
        for headers in ({}, {'origin': 'http://foreign.invalid'}):
            with self.assertRaises(WebSocketDisconnect), self.client.websocket_connect('/api/brain/events', headers=headers):
                pass
        with self.client.websocket_connect('/api/brain/events', headers={'origin': 'http://testserver'}) as socket:
            before = socket.receive_json()
            socket.send_json({'action': 'pause'})
            with self.assertRaises(WebSocketDisconnect):
                socket.receive_json()
        self.assertEqual(self.client.get('/api/brain/status').json()['state'], before['state'])

    def test_write_origin_header_json_and_duplicate_checks_are_enforced(self):
        value = {'expected_revision': 0, 'profile': DEFAULT_PROFILE}
        for headers in ({}, {'Origin': 'http://evil.invalid', 'X-Radxa-Duck-Request': '1'}, {'Origin': 'http://testserver'}):
            self.assertEqual(self.client.put('/api/identity', json=value, headers=headers).status_code, 403)
            self.assertEqual(self.client.post('/api/brain/control', json={}, headers=headers).status_code, 403)
        for raw in ('{"expected_revision":0,"expected_revision":0,"profile":{}}', '{"expected_revision":NaN}',
                    '[]', '{"extra":true}', '{"profile":"' + 'x' * 33000 + '"}'):
            response = self.client.put('/api/identity', content=raw, headers={**WRITE, 'Content-Type': 'application/json'})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json(), {'error': {'code': 'invalid_request'}})

    def test_identity_save_conflict_and_next_turn_activation_are_visible(self):
        self.cloud.release.clear()
        first = self.client.put('/api/identity', json={'expected_revision': 0, 'profile': {**DEFAULT_PROFILE, 'name': '角色一'}}, headers=WRITE)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()['active_revision'], 0)
        self.assertEqual(self.command('enable_microphone').status_code, 200)
        self.wait(lambda s: s['activity'] == 'listening')
        self.mic.segments = [{'id': 1, 'text': '模拟输入'}]
        self.assertTrue(self.cloud.started.wait(2))
        second = self.client.put('/api/identity', json={'expected_revision': 1, 'profile': {**DEFAULT_PROFILE, 'name': '角色二'}}, headers=WRITE)
        self.assertEqual(second.json()['active_revision'], 1)
        status = self.client.get('/api/brain/status').json()
        self.assertEqual(status['identity'], {'name': '角色二', 'revision': 2, 'active_revision': 1})
        self.assertNotIn('角色二', self.cloud.contexts[0])
        conflict = self.client.put('/api/identity', json={'expected_revision': 1, 'profile': DEFAULT_PROFILE}, headers=WRITE)
        self.assertEqual((conflict.status_code, conflict.json()), (409, {'error': {'code': 'conflict'}}))

    def test_control_response_and_retry_survive_other_state_changes(self):
        state = self.client.get('/api/brain/status').json()
        value = {'request_id': 'same-operation', 'action': 'pause', 'expected_instance_id': state['instance_id'],
                 'expected_revision': state['revision']}
        first = self.client.post('/api/brain/control', json=value, headers=WRITE)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()['status']['state'], 'paused')
        self.assertEqual(self.command('resume').status_code, 200)
        repeated = self.client.post('/api/brain/control', json=value, headers=WRITE)
        self.assertEqual(repeated.json(), first.json())
        stale = self.client.post('/api/brain/control', json={**value, 'request_id': 'different-request'}, headers=WRITE)
        self.assertEqual((stale.status_code, stale.json()), (409, {'error': {'code': 'conflict'}}))


if __name__ == '__main__':
    unittest.main()
