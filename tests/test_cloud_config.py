"""Private temporary storage and local/fake transports; never real cloud keys."""

from concurrent.futures import ThreadPoolExecutor
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import ssl
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error

from radxa_duck.cloud_config import CloudConfigError, CloudConfigStore, MAX_FILE_BYTES, MAX_RESPONSE_BYTES


FAKE_KEY = "test-only-secret-DO-NOT-USE"
CONFIG = {"base_url": "https://cloud.example/v1", "model": "example-model", "timeout_seconds": 8,
          "api_key": FAKE_KEY}


class Response(io.BytesIO):
    def __init__(self, body=b'{"data":[{"id":"example-model"}]}', *, status=200):
        super().__init__(body)
        self.status = status


class Opener:
    def __init__(self, *, response=None, error=None):
        self.response = response if response is not None else Response()
        self.error = error
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append((request, timeout))
        if self.error is not None:
            raise self.error
        return self.response


class CloudConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"

    def store(self, **kwargs):
        value = CloudConfigStore(self.state, **kwargs)
        self.addCleanup(value.close)
        return value

    def assert_code(self, code, callable_, *args, **kwargs):
        with self.assertRaises(CloudConfigError) as raised:
            callable_(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        self.assertNotIn(FAKE_KEY, repr(raised.exception))
        return raised.exception

    def private_file(self, content):
        self.state.mkdir(mode=0o700, exist_ok=True)
        target = self.state / "cloud-config.json"
        target.write_bytes(content)
        target.chmod(0o600)
        return target

    def test_defaults_and_saved_config_have_only_redacted_public_fields(self):
        store = self.store()
        initial = store.snapshot()
        self.assertEqual(initial, {"schema_version": 1, "revision": 0, "configured": False,
                                   "provider": "openai-compatible", "base_url": None, "model": None,
                                   "timeout_seconds": 8, "has_api_key": False, "updated_utc": None})
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        result = store.save(CONFIG, expected_revision=0)
        self.assertEqual(result["revision"], 1)
        self.assertTrue(result["configured"])
        self.assertTrue(result["has_api_key"])
        self.assertNotIn("api_key", result)
        self.assertNotIn(FAKE_KEY, repr(result))
        self.assertTrue(result["updated_utc"].endswith("+00:00"))
        result["model"] = "mutated"
        self.assertEqual(store.snapshot()["model"], "example-model")
        for name in ("cloud-config.json", "cloud-config.lock"):
            self.assertEqual(stat.S_IMODE((self.state / name).stat().st_mode), 0o600)
        store.close()
        persisted = self.store().snapshot()
        self.assertEqual(persisted["revision"], 1)
        self.assertTrue(persisted["has_api_key"])

    def test_key_omission_preserves_null_clears_and_nonempty_replaces(self):
        store = self.store()
        store.save(CONFIG, expected_revision=0)
        store.save({"model": "new-model"}, expected_revision=1)
        private = json.loads((self.state / "cloud-config.json").read_bytes())
        self.assertEqual(private["api_key"], FAKE_KEY)
        result = store.save({"api_key": None}, expected_revision=2)
        self.assertFalse(result["has_api_key"])
        self.assertIsNone(json.loads((self.state / "cloud-config.json").read_bytes())["api_key"])
        self.assertTrue(store.save({"api_key": "replacement-test-key"}, expected_revision=3)["has_api_key"])
        self.assert_code("invalid_request", store.save, {"api_key": ""}, expected_revision=4)
        self.assertEqual(store.snapshot()["revision"], 4)

    def test_runtime_snapshot_is_private_revisioned_and_detached_from_storage(self):
        store = self.store()
        self.assert_code("not_configured", store.runtime_snapshot)
        store.save(CONFIG, expected_revision=0)
        saved = store.runtime_snapshot(expected_revision=1)
        self.assertEqual(saved["api_key"], FAKE_KEY)
        saved["model"] = "mutated"
        self.assertEqual(store.runtime_snapshot()["model"], "example-model")
        self.assertNotIn("api_key", store.snapshot())
        self.assert_code("conflict", store.runtime_snapshot, expected_revision=0)
        for value in (True, -1, "1", 2**53):
            self.assert_code("invalid_request", store.runtime_snapshot, expected_revision=value)
        store.close()
        self.assert_code("closed", store.runtime_snapshot)

    def test_connection_feedback_keeps_tested_version_when_configuration_changes(self):
        store = self.store(opener=Opener())
        self.assertEqual(store.connection_snapshot()['state'], 'unknown')
        store.save(CONFIG, expected_revision=0)
        result = store.test_connection(expected_revision=1)
        store.save({'model': 'next-model'}, expected_revision=1)
        check = store.connection_snapshot()
        self.assertEqual(check['revision'], 1)
        self.assertEqual(check['state'], 'succeeded')
        self.assertEqual(check['checked_at'], result['checked_at'])
        self.assertIsNone(check['error_code'])
        self.assertNotIn(FAKE_KEY, repr(check))
        check['state'] = 'changed'
        self.assertEqual(store.connection_snapshot()['state'], 'succeeded')

    def test_field_validation_rejects_invalid_config_without_a_write(self):
        store = self.store()
        changes = ({"provider": "other"}, {"extra": True}, {"model": ""}, {"model": "a b"},
                   {"model": "a\u2028b"}, {"model": "x" * 129}, {"timeout_seconds": True},
                   {"timeout_seconds": 0}, {"timeout_seconds": 121}, {"timeout_seconds": "8"},
                   {"api_key": FAKE_KEY + "\n"}, {"api_key": "x" * 4097}, {"api_key": "非ASCII"})
        for change in changes:
            with self.subTest(change=next(iter(change))):
                self.assert_code("invalid_request", store.save, {**CONFIG, **change}, expected_revision=0)
                self.assertEqual(store.snapshot()["revision"], 0)
                self.assertFalse((self.state / "cloud-config.json").exists())
        self.assert_code("invalid_request", store.save, [], expected_revision=0)
        self.assert_code("invalid_request", store.save, {}, expected_revision=0)

    def test_url_validation_and_explicit_loopback_http(self):
        store = self.store()
        rejected = ("file:///tmp/models", "ftp://cloud.example/v1", "https://u:p@cloud.example/v1",
                    "https://cloud.example/v1?token=" + FAKE_KEY, "https://cloud.example/#fragment",
                    "https://cloud.example/v1?", "https://cloud.example/#", "https://cloud.example/\\v1",
                    "https://cloud.example/\r\n", "https://cloud.example:0/v1", "https://cloud.example:65536/v1",
                    "http://localhost/v1", "https:///missing", "https://cloud.example/" + "x" * 2048)
        for url in rejected:
            with self.subTest(url=url.split("?")[0]):
                self.assert_code("invalid_request", store.save, {**CONFIG, "base_url": url}, expected_revision=0)
        for url in ("http://localhost:8080/v1/", "http://127.0.0.1/v1", "http://[::1]/v1"):
            local_store = self.store(allow_local_http=True)
            revision = local_store.snapshot()["revision"]
            self.assertEqual(local_store.save({**CONFIG, "base_url": url}, expected_revision=revision)["base_url"],
                             url.rstrip("/"))
        local_store = self.store(allow_local_http=True)
        self.assert_code("invalid_request", local_store.save, {**CONFIG, "base_url": "http://192.168.1.2/v1"},
                         expected_revision=local_store.snapshot()["revision"])

    def test_https_private_address_is_explicitly_allowed_and_url_canonicalized(self):
        store = self.store()
        self.assertEqual(store.save({**CONFIG, "base_url": "https://192.168.1.2:443/v1/"},
                                    expected_revision=0)["base_url"], "https://192.168.1.2:443/v1")
        result = store.save({"base_url": "https://CLOUD.EXAMPLE/v1///"}, expected_revision=1)
        self.assertEqual(result["base_url"], "https://cloud.example/v1")

    def test_revisions_are_required_and_prevent_lost_updates_across_store_instances(self):
        first, second = self.store(), self.store()
        gate = threading.Barrier(2)

        def save(store, name):
            gate.wait(timeout=5)
            try:
                return store.save({**CONFIG, "model": name}, expected_revision=0)
            except CloudConfigError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(save, first, "first"), executor.submit(save, second, "second")]
            results = [future.result(timeout=5) for future in futures]
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertEqual(results.count("conflict"), 1)
        self.assertEqual(first.snapshot(), second.snapshot())
        for revision in (None, True, -1, "1", 2 ** 53):
            self.assert_code("invalid_request", first.save, {}, expected_revision=revision)

    def test_an_externally_held_lock_times_out_without_changing_config(self):
        store = self.store()
        with (self.state / "cloud-config.lock").open("rb") as locked:
            fcntl.flock(locked.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch("radxa_duck.cloud_config.time.monotonic", side_effect=[0, 3]):
                self.assert_code("config_unavailable", store.save, CONFIG, expected_revision=0)
        self.assertEqual(store.snapshot()["revision"], 0)

    def test_invalid_persisted_schema_revision_and_metadata_are_fail_closed(self):
        store = self.store()
        store.save(CONFIG, expected_revision=0)
        path = self.state / "cloud-config.json"
        original = json.loads(path.read_bytes())
        for replacement in ({"schema_version": 2}, {"schema_version": True}, {"revision": 0},
                            {"revision": True}, {"revision": 2 ** 53}, {"updated_utc": FAKE_KEY},
                            {"base_url": "https://CLOUD.EXAMPLE/v1"}, {"unexpected": "value"}):
            self.private_file(json.dumps({**original, **replacement}).encode())
            self.assert_code("config_unavailable", store.snapshot)

    def test_corruption_is_fail_closed_and_original_file_is_preserved(self):
        for content in (b'{"api_key":"' + FAKE_KEY.encode() + b'"}', b"not JSON", b"[" * 1100 + b"]" * 1100,
                        b"x" * (MAX_FILE_BYTES + 1)):
            with self.subTest(content_length=len(content)):
                target = self.private_file(content)
                self.assert_code("config_unavailable", self.store)
                self.assertEqual(target.read_bytes(), content)
        (self.state / "cloud-config.json").unlink()
        store = self.store()
        store.save(CONFIG, expected_revision=0)
        self.private_file(b"broken later")
        self.assert_code("config_unavailable", store.save, CONFIG, expected_revision=1)
        self.assertEqual((self.state / "cloud-config.json").read_bytes(), b"broken later")

    def test_insecure_storage_permissions_links_and_nonregular_files_are_rejected(self):
        self.state.mkdir(mode=0o755)
        self.state.chmod(0o755)
        self.assert_code("config_unavailable", self.store)
        self.state.chmod(0o700)
        outside = self.root / "outside"
        outside.write_bytes(b"unchanged")
        outside.chmod(0o600)
        target = self.state / "cloud-config.json"
        target.symlink_to(outside)
        self.assert_code("config_unavailable", self.store)
        self.assertEqual(outside.read_bytes(), b"unchanged")
        target.unlink()
        os.link(outside, target)
        self.assert_code("config_unavailable", self.store)
        target.unlink()
        os.mkfifo(target, 0o600)
        self.assert_code("config_unavailable", self.store)
        target.unlink()
        self.private_file(b"{}").chmod(0o644)
        self.assert_code("config_unavailable", self.store)
        target.unlink()
        lock = self.state / "cloud-config.lock"
        lock.unlink(missing_ok=True)
        lock.symlink_to(outside)
        self.assert_code("config_unavailable", self.store)
        self.assertEqual(outside.read_bytes(), b"unchanged")

    def test_symlink_state_directory_is_rejected(self):
        outside = self.root / "other-state"
        outside.mkdir(mode=0o700)
        self.state.symlink_to(outside)
        self.assert_code("config_unavailable", self.store)
        self.assertEqual(list(outside.iterdir()), [])

    def test_atomic_replace_failure_keeps_previous_config_and_cleans_temporary_file(self):
        store = self.store()
        store.save(CONFIG, expected_revision=0)
        original = (self.state / "cloud-config.json").read_bytes()
        with patch("radxa_duck.cloud_config.os.replace", side_effect=OSError(FAKE_KEY)):
            self.assert_code("config_unavailable", store.save, {"model": "changed"}, expected_revision=1)
        self.assertEqual((self.state / "cloud-config.json").read_bytes(), original)
        self.assertEqual(list(self.state.glob(".cloud-config-*")), [])
        self.assertEqual(store.snapshot()["revision"], 1)

    def test_encoded_size_limit_is_checked_before_persisting(self):
        store = self.store()
        oversized = {**CONFIG, "base_url": "https://cloud.example/" + "界" * 2000,
                     "model": "界" * 128, "api_key": "x" * 4096}
        self.assert_code("invalid_request", store.save, oversized, expected_revision=0)
        self.assertFalse((self.state / "cloud-config.json").exists())

    def test_connection_uses_saved_endpoint_key_model_and_timeout_only(self):
        opener = Opener()
        store = self.store(opener=opener)
        self.assert_code("not_configured", store.test_connection)
        store.save(CONFIG, expected_revision=0)
        self.assert_code("conflict", store.test_connection, expected_revision=0)
        result = store.test_connection(expected_revision=1)
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["revision"], 1)
        self.assertTrue(result["model_available"])
        self.assertIsNone(result["error"])
        self.assertGreaterEqual(result["elapsed_ms"], 0)
        self.assertNotIn(FAKE_KEY, repr(result))
        self.assertEqual(len(opener.calls), 1)
        request, timeout = opener.calls[0]
        self.assertEqual(request.full_url, "https://cloud.example/v1/models")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + FAKE_KEY)
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(timeout, 8)

    def test_connection_can_be_anonymous_after_key_deletion(self):
        opener = Opener()
        store = self.store(opener=opener)
        store.save({**CONFIG, "api_key": None}, expected_revision=0)
        self.assertEqual(store.test_connection()["state"], "succeeded")
        self.assertIsNone(opener.calls[0][0].get_header("Authorization"))

    def test_models_response_validation_limit_and_model_availability(self):
        cases = ((b'{"data":[{"id":"other-model"}]}', "model_unavailable", False),
                 (b'{"data":[]}', "model_unavailable", False),
                 (b'{"data":[{}]}', "invalid_response", None),
                 (b'{"data":[{"id":1}]}', "invalid_response", None),
                 (b'{"data":{}}', "invalid_response", None),
                 (b"not JSON " + FAKE_KEY.encode(), "invalid_response", None),
                 (b"[" * 1100 + b"]" * 1100, "invalid_response", None),
                 (b"x" * (MAX_RESPONSE_BYTES + 1), "response_too_large", None))
        for raw, code, available in cases:
            with self.subTest(code=code, length=len(raw)):
                store = self.store(opener=Opener(response=Response(raw)))
                current = store.snapshot()["revision"]
                store.save(CONFIG, expected_revision=current)
                result = store.test_connection()
                self.assertEqual(result["state"], "failed")
                self.assertEqual(result["error"]["code"], code)
                self.assertEqual(result["model_available"], available)
                self.assertNotIn(FAKE_KEY, repr(result))

    def test_http_error_categories_discard_server_error_bodies_and_exception_text(self):
        for status, code in ((401, "authentication_failed"), (403, "permission_denied"),
                             (404, "endpoint_unavailable"), (429, "rate_limited"),
                             (302, "cloud_unavailable"), (503, "cloud_unavailable")):
            with self.subTest(status=status):
                body = io.BytesIO(FAKE_KEY.encode())
                error = urllib.error.HTTPError("https://invalid/?" + FAKE_KEY, status, FAKE_KEY, {}, body)
                store = self.store(opener=Opener(error=error))
                current = store.snapshot()["revision"]
                store.save(CONFIG, expected_revision=current)
                result = store.test_connection()
                self.assertEqual(result["error"]["code"], code)
                self.assertNotIn(FAKE_KEY, repr(result))
                self.assertTrue(body.closed)

    def test_transport_errors_are_bounded_public_categories_without_sensitive_detail(self):
        errors = ((TimeoutError(FAKE_KEY), "timeout"), (socket.timeout(FAKE_KEY), "timeout"),
                  (ssl.SSLError(FAKE_KEY), "tls_failed"),
                  (urllib.error.URLError(ssl.SSLError(FAKE_KEY)), "tls_failed"),
                  (urllib.error.URLError(TimeoutError(FAKE_KEY)), "timeout"),
                  (urllib.error.URLError(FAKE_KEY), "network_error"),
                  (OSError(FAKE_KEY), "network_error"), (RuntimeError(FAKE_KEY), "network_error"))
        for error, code in errors:
            with self.subTest(kind=type(error).__name__, code=code):
                store = self.store(opener=Opener(error=error))
                current = store.snapshot()["revision"]
                store.save(CONFIG, expected_revision=current)
                result = store.test_connection()
                self.assertEqual(result["error"]["code"], code)
                self.assertNotIn(FAKE_KEY, repr(result))

    def test_read_deadline_prevents_a_slow_response_from_succeeding(self):
        store = self.store(opener=Opener())
        store.save(CONFIG, expected_revision=0)
        with patch("radxa_duck.cloud_config.time.monotonic", side_effect=[0, 0, 9, 9]):
            result = store.test_connection()
        self.assertEqual(result["error"]["code"], "timeout")

    def test_connection_releases_lock_and_reports_the_revision_it_tested(self):
        entered, release = threading.Event(), threading.Event()
        opener = Opener()
        original_open = opener.open

        def blocking_open(request, *, timeout):
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError()
            return original_open(request, timeout=timeout)

        opener.open = blocking_open
        store, other = self.store(opener=opener), self.store()
        store.save(CONFIG, expected_revision=0)
        with ThreadPoolExecutor(max_workers=2) as executor:
            running = executor.submit(store.test_connection, expected_revision=1)
            try:
                self.assertTrue(entered.wait(timeout=5))
                updated = executor.submit(other.save, {"api_key": "new-test-key"}, expected_revision=1)
                self.assertEqual(updated.result(timeout=2)["revision"], 2)
            finally:
                release.set()
            result = running.result(timeout=5)
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(opener.calls[0][0].get_header("Authorization"), "Bearer " + FAKE_KEY)

    def test_close_is_idempotent_and_rejects_new_operations(self):
        store = self.store()
        store.close()
        store.close()
        self.assert_code("closed", store.snapshot)
        self.assert_code("closed", store.save, CONFIG, expected_revision=0)
        self.assert_code("closed", store.test_connection)

    def test_real_local_transport_never_follows_redirect_or_forwards_credentials(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, self.headers.get("Authorization")))
                if self.path == "/v1/models":
                    self.send_response(302)
                    self.send_header("Location", "/redirect-target")
                    self.end_headers()
                    self.wfile.write(FAKE_KEY.encode())
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"data":[{"id":"example-model"}]}')

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            store = self.store(allow_local_http=True)
            store.save({**CONFIG, "base_url": f"http://127.0.0.1:{server.server_port}/v1"}, expected_revision=0)
            result = store.test_connection()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.assertEqual(result["error"]["code"], "cloud_unavailable")
        self.assertEqual(requests, [("/v1/models", "Bearer " + FAKE_KEY)])
        self.assertNotIn(FAKE_KEY, repr(result))


if __name__ == "__main__":
    unittest.main()
