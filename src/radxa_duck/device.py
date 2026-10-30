"""Read-only Linux device telemetry with bounded reads and on-demand sampling.

CPU usage is the aggregate busy-time delta between successful samples, excluding
guest counters already included by Linux in user/nice time. Its first value is
unknown. No device, privileged command, network connection or worker is opened.
Call snapshot from an executor when serving an asynchronous HTTP request.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from importlib import metadata
import math
import os
from pathlib import Path
import re
import shlex
import threading
import time


SOURCE_VERSION = "0.1.1"


class _ReadError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _text(value: bytes) -> str:
    try:
        result = value.decode("utf-8").strip()
    except UnicodeError as exc:
        raise _ReadError("invalid") from exc
    if not result or any(ord(char) < 32 or ord(char) == 127 for char in result):
        raise _ReadError("invalid")
    return result


class DeviceStatus:
    """Cache one copied JSON snapshot for a minimum sampling interval.

    root remaps /proc, /sys and /etc for tests; storage_path remains the logical
    absolute path reported by the API. No missing value falls back to host data
    outside that root. App version comes from installed distribution metadata,
    with the explicitly maintained source version used for source deployments.
    """

    def __init__(self, *, root: Path = Path("/"), storage_path: Path = Path("/"),
                 app_version: str | None = None, sample_interval: float = 1.0):
        if (type(sample_interval) not in (int, float) or not math.isfinite(sample_interval)
                or not 0.1 <= sample_interval <= 60):
            raise ValueError("sample_interval must be between 0.1 and 60 seconds")
        self._root, self._storage_path = Path(root), Path(storage_path)
        if not self._storage_path.is_absolute() or ".." in self._storage_path.parts:
            raise ValueError("storage_path must be an absolute path without parent traversal")
        if app_version is None:
            try:
                app_version = metadata.version("radxa-duck")
            except metadata.PackageNotFoundError:
                app_version = SOURCE_VERSION
        if (not isinstance(app_version, str) or not 1 <= len(app_version) <= 128
                or any(ord(char) < 32 or ord(char) == 127 for char in app_version)):
            raise ValueError("Invalid application version")
        self._app_version = app_version
        self._interval = sample_interval
        self._lock = threading.Lock()
        self._closed = False
        self._sample = None
        self._sampled_at = None
        self._cpu_previous = None

    def _path(self, path: str | Path) -> Path:
        return self._root / str(path).lstrip("/")

    def _read(self, path: str, limit: int = 4096) -> bytes:
        try:
            with self._path(path).open("rb") as source:
                value = source.read(limit + 1)
        except OSError as exc:
            raise _ReadError("unavailable") from exc
        if len(value) > limit:
            raise _ReadError("invalid")
        return value

    @staticmethod
    def _warning(warnings: list, field: str, code: str):
        item = {"field": field, "code": code}
        if item not in warnings:
            warnings.append(item)

    def _field(self, path: str, field: str, warnings: list) -> str | None:
        try:
            return _text(self._read(path))
        except _ReadError as exc:
            self._warning(warnings, field, exc.code)
            return None

    def _model(self, warnings: list) -> str | None:
        failure = "unavailable"
        for path in ("/sys/firmware/devicetree/base/model", "/proc/device-tree/model",
                     "/sys/firmware/devicetree/base/compatible", "/proc/device-tree/compatible"):
            try:
                return _text(self._read(path).split(b"\0", 1)[0])
            except _ReadError as exc:
                if exc.code == "invalid":
                    failure = "invalid"
        self._warning(warnings, "model", failure)
        return None

    def _os(self, warnings: list) -> str | None:
        try:
            values = {}
            for line in self._read("/etc/os-release", 64 * 1024).decode("utf-8").splitlines():
                key, separator, value = line.partition("=")
                if separator and key in ("PRETTY_NAME", "NAME"):
                    parts = shlex.split(value, comments=False, posix=True)
                    if len(parts) != 1:
                        raise _ReadError("invalid")
                    values[key] = _text(parts[0].encode("utf-8"))
            result = values.get("PRETTY_NAME") or values.get("NAME")
            if result is None:
                raise _ReadError("invalid")
            return result
        except (UnicodeError, ValueError) as exc:
            self._warning(warnings, "os", getattr(exc, "code", "invalid"))
            return None

    def _uptime(self, warnings: list) -> float | None:
        try:
            value = float(self._read("/proc/uptime", 256).split()[0])
            if not math.isfinite(value) or value < 0:
                raise _ReadError("invalid")
            return value
        except (ValueError, IndexError) as exc:
            self._warning(warnings, "uptime_seconds", getattr(exc, "code", "invalid"))
            return None

    def _cpu(self, now: float, warnings: list) -> dict:
        result = {"usage_percent": None, "sample_seconds": None, "cores": None, "load_average": None}
        try:
            lines = self._read("/proc/stat", 256 * 1024).decode("ascii").splitlines()
            line = next(line for line in lines if line.startswith("cpu "))
            counters = [int(value) for value in line.split()[1:]]
            if len(counters) < 4 or any(value < 0 for value in counters):
                raise _ReadError("invalid")
            total = sum(counters[:8])
            idle = counters[3] + (counters[4] if len(counters) > 4 else 0)
            cores = sum(bool(re.match(r"cpu[0-9]+\s", line)) for line in lines)
            if cores:
                result["cores"] = cores
            else:
                self._warning(warnings, "cpu.cores", "unavailable")
            previous = self._cpu_previous
            self._cpu_previous = (total, idle, now, counters[:8])
            if previous is not None:
                elapsed, delta, idle_delta = now - previous[2], total - previous[0], idle - previous[1]
                reset = (len(previous[3]) != len(counters[:8])
                         or any(value < old for value, old in zip(counters[:8], previous[3])))
                if not reset and elapsed > 0 and delta > 0 and 0 <= idle_delta <= delta:
                    result["usage_percent"] = (delta - idle_delta) / delta * 100
                    result["sample_seconds"] = elapsed
                elif reset or elapsed <= 0 or delta < 0 or idle_delta < 0 or idle_delta > delta:
                    self._warning(warnings, "cpu.usage_percent", "invalid")
        except (UnicodeError, ValueError, StopIteration) as exc:
            self._cpu_previous = None
            self._warning(warnings, "cpu", getattr(exc, "code", "invalid"))
        try:
            values = [float(value) for value in self._read("/proc/loadavg", 256).split()[:3]]
            if len(values) != 3 or any(not math.isfinite(value) or value < 0 for value in values):
                raise _ReadError("invalid")
            result["load_average"] = values
        except ValueError as exc:
            self._warning(warnings, "cpu.load_average", getattr(exc, "code", "invalid"))
        return result

    def _memory(self, warnings: list) -> dict:
        result = {"total_bytes": None, "available_bytes": None, "used_bytes": None}
        try:
            values = {}
            for line in self._read("/proc/meminfo", 64 * 1024).decode("ascii").splitlines():
                key, separator, value = line.partition(":")
                if separator and key in ("MemTotal", "MemAvailable"):
                    parts = value.split()
                    if len(parts) != 2 or parts[1] != "kB" or not parts[0].isdigit():
                        raise _ReadError("invalid")
                    values[key] = int(parts[0]) * 1024
            total = values.get("MemTotal")
            if total is None or total <= 0:
                raise _ReadError("invalid")
            result["total_bytes"] = total
            available = values.get("MemAvailable")
            if available is None:
                self._warning(warnings, "memory.available_bytes", "unavailable")
            elif not 0 <= available <= total:
                self._warning(warnings, "memory.available_bytes", "invalid")
            else:
                result["available_bytes"], result["used_bytes"] = available, total - available
        except (UnicodeError, ValueError) as exc:
            self._warning(warnings, "memory", getattr(exc, "code", "invalid"))
        return result

    def _storage(self, warnings: list) -> dict:
        result = {"path": str(self._storage_path), "total_bytes": None,
                  "available_bytes": None, "used_bytes": None}
        try:
            info = os.statvfs(self._path(self._storage_path))
            unit = info.f_frsize or info.f_bsize
            if unit <= 0 or info.f_blocks <= 0 or not 0 <= info.f_bavail <= info.f_bfree <= info.f_blocks:
                raise _ReadError("invalid")
            result.update(total_bytes=unit * info.f_blocks, available_bytes=unit * info.f_bavail,
                          used_bytes=unit * (info.f_blocks - info.f_bfree))
        except (OSError, ValueError) as exc:
            self._warning(warnings, "storage", getattr(exc, "code", "unavailable"))
        return result

    def _temperature(self, warnings: list) -> dict:
        result = {"celsius": None, "source": None}
        try:
            zones = []
            with os.scandir(self._path("/sys/class/thermal")) as entries:
                for index, entry in enumerate(entries):
                    if index >= 128:
                        break
                    if re.fullmatch(r"thermal_zone[0-9]+", entry.name):
                        zones.append(entry.name)
        except OSError:
            self._warning(warnings, "temperature", "unavailable")
            return result
        candidates = []
        invalid = False
        # Only CPU/SoC zones qualify. GPU or unrelated ambient readings are not
        # presented as the processor's temperature when its sensor is missing.
        for zone in sorted(zones):
            try:
                base = "/sys/class/thermal/" + zone
                kind = _text(self._read(base + "/type", 256))
                normalized = kind.lower().replace("_", "-")
                if normalized.startswith(("cpu", "bigcore", "littlecore")):
                    priority = 0
                elif normalized.startswith("soc"):
                    priority = 1
                else:
                    continue
                value = int(self._read(base + "/temp", 64)) / 1000
                if not math.isfinite(value) or not -40 <= value <= 150:
                    raise _ReadError("invalid")
                candidates.append((priority, -value, kind, value))
            except ValueError as exc:
                invalid = invalid or getattr(exc, "code", "invalid") == "invalid"
        if candidates:
            _priority, _negative, kind, value = min(candidates)
            result = {"celsius": value, "source": kind}
        else:
            self._warning(warnings, "temperature", "invalid" if invalid else "unavailable")
        return result

    def snapshot(self) -> dict:
        with self._lock:
            if self._closed:
                raise RuntimeError("Device status collector is closed")
            now = time.monotonic()
            if self._sampled_at is not None and 0 <= now - self._sampled_at < self._interval:
                return deepcopy(self._sample)
            warnings = []
            self._sample = {
                "schema_version": 1, "type": "device_status",
                "sampled_at": datetime.now(timezone.utc).isoformat(),
                "hostname": self._field("/proc/sys/kernel/hostname", "hostname", warnings),
                "model": self._model(warnings), "os": self._os(warnings),
                "kernel": self._field("/proc/sys/kernel/osrelease", "kernel", warnings),
                "uptime_seconds": self._uptime(warnings), "app_version": self._app_version,
                "cpu": self._cpu(now, warnings), "memory": self._memory(warnings),
                "storage": self._storage(warnings), "temperature": self._temperature(warnings),
                "warnings": warnings,
            }
            self._sampled_at = now
            return deepcopy(self._sample)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._sample = None
