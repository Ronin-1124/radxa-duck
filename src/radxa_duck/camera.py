"""Bounded RGB camera capture using the board's GStreamer ISP pipeline.

Requires PyGObject, Gst/GstVideo 1.0 introspection, and GStreamer's v4l2src,
videoconvert, videoflip and appsink plugins. No camera is opened on import.
Frames stay in memory; the appsink drops old frames while a consumer is busy.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import stat
import tempfile
import threading
import time
from typing import Any


class CameraError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CameraConfig:
    device: str = "/dev/video0"
    width: int = 640
    height: int = 480
    rotation: int = 0
    frame_timeout: float = 3.0
    max_frame_age: float = 0.5

    def __post_init__(self):
        if not isinstance(self.device, str) or not self.device.startswith("/dev/"):
            raise CameraError("invalid_config", "device must be a path under /dev")
        for name, lower, upper in (("width", 160, 1920), ("height", 120, 1080)):
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper or value % 8:
                raise CameraError("invalid_config", f"{name} must be a multiple of 8 between {lower} and {upper}")
        if type(self.rotation) is not int or self.rotation not in (0, 90, 180, 270):
            raise CameraError("invalid_config", "rotation must be 0, 90, 180 or 270 degrees clockwise")
        for name, lower, upper in (("frame_timeout", 0.1, 10), ("max_frame_age", 0.05, 2)):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
                raise CameraError("invalid_config", f"{name} must be between {lower} and {upper} seconds")


@dataclass(frozen=True)
class CameraFrame:
    sequence: int
    rgb: Any
    captured_monotonic: float
    received_monotonic: float


class Camera:
    """Single-consumer context manager; read() returns a recent, owned RGB array.

    captured_monotonic is derived from the V4L2/GStreamer buffer timestamp,
    not a measurement of the sensor's exposure start. Cancellation is checked
    at most every 50 ms while waiting for a sample. Use on a worker thread.
    """

    def __init__(self, config: CameraConfig | None = None, *, cancelled: threading.Event | None = None):
        self.config = config if config is not None else CameraConfig()
        if not isinstance(self.config, CameraConfig):
            raise CameraError("invalid_config", "config must be CameraConfig")
        if cancelled is not None and not isinstance(cancelled, threading.Event):
            raise CameraError("invalid_config", "cancelled must be a threading.Event")
        self.cancelled = cancelled if cancelled is not None else threading.Event()
        self._lock = None
        self._pipeline = None
        self._sink = None
        self._bus = None
        self._gst = self._video = self._numpy = None
        self._sequence = 0
        self._closed = False

    def _check_cancelled(self):
        if self.cancelled.is_set():
            raise CameraError("cancelled", "Camera capture cancelled")

    def __enter__(self):
        if self._pipeline is not None or self._closed:
            raise CameraError("invalid_state", "Camera contexts cannot be reopened")
        self._check_cancelled()
        try:
            import gi
            import numpy as np
            gi.require_version("Gst", "1.0")
            gi.require_version("GstVideo", "1.0")
            from gi.repository import Gst, GstVideo
        except (ImportError, ValueError) as exc:
            raise CameraError("dependency_unavailable", "GStreamer Python bindings and NumPy are required") from exc
        self._gst, self._video, self._numpy = Gst, GstVideo, np
        Gst.init(None)
        try:
            self._acquire_device()
            self._build_pipeline()
            self._check_cancelled()
            result = self._pipeline.set_state(Gst.State.PLAYING)
            if result == Gst.StateChangeReturn.FAILURE:
                self._check_bus()
                raise CameraError("capture_failed", "Camera pipeline could not start")
            return self
        except BaseException as exc:
            try:
                self.close()
            except Exception as cleanup:
                exc.add_note(f"Camera cleanup failed: {cleanup}")
            raise

    def _acquire_device(self):
        try:
            device = Path(self.config.device).resolve(strict=True)
            info = device.stat()
            if not stat.S_ISCHR(info.st_mode):
                raise CameraError("device_unavailable", "Camera must be a character device")
            name = f"radxa-duck-camera-{os.getuid()}-{os.major(info.st_rdev)}-{os.minor(info.st_rdev)}.lock"
            fd = os.open(Path(tempfile.gettempdir()) / name,
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            self._lock = os.fdopen(fd, "r+b")
            lock_info = os.fstat(self._lock.fileno())
            if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.getuid():
                raise CameraError("device_unavailable", "Camera lock must be owned by this user")
            try:
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CameraError("device_busy", "Another camera session is active") from exc
        except OSError as exc:
            raise CameraError("device_unavailable", f"Cannot open camera device: {exc}") from exc

    def _build_pipeline(self):
        Gst = self._gst
        self._pipeline = Gst.Pipeline.new(None)
        elements = []
        for factory in ("v4l2src", "capsfilter", "queue", "videoflip", "videoconvert", "capsfilter", "appsink"):
            element = Gst.ElementFactory.make(factory, None)
            if element is None:
                raise CameraError("dependency_unavailable", f"Missing GStreamer plugin: {factory}")
            self._pipeline.add(element)
            elements.append(element)
        source, raw_caps, queue, flip, converter, rgb_caps, sink = elements
        source.set_property("device", self.config.device)
        source.set_property("io-mode", 4)
        raw_caps.set_property("caps", Gst.Caps.from_string(
            f"video/x-raw,format=NV12,width={self.config.width},height={self.config.height}"))
        queue.set_property("max-size-buffers", 1)
        queue.set_property("max-size-bytes", 0)
        queue.set_property("max-size-time", 0)
        queue.set_property("leaky", 2)  # Downstream: discard the oldest frame.
        flip.set_property("method", {0: 0, 90: 1, 180: 2, 270: 3}[self.config.rotation])
        rgb_caps.set_property("caps", Gst.Caps.from_string("video/x-raw,format=RGB"))
        sink.set_property("max-buffers", 1)
        sink.set_property("drop", True)
        sink.set_property("sync", False)
        sink.set_property("emit-signals", False)
        sink.set_property("enable-last-sample", False)
        sink.set_property("wait-on-eos", False)
        for left, right in zip(elements, elements[1:]):
            if not left.link(right):
                raise CameraError("capture_failed", "Cannot link camera pipeline")
        self._sink = sink
        self._bus = self._pipeline.get_bus()

    def _check_bus(self):
        Gst = self._gst
        message = self._bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if message is None:
            return
        if message.type == Gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            raise CameraError("capture_failed", str(error.message)[:2048])
        raise CameraError("capture_ended", "Camera stream ended")

    def read(self) -> CameraFrame:
        if self._pipeline is None:
            raise CameraError("invalid_state", "Camera context is not open")
        Gst = self._gst
        deadline = time.monotonic() + self.config.frame_timeout
        while True:
            self._check_cancelled()
            self._check_bus()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CameraError("timeout", "Camera stopped supplying recent frames")
            sample = self._sink.emit("try-pull-sample", int(min(remaining, 0.05) * Gst.SECOND))
            if sample is None:
                continue
            self._check_cancelled()
            buffer = sample.get_buffer()
            clock = self._pipeline.get_clock()
            if buffer.pts == Gst.CLOCK_TIME_NONE or clock is None:
                raise CameraError("invalid_frame", "Camera frame has no capture timestamp")
            running_time = sample.get_segment().to_running_time(Gst.Format.TIME, buffer.pts)
            if running_time == Gst.CLOCK_TIME_NONE:
                raise CameraError("invalid_frame", "Camera frame timestamp is outside its segment")
            age = max(0.0, (clock.get_time() - self._pipeline.get_base_time() - running_time) / Gst.SECOND)
            if age > self.config.max_frame_age:
                continue
            received = time.monotonic()
            info = self._video.VideoInfo.new_from_caps(sample.get_caps())
            width, height = (self.config.width, self.config.height)
            if self.config.rotation in (90, 270):
                width, height = height, width
            if (info.width, info.height) != (width, height) or info.stride[0] < width * 3:
                raise CameraError("invalid_frame", "Unexpected camera frame layout")
            mapped, data = buffer.map(Gst.MapFlags.READ)
            if not mapped:
                raise CameraError("invalid_frame", "Cannot map camera frame")
            try:
                if len(data.data) < info.offset[0] + info.stride[0] * height:
                    raise CameraError("invalid_frame", "Incomplete camera frame")
                rgb = self._numpy.ndarray((height, width, 3), dtype=self._numpy.uint8,
                                          buffer=data.data, offset=info.offset[0],
                                          strides=(info.stride[0], 3, 1)).copy()
            finally:
                buffer.unmap(data)
            rgb.flags.writeable = False
            self._sequence += 1
            sequence = int(buffer.offset) if buffer.offset != Gst.BUFFER_OFFSET_NONE else self._sequence
            self._check_cancelled()
            return CameraFrame(sequence, rgb, received - age, received)

    def close(self):
        if self._pipeline is not None:
            result = self._pipeline.set_state(self._gst.State.NULL)
            if result == self._gst.StateChangeReturn.FAILURE:
                raise CameraError("cleanup_failed", "Camera pipeline could not stop; ownership retained")
            _result, state, _pending = self._pipeline.get_state(2 * self._gst.SECOND)
            if state != self._gst.State.NULL:
                raise CameraError("cleanup_failed", "Camera has not stopped; retry close before reopening")
        self._sink = self._bus = self._pipeline = None
        if self._lock is not None:
            self._lock.close()
            self._lock = None
        self._closed = True

    def __exit__(self, _kind, exc, _traceback):
        try:
            self.close()
        except Exception as cleanup:
            if exc is None:
                raise
            exc.add_note(f"Camera cleanup failed: {cleanup}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--rotation", type=int, choices=(0, 90, 180, 270), default=0)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or not 0 < args.seconds <= 300:
        parser.error("seconds must be greater than 0 and at most 300")
    cancelled = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: cancelled.set())
    count, ages = 0, []
    started = time.monotonic()
    try:
        with Camera(CameraConfig(device=args.device, rotation=args.rotation), cancelled=cancelled) as camera:
            while time.monotonic() - started < args.seconds:
                frame = camera.read()
                count += 1
                ages.append((frame.received_monotonic - frame.captured_monotonic) * 1000)
        print(json.dumps({"schema_version": 1, "frames": count,
                          "elapsed_seconds": time.monotonic() - started,
                          "max_frame_age_ms": max(ages, default=0), "stored_images": 0}))
        return 0
    except CameraError as exc:
        print(json.dumps({"schema_version": 1, "error": {"code": exc.code, "message": str(exc)}}))
        return 130 if exc.code == "cancelled" else 1


if __name__ == "__main__":
    raise SystemExit(main())
