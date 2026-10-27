"""Local voice activity detection for 16 kHz mono PCM16 recordings and streams.

This first backend uses a verified Silero model with sherpa-onnx on the CPU.
It returns sample intervals, never records audio or invokes recognition/cloud.
Runtime dependencies are loaded only when constructing SileroVad.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import stat
import sys
import threading
import time
import wave


SAMPLE_RATE = 16_000
WINDOW_SAMPLES = 512
MAX_AUDIO_SECONDS = 30
# k2-fsa's 16 kHz Silero export; the size and SHA256 below pin the exact artifact.
# Source: https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx
MODEL_BYTES = 643_854
MODEL_SHA256 = "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"


class VadError(RuntimeError):
    """An observable failure, distinct from a successful no-speech result."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class VadConfig:
    threshold: float = 0.5
    min_speech_seconds: float = 0.25
    min_silence_seconds: float = 0.35
    threads: int = 1

    def __post_init__(self) -> None:
        for name, lower, upper in (
            ("threshold", 0, 1),
            ("min_speech_seconds", 0, 2),
            ("min_silence_seconds", 0, 2),
        ):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (float, int))
                    or not math.isfinite(value) or not lower < value < upper):
                raise VadError("invalid_config", f"{name} must be between {lower} and {upper}")
        if type(self.threads) is not int or not 1 <= self.threads <= 4:
            raise VadError("invalid_config", "threads must be an integer from 1 to 4")


@dataclass(frozen=True)
class SpeechSegment:
    """Half-open sample interval [start_sample, end_sample) in the input audio."""

    start_sample: int
    end_sample: int


@dataclass(frozen=True)
class VadFrame:
    """One unmodified PCM16 window and the detector's current speech state."""

    pcm: bytes
    start_sample: int
    speech: bool

    @property
    def end_sample(self) -> int:
        return self.start_sample + len(self.pcm) // 2


@dataclass(frozen=True)
class VadResult:
    input_samples: int
    segments: tuple[SpeechSegment, ...]
    vad_seconds: float
    config: VadConfig
    runtime_version: str

    @property
    def has_speech(self) -> bool:
        return bool(self.segments)

    def to_dict(self) -> dict:
        duration = self.input_samples / SAMPLE_RATE
        return {
            "schema_version": 1,
            "status": "speech" if self.has_speech else "no_speech",
            "has_speech": self.has_speech,
            "sample_rate": SAMPLE_RATE,
            "input_samples": self.input_samples,
            "audio_seconds": duration,
            "segments": [
                {**asdict(segment), "start_seconds": segment.start_sample / SAMPLE_RATE,
                 "end_seconds": segment.end_sample / SAMPLE_RATE}
                for segment in self.segments
            ],
            "speech_seconds": sum(s.end_sample - s.start_sample for s in self.segments) / SAMPLE_RATE,
            "vad_seconds": self.vad_seconds,
            "rtf": self.vad_seconds / duration,
            "backend": "sherpa-onnx/silero", "provider": "cpu",
            "runtime_version": self.runtime_version, "model_sha256": MODEL_SHA256,
            "config": asdict(self.config),
        }


def _check(cancelled: threading.Event | None, deadline: float | None = None) -> None:
    if cancelled is not None and cancelled.is_set():
        raise VadError("cancelled", "Voice activity detection cancelled")
    if deadline is not None and time.monotonic() >= deadline:
        raise VadError("timeout", "Voice activity detection exceeded its time budget")


def _validate_pcm(pcm: bytes) -> None:
    if not isinstance(pcm, bytes) or not 0 < len(pcm) <= MAX_AUDIO_SECONDS * SAMPLE_RATE * 2:
        raise VadError("invalid_audio", "Expected nonempty PCM16 bytes, at most 30 seconds")
    if len(pcm) % 2:
        raise VadError("invalid_audio", "PCM16 input has an incomplete sample")


def read_pcm16_wav(path: Path) -> bytes:
    """Read a bounded WAV; reject mismatched formats instead of relabelling them."""
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC), "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise VadError("invalid_audio", "WAV input must be a regular file")
            if info.st_size > MAX_AUDIO_SECONDS * SAMPLE_RATE * 2 + 65_536:
                raise VadError("invalid_audio", "WAV file exceeds the input size limit")
            with wave.open(handle, "rb") as stream:
                if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth(),
                        stream.getcomptype()) != (SAMPLE_RATE, 1, 2, "NONE"):
                    raise VadError("invalid_audio", "Expected 16 kHz mono PCM16 WAV; use asr.wav")
                count = stream.getnframes()
                if not 0 < count <= MAX_AUDIO_SECONDS * SAMPLE_RATE:
                    raise VadError("invalid_audio", "WAV duration must be greater than zero and at most 30s")
                pcm = stream.readframes(count)
                if len(pcm) != count * 2:
                    raise VadError("invalid_audio", "WAV audio is truncated")
                return pcm
    except (OSError, EOFError, wave.Error) as exc:
        raise VadError("invalid_audio", f"Cannot read WAV: {exc}") from exc


class SileroVad:
    """Reusable single-task CPU detector. Each call resets all temporal state.

    The model fingerprint pins the tested artifact before entering native code.
    Supporting another model requires verifying its format and behavior first.
    One instance can be kept for successive tasks; simultaneous calls fail busy.
    """

    def __init__(self, model: Path, config: VadConfig | None = None):
        self._config = config if config is not None else VadConfig()
        if not isinstance(self._config, VadConfig):
            raise VadError("invalid_config", "config must be VadConfig")
        started = time.monotonic()
        model = Path(model).expanduser().resolve()
        try:
            info = model.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size != MODEL_BYTES:
                raise VadError("unsupported_model", "Expected the verified 643854-byte Silero model")
            if hashlib.sha256(model.read_bytes()).hexdigest() != MODEL_SHA256:
                raise VadError("unsupported_model", "Silero model checksum does not match the verified artifact")
        except OSError as exc:
            raise VadError("model_unavailable", f"Cannot read VAD model: {exc}") from exc
        try:
            import numpy
            import sherpa_onnx
        except (ImportError, OSError) as exc:
            raise VadError("missing_dependency", "A compatible numpy and sherpa-onnx runtime is required") from exc
        self._numpy = numpy
        self.runtime_version = sherpa_onnx.__version__
        backend_config = sherpa_onnx.VadModelConfig()
        backend_config.silero_vad.model = str(model)
        backend_config.silero_vad.threshold = self.config.threshold
        backend_config.silero_vad.min_speech_duration = self.config.min_speech_seconds
        backend_config.silero_vad.min_silence_duration = self.config.min_silence_seconds
        backend_config.silero_vad.max_speech_duration = MAX_AUDIO_SECONDS + 1
        backend_config.silero_vad.window_size = WINDOW_SAMPLES
        backend_config.sample_rate = SAMPLE_RATE
        backend_config.num_threads = self.config.threads
        backend_config.provider = "cpu"
        backend_config.debug = False
        model_started = time.monotonic()
        try:
            self._detector = sherpa_onnx.VoiceActivityDetector(
                backend_config, buffer_size_in_seconds=MAX_AUDIO_SECONDS + 2,
            )
        except Exception as exc:
            raise VadError("backend_failure", f"Cannot initialize VAD: {exc}") from exc
        self.model_load_seconds = time.monotonic() - model_started
        self.setup_seconds = time.monotonic() - started
        self._lock = threading.Lock()

    @property
    def config(self) -> VadConfig:
        return self._config

    def open_stream(self, cancelled: threading.Event | None = None,
                    input_gain: float = 1.0) -> StreamingVad:
        """Reserve this detector for an incremental session until it is closed."""
        return StreamingVad(self, cancelled, input_gain)

    def detect_pcm(self, pcm: bytes, cancelled: threading.Event | None = None,
                   timeout_seconds: float = 5) -> VadResult:
        """Analyze normalized PCM16 without changing gain or invoking ASR.

        The deadline is cooperative, checked between native inference windows.
        It does not preempt a blocked native call or cover model initialization.
        """
        _validate_pcm(pcm)
        if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (float, int))
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60):
            raise VadError("invalid_config", "timeout_seconds must be greater than 0 and at most 60")
        deadline = time.monotonic() + timeout_seconds
        _check(cancelled, deadline)
        if not self._lock.acquire(blocking=False):
            raise VadError("busy", "This VAD instance is already processing audio")
        try:
            if self._detector is None:
                raise VadError("closed", "This VAD instance has been closed")
            np = self._numpy
            samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
            segments: list[SpeechSegment] = []

            def drain() -> None:
                while not self._detector.empty():
                    segment = self._detector.front
                    start = max(0, min(int(segment.start), len(samples)))
                    end = max(0, min(int(segment.start) + len(segment.samples), len(samples)))
                    self._detector.pop()
                    if end <= start:
                        continue
                    if segments and start <= segments[-1].end_sample:
                        last = segments[-1]
                        segments[-1] = SpeechSegment(last.start_sample, max(last.end_sample, end))
                    else:
                        segments.append(SpeechSegment(start, end))

            self._detector.reset()
            started = time.monotonic()
            for offset in range(0, len(samples), WINDOW_SAMPLES):
                _check(cancelled, deadline)
                chunk = samples[offset:offset + WINDOW_SAMPLES]
                if len(chunk) < WINDOW_SAMPLES:
                    chunk = np.pad(chunk, (0, WINDOW_SAMPLES - len(chunk)))
                self._detector.accept_waveform(chunk)
                drain()
            _check(cancelled, deadline)
            self._detector.flush()
            drain()
            _check(cancelled, deadline)
            return VadResult(len(samples), tuple(segments), time.monotonic() - started,
                             self.config, self.runtime_version)
        except VadError:
            raise
        except Exception as exc:
            raise VadError("backend_failure", f"Voice activity detection failed: {exc}") from exc
        finally:
            try:
                if self._detector is not None:
                    self._detector.reset()
            finally:
                self._lock.release()

    def close(self) -> None:
        if not self._lock.acquire(blocking=False):
            raise VadError("busy", "Cancel and finish the active task before closing VAD")
        try:
            self._detector = None
        finally:
            self._lock.release()

    def __enter__(self) -> SileroVad:
        return self

    def __exit__(self, *_args) -> None:
        self.close()


class StreamingVad:
    """Bounded incremental VAD session. Close discards buffered audio/state.

    Whole-file detection and another stream cannot use the model concurrently.
    Feed at most one second at a time; incomplete 512-sample windows stay local.
    Completed native segments are discarded immediately, not retained as audio.
    """

    def __init__(self, owner: SileroVad, cancelled: threading.Event | None = None,
                 input_gain: float = 1.0):
        if (isinstance(input_gain, bool) or not isinstance(input_gain, (int, float))
                or not 1 <= input_gain <= 8):
            raise VadError("invalid_config", "input_gain must be between 1 and 8")
        _check(cancelled)
        if not owner._lock.acquire(blocking=False):
            raise VadError("busy", "This VAD instance is already processing audio")
        self._owner = owner
        self._cancelled = cancelled
        self._input_gain = float(input_gain)
        self._call_lock = threading.Lock()
        self._pending = bytearray()
        self._samples = 0
        self._closed = False
        self._failed = False
        try:
            if owner._detector is None:
                raise VadError("closed", "This VAD instance has been closed")
            owner._detector.reset()
            _check(cancelled)
        except VadError:
            owner._lock.release()
            raise
        except Exception as exc:
            owner._lock.release()
            raise VadError("backend_failure", f"Cannot reset streaming VAD: {exc}") from exc
        except BaseException:
            owner._lock.release()
            raise

    def accept_pcm(self, pcm: bytes) -> tuple[VadFrame, ...]:
        if not isinstance(pcm, bytes) or not 0 < len(pcm) <= SAMPLE_RATE * 2 or len(pcm) % 2:
            raise VadError("invalid_audio", "Expected complete PCM16 samples, at most one second")
        if not self._call_lock.acquire(blocking=False):
            raise VadError("busy", "This VAD stream is already processing audio")
        try:
            if self._closed or self._failed:
                raise VadError("closed", "This VAD stream is closed or failed")
            _check(self._cancelled)
            self._pending.extend(pcm)
            frames = []
            detector = self._owner._detector
            np = self._owner._numpy
            while len(self._pending) >= WINDOW_SAMPLES * 2:
                _check(self._cancelled)
                window = bytes(self._pending[:WINDOW_SAMPLES * 2])
                del self._pending[:WINDOW_SAMPLES * 2]
                samples = np.frombuffer(window, dtype="<i2").astype(np.float32) / 32768
                if self._input_gain != 1:
                    # Only the model's float input is amplified. Returned PCM
                    # remains unchanged for the recognizer's own gain handling.
                    samples *= self._input_gain
                    np.clip(samples, -1, 1, out=samples)
                detector.accept_waveform(samples)
                _check(self._cancelled)
                speech = bool(detector.is_speech_detected())
                while not detector.empty():
                    detector.pop()
                frames.append(VadFrame(window, self._samples, speech))
                self._samples += WINDOW_SAMPLES
            return tuple(frames)
        except VadError as exc:
            if exc.code not in ("busy", "closed"):
                self._failed = True
                self._pending.clear()
            raise
        except Exception as exc:
            self._failed = True
            self._pending.clear()
            raise VadError("backend_failure", f"Streaming VAD failed: {exc}") from exc
        finally:
            self._call_lock.release()

    def close(self) -> None:
        if not self._call_lock.acquire(blocking=False):
            raise VadError("busy", "Cancel and finish the active VAD call before closing")
        try:
            if self._closed:
                return
            self._closed = True
            self._pending.clear()
            try:
                self._owner._detector.reset()
            except Exception as exc:
                raise VadError("backend_failure", f"Cannot reset streaming VAD: {exc}") from exc
            finally:
                self._owner._lock.release()
        finally:
            self._call_lock.release()

    def __enter__(self) -> StreamingVad:
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path, help="16 kHz mono PCM16 WAV, e.g. capture's asr.wav")
    parser.add_argument("--model", type=Path, required=True, help="verified silero_vad.onnx file")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=5, help="cooperative inference deadline in seconds")
    args = parser.parse_args(argv)
    cancelled = threading.Event()
    received_signal = 0

    def cancel(signum: int, _frame: object) -> None:
        nonlocal received_signal
        received_signal = signum
        cancelled.set()

    previous = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM)}
    started = time.monotonic()
    try:
        config = VadConfig(threshold=args.threshold, threads=args.threads)
        _check(cancelled)
        pcm = read_pcm16_wav(args.wav)
        _check(cancelled)
        with SileroVad(args.model, config) as detector:
            result = detector.detect_pcm(pcm, cancelled, args.timeout).to_dict()
            result.update(model_load_seconds=detector.model_load_seconds,
                          setup_seconds=detector.setup_seconds,
                          input_pcm_sha256=hashlib.sha256(pcm).hexdigest(),
                          total_seconds=time.monotonic() - started)
        _check(cancelled)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except VadError as exc:
        print(json.dumps({"status": "error", "error": {"code": exc.code, "message": str(exc)}}),
              file=sys.stderr)
        return 128 + received_signal if received_signal else 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
