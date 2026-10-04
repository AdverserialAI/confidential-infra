#!/usr/bin/env python3
"""Generate the two Ed25519 trust-boundary key sets for a confidential CVM.

The output contains private material. It is written only to a caller-selected
path with 0600 permissions; it is never printed, logged, or committed.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
from pathlib import Path

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
except ImportError as exc:  # pragma: no cover - environment-dependent
    raise SystemExit("install cryptography first: python3 -m pip install cryptography") from exc


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def signing_material() -> tuple[str, dict[str, str]]:
    seed = secrets.token_bytes(32)
    private = Ed25519PrivateKey.from_private_bytes(seed)
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    kid = b64url(hashlib.sha256(public).digest())
    return b64url(seed), {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": b64url(public),
        "kid": kid,
        "use": "sig",
        "alg": "EdDSA",
    }


def write_private_json(path: Path, body: dict[str, object]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(body, stream, indent=2)
        stream.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="new 0600 JSON output path")
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit(f"refusing to overwrite existing secret file: {args.out}")

    entitlement_seed, entitlement_jwk = signing_material()
    meter_seed, meter_jwk = signing_material()
    write_private_json(args.out, {
        "billing": {
            "CC_ENTITLEMENT_PRIVATE_KEY": entitlement_seed,
            "CC_ENTITLEMENT_KEY_ID": entitlement_jwk["kid"],
            "CC_METER_JWKS_JSON": {"keys": [meter_jwk]},
        },
        "cvm": {
            "ENTITLEMENT_JWKS_JSON": {"keys": [entitlement_jwk]},
            "METER_SIGNING_SEED": meter_seed,
            "SGLANG_LOOPBACK_TOKEN": secrets.token_urlsafe(32),
        },
    })
    print(f"wrote sealed-configuration input to {args.out}; keep this file out of Git and shell history")


if __name__ == "__main__":
    main()
