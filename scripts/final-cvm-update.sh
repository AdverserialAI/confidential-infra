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

cd "$repo_root"
ENV_FILE="$env_file" bash scripts/prepare-deployment.sh

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
# remains available for independent attestation; logs and host sysinfo do not.
# The image uses its immutable published slug: the dashboard's shorter display
# alias was retired even though this production GPU image remains available.
phala deploy \
  --cvm-id "$expected_cvm" \
  --instance-type h200.8x.large \
  --image dstack-nvidia-0.5.9-806a352e \
  --no-dev-os \
  --compose docker-compose.yml \
  --env "$env_file" \
  --no-public-logs \
  --no-public-sysinfo \
  --public-tcbinfo \
  --secure-time \
  --wait
