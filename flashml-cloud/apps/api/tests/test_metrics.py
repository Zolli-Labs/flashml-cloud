"""The Stage 8 metrics page, API half — and the nulls that keep it honest.

This page exists to prove the product's central claim: that work survives
machines dying. That makes it the one surface where a made-up number does
the most damage, because a confidently wrong MTTR is indistinguishable from
a measured one and nobody downstream can tell.

So the rule this file pins is not "compute the metrics" — it is **compute
only what this deployment's events actually support, and return null for
the rest**. It used to pin three nulls, each with a named missing event, so
that the day somebody recorded that event they would find a failing test
telling them the field could be real.

**That day was 2026-08-11.** Migration 0015 gave an attempt a terminal
outcome, so ``lost_task_seconds`` and ``mttr_seconds`` are now computed from
recorded events and the tests that pinned them null are gone. What replaces
them is stricter, not looser: tests that the numbers come from resolved
attempts only, that an unresolved attempt is never spent as a failure, and
that ``mttd_seconds`` is STILL null because the one instant it needs — when a
machine actually stopped — is still written down nowhere.

``null`` and ``0`` are different answers throughout: 0 attempted tasks and
"we cannot tell how many were attempted" are not the same fact, and a page
that renders them the same way is lying in one of the two cases.
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

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import fedavg as fedavgmod
from flashml_cloud_api import metrics as metricsmod
from flashml_cloud_api.app import create_cloud_app
from flashml_cloud_api.settings import Settings

JWT_SECRET = "test-jwt-secret-long-enough-for-hs256-abcdef"


# ---------------------------------------------------------------------------
# the rule: what the report says, and what it refuses to say
# ---------------------------------------------------------------------------


def test_goodput_is_accepted_over_resolved():
    assert metricsmod.goodput_ratio(accepted=3, resolved=4) == 0.75


def test_goodput_with_nothing_resolved_is_null_not_zero():
    """0.0 means "everything that finished was wasted", which is the worst
    number this page can show. "Nothing has finished yet" is not that,
    and it must never be a divide by zero either."""
    assert metricsmod.goodput_ratio(accepted=0, resolved=0) is None


def test_goodput_of_a_run_that_lost_everything_is_zero_not_null():
    """The other half of the same distinction: this one really is 0.0."""
    assert metricsmod.goodput_ratio(accepted=0, resolved=5) == 0.0


def test_the_report_carries_every_field_the_page_renders():
    """The shape is a contract with the console — a field that silently
    disappears renders as `undefined`, not as an error."""
    got = metricsmod.report(window_days=30, counts={})
    assert set(got) == {
        "window_days",
        "jobs_total", "jobs_succeeded", "jobs_partial", "jobs_failed",
        "tasks_attempted", "tasks_resolved", "tasks_accepted",
        "goodput_ratio",
        "lost_task_seconds",
        "mttr_seconds",
        "mttd_seconds",
        "machines_contributing",
    }


def test_goodput_divides_by_resolved_attempts_not_by_claimed_ones():
    """The bug this whole change exists to fix, in one assertion.

    Ten leases claimed, four resolved, three of those accepted. The old
    denominator was every attempts row, so an account was punished for work
    that was still running: hand out six more leases and the ratio falls,
    with nothing having gone wrong. Dividing by what actually finished makes
    the number bounded and recoverable."""
    got = metricsmod.report(
        window_days=30,
        counts={"tasks_attempted": 10, "tasks_resolved": 4, "tasks_accepted": 3},
    )
    assert got["tasks_attempted"] == 10
    assert got["goodput_ratio"] == 0.75


def test_lost_task_seconds_is_computed_from_resolved_attempts():
    """REAL since 0015. `resolved_at - claimed_at` over attempts that ended
    without being accepted — observed for a reported failure, the
    coordinator's own lease deadline for an expired one."""
    got = metricsmod.report(
        window_days=30,
        counts={"tasks_resolved": 4, "tasks_accepted": 3, "lost_seconds_total": 612.5},
    )
    assert got["lost_task_seconds"] == 612.5


def test_lost_task_seconds_is_null_until_something_has_resolved():
    """0.0 seconds lost is a claim — "no work was wasted" — and a flattering
    one. An account whose reliability has never been tested must not be shown
    it, exactly as it is not shown a 0.0 goodput."""
    assert metricsmod.report(window_days=30, counts={})["lost_task_seconds"] is None


def test_lost_task_seconds_is_zero_once_work_has_resolved_and_none_was_lost():
    """The other half of that distinction: here 0.0 is measured, not assumed."""
    got = metricsmod.report(
        window_days=30, counts={"tasks_resolved": 3, "tasks_accepted": 3}
    )
    assert got["lost_task_seconds"] == 0.0


def test_mttd_is_null_because_nothing_records_when_a_machine_stopped():
    """Mean time to DETECTION needs two timestamps: when a machine actually
    stopped, and when the system noticed. 0015 supplied only the second — a
    lease deadline says when the attempt stopped counting, not when the host
    died, and the gap between them is the entire quantity being measured.

    `machines.last_seen_at` is a single mutable column with no history, and
    the coordinator's LEASE_EXPIRED / NODE_HEARTBEAT_LOST events live in its
    own ledger, reachable only per-job over HTTP."""
    assert metricsmod.report(window_days=30, counts={})["mttd_seconds"] is None
    # ...and it stays null however much recovery evidence arrives: no
    # quantity of the OTHER interval adds up to this one.
    rich = metricsmod.report(
        window_days=30,
        counts={
            "tasks_resolved": 9, "tasks_accepted": 6,
            "lost_seconds_total": 900.0,
            "recoveries_observed": 3, "recovery_seconds_total": 90.0,
        },
    )
    assert rich["mttd_seconds"] is None


def test_mttr_is_the_mean_over_observed_recoveries():
    got = metricsmod.report(
        window_days=30,
        counts={"recoveries_observed": 4, "recovery_seconds_total": 168.0},
    )
    assert got["mttr_seconds"] == 42.0


def test_mttr_is_null_when_nothing_has_recovered():
    """Never 0.0, which on this page would read as "recovery is
    instantaneous" — the most flattering possible reading of no evidence."""
    assert metricsmod.report(window_days=30, counts={})["mttr_seconds"] is None
    failed_and_never_recovered = metricsmod.report(
        window_days=30,
        counts={"tasks_resolved": 2, "tasks_accepted": 0,
                "lost_seconds_total": 120.0, "recoveries_observed": 0},
    )
    assert failed_and_never_recovered["mttr_seconds"] is None
    # The failure still shows up where it honestly can.
    assert failed_and_never_recovered["lost_task_seconds"] == 120.0
    assert failed_and_never_recovered["goodput_ratio"] == 0.0


def test_a_missing_count_reports_zero_only_where_zero_is_true():
    """Counts come from tables that always answer — a query for an account
    with no jobs returns 0, and 0 is the truth. That is why these are 0 and
    the nulls above are null."""
    got = metricsmod.report(window_days=30, counts={})
    assert got["jobs_total"] == 0
    assert got["tasks_attempted"] == 0
    assert got["tasks_resolved"] == 0
    assert got["machines_contributing"] == 0


def test_the_window_is_echoed_so_the_page_can_label_its_own_numbers():
    assert metricsmod.report(window_days=7, counts={})["window_days"] == 7


# ---------------------------------------------------------------------------
# per-machine, per-capability-class acceptance — the rule a placement
# decision would read, pinned where it can be argued with
# ---------------------------------------------------------------------------


def _rows(machine, klass, outcomes, durations=None):
    durations = durations or [None] * len(outcomes)
    return [
        {"machine_id": machine, "capability_class": klass,
         "outcome": outcome, "duration_s": duration}
        for outcome, duration in zip(outcomes, durations)
    ]


def test_acceptance_is_reported_per_machine_and_per_class():
    got = metricsmod.acceptance_rates(
        _rows("m1", "cpu", ["accepted"] * 5)
        + _rows("m1", "gpu-1", ["failed"] * 5)
    )
    assert [(r["machine_id"], r["capability_class"], r["acceptance_rate"])
            for r in got] == [("m1", "cpu", 1.0), ("m1", "gpu-1", 0.0)]


def test_classes_are_never_aggregated_into_one_number_for_a_machine():
    """The design review's case, exactly. A host at 0.95 on cpu work and 0.40
    on gpu work reads ~0.9 aggregated — and then fails the GPU task, which is
    the only decision anybody was going to use the number for.

    Stated as a property of the OUTPUT, not of the arithmetic: there is no
    entry for the machine alone, so nothing downstream can pick one up by
    accident."""
    got = metricsmod.acceptance_rates(
        _rows("m1", "cpu", ["accepted"] * 19 + ["failed"])
        + _rows("m1", "gpu-1", ["accepted"] * 4 + ["failed"] * 6)
    )
    by_class = {r["capability_class"]: r["acceptance_rate"] for r in got}
    assert by_class == {"cpu": 0.95, "gpu-1": 0.4}
    assert all(r["capability_class"] for r in got), "an unlabelled rollup appeared"
    assert len(got) == 2


def test_thin_evidence_reports_none_and_not_a_rate():
    """Below MIN_EVIDENCE resolved attempts there is no rate — and None here
    means "has not been asked yet", which is a different fact from 0.0
    ("failed everything"). Confusing the two is how a pool stops accepting new
    volunteers: one unlucky first task and the newcomer scores zero for
    ever."""
    got = metricsmod.acceptance_rates(_rows("new", "cpu", ["failed"]))
    assert got[0]["acceptance_rate"] is None
    # The counts are still reported: useful to a human, useless to a
    # threshold, and the caller must not be able to divide them back.
    assert (got[0]["resolved"], got[0]["accepted"]) == (1, 0)


def test_the_evidence_threshold_is_the_schedulers_own():
    """One number, and it is the runtime's. Two copies would drift, and the
    drift would put the console's explanation and the placement decision on
    different sides of the same host."""
    assert metricsmod.MIN_EVIDENCE == 5
    thin = metricsmod.acceptance_rates(
        _rows("m", "cpu", ["accepted"] * (metricsmod.MIN_EVIDENCE - 1))
    )
    assert thin[0]["acceptance_rate"] is None
    enough = metricsmod.acceptance_rates(
        _rows("m", "cpu", ["accepted"] * metricsmod.MIN_EVIDENCE)
    )
    assert enough[0]["acceptance_rate"] == 1.0


def test_a_machine_that_failed_everything_scores_zero_not_none():
    """The other side of the same distinction, and the reason the threshold
    is on the count and never on the rate."""
    got = metricsmod.acceptance_rates(_rows("bad", "cpu", ["failed"] * 6))
    assert got[0]["acceptance_rate"] == 0.0


def test_unresolved_attempts_are_not_evidence_of_anything():
    """An attempt in flight — or one claimed before 0015 — has no outcome. It
    is dropped, not counted as a failure: a machine's rate must not fall
    merely because it is busy right now."""
    got = metricsmod.acceptance_rates(
        _rows("m", "cpu", ["accepted"] * 5 + [None, None, ""])
    )
    assert (got[0]["resolved"], got[0]["accepted"]) == (5, 5)
    assert got[0]["acceptance_rate"] == 1.0


def test_every_terminal_outcome_counts_in_the_denominator():
    """expired and abandoned are not accepted work, and a host whose leases
    keep running out is exactly the host this number exists to find."""
    got = metricsmod.acceptance_rates(
        _rows("m", "cpu", ["accepted", "accepted", "failed", "expired", "abandoned"])
    )
    assert (got[0]["resolved"], got[0]["accepted"]) == (5, 2)
    assert got[0]["acceptance_rate"] == 0.4


def test_untimed_rows_are_excluded_from_timing_but_not_from_the_rate():
    """Federated credit rows carry no duration — `fedavg.on_round` credits
    from the coordinator's task view, which reports none — so they can never
    be timing evidence. (They also write no `attempts` row at all, so they
    cannot reach this function in the first place; this is the belt to that
    pair of braces.) The median is taken over what was actually measured, and
    the untimed rows still count as resolved attempts."""
    got = metricsmod.acceptance_rates(
        _rows("m", "cpu", ["accepted"] * 5, [10.0, 30.0, 20.0, None, None])
    )
    assert got[0]["resolved"] == 5
    assert got[0]["median_seconds"] == 20.0


def test_a_group_with_nothing_timed_reports_no_median_rather_than_zero():
    got = metricsmod.acceptance_rates(_rows("m", "cpu", ["accepted"] * 5))
    assert got[0]["median_seconds"] is None


def test_the_median_is_not_dragged_by_one_attempt_that_hung():
    """Median rather than mean, matching `verify.timing_verdict`: one machine
    that sat there until its lease expired should move the typical duration by
    nothing."""
    got = metricsmod.acceptance_rates(
        _rows("m", "cpu", ["accepted"] * 5, [10.0, 11.0, 12.0, 13.0, 9000.0])
    )
    assert got[0]["median_seconds"] == 12.0


def test_no_rows_is_an_empty_answer_and_not_an_error():
    assert metricsmod.acceptance_rates([]) == []


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


def _attempt(
    db,
    *,
    job_id: str,
    machine_id: str,
    accepted: bool,
    task_id: str | None = None,
    failed: bool = False,
) -> str:
    """One attempt through the real writers, resolved or not.

    `accepted=False, failed=False` is an attempt that is still open — which
    since 0015 is a genuinely different row from a failed one, and several
    tests below turn on that difference.
    """
    lease_id = f"lease-{uuid.uuid4().hex[:12]}"
    dbmod.record_attempt(
        db, lease_id=lease_id, machine_id=machine_id, job_id=job_id,
        task_id=task_id or f"task-{uuid.uuid4().hex[:6]}",
    )
    if accepted:
        dbmod.claim_attempt_credit(db, lease_id=lease_id, machine_id=machine_id)
    elif failed:
        dbmod.record_attempt_failure(db, lease_id=lease_id, machine_id=machine_id)
    return lease_id


def _claimed_seconds_ago(db, lease_id: str, seconds: int) -> None:
    """Backdate a claim so a resolved attempt has a duration worth asserting
    on. The instants are the database's own, exactly as in production."""
    with db.cursor() as cur:
        cur.execute(
            "update public.attempts"
            "   set claimed_at = claimed_at - make_interval(secs => %s)"
            " where lease_id = %s",
            (float(seconds), lease_id),
        )


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
        "tasks_attempted": 0, "tasks_resolved": 0, "tasks_accepted": 0,
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
    _attempt(db, job_id=job_id, machine_id=machine, accepted=False, failed=True)

    body = _get(client, owner)
    assert body["tasks_attempted"] == 4
    assert body["tasks_resolved"] == 4
    assert body["tasks_accepted"] == 3
    assert body["goodput_ratio"] == 0.75


def test_an_attempt_still_running_is_not_counted_against_the_account(client, db):
    """The survivorship bias, inverted, and the reason 0015 exists.

    Before it, a failed attempt and an in-flight one were the same row —
    `accepted_at is null` — so an account was punished the moment work was
    handed out and the ratio only ever recovered if that exact lease was
    accepted. Here two of three leases are still open: they are ATTEMPTED,
    they are not RESOLVED, and goodput describes only what finished."""
    owner = _new_user(db)
    job_id = _job(db, owner, status="RUNNING")
    machine = _machine(db, owner)
    _attempt(db, job_id=job_id, machine_id=machine, accepted=True)
    _attempt(db, job_id=job_id, machine_id=machine, accepted=False)
    _attempt(db, job_id=job_id, machine_id=machine, accepted=False)

    body = _get(client, owner)
    assert body["tasks_attempted"] == 3
    assert body["tasks_resolved"] == 1
    assert body["tasks_accepted"] == 1
    assert body["goodput_ratio"] == 1.0
    # ...and an open attempt has burned no *lost* time either: it may still
    # succeed. `now() - claimed_at` here would grow on every page load.
    assert body["lost_task_seconds"] == 0.0


def test_goodput_is_null_for_an_account_whose_jobs_never_ran(client, db):
    owner = _new_user(db)
    _job(db, owner, status="FAILED")
    body = _get(client, owner)
    assert body["tasks_attempted"] == 0
    assert body["tasks_resolved"] == 0
    assert body["goodput_ratio"] is None
    assert body["lost_task_seconds"] is None


def test_lost_seconds_are_measured_from_the_failed_attempts_own_clock(client, db):
    """The number `contributions` structurally cannot produce: that ledger
    holds accepted work only, so every duration in it is a duration that
    succeeded. This one is a duration that did not."""
    owner = _new_user(db)
    job_id = _job(db, owner, status="FAILED")
    machine = _machine(db, owner)
    lease = _attempt(db, job_id=job_id, machine_id=machine,
                     accepted=False, failed=True)
    _claimed_seconds_ago(db, lease, 300)

    body = _get(client, owner)
    assert body["tasks_resolved"] == 1
    assert body["tasks_accepted"] == 0
    assert body["goodput_ratio"] == 0.0
    assert 300 <= body["lost_task_seconds"] < 320


def test_recovery_is_measured_from_the_failure_to_the_replacement(client, db):
    """MTTR, from two instants that are both now recorded: when the failure
    was resolved, and when the replacement attempt on the SAME task was
    accepted. Pairing is on `(job_id, task_id)` — a different task finishing
    is not a recovery from this one."""
    owner = _new_user(db)
    job_id = _job(db, owner, status="SUCCEEDED")
    dead, live = _machine(db, owner), _machine(db, owner)

    _attempt(db, job_id=job_id, machine_id=dead, accepted=False,
             failed=True, task_id="task-a")
    replacement = _attempt(db, job_id=job_id, machine_id=live, accepted=True,
                           task_id="task-a")
    assert replacement  # the credit path resolved it

    body = _get(client, owner)
    assert body["tasks_resolved"] == 2
    assert body["tasks_accepted"] == 1
    assert body["mttr_seconds"] is not None
    assert 0 <= body["mttr_seconds"] < 30


def test_a_failure_that_never_recovered_reports_no_recovery_time(client, db):
    """A mean over RECOVERIES, not over failures. An abandoned task has no
    recovery interval, and substituting "time since it failed" would let one
    of them drag this number up for ever — the same growing-forever mistake
    `lost_task_seconds` refuses."""
    owner = _new_user(db)
    job_id = _job(db, owner, status="FAILED")
    machine = _machine(db, owner)
    _attempt(db, job_id=job_id, machine_id=machine, accepted=False,
             failed=True, task_id="task-lost")

    body = _get(client, owner)
    assert body["mttr_seconds"] is None
    assert body["lost_task_seconds"] is not None


def test_a_machine_that_simply_vanished_is_counted_as_lost_work(client, db):
    """End to end, on the failure mode this product exists to survive: no
    fail hop, no completion, nothing from the coordinator — just a lease
    whose deadline passed. The route reconciles it before counting, so the
    thirty minutes that machine held the task are lost work rather than an
    attempt that stays open for ever."""
    owner = _new_user(db)
    job_id = _job(db, owner, status="PARTIAL")
    machine = _machine(db, owner)
    lease = f"lease-{uuid.uuid4().hex[:12]}"
    dbmod.record_attempt(
        db, lease_id=lease, machine_id=machine, job_id=job_id,
        task_id="task-gone",
        # Well past the grace window, so the reconciler is willing to act.
        deadline=datetime.now(timezone.utc) - timedelta(seconds=1200),
    )
    # The lease was held for half an hour before it ran out.
    _claimed_seconds_ago(db, lease, 3000)

    body = _get(client, owner)
    assert body["tasks_attempted"] == 1
    assert body["tasks_resolved"] == 1
    assert body["tasks_accepted"] == 0
    assert body["goodput_ratio"] == 0.0
    assert 1790 <= body["lost_task_seconds"] <= 1810

    with db.cursor() as cur:
        cur.execute(
            "select outcome from public.attempts where lease_id = %s", (lease,)
        )
        assert cur.fetchone()["outcome"] == "expired"


def test_detection_time_stays_null_even_with_recovery_evidence(client, db):
    """The one field still waiting on an event nobody records: the instant a
    machine actually stopped. A lease deadline says when the attempt stopped
    counting, which is a different thing, and the difference IS the metric."""
    owner = _new_user(db)
    job_id = _job(db, owner, status="PARTIAL")
    machine = _machine(db, owner)
    _attempt(db, job_id=job_id, machine_id=machine, accepted=False,
             failed=True, task_id="task-x")
    _attempt(db, job_id=job_id, machine_id=machine, accepted=True,
             task_id="task-x")

    assert _get(client, owner)["mttd_seconds"] is None


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
