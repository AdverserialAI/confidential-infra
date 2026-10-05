#!/usr/bin/env bash
# Render-only CVM preflight. It never contacts Phala, dstack, or a GPU.
# Usage: ENV_FILE=/secure/path/cc-api.env bash scripts/prepare-deployment.sh
set -euo pipefail
cd "$(dirname "$0")/.."
env_file="${ENV_FILE:?set ENV_FILE to an untracked sealed-values file}"
[ -f "$env_file" ] || { echo "ENV_FILE does not exist" >&2; exit 2; }
set -a
# shellcheck disable=SC1090
. "$env_file"
set +a
# COMPOSE_DIGEST is deliberately excluded here: it is the digest emitted below
# after normalizing its own field to sha256:SELF. This avoids a self-referential
# hash while still binding every other rendered deployment value.
for v in ATTEST_PROXY_IMAGE GPU_EVIDENCE_COLLECTOR_IMAGE MODEL_MEASURER_IMAGE SGLANG_LOOPBACK_TOKEN POLICY_ID MODEL_MANIFEST_FILE MODEL_ARTIFACT_PATH RUNTIME_DIGEST ACME_EMAIL GANDI_PAT ENTITLEMENT_JWKS_JSON METER_SIGNING_SEED RECEIPT_SIGNING_SEED METER_INGRESS_SHARED_SECRET NV_ATTESTATION_SERVICE_KEY; do
  [ -n "${!v:-}" ] || { echo "missing $v" >&2; exit 2; }
done
for image in "$ATTEST_PROXY_IMAGE" "$GPU_EVIDENCE_COLLECTOR_IMAGE" "$MODEL_MEASURER_IMAGE"; do
  [[ "$image" == *@sha256:* ]] || { echo "image must be digest-pinned: $image" >&2; exit 2; }
done
[[ "$SGLANG_LOOPBACK_TOKEN" != *$'\n'* ]] || { echo "SGLANG_LOOPBACK_TOKEN must be one line" >&2; exit 2; }
[[ "$METER_SIGNING_SEED" != *$'\n'* ]] || { echo "METER_SIGNING_SEED must be one line" >&2; exit 2; }
[[ "$RECEIPT_SIGNING_SEED" != *$'\n'* ]] || { echo "RECEIPT_SIGNING_SEED must be one line" >&2; exit 2; }
[[ "$METER_INGRESS_SHARED_SECRET" != *$'\n'* ]] || { echo "METER_INGRESS_SHARED_SECRET must be one line" >&2; exit 2; }
export COMPOSE_DIGEST="${COMPOSE_DIGEST:-sha256:SELF}"
mkdir -p dist
umask 077
raw_render="$(mktemp "${TMPDIR:-/tmp}/adverserial-compose.XXXXXX")"
trap 'rm -f "$raw_render"' EXIT
docker compose --env-file "$env_file" config > "$raw_render"
python3 - "$raw_render" dist/docker-compose.rendered.yml <<'PY'
import hashlib
import os
import sys
from pathlib import Path
raw = Path(sys.argv[1]).read_text()
# Never write a sealed value to the review artifact. Docker's rendered compose
# includes both environment values and SGLang's command line, so redact each
# secret by value before it reaches dist/ or the configuration fingerprint.
for name in (
    "SGLANG_LOOPBACK_TOKEN", "GANDI_PAT", "ENTITLEMENT_JWKS_JSON",
    "METER_SIGNING_SEED", "RECEIPT_SIGNING_SEED", "METER_INGRESS_SHARED_SECRET", "NV_ATTESTATION_SERVICE_KEY",
):
    value = os.environ.get(name, "")
    if value:
        raw = raw.replace(value, f"REDACTED_{name}")
path = Path(sys.argv[2])
path.write_text(raw)
data = raw
needle = "COMPOSE_DIGEST: "
lines = []
for line in data.splitlines(keepends=True):
    lines.append(line.split(needle, 1)[0] + needle + "sha256:SELF\n" if needle in line else line)
normalized = "".join(lines).encode()
print("deployment_configuration_digest=sha256:" + hashlib.sha256(normalized).hexdigest())
PY
echo "rendered dist/docker-compose.rendered.yml (review only; no deployment performed)"
echo "Set COMPOSE_DIGEST to the printed deployment_configuration_digest and rerun; it will remain stable."
