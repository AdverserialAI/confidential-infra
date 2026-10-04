# Adverserial confidential inference — program index

Status legend: ✅ built+tested · 🟡 built, deploys at launch · 🔲 not started

| Piece | Where | Status |
|---|---|---|
| attest-proxy (Go, stdlib-only) | `attest-proxy/` | ✅ v1: in-enclave TLS (ACME DNS-01 via Gandi), /attestation (nonce+TDX+NRAS bundle), ES256 receipts, per-request receipts (WP-7), auth gate + counts-only billing tap, chat static vhost, models badge-meta, release tooling (`scripts/release.sh`) |
| GPU evidence collector | `attest-proxy/collector/` | ✅ NRAS EAT bundle, atomic refresh, failure-safe |
| SDK | `adverserial-sdk/python` + `adverserial-sdk/typescript` | ✅ verify_endpoint, SPKI pinning, VerifiedSession w/ receipt verification; 33+8 tests green |
| Harness plugins | `adverserial-sdk/plugins/opencode` + `plugins/kimi-code` | ✅ verified against live DEV proxy (pinned + TOFU modes) |
| Policy registry | `adverserial-policy/registry/` (nodes.json, models.json) | ✅ multi-model + multi-node by config edit |
| Verify site | `adverserial-policy/site/` | 🟡 skeleton (publish at launch) |
| cc-chat frontend | `adverserial-confidential-infra/chat-dist/` (from webui `feature/confidential-verification`) | ✅ built, verification UI confirmed in bundle |
| Deploy compose | `adversarial-confidential-infra/docker-compose.yml` → `scripts/build-compose.sh` → `dist/docker-compose.rendered.yml` | ✅ renders, binds proxy source into compose hash |
| E2E acceptance | `adversarial-confidential-infra/scripts/e2e.sh` | 🟡 runs at deploy |

## Gates (user action)

1. DNS: CNAME `cc-api` + `cc-chat` → `gateway.dstack-pha-usc1.phala.network`
   (or fix the Gandi PAT scope so the API can create them).
2. CVM reboot go-ahead for the compose deploy.

## After launch (queued)

- WP-6 CI: syft SBOM + cosign keyless signing on tag push (scripts exist)
- WP-11: external monitor submission
- CyberKimi on 8×B300: one `nodes.json` + one `models.json` entry
