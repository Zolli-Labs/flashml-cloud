"""`restamp_price_quotes.py`, without a database.

    flashml-cloud/apps/api/.venv/bin/python -m pytest \
        flashml-cloud/scripts/prices/test_restamp_price_quotes.py -q

The planning half of that script is deliberately pure — it takes stored quotes
and an instant and returns what would be appended — so the three properties
that matter can be checked without Postgres and without a venue:

* the number is CARRIED, never computed;
* the copy is fresh under the same `prices.is_stale` the panel renders through
  (this is the demo fix, stated as an assertion);
* a chain of copies still measures its age from the vendor reading at the
  bottom of it, so re-running this daily cannot launder an old rate.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from flashml_cloud_api import prices

# By path, because `scripts/` is not a package and the script is meant to be
# run as a file. Registered in `sys.modules` BEFORE it executes: `@dataclass`
# resolves annotations through `sys.modules[cls.__module__]` and raises on a
# module that is not there yet.
_SPEC = importlib.util.spec_from_file_location(
    "restamp_price_quotes",
    pathlib.Path(__file__).with_name("restamp_price_quotes.py"),
)
assert _SPEC is not None and _SPEC.loader is not None
restamp = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = restamp
_SPEC.loader.exec_module(restamp)


#: The instant migration 0019 seeded, verbatim.
SEEDED = datetime(2026, 8, 12, 3, 55, tzinfo=timezone.utc)
SEED_SOURCE = "runpod REST v2 GPU catalogue (list-gpu-types)"


def quote(**over) -> prices.Quote:
    """The cheapest whole-machine hour the trade-off route picks: the one USD
    figure the panel shows."""
    fields = {
        "provider": "runpod",
        "sku": "NVIDIA RTX A5000",
        "region": "global",
        "currency": "USD",
        "amount": Decimal("0.16"),
        "unit": "gpu-hour",
        "captured_at": SEEDED,
        "source": SEED_SOURCE,
        "tier": "community",
        "attrs": {"vram_gb": 24, "max_cards": 10},
        "observed_by": "runpod-api",
    }
    fields.update(over)
    return prices.Quote(**fields)


def test_the_stale_price_the_panel_would_show_becomes_fresh():
    """THE DEMO FIX. A day and a bit after the seed, the panel's only USD
    figure renders `· STALE`; the appended copy does not."""
    demo = SEEDED + timedelta(hours=25)
    stored = quote()
    assert prices.is_stale(stored, demo) is True

    copy = restamp.plan([stored], demo)[0]
    fresh = quote(captured_at=copy.captured_at, source=copy.source,
                  observed_by=restamp.CARRIED_FORWARD)
    assert prices.is_stale(fresh, demo) is False
    assert prices.quote_age(fresh, demo) == timedelta(0)


def test_the_rate_is_carried_and_never_computed():
    """No arithmetic between the read and the write — the same Decimal, with
    the vendor's own digits."""
    stored = quote(amount=Decimal("0.1600000000"))
    copy = restamp.plan([stored], SEEDED + timedelta(hours=25))[0]
    assert copy.quote.amount == Decimal("0.16")
    assert copy.quote.amount is stored.amount


def test_a_copy_says_it_is_a_copy():
    """`observed_by` is the column that distinguishes kinds of evidence, and a
    copy is not a catalogue read. `source` says so in the words the market
    panel renders beside the number."""
    copy = restamp.plan([quote()], SEEDED + timedelta(hours=25))[0]
    assert restamp.CARRIED_FORWARD != "runpod-api"
    assert "not re-observed" in copy.source
    assert SEED_SOURCE in copy.source
    assert "2026-08-12T03:55:00Z" in copy.source


def test_a_chain_of_copies_still_ages_from_the_vendor_reading():
    """Re-running this daily must not reset the clock. The root instant travels
    in `attrs`, so the second copy's age is measured from RunPod's reading and
    not from the first copy."""
    first = restamp.plan([quote()], SEEDED + timedelta(days=1))[0]
    carried = quote(
        captured_at=first.captured_at,
        source=first.source,
        attrs=first.attrs,
        observed_by=restamp.CARRIED_FORWARD,
    )
    second = restamp.plan([carried], SEEDED + timedelta(days=2))[0]

    assert second.root_captured_at == SEEDED
    assert second.root_source == SEED_SOURCE
    # Both copies name the same vendor reading, so the prose is stable rather
    # than nesting one copy's sentence inside the next one's.
    assert second.source == first.source
    assert second.source.count("carried forward") == 1


def test_it_refuses_to_launder_a_rate_nobody_has_checked():
    """Past `--max-carry-days` from the ROOT reading, the answer is 'go and
    re-pull it', not another fresh-looking copy."""
    limit = timedelta(days=restamp.DEFAULT_MAX_CARRY_DAYS)

    inside = restamp.plan([quote()], SEEDED + timedelta(days=6))
    assert restamp.too_old(inside, limit) == []

    outside = restamp.plan([quote()], SEEDED + timedelta(days=8))
    assert restamp.too_old(outside, limit) == outside
    assert outside[0].carried_days == pytest.approx(8.0)


def test_the_root_of_an_unreadable_provenance_is_the_quote_itself():
    """`attrs` is free-form context, so a malformed or absent marker must fall
    back to the quote's own capture rather than crashing an operator five
    minutes before a demo."""
    for attrs in ({}, {"restamped_from": "yesterday"},
                  {"restamped_from": {"captured_at": "not a date",
                                      "source": "x"}}):
        root_at, root_source = restamp.root_of(quote(attrs=attrs))
        assert root_at == SEEDED
        assert root_source == SEED_SOURCE


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
