import time

import jwt
import pytest

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
