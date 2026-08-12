"""The hop from an attempt's terminal outcome to the credit ledger.

`marketplace.py` has always SUPPORTED hard rule 4 — a buyer is never charged
for work that was not accepted — and until 2026-08-11 nothing ENFORCED it:
`record_attempt_failure` and `reconcile_expired_attempts` resolved an attempt
and the escrow held against its lease stayed held for ever. The tests here are
about that hop, and each one names the property whose failure moves money that
should not have moved (or fails to move money that should):

- **A refund fires on the outcome write, not on somebody remembering.** Both
  resolution paths — the fail hop this API is told about, and the expiry it
  has to infer — return the buyer's credits.
- **Exactly once, by the unique index and nothing else.** The realistic double
  fire is an INFERRED `expired` later corrected to an OBSERVED `failed`: the
  hop runs twice, and `credit_entries` is what makes the second one free.
- **A ledger failure never costs the attempt outcome.** The outcome is what
  every reliability number divides by; a credit movement is idempotently
  retryable. Losing the first to protect the second is the wrong trade, and
  the savepoint is what makes it unnecessary.
- **The accepted path settles in the same statement that takes the credit.**
  One event, one transaction: an attempt recorded as accepted with the buyer's
  escrow still held is a charge nobody can explain.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Json

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import marketplace as mk

RTX_4090 = {
    "index": 0,
    "name": "NVIDIA GeForce RTX 4090",
    "memory_total_mb": 24564,
    "compute_capability": "8.9",
}


# ---------------------------------------------------------------------------
# fixtures — the real, freshly migrated ephemeral Postgres from conftest.py
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


def make_machine(db, owner_id, *, capabilities=None) -> str:
    machine_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into public.machines (id, owner_id, node_id, capabilities, status)"
            " values (%s::uuid, %s::uuid, %s, %s, 'active')",
            (
                machine_id,
                owner_id,
                f"node-{machine_id}",
                Json(capabilities if capabilities is not None else {"gpus": [RTX_4090]}),
            ),
        )
    return machine_id


def entitled(db, *, job_id: str, ask: int = 1_000, est_seconds: int = 3600):
    """A buyer, a host, and a claimed entitlement over one job.

    The whole marketplace chain, because the hop under test only exists for an
    attempt pulled under one: list, bid, match, and hold escrow on the claim.
    Returns everything a test needs to assert about afterwards.
    """
    buyer, host = make_user(db), make_user(db)
    machine = make_machine(db, host)
    mk.grant_starting_credits(db, buyer)
    mk.create_listing(db, machine_id=machine, owner_id=host, ask_zc_per_hour=ask)
    bid = mk.create_bid(
        db,
        job_id=job_id,
        owner_id=buyer,
        capability_class_name="gpu-24gb",
        max_zc_per_hour=ask,
        tasks_wanted=1,
        est_task_seconds=est_seconds,
    )
    asks = [
        a
        for a in mk.open_asks(db, "gpu-24gb")
        if a.machine_id == str(machine)
    ]
    plan = mk.match_bid(max_zc_per_hour=ask, tasks_wanted=1, asks=asks)
    match = mk.grant_matches(db, bid_id=str(bid["id"]), plan=plan)[0]
    return {
        "buyer": buyer,
        "host": host,
        "machine": machine,
        "match_id": str(match["id"]),
        "job_id": job_id,
    }


def refund_legs(db, lease_id: str) -> int:
    """How many ledger entries record a refund of this lease. Two per refund
    (a movement has two legs), so four would be a double refund."""
    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.credit_entries"
            " where reason = 'escrow_refund' and ref_type = 'attempt'"
            "   and ref_id = %s",
            (lease_id,),
        )
        return int(cur.fetchone()["n"])


def outcome_of(db, lease_id: str) -> str | None:
    with db.cursor() as cur:
        cur.execute(
            "select outcome from public.attempts where lease_id = %s", (lease_id,)
        )
        row = cur.fetchone()
    return None if row is None else row["outcome"]


# ---------------------------------------------------------------------------
# the refund fires on the outcome write
# ---------------------------------------------------------------------------


def test_a_reported_failure_refunds_the_buyer_without_anybody_asking(db):
    """Hard rule 4 as a consequence of resolving the attempt, not as a step a
    caller might forget. Before this, `POST /attempts/{lease}/fail` wrote the
    outcome and left the hold in place for ever."""
    lease = f"lease-{uuid.uuid4().hex[:12]}"
    world = entitled(db, job_id=f"job-{uuid.uuid4().hex[:8]}")
    dbmod.record_attempt(
        db,
        lease_id=lease,
        machine_id=world["machine"],
        job_id=world["job_id"],
        task_id="t-1",
    )
    mk.hold_escrow_on_claim(db, match_id=world["match_id"], lease_id=lease)
    assert mk.balances(db, world["buyer"]) == {
        "spendable": mk.STARTING_GRANT_ZC - 1_000,
        "escrow": 1_000,
    }

    assert dbmod.record_attempt_failure(
        db, lease_id=lease, machine_id=world["machine"]
    ) is True

    assert outcome_of(db, lease) == "failed"
    assert mk.balances(db, world["buyer"]) == {
        "spendable": mk.STARTING_GRANT_ZC,
        "escrow": 0,
    }
    # The host earns nothing. That is not a penalty — it is the property that
    # lets this market decline to bill for time at all.
    assert mk.balances(db, world["host"]) == {"spendable": 0, "escrow": 0}
    assert mk.held_for_lease(db, owner_id=world["buyer"], lease_id=lease) == 0
    assert refund_legs(db, lease) == 2
    assert mk.verify_ledger(db) == []


def test_an_expired_attempt_refunds_the_buyer_too(db):
    """The event this product exists to survive: a machine unplugged mid-task
    tells nobody, so the coordinator's own lease deadline is the only evidence
    the attempt is over. The buyer must not be billed for it either."""
    lease = f"lease-{uuid.uuid4().hex[:12]}"
    world = entitled(db, job_id=f"job-{uuid.uuid4().hex[:8]}")
    dbmod.record_attempt(
        db,
        lease_id=lease,
        machine_id=world["machine"],
        job_id=world["job_id"],
        task_id="t-1",
        deadline=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    mk.hold_escrow_on_claim(db, match_id=world["match_id"], lease_id=lease)
    assert mk.balances(db, world["buyer"])["escrow"] == 1_000

    assert dbmod.reconcile_expired_attempts(db) >= 1

    assert outcome_of(db, lease) == "expired"
    assert mk.balances(db, world["buyer"]) == {
        "spendable": mk.STARTING_GRANT_ZC,
        "escrow": 0,
    }
    assert mk.balances(db, world["host"]) == {"spendable": 0, "escrow": 0}
    assert refund_legs(db, lease) == 2
    assert mk.verify_ledger(db) == []


def test_an_inferred_expiry_corrected_to_a_failure_refunds_exactly_once(db):
    """The realistic double fire, end to end. An INFERRED `expired` may be
    corrected by an OBSERVED `failed` — the coordinator accepting the report is
    stronger evidence than a deadline having passed — so the hop genuinely runs
    twice for one lease. `credit_entries`' unique index is what makes the
    second one free, and it is the ONLY guard: there is no "have we refunded
    this?" column, because a second count of something the entries already know
    would eventually disagree with them."""
    lease = f"lease-{uuid.uuid4().hex[:12]}"
    world = entitled(db, job_id=f"job-{uuid.uuid4().hex[:8]}")
    dbmod.record_attempt(
        db,
        lease_id=lease,
        machine_id=world["machine"],
        job_id=world["job_id"],
        task_id="t-1",
        deadline=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    mk.hold_escrow_on_claim(db, match_id=world["match_id"], lease_id=lease)

    dbmod.reconcile_expired_attempts(db)
    after_first = mk.balances(db, world["buyer"])
    assert after_first == {"spendable": mk.STARTING_GRANT_ZC, "escrow": 0}

    # The observation lands late and corrects the inference. The outcome moves;
    # the money does not move again.
    assert dbmod.record_attempt_failure(
        db, lease_id=lease, machine_id=world["machine"]
    ) is True

    assert outcome_of(db, lease) == "failed"
    assert mk.balances(db, world["buyer"]) == after_first
    assert refund_legs(db, lease) == 2
    assert mk.lease_settlement(db, lease)["refunded_zc"] == 1_000
    assert mk.verify_ledger(db) == []


def test_calling_the_refund_again_by_hand_credits_nothing(db):
    """The same property from the other side: whatever reaches
    `refund_unaccepted_work` for a lease already refunded gets 0 back, not a
    second credit and not an error."""
    lease = f"lease-{uuid.uuid4().hex[:12]}"
    world = entitled(db, job_id=f"job-{uuid.uuid4().hex[:8]}")
    dbmod.record_attempt(
        db,
        lease_id=lease,
        machine_id=world["machine"],
        job_id=world["job_id"],
        task_id="t-1",
    )
    mk.hold_escrow_on_claim(db, match_id=world["match_id"], lease_id=lease)
    dbmod.record_attempt_failure(db, lease_id=lease, machine_id=world["machine"])

    again = mk.refund_unaccepted_work(
        db, match_id=world["match_id"], lease_id=lease
    )
    assert again == {"refunded_zc": 0}
    assert mk.balances(db, world["buyer"]) == {
        "spendable": mk.STARTING_GRANT_ZC,
        "escrow": 0,
    }
    assert refund_legs(db, lease) == 2


def test_a_second_fail_report_moves_neither_the_outcome_nor_the_money(db):
    """An agent's retry describes one failure. The outcome write is what
    refuses the second call, so the ledger is never even reached."""
    lease = f"lease-{uuid.uuid4().hex[:12]}"
    world = entitled(db, job_id=f"job-{uuid.uuid4().hex[:8]}")
    dbmod.record_attempt(
        db,
        lease_id=lease,
        machine_id=world["machine"],
        job_id=world["job_id"],
        task_id="t-1",
    )
    mk.hold_escrow_on_claim(db, match_id=world["match_id"], lease_id=lease)

    assert dbmod.record_attempt_failure(
        db, lease_id=lease, machine_id=world["machine"]
    ) is True
    assert dbmod.record_attempt_failure(
        db, lease_id=lease, machine_id=world["machine"]
    ) is False
    assert refund_legs(db, lease) == 2
    assert mk.balances(db, world["buyer"]) == {
        "spendable": mk.STARTING_GRANT_ZC,
        "escrow": 0,
    }


# ---------------------------------------------------------------------------
# a ledger failure never costs the outcome
# ---------------------------------------------------------------------------


def test_a_refund_that_fails_does_not_lose_the_failure(db, monkeypatch):
    """The trade this makes, stated as a test: the attempt outcome survives and
    the credit movement is left recoverable, never the other way round.

    The fake fails with a REAL database error rather than a Python one, which
    is the case that matters: a failed statement poisons the surrounding
    transaction, so nothing after it can commit unless the failure was confined
    to a savepoint. If `_close_out_attempt_money` ever stops opening one, this
    test fails with the outcome missing entirely — which is exactly the
    regression it is here to catch.
    """
    lease = f"lease-{uuid.uuid4().hex[:12]}"
    world = entitled(db, job_id=f"job-{uuid.uuid4().hex[:8]}")
    dbmod.record_attempt(
        db,
        lease_id=lease,
        machine_id=world["machine"],
        job_id=world["job_id"],
        task_id="t-1",
    )
    mk.hold_escrow_on_claim(db, match_id=world["match_id"], lease_id=lease)

    def poisoned(conn, **_kwargs):
        with conn.cursor() as cur:
            cur.execute("select 1 / 0")

    monkeypatch.setattr(dbmod.marketplacemod, "refund_unaccepted_work", poisoned)

    assert dbmod.record_attempt_failure(
        db, lease_id=lease, machine_id=world["machine"]
    ) is True

    # The outcome is written and committed…
    assert outcome_of(db, lease) == "failed"
    # …and the money is exactly where it was, i.e. still recoverable rather
    # than half-moved.
    assert mk.balances(db, world["buyer"]) == {
        "spendable": mk.STARTING_GRANT_ZC - 1_000,
        "escrow": 1_000,
    }
    assert refund_legs(db, lease) == 0
    assert mk.verify_ledger(db) == []

    # Recoverable means recoverable: the same hop, run again, completes.
    monkeypatch.undo()
    assert mk.refund_unaccepted_work(
        db, match_id=world["match_id"], lease_id=lease
    ) == {"refunded_zc": 1_000}
    assert mk.balances(db, world["buyer"]) == {
        "spendable": mk.STARTING_GRANT_ZC,
        "escrow": 0,
    }


def test_an_attempt_with_no_entitlement_behind_it_moves_no_money(db):
    """The ordinary case in this deployment, and it must cost nothing: a
    machine pulling work with no match behind it has no escrow, so the hop
    finds nothing and writes nothing. A movement of zero would record an event
    that did not happen."""
    lease = f"lease-{uuid.uuid4().hex[:12]}"
    host = make_user(db)
    machine = make_machine(db, host)
    dbmod.record_attempt(
        db,
        lease_id=lease,
        machine_id=machine,
        job_id=f"job-{uuid.uuid4().hex[:8]}",
        task_id="t-1",
    )

    assert dbmod.record_attempt_failure(
        db, lease_id=lease, machine_id=machine
    ) is True
    assert outcome_of(db, lease) == "failed"
    assert refund_legs(db, lease) == 0
    assert mk.lease_settlement(db, lease) == {
        "held_zc": 0,
        "charged_zc": 0,
        "earned_zc": 0,
        "released_zc": 0,
        "refunded_zc": 0,
    }


# ---------------------------------------------------------------------------
# the accepted path
# ---------------------------------------------------------------------------


def test_taking_the_credit_settles_the_lease_in_the_same_transaction(db):
    """Settlement is on ACCEPTED work, and this is the moment this API learns
    there was any. The host is paid for what was accepted, capped at what was
    held, and the remainder of a generous estimate goes back to the buyer."""
    lease = f"lease-{uuid.uuid4().hex[:12]}"
    world = entitled(db, job_id=f"job-{uuid.uuid4().hex[:8]}")
    dbmod.record_attempt(
        db,
        lease_id=lease,
        machine_id=world["machine"],
        job_id=world["job_id"],
        task_id="t-1",
    )
    mk.hold_escrow_on_claim(db, match_id=world["match_id"], lease_id=lease)

    credit = dbmod.claim_attempt_credit(
        db, lease_id=lease, machine_id=world["machine"]
    )
    assert credit is not None and credit["task_id"] == "t-1"
    assert outcome_of(db, lease) == "accepted"

    settlement = mk.lease_settlement(db, lease)
    assert settlement["held_zc"] == 1_000
    # A sub-second accepted task is charged for one second, never for none:
    # rounding down would make real accepted work free.
    assert settlement["charged_zc"] >= 1
    assert settlement["earned_zc"] == settlement["charged_zc"]
    assert settlement["released_zc"] == 1_000 - settlement["charged_zc"]
    assert settlement["refunded_zc"] == 0

    assert mk.balances(db, world["host"])["spendable"] == settlement["charged_zc"]
    assert mk.balances(db, world["buyer"])["escrow"] == 0
    assert mk.verify_ledger(db) == []


def test_a_repeated_completion_pays_the_host_once(db):
    """Two completions arriving together must not both come back with a row —
    and even if one did, the ledger would refuse the second payment."""
    lease = f"lease-{uuid.uuid4().hex[:12]}"
    world = entitled(db, job_id=f"job-{uuid.uuid4().hex[:8]}")
    dbmod.record_attempt(
        db,
        lease_id=lease,
        machine_id=world["machine"],
        job_id=world["job_id"],
        task_id="t-1",
    )
    mk.hold_escrow_on_claim(db, match_id=world["match_id"], lease_id=lease)

    assert dbmod.claim_attempt_credit(
        db, lease_id=lease, machine_id=world["machine"]
    ) is not None
    earned = mk.balances(db, world["host"])["spendable"]

    assert dbmod.claim_attempt_credit(
        db, lease_id=lease, machine_id=world["machine"]
    ) is None
    assert mk.balances(db, world["host"])["spendable"] == earned
    assert mk.verify_ledger(db) == []
