#!/usr/bin/env python3
"""Small adversarial/format fixtures; no network, keys or real model payload."""
import importlib.util
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('inventory', Path(__file__).with_name('gguf-tensor-inventory.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def string(value):
    value = value.encode()
    return struct.pack('<Q', len(value)) + value


def fixture(*, duplicate=False, kind=8):
    metadata = string('general.architecture') + struct.pack('<I', kind) + string('deepseek41')
    metadata += string('tokenizer.ggml.tokens') + struct.pack('<IIQ', 9, 8, 2) + string('never include vocabulary') + string('in selected metadata')
    tensor = string('blk.11.ffn_down_exps.weight') + struct.pack('<IQQQIQ', 3, 128, 64, 256, 14, 0)
    raw = b'GGUF' + struct.pack('<IQQ', 3, 2 if duplicate else 1, 2) + metadata + tensor
    if duplicate:
        raw += tensor
    return raw + b'\0' * 512


def reader(raw, **kwargs):
    return module.HeaderReader(lambda start, count: raw[start:start+count], chunk=1, **kwargs)


class InventoryTests(unittest.TestCase):
    def test_metadata_only_schema_preserves_expert_dimensions(self):
        result = reader(fixture()).inventory()
        self.assertEqual(result['metadata'], {'general.architecture': 'deepseek41'})
        self.assertEqual(result['tensors'][0]['logical_numpy_shape'], [256, 64, 128])
        self.assertEqual(result['ggml_type_counts'], {'14': 1})
        self.assertGreaterEqual(result['data_start_offset'], result['header_bytes_parsed'])
        self.assertEqual(result['metadata_range_bytes_fetched'], result['header_bytes_parsed'])

    def test_invalid_magic_duplicate_names_and_unknown_types_fail(self):
        for raw in [b'FAIL'+fixture()[4:], fixture(duplicate=True), fixture(kind=99)]:
            with self.subTest(), self.assertRaises(module.InventoryError):
                reader(raw).inventory()

    def test_short_ranges_and_byte_budget_fail_closed(self):
        with self.assertRaises(module.InventoryError):
            reader(fixture(), limit=20).inventory()
        with self.assertRaises(module.InventoryError):
            reader(fixture()[:40]).inventory()

    def test_remote_identity_requires_immutable_revision(self):
        for repo, revision, path in [('org/repo', 'main', 'file.gguf'),
                                     ('https://other.test/repo', 'a'*40, 'file.gguf'),
                                     ('org/repo', 'a'*40, '../file.gguf')]:
            with self.subTest(), self.assertRaises(module.InventoryError):
                module.public_hf_reader(repo, revision, path)

    def test_ignored_range_response_is_closed_without_reading_body(self):
        class Response:
            status = 200
            headers = {}
            read_called = False
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): self.read_called = True; raise AssertionError('Body must not be read')
        response = Response()
        with patch.object(module.urllib.request, 'urlopen', return_value=response):
            with self.assertRaises(module.InventoryError):
                module.public_hf_reader('org/repo', 'a'*40, 'file.gguf')(0, 64)
        self.assertFalse(response.read_called)


if __name__ == '__main__':
    unittest.main()
