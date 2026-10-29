"""Bounded synthesis/process and audio safety checks; no real TTS or speaker."""

from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np

from radxa_duck import audio_capture, speech_output as output


PRIVATE_TEXT = "不应进入日志和返回结果的私有回答"


def write_audio(directory, *, frames=480, sample_rate=48000):
    with wave.open(str(directory / "reply.wav"), "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\0" * frames * 4)
    metadata = {"source_sample_rate": 8000, "audio_seconds": frames / 48000,
                "model_load_seconds": .01, "synthesis_seconds": .02, "conversion_seconds": .01}
    (directory / "result.json").write_text(json.dumps(metadata))
    return metadata


class SpeechOutputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.model_dir = self.root / "model"
        self.model_dir.mkdir()
        files = []
        for name, content in (("model.onnx", b"fake-model"), ("lexicon.txt", b"fake-lexicon"), ("tokens.txt", b"fake-tokens")):
            (self.model_dir / name).write_bytes(content)
            files.append((name, len(content), hashlib.sha256(content).hexdigest()))
        self.model = output._Model("test-vits", "model.onnx", 8000, 1, tuple(files))
        self.addCleanup(patch.stopall)
        patch.object(output, "_MODELS", (self.model,)).start()
        self.cancelled = threading.Event()
        self.services = []
        self.addCleanup(self.close_services)

    def close_services(self):
        for service in self.services:
            service.close(timeout=2)

    def service(self, **kwargs):
        service = output.SpeechOutput(self.model_dir, **kwargs)
        self.services.append(service)
        return service

    def assert_code(self, code, operation):
        with self.assertRaises(output.SpeechOutputError) as raised:
            operation()
        self.assertEqual(raised.exception.code, code)
        self.assertNotIn(PRIVATE_TEXT, str(raised.exception))

    def test_snapshot_is_lazy_and_unsupported_configuration_is_explicit(self):
        with patch.object(output.subprocess, "Popen") as process, patch.object(output.audio_capture, "_audio_guard") as guard:
            status = output.SpeechOutput().snapshot()
            self.assertFalse(status["available"])
            self.assertEqual(status["reason"], "tts_unavailable")
            self.assertEqual(status["provider"], "cpu")
            with patch.object(output.importlib.util, "find_spec", return_value=object()), patch.object(output.shutil, "which", return_value="/bin/fake"):
                available = self.service().snapshot()
                self.assertTrue(available["available"])
                self.assertEqual(available["model"], "test-vits")
                self.assertEqual(available["sample_rate"], 48000)
                self.assertEqual(available["volume_percent"], 45)
            process.assert_not_called()
            guard.assert_not_called()
        self.assert_code("invalid_request", lambda: output.SpeechOutput(volume_percent=True))
        self.assert_code("invalid_request", lambda: output.SpeechOutput(speaker_id=-1))

    def test_manifest_checks_hashes_regular_files_and_exact_sizes(self):
        self.assertEqual(output._model(self.model_dir, hashes=True), self.model)
        path = self.model_dir / "lexicon.txt"
        original = path.read_bytes()
        path.write_bytes(b"x" * len(original))
        self.assert_code("model_mismatch", lambda: output._model(self.model_dir, hashes=True))
        path.write_bytes(b"short")
        self.assert_code("model_mismatch", lambda: output._model(self.model_dir))
        path.unlink()
        target = self.root / "lexicon"
        target.write_bytes(original)
        path.symlink_to(target)
        self.assert_code("model_mismatch", lambda: output._model(self.model_dir))
        path.unlink()
        os.mkfifo(path)
        self.assert_code("model_mismatch", lambda: output._model(self.model_dir))

    def test_invalid_text_never_reaches_synthesis_or_hardware(self):
        service = self.service()
        with patch.object(output, "_synthesize") as synthesis, patch.object(audio_capture, "_audio_guard") as guard:
            for text in (None, "", "  ", "x\0y", "x\x7f", "\ud800", "x" * (output.MAX_TEXT_CHARS + 1)):
                self.assert_code("invalid_request", lambda: service.speak(text, cancelled=self.cancelled))
            synthesis.assert_not_called()
            guard.assert_not_called()

    def test_successful_reply_uses_private_temporary_audio_and_exclusive_guard(self):
        commands, phases, paths, guard_events = [], [], [], []
        def synthesize(_directory, _speaker, text, temporary, cancelled):
            self.assertEqual(text, PRIVATE_TEXT)
            self.assertEqual(stat.S_IMODE(temporary.stat().st_mode), 0o700)
            paths.append(temporary)
            return write_audio(temporary)
        @contextmanager
        def guard(cancelled):
            guard_events.append("enter")
            try:
                yield
            finally:
                guard_events.append("muted_cleanup")
        with patch.object(output, "_synthesize", side_effect=synthesize), patch.object(audio_capture, "_audio_guard", side_effect=guard), patch.object(audio_capture, "_run", side_effect=lambda command, *_: commands.append(command)):
            result = self.service(volume_percent=23).speak(PRIVATE_TEXT, cancelled=self.cancelled, on_phase=phases.append)
        self.assertTrue(result["played"])
        self.assertNotIn(PRIVATE_TEXT, str(result))
        self.assertEqual(result["provider"], "cpu")
        self.assertEqual(phases, ["synthesizing", "playing"])
        self.assertEqual(guard_events, ["enter", "muted_cleanup"])
        self.assertEqual(commands[0], ["amixer", "-q", "-c", "PollenHATC1", "sset", "PCM", "23%"])
        self.assertEqual(commands[1][-3:], ["Line", "0dB", "unmute"])
        self.assertEqual(commands[2][:4], ["aplay", "-q", "-D", "hw:CARD=PollenHATC1,DEV=0"])
        self.assertTrue(all(not path.exists() for path in paths))

    def test_cancelled_playback_cleans_temporary_data_and_restores_mute(self):
        paths, restored = [], threading.Event()
        def synthesize(_directory, _speaker, _text, temporary, _cancelled):
            paths.append(temporary)
            return write_audio(temporary)
        @contextmanager
        def guard(_cancelled):
            try:
                yield
            finally:
                restored.set()
        def run(command, *_args):
            if command[0] == "aplay":
                raise audio_capture.CaptureCancelled("private internal error " + PRIVATE_TEXT)
        with patch.object(output, "_synthesize", side_effect=synthesize), patch.object(audio_capture, "_audio_guard", side_effect=guard), patch.object(audio_capture, "_run", side_effect=run):
            self.assert_code("cancelled", lambda: self.service().speak(PRIVATE_TEXT, cancelled=self.cancelled))
        self.assertTrue(restored.is_set())
        self.assertTrue(all(not path.exists() for path in paths))

    def test_busy_hardware_never_changes_mixer_and_removes_generated_audio(self):
        paths = []
        def synthesize(_directory, _speaker, _text, temporary, _cancelled):
            paths.append(temporary)
            return write_audio(temporary)
        with patch.object(output, "_synthesize", side_effect=synthesize), patch.object(audio_capture, "_audio_guard", side_effect=audio_capture.CaptureError("busy", code="device_busy")), patch.object(audio_capture, "_run") as run:
            self.assert_code("device_busy", lambda: self.service().speak(PRIVATE_TEXT, cancelled=self.cancelled))
            run.assert_not_called()
        self.assertTrue(all(not path.exists() for path in paths))

    def test_cancel_before_synthesis_and_zero_volume_never_touch_hardware(self):
        with patch.object(output, "_synthesize") as synthesize, patch.object(audio_capture, "_audio_guard") as guard:
            self.cancelled.set()
            self.assert_code("cancelled", lambda: self.service().speak(PRIVATE_TEXT, cancelled=self.cancelled))
            self.cancelled.clear()
            result = self.service(volume_percent=0).speak(PRIVATE_TEXT, cancelled=self.cancelled)
            self.assertFalse(result["played"])
            self.assertEqual(result["reason"], "volume_zero")
            synthesize.assert_not_called()
            guard.assert_not_called()

    def test_close_cancels_current_reply_releases_files_and_rejects_new_replies(self):
        entered, errors, paths = threading.Event(), [], []
        def synthesize(_directory, _speaker, _text, temporary, cancelled):
            entered.set()
            paths.append(temporary)
            while not cancelled.is_set():
                threading.Event().wait(.01)
            raise output.SpeechOutputError("cancelled")
        service = self.service()
        def speak():
            try:
                service.speak(PRIVATE_TEXT, cancelled=self.cancelled)
            except output.SpeechOutputError as exc:
                errors.append(exc.code)
        with patch.object(output, "_synthesize", side_effect=synthesize), patch.object(audio_capture, "_audio_guard") as guard:
            thread = threading.Thread(target=speak)
            thread.start()
            self.assertTrue(entered.wait(2))
            self.assert_code("busy", lambda: service.speak("second", cancelled=self.cancelled))
            service.close(timeout=2)
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, ["cancelled"])
            self.assertEqual(service.snapshot()["reason"], "closed")
            self.assert_code("closed", lambda: service.speak("second", cancelled=self.cancelled))
            guard.assert_not_called()
        self.assertTrue(all(not path.exists() for path in paths))

    def test_real_child_timeout_and_cancellation_reap_the_native_process(self):
        real_process = subprocess.Popen
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                processes = []
                event = threading.Event()
                def start(*args, **kwargs):
                    process = real_process(*args, **kwargs)
                    processes.append(process)
                    if cancel:
                        event.set()
                    return process
                command = [sys.executable, "-c", "import time;time.sleep(60)"]
                with patch.object(output, "_worker_command", return_value=command), patch.object(output.subprocess, "Popen", side_effect=start), patch.object(output, "SYNTHESIS_TIMEOUT", .06):
                    self.assert_code("cancelled" if cancel else "synthesis_timeout", lambda: output._synthesize(self.model_dir, 0, PRIVATE_TEXT, self.root, event))
                self.assertEqual(len(processes), 1)
                self.assertIsNotNone(processes[0].poll())

    def test_child_error_does_not_publish_native_error_text(self):
        command = [sys.executable, "-c", "import sys;sys.stderr.write('private native detail');raise SystemExit(2)"]
        with patch.object(output, "_worker_command", return_value=command):
            self.assert_code("synthesis_failed", lambda: output._synthesize(self.model_dir, 0, PRIVATE_TEXT, self.root, self.cancelled))

    def test_audio_result_rejects_invalid_rate_lengths_and_metadata(self):
        write_audio(self.root)
        self.assertEqual(output._read_audio(self.root)["audio_seconds"], .01)
        write_audio(self.root, sample_rate=16000)
        self.assert_code("invalid_audio", lambda: output._read_audio(self.root))
        write_audio(self.root)
        with (self.root / "reply.wav").open("ab") as file:
            file.write(b"unexpected")
        self.assert_code("invalid_audio", lambda: output._read_audio(self.root))
        write_audio(self.root)
        data = json.loads((self.root / "result.json").read_text())
        data["synthesis_seconds"] = float("nan")
        (self.root / "result.json").write_text(json.dumps(data))
        self.assert_code("invalid_audio", lambda: output._read_audio(self.root))

    def test_real_resampling_preserves_duration_duplicates_mono_and_limits_peak(self):
        for rate in (8000, 22050):
            samples = .9 * np.sin(np.arange(rate // 10, dtype=np.float32) * 2 * np.pi * 440 / rate)
            pcm, frames = output._convert(samples, rate)
            stereo = np.frombuffer(pcm, dtype="<i2").reshape((-1, 2))
            self.assertEqual(frames, 4800)
            np.testing.assert_array_equal(stereo[:, 0], stereo[:, 1])
            self.assertLessEqual(int(np.abs(stereo.astype(np.int32)).max()), round(output.PEAK_LIMIT * 32767))
        pcm, _ = output._convert(np.ones(800, dtype=np.float32) * .02, 8000)
        self.assertLess(float(np.abs(np.frombuffer(pcm, dtype="<i2")).max()) / 32768, .03)

    def test_invalid_generated_audio_is_rejected_before_conversion(self):
        for samples, rate in (([], 8000), ([float("nan")], 8000), ([[.1]], 8000), ([.1], 16000), ([.1] * (8000 * 60 + 1), 8000)):
            self.assert_code("invalid_audio", lambda: output._convert(samples, rate))

    def test_worker_loads_cpu_model_only_for_request_and_writes_private_audio(self):
        fields = {}
        def vits(**kwargs):
            fields["vits"] = kwargs
            return kwargs
        def model(**kwargs):
            fields["model"] = kwargs
            return kwargs
        def config(**kwargs):
            fields["config"] = kwargs
            return SimpleNamespace(validate=lambda: True)
        engine = SimpleNamespace(generate=Mock(return_value=SimpleNamespace(sample_rate=8000, samples=np.ones(800, dtype=np.float32) * .02)))
        module = SimpleNamespace(OfflineTtsVitsModelConfig=vits, OfflineTtsModelConfig=model,
                                 OfflineTtsConfig=config, OfflineTts=Mock(return_value=engine))
        previous = os.umask(0o077)
        try:
            with patch.dict(sys.modules, {"sherpa_onnx": module}), patch.object(output.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(PRIVATE_TEXT.encode()))), patch("resource.setrlimit"):
                output._worker(self.model_dir, 0, self.root)
        finally:
            os.umask(previous)
        self.assertEqual(fields["model"]["provider"], "cpu")
        self.assertEqual(fields["model"]["num_threads"], 2)
        self.assertNotIn("rule_fars", fields["config"])
        engine.generate.assert_called_once()
        self.assertEqual(engine.generate.call_args.args, (PRIVATE_TEXT,))
        self.assertEqual(engine.generate.call_args.kwargs["sid"], 0)
        self.assertEqual(engine.generate.call_args.kwargs["speed"], 1.0)
        self.assertTrue(callable(engine.generate.call_args.kwargs["callback"]))
        result = output._read_audio(self.root)
        self.assertEqual(result["audio_seconds"], .1)
        self.assertNotIn(PRIVATE_TEXT, (self.root / "result.json").read_text())
        self.assertEqual(stat.S_IMODE((self.root / "reply.wav").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.root / "result.json").stat().st_mode), 0o600)

    def test_generation_chunks_native_calls_and_never_plays_truncated_audio(self):
        engine = SimpleNamespace(generate=Mock(return_value=SimpleNamespace(
            sample_rate=8000, samples=np.ones(800, dtype=np.float32) * .02)))
        samples = output._generate(engine, "鸭" * 181, self.model, 0)
        self.assertEqual(len(samples), 2400)
        self.assertEqual([len(call.args[0]) for call in engine.generate.call_args_list], [80, 80, 21])
        def oversized(*_args, callback, **_kwargs):
            self.assertEqual(callback(np.zeros(8000 * 60 + 1, dtype=np.float32), .5), 0)
            return SimpleNamespace(sample_rate=8000, samples=np.ones(800, dtype=np.float32))
        engine.generate.side_effect = oversized
        self.assert_code("invalid_audio", lambda: output._generate(engine, PRIVATE_TEXT, self.model, 0))

    def test_vits_callback_continues_all_sentences_using_actual_runtime_convention(self):
        def generate(_text, *, sid, speed, callback):
            pieces = []
            for index in range(2):
                samples = np.ones(100, dtype=np.float32)
                pieces.append(samples)
                if callback(samples, (index + 1) / 2) == 0:
                    break
            return SimpleNamespace(sample_rate=8000, samples=np.concatenate(pieces))
        engine = SimpleNamespace(generate=generate)
        result = output._generate(engine, "你好。我是小鸭。", self.model, 0)
        self.assertEqual(len(result), 200)


if __name__ == "__main__":
    unittest.main()
