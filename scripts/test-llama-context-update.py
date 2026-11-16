#!/usr/bin/env python3
"""Check the narrow context migration and preservation of unrelated settings."""
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest

import yaml

spec = importlib.util.spec_from_file_location(
    "context_update", Path(__file__).with_name("prepare-llama-context-update.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

FIXTURE = '''name: dstack
services:
  attest-proxy:
    environment:
      COMPOSE_DIGEST: sha256:SELF
      FAKE_SIGNING_SEED: fixture-only-never-a-real-secret
    image: example/proxy@sha256:abc
  sglang:
    command:
      - /data/runtime/bin/llama-server
      - --model
      - /data/model-00001.gguf
      - --alias
      - adverserial/cyberseek
      - --host
      - 127.0.0.1
      - --api-key
      - fixture-only-backend-key
      - --ctx-size
      - "1048576"
      - --parallel
      - "64"
      - --cache-type-k
      - q8_0
    network_mode: service:attest-proxy
    image: example/runtime@sha256:def
    volumes:
      - type: volume
        source: persistent
        target: /data
        read_only: true
volumes:
  persistent: {}
'''


class ContextUpdateTests(unittest.TestCase):
    def test_capacity_and_preservation(self):
        rendered, report = module.prepare(FIXTURE)
        before, after = yaml.safe_load(FIXTURE), yaml.safe_load(rendered)
        command = after["services"]["sglang"]["command"]
        self.assertEqual(int(command[command.index("--ctx-size") + 1]) // 64, 1_048_576)
        self.assertEqual(command[-3:], ["--no-kv-unified", "--n-predict", "65536"])
        old_proxy = before["services"]["attest-proxy"]
        new_proxy = after["services"]["attest-proxy"]
        old_proxy["environment"]["COMPOSE_DIGEST"] = new_proxy["environment"]["COMPOSE_DIGEST"]
        self.assertEqual(old_proxy, new_proxy)
        self.assertEqual(before["volumes"], after["volumes"])
        self.assertNotIn("fixture-only", json.dumps(report))
        self.assertEqual(rendered.count("fixture-only-backend-key"), 1)
        self.assertEqual(
            module.configuration_digest(rendered, after),
            report["deployment_configuration_digest"],
        )

    def test_refuses_conflicting_runtime_settings(self):
        fixtures = [
            FIXTURE.replace('      - "64"', '      - "32"'),
            FIXTURE.replace('      - 127.0.0.1', '      - 0.0.0.0'),
            FIXTURE.replace('      - --cache-type-k', '      - --kv-unified\n      - --cache-type-k'),
            FIXTURE.replace('      - --cache-type-k', '      - --n-predict\n      - "1024"\n      - --cache-type-k'),
            FIXTURE.replace('    network_mode:', '    environment:\n      LLAMA_ARG_KV_UNIFIED: "1"\n    network_mode:'),
        ]
        for fixture in fixtures:
            with self.subTest(), self.assertRaises(ValueError):
                module.prepare(fixture)

    def test_private_output_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "private.yml"
            module.private_write(output, "private fixture")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                module.private_write(output, "replacement")
            self.assertEqual(output.read_text(), "private fixture")

    def test_peer_copy_fix_preserves_weights_and_is_a_build_option(self):
        fixture = FIXTURE.replace('/data/runtime/bin/llama-server', '/data/cyberseek-q2-llama.cpp/bin/llama-server')
        fixture = fixture.replace('  sglang:', '  model-download:\n    command:\n      - bash\n      - -c\n      - |\n        echo old-builder\n    image: example/runtime@sha256:def\n  sglang:')
        rendered, report = module.prepare(fixture, no_peer_copy_build=True)
        parsed = yaml.safe_load(rendered)
        builder = parsed['services']['model-download']['command'][2]
        engine = parsed['services']['sglang']['command']
        self.assertIn('-DGGML_CUDA_NO_PEER_COPY=ON', builder)
        self.assertNotIn('rm -rf', builder)
        self.assertNotIn('hf download', builder)
        self.assertIn('no-peer-copy-c49edf0963a7', engine[0])
        self.assertEqual(engine[engine.index('--model') + 1], '/data/model-00001.gguf')
        self.assertTrue(report['runtime_no_peer_copy_build'])
        self.assertFalse(report['model_files_rewritten'])
        observer = parsed['services']['runtime-capacity-check']
        self.assertTrue(observer['read_only'])
        self.assertEqual(observer['network_mode'], 'service:attest-proxy')
        self.assertNotIn('ports', observer)
        self.assertTrue(all(v['read_only'] for v in observer['volumes']))

    def test_cleanup_is_embedded_only_when_explicitly_selected(self):
        fixture = FIXTURE.replace('/data/runtime/bin/llama-server', '/data/cyberseek-q2-llama.cpp/bin/llama-server')
        fixture = fixture.replace('  sglang:', '  model-download:\n    command:\n      - bash\n      - -c\n      - |\n        echo old-builder\n    image: example/runtime@sha256:def\n  sglang:')
        rendered, report = module.prepare(fixture, no_peer_copy_build=True, cleanup_obsolete=True)
        builder = yaml.safe_load(rendered)['services']['model-download']['command'][2]
        self.assertIn('python3 - --root /data --apply', builder)
        self.assertLess(builder.index('model-provenance-checked'), builder.index('cleanup-obsolete-models'))
        self.assertTrue(report['obsolete_model_cleanup'])


if __name__ == "__main__":
    unittest.main()
