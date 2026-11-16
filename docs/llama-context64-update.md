# CyberSeek: 64 independent 1M-context slots

The November 15, 2026 deployment review found `--ctx-size 1048576` together with
`--parallel 64`. In the configured llama.cpp build
`c49edf0963a76d5e78b2a7278c9bd5b71d26c84f`, non-unified KV mode divides total
context by the sequence count. Those flags provide 16,384 tokens per slot.

The requested configuration is:

```text
--ctx-size 67108864
--parallel 64
--no-kv-unified
--n-predict 65536
```

The total context is 64 × 1,048,576. Each session's prompt, conversation history
and generated output must fit within its 1M window; the output limit is 65,536.
This preserves independent engine sequences. It does not replace application
authentication, authorization, entitlement checks or tenant isolation.

`scripts/prepare-llama-context-update.py` prepares this narrow update from a
private snapshot of the active Docker Compose file. It preserves all unrelated
bytes and verifies the resulting parsed configuration differs only in the
context, explicit KV/output flags and corresponding `COMPOSE_DIGEST`. The
digest uses the sealed-value fingerprint convention in `prepare-deployment.sh`,
also resolving the loopback credential from argv when its environment variable
uses a different name. It refuses unexpected aliases, remote
backend bindings, conflicting flags, a different slot count or environment
overrides. It writes new Compose files with mode 0600 and never prints them.

```sh
uv run --no-project --with pyyaml python scripts/test-llama-context-update.py
uv run --no-project --with pyyaml python scripts/prepare-llama-context-update.py \
  /private/path/live.yml /private/path/context64.yml \
  --report /private/path/context64-report.json
docker compose -f /private/path/context64.yml config --quiet
```

## Observed CUDA startup failure and repair

After public logs became accessible, engine output showed:

```text
CUDA error: operation not supported
ggml_backend_cuda_cpy_tensor_async ... ggml-cuda.cu:2740
cudaMemcpyPeerAsync(...)
```

The native backtrace enters the CUDA copy from the scheduler during initial
warm-up. This establishes a direct peer-copy failure in the available engine
logs; it does not identify every earlier kernel-only general-protection fault.

In the pinned source, `GGML_CUDA_NO_PEER_COPY` is a compile-time option, not a
runtime environment toggle. Configure with `-DGGML_CUDA_NO_PEER_COPY=ON` to make
that path decline direct peer copies and use the existing backend host-copy
fallback. Confidential-computing and attestation controls remain enabled.
Host-staged inter-GPU transfers may reduce throughput and require benchmarking.

The user identified [upstream issue #4638](https://github.com/ggml-org/llama.cpp/issues/4638),
which is a useful historical comparison, but not an established identical bug.
That December 2023 report failed with `cudaMemcpyAsync(...cudaMemcpyDeviceToDevice...)`
returning `invalid argument` in the matrix-multiplication path. Its merged fix,
[PR #4620](https://github.com/ggml-org/llama.cpp/pull/4620), explains that copies
between different devices' VMM pools sometimes require an explicit
`cudaMemcpyPeerAsync`. The current observed crash already uses that explicit
peer-copy API and receives `operation not supported` in the backend scheduler's
copy path. Consequently, applying the old fix is not evidence of a repair here.
The compile-time no-peer-copy option in the revision being built is a targeted
workaround for the currently failing call; successful warm-up and inference are
still required before calling the issue resolved. Why the current topology or
allocation rejects the operation remains unconfirmed.

`runtime-cuda-no-peer-copy.sh` validates all seven existing GGUF shard headers,
the projector and model provenance, then builds the same pinned revision into a
separate runtime directory. It does not download, repatch, delete or replace the
model weights or delete the previous runtime. Cached new builds require matching
source/option metadata and an executable SHA-256. Missing or mismatched inputs
fail closed. Model measurement remains in the startup dependency chain.

Pass `--no-peer-copy-build` to the preparer to embed that reviewed build script
and select the new executable. Run these additional checks:

```sh
bash -n scripts/runtime-cuda-no-peer-copy.sh
python3 scripts/test-runtime-cuda-no-peer-copy.py
node --check scripts/guarded-phala-context-update.mjs
```

`guarded-phala-context-update.mjs` uses the installed official Phala SDK. Its
default mode performs read-only preflight checks. It requires the same existing
CVM identity, a completed provider operation, unchanged baseline app/Compose
configuration and exact prepared-file hashes. Only an explicitly authorized
invocation with `--apply` submits one ordinary `patchCvm` request with
`allow_force_stop: false`. It never calls a stop, delete, shutdown, restart or
rental-removal operation. An ambiguous update result must be inspected before
any retry. Credential-bearing inputs stay local; evidence is allowlisted.

## Approved obsolete-artifact cleanup

The user also authorized removal of previous CyberGLM artifacts and failed
CyberSeek releases. The new boot's filesystem report shows 425.9 GB used and
approximately 1.5 TB free on the 1.9 TB data filesystem (22% used); the dashboard's
1.8/1 TB indicator is inconsistent with that guest report.

`cleanup-obsolete-models.py` defaults to an inventory-only mode and removes
reviewed exact historical model/staging directory names only when `--apply` is
selected. It preserves the active `cyberseek-q2` tree, the current runtime/source,
all replacement runtime paths, unrelated data, symlinks and nested mounts.
Unrecognized model directories are counted and retained for later review. It
never prunes Docker volumes or uses a VM/rental operation.

Pass `--cleanup-obsolete` together with `--no-peer-copy-build` to embed this
cleanup after active-model provenance validation and before the runtime build.
The builder emits reviewed artifact names/sizes and a before/after free-space
summary, without printing customer data or credential values. Any later removal
of the previous active runtime should wait until the replacement is verified.

```sh
python3 scripts/test-cleanup-obsolete-models.py
```

The script performs no cloud operation. Do not commit a rendered deployment
file: the existing configuration contains credentials. Reports contain only
allowlisted non-secret fields and file hashes.

## Update and verification requirements

- Wait for any existing Phala update to finish. Do not stack another update on it.
- Obtain explicit approval before updating the existing GPU CVM. Approval was
  provided in this chat for this context update; it is not a standing approval
  for arbitrary future changes.
- Use the existing CVM's normal update operation. Never issue stop, delete,
  rental removal, force-stop or a recovery restart on the on-demand GPU rental.
- Preserve the model, patches, image digests, persistent volumes, prelaunch
  script, encrypted environment, attestation checks and billing controls.
- Inspect native engine startup errors before assuming the context correction
  repairs the separately observed libc general-protection fault.
- Verify runtime output reports 64 slots and a per-slot context of 1,048,576,
  then check per-GPU allocation and successful inference.
- Validate independent concurrent sessions, output limits and signed attested
  inference. A 64-slot configuration alone is not a successful 64-user load test.
- An update changes the measured deployment identity. Clients and policy must
  continue to reject unapproved measurements; do not bypass verification to make
  a changed deployment appear healthy. Review and publish exact new pins only
  after verifying the updated workload.

Static analysis of the Q2 checkpoint and the configured compressed-cache
implementation estimates approximately 366–435 GB of cache for 64 full 1M
sessions. The checkpoint files total approximately 335 GB; eight H200 GPUs
advertise 1,128 GB of aggregate GPU memory. These numbers exclude runtime
buffers, the projector and allocation overhead, and aggregate fit does not
prove per-device fit, acceptable response speed or successful long-context
recall. The actual cached binary and patched weights still require verification.

Sources:

- [Pinned runtime context allocation](https://github.com/ggml-org/llama.cpp/blob/c49edf0963a76d5e78b2a7278c9bd5b71d26c84f/src/llama-context.cpp)
- [Pinned compressed-cache implementation](https://github.com/ggml-org/llama.cpp/blob/c49edf0963a76d5e78b2a7278c9bd5b71d26c84f/src/llama-kv-cache-dsv4.cpp)
- [H200 specifications](https://www.nvidia.com/en-us/data-center/h200/)
- [Phala public-log behavior](https://docs.phala.com/phala-cloud/monitoring/public-logs)

## November 15 update submission

The public-log activation operation finished before the repair was submitted.
Fresh engine logs at 22:32:16 UTC reproduced `CUDA error: operation not supported`
at `cudaMemcpyPeerAsync`; a new model-loading sequence followed at 22:32:24 UTC.
This confirmed that the current engine was crashing and automatically restarting,
rather than merely displaying an old startup error.

Nine local tests passed (five preparation checks, two runtime-builder checks and
two cleanup checks); Docker Compose validation also passed. The live-state guard
confirmed the existing on-demand rental identity, completed provider operation,
unchanged original app configuration and exact candidate hash.

Phala accepted one ordinary update at 22:33:22 UTC with `allow_force_stop=false`:

- Docker Compose SHA-256: `9f5492a6764c7c7cc98431798adc4306f7adadf5f95797f43023aeaebec362c7`
- Boot Compose hash: `5970b63e2c9cf4f76394b4f710b15ad70ccde2780e7f685e9cc37cb27b6c7888`
- Declared deployment digest: `sha256:72b1e33f874c21644d8817680b85728f9744d4bfaef7c8e91543e04769b1943c`
- Provider correlation: `29551f1eb0022b08b098b826e73fa242`

At the first post-submission check the provider reported `updating`, targeting
`running`. No stop, delete, rental removal, force-stop or recovery restart was
issued. Acceptance is not verification: compilation, cleanup, model startup,
64-slot/per-slot-context metadata, fresh attestation and inference remain open
until independently observed.

### Post-update observation at 23:41 UTC

The provider operation finished with the submitted hashes. The replacement
runtime builder exited successfully at 23:10:13 UTC, and fresh model measurement
completed at 23:36:05 UTC with the same 9-file model digest. The model server
started at approximately 23:36:08 UTC. The bounded log checks at 23:41 UTC show
the same process running for five minutes, with no observed new CUDA peer-copy
failure or fatal allocation message; they do not establish successful warm-up.
Public proxy liveness is HTTP 200, but public `/v1/models` remains HTTP 503.
A fresh installed ACK attestation command returned `status=failed`; no inference
prompt was sent and no confidential-verification success is claimed.

At 23:36:56 UTC the engine logged `common_fit_params: failed to fit params to
free device memory: n_gpu_layers already set by user to 999, abort`. In the
[pinned fitting source](https://github.com/ggml-org/llama.cpp/blob/c49edf0963a76d5e78b2a7278c9bd5b71d26c84f/common/fit.cpp),
the explicit layer count triggers a fitting exception, caught by
`common_fit_params` and logged as a warning returning fitting failure. The word
`abort` in that message refers to the fitting attempt; it is not itself a native
process-abort trace. The explicit GPU-offload count prevents the fitter from
redistributing layers, and successful allocation must be established from later
logs/metadata. Do not hide the warning with `--fit off` and call memory fit
verified, or lower the user's requested context/session count without agreement.

### Confirmed prefill workspace exhaustion and microbatch correction

At 23:51:51 UTC a fresh fatal error established the actual allocation failure:

```text
ggml_backend_cuda_buffer_type_alloc_buffer: allocating 131751.76 MiB on device 0: cudaMalloc failed: out of memory
ggml_gallocr_reserve_n_impl: failed to allocate CUDA0 buffer of size 138151736576
graph_reserve: failed to allocate compute buffers
llama_init_from_model: failed to initialize the context: failed to allocate compute pp buffers
```

The server then exited and its container automatically restarted. The failure
recurred at 23:53:11 UTC. Thus the earlier fitting warning was followed by a
confirmed compute-workspace OOM, rather than merely slow loading. This is not a
claim that the model weights alone exceed GPU memory. Nor does reaching this
earlier failure stage validate successful warm-up after the peer-copy repair.

The failed buffer is 128.664 GiB on one GPU. For comparison, the illustrative
F32 context-by-microbatch term is `67108864 * 512 * 4` bytes = 128 GiB. At a
physical microbatch of 32 that term would be 8 GiB. This numerical correspondence
is useful for a targeted correction; it is not a complete allocator trace or
an independently measured 16-fold reduction of the entire graph's workspace.

`prepare-llama-microbatch-update.py` prepares a narrowly guarded follow-up from
the live no-peer-copy configuration. It changes `--ubatch-size 512` to `32` and
extends the read-only metadata observer's deadline from 900 to 2700 seconds.
It updates the corresponding deployment digest and asserts that every other
parsed setting is unchanged: 64 independent slots, total context 67,108,864,
1M per slot, output cap 65,536, logical batch 4096, GPU layer selection, patched
weights, pinned runtime, network bindings, secrets, storage and security controls.
Physical microbatch size is a compute scheduling limit, not a session-count cap.
Prefill and aggregate throughput must be benchmarked after readiness.

Three follow-up configuration tests and Docker Compose validation passed.
The existing-rental live-state preflight also passed. No GPU capacity or
successful inference claim is made from these local tests.

Primary references: [pinned argument definitions](https://github.com/ggml-org/llama.cpp/blob/c49edf0963a76d5e78b2a7278c9bd5b71d26c84f/common/arg.cpp)
distinguish logical batch from physical microbatch; [pinned V4.1 graph](https://github.com/ggml-org/llama.cpp/blob/c49edf0963a76d5e78b2a7278c9bd5b71d26c84f/src/models/deepseek41.cpp)
includes different sparse/gather paths for small query batches. A smaller
microbatch is a testable mitigation, not a guarantee of the complete 64 x 1M
workload or a substitute for per-device memory and throughput measurements.

## Preferred working configuration: November 16, 2026

The microbatch-32 follow-up is the preferred **verified startup and bounded
inference configuration**. Retain it without another CVM update. Any future
change to the existing GPU CVM requires explicit owner approval; never stop or
remove the on-demand rental.

The runtime capacity observer passed at 01:12:48 UTC. Independent read-only
checks then confirmed HTTP 200 from `/v1/models`, canonical
`adverserial/cyberseek`, 64 independent slots and 1,048,576 context tokens per
slot. The observer verified the runtime binary against its build manifest:

- Runtime revision: `c49edf0963a76d5e78b2a7278c9bd5b71d26c84f`
- Binary SHA-256: `c2e5b3eb2902163a4fb8141a2ff5a11b8192c014ab8a3ba9e2ab9c9f25b0d40f`
- Compile-time `GGML_CUDA_NO_PEER_COPY`: enabled
- Physical microbatch: 32; logical batch: 4096
- Total context: 67,108,864; parallel sequences: 64; unified KV: disabled
- Configured output cap: 65,536
- Model artifact digest: `sha256:8dd425171ed78e68263a45f8d01317d991b92bc7128321464f1ab82d9a05eae8`
- Docker Compose SHA-256: `1b86d2fe1ba12f4289d297784dc399402d7a62288c92633574bf7b87307d6fab`
- Boot Compose hash: `7fb6f1888f90dfaa453c80797aa952b1485282be0a100ec4e4071a1ed134b21d`
- Deployment configuration digest: `sha256:53d2ad260cf8adb2a3f2b0e82f78e8ada5e6098cc52f0649d6f3e2402e59cbc1`

Fresh installed ACK verification at 01:18:31 UTC accepted the exact published
pins, signed fresh evidence, Intel DCAP TDX with RTMR3 workload-policy replay,
NVIDIA NRAS and TLS/EHBP bindings, with `devMode=false`. The initial stale-policy
rejection cleared after the public policy registry was synchronized. No trust
check was disabled, and this monitoring run did not modify or deploy the policy.

At approximately 01:21:42 UTC, two concurrently dispatched synthetic requests
through the installed ACK gateway both returned their distinct exact expected
answers with verified signed inference receipts. Each completed in approximately
4 seconds after dispatch; this tiny test is not a representative throughput
measurement. Neither response contained the other request's marker. API keys
remained local to the gateway/billing entitlement exchange, and raw responses
were not persisted in the supervision evidence.

These results establish successful startup, the requested configured capacity,
fresh attestation and bounded concurrent encrypted inference after the two
observed startup failures. They **do not** establish 64 simultaneous fully
occupied 1M-token contexts, long-context recall, sustained throughput, maximum
output generation, cross-account authorization isolation, or broad model
quality. Those remain separate validation gates. A two-request marker test is
not a tenant-isolation audit. No CVM mutation occurred during these checks.
