"""Control protocol tests with a fake microphone service; no hardware access."""

from contextlib import ExitStack
from pathlib import Path
import tempfile
import threading
import unittest

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from radxa_duck.server import create_app
from radxa_duck.speech import SpeechError


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


class ServerTests(unittest.TestCase):
    def make_app(self, **kwargs):
        self.service = FakeSpeech()
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
