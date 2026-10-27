"""CPU speech recognition for 16 kHz mono PCM16 recordings and live input.

Whole-file callers run VAD first; live sessions gate their decoder with VAD.
The caller schedules both modes in a background task.
This module returns text; it never records, uploads, stores, or executes it.
Imports and CLI help do not load third-party runtimes or model weights.
"""

from __future__ import annotations

import argparse
from collections import deque
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

from .vad import MAX_AUDIO_SECONDS, SAMPLE_RATE, WINDOW_SAMPLES, SileroVad, VadError, read_pcm16_wav


CHUNK_SAMPLES = 3_200
TAIL_SAMPLES = 10_560
MODEL_ID = "k2fsa-zipformer-bilingual-zh-en-t/e2382758de9a0219b4efe682b95af30b399db3b8"
# Artifact source: https://huggingface.co/csukuangfj/k2fsa-zipformer-bilingual-zh-en-t
# Encoder/joiner are INT8; decoder is FP32. Pin the matching vocabulary too.
MODEL_FILES = (
    ("encoder-epoch-99-avg-1.int8.onnx", 42_980_793,
     "db6f51551762e40e549166fe041ea3e45464370b595e9ad23f06478ec3794fbb"),
    ("decoder-epoch-99-avg-1.onnx", 13_877_276,
     "89be509a83175261695bdef5fd1c7b9ab1129a663d1284e7ba9f8507b21e0906"),
    ("joiner-epoch-99-avg-1.int8.onnx", 3_228_485,
     "bdda356d6f9b8c2d7cee9ee0e26075fa537490f7fd06520be408d287073667b9"),
    ("tokens.txt", 56_317,
     "a8e0e4ec53810e433789b54a5c0134a7eaa2ffca595a6334d54c00da858841d3"),
)


class AsrError(RuntimeError):
    """Recognition failure, distinct from a successful empty transcript."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AsrConfig:
    threads: int = 1
    normalize_audio: bool = True

    def __post_init__(self) -> None:
        if type(self.threads) is not int or not 1 <= self.threads <= 4:
            raise AsrError("invalid_config", "threads must be an integer from 1 to 4")
        if type(self.normalize_audio) is not bool:
            raise AsrError("invalid_config", "normalize_audio must be a boolean")


@dataclass(frozen=True)
class StreamingAsrConfig:
    max_gain: float = 6.0
    max_utterance_seconds: float = 20

    def __post_init__(self) -> None:
        for name, lower, upper in (("max_gain", 1, 8), ("max_utterance_seconds", 1, 30)):
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not lower <= value <= upper):
                raise AsrError("invalid_config", f"{name} must be between {lower} and {upper}")


@dataclass(frozen=True)
class AsrResult:
    text: str
    input_samples: int
    asr_seconds: float
    applied_gain: float
    config: AsrConfig
    runtime_version: str

    def to_dict(self) -> dict:
        duration = self.input_samples / SAMPLE_RATE
        return {
            "schema_version": 1,
            "status": "transcribed" if self.text else "empty_transcript",
            "text": self.text,
            "sample_rate": SAMPLE_RATE, "input_samples": self.input_samples,
            "audio_seconds": duration, "asr_seconds": self.asr_seconds,
            "applied_gain": self.applied_gain,
            "rtf": self.asr_seconds / duration,
            "backend": "sherpa-onnx/zipformer", "provider": "cpu",
            "model_id": MODEL_ID, "runtime_version": self.runtime_version,
            "config": asdict(self.config),
        }


def _check(cancelled: threading.Event | None, deadline: float | None = None) -> None:
    if cancelled is not None and cancelled.is_set():
        raise AsrError("cancelled", "Speech recognition cancelled")
    if deadline is not None and time.monotonic() >= deadline:
        raise AsrError("timeout", "Speech recognition exceeded its time budget")


def _validate_timeout(timeout_seconds: float) -> None:
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (float, int))
            or not 0 < timeout_seconds <= 120):
        raise AsrError("invalid_config", "timeout_seconds must be greater than 0 and at most 120")


def _verify_models(directory: Path) -> list[str]:
    paths = []
    try:
        for name, expected_size, expected_hash in MODEL_FILES:
            path = directory / name
            flags = os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC
            with os.fdopen(os.open(path, flags), "rb") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size != expected_size:
                    raise AsrError("unsupported_model", f"Unexpected ASR artifact: {name}")
                digest = hashlib.sha256()
                remaining = expected_size
                while remaining:
                    chunk = handle.read(min(1_048_576, remaining))
                    if not chunk:
                        raise AsrError("unsupported_model", f"Truncated ASR artifact: {name}")
                    digest.update(chunk)
                    remaining -= len(chunk)
                if handle.read(1) or digest.hexdigest() != expected_hash:
                    raise AsrError("unsupported_model", f"ASR artifact checksum mismatch: {name}")
            paths.append(str(path))
    except OSError as exc:
        raise AsrError("model_unavailable", f"Cannot read ASR model: {exc}") from exc
    return paths


class ZipformerAsr:
    """Reusable single-task recognizer; each call uses a fresh decoder stream.

    Model initialization is synchronous and outside the recognition deadline.
    Keep an instance for successive tasks and close it during application stop.
    """

    def __init__(self, model_dir: Path, config: AsrConfig | None = None):
        self._config = config if config is not None else AsrConfig()
        if not isinstance(self._config, AsrConfig):
            raise AsrError("invalid_config", "config must be AsrConfig")
        started = time.monotonic()
        try:
            directory = Path(model_dir).expanduser().resolve()
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            raise AsrError("model_unavailable", "Invalid ASR model directory") from exc
        encoder, decoder, joiner, tokens = _verify_models(directory)
        try:
            import numpy
            import sherpa_onnx
        except (ImportError, OSError) as exc:
            raise AsrError("missing_dependency", "A compatible numpy and sherpa-onnx runtime is required") from exc
        self._numpy = numpy
        self.runtime_version = sherpa_onnx.__version__
        model_started = time.monotonic()
        try:
            self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                encoder=encoder, decoder=decoder, joiner=joiner, tokens=tokens,
                num_threads=self.config.threads, sample_rate=SAMPLE_RATE,
                feature_dim=80, decoding_method="greedy_search",
                enable_endpoint_detection=False, provider="cpu", debug=False,
            )
        except Exception as exc:
            raise AsrError("backend_failure", f"Cannot initialize ASR: {exc}") from exc
        self.model_load_seconds = time.monotonic() - model_started
        self.setup_seconds = time.monotonic() - started
        self._lock = threading.Lock()

    @property
    def config(self) -> AsrConfig:
        return self._config

    def open_stream(self, vad: SileroVad, cancelled: threading.Event | None = None,
                    config: StreamingAsrConfig | None = None) -> StreamingAsr:
        """Reserve both models for continuous recognition until session close."""
        return StreamingAsr(self, vad, cancelled, config)

    def recognize_pcm(self, pcm: bytes, cancelled: threading.Event | None = None,
                      timeout_seconds: float = 20) -> AsrResult:
        """Recognize a complete recording without cropping or modifying its bytes.

        Cancellation/deadlines are checked between native calls, not within one.
        Late results are discarded. Timing includes conversion, features, tail
        padding and decoding; it is not pure model time or interaction latency.
        Optional gain targets a 0.8 peak, amplifies at most 8x, and never attenuates.
        An empty transcript is not a VAD decision and must not trigger actions.
        """
        if (not isinstance(pcm, bytes) or not 0 < len(pcm) <= MAX_AUDIO_SECONDS * SAMPLE_RATE * 2
                or len(pcm) % 2):
            raise AsrError("invalid_audio", "Expected nonempty PCM16 bytes, at most 30 seconds")
        _validate_timeout(timeout_seconds)
        started = time.monotonic()
        deadline = started + timeout_seconds
        _check(cancelled, deadline)
        if not self._lock.acquire(blocking=False):
            raise AsrError("busy", "This ASR instance is already processing audio")
        stream = None
        try:
            if self._recognizer is None:
                raise AsrError("closed", "This ASR instance has been closed")
            np = self._numpy
            samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
            gain = 1.0
            if self.config.normalize_audio:
                peak = float(np.max(np.abs(samples)))
                if peak:
                    gain = min(8.0, max(1.0, 0.8 / peak))
                    samples *= gain
            stream = self._recognizer.create_stream()

            def drain() -> None:
                while True:
                    _check(cancelled, deadline)
                    if not self._recognizer.is_ready(stream):
                        return
                    self._recognizer.decode_stream(stream)

            for offset in range(0, len(samples), CHUNK_SAMPLES):
                _check(cancelled, deadline)
                stream.accept_waveform(SAMPLE_RATE, samples[offset:offset + CHUNK_SAMPLES])
                drain()
            # Flush right context as required by the streaming model, without
            # extending input_samples or interpreting the tail as user audio.
            _check(cancelled, deadline)
            stream.accept_waveform(SAMPLE_RATE, np.zeros(TAIL_SAMPLES, dtype=np.float32))
            stream.input_finished()
            drain()
            text = self._recognizer.get_result(stream)
            if not isinstance(text, str):
                raise AsrError("backend_failure", "ASR returned an invalid transcript")
            _check(cancelled, deadline)
            return AsrResult(text.strip(), len(samples), time.monotonic() - started, gain,
                             self.config, self.runtime_version)
        except AsrError:
            raise
        except Exception as exc:
            raise AsrError("backend_failure", f"Speech recognition failed: {exc}") from exc
        finally:
            stream = None
            self._lock.release()

    def close(self) -> None:
        if not self._lock.acquire(blocking=False):
            raise AsrError("busy", "Cancel and finish the active task before closing ASR")
        try:
            self._recognizer = None
        finally:
            self._lock.release()

    def __enter__(self) -> ZipformerAsr:
        return self

    def __exit__(self, *_args) -> None:
        self.close()


class StreamingAsr:
    """Continuous VAD-gated recognition with partial and final text updates.

    Feed at most one second per call. Models stay resident, while each utterance
    uses a fresh decoder stream. Close discards an unfinished utterance, so a
    cancelled listening session cannot publish a late final result. Audio and
    text history are not retained here beyond bounded pre-roll/current context.
    """

    def __init__(self, owner: ZipformerAsr, vad: SileroVad,
                 cancelled: threading.Event | None = None,
                 config: StreamingAsrConfig | None = None):
        self.config = config if config is not None else StreamingAsrConfig()
        if not isinstance(self.config, StreamingAsrConfig):
            raise AsrError("invalid_config", "config must be StreamingAsrConfig")
        if not isinstance(vad, SileroVad):
            raise AsrError("invalid_config", "vad must be SileroVad")
        _check(cancelled)
        if not owner._lock.acquire(blocking=False):
            raise AsrError("busy", "This ASR instance is already processing audio")
        try:
            if owner._recognizer is None:
                raise AsrError("closed", "This ASR instance has been closed")
            self._vad = vad.open_stream(cancelled, input_gain=self.config.max_gain)
        except VadError as exc:
            owner._lock.release()
            raise AsrError(exc.code, str(exc)) from exc
        except BaseException:
            owner._lock.release()
            raise
        self._owner = owner
        self._cancelled = cancelled
        self._call_lock = threading.Lock()
        self._closed = False
        self._failed = False
        self._decoder = None
        self._partial_text = ""
        self._input_samples = 0
        self._start_sample = 0
        self._end_sample = 0
        self._speech = False
        self._gain = 1.0
        # Quiet syllables can precede a confident VAD decision. Keep at least
        # 1.024 s, and also cover a longer configured confirmation delay.
        pre_roll_frames = max(32, math.ceil(vad.config.min_speech_seconds * SAMPLE_RATE / WINDOW_SAMPLES) + 8)
        self._pre_roll = deque(maxlen=pre_roll_frames)

    def _feed(self, pcm: bytes, deadline: float) -> None:
        _check(self._cancelled, deadline)
        np = self._owner._numpy
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        peak = float(np.max(np.abs(samples)))
        if peak:
            # Gain may decrease if a later loud syllable would clip, but never
            # increases within a phrase or follows every noise window upwards.
            self._gain = min(self._gain, max(1.0, 0.8 / peak))
        samples *= self._gain
        self._decoder.accept_waveform(SAMPLE_RATE, samples)
        self._drain(deadline)

    def _drain(self, deadline: float) -> None:
        recognizer = self._owner._recognizer
        decoded = False
        while True:
            _check(self._cancelled, deadline)
            if not recognizer.is_ready(self._decoder):
                break
            recognizer.decode_stream(self._decoder)
            decoded = True
        if decoded:
            text = recognizer.get_result(self._decoder)
            if not isinstance(text, str):
                raise AsrError("backend_failure", "ASR returned an invalid transcript")
            self._partial_text = text.strip()
        _check(self._cancelled, deadline)

    def _finalize(self, reason: str, deadline: float) -> dict | None:
        _check(self._cancelled, deadline)
        self._decoder.accept_waveform(
            SAMPLE_RATE, self._owner._numpy.zeros(TAIL_SAMPLES, dtype=self._owner._numpy.float32),
        )
        self._decoder.input_finished()
        self._drain(deadline)
        text = self._owner._recognizer.get_result(self._decoder)
        if not isinstance(text, str):
            raise AsrError("backend_failure", "ASR returned an invalid transcript")
        _check(self._cancelled, deadline)
        result = {"text": text.strip(), "start_seconds": self._start_sample / SAMPLE_RATE,
                  "end_seconds": self._end_sample / SAMPLE_RATE, "reason": reason}
        self._decoder = None
        self._partial_text = ""
        self._pre_roll.clear()
        return result if result["text"] else None

    def accept_pcm(self, pcm: bytes) -> dict:
        """Return current partial text and only the finals generated by this call.

        The five-second processing deadline is cooperative between native calls;
        it is a failure limit, not a claim about maximum native execution time.
        """
        if not isinstance(pcm, bytes) or not 0 < len(pcm) <= SAMPLE_RATE * 2 or len(pcm) % 2:
            raise AsrError("invalid_audio", "Expected complete PCM16 samples, at most one second")
        if not self._call_lock.acquire(blocking=False):
            raise AsrError("busy", "This ASR stream is already processing audio")
        try:
            if self._closed or self._failed:
                raise AsrError("closed", "This ASR stream is closed or failed")
            deadline = time.monotonic() + 5
            _check(self._cancelled, deadline)
            frames = self._vad.accept_pcm(pcm)
            self._input_samples += len(pcm) // 2
            finalized = []
            for frame in frames:
                _check(self._cancelled, deadline)
                self._speech = frame.speech
                if self._decoder is None:
                    self._pre_roll.append(frame)
                    if not frame.speech:
                        continue
                    self._decoder = self._owner._recognizer.create_stream()
                    self._start_sample = self._pre_roll[0].start_sample
                    self._gain = float(self.config.max_gain)
                    for previous in self._pre_roll:
                        self._feed(previous.pcm, deadline)
                    self._pre_roll.clear()
                else:
                    self._feed(frame.pcm, deadline)
                self._end_sample = frame.end_sample
                limit = self._end_sample - self._start_sample >= self.config.max_utterance_seconds * SAMPLE_RATE
                if not frame.speech or limit:
                    result = self._finalize("limit" if limit else "silence", deadline)
                    if result is not None:
                        finalized.append(result)
            _check(self._cancelled, deadline)
            return {"partial_text": self._partial_text, "finalized": finalized,
                    "audio_seconds": self._input_samples / SAMPLE_RATE,
                    "speech_detected": self._speech, "applied_gain": self._gain}
        except VadError as exc:
            self._failed = True
            self._decoder = None
            self._pre_roll.clear()
            self._partial_text = ""
            raise AsrError(exc.code, str(exc)) from exc
        except AsrError as exc:
            if exc.code not in ("busy", "closed"):
                self._failed = True
                self._decoder = None
                self._pre_roll.clear()
                self._partial_text = ""
            raise
        except Exception as exc:
            self._failed = True
            self._decoder = None
            self._pre_roll.clear()
            self._partial_text = ""
            raise AsrError("backend_failure", f"Streaming speech recognition failed: {exc}") from exc
        finally:
            self._call_lock.release()

    def close(self) -> None:
        if not self._call_lock.acquire(blocking=False):
            raise AsrError("busy", "Cancel and finish the active ASR call before closing")
        try:
            if self._closed:
                return
            self._closed = True
            self._decoder = None
            self._partial_text = ""
            self._pre_roll.clear()
            try:
                self._vad.close()
            except VadError as exc:
                raise AsrError(exc.code, str(exc)) from exc
            finally:
                self._owner._lock.release()
        finally:
            self._call_lock.release()

    def __enter__(self) -> StreamingAsr:
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path, help="16 kHz mono PCM16 WAV")
    parser.add_argument("--model-dir", type=Path, required=True, help="verified Zipformer model directory")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--no-normalize", dest="normalize_audio", action="store_false",
                        help="disable bounded gain for quiet recordings")
    parser.add_argument("--timeout", type=float, default=20, help="cooperative recognition deadline in seconds")
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
        config = AsrConfig(threads=args.threads, normalize_audio=args.normalize_audio)
        _validate_timeout(args.timeout)
        _check(cancelled)
        try:
            pcm = read_pcm16_wav(args.wav)
        except VadError as exc:
            raise AsrError(exc.code, str(exc)) from exc
        _check(cancelled)
        with ZipformerAsr(args.model_dir, config) as recognizer:
            result = recognizer.recognize_pcm(pcm, cancelled, args.timeout).to_dict()
            result.update(model_load_seconds=recognizer.model_load_seconds,
                          setup_seconds=recognizer.setup_seconds)
        _check(cancelled)
        result["total_seconds"] = time.monotonic() - started
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except AsrError as exc:
        print(json.dumps({"status": "error", "error": {"code": exc.code, "message": str(exc)}},
                         ensure_ascii=False), file=sys.stderr)
        return 128 + received_signal if received_signal else 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
