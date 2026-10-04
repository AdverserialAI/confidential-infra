#!/usr/bin/env python3
"""Create a dedicated meter-ingress private PKI outside the CVM.

Writes a CA, ingress server certificate/key, and CVM client certificate/key to
separate 0700 directories. Run this only on an operator-controlled machine,
then upload *only* client.crt/client.key/ingress-ca.crt to the sealed
METER_CLIENT_TLS_VOLUME. The ingress host receives server.crt/server.key,
client-ca.crt and the client SPKI pin. Nothing is printed except paths and the
non-secret SPKI fingerprint.
"""
from __future__ import annotations
import argparse
import base64
import datetime as dt
import hashlib
import ipaddress
import os
from pathlib import Path
import stat
import sys

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
except ImportError as exc:
    raise SystemExit("Install cryptography: python3 -m pip install cryptography") from exc


def safe_dir(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.exists():
        raise SystemExit(f"refusing existing output path: {path}")
    path.mkdir(mode=0o700, parents=True)
    return path


def write(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def cert_builder(common_name: str, *, days: int, is_ca: bool = False) -> x509.CertificateBuilder:
    now = dt.datetime.now(dt.timezone.utc)
    return (x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=is_ca, path_length=0 if is_ca else None), critical=True))


def key_pem(key) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def certificate_pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ingress-hostname", required=True, help="DNS name on the ingress server certificate")
    parser.add_argument("--cvm-out", required=True, help="new directory for sealed CVM material")
    parser.add_argument("--ingress-out", required=True, help="new directory for ingress-host material")
    parser.add_argument("--days", type=int, default=90, help="leaf validity, 1..365")
    args = parser.parse_args()
    if not 1 <= args.days <= 365:
        raise SystemExit("--days must be 1..365")

    cvm_dir, ingress_dir = safe_dir(args.cvm_out), safe_dir(args.ingress_out)
    try:
        ca_key = ec.generate_private_key(ec.SECP256R1())
        ca_cert = cert_builder("Adverserial confidential meter CA", days=min(args.days * 2, 365), is_ca=True).issuer_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Adverserial confidential meter CA")])
        ).public_key(ca_key.public_key()).add_extension(
            x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False, data_encipherment=False,
                          key_agreement=False, key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True
        ).sign(ca_key, hashes.SHA256())

        def issue_leaf(name: str, usage, san=None):
            key = ec.generate_private_key(ec.SECP256R1())
            builder = cert_builder(name, days=args.days).issuer_name(ca_cert.subject).public_key(key.public_key()).add_extension(
                x509.ExtendedKeyUsage([usage]), critical=False
            ).add_extension(
                x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False, data_encipherment=False,
                              key_agreement=True, key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False), critical=True
            )
            if san:
                builder = builder.add_extension(x509.SubjectAlternativeName(san), critical=False)
            return key, builder.sign(ca_key, hashes.SHA256())

        try:
            san = [x509.IPAddress(ipaddress.ip_address(args.ingress_hostname))]
        except ValueError:
            san = [x509.DNSName(args.ingress_hostname)]
        server_key, server_cert = issue_leaf(args.ingress_hostname, ExtendedKeyUsageOID.SERVER_AUTH, san)
        client_key, client_cert = issue_leaf("adverserial-cvm-meter-client", ExtendedKeyUsageOID.CLIENT_AUTH)

        # CVM gets only its leaf and the ingress trust root. Ingress gets the
        # client CA public certificate, never the CVM client private key.
        write(cvm_dir / "client.crt", certificate_pem(client_cert))
        write(cvm_dir / "client.key", key_pem(client_key))
        write(cvm_dir / "ingress-ca.crt", certificate_pem(ca_cert))
        write(ingress_dir / "server.crt", certificate_pem(server_cert))
        write(ingress_dir / "server.key", key_pem(server_key))
        write(ingress_dir / "client-ca.crt", certificate_pem(ca_cert))
        fingerprint = "sha256:" + base64.urlsafe_b64encode(hashlib.sha256(
            client_cert.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        ).digest()).rstrip(b"=").decode()
        print(f"CVM sealed material: {cvm_dir}")
        print(f"Ingress material: {ingress_dir}")
        print(f"EXPECTED_CLIENT_SPKI_SHA256={fingerprint}")
        return 0
    except Exception:
        # Do not leave a partially generated credential directory behind.
        for directory in (cvm_dir, ingress_dir):
            for entry in directory.glob("*"):
                entry.unlink(missing_ok=True)
            directory.rmdir()
        raise

if __name__ == "__main__":
    raise SystemExit(main())
