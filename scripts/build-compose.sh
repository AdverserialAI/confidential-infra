#!/usr/bin/env bash
# Build the deployable CVM compose: inlines the attest-proxy Go source
# (PROXY_SRC_B64, gzipped tar) and the GPU evidence collector
# (COLLECTOR_SRC_B64, single Python file) into docker-compose.yml, writing
# dist/docker-compose.rendered.yml. The compose hash then binds both exact
# sources until signed releases exist.
set -euo pipefail
cd "$(dirname "$0")/.."

SRC_TGZ=$(mktemp /tmp/attest-proxy-src.XXXXXX.tgz)
COPYFILE_DISABLE=1 tar -czf "$SRC_TGZ" -C ../attest-proxy --exclude='.git' --exclude='dist' --exclude='__pycache__' .
B64=$(base64 < "$SRC_TGZ" | tr -d '\n')
rm -f "$SRC_TGZ"

COLLECTOR_B64=$(base64 < ../attest-proxy/collector/gpu_evidence_collector.py | tr -d '\n')

mkdir -p dist
B64F=$(mktemp /tmp/proxy-b64.XXXXXX); CBF=$(mktemp /tmp/collector-b64.XXXXXX)
printf '%s' "$B64" > "$B64F"; printf '%s' "$COLLECTOR_B64" > "$CBF"
B64F="$B64F" CBF="$CBF" python3 <<'EOF'
import os, re
b64 = open(os.environ["B64F"]).read()
collector_b64 = open(os.environ["CBF"]).read()
s = open("docker-compose.yml").read()
s = re.sub(r"\$PROXY_SRC_B64", b64, s)
s = re.sub(r"\$COLLECTOR_SRC_B64", collector_b64, s)
assert "$PROXY_SRC_B64" not in s, "PROXY_SRC_B64 placeholder left"
assert "$COLLECTOR_SRC_B64" not in s, "COLLECTOR_SRC_B64 placeholder left"
open("dist/docker-compose.rendered.yml", "w").write(s)
print(f"rendered dist/docker-compose.rendered.yml ({len(s)} bytes, proxy {len(b64)} + collector {len(collector_b64)} b64 chars)")
EOF
rm -f "$B64F" "$CBF"
