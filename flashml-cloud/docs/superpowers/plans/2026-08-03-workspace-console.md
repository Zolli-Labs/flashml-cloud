# Workspace Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the FlashML console from a personal tool with a pool list on the side into a workspace-scoped collaboration surface: a workspace switcher in the rail, five tabs per workspace, and a permanent personal area for machines.

**Architecture:** The pool becomes the organising unit. `/w/[poolId]/…` carries the workspace in the URL so links are shareable; one `WorkspaceProvider` at the layout fetches everything the five tabs need, replacing today's per-page polling. Three small API additions supply the data the frontend cannot otherwise get honestly: `pool_id` + submitter on job rows, a pool-wide machines read, and a rename route. Machines stay personal property; jobs always belong to a workspace.

**Tech Stack:** Next.js 16.2.9 (App Router) · React 19.2.4 · TypeScript · Tailwind v4 · vitest 4 (node environment, `**/*.test.ts`) · FastAPI · psycopg 3 · pytest.

**Spec:** `docs/superpowers/specs/2026-08-03-workspace-console-design.md`

## Global Constraints

- **Working directory:** `flashml-cloud/apps/api` for Python, `flashml-cloud/apps/web` for TypeScript. Paths in this plan are relative to those two unless shown otherwise.
- **Run the API suite:** `cd flashml-cloud/apps/api && .venv/bin/pytest -q`. A single test: `.venv/bin/pytest tests/test_pools_api.py::test_name -q`.
- **Run the web suite:** `cd flashml-cloud/apps/web && npm test`. A single file: `npx vitest run lib/workspace-scope.test.ts`.
- **Both suites at once, from the repo root:** `make test`.
- **404 doctrine, everywhere.** A resource that does not exist, one that exists but is not yours, and one you can see but do not own all return **404 with the same detail string**. Never 403. A 403 confirms an id is real to someone who should not know it. Every new route in this plan follows `revoke_pool_invites_route`'s exact shape.
- **`psycopg.errors.InvalidTextRepresentation` must be caught** around any `fetch_pool_for_member` / `fetch_machine_for_owner` call — a non-UUID id would otherwise 500 instead of 404.
- **UI says "workspace"; code says "pool".** No route, table, type name, or Python identifier is renamed. Only user-visible strings change. See spec §1.5 and §10.
- **Job list rows must tolerate missing fields.** `pool_id` and `submitted_by` are optional on `JobRecord` and every consumer must handle absence — the web and API deploy separately, so the browser will briefly run new code against the old API. Same insurance the machines page's `pools ?? []` already documents.
- **Never introduce a nav item that leads nowhere** (`ConsoleShell.tsx`'s own rule).
- **Commit style:** `feat(api):`, `feat(web):`, `fix(web):`, `docs:` — matching the existing log.
- **No changes to** `flashruntime`, `flashnode`, the protocol, `/pools/join`, `/activate`, or any migration. This plan touches `apps/web` and `apps/api` only, and adds no migration at all.

---

# Phase 1 — API additions

Three self-contained backend changes. Each ships green and backwards-compatible on its own; the frontend does not depend on any of them until Phase 2.

---

### Task 1: `pool_id` and submitter on job rows

Today `list_jobs_route` runs two queries and unions their ids in Python, discarding the `pool_id` that would tell the console which workspace a job belongs to. One query replaces both and keeps the field.

**Files:**
- Modify: `flashml_cloud_api/db.py` (add two functions after `list_pool_job_ids_for_member`, ~line 1266)
- Modify: `flashml_cloud_api/app.py:1584-1650` (`list_jobs_route`), `app.py:1651-1692` (`get_job_route`)
- Test: `tests/test_pool_visibility.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `db.list_job_scopes_for_viewer(db, user_id) -> dict[str, dict[str, Any]]` — job id → `{"pool_id": str | None, "submitted_by": str | None}`
  - `db.display_name_for(db, user_id) -> str | None`
  - `GET /v1alpha1/jobs` rows gain `pool_id` and `submitted_by`
  - `GET /v1alpha1/jobs/{id}` gains `pool_id` and `submitted_by`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pool_visibility.py`:

```python
def test_job_list_rows_carry_pool_id_and_submitter(make_client, db):
    """The console scopes jobs by workspace, which it can only do if the row
    says which workspace it is in. A pre-pools job reports None rather than
    being absent — 'no workspace' is a real answer, not a missing field."""
    owner = _new_user(db, display_name="Ada")
    client = make_client()
    pool = _create_pool(client, owner).json()

    in_pool = _seed_job(db, owner_id=owner, pool_id=pool["id"])
    orphan = _seed_job(db, owner_id=owner, pool_id=None)
    client.coordinator.jobs = [
        {"job_id": in_pool, "state": "SUCCEEDED"},
        {"job_id": orphan, "state": "SUCCEEDED"},
    ]

    rows = {r["job_id"]: r for r in
            client.get("/v1alpha1/jobs", headers=_auth(owner)).json()}

    assert rows[in_pool]["pool_id"] == pool["id"]
    assert rows[in_pool]["submitted_by"] == "Ada"
    assert rows[orphan]["pool_id"] is None
    assert rows[orphan]["submitted_by"] == "Ada"


def test_teammates_job_row_names_its_submitter(make_client, db):
    """The attribution that makes the workspace read as shared: a member
    seeing a teammate's job must see whose it is, not just that it exists."""
    owner = _new_user(db, display_name="Ada")
    member = _new_user(db, display_name="Grace")
    client = make_client()
    pool = _create_pool(client, owner).json()
    _add_member(db, pool["id"], member)

    job = _seed_job(db, owner_id=owner, pool_id=pool["id"])
    client.coordinator.jobs = [{"job_id": job, "state": "RUNNING"}]

    rows = client.get("/v1alpha1/jobs", headers=_auth(member)).json()

    assert [r["submitted_by"] for r in rows] == ["Ada"]
    assert [r["pool_id"] for r in rows] == [pool["id"]]


def test_job_detail_carries_pool_id(make_client, db):
    """The job detail page renders its own breadcrumb, so it must be able to
    name its workspace without consulting the list it may not have loaded."""
    owner = _new_user(db, display_name="Ada")
    client = make_client()
    pool = _create_pool(client, owner).json()
    job = _seed_job(db, owner_id=owner, pool_id=pool["id"])
    client.coordinator.job_detail = {"job_id": job, "state": "RUNNING"}

    body = client.get(f"/v1alpha1/jobs/{job}", headers=_auth(owner)).json()

    assert body["pool_id"] == pool["id"]
    assert body["submitted_by"] == "Ada"
```

Reuse the module's existing fixtures and helpers. If `test_pool_visibility.py` lacks `_seed_job`, `_create_pool`, `_add_member`, `_auth`, or a `display_name` parameter on `_new_user`, copy them from `tests/test_db_pools.py` (`_seed_job`, `_add_member`) and `tests/test_pools_api.py` (`_auth`, `_create_pool`) rather than inventing new ones — those two modules already share this fixture set via `from test_jobs_from_repo import ...`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_pool_visibility.py -q -k "pool_id or submitter"`
Expected: FAIL — `KeyError: 'pool_id'`.

- [ ] **Step 3: Add the two db functions**

In `flashml_cloud_api/db.py`, immediately after `list_pool_job_ids_for_member`:

```python
def list_job_scopes_for_viewer(
    db: psycopg.Connection, user_id: str
) -> dict[str, dict[str, Any]]:
    """Every job id ``user_id`` can see — owned outright, or reachable
    through a shared pool — mapped to the two fields the console scopes and
    labels on: which pool the job belongs to, and who submitted it.

    Replaces the ``list_job_ids_for_owner`` + ``list_pool_job_ids_for_member``
    pair at ``list_jobs_route``. Those ran the owner half and the pool half
    as two queries and unioned the ids in Python, throwing away the
    ``pool_id`` that came back with them. This is the same union expressed
    once in SQL, and it keeps that column — so the route gets a scoping
    filter and a display mapping out of strictly less work than before.

    ``pool_id`` is None for every pre-pools job. Those rows are reachable by
    their owner alone: a null pool can never match the ``pool_members`` half
    of the check, exactly as ``fetch_job_for_viewer`` documents for itself.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select j.id, j.pool_id, pr.display_name as submitted_by
              from public.jobs j
              left join public.profiles pr on pr.id = j.owner_id
             where j.owner_id = %s
                or exists (
                     select 1 from public.pool_members pm
                      where pm.pool_id = j.pool_id and pm.user_id = %s
                   )
            """,
            (user_id, user_id),
        )
        return {
            row["id"]: {
                "pool_id": None if row["pool_id"] is None else str(row["pool_id"]),
                "submitted_by": row["submitted_by"],
            }
            for row in cur.fetchall()
        }


def display_name_for(db: psycopg.Connection, user_id: str) -> str | None:
    """The profile display name for ``user_id``. None when the profile row
    does not exist yet (a brand-new sign-in that has not hit ``upsert_profile``)
    or the name was never set — both are "no label to show", and the caller
    renders the same fallback for each."""
    with db.cursor() as cur:
        cur.execute(
            "select display_name from public.profiles where id = %s", (user_id,)
        )
        row = cur.fetchone()
        return row["display_name"] if row else None
```

- [ ] **Step 4: Rewrite `list_jobs_route`'s scoping**

In `flashml_cloud_api/app.py`, replace the body of `list_jobs_route` from the `owned = dbmod.list_job_ids_for_owner(...)` line down to the final `return`:

```python
        # One query for both halves of visibility — owned, and reachable
        # through a shared pool — carrying the pool_id and submitter the
        # console renders. This replaced two queries whose ids were unioned
        # in Python; the union is now in the SQL, and it no longer discards
        # the columns that came back with it.
        scopes = dbmod.list_job_scopes_for_viewer(db, user_id)
        # A federated parent id names no coordinator job, so it can never
        # match anything in the coordinator's list; dropping it here is what
        # lets a user whose only jobs are federated skip the round trip
        # entirely instead of fetching a list to throw all of it away.
        seen = {j for j in scopes if not fedavgmod.is_federated_job_id(j)}

        # A federated run is one coordinator job per round, so it is not in
        # the coordinator's list at all and has to be added from this table.
        # `list_federated_jobs_for_viewer` applies the same owner-or-member
        # predicate as `scopes`, so every id it returns is already a key
        # there — the `.get` default is belt-and-braces, not a real branch.
        federated = [
            {
                "job_id": row["id"],
                "name": row.get("name"),
                "state": row.get("status"),
                "mode": "federated",
                **scopes.get(row["id"], {"pool_id": None, "submitted_by": None}),
            }
            for row in dbmod.list_federated_jobs_for_viewer(db, user_id)
        ]
        if not seen:
            # Nothing to scope down to; skip the coordinator round trip
            # rather than fetch a list of jobs we would only throw away.
            return federated
        r = await coordinator.forward("GET", "/v1alpha1/jobs")
        if r.status_code >= 300:
            return _passthrough(r)
        try:
            jobs = r.json()
        except ValueError:
            return _passthrough(r)
        if not isinstance(jobs, list):
            return _passthrough(r)
        # The coordinator has no notion of accounts and returns every job
        # unscoped behind the operator token; `scopes` (owned or reachable
        # through a shared pool) is the only place that filter can be
        # applied — and now also the only place the workspace label comes
        # from, since the coordinator has never heard of pools.
        return [
            {**j, **scopes[j["job_id"]]}
            for j in jobs
            if isinstance(j, dict) and j.get("job_id") in seen
        ] + federated
```

- [ ] **Step 5: Stamp the job detail route**

In `get_job_route`, add `pool_id`/`submitted_by` to the federated return dict, and replace the final two lines:

```python
            return {
                "job_id": job_id,
                "state": row.get("status"),
                "mode": source.get("mode"),
                "rounds_requested": source.get("rounds"),
                "rounds_completed": len(rounds),
                "spec": row.get("spec"),
                "created_at": str(row["created_at"]) if row.get("created_at") else None,
                "finished_at": (
                    str(row["finished_at"]) if row.get("finished_at") else None
                ),
                "pool_id": (
                    None if row.get("pool_id") is None else str(row["pool_id"])
                ),
                "submitted_by": dbmod.display_name_for(db, row["owner_id"]),
            }
        r = await coordinator.forward("GET", f"/v1alpha1/jobs/{_seg(job_id)}")
        # Merge the workspace label in rather than passing the coordinator's
        # body straight through: the detail page renders its own breadcrumb
        # and may have been deep-linked, so it cannot rely on having loaded
        # the list. `row` is already in hand from the visibility check above,
        # so this costs one profile lookup and no extra job query.
        if r.status_code >= 300:
            return _passthrough(r)
        try:
            job = r.json()
        except ValueError:
            return _passthrough(r)
        if not isinstance(job, dict):
            return _passthrough(r)
        job["pool_id"] = (
            None if row.get("pool_id") is None else str(row["pool_id"])
        )
        job["submitted_by"] = dbmod.display_name_for(db, row["owner_id"])
        return job
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_pool_visibility.py -q`
Expected: PASS.

- [ ] **Step 7: Run the full API suite — nothing else regressed**

Run: `.venv/bin/pytest -q`
Expected: PASS. `list_job_ids_for_owner` and `list_pool_job_ids_for_member` are now unused by `list_jobs_route` but still referenced by their own tests; leave both functions in place.

- [ ] **Step 8: Commit**

```bash
git add flashml_cloud_api/db.py flashml_cloud_api/app.py tests/test_pool_visibility.py
git commit -m "feat(api): job rows carry pool_id and submitter; one scoping query, not two"
```

---

### Task 2: `GET /v1alpha1/pools/{pool_id}/machines`

`list_machines_for_owner` is caller-scoped by design, so the console can only ever show you your own machines. This is the other half: what compute a workspace actually has.

**Files:**
- Modify: `flashml_cloud_api/db.py` (add after `pools_for_machines_of_owner`, ~line 1040)
- Modify: `flashml_cloud_api/app.py` (add after `get_pool_route`, ~line 1090)
- Test: `tests/test_pools_api.py`

**Interfaces:**
- Consumes: `MACHINE_PUBLIC_COLUMNS` (`db.py:354`), `fetch_pool_for_member`.
- Produces: `db.list_pool_machines(db, pool_id) -> list[dict[str, Any]]`; route `GET /v1alpha1/pools/{pool_id}/machines` returning `MACHINE_PUBLIC_COLUMNS` plus `owner_id` and `owner_display_name`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pools_api.py`:

```python
def test_pool_machines_lists_every_members_bound_machine(make_client, db):
    """The point of the tab: you see the workspace's compute, not just your
    own. `list_machines_for_owner` structurally cannot answer this."""
    owner = _new_user(db, display_name="Ada")
    member = _new_user(db, display_name="Grace")
    client = make_client()
    pool = _create_pool(client, owner).json()
    _add_member(db, pool["id"], member)

    mine = _enrol(db, owner, "mine")
    theirs = _enrol(db, member, "theirs")
    unbound = _enrol(db, owner, "unbound")
    dbmod.bind_machine_pool(db, machine_id=mine, pool_id=pool["id"])
    dbmod.bind_machine_pool(db, machine_id=theirs, pool_id=pool["id"])

    rows = client.get(
        f"/v1alpha1/pools/{pool['id']}/machines", headers=_auth(owner)
    ).json()

    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {mine, theirs}, "unbound machines must not appear"
    assert unbound not in by_id
    assert by_id[theirs]["owner_display_name"] == "Grace"
    assert "token_hash" not in by_id[mine]


def test_pool_machines_omits_a_machine_whose_owner_left(make_client, db):
    """A binding left behind by someone who has left the pool is inert for
    placement (`pool_ids_for_machine`'s join). This view must agree, or the
    tab overstates the workspace's capacity."""
    owner = _new_user(db)
    leaver = _new_user(db)
    client = make_client()
    pool = _create_pool(client, owner).json()
    _add_member(db, pool["id"], leaver)

    abandoned = _enrol(db, leaver, "abandoned")
    dbmod.bind_machine_pool(db, machine_id=abandoned, pool_id=pool["id"])
    with db.cursor() as cur:
        cur.execute(
            "delete from public.pool_members where pool_id = %s and user_id = %s",
            (pool["id"], leaver),
        )

    rows = client.get(
        f"/v1alpha1/pools/{pool['id']}/machines", headers=_auth(owner)
    ).json()

    assert rows == []


def test_pool_machines_404s_for_non_member_and_for_a_malformed_id(make_client, db):
    """404 doctrine: a stranger and a garbage id get the same answer, so the
    route cannot be used to learn which pool ids are real."""
    owner = _new_user(db)
    stranger = _new_user(db)
    client = make_client()
    pool = _create_pool(client, owner).json()

    assert client.get(
        f"/v1alpha1/pools/{pool['id']}/machines", headers=_auth(stranger)
    ).status_code == 404
    assert client.get(
        "/v1alpha1/pools/not-a-uuid/machines", headers=_auth(owner)
    ).status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_pools_api.py -q -k pool_machines`
Expected: FAIL — 404 on every call, because the route does not exist.

- [ ] **Step 3: Add the db function**

In `flashml_cloud_api/db.py`, after `pools_for_machines_of_owner`:

```python
def list_pool_machines(
    db: psycopg.Connection, pool_id: str
) -> list[dict[str, Any]]:
    """Every machine bound to ``pool_id``, across all of its members, with
    the owner label the console renders beside each row.

    ``list_machines_for_owner`` cannot answer this and is not supposed to:
    it is scoped to one caller by design, so it shows you your own machines
    and none of your teammates'. This is the workspace-wide view — what
    compute the pool actually has.

    Joined against live ``pool_members``, the same guard
    ``pool_ids_for_machine`` and ``pools_for_machines_of_owner`` both apply:
    a binding left behind by an owner who has since left the pool is already
    inert for placement, so listing it here would overstate the workspace's
    capacity to every member looking at it. The three views must agree on
    which machines a pool actually has.

    Revoked machines are NOT filtered out. A revoked machine's token is dead
    and it can never claim work, but it is still a row the workspace can see,
    and the console renders its status — unlike the opt-in checkbox list,
    which filters them because ticking one would be meaningless.
    """
    columns = ", ".join(f"m.{c}" for c in MACHINE_PUBLIC_COLUMNS)
    with db.cursor() as cur:
        cur.execute(
            f"""
            select {columns},
                   m.owner_id,
                   pr.display_name as owner_display_name
              from public.machine_pools mp
              join public.machines m on m.id = mp.machine_id
              join public.pool_members pm
                on pm.pool_id = mp.pool_id and pm.user_id = m.owner_id
              left join public.profiles pr on pr.id = m.owner_id
             where mp.pool_id = %s
             order by m.created_at
            """,
            (pool_id,),
        )
        return list(cur.fetchall())
```

- [ ] **Step 4: Add the route**

In `flashml_cloud_api/app.py`, immediately after `get_pool_route`:

```python
    @app.get("/v1alpha1/pools/{pool_id}/machines", tags=["browser"])
    async def list_pool_machines_route(
        pool_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Every machine this pool has, across all of its members.

        Authorize BEFORE listing, exactly as ``get_pool_route`` does:
        ``list_pool_machines`` takes no viewer param by design, so membership
        has to be established here, first, or any pool's fleet would be
        readable by anyone who could guess an id. 404, not 403 — see
        ``fetch_pool_for_member``'s own docstring.
        """
        try:
            pool = dbmod.fetch_pool_for_member(db, pool_id, user_id)
        except psycopg.errors.InvalidTextRepresentation:
            # A pool_id that is not even a uuid. Same answer as one that
            # simply is not yours.
            pool = None
        if pool is None:
            raise HTTPException(status_code=404, detail="unknown pool")
        return [_jsonable(m) for m in dbmod.list_pool_machines(db, pool_id)]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_pools_api.py -q -k pool_machines`
Expected: PASS.

- [ ] **Step 6: Run the full API suite**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add flashml_cloud_api/db.py flashml_cloud_api/app.py tests/test_pools_api.py
git commit -m "feat(api): GET /pools/{id}/machines — the workspace's fleet across all members"
```

---

### Task 3: `PATCH /v1alpha1/pools/{pool_id}` — rename

Without this the Settings tab has nothing it can change. Six pool routes exist and none of them can alter a pool after creation.

**Files:**
- Modify: `flashml_cloud_api/db.py` (add after `create_pool`)
- Modify: `flashml_cloud_api/app.py` (add after `list_pool_machines_route` from Task 2)
- Test: `tests/test_pools_api.py`

**Interfaces:**
- Consumes: `fetch_pool_for_member`, `_json_object`, `_jsonable`.
- Produces: `db.rename_pool(db, *, pool_id, name) -> dict[str, Any] | None`; route `PATCH /v1alpha1/pools/{pool_id}` accepting `{"name": str}` and returning the updated `Pool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pools_api.py`:

```python
def test_owner_can_rename_their_pool(make_client, db):
    owner = _new_user(db)
    client = make_client()
    pool = _create_pool(client, owner, name="Untitled").json()

    r = client.patch(
        f"/v1alpha1/pools/{pool['id']}",
        json={"name": "  Vision Lab  "},
        headers=_auth(owner),
    )

    assert r.status_code == 200
    assert r.json()["name"] == "Vision Lab", "the name is stored trimmed"
    assert client.get(
        f"/v1alpha1/pools/{pool['id']}", headers=_auth(owner)
    ).json()["pool"]["name"] == "Vision Lab"


def test_a_member_who_is_not_the_owner_cannot_rename(make_client, db):
    """404, not 403 — the same answer a stranger gets. A 403 here would
    confirm the pool is real to someone outside it."""
    owner = _new_user(db)
    member = _new_user(db)
    stranger = _new_user(db)
    client = make_client()
    pool = _create_pool(client, owner, name="Vision Lab").json()
    _add_member(db, pool["id"], member)

    for who in (member, stranger):
        r = client.patch(
            f"/v1alpha1/pools/{pool['id']}",
            json={"name": "Hijacked"},
            headers=_auth(who),
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "unknown pool"

    assert client.get(
        f"/v1alpha1/pools/{pool['id']}", headers=_auth(owner)
    ).json()["pool"]["name"] == "Vision Lab"


def test_rename_rejects_empty_and_overlong_names(make_client, db):
    """Same validation as create — the two routes must agree on what a
    legal pool name is."""
    owner = _new_user(db)
    client = make_client()
    pool = _create_pool(client, owner).json()

    for bad in ("", "   ", 42, None):
        r = client.patch(
            f"/v1alpha1/pools/{pool['id']}", json={"name": bad},
            headers=_auth(owner),
        )
        assert r.status_code == 400, f"{bad!r} should be rejected"

    r = client.patch(
        f"/v1alpha1/pools/{pool['id']}", json={"name": "x" * 201},
        headers=_auth(owner),
    )
    assert r.status_code == 400
    assert "200 characters" in r.json()["detail"]


def test_rename_404s_on_a_malformed_pool_id(make_client, db):
    owner = _new_user(db)
    client = make_client()
    r = client.patch(
        "/v1alpha1/pools/not-a-uuid", json={"name": "x"}, headers=_auth(owner)
    )
    assert r.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_pools_api.py -q -k rename`
Expected: FAIL — 405 Method Not Allowed, since no PATCH handler is registered.

- [ ] **Step 3: Add the db function**

In `flashml_cloud_api/db.py`, after `create_pool`:

```python
def rename_pool(
    db: psycopg.Connection, *, pool_id: str, name: str
) -> dict[str, Any] | None:
    """Set a pool's name and return the updated row.

    Takes no viewer or owner argument on purpose: authorization for a write
    this consequential belongs at the route, checked against the pool's own
    row before anything is written, the same shape ``revoke_pool_invites``
    uses. None here therefore means the row vanished between that check and
    this write — not that the caller was refused.
    """
    with db.cursor() as cur:
        cur.execute(
            "update public.pools set name = %s where id = %s returning *",
            (name, pool_id),
        )
        return cur.fetchone()
```

- [ ] **Step 4: Add the route**

In `flashml_cloud_api/app.py`, after `list_pool_machines_route`:

```python
    @app.patch("/v1alpha1/pools/{pool_id}", tags=["browser"])
    async def rename_pool_route(
        pool_id: str,
        request: Request,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Rename a pool. Owner only — checked here, against this pool's
        row, before anything is written. 404, not 403, whether the pool does
        not exist, the caller is a stranger to it, or the caller is a member
        who simply isn't its owner: the same doctrine, and for the same
        reason, as the three invite routes.

        Not gated by ``admitted_user``, for the reason
        ``create_pool_invite_route`` states: renaming a pool already requires
        owning one, and owning one already required admission at create time.
        """
        try:
            pool = dbmod.fetch_pool_for_member(db, pool_id, user_id)
        except psycopg.errors.InvalidTextRepresentation:
            pool = None
        if pool is None or str(pool["owner_id"]) != user_id:
            raise HTTPException(status_code=404, detail="unknown pool")

        payload = await _json_object(request)
        raw_name = payload.get("name")
        # Validated identically to create_pool_route, and deliberately
        # duplicated rather than extracted: the two routes must agree on what
        # a legal pool name is, and eight lines that read the same are easier
        # to keep honest than a shared helper that can drift out from under
        # one of them.
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise HTTPException(status_code=400, detail="name is required")
        name = raw_name.strip()
        if len(name) > 200:
            raise HTTPException(
                status_code=400, detail="name is limited to 200 characters"
            )

        updated = dbmod.rename_pool(db, pool_id=pool_id, name=name)
        if updated is None:
            raise HTTPException(status_code=404, detail="unknown pool")
        return _jsonable(updated)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_pools_api.py -q -k rename`
Expected: PASS.

- [ ] **Step 6: Run the full API suite**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add flashml_cloud_api/db.py flashml_cloud_api/app.py tests/test_pools_api.py
git commit -m "feat(api): PATCH /pools/{id} — owner-only rename"
```

---

# Phase 2 — Client surface and pure logic

Everything here is testable with vitest in a node environment: no components, no DOM.

---

### Task 4: `lib/workspace-scope.ts` — which workspace a request lands in

**Files:**
- Create: `lib/workspace-scope.ts`
- Test: `lib/workspace-scope.test.ts`

**Interfaces:**
- Consumes: `PoolSummary` from `./cloud-api`.
- Produces: `LAST_WORKSPACE_COOKIE`, `workspacePath(poolId, tab)`, `workspaceIdFromPath(pathname)`, `resolveWorkspace(pathname, pools, cookieValue)`, `WORKSPACE_TABS`, type `WorkspaceResolution`.

- [ ] **Step 1: Write the failing test**

Create `lib/workspace-scope.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import {
  resolveWorkspace,
  workspaceIdFromPath,
  workspacePath,
} from "./workspace-scope";
import type { PoolSummary } from "./cloud-api";

function pool(id: string, name: string): PoolSummary {
  return {
    id,
    name,
    owner_id: "owner",
    created_at: "2026-08-01T00:00:00Z",
    member_count: 1,
    machines_online: 0,
  };
}

const VISION = pool("vision", "Vision Lab");
const ROBOTICS = pool("robotics", "Almanac Robotics");

describe("workspaceIdFromPath", () => {
  it("reads the id out of a workspace route", () => {
    expect(workspaceIdFromPath("/w/vision")).toBe("vision");
    expect(workspaceIdFromPath("/w/vision/jobs")).toBe("vision");
    expect(workspaceIdFromPath("/w/vision/jobs/abc-123")).toBe("vision");
  });

  it("is null for anything not workspace-scoped", () => {
    expect(workspaceIdFromPath("/account/machines")).toBeNull();
    expect(workspaceIdFromPath("/w")).toBeNull();
    expect(workspaceIdFromPath("/w/")).toBeNull();
    expect(workspaceIdFromPath("/")).toBeNull();
  });

  it("decodes an escaped segment", () => {
    expect(workspaceIdFromPath("/w/a%2Fb/jobs")).toBe("a/b");
  });
});

describe("resolveWorkspace", () => {
  const pools = [VISION, ROBOTICS];

  it("prefers the URL over the cookie", () => {
    expect(resolveWorkspace("/w/vision/jobs", pools, "robotics")).toEqual({
      kind: "workspace",
      poolId: "vision",
    });
  });

  it("ignores a URL naming a workspace you are not in", () => {
    expect(resolveWorkspace("/w/someone-elses", pools, "robotics")).toEqual({
      kind: "workspace",
      poolId: "robotics",
    });
  });

  it("falls back to the cookie when the path carries no workspace", () => {
    expect(resolveWorkspace("/overview", pools, "vision")).toEqual({
      kind: "workspace",
      poolId: "vision",
    });
  });

  it("ignores a cookie naming a workspace you were removed from", () => {
    // Alphabetical, so "Almanac Robotics" wins over "Vision Lab".
    expect(resolveWorkspace("/overview", pools, "left-this-one")).toEqual({
      kind: "workspace",
      poolId: "robotics",
    });
  });

  it("falls back to the first workspace by NAME, not by list order", () => {
    expect(resolveWorkspace("/overview", pools, null)).toEqual({
      kind: "workspace",
      poolId: "robotics",
    });
  });

  it("sends a user with no workspaces to onboarding", () => {
    expect(resolveWorkspace("/overview", [], "vision")).toEqual({
      kind: "onboarding",
    });
  });
});

describe("workspacePath", () => {
  it("builds a tab URL", () => {
    expect(workspacePath("vision", "jobs")).toBe("/w/vision/jobs");
  });

  it("escapes an id that would otherwise break the path", () => {
    expect(workspacePath("a/b", "people")).toBe("/w/a%2Fb/people");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run lib/workspace-scope.test.ts`
Expected: FAIL — cannot resolve `./workspace-scope`.

- [ ] **Step 3: Write the implementation**

Create `lib/workspace-scope.ts`:

```typescript
import type { PoolSummary } from "./cloud-api";

/** Remembers the workspace you were last in, so an entry point that carries
 * no id — `/overview`, a bookmark of the bare console, the post-sign-in
 * redirect — can resolve to somewhere real instead of guessing.
 *
 * A pool id, which already appears in the path of every workspace URL. No
 * secret moves here, which is why a plain readable cookie is the right
 * mechanism rather than anything server-signed. */
export const LAST_WORKSPACE_COOKIE = "flashml_last_workspace";

/** The five tabs of a workspace, in rail order. The single source of this
 * list: the shell renders it, and the layout validates a segment against
 * it. Adding a sixth means adding a route, and this array is where the
 * compiler will point you. */
export const WORKSPACE_TABS = [
  "overview",
  "jobs",
  "machines",
  "people",
  "settings",
] as const;

export type WorkspaceTab = (typeof WORKSPACE_TABS)[number];

/** `/w/<poolId>/<tab>`. Always build workspace URLs through this rather
 * than interpolating — a pool id is a uuid today, but the encode is what
 * keeps a link correct if that ever stops being true. */
export function workspacePath(poolId: string, tab: WorkspaceTab | "submit"): string {
  return `/w/${encodeURIComponent(poolId)}/${tab}`;
}

/** The pool id in a console pathname, or null if the path is not
 * workspace-scoped. Pure string work: this does not check that the id names
 * a pool you belong to, which is `resolveWorkspace`'s job. */
export function workspaceIdFromPath(pathname: string): string | null {
  const match = /^\/w\/([^/]+)(?:\/|$)/.exec(pathname);
  return match ? decodeURIComponent(match[1]) : null;
}

export type WorkspaceResolution =
  | { kind: "workspace"; poolId: string }
  | { kind: "onboarding" };

/** Which workspace a console request should land in.
 *
 * The order is the whole point:
 *
 * 1. The URL wins. A link pasted into Slack has to open the SENDER's
 *    workspace for the receiver, not whatever the receiver looked at last.
 *    This is the property that makes the console shareable at all.
 * 2. Then the cookie, for entry points carrying no id.
 * 3. Then the first workspace by name — stable and predictable, unlike
 *    "whatever the API listed first".
 * 4. Then onboarding.
 *
 * Both (1) and (2) are checked against live membership: a workspace you
 * were removed from must not resolve just because its id survives in your
 * cookie or your browser history. `pools` is the caller's own membership
 * list from `listPools()`, so presence in it IS the membership check.
 */
export function resolveWorkspace(
  pathname: string,
  pools: PoolSummary[],
  cookieValue: string | null
): WorkspaceResolution {
  const member = new Set(pools.map((p) => p.id));

  const fromPath = workspaceIdFromPath(pathname);
  if (fromPath !== null && member.has(fromPath)) {
    return { kind: "workspace", poolId: fromPath };
  }
  if (cookieValue !== null && member.has(cookieValue)) {
    return { kind: "workspace", poolId: cookieValue };
  }

  const first = [...pools].sort((a, b) => a.name.localeCompare(b.name))[0];
  if (first) return { kind: "workspace", poolId: first.id };

  return { kind: "onboarding" };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run lib/workspace-scope.test.ts`
Expected: PASS, 12 tests.

- [ ] **Step 5: Commit**

```bash
git add lib/workspace-scope.ts lib/workspace-scope.test.ts
git commit -m "feat(web): workspace resolution — URL over cookie, both checked against membership"
```

---

### Task 5: `lib/job-scope.ts` — partitioning jobs by workspace

**Files:**
- Create: `lib/job-scope.ts`
- Test: `lib/job-scope.test.ts`

**Interfaces:**
- Consumes: `JobRecord` from `./cloud-api` (with the optional `pool_id` added in Task 6 — write this task first and let the type error stand until Task 6 lands, or do Task 6 first; either order works, they are independent).
- Produces: `isInWorkspace(job, poolId)`, `isEarlierJob(job)`, `jobsInWorkspace(jobs, poolId)`, `earlierJobs(jobs)`, `isActiveJob(job)`, `TERMINAL_STATES`.

- [ ] **Step 1: Write the failing test**

Create `lib/job-scope.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import {
  earlierJobs,
  isActiveJob,
  isEarlierJob,
  isInWorkspace,
  jobsInWorkspace,
} from "./job-scope";
import type { JobRecord } from "./cloud-api";

function job(overrides: Partial<JobRecord> = {}): JobRecord {
  return { job_id: "j1", state: "RUNNING", ...overrides };
}

describe("isInWorkspace", () => {
  it("matches on pool_id", () => {
    expect(isInWorkspace(job({ pool_id: "vision" }), "vision")).toBe(true);
    expect(isInWorkspace(job({ pool_id: "robotics" }), "vision")).toBe(false);
  });

  it("never claims a job with no pool_id", () => {
    // The dangerous default. If absence read as "belongs here", one member's
    // pre-pools jobs would render to their whole team.
    expect(isInWorkspace(job({ pool_id: null }), "vision")).toBe(false);
    expect(isInWorkspace(job(), "vision")).toBe(false);
  });
});

describe("isEarlierJob", () => {
  it("treats both null and absent as having no workspace", () => {
    // null: an API that has the column and this job has no pool.
    // undefined: an API deployed before the field existed. Same answer.
    expect(isEarlierJob(job({ pool_id: null }))).toBe(true);
    expect(isEarlierJob(job())).toBe(true);
  });

  it("is false for a job in a workspace", () => {
    expect(isEarlierJob(job({ pool_id: "vision" }))).toBe(false);
  });
});

describe("partitioning", () => {
  const jobs = [
    job({ job_id: "a", pool_id: "vision" }),
    job({ job_id: "b", pool_id: "robotics" }),
    job({ job_id: "c", pool_id: null }),
    job({ job_id: "d" }),
  ];

  it("splits into this workspace and the earlier pile", () => {
    expect(jobsInWorkspace(jobs, "vision").map((j) => j.job_id)).toEqual(["a"]);
    expect(earlierJobs(jobs).map((j) => j.job_id)).toEqual(["c", "d"]);
  });

  it("never puts one job in both halves", () => {
    const inWs = new Set(jobsInWorkspace(jobs, "vision").map((j) => j.job_id));
    for (const j of earlierJobs(jobs)) expect(inWs.has(j.job_id)).toBe(false);
  });
});

describe("isActiveJob", () => {
  it("is false for every terminal state", () => {
    for (const state of ["SUCCEEDED", "FAILED", "CANCELLED"]) {
      expect(isActiveJob(job({ state }))).toBe(false);
    }
  });

  it("is true for anything still in flight", () => {
    for (const state of ["PENDING", "QUEUED", "RUNNING"]) {
      expect(isActiveJob(job({ state }))).toBe(true);
    }
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run lib/job-scope.test.ts`
Expected: FAIL — cannot resolve `./job-scope`.

- [ ] **Step 3: Write the implementation**

Create `lib/job-scope.ts`:

```typescript
import type { JobRecord } from "./cloud-api";

/** Job states that mean "this is over". Lived inline in
 * `overview/page.tsx` and was about to be copied into the workspace
 * provider; one definition instead, since the provider's polling rule and
 * the overview's active list must agree on what "still running" means. */
export const TERMINAL_STATES = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);

export function isActiveJob(job: JobRecord): boolean {
  return !TERMINAL_STATES.has(job.state);
}

/** Whether `job` belongs to the workspace `poolId`.
 *
 * A row with no `pool_id` — a pre-pools orphan, or a response from an API
 * deployed before the field existed — is NEVER "in this workspace".
 * Defaulting the other way is the dangerous direction: it would render one
 * member's private pre-pools jobs to their entire team. */
export function isInWorkspace(job: JobRecord, poolId: string): boolean {
  return job.pool_id === poolId;
}

/** Jobs with no workspace at all: the pre-pools rows that surface read-only
 * under My account. `null` and `undefined` both count — `null` is an API
 * that has the field and a job with no pool, `undefined` is an API that
 * predates the field — and neither is a workspace job. */
export function isEarlierJob(job: JobRecord): boolean {
  return job.pool_id === null || job.pool_id === undefined;
}

export function jobsInWorkspace(
  jobs: JobRecord[],
  poolId: string
): JobRecord[] {
  return jobs.filter((j) => isInWorkspace(j, poolId));
}

export function earlierJobs(jobs: JobRecord[]): JobRecord[] {
  return jobs.filter(isEarlierJob);
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run lib/job-scope.test.ts`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add lib/job-scope.ts lib/job-scope.test.ts
git commit -m "feat(web): job scoping — absent pool_id is never 'in this workspace'"
```

---

### Task 6: Client calls and types for the three API additions

**Files:**
- Modify: `lib/cloud-api.ts` (`JobRecord` at :214, add `PoolMachine` after `Machine` at :157, add calls after `createPool` at :517)
- Test: `lib/cloud-api.test.ts`

**Interfaces:**
- Consumes: the `request<T>()` helper (`cloud-api.ts:405`), `Machine`, `Pool`.
- Produces: `JobRecord.pool_id`, `JobRecord.submitted_by`, interface `PoolMachine`, `listPoolMachines(poolId)`, `renamePool(poolId, name)`.

- [ ] **Step 1: Write the failing test**

Append to `lib/cloud-api.test.ts`, following the module's existing mock-fetch pattern (copy the helper the file already uses to stub `fetch` and the Supabase session — do not invent a second one):

```typescript
describe("listPoolMachines", () => {
  it("GETs the pool-scoped machines route", async () => {
    const fetchMock = mockFetch([{ id: "m1", owner_display_name: "Grace" }]);
    const rows = await listPoolMachines("vision");
    expect(fetchMock.mock.calls[0][0]).toMatch(
      /\/v1alpha1\/pools\/vision\/machines$/
    );
    expect(rows[0].owner_display_name).toBe("Grace");
  });
});

describe("renamePool", () => {
  it("PATCHes the pool with a JSON name body", async () => {
    const fetchMock = mockFetch({ id: "vision", name: "Vision Lab" });
    const pool = await renamePool("vision", "Vision Lab");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/v1alpha1\/pools\/vision$/);
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ name: "Vision Lab" });
    expect(pool.name).toBe("Vision Lab");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run lib/cloud-api.test.ts`
Expected: FAIL — `listPoolMachines is not exported`.

- [ ] **Step 3: Add the types**

In `lib/cloud-api.ts`, add two optional fields to `JobRecord` (after `mode?: string;`):

```typescript
  /** The pool this job belongs to, or null for every job submitted before
   * pools shipped. OPTIONAL, not merely nullable: the web and API deploy
   * separately, so a browser running this code will briefly talk to an API
   * that has never heard of the field. `lib/job-scope.ts` treats absent and
   * null identically for exactly that reason. */
  pool_id?: string | null;
  /** Display name of whoever submitted this, for the attribution that makes
   * a shared workspace read as shared. Null when they never set one. */
  submitted_by?: string | null;
```

And after the `Machine` interface:

```typescript
/** A row of `GET /v1alpha1/pools/{id}/machines` — every machine bound to a
 * pool, across all its members, which `listMachines()` cannot return
 * because it is scoped to the caller by design.
 *
 * No `pools` field: this response is already answering "which pool", so a
 * per-machine chip list would be a longer way of saying the id in the URL.
 * That absence is why this extends `Omit<Machine, "pools">` rather than
 * `Machine` — the compiler should reject reading `.pools` off one of these,
 * not let it be silently undefined. */
export interface PoolMachine extends Omit<Machine, "pools"> {
  owner_id: string;
  owner_display_name: string | null;
}
```

- [ ] **Step 4: Add the two calls**

In `lib/cloud-api.ts`, after `createPool`:

```typescript
/** `GET /v1alpha1/pools/{id}/machines` — the workspace's whole fleet, one
 * row per bound machine across every member.
 *
 * Member-scoped server-side: a non-member gets 404, indistinguishable from
 * a pool that does not exist, so `NotFound` from here must never be
 * reworded into an access-denied message.
 *
 * Machines whose owner has left the pool are already excluded by the query
 * (`list_pool_machines`), matching what placement actually sees — so the
 * count rendered from this list is the workspace's real capacity, not an
 * optimistic one. */
export function listPoolMachines(poolId: string): Promise<PoolMachine[]> {
  return request<PoolMachine[]>(
    `/v1alpha1/pools/${encodeURIComponent(poolId)}/machines`
  );
}

/** `PATCH /v1alpha1/pools/{id}` — rename, owner only.
 *
 * A member who is not the owner gets 404, the same as a stranger: the
 * caller cannot distinguish the two and must not try to. The API trims and
 * caps at 200 characters, so the returned `Pool` is the authority on what
 * the name actually became — render that, not the string you sent. */
export function renamePool(poolId: string, name: string): Promise<Pool> {
  return request<Pool>(`/v1alpha1/pools/${encodeURIComponent(poolId)}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npx vitest run lib/cloud-api.test.ts`
Expected: PASS.

- [ ] **Step 6: Typecheck and run the whole web suite**

Run: `npx tsc --noEmit && npm test`
Expected: PASS. `lib/job-scope.ts` from Task 5 now typechecks against the real `pool_id` field.

- [ ] **Step 7: Commit**

```bash
git add lib/cloud-api.ts lib/cloud-api.test.ts
git commit -m "feat(web): client surface for pool machines, rename, and job workspace labels"
```

---

# Phase 3 — Shell and routing

After this phase the console navigates as a workspace product; the tabs render placeholders until Phase 4.

---

### Task 7: `WorkspaceProvider` and the workspace layout

One fetch for all five tabs. This is a net reduction in traffic: today `overview/page.tsx` and `jobs/page.tsx` each run their own poll and `pools/[poolId]/page.tsx` calls `getMe()` a second time on top of the shell's.

**Files:**
- Create: `components/workspace/WorkspaceProvider.tsx`
- Create: `app/(console)/w/[poolId]/layout.tsx`
- Create: `app/(console)/w/[poolId]/page.tsx`

**Interfaces:**
- Consumes: `getPool`, `getMe`, `listJobs`, `listPoolMachines` (Task 6); `isActiveJob`, `jobsInWorkspace` (Task 5); `workspacePath`, `LAST_WORKSPACE_COOKIE` (Task 4).
- Produces: `useWorkspace(): WorkspaceContextValue` with `{ pool, members, machines, jobs, viewerId, isOwner, state, error, reload }`, where `jobs` is already filtered to this workspace and `machines` is `PoolMachine[]`.

- [ ] **Step 1: Write the provider**

Create `components/workspace/WorkspaceProvider.tsx`:

```typescript
"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  NotAuthenticated,
  NotFound,
  getMe,
  getPool,
  listJobs,
  listPoolMachines,
  type JobRecord,
  type Pool,
  type PoolMachine,
  type PoolMember,
} from "@/lib/cloud-api";
import { isActiveJob, jobsInWorkspace } from "@/lib/job-scope";
import { LAST_WORKSPACE_COOKIE } from "@/lib/workspace-scope";

const POLL_MS = 5000;

export type WorkspaceLoadState = "loading" | "ready" | "not-found" | "error";

export interface WorkspaceContextValue {
  pool: Pool | null;
  members: PoolMember[];
  machines: PoolMachine[];
  /** Already filtered to this workspace. A tab must never re-filter. */
  jobs: JobRecord[];
  viewerId: string | null;
  isOwner: boolean;
  state: WorkspaceLoadState;
  error: string | null;
  reload: () => void;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

/** The five tabs read everything through this. It throws rather than
 * returning null outside the provider: a tab rendering with no workspace is
 * a routing bug, and silently showing an empty page would hide it. */
export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (ctx === null) {
    throw new Error("useWorkspace must be used inside a WorkspaceProvider");
  }
  return ctx;
}

export function WorkspaceProvider({
  poolId,
  children,
}: {
  poolId: string;
  children: React.ReactNode;
}) {
  const router = useRouter();
  const [pool, setPool] = useState<Pool | null>(null);
  const [members, setMembers] = useState<PoolMember[]>([]);
  const [machines, setMachines] = useState<PoolMachine[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [viewerId, setViewerId] = useState<string | null>(null);
  const [state, setState] = useState<WorkspaceLoadState>("loading");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([getPool(poolId), getMe(), listJobs(), listPoolMachines(poolId)])
      .then(([detail, me, allJobs, fleet]) => {
        setPool(detail.pool);
        setMembers(detail.members);
        setViewerId(me.id);
        // Filtered once, here. `listJobs` returns everything the viewer can
        // see across every workspace they belong to, and a tab that filtered
        // it again would be one refactor away from forgetting to.
        setJobs(jobsInWorkspace(allJobs, poolId));
        setMachines(fleet);
        setState("ready");
        setError(null);
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          const next = window.location.pathname + window.location.search;
          router.push(`/sign-in?next=${encodeURIComponent(next)}`);
          return;
        }
        if (err instanceof NotFound) {
          // The API 404s for "does not exist" and "exists but you're not a
          // member" identically (fetch_pool_for_member's doctrine). This must
          // not be reworded into an access-denied message that would confirm
          // the id is real to someone outside the pool.
          setState("not-found");
          return;
        }
        setError(
          err instanceof Error ? err.message : "Couldn't load this workspace."
        );
        setState("error");
      });
  }, [poolId, router]);

  useEffect(() => {
    load();
  }, [load]);

  // Remember where we were, so `/overview` and the post-sign-in redirect can
  // resolve to somewhere real. Written only once the fetch SUCCEEDS: caching
  // an id we just failed to load would send the next bare entry straight
  // back into the same failure.
  useEffect(() => {
    if (state !== "ready") return;
    document.cookie = `${LAST_WORKSPACE_COOKIE}=${encodeURIComponent(poolId)}; path=/; max-age=31536000; SameSite=Lax`;
  }, [state, poolId]);

  // Stop polling once nothing is in flight. A settled workspace changes only
  // when someone acts on it, and the console is the kind of thing left open
  // in a background tab for days.
  useEffect(() => {
    if (state !== "ready") return;
    if (!jobs.some(isActiveJob)) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [jobs, state, load]);

  const isOwner =
    viewerId !== null && pool !== null && viewerId === pool.owner_id;

  return (
    <WorkspaceContext.Provider
      value={{ pool, members, machines, jobs, viewerId, isOwner, state, error, reload: load }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}
```

- [ ] **Step 2: Write the layout and the index redirect**

Create `app/(console)/w/[poolId]/layout.tsx`:

```typescript
import { WorkspaceProvider } from "@/components/workspace/WorkspaceProvider";

export default async function WorkspaceLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ poolId: string }>;
}) {
  const { poolId } = await params;
  return <WorkspaceProvider poolId={poolId}>{children}</WorkspaceProvider>;
}
```

Create `app/(console)/w/[poolId]/page.tsx`:

```typescript
import { redirect } from "next/navigation";

/** `/w/<id>` alone names a workspace but no tab. Overview is the tab a
 * workspace opens on, so send it there rather than rendering a fifth thing
 * that is really the same page. */
export default async function WorkspaceIndex({
  params,
}: {
  params: Promise<{ poolId: string }>;
}) {
  const { poolId } = await params;
  redirect(`/w/${encodeURIComponent(poolId)}/overview`);
}
```

- [ ] **Step 3: Verify the route modules are legal**

Run: `npx vitest run lib/route-exports.test.ts`
Expected: PASS. This test walks `app/` recursively and enforces that page and layout modules export only a default plus route config — the guard that exists because a stray export white-screens in production with no build failure.

- [ ] **Step 4: Typecheck**

Run: `npx tsc --noEmit`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add components/workspace/WorkspaceProvider.tsx "app/(console)/w"
git commit -m "feat(web): WorkspaceProvider — one fetch and one poll for all five tabs"
```

---

### Task 8: `WorkspaceShell` — the switcher and the scoped rail

**Files:**
- Create: `components/shell/WorkspaceSwitcher.tsx`
- Create: `components/shell/WorkspaceShell.tsx` (from `components/shell/ConsoleShell.tsx`)
- Modify: `app/(console)/layout.tsx`
- Delete: `components/shell/ConsoleShell.tsx` (in Task 18, once nothing imports it)

**Interfaces:**
- Consumes: `listPools`, `WORKSPACE_TABS`, `workspacePath`, `workspaceIdFromPath`.
- Produces: `WorkspaceShell` — same props as `ConsoleShell` (`{ children }`).

- [ ] **Step 1: Write the switcher**

Create `components/shell/WorkspaceSwitcher.tsx`:

```typescript
"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { CaretUpDown, Check, Plus } from "@phosphor-icons/react";
import { listPools, type PoolSummary } from "@/lib/cloud-api";
import { workspacePath } from "@/lib/workspace-scope";

/** The control that makes this a workspace product rather than a personal
 * one. Lists every workspace you belong to, plus the two ways to get
 * another: create one, or redeem a link.
 *
 * Fetches its own list rather than reading the provider's: it renders on
 * `/account/*` and `/onboarding` too, where there is no current workspace
 * at all and therefore no provider above it. */
export function WorkspaceSwitcher({ currentId }: { currentId: string | null }) {
  const [pools, setPools] = useState<PoolSummary[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    listPools()
      .then(setPools)
      .catch(() => {
        // A failed list is not an empty list. Leaving it empty would render
        // "no workspaces" to someone who has several, and every page under
        // here reports its own load failure already.
      });
  }, []);

  const current = pools.find((p) => p.id === currentId) ?? null;

  return (
    <div className="relative px-3 pb-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="menu"
        className="flex w-full items-center gap-2 rounded-md border border-border bg-background/60 px-2.5 py-2 text-left text-sm transition-colors hover:bg-white/[0.04]"
      >
        <span className="min-w-0 flex-1 truncate font-medium">
          {current?.name ?? "Choose a workspace"}
        </span>
        <CaretUpDown size={14} className="shrink-0 text-muted-foreground" />
      </button>

      {open && (
        <>
          <button
            type="button"
            aria-label="Close workspace menu"
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-40 cursor-default"
          />
          <div
            role="menu"
            className="absolute left-3 right-3 z-50 mt-1 overflow-hidden rounded-md border border-border bg-surface-elevated py-1 shadow-lg"
          >
            {pools.map((p) => (
              <Link
                key={p.id}
                role="menuitem"
                href={workspacePath(p.id, "overview")}
                onClick={() => setOpen(false)}
                className="flex items-center gap-2 px-3 py-2 text-sm hover:bg-white/[0.06]"
              >
                <span className="w-4 shrink-0">
                  {p.id === currentId && (
                    <Check size={13} weight="bold" className="text-primary" />
                  )}
                </span>
                <span className="min-w-0 flex-1 truncate">{p.name}</span>
                <span className="meta shrink-0">{p.member_count}</span>
              </Link>
            ))}
            {pools.length > 0 && (
              <div className="my-1 border-t border-border" />
            )}
            <Link
              role="menuitem"
              href="/onboarding"
              onClick={() => setOpen(false)}
              className="flex items-center gap-2 px-3 py-2 text-sm text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
            >
              <Plus size={13} weight="bold" className="ml-0.5" />
              New workspace
            </Link>
          </div>
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Write the shell**

Create `components/shell/WorkspaceShell.tsx` by copying `components/shell/ConsoleShell.tsx` wholesale, then making exactly these changes. Everything else — the `getMe` admission effect and its mount-only comment, `InviteGate`, `CommandPalette`, `Shortcuts`, `FleetPill`, `UserMenu`, the mobile drawer, `INVITE_GATE_BYPASS` — moves across untouched.

1. Rename the exported function `ConsoleShell` → `WorkspaceShell`.
2. Replace the `GROUPS` constant with:

```typescript
import {
  ChartBar,
  Desktop,
  Gear,
  House,
  ListChecks,
  UsersThree,
} from "@phosphor-icons/react";
import { WORKSPACE_TABS, workspaceIdFromPath, workspacePath } from "@/lib/workspace-scope";

// The five workspace tabs, in rail order. Keyed by the same strings
// `WORKSPACE_TABS` defines, so adding a tab there without adding it here is
// a compile error rather than a silently missing nav item.
const TAB_META: Record<(typeof WORKSPACE_TABS)[number], {
  label: string;
  icon: React.ElementType;
}> = {
  overview: { label: "Overview", icon: House },
  jobs: { label: "Jobs", icon: ListChecks },
  machines: { label: "Machines", icon: Desktop },
  people: { label: "People", icon: UsersThree },
  settings: { label: "Settings", icon: Gear },
};
```

3. Inside the component, derive the current workspace and render the rail:

```typescript
  const currentWorkspace = workspaceIdFromPath(pathname);
```

4. Replace the `<nav>` block's contents with the workspace section plus the personal section:

```typescript
      <nav className="flex-1 overflow-y-auto px-3 pb-4">
        {currentWorkspace !== null &&
          WORKSPACE_TABS.map((tab) => {
            const { label, icon } = TAB_META[tab];
            const href = workspacePath(currentWorkspace, tab);
            return (
              <NavItem
                key={tab}
                href={href}
                label={label}
                icon={icon}
                active={isActive(href)}
              />
            );
          })}

        {/* Personal, below the workspace and visually separated. Machines
            are owned by a person, not a workspace — you enrol and revoke
            them here, then tick which workspaces they serve. Jobs have no
            personal mode at all, so the only thing under here besides your
            fleet is the read-only pre-pools pile. */}
        <div className="mt-6 border-t border-border pt-4">
          <p className="label-caps px-2.5 pb-1">My account</p>
          <NavItem
            href="/account/machines"
            label="My machines"
            icon={Desktop}
            active={isActive("/account/machines")}
          />
          <NavItem
            href="/account/earlier-jobs"
            label="Earlier jobs"
            icon={ChartBar}
            active={isActive("/account/earlier-jobs")}
          />
        </div>
      </nav>
```

5. Mount the switcher directly above the ⌘K button, inside `rail`:

```typescript
      <WorkspaceSwitcher currentId={currentWorkspace} />
```

- [ ] **Step 3: Point the console layout at the new shell**

Replace `app/(console)/layout.tsx` entirely:

```typescript
import { WorkspaceShell } from "@/components/shell/WorkspaceShell";

/** Console chrome: left rail carrying the workspace switcher and the
 * scoped tabs, a sticky top bar with the fleet pill, and a flat content
 * column. No glass and no atmosphere in here. */
export default function ConsoleLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <WorkspaceShell>{children}</WorkspaceShell>;
}
```

- [ ] **Step 4: Typecheck and run route-exports**

Run: `npx tsc --noEmit && npx vitest run lib/route-exports.test.ts`
Expected: clean, PASS.

- [ ] **Step 5: Commit**

```bash
git add components/shell/WorkspaceShell.tsx components/shell/WorkspaceSwitcher.tsx "app/(console)/layout.tsx"
git commit -m "feat(web): workspace switcher and scoped rail"
```

---

### Task 9: Resolvers, redirects, onboarding

Old URLs must keep working, and every entry point that carries no workspace must land somewhere real.

**Files:**
- Create: `app/(console)/onboarding/page.tsx`
- Create: `components/workspace/WorkspaceResolver.tsx`
- Replace: `app/(console)/overview/page.tsx` (resolver), `app/(console)/jobs/page.tsx` (resolver), `app/(console)/pools/page.tsx` (resolver)
- Modify: `next.config.ts`, `middleware.ts`, `middleware.test.ts`

**Interfaces:**
- Consumes: `resolveWorkspace`, `LAST_WORKSPACE_COOKIE`, `workspacePath`, `listPools`.
- Produces: `WorkspaceResolver` component (takes `tab`), `/onboarding` route.

- [ ] **Step 1: Write the resolver component**

Create `components/workspace/WorkspaceResolver.tsx`:

```typescript
"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { NotAuthenticated, listPools } from "@/lib/cloud-api";
import {
  LAST_WORKSPACE_COOKIE,
  resolveWorkspace,
  workspacePath,
  type WorkspaceTab,
} from "@/lib/workspace-scope";

function readCookie(name: string): string | null {
  const hit = document.cookie
    .split("; ")
    .find((c) => c.startsWith(`${name}=`));
  return hit ? decodeURIComponent(hit.slice(name.length + 1)) : null;
}

/** Stands in for the pre-workspace routes (`/overview`, `/jobs`, `/pools`)
 * and for anything else that names a tab but not a workspace. Picks one and
 * replaces the URL.
 *
 * `router.replace`, not `push`: this page is a waypoint, and leaving it in
 * history means Back from a workspace bounces straight forward again. */
export function WorkspaceResolver({ tab }: { tab: WorkspaceTab }) {
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    listPools()
      .then((pools) => {
        const resolved = resolveWorkspace(
          pathname,
          pools,
          readCookie(LAST_WORKSPACE_COOKIE)
        );
        router.replace(
          resolved.kind === "onboarding"
            ? "/onboarding"
            : workspacePath(resolved.poolId, tab)
        );
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.replace("/sign-in");
          return;
        }
        // Any other failure: onboarding can create or join, which is the
        // only useful thing to offer someone whose workspace list would not
        // load.
        router.replace("/onboarding");
      });
  }, [router, pathname, tab]);

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <div className="skeleton h-32 rounded-lg" />
    </div>
  );
}
```

- [ ] **Step 2: Replace the three legacy pages with resolvers**

Replace the entire contents of `app/(console)/overview/page.tsx`:

```typescript
import { WorkspaceResolver } from "@/components/workspace/WorkspaceResolver";

/** `/overview` predates workspaces and is still where sign-in lands. Keep
 * the URL working by resolving it to the right workspace's Overview rather
 * than breaking every bookmark and the middleware redirect at once. */
export default function OverviewRedirect() {
  return <WorkspaceResolver tab="overview" />;
}
```

Replace `app/(console)/jobs/page.tsx` identically with `tab="jobs"`, and `app/(console)/pools/page.tsx` with `tab="overview"` (a pool list is now the switcher; landing on a workspace is the useful answer).

Leave `app/(console)/jobs/[jobId]/page.tsx` alone — job detail keeps its URL (spec §2).

- [ ] **Step 3: Write onboarding**

Create `app/(console)/onboarding/page.tsx`. It has two actions: create a workspace (`createPool`, then `router.push(workspacePath(pool.id, "overview"))`) and a note that an invite link joins an existing one. Reuse `CreatePoolCard`'s submit logic from the current `app/(console)/pools/page.tsx:170-248` verbatim — the `createPool` call, the `NotAuthenticated` redirect, the `ApiError` detail message, and the disabled-while-empty button — changing only the success path to navigate into the new workspace instead of refetching a list:

```typescript
      const pool = await createPool(trimmed);
      toast.success("Workspace created", { description: pool.name });
      router.push(workspacePath(pool.id, "overview"));
```

Copy is the only other change: "Create a workspace" / "Name it after your team or your project. You can rename it later." plus a line reading "Been sent an invite link? Open it and you'll join that workspace." Do NOT link to `/pools/join` without a token — that route needs one and would only show an error.

- [ ] **Step 4: Add the compatibility redirects**

In `next.config.ts`, add to the `nextConfig` object:

```typescript
  async redirects() {
    return [
      // The pool detail page IS the workspace now. Permanent: these ids are
      // stable and the old URL is never coming back.
      {
        source: "/pools/:poolId",
        destination: "/w/:poolId/overview",
        permanent: true,
      },
      // Machines became personal property with their own home.
      { source: "/machines", destination: "/account/machines", permanent: true },
    ];
  },
```

`/pools/join` is NOT matched by `/pools/:poolId` — Next matches static segments before dynamic ones, and `app/(console)/pools/join/page.tsx` still exists. Confirm this in Step 6; live invite links depend on it.

- [ ] **Step 5: Update the middleware's post-sign-in target**

In `middleware.ts`, the signed-in `/sign-in` redirect currently sets `redirectUrl.pathname = "/overview"`. Leave that value as-is — `/overview` is now the resolver, which is exactly the right landing point — but update its comment:

```typescript
  // Signed in and asking for /sign-in: send them to /overview, which is now
  // a resolver rather than a page — it picks the right workspace (URL, then
  // cookie, then first by name) and replaces itself. Landing a returning
  // user on a single resource list was a stand-in for not having a console
  // home; sending them to a *specific* workspace from here would be a
  // second one, since middleware cannot know which.
```

- [ ] **Step 6: Run the suites**

Run: `npm test && npx tsc --noEmit`
Expected: PASS. `middleware.test.ts` should be unchanged and still green, since the redirect target did not change.

Then verify the redirect precedence manually:

```bash
npm run dev
# In another shell:
curl -sI localhost:3000/pools/join?token=fmi_x | head -1   # expect 200, NOT 308
curl -sI localhost:3000/pools/some-uuid | head -1          # expect 308
```

- [ ] **Step 7: Commit**

```bash
git add "app/(console)/onboarding" "app/(console)/overview" "app/(console)/jobs/page.tsx" "app/(console)/pools/page.tsx" components/workspace/WorkspaceResolver.tsx next.config.ts middleware.ts
git commit -m "feat(web): workspace resolvers, legacy redirects, and onboarding"
```

---

# Phase 4 — The five tabs

Task 10 is pure re-parenting; Tasks 11–14 assemble the tabs from the moved pieces.

---

### Task 10: Extract the pool page's four sections

The 668-line `pools/[poolId]/page.tsx` contains four components that already have clean boundaries. Move them out **verbatim** — including every comment, the optimistic-toggle-with-revert logic, and the revoked-machine filter. This task changes no behaviour.

**Files:**
- Create: `components/workspace/MemberTable.tsx` (from `pools/[poolId]/page.tsx:206-233`, `MemberRow`)
- Create: `components/workspace/YourMachines.tsx` (from `:244-441`, `YourMachinesSection` + `MachineToggleRow`)
- Create: `components/workspace/InviteManager.tsx` (from `:447-658`, `InviteSection`)
- Keep: `components/pools/ConnectPanel.tsx` unchanged, in place

**Interfaces:**
- Produces: `<MemberTable members ownerId />`, `<YourMachines poolId poolName />`, `<InviteManager poolId />`

- [ ] **Step 1: Move `MemberRow` into `MemberTable`**

Create `components/workspace/MemberTable.tsx` containing the `<table>` markup from `pools/[poolId]/page.tsx:155-176` plus the `MemberRow` function from `:206-233`, verbatim. Wrap it as:

```typescript
export function MemberTable({
  members,
  ownerId,
}: {
  members: PoolMember[];
  ownerId: string;
}) {
```

and replace `m.user_id === pool.owner_id` with `m.user_id === ownerId`.

- [ ] **Step 2: Move `YourMachinesSection`**

Create `components/workspace/YourMachines.tsx` with `YourMachinesSection` (renamed `YourMachines`) and `MachineToggleRow`, copied verbatim from `:244-441` including all imports (`listMachines`, `bindMachineToPool`, `unbindMachineFromPool`, `machineBadge`, `isOnline`, `toast`). Change nothing inside — in particular keep the revoked-machine filter and its comment, and keep the `#connect-panel` anchor link, which Task 12 preserves.

- [ ] **Step 3: Move `InviteSection`**

Create `components/workspace/InviteManager.tsx` with `InviteSection` renamed `InviteManager`, copied verbatim from `:447-658`.

- [ ] **Step 4: Typecheck**

Run: `npx tsc --noEmit`
Expected: clean. `pools/[poolId]/page.tsx` still has its own copies and still compiles — it is deleted in Task 18.

- [ ] **Step 5: Commit**

```bash
git add components/workspace/
git commit -m "refactor(web): extract member table, machine opt-in, and invite manager"
```

---

### Task 11: Overview and Jobs tabs

**Files:**
- Create: `app/(console)/w/[poolId]/overview/page.tsx`, `app/(console)/w/[poolId]/jobs/page.tsx`
- Create: `components/workspace/WorkspaceHeader.tsx`

**Interfaces:**
- Consumes: `useWorkspace()`, `isActiveJob`, `StateBadge`, `workspacePath`.
- Produces: `<WorkspaceHeader title action? />` — the name line, the member/online summary, and the "New job" button, shared by all five tabs.

- [ ] **Step 1: Write the shared header**

Create `components/workspace/WorkspaceHeader.tsx`. It reads `useWorkspace()` for `pool`, `members`, `machines`, renders the workspace name as `<h1 className="title">`, a `meta` line reading `N people · M machines online` (count `machines` with `status === "active"`), and a "New job" link to `workspacePath(pool.id, "submit")` styled with the same classes the current overview button uses (`app/(console)/overview/page.tsx:74-81`).

- [ ] **Step 2: Write the Overview tab**

Create `app/(console)/w/[poolId]/overview/page.tsx`. Take the three-`Stat` grid and the active-jobs list from the current `app/(console)/overview/page.tsx:97-165` verbatim, with these changes:

- Data comes from `useWorkspace()` — delete the page's own `useState`/`useEffect`/`load`/polling entirely, and the `TERMINAL` constant (now `isActiveJob` from `lib/job-scope`).
- The `Stat` labels become `Machines online` (value: `machines.filter(m => m.status === "active").length`, total `machines.length`), `Jobs running`, `Jobs finished`.
- Each active-job row gains the submitter under the name:

```typescript
                          <span className="block truncate font-mono text-xs text-muted-foreground">
                            {j.submitted_by ? `by ${j.submitted_by} · ` : ""}
                            {j.mode === "federated" ? "federated" : "independent"}
                            {j.created_at
                              ? ` · started ${new Date(j.created_at).toLocaleTimeString()}`
                              : ""}
                          </span>
```

- The empty state's copy changes to name the workspace: `"No jobs in this workspace yet."`

- [ ] **Step 3: Write the Jobs tab**

Create `app/(console)/w/[poolId]/jobs/page.tsx` from the current `app/(console)/jobs/page.tsx`, with the same substitution: data from `useWorkspace()`, no local fetching or polling, and a `Submitted by` column rendering `j.submitted_by ?? "—"`.

- [ ] **Step 4: Typecheck and route-exports**

Run: `npx tsc --noEmit && npx vitest run lib/route-exports.test.ts`
Expected: clean, PASS.

- [ ] **Step 5: Commit**

```bash
git add "app/(console)/w/[poolId]/overview" "app/(console)/w/[poolId]/jobs" components/workspace/WorkspaceHeader.tsx
git commit -m "feat(web): workspace Overview and Jobs tabs with submitter attribution"
```

---

### Task 12: Machines tab

Three answers to three questions where today's pool page stacks three sections: what compute does this workspace have, what am I contributing, and how do I add more.

**Files:**
- Create: `app/(console)/w/[poolId]/machines/page.tsx`
- Create: `components/workspace/PoolFleetTable.tsx`

**Interfaces:**
- Consumes: `useWorkspace()`, `<YourMachines>`, `<ConnectPanel>`, `machineBadge`, `isOnline`, `relativeTime`.

- [ ] **Step 1: Write the fleet table**

Create `components/workspace/PoolFleetTable.tsx`: a table over `PoolMachine[]` with columns Machine (name or `node_id`, monospace, with the online status dot), Owner (`owner_display_name ?? "unnamed"`), Trust (the `machineBadge` badge, styled with `MACHINE_BADGE_STYLES`/`MACHINE_BADGE_LABELS` exactly as `MachineToggleRow` does), and Last seen (`relativeTime(last_seen_at)`).

The online derivation must match the rest of the console exactly:

```typescript
  const revoked = machine.status === "revoked";
  const online = !revoked && isOnline(machine.last_seen_at);
```

Empty state: `"No machines serving this workspace yet. Tick one of yours in below, or connect a new one."`

- [ ] **Step 2: Write the tab**

Create `app/(console)/w/[poolId]/machines/page.tsx`:

```typescript
"use client";

import { ConnectPanel } from "@/components/pools/ConnectPanel";
import { PoolFleetTable } from "@/components/workspace/PoolFleetTable";
import { WorkspaceHeader } from "@/components/workspace/WorkspaceHeader";
import { YourMachines } from "@/components/workspace/YourMachines";
import { useWorkspace } from "@/components/workspace/WorkspaceProvider";

export default function WorkspaceMachinesPage() {
  const { pool, machines, state } = useWorkspace();
  if (state !== "ready" || pool === null) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        <div className="skeleton h-32 rounded-lg" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <WorkspaceHeader />

      <section className="mt-8">
        <h2 className="text-sm font-semibold">Serving this workspace</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Every machine your teammates have opted in, not only yours.
        </p>
        <div className="mt-3">
          <PoolFleetTable machines={machines} />
        </div>
      </section>

      <div className="mt-8">
        <YourMachines poolId={pool.id} poolName={pool.name} />
      </div>

      {/* The anchor `YourMachines`' empty state links to. Keep the id. */}
      <div id="connect-panel" className="mt-8">
        <h2 className="text-sm font-semibold">Connect a machine</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          No spare laptop? Point a Colab notebook or a rented pod at this
          workspace instead.
        </p>
        <div className="mt-4">
          <ConnectPanel poolId={pool.id} />
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Typecheck and route-exports**

Run: `npx tsc --noEmit && npx vitest run lib/route-exports.test.ts`
Expected: clean, PASS.

- [ ] **Step 4: Commit**

```bash
git add "app/(console)/w/[poolId]/machines" components/workspace/PoolFleetTable.tsx
git commit -m "feat(web): workspace Machines tab — the pool's whole fleet, not just yours"
```

---

### Task 13: People tab

**Files:**
- Create: `app/(console)/w/[poolId]/people/page.tsx`

- [ ] **Step 1: Write the tab**

Create `app/(console)/w/[poolId]/people/page.tsx` rendering `<WorkspaceHeader />` then `<MemberTable members={members} ownerId={pool.owner_id} />` from `useWorkspace()`. For the owner only (`isOwner`), append a single line beneath the table:

```typescript
      {isOwner && (
        <p className="mt-4 text-sm text-muted-foreground">
          Want to add someone?{" "}
          <Link
            href={workspacePath(pool.id, "settings")}
            className="text-primary hover:underline"
          >
            Manage the invite link in Settings
          </Link>
          .
        </p>
      )}
```

The invite manager itself is NOT rendered here — one copy, in Settings (spec §5).

- [ ] **Step 2: Typecheck and route-exports**

Run: `npx tsc --noEmit && npx vitest run lib/route-exports.test.ts`
Expected: clean, PASS.

- [ ] **Step 3: Commit**

```bash
git add "app/(console)/w/[poolId]/people"
git commit -m "feat(web): workspace People tab"
```

---

### Task 14: Settings tab

**Files:**
- Create: `app/(console)/w/[poolId]/settings/page.tsx`
- Create: `components/workspace/RenameWorkspace.tsx`

**Interfaces:**
- Consumes: `renamePool` (Task 6), `useWorkspace().reload`, `<InviteManager>`.

- [ ] **Step 1: Write the rename form**

Create `components/workspace/RenameWorkspace.tsx`:

```typescript
"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { ApiError, NotFound, renamePool } from "@/lib/cloud-api";

export function RenameWorkspace({
  poolId,
  currentName,
  onRenamed,
}: {
  poolId: string;
  currentName: string;
  onRenamed: () => void;
}) {
  const [name, setName] = useState(currentName);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmed = name.trim();
  const unchanged = trimmed === currentName;

  async function save() {
    if (!trimmed || unchanged) return;
    setSaving(true);
    setError(null);
    try {
      // The API trims and caps at 200 characters, so its response — not the
      // string we sent — is what the name actually became.
      const updated = await renamePool(poolId, trimmed);
      setName(updated.name);
      onRenamed();
      toast.success("Workspace renamed", { description: updated.name });
    } catch (err) {
      if (err instanceof NotFound) {
        // Owner-only, and the API answers 404 for "not the owner" exactly as
        // it does for "no such pool" — so this cannot be reported as a
        // permissions problem without guessing which one it was.
        setError("This workspace can't be renamed from here.");
      } else {
        setError(
          err instanceof ApiError ? err.detail : "Couldn't rename it. Try again."
        );
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <section>
      <h2 className="text-sm font-semibold">Name</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          save();
        }}
        className="mt-3 flex flex-wrap items-start gap-2"
      >
        <div className="min-w-0 flex-1">
          <Input
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setError(null);
            }}
            aria-label="Workspace name"
            disabled={saving}
            maxLength={200}
          />
          {error && <p className="mt-1.5 text-xs text-destructive">{error}</p>}
        </div>
        <button
          type="submit"
          disabled={saving || !trimmed || unchanged}
          className="interactive rounded-md bg-primary px-3.5 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </form>
    </section>
  );
}
```

- [ ] **Step 2: Write the tab**

Create `app/(console)/w/[poolId]/settings/page.tsx` rendering, from `useWorkspace()`:

- `<WorkspaceHeader />`
- `<RenameWorkspace poolId={pool.id} currentName={pool.name} onRenamed={reload} />` — owner only
- `<InviteManager poolId={pool.id} />` — owner only, unchanged from Task 10
- A "Details" block: workspace id (monospace), created (`relativeTime(pool.created_at)`), owner (the member whose `user_id === pool.owner_id`, by `display_name`)

For a non-owner, render only the Details block plus one line: `"Only this workspace's owner can rename it or manage its invite link."` Do not render disabled controls — a non-owner's 404 is indistinguishable from the pool not existing, so there is nothing honest for a greyed-out button to promise.

- [ ] **Step 3: Typecheck and route-exports**

Run: `npx tsc --noEmit && npx vitest run lib/route-exports.test.ts`
Expected: clean, PASS.

- [ ] **Step 4: Commit**

```bash
git add "app/(console)/w/[poolId]/settings" components/workspace/RenameWorkspace.tsx
git commit -m "feat(web): workspace Settings tab — rename and invite management"
```

---

# Phase 5 — Personal area, submit, and cleanup

---

### Task 15: Submit becomes workspace-scoped

**Files:**
- Create: `app/(console)/w/[poolId]/submit/page.tsx` (from `app/(console)/submit/page.tsx`)
- Modify: `lib/pool-selection.ts`, `lib/pool-selection.test.ts`
- Replace: `app/(console)/submit/page.tsx` (resolver)

- [ ] **Step 1: Update the pure predicates first**

`NO_POOL` and `isPoolSelected` exist solely to model the public-queue default that no longer exists. Delete both from `lib/pool-selection.ts`, keeping `hasNoWorkersOnline` and its docstring, with the second paragraph rewritten:

```typescript
/** Whether the workspace this job will run in has nobody online to run it
 * right now — the condition that gates the amber "0 workers online" banner.
 *
 * `null` is never "zero workers": a workspace whose summary has not loaded
 * yet is not a workspace known to be empty, so the banner stays hidden
 * rather than firing on absence of data. */
export function hasNoWorkersOnline(pool: PoolSummary | null): boolean {
  return pool !== null && pool.machines_online === 0;
}
```

Delete the `NO_POOL` and `isPoolSelected` blocks from `lib/pool-selection.test.ts`, keeping every `hasNoWorkersOnline` case including the `null` one.

- [ ] **Step 2: Run the test**

Run: `npx vitest run lib/pool-selection.test.ts`
Expected: PASS with the remaining cases.

- [ ] **Step 3: Move and rewrite the submit page**

Create `app/(console)/w/[poolId]/submit/page.tsx` from `app/(console)/submit/page.tsx`, changing exactly this:

- Delete the `pools`/`poolId` state, the `listPools()` effect, and the whole `<Select>` block at `:219-238`.
- Take the workspace from `useWorkspace()` and render it as a static line where the selector was:

```typescript
            <div>
              <Label>Workspace</Label>
              <p className="mt-1.5 text-sm">
                Runs in <span className="font-medium">{pool.name}</span>
              </p>
            </div>
```

- The unsandboxed-notice at `:239` was gated on `isPoolSelected(poolId)`; it now always applies, since every job runs in a workspace. Render it unconditionally.
- The `hasNoWorkersOnline` banner takes the workspace's own summary. `useWorkspace()` exposes `machines`, so compute it directly instead: `machines.filter(m => m.status === "active").length === 0`.
- `submitFromRepo(..., poolId)` becomes `submitFromRepo(..., pool.id)` — the call signature is unchanged.
- On success, navigate to `/jobs/${result.job_id}` exactly as today.

- [ ] **Step 4: Turn the old route into a resolver**

Replace `app/(console)/submit/page.tsx` entirely:

```typescript
import { WorkspaceResolver } from "@/components/workspace/WorkspaceResolver";

/** `/submit` no longer names a place to submit from — every job belongs to
 * a workspace. Resolve to one and let the user submit there. */
export default function SubmitRedirect() {
  return <WorkspaceResolver tab="overview" />;
}
```

`tab="overview"`, not `"submit"`: `submit` is not in `WORKSPACE_TABS`, and dropping someone into a form for a workspace they did not choose is worse than showing them the workspace and letting them press the button.

- [ ] **Step 5: Typecheck and run the suite**

Run: `npx tsc --noEmit && npm test`
Expected: clean, PASS.

- [ ] **Step 6: Commit**

```bash
git add "app/(console)/w/[poolId]/submit" "app/(console)/submit" lib/pool-selection.ts lib/pool-selection.test.ts
git commit -m "feat(web): submit is workspace-scoped; the public-queue option is gone"
```

---

### Task 16: Personal machines and earlier jobs

**Files:**
- Create: `app/(console)/account/machines/page.tsx` (moved from `app/(console)/machines/page.tsx`)
- Create: `app/(console)/account/earlier-jobs/page.tsx`
- Delete: `app/(console)/machines/page.tsx`, `app/(console)/machines/layout.tsx`

- [ ] **Step 1: Move the machines page**

`git mv "app/(console)/machines/page.tsx" "app/(console)/account/machines/page.tsx"` and the same for `layout.tsx`. Change only the heading copy and the layout's `metadata.title`:

- `<h1 className="title">My machines</h1>`
- Subtitle: `"Machines you own. Tick one into a workspace to let that team place jobs on it."`

Everything else — the revoke flow, the pool chips, the badges, the enrol instructions — stays exactly as it is. The `/machines` → `/account/machines` redirect from Task 9 keeps old links working.

- [ ] **Step 2: Write the earlier-jobs page**

Create `app/(console)/account/earlier-jobs/page.tsx`:

```typescript
"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Warning } from "@phosphor-icons/react";
import { StateBadge } from "@/components/jobs/StateBadge";
import { earlierJobs } from "@/lib/job-scope";
import { NotAuthenticated, listJobs, type JobRecord } from "@/lib/cloud-api";

// Jobs from before pools shipped: `pool_id` is null and there is no
// workspace to file them under. They stay readable — a tester who submitted
// last week must not open the console to find their history gone — but
// nothing new can land here, because every new job belongs to a workspace.
// This list empties itself over time, and the route can go with it.

export default function EarlierJobsPage() {
  const router = useRouter();
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    listJobs()
      .then((all) => {
        setJobs(earlierJobs(all));
        setState("ready");
        setError(null);
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push("/sign-in?next=/account/earlier-jobs");
          return;
        }
        setError(err instanceof Error ? err.message : "Couldn't load these.");
        setState("error");
      });
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);
  // No polling: these are finished jobs from before pools existed. Nothing
  // about them can change.

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <h1 className="title">Earlier jobs</h1>
      <p className="mt-1.5 text-sm text-muted-foreground">
        Jobs you ran before workspaces existed. Read-only — new jobs belong
        to a workspace.
      </p>

      <div className="mt-6">
        {state === "loading" ? (
          <div className="space-y-px">
            <div className="skeleton h-14" />
            <div className="skeleton h-14" />
          </div>
        ) : state === "error" ? (
          <div className="flex flex-col items-center gap-3 py-12 text-center">
            <Warning className="h-5 w-5 text-destructive" weight="fill" />
            <p className="text-sm text-muted-foreground">{error}</p>
            <button
              type="button"
              onClick={load}
              className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-white/[0.06]"
            >
              Try again
            </button>
          </div>
        ) : jobs.length === 0 ? (
          <p className="py-12 text-center text-sm text-muted-foreground">
            Nothing here — every job you have run belongs to a workspace.
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {jobs.map((j) => (
              <li key={j.job_id}>
                <Link
                  href={`/jobs/${j.job_id}`}
                  className="flex items-center gap-3 py-3 transition-colors hover:bg-white/[0.03]"
                >
                  <span className="min-w-0 flex-1 truncate font-mono text-sm">
                    {j.spec?.metadata?.name ?? j.name ?? j.job_id}
                  </span>
                  <StateBadge state={j.state} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Typecheck and route-exports**

Run: `npx tsc --noEmit && npx vitest run lib/route-exports.test.ts`
Expected: clean, PASS.

- [ ] **Step 4: Commit**

```bash
git add "app/(console)/account" "app/(console)/machines"
git commit -m "feat(web): personal machines under My account; read-only earlier jobs"
```

---

### Task 17: Job detail breadcrumb

**Files:**
- Modify: `app/(console)/jobs/[jobId]/page.tsx`

- [ ] **Step 1: Add the breadcrumb**

The page's back link currently points at `/jobs`. Replace it with one derived from the job's own `pool_id` — which Task 1 now supplies, so the page works when deep-linked and never needs the list:

```typescript
import { earlierJobs } from "@/lib/job-scope";
import { workspacePath } from "@/lib/workspace-scope";

// ...once `job` has loaded:
const backHref =
  job.pool_id != null ? workspacePath(job.pool_id, "jobs") : "/account/earlier-jobs";
const backLabel = job.pool_id != null ? "Jobs" : "Earlier jobs";
```

and render it with the existing `<ArrowLeft />` link markup. Import `earlierJobs` only if the page needs the predicate; otherwise use the `!= null` check shown, which covers both `null` and `undefined` in one comparison.

If `job.submitted_by` is present, render it beside the job name as `by {job.submitted_by}` in the existing `meta` style.

- [ ] **Step 2: Typecheck**

Run: `npx tsc --noEmit`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add "app/(console)/jobs/[jobId]/page.tsx"
git commit -m "feat(web): job detail names its workspace"
```

---

### Task 18: Delete the old surface and document the vocabulary

**Files:**
- Delete: `app/(console)/pools/[poolId]/` (page + layout), `components/shell/ConsoleShell.tsx`
- Modify: `AGENTS.md` (repo root), `PROGRESS.md` (workspace root)

- [ ] **Step 1: Confirm nothing imports what is about to go**

Run:

```bash
grep -rn "ConsoleShell" --include="*.tsx" --include="*.ts" app components lib
grep -rn "pools/\[poolId\]" --include="*.tsx" --include="*.ts" app components lib
```

Expected: no hits. If `ConsoleShell` still appears, Task 8 Step 3 was not applied.

- [ ] **Step 2: Delete**

```bash
git rm -r "app/(console)/pools/[poolId]" components/shell/ConsoleShell.tsx
```

Keep `app/(console)/pools/join/` and `app/(console)/pools/layout.tsx` — live invite links depend on the first, and the second supplies the route group's metadata.

- [ ] **Step 3: Add the vocabulary note to `AGENTS.md`**

Append to `flashml-cloud/AGENTS.md`, under a new `## Vocabulary` heading:

```markdown
## Vocabulary

The console UI says **workspace**; the API, the database and the TypeScript
types say **pool**. They are the same thing. The rename was deliberate and
UI-only — see
`docs/superpowers/specs/2026-08-03-workspace-console-design.md` §1.5. Do not
"fix" one side to match the other: renaming through the API would be a
breaking change to a shipped release plus a table migration, for a naming
win.
```

- [ ] **Step 4: Run everything**

Run, from the workspace root: `make test`
Expected: both suites green. Then:

```bash
cd flashml-cloud/apps/web && npx tsc --noEmit && npm run build
```

Expected: a clean production build. `next build` is what catches an invalid route module that `tsc` reports only in generated types — the failure mode `route-exports.test.ts` exists to prevent.

- [ ] **Step 5: Manual smoke test**

```bash
cd flashml-cloud && ./scripts/dev.sh --all
```

Walk the loop and confirm each: sign in lands on a workspace · the switcher lists your workspaces and moves between them · all five tabs render · Machines shows a teammate's machine · renaming in Settings updates the switcher · "New job" submits into the workspace · `/pools/<id>` redirects to `/w/<id>/overview` · `/machines` redirects to `/account/machines` · **`/pools/join?token=…` still opens the join page, not a redirect** · a signed-in user with no workspaces lands on `/onboarding`.

- [ ] **Step 6: Log the slice in `PROGRESS.md`**

Add an entry at the top of `## Entries`, following the file's own template (What/why · How verified · Gotchas · Next). "How verified" must carry real numbers — the two suite counts from Step 4 — per the log's rule 1: evidence or it didn't happen.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(web): retire the pool detail page; document the workspace/pool split"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1.1 pool is the workspace | 7, 8 |
| §1.2 machines personal, jobs always in a workspace | 15, 16 |
| §1.3 earlier jobs read-only | 5, 16 |
| §1.4 five tabs, Submit is a button | 8, 11–14 |
| §1.5 UI-only rename | 18 |
| §1.6 three API additions | 1, 2, 3 |
| §2 routes, job detail stays put, redirects | 7, 9, 17 |
| §3 resolution order, provider, 404 copy | 4, 7, 9 |
| §4a/b/c | 1, 2, 3 |
| §5 per-tab content, `pool-selection` cleanup | 11–15 |
| §6 component extraction | 10 |
| §7 first run | 9 |
| §8 testing | 1–6, 18 |
| §10 AGENTS.md note | 18 |

**Placeholder scan:** none. Every code step carries the code; the extraction steps in Tasks 10–12 name exact source line ranges rather than restating moved code, which is the precise instruction, not a placeholder.

**Type consistency checked:** `PoolMachine` (Task 6) is what `listPoolMachines` returns (Task 6), what the provider stores (Task 7), and what `PoolFleetTable` consumes (Task 12). `WORKSPACE_TABS` (Task 4) drives `TAB_META` (Task 8) and `workspacePath`'s `tab` parameter, which also accepts `"submit"` — used in Task 11's header and excluded from the rail in Task 8. `isActiveJob` is defined once (Task 5) and used by the provider's polling rule (Task 7) and the Overview tab (Task 11). `reload` on the context (Task 7) is what `RenameWorkspace`'s `onRenamed` receives (Task 14).

**One thing an implementer should know:** Task 5 references `JobRecord.pool_id`, which Task 6 adds. They are independent and either order works, but running Task 6 first avoids a transient type error.
