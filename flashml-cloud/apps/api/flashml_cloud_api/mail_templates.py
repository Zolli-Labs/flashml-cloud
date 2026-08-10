"""The bodies of the two product emails.

Separate from `mailer.py` so copy can change without touching transport,
and separate from `emails.py`, which classifies signup addresses and sends
nothing.

The declined copy deliberately echoes the console's own DeclinedScreen
("a capacity decision, not a permanent one") — a screen and an email that
contradict each other about the same decision is worse than either alone.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Email:
    subject: str
    html: str
    text: str


def _wrap(body_html: str) -> str:
    """Minimal, inline-styled, no external assets — mail clients strip
    stylesheets and block remote images by default."""
    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,'
        "'Segoe UI',Roboto,sans-serif;font-size:15px;line-height:1.6;"
        'color:#1a1a1a;max-width:34rem">'
        f"{body_html}"
        '<p style="margin-top:2rem;font-size:13px;color:#6b7280">FlashML</p>'
        "</div>"
    )


def admitted_email(console_url: str) -> Email:
    link = console_url.strip()
    button = (
        f'<p><a href="{link}" style="display:inline-block;padding:10px 16px;'
        'background:#0e6b7a;color:#ffffff;text-decoration:none;'
        f'border-radius:6px">Open the console</a></p>'
        if link
        else ""
    )
    return Email(
        subject="You're in — FlashML",
        html=_wrap(
            "<p>Your FlashML access request was approved.</p>"
            f"{button}"
            "<p>One thing before your first run: FlashML runs your training on "
            "machines you attach — a Colab notebook, a rented pod, or hardware "
            "you own. The console walks you through connecting one, then you "
            "can point it at a public GitHub repo and go.</p>"
        ),
        text=(
            "Your FlashML access request was approved.\n\n"
            + (f"Open the console: {link}\n\n" if link else "")
            + "One thing before your first run: FlashML runs your training on\n"
            "machines you attach - a Colab notebook, a rented pod, or hardware\n"
            "you own. The console walks you through connecting one, then you\n"
            "can point it at a public GitHub repo and go.\n"
        ),
    )


def declined_email() -> Email:
    return Email(
        subject="About your FlashML request",
        html=_wrap(
            "<p>We couldn't approve your FlashML request right now. That's a "
            "capacity decision, not a permanent one — this is a small alpha "
            "and we admit in batches.</p>"
            "<p>If what you're trying to run changes, reply to this message "
            "and tell us about it.</p>"
        ),
        text=(
            "We couldn't approve your FlashML request right now. That's a\n"
            "capacity decision, not a permanent one - this is a small alpha\n"
            "and we admit in batches.\n\n"
            "If what you're trying to run changes, reply to this message and\n"
            "tell us about it.\n"
        ),
    )
