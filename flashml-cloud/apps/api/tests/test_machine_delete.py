"""Deleting a machine: ``DELETE /v1alpha1/machines/{machine_id}``.

Revoked machines accumulated in "My machines" for ever, each one a full
detail row, and the console had no way to clear them. This is the API half of
the Delete action, and the whole of the design argument is a single tension:

* the OWNER wants the row gone, detail and all;
* the LEDGER cannot let it go. Six tables reference ``public.machines(id)``
  with ``on delete cascade`` — ``contributions`` (accepted-work credit),
  ``attempts`` (evidence), ``verifications``, ``machine_pools``, ``listings``
  and ``matches`` — so a real ``DELETE`` would make somebody's contribution
  total FALL because they tidied their fleet. Workspace hard rules 3 and 4,
  and exactly what ``db.contributions_for_owner`` warned about in prose long
  before this route existed.

So the row becomes a TOMBSTONE: ``status = 'deleted'`` (migration 0028) with
every column that described the device scrubbed by the same UPDATE, and gone
from the fleet listing. This file pins both halves — that the detail is
really gone, and that the history really is not — because each fails
silently on its own: a scrub that missed a column leaks a hostname nobody can
see any more to delete, and a cascade that fired reduces a number nothing in
this system ever debits.

Route-level throughout, because the ordering rule (revoke first) and the
information-hiding rule (404, never 403) are properties of the HTTP surface
the console consumes. The repository-level facts that have no route —
re-enrolment, the CHECK constraint — are asserted directly at the bottom.
"""
from __future__ import annotations

import json
import time
import uuid

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import enrolment
from flashml_cloud_api import marketplace as marketmod
from flashml_cloud_api.app import create_cloud_app
from flashml_cloud_api.settings import Settings

JWT_SECRET = "test-jwt-secret-long-enough-for-hs256-abcdef"

#: A capability snapshot the marketplace ladder can class, so a listing can
#: exist at all. The exact class does not matter here; that it HAS one does.
GPU_24GB = {"gpus": [{"memory_total_mb": 24564}]}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def settings(postgres_dsn) -> Settings:
    return Settings(
        supabase_url="https://yualksqjjvlfscbbsygq.supabase.co",
        supabase_jwt_secret=JWT_SECRET,
        supabase_service_key="service-key-not-used-here",
        coordinator_url="http://coordinator.internal:8100",
        coordinator_operator_token="op-secret-do-not-leak-3f9c1b",
        require_auth=True,
        database_url=postgres_dsn,
        console_url="https://console.example",
    )


class SilentTransport(httpx.AsyncBaseTransport):
    """Deleting a machine is a decision about this database and nothing else.
    A call to the coordinator here would mean the route had made retiring a
    row depend on a service that can be down."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"the delete route contacted {request.url}")


@pytest.fixture
def client(settings, postgres_dsn):
    def connect() -> psycopg.Connection:
        conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        return conn

    app = create_cloud_app(settings, connect=connect, transport=SilentTransport())
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def _new_user(db) -> str:
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (%s)", (user_id,))
        cur.execute(
            "insert into public.profiles (id, admitted_at) values (%s, now())",
            (user_id,),
        )
    return user_id


def _browser_jwt(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": time.time() + 3600},
        JWT_SECRET,
        algorithm="HS256",
    )


def _auth(user_id: str) -> dict:
    return {"Authorization": f"Bearer {_browser_jwt(user_id)}"}


def _node_id(tag: str) -> str:
    """``machines.node_id`` is globally unique and the database is shared by
    the whole session, so every node id has to be unique across tests too."""
    return f"del-{tag}-{uuid.uuid4().hex[:10]}"


def _machine(
    db,
    owner: str,
    *,
    status: str = "active",
    name: str | None = "phongs-macbook-air",
    platform: str | None = "macOS-15",
    capabilities: dict | None = None,
    lifecycle: str = "persistent",
) -> str:
    """An enrolled machine with the detail a real one carries.

    Written directly rather than through the device-code dance: the point of
    every test here is what happens to those columns, and the flow that
    populates them is tested in ``test_enrolment.py``. The token prefix and
    last-seen are set because they are part of what deletion must scrub.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.machines
                (owner_id, node_id, name, platform, status, capabilities,
                 lifecycle, token_hash, token_prefix, last_seen_at,
                 sandbox_capable, argv_capable, unsandboxed_argv_capable,
                 module_capable)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now(),
                    true, true, true, true)
            returning id
            """,
            (
                owner,
                _node_id("m"),
                name,
                platform,
                status,
                json.dumps(capabilities if capabilities is not None else GPU_24GB),
                lifecycle,
                f"hash-{uuid.uuid4().hex}",
                "fm_abcdef1234",
            ),
        )
        return str(cur.fetchone()["id"])


def _revoked_machine(db, owner: str, **kwargs) -> str:
    machine_id = _machine(db, owner, **kwargs)
    with db.cursor() as cur:
        cur.execute(
            "update public.machines set status = 'revoked', revoked_at = now()"
            " where id = %s",
            (machine_id,),
        )
    return machine_id


def _row(db, machine_id: str) -> dict:
    with db.cursor() as cur:
        cur.execute("select * from public.machines where id = %s", (machine_id,))
        return cur.fetchone()


def _credit(db, machine_id: str, job_id: str, task_id: str = "task-000") -> None:
    """One accepted contribution, written the way both credit paths write it."""
    with db.cursor() as cur:
        cur.execute(
            "insert into public.contributions (machine_id, job_id, task_id)"
            " values (%s, %s, %s) on conflict do nothing",
            (machine_id, job_id, task_id),
        )


def _attempt(db, machine_id: str, job_id: str) -> str:
    lease_id = f"lease-{uuid.uuid4().hex[:12]}"
    with db.cursor() as cur:
        cur.execute(
            "insert into public.attempts (lease_id, machine_id, job_id, task_id)"
            " values (%s, %s, %s, %s)",
            (lease_id, machine_id, job_id, "task-000"),
        )
    return lease_id


def _delete(client, user_id: str, machine_id: str) -> httpx.Response:
    return client.delete(f"/v1alpha1/machines/{machine_id}", headers=_auth(user_id))


def _fleet_ids(client, user_id: str) -> list[str]:
    r = client.get("/v1alpha1/machines", headers=_auth(user_id))
    assert r.status_code == 200, r.text
    return [m["id"] for m in r.json()]


# ---------------------------------------------------------------------------
# the route: who may delete what, and when
# ---------------------------------------------------------------------------


def test_deleting_needs_a_jwt(client):
    assert client.delete(f"/v1alpha1/machines/{uuid.uuid4()}").status_code == 401


def test_an_owner_deletes_a_revoked_machine_and_it_leaves_the_fleet(client, db):
    """The whole product need in one assertion: the row the owner is tired of
    looking at stops being listed."""
    owner = _new_user(db)
    machine = _revoked_machine(db, owner)
    assert machine in _fleet_ids(client, owner)

    r = _delete(client, owner, machine)
    assert r.status_code == 200, r.text
    assert r.json() == {"machine_id": machine, "status": "deleted"}
    assert machine not in _fleet_ids(client, owner)


def test_an_active_machine_is_refused_and_told_to_revoke_first(client, db):
    """Revoking is the security action and deleting is tidying. Folding them
    together would put "kill this machine's credential" behind a button
    labelled Delete, and for a rented machine it would skip the mid-task
    guard the revoke route carries."""
    owner = _new_user(db)
    machine = _machine(db, owner)

    r = _delete(client, owner, machine)
    assert r.status_code == 409, r.text
    assert "revoke it first" in r.json()["detail"]

    assert machine in _fleet_ids(client, owner)
    assert _row(db, machine)["status"] == "active"


def test_a_pending_machine_is_refused_the_same_way(client, db):
    """A machine that started enrolling and never redeemed its token is not
    revoked either. The gate is `status == 'revoked'`, not `status !=
    'active'` — anything else is a second list of states to keep in step."""
    owner = _new_user(db)
    machine = _machine(db, owner, status="pending")
    r = _delete(client, owner, machine)
    assert r.status_code == 409, r.text
    assert _row(db, machine)["status"] == "pending"


def test_somebody_elses_machine_is_a_404_not_a_403(client, db):
    """The same fold every machines route keeps: a 403 for "exists but isn't
    yours" confirms to a guesser that the id is real. It must also be the
    same answer whatever the machine's status, or the code becomes an oracle
    for that instead."""
    owner, stranger = _new_user(db), _new_user(db)
    revoked = _revoked_machine(db, owner)
    active = _machine(db, owner)

    assert _delete(client, stranger, revoked).status_code == 404
    assert _delete(client, stranger, active).status_code == 404
    assert _row(db, revoked)["status"] == "revoked"
    assert _row(db, active)["status"] == "active"


def test_an_unknown_id_is_a_404(client, db):
    assert _delete(client, _new_user(db), str(uuid.uuid4())).status_code == 404


def test_an_id_that_is_not_even_a_uuid_is_the_same_404(client, db):
    """Not a 500 and not a 422: a malformed id is indistinguishable from one
    that simply is not yours, which is the answer that reveals nothing."""
    assert _delete(client, _new_user(db), "not-a-uuid").status_code == 404


def test_deleting_twice_is_a_404_not_a_second_success(client, db):
    """Idempotent in the sense that matters — the second call changes nothing
    and says so. Reporting a fresh 200 for a machine that was already gone is
    how a console shows a delete that did not happen as one that did."""
    owner = _new_user(db)
    machine = _revoked_machine(db, owner)
    assert _delete(client, owner, machine).status_code == 200
    assert _delete(client, owner, machine).status_code == 404


def test_a_deleted_machine_cannot_be_revoked_back_into_the_fleet(client, db):
    """`deleted` is terminal. Revoking one would move it back to `revoked` —
    and back into `list_machines_for_owner` as a blank row, name and
    capabilities already scrubbed, that nobody asked to see again."""
    owner = _new_user(db)
    machine = _revoked_machine(db, owner)
    assert _delete(client, owner, machine).status_code == 200

    r = client.post(f"/v1alpha1/machines/{machine}/revoke", headers=_auth(owner))
    assert r.status_code == 404, r.text
    assert _row(db, machine)["status"] == "deleted"
    assert machine not in _fleet_ids(client, owner)


# ---------------------------------------------------------------------------
# the tombstone: what is scrubbed, and what survives
# ---------------------------------------------------------------------------


def test_every_column_that_described_the_device_is_cleared(client, db):
    """The owner's actual complaint is the DETAIL, not the row. A tombstone
    that kept the hostname would answer the invariant and not the person: the
    machine is unlisted, and its name is still sitting in a table that four
    other queries join."""
    owner = _new_user(db)
    machine = _revoked_machine(db, owner)
    assert _delete(client, owner, machine).status_code == 200

    row = _row(db, machine)
    assert row["status"] == "deleted"
    assert row["deleted_at"] is not None
    assert row["name"] is None
    assert row["platform"] is None
    assert row["capabilities"] == {}
    assert row["token_hash"] is None
    assert row["token_prefix"] is None
    assert row["last_seen_at"] is None
    assert row["sandbox_capable"] is False
    assert row["argv_capable"] is False
    assert row["unsandboxed_argv_capable"] is False
    assert row["module_capable"] is False


def test_the_id_owner_and_node_id_survive_because_the_history_needs_them(
    client, db
):
    """A tombstone is not an empty row. The id is what six foreign keys
    resolve, the owner is what scopes the credit total, and `node_id` is the
    opaque `fn-<hex>` the agent matches on when the same machine enrols
    again — none of the three describes the hardware."""
    owner = _new_user(db)
    machine = _revoked_machine(db, owner)
    before = _row(db, machine)
    assert _delete(client, owner, machine).status_code == 200

    after = _row(db, machine)
    assert str(after["id"]) == machine
    assert after["owner_id"] == before["owner_id"]
    assert after["node_id"] == before["node_id"]
    assert after["created_at"] == before["created_at"]
    assert after["revoked_at"] == before["revoked_at"]


def test_accepted_work_still_counts_after_the_machine_is_deleted(client, db):
    """THE INVARIANT THE HARD DELETE WOULD HAVE BROKEN. `contributions`
    cascades on `machines.id`, so removing the row would silently subtract
    accepted work from a total nothing in this system ever debits — a number
    that falls when somebody tidies their fleet is indistinguishable from a
    bug, and it penalises exactly the hygiene we want."""
    owner = _new_user(db)
    machine = _revoked_machine(db, owner)
    job = f"cjob-{uuid.uuid4().hex[:12]}"
    _credit(db, machine, job, "task-000")
    _credit(db, machine, job, "task-001")

    before = client.get("/v1alpha1/me/contributions", headers=_auth(owner)).json()
    assert before["accepted_tasks"] == 2

    assert _delete(client, owner, machine).status_code == 200

    after = client.get("/v1alpha1/me/contributions", headers=_auth(owner)).json()
    assert after["accepted_tasks"] == 2, (
        "deleting a machine must not erase the work it was credited for"
    )
    assert after["jobs_contributed_to"] == before["jobs_contributed_to"]

    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.contributions where machine_id = %s",
            (machine,),
        )
        assert cur.fetchone()["n"] == 2


def test_attempt_evidence_survives_the_delete(client, db):
    """`attempts` cascades too, and it is the evidence trail behind every
    settlement and every acceptance rate. Hard rule 4 distinguishes attempted
    from accepted work everywhere money is involved; a delete that erased the
    attempted half would leave the accepted half unauditable."""
    owner = _new_user(db)
    machine = _revoked_machine(db, owner)
    lease = _attempt(db, machine, f"cjob-{uuid.uuid4().hex[:12]}")

    assert _delete(client, owner, machine).status_code == 200

    with db.cursor() as cur:
        cur.execute("select machine_id from public.attempts where lease_id = %s",
                    (lease,))
        row = cur.fetchone()
    assert row is not None and str(row["machine_id"]) == machine


# ---------------------------------------------------------------------------
# the book: a deleted machine keeps no open ask
# ---------------------------------------------------------------------------


def test_an_open_ask_is_withdrawn_with_the_machine(client, db):
    """A listing outliving its machine is an offer nothing can ever fill.
    Withdrawn through `marketplace.withdraw_listing` — the same path the
    console's own withdraw button uses — rather than deleted, so the ask
    keeps its terminal state and the price series keeps its cause."""
    owner = _new_user(db)
    machine = _revoked_machine(db, owner)
    listing = marketmod.create_listing(
        db, machine_id=machine, owner_id=owner, ask_zc_per_hour=1200
    )

    assert _delete(client, owner, machine).status_code == 200

    with db.cursor() as cur:
        cur.execute("select state from public.listings where id = %s",
                    (str(listing["id"]),))
        assert cur.fetchone()["state"] == "withdrawn"


def test_a_paused_ask_is_withdrawn_too(client, db):
    """`paused` still occupies the book — it is an offer its owner intends to
    resume. Only `withdrawn` is terminal, so it is the only state a delete
    may leave alone."""
    owner = _new_user(db)
    machine = _revoked_machine(db, owner)
    listing = marketmod.create_listing(
        db, machine_id=machine, owner_id=owner, ask_zc_per_hour=800
    )
    assert marketmod.pause_listing(
        db, listing_id=str(listing["id"]), owner_id=owner
    ) is True

    assert _delete(client, owner, machine).status_code == 200

    with db.cursor() as cur:
        cur.execute("select state from public.listings where id = %s",
                    (str(listing["id"]),))
        assert cur.fetchone()["state"] == "withdrawn"


def test_the_withdrawal_records_why_the_book_moved(client, db):
    """Every point in `price_observations` names the event that caused it —
    that is what makes the series evidence rather than a story. Withdrawing
    through the repository is what keeps that true here; a hand-written
    UPDATE would have removed an ask from the book and left no point at all.
    """
    owner = _new_user(db)
    machine = _revoked_machine(db, owner)
    listing = marketmod.create_listing(
        db, machine_id=machine, owner_id=owner, ask_zc_per_hour=1500
    )
    klass = listing["capability_class"]

    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.price_observations"
            " where capability_class = %s and cause = 'listing'",
            (klass,),
        )
        before = cur.fetchone()["n"]

    assert _delete(client, owner, machine).status_code == 200

    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.price_observations"
            " where capability_class = %s and cause = 'listing'",
            (klass,),
        )
        assert cur.fetchone()["n"] == before + 1


def test_a_refused_delete_leaves_the_ask_exactly_where_it_was(client, db):
    """The withdrawals and the tombstone commit as one. A 409 that had
    already closed somebody's listing would be a refusal that changed the
    market."""
    owner = _new_user(db)
    machine = _machine(db, owner)  # active: the delete will be refused
    listing = marketmod.create_listing(
        db, machine_id=machine, owner_id=owner, ask_zc_per_hour=900
    )

    assert _delete(client, owner, machine).status_code == 409

    with db.cursor() as cur:
        cur.execute("select state from public.listings where id = %s",
                    (str(listing["id"]),))
        assert cur.fetchone()["state"] == "open"


def test_a_deleted_machine_cannot_be_listed_again(client, db):
    """`delete_machine_row` scrubs `capabilities` to `{}`, and
    `capability_class({})` is a perfectly good `cpu-small` — so without an
    explicit refusal the owner could post a fresh ask against a machine they
    had retired. Same 404 as an id that was never real: a tombstone is not a
    machine."""
    owner = _new_user(db)
    machine = _revoked_machine(db, owner)
    assert _delete(client, owner, machine).status_code == 200

    r = client.post(
        "/v1alpha1/market/listings",
        headers=_auth(owner),
        json={"machine_id": machine, "ask_zc_per_hour": 100},
    )
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# workspaces: a deleted machine is nobody's compute
# ---------------------------------------------------------------------------


def test_deleting_unbinds_the_machine_from_every_workspace(client, db):
    """`list_pool_machines` deliberately shows REVOKED machines to teammates
    — the console badges them. A deleted one is a different fact: its owner
    retired it, and a workspace fleet view still listing it would overstate
    the pool's compute to every member looking at it."""
    owner = _new_user(db)
    pool_id = str(dbmod.create_pool(db, name="Lab", owner_id=owner)["id"])
    machine = _revoked_machine(db, owner)
    dbmod.bind_machine_pool(db, machine_id=machine, pool_id=pool_id)
    assert dbmod.pool_ids_bound_to_machine(db, machine) == [pool_id]

    assert _delete(client, owner, machine).status_code == 200

    assert dbmod.pool_ids_bound_to_machine(db, machine) == []
    assert [str(m["id"]) for m in dbmod.list_pool_machines(db, pool_id)] == []


def test_a_refused_delete_leaves_the_workspace_bindings_alone(client, db):
    """The one place the order differs from `revoke_sandbox_machine`, which
    unbinds on every call. Here a call that changes nothing must change
    NOTHING — unbinding a working machine on the way to answering 409 would
    quietly take it out of its team's pool."""
    owner = _new_user(db)
    pool_id = str(dbmod.create_pool(db, name="Lab", owner_id=owner)["id"])
    machine = _machine(db, owner)  # active
    dbmod.bind_machine_pool(db, machine_id=machine, pool_id=pool_id)

    assert _delete(client, owner, machine).status_code == 409

    assert dbmod.pool_ids_bound_to_machine(db, machine) == [pool_id]


# ---------------------------------------------------------------------------
# below the route: re-enrolment, and the constraint itself
# ---------------------------------------------------------------------------


def test_a_deleted_machine_can_be_re_enrolled_by_its_owner(client, db):
    """Delete must not be a one-way door, for the same reason revoke is not.
    The tombstone keeps its `node_id`, so without the matching branch in
    `approve_device_code` a machine its owner deleted could never enrol again
    — "this machine is already enrolled" for ever, with no way back short of
    deleting the agent's identity file.

    Re-enrolment REUSES the row, which is what keeps the credit history
    attached to the machine it belongs to instead of splitting it in two.
    """
    owner = _new_user(db)
    node_id = _node_id("re-enrol")
    first = enrolment.start_device_code(db, node_id, "host", "linux")
    machine_id = str(enrolment.approve_device_code(db, first["user_code"], owner))
    old_token = enrolment.redeem_device_code(db, first["device_code"])
    job = f"cjob-{uuid.uuid4().hex[:12]}"
    _credit(db, machine_id, job)

    assert enrolment.revoke_machine(db, machine_id, owner) is True
    assert _delete(client, owner, machine_id).status_code == 200

    second = enrolment.start_device_code(db, node_id, "host-again", "linux")
    again_id = enrolment.approve_device_code(db, second["user_code"], owner)
    new_token = enrolment.redeem_device_code(db, second["device_code"])

    assert str(again_id) == machine_id, "re-enrolment must reuse the row"
    assert enrolment.authenticate_machine(db, new_token) is not None
    assert enrolment.authenticate_machine(db, old_token) is None, (
        "the deleted machine's token must not come back to life"
    )
    row = _row(db, machine_id)
    assert row["status"] == "pending" or row["status"] == "active"
    assert row["name"] == "host-again"
    assert machine_id in _fleet_ids(client, owner)

    body = client.get("/v1alpha1/me/contributions", headers=_auth(owner)).json()
    assert body["accepted_tasks"] == 1


def test_a_deleted_machine_cannot_be_claimed_by_a_different_account(db):
    """Re-enrolment stays owner-scoped. A deleted node_id must not become a
    way for a second account to adopt someone else's machine identity — the
    impersonation the unique constraint exists to prevent."""
    owner, stranger = _new_user(db), _new_user(db)
    node_id = _node_id("re-enrol-other")
    first = enrolment.start_device_code(db, node_id, "host", "linux")
    machine_id = enrolment.approve_device_code(db, first["user_code"], owner)
    assert enrolment.revoke_machine(db, machine_id, owner) is True
    assert dbmod.delete_machine_row(db, machine_id, owner) is True

    second = enrolment.start_device_code(db, node_id, "host", "linux")
    with pytest.raises(enrolment.NodeAlreadyEnrolled):
        enrolment.approve_device_code(db, second["user_code"], stranger)


def test_delete_machine_row_refuses_a_machine_that_is_not_revoked(db):
    """The gate is in the SQL, not only in the route, so it holds for every
    caller. Returns False rather than raising: unknown, not-yours, still-live
    and already-deleted are one answer, `revoke_machine_row`'s convention."""
    owner, stranger = _new_user(db), _new_user(db)
    active = _machine(db, owner)
    assert dbmod.delete_machine_row(db, active, owner) is False
    assert _row(db, active)["status"] == "active"

    revoked = _revoked_machine(db, owner)
    assert dbmod.delete_machine_row(db, revoked, stranger) is False
    assert dbmod.delete_machine_row(db, revoked, owner) is True
    assert dbmod.delete_machine_row(db, revoked, owner) is False


def test_machine_status_admits_deleted_and_still_refuses_anything_else(db):
    """0028 widened `machines_status_check` to accept `'deleted'`, and both
    halves of the widening fail differently. If the migration had not
    applied, every delete would raise and the failure would at least be loud.
    If the constraint had been DROPPED rather than widened — `drop constraint
    if exists` runs first, and a typo in the `add` after it is a legal file —
    nothing would refuse a status at all, and a value no query in this schema
    reads would be writable. That failure is silent, which is why the refusal
    is asserted too. Migration 0023 has the same pair one column over.
    """
    owner = _new_user(db)
    machine = _revoked_machine(db, owner)

    with db.cursor() as cur:
        cur.execute(
            "update public.machines set status = 'deleted' where id = %s"
            " returning status",
            (machine,),
        )
        assert cur.fetchone()["status"] == "deleted"

    with pytest.raises(psycopg.errors.CheckViolation):
        with db.cursor() as cur:
            cur.execute(
                "update public.machines set status = 'archived' where id = %s",
                (machine,),
            )
