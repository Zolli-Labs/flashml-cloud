"""The verification ledger: what the API believes about work it was told
happened, and — just as important — what it refuses to claim it believes.

Design: `docs/superpowers/specs/2026-08-03-result-verification-design.md`.

Two rules dominate every test in this file.

**Nothing is enforced.** A verdict is a row and nothing else. No lease is
refused, no credit withheld, no machine quarantined. With a fleet this size
a false positive costs a volunteer their machine while a false negative
costs one undeserved credit, and that asymmetry only points one way (§5).

**`unknown` is never stored or returned as `pass`.** Too few peers, no
peers, an unusable peer distribution, a duration we never learned — all
`unknown`. Absence of evidence is not evidence of honesty, and every path
below that could plausibly leak a wrong `pass` has a test of its own.

Node ids are namespaced per run: `machines.node_id` is globally unique, so
two tests enrolling `node-0` would collide on the schema rather than on
anything under test.
"""
from __future__ import annotations

import uuid

import psycopg
import pytest

from flashml_cloud_api import db as dbmod

from test_jobs_from_repo import (
    _new_user,
    db,  # noqa: F401 - fixture
)

RUN_MARKER = uuid.uuid4().hex[:8]


def _node_id(tag: str) -> str:
    return f"node-{RUN_MARKER}-{tag}-{uuid.uuid4().hex[:6]}"


def _enrol(db, node_id: str | None = None) -> str:
    owner = _new_user(db)
    return dbmod.insert_machine(
        db,
        owner_id=owner,
        node_id=node_id or _node_id("v"),
        name="laptop",
        platform="linux",
    )


def _job() -> str:
    return f"cjob-{uuid.uuid4().hex[:10]}"


def _rows(db, job_id: str) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "select machine_id, job_id, task_id, slice, verdict, detail,"
            "       created_at"
            "  from public.verifications where job_id = %s"
            " order by created_at, task_id",
            (job_id,),
        )
        return list(cur.fetchall())


# ---------------------------------------------------------------------------
# 1. record_verification against real Postgres
# ---------------------------------------------------------------------------


def test_a_verdict_is_written_exactly_as_given(db):
    machine, job = _enrol(db), _job()

    dbmod.record_verification(
        db,
        machine_id=machine,
        job_id=job,
        task_id="task-000",
        slice_name="timing",
        verdict="flag",
        detail={"observed_s": 0.3, "peer_median_s": 9.1},
    )

    rows = _rows(db, job)
    assert len(rows) == 1
    assert str(rows[0]["machine_id"]) == str(machine)
    assert rows[0]["task_id"] == "task-000"
    assert rows[0]["slice"] == "timing"
    assert rows[0]["verdict"] == "flag"
    assert rows[0]["detail"] == {"observed_s": 0.3, "peer_median_s": 9.1}


def test_an_unknown_verdict_is_stored_as_unknown(db):
    """The single most important row-level property in this file.

    Storing `unknown` as `pass` would turn "we could not tell" into "we
    checked and it was fine" — for a first-of-its-kind task, a cold-start
    fleet, or a machine nobody else has ever shared a job with. That is the
    error §5 names as the one most likely to creep in.
    """
    machine, job = _enrol(db), _job()

    dbmod.record_verification(
        db,
        machine_id=machine,
        job_id=job,
        task_id="task-000",
        slice_name="timing",
        verdict="unknown",
        detail={"reason": "too_few_peers", "peers": 1},
    )

    rows = _rows(db, job)
    assert [r["verdict"] for r in rows] == ["unknown"]


def test_a_verdict_the_schema_does_not_know_is_refused_by_the_database(db):
    """The check constraint, tested rather than trusted.

    A caller that invented `ok`, `suspicious` or `""` must not be able to
    write it: a reader counting `verdict = 'pass'` would silently
    under-report, and one counting "not flag" would silently over-report.
    """
    machine, job = _enrol(db), _job()

    for bogus in ("ok", "PASS", "suspicious", ""):
        with pytest.raises(psycopg.errors.CheckViolation):
            dbmod.record_verification(
                db,
                machine_id=machine,
                job_id=job,
                task_id="task-000",
                slice_name="timing",
                verdict=bogus,
                detail={},
            )

    assert _rows(db, job) == []


def test_a_slice_the_schema_does_not_know_is_refused_by_the_database(db):
    machine, job = _enrol(db), _job()

    with pytest.raises(psycopg.errors.CheckViolation):
        dbmod.record_verification(
            db,
            machine_id=machine,
            job_id=job,
            task_id="task-000",
            slice_name="vibes",
            verdict="pass",
            detail={},
        )

    assert _rows(db, job) == []


def test_a_verdict_can_name_no_machine_at_all(db):
    """A redundancy mismatch is about a PAIR, and §8.5 is explicit that it
    does not name the liar. A row forced to blame one of the two would be a
    fabricated accusation; `machine_id is null` is the honest shape."""
    job = _job()

    dbmod.record_verification(
        db,
        machine_id=None,
        job_id=job,
        task_id="shard-003",
        slice_name="redundancy",
        verdict="flag",
        detail={"machines": ["a", "b"], "note": "mismatch names neither"},
    )

    rows = _rows(db, job)
    assert len(rows) == 1
    assert rows[0]["machine_id"] is None


def test_detail_defaults_to_an_empty_object_never_null(db):
    """Same reasoning as `job_rounds.clipped` in 0005: every row has an
    answer to "what did the check see?", and a nullable column would make
    every reader distinguish two kinds of nothing."""
    machine, job = _enrol(db), _job()

    dbmod.record_verification(
        db,
        machine_id=machine,
        job_id=job,
        task_id="task-000",
        slice_name="evidence",
        verdict="unknown",
        detail=None,
    )

    assert _rows(db, job)[0]["detail"] == {}


def test_deleting_a_machine_takes_its_verdicts_with_it(db):
    """A revoked host's rows do not outlive the machine they accuse."""
    machine, job = _enrol(db), _job()
    dbmod.record_verification(
        db,
        machine_id=machine,
        job_id=job,
        task_id="task-000",
        slice_name="timing",
        verdict="pass",
        detail={},
    )
    assert len(_rows(db, job)) == 1

    with db.cursor() as cur:
        cur.execute("delete from public.machines where id = %s", (machine,))

    assert _rows(db, job) == []


def test_two_verdicts_for_one_task_both_survive(db):
    """Three slices judge the same task independently and each gets a row.

    Deliberately NOT unique on (machine, job, task): a `timing` pass and an
    `evidence` flag about one task are two findings, and collapsing them
    would silently drop whichever arrived second.
    """
    machine, job = _enrol(db), _job()
    for slice_name, verdict in (("timing", "pass"), ("evidence", "flag")):
        dbmod.record_verification(
            db,
            machine_id=machine,
            job_id=job,
            task_id="task-000",
            slice_name=slice_name,
            verdict=verdict,
            detail={},
        )

    rows = _rows(db, job)
    assert {(r["slice"], r["verdict"]) for r in rows} == {
        ("timing", "pass"), ("evidence", "flag")
    }
