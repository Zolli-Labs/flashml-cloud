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
from flashml_cloud_api import verify

from test_jobs_from_repo import (
    _new_user,
    db,  # noqa: F401 - fixture
)

NAN = float("nan")
INF = float("inf")

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


# ---------------------------------------------------------------------------
# 1b. list_verifications_for_job — the read half of D-4
#
# record_verification had exactly one caller and zero readers before this.
# These tests are about the reader alone: the route (test_job_verifications
# _route.py) covers the viewer check and the JSON shape on top of it.
# ---------------------------------------------------------------------------


def test_a_job_with_no_verifications_reads_an_empty_list_not_a_fabricated_pass(db):
    """Absent is absent. A task nothing has ever checked must not read as a
    task that passed — the same rule §2 of 0006 states for `unknown`,
    applied one layer up: silence is not a verdict."""
    assert dbmod.list_verifications_for_job(db, _job()) == []


def test_the_reader_returns_a_seeded_row_verbatim(db):
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

    rows = dbmod.list_verifications_for_job(db, job)
    assert len(rows) == 1
    assert str(rows[0]["machine_id"]) == str(machine)
    assert rows[0]["job_id"] == job
    assert rows[0]["task_id"] == "task-000"
    assert rows[0]["slice"] == "timing"
    assert rows[0]["verdict"] == "flag"
    assert rows[0]["detail"] == {"observed_s": 0.3, "peer_median_s": 9.1}


def test_the_reader_preserves_unknown_it_never_becomes_pass(db):
    """The one property this file exists to pin at the reader, not only the
    writer: `unknown` must survive a round trip through the reader exactly
    as it was written, never silently upgraded to `pass`."""
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

    rows = dbmod.list_verifications_for_job(db, job)
    assert [r["verdict"] for r in rows] == ["unknown"]


def test_the_reader_is_scoped_to_one_job(db):
    machine = _enrol(db)
    job, other = _job(), _job()
    dbmod.record_verification(
        db, machine_id=machine, job_id=job, task_id="task-000",
        slice_name="timing", verdict="pass", detail={},
    )
    dbmod.record_verification(
        db, machine_id=machine, job_id=other, task_id="task-000",
        slice_name="timing", verdict="flag", detail={},
    )

    rows = dbmod.list_verifications_for_job(db, job)
    assert len(rows) == 1
    assert rows[0]["job_id"] == job


def test_the_reader_orders_deterministically_by_task_then_slice(db):
    """No unique index backs this table (0006's header), so ordering must
    come from the query, not from insertion or wall-clock order. Written in
    the opposite order from how it must read back, so a query that merely
    preserved insertion order would fail this."""
    machine, job = _enrol(db), _job()
    for task_id, slice_name in (
        ("task-002", "timing"),
        ("task-000", "timing"),
        ("task-000", "evidence"),
    ):
        dbmod.record_verification(
            db, machine_id=machine, job_id=job, task_id=task_id,
            slice_name=slice_name, verdict="pass", detail={},
        )

    rows = dbmod.list_verifications_for_job(db, job)
    assert [(r["task_id"], r["slice"]) for r in rows] == [
        ("task-000", "evidence"),
        ("task-000", "timing"),
        ("task-002", "timing"),
    ]


def test_the_reader_returns_a_null_machine_id_as_none(db):
    """A redundancy finding names no machine (§8.5) — the reader must not
    invent one on the way out."""
    job = _job()
    dbmod.record_verification(
        db, machine_id=None, job_id=job, task_id="shard-003",
        slice_name="redundancy", verdict="flag",
        detail={"machines": ["a", "b"]},
    )

    rows = dbmod.list_verifications_for_job(db, job)
    assert rows[0]["machine_id"] is None


def test_the_reader_returns_every_slice_for_a_task_not_just_one(db):
    machine, job = _enrol(db), _job()
    for slice_name, verdict in (("timing", "pass"), ("evidence", "flag")):
        dbmod.record_verification(
            db, machine_id=machine, job_id=job, task_id="task-000",
            slice_name=slice_name, verdict=verdict, detail={},
        )

    rows = dbmod.list_verifications_for_job(db, job)
    assert {(r["slice"], r["verdict"]) for r in rows} == {
        ("timing", "pass"), ("evidence", "flag")
    }


# ---------------------------------------------------------------------------
# 2. timing_verdict — slice 1, and the wrong-`pass` paths it must not take
#
# Peers are OTHER machines' `duration_s` on the same job. The function is
# pure: given a peer sample and one observation it returns a verdict and
# what it saw. Every test below that ends in `unknown` is a test that a
# particular kind of ignorance did not get written down as `pass`.
# ---------------------------------------------------------------------------

BASELINE = [8.0, 9.0, 10.0]      # median 9.0 -> floor 1.8 at floor_ratio 0.2


def test_an_ordinary_duration_against_a_real_baseline_passes():
    verdict, detail = verify.timing_verdict(BASELINE, 9.4)
    assert verdict == "pass"
    assert detail["peer_median_s"] == 9.0


def test_a_fast_but_honest_machine_at_half_the_median_passes():
    """The false positive this slice must not produce.

    A desktop finishing in half the time a laptop takes is the fleet working
    as intended — the observed range on real work is 5.5s to 37.5s, a 7x
    spread, and a floor anywhere near the median would flag the fast half of
    an honest fleet.
    """
    assert verify.timing_verdict(BASELINE, 4.5)[0] == "pass"


def test_an_instant_return_at_a_twentieth_of_the_median_is_flagged():
    """The attack: a result committed 0.45s after the claim is not a fast
    machine, it is a machine that did not download the input."""
    verdict, detail = verify.timing_verdict(BASELINE, 0.45)
    assert verdict == "flag"
    assert detail["floor_s"] == 1.8


def test_exactly_at_the_floor_is_not_a_flag():
    """`<` and not `<=`, pinned: the boundary belongs to the machine."""
    assert verify.timing_verdict(BASELINE, 9.0 * 0.2)[0] == "pass"


def test_two_peers_are_not_a_baseline():
    """One sample short of `min_peers` and the answer is `unknown`, however
    suspicious the observation looks. This is the rule that costs the most
    signal and is worth every bit of it."""
    verdict, detail = verify.timing_verdict([9.0, 9.0], 0.01)
    assert verdict == "unknown"
    assert detail["reason"] == "too_few_peers"


def test_no_peers_at_all_is_unknown_never_pass():
    """A first-of-its-kind task has no distribution to compare against
    (§8.8). A cold-start fleet verifies nothing by timing, and must say so."""
    assert verify.timing_verdict([], 0.01)[0] == "unknown"
    assert verify.timing_verdict([], 9.0)[0] == "unknown"


def test_peers_that_are_all_zero_are_no_baseline_at_all():
    """The wrong-`pass` path the naive formula takes.

    `observed < median(peers) * floor_ratio` with a peer median of 0.0 is
    `0.01 < 0.0`, which is False, which reads as `pass` — so a job on which
    every other machine also returned instantly would certify the next liar
    as honest. A distribution of zeros is not a distribution.
    """
    verdict, detail = verify.timing_verdict([0.0, 0.0, 0.0, 0.0], 0.01)
    assert verdict == "unknown"
    assert verdict != "pass"
    assert detail["peers"] == 0


def test_non_finite_peers_are_no_baseline_either():
    """`statistics.median` of a list containing NaN returns whichever value
    the sort happened to leave in the middle — a number with no meaning that
    would then be compared against as if it had one."""
    for junk in ([NAN] * 4, [INF] * 4, [NAN, INF, NAN, INF]):
        assert verify.timing_verdict(junk, 0.01)[0] == "unknown"


def test_unusable_peers_do_not_count_toward_the_baseline():
    """Four peer rows, two usable — that is two peers, not four. Counting
    the unusable ones would clear the `min_peers` gate on a sample that
    cannot support a median."""
    verdict, detail = verify.timing_verdict([9.0, 9.0, NAN, 0.0], 0.01)
    assert verdict == "unknown"
    assert detail["peers"] == 2


def test_a_zero_duration_is_flagged_not_a_crash():
    verdict, detail = verify.timing_verdict(BASELINE, 0.0)
    assert verdict == "flag"
    assert detail["reason"] == "non_positive_observed"


def test_a_negative_duration_is_flagged_not_a_crash():
    assert verify.timing_verdict(BASELINE, -3.0)[0] == "flag"


def test_a_zero_duration_is_flagged_even_with_no_peers():
    """Ordering decision, pinned because both orderings are defensible.

    A non-positive elapsed is impossible *without reference to any peer*:
    both timestamps are the API's own, and no machine downloads an input,
    computes and uploads a result in zero seconds. So the impossibility
    check runs before the baseline gate. It cannot produce a wrong `pass`
    either way; it can only turn an `unknown` into a `flag`, and nothing is
    enforced from a flag.
    """
    assert verify.timing_verdict([], 0.0)[0] == "flag"


def test_an_observation_that_is_not_a_finite_number_is_unknown():
    """NaN loses every comparison, so `NaN < floor` is False and the naive
    formula answers `pass` — certifying a machine on a duration we never
    actually learned."""
    for junk in (NAN, INF, -INF):
        verdict, detail = verify.timing_verdict(BASELINE, junk)
        assert verdict == "unknown", junk
        assert detail["reason"] == "no_observation"


def test_the_detail_records_what_the_check_actually_saw():
    """A verdict nobody can argue with is not evidence. The row has to carry
    enough to re-derive it: the observation, the baseline, how many samples
    it rested on, and the thresholds in force at the time — which are
    tunable, so a row read a year later must not assume today's values."""
    _, detail = verify.timing_verdict(BASELINE, 0.45)
    assert detail == {
        "observed_s": 0.45,
        "peer_median_s": 9.0,
        "peers": 3,
        "floor_s": 1.8,
        "floor_ratio": 0.2,
        "min_peers": 3,
    }


def test_a_floor_ratio_that_disables_the_check_is_refused():
    """`floor_ratio=0` makes the floor 0, which nothing can fall below, so
    every observation would be recorded as `pass`. A verifier misconfigured
    into certifying everything is worse than one that is switched off, so
    this raises rather than returning a comfortable answer."""
    for bad in (0.0, -0.1):
        with pytest.raises(ValueError):
            verify.timing_verdict(BASELINE, 0.45, floor_ratio=bad)


def test_a_floor_ratio_above_one_is_refused():
    """A floor above the peer median flags the median machine itself."""
    with pytest.raises(ValueError):
        verify.timing_verdict(BASELINE, 9.0, floor_ratio=1.5)


def test_min_peers_below_one_is_refused():
    """`min_peers=0` would take the median of an empty sample."""
    with pytest.raises(ValueError):
        verify.timing_verdict(BASELINE, 9.0, min_peers=0)


def test_the_thresholds_are_tunable_and_the_verdict_follows_them():
    strict = verify.timing_verdict(BASELINE, 3.0, floor_ratio=0.5)
    assert strict[0] == "flag"
    assert verify.timing_verdict(BASELINE, 3.0, floor_ratio=0.2)[0] == "pass"
    assert verify.timing_verdict([9.0, 9.0], 9.0, min_peers=2)[0] == "pass"


def test_the_verdict_is_only_ever_one_of_the_three_the_schema_allows():
    """`record_verification` hands the verdict straight to a check
    constraint. A fourth string here is an exception on the credit path."""
    observations = [0.0, -1.0, 0.01, 1.8, 4.5, 9.0, 1e9, NAN, INF]
    peer_sets = [[], [9.0], [9.0, 9.0], BASELINE, [0.0] * 4, [NAN] * 4]
    for peers in peer_sets:
        for observed in observations:
            verdict, detail = verify.timing_verdict(peers, observed)
            assert verdict in {"pass", "flag", "unknown"}, (peers, observed)
            assert isinstance(detail, dict)


# ---------------------------------------------------------------------------
# 3. who counts as a peer
# ---------------------------------------------------------------------------


def _credit(db, *, machine_id: str, job_id: str, task_id: str, duration):
    with db.cursor() as cur:
        cur.execute(
            "insert into public.contributions"
            "            (machine_id, job_id, task_id, duration_s)"
            "     values (%s, %s, %s, %s)",
            (machine_id, job_id, task_id, duration),
        )


def test_a_machines_own_history_is_not_its_own_peer_group(db):
    """The decision this slice turns on.

    Counting a machine's own past durations as peers hands a consistently
    fast liar its own baseline: return in 0.3s often enough and 0.3s becomes
    the median it is measured against, so it passes forever — and the first
    honest machine to join is the one that looks anomalous. Worse, a machine
    that has worked a job alone would be compared only against itself, which
    always passes; that is "nobody has ever checked" recorded as `pass`.
    """
    mine, job = _enrol(db), _job()
    _credit(db, machine_id=mine, job_id=job, task_id="task-000", duration=0.3)
    _credit(db, machine_id=mine, job_id=job, task_id="task-001", duration=0.3)
    _credit(db, machine_id=mine, job_id=job, task_id="task-002", duration=0.3)

    assert dbmod.peer_task_durations(db, job_id=job, machine_id=mine) == []


def test_peers_are_the_other_machines_on_the_same_job(db):
    mine, job = _enrol(db), _job()
    for i, seconds in enumerate((8.0, 9.0, 10.0)):
        _credit(db, machine_id=_enrol(db), job_id=job,
                task_id=f"task-{i:03d}", duration=seconds)
    _credit(db, machine_id=mine, job_id=job, task_id="task-003", duration=0.3)

    peers = dbmod.peer_task_durations(db, job_id=job, machine_id=mine)
    assert sorted(peers) == [8.0, 9.0, 10.0]
    # Floats, not Decimals: `duration_s` is numeric and psycopg hands back
    # Decimal, which `statistics.median` would happily mix with the floats
    # the verdict thresholds are computed in.
    assert all(isinstance(p, float) for p in peers)


def test_a_peer_row_with_no_recorded_duration_is_not_a_peer(db):
    """`fedavg.on_round` credits from the coordinator's task view, which
    reports no duration, so it writes `duration_s = null`. Those rows are
    real contributions and useless as timing evidence — and counting them
    toward `min_peers` would clear the baseline gate on a sample of
    nothing."""
    mine, job = _enrol(db), _job()
    for i in range(3):
        _credit(db, machine_id=_enrol(db), job_id=job,
                task_id=f"task-{i:03d}", duration=None)

    assert dbmod.peer_task_durations(db, job_id=job, machine_id=mine) == []


def test_peers_never_come_from_another_job(db):
    """Task cost varies by orders of magnitude between jobs, which is the
    whole reason the floor is a fraction of a peer median rather than a
    constant. A peer group that crossed jobs would be comparing a sweep
    shard against a federated round."""
    mine, job, other = _enrol(db), _job(), _job()
    _credit(db, machine_id=_enrol(db), job_id=other, task_id="task-000",
            duration=9.0)

    assert dbmod.peer_task_durations(db, job_id=job, machine_id=mine) == []
