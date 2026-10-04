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

| Value | Purpose |
| --- | --- |
| `SGLANG_LOOPBACK_TOKEN` | random credential used only proxy → loopback SGLang |
| `METER_SIGNING_SEED` | 32-byte base64url Ed25519 seed for count-only meter events |
| `GANDI_PAT` | DNS-01 credential restricted to the `adverserial.ai` zone; must be valid through the next renewal and rotated before expiry |
| `ACME_EMAIL` | ACME incident/expiry contact |
| `ENTITLEMENT_JWKS_JSON` | billing entitlement **public** JWK set, still sealed to keep the render self-contained |

Also set the non-secret release bindings: `POLICY_ID`, `MODEL_DIGEST`,
`RUNTIME_DIGEST`, `COMPOSE_DIGEST`, pinned image digests, `MODEL_ID`,
`SGLANG_MODEL_PATH`, `SGLANG_TP_SIZE`, `GPU_COUNT`,
`GPU_EVIDENCE_EXPECTED_TYPE`, and `SGLANG_CONTEXT_LENGTH`.

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

Only then deploy `cc-chat` and guide users to `cc-api`. The existing public
registry and client documentation intentionally fail closed before this step.
