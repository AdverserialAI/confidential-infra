#!/usr/bin/env bash
# Render-only CVM preflight. It never contacts Phala, dstack, or a GPU.
# Usage: ENV_FILE=/secure/path/cc-api.env bash scripts/prepare-deployment.sh
set -euo pipefail

cd "$(dirname "$0")/.."
env_file="${ENV_FILE:?set ENV_FILE to an untracked sealed-values file}"
[ -f "$env_file" ] || { echo "ENV_FILE does not exist" >&2; exit 2; }

# Compose gives an inherited shell variable precedence over an env-file value.
# Clear every template input before parsing the reviewed sealed file so a local
# shell cannot silently change an attested deployment while the rendered review
# artifact appears to describe something else.
deployment_vars=(
  ATTEST_PROXY_IMAGE GPU_EVIDENCE_COLLECTOR_IMAGE MODEL_MEASURER_IMAGE SGLANG_IMAGE
  MODEL_ID SGLANG_MODEL_PATH MODEL_ARTIFACT_PATH SGLANG_TP_SIZE SGLANG_MEM_FRACTION
  SGLANG_CONTEXT_LENGTH SGLANG_MAX_RUNNING_REQUESTS SGLANG_MODEL_OPTIONS GPU_DRIVER
  GPU_COUNT GPU_EVIDENCE_EXPECTED_TYPE GPU_EVIDENCE_INTERVAL SGLANG_LOOPBACK_TOKEN
  POLICY_ID COMPOSE_DIGEST MODEL_MANIFEST_FILE RUNTIME_DIGEST ACME_EMAIL
  CLOUDFLARE_API_TOKEN ENTITLEMENT_JWKS_JSON METER_SIGNING_SEED RECEIPT_SIGNING_SEED
  METER_DELIVERY_MODE METER_URL METER_INGRESS_SHARED_SECRET NV_ATTESTATION_SERVICE_KEY
  EHBP_IDENTITY_B64 CONFIDENTIAL_ACTIVATION
)
compose_controls=(
  COMPOSE_FILE COMPOSE_PROJECT_NAME COMPOSE_PROFILES COMPOSE_ENV_FILES
  COMPOSE_PATH_SEPARATOR COMPOSE_PARALLEL_LIMIT COMPOSE_PROGRESS
)
for name in "${deployment_vars[@]}" "${compose_controls[@]}"; do
  unset "$name" || true
done

# Parse dotenv values without evaluating them as shell code. In particular,
# ENTITLEMENT_JWKS_JSON is intentionally raw JSON beginning with `{`; sourcing
# a sealed values file would execute shell syntax. Docker-style comments after
# an unquoted value are accepted, while values containing ` # ` must be quoted.
seen_names=" "
while IFS= read -r raw_line || [ -n "$raw_line" ]; do
  line="${raw_line#"${raw_line%%[![:space:]]*}"}"
  [ -z "$line" ] && continue
  [[ "$line" == \#* ]] && continue
  [[ "$line" == *=* ]] || { echo "invalid dotenv line in sealed env" >&2; exit 2; }
  name="${line%%=*}"
  value="${line#*=}"
  [[ "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { echo "invalid dotenv variable name" >&2; exit 2; }
  allowed=false
  for allowed_name in "${deployment_vars[@]}"; do
    if [[ "$name" == "$allowed_name" ]]; then
      allowed=true
      break
    fi
  done
  [[ "$allowed" == true ]] || { echo "unexpected dotenv variable: $name" >&2; exit 2; }
  [[ "$seen_names" != *" $name "* ]] || { echo "duplicate dotenv variable: $name" >&2; exit 2; }
  seen_names+="$name "
  if [[ ${#value} -ge 2 && ( ( "${value:0:1}" == '"' && "${value: -1}" == '"' ) || ( "${value:0:1}" == "'" && "${value: -1}" == "'" ) ) ]]; then
    value="${value:1:${#value}-2}"
  else
    value="${value%%[[:space:]]\#*}"
  fi
  export "$name=$value"
done < "$env_file"

required_vars=(
  ATTEST_PROXY_IMAGE GPU_EVIDENCE_COLLECTOR_IMAGE MODEL_MEASURER_IMAGE SGLANG_IMAGE
  MODEL_ID SGLANG_MODEL_PATH MODEL_ARTIFACT_PATH SGLANG_TP_SIZE SGLANG_MEM_FRACTION
  SGLANG_CONTEXT_LENGTH SGLANG_MAX_RUNNING_REQUESTS GPU_DRIVER GPU_COUNT
  GPU_EVIDENCE_EXPECTED_TYPE GPU_EVIDENCE_INTERVAL SGLANG_LOOPBACK_TOKEN POLICY_ID
  MODEL_MANIFEST_FILE RUNTIME_DIGEST ACME_EMAIL CLOUDFLARE_API_TOKEN
  ENTITLEMENT_JWKS_JSON METER_SIGNING_SEED RECEIPT_SIGNING_SEED METER_DELIVERY_MODE
  METER_URL METER_INGRESS_SHARED_SECRET NV_ATTESTATION_SERVICE_KEY EHBP_IDENTITY_B64
  CONFIDENTIAL_ACTIVATION
)
for name in "${required_vars[@]}"; do
  [ -n "${!name:-}" ] || { echo "missing $name" >&2; exit 2; }
  [[ "${!name}" != *$'\n'* && "${!name}" != *$'\r'* ]] || { echo "$name must be one line" >&2; exit 2; }
done

require_image() {
  local value=$1 expected_prefix=$2
  [[ "$value" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ && "$value" == "$expected_prefix"* ]] || {
    echo "image must be an immutable $expected_prefix digest: $value" >&2
    exit 2
  }
}
require_image "$ATTEST_PROXY_IMAGE" "ghcr.io/adverserialai/attest-proxy@"
require_image "$GPU_EVIDENCE_COLLECTOR_IMAGE" "ghcr.io/adverserialai/gpu-evidence-collector@"
require_image "$MODEL_MEASURER_IMAGE" "ghcr.io/adverserialai/model-measurer@"
require_image "$SGLANG_IMAGE" "lmsysorg/sglang@"

[[ "$RUNTIME_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "RUNTIME_DIGEST must be a SHA-256 digest" >&2; exit 2; }
[[ "${SGLANG_IMAGE##*@}" == "$RUNTIME_DIGEST" ]] || { echo "RUNTIME_DIGEST must exactly match SGLANG_IMAGE" >&2; exit 2; }
[[ "$MODEL_ID" =~ ^[a-z0-9][a-z0-9._-]{0,127}/[a-z0-9][a-z0-9._-]{0,127}$ ]] || { echo "MODEL_ID must be canonical publisher/model" >&2; exit 2; }
[[ "$POLICY_ID" =~ ^[a-z0-9][a-z0-9._-]{0,127}/[a-z0-9][a-z0-9._-]{0,127}$ ]] || { echo "POLICY_ID must be a canonical namespace/name" >&2; exit 2; }
[[ "$SGLANG_MODEL_PATH" =~ ^/data/[A-Za-z0-9][A-Za-z0-9._/-]*$ && "$SGLANG_MODEL_PATH" != *".."* ]] || { echo "SGLANG_MODEL_PATH must be a simple child path below /data" >&2; exit 2; }
[[ "$MODEL_ARTIFACT_PATH" =~ ^/[A-Za-z0-9][A-Za-z0-9._/-]*$ && "$MODEL_ARTIFACT_PATH" != *".."* ]] || { echo "MODEL_ARTIFACT_PATH must be a simple absolute child path" >&2; exit 2; }
[[ "$MODEL_MANIFEST_FILE" =~ ^/model-evidence/[A-Za-z0-9][A-Za-z0-9._/-]*$ && "$MODEL_MANIFEST_FILE" != *".."* ]] || { echo "MODEL_MANIFEST_FILE must be a simple child path below /model-evidence" >&2; exit 2; }
[[ "$SGLANG_LOOPBACK_TOKEN" =~ ^[A-Za-z0-9_-]+$ && ${#SGLANG_LOOPBACK_TOKEN} -ge 32 && ${#SGLANG_LOOPBACK_TOKEN} -le 256 ]] || { echo "SGLANG_LOOPBACK_TOKEN must be a 32..256 character base64url token" >&2; exit 2; }
[[ "$SGLANG_TP_SIZE" =~ ^[1-9][0-9]*$ && "$GPU_COUNT" =~ ^[1-9][0-9]*$ ]] || { echo "GPU_COUNT and SGLANG_TP_SIZE must be positive integers" >&2; exit 2; }
[[ "$GPU_COUNT" == "8" && "$SGLANG_TP_SIZE" == "$GPU_COUNT" ]] || { echo "this H200 profile requires 8 GPUs and matching tensor parallelism" >&2; exit 2; }
[[ "$GPU_DRIVER" == "nvidia" ]] || { echo "GPU_DRIVER must be nvidia" >&2; exit 2; }
[[ "$GPU_EVIDENCE_EXPECTED_TYPE" == "nvidia-cc" ]] || { echo "GPU_EVIDENCE_EXPECTED_TYPE must be nvidia-cc" >&2; exit 2; }
[[ "$GPU_EVIDENCE_INTERVAL" =~ ^[0-9]+$ && "$GPU_EVIDENCE_INTERVAL" -ge 60 && "$GPU_EVIDENCE_INTERVAL" -le 3600 ]] || { echo "GPU_EVIDENCE_INTERVAL must be 60..3600 seconds" >&2; exit 2; }
[[ "$SGLANG_MEM_FRACTION" =~ ^(0\.[0-9]+|1(\.0+)?)$ ]] || { echo "SGLANG_MEM_FRACTION must be in (0,1]" >&2; exit 2; }
[[ "$SGLANG_CONTEXT_LENGTH" =~ ^[1-9][0-9]*$ && "$SGLANG_CONTEXT_LENGTH" -le 1048576 ]] || { echo "SGLANG_CONTEXT_LENGTH must be 1..1048576" >&2; exit 2; }
[[ "$SGLANG_MAX_RUNNING_REQUESTS" =~ ^[1-8]$ ]] || { echo "SGLANG_MAX_RUNNING_REQUESTS must be 1..8 for this profile" >&2; exit 2; }
[[ "$CONFIDENTIAL_ACTIVATION" == "pre-activation" || "$CONFIDENTIAL_ACTIVATION" == "active" ]] || { echo "CONFIDENTIAL_ACTIVATION must be pre-activation or active" >&2; exit 2; }
[[ "$METER_DELIVERY_MODE" == "direct-signed" ]] || { echo "METER_DELIVERY_MODE must be direct-signed for this profile" >&2; exit 2; }
[[ "$METER_URL" == "https://billing.adverserial.ai" ]] || { echo "METER_URL must be the fixed billing origin for this profile" >&2; exit 2; }

# Flags that define routing, authentication, model identity, and admission are
# part of the measured compose contract. An append-only profile option cannot
# override them after the trusted command-line arguments are set above.
for protected_flag in --host --port --api-key --served-model-name --model-path --tp --mem-fraction-static --context-length --max-running-requests; do
  if [[ " ${SGLANG_MODEL_OPTIONS:-} " =~ (^|[[:space:]])${protected_flag}([=[:space:]]|$) ]]; then
    echo "SGLANG_MODEL_OPTIONS may not override protected flag $protected_flag" >&2
    exit 2
  fi
done

python3 - "$ENTITLEMENT_JWKS_JSON" <<'PY'
import json
import sys
try:
    value = json.loads(sys.argv[1])
except json.JSONDecodeError as exc:
    raise SystemExit(f"ENTITLEMENT_JWKS_JSON is not valid JSON: {exc}")
keys = value.get("keys") if isinstance(value, dict) else None
if not isinstance(keys, list) or not keys:
    raise SystemExit("ENTITLEMENT_JWKS_JSON must contain at least one public JWK")
for key in keys:
    if not isinstance(key, dict) or not isinstance(key.get("kid"), str) or not key["kid"]:
        raise SystemExit("ENTITLEMENT_JWKS_JSON contains an invalid JWK")
    if "d" in key or "k" in key:
        raise SystemExit("ENTITLEMENT_JWKS_JSON must not contain private or symmetric key material")
PY

export COMPOSE_DIGEST="${COMPOSE_DIGEST:-sha256:SELF}"
[[ "$COMPOSE_DIGEST" == "sha256:SELF" || "$COMPOSE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "COMPOSE_DIGEST must be sha256:SELF or a SHA-256 digest" >&2; exit 2; }

rendered_output="${RENDERED_COMPOSE_OUT:-dist/docker-compose.rendered.yml}"
mkdir -p "$(dirname "$rendered_output")"
umask 077
raw_render="$(mktemp "${TMPDIR:-/tmp}/adverserial-compose.XXXXXX")"
trap 'rm -f "$raw_render"' EXIT
# -f and --project-directory prevent inherited file/project selection. The
# Compose logical volume name remains unchanged, preserving its historical
# project-prefixed model volume when the reviewed deployment is run later.
docker compose --project-directory "$PWD" --env-file "$env_file" -f docker-compose.yml config > "$raw_render"
python3 - "$raw_render" "$rendered_output" <<'PY'
import hashlib
import os
import re
import sys
from pathlib import Path

raw = Path(sys.argv[1]).read_text(encoding="utf-8")
secrets = (
    "SGLANG_LOOPBACK_TOKEN", "CLOUDFLARE_API_TOKEN", "ENTITLEMENT_JWKS_JSON",
    "METER_SIGNING_SEED", "RECEIPT_SIGNING_SEED", "METER_INGRESS_SHARED_SECRET",
    "NV_ATTESTATION_SERVICE_KEY", "EHBP_IDENTITY_B64",
)
review = raw
fingerprint = raw
for name in secrets:
    value = os.environ.get(name, "")
    if value:
        review = review.replace(value, f"REDACTED_{name}")
        fingerprint = fingerprint.replace(
            value, f"SHA256_{name}_{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
        )

# The review artifact is designed for sharing. Refuse to write it if any
# supplied sealed value remains, including a value repeated in SGLang argv.
leaked = [name for name in secrets if os.environ.get(name) and os.environ[name] in review]
if leaked:
    raise SystemExit(f"refusing to write artifact: unredacted secrets present: {', '.join(leaked)}")

compose_field = re.compile(r"^(\s*COMPOSE_DIGEST:\s*).*?$", re.MULTILINE)
fingerprint, substitutions = compose_field.subn(r"\1sha256:SELF", fingerprint)
if substitutions != 1:
    raise SystemExit("rendered compose must contain exactly one COMPOSE_DIGEST field")
expected = "sha256:" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()
supplied = os.environ.get("COMPOSE_DIGEST", "sha256:SELF")
if os.environ.get("REQUIRE_COMPOSE_DIGEST_MATCH") == "1":
    if supplied == "sha256:SELF" or supplied != expected:
        raise SystemExit(
            "COMPOSE_DIGEST does not match this sealed deployment. "
            f"Set it to {expected}, then rerun the guarded update."
        )

Path(sys.argv[2]).write_text(review, encoding="utf-8")
print(f"deployment_configuration_digest={expected}")
PY
echo "rendered $rendered_output (review only; no deployment performed)"
if [[ "${REQUIRE_COMPOSE_DIGEST_MATCH:-}" == "1" ]]; then
  echo "COMPOSE_DIGEST matches the reviewed deployment configuration."
else
  echo "Set COMPOSE_DIGEST to the printed deployment_configuration_digest and rerun; it will remain stable."
fi
