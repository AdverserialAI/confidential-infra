#!/usr/bin/env python3
"""Prepare a private Compose file for 64 independent 1M-token llama-server slots.

This script only edits local files. It never calls a cloud API or changes a VM.
Run with PyYAML installed, for example: uv run --no-project --with pyyaml ...
"""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys

import yaml


SESSIONS = 64
CONTEXT_PER_SESSION = 1_048_576
OUTPUT_CAP = 65_536
MODEL_ID = "adverserial/cyberseek"


def configuration_digest(rendered, configuration):
    """Use prepare-deployment.sh's sealed-value fingerprint convention.

    Resolve the loopback value from argv as well: existing rendered deployments
    may call the environment variable UPSTREAM_API_KEY rather than
    SGLANG_LOOPBACK_TOKEN. No values are emitted by this function.
    """
    names = (
        "SGLANG_LOOPBACK_TOKEN", "CLOUDFLARE_API_TOKEN", "ENTITLEMENT_JWKS_JSON",
        "METER_SIGNING_SEED", "RECEIPT_SIGNING_SEED", "METER_INGRESS_SHARED_SECRET",
        "NV_ATTESTATION_SERVICE_KEY", "EHBP_IDENTITY_B64", "HF_TOKEN", "GANDI_PAT",
    )
    values = {}
    for service in configuration["services"].values():
        for name, value in (service.get("environment") or {}).items():
            if name in names and isinstance(value, str) and value:
                values[name] = value
    command = configuration["services"]["sglang"]["command"]
    if command.count("--api-key") != 1:
        raise ValueError("Expected one backend authentication flag")
    values["SGLANG_LOOPBACK_TOKEN"] = command[command.index("--api-key") + 1]
    fingerprint = rendered
    for name in names:
        if name in values:
            value = values[name]
            fingerprint = fingerprint.replace(
                value, f"SHA256_{name}_{hashlib.sha256(value.encode()).hexdigest()}"
            )
    fingerprint, count = re.subn(
        r"^(\s*COMPOSE_DIGEST:\s*).*?$", r"\1sha256:SELF", fingerprint, flags=re.M
    )
    if count != 1:
        raise ValueError("Expected one measured deployment digest")
    return "sha256:" + hashlib.sha256(fingerprint.encode()).hexdigest()


def prepare(source, no_peer_copy_build=False, cleanup_obsolete=False):
    before = yaml.safe_load(source)
    command = before["services"]["sglang"]["command"]
    if not isinstance(command, list) or not all(isinstance(x, str) for x in command):
        raise ValueError("Expected a literal llama-server argument list")
    if not command[0].endswith("/llama-server"):
        raise ValueError("Unexpected inference executable")

    def position(flag):
        if command.count(flag) != 1:
            raise ValueError("Required runtime flag missing or repeated")
        i = command.index(flag)
        if i + 1 >= len(command):
            raise ValueError("Required runtime value missing")
        return i + 1

    context_i = position("--ctx-size")
    parallel_i = position("--parallel")
    if int(command[parallel_i]) != SESSIONS:
        raise ValueError("The existing deployment must already have 64 slots")
    if command[position("--alias")] != MODEL_ID:
        raise ValueError("Unexpected model alias")
    if command[position("--host")] != "127.0.0.1":
        raise ValueError("The model backend must remain loopback-only")
    if any(x in command for x in ["--kv-unified", "-kvu", "--kv-unified-per-slot"]):
        raise ValueError("Review existing KV sharing overrides before preparing")
    if any(x in command for x in ["-n", "--predict", "--n-predict"]):
        raise ValueError("Review the existing prediction limit before preparing")
    env = before["services"]["sglang"].get("environment", {})
    if env:
        raise ValueError("Review runtime environment overrides before preparing")

    # Use parser marks to replace only the one scalar. Leave every unrelated
    # byte, including encrypted/sealed configuration and secret values, intact.
    tree = yaml.compose(source)

    def child(mapping, name):
        return next(value for key, value in mapping.value if key.value == name)

    node = child(child(child(tree, "services"), "sglang"), "command")
    scalar = node.value[context_i]
    total = SESSIONS * CONTEXT_PER_SESSION
    rendered = source[:scalar.start_mark.index] + '"' + str(total) + '"' + source[scalar.end_mark.index:]

    # Append explicit independent KV mode and the existing product output cap
    # to the same sequence, without serializing secret-bearing structures.
    command_end = node.end_mark.index
    offset = len(rendered) - len(source)
    insertion = command_end + offset
    line_start = rendered.rfind("\n", 0, insertion) + 1
    if rendered[line_start:insertion].strip():
        raise ValueError("Unexpected command layout; refusing a broad rewrite")
    indent = " " * node.value[0].start_mark.column
    # start_mark points after '- ', whereas a sequence item starts two columns earlier.
    indent = indent[:-2]
    flags = (
        f"{indent}- --no-kv-unified\n"
        f"{indent}- --n-predict\n"
        f'{indent}- "{OUTPUT_CAP}"\n'
    )
    rendered = rendered[:line_start] + flags + rendered[line_start:]
    builder_script = None
    runtime_path = None
    observer = None
    if cleanup_obsolete and not no_peer_copy_build:
        raise ValueError("Cleanup requires the validated existing-model build path")
    if no_peer_copy_build:
        if command[0] != "/data/cyberseek-q2-llama.cpp/bin/llama-server":
            raise ValueError("Unexpected original runtime path")
        builder_command = before["services"]["model-download"]["command"]
        if len(builder_command) != 3 or builder_command[:2] != ["bash", "-c"]:
            raise ValueError("Unexpected original builder command")
        # Compose turns $$ into a literal $ before executing the container command.
        builder_script = Path(__file__).with_name("runtime-cuda-no-peer-copy.sh").read_text()
        if cleanup_obsolete:
            cleanup_script = Path(__file__).with_name("cleanup-obsolete-models.py").read_text()
            embedded = "echo runtime-build-stage: cleanup-obsolete-models\npython3 - --root /data --apply <<'MODEL_CLEANUP_PY'\n" + cleanup_script + "\nMODEL_CLEANUP_PY"
            builder_script = builder_script.replace("# EMBED_OBSOLETE_MODEL_CLEANUP", embedded)
        builder_script = builder_script.replace("$", "$$")
        tree = yaml.compose(rendered)
        builder_node = child(child(child(tree, "services"), "model-download"), "command").value[2]
        end_line = rendered.rfind("\n", 0, builder_node.end_mark.index) + 1
        trailing_indent = rendered[end_line:builder_node.end_mark.index]
        if trailing_indent.strip():
            raise ValueError("Unexpected builder layout")
        replacement = json.dumps(builder_script) + "\n" + trailing_indent
        rendered = rendered[:builder_node.start_mark.index] + replacement + rendered[builder_node.end_mark.index:]
        tree = yaml.compose(rendered)
        executable_node = child(child(child(tree, "services"), "sglang"), "command").value[0]
        runtime_path = "/data/cyberseek-q2-llama.cpp-no-peer-copy-c49edf0963a7/bin/llama-server"
        rendered = (rendered[:executable_node.start_mark.index] + json.dumps(runtime_path)
                    + rendered[executable_node.end_mark.index:])
        if 'runtime-capacity-check' in before['services']:
            raise ValueError('Review existing runtime observer before preparing')
        observer = {
            'image': before['services']['sglang']['image'],
            'command': ['python3', '-B', '-c', Path(__file__).with_name('check-runtime-capacity.py').read_text()],
            'environment': {'BACKEND_API_KEY': command[position('--api-key')]},
            'depends_on': {'sglang': {'condition': 'service_started', 'required': True}},
            'network_mode': 'service:attest-proxy',
            'restart': 'no', 'read_only': True, 'user': '65532:65532',
            'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
            'volumes': copy.deepcopy(before['services']['sglang']['volumes']),
        }
        if any(v.get('read_only') is not True for v in observer['volumes']):
            raise ValueError('Runtime observer requires read-only model storage')
        tree = yaml.compose(rendered)
        services_end = child(tree, 'services').end_mark.index
        insertion = rendered.rfind('\n', 0, services_end) + 1
        observer_yaml = yaml.safe_dump({'runtime-capacity-check': observer}, sort_keys=False)
        observer_yaml = ''.join('  ' + line + '\n' for line in observer_yaml.splitlines())
        rendered = rendered[:insertion] + observer_yaml + rendered[insertion:]
    digest = configuration_digest(rendered, before)
    rendered = re.sub(
        r"^(\s*COMPOSE_DIGEST:\s*).*?$", lambda match: match[1] + digest,
        rendered, flags=re.M,
    )
    after = yaml.safe_load(rendered)
    expected = copy.deepcopy(before)
    expected_command = expected["services"]["sglang"]["command"]
    expected_command[context_i] = str(total)
    expected_command.extend(["--no-kv-unified", "--n-predict", str(OUTPUT_CAP)])
    if no_peer_copy_build:
        expected_command[0] = runtime_path
        expected["services"]["model-download"]["command"][2] = builder_script
        expected['services']['runtime-capacity-check'] = observer
    expected["services"]["attest-proxy"]["environment"]["COMPOSE_DIGEST"] = digest
    if after != expected:
        raise ValueError("Unexpected semantic change; refusing to write")
    report = {
        "kind": "local_configuration_preparation_only",
        "model": MODEL_ID,
        "slots": SESSIONS,
        "total_context_tokens": total,
        "context_tokens_per_slot": total // SESSIONS,
        "max_output_tokens": OUTPUT_CAP,
        "kv_unified": False,
        "deployment_configuration_digest": digest,
        "runtime_no_peer_copy_build": no_peer_copy_build,
        "runtime_revision": "c49edf0963a76d5e78b2a7278c9bd5b71d26c84f" if no_peer_copy_build else None,
        "read_only_runtime_capacity_check": no_peer_copy_build,
        "model_files_rewritten": False,
        "obsolete_model_cleanup": cleanup_obsolete,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "prepared_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "unrelated_semantic_changes": False,
        "provider_mutations": False,
    }
    return rendered, report


def private_write(path, contents):
    # A Compose file may contain credentials. Refuse replacement and never echo it.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(contents)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--no-peer-copy-build", action="store_true")
    parser.add_argument("--cleanup-obsolete", action="store_true")
    args = parser.parse_args()
    try:
        rendered, report = prepare(args.source.read_text(), args.no_peer_copy_build, args.cleanup_obsolete)
        private_write(args.output, rendered)
        private_write(args.report, json.dumps(report, indent=2) + "\n")
    except Exception as exc:
        # Parser/SDK exceptions can contain source text. Report types, never data.
        print(f"Preparation failed ({type(exc).__name__}); source content withheld", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
