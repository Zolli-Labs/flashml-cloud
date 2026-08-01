"""Credential verification: Supabase JWTs (browsers) and machine tokens
(enrolled worker machines). No FastAPI here — pure functions the app layer
wraps into 401s.

SUPABASE SIGNING KEYS, AND WHY THERE ARE TWO PATHS
--------------------------------------------------
Supabase projects used to sign every access token with one shared HS256
secret (`SUPABASE_JWT_SECRET`). Modern projects — including ours — have
migrated to asymmetric keys: the CURRENT key is an ECC P-256 keypair and
tokens arrive signed `ES256`, verifiable only against the public key
published at `{supabase_url}/auth/v1/.well-known/jwks.json`. There is no
shared secret to configure at all.

Verifying such a token with the legacy secret fails on *every* token, which
is a total sign-in outage, so this module resolves the key from the JWKS.
The legacy secret stays supported because a rotated-away HS256 key remains
listed as PREVIOUS and its tokens stay valid until they expire, and because
self-hosted or older projects may still be HS256-only.

The algorithm is chosen by *us* on both paths and passed explicitly to
`jwt.decode`. The token header only selects which path it is judged on; it
never selects the algorithm its own signature is checked with. That is the
whole defense against the `alg=none` bypass and the RS256->HS256 confusion
attack, where an attacker re-signs a token using the public key as an HMAC
secret.
"""
from __future__ import annotations

import hashlib
import secrets
import threading

import jwt
from jwt import PyJWKClient

from flashml_cloud_api.settings import Settings

MACHINE_TOKEN_PREFIX = "fmk_"

#: Path, relative to the Supabase project URL, of the published public keys.
JWKS_PATH = "/auth/v1/.well-known/jwks.json"

#: Asymmetric algorithms we will verify against a JWKS public key. HS256 is
#: deliberately absent: a symmetric algorithm must NEVER be verifiable with
#: a key that came from the public JWKS document, because that key is public
#: and anyone could forge an HMAC with it.
JWKS_ALGORITHMS = ("ES256", "ES384", "ES512", "RS256", "RS384", "RS512")

#: The one symmetric algorithm, verified only against the configured secret.
SHARED_SECRET_ALGORITHM = "HS256"

#: How long a fetched JWKS stays good before `PyJWKClient` refetches it.
#: Key rotation publishes the new key well before it starts signing, so five
#: minutes of staleness costs nothing and keeps us off Supabase's network on
#: the hot path.
JWKS_CACHE_LIFESPAN_SECONDS = 300

# One client per JWKS URL for the life of the process. Constructing it per
# request would defeat PyJWKClient's own cache and put a network round trip
# in front of every authenticated call — turning any unauthenticated caller
# into a way to hammer Supabase through us. Guarded by a lock because
# FastAPI runs sync dependencies (`current_user`) on a threadpool.
_jwks_clients: dict[str, PyJWKClient] = {}
_jwks_lock = threading.Lock()


class AuthError(Exception):
    """Raised for any credential that fails verification. Never let a
    malformed or malicious token surface as anything other than this —
    a 500 gives an attacker an oracle distinguishing "malformed" from
    "wrong signature"."""


def jwks_url(settings: Settings) -> str:
    """Where this project publishes its public signing keys."""
    return settings.supabase_url.rstrip("/") + JWKS_PATH


def _jwks_client(settings: Settings) -> PyJWKClient:
    """The process-wide JWKS client for this project, created once."""
    url = jwks_url(settings)
    with _jwks_lock:
        client = _jwks_clients.get(url)
        if client is None:
            client = PyJWKClient(
                url,
                cache_keys=True,
                cache_jwk_set=True,
                lifespan=JWKS_CACHE_LIFESPAN_SECONDS,
                timeout=10,
            )
            _jwks_clients[url] = client
        return client


def reset_jwks_cache() -> None:
    """Drop every cached JWKS client. For tests, and for the rare operator
    who has rotated keys and does not want to wait out the lifespan."""
    with _jwks_lock:
        _jwks_clients.clear()


def _jwks_signing_key(token: str, settings: Settings, header_alg: str):
    """Resolve the public key whose `kid` matches the token's, and the single
    algorithm it is allowed to verify."""
    if not settings.supabase_url:
        raise AuthError("no Supabase URL configured; cannot verify an asymmetric token")

    try:
        signing_key = _jwks_client(settings).get_signing_key_from_jwt(token)
    except Exception as exc:  # noqa: BLE001 — see below
        # Unknown `kid`, a malformed JWKS document, or Supabase simply being
        # unreachable all land here. Each is a failure to verify, so each
        # becomes an AuthError: never an uncaught exception (that would be
        # an unauthenticated remote 500, and a 500-vs-401 oracle), and above
        # all never a pass. An outage at the identity provider must fail
        # closed.
        raise AuthError(f"could not resolve a signing key: {exc}") from exc

    key_alg = getattr(signing_key, "algorithm_name", None)
    if key_alg not in JWKS_ALGORITHMS:
        # Covers a `kty: oct` entry in the JWKS, whose algorithm would be
        # HS256 — verifying with a *public* value as an HMAC secret is the
        # confusion attack itself.
        raise AuthError(f"signing key declares an unusable algorithm: {key_alg!r}")
    if key_alg != header_alg:
        raise AuthError("token algorithm does not match its signing key")

    return signing_key.key, [key_alg]


def verify_supabase_jwt(token: str | None, settings: Settings) -> str:
    """Verify a Supabase-issued JWT and return the user id (`sub` claim).

    Checks signature, expiry, and audience. `options={"require": ...}` is
    load-bearing: PyJWT validates `exp`, `sub` and `aud` only if they are
    *present*, so without it a token that simply omits `exp` would never
    expire.
    """
    if not token or not isinstance(token, str):
        raise AuthError("missing or invalid token")

    try:
        header = jwt.get_unverified_header(token)
    except Exception as exc:  # noqa: BLE001 — garbage in must be an AuthError
        raise AuthError(f"unreadable token header: {exc}") from exc

    alg = header.get("alg")
    if alg == SHARED_SECRET_ALGORITHM:
        if not settings.supabase_jwt_secret:
            # A modern, asymmetric-only project. An HS256 token here is
            # either a leftover from before rotation on a project that never
            # had a secret configured, or a forgery attempt.
            raise AuthError("HS256 token but no shared secret is configured")
        key, algorithms = settings.supabase_jwt_secret, [SHARED_SECRET_ALGORITHM]
    elif alg in JWKS_ALGORITHMS:
        key, algorithms = _jwks_signing_key(token, settings, alg)
    else:
        # `alg=none` dies here, before any key lookup or network call.
        raise AuthError(f"unsupported token algorithm: {alg!r}")

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=algorithms,
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
