# Component index

| Component | Responsibility | Where it lives |
| --- | --- | --- |
| `attest-proxy` (with the GPU evidence collector and model measurer) | In-VM TLS endpoint, evidence service, local entitlement check, signed receipts, signed meter | <https://github.com/AdverserialAI/attest-proxy> — source, release tags, digest-pinned images with provenance and SBOM |
| Deployed composition | The Docker Compose file running in production and how its hashes are computed | [`deployed/`](deployed/README.md) in this repository |
| Published policy and release manifests | What clients verify evidence against | <https://verify.adverserial.ai/policies/production.json>. [`confidential-policy`](https://github.com/AdverserialAI/confidential-policy) holds older templates and is not the live source. |
| Chat client | Browser client; never proxies prompts | <https://chat.adverserial.ai>, built from [`confidential-open-webui`](https://github.com/AdverserialAI/confidential-open-webui). [`confidential-chat`](https://github.com/AdverserialAI/confidential-chat) is the earlier static client and is not deployed. |
| Command-line client | Verification, local gateway and coding-harness setup | <https://github.com/AdverserialAI/adverserial-confidential-kit> |
| SDK | TypeScript and Python verification libraries, gateway and plugins | <https://github.com/AdverserialAI/confidential-sdk> |
| Billing | Identity, reservations, entitlement signing, count-only settlement | `https://billing.adverserial.ai` (not public source) |
| Meter ingress | Optional mTLS boundary for meter records | <https://github.com/AdverserialAI/confidential-meter-ingress> — not used in production, which delivers signed records directly to billing |
| Independent verifier and attestation history | Python verifier, hash-chained attestation history, deployment files | `AdverserialAI/cyberseek-transparency` (being published) |

Model identifiers are canonical `publisher/model` values. The model in
production is `adverserial/cyberseek`. Earlier documents refer to
`lordx64/cyberglm` and `lordx64/cyberkimi`, which this deployment does not
serve.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the request flow and trust
boundaries.
