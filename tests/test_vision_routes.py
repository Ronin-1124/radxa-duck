"""Vision transport tests with an in-memory camera service; no hardware access."""

from contextlib import ExitStack, contextmanager
import asyncio
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import anyio
from starlette.applications import Starlette
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from radxa_duck.vision_routes import create_vision_routes
from radxa_duck.vision_service import VisionServiceError


class FakeVision:
    def __init__(self):
        self.lock = threading.RLock()
        self.owner = None
        self.session_id = None
        self.session_count = 0
        self.revision = 0
        self.frame = None
        self.settings = {}
        self.start_calls = []
        self.stopped = threading.Event()

    def snapshot(self, owner=None):
        with self.lock:
            return {"type": "state", "schema_version": 1, "revision": self.revision,
                    "state": "streaming" if self.owner else "idle", "ready": True,
                    "session_id": self.session_id,
                    "owned": owner is not None and owner == self.owner,
                    "settings": dict(self.settings), "stats": {}, "error": None,
                    "capabilities": {"transport": "jpeg-websocket-v1"}}

    def start(self, owner, settings=None):
        with self.lock:
            if self.owner and self.owner != owner:
                raise VisionServiceError("busy", "Camera already owned")
            self.start_calls.append((owner, settings))
            if self.owner == owner:
                return
            self.owner = owner
            self.session_count += 1
            self.session_id = f"session-{self.session_count}"
            self.settings = dict(settings or {})
            self.frame = None
            self.stopped.clear()
            self.revision += 1

    def stop(self, owner):
        with self.lock:
            if self.owner is None:
                return
            if self.owner != owner:
                raise VisionServiceError("not_owner", "Not the camera owner")
            self.owner = self.session_id = self.frame = None
            self.revision += 1
            self.stopped.set()

    def publish(self, frame_id, result=None):
        with self.lock:
            jpeg = b"\xff\xd8" + f"{self.session_id}:{frame_id}".encode() + b"\xff\xd9"
            self.frame = SimpleNamespace(
                header={"type": "frame", "schema_version": 1,
                        "session_id": self.session_id, "frame_id": frame_id,
                        "width": 640, "height": 480, "mime_type": "image/jpeg",
                        "image_bytes": len(jpeg), "result": result},
                jpeg=jpeg,
            )
            self.revision += 1
            return self.frame

    def latest_frame(self, owner, session_id, after_frame_id):
        with self.lock:
            if (owner == self.owner and session_id == self.session_id
                    and self.frame is not None and self.frame.header["frame_id"] > after_frame_id):
                return self.frame
            return None


class VisionRouteTests(unittest.TestCase):
    def make_app(self, *, ack_timeout=3.0):
        self.service = FakeVision()
        app = Starlette(routes=create_vision_routes({"http://testserver"}, ack_timeout=ack_timeout))
        app.state.vision = self.service
        return app

    @staticmethod
    @contextmanager
    def connect(client):
        with client.websocket_connect("/api/vision/live", headers={"origin": "http://testserver"}) as socket:
            try:
                yield socket
            finally:
                # TestClient cancels the ASGI task when its context exits. Let
                # an ordinary disconnect finish before that framework cleanup.
                if not getattr(socket, "_vision_close_received", False):
                    socket.close()
                    deadline = time.monotonic() + 2
                    while not getattr(socket, "_vision_close_received", False):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise AssertionError("Vision endpoint did not finish disconnect cleanup")
                        try:
                            VisionRouteTests.receive_wire(socket, timeout=remaining)
                        except WebSocketDisconnect:
                            break

    @staticmethod
    def receive_wire(socket, timeout=2.0):
        # TestClient's public receive blocks indefinitely. Bound its receive
        # stream so a missing protocol message fails instead of hanging CI.
        async def receive():
            with anyio.fail_after(timeout):
                return await socket._send_rx.receive()

        message = socket.portal.call(receive)
        if message["type"] == "websocket.close":
            socket._vision_close_received = True
            raise WebSocketDisconnect(message.get("code", 1000), message.get("reason", ""))
        return message

    def receive_json(self, socket, predicate=lambda _message: True):
        for _ in range(24):
            message = self.receive_wire(socket)
            self.assertIn("text", message, "Unexpected unpaired binary image")
            value = json.loads(message["text"])
            if predicate(value):
                return value
            self.assertEqual(value["type"], "state", "Unexpected protocol message")
        self.fail("Expected protocol message not received")

    def start(self, socket, settings=None):
        command = {"type": "start"}
        if settings is not None:
            command["settings"] = settings
        socket.send_json(command)
        state = self.receive_json(socket, lambda value: value.get("state") == "streaming")
        self.assertTrue(state["owned"])
        return state["session_id"]

    def receive_frame(self, socket, expected):
        header = self.receive_json(socket, lambda value: value["type"] == "frame")
        self.assertEqual(header, expected.header)
        image = self.receive_wire(socket)
        self.assertEqual(image.get("bytes"), expected.jpeg, "Header must be immediately followed by JPEG")
        return header

    def assert_no_frame(self, socket, duration=0.12):
        deadline = time.monotonic() + duration
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                message = self.receive_wire(socket, timeout=remaining)
            except TimeoutError:
                return
            self.assertIn("text", message, "Unexpected binary image without credit")
            self.assertEqual(json.loads(message["text"])["type"], "state", "Frame sent without credit")

    @staticmethod
    def ack(socket, header):
        socket.send_json({"type": "ack", "session_id": header["session_id"], "frame_id": header["frame_id"]})

    def test_status_does_not_expose_images_or_claim_ownership(self):
        with TestClient(self.make_app()) as client:
            self.service.start("another-owner")
            self.service.publish(1, {"detections": [{"label": "person"}]})
            response = client.get("/api/vision/status")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.json(), self.service.snapshot())
            self.assertFalse(response.json()["owned"])
            for key in ("jpeg", "result", "frame"):
                self.assertNotIn(key, response.json())

    def test_header_and_binary_stay_paired_when_stop_arrives(self):
        with TestClient(self.make_app()) as client, self.connect(client) as socket:
            self.receive_json(socket)
            settings = {"detection_enabled": True, "max_fps": 10}
            self.start(socket, settings)
            self.assertEqual(self.service.start_calls[-1][1], settings)
            expected = self.service.publish(1, {"detections": [{"label": "person", "confidence": 0.9}]})
            header = self.receive_json(socket, lambda value: value["type"] == "frame")
            self.assertEqual(header, expected.header)
            socket.send_json({"type": "stop"})
            self.assertEqual(self.receive_wire(socket).get("bytes"), expected.jpeg)
            self.receive_json(socket, lambda value: value.get("state") == "idle")
            self.assertTrue(self.service.stopped.wait(1))

    def test_only_exact_ack_grants_one_frame_credit_and_latest_frame_wins(self):
        with TestClient(self.make_app()) as client, self.connect(client) as socket:
            self.receive_json(socket)
            self.start(socket)
            first = self.receive_frame(socket, self.service.publish(1))
            self.service.publish(2)
            newest = self.service.publish(3)
            self.assert_no_frame(socket)
            self.ack(socket, {"session_id": "old-session", "frame_id": 1})
            self.ack(socket, {"session_id": first["session_id"], "frame_id": 2})
            self.assert_no_frame(socket)
            self.ack(socket, first)
            third = self.receive_frame(socket, newest)
            fourth = self.service.publish(4)
            self.ack(socket, first)
            self.assert_no_frame(socket)
            self.ack(socket, third)
            self.receive_frame(socket, fourth)

    def test_old_session_ack_cannot_release_new_session_frame(self):
        with TestClient(self.make_app()) as client, self.connect(client) as socket:
            self.receive_json(socket)
            self.start(socket)
            old = self.receive_frame(socket, self.service.publish(1))
            socket.send_json({"type": "stop"})
            self.receive_json(socket, lambda value: value.get("state") == "idle")
            new_session = self.start(socket)
            self.assertNotEqual(old["session_id"], new_session)
            current = self.receive_frame(socket, self.service.publish(1))
            next_frame = self.service.publish(2)
            self.ack(socket, old)
            self.assert_no_frame(socket)
            self.ack(socket, current)
            self.receive_frame(socket, next_frame)

    def test_stop_discards_previous_ack_timeout_before_new_session(self):
        with TestClient(self.make_app(ack_timeout=0.1)) as client, self.connect(client) as socket:
            self.receive_json(socket)
            self.start(socket)
            self.receive_frame(socket, self.service.publish(1))
            socket.send_json({"type": "stop"})
            self.receive_json(socket, lambda value: value.get("state") == "idle")
            self.start(socket)
            # No frame in the new session: the old session's deadline must not
            # close the connection or release ownership while waiting for one.
            self.assert_no_frame(socket, duration=0.2)
            socket.send_json({"type": "unknown"})
            message = self.receive_json(socket, lambda value: value["type"] == "error")
            self.assertEqual(message["error"]["code"], "invalid_request")
            self.assertIsNotNone(self.service.owner)

    def test_missing_ack_closes_connection_and_stops_camera(self):
        with TestClient(self.make_app(ack_timeout=0.08)) as client, self.connect(client) as socket:
            self.receive_json(socket)
            self.start(socket)
            self.receive_frame(socket, self.service.publish(1))
            with self.assertRaises(WebSocketDisconnect) as closed:
                self.receive_json(socket, lambda _value: False)
            self.assertEqual(closed.exception.code, 1013)
            self.assertTrue(self.service.stopped.wait(1))
            self.assertIsNone(self.service.owner)

    def test_observer_cannot_receive_images_or_control_owner(self):
        with TestClient(self.make_app()) as client:
            with self.connect(client) as owner:
                self.receive_json(owner)
                self.start(owner)
                with self.connect(client) as observer:
                    self.assertFalse(self.receive_json(observer)["owned"])
                    self.receive_frame(owner, self.service.publish(1, {"detections": [{"label": "person"}]}))
                    for command, code in (("start", "busy"), ("stop", "not_owner")):
                        observer.send_json({"type": command})
                        error = self.receive_json(observer, lambda value: value["type"] == "error")
                        self.assertEqual(error["error"]["code"], code)
                    self.assert_no_frame(observer)
                self.assertIsNotNone(self.service.owner)
                self.assertFalse(self.service.stopped.is_set())
            self.assertTrue(self.service.stopped.wait(1))
            self.assertIsNone(self.service.owner)

    def test_invalid_commands_do_not_start_camera(self):
        with TestClient(self.make_app()) as client, self.connect(client) as socket:
            self.receive_json(socket)
            bad_commands = ["{", "[]", "null", '{"type":"unknown"}',
                            '{"type":"start","extra":1}', '{"type":"stop","settings":{}}']
            bad_commands += [json.dumps({"type": "start", "settings": value})
                             for value in (None, [], True, "settings")]
            bad_commands += [json.dumps({"type": "ack", "session_id": session, "frame_id": frame})
                             for session, frame in (("session", True), ("session", -1),
                                                    ("session", 1.5), ("session", 2 ** 53),
                                                    (None, 1), ("", 1), ("s" * 65, 1))]
            bad_commands += ['{"type":"ack","session_id":"session","frame_id":1,"extra":1}',
                             '{"type":"ack","frame_id":1}']
            for raw in bad_commands:
                with self.subTest(raw=raw):
                    socket.send_text(raw)
                    error = self.receive_json(socket, lambda value: value["type"] == "error")
                    self.assertEqual(error["error"]["code"], "invalid_request")
                    self.assertIsInstance(error["error"]["message"], str)
            self.assertEqual(self.service.start_calls, [])
            self.assertIsNone(self.service.owner)

    def test_binary_and_oversize_commands_close_without_starting_camera(self):
        with TestClient(self.make_app()) as client:
            for binary, code in ((True, 1003), (False, 1008)):
                with self.subTest(binary=binary), self.connect(client) as socket:
                    self.receive_json(socket)
                    if binary:
                        socket.send_bytes(b"not a control command")
                    else:
                        socket.send_text(' ' * 1025)
                    with self.assertRaises(WebSocketDisconnect) as closed:
                        self.receive_json(socket)
                    self.assertEqual(closed.exception.code, code)
            self.assertEqual(self.service.start_calls, [])

    def test_origin_required_and_connection_limit_released_after_disconnect(self):
        with TestClient(self.make_app()) as client:
            for headers in ({}, {"origin": "http://untrusted.example"}):
                with self.subTest(headers=headers):
                    with self.assertRaises(WebSocketDisconnect) as closed:
                        with client.websocket_connect("/api/vision/live", headers=headers):
                            self.fail("Untrusted connection accepted")
                    self.assertEqual(closed.exception.code, 1008)
            with ExitStack() as connections:
                for _ in range(4):
                    socket = connections.enter_context(self.connect(client))
                    self.receive_json(socket)
                with self.assertRaises(WebSocketDisconnect) as closed:
                    with self.connect(client):
                        self.fail("Fifth connection accepted")
                self.assertEqual(closed.exception.code, 1008)
            with self.connect(client) as socket:
                self.assertEqual(self.receive_json(socket)["state"], "idle")

    def test_command_rate_limit_resets_after_one_second(self):
        now = [100.0]
        route_clock = SimpleNamespace(monotonic=lambda: now[0])
        with patch("radxa_duck.vision_routes.time", route_clock):
            with TestClient(self.make_app()) as client, self.connect(client) as socket:
                self.receive_json(socket)
                for window in range(2):
                    now[0] = 100.0 + window
                    for _ in range(40):
                        socket.send_json({"type": "unknown"})
                        error = self.receive_json(socket, lambda value: value["type"] == "error")
                        self.assertEqual(error["error"]["code"], "invalid_request")
                socket.send_json({"type": "start"})
                with self.assertRaises(WebSocketDisconnect) as closed:
                    self.receive_json(socket)
                self.assertEqual(closed.exception.code, 1008)
                self.assertEqual(self.service.start_calls, [])

    def test_waiting_for_send_lock_discards_expired_frame_and_uses_latest_replacement(self):
        async def exercise(replace_frame):
            response_entered, release_response = asyncio.Event(), asyncio.Event()
            checked_or_sent, delivered = asyncio.Event(), asyncio.Event()
            now = [0.0]

            class ExpiringVision(FakeVision):
                def start(self, owner, settings=None):
                    super().start(owner, settings)
                    self.publish(1)

                def publish(self, frame_id, result=None):
                    self.captured_at = now[0]
                    return super().publish(frame_id, result)

                def latest_frame(self, owner, session_id, after_frame_id):
                    frame = super().latest_frame(owner, session_id, after_frame_id)
                    if now[0] >= 2:
                        checked_or_sent.set()
                    if frame is None or now[0] - self.captured_at > 1:
                        return None
                    return SimpleNamespace(header={**frame.header, "frame_age_ms":
                                                   (now[0] - self.captured_at) * 1000}, jpeg=frame.jpeg)

            service = ExpiringVision()

            class GatedSocket:
                headers = {"origin": "http://testserver"}
                app = SimpleNamespace(state=SimpleNamespace(vision=service))

                def __init__(self):
                    self.incoming = asyncio.Queue()
                    self.messages = []
                    self.images = []
                    self.streaming_states = 0

                async def accept(self):
                    self.incoming.put_nowait({"type": "websocket.receive", "text": '{"type":"start"}'})

                async def receive(self):
                    return await self.incoming.get()

                async def send_json(self, message):
                    self.messages.append(message)
                    if message.get("state") == "streaming":
                        self.streaming_states += 1
                        if self.streaming_states == 2:
                            # Queue a command while publish still holds the send
                            # lock. Its response acquires that lock before the
                            # publisher can send its first image.
                            self.incoming.put_nowait({"type": "websocket.receive",
                                                      "text": '{"type":"unknown"}'})
                            await asyncio.sleep(0)
                    if message["type"] == "error":
                        response_entered.set()
                        await release_response.wait()

                async def send_bytes(self, image):
                    self.images.append(image)
                    checked_or_sent.set()
                    delivered.set()

                async def close(self, **_kwargs):
                    pass

            socket = GatedSocket()
            endpoint = create_vision_routes({"http://testserver"})[1].endpoint
            connection = asyncio.create_task(endpoint(socket))
            try:
                await asyncio.wait_for(response_entered.wait(), 1)
                # Advance capture age while the command response occupies the
                # lock, without slowing this test down by two real seconds.
                now[0] = 2.0
                expected = service.publish(2) if replace_frame else None
                release_response.set()
                await asyncio.wait_for((delivered if replace_frame else checked_or_sent).wait(), 1)
                headers = [message for message in socket.messages if message["type"] == "frame"]
                if replace_frame:
                    self.assertEqual([header["frame_id"] for header in headers], [2])
                    self.assertEqual(headers[0]["frame_age_ms"], 0)
                    self.assertEqual(socket.images, [expected.jpeg])
                else:
                    self.assertEqual(headers, [], "Expired frame was sent after waiting for the lock")
                    self.assertEqual(socket.images, [])
            finally:
                release_response.set()
                socket.incoming.put_nowait({"type": "websocket.disconnect"})
                await asyncio.wait_for(connection, 1)
            self.assertTrue(service.stopped.is_set())

        for replace_frame in (False, True):
            with self.subTest(replace_frame=replace_frame):
                asyncio.run(exercise(replace_frame))

    def test_cancellation_during_disconnect_stops_capture_and_returns_connection_slot(self):
        async def exercise():
            class CancelDuringCleanup(FakeVision):
                def stop(self, owner):
                    super().stop(owner)
                    # Deliver cancellation at cleanup's first suspension, as a
                    # server deadline can do after a normal disconnect arrives.
                    asyncio.get_running_loop().call_soon(asyncio.current_task().cancel)

            service = CancelDuringCleanup()

            class Socket:
                headers = {"origin": "http://testserver"}
                app = SimpleNamespace(state=SimpleNamespace(vision=service))

                def __init__(self):
                    self.incoming, self.outgoing = asyncio.Queue(), asyncio.Queue()

                async def accept(self):
                    self.incoming.put_nowait({"type": "websocket.receive", "text": '{"type":"start"}'})

                async def receive(self):
                    return await self.incoming.get()

                async def send_json(self, message):
                    self.outgoing.put_nowait(message)

                async def close(self, **kwargs):
                    self.outgoing.put_nowait({"type": "close", **kwargs})

            endpoint = create_vision_routes({"http://testserver"})[1].endpoint
            # Four leaked slots would reject the fifth connection. Each pass
            # acquires the camera and interrupts the endpoint during cleanup.
            for _ in range(5):
                socket = Socket()
                connection = asyncio.create_task(endpoint(socket))
                try:
                    while True:
                        message = await asyncio.wait_for(socket.outgoing.get(), 1)
                        self.assertEqual(message["type"], "state", "Cancelled connection leaked its slot")
                        if message["state"] == "streaming":
                            break
                    self.assertIsNotNone(service.owner)
                    socket.incoming.put_nowait({"type": "websocket.disconnect"})
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(connection, 1)
                    self.assertTrue(service.stopped.is_set())
                    self.assertIsNone(service.owner)
                finally:
                    if not connection.done():
                        connection.cancel()
                        await asyncio.wait_for(asyncio.gather(connection, return_exceptions=True), 1)

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
