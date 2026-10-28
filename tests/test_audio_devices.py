"""ALSA file and command fixtures; never writes to real mixer controls."""

from contextlib import contextmanager
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from radxa_duck.audio_capture import CaptureError
from radxa_duck.audio_devices import AudioDeviceError, AudioDevices, _parse_mixer


class _Alsa:
    def __init__(self):
        self.raw = [35, 35]
        self.switches = [False, False]
        self.calls = []
        self.fail = []
        self.invalid = {}
        self.ignore_volume = False

    def text(self, control):
        if control in self.invalid:
            return self.invalid[control]
        result = f"Simple mixer control '{control}',0\n"
        if control == "PCM":
            result += "  Capabilities: pvolume\n  Playback channels: Front Left - Front Right\n  Limits: Playback 0 - 100\n"
            for channel, value in zip(("Front Left", "Front Right"), self.raw):
                result += f"  {channel}: Playback {value} [{value}%] [-3.00dB]\n"
        else:
            result += "  Capabilities: pswitch pswitch-joined\n  Playback channels: Front Left - Front Right\n"
            for channel, value in zip(("Front Left", "Front Right"), self.switches):
                result += f"  {channel}: Playback [{'on' if value else 'off'}]\n"
        return result

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        operation, control = command[3:5]
        key = tuple(command[3:])
        if key in self.fail:
            self.fail.remove(key)
            return SimpleNamespace(returncode=1)
        if operation == "sget":
            value = self.text(control)
            kwargs["stdout"].write(value.encode() if isinstance(value, str) else value)
        elif operation == "sset":
            value = command[5]
            if control == "PCM" and not self.ignore_volume:
                self.raw = ([int(value[:-1])] * 2 if value.endswith("%")
                            else list(map(int, value.split(","))))
            elif control == "Line":
                self.switches = [value == "unmute"] * 2
        else:
            raise AssertionError(command)
        return SimpleNamespace(returncode=0)


class AudioDevicesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "card0").mkdir()
        (self.root / "card0/id").write_text("PollenHATC1\n")
        (self.root / "card1").mkdir()
        (self.root / "card1/id").write_text("USBMic\n")
        (self.root / "pcm").write_text("00-00: HAT HiFi : HAT codec : playback 1 : capture 1\n"
                                            "01-00: USB microphone : USB audio : capture 1\n")
        self.alsa = _Alsa()
        self.audio = AudioDevices(proc_root=self.root, runner=self.alsa)
        self.guard_calls = []

        @contextmanager
        def guard(cancelled, *, mute_output=True):
            self.guard_calls.append((cancelled, mute_output))
            yield

        self.guard = patch("radxa_duck.audio_devices.audio_capture._audio_guard", guard)
        self.guard.start()
        self.addCleanup(self.guard.stop)

    def writes(self):
        return [command[3:] for command, _kwargs in self.alsa.calls if command[3] == "sset"]

    def test_read_only_discovery_and_true_hardware_values(self):
        value = self.audio.snapshot()
        self.assertEqual(value["schema_version"], 1)
        self.assertEqual(value["type"], "audio_devices")
        self.assertTrue(value["sampled_at"].endswith("+00:00"))
        self.assertEqual(len(value["inputs"]), 2)
        self.assertEqual(value["inputs"][0], {"id": "hw:CARD=PollenHATC1,DEV=0", "card": "PollenHATC1",
                                            "name": "HAT HiFi", "rate": 48000, "channels": 2,
                                            "active_channel": "left", "read_only": True})
        self.assertIsNone(value["inputs"][1]["rate"])
        self.assertEqual(value["output"], {"device": "hw:CARD=PollenHATC1,DEV=0", "volume_percent": 35, "muted": True})
        self.assertEqual(value["capabilities"], {"output_volume": True, "output_mute": True})
        self.assertEqual(value["warnings"], [])
        self.assertEqual(self.guard_calls, [])
        self.assertEqual(self.writes(), [])
        for command, kwargs in self.alsa.calls:
            self.assertEqual(command[:3], ["amixer", "-c", "PollenHATC1"])
            self.assertEqual(kwargs["env"]["LC_ALL"], "C")
            self.assertEqual(kwargs["timeout"], 2)
            self.assertFalse(kwargs["check"])
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            self.assertNotIn("shell", kwargs)

    def test_volume_preserves_mute_and_unmute_does_not_raise_volume(self):
        value = self.audio.set_output(volume_percent=60)
        self.assertEqual(value["output"]["volume_percent"], 60)
        self.assertTrue(value["output"]["muted"])
        self.assertEqual(self.guard_calls, [(None, False)])
        self.assertEqual(self.writes(), [["sset", "Line", "mute"], ["sset", "PCM", "60%"]])
        value = self.audio.set_output(muted=False)
        self.assertFalse(value["output"]["muted"])
        self.assertEqual(value["output"]["volume_percent"], 60)
        value = self.audio.set_output(muted=True)
        self.assertEqual(value["output"]["volume_percent"], 0)
        self.assertTrue(value["output"]["muted"])
        value = self.audio.set_output(muted=False)
        self.assertFalse(value["output"]["muted"])
        self.assertEqual(value["output"]["volume_percent"], 0)

    def test_combined_volume_and_mute_change_is_ordered(self):
        self.alsa.switches = [True, True]
        value = self.audio.set_output(volume_percent=45, muted=False)
        self.assertEqual(value["output"]["volume_percent"], 45)
        self.assertFalse(value["output"]["muted"])
        self.assertEqual(self.writes(), [["sset", "Line", "mute"], ["sset", "PCM", "45%"],
                                        ["sset", "Line", "unmute"]])
        value = self.audio.set_output(volume_percent=25, muted=True)
        self.assertEqual(value["output"]["volume_percent"], 25)
        self.assertTrue(value["output"]["muted"])

    def test_busy_capture_rejects_before_any_mixer_command(self):
        @contextmanager
        def busy(*_args, **_kwargs):
            raise CaptureError("private path or raw error", code="device_busy")
            yield

        with patch("radxa_duck.audio_devices.audio_capture._audio_guard", busy):
            with self.assertRaises(AudioDeviceError) as raised:
                self.audio.set_output(volume_percent=30)
        self.assertEqual(raised.exception.code, "device_busy")
        self.assertNotIn("private", str(raised.exception))
        self.assertEqual(self.alsa.calls, [])

    def test_failed_second_write_restores_exact_raw_channels_and_switches(self):
        self.alsa.raw = [20, 30]
        self.alsa.switches = [True, True]
        self.alsa.fail.append(("sset", "PCM", "75%"))
        with self.assertRaises(AudioDeviceError) as raised:
            self.audio.set_output(volume_percent=75)
        self.assertEqual(raised.exception.code, "mutation_failed")
        self.assertEqual(self.alsa.raw, [20, 30])
        self.assertEqual(self.alsa.switches, [True, True])
        self.assertIn(["sset", "PCM", "20,30"], self.writes())

    def test_rollback_failure_keeps_line_muted_instead_of_exposing_wrong_volume(self):
        self.alsa.switches = [True, True]
        self.alsa.fail.extend([("sset", "Line", "unmute"), ("sset", "PCM", "35,35")])
        with self.assertRaises(AudioDeviceError) as raised:
            self.audio.set_output(volume_percent=75)
        self.assertEqual(raised.exception.code, "rollback_failed")
        self.assertEqual(self.alsa.raw, [75, 75])
        self.assertEqual(self.alsa.switches, [False, False])
        self.assertEqual(self.writes()[-1], ["sset", "Line", "mute"])

    def test_ignored_mutation_is_rejected_and_rollback_is_verified(self):
        self.alsa.ignore_volume = True
        with self.assertRaises(AudioDeviceError) as raised:
            self.audio.set_output(volume_percent=90)
        self.assertEqual(raised.exception.code, "mutation_failed")
        self.assertEqual(self.audio.snapshot()["output"]["volume_percent"], 35)

    def test_absent_hardware_has_no_fake_capabilities_or_commands(self):
        (self.root / "pcm").write_text("01-00: USB microphone : USB audio : capture 1\n")
        value = self.audio.snapshot()
        self.assertEqual(value["outputs"], [])
        self.assertEqual(value["output"], {"device": None, "volume_percent": None, "muted": None})
        self.assertEqual(value["capabilities"], {"output_volume": False, "output_mute": False})
        with self.assertRaises(AudioDeviceError) as raised:
            self.audio.set_output(muted=False)
        self.assertEqual(raised.exception.code, "device_unavailable")
        self.assertEqual(self.alsa.calls, [])

    def test_invalid_oversized_and_missing_mixer_responses_are_unavailable(self):
        for invalid in ("x" * 8193, b"\xff\xff", "Simple mixer control 'Other',0\n",
                        "Simple mixer control 'PCM',0\n  Capabilities: pvolume\n  Limits: Playback 0 - 100\n  Mono: Playback 2 [999%]\n"):
            with self.subTest(invalid=repr(invalid)[:40]):
                self.alsa.invalid["PCM"] = invalid
                value = self.audio.snapshot()
                self.assertIsNone(value["output"]["volume_percent"])
                self.assertFalse(value["capabilities"]["output_volume"])
        self.alsa.invalid.clear()
        self.alsa.fail.append(("sget", "Line"))
        value = self.audio.snapshot()
        self.assertIsNone(value["output"]["muted"])
        self.assertFalse(value["capabilities"]["output_mute"])

    def test_missing_dependency_and_timeouts_do_not_leak_raw_errors(self):
        for error in (FileNotFoundError("private"), subprocess.TimeoutExpired("secret", 2, output=b"private")):
            def fail(*_args, **_kwargs):
                raise error

            audio = AudioDevices(proc_root=self.root, runner=fail)
            value = audio.snapshot()
            self.assertIsNone(value["output"]["volume_percent"])
            self.assertNotIn("private", str(value))
            with self.assertRaises(AudioDeviceError) as raised:
                audio.set_output(volume_percent=30)
            self.assertEqual(raised.exception.code, "device_unavailable")
            self.assertNotIn("private", str(raised.exception))

    def test_mixed_line_switches_are_not_presented_as_a_known_mute_state(self):
        self.alsa.switches = [True, False]
        value = self.audio.snapshot()
        self.assertIsNone(value["output"]["muted"])
        self.assertFalse(value["capabilities"]["output_mute"])
        with self.assertRaises(AudioDeviceError):
            self.audio.set_output(muted=True)
        self.assertEqual(self.writes(), [])

    def test_invalid_inputs_are_rejected_without_hardware_access(self):
        for kwargs in ({}, {"volume_percent": True}, {"volume_percent": -1}, {"volume_percent": 101},
                       {"volume_percent": 2.5}, {"muted": 1}, {"muted": "false"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(AudioDeviceError) as raised:
                self.audio.set_output(**kwargs)
            self.assertEqual(raised.exception.code, "invalid_request")
        self.assertEqual(self.guard_calls, [])
        self.assertEqual(self.alsa.calls, [])

    def test_negative_ranges_and_mono_playback_parse_without_db_confusion(self):
        value = _parse_mixer("PCM", "Simple mixer control 'PCM',0\n  Capabilities: pvolume pvolume-joined\n"
                             "  Limits: Playback -127 - 0\n  Mono: Playback -32 [75%] [-16.00dB]\n")
        self.assertEqual(value.raw, (-32,))
        self.assertEqual(value.percentages, (75,))
        self.assertEqual((value.minimum, value.maximum), (-127, 0))


if __name__ == "__main__":
    unittest.main()
