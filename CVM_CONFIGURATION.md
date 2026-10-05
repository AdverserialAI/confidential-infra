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

The current `v0.1.0-rc.7` release is pinned in the H200 profile:

- proxy: `ghcr.io/adverserialai/attest-proxy@sha256:f2f9f6dff2f68405d2db3f115764a9a6d59f18dd916bf30a93c8a882d4df4de8`
- collector: `ghcr.io/adverserialai/gpu-evidence-collector@sha256:7b2853811da6acb40b3f06903571fb1e32e56a1e869de0ee1989303a3493cf39`

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

The final compose reuses exactly one pre-existing named volume:

| Volume | Mount | Allowed contents |
| --- | --- | --- |
| `cyberglm-data` | SGLang `/data` read-only | the existing model artifact only |

It creates two isolated private named volumes during the one final deployment:

| Volume | Mount | Allowed contents |
| --- | --- | --- |
| `proxy-state` | attest-proxy `/state` | ACME account/certificate, consumed entitlement IDs, count-only outbox, and the proxy’s locally materialized mTLS credential |
| `gpu-evidence-state` | collector `/evidence` read-write; proxy `/evidence` read-only | signed NVIDIA evidence only |

Both runtime images pre-create their mount points as UID/GID `65532`, so Docker
initializes an empty named volume with the correct owner at first boot. No
manual secret volume, container shell step, or mutable bootstrap process is
part of production. The collector receives no model, proxy-state, certificate,
or meter-key mount.

Expose only TCP 443 from `attest-proxy`. SGLang is `127.0.0.1:30000` in the
proxy's shared network namespace, protected by a distinct loopback credential.
There is no public SGLang port.

At DNS, `cc-api.adverserial.ai` must use Phala’s documented L4/SNI
TLS-pass-through hostname for port 443 of this CVM. Do not terminate TLS at
Heroku, Cloudflare, Gandi, a load balancer, or a GPU proxy. The CVM needs
outbound access only to the selected ACME CA/DNS API, the container registry,
the NVIDIA evidence service, immutable policy/source sites, and the dedicated
`meter-ingress.adverserial.ai` endpoint for count-only delivery. It does not
directly call `billing.adverserial.ai`.

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
| `METER_URL` | exact external mTLS ingress origin, e.g. `https://meter-ingress.adverserial.ai`; never the Heroku billing origin |
| `METER_TLS_BUNDLE_B64` | sealed one-line base64url JSON containing `client_cert_pem`, `client_key_pem`, and `ingress_ca_pem`; attest-proxy validates it and writes private files under `proxy-state` at boot |
| `RECEIPT_SIGNING_SEED` | sealed 32-byte base64url P-256 seed; only the derived public JWK is published in the signed policy |

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
- `CC_METER_INGRESS_SHARED_SECRET`: a new high-entropy shared secret held
  only by billing and `confidential-meter-ingress`. Billing rejects direct
  `/cc/meter` requests without the ingress header before parsing an event.
- `CC_ALLOWED_MODELS=lordx64/cyberglm` for the first rollout.
- `CC_ENTITLEMENT_AUDIENCE=https://cc-api.adverserial.ai` and
  `CC_METER_ISSUER=https://cc-api.adverserial.ai`.

Deploy [`AdverserialAI/confidential-meter-ingress`](https://github.com/AdverserialAI/confidential-meter-ingress)
outside the CVM, at `meter-ingress.adverserial.ai`, before configuring the CVM.
It must have a true L4/TCP pass-through edge so its Go process terminates TLS
and validates the dedicated CVM client certificate itself. Give it a fixed
HTTPS upstream of `https://billing.adverserial.ai/cc/meter`, its server
certificate/key, the client CA public certificate, the expected client SPKI
fingerprint, and the same `CC_METER_INGRESS_SHARED_SECRET`.

Generate dedicated material on an administrator workstation; the script never
prints a private key and writes files with mode `0600`:

```bash
python3 -m pip install cryptography
python3 scripts/generate-meter-mtls-material.py \
  --ingress-hostname meter-ingress.adverserial.ai \
  --cvm-out "$HOME/.config/adverserial/cc-meter-cvm" \
  --ingress-out "$HOME/.config/adverserial/cc-meter-ingress"
```

Encode only `client.crt`, `client.key`, and `ingress-ca.crt` from the CVM
output into the sealed `METER_TLS_BUNDLE_B64` deployment value; configure the
ingress with its separate server files and `client-ca.crt`. The proxy validates
this bundle and writes the private key only to its isolated state volume. Use
the emitted `EXPECTED_CLIENT_SPKI_SHA256` at the ingress. Never put the CVM
client key in billing, the ingress host, an image, Git, or an unsealed file.

Billing returns a five-minute, single-use, model-scoped entitlement after it
reserves bounded usage. The raw customer API key terminates at billing. The
CVM receives only the entitlement. After inference it durably writes a signed,
count-only meter event, then sends it over TLS 1.3 with its client certificate
to the ingress. The ingress forwards the unchanged envelope over its fixed
HTTPS billing connection. Billing verifies both the ingress secret and the
proxy's Ed25519 signature, then settles the reservation idempotently.

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
