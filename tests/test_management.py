"""HTTP contracts using fake hardware and real private config/task storage."""

from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

from radxa_duck.cloud_config import CloudConfigStore
from radxa_duck.management import ManagementServices, UpdateStatus
from radxa_duck.network import NetworkError, NetworkService
from radxa_duck.server import create_app
from radxa_duck.tasks import TaskManager
from test_server import FakeSpeech, FakeVision


WRITE = {"Origin": "http://testserver", "X-Radxa-Duck-Request": "1"}
SECRET = "never-return-this-private-key"


class FakeNetwork:
    validate_connection = staticmethod(NetworkService.validate_connection)

    def __init__(self):
        self.release = threading.Event()
        self.calls = []
        self.fail = False

    def snapshot(self):
        return {"schema_version": 1, "state": "connected", "ssid": "test"}

    def scan(self, context):
        context.update("scanning")
        return {"networks": [{"ssid": "test", "security": "wpa-psk"}]}

    def connect(self, context, **arguments):
        self.calls.append(arguments)
        context.update("awaiting_confirmation")
        while not self.release.wait(0.01):
            context.check_cancelled()
        if self.fail:
            context.update("rolled_back")
            raise NetworkError("activation_failed", rollback="succeeded")
        return {"connected": True, "confirmed": True}

    def confirm(self, task_id):
        self.release.set()
        return {"task_id": task_id, "confirmed": True}

    def close(self):
        self.release.set()


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.speech, self.vision = FakeSpeech(), FakeVision()
        self.network = FakeNetwork()
        self.tasks = TaskManager(self.path)
        self.cloud = CloudConfigStore(self.path)
        self.output = {"volume_percent": 0, "muted": True}
        def set_output(**fields):
            self.output.update(fields)
            return dict(self.output)
        self.management = SimpleNamespace(
            device=SimpleNamespace(snapshot=lambda: {"schema_version": 1, "hostname": "duck"}),
            network=self.network, cloud=self.cloud, tasks=self.tasks,
            audio=SimpleNamespace(snapshot=lambda: dict(self.output), set_output=set_output),
            updates=UpdateStatus(),
            power=SimpleNamespace(snapshot=lambda: {"capabilities": {}},
                                  execute=lambda context, action: {"action": action, "state": "scheduled"}),
            close=lambda: (self.network.close(), self.tasks.close(), self.cloud.close()))
        self.app = create_app(Path("model"), Path("vad"), origins=("http://testserver",),
                              service_factory=lambda *_: self.speech,
                              vision_service_factory=lambda *_args, **_kwargs: self.vision,
                              management_state_dir=self.path, management_factory=lambda *_: self.management)
        self.client = TestClient(self.app).__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def post(self, path, fields):
        return self.client.post(path, json=fields, headers=WRITE)

    def terminal(self, task_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            response = self.client.get("/api/tasks/" + task_id)
            self.assertEqual(response.status_code, 200)
            value = response.json()
            if value["state"] not in ("queued", "running"):
                return value
            threading.Event().wait(0.01)
        self.fail("Task did not finish")

    def save_cloud(self, **extra):
        fields = {"expected_revision": 0, "base_url": "https://example.org/v1", "model": "test", "api_key": SECRET}
        fields.update(extra)
        return self.client.put("/api/cloud/config", json=fields, headers=WRITE)

    def test_read_only_endpoints_are_uncached_and_hardware_free(self):
        for path in ("device/status", "network/status", "audio", "cloud/config", "power/status", "update/status", "tasks"):
            with self.subTest(path=path):
                response = self.client.get("/api/" + path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertFalse(self.speech.owner)
        self.assertFalse(self.vision.owner)

    def test_mutations_require_origin_and_custom_header(self):
        for headers in ({}, {"Origin": "http://evil.invalid", "X-Radxa-Duck-Request": "1"},
                        {"Origin": "http://testserver"}, {"X-Radxa-Duck-Request": "1"}):
            response = self.client.patch("/api/audio", json={"muted": False}, headers=headers)
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.output, {"volume_percent": 0, "muted": True})

    def test_cloud_revision_and_key_redaction(self):
        response = self.save_cloud()
        self.assertEqual(response.status_code, 200)
        first = response.json()
        self.assertEqual(first["revision"], 1)
        self.assertTrue(first["has_api_key"])
        self.assertNotIn(SECRET, response.text)
        read = self.client.get("/api/cloud/config")
        self.assertNotIn(SECRET, read.text)
        self.assertEqual(self.save_cloud().status_code, 409)
        saved = self.client.put("/api/cloud/config", json={"expected_revision": 1, "model": "new"}, headers=WRITE)
        self.assertTrue(saved.json()["has_api_key"])
        removed = self.client.put("/api/cloud/config", json={"expected_revision": 2, "api_key": None}, headers=WRITE)
        self.assertFalse(removed.json()["has_api_key"])

    def test_rejects_unbounded_duplicate_nonfinite_and_unknown_json(self):
        for content in ('{"muted":true,"muted":false}', '{"volume_percent":NaN}', '[true]',
                        '{"unknown":true}', '[' * 1100 + '0' + ']' * 1100,
                        '{"muted":"' + 'x' * 8192 + '"}'):
            response = self.client.patch("/api/audio", content=content,
                                         headers={**WRITE, "Content-Type": "application/json"})
            self.assertEqual(response.status_code, 400)
        response = self.client.patch("/api/audio", content='{}', headers=WRITE)
        self.assertEqual(response.status_code, 400)

    def test_network_task_survives_http_and_retries_are_idempotent(self):
        fields = {"request_id": "network-test-001", "ssid": "new", "password": "private-pass"}
        first = self.post("/api/network/connect", fields)
        self.assertEqual(first.status_code, 202)
        task_id = first.json()["task_id"]
        again = self.post("/api/network/connect", fields)
        self.assertEqual(again.json()["task_id"], task_id)
        self.assertEqual(first.headers["location"], "/api/tasks/" + task_id)
        self.assertEqual(self.post("/api/network/scan", {"request_id": "another-scan"}).status_code, 409)
        confirm = self.post("/api/network/confirm", {"task_id": task_id})
        self.assertTrue(confirm.json()["confirmed"])
        self.assertEqual(self.terminal(task_id)["state"], "succeeded")
        self.assertTrue(self.post("/api/network/confirm", {"task_id": task_id}).json()["confirmed"])
        self.assertEqual(len(self.network.calls), 1)
        self.assertEqual(self.network.calls[0]["task_id"], task_id)
        journal = (self.path / "tasks.json").read_text()
        self.assertNotIn("private-pass", journal)
        self.assertNotIn('"ssid"', journal)
        listed = self.client.get("/api/tasks?kind=network.connect&active=false").json()["tasks"]
        self.assertEqual(listed[0]["task_id"], task_id)

    def test_failed_network_task_preserves_rollback_status(self):
        self.network.fail = True
        self.network.release.set()
        response = self.post("/api/network/connect", {"request_id": "network-failed", "ssid": "new", "password": "private-pass"})
        value = self.terminal(response.json()["task_id"])
        self.assertEqual(value["state"], "failed")
        self.assertEqual(value["phase"], "rolled_back")
        self.assertEqual(value["result"], {"rollback": "succeeded"})
        self.assertNotIn("private-pass", str(value))

    def test_invalid_network_settings_do_not_create_tasks(self):
        response = self.post("/api/network/connect", {"request_id": "network-bad", "ssid": "new", "password": "short"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.tasks.list(), [])

    def test_cloud_test_uses_saved_revision_as_background_task(self):
        self.save_cloud()
        with patch.object(self.cloud, "test_connection", return_value={
                "schema_version": 1, "revision": 1, "state": "failed", "error": {
                    "code": "authentication_failed", "message": "The cloud service rejected the API key"}}):
            response = self.post("/api/cloud/test", {"request_id": "cloud-test-001", "expected_revision": 1})
            value = self.terminal(response.json()["task_id"])
        self.assertEqual(value["error"]["code"], "authentication_failed")
        self.assertNotIn(SECRET, str(value))
        self.assertEqual(self.post("/api/cloud/test", {"request_id": "cloud-test-002", "expected_revision": True}).status_code, 400)

    def test_update_reports_missing_delivery_prerequisites(self):
        with patch("radxa_duck.management.subprocess.run", side_effect=FileNotFoundError):
            result = self.client.get("/api/update/status").json()
            self.assertEqual(result["reason"], "package_not_installed")
            response = self.post("/api/update/check", {"request_id": "update-check-001"})
            task = self.terminal(response.json()["task_id"])
            self.assertEqual(task["error"]["code"], "update_unavailable")
            self.assertFalse(task["result"]["capabilities"]["install"])
        self.assertEqual(self.post("/api/update/install", {"request_id": "update-install", "version": "0.1.0"}).status_code, 409)

    def test_power_accepts_only_explicit_supported_action(self):
        self.assertEqual(self.post("/api/power", {"request_id": "power-test-001", "action": "halt"}).status_code, 400)
        result = self.post("/api/power", {"request_id": "power-test-001", "action": "reboot"})
        self.assertEqual(self.terminal(result.json()["task_id"])["result"]["state"], "scheduled")

    def test_task_lookup_and_filters_validate_input(self):
        self.assertEqual(self.client.get("/api/tasks/" + "0" * 32).status_code, 404)
        self.assertEqual(self.client.get("/api/tasks/no-such-task").status_code, 400)
        self.assertEqual(self.client.get("/api/tasks?kind=unknown").status_code, 400)
        self.assertEqual(self.client.get("/api/tasks?active=1").status_code, 400)
        self.assertEqual(self.client.get("/api/tasks?password=anything").status_code, 400)


if __name__ == "__main__":
    unittest.main()
