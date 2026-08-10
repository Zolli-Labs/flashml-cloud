"""Copy tests. Thin, but they pin the three things that would embarrass us:
the vocabulary decision, the console link actually appearing, and the
plain-text alternative never being empty."""
from __future__ import annotations

import pytest

from flashml_cloud_api.mail_templates import admitted_email, declined_email

BANNED = ("Zolli", "Crew", "crewmate")


@pytest.mark.parametrize(
    "email",
    [admitted_email("https://console.example"), declined_email()],
    ids=["admitted", "declined"],
)
def test_every_email_has_a_subject_and_both_bodies(email):
    assert email.subject.strip()
    assert email.html.strip()
    assert email.text.strip()


@pytest.mark.parametrize(
    "email",
    [admitted_email("https://console.example"), declined_email()],
    ids=["admitted", "declined"],
)
def test_no_retired_vocabulary(email):
    """Owner decision 2026-08-10: the interface says machine and workspace.
    An email is the one surface a user cannot re-read after we fix it."""
    blob = f"{email.subject} {email.html} {email.text}"
    for word in BANNED:
        assert word not in blob, f"{word!r} is retired vocabulary"


def test_admitted_email_links_the_console():
    email = admitted_email("https://console.example")
    assert "https://console.example" in email.html
    assert "https://console.example" in email.text


def test_admitted_email_survives_an_unset_console_url():
    """FLASHML_CONSOLE_URL is optional and warns rather than refusing, so
    this must not render a broken href or crash the approve route."""
    email = admitted_email("")
    assert email.subject.strip()
    assert 'href=""' not in email.html


def test_declined_email_invites_a_reply():
    """Re-applying is refused by design (POST /access-request 409s once
    decided), so a reply is the only door left open."""
    assert "reply" in declined_email().text.lower()


def test_the_two_emails_are_different():
    assert admitted_email("https://c.example").subject != declined_email().subject
