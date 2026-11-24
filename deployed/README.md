# Deployed composition (current reference)

This directory holds the Docker Compose file that is actually deployed on the
production confidential VM, copied byte for byte, and the app-compose JSON
document that the VM's operating system measured at boot, also byte for byte.
It is the reference for what runs behind `https://api.adverserial.ai`. The root `docker-compose.yml`
template and the `.env.example` profiles describe an earlier design and are
kept only as history (see the [root README](../README.md)).

| | |
| --- | --- |
| Files | [`2026-11-24/docker-compose.yml`](2026-11-24/docker-compose.yml) and the measured [`2026-11-24/app-compose.json`](2026-11-24/app-compose.json) |
| Status | Rolled out 2026-11-24 on OS image `dstack-0.6.5`; policy update pending first verified live attestation |
| Proxy | `ghcr.io/adverserialai/attest-proxy@sha256:5a41bcfb6d7f4f3d3df53ad5ac39a45d1d32a6db206e35c0380b81d56404824b` (tag `v0.1.0-rc.33`, source [AdverserialAI/attest-proxy](https://github.com/AdverserialAI/attest-proxy)) |
| Model server | SGLang, `lmsysorg/sglang@sha256:b4f09a2ccbcfcc262d49600c3cd7f8581a793ea7d104947257b6a95500953b90` |
| Model ID | `adverserial/cyberseek` |
| Platform | One Intel TDX confidential VM on Phala Cloud (dstack), 8× NVIDIA H200 in NVIDIA's multi-GPU Protected PCIe mode (see [GPU attestation by the OS is switched off](#gpu-attestation-by-the-os-is-switched-off)) |
| OS image | `dstack-0.6.5`, `os_image_hash` `4771a29e03e93f0e2d5e37174cd8bb1112942c9689deb36b16c5df9e183d8623`, NVIDIA driver 595.91.07 |
| App ID | `b74e3dde6292cc69bac759b22fc723a299aa8a83` |
| Endpoint | `https://api.adverserial.ai`, a CNAME to `gateway.dstack-pha1-usc1.phala.network`. The gateway passes TLS bytes through; TLS terminates in `attest-proxy` inside the VM. |
| `compose_hash` | `9c78bf82dfad3a133838f2ac87406b3fe1c9d875d8a4d5fbe04e9f34d7d3b820` (= `sha256sum 2026-11-24/app-compose.json`) |
| GPU policy in the measured manifest | `{"attest_gpu":false}`, measured as `gpu-policy-hash` `930188e08f58d0c555acff500a40be79c1f7e1d9ae23d26d901486cc9cc1a77d` |
| Compose self-digest (`COMPOSE_DIGEST`) | `sha256:96ba75d8d59d93069da02661e2e6078f82b4cd7d2745a5b8b528057809831da4` |

**What happened on 2026-11-24.** Earlier that day this page described the same
compose on OS image `dstack-nvidia-0.5.15` (`os_image_hash` `3bb7d262…4ef2`)
with `compose_hash` `275c6e53…1a1d`. That configuration never served a
request: on that image one of the eight GPUs failed to initialise at every
boot (NVIDIA driver 595.58.03). At about 14:00 UTC the hosting provider,
Phala, moved the VM to `dstack-0.6.5` (one image; there is no separate
`nvidia` variant any more) and changed two fields of the app-compose document:
`manifest_version` went from `2` to `"3"` (a string), and
`"requirements":{"gpu_policy":{"attest_gpu":false}}` was added. The Docker
Compose text is byte-identical to before, so `COMPOSE_DIGEST` did not change,
but `compose_hash` did. The proxy came up on the new image at 14:47 UTC with
keys generated inside the VM (its Let's Encrypt certificate, requested from
inside the VM, is dated 13:49 UTC), and the model was ready at 15:01 UTC. At 15:39 UTC the VM was
restarted once more to replace one sealed variable (the key for NVIDIA's
attestation service was not entitled, so the GPU evidence was stale); the
configuration and `compose_hash` are the same after that restart.

**What "policy update pending" means.** The policy published at
<https://verify.adverserial.ai/policies/production.json> still describes the
previous release (`v0.1.0-rc.30`, the earlier OS image and the earlier compose).
A new policy for this composition is published only after a first live
attestation of it has been checked; that is planned for after the 15:39 UTC
restart. Until then this file documents what was
deployed; it is not a statement that the running service has been verified
against it. No new measurements, TLS pins or receipt keys are recorded here:
the published policy is the only place for them.

## No secrets in the file

The compose contains no secret literal. It references exactly four sealed
variables, which Phala encrypts to the VM and which never appear in this
repository:

| Variable | Used by | Purpose |
| --- | --- | --- |
| `HF_TOKEN` | `model-download` | Read access to the model repository on Hugging Face. |
| `CLOUDFLARE_API_TOKEN` | `attest-proxy` | ACME DNS-01 challenge for the `api.adverserial.ai` certificate. |
| `NV_ATTESTATION_SERVICE_KEY` | `gpu-evidence-collector` | NVIDIA remote attestation service. |
| `METER_INGRESS_SHARED_SECRET` | `attest-proxy` | Capability that billing requires, in addition to the meter signature, before it accepts a usage record. |

Everything else in the file is public: image digests, hostnames, the billing
entitlement **public** key (`ENTITLEMENT_JWKS_JSON`, an Ed25519 JWK with no
private part), and `EXPECTED_MANIFEST_B64`, which is the base64 of a JSON list
of the 57 model files with their sizes and SHA-256 hashes.

The four sealed variables are operator-supplied credentials for outside
services. None of them is a signing or encryption key of the service itself.

## Keys are generated inside the VM

Since `attest-proxy` `v0.1.0-rc.31`, every private key the service uses is
generated inside the confidential VM:

- the receipt signing key (P-256) and the meter signing key (Ed25519), created
  on first boot under `ENCLAVE_KEY_DIR` (`/state/keys`);
- the HPKE/EHBP key (X25519), new on every proxy start;
- the TLS key, created in the VM and certified through ACME DNS-01.

In confidential mode the proxy refuses to start if a receipt seed, a meter
seed, an EHBP identity or an upstream bearer token is supplied as
configuration. The token the proxy uses to talk to the model server is created
inside the VM at each boot by `inference-auth-init`.

**History.** Releases up to and including `v0.1.0-rc.30` did not work this
way. Their receipt and meter keys were derived from seeds generated by the
operator on a workstation, and the EHBP identity was also operator-generated;
all three were passed to the VM as sealed configuration, as was the
proxy-to-model token. The scripts and templates for that scheme are still in
this repository as history. The `retire-old-proxy-state` service empties the
state volume those releases used once the new proxy has its own certificate.

One limit to state plainly: the operator still controls DNS for the hostname
(that is what the Cloudflare token is for), so a publicly trusted certificate
alone does not prove a connection ends in the VM. Clients must compare the TLS
key with the one bound into fresh attestation evidence.

## Services

| Service | Runs | Network | What it does |
| --- | --- | --- | --- |
| `attest-proxy` | always | `default` and `inference`; the only published port (443 → 8443) | Terminates TLS, serves attestation evidence, checks billing entitlements locally, decrypts EHBP request bodies, forwards to the model server, signs receipts and usage records. Runs as uid 65532 with a read-only root filesystem and no capabilities. Keeps certificates, keys, used entitlement IDs and the meter outbox in the `proxy-state-v2` volume. |
| `sglang` | always | `inference` only | The model server (SGLang) for `adverserial/cyberseek` on 8 GPUs. No published port. Requires the per-boot token on every request. Model volume is mounted read-only; Hugging Face access is switched off (`HF_HUB_OFFLINE`). The entrypoint is a startup ladder: it first tries the normal configuration as an unprivileged user, and only if that never becomes ready falls back to three more conservative configurations. |
| `inference-auth-init` | once per boot | none | Generates a fresh random proxy-to-model token inside the VM and writes it to a volume that the proxy and the model server mount read-only. |
| `model-download` | once per boot | `default` | Downloads the pinned model revision from Hugging Face if it is not already on the volume. |
| `model-perms` | once per boot | none | Makes the model files readable for the unprivileged measurer. |
| `model-measurer` | once per boot | none | Hashes every model file and writes a manifest. |
| `native-manifest-check` | once per boot | none | Compares the measured manifest, file by file, with the expected manifest embedded in the compose. The proxy and the model server start only if it succeeds. |
| `gpu-evidence-collector` | always | `default` | Collects NVIDIA attestation evidence for all 8 GPUs every 300 seconds and writes it to a volume the proxy reads. |
| `retire-old-proxy-state` | once | none | Empties the state volume used by releases up to `rc.30` after the new proxy has obtained its certificate. |

The `inference` network is declared `internal: true`, so the model server has
no route outside the VM and is reachable only by the proxy. This replaced the
earlier design in which the model server shared the proxy's network namespace
and listened on loopback.

Points a reviewer may want to check for themselves:

- The helper services (`model-download`, `model-perms`, `inference-auth-init`,
  `native-manifest-check`, `retire-old-proxy-state`) reuse a digest-pinned
  `vllm/vllm-openai` image only as a Python and shell toolbox. vLLM does not
  serve the model.
- `model-download` installs `huggingface_hub` with `pip` at run time, without a
  version pin, when it has to download. The downloaded files are then hashed by
  `model-measurer` in a container with no network and checked against the
  embedded manifest before anything serves them.
- `sglang` starts as root with a small set of added capabilities, prepares its
  cache directories, and on the first ladder step drops to uid 65532 before
  launching the server. It runs with `--trust-remote-code`.

## Usage records

`METER_DELIVERY_MODE` is `direct-signed`. The proxy signs each count-only usage
record with the meter key generated in the VM and posts it over TLS straight to
`https://billing.adverserial.ai`. Billing checks the signature and the shared
capability. The separate mTLS service in
[confidential-meter-ingress](https://github.com/AdverserialAI/confidential-meter-ingress)
is **not** in this path; the proxy still supports that mode, but this
deployment does not use it.

## Two different hashes

**`COMPOSE_DIGEST` (self-digest).** The value inside the file is the SHA-256 of
the file's own text with that value replaced by the placeholder
`sha256:SELF`. The proxy reports it in its evidence. To check it:

```sh
python3 - deployed/2026-11-24/docker-compose.yml <<'EOF'
import hashlib, re, sys
text = open(sys.argv[1]).read()
value = re.search(r"COMPOSE_DIGEST: (sha256:[0-9a-f]{64})", text).group(1)
print("sha256:" + hashlib.sha256(text.replace(value, "sha256:SELF").encode()).hexdigest() == value)
EOF
```

This digest is a label the workload reports about itself. It is useful for
matching a file to a deployment but it is not a hardware measurement.

**`compose_hash` (measured by dstack).** dstack does not hash this YAML file
directly. It hashes the *app-compose* JSON document, which contains this file's
text as one field together with the other deployment settings. `compose_hash`
is the SHA-256 of the exact bytes of that document as the VM received it; the
guest does not parse and re-serialise it. Those bytes are in
[`2026-11-24/app-compose.json`](2026-11-24/app-compose.json), so the check
needs no tooling:

```sh
sha256sum deployed/2026-11-24/app-compose.json
# 9c78bf82dfad3a133838f2ac87406b3fe1c9d875d8a4d5fbe04e9f34d7d3b820
```

dstack extends that value into the TDX runtime measurements (RTMR3, event
`compose-hash`) at boot, which is what ties a hardware quote to this exact
configuration. Because the JSON wraps the file, `sha256sum docker-compose.yml`
will not equal `compose_hash`, and a change of a single byte in the YAML
changes it.

The form of those bytes, for anyone rebuilding them from what the Phala API
shows for a VM: one JSON object, keys sorted, compact separators (no spaces),
UTF-8, no trailing newline; keys whose value is `null` are left out and
nothing else is. In particular `"name":""` is part of the bytes. One detail
differs between manifest versions. For `"manifest_version":2` (dstack 0.5.x)
the `"init_script":[]` that the API shows is not in the measured bytes. For
`"manifest_version":"3"` (dstack 0.6.x, this deployment) it is. An earlier
version of this page said an empty `init_script` is always dropped; that is
wrong for version 3.

To see that the JSON really wraps the compose file in this directory:

```sh
python3 - deployed/2026-11-24 <<'EOF'
import json, sys
d = sys.argv[1]
app = json.load(open(d + "/app-compose.json"))
print(app["docker_compose_file"] == open(d + "/docker-compose.yml").read())
print(app["manifest_version"], app["requirements"])
EOF
```

The same two files, an independent Python verifier and a hash-chained
attestation history are published in
[AdverserialAI/cyberseek-transparency](https://github.com/AdverserialAI/cyberseek-transparency)
(`deployments/v0.1.0-rc.33-os-0.6.5/`).

## The OS image

`dstack-0.6.5` is a public download:
<https://github.com/Dstack-TEE/dstack/releases/download/mkosi-os-v0.6.5/dstack-0.6.5.tar.gz>.
Its `os_image_hash` is the SHA-256 of the file `sha256sum.txt` inside the
archive, so anyone can recompute `4771a29e…8623`. It was built with `mkosi`
from [Dstack-TEE/dstack](https://github.com/Dstack-TEE/dstack) at the tag
`mkosi-os-v0.6.5`. The image carries no signed build provenance, and we have
not rebuilt it from source.

## GPU attestation by the OS is switched off

The measured app-compose contains
`"requirements":{"gpu_policy":{"attest_gpu":false}}`. We did not choose this;
the hosting provider set it so that the VM boots, and this is what it means.

dstack 0.6.x by default attests the GPUs at boot and requires each GPU's
Confidential Computing mode to be on before it releases the VM's keys. A
machine with eight H200 GPUs runs them in NVIDIA's multi-GPU Protected PCIe
mode, and in that mode the per-GPU Confidential Computing status reads "off".
The check in 0.6.5 does not recognise that mode and refuses to boot. Phala
therefore disabled the check for this VM, and says it will add support for
the mode and tell us when the flag can be removed.

- The protection is the same as on dstack 0.5.x, which never had this check.
- Nothing in the measured boot chain currently verifies that the GPUs are in
  their protected mode.
- The opt-out is visible to anyone who checks an attestation: it is in the
  measured app-compose (so `compose_hash` covers it), and the RTMR3 event log
  has the event `gpu-policy-hash` =
  SHA-256 of the canonical JSON `{"attest_gpu":false}` =
  `930188e08f58d0c555acff500a40be79c1f7e1d9ae23d26d901486cc9cc1a77d`, and no
  `gpu-attestation` event.
- In the meantime GPU attestation is done by our own `gpu-evidence-collector`
  through NVIDIA's remote attestation service. That proves genuine GPUs,
  firmware measurements that NVIDIA accepts, secure boot on and debug off. It
  is not bound to the TDX quote, and it does not by itself prove Protected
  PCIe mode.

When the flag is removed the app-compose changes, so `compose_hash` changes,
and this page will be updated with the new bytes.

For reference, the RTMR3 events that dstack 0.6.5 writes at boot, in order:
`system-preparing`, `app-id`, `compose-hash`, `gpu-policy-hash`,
`instance-id`, `boot-mr-done`, `os-image-hash`, `key-provider`, `storage-fs`,
`storage-encrypted`, `system-ready`.

## Related

- Proxy source and release tags: <https://github.com/AdverserialAI/attest-proxy>
- Published policy: <https://verify.adverserial.ai/policies/production.json>
- Chat client: <https://chat.adverserial.ai>, built from
  [confidential-open-webui](https://github.com/AdverserialAI/confidential-open-webui)
- Command-line client: <https://github.com/AdverserialAI/adverserial-confidential-kit>
