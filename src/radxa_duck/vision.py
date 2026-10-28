"""Local RK3566 NPU object detection for RGB images and live camera frames.

Requires OpenCV and RKNN Toolkit Lite2 with the board's compatible librknnrt.
Imports and --help do not initialize a model, NPU, or camera. The library never
stores frames, uploads images, or issues movement commands. Native inference
is synchronous: cancellation discards its result after the call returns.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import select
import signal
import stat
import sys
import tempfile
import threading
import time

from .camera import Camera, CameraConfig, CameraError, CameraFrame


INPUT_SIZE = 640
# Keep alternatives selectable for measured workload comparisons.
DEFAULT_MODEL = "yolov8n"
MODEL_ZOO_REVISION = "837db8e992815ff8647eadb709f07067f4bc3a71"
# YOLOv5n, YOLOv8n and YOLO26s ONNX files use the pinned Model Zoo sources.
# YOLO26n uses an ONNX export of the official checkpoint:
# https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n.pt
# Checkpoint SHA256: 9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef
# Export: Ultralytics 8.4.6, torch 2.4.0+cpu; format=onnx, imgsz=640,
# opset=19, batch=1, dynamic=False, simplify=False, device=cpu.
# All RKNN artifacts are converted with Toolkit2 2.3.0 for RK3566.
# INT8 calibration: Model Zoo datasets/COCO/subset (20 images), INTER_LINEAR
# letterbox to 640x640 with centered padding 114; mean=0, std=255, RGB uint8.
# Upstream model projects: airockchip/yolov5 (GPL-3.0) and
# airockchip/ultralytics_yolov8 and ultralytics/ultralytics (AGPL-3.0).
# YOLO26 exports the one2one Sigmoid class scores and Mul_2 xyxy nodes.
# No weights are bundled here.
MODEL_SOURCE = "https://ftrg.zbox.filez.com/v2/delivery/data/95f00b0fc900458ba134f8b180b3f7a1/examples"
MODELS = {
    "yolov5n": (3_046_520, "72e7e6bffdaa9be243997a2c86633971594160292773e60668cc2be024e38941",
                "b3237d0f59a5d90ff6ecec3dca220e0ada8abd929f8a158b08b12e1a3bad7232"),
    "yolov8n": (4_810_315, "c2ec0ee43d49a48ea9cb7bd9401858d4135685a57c7a9a370068310a0d6cd151",
                "0c8716701f471067932b797eeb67c8e5db47c693c2557c881d7679ec12e21bc5"),
    "yolo26n": (4_126_632, "ed8603ba8aa171a7e5e65618a4060cf014cbc2b0fb1067733e7e87e60655c4ab",
                "25de3f8ca06eed0907024f9862f6979b0791d5dbbf52d7551f6ebb15cd988a38"),
    "yolo26s": (11_734_248, "b424b13a4aaba6e3c6219a28ba1da3f5d84c6123115c9f45b2422ddf0c955766",
                "88dbcad6f8e1e70e3c32dd3f86d37ab41ec52e5a65bc638407d44c6bee73bb6c"),
}
LABELS = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
    "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
    "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
    "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
)
ANCHORS = (((10, 13), (16, 30), (33, 23)), ((30, 61), (62, 45), (59, 119)),
           ((116, 90), (156, 198), (373, 326)))
LOCK_PATH = Path(tempfile.gettempdir()) / f"radxa-duck-vision-{os.getuid()}.lock"


class VisionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DetectorConfig:
    score_threshold: float = 0.35
    iou_threshold: float = 0.45
    max_detections: int = 20
    max_candidates: int = 300

    def __post_init__(self):
        for name in ("score_threshold", "iou_threshold"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value < 1:
                raise VisionError("invalid_config", f"{name} must be between 0 and 1, exclusive")
        for name, upper in (("max_detections", 100), ("max_candidates", 1000)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= upper:
                raise VisionError("invalid_config", f"{name} must be an integer from 1 to {upper}")
        if self.max_detections > self.max_candidates:
            raise VisionError("invalid_config", "max_candidates must cover max_detections")


def _dependencies():
    try:
        import numpy as np
        import cv2
        from rknnlite.api import RKNNLite
    except ImportError as exc:
        raise VisionError("dependency_unavailable", "NumPy, OpenCV and RKNN Toolkit Lite2 are required") from exc
    return np, cv2, RKNNLite


def _verify_model(path: Path, model_name: str):
    expected_size, expected_hash, _source_hash = MODELS[model_name]
    try:
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC), "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != expected_size:
                raise VisionError("unsupported_model", "Unexpected RKNN model size or file type")
            data = handle.read(expected_size + 1)
            if len(data) != expected_size or hashlib.sha256(data).hexdigest() != expected_hash:
                raise VisionError("unsupported_model", "RKNN model checksum does not match the selected model")
    except OSError as exc:
        raise VisionError("model_unavailable", f"Cannot read RKNN model: {exc}") from exc


class YoloDetector:
    """One serial NPU detector, holding an exclusive process lock until close.

    Output boxes refer to the supplied RGB image, including any camera rotation.
    inference_call_ms includes RKNN input transfer and output conversion; it is
    not a measurement of only the NPU's kernel execution time.
    """

    def __init__(self, model: Path, model_name: str = DEFAULT_MODEL, config: DetectorConfig | None = None,
                 *, cancelled: threading.Event | None = None):
        if model_name not in MODELS:
            raise VisionError("unsupported_model", f"model_name must be one of {', '.join(MODELS)}")
        self.config = config if config is not None else DetectorConfig()
        if not isinstance(self.config, DetectorConfig):
            raise VisionError("invalid_config", "config must be DetectorConfig")
        if cancelled is not None and not isinstance(cancelled, threading.Event):
            raise VisionError("invalid_config", "cancelled must be a threading.Event")
        self.model_name = model_name
        self.cancelled = cancelled if cancelled is not None else threading.Event()
        self._operation = threading.Lock()
        self._lock = None
        self._runtime = None
        self._closed = False
        self._check_cancelled(None)
        started = time.monotonic()
        try:
            path = Path(model).expanduser().resolve()
            _verify_model(path, model_name)
            self._np, self._cv2, runtime_type = _dependencies()
            self._check_cancelled(None)
            self._acquire_npu()
            self._runtime = runtime_type(verbose=False)
            if self._runtime.load_rknn(str(path)) != 0:
                raise VisionError("model_load_failed", "RKNN could not load the model")
            self._check_cancelled(None)
            if self._runtime.init_runtime() != 0:
                raise VisionError("runtime_unavailable", "RKNN could not initialize the board NPU")
            self._check_cancelled(None)
        except BaseException as exc:
            try:
                self.close()
            except Exception as cleanup:
                exc.add_note(f"NPU cleanup failed: {cleanup}")
            if isinstance(exc, (VisionError, KeyboardInterrupt, SystemExit)):
                raise
            raise VisionError("runtime_unavailable", f"Cannot initialize NPU detector: {exc}") from exc
        self.initialization_ms = (time.monotonic() - started) * 1000

    def _acquire_npu(self):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            self._lock = os.fdopen(fd, "r+b")
            info = os.fstat(self._lock.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise VisionError("runtime_unavailable", "NPU lock must be owned by this user")
            try:
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise VisionError("device_busy", "Another visual detector owns the NPU") from exc
        except OSError as exc:
            raise VisionError("runtime_unavailable", f"Cannot acquire NPU lock: {exc}") from exc

    def _check_cancelled(self, event):
        if self.cancelled.is_set() or (event is not None and event.is_set()):
            raise VisionError("cancelled", "Visual detection cancelled")

    def metadata(self):
        return {"provider": "npu", "backend": "rknn-lite", "model_name": self.model_name,
                "model_sha256": MODELS[self.model_name][1], "precision": "int8",
                "input_size": [INPUT_SIZE, INPUT_SIZE], "initialization_ms": self.initialization_ms}

    def _prepare(self, rgb):
        np = self._np
        if (not isinstance(rgb, np.ndarray) or rgb.dtype != np.uint8 or rgb.ndim != 3
                or rgb.shape[2] != 3 or not 1 <= rgb.shape[0] <= 1920 or not 1 <= rgb.shape[1] <= 1920
                or rgb.shape[0] * rgb.shape[1] > 1920 * 1080):
            raise VisionError("invalid_frame", "Input must be a bounded HWC uint8 RGB image")
        height, width = rgb.shape[:2]
        scale = INPUT_SIZE / max(width, height)
        resized_width, resized_height = max(1, round(width * scale)), max(1, round(height * scale))
        left, top = (INPUT_SIZE - resized_width) // 2, (INPUT_SIZE - resized_height) // 2
        image = np.full((INPUT_SIZE, INPUT_SIZE, 3), 114, dtype=np.uint8)
        image[top:top + resized_height, left:left + resized_width] = self._cv2.resize(
            rgb, (resized_width, resized_height), interpolation=self._cv2.INTER_LINEAR)
        return image, (width, height, resized_width / width, resized_height / height, left, top)

    def _check_outputs(self, outputs):
        np = self._np
        if self.model_name in ("yolo26n", "yolo26s"):
            # Rockchip's truncated one2one graph exports sigmoid class scores
            # followed by decoded xyxy boxes in 640x640 input pixel coordinates.
            expected = [(1, 80, 8400), (1, 4, 8400)]
        else:
            expected = []
            for size in (80, 40, 20):
                channels = (255,) if self.model_name == "yolov5n" else (64, 80, 1)
                expected.extend((1, channel, size, size) for channel in channels)
        if not isinstance(outputs, (list, tuple)) or len(outputs) != len(expected):
            raise VisionError("invalid_output", "Unexpected RKNN output count")
        for output, shape in zip(outputs, expected):
            if (not isinstance(output, np.ndarray) or output.shape != shape
                    or output.dtype.kind != "f" or not np.isfinite(output).all()):
                raise VisionError("invalid_output", "RKNN output shape, dtype or values violate the model contract")

    def _decode(self, outputs):
        np, threshold = self._np, self.config.score_threshold
        self._check_outputs(outputs)
        if self.model_name in ("yolo26n", "yolo26s"):
            probabilities = outputs[0][0]
            confidence = probabilities.max(axis=0).clip(0, 1)
            indices = np.flatnonzero(confidence >= threshold)
            labels = probabilities[:, indices].argmax(axis=0)
            # Own the coordinate array: reversing letterbox must not mutate
            # native runtime outputs or a caller's replayed inference tensors.
            return outputs[1][0][:, indices].T.copy(), confidence[indices], labels
        boxes, scores, classes = [], [], []
        for level, size in enumerate((80, 40, 20)):
            stride = INPUT_SIZE / size
            if self.model_name == "yolov5n":
                prediction = outputs[level].reshape(3, 85, size, size)
                anchor, y, x = np.where(prediction[:, 4] >= threshold)
                values = prediction[anchor, :, y, x].clip(0, 1)
                labels = values[:, 5:].argmax(axis=1)
                confidence = values[:, 4] * values[np.arange(len(labels)), labels + 5]
                selected = confidence >= threshold
                values, anchor, y, x = values[selected], anchor[selected], y[selected], x[selected]
                confidence, labels = confidence[selected], labels[selected]
                centers = (values[:, :2] * 2 - 0.5 + np.stack((x, y), axis=1)) * stride
                sizes = (values[:, 2:4] * 2) ** 2 * np.asarray(ANCHORS[level], dtype=np.float32)[anchor]
                decoded = np.concatenate((centers - sizes / 2, centers + sizes / 2), axis=1)
            else:
                probabilities = outputs[level * 3 + 1][0].reshape(80, -1)
                # Threshold the actual class scores before finding class IDs.
                # This avoids argmax's strided 80-class scan at rejected cells.
                # The separately quantized score_sum is not a safe upper bound.
                confidence = probabilities.max(axis=0).clip(0, 1)
                indices = np.flatnonzero(confidence >= threshold)
                labels = probabilities[:, indices].argmax(axis=0)
                confidence = confidence[indices]
                logits = outputs[level * 3].reshape(4, 16, -1)[:, :, indices]
                logits = logits - logits.max(axis=1, keepdims=True)
                exponent = np.exp(logits)
                distances = (exponent * np.arange(16, dtype=np.float32)[None, :, None]).sum(axis=1) / exponent.sum(axis=1)
                centers = np.stack((indices % size + 0.5, indices // size + 0.5), axis=0)
                decoded = np.concatenate((centers - distances[:2], centers + distances[2:]), axis=0).T * stride
            boxes.append(decoded)
            scores.append(confidence)
            classes.append(labels)
        return np.concatenate(boxes), np.concatenate(scores), np.concatenate(classes)

    def _postprocess(self, outputs, transform):
        np = self._np
        boxes, scores, classes = self._decode(outputs)
        width, height, scale_x, scale_y, left, top = transform
        boxes[:, (0, 2)] = ((boxes[:, (0, 2)] - left) / scale_x).clip(0, width)
        boxes[:, (1, 3)] = ((boxes[:, (1, 3)] - top) / scale_y).clip(0, height)
        valid = np.flatnonzero((boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1]))
        order = valid[np.argsort(-scores[valid], kind="stable")[:self.config.max_candidates]]
        if self.model_name in ("yolo26n", "yolo26s"):
            # The one2one head is trained without NMS; keep its ranked boxes,
            # including overlapping detections of the same class.
            kept = order[:self.config.max_detections]
        else:
            kept = []
            while len(order) and len(kept) < self.config.max_detections:
                current = order[0]
                kept.append(current)
                rest = order[1:]
                top_left = np.maximum(boxes[current, :2], boxes[rest, :2])
                bottom_right = np.minimum(boxes[current, 2:], boxes[rest, 2:])
                overlap = np.prod(np.maximum(0, bottom_right - top_left), axis=1)
                area = np.prod(boxes[current, 2:] - boxes[current, :2])
                other_area = np.prod(boxes[rest, 2:] - boxes[rest, :2], axis=1)
                iou = overlap / np.maximum(area + other_area - overlap, np.finfo(np.float32).tiny)
                order = rest[(classes[rest] != classes[current]) | (iou <= self.config.iou_threshold)]
        return [{"class_id": int(classes[i]), "label": LABELS[int(classes[i])],
                 "score": float(scores[i]), "box_xyxy": [float(value) for value in boxes[i]],
                 "center_normalized": [float((boxes[i, 0] + boxes[i, 2]) / (2 * width)),
                                       float((boxes[i, 1] + boxes[i, 3]) / (2 * height))]}
                for i in kept]

    def detect(self, frame, *, cancelled: threading.Event | None = None):
        if cancelled is not None and not isinstance(cancelled, threading.Event):
            raise VisionError("invalid_config", "cancelled must be a threading.Event")
        if not self._operation.acquire(blocking=False):
            raise VisionError("busy", "Detector is already processing a frame")
        try:
            if self._closed or self._runtime is None:
                raise VisionError("invalid_state", "Detector is closed")
            self._check_cancelled(cancelled)
            started = time.monotonic()
            if isinstance(frame, CameraFrame):
                frame_id, captured_at, rgb = frame.sequence, frame.captured_monotonic, frame.rgb
                if not math.isfinite(captured_at) or not 0 <= captured_at <= started:
                    raise VisionError("invalid_frame", "Invalid capture timestamp")
            else:
                frame_id, captured_at, rgb = None, None, frame
            image, transform = self._prepare(rgb)
            prepared = time.monotonic()
            self._check_cancelled(cancelled)
            try:
                outputs = self._runtime.inference(inputs=[image[None, ...]], data_format=["nhwc"])
            except Exception as exc:
                raise VisionError("inference_failed", f"RKNN inference failed: {exc}") from exc
            inferred = time.monotonic()
            self._check_cancelled(cancelled)
            if outputs is None:
                raise VisionError("inference_failed", "RKNN returned no inference outputs")
            detections = self._postprocess(outputs, transform)
            finished = time.monotonic()
            self._check_cancelled(cancelled)
            return {"schema_version": 1, "type": "detections", **self.metadata(),
                    "frame_id": frame_id, "captured_monotonic": captured_at,
                    "image_size": list(transform[:2]), "detections": detections,
                    "preprocess_ms": (prepared - started) * 1000,
                    "inference_call_ms": (inferred - prepared) * 1000,
                    "postprocess_ms": (finished - inferred) * 1000,
                    "total_ms": (finished - started) * 1000,
                    "frame_age_ms": (finished - captured_at) * 1000 if captured_at is not None else None}
        finally:
            self._operation.release()

    def close(self):
        if not self._operation.acquire(blocking=False):
            raise VisionError("busy", "Wait for current inference before closing the detector")
        try:
            if self._runtime is not None:
                try:
                    self._runtime.release()
                except Exception as exc:
                    raise VisionError("cleanup_failed", "NPU release failed; ownership retained") from exc
                self._runtime = None
            if self._lock is not None:
                self._lock.close()
                self._lock = None
            self._closed = True
        finally:
            self._operation.release()

    def __enter__(self):
        if self._closed:
            raise VisionError("invalid_state", "Detector is closed")
        return self

    def __exit__(self, _kind, exc, _traceback):
        try:
            self.close()
        except Exception as cleanup:
            if exc is None:
                raise
            exc.add_note(f"NPU cleanup failed: {cleanup}")


@contextmanager
def _json_output():
    """CLI-only: reserve stdout for JSON, send native library diagnostics to stderr."""
    sys.stdout.flush()
    original = os.dup(sys.stdout.fileno())
    stream = os.fdopen(os.dup(original), "w", buffering=1)
    blocking = os.get_blocking(original)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    os.set_blocking(original, False)
    try:
        yield stream
    except BrokenPipeError:
        # A downstream JSON consumer closed its pipe. Context owners have
        # already unwound, so capture and NPU resources are no longer in use.
        pass
    finally:
        sys.stdout.flush()
        # RKNN uses C stdio too; flush it before restoring the original fd.
        import ctypes
        ctypes.CDLL(None).fflush(None)
        os.set_blocking(original, blocking)
        os.dup2(original, sys.stdout.fileno())
        os.close(original)
        try:
            stream.close()
        except BrokenPipeError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--model-name", choices=tuple(MODELS), default=DEFAULT_MODEL)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--rotation", type=int, choices=(0, 90, 180, 270), default=0)
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--threshold", type=float, default=0.35)
    parser.add_argument("--warmup-frames", type=int, default=2)
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or not 0 < args.seconds <= 300:
        parser.error("seconds must be greater than 0 and at most 300")
    if not 0 <= args.warmup_frames <= 20:
        parser.error("warmup-frames must be between 0 and 20")
    cancelled = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: cancelled.set())
    started = time.monotonic()
    count, warmup = 0, 0
    totals = {name: 0.0 for name in ("preprocess_ms", "inference_call_ms", "postprocess_ms", "total_ms", "frame_age_ms")}
    maxima = dict(totals)
    status, code = "completed", 0
    metadata = {"provider": "npu", "model_name": args.model_name, "model_sha256": MODELS[args.model_name][1]}
    with _json_output() as output:
        def emit(value):
            if cancelled.is_set() and value["type"] == "detections":
                raise VisionError("cancelled", "Visual detection cancelled")
            pending = memoryview((json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n").encode())
            deadline = time.monotonic() + 1
            while pending:
                try:
                    written = os.write(output.fileno(), pending)
                    if written == 0:
                        raise BrokenPipeError("JSON output closed")
                    pending = pending[written:]
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise BrokenPipeError("JSON output consumer stopped reading")
                    select.select([], [output.fileno()], [], 0.05)
        try:
            config = DetectorConfig(score_threshold=args.threshold)
            with YoloDetector(args.model, args.model_name, config, cancelled=cancelled) as detector:
                metadata = detector.metadata()
                with Camera(CameraConfig(device=args.device, rotation=args.rotation), cancelled=cancelled) as camera:
                    started = time.monotonic()
                    emit({"schema_version": 1, "type": "started", **metadata,
                          "rotation": args.rotation, "warmup_frames": args.warmup_frames})
                    while time.monotonic() - started < args.seconds:
                        result = detector.detect(camera.read())
                        if warmup < args.warmup_frames:
                            warmup += 1
                            continue
                        count += 1
                        for name in totals:
                            totals[name] += result[name]
                            maxima[name] = max(maxima[name], result[name])
                        emit(result)
        except (VisionError, CameraError) as exc:
            status = "cancelled" if exc.code == "cancelled" else "error"
            code = 130 if exc.code == "cancelled" else 1
            emit({"schema_version": 1, "type": "error", **metadata,
                  "error": {"code": exc.code, "message": str(exc)}})
        except BrokenPipeError:
            return 1
        emit({"schema_version": 1, "type": "summary", **metadata, "status": status,
              "frames": count, "warmup_frames": warmup, "elapsed_seconds": time.monotonic() - started,
              "mean_ms": {name: value / count if count else None for name, value in totals.items()},
              "max_ms": maxima, "stored_images": 0})
    return code


if __name__ == "__main__":
    raise SystemExit(main())
