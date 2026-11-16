#!/usr/bin/env python3
"""Fail-closed projection coverage and native encoding tests; no payloads."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('planner', Path(__file__).with_name('plan-q2-native-overlay.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def fixture():
    tensors, headers, index = [], {}, {'weight_map': {}}
    for layer in range(11, 29):
        for kind, suffix in [('attn_output_b', 'attn.wo_b'), ('ffn_down_shexp', 'ffn.shared_experts.w2'), ('ffn_down_exps', None)]:
            routed = suffix is None
            tensors.append({'name': f'blk.{layer}.{kind}.weight', 'logical_numpy_shape': [384, 32, 64] if routed else [32, 64], 'ggml_type_id': 39 if routed else 8})
            for e in range(384) if routed else [None]:
                prefix = f'layers.{layer}.ffn.experts.{e}.w2' if routed else f'layers.{layer}.{suffix}'
                for n, dtype, shape in [(prefix+'.weight', 'I8' if routed else 'F8_E4M3', [32, 32] if routed else [32, 64]), (prefix+'.scale', 'F8_E8M0', [32, 2] if routed else [1, 2])]:
                    headers[n] = {'shape': shape, 'dtype': dtype}
                    index['weight_map'][n] = 'model-00001-of-00001.safetensors'
    inv = [{'tensors': tensors, 'source': {'repo': 'unsloth/DeepSeek-V4.1-Flash-1017-GGUF', 'revision': 'dfcee0ec8d6b874c6663eca0fc55d90ab32534c8'}, 'parsed_header_sha256': 'a'*64}]
    config = {'architectures': ['DeepseekV41ForCausalLM'], 'text_config': {'n_routed_experts': 384}, 'quantization_config': {'expert_dtype': 'fp4', 'scale_fmt': 'ue8m0', 'weight_block_size': [32, 32]}}
    return inv, headers, index, config


class PlanTests(unittest.TestCase):
    def test_complete_patch_includes_all_experts_and_updated_scales(self):
        result = p.plan(*fixture())
        self.assertEqual(result['projection_count'], 54)
        self.assertEqual(result['native_replacement_tensor_and_scale_count'], 13896)
        self.assertIn('Not bit-equivalent', result['artifact_semantics'])

    def test_missing_projection_expert_or_scale_is_rejected(self):
        for target in ['projection', 'weight', 'scale']:
            args = fixture()
            if target == 'projection': args[0][0]['tensors'].pop()
            else: args[1].pop(f'layers.28.ffn.experts.383.w2.{target}')
            with self.subTest(target=target), self.assertRaises(ValueError):p.plan(*args)

    def test_wrong_native_layout_or_quantization_is_rejected(self):
        for target in ['shape', 'dtype', 'expert_count', 'source_revision']:
            inv, headers, index, config = fixture()
            if target == 'shape': headers['layers.11.attn.wo_b.weight']['shape'] = [64, 32]
            if target == 'dtype': headers['layers.11.attn.wo_b.weight']['dtype'] = 'BF16'
            if target == 'expert_count': config['text_config']['n_routed_experts'] = 256
            if target == 'source_revision': inv[0]['source']['revision'] = 'b'*40
            with self.subTest(target=target), self.assertRaises(ValueError):p.plan(inv, headers, index, config)


if __name__ == '__main__':unittest.main()
