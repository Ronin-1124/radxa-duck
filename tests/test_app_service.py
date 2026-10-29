"""Application scheduling with simulated cloud, microphone, camera and speaker."""

from pathlib import Path
from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import Mock

from radxa_duck.actions import ActionError, ActionService
from radxa_duck.app_service import AppService, AppServiceError
from radxa_duck.cloud_client import CloudClientError
from radxa_duck.speech import SpeechError
from radxa_duck.vision_service import VisionServiceError


JPEG = b'\xff\xd8private frame\xff\xd9'


class Capture:
    def __init__(self, camera=False):
        self.owner = None
        self.session = 0
        self.starts = []
        self.stops = []
        self.ready = True
        self.camera = camera
        self.partial = ''
        self.segments = []
        self.clear_transcript = Mock()
        self.latest_frame = Mock(return_value=SimpleNamespace(jpeg=JPEG))

    def snapshot(self, owner=None):
        return {'ready': self.ready, 'state': 'streaming' if self.camera and self.owner else 'listening' if self.owner else 'idle',
                'owned': owner is not None and self.owner == owner, 'session_id': str(self.session),
                'partial_text': self.partial, 'segments': list(self.segments)}

    def start(self, owner, settings=None, *, private=False):
        if self.owner is not None:
            error = VisionServiceError if self.camera else SpeechError
            raise error('busy', 'Another owner is using the device')
        self.owner = owner
        self.session += 1
        self.partial = ''
        self.segments = []
        self.starts.append((owner, settings))

    def stop(self, owner):
        if self.owner is None:
            return
        if self.owner != owner:
            raise SpeechError('not_owner', 'Another owner')
        self.stops.append(owner)
        self.owner = None


class Cloud:
    def __init__(self):
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.error = None
        self.closed = False
        self.text = '你好，小鸭'

    def stream(self, text, *, cancelled, image_jpeg=None, history=()):
        self.calls.append((text, image_jpeg, history))
        self.started.set()
        yield {'type': 'delta', 'text': self.text[:2]}
        while not self.release.wait(.01):
            if cancelled.is_set():
                raise CloudClientError('cancelled')
        if self.error:
            raise self.error
        # Intentionally yield after cancellation to check rejection of late data.
        yield {'type': 'delta', 'text': self.text[2:]}
        yield {'type': 'done', 'revision': 1, 'finish_reason': 'stop'}

    def close(self):
        self.closed = True


class DelayedStopCapture(Capture):
    """Model asynchronous capture teardown without releasing hardware early."""

    def __init__(self):
        super().__init__()
        self.stop_called = threading.Event()
        self.release = threading.Event()

    def snapshot(self, owner=None):
        if self.stop_called.is_set() and self.release.is_set():
            self.owner = None
        return super().snapshot(owner)

    def start(self, owner, settings=None, *, private=False):
        super().start(owner, settings, private=private)
        self.stop_called.clear()

    def stop(self, owner):
        if self.owner != owner:
            raise SpeechError('not_owner', 'Another owner')
        self.stops.append(owner)
        self.stop_called.set()


class Output:
    def __init__(self, mic):
        self.mic = mic
        self.calls = []
        self.closed = False

    def snapshot(self):
        return {'available': True}

    def speak(self, text, *, cancelled, on_phase):
        if self.mic.owner is not None:
            raise AssertionError('Playback began while microphone was still owned')
        on_phase('synthesizing')
        on_phase('playing')
        self.calls.append(text)
        return {'audio_seconds': 1}

    def close(self):
        self.closed = True


class AppServiceTests(unittest.TestCase):
    def setUp(self):
        self.mic, self.camera = Capture(), Capture(camera=True)
        self.cloud = Cloud()
        self.store = SimpleNamespace(snapshot=lambda: {'configured': True, 'base_url': 'https://api.deepseek.com/v1',
                                                       'model': 'deepseek-flash', 'revision': 1})
        self.output = Output(self.mic)
        self.service = AppService(self.mic, self.camera, cloud=self.cloud,
                                  cloud_config=self.store, output=self.output)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.cloud.release.set()
        self.service.close(timeout=3)

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        with self.service._condition:
            while not predicate(self.service.snapshot('owner')):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.fail(str(self.service.snapshot('owner')))
                self.service._condition.wait(timeout=min(.02, remaining))
        return self.service.snapshot('owner')

    def ended(self):
        self.wait(lambda s: s['turn_id'] is not None and s['state'] in ('waiting', 'listening'))
        return [e for e in self.service.events('owner', 0) if e['type'] == 'turn_end'][-1]

    def assert_code(self, code, operation):
        with self.assertRaises(AppServiceError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def test_capabilities_and_passive_session_do_not_open_hardware(self):
        before = self.service.capabilities()
        self.assertFalse(before['capabilities']['companion']['proactive'])
        self.assertFalse(before['capabilities']['image_input']['verified'])
        self.service.start('owner', scene='companion', speak=True)
        self.assertEqual(self.service.snapshot('owner')['state'], 'waiting')
        self.assertEqual(self.mic.starts, [])
        self.assertEqual(self.camera.starts, [])

        self.assertEqual(self.cloud.calls, [])
        self.assertEqual(self.output.calls, [])
        self.assert_code('invalid_request', lambda: self.service.set_listening('owner', True))

    def test_synthesis_and_playback_feedback_distinguish_failure_stages(self):
        from radxa_duck.speech_output import SpeechOutputError
        def fail(text, *, cancelled, on_phase):
            on_phase('synthesizing')
            on_phase('playing')
            raise SpeechOutputError('playback_failed')
        self.output.speak = fail
        self.service.start('owner', scene='voice-chat', speak=True)
        self.service.submit('owner', request_id='voice-feedback', text='hello')
        self.wait(lambda s: s['error'] is not None)
        voice = self.service.voice_feedback()
        self.assertEqual(voice['synthesis']['state'], 'succeeded')
        self.assertEqual(voice['playback']['state'], 'failed')
        self.assertEqual(voice['playback']['error_code'], 'playback_failed')
        self.assertEqual(voice['playback']['task_id'], self.service.snapshot('owner')['turn_id'])

    def test_typed_chat_streams_and_uses_only_bounded_text_history(self):
        self.service.start('owner', scene='voice-chat')
        self.service.submit('owner', request_id='first-turn', text='你好')
        self.assertEqual(self.ended()['state'], 'succeeded')
        events = self.service.events('owner', 0)
        self.assertEqual(''.join(e['text'] for e in events if e['type'] == 'answer_delta'), self.cloud.text)
        self.assertEqual(self.service.snapshot('owner')['reply'], self.cloud.text)
        self.service.submit('owner', request_id='second-turn', text='继续')
        self.ended()
        self.assertEqual(self.cloud.calls[-1][2][0], {'role': 'user', 'content': '你好'})
        self.assertEqual(self.mic.starts, [])

    def test_duplicate_submission_is_idempotent_before_and_after_completion(self):
        self.service.start('owner', scene='companion')
        self.cloud.release.clear()
        self.service.submit('owner', request_id='same-request', text='test')
        self.service.submit('owner', request_id='same-request', text='test')
        self.assert_code('busy', lambda: self.service.submit('owner', request_id='other-request', text='test'))
        self.cloud.release.set()
        self.ended()
        self.service.submit('owner', request_id='same-request', text='test')
        self.assertEqual(len(self.cloud.calls), 1)

    def delayed_microphone(self):
        mic = DelayedStopCapture()
        self.mic = self.service._speech = self.output.mic = mic
        self.addCleanup(mic.release.set)
        return mic

    def test_listen_stop_waits_for_microphone_release_before_reporting_waiting(self):
        mic = self.delayed_microphone()
        self.service.start('owner', scene='voice-chat', listen=True)
        self.wait(lambda s: len(mic.starts) == 1)
        self.service.set_listening('owner', False)
        state = self.service.snapshot('owner')
        self.assertFalse(state['listening'])
        self.assertEqual(state['state'], 'stopping')
        self.assertTrue(mic.stop_called.wait(2))
        self.assertIsNotNone(mic.owner)
        self.assertEqual(self.service.snapshot('owner')['state'], 'stopping')
        mic.release.set()
        self.wait(lambda s: s['state'] == 'waiting')
        self.assertIsNone(mic.owner)
        self.assertEqual(len(mic.starts), 1)

    def test_listen_stop_before_capture_start_reports_waiting_without_opening_mic(self):
        with self.service._condition:
            self.service.start('owner', scene='voice-chat', listen=True)
            self.service.set_listening('owner', False)
            state = self.service.snapshot('owner')
            self.assertFalse(state['listening'])
            self.assertEqual(state['state'], 'waiting')
        self.assertEqual(self.mic.starts, [])

    def test_listen_stop_during_reply_finishes_answer_without_reopening_mic(self):
        self.cloud.release.clear()
        self.service.start('owner', scene='voice-chat', listen=True)
        self.wait(lambda s: len(self.mic.starts) == 1)
        self.service.submit('owner', request_id='listen-stop-turn', text='test')
        self.assertTrue(self.cloud.started.wait(2))
        self.service.set_listening('owner', False)
        state = self.service.snapshot('owner')
        self.assertFalse(state['listening'])
        self.assertEqual(state['state'], 'generating')
        self.cloud.release.set()
        self.assertEqual(self.ended()['state'], 'succeeded')
        self.assertEqual(self.service.snapshot('owner')['state'], 'waiting')
        self.assertEqual(len(self.mic.starts), 1)
        self.assertEqual(self.output.calls, [])

    def test_listen_restart_during_release_reopens_only_after_previous_capture_stops(self):
        mic = self.delayed_microphone()
        self.service.start('owner', scene='voice-chat', listen=True)
        self.wait(lambda s: len(mic.starts) == 1)
        self.service.set_listening('owner', False)
        self.assertTrue(mic.stop_called.wait(2))
        self.service.set_listening('owner', True)
        self.assertEqual(len(mic.starts), 1)
        mic.release.set()
        state = self.wait(lambda s: len(mic.starts) == 2 and s['state'] == 'listening')
        self.assertTrue(state['listening'])
        self.assertIsNotNone(mic.owner)

    def test_cancel_discards_late_deltas_and_never_plays_or_restarts_recording(self):
        self.service.start('owner', scene='voice-chat', speak=True)
        self.cloud.release.clear()
        self.service.submit('owner', request_id='cancel-turn', text='private text')
        self.assertTrue(self.cloud.started.wait(2))
        self.service.cancel('owner')
        self.cloud.release.set()
        ended = self.ended()
        self.assertEqual(ended['state'], 'cancelled')
        self.assertEqual(self.output.calls, [])
        self.assertFalse(self.service.snapshot('owner')['listening'])
        self.assertEqual(self.mic.starts, [])

    def test_stop_clears_owned_text_and_events_and_rejects_other_owners(self):
        self.service.start('owner', scene='companion')
        self.service.submit('owner', request_id='private-turn', text='private text')
        self.ended()
        other = self.service.snapshot('other')
        self.assertEqual(other['reply'], '')
        self.assertEqual(other['transcript'], '')
        self.assertEqual(self.service.events('other', 0), [])
        self.assert_code('not_owner', lambda: self.service.stop('other'))
        self.service.stop('owner')
        self.wait(lambda s: s['state'] == 'idle')
        self.assertEqual(self.service.snapshot('owner')['reply'], '')
        self.assertEqual(self.service._history, [])
        self.assertEqual(self.service.events('owner', 0), [])

    def test_explicit_asr_turn_stops_microphone_before_output_then_resumes(self):
        self.service.start('owner', scene='voice-chat', listen=True, speak=True)
        self.wait(lambda s: bool(self.mic.starts))
        self.mic.segments = [{'id': 1, 'text': '你好'}]
        self.wait(lambda s: bool(self.output.calls) and len(self.mic.starts) >= 2)
        self.assertEqual(self.cloud.calls[0][0], '你好')
        self.assertEqual(self.output.calls, [self.cloud.text])
        self.assertTrue(self.mic.stops)
        self.mic.clear_transcript.assert_called()
        self.service.cancel('owner')
        self.wait(lambda s: self.mic.owner is None)
        self.assertFalse(self.service.snapshot('owner')['listening'])

    def test_microphone_busy_does_not_stop_another_owner(self):
        self.mic.owner = 'other-page'
        self.service.start('owner', scene='voice-chat', listen=True)
        self.wait(lambda s: s['state'] == 'error')
        self.assertEqual(self.mic.owner, 'other-page')
        self.assertEqual(self.mic.stops, [])
        self.service.stop('owner')
        self.wait(lambda s: s['state'] == 'idle')
        self.assertEqual(self.mic.owner, 'other-page')

    def test_explicit_listen_retry_clears_capture_error_after_successful_start(self):
        self.mic.owner = 'other-page'
        self.service.start('owner', scene='voice-chat', listen=True)
        state = self.wait(lambda s: s['state'] == 'error')
        self.assertEqual(state['error']['code'], 'device_unavailable')
        self.mic.owner = None
        self.service.set_listening('owner', True)
        state = self.wait(lambda s: len(self.mic.starts) == 1)
        self.assertEqual(state['state'], 'listening')
        self.assertTrue(state['listening'])
        self.assertIsNone(state['error'])
        self.assertEqual(self.cloud.calls, [])

    def test_image_question_requires_explicit_capture_and_releases_before_upload(self):
        self.service.start('owner', scene='image-qa')
        self.assert_code('capture_required', lambda: self.service.submit('owner', request_id='image-turn', text='看到了什么'))
        self.service.submit('owner', request_id='image-turn', text='看到了什么', capture_image=True)
        self.assertEqual(self.ended()['state'], 'succeeded')
        self.assertEqual(self.cloud.calls[0][1], JPEG)
        self.assertEqual(self.camera.starts[0][1], {'detection_enabled': False, 'max_fps': 1})
        self.assertIsNone(self.camera.owner)
        self.assertEqual(self.mic.starts, [])

    def test_busy_camera_is_not_shared_or_stopped(self):
        self.camera.owner = 'vision-page'
        self.service.start('owner', scene='image-qa')
        self.service.submit('owner', request_id='busy-image', text='what', capture_image=True)
        self.assertEqual(self.ended()['state'], 'failed')
        self.assertEqual(self.cloud.calls, [])
        self.assertEqual(self.camera.owner, 'vision-page')
        self.assertEqual(self.camera.stops, [])

    def test_partial_cloud_failure_remains_visible_without_speech_or_motion(self):
        self.service.start('owner', scene='voice-chat', speak=True)
        self.cloud.error = CloudClientError('request_rejected')
        self.service.submit('owner', request_id='failed-turn', text='test')
        ended = self.ended()
        self.assertEqual(ended['state'], 'failed')
        self.assertEqual(ended['error']['code'], 'request_rejected')
        self.assertEqual(self.service.snapshot('owner')['reply'], self.cloud.text[:2])
        self.assertEqual(self.output.calls, [])

    def test_unknown_exceptions_do_not_leak_private_details(self):
        self.service.start('owner', scene='companion')
        self.cloud.error = RuntimeError('secret provider text')
        self.service.submit('owner', request_id='error-turn', text='test')
        self.assertEqual(self.ended()['error']['code'], 'operation_failed')
        self.assertNotIn('secret provider text', str(self.service.snapshot('owner')))

    def test_maintenance_cancels_inflight_work_and_does_not_auto_resume(self):
        self.service.start('owner', scene='companion')
        self.cloud.release.clear()
        self.service.submit('owner', request_id='suspend-turn', text='test')
        self.assertTrue(self.cloud.started.wait(2))
        self.service.suspend(timeout=2)
        self.assert_code('maintenance', lambda: self.service.start('owner', scene='companion'))
        self.service.resume()
        self.assertEqual(self.service.snapshot('owner')['state'], 'idle')
        self.assertEqual(self.mic.starts, [])

    def test_controller_absent_is_explicitly_unavailable(self):
        self.assertEqual(self.service.actions_snapshot()['actions'], [])
        self.assert_code('controller_unavailable', lambda: self.service.start('owner', scene='motion'))

    def test_capabilities_match_camera_and_controller_readiness(self):
        self.camera.ready = False
        image = next(scene for scene in self.service.capabilities()['scenes'] if scene['id'] == 'image-qa')
        self.assertFalse(image['available'])
        self.assertEqual(image['reason'], 'camera_unavailable')
        self.assert_code('camera_unavailable', lambda: self.service.start('owner', scene='image-qa'))
        self.service._cloud_config = None
        self.service._actions = SimpleNamespace(snapshot=lambda: {'available': True, 'actions': []})
        motion = next(scene for scene in self.service.capabilities()['scenes'] if scene['id'] == 'motion')
        self.assertTrue(motion['available'])
        self.assertIsNone(motion['reason'])

    def test_cancel_at_publish_lock_prevents_late_delta_and_history_commit(self):
        entered, release = threading.Event(), threading.Event()
        self.service.start('owner', scene='companion')
        original = self.service._check
        paused = False
        def checkpoint(cancelled):
            nonlocal paused
            original(cancelled)
            if self.cloud.started.is_set() and not paused:
                paused = True
                entered.set()
                if not release.wait(2):
                    raise AssertionError('Publish gate was not released')
        self.service._check = checkpoint
        try:
            self.service.submit('owner', request_id='publish-race', text='test')
            self.assertTrue(entered.wait(2))
            self.service.cancel('owner')
        finally:
            release.set()
        self.assertEqual(self.ended()['state'], 'cancelled')
        self.assertEqual([e for e in self.service.events('owner', 0) if e['type'] == 'answer_delta'], [])
        self.assertEqual(self.service._history, [])

    def test_long_answer_has_explicit_speech_limit_without_losing_answer(self):
        self.cloud.text = '文' * 1201
        self.service.start('owner', scene='companion', speak=True)
        self.service.submit('owner', request_id='long-answer', text='test')
        end = self.ended()
        self.assertEqual(end['error']['code'], 'speech_text_limit')
        self.assertEqual(self.service.snapshot('owner')['reply'], self.cloud.text)
        self.assertEqual(self.output.calls, [])

    def test_unconfigured_cloud_and_tts_fail_before_any_capture(self):
        self.service._cloud_config = None
        self.assert_code('cloud_unconfigured', lambda: self.service.start('owner', scene='voice-chat', listen=True))
        self.service._cloud_config = self.store
        self.service._output = None
        self.assert_code('tts_unavailable', lambda: self.service.start('owner', scene='voice-chat', speak=True))
        self.assertEqual(self.mic.starts, [])


class ActionServiceTests(unittest.TestCase):
    def test_only_whitelisted_acknowledged_actions_succeed(self):
        adapter = SimpleNamespace(list_actions=lambda: [{'id': 'wave', 'label': '挥手'}],
                                  execute=lambda action_id, cancelled: {'action_id': action_id, 'state': 'succeeded'})
        service = ActionService(adapter)
        self.assertEqual(service.execute('wave', cancelled=threading.Event())['state'], 'succeeded')
        with self.assertRaises(ActionError):
            service.execute('arbitrary-command', cancelled=threading.Event())
        adapter.execute = lambda *a, **kw: {'state': 'accepted'}
        with self.assertRaises(ActionError):
            service.execute('wave', cancelled=threading.Event())


if __name__ == '__main__':
    unittest.main()
