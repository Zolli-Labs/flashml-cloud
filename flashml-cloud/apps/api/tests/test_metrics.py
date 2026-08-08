"""The Stage 8 metrics page, API half — and the nulls that keep it honest.

This page exists to prove the product's central claim: that work survives
machines dying. That makes it the one surface where a made-up number does
the most damage, because a confidently wrong MTTR is indistinguishable from
a measured one and nobody downstream can tell.

So the rule this file pins is not "compute the metrics" — it is **compute
only what this deployment's events actually support, and return null for
the rest**. Three of the ten fields are null today and each has a named
missing event; the tests below say which, so that the day somebody records
that event they find a failing test telling them the field can now be real.

``null`` and ``0`` are different answers throughout: 0 attempted tasks and
"we cannot tell how many were attempted" are not the same fact, and a page
that renders them the same way is lying in one of the two cases.
"""
from __future__ import annotations

import time
import uuid

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import fedavg as fedavgmod
from flashml_cloud_api import metrics as metricsmod
from flashml_cloud_api.app import create_cloud_app
from flashml_cloud_api.settings import Settings

JWT_SECRET = "test-jwt-secret-long-enough-for-hs256-abcdef"


# ---------------------------------------------------------------------------
# the rule: what the report says, and what it refuses to say
# ---------------------------------------------------------------------------


def test_goodput_is_accepted_over_attempted():
    assert metricsmod.goodput_ratio(accepted=3, attempted=4) == 0.75


def test_goodput_with_nothing_attempted_is_null_not_zero():
    """0.0 means "everything attempted was wasted", which is the worst
    number this page can show. "Nothing has been attempted yet" is not that,
    and it must never be a divide by zero either."""
    assert metricsmod.goodput_ratio(accepted=0, attempted=0) is None


def test_goodput_of_a_run_that_lost_everything_is_zero_not_null():
    """The other half of the same distinction: this one really is 0.0."""
    assert metricsmod.goodput_ratio(accepted=0, attempted=5) == 0.0


def test_the_report_carries_every_field_the_page_renders():
    """The shape is a contract with the console — a field that silently
    disappears renders as `undefined`, not as an error."""
    got = metricsmod.report(window_days=30, counts={})
    assert set(got) == {
        "window_days",
        "jobs_total", "jobs_succeeded", "jobs_partial", "jobs_failed",
        "tasks_attempted", "tasks_accepted",
        "goodput_ratio",
        "lost_task_seconds",
        "mttr_seconds",
        "mttd_seconds",
        "machines_contributing",
    }


def test_lost_task_seconds_is_null_because_nothing_records_when_work_stopped():
    """`attempts` records when a lease was CLAIMED and when it was ACCEPTED.
    An attempt that failed or expired has neither an end time nor any row of
    its own: `POST /attempts/{id}/fail` writes nothing, and lease expiry
    happens inside the coordinator's sweeper, which never tells this API.

    So the seconds burned on work that was thrown away cannot be computed
    here. `now() - claimed_at` would look like an answer and would report a
    three-week-old abandoned attempt as three weeks of lost compute."""
    assert metricsmod.report(window_days=30, counts={})["lost_task_seconds"] is None


def test_mttd_is_null_because_nothing_records_when_a_failure_was_noticed():
    """Mean time to DETECTION needs two timestamps: when a machine actually
    stopped, and when the system noticed. Neither is written down.
    `machines.last_seen_at` is a single mutable column with no history, and
    the coordinator's LEASE_EXPIRED / NODE_HEARTBEAT_LOST events live in its
    own ledger, reachable only per-job over HTTP."""
    assert metricsmod.report(window_days=30, counts={})["mttd_seconds"] is None


def test_mttr_is_null_because_nothing_records_when_work_resumed():
    """Mean time to RECOVERY needs the interval from noticing a failure to
    the replacement attempt being accepted. The second half exists
    (`attempts.accepted_at`); the first does not, and half an interval is
    not a duration."""
    assert metricsmod.report(window_days=30, counts={})["mttr_seconds"] is None


def test_a_missing_count_reports_zero_only_where_zero_is_true():
    """Counts come from tables that always answer — a query for an account
    with no jobs returns 0, and 0 is the truth. That is why these are 0 and
    the three above are null."""
    got = metricsmod.report(window_days=30, counts={})
    assert got["jobs_total"] == 0
    assert got["tasks_attempted"] == 0
    assert got["machines_contributing"] == 0


def test_the_window_is_echoed_so_the_page_can_label_its_own_numbers():
    assert metricsmod.report(window_days=7, counts={})["window_days"] == 7


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
    """The metrics route is answered entirely from the ledger. This
    transport exists to prove it: any coordinator call at all fails the
    request loudly rather than quietly costing N round trips per page."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"the metrics route contacted {request.url}")


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


def _jwt(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": time.time() + 3600},
        JWT_SECRET,
        algorithm="HS256",
    )


def _job(db, owner: str, status: str = "RUNNING", days_ago: int = 0) -> str:
    job_id = f"m-{uuid.uuid4().hex[:12]}"
    dbmod.insert_job(
        db, job_id=job_id, owner_id=owner, name=job_id, source=None,
        spec=None, status=status,
    )
    if days_ago:
        with db.cursor() as cur:
            cur.execute(
                "update public.jobs set created_at = now() - make_interval(days => %s) "
                " where id = %s",
                (days_ago, job_id),
            )
    return job_id


def _machine(db, owner: str) -> str:
    with db.cursor() as cur:
        cur.execute(
            "insert into public.machines (owner_id, node_id, status) "
            "values (%s, %s, 'active') returning id",
            (owner, f"node-{uuid.uuid4().hex[:10]}"),
        )
        return str(cur.fetchone()["id"])


def _attempt(db, *, job_id: str, machine_id: str, accepted: bool) -> None:
    lease_id = f"lease-{uuid.uuid4().hex[:12]}"
    dbmod.record_attempt(
        db, lease_id=lease_id, machine_id=machine_id, job_id=job_id,
        task_id=f"task-{uuid.uuid4().hex[:6]}",
    )
    if accepted:
        dbmod.claim_attempt_credit(db, lease_id=lease_id, machine_id=machine_id)


def _get(client, user_id: str, **params) -> dict:
    r = client.get(
        "/v1alpha1/me/metrics",
        params=params,
        headers={"Authorization": f"Bearer {_jwt(user_id)}"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_a_brand_new_account_reads_zeros_and_nulls(client, db):
    body = _get(client, _new_user(db))
    assert body == {
        "window_days": 30,
        "jobs_total": 0, "jobs_succeeded": 0, "jobs_partial": 0, "jobs_failed": 0,
        "tasks_attempted": 0, "tasks_accepted": 0,
        "goodput_ratio": None,
        "lost_task_seconds": None,
        "mttr_seconds": None,
        "mttd_seconds": None,
        "machines_contributing": 0,
    }


def test_metrics_need_a_jwt(client):
    assert client.get("/v1alpha1/me/metrics").status_code == 401


def test_jobs_are_counted_by_the_outcome_recorded_for_them(client, db):
    owner = _new_user(db)
    _job(db, owner, status="SUCCEEDED")
    _job(db, owner, status="SUCCEEDED")
    _job(db, owner, status="PARTIAL")
    _job(db, owner, status="FAILED")
    _job(db, owner, status="RUNNING")

    body = _get(client, owner)
    assert body["jobs_total"] == 5
    assert body["jobs_succeeded"] == 2
    assert body["jobs_partial"] == 1
    assert body["jobs_failed"] == 1


def test_a_partial_job_is_never_counted_as_a_success(client, db):
    """PARTIAL means some shards were lost and the submitter opted into
    getting the rest. Folding it into "succeeded" on a page whose whole
    subject is reliability would flatter exactly the number being claimed."""
    owner = _new_user(db)
    _job(db, owner, status="PARTIAL")
    body = _get(client, owner)
    assert body["jobs_succeeded"] == 0
    assert body["jobs_partial"] == 1


def test_another_accounts_work_is_never_counted(client, db):
    mine, theirs = _new_user(db), _new_user(db)
    their_job = _job(db, theirs, status="SUCCEEDED")
    _attempt(db, job_id=their_job, machine_id=_machine(db, theirs), accepted=True)

    body = _get(client, mine)
    assert body["jobs_total"] == 0
    assert body["tasks_attempted"] == 0
    assert body["machines_contributing"] == 0


def test_work_older_than_the_window_is_excluded(client, db):
    owner = _new_user(db)
    _job(db, owner, status="SUCCEEDED", days_ago=0)
    old = _job(db, owner, status="SUCCEEDED", days_ago=60)
    _attempt(db, job_id=old, machine_id=_machine(db, owner), accepted=True)

    body = _get(client, owner)
    assert body["jobs_total"] == 1
    # The old job's attempts go with it: one window, one set of jobs, so the
    # task counts and the job counts always describe the same work.
    assert body["tasks_attempted"] == 0


def test_a_wider_window_reaches_further_back(client, db):
    owner = _new_user(db)
    _job(db, owner, status="SUCCEEDED", days_ago=60)
    assert _get(client, owner, window_days=30)["jobs_total"] == 0
    assert _get(client, owner, window_days=90)["jobs_total"] == 1
    assert _get(client, owner, window_days=90)["window_days"] == 90


def test_an_impossible_window_is_refused_rather_than_silently_changed(client, db):
    owner = _new_user(db)
    for bad in (0, -1, 100000):
        r = client.get(
            "/v1alpha1/me/metrics",
            params={"window_days": bad},
            headers={"Authorization": f"Bearer {_jwt(owner)}"},
        )
        assert r.status_code == 422, bad


def test_attempted_and_accepted_are_counted_apart(client, db):
    """The workspace rule everywhere money or metrics are involved: work
    attempted is not work accepted. A machine that claimed a lease and never
    committed did attempt it, and this page exists to show that gap."""
    owner = _new_user(db)
    job_id = _job(db, owner, status="PARTIAL")
    machine = _machine(db, owner)
    _attempt(db, job_id=job_id, machine_id=machine, accepted=True)
    _attempt(db, job_id=job_id, machine_id=machine, accepted=True)
    _attempt(db, job_id=job_id, machine_id=machine, accepted=True)
    _attempt(db, job_id=job_id, machine_id=machine, accepted=False)

    body = _get(client, owner)
    assert body["tasks_attempted"] == 4
    assert body["tasks_accepted"] == 3
    assert body["goodput_ratio"] == 0.75


def test_goodput_is_null_for_an_account_whose_jobs_never_ran(client, db):
    owner = _new_user(db)
    _job(db, owner, status="FAILED")
    body = _get(client, owner)
    assert body["tasks_attempted"] == 0
    assert body["goodput_ratio"] is None


def test_machines_are_counted_once_however_many_tasks_they_ran(client, db):
    owner = _new_user(db)
    job_id = _job(db, owner, status="SUCCEEDED")
    a, b = _machine(db, owner), _machine(db, owner)
    _attempt(db, job_id=job_id, machine_id=a, accepted=True)
    _attempt(db, job_id=job_id, machine_id=a, accepted=True)
    _attempt(db, job_id=job_id, machine_id=b, accepted=True)

    assert _get(client, owner)["machines_contributing"] == 2


def test_a_machine_that_only_ever_failed_is_not_counted_as_contributing(client, db):
    owner = _new_user(db)
    job_id = _job(db, owner, status="FAILED")
    _attempt(db, job_id=job_id, machine_id=_machine(db, owner), accepted=False)

    body = _get(client, owner)
    assert body["tasks_attempted"] == 1
    assert body["machines_contributing"] == 0


def test_a_federated_runs_rounds_count_towards_its_owner(client, db):
    """A federated run is N coordinator jobs under one local id, and its
    attempts are recorded against the ROUND's coordinator job — an id that
    is not a row in `jobs` at all. Joining only on `jobs.id` would report
    zero tasks for every federated run ever submitted."""
    owner = _new_user(db)
    job_id = fedavgmod.new_federated_job_id()
    dbmod.insert_job(
        db, job_id=job_id, owner_id=owner, name="fed",
        source={"mode": "federated", "rounds": 2}, spec=None, status="SUCCEEDED",
    )
    machine = _machine(db, owner)
    for index in range(2):
        coordinator_job_id = f"round-{uuid.uuid4().hex[:10]}"
        dbmod.insert_job_round(
            db, job_id=job_id, round_index=index, participants=1, mean_loss=0.5,
            contributors=[], coordinator_job_id=coordinator_job_id,
        )
        _attempt(db, job_id=coordinator_job_id, machine_id=machine, accepted=True)

    body = _get(client, owner)
    assert body["jobs_total"] == 1
    assert body["jobs_succeeded"] == 1
    assert body["tasks_attempted"] == 2
    assert body["tasks_accepted"] == 2
    assert body["machines_contributing"] == 1
