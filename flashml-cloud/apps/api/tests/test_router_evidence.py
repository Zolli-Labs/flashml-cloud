"""What the router is allowed to know, and where it comes from.

Two producers landed on 2026-08-11 and this file is about both, plus the
column they both read:

- ``db.acceptance_rate_rows`` — the input ``metrics.acceptance_rates`` has
  always required and nothing supplied. That function is keyed on
  ``(machine_id, capability_class)`` and refuses to roll up across classes, so
  a row without a class cannot reach it at all; it was written, tested, and
  unwired for want of this query.
- ``db.peer_task_observations`` — the same durations ``peer_task_durations``
  returns, LABELLED. Bare floats cannot satisfy the no-cross-class rule: a
  list of numbers with no class on them is what a cross-class average looks
  like.

Both derive the class with ``router.estimator.hardware_class`` and neither
carries a ladder of its own. **One producer** is the property under test —
two implementations would agree the day they were written and never again,
and the disagreement would surface as a machine whose durations pool into one
class while its acceptance rate is filed under another.

And the column: until this landed ``machines.capabilities`` held
``{"dataset_cache_bytes": n}`` and nothing else, so every classifier reading it
saw a machine with no cores and no GPUs. A 4090 rig classed as ``cpu-small``,
and no acceptance rate could be keyed by class at all.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Json

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import metrics as metricsmod
from flashml_cloud_api import router as routermod

RTX_4090 = {
    "index": 0,
    "name": "NVIDIA GeForce RTX 4090",
    "memory_total_mb": 24564,
    "compute_capability": "8.9",
}
H100 = {
    "index": 0,
    "name": "NVIDIA H100 PCIe",
    "memory_total_mb": 81559,
    "compute_capability": "9.0",
}
LAPTOP = {"cpu_cores": 4}


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def make_user(db) -> str:
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id, email) values (%s::uuid, %s)",
            (user_id, f"{user_id}@example.test"),
        )
        cur.execute("insert into public.profiles (id) values (%s::uuid)", (user_id,))
    return user_id


def make_machine(db, owner_id, *, capabilities) -> str:
    machine_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into public.machines (id, owner_id, node_id, capabilities, status)"
            " values (%s::uuid, %s::uuid, %s, %s, 'active')",
            (machine_id, owner_id, f"node-{machine_id}", Json(capabilities)),
        )
    return machine_id


def resolve_attempt(db, *, machine_id, job_id, task_id, outcome, seconds=12.0):
    """One resolved attempt, written the way the three real writers leave it:
    `claimed_at` and, for a terminal outcome, `resolved_at`."""
    lease = f"lease-{uuid.uuid4().hex[:12]}"
    claimed = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    with db.cursor() as cur:
        cur.execute(
            "insert into public.attempts"
            " (lease_id, machine_id, job_id, task_id, claimed_at, accepted_at,"
            "  resolved_at, outcome)"
            " values (%s, %s::uuid, %s, %s, %s, %s, %s, %s)",
            (
                lease,
                machine_id,
                job_id,
                task_id,
                claimed,
                datetime.now(timezone.utc) if outcome == "accepted" else None,
                None if outcome is None else datetime.now(timezone.utc),
                outcome,
            ),
        )
    return lease


def contribute(db, *, machine_id, job_id, task_id, duration_s):
    with db.cursor() as cur:
        cur.execute(
            "insert into public.contributions (machine_id, job_id, task_id, duration_s)"
            " values (%s::uuid, %s, %s, %s)",
            (machine_id, job_id, task_id, duration_s),
        )


# ---------------------------------------------------------------------------
# the column both producers read
# ---------------------------------------------------------------------------


def test_a_registration_persists_the_hardware_the_classifier_needs(db):
    """The gap that made every class null. `set_machine_capabilities` wrote
    only `dataset_cache_bytes`, so `hardware_class` read a machine with no
    cores and no GPUs — which is `None`, and a null class reaches no rate and
    pools no duration."""
    owner = make_user(db)
    machine = make_machine(db, owner, capabilities={})

    dbmod.set_machine_capabilities(
        db,
        machine_id=machine,
        sandbox_capable=True,
        argv_capable=True,
        unsandboxed_argv_capable=False,
        module_capable=True,
        dataset_cache_bytes=1024,
        reported={"cpu_cores": 16, "memory_bytes": 68719476736, "gpus": [RTX_4090]},
    )

    with db.cursor() as cur:
        cur.execute(
            "select capabilities from public.machines where id = %s::uuid", (machine,)
        )
        stored = cur.fetchone()["capabilities"]

    assert routermod.hardware_class(stored) == "gpu-24gb"
    # The capacity figure the dataset gate reads is still there beside it.
    assert stored["dataset_cache_bytes"] == 1024


def test_a_narrower_re_registration_retracts_what_it_no_longer_advertises(db):
    """A driver that broke between two registrations must stop selling the GPU
    it can no longer see. A merge that only writes the keys it was given cannot
    retract, so every allowlisted key is written every time — including as
    null."""
    owner = make_user(db)
    machine = make_machine(db, owner, capabilities={})

    common = dict(
        machine_id=machine,
        sandbox_capable=False,
        argv_capable=False,
        unsandboxed_argv_capable=False,
        module_capable=True,
    )
    dbmod.set_machine_capabilities(
        db, **common, reported={"cpu_cores": 16, "gpus": [H100]}
    )
    dbmod.set_machine_capabilities(db, **common, reported={"cpu_cores": 16})

    with db.cursor() as cur:
        cur.execute(
            "select capabilities from public.machines where id = %s::uuid", (machine,)
        )
        stored = cur.fetchone()["capabilities"]
    assert routermod.hardware_class(stored) == "cpu-large"


def test_an_agent_cannot_write_its_own_pools_through_the_capability_snapshot(db):
    """Pools are stamped SERVER-SIDE by the register proxy from the owner's
    live memberships, precisely because an agent may not name its own. The
    allowlist is what keeps the agent's claim out of the same jsonb — one
    careless read away from being believed."""
    owner = make_user(db)
    machine = make_machine(db, owner, capabilities={})

    dbmod.set_machine_capabilities(
        db,
        machine_id=machine,
        sandbox_capable=False,
        argv_capable=False,
        unsandboxed_argv_capable=False,
        module_capable=True,
        reported={"cpu_cores": 4, "pools": ["a-pool-i-do-not-belong-to"]},
    )

    with db.cursor() as cur:
        cur.execute(
            "select capabilities from public.machines where id = %s::uuid", (machine,)
        )
        stored = cur.fetchone()["capabilities"]
    assert "pools" not in stored


def test_a_typo_shaped_reading_is_refused_rather_than_believed(db):
    """`cpu_cores: true` would arrive as one core — a plausible number derived
    from something that was never a measurement — and a `gpus` list of strings
    would class every entry as an unreadable device."""
    owner = make_user(db)
    machine = make_machine(db, owner, capabilities={})

    dbmod.set_machine_capabilities(
        db,
        machine_id=machine,
        sandbox_capable=False,
        argv_capable=False,
        unsandboxed_argv_capable=False,
        module_capable=True,
        reported={"cpu_cores": True, "gpus": ["4090"]},
    )

    with db.cursor() as cur:
        cur.execute(
            "select capabilities from public.machines where id = %s::uuid", (machine,)
        )
        stored = cur.fetchone()["capabilities"]
    assert stored["cpu_cores"] is None
    assert stored["gpus"] == []
    assert routermod.hardware_class(stored) is None


# ---------------------------------------------------------------------------
# acceptance rates finally have a class on them
# ---------------------------------------------------------------------------


def test_resolved_attempts_arrive_labelled_with_a_class(db):
    """The join that was missing. Without it `acceptance_rates` cannot be
    called at all: it keys on the pair and there was no producer of the second
    half anywhere in the repo."""
    owner = make_user(db)
    gpu = make_machine(db, owner, capabilities={"gpus": [RTX_4090]})
    cpu = make_machine(db, owner, capabilities=LAPTOP)
    job = f"job-{uuid.uuid4().hex[:8]}"

    resolve_attempt(db, machine_id=gpu, job_id=job, task_id="t1", outcome="accepted")
    resolve_attempt(db, machine_id=cpu, job_id=job, task_id="t2", outcome="failed")

    rows = dbmod.acceptance_rate_rows(db, machine_ids=[gpu, cpu])
    by_machine = {row["machine_id"]: row for row in rows}
    assert by_machine[gpu]["capability_class"] == "gpu-24gb"
    assert by_machine[cpu]["capability_class"] == "cpu-small"


def test_an_unresolved_attempt_is_not_evidence_and_never_a_failure(db):
    """In flight, or pre-0015. Counting it would make a machine's rate fall
    because it is busy — the survivorship bias inverted."""
    owner = make_user(db)
    machine = make_machine(db, owner, capabilities={"gpus": [RTX_4090]})
    job = f"job-{uuid.uuid4().hex[:8]}"

    resolve_attempt(db, machine_id=machine, job_id=job, task_id="t1", outcome="accepted")
    resolve_attempt(db, machine_id=machine, job_id=job, task_id="t2", outcome=None)

    rows = dbmod.acceptance_rate_rows(db, machine_ids=[machine])
    assert [row["outcome"] for row in rows] == ["accepted"]


def test_only_an_accepted_attempt_carries_a_duration(db):
    """`resolved_at - claimed_at` on a failed attempt is time that was WASTED,
    not time a task takes, and `median_seconds` is a statement about the
    second. The row still counts toward the rate."""
    owner = make_user(db)
    machine = make_machine(db, owner, capabilities={"gpus": [RTX_4090]})
    job = f"job-{uuid.uuid4().hex[:8]}"

    resolve_attempt(
        db, machine_id=machine, job_id=job, task_id="t1", outcome="accepted", seconds=9
    )
    resolve_attempt(
        db, machine_id=machine, job_id=job, task_id="t2", outcome="expired", seconds=900
    )

    rows = dbmod.acceptance_rate_rows(db, machine_ids=[machine])
    timed = {row["outcome"]: row["duration_s"] for row in rows}
    assert timed["expired"] is None
    assert 8.0 <= timed["accepted"] <= 12.0


def test_a_machine_with_no_readable_hardware_is_dropped_not_filed_as_none(db):
    """`str(None)` would key a group under the literal string "None" — a rate
    reported against a class that does not exist. Unproven is the honest
    answer, and it is a state the fleet handles."""
    owner = make_user(db)
    unreadable = make_machine(
        db, owner, capabilities={"gpus": [{"index": 0, "memory_total_mb": None}]}
    )
    job = f"job-{uuid.uuid4().hex[:8]}"
    resolve_attempt(
        db, machine_id=unreadable, job_id=job, task_id="t1", outcome="accepted"
    )

    assert dbmod.acceptance_rate_rows(db, machine_ids=[unreadable]) == []


def test_the_rate_is_none_below_five_and_a_number_at_five(db):
    """`MIN_EVIDENCE`, end to end through the producer that was missing. 0.0
    means "it failed everything" and None means "it has not been asked yet";
    a pool that confuses the two stops accepting new volunteers on their first
    unlucky task."""
    owner = make_user(db)
    machine = make_machine(db, owner, capabilities={"gpus": [RTX_4090]})
    job = f"job-{uuid.uuid4().hex[:8]}"

    for index in range(metricsmod.MIN_EVIDENCE - 1):
        resolve_attempt(
            db, machine_id=machine, job_id=job, task_id=f"t{index}", outcome="accepted"
        )

    thin = metricsmod.acceptance_rates(
        dbmod.acceptance_rate_rows(db, machine_ids=[machine])
    )
    assert len(thin) == 1
    assert thin[0]["resolved"] == metricsmod.MIN_EVIDENCE - 1
    assert thin[0]["accepted"] == metricsmod.MIN_EVIDENCE - 1
    # Four for four, and still no rate. The counts are reported because "4 of
    # 4" is useful to a human and useless to a threshold.
    assert thin[0]["acceptance_rate"] is None

    resolve_attempt(db, machine_id=machine, job_id=job, task_id="t-last", outcome="failed")
    now = metricsmod.acceptance_rates(
        dbmod.acceptance_rate_rows(db, machine_ids=[machine])
    )
    assert now[0]["resolved"] == metricsmod.MIN_EVIDENCE
    assert now[0]["acceptance_rate"] == 0.8


def test_a_rate_is_never_pooled_across_two_classes_of_the_same_machine(db):
    """`acceptance_rates` produces one entry per pair and no rollup, and
    `select_acceptance` refuses to borrow a neighbouring class's number. Both
    hold over rows this producer supplies."""
    owner = make_user(db)
    gpu = make_machine(db, owner, capabilities={"gpus": [RTX_4090]})
    job = f"job-{uuid.uuid4().hex[:8]}"
    for index in range(metricsmod.MIN_EVIDENCE):
        resolve_attempt(
            db, machine_id=gpu, job_id=job, task_id=f"t{index}", outcome="accepted"
        )

    rates = metricsmod.acceptance_rates(
        dbmod.acceptance_rate_rows(db, machine_ids=[gpu])
    )
    assert routermod.select_acceptance(
        rates, machine_id=gpu, capability_class="gpu-24gb"
    )["acceptance_rate"] == 1.0
    # The same machine, asked about work it has never done in another class.
    assert (
        routermod.select_acceptance(rates, machine_id=gpu, capability_class="cpu-small")
        is None
    )
    assert routermod.reliability_tier(None) == routermod.TIER_UNPROVEN


# ---------------------------------------------------------------------------
# labelled durations
# ---------------------------------------------------------------------------


def test_durations_arrive_labelled_and_the_estimator_keeps_one_class(db):
    """The reason a labelled variant exists at all. `peer_task_durations`
    returns bare floats, and a bare float from an H100 is indistinguishable
    from one from a laptop — which is exactly the pooling the estimator
    refuses."""
    owner = make_user(db)
    gpu_a = make_machine(db, owner, capabilities={"gpus": [RTX_4090]})
    gpu_b = make_machine(db, owner, capabilities={"gpus": [RTX_4090]})
    hopper = make_machine(db, owner, capabilities={"gpus": [H100]})
    job = f"job-{uuid.uuid4().hex[:8]}"

    contribute(db, machine_id=gpu_a, job_id=job, task_id="t1", duration_s=10.0)
    contribute(db, machine_id=gpu_b, job_id=job, task_id="t2", duration_s=12.0)
    contribute(db, machine_id=hopper, job_id=job, task_id="t3", duration_s=2.0)

    rows = dbmod.peer_task_observations(db, job_id=job)
    assert {row["capability_class"] for row in rows} == {"gpu-24gb", "gpu-80gb-hopper"}

    observations = tuple(
        routermod.Observation(
            seconds=row["duration_s"],
            capability_class=row["capability_class"],
            federated=row["federated"],
        )
        for row in rows
    )
    estimate = routermod.estimate_task_seconds(
        [routermod.Evidence(rung=routermod.RUNG_SAME_JOB, observations=observations)],
        capability_class="gpu-24gb",
    )
    # Two samples, both from the 24GB pair. The Hopper's 2.0s is dropped, not
    # averaged in — with it the band would sit far lower.
    assert estimate is not None
    assert estimate.n == 2
    assert estimate.basis == routermod.BASIS_PROJECTED
    assert estimate.low >= 5.0


def test_a_federated_contribution_is_excluded_and_labelled_as_one(db):
    """Two mechanisms for one rule. `fedavg.on_round` credits from the
    coordinator's task view, which reports no duration — so the untimed rows
    fall out of the query — and a federated row that somehow acquired a
    duration is still flagged, so `estimator._usable` refuses it."""
    owner = make_user(db)
    machine = make_machine(db, owner, capabilities={"gpus": [RTX_4090]})
    parent = f"fed-{uuid.uuid4().hex[:8]}"
    round_job = f"round-{uuid.uuid4().hex[:8]}"

    with db.cursor() as cur:
        cur.execute(
            "insert into public.jobs (id, owner_id, status) values (%s, %s::uuid, %s)",
            (parent, owner, "RUNNING"),
        )
        cur.execute(
            "insert into public.job_rounds"
            " (job_id, round, participants, coordinator_job_id)"
            " values (%s, %s, %s, %s)",
            (parent, 0, 1, round_job),
        )

    # What `fedavg` actually writes: a credit with no duration at all.
    contribute(db, machine_id=machine, job_id=round_job, task_id="t1", duration_s=None)
    assert dbmod.peer_task_observations(db, job_id=round_job) == []

    # And the belt-and-braces case: a federated row that somehow has one.
    contribute(db, machine_id=machine, job_id=round_job, task_id="t2", duration_s=7.0)
    rows = dbmod.peer_task_observations(db, job_id=round_job)
    assert [row["federated"] for row in rows] == [True]

    observations = tuple(
        routermod.Observation(
            seconds=row["duration_s"],
            capability_class=row["capability_class"],
            federated=row["federated"],
        )
        for row in rows
    )
    assert (
        routermod.estimate_task_seconds(
            [
                routermod.Evidence(
                    rung=routermod.RUNG_SAME_JOB, observations=observations
                )
            ],
            capability_class="gpu-24gb",
        )
        is None
    )


def test_excluding_a_machine_is_optional_here_and_mandatory_next_door(db):
    """A verifier grades one machine and must not grade it against itself; a
    PLAN is about the whole fleet and has no subject to exclude. Same rows,
    two questions, and the older signature is left alone."""
    owner = make_user(db)
    subject = make_machine(db, owner, capabilities={"gpus": [RTX_4090]})
    peer = make_machine(db, owner, capabilities={"gpus": [RTX_4090]})
    job = f"job-{uuid.uuid4().hex[:8]}"

    contribute(db, machine_id=subject, job_id=job, task_id="t1", duration_s=1.0)
    contribute(db, machine_id=peer, job_id=job, task_id="t2", duration_s=11.0)

    everything = dbmod.peer_task_observations(db, job_id=job)
    assert sorted(row["duration_s"] for row in everything) == [1.0, 11.0]

    without_subject = dbmod.peer_task_observations(
        db, job_id=job, exclude_machine_id=subject
    )
    assert [row["duration_s"] for row in without_subject] == [11.0]
    # The unlabelled original is unchanged and still excludes by construction.
    assert dbmod.peer_task_durations(db, job_id=job, machine_id=subject) == [11.0]
