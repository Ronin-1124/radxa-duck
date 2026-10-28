"""Task execution and recovery against private, disposable local journals."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from radxa_duck.tasks import (MAX_FILE_BYTES, MAX_RESULT_BYTES, TaskCancelled, TaskFailure,
                              TaskManager, TaskManagerError)


SECRET = "test-only-secret-not-task-status"


class TaskTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"

    def manager(self, **kwargs):
        manager = TaskManager(self.state, **kwargs)
        self.addCleanup(manager.close)
        return manager

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(TaskManagerError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)
        self.assertNotIn(SECRET, repr(raised.exception))

    def wait(self, manager, task_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            task = manager.get(task_id)
            if task["state"] not in {"queued", "running"}:
                return task
            time.sleep(0.005)
        self.fail("Background operation did not finish")

    def journal(self):
        return json.loads((self.state / "tasks.json").read_bytes())

    def write(self, content):
        self.state.mkdir(mode=0o700, exist_ok=True)
        target = self.state / "tasks.json"
        target.write_bytes(content)
        target.chmod(0o600)
        return target

    def blocked(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def operation(context):
            context.update("waiting", 30)
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test release missing")
            return {"ready": True}

        return operation, entered, release

    def test_success_updates_progress_and_persists_only_public_json(self):
        manager = self.manager()
        observed = []

        def operation(context):
            observed.append(context.task_id)
            # A captured parameter never becomes journal data automatically.
            hidden = SECRET
            context.check_cancelled()
            context.update("connecting", 20)
            self.assertEqual(manager.get(context.task_id)["phase"], "connecting")
            self.assertEqual(hidden, SECRET)
            return {"connected": True, "items": [1, None, "ok"]}

        queued = manager.submit("wifi_connect", operation, request_id="request_001", key="network")
        self.assertEqual(queued["state"], "queued")
        self.assertRegex(queued["task_id"], r"^[a-f0-9]{32}$")
        completed = self.wait(manager, queued["task_id"])
        self.assertEqual(observed, [queued["task_id"]])
        self.assertEqual(completed["state"], "succeeded")
        self.assertEqual(completed["phase"], "connecting")
        self.assertEqual(completed["progress"], 100)
        self.assertEqual(completed["result"], {"connected": True, "items": [1, None, "ok"]})
        self.assertIsNone(completed["error"])
        self.assertEqual(set(completed), {"schema_version", "task_id", "kind", "request_id", "state", "phase",
                                         "progress", "created_utc", "updated_utc", "result", "error"})
        self.assertNotIn(SECRET, (self.state / "tasks.json").read_text())
        self.assertNotIn("network", (self.state / "tasks.json").read_text())
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        for name in ("tasks.json", "tasks.lock"):
            self.assertEqual(stat.S_IMODE((self.state / name).stat().st_mode), 0o600)
        completed["result"]["items"].append("mutated")
        self.assertEqual(manager.get(queued["task_id"])["result"]["items"], [1, None, "ok"])
        manager.close()
        restored = self.manager()
        self.assertEqual(restored.get(queued["task_id"])["state"], "succeeded")

    def test_unknown_exceptions_never_persist_arguments_repr_or_traceback(self):
        manager = self.manager()

        def operation(context):
            context.update("verifying", 45)
            raise RuntimeError(SECRET)

        task = manager.submit("cloud_test", operation, request_id="request_002", key="cloud")
        result = self.wait(manager, task["task_id"])
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["phase"], "verifying")
        self.assertEqual(result["progress"], 45)
        self.assertEqual(result["error"], {"code": "operation_failed", "message": "The operation failed"})
        self.assertIsNone(result["result"])
        self.assertNotIn(SECRET, repr(manager.list()))
        self.assertNotIn(SECRET, (self.state / "tasks.json").read_text())

    def test_deliberate_failure_has_safe_error_and_bounded_result(self):
        manager = self.manager()

        def operation(context):
            context.update("rolling_back", 75)
            raise TaskFailure("connection_failed", "The Wi-Fi connection failed", {"previous_network_restored": True})

        task = manager.submit("wifi_connect", operation, request_id="request_003", key="network")
        result = self.wait(manager, task["task_id"])
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["phase"], "rolling_back")
        self.assertEqual(result["error"]["code"], "connection_failed")
        self.assertTrue(result["result"]["previous_network_restored"])

    def test_non_json_non_finite_recursive_and_oversized_results_fail_safely(self):
        recursive = []
        recursive.append(recursive)
        deeply_nested = None
        for _ in range(100):
            deeply_nested = [deeply_nested]
        for index, value in enumerate((object(), float("nan"), float("inf"), recursive,
                                       deeply_nested, {1: "not a JSON object key"}, {"large": "x" * MAX_RESULT_BYTES})):
            with self.subTest(index=index):
                manager = self.manager()
                task = manager.submit("check", lambda _ctx, result=value: result,
                                      request_id=f"request_{index:03d}", key="check")
                result = self.wait(manager, task["task_id"])
                self.assertEqual(result["state"], "failed")
                self.assertEqual(result["error"]["code"], "invalid_result")
                self.assertIsNone(result["result"])
                manager.close()

    def test_failure_result_is_bounded_and_failure_metadata_validated(self):
        manager = self.manager()

        def operation(_context):
            raise TaskFailure("safe_error", "Safe failure", {"large": "x" * MAX_RESULT_BYTES})

        task = manager.submit("check", operation, request_id="request_004", key="check")
        result = self.wait(manager, task["task_id"])
        self.assertEqual(result["error"]["code"], "invalid_result")
        failure = TaskFailure("BAD CODE " + SECRET, "message\n" + SECRET)
        self.assertEqual(failure.code, "operation_failed")
        self.assertNotIn(SECRET, str(failure))

    def test_same_request_is_idempotent_and_different_kind_is_a_conflict(self):
        manager = self.manager()
        operation, entered, release = self.blocked()
        first = manager.submit("check", operation, request_id="request_005", key="resource")
        self.assertTrue(entered.wait(timeout=3))
        duplicate = manager.submit("check", lambda _ctx: self.fail("duplicate ran"),
                                   request_id="request_005", key="another-resource")
        self.assertEqual(duplicate["task_id"], first["task_id"])
        self.assert_code("conflict", manager.submit, "another_kind", lambda _ctx: None,
                         request_id="request_005", key="another-resource")
        release.set()
        self.wait(manager, first["task_id"])
        repeated = manager.submit("check", lambda _ctx: self.fail("completed duplicate ran"),
                                  request_id="request_005", key="resource")
        self.assertEqual(repeated["task_id"], first["task_id"])
        manager.close()
        restored = self.manager()
        self.assertEqual(restored.submit("check", lambda _ctx: None, request_id="request_005", key="resource")["task_id"],
                         first["task_id"])

    def test_simultaneous_duplicate_requests_schedule_exactly_once(self):
        manager = self.manager()
        count, barrier = [], threading.Barrier(6)

        def submit(_index):
            barrier.wait(timeout=3)
            return manager.submit("check", lambda _ctx: count.append(1), request_id="request_006", key="resource")

        with ThreadPoolExecutor(max_workers=6) as executor:
            values = list(executor.map(submit, range(6)))
        self.assertEqual(len({value["task_id"] for value in values}), 1)
        self.wait(manager, values[0]["task_id"])
        self.assertEqual(count, [1])

    def test_exclusive_keys_cover_queued_and_running_tasks_and_release_after_completion(self):
        manager = self.manager(max_workers=1)
        operation, entered, release = self.blocked()
        first = manager.submit("check", operation, request_id="request_007", key="first")
        self.assertTrue(entered.wait(timeout=3))
        queued = manager.submit("check", lambda _ctx: None, request_id="request_008", key="second")
        self.assertEqual(queued["state"], "queued")
        for key in ("first", "second"):
            self.assert_code("conflict", manager.submit, "check", lambda _ctx: None,
                             request_id="request_009", key=key)
        release.set()
        self.wait(manager, first["task_id"])
        self.wait(manager, queued["task_id"])
        next_task = manager.submit("check", lambda _ctx: True, request_id="request_009", key="first")
        self.assertEqual(self.wait(manager, next_task["task_id"])["result"], True)

    def test_different_resources_really_run_in_parallel(self):
        manager = self.manager(max_workers=2)
        first_operation, first_entered, first_release = self.blocked()
        second_operation, second_entered, second_release = self.blocked()
        first = manager.submit("check", first_operation, request_id="request_010", key="first")
        second = manager.submit("check", second_operation, request_id="request_011", key="second")
        self.assertTrue(first_entered.wait(timeout=3))
        self.assertTrue(second_entered.wait(timeout=3))
        first_release.set()
        second_release.set()
        self.wait(manager, first["task_id"])
        self.wait(manager, second["task_id"])

    def test_history_is_bounded_recent_first_and_does_not_evict_active_tasks(self):
        manager = self.manager(history_limit=2)
        operation, entered, release = self.blocked()
        active = manager.submit("check", operation, request_id="request_012", key="active")
        self.assertTrue(entered.wait(timeout=3))
        completed = manager.submit("check", lambda _ctx: "first", request_id="request_013", key="other")
        self.wait(manager, completed["task_id"])
        latest = manager.submit("check", lambda _ctx: "second", request_id="request_014", key="other")
        self.wait(manager, latest["task_id"])
        self.assertEqual([row["task_id"] for row in manager.list()], [latest["task_id"], active["task_id"]])
        self.assert_code("not_found", manager.get, completed["task_id"])
        self.assertEqual(len(self.journal()["tasks"]), 2)
        release.set()
        self.wait(manager, active["task_id"])
        manager.close()
        smaller = self.manager(history_limit=1)
        self.assertEqual([row["task_id"] for row in smaller.list()], [latest["task_id"]])
        self.assertEqual(len(self.journal()["tasks"]), 1)

    def test_full_history_of_active_tasks_rejects_instead_of_growing_unbounded(self):
        manager = self.manager(max_workers=1, history_limit=1)
        operation, entered, release = self.blocked()
        task = manager.submit("check", operation, request_id="request_015", key="first")
        self.assertTrue(entered.wait(timeout=3))
        self.assert_code("conflict", manager.submit, "check", lambda _ctx: None,
                         request_id="request_016", key="second")
        release.set()
        self.wait(manager, task["task_id"])

    def test_restart_marks_unfinished_tasks_interrupted_without_replaying_them(self):
        manager = self.manager()
        task = manager.submit("check", lambda _ctx: {"ok": True}, request_id="request_017", key="resource")
        completed = self.wait(manager, task["task_id"])
        manager.close()
        interrupted = {**completed, "state": "running", "phase": "verifying", "progress": 60,
                       "result": None, "error": None}
        queued = {**interrupted, "task_id": "a" * 32, "request_id": "request_018", "state": "queued", "phase": "queued"}
        self.write(json.dumps({"schema_version": 1, "tasks": [interrupted, queued]}).encode())
        restored = self.manager()
        self.assertEqual([row["state"] for row in restored.list()], ["interrupted", "interrupted"])
        self.assertEqual(restored.get(task["task_id"])["phase"], "verifying")
        self.assertEqual(restored.get(task["task_id"])["progress"], 60)
        self.assertEqual([row["state"] for row in self.journal()["tasks"]], ["interrupted", "interrupted"])

    def test_close_signals_cancellation_allows_rollback_and_cancels_queued_work(self):
        manager = self.manager(max_workers=1)
        entered, rolled_back = threading.Event(), threading.Event()
        queued_ran = threading.Event()

        def operation(context):
            context.update("connecting", 20)
            entered.set()
            if not context.cancelled.wait(timeout=5):
                raise RuntimeError("missing cancellation")
            context.update("rolling_back", 70)
            rolled_back.set()
            context.check_cancelled()

        running = manager.submit("wifi_connect", operation, request_id="request_019", key="network")
        self.assertTrue(entered.wait(timeout=3))
        queued = manager.submit("other", lambda _ctx: queued_ran.set(), request_id="request_020", key="other")
        manager.close(timeout=2)
        self.assertTrue(rolled_back.is_set())
        self.assertFalse(queued_ran.is_set())
        restored = self.manager()
        self.assertEqual(restored.get(running["task_id"])["state"], "cancelled")
        self.assertEqual(restored.get(running["task_id"])["phase"], "rolling_back")
        self.assertEqual(restored.get(queued["task_id"])["state"], "cancelled")
        self.assert_code("closed", manager.submit, "check", lambda _ctx: None,
                         request_id="request_021", key="network")
        self.assert_code("closed", manager.list)
        manager.close()

    def test_close_timeout_is_bounded_and_keeps_lock_until_old_operation_exits(self):
        manager = self.manager(max_workers=1)
        operation, entered, release = self.blocked()
        task = manager.submit("check", operation, request_id="request_022", key="resource")
        self.assertTrue(entered.wait(timeout=3))
        started = time.monotonic()
        manager.close(timeout=0.02)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(self.journal()["tasks"][0]["state"], "interrupted")
        self.assertEqual(self.journal()["tasks"][0]["phase"], "waiting")
        self.assert_code("tasks_unavailable", self.manager)
        release.set()
        for worker in manager._workers:
            worker.join(timeout=3)
        restored = self.manager()
        self.assertEqual(restored.get(task["task_id"])["state"], "interrupted")

    def test_close_does_not_override_an_operation_that_crossed_its_commit_boundary(self):
        manager = self.manager(max_workers=1)
        entered = threading.Event()

        def operation(context):
            context.update("scheduling", 80)
            entered.set()
            self.assertTrue(context.cancelled.wait(timeout=3))
            # A simulated external commit already succeeded. The operation
            # returns success deliberately rather than attempting a rollback.
            context.update("scheduled", 95)
            return {"scheduled": True}

        task = manager.submit("power_reboot", operation, request_id="request_029", key="power")
        self.assertTrue(entered.wait(timeout=3))
        manager.close(timeout=2)
        restored = self.manager()
        result = restored.get(task["task_id"])
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["phase"], "scheduled")
        self.assertTrue(result["result"]["scheduled"])

    def test_safe_cancelled_failure_preserves_the_rollback_result(self):
        manager = self.manager(max_workers=1)
        entered = threading.Event()

        def operation(context):
            context.update("connecting", 20)
            entered.set()
            self.assertTrue(context.cancelled.wait(timeout=3))
            context.update("rolling_back", 70)
            raise TaskFailure("cancelled", "The network change was cancelled", {"rollback": "succeeded"})

        task = manager.submit("wifi_connect", operation, request_id="request_030", key="network")
        self.assertTrue(entered.wait(timeout=3))
        manager.close(timeout=2)
        result = self.manager().get(task["task_id"])
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(result["phase"], "rolling_back")
        self.assertEqual(result["error"]["code"], "cancelled")
        self.assertEqual(result["result"], {"rollback": "succeeded"})

    def test_two_managers_cannot_own_the_same_journal(self):
        first = self.manager()
        self.assert_code("tasks_unavailable", self.manager)
        first.close()
        self.assertEqual(self.manager().list(), [])

    def test_persist_failure_prevents_operation_and_latches_manager_closed_to_new_work(self):
        manager = self.manager()
        first = manager.submit("check", lambda _ctx: True, request_id="request_023", key="resource")
        self.wait(manager, first["task_id"])
        original = (self.state / "tasks.json").read_bytes()
        ran = threading.Event()
        with patch("radxa_duck.tasks.os.replace", side_effect=OSError(SECRET)):
            self.assert_code("tasks_unavailable", manager.submit, "check", lambda _ctx: ran.set(),
                             request_id="request_024", key="resource")
        self.assertFalse(ran.is_set())
        self.assertEqual((self.state / "tasks.json").read_bytes(), original)
        self.assertEqual(list(self.state.glob(".tasks-*")), [])
        self.assert_code("tasks_unavailable", manager.submit, "check", lambda _ctx: None,
                         request_id="request_024", key="resource")

    def test_queued_task_is_persisted_before_any_operation_can_observe_it(self):
        manager = self.manager()
        seen = []

        def operation(context):
            persisted = self.journal()["tasks"]
            seen.append(any(row["task_id"] == context.task_id for row in persisted))
            return True

        task = manager.submit("check", operation, request_id="request_025", key="resource")
        self.wait(manager, task["task_id"])
        self.assertEqual(seen, [True])

    def test_external_corruption_is_not_overwritten_by_an_existing_manager(self):
        manager = self.manager()
        broken = b"broken " + SECRET.encode()
        self.write(broken)
        self.assert_code("tasks_unavailable", manager.submit, "check", lambda _ctx: None,
                         request_id="request_026", key="resource")
        self.assertEqual((self.state / "tasks.json").read_bytes(), broken)

    def test_corrupt_invalid_schema_and_oversized_journals_are_fail_closed(self):
        cases = (b"not JSON " + SECRET.encode(), b'{"schema_version":2,"tasks":[]}',
                 b'{"schema_version":true,"tasks":[]}', b'{"schema_version":1,"tasks":{}}',
                 b"[" * 1100 + b"]" * 1100, b"x" * (MAX_FILE_BYTES + 1))
        for content in cases:
            with self.subTest(size=len(content)):
                target = self.write(content)
                self.assert_code("tasks_unavailable", self.manager)
                self.assertEqual(target.read_bytes(), content)

    def test_invalid_task_journal_metadata_is_fail_closed(self):
        manager = self.manager()
        task = manager.submit("check", lambda _ctx: None, request_id="request_027", key="resource")
        original = self.wait(manager, task["task_id"])
        manager.close()
        for changes in ({"task_id": "invalid"}, {"request_id": "short"}, {"kind": "bad kind"},
                        {"phase": "bad phase"}, {"progress": True}, {"progress": 101}, {"state": "unknown"},
                        {"updated_utc": SECRET}, {"result": "x" * MAX_RESULT_BYTES}, {"unexpected": SECRET}):
            self.write(json.dumps({"schema_version": 1, "tasks": [{**original, **changes}]}).encode())
            self.assert_code("tasks_unavailable", self.manager)

    def test_insecure_permissions_symlinks_hardlinks_and_nonregular_storage_are_rejected(self):
        self.state.mkdir(mode=0o700)
        self.state.chmod(0o755)
        self.assert_code("tasks_unavailable", self.manager)
        self.state.chmod(0o700)
        outside = self.root / "outside"
        outside.write_bytes(b"untouched")
        outside.chmod(0o600)
        target = self.state / "tasks.json"
        target.symlink_to(outside)
        self.assert_code("tasks_unavailable", self.manager)
        target.unlink()
        os.link(outside, target)
        self.assert_code("tasks_unavailable", self.manager)
        target.unlink()
        os.mkfifo(target, 0o600)
        self.assert_code("tasks_unavailable", self.manager)
        target.unlink()
        self.write(b"{}").chmod(0o644)
        self.assert_code("tasks_unavailable", self.manager)
        target.unlink()
        lock = self.state / "tasks.lock"
        lock.unlink(missing_ok=True)
        lock.symlink_to(outside)
        self.assert_code("tasks_unavailable", self.manager)
        self.assertEqual(outside.read_bytes(), b"untouched")

    def test_symlink_directory_is_rejected(self):
        outside = self.root / "other-state"
        outside.mkdir(mode=0o700)
        self.state.symlink_to(outside)
        self.assert_code("tasks_unavailable", self.manager)
        self.assertEqual(list(outside.iterdir()), [])

    def test_request_and_context_validation_are_bounded(self):
        manager = self.manager()
        for request in (None, "short", "x" * 65, "invalid id", "有中文的请求编号"):
            self.assert_code("invalid_request", manager.submit, "check", lambda _ctx: None,
                             request_id=request, key="resource")
        for kind in (None, "Bad", "bad kind", "x" * 65):
            self.assert_code("invalid_request", manager.submit, kind, lambda _ctx: None,
                             request_id="request_028", key="resource")
        self.assert_code("invalid_request", manager.get, "invalid")
        self.assert_code("not_found", manager.get, "0" * 32)
        for kwargs in ({"max_workers": True}, {"max_workers": 0}, {"history_limit": 0}, {"history_limit": 257}):
            self.assert_code("invalid_request", TaskManager, self.root / "unused", **kwargs)
        for timeout in (-1, True, float("inf")):
            self.assert_code("invalid_request", manager.close, timeout=timeout)

        def operation(context):
            for phase, progress in (("bad phase", None), ("ok", True), ("ok", -1), ("ok", 101)):
                self.assert_code("invalid_request", context.update, phase, progress)
            raise TaskCancelled()

        task = manager.submit("check", operation, request_id="request_028", key="resource")
        self.assertEqual(self.wait(manager, task["task_id"])["state"], "cancelled")


if __name__ == "__main__":
    unittest.main()
