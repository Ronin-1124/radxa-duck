"""Non-interactive login1 power scheduling after hardware sessions are stopped."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import datetime, timedelta, timezone
import re
import threading
import time

from .tasks import TaskCancelled, TaskFailure


BUS = "org.freedesktop.login1"
PATH = "/org/freedesktop/login1"
INTERFACE = BUS + ".Manager"
GRACE_SECONDS = 5


def _utc(microseconds: int) -> str | None:
    try:
        return (datetime(1970, 1, 1, tzinfo=timezone.utc)
                + timedelta(microseconds=microseconds)).isoformat()
    except OverflowError:
        return None


def _scheduled(value) -> dict | None:
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or not isinstance(value[0], str) or type(value[1]) is not int
            or not 0 <= value[1] < 2**64):
        raise ValueError("Invalid scheduled power status")
    kind, timestamp = value
    # login1 uses USEC_INFINITY when no shutdown is scheduled on Debian 12;
    # some implementations return zero instead. Neither means a pending task.
    if not kind and timestamp in (0, 2**64 - 1):
        return None
    if not re.fullmatch(r"[a-z][a-z-]{0,31}", kind) or timestamp == 0:
        raise ValueError("Invalid scheduled power status")
    return {"action": {"reboot": "reboot", "poweroff": "shutdown"}.get(kind),
            "type": kind, "scheduled_usec": timestamp, "scheduled_utc": _utc(timestamp)}


class _GioLogin1:
    """Lazy synchronous system bus transport with finite method timeouts."""

    def __init__(self):
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        self._gio, self._glib = Gio, GLib
        self._bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)

    def _call(self, interface, method, signature=None, arguments=()):
        variant = self._glib.Variant(signature, arguments) if signature else None
        # ALLOW_INTERACTIVE_AUTHORIZATION is intentionally absent. A web task
        # must never create an authentication prompt or start login1 itself.
        return self._bus.call_sync(BUS, PATH, interface, method, variant, None,
                                   self._gio.DBusCallFlags.NO_AUTO_START, 3000, None).unpack()

    def capabilities(self):
        return {"reboot": self._call(INTERFACE, "CanReboot")[0],
                "shutdown": self._call(INTERFACE, "CanPowerOff")[0]}

    def scheduled(self):
        result = self._call("org.freedesktop.DBus.Properties", "Get", "(ss)",
                            (INTERFACE, "ScheduledShutdown"))[0]
        return result.unpack() if hasattr(result, "unpack") else result

    def schedule(self, kind: str, timestamp: int):
        self._call(INTERFACE, "ScheduleShutdown", "(st)", (kind, timestamp))


class PowerService:
    """Coordinate capture shutdown; task success means accepted scheduling.

    backend supplies capabilities(), scheduled(), and schedule(type, usec).
    Services supply suspend(timeout=15) and resume(); suspend must finish or
    raise within that timeout. Both suspensions begin before either is awaited.
    """

    def __init__(self, services: tuple, backend=None):
        if not isinstance(services, tuple) or len(services) > 8:
            raise ValueError("Expected at most eight hardware services")
        self._services = services
        self._backend = backend
        self._backend_lock = threading.Lock()
        self._operation_lock = threading.Lock()

    def _get_backend(self):
        with self._backend_lock:
            if self._backend is None:
                self._backend = _GioLogin1()
            return self._backend

    @staticmethod
    def _capability(value):
        reason = {"yes": None, "challenge": "permission_denied", "no": "permission_denied",
                  "na": "unsupported"}.get(value, "unavailable")
        return {"available": value == "yes", "reason": reason}

    def snapshot(self) -> dict:
        scheduled = None
        try:
            backend = self._get_backend()
            scheduled = _scheduled(backend.scheduled())
            values = backend.capabilities()
            capabilities = {action: self._capability(values.get(action))
                            for action in ("reboot", "shutdown")}
            if scheduled is not None:
                capabilities = {action: {"available": False, "reason": "already_scheduled"}
                                for action in capabilities}
        except Exception:
            capabilities = {action: {"available": False, "reason": "unavailable"}
                            for action in ("reboot", "shutdown")}
        return {"schema_version": 1, "type": "power_status", "capabilities": capabilities,
                "scheduled": scheduled}

    @staticmethod
    def _resume(services: list) -> bool:
        successful = True
        for service in services:
            try:
                if service.resume() is False:
                    successful = False
            except Exception:
                successful = False
        return successful

    def _suspend(self, context, attempted: list):
        if not self._services:
            return
        failure = None
        with ThreadPoolExecutor(max_workers=len(self._services), thread_name_prefix="duck-power-stop") as pool:
            pending = set()
            for service in self._services:
                attempted.append(service)
                pending.add(pool.submit(service.suspend, timeout=15))
            while pending:
                done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                if failure is None:
                    try:
                        context.check_cancelled()
                    except Exception as exc:
                        failure = exc
                for future in done:
                    try:
                        if future.result() is False:
                            raise RuntimeError("Capture stop failed")
                    except Exception:
                        if failure is None:
                            failure = TaskFailure("stop_failed", "Active hardware sessions could not be stopped")
        if failure is not None:
            raise failure

    def execute(self, context, *, action: str) -> dict:
        if action not in ("reboot", "shutdown"):
            raise TaskFailure("invalid_request", "Power action must be reboot or shutdown")
        if not self._operation_lock.acquire(blocking=False):
            raise TaskFailure("busy", "Another power operation is running")
        attempted = []
        try:
            context.check_cancelled()
            try:
                backend = self._get_backend()
                scheduled = _scheduled(backend.scheduled())
                capability = self._capability(backend.capabilities().get(action))
            except Exception:
                raise TaskFailure("power_unavailable", "System power management is unavailable") from None
            if scheduled is not None:
                raise TaskFailure("conflict", "A system power operation is already scheduled")
            if not capability["available"]:
                raise TaskFailure(capability["reason"], "Non-interactive system power permission is required")
            context.update("stopping_capture", 20)
            self._suspend(context, attempted)
            context.check_cancelled()
            context.update("scheduling", 80)
            try:
                # Recheck immediately before mutation to avoid replacing a
                # schedule created while hardware sessions were stopping.
                if _scheduled(backend.scheduled()) is not None:
                    raise TaskFailure("conflict", "A system power operation is already scheduled")
            except TaskFailure:
                raise
            except Exception:
                raise TaskFailure("power_unavailable", "Scheduled system power status is unavailable") from None
            context.check_cancelled()
            timestamp = time.time_ns() // 1000 + GRACE_SECONDS * 1_000_000
            kind = "reboot" if action == "reboot" else "poweroff"
            try:
                backend.schedule(kind, timestamp)
            except Exception:
                raise TaskFailure("schedule_failed", "The system did not confirm power scheduling") from None
            # The system has accepted the operation. Do not turn a later task
            # cancellation into a false report that the schedule was cancelled.
            return {"schema_version": 1, "type": "power_result", "action": action,
                    "state": "scheduled", "scheduled_usec": timestamp,
                    "scheduled_utc": _utc(timestamp), "grace_seconds": GRACE_SECONDS}
        except BaseException as exc:
            if not self._resume(attempted):
                raise TaskFailure("resume_failed", "Hardware sessions could not be made available again",
                                  result={"action": action, "state": "failed", "recovery": "failed"}) from None
            if isinstance(exc, (TaskCancelled, TaskFailure)):
                raise
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise TaskFailure("power_failed", "The power operation could not be completed") from None
        finally:
            self._operation_lock.release()
