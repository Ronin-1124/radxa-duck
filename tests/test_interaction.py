"""Semantic decisions, single-frame consent, shared leases and speaker boundaries."""

from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from radxa_duck.app_service import AppService
from radxa_duck.cloud_client import CloudClientError
from radxa_duck.duck_output import DuckOutput
from radxa_duck.interaction import image_support, parse_decision, requests_image
from radxa_duck.speech_output import SpeechOutputError
from test_app_service import Capture, JPEG


BANK = Path(__file__).parents[1] / 'assets/microduck'
CONFIG = {'configured': True, 'base_url': 'https://api.deepseek.com/v1', 'model': 'deepseek-flash', 'revision': 2}


class DecisionCloud:
    def __init__(self):
        self.plans = [{'reply': '你好呀', 'sound': 'greet', 'look': False}]
        self.calls = []
        self.on_call = lambda: None

    def pin_config(self):
        return dict(CONFIG)

    def stream(self, text, *, cancelled, **kwargs):
        self.calls.append(kwargs)
        self.on_call()
        raw = json.dumps(self.plans.pop(0), ensure_ascii=False)
        yield {'type': 'delta', 'text': raw[:7]}
        yield {'type': 'delta', 'text': raw[7:]}
        yield {'type': 'done', 'revision': 2, 'finish_reason': 'stop'}

    def close(self):
        pass


class DuckSpeaker:
    def __init__(self, mic):
        self.calls, self.mic = [], mic

    def snapshot(self):
        return {'available': True, 'kind': 'duck', 'sounds': ['greet', 'coo', 'chirp']}

    def play(self, sound, *, cancelled, on_phase):
        assert self.mic.owner is None, 'Microphone must be released before playback'
        self.calls.append(sound)
        if sound != 'none':
            on_phase('playing')
        return {'played': sound != 'none', 'audio_seconds': .1}

    def close(self):
        pass


class SharedCamera:
    def __init__(self):
        self.leases = {'resident-brain'}
        self.acquired, self.released = [], []

    def acquire(self, owner):
        self.leases.add(owner)
        self.acquired.append(owner)

    def release(self, owner):
        self.leases.remove(owner)
        self.released.append(owner)

    def snapshot(self):
        return {'state': 'streaming'}

    def frame(self):
        return SimpleNamespace(jpeg=JPEG)


class DuckAppTests(unittest.TestCase):
    def setUp(self):
        self.mic, self.camera = Capture(), Capture(camera=True)
        self.cloud, self.observer = DecisionCloud(), SharedCamera()
        self.speaker = DuckSpeaker(self.mic)
        self.enabled = True
        self.apps = AppService(self.mic, self.camera, cloud=self.cloud,
                               cloud_config=SimpleNamespace(snapshot=lambda: dict(CONFIG)), output=self.speaker,
                               vision_observer=self.observer, visual_policy=lambda: self.enabled)
        self.addCleanup(self.apps.close)
        self.apps.start('owner', scene='voice-chat', speak=True)

    def submit(self, text='你好'):
        self.apps.submit('owner', request_id='request_123', text=text)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            events = self.apps.events('owner', 0)
            ended = [e for e in events if e['type'] == 'turn_end']
            if ended:
                return ended[-1]
            threading.Event().wait(.005)
        self.fail('Interaction did not finish')

    def test_semantic_sound_and_no_synthesis(self):
        self.assertEqual(self.submit()['state'], 'succeeded')
        self.assertEqual(self.speaker.calls, ['greet'])
        self.assertEqual(self.apps.snapshot('owner')['reply'], '你好呀')
        feedback = self.apps.voice_feedback()
        self.assertEqual(feedback['synthesis']['state'], 'skipped')
        self.assertEqual(feedback['playback']['state'], 'succeeded')
        self.assertFalse(feedback['tts_available'])
        self.assertIsNone(self.cloud.calls[0]['image_jpeg'])
        self.assertTrue(self.cloud.calls[0]['json_mode'])

    def test_explicit_question_gets_one_shared_frame_without_stopping_resident(self):
        self.cloud.plans = [{'reply': '我看一下', 'sound': 'inquire', 'look': True},
                            {'reply': '画面中有一个杯子', 'sound': 'chirp', 'look': False}]
        self.assertEqual(self.submit('请看看眼前有什么')['state'], 'succeeded')
        self.assertEqual([c['image_jpeg'] for c in self.cloud.calls], [None, JPEG])
        self.assertEqual(self.observer.leases, {'resident-brain'})
        self.assertEqual(self.observer.acquired, self.observer.released)
        self.assertEqual(self.camera.starts, [])
        self.assertEqual(self.speaker.calls, ['chirp'])
        self.assertTrue(self.apps.image_capability()['verified'])
        self.assertNotIn('我看一下', str(self.apps._history))
        self.assertNotIn('private frame', str(self.apps._history))

    def test_model_cannot_request_picture_without_current_consent(self):
        self.cloud.plans[0]['look'] = True
        result = self.submit('你好')
        self.assertEqual(result['error']['code'], 'invalid_response')
        self.assertEqual(self.observer.acquired, [])
        self.assertEqual(self.speaker.calls, [])

    def test_disabled_camera_is_not_bypassed(self):
        self.enabled = False
        self.cloud.plans[0]['look'] = True
        self.assertEqual(self.submit('看看眼前有什么')['state'], 'failed')
        self.assertEqual(self.observer.acquired, [])

    def test_policy_is_rechecked_after_cloud_understanding(self):
        self.cloud.plans[0]['look'] = True
        self.cloud.on_call = lambda: setattr(self, 'enabled', False)
        self.assertEqual(self.submit('看看眼前有什么')['error']['code'], 'camera_disabled')
        self.assertEqual(self.observer.acquired, [])

    def test_unknown_sound_cannot_execute_or_play(self):
        self.cloud.plans[0]['sound'] = '../../bin/reboot'
        self.assertEqual(self.submit()['state'], 'failed')
        self.assertEqual(self.speaker.calls, [])

    def test_silence_skips_both_synthesis_and_playback(self):
        self.cloud.plans[0]['sound'] = 'none'
        self.assertEqual(self.submit('先不要发出声音')['state'], 'succeeded')
        feedback = self.apps.voice_feedback()
        self.assertEqual(feedback['synthesis']['state'], 'skipped')
        self.assertEqual(feedback['playback']['state'], 'skipped')

    def test_next_turn_receives_valid_structured_history_with_actual_sound(self):
        self.assertEqual(self.submit()['state'], 'succeeded')
        history = self.apps._history
        self.assertEqual(json.loads(history[1]['content']), {'reply': '你好呀', 'sound': 'greet', 'look': False})
        self.cloud.plans.append({'reply': '陪着你呢', 'sound': 'coo', 'look': False})
        self.apps.submit('owner', request_id='request_456', text='我有点难过')
        deadline = time.monotonic()+2
        while len(self.cloud.calls) < 2 and time.monotonic() < deadline:
            threading.Event().wait(.005)
        self.assertEqual(self.cloud.calls[-1]['history'][1], history[1])


class DecisionTests(unittest.TestCase):
    def test_image_turn_keeps_pinned_model_when_settings_change_before_capture(self):
        current = dict(CONFIG)
        cloud, camera = DecisionCloud(), SharedCamera()
        def pin():
            current['model'] = 'unknown-new-model'
            return dict(CONFIG)
        cloud.pin_config = pin
        apps = AppService(Capture(), Capture(camera=True), cloud=cloud,
                          cloud_config=SimpleNamespace(snapshot=lambda: dict(current)), vision_observer=camera)
        self.addCleanup(apps.close)
        apps.start('owner', scene='image-qa')
        apps.submit('owner', request_id='pinned_image_turn', text='这是什么', capture_image=True)
        deadline = time.monotonic()+2
        while time.monotonic()<deadline:
            ended = [e for e in apps.events('owner', 0) if e['type']=='turn_end']
            if ended:
                self.assertEqual(ended[-1]['state'], 'succeeded')
                self.assertEqual(camera.acquired, camera.released)
                self.assertEqual(cloud.calls[0]['runtime_config']['model'], 'deepseek-flash')
                return
            threading.Event().wait(.005)
        self.fail('Pinned image turn did not finish')

    def test_image_support_is_provider_and_model_specific(self):
        self.assertTrue(image_support(CONFIG))
        for config in ({**CONFIG, 'base_url': 'https://api.deepseek.com.evil.test'},
                       {**CONFIG, 'model': 'deepseek-v4-pro'}, {}, {**CONFIG, 'model': 'unknown'}):
            self.assertFalse(image_support(config))

    def test_consent_is_positive_and_single_utterance(self):
        for text in ['请看看眼前有什么', '这是什么', '你能看到我吗', 'what can you see?', 'look at this']:
            self.assertTrue(requests_image(text), text)
        for text in ['你好', '不要看摄像头', "don't take a picture", '不用拍照，只聊天',
                     '摄像头是什么型号', '我的照片保存在哪里', 'You look nice']:
            self.assertFalse(requests_image(text), text)

    def test_strict_json_has_no_extra_or_duplicate_operations(self):
        valid = {'reply': '你好', 'sound': 'greet', 'look': False}
        self.assertEqual(parse_decision(json.dumps(valid), can_look=False), valid)
        for raw in [json.dumps({**valid, 'shell': 'ls'}), json.dumps({**valid, 'look': 1}),
                    json.dumps({**valid, 'look': True}), json.dumps({**valid, 'sound': ['greet']}),
                    '{"reply":"a","sound":"greet","look":false,"look":false}']:
            with self.assertRaises(CloudClientError):
                parse_decision(raw, can_look=False)


class DuckOutputTests(unittest.TestCase):
    def speaker(self, directory=BANK):
        with patch('radxa_duck.duck_output.shutil.which', return_value='/bin/tool'):
            speaker = DuckOutput(directory)
        self.addCleanup(speaker.close)
        return speaker

    def test_official_bank_is_verified_and_has_six_short_clips(self):
        speaker = self.speaker()
        self.assertTrue(speaker.snapshot()['available'])
        self.assertEqual(len(speaker.snapshot()['sounds']), 6)
        self.assertFalse(speaker.snapshot()['tts_available'])
        with tempfile.TemporaryDirectory() as temp:
            bank = Path(temp) / 'bank'
            shutil.copytree(BANK, bank)
            (bank / 'chirp.wav').write_bytes(b'bad')
            self.assertFalse(self.speaker(bank).snapshot()['available'])

    def test_real_playback_boundary_uses_guard_and_zero_db_pcm(self):
        speaker = self.speaker()
        calls, guard = [], []
        @contextmanager
        def owned(cancelled):
            guard.append('acquired')
            try:
                yield
            finally:
                guard.append('muted_and_released')
        with patch('radxa_duck.duck_output.audio_capture._audio_guard', owned), \
             patch('radxa_duck.duck_output.audio_capture._run', side_effect=lambda *args: calls.append(args[0])):
            result = speaker.play('greet', cancelled=threading.Event())
        self.assertTrue(result['played'])
        self.assertIn(['amixer', '-q', '-c', 'PollenHATC1', 'sset', 'PCM', '100%'], calls)
        self.assertEqual(guard, ['acquired', 'muted_and_released'])
        self.assertEqual(speaker.snapshot()['last_sound']['state'], 'succeeded')

    def test_cancelled_or_unknown_sound_never_starts_audio(self):
        speaker = self.speaker()
        cancelled = threading.Event(); cancelled.set()
        with patch('radxa_duck.duck_output.audio_capture._run') as run:
            for sound in ['greet', '/tmp/other.wav']:
                with self.assertRaises(SpeechOutputError):
                    speaker.play(sound, cancelled=cancelled)
            self.assertFalse(run.called)
