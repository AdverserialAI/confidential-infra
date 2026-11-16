#!/usr/bin/env python3
"""Validate bounded samples from the immutable public Q2_2 patch.

Does not execute upstream patch code, download whole tensors, persist decoded
values, access user credentials, or mutate the CVM. The upstream obfuscation
key is public artifact data, held in memory and never printed.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import urllib.request

import numpy as np
from gguf import GGMLQuantizationType, GGML_QUANT_SIZES


def sibling(name, alias):
    spec = importlib.util.spec_from_file_location(alias, Path(__file__).with_name(name))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


inventory = sibling('gguf-tensor-inventory.py', 'inventory')
quant = sibling('q2-native-quant.py', 'native_quant')
REPO = 'huihui-ai/Huihui-DeepSeek-V4.1-Flash-1017-abliterated-GGUF'
REVISION = '73fa239749dfc09e847ea5e1149626d064224d92'
PREFIX = 'patch-unsloth-Q2/patch-unsloth-Q2_2/'


def validate(plan):
    if plan['patch'] != {'revision': REVISION, 'variant': 'Q2_2', 'ablate_experts': 2,
                         'first_layer': 11, 'last_layer': 28}:
        raise ValueError('Unexpected patch identity')
    base = f'https://huggingface.co/{REPO}/resolve/{REVISION}/{PREFIX}'
    with urllib.request.urlopen(base+'Huihui-DeepSeek-V4.1-Flash-1017-patch-Q2-2.gguf.key', timeout=30) as r:
        key = r.read(4097)
    if len(key) > 4096 or not key.strip():
        raise ValueError('Unexpected public patch-key length')
    key = key.decode('utf-8').strip().encode('utf-8')
    lookup, reports = {}, []
    for i in range(6):
        name = PREFIX+f'Huihui-DeepSeek-V4.1-Flash-1017-patch-Q2-2-{i}.gguf'
        reader = inventory.public_hf_reader(REPO, REVISION, name)
        header = inventory.HeaderReader(reader).inventory()
        reports.append({'file': name, 'tensor_count': header['tensor_count'],
                        'parsed_header_sha256': header['parsed_header_sha256']})
        for tensor in header['tensors']:
            if tensor['name'] in lookup:
                raise ValueError('Ambiguous duplicate patch tensor')
            lookup[tensor['name']] = (tensor, header['data_start_offset'], reader)
    # A representative of every selected encoding, with native-dense vs
    # routed encoding checked separately when the same source type occurs.
    selected = {}
    for projection in plan['projections']:
        routed = projection['gguf_tensor'].endswith('.ffn_down_exps.weight')
        selected.setdefault((projection['ggml_type_id'], routed), projection)
    rows = []
    for (kind, routed), projection in selected.items():
        name = projection['gguf_tensor']+f'_{kind}'
        tensor, data_start, reader = lookup[name]
        if tensor['ggml_type_id'] != kind or tensor['logical_numpy_shape'] != projection['logical_shape']:
            raise ValueError('Patch/reference shape or encoding mismatch')
        cols = projection['logical_shape'][-1]
        block_size, bytes_per_block = GGML_QUANT_SIZES[GGMLQuantizationType(kind)]
        size = 32 * cols // block_size * bytes_per_block
        if not 1 <= size <= 4 * 1024 * 1024:
            raise ValueError('Sample exceeds explicit bound')
        encrypted = reader(data_start+tensor['data_offset'], size)
        raw = np.frombuffer(encrypted, dtype=np.uint8) ^ np.resize(np.frombuffer(key, dtype=np.uint8), size)
        decoded = quant.decode_gguf(raw.tobytes(), kind, 32, cols)
        if routed:
            if kind == 39:
                native, scales = quant.repack_mxfp4_losslessly(raw.tobytes(), 32, cols)
            else:
                native, scales = quant.encode_mxfp4(decoded)
            reconstructed = quant.decode_mxfp4(native, scales)
            threshold = .18
        else:
            native, scales = quant.encode_block_fp8(decoded)
            reconstructed = quant.decode_block_fp8(native, scales)
            threshold = .05
        errors = quant.error_metrics(decoded, reconstructed)
        passed = errors['relative_rms_error'] <= threshold
        if routed and kind == 39:
            passed = passed and np.array_equal(decoded, reconstructed)
        rows.append({'projection': projection['gguf_tensor'], 'source_type_id': kind,
                     'native_encoding': 'mxfp4' if routed else 'block-fp8',
                     'sample_rows': 32, 'columns': cols, 'public_payload_sample_bytes': size,
                     'decoded_sample_sha256': hashlib.sha256(decoded.tobytes()).hexdigest(),
                     'errors': errors, 'relative_rms_gate': threshold, 'passed': bool(passed)})
    return {'observed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'source': {'repo': REPO, 'revision': REVISION, 'variant': 'Q2_2'},
            'patch_header_inventory': reports, 'samples': rows,
            'passed': all(row['passed'] for row in rows),
            'scope': 'Bounded real public patched samples only; not a whole-model conversion, quality evaluation or GPU test',
            'raw_values_or_public_obfuscation_key_persisted': False, 'cvm_mutations': 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--report', type=Path, required=True)
    a = p.parse_args()
    result = validate(json.loads(a.plan.read_text()))
    with a.report.open('x') as f:
        json.dump(result, f, indent=2)
        f.write('\n')
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'sample_validation_failed': True, 'error_class': type(error).__name__}))
        raise SystemExit(1)
