# Developer Identity (`fmu_` tokens) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a developer's CLI a credential of its own, so that a non-browser
program can call the job-author API as its owner.

**Architecture:** A new bearer-token class, `fmu_`, minted by a `kind: "cli"`
variant of the device-code flow that already enrols machines. Tokens live in a
new `public.cli_credentials` table, hashed, revocable from the console. The
single change that unlocks everything is in `current_user`: it learns to resolve
an `fmu_` token to a user id, which makes every route already tagged `browser`
reachable from a CLI with no per-route edit.

**Tech Stack:** FastAPI + psycopg 3 + Postgres (API), pytest against a real
ephemeral Postgres, Next.js 15 + React + Vitest (console).

This is **Plan 1 of 3** from
`docs/superpowers/specs/2026-08-10-developer-surface-and-mcp-design.md` (§3).
Plans 2 and 3 (the `flashml` package, then the MCP server) both depend on it and
neither is started here.

## Global Constraints

- **Token prefix is exactly `fmu_`.** Mirrors `fmk_` (machines) and `fmi_`
  (invites) in `flashml_cloud_api/auth.py`.
- **Entropy is `secrets.token_urlsafe(32)`,** identical to `new_machine_token`.
- **The raw token is returned exactly once and never stored or logged.** Only
  `sha256` hex reaches the database.
- **The two credential kinds never share a code path.** A prefix check routes
  the token before any database work — the reason is documented on
  `machine_caller` in `app.py`: resolving a connection before checking the
  credential's shape makes every anonymous request cost a Postgres connection.
- **`owner_id` never comes from a request body.** Only from a verified JWT
  `sub`, at approval time.
- **Unknown and revoked are indistinguishable.** Both answer 401. Ownership
  mismatches answer 404, matching `fetch_machine_for_owner`'s documented fold.
- **No `select *` into a response.** Column allowlists only, following
  `MACHINE_PUBLIC_COLUMNS` — `token_hash` sits in the same table.
- **Migrations are append-only and never edited after being applied.** The
  runner in `flashml_cloud_api/migrate.py` checksums every file and refuses the
  whole set on drift. The next free version is `0012`.
- **Python API tests run from `flashml-cloud/apps/api/` with its `.venv`.** Web
  tests run from `flashml-cloud/apps/web/` with `npx vitest run`.

---

## File Structure

**Create**

| Path | Responsibility |
|---|---|
| `flashml-cloud/apps/api/migrations/0012_cli_credentials.sql` | The table, the `kind` column, the relaxed `node_id` |
| `flashml-cloud/apps/api/flashml_cloud_api/cli_auth.py` | Pure functions for the CLI credential lifecycle. No FastAPI. |
| `flashml-cloud/apps/api/tests/test_cli_auth.py` | The lifecycle, against a real Postgres |
| `flashml-cloud/apps/api/tests/test_cli_token_routes.py` | The HTTP surface, via `TestClient` |
| `flashml-cloud/apps/web/app/(console)/account/cli/page.tsx` | The console page |
| `flashml-cloud/apps/web/lib/cli-credential-status.ts` | Presentation logic, unit-testable |
| `flashml-cloud/apps/web/lib/cli-credential-status.test.ts` | Its tests |

**Modify**

| Path | Change |
|---|---|
| `.../flashml_cloud_api/auth.py` | `fmu_` helpers, beside the `fmk_`/`fmi_` ones |
| `.../flashml_cloud_api/db.py` | Row helpers for `cli_credentials` and CLI device codes |
| `.../flashml_cloud_api/app.py` | `current_user` branch; `kind` on three device routes; two new routes |
| `.../apps/web/lib/cloud-api.ts` | `CliCredential`, `listCliCredentials`, `revokeCliCredential`, widened `ApproveDeviceCodeResult` |
| `.../apps/web/app/(console)/activate/page.tsx` | Branch on what the approval returned |

`cli_auth.py` is a new module rather than more functions in `enrolment.py`
deliberately. `enrolment.py`'s entire docstring is about turning a machine into
an authenticated worker, and its `approve_device_code` carries three pages of
`node_id` uniqueness reasoning that a CLI credential has no equivalent of. The
two flows share a `device_codes` table and nothing else.

---

### Task 1: `fmu_` token helpers

Pure functions, no database, no FastAPI. Mirrors the `fmk_`/`fmi_` blocks
already in the file.

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/auth.py` (append after the
  `fmi_` block, which ends with `looks_like_invite_token`)
- Test: `flashml-cloud/apps/api/tests/test_auth.py` (append)

**Interfaces:**
- Consumes: nothing.
- Produces: `USER_TOKEN_PREFIX: str`, `new_user_token() -> str`,
  `hash_user_token(token: str) -> str`,
  `looks_like_user_token(token: str | None) -> bool`.

- [ ] **Step 1: Write the failing test**

Append to `flashml-cloud/apps/api/tests/test_auth.py`:

```python
def test_user_token_has_the_fmu_prefix_and_real_entropy():
    from flashml_cloud_api.auth import new_user_token

    token = new_user_token()
    assert token.startswith("fmu_")
    # token_urlsafe(32) is 43 base64url characters.
    assert len(token) == len("fmu_") + 43
    assert new_user_token() != token


def test_user_token_hash_is_stable_sha256_hex():
    import hashlib

    from flashml_cloud_api.auth import hash_user_token

    assert hash_user_token("fmu_abc") == hashlib.sha256(b"fmu_abc").hexdigest()
    assert hash_user_token("fmu_abc") == hash_user_token("fmu_abc")


def test_looks_like_user_token_does_not_confuse_the_credential_kinds():
    from flashml_cloud_api.auth import (
        looks_like_invite_token,
        looks_like_machine_token,
        looks_like_user_token,
    )

    user = "fmu_x"
    assert looks_like_user_token(user)
    assert not looks_like_machine_token(user)
    assert not looks_like_invite_token(user)
    assert not looks_like_user_token("fmk_x")
    assert not looks_like_user_token("fmi_x")
    assert not looks_like_user_token("eyJhbGciOi")
    assert not looks_like_user_token(None)
    assert not looks_like_user_token("")
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest tests/test_auth.py -k user_token -v
```

Expected: FAIL — `ImportError: cannot import name 'new_user_token'`.

- [ ] **Step 3: Write the implementation**

Append to `flashml_cloud_api/auth.py`:

```python
#: Same discipline as ``MACHINE_TOKEN_PREFIX`` and ``INVITE_TOKEN_PREFIX``: a
#: leaked developer token is greppable in logs without revealing anything
#: about its value. `fmu_` — u for user — because unlike a machine token it
#: acts as its owner, with exactly their access and no more.
USER_TOKEN_PREFIX = "fmu_"


def new_user_token() -> str:
    """Mint a new, unguessable developer (CLI) token. Mirrors
    ``new_machine_token`` exactly — same entropy, same "prefix on the
    outside, nothing recoverable from it" shape — because it is the same
    kind of bearer secret: whoever holds the raw value acts as its owner
    until it is revoked."""
    return USER_TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_user_token(token: str) -> str:
    """One-way, stable digest of a developer token for storage/comparison.
    The raw token is never stored or logged — only this hash ever reaches
    ``public.cli_credentials.token_hash``."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def looks_like_user_token(token: str | None) -> bool:
    return bool(token) and token.startswith(USER_TOKEN_PREFIX)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest tests/test_auth.py -v
```

Expected: PASS, including every pre-existing test in the file.

- [ ] **Step 5: Commit**

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/auth.py \
        flashml-cloud/apps/api/tests/test_auth.py
git commit -m "feat(api): add the fmu_ developer token class"
```

---

### Task 2: Migration 0012 and the row helpers

**Files:**
- Create: `flashml-cloud/apps/api/migrations/0012_cli_credentials.sql`
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/db.py`
- Test: `flashml-cloud/apps/api/tests/test_schema.py` (append)

**Interfaces:**
- Consumes: Task 1's `hash_user_token` (only in later tasks; this task stores
  whatever hash it is handed).
- Produces, all in `flashml_cloud_api.db`:
  - `CliCredential` dataclass: `id: str`, `owner_id: str`, `label: str | None`,
    `status: str`, `created_at: datetime | None`, `revoked_at: datetime | None`
  - `insert_cli_device_code(db, *, device_code: str, user_code: str, label: str | None, expires_at: datetime) -> None`
  - `insert_cli_credential(db, *, owner_id: str, label: str | None) -> str`
  - `mark_cli_device_code_approved(db, user_code: str, user_id: str, credential_id: str) -> None`
  - `claim_cli_device_code_for_redemption(db, device_code: str) -> str | None`
  - `set_cli_credential_token(db, credential_id: str, token_hash: str, token_prefix: str) -> None`
  - `fetch_cli_credential_by_token_hash(db, token_hash: str) -> dict[str, Any] | None`
  - `touch_cli_credential_last_used(db, credential_id: str) -> None`
  - `list_cli_credentials_for_owner(db, owner_id: str) -> list[dict[str, Any]]`
  - `revoke_cli_credential_row(db, credential_id: str, owner_id: str) -> bool`
  - `CLI_CREDENTIAL_PUBLIC_COLUMNS: tuple[str, ...]`

- [ ] **Step 1: Write the failing test**

Append to `flashml-cloud/apps/api/tests/test_schema.py`:

```python
def test_cli_credentials_table_exists_with_the_expected_shape(postgres_dsn):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(postgres_dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select column_name, is_nullable
                  from information_schema.columns
                 where table_schema = 'public'
                   and table_name = 'cli_credentials'
                """
            )
            cols = {r["column_name"]: r["is_nullable"] for r in cur.fetchall()}

    assert cols["id"] == "NO"
    assert cols["owner_id"] == "NO"
    assert cols["token_hash"] == "NO"
    assert cols["token_prefix"] == "NO"
    assert cols["status"] == "NO"
    assert cols["label"] == "YES"
    assert cols["last_used_at"] == "YES"
    assert cols["revoked_at"] == "YES"


def test_device_codes_carries_a_kind_defaulting_to_machine(postgres_dsn):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(postgres_dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select column_name, is_nullable, column_default
                  from information_schema.columns
                 where table_schema = 'public'
                   and table_name = 'device_codes'
                   and column_name in ('kind', 'node_id', 'credential_id')
                """
            )
            cols = {r["column_name"]: r for r in cur.fetchall()}

    assert cols["kind"]["is_nullable"] == "NO"
    assert "machine" in (cols["kind"]["column_default"] or "")
    # Relaxed so a CLI code, which has no node, can be inserted at all.
    assert cols["node_id"]["is_nullable"] == "YES"
    assert cols["credential_id"]["is_nullable"] == "YES"


def test_a_machine_device_code_still_requires_a_node_id(postgres_dsn):
    """The check constraint that keeps relaxing node_id from weakening the
    machine flow: only kind='cli' may omit it."""
    import psycopg
    from datetime import datetime, timedelta, timezone

    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    with psycopg.connect(postgres_dsn) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    insert into public.device_codes
                        (device_code, user_code, kind, node_id, expires_at)
                    values ('dc-test-1', 'UC-TEST1', 'machine', null, %s)
                    """,
                    (expires,),
                )
            except psycopg.errors.CheckViolation:
                conn.rollback()
            else:
                conn.rollback()
                raise AssertionError(
                    "a machine device code was accepted with a null node_id"
                )
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest tests/test_schema.py -k "cli_credentials or device_codes or node_id" -v
```

Expected: FAIL — `KeyError: 'id'` / `KeyError: 'kind'`, because neither the
table nor the column exists.

- [ ] **Step 3: Write the migration**

Create `flashml-cloud/apps/api/migrations/0012_cli_credentials.sql`:

```sql
-- 0012_cli_credentials.sql
--
-- A credential a developer's own program can present.
--
-- WHY THIS EXISTS. Until now the API knew exactly two kinds of caller: a
-- Supabase JWT (a browser) and an `fmk_` machine token (an enrolled worker).
-- Every job-author route is tagged `browser`, so the only way to submit a job
-- was to be a person clicking in a console. A CLI — and the MCP server built
-- on it — has nothing to present. This table is that third kind.
--
-- IT IS NOT A MACHINE. `public.machines` rows are workers: they have a
-- node_id, they claim leases, they are placed on. A cli_credential does none
-- of that. It acts as its owner, with exactly their access — an un-admitted
-- account's CLI token gets the same 403 their browser does — and it is
-- revocable on its own without disturbing their browser session.
--
-- ONE CODE TABLE, NOT TWO. Both flows mint a short user_code a human types at
-- /activate, and that page cannot tell which flow a code belongs to unless
-- user_code is unique across both. Postgres will not enforce uniqueness across
-- two tables, so `kind` discriminates within one. The default keeps every
-- existing row and every existing insert meaning exactly what it meant before.
--
-- RELAXING node_id IS GUARDED. A CLI code has no node, so the NOT NULL had to
-- go; the check constraint below puts it straight back for kind='machine', so
-- the volunteer enrolment path cannot be weakened by this migration.

create table if not exists public.cli_credentials (
    id            uuid primary key default gen_random_uuid(),
    owner_id      uuid not null references public.profiles(id) on delete cascade,
    label         text,
    token_hash    text not null unique,
    token_prefix  text not null,
    status        text not null default 'active'
                  check (status in ('active', 'revoked')),
    last_used_at  timestamptz,
    created_at    timestamptz not null default now(),
    revoked_at    timestamptz
);

comment on table public.cli_credentials is
    'A developer CLI credential. token_hash is a sha256 hex digest of an '
    '`fmu_` token; the raw token is returned to the CLI exactly once at '
    'redemption and never stored. Presenting it authenticates AS THE OWNER '
    '— it grants no access the owner does not already have. status is '
    'constrained at the database level so an invalid state cannot be '
    'written even by a bug in the API.';

comment on column public.cli_credentials.last_used_at is
    'Written at most once per minute per credential. A timestamp update on '
    'every authenticated request would put a write in front of every read.';

alter table public.cli_credentials enable row level security;

create index if not exists cli_credentials_owner_idx
    on public.cli_credentials (owner_id);

alter table public.device_codes
    add column if not exists kind text not null default 'machine';

alter table public.device_codes
    drop constraint if exists device_codes_kind_check;
alter table public.device_codes
    add constraint device_codes_kind_check check (kind in ('machine', 'cli'));

alter table public.device_codes
    add column if not exists credential_id uuid
        references public.cli_credentials(id) on delete set null;

alter table public.device_codes alter column node_id drop not null;

alter table public.device_codes
    drop constraint if exists device_codes_machine_needs_node;
alter table public.device_codes
    add constraint device_codes_machine_needs_node
        check (kind <> 'machine' or node_id is not null);

comment on column public.device_codes.kind is
    'Which flow this code belongs to. A machine code redeems for an fmk_ '
    'token bound to a machines row; a cli code redeems for an fmu_ token '
    'bound to a cli_credentials row. One table so user_code is unique '
    'across both — /activate has only the typed code to go on.';
```

- [ ] **Step 4: Run the schema tests**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest tests/test_schema.py -v
```

Expected: PASS. The `postgres_dsn` fixture applies migrations with the real
runner, so a syntax error surfaces here.

- [ ] **Step 5: Write the row helpers**

Append to `flashml_cloud_api/db.py`. Put the dataclass next to `Machine` (around
line 53) and the functions after the machine block (around line 710):

```python
@dataclass(frozen=True)
class CliCredential:
    """A row from ``public.cli_credentials``, as returned to callers that
    have already authenticated the credential (never constructed from
    caller-supplied data). ``token_hash`` is deliberately absent — nothing
    that leaves this module needs it."""

    id: str
    owner_id: str
    label: str | None
    status: str
    created_at: datetime | None = None
    revoked_at: datetime | None = None
```

```python
# ---------------------------------------------------------------------------
# cli credentials
# ---------------------------------------------------------------------------

#: The columns of ``public.cli_credentials`` that may ever leave the API.
#: Spelled out rather than ``select *`` for the same reason
#: ``MACHINE_PUBLIC_COLUMNS`` is: ``token_hash`` lives in the same table, and
#: a ``select *`` feeding a JSON response is exactly how a credential digest
#: ends up in a browser.
CLI_CREDENTIAL_PUBLIC_COLUMNS = (
    "id", "label", "status", "token_prefix",
    "last_used_at", "created_at", "revoked_at",
)


def insert_cli_device_code(
    db: psycopg.Connection,
    *,
    device_code: str,
    user_code: str,
    label: str | None,
    expires_at: datetime,
) -> None:
    """A device code for the CLI flow. ``node_id`` is left null — the check
    constraint added in 0012 permits that only for ``kind = 'cli'``. The
    label rides in ``hostname``, the column that already carries "what the
    human will recognise this as"."""
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.device_codes
                (device_code, user_code, kind, hostname, expires_at)
            values (%s, %s, 'cli', %s, %s)
            """,
            (device_code, user_code, label, expires_at),
        )


def insert_cli_credential(
    db: psycopg.Connection, *, owner_id: str, label: str | None
) -> str:
    """Create the credential row at APPROVAL time, before any token exists.
    token_hash is not null, so a placeholder that cannot collide and cannot
    be presented is written and then overwritten by
    ``set_cli_credential_token``. The placeholder is not a valid sha256 hex
    digest, so no token can ever hash to it."""
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.cli_credentials
                (owner_id, label, token_hash, token_prefix)
            values (%s, %s, 'pending:' || gen_random_uuid()::text, '')
            returning id
            """,
            (owner_id, label),
        )
        return str(cur.fetchone()["id"])


def mark_cli_device_code_approved(
    db: psycopg.Connection, user_code: str, user_id: str, credential_id: str
) -> None:
    with db.cursor() as cur:
        cur.execute(
            """
            update public.device_codes
               set credential_id = %s, approved_by = %s
             where user_code = %s and kind = 'cli'
            """,
            (credential_id, user_id, user_code),
        )


def claim_cli_device_code_for_redemption(
    db: psycopg.Connection, device_code: str
) -> str | None:
    """Atomically mark a CLI device_code consumed and return its
    credential_id — but only if it is approved, unexpired, and not already
    consumed. Returns None in every other case without distinguishing which,
    so a caller cannot use this as an oracle for which codes exist. The
    single ``UPDATE ... WHERE consumed_at is null ... RETURNING`` is what
    makes "redeemed exactly once" hold under concurrent attempts."""
    with db.cursor() as cur:
        cur.execute(
            """
            update public.device_codes
               set consumed_at = now()
             where device_code = %s
               and kind = 'cli'
               and consumed_at is null
               and credential_id is not null
               and expires_at > now()
            returning credential_id
            """,
            (device_code,),
        )
        row = cur.fetchone()
        return str(row["credential_id"]) if row else None


def set_cli_credential_token(
    db: psycopg.Connection, credential_id: str, token_hash: str, token_prefix: str
) -> None:
    with db.cursor() as cur:
        cur.execute(
            """
            update public.cli_credentials
               set token_hash = %s, token_prefix = %s, status = 'active'
             where id = %s
            """,
            (token_hash, token_prefix, credential_id),
        )


def fetch_cli_credential_by_token_hash(
    db: psycopg.Connection, token_hash: str
) -> dict[str, Any] | None:
    with db.cursor() as cur:
        cur.execute(
            "select * from public.cli_credentials where token_hash = %s",
            (token_hash,),
        )
        return cur.fetchone()


def touch_cli_credential_last_used(db: psycopg.Connection, credential_id: str) -> None:
    """Rate-limited in SQL, not in Python: the WHERE clause means a
    credential used a hundred times a second costs one write a minute, and
    it holds across API processes, which a Python-side cache would not."""
    with db.cursor() as cur:
        cur.execute(
            """
            update public.cli_credentials
               set last_used_at = now()
             where id = %s
               and (last_used_at is null or last_used_at < now() - interval '1 minute')
            """,
            (credential_id,),
        )


def list_cli_credentials_for_owner(
    db: psycopg.Connection, owner_id: str
) -> list[dict[str, Any]]:
    """Every credential belonging to owner_id, and nothing else. The owner
    filter is in the SQL, not applied afterwards in Python — omitting it
    would be a missing argument, not a missing ``if``."""
    columns = ", ".join(CLI_CREDENTIAL_PUBLIC_COLUMNS)
    with db.cursor() as cur:
        cur.execute(
            f"select {columns} from public.cli_credentials "
            "where owner_id = %s order by created_at",
            (owner_id,),
        )
        return list(cur.fetchall())


def revoke_cli_credential_row(
    db: psycopg.Connection, credential_id: str, owner_id: str
) -> bool:
    """Owner-scoped revoke. Returns True only if a row belonging to owner_id
    was actually updated — a bad id and an id owned by someone else both
    return False, indistinguishably."""
    with db.cursor() as cur:
        try:
            cur.execute(
                """
                update public.cli_credentials
                   set status = 'revoked', revoked_at = now()
                 where id = %s and owner_id = %s and status <> 'revoked'
                """,
                (credential_id, owner_id),
            )
        except psycopg.errors.InvalidTextRepresentation:
            # Not even a uuid. Same answer as "no such credential".
            return False
        return cur.rowcount > 0
```

- [ ] **Step 6: Run the whole API suite**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest -q
```

Expected: PASS, with the count up by the three schema tests. Record the number.

- [ ] **Step 7: Commit**

```bash
git add flashml-cloud/apps/api/migrations/0012_cli_credentials.sql \
        flashml-cloud/apps/api/flashml_cloud_api/db.py \
        flashml-cloud/apps/api/tests/test_schema.py
git commit -m "feat(api): add cli_credentials and the cli device-code kind"
```

---

### Task 3: The `cli_auth` module

**Files:**
- Create: `flashml-cloud/apps/api/flashml_cloud_api/cli_auth.py`
- Test: `flashml-cloud/apps/api/tests/test_cli_auth.py`

**Interfaces:**
- Consumes: Task 1's `new_user_token` / `hash_user_token`; every Task 2 helper.
- Produces, in `flashml_cloud_api.cli_auth`:
  - `CliCodeError(Exception)`, `CliCodeNotFound(CliCodeError)`,
    `CliCodeExpired(CliCodeError)`
  - `start_cli_code(db, label: str | None) -> dict` — keys `device_code`,
    `user_code`, `expires_at`, `interval`
  - `approve_cli_code(db, user_code: str, user_id: str) -> str` (credential id)
  - `redeem_cli_code(db, device_code: str) -> str | None` (raw token, once)
  - `authenticate_cli(db, token: str | None) -> CliCredential | None`

- [ ] **Step 1: Write the failing test**

Create `flashml-cloud/apps/api/tests/test_cli_auth.py`:

```python
"""The CLI credential lifecycle, pinned against a real Postgres.

The properties that matter here — exactly-once redemption, revocation
taking effect immediately, an unapproved code never yielding a token — are
properties of the database's transactional behaviour as much as of the
Python, so nothing in this file is mocked. Wiring matches
tests/test_enrolment.py: the session-scoped ephemeral Postgres from
conftest.py, with every row namespaced by a per-run marker.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from flashml_cloud_api import cli_auth, db as dbmod

RUN_MARKER = uuid.uuid4().hex[:12]


@pytest.fixture(scope="module")
def db(postgres_dsn):
    url = (
        os.environ.get("TEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or postgres_dsn
    )
    conn = psycopg.connect(url, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def owner(db):
    """A real profiles row. profiles.id is a FK to auth.users, so the user
    must exist first — the same two-step tests/test_enrolment.py does.

    NOTE: `_make_test_user` lives in tests/test_enrolment.py. Import it only
    if `tests/` is an importable package (check for `tests/__init__.py`); if
    it is not, copy the helper verbatim into this file rather than adding an
    `__init__.py`, which would change how the whole suite is collected.
    """
    from tests.test_enrolment import _make_test_user

    user_id = _make_test_user(db, f"cli-{RUN_MARKER}")
    dbmod.upsert_profile(db, user_id)
    yield user_id
    with db.cursor() as cur:
        cur.execute("delete from public.cli_credentials where owner_id = %s", (user_id,))
        cur.execute("delete from public.profiles where id = %s", (user_id,))
        cur.execute("delete from auth.users where id = %s", (user_id,))


def test_a_started_code_yields_nothing_until_someone_approves_it(db):
    started = cli_auth.start_cli_code(db, "laptop")
    assert cli_auth.redeem_cli_code(db, started["device_code"]) is None


def test_the_full_flow_returns_a_usable_fmu_token(db, owner):
    started = cli_auth.start_cli_code(db, "laptop")
    credential_id = cli_auth.approve_cli_code(db, started["user_code"], owner)

    token = cli_auth.redeem_cli_code(db, started["device_code"])
    assert token is not None
    assert token.startswith("fmu_")

    resolved = cli_auth.authenticate_cli(db, token)
    assert resolved is not None
    assert resolved.owner_id == owner
    assert resolved.id == credential_id
    assert resolved.label == "laptop"


def test_a_code_redeems_exactly_once(db, owner):
    started = cli_auth.start_cli_code(db, "laptop")
    cli_auth.approve_cli_code(db, started["user_code"], owner)

    assert cli_auth.redeem_cli_code(db, started["device_code"]) is not None
    assert cli_auth.redeem_cli_code(db, started["device_code"]) is None


def test_an_expired_code_is_refused_at_approval(db, owner):
    started = cli_auth.start_cli_code(db, "laptop")
    with db.cursor() as cur:
        cur.execute(
            "update public.device_codes set expires_at = %s where device_code = %s",
            (datetime.now(timezone.utc) - timedelta(seconds=1), started["device_code"]),
        )
    with pytest.raises(cli_auth.CliCodeExpired):
        cli_auth.approve_cli_code(db, started["user_code"], owner)


def test_an_unknown_user_code_is_refused(db, owner):
    with pytest.raises(cli_auth.CliCodeNotFound):
        cli_auth.approve_cli_code(db, "ZZZZZZZZ", owner)


def test_approving_twice_does_not_mint_a_second_credential(db, owner):
    started = cli_auth.start_cli_code(db, "laptop")
    first = cli_auth.approve_cli_code(db, started["user_code"], owner)
    second = cli_auth.approve_cli_code(db, started["user_code"], owner)
    assert first == second


def test_a_revoked_credential_stops_authenticating_immediately(db, owner):
    started = cli_auth.start_cli_code(db, "laptop")
    credential_id = cli_auth.approve_cli_code(db, started["user_code"], owner)
    token = cli_auth.redeem_cli_code(db, started["device_code"])

    assert dbmod.revoke_cli_credential_row(db, credential_id, owner) is True
    assert cli_auth.authenticate_cli(db, token) is None


def test_revoking_someone_elses_credential_reports_nothing(db, owner):
    started = cli_auth.start_cli_code(db, "laptop")
    credential_id = cli_auth.approve_cli_code(db, started["user_code"], owner)
    stranger = str(uuid.uuid4())
    assert dbmod.revoke_cli_credential_row(db, credential_id, stranger) is False
    # And a garbage id is the same answer, not a 500.
    assert dbmod.revoke_cli_credential_row(db, "not-a-uuid", owner) is False


def test_an_unknown_token_and_no_token_both_resolve_to_none(db):
    assert cli_auth.authenticate_cli(db, None) is None
    assert cli_auth.authenticate_cli(db, "") is None
    assert cli_auth.authenticate_cli(db, "fmu_nope") is None


def test_a_machine_code_is_not_approvable_through_the_cli_path(db, owner):
    """The two flows share a table. They must not share a code."""
    from flashml_cloud_api import enrolment

    started = enrolment.start_device_code(
        db, f"node-{RUN_MARKER}", "host", "linux"
    )
    with pytest.raises(cli_auth.CliCodeNotFound):
        cli_auth.approve_cli_code(db, started["user_code"], owner)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest tests/test_cli_auth.py -v
```

Expected: FAIL at collection — `ImportError: cannot import name 'cli_auth'`.

- [ ] **Step 3: Write the implementation**

Create `flashml-cloud/apps/api/flashml_cloud_api/cli_auth.py`:

```python
"""Device-code login for a developer's CLI: turning a program into a
caller that acts as its owner.

A SIBLING OF enrolment.py, NOT AN EXTENSION OF IT
-------------------------------------------------
Both flows mint a short user_code a human types at /activate, and they
share the ``device_codes`` table so that code is unique across both. They
share nothing else. ``enrolment.approve_device_code`` is three pages of
node_id uniqueness reasoning — a machine identity is globally unique,
survives revocation, and must never be adoptable by a second account. A
CLI credential has no equivalent of any of that: it is one of many a
person may hold, it grants exactly its owner's access, and revoking it
disturbs nothing else. Folding the two into one function would mean every
reader of either has to hold both sets of rules.

The flow, spelled out because the security properties live in the order of
operations:

1. The CLI calls ``start_cli_code`` and gets a long ``device_code`` (for
   itself) and a short ``user_code`` (for a human). Neither identifies
   anyone yet.
2. A signed-in person approves the user_code in the console. This is the
   only place ``owner_id`` enters the flow, and it comes from the verified
   JWT ``sub`` — never from a request body.
3. The CLI polls ``redeem_cli_code``. Only once approval has happened does
   this return a token, and it returns the raw token *exactly once* — the
   raw value is never persisted, only its hash, so after that one response
   it is gone even from the database's point of view.

No FastAPI here — pure functions the app layer wraps into HTTP responses.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import psycopg

from flashml_cloud_api import db as dbmod
from flashml_cloud_api.auth import hash_user_token, new_user_token
from flashml_cloud_api.db import CliCredential
from flashml_cloud_api.enrolment import (
    POLL_INTERVAL_SECONDS,
    USER_CODE_ALPHABET,
    USER_CODE_INSERT_ATTEMPTS,
    USER_CODE_LENGTH,
)

#: The same ten minutes ``enrolment.DEVICE_CODE_TTL`` gives a machine. A
#: person approving a CLI login is doing the same thing at the same desk.
CLI_CODE_TTL = timedelta(minutes=10)

#: How much of the raw token is kept in the clear, for the console to show
#: so a person can tell two credentials apart. Matches what
#: ``enrolment.redeem_device_code`` keeps for a machine.
TOKEN_PREFIX_CHARS = 12


class CliCodeError(Exception):
    """Base class for CLI-login failures the app layer must turn into a
    clean HTTP response — never let one surface as an unhandled 500."""


class CliCodeNotFound(CliCodeError):
    """No CLI device_codes row matches this user_code. A code belonging to
    the *machine* flow raises this too: the flows share a table, and one
    must not be approvable through the other's path."""


class CliCodeExpired(CliCodeError):
    """The code existed but its ten-minute window passed unapproved."""


def _new_user_code() -> str:
    return "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(USER_CODE_LENGTH))


def _new_device_code() -> str:
    return secrets.token_urlsafe(32)


def start_cli_code(db: psycopg.Connection, label: str | None) -> dict:
    """Issue a fresh device_code/user_code pair for a CLI that wants to log
    in. Nobody is authenticated yet, and ``label`` is only a claim — it is
    display text, never read by any authorization decision."""
    device_code = _new_device_code()
    expires_at = datetime.now(timezone.utc) + CLI_CODE_TTL

    user_code = ""
    for _ in range(USER_CODE_INSERT_ATTEMPTS):
        candidate = _new_user_code()
        try:
            dbmod.insert_cli_device_code(
                db,
                device_code=device_code,
                user_code=candidate,
                label=label,
                expires_at=expires_at,
            )
        except psycopg.errors.UniqueViolation:
            # user_code collision (astronomically unlikely at 32**8) or, in
            # principle, a device_code collision. Retry with fresh random
            # values rather than surfacing a 500. Note the uniqueness is
            # across BOTH kinds, which is the whole reason one table.
            device_code = _new_device_code()
            continue
        user_code = candidate
        break
    else:
        raise CliCodeError("could not allocate a unique device code")

    return {
        "device_code": device_code,
        "user_code": user_code,
        "expires_at": expires_at,
        "interval": POLL_INTERVAL_SECONDS,
    }


def approve_cli_code(db: psycopg.Connection, user_code: str, user_id: str) -> str:
    """A signed-in user approves a code they read off their own terminal.
    Returns the credential's id. Raises rather than ever minting a
    credential nobody approved."""
    row = dbmod.fetch_device_code_by_user_code(db, user_code)
    if row is None or row.get("kind") != "cli":
        # A machine code folds into "not found" here on purpose. Telling a
        # caller "that code is real but belongs to the other flow" is a fact
        # they can do nothing with and a guesser can.
        raise CliCodeNotFound(user_code)

    if row["credential_id"] is not None:
        # Already approved — approving twice must not mint a second
        # credential, so this is a no-op returning the existing id.
        return str(row["credential_id"])

    if row["expires_at"] <= datetime.now(timezone.utc):
        raise CliCodeExpired(user_code)

    # Ownership is established here and nowhere else. The label comes from
    # the code row (what the CLI reported about itself), never from the
    # approver's request body.
    with db.transaction():
        credential_id = dbmod.insert_cli_credential(
            db, owner_id=user_id, label=row["hostname"]
        )
        dbmod.mark_cli_device_code_approved(db, user_code, user_id, credential_id)
    return credential_id


def redeem_cli_code(db: psycopg.Connection, device_code: str) -> str | None:
    """The CLI exchanges its device_code for a token. Returns the raw token
    exactly once: the atomic claim in
    ``claim_cli_device_code_for_redemption`` ensures a second call — or a
    concurrent one — gets None instead of a second copy. Returns None
    (never raises) for an unknown, unapproved, expired, or already-redeemed
    code, all indistinguishably."""
    credential_id = dbmod.claim_cli_device_code_for_redemption(db, device_code)
    if credential_id is None:
        return None

    token = new_user_token()
    dbmod.set_cli_credential_token(
        db, credential_id, hash_user_token(token), token[:TOKEN_PREFIX_CHARS]
    )
    return token


def authenticate_cli(
    db: psycopg.Connection, token: str | None
) -> CliCredential | None:
    """Resolve a token to the credential it belongs to. Returns None
    immediately for an unknown token or a revoked credential — revocation
    flips ``status`` in the row this reads, so it takes effect on the very
    next request; there is no cache to expire and no refresh to wait for.
    ``fmu_`` tokens do not expire on their own."""
    if not token:
        return None
    row = dbmod.fetch_cli_credential_by_token_hash(db, hash_user_token(token))
    if row is None:
        return None
    if row["status"] == "revoked":
        return None
    dbmod.touch_cli_credential_last_used(db, str(row["id"]))
    return CliCredential(
        id=str(row["id"]),
        owner_id=str(row["owner_id"]),
        label=row["label"],
        status=row["status"],
        created_at=row.get("created_at"),
        revoked_at=row.get("revoked_at"),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest tests/test_cli_auth.py -v
```

Expected: PASS, all eleven tests.

- [ ] **Step 5: Check the import boundary still holds**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest tests/test_import_boundary.py -v
```

Expected: PASS. `cli_auth.py` imports nothing from `flashruntime` beyond what
the rest of the package already does — if this fails, an import crept in.

- [ ] **Step 6: Commit**

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/cli_auth.py \
        flashml-cloud/apps/api/tests/test_cli_auth.py
git commit -m "feat(api): add the CLI device-code login flow"
```

---

### Task 4: `current_user` accepts `fmu_`

The load-bearing change. One edit; every `browser`-tagged route becomes
CLI-reachable.

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py:861-873` (the
  `current_user` function) and the `flashml_cloud_api.auth` import block near
  the top of the file
- Test: `flashml-cloud/apps/api/tests/test_cli_token_routes.py` (create)

**Interfaces:**
- Consumes: `cli_auth.authenticate_cli`, `auth.looks_like_user_token`.
- Produces: no new names. `current_user`'s signature and return type are
  unchanged — `(request: Request) -> str`, the user id.

- [ ] **Step 1: Write the failing test**

Create `flashml-cloud/apps/api/tests/test_cli_token_routes.py`:

```python
"""What an `fmu_` token can and cannot do over HTTP.

The point of this file is the claim in the design: extending
``current_user`` makes every route already tagged `browser` reachable from
a CLI, and grants nothing beyond what the owner already had. Both halves
need pinning — the second more than the first.
"""
from __future__ import annotations

import uuid

import pytest

from flashml_cloud_api import cli_auth, db as dbmod


@pytest.fixture
def cli_token(client, db, admitted_owner):
    """An `fmu_` token belonging to an admitted account."""
    started = cli_auth.start_cli_code(db, "test-laptop")
    cli_auth.approve_cli_code(db, started["user_code"], admitted_owner)
    token = cli_auth.redeem_cli_code(db, started["device_code"])
    assert token is not None
    return token


def test_an_fmu_token_reaches_a_browser_tagged_route(client, cli_token):
    r = client.get("/v1alpha1/me", headers={"Authorization": f"Bearer {cli_token}"})
    assert r.status_code == 200


def test_an_fmu_token_resolves_to_its_owner_not_someone_else(
    client, cli_token, admitted_owner
):
    r = client.get("/v1alpha1/me", headers={"Authorization": f"Bearer {cli_token}"})
    assert r.json()["id"] == admitted_owner


def test_a_revoked_token_stops_working_on_the_next_request(
    client, db, cli_token, admitted_owner
):
    ok = client.get("/v1alpha1/me", headers={"Authorization": f"Bearer {cli_token}"})
    assert ok.status_code == 200

    rows = dbmod.list_cli_credentials_for_owner(db, admitted_owner)
    assert dbmod.revoke_cli_credential_row(db, str(rows[0]["id"]), admitted_owner)

    after = client.get("/v1alpha1/me", headers={"Authorization": f"Bearer {cli_token}"})
    assert after.status_code == 401


def test_an_unknown_fmu_token_is_401_not_500(client):
    r = client.get(
        "/v1alpha1/me", headers={"Authorization": "Bearer fmu_" + "x" * 43}
    )
    assert r.status_code == 401


def test_an_un_admitted_owners_token_still_hits_the_admission_gate(
    client, db, unadmitted_owner
):
    """The credential grants its owner's access and not one step more."""
    started = cli_auth.start_cli_code(db, "test-laptop")
    cli_auth.approve_cli_code(db, started["user_code"], unadmitted_owner)
    token = cli_auth.redeem_cli_code(db, started["device_code"])

    # /me is open to un-admitted accounts, by design.
    assert client.get(
        "/v1alpha1/me", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 200
    # Anything that creates state is not.
    r = client.post(
        "/v1alpha1/pools",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "nope"},
    )
    assert r.status_code == 403


def test_a_machine_token_still_cannot_reach_a_browser_route(client, machine_token):
    r = client.get("/v1alpha1/me", headers={"Authorization": f"Bearer {machine_token}"})
    assert r.status_code == 401


def test_a_supabase_jwt_still_works_unchanged(client, jwt_headers):
    r = client.get("/v1alpha1/me", headers=jwt_headers)
    assert r.status_code == 200
```

**Fixtures this file needs.** `tests/conftest.py` already provides
`postgres_dsn`. The others (`client`, `db`, `admitted_owner`,
`unadmitted_owner`, `machine_token`, `jwt_headers`) exist in the API test suite
already — find them by running
`grep -rn "def client\|def admitted_owner\|def jwt_headers\|def machine_token" tests/`
and import or re-export from wherever they live. If `unadmitted_owner` does not
exist, add it beside `admitted_owner` as the same fixture without the
`profile_is_admitted` update.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest tests/test_cli_token_routes.py -v
```

Expected: FAIL — `/v1alpha1/me` answers 401 for an `fmu_` token, because
`current_user` hands it to the JWT decoder, which rejects it.

- [ ] **Step 3: Write the implementation**

In `flashml_cloud_api/app.py`, add to the `flashml_cloud_api.auth` import block:

```python
from flashml_cloud_api.auth import (
    # ... existing names, unchanged ...
    looks_like_user_token,
)
```

and add `cli_auth` to the module imports:

```python
from flashml_cloud_api import cli_auth
```

Then replace `current_user` (currently at `app.py:861`):

```python
    def current_user(request: Request) -> str:
        """The signed-in user id, from either a verified Supabase JWT (a
        browser) or an `fmu_` developer token (a CLI, or the MCP server
        built on it). A machine token is rejected without ever reaching
        either.

        THE THREE KINDS NEVER SHARE A CODE PATH. Each is selected by its
        prefix before any work happens, for the reason ``machine_caller``
        documents at length: opening a database connection before checking
        the credential's shape makes every anonymous request cost a
        Postgres connection, which is cheap for an attacker and expensive
        for us. It is also why a browser JWT is never hashed and looked up
        as though it might be a token.

        An `fmu_` token grants EXACTLY its owner's access. This function
        returns a user id and nothing else, so every gate layered on top —
        ``admitted_user``, ``admin_user``, every per-resource ownership
        check — applies to a CLI caller identically and with no second
        implementation to keep aligned.
        """
        token = _bearer(request)
        if token is None or looks_like_machine_token(token):
            raise HTTPException(status_code=401, detail="sign-in required")

        if looks_like_user_token(token):
            db = request.app.state.connect()
            try:
                credential = cli_auth.authenticate_cli(db, token)
            finally:
                db.close()
            if credential is None:
                # Unknown token and revoked credential give the same answer,
                # on purpose — same doctrine as ``machine_caller``.
                raise HTTPException(status_code=401, detail="sign-in required")
            return credential.owner_id

        try:
            return verify_supabase_jwt(token, settings)
        except AuthError:
            # The reason is not reported: "expired" vs "bad signature" is an
            # oracle, and the caller can do nothing different either way.
            raise HTTPException(status_code=401, detail="sign-in required") from None
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest tests/test_cli_token_routes.py -v
```

Expected: PASS, all seven.

- [ ] **Step 5: Run the whole suite — this change touches every route**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest -q
```

Expected: PASS with no regressions. `test_auth.py`, `test_enrolment.py`,
`test_jobs.py`, `test_pools_api.py` and `test_agent_proxy.py` all exercise
`current_user` indirectly; a failure in any of them means the JWT or machine
path moved.

- [ ] **Step 6: Commit**

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/app.py \
        flashml-cloud/apps/api/tests/test_cli_token_routes.py
git commit -m "feat(api): accept fmu_ developer tokens in current_user"
```

---

### Task 5: The device-code routes learn `kind`

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py:998-1053` (the
  `device_code` and `device_token` routes) and `app.py:1258-1336` (`approve`)
- Test: `flashml-cloud/apps/api/tests/test_cli_token_routes.py` (append)

**Interfaces:**
- Consumes: Task 3's `cli_auth.start_cli_code`, `approve_cli_code`,
  `redeem_cli_code`, `CliCodeNotFound`, `CliCodeExpired`.
- Produces (HTTP contract, relied on by Plan 2's CLI):
  - `POST /v1alpha1/device/code` with `{"kind": "cli", "label": "<hostname>"}`
    → `{device_code, user_code, verification_uri, interval, expires_at}`
  - `POST /v1alpha1/device/token` → `{"token": "fmu_…", "token_type": "cli"}`
  - `POST /v1alpha1/device/approve` with a CLI `user_code`
    → `{"credential_id": "<uuid>", "kind": "cli", "status": "approved"}`
  - The machine responses are unchanged, and still carry `machine_id` with no
    `kind` key, so no existing client has to be updated in lockstep.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_token_routes.py`:

```python
def test_the_cli_device_flow_end_to_end_over_http(client, jwt_headers):
    start = client.post(
        "/v1alpha1/device/code", json={"kind": "cli", "label": "test-laptop"}
    )
    assert start.status_code == 200
    body = start.json()
    assert body["user_code"] and body["device_code"]
    assert body["verification_uri"].endswith("/activate")

    pending = client.post(
        "/v1alpha1/device/token", json={"device_code": body["device_code"]}
    )
    assert pending.status_code == 400
    assert pending.json()["error"] == "authorization_pending"

    approved = client.post(
        "/v1alpha1/device/approve",
        headers=jwt_headers,
        json={"user_code": body["user_code"]},
    )
    assert approved.status_code == 200
    assert approved.json()["kind"] == "cli"
    assert approved.json()["credential_id"]

    redeemed = client.post(
        "/v1alpha1/device/token", json={"device_code": body["device_code"]}
    )
    assert redeemed.status_code == 200
    assert redeemed.json()["token"].startswith("fmu_")
    assert redeemed.json()["token_type"] == "cli"


def test_a_cli_start_does_not_require_a_node_id(client):
    """The machine route demands one. A CLI has no node."""
    r = client.post("/v1alpha1/device/code", json={"kind": "cli"})
    assert r.status_code == 200


def test_a_machine_start_still_demands_a_valid_node_id(client):
    assert client.post("/v1alpha1/device/code", json={}).status_code == 400
    assert client.post(
        "/v1alpha1/device/code", json={"node_id": "bad id!"}
    ).status_code == 400


def test_an_unknown_kind_is_refused_rather_than_guessed(client):
    r = client.post("/v1alpha1/device/code", json={"kind": "printer"})
    assert r.status_code == 400


def test_approving_an_expired_cli_code_is_410(client, db, jwt_headers):
    from datetime import datetime, timedelta, timezone

    start = client.post("/v1alpha1/device/code", json={"kind": "cli"}).json()
    with db.cursor() as cur:
        cur.execute(
            "update public.device_codes set expires_at = %s where device_code = %s",
            (
                datetime.now(timezone.utc) - timedelta(seconds=1),
                start["device_code"],
            ),
        )
    r = client.post(
        "/v1alpha1/device/approve",
        headers=jwt_headers,
        json={"user_code": start["user_code"]},
    )
    assert r.status_code == 410


def test_a_pool_id_on_a_cli_approval_is_refused(client, jwt_headers):
    """pool_id binds a MACHINE to a pool. A credential is not placed on, so
    silently ignoring it would accept a request that did not do what it
    said."""
    start = client.post("/v1alpha1/device/code", json={"kind": "cli"}).json()
    r = client.post(
        "/v1alpha1/device/approve",
        headers=jwt_headers,
        json={"user_code": start["user_code"], "pool_id": str(uuid.uuid4())},
    )
    assert r.status_code == 400
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest tests/test_cli_token_routes.py -k "device_flow or cli_start or unknown_kind or expired_cli or pool_id_on_a_cli" -v
```

Expected: FAIL — the first assertion, 400 not 200, because `device_code` reads
`node_id` unconditionally and `kind: "cli"` supplies none.

- [ ] **Step 3: Update `POST /v1alpha1/device/code`**

Replace the body of `device_code` (`app.py:998`):

```python
    @app.post("/v1alpha1/device/code", tags=["enrolment"])
    async def device_code(
        request: Request, db: psycopg.Connection = Depends(db_conn)
    ):
        """Start a device-code flow, for a machine enrolling or a CLI
        logging in. ``kind`` selects which; it defaults to ``machine``, so
        every agent already in the field keeps working byte-for-byte."""
        payload = await _json_object(request)
        kind = payload.get("kind", "machine")
        if kind not in ("machine", "cli"):
            # Refused, not coerced to the default: a typo'd kind must not
            # silently start the wrong flow and hand back the wrong token.
            raise HTTPException(status_code=400, detail="unknown kind")

        if kind == "cli":
            started = cli_auth.start_cli_code(db, _opt_str(payload.get("label")))
        else:
            node_id = payload.get("node_id")
            if not valid_node_id(node_id):
                # See NODE_ID_RE: this value later becomes a header value on
                # a request carrying the operator credential.
                raise HTTPException(status_code=400, detail="invalid node_id")
            started = enrolment.start_device_code(
                db,
                node_id,
                _opt_str(payload.get("hostname")),
                _opt_str(payload.get("platform")),
            )

        base = settings.console_url.rstrip("/")
        # /activate, not /enrol. The console has never served /enrol —
        # apps/web/app/activate/page.tsx is the page — so this URL was
        # printed on the volunteer's terminal, typed into a browser, and
        # 404'd, at the one moment they are most likely to give up. Nothing
        # caught it because each side was self-consistent: the API had a
        # route name, the web app had a page, and no test compared them.
        # tests/test_device_code.py now pins this against the filesystem.
        return {
            "device_code": started["device_code"],
            "user_code": started["user_code"],
            "verification_uri": f"{base}/activate" if base else "/activate",
            "interval": started["interval"],
            "expires_at": started["expires_at"].isoformat(),
        }
```

- [ ] **Step 4: Update `POST /v1alpha1/device/token`**

Replace the body of `device_token` (`app.py:1030`):

```python
    @app.post("/v1alpha1/device/token", tags=["enrolment"])
    async def device_token(
        request: Request, db: psycopg.Connection = Depends(db_conn)
    ):
        """Redeem a device_code. Which flow it belongs to is read off the
        stored row, never off the request — a caller holding a machine's
        device_code must not be able to ask for a user token with it."""
        payload = await _json_object(request)
        device_code_value = payload.get("device_code")
        if not isinstance(device_code_value, str) or not device_code_value:
            raise HTTPException(status_code=400, detail="device_code required")

        row = dbmod.fetch_device_code(db, device_code_value)
        kind = row["kind"] if row else "machine"
        if kind == "cli":
            token = cli_auth.redeem_cli_code(db, device_code_value)
            token_type = "cli"
        else:
            token = enrolment.redeem_device_code(db, device_code_value)
            token_type = "machine"

        if token is None:
            # RFC 8628's polling shape. Unknown / unapproved / expired /
            # already-redeemed are one indistinguishable answer, so this
            # cannot be used to learn which codes exist. An unknown code
            # takes the machine branch above and lands here identically.
            return Response(
                content=json.dumps(
                    {"error": "authorization_pending",
                     "interval": enrolment.POLL_INTERVAL_SECONDS}
                ),
                status_code=400,
                media_type="application/json",
            )
        return {"token": token, "token_type": token_type}
```

Add the lookup to `flashml_cloud_api/db.py`, beside
`fetch_device_code_by_user_code`:

```python
def fetch_device_code(
    db: psycopg.Connection, device_code: str
) -> dict[str, Any] | None:
    """Read a device code by its long half. Used only to learn which flow
    a code belongs to before redemption — the redemption itself stays the
    atomic claim, so this read cannot introduce a race: a code that changes
    hands between this SELECT and that UPDATE still redeems exactly once,
    and reading the wrong kind would only route it to a claim query whose
    ``kind =`` filter then matches nothing."""
    with db.cursor() as cur:
        cur.execute(
            "select * from public.device_codes where device_code = %s",
            (device_code,),
        )
        return cur.fetchone()
```

- [ ] **Step 5: Update `POST /v1alpha1/device/approve`**

Insert this block into `approve` (`app.py:1258`), immediately after the
`user_code` validation and **before** the `pool_id` block:

```python
        # Which flow is being approved is read off the stored row, not the
        # request body — the approver types a code, and nothing else about
        # it is theirs to assert.
        code_row = dbmod.fetch_device_code_by_user_code(db, user_code.strip().upper())
        if code_row is not None and code_row.get("kind") == "cli":
            if payload.get("pool_id") is not None:
                # pool_id binds a MACHINE to a pool. A credential is never
                # placed on, so accepting and ignoring it would confirm a
                # request that did not do what it said.
                raise HTTPException(
                    status_code=400, detail="pool_id does not apply to a CLI login"
                )
            dbmod.upsert_profile(db, user_id)
            try:
                credential_id = cli_auth.approve_cli_code(
                    db, user_code.strip().upper(), user_id
                )
            except cli_auth.CliCodeNotFound:
                raise HTTPException(status_code=404, detail="unknown code") from None
            except cli_auth.CliCodeExpired:
                raise HTTPException(status_code=410, detail="code expired") from None
            return {
                "credential_id": str(credential_id),
                "kind": "cli",
                "status": "approved",
            }
```

Then, at the end of the machine path, change the existing return so the console
can branch on one key in both cases:

```python
        return {"machine_id": str(machine_id), "kind": "machine", "status": "approved"}
```

- [ ] **Step 6: Run the tests**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest tests/test_cli_token_routes.py tests/test_enrolment.py -v
```

Expected: PASS. `test_enrolment.py` passing unchanged is the evidence that the
machine flow was not disturbed.

- [ ] **Step 7: Run the whole suite and commit**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest -q
git add flashml-cloud/apps/api/flashml_cloud_api/app.py \
        flashml-cloud/apps/api/flashml_cloud_api/db.py \
        flashml-cloud/apps/api/tests/test_cli_token_routes.py
git commit -m "feat(api): route the device-code flow by kind"
```

---

### Task 6: List and revoke routes

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py` (add after the
  `revoke` machine route, which ends around `app.py:1361`)
- Test: `flashml-cloud/apps/api/tests/test_cli_token_routes.py` (append)

**Interfaces:**
- Consumes: `dbmod.list_cli_credentials_for_owner`,
  `dbmod.revoke_cli_credential_row`.
- Produces (HTTP contract, consumed by Task 7):
  - `GET /v1alpha1/cli-credentials` → a list of
    `{id, label, status, token_prefix, last_used_at, created_at, revoked_at}`
  - `POST /v1alpha1/cli-credentials/{id}/revoke` → `{"revoked": true}` or 404

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_token_routes.py`:

```python
def test_listing_shows_your_credentials_and_never_a_token_hash(
    client, jwt_headers, cli_token
):
    r = client.get("/v1alpha1/cli-credentials", headers=jwt_headers)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    row = rows[0]
    assert row["label"] == "test-laptop"
    assert row["status"] == "active"
    assert row["token_prefix"].startswith("fmu_")
    assert "token_hash" not in row


def test_listing_is_owner_scoped(client, jwt_headers, other_jwt_headers, cli_token):
    mine = client.get("/v1alpha1/cli-credentials", headers=jwt_headers).json()
    theirs = client.get(
        "/v1alpha1/cli-credentials", headers=other_jwt_headers
    ).json()
    assert len(mine) >= 1
    assert theirs == []


def test_revoking_your_own_credential_kills_its_token(client, jwt_headers, cli_token):
    rows = client.get("/v1alpha1/cli-credentials", headers=jwt_headers).json()
    r = client.post(
        f"/v1alpha1/cli-credentials/{rows[0]['id']}/revoke", headers=jwt_headers
    )
    assert r.status_code == 200
    assert r.json()["revoked"] is True
    assert client.get(
        "/v1alpha1/me", headers={"Authorization": f"Bearer {cli_token}"}
    ).status_code == 401


def test_revoking_a_credential_you_do_not_own_is_404(
    client, jwt_headers, other_jwt_headers, cli_token
):
    rows = client.get("/v1alpha1/cli-credentials", headers=jwt_headers).json()
    r = client.post(
        f"/v1alpha1/cli-credentials/{rows[0]['id']}/revoke",
        headers=other_jwt_headers,
    )
    assert r.status_code == 404


def test_revoking_a_nonsense_id_is_404_not_500(client, jwt_headers):
    r = client.post("/v1alpha1/cli-credentials/not-a-uuid/revoke", headers=jwt_headers)
    assert r.status_code == 404
```

`other_jwt_headers` is a second signed-in account. If the suite has no such
fixture, add one beside `jwt_headers` for a different `sub`.

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest tests/test_cli_token_routes.py -k credential -v
```

Expected: FAIL — 404 from FastAPI, the route does not exist.

- [ ] **Step 3: Write the routes**

Add to `app.py` after the machine `revoke` route:

```python
    @app.get("/v1alpha1/cli-credentials", tags=["browser"])
    async def list_cli_credentials(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Every CLI credential this account holds. ``current_user``, not
        ``admitted_user``: an account still waiting on approval must be
        able to see and revoke a credential it has already minted."""
        return [_jsonable(r) for r in dbmod.list_cli_credentials_for_owner(db, user_id)]

    @app.post("/v1alpha1/cli-credentials/{credential_id}/revoke", tags=["browser"])
    async def revoke_cli_credential(
        credential_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Revoke a credential. 404 — not 403 — when it belongs to someone
        else or does not exist, indistinguishably, so this cannot be used to
        learn which credential ids are real. Takes effect on the revoked
        token's very next request: ``authenticate_cli`` reads ``status`` on
        every call and there is no cache in front of it."""
        if not dbmod.revoke_cli_credential_row(db, credential_id, user_id):
            raise HTTPException(status_code=404, detail="unknown credential")
        return {"revoked": True}
```

- [ ] **Step 4: Run the tests**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest tests/test_cli_token_routes.py -v
```

Expected: PASS.

- [ ] **Step 5: Run the whole suite and commit**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest -q
git add flashml-cloud/apps/api/flashml_cloud_api/app.py \
        flashml-cloud/apps/api/tests/test_cli_token_routes.py
git commit -m "feat(api): list and revoke CLI credentials"
```

---

### Task 7: Console client functions

**Files:**
- Modify: `flashml-cloud/apps/web/lib/cloud-api.ts`
- Create: `flashml-cloud/apps/web/lib/cli-credential-status.ts`
- Test: `flashml-cloud/apps/web/lib/cli-credential-status.test.ts` (create),
  `flashml-cloud/apps/web/lib/cloud-api.test.ts` (append)

**Interfaces:**
- Consumes: Task 6's two routes; Task 5's widened approve response.
- Produces, from `@/lib/cloud-api`:
  - `interface CliCredential { id: string; label: string | null; status: "active" | "revoked"; token_prefix: string; last_used_at: string | null; created_at: string; revoked_at: string | null }`
  - `listCliCredentials(): Promise<CliCredential[]>`
  - `revokeCliCredential(id: string): Promise<{ revoked: boolean }>`
  - `ApproveDeviceCodeResult` widened to
    `{ status: string; kind?: "machine" | "cli"; machine_id?: string; credential_id?: string }`
- Produces, from `@/lib/cli-credential-status`:
  - `credentialLabel(c: CliCredential): string`
  - `credentialBadge(c: CliCredential): { label: string; tone: "active" | "revoked" }`

- [ ] **Step 1: Write the failing test**

Create `flashml-cloud/apps/web/lib/cli-credential-status.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { credentialBadge, credentialLabel } from "./cli-credential-status";
import type { CliCredential } from "./cloud-api";

const base: CliCredential = {
  id: "c1",
  label: "phong's laptop",
  status: "active",
  token_prefix: "fmu_abc12345",
  last_used_at: null,
  created_at: "2026-08-10T00:00:00Z",
  revoked_at: null,
};

describe("credentialLabel", () => {
  it("uses the label the CLI reported", () => {
    expect(credentialLabel(base)).toBe("phong's laptop");
  });

  it("falls back to the token prefix rather than showing an empty row", () => {
    expect(credentialLabel({ ...base, label: null })).toBe("fmu_abc12345…");
  });

  it("falls back again when even the prefix is missing", () => {
    expect(credentialLabel({ ...base, label: null, token_prefix: "" })).toBe(
      "unnamed credential"
    );
  });
});

describe("credentialBadge", () => {
  it("marks an active credential active", () => {
    expect(credentialBadge(base)).toEqual({ label: "Active", tone: "active" });
  });

  it("marks a revoked credential revoked", () => {
    expect(credentialBadge({ ...base, status: "revoked" })).toEqual({
      label: "Revoked",
      tone: "revoked",
    });
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd flashml-cloud/apps/web
npx vitest run lib/cli-credential-status.test.ts
```

Expected: FAIL — cannot resolve `./cli-credential-status`.

- [ ] **Step 3: Write the client additions**

Add to `flashml-cloud/apps/web/lib/cloud-api.ts`, near the `Machine` interface:

```ts
/** A developer CLI credential. Mirrors `public.cli_credentials`, minus
 * `token_hash` — the API's column allowlist never sends it and this type
 * must not invite anyone to look for it. */
export interface CliCredential {
  id: string;
  label: string | null;
  status: "active" | "revoked";
  /** The first 12 characters of the raw token, kept so a person can tell
   * two credentials apart. Not a secret and not sufficient to authenticate. */
  token_prefix: string;
  last_used_at: string | null;
  created_at: string;
  revoked_at: string | null;
}
```

Widen `ApproveDeviceCodeResult`:

```ts
/** `POST /v1alpha1/device/approve`. One route, two flows: `kind` says
 * which, and exactly one of `machine_id` / `credential_id` is present.
 * `kind` is optional only because a response from an API deployed before
 * the CLI flow existed carries `machine_id` and no `kind` — treat its
 * absence as "machine". */
export interface ApproveDeviceCodeResult {
  status: string;
  kind?: "machine" | "cli";
  machine_id?: string;
  credential_id?: string;
}
```

And beside `listMachines` / `revokeMachine`:

```ts
export function listCliCredentials(): Promise<CliCredential[]> {
  return request<CliCredential[]>("/v1alpha1/cli-credentials");
}

export function revokeCliCredential(
  credentialId: string
): Promise<{ revoked: boolean }> {
  return request<{ revoked: boolean }>(
    `/v1alpha1/cli-credentials/${encodeURIComponent(credentialId)}/revoke`,
    { method: "POST" }
  );
}
```

Create `flashml-cloud/apps/web/lib/cli-credential-status.ts`:

```ts
import type { CliCredential } from "./cloud-api";

/** What to call a credential in a list. The label is whatever the CLI
 * reported about the machine it ran on, and it is optional all the way
 * down — a caller can start a device code with no label at all — so this
 * degrades twice rather than rendering a blank row that looks like a bug. */
export function credentialLabel(c: CliCredential): string {
  if (c.label && c.label.trim()) return c.label;
  if (c.token_prefix) return `${c.token_prefix}…`;
  return "unnamed credential";
}

export function credentialBadge(c: CliCredential): {
  label: string;
  tone: "active" | "revoked";
} {
  return c.status === "revoked"
    ? { label: "Revoked", tone: "revoked" }
    : { label: "Active", tone: "active" };
}
```

- [ ] **Step 4: Run the tests**

```bash
cd flashml-cloud/apps/web
npx vitest run
npx tsc --noEmit
```

Expected: both PASS. `tsc` matters here — widening `ApproveDeviceCodeResult`
from a required `machine_id` to an optional one will break any existing caller
that reads it unguarded, and that break is the point: Task 9 fixes it.

If `tsc` reports an error in `app/(console)/activate/page.tsx`, leave it —
Task 9 is where it is fixed. If it reports one anywhere else, fix it there
before committing.

- [ ] **Step 5: Commit**

```bash
git add flashml-cloud/apps/web/lib/cloud-api.ts \
        flashml-cloud/apps/web/lib/cli-credential-status.ts \
        flashml-cloud/apps/web/lib/cli-credential-status.test.ts
git commit -m "feat(web): add the CLI credential client and presentation"
```

---

### Task 8: The `/account/cli` page

**Files:**
- Create: `flashml-cloud/apps/web/app/(console)/account/cli/page.tsx`
- Modify: whichever file renders the account navigation — find it with
  `grep -rn "account/machines" flashml-cloud/apps/web/components flashml-cloud/apps/web/app`
- Test: covered by Task 7's unit tests plus the manual check below. Follow the
  suite's existing convention: page components are not unit-tested, their
  extractable logic is.

**Interfaces:**
- Consumes: `listCliCredentials`, `revokeCliCredential`, `CliCredential`,
  `NotAuthenticated` from `@/lib/cloud-api`; `credentialLabel`,
  `credentialBadge` from `@/lib/cli-credential-status`; `relativeTime` from
  `@/lib/machine-status`.
- Produces: a route at `/account/cli`.

- [ ] **Step 1: Read the page this one mirrors**

```bash
cd flashml-cloud/apps/web
cat 'app/(console)/account/machines/page.tsx'
```

Copy its structure exactly: the `useCallback` loader, the
`"loading" | "ready" | "error"` state machine, the `NotAuthenticated` →
`router.push("/sign-in?next=…")` branch, the `AlertDialog` confirm before a
destructive action, and `toast` on success. Deviating from it here would give
the console two different answers to the same problems.

- [ ] **Step 2: Write the page**

Create `flashml-cloud/apps/web/app/(console)/account/cli/page.tsx`. It must:

1. Load with `listCliCredentials()` on mount and poll every `15_000` ms, the
   same `POLL_MS` the machines page uses.
2. On `NotAuthenticated`, `router.push("/sign-in?next=/account/cli")` — its own
   path, so signing in returns here and not to a redirect stub.
3. Render one row per credential: `credentialLabel(c)`, a badge from
   `credentialBadge(c)`, `relativeTime(c.last_used_at)` (or "never used"), and
   a Revoke button.
4. Guard Revoke behind `AlertDialog`, with body text naming the consequence:
   *"Any program signed in with this credential stops working immediately.
   Running jobs are unaffected."*
5. Call `revokeCliCredential(c.id)`, then reload, then `toast.success`.
6. Show an empty state that tells a first-time visitor what to do:
   *"No CLI credentials yet. Run `flashml login` and approve the code it
   prints."*

- [ ] **Step 3: Add the navigation entry**

Add a link to `/account/cli` beside the existing `/account/machines` link,
labelled **CLI access**.

- [ ] **Step 4: Verify it compiles and renders**

```bash
cd flashml-cloud/apps/web
npx tsc --noEmit
npx next lint
```

Then, from the repo root, with the stack up:

```bash
cd flashml-cloud
./scripts/dev.sh --all
```

Visit `http://localhost:3000/account/cli` signed in. Expected: the empty state.
Then, in another terminal:

```bash
curl -s localhost:8000/v1alpha1/device/code \
  -H 'content-type: application/json' \
  -d '{"kind":"cli","label":"manual test"}'
```

Approve the printed `user_code` at `/activate`, reload `/account/cli`, and
confirm the row appears with the label "manual test". Revoke it and confirm the
badge flips.

Use `localhost`, not `127.0.0.1` — the console's CORS origin is configured for
the former.

- [ ] **Step 5: Commit**

```bash
git add 'flashml-cloud/apps/web/app/(console)/account/cli/page.tsx'
git add <the navigation file you modified>
git commit -m "feat(web): add the CLI access page"
```

---

### Task 9: `/activate` branches on what it approved

**Files:**
- Modify: `flashml-cloud/apps/web/app/(console)/activate/page.tsx`
- Modify: `flashml-cloud/apps/web/lib/activate-errors.ts` if the copy lives
  there — check first with
  `grep -n "machine" flashml-cloud/apps/web/lib/activate-errors.ts`
- Test: `flashml-cloud/apps/web/lib/activate-errors.test.ts` (append, if the
  copy lives there)

**Interfaces:**
- Consumes: Task 7's widened `ApproveDeviceCodeResult`.
- Produces: no new exported names.

- [ ] **Step 1: Read the page and find every use of `machine_id`**

```bash
cd flashml-cloud/apps/web
grep -n "machine_id\|machine\b" 'app/(console)/activate/page.tsx'
npx tsc --noEmit    # the errors left by Task 7 are the checklist
```

- [ ] **Step 2: Write the failing check**

If the success copy lives in `lib/activate-errors.ts` (or a sibling), add a test
there for a `kind: "cli"` result. If it is inline JSX, the `tsc` errors from
Task 7 are the failing check — record them before fixing.

- [ ] **Step 3: Branch the page**

Three things must differ, because the two approvals grant different powers and
a person must be told which one they just granted:

1. **The pool picker stays as it is.** A CLI credential is never placed on, and
   Task 5 refuses a `pool_id` on a CLI approval with a 400. The page cannot know
   the kind before it approves, so do not try to hide the picker: leave it, and
   rely on the existing client behaviour of omitting `pool_id` from the body
   entirely unless a pool was picked (`approveDeviceCode` in `cloud-api.ts`
   already does exactly this, and its docstring says why). A person who picks a
   pool and then types a CLI code gets the 400, which is the correct answer —
   they asked for something that does not exist. Surface that 400's `detail`
   verbatim rather than mapping it to a generic failure.
2. **The success copy differs.** Machine: *"This machine can now run jobs for
   you."* CLI: *"This program can now submit jobs as you. Manage it under
   Account → CLI access."*
3. **The follow-on link differs.** Machine → `/account/machines`.
   CLI → `/account/cli`.

Branch on `result.kind === "cli"`, treating an absent `kind` as `"machine"` —
the reason is on the type in Task 7.

- [ ] **Step 4: Verify**

```bash
cd flashml-cloud/apps/web
npx tsc --noEmit
npx vitest run
npx next lint
```

Expected: all three clean, with no remaining `ApproveDeviceCodeResult` errors.

Then re-run the manual check from Task 8 Step 4 and confirm `/activate` shows
the CLI copy and links to `/account/cli`. Enrol a machine the old way as well
and confirm that path still shows the machine copy and links to
`/account/machines`.

- [ ] **Step 5: Commit**

```bash
git add 'flashml-cloud/apps/web/app/(console)/activate/page.tsx'
git add flashml-cloud/apps/web/lib/activate-errors.ts \
        flashml-cloud/apps/web/lib/activate-errors.test.ts
git commit -m "feat(web): tell people which kind of access they just granted"
```

---

## Done means

All nine tasks committed, and:

```bash
cd flashml-cloud/apps/api && .venv/bin/pytest -q     # record the count
cd ../web && npx vitest run && npx tsc --noEmit && npx next lint
```

plus the manual loop, end to end, against `./scripts/dev.sh --all`:

```bash
curl -s localhost:8000/v1alpha1/device/code \
  -H 'content-type: application/json' -d '{"kind":"cli","label":"laptop"}'
# approve the user_code at localhost:3000/activate
curl -s localhost:8000/v1alpha1/device/token \
  -H 'content-type: application/json' -d '{"device_code":"<...>"}'
# -> {"token":"fmu_...","token_type":"cli"}
curl -s localhost:8000/v1alpha1/jobs -H "Authorization: Bearer fmu_..."
# -> the caller's jobs, from a route tagged `browser`, with no browser
```

That last command is the whole plan: a program, holding a credential of its
own, reading a route that until now only a signed-in browser could reach.

Then log it in `PROGRESS.md` following the protocol at the top of that file —
evidence with numbers, root causes, and the single most useful next action
(**write Plan 2: the client core and CLI**).

---

## Not in this plan

From the spec, deliberately deferred:

- `POST /v1alpha1/preflight` (spec §5) and `POST /v1alpha1/jobs/from-upload`
  (spec §7) — both land in Plan 2, where there is a client to call them.
- The `flashml` package itself (spec §4) — Plan 2.
- Everything MCP (spec §6) — Plan 3.
- Rate limiting on authenticated routes (spec §10.3) — flagged, not designed.
- Whether the event ledger carries enough to debug a job (spec §10.1) — must be
  resolved before Plan 3 is written, and does not block this plan.
