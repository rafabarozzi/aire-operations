"""Local-only issuer. Never mount its private key in the API or worker."""

import argparse
import os
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

DEFAULT_ROLES = [
    "operator",
    "coordinator",
    "admin",
    "simulator",
    "reader",
    "draft:operations",
    "draft:management",
    "draft:customer",
    "approve:operations",
    "approve:management",
    "approve:customer",
]


def keys(folder):
    folder.mkdir(parents=True, exist_ok=True)
    secret = folder / "issuer.pem"
    if not secret.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with os.fdopen(
            os.open(secret, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb"
        ) as f:
            f.write(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
    key = serialization.load_pem_private_key(secret.read_bytes(), password=None)
    (folder / "public.pem").write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )


def token(folder, tenant, sub, roles, client="portal", issuer="aire-local"):
    now = datetime.now(UTC)
    return jwt.encode(
        dict(
            iss=issuer,
            aud="aire-operations-v1",
            azp=client,
            sub=sub,
            tenant=tenant,
            roles=roles,
            actor_type="service" if client in ("bot", "postmortem") else "person",
            iat=now,
            exp=now + timedelta(hours=1),
        ),
        (folder / "issuer.pem").read_bytes(),
        algorithm="RS256",
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["keys", "token"])
    p.add_argument("--tenant", default="demo")
    p.add_argument("--sub", default="ana")
    p.add_argument("--roles", default=",".join(DEFAULT_ROLES))
    p.add_argument("--client", default="portal")
    p.add_argument("--folder", type=Path, default=Path(".local"))
    a = p.parse_args()
    if a.action == "keys":
        keys(a.folder)
    else:
        print(token(a.folder, a.tenant, a.sub, a.roles.split(","), a.client))
