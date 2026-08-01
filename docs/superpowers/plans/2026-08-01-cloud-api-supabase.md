# Cloud API + Supabase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Real accounts. A person signs in with Google, enrols a machine from any browser, and submits a job that runs on the pool — with the coordinator no longer reachable from the internet.

**Architecture:** Supabase provides Auth (Google) and Postgres. The cloud API (`flashml-cloud/apps/api`) becomes the **only** public backend: it verifies Supabase JWTs from browsers, verifies machine tokens from agents, and forwards agent traffic to the now-private coordinator using an operator credential plus an asserted node identity. The coordinator keeps enforcing lease scoping against that asserted identity, so Plan 2's guarantees survive the indirection.

**Tech Stack:** Supabase (Auth, Postgres 17, RLS), FastAPI, PyJWT, httpx, pytest.

This is **Plan 3 of 7** for M1, implementing §3, §4 and §5.1–5.2 of
`docs/superpowers/specs/2026-07-31-deployed-multi-user-poc-design.md`.

**Supabase target:** project `flashml-poc`, ref **`yualksqjjvlfscbbsygq`**,
URL `https://yualksqjjvlfscbbsygq.supabase.co`. **Never** migrate
`sgyrzypimyullipjxgvo` or `ohqkajtzefseyrafzbfj` — they are a different
product (`M1_DECISIONS.md` D13).

---

## The delegation decision this plan settles

Plan 2 authenticates agents *at the coordinator* with static
`FLASHML_NODE_TOKENS`. That cannot survive self-service enrolment: machines
appear at runtime, and the coordinator has no database.

The spec (§3.2) makes the coordinator a **private** service with the cloud API
as the only public door. So agents will talk to the API, and the API forwards.
The naive forwarding — API holds one operator token, uses it for everything —
would **destroy Plan 2's lease scoping**, because every write would arrive as an
unscoped operator.

**Decision: operator-asserted node identity.** The API forwards with its
operator token *plus* a header naming the machine it is acting for. The
coordinator accepts that header **only** from an operator credential, and then
authorizes exactly as before — the write must fall inside a live lease held by
that node.

- A volunteer cannot use it: the header is ignored unless the caller is an operator.
- Lease scoping is unchanged: the coordinator still consults `live_leases_for_node`.
- The API stays the only place that knows about accounts.

The alternative — the API dynamically registering per-machine tokens into the
coordinator — would give the coordinator a second source of truth about
identity and a cache to invalidate on revocation. Rejected for that reason.

## Global Constraints

- **Supabase project is `yualksqjjvlfscbbsygq` and nothing else.**
- **Database access is API-only.** RLS is enabled with deny-by-default policies for the `anon` and `authenticated` roles, so a browser holding a valid JWT still cannot read Postgres directly. Every read goes through the API, which filters on `owner_id`.
- **Never trust a client-supplied identity.** `node_id` comes from the machine token; `user_id` comes from the verified JWT `sub`. Request bodies are not authoritative — this is the rule Plan 2 learned the hard way on `claim`.
- **Secrets never enter git.** The service-role key and JWT secret live in env only. `flashruntime/scripts/audit_secrets.sh` must stay CLEAN; `flashml-cloud` is private but the rule holds.
- Reads of *artifacts* stay open at the coordinator; the API scopes reads by job ownership.
- flashml-cloud imports only `flashruntime.protocol` from the runtime (`CLAUDE.md` dependency rule).
- **Baselines — do not reduce:** flashruntime **475**, flashnode **85 passed 1 skipped**, e2e **15**, flashml-cloud API tests (currently minimal — record the number before you start).
- Run the API's tests from `flashml-cloud/apps/api` with its own `.venv`.

## File Structure

| File | Responsibility |
|---|---|
| `apps/api/migrations/0001_initial.sql` (new) | The schema + RLS, checked in. Applied via Supabase MCP; the file is the reviewable artifact. |
| `apps/api/flashml_cloud_api/settings.py` (new) | Env config: Supabase URL/keys, coordinator URL, operator token. Fails loudly on absence in production mode. |
| `apps/api/flashml_cloud_api/auth.py` (new) | Supabase JWT verification → `user_id`; machine-token verification → `machine`. No FastAPI. |
| `apps/api/flashml_cloud_api/db.py` (new) | Thin Postgres access. Every query takes an explicit owner filter. |
| `apps/api/flashml_cloud_api/enrolment.py` (new) | Device-code flow: issue, approve, redeem. |
| `apps/api/flashml_cloud_api/app.py` (modify) | Wire routes; agent proxy with delegation header. |
| `flashruntime/flashruntime/service/modea.py` (modify) | Accept `X-FlashML-On-Behalf-Of` from operator credentials only. |

---

### Task 1: Schema and RLS

**Files:**
- Create: `apps/api/migrations/0001_initial.sql`
- Test: `apps/api/tests/test_schema.py`

**Interfaces:**
- Produces tables `profiles`, `machines`, `device_codes`, `jobs`, `contributions` in `public`, all with RLS enabled and **no** policy granting `anon`/`authenticated` access.

Schema per spec §4. Key columns:
- `profiles(id uuid pk → auth.users, display_name, github_login, is_host bool, is_developer bool, created_at)`
- `machines(id uuid pk, owner_id uuid → profiles, node_id text unique, name, platform, capabilities jsonb, token_hash text, token_prefix text, status text check in ('pending','active','revoked'), last_seen_at, created_at, revoked_at)`
- `device_codes(device_code text pk, user_code text unique, node_id text, hostname text, platform text, machine_id uuid null, approved_by uuid null, expires_at, consumed_at)`
- `jobs(id text pk, owner_id uuid → profiles, name, source jsonb, spec jsonb, status text, created_at, finished_at)`
- `contributions(id uuid pk, machine_id uuid → machines, job_id text, task_id text, accepted_at, duration_s)`

**`token_hash` stores a hash, never the token.** Use `encode(digest(token,'sha256'),'hex')` semantics — the API hashes before writing; the DB never sees plaintext.

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_schema.py
"""The migration file is the reviewable artifact; these tests pin the
invariants that matter rather than re-describing every column."""
import pathlib
import re

SQL = (pathlib.Path(__file__).parent.parent / "migrations" / "0001_initial.sql").read_text()

TABLES = ["profiles", "machines", "device_codes", "jobs", "contributions"]


def test_every_table_is_created():
    for t in TABLES:
        assert re.search(rf"create table\s+(if not exists\s+)?public\.{t}\b", SQL, re.I), t


def test_rls_is_enabled_on_every_table():
    for t in TABLES:
        assert re.search(rf"alter table\s+public\.{t}\s+enable row level security", SQL, re.I), t


def test_no_policy_grants_anon_or_authenticated():
    """Database access is API-only. A browser holding a valid JWT must not be
    able to read Postgres directly — every read goes through the API, which
    filters on owner_id. A policy naming these roles would silently open that."""
    for role in ("anon", "authenticated"):
        assert not re.search(rf"create policy.*\bto\s+{role}\b", SQL, re.I | re.S), role


def test_machines_store_a_hash_not_a_token():
    assert "token_hash" in SQL
    assert not re.search(r"\btoken\s+text", SQL, re.I), "raw token column present"


def test_node_id_is_unique():
    assert re.search(r"node_id\s+text\s+(not null\s+)?unique", SQL, re.I)


def test_machine_status_is_constrained():
    assert re.search(r"status.*check.*pending.*active.*revoked", SQL, re.I | re.S)


def test_owner_columns_cascade_from_profiles():
    assert SQL.lower().count("references public.profiles(id)") >= 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_schema.py -v`
Expected: FAIL — the migrations file does not exist.

- [ ] **Step 3: Write the migration, then apply it**

Write `apps/api/migrations/0001_initial.sql` satisfying the tests. Then apply it
to Supabase project `yualksqjjvlfscbbsygq` using the `apply_migration` MCP tool
with name `initial_schema`. **Do not apply it to any other project.**

Afterwards call `list_tables` on that project and confirm exactly the five
tables exist with `rls_enabled: true`, and call `get_advisors` with type
`security` and record what it says. If an advisor flags something, fix it now —
that tool exists precisely to catch RLS mistakes.

- [ ] **Step 4: Verify**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_schema.py -v` → 7 passed.
Then confirm via MCP `list_tables` that all five tables report `rls_enabled: true`.

- [ ] **Step 5: Commit**

```bash
git add apps/api/migrations/0001_initial.sql apps/api/tests/test_schema.py
git commit -m "feat(db): initial schema with deny-by-default RLS

Five tables, RLS on every one, and no policy granting anon or authenticated —
the database is API-only, so a browser holding a valid JWT still cannot read
Postgres directly. machines stores token_hash, never a raw token."
```

---

### Task 2: Verify Supabase JWTs and machine tokens

**Files:**
- Create: `apps/api/flashml_cloud_api/settings.py`, `apps/api/flashml_cloud_api/auth.py`
- Test: `apps/api/tests/test_auth.py`

**Interfaces:**
- Produces:
  - `Settings.from_env()` with `supabase_url`, `supabase_jwt_secret`, `supabase_service_key`, `coordinator_url`, `coordinator_operator_token`, `require_auth: bool`
  - `verify_supabase_jwt(token: str, settings) -> str` returning the user id (`sub`), raising `AuthError` otherwise
  - `hash_machine_token(token: str) -> str` — sha256 hex
  - `new_machine_token() -> str` — `secrets.token_urlsafe(32)` with an `fmk_` prefix
  - `AuthError(Exception)`

Verification must check **signature, expiry, and audience**. A token that is
merely well-formed is not a credential.

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_auth.py
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
```

- [ ] **Step 2–5:** run (fails on import), implement, re-run to 8 passed, commit.

Add `pyjwt>=2.8` and `psycopg[binary]>=3.1` to `apps/api/pyproject.toml`
dependencies. Implementation notes that matter:
- Pass `algorithms=["HS256"]` explicitly to `jwt.decode`. Never let the token
  choose its own algorithm — that is what makes `alg=none` and the
  RS256→HS256 confusion attack work.
- `audience="authenticated"` and let PyJWT enforce `exp`.
- `Settings.from_env()` raises when `require_auth` is on and any secret is
  missing, so a misconfigured deploy fails at startup, not at first request.

---

### Task 3: Device-code enrolment

**Files:**
- Create: `apps/api/flashml_cloud_api/db.py`, `apps/api/flashml_cloud_api/enrolment.py`
- Test: `apps/api/tests/test_enrolment.py`

**Interfaces:**
- `start_device_code(db, node_id, hostname, platform) -> dict` → `{device_code, user_code, expires_at, interval}`
- `approve_device_code(db, user_code, user_id) -> uuid` (the new machine id)
- `redeem_device_code(db, device_code) -> str | None` (the machine token, **once**)
- `authenticate_machine(db, token) -> Machine | None`
- `revoke_machine(db, machine_id, user_id) -> bool`

Rules the tests pin:
- `user_code` is short and human-typable (8 chars, unambiguous alphabet — no `O`/`0`/`I`/`1`).
- A code expires (10 minutes) and cannot be approved after that.
- Redeeming returns the token **exactly once**; a second redeem returns `None`.
- Redeeming before approval returns `None` — no token leaks to a machine nobody approved.
- Approving twice does not mint a second machine.
- `authenticate_machine` returns `None` for a revoked machine, immediately.
- Enrolling a `node_id` that is already bound to another machine is refused — otherwise a machine could impersonate one already enrolled.

- [ ] **Steps:** write the tests first, run against a real Postgres (use the
Supabase project — create a `test_` schema or clean up after; state which you
chose and why), implement, verify, commit.

---

### Task 4: The coordinator accepts operator-asserted identity

**Files:**
- Modify: `flashruntime/flashruntime/service/modea.py`
- Test: `flashruntime/tests/test_service_delegation.py`

**Interfaces:**
- `X-FlashML-On-Behalf-Of: <node_id>` is honoured **only** when the caller presents an operator credential. For any other caller it is ignored entirely.

This is the seam that lets the cloud API front the coordinator without losing
Plan 2's lease scoping.

Rules the tests pin:
- Operator token + header → authorized as that node; writes are scoped to *that node's* live leases, exactly as if the node had called directly.
- Operator token + header naming a node with no live lease covering the key → **403**. Delegation does not widen scope.
- **Node** token + header → header **ignored**; the caller remains itself. A volunteer must not be able to act as another machine.
- No header + operator token → unscoped operator behaviour, unchanged from Plan 2.
- Header naming an unknown node → 403.

- [ ] **Steps:** tests first (they must fail), implement, confirm the whole
flashruntime suite (475) and the e2e write-scope suite (15) still pass, commit.

---

### Task 5: The API fronts the coordinator

**Files:**
- Modify: `apps/api/flashml_cloud_api/app.py`
- Test: `apps/api/tests/test_agent_proxy.py`

**Interfaces:**
- Agent-facing: `POST /v1alpha1/device/code`, `POST /v1alpha1/device/token`, and proxies for `nodes/register`, `leases/claim`, `attempts/*`, `artifacts/*`, `checkpoints/*`.
- Browser-facing: `GET /v1alpha1/me`, `GET /v1alpha1/machines`, `POST /v1alpha1/machines/{id}/revoke`, `POST /v1alpha1/device/approve`.

Every agent proxy call: authenticate the machine token → look up its `node_id`
→ forward to the coordinator with the operator token **and**
`X-FlashML-On-Behalf-Of: <node_id>`. The agent's own body `node_id`, if any, is
overwritten — same rule as Plan 2.

Tests must pin: an unauthenticated agent call is 401; a revoked machine's token
is 401 immediately; the forwarded request carries both the operator token and
the correct on-behalf-of header; and a browser JWT cannot be used as a machine
token or vice versa.

- [ ] **Steps:** tests first, implement, verify, commit.

---

### Task 6: Job ownership, then docs and the progress entry

**Files:**
- Modify: `apps/api/flashml_cloud_api/app.py`, `../PROGRESS.md`, `../M1_DECISIONS.md`
- Test: `apps/api/tests/test_jobs.py`

`POST /v1alpha1/jobs` requires a browser JWT, writes a `jobs` row owned by that
user, and forwards to the coordinator. `GET /v1alpha1/jobs` returns **only** the
caller's jobs. Fetching another user's job by id is 404 — not 403, which would
confirm the id exists.

Record in `M1_DECISIONS.md` as D14: the operator-asserted-identity delegation
decision, why the dynamic-token-registration alternative was rejected, and that
the coordinator must be unreachable from the internet once deployed (Plan 7).

Write the `PROGRESS.md` entry per the logging protocol, with real numbers, and
state plainly what is still not true: no web UI yet (Plan 5), no GitHub
integration (Plan 4), result verification still unbuilt (M3).

---

## Self-Review

**Spec coverage.** §3.1 topology → Tasks 4, 5. §4 data model → Task 1. §5.1
browser auth → Task 2. §5.2 device flow + "resolve node_id from the token,
never the body" → Tasks 3, 5. §6.1 lease-scoped writes survive the indirection →
Task 4. Job ownership (§4 `jobs`) → Task 6.

**Deliberately not here:** the web UI (Plan 5), GitHub→job and preflight
(Plan 4), Windows hosts (Plan 6), the Render deploy itself (Plan 7). Google
OAuth is configured in the Supabase dashboard rather than in code — Task 6's
docs must say so, since it is a manual step nobody can infer from the repo.

**Type consistency.** `verify_supabase_jwt(token, settings) -> str` (user id) is
used in Tasks 5 and 6. `authenticate_machine(db, token) -> Machine | None` is
used in Task 5. `X-FlashML-On-Behalf-Of` is spelled identically in Tasks 4 and 5.

**Known risk.** Task 4 changes a security-critical authorization path that Plan 2
just proved correct. The e2e write-scope suite (15 tests) is the regression
gate; if any of those 8 assertions breaks, the delegation design is wrong, not
the test.
