import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from jwt.algorithms import ECAlgorithm
from jwt.exceptions import PyJWKClientConnectionError

from flashml_cloud_api import auth as auth_module
from flashml_cloud_api.auth import (
    AuthError, hash_machine_token, new_machine_token, verify_supabase_jwt,
)
from flashml_cloud_api.settings import Settings

SECRET = "test-secret"
S = Settings(supabase_url="https://x.supabase.co", supabase_jwt_secret=SECRET,
             supabase_service_key="svc", coordinator_url="http://c",
             coordinator_operator_token="op", require_auth=True)


def _tok(**over):
    claims = {"sub": "user-1", "aud": "authenticated", "exp": time.time() + 3600}
    claims.update(over)
    return jwt.encode(claims, SECRET, algorithm="HS256")


def test_valid_token_yields_the_user_id():
    assert verify_supabase_jwt(_tok(), S) == "user-1"


def test_expired_token_is_rejected():
    with pytest.raises(AuthError):
        verify_supabase_jwt(_tok(exp=time.time() - 1), S)


def test_wrong_signature_is_rejected():
    bad = jwt.encode({"sub": "u", "aud": "authenticated", "exp": time.time() + 60},
                     "other-secret", algorithm="HS256")
    with pytest.raises(AuthError):
        verify_supabase_jwt(bad, S)


def test_wrong_audience_is_rejected():
    with pytest.raises(AuthError):
        verify_supabase_jwt(_tok(aud="anon"), S)


def test_alg_none_is_rejected():
    """The classic JWT bypass: an unsigned token claiming alg=none."""
    forged = jwt.encode({"sub": "attacker", "aud": "authenticated",
                         "exp": time.time() + 60}, None, algorithm="none")
    with pytest.raises(AuthError):
        verify_supabase_jwt(forged, S)


def test_garbage_is_rejected_without_crashing():
    for junk in ("", "not.a.jwt", "a.b.c", None):
        with pytest.raises(AuthError):
            verify_supabase_jwt(junk, S)


def test_machine_tokens_are_unguessable_and_prefixed():
    a, b = new_machine_token(), new_machine_token()
    assert a != b
    assert a.startswith("fmk_")
    assert len(a) > 30


def test_token_hash_is_stable_and_one_way():
    t = new_machine_token()
    assert hash_machine_token(t) == hash_machine_token(t)
    assert t not in hash_machine_token(t)
    assert len(hash_machine_token(t)) == 64


def test_token_without_exp_is_rejected():
    """PyJWT validates exp only if present — a token omitting it would
    otherwise never expire."""
    tok = jwt.encode({"sub": "u", "aud": "authenticated"}, SECRET, algorithm="HS256")
    with pytest.raises(AuthError):
        verify_supabase_jwt(tok, S)


def test_token_without_sub_is_rejected():
    tok = jwt.encode({"aud": "authenticated", "exp": time.time() + 60},
                     SECRET, algorithm="HS256")
    with pytest.raises(AuthError):
        verify_supabase_jwt(tok, S)


def test_token_without_aud_is_rejected():
    tok = jwt.encode({"sub": "u", "exp": time.time() + 60}, SECRET, algorithm="HS256")
    with pytest.raises(AuthError):
        verify_supabase_jwt(tok, S)


# ---------------------------------------------------------------------------
# Asymmetric (ES256) verification via JWKS
#
# Our Supabase project rotated to an ECC P-256 CURRENT key, so every
# newly-issued token is ES256 and the legacy HS256 secret verifies none of
# them. These pin the JWKS path. No test here touches the network: the only
# place `PyJWKClient` reaches out is `fetch_data`, and every test replaces it.
# ---------------------------------------------------------------------------

#: A modern project: a URL to fetch keys from, and NO shared secret at all.
ASYM = Settings(supabase_url="https://yualksqjjvlfscbbsygq.supabase.co",
                supabase_service_key="svc", coordinator_url="http://c",
                coordinator_operator_token="op", require_auth=True)


def _es256_keypair(kid: str):
    """A fresh P-256 keypair plus the public JWK Supabase would publish."""
    private = ec.generate_private_key(ec.SECP256R1())
    public_jwk = ECAlgorithm.to_jwk(private.public_key(), as_dict=True)
    public_jwk.update({"kid": kid, "alg": "ES256", "use": "sig"})
    return private, public_jwk


def _es256_token(private, kid: str, **over):
    claims = {"sub": "user-es", "aud": "authenticated", "exp": time.time() + 3600}
    claims.update(over)
    return jwt.encode(claims, private, algorithm="ES256", headers={"kid": kid})


class StubJWKS:
    """Stands in for the Supabase JWKS endpoint. The real `PyJWKClient` still
    does the `kid` matching and JWK->key conversion — only the fetch is
    replaced, so the code under test is exercised for real."""

    def __init__(self, *public_jwks, error: Exception | None = None):
        self.keys = list(public_jwks)
        self.error = error
        self.fetches = 0

    def install(self, monkeypatch):
        stub = self

        def fetch_data(_self):
            stub.fetches += 1
            if stub.error is not None:
                raise stub.error
            return {"keys": stub.keys}

        monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fetch_data)
        return self


@pytest.fixture(autouse=True)
def _clean_jwks_cache():
    """The JWKS client is process-wide by design; tests must not inherit one
    built against another test's stub."""
    auth_module.reset_jwks_cache()
    yield
    auth_module.reset_jwks_cache()


def test_jwks_url_is_derived_from_the_supabase_url():
    assert auth_module.jwks_url(ASYM) == (
        "https://yualksqjjvlfscbbsygq.supabase.co/auth/v1/.well-known/jwks.json"
    )


def test_es256_token_signed_by_the_published_key_verifies(monkeypatch):
    """The bug this whole change exists for: with HS256-only verification
    this token — the only kind the live project now issues — was rejected,
    and nobody could sign in."""
    private, public_jwk = _es256_keypair("kid-current")
    StubJWKS(public_jwk).install(monkeypatch)

    assert verify_supabase_jwt(_es256_token(private, "kid-current"), ASYM) == "user-es"


def test_es256_token_signed_by_a_different_key_is_rejected(monkeypatch):
    """The forgery that matters: right `kid`, wrong private key."""
    _, published_jwk = _es256_keypair("kid-current")
    attacker_key, _ = _es256_keypair("kid-current")
    StubJWKS(published_jwk).install(monkeypatch)

    with pytest.raises(AuthError):
        verify_supabase_jwt(_es256_token(attacker_key, "kid-current"), ASYM)


def test_unknown_kid_is_rejected(monkeypatch):
    private, _ = _es256_keypair("kid-not-published")
    _, published_jwk = _es256_keypair("kid-current")
    StubJWKS(published_jwk).install(monkeypatch)

    with pytest.raises(AuthError):
        verify_supabase_jwt(_es256_token(private, "kid-not-published"), ASYM)


def test_token_with_no_kid_at_all_is_rejected(monkeypatch):
    private, published_jwk = _es256_keypair("kid-current")
    StubJWKS(published_jwk).install(monkeypatch)
    token = jwt.encode({"sub": "u", "aud": "authenticated", "exp": time.time() + 60},
                       private, algorithm="ES256")

    with pytest.raises(AuthError):
        verify_supabase_jwt(token, ASYM)


def test_unreachable_jwks_fails_closed_as_an_auth_error(monkeypatch):
    """A Supabase outage must produce a 401, not an uncaught exception (an
    unauthenticated remote 500, and a 500-vs-401 oracle) and above all not
    an accepted token."""
    private, _ = _es256_keypair("kid-current")
    StubJWKS(error=PyJWKClientConnectionError("boom")).install(monkeypatch)

    with pytest.raises(AuthError):
        verify_supabase_jwt(_es256_token(private, "kid-current"), ASYM)


def test_a_broken_jwks_document_fails_closed_too(monkeypatch):
    private, _ = _es256_keypair("kid-current")
    StubJWKS(error=ValueError("not json")).install(monkeypatch)

    with pytest.raises(AuthError):
        verify_supabase_jwt(_es256_token(private, "kid-current"), ASYM)


def test_expired_es256_token_is_rejected(monkeypatch):
    private, public_jwk = _es256_keypair("kid-current")
    StubJWKS(public_jwk).install(monkeypatch)

    with pytest.raises(AuthError):
        verify_supabase_jwt(
            _es256_token(private, "kid-current", exp=time.time() - 1), ASYM)


def test_es256_token_with_the_wrong_audience_is_rejected(monkeypatch):
    private, public_jwk = _es256_keypair("kid-current")
    StubJWKS(public_jwk).install(monkeypatch)

    with pytest.raises(AuthError):
        verify_supabase_jwt(_es256_token(private, "kid-current", aud="anon"), ASYM)


@pytest.mark.parametrize("missing", ["exp", "sub", "aud"])
def test_es256_token_missing_a_required_claim_is_rejected(monkeypatch, missing):
    """`options={"require": [...]}` has to survive the JWKS change: PyJWT
    validates these claims only if they are present."""
    private, public_jwk = _es256_keypair("kid-current")
    StubJWKS(public_jwk).install(monkeypatch)
    claims = {"sub": "u", "aud": "authenticated", "exp": time.time() + 60}
    del claims[missing]
    token = jwt.encode(claims, private, algorithm="ES256", headers={"kid": "kid-current"})

    with pytest.raises(AuthError):
        verify_supabase_jwt(token, ASYM)


def test_the_jwks_is_fetched_once_and_then_cached(monkeypatch):
    """A fetch per request would add a network round trip to every
    authenticated call and let any anonymous caller hammer Supabase through
    us."""
    private, public_jwk = _es256_keypair("kid-current")
    stub = StubJWKS(public_jwk).install(monkeypatch)

    for _ in range(5):
        assert verify_supabase_jwt(_es256_token(private, "kid-current"), ASYM) == "user-es"

    assert stub.fetches == 1


def test_the_jwks_client_itself_is_built_once_per_project(monkeypatch):
    _, public_jwk = _es256_keypair("kid-current")
    StubJWKS(public_jwk).install(monkeypatch)

    assert auth_module._jwks_client(ASYM) is auth_module._jwks_client(ASYM)


def test_alg_none_is_rejected_without_any_jwks_lookup(monkeypatch):
    """The classic bypass must die before we go anywhere near a key —
    otherwise an unsigned token is a free network call out of the process."""
    stub = StubJWKS().install(monkeypatch)
    forged = jwt.encode({"sub": "attacker", "aud": "authenticated",
                         "exp": time.time() + 60}, None, algorithm="none")

    with pytest.raises(AuthError):
        verify_supabase_jwt(forged, ASYM)
    assert stub.fetches == 0


def test_hs256_token_is_refused_when_no_secret_is_configured(monkeypatch):
    """On an asymmetric-only project there is no secret, so an HS256 token
    must be rejected outright — never quietly verified against a public key
    pulled from the JWKS, which is the RS256->HS256 confusion attack."""
    stub = StubJWKS().install(monkeypatch)
    forged = jwt.encode({"sub": "attacker", "aud": "authenticated",
                         "exp": time.time() + 60}, "anything", algorithm="HS256")

    with pytest.raises(AuthError):
        verify_supabase_jwt(forged, ASYM)
    assert stub.fetches == 0


def test_hs256_still_works_when_a_secret_is_configured(monkeypatch):
    """The legacy key stays listed as PREVIOUS after rotation and its tokens
    remain valid until they expire; self-hosted projects may be HS256-only.
    A project with both configured must accept either."""
    private, public_jwk = _es256_keypair("kid-current")
    StubJWKS(public_jwk).install(monkeypatch)
    both = Settings(supabase_url=ASYM.supabase_url, supabase_jwt_secret=SECRET,
                    supabase_service_key="svc", coordinator_url="http://c",
                    coordinator_operator_token="op", require_auth=True)

    assert verify_supabase_jwt(_tok(), both) == "user-1"
    assert verify_supabase_jwt(_es256_token(private, "kid-current"), both) == "user-es"


def test_hs256_token_forged_with_the_public_key_is_rejected(monkeypatch):
    """Explicit confusion-attack test: take the published EC public key,
    use its raw bytes as an HMAC secret, and claim alg=HS256."""
    _, public_jwk = _es256_keypair("kid-current")
    StubJWKS(public_jwk).install(monkeypatch)
    public_material = public_jwk["x"] + public_jwk["y"]
    forged = jwt.encode({"sub": "attacker", "aud": "authenticated",
                         "exp": time.time() + 60},
                        public_material, algorithm="HS256",
                        headers={"kid": "kid-current"})

    with pytest.raises(AuthError):
        verify_supabase_jwt(forged, ASYM)
