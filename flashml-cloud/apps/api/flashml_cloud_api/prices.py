"""What compute costs at each venue, and how old that answer is.

The comparison view is the visible half of this product, and the rule it has
to satisfy came out of the design review in one line: **a scraped price
presented as live is a lie with a delay.** So every external price in
``public.price_quotes`` (migration 0019) carries ``(provider, sku, region,
currency, unit, captured_at, source)``, and this module is what turns those
columns into the two answers the page owes a reader:

- **How old is this number?** ``quote_age`` and ``is_stale``. A quote past
  the threshold is shown as STALE, never silently as current. The threshold
  is 24 hours (:data:`DEFAULT_MAX_AGE`) and it is an argument everywhere, so
  a caller that needs a different one says so rather than editing a constant
  everyone else depends on.
- **Which venues have no number at all?** ``unpriced``. Those render *not
  observed*. Never ``0``, and never omitted — an omitted venue is a venue
  that looks like it was not worth comparing, and a zeroed one looks like
  free compute.

Pure policy on top, repository below, the split ``contributions.py`` and
``storage.py`` both argue for: the rules here are policy statements that must
be readable and testable without a database, and the rows are facts. It is
also what lets the staleness boundary be pinned by a test that constructs two
timestamps rather than by one that has to age a database row.

FIXED VALUE, NARROWLY APPLIED
-----------------------------

**ZC and USD normalize at 1:1 on wallet and marketplace surfaces.** The
active testing-credit policy fixes 1 ZC = 1 USD. Helpers in this module expose
that value without replacing the source denomination: a community ask remains
ZC settlement and a RunPod quote remains USD.

This is not a general FX table. ``CU`` is Alibaba's Compute Unit rather than
money, and no CU-to-USD value is published here; CNY also has no policy rate.
:func:`combine` still requires one source currency because its return type
holds one denomination. Cross-source comparison uses explicit normalized
fields instead of collapsing settlement provenance into that type.

**It never converts a unit whose factor is a convention.** Seconds to hours
is 3600 and always was. GB-month to GB-hour requires choosing 730 or 720 or
744, and that choice is an invented number wearing the clothes of arithmetic
— :func:`to_hourly` raises :class:`UnconvertibleUnit` rather than pick one.
GB and GiB are likewise kept apart: one Alibaba document quotes GiB and
another quotes GB, and equating them misstates by 7.4% in silence.

**It never prices off ``attrs``.** Dividing a per-card price by ``vram_gb``
to compare with a per-GB-VRAM price would be exactly the cross-venue
arithmetic the no-conversion rule exists to prevent, arriving by a side door.
``attrs`` is context for a human checking the row.

**It never updates a quote.** ``price_quotes`` is append-only and there is no
update path in this file, deliberately. A price that moved is a NEW
observation; the old row is the evidence that it moved, and it is the only
thing that can answer "where did $0.34 come from?" a week after the vendor
changed it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import psycopg
from psycopg.types.json import Json

from flashml_cloud_api import marketplace as marketplacemod

#: How old a quote may be before the comparison view must label it stale.
#:
#: Twenty-four hours. RunPod's catalogue moves on no schedule anyone
#: publishes, and a vendor document can change the day after it is read (one
#: did: see 0019's note on Alibaba's `ada.1` factor). A day is short enough
#: that a stale label means something and long enough that a page opened the
#: morning after a capture is not shouting.
#:
#: It is a DEFAULT, not a policy buried in a function. Every caller may pass
#: its own, which is what keeps "how fresh is fresh enough" a decision the
#: view makes in the open instead of one this module makes for it.
DEFAULT_MAX_AGE = timedelta(hours=24)

#: Denominations `amount` may be expressed in. Mirrors the check constraint in
#: migration 0019; adding one is a migration, on purpose.
#:
#: `CU` is in this set and is NOT money. It is here because it is the
#: denomination of a number we recorded, and keeping it in the same column as
#: USD and ZC have the fixed policy value above. CU and CNY remain source
#: denominations with no conversion supplied by this module.
CURRENCIES = frozenset({"USD", "CNY", "ZC", "CU"})

#: What one `amount` buys. Mirrors 0019's check constraint.
UNITS = frozenset({
    "gpu-hour",
    "vcpu-hour", "vcpu-second",
    "gib-hour", "gib-second",
    "gb-hour", "gb-second",
    "gb-vram-hour", "gb-vram-second",
    "gb-month",
})

#: Unit -> the physical thing it measures. Two quotes are comparable only if
#: they share BOTH a currency and one of these.
#:
#: `gib` and `gb` are DIFFERENT dimensions on purpose: 1 GiB is 1.074 GB, one
#: Alibaba document quotes each, and a comparison that treated them as one
#: unit would be wrong by 7.4% with nothing on screen to suggest it.
#:
#: Same dimension is NECESSARY but not SUFFICIENT for a meaningful
#: comparison, and this module cannot close that gap: `fc-agent-sandbox/disk`
#: and `fc-agent-sandbox/memory` are both `gib-hour`, and comparing them
#: computes a real number that means nothing. What is being compared is a
#: judgement about the SKUs, which belongs to whoever builds the view.
_DIMENSIONS = {
    "gpu-hour": "gpu",
    "vcpu-hour": "vcpu",
    "vcpu-second": "vcpu",
    "gib-hour": "gib",
    "gib-second": "gib",
    "gb-hour": "gb",
    "gb-second": "gb",
    "gb-vram-hour": "gb-vram",
    "gb-vram-second": "gb-vram",
    "gb-month": "gb-storage",
}

#: Per-second unit -> its hourly equivalent. The factor is 3600 and is exact.
_TO_HOURLY = {
    "vcpu-second": "vcpu-hour",
    "gib-second": "gib-hour",
    "gb-second": "gb-hour",
    "gb-vram-second": "gb-vram-hour",
}

#: Units that are already per hour: `to_hourly` returns these unchanged.
_HOURLY = frozenset({
    "gpu-hour", "vcpu-hour", "gib-hour", "gb-hour", "gb-vram-hour",
})

_SECONDS_PER_HOUR = Decimal(3600)

#: The price PUBLISHERS the comparison view is meant to cover.
#:
#: This tuple is what makes a publisher with no quotes render *not observed*
#: instead of vanishing from the page. One nobody has priced is invisible
#: unless something names it, and invisible reads as "not worth comparing" —
#: which is a claim, made silently, that we did not check.
#:
#: Adding a name here is therefore a deliberate act with a visible
#: consequence: the page grows a row that says we have not measured it. That
#: is the intended pressure. Removing a name to tidy the page is how a
#: comparison quietly becomes a selection.
#:
#: NOT the same axis as a VENUE in the router's sense, and the name says so
#: to keep the two from being imported for each other. A `provider` is WHO
#: PUBLISHED THE PRICE; a venue is WHERE WORK COULD RUN, and Alibaba is one
#: publisher covering two of them — the FC Agent Sandbox (`sku` beginning
#: `fc-agent-sandbox/`) and FC serverless GPU (`fc-gpu/`). Whoever wires the
#: comparison maps venue -> (provider, sku family); this module deliberately
#: does not, because splitting one publisher into two providers would make
#: "has Alibaba been priced at all?" a question with no way to ask it.
PRICED_PROVIDERS = ("runpod", "alibaba-fc")

#: The landing page's curated GPU board: (display label, exact SKU string),
#: in the order the page renders them. Curated because the marketing page
#: is not the full comparison view — it needs a short, recognisable list a
#: visitor can scan before ever signing in.
#:
#: Every SKU is copied VERBATIM from migration 0019's seed. That is the
#: venue's own identifier (see 0019's decision 3 on `sku`), and a
#: paraphrased or "tidied" string here would simply stop matching the day
#: somebody typo-fixed it — the exact opposite of the re-verifiability that
#: field exists for.
#:
#: `A100 80GB` names the SXM4 SKU rather than the PCIe one 0019 also seeds,
#: so it reads as one lineage with `A100 40GB` (also SXM4) instead of mixing
#: form factors the visitor never asked to compare.
#:
#: RunPod-only today: it is the one provider 0019 quotes in plain USD per
#: GPU-hour, which is the only shape this board renders. Nothing here stops
#: a second provider's SKU from being added later — `landing_rows` matches
#: on `sku` alone, not on provider.
LANDING_GPUS: tuple[tuple[str, str], ...] = (
    ("H100 80GB", "NVIDIA H100 80GB HBM3"),
    ("A100 80GB", "NVIDIA A100-SXM4-80GB"),
    ("A100 40GB", "NVIDIA A100-SXM4-40GB"),
    ("RTX 5090", "NVIDIA GeForce RTX 5090"),
    ("RTX 4090", "NVIDIA GeForce RTX 4090"),
    ("RTX 3090", "NVIDIA GeForce RTX 3090"),
    ("L40S", "NVIDIA L40S"),
)

#: Tier preference for the landing board, in this order and no other.
#: `community` first, because it is the price most visitors can actually
#: get; `secure` only when a SKU has no `community` row at all — 0019's
#: `NVIDIA A100-SXM4-40GB` is exactly that case, since RunPod's own
#: "secure" figure for it is a not-offered sentinel and was never seeded
#: (see 0019 decision 2). A third tier would need this tuple extended
#: deliberately, not an `order by tier` that happens to sort the way two
#: tiers currently do.
_LANDING_TIER_PREFERENCE: tuple[str, ...] = ("community", "secure")


class CurrencyMismatch(ValueError):
    """Two amounts in different denominations were about to be combined.

    ``combine`` returns one source-denominated :class:`Cost`, so it cannot
    preserve two settlement currencies. ZC/USD normalized comparison is
    explicit elsewhere; CU and CNY have no policy rate at all.
    """


class UnconvertibleUnit(ValueError):
    """A unit whose conversion factor would have to be invented.

    ``gb-month`` is the case that exists today: a month is not a fixed number
    of hours, so any factor is a convention chosen by whoever wrote the code
    and it would be indistinguishable, downstream, from arithmetic.
    """


class UnknownUnit(ValueError):
    """A unit outside the vocabulary migration 0019 constrains.

    Loud rather than pass-through: a unit this module has never heard of has
    no dimension and no factor, and normalising it by guess is how a
    per-second price gets displayed as a per-hour one.
    """


class ImpreciseAmount(TypeError):
    """A price arrived as a binary float.

    Refused rather than coerced. ``numeric`` in Postgres and ``Decimal`` in
    Python are exact decimal arithmetic, and the entire value of this table
    is that a stored number can be checked against the vendor's published
    one. ``float("0.00031896")`` is already not that number, and
    ``Decimal(0.1)`` is 0.1000000000000000055511151231257827021181583404541015625
    — a wrong price that looks right in every log line.

    Pass a ``Decimal``, an ``int``, or the string exactly as the source
    printed it.
    """


class NaiveTimestamp(ValueError):
    """A timestamp arrived without a timezone.

    An offset-naive ``captured_at`` is an error of hours that reads as a
    correct answer, and hours are the whole subject here: a quote seven hours
    in the "future" because somebody's clock was local is a quote that never
    goes stale.
    """


class ConflictingQuote(ValueError):
    """The same source, at the same instant, with two different prices.

    ``record_quote`` is idempotent against the natural key — re-recording an
    identical observation is a no-op — but ``amount`` is deliberately NOT in
    that key, so a second, different price for the same
    (provider, sku, region, tier, currency, unit, captured_at, source) is a
    contradiction rather than a duplicate. Raised instead of swallowed,
    because a contradiction dropped in silence is how the page ends up
    showing a number nobody can reproduce.
    """


@dataclass(frozen=True)
class Quote:
    """One observation of one published price.

    Frozen because it is a record of something that already happened. A
    mutable quote invites the update path this table does not have.
    """

    provider: str
    sku: str
    region: str
    currency: str
    amount: Decimal
    unit: str
    captured_at: datetime
    source: str
    tier: str | None = None
    attrs: Mapping[str, Any] = field(default_factory=dict)
    observed_by: str | None = None
    #: Set for rows read back from Postgres; None for a quote built in
    #: memory and not yet recorded.
    id: int | None = None
    recorded_at: datetime | None = None


@dataclass(frozen=True)
class QuoteKey:
    """What identifies "the same thing being priced", across observations.

    SIX FIELDS, and the last two are the ones a shorter key loses. The
    obvious key is (provider, sku, region, tier) — but Alibaba prices one
    venue in two denominations (dollars for the sandbox, CU for the GPU) and
    could quote one SKU both per-second and per-hour. With ``currency`` and
    ``unit`` outside the key, latest-per-key keeps one row and DROPS the
    other, and a venue's price disappears from the comparison without
    anything on screen to say so. That is the same failure ``unpriced``
    exists to prevent, one level down.
    """

    provider: str
    sku: str
    region: str
    tier: str | None
    currency: str
    unit: str


@dataclass(frozen=True)
class Cost:
    """A combined amount that has not forgotten what it counts.

    :func:`combine` returns this rather than a bare ``Decimal`` on purpose. A
    loose number is exactly what gets added to another loose number in a
    different currency three call sites later; carrying the denomination
    means the mistake has to be made deliberately.
    """

    amount: Decimal
    currency: str
    #: The time base the amount is per — 'hour' for everything today.
    per: str


# ---------------------------------------------------------------------------
# Policy. No database, no I/O, no clock of its own — `now` is always passed
# in, so every boundary below is testable at the exact instant it matters.
# ---------------------------------------------------------------------------


def normalized_usd_amount(
    currency: str, amount: Decimal | int | str
) -> Decimal | None:
    """Comparable USD value for ZC or USD, preserving the source elsewhere.

    ``None`` for CNY, CU, or an unknown denomination: the fixed testing-credit
    policy is deliberately not a general exchange-rate service.
    """
    exact = _exact(amount, "amount")
    if currency == "USD":
        return exact
    if currency == "ZC":
        return exact * marketplacemod.USD_PER_ZC
    return None


def zc_equivalent_amount(
    currency: str, amount: Decimal | int | str
) -> Decimal | None:
    """ZC equivalent for a USD or ZC marketplace quote at fixed parity."""
    exact = _exact(amount, "amount")
    if currency == "ZC":
        return exact
    if currency == "USD":
        return exact / marketplacemod.USD_PER_ZC
    return None


def zc_ask_price_label(ask_zc_per_hour: int) -> str:
    """A source-settlement label for one integer-millicredit ZC ask."""
    credits = _zc_ask_amount(ask_zc_per_hour)
    if credits == 0:
        return "donated"
    return f"{_market_amount(credits)} ZC/hour"


def zc_ask_usd_amount_text(ask_zc_per_hour: int) -> str:
    """Exact decimal USD text for an integer-millicredit ZC ask."""
    usd = normalized_usd_amount("ZC", _zc_ask_amount(ask_zc_per_hour))
    assert usd is not None
    return _market_amount(usd)


def zc_ask_usd_equivalent_label(ask_zc_per_hour: int) -> str:
    """The fixed cash equivalent beside a ZC ask, never in its place."""
    return f"${zc_ask_usd_amount_text(ask_zc_per_hour)}/hour equivalent"


def quote_age(quote: Quote, now: datetime) -> timedelta:
    """How long ago this price was OBSERVED. Signed, deliberately.

    Measured from ``captured_at`` — when the vendor's number was read — and
    never from ``recorded_at``, which is when our row was written. Measuring
    from the write is precisely the "lie with a delay": a number read from a
    document last month, stored today, would report an age of zero.

    A NEGATIVE age is returned as-is rather than clamped to zero. It means a
    clock disagreed, and that is information the caller may want to show;
    :func:`is_stale` is where it turns into a decision.
    """
    return _aware(now, "now") - _aware(quote.captured_at, "captured_at")


def is_stale(
    quote: Quote, now: datetime, max_age: timedelta = DEFAULT_MAX_AGE
) -> bool:
    """Whether this quote must be shown as stale rather than as current.

    ``>=`` at the boundary, not ``>``: at exactly ``max_age`` the quote has
    reached the age at which we said we would stop vouching for it, and the
    honest side of a boundary is the cautious one. Labelling something fresh
    costs credibility the moment it is wrong; labelling it stale costs a word
    on screen.

    A quote stamped in the FUTURE is stale too. A negative age can only come
    from a clock that disagrees, and a quote that never ages is worse than an
    old one — it would sit on the page looking current for ever. Treating it
    as stale fails in the direction that gets it re-pulled.
    """
    age = quote_age(quote, now)
    if age < timedelta(0):
        return True
    return age >= max_age


def dimension(unit: str) -> str:
    """What physical thing this unit measures.

    Raises :class:`UnknownUnit` rather than returning a placeholder: a unit
    with no dimension cannot be compared with anything, and a placeholder
    would let it be compared with every other unit that also has none.
    """
    try:
        return _DIMENSIONS[unit]
    except KeyError:
        raise UnknownUnit(
            f"{unit!r} is not one of the units migration 0019 allows: "
            + ", ".join(sorted(UNITS))
        ) from None


def to_hourly(quote: Quote) -> Quote:
    """The same quote expressed per hour, when that is exact arithmetic.

    THE UNIT NORMALISER, and note what it does not touch: the source currency
    is unchanged, always. It puts a venue's native time base onto a common one
    so that per-second and per-hour prices can sit in one column. ZC/USD cash
    normalization is an explicit second step through
    :func:`normalized_usd_amount`; CU and CNY remain unconverted.

    Per-second to per-hour is x3600 and exact in ``Decimal``. Already-hourly
    quotes are returned unchanged (the same object's values, not a rounded
    copy). ``gb-month`` raises :class:`UnconvertibleUnit`: a month is not a
    fixed number of hours, and picking 730 would put an invented constant
    behind a displayed price.
    """
    if quote.unit not in UNITS:
        raise UnknownUnit(
            f"{quote.unit!r} is not one of the units migration 0019 allows"
        )
    if quote.unit in _HOURLY:
        return quote
    hourly = _TO_HOURLY.get(quote.unit)
    if hourly is None:
        raise UnconvertibleUnit(
            f"{quote.unit!r} cannot be converted to an hourly unit without "
            "inventing a factor — a month is not a fixed number of hours. "
            "Show it in its own unit."
        )
    return Quote(
        provider=quote.provider,
        sku=quote.sku,
        region=quote.region,
        currency=quote.currency,
        amount=quote.amount * _SECONDS_PER_HOUR,
        unit=hourly,
        captured_at=quote.captured_at,
        source=quote.source,
        tier=quote.tier,
        attrs=quote.attrs,
        observed_by=quote.observed_by,
        id=quote.id,
        recorded_at=quote.recorded_at,
    )


def comparable(left: Quote, right: Quote) -> bool:
    """Whether source-denominated quote arithmetic may compare these directly.

    Same currency AND same dimension. Necessary, not sufficient — see
    ``_DIMENSIONS``: two SKUs can share a unit and still be different things.
    This function answers whether the original quote fields align. A
    marketplace may separately compare ZC/USD normalized values while still
    displaying these source fields; that does not make CU or CNY comparable.
    """
    return (
        left.currency == right.currency
        and dimension(left.unit) == dimension(right.unit)
    )


def combine(components: Sequence[tuple[Quote, Decimal | int | str]]) -> Cost:
    """Add up a shape: e.g. 4 vCPU + 8 GiB of memory + 30 GiB of disk, per hour.

    THE ONLY PLACE IN THIS MODULE THAT BUILDS ONE SOURCE-DENOMINATED COST, so
    every component must share that source currency. ZC/USD fixed-value
    comparison belongs in :func:`normalized_usd_amount`; collapsing them here
    would discard which account or provider actually settles each component.
    CU/CNY combinations are additionally unpriced because no rate exists.

    Every component is normalised to its hourly unit first, so a venue
    quoting per-second and a venue quoting per-hour add up correctly instead
    of by a factor of 3600. Quantities are exact: a float quantity is refused
    for the same reason a float price is.

    Different UNITS across components are fine and expected — that is what a
    shape is. Different CURRENCIES never are.

    An empty sequence raises rather than returning zero: "the cost of nothing
    is 0 USD" is a currency picked by a default, and a zero on a comparison
    page is the exact reading this whole design forbids.
    """
    if not components:
        raise ValueError(
            "combine() of nothing has no currency and therefore no honest "
            "answer — an empty shape is not 0 USD"
        )

    hourly = [(to_hourly(quote), _exact(qty, "quantity")) for quote, qty in components]

    currencies = {quote.currency for quote, _ in hourly}
    if len(currencies) > 1:
        raise CurrencyMismatch(
            "refusing to collapse source amounts in "
            + " and ".join(sorted(currencies))
            + ": combine() returns one settlement denomination. Preserve "
            "the sources and normalize ZC/USD explicitly for comparison."
        )

    total = sum((quote.amount * qty for quote, qty in hourly), Decimal(0))
    return Cost(amount=total, currency=currencies.pop(), per="hour")


def latest_by_key(quotes: Iterable[Quote]) -> dict[QuoteKey, Quote]:
    """The newest observation of each thing priced.

    Pure, and used by :func:`latest_quotes` only as the shape it returns —
    the selection there is done in SQL, because the alternative is fetching
    an ever-growing history to throw most of it away. This function is what
    lets the SELECTION RULE be stated and tested without a database, and it
    is the definition the SQL is written to match.

    Ties on ``captured_at`` break on ``id`` (later row wins), because two
    sources read in the same second must produce a deterministic answer; a
    comparison that reorders itself between page loads reads as a price
    moving and is not. A quote with no id — one built in memory — loses to
    one that has been recorded, which is the same rule: the row that exists
    beats the row that has not been written down.
    """
    best: dict[QuoteKey, Quote] = {}
    for quote in quotes:
        key = key_of(quote)
        current = best.get(key)
        if current is None or _recency(quote) > _recency(current):
            best[key] = quote
    return best


def key_of(quote: Quote) -> QuoteKey:
    """The identity of what this quote prices. See :class:`QuoteKey`."""
    return QuoteKey(
        provider=quote.provider,
        sku=quote.sku,
        region=quote.region,
        tier=quote.tier,
        currency=quote.currency,
        unit=quote.unit,
    )


def unpriced(providers: Iterable[str], quotes: Iterable[Quote]) -> list[str]:
    """The named providers with NO quote at all, in the order they were named.

    **These must render "not observed".** Not ``0``, which reads as free
    compute and understates that venue to the point of dishonesty; and not
    omitted, which silently turns a comparison into a selection and hides
    that we did not check.

    Order is the caller's, not sorted: :data:`PRICED_PROVIDERS` is a
    deliberate list and re-sorting it would put the unmeasured ones in an
    order nobody chose.
    """
    priced = {quote.provider for quote in quotes}
    return [provider for provider in providers if provider not in priced]


def render(
    quote: Quote, now: datetime, max_age: timedelta = DEFAULT_MAX_AGE
) -> dict[str, Any]:
    """One quote as the comparison view needs it, age included.

    ``amount`` is a STRING, not a float. It travels to a browser through
    JSON, and ``json.dumps`` on a ``Decimal`` fails while ``float(Decimal)``
    succeeds and quietly rounds — the second of which is how an exact price
    becomes 0.0003189600000000001 on screen. A string preserves the vendor's
    own digits all the way to the page.

    ``normalize()`` first, because ``numeric(20, 10)`` pads: 0.16 comes back
    as ``0.1600000000`` and eight meaningless zeros on a price list look like
    a precision nobody claimed. Normalising drops only trailing zeros, so the
    value is unchanged; ``format(..., "f")`` then keeps it out of exponent
    notation, which ``normalize()`` would otherwise produce for round numbers
    (``Decimal("100")`` normalises to ``1E+2``).

    ``age_seconds`` is here as well as ``stale`` because a threshold answers
    "may I trust this?" and a reader also wants "how old, exactly?". A page
    that shows only the verdict cannot be argued with, and the whole point of
    this table is that it can be.
    """
    age = quote_age(quote, now)
    equivalent = zc_equivalent_amount(quote.currency, quote.amount)
    return {
        "provider": quote.provider,
        "sku": quote.sku,
        "region": quote.region,
        "tier": quote.tier,
        "currency": quote.currency,
        "amount": format(quote.amount.normalize(), "f"),
        "unit": quote.unit,
        "attrs": dict(quote.attrs),
        "captured_at": _iso(quote.captured_at),
        "age_seconds": int(age.total_seconds()),
        "stale": is_stale(quote, now, max_age),
        "max_age_seconds": int(max_age.total_seconds()),
        "source": quote.source,
        "observed_by": quote.observed_by,
        "zc_equivalent_amount": (
            format(equivalent.normalize(), "f")
            if equivalent is not None
            else None
        ),
    }


def render_unpriced(venue: str) -> dict[str, Any]:
    """A venue with no quote, shaped like one so the view cannot skip it.

    Same keys as :func:`render` where they apply, with ``amount`` explicitly
    ``None`` and ``state`` saying so in words. ``None`` rather than 0 or
    ``""``: a consumer that falls back on a falsy value gets nothing to
    render and has to say so, which is the intended outcome.
    """
    return {
        "provider": venue,
        "state": "not observed",
        "amount": None,
        "zc_equivalent_amount": None,
        "currency": None,
        "unit": None,
        "captured_at": None,
        "age_seconds": None,
        "stale": None,
        "source": None,
        "observed_by": None,
    }


# ---------------------------------------------------------------------------
# Repository. Append-only: an insert and two reads, and no UPDATE or DELETE
# anywhere in this file.
# ---------------------------------------------------------------------------

_COLUMNS = """
    id, provider, sku, region, currency, amount, unit, tier, attrs,
    captured_at, source, observed_by, recorded_at
"""


def record_quote(
    db: psycopg.Connection,
    *,
    provider: str,
    sku: str,
    region: str,
    currency: str,
    amount: Decimal | int | str,
    unit: str,
    captured_at: datetime,
    source: str,
    tier: str | None = None,
    attrs: Mapping[str, Any] | None = None,
    observed_by: str | None = None,
) -> Quote:
    """Write one observation. Returns the stored row.

    Keyword-only throughout, because ``(provider, sku, region, currency,
    unit, tier)`` are six strings in a row and a positional call that
    transposes two of them produces a plausible-looking quote for the wrong
    thing.

    IDEMPOTENT against the natural key — re-recording an identical
    observation returns the existing row instead of writing a second one, so
    an importer that retries after a lost round trip is correct rather than
    duplicating. The same source at the same instant with a DIFFERENT amount
    raises :class:`ConflictingQuote`, because that is a contradiction and not
    a retry; ``amount`` is deliberately outside the unique key so the
    database can surface it here instead of storing both and letting the
    latest-per-key rule pick one.

    ``attrs`` is not part of the key: a re-record with richer attrs keeps the
    stored row unchanged and returns it. Attributes are context for a human,
    never a price, so quietly preferring the first observation is safe — and
    the alternative is an UPDATE, which this table does not have.

    Validation is deliberately thin and refuses only what cannot be checked
    afterwards: a float amount (see :class:`ImpreciseAmount`), a naive
    ``captured_at`` (:class:`NaiveTimestamp`), and a currency or unit outside
    the vocabulary. The database enforces the rest — non-empty provider/sku/
    region/source, non-negative amount — and it enforces it for every writer,
    not only this one.
    """
    exact = _exact(amount, "amount")
    when = _aware(captured_at, "captured_at")

    if currency not in CURRENCIES:
        raise ValueError(
            f"{currency!r} is not a denomination migration 0019 allows "
            f"({', '.join(sorted(CURRENCIES))}). Adding one is a migration, "
            "so that somebody answers 'what may this be compared with?' "
            "before the first row lands."
        )
    if unit not in UNITS:
        raise UnknownUnit(
            f"{unit!r} is not one of the units migration 0019 allows: "
            + ", ".join(sorted(UNITS))
        )

    with db.cursor() as cur:
        cur.execute(
            f"""
            insert into public.price_quotes
                (provider, sku, region, currency, amount, unit, tier, attrs,
                 captured_at, source, observed_by)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict do nothing
            returning {_COLUMNS}
            """,
            (provider, sku, region, currency, exact, unit, tier,
             Json(dict(attrs or {})), when, source, observed_by),
        )
        row = cur.fetchone()
        if row is not None:
            return _quote(row)

        # Nothing inserted: the natural key already exists. Fetch it and find
        # out which of the two cases this is.
        cur.execute(
            f"""
            select {_COLUMNS}
              from public.price_quotes
             where provider = %s and sku = %s and region = %s
               and coalesce(tier, '') = coalesce(%s, '')
               and currency = %s and unit = %s
               and captured_at = %s and source = %s
            """,
            (provider, sku, region, tier, currency, unit, when, source),
        )
        existing = cur.fetchone()

    if existing is None:  # pragma: no cover - only reachable under a race
        raise ConflictingQuote(
            "the insert conflicted but the conflicting row is gone; "
            "price_quotes is append-only, so this should be impossible"
        )

    stored = _quote(existing)
    if stored.amount != exact:
        raise ConflictingQuote(
            f"{source} already recorded {stored.amount} for "
            f"{provider}/{sku} ({unit}) at {_iso(when)}, and this call says "
            f"{exact}. One source cannot have published two prices for the "
            "same thing at the same instant — check which reading is right "
            "rather than storing both."
        )
    return stored


def latest_quotes(
    db: psycopg.Connection, *, providers: Sequence[str] | None = None
) -> list[Quote]:
    """The newest quote for each thing priced, newest capture first.

    ``distinct on`` over the six-field key, matching :func:`latest_by_key`
    exactly — the same rule stated twice, once where it can be tested without
    a database and once where it can be executed without fetching the whole
    history. The ``order by`` prefix is the leading column list of
    ``price_quotes_latest_idx`` (0019), so this is an ordered index scan
    rather than a sort of every observation ever made.

    ``id desc`` after ``captured_at desc`` is the tiebreak, and it is what
    keeps the page stable: two captures in the same second must resolve the
    same way on every load, or the view redraws itself and reads as a price
    moving.

    ``providers=None`` means every venue. An empty LIST, by contrast, is a
    caller asking for nothing and gets nothing — the two are different
    questions and the SQL keeps them different.
    """
    filtered = providers is not None
    with db.cursor() as cur:
        cur.execute(
            f"""
            select distinct on (provider, sku, region, tier, currency, unit)
                   {_COLUMNS}
              from public.price_quotes
             where (%s = false or provider = any(%s::text[]))
             order by provider, sku, region, tier, currency, unit,
                      captured_at desc, id desc
            """,
            (filtered, list(providers or [])),
        )
        rows = cur.fetchall()

    quotes = [_quote(row) for row in rows]
    quotes.sort(key=lambda q: (q.captured_at, q.id or 0), reverse=True)
    return quotes


def quote_history(
    db: psycopg.Connection, key: QuoteKey, *, limit: int = 50
) -> list[Quote]:
    """Every observation of one thing, newest first.

    The append-only table's reason for existing, made readable: "where did
    $0.34 come from?" is answered by the newest row, and "has it moved?" only
    by the ones behind it. A series nothing can read is a series nobody can
    check.
    """
    with db.cursor() as cur:
        cur.execute(
            f"""
            select {_COLUMNS}
              from public.price_quotes
             where provider = %s and sku = %s and region = %s
               and coalesce(tier, '') = coalesce(%s, '')
               and currency = %s and unit = %s
             order by captured_at desc, id desc
             limit %s
            """,
            (key.provider, key.sku, key.region, key.tier, key.currency,
             key.unit, limit),
        )
        return [_quote(row) for row in cur.fetchall()]


def landing_rows(
    db: psycopg.Connection,
    now: datetime,
    curated: Sequence[tuple[str, str]] = LANDING_GPUS,
) -> list[dict[str, Any]]:
    """The public landing board: one row per curated GPU that has a price.

    Read-only over the SAME append-only table the authenticated comparison
    view reads (:func:`latest_quotes` / :func:`quote_history`), filtered to
    a short curated list and reshaped for a visitor who has not signed in.
    This is the whole boundary that makes ``GET /v1alpha1/public/prices``
    (``app.py``) safe to leave unauthenticated: it can only ever return
    curated, already-public vendor catalogue quotes — never a pool, a
    wallet, or a ZC ask, because those are not read here at all, not merely
    filtered out afterwards.

    For each ``(display_label, sku)`` pair, IN ``curated``'s OWN ORDER: the
    newest ``community`` quote, or the newest ``secure`` quote if there is
    no community one, or the row is OMITTED — never zeroed, never a
    placeholder. Same reasoning as :func:`unpriced`, one level down: a
    curated GPU with nothing behind it must not render a number. ``trend``
    compares that quote against the newest STRICTLY OLDER observation
    sharing ``(provider, sku, tier, unit, currency)``; see
    :func:`_landing_trend`.
    """
    rows: list[dict[str, Any]] = []
    for display_label, sku in curated:
        chosen = _landing_quote(db, sku)
        if chosen is None:
            continue
        rows.append(_landing_row(display_label, chosen, now, db))
    return rows


def _landing_quote(db: psycopg.Connection, sku: str) -> Quote | None:
    """The newest ``community`` quote for ``sku``, or else the newest
    ``secure`` one.

    Two queries, not one ``order by tier``: sorting ``community`` ahead of
    ``secure`` textually would be an accident of the alphabet rather than a
    stated preference, and it would break silently and wrongly the day a
    third tier (say, ``pro``) is seeded for the same SKU.
    """
    for tier in _LANDING_TIER_PREFERENCE:
        with db.cursor() as cur:
            cur.execute(
                f"""
                select {_COLUMNS}
                  from public.price_quotes
                 where sku = %s and tier = %s
                 order by captured_at desc, id desc
                 limit 1
                """,
                (sku, tier),
            )
            row = cur.fetchone()
        if row is not None:
            return _quote(row)
    return None


def _landing_row(
    display_label: str, quote: Quote, now: datetime, db: psycopg.Connection
) -> dict[str, Any]:
    """One curated GPU as ``GET /v1alpha1/public/prices`` renders it.

    ``amount`` is the vendor's own digits as a string — :func:`render`'s
    reasoning applies unchanged here. ``age_seconds`` is the raw float
    (``timedelta.total_seconds()``), not :func:`render`'s rounded ``int``:
    the landing contract carries a decimal age, and there is no reason to
    throw the fraction away a second time.
    """
    age = quote_age(quote, now)
    return {
        "gpu": display_label,
        "sku": quote.sku,
        "provider": quote.provider,
        "tier": quote.tier,
        "amount": format(quote.amount.normalize(), "f"),
        "currency": quote.currency,
        "unit": quote.unit,
        "captured_at": _iso(quote.captured_at),
        "age_seconds": age.total_seconds(),
        "stale": is_stale(quote, now),
        "trend": _landing_trend(db, quote),
    }


def _landing_trend(db: psycopg.Connection, quote: Quote) -> dict[str, Any]:
    """How ``quote`` compares with the observation right before it.

    THE KEY IS FIVE FIELDS, NOT SIX — ``(provider, sku, tier, unit,
    currency)``, with ``region`` deliberately left out. The landing board
    shows one number per curated GPU, not a region-scoped one, so "did this
    move" means moved across the venue's whole quoted history for that SKU
    and tier, not the sliver of it captured under one region label. (Every
    SKU in :data:`LANDING_GPUS` is ``region = 'global'`` today, so this is
    dormant now and load-bearing the day that stops being true.)

    A quote sharing the key at the SAME ``captured_at`` is not "previous" —
    it is the same observation, or a second source reporting the same
    instant — so the comparison is strictly ``<``. No earlier row at all
    means this GPU has been priced exactly once: ``direction: "new"``, and
    nothing else, per the contract.

    ``pct`` is ``Decimal`` arithmetic throughout, one decimal place,
    ``ROUND_HALF_UP``, and explicitly signed for ``up``/``down`` — ``+5.6``
    as much as ``-5.6``, so the number alone says which way the price moved
    without reading ``direction``. Equal amounts is ``flat`` and
    short-circuits before any division: both because ``pct`` is defined as
    exactly ``"0.0"`` for that case, and because a zero previous amount
    (never seen among the landing SKUs, all real GPU-hour prices) would
    divide by itself only in the one case where the answer is already known.
    """
    with db.cursor() as cur:
        cur.execute(
            f"""
            select {_COLUMNS}
              from public.price_quotes
             where provider = %s and sku = %s
               and coalesce(tier, '') = coalesce(%s, '')
               and currency = %s and unit = %s
               and captured_at < %s
             order by captured_at desc, id desc
             limit 1
            """,
            (quote.provider, quote.sku, quote.tier, quote.currency,
             quote.unit, quote.captured_at),
        )
        row = cur.fetchone()

    if row is None:
        return {"direction": "new"}

    previous = _quote(row)
    previous_fields = {
        "previous_amount": format(previous.amount.normalize(), "f"),
        "previous_captured_at": _iso(previous.captured_at),
    }

    if quote.amount == previous.amount:
        return {"direction": "flat", "pct": "0.0", **previous_fields}

    diff = quote.amount - previous.amount
    pct = (diff / previous.amount * Decimal(100)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    return {
        "direction": "up" if diff > 0 else "down",
        "pct": f"+{pct}" if pct > 0 else str(pct),
        **previous_fields,
    }


# ---------------------------------------------------------------------------
# Internals.
# ---------------------------------------------------------------------------


def _quote(row: Mapping[str, Any]) -> Quote:
    """One database row as a :class:`Quote`.

    ``amount`` arrives from psycopg as ``decimal.Decimal`` because the column
    is ``numeric``; it is passed through untouched. Nothing here calls
    ``float()`` — that would undo the exactness the column type exists for,
    at the last possible moment and invisibly.
    """
    return Quote(
        id=row["id"],
        provider=row["provider"],
        sku=row["sku"],
        region=row["region"],
        currency=row["currency"],
        amount=row["amount"],
        unit=row["unit"],
        tier=row["tier"],
        attrs=row["attrs"] or {},
        captured_at=row["captured_at"],
        source=row["source"],
        observed_by=row["observed_by"],
        recorded_at=row["recorded_at"],
    )


def _recency(quote: Quote) -> tuple[datetime, int]:
    return (quote.captured_at, quote.id or 0)


def _exact(value: Decimal | int | str | Any, what: str) -> Decimal:
    """A number that is exactly what the source said, or an exception.

    ``bool`` is rejected explicitly: it is an ``int`` in Python, so ``True``
    would otherwise store as a price of 1 — the same trap ``storage.py``
    guards against when summing artifact sizes, and just as silent.
    """
    if isinstance(value, bool):
        raise ImpreciseAmount(f"{what} must be a number, not a bool")
    if isinstance(value, float):
        raise ImpreciseAmount(
            f"{what} arrived as a float ({value!r}). Binary floats cannot "
            "represent decimal prices exactly, and this table's whole value "
            "is that a stored number can be checked against the vendor's "
            "published one. Pass a Decimal, an int, or the string exactly as "
            "the source printed it."
        )
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        return Decimal(value)
    raise ImpreciseAmount(f"{what} must be a Decimal, int or str, not {type(value)!r}")


def _zc_ask_amount(ask_zc_per_hour: int) -> Decimal:
    if isinstance(ask_zc_per_hour, bool) or not isinstance(ask_zc_per_hour, int):
        raise TypeError("ask_zc_per_hour must be integer millicredits")
    if ask_zc_per_hour < 0:
        raise ValueError("ask_zc_per_hour must not be negative")
    return Decimal(ask_zc_per_hour) / Decimal(
        marketplacemod.MILLICREDITS_PER_CREDIT
    )


def _market_amount(value: Decimal) -> str:
    """At least cents, retaining a third millicredit digit when needed."""
    whole, fraction = format(value, ".3f").split(".")
    fraction = fraction.rstrip("0")
    return f"{whole}.{fraction.ljust(2, '0')}"


def _aware(value: datetime, what: str) -> datetime:
    if not isinstance(value, datetime):
        raise NaiveTimestamp(f"{what} must be a datetime, not {type(value)!r}")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise NaiveTimestamp(
            f"{what} has no timezone. An offset-naive instant is an error of "
            "hours that reads as a correct answer, and hours are exactly what "
            "staleness is measured in."
        )
    return value


def _iso(value: datetime) -> str:
    """ISO-8601 UTC with a ``Z``, the spelling every runtime parses.

    ``contributions._instant`` gives the argument at length: ``str()`` on a
    datetime yields a space instead of a ``T``, which Safari's ``new Date()``
    rejects outright. Converted to UTC, never relabelled — stamping a ``Z``
    on a non-zero offset is an error of hours that looks correct.
    """
    return (
        value.astimezone(timezone.utc)
        .replace(tzinfo=None)
        .isoformat(timespec="seconds")
        + "Z"
    )
