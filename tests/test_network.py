"""Fake system-bus transactions; these tests never scan or change real Wi-Fi."""

import json
import unittest
from unittest.mock import patch

from radxa_duck.network import BUS, NetworkError, NetworkService, _GioNetworkManager


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


class Cancelled:
    def __init__(self, clock):
        self.clock = clock
        self.value = False

    def is_set(self):
        return self.value

    def set(self):
        self.value = True

    def wait(self, seconds):
        self.clock.now += seconds
        return self.value


class Context:
    def __init__(self, clock, hook=None):
        self.cancelled = Cancelled(clock)
        self.phases = []
        self.hook = hook

    def check_cancelled(self):
        if self.cancelled.is_set():
            raise RuntimeError("cancelled")

    def update(self, phase, progress=None):
        self.phases.append((phase, progress))
        if self.hook:
            self.hook(phase)


def point(ssid=b"Duck WiFi", *, strength=80, privacy=1, wpa=0, rsn=0x100, path="/ap/1"):
    return {"Ssid": ssid, "Strength": strength, "Flags": privacy,
            "WpaFlags": wpa, "RsnFlags": rsn, "path": path}


class Backend:
    def __init__(self):
        self.events = []
        self.perms = {BUS + "." + name: "yes" for name in (
            "wifi.scan", "network-control", "checkpoint-rollback", "settings.modify.system")}
        self.points = [point()]
        self.scanned = False
        self.scan_completes = True
        self.status = {"state": 2, "current": True, "ipv4": ["192.168.1.101"]}
        self.rollback_result = {"/devices/wlan0": 0}
        self.fail = None
        self.information = {
            "device": "/devices/wlan0", "interface": "wlan0", "state_code": 100,
            "ipv4": ["192.168.1.100"], "ssid_raw": b"Original WiFi", "strength": 72,
            "security": "wpa-psk", "last_scan": 10, "radio_enabled": True,
            "active_connection": "/active/original", "version": "1.42.4"}

    def event(self, name):
        self.events.append(name)
        if self.fail == name:
            raise RuntimeError("backend secret password-or-SSID must not escape")

    def info(self):
        self.event("info")
        return self.information

    def permissions(self):
        return self.perms

    def last_scan(self, _device):
        return 11 if self.scanned and self.scan_completes else 10

    def request_scan(self, _device):
        self.event("scan")
        self.scanned = True

    def access_points(self, _device):
        self.event("points")
        return self.points

    def checkpoint(self, _device):
        self.event("checkpoint")
        return "/checkpoints/1"

    def activate(self, _device, _ap, **kwargs):
        self.event("activate")
        self.activation = kwargs
        return "/profiles/new", "/active/new"

    def connection(self, _device, _active):
        self.event("connection")
        return self.status

    def save(self, _profile, **kwargs):
        self.event("save")
        self.saved = kwargs

    def destroy(self, _checkpoint):
        self.event("destroy")

    def rollback(self, _checkpoint):
        self.event("rollback")
        return self.rollback_result


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.backend = Backend()
        self.service = NetworkService(backend=self.backend, clock=self.clock,
                                      scan_timeout=1, activation_timeout=1, confirmation_timeout=1)
        self.context = Context(self.clock)
        self.addCleanup(self.service.close)

    def connect(self, **kwargs):
        return self.service.connect(self.context, task_id="task-1", ssid="Duck WiFi",
                                    password="test-strong-password", **kwargs)

    def confirm(self, phase):
        if phase == "awaiting_confirmation":
            self.service.confirm("task-1")

    def error(self, code, function):
        with self.assertRaises(NetworkError) as caught:
            function()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn("secret", str(caught.exception))
        self.assertNotIn("password-or-SSID", str(caught.exception))
        return caught.exception

    def test_snapshot_is_read_only_and_reports_real_permission_requirements(self):
        result = self.service.snapshot()
        self.assertTrue(result["available"])
        self.assertEqual(result["ssid"], "Original WiFi")
        self.assertEqual(result["state"], "connected")
        self.assertEqual(result["ipv4"], ["192.168.1.100"])
        self.assertTrue(result["capabilities"]["connect"]["available"])
        self.assertEqual(self.backend.events, ["info"])
        self.backend.perms[BUS + ".checkpoint-rollback"] = "auth"
        result = self.service.snapshot()
        self.assertFalse(result["capabilities"]["connect"]["available"])
        self.assertEqual(result["capabilities"]["connect"]["reason"], "permission_denied")
        self.assertTrue(result["capabilities"]["scan"]["available"])

    def test_snapshot_failure_is_redacted_and_other_hosts_need_no_gi(self):
        self.backend.fail = "info"
        result = self.service.snapshot()
        self.assertFalse(result["available"])
        self.assertNotIn("secret", json.dumps(result))
        with patch("radxa_duck.network._GioNetworkManager", side_effect=ImportError("private")):
            service = NetworkService()
            self.addCleanup(service.close)
            self.assertFalse(service.snapshot()["available"])

    def test_disabled_radio_rejects_writes_before_scan_or_checkpoint(self):
        self.backend.information["radio_enabled"] = False
        self.error("radio_disabled", self.connect)
        self.error("radio_disabled", lambda: self.service.scan(self.context))
        self.assertNotIn("checkpoint", self.backend.events)
        self.assertNotIn("scan", self.backend.events)

    def test_scan_safe_ssid_decode_deduplicate_and_security(self):
        self.backend.points = [point(strength=10), point(), point(b"open", privacy=0, rsn=0),
                               point(b"enterprise", rsn=0x200), point(b"wep", rsn=0),
                               point(b"sae", rsn=0x400), point(b"owe", rsn=0x800),
                               point(b"mal\x00formed"), point(b"invalid\xff"), point(b"")]
        result = self.service.scan(self.context)
        by_name = {item["ssid"]: item for item in result["networks"]}
        self.assertEqual(by_name["Duck WiFi"]["strength"], 80)
        self.assertTrue(by_name["open"]["connectable"])
        self.assertEqual(by_name["open"]["security"], "open")
        for name in ("enterprise", "wep", "sae", "owe"):
            self.assertEqual(by_name[name]["security"], "unsupported")
            self.assertFalse(by_name[name]["connectable"])
        self.assertFalse(by_name["malformed"]["connectable"])
        self.assertFalse(by_name["invalid�"]["connectable"])
        self.assertNotIn("", by_name)
        self.assertNotIn("path", json.dumps(result))
        self.assertNotIn("checkpoint", self.backend.events)
        self.assertFalse(result["truncated"])

    def test_dense_unicode_and_malformed_scans_fit_persistent_task_budget(self):
        for prefix in ("😀".encode() * 7, b"\xff" * 29):
            with self.subTest(prefix=prefix):
                self.backend.points = [point(prefix + f"{index:03d}".encode(), strength=index % 100)
                                       for index in range(128)]
                result = self.service.scan(self.context)
                encoded = json.dumps(result, ensure_ascii=True, separators=(",", ":"))
                self.assertLessEqual(len(encoded), 12 * 1024)
                self.assertTrue(result["truncated"])
                all_networks = NetworkService._networks(self.backend.points)
                self.assertEqual(result["networks"], all_networks[:len(result["networks"])])
                self.assertGreater(len(result["networks"]), 0)
                self.assertLess(len(result["networks"]), len(all_networks))
                self.backend.scanned = False

    def test_scan_timeout_and_bus_failures_are_bounded_and_redacted(self):
        self.backend.scan_completes = False
        self.error("scan_timeout", lambda: self.service.scan(self.context))
        self.assertLessEqual(self.clock.now, 101.25)
        self.backend.fail = "scan"
        self.error("scan_failed", lambda: self.service.scan(self.context))

    def test_only_confirmed_connection_is_saved_and_checkpoint_destroyed(self):
        self.context.hook = self.confirm
        result = self.connect()
        self.assertTrue(result["confirmed"])
        self.assertTrue(result["credentials_saved"])
        self.assertEqual(self.backend.saved, {"password": "test-strong-password", "security": "wpa-psk"})
        self.assertEqual(result["ipv4"], ["192.168.1.101"])
        self.assertNotIn("password", json.dumps(result))
        events = self.backend.events
        self.assertLess(events.index("checkpoint"), events.index("activate"))
        self.assertLess(events.index("activate"), events.index("save"))
        self.assertLess(events.index("save"), events.index("destroy"))
        self.assertNotIn("rollback", events)
        self.assertIsNone(self.service.snapshot()["pending_task"])
        self.error("confirmation_unavailable", lambda: self.service.confirm("task-1"))

    def test_confirmation_task_id_and_phase_are_enforced(self):
        self.error("confirmation_unavailable", lambda: self.service.confirm("task-1"))
        def hook(phase):
            if phase == "connecting":
                self.error("confirmation_unavailable", lambda: self.service.confirm("task-1"))
            if phase == "awaiting_confirmation":
                self.error("confirmation_unavailable", lambda: self.service.confirm("stale-task"))
                pending = self.service.snapshot()["pending_task"]
                self.assertEqual(pending["task_id"], "task-1")
                self.assertTrue(pending["awaiting_confirmation"])
                self.assertEqual(self.service.confirm("task-1"), {"task_id": "task-1", "confirmed": True})
        self.context.hook = hook
        self.connect()

    def test_no_confirmation_rolls_back_without_persisting(self):
        error = self.error("confirmation_timeout", self.connect)
        self.assertEqual(error.rollback, "succeeded")
        self.assertEqual(self.context.phases[-1], ("rolled_back", 100))
        self.assertNotIn("save", self.backend.events)
        self.assertNotIn("destroy", self.backend.events)
        self.assertIsNone(self.service.snapshot()["pending_task"])

    def test_no_ipv4_does_not_request_confirmation(self):
        self.backend.status["ipv4"] = []
        self.error("activation_timeout", self.connect)
        self.assertNotIn("awaiting_confirmation", [phase for phase, _ in self.context.phases])
        self.assertIn("rollback", self.backend.events)

    def test_failed_activation_rolls_back_and_never_leaks_backend_text(self):
        self.backend.fail = "activate"
        error = self.error("activation_failed", self.connect)
        self.assertEqual(error.rollback, "succeeded")
        self.assertIn("rollback", self.backend.events)

    def test_checkpoint_failure_does_not_touch_connection(self):
        self.backend.fail = "checkpoint"
        error = self.error("checkpoint_failed", self.connect)
        self.assertIsNone(error.rollback)
        self.assertNotIn("activate", self.backend.events)

    def test_connection_lost_before_confirmation_is_not_success(self):
        def hook(phase):
            if phase == "awaiting_confirmation":
                self.service.confirm("task-1")
                self.backend.status["current"] = False
        self.context.hook = hook
        self.error("connection_lost", self.connect)
        self.assertIn("rollback", self.backend.events)
        self.assertNotIn("save", self.backend.events)

    def test_cancellation_and_service_close_roll_back(self):
        for close in (False, True):
            with self.subTest(close=close):
                self.setUp()
                def hook(phase):
                    if phase == "awaiting_confirmation":
                        if close:
                            self.service.close()
                        else:
                            self.context.cancelled.set()
                self.context.hook = hook
                error = self.error("cancelled", self.connect)
                self.assertEqual(error.rollback, "succeeded")
                self.assertNotIn("save", self.backend.events)

    def test_failed_save_or_destroy_rolls_back(self):
        for operation in ("save", "destroy"):
            with self.subTest(operation=operation):
                self.setUp()
                self.context.hook = self.confirm
                self.backend.fail = operation
                error = self.error("commit_failed", self.connect)
                self.assertEqual(error.rollback, "succeeded")

    def test_disconnect_during_save_is_not_success(self):
        original = self.backend.save
        def save(profile, **kwargs):
            original(profile, **kwargs)
            self.backend.status["state"] = 4
        self.backend.save = save
        self.context.hook = self.confirm
        self.error("connection_lost", self.connect)
        self.assertNotIn("destroy", self.backend.events)

    def test_rollback_failures_are_explicit(self):
        self.backend.rollback_result = {"/devices/wlan0": 3}
        error = self.error("confirmation_timeout", self.connect)
        self.assertEqual(error.rollback, "failed")
        self.assertEqual(self.context.phases[-1], ("rollback_failed", 100))

    def test_failed_task_journal_never_prevents_checkpoint_rollback(self):
        def fault(phase):
            if phase in ("awaiting_confirmation", "rolling_back", "rolled_back", "rollback_failed"):
                raise RuntimeError("private storage fault")
        self.context.hook = fault
        error = self.error("activation_failed", self.connect)
        self.assertEqual(self.backend.events.count("rollback"), 1)
        self.assertEqual(error.rollback, "succeeded")
        self.assertNotIn("save", self.backend.events)

    def test_closed_task_context_cannot_mask_rollback_outcome(self):
        for failed_rollback in (False, True):
            with self.subTest(failed_rollback=failed_rollback):
                self.setUp()
                if failed_rollback:
                    self.backend.fail = "rollback"
                def fault(phase):
                    if phase in ("rolling_back", "rolled_back", "rollback_failed"):
                        raise RuntimeError("task context is closed")
                self.context.hook = fault
                error = self.error("confirmation_timeout", self.connect)
                self.assertEqual(self.backend.events.count("rollback"), 1)
                self.assertEqual(error.rollback, "failed" if failed_rollback else "succeeded")

    def test_busy_does_not_start_a_second_operation(self):
        self.service._operation.acquire()
        try:
            self.error("busy", self.connect)
            self.error("busy", lambda: self.service.scan(self.context))
            self.assertEqual(self.backend.events, [])
        finally:
            self.service._operation.release()

    def test_unknown_network_or_wrong_security_does_not_change_network(self):
        self.backend.points = [point(rsn=0x200)]
        self.error("network_not_found", self.connect)
        self.assertNotIn("checkpoint", self.backend.events)

    def test_invalid_inputs_never_reach_system_bus(self):
        for kwargs in ({"ssid": ""}, {"ssid": "x" * 33}, {"ssid": "n\net"}, {"ssid": "\ud800"},
                       {"password": "short"}, {"password": "g" * 64}, {"password": "hello\x00world"},
                       {"security": "enterprise"}, {"security": "open", "password": "not-empty"}):
            with self.subTest(kwargs=kwargs):
                values = {"ssid": "Duck WiFi", "password": "test-strong-password", **kwargs}
                with self.assertRaises(NetworkError):
                    self.service.connect(self.context, task_id="task-1", **values)
        self.assertEqual(self.backend.events, [])

    def test_valid_psk_and_open_inputs(self):
        NetworkService.validate_connection(ssid="鸭子", password="a" * 63)
        NetworkService.validate_connection(ssid="network", password="F" * 64)
        NetworkService.validate_connection(ssid="open", security="open")
        self.backend.points = [point(privacy=0, rsn=0)]
        self.context.hook = self.confirm
        result = self.service.connect(self.context, task_id="task-1", ssid="Duck WiFi", security="open")
        self.assertTrue(result["credentials_saved"])

    def test_gio_transport_never_uses_disk_persistence_for_candidate_password(self):
        transport = object.__new__(_GioNetworkManager)
        class GLib:
            @staticmethod
            def Variant(signature, value):
                return (signature, value)
        transport.GLib = GLib
        calls = []
        def call(*args):
            calls.append(args)
            return ("/profile/new", "/active/new", {})
        transport.call = call
        transport.activate("/device/wlan0", "/ap/1", ssid="鸭子", password="private-password", security="wpa-psk")
        path, interface, method, signature, arguments = calls[0]
        self.assertEqual((path, interface, method), ("/org/freedesktop/NetworkManager", BUS, "AddAndActivateConnection2"))
        self.assertEqual(signature, "(a{sa{sv}}ooa{sv})")
        settings, _device, _ap, options = arguments
        self.assertEqual(options["persist"], ("s", "memory"))
        self.assertEqual(settings["802-11-wireless-security"]["psk-flags"], ("u", 2))
        self.assertEqual(settings["802-11-wireless"]["ssid"], ("ay", "鸭子".encode()))
        calls.clear()
        transport.checkpoint("/device/wlan0")
        self.assertEqual(calls[0][3:], ("(aouu)", (["/device/wlan0"], 120, 2)))

    def test_confirmed_profile_preserves_dbus_types_and_saves_credentials(self):
        try:
            from gi.repository import GLib
        except ImportError:
            self.skipTest("System PyGObject is unavailable in this interpreter")
        transport = object.__new__(_GioNetworkManager)
        transport.GLib = GLib
        v = GLib.Variant
        existing = {"connection": {"id": v("s", "example"), "uuid": v("s", "uuid"),
                                   "type": v("s", "802-11-wireless"), "autoconnect": v("b", True)},
                    "802-11-wireless": {"ssid": v("ay", b"Duck WiFi")},
                    "802-11-wireless-security": {"key-mgmt": v("s", "wpa-psk"), "psk-flags": v("u", 2)},
                    "ipv4": {"method": v("s", "auto")}}
        transport.call_variant = lambda *_args: v("(a{sa{sv}})", (existing,))
        written = []
        def write(_profile, _interface, method, signature, arguments):
            self.assertEqual(method, "Update2")
            # Constructing the real GLib Variant checks preserved nested types,
            # not just the Python dictionary shape used by the service fake.
            written.append(v(signature, arguments).unpack())
        transport.call = write
        transport.save("/profile/new", password="private-password", security="wpa-psk")
        settings, flags, options = written[0]
        self.assertEqual(flags, 1)
        self.assertEqual(options, {})
        self.assertEqual(settings["802-11-wireless-security"]["psk"], "private-password")
        self.assertEqual(settings["802-11-wireless-security"]["psk-flags"], 0)
        self.assertEqual(settings["802-11-wireless"]["ssid"], list(b"Duck WiFi"))


if __name__ == "__main__":
    unittest.main()
