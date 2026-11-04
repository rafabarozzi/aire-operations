import os
from dataclasses import dataclass
from pathlib import Path

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


@dataclass(frozen=True)
class Identity:
    tenant: str
    sub: str
    roles: frozenset[str]
    client: str
    actor_type: str


bearer = HTTPBearer(auto_error=False)


def identity(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    try:
        if not credentials:
            raise ValueError()
        claims = jwt.decode(
            credentials.credentials,
            Path(os.environ["AIRE_PUBLIC_KEY"]).read_bytes(),
            algorithms=["RS256"],
            issuer=os.environ["AIRE_ISSUER"],
            audience="aire-operations-v1",
            options={
                "require": ["iss", "aud", "sub", "exp", "iat", "tenant", "roles", "azp"]
            },
        )
        client = claims["azp"]
        if client not in os.environ["AIRE_CLIENTS"].split(","):
            raise ValueError()
        if not all(
            isinstance(x, str) and x for x in (claims["sub"], claims["tenant"], client)
        ):
            raise ValueError()
        if not isinstance(claims["roles"], list) or not all(
            isinstance(r, str) for r in claims["roles"]
        ):
            raise ValueError()
        if claims.get("actor_type") not in ("person", "service"):
            raise ValueError()
        return Identity(
            claims["tenant"],
            claims["sub"],
            frozenset(claims["roles"]),
            client,
            claims["actor_type"],
        )
    except (ValueError, jwt.InvalidTokenError):
        raise HTTPException(
            401, "Invalid access token", headers={"WWW-Authenticate": "Bearer"}
        )


def require(actor, *roles):
    if not actor.roles.intersection(roles):
        raise HTTPException(403, "Permission required")
