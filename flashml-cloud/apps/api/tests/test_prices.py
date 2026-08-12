"""Prices: the policy without a database, then the captured record with one.

Each property pinned here is one whose failure puts a number on the
comparison view that nobody could defend when asked where it came from:

- **The staleness boundary**, at exactly the threshold and on both sides of
  it, plus the future-timestamp case. "A scraped price presented as live is a
  lie with a delay" is the whole rule, and it lives or dies on this
  comparison being the cautious one.
- **Append-only.** There is no UPDATE and no DELETE in the module or the
  migration, and a second observation of the same price ADDS a row. The old
  row is the only evidence that the price moved.
- **Latest-per-key**, stated twice — once pure, once in SQL — and the two
  agreeing on the same rows. Including the case a four-field key would get
  wrong, which Alibaba really produces.
- **`unpriced()`.** A venue with no quote is named, so the view can render
  *not observed* rather than 0 or nothing.
- **No code path sums or converts across currencies.** Behaviourally, and by
  reading the module's own source for a rate table nobody should ever add.
- **Every seeded quote carries a source and a captured_at**, and neither is
  a placeholder — a quote nobody can go and re-check is the thing this table
  was built to make impossible.
- **A zero is a price; a missing price is a missing row.** RunPod's
  not-offered sentinel is absent from the seed and Alibaba's documented free
  idle vCPU is present at 0. Same digit, opposite meanings.
"""
from __future__ import annotations

import ast
import pathlib
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from flashml_cloud_api import prices

# The session Postgres from `conftest.py`, migrated by the real runner and
# wrapped in a dict-row connection. `test_schema.py` and `test_contributions
# .py` borrow the same fixture the same way.
from test_jobs_from_repo import db  # noqa: F401 - fixture

MODULE = pathlib.Path(prices.__file__)
SOURCE = MODULE.read_text()

MIGRATION = (
    pathlib.Path(__file__).parent.parent / "migrations" / "0019_price_quotes.sql"
)
MIGRATION_SQL = MIGRATION.read_text()

#: The instants the seed was captured at, exactly as 0019 writes them.
RUNPOD_CAPTURE = datetime(2026, 8, 12, 3, 55, tzinfo=timezone.utc)
FC_GPU_CAPTURE = datetime(2026, 8, 12, 3, 57, tzinfo=timezone.utc)
FC_SANDBOX_CAPTURE = datetime(2026, 8, 12, 3, 58, tzinfo=timezone.utc)

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)


def _identifiers(source: str) -> set[str]:
    """Every name the module's CODE uses, ignoring comments and docstrings."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
    return names


def _numbers(source: str) -> set[float]:
    """Every numeric literal in the module's code. Prose does not count."""
    return {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    }


def quote(**overrides) -> prices.Quote:
    """A plausible RunPod quote, with any field replaced."""
    fields = dict(
        provider="runpod",
        sku="NVIDIA GeForce RTX 4090",
        region="global",
        currency="USD",
        amount=Decimal("0.34"),
        unit="gpu-hour",
        captured_at=RUNPOD_CAPTURE,
        source="runpod REST v2 GPU catalogue (list-gpu-types)",
        tier="community",
        observed_by="runpod-api",
    )
    fields.update(overrides)
    return prices.Quote(**fields)


# ---------------------------------------------------------------------------
# Staleness: the boundary, and the two ways a clock can lie about it.
# ---------------------------------------------------------------------------


def test_a_quote_is_fresh_one_second_before_the_threshold():
    q = quote(captured_at=NOW - prices.DEFAULT_MAX_AGE + timedelta(seconds=1))
    assert prices.is_stale(q, NOW) is False


def test_a_quote_is_stale_at_exactly_the_threshold():
    """`>=`, not `>`. At exactly max_age the quote has reached the age we said
    we would stop vouching for it, and the cautious side of that boundary is
    the honest one: a wrongly-fresh label costs credibility, a wrongly-stale
    one costs a word on screen."""
    q = quote(captured_at=NOW - prices.DEFAULT_MAX_AGE)
    assert prices.is_stale(q, NOW) is True


def test_a_quote_is_stale_one_second_after_the_threshold():
    q = quote(captured_at=NOW - prices.DEFAULT_MAX_AGE - timedelta(seconds=1))
    assert prices.is_stale(q, NOW) is True


def test_the_default_threshold_is_twenty_four_hours():
    assert prices.DEFAULT_MAX_AGE == timedelta(hours=24)


def test_the_threshold_is_an_argument_not_a_constant_everyone_shares():
    """A view that needs a tighter window says so; it does not edit a module
    constant every other caller depends on."""
    q = quote(captured_at=NOW - timedelta(hours=2))
    assert prices.is_stale(q, NOW) is False
    assert prices.is_stale(q, NOW, timedelta(hours=1)) is True


def test_a_quote_stamped_in_the_future_is_stale():
    """A negative age can only come from a clock that disagrees, and a quote
    that never ages is worse than an old one: it would sit on the page
    looking current for ever. Stale is the reading that gets it re-pulled."""
    q = quote(captured_at=NOW + timedelta(hours=1))
    assert prices.is_stale(q, NOW) is True


def test_quote_age_is_signed_and_measured_from_the_capture():
    """Signed, so a caller can SHOW that a clock disagreed rather than having
    the evidence rounded to zero on its way past."""
    assert prices.quote_age(quote(captured_at=NOW - timedelta(hours=3)), NOW) == (
        timedelta(hours=3)
    )
    assert prices.quote_age(quote(captured_at=NOW + timedelta(hours=3)), NOW) < (
        timedelta(0)
    )


def test_age_is_never_measured_from_when_the_row_was_written():
    """The bug this table exists to prevent, in one assertion: a price read
    from a document weeks ago and stored a moment ago is WEEKS old. Measuring
    from `recorded_at` would report it as fresh — a lie with a delay."""
    q = quote(
        captured_at=NOW - timedelta(days=30),
        recorded_at=NOW - timedelta(seconds=1),
    )
    assert prices.quote_age(q, NOW) == timedelta(days=30)
    assert prices.is_stale(q, NOW) is True


def test_a_naive_timestamp_is_refused_rather_than_assumed_to_be_utc():
    """An offset-naive instant is an error of hours that reads as correct,
    and hours are exactly the unit staleness is measured in."""
    q = quote(captured_at=datetime(2026, 8, 12, 3, 55))
    with pytest.raises(prices.NaiveTimestamp):
        prices.quote_age(q, NOW)


def test_render_carries_the_age_the_ui_has_to_show():
    """The view must be able to say how old, not only whether. A page that
    shows the verdict alone cannot be argued with, and being checkable is the
    entire point."""
    q = quote(captured_at=NOW - timedelta(hours=30))
    shown = prices.render(q, NOW)
    assert shown["stale"] is True
    assert shown["age_seconds"] == 30 * 3600
    assert shown["captured_at"] == "2026-08-11T06:00:00Z"
    assert shown["source"].startswith("runpod REST v2")


def test_render_keeps_the_price_exact_and_out_of_float():
    """A Decimal that reaches JSON through `float()` rounds silently. The
    string keeps the vendor's own digits all the way to the page — and drops
    the padding `numeric(20, 10)` adds, which would otherwise claim ten
    decimal places nobody published."""
    assert prices.render(quote(amount=Decimal("0.3400000000")), NOW)["amount"] == "0.34"
    assert prices.render(
        quote(amount=Decimal("0.00031896"), unit="gib-hour"), NOW
    )["amount"] == "0.00031896"


# ---------------------------------------------------------------------------
# Unpriced venues: named, never zeroed, never dropped.
# ---------------------------------------------------------------------------


def test_unpriced_names_a_venue_with_no_quote_at_all():
    assert prices.unpriced(["runpod", "lambda-labs"], [quote()]) == ["lambda-labs"]


def test_unpriced_is_empty_when_every_venue_has_a_quote():
    assert prices.unpriced(["runpod"], [quote()]) == []


def test_unpriced_keeps_the_callers_order():
    """`PRICED_PROVIDERS` is a deliberate list; re-sorting would put the
    unmeasured ones in an order nobody chose."""
    assert prices.unpriced(["vast-ai", "lambda-labs"], []) == [
        "vast-ai", "lambda-labs",
    ]


def test_an_unpriced_venue_renders_as_not_observed_and_never_as_zero():
    """The failure this exists to prevent: a venue rendered `0` reads as free
    compute and understates it to the point of dishonesty, and a venue simply
    omitted turns a comparison into a selection."""
    shown = prices.render_unpriced("lambda-labs")
    assert shown["state"] == "not observed"
    assert shown["amount"] is None
    assert shown["amount"] != 0
    assert shown["provider"] == "lambda-labs"


def test_every_provider_the_comparison_names_has_a_seeded_quote(db):
    """`PRICED_PROVIDERS` is what makes an unmeasured publisher visible. Both
    names in it today are seeded by 0019, so the live page shows two priced
    providers and no `not observed` row — and if somebody adds a third name,
    this test is where they find out the page will start admitting it has no
    number."""
    assert prices.unpriced(prices.PRICED_PROVIDERS, prices.latest_quotes(db)) == []


# ---------------------------------------------------------------------------
# No conversion, and no summing, across denominations.
# ---------------------------------------------------------------------------


def test_combine_refuses_to_add_two_currencies(db):
    """The absolute rule, against two denominations that really are both in
    this database: USD from RunPod and CU from Alibaba's GPU factors. There
    is no published CU price cited anywhere here, so the only honest outcome
    is to stop."""
    latest = prices.latest_quotes(db)
    usd = next(q for q in latest if q.currency == "USD")
    cu = next(q for q in latest if q.currency == "CU")

    with pytest.raises(prices.CurrencyMismatch):
        prices.combine([(usd, 1), (cu, 1)])


def test_combine_refuses_every_pair_of_currencies_not_just_zc_and_usd():
    """Written generically, so a denomination added later inherits the rule
    without anybody remembering to extend it. ZC is reserved and unwritten
    today; the day it is written, this is already enforced."""
    for other in ("ZC", "CU", "CNY"):
        with pytest.raises(prices.CurrencyMismatch):
            prices.combine([(quote(), 1), (quote(currency=other), 1)])


def test_combine_adds_a_shape_within_one_currency():
    """The legitimate sum, and the only one: 2 vCPU + 4 GiB of an Alibaba
    sandbox, per hour, in the one currency the vendor quoted."""
    vcpu = quote(
        provider="alibaba-fc", sku="fc-agent-sandbox/vcpu", region="cn-mainland",
        currency="USD", amount=Decimal("0.01872"), unit="vcpu-hour", tier="pro",
    )
    memory = quote(
        provider="alibaba-fc", sku="fc-agent-sandbox/memory", region="cn-mainland",
        currency="USD", amount=Decimal("0.009360"), unit="gib-hour", tier="pro",
    )
    total = prices.combine([(vcpu, 2), (memory, 4)])
    assert total.currency == "USD"
    assert total.per == "hour"
    # 2 x 0.01872 + 4 x 0.00936 = 0.07488, exactly — the document's own
    # worked example, and exact because nothing here went through a float.
    assert total.amount == Decimal("0.07488")


def test_combine_of_nothing_raises_rather_than_returning_zero():
    """"The cost of nothing is 0 USD" picks a currency by default, and a zero
    on this page is the exact reading the design forbids."""
    with pytest.raises(ValueError):
        prices.combine([])


def test_two_currencies_are_never_comparable():
    assert prices.comparable(quote(), quote(currency="CU")) is False


def test_the_module_names_nothing_that_could_be_an_exchange_rate():
    """Read the CODE, not the prose: the damaging change is somebody ADDING a
    rate table, and by the time it has a caller the comparison is already
    unreproducible.

    Parsed rather than grepped, because the module's own docstring names
    `convert_to_usd` as the thing never to write and a text search cannot
    tell a prohibition from an implementation.
    """
    # The one name allowed to mention conversion is the exception raised to
    # REFUSE one.
    permitted = {"UnconvertibleUnit"}
    identifiers = _identifiers(SOURCE) - permitted
    for fragment in ("usd", "fx", "convert", "rate"):
        offenders = [n for n in identifiers if fragment in n.lower()]
        assert offenders == [], (fragment, offenders)


def test_the_only_constants_in_this_module_are_the_ones_it_can_justify():
    """An exchange rate is, in the end, a NUMBER. So this pins which numbers
    may appear at all:

      3600 — seconds in an hour, the one exact conversion here
        24 — the staleness threshold, in hours
        50 — the default history page size
         0 — the zero of Decimal and timedelta
         1 — "more than one currency", in the refusal itself

    Anything else has to be argued for in review rather than slipped in as a
    literal, which is exactly the guard a rate constant would need to defeat.
    """
    assert _numbers(SOURCE) == {0, 1, 24, 50, 3600}


# ---------------------------------------------------------------------------
# The normaliser: exact factors only.
# ---------------------------------------------------------------------------


def test_per_second_becomes_per_hour_exactly():
    """3600, and always was. The only kind of conversion this module does."""
    cu = quote(
        provider="alibaba-fc", sku="fc-gpu/ada.1", currency="CU",
        amount=Decimal("1.7"), unit="gb-vram-second", tier="elastic-active",
    )
    hourly = prices.to_hourly(cu)
    assert hourly.unit == "gb-vram-hour"
    assert hourly.amount == Decimal("6120")


def test_normalising_never_touches_the_currency():
    """The normaliser puts a venue's time base onto a common one. Nothing
    else. Two quotes in different currencies are exactly as incomparable
    after this call as before it."""
    cu = quote(currency="CU", unit="vcpu-second", amount=Decimal("1"))
    assert prices.to_hourly(cu).currency == "CU"


def test_an_already_hourly_quote_comes_back_unchanged():
    q = quote()
    assert prices.to_hourly(q) is q


def test_gb_month_refuses_to_become_an_hourly_price():
    """A month is not a fixed number of hours. Picking 730 would put an
    invented constant behind a displayed price, indistinguishable downstream
    from arithmetic."""
    with pytest.raises(prices.UnconvertibleUnit):
        prices.to_hourly(quote(unit="gb-month"))


def test_gb_and_gib_are_not_the_same_unit():
    """One Alibaba document quotes GiB and another quotes GB. Equating them
    misstates by 7.4% with nothing on screen to suggest it, so they are
    different dimensions and never comparable."""
    assert prices.dimension("gib-hour") != prices.dimension("gb-hour")
    assert prices.comparable(
        quote(unit="gib-hour"), quote(unit="gb-hour")
    ) is False


def test_an_unknown_unit_is_loud_rather_than_passed_through():
    with pytest.raises(prices.UnknownUnit):
        prices.dimension("gpu-fortnight")


# ---------------------------------------------------------------------------
# Latest-per-key: the rule, stated pure and then in SQL.
# ---------------------------------------------------------------------------


def test_latest_by_key_keeps_the_newest_capture():
    old = quote(amount=Decimal("0.30"), captured_at=NOW - timedelta(days=2), id=1)
    new = quote(amount=Decimal("0.34"), captured_at=NOW - timedelta(hours=2), id=2)
    latest = prices.latest_by_key([new, old])
    assert list(latest.values()) == [new]


def test_latest_by_key_breaks_a_tie_deterministically():
    """Two captures in the same second must resolve the same way on every
    load. A view that reorders itself between polls reads as a price moving."""
    first = quote(amount=Decimal("0.30"), id=1)
    second = quote(amount=Decimal("0.34"), id=2)
    assert list(prices.latest_by_key([first, second]).values()) == [second]
    assert list(prices.latest_by_key([second, first]).values()) == [second]


def test_the_key_separates_tiers_and_regions():
    community = quote(tier="community")
    secure = quote(tier="secure", amount=Decimal("0.74"))
    assert len(prices.latest_by_key([community, secure])) == 2


def test_the_key_separates_currencies_and_units(db):
    """A four-field key would DROP one of these, and Alibaba really produces
    the case: the same venue priced in dollars for the sandbox and in CU for
    the GPU. A venue's price disappearing from the comparison is the same
    failure `unpriced()` prevents, one level down."""
    dollars = quote(provider="alibaba-fc", sku="x", currency="USD", unit="vcpu-hour")
    compute_units = quote(
        provider="alibaba-fc", sku="x", currency="CU", unit="vcpu-second"
    )
    assert len(prices.latest_by_key([dollars, compute_units]) ) == 2

    # And the same is true of what the database actually returns: both
    # denominations survive the SQL selection.
    from_db = prices.latest_quotes(db, providers=["alibaba-fc"])
    assert {q.currency for q in from_db} == {"USD", "CU"}


def test_the_sql_selection_agrees_with_the_pure_rule(db):
    """The rule is written twice — once where it can be tested without a
    database, once where it can run without fetching the whole history — so
    the two have to be shown to agree."""
    from_db = prices.latest_quotes(db)
    assert len(from_db) == len(prices.latest_by_key(from_db))
    assert {prices.key_of(q) for q in from_db} == set(
        prices.latest_by_key(from_db)
    )


def test_latest_quotes_returns_the_newest_observation_not_the_first(db):
    """The read the comparison view makes, against a price that moved."""
    provider = f"test-venue-{uuid.uuid4().hex[:8]}"
    common = dict(
        provider=provider, sku="gpu-1", region="global", currency="USD",
        unit="gpu-hour", tier="community", source="test",
    )
    prices.record_quote(
        db, amount=Decimal("1.00"),
        captured_at=NOW - timedelta(days=2), **common,
    )
    prices.record_quote(
        db, amount=Decimal("1.50"),
        captured_at=NOW - timedelta(hours=1), **common,
    )

    latest = prices.latest_quotes(db, providers=[provider])
    assert [q.amount for q in latest] == [Decimal("1.50")]

    # ...and the older observation is still there. That row is the only
    # evidence the price moved.
    history = prices.quote_history(db, prices.key_of(latest[0]))
    assert [q.amount for q in history] == [Decimal("1.50"), Decimal("1.00")]


def test_asking_for_no_providers_is_not_the_same_as_asking_for_all(db):
    assert prices.latest_quotes(db, providers=[]) == []
    assert prices.latest_quotes(db) != []


# ---------------------------------------------------------------------------
# Append-only.
# ---------------------------------------------------------------------------


def test_no_update_or_delete_path_exists_in_the_module():
    """A quote is an observation. It does not become false when the vendor
    changes the price — it becomes OLD, and the old value is what proves the
    new one moved. There is no update path and there must never be one."""
    assert not re.search(r"\bupdate\s+public\.price_quotes\b", SOURCE, re.I)
    assert not re.search(r"\bdelete\s+from\b", SOURCE, re.I)
    assert not re.search(r"on\s+conflict.*do\s+update", SOURCE, re.I | re.S)


def test_the_migration_writes_no_update_or_delete_either():
    assert not re.search(r"\bupdate\s+public\.price_quotes\b", MIGRATION_SQL, re.I)
    assert not re.search(r"\bdelete\s+from\b", MIGRATION_SQL, re.I)


def test_recording_the_same_observation_twice_writes_one_row(db):
    """Idempotent by the unique index, not by the caller's discipline: an
    importer retrying after a lost round trip is correct rather than
    doubling."""
    provider = f"test-venue-{uuid.uuid4().hex[:8]}"
    args = dict(
        provider=provider, sku="gpu-1", region="global", currency="USD",
        amount=Decimal("2.00"), unit="gpu-hour", tier=None,
        captured_at=NOW, source="test", observed_by="invoice",
    )
    first = prices.record_quote(db, **args)
    again = prices.record_quote(db, **args)

    assert again.id == first.id
    assert len(prices.quote_history(db, prices.key_of(first))) == 1


def test_one_source_cannot_publish_two_prices_at_one_instant(db):
    """`amount` is deliberately outside the unique key so this surfaces as a
    contradiction instead of being swallowed by `do nothing` — a
    contradiction dropped in silence is how the page ends up showing a number
    nobody can reproduce."""
    provider = f"test-venue-{uuid.uuid4().hex[:8]}"
    args = dict(
        provider=provider, sku="gpu-1", region="global", currency="USD",
        unit="gpu-hour", captured_at=NOW, source="test",
    )
    prices.record_quote(db, amount=Decimal("2.00"), **args)
    with pytest.raises(prices.ConflictingQuote):
        prices.record_quote(db, amount=Decimal("3.00"), **args)


def test_a_float_price_is_refused_rather_than_coerced(db):
    """`Decimal(0.1)` is 0.100000000000000005551115123125782702... — a wrong
    price that looks right in every log line, in a table whose whole value is
    that the number can be checked against the vendor's published one."""
    with pytest.raises(prices.ImpreciseAmount):
        prices.record_quote(
            db, provider="test-venue", sku="gpu-1", region="global",
            currency="USD", amount=0.34, unit="gpu-hour",
            captured_at=NOW, source="test",
        )


def test_a_price_may_be_given_as_the_string_the_source_printed(db):
    provider = f"test-venue-{uuid.uuid4().hex[:8]}"
    stored = prices.record_quote(
        db, provider=provider, sku="disk", region="cn-mainland", currency="USD",
        amount="0.00031896", unit="gib-hour", captured_at=NOW, source="doc",
    )
    assert stored.amount == Decimal("0.00031896")


def test_an_unknown_currency_or_unit_is_refused_at_the_boundary(db):
    """Adding a denomination is a migration, on purpose: it forces somebody
    to answer "what may this be compared with?" before the first row lands."""
    args = dict(
        provider="test-venue", sku="gpu-1", region="global",
        amount=Decimal("1"), captured_at=NOW, source="test",
    )
    with pytest.raises(ValueError):
        prices.record_quote(db, currency="XBT", unit="gpu-hour", **args)
    with pytest.raises(prices.UnknownUnit):
        prices.record_quote(db, currency="USD", unit="gpu-fortnight", **args)


# ---------------------------------------------------------------------------
# The seed: real numbers, with somewhere to go and check them.
# ---------------------------------------------------------------------------


def test_every_seeded_quote_carries_a_source_and_a_capture_instant(db):
    """A quote nobody can re-check is indistinguishable from a number
    somebody remembered, which is the thing this table was built to make
    impossible. Both columns are NOT NULL in the schema; this checks the
    seeded values are real rather than placeholders."""
    with db.cursor() as cur:
        cur.execute(
            "select provider, sku, source, captured_at, observed_by"
            "  from public.price_quotes"
            " where observed_by in ('runpod-api', 'alibaba-doc')"
        )
        rows = cur.fetchall()

    assert rows, "migration 0019 did not seed anything"
    for row in rows:
        assert row["source"] and row["source"].strip() == row["source"]
        assert row["captured_at"] is not None
        assert row["captured_at"].tzinfo is not None
        # Names the endpoint or the document, not the vendor in general.
        assert ("doc " in row["source"]) or ("REST v2" in row["source"]), row


def test_the_runpod_capture_landed_with_both_clouds(db):
    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.price_quotes"
            " where provider = 'runpod' and captured_at = %s",
            (RUNPOD_CAPTURE,),
        )
        # Thirteen SKUs on two clouds, less the one secure price RunPod does
        # not actually offer.
        assert cur.fetchone()["n"] == 25


def test_a_price_runpod_does_not_offer_is_absent_not_zero(db):
    """The catalogue returns `price.secure: 0` for the A100 SXM 40GB beside
    `secure: false` and `maxCount.secure: 0`. Written down as a price it
    would make a card you cannot rent the cheapest secure GPU in the world,
    and every comparison computed from it would be wrong in our favour."""
    with db.cursor() as cur:
        cur.execute(
            "select tier, amount from public.price_quotes"
            " where provider = 'runpod' and sku = 'NVIDIA A100-SXM4-40GB'"
        )
        rows = cur.fetchall()
    assert [r["tier"] for r in rows] == ["community"]
    assert rows[0]["amount"] == Decimal("1.00")


def test_a_documented_zero_is_stored_as_a_price(db):
    """The opposite case, and the schema tells them apart by whether the row
    exists rather than by the value. Alibaba documents idle vCPU as not
    charged; that is a fact with a source, and dropping it would misstate the
    cost of an idle instance as unknown."""
    with db.cursor() as cur:
        cur.execute(
            "select amount from public.price_quotes"
            " where provider = 'alibaba-fc' and sku = 'fc-gpu/vcpu'"
            "   and tier = 'elastic-idle'"
        )
        assert cur.fetchone()["amount"] == Decimal("0")


def test_the_alibaba_gpu_factors_are_recorded_in_cu_and_never_in_dollars(db):
    """FC bills in Compute Units and no published CU price has been cited, so
    the record stops there. An unconverted CU figure is honest; a dollar
    figure derived from a rate nobody can point at is not."""
    with db.cursor() as cur:
        cur.execute(
            "select distinct currency, unit from public.price_quotes"
            " where provider = 'alibaba-fc' and sku like 'fc-gpu/%'"
        )
        rows = cur.fetchall()
    assert {r["currency"] for r in rows} == {"CU"}
    assert "gb-vram-second" in {r["unit"] for r in rows}


def test_the_alibaba_sandbox_prices_are_recorded_in_dollars(db):
    """The half of Alibaba that IS quoted in money — the FC Agent Sandbox
    preview unit prices — so the comparison can state those without a
    conversion of any kind."""
    with db.cursor() as cur:
        cur.execute(
            "select tier, amount, unit from public.price_quotes"
            " where provider = 'alibaba-fc' and currency = 'USD'"
            "   and sku = 'fc-agent-sandbox/vcpu'"
            " order by amount"
        )
        rows = cur.fetchall()
    assert [(r["tier"], r["amount"]) for r in rows] == [
        ("eco", Decimal("0.00936")),
        ("std", Decimal("0.01224")),
        ("pro", Decimal("0.01872")),
    ]
    assert {r["unit"] for r in rows} == {"vcpu-hour"}


def test_the_untiered_disk_price_survives_the_latest_selection(db):
    """`tier` is NULL for the FC disk price because the document quotes one
    figure for all three plans — and NULLs never collide in a unique index,
    which is exactly the hole 0003 had to fix on `contributions`. Both
    regional rows must come back."""
    latest = prices.latest_quotes(db, providers=["alibaba-fc"])
    disk = [q for q in latest if q.sku == "fc-agent-sandbox/disk"]
    assert {q.region for q in disk} == {"cn-mainland", "outside-cn-mainland"}
    assert {q.tier for q in disk} == {None}


def test_the_free_disk_allowance_is_recorded_but_not_applied(db):
    """A free tier is a price that depends on QUANTITY and an (amount, unit)
    pair cannot express a band. It is in `attrs` so a human sees it, and
    nothing computes with it: applying it silently understates Alibaba,
    ignoring it silently overstates, and whoever wires the view has to
    decide in the open."""
    latest = prices.latest_quotes(db, providers=["alibaba-fc"])
    disk = next(q for q in latest if q.sku == "fc-agent-sandbox/disk")
    assert disk.attrs["free_allowance_gib"] == 15
    assert disk.attrs["free_allowance_applied_by_this_system"] is False
    assert "free_allowance" not in SOURCE


def test_a_seeded_price_is_exact_to_the_digit_the_vendor_published(db):
    """Eight decimal places, stored and read back without passing through a
    float on either side. This is what `numeric` buys and what a `float`
    column would have quietly taken away."""
    latest = prices.latest_quotes(db, providers=["alibaba-fc"])
    mainland = next(
        q for q in latest
        if q.sku == "fc-agent-sandbox/disk" and q.region == "cn-mainland"
    )
    assert mainland.amount == Decimal("0.00031896")
    assert isinstance(mainland.amount, Decimal)


def test_the_seed_is_old_enough_to_read_as_stale_by_now(db):
    """Not a bug — the point. These captures are dated, and a page rendering
    them a week later must say so rather than presenting them as live. The
    remedy is a re-pull through `record_quote`, not a wider threshold."""
    quotes = prices.latest_quotes(db, providers=["runpod"])
    a_year_later = RUNPOD_CAPTURE + timedelta(days=365)
    assert all(prices.is_stale(q, a_year_later) for q in quotes)
    assert not any(
        prices.is_stale(q, RUNPOD_CAPTURE + timedelta(hours=1)) for q in quotes
    )
