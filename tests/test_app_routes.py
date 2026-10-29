"""Application HTTP/socket contracts with an in-memory service, no hardware."""

from contextlib import ExitStack, contextmanager
from copy import deepcopy
import asyncio
import json
import threading
import time
import unittest
from unittest.mock import Mock, patch

import anyio
from starlette.applications import Starlette
from starlette.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

from radxa_duck.app_routes import MAX_COMMAND_BYTES, create_app_routes
from radxa_duck.app_service import AppService, AppServiceError


class FakeApps:
    def __init__(self):
        self.lock = threading.RLock()
        self.owner = None
        self.session_id = None
        self.scene = None
        self.listen = False
        self.speak = False
        self.turn_id = None
        self.reply = ""
        self.revision = self.sequence = 0
        self.buffer = []
        self.starts = []
        self.submissions = []
        self.listening = []
        self.cancelled = []
        self.stops = []
        self.stopped = threading.Event()
        self.stop_calls = 0

    def capabilities(self):
        return {"schema_version": 1, "scenes": ["voice-chat", "image-qa", "motion", "companion"],
                "capabilities": {"sessions": True, "motion": False}}

    def actions_snapshot(self):
        return {"schema_version": 1, "available": False, "reason": "controller_unavailable", "actions": []}

    def snapshot(self, owner=None):
        with self.lock:
            owned = self.owner is not None and owner == self.owner
            return {"schema_version": 1, "type": "app_state", "revision": self.revision,
                    "event_sequence": self.sequence, "state": "idle" if self.owner is None else "ready",
                    "session_id": self.session_id, "owned": owned, "scene": self.scene,
                    "turn_id": self.turn_id if owned else None, "reply": self.reply if owned else "",
                    "listen": self.listen if owned else False, "speak": self.speak if owned else False}

    def require_owner(self, owner):
        if self.owner != owner:
            raise AppServiceError("not_owner", "Another connection controls the application session")

    def start(self, owner, *, scene, listen=False, speak=False, cancelled=None):
        with self.lock:
            if cancelled is not None and cancelled.is_set():
                raise AppServiceError("cancelled", "Application session start was cancelled")
            if self.owner is not None and self.owner != owner:
                raise AppServiceError("busy", "Another application session is already active")
            self.owner = owner
            self.session_id = "session-" + str(len(self.starts) + 1)
            self.scene, self.listen, self.speak = scene, listen, speak
            self.starts.append((owner, scene, listen, speak))
            self.reply, self.buffer = "", []
            self.sequence = 0
            self.revision += 1
            self.stopped.clear()

    def submit(self, owner, **request):
        with self.lock:
            self.require_owner(owner)
            self.submissions.append((owner, request))
            self.turn_id = request["request_id"]
            self.revision += 1

    def set_listening(self, owner, *, enabled):
        with self.lock:
            self.require_owner(owner)
            self.listen = enabled
            self.listening.append(enabled)
            self.revision += 1

    def cancel(self, owner):
        with self.lock:
            self.require_owner(owner)
            self.cancelled.append(owner)
            self.listen = False
            self.revision += 1

    def stop(self, owner):
        with self.lock:
            self.stop_calls += 1
            if self.owner is None:
                return
            self.require_owner(owner)
            self.stops.append(owner)
            self.owner = self.session_id = self.scene = self.turn_id = None
            self.listen, self.speak, self.reply, self.buffer = False, False, "", []
            self.revision += 1
            self.stopped.set()

    def emit(self, kind, **fields):
        with self.lock:
            self.sequence += 1
            event = {"type": kind, "schema_version": 1, "seq": self.sequence,
                     "session_id": self.session_id, "turn_id": self.turn_id, **fields}
            self.buffer.append(event)
            if kind == "answer_delta":
                self.reply += fields["text"]
            self.revision += 1
            return event

    def events(self, owner, after):
        with self.lock:
            if self.owner != owner:
                return []
            return deepcopy([event for event in self.buffer if event["seq"] > after])


class ControlledMicrophone:
    """Keep simulated capture owned until the test allows asynchronous release."""

    def __init__(self):
        self.lock = threading.Lock()
        self.owner = None
        self.started = threading.Event()
        self.stop_requested = threading.Event()
        self.release = threading.Event()
        self.starts = 0
        self.clear_transcript = Mock()

    def snapshot(self, owner=None):
        with self.lock:
            if self.stop_requested.is_set() and self.release.is_set():
                self.owner = None
            return {"ready": True, "owned": owner is not None and owner == self.owner,
                    "state": "listening" if self.owner is not None else "idle",
                    "session_id": "simulated-microphone", "partial_text": "", "segments": []}

    def start(self, owner, *, private=False):
        with self.lock:
            if self.owner is not None or not private:
                raise AssertionError("Expected exclusive private application capture")
            self.owner = owner
            self.starts += 1
            self.started.set()

    def stop(self, owner):
        with self.lock:
            if self.owner != owner:
                raise AssertionError("Capture stop changed another owner's device")
            self.stop_requested.set()


class AppRouteTests(unittest.TestCase):
    def make_app(self, service=True):
        self.service = FakeApps() if service is True else service
        app = Starlette(routes=create_app_routes({"http://testserver"}))
        app.state.apps = self.service
        return app

    @staticmethod
    def receive_wire(socket, timeout=2):
        async def receive():
            with anyio.fail_after(timeout):
                return await socket._send_rx.receive()
        message = socket.portal.call(receive)
        if message["type"] == "websocket.close":
            socket._apps_close_received = True
            raise WebSocketDisconnect(message.get("code", 1000), message.get("reason", ""))
        return message

    def receive(self, socket, predicate=lambda _message: True):
        for _ in range(24):
            message = self.receive_wire(socket)
            self.assertIn("text", message)
            value = json.loads(message["text"])
            if predicate(value):
                return value
        self.fail("Expected application protocol message was not received")

    @contextmanager
    def connect(self, client, origin="http://testserver"):
        with client.websocket_connect("/api/apps/live", headers={"Origin": origin}) as socket:
            try:
                yield socket
            finally:
                # Finish an ordinary disconnect before TestClient cancels its
                # ASGI task; resource cleanup must not rely on framework exit.
                if not getattr(socket, "_apps_close_received", False):
                    socket.close()
                    deadline = time.monotonic() + 2
                    while not getattr(socket, "_apps_close_received", False):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise AssertionError("Application socket did not finish disconnect cleanup")
                        try:
                            self.receive_wire(socket, remaining)
                        except WebSocketDisconnect:
                            break

    def start(self, socket, scene="voice-chat", **fields):
        socket.send_json({"type": "start", "scene": scene, **fields})
        value = self.receive(socket, lambda state: state.get("type") == "app_state" and state.get("owned"))
        return value["session_id"]

    def submit(self, socket, request_id="request-0001", **fields):
        socket.send_json({"type": "submit", "request_id": request_id, **fields})
        return self.receive(socket, lambda state: state.get("type") == "app_state" and state.get("turn_id") == request_id)

    def test_http_status_and_actions_are_read_only_and_uncached(self):
        with TestClient(self.make_app()) as client:
            for path in ("/api/apps/status", "/api/apps/actions"):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["schema_version"], 1)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            self.assertEqual(self.service.starts, [])
            self.assertEqual(self.service.submissions, [])
            self.assertEqual(self.service.stop_calls, 0)

    def test_unconfigured_service_returns_unavailable_without_start(self):
        with TestClient(self.make_app(service=None)) as client:
            self.assertEqual(client.get("/api/apps/status").status_code, 503)
            self.assertEqual(client.get("/api/apps/actions").json()["error"]["code"], "apps_unavailable")
            with self.assertRaises(WebSocketDisconnect) as raised:
                with self.connect(client):
                    pass
            self.assertEqual(raised.exception.code, 1013)

    def test_status_failure_is_a_fixed_json_error_without_private_details(self):
        service = FakeApps()
        def fail():
            raise RuntimeError("private cloud or controller transport detail")
        service.capabilities = fail
        with TestClient(self.make_app(service=service)) as client:
            response = client.get("/api/apps/status")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "apps_unavailable")
            self.assertNotIn("private", response.text)
            self.assertEqual(service.stop_calls, 0)

    def test_socket_connection_and_scene_read_do_not_start_capture(self):
        with TestClient(self.make_app()) as client, self.connect(client) as socket:
            initial = self.receive(socket)
            self.assertFalse(initial["owned"])
            self.assertEqual(self.service.starts, [])
            self.start(socket, "companion")
            self.assertEqual(self.service.starts[0][1:], ("companion", False, False))
            self.assertFalse(self.service.listen)
            self.assertFalse(self.service.speak)
        self.assertTrue(self.service.stopped.is_set())
        self.assertEqual(len(self.service.stops), 1)

    def test_start_validation_does_not_block_status_or_socket_disconnect(self):
        service = FakeApps()
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        original = service.start
        closing = []

        def checking_start(owner, **settings):
            closing.append(settings["cancelled"])
            entered.set()
            try:
                if not release.wait(3):
                    raise RuntimeError("Simulated disk validation did not finish")
                original(owner, **settings)
            finally:
                finished.set()

        service.start = checking_start
        with TestClient(self.make_app(service=service)) as client, self.connect(client) as socket:
            self.receive(socket)
            socket.send_json({"type": "start", "scene": "voice-chat", "listen": True})
            self.assertTrue(entered.wait(1))
            try:
                self.assertEqual(client.get("/api/apps/status").status_code, 200)
                self.assertFalse(finished.is_set(), "Disk validation blocked the HTTP event loop")
                # A command queued after start must never execute after its
                # controlling connection disconnects during validation.
                socket.send_json({"type": "submit", "request_id": "request-0001", "text": "你好"})
                socket.close()
                self.assertTrue(closing[0].wait(1), "Disconnect did not cancel pending initialization")
                self.assertIsNone(service.owner)
                self.assertFalse(service.listen)
            finally:
                release.set()
            with self.assertRaises(WebSocketDisconnect):
                while True:
                    self.receive_wire(socket)
            self.assertTrue(finished.wait(1))
            self.assertEqual(service.starts, [])
            self.assertEqual(service.submissions, [])
            self.assertIsNone(service.owner)
            self.assertFalse(service.listen)

    def test_commands_remain_ordered_while_start_checks_run_in_a_thread(self):
        service = FakeApps()
        entered, release = threading.Event(), threading.Event()
        original = service.start

        def checking_start(owner, **settings):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("Simulated disk validation did not finish")
            original(owner, **settings)

        service.start = checking_start
        with TestClient(self.make_app(service=service)) as client, self.connect(client) as socket:
            self.receive(socket)
            socket.send_json({"type": "start", "scene": "voice-chat"})
            self.assertTrue(entered.wait(1))
            try:
                socket.send_json({"type": "submit", "request_id": "request-0001", "text": "你好"})
                self.assertEqual(client.get("/api/apps/status").status_code, 200)
                self.assertEqual(service.submissions, [])
            finally:
                release.set()
            state = self.receive(socket, lambda value: value.get("turn_id") == "request-0001")
            self.assertTrue(state["owned"])
            self.assertEqual(len(service.starts), 1)
            self.assertEqual(len(service.submissions), 1)

    def test_explicit_session_controls_submit_and_cancel_are_forwarded(self):
        with TestClient(self.make_app()) as client, self.connect(client) as socket:
            self.receive(socket)
            self.start(socket, listen=True, speak=True)
            self.submit(socket, text="你好")
            self.assertEqual(self.service.submissions[0][1], {
                "request_id": "request-0001", "text": "你好", "capture_image": False, "action_id": None})
            socket.send_json({"type": "listen_stop"})
            self.receive(socket, lambda state: state.get("type") == "app_state" and not state["listen"])
            socket.send_json({"type": "listen_start"})
            self.receive(socket, lambda state: state.get("type") == "app_state" and state["listen"])
            socket.send_json({"type": "cancel"})
            self.receive(socket, lambda state: state.get("type") == "app_state" and not state["listen"])
            self.assertEqual(self.service.listening, [False, True])
            self.assertEqual(len(self.service.cancelled), 1)
            socket.send_json({"type": "stop"})
            self.receive(socket, lambda state: state.get("type") == "app_state" and not state["owned"])

    def test_real_service_listen_stop_publishes_stopping_then_waiting_after_microphone_release(self):
        microphone, vision, cloud, config = ControlledMicrophone(), Mock(), Mock(), Mock()
        vision.snapshot.return_value = {"ready": False, "owned": False}
        config.snapshot.return_value = {"configured": True}
        service = AppService(microphone, vision, cloud=cloud, cloud_config=config)
        self.addCleanup(service.close, timeout=3)
        self.addCleanup(microphone.release.set)

        with TestClient(self.make_app(service=service)) as client, self.connect(client) as socket:
            self.receive(socket)
            session_id = self.start(socket, listen=True)
            self.assertTrue(microphone.started.wait(1))
            socket.send_json({"type": "listen_stop"})
            stopping = self.receive(socket, lambda state: state.get("type") == "app_state"
                                    and state.get("session_id") == session_id
                                    and state.get("listening") is False)
            self.assertTrue(stopping["owned"])
            self.assertEqual(stopping["state"], "stopping")
            self.assertTrue(microphone.stop_requested.wait(1))
            self.assertIsNotNone(microphone.owner)

            microphone.release.set()
            waiting = self.receive(socket, lambda state: state.get("type") == "app_state"
                                   and state.get("state") == "waiting")
            self.assertEqual(waiting["session_id"], session_id)
            self.assertTrue(waiting["owned"])
            self.assertFalse(waiting["listening"])
            self.assertGreater(waiting["revision"], stopping["revision"])
            self.assertIsNone(microphone.owner)
            self.assertEqual(microphone.starts, 1)
            cloud.stream.assert_not_called()
            vision.start.assert_not_called()

    def test_image_and_motion_commands_retain_explicit_user_parameters(self):
        for scene, fields in (("image-qa", {"text": "看到了什么？", "capture_image": True}),
                              ("motion", {"action_id": "head.nod"})):
            with self.subTest(scene=scene), TestClient(self.make_app()) as client, self.connect(client) as socket:
                self.receive(socket)
                self.start(socket, scene)
                self.submit(socket, **fields)
                self.assertEqual(self.service.submissions[0][1]["capture_image"], fields.get("capture_image", False))
                self.assertEqual(self.service.submissions[0][1]["action_id"], fields.get("action_id"))

    def test_streamed_events_keep_sequence_turn_and_session_identity(self):
        with TestClient(self.make_app()) as client, self.connect(client) as socket:
            self.receive(socket)
            session_id = self.start(socket)
            self.submit(socket)
            first = self.service.emit("answer_delta", text="你好")
            second = self.service.emit("answer_delta", text="，小鸭")
            ended = self.service.emit("turn_end", state="succeeded")
            received = [self.receive(socket, lambda message: message["type"] != "app_state") for _ in range(3)]
            self.assertEqual(received, [first, second, ended])
            self.assertEqual([event["seq"] for event in received], [1, 2, 3])
            self.assertTrue(all(event["session_id"] == session_id and event["turn_id"] == "request-0001" for event in received))
            self.assertEqual(self.service.snapshot(self.service.owner)["reply"], "你好，小鸭")

    def test_observer_is_rejected_without_exposing_reply_or_stopping_owner(self):
        with TestClient(self.make_app()) as client, self.connect(client) as first:
            self.receive(first)
            self.start(first)
            self.submit(first)
            self.service.emit("answer_delta", text="private session reply")
            self.receive(first, lambda message: message["type"] == "answer_delta")
            owner = self.service.owner
            with self.connect(client) as second:
                state = self.receive(second)
                self.assertFalse(state["owned"])
                self.assertEqual(state["reply"], "")
                second.send_json({"type": "start", "scene": "companion"})
                self.assertEqual(self.receive(second, lambda message: message["type"] == "error")["error"]["code"], "busy")
                second.send_json({"type": "cancel"})
                self.assertEqual(self.receive(second, lambda message: message["type"] == "error")["error"]["code"], "not_owner")
            self.assertEqual(self.service.owner, owner)
            self.assertEqual(self.service.stops, [])

    def test_invalid_json_fields_and_types_are_rejected_without_session(self):
        messages = [
            '[]', '{"type":"start","scene":"companion","scene":"voice-chat"}',
            '{"type":"start","scene":"companion","listen":NaN}', '[' * 1100 + '0' + ']' * 1100,
            {"type": "start", "scene": "unknown"}, {"type": "start", "scene": []},
            {"type": "start", "scene": "voice-chat", "listen": 1},
            {"type": "start", "scene": "voice-chat", "speak": "true"},
            {"type": "start", "scene": "voice-chat", "secret": "not accepted"},
            {"type": "submit", "request_id": "short"},
            {"type": "submit", "request_id": "request-0001", "text": "x" * 2001},
            {"type": "submit", "request_id": "request-0001", "capture_image": 1},
            {"type": "submit", "request_id": "request-0001", "action_id": "../../bin/sh"},
            {"type": "cancel", "listen": True}, {"type": ["start"]},
        ]
        with TestClient(self.make_app()) as client:
            for message in messages:
                with self.subTest(message=str(message)[:80]), self.connect(client) as socket:
                    self.receive(socket)
                    socket.send_text(message if isinstance(message, str) else json.dumps(message))
                    error = self.receive(socket, lambda value: value["type"] == "error")
                    self.assertEqual(error["error"]["code"], "invalid_request")
            self.assertEqual(self.service.starts, [])
            self.assertEqual(self.service.submissions, [])

    def test_rejected_origin_and_connection_limit_never_start_or_stop_sessions(self):
        with TestClient(self.make_app()) as client:
            for origin in ("http://evil.invalid", "null", "http://testserver.evil.invalid"):
                with self.subTest(origin=origin), self.assertRaises(WebSocketDisconnect) as raised:
                    with self.connect(client, origin):
                        pass
                self.assertEqual(raised.exception.code, 1008)
            with ExitStack() as stack:
                sockets = [stack.enter_context(self.connect(client)) for _ in range(4)]
                for socket in sockets:
                    self.receive(socket)
                with self.assertRaises(WebSocketDisconnect) as raised:
                    with self.connect(client):
                        pass
                self.assertEqual(raised.exception.code, 1008)
            with self.connect(client) as socket:
                self.assertEqual(self.receive(socket)["state"], "idle")
            self.assertEqual(self.service.starts, [])

    def test_binary_oversized_and_rate_limited_commands_close_and_stop_owned_session(self):
        for kind in ("binary", "oversized", "rate"):
            with self.subTest(kind=kind), TestClient(self.make_app()) as client, self.connect(client) as socket:
                self.receive(socket)
                self.start(socket)
                if kind == "binary":
                    socket.send_bytes(b"unexpected audio upload")
                elif kind == "oversized":
                    socket.send_text("中" * (MAX_COMMAND_BYTES // 3 + 1))
                else:
                    # The initial start also consumes one command in this window.
                    for _ in range(10):
                        socket.send_json({"type": "cancel"})
                with self.assertRaises(WebSocketDisconnect) as raised:
                    while True:
                        self.receive_wire(socket)
                self.assertEqual(raised.exception.code, 1003 if kind == "binary" else 1008)
                self.assertTrue(self.service.stopped.wait(1))
                self.assertIsNone(self.service.owner)

    def test_send_timeout_signals_stop_before_transport_cleanup(self):
        original = WebSocket.send_json
        async def blocked(socket, value, *args, **kwargs):
            if value.get("type") == "answer_delta":
                raise asyncio.TimeoutError()
            return await original(socket, value, *args, **kwargs)
        with patch.object(WebSocket, "send_json", blocked), TestClient(self.make_app()) as client, self.connect(client) as socket:
            self.receive(socket)
            self.start(socket)
            self.submit(socket)
            self.service.emit("answer_delta", text="private partial answer")
            with self.assertRaises(WebSocketDisconnect):
                while True:
                    self.receive_wire(socket)
            self.assertTrue(self.service.stopped.wait(1))
            self.assertIsNone(self.service.owner)

    def test_late_session_events_do_not_transfer_to_a_new_connection(self):
        with TestClient(self.make_app()) as client:
            with self.connect(client) as socket:
                self.receive(socket)
                old_session = self.start(socket)
                self.submit(socket)
                self.service.emit("answer_delta", text="old private answer")
                self.receive(socket, lambda message: message["type"] == "answer_delta")
            with self.connect(client) as socket:
                state = self.receive(socket)
                self.assertFalse(state["owned"])
                self.assertEqual(state["reply"], "")
                new_session = self.start(socket)
                self.assertNotEqual(old_session, new_session)
                self.submit(socket, request_id="request-0002")
                event = self.service.emit("answer_delta", text="new answer")
                self.assertEqual(self.receive(socket, lambda message: message["type"] == "answer_delta"), event)

    def test_a_new_turn_during_send_discards_the_remaining_old_event_batch(self):
        app = self.make_app()
        original = WebSocket.send_json
        advanced = threading.Event()
        async def advance(socket, value, *args, **kwargs):
            if value.get("type") == "answer_delta" and value.get("text") == "old first":
                self.service.submit(self.service.owner, request_id="request-0002", text="new question")
                self.service.emit("answer_delta", text="new answer")
                advanced.set()
            return await original(socket, value, *args, **kwargs)
        with patch.object(WebSocket, "send_json", advance), TestClient(app) as client, self.connect(client) as socket:
            self.receive(socket)
            self.start(socket)
            self.submit(socket)
            self.service.emit("answer_delta", text="old first")
            self.service.emit("answer_delta", text="old late")
            self.service.emit("turn_end", state="succeeded")
            first = self.receive(socket, lambda message: message["type"] == "answer_delta")
            self.assertEqual(first["text"], "old first")
            self.assertTrue(advanced.wait(1))
            next_event = self.receive(socket, lambda message: message["type"] != "app_state")
            self.assertEqual(next_event["type"], "answer_delta")
            self.assertEqual(next_event["text"], "new answer")
            self.assertEqual(next_event["turn_id"], "request-0002")

    def test_a_stop_exception_does_not_leak_connection_admission(self):
        app = self.make_app()
        original = self.service.stop
        def broken_stop(owner):
            original(owner)
            raise RuntimeError("private cleanup failure")
        self.service.stop = broken_stop
        with TestClient(app) as client:
            with self.connect(client) as socket:
                self.receive(socket)
                self.start(socket)
            self.assertTrue(self.service.stopped.wait(1))
            with ExitStack() as stack:
                sockets = [stack.enter_context(self.connect(client)) for _ in range(4)]
                for socket in sockets:
                    self.assertFalse(self.receive(socket)["owned"])


if __name__ == "__main__":
    unittest.main()
