"""GitHub App credentials on `Settings`, and the inert-when-unset rule.

The doctrine these pin is the one mail already follows (`settings.py:68-72`):
a deploy with no App configured must BOOT AND SERVE. Public-repo submission
is the entire product for everyone who has not connected GitHub, and taking
the API down over an optional integration would convert "one feature is off"
into "nothing works".

`github_app_configured` is the single predicate everything else asks. It is
deliberately all-or-nothing: a half-configured App (an id with no key) can
mint nothing, so treating it as configured would mean the console offers a
Connect button that leads to a 502.
"""
from __future__ import annotations

import base64

import pytest

from flashml_cloud_api.settings import Settings

REQUIRED_ENV = {
    "SUPABASE_URL": "https://yualksqjjvlfscbbsygq.supabase.co",
    "SUPABASE_SERVICE_KEY": "service-key-not-used-here",
    "COORDINATOR_URL": "flashml-coordinator:10000",
    "COORDINATOR_OPERATOR_TOKEN": "op-secret",
}

# A PEM shape, not a real key — nothing here signs anything.
PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAK\n-----END RSA PRIVATE KEY-----\n"


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    for key in ("GITHUB_APP_ID", "GITHUB_APP_SLUG", "GITHUB_APP_PRIVATE_KEY"):
        monkeypatch.delenv(key, raising=False)


def test_unset_app_leaves_the_api_bootable_and_unconfigured(monkeypatch):
    _base_env(monkeypatch)
    settings = Settings.from_env()
    assert settings.github_app_id == ""
    assert settings.github_app_slug == ""
    assert settings.github_app_private_key == ""
    assert settings.github_app_configured is False


def test_all_three_set_reports_configured(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("GITHUB_APP_ID", "123456")
    monkeypatch.setenv("GITHUB_APP_SLUG", "flashml")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", PEM)
    settings = Settings.from_env()
    assert settings.github_app_configured is True
    assert settings.github_app_private_key == PEM


@pytest.mark.parametrize(
    "present",
    [
        {"GITHUB_APP_ID": "123456"},
        {"GITHUB_APP_SLUG": "flashml"},
        {"GITHUB_APP_PRIVATE_KEY": PEM},
        {"GITHUB_APP_ID": "123456", "GITHUB_APP_SLUG": "flashml"},
        {"GITHUB_APP_ID": "123456", "GITHUB_APP_PRIVATE_KEY": PEM},
    ],
)
def test_a_partly_configured_app_is_not_configured(monkeypatch, present):
    """Half-configured must read as OFF, never as ON.

    Reading as ON is the damaging direction: the console renders a Connect
    button, the person completes an install on GitHub, and the callback
    fails — after they have granted us access to their code.
    """
    _base_env(monkeypatch)
    for key, value in present.items():
        monkeypatch.setenv(key, value)
    assert Settings.from_env().github_app_configured is False


def test_a_base64_private_key_is_decoded(monkeypatch):
    """The PEM is multi-line and `.env` files cannot hold newlines.

    Base64 is therefore the storage format that works in every place this
    value has to live (a `.env` file, a Render dashboard field, a shell
    export). Decoding happens here, at the single point the value enters
    the process, so no caller has to know which form it was given.
    """
    _base_env(monkeypatch)
    monkeypatch.setenv("GITHUB_APP_ID", "123456")
    monkeypatch.setenv("GITHUB_APP_SLUG", "flashml")
    monkeypatch.setenv(
        "GITHUB_APP_PRIVATE_KEY", base64.b64encode(PEM.encode()).decode()
    )
    assert Settings.from_env().github_app_private_key == PEM


def test_a_raw_pem_passes_through_unchanged(monkeypatch):
    """Render env vars DO take newlines, so a raw PEM must keep working —
    otherwise the format that is most natural to paste is the one that
    breaks."""
    _base_env(monkeypatch)
    monkeypatch.setenv("GITHUB_APP_ID", "123456")
    monkeypatch.setenv("GITHUB_APP_SLUG", "flashml")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", PEM)
    assert Settings.from_env().github_app_private_key == PEM


def test_a_non_base64_non_pem_value_is_left_alone(monkeypatch):
    """Garbage stays garbage rather than being mangled into different
    garbage: `github_app.py` raises a clear "not a valid private key" on
    it, which is a far better error than a base64 decode of nonsense."""
    _base_env(monkeypatch)
    monkeypatch.setenv("GITHUB_APP_ID", "123456")
    monkeypatch.setenv("GITHUB_APP_SLUG", "flashml")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "not-a-key")
    assert Settings.from_env().github_app_private_key == "not-a-key"
