"""One id spanning session -> sandbox -> job -> task -> lease, and the rule
that keeps it honest.

D-4 asks for a correlation id that survives a hibernation, which is the
specific thing a request id cannot do: a request id dies with the response and
the work it names does not. So the id lives in columns, on the three tables
that already anchor those five ids — `jobs` (job_id), `sandbox_sessions`
(session_id + sandbox_id) and `attempts` (task_id + lease_id) — and it is read
back out of Postgres on the far side of the pause by a process that did not
mint it.

**The property every test below is really about is D-2: absent is null.** A row
with no correlation id says so, for ever. Minting one on read, or on write when
none was supplied, manufactures a thread that does not exist and makes two
unrelated pieces of work look related — the same failure mode as a `pass`
written where `record_verification` should have said `unknown`, and it fails
the same way: silently, and in the direction that looks like success.

The one place a fresh id may be created is the **edge** — the outermost thing a
user initiated. `start_session` is such an edge (D-1 names a sandbox session as
one), and it is tested here both ways: it inherits when the training job
already carries an id, and mints only when nothing upstream has one.
"""
from __future__ import annotations

import uuid

import pytest

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import observability as obs
from flashml_cloud_api import sandbox_orchestrator as orch
from flashml_cloud_api import sandbox_sessions as ss

# The orchestrator's own `db` fixture, deliberately, rather than the one in
# `test_jobs_from_repo`: it clears `public.sandbox_sessions` between tests, and
# the fake gateway hands out `sbx_0001` per instance, so a session table that
# survives the previous test collides on `external_sandbox_id`.
from test_sandbox_orchestrator import (  # noqa: F401 - fixtures and helpers
    _pool,
    _start,
    _user,
    _wake,
    _world,
    db,
)

CID = "7f1a4a26-0d1f-4a1e-9a8e-1b2c3d4e5f60"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _job(db, owner_id: str, *, correlation_id: str | None = None) -> str:
    job_id = f"job-{uuid.uuid4().hex[:12]}"
    dbmod.insert_job(
        db, job_id=job_id, owner_id=owner_id, name="a name the submitter typed",
        source=None, spec=None, status="RUNNING", correlation_id=correlation_id,
    )
    return job_id


def _machine(db, owner_id: str) -> str:
    return str(dbmod.insert_machine(
        db, owner_id=owner_id, node_id=f"node-{uuid.uuid4().hex[:12]}",
        name="a hostname its host supplied", platform="linux",
    ))


def _job_correlation(db, job_id: str):
    row = db.execute(
        "select correlation_id from public.jobs where id = %s", (job_id,)
    ).fetchone()
    return row["correlation_id"] and str(row["correlation_id"])


def _attempt_correlation(db, lease_id: str):
    row = db.execute(
        "select correlation_id from public.attempts where lease_id = %s",
        (lease_id,),
    ).fetchone()
    return row["correlation_id"] and str(row["correlation_id"])


def _session_correlation(db, session_id: str):
    row = db.execute(
        "select correlation_id from public.sandbox_sessions where id = %s",
        (session_id,),
    ).fetchone()
    return row["correlation_id"] and str(row["correlation_id"])


# ---------------------------------------------------------------------------
# The schema
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table", ["jobs", "sandbox_sessions", "attempts"],
)
def test_the_column_is_a_nullable_uuid_with_no_default(db, table):
    """Nullable, because absent is a real answer. No DEFAULT, because a
    default IS minting on write — the database would manufacture a thread for
    every row nobody supplied one for, which is exactly D-2's prohibition
    expressed in DDL and is the version of it nobody would ever notice."""
    row = db.execute(
        "select is_nullable, column_default, data_type"
        "  from information_schema.columns"
        " where table_schema = 'public' and table_name = %s"
        "   and column_name = 'correlation_id'",
        (table,),
    ).fetchone()
    assert row is not None, f"the migration did not reach public.{table}"
    assert row["is_nullable"] == "YES"
    assert row["column_default"] is None
    assert row["data_type"] == "uuid"


def test_every_row_that_predates_the_migration_is_null_and_stays_null(db):
    """Backfilling would have been the single most damaging thing this
    migration could do: one UPDATE, and every job this deployment has ever run
    is threaded to every other, permanently and unrecoverably."""
    assert not db.execute(
        "select 1 from public.jobs where correlation_id is not null"
        " and created_at < (select applied_at from public.schema_migrations"
        "                    where version like '0026%')"
        " limit 1"
    ).fetchall()


# ---------------------------------------------------------------------------
# jobs: absent is null, all the way through the write path
# ---------------------------------------------------------------------------


def test_a_job_written_with_no_correlation_id_is_null(db):
    owner = _user(db)
    assert _job_correlation(db, _job(db, owner)) is None


def test_a_job_written_with_a_correlation_id_keeps_exactly_that_one(db):
    owner = _user(db)
    assert _job_correlation(db, _job(db, owner, correlation_id=CID)) == CID


def test_reading_a_job_twice_never_mints_one(db):
    """The read path is where a tolerant implementation is most tempting and
    least visible: nothing fails, and the next reader sees a thread."""
    owner = _user(db)
    job_id = _job(db, owner)
    dbmod.fetch_job_for_owner(db, job_id, owner)
    dbmod.fetch_job_for_owner(db, job_id, owner)
    assert _job_correlation(db, job_id) is None


def test_a_job_refuses_a_correlation_id_that_is_not_one(db):
    """A hostname is not an id, and storing it would put a submitter-authored
    string into the column every log line is keyed on."""
    owner = _user(db)
    job_id = f"job-{uuid.uuid4().hex[:12]}"
    with pytest.raises(ValueError):
        dbmod.insert_job(
            db, job_id=job_id, owner_id=owner, name=None, source=None,
            spec=None, status="RUNNING", correlation_id="phongs-macbook.local",
        )


# ---------------------------------------------------------------------------
# job -> task -> lease: the attempt inherits, and inheriting is not minting
# ---------------------------------------------------------------------------


def test_an_attempt_inherits_the_correlation_id_of_the_job_it_is_an_attempt_of(db):
    """The hop that makes `task_id` and `lease_id` findable from a job.

    Inheritance, not minting: the value is copied from the row this attempt is
    literally an attempt of, in the same INSERT, so it can never relate two
    unrelated pieces of work — the two ends are the same piece of work by
    definition.
    """
    owner = _user(db)
    job_id = _job(db, owner, correlation_id=CID)
    lease = f"lease-{uuid.uuid4().hex[:8]}"
    dbmod.record_attempt(
        db, lease_id=lease, machine_id=_machine(db, owner),
        job_id=job_id, task_id="trial-000",
    )
    assert _attempt_correlation(db, lease) == CID


def test_an_attempt_on_a_job_with_no_correlation_id_is_null(db):
    owner = _user(db)
    lease = f"lease-{uuid.uuid4().hex[:8]}"
    dbmod.record_attempt(
        db, lease_id=lease, machine_id=_machine(db, owner),
        job_id=_job(db, owner), task_id="trial-000",
    )
    assert _attempt_correlation(db, lease) is None


def test_an_attempt_on_a_coordinator_side_job_id_is_null_not_invented(db):
    """`attempts.job_id` is text with no foreign key precisely because it may
    name a job this database has never heard of. The lookup misses, and a miss
    is `null` — never a fresh id, and never an exception."""
    owner = _user(db)
    lease = f"lease-{uuid.uuid4().hex[:8]}"
    dbmod.record_attempt(
        db, lease_id=lease, machine_id=_machine(db, owner),
        job_id="a-job-only-the-coordinator-knows", task_id="trial-000",
    )
    assert _attempt_correlation(db, lease) is None


def test_an_attempt_on_a_federated_round_inherits_from_the_parent_run(db):
    """The hole a job-table lookup alone would leave, and it is the product's
    main path.

    A federated run is N coordinator jobs under one parent. `attempts.job_id`
    holds the ROUND's coordinator job id, which is not a row in `public.jobs`
    — so a direct lookup finds nothing and every attempt in every federated
    run would be off the chain. `job_rounds.coordinator_job_id` is the join
    that closes it, and it is a join onto the parent this round belongs to,
    so it is still inheritance and still cannot relate anything unrelated.
    """
    owner = _user(db)
    parent = _job(db, owner, correlation_id=CID)
    round_job = f"round-{uuid.uuid4().hex[:12]}"
    dbmod.insert_job_round(
        db, job_id=parent, round_index=0, participants=2, mean_loss=0.5,
        contributors=["node-a", "node-b"], coordinator_job_id=round_job,
    )
    lease = f"lease-{uuid.uuid4().hex[:8]}"
    dbmod.record_attempt(
        db, lease_id=lease, machine_id=_machine(db, owner),
        job_id=round_job, task_id="it00-shard-000",
    )
    assert _attempt_correlation(db, lease) == CID


def test_an_explicit_correlation_id_wins_over_the_inherited_one(db):
    owner = _user(db)
    other = str(uuid.uuid4())
    lease = f"lease-{uuid.uuid4().hex[:8]}"
    dbmod.record_attempt(
        db, lease_id=lease, machine_id=_machine(db, owner),
        job_id=_job(db, owner, correlation_id=CID), task_id="trial-000",
        correlation_id=other,
    )
    assert _attempt_correlation(db, lease) == other


def test_a_re_forwarded_claim_does_not_acquire_a_thread_it_did_not_have(db):
    """`record_attempt` is `on conflict do nothing` because one lease is one
    attempt however many times the claim is forwarded. The second call must
    not be the one that writes a correlation id — a retry is not new
    information about what started this work."""
    owner = _user(db)
    machine = _machine(db, owner)
    lease = f"lease-{uuid.uuid4().hex[:8]}"
    job_id = _job(db, owner)
    dbmod.record_attempt(
        db, lease_id=lease, machine_id=machine, job_id=job_id, task_id="trial-000",
    )
    db.execute("update public.jobs set correlation_id = %s where id = %s",
               (CID, job_id))
    dbmod.record_attempt(
        db, lease_id=lease, machine_id=machine, job_id=job_id, task_id="trial-000",
    )
    assert _attempt_correlation(db, lease) is None


# ---------------------------------------------------------------------------
# sandbox_sessions: the writer never mints
# ---------------------------------------------------------------------------


def test_create_session_stores_the_id_it_is_given(db):
    owner = _user(db)
    row = ss.create_session(
        db, owner_id=owner, pool_id=_pool(db, owner),
        training_job_id=_job(db, owner), region="ap-southeast-1",
        template="t", correlation_id=CID,
    )
    assert str(row["correlation_id"]) == CID
    assert _session_correlation(db, str(row["id"])) == CID


def test_create_session_given_none_writes_null(db):
    """The writer is a writer. D-2's "never mint on write when none was
    supplied" is this function, and `start_session` above it is where the
    decision to mint is allowed to live."""
    owner = _user(db)
    row = ss.create_session(
        db, owner_id=owner, pool_id=_pool(db, owner),
        training_job_id=_job(db, owner), region="ap-southeast-1", template="t",
    )
    assert row["correlation_id"] is None
    assert _session_correlation(db, str(row["id"])) is None


def test_the_correlation_id_is_on_the_owner_view_and_not_on_the_public_one():
    """It is our value, not a submitter's, so it fails no provenance test —
    but a public evidence page has no use for an internal trace id, and every
    id published to a link-holder is one more thing to correlate a stranger
    with. The owner sees the whole row; the share page sees the narrowed one.
    """
    assert "correlation_id" in ss.SESSION_COLUMNS
    assert "correlation_id" not in ss.SESSION_SHARE_COLUMNS


def test_a_session_refuses_a_correlation_id_that_is_not_one(db):
    owner = _user(db)
    with pytest.raises(ValueError):
        ss.create_session(
            db, owner_id=owner, pool_id=_pool(db, owner),
            training_job_id=_job(db, owner), region="ap-southeast-1",
            template="t", correlation_id="phongs-macbook.local",
        )


# ---------------------------------------------------------------------------
# The edge, and the hibernated window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_session_inherits_the_correlation_id_of_its_training_job(db):
    """`start_session` already authorises the training job for this owner and
    holds the row. If that row carries a thread, this session joins it rather
    than starting a second one for the same work."""
    world = _world(db)
    db.execute("update public.jobs set correlation_id = %s where id = %s",
               (CID, world.job))
    session_id = await _start(world)
    assert _session_correlation(db, session_id) == CID


@pytest.mark.asyncio
async def test_a_session_mints_when_nothing_upstream_has_a_thread(db):
    """D-1 names a sandbox session as one of the three edges. When the job it
    hangs off carries no id, this session IS the outermost user-initiated
    thing, and minting here relates nothing that was not already related."""
    world = _world(db)
    assert _job_correlation(db, world.job) is None
    minted = _session_correlation(db, await _start(world))
    assert minted is not None
    assert obs.correlation_id_or_none(minted) == minted


@pytest.mark.asyncio
async def test_minting_at_the_edge_never_writes_back_onto_the_training_job(db):
    """The session's thread is the session's. Stamping it onto the job would
    make every later attempt on that job inherit an id minted by an unrelated
    evaluation — a thread that reads backwards."""
    world = _world(db)
    await _start(world)
    assert _job_correlation(db, world.job) is None


@pytest.mark.asyncio
async def test_an_explicit_correlation_id_reaches_the_session_row(db):
    """The wiring a route will use once `app.py` can be edited: the id is
    minted at the request and handed down, and nothing below invents one."""
    world = _world(db)
    session_id = await _start(world, correlation_id=CID)
    assert _session_correlation(db, session_id) == CID


@pytest.mark.asyncio
async def test_the_correlation_id_survives_the_hibernation(db):
    """The whole of D-4 in one assertion.

    `on_model_ready` is handed a session id and a connection and nothing else
    — which is exactly what a redeployed API has on the far side of a deep
    hibernation that destroyed the instance and outlived the process that
    minted the id. The thread is still there because it is a column, and the
    ids at both ends of the pause are the same one.
    """
    world = _world(db)
    db.execute("update public.jobs set correlation_id = %s where id = %s",
               (CID, world.job))
    session_id = await _start(world)
    before = _session_correlation(db, session_id)

    await _wake(world, session_id)

    row = db.execute(
        "select correlation_id, state, external_sandbox_id"
        "  from public.sandbox_sessions where id = %s",
        (session_id,),
    ).fetchone()
    assert before == CID
    assert str(row["correlation_id"]) == CID
    # TERMINATED, not SUCCEEDED: the evaluation succeeded and then
    # `cleanup_session` killed the sandbox that was still billing. The state
    # moved twice across the pause and the thread did not move at all, which
    # is the property.
    assert row["state"] == "TERMINATED"


@pytest.mark.asyncio
async def test_the_hibernated_window_is_logged_with_the_whole_chain(db, caplog):
    """"Reading the logs" is the thing this exists for. The wake emits one
    structured line carrying the correlation id, the session id and the
    sandbox id together — the join a human would otherwise do by hand across
    five queries, on the one path where the process that could have held the
    thread in memory no longer exists.
    """
    import json
    import logging

    world = _world(db)
    db.execute("update public.jobs set correlation_id = %s where id = %s",
               (CID, world.job))
    session_id = await _start(world)

    with caplog.at_level(logging.INFO, logger=orch.__name__):
        await _wake(world, session_id)

    lines = [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == orch.__name__ and r.getMessage().startswith("{")
    ]
    resumed = [line for line in lines if line.get("correlation_id") == CID]
    assert resumed, f"no chained line on the wake path: {lines}"
    assert any(
        line.get("session_id") == session_id and line.get("sandbox_id")
        for line in resumed
    )
