"""NetworkManager Wi-Fi transactions protected by an expiring checkpoint.

Passwords cross the system D-Bus only; they never enter task results or errors.
The Gio dependency is loaded on first use so unsupported hosts can still run
the other services. All mutating operations are serialized in this process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import json
import re
import threading
import time
import unicodedata
import uuid


BUS = "org.freedesktop.NetworkManager"
ROOT = "/org/freedesktop/NetworkManager"
DEVICE = BUS + ".Device"
WIRELESS = DEVICE + ".Wireless"
AP = BUS + ".AccessPoint"
ACTIVE = BUS + ".Connection.Active"
SETTINGS = BUS + ".Settings.Connection"
_SCAN_RESULT_BYTES = 12 * 1024
_MESSAGES = {
    "unavailable": "NetworkManager Wi-Fi is unavailable",
    "permission_denied": "Wi-Fi control requires non-interactive system permission",
    "radio_disabled": "The Wi-Fi radio is disabled",
    "busy": "Another Wi-Fi operation is running",
    "invalid_request": "Invalid Wi-Fi connection settings",
    "unsupported_security": "Only open and WPA personal Wi-Fi networks are supported",
    "network_not_found": "The requested Wi-Fi network is not currently visible",
    "scan_failed": "The Wi-Fi scan could not be started",
    "scan_timeout": "The Wi-Fi scan did not complete in time",
    "checkpoint_failed": "A safe network checkpoint could not be created",
    "activation_failed": "The Wi-Fi connection could not be activated",
    "activation_timeout": "The Wi-Fi connection did not acquire an IPv4 address in time",
    "connection_lost": "The new Wi-Fi connection was lost before confirmation",
    "confirmation_timeout": "The new Wi-Fi connection was not confirmed in time",
    "confirmation_unavailable": "This Wi-Fi task is not awaiting confirmation",
    "commit_failed": "The confirmed Wi-Fi settings could not be saved",
    "cancelled": "The Wi-Fi operation was cancelled",
    "rollback_failed": "The previous network could not be restored; the system checkpoint remains in effect",
}


class NetworkError(RuntimeError):
    def __init__(self, code: str, *, rollback: str | None = None):
        super().__init__(_MESSAGES.get(code, _MESSAGES["unavailable"]))
        self.code = code
        self.rollback = rollback


def _display(value: str) -> str:
    return "".join(char for char in value if not unicodedata.category(char).startswith("C"))


def _ssid(raw) -> tuple[str, bool]:
    try:
        data = bytes(raw)
        decoded = data.decode("utf-8", errors="strict")
        display = _display(decoded)
        return display, bool(data) and len(data) <= 32 and display == decoded
    except (TypeError, ValueError, UnicodeError):
        try:
            return _display(bytes(raw).decode("utf-8", errors="replace")), False
        except (TypeError, ValueError):
            return "", False


def _security(ap: dict) -> str:
    flags = int(ap.get("WpaFlags", 0)) | int(ap.get("RsnFlags", 0))
    # PSK can coexist with SAE (WPA2/WPA3 transition mode). Enterprise-only,
    # SAE-only, OWE and WEP are intentionally not treated as WPA personal.
    if flags & 0x100:
        return "wpa-psk"
    if not flags and not int(ap.get("Flags", 0)) & 1:
        return "open"
    return "unsupported"


def _addresses(properties: dict) -> list[str]:
    values = []
    for item in properties.get("AddressData", [])[:32]:
        try:
            address = ipaddress.IPv4Address(item.get("address", ""))
            if not address.is_unspecified and not address.is_loopback:
                values.append(str(address))
        except (AttributeError, ipaddress.AddressValueError):
            pass
    return values


class _GioNetworkManager:
    """Small synchronous transport; each call has a finite bus timeout."""

    def __init__(self):
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
            self.Gio, self.GLib = Gio, GLib
            self.bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except Exception:
            raise NetworkError("unavailable") from None

    def call(self, path, interface, method, signature=None, arguments=()):
        return self.call_variant(path, interface, method, signature, arguments).unpack()

    def call_variant(self, path, interface, method, signature=None, arguments=()):
        variant = self.GLib.Variant(signature, arguments) if signature else None
        return self.bus.call_sync(BUS, path, interface, method, variant, None,
                                  self.Gio.DBusCallFlags.NO_AUTO_START, 3000, None)

    def properties(self, path, interface):
        return self.call(path, "org.freedesktop.DBus.Properties", "GetAll", "(s)", (interface,))[0]

    def info(self):
        root = self.properties(ROOT, BUS)
        paths = self.call(ROOT, BUS, "GetDevices")[0]
        candidates = []
        for path in paths[:32]:
            props = self.properties(path, DEVICE)
            if props.get("DeviceType") == 2:
                candidates.append((path, props))
        if not candidates:
            raise NetworkError("unavailable")
        # Keep the active Wi-Fi interface when a machine has several adapters.
        path, device = max(candidates, key=lambda value: value[1].get("State") == 100)
        wireless = self.properties(path, WIRELESS)
        active_ap = wireless.get("ActiveAccessPoint", "/")
        ap = self.properties(active_ap, AP) if active_ap != "/" else {}
        ip4 = device.get("Ip4Config", "/")
        addresses = _addresses(self.properties(ip4, BUS + ".IP4Config")) if ip4 != "/" else []
        return {"device": path, "interface": device.get("Interface"),
                "state_code": int(device.get("State", 0)), "ipv4": addresses,
                "ssid_raw": ap.get("Ssid", []), "strength": ap.get("Strength"),
                "security": _security(ap), "last_scan": wireless.get("LastScan", -1),
                "radio_enabled": bool(root.get("WirelessEnabled") and root.get("WirelessHardwareEnabled")),
                "active_connection": device.get("ActiveConnection", "/"),
                "version": root.get("Version")}

    def permissions(self):
        return self.call(ROOT, BUS, "GetPermissions")[0]

    def request_scan(self, device):
        self.call(device, WIRELESS, "RequestScan", "(a{sv})", ({},))

    def last_scan(self, device):
        return self.properties(device, WIRELESS).get("LastScan", -1)

    def access_points(self, device):
        paths = self.call(device, WIRELESS, "GetAllAccessPoints")[0]
        deadline = time.monotonic() + 5
        result = []
        for path in paths[:128]:
            if time.monotonic() >= deadline:
                break
            try:
                result.append({**self.properties(path, AP), "path": path})
            except Exception:
                # APs can disappear between listing and reading their properties.
                continue
        return result

    def checkpoint(self, device):
        return self.call(ROOT, BUS, "CheckpointCreate", "(aouu)", ([device], 120, 2))[0]

    def activate(self, device, ap_path, *, ssid, password, security):
        v = self.GLib.Variant
        settings = {
            "connection": {"id": v("s", "radxa-duck-" + str(uuid.uuid4())),
                           "uuid": v("s", str(uuid.uuid4())), "type": v("s", "802-11-wireless"),
                           "autoconnect": v("b", True)},
            "802-11-wireless": {"ssid": v("ay", ssid.encode("utf-8")),
                                "mode": v("s", "infrastructure")},
            "ipv4": {"method": v("s", "auto")},
            "ipv6": {"method": v("s", "auto")},
        }
        if security == "wpa-psk":
            settings["802-11-wireless-security"] = {
                "key-mgmt": v("s", "wpa-psk"), "psk": v("s", password),
                "psk-flags": v("u", 2),  # NOT_SAVED: never persist a candidate password.
            }
        result = self.call(ROOT, BUS, "AddAndActivateConnection2", "(a{sa{sv}}ooa{sv})",
                           (settings, device, ap_path, {"persist": v("s", "memory")}))
        return result[0], result[1]

    def connection(self, device, active):
        props = self.properties(active, ACTIVE)
        current = self.properties(device, DEVICE)
        ip4 = props.get("Ip4Config", "/")
        addresses = _addresses(self.properties(ip4, BUS + ".IP4Config")) if ip4 != "/" else []
        return {"state": props.get("State", 0), "ipv4": addresses,
                "current": current.get("ActiveConnection") == active}

    def save(self, profile, *, password, security):
        if security == "open":
            self.call(profile, SETTINGS, "Save")
            return
        # Preserve D-Bus value types when getting the completed profile. The
        # normal unpack() recursively loses uint/byte/array type information.
        # GetSettings deliberately excludes secrets; no GetSecrets is used.
        groups = self.call_variant(profile, SETTINGS, "GetSettings").get_child_value(0)
        settings = {}
        for index in range(groups.n_children()):
            entry = groups.get_child_value(index)
            group_name = entry.get_child_value(0).get_string()
            group = entry.get_child_value(1)
            values = {}
            for property_index in range(group.n_children()):
                property_entry = group.get_child_value(property_index)
                values[property_entry.get_child_value(0).get_string()] = property_entry.get_child_value(1).get_variant()
            settings[group_name] = values
        security_settings = settings.get("802-11-wireless-security")
        if security_settings is None or not password:
            raise NetworkError("commit_failed")
        security_settings["psk"] = self.GLib.Variant("s", password)
        security_settings["psk-flags"] = self.GLib.Variant("u", 0)
        try:
            # TO_DISK=1 lets NM retain the explicitly confirmed credentials in
            # its own restricted system profile for subsequent reconnections.
            self.call(profile, SETTINGS, "Update2", "(a{sa{sv}}ua{sv})", (settings, 1, {}))
        finally:
            security_settings.pop("psk", None)

    def destroy(self, checkpoint):
        self.call(ROOT, BUS, "CheckpointDestroy", "(o)", (checkpoint,))

    def rollback(self, checkpoint):
        return self.call(ROOT, BUS, "CheckpointRollback", "(o)", (checkpoint,))[0]


@dataclass
class _Pending:
    task_id: str
    deadline: float
    confirmation: threading.Event = field(default_factory=threading.Event)
    awaiting: bool = False


class NetworkService:
    def __init__(self, *, backend=None, clock=time.monotonic,
                 scan_timeout=12.0, activation_timeout=35.0, confirmation_timeout=45.0,
                 poll_interval=0.25):
        if (not 0 < scan_timeout <= 30 or not 0 < activation_timeout <= 40
                or not 0 < confirmation_timeout <= 50 or not 0 < poll_interval <= 1):
            raise ValueError("Invalid network timeouts")
        self._backend = backend
        self._backend_lock = threading.Lock()
        self._operation = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending: _Pending | None = None
        self._clock = clock
        self._scan_timeout = scan_timeout
        self._activation_timeout = activation_timeout
        self._confirmation_timeout = confirmation_timeout
        self._poll_interval = poll_interval
        self._closed = threading.Event()

    def _transport(self):
        if self._closed.is_set():
            raise NetworkError("unavailable")
        with self._backend_lock:
            if self._backend is None:
                self._backend = _GioNetworkManager()
            return self._backend

    @staticmethod
    def _capabilities(info, permissions):
        def available(names):
            if not info["radio_enabled"]:
                return {"available": False, "reason": "radio_disabled"}
            allowed = all(permissions.get(BUS + "." + name) == "yes" for name in names)
            return {"available": allowed, "reason": None if allowed else "permission_denied"}
        return {"scan": available(("wifi.scan",)),
                "connect": available(("network-control", "checkpoint-rollback", "settings.modify.system"))}

    def snapshot(self):
        try:
            backend = self._transport()
            info = backend.info()
            capabilities = self._capabilities(info, backend.permissions())
            name, _ = _ssid(info["ssid_raw"])
            code = info["state_code"]
            state = ("connected" if code == 100 else "connecting" if 40 <= code <= 90
                     else "disconnecting" if code == 110
                     else "disconnected" if code == 30 else "unavailable" if code <= 20 else "failed")
            result = {"schema_version": 1, "type": "network_status", "available": True,
                      "backend": "NetworkManager", "backend_version": info.get("version"),
                      "interface": info["interface"], "state": state, "state_code": code,
                      "ssid": name or None, "ipv4": list(info["ipv4"]),
                      "strength": info.get("strength"), "security": info.get("security"),
                      "radio_enabled": info["radio_enabled"], "capabilities": capabilities}
        except Exception:
            result = {"schema_version": 1, "type": "network_status", "available": False,
                      "backend": "NetworkManager", "reason": "unavailable",
                      "interface": None, "state": "unavailable", "ssid": None, "ipv4": [],
                      "strength": None, "capabilities": {
                          key: {"available": False, "reason": "unavailable"} for key in ("scan", "connect")}}
        with self._pending_lock:
            pending = self._pending
            result["pending_task"] = None if pending is None else {
                "task_id": pending.task_id, "awaiting_confirmation": pending.awaiting,
                "confirmation_remaining_seconds": max(0, round(pending.deadline - self._clock()))
                if pending.awaiting else None}
        return result

    def _prepare(self, operation):
        try:
            backend = self._transport()
            info = backend.info()
            capability = self._capabilities(info, backend.permissions())[operation]
            if not capability["available"]:
                raise NetworkError(capability["reason"])
            return backend, info
        except NetworkError:
            raise
        except Exception:
            raise NetworkError("unavailable") from None

    def _check(self, context):
        if self._closed.is_set() or context.cancelled.is_set():
            raise NetworkError("cancelled")
        context.check_cancelled()

    def _pause(self, context):
        context.cancelled.wait(self._poll_interval)
        self._check(context)

    @staticmethod
    def _cleanup_phase(context, phase):
        # A cancelled/closed task manager or a failed task journal must never
        # prevent the system checkpoint from restoring network connectivity.
        try:
            context.update(phase, 100 if phase != "rolling_back" else 90)
        except Exception:
            pass

    @staticmethod
    def _networks(points):
        networks = {}
        for point in points[:128]:
            name, valid = _ssid(point.get("Ssid", []))
            if not name:
                continue
            security = _security(point)
            item = {"ssid": name, "strength": max(0, min(100, int(point.get("Strength", 0)))),
                    "security": security, "connectable": valid and security != "unsupported"}
            key = (name, security)
            if key not in networks or item["strength"] > networks[key]["strength"]:
                networks[key] = item
        return sorted(networks.values(), key=lambda item: (-item["strength"], item["ssid"]))

    @classmethod
    def _scan_result(cls, interface, points):
        # Task journals escape non-ASCII characters. Budget that representation
        # so dense scans and malformed SSIDs cannot overflow their result cap.
        result = {"interface": interface, "networks": [], "truncated": False}
        options = {"ensure_ascii": True, "separators": (",", ":")}
        used = len(json.dumps(result, **options))
        for item in cls._networks(points):
            length = len(json.dumps(item, **options)) + bool(result["networks"])
            if used + length > _SCAN_RESULT_BYTES:
                result["truncated"] = True
                break
            result["networks"].append(item)
            used += length
        return result

    def scan(self, context):
        if not self._operation.acquire(blocking=False):
            raise NetworkError("busy")
        try:
            self._check(context)
            backend, info = self._prepare("scan")
            context.update("scanning", 10)
            old_scan = backend.last_scan(info["device"])
            try:
                backend.request_scan(info["device"])
            except Exception:
                raise NetworkError("scan_failed") from None
            deadline = self._clock() + self._scan_timeout
            while self._clock() < deadline:
                self._check(context)
                if backend.last_scan(info["device"]) > old_scan:
                    return self._scan_result(info["interface"], backend.access_points(info["device"]))
                self._pause(context)
            raise NetworkError("scan_timeout")
        except NetworkError:
            raise
        except Exception:
            raise NetworkError("scan_failed") from None
        finally:
            self._operation.release()

    @staticmethod
    def validate_connection(*, ssid, password=None, security="wpa-psk"):
        if not isinstance(ssid, str) or _display(ssid) != ssid:
            raise NetworkError("invalid_request")
        try:
            if not 1 <= len(ssid.encode("utf-8")) <= 32:
                raise NetworkError("invalid_request")
        except UnicodeError:
            raise NetworkError("invalid_request") from None
        if security not in ("open", "wpa-psk"):
            raise NetworkError("unsupported_security")
        if security == "open":
            if password not in (None, ""):
                raise NetworkError("invalid_request")
            return
        if not isinstance(password, str) or _display(password) != password:
            raise NetworkError("invalid_request")
        # wpa_supplicant accepts an 8..63 byte passphrase or 64 hex PSK.
        try:
            valid = 8 <= len(password.encode("utf-8")) <= 63 or bool(re.fullmatch("[0-9a-fA-F]{64}", password))
        except UnicodeError:
            valid = False
        if not valid:
            raise NetworkError("invalid_request")

    def confirm(self, task_id: str):
        with self._pending_lock:
            pending = self._pending
            if (pending is None or pending.task_id != task_id or not pending.awaiting
                    or self._clock() >= pending.deadline or self._closed.is_set()):
                raise NetworkError("confirmation_unavailable")
            pending.confirmation.set()
        return {"task_id": task_id, "confirmed": True}

    def connect(self, context, *, task_id: str, ssid: str, password: str | None = None,
                security: str = "wpa-psk"):
        self.validate_connection(ssid=ssid, password=password, security=security)
        if not isinstance(task_id, str) or not 1 <= len(task_id) <= 128 or _display(task_id) != task_id:
            raise NetworkError("invalid_request")
        if not self._operation.acquire(blocking=False):
            raise NetworkError("busy")
        checkpoint = None
        backend = None
        try:
            self._check(context)
            backend, info = self._prepare("connect")
            matches = [ap for ap in backend.access_points(info["device"])
                       if _ssid(ap.get("Ssid", [])) == (ssid, True) and _security(ap) == security]
            if not matches:
                raise NetworkError("network_not_found")
            target = max(matches, key=lambda ap: int(ap.get("Strength", 0)))
            context.update("creating_checkpoint", 5)
            # The system's 120-second rollback timer must also cover bounded
            # bus calls and final credential persistence, not just our waits.
            confirmation_limit = self._clock() + 95
            try:
                checkpoint = backend.checkpoint(info["device"])
            except Exception:
                raise NetworkError("checkpoint_failed") from None
            if not checkpoint or checkpoint == "/":
                raise NetworkError("checkpoint_failed")
            self._check(context)
            context.update("connecting", 15)
            try:
                profile, active = backend.activate(info["device"], target["path"],
                                                    ssid=ssid, password=password, security=security)
            except Exception:
                raise NetworkError("activation_failed") from None
            deadline = self._clock() + self._activation_timeout
            while True:
                self._check(context)
                status = backend.connection(info["device"], active)
                if status["state"] == 2 and status["current"] and status["ipv4"]:
                    break
                if status["state"] in (3, 4):
                    raise NetworkError("activation_failed")
                if self._clock() >= deadline:
                    raise NetworkError("activation_timeout")
                self._pause(context)
            pending = _Pending(task_id, min(self._clock() + self._confirmation_timeout,
                                           confirmation_limit), awaiting=True)
            with self._pending_lock:
                self._pending = pending
            context.update("awaiting_confirmation", 70)
            while True:
                self._check(context)
                status = backend.connection(info["device"], active)
                if not (status["state"] == 2 and status["current"] and status["ipv4"]):
                    raise NetworkError("connection_lost")
                if self._clock() >= pending.deadline:
                    raise NetworkError("confirmation_timeout")
                if pending.confirmation.is_set():
                    break
                self._pause(context)
            with self._pending_lock:
                pending.awaiting = False
            context.update("saving", 90)
            try:
                backend.save(profile, password=password, security=security)
                password = None
                self._check(context)
                # Saving must never turn a connection lost during finalization
                # into a successful transaction.
                status = backend.connection(info["device"], active)
                if not (status["state"] == 2 and status["current"] and status["ipv4"]):
                    raise NetworkError("connection_lost")
                backend.destroy(checkpoint)
            except NetworkError:
                raise
            except Exception:
                raise NetworkError("commit_failed") from None
            checkpoint = None
            return {"interface": info["interface"], "ssid": ssid, "ipv4": status["ipv4"],
                    "confirmed": True, "credentials_saved": True}
        except Exception as error:
            safe = error if isinstance(error, NetworkError) else NetworkError("activation_failed")
            if checkpoint is not None:
                self._cleanup_phase(context, "rolling_back")
                try:
                    outcome = backend.rollback(checkpoint)
                    if not outcome or any(result != 0 for result in outcome.values()):
                        raise NetworkError("rollback_failed")
                    safe.rollback = "succeeded"
                except Exception:
                    safe.rollback = "failed"
                self._cleanup_phase(context, "rolled_back" if safe.rollback == "succeeded" else "rollback_failed")
            raise safe from None
        finally:
            password = None
            with self._pending_lock:
                self._pending = None
            self._operation.release()

    def close(self):
        self._closed.set()
