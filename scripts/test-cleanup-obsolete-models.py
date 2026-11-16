#!/usr/bin/env python3
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('cleanup', Path(__file__).with_name('cleanup-obsolete-models.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CleanupTests(unittest.TestCase):
    def test_removes_only_reviewed_inactive_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = ['cyberglm', 'cyberseek-sglang', 'cyberseek-q2-download',
                     'cyberseek-q2', 'cyberseek-q2-llama.cpp',
                     'cyberseek-q2-llama.cpp-no-peer-copy-c49edf0963a7',
                     'proxy-state', 'cyberseek-unreviewed']
            for name in names:
                (root / name).mkdir()
                (root / name / 'data').write_text('fixture-' + name)
            with contextlib.redirect_stdout(io.StringIO()):
                inventory = module.cleanup(root)
                self.assertEqual(len(inventory['entries']), 3)
                self.assertTrue((root / 'cyberglm').exists())
                result = module.cleanup(root, apply=True)
            for name in names[:3]:
                self.assertFalse((root / name).exists())
            for name in names[3:]:
                self.assertEqual((root / name / 'data').read_text(), 'fixture-' + name)
            self.assertEqual(result['unreviewed_model_directory_count'], 1)

    def test_skips_symlink_and_nested_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active = root / 'cyberseek-q2'
            active.mkdir()
            (active / 'weight').write_text('keep')
            (root / 'cyberglm').symlink_to(active, target_is_directory=True)
            nested = root / 'cyberseek-sglang' / 'mounted'
            nested.mkdir(parents=True)
            with patch.object(module.os.path, 'ismount', side_effect=lambda p: Path(p) == nested.resolve()):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = module.cleanup(root, apply=True)
            self.assertEqual((active / 'weight').read_text(), 'keep')
            self.assertTrue(nested.exists())
            self.assertEqual({e['action'] for e in result['entries']},
                             {'skipped-not-a-plain-directory', 'skipped-nested-mount'})


if __name__ == '__main__':
    unittest.main()
