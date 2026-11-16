#!/usr/bin/env python3
"""Check reconstruction provenance against fresh model-measurer evidence.

Read-only, CPU-only and network-free. Weight payloads are hashed by the pinned
model-measurer; this check binds those hashes to the completed converter state.
It neither accepts a provider claim as a policy pin nor evaluates model quality.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

IDENTITY = {
    'source_digest': 'sha256:8dd425171ed78e68263a45f8d01317d991b92bc7128321464f1ab82d9a05eae8',
    'native_revision': '08c8657f02291e11b6f6b63e0e16014f8e748518',
    'plan_sha256': '38ddda77a9d7ed70da76ed7054bca10c40fb10c8a767bfefdffa8341cd3724c0',
    'converter_sha256': '24c088b2cc05cfd0374e512b1e202867a02f2ef1f424b3bc50408193b6c0d377',
    'quantizer_sha256': '4aac37f089b4dd37aeb417d1d547fd106a79562bb9955d96af6d4033b444548c',
}


def json_file(path, limit=32 * 1024 * 1024):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError('Invalid bounded metadata input')
    with path.open('rb') as f:
        if not os.path.samestat(info, os.fstat(f.fileno())):
            raise ValueError('Metadata changed before reading')
        raw = f.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('Metadata exceeded bound')
    # Duplicate JSON keys must not select different identities in two readers.
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError('Duplicate metadata key')
            obj[key] = value
        return obj
    return json.loads(raw, object_pairs_hook=unique), raw


def file_name(value):
    if not isinstance(value, str) or not value or any(c in value for c in '\0\r\n\\'):
        raise ValueError('Unsafe artifact path')
    p = PurePosixPath(value)
    if p.is_absolute() or '..' in p.parts or p.as_posix() != value:
        raise ValueError('Unsafe artifact path')
    return value


def expected_replacements(plan):
    if plan.get('projection_count') != 54 or len(plan.get('projections', [])) != 54:
        raise ValueError('Unexpected projection plan')
    names = set()
    for projection in plan['projections']:
        routed = projection['gguf_tensor'].endswith('.ffn_down_exps.weight')
        count = projection['logical_shape'][0] if routed else 1
        if count != (384 if routed else 1):
            raise ValueError('Unexpected expert count')
        for expert in range(count):
            for role in ('weight', 'scale'):
                name = projection['native_output_template'][role].replace('{expert}', str(expert))
                if name in names:
                    raise ValueError('Duplicate projection coverage')
                names.add(name)
    if len(names) != 13896:
        raise ValueError('Incomplete patched tensor coverage')
    return names


def validate(root, manifest_path, plan_path):
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Invalid native artifact root')
    model = root / 'model'
    if model.is_symlink() or not model.is_dir():
        raise ValueError('Invalid model root')
    state, _ = json_file(root / 'conversion-state.json')
    plan, plan_raw = json_file(plan_path)
    manifest, _ = json_file(manifest_path)
    provenance, provenance_raw = json_file(model / 'MODEL_SOURCE.json')
    if hashlib.sha256(plan_raw).hexdigest() != IDENTITY['plan_sha256']:
        raise ValueError('Reconstruction plan changed')
    if state.get('complete') is not True or state.get('identity') != IDENTITY:
        raise ValueError('Incomplete or different reconstruction')
    if state.get('provenance') != provenance:
        raise ValueError('Provenance/state disagreement')
    for key in ['source_digest', 'plan_sha256', 'converter_sha256']:
        if provenance.get(key) != IDENTITY[key]:
            raise ValueError('Unexpected source provenance')
    if (provenance.get('version') != 1 or provenance.get('patch') != plan['patch'] or
            provenance.get('native_base') != plan['native_base'] or
            provenance.get('artifact_semantics') != plan['artifact_semantics'] or
            provenance.get('replaced_tensor_count') != 13896):
        raise ValueError('Unexpected patch/native semantics')
    files = state.get('files')
    if not isinstance(files, dict) or len(files) != 84 or 'MODEL_SOURCE.json' in files:
        raise ValueError('Unexpected completed file set')
    shards = {name for name in files if name.endswith('.safetensors')}
    if len(shards) != 48:
        raise ValueError('Incomplete native checkpoint')
    expected = {}
    replaced = []
    for name, entry in files.items():
        file_name(name)
        if (type(entry.get('size')) is not int or entry['size'] < 0 or
                not re.fullmatch(r'[0-9a-f]{64}', entry.get('sha256', ''))):
            raise ValueError('Invalid committed file metadata')
        expected[name] = {'path': name, 'size': entry['size'], 'sha256': entry['sha256']}
        these = entry.get('replaced_tensors')
        if not isinstance(these, list) or any(not isinstance(x, str) for x in these):
            raise ValueError('Invalid tensor coverage metadata')
        if these and name not in shards:
            raise ValueError('Tensor replacement outside a weight shard')
        replaced.extend(these)
    if len(replaced) != len(set(replaced)) or set(replaced) != expected_replacements(plan):
        raise ValueError('Incomplete or duplicate patched tensor coverage')
    index, _ = json_file(model / 'model.safetensors.index.json')
    mapping = index.get('weight_map', {})
    if len(mapping) != 96085 or set(mapping.values()) != shards:
        raise ValueError('Unexpected complete native index')
    for name, entry in files.items():
        if any(mapping.get(tensor) != name for tensor in entry['replaced_tensors']):
            raise ValueError('Patch/index shard mismatch')
    expected['MODEL_SOURCE.json'] = {'path': 'MODEL_SOURCE.json', 'size': len(provenance_raw),
                                    'sha256': hashlib.sha256(provenance_raw).hexdigest()}
    if (manifest.get('version') != 1 or manifest.get('algorithm') != 'sha256-tree-v1' or
            manifest.get('model_id') != 'adverserial/cyberseek' or manifest.get('file_count') != 85):
        raise ValueError('Unexpected fresh measurement schema')
    entries = manifest.get('files', [])
    if len(entries) != 85 or any(not isinstance(e, dict) for e in entries):
        raise ValueError('Incomplete fresh measurement')
    observed = {}
    for entry in entries:
        name = file_name(entry.get('path'))
        if name in observed:
            raise ValueError('Duplicate fresh measurement path')
        observed[name] = entry
    if observed != expected:
        raise ValueError('Measured payload differs from completed reconstruction')
    tree = hashlib.sha256()
    for name, entry in sorted(expected.items()):
        tree.update(f"{name}\0{entry['size']}\0{entry['sha256']}\n".encode())
    digest = 'sha256:' + tree.hexdigest()
    total = sum(e['size'] for e in expected.values())
    if manifest.get('digest') != digest or manifest.get('total_bytes') != total:
        raise ValueError('Measurement tree/count disagreement')
    # The measurer hashes payloads once. These bounded metadata reads must also
    # be the same files it measured; otherwise an index/provenance swap could
    # invalidate the coverage check without changing the recorded payloads.
    for name in ['MODEL_SOURCE.json', 'model.safetensors.index.json']:
        _, raw = json_file(model / name)
        if hashlib.sha256(raw).hexdigest() != expected[name]['sha256']:
            raise ValueError('Metadata differs from fresh measurement')
    return {'native_artifact_verified': True, 'model': 'adverserial/cyberseek',
            'model_digest': digest, 'files': 85, 'bytes': total,
            'patched_tensors_and_scales': 13896, 'quality_or_speed_verified': False}


if __name__ == '__main__':
    try:
        print(json.dumps(validate(Path('/native'), Path('/model-evidence/native-manifest.json'),
                                  Path('/app/q2-native-compact-plan.json'))), flush=True)
    except Exception:
        # No metadata, model contents, paths, credentials or private traces.
        print(json.dumps({'native_artifact_verified': False, 'private_diagnostics_withheld': True}), flush=True)
        raise SystemExit(1)
