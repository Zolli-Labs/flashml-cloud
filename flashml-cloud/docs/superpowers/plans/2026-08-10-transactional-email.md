# Transactional Email Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an admin approves or declines an access request, the person is emailed — instead of the silence the product ships with today.

**Architecture:** The API sends directly from its existing approve/decline handlers via Resend's HTTP API over `httpx`, reusing the injectable-dependency pattern `create_cloud_app` already uses for `connect` / `fetch_repo` / `start_federated_job`. No Supabase Edge Function (the repo has no Supabase deploy surface), no database trigger, and **no migration** — exactly-once delivery falls out of the existing `and status = 'pending'` guard, which makes a second approve a 404 before the mailer is reached.

**Tech Stack:** Python 3.11+, FastAPI, httpx, psycopg, pytest; Next.js/TypeScript + sonner for the console.

**Spec:** `docs/superpowers/specs/2026-08-10-transactional-email-design.md`

## Global Constraints

- **The status literal is `'admitted'`, never `'approved'`.** The route is *named* approve and returns `{"status": "admitted"}`. Table check constraint: `('pending', 'admitted', 'declined')`.
- **Every new `Settings` field must have a default.** Seven test files construct `Settings(...)` explicitly; a field without a default breaks all of them.
- **The mailer is inert unless BOTH `RESEND_API_KEY` and `EMAIL_FROM` are set.** Unconfigured ⇒ `send()` returns `False`, makes no HTTP call, raises nothing.
- **An email failure must never fail an admission.** The DB write commits first; send errors are caught and swallowed.
- **Never log the recipient address, the API key, or the response body.** Log `user_id` and status code only — matching `app.py:384-388`, which swallows coordinator exception strings because they can carry credentials.
- **Mailer timeout is `10.0` seconds**, not the coordinator's 60 — this sits inside an admin's click.
- **Vocabulary: `machine` and `workspace`.** Never "Zolli" or "Crew" in any string this plan adds (owner decision §6.3 as amended).
- **No migration.** `0012` stays free for the developer-surface spec's `0012_cli_credentials.sql`.
- Run API commands from `flashml-cloud/apps/api` with its venv: `.venv/bin/pytest`.
- **The suite is RED at this branch point, and that is expected.** Baseline:
  `905 passed, 19 failed, 1 skipped`. All 19 failures are pre-existing and
  unrelated — 6 in `tests/test_contributions.py`, 13 in
  `tests/test_federated.py`, every one of them
  `TypeError: run_fedavg() got an unexpected keyword argument 'rounds'` from a
  half-landed federated v2 schema change. **No task in this plan touches
  `fedavg.py` or those two test files.** The success criterion is therefore
  **"no NEW failures"**, never "suite green": after your change the failure
  set must still be exactly those 19. If a 20th appears, it is yours. The
  exact list is at
  `.superpowers/sdd/2026-08-10-transactional-email/baseline-failures.txt`.

## Task Dependency Graph

```
Task 1 (mailer) ─┐
Task 2 (templates) ─┼─→ Task 3 (wiring) ─→ Task 4 (console)
Task 5 (config/docs) ─┘ (independent)
```

Tasks 1, 2 and 5 touch disjoint files and may run in parallel. Task 3 needs 1 and 2. Task 4 needs 3.

---

### Task 1: Settings fields and the Mailer

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/settings.py` (dataclass fields after `console_url`; reads + warning in `from_env`)
- Create: `flashml-cloud/apps/api/flashml_cloud_api/mailer.py`
- Test: `flashml-cloud/apps/api/tests/test_mailer.py`

**Interfaces:**
- Consumes: `Settings` from `.settings`.
- Produces: `Mailer(settings, transport=None, timeout=10.0)` with `.configured -> bool` and `async .send(*, to: str, subject: str, html: str, text: str, user_id: str = "") -> bool`. `Settings.resend_api_key`, `Settings.email_from`, `Settings.email_reply_to` (all `str`, default `""`).

- [ ] **Step 1: Write the failing test**

Create `flashml-cloud/apps/api/tests/test_mailer.py`:

```python
"""The mailer: inert without configuration, and silent about secrets.

The two assertions that matter are that an unconfigured deploy sends
nothing and raises nothing (every other test file in this suite runs that
way), and that a provider failure is reported as False rather than raised —
an admission must never fail because mail did.
"""
from __future__ import annotations

import json

import httpx
import pytest

from flashml_cloud_api.mailer import RESEND_ENDPOINT, Mailer
from flashml_cloud_api.settings import Settings


def _settings(**over) -> Settings:
    base = dict(
        supabase_url="https://example.supabase.co",
        supabase_service_key="unused",
        coordinator_url="http://coordinator",
        coordinator_operator_token="op",
        require_auth=True,
    )
    base.update(over)
    return Settings(**base)


class FakeResend(httpx.AsyncBaseTransport):
    def __init__(self, status: int = 200, boom: bool = False):
        self.requests: list[httpx.Request] = []
        self._status = status
        self._boom = boom

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        if self._boom:
            raise httpx.ConnectError("no route to host")
        return httpx.Response(self._status, json={"id": "msg_123"})


def _configured(transport: FakeResend) -> Mailer:
    return Mailer(
        _settings(resend_api_key="re_test_key", email_from="FlashML <no-reply@mail.example>"),
        transport=transport,
    )


@pytest.mark.asyncio
async def test_unconfigured_mailer_sends_nothing_and_raises_nothing():
    transport = FakeResend()
    mailer = Mailer(_settings(), transport=transport)
    assert mailer.configured is False
    assert await mailer.send(to="a@b.c", subject="s", html="<p>h</p>", text="t") is False
    assert transport.requests == []


@pytest.mark.asyncio
async def test_a_key_without_a_from_address_is_still_unconfigured():
    transport = FakeResend()
    mailer = Mailer(_settings(resend_api_key="re_test_key"), transport=transport)
    assert mailer.configured is False
    assert await mailer.send(to="a@b.c", subject="s", html="<p>h</p>", text="t") is False
    assert transport.requests == []


@pytest.mark.asyncio
async def test_configured_mailer_posts_to_resend_and_returns_true():
    transport = FakeResend()
    ok = await _configured(transport).send(
        to="her@example.com", subject="You're in", html="<p>hi</p>", text="hi"
    )
    assert ok is True
    assert len(transport.requests) == 1
    req = transport.requests[0]
    assert str(req.url) == RESEND_ENDPOINT
    assert req.headers["authorization"] == "Bearer re_test_key"
    body = json.loads(req.content)
    assert body["to"] == ["her@example.com"]
    assert body["subject"] == "You're in"
    assert body["html"] == "<p>hi</p>"
    assert body["text"] == "hi"
    assert body["from"] == "FlashML <no-reply@mail.example>"


@pytest.mark.asyncio
async def test_reply_to_defaults_to_the_from_address():
    transport = FakeResend()
    await _configured(transport).send(to="a@b.c", subject="s", html="<p>h</p>", text="t")
    body = json.loads(transport.requests[0].content)
    assert body["reply_to"] == "FlashML <no-reply@mail.example>"


@pytest.mark.asyncio
async def test_explicit_reply_to_wins():
    transport = FakeResend()
    mailer = Mailer(
        _settings(
            resend_api_key="re_test_key",
            email_from="FlashML <no-reply@mail.example>",
            email_reply_to="humans@example.com",
        ),
        transport=transport,
    )
    await mailer.send(to="a@b.c", subject="s", html="<p>h</p>", text="t")
    body = json.loads(transport.requests[0].content)
    assert body["reply_to"] == "humans@example.com"


@pytest.mark.asyncio
async def test_provider_error_status_returns_false_and_does_not_raise():
    transport = FakeResend(status=500)
    assert await _configured(transport).send(
        to="a@b.c", subject="s", html="<p>h</p>", text="t"
    ) is False


@pytest.mark.asyncio
async def test_transport_failure_returns_false_and_does_not_raise():
    transport = FakeResend(boom=True)
    assert await _configured(transport).send(
        to="a@b.c", subject="s", html="<p>h</p>", text="t"
    ) is False


@pytest.mark.asyncio
async def test_failure_logs_carry_no_address_and_no_key(caplog):
    transport = FakeResend(status=500)
    with caplog.at_level("ERROR"):
        await _configured(transport).send(
            to="secret-person@example.com", subject="s", html="<p>h</p>", text="t",
            user_id="u-42",
        )
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "secret-person@example.com" not in logged
    assert "re_test_key" not in logged
    # The failure must still be diagnosable: user_id and status, nothing else.
    assert "u-42" in logged
    assert "500" in logged
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_mailer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flashml_cloud_api.mailer'`

- [ ] **Step 3: Add the three Settings fields**

In `flashml_cloud_api/settings.py`, immediately after the `console_url: str = ""` field:

```python
    #: Resend API key. Optional: an unconfigured deploy must still boot and
    #: serve — the failure mode of a missing mail provider is a silent
    #: product, not a dead API, so this is deliberately NOT in the
    #: require_auth missing-secret check. Same reasoning as `console_url`,
    #: which warns rather than refusing.
    resend_api_key: str = ""
    #: The From address, e.g. "FlashML <no-reply@mail.zolliai.com>". Mail is
    #: sent only when this AND `resend_api_key` are both set.
    email_from: str = ""
    #: Reply-to. Falls back to `email_from`. The declined email invites a
    #: reply (re-applying is refused by design — POST /access-request 409s
    #: once decided), so this should be a monitored mailbox.
    email_reply_to: str = ""
```

Then in `from_env`, after the `console_url = os.environ.get("FLASHML_CONSOLE_URL", "")` line:

```python
        resend_api_key = os.environ.get("RESEND_API_KEY", "")
        email_from = os.environ.get("EMAIL_FROM", "")
        email_reply_to = os.environ.get("EMAIL_REPLY_TO", "")
```

Add them to the `cls(...)` call:

```python
            resend_api_key=resend_api_key,
            email_from=email_from,
            email_reply_to=email_reply_to,
```

And inside the `if require_auth:` block, beside the existing `console_url` warning:

```python
            # Warn, do not refuse. Half-configured mail is the case worth
            # naming: a key with no From address (or the reverse) looks
            # configured in the dashboard and sends nothing, so approvals go
            # back to being silent with no signal anywhere.
            if bool(resend_api_key) != bool(email_from):
                logging.getLogger("flashml-cloud-api").warning(
                    "Mail is half-configured: RESEND_API_KEY and EMAIL_FROM "
                    "must both be set. No approval or decline email will be "
                    "sent until they are."
                )
```

- [ ] **Step 4: Write the Mailer**

Create `flashml_cloud_api/mailer.py`:

```python
"""Sends product mail through Resend's HTTP API.

Two emails exist today — access approved and access declined — and both are
transactional: a direct response to something the recipient themselves did.
That is why there is no unsubscribe here. The first marketing or digest mail
changes that and must not be bolted onto this path.

Inert unless both RESEND_API_KEY and EMAIL_FROM are set. That is not a
convenience: every other test file in this suite constructs Settings without
mail configured, and a deploy with no provider must still boot and serve.

Mirrors CoordinatorClient's shape deliberately — settings in, an optional
httpx transport for tests, one AsyncClient per call. It does not mirror its
timeout: 60s is right for a coordinator hop and wrong inside an admin's
click, so this waits 10.
"""
from __future__ import annotations

import json
import logging

import httpx

from .settings import Settings

log = logging.getLogger("flashml-cloud-api")

RESEND_ENDPOINT = "https://api.resend.com/emails"


class Mailer:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
    ):
        self._api_key = settings.resend_api_key
        self._from = settings.email_from
        self._reply_to = settings.email_reply_to or settings.email_from
        self._transport = transport
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        """Both halves or nothing. A key with no From address cannot send."""
        return bool(self._api_key and self._from)

    async def send(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        text: str,
        user_id: str = "",
    ) -> bool:
        """True if the provider accepted it. Never raises.

        The caller has already committed a database write it must not undo,
        so every failure here is reported as False and swallowed. Nothing
        about the recipient or the response is logged: the address is
        personal data and a provider error body can echo the request,
        which carries the API key.
        """
        if not self.configured:
            return False

        payload = {
            "from": self._from,
            "to": [to],
            "subject": subject,
            "html": html,
            "text": text,
        }
        if self._reply_to:
            payload["reply_to"] = self._reply_to

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    RESEND_ENDPOINT,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json=payload,
                )
        except httpx.HTTPError:
            log.error(
                json.dumps({"text": "email send failed", "reason": "transport",
                            "user_id": user_id})
            )
            return False

        if response.status_code >= 400:
            log.error(
                json.dumps({"text": "email send failed",
                            "status": response.status_code, "user_id": user_id})
            )
            return False
        return True
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_mailer.py tests/test_settings.py -v`
Expected: PASS (all of `test_mailer.py`, and `test_settings.py` unchanged and still green)

- [ ] **Step 6: Verify no existing test regressed**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest -q`
Expected: **`19 failed` and nothing more** — the same 19 as the baseline (see Global Constraints). Passing count should rise by the new `test_mailer.py` tests. The new `Settings` fields all have defaults, so the seven files that construct `Settings(...)` are unaffected; if any of them newly fails, a default is missing.

- [ ] **Step 7: Commit**

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/mailer.py \
        flashml-cloud/apps/api/flashml_cloud_api/settings.py \
        flashml-cloud/apps/api/tests/test_mailer.py
git commit -m "feat(api): add Mailer and mail settings, inert until configured"
```

---

### Task 2: The two email bodies

**Files:**
- Create: `flashml-cloud/apps/api/flashml_cloud_api/mail_templates.py`
- Test: `flashml-cloud/apps/api/tests/test_mail_templates.py`

**Interfaces:**
- Consumes: nothing — pure functions, no imports from the rest of the package.
- Produces: `Email` (frozen dataclass with `.subject: str`, `.html: str`, `.text: str`), `admitted_email(console_url: str) -> Email`, `declined_email() -> Email`.

**Note:** `emails.py` is a *classifier* (free-provider domain list) and must not be touched. This is a separate module so Task 1 and Task 2 never edit the same file.

- [ ] **Step 1: Write the failing test**

Create `flashml-cloud/apps/api/tests/test_mail_templates.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_mail_templates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flashml_cloud_api.mail_templates'`

- [ ] **Step 3: Write the templates**

Create `flashml_cloud_api/mail_templates.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_mail_templates.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/mail_templates.py \
        flashml-cloud/apps/api/tests/test_mail_templates.py
git commit -m "feat(api): add admitted and declined email bodies"
```

---

### Task 3: Wire the mailer into approve and decline

**Depends on:** Tasks 1 and 2.

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py` (imports; `create_cloud_app` signature ~line 736; mailer construction beside `coordinator = CoordinatorClient(...)` ~line 764; handlers at lines 1219-1241)
- Modify: `flashml-cloud/apps/api/tests/test_jobs_from_repo.py` (`make_client` gains an optional `mailer` passthrough)
- Test: `flashml-cloud/apps/api/tests/test_admin_access_api.py` (append)

**Interfaces:**
- Consumes: `Mailer` and `Settings` (Task 1); `admitted_email`, `declined_email`, `Email` (Task 2); existing `dbmod.email_for_user(db, user_id) -> str | None` at `db.py:190`.
- Produces: both admin routes now return `{"user_id": str, "status": str, "emailed": bool}`. `create_cloud_app(..., mailer: Mailer | None = None)`.

- [ ] **Step 1: Write the failing tests**

First add these imports to the **top** of `flashml-cloud/apps/api/tests/test_admin_access_api.py`, beside the existing `from test_jobs_from_repo import (...)` block — not mid-file:

```python
import json
from dataclasses import replace

import httpx

from flashml_cloud_api.mailer import Mailer
```

Then append to the end of the same file:

```python
# ---------------------------------------------------------------------------
# email on decision
#
# Exactly-once is structural here, not bookkept: `approve_access_request`
# and `decline_access_request` both filter on `status = 'pending'`, so a
# second call matches no row and the route 404s before the mailer is
# reached. That is why there is no sent-log table and no migration.
# ---------------------------------------------------------------------------


class FakeResend(httpx.AsyncBaseTransport):
    def __init__(self, status: int = 200):
        self.requests: list[httpx.Request] = []
        self._status = status

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        return httpx.Response(self._status, json={"id": "msg_1"})

    @property
    def sent(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


def _mail_client(make_client, settings, status: int = 200):
    """A client whose mailer is configured and pointed at a fake Resend."""
    resend = FakeResend(status=status)
    configured = replace(
        settings,
        resend_api_key="re_test_key",
        email_from="FlashML <no-reply@mail.example>",
    )
    client = make_client(mailer=Mailer(configured, transport=resend))
    return client, resend


def test_approving_emails_the_account(make_client, settings, db):
    client, resend = _mail_client(make_client, settings)
    admin = _admin(db)
    user = _pending(client, db, email="her@example.com")

    r = client.post(
        f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin)
    )
    assert r.status_code == 200
    assert r.json()["status"] == "admitted"
    assert r.json()["emailed"] is True

    assert len(resend.sent) == 1
    assert resend.sent[0]["to"] == ["her@example.com"]
    assert "You're in" in resend.sent[0]["subject"]


def test_approving_twice_sends_exactly_one_email(make_client, settings, db):
    client, resend = _mail_client(make_client, settings)
    admin = _admin(db)
    user = _pending(client, db, email="her@example.com")

    first = client.post(
        f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin)
    )
    second = client.post(
        f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin)
    )
    assert first.status_code == 200
    assert second.status_code == 404
    assert len(resend.sent) == 1


def test_declining_sends_the_declined_email(make_client, settings, db):
    client, resend = _mail_client(make_client, settings)
    admin = _admin(db)
    user = _pending(client, db, email="them@example.com")

    r = client.post(
        f"/v1alpha1/admin/access-requests/{user}/decline", headers=_auth(admin)
    )
    assert r.status_code == 200
    assert r.json()["emailed"] is True
    assert len(resend.sent) == 1
    assert "About your FlashML request" in resend.sent[0]["subject"]
    assert "You're in" not in resend.sent[0]["subject"]


def test_a_provider_failure_still_admits_the_user(make_client, settings, db):
    """The admission is committed before the send. A 500 from Resend must
    cost the user their email, never their access."""
    client, resend = _mail_client(make_client, settings, status=500)
    admin = _admin(db)
    user = _pending(client, db, email="her@example.com")

    r = client.post(
        f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin)
    )
    assert r.status_code == 200
    assert r.json()["emailed"] is False
    assert _request_row(db, user)["status"] == "admitted"
    assert _admitted_at(db, user) is not None


def test_an_account_with_no_address_is_still_admitted(make_client, settings, db):
    client, resend = _mail_client(make_client, settings)
    admin = _admin(db)
    user = _pending(client, db)  # no email seeded in auth.users

    r = client.post(
        f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin)
    )
    assert r.status_code == 200
    assert r.json()["emailed"] is False
    assert resend.sent == []
    assert _admitted_at(db, user) is not None


def test_an_unconfigured_deploy_sends_nothing_and_still_works(make_client, db):
    """The default client has no mail configured — the shape every other
    test file in this suite runs under."""
    client = make_client()
    admin = _admin(db)
    user = _pending(client, db, email="her@example.com")

    r = client.post(
        f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin)
    )
    assert r.status_code == 200
    assert r.json()["emailed"] is False
    assert _admitted_at(db, user) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_admin_access_api.py -v -k email or address or admits or provider or twice`
Expected: FAIL — `make_client()` does not accept `mailer`, and responses have no `emailed` key.

- [ ] **Step 3: Add the mailer passthrough to `make_client`**

In `tests/test_jobs_from_repo.py`, change the `build` function inside the `make_client` fixture to accept and forward a mailer:

```python
    def build(
        files: dict[str, str] | None = None,
        tar_bytes: bytes | None = None,
        mailer=None,
    ):
```

and add `mailer=mailer,` to its `create_cloud_app(...)` call.

- [ ] **Step 4: Wire the mailer into the app factory**

In `flashml_cloud_api/app.py`, add to the imports:

```python
from .mail_templates import admitted_email, declined_email
from .mailer import Mailer
```

Add a parameter to `create_cloud_app` (after `start_federated_job`):

```python
    mailer: Mailer | None = None,
```

and beside `coordinator = CoordinatorClient(settings, transport=transport)`:

```python
    # Its own transport, not the coordinator's: these are two unrelated
    # hosts, and a test fake for one must not have to answer for the other.
    mailer = mailer or Mailer(settings)
```

- [ ] **Step 5: Send from the two handlers**

Replace the body of `approve_request` (`app.py:1219-1231`) so the send happens *after* the 404 guard:

```python
        _uuid_or_400(user_id)
        # 404, not 200, when nothing was pending: reporting success for a
        # call that changed nothing is how a queue silently stops working.
        #
        # It is also what makes the email exactly-once. The guard below
        # matches only a row still in 'pending', so a second approve returns
        # here and never reaches the mailer — no sent-log table needed.
        if not dbmod.approve_access_request(db, user_id, decided_by=admin_id):
            raise HTTPException(status_code=404, detail="no pending request")
        emailed = await _send_decision_email(db, mailer, settings, user_id, admitted=True)
        return {"user_id": user_id, "status": "admitted", "emailed": emailed}
```

and `decline_request` (`app.py:1232-1241`):

```python
        _uuid_or_400(user_id)
        if not dbmod.decline_access_request(db, user_id, decided_by=admin_id):
            raise HTTPException(status_code=404, detail="no pending request")
        emailed = await _send_decision_email(db, mailer, settings, user_id, admitted=False)
        return {"user_id": user_id, "status": "declined", "emailed": emailed}
```

Add this helper at module level in `app.py`, next to the other module-level helpers (above `create_cloud_app`):

```python
async def _send_decision_email(
    db: psycopg.Connection,
    mailer: Mailer,
    settings: Settings,
    user_id: str,
    *,
    admitted: bool,
) -> bool:
    """Tell the account what was decided. Returns whether mail went out.

    The database write has already committed by the time this runs, and
    nothing here may undo it — an account that is admitted stays admitted
    whether or not the provider answered. The boolean travels back to the
    console so the admin's toast can say which of the two actually
    happened instead of assuming.
    """
    address = dbmod.email_for_user(db, user_id)
    if address is None:
        return False
    message = admitted_email(settings.console_url) if admitted else declined_email()
    return await mailer.send(
        to=address,
        subject=message.subject,
        html=message.html,
        text=message.text,
        user_id=user_id,
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_admin_access_api.py -v`
Expected: PASS — including the pre-existing `test_approving_twice_is_a_404_the_second_time`.

- [ ] **Step 7: Run the whole API suite**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest -q`
Expected: **still exactly the baseline 19 failures**, no more (see Global Constraints). This task changes `create_cloud_app`'s signature and two handlers, so it is the task most likely to break an unrelated test — diff your failure list against `baseline-failures.txt` before declaring done. Report counts in the report file.

- [ ] **Step 8: Commit**

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/app.py \
        flashml-cloud/apps/api/tests/test_admin_access_api.py \
        flashml-cloud/apps/api/tests/test_jobs_from_repo.py
git commit -m "feat(api): email the account when access is approved or declined"
```

---

### Task 4: The console tells the truth

**Depends on:** Task 3 (the `emailed` field must exist).

**Files:**
- Modify: `flashml-cloud/apps/web/lib/cloud-api.ts:1113-1130` (both client functions + a new response type)
- Modify: `flashml-cloud/apps/web/app/(console)/admin/requests/page.tsx:88-101` (approve toast; and the decline toast beside it)
- Modify: `flashml-cloud/apps/web/components/onboarding/PendingScreen.tsx:14-22, 40-69`

**Interfaces:**
- Consumes: `{user_id, status, emailed}` from Task 3.
- Produces: `AccessDecision` exported from `lib/cloud-api.ts`.

- [ ] **Step 1: Change the two client functions**

In `lib/cloud-api.ts`, replace the `Promise<void>` signatures:

```ts
/** What the admin queue's approve/decline routes answer with. `emailed` is
 * false when no provider is configured, when the account has no address, or
 * when the provider refused — the caller must not assume a message went
 * out. */
export interface AccessDecision {
  user_id: string;
  status: string;
  emailed: boolean;
}

/** `POST /v1alpha1/admin/access-requests/{userId}/approve` — admin only.
 * 404s (via `NotFound`) when there was no pending request for this user;
 * `NotFound` here means "nothing to approve", not "unknown user". */
export function approveAccessRequest(userId: string): Promise<AccessDecision> {
  return request<AccessDecision>(
    `/v1alpha1/admin/access-requests/${encodeURIComponent(userId)}/approve`,
    { method: "POST" }
  );
}

/** The decline counterpart of `approveAccessRequest` — same route shape,
 * same 404-means-nothing-pending doctrine. */
export function declineAccessRequest(userId: string): Promise<AccessDecision> {
  return request<AccessDecision>(
    `/v1alpha1/admin/access-requests/${encodeURIComponent(userId)}/decline`,
    { method: "POST" }
  );
}
```

- [ ] **Step 2: Make the toast read the flag**

In `app/(console)/admin/requests/page.tsx`, replace the approve toast and the comment above it:

```tsx
      const decision = await approveAccessRequest(row.user_id);
      // Say which of the two actually happened. An unconditional "we
      // emailed them" would just relocate the dishonesty this replaced:
      // mail is skipped when no provider is configured, when the account
      // has no address, and when the provider refuses.
      toast.success(
        decision.emailed
          ? "Approved — they're in, and we've emailed them."
          : "Approved — they're in. No email went out, so let them know yourself."
      );
```

Apply the same pattern to the decline handler in the same file, using "Declined — we've let them know." / "Declined. No email went out."

- [ ] **Step 3: Make the pending screen honest**

In `components/onboarding/PendingScreen.tsx`, delete the comment block at lines 14-22 (it explains why no email is promised, which stops being true) and replace it with:

```tsx
/** Stands in for the whole console while `access` is `pending` — the
 * request is in and an admin has not decided yet.
 *
 * Approval and decline both send mail now (see
 * `docs/superpowers/specs/2026-08-10-transactional-email-design.md`), so
 * this screen may finally promise one. It still offers Reload, because a
 * person holding this tab open when the decision lands should not have to
 * wait for an inbox.
 */
```

Then replace the `Already approved? Reload this page.` line (`PendingScreen.tsx:40-69`) with exactly:

```tsx
            <p className="text-sm text-muted-foreground">
              We&rsquo;ll email you at{" "}
              <span className="font-medium text-foreground">
                {email ?? "the address you signed up with"}
              </span>{" "}
              as soon as a human has looked at it. Already approved? Reload
              this page.
            </p>
```

Keep the existing Reload button directly beneath it. The existing description line — *"A human reads every request — ZolliAI Cloud is a small alpha, not an automated signup."* — stays as it is; "ZolliAI" is brand and does not retire.

Change the `/pools/join` link text from `Have a Crew invite code?` to `Have a workspace invite code?` (owner decision §6.3 as amended — "Crew" retires, "workspace" stays).

- [ ] **Step 4: Verify the console builds and its tests pass**

Run: `cd flashml-cloud/apps/web && npm run lint && npx tsc --noEmit && npm test`
Expected: PASS. `tsc` is the one that matters — it proves every `approveAccessRequest` caller was updated for the changed return type.

- [ ] **Step 5: Commit**

```bash
git add flashml-cloud/apps/web/lib/cloud-api.ts \
        "flashml-cloud/apps/web/app/(console)/admin/requests/page.tsx" \
        flashml-cloud/apps/web/components/onboarding/PendingScreen.tsx
git commit -m "feat(web): report whether the decision email actually sent"
```

---

### Task 5: Deployment config and the standing instruction

**Independent** — may run in parallel with Tasks 1-4. Ships no behaviour on its own.

**Files:**
- Modify: `flashml-cloud/render.yaml` (service `flashml-api` envVars ~line 150; service `flashml-dev-api` envVars ~line 340)
- Modify: `flashml-cloud/flashml-cloud/CLAUDE.md` ("Granting access and admin" section, the "Approval is silent" paragraph)

- [ ] **Step 1: Add the three variables to both API services**

In `render.yaml`, inside the `envVars:` block of **both** `flashml-api` and `flashml-dev-api`:

```yaml
      # Mail. The API sends two product emails — access approved and access
      # declined. Absent, it boots and serves and sends nothing: a missing
      # provider must not take the API down, so these are NOT in
      # settings.from_env's required list.
      #
      # Supabase's own SMTP setting is separate and governs AUTH mail
      # (password reset, confirmation) — it is configured in the Supabase
      # dashboard, not here, and points at the same Resend account.
      - key: RESEND_API_KEY
        sync: false
      - key: EMAIL_FROM
        value: "FlashML <no-reply@mail.zolliai.com>"
      - key: EMAIL_REPLY_TO
        value: "hello@mail.zolliai.com"
```

`RESEND_API_KEY` is `sync: false` (Render prompts once, never in git). The two addresses are not secrets and stay as plain values.

- [ ] **Step 2: Fix the standing instruction that forbids this feature**

In `flashml-cloud/flashml-cloud/CLAUDE.md`, replace the **Approval is silent** paragraph. It currently ends *"No copy anywhere may imply a message was sent"* — a standing instruction to every future agent that directly contradicts what now ships, and which will otherwise get the new copy "fixed" back. Replace with:

```markdown
**Approval sends mail.** Approving or declining emails the account through
Resend (`mailer.py`), configured by `RESEND_API_KEY` + `EMAIL_FROM`. Both
routes return `emailed`, and copy must reflect that flag rather than assume
either outcome — mail is skipped when no provider is configured, when the
account has no address in `auth.users`, and when the provider refuses. With
mail unconfigured the product behaves exactly as before: the flag is false
and telling the person is manual. Supabase's built-in SMTP (~2/hour
project-wide) is still not usable for this; custom SMTP in the Supabase
dashboard covers auth mail only.
```

- [ ] **Step 3: Verify nothing else still claims approval is silent**

Run:

```bash
cd /Users/phongcao/Work/Zolli-Labs/flashml-cloud && \
  grep -rn "silent\|no email provider\|Let them know yourself" \
  --include="*.md" --include="*.tsx" --include="*.py" . | grep -v node_modules | grep -v ".venv"
```

Expected: no remaining claim that no email provider exists. Fix any that survive (`PendingScreen.tsx` and `admin/requests/page.tsx` are handled in Task 4).

- [ ] **Step 4: Commit**

```bash
git add flashml-cloud/render.yaml flashml-cloud/flashml-cloud/CLAUDE.md
git commit -m "chore: declare mail env vars and correct the approval-is-silent rule"
```

---

## Operator setup (not code — do before the first real send)

1. Create a Resend account (free tier: 3,000/month, 100/day, 1 domain).
2. **DONE 2026-08-10 — verified the apex `zolliai.com`.** Records confirmed live: DKIM at `resend._domainkey.zolliai.com`, and the bounce pair on `send.zolliai.com` (MX `feedback-smtp.us-east-1.amazonses.com`, TXT `v=spf1 include:amazonses.com ~all`). Note this is the **apex**, not the `mail.` subdomain this plan originally assumed — **`EMAIL_FROM` must therefore be `@zolliai.com`.** Verification does not cascade to subdomains: sending from `no-reply@mail.zolliai.com` against an apex-verified domain returns `403 "The mail.zolliai.com domain is not verified"`, an error that names the domain and never the variable. Adding the records did not disturb the other product on that zone — the apex carried no prior TXT or MX.
3. **DONE 2026-08-10 — `EMAIL_REPLY_TO` is the owner's own monitored inbox.** The blocking prerequisite, kept here because the reasoning outlives the fix: Resend verification proves a domain can *send* and says nothing about *receiving*. `zolliai.com` has **no MX record at all** (checked), so no address there receives anything, and `hello@…` would have bounced. The declined email's only call to action is "reply to this message" (re-applying is refused by design — `POST /access-request` 409s once decided), so an unresolved reply-to strands exactly the person who most needs a way back in. Reply-To need not sit on the verified sending domain; only `From` does.
4. Create a send-only API key; set `RESEND_API_KEY` on both Render API services.
5. Point Supabase Auth's custom SMTP at Resend on both `flashml-poc` (prod) and `flashml-dev`. This lifts the 2/hour cap and is the prerequisite for password reset — it does **not** carry the two emails in this plan.
6. Leave "Confirm email" **OFF** — turning it on changes the two-step signup flow and is out of scope.

## Definition of Done

Per `HANDBOOK.md` §8: red test first and watched failing for the right reason; full API suite green with counts; docs updated in the same session; a `PROGRESS.md` entry per the logging protocol.

**Demo that closes the plan:** approve a real pending account in the dev console and have the mail arrive; approve it again and show that no second mail is sent and the route 404s.
