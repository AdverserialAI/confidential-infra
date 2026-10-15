#!/usr/bin/env bash
# Public smoke probe for an already-attested confidential deployment.
#
# This script intentionally never accepts an entitlement, API key, prompt, or
# completion. A raw JSON request would not prove EHBP encryption and could
# become an accidental plaintext inference path if a future proxy regressed.
# Use the released browser/SDK client for the encrypted end-to-end test: it
# verifies the fresh TDX/NVIDIA evidence and policy, encrypts the prompt to the
# attested EHBP key, validates the signed inference receipt, and settles the
# count-only meter record.
#
# Usage: bash scripts/e2e.sh [api-url] [chat-url]
set -euo pipefail
BASE="${1:-https://api.adverserial.ai}"
CHAT="${2:-https://chat.adverserial.ai}"
FAIL=0
check() {
  if [ "$1" = "0" ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAIL=1; fi
}

printf '%s\n' '== 1. attestation endpoint =='
R=$(curl -fsS -m 30 "$BASE/attestation?nonce=$(openssl rand -hex 16 | base64 | tr '+/' '_-' | tr -d '=')")
printf '%s' "$R" | grep -q '"tdx_quote"'; check $? 'attestation returns TDX evidence'
printf '%s' "$R" | grep -q '"gpu_evidence"'; check $? 'attestation carries NVIDIA evidence field'
printf '%s' "$R" | grep -q '"verification_receipt"'; check $? 'attestation returns signed receipt'
printf '%s' "$R" | grep -q '"dev":true' && { echo 'FAIL  dev mode evidence in production'; FAIL=1; } || echo 'PASS  not dev mode'

printf '%s\n' '== 2. discovery and public chat =='
M=$(curl -fsS -m 30 "$BASE/v1/models")
printf '%s' "$M" | grep -q 'lordx64/cyberglm'; check $? 'canonical model listed'
printf '%s' "$M" | grep -q 'confidential_verification'; check $? 'verification metadata injected'
C=$(curl -sS -m 30 -o /dev/null -w '%{http_code}' "$CHAT/")
[ "$C" = 200 ]; check $? "chat serves (got $C)"

printf '%s\n' '== 3. encrypted client gate =='
echo 'PASS  raw HTTP inference is intentionally not exercised by this script'
echo 'INFO  use a supported confidential browser/SDK client to verify the attestation, encrypt a non-sensitive test prompt, and validate its signed receipt'

if [ "$FAIL" = 0 ]; then echo 'PUBLIC SMOKE PASS'; else echo 'PUBLIC SMOKE FAILURES PRESENT'; exit 1; fi
