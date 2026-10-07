#!/usr/bin/env bash
# Acceptance probe for an already-attested confidential deployment.
#
# Prerequisites:
# 1. Run the official SDK / independent hardware verifier first.
# 2. Obtain one freshly verified entitlement from billing (it is single-use).
# This script never takes a normal sk- API key and cannot substitute for
# independent TDX/NVIDIA validation.
# Usage: E2E_ENTITLEMENT='eyJ...' bash scripts/e2e.sh [api-url] [chat-url]
set -euo pipefail
BASE="${1:-https://api.adverserial.ai}"
CHAT="${2:-https://chat.adverserial.ai}"
ENTITLEMENT="${E2E_ENTITLEMENT:?set a fresh billing-issued one-use entitlement}"
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

printf '%s\n' '== 3. confidential authorization =='
CODE=$(curl -sS -m 30 -o /dev/null -w '%{http_code}' -X POST "$BASE/v1/chat/completions" -H 'Content-Type: application/json' -d '{"model":"lordx64/cyberglm","max_tokens":32,"messages":[{"role":"user","content":"hi"}]}')
[ "$CODE" = 401 ]; check $? "missing entitlement rejected (got $CODE)"
R=$(curl -fsS -m 180 -X POST "$BASE/v1/chat/completions" -H "Authorization: Bearer $ENTITLEMENT" -H 'Content-Type: application/json' -d '{"model":"lordx64/cyberglm","max_tokens":32,"messages":[{"role":"user","content":"Reply PONG only."}]}')
printf '%s' "$R" | grep -q 'PONG'; check $? 'entitlement-authorized inference answers PONG'
printf '%s' "$R" | grep -q '"usage"'; check $? 'usage present for signed settlement'
CODE=$(curl -sS -m 30 -o /dev/null -w '%{http_code}' -X POST "$BASE/v1/chat/completions" -H "Authorization: Bearer $ENTITLEMENT" -H 'Content-Type: application/json' -d '{"model":"lordx64/cyberglm","max_tokens":32,"messages":[{"role":"user","content":"replay"}]}')
[ "$CODE" = 401 ]; check $? "replayed entitlement rejected (got $CODE)"

if [ "$FAIL" = 0 ]; then echo 'ALL E2E PASS'; else echo 'E2E FAILURES PRESENT'; exit 1; fi
