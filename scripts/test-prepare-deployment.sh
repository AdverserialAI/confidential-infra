#!/usr/bin/env bash
# Regression tests for the render-only confidential deployment preflight.
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/adverserial-preflight-test.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
base_env="$tmp/sealed.env"
review="$tmp/review.yml"

cat > "$base_env" <<'ENV'
ATTEST_PROXY_IMAGE=ghcr.io/adverserialai/attest-proxy@sha256:96970a38e0d305d502459e69766c72f5fbdb0f6257dc5737a562287732838587
GPU_EVIDENCE_COLLECTOR_IMAGE=ghcr.io/adverserialai/gpu-evidence-collector@sha256:46addca873922b51f95226b2fb44eb3e5aea298bf0a295d1ea62ee20ada34bfd
MODEL_MEASURER_IMAGE=ghcr.io/adverserialai/model-measurer@sha256:c459e8f3f5d6fcb662ec7a471c02e6f6af27074e6253ff60774afa6487a95f20
SGLANG_IMAGE=lmsysorg/sglang@sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469
MODEL_ID=lordx64/cyberglm
SGLANG_MODEL_PATH=/data/cyberglm-fp8
MODEL_ARTIFACT_PATH=/cyberglm-fp8
SGLANG_TP_SIZE=8
SGLANG_MEM_FRACTION=0.8
SGLANG_CONTEXT_LENGTH=1048576
SGLANG_MAX_RUNNING_REQUESTS=8
SGLANG_MODEL_OPTIONS='--trust-remote-code --reasoning-parser glm45 --tool-call-parser glm47'
GPU_DRIVER=nvidia
GPU_COUNT=8
GPU_EVIDENCE_EXPECTED_TYPE=nvidia-cc
GPU_EVIDENCE_INTERVAL=300
NV_ATTESTATION_SERVICE_KEY=test-nvidia-attestation-key
SGLANG_LOOPBACK_TOKEN=test_loopback_token_0123456789abcdef
POLICY_ID=adverserial-policy/lordx64-cyberglm
CONFIDENTIAL_ACTIVATION=pre-activation
COMPOSE_DIGEST=sha256:SELF
MODEL_MANIFEST_FILE=/model-evidence/manifest.json
RUNTIME_DIGEST=sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469
ACME_EMAIL=ops@example.test
CLOUDFLARE_API_TOKEN=test-cloudflare-token
ENTITLEMENT_JWKS_JSON={"keys":[{"kty":"OKP","crv":"Ed25519","x":"8cNf9JYyJQ4fP0Ue5LjbPSVCzAwZpx6f7GRbIlG9R_0","kid":"billing-test"}]}
METER_DELIVERY_MODE=direct-signed
METER_URL=https://billing.adverserial.ai
METER_INGRESS_SHARED_SECRET=test-meter-ingress-secret
METER_SIGNING_SEED=test-meter-signing-seed
RECEIPT_SIGNING_SEED=test-receipt-signing-seed
EHBP_IDENTITY_B64=test-ehbp-identity
ENV

run_preflight() {
  ENV_FILE="$1" RENDERED_COMPOSE_OUT="$review" bash "$repo_root/scripts/prepare-deployment.sh"
}

expect_rejected() {
  local name=$1 env_file=$2
  if run_preflight "$env_file" >/dev/null 2>&1; then
    echo "expected preflight rejection: $name" >&2
    exit 1
  fi
}

# Use Python directly so replacements remain literal (not regex or shell code).
replace_value() {
  local destination=$1 old=$2 new=$3
  python3 - "$base_env" "$destination" "$old" "$new" <<'PY'
import sys
from pathlib import Path
source = Path(sys.argv[1])
destination = Path(sys.argv[2])
old = sys.argv[3]
new = sys.argv[4]
text = source.read_text(encoding="utf-8")
if old not in text:
    raise SystemExit(f"test fixture text not found: {old!r}")
destination.write_text(text.replace(old, new, 1), encoding="utf-8")
PY
}

output=$(SGLANG_IMAGE='lmsysorg/sglang@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' run_preflight "$base_env")
digest=$(printf '%s\n' "$output" | sed -n 's/^deployment_configuration_digest=//p')
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "missing rendered compose digest" >&2; exit 1; }
for secret in test_loopback_token_0123456789abcdef test-cloudflare-token test-meter-ingress-secret test-nvidia-attestation-key test-ehbp-identity; do
  ! grep -Fq "$secret" "$review" || { echo "secret leaked into review artifact: $secret" >&2; exit 1; }
done
grep -Fq 'lmsysorg/sglang@sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469' "$review"
grep -Fq -- '--max-running-requests' "$review"
grep -Fq -- '"8"' "$review"
[[ "$(grep -Fc 'network_mode: none' "$review")" == "2" ]] || { echo "model helper network isolation missing" >&2; exit 1; }

strict_env="$tmp/strict.env"
replace_value "$strict_env" 'COMPOSE_DIGEST=sha256:SELF' "COMPOSE_DIGEST=$digest"
ENV_FILE="$strict_env" RENDERED_COMPOSE_OUT="$review" REQUIRE_COMPOSE_DIGEST_MATCH=1 bash "$repo_root/scripts/prepare-deployment.sh" >/dev/null

bad_options="$tmp/bad-options.env"
replace_value "$bad_options" '--trust-remote-code --reasoning-parser glm45 --tool-call-parser glm47' '--trust-remote-code --max-running-requests 64'
expect_rejected "protected SGLang option override" "$bad_options"

bad_image="$tmp/bad-image.env"
replace_value "$bad_image" 'SGLANG_IMAGE=lmsysorg/sglang@sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469' 'SGLANG_IMAGE=lmsysorg/sglang:latest'
expect_rejected "mutable SGLang image" "$bad_image"

bad_path="$tmp/bad-path.env"
replace_value "$bad_path" 'SGLANG_MODEL_PATH=/data/cyberglm-fp8' 'SGLANG_MODEL_PATH=/data/cyberglm-fp8 --host 0.0.0.0'
expect_rejected "model path command injection" "$bad_path"

bad_token="$tmp/bad-token.env"
replace_value "$bad_token" 'SGLANG_LOOPBACK_TOKEN=test_loopback_token_0123456789abcdef' 'SGLANG_LOOPBACK_TOKEN=token --host 0.0.0.0'
expect_rejected "loopback token command injection" "$bad_token"

bad_runtime="$tmp/bad-runtime.env"
replace_value "$bad_runtime" 'RUNTIME_DIGEST=sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469' 'RUNTIME_DIGEST=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
expect_rejected "runtime/image mismatch" "$bad_runtime"

bad_control="$tmp/bad-control.env"
cp "$base_env" "$bad_control"
printf '%s\n' 'COMPOSE_PROJECT_NAME=attacker-selected-project' >> "$bad_control"
expect_rejected "Compose control variable from sealed env" "$bad_control"

changed_secret="$tmp/changed-secret.env"
replace_value "$changed_secret" 'METER_SIGNING_SEED=test-meter-signing-seed' 'METER_SIGNING_SEED=test-meter-signing-seed-rotated'
python3 - "$changed_secret" "$digest" <<'PY'
import sys
from pathlib import Path
path = Path(sys.argv[1])
path.write_text(path.read_text().replace('COMPOSE_DIGEST=sha256:SELF', f'COMPOSE_DIGEST={sys.argv[2]}'), encoding='utf-8')
PY
if ENV_FILE="$changed_secret" RENDERED_COMPOSE_OUT="$review" REQUIRE_COMPOSE_DIGEST_MATCH=1 bash "$repo_root/scripts/prepare-deployment.sh" >/dev/null 2>&1; then
  echo "expected digest mismatch after sealed value rotation" >&2
  exit 1
fi

echo "prepare-deployment regression tests passed"
