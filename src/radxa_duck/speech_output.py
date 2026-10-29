"""On-demand CPU VITS synthesis and exclusive, bounded Pollen HAT playback.

Model archives come from the sherpa-onnx tts-models release. Only these explicitly
identified reference artifacts are accepted; configuring one does not load it.
Synthesis happens in a disposable process so cancellation can stop native code.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import wave

from . import audio_capture


MAX_TEXT_CHARS = 1200
MAX_TEXT_BYTES = 4096
MAX_CHUNK_CHARS = 80
MAX_AUDIO_SECONDS = 60
MAX_WAV_BYTES = 48_000 * 4 * MAX_AUDIO_SECONDS + 44
SYNTHESIS_TIMEOUT = 60.0
PEAK_LIMIT = 0.12
MODEL_SOURCE = "https://github.com/k2-fsa/sherpa-onnx/releases/tag/tts-models"
_MESSAGES = {
    "tts_unavailable": "CPU speech synthesis has not been configured",
    "missing_dependency": "Speech synthesis or audio playback dependencies are unavailable",
    "model_mismatch": "The configured speech model files do not match a supported version",
    "invalid_request": "Speech text or playback settings are invalid",
    "busy": "Another speech reply is running",
    "closed": "Speech output is closing",
    "cancelled": "The speech reply was cancelled",
    "synthesis_failed": "CPU speech synthesis failed",
    "synthesis_timeout": "CPU speech synthesis exceeded its time limit",
    "invalid_audio": "Speech synthesis did not produce valid bounded audio",
    "device_busy": "The board audio device is in use",
    "device_unavailable": "The board audio device is unavailable",
    "playback_failed": "The speech reply could not be played",
    "shutdown_timeout": "Speech output did not finish stopping",
}


class SpeechOutputError(RuntimeError):
    def __init__(self, code):
        self.code = code if code in _MESSAGES else "synthesis_failed"
        super().__init__(_MESSAGES[self.code])


@dataclass(frozen=True)
class _Model:
    name: str
    model: str
    sample_rate: int
    speakers: int
    files: tuple[tuple[str, int, str], ...]


_RULES = (
    ("phone.fst", 88630, "1ac2b6fa56b1442320c4de7db08353bab8963a2b57f365eebcdd3a2d3562f8d7"),
    ("date.fst", 59154, "eb8aa079ae3cb81d8f4404992f39d61a0cb990947512b5b8d1e54d1f6980e718"),
    ("number.fst", 64482, "743f402181fcfebf76cc2f0546b71fa26476e626fbe4e460fb7b4c3a7a8bd5bd"),
)
_MODELS = (
    _Model("vits-icefall-zh-aishell3", "model.onnx", 8000, 174, (
        ("model.onnx", 30482262, "5511d651b7840c0a93a6bbfd4afd070a2c7f39ca1ec3ff2ecd73191519bbb852"),
        ("tokens.txt", 1671, "50b45a7b7de1752fd3c7b4755661c285f1547f59186eca2281089a81307ad953"),
        ("lexicon.txt", 2042943, "ab2e61d357551e7b24ddd965d924aca784c20165ff58c150794e539c6b5e9e35"),
    ) + _RULES),
    # Converted Piper model card: zh_CN, one speaker, 22050 Hz; dataset CC0.
    _Model("vits-piper-zh_CN-chaowen-medium", "zh_CN-chaowen-medium.onnx", 22050, 1, (
        ("zh_CN-chaowen-medium.onnx", 63222141, "1cb646ea826a6ee73e329ea06319fb8d459da8a35be6c1ba30210387deb3bc55"),
        ("tokens.txt", 485, "e9e6d2838a2676749adbf0376c4cf3c3cd28eb27ed953dcc2adcab8db3c33ba7"),
        ("lexicon.txt", 2064505, "003e76f89fec389ec109b557f47cc315a1ac31a99091f0cc9d15949f9024416b"),
    ) + _RULES),
)


def _check(cancelled):
    if cancelled.is_set():
        raise SpeechOutputError("cancelled")


def _model(directory: Path | None, *, hashes=False, cancelled=None):
    if directory is None:
        raise SpeechOutputError("tts_unavailable")
    matches = [model for model in _MODELS if (directory / model.model).exists()]
    if len(matches) != 1:
        raise SpeechOutputError("model_mismatch")
    model = matches[0]
    try:
        for filename, size, expected in model.files:
            if cancelled is not None:
                _check(cancelled)
            path = directory / filename
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            with os.fdopen(os.open(path, flags), "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size != size:
                    raise SpeechOutputError("model_mismatch")
                if hashes:
                    digest = hashlib.sha256()
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        if cancelled is not None:
                            _check(cancelled)
                        digest.update(chunk)
                    if digest.hexdigest() != expected:
                        raise SpeechOutputError("model_mismatch")
    except OSError:
        raise SpeechOutputError("model_mismatch") from None
    return model


class _Cancelled:
    def __init__(self, *events):
        self.events = events

    def is_set(self):
        return any(event.is_set() for event in self.events)


def _worker_command(directory, speaker_id, output):
    return [sys.executable, "-B", "-m", "radxa_duck.speech_output", "--worker",
            "--model-dir", str(directory), "--speaker-id", str(speaker_id), "--output", str(output)]


def _read_audio(output):
    try:
        path = output / "reply.wav"
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_WAV_BYTES:
            raise ValueError()
        with wave.open(str(path), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (2, 2, 48000, "NONE"):
                raise ValueError()
            frames = audio.getnframes()
            if not 1 <= frames <= MAX_AUDIO_SECONDS * 48000 or path.stat().st_size != frames * 4 + 44:
                raise ValueError()
        info = output / "result.json"
        if not info.is_file() or info.is_symlink() or info.stat().st_size > 2048:
            raise ValueError()
        result = json.loads(info.read_bytes())
        fields = {"source_sample_rate", "audio_seconds", "model_load_seconds", "synthesis_seconds", "conversion_seconds"}
        if not isinstance(result, dict) or set(result) != fields:
            raise ValueError()
        if type(result["source_sample_rate"]) is not int or result["source_sample_rate"] not in (8000, 22050):
            raise ValueError()
        for key in fields - {"source_sample_rate"}:
            if type(result[key]) not in (int, float) or not math.isfinite(result[key]) or result[key] < 0:
                raise ValueError()
        if abs(result["audio_seconds"] - frames / 48000) > 1 / 48000:
            raise ValueError()
        return result
    except (OSError, ValueError, TypeError, wave.Error, EOFError, RecursionError):
        raise SpeechOutputError("invalid_audio") from None


def _synthesize(directory, speaker_id, text, output, cancelled):
    _check(cancelled)
    try:
        with subprocess.Popen(_worker_command(directory, speaker_id, output), stdin=subprocess.PIPE,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}) as process:
            try:
                process.stdin.write(text.encode("utf-8"))
                process.stdin.close()
                deadline = time.monotonic() + SYNTHESIS_TIMEOUT
                while process.poll() is None:
                    _check(cancelled)
                    if time.monotonic() >= deadline:
                        raise SpeechOutputError("synthesis_timeout")
                    try:
                        process.wait(timeout=0.05)
                    except subprocess.TimeoutExpired:
                        pass
                _check(cancelled)
                if process.returncode:
                    raise SpeechOutputError("synthesis_failed")
            finally:
                audio_capture._stop(process)
    except (OSError, subprocess.SubprocessError):
        raise SpeechOutputError("synthesis_failed") from None
    return _read_audio(output)


class SpeechOutput:
    def __init__(self, model_dir: Path | None = None, *, volume_percent=45, speaker_id=0):
        if type(volume_percent) is not int or not 0 <= volume_percent <= 100 or type(speaker_id) is not int or speaker_id < 0:
            raise SpeechOutputError("invalid_request")
        self._directory = Path(model_dir) if model_dir is not None else None
        self._volume, self._speaker = volume_percent, speaker_id
        self._cancelled = threading.Event()
        self._operation = threading.Lock()
        self._lock = threading.Lock()
        self._state = "idle"
        self._closed = False

    def snapshot(self):
        reason, model = None, None
        try:
            model = _model(self._directory)
            if self._speaker >= model.speakers:
                raise SpeechOutputError("invalid_request")
            if (importlib.util.find_spec("sherpa_onnx") is None
                    or shutil.which("aplay") is None or shutil.which("amixer") is None):
                raise SpeechOutputError("missing_dependency")
        except SpeechOutputError as exc:
            reason = exc.code
        except (ImportError, ValueError, OSError):
            reason = "missing_dependency"
        with self._lock:
            if self._closed:
                reason = "closed"
            return {"schema_version": 1, "type": "speech_output_status", "provider": "cpu",
                    "available": reason is None, "reason": reason, "state": self._state,
                    "model": model.name if model is not None else None, "volume_percent": self._volume,
                    "speaker_id": self._speaker, "sample_rate": 48000, "channels": 2,
                    "max_text_chars": MAX_TEXT_CHARS, "max_audio_seconds": MAX_AUDIO_SECONDS}

    def _phase(self, name, callback):
        with self._lock:
            self._state = name
        if callback is not None:
            callback(name)

    def speak(self, text, *, cancelled, on_phase=None):
        try:
            if (not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS
                    or len(text.encode("utf-8")) > MAX_TEXT_BYTES
                    or any(ord(char) < 32 and char not in "\n\t" or ord(char) == 127 for char in text)):
                raise SpeechOutputError("invalid_request")
        except UnicodeError:
            raise SpeechOutputError("invalid_request") from None
        if not self._operation.acquire(blocking=False):
            raise SpeechOutputError("busy")
        cancellation = _Cancelled(cancelled, self._cancelled)
        started = time.monotonic()
        try:
            with self._lock:
                if self._closed:
                    raise SpeechOutputError("closed")
            _check(cancellation)
            model = _model(self._directory)
            if self._speaker >= model.speakers:
                raise SpeechOutputError("invalid_request")
            if self._volume == 0:
                return {"provider": "cpu", "played": False, "reason": "volume_zero", "audio_seconds": 0}
            self._phase("synthesizing", on_phase)
            with tempfile.TemporaryDirectory(prefix="radxa-duck-speech-") as temporary:
                output = Path(temporary)
                result = _synthesize(self._directory, self._speaker, text, output, cancellation)
                _check(cancellation)
                self._phase("playing", on_phase)
                with audio_capture._audio_guard(cancellation):
                    _check(cancellation)
                    audio_capture._run(["amixer", "-q", "-c", audio_capture.CARD, "sset", "PCM", f"{self._volume}%"], 2, cancellation)
                    audio_capture._run(["amixer", "-q", "-c", audio_capture.CARD, "sset", "Line", "0dB", "unmute"], 2, cancellation)
                    playing = time.monotonic()
                    audio_capture._run(["aplay", "-q", "-D", audio_capture.DEVICE, str(output / "reply.wav")],
                                       result["audio_seconds"] + 5, cancellation)
                    result["playback_seconds"] = time.monotonic() - playing
            return {**result, "provider": "cpu", "model": model.name, "sample_rate": 48000, "channels": 2,
                    "played": True, "total_seconds": time.monotonic() - started}
        except audio_capture.CaptureError as exc:
            code = exc.code if exc.code in ("cancelled", "device_busy", "device_unavailable", "missing_dependency") else "playback_failed"
            raise SpeechOutputError(code) from None
        except (OSError, subprocess.SubprocessError):
            raise SpeechOutputError("playback_failed") from None
        finally:
            with self._lock:
                self._state = "closed" if self._closed else "idle"
            self._operation.release()

    def close(self, timeout=10):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 30:
            raise SpeechOutputError("invalid_request")
        with self._lock:
            self._closed = True
            self._cancelled.set()
        if not self._operation.acquire(timeout=timeout):
            raise SpeechOutputError("shutdown_timeout")
        self._operation.release()
        with self._lock:
            self._state = "closed"


def _convert(samples, source_rate):
    """Resample finite mono float32 audio, then attenuate and duplicate PCM16."""
    import numpy as np
    source = np.asarray(samples, dtype=np.float32)
    if (source.ndim != 1 or source_rate not in (8000, 22050) or not 1 <= source.size <= source_rate * MAX_AUDIO_SECONDS
            or not np.isfinite(source).all()):
        raise SpeechOutputError("invalid_audio")
    source = np.ascontiguousarray(source)
    library = audio_capture._samplerate()
    capacity = math.ceil(len(source) * 48000 / source_rate) + 64
    target = np.empty(capacity, dtype=np.float32)
    data = audio_capture._SRCData(source.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                                  target.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                                  len(source), capacity, 0, 0, 1, 48000 / source_rate)
    code = library.src_simple(ctypes.byref(data), audio_capture.SRC_CONVERTER, 1)
    count = data.output_frames_gen
    if (code or data.input_frames_used != len(source) or not 1 <= count <= MAX_AUDIO_SECONDS * 48000
            or abs(count - len(source) * 48000 / source_rate) > 1):
        raise SpeechOutputError("invalid_audio")
    mono = target[:count]
    if not np.isfinite(mono).all():
        raise SpeechOutputError("invalid_audio")
    peak = float(np.abs(mono).max())
    if peak > PEAK_LIMIT:
        mono = mono * (PEAK_LIMIT / peak)
    pcm = np.rint(mono * 32767).astype("<i2")
    stereo = np.repeat(pcm, 2).tobytes()
    return stereo, count


def _generate(engine, text, model, speaker_id):
    """Bound each native inference and reject incomplete/oversized replies."""
    import numpy as np
    pieces, frames = [], 0
    for offset in range(0, len(text), MAX_CHUNK_CHARS):
        chunk = text[offset:offset + MAX_CHUNK_CHARS].strip()
        if not chunk:
            continue
        remaining = model.sample_rate * MAX_AUDIO_SECONDS - frames
        callback_frames, oversized = 0, False

        def callback(samples, _progress):
            nonlocal callback_frames, oversized
            callback_frames += len(samples)
            oversized |= callback_frames > remaining
            # sherpa-onnx 1.13.8 VITS uses 1 to continue and 0 to stop.
            return int(not oversized)

        generated = engine.generate(chunk, sid=speaker_id,
                                    speed=1.0, callback=callback)
        samples = np.asarray(generated.samples, dtype=np.float32)
        if (oversized or generated.sample_rate != model.sample_rate or samples.ndim != 1
                or not 1 <= len(samples) <= remaining or not np.isfinite(samples).all()):
            raise SpeechOutputError("invalid_audio")
        pieces.append(samples)
        frames += len(samples)
    return np.concatenate(pieces)


def _worker(directory, speaker_id, output):
    # stdin contains only the current reply; it is never an argument or log.
    raw = sys.stdin.buffer.read(MAX_TEXT_BYTES + 1)
    if not raw or len(raw) > MAX_TEXT_BYTES:
        raise SpeechOutputError("invalid_request")
    text = raw.decode("utf-8")
    if len(text) > MAX_TEXT_CHARS or not text.strip():
        raise SpeechOutputError("invalid_request")
    model = _model(directory, hashes=True)
    if not 0 <= speaker_id < model.speakers:
        raise SpeechOutputError("invalid_request")
    import resource
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_WAV_BYTES + 4096, MAX_WAV_BYTES + 4096))
    os.umask(0o077)
    import sherpa_onnx
    loaded = time.monotonic()
    config = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(model=str(directory / model.model),
                  lexicon=str(directory / "lexicon.txt"), tokens=str(directory / "tokens.txt")),
            num_threads=2, provider="cpu", debug=False),
        rule_fsts=",".join(str(directory / name) for name, _, _ in _RULES), max_num_sentences=1)
    if not config.validate():
        raise SpeechOutputError("model_mismatch")
    engine = sherpa_onnx.OfflineTts(config)
    load_seconds = time.monotonic() - loaded
    generated_at = time.monotonic()
    samples = _generate(engine, text, model, speaker_id)
    synthesis_seconds = time.monotonic() - generated_at
    converted_at = time.monotonic()
    pcm, frames = _convert(samples, model.sample_rate)
    conversion_seconds = time.monotonic() - converted_at
    with wave.open(str(output / "reply.wav"), "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(pcm)
    (output / "result.json").write_text(json.dumps({"source_sample_rate": model.sample_rate,
        "audio_seconds": frames / 48000, "model_load_seconds": load_seconds,
        "synthesis_seconds": synthesis_seconds, "conversion_seconds": conversion_seconds}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--speaker-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        _worker(args.model_dir, args.speaker_id, args.output)
    except Exception:
        # Native/runtime exceptions can include source text and model paths.
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
