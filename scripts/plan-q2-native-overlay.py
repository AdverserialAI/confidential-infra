#!/usr/bin/env python3
"""Plan Q2_2 projection reconstruction; no weight, CVM or policy mutation.

The planned artifact uses the native 1017 base plus the actual patched GGUF's
attention/shared/routed down projections. It is a NEW mixed-precision artifact,
not a byte-equivalent conversion of the entire Q2 GGUF checkpoint.
"""
import argparse
import json
import math
from pathlib import Path


BASE_REPO = 'deepseek-ai/DeepSeek-V4.1-Flash-1017'
BASE_REVISION = '08c8657f02291e11b6f6b63e0e16014f8e748518'
PATCH_REVISION = '73fa239749dfc09e847ea5e1149626d064224d92'
SOURCE_DIGEST = 'sha256:8dd425171ed78e68263a45f8d01317d991b92bc7128321464f1ab82d9a05eae8'


def plan(inventories, native_headers, native_index, config):
    if config.get('architectures') != ['DeepseekV41ForCausalLM']:
        raise ValueError('Wrong native architecture')
    q = config.get('quantization_config', {})
    if (q.get('expert_dtype'), q.get('scale_fmt'), q.get('weight_block_size')) != ('fp4', 'ue8m0', [32, 32]):
        raise ValueError('Unreviewed native quantization schema')
    experts = config.get('text_config', config).get('n_routed_experts')
    if experts != 384:
        raise ValueError('Unexpected routed-expert count')
    tensors = {}
    for inv in inventories:
        source = inv.get('source', {})
        if source.get('repo') != 'unsloth/DeepSeek-V4.1-Flash-1017-GGUF' or source.get('revision') != 'dfcee0ec8d6b874c6663eca0fc55d90ab32534c8':
            raise ValueError('Unexpected reference GGUF inventory revision')
        for tensor in inv['tensors']:
            if tensor['name'] in tensors:
                raise ValueError('Duplicate GGUF tensor')
            tensors[tensor['name']] = (tensor, inv['source'], inv['parsed_header_sha256'])
    projections, total_bytes = [], 0
    for layer in range(11, 29):
        for kind, suffix in [('attn_output_b', 'attn.wo_b'),
                             ('ffn_down_shexp', 'ffn.shared_experts.w2'),
                             ('ffn_down_exps', None)]:
            name = f'blk.{layer}.{kind}.weight'
            if name not in tensors:
                raise ValueError('Missing required Q2_2 projection')
            tensor, source, header_hash = tensors[name]
            shape = tensor['logical_numpy_shape']
            routed = suffix is None
            if len(shape) != (3 if routed else 2) or (routed and shape[0] != experts):
                raise ValueError('Unexpected GGUF projection layout')
            rows, cols = shape[-2:]
            outputs = []
            for expert in (range(experts) if routed else [None]):
                prefix = f'layers.{layer}.ffn.experts.{expert}.w2' if routed else f'layers.{layer}.{suffix}'
                weight_name, scale_name = prefix + '.weight', prefix + '.scale'
                for n in [weight_name, scale_name]:
                    if n not in native_index['weight_map'] or n not in native_headers:
                        raise ValueError('Missing required native tensor or scale')
                w, s = native_headers[weight_name], native_headers[scale_name]
                expected_w = [rows, cols // 2] if routed else [rows, cols]
                expected_s = [rows, cols // 32] if routed else [rows // 32, cols // 32]
                if rows % 32 or cols % 32 or w['shape'] != expected_w or s['shape'] != expected_s:
                    raise ValueError('GGUF/native shape mismatch')
                if w['dtype'] != ('I8' if routed else 'F8_E4M3') or s['dtype'] != 'F8_E8M0':
                    raise ValueError('Unreviewed native projection encoding')
                length = math.prod(expected_w) + math.prod(expected_s)
                total_bytes += length
                outputs.append({'weight': weight_name, 'scale': scale_name,
                                'weight_shape': expected_w, 'scale_shape': expected_s,
                                'weight_dtype': w['dtype'], 'scale_dtype': s['dtype'],
                                'source_base_weight_file': native_index['weight_map'][weight_name]})
            projections.append({'gguf_tensor': name, 'layer': layer,
                                'ggml_type_id': tensor['ggml_type_id'],
                                'logical_shape': shape, 'source_header_sha256': header_hash,
                                'unmodified_public_base_header_source': source,
                                'actual_conversion_input': 'Existing measured, patched GGUF; never the unmodified public GGUF payload',
                                'native_outputs': outputs})
    return {'status': 'shape/encoding plan only; conversion and GPU execution not yet verified',
            'native_base': {'repo': BASE_REPO, 'revision': BASE_REVISION},
            'required_patched_gguf_model_digest': SOURCE_DIGEST,
            'patch': {'revision': PATCH_REVISION, 'variant': 'Q2_2', 'ablate_experts': 2,
                      'first_layer': 11, 'last_layer': 28},
            'artifact_semantics': 'New native-base reconstruction retaining Q2_2-modified projections; unchanged weights use native-base precision. Not bit-equivalent to all Q2 GGUF weights.',
            'quantization': 'MXFP4 routed down projections; block FP8 dense/shared down projections; regenerated UE8M0 scales',
            'projection_count': len(projections),
            'native_replacement_tensor_and_scale_count': sum(2 * len(p['native_outputs']) for p in projections),
            'overlay_payload_bytes': total_bytes,
            'projections': projections,
            'required_validation': ['Source patched model identity and hash manifest',
                'Bounded-memory quantization round trips for every selected GGML type',
                'Quantization error and patch-vs-base behavior evaluation',
                'Native index coverage and atomic resumable overlay writes',
                'Pinned vLLM startup on confidential 8xH200 without weakened TEE',
                'Measured latency/throughput at increasing concurrency and context',
                'New measured artifact and runtime/config policy; attestation/ACK/chat/metering'],
            'cvm_mutations': 0}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inventories', type=Path, nargs='+', required=True)
    p.add_argument('--native-headers', type=Path, required=True)
    p.add_argument('--native-index', type=Path, required=True)
    p.add_argument('--native-config', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    headers = {}
    for path in a.native_headers.glob('*.json'):
        for name, value in json.loads(path.read_text()).items():
            if name != '__metadata__':
                if name in headers:
                    raise ValueError('Duplicate native header tensor')
                headers[name] = value
    result = plan([json.loads(v.read_text()) for v in a.inventories], headers,
                  json.loads(a.native_index.read_text()), json.loads(a.native_config.read_text()))
    with a.output.open('x') as f:
        json.dump(result, f, indent=2)
        f.write('\n')
    print(json.dumps({k: result[k] for k in ['status', 'projection_count', 'native_replacement_tensor_and_scale_count', 'overlay_payload_bytes', 'artifact_semantics', 'cvm_mutations']}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'plan_failed': True, 'error_class': type(error).__name__}))
        raise SystemExit(1)
