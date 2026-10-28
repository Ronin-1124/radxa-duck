"""Read-only ALSA discovery and coordinated Pollen HAT output controls.

Only PCM volume and the Line playback switch are changed. Input gain, routes,
DAC controls and speaker playback are deliberately outside this interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading

from . import audio_capture


class AudioDeviceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class _Unavailable(ValueError):
    pass


@dataclass(frozen=True)
class _Mixer:
    raw: tuple[int, ...] = ()
    percentages: tuple[int, ...] = ()
    switches: tuple[bool, ...] = ()
    minimum: int | None = None
    maximum: int | None = None


def _parse_mixer(control: str, text: str) -> _Mixer:
    if not text.startswith(f"Simple mixer control '{control}',0\n"):
        raise ValueError("Unexpected control")
    lines = text.splitlines()
    capabilities = next((line.split(":", 1)[1].split() for line in lines
                         if line.strip().startswith("Capabilities:")), [])
    bounds = None
    raw, percentages, switches = [], [], []
    for line in lines:
        match = re.fullmatch(r"\s*Limits:\s+Playback\s+(-?\d+)\s+-\s+(-?\d+)\s*", line)
        if match:
            bounds = tuple(map(int, match.groups()))
        match = re.fullmatch(r"\s*[^:]{1,64}:\s+Playback\s+(.+)", line)
        if not match or line.strip().startswith(("Limits:", "Capabilities:")):
            continue
        value = match.group(1)
        level = re.match(r"(-?\d+)\s+", value)
        percent = re.findall(r"\[(\d+)%\]", value)
        switch = re.findall(r"\[(on|off)\]", value)
        if control == "PCM":
            if level is None or len(percent) != 1:
                raise ValueError("Missing playback volume")
            raw.append(int(level.group(1)))
            percentages.append(int(percent[0]))
        else:
            if len(switch) != 1:
                raise ValueError("Missing playback switch")
            switches.append(switch[0] == "on")
    if control == "PCM":
        if ("pvolume" not in capabilities or bounds is None or bounds[0] >= bounds[1]
                or not 1 <= len(raw) <= 8 or len(raw) != len(percentages)
                or any(not bounds[0] <= value <= bounds[1] for value in raw)
                or any(not 0 <= value <= 100 for value in percentages)):
            raise ValueError("Invalid playback volume")
    elif "pswitch" not in capabilities or not 1 <= len(switches) <= 8:
        raise ValueError("Invalid playback switch")
    return _Mixer(tuple(raw), tuple(percentages), tuple(switches),
                  bounds[0] if bounds else None, bounds[1] if bounds else None)


class AudioDevices:
    """Query live ALSA state; mutate under the capture/playback process lock.

    runner follows subprocess.run's signature. proc_root remaps /proc/asound
    for fixtures. No board command runs on construction or module import.
    """

    def __init__(self, *, proc_root: Path = Path("/proc/asound"), runner=None):
        self._proc_root = Path(proc_root)
        self._runner = subprocess.run if runner is None else runner
        self._lock = threading.RLock()

    @staticmethod
    def _warning(warnings: list, field: str, code: str):
        warning = {"field": field, "code": code}
        if warning not in warnings:
            warnings.append(warning)

    def _read(self, path: Path, limit: int) -> str:
        try:
            with path.open("rb") as stream:
                value = stream.read(limit + 1)
        except OSError as exc:
            raise _Unavailable("Audio device information unavailable") from exc
        if len(value) > limit:
            raise ValueError("Audio device information exceeds limit")
        return value.decode("utf-8")

    def _command(self, operation: str, control: str, value: str | None = None) -> str:
        # Callers only supply fixed controls and validated integer/switch values.
        command = ["amixer", "-c", audio_capture.CARD, operation, control]
        if value is not None:
            command.append(value)
        environment = dict(os.environ, LC_ALL="C", LANG="C")
        try:
            # A file prevents an unexpected command response from accumulating
            # unbounded captured bytes in memory. Only 8 KiB is ever decoded.
            with tempfile.TemporaryFile() as output:
                result = self._runner(command, stdin=subprocess.DEVNULL, stdout=output,
                                      stderr=subprocess.DEVNULL, timeout=2,
                                      env=environment, check=False)
                if result.returncode != 0:
                    raise _Unavailable("Audio control unavailable")
                output.seek(0)
                value = output.read(8193)
            if len(value) > 8192:
                raise ValueError("Audio control response exceeds limit")
            return value.decode("utf-8")
        except (OSError, subprocess.SubprocessError) as exc:
            raise _Unavailable("Audio control unavailable") from exc

    def _devices(self, warnings: list) -> tuple[list, list]:
        inputs, outputs, cards = [], [], {}
        try:
            with os.scandir(self._proc_root) as entries:
                for index, entry in enumerate(entries):
                    if index >= 128:
                        break
                    match = re.fullmatch(r"card(\d{1,3})", entry.name)
                    if match:
                        try:
                            identity = self._read(Path(entry.path) / "id", 128).strip()
                            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identity):
                                raise ValueError("Invalid sound card identity")
                            cards[int(match[1])] = identity
                        except ValueError:
                            self._warning(warnings, "devices", "unavailable")
            text = self._read(self._proc_root / "pcm", 64 * 1024)
        except (OSError, ValueError) as exc:
            self._warning(warnings, "devices", "unavailable" if isinstance(exc, (OSError, _Unavailable)) else "invalid")
            return inputs, outputs
        for line in text.splitlines():
            match = re.fullmatch(r"(\d{1,3})-(\d{1,3}):\s*(.+)", line)
            if not match:
                if line.strip():
                    self._warning(warnings, "devices", "invalid")
                continue
            card_index, device_index = int(match[1]), int(match[2])
            card = cards.get(card_index, str(card_index))
            name = match[3].split(":", 1)[0].strip()
            if not name or len(name) > 256 or any(ord(char) < 32 for char in name):
                self._warning(warnings, "devices", "invalid")
                continue
            descriptor = {"id": f"hw:CARD={card},DEV={device_index}", "card": card, "name": name}
            if re.search(r":\s*capture\s+[1-9]\d*(?:\s|$)", line):
                known = card == audio_capture.CARD
                inputs.append(dict(descriptor, rate=48_000 if known else None,
                                   channels=2 if known else None,
                                   active_channel="left" if known else None, read_only=True))
            if re.search(r":\s*playback\s+[1-9]\d*(?:\s|$)", line):
                outputs.append(descriptor)
        return inputs, outputs

    def _collect(self) -> tuple[dict, _Mixer | None, _Mixer | None]:
        warnings = []
        inputs, outputs = self._devices(warnings)
        output = next((item["id"] for item in outputs if item["card"] == audio_capture.CARD), None)
        pcm, line = None, None
        if output is not None:
            for control in ("PCM", "Line"):
                try:
                    parsed = _parse_mixer(control, self._command("sget", control))
                    if control == "PCM":
                        pcm = parsed
                    else:
                        line = parsed
                except ValueError as exc:
                    self._warning(warnings, "output." + ("volume_percent" if control == "PCM" else "muted"),
                                  "unavailable" if isinstance(exc, _Unavailable) else "invalid")
        else:
            self._warning(warnings, "output", "unavailable")
        volume = pcm.percentages[0] if pcm and len(set(pcm.percentages)) == 1 else None
        muted = not line.switches[0] if line and len(set(line.switches)) == 1 else None
        if pcm and volume is None:
            self._warning(warnings, "output.volume_percent", "mixed_channels")
        if line and muted is None:
            self._warning(warnings, "output.muted", "mixed_channels")
        return ({"schema_version": 1, "type": "audio_devices",
                 "sampled_at": datetime.now(timezone.utc).isoformat(),
                 "inputs": inputs, "outputs": outputs,
                 "output": {"device": output, "volume_percent": volume, "muted": muted},
                 "capabilities": {"output_volume": pcm is not None and muted is not None,
                                  "output_mute": pcm is not None and muted is not None},
                 "warnings": warnings}, pcm, line)

    def snapshot(self) -> dict:
        with self._lock:
            return self._collect()[0]

    def _restore(self, pcm: _Mixer, line: _Mixer) -> bool:
        for control, value in (("Line", "mute"), ("PCM", ",".join(map(str, pcm.raw)))):
            try:
                self._command("sset", control, value)
            except ValueError:
                pass
        _snapshot, restored_pcm, _restored_line = self._collect()
        # Never reopen the line while a failed restore may leave a higher PCM
        # level behind. A successful readback is required before unmuting.
        safe = restored_pcm is not None and restored_pcm.raw == pcm.raw
        try:
            self._command("sset", "Line", "unmute" if safe and line.switches[0] else "mute")
        except ValueError:
            pass
        _snapshot, restored_pcm, restored_line = self._collect()
        return (restored_pcm is not None and restored_pcm.raw == pcm.raw
                and restored_line is not None and restored_line.switches == line.switches)

    def set_output(self, *, volume_percent: int | None = None, muted: bool | None = None) -> dict:
        if (volume_percent is not None and (type(volume_percent) is not int or not 0 <= volume_percent <= 100)
                or muted is not None and type(muted) is not bool
                or volume_percent is None and muted is None):
            raise AudioDeviceError("invalid_request", "Provide volume 0–100 or a mute boolean")
        try:
            with self._lock, audio_capture._audio_guard(None, mute_output=False):
                before, pcm, line = self._collect()
                if pcm is None or line is None or before["output"]["muted"] is None:
                    raise AudioDeviceError("device_unavailable", "Output controls are unavailable")
                target_muted = before["output"]["muted"] if muted is None else muted
                try:
                    self._command("sset", "Line", "mute")
                    if volume_percent is not None:
                        self._command("sset", "PCM", f"{volume_percent}%")
                    elif muted is True:
                        self._command("sset", "PCM", "0%")
                    if not target_muted:
                        self._command("sset", "Line", "unmute")
                    after, next_pcm, next_line = self._collect()
                    if next_pcm is None or next_line is None or after["output"]["muted"] != target_muted:
                        raise ValueError("Output state could not be verified")
                    if volume_percent is not None:
                        # ALSA quantizes percentages to a control's raw steps.
                        tolerance = max(1, math.ceil(100 / (next_pcm.maximum - next_pcm.minimum)))
                        if any(abs(value - volume_percent) > tolerance for value in next_pcm.percentages):
                            raise ValueError("Output volume did not change")
                    elif muted is True and any(value != next_pcm.minimum for value in next_pcm.raw):
                        raise ValueError("PCM mute did not change")
                    elif muted is not True and next_pcm.raw != pcm.raw:
                        raise ValueError("Output volume changed unexpectedly")
                    return after
                except ValueError:
                    restored = self._restore(pcm, line)
                    raise AudioDeviceError("mutation_failed" if restored else "rollback_failed",
                                           "Output change failed" if restored else "Output change and restoration failed") from None
        except audio_capture.CaptureError as exc:
            code = "device_busy" if exc.code == "device_busy" else "device_unavailable"
            raise AudioDeviceError(code, "Audio is busy" if code == "device_busy" else "Audio device is unavailable") from None
        except OSError:
            raise AudioDeviceError("device_unavailable", "Audio device is unavailable") from None
