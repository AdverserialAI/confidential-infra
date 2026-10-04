# CVM configuration and activation checklist

This is the exact configuration boundary for the confidential path. It is a
review checklist only: running it changes the CVM and must be approved
separately. Do not start the confidential listener or publish an active policy
until every prerequisite below has been verified.

## 0. Keep the current platform unchanged

The legacy `api.adverserial.ai`, `chat.adverserial.ai`, billing, and GPU proxy
stay in place. The confidential path uses new names only:

- `cc-chat.adverserial.ai`: external static client (no inference proxying).
- `cc-api.adverserial.ai`: L4/SNI route to port 443 of the CVM; TLS terminates
  only in `attest-proxy`.
- `verify.adverserial.ai`: public policy, release, and source evidence. It is
  already deployed as a public registry, currently showing the honest
  **evidence pending** state.

Do not point `api.adverserial.ai` or `chat.adverserial.ai` at the CVM.

## 1. Release artifacts before the CVM

Build from tagged releases of `AdverserialAI/attest-proxy` and capture the two
immutable GHCR image digests (proxy and GPU-evidence collector), their GitHub
build provenance, and SBOM. Put those exact digests in the profile; never use
a mutable image tag. Publish the matching source release first.

The current `v0.1.0-rc.5` release is pinned in the H200 profile:

- proxy: `ghcr.io/adverserialai/attest-proxy@sha256:0ca9b1d793105ee5d9885990a4b2d4da37134c1ba1a5cf216a8ff429b027e5f5`
- collector: `ghcr.io/adverserialai/gpu-evidence-collector@sha256:6fd6f49726d8ba234f07d03f4222afb86112a8f3092d5e9818b423179f863d22`

Both were built from the public tag with GitHub provenance and SBOMs. The initial GHCR release-candidate images are private packages. Before any
CVM compose pull, authenticate the CVM Docker runtime to `ghcr.io` with a
dedicated read-only Packages token owned by the organization, stored in the
platform's secret/deployment mechanism. Do not place that token in compose,
the model volume, or a committed environment file. Alternatively, make the
packages explicitly public as an intentional release decision; public source
does not require public images.

Prepare a model profile from `profiles/h200-cyberglm.env.example`. Its
canonical model ID must be `lordx64/cyberglm`. The profile is the place for
model path, tensor-parallelism, GPU count, context limit, collector evidence
type, and any model-server flags. A CyberKimi deployment gets a separate
profile with `lordx64/cyberkimi`; it does not modify the GLM policy.

## 2. CVM storage and network prerequisites

Create two external volumes before rendering compose:

| Volume | Permission | Mount | Allowed contents |
| --- | --- | --- | --- |
| configured `MODEL_WEIGHTS_VOLUME` | model runtime user read-only | SGLang `/data` | model artifact only |
| `proxy-state` | UID/GID `65532:65532`, mode `0700` | proxy/collector `/state` | certificates, one-use entitlement IDs, meter outbox, GPU evidence |

The model volume must never be mounted into the proxy or evidence collector.
Expose only TCP 443 to `attest-proxy`. SGLang is `127.0.0.1:30000` in the
proxy's shared network namespace, protected with its own random loopback
credential. There is no public SGLang port.

At DNS, `cc-api.adverserial.ai` must use L4/SNI pass-through to the CVM. Do not
terminate TLS at Heroku, Cloudflare, Gandi, a load balancer, or a GPU proxy.
The CVM needs outbound access only to the selected ACME CA/DNS API, the
container registry, the NVIDIA evidence service, and `billing.adverserial.ai`
for meter delivery.

## 3. Sealed CVM environment values

Store these in Phala/dstack sealed environment configuration, never Git,
Hugging Face, a Docker image, or an ordinary shell history:

Phala replaces the sealed environment as a complete set and restarts the CVM
when it applies an environment update. Assemble and review the complete file
first; do not use `phala envs update` for an incremental secret change on a
running inference service.

| Value | Purpose |
| --- | --- |
| `SGLANG_LOOPBACK_TOKEN` | random credential used only proxy → loopback SGLang; it is the value passed to SGLang `--api-key` |
| `METER_SIGNING_SEED` | 32-byte base64url Ed25519 seed for count-only meter events |
| `GANDI_PAT` | DNS-01 credential restricted to the `adverserial.ai` zone; must be valid through the next renewal and rotated before expiry |
| `ACME_EMAIL` | ACME incident/expiry contact |
| `ENTITLEMENT_JWKS_JSON` | billing entitlement **public** JWK set, still sealed to keep the render self-contained |
| `NV_ATTESTATION_SERVICE_KEY` | NVIDIA remote-attestation service key used only by the collector; it must not be visible to proxy or SGLang |

Generate the two local trust-boundary key pairs once, on an administrator
workstation, without printing them:

```bash
python3 -m pip install cryptography
python3 scripts/generate-confidential-key-material.py --out "$HOME/.config/adverserial/cc-key-material.json"
```

The generated `billing` object provides `CC_ENTITLEMENT_PRIVATE_KEY`,
`CC_ENTITLEMENT_KEY_ID`, and `CC_METER_JWKS_JSON`; the `cvm` object provides
`ENTITLEMENT_JWKS_JSON`, `METER_SIGNING_SEED`, and `SGLANG_LOOPBACK_TOKEN`.
The script refuses to overwrite a file and creates it with mode `0600`.

After you prepare the complete sealed file, run
`ENV_FILE=/secure/path/cc-api.env bash scripts/prepare-deployment.sh`. It
redacts sealed values before writing `dist/docker-compose.rendered.yml` and
prints the `COMPOSE_DIGEST` for that exact configuration. Do not reuse a
digest generated from the example file.

Keep `CONFIDENTIAL_ACTIVATION=pre-activation` for the evidence-only boot. In this state every inference POST fails before its body is read. Set it to `active` only in the final approved activation change.

Also set the non-secret release bindings: `POLICY_ID`, `MODEL_DIGEST`,
`RUNTIME_DIGEST`, `COMPOSE_DIGEST`, `CONFIDENTIAL_ACTIVATION`, pinned image digests, `MODEL_ID`,
`SGLANG_MODEL_PATH`, `SGLANG_TP_SIZE`, `GPU_COUNT`,
`GPU_EVIDENCE_EXPECTED_TYPE`, `NV_ATTESTATION_SERVICE_KEY`, and `SGLANG_CONTEXT_LENGTH`. The collector fails closed unless NVIDIA returns at least `GPU_COUNT` independently attested EATs.

Use a Gandi token with DNS-01 permissions only. A one-year token is acceptable
only if its scope is minimal and it has an owner/rotation date; a token that
expires before the next renewal makes certificate renewal fail, but it does
not expose prompt contents.

## 4. Billing prerequisites

The confidential billing routes are implemented on the
`feature/confidential-entitlements` branch of the private platform repository.
They are **not deployed** with this document. Before an endpoint can serve a
confidential prompt, deploy that change in a staged billing release and set:

- `CC_ENTITLEMENT_PRIVATE_KEY`: a newly generated 32-byte Ed25519 base64url
  seed, stored only in billing secret storage.
- `CC_ENTITLEMENT_KEY_ID`: the corresponding stable public-key ID.
- `CC_METER_JWKS_JSON`: public JWK for the CVM meter signer derived from
  `METER_SIGNING_SEED`.
- `CC_ALLOWED_MODELS=lordx64/cyberglm` for the first rollout.
- `CC_ENTITLEMENT_AUDIENCE=https://cc-api.adverserial.ai` and
  `CC_METER_ISSUER=https://cc-api.adverserial.ai`.

Billing returns a five-minute, single-use, model-scoped entitlement after it
reserves bounded usage. The raw customer API key terminates at billing. The
CVM receives only the entitlement. After inference, it writes a signed,
count-only meter event durably, then POSTs it to billing. Billing verifies the
signature and settles the reservation idempotently.

Current billing on Heroku accepts this signed meter event over HTTPS. It does
not authenticate a TLS client certificate. Add enforced mTLS only when billing
has an ingress that actually validates the proxy client certificate; do not
claim mTLS before then.

## 5. Evidence, policy, and client activation

Before changing a policy status to `active`:

1. Obtain a fresh nonce-bound Intel TDX quote from the deployed CVM and fresh
   NVIDIA evidence from the collector.
2. Use an independent verifier to validate Intel collateral and NVIDIA evidence
   and check that they bind the **observed** `cc-api` TLS SPKI key.
3. Publish a release manifest with proxy/runtime/model/config digests, receipt
   public keys, evidence requirements, SBOM, and GitHub provenance.
4. Replace the template policy in `AdverserialAI/confidential-policy` with a
   signed active policy that pins those values; publish matching registry node
   metadata.
5. Update `verify.adverserial.ai` from **evidence pending** only after those
   files are public and independently checkable.
6. Run the official SDK with hardware verification required. It must reject a
   missing quote, dev evidence, mismatched TLS key, expired receipt, replayed
   entitlement, incorrect canonical model ID, and failed meter settlement.

For a pre-activation boot, do not invent a `RUNTIME_DIGEST`: retain the explicit `sha256:REPLACE_AFTER_REAL_TDX_MEASUREMENT` placeholder and keep `CONFIDENTIAL_ACTIVATION=pre-activation`. Obtain fresh evidence first, then publish the independently verified measurement and deploy the final pinned value before activating any policy.

Only then deploy `cc-chat` and guide users to `cc-api`. `verify.adverserial.ai` is already deployed as the public registry; its evidence-pending state is intentional until this activation sequence has produced independently verifiable evidence. The existing public
registry and client documentation intentionally fail closed before this step.
