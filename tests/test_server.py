"""Server integration tests with fake device services; no hardware access."""

import asyncio
from contextlib import ExitStack
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from radxa_duck.server import create_app
from radxa_duck.speech import SpeechError
from radxa_duck.vision_service import VisionServiceError


class FakeSpeech:
    def __init__(self, *_args):
        self.owner = None
        self.revision = 0
        self.stopped = threading.Event()
        self.closed = False

    def snapshot(self, owner=None):
        return {"type": "state", "schema_version": 1, "revision": self.revision,
                "state": "listening" if self.owner else "idle", "ready": True,
                "session_id": "session" if self.owner else None,
                "owned": owner is not None and owner == self.owner,
                "partial_text": "测试" if self.owner else "", "segments": [],
                "audio_seconds": 0, "error": None, "provider": "cpu"}

    def start(self, owner):
        if self.owner and self.owner != owner:
            raise SpeechError("busy", "Already listening")
        self.owner = owner
        self.revision += 1

    def stop(self, owner):
        if self.owner is None:
            return
        if self.owner != owner:
            raise SpeechError("not_owner", "Not the microphone owner")
        self.owner = None
        self.revision += 1
        self.stopped.set()

    def close(self):
        self.closed = True


class FakeVision:
    def __init__(self):
        self.owner = None
        self.revision = 0
        self.settings = {}
        self.stopped = threading.Event()
        self.closed = False

    def snapshot(self, owner=None):
        return {"type": "state", "schema_version": 1, "revision": self.revision,
                "state": "streaming" if self.owner else "idle", "ready": True,
                "session_id": "vision-session" if self.owner else None,
                "owned": owner is not None and owner == self.owner,
                "settings": dict(self.settings), "stats": {}, "error": None,
                "capabilities": {"transport": "jpeg-websocket-v1"}}

    def start(self, owner, settings=None):
        if self.owner and self.owner != owner:
            raise VisionServiceError("busy", "Camera already owned")
        self.owner = owner
        self.settings = dict(settings or {})
        self.revision += 1

    def stop(self, owner):
        if self.owner is None:
            return
        if self.owner != owner:
            raise VisionServiceError("not_owner", "Not the camera owner")
        self.owner = None
        self.revision += 1
        self.stopped.set()

    def latest_frame(self, owner, session_id, after_frame_id):
        return None

    def close(self):
        self.closed = True


class ServerTests(unittest.TestCase):
    def test_brain_context_is_read_only_and_contains_no_private_session_buffers(self):
        with TestClient(self.make_app()) as client:
            response = client.get('/api/brain/context')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['type'], 'brain_context')
            self.assertEqual(response.headers['cache-control'], 'no-store')
            self.assertEqual(client.post('/api/brain/context', json={}).status_code, 405)
            self.assertNotIn('api_key', response.text)
            self.assertNotIn('history', response.text)

    def make_app(self, **kwargs):
        self.service = FakeSpeech()
        self.vision = FakeVision()
        kwargs.setdefault("vision_service_factory", lambda *_args, **_kwargs: self.vision)
        return create_app(Path("model"), Path("vad"), origins=("http://testserver",),
                          service_factory=lambda *_: self.service, **kwargs)

    @staticmethod
    def receive(socket, predicate):
        for _ in range(12):
            message = socket.receive_json()
            if predicate(message):
                return message
        raise AssertionError("Expected protocol message not received")

    def connect(self, client):
        return client.websocket_connect("/api/speech/live", headers={"origin": "http://testserver"})

    def test_start_stop_disconnect_and_lifespan(self):
        with TestClient(self.make_app()) as client:
            response = client.get("/api/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            with self.connect(client) as socket:
                self.assertEqual(socket.receive_json()["state"], "idle")
                socket.send_json({"type": "start"})
                message = self.receive(socket, lambda m: m.get("state") == "listening")
                self.assertTrue(message["owned"])
                socket.send_json({"type": "stop"})
                self.receive(socket, lambda m: m.get("state") == "idle")
                socket.send_json({"type": "start"})
                self.receive(socket, lambda m: m.get("state") == "listening")
                self.service.stopped.clear()
            self.assertTrue(self.service.stopped.wait(1))
            self.assertIsNone(self.service.owner)
        self.assertTrue(self.service.closed)
        self.assertTrue(self.vision.closed)

    def test_application_socket_streams_through_real_session_worker_and_cleans_up(self):
        from types import SimpleNamespace
        from radxa_duck.app_service import AppService
        from test_app_service import Cloud, Output

        cloud = Cloud()
        def applications(speech, vision, **_kwargs):
            return AppService(speech, vision, cloud=cloud,
                              cloud_config=SimpleNamespace(snapshot=lambda: {"configured": True}),
                              output=Output(speech))

        app = self.make_app(apps_factory=applications)
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/apps/status").status_code, 200)
            self.assertFalse(client.get("/api/apps/actions").json()["available"])
            with client.websocket_connect("/api/apps/live", headers={"origin": "http://testserver"}) as socket:
                self.assertFalse(socket.receive_json()["owned"])
                self.assertIsNone(self.service.owner)
                socket.send_json({"type": "start", "scene": "companion"})
                self.receive(socket, lambda value: value.get("owned") is True)
                socket.send_json({"type": "submit", "request_id": "integration-turn", "text": "hello"})
                end = self.receive(socket, lambda value: value.get("type") == "turn_end")
                self.assertEqual(end["state"], "succeeded")
                self.assertEqual(cloud.calls[0][0], "hello")
                self.assertIsNone(self.service.owner)
                self.assertIsNone(self.vision.owner)
        self.assertTrue(cloud.closed)
        self.assertEqual(app.state.apps.snapshot()["reply"], "")

    def test_rapid_disconnects_release_microphone_and_connection_admission(self):
        with TestClient(self.make_app()) as client:
            for _ in range(20):
                with self.connect(client) as socket:
                    socket.receive_json()
                    socket.send_json({"type": "start"})
                    self.receive(socket, lambda value: value.get("owned") is True)
                self.assertIsNone(self.service.owner)

    def test_vision_and_speech_routes_can_run_and_stop_independently(self):
        with TestClient(self.make_app()) as client, self.connect(client) as speech:
            speech.receive_json()
            speech.send_json({"type": "start"})
            self.receive(speech, lambda message: message.get("state") == "listening")
            speech_owner = self.service.owner
            with client.websocket_connect("/api/vision/live", headers={"origin": "http://testserver"}) as vision:
                self.assertEqual(vision.receive_json()["state"], "idle")
                vision.send_json({"type": "start", "settings": {"detection_enabled": False}})
                state = self.receive(vision, lambda message: message.get("state") == "streaming")
                self.assertTrue(state["owned"])
                self.assertNotEqual(self.vision.owner, speech_owner)
                self.assertEqual(self.vision.settings, {"detection_enabled": False})
                status = client.get("/api/vision/status")
                self.assertEqual(status.status_code, 200)
                self.assertEqual(status.headers["cache-control"], "no-store")
                self.assertEqual(status.json()["state"], "streaming")
                self.assertFalse(status.json()["owned"])
                self.assertEqual(client.get("/api/health").json()["state"], "listening")
                speech.send_json({"type": "stop"})
                self.receive(speech, lambda message: message.get("state") == "idle")
                self.assertIsNotNone(self.vision.owner)
            self.assertTrue(self.vision.stopped.wait(1))
            self.assertIsNone(self.vision.owner)
            self.assertIsNone(self.service.owner)
        self.assertTrue(self.service.closed)
        self.assertTrue(self.vision.closed)

    def test_vision_configuration_is_passed_to_service_factory(self):
        vision = FakeVision()
        factory = Mock(return_value=vision)
        models = {"test-model": Path("models/test.rknn")}
        app = self.make_app(vision_models=models, camera_device="/dev/video9", camera_rotation=90,
                            vision_service_factory=factory)
        with TestClient(app) as client:
            self.assertIs(app.state.vision, vision)
            self.assertEqual(client.get("/api/vision/status").json()["state"], "idle")
            factory.assert_called_once_with(models, device="/dev/video9", rotation=90)
        self.assertTrue(vision.closed)
        self.assertTrue(self.service.closed)

    def test_vision_construction_failure_closes_speech(self):
        factory = Mock(side_effect=RuntimeError("vision initialization failed"))
        app = self.make_app(vision_service_factory=factory)
        with self.assertRaisesRegex(RuntimeError, "vision initialization failed"):
            with TestClient(app):
                self.fail("Startup should fail")
        self.assertTrue(self.service.closed)

    def test_cancelled_startup_closes_management_created_by_constructor_thread(self):
        for repeated in (False, True):
            with self.subTest(repeated_cancellation=repeated):
                entered = threading.Event()
                release = threading.Event()
                constructed = threading.Event()
                closed = threading.Event()
                management = Mock()
                management.close.side_effect = closed.set

                def construct(_directory, speech, vision):
                    self.assertIs(speech, self.service)
                    self.assertIs(vision, self.vision)
                    entered.set()
                    if not release.wait(2):
                        raise TimeoutError("Test did not release management construction")
                    constructed.set()
                    return management

                app = self.make_app(management_state_dir=Path("private-state"),
                                    management_factory=construct)

                async def check_startup():
                    context = app.router.lifespan_context(app)
                    startup = asyncio.create_task(context.__aenter__())
                    try:
                        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                        startup.cancel("startup cancelled")
                        await asyncio.sleep(0)
                        if repeated:
                            for _ in range(2):
                                startup.cancel("repeated startup cancellation")
                                await asyncio.sleep(0)
                        done, _ = await asyncio.wait({startup}, timeout=0.03)
                        self.assertFalse(done, "Cancelled startup abandoned its constructor thread")
                        self.assertFalse(constructed.is_set())
                        self.assertFalse(closed.is_set())
                    finally:
                        release.set()
                        with self.assertRaises(asyncio.CancelledError) as raised:
                            await asyncio.wait_for(startup, 2)
                    self.assertEqual(str(raised.exception), "startup cancelled")
                    self.assertTrue(constructed.is_set())
                    self.assertTrue(closed.is_set())
                    management.close.assert_called_once_with()
                    self.assertTrue(self.service.closed)
                    self.assertTrue(self.vision.closed)

                asyncio.run(check_startup())

    def test_management_constructor_failure_propagates_after_cancelled_startup(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                entered = threading.Event()
                release = threading.Event()

                def construct(*_args):
                    entered.set()
                    if not release.wait(2):
                        raise TimeoutError("Test did not release management construction")
                    raise RuntimeError("management initialization failed")

                app = self.make_app(management_state_dir=Path("private-state"),
                                    management_factory=construct)

                async def check_failure():
                    context = app.router.lifespan_context(app)
                    startup = asyncio.create_task(context.__aenter__())
                    try:
                        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
                        if cancelled:
                            startup.cancel()
                            done, _ = await asyncio.wait({startup}, timeout=0.03)
                            self.assertFalse(done, "Cancelled startup abandoned its failing constructor")
                    finally:
                        release.set()
                        with self.assertRaisesRegex(RuntimeError, "management initialization failed"):
                            await asyncio.wait_for(startup, 2)
                    self.assertTrue(self.service.closed)
                    self.assertTrue(self.vision.closed)
                    self.assertIsNone(app.state.management)

                asyncio.run(check_failure())

    def test_shutdown_closes_services_concurrently(self):
        app = self.make_app()
        closing = threading.Barrier(2, timeout=2)

        def close(service):
            closing.wait()
            service.closed = True

        self.service.close = lambda: close(self.service)
        self.vision.close = lambda: close(self.vision)
        with TestClient(app):
            pass
        self.assertTrue(self.service.closed)
        self.assertTrue(self.vision.closed)

    def test_shutdown_waits_for_other_service_even_when_one_close_fails(self):
        for failed_name in ("speech", "vision"):
            with self.subTest(failed_service=failed_name):
                app = self.make_app()
                failed, remaining = ((self.service, self.vision) if failed_name == "speech"
                                     else (self.vision, self.service))
                started = threading.Event()
                release = threading.Event()

                def fail():
                    raise RuntimeError(f"{failed_name} close failed")

                def finish():
                    started.set()
                    if not release.wait(2):
                        raise TimeoutError("Test did not release the remaining service")
                    remaining.closed = True

                failed.close = fail
                remaining.close = finish

                async def check_shutdown():
                    context = app.router.lifespan_context(app)
                    await context.__aenter__()
                    shutdown = asyncio.create_task(context.__aexit__(None, None, None))
                    try:
                        self.assertTrue(await asyncio.to_thread(started.wait, 1))
                        await asyncio.sleep(0.05)
                        self.assertFalse(shutdown.done(), "Shutdown returned before both services finished")
                    finally:
                        release.set()
                        with self.assertRaises(ExceptionGroup) as raised:
                            await asyncio.wait_for(shutdown, 2)
                    self.assertEqual(len(raised.exception.exceptions), 1)
                    self.assertEqual(str(raised.exception.exceptions[0]), f"{failed_name} close failed")

                asyncio.run(check_shutdown())
                self.assertTrue(remaining.closed)

    def test_cancelled_shutdown_waits_for_both_services_to_finish(self):
        for timing in ("before_close_starts", "during_close", "repeated_cancellation"):
            with self.subTest(timing=timing):
                app = self.make_app()
                services = (self.service, self.vision)
                started = (threading.Event(), threading.Event())
                release = threading.Event()
                close_calls = [0, 0]

                def close(index):
                    close_calls[index] += 1
                    started[index].set()
                    if not release.wait(2):
                        raise TimeoutError("Test did not release service cleanup")
                    services[index].closed = True

                self.service.close = lambda: close(0)
                self.vision.close = lambda: close(1)

                async def check_cancellation():
                    context = app.router.lifespan_context(app)
                    await context.__aenter__()
                    shutdown = asyncio.create_task(context.__aexit__(None, None, None))
                    # Let __aexit__ enter cleanup; cancelling before this turn
                    # would cancel the coroutine before it could run at all.
                    await asyncio.sleep(0)
                    try:
                        if timing != "before_close_starts":
                            for event in started:
                                self.assertTrue(await asyncio.to_thread(event.wait, 1))
                        shutdown.cancel()
                        await asyncio.sleep(0)
                        if timing == "repeated_cancellation":
                            for _ in range(2):
                                shutdown.cancel()
                                await asyncio.sleep(0)
                        done, _ = await asyncio.wait({shutdown}, timeout=0.03)
                        self.assertFalse(done, "Cancellation returned while cleanup was unfinished")
                        for event in started:
                            self.assertTrue(await asyncio.to_thread(event.wait, 1),
                                            "Cancellation prevented a service from closing")
                    finally:
                        release.set()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(shutdown, 2)
                    # Assert before asyncio.run shuts down its executor: that
                    # implicit wait must not conceal an early lifespan exit.
                    self.assertEqual(close_calls, [1, 1])
                    self.assertTrue(all(service.closed for service in services))

                asyncio.run(check_cancellation())

    def test_second_connection_cannot_control_first(self):
        with TestClient(self.make_app()) as client, self.connect(client) as first:
            first.receive_json()
            first.send_json({"type": "start"})
            self.receive(first, lambda m: m.get("state") == "listening")
            with self.connect(client) as second:
                self.assertFalse(second.receive_json()["owned"])
                for operation, code in (("start", "busy"), ("stop", "not_owner")):
                    second.send_json({"type": operation})
                    result = self.receive(second, lambda m: m["type"] == "error")
                    self.assertEqual(result["error"]["code"], code)
            self.assertIsNotNone(self.service.owner)

    def test_invalid_command_and_binary_do_not_start_recording(self):
        with TestClient(self.make_app()) as client, self.connect(client) as socket:
            socket.receive_json()
            for raw in ("{", "[]", '{"type":"unknown"}', '{"type":"start","extra":1}'):
                socket.send_text(raw)
                result = self.receive(socket, lambda m: m["type"] == "error")
                self.assertEqual(result["error"]["code"], "invalid_request")
            self.assertIsNone(self.service.owner)
            socket.send_bytes(b"audio must not be uploaded")
            with self.assertRaises(WebSocketDisconnect):
                socket.receive_json()

    def test_origin_host_and_connection_limit(self):
        with TestClient(self.make_app()) as client:
            self.assertEqual(client.get("/api/health", headers={"host": "untrusted.example"}).status_code, 400)
            for headers in ({}, {"origin": "http://untrusted.example"}):
                with self.assertRaises(WebSocketDisconnect), client.websocket_connect("/api/speech/live", headers=headers):
                    pass
            with ExitStack() as stack:
                for _ in range(4):
                    socket = stack.enter_context(self.connect(client))
                    socket.receive_json()
                with self.assertRaises(WebSocketDisconnect), self.connect(client):
                    pass

    def test_known_page_refresh_works_without_swallowing_api_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text("<title>Duck</title>")
            with TestClient(self.make_app(web_root=root)) as client:
                self.assertEqual(client.get("/voice").text, "<title>Duck</title>")
                self.assertEqual(client.get("/overview").status_code, 200)
                self.assertEqual(client.get("/api/missing").status_code, 404)
                self.assertEqual(client.get("/missing.js").status_code, 404)


if __name__ == "__main__":
    unittest.main()
