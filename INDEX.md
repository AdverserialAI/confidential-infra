# Program index

The confidential platform is organized so models and nodes can be added by
policy and configuration rather than by forking the client or proxy.

| Component | Responsibility | Public evidence |
| --- | --- | --- |
| `confidential-chat` | Static browser UI; never proxies prompts | Source commit, asset-integrity manifest, GitHub build provenance |
| `confidential-sdk` | Independent endpoint verification and direct client transport | Source, release artifacts, tests, signed releases |
| `attest-proxy` | In-CVM TLS endpoint, evidence service, local entitlement check, signed meter | Source, SBOM, image digest, policy measurement |
| `confidential-policy` | Canonical model IDs, approved nodes, release policy | Signed policy and public key set |
| `billing` | Identity, reservations, entitlement signing, count-only settlement | Public entitlement JWK set and ledger audit records |
| `confidential-infra` | Reviewed deployment templates and acceptance checks | Immutable compose and image digests |

A model identifier is always canonical, for example
`lordx64/cyberglm` or `lordx64/cyberkimi`. A new node or model needs a reviewed
policy entry, an immutable runtime/model identity, and client-compatible
evidence; it does not need a new product-specific proxy.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full flow and production gates.
