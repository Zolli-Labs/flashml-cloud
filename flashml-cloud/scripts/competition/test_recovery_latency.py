"""`recovery_latency.py` — the ledger arithmetic and its honesty, offline.

    flashml-cloud/apps/api/.venv/bin/python -m pytest \
        flashml-cloud/scripts/competition/test_recovery_latency.py -q

**No coordinator, no network, no paid API.** Every test here builds a
synthetic event stream in the exact wire shape of
``GET /v1alpha1/jobs/{id}/events`` — a list of `Event` models with
``{type, timestamp, source, message, data:{task_id, node_id}}`` — and asserts
on pure functions. Nothing in this file enters `fetch_events`.

What is pinned:

* **The four intervals**, on a single death and on two deaths of one task.
* **Absence is never zero.** An expiry with no reclaim yields ``None`` with a
  named reason, not ``0.0``; so does a resume that never commits anything.
* **Order comes from the ledger, not the file.** A shuffled input produces the
  same numbers, whether it carries ``seq`` or only timestamps.
* **Tasks that never died are excluded and counted**, so a p50 cannot be
  diluted by tasks that had nothing to recover from.
* **Rates are refused, not zeroed.** A missing venue, a null rate and a
  non-numeric rate all end as ``None`` with a reason; a non-hibernating
  venue's cost-of-wait is a real, explained ``0.0``.
* **The venue mirror does not drift** from `apps/api/.../router/venues.py`.
* **The markdown carries the labels** — every table names its kind, and the
  header names the FC hibernation evidence this tool defers to.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "recovery_latency", HERE / "recovery_latency.py"
    )
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: `@dataclass` looks its class's module up in
    # `sys.modules` and raises an unrelated AttributeError if it is not there.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rl = _load()

T0 = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)


def ev(offset_s: float, etype: str, task_id: str | None = "task-000",
       node_id: str | None = None, seq: int | None = None) -> dict:
    """One ledger row in the wire shape, `offset_s` after T0.

    Mirrors `LeaseManager._emit`: `node_id` is present in `data` only when the
    event has one, exactly as the real emitter writes it.
    """
    data: dict = {}
    if task_id is not None:
        data["task_id"] = task_id
    if node_id is not None:
        data["node_id"] = node_id
    row = {
        "schema_version": "v1alpha1",
        "job_id": "job-1",
        "type": etype,
        "timestamp": (T0 + timedelta(seconds=offset_s)).isoformat(),
        "source": "flashruntime.leases",
        "message": etype,
        "data": data,
    }
    if seq is not None:
        row["seq"] = seq
    return row


# ---------------------------------------------------------------------------
# fixtures: the streams
# ---------------------------------------------------------------------------


def single_death() -> list[dict]:
    """One task, one machine death, a clean recovery.

    node-a claims at t=0, renews at 10 and 20, commits progress at 15, then
    vanishes. The lease expires at 60 (40 s lease + the sweeper). node-b
    claims at 62 and commits at 75.
    """
    return [
        ev(0, "TASK_CREATED"),
        ev(0, "LEASE_CLAIMED", node_id="node-a"),
        ev(10, "LEASE_RENEWED", node_id="node-a"),
        ev(15, "TASK_COMMIT_ACCEPTED", node_id="node-a"),
        ev(20, "LEASE_RENEWED", node_id="node-a"),
        ev(60, "LEASE_EXPIRED", node_id="node-a"),
        ev(60, "TASK_REQUEUED"),
        ev(62, "LEASE_CLAIMED", node_id="node-b"),
        ev(75, "TASK_COMMIT_ACCEPTED", node_id="node-b"),
        ev(80, "JOB_SUCCEEDED", task_id=None),
    ]


def double_death() -> list[dict]:
    """The same task loses TWO machines. Cycle 0 on node-a, cycle 1 on node-b,
    finally recovered by node-c."""
    return [
        ev(0, "LEASE_CLAIMED", node_id="node-a"),
        ev(10, "LEASE_RENEWED", node_id="node-a"),
        ev(50, "LEASE_EXPIRED", node_id="node-a"),
        ev(54, "LEASE_CLAIMED", node_id="node-b"),
        ev(60, "TASK_COMMIT_ACCEPTED", node_id="node-b"),
        ev(64, "LEASE_RENEWED", node_id="node-b"),
        ev(100, "LEASE_EXPIRED", node_id="node-b"),
        ev(103, "LEASE_CLAIMED", node_id="node-c"),
        ev(120, "TASK_COMMIT_ACCEPTED", node_id="node-c"),
    ]


def expiry_with_no_reclaim() -> list[dict]:
    """The attempt budget ran out: the lease expired and nobody ever claimed
    it again. There is no wake, so there is no wake latency."""
    return [
        ev(0, "LEASE_CLAIMED", node_id="node-a"),
        ev(10, "LEASE_RENEWED", node_id="node-a"),
        ev(50, "LEASE_EXPIRED", node_id="node-a"),
        ev(50, "TASK_REQUEUED"),
        ev(51, "JOB_FAILED", task_id=None),
    ]


def _analyse(rows, kill_at=None):
    events, warnings = rl.parse_events(rows)
    cycles, coverage = rl.analyse(events, kill_at=kill_at)
    return cycles, coverage, warnings


# ---------------------------------------------------------------------------
# the four intervals
# ---------------------------------------------------------------------------


def test_single_death_yields_all_four_intervals_derived():
    cycles, coverage, warnings = _analyse(single_death())

    assert warnings == []
    assert coverage.death_cycles == 1
    (c,) = cycles

    assert c.task_id == "task-000"
    assert c.cycle == 0
    assert c.died_node == "node-a"
    assert c.resumed_node == "node-b"

    # detection: LEASE_EXPIRED(60) − last LEASE_RENEWED by node-a(20)
    assert c.detection_s.value == 40.0
    assert c.detection_s.kind == "derived"
    assert "LEASE_EXPIRED" in c.detection_s.source

    # reclaim — the wake: LEASE_CLAIMED(62) − LEASE_EXPIRED(60)
    assert c.reclaim_s.value == 2.0
    assert c.reclaim_s.kind == "derived"

    # resume → demonstrable progress: TASK_COMMIT_ACCEPTED(75) − claim(62)
    assert c.resume_to_progress_s.value == 13.0
    assert c.progress_type == "TASK_COMMIT_ACCEPTED"

    # recomputed: last proof of life(20) − last commit on the dying attempt(15)
    assert c.recomputed_s.value == 5.0
    assert "event gap" in c.recomputed_s.source


def test_recomputed_falls_back_to_the_attempt_start_when_nothing_committed():
    rows = [
        ev(0, "LEASE_CLAIMED", node_id="node-a"),
        ev(30, "LEASE_RENEWED", node_id="node-a"),
        ev(70, "LEASE_EXPIRED", node_id="node-a"),
        ev(72, "LEASE_CLAIMED", node_id="node-b"),
    ]
    (c,), _, _ = _analyse(rows)
    # Nothing was ever committed, so the whole attempt is lost: 30 − 0.
    assert c.recomputed_s.value == 30.0
    assert "nothing was ever committed" in c.recomputed_s.source


def test_double_death_on_one_task_is_two_numbered_cycles():
    cycles, coverage, _ = _analyse(double_death())

    assert coverage.death_cycles == 2
    assert coverage.tasks_seen == 1
    assert [c.cycle for c in cycles] == [0, 1]

    first, second = cycles
    assert (first.died_node, first.resumed_node) == ("node-a", "node-b")
    assert (second.died_node, second.resumed_node) == ("node-b", "node-c")

    assert first.detection_s.value == 40.0     # 50 − 10
    assert first.reclaim_s.value == 4.0        # 54 − 50
    assert second.detection_s.value == 36.0    # 100 − 64
    assert second.reclaim_s.value == 3.0       # 103 − 100
    assert second.resume_to_progress_s.value == 17.0   # 120 − 103

    # Cycle 1's recomputed work anchors on node-b's OWN commit at 60, not on
    # node-a's — the search stops at the dying attempt's LEASE_CLAIMED.
    assert second.recomputed_s.value == 4.0    # 64 − 60


def test_expiry_with_no_reclaim_is_none_with_a_named_reason_not_zero():
    (c,), coverage, _ = _analyse(expiry_with_no_reclaim())

    assert coverage.death_cycles == 1
    assert c.detection_s.value == 40.0        # the death WAS detected
    assert c.reclaim_s.value is None
    assert c.reclaim_s.kind == "unavailable"
    assert "no LEASE_CLAIMED follows" in c.reclaim_s.reason
    assert "not zero" in c.reclaim_s.reason

    # and the interval that depends on the reclaim is absent for that reason
    assert c.resume_to_progress_s.value is None
    assert "never reclaimed" in c.resume_to_progress_s.reason


def test_missing_checkpoint_events_leave_resume_to_progress_none_and_say_why():
    rows = [
        ev(0, "LEASE_CLAIMED", node_id="node-a"),
        ev(20, "LEASE_RENEWED", node_id="node-a"),
        ev(60, "LEASE_EXPIRED", node_id="node-a"),
        ev(62, "LEASE_CLAIMED", node_id="node-b"),
        # node-b is working, but nothing it commits shows up in THIS feed.
    ]
    (c,), _, _ = _analyse(rows)

    assert c.reclaim_s.value == 2.0
    assert c.resume_to_progress_s.value is None
    # The composite-scope gap is named, so a reader does not read this as a
    # runtime fault: CHECKPOINT_MANIFEST_COMMITTED is emitted under
    # "<job_id>::<task_id>" and never lands in a job's own feed.
    assert "composite scope" in c.resume_to_progress_s.reason


def test_manifest_committed_counts_as_progress_when_the_feed_carries_it():
    rows = [
        ev(0, "LEASE_CLAIMED", node_id="node-a"),
        ev(20, "LEASE_RENEWED", node_id="node-a"),
        ev(60, "LEASE_EXPIRED", node_id="node-a"),
        ev(62, "LEASE_CLAIMED", node_id="node-b"),
        ev(70, "CHECKPOINT_MANIFEST_COMMITTED", node_id="node-b"),
    ]
    (c,), _, _ = _analyse(rows)
    assert c.progress_type == "CHECKPOINT_MANIFEST_COMMITTED"
    assert c.resume_to_progress_s.value == 8.0


# ---------------------------------------------------------------------------
# ordering
# ---------------------------------------------------------------------------


def test_out_of_order_input_sorts_by_seq_when_the_payload_carries_it():
    rows = [
        ev(60, "LEASE_EXPIRED", node_id="node-a", seq=4),
        ev(0, "LEASE_CLAIMED", node_id="node-a", seq=1),
        ev(75, "TASK_COMMIT_ACCEPTED", node_id="node-b", seq=6),
        ev(20, "LEASE_RENEWED", node_id="node-a", seq=3),
        ev(62, "LEASE_CLAIMED", node_id="node-b", seq=5),
        ev(15, "TASK_COMMIT_ACCEPTED", node_id="node-a", seq=2),
    ]
    events, _ = rl.parse_events(rows)
    ordered, key = rl.order_events(events)

    assert key == "seq"
    assert [e.seq for e in ordered] == [1, 2, 3, 4, 5, 6]

    (c,), _, _ = _analyse(rows)
    assert (c.detection_s.value, c.reclaim_s.value) == (40.0, 2.0)
    assert c.resume_to_progress_s.value == 13.0


def test_out_of_order_input_sorts_by_timestamp_when_there_is_no_seq():
    """The HTTP wire shape carries no `seq` — `Event` in
    `protocol/v1alpha1.py` has no such field — so this is the path a captured
    response actually takes. A shuffled capture must still produce the same
    numbers as `test_single_death_yields_all_four_intervals_derived`."""
    rows = list(reversed(single_death()))
    events, _ = rl.parse_events(rows)
    _, key = rl.order_events(events)
    assert key == "timestamp"

    (c,), _, _ = _analyse(rows)
    assert c.detection_s.value == 40.0
    assert c.reclaim_s.value == 2.0
    assert c.resume_to_progress_s.value == 13.0
    assert c.recomputed_s.value == 5.0


def test_a_partial_seq_does_not_win_over_timestamps():
    """One row carrying `seq` while the rest do not is not a ledger order; it
    is a hand-edited file. Timestamps decide."""
    rows = single_death()
    rows[0]["seq"] = 99
    events, _ = rl.parse_events(rows)
    _, key = rl.order_events(events)
    assert key == "timestamp"


def test_unreadable_rows_are_dropped_and_reported_never_silently_ignored():
    rows = single_death() + [{"type": "LEASE_EXPIRED"}, "not-an-object", {"timestamp": "nope"}]
    events, warnings = rl.parse_events(rows)

    assert len(events) == len(single_death())
    assert len(warnings) == 1
    assert "3 event(s) dropped" in warnings[0]


def test_naive_and_z_suffixed_timestamps_both_parse_as_utc():
    aware = rl._parse_timestamp("2026-08-13T12:00:00+00:00")
    zulu = rl._parse_timestamp("2026-08-13T12:00:00Z")
    naive = rl._parse_timestamp("2026-08-13T12:00:00")
    assert aware == zulu == naive


# ---------------------------------------------------------------------------
# coverage: tasks that never died
# ---------------------------------------------------------------------------


def test_tasks_with_no_death_are_excluded_and_counted():
    rows = single_death() + [
        ev(0, "LEASE_CLAIMED", task_id="task-001", node_id="node-b"),
        ev(30, "TASK_COMMIT_ACCEPTED", task_id="task-001", node_id="node-b"),
        ev(0, "LEASE_CLAIMED", task_id="task-002", node_id="node-c"),
        ev(30, "TASK_COMMIT_ACCEPTED", task_id="task-002", node_id="node-c"),
    ]
    cycles, coverage, _ = _analyse(rows)

    assert coverage.tasks_seen == 3
    assert coverage.tasks_with_a_death == 1
    assert coverage.tasks_without_a_death == 2
    assert coverage.task_ids_without_a_death == ["task-001", "task-002"]
    # and none of them dilutes the aggregate
    assert rl.fleet_stats(cycles)["reclaim_s"].n == 1


# ---------------------------------------------------------------------------
# the external kill timestamp — the only `measured` label this tool emits
# ---------------------------------------------------------------------------


def test_kill_at_produces_measured_intervals_on_exactly_one_cycle():
    kill_at = T0 + timedelta(seconds=21)
    cycles, _, _ = _analyse(double_death(), kill_at=kill_at)
    first, second = cycles

    # The first expiry at or after the kill is the one that kill caused.
    assert first.kill_to_expire_s.value == 29.0     # 50 − 21
    assert first.kill_to_expire_s.kind == "measured"
    assert first.kill_to_progress_s.value == 39.0   # 60 − 21
    assert first.kill_to_progress_s.kind == "measured"

    # The second death happened later and was not that kill.
    assert second.kill_to_expire_s.value is None
    assert "names one death" in second.kill_to_expire_s.reason


def test_without_kill_at_nothing_is_labelled_measured():
    cycles, _, _ = _analyse(single_death())
    (c,) = cycles
    for name in rl.INTERVAL_FIELDS:
        metric = getattr(c, name)
        if metric.value is not None:
            assert metric.kind == "derived"
    assert c.kill_to_expire_s.value is None
    assert c.kill_to_progress_s.value is None


def test_kill_at_after_every_expiry_is_refused_with_a_reason():
    kill_at = T0 + timedelta(seconds=9999)
    (c,), _, _ = _analyse(single_death(), kill_at=kill_at)
    assert c.kill_to_expire_s.value is None
    assert "after every LEASE_EXPIRED" in c.kill_to_expire_s.reason


def test_parse_kill_at_takes_epoch_seconds_and_iso8601():
    """`run_local_recovery.sh` records `KILLED_AT=$(date +%s)`; both forms are
    accepted so the shell patch needs no portable `date` conversion."""
    epoch = rl.parse_kill_at("1786000000")
    assert epoch.tzinfo is not None
    assert rl.parse_kill_at("2026-08-13T12:00:00Z") == T0
    assert rl.parse_kill_at("2026-08-13T12:00:00") == T0
    # "2026" is a float, and must NOT be read as epoch 2026 (1970-01-01).
    with pytest.raises(Exception):
        rl.parse_kill_at("not-a-time")


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------


def test_absent_values_are_counted_not_imputed():
    rows = single_death() + [
        e | {"data": dict(e["data"], task_id="task-001")} if e.get("data") else e
        for e in expiry_with_no_reclaim()
    ]
    cycles, _, _ = _analyse(rows)
    stats = rl.fleet_stats(cycles)

    assert stats["detection_s"].n == 2
    assert stats["reclaim_s"].n == 1          # the unreclaimed cycle is not a 0
    assert stats["reclaim_s"].n_absent == 1
    assert stats["reclaim_s"].p50 == 2.0


def test_a_stat_with_no_values_is_unavailable_with_a_reason():
    (c,), _, _ = _analyse(expiry_with_no_reclaim())
    stat = rl.summarise([c], "reclaim_s")
    assert stat.n == 0
    assert stat.p50 is None
    assert stat.kind == "unavailable"
    assert stat.reason


def test_nearest_rank_is_not_interpolated():
    values = [1.0, 2.0, 3.0, 4.0]
    assert rl.nearest_rank(values, 0.50) == 2.0     # interpolation would say 2.5
    assert rl.nearest_rank(values, 0.90) == 4.0
    assert rl.nearest_rank([], 0.5) is None


def test_per_node_keeps_the_victim_and_rescuer_roles_apart():
    cycles, _, _ = _analyse(double_death())
    nodes = rl.per_node_stats(cycles)

    assert nodes["node-a"]["deaths_as_holder"] == 1
    assert nodes["node-a"]["reclaims_as_rescuer"] == 0
    assert nodes["node-b"]["deaths_as_holder"] == 1
    assert nodes["node-b"]["reclaims_as_rescuer"] == 1
    assert nodes["node-c"]["deaths_as_holder"] == 0
    assert nodes["node-c"]["as_rescuer"]["reclaim_s"]["p50"] == 3.0


def test_per_task_carries_one_row_per_task_with_its_cycle_numbers():
    cycles, _, _ = _analyse(double_death())
    tasks = rl.per_task_stats(cycles)
    assert tasks["task-000"]["deaths"] == 2
    assert tasks["task-000"]["cycles"] == [0, 1]
    assert tasks["task-000"]["intervals"]["reclaim_s"]["n"] == 2


# ---------------------------------------------------------------------------
# the cost layer
# ---------------------------------------------------------------------------


def _stats_for_costing():
    cycles, _, _ = _analyse(single_death())
    return rl.fleet_stats(cycles)


def test_a_non_hibernating_venue_waits_for_free_and_that_zero_is_explained():
    stats = _stats_for_costing()
    rate = rl.VenueRate(venue="runpod", usd_per_hour=0.34, hibernates=False,
                        source="supplied: rented_capacity.usd_per_hour")
    (row,) = rl.venue_costs(stats, [rate])

    assert row.cost_of_wait.value == 0.0
    assert row.cost_of_wait.kind == "derived"
    assert "LEAVES the fleet" in row.cost_of_wait.source

    # cost of death = (40 + 2 + 13 + 5) s at $0.34/hr
    assert row.death_s.value == 60.0
    assert row.cost_of_death.value == pytest.approx(0.34 * 60.0 / 3600.0, rel=1e-9)


def test_a_hibernating_venue_pays_the_hibernated_rate_for_its_wait():
    stats = _stats_for_costing()
    rate = rl.VenueRate(
        venue="fc-sandbox", usd_per_hour=0.05654376, hibernates=True,
        hibernated_usd_per_hour=0.00351553,
        source="measured: alibaba-hibernation-modes-20260813T041325Z.json",
    )
    (row,) = rl.venue_costs(stats, [rate])

    assert row.wait_s.value == 42.0          # detection 40 + reclaim 2
    assert row.cost_of_wait.value == pytest.approx(0.00351553 * 42.0 / 3600.0, rel=1e-9)
    assert row.active_rate.kind == "measured"
    assert row.hibernated_rate.kind == "measured"


def test_a_hibernating_venue_without_a_hibernated_rate_is_none_not_zero():
    stats = _stats_for_costing()
    rate = rl.VenueRate(venue="fc-gpu", usd_per_hour=2.0, hibernates=True)
    (row,) = rl.venue_costs(stats, [rate])

    assert row.cost_of_wait.value is None
    assert "no hibernated_usd_per_hour was supplied" in row.cost_of_wait.reason
    # the ACTIVE side is still priceable — one absent input does not void the row
    assert row.cost_of_death.value is not None


def test_a_venue_with_no_rate_is_unpriced_with_a_named_reason():
    stats = _stats_for_costing()
    rate = rl.VenueRate(venue="ecs-gpu", usd_per_hour=None, hibernates=False)
    (row,) = rl.venue_costs(stats, [rate])

    assert row.active_rate.value is None
    assert "never invents a rate" in row.active_rate.reason
    assert row.cost_of_death.value is None
    assert "unpriced venue" in row.cost_of_death.reason
    # …but the wait is still free, because that is a fact about the venue and
    # not a number the rate table was supposed to supply.
    assert row.cost_of_wait.value == 0.0


def test_a_null_rate_is_refused_not_zeroed():
    rates, warnings = rl.parse_rates({"venues": [
        {"venue": "runpod", "usd_per_hour": None, "hibernates": False},
    ]})
    (rate,) = rates
    assert rate.usd_per_hour is None
    assert warnings == []          # an explicit null is a legitimate statement


def test_a_non_numeric_rate_is_refused_loudly_and_never_coerced():
    rates, warnings = rl.parse_rates({"venues": [
        {"venue": "runpod", "usd_per_hour": "0.34"},
        {"venue": "ecs-gpu", "usd_per_hour": True},
    ]})
    assert [r.usd_per_hour for r in rates] == [None, None]
    assert len(warnings) == 2
    assert all("NOT as zero" in w for w in warnings)


def test_volunteer_hardware_prices_at_exactly_zero_with_its_own_source():
    stats = _stats_for_costing()
    (row,) = rl.venue_costs(stats, [rl.VenueRate(venue="owned", usd_per_hour=0.0,
                                                 hibernates=False, volunteer=True)])
    assert row.cost_of_wait.value == 0.0
    assert row.cost_of_death.value == 0.0
    assert row.cost_of_wait.source == "volunteer hardware, no hourly rate"
    assert row.cost_of_death.source == "volunteer hardware, no hourly rate"


def test_owned_is_volunteer_by_default_without_being_told():
    (row,) = rl.venue_costs(_stats_for_costing(),
                            [rl.VenueRate(venue="owned", usd_per_hour=None, hibernates=False)])
    assert row.cost_of_death.value == 0.0
    assert row.cost_of_death.source == "volunteer hardware, no hourly rate"


def test_a_missing_venue_in_the_rate_table_still_appears_unpriced():
    """A rate table that names only RunPod must not delete the other venues
    from the report — a venue nobody priced is a visible gap, not an absence."""
    supplied, _ = rl.parse_rates({"venues": [{"venue": "runpod", "usd_per_hour": 0.34}]})
    merged = rl.merge_rates(supplied, [])
    by = {r.venue: r for r in merged}

    assert set(by) == set(rl.DEFAULT_VENUE_HIBERNATES)
    assert by["runpod"].usd_per_hour == 0.34
    assert by["ecs-gpu"].usd_per_hour is None
    assert by["owned"].usd_per_hour == 0.0        # volunteer, not "unpriced"
    # `hibernates` is filled from the mirror when the table does not say.
    assert by["runpod"].hibernates is False
    assert by["fc-sandbox"].hibernates is True


def test_rates_can_be_given_as_a_plain_mapping():
    rates, warnings = rl.parse_rates({"runpod": {"usd_per_hour": 0.5}})
    assert warnings == []
    assert rates[0].venue == "runpod"
    assert rates[0].usd_per_hour == 0.5


def test_fc_rates_come_from_the_hibernation_evidence_not_from_a_guess():
    """The join between the two artifacts. `hibernation_modes_probe.py` already
    measured the FC pair; this reads it rather than restating it."""
    evidence = {"report": {"cost": {"per_state": {
        "active": {"rate_usd_per_hour": 0.05654376},
        "deep_hibernation": {"rate_usd_per_hour": 0.00351553},
    }}}}
    (rate,) = rl.rates_from_hibernation_evidence(
        evidence, "alibaba-hibernation-modes-20260813T041325Z.json"
    )
    assert rate.venue == "fc-sandbox"
    assert rate.usd_per_hour == 0.05654376
    assert rate.hibernated_usd_per_hour == 0.00351553
    assert rate.kind == "measured"
    assert "20260813T041325Z" in rate.source


def test_hibernation_evidence_without_rates_yields_nothing_rather_than_zeros():
    assert rl.rates_from_hibernation_evidence({}, "f.json") == []
    assert rl.rates_from_hibernation_evidence({"report": {"cost": {}}}, "f.json") == []


def test_an_empty_ledger_prices_nothing_and_says_so():
    cycles, coverage, _ = _analyse([])
    stats = rl.fleet_stats(cycles)
    rows = rl.venue_costs(stats, rl.default_rates())

    assert coverage.death_cycles == 0
    for row in rows:
        assert row.cost_of_death.value in (None, 0.0)   # 0.0 only for volunteer
        if row.venue not in rl.VOLUNTEER_VENUES:
            assert row.cost_of_death.value is None
            assert row.cost_of_death.reason


# ---------------------------------------------------------------------------
# the venue mirror
# ---------------------------------------------------------------------------


def test_the_venue_mirror_matches_the_api_table():
    """`DEFAULT_VENUE_HIBERNATES` is a copy of `Venue.hibernates` because this
    script must run under a bare `python3` in the e2e rehearsal, where
    `flashml_cloud_api` is not installed. A copy needs a drift guard, and this
    is it — run under the api venv, where the real table IS importable."""
    venues = pytest.importorskip(
        "flashml_cloud_api.router.venues",
        reason="run under apps/api/.venv to check the mirror against the real table",
    )
    real = {v.id: v.hibernates for v in venues.VENUES}
    assert rl.DEFAULT_VENUE_HIBERNATES == real


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _rendered(rows=None, kill_at=None, rates=None):
    cycles, coverage, warnings = _analyse(rows if rows is not None else single_death(),
                                          kill_at=kill_at)
    stats = rl.fleet_stats(cycles)
    venues = rl.venue_costs(stats, rates if rates is not None else rl.default_rates())
    inputs = {"events_source": "events.json",
              "kill_at": kill_at.isoformat() if kill_at else None}
    return rl.render_markdown(
        cycles, coverage, stats, venues, inputs, warnings,
        "alibaba-hibernation-modes-20260813T041325Z.json", "20260813T120000Z",
    )


def test_markdown_carries_the_kind_labels_and_every_interval_column():
    doc = _rendered()
    for column in ("detection_s", "reclaim_s", "resume_to_progress_s",
                   "recomputed_s", "kill_to_expire_s", "kill_to_progress_s"):
        assert column in doc
    assert "| Metric | n | absent | p50 | p90 | min | max | Unit | Kind |" in doc
    assert "derived" in doc
    assert "nearest-rank, not interpolated" in doc


def test_markdown_header_is_honest_about_what_it_does_and_does_not_cover():
    doc = _rendered()
    assert "What is derived" in doc
    assert "What needs an external clock" in doc
    # the FC evidence is CITED BY FILENAME, so the pair reads as one answer
    assert "alibaba-hibernation-modes-20260813T041325Z.json" in doc
    assert "1109 ms" in doc
    assert "93.78%" in doc
    assert "composite scope" in doc


def test_markdown_shows_a_blank_cell_with_its_reason_rather_than_a_zero():
    doc = _rendered()
    assert "### Why a cell is blank" in doc
    assert "never invents a rate" in doc
    # ecs-gpu has no rate, so its cost-of-death cell is blank, not 0.00000000
    ecs_row = next(line for line in doc.splitlines() if line.startswith("| ecs-gpu |"))
    assert "— _(none)_" in ecs_row


def test_markdown_reports_the_measured_row_when_a_kill_timestamp_is_given():
    doc = _rendered(kill_at=T0 + timedelta(seconds=21))
    assert "measured" in doc
    # kill(21) → expiry(60) = 39 s, and kill → progress(75) = 54 s
    assert "| 39 |" in doc
    assert "| 54 |" in doc


def test_markdown_renders_an_empty_ledger_without_pretending_anything():
    doc = _rendered(rows=[])
    assert "_(no death cycle in this ledger)_" in doc
    assert "0.00000000" not in doc.split("## Per venue")[0]


def test_the_json_report_round_trips_and_labels_every_metric():
    cycles, coverage, warnings = _analyse(single_death(), kill_at=T0 + timedelta(seconds=21))
    stats = rl.fleet_stats(cycles)
    venues = rl.venue_costs(stats, rl.default_rates())
    report = rl.build_report(
        cycles, coverage, stats, venues,
        {"events_source": "events.json", "kill_at": None}, warnings,
        "alibaba-hibernation-modes-20260813T041325Z.json", "20260813T120000Z",
    )
    restored = json.loads(json.dumps(report))

    assert restored["schema"] == "recovery_latency_v1"
    assert restored["hibernation_evidence_cited"].startswith("alibaba-hibernation-modes-")
    cycle = restored["cycles"][0]
    for name in rl.INTERVAL_FIELDS:
        entry = cycle[name]
        assert entry["unit"] == "s"
        assert entry["kind"] in ("measured", "derived", "unavailable")
        if entry["value"] is None:
            assert entry["kind"] == "unavailable" and entry["reason"]
        else:
            assert entry["source"]


def test_no_unavailable_metric_ever_carries_a_value():
    """The house rule, enforced across everything this module can produce."""
    cycles, _, _ = _analyse(double_death() + expiry_with_no_reclaim())
    stats = rl.fleet_stats(cycles)
    metrics = [getattr(c, n) for c in cycles for n in rl.INTERVAL_FIELDS]
    for row in rl.venue_costs(stats, rl.default_rates()):
        metrics += [row.active_rate, row.hibernated_rate, row.wait_s,
                    row.death_s, row.cost_of_wait, row.cost_of_death]
    for m in metrics:
        if m.kind == "unavailable":
            assert m.value is None and m.reason
        else:
            assert m.value is not None and m.source


# ---------------------------------------------------------------------------
# the CLI, in file mode only — nothing here touches the network
# ---------------------------------------------------------------------------


def test_cli_file_mode_writes_both_artifacts(tmp_path):
    events_file = tmp_path / "events.json"
    events_file.write_text(json.dumps(single_death()))
    out_dir = tmp_path / ".evidence"

    code = rl.main([
        "--events-json", str(events_file),
        "--kill-at", "1786000000",
        "--out-dir", str(out_dir),
        "--quiet",
    ])
    assert code == 0

    jsons = list(out_dir.glob("recovery-latency-*.json"))
    mds = list(out_dir.glob("recovery-latency-*.md"))
    assert len(jsons) == 1 and len(mds) == 1

    report = json.loads(jsons[0].read_text())
    assert report["coverage"]["death_cycles"] == 1
    assert report["inputs"]["kill_at"]
    assert report["venues"]


def test_cli_refuses_a_run_with_neither_a_file_nor_a_job(capsys):
    assert rl.main([]) == 2
    assert "file mode" in capsys.readouterr().err


def test_cli_refuses_a_bad_ledger_with_one_line_not_a_traceback(tmp_path, capsys):
    """`run_local_recovery.sh` calls this AFTER printing its own verdict. A
    traceback there reads as the rehearsal having failed, which it has not."""
    bad = tmp_path / "events.json"
    bad.write_text("not json")
    assert rl.main(["--events-json", str(bad), "--out-dir", str(tmp_path / "e")]) == 1
    err = capsys.readouterr().err
    assert "could not read the event ledger" in err
    assert "Traceback" not in err


def test_cli_refuses_a_missing_ledger_file(tmp_path, capsys):
    assert rl.main(["--events-json", str(tmp_path / "nope.json"),
                    "--out-dir", str(tmp_path / "e")]) == 1
    assert "could not read the event ledger" in capsys.readouterr().err


def test_cli_reads_a_rate_table_and_keeps_the_unpriced_venues(tmp_path):
    events_file = tmp_path / "events.json"
    events_file.write_text(json.dumps(single_death()))
    rates_file = tmp_path / "rates.json"
    rates_file.write_text(json.dumps({"venues": [
        {"venue": "runpod", "usd_per_hour": 0.34, "hibernates": False,
         "source": "supplied: rented_capacity.usd_per_hour for the demo pod"},
    ]}))
    out_dir = tmp_path / ".evidence"

    assert rl.main(["--events-json", str(events_file), "--rates", str(rates_file),
                    "--out-dir", str(out_dir), "--quiet"]) == 0

    report = json.loads(next(out_dir.glob("recovery-latency-*.json")).read_text())
    by = {v["venue"]: v for v in report["venues"]}
    assert by["runpod"]["cost_of_death_usd"]["value"] > 0
    assert by["runpod"]["cost_of_wait_usd"]["value"] == 0.0
    assert by["ecs-gpu"]["cost_of_death_usd"]["value"] is None
    assert by["ecs-gpu"]["cost_of_death_usd"]["reason"]


def test_cli_reads_stdin(tmp_path, monkeypatch, capsys):
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(single_death())))
    out_dir = tmp_path / ".evidence"
    assert rl.main(["--events-json", "-", "--out-dir", str(out_dir), "--quiet"]) == 0
    assert list(out_dir.glob("recovery-latency-*.json"))
