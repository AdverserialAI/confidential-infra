#!/usr/bin/env python3
"""Prepare the exact reviewed staging CPU-quota correction locally only."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import yaml

SOURCE_SHA256 = '9c8455bd7ee1134f87277c72aaa92c6d493e44c2adf4ac4abfe9c76813f1ae0a'
KIND = 'reviewed_staging_cpu_quota_correction_only'
spec = importlib.util.spec_from_file_location('context_update', Path(__file__).with_name('prepare-llama-context-update.py'))
context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(context)


def prepare(source):
    if hashlib.sha256(source.encode()).hexdigest() != SOURCE_SHA256:
        raise ValueError('Input differs from the exact reviewed failed staging configuration')
    before = yaml.safe_load(source)
    service = before['services']['q2-native-reconstruction']
    if service.get('cpus') != '8.0' or service.get('mem_limit') != '16g':
        raise ValueError('Unexpected converter resource configuration')
    # Remove this scalar by its parsed location, preserving all other private
    # bytes (including folded scripts, quoting and authentication values).
    tree = yaml.compose(source)
    child = lambda node, key: next(value for name, value in node.value if name.value == key)
    converter = child(child(tree, 'services'), 'q2-native-reconstruction')
    key, value = next((k, v) for k, v in converter.value if k.value == 'cpus')
    start = source.rfind('\n', 0, key.start_mark.index) + 1
    end = source.find('\n', value.end_mark.index)
    if end < 0 or source[start:key.start_mark.index].strip() or source[value.end_mark.index:end].strip():
        raise ValueError('Unexpected quota scalar boundary')
    rendered = source[:start] + source[end + 1:]
    digest = context.configuration_digest(rendered, before)
    rendered, count = re.subn(r'^(\s*COMPOSE_DIGEST:\s*).*?$', lambda m: m[1] + digest, rendered, flags=re.M)
    if count != 1:
        raise ValueError('Unexpected deployment digest count')
    permitted = copy.deepcopy(before)
    del permitted['services']['q2-native-reconstruction']['cpus']
    permitted['services']['attest-proxy']['environment']['COMPOSE_DIGEST'] = digest
    if yaml.safe_load(rendered) != permitted:
        raise ValueError('Unexpected unrelated semantic change')
    return rendered, {
        'kind': KIND, 'model': 'adverserial/cyberseek', 'slots': 64,
        'context_tokens_per_slot': 1048576, 'total_context_tokens': 67108864,
        'max_output_tokens': 65536, 'kv_unified': False,
        'unrelated_semantic_changes': False, 'removed_converter_cpu_quota': '8.0',
        'converter_memory_limit_unchanged': '16g', 'serving_runtime_unchanged': True,
        'provider_mutations': False, 'requires_fresh_explicit_owner_approval': True,
        'source_sha256': SOURCE_SHA256,
        'prepared_sha256': hashlib.sha256(rendered.encode()).hexdigest(),
        'deployment_configuration_digest': digest,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    rendered, report = prepare(args.source.read_text())
    context.private_write(args.output, rendered)
    context.private_write(args.report, json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'cpu_fix_preparation_failed': True, 'error_class': type(error).__name__}))
        raise SystemExit(1)
