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
for v in ATTEST_PROXY_IMAGE GPU_EVIDENCE_COLLECTOR_IMAGE SGLANG_LOOPBACK_TOKEN POLICY_ID COMPOSE_DIGEST MODEL_DIGEST RUNTIME_DIGEST ACME_EMAIL GANDI_PAT ENTITLEMENT_JWKS_JSON METER_SIGNING_SEED; do
  [ -n "${!v:-}" ] || { echo "missing $v" >&2; exit 2; }
done
for image in "$ATTEST_PROXY_IMAGE" "$GPU_EVIDENCE_COLLECTOR_IMAGE"; do
  [[ "$image" == *@sha256:* ]] || { echo "image must be digest-pinned: $image" >&2; exit 2; }
done
[[ "$SGLANG_LOOPBACK_TOKEN" != *$'\n'* ]] || { echo "SGLANG_LOOPBACK_TOKEN must be one line" >&2; exit 2; }
[[ "$METER_SIGNING_SEED" != *$'\n'* ]] || { echo "METER_SIGNING_SEED must be one line" >&2; exit 2; }
mkdir -p dist
docker compose --env-file "$env_file" config > dist/docker-compose.rendered.yml
sha256sum dist/docker-compose.rendered.yml | awk '{print "rendered_compose_sha256=sha256:" $1}'
echo "rendered dist/docker-compose.rendered.yml (review only; no deployment performed)"
