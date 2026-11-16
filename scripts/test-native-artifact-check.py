#!/usr/bin/env python3
"""Synthetic fresh-measurement/provenance corruption tests, no real weights."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('artifact', Path(__file__).with_name('check-native-artifact.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
PLAN = Path(__file__).resolve().parents[1] / 'profiles/cyberseek-q2_2-native-plan.json'


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n')


def fixture(root):
    model = root / 'model'
    model.mkdir()
    plan = json.loads(PLAN.read_text())
    shards = [f'model-{i:05}-of-00048.safetensors' for i in range(1, 49)]
    patched = sorted(m.expected_replacements(plan))
    mapping = {name: shards[i % 48] for i, name in enumerate(patched)}
    for i in range(96085 - len(mapping)):
        mapping[f'synthetic-unmodified-{i}'] = shards[i % 48]
    write(model / 'model.safetensors.index.json', {'weight_map': mapping})
    for name in shards + ['config.json'] + [f'synthetic-aux-{i}.json' for i in range(34)]:
        (model / name).write_bytes(b'synthetic only\n')
    provenance = dict(version=1, artifact_semantics=plan['artifact_semantics'],
                      source_digest=m.IDENTITY['source_digest'], native_base=plan['native_base'],
                      patch=plan['patch'], replaced_tensor_count=13896,
                      plan_sha256=m.IDENTITY['plan_sha256'], converter_sha256=m.IDENTITY['converter_sha256'])
    files = {}
    for path in model.iterdir():
        raw = path.read_bytes()
        files[path.name] = {'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                            'replaced_tensors': [t for t in patched if mapping[t] == path.name]}
    write(root / 'conversion-state.json', {'complete': True, 'identity': m.IDENTITY, 'files': files, 'provenance': provenance})
    write(model / 'MODEL_SOURCE.json', provenance)
    entries = [{'path': p.name, 'size': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
               for p in sorted(model.iterdir())]
    tree = hashlib.sha256()
    for e in entries:
        tree.update(f"{e['path']}\0{e['size']}\0{e['sha256']}\n".encode())
    manifest = {'version': 1, 'algorithm': 'sha256-tree-v1', 'model_id': 'adverserial/cyberseek',
                'file_count': len(entries), 'total_bytes': sum(e['size'] for e in entries),
                'digest': 'sha256:' + tree.hexdigest(), 'files': entries}
    write(root / 'manifest.json', manifest)


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        fixture(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def check(self):
        return m.validate(self.root, self.root / 'manifest.json', PLAN)

    def test_complete_source_and_fresh_payload_measurement_bind(self):
        result = self.check()
        self.assertTrue(result['native_artifact_verified'])
        self.assertFalse(result['quality_or_speed_verified'])
        self.assertEqual(result['patched_tensors_and_scales'], 13896)

    def test_incomplete_or_wrong_source_fails(self):
        state = json.loads((self.root / 'conversion-state.json').read_text())
        state['complete'] = False
        write(self.root / 'conversion-state.json', state)
        with self.assertRaises(ValueError):
            self.check()
        state['complete'] = True
        state['identity']['source_digest'] = 'sha256:' + '0' * 64
        write(self.root / 'conversion-state.json', state)
        with self.assertRaises(ValueError):
            self.check()

    def test_same_size_corruption_and_old_or_duplicate_measurement_fail(self):
        path = self.root / 'manifest.json'
        original = json.loads(path.read_text())
        for change in ['hash', 'old-model', 'duplicate', 'tree']:
            manifest = json.loads(json.dumps(original))
            if change == 'hash':
                manifest['files'][0]['sha256'] = '0' * 64
            if change == 'old-model':
                manifest['model_id'] = 'adverserial/cyberglm'
            if change == 'duplicate':
                manifest['files'][1] = manifest['files'][0]
            if change == 'tree':
                manifest['digest'] = 'sha256:' + '0' * 64
            write(path, manifest)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.check()

    def test_metadata_swap_missing_projection_or_symlink_fails(self):
        path = self.root / 'model' / 'model.safetensors.index.json'
        raw = path.read_bytes()
        index = json.loads(raw)
        tensor = next(iter(m.expected_replacements(json.loads(PLAN.read_text()))))
        index['weight_map'][tensor] = 'not-a-committed-shard'
        write(path, index)
        with self.assertRaises(ValueError):
            self.check()
        path.write_bytes(raw)
        target = self.root / 'index-copy.json'
        target.write_bytes(raw)
        path.unlink()
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.check()

    def test_duplicate_json_keys_and_path_traversal_fail(self):
        path = self.root / 'duplicate.json'
        path.write_text('{"complete":false,"complete":true}')
        with self.assertRaises(ValueError):
            m.json_file(path)
        for name in ['../outside', '/outside', 'a/../b', 'a\nb', 'a\\b', 'a//b']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                m.file_name(name)


if __name__ == '__main__':
    unittest.main()
