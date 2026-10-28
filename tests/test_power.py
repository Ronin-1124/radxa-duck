"""Power scheduling fixtures; no real system bus or power operation is used."""

from datetime import datetime, timezone
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from radxa_duck.power import PowerService, _GioLogin1
from radxa_duck.tasks import TaskCancelled, TaskFailure


class _Context:
    def __init__(self):
        self.cancelled = threading.Event()
        self.phases = []

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise TaskCancelled()

    def update(self, phase, progress=None):
        self.phases.append((phase, progress))


class _Service:
    def __init__(self, *, barrier=None, on_stop=None, fail_stop=False, fail_resume=False):
        self.barrier = barrier
        self.on_stop = on_stop
        self.fail_stop, self.fail_resume = fail_stop, fail_resume
        self.suspended = False
        self.resumes = 0
        self.timeouts = []

    def suspend(self, *, timeout=15):
        self.timeouts.append(timeout)
        self.suspended = True
        if self.barrier is not None:
            self.barrier.wait(timeout=2)
        if self.on_stop:
            self.on_stop()
        if self.fail_stop:
            raise RuntimeError("private hardware path")

    def resume(self):
        self.resumes += 1
        if self.fail_resume:
            raise RuntimeError("private hardware path")
        self.suspended = False


class _Backend:
    def __init__(self):
        self.values = {"reboot": "yes", "shutdown": "yes"}
        self.schedules = [("", 0)]
        self.calls = []
        self.services = ()
        self.schedule_error = None
        self.on_schedule = None

    def capabilities(self):
        return self.values

    def scheduled(self):
        if len(self.schedules) > 1:
            return self.schedules.pop(0)
        return self.schedules[0]

    def schedule(self, kind, timestamp):
        assert all(service.suspended for service in self.services)
        self.calls.append((kind, timestamp))
        if self.on_schedule:
            self.on_schedule()
        if self.schedule_error:
            raise self.schedule_error
        self.schedules = [(kind, timestamp)]


class PowerTests(unittest.TestCase):
    def setUp(self):
        self.backend = _Backend()
        self.context = _Context()
        self.services = (_Service(), _Service())
        self.backend.services = self.services
        self.power = PowerService(self.services, backend=self.backend)

    def test_read_only_capabilities_require_exact_noninteractive_yes(self):
        for value, available, reason in (("yes", True, None), ("challenge", False, "permission_denied"),
                                         ("no", False, "permission_denied"), ("na", False, "unsupported"),
                                         ("unexpected", False, "unavailable")):
            with self.subTest(value=value):
                self.backend.values["reboot"] = value
                status = self.power.snapshot()
                self.assertEqual(status["type"], "power_status")
                self.assertEqual(status["capabilities"]["reboot"], {"available": available, "reason": reason})
        self.assertEqual(self.backend.calls, [])
        self.assertTrue(all(not service.timeouts for service in self.services))

    def test_existing_system_schedule_disables_and_is_never_replaced(self):
        self.backend.schedules = [("poweroff", 1_800_000_000_000_000)]
        status = self.power.snapshot()
        self.assertEqual(status["scheduled"]["action"], "shutdown")
        self.assertEqual(status["capabilities"]["reboot"], {"available": False, "reason": "already_scheduled"})
        with self.assertRaises(TaskFailure) as raised:
            self.power.execute(self.context, action="reboot")
        self.assertEqual(raised.exception.code, "conflict")
        self.assertEqual(self.backend.calls, [])
        self.assertTrue(all(not service.timeouts for service in self.services))

    def test_login1_infinity_sentinel_is_no_schedule_and_keeps_permission_result(self):
        # Read from the real Debian 12 board: ('', uint64 18446744073709551615).
        self.backend.schedules = [("", 2**64 - 1)]
        self.backend.values = {"reboot": "challenge", "shutdown": "challenge"}
        status = self.power.snapshot()
        self.assertIsNone(status["scheduled"])
        for capability in status["capabilities"].values():
            self.assertEqual(capability, {"available": False, "reason": "permission_denied"})
        with self.assertRaises(TaskFailure) as raised:
            self.power.execute(self.context, action="reboot")
        self.assertEqual(raised.exception.code, "permission_denied")
        self.assertEqual(self.backend.calls, [])
        self.assertTrue(all(not service.timeouts for service in self.services))
        self.backend.values = {"reboot": "yes", "shutdown": "yes"}
        self.assertTrue(self.power.snapshot()["capabilities"]["reboot"]["available"])

    def test_parallel_suspension_finishes_before_realtime_schedule(self):
        barrier = threading.Barrier(2)
        self.services = (_Service(barrier=barrier), _Service(barrier=barrier))
        self.backend.services = self.services
        power = PowerService(self.services, backend=self.backend)
        with patch("radxa_duck.power.time.time_ns", return_value=1_800_000_000_123_456_000):
            result = power.execute(self.context, action="shutdown")
        self.assertEqual(self.backend.calls, [("poweroff", 1_800_000_005_123_456)])
        self.assertEqual(result["state"], "scheduled")
        self.assertEqual(result["action"], "shutdown")
        self.assertEqual(result["grace_seconds"], 5)
        self.assertEqual(datetime.fromisoformat(result["scheduled_utc"]).tzinfo, timezone.utc)
        self.assertTrue(all(service.timeouts == [15] for service in self.services))
        self.assertTrue(all(service.suspended and service.resumes == 0 for service in self.services))

    def test_failure_to_stop_one_service_resumes_both_and_never_schedules(self):
        self.services[0].fail_stop = True
        with self.assertRaises(TaskFailure) as raised:
            self.power.execute(self.context, action="reboot")
        self.assertEqual(raised.exception.code, "stop_failed")
        self.assertNotIn("private", str(raised.exception))
        self.assertEqual(self.backend.calls, [])
        self.assertTrue(all(service.resumes == 1 and not service.suspended for service in self.services))

    def test_cancellation_during_stop_drains_both_and_resumes_without_scheduling(self):
        self.services[0].on_stop = self.context.cancelled.set
        with self.assertRaises(TaskCancelled):
            self.power.execute(self.context, action="reboot")
        self.assertTrue(all(service.resumes == 1 and not service.suspended for service in self.services))
        self.assertEqual(self.backend.calls, [])

    def test_permission_denied_is_detected_before_capture_is_interrupted(self):
        self.backend.values["shutdown"] = "challenge"
        with self.assertRaises(TaskFailure) as raised:
            self.power.execute(self.context, action="shutdown")
        self.assertEqual(raised.exception.code, "permission_denied")
        self.assertEqual(self.backend.calls, [])
        self.assertTrue(all(not service.timeouts for service in self.services))

    def test_new_schedule_during_stop_is_not_overwritten_and_services_resume(self):
        self.backend.schedules = [("", 0), ("reboot", 1_800_000_000_000_000)]
        with self.assertRaises(TaskFailure) as raised:
            self.power.execute(self.context, action="shutdown")
        self.assertEqual(raised.exception.code, "conflict")
        self.assertTrue(all(service.resumes == 1 for service in self.services))
        self.assertEqual(self.backend.calls, [])

    def test_schedule_failure_resumes_all_attempted_services(self):
        self.backend.schedule_error = RuntimeError("private bus details")
        with self.assertRaises(TaskFailure) as raised:
            self.power.execute(self.context, action="reboot")
        self.assertEqual(raised.exception.code, "schedule_failed")
        self.assertNotIn("private", str(raised.exception))
        self.assertTrue(all(service.resumes == 1 and not service.suspended for service in self.services))

    def test_resume_failure_does_not_skip_restoring_other_services(self):
        self.services[0].fail_stop = True
        self.services[0].fail_resume = True
        with self.assertRaises(TaskFailure) as raised:
            self.power.execute(self.context, action="reboot")
        self.assertEqual(raised.exception.code, "resume_failed")
        self.assertEqual(raised.exception.result["recovery"], "failed")
        self.assertTrue(all(service.resumes == 1 for service in self.services))
        self.assertFalse(self.services[1].suspended)

    def test_cancellation_after_system_acceptance_remains_scheduled(self):
        self.backend.on_schedule = self.context.cancelled.set
        result = self.power.execute(self.context, action="reboot")
        self.assertEqual(result["state"], "scheduled")
        self.assertTrue(all(service.suspended and service.resumes == 0 for service in self.services))

    def test_invalid_or_unavailable_bus_state_is_safe_and_read_only(self):
        for value in (("reboot", -1), ("", 99), ("reboot", True), ("private\nvalue", 100), None):
            with self.subTest(value=value):
                self.backend.schedules = [value]
                result = self.power.snapshot()
                self.assertIsNone(result["scheduled"])
                self.assertFalse(result["capabilities"]["reboot"]["available"])
                with self.assertRaises(TaskFailure) as raised:
                    self.power.execute(self.context, action="reboot")
                self.assertEqual(raised.exception.code, "power_unavailable")
        self.assertTrue(all(not service.timeouts for service in self.services))
        with patch("radxa_duck.power._GioLogin1", side_effect=ImportError("private import details")):
            result = PowerService(()).snapshot()
        self.assertFalse(result["capabilities"]["shutdown"]["available"])
        self.assertNotIn("private", str(result))

    def test_invalid_action_and_competing_operation_are_rejected(self):
        with self.assertRaises(TaskFailure) as raised:
            self.power.execute(self.context, action="halt")
        self.assertEqual(raised.exception.code, "invalid_request")
        self.power._operation_lock.acquire()
        try:
            with self.assertRaises(TaskFailure) as raised:
                self.power.execute(self.context, action="reboot")
        finally:
            self.power._operation_lock.release()
        self.assertEqual(raised.exception.code, "busy")

    def test_gio_methods_use_fixed_signatures_timeouts_and_noninteractive_flags(self):
        calls = []

        class Variant:
            def __init__(self, signature, value):
                self.signature, self.value = signature, value

            def unpack(self):
                return self.value

        def call(*args):
            calls.append(args)
            return Variant(None, (("", 0),) if args[3] == "Get" else ("yes",))

        gio = SimpleNamespace(BusType=SimpleNamespace(SYSTEM=1),
                              DBusCallFlags=SimpleNamespace(NO_AUTO_START=32),
                              bus_get_sync=lambda *_args: SimpleNamespace(call_sync=call))
        gi = SimpleNamespace(require_version=lambda *_args: None)
        with patch.dict(sys.modules, {"gi": gi, "gi.repository": SimpleNamespace(Gio=gio, GLib=SimpleNamespace(Variant=Variant))}):
            backend = _GioLogin1()
            self.assertEqual(backend.capabilities(), {"reboot": "yes", "shutdown": "yes"})
            self.assertEqual(backend.scheduled(), ("", 0))
            backend.schedule("poweroff", 1_800_000_000_000_000)
        self.assertEqual([args[3] for args in calls], ["CanReboot", "CanPowerOff", "Get", "ScheduleShutdown"])
        self.assertTrue(all(args[6] == 32 and args[7] == 3000 for args in calls))
        self.assertEqual(calls[-1][4].signature, "(st)")
        self.assertEqual(calls[-1][4].value, ("poweroff", 1_800_000_000_000_000))


if __name__ == "__main__":
    unittest.main()
