"""Resident scheduling with simulated microphone/cloud/speaker and real identity."""

from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

from radxa_duck.app_service import AppService, AppServiceError
from radxa_duck.brain import BrainError, BrainService, TurnContext
from radxa_duck.cloud_client import CloudClientError
from radxa_duck.identity import DEFAULT_PROFILE, IdentityStore
from radxa_duck.vision_observer import VisionObserver
from test_app_service import Capture, Cloud, Output


class ContextCloud(Cloud):
    def __init__(self):
        super().__init__()
        self.contexts = []

    def stream(self, text, **kwargs):
        self.contexts.append(kwargs.pop('system_prompt', None))
        return super().stream(text, **kwargs)


class BrainTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.identity = IdentityStore(Path(self.directory.name))
        self.context = TurnContext(self.identity)
        self.mic, self.camera = Capture(), Capture(camera=True)
        self.cloud, self.output = ContextCloud(), Output(self.mic)
        self.config = {'configured': True, 'model': 'test-model', 'revision': 1}
        self.store = SimpleNamespace(snapshot=lambda: dict(self.config))
        self.observer = VisionObserver(self.camera)
        self.apps = AppService(self.mic, self.camera, cloud=self.cloud, cloud_config=self.store,
                               output=self.output, context_provider=self.context.for_turn)
        self.brain = BrainService(self.apps, self.mic, self.observer, self.identity,
                                  cloud_config=self.store, context=self.context)
        self.counter = 0
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.cloud.release.set()
        self.brain.close()
        self.apps.close(timeout=3)
        self.observer.close()
        self.identity.close()
        self.directory.cleanup()

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = self.brain.snapshot()
            if predicate(state):
                return state
            threading.Event().wait(.01)
        self.fail(str(self.brain.snapshot()))

    def command(self, action, **extra):
        state = self.wait(lambda s: s['controls'][action])
        self.counter += 1
        value = {'request_id': f'request-{self.counter}', 'action': action,
                 'expected_instance_id': state['instance_id'], 'expected_revision': state['revision'], **extra}
        return value, self.brain.control(value)

    def utterance(self, text='你好'):
        self.wait(lambda s: s['activity'] == 'listening')
        self.mic.segments = [{'id': 1, 'text': text}]
        return self.wait(lambda s: s['task'] is not None and s['task']['state'] == 'running')['task']['id']

    def test_initial_state_and_status_reads_never_acquire_hardware(self):
        state = self.wait(lambda s: s['cloud']['configured'])
        self.assertEqual(state['state'], 'running')
        self.assertEqual(state['activity'], 'idle')
        self.assertEqual(state['cloud']['health'], 'unknown')
        self.assertFalse(state['microphone']['enabled'])
        self.assertFalse(state['camera']['enabled'])
        self.assertEqual(state['motion'], {'available': False, 'state': 'unavailable'})
        revision = state['revision']
        self.assertEqual(self.brain.snapshot()['revision'], revision)
        self.assertEqual((self.mic.starts, self.camera.starts), ([], []))

    def test_configured_resident_microphone_starts_without_any_web_client(self):
        self.brain.close()
        self.brain = BrainService(self.apps, self.mic, self.observer, self.identity,
                                  cloud_config=self.store, context=self.context, microphone_enabled=True)
        self.wait(lambda s: s['microphone']['enabled'] and s['microphone']['active'])
        self.assertEqual(len(self.mic.starts), 1)
        self.command('disable_microphone')
        self.wait(lambda s: not s['microphone']['active'])
        self.brain._tick()
        self.assertEqual(len(self.mic.starts), 1)

    def test_control_is_idempotent_before_version_check_and_rejects_reused_id(self):
        value, result = self.command('pause')
        self.command('resume')
        self.assertEqual(self.brain.control(value), result)
        self.assertEqual(self.brain.snapshot()['state'], 'running')
        for changed in ({**value, 'action': 'resume'}, {**value, 'request_id': 'new-id'}):
            with self.assertRaises(BrainError) as caught:
                self.brain.control(changed)
            self.assertEqual(caught.exception.code, 'conflict')

    def test_old_instance_and_invalid_task_target_cannot_change_state(self):
        state = self.brain.snapshot()
        base = {'request_id': 'old-instance', 'action': 'pause', 'expected_instance_id': 'retired',
                'expected_revision': state['revision']}
        with self.assertRaises(BrainError):
            self.brain.control(base)
        for fields in ({**base, 'expected_revision': True}, {**base, 'extra': True},
                       {**base, 'action': 'cancel_task'}, {**base, 'request_id': 'x' * 129}):
            with self.assertRaises(BrainError) as caught:
                self.brain.control(fields)
            self.assertEqual(caught.exception.code, 'invalid_request')
        self.assertEqual(self.brain.snapshot()['state'], 'running')

    def test_enable_while_paused_does_not_capture_and_resume_preserves_policy(self):
        self.command('pause')
        self.command('enable_microphone')
        self.assertEqual(self.mic.starts, [])
        self.command('resume')
        self.wait(lambda s: s['microphone']['active'])
        self.command('disable_microphone')
        self.wait(lambda s: not s['microphone']['active'])
        self.command('pause')
        self.command('resume')
        self.assertFalse(self.brain.snapshot()['microphone']['enabled'])
        self.assertFalse(self.brain.snapshot()['microphone']['active'])

    def test_pause_allows_current_answer_and_playback_to_complete_without_reopening_mic(self):
        self.cloud.release.clear()
        self.command('enable_microphone')
        task_id = self.utterance()
        self.assertTrue(self.cloud.started.wait(2))
        self.command('pause')
        state = self.brain.snapshot()
        self.assertEqual((state['state'], state['task']['id'], state['task']['state']), ('paused', task_id, 'running'))
        self.cloud.release.set()
        self.wait(lambda s: s['task']['state'] == 'succeeded' and not s['microphone']['active'])
        self.assertEqual(self.output.calls, [self.cloud.text])
        self.assertTrue(self.brain.snapshot()['microphone']['enabled'])
        self.assertEqual(len(self.mic.starts), 1)

    def test_cancel_exact_task_then_resume_listening_without_replaying_answer(self):
        self.cloud.release.clear()
        self.command('enable_microphone')
        task_id = self.utterance()
        self.command('cancel_task', task_id=task_id)
        state = self.wait(lambda s: s['task']['state'] == 'cancelled' and s['activity'] == 'listening')
        self.assertTrue(state['microphone']['enabled'])
        self.assertEqual(self.output.calls, [])
        next_task = self.utterance('下一句')
        self.assertNotEqual(task_id, next_task)
        state = self.brain.snapshot()
        with self.assertRaises(BrainError):
            self.brain.control({'request_id': 'stale-target', 'action': 'cancel_task',
                                'expected_instance_id': state['instance_id'], 'expected_revision': state['revision'],
                                'task_id': task_id})
        self.assertEqual(self.brain.snapshot()['task']['id'], next_task)

    def test_identity_changes_only_next_turn_and_context_reaches_cloud(self):
        self.identity.save(expected_revision=0, profile={**DEFAULT_PROFILE, 'name': '第一身份'})
        self.cloud.release.clear()
        self.command('enable_microphone')
        self.utterance()
        self.assertTrue(self.cloud.started.wait(2))
        saved = self.identity.save(expected_revision=1, profile={**DEFAULT_PROFILE, 'name': '第二身份'})
        self.assertEqual(saved['active_revision'], 1)
        self.assertIn('第一身份', self.cloud.contexts[0])
        self.assertNotIn('第二身份', self.cloud.contexts[0])
        self.assertEqual(self.brain.snapshot()['identity']['active_revision'], 1)
        self.cloud.release.set()
        self.wait(lambda s: s['task']['state'] == 'succeeded' and s['activity'] == 'listening')
        self.cloud.release.clear()
        self.utterance('第二轮')
        self.wait(lambda s: len(self.cloud.contexts) == 2)
        self.assertIn('第二身份', self.cloud.contexts[1])
        self.assertIn('设备事实', self.cloud.contexts[1])
        self.assertEqual(self.brain.snapshot()['identity']['active_revision'], 2)

    def test_legacy_session_cannot_take_over_resident_app_or_microphone(self):
        self.command('enable_microphone')
        self.wait(lambda s: s['activity'] == 'listening')
        with self.assertRaises(AppServiceError) as caught:
            self.apps.start('debug-browser', scene='companion')
        self.assertEqual(caught.exception.code, 'busy')
        self.assertTrue(self.brain.snapshot()['microphone']['active'])

    def test_microphone_can_be_enabled_after_a_previous_capture_session_has_closed(self):
        self.mic.start('debug-browser')
        self.mic.stop('debug-browser')
        self.assertIsNotNone(self.mic.snapshot()['session_id'])
        self.command('enable_microphone')
        self.wait(lambda s: s['activity'] == 'listening')

    def test_camera_perception_is_shared_with_preview_and_enters_turn_context(self):
        from test_vision_routes import FakeVision
        self.brain.close()
        self.observer.close()
        self.camera = FakeVision()
        self.observer = VisionObserver(self.camera)
        self.brain = BrainService(self.apps, self.mic, self.observer, self.identity,
                                  cloud_config=self.store, context=self.context, camera_enabled=True)
        self.wait(lambda s: s['camera']['active'])
        self.observer.acquire('temporary-browser')
        self.observer.release('temporary-browser')
        self.assertTrue(self.brain.snapshot()['camera']['active'])
        self.camera.publish(1, result={'detections': [{'label': 'person', 'score': .9, 'box_xyxy': [0, 0, 10, 20]}]})
        self.brain._tick()
        self.assertIn('person', self.context.for_turn())
        self.command('pause')
        self.wait(lambda s: not s['camera']['active'])
        self.assertTrue(self.brain.snapshot()['camera']['enabled'])

    def test_camera_controls_release_only_brain_lease_and_allow_explicit_retry(self):
        from test_vision_routes import FakeVision
        self.brain.close()
        self.observer.close()
        self.camera = FakeVision()
        self.observer = VisionObserver(self.camera)
        self.brain = BrainService(self.apps, self.mic, self.observer, self.identity,
                                  cloud_config=self.store, context=self.context)
        self.command('enable_camera')
        self.wait(lambda s: s['camera']['active'])
        self.observer.acquire('preview')
        request, result = self.command('disable_camera')
        self.assertTrue(result['status']['camera']['active'])
        self.assertFalse(result['status']['camera']['enabled'])
        self.assertEqual(self.brain.control(request), result)
        self.observer.release('preview')
        self.wait(lambda s: not s['camera']['active'])
        self.command('pause')
        self.command('enable_camera')
        self.assertFalse(self.brain.snapshot()['camera']['active'])
        self.command('resume')
        self.wait(lambda s: s['camera']['active'])

    def test_voice_feedback_reports_explicitly_disabled_input_without_capture(self):
        status = self.wait(lambda s: s['state'] == 'running')
        self.assertEqual(status['voice']['input']['pause_reason'], 'disabled')
        self.assertTrue(status['voice']['reply_available'])
        self.assertEqual(status['voice']['synthesis']['state'], 'idle')
        self.assertEqual(self.mic.starts, [])

    def test_cloud_failure_requires_explicit_resume_and_new_config_resets_health(self):
        self.cloud.release.clear()
        self.cloud.error = CloudClientError('network_error')
        self.command('enable_microphone')
        self.utterance()
        self.cloud.release.set()
        state = self.wait(lambda s: s['state'] == 'error' and not s['microphone']['active'])
        self.assertEqual(state['cloud']['health'], 'error')
        self.cloud.error = None
        self.config['revision'] = 2
        self.brain._sample_configuration()
        self.assertEqual(self.brain.snapshot()['cloud']['health'], 'unknown')
        self.command('resume')
        self.wait(lambda s: s['activity'] == 'listening')

    def test_terminal_task_and_conversation_expire(self):
        self.cloud.release.clear()
        self.command('enable_microphone')
        self.utterance()
        self.cloud.release.set()
        self.wait(lambda s: s['task']['state'] == 'succeeded')
        with self.brain._lock:
            self.brain._task_finished_at = time.monotonic() - 301
        self.brain._tick()
        self.assertIsNone(self.brain.snapshot()['task'])
        self.assertEqual(self.apps._history, [])
        self.assertEqual(self.apps.events(self.brain._owner, 0), [])

    def test_maintenance_blocks_admission_and_never_implicitly_resumes(self):
        self.command('enable_microphone')
        self.wait(lambda s: s['microphone']['active'])
        self.brain.suspend()
        self.wait(lambda s: not s['microphone']['active'])
        self.assertFalse(any(self.brain.snapshot()['controls'].values()))
        self.brain.resume()
        state = self.brain.snapshot()
        self.assertEqual(state['state'], 'paused')
        self.assertFalse(state['microphone']['enabled'])


if __name__ == '__main__':
    unittest.main()
