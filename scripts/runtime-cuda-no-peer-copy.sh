#!/usr/bin/env bash
# Runs in the existing model-download service during an approved CVM update.
# Preserve all existing model/runtime files; build one separately named runtime.
set -Eeuo pipefail
trap 'code=$?; echo runtime-build-failed:$code; exit "$code"' ERR

target=/data/cyberseek-q2
revision=c49edf0963a76d5e78b2a7278c9bd5b71d26c84f
prefix="$target-llama.cpp-no-peer-copy-c49edf0963a7"
source_dir="$prefix-src"
build_dir="$prefix-build"
binary="$prefix/bin/llama-server"

echo runtime-build-stage: validate-existing-model
python3 - "$target" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
for i in range(1, 8):
    p = root / f'DeepSeek-V4.1-Flash-1017-UD-Q2_K_XL-{i:05d}-of-00007.gguf'
    if not p.is_file() or p.stat().st_size < 24:
        raise SystemExit('Model shard missing or incomplete; model files left unchanged')
    with p.open('rb') as stream:
        if stream.read(4) != b'GGUF':
            raise SystemExit('Invalid GGUF header; model files left unchanged')
projector = root / 'mmproj-BF16.gguf'
if not projector.is_file() or projector.stat().st_size < 24:
    raise SystemExit('Projector missing or incomplete; model files left unchanged')
try:
    manifest = json.loads((root / 'MODEL_SOURCE.json').read_text())
except Exception:
    raise SystemExit('Model provenance unavailable; model files left unchanged')
expected = {
    'base_revision': 'dfcee0ec8d6b874c6663eca0fc55d90ab32534c8',
    'patch_revision': '73fa239749dfc09e847ea5e1149626d064224d92',
    'variant': 'UD-Q2_K_XL', 'ablate_experts': 2, 'layers': [11, 28],
}
if any(manifest.get(k) != v for k, v in expected.items()):
    raise SystemExit('Model provenance does not match reviewed deployment; files left unchanged')
print('runtime-build-stage: model-provenance-checked')
PY

# The preparer embeds the separately reviewed, user-authorized cleanup here.
# EMBED_OBSOLETE_MODEL_CLEANUP

if [ -x "$binary" ] && [ -s "$prefix/BUILD_INFO.json" ]; then
    python3 - "$prefix" "$revision" <<'PY'
import hashlib, json, pathlib, sys
root, revision = pathlib.Path(sys.argv[1]), sys.argv[2]
try:
    manifest = json.loads((root / 'BUILD_INFO.json').read_text())
    actual = hashlib.sha256((root / 'bin/llama-server').read_bytes()).hexdigest()
except Exception:
    raise SystemExit('Cached runtime validation failed; existing files left unchanged')
if (manifest.get('revision') != revision or manifest.get('cuda_no_peer_copy') is not True
        or manifest.get('cuda_architectures') != '90' or manifest.get('binary_sha256') != actual):
    raise SystemExit('Cached runtime identity mismatch; existing files left unchanged')
print('runtime-build-stage: verified-cached-build')
PY
    exit 0
fi

if ! command -v git >/dev/null 2>&1 || ! command -v cmake >/dev/null 2>&1 || ! command -v cc >/dev/null 2>&1; then
    echo runtime-build-stage: dependencies
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git cmake build-essential
fi

echo runtime-build-stage: pinned-source
if [ ! -d "$source_dir/.git" ]; then
    if [ -e "$source_dir" ]; then
        echo runtime-build-error: source-directory-exists-without-git
        exit 1
    fi
    git clone --depth 1 --branch b11879 https://github.com/ggml-org/llama.cpp.git "$source_dir"
fi
test "$(git -C "$source_dir" rev-parse HEAD)" = "$revision"
git -C "$source_dir" diff --quiet
git -C "$source_dir" diff --cached --quiet
test -z "$(git -C "$source_dir" ls-files --others --exclude-standard)"

echo runtime-build-stage: configure-no-peer-copy
cmake -S "$source_dir" -B "$build_dir" \
    -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
    -DGGML_CUDA=ON -DGGML_NATIVE=OFF -DCMAKE_CUDA_ARCHITECTURES=90 \
    -DGGML_CUDA_NO_PEER_COPY=ON -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
grep -q '^GGML_CUDA_NO_PEER_COPY:BOOL=ON$' "$build_dir/CMakeCache.txt"
python3 - "$build_dir/compile_commands.json" <<'PY'
import json, sys
commands = json.load(open(sys.argv[1]))
cuda = [c for c in commands if c.get('file', '').endswith('/ggml-cuda.cu')]
if not cuda or not all('GGML_CUDA_NO_PEER_COPY' in (c.get('command', '') + ' '.join(c.get('arguments', []))) for c in cuda):
    raise SystemExit('Peer-copy disabling macro missing from CUDA compilation commands')
print('runtime-build-stage: compiler-peer-copy-guard-verified')
PY
echo runtime-build-stage: compile
cmake --build "$build_dir" --config Release --target llama-server -j 16

mkdir -p "$prefix/bin"
install -m 0755 "$build_dir/bin/llama-server" "$prefix/bin/llama-server.pending"
mv "$prefix/bin/llama-server.pending" "$binary"
python3 - "$prefix" "$revision" <<'PY'
import hashlib, json, pathlib, sys
root, revision = pathlib.Path(sys.argv[1]), sys.argv[2]
manifest = {
    'revision': revision, 'cuda_architectures': '90', 'cuda_no_peer_copy': True,
    'binary_sha256': hashlib.sha256((root / 'bin/llama-server').read_bytes()).hexdigest(),
}
temporary = root / 'BUILD_INFO.json.pending'
temporary.write_text(json.dumps(manifest, sort_keys=True) + '\n')
temporary.replace(root / 'BUILD_INFO.json')
print('runtime-build-stage: build-identity-written')
PY
echo runtime-build-stage: complete
