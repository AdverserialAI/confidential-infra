# CVM configuration and activation checklist

This is the exact configuration boundary for the confidential path. It is a
review checklist only: running it changes the CVM and must be approved
separately. Do not start the confidential listener or publish an active policy
until every prerequisite below has been verified.

## 0. Hostnames after the cutover

The confidential path now owns the primary hostnames; the temporary `cc-*`
names are retired:

- `chat.adverserial.ai`: external static client (no inference proxying).
- `api.adverserial.ai`: L4/SNI route to port 443 of the CVM; TLS terminates
  only in `attest-proxy`.
- `verify.adverserial.ai`: public policy, release, and source evidence. It is
  already deployed as a public registry, currently showing the honest
  **evidence pending** state.

Billing (`billing.adverserial.ai`) is unchanged.

## 1. Release artifacts before the CVM

Build from tagged releases of `AdverserialAI/attest-proxy` and capture the two
immutable GHCR image digests (proxy and GPU-evidence collector), their GitHub
build provenance, and SBOM. Put those exact digests in the profile; never use
a mutable image tag. Publish the matching source release first.

The current `v0.1.0-rc.11` release is pinned in the H200 profile:

- proxy: `ghcr.io/adverserialai/attest-proxy@sha256:feffcfc7e2fd9f5843341205355955961be68d1647d1f360feb854ded6ef0522`
- collector: `ghcr.io/adverserialai/gpu-evidence-collector@sha256:8fb8bcae0ec83b07452344b6350d8657e2a12e7bcd7a575a76ad06002496b17b`
- model measurer: `ghcr.io/adverserialai/model-measurer@sha256:cd8eac2c773f9ef027cd9909f856b4cf57bcefdfa3b4880eaeb2e9217ee8358c`

All three images, including the read-only model measurer, were built from the public tag with GitHub provenance and SBOMs. Their
immutable manifests have been verified as publicly pullable before this
configuration was prepared. The final CVM therefore needs no registry token or
container-registry secret. Do not add credentials for a public immutable image
to compose, the model volume, or the sealed environment.

Prepare a model profile from `profiles/h200-cyberglm.env.example`. Its
canonical model ID must be `lordx64/cyberglm`. The profile is the place for
model path, tensor-parallelism, GPU count, context limit, collector evidence
type, and any model-server flags. A CyberKimi deployment gets a separate
profile with `lordx64/cyberkimi`; it does not modify the GLM policy.

## 2. CVM storage and network prerequisites

The final compose reuses exactly one pre-existing named volume:

| Volume | Mount | Allowed contents |
| --- | --- | --- |
| `cyberglm-data` (Compose logical name) | SGLang `/data` read-only | the existing model artifact only; Docker may store it with the historical Compose project prefix |

It creates two isolated private named volumes during the one final deployment:

| Volume | Mount | Allowed contents |
| --- | --- | --- |
| `proxy-state` | attest-proxy `/state` | ACME account/certificate, consumed entitlement IDs, and count-only outbox |
| `gpu-evidence-state` | collector `/evidence` read-write; proxy `/evidence` read-only | signed NVIDIA evidence only |
| `model-evidence-state` | model-measurer write; proxy read-only | deterministic model manifest only |

Both runtime images pre-create their mount points as UID/GID `65532`, so Docker
initializes an empty named volume with the correct owner at first boot. No
manual secret volume, container shell step, or mutable bootstrap process is
part of production. The collector receives no model, proxy-state, certificate,
or meter-key mount.

Expose only TCP 443 from `attest-proxy`. SGLang is `127.0.0.1:30000` in the
proxy's shared network namespace, protected by a distinct loopback credential.
There is no public SGLang port.

At DNS, `api.adverserial.ai` must use Phala’s documented L4/SNI
TLS-pass-through hostname for port 443 of this CVM: `b74e3dde6292cc69bac759b22fc723a299aa8a83-443s.dstack-pha-usc1.phala.network`.
Do not terminate TLS at Heroku, Cloudflare, Gandi, a load balancer, or a GPU
proxy. The CVM needs
outbound access only to the selected ACME CA/DNS API, the container registry,
the NVIDIA evidence service, immutable policy/source sites, and
`billing.adverserial.ai` for signed count-only meter delivery. It does not
send prompts, completions, customer API keys, or entitlements to billing.

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
| `CLOUDFLARE_API_TOKEN` | Cloudflare DNS-01 credential (Edit-zone-DNS) restricted to the `adverserial.ai` zone; must be valid through the next renewal and rotated before expiry |
| `ACME_EMAIL` | ACME incident/expiry contact |
| `ENTITLEMENT_JWKS_JSON` | billing entitlement **public** JWK set, still sealed to keep the render self-contained |
| `NV_ATTESTATION_SERVICE_KEY` | NVIDIA remote-attestation service key used only by the collector; it must not be visible to proxy or SGLang |
| `METER_DELIVERY_MODE` | `direct-signed` for this H200 deployment; this must be explicit rather than inferred |
| `METER_URL` | fixed `https://billing.adverserial.ai` origin for signed count-only delivery |
| `METER_INGRESS_SHARED_SECRET` | sealed dedicated meter capability; billing requires it in addition to a valid proxy Ed25519 JWS |
| `RECEIPT_SIGNING_SEED` | sealed 32-byte base64url P-256 seed; only the derived public JWK is published in the signed policy |
| `EHBP_IDENTITY_B64` | sealed base64url JSON identity for the RFC 9180/RFC 9458 EHBP receiver; its public key config is quote-bound and exposed only through verified evidence |

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

For the single-update production path, set `CONFIDENTIAL_ACTIVATION=active` in the final sealed file, but first set billing's `CC_ALLOWED_MODELS` to an empty value. The proxy can then collect real evidence while billing refuses to issue every confidential entitlement. Do not activate access by changing the CVM again: publish and verify the active policy first, then set `CC_ALLOWED_MODELS=lordx64/cyberglm` in billing. This leaves the raw API key at billing and enables only the canonical CyberGLM model.

Also set the non-secret release bindings: `POLICY_ID`, `MODEL_MANIFEST_FILE`,
`RUNTIME_DIGEST`, `COMPOSE_DIGEST`, `CONFIDENTIAL_ACTIVATION`, pinned image digests, `MODEL_ID`,
`SGLANG_MODEL_PATH`, `SGLANG_TP_SIZE`, `GPU_COUNT`,
`GPU_EVIDENCE_EXPECTED_TYPE`, and `SGLANG_CONTEXT_LENGTH`. Set
`NV_ATTESTATION_SERVICE_KEY` only in the sealed environment: it is a service
credential, not a public release binding. The collector fails closed unless
NVIDIA returns at least `GPU_COUNT` independently attested EATs.

For the existing H200 CVM, use the immutable Phala production GPU image slug
`dstack-nvidia-0.5.9-806a352e` (OS image hash
`806a352e16175d90568de97dff563f31f680239e6b90e9b5b2e9141d0955b0d9`). Do
not use the shorter `dstack-nvidia-0.5.9` display alias; Phala has retired it
from the image selector.

Use a Cloudflare API token (Edit-zone-DNS template) scoped to the zone only. A
one-year token is acceptable
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
- `CC_METER_INGRESS_SHARED_SECRET`: a new high-entropy count-only meter
  capability held only by billing and the CVM proxy. Billing rejects meter
  requests without it before parsing an event, then verifies the JWS.
- `CC_ALLOWED_MODELS=lordx64/cyberglm` for the first rollout.
- `CC_ENTITLEMENT_AUDIENCE=https://api.adverserial.ai` and
  `CC_METER_ISSUER=https://api.adverserial.ai`.

Generate the separate EHBP receiver identity using the released
[`attest-proxy`](https://github.com/AdverserialAI/attest-proxy) tool. It
writes a single base64url value to a mode-0600 file and never prints the
private identity:

```bash
go run ./cmd/generate-ehbp-identity --out "$HOME/.config/adverserial/ehbp-identity"
```

Set that value as `EHBP_IDENTITY_B64` in the sealed CVM environment. Its
private half stays in the CVM; only its RFC 9458 public key config appears in
fresh attestation evidence and is bound by the TDX quote.

The production H200 profile uses direct-signed count-only delivery and therefore does not require an additional CPU CVM or a public meter endpoint. The proxy opens outbound TLS 1.3 only to the fixed billing origin. It sends a compact Ed25519 JWS containing a reservation ID, request ID, canonical model ID, and token counts. Billing requires both this valid signature and `CC_METER_INGRESS_SHARED_SECRET`, then settles the reservation idempotently. It never receives prompt or completion bytes on this path.

A separately deployed [`AdverserialAI/confidential-meter-ingress`](https://github.com/AdverserialAI/confidential-meter-ingress) can be selected later for a stronger, independently mTLS-authenticated network boundary. That design requires a dedicated CPU TEE and meter client certificate rotation. Do not describe the direct-signed profile as mTLS.

## 5. Evidence, policy, and client activation

The single-update activation sequence is deliberately split between the CVM
and billing:

1. Set billing `CC_ALLOWED_MODELS` to an empty value. Its confidential route
   then rejects every entitlement request while legacy API and chat traffic are
   untouched.
2. Perform the one guarded CVM update with `CONFIDENTIAL_ACTIVATION=active`.
   No customer can reach inference because billing still issues no entitlement.
3. Obtain a fresh nonce-bound Intel TDX quote from the deployed CVM and fresh
   NVIDIA evidence from the collector. Use the independent verifier to validate
   Intel collateral and NVIDIA evidence and confirm the observed `api` TLS
   SPKI, receipt key, and event-log configuration binding.
4. Calculate and publish the canonical model artifact digest, release manifest,
   SBOM/provenance, and a signed active policy that pins model, runtime image,
   compose, TLS, and receipt key commitments. Update `verify.adverserial.ai`
   only after the files are publicly independently checkable.
5. Run the official SDK with hardware verification required. It must reject a
   missing quote, dev evidence, mismatched TLS key, expired receipt, replayed
   entitlement, incorrect canonical model ID, and failed meter settlement.
6. Only after all checks pass, set billing
   `CC_ALLOWED_MODELS=lordx64/cyberglm`. This grants the first usable
   entitlement without another CVM update.

`RUNTIME_DIGEST` is the immutable SGLang runtime image digest. The fresh TDX
quote and event log independently bind the complete rendered compose, including
that image, at verification time. Do not substitute a host or VM measurement
for this field.

Only then publish `chat` as the user-facing confidential client and guide
users to `api`. `verify.adverserial.ai` remains evidence-pending until this
sequence has produced independently verifiable evidence. The existing public
registry and client documentation fail closed before that point.

## 6. One production CVM update

Do not replace the pre-launch script used by the last known-good CVM startup
until Phala has restored that configuration. The confidential Compose profile
uses the original `cyberglm-data` **logical** volume name so Docker Compose
resolves its historical project-prefixed Docker-volume name. It intentionally
does not inspect a guessed bare Docker-volume name before Compose starts.
Model availability is checked through the mounted `/data/cyberglm-fp8` path by
the model-measurer before attest-proxy can become ready.

After every external prerequisite above has a real value and the rendered
compose has been reviewed, save the **exact** last-known-good Phala pre-launch
script locally. The update command refuses to run without that file so it
cannot silently replace the working script:

```bash
ENV_FILE=/secure/cc-api.production.env \
PRE_LAUNCH_SCRIPT=/secure/phala-last-known-good-pre-launch.sh \
CONFIRM_FINAL_CVM_UPDATE=I_UNDERSTAND_THIS_UPDATES_THE_RUNNING_CVM \
bash scripts/final-cvm-update.sh
```

The script verifies that `gpu-tee-cwpu7` is the currently running 8×H200 CVM,
reruns the redacted render validation, then performs a graceful `phala deploy`
without `--force-stop`. It disables public logs and system information, keeps
public TCB information available for independent verification, enables secure
time, and sends the complete sealed environment plus the immutable compose in
the same update. It refuses accidental execution without the explicit local
confirmation string.
