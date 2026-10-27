"""Pollen HAT capture: 48 kHz stereo input to 16 kHz mono PCM streams or WAVs.

Run with PYTHONPATH=src python3 -m radxa_duck.audio_capture OUTPUT_DIRECTORY.
Requires a configured PollenHATC1, arecord/amixer (alsa-utils), and
libsamplerate.so.0 (libsamplerate0) on Linux.
Importing this module does not access hardware or load the resampling library.
"""

from __future__ import annotations

import argparse
import array
from contextlib import contextmanager
import ctypes
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import BinaryIO, Callable, Iterator
import wave


CARD = "PollenHATC1"
DEVICE = f"hw:CARD={CARD},DEV=0"
RAW_RATE = 48_000
ASR_RATE = 16_000
MAX_SECONDS = 10
STREAM_FRAMES = RAW_RATE // 10
STREAM_READ_TIMEOUT = 2.0
SRC_CONVERTER = 1  # SRC_SINC_MEDIUM_QUALITY
LOCK_PATH = Path(tempfile.gettempdir()) / f"radxa-duck-audio-{os.getuid()}.lock"


class CaptureError(RuntimeError):
    """Capture could not complete; no successful result was published."""

    code = "capture_failed"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        if code is not None:
            self.code = code


class CaptureCancelled(CaptureError):
    """The caller cancelled the operation."""

    code = "cancelled"


class _SRCData(ctypes.Structure):
    _fields_ = [
        ("data_in", ctypes.POINTER(ctypes.c_float)),
        ("data_out", ctypes.POINTER(ctypes.c_float)),
        ("input_frames", ctypes.c_long),
        ("output_frames", ctypes.c_long),
        ("input_frames_used", ctypes.c_long),
        ("output_frames_gen", ctypes.c_long),
        ("end_of_input", ctypes.c_int),
        ("src_ratio", ctypes.c_double),
    ]


def _samplerate() -> ctypes.CDLL:
    try:
        library = ctypes.CDLL("libsamplerate.so.0")
    except OSError as exc:
        raise CaptureError("Install the system libsamplerate0 package", code="missing_dependency") from exc
    library.src_simple.argtypes = [ctypes.POINTER(_SRCData), ctypes.c_int, ctypes.c_int]
    library.src_simple.restype = ctypes.c_int
    library.src_short_to_float_array.argtypes = [
        ctypes.POINTER(ctypes.c_short), ctypes.POINTER(ctypes.c_float), ctypes.c_int,
    ]
    library.src_short_to_float_array.restype = None
    library.src_float_to_short_array.argtypes = [
        ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_short), ctypes.c_int,
    ]
    library.src_float_to_short_array.restype = None
    library.src_strerror.argtypes = [ctypes.c_int]
    library.src_strerror.restype = ctypes.c_char_p
    library.src_get_version.argtypes = []
    library.src_get_version.restype = ctypes.c_char_p
    library.src_new.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    library.src_new.restype = ctypes.c_void_p
    library.src_process.argtypes = [ctypes.c_void_p, ctypes.POINTER(_SRCData)]
    library.src_process.restype = ctypes.c_int
    library.src_delete.argtypes = [ctypes.c_void_p]
    library.src_delete.restype = ctypes.c_void_p
    return library


def _check_cancel(cancelled: threading.Event | None) -> None:
    if cancelled is not None and cancelled.is_set():
        raise CaptureCancelled("Audio capture cancelled")


def _samples(data: bytes) -> array.array:
    result = array.array("h")
    result.frombytes(data)
    if sys.byteorder != "little":
        result.byteswap()
    return result


def prepare_asr(pcm: bytes, cancelled: threading.Event | None = None) -> bytes:
    """Low-pass resample complete S16_LE stereo audio using only its left channel.

    Input is bounded to ten seconds. Cancellation is checked around the single
    native resampling call; the C call itself cannot be interrupted by the event.
    No gain normalization, denoising or voice activity decision is applied.
    """
    if not 12 <= len(pcm) <= MAX_SECONDS * RAW_RATE * 4 or len(pcm) % 4:
        raise ValueError("Expected complete stereo PCM16 frames, up to ten seconds")
    _check_cancel(cancelled)
    library = _samplerate()
    left = _samples(pcm)[::2]
    source = (ctypes.c_float * len(left))()
    library.src_short_to_float_array(
        (ctypes.c_short * len(left)).from_buffer(left), source, len(left),
    )
    capacity = math.ceil(len(left) * ASR_RATE / RAW_RATE) + 64
    target = (ctypes.c_float * capacity)()
    data = _SRCData(source, target, len(left), capacity, 0, 0, 1, ASR_RATE / RAW_RATE)
    code = library.src_simple(ctypes.byref(data), SRC_CONVERTER, 1)
    if code:
        detail = library.src_strerror(code)
        raise CaptureError(f"Resampling failed: {detail!r}")
    _check_cancel(cancelled)
    count = data.output_frames_gen
    if data.input_frames_used != len(left) or not 0 < count <= capacity:
        raise CaptureError("Resampler did not consume the complete recording")
    if abs(count - len(left) * ASR_RATE / RAW_RATE) > 1:
        raise CaptureError("Resampled duration differs from the source")
    result = array.array("h", [0]) * count
    library.src_float_to_short_array(target, (ctypes.c_short * count).from_buffer(result), count)
    if sys.byteorder != "little":
        result.byteswap()
    return result.tobytes()


def _stop(process: subprocess.Popen) -> None:
    # Reap our child and kill any descendants still in its private process group.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=1)


def _run(command: list[str], timeout: float, cancelled: threading.Event | None = None,
         stdout: BinaryIO | None = None) -> None:
    _check_cancel(cancelled)
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL,
            stdout=stdout if stdout is not None else subprocess.DEVNULL,
            stderr=errors, start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                _check_cancel(cancelled)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CaptureError(f"{command[0]} timed out after {timeout:g}s")
                try:
                    process.wait(timeout=min(0.05, remaining))
                except subprocess.TimeoutExpired:
                    pass
            _check_cancel(cancelled)
            if process.returncode:
                errors.seek(0)
                detail = errors.read(2048).decode("utf-8", errors="replace").strip()
                raise CaptureError(f"{command[0]} failed ({process.returncode}): {detail}")
        finally:
            _stop(process)


def _check_pcm() -> None:
    for identity in Path("/proc/asound").glob("card*/id"):
        if identity.read_text().strip() == CARD:
            statuses = list(identity.parent.glob("pcm*/sub*/status"))
            if not statuses:
                raise CaptureError("HAT PCM status is unavailable")
            for status in statuses:
                if status.read_text().strip() != "closed":
                    raise CaptureError(f"HAT audio is already in use: {status}", code="device_busy")
            return
    raise CaptureError(f"{CARD} sound card not found", code="device_unavailable")


def _mute(cancelled: threading.Event | None = None) -> None:
    # Attempt both controls even if one fails; neither changes microphone gain.
    failure = None
    for control in (("Line", "mute"), ("PCM", "0%")):
        try:
            _run(["amixer", "-q", "-c", CARD, "sset", *control], 2, cancelled)
        except Exception as exc:
            if failure is None:
                failure = exc
            else:
                failure.add_note(f"Additional mute failure: {exc}")
    if failure is not None:
        raise failure


@contextmanager
def _audio_guard(cancelled: threading.Event | None) -> Iterator[None]:
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
    with os.fdopen(os.open(LOCK_PATH, flags, 0o600), "r+b") as lock:
        info = os.fstat(lock.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise CaptureError("Audio lock must be a regular file owned by this user")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CaptureError("Another Radxa Duck audio operation is running", code="device_busy") from exc
        _check_cancel(cancelled)
        _check_pcm()
        _run(["systemctl", "is-active", "--quiet", "pollen-hat-audio-init.service"], 2, cancelled)
        failure = None
        try:
            _mute(cancelled)
            yield
        except BaseException as exc:
            failure = exc
            raise
        finally:
            try:
                _mute()  # Cleanup deliberately ignores the cancelled event.
            except Exception as exc:
                if failure is None:
                    raise
                failure.add_note(f"Could not restore speaker mute: {exc}")


def _metrics(samples: array.array) -> dict:
    rms = math.sqrt(sum(value * value for value in samples) / len(samples)) / 32768
    return {
        "samples": len(samples), "rms_dbfs": 20 * math.log10(rms) if rms else None,
        "peak": max(abs(value) for value in samples) / 32768,
        "clipped_samples": sum(value in (-32768, 32767) for value in samples),
    }


class _StreamingResampler:
    """Preserve filter history across blocks; discard delayed output on close."""

    def __init__(self) -> None:
        self.library = _samplerate()
        error = ctypes.c_int()
        self.state = self.library.src_new(SRC_CONVERTER, 1, ctypes.byref(error))
        if not self.state:
            raise CaptureError("Cannot initialize streaming audio resampler", code="conversion_failed")

    def process(self, pcm: bytes) -> bytes:
        if self.state is None:
            raise CaptureError("Streaming resampler is closed", code="conversion_failed")
        left = _samples(pcm)[::2]
        source = (ctypes.c_float * len(left))()
        self.library.src_short_to_float_array(
            (ctypes.c_short * len(left)).from_buffer(left), source, len(left),
        )
        capacity = math.ceil(len(left) * ASR_RATE / RAW_RATE) + 64
        target = (ctypes.c_float * capacity)()
        data = _SRCData(source, target, len(left), capacity, 0, 0, 0, ASR_RATE / RAW_RATE)
        code = self.library.src_process(self.state, ctypes.byref(data))
        if code:
            detail = self.library.src_strerror(code)
            raise CaptureError(f"Streaming resampling failed: {detail!r}", code="conversion_failed")
        count = data.output_frames_gen
        if data.input_frames_used != len(left) or not 0 <= count <= capacity:
            raise CaptureError("Streaming resampler did not consume the input", code="conversion_failed")
        output = array.array("h", [0]) * count
        self.library.src_float_to_short_array(
            target, (ctypes.c_short * count).from_buffer(output), count,
        )
        if sys.byteorder != "little":
            output.byteswap()
        return output.tobytes()

    def close(self) -> None:
        if self.state is not None:
            self.library.src_delete(self.state)
            self.state = None


def _pcm_chunks(process: subprocess.Popen, converter: _StreamingResampler,
                cancelled: threading.Event, on_recording: Callable[[], None] | None) -> Iterator[bytes]:
    """Read a bounded block at a time; a stalled or discontinuous stream fails."""
    pcm = bytearray()
    errors = bytearray()
    announced = False
    audio_closed = False
    deadline = time.monotonic() + STREAM_READ_TIMEOUT
    try:
        with selectors.DefaultSelector() as selector:
            for pipe, kind in ((process.stdout, "audio"), (process.stderr, "error")):
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, kind)
            while True:
                _check_cancel(cancelled)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CaptureError("Audio recorder stopped supplying samples", code="timeout")
                for key, _mask in selector.select(timeout=min(0.02, remaining)):
                    amount = min(8192, STREAM_FRAMES * 4 - len(pcm)) if key.data == "audio" else 2048
                    try:
                        data = os.read(key.fd, amount)
                    except BlockingIOError:
                        continue
                    if not data:
                        selector.unregister(key.fileobj)
                        if key.data == "audio":
                            audio_closed = True
                    elif key.data == "audio":
                        pcm.extend(data)
                        deadline = time.monotonic() + STREAM_READ_TIMEOUT
                    else:
                        errors.extend(data[:max(0, 2048 - len(errors))])
                _check_cancel(cancelled)
                # Quiet arecord emits no normal stderr. Fatal XRUN/read errors
                # must not be treated as a continuous recording with missing words.
                if errors:
                    detail = errors.decode("utf-8", errors="replace").strip()
                    raise CaptureError(f"Audio recorder reported an error: {detail}")
                if audio_closed or process.poll() is not None:
                    raise CaptureError("Audio recorder ended unexpectedly")
                if len(pcm) >= 4 and not announced:
                    announced = True
                    if on_recording is not None:
                        on_recording()
                if len(pcm) == STREAM_FRAMES * 4:
                    _check_cancel(cancelled)
                    output = converter.process(bytes(pcm))
                    pcm.clear()
                    _check_cancel(cancelled)
                    if output:
                        yield output
    except (OSError, subprocess.SubprocessError) as exc:
        raise CaptureError(f"Cannot read microphone audio: {exc}") from exc


@contextmanager
def open_pcm_stream(cancelled: threading.Event,
                    on_recording: Callable[[], None] | None = None) -> Iterator[Iterator[bytes]]:
    """Own the HAT while yielding continuous 16 kHz mono PCM16 blocks in memory.

    The first block can be shorter because of filter delay; later blocks are
    about 100 ms. Consume promptly: ALSA uses a 500 ms buffer, pipes are bounded,
    and overruns fail rather than silently dropping audio. The callback runs
    once on the consuming thread after the first raw PCM frame and must not block.
    Closing the context or cancellation discards pending samples and filter tail.
    """
    if not isinstance(cancelled, threading.Event):
        raise CaptureError("cancelled must be a threading.Event", code="invalid_config")
    if on_recording is not None and not callable(on_recording):
        raise CaptureError("on_recording must be callable", code="invalid_config")
    _check_cancel(cancelled)
    converter = _StreamingResampler()
    try:
        with _audio_guard(cancelled):
            _check_cancel(cancelled)
            command = [
                "arecord", "-q", "-D", DEVICE, "-t", "raw", "-f", "S16_LE",
                "-r", str(RAW_RATE), "-c", "2", "--period-time=20000",
                "--buffer-time=500000", "--fatal-errors",
            ]
            try:
                process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    bufsize=0, start_new_session=True, env={**os.environ, "LC_ALL": "C"},
                )
            except OSError as exc:
                raise CaptureError(f"Cannot start audio recorder: {exc}") from exc
            chunks = _pcm_chunks(process, converter, cancelled, on_recording)
            try:
                yield chunks
            finally:
                try:
                    chunks.close()
                finally:
                    try:
                        _stop(process)
                    finally:
                        process.stdout.close()
                        process.stderr.close()
    finally:
        converter.close()


def capture(output_dir: Path, seconds: int = 5,
            cancelled: threading.Event | None = None) -> dict:
    """Capture into a NEW private directory. capture.json marks successful completion.

    Failure/cancellation removes files created by this call. Existing output
    directories are never reused. The API never installs signal handlers.
    """
    if type(seconds) is not int or not 1 <= seconds <= MAX_SECONDS:
        raise ValueError(f"seconds must be an integer from 1 to {MAX_SECONDS}")
    _check_cancel(cancelled)
    library = _samplerate()  # Fail before recording if conversion is unavailable.
    output_dir = Path(os.path.abspath(Path(output_dir).expanduser()))
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(mode=0o700)  # Exclusive reservation; also refuses symlinks.
    created: list[Path] = []
    started = time.monotonic()
    started_utc = datetime.now(timezone.utc).isoformat()
    try:
        with _audio_guard(cancelled), tempfile.TemporaryFile() as recording:
            capture_started = time.monotonic()
            _run([
                "arecord", "-q", "-D", DEVICE, "-t", "raw", "-f", "S16_LE",
                "-r", str(RAW_RATE), "-c", "2", "-s", str(seconds * RAW_RATE),
            ], seconds + 3, cancelled, recording)
            capture_seconds = time.monotonic() - capture_started
            recording.seek(0)
            pcm = recording.read(seconds * RAW_RATE * 4 + 1)
            if len(pcm) != seconds * RAW_RATE * 4:
                raise CaptureError("Recorder returned an incomplete or oversized recording")
        conversion_started = time.monotonic()
        asr = prepare_asr(pcm, cancelled)
        conversion_seconds = time.monotonic() - conversion_started
        stereo = _samples(pcm)
        result = {
            "schema_version": 1, "started_utc": started_utc,
            "device": DEVICE, "requested_seconds": seconds,
            "raw": {"file": "capture.wav", "rate": RAW_RATE, "channels": 2, "format": "PCM16"},
            "asr": {"file": "asr.wav", "rate": ASR_RATE, "channels": 1, "format": "PCM16"},
            "selected_channel": "left", "resampler": "SRC_SINC_MEDIUM_QUALITY",
            "samplerate_version": library.src_get_version().decode(),
            "kernel": platform.release(), "machine": platform.machine(),
            "python": platform.python_version(),
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "capture_seconds": capture_seconds, "conversion_seconds": conversion_seconds,
            "metrics": {"left": _metrics(stereo[::2]), "right": _metrics(stereo[1::2]),
                        "asr": _metrics(_samples(asr))},
        }
        for name, data, rate, channels in (
            ("capture.wav", pcm, RAW_RATE, 2), ("asr.wav", asr, ASR_RATE, 1),
        ):
            _check_cancel(cancelled)
            path = output_dir / name
            with path.open("xb") as handle:
                created.append(path)
                os.fchmod(handle.fileno(), 0o600)
                with wave.open(handle, "wb") as target:
                    target.setnchannels(channels)
                    target.setsampwidth(2)
                    target.setframerate(rate)
                    target.writeframes(data)
        result["total_seconds"] = time.monotonic() - started
        _check_cancel(cancelled)
        path = output_dir / ".capture.json.tmp"
        with path.open("x", encoding="utf-8") as handle:
            created.append(path)
            os.fchmod(handle.fileno(), 0o600)
            json.dump(result, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        _check_cancel(cancelled)
        # Publish a complete metadata file without replacing any existing file.
        published = output_dir / "capture.json"
        os.link(path, published)
        created.append(published)
        path.unlink()
        created.remove(path)
        return result
    except BaseException as exc:
        for path in reversed(created):
            try:
                path.unlink()
            except OSError as cleanup_error:
                exc.add_note(f"Could not remove incomplete output {path}: {cleanup_error}")
        try:
            output_dir.rmdir()
        except OSError as cleanup_error:
            exc.add_note(f"Could not remove incomplete directory: {cleanup_error}")
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_directory", type=Path, help="new directory for private WAVs and metadata")
    parser.add_argument("--seconds", type=int, choices=range(1, MAX_SECONDS + 1), default=5)
    args = parser.parse_args(argv)
    cancelled = threading.Event()
    received_signal = 0

    def cancel(signum: int, _frame: object) -> None:
        nonlocal received_signal
        received_signal = signum
        cancelled.set()

    previous = {sig: signal.signal(sig, cancel) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        result = capture(args.output_directory, args.seconds, cancelled)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except (CaptureError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc), "details": getattr(exc, "__notes__", [])}), file=sys.stderr)
        return 128 + received_signal if received_signal else 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
