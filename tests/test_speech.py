"""Speech session lifecycle tests with controlled model and microphone doubles."""

from collections import deque
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from radxa_duck import speech
from radxa_duck.asr import AsrError
from radxa_duck.audio_capture import CaptureCancelled, CaptureError
from radxa_duck.vad import VadError


WAIT = 3
PCM = b"private microphone samples"


def update(partial="", texts=(), audio_seconds=0.1):
    return {
        "partial_text": partial, "audio_seconds": audio_seconds,
        "finalized": [{"text": text, "start_seconds": 0, "end_seconds": 0.1}
                      for text in texts],
    }


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
    def __init__(self, harness, updates=(), *, fail_capture=None):
        self.updates = list(updates)
        self.steps = [harness.gate() for _ in updates]
        self.start = harness.gate()
        self.cleanup = harness.gate()
        self.capture_released = threading.Event()
        self.decoder_released = threading.Event()
        self.accepted = []
        self.fail_capture = fail_capture


class Model:
    def __init__(self, harness, name):
        self.harness, self.name = harness, name

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.harness.closed.append(self.name)

    @contextmanager
    def open_stream(self, vad, cancelled):
        if cancelled.is_set():
            raise AsrError("cancelled", "Cancelled before decoder startup")
        session = self.harness.sessions.popleft()
        self.harness.active = session

        class Decoder:
            def accept_pcm(self, pcm):
                index = len(session.accepted)
                session.accepted.append(pcm)
                session.steps[index].wait()
                result = session.updates[index]
                if isinstance(result, Exception):
                    raise result
                # Intentionally return even after cancellation to exercise the
                # service's rejection of late native decode results.
                return deepcopy(result)

        try:
            yield Decoder()
        finally:
            session.decoder_released.set()


class Harness:
    def __init__(self):
        self.gates = []
        self.asr_init = self.gate()
        self.vad_init = self.gate()
        self.asr_error = self.vad_error = None
        self.created = []
        self.closed = []
        self.sessions = deque()
        self.active = None
        self.capture_calls = 0

    def gate(self, opened=True):
        gate = Gate(opened)
        self.gates.append(gate)
        return gate

    def session(self, *args, **kwargs):
        session = Session(self, *args, **kwargs)
        self.sessions.append(session)
        return session

    def make_asr(self, _path):
        self.asr_init.wait()
        if self.asr_error:
            raise self.asr_error
        self.created.append("asr")
        return Model(self, "asr")

    def make_vad(self, _path):
        self.vad_init.wait()
        if self.vad_error:
            raise self.vad_error
        self.created.append("vad")
        return Model(self, "vad")

    @contextmanager
    def capture(self, cancelled, on_recording):
        self.capture_calls += 1
        session = self.active

        def chunks():
            for _result in session.updates:
                if cancelled.is_set():
                    raise CaptureCancelled("Capture cancelled")
                yield PCM
            if session.fail_capture:
                raise session.fail_capture
            if not cancelled.wait(WAIT):
                raise AssertionError("Test session was not stopped")
            raise CaptureCancelled("Capture cancelled")

        try:
            session.start.wait()
            if cancelled.is_set():
                raise CaptureCancelled("Cancelled before first audio")
            on_recording()
            yield chunks()
        finally:
            session.cleanup.wait()
            session.capture_released.set()


class SpeechServiceTests(unittest.TestCase):
    def setUp(self):
        self.harness = Harness()
        self.services = []
        for name, replacement in (
            ("ZipformerAsr", self.harness.make_asr),
            ("SileroVad", self.harness.make_vad),
            ("open_pcm_stream", self.harness.capture),
        ):
            mock = patch.object(speech, name, replacement)
            mock.start()
            self.addCleanup(mock.stop)
        self.addCleanup(self.cleanup_services)

    def cleanup_services(self):
        for gate in self.harness.gates:
            gate.release.set()
        for service in self.services:
            service.close(timeout=WAIT)

    def service(self, ready=True):
        service = speech.SpeechService(Path("models/asr"), Path("models/vad"))
        self.services.append(service)
        if ready:
            self.wait(service, lambda state: state["ready"])
        return service

    def wait(self, service, predicate):
        with service._condition:
            reached = service._condition.wait_for(lambda: predicate(service.snapshot("owner")), WAIT)
            state = service.snapshot("owner")
        self.assertTrue(reached, state)
        return state

    def assert_code(self, code, action):
        with self.assertRaises(speech.SpeechError) as result:
            action()
        self.assertEqual(result.exception.code, code)

    def test_background_initialization_and_not_ready(self):
        self.harness.asr_init.release.clear()
        service = self.service(ready=False)
        self.assertTrue(self.harness.asr_init.entered.wait(WAIT))
        self.assertEqual(service.snapshot()["state"], "loading")
        self.assert_code("not_ready", lambda: service.start("owner"))
        self.harness.asr_init.release.set()
        state = self.wait(service, lambda state: state["ready"])
        self.assertEqual(state["state"], "idle")
        self.assertEqual(self.harness.created, ["asr", "vad"])
        self.assertEqual(self.harness.capture_calls, 0)

    def test_asr_initialization_error_does_not_open_vad(self):
        self.harness.asr_error = AsrError("unsupported_model", "Wrong model")
        service = self.service(ready=False)
        state = self.wait(service, lambda state: state["state"] == "error")
        self.assertFalse(state["ready"])
        self.assertEqual(state["error"]["code"], "unsupported_model")
        self.assertEqual(self.harness.created, [])
        self.assertFalse(self.harness.vad_init.entered.is_set())

    def test_vad_initialization_error_closes_already_loaded_asr(self):
        self.harness.vad_error = VadError("model_unavailable", "No VAD model")
        service = self.service(ready=False)
        state = self.wait(service, lambda state: state["state"] == "error")
        self.assertEqual(state["error"]["code"], "model_unavailable")
        self.assertEqual(self.harness.closed, ["asr"])

    def test_same_owner_start_is_idempotent_other_owner_is_rejected(self):
        session = self.harness.session()
        session.start.release.clear()
        service = self.service()
        service.start("owner")
        self.assertTrue(session.start.entered.wait(WAIT))
        starting = service.snapshot("owner")
        service.start("owner")
        self.assertEqual(service.snapshot("owner")["revision"], starting["revision"])
        self.assert_code("busy", lambda: service.start("other"))
        self.assert_code("not_owner", lambda: service.stop("other"))
        session.start.release.set()
        listening = self.wait(service, lambda state: state["state"] == "listening")
        service.start("owner")
        self.assertEqual(service.snapshot("owner")["session_id"], starting["session_id"])
        self.assertEqual(service.snapshot("owner")["revision"], listening["revision"])
        self.assertFalse(service.snapshot("other")["owned"])
        service.stop("owner")
        self.wait(service, lambda state: state["state"] == "idle")
        self.assertEqual(self.harness.capture_calls, 1)

    def test_cancel_before_worker_opens_microphone(self):
        service = self.service()
        with service._condition:
            service.start("owner")
            service.stop("owner")
        state = self.wait(service, lambda state: state["state"] == "idle")
        self.assertEqual(self.harness.capture_calls, 0)
        self.assertEqual(state["segments"], [])
        self.assertIsNone(state["error"])

    def test_cancel_during_capture_startup(self):
        session = self.harness.session()
        session.start.release.clear()
        service = self.service()
        service.start("owner")
        self.assertTrue(session.start.entered.wait(WAIT))
        service.stop("owner")
        session.start.release.set()
        state = self.wait(service, lambda state: state["state"] == "idle")
        self.assertEqual(session.accepted, [])
        self.assertEqual(state["partial_text"], "")
        self.assertTrue(session.capture_released.is_set())
        self.assertTrue(session.decoder_released.is_set())

    def test_late_partial_and_final_are_discarded_after_stop(self):
        session = self.harness.session([
            update("current", audio_seconds=0.1),
            update("late", ["late final"], audio_seconds=0.2),
        ])
        session.steps[1].release.clear()
        service = self.service()
        service.start("owner")
        self.assertTrue(session.steps[1].entered.wait(WAIT))
        self.assertEqual(service.snapshot()["partial_text"], "current")
        service.stop("owner")
        self.assertEqual(service.snapshot()["partial_text"], "")
        session.steps[1].release.set()
        state = self.wait(service, lambda state: state["state"] == "idle")
        self.assertEqual(state["segments"], [])
        self.assertEqual(state["partial_text"], "")
        self.assertEqual(state["audio_seconds"], 0.1)

    def test_stop_waits_for_capture_and_decoder_release_before_idle(self):
        session = self.harness.session()
        session.cleanup.release.clear()
        service = self.service()
        service.start("owner")
        self.wait(service, lambda state: state["state"] == "listening")
        service.stop("owner")
        self.assertTrue(session.cleanup.entered.wait(WAIT))
        state = service.snapshot("owner")
        self.assertEqual(state["state"], "stopping")
        self.assertTrue(state["owned"])
        self.assert_code("busy", lambda: service.start("owner"))
        self.assert_code("busy", lambda: service.start("other"))
        session.cleanup.release.set()
        state = self.wait(service, lambda state: state["state"] == "idle")
        self.assertTrue(session.capture_released.is_set())
        self.assertTrue(session.decoder_released.is_set())
        self.assertFalse(state["owned"])
        service.stop("owner")

    def test_maintenance_waits_for_cleanup_and_requires_explicit_new_start(self):
        session = self.harness.session()
        session.cleanup.release.clear()
        service = self.service()
        service.start("owner")
        self.wait(service, lambda state: state["state"] == "listening")
        self.assert_code("shutdown_timeout", lambda: service.suspend(timeout=0.01))
        self.assertTrue(session.cleanup.entered.wait(WAIT))
        self.assertFalse(service.snapshot()["ready"])
        self.assert_code("maintenance", lambda: service.start("other"))
        session.cleanup.release.set()
        service.suspend(timeout=WAIT)
        self.assertTrue(session.capture_released.is_set())
        self.assertTrue(session.decoder_released.is_set())
        service.resume()
        self.assertEqual(service.snapshot()["state"], "idle")
        self.assertTrue(service.snapshot()["ready"])
        self.assertFalse(service.snapshot("owner")["owned"])
        self.assertEqual(self.harness.capture_calls, 1)

    def test_capture_error_releases_resources_and_allows_restart(self):
        first = self.harness.session(fail_capture=CaptureError("Microphone failed", code="device_unavailable"))
        second = self.harness.session()
        service = self.service()
        service.start("owner")
        state = self.wait(service, lambda state: state["state"] == "error")
        self.assertEqual(state["error"]["code"], "device_unavailable")
        self.assertTrue(state["ready"])
        self.assertTrue(first.capture_released.is_set())
        self.assertTrue(first.decoder_released.is_set())
        service.start("other")
        state = self.wait(service, lambda state: state["state"] == "listening")
        self.assertIsNone(state["error"])
        self.assertNotEqual(state["session_id"], None)
        service.stop("other")
        self.wait(service, lambda state: state["state"] == "idle")
        self.assertTrue(second.capture_released.is_set())
        self.assertEqual(self.harness.created, ["asr", "vad"])

    def test_decoder_error_and_unexpected_error_are_classified(self):
        for failure, code in ((AsrError("timeout", "Decode timed out"), "timeout"),
                              (ValueError("private internal details"), "internal_error")):
            with self.subTest(code=code):
                session = self.harness.session([failure])
                service = self.service()
                service.start("owner")
                state = self.wait(service, lambda state: state["state"] == "error")
                self.assertEqual(state["error"]["code"], code)
                self.assertNotIn("private internal details", state["error"]["message"])
                self.assertTrue(session.capture_released.is_set())
                self.assertTrue(session.decoder_released.is_set())
                service.close(timeout=WAIT)

    def test_stopping_discards_partial_but_keeps_already_finalized_text(self):
        self.harness.session([update("unfinished", ["completed"])])
        service = self.service()
        service.start("owner")
        self.wait(service, lambda state: state["partial_text"] == "unfinished")
        service.stop("owner")
        state = self.wait(service, lambda state: state["state"] == "idle")
        self.assertEqual(state["partial_text"], "")
        self.assertEqual([item["text"] for item in state["segments"]], ["completed"])

    def test_only_twenty_finals_are_retained_and_expire_after_five_minutes(self):
        self.harness.session([update(texts=[f"sentence {i}" for i in range(25)])])
        service = self.service()
        service.start("owner")
        state = self.wait(service, lambda state: len(state["segments"]) == 20)
        self.assertEqual([item["id"] for item in state["segments"]], list(range(6, 26)))
        state["segments"][0]["text"] = "external mutation"
        self.assertEqual(service.snapshot()["segments"][0]["text"], "sentence 5")
        service.stop("owner")
        self.wait(service, lambda state: state["state"] == "idle")
        finished = service._finished_at
        with patch.object(speech.time, "monotonic", return_value=finished + 299.999):
            self.assertEqual(len(service.snapshot()["segments"]), 20)
        with patch.object(speech.time, "monotonic", return_value=finished + 300):
            self.assertEqual(service.snapshot()["segments"], [])

    def test_new_session_clears_prior_text_and_stale_callback_cannot_change_state(self):
        self.harness.session([update(texts=["old session"])])
        next_session = self.harness.session()
        next_session.start.release.clear()
        service = self.service()
        service.start("owner")
        old = self.wait(service, lambda state: bool(state["segments"]))
        service.stop("owner")
        self.wait(service, lambda state: state["state"] == "idle")
        service.start("other")
        self.assertTrue(next_session.start.entered.wait(WAIT))
        new = service.snapshot("other")
        self.assertNotEqual(old["session_id"], new["session_id"])
        self.assertEqual(new["segments"], [])
        self.assertEqual(new["audio_seconds"], 0)
        service._recording(old["session_id"])
        self.assertEqual(service.snapshot("other")["state"], "starting")
        service.stop("other")
        next_session.start.release.set()
        self.wait(service, lambda state: state["state"] == "idle")

    def test_close_during_asr_initialization_releases_partial_initialization(self):
        self.harness.asr_init.release.clear()
        service = self.service(ready=False)
        self.assertTrue(self.harness.asr_init.entered.wait(WAIT))
        self.assert_code("shutdown_timeout", lambda: service.close(timeout=0))
        self.assert_code("closed", lambda: service.start("owner"))
        self.harness.asr_init.release.set()
        self.wait(service, lambda state: state["state"] == "closed")
        self.assertEqual(self.harness.closed, ["asr"])
        self.assertFalse(self.harness.vad_init.entered.is_set())

    def test_close_during_vad_initialization_never_advertises_ready(self):
        self.harness.vad_init.release.clear()
        service = self.service(ready=False)
        self.assertTrue(self.harness.vad_init.entered.wait(WAIT))
        changes = []
        original = service._changed

        def observe_change():
            changes.append((service._closing, service._ready, service._state))
            original()

        with patch.object(service, "_changed", observe_change):
            self.assert_code("shutdown_timeout", lambda: service.close(timeout=0))
            self.harness.vad_init.release.set()
            self.wait(service, lambda state: state["state"] == "closed")
        self.assertFalse(any(closing and ready for closing, ready, _state in changes), changes)
        self.assertEqual(self.harness.closed, ["vad", "asr"])

    def test_close_during_capture_waits_for_microphone_and_closes_models(self):
        session = self.harness.session([update("unfinished", ["finished"])])
        session.cleanup.release.clear()
        service = self.service()
        service.start("owner")
        self.wait(service, lambda state: state["partial_text"] == "unfinished")
        self.assert_code("shutdown_timeout", lambda: service.close(timeout=0))
        self.assertTrue(session.cleanup.entered.wait(WAIT))
        self.assertEqual(service.snapshot()["state"], "stopping")
        self.assertEqual(self.harness.closed, [])
        session.cleanup.release.set()
        state = self.wait(service, lambda state: state["state"] == "closed")
        self.assertFalse(state["ready"])
        self.assertEqual(state["segments"], [])
        self.assertEqual(state["partial_text"], "")
        self.assertTrue(session.capture_released.is_set())
        self.assertTrue(session.decoder_released.is_set())
        self.assertEqual(self.harness.closed, ["vad", "asr"])

    def test_session_state_does_not_store_or_write_audio(self):
        self.harness.session([update("temporary text")])
        service = self.service()
        with (patch("builtins.open") as file_open,
              patch.object(Path, "open") as path_open,
              patch.object(Path, "write_bytes") as write_bytes):
            service.start("owner")
            state = self.wait(service, lambda state: state["partial_text"] == "temporary text")
            service.stop("owner")
            self.wait(service, lambda state: state["state"] == "idle")
        file_open.assert_not_called()
        path_open.assert_not_called()
        write_bytes.assert_not_called()

        def contains_audio(value):
            if isinstance(value, (bytes, bytearray)):
                return True
            if isinstance(value, dict):
                return any(contains_audio(item) for item in value.values())
            if isinstance(value, (list, tuple, deque)):
                return any(contains_audio(item) for item in value)
            return False

        self.assertFalse(contains_audio(vars(service)))
        self.assertNotIn("pcm", state)
        self.assertNotIn("audio", state)
        self.assertNotIn(PCM.decode(), repr(state))


if __name__ == "__main__":
    unittest.main()
