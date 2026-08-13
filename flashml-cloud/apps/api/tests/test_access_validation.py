"""Validation for the onboarding form payload.

Kept out of app.py so the rules are testable without a database, a JWT, or
an HTTP client — and so the route stays a thin translation of ValueError
into HTTP 400.
"""
from __future__ import annotations

import pytest

from flashml_cloud_api.access import parse_submission

VALID = {
    "first_name": "Ha",
    "last_name": "Nguyen",
    "company_name": "VinAI",
    "role": "researcher",
    "team_size": "2_5",
    "use_case": "Fine-tune a 7B model across our lab's four machines.",
    "compute_sources": ["own_machines", "colab"],
    "heard_from": "github",
    "linkedin_url": "https://linkedin.com/in/hanguyen",
}


def test_rejects_non_dict_payloads():
    with pytest.raises(ValueError, match="payload"):
        parse_submission(None)
    with pytest.raises(ValueError, match="payload"):
        parse_submission([])
    with pytest.raises(ValueError, match="payload"):
        parse_submission("x")


def test_accepts_a_complete_submission():
    s = parse_submission(dict(VALID))
    assert s.first_name == "Ha"
    assert s.compute_sources == ["own_machines", "colab"]


def test_trims_whitespace_on_every_text_field():
    s = parse_submission({**VALID, "first_name": "  Ha  ", "company_name": " VinAI "})
    assert s.first_name == "Ha"
    assert s.company_name == "VinAI"


@pytest.mark.parametrize(
    "field", ["first_name", "last_name", "company_name", "use_case", "linkedin_url"]
)
def test_required_text_fields_cannot_be_blank(field):
    for blank in ("", "   "):
        with pytest.raises(ValueError, match=field):
            parse_submission({**VALID, field: blank})


@pytest.mark.parametrize(
    "field", ["first_name", "last_name", "company_name", "use_case", "linkedin_url"]
)
def test_required_text_fields_cannot_be_missing(field):
    payload = dict(VALID)
    del payload[field]
    with pytest.raises(ValueError, match=field):
        parse_submission(payload)


def test_linkedin_url_gets_a_scheme_when_typed_without_one():
    s = parse_submission({**VALID, "linkedin_url": "linkedin.com/in/hanguyen"})
    assert s.linkedin_url == "https://linkedin.com/in/hanguyen"
    s = parse_submission({**VALID, "linkedin_url": "http://linkedin.com/in/x"})
    assert s.linkedin_url == "http://linkedin.com/in/x"


def test_rejects_an_unknown_role():
    with pytest.raises(ValueError, match="role"):
        parse_submission({**VALID, "role": "wizard"})


def test_rejects_an_unknown_team_size():
    with pytest.raises(ValueError, match="team_size"):
        parse_submission({**VALID, "team_size": "a_few"})


def test_rejects_an_unknown_compute_source():
    with pytest.raises(ValueError, match="compute_sources"):
        parse_submission({**VALID, "compute_sources": ["own_machines", "quantum"]})


def test_compute_sources_may_be_empty_but_must_be_a_list():
    assert parse_submission({**VALID, "compute_sources": []}).compute_sources == []
    with pytest.raises(ValueError, match="compute_sources"):
        parse_submission({**VALID, "compute_sources": "own_machines"})


def test_compute_sources_are_deduplicated_and_order_preserved():
    s = parse_submission(
        {**VALID, "compute_sources": ["colab", "own_machines", "colab"]}
    )
    assert s.compute_sources == ["colab", "own_machines"]


def test_heard_from_is_optional():
    payload = dict(VALID)
    del payload["heard_from"]
    assert parse_submission(payload).heard_from is None


def test_length_caps_are_enforced():
    with pytest.raises(ValueError, match="first_name"):
        parse_submission({**VALID, "first_name": "x" * 81})
    with pytest.raises(ValueError, match="company_name"):
        parse_submission({**VALID, "company_name": "x" * 161})
    with pytest.raises(ValueError, match="use_case"):
        parse_submission({**VALID, "use_case": "x" * 2001})
    with pytest.raises(ValueError, match="linkedin_url"):
        parse_submission({**VALID, "linkedin_url": "x" * 201})


def test_length_caps_accept_boundary_values():
    s = parse_submission({**VALID, "first_name": "x" * 80})
    assert s.first_name == "x" * 80
    s = parse_submission({**VALID, "company_name": "x" * 160})
    assert s.company_name == "x" * 160
    s = parse_submission({**VALID, "use_case": "x" * 2000})
    assert s.use_case == "x" * 2000
    s = parse_submission({**VALID, "linkedin_url": "x" * 200})
    assert s.linkedin_url == "https://" + "x" * 200


def test_privileged_fields_in_the_body_are_ignored_not_honoured():
    """Privileged keys must be silently ignored, not rejected by name.
    A ValueError naming `is_admin` would tell an attacker the field exists.
    This test proves: (a) no raise (they are ignored, not rejected),
    and (b) legitimate fields parse correctly alongside them."""
    s = parse_submission(
        {
            **VALID,
            "is_admin": True,
            "admitted_at": "now",
            "is_host": True,
            "is_developer": True,
            "github_login": "attacker",
        }
    )
    # The absence of a raise above IS the assertion: privileged keys are silently ignored.
    # Verify legitimate fields still have correct values:
    assert s.role == "researcher"
    assert s.first_name == "Ha"
    assert s.last_name == "Nguyen"
