"""Play a verified official Microduck sound bank, without neural synthesis."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import threading
import time
import wave

from . import audio_capture
from .interaction import SOUNDS
from .speech_output import SpeechOutputError, _Cancelled, _check


class DuckOutput:
    def __init__(self, directory: Path, *, volume_percent=100):
        if type(volume_percent) is not int or not 0 <= volume_percent <= 100:
            raise ValueError("Invalid duck playback volume")
        self._directory, self._volume = Path(directory), volume_percent
        self._lock, self._operation = threading.Lock(), threading.Lock()
        self._cancelled = threading.Event()
        self._closed = False
        self._state, self._last = "idle", None
        self._clips, self._reason = {}, None
        try:
            manifest_path = self._directory / "manifest.json"
            if manifest_path.is_symlink() or manifest_path.stat().st_size > 16384:
                raise ValueError()
            manifest = json.loads(manifest_path.read_bytes())
            clips = manifest["clips"]
            if set(clips) != set(SOUNDS):
                raise ValueError()
            for sound, record in clips.items():
                path = self._directory / (sound + ".wav")
                if path.is_symlink() or path.stat().st_size > 48000 * 4 * 5 + 44:
                    raise ValueError()
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != record["sha256"]:
                    raise ValueError()
                with wave.open(str(path), "rb") as wav:
                    if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getcomptype()) != (48000, 2, 2, "NONE"):
                        raise ValueError()
                    count = wav.getnframes()
                    if not 1 <= count <= 48000 * 5 or len(data) != count * 4 + 44:
                        raise ValueError()
                self._clips[sound] = {"path": path, "audio_seconds": count / 48000}
        except (OSError, ValueError, TypeError, KeyError, wave.Error):
            self._reason, self._clips = "invalid_audio", {}
        if shutil.which("aplay") is None or shutil.which("amixer") is None:
            self._reason = "missing_dependency"

    def snapshot(self):
        with self._lock:
            reason = "closed" if self._closed else self._reason
            return {"available": reason is None, "reason": reason, "kind": "duck", "state": self._state,
                    "tts_available": False, "sounds": list(self._clips), "last_sound": deepcopy(self._last),
                    "volume_percent": self._volume, "sample_rate": 48000, "channels": 2}

    def play(self, sound, *, cancelled, on_phase=None):
        if not isinstance(sound, str) or sound not in (*SOUNDS, "none"):
            raise SpeechOutputError("invalid_request")
        if not self._operation.acquire(blocking=False):
            raise SpeechOutputError("device_busy")
        cancellation = _Cancelled(cancelled, self._cancelled)
        try:
            _check(cancellation)
            status = self.snapshot()
            if not status["available"]:
                raise SpeechOutputError(status["reason"])
            if sound == "none":
                with self._lock:
                    self._last = {"id": "none", "state": "skipped"}
                return {"played": False, "sound": sound, "audio_seconds": 0}
            clip = self._clips[sound]
            with self._lock:
                self._state = "playing"
                self._last = {"id": sound, "state": "running"}
            if on_phase is not None:
                on_phase("playing")
            started = time.monotonic()
            with audio_capture._audio_guard(cancellation):
                audio_capture._run(["amixer", "-q", "-c", audio_capture.CARD, "sset", "PCM", f"{self._volume}%"], 2, cancellation)
                audio_capture._run(["amixer", "-q", "-c", audio_capture.CARD, "sset", "Line", "0dB", "unmute"], 2, cancellation)
                audio_capture._run(["aplay", "-q", "-D", audio_capture.DEVICE, str(clip["path"])],
                                   clip["audio_seconds"] + 5, cancellation)
            # Brief acoustic tail before the application may reopen capture.
            if cancelled.wait(0.2):
                _check(cancellation)
            _check(cancellation)
            with self._lock:
                self._last = {"id": sound, "state": "succeeded"}
            return {"played": True, "sound": sound, "audio_seconds": clip["audio_seconds"],
                    "playback_seconds": time.monotonic() - started}
        except audio_capture.CaptureError as exc:
            raise SpeechOutputError(exc.code if exc.code in ("cancelled", "device_busy", "device_unavailable", "missing_dependency")
                                    else "playback_failed") from None
        except OSError:
            raise SpeechOutputError("playback_failed") from None
        finally:
            with self._lock:
                if self._last is not None and self._last["state"] == "running":
                    self._last["state"] = "cancelled" if cancellation.is_set() else "failed"
                self._state = "closed" if self._closed else "idle"
            self._operation.release()

    def close(self, timeout=10):
        with self._lock:
            self._closed = True
            self._cancelled.set()
        if not self._operation.acquire(timeout=timeout):
            raise SpeechOutputError("shutdown_timeout")
        self._operation.release()
