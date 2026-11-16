# Huihui Q2_2 to vLLM: implementation and acceptance plan

Status, November 16, 2026: migration preparation, **not deployed or GPU tested**.
The owner authorizes normal updates of the existing 8×H200 Phala GPU CVM for
this migration without another approval prompt. Never stop, shut down, delete,
force-stop or remove the on-demand rental. Preserve recovery configuration and
weights; recover through an ordinary guarded update if necessary.

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
  `4ba787ec9959e566d3b587a796dff07673324da5`; image digest and flags must be
  independently revalidated before a deployment candidate is accepted.

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
6. Submit only a guarded ordinary CVM update with `allow_force_stop=false`.
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
  metrics. These are building blocks, not a completed checkpoint converter.

Five inventory tests, three projection-plan tests and four numeric tests pass.
Numeric fixtures cover every selected GGML type (Q8_0, Q6_K, IQ3_XXS, IQ3_S,
MXFP4), exact MXFP4 code/scale preservation, finite-value checks and FP4 tie
handling. Synthetic numeric tests do not replace real patched-weight checks,
model-quality comparisons or H200 execution.

The full streaming converter, real patched samples, GPU execution and all
end-to-end acceptance gates remain incomplete. No CVM mutation has been made
as part of this vLLM preparation at this checkpoint.

## Primary references

- [Official native V4.1 recipe](https://github.com/vllm-project/vllm/blob/main/recipes/models/deepseek-ai/DeepSeek-V4.1-Flash.yaml)
- [Official GGUF limitations](https://github.com/vllm-project/vllm/blob/main/docs/features/quantization/gguf.md)
- [Pinned V4.1 forward conversion](https://github.com/ggml-org/llama.cpp/blob/c49edf0963a76d5e78b2a7278c9bd5b71d26c84f/conversion/deepseek41.py)
- [Pinned lossless native/GGUF MXFP4 packing](https://github.com/ggml-org/llama.cpp/blob/c49edf0963a76d5e78b2a7278c9bd5b71d26c84f/conversion/base.py#L918)
- [Pinned Huihui patch](https://huggingface.co/huihui-ai/Huihui-DeepSeek-V4.1-Flash-1017-abliterated-GGUF/blob/73fa239749dfc09e847ea5e1149626d064224d92/patch-unsloth-Q2/patch-unsloth-DeepSeek-V4.1-Flash-1017-dec.py)
