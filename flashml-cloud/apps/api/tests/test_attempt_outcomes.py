"""An attempt's terminal outcome: who writes it, once, and who may not.

Migration 0015 gave ``public.attempts`` an outcome, and the value of that
column is entirely a function of the discipline around writing it. A ledger
that resolves an attempt twice reports a longer failure than happened; one
that resolves somebody else's attempt reports a failure that did not happen;
one that guesses at the rows it cannot classify reports failures that were
never observed at all. Each of those produces a *plausible* reliability
number, which is the only kind of wrong number that survives review.

So the tests here are about the writes, and `test_metrics.py` is about what
the numbers mean. The split matches the modules: `db` supplies facts,
`metrics` supplies the rule.

The three writers:

* ``claim_attempt_credit``  — accepted, in the statement that already took
  the credit. One event, one write.
* ``record_attempt_failure`` — failed, after the coordinator has ACCEPTED
  the report and never before.
* ``reconcile_expired_attempts`` — expired, for a lease whose
  coordinator-issued deadline passed with nothing reported. The only
  inferred outcome in the table, and the one every test below treats as
  weaker evidence than the other two.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import enrolment, migrate
from flashml_cloud_api.app import create_cloud_app
from flashml_cloud_api.settings import Settings

# The scratch-database helper from the migration suite: these backfill tests
# need a database the runner has NOT already carried all the way to 0015.
from test_migrate import AUTH_STUB, connected, scratch_database  # noqa: F401

RUN_MARKER = uuid.uuid4().hex[:12]

JWT_SECRET = "test-jwt-secret-long-enough-for-hs256-abcdef"
OPERATOR_TOKEN = "op-secret-do-not-leak-3f9c1b"
COORDINATOR_URL = "http://coordinator.internal:8100"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


class FakeCoordinator(httpx.AsyncBaseTransport):
    """A stand-in coordinator whose status and body each test sets. The
    status is what decides whether this API is allowed to write anything
    down, so it has to be controllable per call rather than assumed 200."""

    def __init__(self) -> None:
        self.status_code = 200
        self.payload: dict | None = {"ok": True}
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        return httpx.Response(self.status_code, json=self.payload)


@pytest.fixture(scope="module")
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        with conn.cursor() as cur:
            cur.execute(
                "delete from public.machines where node_id like %s",
                (f"outcome-{RUN_MARKER}-%",),
            )
        conn.close()


@pytest.fixture(scope="module")
def settings(postgres_dsn) -> Settings:
    return Settings(
        supabase_url="https://yualksqjjvlfscbbsygq.supabase.co",
        supabase_jwt_secret=JWT_SECRET,
        supabase_service_key="service-key-not-used-here",
        coordinator_url=COORDINATOR_URL,
        coordinator_operator_token=OPERATOR_TOKEN,
        require_auth=True,
        database_url=postgres_dsn,
        console_url="https://console.example",
    )


@pytest.fixture
def coordinator() -> FakeCoordinator:
    return FakeCoordinator()


@pytest.fixture
def client(settings, postgres_dsn, coordinator):
    def connect() -> psycopg.Connection:
        conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        return conn

    app = create_cloud_app(settings, connect=connect, transport=coordinator)
    with TestClient(app) as c:
        yield c


def _new_user(db) -> str:
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (%s)", (user_id,))
        cur.execute(
            "insert into public.profiles (id, admitted_at) values (%s, now())",
            (user_id,),
        )
    return user_id


def _enrol(db, owner_id: str, node_id: str) -> tuple[str, str]:
    started = enrolment.start_device_code(db, node_id, "host-" + node_id, "linux")
    machine_id = enrolment.approve_device_code(db, started["user_code"], owner_id)
    token = enrolment.redeem_device_code(db, started["device_code"])
    assert token is not None
    return str(machine_id), token


@pytest.fixture(scope="module")
def machine(db):
    owner = _new_user(db)
    node_id = f"outcome-{RUN_MARKER}-a"
    machine_id, token = _enrol(db, owner, node_id)
    return {"owner": owner, "id": machine_id, "token": token, "node_id": node_id}


@pytest.fixture(scope="module")
def other_machine(db):
    owner = _new_user(db)
    node_id = f"outcome-{RUN_MARKER}-b"
    machine_id, token = _enrol(db, owner, node_id)
    return {"owner": owner, "id": machine_id, "token": token, "node_id": node_id}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _lease(lease_id: str, job_id: str, task_id: str, deadline: str) -> dict:
    return {
        "schema_version": "v1alpha1",
        "lease_id": lease_id,
        "task_id": task_id,
        "job_id": job_id,
        "node_id": "whatever-the-agent-says",
        "attempt_number": 1,
        "deadline": deadline,
        "payload": {},
    }


def _iso(delta_seconds: int) -> str:
    moment = datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)
    return moment.isoformat().replace("+00:00", "Z")


def _row(db, lease_id: str) -> dict:
    with db.cursor() as cur:
        cur.execute(
            "select * from public.attempts where lease_id = %s", (lease_id,)
        )
        row = cur.fetchone()
    assert row is not None, f"no attempt row for {lease_id}"
    return row


def _claim(db, *, machine_id: str, deadline=None, lease_id=None,
           job_id=None, task_id="task-000") -> str:
    lease_id = lease_id or f"lease-{uuid.uuid4().hex[:12]}"
    dbmod.record_attempt(
        db,
        lease_id=lease_id,
        machine_id=machine_id,
        job_id=job_id or f"cjob-{uuid.uuid4().hex[:10]}",
        task_id=task_id,
        deadline=deadline,
    )
    return lease_id


# ---------------------------------------------------------------------------
# the backfill: one direction, and never a guess
# ---------------------------------------------------------------------------


def _apply_through(conn, version: str) -> None:
    """Run the real migrations, in order, up to and including `version`."""
    for migration in migrate.discover():
        conn.execute(migration.sql)
        if migration.version == version:
            return
    raise AssertionError(f"no migration named {version}")


def _apply_only(conn, version: str) -> None:
    for migration in migrate.discover():
        if migration.version == version:
            conn.execute(migration.sql)
            return
    raise AssertionError(f"no migration named {version}")


@pytest.fixture
def pre_0015(postgres_dsn):
    """A database carried to 0014 and no further, with an owner, a machine
    and a job already in it — the state production was in the moment before
    this migration ran."""
    with scratch_database(postgres_dsn) as dsn, connected(dsn) as conn:
        conn.execute(AUTH_STUB)
        _apply_through(conn, "0014_sandbox_sessions")
        owner = str(uuid.uuid4())
        conn.execute("insert into auth.users (id) values (%s)", (owner,))
        conn.execute("insert into public.profiles (id) values (%s)", (owner,))
        conn.execute(
            "insert into public.machines (id, owner_id, node_id, status) "
            "values (%s, %s, 'legacy-node', 'active')",
            (str(uuid.uuid4()), owner),
        )
        yield conn


def _legacy_attempt(conn, lease_id: str, *, accepted: bool) -> None:
    machine_id = conn.execute(
        "select id from public.machines limit 1"
    ).fetchone()[0]
    conn.execute(
        "insert into public.attempts"
        "  (lease_id, machine_id, job_id, task_id, accepted_at)"
        "  values (%s, %s, 'legacy-job', %s, %s)",
        (
            lease_id,
            machine_id,
            f"task-{lease_id}",
            datetime.now(timezone.utc) if accepted else None,
        ),
    )


def test_the_backfill_resolves_exactly_the_attempts_the_ledger_knew(pre_0015):
    """An accepted row is not ambiguous: `accepted_at` has one writer and one
    meaning. It becomes `outcome='accepted'` with `resolved_at` set to the
    instant already recorded — not to the moment the migration ran, which
    would date every historical acceptance to a deploy."""
    _legacy_attempt(pre_0015, "old-accepted", accepted=True)
    _apply_only(pre_0015, "0015_attempt_outcomes")

    row = pre_0015.execute(
        "select outcome, resolved_at, accepted_at from public.attempts"
        " where lease_id = 'old-accepted'"
    ).fetchone()
    assert row[0] == "accepted"
    assert row[1] == row[2]


def test_the_backfill_refuses_to_bucket_an_attempt_nobody_recorded(pre_0015):
    """A row without `accepted_at` may have failed, expired, or still be
    running, and nothing written down can tell the three apart. It is left
    null — which every denominator then excludes — because putting it in a
    bucket would manufacture exactly the number this migration exists to stop
    being manufactured."""
    _legacy_attempt(pre_0015, "old-unknown", accepted=False)
    _apply_only(pre_0015, "0015_attempt_outcomes")

    row = pre_0015.execute(
        "select outcome, resolved_at, lease_deadline from public.attempts"
        " where lease_id = 'old-unknown'"
    ).fetchone()
    assert row == (None, None, None)


def test_the_backfill_runs_once_and_is_safe_to_re_run(pre_0015):
    """The runner is idempotent by convention; the SQL has to be idempotent
    in fact, because a re-applied migration must not re-stamp rows that have
    since been resolved differently."""
    _legacy_attempt(pre_0015, "old-accepted-twice", accepted=True)
    _apply_only(pre_0015, "0015_attempt_outcomes")
    pre_0015.execute(
        "update public.attempts set outcome = 'failed'"
        " where lease_id = 'old-accepted-twice'"
    )
    _apply_only(pre_0015, "0015_attempt_outcomes")

    row = pre_0015.execute(
        "select outcome from public.attempts where lease_id = 'old-accepted-twice'"
    ).fetchone()
    assert row[0] == "failed", "the backfill overwrote a later decision"


# ---------------------------------------------------------------------------
# the accepted write
# ---------------------------------------------------------------------------


def test_crediting_an_attempt_resolves_it_in_the_same_statement(db, machine):
    lease = _claim(db, machine_id=machine["id"])
    assert dbmod.claim_attempt_credit(
        db, lease_id=lease, machine_id=machine["id"]
    ) is not None

    row = _row(db, lease)
    assert row["outcome"] == "accepted"
    assert row["resolved_at"] is not None
    assert row["accepted_at"] == row["resolved_at"]


def test_a_second_completion_neither_credits_nor_re_resolves(db, machine):
    """The idempotency `claim_attempt_credit` already had, now covering the
    outcome too: a repeated commit must not move `resolved_at` forward and
    silently lengthen the attempt."""
    lease = _claim(db, machine_id=machine["id"])
    dbmod.claim_attempt_credit(db, lease_id=lease, machine_id=machine["id"])
    first = _row(db, lease)["resolved_at"]

    assert dbmod.claim_attempt_credit(
        db, lease_id=lease, machine_id=machine["id"]
    ) is None
    assert _row(db, lease)["resolved_at"] == first


# ---------------------------------------------------------------------------
# the failed write
# ---------------------------------------------------------------------------


def test_a_reported_failure_is_recorded_once(db, machine):
    lease = _claim(db, machine_id=machine["id"])
    assert dbmod.record_attempt_failure(
        db, lease_id=lease, machine_id=machine["id"]
    ) is True

    row = _row(db, lease)
    assert row["outcome"] == "failed"
    assert row["resolved_at"] is not None
    # A failure is not a credit. Nothing here may look like accepted work.
    assert row["accepted_at"] is None


def test_a_retried_failure_does_not_stretch_the_attempt(db, machine):
    """An agent retrying its own error report describes ONE failure. Writing
    it twice would move `resolved_at` forward and report a longer piece of
    wasted work than actually happened — a number that is plausible, wrong,
    and grows with every retry."""
    lease = _claim(db, machine_id=machine["id"])
    dbmod.record_attempt_failure(db, lease_id=lease, machine_id=machine["id"])
    first = _row(db, lease)["resolved_at"]

    assert dbmod.record_attempt_failure(
        db, lease_id=lease, machine_id=machine["id"]
    ) is False
    assert _row(db, lease)["resolved_at"] == first


def test_a_failure_never_overwrites_an_accepted_attempt(db, machine):
    lease = _claim(db, machine_id=machine["id"])
    dbmod.claim_attempt_credit(db, lease_id=lease, machine_id=machine["id"])

    assert dbmod.record_attempt_failure(
        db, lease_id=lease, machine_id=machine["id"]
    ) is False
    assert _row(db, lease)["outcome"] == "accepted"


def test_one_machine_cannot_resolve_anothers_attempt(db, machine, other_machine):
    """The same scoping every write on this table has. A machine reporting
    somebody else's lease failed would mark work that is still running as
    thrown away, and the machine actually doing it would then be unable to
    claim its own credit cleanly."""
    lease = _claim(db, machine_id=machine["id"])

    assert dbmod.record_attempt_failure(
        db, lease_id=lease, machine_id=other_machine["id"]
    ) is False
    assert _row(db, lease)["outcome"] is None


# ---------------------------------------------------------------------------
# the inferred write: expiry
# ---------------------------------------------------------------------------


def test_an_attempt_past_its_deadline_is_resolved_as_expired(db, machine):
    """The event the coordinator never sends. Its sweeper expires the lease,
    emits into its own ledger, and calls nobody — so a machine unplugged
    mid-task left an attempt row open for ever, and the one failure mode this
    product exists to survive was the one the page could not see."""
    lease = _claim(db, machine_id=machine["id"], deadline=_iso(-3600))

    assert dbmod.reconcile_expired_attempts(db) >= 1
    row = _row(db, lease)
    assert row["outcome"] == "expired"
    # Resolved AT THE DEADLINE, not at the moment the reconciler ran: the
    # first is when the work stopped counting, the second is when somebody
    # opened a page. Stamping now() would make lost_task_seconds grow on
    # every load, which is the number metrics.py refused to ship.
    assert row["resolved_at"] == row["lease_deadline"]


def test_a_lease_inside_its_grace_period_is_left_alone(db, machine):
    """The deadline alone would do if this API saw every heartbeat land. The
    grace covers the gap between the coordinator granting a renewal and this
    API recording it — a best-effort write, like every accounting write on
    the agent path."""
    lease = _claim(db, machine_id=machine["id"], deadline=_iso(-60))

    dbmod.reconcile_expired_attempts(db)
    assert _row(db, lease)["outcome"] is None


def test_a_live_lease_is_never_reconciled(db, machine):
    lease = _claim(db, machine_id=machine["id"], deadline=_iso(3600))

    dbmod.reconcile_expired_attempts(db)
    assert _row(db, lease)["outcome"] is None


def test_an_attempt_with_no_deadline_stays_unresolved_for_ever(db, machine):
    """Claimed before 0015, or from a claim response that could not be
    parsed. "We do not know how this ended" is not "it failed", and the row
    stays out of every denominator rather than being assigned to one."""
    lease = _claim(db, machine_id=machine["id"], deadline=None)

    dbmod.reconcile_expired_attempts(db)
    row = _row(db, lease)
    assert row["lease_deadline"] is None
    assert row["outcome"] is None


def test_reconciliation_never_touches_a_resolved_attempt(db, machine):
    """Including one whose deadline has long passed: an attempt that was
    credited is finished, and a later reconciler must not relabel it as
    thrown-away work."""
    lease = _claim(db, machine_id=machine["id"], deadline=_iso(-3600))
    dbmod.claim_attempt_credit(db, lease_id=lease, machine_id=machine["id"])

    dbmod.reconcile_expired_attempts(db)
    assert _row(db, lease)["outcome"] == "accepted"


def test_an_observed_outcome_may_correct_an_inferred_one(db, machine):
    """Precedence, in the only direction it goes. If the coordinator accepts
    a commit for an attempt this API had inferred to be expired, the
    inference was wrong and the credit is written anyway — a bad guess must
    never cost a volunteer their work."""
    lease = _claim(db, machine_id=machine["id"], deadline=_iso(-3600))
    dbmod.reconcile_expired_attempts(db)
    assert _row(db, lease)["outcome"] == "expired"

    assert dbmod.claim_attempt_credit(
        db, lease_id=lease, machine_id=machine["id"]
    ) is not None
    assert _row(db, lease)["outcome"] == "accepted"


def test_a_reported_failure_also_corrects_an_inferred_expiry(db, machine):
    lease = _claim(db, machine_id=machine["id"], deadline=_iso(-3600))
    dbmod.reconcile_expired_attempts(db)

    assert dbmod.record_attempt_failure(
        db, lease_id=lease, machine_id=machine["id"]
    ) is True
    assert _row(db, lease)["outcome"] == "failed"


# ---------------------------------------------------------------------------
# the deadline itself
# ---------------------------------------------------------------------------


def test_a_deadline_is_parsed_from_every_shape_the_wire_produces():
    assert dbmod._lease_deadline("2026-08-11T10:00:00Z") == datetime(
        2026, 8, 11, 10, 0, tzinfo=timezone.utc
    )
    assert dbmod._lease_deadline("2026-08-11T10:00:00+00:00") == datetime(
        2026, 8, 11, 10, 0, tzinfo=timezone.utc
    )
    moment = datetime.now(timezone.utc)
    assert dbmod._lease_deadline(moment) == moment


def test_an_unusable_deadline_costs_the_column_and_never_the_row(db, machine):
    """Parsed in Python rather than handed to Postgres, because the write is
    best-effort and the value is not: a malformed timestamp reaching a
    `timestamptz` parameter would abort the INSERT and cost the whole
    lease -> (job, task) mapping the credit path cannot work without."""
    for junk in ("", "  ", "not-a-timestamp", 17, None, {"deadline": "x"}):
        assert dbmod._lease_deadline(junk) is None

    lease = _claim(db, machine_id=machine["id"], deadline="not-a-timestamp")
    row = _row(db, lease)
    assert row["lease_deadline"] is None
    assert row["job_id"], "the mapping the credit path needs was lost"


def test_a_renewed_heartbeat_carries_the_deadline_forward(db, machine):
    lease = _claim(db, machine_id=machine["id"], deadline=_iso(-3600))
    dbmod.note_attempt_deadline(
        db, lease_id=lease, machine_id=machine["id"], deadline=_iso(3600)
    )

    dbmod.reconcile_expired_attempts(db)
    assert _row(db, lease)["outcome"] is None


def test_a_machine_cannot_extend_somebody_elses_lease(db, machine, other_machine):
    lease = _claim(db, machine_id=machine["id"], deadline=_iso(-3600))
    dbmod.note_attempt_deadline(
        db, lease_id=lease, machine_id=other_machine["id"], deadline=_iso(3600)
    )

    assert dbmod.reconcile_expired_attempts(db) >= 1
    assert _row(db, lease)["outcome"] == "expired"


def test_a_resolved_attempts_deadline_is_frozen(db, machine):
    """Moving it afterwards could only ever un-resolve a decision already
    made."""
    lease = _claim(db, machine_id=machine["id"], deadline=_iso(-3600))
    dbmod.record_attempt_failure(db, lease_id=lease, machine_id=machine["id"])
    before = _row(db, lease)["lease_deadline"]

    dbmod.note_attempt_deadline(
        db, lease_id=lease, machine_id=machine["id"], deadline=_iso(3600)
    )
    assert _row(db, lease)["lease_deadline"] == before


# ---------------------------------------------------------------------------
# through the proxy, which is where all of this actually happens
# ---------------------------------------------------------------------------


def _headers(machine) -> dict:
    return {"Authorization": f"Bearer {machine['token']}"}


def test_a_claim_records_the_coordinators_deadline(client, coordinator, db, machine):
    lease_id = f"lease-{uuid.uuid4().hex[:12]}"
    coordinator.status_code = 200
    coordinator.payload = _lease(
        lease_id, f"cjob-{uuid.uuid4().hex[:8]}", "task-000", _iso(600)
    )

    assert client.post(
        "/v1alpha1/leases/claim", json={}, headers=_headers(machine)
    ).status_code == 200
    assert _row(db, lease_id)["lease_deadline"] is not None


def test_a_lease_body_with_no_deadline_still_records_the_attempt(
    client, coordinator, db, machine
):
    lease_id = f"lease-{uuid.uuid4().hex[:12]}"
    payload = _lease(lease_id, f"cjob-{uuid.uuid4().hex[:8]}", "task-000", _iso(600))
    del payload["deadline"]
    coordinator.status_code = 200
    coordinator.payload = payload

    assert client.post(
        "/v1alpha1/leases/claim", json={}, headers=_headers(machine)
    ).status_code == 200
    assert _row(db, lease_id)["lease_deadline"] is None


def test_an_accepted_heartbeat_pushes_the_deadline_out(
    client, coordinator, db, machine
):
    lease = _claim(db, machine_id=machine["id"], deadline=_iso(-3600))
    coordinator.status_code = 200
    coordinator.payload = _lease(lease, "cjob-x", "task-000", _iso(3600))

    assert client.post(
        f"/v1alpha1/attempts/{lease}/heartbeat", json={}, headers=_headers(machine)
    ).status_code == 200

    dbmod.reconcile_expired_attempts(db)
    assert _row(db, lease)["outcome"] is None, "a live lease was called expired"


def test_a_refused_heartbeat_never_pushes_the_deadline_out(
    client, coordinator, db, machine
):
    """410 Gone is the coordinator saying the lease is already dead. It is
    the one answer that must not extend anything — an agent whose heartbeat
    was refused has stopped working, and treating the refusal as a renewal
    would keep its abandoned attempt looking alive for ever."""
    lease = _claim(db, machine_id=machine["id"], deadline=_iso(-3600))
    coordinator.status_code = 410
    coordinator.payload = {"detail": "lease expired"}

    client.post(
        f"/v1alpha1/attempts/{lease}/heartbeat", json={}, headers=_headers(machine)
    )

    assert dbmod.reconcile_expired_attempts(db) >= 1
    assert _row(db, lease)["outcome"] == "expired"


def test_the_fail_hop_is_no_longer_a_pure_proxy(client, coordinator, db, machine):
    """The whole point of the change, at the hop it happens on. This route
    forwarded and wrote nothing, so the one moment this API was TOLD an
    attempt had failed left no mark, and a failed attempt was
    indistinguishable from one still running."""
    lease = _claim(db, machine_id=machine["id"])
    coordinator.status_code = 200
    coordinator.payload = {"status": "failed"}

    assert client.post(
        f"/v1alpha1/attempts/{lease}/fail",
        json={"reason": "boom"},
        headers=_headers(machine),
    ).status_code == 200
    assert _row(db, lease)["outcome"] == "failed"


def test_a_failure_the_coordinator_refused_is_not_recorded(
    client, coordinator, db, machine
):
    """A 404 means the coordinator has no such live lease. The report
    describes nothing that happened, and recording it would resolve an
    attempt on the strength of a rejected request — for a lease still running
    somewhere, it would resolve one that had not finished."""
    lease = _claim(db, machine_id=machine["id"])
    coordinator.status_code = 404
    coordinator.payload = {"detail": "unknown lease"}

    client.post(
        f"/v1alpha1/attempts/{lease}/fail",
        json={"reason": "boom"},
        headers=_headers(machine),
    )
    assert _row(db, lease)["outcome"] is None


def test_a_failure_write_that_breaks_never_fails_the_agents_report(
    client, coordinator, db, machine, monkeypatch
):
    """Best-effort, exactly like the credit write next door. An agent's error
    path must keep working when the ledger does not — the alternative is a
    machine that cannot report a failure because we could not write it down,
    which turns one lost task into a stalled one, and it would do so at
    precisely the moment the system is already having a bad day."""
    lease = _claim(db, machine_id=machine["id"])
    coordinator.status_code = 200
    coordinator.payload = {"status": "failed"}

    def explode(*args, **kwargs):
        raise RuntimeError("ledger is down")

    monkeypatch.setattr(dbmod, "record_attempt_failure", explode)

    assert client.post(
        f"/v1alpha1/attempts/{lease}/fail",
        json={"reason": "boom"},
        headers=_headers(machine),
    ).status_code == 200
    assert _row(db, lease)["outcome"] is None


def test_a_deadline_write_that_breaks_never_fails_a_heartbeat(
    client, coordinator, db, machine, monkeypatch
):
    """Same rule on the hop that runs most often. A heartbeat is how a
    working task stays alive; an accounting column must never be the reason
    one is told to stop."""
    lease = _claim(db, machine_id=machine["id"], deadline=_iso(600))
    coordinator.status_code = 200
    coordinator.payload = _lease(lease, "cjob-x", "task-000", _iso(3600))

    def explode(*args, **kwargs):
        raise RuntimeError("ledger is down")

    monkeypatch.setattr(dbmod, "note_attempt_deadline", explode)

    assert client.post(
        f"/v1alpha1/attempts/{lease}/heartbeat", json={}, headers=_headers(machine)
    ).status_code == 200
