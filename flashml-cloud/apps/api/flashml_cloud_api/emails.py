"""Derive the company signal from a signup address.

There is no separate "company email" field, deliberately: the signup
address is the only one that is verified and the only one anybody would
actually contact. Requiring a work address at signup was rejected because
this release targets small labs and researchers pooling Colab and RunPod
accounts, a large share of whom sign up with personal addresses.

Nothing here authorizes anything. `is_personal_email` is a segmentation
flag, not a gate.
"""
from __future__ import annotations

#: Free providers. Not exhaustive and does not need to be — an unlisted
#: provider is reported as a company domain, which is a mild
#: false-negative, whereas listing a real company would erase it from the
#: segment it belongs to.
PERSONAL_EMAIL_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "outlook.com", "hotmail.com",
        "live.com", "msn.com", "yahoo.com", "yahoo.co.uk", "aol.com",
        "proton.me", "protonmail.com", "pm.me", "icloud.com", "me.com",
        "mac.com", "gmx.com", "gmx.de", "mail.com", "zoho.com",
        "yandex.com", "yandex.ru", "qq.com", "163.com", "126.com",
        "naver.com", "hey.com", "fastmail.com", "tutanota.com",
    }
)


def derive_email_facts(email: str | None) -> tuple[str | None, bool | None]:
    """``(email_domain, is_personal_email)`` for an address, or
    ``(None, None)`` when there is nothing usable to derive from.

    Returning nulls rather than a guess is the point: a wrong domain
    pollutes every later ``GROUP BY email_domain`` invisibly, while a null
    is obviously absent.
    """
    if not isinstance(email, str):
        return (None, None)
    value = email.strip()
    if "@" not in value:
        return (None, None)
    # Last "@": a quoted local part may legally contain one.
    domain = value.rsplit("@", 1)[1].strip().lower()
    if not domain:
        return (None, None)
    return (domain, domain in PERSONAL_EMAIL_DOMAINS)
