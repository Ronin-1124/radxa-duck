"""Linux telemetry fixtures; no board access, privileged operations or secrets."""

from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import PackageNotFoundError
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from radxa_duck.device import DeviceStatus, SOURCE_VERSION


class DeviceStatusTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = 100.0
        clock = patch("radxa_duck.device.time.monotonic", lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.statvfs = SimpleNamespace(f_frsize=4096, f_bsize=4096, f_blocks=100,
                                      f_bfree=40, f_bavail=30)
        storage = patch("radxa_duck.device.os.statvfs", return_value=self.statvfs)
        self.storage = storage.start()
        self.addCleanup(storage.stop)
        self.write("/proc/sys/kernel/hostname", "radxa-zero3\n")
        self.write("/proc/sys/kernel/osrelease", "6.1.84-10-rk2410-nocsf\n")
        self.write("/sys/firmware/devicetree/base/model", b"Radxa ZERO 3W\0")
        self.write("/etc/os-release", 'NAME="Debian GNU/Linux"\nPRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\n')
        self.write("/proc/uptime", "1234.25 4000.12\n")
        self.write("/proc/loadavg", "0.25 0.50 0.75 2/123 999\n")
        self.write("/proc/meminfo", "MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\n")
        self.cpu((100, 20, 30, 400, 50, 0, 0, 0, 100, 20))
        self.zone(0, "soc-thermal", "55000")
        self.service = self.make()

    def write(self, path, value):
        target = self.root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value.encode() if isinstance(value, str) else value)

    def cpu(self, values):
        self.write("/proc/stat", "cpu  " + " ".join(map(str, values)) + "\n"
                   + "".join(f"cpu{index} 0 0 0 0 0 0 0 0\n" for index in range(4)))

    def zone(self, index, kind, value):
        self.write(f"/sys/class/thermal/thermal_zone{index}/type", kind)
        self.write(f"/sys/class/thermal/thermal_zone{index}/temp", value)

    def make(self, **kwargs):
        kwargs.setdefault("app_version", "0.1.0-test")
        service = DeviceStatus(root=self.root, **kwargs)
        self.addCleanup(service.close)
        return service

    def refresh(self):
        self.now += 1
        return self.service.snapshot()

    def test_identity_and_resources_are_real_fixture_values(self):
        value = self.service.snapshot()
        self.assertEqual(value["schema_version"], 1)
        self.assertEqual(value["type"], "device_status")
        self.assertTrue(value["sampled_at"].endswith("+00:00"))
        self.assertEqual(value["hostname"], "radxa-zero3")
        self.assertEqual(value["model"], "Radxa ZERO 3W")
        self.assertEqual(value["os"], "Debian GNU/Linux 12 (bookworm)")
        self.assertEqual(value["kernel"], "6.1.84-10-rk2410-nocsf")
        self.assertEqual(value["uptime_seconds"], 1234.25)
        self.assertEqual(value["app_version"], "0.1.0-test")
        self.assertEqual(value["cpu"], {"cores": 4, "usage_percent": None,
                                        "sample_seconds": None, "load_average": [0.25, 0.5, 0.75]})
        self.assertEqual(value["memory"], {"total_bytes": 1024000, "available_bytes": 409600,
                                           "used_bytes": 614400})
        self.assertEqual(value["storage"], {"path": "/", "total_bytes": 409600,
                                            "available_bytes": 122880, "used_bytes": 245760})
        self.assertEqual(value["temperature"], {"celsius": 55.0, "source": "soc-thermal"})
        self.assertEqual(value["warnings"], [])
        self.storage.assert_called_once_with(self.root)
        # statvfs reserved blocks are neither used nor available to this user.
        self.assertLess(value["storage"]["used_bytes"] + value["storage"]["available_bytes"],
                        value["storage"]["total_bytes"])

    def test_cpu_delta_excludes_guest_double_count_and_cache_is_isolated(self):
        first = self.service.snapshot()
        self.cpu((130, 30, 50, 430, 60, 0, 0, 0, 9999, 9999))
        self.now += 0.5
        first["cpu"]["cores"] = 99
        first["warnings"].append({"field": "poison", "code": "invalid"})
        cached = self.service.snapshot()
        self.assertEqual(cached["cpu"]["cores"], 4)
        self.assertEqual(cached["warnings"], [])
        self.assertIsNone(cached["cpu"]["usage_percent"])
        self.now += 0.5
        measured = self.service.snapshot()
        self.assertEqual(measured["cpu"]["usage_percent"], 60)
        self.assertEqual(measured["cpu"]["sample_seconds"], 1)

    def test_cpu_counter_reset_and_iowait_decrease_restart_baseline(self):
        self.service.snapshot()
        for counters in ((1, 1, 1, 4, 1, 0, 0, 0), (2, 1, 2, 5, 0, 0, 0, 0)):
            self.cpu(counters)
            value = self.refresh()
            self.assertIsNone(value["cpu"]["usage_percent"])
            self.assertIn({"field": "cpu.usage_percent", "code": "invalid"}, value["warnings"])
        self.cpu((12, 1, 2, 15, 0, 0, 0, 0))
        self.assertEqual(self.refresh()["cpu"]["usage_percent"], 50)

    def test_cpu_parse_failure_does_not_reuse_old_baseline_or_fail_other_fields(self):
        self.service.snapshot()
        self.write("/proc/stat", "cpu  1 2 nan 4\n")
        value = self.refresh()
        self.assertEqual(value["memory"]["total_bytes"], 1024000)
        self.assertEqual(value["cpu"]["load_average"], [0.25, 0.5, 0.75])
        self.assertIsNone(value["cpu"]["usage_percent"])
        self.assertIn({"field": "cpu", "code": "invalid"}, value["warnings"])
        self.cpu((130, 30, 50, 430, 60, 0, 0, 0))
        self.assertIsNone(self.refresh()["cpu"]["usage_percent"])

    def test_monotonic_reset_discards_cpu_interval_and_restarts_cache(self):
        self.service.snapshot()
        self.now = 99
        self.cpu((130, 30, 50, 430, 60, 0, 0, 0))
        value = self.service.snapshot()
        self.assertIsNone(value["cpu"]["usage_percent"])
        self.assertIn({"field": "cpu.usage_percent", "code": "invalid"}, value["warnings"])
        self.cpu((140, 30, 50, 440, 60, 0, 0, 0))
        self.assertEqual(self.refresh()["cpu"]["usage_percent"], 50)

    def test_unavailable_files_preserve_partial_status_without_host_fallback(self):
        empty_root = self.root / "empty"
        empty_root.mkdir()
        service = DeviceStatus(root=empty_root, app_version="test")
        self.addCleanup(service.close)
        self.storage.side_effect = FileNotFoundError()
        value = service.snapshot()
        self.assertEqual(value["app_version"], "test")
        for field in ("hostname", "model", "os", "kernel", "uptime_seconds"):
            self.assertIsNone(value[field])
        for field in ("cpu", "memory", "temperature"):
            self.assertTrue(all(item is None for item in value[field].values()))
        self.assertTrue(value["warnings"])
        self.assertTrue(all(item["code"] == "unavailable" for item in value["warnings"]))
        json.dumps(value, allow_nan=False)

    def test_unknown_memory_availability_is_not_replaced_with_free_memory(self):
        for available, code in ((None, "unavailable"), ("1001", "invalid")):
            with self.subTest(available=available):
                lines = "MemTotal: 1000 kB\nMemFree: 100 kB\n"
                if available is not None:
                    lines += f"MemAvailable: {available} kB\n"
                self.write("/proc/meminfo", lines)
                value = self.refresh()
                self.assertEqual(value["memory"]["total_bytes"], 1024000)
                self.assertIsNone(value["memory"]["available_bytes"])
                self.assertIsNone(value["memory"]["used_bytes"])
                self.assertIn({"field": "memory.available_bytes", "code": code}, value["warnings"])

    def test_temperature_uses_hottest_cpu_zone_before_soc_and_ignores_gpu(self):
        self.zone(1, "gpu-thermal", "140000")
        self.zone(2, "cpu-thermal", "51000")
        self.zone(3, "cpu_thermal", "52000")
        value = self.service.snapshot()
        self.assertEqual(value["temperature"], {"celsius": 52.0, "source": "cpu_thermal"})
        for index in (0, 2, 3):
            (self.root / f"sys/class/thermal/thermal_zone{index}/type").unlink()
        value = self.refresh()
        self.assertEqual(value["temperature"], {"celsius": None, "source": None})
        self.assertIn({"field": "temperature", "code": "unavailable"}, value["warnings"])

    def test_compatible_model_and_os_name_are_explicit_fallbacks(self):
        (self.root / "sys/firmware/devicetree/base/model").unlink()
        self.write("/proc/device-tree/compatible", b"radxa,zero3w\0rockchip,rk3566\0")
        self.write("/etc/os-release", 'NAME="Debian GNU/Linux"\n')
        value = self.service.snapshot()
        self.assertEqual(value["model"], "radxa,zero3w")
        self.assertEqual(value["os"], "Debian GNU/Linux")
        self.assertNotIn("model", [item["field"] for item in value["warnings"]])

    def test_invalid_and_oversized_reads_are_null_without_host_fallback_or_nan(self):
        self.write("/proc/sys/kernel/hostname", b"x" * 4097)
        self.write("/proc/sys/kernel/osrelease", b"\xff\xff")
        self.write("/proc/uptime", "nan 10")
        self.write("/proc/loadavg", "inf -1 0")
        self.write("/proc/meminfo", "MemTotal: 1 MB\nMemAvailable: 0 MB")
        self.write("/etc/os-release", 'PRETTY_NAME="unfinished')
        self.zone(0, "soc-thermal", "9999999999")
        self.storage.side_effect = OSError("permission denied: private path")
        value = self.service.snapshot()
        for field in ("hostname", "kernel", "uptime_seconds", "os"):
            self.assertIsNone(value[field])
        self.assertIsNone(value["cpu"]["load_average"])
        self.assertIsNone(value["memory"]["total_bytes"])
        self.assertIsNone(value["temperature"]["celsius"])
        self.assertIsNone(value["storage"]["total_bytes"])
        encoded = json.dumps(value, allow_nan=False)
        self.assertNotIn("private path", encoded)
        self.assertNotIn(str(self.root), encoded)

    def test_concurrent_browser_reads_share_one_sample(self):
        original = self.service._read
        with patch.object(self.service, "_read", wraps=original) as read:
            with ThreadPoolExecutor(max_workers=8) as pool:
                values = list(pool.map(lambda _: self.service.snapshot(), range(24)))
        self.assertEqual(sum(call.args[0] == "/proc/stat" for call in read.call_args_list), 1)
        self.assertTrue(all(value == values[0] for value in values))
        values[0]["memory"]["used_bytes"] = 0
        self.assertNotEqual(values[0], values[1])

    def test_close_and_invalid_configuration(self):
        self.service.close()
        self.service.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.service.snapshot()
        for interval in (True, 0, float("nan"), float("inf"), 61):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                self.make(sample_interval=interval)
        for path in (Path("relative"), Path("/var/../etc")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.make(storage_path=path)
        with patch("radxa_duck.device.metadata.version", side_effect=PackageNotFoundError):
            service = self.make(app_version=None)
            self.assertEqual(service.snapshot()["app_version"], SOURCE_VERSION)


if __name__ == "__main__":
    unittest.main()
