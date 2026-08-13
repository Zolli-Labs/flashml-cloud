"""``class_board`` — the compute-board row for one capability class.

Against a real, freshly migrated Postgres like `test_marketplace.py`, because
the properties pinned here are about what several repository reads compose
into together, not about any one of them in isolation:

- **null is never 0.** An unpriced class is unpriced, not free — `last_zc`
  and `median_ask_zc` stay `None` until a real ask exists, even though
  `depth` (a genuine count of rows) is correctly `0` at the same moment.
  Those are different facts and this file keeps them different.
- **`median_ask_zc` is a live order statistic of the REAL book** — the same
  one `open_asks`/`match_bid` walk, filtered to machines that are still
  `status = 'active'` — not a raw scan of `public.listings` that would keep
  counting a listing whose machine was since revoked.
- **`last_zc`/`depth` are read from ONE recorded observation row**, on
  purpose, and do NOT chase a live recount the way `median_ask_zc` does —
  see `_record_observation_locked`'s own docstring for why splitting them
  across two live queries would be worse than reading a slightly older pair.
- **`change_zc` is null with no 24h-old pair, never a bare 0**, and a null
  point in between (a withdrawal has no best ask) must not break the walk
  back to a point that IS priced.

Each test below claims a capability class no other test in this suite lists
a machine into or bids in — `cpu-large` and `gpu-16gb` — and each is used by
exactly ONE test function here, because the ephemeral Postgres is one shared
session-scoped database (see `conftest.py`): every book is real state that
persists across tests exactly as it would in production, so two tests
sharing a class would corrupt each other's counts. `test_marketplace.py`
documents the same discipline for `gpu-48gb`.
"""
from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Json

from flashml_cloud_api import marketplace as mk

#: A 16GB card as a driver actually reports it (matches the reading
#: `test_router_estimator.py` uses for the same floor).
GPU_16GB = {"index": 0, "name": "NVIDIA GeForce RTX 4060 Ti", "memory_total_mb": 16376,
            "compute_capability": "8.9"}


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


def test_class_board_starts_null_then_tracks_a_live_book_ignoring_a_revoked_machine(db):
    """A class nobody has ever listed into has no price to report — `depth`
    is a real, honest 0 (a real count of real rows), but `last_zc` and
    `median_ask_zc` stay `None`: "nobody is selling" and "the price is free"
    are different facts and must not collapse into the same number.

    Once three asks exist, the median is the true middle ask; revoking the
    priciest machine's owner — WITHOUT any new listing event to write a
    fresh observation — must still move the median, because `open_asks`
    (which `class_board` now reuses instead of a raw `public.listings` scan)
    drops a revoked machine's listing on every call. `last_zc`/`depth`,
    sourced from the frozen observation row instead, correctly do NOT move:
    nothing re-recorded the book.
    """
    empty = mk.class_board(db, "cpu-large")
    assert empty["capability_class"] == "cpu-large"
    assert empty["last_zc"] is None
    assert empty["median_ask_zc"] is None
    assert empty["depth"] == 0
    assert empty["change_zc"] is None
    assert empty["history"] == []

    host = make_user(db)
    cheap = make_machine(db, host, capabilities={"cpu_cores": 8})
    middle = make_machine(db, host, capabilities={"cpu_cores": 12})
    priciest = make_machine(db, host, capabilities={"cpu_cores": 16})

    mk.create_listing(db, machine_id=cheap, owner_id=host, ask_zc_per_hour=1_000)
    mk.create_listing(db, machine_id=middle, owner_id=host, ask_zc_per_hour=2_000)
    listing3 = mk.create_listing(
        db, machine_id=priciest, owner_id=host, ask_zc_per_hour=3_000
    )
    assert listing3["capability_class"] == "cpu-large"

    board = mk.class_board(db, "cpu-large")
    assert board["last_zc"] == 1_000  # the cheapest ask was always the best
    assert board["depth"] == 3
    assert board["median_ask_zc"] == 2_000  # odd count: the true middle ask
    assert len(board["history"]) == 3

    with db.cursor() as cur:
        cur.execute(
            "update public.machines set status = 'revoked' where id = %s::uuid",
            (priciest,),
        )

    revoked = mk.class_board(db, "cpu-large")
    # The recorded snapshot is untouched: revocation wrote no observation.
    assert revoked["last_zc"] == 1_000
    assert revoked["depth"] == 3
    # The median is NOT: it is read live, so it now reflects only the two
    # machines still able to actually clear a bid — 1000 and 2000.
    assert revoked["median_ask_zc"] == 1_500


def test_class_board_change_zc_skips_null_points_and_history_respects_its_limit(db):
    """`change_zc` is the newest best ask minus the newest observation older
    than 24h. A brand-new class has no such pair (null, not 0); a null point
    recorded in between (a withdrawal has no best ask to report) must not
    break the walk back to a point that IS priced. Separately, `history` is
    capped at the caller's own `history_limit`, newest first, exactly as
    `price_series` already promises — `class_board` must not silently return
    more rows than asked for, nor reorder what it was handed.
    """
    host = make_user(db)
    first_machine = make_machine(db, host, capabilities={"gpus": [GPU_16GB]})
    first_listing = mk.create_listing(
        db, machine_id=first_machine, owner_id=host, ask_zc_per_hour=1_000
    )
    assert mk.class_board(db, "gpu-16gb")["change_zc"] is None

    # Age this class's one observation past the 24h boundary by hand — real
    # time is the only thing that is allowed to make a row old, and a test
    # cannot wait a day for it.
    with db.cursor() as cur:
        cur.execute(
            "update public.price_observations"
            "   set observed_at = observed_at - interval '25 hours'"
            " where capability_class = 'gpu-16gb'"
        )

    # Withdrawing records a second, "now" observation with a NULL best ask
    # (M5: a price moves because something happened, never on a timer).
    assert mk.withdraw_listing(
        db, listing_id=str(first_listing["id"]), owner_id=host
    )

    second_machine = make_machine(
        db, host, capabilities={"gpus": [{**GPU_16GB, "index": 1}]}
    )
    second_listing = mk.create_listing(
        db, machine_id=second_machine, owner_id=host, ask_zc_per_hour=1_500
    )

    board = mk.class_board(db, "gpu-16gb")
    assert board["last_zc"] == 1_500
    # 1500 (now) minus 1000 (the backdated point) — the null withdrawal
    # point between them contributes nothing to the arithmetic.
    assert board["change_zc"] == 500

    # Three more book events, so this class now has six recorded rows: the
    # two creates and the withdrawal above, plus a pause/resume/pause below.
    assert mk.pause_listing(db, listing_id=str(second_listing["id"]), owner_id=host)
    assert mk.resume_listing(db, listing_id=str(second_listing["id"]), owner_id=host)
    assert mk.pause_listing(db, listing_id=str(second_listing["id"]), owner_id=host)

    full = mk.class_board(db, "gpu-16gb")
    assert len(full["history"]) == 6

    capped = mk.class_board(db, "gpu-16gb", history_limit=2)
    assert len(capped["history"]) == 2
    assert capped["history"][0]["at"] >= capped["history"][1]["at"]
    # The newest point is the final pause: the book is empty again — the
    # class line renders that as null, never as 0.
    assert capped["history"][0]["best_ask_zc"] is None
    assert capped["history"][0]["open_asks"] == 0
