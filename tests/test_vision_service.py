"""Camera preview lifecycle tests with controlled hardware and encoder doubles."""

from collections import deque
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import Mock, patch

from radxa_duck import vision_service
from radxa_duck.camera import CameraError, CameraFrame
from radxa_duck.vision import VisionError


WAIT = 3
JPEG = b"\xff\xd8private camera image\xff\xd9"
MODELS = {"yolov8n": Path("models/yolov8n.rknn")}


class Gate:
    def __init__(self, opened=True):
        self.entered = threading.Event()
        self.release = threading.Event()
        if opened:
            self.release.set()

    def wait(self):
        self.entered.set()
        if not self.release.wait(WAIT):
            raise AssertionError("Test gate was not released")


class Session:
    def __init__(self, harness, frames=1):
        self.load = harness.gate()
        self.open = harness.gate()
        self.camera_cleanup = harness.gate()
        self.detector_cleanup = harness.gate()
        self.read = [harness.gate() for _ in range(frames)]
        self.detect = [harness.gate() for _ in range(frames)]
        self.encode = [harness.gate() for _ in range(frames)]
        self.load_error = self.open_error = self.read_error = None
        self.detect_error = self.encode_error = None
        self.camera_close_failures = self.detector_close_failures = 0
        self.camera_close_calls = self.detector_close_calls = 0
        self.camera_closed = self.detector_closed = False
        self.detected = self.encoded = 0
        self.age = 0
        self.jpeg = JPEG


class Harness:
    def __init__(self):
        self.gates = []
        self.sessions = deque()
        self.all_sessions = []
        self.camera_calls = []
        self.detector_calls = []
        self.worker_threads = []

    def gate(self, opened=True):
        gate = Gate(opened)
        self.gates.append(gate)
        return gate

    def session(self, frames=1):
        session = Session(self, frames)
        self.sessions.append(session)
        self.all_sessions.append(session)
        return session

    def make_camera(self, config, *, cancelled):
        session = self.sessions.popleft()
        self.camera_calls.append(config)
        self.worker_threads.append(threading.current_thread())

        class Camera:
            index = 0

            def __enter__(self):
                session.open.wait()
                if session.open_error:
                    raise session.open_error
                return self

            def read(self):
                if self.index >= len(session.read):
                    if session.read_error:
                        raise session.read_error
                    if not cancelled.wait(WAIT):
                        raise AssertionError("Test session was not stopped")
                    raise CameraError("cancelled", "Cancelled")
                index = self.index
                self.index += 1
                session.read[index].wait()
                shape = (640, 480, 3) if config.rotation in (90, 270) else (480, 640, 3)
                rgb = SimpleNamespace(shape=shape, session=session, index=index)
                now = time.monotonic()
                return CameraFrame(index + 1, rgb, now - session.age, now)

            def close(self):
                session.camera_close_calls += 1
                session.camera_cleanup.wait()
                if session.camera_close_failures:
                    session.camera_close_failures -= 1
                    raise CameraError("pipeline_error", "Camera cleanup failed")
                session.camera_closed = True

        return Camera()

    def make_detector(self, path, name, config, *, cancelled):
        session = self.sessions[0]
        self.detector_calls.append((path, name, config))
        self.worker_threads.append(threading.current_thread())
        session.load.wait()
        if session.load_error:
            raise session.load_error

        class Detector:
            def detect(self, frame):
                session.detected += 1
                session.detect[frame.rgb.index].wait()
                if session.detect_error:
                    raise session.detect_error
                return {"type": "detections", "frame_id": frame.sequence,
                        "inference_call_ms": 8, "detections": [{"label": "person"}]}

            def close(self):
                session.detector_close_calls += 1
                session.detector_cleanup.wait()
                if session.detector_close_failures:
                    session.detector_close_failures -= 1
                    raise VisionError("runtime_error", "NPU cleanup failed")
                session.detector_closed = True

        return Detector()

    def encode(self, rgb):
        self.worker_threads.append(threading.current_thread())
        rgb.session.encoded += 1
        rgb.session.encode[rgb.index].wait()
        if rgb.session.encode_error:
            raise rgb.session.encode_error
        return rgb.session.jpeg


class VisionServiceTests(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()
        self.services = []
        self.unsafe_services = set()
        for name, replacement in (("Camera", self.harness.make_camera),
                                  ("YoloDetector", self.harness.make_detector),
                                  ("_encode_jpeg", self.harness.encode)):
            mock = patch.object(vision_service, name, replacement)
            mock.start()
            self.addCleanup(mock.stop)
        self.addCleanup(self.cleanup_services)

    def cleanup_services(self):
        for gate in self.harness.gates:
            gate.release.set()
        for session in self.harness.all_sessions:
            session.camera_close_failures = session.detector_close_failures = 0
        for service in self.services:
            try:
                service.close(timeout=WAIT)
            except vision_service.VisionServiceError:
                if service not in self.unsafe_services:
                    raise

    def service(self, models=MODELS, **kwargs):
        service = vision_service.VisionService(models, **kwargs)
        self.services.append(service)
        return service

    def wait(self, service, predicate):
        with service._condition:
            reached = service._condition.wait_for(lambda: predicate(service.snapshot("owner")), WAIT)
            state = service.snapshot("owner")
        self.assertTrue(reached, state)
        return state

    def streaming(self, service, frames=1):
        return self.wait(service, lambda state: state["state"] == "streaming" and state["stats"]["frames"] >= frames)

    def assert_code(self, code, action):
        with self.assertRaises(vision_service.VisionServiceError) as result:
            action()
        self.assertEqual(result.exception.code, code)

    def test_construction_is_lazy_and_capabilities_are_isolated(self):
        service = self.service()
        state = service.snapshot()
        self.assertEqual(state["state"], "idle")
        self.assertTrue(state["ready"])
        self.assertEqual(self.harness.camera_calls, [])
        self.assertEqual(self.harness.detector_calls, [])
        self.assertEqual(state["capabilities"]["transport"], "jpeg-websocket-v1")
        self.assertEqual(state["capabilities"]["capture_size"], [640, 480])
        self.assertEqual(state["capabilities"]["max_jpeg_bytes"], 256 * 1024)
        state["settings"]["rotation"] = 90
        state["capabilities"]["default_settings"]["rotation"] = 270
        self.assertEqual(service.snapshot()["settings"]["rotation"], 0)
        self.assertEqual(service.snapshot()["capabilities"]["default_settings"]["rotation"], 0)

    def test_pure_preview_uses_no_detector_and_runs_hardware_in_worker(self):
        session = self.harness.session()
        service = self.service(models=None)
        service.start("owner", {"rotation": 90})
        state = self.streaming(service)
        packet = service.latest_frame("owner", state["session_id"], 0)
        self.assertEqual(packet.jpeg, JPEG)
        self.assertEqual((packet.header["width"], packet.header["height"]), (480, 640))
        self.assertIsNone(packet.header["result"])
        self.assertFalse(state["settings"]["detection_enabled"])
        self.assertEqual(self.harness.detector_calls, [])
        self.assertTrue(all(thread.daemon and thread is not threading.current_thread()
                            for thread in self.harness.worker_threads))
        service.stop("owner")
        self.wait(service, lambda state: state["state"] == "idle")
        self.assertTrue(session.camera_closed)

    def test_configured_detector_can_be_disabled_and_model_default_is_selected(self):
        self.harness.session()
        service = self.service(models={"yolov5n": Path("v5.rknn"), **MODELS})
        self.assertEqual(service.snapshot()["settings"]["model"], "yolov8n")
        service.start("owner", {"model": "", "detection_enabled": False})
        self.streaming(service)
        self.assertEqual(self.harness.detector_calls, [])

    def test_settings_are_validated_before_opening_hardware(self):
        service = self.service()
        bad = [{"extra": 1}, [], {"model": None}, {"rotation": True}, {"rotation": 45},
               {"detection_enabled": 1}, {"max_fps": True}, {"max_fps": 0},
               {"max_fps": 16}, {"max_fps": 1.5}, {"score_threshold": True},
               {"score_threshold": float("nan")}, {"score_threshold": float("inf")},
               {"score_threshold": 0.01}, {"score_threshold": 0.96}]
        for settings in bad:
            with self.subTest(settings=settings):
                self.assert_code("invalid_request", lambda: service.start("owner", settings))
        self.assert_code("model_unavailable", lambda: service.start("owner", {"model": "yolov5n"}))
        self.assertEqual(self.harness.camera_calls, [])
        self.assertEqual(self.harness.detector_calls, [])

    def test_same_owner_start_is_idempotent_and_other_owner_is_rejected(self):
        session = self.harness.session()
        session.load.release.clear()
        service = self.service()
        service.start("owner", {"score_threshold": 0.5})
        self.assertTrue(session.load.entered.wait(WAIT))
        starting = service.snapshot("owner")
        service.start("owner")
        service.start("owner", {"score_threshold": 0.5})
        self.assertEqual(service.snapshot("owner"), starting)
        self.assert_code("restart_required", lambda: service.start("owner", {"rotation": 90}))
        self.assert_code("busy", lambda: service.start("other"))
        self.assert_code("not_owner", lambda: service.stop("other"))
        self.assertTrue(starting["owned"])
        self.assertFalse(service.snapshot("other")["owned"])
        session.load.release.set()
        state = self.streaming(service)
        service.start("owner")
        self.assertEqual(service.snapshot("owner")["session_id"], state["session_id"])
        self.assertEqual(len(self.harness.detector_calls), 1)
        self.assertEqual(self.harness.detector_calls[0][2].score_threshold, 0.5)

    def test_latest_frame_overwrites_old_and_cannot_cross_owner_or_session(self):
        session = self.harness.session(frames=2)
        session.read[1].release.clear()
        service = self.service()
        service.start("owner")
        state = self.streaming(service)
        sid = state["session_id"]
        first = service.latest_frame("owner", sid, 0)
        self.assertEqual(first.header["frame_id"], 1)
        self.assertIsNone(service.latest_frame("other", sid, 0))
        self.assertIsNone(service.latest_frame("owner", "old-session", 0))
        self.assertIsNone(service.latest_frame("owner", sid, 1))
        session.read[1].release.set()
        self.streaming(service, frames=2)
        self.assertEqual(service.latest_frame("owner", sid, 0).header["frame_id"], 2)
        self.assertIsNone(service.latest_frame("owner", sid, 2))
        self.assertEqual(first.header["frame_id"], 1)
        service.stop("owner")
        self.assertIsNone(service.latest_frame("owner", sid, 0))
        self.wait(service, lambda state: state["state"] == "idle")
        self.harness.session()
        service.start("owner")
        new = self.streaming(service)
        self.assertNotEqual(new["session_id"], sid)
        self.assertEqual(new["stats"]["frames"], 1)
        self.assertIsNone(service.latest_frame("owner", sid, 0))
        self.assertEqual(service.latest_frame("owner", new["session_id"], 0).header["frame_id"], 1)

    def test_packet_is_immutable_and_state_never_contains_image_or_detections(self):
        self.harness.session()
        service = self.service()
        service.start("owner")
        state = self.streaming(service)
        packet = service.latest_frame("owner", state["session_id"], 0)
        header = packet.header
        self.assertEqual(set(header), {"schema_version", "type", "session_id", "frame_id", "width", "height",
                                       "mime_type", "image_bytes", "result", "frame_age_ms"})
        self.assertEqual(header["image_bytes"], len(packet.jpeg))
        self.assertEqual(header["mime_type"], "image/jpeg")
        header["result"]["detections"][0]["label"] = "tampered"
        self.assertEqual(packet.header["result"]["detections"][0]["label"], "person")
        self.assertEqual(service.latest_frame("owner", state["session_id"], 0).header["result"]["detections"][0]["label"], "person")
        with self.assertRaises(FrozenInstanceError):
            packet.jpeg = b"changed"
        self.assertNotIn("private camera image", repr(state))
        self.assertNotIn("detections", repr(state))
        self.assertNotIn("result", state)

    def test_stale_frame_is_not_served(self):
        session = self.harness.session()
        session.age = 2
        service = self.service()
        service.start("owner")
        state = self.streaming(service)
        self.assertIsNone(service.latest_frame("owner", state["session_id"], 0))

    def test_stop_retains_ownership_until_camera_and_detector_are_released(self):
        session = self.harness.session()
        session.camera_cleanup.release.clear()
        service = self.service()
        service.start("owner")
        state = self.streaming(service)
        service.stop("owner")
        self.assertTrue(session.camera_cleanup.entered.wait(WAIT))
        stopping = service.snapshot("owner")
        self.assertEqual(stopping["state"], "stopping")
        self.assertTrue(stopping["owned"])
        self.assertIsNone(service.latest_frame("owner", state["session_id"], 0))
        self.assert_code("busy", lambda: service.start("other"))
        session.camera_cleanup.release.set()
        idle = self.wait(service, lambda state: state["state"] == "idle")
        self.assertFalse(idle["owned"])
        self.assertIsNone(idle["session_id"])
        self.assertTrue(session.camera_closed)
        self.assertTrue(session.detector_closed)
        service.stop("owner")

    def test_maintenance_waits_for_resources_and_blocks_preview_until_resumed(self):
        session = self.harness.session()
        session.camera_cleanup.release.clear()
        service = self.service()
        service.start("owner")
        self.streaming(service)
        self.assert_code("shutdown_timeout", lambda: service.suspend(timeout=0.01))
        self.assertTrue(session.camera_cleanup.entered.wait(WAIT))
        self.assert_code("maintenance", lambda: service.start("other"))
        self.assertFalse(service.snapshot()["ready"])
        session.camera_cleanup.release.set()
        service.suspend(timeout=WAIT)
        self.assertTrue(session.camera_closed)
        self.assertTrue(session.detector_closed)
        service.resume()
        self.assertEqual(service.snapshot()["state"], "idle")
        self.assertTrue(service.snapshot()["ready"])
        self.assertIsNone(service.snapshot()["session_id"])

    def test_cancellation_discards_results_after_each_blocking_stage(self):
        for stage in ("read", "detect", "encode"):
            with self.subTest(stage=stage):
                session = self.harness.session()
                gate = getattr(session, stage)[0]
                gate.release.clear()
                service = self.service()
                service.start("owner")
                self.assertTrue(gate.entered.wait(WAIT))
                sid = service.snapshot("owner")["session_id"]
                service.stop("owner")
                gate.release.set()
                state = self.wait(service, lambda state: state["state"] == "idle")
                self.assertEqual(state["stats"]["frames"], 0)
                self.assertIsNone(service.latest_frame("owner", sid, 0))
                if stage == "read":
                    self.assertEqual(session.detected, 0)
                if stage != "encode":
                    self.assertEqual(session.encoded, 0)

    def test_model_initialization_failure_never_opens_camera(self):
        session = self.harness.session()
        session.load_error = VisionError("model_unavailable", "Missing model")
        service = self.service()
        service.start("owner")
        state = self.wait(service, lambda state: state["state"] == "error")
        self.assertEqual(state["error"]["code"], "model_unavailable")
        self.assertFalse(state["owned"])
        self.assertTrue(state["ready"])
        self.assertEqual(self.harness.camera_calls, [])
        failed_session = state["session_id"]
        session.load_error = None
        service.start("owner")
        recovered = self.streaming(service)
        self.assertIsNone(recovered["error"])
        self.assertNotEqual(recovered["session_id"], failed_session)

    def test_camera_open_and_processing_errors_release_resources(self):
        for attribute, error in (
            ("open_error", CameraError("camera_unavailable", "No camera")),
            ("read_error", CameraError("frame_timeout", "No frame")),
            ("detect_error", VisionError("inference_failed", "NPU failure")),
            ("encode_error", vision_service.VisionServiceError("encode_failed", "JPEG failure")),
        ):
            with self.subTest(stage=attribute):
                session = self.harness.session(frames=0 if attribute == "read_error" else 1)
                setattr(session, attribute, error)
                service = self.service()
                service.start("owner")
                state = self.wait(service, lambda state: state["state"] == "error")
                self.assertEqual(state["error"]["code"], error.code)
                self.assertFalse(state["owned"])
                self.assertTrue(session.camera_closed)
                self.assertTrue(session.detector_closed)
                self.assertIsNone(service.latest_frame("owner", state["session_id"], 0))

    def test_empty_and_oversized_jpeg_are_rejected(self):
        for jpeg in (b"", bytes(256 * 1024 + 1)):
            with self.subTest(length=len(jpeg)):
                session = self.harness.session()
                session.jpeg = jpeg
                service = self.service()
                service.start("owner")
                state = self.wait(service, lambda state: state["state"] == "error")
                self.assertEqual(state["error"]["code"], "encode_failed")
                self.assertEqual(state["stats"]["frames"], 0)

    def test_failed_cleanup_retains_owner_and_resources_for_close_retry(self):
        session = self.harness.session()
        session.camera_close_failures = session.detector_close_failures = 1
        service = self.service()
        service.start("owner")
        self.streaming(service)
        service.stop("owner")
        state = self.wait(service, lambda state: state["state"] == "error")
        self.assertEqual(state["error"]["code"], "cleanup_failed")
        self.assertTrue(state["owned"])
        self.assertFalse(state["ready"])
        self.assert_code("not_ready", lambda: service.start("owner"))
        service.close(timeout=WAIT)
        self.assertTrue(session.camera_closed)
        self.assertTrue(session.detector_closed)
        self.assertEqual(session.camera_close_calls, 2)
        self.assertEqual(session.detector_close_calls, 2)
        self.assertEqual(service.snapshot()["state"], "closed")
        self.assertFalse(service.snapshot()["ready"])

    def test_initialization_cleanup_failure_keeps_service_unavailable(self):
        for wrapped in (False, True):
            with self.subTest(wrapped=wrapped):
                session = self.harness.session()
                session.load_error = VisionError("runtime_unavailable", "Cannot initialize NPU")
                if wrapped:
                    cause = RuntimeError("Native initialization failed")
                    cause.add_note("NPU cleanup failed: resource is still live")
                    session.load_error.__cause__ = cause
                else:
                    session.load_error.add_note("NPU cleanup failed: resource is still live")
                service = self.service()
                self.unsafe_services.add(service)
                service.start("owner")
                state = self.wait(service, lambda state: state["state"] == "error")
                self.assertEqual(state["error"]["code"], "cleanup_failed")
                self.assertTrue(state["owned"])
                self.assertFalse(state["ready"])
                self.assert_code("not_ready", lambda: service.start("owner"))
                self.assert_code("cleanup_failed", lambda: service.close(timeout=WAIT))
                self.assertEqual(service.snapshot()["state"], "error")
                self.harness.sessions.remove(session)

    def test_repeated_close_retries_retained_native_resource_after_worker_exit(self):
        session = self.harness.session()
        session.camera_close_failures = 3
        service = self.service()
        service.start("owner")
        self.streaming(service)
        service.stop("owner")
        self.wait(service, lambda state: state["state"] == "error")
        self.assert_code("cleanup_failed", lambda: service.close(timeout=WAIT))
        state = service.snapshot("owner")
        self.assertEqual(state["state"], "error")
        self.assertTrue(state["owned"])
        self.assertFalse(state["ready"])
        self.assertFalse(session.camera_closed)
        self.assert_code("closed", lambda: service.start("other"))
        session.camera_close_failures = 0
        service.close(timeout=WAIT)
        self.assertTrue(session.camera_closed)
        self.assertEqual(service.snapshot()["state"], "closed")
        self.assertFalse(service.snapshot("owner")["owned"])

    def test_close_during_load_or_first_frame_waits_and_discards_late_work(self):
        for stage in ("load", "encode"):
            with self.subTest(stage=stage):
                session = self.harness.session()
                gate = session.load if stage == "load" else session.encode[0]
                gate.release.clear()
                service = self.service()
                service.start("owner")
                self.assertTrue(gate.entered.wait(WAIT))
                sid = service.snapshot("owner")["session_id"]
                self.assert_code("shutdown_timeout", lambda: service.close(timeout=0.001))
                self.assertEqual(service.snapshot()["state"], "stopping")
                self.assertFalse(service.snapshot()["ready"])
                self.assert_code("closed", lambda: service.start("owner"))
                gate.release.set()
                service.close(timeout=WAIT)
                state = service.snapshot()
                self.assertEqual(state["state"], "closed")
                self.assertEqual(state["stats"]["frames"], 0)
                self.assertTrue(session.detector_closed)
                self.assertIsNone(service.latest_frame("owner", sid, 0))
                if stage == "load":
                    # No camera was constructed, so its pending test session
                    # remains queued for the next subtest.
                    self.harness.sessions.remove(session)
                    self.assertFalse(session.open.entered.is_set())


class JpegEncoderTests(unittest.TestCase):
    def fake_cv2(self):
        return SimpleNamespace(COLOR_RGB2BGR=4, IMWRITE_JPEG_QUALITY=1,
                               cvtColor=Mock(return_value=object()),
                               imencode=Mock(return_value=(True, SimpleNamespace(tobytes=lambda: JPEG))))

    def test_encoder_converts_rgb_to_bgr_and_uses_quality_75(self):
        cv2 = self.fake_cv2()
        rgb = object()
        with patch.dict("sys.modules", {"cv2": cv2}):
            self.assertEqual(vision_service._encode_jpeg(rgb), JPEG)
        cv2.cvtColor.assert_called_once_with(rgb, cv2.COLOR_RGB2BGR)
        cv2.imencode.assert_called_once_with(".jpg", cv2.cvtColor.return_value, [cv2.IMWRITE_JPEG_QUALITY, 75])

    def test_encoder_reports_encode_failed_for_native_errors(self):
        for failure in ("false", "missing", "convert_error", "encode_error"):
            with self.subTest(failure=failure):
                cv2 = self.fake_cv2()
                if failure == "false":
                    cv2.imencode.return_value = (False, None)
                elif failure == "missing":
                    cv2.imencode.return_value = (True, None)
                elif failure == "convert_error":
                    cv2.cvtColor.side_effect = RuntimeError("Native conversion failed")
                else:
                    cv2.imencode.side_effect = RuntimeError("Native encoding failed")
                with patch.dict("sys.modules", {"cv2": cv2}):
                    with self.assertRaises(vision_service.VisionServiceError) as result:
                        vision_service._encode_jpeg(object())
                self.assertEqual(result.exception.code, "encode_failed")


if __name__ == "__main__":
    unittest.main()
