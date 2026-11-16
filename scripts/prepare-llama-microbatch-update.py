#!/usr/bin/env python3
"""Prepare a narrow, private follow-up to the verified 64 x 1M configuration.

No cloud operation is performed. Keep the context, session count, model and
security settings fixed; reduce only the physical compute microbatch and allow
the read-only capacity observer enough time for cold loading.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

import yaml

spec = importlib.util.spec_from_file_location(
    'context_update', Path(__file__).with_name('prepare-llama-context-update.py'))
context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(context)


def prepare(source):
    before = yaml.safe_load(source)
    services = before['services']
    cmd = services['sglang']['command']
    if not isinstance(cmd, list) or not all(isinstance(x, str) for x in cmd):
        raise ValueError('Expected literal engine argv')

    def value(flag):
        if cmd.count(flag) != 1 or cmd.index(flag) + 1 >= len(cmd):
            raise ValueError('Missing or repeated required engine option')
        return cmd[cmd.index(flag) + 1]

    expected = {
        '--ctx-size': '67108864', '--parallel': '64', '--n-predict': '65536',
        '--alias': 'adverserial/cyberseek', '--host': '127.0.0.1',
        '--ubatch-size': '512', '--batch-size': '4096', '--n-gpu-layers': '999',
    }
    if any(value(k) != v for k, v in expected.items()):
        raise ValueError('Unexpected reviewed runtime configuration')
    if cmd.count('--no-kv-unified') != 1 or '--kv-unified' in cmd:
        raise ValueError('Independent context mode changed')
    runtime = '/data/cyberseek-q2-llama.cpp-no-peer-copy-c49edf0963a7/bin/llama-server'
    if cmd[0] != runtime or services['sglang'].get('environment'):
        raise ValueError('Unexpected runtime executable or environment override')
    observer = services['runtime-capacity-check']['command']
    if not (isinstance(observer, list) and len(observer) == 4
            and observer[:3] == ['python3', '-B', '-c']
            and observer[3].count('time.monotonic() + 900') == 1):
        raise ValueError('Unexpected metadata observer')

    tree = yaml.compose(source)
    def child(mapping, name):
        return next(v for k, v in mapping.value if k.value == name)
    svc = child(tree, 'services')
    argv = child(child(svc, 'sglang'), 'command')
    token = argv.value[cmd.index('--ubatch-size') + 1]
    script = child(child(svc, 'runtime-capacity-check'), 'command').value[3]
    updated_script = observer[3].replace('time.monotonic() + 900', 'time.monotonic() + 2700')
    replacements = [
        (token.start_mark.index, token.end_mark.index, '"32"'),
        (script.start_mark.index, script.end_mark.index, json.dumps(updated_script)),
    ]
    # A quoted observer scalar may be followed immediately by the next YAML
    # mapping; preserve the scalar's trailing newline/indent where necessary.
    line_start = source.rfind('\n', 0, script.end_mark.index) + 1
    trailing = source[line_start:script.end_mark.index]
    if not trailing.strip():
        replacements[1] = (script.start_mark.index, script.end_mark.index,
                           json.dumps(updated_script) + '\n' + trailing)
    rendered = source
    for start, end, replacement in sorted(replacements, reverse=True):
        rendered = rendered[:start] + replacement + rendered[end:]
    digest = context.configuration_digest(rendered, before)
    rendered, count = re.subn(r'^(\s*COMPOSE_DIGEST:\s*).*?$',
                             lambda m: m[1] + digest, rendered, flags=re.M)
    if count != 1:
        raise ValueError('Unexpected deployment digest layout')
    after = yaml.safe_load(rendered)
    permitted = copy.deepcopy(before)
    permitted['services']['sglang']['command'][cmd.index('--ubatch-size') + 1] = '32'
    permitted['services']['runtime-capacity-check']['command'][3] = updated_script
    permitted['services']['attest-proxy']['environment']['COMPOSE_DIGEST'] = digest
    if after != permitted:
        raise ValueError('Unrelated configuration change')
    return rendered, {
        'kind': 'local_microbatch_configuration_preparation_only',
        'model': 'adverserial/cyberseek', 'slots': 64,
        'total_context_tokens': 67108864, 'context_tokens_per_slot': 1048576,
        'max_output_tokens': 65536, 'kv_unified': False,
        'physical_microbatch_before': 512, 'physical_microbatch_after': 32,
        'logical_batch_unchanged': 4096, 'gpu_layers_unchanged': 999,
        'observer_wait_seconds': 2700,
        'runtime_revision': 'c49edf0963a76d5e78b2a7278c9bd5b71d26c84f',
        'runtime_no_peer_copy_build': True, 'model_files_rewritten': False,
        'deployment_configuration_digest': digest,
        'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
        'prepared_sha256': hashlib.sha256(rendered.encode()).hexdigest(),
        'unrelated_semantic_changes': False, 'provider_mutations': False,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--report', type=Path, required=True)
    args = p.parse_args()
    try:
        rendered, report = prepare(args.source.read_text())
        context.private_write(args.output, rendered)
        context.private_write(args.report, json.dumps(report, indent=2) + '\n')
    except Exception as exc:
        print(f'Preparation failed ({type(exc).__name__}); private content withheld', file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
