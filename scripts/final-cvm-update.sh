#!/usr/bin/env bash
# Executes the single production CVM update only after local rendering has
# succeeded. It never uses force-stop, stop, delete, resize, prepare-only, or
# any Docker-pruning operation.
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
confirmation=${CONFIRM_FINAL_CVM_UPDATE:-}
expected_cvm=${CVM_ID:-gpu-tee-cwpu7}

if [[ "$confirmation" != "I_UNDERSTAND_THIS_UPDATES_THE_RUNNING_CVM" ]]; then
  echo 'Refusing to update. Set CONFIRM_FINAL_CVM_UPDATE=I_UNDERSTAND_THIS_UPDATES_THE_RUNNING_CVM after reviewing the rendered configuration.' >&2
  exit 2
fi

env_file=${ENV_FILE:?Set ENV_FILE to the untracked sealed production environment file.}
pre_launch_script=${PRE_LAUNCH_SCRIPT:?Set PRE_LAUNCH_SCRIPT to the exact last-known-good Phala pre-launch script.}
pre_launch_script_sha256=${PRE_LAUNCH_SCRIPT_SHA256:?Set PRE_LAUNCH_SCRIPT_SHA256 to the exact SHA-256 of the reviewed pre-launch script.}
[ -f "$pre_launch_script" ] || { echo "refusing update: PRE_LAUNCH_SCRIPT does not exist" >&2; exit 2; }
[[ "$pre_launch_script_sha256" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo "refusing update: PRE_LAUNCH_SCRIPT_SHA256 must be sha256:<64 lowercase hex characters>" >&2; exit 2; }
actual_pre_launch_sha256="$(python3 - "$pre_launch_script" <<'PY'
import hashlib
import sys
from pathlib import Path
print("sha256:" + hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest())
PY
)"
[[ "$actual_pre_launch_sha256" == "$pre_launch_script_sha256" ]] || { echo "refusing update: PRE_LAUNCH_SCRIPT_SHA256 does not match PRE_LAUNCH_SCRIPT" >&2; exit 2; }

cd "$repo_root"
# The deployment contract must be committed and reviewed. Do not send a
# locally modified compose or preflight script to the long-running CVM.
if ! git diff --quiet -- docker-compose.yml scripts/prepare-deployment.sh || ! git diff --cached --quiet -- docker-compose.yml scripts/prepare-deployment.sh; then
  echo "refusing update: commit the reviewed compose and preflight changes first" >&2
  exit 2
fi

# A render can first print the expected digest for review. The actual update
# must use that exact value, so the value attested by the proxy cannot be a
# stale digest or the self-placeholder.
REQUIRE_COMPOSE_DIGEST_MATCH=1 ENV_FILE="$env_file" bash scripts/prepare-deployment.sh

# Ensure this is the expected live 8xH200 CVM before Phala receives any update.
cvm_json=$(phala cvms get "$expected_cvm" --json)
python3 - <<'PY' "$cvm_json"
import json, sys
cvm = json.loads(sys.argv[1])
if cvm.get("status") != "running":
    raise SystemExit("refusing update: CVM is not running")
resource = cvm.get("resource") or {}
if resource.get("instance_type") != "h200.8x.large" or resource.get("gpus") != 8:
    raise SystemExit("refusing update: unexpected CVM resource shape")
print("CVM identity and 8xH200 resource shape verified")
PY

# This is intentionally the only mutating operation in the script. The pinned
# compose and complete sealed environment are supplied together. The historical
# Compose-managed model volume is resolved by Compose itself; no bare Docker-volume
# check runs before Compose. public-tcbinfo
# remains available for independent attestation. Public logs are ON (owner
# decision 2026-10-17: crash forensics beat the secrecy of "no logs" — the
# proxy's request logging is content-free and SGLang never prints prompts).
# The image uses its immutable published slug: the dashboard's shorter display
# alias was retired even though this production GPU image remains available.
phala deploy \
  --cvm-id "$expected_cvm" \
  --instance-type h200.8x.large \
  --image dstack-nvidia-0.5.9-806a352e \
  --no-dev-os \
  --compose docker-compose.yml \
  --env "$env_file" \
  --pre-launch-script "$pre_launch_script" \
  --public-logs \
  --no-public-sysinfo \
  --public-tcbinfo \
  --secure-time \
  --wait
