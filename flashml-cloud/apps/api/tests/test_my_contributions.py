"""What one account has contributed, across everything it ever worked on.

``public.contributions`` has recorded accepted work per (machine, job, task)
since 0001, and until this route existed a person could not see their own
total anywhere. Credit surfaced only inside one job's contribution panel —
"which machine did the work on THIS job" — so somebody who had donated forty
hours across five jobs had no way to learn it.

Two things this file exists to pin, both of which are easy to get wrong in a
way nothing else would catch:

* **The table.** ``contributions`` is the credit ledger; ``attempts`` is the
  lease → (job, task) mapping the completion proxy needs. They overlap and
  they are not the same set. ``fedavg.on_round`` credits federated rounds
  straight into ``contributions`` and writes **no** attempts row at all —
  which is why production on 2026-08-03 read 26 credits against 16 attempts
  on one machine, the 10-row gap being federated work that predates the
  attempt ledger. Counting ``attempts`` here would have silently dropped
  every one of those ten.
* **No double counting.** Both writers can credit the same accepted task,
  and the unique index from 0003 is what collapses them to one row. That is
  asserted here through the route rather than assumed from the index, because
  a total that drifts upward fails silently and compounds — hard rule 4.

The counter is a **counter, not a currency**: nothing in this system debits
it, so there is no balance and nothing can be spent. Every name in the shape
below says "tasks", never "credits remaining".
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api import contributions as contribmod
from flashml_cloud_api import db as dbmod
from flashml_cloud_api import fedavg as fedavgmod
from flashml_cloud_api.app import create_cloud_app
from flashml_cloud_api.settings import Settings

JWT_SECRET = "test-jwt-secret-long-enough-for-hs256-abcdef"


# ---------------------------------------------------------------------------
# the rule: the shape, the ordering, and the nulls it refuses to fill
# ---------------------------------------------------------------------------


def _m(machine_id: str, tasks: int, hostname: str | None = "laptop",
       last_seen: datetime | None = None) -> dict:
    return {
        "machine_id": machine_id,
        "hostname": hostname,
        "accepted_tasks": tasks,
        "last_seen_at": last_seen,
    }


def test_the_report_carries_every_field_the_panel_renders():
    """The shape is a contract with the console. A field that disappears when
    it has nothing to say arrives as `undefined` and renders as a blank tile
    that looks like a loading state."""
    got = contribmod.report(machines=[], jobs_contributed_to=0)
    assert set(got) == {"accepted_tasks", "jobs_contributed_to", "machines"}


def test_a_machine_entry_carries_every_field_the_panel_renders():
    got = contribmod.report(machines=[_m("m-1", 3)], jobs_contributed_to=1)
    assert set(got["machines"][0]) == {
        "machine_id", "hostname", "accepted_tasks", "last_seen_at",
    }


def test_contributing_nothing_is_zeros_and_an_empty_list():
    """Zero is a real answer, not a 404 and not an error: somebody who has
    just signed up has contributed nothing, and that is the state the panel
    most needs to render honestly."""
    assert contribmod.report(machines=[], jobs_contributed_to=0) == {
        "accepted_tasks": 0,
        "jobs_contributed_to": 0,
        "machines": [],
    }


def test_the_total_is_the_sum_of_the_machines_so_the_tile_and_the_list_agree():
    """Derived, never counted a second time. Two independent counts of the
    same thing eventually disagree, and a headline number that does not add
    up to the list under it reads as a bug in whichever half is right."""
    got = contribmod.report(
        machines=[_m("m-1", 7), _m("m-2", 3)], jobs_contributed_to=2
    )
    assert got["accepted_tasks"] == 10
    assert sum(m["accepted_tasks"] for m in got["machines"]) == 10


def test_machines_are_ordered_most_contributed_first():
    got = contribmod.report(
        machines=[_m("m-a", 1), _m("m-b", 9), _m("m-c", 4)],
        jobs_contributed_to=1,
    )
    assert [m["accepted_tasks"] for m in got["machines"]] == [9, 4, 1]


def test_a_tie_is_broken_deterministically_so_the_list_does_not_reshuffle():
    """Equal counts are the common case on a two-laptop fleet. Left to the
    database's row order the panel would reorder itself between polls, which
    looks like work moving between machines and is not."""
    first = contribmod.report(
        machines=[_m("m-b", 5), _m("m-a", 5)], jobs_contributed_to=1
    )
    second = contribmod.report(
        machines=[_m("m-a", 5), _m("m-b", 5)], jobs_contributed_to=1
    )
    assert first["machines"] == second["machines"]
    assert [m["machine_id"] for m in first["machines"]] == ["m-a", "m-b"]


def test_a_machine_that_never_reported_a_hostname_stays_null():
    """The agent supplies its hostname at enrolment and may not have one.
    "Unknown machine" invented here would be indistinguishable from a real
    host actually called that."""
    got = contribmod.report(machines=[_m("m-1", 1, hostname=None)],
                            jobs_contributed_to=1)
    assert got["machines"][0]["hostname"] is None


def test_a_machine_that_never_checked_in_has_a_null_last_seen_at():
    """`last_seen_at` is null until the first heartbeat. `now()` or the epoch
    would both render as a real moment the machine was never seen at."""
    got = contribmod.report(machines=[_m("m-1", 1, last_seen=None)],
                            jobs_contributed_to=1)
    assert got["machines"][0]["last_seen_at"] is None


def test_last_seen_at_is_rendered_as_utc_iso_8601_with_a_z():
    """Postgres hands back a datetime in the session's timezone. Rendered
    with `str()` — as `_jsonable` does for the older machine routes — that is
    `2026-08-08 00:00:00+00:00`, which `new Date()` refuses in Safari. This
    route is new, so it can serve the unambiguous spelling from the start."""
    seen = datetime(2026, 8, 8, 0, 0, 0, tzinfo=timezone.utc)
    got = contribmod.report(machines=[_m("m-1", 1, last_seen=seen)],
                            jobs_contributed_to=1)
    assert got["machines"][0]["last_seen_at"] == "2026-08-08T00:00:00Z"


def test_a_non_utc_timestamp_is_converted_rather_than_relabelled():
    """The offset the session happens to be in must not be stamped with a Z
    and shipped as if it were UTC — that is an eight-hour error that looks
    exactly like a correct answer."""
    seen = datetime(2026, 8, 8, 8, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    got = contribmod.report(machines=[_m("m-1", 1, last_seen=seen)],
                            jobs_contributed_to=1)
    assert got["machines"][0]["last_seen_at"] == "2026-08-08T00:00:00Z"


def test_a_machine_id_is_rendered_as_a_string():
    """psycopg hands back `uuid.UUID`, which the JSON encoder cannot serialise
    — the whole route would 500 on the first account that ever contributed."""
    machine_id = uuid.uuid4()
    got = contribmod.report(machines=[_m(machine_id, 1)], jobs_contributed_to=1)
    assert got["machines"][0]["machine_id"] == str(machine_id)


# ---------------------------------------------------------------------------
# the measurement, against a real database
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
    """This route is answered entirely from the ledger, and must be: an
    account's whole contribution history is one query, never one coordinator
    call per job it ever helped with."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"the contributions route contacted {request.url}")


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


def _node_id(tag: str) -> str:
    """`machines.node_id` is globally unique and the database is shared by the
    whole session, so every node id has to be unique across tests too."""
    return f"contrib-{tag}-{uuid.uuid4().hex[:10]}"


def _machine(
    db,
    owner: str,
    *,
    hostname: str | None = "laptop",
    status: str = "active",
    last_seen_at: datetime | None = None,
) -> str:
    """An enrolled machine, with the columns this route reads set directly.

    Enrolment's device-code dance writes `machines.name` from the hostname the
    agent reported; going through it would make every test here also a test of
    that flow, and would leave no way to build the "never reported a hostname"
    and "never checked in" rows that matter most.
    """
    with db.cursor() as cur:
        cur.execute(
            "insert into public.machines"
            "       (owner_id, node_id, name, status, last_seen_at)"
            " values (%s, %s, %s, %s, %s) returning id",
            (owner, _node_id("m"), hostname, status, last_seen_at),
        )
        return str(cur.fetchone()["id"])


def _job_id() -> str:
    return f"cjob-{uuid.uuid4().hex[:12]}"


def _credit(db, machine_id: str, job_id: str, task_id: str | None = "task-000") -> None:
    """One accepted contribution, written the way both credit paths write it."""
    with db.cursor() as cur:
        cur.execute(
            "insert into public.contributions (machine_id, job_id, task_id)"
            " values (%s, %s, %s) on conflict do nothing",
            (machine_id, job_id, task_id),
        )


def _get(client, user_id: str) -> dict:
    r = client.get(
        "/v1alpha1/me/contributions",
        headers={"Authorization": f"Bearer {_browser_jwt(user_id)}"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_contributions_need_a_jwt(client):
    assert client.get("/v1alpha1/me/contributions").status_code == 401


def test_an_account_that_has_contributed_nothing_reads_zeros_not_a_404(client, db):
    assert _get(client, _new_user(db)) == {
        "accepted_tasks": 0,
        "jobs_contributed_to": 0,
        "machines": [],
    }


def test_an_enrolled_machine_that_never_worked_is_absent_from_the_list(client, db):
    """The list is machines that HAVE contributed, not machines that exist —
    `GET /v1alpha1/machines` is the fleet view and this is not it. A row of
    zeros here would put a laptop somebody enrolled and never ran under a
    heading that says what it contributed."""
    owner = _new_user(db)
    _machine(db, owner)
    body = _get(client, owner)
    assert body["machines"] == []
    assert body["accepted_tasks"] == 0


def test_one_machines_accepted_work_is_totalled_across_every_job(client, db):
    owner = _new_user(db)
    machine = _machine(db, owner, hostname="ada-laptop")
    for job in (_job_id(), _job_id()):
        _credit(db, machine, job, "task-000")
        _credit(db, machine, job, "task-001")

    body = _get(client, owner)
    assert body["accepted_tasks"] == 4
    assert body["jobs_contributed_to"] == 2
    assert body["machines"] == [
        {
            "machine_id": machine,
            "hostname": "ada-laptop",
            "accepted_tasks": 4,
            "last_seen_at": None,
        }
    ]


def test_the_route_reports_the_ledger_not_the_attempt_table(client, db):
    """**Which table this counts, pinned.**

    A lease claimed and never credited leaves an `attempts` row and no
    contribution. Counting attempts would pay for work the system threw away
    — hard rule 4, attempted is not accepted.
    """
    owner = _new_user(db)
    machine = _machine(db, owner)
    job = _job_id()

    accepted_lease = f"lease-{uuid.uuid4().hex[:12]}"
    dbmod.record_attempt(
        db, lease_id=accepted_lease, machine_id=machine, job_id=job,
        task_id="task-000",
    )
    credit = dbmod.claim_attempt_credit(
        db, lease_id=accepted_lease, machine_id=machine
    )
    assert credit is not None
    _credit(db, machine, job, "task-000")

    # ...and one the machine claimed and never completed.
    dbmod.record_attempt(
        db, lease_id=f"lease-{uuid.uuid4().hex[:12]}", machine_id=machine,
        job_id=job, task_id="task-001",
    )

    assert _get(client, owner)["accepted_tasks"] == 1


def test_a_federated_credit_with_no_attempt_row_still_counts(client, db):
    """The other half of the same choice, and the reason `attempts` is not the
    table. `fedavg.on_round` credits a round's contributors straight into the
    ledger and writes no attempts row at all — this is the shape production
    read as 26 credits against 16 attempts on 2026-08-03, a ten-row gap that
    is real work and would be invisible if this counted leases."""
    owner = _new_user(db)
    machine = _machine(db, owner)
    round_job = _job_id()
    dbmod.record_contributions(
        db,
        job_id=round_job,
        entries=[{"node_id": _node_id("unused"), "task_id": "task-000"}],
    )
    _credit(db, machine, round_job, "task-000")

    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.attempts where machine_id = %s",
            (machine,),
        )
        assert cur.fetchone()["n"] == 0

    assert _get(client, owner)["accepted_tasks"] == 1


def test_a_task_credited_by_both_paths_counts_once(client, db):
    """**The no-double-counting proof, through the route.**

    Both writers can reach the same accepted task: the completion proxy
    credits from the `attempts` mapping, and `fedavg.on_round` credits from
    the coordinator's task view, and for a federated round both compute the
    same (machine_id, job_id, task_id) because both use the ROUND's
    coordinator job id. The unique index from 0003 collapses them to one row.

    `test_contributions.py::test_federated_round_credited_by_both_paths_yields_one_row`
    pins that at the ledger. This pins that the total a person is shown is the
    same one row — a counter that drifts upward is exactly the failure 0003's
    comment describes, and it is silent.
    """
    owner = _new_user(db)
    node_id = _node_id("both")
    machine = dbmod.insert_machine(
        db, owner_id=owner, node_id=node_id, name="both", platform="linux"
    )
    round_job = _job_id()

    # path 1 — the completion proxy, via the attempts mapping
    lease = f"lease-{uuid.uuid4().hex[:12]}"
    dbmod.record_attempt(
        db, lease_id=lease, machine_id=str(machine), job_id=round_job,
        task_id="task-000",
    )
    credit = dbmod.claim_attempt_credit(db, lease_id=lease, machine_id=str(machine))
    assert credit is not None
    dbmod.record_contributions(
        db,
        job_id=credit["job_id"],
        entries=[{"node_id": node_id, "task_id": credit["task_id"],
                  "duration_s": credit["duration_s"]}],
    )

    # path 2 — fedavg's on_round credit for the same accepted task
    dbmod.record_contributions(
        db,
        job_id=round_job,
        entries=[{"node_id": node_id, "task_id": "task-000", "duration_s": 1.0}],
    )

    body = _get(client, owner)
    assert body["accepted_tasks"] == 1
    assert body["jobs_contributed_to"] == 1
    assert body["machines"][0]["accepted_tasks"] == 1


def test_replaying_a_round_callback_does_not_inflate_the_total(client, db):
    """The retry the 0003 index was written for, seen from the panel."""
    owner = _new_user(db)
    node_id = _node_id("replay")
    dbmod.insert_machine(
        db, owner_id=owner, node_id=node_id, name="replay", platform="linux"
    )
    entries = [{"node_id": node_id, "task_id": "task-000", "duration_s": 2.0}]
    job = _job_id()
    for _ in range(3):
        dbmod.record_contributions(db, job_id=job, entries=entries)

    assert _get(client, owner)["accepted_tasks"] == 1


def test_another_accounts_machines_are_never_counted(client, db):
    """Owner-scoped from the verified `sub`, like every other `/me` route."""
    mine, theirs = _new_user(db), _new_user(db)
    _credit(db, _machine(db, theirs), _job_id())

    assert _get(client, mine) == {
        "accepted_tasks": 0,
        "jobs_contributed_to": 0,
        "machines": [],
    }


def test_work_on_somebody_elses_job_is_still_my_contribution(client, db):
    """The whole barter premise: a person's machines mostly run OTHER people's
    jobs. Scoping this on `jobs.owner_id` as the metrics page does would
    report zero for every volunteer in the product."""
    contributor, submitter = _new_user(db), _new_user(db)
    job_id = f"j-{uuid.uuid4().hex[:12]}"
    dbmod.insert_job(
        db, job_id=job_id, owner_id=submitter, name="theirs", source=None,
        spec=None, status="SUCCEEDED",
    )
    _credit(db, _machine(db, contributor), job_id)

    body = _get(client, contributor)
    assert body["accepted_tasks"] == 1
    assert body["jobs_contributed_to"] == 1


def test_machines_are_listed_most_contributed_first(client, db):
    owner = _new_user(db)
    quiet = _machine(db, owner, hostname="quiet")
    busy = _machine(db, owner, hostname="busy")
    job = _job_id()
    _credit(db, quiet, job, "task-000")
    for i in range(4):
        _credit(db, busy, job, f"task-{i:03d}")

    body = _get(client, owner)
    assert [m["hostname"] for m in body["machines"]] == ["busy", "quiet"]
    assert [m["accepted_tasks"] for m in body["machines"]] == [4, 1]
    assert body["accepted_tasks"] == 5


def test_a_job_two_machines_worked_on_is_one_job(client, db):
    owner = _new_user(db)
    job = _job_id()
    _credit(db, _machine(db, owner), job, "task-000")
    _credit(db, _machine(db, owner), job, "task-001")

    body = _get(client, owner)
    assert body["accepted_tasks"] == 2
    assert body["jobs_contributed_to"] == 1


def test_a_federated_runs_rounds_are_one_job_not_one_per_round(client, db):
    """A federated run is one coordinator job PER ROUND, and
    `contributions.job_id` carries the ROUND's id — not a row in `public.jobs`
    at all. Counted raw, a five-round run would tell a volunteer they helped
    with five jobs when they helped with one. `job_rounds` is the mapping
    back, the same join `metrics_counts_for_owner` calls not optional.
    """
    owner, submitter = _new_user(db), _new_user(db)
    machine = _machine(db, owner)
    parent = fedavgmod.new_federated_job_id()
    dbmod.insert_job(
        db, job_id=parent, owner_id=submitter, name="fed",
        source={"mode": "federated", "rounds": 3}, spec=None, status="SUCCEEDED",
    )
    for index in range(3):
        coordinator_job_id = _job_id()
        dbmod.insert_job_round(
            db, job_id=parent, round_index=index, participants=1, mean_loss=0.5,
            contributors=[], coordinator_job_id=coordinator_job_id,
        )
        _credit(db, machine, coordinator_job_id, "task-000")

    body = _get(client, owner)
    assert body["accepted_tasks"] == 3
    assert body["jobs_contributed_to"] == 1


def test_a_revoked_machines_past_contributions_still_count(client, db):
    """**The decision about a machine that is no longer active, stated.**

    Revoking a machine invalidates its token — it stops the machine claiming
    new leases. It says nothing about work already done and already accepted.
    Dropping its history would make a person's total FALL when they retire a
    laptop or rotate a token they think was compromised, which for a
    counter that nothing ever debits is indistinguishable from a bug, and
    would penalise exactly the hygiene we want.
    """
    owner = _new_user(db)
    machine = _machine(db, owner, hostname="retired", status="revoked")
    with db.cursor() as cur:
        cur.execute(
            "update public.machines set revoked_at = now() where id = %s", (machine,)
        )
    _credit(db, machine, _job_id())

    body = _get(client, owner)
    assert body["accepted_tasks"] == 1
    assert body["machines"][0]["hostname"] == "retired"


def test_history_would_follow_a_machine_handed_to_another_account(client, db):
    """**The other half of that decision, and the hazard in it.**

    Unreachable today: `machines.owner_id` is written once, at device-code
    approval, and nothing in this codebase ever reassigns it — so "a machine
    the person no longer owns" has exactly one shape, the revoked one above.
    Pinned anyway, because this query answers "whose machine is it NOW" and
    the day somebody adds a transfer route that silently moves the previous
    owner's forty hours onto the new owner's panel. Whoever writes that route
    should find this test telling them credit needs an owner recorded at
    credit time, not looked up at read time.
    """
    first, second = _new_user(db), _new_user(db)
    machine = _machine(db, first, hostname="handed-over")
    _credit(db, machine, _job_id())
    assert _get(client, first)["accepted_tasks"] == 1

    with db.cursor() as cur:
        cur.execute(
            "update public.machines set owner_id = %s where id = %s",
            (second, machine),
        )

    assert _get(client, first)["accepted_tasks"] == 0
    assert _get(client, second)["accepted_tasks"] == 1


def test_a_machine_that_never_reported_a_hostname_reads_null_through_the_route(
    client, db
):
    owner = _new_user(db)
    _credit(db, _machine(db, owner, hostname=None), _job_id())
    assert _get(client, owner)["machines"][0]["hostname"] is None


def test_last_seen_at_is_served_as_utc_iso_8601(client, db):
    owner = _new_user(db)
    seen = datetime(2026, 8, 8, 12, 30, 0, tzinfo=timezone.utc)
    _credit(db, _machine(db, owner, last_seen_at=seen), _job_id())
    assert _get(client, owner)["machines"][0]["last_seen_at"] == "2026-08-08T12:30:00Z"
