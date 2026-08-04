"""Email domain derivation.

This value is a marketing/segmentation signal, not an authorization one —
nothing grants access on it. It still has to be right, because it is
derived once at submit time and never revisited.
"""
from __future__ import annotations

import pytest

from flashml_cloud_api.emails import derive_email_facts


@pytest.mark.parametrize(
    "email,domain,personal",
    [
        ("ha@vinai.io", "vinai.io", False),
        ("minh.tran@gmail.com", "gmail.com", True),
        # Case is not meaningful in a domain; normalise so GROUP BY works.
        ("Ha@VinAI.IO", "vinai.io", False),
        # Plus-addressing is a local-part feature — the domain is unaffected.
        ("ha+flashml@vinai.io", "vinai.io", False),
        # A subdomain is kept whole: mail.vinai.io and vinai.io are
        # different hosts and collapsing them would invent data.
        ("ops@mail.vinai.io", "mail.vinai.io", False),
        # An @ is legal in a quoted local part, so split on the LAST one.
        ('"odd@name"@vinai.io', "vinai.io", False),
        ("someone@googlemail.com", "googlemail.com", True),
        ("someone@proton.me", "proton.me", True),
    ],
)
def test_derives_domain_and_personal_flag(email, domain, personal):
    assert derive_email_facts(email) == (domain, personal)


@pytest.mark.parametrize("value", [None, "", "   ", "no-at-sign", "trailing@"])
def test_unusable_input_yields_nulls_rather_than_a_guess(value):
    """An account with no usable address stores NULL. A wrong domain is
    worse than a missing one: it silently pollutes every later GROUP BY."""
    assert derive_email_facts(value) == (None, None)
