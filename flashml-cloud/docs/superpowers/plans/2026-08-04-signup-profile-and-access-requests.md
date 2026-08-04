# Signup Profile and Access Requests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dead-end invite gate with an onboarding form that doubles as an access request, a queue the owner approves from, and profile columns worth analysing.

**Architecture:** `admitted_at` currently means both "allowed into FlashML" and "member of a pool". This splits them: access becomes an account property decided by an admin through `public.access_requests`; pool membership stays a workspace property decided by its owner. `GET /v1alpha1/me` gains a derived `access` state that the console switches on, replacing the `InviteGate` boolean.

**Tech Stack:** FastAPI + psycopg3 + Postgres (`apps/api`), Next.js 15 App Router + TypeScript + Tailwind (`apps/web`), pytest, vitest.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-04-signup-profile-and-access-requests-design.md`. Read it before starting.
- **Never edit an applied migration.** The runner checksums `migrations/*.sql`; an edit to an applied file reads as drift and blocks every later migration. New file only.
- **RLS on, zero policies,** on every new table. The API holds the service-role key and is the only door. Do not add a policy for `anon` or `authenticated`.
- **`admitted_at` stays the switch.** `admitted_user` and the seven placement gates read it and must not change. `access_requests` is the paperwork.
- **`access` is derived, never stored.** No `access_requests` row → `needs_onboarding`; otherwise the row's `status`.
- **Never accept `is_admin`, `admitted_at`, `is_host`, `is_developer`, or `github_login` from a request body.**
- **Email is read from `auth.users`, never from client input, never from the JWT** (`_jwt` in the test fixtures carries only `sub`/`aud`/`exp`).
- Enumerations, exact values:
  - `role`: `researcher`, `ml_engineer`, `student`, `founder`, `other`
  - `team_size`: `solo`, `2_5`, `6_20`, `20_plus`
  - `compute_sources`: `own_machines`, `colab`, `runpod`, `cloud`, `none`
  - `status`: `pending`, `admitted`, `declined`
  - `access`: `needs_onboarding`, `pending`, `admitted`, `declined`
- Run API tests from `apps/api` with its venv: `pytest tests/ -v`. Run web tests from `apps/web`: `npm test`.
- Commit after every task. Conventional commits (`feat:`, `test:`, `refactor:`).

## File Structure

| File | Responsibility |
|---|---|
| `apps/api/migrations/0009_access_requests.sql` | **Create.** Profile columns, `access_requests`, backfill. |
| `apps/api/flashml_cloud_api/emails.py` | **Create.** Email domain derivation only. |
| `apps/api/flashml_cloud_api/access.py` | **Create.** Enumerations + payload validation for the form. |
| `apps/api/flashml_cloud_api/db.py` | **Modify.** Access-request queries; decouple `consume_pool_invite`. |
| `apps/api/flashml_cloud_api/app.py` | **Modify.** `admin_user` dep, `/me`, `/access-request`, admin routes, `/invites/accept`. |
| `apps/api/tests/conftest.py:122` | **Modify.** Auth stub gains `email`. |
| `apps/api/tests/test_migrate.py:29` | **Modify.** Same stub, kept identical. |
| `apps/web/lib/cloud-api.ts` | **Modify.** `access` on `Profile`, new request functions. |
| `apps/web/components/onboarding/OnboardingForm.tsx` | **Create.** The seven questions. |
| `apps/web/components/onboarding/PendingScreen.tsx` | **Create.** Waiting state. |
| `apps/web/components/onboarding/DeclinedScreen.tsx` | **Create.** Declined state. |
| `apps/web/components/shell/ConsoleShell.tsx` | **Modify.** Four-way switch replaces `gated`. |
| `apps/web/components/shell/InviteGate.tsx` | **Delete.** |
| `apps/web/app/(console)/admin/requests/page.tsx` | **Create.** The queue. |
| `apps/web/app/(console)/account/page.tsx` | **Modify.** New editable fields. |
| `apps/web/app/(console)/pools/page.tsx` | **Modify.** "Join with a code". |
| `apps/web/app/(console)/pools/join/page.tsx` | **Modify.** Pending-account copy. |

---

### Task 1: Migration and the test auth stub

**Files:**
- Create: `apps/api/migrations/0009_access_requests.sql`
- Modify: `apps/api/tests/conftest.py:122`
- Modify: `apps/api/tests/test_migrate.py:29`
- Test: `apps/api/tests/test_access_schema.py`

**Interfaces:**
- Produces: table `public.access_requests` with columns `user_id, status, use_case, compute_sources, heard_from, pending_pool_id, invited_by, requested_at, decided_at, decided_by`; `public.profiles` columns `first_name, last_name, company_name, role, team_size, email_domain, is_personal_email, is_admin`; `auth.users.email` available in the test harness.

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_access_schema.py`:

```python
"""0009 — the access-request schema.

The migration runs against the ephemeral Postgres in conftest, so these
assertions are against a really-applied migration, not a parsed file.
"""
from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import dict_row


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def _columns(db, schema: str, table: str) -> dict[str, str]:
    with db.cursor() as cur:
        cur.execute(
            """
            select column_name, data_type
              from information_schema.columns
             where table_schema = %s and table_name = %s
            """,
            (schema, table),
        )
        return {r["column_name"]: r["data_type"] for r in cur.fetchall()}


def test_profiles_gains_the_onboarding_columns(db):
    cols = _columns(db, "public", "profiles")
    for name in (
        "first_name", "last_name", "company_name", "role", "team_size",
        "email_domain", "is_personal_email", "is_admin",
    ):
        assert name in cols, f"profiles.{name} missing"


def test_is_admin_defaults_false_and_is_not_null(db):
    with db.cursor() as cur:
        cur.execute(
            """
            select column_default, is_nullable
              from information_schema.columns
             where table_schema = 'public' and table_name = 'profiles'
               and column_name = 'is_admin'
            """
        )
        row = cur.fetchone()
    assert row["is_nullable"] == "NO"
    assert "false" in row["column_default"]


def test_access_requests_table_exists_with_expected_columns(db):
    cols = _columns(db, "public", "access_requests")
    assert cols["user_id"] == "uuid"
    assert cols["status"] == "text"
    assert cols["compute_sources"] == "ARRAY"
    for name in (
        "use_case", "heard_from", "pending_pool_id", "invited_by",
        "requested_at", "decided_at", "decided_by",
    ):
        assert name in cols, f"access_requests.{name} missing"


def test_rls_is_enabled_with_zero_policies(db):
    """Same discipline as every other table: the API is the only door."""
    with db.cursor() as cur:
        cur.execute(
            "select relrowsecurity from pg_class where relname = 'access_requests'"
        )
        assert cur.fetchone()["relrowsecurity"] is True
        cur.execute(
            "select count(*) as n from pg_policies where tablename = 'access_requests'"
        )
        assert cur.fetchone()["n"] == 0


def test_status_is_constrained_to_the_three_states(db):
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (gen_random_uuid()) returning id")
        user_id = cur.fetchone()["id"]
        cur.execute("insert into public.profiles (id) values (%s)", (user_id,))
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "insert into public.access_requests (user_id, status) values (%s, %s)",
                (user_id, "banana"),
            )


def test_auth_users_stub_has_email_like_real_supabase(db):
    """Real `auth.users` has an email column; the test stub must too, or
    every email-derivation test passes against a schema that isn't the
    deployed one."""
    assert "email" in _columns(db, "auth", "users")


def test_existing_admitted_profiles_are_backfilled_as_admitted(db):
    """Grandfathered testers must NOT compute as needs_onboarding — they
    would be shown the form despite already being admitted."""
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (gen_random_uuid()) returning id")
        user_id = cur.fetchone()["id"]
        cur.execute(
            "insert into public.profiles (id, admitted_at) values (%s, now())",
            (user_id,),
        )
        # Re-running the backfill statement is what the migration does; it
        # must be idempotent and must pick this row up.
        cur.execute(
            """
            insert into public.access_requests (user_id, status, decided_at)
            select p.id, 'admitted', p.admitted_at
              from public.profiles p
             where p.admitted_at is not null
            on conflict (user_id) do nothing
            """
        )
        cur.execute(
            "select status from public.access_requests where user_id = %s", (user_id,)
        )
        assert cur.fetchone()["status"] == "admitted"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/api && pytest tests/test_access_schema.py -v`
Expected: FAIL — `access_requests` does not exist, `profiles.first_name` missing, `auth.users.email` missing.

- [ ] **Step 3: Add `email` to the test auth stub, in both places**

In `apps/api/tests/conftest.py`, line 122, replace:

```python
            "create schema auth; create table auth.users (id uuid primary key);",
```

with:

```python
            # `email` mirrors real Supabase `auth.users`. Without it, every
            # email-derivation test would pass against a schema that is not
            # the deployed one. No migration touches auth.users — that
            # schema is Supabase's, and this stub exists to imitate it.
            "create schema auth; "
            "create table auth.users (id uuid primary key, email text);",
```

In `apps/api/tests/test_migrate.py`, line 29, make `AUTH_STUB` identical:

```python
AUTH_STUB = (
    "create schema auth; "
    "create table auth.users (id uuid primary key, email text);"
)
```

- [ ] **Step 4: Write the migration**

Create `apps/api/migrations/0009_access_requests.sql`:

```sql
-- 0009_access_requests.sql
--
-- Onboarding profile fields, and the access-request queue that replaces the
-- invite gate.
--
-- WHY THIS EXISTS. `admitted_at` (0007) meant two things at once: allowed
-- into FlashML, and member of a pool — `pool_invites` says so outright.
-- Redeeming a workspace invite was therefore the only way in, and an
-- uninvited signup was a dead end with nothing to ask and nothing to
-- review. This separates them. Access is an account property an admin
-- decides here; pool membership stays a workspace property its owner
-- decides. `admitted_at` REMAINS the switch every gate reads — this table
-- is the paperwork behind it, not a replacement for it.
--
-- The backfill is load-bearing, not cosmetic: `access` is DERIVED (no row
-- => needs_onboarding), so without it every grandfathered tester would be
-- shown the onboarding form despite already being admitted.
--
-- auth.users is deliberately untouched. That schema is Supabase's; the
-- email address is read from it, never written.
--
-- HOW THIS IS APPLIED: by the migration runner,
-- `python -m flashml_cloud_api.migrate`, which records it in
-- public.schema_migrations. There are two databases, dev (auto-migrated on
-- merge to `develop`) and production (gated behind a manual workflow).
--
-- Do not edit this file after it has been applied anywhere: the runner
-- checksums it, and an edit reads as drift and blocks every later
-- migration.

-- ---------------------------------------------------------------------------
-- profiles: the facts a user owns and edits forever.
-- ---------------------------------------------------------------------------
alter table public.profiles add column if not exists first_name        text;
alter table public.profiles add column if not exists last_name         text;
alter table public.profiles add column if not exists company_name      text;
alter table public.profiles add column if not exists role              text;
alter table public.profiles add column if not exists team_size         text;
alter table public.profiles add column if not exists email_domain      text;
alter table public.profiles add column if not exists is_personal_email boolean;
alter table public.profiles add column if not exists is_admin          boolean not null default false;

comment on column public.profiles.email_domain is
    'Derived server-side from the auth.users address, never accepted from a '
    'client. Full host, so a subdomain stays distinguishable.';
comment on column public.profiles.is_admin is
    'Grants the access-request queue. Deliberately has no granting UI: set '
    'it with one UPDATE against your own row.';

create index if not exists profiles_email_domain_idx on public.profiles (email_domain);

-- ---------------------------------------------------------------------------
-- access_requests: one row per account, the point-in-time record that was
-- screened. `access` is derived from it and is never stored.
-- ---------------------------------------------------------------------------
create table if not exists public.access_requests (
    user_id         uuid primary key references public.profiles(id) on delete cascade,
    status          text not null check (status in ('pending', 'admitted', 'declined')),
    use_case        text,
    compute_sources text[] not null default '{}',
    heard_from      text,
    pending_pool_id uuid references public.pools(id) on delete set null,
    invited_by      uuid references public.profiles(id) on delete set null,
    requested_at    timestamptz not null default now(),
    decided_at      timestamptz,
    decided_by      uuid references public.profiles(id) on delete set null
);

comment on table public.access_requests is
    'One row per account. status drives the console''s access state (no row '
    '= needs_onboarding). pending_pool_id is a workspace invite redeemed '
    'before approval: the join is materialised when an admin approves.';

alter table public.access_requests enable row level security;
create index if not exists access_requests_status_idx
    on public.access_requests (status, requested_at);

-- ---------------------------------------------------------------------------
-- Backfill. Every account admitted before this migration keeps working and
-- is never shown the form. Accounts with a NULL admitted_at get no row on
-- purpose: they correctly compute as needs_onboarding.
-- ---------------------------------------------------------------------------
insert into public.access_requests (user_id, status, decided_at)
select p.id, 'admitted', p.admitted_at
  from public.profiles p
 where p.admitted_at is not null
on conflict (user_id) do nothing;
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd apps/api && pytest tests/test_access_schema.py tests/test_migrate.py -v`
Expected: PASS.

- [ ] **Step 6: Run the whole API suite for regressions**

Run: `cd apps/api && pytest tests/ -q`
Expected: PASS. The auth-stub change touches every test that inserts into `auth.users`; adding a nullable column must not break them.

- [ ] **Step 7: Commit**

```bash
git add apps/api/migrations/0009_access_requests.sql apps/api/tests/test_access_schema.py apps/api/tests/conftest.py apps/api/tests/test_migrate.py
git commit -m "feat: add access_requests schema and onboarding profile columns"
```

---

### Task 2: Email domain derivation

**Files:**
- Create: `apps/api/flashml_cloud_api/emails.py`
- Test: `apps/api/tests/test_emails.py`

**Interfaces:**
- Produces: `derive_email_facts(email: str | None) -> tuple[str | None, bool | None]` returning `(email_domain, is_personal_email)`; `PERSONAL_EMAIL_DOMAINS: frozenset[str]`.

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_emails.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/api && pytest tests/test_emails.py -v`
Expected: FAIL — `ModuleNotFoundError: flashml_cloud_api.emails`.

- [ ] **Step 3: Write the implementation**

Create `apps/api/flashml_cloud_api/emails.py`:

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/api && pytest tests/test_emails.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/flashml_cloud_api/emails.py apps/api/tests/test_emails.py
git commit -m "feat: derive email domain and personal-provider flag"
```

---

### Task 3: Form validation and enumerations

**Files:**
- Create: `apps/api/flashml_cloud_api/access.py`
- Test: `apps/api/tests/test_access_validation.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ROLES`, `TEAM_SIZES`, `COMPUTE_SOURCES`, `HEARD_FROM` (each `frozenset[str]`); `OnboardingSubmission` dataclass with fields `first_name, last_name, company_name, role, team_size, use_case, compute_sources: list[str], heard_from`; `parse_submission(payload: dict) -> OnboardingSubmission` raising `ValueError(message)` on bad input.

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_access_validation.py`:

```python
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
}


def test_accepts_a_complete_submission():
    s = parse_submission(dict(VALID))
    assert s.first_name == "Ha"
    assert s.compute_sources == ["own_machines", "colab"]


def test_trims_whitespace_on_every_text_field():
    s = parse_submission({**VALID, "first_name": "  Ha  ", "company_name": " VinAI "})
    assert s.first_name == "Ha"
    assert s.company_name == "VinAI"


@pytest.mark.parametrize(
    "field", ["first_name", "last_name", "company_name", "use_case"]
)
def test_required_text_fields_cannot_be_blank(field):
    for blank in ("", "   "):
        with pytest.raises(ValueError, match=field):
            parse_submission({**VALID, field: blank})


@pytest.mark.parametrize(
    "field", ["first_name", "last_name", "company_name", "use_case"]
)
def test_required_text_fields_cannot_be_missing(field):
    payload = dict(VALID)
    del payload[field]
    with pytest.raises(ValueError, match=field):
        parse_submission(payload)


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


def test_privileged_fields_in_the_body_are_ignored_not_honoured():
    """A client handing us a role is the attack this shape exists to make
    impossible: parse_submission has nowhere to put it."""
    s = parse_submission(
        {**VALID, "is_admin": True, "admitted_at": "now", "is_host": True}
    )
    assert not hasattr(s, "is_admin")
    assert not hasattr(s, "admitted_at")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/api && pytest tests/test_access_validation.py -v`
Expected: FAIL — `ModuleNotFoundError: flashml_cloud_api.access`.

- [ ] **Step 3: Write the implementation**

Create `apps/api/flashml_cloud_api/access.py`:

```python
"""The onboarding form: what it may contain, and nothing else.

`parse_submission` is a whitelist by construction — it reads the fields it
knows and has nowhere to put anything else. A body carrying `is_admin` or
`admitted_at` is not rejected with an error that tells an attacker the
field name exists; it is simply never read. Same discipline as
`PATCH /v1alpha1/me`, which has taken exactly one key since it shipped.
"""
from __future__ import annotations

from dataclasses import dataclass

ROLES = frozenset({"researcher", "ml_engineer", "student", "founder", "other"})
TEAM_SIZES = frozenset({"solo", "2_5", "6_20", "20_plus"})
COMPUTE_SOURCES = frozenset({"own_machines", "colab", "runpod", "cloud", "none"})
HEARD_FROM = frozenset(
    {"github", "search", "twitter", "friend", "paper", "event", "other"}
)

NAME_MAX = 80
COMPANY_MAX = 160
USE_CASE_MAX = 2000


@dataclass(frozen=True)
class OnboardingSubmission:
    first_name: str
    last_name: str
    company_name: str
    role: str
    team_size: str
    use_case: str
    compute_sources: list[str]
    heard_from: str | None


def _required_text(payload: dict, field: str, cap: int) -> str:
    raw = payload.get(field)
    if not isinstance(raw, str):
        raise ValueError(f"{field} is required")
    value = raw.strip()
    if not value:
        raise ValueError(f"{field} is required")
    if len(value) > cap:
        raise ValueError(f"{field} is limited to {cap} characters")
    return value


def _one_of(payload: dict, field: str, allowed: frozenset[str]) -> str:
    raw = payload.get(field)
    if not isinstance(raw, str) or raw not in allowed:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(allowed))}")
    return raw


def parse_submission(payload: dict) -> OnboardingSubmission:
    """Validate the form body. Raises ``ValueError`` with a message safe to
    show a user; the route turns that into a 400."""
    sources = payload.get("compute_sources", [])
    if not isinstance(sources, list):
        raise ValueError("compute_sources must be a list")
    seen: list[str] = []
    for item in sources:
        if not isinstance(item, str) or item not in COMPUTE_SOURCES:
            raise ValueError(
                "compute_sources must contain only: "
                + ", ".join(sorted(COMPUTE_SOURCES))
            )
        if item not in seen:
            seen.append(item)

    heard = payload.get("heard_from")
    if heard is not None:
        heard = _one_of(payload, "heard_from", HEARD_FROM)

    return OnboardingSubmission(
        first_name=_required_text(payload, "first_name", NAME_MAX),
        last_name=_required_text(payload, "last_name", NAME_MAX),
        company_name=_required_text(payload, "company_name", COMPANY_MAX),
        role=_one_of(payload, "role", ROLES),
        team_size=_one_of(payload, "team_size", TEAM_SIZES),
        use_case=_required_text(payload, "use_case", USE_CASE_MAX),
        compute_sources=seen,
        heard_from=heard,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/api && pytest tests/test_access_validation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/flashml_cloud_api/access.py apps/api/tests/test_access_validation.py
git commit -m "feat: validate onboarding form payloads"
```

---

### Task 4: Access-request data layer

**Files:**
- Modify: `apps/api/flashml_cloud_api/db.py` (append a new section after the profiles section, which ends around line 95)
- Test: `apps/api/tests/test_db_access.py`

**Interfaces:**
- Consumes: `OnboardingSubmission` (Task 3), `derive_email_facts` (Task 2).
- Produces, all in `db.py`:
  - `access_state_for(db, user_id) -> str` — one of `needs_onboarding`/`pending`/`admitted`/`declined`
  - `email_for_user(db, user_id) -> str | None`
  - `profile_is_admin(db, user_id) -> bool`
  - `submit_access_request(db, user_id, submission, *, email_domain, is_personal_email) -> None`
  - `list_access_requests(db, *, status="pending") -> list[dict]`
  - `approve_access_request(db, user_id, *, decided_by) -> bool`
  - `decline_access_request(db, user_id, *, decided_by) -> bool`
  - `record_pending_invite(db, user_id, *, pool_id, invited_by) -> None`

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_db_access.py`:

```python
"""The access-request data layer.

Written against the real ephemeral Postgres, like every other db test
here — the transactional guarantee in `approve_access_request` is the
whole point of this module and cannot be shown against a mock.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from flashml_cloud_api import db as dbmod
from flashml_cloud_api.access import parse_submission

SUBMISSION = parse_submission(
    {
        "first_name": "Ha",
        "last_name": "Nguyen",
        "company_name": "VinAI",
        "role": "researcher",
        "team_size": "2_5",
        "use_case": "Fine-tune across the lab's machines.",
        "compute_sources": ["own_machines", "colab"],
        "heard_from": "github",
    }
)


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def _user(db, *, email: str | None = None, admitted: bool = False) -> str:
    """A real ``auth.users`` + ``public.profiles`` pair — profiles.id is an
    FK to auth.users, so both rows are required."""
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id, email) values (%s, %s)", (user_id, email)
        )
        cur.execute(
            "insert into public.profiles (id, admitted_at) values (%s, %s)",
            (user_id, datetime.now(timezone.utc) if admitted else None),
        )
    return user_id


def _pool(db, owner_id: str) -> str:
    pool_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into public.pools (id, name, owner_id) values (%s, %s, %s)",
            (pool_id, "Lab", owner_id),
        )
    return pool_id


# -- access_state_for -------------------------------------------------------

def test_no_row_is_needs_onboarding(db):
    assert dbmod.access_state_for(db, _user(db)) == "needs_onboarding"


def test_state_follows_the_row_status(db):
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain="vinai.io", is_personal_email=False
    )
    assert dbmod.access_state_for(db, user) == "pending"
    dbmod.approve_access_request(db, user, decided_by=user)
    assert dbmod.access_state_for(db, user) == "admitted"


def test_declined_is_its_own_state(db):
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    dbmod.decline_access_request(db, user, decided_by=user)
    assert dbmod.access_state_for(db, user) == "declined"


# -- submit -----------------------------------------------------------------

def test_submit_writes_profile_columns_and_seeds_display_name(db):
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain="vinai.io", is_personal_email=False
    )
    with db.cursor() as cur:
        cur.execute(
            "select first_name, last_name, company_name, role, team_size, "
            "       email_domain, is_personal_email, display_name "
            "  from public.profiles where id = %s",
            (user,),
        )
        row = cur.fetchone()
    assert row["first_name"] == "Ha"
    assert row["company_name"] == "VinAI"
    assert row["email_domain"] == "vinai.io"
    assert row["is_personal_email"] is False
    assert row["display_name"] == "Ha Nguyen"


def test_submit_does_not_overwrite_a_display_name_the_user_chose(db):
    user = _user(db)
    dbmod.upsert_profile(db, user, display_name="hanguyen")
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    with db.cursor() as cur:
        cur.execute("select display_name from public.profiles where id = %s", (user,))
        assert cur.fetchone()["display_name"] == "hanguyen"


def test_submit_does_not_admit(db):
    """Submitting is asking, not being let in."""
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    with db.cursor() as cur:
        cur.execute("select admitted_at from public.profiles where id = %s", (user,))
        assert cur.fetchone()["admitted_at"] is None


def test_resubmitting_while_pending_updates_in_place(db):
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    second = parse_submission(
        {
            "first_name": "Ha",
            "last_name": "Nguyen",
            "company_name": "VinAI Research",
            "role": "founder",
            "team_size": "6_20",
            "use_case": "Changed my mind.",
            "compute_sources": ["runpod"],
        }
    )
    dbmod.submit_access_request(
        db, user, second, email_domain=None, is_personal_email=None
    )
    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.access_requests where user_id = %s",
            (user,),
        )
        assert cur.fetchone()["n"] == 1
        cur.execute(
            "select use_case, compute_sources from public.access_requests "
            " where user_id = %s",
            (user,),
        )
        row = cur.fetchone()
    assert row["use_case"] == "Changed my mind."
    assert row["compute_sources"] == ["runpod"]


# -- approve ----------------------------------------------------------------

def test_approve_sets_admitted_at_and_records_the_decider(db):
    admin = _user(db, admitted=True)
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    assert dbmod.approve_access_request(db, user, decided_by=admin) is True
    with db.cursor() as cur:
        cur.execute("select admitted_at from public.profiles where id = %s", (user,))
        assert cur.fetchone()["admitted_at"] is not None
        cur.execute(
            "select status, decided_by, decided_at from public.access_requests "
            " where user_id = %s",
            (user,),
        )
        row = cur.fetchone()
    assert row["status"] == "admitted"
    assert str(row["decided_by"]) == admin
    assert row["decided_at"] is not None


def test_approve_materialises_a_banked_workspace_invite(db):
    """The invite redeemed before approval has to actually land, or the
    person is admitted into a console with no pool — which looks exactly
    like the invite never worked."""
    owner = _user(db, admitted=True)
    pool_id = _pool(db, owner)
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    dbmod.record_pending_invite(db, user, pool_id=pool_id, invited_by=owner)

    dbmod.approve_access_request(db, user, decided_by=owner)

    with db.cursor() as cur:
        cur.execute(
            "select 1 from public.pool_members where pool_id = %s and user_id = %s",
            (pool_id, user),
        )
        assert cur.fetchone() is not None


def test_approve_without_a_banked_invite_joins_nothing(db):
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    dbmod.approve_access_request(db, user, decided_by=user)
    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.pool_members where user_id = %s", (user,)
        )
        assert cur.fetchone()["n"] == 0


def test_approving_twice_is_idempotent_not_an_error(db):
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    assert dbmod.approve_access_request(db, user, decided_by=user) is True
    assert dbmod.approve_access_request(db, user, decided_by=user) is False


def test_approve_is_false_for_an_account_that_never_asked(db):
    assert dbmod.approve_access_request(db, _user(db), decided_by=_user(db)) is False


# -- list -------------------------------------------------------------------

def test_list_returns_pending_with_the_email_and_profile_facts(db):
    user = _user(db, email="ha@vinai.io")
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain="vinai.io", is_personal_email=False
    )
    rows = dbmod.list_access_requests(db, status="pending")
    row = next(r for r in rows if str(r["user_id"]) == user)
    assert row["email"] == "ha@vinai.io"
    assert row["first_name"] == "Ha"
    assert row["company_name"] == "VinAI"
    assert row["use_case"] == "Fine-tune across the lab's machines."


def test_list_excludes_decided_requests(db):
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    dbmod.approve_access_request(db, user, decided_by=user)
    assert all(
        str(r["user_id"]) != user for r in dbmod.list_access_requests(db, status="pending")
    )


def test_list_names_the_inviting_pool_when_one_was_banked(db):
    owner = _user(db, admitted=True)
    pool_id = _pool(db, owner)
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    dbmod.record_pending_invite(db, user, pool_id=pool_id, invited_by=owner)
    row = next(
        r for r in dbmod.list_access_requests(db, status="pending")
        if str(r["user_id"]) == user
    )
    assert row["pending_pool_name"] == "Lab"


# -- helpers ----------------------------------------------------------------

def test_email_for_user_reads_auth_users(db):
    assert dbmod.email_for_user(db, _user(db, email="ha@vinai.io")) == "ha@vinai.io"


def test_email_for_user_is_none_when_absent(db):
    assert dbmod.email_for_user(db, _user(db)) is None


def test_profile_is_admin_defaults_false(db):
    assert dbmod.profile_is_admin(db, _user(db)) is False


def test_profile_is_admin_reads_the_column(db):
    user = _user(db)
    with db.cursor() as cur:
        cur.execute("update public.profiles set is_admin = true where id = %s", (user,))
    assert dbmod.profile_is_admin(db, user) is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/api && pytest tests/test_db_access.py -v`
Expected: FAIL — `AttributeError: module 'flashml_cloud_api.db' has no attribute 'access_state_for'`.

- [ ] **Step 3: Write the implementation**

Append to `apps/api/flashml_cloud_api/db.py`, after the profiles section:

```python
# ---------------------------------------------------------------------------
# access requests
#
# `admitted_at` on profiles remains the switch every gate reads. This table
# is the paperwork behind it: who asked, what they said, who decided.
# ---------------------------------------------------------------------------


def access_state_for(db: psycopg.Connection, user_id: str) -> str:
    """``needs_onboarding`` | ``pending`` | ``admitted`` | ``declined``.

    DERIVED, never stored. No row means the form has not been submitted,
    which is why 0009's backfill is load-bearing: without it every
    grandfathered account would compute as ``needs_onboarding`` and be
    shown the form despite already being admitted.
    """
    with db.cursor() as cur:
        cur.execute(
            "select status from public.access_requests where user_id = %s", (user_id,)
        )
        row = cur.fetchone()
    return row["status"] if row else "needs_onboarding"


def email_for_user(db: psycopg.Connection, user_id: str) -> str | None:
    """The signup address, from ``auth.users``.

    Read here rather than from the JWT: the access token's ``email`` claim
    is not guaranteed present, and this API already holds the service-role
    key that can see the table. Never written — that schema is Supabase's.
    """
    with db.cursor() as cur:
        cur.execute("select email from auth.users where id = %s", (user_id,))
        row = cur.fetchone()
    return row["email"] if row else None


def profile_is_admin(db: psycopg.Connection, user_id: str) -> bool:
    with db.cursor() as cur:
        cur.execute("select is_admin from public.profiles where id = %s", (user_id,))
        row = cur.fetchone()
    return bool(row and row["is_admin"])


def submit_access_request(
    db: psycopg.Connection,
    user_id: str,
    submission: "OnboardingSubmission",
    *,
    email_domain: str | None,
    is_personal_email: bool | None,
) -> None:
    """Write the profile facts and create (or update) the pending request.

    One transaction: a profile written without its request row would leave
    the account computing as ``needs_onboarding`` with the form already
    filled, and it would be shown again with everything blank.

    Deliberately does NOT touch ``admitted_at``. Submitting is asking.

    ``display_name`` is only SEEDED — ``coalesce`` leaves a name the user
    chose alone, so filling this form never renames somebody.
    """
    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                """
                insert into public.profiles
                    (id, first_name, last_name, company_name, role, team_size,
                     email_domain, is_personal_email, display_name)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (id) do update
                   set first_name        = excluded.first_name,
                       last_name         = excluded.last_name,
                       company_name      = excluded.company_name,
                       role              = excluded.role,
                       team_size         = excluded.team_size,
                       email_domain      = excluded.email_domain,
                       is_personal_email = excluded.is_personal_email,
                       display_name      = coalesce(public.profiles.display_name,
                                                    excluded.display_name)
                """,
                (
                    user_id,
                    submission.first_name,
                    submission.last_name,
                    submission.company_name,
                    submission.role,
                    submission.team_size,
                    email_domain,
                    is_personal_email,
                    f"{submission.first_name} {submission.last_name}",
                ),
            )
            cur.execute(
                """
                insert into public.access_requests
                    (user_id, status, use_case, compute_sources, heard_from)
                values (%s, 'pending', %s, %s, %s)
                on conflict (user_id) do update
                   set use_case        = excluded.use_case,
                       compute_sources = excluded.compute_sources,
                       heard_from      = excluded.heard_from,
                       requested_at    = now()
                 where public.access_requests.status = 'pending'
                """,
                (
                    user_id,
                    submission.use_case,
                    submission.compute_sources,
                    submission.heard_from,
                ),
            )


def record_pending_invite(
    db: psycopg.Connection, user_id: str, *, pool_id: str, invited_by: str
) -> None:
    """Bank a workspace invite redeemed before approval.

    Creates a stub request if the account has not onboarded yet, so an
    invite clicked before the form is never lost. The stub is still
    ``pending`` — banking an invite is not being admitted.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.access_requests
                (user_id, status, pending_pool_id, invited_by)
            values (%s, 'pending', %s, %s)
            on conflict (user_id) do update
               set pending_pool_id = excluded.pending_pool_id,
                   invited_by      = excluded.invited_by
             where public.access_requests.status = 'pending'
            """,
            (user_id, pool_id, invited_by),
        )


def approve_access_request(
    db: psycopg.Connection, user_id: str, *, decided_by: str
) -> bool:
    """Admit the account and materialise any banked workspace join.

    ONE TRANSACTION, deliberately: an approval that admits but silently
    drops the queued pool join puts the person in a console with no pool,
    which is indistinguishable from the invite never having worked.

    Returns False for an account with no pending request — already decided,
    or never asked — so the route can 404 rather than report a success that
    changed nothing.
    """
    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                """
                update public.access_requests
                   set status = 'admitted', decided_at = now(), decided_by = %s
                 where user_id = %s and status = 'pending'
             returning pending_pool_id
                """,
                (decided_by, user_id),
            )
            row = cur.fetchone()
            if row is None:
                return False

            cur.execute(
                "update public.profiles set admitted_at = coalesce(admitted_at, now()) "
                " where id = %s",
                (user_id,),
            )

            if row["pending_pool_id"] is not None:
                cur.execute(
                    """
                    insert into public.pool_members (pool_id, user_id)
                    values (%s, %s)
                    on conflict (pool_id, user_id) do nothing
                    """,
                    (row["pending_pool_id"], user_id),
                )
    return True


def decline_access_request(
    db: psycopg.Connection, user_id: str, *, decided_by: str
) -> bool:
    """Refuse the request. ``admitted_at`` is left alone rather than
    cleared: this route decides a pending request, and using it to revoke
    an already-admitted account would be a different, unaudited action."""
    with db.cursor() as cur:
        cur.execute(
            """
            update public.access_requests
               set status = 'declined', decided_at = now(), decided_by = %s
             where user_id = %s and status = 'pending'
            """,
            (decided_by, user_id),
        )
        return cur.rowcount == 1


def list_access_requests(
    db: psycopg.Connection, *, status: str = "pending"
) -> list[dict[str, Any]]:
    """The queue. Joins ``auth.users`` for the address — possible only
    because this API holds the service-role key; a browser cannot reach
    that table, which is the entire reason this is a server route."""
    with db.cursor() as cur:
        cur.execute(
            """
            select ar.user_id, ar.status, ar.use_case, ar.compute_sources,
                   ar.heard_from, ar.requested_at, ar.pending_pool_id,
                   ar.invited_by,
                   u.email,
                   p.first_name, p.last_name, p.company_name, p.role,
                   p.team_size, p.email_domain, p.is_personal_email,
                   po.name as pending_pool_name,
                   inv.display_name as invited_by_name
              from public.access_requests ar
              join public.profiles p on p.id = ar.user_id
              left join auth.users u on u.id = ar.user_id
              left join public.pools po on po.id = ar.pending_pool_id
              left join public.profiles inv on inv.id = ar.invited_by
             where ar.status = %s
             order by ar.requested_at
            """,
            (status,),
        )
        return list(cur.fetchall())
```

Add the import at the top of `db.py`, inside a `TYPE_CHECKING` block so the
data layer does not import the validation module at runtime:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flashml_cloud_api.access import OnboardingSubmission
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/api && pytest tests/test_db_access.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/flashml_cloud_api/db.py apps/api/tests/test_db_access.py
git commit -m "feat: add access-request data layer"
```

---

### Task 5: Decouple the workspace invite from admission

This is the behaviour change the whole design turns on, and it touches
shipped code with tests that currently assert the opposite. Both layers move
together because neither is shippable alone.

**Files:**
- Modify: `apps/api/flashml_cloud_api/db.py` — `consume_pool_invite`
- Modify: `apps/api/flashml_cloud_api/app.py` — the `/v1alpha1/invites/accept` route
- Modify: `apps/api/tests/test_db_pools.py` — `test_consume_pool_invite_admits_the_profile` asserts the coupling being removed
- Modify: `apps/api/tests/test_pools_api.py` — any test asserting that accepting an invite admits
- Test: `apps/api/tests/test_invite_decoupling.py`

**Interfaces:**
- Consumes: `record_pending_invite` (Task 4).
- Produces: `consume_pool_invite` returns `{"pool_id", "name", "created_by", "admitted": bool}`; the route returns `{"pool_id", "name", "joined": bool}` where `joined` is False when the caller is not yet admitted.

- [ ] **Step 1: Read the current implementation**

Read `consume_pool_invite` in `apps/api/flashml_cloud_api/db.py` (starts
around line 1135) and the `/v1alpha1/invites/accept` route in `app.py`. Note
exactly where `admitted_at` is set and what the current return shape is —
the existing docstring describes the coupling in detail and must be rewritten,
not left describing behaviour that no longer exists.

- [ ] **Step 2: Write the failing test**

Create `apps/api/tests/test_invite_decoupling.py`:

```python
"""A workspace invite joins a workspace. It does not grant the product.

Until 0009 these were one act — `pool_invites`' own comment said
"Consuming an invite both ADMITS the account through the alpha signup gate
and joins it to the pool". Separating them is the point of this design, so
it gets tests that pin the two apart rather than trusting a code read.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from flashml_cloud_api import db as dbmod
from flashml_cloud_api.access import parse_submission

# `consume_pool_invite` takes a token_hash directly and never hashes
# anything itself, so these tests pass an opaque digest exactly as
# test_db_pools.py already does. There is no `hash_token` helper to import.
def _digest(label: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, label).hex

SUBMISSION = parse_submission(
    {
        "first_name": "Minh", "last_name": "Tran", "company_name": "VinAI",
        "role": "ml_engineer", "team_size": "2_5",
        "use_case": "Join my team's pool.", "compute_sources": ["own_machines"],
    }
)


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def _user(db, *, admitted: bool = False) -> str:
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (%s)", (user_id,))
        cur.execute(
            "insert into public.profiles (id, admitted_at) values (%s, %s)",
            (user_id, datetime.now(timezone.utc) if admitted else None),
        )
    return user_id


def _pool_with_invite(db, owner_id: str, *, token: str, uses: int = 3) -> str:
    pool_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into public.pools (id, name, owner_id) values (%s, %s, %s)",
            (pool_id, "Lab", owner_id),
        )
        cur.execute(
            "insert into public.pool_members (pool_id, user_id) values (%s, %s)",
            (pool_id, owner_id),
        )
    dbmod.create_pool_invite(
        db,
        pool_id=pool_id,
        created_by=owner_id,
        token_hash=_digest(token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        uses=uses,
    )
    return pool_id


def test_redeeming_while_unadmitted_does_not_set_admitted_at(db):
    """THE regression this file exists for."""
    owner = _user(db, admitted=True)
    _pool_with_invite(db, owner, token="fmi_abc")
    newcomer = _user(db)

    dbmod.consume_pool_invite(db, token_hash=_digest("fmi_abc"), user_id=newcomer)

    with db.cursor() as cur:
        cur.execute("select admitted_at from public.profiles where id = %s", (newcomer,))
        assert cur.fetchone()["admitted_at"] is None


def test_redeeming_while_unadmitted_banks_the_pool_instead_of_joining(db):
    owner = _user(db, admitted=True)
    pool_id = _pool_with_invite(db, owner, token="fmi_bank")
    newcomer = _user(db)

    result = dbmod.consume_pool_invite(
        db, token_hash=_digest("fmi_bank"), user_id=newcomer
    )
    assert result is not None
    assert result["admitted"] is False

    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.pool_members "
            " where pool_id = %s and user_id = %s",
            (pool_id, newcomer),
        )
        assert cur.fetchone()["n"] == 0
        cur.execute(
            "select pending_pool_id from public.access_requests where user_id = %s",
            (newcomer,),
        )
        assert str(cur.fetchone()["pending_pool_id"]) == pool_id


def test_an_admitted_account_still_joins_immediately(db):
    """The path that already worked must be untouched."""
    owner = _user(db, admitted=True)
    pool_id = _pool_with_invite(db, owner, token="fmi_now")
    member = _user(db, admitted=True)

    result = dbmod.consume_pool_invite(
        db, token_hash=_digest("fmi_now"), user_id=member
    )
    assert result["admitted"] is True
    with db.cursor() as cur:
        cur.execute(
            "select 1 from public.pool_members where pool_id = %s and user_id = %s",
            (pool_id, member),
        )
        assert cur.fetchone() is not None


def test_approval_after_banking_lands_the_join(db):
    """End to end: invite before approval, then approval, then membership."""
    owner = _user(db, admitted=True)
    pool_id = _pool_with_invite(db, owner, token="fmi_e2e")
    newcomer = _user(db)

    dbmod.submit_access_request(
        db, newcomer, SUBMISSION, email_domain=None, is_personal_email=None
    )
    dbmod.consume_pool_invite(db, token_hash=_digest("fmi_e2e"), user_id=newcomer)
    dbmod.approve_access_request(db, newcomer, decided_by=owner)

    with db.cursor() as cur:
        cur.execute(
            "select 1 from public.pool_members where pool_id = %s and user_id = %s",
            (pool_id, newcomer),
        )
        assert cur.fetchone() is not None


def test_a_use_is_consumed_even_when_the_join_is_only_banked(db):
    """Accepted cost, recorded in the spec: declining that person later
    burns the use. Holding it instead would let one link be claimed by
    unlimited pending accounts."""
    owner = _user(db, admitted=True)
    _pool_with_invite(db, owner, token="fmi_use", uses=1)
    newcomer = _user(db)

    assert dbmod.consume_pool_invite(
        db, token_hash=_digest("fmi_use"), user_id=newcomer
    ) is not None
    # Exhausted now, for everybody.
    assert dbmod.consume_pool_invite(
        db, token_hash=_digest("fmi_use"), user_id=_user(db)
    ) is None
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd apps/api && pytest tests/test_invite_decoupling.py -v`
Expected: FAIL — `admitted_at` is set, and `result` has no `admitted` key.

Also run the existing pool tests and note which now-wrong assertions exist:

Run: `cd apps/api && pytest tests/test_db_pools.py tests/test_pools_api.py -v`

- [ ] **Step 4: Change `consume_pool_invite`**

In `db.py`, keep the decrement exactly as it is (the
`UPDATE ... WHERE ... RETURNING` idiom and the single folded `None` for
unknown/expired/exhausted are both deliberate and stay). Replace the
admission half:

- Remove the `update public.profiles set admitted_at = ...` statement.
- After a successful decrement, read the caller's `admitted_at`.
- If admitted: insert into `pool_members` as today.
- If not: call `record_pending_invite(db, user_id, pool_id=..., invited_by=<invite's created_by>)` instead.
- Return `{"pool_id", "name", "created_by", "admitted"}`.

Rewrite the docstring: it currently states "decrement its use, join the pool,
admit the profile — or refuse all three at once", which stops being true.
Say instead that it decrements the use and either joins or banks, and that
admission is no longer its business.

- [ ] **Step 5: Change the route**

In `app.py`, `/v1alpha1/invites/accept` stays on `current_user` (it must
remain callable by a not-yet-admitted account — that is how an invite gets
banked). Return `{"pool_id", "name", "joined": result["admitted"]}`.

- [ ] **Step 6: Update the tests that asserted the old coupling**

In `test_db_pools.py`, `test_consume_pool_invite_admits_the_profile` asserts
the removed behaviour. Rename it to
`test_consume_pool_invite_no_longer_admits_the_profile` and invert the
assertion, with a comment pointing at
`docs/superpowers/specs/2026-08-04-signup-profile-and-access-requests-design.md`.
Do the same for any assertion in `test_pools_api.py` that an un-admitted
account becomes admitted by accepting.

Also update `_new_user`'s docstring in `test_jobs_from_repo.py:214-226`,
which cites `test_consume_pool_invite_admits_the_profile` by name.

Update the `comment on table public.pool_invites` claim by adding a
correcting comment in migration 0009 (do NOT edit 0007):

```sql
comment on table public.pool_invites is
    'One invite link: sha256 of the raw token (raw returned exactly once, '
    'like machine tokens). Consuming an invite joins the pool, or banks the '
    'join on public.access_requests when the account is not yet admitted. '
    'It no longer admits — that is an admin decision (0009).';
```

Add that statement to the end of `0009_access_requests.sql` **only if 0009
has not yet been applied anywhere**. If it has, add it in a new `0010`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd apps/api && pytest tests/test_invite_decoupling.py tests/test_db_pools.py tests/test_pools_api.py -v`
Expected: PASS.

- [ ] **Step 8: Run the whole API suite**

Run: `cd apps/api && pytest tests/ -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add -A apps/api
git commit -m "refactor: a workspace invite joins a pool, it no longer admits"
```

---

### Task 6: `/me` gains `access`, and `PATCH /me` widens

**Files:**
- Modify: `apps/api/flashml_cloud_api/app.py:856-903`
- Test: `apps/api/tests/test_profile.py`

**Interfaces:**
- Consumes: `access_state_for` (Task 4), `ROLES`/`TEAM_SIZES` (Task 3).
- Produces: `GET /v1alpha1/me` response gains `access: str`; `PATCH /v1alpha1/me` accepts `display_name`, `first_name`, `last_name`, `company_name`, `role`, `team_size`.

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/test_profile.py`:

```python
# -- access state -----------------------------------------------------------

def test_me_reports_needs_onboarding_for_a_fresh_account(make_client, db):
    client = make_client()
    user = _new_user(db, admitted=False)
    assert client.get("/v1alpha1/me", headers=_auth(user)).json()["access"] == (
        "needs_onboarding"
    )


def test_me_keeps_the_admitted_boolean_alongside_access(make_client, db):
    """Additive: `admitted` predates this and other readers rely on it."""
    client = make_client()
    body = client.get("/v1alpha1/me", headers=_auth(_new_user(db))).json()
    assert body["admitted"] is True
    assert body["access"] == "admitted"


def test_me_is_readable_in_every_access_state(make_client, db):
    """The one route an un-admitted account MUST reach — it is how the
    console learns which screen to show instead of the product."""
    client = make_client()
    assert client.get(
        "/v1alpha1/me", headers=_auth(_new_user(db, admitted=False))
    ).status_code == 200


# -- widened PATCH ----------------------------------------------------------

def test_patch_writes_the_profile_fields(make_client, db):
    client = make_client()
    user = _new_user(db)
    r = client.patch(
        "/v1alpha1/me",
        json={
            "first_name": "Ha",
            "last_name": "Nguyen",
            "company_name": "VinAI",
            "role": "researcher",
            "team_size": "2_5",
        },
        headers=_auth(user),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["first_name"] == "Ha"
    assert body["company_name"] == "VinAI"
    assert body["role"] == "researcher"


def test_patch_rejects_an_unknown_role(make_client, db):
    client = make_client()
    r = client.patch(
        "/v1alpha1/me", json={"role": "wizard"}, headers=_auth(_new_user(db))
    )
    assert r.status_code == 400


def test_patch_refuses_to_grant_admin(make_client, db):
    """The escalation this route's shape exists to prevent."""
    client = make_client()
    user = _new_user(db)
    client.patch("/v1alpha1/me", json={"is_admin": True}, headers=_auth(user))
    with db.cursor() as cur:
        cur.execute("select is_admin from public.profiles where id = %s", (user,))
        assert cur.fetchone()["is_admin"] is False


def test_patch_refuses_to_grant_admission(make_client, db):
    client = make_client()
    user = _new_user(db, admitted=False)
    client.patch(
        "/v1alpha1/me", json={"admitted_at": "2020-01-01T00:00:00Z"}, headers=_auth(user)
    )
    assert client.get("/v1alpha1/me", headers=_auth(user)).json()["admitted"] is False


def test_patch_refuses_the_role_flags(make_client, db):
    client = make_client()
    user = _new_user(db)
    client.patch(
        "/v1alpha1/me",
        json={"is_host": True, "is_developer": True, "github_login": "spoofed"},
        headers=_auth(user),
    )
    body = client.get("/v1alpha1/me", headers=_auth(user)).json()
    assert body["is_host"] is False
    assert body["is_developer"] is False
    assert body["github_login"] is None


def test_display_name_still_works_unchanged(make_client, db):
    client = make_client()
    user = _new_user(db)
    r = client.patch(
        "/v1alpha1/me", json={"display_name": "Ada"}, headers=_auth(user)
    )
    assert r.json()["display_name"] == "Ada"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/api && pytest tests/test_profile.py -v`
Expected: FAIL — `KeyError: 'access'`.

- [ ] **Step 3: Implement**

In `app.py`, extend the `me` route:

```python
    @app.get("/v1alpha1/me", tags=["browser"])
    async def me(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        # Additive: every existing key from upsert_profile is unchanged, and
        # this is the one route an un-admitted account MUST be able to
        # read — it is how the console learns which screen to show instead
        # of the product itself.
        profile = _jsonable(dbmod.upsert_profile(db, user_id))
        profile["admitted"] = dbmod.profile_is_admitted(db, user_id)
        # `access` is the four-state version `admitted` cannot express:
        # a signed-in account that has not filled the form is neither
        # admitted nor refused.
        profile["access"] = dbmod.access_state_for(db, user_id)
        return profile
```

Widen `update_me`. Keep the existing `display_name` handling verbatim, then
add the new fields — each optional, each validated, and the privileged keys
still simply never read:

```python
    #: Fields a user owns. Everything absent from this map is either the
    #: identity provider's (email, avatar), written by enrolment
    #: (github_login), or a role rather than a preference (is_host,
    #: is_developer, is_admin, admitted_at). A client handing us one of
    #: those is not rejected with an error naming it; it is never read.
    _PATCHABLE_TEXT = {"first_name": 80, "last_name": 80, "company_name": 160}
    _PATCHABLE_ENUM = {"role": access.ROLES, "team_size": access.TEAM_SIZES}

    @app.patch("/v1alpha1/me", tags=["browser"])
    async def update_me(
        request: Request,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        payload = await _json_object(request)
        fields: dict[str, str] = {}

        raw = payload.get("display_name")
        if raw is not None and not isinstance(raw, str):
            raise HTTPException(
                status_code=400, detail="display_name must be a string or null"
            )
        if isinstance(raw, str):
            name = raw.strip()
            if len(name) > 80:
                raise HTTPException(
                    status_code=400, detail="display_name is limited to 80 characters"
                )
            # An empty string is a user clearing the field, not a request to
            # leave it alone. `upsert_profile` coalesces null to "keep the
            # existing value", so an empty submission has to be rejected
            # rather than silently doing nothing the user can see.
            if name == "":
                raise HTTPException(
                    status_code=400, detail="display_name cannot be empty"
                )
            fields["display_name"] = name

        for field, cap in _PATCHABLE_TEXT.items():
            value = payload.get(field)
            if value is None:
                continue
            if not isinstance(value, str):
                raise HTTPException(status_code=400, detail=f"{field} must be a string")
            trimmed = value.strip()
            if not trimmed:
                raise HTTPException(status_code=400, detail=f"{field} cannot be empty")
            if len(trimmed) > cap:
                raise HTTPException(
                    status_code=400, detail=f"{field} is limited to {cap} characters"
                )
            fields[field] = trimmed

        for field, allowed in _PATCHABLE_ENUM.items():
            value = payload.get(field)
            if value is None:
                continue
            if value not in allowed:
                raise HTTPException(
                    status_code=400,
                    detail=f"{field} must be one of: {', '.join(sorted(allowed))}",
                )
            fields[field] = value

        return _jsonable(dbmod.update_profile_fields(db, user_id, **fields))
```

Add `update_profile_fields` to `db.py` next to `upsert_profile`:

```python
def update_profile_fields(
    db: psycopg.Connection, user_id: str, **fields: str
) -> dict[str, Any]:
    """Set exactly the named columns and return the whole row.

    The caller decides which fields are writable; this refuses to be a
    generic column setter by whitelisting here as well, so a future caller
    cannot turn it into one by accident.
    """
    allowed = {
        "display_name", "first_name", "last_name", "company_name",
        "role", "team_size",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"not a writable profile field: {', '.join(sorted(unknown))}")
    if not fields:
        return upsert_profile(db, user_id)

    assignments = ", ".join(f"{name} = %s" for name in fields)
    with db.cursor() as cur:
        upsert_profile(db, user_id)  # guarantee the row exists
        cur.execute(
            f"""
            update public.profiles set {assignments}
             where id = %s
         returning id, display_name, github_login, is_host, is_developer,
                   created_at, first_name, last_name, company_name, role,
                   team_size, email_domain, is_personal_email
            """,
            (*fields.values(), user_id),
        )
        return cur.fetchone()
```

Note: `assignments` is built from the whitelisted keys only — never from
user input — so the f-string cannot carry injected SQL. Values stay
parameterised.

Import `access` at the top of `app.py`: `from flashml_cloud_api import access`.

`upsert_profile`'s `returning` list must also grow to include the new
columns, or `GET /me` will not expose them.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/api && pytest tests/test_profile.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/flashml_cloud_api/app.py apps/api/flashml_cloud_api/db.py apps/api/tests/test_profile.py
git commit -m "feat: expose access state and widen the profile PATCH"
```

---

### Task 7: `POST /v1alpha1/access-request`

**Files:**
- Modify: `apps/api/flashml_cloud_api/app.py` (after the `/me` routes)
- Test: `apps/api/tests/test_access_request_api.py`

**Interfaces:**
- Consumes: `parse_submission` (Task 3), `derive_email_facts` (Task 2), `submit_access_request`, `email_for_user`, `access_state_for` (Task 4).
- Produces: `POST /v1alpha1/access-request` → `200 {"access": "pending"}`; `400` on validation failure; `409` when already decided.

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_access_request_api.py`:

```python
"""POST /v1alpha1/access-request — the onboarding form.

Sits on `current_user`, never `admitted_user`: an account that has not been
admitted is exactly who submits this. Gating it behind admission would make
the only route into the product require having already passed it.
"""
from __future__ import annotations

from test_jobs_from_repo import (  # noqa: F401 - fixtures
    _jwt, _new_user, db, make_client, settings, transport,
)

VALID = {
    "first_name": "Ha",
    "last_name": "Nguyen",
    "company_name": "VinAI",
    "role": "researcher",
    "team_size": "2_5",
    "use_case": "Fine-tune a 7B model across the lab's machines.",
    "compute_sources": ["own_machines", "colab"],
    "heard_from": "github",
}


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_jwt(user_id)}"}


def test_submitting_moves_the_account_to_pending(make_client, db):
    client = make_client()
    user = _new_user(db, admitted=False)
    r = client.post("/v1alpha1/access-request", json=VALID, headers=_auth(user))
    assert r.status_code == 200, r.text
    assert client.get("/v1alpha1/me", headers=_auth(user)).json()["access"] == "pending"


def test_submitting_does_not_admit(make_client, db):
    client = make_client()
    user = _new_user(db, admitted=False)
    client.post("/v1alpha1/access-request", json=VALID, headers=_auth(user))
    assert client.get("/v1alpha1/me", headers=_auth(user)).json()["admitted"] is False


def test_email_domain_is_derived_server_side(make_client, db):
    client = make_client()
    user = _new_user(db, admitted=False)
    with db.cursor() as cur:
        cur.execute(
            "update auth.users set email = %s where id = %s", ("ha@vinai.io", user)
        )
    client.post("/v1alpha1/access-request", json=VALID, headers=_auth(user))
    with db.cursor() as cur:
        cur.execute(
            "select email_domain, is_personal_email from public.profiles where id = %s",
            (user,),
        )
        row = cur.fetchone()
    assert row["email_domain"] == "vinai.io"
    assert row["is_personal_email"] is False


def test_a_client_supplied_email_domain_is_ignored(make_client, db):
    """The domain is a derived fact, not a claim. Accepting it from the body
    would let anyone label themselves as any company."""
    client = make_client()
    user = _new_user(db, admitted=False)
    with db.cursor() as cur:
        cur.execute(
            "update auth.users set email = %s where id = %s",
            ("minh@gmail.com", user),
        )
    client.post(
        "/v1alpha1/access-request",
        json={**VALID, "email_domain": "openai.com", "is_personal_email": False},
        headers=_auth(user),
    )
    with db.cursor() as cur:
        cur.execute("select email_domain from public.profiles where id = %s", (user,))
        assert cur.fetchone()["email_domain"] == "gmail.com"


def test_validation_failure_is_a_400_naming_the_field(make_client, db):
    client = make_client()
    r = client.post(
        "/v1alpha1/access-request",
        json={**VALID, "role": "wizard"},
        headers=_auth(_new_user(db, admitted=False)),
    )
    assert r.status_code == 400
    assert "role" in r.json()["detail"]


def test_resubmitting_while_pending_is_allowed(make_client, db):
    client = make_client()
    user = _new_user(db, admitted=False)
    client.post("/v1alpha1/access-request", json=VALID, headers=_auth(user))
    r = client.post(
        "/v1alpha1/access-request",
        json={**VALID, "company_name": "VinAI Research"},
        headers=_auth(user),
    )
    assert r.status_code == 200


def test_submitting_after_a_decision_is_a_409(make_client, db):
    """An admitted account edits its profile through PATCH /me. Letting it
    re-submit here would silently reset a decided request to pending."""
    client = make_client()
    user = _new_user(db)  # admitted, so backfilled/decided
    r = client.post("/v1alpha1/access-request", json=VALID, headers=_auth(user))
    assert r.status_code == 409


def test_requires_a_session(make_client, db):
    assert make_client().post("/v1alpha1/access-request", json=VALID).status_code == 401
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/api && pytest tests/test_access_request_api.py -v`
Expected: FAIL — 404, the route does not exist.

- [ ] **Step 3: Implement the route**

In `app.py`, after the `/me` routes:

```python
    # `current_user`, not `admitted_user`: this route is how an un-admitted
    # account asks to be admitted. Gating it behind admission would make the
    # only way in require already being in.
    @app.post("/v1alpha1/access-request", tags=["browser"])
    async def create_access_request(
        request: Request,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        state = dbmod.access_state_for(db, user_id)
        if state in ("admitted", "declined"):
            # Re-submitting after a decision would reset it to pending —
            # silently un-deciding something an admin decided. An admitted
            # account edits these fields through PATCH /v1alpha1/me.
            raise HTTPException(
                status_code=409, detail="this account's access is already decided"
            )

        payload = await _json_object(request)
        try:
            submission = access.parse_submission(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

        # Derived, never accepted from the body: the domain is a fact about
        # the verified signup address, not a claim the client gets to make.
        domain, personal = derive_email_facts(dbmod.email_for_user(db, user_id))

        dbmod.upsert_profile(db, user_id)  # the FK target must exist
        dbmod.submit_access_request(
            db, user_id, submission,
            email_domain=domain, is_personal_email=personal,
        )
        return {"access": dbmod.access_state_for(db, user_id)}
```

Import at the top of `app.py`: `from flashml_cloud_api.emails import derive_email_facts`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/api && pytest tests/test_access_request_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/flashml_cloud_api/app.py apps/api/tests/test_access_request_api.py
git commit -m "feat: accept onboarding submissions as access requests"
```

---

### Task 8: The admin dependency and the queue routes

**Files:**
- Modify: `apps/api/flashml_cloud_api/app.py` (dependency beside `admitted_user` at line 696; routes after the access-request route)
- Test: `apps/api/tests/test_admin_access_api.py`

**Interfaces:**
- Consumes: `profile_is_admin`, `list_access_requests`, `approve_access_request`, `decline_access_request` (Task 4).
- Produces: `admin_user` dependency; `GET /v1alpha1/admin/access-requests?status=pending`; `POST /v1alpha1/admin/access-requests/{user_id}/approve`; `.../decline`.

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_admin_access_api.py`:

```python
"""The access-request queue.

The 403 tests here are the ones that matter: this is the only surface that
grants product access, so "a non-admin cannot reach it" is the assertion
that stops the feature becoming a privilege-escalation bug.
"""
from __future__ import annotations

from test_jobs_from_repo import (  # noqa: F401 - fixtures
    _jwt, _new_user, db, make_client, settings, transport,
)

VALID = {
    "first_name": "Ha", "last_name": "Nguyen", "company_name": "VinAI",
    "role": "researcher", "team_size": "2_5",
    "use_case": "Fine-tune across the lab.", "compute_sources": ["own_machines"],
}


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_jwt(user_id)}"}


def _admin(db) -> str:
    user = _new_user(db)
    with db.cursor() as cur:
        cur.execute("update public.profiles set is_admin = true where id = %s", (user,))
    return user


def _pending(client, db) -> str:
    user = _new_user(db, admitted=False)
    client.post("/v1alpha1/access-request", json=VALID, headers=_auth(user))
    return user


# -- authorization ----------------------------------------------------------

def test_a_plain_user_cannot_list_the_queue(make_client, db):
    client = make_client()
    r = client.get("/v1alpha1/admin/access-requests", headers=_auth(_new_user(db)))
    assert r.status_code == 403


def test_a_plain_user_cannot_approve(make_client, db):
    """The escalation path: approving yourself."""
    client = make_client()
    attacker = _new_user(db, admitted=False)
    client.post("/v1alpha1/access-request", json=VALID, headers=_auth(attacker))
    r = client.post(
        f"/v1alpha1/admin/access-requests/{attacker}/approve", headers=_auth(attacker)
    )
    assert r.status_code == 403
    assert client.get("/v1alpha1/me", headers=_auth(attacker)).json()["admitted"] is False


def test_a_plain_user_cannot_decline(make_client, db):
    client = make_client()
    victim = _pending(client, db)
    r = client.post(
        f"/v1alpha1/admin/access-requests/{victim}/decline",
        headers=_auth(_new_user(db)),
    )
    assert r.status_code == 403


def test_the_queue_requires_a_session(make_client, db):
    assert make_client().get("/v1alpha1/admin/access-requests").status_code == 401


# -- the queue --------------------------------------------------------------

def test_an_admin_sees_pending_requests(make_client, db):
    client = make_client()
    admin = _admin(db)
    user = _pending(client, db)
    rows = client.get(
        "/v1alpha1/admin/access-requests", headers=_auth(admin)
    ).json()
    assert any(r["user_id"] == user for r in rows)


def test_approve_admits_and_removes_from_the_queue(make_client, db):
    client = make_client()
    admin = _admin(db)
    user = _pending(client, db)

    r = client.post(
        f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin)
    )
    assert r.status_code == 200, r.text

    assert client.get("/v1alpha1/me", headers=_auth(user)).json()["access"] == "admitted"
    rows = client.get("/v1alpha1/admin/access-requests", headers=_auth(admin)).json()
    assert all(row["user_id"] != user for row in rows)


def test_decline_sets_declined_without_admitting(make_client, db):
    client = make_client()
    admin = _admin(db)
    user = _pending(client, db)

    client.post(f"/v1alpha1/admin/access-requests/{user}/decline", headers=_auth(admin))

    body = client.get("/v1alpha1/me", headers=_auth(user)).json()
    assert body["access"] == "declined"
    assert body["admitted"] is False


def test_approving_an_unknown_user_is_a_404(make_client, db):
    client = make_client()
    r = client.post(
        "/v1alpha1/admin/access-requests/"
        "00000000-0000-0000-0000-000000000000/approve",
        headers=_auth(_admin(db)),
    )
    assert r.status_code == 404


def test_approving_twice_is_a_404_the_second_time(make_client, db):
    """Reports honestly that nothing changed rather than a success that
    did nothing."""
    client = make_client()
    admin = _admin(db)
    user = _pending(client, db)
    assert client.post(
        f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin)
    ).status_code == 200
    assert client.post(
        f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin)
    ).status_code == 404


def test_a_malformed_user_id_is_rejected(make_client, db):
    client = make_client()
    r = client.post(
        "/v1alpha1/admin/access-requests/not-a-uuid/approve", headers=_auth(_admin(db))
    )
    assert r.status_code in (400, 422)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/api && pytest tests/test_admin_access_api.py -v`
Expected: FAIL — 404, routes do not exist.

- [ ] **Step 3: Add the dependency**

In `app.py`, beside `admitted_user`:

```python
    def admin_user(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ) -> str:
        """current_user plus the admin flag. 403, not 404, for the same
        reason `admitted_user` gives: unlike a resource id, the gate's
        existence is not a secret.

        `is_admin` has no granting route anywhere in this API, deliberately.
        It is set with one UPDATE against the owner's own row.
        """
        if not dbmod.profile_is_admin(db, user_id):
            raise HTTPException(status_code=403, detail="admin required")
        return user_id
```

- [ ] **Step 4: Add the routes**

```python
    @app.get("/v1alpha1/admin/access-requests", tags=["admin"])
    async def list_requests(
        status: str = "pending",
        _admin: str = Depends(admin_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        if status not in ("pending", "admitted", "declined"):
            raise HTTPException(status_code=400, detail="unknown status")
        return [_jsonable(r) for r in dbmod.list_access_requests(db, status=status)]

    @app.post("/v1alpha1/admin/access-requests/{user_id}/approve", tags=["admin"])
    async def approve_request(
        user_id: str,
        admin_id: str = Depends(admin_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        _uuid_or_400(user_id)
        # 404, not 200, when nothing was pending: reporting success for a
        # call that changed nothing is how a queue silently stops working.
        if not dbmod.approve_access_request(db, user_id, decided_by=admin_id):
            raise HTTPException(status_code=404, detail="no pending request")
        return {"user_id": user_id, "status": "admitted"}

    @app.post("/v1alpha1/admin/access-requests/{user_id}/decline", tags=["admin"])
    async def decline_request(
        user_id: str,
        admin_id: str = Depends(admin_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        _uuid_or_400(user_id)
        if not dbmod.decline_access_request(db, user_id, decided_by=admin_id):
            raise HTTPException(status_code=404, detail="no pending request")
        return {"user_id": user_id, "status": "declined"}
```

Add the helper beside the other validators in `app.py`:

```python
def _uuid_or_400(value: str) -> str:
    """A path segment that reaches a WHERE clause. psycopg parameterises it
    safely, but a malformed uuid raises a DataError that would surface as a
    500 — a 400 is the honest answer."""
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="invalid user id") from None
    return value
```

Ensure `import uuid` is present at the top of `app.py`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd apps/api && pytest tests/test_admin_access_api.py -v`
Expected: PASS.

- [ ] **Step 6: Run the whole API suite**

Run: `cd apps/api && pytest tests/ -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/api/flashml_cloud_api/app.py apps/api/tests/test_admin_access_api.py
git commit -m "feat: add the admin access-request queue"
```

---

### Task 9: Web API client

**Files:**
- Modify: `apps/web/lib/cloud-api.ts`
- Test: `apps/web/lib/cloud-api.test.ts`

**Interfaces:**
- Consumes: the API routes from Tasks 6–8.
- Produces:
  - `type AccessState = "needs_onboarding" | "pending" | "admitted" | "declined"`
  - `Profile` gains `access: AccessState`, `first_name`, `last_name`, `company_name`, `role`, `team_size` (all `string | null`)
  - `interface OnboardingSubmission { first_name; last_name; company_name; role; team_size; use_case; compute_sources: string[]; heard_from?: string }`
  - `interface AccessRequestRow { user_id; email: string | null; first_name; last_name; company_name; role; team_size; email_domain; is_personal_email; use_case; compute_sources: string[]; heard_from; requested_at; pending_pool_name: string | null; invited_by_name: string | null }`
  - `submitAccessRequest(body: OnboardingSubmission): Promise<{ access: AccessState }>`
  - `listAccessRequests(status?: string): Promise<AccessRequestRow[]>`
  - `approveAccessRequest(userId: string): Promise<void>`
  - `declineAccessRequest(userId: string): Promise<void>`
  - `updateProfile(fields: Partial<...>): Promise<Profile>` alongside the existing `updateMe`
  - `acceptInvite` return type becomes `{ pool_id: string; name: string; joined: boolean }`

- [ ] **Step 1: Write the failing test**

Append to `apps/web/lib/cloud-api.test.ts` (add the new names to the existing import block at the top):

```typescript
describe("access requests", () => {
  it("POSTs the onboarding submission to /v1alpha1/access-request", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { access: "pending" }));
    const result = await submitAccessRequest({
      first_name: "Ha",
      last_name: "Nguyen",
      company_name: "VinAI",
      role: "researcher",
      team_size: "2_5",
      use_case: "Fine-tune across the lab.",
      compute_sources: ["own_machines"],
    });
    expect(result.access).toBe("pending");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/v1alpha1/access-request");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string).company_name).toBe("VinAI");
  });

  it("surfaces a 409 as an ApiError carrying the API's detail", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(409, { detail: "this account's access is already decided" })
    );
    await expect(
      submitAccessRequest({
        first_name: "Ha",
        last_name: "Nguyen",
        company_name: "VinAI",
        role: "researcher",
        team_size: "2_5",
        use_case: "x",
        compute_sources: [],
      })
    ).rejects.toBeInstanceOf(ApiError);
  });

  it("lists the queue and passes the status through as a query param", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, []));
    await listAccessRequests("declined");
    expect(fetchMock.mock.calls[0][0]).toContain("status=declined");
  });

  it("POSTs to the approve route with the user id encoded", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { status: "admitted" }));
    await approveAccessRequest("a b/c");
    expect(fetchMock.mock.calls[0][0]).toContain(encodeURIComponent("a b/c"));
    expect(fetchMock.mock.calls[0][0]).toContain("/approve");
  });

  it("POSTs to the decline route", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { status: "declined" }));
    await declineAccessRequest("u1");
    expect(fetchMock.mock.calls[0][0]).toContain("/decline");
  });
});

describe("acceptInvite after decoupling", () => {
  it("reports joined:false when the account is not yet admitted", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { pool_id: "p1", name: "Lab", joined: false })
    );
    const result = await acceptInvite("fmi_abc");
    expect(result.joined).toBe(false);
    expect(result.name).toBe("Lab");
  });
});

describe("updateProfile", () => {
  it("PATCHes only the fields it is given", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(200, { id: "u1", first_name: "Ha", company_name: "VinAI" })
    );
    await updateProfile({ first_name: "Ha", company_name: "VinAI" });
    const [, init] = fetchMock.mock.calls[0];
    expect(init.method).toBe("PATCH");
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({ first_name: "Ha", company_name: "VinAI" });
    expect(body).not.toHaveProperty("is_admin");
  });
});
```

Use whatever the file already names its fetch mock — read the existing
`beforeEach` block and match it rather than introducing a second mock.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/web && npm test -- cloud-api`
Expected: FAIL — the new exports do not exist.

- [ ] **Step 3: Implement**

Extend the `Profile` interface:

```typescript
/** The four-state access model. `admitted` alone could not express a
 * signed-in account that has not filled the onboarding form: it is
 * neither admitted nor refused. */
export type AccessState =
  | "needs_onboarding"
  | "pending"
  | "admitted"
  | "declined";

export interface Profile {
  id: string;
  display_name: string | null;
  github_login: string | null;
  is_host: boolean;
  is_developer: boolean;
  created_at: string;
  /** Kept alongside `access` rather than replaced: it predates this and
   * other readers rely on it. */
  admitted: boolean;
  access: AccessState;
  first_name: string | null;
  last_name: string | null;
  company_name: string | null;
  role: string | null;
  team_size: string | null;
}
```

Then the request functions, following the file's existing style — one
thin function per route, with a comment where the route's behaviour is
not obvious from its name:

```typescript
// -- onboarding and access ---------------------------------------------

export interface OnboardingSubmission {
  first_name: string;
  last_name: string;
  company_name: string;
  role: string;
  team_size: string;
  use_case: string;
  compute_sources: string[];
  heard_from?: string;
}

/** `POST /v1alpha1/access-request` — callable by a not-yet-admitted
 * account on purpose: this IS how an account asks to be admitted. A 409
 * means the request was already decided; an admitted user edits these
 * fields through `updateProfile` instead. */
export function submitAccessRequest(
  body: OnboardingSubmission
): Promise<{ access: AccessState }> {
  return request<{ access: AccessState }>("/v1alpha1/access-request", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export interface AccessRequestRow {
  user_id: string;
  email: string | null;
  first_name: string | null;
  last_name: string | null;
  company_name: string | null;
  role: string | null;
  team_size: string | null;
  email_domain: string | null;
  is_personal_email: boolean | null;
  use_case: string | null;
  compute_sources: string[];
  heard_from: string | null;
  requested_at: string;
  pending_pool_name: string | null;
  invited_by_name: string | null;
}

/** Admin only — a non-admin gets a plain `ApiError` carrying the API's
 * "admin required", not a `NotAuthenticated`. */
export function listAccessRequests(
  status: string = "pending"
): Promise<AccessRequestRow[]> {
  return request<AccessRequestRow[]>(
    `/v1alpha1/admin/access-requests?status=${encodeURIComponent(status)}`
  );
}

export function approveAccessRequest(userId: string): Promise<void> {
  return request<void>(
    `/v1alpha1/admin/access-requests/${encodeURIComponent(userId)}/approve`,
    { method: "POST" }
  );
}

export function declineAccessRequest(userId: string): Promise<void> {
  return request<void>(
    `/v1alpha1/admin/access-requests/${encodeURIComponent(userId)}/decline`,
    { method: "POST" }
  );
}

/** `PATCH /v1alpha1/me` with the profile fields. `updateMe` (display name
 * only) stays for its existing callers. */
export function updateProfile(
  fields: Partial<
    Pick<
      Profile,
      "display_name" | "first_name" | "last_name" | "company_name" | "role" | "team_size"
    >
  >
): Promise<Profile> {
  return request<Profile>("/v1alpha1/me", {
    method: "PATCH",
    body: JSON.stringify(fields),
  });
}
```

Update `acceptInvite`'s return type to `{ pool_id: string; name: string; joined: boolean }`
and rewrite its docstring — the current one says "this call IS the admission
bootstrap", which stops being true.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/web && npm test -- cloud-api`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/lib/cloud-api.ts apps/web/lib/cloud-api.test.ts
git commit -m "feat: add access-request client functions"
```

---

### Task 10: The onboarding form

**Files:**
- Create: `apps/web/components/onboarding/OnboardingForm.tsx`
- Create: `apps/web/lib/onboarding-options.ts`
- Test: `apps/web/lib/onboarding-options.test.ts`

**Interfaces:**
- Consumes: `submitAccessRequest`, `OnboardingSubmission` (Task 9).
- Produces: `<OnboardingForm onSubmitted={() => void} />`; `ROLE_OPTIONS`, `TEAM_SIZE_OPTIONS`, `COMPUTE_OPTIONS`, `HEARD_FROM_OPTIONS` as `{value, label}[]`; `isComplete(draft) -> boolean`.

- [ ] **Step 1: Write the failing test**

Create `apps/web/lib/onboarding-options.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import {
  COMPUTE_OPTIONS,
  HEARD_FROM_OPTIONS,
  ROLE_OPTIONS,
  TEAM_SIZE_OPTIONS,
  isComplete,
} from "./onboarding-options";

// These values are a contract with the API's enumerations. A label typo is
// cosmetic; a VALUE typo is a 400 the user cannot fix, so the values are
// pinned here rather than trusted to review.
describe("option values match the API enumerations", () => {
  it("roles", () => {
    expect(ROLE_OPTIONS.map((o) => o.value)).toEqual([
      "researcher",
      "ml_engineer",
      "student",
      "founder",
      "other",
    ]);
  });

  it("team sizes", () => {
    expect(TEAM_SIZE_OPTIONS.map((o) => o.value)).toEqual([
      "solo",
      "2_5",
      "6_20",
      "20_plus",
    ]);
  });

  it("compute sources", () => {
    expect(COMPUTE_OPTIONS.map((o) => o.value)).toEqual([
      "own_machines",
      "colab",
      "runpod",
      "cloud",
      "none",
    ]);
  });

  it("heard-from values are a subset of what the API accepts", () => {
    const allowed = new Set([
      "github", "search", "twitter", "friend", "paper", "event", "other",
    ]);
    for (const o of HEARD_FROM_OPTIONS) expect(allowed.has(o.value)).toBe(true);
  });

  it("every option has a human label", () => {
    for (const o of [
      ...ROLE_OPTIONS, ...TEAM_SIZE_OPTIONS, ...COMPUTE_OPTIONS, ...HEARD_FROM_OPTIONS,
    ]) {
      expect(o.label.trim().length).toBeGreaterThan(0);
    }
  });
});

describe("isComplete", () => {
  const full = {
    first_name: "Ha",
    last_name: "Nguyen",
    company_name: "VinAI",
    role: "researcher",
    team_size: "2_5",
    use_case: "Fine-tune across the lab.",
    compute_sources: ["own_machines"],
    heard_from: "github",
  };

  it("accepts a complete draft", () => {
    expect(isComplete(full)).toBe(true);
  });

  it("compute_sources may be empty — the API allows it", () => {
    expect(isComplete({ ...full, compute_sources: [] })).toBe(true);
  });

  it("heard_from is optional", () => {
    expect(isComplete({ ...full, heard_from: "" })).toBe(true);
  });

  it.each(["first_name", "last_name", "company_name", "use_case"])(
    "requires %s",
    (field) => {
      expect(isComplete({ ...full, [field]: "   " })).toBe(false);
    }
  );

  it.each(["role", "team_size"])("requires %s to be chosen", (field) => {
    expect(isComplete({ ...full, [field]: "" })).toBe(false);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/web && npm test -- onboarding-options`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the options module**

Create `apps/web/lib/onboarding-options.ts`:

```typescript
/** Option values are a contract with the API's enumerations in
 * `flashml_cloud_api/access.py`. A label is cosmetic; a VALUE typo is a
 * 400 the user has no way to fix, which is why they are pinned by test. */

export interface Option {
  value: string;
  label: string;
}

export const ROLE_OPTIONS: Option[] = [
  { value: "researcher", label: "Researcher" },
  { value: "ml_engineer", label: "ML engineer" },
  { value: "student", label: "Student" },
  { value: "founder", label: "Founder" },
  { value: "other", label: "Something else" },
];

export const TEAM_SIZE_OPTIONS: Option[] = [
  { value: "solo", label: "Just me" },
  { value: "2_5", label: "2–5 people" },
  { value: "6_20", label: "6–20 people" },
  { value: "20_plus", label: "More than 20" },
];

export const COMPUTE_OPTIONS: Option[] = [
  { value: "own_machines", label: "My own machines" },
  { value: "colab", label: "Google Colab" },
  { value: "runpod", label: "RunPod" },
  { value: "cloud", label: "Cloud (AWS, GCP, Azure)" },
  { value: "none", label: "Nothing yet" },
];

export const HEARD_FROM_OPTIONS: Option[] = [
  { value: "github", label: "GitHub" },
  { value: "search", label: "Search" },
  { value: "twitter", label: "X / Twitter" },
  { value: "friend", label: "From someone I know" },
  { value: "paper", label: "A paper or article" },
  { value: "event", label: "An event" },
  { value: "other", label: "Somewhere else" },
];

export interface OnboardingDraft {
  first_name: string;
  last_name: string;
  company_name: string;
  role: string;
  team_size: string;
  use_case: string;
  compute_sources: string[];
  heard_from: string;
}

export const EMPTY_DRAFT: OnboardingDraft = {
  first_name: "",
  last_name: "",
  company_name: "",
  role: "",
  team_size: "",
  use_case: "",
  compute_sources: [],
  heard_from: "",
};

/** Mirrors the API's own rules: four required text fields, two required
 * choices, and `compute_sources` / `heard_from` genuinely optional.
 * Client-side only — the API validates independently and is the authority. */
export function isComplete(draft: OnboardingDraft): boolean {
  const filled = (v: string) => v.trim().length > 0;
  return (
    filled(draft.first_name) &&
    filled(draft.last_name) &&
    filled(draft.company_name) &&
    filled(draft.use_case) &&
    draft.role !== "" &&
    draft.team_size !== ""
  );
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/web && npm test -- onboarding-options`
Expected: PASS.

- [ ] **Step 5: Build the form component**

Create `apps/web/components/onboarding/OnboardingForm.tsx`. Requirements:

- `"use client"` at the top.
- Local `OnboardingDraft` state seeded from `EMPTY_DRAFT`.
- Reuses `@/components/ui/input`, `@/components/ui/label`, `@/components/ui/button` and the existing `panel` / `glass` classes — match `SignInCard.tsx` and the account page rather than inventing styling.
- Submit disabled unless `isComplete(draft)` and not already submitting.
- On submit calls `submitAccessRequest`, then `onSubmitted()`.
- On `ApiError` shows `err.detail` inline in an alert styled like `SignInCard`'s error block (`role="alert"`, destructive border/background).
- On `NotAuthenticated` redirects to `/sign-in?next=/overview`.
- Compute sources render as checkboxes; toggling appends/removes from the array.
- Every input has a matching `<Label htmlFor>`, and the free-text field is a `<textarea>` with a visible character counter against 2000.

Copy for the heading and lede:

```
Tell us about you
FlashML is a small alpha. A human reads every request — this is what they read.
```

Field labels, in order: `First name`, `Last name`,
`Company, lab, or university`, `Your role`, `Team size`,
`What do you want to run on FlashML?`, `Where's your compute?` (help text:
`Check everything you have access to.`), `How did you hear about FlashML?`
(help text: `Optional.`).

- [ ] **Step 6: Verify it compiles and lints**

Run: `cd apps/web && npx tsc --noEmit && npm run lint`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add apps/web/lib/onboarding-options.ts apps/web/lib/onboarding-options.test.ts apps/web/components/onboarding/OnboardingForm.tsx
git commit -m "feat: add the onboarding form"
```

---

### Task 11: Console shell switches on access state

**Files:**
- Modify: `apps/web/components/shell/ConsoleShell.tsx:121-170,294`
- Create: `apps/web/components/onboarding/PendingScreen.tsx`
- Create: `apps/web/components/onboarding/DeclinedScreen.tsx`
- Delete: `apps/web/components/shell/InviteGate.tsx`
- Create: `apps/web/lib/access-screen.ts`
- Test: `apps/web/lib/access-screen.test.ts`

**Interfaces:**
- Consumes: `AccessState` (Task 9), `OnboardingForm` (Task 10).
- Produces: `screenFor(access: AccessState | undefined, pathname: string) -> "console" | "onboarding" | "pending" | "declined"`.

- [ ] **Step 1: Write the failing test**

Create `apps/web/lib/access-screen.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { INVITE_ROUTE, screenFor } from "./access-screen";

describe("screenFor", () => {
  it("renders the console while the state is still unknown", () => {
    // The shell mounts once per console session. Showing a loading state on
    // every first paint would punish the overwhelming majority — already
    // -admitted returning users — for one round trip. Nothing this guards
    // is enforced only here; every state-creating route re-checks
    // server-side.
    expect(screenFor(undefined, "/overview")).toBe("console");
  });

  it("routes each state to its screen", () => {
    expect(screenFor("admitted", "/overview")).toBe("console");
    expect(screenFor("needs_onboarding", "/overview")).toBe("onboarding");
    expect(screenFor("pending", "/overview")).toBe("pending");
    expect(screenFor("declined", "/overview")).toBe("declined");
  });

  it("lets every state reach /pools/join", () => {
    // This is how an invite survives the wait: a pending account must be
    // able to redeem a link so the join is banked and applied on approval.
    for (const state of ["needs_onboarding", "pending", "declined"] as const) {
      expect(screenFor(state, INVITE_ROUTE)).toBe("console");
    }
  });

  it("does not treat a route merely starting with the invite path as the invite route", () => {
    expect(screenFor("pending", "/pools/joinery")).toBe("pending");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/web && npm test -- access-screen`
Expected: FAIL — module not found.

- [ ] **Step 3: Write the module**

Create `apps/web/lib/access-screen.ts`:

```typescript
import type { AccessState } from "@/lib/cloud-api";

/** The one console route every access state must reach. Redeeming a link
 * while un-admitted banks the workspace join so it applies on approval;
 * blocking it here would lose the invite. The API's `accept_invite` sits
 * on `current_user`, not `admitted_user`, for the identical reason. */
export const INVITE_ROUTE = "/pools/join";

export type Screen = "console" | "onboarding" | "pending" | "declined";

/** `undefined` means `GET /me` has not answered yet and renders the
 * console optimistically — see the test for why. */
export function screenFor(
  access: AccessState | undefined,
  pathname: string
): Screen {
  if (pathname === INVITE_ROUTE) return "console";
  switch (access) {
    case "needs_onboarding":
      return "onboarding";
    case "pending":
      return "pending";
    case "declined":
      return "declined";
    default:
      return "console";
  }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/web && npm test -- access-screen`
Expected: PASS.

- [ ] **Step 5: Build the two screens**

`PendingScreen.tsx` — `"use client"`, centred card matching `InviteGate`'s
layout (`flex min-h-[calc(100dvh-3.5rem)] items-center justify-center`).
It takes `email: string | null`. Copy, exactly:

```
Request received
A human reads every request — FlashML is a small alpha, not an automated signup.
We'll get back to you at {email ?? "the address you signed up with"}.
Already approved? Reload this page.
```

It must NOT claim an email is on its way automatically. Nothing sends one:
this deployment has no email provider, which is the same constraint that
removed magic links. A "check your inbox" line here would be the exact
failure `SignInCard` documents at length.

Include a reload button calling `window.location.reload()`.

`DeclinedScreen.tsx` — same layout. Copy:

```
Not right now
Your request wasn't approved for this alpha. That's a capacity decision, not a permanent one.
```

Read `email` for the pending screen from `useSessionUser()` in
`@/lib/session-user`, which already exposes it.

- [ ] **Step 6: Rewire `ConsoleShell`**

Replace the `gated` state with the access state:

```typescript
  const [access, setAccess] = useState<AccessState | undefined>(undefined);
```

In the existing `getMe()` effect, replace `setGated(!me.admitted)` with
`setAccess(me.access)`. Keep the `NotAuthenticated` redirect and the
fail-open `catch` exactly as they are — a transient 500 must not lock an
admitted user out of their own console, and that reasoning is unchanged.

Replace line 170:

```typescript
  const screen = screenFor(access, pathname);
```

Replace the `<main>` body at line 294:

```typescript
        <main id="content" className="min-w-0 flex-1">
          {screen === "onboarding" ? (
            <OnboardingForm onSubmitted={() => setAccess("pending")} />
          ) : screen === "pending" ? (
            <PendingScreen email={session?.email ?? null} />
          ) : screen === "declined" ? (
            <DeclinedScreen />
          ) : (
            children
          )}
        </main>
```

Delete the `INVITE_GATE_BYPASS` constant and the `InviteGate` import; the
constant moves to `access-screen.ts` as `INVITE_ROUTE`.

Hide the nav rail's links for every non-console screen — a nav to pages the
user cannot open is worse than no nav. Render the rail's `<nav>` only when
`screen === "console"`, keeping the wordmark and the account/sign-out
footer.

Add an `Admin` nav item, rendered only when the loaded profile has
`is_admin`. Store the profile from the same `getMe()` call rather than
adding a second request.

- [ ] **Step 7: Delete `InviteGate`**

```bash
git rm apps/web/components/shell/InviteGate.tsx
```

Confirm nothing still imports it:

Run: `cd apps/web && grep -rn "InviteGate" --include=*.tsx --include=*.ts . | grep -v node_modules`
Expected: no output.

- [ ] **Step 8: Verify**

Run: `cd apps/web && npx tsc --noEmit && npm run lint && npm test`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add -A apps/web
git commit -m "feat: switch the console on access state, retire the invite gate"
```

---

### Task 12: The admin queue page

**Files:**
- Create: `apps/web/app/(console)/admin/requests/page.tsx`
- Create: `apps/web/app/(console)/admin/layout.tsx`

**Interfaces:**
- Consumes: `listAccessRequests`, `approveAccessRequest`, `declineAccessRequest`, `AccessRequestRow` (Task 9).

- [ ] **Step 1: Build the layout**

Create `apps/web/app/(console)/admin/layout.tsx` mirroring
`app/(console)/account/layout.tsx` (read it first — it is 195 bytes and
sets the page title).

- [ ] **Step 2: Build the page**

Create `apps/web/app/(console)/admin/requests/page.tsx`. Requirements:

- `"use client"`.
- Loads `listAccessRequests("pending")` on mount.
- A 403 renders "You don't have access to this page." rather than an error
  dump — a non-admin who guesses the URL should get a plain answer. The API
  returns `ApiError` with detail `admin required`, not `NotAuthenticated`.
- One row per request showing: full name, email, company, `email_domain`
  with a "personal" chip when `is_personal_email`, role, team size,
  `requested_at` as a date, `compute_sources` as chips, `use_case` as
  wrapped text, and — when `pending_pool_name` is set — the line
  `Invited to {pending_pool_name} by {invited_by_name}`.
- Approve and Decline buttons per row. Both optimistically remove the row,
  and restore it with a `toast.error` if the call fails. Use `sonner`'s
  `toast`, as the account page does.
- After approving, show `toast.success` with copy that does not imply an
  email was sent: `Approved — they're in. Let them know yourself.`
- Empty state: `Nothing waiting. New requests show up here.`

Do NOT add a control that grants `is_admin`. It is set by SQL, deliberately.

- [ ] **Step 3: Verify**

Run: `cd apps/web && npx tsc --noEmit && npm run lint`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add apps/web/app/\(console\)/admin
git commit -m "feat: add the admin access-request queue page"
```

---

### Task 13: Account fields, and the pool-join surfaces

**Files:**
- Modify: `apps/web/app/(console)/account/page.tsx`
- Modify: `apps/web/app/(console)/pools/page.tsx`
- Modify: `apps/web/app/(console)/pools/join/page.tsx`

**Interfaces:**
- Consumes: `updateProfile`, `Profile`, `acceptInvite` (Task 9), `ROLE_OPTIONS`, `TEAM_SIZE_OPTIONS` (Task 10).

- [ ] **Step 1: Add the profile fields to the account page**

In `apps/web/app/(console)/account/page.tsx`, add a section between the
display-name section and "Account details":

- Heading: `Your details`
- Help: `Used to understand who's on FlashML. Only you and the FlashML team see this.`
- Editable: first name, last name, company, role (select), team size (select).
- One Save button for the section, calling `updateProfile` with only the
  changed fields, mirroring the existing dirty/saving/saved pattern rather
  than inventing a second one.
- On success `toast.success("Details saved")`.

This is the only path by which a grandfathered tester ever fills these in —
they are deliberately never prompted — so the empty state must invite rather
than nag. When every field is null, show the help line
`You signed up before we asked for this. Filling it in helps us build the right thing.`

- [ ] **Step 2: Add "Join with a code" to the pools page**

In `apps/web/app/(console)/pools/page.tsx`, add an input + button that calls
`acceptInvite(tokenFromInput(value))` using the existing
`@/lib/invite-token` helper — this is the affordance `InviteGate` used to
own, and `tokenFromInput` already accepts either a full link or a bare code.

On success: `toast.success` naming the pool, then reload the list. On
`NotFound`: `That invite link isn't valid, or it's already been used.` —
reuse the copy `InviteGate` used, it was already right.

If the response has `joined: false`, say so honestly instead of claiming
membership:
`Saved. You'll join {name} as soon as your access is approved.`

- [ ] **Step 3: Update the invite landing page**

In `apps/web/app/(console)/pools/join/page.tsx`, the current success path
reloads into the console, which assumed redeeming an invite admits you. For
a `joined: false` response, render instead:

```
Invite saved
You'll join {name} as soon as your access is approved.
```

Remove or rewrite the comments in that file describing admission-on-accept —
lines 9–11 and 41 both state the old coupling and would now be misleading.

- [ ] **Step 4: Verify**

Run: `cd apps/web && npx tsc --noEmit && npm run lint && npm test`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A apps/web
git commit -m "feat: edit profile details, join a pool with a code"
```

---

### Task 14: Full-stack verification

**Files:** none created.

- [ ] **Step 1: Run both suites**

```bash
cd apps/api && pytest tests/ -q
cd ../web && npm test && npx tsc --noEmit && npm run lint
```

Expected: all pass.

- [ ] **Step 2: Apply the migration to a scratch database and check the dry run**

```bash
cd apps/api
python -m flashml_cloud_api.migrate --dry-run
```

Expected: `0009_access_requests` listed as pending, no drift reported on
0001–0008. **Drift on an earlier file means one was edited — stop and fix
that before anything else.**

- [ ] **Step 3: Exercise the flow by hand**

```bash
cd ../..
./scripts/dev.sh --all
```

Then, against `localhost:3000`:

1. Sign up with a fresh email → the onboarding form appears.
2. Submit → the pending screen appears, naming your address.
3. `UPDATE public.profiles SET is_admin = true WHERE id = '<your-other-account>';`
4. As that admin, open `/admin/requests` → the request is listed with its company and use case.
5. Approve → reload the first account → the console renders.
6. Create a pool as the admin, generate an invite, redeem it from a third fresh account → "Invite saved", and the request shows `Invited to …`.
7. Approve that third account → confirm it lands in the pool.

- [ ] **Step 4: Update the docs**

- `PROGRESS.md` — append a dated entry per the logging protocol in that file.
- `POSITIONING_LOG.md` — append an entry recording that a workspace invite
  no longer grants product access, with the trigger (the owner's 2026-08-04
  decision that signup must be manageable and invites are workspace joins
  "like GitHub"), and that admission is now one door the owner controls.

- [ ] **Step 5: Commit**

```bash
git add PROGRESS.md flashml-cloud/docs/superpowers/specs/POSITIONING_LOG.md
git commit -m "docs: record the access-request flow and the invite decoupling"
```

---

## Notes for the implementer

**Three things carried deliberately, not overlooked:**

1. **Approval is silent.** No email is sent, ever. Nothing in the UI may
   imply otherwise. The owner notifies people by hand.
2. **`is_admin` is granted by SQL only.** Do not add a UI for it.
3. **A declined invitee burns one use of the pool owner's link.** The
   alternative lets one link be claimed by unlimited pending accounts.

**The single highest-risk change** is Task 5. `admitted_at` is read by
`admitted_user` and by the seven placement gates; the tests that currently
assert invite-grants-admission are the specification of the behaviour being
removed. Change them deliberately, with a comment pointing at the spec — do
not delete them.
