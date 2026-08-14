"""The marketplace: policy without a database, then the ledger with one.

The properties pinned here are the ones whose failure moves credits that
should not have moved, or fails to move ones that should, and each test says
which:

- **Conservation.** Every movement's legs sum to zero except a grant. A ledger
  that accepts one unbalanced write cannot be audited afterwards, because from
  then on every check fails and none of them says which write was the bad one.
- **The balance invariant.** ``balance_zc == sum(delta_zc)`` for every
  account, including under writes racing on one account.
- **Idempotency by the unique index**, not by the caller's discipline. A
  retried settlement pays once.
- **The one-time grant.** Enforced, not advised — scarcity is what makes a
  price mean anything, and a second grant is a refill by another name.
- **Escrow on ``claimed``, never on ``granted``.** Holding at grant lets a
  host who never shows up freeze a buyer's balance, and a grant costs the host
  nothing.
- **Settlement on ACCEPTED work.** A failed attempt refunds in full and earns
  the host nothing. This is the market's most distinctive property and hard
  rule 4 in one sentence.
- **Unproven hosts.** Ranked without a synthesised denominator, and bounded by
  a share cap rather than by a number somebody made up.
- **Owner scoping.** Another user sees nothing and cannot tell "not yours"
  from "not there".
- **``contributions`` is never written.** The rule that module states about
  itself, enforced against this one.
"""
from __future__ import annotations

import re
import pathlib
import threading
import uuid
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Json

from flashml_cloud_api import marketplace as mk
from flashml_cloud_api import metrics

MODULE = pathlib.Path(mk.__file__)
MIGRATION = (
    pathlib.Path(__file__).parent.parent / "migrations" / "0018_marketplace.sql"
)

#: A 24GB card as a driver actually reports it — 24564, not 24576, and not
#: 24000. Every fixture below uses real readings for the same reason the
#: floors in `GPU_CLASS_FLOORS_MB` are floors.
RTX_4090 = {"index": 0, "name": "NVIDIA GeForce RTX 4090", "memory_total_mb": 24564,
            "compute_capability": "8.9"}
RTX_3070 = {"index": 0, "name": "NVIDIA GeForce RTX 3070", "memory_total_mb": 8192,
            "compute_capability": "8.6"}
A100_80 = {"index": 0, "name": "NVIDIA A100-SXM4-80GB", "memory_total_mb": 81920,
           "compute_capability": "8.0"}
H100 = {"index": 0, "name": "NVIDIA H100 PCIe", "memory_total_mb": 81559,
        "compute_capability": "9.0"}


# ---------------------------------------------------------------------------
# Policy. No database anywhere below this line until the fixtures.
# ---------------------------------------------------------------------------


def test_the_testing_grant_and_usd_conversion_are_exact():
    """A wrong testing grant overfunds every new account, while a float
    conversion makes exact display and billing arithmetic impossible."""
    assert mk.STARTING_GRANT_ZC == 10_000
    assert mk.USD_PER_ZC == Decimal("1")
    assert mk.MILLICREDITS_PER_CREDIT == 1_000
    assert mk.STARTING_GRANT_ZC // mk.MILLICREDITS_PER_CREDIT == 10

    # The unit is an hour of compute, never a dollar. `gpu-24gb` is 1 ZC by
    # definition and everything else is a ratio against it.
    assert mk.REFERENCE_CLASS == "gpu-24gb"
    assert mk.REFERENCE_ZC_PER_HOUR[mk.REFERENCE_CLASS] == mk.MILLICREDITS_PER_CREDIT


def test_the_ladder_is_the_one_captured_on_2026_08_11():
    """Design §2.5.2. The ratios come from real RunPod community prices, which
    is what makes the ladder defensible; drifting one of them silently
    re-prices everybody's machine."""
    assert mk.REFERENCE_ZC_PER_HOUR == {
        "cpu-small": 100,
        "cpu-large": 100,
        "gpu-8gb": 500,
        "gpu-16gb": 1_000,
        "gpu-24gb": 1_000,
        "gpu-48gb": 2_000,
        "gpu-80gb": 4_000,
        "gpu-80gb-hopper": 10_000,
    }
    assert set(mk.REFERENCE_ZC_PER_HOUR) == set(mk.CAPABILITY_CLASSES)


def test_a_class_is_derived_from_what_the_driver_reported():
    """Never user-entered (design §4). A host who could type their own class
    would sell a 3070 as gpu-80gb-hopper for twenty times the price."""
    assert mk.capability_class({"gpus": [RTX_4090]}) == "gpu-24gb"
    assert mk.capability_class({"gpus": [RTX_3070]}) == "gpu-8gb"
    assert mk.capability_class({"gpus": [A100_80]}) == "gpu-80gb"
    assert mk.capability_class({"gpus": [H100]}) == "gpu-80gb-hopper"
    assert mk.capability_class({"gpus": [], "cpu_cores": 16}) == "cpu-large"
    assert mk.capability_class({"gpus": [], "cpu_cores": 4}) == "cpu-small"


def test_a_mixed_rig_is_classed_by_its_SMALLEST_gpu():
    """A 4090 next to a 3070 can only promise 8GB to whichever task lands on
    whichever card — placement matches on GPU COUNT alone, so the machine
    cannot steer the big task to the big card. Classing it by its best card
    sells a promise it cannot keep, and the buyer discovers that as an OOM."""
    assert mk.capability_class({"gpus": [RTX_4090, RTX_3070]}) == "gpu-8gb"


def test_a_gpu_with_no_readable_memory_has_no_class_at_all():
    """None, not a CPU fallback, and the distinction is the one this codebase
    draws everywhere between None and 0.

    A machine reporting GPUs is a machine somebody means to sell as a GPU
    host. Filing it in the CPU book at a tenth of the price is worse than
    refusing to file it, because the host has no way to notice except from
    their earnings — whereas a refusal is visible and has one obvious fix.
    """
    assert mk.capability_class({"gpus": [{"index": 0, "name": "?"}]}) is None
    assert mk.capability_class(
        {"gpus": [RTX_4090, {"index": 1, "name": "?"}]}
    ) is None
    assert mk.capability_class(None) is None


def test_a_gpu_below_the_ladder_falls_back_to_the_cpu_class():
    """The other side of the same rule. Here the reading SUCCEEDED: the market
    simply does not price a 6GB card, and what this machine has to sell is
    genuinely its CPU. Refusing would be refusing a fact we have."""
    tiny = {"index": 0, "name": "GTX 1660", "memory_total_mb": 6144}
    assert mk.capability_class({"gpus": [tiny], "cpu_cores": 16}) == "cpu-large"


def test_hopper_is_told_from_ampere_by_compute_capability_not_by_vram():
    """Both are 80GB and they differ by 2.5x in price, so VRAM cannot separate
    them. An unreadable capability under-claims to gpu-80gb, which is the only
    safe direction."""
    unknown = dict(H100)
    unknown["compute_capability"] = ""
    assert mk.capability_class({"gpus": [unknown]}) == "gpu-80gb"
    # Every device must be Hopper, for the same reason VRAM takes the minimum.
    assert mk.capability_class({"gpus": [H100, A100_80]}) == "gpu-80gb"


def test_a_zero_ask_is_legal_and_is_labelled_donated():
    """M13. Volunteers are a real supply tier and pricing them at a floor
    would misrepresent them, so there is no minimum ask anywhere."""
    assert mk.is_donated(0)
    assert mk.price_label(0) == "donated"
    assert not mk.is_donated(1)
    assert mk.price_label(340) == "priced"


def test_a_hold_rounds_up_and_a_charge_never_exceeds_it():
    """The two errors are not symmetric. A hold that is short is an
    under-collateralised charge; a hold that is generous is refunded at
    settlement — so the rounding goes the way whose mistake is refundable."""
    assert mk.hold_zc(1_000, 3600) == 1_000        # one hour at 1 ZC
    assert mk.hold_zc(1_000, 360) == 100           # six minutes
    assert mk.hold_zc(1_000, 1) == 1               # rounded up, never to 0
    assert mk.hold_zc(0, 3600) == 0                # donated

    # An attempt that overran its estimate charges the estimate, not the
    # overrun: the excess is the host's risk and a host who dislikes it can
    # raise their ask.
    assert mk.charge_zc(1_000, 7200, held_zc=1_000) == 1_000
    # ...and accepted work that finished early costs what it cost.
    assert mk.charge_zc(1_000, 1800, held_zc=1_000) == 500
    # Hard rule 4 as arithmetic: no accepted seconds, no charge.
    assert mk.charge_zc(1_000, 0, held_zc=1_000) == 0


def test_an_unproven_host_ranks_on_its_ask_and_nothing_is_substituted():
    """metrics.acceptance_rates returns None below five resolved observations
    and nothing here replaces it. Ranking on the ask does rank an unproven
    host as if it were perfect; the answer to that is the SHARE CAP, not a
    denominator somebody invented."""
    assert metrics.MIN_EVIDENCE == 5
    unproven = metrics.acceptance_rates(
        [{"machine_id": "m", "capability_class": "gpu-24gb", "outcome": "accepted"}]
        * 4
    )
    assert unproven[0]["acceptance_rate"] is None

    assert mk.effective_price(400, None) == 400
    # A proven host is priced per ACCEPTED result, which is what a buyer pays
    # for: 400 at 0.98 beats 300 at 0.71.
    assert mk.effective_price(400, 0.98) < mk.effective_price(300, 0.71)


def test_a_donated_ask_is_free_per_result_whatever_the_rate():
    """0 / rate is 0 for every rate that exists, including the one that does
    not — which is why the case comes before the division."""
    assert mk.effective_price(0, None) == 0
    assert mk.effective_price(0, 0.5) == 0
    assert mk.effective_price(0, 0.0) == 0


def test_a_host_that_has_never_had_anything_accepted_prices_at_infinity():
    """None means "no finite bid clears this", not "excluded". The host stays
    in the book and stays ranked — scheduler/__init__.py:637 is explicit that
    reliability may rank and must never exclude — it just costs what the
    arithmetic says a host with zero accepted work costs."""
    assert mk.effective_price(400, 0.0) is None


def test_the_unproven_budget_is_a_quarter_but_never_zero():
    """floor(1/4 * 1) is 0, which would mean a single-task job never touches a
    new host. Since a host needs five RESOLVED attempts before it has a rate
    at all, a market that never gives a newcomer a first task has no newcomers
    in it after the first week."""
    assert mk.unproven_task_budget(40) == 10
    assert mk.unproven_task_budget(4) == 1
    assert mk.unproven_task_budget(1) == 1        # the floor that matters
    assert mk.unproven_task_budget(2) == 1        # never more than it has
    assert mk.unproven_task_budget(0) == 0


def _ask(listing, ask, rate=None, cap=1, machine=None):
    return mk.Ask(
        listing_id=listing,
        machine_id=machine or f"m-{listing}",
        host_id=f"h-{listing}",
        ask_zc_per_hour=ask,
        max_concurrent_tasks=cap,
        acceptance_rate=rate,
    )


def test_a_bid_partial_fills_across_several_machines_at_several_prices():
    """Design §4 rule 5: a 40-trial sweep matching eight machines at four
    prices is the point, not a degradation. And rule 4: every fill executes at
    the HOST'S ask, never at the bid and never at a midpoint."""
    plan = mk.match_bid(
        max_zc_per_hour=420,
        tasks_wanted=6,
        asks=[
            _ask("a", 300, rate=0.98, cap=2),
            _ask("b", 400, rate=0.99, cap=2),
            _ask("c", 410, rate=0.99, cap=2),
        ],
    )
    assert [(f.listing_id, f.tasks, f.agreed_zc_per_hour) for f in plan.fills] == [
        ("a", 2, 300), ("b", 2, 400), ("c", 2, 410),
    ]
    assert plan.tasks_filled == 6 and plan.tasks_unfilled == 0


def test_an_unfilled_remainder_is_reported_not_raised():
    """A bid that bought four of forty tasks bought four. The console says so
    and the bid stays live for the rest."""
    plan = mk.match_bid(
        max_zc_per_hour=500, tasks_wanted=40,
        asks=[_ask("a", 300, rate=0.9, cap=4)],
    )
    assert plan.tasks_filled == 4
    assert plan.tasks_unfilled == 36


def test_ranking_is_on_effective_price_not_on_the_headline_ask():
    """The cheapest ask is not the cheapest result. A 300 ask at 0.60 goodput
    costs 500 per accepted task; a 400 ask at 1.0 costs 400 — so the dearer
    machine wins, and the buyer is never asked to do that arithmetic."""
    plan = mk.match_bid(
        max_zc_per_hour=450, tasks_wanted=1,
        asks=[_ask("cheap", 300, rate=0.60), _ask("dear", 400, rate=1.0)],
    )
    assert [f.listing_id for f in plan.fills] == ["dear"]
    # ...and the loser was priced out, not thrown out: raise the bid and it
    # matches. Reliability ranks; it never excludes.
    higher = mk.match_bid(
        max_zc_per_hour=600, tasks_wanted=2,
        asks=[_ask("cheap", 300, rate=0.60), _ask("dear", 400, rate=1.0)],
    )
    assert {f.listing_id for f in higher.fills} == {"cheap", "dear"}


def test_unproven_hosts_are_capped_by_share_and_the_rest_goes_to_evidence():
    """The cap is a bound on blast radius. Four unproven machines could win
    every task of this bid on ask alone; only a quarter of them may."""
    plan = mk.match_bid(
        max_zc_per_hour=1_000,
        tasks_wanted=8,
        asks=[
            _ask("new1", 100, rate=None, cap=8),
            _ask("new2", 110, rate=None, cap=8),
            _ask("known", 900, rate=1.0, cap=8),
        ],
    )
    assert plan.unproven_task_cap == 2
    assert plan.unproven_tasks == 2
    by_listing = {f.listing_id: f.tasks for f in plan.fills}
    assert by_listing == {"new1": 2, "known": 6}
    # Nothing anywhere reports a rate for the unproven host.
    assert all(f.unproven_host for f in plan.fills if f.listing_id == "new1")


def test_a_donated_host_matches_first_and_is_labelled():
    """M13 again, this time in the engine: a zero ask clears every bid,
    including a bid of zero, and the fill says `donated` rather than pricing
    the volunteer at a floor."""
    plan = mk.match_bid(
        max_zc_per_hour=0, tasks_wanted=2,
        asks=[_ask("free", 0, rate=0.9, cap=1), _ask("paid", 10, rate=1.0, cap=1)],
    )
    assert [f.listing_id for f in plan.fills] == ["free"]
    assert plan.fills[0].donated
    assert plan.tasks_unfilled == 1  # a zero bid buys nothing that costs


def test_workspace_demand_takes_priority_over_the_open_market():
    """M12. A member may list on the open market while still serving their
    team free; the open listing is what the machine does when its team is
    idle. So a machine its workspace needs right now is withheld from the book
    entirely rather than priced out of it — the gates come first, always."""
    plan = mk.match_bid(
        max_zc_per_hour=1_000, tasks_wanted=2,
        asks=[_ask("busy", 100, rate=1.0, machine="m-busy"),
              _ask("idle", 900, rate=1.0, machine="m-idle")],
        workspace_reserved={"m-busy"},
    )
    assert [f.listing_id for f in plan.fills] == ["idle"]


def test_matching_is_deterministic_when_prices_tie():
    """Equal asks are the ordinary case in a thin book. Without a stable
    tiebreak the same bid against the same book fills different machines on
    two runs, which looks like the engine changing its mind."""
    asks = [_ask("b", 300, rate=1.0), _ask("a", 300, rate=1.0)]
    first = mk.match_bid(max_zc_per_hour=300, tasks_wanted=1, asks=asks)
    second = mk.match_bid(max_zc_per_hour=300, tasks_wanted=1, asks=list(reversed(asks)))
    assert first.fills == second.fills


def test_a_match_cannot_jump_from_granted_to_settled():
    """Settlement is on ACCEPTED work, and no work can have been accepted
    under an entitlement nobody ever claimed. The state machine says so."""
    assert mk.is_legal_match_transition("granted", "claimed")
    assert mk.is_legal_match_transition("claimed", "settled")
    assert not mk.is_legal_match_transition("granted", "settled")
    # A granted match that nobody claimed ends as `expired`: no work, no
    # charge, and not an error.
    assert mk.is_legal_match_transition("granted", "expired")
    assert mk.MATCH_TRANSITIONS["settled"] == frozenset()


def test_a_filled_bid_becomes_live_again_when_its_matches_expire():
    """`filled` means entitlements exist, NOT that work happened — a match
    cannot make anybody run anything. So a buyer whose matched hosts all went
    offline still needs machines, and their bid has to be able to look for
    more. A terminal `filled` would strand them."""
    assert "partial" in mk.BID_TRANSITIONS["filled"]
    assert mk.BID_TRANSITIONS["cancelled"] == frozenset()


def test_the_rendered_book_reports_an_empty_side_as_none():
    """Same distinction as the recorded series, on the read path: a zero ask
    is a price somebody posted and an empty book is not."""
    empty = mk.book([], [])
    assert empty["best_ask_zc"] is None and empty["best_bid_zc"] is None

    live = mk.book(
        [_ask("free", 0), _ask("paid", 400, rate=0.5)],
        [{"max_zc_per_hour": 900}],
    )
    assert live["best_ask_zc"] == 0
    assert [row["label"] for row in live["asks"]] == ["donated", "priced"]
    # 400 at a 0.5 acceptance rate costs 800 per accepted result, and the book
    # says so rather than making the buyer do the arithmetic.
    assert live["asks"][1]["effective_price_zc"] == 800
    assert live["asks"][0]["effective_price_zc"] == 0


def test_no_code_path_writes_contributions():
    """`contributions.py`: "It must never grow a `balance`, a `remaining`, or
    a `credits`." That rule stands, and this module obeys it literally —
    credits reference accepted work and never mutate the record of it.

    Checked against the SOURCE rather than trusted to review, because the
    tempting version of this feature is one extra column on a table that
    already counts the right things.
    """
    source = MODULE.read_text().lower()
    for verb in (r"insert\s+into\s+(public\.)?contributions",
                 r"update\s+(public\.)?contributions",
                 r"delete\s+from\s+(public\.)?contributions"):
        assert not re.search(verb, source), verb
    assert not re.search(r"alter\s+table\s+public\.contributions",
                         MIGRATION.read_text(), re.I)


def test_nothing_updates_or_deletes_a_ledger_entry():
    """Append-only in substance. A correction is a new compensating entry —
    which is what `adjustment` is reserved for — because a ledger you can edit
    is a ledger whose history is whatever the last writer believed."""
    source = MODULE.read_text().lower()
    assert not re.search(r"update\s+public\.credit_entries", source)
    assert not re.search(r"delete\s+from\s+public\.credit_entries", source)


# ---------------------------------------------------------------------------
# Fixtures. Everything below reaches the real, freshly migrated ephemeral
# Postgres from conftest.py.
# ---------------------------------------------------------------------------


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


def make_machine(db, owner_id, *, capabilities=None, status="active") -> str:
    machine_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into public.machines (id, owner_id, node_id, capabilities, status)"
            " values (%s::uuid, %s::uuid, %s, %s, %s)",
            (
                machine_id,
                owner_id,
                f"node-{machine_id}",
                Json(capabilities if capabilities is not None else {"gpus": [RTX_4090]}),
                status,
            ),
        )
    return machine_id


def asks_for(db, machine_id, *, klass="gpu-24gb", rate=None) -> list[mk.Ask]:
    """The book, narrowed to one machine.

    Not a convenience: this stands in for the placement gates, which run
    BEFORE the market and hand `match_bid` only the machines that may legally
    run this job's tasks. It also keeps each test's arithmetic about its own
    machine — the session Postgres is shared, so the real book carries every
    other test's listings, exactly as production's would carry every other
    host's.
    """
    rates = {(str(machine_id), klass): rate} if rate is not None else None
    return [
        ask
        for ask in mk.open_asks(db, klass, acceptance_rates=rates)
        if ask.machine_id == str(machine_id)
    ]


def entries_sum(db, owner_id, kind) -> int:
    with db.cursor() as cur:
        cur.execute(
            "select coalesce(sum(e.delta_zc), 0) as t from public.credit_entries e"
            "  join public.credit_accounts a on a.id = e.account_id"
            " where a.owner_id = %s::uuid and a.kind = %s",
            (owner_id, kind),
        )
        return int(cur.fetchone()["t"])


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------


def test_a_credit_request_trims_and_preserves_history(db):
    """Purpose whitespace is presentation noise, while decided requests must
    remain as an audit trail when a user submits a later request."""
    owner = make_user(db)

    first = mk.create_credit_request(
        db, owner, requested_zc=50_000, purpose="  Test a multi-GPU sweep  "
    )
    assert first["requested_zc"] == 50_000
    assert first["purpose"] == "Test a multi-GPU sweep"
    assert first["status"] == "pending"

    mk.decline_credit_request(db, str(first["id"]), admin_id=make_user(db))
    second = mk.create_credit_request(
        db, owner, requested_zc=25_000, purpose="Retry with a smaller sweep"
    )

    history = mk.list_credit_requests(db, owner)
    assert [str(row["id"]) for row in history] == [str(second["id"]), str(first["id"])]
    assert [row["status"] for row in history] == ["pending", "declined"]


@pytest.mark.parametrize("requested_zc", [0, -1, 1.5, "1", True, 2**63])
def test_a_credit_request_amount_must_be_a_positive_bigint(db, requested_zc):
    owner = make_user(db)

    with pytest.raises(mk.InvalidCreditRequest):
        mk.create_credit_request(
            db, owner, requested_zc=requested_zc, purpose="Run a test"
        )


@pytest.mark.parametrize("purpose", ["", "   ", "x" * 2_001])
def test_a_credit_request_purpose_must_trim_to_one_through_two_thousand_chars(
    db, purpose
):
    owner = make_user(db)

    with pytest.raises(mk.InvalidCreditRequest):
        mk.create_credit_request(db, owner, requested_zc=1, purpose=purpose)


def test_only_one_pending_credit_request_exists_per_user(db):
    owner = make_user(db)
    mk.create_credit_request(db, owner, requested_zc=10_000, purpose="First test")

    with pytest.raises(mk.PendingCreditRequestExists):
        mk.create_credit_request(db, owner, requested_zc=20_000, purpose="Second test")

    assert len(mk.list_credit_requests(db, owner)) == 1


def test_admin_credit_requests_include_profile_and_current_balances(db):
    owner = make_user(db)
    with db.cursor() as cur:
        cur.execute(
            "update public.profiles"
            "   set display_name = 'Ada', first_name = 'Ada', last_name = 'Lovelace',"
            "       company_name = 'Analytical Engines'"
            " where id = %s::uuid",
            (owner,),
        )
    mk.grant_starting_credits(db, owner)
    request = mk.create_credit_request(
        db, owner, requested_zc=30_000, purpose="Test compiler workloads"
    )

    rows = mk.list_admin_credit_requests(db, status="pending")
    row = next(item for item in rows if str(item["id"]) == str(request["id"]))

    assert row["display_name"] == "Ada"
    assert row["first_name"] == "Ada"
    assert row["last_name"] == "Lovelace"
    assert row["company_name"] == "Analytical Engines"
    assert row["spendable_zc"] == 10_000
    assert row["escrow_zc"] == 0


def test_admin_may_approve_less_than_requested(db):
    owner, admin = make_user(db), make_user(db)
    request = mk.create_credit_request(
        db, owner, requested_zc=50_000, purpose="Test a multi-GPU sweep"
    )

    decided = mk.approve_credit_request(
        db, str(request["id"]), admin_id=admin, approved_zc=20_000
    )

    assert decided["requested_zc"] == 50_000
    assert decided["approved_zc"] == 20_000
    assert decided["status"] == "approved"
    assert str(decided["decided_by"]) == admin
    assert mk.credit_balances(db, owner)["spendable_zc"] == 20_000
    with db.cursor() as cur:
        cur.execute(
            "select reason, ref_type, ref_id, delta_zc"
            "  from public.credit_entries where ref_type = 'credit_request'"
            "   and ref_id = %s",
            (str(request["id"]),),
        )
        entries = cur.fetchall()
    assert entries == [
        {
            "reason": "adjustment",
            "ref_type": "credit_request",
            "ref_id": str(request["id"]),
            "delta_zc": 20_000,
        }
    ]


@pytest.mark.parametrize("approved_zc", [0, -1, 1.5, "1", True, 2**63])
def test_approved_credit_amount_must_be_a_positive_bigint(db, approved_zc):
    owner, admin = make_user(db), make_user(db)
    request = mk.create_credit_request(
        db, owner, requested_zc=50_000, purpose="Test a multi-GPU sweep"
    )

    with pytest.raises(mk.InvalidCreditRequest):
        mk.approve_credit_request(
            db, str(request["id"]), admin_id=admin, approved_zc=approved_zc
        )

    assert mk.list_credit_requests(db, owner)[0]["status"] == "pending"
    assert mk.credit_balances(db, owner)["spendable_zc"] == 0


@pytest.mark.parametrize("decision", ["approve", "decline"])
def test_a_malformed_credit_request_id_uses_the_domain_error(db, decision):
    owner, admin = make_user(db), make_user(db)
    mk.create_credit_request(db, owner, requested_zc=10_000, purpose="Run a test")

    with pytest.raises(mk.CreditRequestNotPending):
        if decision == "approve":
            mk.approve_credit_request(
                db, "not-a-uuid", admin_id=admin, approved_zc=5_000
            )
        else:
            mk.decline_credit_request(db, "not-a-uuid", admin_id=admin)

    assert mk.list_credit_requests(db, owner)[0]["status"] == "pending"
    assert mk.credit_balances(db, owner)["spendable_zc"] == 0


def test_approval_rolls_back_adjustment_when_decision_update_fails(db):
    owner = make_user(db)
    request = mk.create_credit_request(
        db, owner, requested_zc=20_000, purpose="Test atomic approval"
    )
    request_id = str(request["id"])

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        mk.approve_credit_request(
            db,
            request_id,
            admin_id=str(uuid.uuid4()),
            approved_zc=12_000,
        )

    assert mk.list_credit_requests(db, owner)[0]["status"] == "pending"
    assert mk.credit_balances(db, owner) == {"spendable_zc": 0, "escrow_zc": 0}
    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.credit_entries"
            " where reason = 'adjustment' and ref_type = 'credit_request'"
            "   and ref_id = %s",
            (request_id,),
        )
        assert cur.fetchone()["n"] == 0


def test_a_credit_request_cannot_be_decided_twice(db):
    owner, admin = make_user(db), make_user(db)
    request = mk.create_credit_request(
        db, owner, requested_zc=40_000, purpose="Test a retry"
    )
    request_id = str(request["id"])
    mk.approve_credit_request(db, request_id, admin_id=admin, approved_zc=15_000)

    with pytest.raises(mk.CreditRequestNotPending):
        mk.approve_credit_request(db, request_id, admin_id=admin, approved_zc=15_000)
    with pytest.raises(mk.CreditRequestNotPending):
        mk.decline_credit_request(db, request_id, admin_id=admin)

    assert mk.credit_balances(db, owner)["spendable_zc"] == 15_000


def test_concurrent_credit_request_decisions_mint_once(postgres_dsn, db):
    owner, first_admin, second_admin = make_user(db), make_user(db), make_user(db)
    request = mk.create_credit_request(
        db, owner, requested_zc=40_000, purpose="Test concurrent review"
    )
    request_id = str(request["id"])
    barrier = threading.Barrier(2)
    results: list[dict] = []
    errors: list[BaseException] = []

    def approve(admin_id: str) -> None:
        conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        try:
            barrier.wait()
            results.append(
                dict(
                    mk.approve_credit_request(
                        conn, request_id, admin_id=admin_id, approved_zc=12_000
                    )
                )
            )
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            errors.append(exc)
        finally:
            conn.close()

    threads = [
        threading.Thread(target=approve, args=(first_admin,)),
        threading.Thread(target=approve, args=(second_admin,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], mk.CreditRequestNotPending)
    assert mk.credit_balances(db, owner)["spendable_zc"] == 12_000
    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.credit_entries"
            " where reason = 'adjustment' and ref_type = 'credit_request'"
            "   and ref_id = %s",
            (request_id,),
        )
        assert cur.fetchone()["n"] == 1


def test_declining_a_credit_request_moves_no_credits(db):
    owner, admin = make_user(db), make_user(db)
    request = mk.create_credit_request(
        db, owner, requested_zc=25_000, purpose="Test an optional experiment"
    )

    decided = mk.decline_credit_request(db, str(request["id"]), admin_id=admin)

    assert decided["status"] == "declined"
    assert decided["approved_zc"] is None
    assert mk.credit_balances(db, owner) == {"spendable_zc": 0, "escrow_zc": 0}
    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.credit_entries"
            " where ref_type = 'credit_request' and ref_id = %s",
            (str(request["id"]),),
        )
        assert cur.fetchone()["n"] == 0


def test_the_grant_is_one_time_and_the_index_is_what_makes_it_so(db):
    """M10: no refill. A refilling allowance would remove every reason to care
    what anything costs, so "one time" has to be a property of the table
    rather than a check the caller remembers to make."""
    owner = make_user(db)

    assert mk.grant_starting_credits(db, owner) == 10_000
    assert mk.balances(db, owner)["spendable"] == 10_000

    # Called again on every sign-in, deliberately: 0 means "already granted"
    # and is not an error.
    for _ in range(3):
        assert mk.grant_starting_credits(db, owner) == 0
    assert mk.balances(db, owner)["spendable"] == 10_000

    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.credit_entries e"
            "  join public.credit_accounts a on a.id = e.account_id"
            " where a.owner_id = %s::uuid and e.reason = 'grant'",
            (owner,),
        )
        assert cur.fetchone()["n"] == 1


def test_a_legacy_250_zc_grant_remains_untouched(db):
    """Changing the grant for new testers must not rewrite an append-only
    ledger entry already issued under the previous policy."""
    owner = make_user(db)
    account = mk.ensure_accounts(db, owner)["spendable"]
    assert mk.post(
        db,
        ref_type="account",
        ref_id=str(account["id"]),
        legs=[mk.Leg(str(account["id"]), 250_000, "grant")],
    ) == 1

    assert mk.grant_starting_credits(db, owner) == 0
    assert mk.credit_balances(db, owner)["spendable_zc"] == 250_000


def test_an_existing_ungranted_profile_does_not_receive_the_new_grant(db):
    """The launch boundary is profile creation, not first wallet visit.

    An older tester may never have opened the credits page and therefore may
    have no historical grant entry. That absence must not make the account look
    new after the policy launches.
    """
    owner = make_user(db)
    with db.cursor() as cur:
        cur.execute(
            "update public.profiles set starter_grant_eligible = false"
            " where id = %s::uuid",
            (owner,),
        )

    assert mk.grant_starting_credits(db, owner) == 0
    assert mk.credit_balances(db, owner) == {"spendable_zc": 0, "escrow_zc": 0}
    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.credit_entries e"
            " join public.credit_accounts a on a.id = e.account_id"
            " where a.owner_id = %s::uuid and e.reason = 'grant'",
            (owner,),
        )
        assert cur.fetchone()["n"] == 0


def test_a_stranger_has_a_zero_balance_and_not_an_error(db):
    """Zero is a real answer. Somebody who just signed up has no credits, and
    a field that disappears when it has nothing to say arrives at the console
    as `undefined` and draws as a blank tile that looks like a loading."""
    assert mk.balances(db, str(uuid.uuid4())) == {"spendable": 0, "escrow": 0}


def test_an_unbalanced_movement_is_refused_before_anything_is_written(db):
    """One unbalanced write and the ledger can never be audited again: from
    then on every check fails and none of them names the bad write."""
    owner = make_user(db)
    accounts = mk.ensure_accounts(db, owner)
    mk.grant_starting_credits(db, owner)

    with pytest.raises(mk.LedgerImbalance):
        mk.post(
            db, ref_type="attempt", ref_id="lease-x",
            legs=[mk.Leg(str(accounts["spendable"]["id"]), -100, "escrow_hold")],
        )
    with pytest.raises(mk.LedgerImbalance):
        mk.post(
            db, ref_type="attempt", ref_id="lease-y",
            legs=[
                mk.Leg(str(accounts["spendable"]["id"]), 100, "grant"),
                mk.Leg(str(accounts["escrow"]["id"]), -100, "escrow_hold"),
            ],
        )
    assert mk.balances(db, owner)["spendable"] == 10_000
    assert mk.verify_ledger(db) == []


def test_the_balance_equals_the_sum_of_its_entries_after_every_operation(db):
    """The invariant, walked through the whole lifecycle rather than asserted
    once at the end — a ledger that is only correct when you stop looking is
    not correct."""
    buyer, host = make_user(db), make_user(db)
    machine = make_machine(db, host)
    mk.grant_starting_credits(db, buyer)

    listing = mk.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=400
    )
    bid = mk.create_bid(
        db, job_id="job-1", owner_id=buyer, capability_class_name="gpu-24gb",
        max_zc_per_hour=500, tasks_wanted=1, est_task_seconds=3600,
    )
    plan = mk.match_bid(
        max_zc_per_hour=500, tasks_wanted=1,
        asks=asks_for(db, machine, rate=1.0),
    )
    matches = mk.grant_matches(db, bid_id=str(bid["id"]), plan=plan)
    assert mk.verify_ledger(db) == []

    match_id = str(matches[0]["id"])
    mk.hold_escrow_on_claim(db, match_id=match_id, lease_id="lease-1")
    assert mk.verify_ledger(db) == []

    mk.settle_accepted_work(
        db, match_id=match_id, lease_id="lease-1", accepted_seconds=1800
    )
    assert mk.verify_ledger(db) == []

    for kind in ("spendable", "escrow"):
        assert mk.balances(db, buyer)[kind] == entries_sum(db, buyer, kind)
        assert mk.balances(db, host)[kind] == entries_sum(db, host, kind)


def test_credits_are_conserved_everywhere_except_the_grant(db):
    """Every debit has a matching credit, so the sum of every live balance is
    exactly what has been minted — at any moment, whatever else the suite has
    done to this database."""
    buyer, host = make_user(db), make_user(db)
    machine = make_machine(db, host)
    mk.grant_starting_credits(db, buyer)

    listing = mk.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=1_000
    )
    bid = mk.create_bid(
        db, job_id="job-conserve", owner_id=buyer,
        capability_class_name="gpu-24gb", max_zc_per_hour=1_000,
        tasks_wanted=1, est_task_seconds=3600,
    )
    plan = mk.match_bid(max_zc_per_hour=1_000, tasks_wanted=1,
                        asks=asks_for(db, machine))
    match = mk.grant_matches(db, bid_id=str(bid["id"]), plan=plan)[0]
    mk.hold_escrow_on_claim(db, match_id=str(match["id"]), lease_id="lease-c")
    mk.settle_accepted_work(
        db, match_id=str(match["id"]), lease_id="lease-c", accepted_seconds=3600
    )

    totals = mk.ledger_totals(db)
    assert totals["minted_zc"] == totals["held_zc"]
    assert mk.verify_ledger(db) == []


def test_a_replayed_settlement_pays_exactly_once(db):
    """Idempotent by the unique index, not by the caller's discipline: a round
    callback can be retried and a driver can be restarted onto work already
    recorded. A credit ledger that double-counts fails silently and compounds
    — nothing errors, the numbers merely drift, and by then there is no way to
    tell a real settlement from a replayed one."""
    buyer, host = make_user(db), make_user(db)
    machine = make_machine(db, host)
    mk.grant_starting_credits(db, buyer)
    mk.create_listing(db, machine_id=machine, owner_id=host, ask_zc_per_hour=1_000)
    bid = mk.create_bid(
        db, job_id="job-2", owner_id=buyer, capability_class_name="gpu-24gb",
        max_zc_per_hour=1_000, tasks_wanted=1, est_task_seconds=3600,
    )
    plan = mk.match_bid(max_zc_per_hour=1_000, tasks_wanted=1,
                        asks=asks_for(db, machine))
    match = mk.grant_matches(db, bid_id=str(bid["id"]), plan=plan)[0]

    for _ in range(3):
        mk.hold_escrow_on_claim(db, match_id=str(match["id"]), lease_id="lease-2")
    assert mk.balances(db, buyer)["escrow"] == 1_000

    first = mk.settle_accepted_work(
        db, match_id=str(match["id"]), lease_id="lease-2", accepted_seconds=3600
    )
    again = mk.settle_accepted_work(
        db, match_id=str(match["id"]), lease_id="lease-2", accepted_seconds=3600
    )
    assert first["charged_zc"] == 1_000
    assert again["charged_zc"] == 0 and again["already_settled"]
    assert mk.balances(db, host)["spendable"] == 1_000
    assert mk.balances(db, buyer) == {"spendable": 9_000, "escrow": 0}


def test_escrow_is_held_on_claimed_and_never_on_granted(db):
    """Design §0.1. A grant costs a host nothing, so holding at grant would
    let a host who never shows up freeze a buyer's balance — a denial of
    service with no attacker required."""
    buyer, host = make_user(db), make_user(db)
    machine = make_machine(db, host)
    mk.grant_starting_credits(db, buyer)
    mk.create_listing(db, machine_id=machine, owner_id=host, ask_zc_per_hour=400)
    bid = mk.create_bid(
        db, job_id="job-3", owner_id=buyer, capability_class_name="gpu-24gb",
        max_zc_per_hour=500, tasks_wanted=1, est_task_seconds=3600,
    )
    plan = mk.match_bid(max_zc_per_hour=500, tasks_wanted=1,
                        asks=asks_for(db, machine))
    match = mk.grant_matches(db, bid_id=str(bid["id"]), plan=plan)[0]

    assert match["state"] == "granted"
    assert mk.balances(db, buyer) == {"spendable": 10_000, "escrow": 0}

    mk.hold_escrow_on_claim(db, match_id=str(match["id"]), lease_id="lease-3")
    assert mk.balances(db, buyer) == {"spendable": 9_600, "escrow": 400}
    with db.cursor() as cur:
        cur.execute("select state, claimed_at from public.matches where id = %s",
                    (match["id"],))
        row = cur.fetchone()
    assert row["state"] == "claimed" and row["claimed_at"] is not None


def test_a_match_nobody_claims_costs_nobody_anything(db):
    """The consequence of a pull system that the UI must show or buyers will
    read a match as a reservation: a matched host that never claims produces
    no work and no charge. `expired` is that outcome, and it is not an
    error."""
    buyer, host = make_user(db), make_user(db)
    machine = make_machine(db, host)
    mk.grant_starting_credits(db, buyer)
    mk.create_listing(db, machine_id=machine, owner_id=host, ask_zc_per_hour=400)
    bid = mk.create_bid(
        db, job_id="job-4", owner_id=buyer, capability_class_name="gpu-24gb",
        max_zc_per_hour=500, tasks_wanted=1, est_task_seconds=3600,
    )
    plan = mk.match_bid(max_zc_per_hour=500, tasks_wanted=1,
                        asks=asks_for(db, machine))
    match = mk.grant_matches(db, bid_id=str(bid["id"]), plan=plan)[0]

    assert mk.close_match(
        db, match_id=str(match["id"]), from_state="granted", to_state="expired"
    )
    assert mk.balances(db, buyer) == {"spendable": 10_000, "escrow": 0}
    assert mk.balances(db, host) == {"spendable": 0, "escrow": 0}


def test_a_failed_attempt_refunds_the_buyer_and_earns_the_host_nothing(db):
    """Hard rule 4, and the market's most distinctive property (design §3.1).
    Every other compute market bills for time and pushes interruption risk
    onto the buyer; this one declines to, because the runtime knows the
    difference between attempted and accepted work."""
    buyer, host = make_user(db), make_user(db)
    machine = make_machine(db, host)
    mk.grant_starting_credits(db, buyer)
    mk.create_listing(db, machine_id=machine, owner_id=host, ask_zc_per_hour=1_000)
    bid = mk.create_bid(
        db, job_id="job-5", owner_id=buyer, capability_class_name="gpu-24gb",
        max_zc_per_hour=1_000, tasks_wanted=1, est_task_seconds=3600,
    )
    plan = mk.match_bid(max_zc_per_hour=1_000, tasks_wanted=1,
                        asks=asks_for(db, machine))
    match = mk.grant_matches(db, bid_id=str(bid["id"]), plan=plan)[0]

    mk.hold_escrow_on_claim(db, match_id=str(match["id"]), lease_id="lease-5")
    assert mk.balances(db, buyer)["escrow"] == 1_000

    refund = mk.refund_unaccepted_work(
        db, match_id=str(match["id"]), lease_id="lease-5"
    )
    assert refund["refunded_zc"] == 1_000
    assert mk.balances(db, buyer) == {"spendable": 10_000, "escrow": 0}
    assert mk.balances(db, host) == {"spendable": 0, "escrow": 0}
    assert mk.held_for_lease(db, owner_id=buyer, lease_id="lease-5") == 0
    assert mk.verify_ledger(db) == []


def test_work_shorter_than_its_estimate_releases_the_remainder(db):
    """An estimate that was generous is not a windfall for anybody. The host
    is paid for what was accepted and the rest goes back."""
    buyer, host = make_user(db), make_user(db)
    machine = make_machine(db, host)
    mk.grant_starting_credits(db, buyer)
    mk.create_listing(db, machine_id=machine, owner_id=host, ask_zc_per_hour=1_000)
    bid = mk.create_bid(
        db, job_id="job-6", owner_id=buyer, capability_class_name="gpu-24gb",
        max_zc_per_hour=1_000, tasks_wanted=1, est_task_seconds=3600,
    )
    plan = mk.match_bid(max_zc_per_hour=1_000, tasks_wanted=1,
                        asks=asks_for(db, machine))
    match = mk.grant_matches(db, bid_id=str(bid["id"]), plan=plan)[0]
    mk.hold_escrow_on_claim(db, match_id=str(match["id"]), lease_id="lease-6")

    result = mk.settle_accepted_work(
        db, match_id=str(match["id"]), lease_id="lease-6", accepted_seconds=360
    )
    assert result == {"charged_zc": 100, "released_zc": 900, "already_settled": False}
    assert mk.balances(db, buyer) == {"spendable": 9_900, "escrow": 0}
    assert mk.balances(db, host)["spendable"] == 100

    # The audit answer to "why was I charged this?", straight from the entries.
    assert mk.lease_settlement(db, "lease-6") == {
        "held_zc": 1_000,
        "charged_zc": 100,
        "earned_zc": 100,
        "released_zc": 900,
        "refunded_zc": 0,
    }


def test_a_buyer_who_cannot_cover_a_hold_is_refused_by_the_database(db):
    """`balance_zc >= 0` is what raises, so there is no window between reading
    a balance and spending it in which two claims are both affordable."""
    buyer, host = make_user(db), make_user(db)
    machine = make_machine(db, host)
    mk.ensure_accounts(db, buyer)  # no grant: this buyer has nothing
    mk.create_listing(db, machine_id=machine, owner_id=host, ask_zc_per_hour=1_000)
    bid = mk.create_bid(
        db, job_id="job-7", owner_id=buyer, capability_class_name="gpu-24gb",
        max_zc_per_hour=1_000, tasks_wanted=1, est_task_seconds=3600,
    )
    plan = mk.match_bid(max_zc_per_hour=1_000, tasks_wanted=1,
                        asks=asks_for(db, machine))
    match = mk.grant_matches(db, bid_id=str(bid["id"]), plan=plan)[0]

    assert not mk.can_cover(0, zc_per_hour=1_000, seconds=3600, tasks=1)
    with pytest.raises(mk.InsufficientCredits):
        mk.hold_escrow_on_claim(db, match_id=str(match["id"]), lease_id="lease-7")

    # Nothing half-happened: no hold, and the match did not move to claimed.
    assert mk.balances(db, buyer) == {"spendable": 0, "escrow": 0}
    with db.cursor() as cur:
        cur.execute("select state from public.matches where id = %s", (match["id"],))
        assert cur.fetchone()["state"] == "granted"
    assert mk.verify_ledger(db) == []


def test_the_invariant_survives_writes_racing_on_one_account(postgres_dsn, db):
    """Balances move by `balance_zc = balance_zc + %s` in SQL, never
    read-modify-write in Python, and the accounts are locked in id order. The
    window a read-then-write leaves open is exactly where two concurrent holds
    both see the old balance and both succeed."""
    buyer, host = make_user(db), make_user(db)
    machine = make_machine(db, host)
    mk.grant_starting_credits(db, buyer)
    mk.create_listing(db, machine_id=machine, owner_id=host, ask_zc_per_hour=1_000)
    bid = mk.create_bid(
        db, job_id="job-race", owner_id=buyer, capability_class_name="gpu-24gb",
        max_zc_per_hour=1_000, tasks_wanted=8, est_task_seconds=3600,
    )
    plan = mk.match_bid(max_zc_per_hour=1_000, tasks_wanted=8,
                        asks=asks_for(db, machine))
    match = mk.grant_matches(db, bid_id=str(bid["id"]), plan=plan)[0]
    match_id = str(match["id"])

    errors: list[BaseException] = []

    def claim_and_settle(n: int) -> None:
        conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        try:
            lease = f"lease-race-{n}"
            mk.hold_escrow_on_claim(conn, match_id=match_id, lease_id=lease)
            mk.settle_accepted_work(
                conn, match_id=match_id, lease_id=lease, accepted_seconds=360
            )
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=claim_and_settle, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert mk.verify_ledger(db) == []
    # Eight tasks, six minutes each, at 1 ZC/hour: 800 millicredits, and every
    # over-held millicredit back where it came from.
    assert mk.balances(db, host)["spendable"] == 800
    assert mk.balances(db, buyer) == {"spendable": 9_200, "escrow": 0}


# ---------------------------------------------------------------------------
# Listings, bids, the book
# ---------------------------------------------------------------------------


def test_a_listing_takes_its_class_from_the_machine_and_not_from_the_host(db):
    """There is no capability_class argument and there will not be one."""
    host = make_user(db)
    small = make_machine(db, host, capabilities={"gpus": [RTX_3070]})
    listing = mk.create_listing(
        db, machine_id=small, owner_id=host, ask_zc_per_hour=10_000
    )
    assert listing["capability_class"] == "gpu-8gb"
    assert "capability_class" not in mk.create_listing.__code__.co_varnames


def test_a_machine_whose_gpus_report_no_memory_cannot_be_listed(db):
    """Refusing is visible and has one obvious fix. Filing it under a guess is
    a promise about VRAM made from no reading at all."""
    host = make_user(db)
    machine = make_machine(db, host, capabilities={"gpus": [{"index": 0}]})
    with pytest.raises(mk.UnclassifiableMachine):
        mk.create_listing(db, machine_id=machine, owner_id=host, ask_zc_per_hour=100)


def test_a_machine_may_hold_only_one_open_listing(db):
    """Two open listings for one machine are two prices for the same hour with
    no rule for which one a match executes at. That is an ambiguity, not more
    supply."""
    host = make_user(db)
    machine = make_machine(db, host)
    mk.create_listing(db, machine_id=machine, owner_id=host, ask_zc_per_hour=400)
    with pytest.raises(mk.AlreadyListed):
        mk.create_listing(db, machine_id=machine, owner_id=host, ask_zc_per_hour=300)

    # ...but pausing one frees the machine to be listed again at a new price.
    listing = mk.listings_for_owner(db, host)[0]
    assert mk.pause_listing(db, listing_id=str(listing["id"]), owner_id=host)
    mk.create_listing(db, machine_id=machine, owner_id=host, ask_zc_per_hour=300)


def test_another_user_sees_nothing_and_cannot_move_anything(db):
    """Owner scoping folded into the query, never applied afterwards in
    Python. "Not yours" and "not there" answer the same thing, so a guesser
    cannot learn that an id is real."""
    host, stranger = make_user(db), make_user(db)
    machine = make_machine(db, host)
    listing = mk.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=400
    )

    assert mk.listings_for_owner(db, stranger) == []
    assert not mk.pause_listing(db, listing_id=str(listing["id"]), owner_id=stranger)
    assert not mk.withdraw_listing(db, listing_id=str(listing["id"]), owner_id=stranger)
    with pytest.raises(LookupError):
        mk.create_listing(
            db, machine_id=machine, owner_id=stranger, ask_zc_per_hour=1
        )
    # Still open, still the owner's, still at the owner's price.
    assert mk.listings_for_owner(db, host)[0]["state"] == "open"

    buyer = make_user(db)
    bid = mk.create_bid(
        db, job_id="job-scope", owner_id=buyer, capability_class_name="gpu-24gb",
        max_zc_per_hour=500, tasks_wanted=1, est_task_seconds=60,
    )
    assert mk.bids_for_owner(db, stranger) == []
    assert not mk.cancel_bid(db, bid_id=str(bid["id"]), owner_id=stranger)
    assert mk.cancel_bid(db, bid_id=str(bid["id"]), owner_id=buyer)


def test_a_withdrawn_listing_never_reopens(db):
    """Terminal, so a machine taken back off the market cannot be quietly
    returned to the book by a stale tab that still thinks it owns it."""
    host = make_user(db)
    machine = make_machine(db, host)
    listing = mk.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=400
    )
    assert mk.withdraw_listing(db, listing_id=str(listing["id"]), owner_id=host)
    with pytest.raises(mk.IllegalTransition):
        mk.set_listing_state(
            db, listing_id=str(listing["id"]), owner_id=host,
            from_state="withdrawn", to_state="open",
        )


def test_a_revoked_machine_leaves_the_book(db):
    """A listing whose machine was revoked is not supply. Leaving it in would
    let a withdrawn host keep winning matches nobody can claim."""
    host = make_user(db)
    machine = make_machine(db, host)
    mk.create_listing(db, machine_id=machine, owner_id=host, ask_zc_per_hour=400)
    assert len(mk.open_asks(db, "gpu-24gb")) >= 1

    with db.cursor() as cur:
        cur.execute(
            "update public.machines set status = 'revoked' where id = %s::uuid",
            (machine,),
        )
    assert all(a.machine_id != machine for a in mk.open_asks(db, "gpu-24gb"))


def test_an_ask_with_no_recorded_history_arrives_unproven(db):
    """`open_asks` will not invent a rate, and a caller that passes no rates
    gets a book of unproven hosts rather than a book of perfect ones."""
    host = make_user(db)
    machine = make_machine(db, host)
    mk.create_listing(db, machine_id=machine, owner_id=host, ask_zc_per_hour=400)
    ask = next(a for a in mk.open_asks(db, "gpu-24gb") if a.machine_id == machine)
    assert ask.acceptance_rate is None and ask.unproven


def test_an_ask_carries_the_median_the_caller_measured_and_nothing_else(db):
    """The timing signal reaches the book the same way the rate does: from a
    mapping the caller built out of `metrics.acceptance_rates`, keyed by
    `(machine_id, capability_class)`, and never invented here.

    A machine absent from the mapping is UNMEASURED — None, not 0. The two
    are opposite claims ("nobody has timed this" against "it finishes
    instantly"), and under the `fastest` objective a 0 would put an untimed
    machine at the head of the book on the strength of no evidence at all.
    """
    host = make_user(db)
    timed = make_machine(db, host)
    untimed = make_machine(db, host)
    mk.create_listing(db, machine_id=timed, owner_id=host, ask_zc_per_hour=410)
    mk.create_listing(db, machine_id=untimed, owner_id=host, ask_zc_per_hour=420)

    book = mk.open_asks(
        db,
        "gpu-24gb",
        acceptance_rates={(str(timed), "gpu-24gb"): 0.9},
        median_seconds={(str(timed), "gpu-24gb"): 12.5},
    )
    by_machine = {a.machine_id: a for a in book}
    assert by_machine[timed].median_seconds == 12.5
    assert by_machine[timed].acceptance_rate == 0.9
    assert by_machine[untimed].median_seconds is None

    # A caller that passes no medians at all gets a book of unmeasured hosts,
    # exactly as it gets a book of unproven ones.
    plain = {a.machine_id: a for a in mk.open_asks(db, "gpu-24gb")}
    assert plain[timed].median_seconds is None


def test_a_bid_must_estimate_how_long_a_task_takes(db):
    """Not validation for its own sake: the hold is sized from this and the
    charge is capped at the hold, so a zero estimate is free compute for as
    long as the task cares to run."""
    buyer = make_user(db)
    with pytest.raises(ValueError):
        mk.create_bid(
            db, job_id="job-8", owner_id=buyer, capability_class_name="gpu-24gb",
            max_zc_per_hour=500, tasks_wanted=1, est_task_seconds=0,
        )


def test_a_bid_remembers_the_objective_it_was_matched_under(db):
    """Migration 0032. The order a book was walked in is an input to matching
    exactly as the cap is, and until this column existed it was the one input
    the row did not keep — so anything re-matching or re-explaining the bid
    later had to guess.

    Stored rather than re-derived from the job's config, because neither
    reader can honestly re-derive it: the repo's flashml.yaml may have been
    edited since submission, and a spilled class's bid was never derived from
    that config at all (`routing.plan_pool_routing` asks each later class for
    the remainder, not for the job).
    """
    buyer = make_user(db)
    bid = mk.create_bid(
        db, job_id="job-objective", owner_id=buyer,
        capability_class_name="gpu-24gb", max_zc_per_hour=500,
        tasks_wanted=1, est_task_seconds=3600, objective="fastest",
    )
    assert bid["objective"] == "fastest"


def test_a_bid_that_names_no_objective_records_the_engine_default(db):
    """`cheapest`, not flashml.yaml's `balanced`. A caller that names nothing
    gets the order this module ranks in when nobody names anything, and the
    row says so — the same reasoning the column default carries for every bid
    written before 0032 (see the migration's header)."""
    buyer = make_user(db)
    bid = mk.create_bid(
        db, job_id="job-objective-default", owner_id=buyer,
        capability_class_name="gpu-24gb", max_zc_per_hour=500,
        tasks_wanted=1, est_task_seconds=3600,
    )
    assert bid["objective"] == mk.DEFAULT_RANK_OBJECTIVE == "cheapest"


def test_a_bid_cannot_be_posted_under_an_objective_the_engine_lacks(db):
    """Refused at the Python boundary, with the three it could have been —
    the same message `objective_formula` and `match_bid` give — rather than
    as a bare CheckViolation from the table. The constraint in 0032 is the
    backstop for anything that reaches the row another way, not the first
    line of defence."""
    buyer = make_user(db)
    with pytest.raises(ValueError) as caught:
        mk.create_bid(
            db, job_id="job-objective-bogus", owner_id=buyer,
            capability_class_name="gpu-24gb", max_zc_per_hour=500,
            tasks_wanted=1, est_task_seconds=3600, objective="cheapest-ish",
        )
    assert "cheapest-ish" in str(caught.value)
    for name in mk.OBJECTIVES:
        assert name in str(caught.value)


# ---------------------------------------------------------------------------
# Price observations
# ---------------------------------------------------------------------------


def test_the_price_series_is_recorded_at_every_book_change(db):
    """Never derived afterwards. A series assembled retroactively from matches
    can only contain the prices that cleared, so it shows a market that never
    had a bad day; one recorded as it happened includes the observation where
    the best ask got worse because a machine went offline (M5)."""
    # `gpu-48gb` is untouched by every other test in this file, so the numbers
    # below are this book and nothing else — the same reason a real book is
    # per class rather than one market for every machine.
    host, buyer = make_user(db), make_user(db)
    a6000 = {"index": 0, "name": "NVIDIA RTX A6000", "memory_total_mb": 49140,
             "compute_capability": "8.6"}
    machine = make_machine(db, host, capabilities={"gpus": [a6000]})
    assert mk.price_series(db, "gpu-48gb") == []

    listing = mk.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=2_000
    )
    mk.create_bid(
        db, job_id="job-obs", owner_id=buyer, capability_class_name="gpu-48gb",
        max_zc_per_hour=2_500, tasks_wanted=1, est_task_seconds=60,
    )
    mk.pause_listing(db, listing_id=str(listing["id"]), owner_id=host)

    series = mk.price_series(db, "gpu-48gb")
    # Newest first: the pause, the bid, the listing. Every event that could
    # move a price left a point, and every point names what caused it.
    assert [row["cause"] for row in series] == ["listing", "bid", "listing"]

    paused, bid_point, listed = series
    assert (listed["best_ask_zc"], listed["open_listings"]) == (2_000, 1)
    assert (bid_point["best_bid_zc"], bid_point["open_bids"]) == (2_500, 1)
    # Supply left. The best ask is gone, and it is NULL rather than 0 — see
    # the test below for why that distinction is the whole point.
    assert (paused["best_ask_zc"], paused["open_listings"]) == (None, 0)


def test_an_empty_side_of_the_book_is_null_and_never_zero(db):
    """A zero ask is legal and means donated (M13), so "the best ask is free"
    and "nobody is selling" are different facts. Recording the empty book as 0
    would draw a line along the floor for a class nobody is selling in —
    indistinguishable from a class full of volunteers."""
    row = mk.record_price_observation(
        db, capability_class_name="gpu-80gb-hopper", cause="listing"
    )
    assert row["best_ask_zc"] is None
    assert row["best_bid_zc"] is None
    assert row["open_listings"] == 0 and row["open_bids"] == 0


def test_a_donated_listing_records_a_best_ask_of_zero(db):
    """The other half of the same distinction: 0 is a price somebody posted."""
    host = make_user(db)
    machine = make_machine(db, host, capabilities={"gpus": [A100_80]})
    mk.create_listing(db, machine_id=machine, owner_id=host, ask_zc_per_hour=0)
    row = mk.price_series(db, "gpu-80gb")[0]
    assert row["best_ask_zc"] == 0
    assert row["open_listings"] == 1


# ---------------------------------------------------------------------------
# Schema properties, checked against the applied database
# ---------------------------------------------------------------------------


def test_the_pull_gate_index_predicate_matches_the_policy(db):
    """The index hardcodes the live states where LIVE_MATCH_STATES says the
    same thing, and those two CAN drift. This is the only thing standing
    between a widened live set and a gate that silently stops using its
    index — the same pin `sandbox_sessions_unfinished_idx` has."""
    with db.cursor() as cur:
        cur.execute(
            "select indexdef from pg_indexes where schemaname = 'public'"
            "   and indexname = 'matches_live_for_machine_idx'"
        )
        row = cur.fetchone()
    assert row is not None, "migration 0018 did not apply"
    definition = row["indexdef"].lower()
    for state in mk.LIVE_MATCH_STATES:
        assert f"'{state}'" in definition, state


def test_rls_is_enabled_with_no_policies_on_every_marketplace_table(db):
    """RLS with ZERO policies is what denies every role but the owner and
    BYPASSRLS. The database is API-only; a policy with any role name — or one
    written without a `to` clause, which defaults to `public` — reopens direct
    access just as effectively."""
    tables = ("credit_accounts", "credit_entries", "listings", "bids",
              "matches", "price_observations")
    with db.cursor() as cur:
        cur.execute(
            "select relname, relrowsecurity from pg_class"
            " where relname = any(%s) and relnamespace = 'public'::regnamespace",
            (list(tables),),
        )
        rows = {r["relname"]: r["relrowsecurity"] for r in cur.fetchall()}
        assert rows == {t: True for t in tables}

        cur.execute(
            "select count(*) as n from pg_policies"
            " where schemaname = 'public' and tablename = any(%s)",
            (list(tables),),
        )
        assert cur.fetchone()["n"] == 0


def test_the_ledger_is_millicredits_as_bigint_with_no_float_anywhere(db):
    """Checked against the applied schema rather than the file that was typed:
    a migration that failed to apply would leave every test above passing
    against a database that does not exist."""
    with db.cursor() as cur:
        cur.execute(
            "select table_name, column_name, data_type"
            "  from information_schema.columns"
            " where table_schema = 'public'"
            "   and table_name in ('credit_accounts', 'credit_entries',"
            "                      'listings', 'bids', 'matches',"
            "                      'price_observations')"
        )
        columns = {(r["table_name"], r["column_name"]): r["data_type"]
                   for r in cur.fetchall()}
    assert columns[("credit_accounts", "balance_zc")] == "bigint"
    assert columns[("credit_entries", "delta_zc")] == "bigint"
    assert columns[("listings", "ask_zc_per_hour")] == "bigint"
    assert columns[("bids", "max_zc_per_hour")] == "bigint"
    assert columns[("matches", "agreed_zc_per_hour")] == "bigint"
    assert not [
        key for key, kind in columns.items()
        if kind in ("real", "double precision", "numeric")
    ]


def test_a_balance_cannot_go_below_zero_even_by_hand(db):
    """The overdraft guard is a database constraint, not a Python check that
    only appears to work."""
    owner = make_user(db)
    accounts = mk.ensure_accounts(db, owner)
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.cursor() as cur:
            cur.execute(
                "update public.credit_accounts set balance_zc = -1 where id = %s",
                (accounts["spendable"]["id"],),
            )
    db.rollback()
