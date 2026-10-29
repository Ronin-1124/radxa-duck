"""Release boundaries reject tampered assets and unsafe wheel contents."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


spec = importlib.util.spec_from_file_location('duck_packaging', Path(__file__).parents[1] / 'packaging/build_deb.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_installed_entrypoint_exposes_packaged_modules_to_native_children(self):
        installed = self.root/'installed'
        installed.mkdir()
        shutil.copyfile(builder.ROOT/'packaging/entrypoint.py', installed/'entrypoint.py')
        code = installed/'radxa_duck'
        code.mkdir()
        (code/'probe.py').write_text('VALUE = 1\n')
        (code/'launcher.py').write_text('import subprocess,sys\ndef main():\n subprocess.run([sys.executable,"-m","radxa_duck.probe"],check=True)\n')
        result = subprocess.run([sys.executable,'-P',str(installed/'entrypoint.py')],cwd=self.root,
                                env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME')},
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr.decode())

    def test_models_require_exact_size_checksum_and_regular_source(self):
        source = self.root / 'model'
        source.write_bytes(b'model')
        checksum = hashlib.sha256(b'model').hexdigest()
        builder.copy_checked(source, self.root/'output/model', 5, checksum)
        self.assertEqual((self.root/'output/model').read_bytes(), b'model')
        for size, expected in ((4, checksum), (5, '0'*64)):
            with self.assertRaises(ValueError):
                builder.copy_checked(source, self.root/'bad', size, expected)
        linked = self.root/'linked'
        linked.symlink_to(source)
        with self.assertRaises(ValueError):
            builder.copy_checked(linked, self.root/'bad', 5, checksum)

    def test_release_tree_rejects_links_into_private_files(self):
        inputs = self.root/'inputs'
        inputs.mkdir()
        (inputs/'private').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            builder.safe_tree(inputs, self.root/'output')

    def test_wheels_cannot_escape_staging_or_hide_unpinned_dependencies(self):
        (self.root/'packaging').mkdir()
        (self.root/'packaging/runtime-requirements.txt').write_text('example==1.0\n')
        wheelhouse = self.root/'wheels'
        wheelhouse.mkdir()
        wheel = wheelhouse/'example-1.0-py3-none-any.whl'
        def write(name):
            with zipfile.ZipFile(wheel, 'w') as archive:
                archive.writestr('example-1.0.dist-info/METADATA', 'Name: example\nVersion: 1.0\n')
                archive.writestr(name, 'data')
            (self.root/'packaging/runtime-wheels.json').write_text(json.dumps([
                {'filename':wheel.name, 'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest()}]))
        with patch.object(builder, 'ROOT', self.root):
            write('../escape')
            with self.assertRaises(ValueError):
                builder.wheels(wheelhouse, self.root/'out')
            self.assertFalse((self.root/'escape').exists())
            write('example/__init__.py')
            result = builder.wheels(wheelhouse, self.root/'out')
            self.assertEqual(result[0]['name'], 'example')
            self.assertEqual((self.root/'out/example/__init__.py').read_text(), 'data')
