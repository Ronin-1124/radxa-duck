"""Installed configuration validation without opening devices or the network."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from radxa_duck.launcher import load_config, local_origins


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.file = Path(self.directory.name) / 'config.json'
        self.config = json.loads((Path(__file__).parents[1] / 'packaging/config.json').read_text())

    def load(self, **changes):
        self.file.write_text(json.dumps({**self.config, **changes}))
        return load_config(self.file)

    def test_defaults_use_installed_paths_and_local_explicit_origins(self):
        with patch('radxa_duck.launcher.local_origins', return_value={'http://robot.local:8765'}):
            config = self.load()
        self.assertEqual(config['origins'], ('http://robot.local:8765',))
        self.assertEqual(config['model_dir'], Path('/usr/share/radxa-duck/models/asr'))
        self.assertTrue(config['brain_camera_enabled'])
        self.assertTrue(config['microphone_enabled'])
        self.assertIsNone(config['tts_model_dir'])
        self.assertEqual(config['duck_volume'], 100)
        self.assertEqual(config['duck_sound_dir'], Path('/usr/share/radxa-duck/sounds/microduck'))

    def test_older_v1_config_remains_quiet_until_sound_bank_is_configured(self):
        self.config.pop('duck_sound_dir')
        self.config.pop('duck_volume')
        self.assertIsNone(self.load()['duck_sound_dir'])

    def test_invalid_config_is_rejected_without_touching_hardware(self):
        for change in ({'port':True}, {'local_origins':'yes'}, {'host':'invalid host'},
                       {'tts_volume':101}, {'camera_rotation':True}, {'model_dir':'relative'},
                       {'vision_models':{'unknown':'/model'}}, {'origins':'http://robot'},
                       {'state_dir':'/var/lib/../secrets'}, {'schema_version':True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.load(**change)
        with self.assertRaises(ValueError):
            self.load(local_origins=False, origins=[])

    def test_duplicate_keys_and_oversized_config_are_rejected(self):
        self.file.write_text('{"port":1,"port":2}')
        with self.assertRaises(ValueError):
            load_config(self.file)
        self.file.write_bytes(b' ' * 17000)
        with self.assertRaises(ValueError):
            load_config(self.file)

    def test_loopback_origins_are_explicit_and_not_wildcards(self):
        with patch('socket.if_nameindex', return_value=[]), patch('socket.gethostname', return_value='duck'):
            self.assertEqual(local_origins(8765), {'http://localhost:8765', 'http://127.0.0.1:8765',
                'http://[::1]:8765', 'http://duck:8765', 'http://duck.local:8765'})
