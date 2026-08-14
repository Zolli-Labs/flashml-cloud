"""The provider network reads: what a stranger sees, and what the numbers mean.

Four things are being pinned here, and each one is a specific way this feature
could ship a plausible lie:

* **ANONYMISATION.** The provider list is fleet-wide, so nearly every row a
  viewer loads belongs to somebody else. A machine's ``name`` is whatever its
  owner typed — routinely their own name — and its ``node_id`` is the handle
  enrolment matches on. Neither may leave the API for a machine the viewer does
  not own, and "the console does not render it" is not the same guarantee as
  "the API does not send it".

* **THE DENOMINATOR.** ``attempts.outcome IS NULL`` means in flight, or a row
  predating migration 0015 that nothing can classify. Counting one as a failure
  makes a machine's reliability fall *because it is busy* — the exact
  survivorship inversion 0015 exists to prevent — and the rate is
  ``metrics.acceptance_rates``' to compute, not this module's.

* **UPTIME.** Nothing writes a ``beats = 0`` row, so "hours observed" and
  "hours up" are the same count, and a percentage that divides by the rows it
  found reports 100% for every machine that has ever heartbeated once. The
  denominator is the WINDOW.

* **THE HEARTBEAT.** ``touch_machine_last_seen`` now writes two rows, and
  ``last_seen_at`` is what stands between a live rental and
  ``capacity.reconcile`` destroying it. The uptime bucket must never be able to
  cost it — including on a database where migration 0029 has not been applied.

Every read here is FLEET-WIDE by design, and this database is shared with the
rest of the suite. So nothing asserts an absolute provider count: rows are
found by id, and totals are asserted as deltas.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.types.json import Json

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import migrate
from flashml_cloud_api import network

from test_jobs_from_repo import (  # noqa: F401 - fixtures
    _new_user,
    db,
)
from test_migrate import AUTH_STUB, connected, scratch_database  # noqa: F401


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _machine(
    db,
    owner: str,
    *,
    name: str | None = "a laptop",
    seen_seconds_ago: float | None = 5,
    status: str = "active",
    capabilities: dict | None = None,
    sandbox_capable: bool = True,
    argv_capable: bool = False,
    unsandboxed_argv_capable: bool = False,
) -> str:
    machine_id = dbmod.insert_machine(
        db,
        owner_id=owner,
        node_id=f"fn-{uuid.uuid4().hex[:16]}",
        name=name,
        platform="linux",
    )
    with db.cursor() as cur:
        cur.execute(
            """
            update public.machines
               set status = %s,
                   last_seen_at = case when %s::interval is null then null
                                       else now() - %s::interval end,
                   capabilities = %s,
                   sandbox_capable = %s,
                   argv_capable = %s,
                   unsandboxed_argv_capable = %s
             where id = %s
            """,
            (
                status,
                None if seen_seconds_ago is None else timedelta(seconds=seen_seconds_ago),
                None if seen_seconds_ago is None else timedelta(seconds=seen_seconds_ago),
                Json(capabilities if capabilities is not None else {"cpu_cores": 8}),
                sandbox_capable,
                argv_capable,
                unsandboxed_argv_capable,
                machine_id,
            ),
        )
    return machine_id


def _node_id(db, machine_id: str) -> str:
    with db.cursor() as cur:
        cur.execute("select node_id from public.machines where id = %s", (machine_id,))
        return cur.fetchone()["node_id"]


def _attempt(
    db,
    machine_id: str,
    *,
    outcome: str | None,
    deadline_in_s: float | None = None,
    duration_s: float = 10.0,
    resolved_days_ago: float = 0.0,
) -> str:
    """One attempts row, written straight in.

    Direct SQL rather than ``db.record_attempt`` + a writer, because the point
    of every test below is the SHAPE of the resulting rows (which outcome, which
    deadline) and going through the real writers would need a coordinator and a
    lease per row to express the same thing.
    """
    lease_id = f"lease-{uuid.uuid4().hex[:12]}"
    # Instants computed here rather than as SQL CASE expressions: the same
    # local clock backs both this process and the ephemeral test server, and a
    # plain INSERT of nine values is far easier to read than the conditional
    # timestamp arithmetic it replaces.
    now = datetime.now(timezone.utc)
    resolved_at = None if outcome is None else now - timedelta(days=resolved_days_ago)
    claimed_at = (resolved_at or now) - timedelta(seconds=duration_s)
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.attempts
                (lease_id, machine_id, job_id, task_id, claimed_at,
                 resolved_at, accepted_at, outcome, lease_deadline)
            values (%s, %s, %s, 'task-1', %s, %s, %s, %s, %s)
            """,
            (
                lease_id,
                machine_id,
                f"job-{uuid.uuid4().hex[:8]}",
                claimed_at,
                resolved_at,
                resolved_at if outcome == "accepted" else None,
                outcome,
                (
                    None
                    if deadline_in_s is None
                    else now + timedelta(seconds=deadline_in_s)
                ),
            ),
        )
    return lease_id


def _uptime_bucket(db, machine_id: str, *, hours_ago: int, beats: int = 1) -> None:
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.machine_uptime_hours (machine_id, hour_ts, beats)
            values (%s, date_trunc('hour', now()) - %s::interval, %s)
            on conflict (machine_id, hour_ts) do update set beats = excluded.beats
            """,
            (machine_id, timedelta(hours=hours_ago), beats),
        )


def _find(overview: dict, machine_id: str) -> dict:
    for provider in overview["providers"]:
        if provider["id"] == str(machine_id):
            return provider
    raise AssertionError(f"machine {machine_id} is not in the overview")


# ---------------------------------------------------------------------------
# anonymisation
# ---------------------------------------------------------------------------


def test_a_stranger_sees_a_handle_and_never_a_name_or_a_node_id(db):
    """The one that has to hold. A provider list is fleet-wide, so a stranger
    loads it and gets everyone's machines; ``name`` is frequently a person's own
    name and ``node_id`` is the key enrolment matches on."""
    owner = _new_user(db)
    stranger = _new_user(db)
    machine_id = _machine(db, owner, name="Phong's 4090")
    node_id = _node_id(db, machine_id)

    provider = _find(network.providers_overview(db, stranger), machine_id)

    assert provider["own"] is False
    assert provider["label"] == f"prov…{node_id[-6:]}"

    blob = json.dumps(provider)
    assert "Phong's 4090" not in blob
    assert node_id not in blob, "the full node_id must never leave the API"
    assert owner not in blob, "nor the owner's identity, in any field"


def test_the_owner_sees_their_own_machine_by_name(db):
    owner = _new_user(db)
    machine_id = _machine(db, owner, name="Phong's 4090")

    provider = _find(network.providers_overview(db, owner), machine_id)

    assert provider["own"] is True
    assert provider["label"] == "Phong's 4090"


def test_an_unnamed_machine_falls_back_to_its_node_id_for_its_owner_only(db):
    owner = _new_user(db)
    stranger = _new_user(db)
    machine_id = _machine(db, owner, name=None)
    node_id = _node_id(db, machine_id)

    assert _find(network.providers_overview(db, owner), machine_id)["label"] == node_id
    assert node_id not in json.dumps(
        _find(network.providers_overview(db, stranger), machine_id)
    )


def test_the_owner_block_on_a_detail_read_is_owner_only(db):
    owner = _new_user(db)
    stranger = _new_user(db)
    machine_id = _machine(db, owner, name="Phong's 4090")

    mine = network.provider_detail(db, machine_id, owner)
    theirs = network.provider_detail(db, machine_id, stranger)

    assert mine["owner"]["name"] == "Phong's 4090"
    assert mine["owner"]["node_id"] == _node_id(db, machine_id)
    # Absent, not present-and-null: a null `owner` key is a shape a careless
    # console renders as an empty owner panel on a stranger's machine.
    assert "owner" not in theirs
    assert _node_id(db, machine_id) not in json.dumps(theirs)


def test_only_active_machines_are_providers(db):
    """A pending machine never redeemed a token, a revoked one holds a dead
    credential, and a deleted one is a tombstone with its device columns
    scrubbed. None of the three is capacity."""
    owner = _new_user(db)
    ids = {
        status: _machine(db, owner, status=status)
        for status in ("pending", "revoked", "deleted", "active")
    }

    listed = {p["id"] for p in network.providers_overview(db, owner)["providers"]}

    assert str(ids["active"]) in listed
    for status in ("pending", "revoked", "deleted"):
        assert str(ids[status]) not in listed


# ---------------------------------------------------------------------------
# the denominator
# ---------------------------------------------------------------------------


def test_success_rate_divides_by_resolved_and_never_by_every_attempt(db):
    """Five accepted and one failed out of six RESOLVED, with three still in
    flight. The rate is 5/6, not 5/9 — an unresolved attempt is not a failure,
    and a machine's standing must not fall merely because it is busy."""
    owner = _new_user(db)
    machine_id = _machine(db, owner)

    for _ in range(5):
        _attempt(db, machine_id, outcome="accepted")
    _attempt(db, machine_id, outcome="failed")
    _attempt(db, machine_id, outcome=None, deadline_in_s=600)
    _attempt(db, machine_id, outcome=None, deadline_in_s=600)
    _attempt(db, machine_id, outcome=None, deadline_in_s=-600)

    leases = _find(network.providers_overview(db, owner), machine_id)["leases"]

    assert leases["total"] == 9
    assert leases["resolved"] == 6
    assert leases["success_rate"] == round(5 / 6, 4)
    assert leases["success_rate"] != round(5 / 9, 4)


def test_an_expired_deadline_is_not_an_active_lease(db):
    """Unresolved AND still in the coordinator's window. Past the deadline the
    coordinator refuses the heartbeat and rejects the commit (0015), so the
    machine is not working — and a dead machine must not appear to hold leases
    for ever."""
    owner = _new_user(db)
    machine_id = _machine(db, owner)

    _attempt(db, machine_id, outcome=None, deadline_in_s=600)
    _attempt(db, machine_id, outcome=None, deadline_in_s=-600)
    _attempt(db, machine_id, outcome=None, deadline_in_s=None)

    leases = _find(network.providers_overview(db, owner), machine_id)["leases"]

    assert leases["total"] == 3
    assert leases["resolved"] == 0
    assert leases["active"] == 1


def test_below_min_evidence_the_rate_is_unproven_and_not_zero(db):
    """``metrics.MIN_EVIDENCE`` is 5, and the contract ``marketplace`` depends
    on is that None means UNPROVEN. Two resolved attempts, both failed, must not
    read as 0.0 — that is the worst number this page can show and a new host has
    not earned it. The COUNTS are still reported."""
    owner = _new_user(db)
    machine_id = _machine(db, owner)

    _attempt(db, machine_id, outcome="failed")
    _attempt(db, machine_id, outcome="failed")

    leases = _find(network.providers_overview(db, owner), machine_id)["leases"]

    assert leases["resolved"] == 2
    assert leases["success_rate"] is None


def test_a_machine_with_no_attempts_at_all_reports_zeroes_and_no_rate(db):
    owner = _new_user(db)
    machine_id = _machine(db, owner)

    leases = _find(network.providers_overview(db, owner), machine_id)["leases"]

    assert leases == {
        "active": 0,
        "total": 0,
        "resolved": 0,
        "success_rate": None,
    }


def test_history_counts_every_outcome_including_the_unresolved_ones(db):
    owner = _new_user(db)
    machine_id = _machine(db, owner)

    _attempt(db, machine_id, outcome="accepted", duration_s=30)
    _attempt(db, machine_id, outcome="accepted", duration_s=10)
    _attempt(db, machine_id, outcome="failed", duration_s=999)
    _attempt(db, machine_id, outcome="expired", duration_s=999)
    _attempt(db, machine_id, outcome=None, deadline_in_s=600)

    history = network.provider_detail(db, machine_id, owner)["history"]

    assert history["attempts"] == {
        "accepted": 2,
        "failed": 1,
        "expired": 1,
        "unresolved": 1,
    }
    # Accepted only: on a failed or expired attempt the same interval is time
    # WASTED, not time a task takes.
    assert history["accepted_duration_s_total"] == pytest.approx(40.0, abs=0.5)


# ---------------------------------------------------------------------------
# uptime
# ---------------------------------------------------------------------------


def test_uptime_is_derived_from_the_buckets_over_the_whole_window(db):
    """Half of the last 168 hours have a bucket, so the machine was up half the
    week — 50%, not 100%. Dividing by the buckets found instead of by the window
    would report every machine that ever heartbeated as perfect."""
    owner = _new_user(db)
    machine_id = _machine(db, owner)
    for hours_ago in range(84):
        _uptime_bucket(db, machine_id, hours_ago=hours_ago)

    provider = _find(network.providers_overview(db, owner), machine_id)

    assert provider["uptime"] == {"hours_up_7d": 84, "hours_observed_7d": 84}
    assert provider["uptime_7d_pct"] == 50.0


def test_uptime_is_null_and_not_zero_before_the_first_bucket(db):
    """0.0 is "it was never up", which is the worst reading available; a machine
    that enrolled ten minutes ago has not earned it."""
    owner = _new_user(db)
    machine_id = _machine(db, owner)

    provider = _find(network.providers_overview(db, owner), machine_id)

    assert provider["uptime_7d_pct"] is None
    assert provider["uptime"] == {"hours_up_7d": 0, "hours_observed_7d": 0}


def test_buckets_older_than_the_window_do_not_count(db):
    owner = _new_user(db)
    machine_id = _machine(db, owner)
    _uptime_bucket(db, machine_id, hours_ago=1)
    _uptime_bucket(db, machine_id, hours_ago=200)

    provider = _find(network.providers_overview(db, owner), machine_id)

    assert provider["uptime"]["hours_observed_7d"] == 1


def test_a_zero_beat_bucket_counts_as_observed_but_not_as_up(db):
    """Nothing writes one today. It is asserted anyway because the whole reason
    both counts are reported is that the day something DOES write an
    observed-absent bucket, every reader that assumed the two were equal is
    silently wrong."""
    owner = _new_user(db)
    machine_id = _machine(db, owner)
    _uptime_bucket(db, machine_id, hours_ago=1, beats=3)
    _uptime_bucket(db, machine_id, hours_ago=2, beats=0)

    provider = _find(network.providers_overview(db, owner), machine_id)

    assert provider["uptime"] == {"hours_up_7d": 1, "hours_observed_7d": 2}


def test_the_24h_strip_is_dense_so_a_dark_night_stays_dark(db):
    """Generated from the clock and left-joined, not read out of the ledger: a
    missing hour has to hold its position, or the strip closes up and draws a
    machine that was down all night as one that was up all night."""
    owner = _new_user(db)
    machine_id = _machine(db, owner)
    _uptime_bucket(db, machine_id, hours_ago=0)
    _uptime_bucket(db, machine_id, hours_ago=3)

    strip = network.provider_detail(db, machine_id, owner)["series"]["uptime_24h"]

    assert len(strip) == 24
    assert [i for i, hour in enumerate(strip) if hour["up"]] == [20, 23]
    assert all(hour["hour_ts"] is not None for hour in strip)


def test_the_30_day_lease_series_is_dense_and_dated(db):
    owner = _new_user(db)
    machine_id = _machine(db, owner)
    _attempt(db, machine_id, outcome="accepted", resolved_days_ago=2)
    _attempt(db, machine_id, outcome="failed", resolved_days_ago=2)
    _attempt(db, machine_id, outcome="accepted", resolved_days_ago=40)

    series = network.provider_detail(db, machine_id, owner)["series"]["leases_daily_30d"]

    assert len(series) == 30
    assert series == sorted(series, key=lambda point: point["date"])
    day = series[-3]
    assert (day["resolved"], day["accepted"]) == (2, 1)
    assert sum(point["resolved"] for point in series) == 2, "40 days ago is outside"


# ---------------------------------------------------------------------------
# specs, badges and totals
# ---------------------------------------------------------------------------


def test_specs_surface_the_reported_gpus_and_never_reclassify_them(db):
    owner = _new_user(db)
    machine_id = _machine(
        db,
        owner,
        capabilities={
            "cpu_cores": 12,
            "memory_bytes": 34359738368,
            "os": "Linux",
            "architecture": "x86_64",
            "gpus": [
                {"index": 0, "name": "NVIDIA GeForce RTX 4090", "memory_total_mb": 24564},
                {"index": 1, "name": "NVIDIA GeForce RTX 3070", "memory_total_mb": 8192},
            ],
        },
    )

    specs = _find(network.providers_overview(db, owner), machine_id)["specs"]

    assert specs["cpu_cores"] == 12.0
    assert specs["memory_bytes"] == 34359738368
    assert specs["os"] == "Linux"
    assert specs["architecture"] == "x86_64"
    # Both cards, in the driver's order, unclassified. The ladder's "smallest
    # card wins" rule is a listing decision and does not belong here.
    assert specs["gpus"] == [
        {"name": "NVIDIA GeForce RTX 4090", "memory_total_mb": 24564},
        {"name": "NVIDIA GeForce RTX 3070", "memory_total_mb": 8192},
    ]


def test_an_unreadable_card_reports_no_memory_rather_than_zero_memory(db):
    owner = _new_user(db)
    machine_id = _machine(
        db, owner, capabilities={"gpus": [{"index": 0, "name": "GPU"}]}
    )

    specs = _find(network.providers_overview(db, owner), machine_id)["specs"]

    assert specs["gpus"] == [{"name": "GPU", "memory_total_mb": None}]
    assert specs["cpu_cores"] is None
    assert specs["os"] is None


def test_the_badge_matches_the_console_rules_including_precedence(db):
    owner = _new_user(db)
    sandboxed = _machine(db, owner, sandbox_capable=True, unsandboxed_argv_capable=True)
    trusted = _machine(db, owner, sandbox_capable=False, unsandboxed_argv_capable=True)
    modules = _machine(db, owner, sandbox_capable=False)

    overview = network.providers_overview(db, owner)

    # A Docker-capable host never falls back to running work on the host.
    assert _find(overview, sandboxed)["badge"] == "sandboxed"
    assert _find(overview, trusted)["badge"] == "trusted"
    assert _find(overview, modules)["badge"] == "modules-only"


def test_used_is_the_capacity_committed_to_a_live_lease(db):
    """A whole machine, because a machine holding a lease is committed to it —
    this API cannot measure a host's actual CPU utilisation and must not appear
    to. Asserted as a delta: the fleet is shared with the rest of the suite."""
    owner = _new_user(db)
    before = network.providers_overview(db, owner)["totals"]

    caps = {"cpu_cores": 4, "memory_bytes": 1000, "gpus": [{"index": 0}]}
    busy = _machine(db, owner, capabilities=caps)
    _machine(db, owner, capabilities=caps)
    _attempt(db, busy, outcome=None, deadline_in_s=600)

    after = network.providers_overview(db, owner)["totals"]

    assert after["cpu_cores"]["total"] - before["cpu_cores"]["total"] == 8.0
    assert after["cpu_cores"]["used"] - before["cpu_cores"]["used"] == 4.0
    assert after["gpus"]["total"] - before["gpus"]["total"] == 2
    assert after["gpus"]["used"] - before["gpus"]["used"] == 1
    assert after["memory_bytes"]["total"] - before["memory_bytes"]["total"] == 2000
    assert after["memory_bytes"]["used"] - before["memory_bytes"]["used"] == 1000
    assert after["providers_total"] - before["providers_total"] == 2


def test_online_uses_the_one_shared_freshness_threshold(db):
    """``db.MACHINE_ONLINE_PREDICATE`` — 90 seconds — and not a second one
    invented here. A machine the workspace page calls online and the network
    page calls offline is a bug report nobody can reproduce."""
    owner = _new_user(db)
    fresh = _machine(db, owner, seen_seconds_ago=5)
    stale = _machine(db, owner, seen_seconds_ago=600)
    never = _machine(db, owner, seen_seconds_ago=None)

    overview = network.providers_overview(db, owner)

    assert _find(overview, fresh)["online"] is True
    assert _find(overview, stale)["online"] is False
    assert _find(overview, never)["online"] is False
    assert _find(overview, never)["last_seen_at"] is None


# ---------------------------------------------------------------------------
# detail: what is not there
# ---------------------------------------------------------------------------


def test_a_deleted_machine_has_no_detail_to_show(db):
    owner = _new_user(db)
    machine_id = _machine(db, owner, status="deleted")

    assert network.provider_detail(db, machine_id, owner) is None


def test_an_unknown_or_malformed_id_answers_the_same_nothing(db):
    """One answer for "no such machine" and "not even a uuid": a response that
    told them apart would let a prober learn which ids are real."""
    owner = _new_user(db)

    assert network.provider_detail(db, str(uuid.uuid4()), owner) is None
    assert network.provider_detail(db, "not-a-uuid", owner) is None
    # And the connection is still usable afterwards.
    assert network.providers_overview(db, owner)["totals"]["providers_total"] >= 0


def test_a_stranger_may_still_read_an_active_machines_detail(db):
    """The anonymised profile is the point of a provider network: one you can
    only inspect if you already own it is not a network anybody can choose
    from."""
    owner = _new_user(db)
    stranger = _new_user(db)
    machine_id = _machine(db, owner)
    _attempt(db, machine_id, outcome="accepted")

    detail = network.provider_detail(db, machine_id, stranger)

    assert detail is not None
    assert detail["own"] is False
    assert detail["history"]["attempts"]["accepted"] == 1
    assert len(detail["series"]["uptime_24h"]) == 24


# ---------------------------------------------------------------------------
# credits
# ---------------------------------------------------------------------------


def _earn(db, owner: str, lease_id: str, amount_zc: int) -> None:
    """One ``earned_accepted_work`` leg on the host's spendable account, the
    shape ``marketplace.settle_accepted_work`` writes."""
    with db.cursor() as cur:
        cur.execute(
            "insert into public.credit_accounts (owner_id, kind, balance_zc) "
            "values (%s, 'spendable', %s) "
            "on conflict (owner_id, kind) do update set balance_zc = "
            "public.credit_accounts.balance_zc + excluded.balance_zc "
            "returning id",
            (owner, amount_zc),
        )
        account_id = cur.fetchone()["id"]
        cur.execute(
            "insert into public.credit_entries "
            "    (account_id, delta_zc, reason, ref_type, ref_id) "
            "values (%s, %s, 'earned_accepted_work', 'attempt', %s)",
            (account_id, amount_zc, lease_id),
        )


def test_credits_are_attributed_to_the_machine_through_the_lease(db):
    """``credit_accounts`` is per PERSON, so the ledger alone cannot say what a
    MACHINE earned. The earning leg's ``('attempt', lease_id)`` ref and
    ``attempts.machine_id`` are what close the gap — exactly, with nothing
    apportioned."""
    owner = _new_user(db)
    mine = _machine(db, owner)
    other = _machine(db, owner)

    _earn(db, owner, _attempt(db, mine, outcome="accepted"), 1500)
    _earn(db, owner, _attempt(db, mine, outcome="accepted"), 500)
    _earn(db, owner, _attempt(db, other, outcome="accepted"), 9999)

    assert network.provider_detail(db, mine, owner)["owner"]["credits_earned"] == 2000.0


def test_a_machine_that_has_never_been_paid_reports_none_not_zero(db):
    """Zero is a real outcome — a listing donated at 0 ZC/hour settles for
    nothing — and it must stay distinguishable from never having been paid at
    all."""
    owner = _new_user(db)
    machine_id = _machine(db, owner)
    _attempt(db, machine_id, outcome="accepted")

    assert network.provider_detail(db, machine_id, owner)["owner"][
        "credits_earned"
    ] is None


def test_only_the_earning_leg_counts_never_the_buyers_side(db):
    """Holds, releases, refunds and the buyer's ``spent_accepted_work`` share
    the same ``('attempt', lease_id)`` ref. Naming the reason is what keeps a
    host's earnings from being a sum over the whole movement."""
    owner = _new_user(db)
    machine_id = _machine(db, owner)
    lease_id = _attempt(db, machine_id, outcome="accepted")
    _earn(db, owner, lease_id, 700)

    with db.cursor() as cur:
        cur.execute(
            "select id from public.credit_accounts where owner_id = %s "
            "and kind = 'spendable'",
            (owner,),
        )
        account_id = cur.fetchone()["id"]
        for reason, delta in (
            ("spent_accepted_work", -700),
            ("escrow_hold", -50),
            ("escrow_refund", 50),
        ):
            cur.execute(
                "insert into public.credit_entries "
                "    (account_id, delta_zc, reason, ref_type, ref_id) "
                "values (%s, %s, %s, 'attempt', %s)",
                (account_id, delta, reason, lease_id),
            )

    assert network.provider_detail(db, machine_id, owner)["owner"][
        "credits_earned"
    ] == 700.0


# ---------------------------------------------------------------------------
# declaring a location
# ---------------------------------------------------------------------------


def test_an_owner_declares_a_location_and_the_source_says_so(db):
    owner = _new_user(db)
    machine_id = _machine(db, owner)

    assert network.set_machine_location(
        db, machine_id, owner,
        country="de", region=" Bavaria ", city="Munich", lat=48.14, lon=11.58,
    ) is True

    location = _find(network.providers_overview(db, owner), machine_id)["location"]
    assert location == {
        "country": "DE",          # uppercased on the way in
        "region": "Bavaria",      # trimmed
        "city": "Munich",
        "lat": 48.14,
        "lon": 11.58,
        # NEVER a parameter. Geo is declared, never detected.
        "source": "declared",
    }


def test_a_stranger_cannot_declare_where_somebody_elses_machine_is(db):
    """The ownership test is in the WHERE clause, so this cannot be forgotten.
    False rather than an exception, and the same False a missing machine gets —
    the route's 404 must not confirm which ids exist."""
    owner = _new_user(db)
    stranger = _new_user(db)
    machine_id = _machine(db, owner)
    network.set_machine_location(db, machine_id, owner, country="DE")

    assert network.set_machine_location(
        db, machine_id, stranger, country="FR", city="Paris"
    ) is False

    location = _find(network.providers_overview(db, owner), machine_id)["location"]
    assert location["country"] == "DE", "the stranger's write must not have landed"


def test_a_machine_that_is_not_active_cannot_be_relocated(db):
    owner = _new_user(db)
    revoked = _machine(db, owner, status="revoked")
    deleted = _machine(db, owner, status="deleted")

    assert network.set_machine_location(db, revoked, owner, country="DE") is False
    assert network.set_machine_location(db, deleted, owner, country="DE") is False


def test_an_unknown_or_malformed_machine_id_is_refused_not_crashed(db):
    owner = _new_user(db)

    assert network.set_machine_location(db, str(uuid.uuid4()), owner, country="DE") is False
    assert network.set_machine_location(db, "not-a-uuid", owner, country="DE") is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"country": "DEU"},          # alpha-3, not alpha-2
        {"country": "D"},
        {"country": "12"},
        {"country": 49},
        {"lat": 91.0, "lon": 0.0},
        {"lat": -91.0, "lon": 0.0},
        {"lat": 0.0, "lon": 181.0},
        {"lat": 0.0, "lon": -181.0},
        {"lat": 48.14},              # half a coordinate pins nothing
        {"lon": 11.58},
        {"lat": "48.14", "lon": "11.58"},
        {"city": 7},
    ],
)
def test_a_location_that_is_not_one_is_refused_as_bad_input(db, kwargs):
    """``InvalidLocation``, not ``False``: "you typed a latitude of 500" and
    "that machine is not yours" are different problems and the route answers
    400 and 404 respectively."""
    owner = _new_user(db)
    machine_id = _machine(db, owner)

    with pytest.raises(network.InvalidLocation):
        network.set_machine_location(db, machine_id, owner, **kwargs)


def test_the_boundary_values_are_legal(db):
    owner = _new_user(db)
    machine_id = _machine(db, owner)

    assert network.set_machine_location(
        db, machine_id, owner, country="NZ", lat=-90.0, lon=180.0
    ) is True


def test_a_declaration_replaces_the_whole_location_so_a_field_can_be_cleared(db):
    """Not a patch of individual fields: without this, a city typed once could
    never be removed."""
    owner = _new_user(db)
    machine_id = _machine(db, owner)
    network.set_machine_location(
        db, machine_id, owner, country="DE", city="Munich", lat=48.14, lon=11.58
    )

    network.set_machine_location(db, machine_id, owner, country="DE", city="")

    location = _find(network.providers_overview(db, owner), machine_id)["location"]
    assert location["city"] is None
    assert location["lat"] is None and location["lon"] is None
    assert location["country"] == "DE"


def test_the_database_refuses_an_impossible_coordinate_even_without_the_api(db):
    """Belt and braces, the same discipline ``machines_status_check`` applies:
    an invalid state must be impossible to write even given a bug above."""
    owner = _new_user(db)
    machine_id = _machine(db, owner)

    with pytest.raises(psycopg.errors.CheckViolation):
        with db.cursor() as cur:
            cur.execute(
                "update public.machines set geo_lat = 500 where id = %s", (machine_id,)
            )


def test_the_database_refuses_a_geo_source_it_has_never_heard_of(db):
    """There is deliberately no value meaning "we worked it out from the
    network"."""
    owner = _new_user(db)
    machine_id = _machine(db, owner)

    with pytest.raises(psycopg.errors.CheckViolation):
        with db.cursor() as cur:
            cur.execute(
                "update public.machines set geo_source = 'geoip' where id = %s",
                (machine_id,),
            )


# ---------------------------------------------------------------------------
# the heartbeat write
# ---------------------------------------------------------------------------


def test_a_heartbeat_writes_last_seen_and_counts_a_beat_in_the_current_hour(db):
    owner = _new_user(db)
    machine_id = _machine(db, owner, seen_seconds_ago=None)

    dbmod.touch_machine_last_seen(db, machine_id)
    dbmod.touch_machine_last_seen(db, machine_id)

    with db.cursor() as cur:
        cur.execute(
            "select last_seen_at from public.machines where id = %s", (machine_id,)
        )
        assert cur.fetchone()["last_seen_at"] is not None
        cur.execute(
            "select hour_ts, beats from public.machine_uptime_hours "
            "where machine_id = %s",
            (machine_id,),
        )
        rows = cur.fetchall()

    assert len(rows) == 1, "two beats in one hour are one bucket, not two rows"
    assert rows[0]["beats"] == 2


def test_two_heartbeats_an_hour_apart_are_two_buckets(db):
    owner = _new_user(db)
    machine_id = _machine(db, owner)
    _uptime_bucket(db, machine_id, hours_ago=1, beats=4)

    dbmod.touch_machine_last_seen(db, machine_id)

    provider = _find(network.providers_overview(db, owner), machine_id)
    assert provider["uptime"]["hours_observed_7d"] == 2


def _apply_through(conn, version: str) -> None:
    """The real migrations, in order, up to and including `version`."""
    for migration in migrate.discover():
        conn.execute(migration.sql)
        if migration.version == version:
            return
    raise AssertionError(f"no migration named {version}")


def test_a_heartbeat_still_lands_on_a_database_without_migration_0029(postgres_dsn):
    """The deploy-window case, and the one that must not be a regression.

    ``last_seen_at`` is what stands between a live rental and
    ``capacity.reconcile`` destroying it, and the uptime bucket is a pixel on a
    chart. An API that reaches production before 0029 does must keep writing the
    first while quietly recording none of the second — so the write sits in its
    own savepoint, and the savepoint is what leaves the enclosing transaction
    usable afterwards.
    """
    with scratch_database(postgres_dsn) as dsn, connected(dsn) as conn:
        conn.execute(AUTH_STUB)
        _apply_through(conn, "0028_machine_deletion")

        owner = str(uuid.uuid4())
        machine_id = str(uuid.uuid4())
        conn.execute("insert into auth.users (id) values (%s)", (owner,))
        conn.execute("insert into public.profiles (id) values (%s)", (owner,))
        conn.execute(
            "insert into public.machines (id, owner_id, node_id, status) "
            "values (%s, %s, 'pre-0029-node', 'active')",
            (machine_id, owner),
        )
        assert conn.execute(
            "select to_regclass('public.machine_uptime_hours') is null"
        ).fetchone()[0], "this test is meaningless if the table exists"

        # Must not raise.
        dbmod.touch_machine_last_seen(conn, machine_id)

        assert conn.execute(
            "select last_seen_at is not null from public.machines where id = %s",
            (machine_id,),
        ).fetchone()[0], "the liveness write must survive the missing ledger"

        # And the connection is still usable — the savepoint cleared the error
        # state rather than leaving an aborted transaction behind.
        assert conn.execute("select 1").fetchone()[0] == 1
