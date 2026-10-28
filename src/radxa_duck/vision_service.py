"""One browser-owned camera session with a bounded, in-memory JPEG mailbox.

The worker alone opens the camera, uses the NPU and encodes images. Consumers
receive only the newest complete frame; stopping immediately clears that frame.
Native calls use cooperative cancellation, with ownership retained until cleanup.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from pathlib import Path
import threading
import time
import uuid

from .camera import Camera, CameraConfig, CameraError
from .vision import DEFAULT_MODEL, MODELS, DetectorConfig, VisionError, YoloDetector


MAX_JPEG_BYTES = 256 * 1024
JPEG_QUALITY = 75
MAX_FRAME_AGE = 1.0


class VisionServiceError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, init=False)
class PreviewFrame:
    _header: dict
    jpeg: bytes

    def __init__(self, header: dict, jpeg: bytes):
        object.__setattr__(self, "_header", deepcopy(header))
        object.__setattr__(self, "jpeg", bytes(jpeg))

    @property
    def header(self) -> dict:
        return deepcopy(self._header)


def _encode_jpeg(rgb) -> bytes:
    try:
        import cv2
    except (ImportError, OSError) as exc:
        raise VisionServiceError("dependency_unavailable", "OpenCV is required for camera previews") from exc
    try:
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        success, encoded = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not success or encoded is None:
            raise VisionServiceError("encode_failed", "Cannot encode camera preview")
        return encoded.tobytes()
    except VisionServiceError:
        raise
    except Exception as exc:
        raise VisionServiceError("encode_failed", "Cannot encode camera preview") from exc


def _initialization_cleanup_failed(error: BaseException) -> bool:
    # Native setup may wrap an exception after attaching its cleanup failure.
    # Keep the whole chain alive if any constructor could still own resources.
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if (getattr(current, "code", None) == "cleanup_failed"
                or any("cleanup failed" in str(note).lower() for note in getattr(current, "__notes__", ()))):
            return True
        pending.extend(item for item in (current.__cause__, current.__context__) if item is not None)
    return False


class VisionService:
    def __init__(self, models: dict[str, Path] | None = None, *, device: str = "/dev/video0", rotation: int = 0):
        if models is not None and (not isinstance(models, dict) or any(name not in MODELS for name in models)):
            raise VisionServiceError("invalid_config", "Unknown detector model configuration")
        try:
            self._models = {name: Path(path) for name, path in (models or {}).items()}
            self._camera_config = CameraConfig(device=device, rotation=rotation)
        except (TypeError, ValueError, CameraError) as exc:
            raise VisionServiceError("invalid_config", "Invalid camera or model configuration") from exc
        default_model = DEFAULT_MODEL if DEFAULT_MODEL in self._models else next(iter(self._models), DEFAULT_MODEL)
        self._defaults = {"model": default_model, "rotation": rotation, "detection_enabled": bool(self._models),
                          "max_fps": 10, "score_threshold": 0.35}
        self._settings = dict(self._defaults)
        self._condition = threading.Condition()
        self._state = "idle"
        self._revision = 0
        self._owner = self._session_id = self._cancelled = None
        self._closing = self._fatal = False
        self._suspended = False
        self._error = None
        self._latest = None
        self._captured_at = 0.0
        self._stats = {"frames": 0}
        self._resources: list[object] = []
        # An initializer whose own cleanup failed cannot be reconstructed safely.
        # Keep its traceback alive to retain native resource references.
        self._unsafe_initialization: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="radxa-vision", daemon=True)
        self._thread.start()

    def _changed(self):
        self._revision += 1
        self._condition.notify_all()

    def snapshot(self, owner: str | None = None) -> dict:
        with self._condition:
            return {"type": "state", "schema_version": 1, "revision": self._revision,
                    "state": self._state, "ready": not self._closing and not self._fatal and not self._suspended,
                    "maintenance": self._suspended,
                    "session_id": self._session_id, "owned": owner is not None and owner == self._owner,
                    "settings": dict(self._settings), "stats": dict(self._stats),
                    "error": dict(self._error) if self._error else None,
                    "capabilities": {"transport": "jpeg-websocket-v1", "available_models": sorted(self._models),
                                     "rotations": [0, 90, 180, 270], "default_settings": dict(self._defaults),
                                     "max_fps": 15, "max_jpeg_bytes": MAX_JPEG_BYTES,
                                     "jpeg_quality": JPEG_QUALITY, "capture_size": [640, 480],
                                     "ack_timeout_seconds": 3}}

    def _normalize_settings(self, settings, base):
        if settings is None:
            settings = {}
        if not isinstance(settings, dict) or set(settings) - set(self._defaults):
            raise VisionServiceError("invalid_request", "Unknown camera settings")
        result = {**base, **settings}
        if result["model"] == "":
            result["model"] = self._defaults["model"]
        if not isinstance(result["model"], str):
            raise VisionServiceError("invalid_request", "Detector model must be a string")
        if type(result["rotation"]) is not int or result["rotation"] not in (0, 90, 180, 270):
            raise VisionServiceError("invalid_request", "Rotation must be 0, 90, 180 or 270")
        if type(result["detection_enabled"]) is not bool:
            raise VisionServiceError("invalid_request", "detection_enabled must be a boolean")
        if type(result["max_fps"]) is not int or not 1 <= result["max_fps"] <= 15:
            raise VisionServiceError("invalid_request", "max_fps must be an integer from 1 to 15")
        threshold = result["score_threshold"]
        if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0.05 <= threshold <= 0.95:
            raise VisionServiceError("invalid_request", "score_threshold must be between 0.05 and 0.95")
        if result["detection_enabled"] and result["model"] not in self._models:
            raise VisionServiceError("model_unavailable", "The selected detector has not been configured")
        if result["model"] not in MODELS:
            raise VisionServiceError("invalid_request", "Unknown detector model")
        return result

    def start(self, owner: str, settings: dict | None = None) -> None:
        if not isinstance(owner, str) or not owner:
            raise VisionServiceError("invalid_request", "A connection owner is required")
        with self._condition:
            if self._closing:
                raise VisionServiceError("closed", "Vision service is closing")
            if self._suspended:
                raise VisionServiceError("maintenance", "Device maintenance is in progress")
            if self._fatal:
                raise VisionServiceError("not_ready", "Vision resources have not been released")
            if self._owner is not None:
                if self._owner != owner or self._state not in ("starting", "streaming"):
                    raise VisionServiceError("busy", "A camera session is already active")
                current = self._normalize_settings(settings, self._settings)
                if current != self._settings:
                    raise VisionServiceError("restart_required", "Stop preview before changing its settings")
                return
            self._settings = self._normalize_settings(settings, self._defaults)
            self._owner, self._session_id = owner, uuid.uuid4().hex
            self._cancelled = threading.Event()
            self._latest, self._error = None, None
            self._stats = {"frames": 0}
            self._state = "starting"
            self._changed()

    def stop(self, owner: str) -> None:
        with self._condition:
            if self._owner is None:
                return
            if self._owner != owner:
                raise VisionServiceError("not_owner", "Another connection controls the camera")
            self._cancelled.set()
            self._latest = None
            if not self._fatal:
                self._state = "stopping"
            self._changed()

    def latest_frame(self, owner: str, session_id: str, after_frame_id: int) -> PreviewFrame | None:
        with self._condition:
            if (self._state != "streaming" or owner != self._owner or session_id != self._session_id
                    or self._latest is None or time.monotonic() - self._captured_at > MAX_FRAME_AGE):
                return None
            header = self._latest.header
            if header["frame_id"] <= after_frame_id:
                return None
            header["frame_age_ms"] = (time.monotonic() - self._captured_at) * 1000
            return PreviewFrame(header, self._latest.jpeg)

    def suspend(self, timeout: float = 15) -> None:
        """Block new preview and wait for camera/NPU cleanup before maintenance."""
        with self._condition:
            self._suspended = True
            if self._cancelled is not None:
                self._cancelled.set()
                self._latest = None
                if not self._fatal:
                    self._state = "stopping"
            self._changed()
            if not self._condition.wait_for(lambda: self._owner is None, timeout=timeout) or self._fatal:
                raise VisionServiceError("shutdown_timeout", "Camera resources did not finish stopping")

    def resume(self) -> None:
        with self._condition:
            self._suspended = False
            self._changed()

    @staticmethod
    def _check_cancelled(cancelled):
        if cancelled.is_set():
            raise VisionServiceError("cancelled", "Camera preview cancelled")

    def _cleanup_resources(self):
        retained, failures = [], []
        for resource in reversed(self._resources):
            try:
                resource.close()
            except Exception as exc:
                retained.append(resource)
                failures.append(str(exc))
        self._resources = list(reversed(retained))
        if self._unsafe_initialization is not None:
            failures.append("Model initialization cleanup failed; restart the service process")
        if failures:
            return {"code": "cleanup_failed", "message": "; ".join(failures)[:2048]}
        return None

    def _preview(self, session_id, cancelled, settings):
        failure = None
        detector = None
        try:
            self._check_cancelled(cancelled)
            if settings["detection_enabled"]:
                try:
                    detector = YoloDetector(self._models[settings["model"]], settings["model"],
                                            DetectorConfig(score_threshold=settings["score_threshold"]),
                                            cancelled=cancelled)
                except Exception as exc:
                    if _initialization_cleanup_failed(exc):
                        self._unsafe_initialization = exc
                    raise
                self._resources.append(detector)
            self._check_cancelled(cancelled)
            camera = Camera(CameraConfig(device=self._camera_config.device, rotation=settings["rotation"]),
                            cancelled=cancelled)
            self._resources.append(camera)
            camera.__enter__()
            first_published = None
            while not cancelled.is_set():
                began = time.monotonic()
                frame = camera.read()
                self._check_cancelled(cancelled)
                result = detector.detect(frame) if detector is not None else None
                self._check_cancelled(cancelled)
                encoded_at = time.monotonic()
                jpeg = _encode_jpeg(frame.rgb)
                published_at = time.monotonic()
                self._check_cancelled(cancelled)
                if not isinstance(jpeg, bytes) or not 0 < len(jpeg) <= MAX_JPEG_BYTES:
                    raise VisionServiceError("encode_failed", "Encoded preview exceeds its size limit")
                height, width = frame.rgb.shape[:2]
                header = {"type": "frame", "schema_version": 1, "session_id": session_id,
                          "frame_id": frame.sequence, "width": width, "height": height,
                          "mime_type": "image/jpeg", "image_bytes": len(jpeg), "result": result,
                          "frame_age_ms": (published_at - frame.captured_monotonic) * 1000}
                packet = PreviewFrame(header, jpeg)
                with self._condition:
                    if cancelled.is_set() or self._session_id != session_id:
                        break
                    self._latest, self._captured_at = packet, frame.captured_monotonic
                    frames = self._stats["frames"] + 1
                    if first_published is None:
                        first_published = published_at
                    self._stats = {"frames": frames, "jpeg_bytes": len(jpeg),
                                   "frame_age_ms": header["frame_age_ms"],
                                   "jpeg_encode_ms": (published_at - encoded_at) * 1000,
                                   "inference_call_ms": result["inference_call_ms"] if result else None,
                                   "preview_fps": (frames - 1) / max(published_at - first_published, 1e-9)}
                    self._state = "streaming"
                    self._changed()
                cancelled.wait(max(0, began + 1 / settings["max_fps"] - time.monotonic()))
        except (CameraError, VisionError, VisionServiceError) as exc:
            if exc.code != "cancelled":
                failure = {"code": exc.code, "message": str(exc)}
        except Exception:
            failure = {"code": "internal_error", "message": "Camera preview processing failed"}
        finally:
            cleanup_failure = self._cleanup_resources()
            with self._condition:
                self._latest = None
                if cleanup_failure:
                    self._fatal = True
                    self._error, self._state = cleanup_failure, "error"
                else:
                    self._owner = self._session_id = self._cancelled = None
                    self._error = failure
                    self._state = "error" if failure else "idle"
                self._changed()

    def _shutdown(self):
        failure = self._cleanup_resources()
        with self._condition:
            self._latest = None
            if failure:
                self._error, self._state, self._fatal = failure, "error", True
            else:
                self._owner = self._session_id = self._cancelled = None
                self._state = "closed"
            self._changed()

    def _run(self):
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closing or (self._owner is not None and not self._fatal))
                    if self._closing:
                        break
                    session_id, cancelled, settings = self._session_id, self._cancelled, dict(self._settings)
                self._preview(session_id, cancelled, settings)
        finally:
            self._shutdown()

    def close(self, timeout: float = 30) -> None:
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise VisionServiceError("invalid_config", "Shutdown timeout must be positive and finite")
        with self._condition:
            self._closing = True
            self._latest = None
            if self._cancelled is not None:
                self._cancelled.set()
                if not self._fatal:
                    self._state = "stopping"
            if not self._thread.is_alive() and self._resources:
                self._thread = threading.Thread(target=self._shutdown, name="radxa-vision-cleanup", daemon=True)
                self._thread.start()
            self._changed()
            thread = self._thread
        thread.join(timeout)
        if thread.is_alive():
            raise VisionServiceError("shutdown_timeout", "Vision worker did not finish shutdown")
        with self._condition:
            if self._state != "closed":
                raise VisionServiceError("cleanup_failed", "Vision resources have not been released")
