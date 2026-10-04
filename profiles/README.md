# Deployment profiles

A profile describes one confidential inference node. It supplies a canonical
model ID, a model-volume name and digest, a pinned runtime image, GPU topology,
model-server arguments, and the GPU-evidence type that its published policy
requires. The common proxy, entitlement, meter, receipt, and browser/SDK
protocol do not change between profiles.

The checked-in [H200 CyberGLM profile](h200-cyberglm.env.example) is a template,
not a production deployment. It is intentionally explicit about the
GLM-specific SGLang options. Do not reuse those flags for a different model.

To add a model or hardware type:

1. Create a profile with a canonical `publisher/model` identifier.
2. Pin a runtime and collector image by digest, and set the model artifact
digest and GPU evidence type.
3. Render and review compose using that profile. Run the model-specific load
and verification tests outside production.
4. Publish a new policy and release manifest that bind all those values.
5. Only after independent evidence verification, mark the policy active and
add the model/node to the public registry.

A new GPU vendor requires a collector that emits the common signed evidence
contract to `/state/gpu-evidence.json`; the proxy receives no model weights and
is unchanged. It must not be declared supported in a policy until the client
verifier validates that vendor's evidence chain.
