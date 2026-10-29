"""Identity persistence and per-turn activation; no cloud or hardware."""

from copy import deepcopy
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from radxa_duck.identity import DEFAULT_PROFILE, IdentityError, IdentityStore


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = IdentityStore(self.root)
        self.addCleanup(self.store.close)

    def save(self, revision=0, **fields):
        return self.store.save(expected_revision=revision, profile={**DEFAULT_PROFILE, **fields})

    def test_default_does_not_create_profile_and_reads_are_copied(self):
        value = self.store.snapshot()
        self.assertEqual((value['revision'], value['active_revision']), (0, 0))
        self.assertFalse((self.root / 'identity.json').exists())
        value['profile']['name'] = 'changed'
        self.assertEqual(self.store.snapshot()['profile']['name'], DEFAULT_PROFILE['name'])

    def test_save_is_private_persistent_and_only_next_turn_activates_it(self):
        saved = self.save(name='达达', description='第一行\n第二行')
        self.assertEqual((saved['revision'], saved['active_revision']), (1, 0))
        self.assertEqual(self.store.for_turn()['name'], '达达')
        old = self.store.for_turn()
        saved = self.save(1, name='小达')
        self.assertEqual((saved['revision'], saved['active_revision']), (2, 1))
        self.assertEqual(old['name'], '达达')
        self.assertEqual(self.store.for_turn()['name'], '小达')
        self.assertEqual(self.store.summary()['active_revision'], 2)
        self.assertEqual((self.root / 'identity.json').stat().st_mode & 0o777, 0o600)
        with self.subTest('restart has no pretend completed interaction'):
            reopened = IdentityStore(self.root)
            self.addCleanup(reopened.close)
            self.assertEqual(reopened.snapshot()['profile']['name'], '小达')
            self.assertEqual(reopened.snapshot()['active_revision'], 0)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['identity.json', 'identity.lock'])

    def test_concurrent_writers_with_same_version_cannot_lose_updates(self):
        other = IdentityStore(self.root)
        self.addCleanup(other.close)
        barrier = threading.Barrier(2)
        results = []
        def write(store, name):
            barrier.wait()
            try:
                results.append(store.save(expected_revision=0, profile={**DEFAULT_PROFILE, 'name': name}))
            except IdentityError as exc:
                results.append(exc.code)
        threads = [threading.Thread(target=write, args=(self.store, 'A')),
                   threading.Thread(target=write, args=(other, 'B'))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sum(isinstance(item, dict) for item in results), 1)
        self.assertIn('conflict', results)
        self.assertEqual(self.store.snapshot()['revision'], 1)

    def test_invalid_fields_unicode_and_revisions_do_not_create_file(self):
        invalid = [{'name': ''}, {'name': ' '}, {'name': 'x' * 41}, {'name': '🦆' * 21},
                   {'description': '\x00'}, {'personality': '\x85'}, {'expression_style': '\ud800'},
                   {'description': 'x' * 2001}, {'personality': 'x' * 1001},
                   {'expression_style': 'x' * 1001}, {'extra': 'value'}]
        for fields in invalid:
            with self.subTest(fields=repr(fields)), self.assertRaises(IdentityError):
                self.save(**fields)
        for revision in (True, -1, 1.0, '0', 2 ** 53 - 1):
            with self.subTest(revision=revision), self.assertRaises(IdentityError):
                self.save(revision)
        self.assertFalse((self.root / 'identity.json').exists())

    def test_broken_or_linked_files_are_not_overwritten(self):
        self.save()
        path = self.root / 'identity.json'
        original = path.read_bytes()
        for raw in (b'not json', b'{"schema_version":1,"schema_version":1}', b'x' * 32769):
            path.write_bytes(raw)
            with self.assertRaises(IdentityError):
                self.save(1, name='replacement')
            self.assertEqual(path.read_bytes(), raw)
        path.write_bytes(original)
        linked = self.root / 'linked.json'
        os.link(path, linked)
        with self.assertRaises(IdentityError):
            self.store.snapshot()
        linked.unlink()
        path.chmod(0o644)
        with self.assertRaises(IdentityError):
            self.store.snapshot()
        path.chmod(0o600)
        target = self.root / 'target.json'
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaises(IdentityError):
            self.store.snapshot()
        self.assertEqual(target.read_bytes(), original)

    def test_failed_atomic_replace_preserves_old_version_and_cleans_temporary(self):
        original = self.save()
        with patch('radxa_duck.identity.os.replace', side_effect=OSError('private path')):
            with self.assertRaises(IdentityError) as caught:
                self.save(1, name='lost')
        self.assertEqual(caught.exception.code, 'config_unavailable')
        self.assertEqual(self.store.snapshot(), original)
        self.assertFalse(any(p.name.startswith('.identity-') for p in self.root.iterdir()))

    def test_memory_defaults_cannot_pretend_to_persist(self):
        store = IdentityStore()
        self.addCleanup(store.close)
        self.assertEqual(store.snapshot()['revision'], 0)
        with self.assertRaises(IdentityError):
            store.save(expected_revision=0, profile=deepcopy(DEFAULT_PROFILE))


if __name__ == '__main__':
    unittest.main()
