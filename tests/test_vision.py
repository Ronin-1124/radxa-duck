"""Detector geometry, ownership and cancellation tests without an NPU or camera."""

import hashlib
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from radxa_duck import vision
from radxa_duck.camera import CameraFrame


def v5_outputs():
    return [np.zeros((1, 255, size, size), np.float32) for size in (80, 40, 20)]


def v5_box(outputs, *, level=2, anchor=0, x=10, y=10, label=0,
           objectness=0.8, probability=0.75, xy=(0.5, 0.5), wh=(0.5, 0.5)):
    size = outputs[level].shape[-1]
    row = outputs[level].reshape(3, 85, size, size)[anchor, :, y, x]
    row[:5] = [*xy, *wh, objectness]
    row[5 + label] = probability


def v8_outputs():
    return [np.zeros((1, channels, size, size), np.float32)
            for size in (80, 40, 20) for channels in (64, 80, 1)]


def v8_box(outputs, *, level=2, x=10, y=10, label=0, score=0.8,
           distances=(1, 2, 3, 4), magnitude=1000):
    size = outputs[level * 3].shape[-1]
    logits = outputs[level * 3].reshape(4, 16, size, size)
    logits[:, :, y, x] = -magnitude
    for side, distance in enumerate(distances):
        logits[side, distance, y, x] = magnitude
    outputs[level * 3 + 1][0, label, y, x] = score
    # The auxiliary sum output intentionally stays zero: class scores govern
    # detection; it is not another objectness probability to multiply in.


def v26_outputs():
    return [np.zeros((1, 80, 8400), np.float32), np.zeros((1, 4, 8400), np.float32)]


def v26_box(outputs, *, index=0, label=0, score=0.8, box=(304, 272, 432, 464)):
    outputs[0][0, label, index] = score
    outputs[1][0, :, index] = box


class FakeCv2:
    INTER_LINEAR = 1

    @staticmethod
    def resize(image, size, interpolation):
        if interpolation != FakeCv2.INTER_LINEAR:
            raise AssertionError("Unexpected interpolation")
        width, height = size
        rows = np.arange(height) * image.shape[0] // height
        columns = np.arange(width) * image.shape[1] // width
        return image[rows[:, None], columns[None, :]].copy()


class Runtime:
    def __init__(self, outputs):
        self.outputs = outputs
        self.loaded = []
        self.inputs = []
        self.releases = 0
        self.load_status = self.init_status = 0
        self.inference_callback = None
        self.release_failure = False

    def load_rknn(self, path):
        self.loaded.append(path)
        return self.load_status

    def init_runtime(self):
        return self.init_status

    def inference(self, *, inputs, data_format):
        if data_format != ["nhwc"] or len(inputs) != 1:
            raise AssertionError("Unexpected inference interface")
        if inputs[0].shape != (1, 640, 640, 3) or inputs[0].dtype != np.uint8:
            raise AssertionError("Expected a batched NHWC uint8 tensor")
        self.inputs.append(inputs[0])
        if self.inference_callback:
            self.inference_callback()
        return self.outputs

    def release(self):
        self.releases += 1
        if self.release_failure:
            raise RuntimeError("Native release failed")


class DetectorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.detectors = []
        self.runtimes = []
        self.next_runtime = Runtime(v5_outputs())
        self.patch("LOCK_PATH", self.directory / "npu.lock")
        self.patch("_verify_model", lambda *_: None)
        self.patch("_dependencies", lambda: (np, FakeCv2, self.runtime_factory))
        self.addCleanup(self.close_detectors)

    def patch(self, name, value):
        mock = patch.object(vision, name, value)
        mock.start()
        self.addCleanup(mock.stop)

    def runtime_factory(self, *, verbose):
        self.assertFalse(verbose)
        runtime = self.next_runtime
        self.runtimes.append(runtime)
        return runtime

    def detector(self, model_name="yolov5n", **kwargs):
        detector = vision.YoloDetector(self.directory / "model.rknn", model_name, **kwargs)
        self.detectors.append(detector)
        return detector

    def close_detectors(self):
        for runtime in self.runtimes:
            runtime.release_failure = False
        for detector in self.detectors:
            detector.close()

    def assert_code(self, code, action):
        with self.assertRaises(vision.VisionError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)

    def test_v5_coordinates_rgb_letterbox_and_probability_contract(self):
        outputs = self.next_runtime.outputs
        v5_box(outputs)
        detector = self.detector()
        source = np.full((240, 320, 3), (13, 27, 91), dtype=np.uint8)
        capture_time = time.monotonic() - 0.1
        result = detector.detect(CameraFrame(41, source, capture_time, capture_time + 0.01))
        self.assertEqual(result["provider"], "npu")
        self.assertEqual(result["frame_id"], 41)
        self.assertEqual(result["captured_monotonic"], capture_time)
        self.assertEqual(result["image_size"], [320, 240])
        self.assertEqual(len(result["detections"]), 1)
        item = result["detections"][0]
        self.assertEqual((item["class_id"], item["label"]), (0, "person"))
        self.assertAlmostEqual(item["score"], 0.6, places=6)
        np.testing.assert_allclose(item["box_xyxy"], [139, 105.5, 197, 150.5])
        np.testing.assert_allclose(item["center_normalized"], [0.525, 128 / 240])
        submitted = self.next_runtime.inputs[0]
        self.assertEqual(submitted.shape, (1, 640, 640, 3))
        self.assertEqual(submitted.dtype, np.uint8)
        submitted = submitted[0]
        np.testing.assert_array_equal(submitted[0, 0], [114, 114, 114])
        np.testing.assert_array_equal(submitted[80, 0], [13, 27, 91])
        np.testing.assert_array_equal(submitted[559, 639], [13, 27, 91])
        np.testing.assert_array_equal(submitted[560, 0], [114, 114, 114])
        self.assertGreaterEqual(result["frame_age_ms"], 100)
        for name in ("preprocess_ms", "inference_call_ms", "postprocess_ms", "total_ms"):
            self.assertGreaterEqual(result[name], 0)

    def test_portrait_frame_coordinates_follow_supplied_orientation(self):
        v5_box(self.next_runtime.outputs)
        result = self.detector().detect(np.zeros((640, 480, 3), np.uint8))
        self.assertEqual(result["image_size"], [480, 640])
        self.assertIsNone(result["frame_id"])
        self.assertIsNone(result["frame_age_ms"])
        np.testing.assert_allclose(result["detections"][0]["box_xyxy"], [198, 291, 314, 381])

    def test_class_aware_nms_and_detection_limit(self):
        outputs = self.next_runtime.outputs
        v5_box(outputs, level=0, x=20, y=20, xy=(0.75, 0.5), objectness=1, probability=0.8)
        v5_box(outputs, level=0, x=21, y=20, xy=(0.25, 0.5), objectness=1, probability=0.6)
        v5_box(outputs, level=0, anchor=1, x=20, y=20, label=56,
               xy=(0.75, 0.5), wh=((10 / 16) ** 0.5 / 2, (13 / 30) ** 0.5 / 2),
               objectness=1, probability=0.7)
        detector = self.detector()
        source = np.zeros((640, 640, 3), np.uint8)
        detections = detector.detect(source)["detections"]
        self.assertEqual([item["label"] for item in detections], ["person", "chair"])
        np.testing.assert_allclose(detections[0]["box_xyxy"], detections[1]["box_xyxy"], atol=1e-5)
        detector.close()
        limited = self.detector(config=vision.DetectorConfig(max_detections=1, max_candidates=2))
        self.assertEqual([item["label"] for item in limited.detect(source)["detections"]], ["person"])

    def test_v8_dfl_extreme_logits_and_no_extra_sigmoid(self):
        self.next_runtime.outputs = v8_outputs()
        v8_box(self.next_runtime.outputs, magnitude=1e30)
        detector = self.detector("yolov8n")
        detections = detector.detect(np.zeros((240, 320, 3), np.uint8))["detections"]
        self.assertEqual(len(detections), 1)
        self.assertAlmostEqual(detections[0]["score"], 0.8, places=6)
        np.testing.assert_allclose(detections[0]["box_xyxy"], [152, 96, 216, 192], atol=1e-5)

    def test_v26_decoded_boxes_reverse_letterbox_without_mutating_outputs(self):
        for model_name in ("yolo26n", "yolo26s"):
            with self.subTest(model=model_name):
                self.next_runtime.outputs = v26_outputs()
                v26_box(self.next_runtime.outputs, label=56)
                original_boxes = self.next_runtime.outputs[1].copy()
                detector = self.detector(model_name)
                source = np.zeros((240, 320, 3), np.uint8)
                first = detector.detect(source)
                second = detector.detect(source)
                self.assertEqual(first["model_name"], model_name)
                self.assertEqual(first["provider"], "npu")
                self.assertEqual(first["image_size"], [320, 240])
                self.assertEqual(first["detections"], second["detections"])
                self.assertEqual(len(first["detections"]), 1)
                item = first["detections"][0]
                self.assertEqual((item["class_id"], item["label"]), (56, "chair"))
                self.assertAlmostEqual(item["score"], 0.8, places=6)
                np.testing.assert_allclose(item["box_xyxy"], [152, 96, 216, 192])
                np.testing.assert_allclose(item["center_normalized"], [0.575, 0.6])
                np.testing.assert_array_equal(self.next_runtime.outputs[1], original_boxes)
                self.detectors[-1].close()

    def test_v26_preserves_overlapping_one2one_boxes_without_nms(self):
        for model_name in ("yolo26n", "yolo26s"):
            with self.subTest(model=model_name):
                self.next_runtime.outputs = v26_outputs()
                v26_box(self.next_runtime.outputs, index=0, score=0.8)
                v26_box(self.next_runtime.outputs, index=1, score=0.9)
                detector = self.detector(model_name, config=vision.DetectorConfig(iou_threshold=0.01))
                items = detector.detect(np.zeros((640, 640, 3), np.uint8))["detections"]
                self.assertEqual(len(items), 2)
                self.assertEqual([item["class_id"] for item in items], [0, 0])
                np.testing.assert_allclose([item["score"] for item in items], [0.9, 0.8])
                np.testing.assert_array_equal(items[0]["box_xyxy"], items[1]["box_xyxy"])
                self.detectors[-1].close()

    def test_v26_threshold_ties_are_stable_and_detection_count_is_bounded(self):
        for model_name in ("yolo26n", "yolo26s"):
            with self.subTest(model=model_name):
                self.next_runtime.outputs = v26_outputs()
                threshold = np.float32(0.35)
                below = np.nextafter(threshold, np.float32(0))
                above = np.nextafter(threshold, np.float32(1))
                for index, score in ((0, below), (1, threshold), (2, above), (3, above)):
                    v26_box(self.next_runtime.outputs, index=index, label=3, score=score,
                            box=(index * 10, 100, index * 10 + 5, 120))
                # Tied classes resolve to the first ID; tied anchors retain source order.
                self.next_runtime.outputs[0][0, 7, 2] = above
                detector = self.detector(model_name, config=vision.DetectorConfig(max_detections=2, max_candidates=2))
                items = detector.detect(np.zeros((640, 640, 3), np.uint8))["detections"]
                self.assertEqual([item["class_id"] for item in items], [3, 3])
                self.assertEqual([item["score"] for item in items], [float(above), float(above)])
                self.assertEqual([item["box_xyxy"][0] for item in items], [20, 30])
                detector.close()
                detector = self.detector(model_name)
                items = detector.detect(np.zeros((640, 640, 3), np.uint8))["detections"]
                self.assertEqual([item["score"] for item in items], [float(above), float(above), float(threshold)])
                self.detectors[-1].close()

    def test_v26_padding_boxes_are_removed_and_rotated_frame_coordinates_are_clipped(self):
        for model_name in ("yolo26n", "yolo26s"):
            with self.subTest(model=model_name):
                self.next_runtime.outputs = v26_outputs()
                v26_box(self.next_runtime.outputs, index=0, score=0.9, box=(0, 20, 70, 50))
                v26_box(self.next_runtime.outputs, index=1, score=0.8, box=(60, -10, 700, 700))
                v26_box(self.next_runtime.outputs, index=2, score=0.7, box=(300, 100, 200, 200))
                items = self.detector(model_name).detect(np.zeros((640, 480, 3), np.uint8))["detections"]
                self.assertEqual(len(items), 1)
                np.testing.assert_allclose(items[0]["box_xyxy"], [0, 0, 480, 640])
                self.detectors[-1].close()

    def test_v26_output_contract_rejects_wrong_order_count_shapes_dtype_and_nonfinite(self):
        for model_name in ("yolo26n", "yolo26s"):
            with self.subTest(model=model_name):
                detector = self.detector(model_name)
                source = np.zeros((64, 64, 3), np.uint8)
                nan_scores = v26_outputs()
                nan_scores[0][0, 0, 0] = np.nan
                inf_boxes = v26_outputs()
                inf_boxes[1][0, 0, 0] = np.inf
                for outputs in ([], v26_outputs()[:1], list(reversed(v26_outputs())),
                                [np.zeros((1, 80, 8399), np.float32), v26_outputs()[1]],
                                [v26_outputs()[0], np.zeros((1, 4, 80, 105), np.float32)],
                                [np.zeros((1, 80, 8400), np.uint8), v26_outputs()[1]],
                                nan_scores, inf_boxes):
                    with self.subTest(shapes=[getattr(value, "shape", None) for value in outputs]):
                        self.next_runtime.outputs = outputs
                        self.assert_code("invalid_output", lambda: detector.detect(source))
                self.detectors[-1].close()

    def test_empty_detector_outputs_are_successful_empty_detections(self):
        for model_name, outputs in (("yolov5n", v5_outputs()), ("yolov8n", v8_outputs()),
                                    ("yolo26n", v26_outputs()), ("yolo26s", v26_outputs())):
            with self.subTest(model_name=model_name):
                self.next_runtime.outputs = outputs
                detector = self.detector(model_name)
                self.assertEqual(detector.detect(np.zeros((64, 64, 3), np.uint8))["detections"], [])
                detector.close()

    def test_v8_threshold_boundary_and_ties_ignore_auxiliary_rounding(self):
        self.next_runtime.outputs = v8_outputs()
        threshold = np.float32(0.35)
        below = np.nextafter(threshold, np.float32(0))
        above = np.nextafter(threshold, np.float32(1))
        for x, score in ((5, below), (10, threshold), (15, above)):
            v8_box(self.next_runtime.outputs, x=x, score=score,
                   label=3, distances=(1, 1, 1, 1))
        # Equal class scores retain the first class ID. Auxiliary sums are all
        # zero, even for accepted cells, so they cannot decide the threshold.
        self.next_runtime.outputs[7][0, 7, 10, 10] = threshold
        result = self.detector("yolov8n").detect(np.zeros((640, 640, 3), np.uint8))
        self.assertEqual(len(result["detections"]), 2)
        self.assertEqual([item["class_id"] for item in result["detections"]], [3, 3])
        self.assertEqual([item["score"] for item in result["detections"]],
                         [float(above), float(threshold)])

    def test_padding_boxes_are_discarded_and_partial_boxes_are_clipped(self):
        outputs = self.next_runtime.outputs
        v5_box(outputs, level=0, x=20, y=0, objectness=1, probability=0.9)
        v5_box(outputs, level=0, x=20, y=10, objectness=1, probability=0.8)
        detections = self.detector().detect(np.zeros((480, 640, 3), np.uint8))["detections"]
        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0]["box_xyxy"][1], 0)
        self.assertGreater(detections[0]["box_xyxy"][3], 0)

    def test_invalid_output_count_shape_dtype_and_nonfinite_fail(self):
        detector = self.detector()
        source = np.zeros((64, 64, 3), np.uint8)
        bad_nan = v5_outputs()
        bad_nan[0][0, 0, 0, 0] = np.nan
        bad_inf = v5_outputs()
        bad_inf[0][0, 0, 0, 0] = np.inf
        for outputs in ([], v5_outputs()[:2],
                        [np.zeros((1, 255, 79, 80), np.float32), *v5_outputs()[1:]],
                        [np.zeros((1, 255, 80, 80), np.uint8), *v5_outputs()[1:]],
                        bad_nan, bad_inf):
            with self.subTest(kind=type(outputs).__name__):
                self.next_runtime.outputs = outputs
                self.assert_code("invalid_output", lambda: detector.detect(source))
        detector.close()
        self.next_runtime.outputs = v8_outputs()
        self.next_runtime.outputs[0][0, 0, 0, 0] = np.nan
        detector = self.detector("yolov8n")
        self.assert_code("invalid_output", lambda: detector.detect(source))

    def test_native_failure_is_classified_and_does_not_poison_session(self):
        detector = self.detector()
        source = np.zeros((64, 64, 3), np.uint8)
        self.next_runtime.outputs = None
        self.assert_code("inference_failed", lambda: detector.detect(source))
        self.next_runtime.outputs = v5_outputs()
        def failed_call():
            raise RuntimeError("Native inference failed")
        self.next_runtime.inference_callback = failed_call
        self.assert_code("inference_failed", lambda: detector.detect(source))
        self.next_runtime.inference_callback = None
        self.assertEqual(detector.detect(source)["detections"], [])
        detector.close()
        self.assertEqual(self.next_runtime.releases, 1)

    def test_cancel_before_and_after_native_inference_returns_no_result(self):
        event = threading.Event()
        detector = self.detector()
        source = np.zeros((64, 64, 3), np.uint8)
        event.set()
        self.assert_code("cancelled", lambda: detector.detect(source, cancelled=event))
        self.assertEqual(self.next_runtime.inputs, [])
        event.clear()
        self.next_runtime.inference_callback = event.set
        self.assert_code("cancelled", lambda: detector.detect(source, cancelled=event))
        self.assertEqual(len(self.next_runtime.inputs), 1)
        self.next_runtime.inference_callback = None
        event.clear()
        self.assertEqual(detector.detect(source, cancelled=event)["detections"], [])

    def test_cancel_during_initialization_releases_runtime_and_lock(self):
        event = threading.Event()
        def load(_path):
            event.set()
            return 0
        self.next_runtime.load_rknn = load
        self.assert_code("cancelled", lambda: self.detector(cancelled=event))
        self.assertEqual(self.next_runtime.releases, 1)
        self.next_runtime = Runtime(v5_outputs())
        self.detector().close()

    def test_initialization_errors_release_runtime_and_allow_retry(self):
        for setting, code in (("load_status", "model_load_failed"),
                              ("init_status", "runtime_unavailable")):
            with self.subTest(setting=setting):
                self.next_runtime = Runtime(v5_outputs())
                setattr(self.next_runtime, setting, -1)
                self.assert_code(code, self.detector)
                self.assertEqual(self.next_runtime.releases, 1)
                self.next_runtime = Runtime(v5_outputs())
                self.detector().close()

    def test_npu_lock_blocks_another_detector_and_close_releases_it(self):
        first = self.detector()
        self.assert_code("device_busy", self.detector)
        self.assertEqual(len(self.runtimes), 1)
        first.close()
        first.close()
        self.assertEqual(self.next_runtime.releases, 1)
        self.next_runtime = Runtime(v5_outputs())
        self.detector().close()

    def test_release_failure_retains_ownership_until_successful_retry(self):
        detector = self.detector()
        runtime = self.next_runtime
        runtime.release_failure = True
        self.assert_code("cleanup_failed", detector.close)
        self.assert_code("device_busy", self.detector)
        runtime.release_failure = False
        detector.close()
        self.next_runtime = Runtime(v5_outputs())
        self.detector().close()

    def test_serial_inference_and_close_do_not_race_native_call(self):
        entered, finish = threading.Event(), threading.Event()
        detector = self.detector()
        source = np.zeros((64, 64, 3), np.uint8)
        outcomes = []
        def inference():
            entered.set()
            if not finish.wait(2):
                raise AssertionError("Blocked inference was not released")
        def detect():
            try:
                outcomes.append(detector.detect(source))
            except BaseException as exc:
                outcomes.append(exc)
        self.next_runtime.inference_callback = inference
        worker = threading.Thread(target=detect)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assert_code("busy", lambda: detector.detect(source))
            self.assert_code("busy", detector.close)
            self.assertEqual(self.next_runtime.releases, 0)
        finally:
            finish.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], dict)

    def test_invalid_frames_fail_before_native_inference(self):
        detector = self.detector()
        for frame in (None, np.zeros((64, 64), np.uint8), np.zeros((64, 64, 4), np.uint8),
                      np.zeros((64, 64, 3), np.float32), np.zeros((0, 64, 3), np.uint8),
                      np.zeros((1921, 1, 3), np.uint8),
                      CameraFrame(1, np.zeros((64, 64, 3), np.uint8), float("nan"), 0),
                      CameraFrame(1, np.zeros((64, 64, 3), np.uint8), time.monotonic() + 30, 0)):
            self.assert_code("invalid_frame", lambda: detector.detect(frame))
        self.assertEqual(self.next_runtime.inputs, [])
        detector.close()
        self.assert_code("invalid_state", lambda: detector.detect(np.zeros((64, 64, 3), np.uint8)))

    def test_missing_dependency_does_not_acquire_npu_or_fall_back_to_cpu(self):
        def unavailable():
            raise vision.VisionError("dependency_unavailable", "RKNNLite absent")
        with patch.object(vision, "_dependencies", unavailable):
            self.assert_code("dependency_unavailable", self.detector)
        self.assertEqual(self.runtimes, [])
        self.assertFalse(vision.LOCK_PATH.exists())


class ModelAndConfigTests(unittest.TestCase):
    def assert_code(self, code, action):
        with self.assertRaises(vision.VisionError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)

    def test_config_rejects_nonfinite_bool_and_unbounded_counts(self):
        for config in ({"score_threshold": True}, {"score_threshold": float("nan")},
                       {"score_threshold": 0}, {"iou_threshold": 1},
                       {"iou_threshold": float("inf")}, {"max_detections": True},
                       {"max_detections": 101}, {"max_candidates": 1001},
                       {"max_detections": 20, "max_candidates": 10}):
            self.assert_code("invalid_config", lambda: vision.DetectorConfig(**config))

    def test_model_file_size_digest_type_and_missing_path(self):
        data = b"only a synthetic model for checksum validation"
        expected = (len(data), hashlib.sha256(data).hexdigest(), "unused-source-hash")
        with tempfile.TemporaryDirectory() as directory, patch.object(vision, "MODELS", {"test": expected}):
            path = Path(directory) / "model.rknn"
            self.assert_code("model_unavailable", lambda: vision._verify_model(path, "test"))
            path.write_bytes(data)
            vision._verify_model(path, "test")
            path.write_bytes(data + b"extra")
            self.assert_code("unsupported_model", lambda: vision._verify_model(path, "test"))
            path.write_bytes(bytes(len(data)))
            self.assert_code("unsupported_model", lambda: vision._verify_model(path, "test"))
            pipe = Path(directory) / "fifo"
            os.mkfifo(pipe)
            self.assert_code("unsupported_model", lambda: vision._verify_model(pipe, "test"))


if __name__ == "__main__":
    unittest.main()
