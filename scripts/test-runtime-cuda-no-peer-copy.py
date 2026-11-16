#!/usr/bin/env python3
"""Exercise the actual startup validator using tiny local fixtures, without GPUs."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("runtime-cuda-no-peer-copy.sh").read_text()
REVISION = "c49edf0963a76d5e78b2a7278c9bd5b71d26c84f"


class RuntimeCacheTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory) / "model"
        root.mkdir()
        for i in range(1, 8):
            (root / f'DeepSeek-V4.1-Flash-1017-UD-Q2_K_XL-{i:05d}-of-00007.gguf').write_bytes(b'GGUF' + b'\0' * 20)
        (root / 'mmproj-BF16.gguf').write_bytes(b'GGUF' + b'\0' * 20)
        (root / 'MODEL_SOURCE.json').write_text(json.dumps({
            'base_revision': 'dfcee0ec8d6b874c6663eca0fc55d90ab32534c8',
            'patch_revision': '73fa239749dfc09e847ea5e1149626d064224d92',
            'variant': 'UD-Q2_K_XL', 'ablate_experts': 2, 'layers': [11, 28],
        }))
        runtime = Path(str(root) + '-llama.cpp-no-peer-copy-c49edf0963a7')
        (runtime / 'bin').mkdir(parents=True)
        binary = runtime / 'bin/llama-server'
        binary.write_text('#!/bin/sh\nexit 0\n')
        binary.chmod(0o755)
        (runtime / 'BUILD_INFO.json').write_text(json.dumps({
            'revision': REVISION, 'cuda_no_peer_copy': True, 'cuda_architectures': '90',
            'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        }))
        return root, binary

    def run_fixture(self, root):
        script = SCRIPT.replace('target=/data/cyberseek-q2', f'target="{root}"', 1)
        return subprocess.run(['bash', '-c', script], capture_output=True, text=True, timeout=10)

    def test_verified_cache_skips_all_build_network_operations(self):
        with tempfile.TemporaryDirectory() as directory:
            root, _ = self.fixture(directory)
            result = self.run_fixture(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('verified-cached-build', result.stdout)
            self.assertNotIn('pinned-source', result.stdout)

    def test_invalid_cache_never_reuses_binary_or_deletes_model(self):
        for corruption in ['missing-shard', 'bad-binary', 'wrong-provenance']:
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as directory:
                root, binary = self.fixture(directory)
                if corruption == 'missing-shard':
                    (root / 'DeepSeek-V4.1-Flash-1017-UD-Q2_K_XL-00007-of-00007.gguf').unlink()
                elif corruption == 'bad-binary':
                    binary.write_text('changed executable')
                else:
                    (root / 'MODEL_SOURCE.json').write_text('{}')
                result = self.run_fixture(root)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('verified-cached-build', result.stdout)
                self.assertTrue((root / 'DeepSeek-V4.1-Flash-1017-UD-Q2_K_XL-00001-of-00007.gguf').exists())


if __name__ == '__main__':
    unittest.main()
