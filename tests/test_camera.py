"""Camera ownership and frame-boundary tests without GStreamer or a camera."""

from collections import deque
import io
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from radxa_duck.camera import Camera, CameraConfig, CameraError


SECOND = 1_000_000_000
NONE = (1 << 64) - 1
GST = SimpleNamespace(
    SECOND=SECOND, CLOCK_TIME_NONE=NONE, BUFFER_OFFSET_NONE=NONE,
    MessageType=SimpleNamespace(ERROR=1, EOS=2),
    MapFlags=SimpleNamespace(READ=1), Format=SimpleNamespace(TIME=1),
    State=SimpleNamespace(NULL=0, PLAYING=1),
    StateChangeReturn=SimpleNamespace(FAILURE=0, SUCCESS=1),
    init=lambda _args: None,
)


class Clock:
    now = 100.0

    def get_time(self):
        return int(self.now * SECOND)


class Buffer:
    def __init__(self, pixels, *, pts=99.9, sequence=42, after_unmap=None):
        height, width, _ = pixels.shape
        stride, offset = width * 3 + 8, 7
        self.layout = SimpleNamespace(width=width, height=height,
                                      stride=[stride], offset=[offset])
        self.data = bytearray([255]) * (offset + height * stride)
        view = np.ndarray(pixels.shape, np.uint8, buffer=self.data,
                          offset=offset, strides=(stride, 3, 1))
        view[:] = pixels
        self.pts = int(pts * SECOND) if pts is not None else NONE
        self.offset = sequence
        self.maps = self.unmaps = 0
        self.after_unmap = after_unmap

    def map(self, _flags):
        self.maps += 1
        return True, SimpleNamespace(data=self.data)

    def unmap(self, _data):
        self.unmaps += 1
        if self.after_unmap:
            self.after_unmap()


class Sample:
    def __init__(self, buffer):
        self.buffer = buffer

    def get_buffer(self):
        return self.buffer

    def get_caps(self):
        return self.buffer.layout

    def get_segment(self):
        return SimpleNamespace(to_running_time=lambda _format, pts: pts)


class Pipeline:
    def __init__(self, clock):
        self.clock = clock
        self.states = []
        self.fail_stop = False
        self.stop_pending = False

    def get_clock(self):
        return self.clock

    def get_base_time(self):
        return 0

    def set_state(self, state):
        self.states.append(state)
        return GST.StateChangeReturn.FAILURE if self.fail_stop else GST.StateChangeReturn.SUCCESS

    def get_state(self, _timeout):
        state = GST.State.PLAYING if self.stop_pending else GST.State.NULL
        return GST.StateChangeReturn.SUCCESS, state, GST.State.NULL


class Sink:
    def __init__(self, clock, samples):
        self.clock = clock
        self.samples = deque(samples)
        self.on_pull = None
        self.pulls = 0

    def emit(self, _operation, timeout):
        self.pulls += 1
        if self.on_pull:
            self.on_pull()
        if self.samples:
            return self.samples.popleft()
        self.clock.now += timeout / SECOND
        return None


class CameraTests(unittest.TestCase):
    def opened(self, *buffers, **config):
        unit = Camera(CameraConfig(width=160, height=120, **config))
        clock = Clock()
        unit._gst = GST
        unit._numpy = np
        unit._video = SimpleNamespace(VideoInfo=SimpleNamespace(new_from_caps=lambda caps: caps))
        unit._pipeline = Pipeline(clock)
        unit._bus = SimpleNamespace(pop_filtered=lambda _mask: None)
        unit._sink = Sink(clock, [Sample(buffer) for buffer in buffers])
        unit._lock = io.BytesIO()
        self.addCleanup(unit.close)
        return unit, clock

    def assert_code(self, code, action):
        with self.assertRaises(CameraError) as raised:
            action()
        self.assertEqual(raised.exception.code, code)

    def test_invalid_config_does_not_accept_bool_nonfinite_or_unbounded_values(self):
        for config in ({"width": True}, {"width": 159}, {"height": 127},
                       {"width": 1928}, {"rotation": True}, {"rotation": 45},
                       {"frame_timeout": float("nan")}, {"frame_timeout": float("inf")},
                       {"max_frame_age": False}, {"max_frame_age": 0},
                       {"device": "relative/video0"}):
            with self.subTest(config=config):
                self.assert_code("invalid_config", lambda: CameraConfig(**config))
        self.assert_code("invalid_config", lambda: Camera(cancelled=True))

    def test_padded_rgb_is_an_owned_read_only_copy(self):
        expected = np.arange(120 * 160 * 3, dtype=np.uint8).reshape(120, 160, 3)
        buffer = Buffer(expected)
        unit, clock = self.opened(buffer)
        with patch("radxa_duck.camera.time.monotonic", lambda: clock.now):
            frame = unit.read()
        self.assertEqual(frame.sequence, 42)
        self.assertAlmostEqual(frame.captured_monotonic, 99.9)
        self.assertAlmostEqual(frame.received_monotonic, 100.0)
        self.assertEqual((buffer.maps, buffer.unmaps), (1, 1))
        buffer.data[:] = bytes(len(buffer.data))
        np.testing.assert_array_equal(frame.rgb, expected)
        self.assertTrue(frame.rgb.flags.owndata)
        with self.assertRaises(ValueError):
            frame.rgb[0, 0, 0] = 0

    def test_rotation_requires_swapped_dimensions(self):
        for rotation in (90, 270):
            with self.subTest(rotation=rotation):
                pixels = np.full((160, 120, 3), 21, dtype=np.uint8)
                unit, clock = self.opened(Buffer(pixels), rotation=rotation)
                with patch("radxa_duck.camera.time.monotonic", lambda: clock.now):
                    self.assertEqual(unit.read().rgb.shape, (160, 120, 3))

    def test_stale_frames_are_skipped_without_mapping(self):
        pixels = np.zeros((120, 160, 3), np.uint8)
        old, recent = Buffer(pixels, pts=90, sequence=3), Buffer(pixels, sequence=7)
        unit, clock = self.opened(old, recent)
        with patch("radxa_duck.camera.time.monotonic", lambda: clock.now):
            frame = unit.read()
        self.assertEqual(frame.sequence, 7)
        self.assertEqual(old.maps, 0)
        self.assertEqual(unit._sink.pulls, 2)

    def test_stale_or_missing_input_times_out(self):
        old = Buffer(np.zeros((120, 160, 3), np.uint8), pts=90)
        unit, clock = self.opened(old, frame_timeout=0.1)
        with patch("radxa_duck.camera.time.monotonic", lambda: clock.now):
            self.assert_code("timeout", unit.read)
        self.assertEqual(old.maps, 0)
        self.assertLessEqual(clock.now, 100.151)

    def test_cancel_while_waiting_does_not_wait_for_frame_timeout(self):
        unit, clock = self.opened()
        unit._sink.on_pull = unit.cancelled.set
        with patch("radxa_duck.camera.time.monotonic", lambda: clock.now):
            self.assert_code("cancelled", unit.read)
        self.assertEqual(unit._sink.pulls, 1)
        self.assertLessEqual(clock.now, 100.051)

    def test_cancelled_sample_is_not_mapped_or_returned(self):
        buffer = Buffer(np.zeros((120, 160, 3), np.uint8))
        unit, clock = self.opened(buffer)
        unit._sink.on_pull = unit.cancelled.set
        with patch("radxa_duck.camera.time.monotonic", lambda: clock.now):
            self.assert_code("cancelled", unit.read)
        self.assertEqual(buffer.maps, 0)

    def test_cancellation_after_copy_discards_frame_and_unmaps_buffer(self):
        buffer = Buffer(np.zeros((120, 160, 3), np.uint8))
        unit, clock = self.opened(buffer)
        buffer.after_unmap = unit.cancelled.set
        with patch("radxa_duck.camera.time.monotonic", lambda: clock.now):
            self.assert_code("cancelled", unit.read)
        self.assertEqual((buffer.maps, buffer.unmaps), (1, 1))

    def test_truncated_mapped_buffer_is_unmapped(self):
        buffer = Buffer(np.zeros((120, 160, 3), np.uint8))
        buffer.data.pop()
        unit, clock = self.opened(buffer)
        with patch("radxa_duck.camera.time.monotonic", lambda: clock.now):
            self.assert_code("invalid_frame", unit.read)
        self.assertEqual((buffer.maps, buffer.unmaps), (1, 1))

    def test_missing_timestamp_is_rejected_before_mapping(self):
        buffer = Buffer(np.zeros((120, 160, 3), np.uint8), pts=None)
        unit, clock = self.opened(buffer)
        with patch("radxa_duck.camera.time.monotonic", lambda: clock.now):
            self.assert_code("invalid_frame", unit.read)
        self.assertEqual(buffer.maps, 0)

    def test_capture_error_closes_pipeline_and_lock(self):
        unit, clock = self.opened()
        pipeline, lock = unit._pipeline, unit._lock
        message = SimpleNamespace(type=GST.MessageType.ERROR,
                                  parse_error=lambda: (SimpleNamespace(message="Device lost"), "debug"))
        unit._bus.pop_filtered = lambda _mask: message
        with patch.object(Camera, "__enter__", return_value=unit):
            with self.assertRaises(CameraError) as raised:
                with unit:
                    unit.read()
        self.assertEqual(raised.exception.code, "capture_failed")
        self.assertEqual(pipeline.states, [GST.State.NULL])
        self.assertTrue(lock.closed)
        self.assert_code("invalid_state", unit.read)
        unit.close()
        self.assertEqual(pipeline.states, [GST.State.NULL])

    def test_initialization_failure_releases_acquired_lock(self):
        unit = Camera()
        lock = io.BytesIO()
        gi = SimpleNamespace(require_version=lambda *_: None)
        repository = SimpleNamespace(Gst=GST, GstVideo=SimpleNamespace())
        def acquire():
            unit._lock = lock
        with patch.dict("sys.modules", {"gi": gi, "gi.repository": repository}), \
                patch.object(unit, "_acquire_device", acquire), \
                patch.object(unit, "_build_pipeline", side_effect=CameraError("dependency_unavailable", "Missing plugin")):
            self.assert_code("dependency_unavailable", unit.__enter__)
        self.assertTrue(lock.closed)
        self.assertIsNone(unit._lock)

    def test_device_lock_excludes_second_owner_and_is_released(self):
        # /dev/null is only stat'ed; these tests never open a video device.
        with tempfile.TemporaryDirectory() as directory, \
                patch("radxa_duck.camera.tempfile.gettempdir", return_value=directory):
            first = Camera(CameraConfig(device="/dev/null"))
            second = Camera(CameraConfig(device="/dev/null"))
            third = Camera(CameraConfig(device="/dev/null"))
            try:
                first._acquire_device()
                self.assert_code("device_busy", second._acquire_device)
                second.close()
                first.close()
                third._acquire_device()
            finally:
                first.close()
                second.close()
                third.close()

    def test_failed_or_pending_stop_retains_ownership_until_successful_retry(self):
        for failure in ("fail_stop", "stop_pending"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory, \
                    patch("radxa_duck.camera.tempfile.gettempdir", return_value=directory):
                owner = Camera(CameraConfig(device="/dev/null"))
                contender = Camera(CameraConfig(device="/dev/null"))
                replacement = Camera(CameraConfig(device="/dev/null"))
                pipeline = Pipeline(Clock())
                owner._gst, owner._pipeline = GST, pipeline
                try:
                    owner._acquire_device()
                    lock = owner._lock
                    setattr(pipeline, failure, True)
                    self.assert_code("cleanup_failed", owner.close)
                    self.assertIs(owner._pipeline, pipeline)
                    self.assertIs(owner._lock, lock)
                    self.assertFalse(lock.closed)
                    self.assertFalse(owner._closed)
                    self.assert_code("device_busy", contender._acquire_device)
                    contender.close()

                    setattr(pipeline, failure, False)
                    owner.close()
                    self.assertIsNone(owner._pipeline)
                    self.assertIsNone(owner._lock)
                    self.assertTrue(lock.closed)
                    replacement._acquire_device()
                finally:
                    pipeline.fail_stop = pipeline.stop_pending = False
                    owner.close()
                    contender.close()
                    replacement.close()

    def test_cleanup_failure_keeps_original_error_and_allows_explicit_retry(self):
        unit, _clock = self.opened()
        pipeline, lock = unit._pipeline, unit._lock
        original = CameraError("capture_failed", "Device lost")
        pipeline.fail_stop = True
        try:
            with patch.object(Camera, "__enter__", return_value=unit):
                with self.assertRaises(CameraError) as raised:
                    with unit:
                        raise original
            self.assertIs(raised.exception, original)
            self.assertTrue(any("cleanup failed" in note for note in original.__notes__))
            self.assertIs(unit._pipeline, pipeline)
            self.assertFalse(lock.closed)
        finally:
            pipeline.fail_stop = False
            unit.close()
        self.assertTrue(lock.closed)


if __name__ == "__main__":
    unittest.main()
