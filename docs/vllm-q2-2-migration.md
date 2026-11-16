# Huihui Q2_2 to vLLM: implementation and acceptance plan

Status, November 16, 2026: migration preparation, **not deployed or GPU tested**.
The owner approved the exact isolated staging update on November 16; Phala
accepted it at 09:28 UTC (details below). Serial logs subsequently confirmed
a container-creation failure caused by the converter's 8-CPU quota. The exact
correction below is prepared but not approved or submitted. The current workspace
AGENTS.md requires explicit approval before any additional CVM change. The
later serving switch is not approved. Never stop,
shut down, delete, force-stop
or remove the on-demand rental. Preserve recovery configuration and weights;
any later recovery must also use an approved ordinary guarded update.

## Required outcome

- Serve the Huihui Q2_2-patched V4.1 model through vLLM with acceptable measured
  speed, retaining canonical `adverserial/cyberseek`.
- Retain the 64 simultaneous-session and 1,048,576-token per-request targets,
  plus the configured 65,536 output cap. Declared limits do not prove a fully
  occupied 64×1M workload fits or performs well.
- Preserve confidential-computing controls, independent fresh attestation,
  quote-bound TLS/EHBP, signed receipts, tenant authorization and metering.
- Verify ACK and chat against the exact new measured artifact/runtime/config,
  then document and publish the implementation and validation evidence.
- Do not claim success from startup, API liveness, an advertised supported
  architecture, or a small completion alone.

## Performance baseline

The serving llama.cpp workaround uses physical microbatch 32 and compile-time
no-peer-copy, with 64 independent 1M slots. It starts and passes small concurrent
encrypted inference, but its current performance is insufficient:

| Bounded encrypted test | Input tokens | First visible text | Total, 256-token cap | Native decode |
|---|---:|---:|---:|---:|
| Short prompt | 55 | 3.094 s | 11.491 s | ~27.35 tokens/s |
| Longer prompt | 9,285 | 39.935 s | 82.794 s | ~5.45 tokens/s |

Native logs also show 15,707 input tokens taking 91.962 seconds to prefill.
Entitlement overhead was approximately 0.57–0.70 seconds; independent hardware
verification took 3.401 seconds once before the two probes. Both signed
inference receipts passed. This was a shared live service, not an exclusive
benchmark; the observations establish latency, not isolated attribution to one
runtime flag. Do not blindly raise the llama.cpp microbatch: the pinned V4.1
graph has a different gather-attention eligibility path above 32 query tokens.

## Exact model and reconstruction semantics

The owner explicitly selected the **current Huihui Q2_2 patch**, not an earlier
CyberSeek artifact or the unmodified DeepSeek model:

- Serving patched artifact digest:
  `sha256:8dd425171ed78e68263a45f8d01317d991b92bc7128321464f1ab82d9a05eae8`.
- GGUF base: `unsloth/DeepSeek-V4.1-Flash-1017-GGUF`, immutable revision
  `dfcee0ec8d6b874c6663eca0fc55d90ab32534c8`, UD-Q2_K_XL.
- Patch: `huihui-ai/Huihui-DeepSeek-V4.1-Flash-1017-abliterated-GGUF`, revision
  `73fa239749dfc09e847ea5e1149626d064224d92`, Q2_2, expert mode 2, layers 11–28.
- Candidate native reference: `deepseek-ai/DeepSeek-V4.1-Flash-1017`, revision
  `08c8657f02291e11b6f6b63e0e16014f8e748518`.
- Candidate vLLM: v0.33.0, source commit
  `4ba787ec9959e566d3b587a796dff07673324da5`; reviewed AMD64 image
  `vllm/vllm-openai@sha256:91ee972ab751fb7180bc71c175865e36b091686600dca783d52b2511399810b3`.
  OCI version/source/revision labels match this release. This is metadata
  verification, not confidential H200 execution or a performance result.

Header inventory covers all seven GGUF parts and 1,043 tensors. The patch scope
contains 54 projection tensors: attention output, shared-expert down and routed
expert down in each selected layer. Native schema validation maps them to
13,896 individual native weights/scales, approximately 44.285 GB of payload.
The reference native index has 96,085 tensors across 48 shards.

The proposed reconstruction uses native-base precision for unchanged tensors
and the **actual measured, patched GGUF values** for the selected projections.
It is a new native-format mixed-precision artifact, not a byte-equivalent
conversion of all Q2 GGUF weights. Five routed-expert layer tensors are already
MXFP4 and can be repacked losslessly; remaining IQ/Q projections require
dequantization and conversion to the native encoding with fresh scales.
Quantization error and model behavior must pass validation; changing a filename
or retaining stale scales is not a conversion.

Native weight shards must contain each tensor exactly once. Do not assume an
HF index makes duplicate tensors in original and overlay shards harmless:
the loader's iteration/filter behavior must be checked. Prefer streaming
replacement into complete native shards, preserving untouched native bytes,
rather than depending on file iteration order to select patched values.

## Implementation order

1. Save and verify the currently working configuration, model identity and
   recovery pins. Keep current artifacts intact.
2. Inventory immutable metadata, validate all patch/native shapes and implement
   bounded-memory conversion. Fail closed on missing projections, mismatched
   expert counts or encodings, nonfinite values and incomplete outputs.
3. Validate real source samples, numerical round trips, provenance and atomic
   resumability before allocating or rewriting a full checkpoint. Do not print
   keys, signed download URLs, credential-bearing Compose or raw private logs.
4. Prepare the exact pinned vLLM configuration with chunked prefill, the
   appropriate native model/tokenizer/parsers and a confidential-VM-compatible
   communication path. Test GPU communication rather than assuming an engine
   change repairs the earlier unsupported CUDA peer-copy path.
5. Stage and verify the new checkpoint on encrypted storage. Keep conversion
   outside sensitive prompt paths and never publish private weights. Account
   for existing model, staging, new outputs, runtime images and safety margin;
   local workstation disk is insufficient for a full checkpoint.
6. After explicit approval, submit only a guarded ordinary CVM update with
   `allow_force_stop=false`.
   Inspect ambiguous outcomes; never stack updates or restart a slow update.
7. Validate memory, model readiness, behavior, first-token latency, decode and
   total throughput, increasing concurrency and context, streaming, tools,
   cancellation and maximum-output behavior. Keep configured versus fully
   exercised capacity separate in every report.
8. Independently verify the new measurements, publish exact policy bindings,
   and test fresh attestation, encrypted signed inference, ACK, chat and actual
   metering paths. Inspect migration ledgers read-only; no financial mutations
   just to test them. Restore the saved working setup through a normal update
   if a candidate cannot serve safely; retain performance as an open gate.

## Preparation tools and validation so far

- `scripts/gguf-tensor-inventory.py`: bounded public HTTP-range prefix reads,
  immutable revisions, explicit byte budget and no full weight download.
- `scripts/plan-q2-native-overlay.py`: complete projection/native weight-scale
  coverage and shape/encoding plan; no payload conversion or cloud mutation.
- `scripts/q2-native-quant.py`: CPU primitives using the exact serving GGUF
  decoders, lossless MXFP4 repacking, native MXFP4/block-FP8 encoding and error
  metrics. The streaming converter uses these primitives.

Five inventory tests, three projection-plan tests and four numeric tests pass.
Numeric fixtures cover every selected GGML type (Q8_0, Q6_K, IQ3_XXS, IQ3_S,
MXFP4), exact MXFP4 code/scale preservation, finite-value checks and FP4 tie
handling. Synthetic numeric tests do not replace real patched-weight checks,
model-quality comparisons or H200 execution.

`scripts/reconstruct-q2-native.py` now implements the full streaming conversion
path: verify the original measured source tree, download immutable native
shards with pinned checksums, replace the selected payloads and scales, and
commit each complete shard atomically. Resume revalidates completed shards and
rejects untracked output. Source and output trees must be distinct. No full
checkpoint has been reconstructed yet.

Five bounded real public Q2_2 samples passed the encoding checks. Dense Q8/Q6
to block FP8 relative RMS was about 2.1%; IQ3 routed-expert to native MXFP4 was
about 10.8–11.3%; the MXFP4 sample repack was exact. These are additional
quantization errors on the tested projection samples, not whole-model quality
scores or proof of equivalent behavior.

The converter was built from commit
`676a17c4b8719fc7050cff9ee9359150955c2d3b` in an isolated Linux/AMD64 CI job:
[build and tests](https://github.com/AdverserialAI/confidential-infra/actions/runs/43790758820).
Four numeric tests and five reconstruction/integrity tests passed inside the
image, together with a read-only, network-disabled startup check. Its public,
code-only package is:

`ghcr.io/adverserialai/q2-native-converter@sha256:0309127f824ab7c472f3ffeb66361e73d7b571bb1272f344bf90613fe55682fe`

The anonymous registry manifest digest was independently checked. The public
native download manifest pins 84 files, including 48 weight shards, totaling
510,311,062,017 bytes. No weights or credentials are in the converter image.

## Isolated staging preparation

`scripts/prepare-vllm-stage.py` prepares a private candidate locally. It adds:

- A CPU-only converter with a 16 GiB memory limit and one-thread numerical
  libraries, active model and evidence
  volumes mounted read-only, and a separate native output volume.
- An eight-rank communication preflight using the exact vLLM image. It sends
  only small synthetic tensors over loopback, with no model, prompts, secrets
  or external network. The proposed NCCL host-transfer settings must be tested
  on the confidential H200 topology; a new engine does not prove P2P support.

Both wait for the existing capacity observer to finish. The preparer verifies
that the active serving arguments and all unrelated settings remain identical,
except the deployment digest needed to bind the reviewed additions. It does
not call a cloud API. `scripts/test-vllm-stage-preparation.py` covers those
preservation checks and rejected configuration/image changes.

The staged configuration still uses the current llama.cpp server. The owner
approved it and Phala accepted one ordinary update at 09:28 UTC on November 16,
correlation `64764bc2ec9a787541b6a133e2b6343d`, with `allow_force_stop=false`.
Approved Docker Compose SHA-256:
`9c8455bd7ee1134f87277c72aaa92c6d493e44c2adf4ac4abfe9c76813f1ae0a`;
boot Compose hash:
`2c29262158e490646ca46cf249ac1da58c38d3daead6e2440bf68ab4dd21a225`.
Provider status was `updating` with the exact approved configuration at the
first post-acceptance checks; container observation was temporarily unavailable.
At 10:38–10:41 UTC the provider reported `error`, no operation in progress,
and `failed to start containers`. Serial logs establish that Docker rejected
the converter's `cpus: '8.0'`: its daemon reported only one available CPU.
Both new images were pulled, but container creation failed before native
conversion or GPU preflight. Host CPU counts do not override this daemon check;
why Docker reports one CPU remains unconfirmed. This was a staging configuration
mistake, not evidence that vLLM inference crashed. No stop command was issued.

Native conversion and GPU communication results remain unverified. Do not
resubmit, stack an update or perform recovery restart during this operation.
The later serving switch needs a separately reviewed configuration,
fresh measurements and policy bindings. Do not publish pins from provider
claims alone or bypass existing ACK checks during either transition.

The saved pre-staging baseline independently passed pinned hardware attestation
and two bounded encrypted sessions with verified signed receipts. Performance remains
unacceptable. Full 64×1M occupancy/recall, whole-model behavior, maximum output,
tenant isolation and downstream launch gates remain open.

`scripts/monitor-vllm-stage.mjs` observes only this approved staging configuration
through read-only provider/container/log APIs. It records allowlisted numeric
conversion progress, fresh measurement metadata and communication results;
raw logs, prompts, credentials and private traces are withheld. Temporary API
unavailability is reported without treating it as a terminal operation or
triggering any mutation. Run it with the installed Phala SDK path and a local
sanitized `--report` destination. It does not submit inference prompts or enable
additional diagnostic settings. Its two privacy/field-boundary tests run with
`node --test scripts/test-vllm-stage-monitor.mjs`.

### Prepared CPU-quota correction; new approval required

`scripts/prepare-vllm-stage-cpu-fix.py` accepts only the exact failed staging
SHA above. It removes the converter's CPU quota and updates the declared
configuration digest. Every other semantic field, serving argument, key,
volume, image, 16 GiB memory cap and one-thread library setting is preserved.
The fixed private candidate's Docker Compose SHA-256 is
`313355e984ff088cb0e7b658b66178cfec4211111e9c04787cac073058128fd8`,
declared digest
`sha256:69a6ddaf1f832bd5eb244b29aacc08f89677fb6a6e7d82266d1a16d9455c34f8`.
It is not submitted and does not change the 64-session/1M target.

The guarded ordinary update now has a narrow `--reviewed-startup-error` path.
It still defaults to read-only and requires separate explicit owner approval
before `--apply`. The opt-in requires the exact failed baseline, diagnosed
container-startup error, no active operation, unchanged on-demand rental,
KMS, app identity and reported CPU/RAM/disk/GPU resource configuration; any
other state is rejected. It only uses the ordinary
PATCH with `allow_force_stop=false`, never stop/start/restart/delete. The
initial staging approval does not authorize this additional update or the
future serving switch. Tests cover rejected states and preservation boundaries;
they do not prove a provider will accept an update from its current error state.

### Provider stopped state and resource discrepancy

At 11:00 UTC the provider status changed from `error` to `stopped`, still
with no active operation and the original staging Compose unchanged. The
guest is offline and reports the previous container-startup failure. The
rental order separately reports `active`/`on_demand`, with no completion,
cancellation or decoupling timestamp. Neither the active order nor retained
node metadata proves physical GPU allocation. No stop, shutdown, restart,
delete or rental-removal call was issued. The correction guard rejects this
new stopped state; its earlier error-state preflight is no longer valid.

The current and both saved pre-update/error baselines consistently report
128 vCPUs and 1,536 GB RAM. Therefore the provider resource metadata does not
show a resize to one CPU. Docker's one-CPU creation check and the provider's
configured resource metadata disagree; the underlying guest/daemon cause
remains unverified. Removing the unnecessary converter quota addresses the
observed validation rejection, but does not prove runtime CPU performance.

`scripts/observe-cvm-allocation.mjs` records only allowlisted resource and
rental metadata through authenticated read-only calls; it prints no Compose,
keys, account data or raw traces. Run it with `--sdk` and a sanitized `--report`
path. The ordinary update guard now rejects changed or unavailable reported
hardware allocation, and tests cover CPU/RAM/disk/GPU/type changes. These
checks do not authorize recovery from stopped state or claim GPU readiness.

The future serving generator now accepts the reviewed CPU-corrected staging
SHA `313355e984ff...` instead of the failed quota-bearing input. A private
regeneration produces the same already reviewed future serving candidate
SHA `845b1c15d53f...`: the native startup, security and engine configuration
is unchanged. Neither this regeneration nor source tests approve deployment,
prove artifact conversion or establish the requested performance/capacity.

## Primary references

- [Official native V4.1 recipe](https://github.com/vllm-project/vllm/blob/main/recipes/models/deepseek-ai/DeepSeek-V4.1-Flash.yaml)
- [Official GGUF limitations](https://github.com/vllm-project/vllm/blob/main/docs/features/quantization/gguf.md)
- [Pinned V4.1 forward conversion](https://github.com/ggml-org/llama.cpp/blob/c49edf0963a76d5e78b2a7278c9bd5b71d26c84f/conversion/deepseek41.py)
- [Pinned lossless native/GGUF MXFP4 packing](https://github.com/ggml-org/llama.cpp/blob/c49edf0963a76d5e78b2a7278c9bd5b71d26c84f/conversion/base.py#L918)
- [Pinned Huihui patch](https://huggingface.co/huihui-ai/Huihui-DeepSeek-V4.1-Flash-1017-abliterated-GGUF/blob/73fa239749dfc09e847ea5e1149626d064224d92/patch-unsloth-Q2/patch-unsloth-DeepSeek-V4.1-Flash-1017-dec.py)

## Reviewed launch target and outstanding isolation checks

`profiles/cyberseek-vllm-h200.json` replaces the earlier one-session/native-TP4
artifact proposal. It is a non-executable plan, with no credential values. A
private Compose generator must preserve backend authentication and all proxy
security controls before any later deployment. The pending staging candidate
is unchanged by this launch-plan review.

The launch target is TP8, 64 scheduled sequences, 1,048,576 context tokens per
request, a 65,536 output cap, chunked prefill and an initial 4,096-token prefill
budget. It retains vision support and ordinary graph execution. Engram host
offload and a small prefill budget are memory levers for profiling, not a
proven fast configuration. The proposed NCCL host-transport fallback must pass
the staged communication test and later realistic communication benchmarks.

Pinned Hopper attention source resolves FP8 KV to its packed DeepSeek layout;
the smaller V4.1 MXFP8/NVFP4 record is selected only on SM100. Do not borrow a
Blackwell cache-memory estimate or unsupported 4-bit KV setting for H200.
Measure actual allocated cache blocks, memory headroom and preemptions. A
64-sequence scheduler limit does not reserve 64 full 1M-token windows.

Upstream prefix caching defaults on. This initial plan explicitly disables it
and multimodal preprocessing caches until cross-tenant namespace handling is
implemented and tested. Current signed entitlements deliberately contain no
stable tenant identity, and the proxy does not bind a tenant cache salt. A
client-provided salt alone is insufficient. Cache-related prices must use
actual measured cached usage; do not invent cache hits or claim that this
uncached baseline is the final performance result. Request, output, delta and
HTTP access logging are disabled in the proposed backend; the public-log
acceptance test must also check failure paths and third-party diagnostics.

Speed acceptance must report queueing, first-token latency, prefill, decode,
end-to-end throughput and errors, compared with the measured llama.cpp
baselines. Increase concurrency through 1, 2, 4, 8, 16, 32 and 64; include
representative short prompts and near-1M independent recall tests. Full 64x1M
occupancy is a separate workload and cannot be inferred from those flags or
small probes. Prefix-cache isolation, artifact behavior, streaming, tools,
cancellation, output bounds, signed receipts and metering must all pass.

## Future serving candidate preparation

`scripts/prepare-vllm-serving.py` now makes a concrete private serving candidate
from the exact reviewed staging Compose, without calling a cloud API. It
preserves proxy keys, encrypted-body requirements, metering, independent GPU
evidence and recovery volumes. The original GGUF builder/permissions jobs
become explicit `recovery-only` profile jobs; the old startup dependency chain
is not reused for the native engine.

The new startup graph is:

1. The pinned model-measurer reads the completed native model volume read-only
   and writes a separate `native-manifest.json` on the evidence volume.
2. `scripts/check-native-artifact.py`, embedded in the existing converter-image
   service slot, validates that fresh measurement against complete converter
   state, the exact plan/source/code identities, all 48 shards and 13,896
   replaced weights/scales, native index coverage and provenance. It reads only
   bounded metadata; the separate measurer hashes the actual weight payloads.
   Both model and evidence mounts are read-only to the check, with no network,
   proxy keys or dstack socket.
3. The pinned eight-GPU communication preflight must succeed before the proxy
   starts. Neither a failed native check nor a failed communication check may
   advance to the serving engine.
4. The authenticated loopback vLLM engine starts with a read-only model, a
   non-root UID, dropped capabilities, no new privileges, a writable temporary
   compilation cache, disabled core dumps, offline model access and no developer
   routes. Proxy `RUNTIME_DIGEST` and its model-manifest path change with the
   engine; unrelated proxy authority, keys and settlement controls are retained.
5. A bounded normal `/v1/models` check records the observed canonical model,
   1M context limit and package version. It explicitly labels 64 sequences as
   configured, not a completed 64x1M workload. It sends no inference prompt and
   does not enable developer `/server_info` routes.

The private candidate is for review only. It is **not** the pending staging
update, has not been approved or submitted, and has no published replacement
policy pins. A final native digest still requires actual reconstruction and
independent verification. Readiness alone cannot establish speed, quality or
full capacity. Unit tests use small synthetic payloads and corruption cases;
static Compose validation is not GPU execution.

Optional tenant prefix-cache binding is implemented separately in
[attest-proxy 26d71a8](https://github.com/AdverserialAI/attest-proxy/commit/26d71a8370a78db39ea21a1c7b17963e73ac2cde)
and the private billing feature branch. Both remain disabled and undeployed.
The initial future serving candidate keeps prefix caching off until real
cross-account cache behavior, privacy and cached-token settlement pass.
