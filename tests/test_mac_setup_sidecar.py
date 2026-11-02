"""Regression checks for the zero-magic Tauri sidecar packaging hook."""
import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / 'scripts' / 'ensure_sidecar.py'
spec = importlib.util.spec_from_file_location('bughunter_ensure_sidecar', SCRIPT)
sidecar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sidecar)

class SidecarBuildTests(unittest.TestCase):
    def test_missing_sidecar_needs_build(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(sidecar, 'ROOT', pathlib.Path(root)), mock.patch.object(sidecar, 'BACKEND', pathlib.Path(root)/'backend'):
                self.assertTrue(sidecar.needs_rebuild(pathlib.Path(root) / 'missing'))

    def test_unchanged_sidecar_is_reused(self):
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            (root/'scripts').mkdir(); (root/'backend'/'bughunter').mkdir(parents=True)
            (root/'backend'/'bughunter'/'app.py').write_text('print(1)')
            output = root/'sidecar'; output.write_bytes(b'valid')
            with mock.patch.object(sidecar, 'ROOT', root), mock.patch.object(sidecar, 'BACKEND', root/'backend'):
                self.assertFalse(sidecar.needs_rebuild(output))
                # Updating backend code invalidates a previously generated sidecar.
                import os, time
                os.utime(root/'backend'/'bughunter'/'app.py', (output.stat().st_mtime+10, output.stat().st_mtime+10))
                self.assertTrue(sidecar.needs_rebuild(output))

    def test_host_triple_has_correct_path(self):
        with mock.patch.object(sidecar.subprocess, 'check_output', return_value='aarch64-apple-darwin\n'):
            self.assertTrue(str(sidecar.sidecar_out()).endswith('bughunter-backend-aarch64-apple-darwin'))

if __name__ == '__main__':
    unittest.main()
