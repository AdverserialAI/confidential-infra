# Adverserial AI confidential infrastructure

Public architecture, policy templates, and deployment-validation material for
Adverserial AI confidential inference. It contains no production credentials,
private addresses, customer data, or model weights.

Read [ARCHITECTURE.md](ARCHITECTURE.md) for the end-to-end diagram, trust
boundaries, TLS termination point, billing data contract, and evidence needed
before production verification is enabled.

The repository documents a target design. It must not be used to claim that an
endpoint is verified until a client independently validates fresh hardware
evidence against an active signed policy.

## CVM deployment preparation

`docker-compose.yml` is intentionally a **render-only template**. It has no
deployable defaults: it requires immutable proxy and GPU-collector image
digests, sealed credentials, a published policy identifier, and measured
runtime/model digests. The template does not host `cc-chat`; the static
browser app is external and connects directly to `cc-api` after verification.

Before any CVM change, create an untracked sealed-values file from
`.env.example` and run:

```sh
ENV_FILE=/secure/path/cc-api.env bash scripts/prepare-deployment.sh
```

This validates required values, refuses unpinned release images, and writes
`dist/docker-compose.rendered.yml` for review. It does **not** contact Phala,
dstack, a GPU, Gandi, billing, or production DNS.

The only pre-existing CVM volume is the existing model volume (`cyberglm-data`),
mounted read-only into SGLang as `/data`. Docker creates two private named
volumes on the final deployment: `proxy-state` for ACME/replay/outbox state and
a separate `gpu-evidence-state` for collector output. The proxy image
pre-creates its state directory with UID/GID `65532`, and the collector image
pre-creates its evidence directory with the same ownership. The collector can
write evidence but cannot read meter credentials, replay state, ACME state, or
model weights.

The proxy receives the mTLS client certificate, private key, and ingress CA as
one sealed `METER_TLS_BUNDLE_B64` value. It validates and atomically
materializes those files with mode `0600` into its own private state at boot;
there is no manually provisioned credential volume. SGLang
shares the proxy network namespace and binds only `127.0.0.1:30000`; it also
requires a distinct sealed loopback token. Customer API keys terminate at
billing and the CVM receives only one-use billing entitlements.

`METER_SIGNING_SEED`, `SGLANG_LOOPBACK_TOKEN`, and `GANDI_PAT` are sealed
values. `ENTITLEMENT_JWKS_JSON` is public key material, but is included in the
sealed deployment set to keep the rendered compose self-contained. A
subsequent real attestation/measurement step is required before publishing a
policy or enabling clients; do not set a production endpoint to verified
before that evidence is independently validated.

The proxy and collector release workflows build digest-pinned images with
GitHub provenance and SBOM artifacts. Copy the resulting immutable GHCR
digests into the sealed values file; never use a mutable tag in the compose.

## Security

Please report security vulnerabilities privately to [security@adverserial.ai](mailto:security@adverserial.ai). Do not open a public issue for a suspected vulnerability.
