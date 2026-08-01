"""Credential verification: Supabase JWTs (browsers) and machine tokens
(enrolled worker machines). No FastAPI here — pure functions the app layer
wraps into 401s.
"""
from __future__ import annotations

import hashlib
import secrets

import jwt

from flashml_cloud_api.settings import Settings

MACHINE_TOKEN_PREFIX = "fmk_"


class AuthError(Exception):
    """Raised for any credential that fails verification. Never let a
    malformed or malicious token surface as anything other than this —
    a 500 gives an attacker an oracle distinguishing "malformed" from
    "wrong signature"."""


def verify_supabase_jwt(token: str | None, settings: Settings) -> str:
    """Verify a Supabase-issued JWT and return the user id (`sub` claim).

    Checks signature, expiry, and audience. The algorithm is pinned to
    HS256 explicitly — never let the token declare its own algorithm,
    which is what makes the `alg=none` bypass and RS256/HS256 confusion
    attacks work.
    """
    if not token or not isinstance(token, str):
        raise AuthError("missing or invalid token")

    try:
        claims = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
            options={"require": ["exp", "sub", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(f"invalid token: {exc}") from exc

    sub = claims.get("sub")
    if not sub:
        raise AuthError("token missing sub claim")

    return sub


def new_machine_token() -> str:
    """Mint a new, unguessable machine enrolment token. The `fmk_` prefix
    makes a leaked token greppable in logs without revealing anything
    about its value."""
    return MACHINE_TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_machine_token(token: str) -> str:
    """One-way, stable digest of a machine token for storage/comparison.
    The raw token is never stored or logged."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
