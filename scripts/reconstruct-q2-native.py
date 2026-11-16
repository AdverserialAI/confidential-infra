#!/usr/bin/env python3
"""Stream a native checkpoint while retaining measured Q2_2 projections.

Only writes a dedicated new output directory. Active GGUF inputs are read-only.
Each immutable native shard is SHA-256 verified, selected payloads are replaced
in a private temporary file, and the complete shard is atomically published.
No duplicated overlay tensors, retained obsolete scales, prompt/API key access,
cloud operations or execution of model-repository code.
"""
import argparse
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import time
import urllib.parse
import urllib.request

import numpy as np
from gguf import GGUFReader, GGMLQuantizationType, GGML_QUANT_SIZES


def load_sibling(name, alias):
    spec = importlib.util.spec_from_file_location(alias, Path(__file__).with_name(name))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


q = load_sibling('q2-native-quant.py', 'native_quant')
planner = load_sibling('plan-q2-native-overlay.py', 'projection_plan')
CHUNK = 16 * 1024 * 1024


def emit(event, **fields):
    print(json.dumps({'at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      'event': event, **fields}), flush=True)


def atomic_json(path, data):
    encoded = (json.dumps(data, sort_keys=True, separators=(',', ':'))+'\n').encode()
    temp = path.with_name(path.name+'.tmp')
    with temp.open('wb') as f:
        f.write(encoded)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    with directory_fd(path.parent) as fd:
        os.fsync(fd)


class directory_fd:
    def __init__(self, path): self.path = path
    def __enter__(self): self.fd = os.open(self.path, os.O_RDONLY); return self.fd
    def __exit__(self, *args): os.close(self.fd)


def hash_file(path):
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('Nonregular artifact file')
    h = hashlib.sha256()
    with path.open('rb') as f:
        if not os.path.samestat(before, os.fstat(f.fileno())):
            raise ValueError('Artifact changed before hashing')
        while block := f.read(CHUNK): h.update(block)
    after = path.lstat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('Artifact changed during hashing')
    return h.hexdigest()


def source_identity(root, manifest, expected):
    if manifest.get('version') != 1 or manifest.get('algorithm') != 'sha256-tree-v1' or manifest.get('model_id') != 'adverserial/cyberseek':
        raise ValueError('Invalid source measurement schema')
    entries = manifest.get('files', [])
    actual = sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() or p.is_symlink())
    if actual != sorted(e['path'] for e in entries) or len(actual) != len(set(actual)):
        raise ValueError('Source measurement file coverage mismatch')
    tree = hashlib.sha256()
    for entry in sorted(entries, key=lambda e:e['path']):
        relative = Path(entry['path'])
        if relative.is_absolute() or '..' in relative.parts or '\0' in str(relative):
            raise ValueError('Unsafe source measurement path')
        path = root / relative
        if any(parent.is_symlink() for parent in [path, *path.parents] if parent == root or root in parent.parents):
            raise ValueError('Symlink in source artifact')
        if path.stat().st_size != entry['size'] or hash_file(path) != entry['sha256']:
            raise ValueError('Source artifact payload measurement mismatch')
        tree.update(f"{entry['path']}\0{entry['size']}\0{entry['sha256']}\n".encode())
        emit('source_file_verified', file=relative.name, bytes=entry['size'])
    digest = 'sha256:'+tree.hexdigest()
    if digest != expected or manifest.get('digest') != digest:
        raise ValueError('Source model digest does not match the approved Q2_2 artifact')
    return digest


def safetensors_header(path):
    size = path.stat().st_size
    with path.open('rb') as f:
        prefix = f.read(8)
        if len(prefix) != 8: raise ValueError('Short native shard')
        length = struct.unpack('<Q', prefix)[0]
        if not 2 <= length <= min(64 * 1024 * 1024, size-8):
            raise ValueError('Invalid native header length')
        header = json.loads(f.read(length))
    end = 0
    for name, entry in sorted(((n,e) for n,e in header.items() if n != '__metadata__'), key=lambda p:p[1]['data_offsets'][0]):
        start, stop = entry['data_offsets']
        if start != end or stop < start or stop > size-8-length:
            raise ValueError('Native payload gaps, overlap or out-of-range offset')
        end = stop
    if end != size-8-length: raise ValueError('Unindexed native payload bytes')
    return header, 8+length


class PatchedSource:
    def __init__(self, root, plan):
        self.plan = plan
        self.readers, self.tensors = [], {}
        for path in sorted(root.glob('*.gguf')):
            if path.name.startswith('mmproj'): continue
            reader = GGUFReader(path, mode='r')
            self.readers.append(reader)  # retain memory map lifetime
            for tensor in reader.tensors:
                if tensor.name in self.tensors: raise ValueError('Duplicate source GGUF tensor')
                self.tensors[tensor.name] = tensor
        self.cache_key, self.cache = None, None

    def native(self, projection, expert):
        key = (projection['gguf_tensor'], expert)
        if key == self.cache_key: return self.cache
        tensor = self.tensors[projection['gguf_tensor']]
        if int(tensor.tensor_type) != projection['ggml_type_id'] or list(reversed(tensor.shape.tolist())) != projection['logical_shape']:
            raise ValueError('Actual patched tensor does not match reviewed shape or encoding')
        rows, cols = projection['logical_shape'][-2:]
        block, block_bytes = GGML_QUANT_SIZES[GGMLQuantizationType(projection['ggml_type_id'])]
        size = rows * cols // block * block_bytes
        start = (expert or 0) * size
        data = tensor.data.view(np.uint8).reshape(-1)[start:start+size]
        if data.size != size: raise ValueError('Truncated source projection slice')
        kind = projection['ggml_type_id']
        decoded = q.decode_gguf(data, kind, rows, cols)
        routed = expert is not None
        if routed:
            weight, scale = q.repack_mxfp4_losslessly(data, rows, cols) if kind == 39 else q.encode_mxfp4(decoded)
            reconstructed, threshold = q.decode_mxfp4(weight, scale), .18
        else:
            weight, scale = q.encode_block_fp8(decoded)
            reconstructed, threshold = q.decode_block_fp8(weight, scale), .05
        metrics = q.error_metrics(decoded, reconstructed)
        if metrics['relative_rms_error'] > threshold or (routed and kind == 39 and not np.array_equal(decoded, reconstructed)):
            raise ValueError('Projection reconstruction exceeded its numerical error gate')
        self.cache_key, self.cache = key, (weight, scale, metrics)
        return self.cache


def replacement_map(plan):
    result = {}
    for projection in plan['projections']:
        for i, output in enumerate(projection['native_outputs']):
            expert = i if projection['gguf_tensor'].endswith('.ffn_down_exps.weight') else None
            for role in ('weight', 'scale'):
                name = output[role]
                if name in result: raise ValueError('Duplicate planned native tensor')
                result[name] = (projection, expert, role, output)
    if len(result) != plan['native_replacement_tensor_and_scale_count']:
        raise ValueError('Native replacement coverage mismatch')
    return result


def expand_plan(plan):
    """Expand a small reviewed deployment plan without embedding a huge index."""
    for projection in plan['projections']:
        if 'native_outputs' in projection:
            continue
        template = projection['native_output_template']
        routed = projection['gguf_tensor'].endswith('.ffn_down_exps.weight')
        count = projection['logical_shape'][0] if routed else 1
        if count != (384 if routed else 1): raise ValueError('Invalid compact expert count')
        projection['native_outputs'] = []
        for i in range(count):
            item = dict(template)
            for role in ('weight', 'scale'):
                item[role] = template[role].replace('{expert}', str(i))
            projection['native_outputs'].append(item)
    return plan


def patch_shard(path, source, replacements, weight_map):
    header, offset = safetensors_header(path)
    expected = {n for n, filename in weight_map.items() if filename == path.name.removesuffix('.partial')}
    actual = set(header)-{'__metadata__'}
    if actual != expected: raise ValueError('Native shard/index tensor coverage mismatch')
    selected = {n:replacements[n] for n in actual if n in replacements}
    metrics, completed = [], []
    # Group weight and scale to reuse a single bounded source matrix.
    names = sorted(selected, key=lambda n:(selected[n][0]['gguf_tensor'], selected[n][1] if selected[n][1] is not None else -1, selected[n][2]))
    with path.open('r+b') as f:
        for name in names:
            projection, expert, role, planned = selected[name]
            entry = header[name]
            if entry['dtype'] != planned[role+'_dtype'] or entry['shape'] != planned[role+'_shape']:
                raise ValueError('Actual native payload layout does not match the plan')
            weight, scale, errors = source.native(projection, expert)
            array = weight if role == 'weight' else scale
            start, stop = entry['data_offsets']
            raw = memoryview(np.ascontiguousarray(array)).cast('B')
            if len(raw) != stop-start: raise ValueError('Replacement payload byte length mismatch')
            f.seek(offset+start)
            if f.write(raw) != len(raw): raise OSError('Short native payload write')
            completed.append(name)
            metrics.append(errors['relative_rms_error'])
        f.flush()
        os.fsync(f.fileno())
    return {'replaced_tensors': completed, 'maximum_projection_relative_rms_error': max(metrics, default=0.)}


def public_manifest():
    url = f'https://huggingface.co/api/models/{planner.BASE_REPO}/tree/{planner.BASE_REVISION}?recursive=true&limit=1000'
    with urllib.request.urlopen(url, timeout=45) as r:
        if r.headers.get('Link') and 'rel="next"' in r.headers['Link']:
            raise ValueError('Native metadata listing requires explicit pagination')
        data = json.load(r)
    files = {}
    for item in data:
        if item.get('type') != 'file': continue
        name = item['path']
        if Path(name).is_absolute() or '..' in Path(name).parts: raise ValueError('Unsafe native artifact path')
        lfs = item.get('lfs') or {}
        sha = lfs.get('oid')
        if sha and sha.startswith('sha256:'): sha = sha[7:]
        if name.endswith('.safetensors'):
            if not re.fullmatch(r'[0-9a-f]{64}', sha or '') or item.get('size',0) < 8:
                raise ValueError('Missing immutable native weight payload hash')
        elif item.get('size',0) > 32*1024*1024:
            raise ValueError('Unexpected native auxiliary file size')
        files[name] = {'size': item['size'], 'sha256': sha, 'git_blob_sha1': item.get('oid')}
    if sum(n.endswith('.safetensors') for n in files) != 48:
        raise ValueError('Unexpected native checkpoint shard count')
    return files


def download_native(name, metadata, path):
    url = f'https://huggingface.co/{planner.BASE_REPO}/resolve/{planner.BASE_REVISION}/'+urllib.parse.quote(name, safe='/')+'?download=true'
    # Retry only this idempotent public artifact download, never inference.
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={'Accept-Encoding':'identity','User-Agent':'Adverserial-native-reconstruction/1.0'})
            sha256, git = hashlib.sha256(), hashlib.sha1()
            git.update(f"blob {metadata['size']}\0".encode())
            count = 0
            with urllib.request.urlopen(request, timeout=60) as r, path.open('wb') as f:
                if r.status != 200: raise ValueError('Native download did not return a complete artifact')
                while data := r.read(CHUNK):
                    count += len(data)
                    if count > metadata['size']: raise ValueError('Native download exceeded its byte budget')
                    sha256.update(data);git.update(data);f.write(data)
                f.flush();os.fsync(f.fileno())
            if count != metadata['size']: raise ValueError('Native artifact size mismatch')
            expected = metadata.get('sha256')
            if expected and sha256.hexdigest() != expected: raise ValueError('Native weight hash mismatch')
            if not expected and git.hexdigest() != metadata.get('git_blob_sha1'): raise ValueError('Native auxiliary blob hash mismatch')
            return sha256.hexdigest()
        except Exception:
            if attempt == 2: raise ValueError('Native artifact download/integrity failed; provider details suppressed') from None
            emit('native_download_retry', file=Path(name).name, attempt=attempt+1)
            time.sleep(2**attempt)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--source-manifest', type=Path, required=True)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.source.is_symlink() or a.output.is_symlink(): raise ValueError('Artifact root must not be a symlink')
    source_root, output_root = a.source.resolve(), a.output.resolve()
    if source_root == output_root or source_root in output_root.parents or output_root in source_root.parents:
        raise ValueError('Source and new output roots must be separate')
    plan_bytes = a.plan.read_bytes();plan = expand_plan(json.loads(plan_bytes))
    if plan.get('required_patched_gguf_model_digest') != planner.SOURCE_DIGEST or plan.get('native_base') != {'repo':planner.BASE_REPO,'revision':planner.BASE_REVISION} or plan.get('projection_count') != 54:
        raise ValueError('Unexpected reconstruction plan identity')
    for key, value in {'revision':planner.PATCH_REVISION,'variant':'Q2_2','ablate_experts':2,'first_layer':11,'last_layer':28}.items():
        if plan.get('patch',{}).get(key) != value: raise ValueError('Unexpected patch scope')
    output_root.mkdir(parents=True, exist_ok=True)
    if output_root.is_symlink(): raise ValueError('Output root must not be a symlink')
    with (output_root/'.conversion.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state_path=output_root/'conversion-state.json'
        if not state_path.exists() and any(p.name != '.conversion.lock' for p in output_root.iterdir()):
            raise ValueError('Refusing an untracked nonempty output directory')
        model, work = output_root/'model', output_root/'.work'
        if model.is_symlink() or work.is_symlink() or state_path.is_symlink():
            raise ValueError('Symlink in conversion state/output layout')
        model.mkdir(exist_ok=True);work.mkdir(mode=0o700,exist_ok=True)
        identity={'source_digest':planner.SOURCE_DIGEST,'native_revision':planner.BASE_REVISION,
                  'plan_sha256':hashlib.sha256(plan_bytes).hexdigest(),
                  'converter_sha256':hash_file(Path(__file__)),
                  'quantizer_sha256':hash_file(Path(__file__).with_name('q2-native-quant.py'))}
        state=json.loads(state_path.read_text()) if state_path.exists() else {'identity':identity,'files':{}}
        if state.get('identity') != identity: raise ValueError('Existing conversion state belongs to another artifact')
        if not isinstance(state.get('files'),dict): raise ValueError('Invalid conversion-state file map')
        for name in state['files']:
            if Path(name).is_absolute() or '..' in Path(name).parts or (model/name).is_symlink():
                raise ValueError('Unsafe committed output path')
        atomic_json(state_path,state)
        source_identity(source_root,json.loads(a.source_manifest.read_text()),planner.SOURCE_DIGEST)
        emit('source_model_identity_verified', digest=planner.SOURCE_DIGEST)
        manifest=public_manifest()
        if not set(state['files']).issubset(manifest): raise ValueError('Unrecognized committed output file')
        required=sum(x['size'] for x in manifest.values())
        already=sum((model/name).stat().st_size for name in state['files'] if (model/name).is_file())
        if shutil.disk_usage(output_root).free < required-already+100*1024**3:
            raise ValueError('Insufficient free storage including 100 GiB safety margin')
        # Resolve authoritative native index first; all metadata comes from the
        # same pinned public revision and is verified as immutable Git/LFS data.
        for name in [n for n in manifest if not n.endswith('.safetensors')]:
            destination=model/name;destination.parent.mkdir(parents=True,exist_ok=True)
            temp=work/(hashlib.sha256(name.encode()).hexdigest()+'.aux')
            digest=download_native(name,manifest[name],temp)
            os.replace(temp,destination)
            state['files'][name]={'sha256':digest,'size':manifest[name]['size'],'replaced_tensors':[]}
        index=json.loads((model/'model.safetensors.index.json').read_text())
        if len(index['weight_map']) != 96085 or set(index['weight_map'].values()) != {n for n in manifest if n.endswith('.safetensors')}:
            raise ValueError('Native index coverage differs from the reviewed checkpoint')
        replacements=replacement_map(plan)
        if not set(replacements).issubset(index['weight_map']): raise ValueError('Incomplete native replacement index coverage')
        source=PatchedSource(source_root,plan)
        atomic_json(state_path,state)
        for number,name in enumerate(sorted(n for n in manifest if n.endswith('.safetensors')),1):
            destination=model/name
            prior=state['files'].get(name)
            if prior:
                if not destination.is_file() or destination.stat().st_size != prior['size'] or hash_file(destination) != prior['sha256']:
                    raise ValueError('Previously committed native shard failed revalidation')
                emit('native_shard_revalidated', shard=number, bytes=prior['size']);continue
            if destination.exists(): raise ValueError('Untracked output shard; refusing to overwrite')
            temp=work/(name+'.partial')
            emit('native_shard_download_started', shard=number, bytes=manifest[name]['size'])
            download_native(name,manifest[name],temp)
            result=patch_shard(temp,source,replacements,index['weight_map'])
            digest=hash_file(temp)
            os.replace(temp,destination)
            state['files'][name]={'sha256':digest,'size':manifest[name]['size'],**result}
            atomic_json(state_path,state)
            emit('native_shard_completed', shard=number, total_shards=48, bytes=manifest[name]['size'],
                 replaced_tensor_count=len(result['replaced_tensors']),maximum_projection_relative_rms_error=result['maximum_projection_relative_rms_error'])
        replaced=[n for x in state['files'].values() for n in x.get('replaced_tensors',[])]
        if len(replaced) != len(set(replaced)) or set(replaced) != set(replacements):
            raise ValueError('Final native patched tensor/scale coverage mismatch')
        provenance={'version':1,'artifact_semantics':plan['artifact_semantics'],'source_digest':planner.SOURCE_DIGEST,
                    'native_base':plan['native_base'],'patch':plan['patch'],'replaced_tensor_count':len(replaced),
                    'plan_sha256':identity['plan_sha256'],'converter_sha256':hash_file(Path(__file__)),
                    'numerical_maximum_relative_rms_error':max(v.get('maximum_projection_relative_rms_error',0) for v in state['files'].values()),
                    'validation_scope':'Artifact encoding only; requires independent measurement, GPU, quality, performance and downstream verification'}
        atomic_json(model/'MODEL_SOURCE.json',provenance)
        state['complete']=True;state['provenance']=provenance
        atomic_json(state_path,state)
        emit('native_reconstruction_complete', files=len(state['files'])+1,replaced_tensors=len(replaced),
             numerical_maximum_relative_rms_error=provenance['numerical_maximum_relative_rms_error'])


if __name__ == '__main__':
    try:main()
    except Exception as error:
        emit('native_reconstruction_failed',error_class=type(error).__name__)
        raise SystemExit(1)
