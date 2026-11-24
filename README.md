# Adverserial AI confidential infrastructure

Deployment documentation for Adverserial AI confidential inference. It contains
no production credentials, customer data or model weights.

## What is deployed

The composition that runs in production is in
[`deployed/`](deployed/README.md): the exact Docker Compose file, the measured
app-compose document, an explanation of every service, and how its hashes are
computed. In short:

- One Intel TDX confidential VM on Phala Cloud (dstack), OS image
  `dstack-0.6.5`, with 8× NVIDIA H200 in NVIDIA's multi-GPU Protected PCIe
  mode. The hosting provider moved the VM to this image on 2026-11-24; the
  configuration on `dstack-nvidia-0.5.15` that this page named earlier that
  day never served a request.
- GPU attestation by the OS is switched off in the measured configuration
  (`"attest_gpu": false`), because dstack 0.6.5 cannot check that GPU mode.
  Nothing in the measured boot chain currently verifies the GPUs' protected
  mode; [`deployed/`](deployed/README.md#gpu-attestation-by-the-os-is-switched-off)
  explains what is and is not checked instead.
- `https://api.adverserial.ai` is a CNAME to a Phala gateway that passes TLS
  bytes through. TLS terminates in
  [`attest-proxy`](https://github.com/AdverserialAI/attest-proxy)
  (`v0.1.0-rc.33`) inside the VM.
- The model server is SGLang serving `adverserial/cyberseek`. It sits on an
  internal Docker network with no published port and accepts only requests
  carrying a token generated inside the VM at each boot.
- Every private key of the service (receipt, meter, HPKE/EHBP, TLS) is
  generated inside the VM. This has been the case since proxy `v0.1.0-rc.31`;
  earlier releases used operator-generated seeds passed in as sealed
  configuration.
- Usage records are signed in the VM and posted directly to
  `https://billing.adverserial.ai`. They contain token counts, not prompts or
  completions.

Status: rolled out 2026-11-24 on `dstack-0.6.5`; policy update pending first
verified live attestation.
The policy clients verify against is published at
<https://verify.adverserial.ai/policies/production.json>. This repository is
not a source of runtime trust and does not by itself show that an endpoint is
verified: a client must check fresh hardware evidence against the published
policy.

## What in this repository is history

Everything outside `deployed/` was written for the first activation in October
2026 (model `lordx64/cyberglm`, proxy release candidates up to `rc.24`) and no
longer matches production. It is kept for the record and is marked as
historical in each file:

| Path | What it was | Why it no longer applies |
| --- | --- | --- |
| [`docker-compose.yml`](docker-compose.yml) | Render-only template for the CyberGLM deployment | Model server shared the proxy's network namespace on loopback; receipt seed, meter seed, EHBP identity and the proxy-to-model token were operator-supplied sealed values. The current proxy refuses all four. |
| [`.env.example`](.env.example), [`profiles/`](profiles/README.md) | Variable set for that template | Pins `rc.24` images and the CyberGLM model; lists the removed seed variables. The placeholders are not secrets. |
| [`CVM_CONFIGURATION.md`](CVM_CONFIGURATION.md) | Activation checklist | Names the old OS image (`dstack-nvidia-0.5.9`), the old gateway hostname and workstation key generation. |
| [`scripts/generate-confidential-key-material.py`](scripts/generate-confidential-key-material.py) | Workstation generator for the meter seed and proxy-to-model token | Those values are now generated inside the VM. Billing's entitlement signing key is still generated outside the VM, by billing. |
| [`scripts/generate-meter-mtls-material.py`](scripts/generate-meter-mtls-material.py) | Certificates for the optional mTLS meter ingress | Production uses direct signed delivery; the ingress is not deployed in that path. |
| [`scripts/prepare-deployment.sh`](scripts/prepare-deployment.sh), [`scripts/final-cvm-update.sh`](scripts/final-cvm-update.sh) | Preflight and guarded update for the template | They render the historical template. CI still runs the preflight tests so the scripts keep working as written. |
| [`backup-compose.yml`](backup-compose.yml), [`backup/`](backup/) | One-time upload of the CyberGLM model artifact | A completed one-off job for a model that is no longer served. |
| [`ARCHITECTURE.md`](ARCHITECTURE.md), [`INDEX.md`](INDEX.md) | Architecture and component index | Updated to the current design; see the notes in each file. |

Neither the template nor the example files contain a secret: sealed values are
`${VAR}` references or `SECRET_REPLACE_…` placeholders.

## What these documents do not claim

- **Reproducible builds.** Images are pinned by digest, and the proxy, GPU
  evidence collector and model measurer are built by GitHub Actions with build
  provenance and an SBOM. Nobody has shown that rebuilding the source yields
  the same image digest, so this repository does not claim it.
- **A signed policy.** The published policy is distributed over HTTPS from
  `verify.adverserial.ai` and says so itself (`detached_policy_signature:
  false`). Receipts are signed; the policy document is not.
- **mTLS metering.** Usage records go straight from the proxy to billing over
  TLS with an Ed25519 signature and a shared capability.
  [confidential-meter-ingress](https://github.com/AdverserialAI/confidential-meter-ingress)
  is an optional alternative that production does not use.

## Related repositories

| Component | Where |
| --- | --- |
| Proxy, GPU evidence collector, model measurer | <https://github.com/AdverserialAI/attest-proxy> |
| Published policy and release manifests | <https://verify.adverserial.ai> ([confidential-policy](https://github.com/AdverserialAI/confidential-policy) holds older templates only) |
| Chat client | <https://chat.adverserial.ai>, built from [confidential-open-webui](https://github.com/AdverserialAI/confidential-open-webui) |
| Command-line client | <https://github.com/AdverserialAI/adverserial-confidential-kit> |
| SDK source | <https://github.com/AdverserialAI/confidential-sdk> |
| Independent verifier and attestation history | `AdverserialAI/cyberseek-transparency` (being published) |

## Security

Please report security vulnerabilities privately to [security@adverserial.ai](mailto:security@adverserial.ai). Do not open a public issue for a suspected vulnerability.

## License

This repository is source-available proprietary software owned by Adverserial
AI LLC. See [`LICENSE`](./LICENSE) and [`LICENSING.md`](./LICENSING.md).
Third-party components retain their own terms.
