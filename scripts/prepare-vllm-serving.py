#!/usr/bin/env python3
"""Prepare a private future vLLM serving candidate; never call a cloud API.

Input is the exact reviewed staging configuration, not an arbitrary Compose.
This does not approve deployment, establish native output identity, publish
policy pins or prove GPU readiness/performance. Owner approval is separate.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]
# Only the reviewed CPU-corrected staging candidate is a valid future input.
# This local pin grants no staging recovery or serving-switch authorization.
STAGE_SHA256 = '313355e984ff088cb0e7b658b66178cfec4211111e9c04787cac073058128fd8'
PROFILE_SHA256 = 'e1b5a5d6630771e8e28fdb1954b1a00a54efe2e90015505370555440f0ad5b7c'
CONVERTER = 'ghcr.io/adverserialai/q2-native-converter@sha256:0309127f824ab7c472f3ffeb66361e73d7b571bb1272f344bf90613fe55682fe'
spec = importlib.util.spec_from_file_location('context_update', Path(__file__).with_name('prepare-llama-context-update.py'))
context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(context)

READINESS = r'''
import importlib.metadata,json,os,time,urllib.request
try:
    deadline=time.monotonic()+7200
    while time.monotonic()<deadline:
        try:
            req=urllib.request.Request('http://127.0.0.1:30000/v1/models',headers={'Authorization':'Bearer '+os.environ['BACKEND_API_KEY']})
            with urllib.request.urlopen(req,timeout=10) as r:
                payload=r.read(65537)
                if len(payload)>65536:raise ValueError('bounded metadata exceeded')
                rows=json.loads(payload).get('data',[])
            if len(rows)!=1 or rows[0].get('id')!='adverserial/cyberseek' or rows[0].get('max_model_len')!=1048576:
                raise ValueError('unexpected serving model/context metadata')
            version=importlib.metadata.version('vllm')
            if version!='0.33.0':raise ValueError('unexpected runtime version')
            print(json.dumps({'runtime_readiness':{'verified_model_listing':True,'model':'adverserial/cyberseek','observed_max_model_len':1048576,'runtime_version':version,'configured_max_num_seqs':64,'full_64_by_1M_capacity_verified':False,'speed_or_quality_verified':False,'inference_prompts_submitted':0}}),flush=True)
            break
        except Exception:
            time.sleep(10)
    else:raise RuntimeError('readiness timeout')
except Exception:
    print(json.dumps({'runtime_readiness':{'verified_model_listing':False,'private_diagnostics_withheld':True}}),flush=True)
    raise SystemExit(1)
'''


def mapping(node, key):
    return next(v for k, v in node.value if k.value == key)


def replace_service(source, name, replacement):
    services = mapping(yaml.compose(source), 'services')
    entries = services.value
    for i, (key, value) in enumerate(entries):
        if key.value != name:
            continue
        start = source.rfind('\n', 0, key.start_mark.index) + 1
        end_mark = entries[i + 1][0].start_mark.index if i + 1 < len(entries) else services.end_mark.index
        end = source.rfind('\n', 0, end_mark) + 1
        if source[start:key.start_mark.index].strip() or source[end:end_mark].strip():
            raise ValueError('Unsafe service rewrite boundary')
        block = '' if replacement is None else yaml.safe_dump({name: replacement}, sort_keys=False)
        block = ''.join('  ' + line if line.strip() else line for line in block.splitlines(keepends=True))
        return source[:start] + block + source[end:]
    raise ValueError('Required reviewed service missing')


def prepare(source):
    if hashlib.sha256(source.encode()).hexdigest() != STAGE_SHA256:
        raise ValueError('Input is not the reviewed staging candidate')
    profile_raw = (ROOT / 'profiles/cyberseek-vllm-h200.json').read_bytes()
    if hashlib.sha256(profile_raw).hexdigest() != PROFILE_SHA256:
        raise ValueError('Reviewed launch profile changed')
    profile = json.loads(profile_raw)
    before = yaml.safe_load(source)
    services = before['services']
    old = services['sglang']
    proxy = services['attest-proxy']
    command = old['command']
    if command.count('--api-key') != 1:
        raise ValueError('Expected one existing loopback credential')
    key = command[command.index('--api-key') + 1]
    if (not isinstance(key, str) or not key or
            proxy['environment'].get('UPSTREAM_BEARER_TOKEN') != key):
        raise ValueError('Existing backend authentication mismatch')
    if any(proxy['environment'].get(k) != v for k, v in {
            'CONFIDENTIAL_MODE': '1', 'AUTH_REQUIRED': '0', 'EHBP_REQUIRED': '1',
            'MODEL_ID': 'adverserial/cyberseek'}.items()):
        raise ValueError('Confidential baseline controls differ')
    if proxy['environment'].get('MODEL_DIGEST'):
        raise ValueError('Review explicit model digest before switching artifacts')
    if services['q2-native-reconstruction']['image'] != CONVERTER:
        raise ValueError('Unexpected converter image')
    permitted = copy.deepcopy(before)
    changed = permitted['services']
    # Recovery inputs and volumes stay intact. Do not rerun old GGUF builder or
    # converter in the new serving graph, or create a cycle through its observer.
    for name in ['model-download', 'model-perms']:
        changed[name]['profiles'] = ['recovery-only']
    del changed['q2-native-reconstruction']
    measure = changed['model-measurer']
    measure.pop('depends_on', None)
    measure['environment']['MODEL_ROOT'] = '/native/model'
    measure['environment']['MODEL_MANIFEST_FILE'] = '/model-evidence/native-manifest.json'
    measure['volumes'] = [
        {'type': 'volume', 'source': 'cyberseek-vllm-native-data', 'target': '/native', 'read_only': True, 'volume': {}},
        copy.deepcopy(next(v for v in services['model-measurer']['volumes'] if v['target'] == '/model-evidence')),
    ]
    artifact_check = {
        'image': CONVERTER, 'entrypoint': ['python', '-B', '-c'],
        'command': [Path(__file__).with_name('check-native-artifact.py').read_text().replace('$', '$$')],
        'restart': 'no', 'read_only': True, 'user': '65532:65532', 'network_mode': 'none',
        'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
        'mem_limit': '512m', 'pids_limit': 32,
        'depends_on': {'model-measurer': {'condition': 'service_completed_successfully', 'required': True}},
        'volumes': [
            {'type': 'volume', 'source': 'cyberseek-vllm-native-data', 'target': '/native', 'read_only': True, 'volume': {}},
            {'type': 'volume', 'source': 'model-evidence-state', 'target': '/model-evidence', 'read_only': True, 'volume': {}},
        ],
    }
    # Reuse the staging service slot for a pure artifact check so all rewrites
    # are bounded named-service replacements rather than a secret-bearing dump.
    changed['q2-native-reconstruction'] = artifact_check
    preflight = changed['vllm-communication-preflight']
    preflight['depends_on'] = {'q2-native-reconstruction': {'condition': 'service_completed_successfully', 'required': True}}
    changed['attest-proxy']['depends_on'] = {
        'q2-native-reconstruction': {'condition': 'service_completed_successfully', 'required': True},
        'vllm-communication-preflight': {'condition': 'service_completed_successfully', 'required': True},
    }
    changed['attest-proxy']['environment']['MODEL_MANIFEST_FILE'] = '/model-evidence/native-manifest.json'
    changed['attest-proxy']['environment']['RUNTIME_DIGEST'] = profile['image'].split('@')[1]
    engine = {
        'image': profile['image'], 'entrypoint': ['vllm'],
        'command': profile['argv_without_secret_authentication'] + ['--api-key', key],
        'environment': dict(profile['environment'], HOME='/tmp/home', HF_HOME='/tmp/hf',
                            XDG_CACHE_HOME='/tmp/cache', VLLM_CACHE_ROOT='/tmp/vllm',
                            TORCHINDUCTOR_CACHE_DIR='/tmp/torchinductor', TRITON_CACHE_DIR='/tmp/triton',
                            HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', VLLM_SERVER_DEV_MODE='0'),
        'depends_on': {'attest-proxy': {'condition': 'service_started', 'required': True}},
        'network_mode': 'service:attest-proxy', 'restart': 'unless-stopped', 'init': True,
        'read_only': True, 'user': '65532:65532', 'cap_drop': ['ALL'],
        'security_opt': ['no-new-privileges:true'], 'pids_limit': 2048,
        'shm_size': '32g', 'tmpfs': ['/tmp:size=32g,mode=1777'],
        'ulimits': {'memlock': {'soft': -1, 'hard': -1}, 'core': {'soft': 0, 'hard': 0}},
        'deploy': copy.deepcopy(old['deploy']),
        'volumes': [{'type': 'volume', 'source': 'cyberseek-vllm-native-data', 'target': '/data', 'read_only': True, 'volume': {}}],
    }
    changed['sglang'] = engine
    changed['runtime-capacity-check'] = {
        'image': profile['image'], 'entrypoint': ['python3', '-B', '-c'],
        'command': [READINESS.replace('$', '$$')], 'environment': {'BACKEND_API_KEY': key, 'HOME': '/tmp'},
        'network_mode': 'service:attest-proxy', 'restart': 'no', 'read_only': True, 'user': '65532:65532',
        'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
        'depends_on': {'sglang': {'condition': 'service_started', 'required': True}},
        'mem_limit': '256m', 'pids_limit': 32,
    }
    rendered = source
    names = ['model-download', 'model-perms', 'model-measurer', 'q2-native-reconstruction',
             'vllm-communication-preflight', 'sglang', 'runtime-capacity-check', 'attest-proxy']
    for name in names:
        rendered = replace_service(rendered, name, changed[name])
    digest = context.configuration_digest(rendered, permitted)
    rendered, count = re.subn(r'^(\s*COMPOSE_DIGEST:\s*).*?$', lambda m: m[1] + digest, rendered, flags=re.M)
    if count != 1:
        raise ValueError('Unexpected deployment digest count')
    changed['attest-proxy']['environment']['COMPOSE_DIGEST'] = digest
    if yaml.safe_load(rendered) != permitted:
        raise ValueError('Unexpected unrelated semantic change')
    return rendered, {
        'kind': 'local_future_vllm_serving_candidate_not_approved_or_deployed',
        'source_sha256': STAGE_SHA256, 'prepared_sha256': hashlib.sha256(rendered.encode()).hexdigest(),
        'deployment_configuration_digest': digest, 'runtime_image': profile['image'],
        'configured_max_num_seqs': 64, 'configured_max_model_len': 1048576, 'configured_output_cap': 65536,
        'reconstruction_and_fresh_measurement_gate': True, 'gpu_communication_gate': True,
        'model_weights_and_manifest_read_only_to_inference': True,
        'backend_loopback_authentication_preserved': True, 'old_recovery_volumes_preserved': True,
        'prefix_cache_enabled': False, 'secret_values_printed': False,
        'provider_mutations': False, 'approval_required': True,
        'new_model_digest': None, 'new_policy_published': False,
        'gpu_startup_speed_quality_and_full_capacity_verified': False,
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
        print(json.dumps({'serving_preparation_failed': True, 'error_class': type(error).__name__}))
        raise SystemExit(1)
