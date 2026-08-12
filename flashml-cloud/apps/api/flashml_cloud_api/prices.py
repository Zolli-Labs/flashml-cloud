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

WHAT THIS MODULE WILL NOT DO
----------------------------

**It never converts between currencies, and it never sums across them.**
There is no rate table here, no ``fx``, no ``convert_to_usd``, and adding one
is the single change that would turn a defensible comparison back into a
made-up one. ZC and USD must never be summed — that is absolute. So is USD
and CU: ``CU`` is Alibaba's Compute Unit, it is not money, and no published
CU price has been cited anywhere in this repository. An unconverted CU figure
is honest; a dollar figure derived from a rate nobody can point at is not.

The refusal is generic rather than a pair of special cases: :func:`combine`
requires one currency across everything it adds and raises
:class:`CurrencyMismatch` otherwise, so every future denomination inherits the
rule without anybody remembering to extend it.

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
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.types.json import Json

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
#: USD is what makes the no-conversion rule enforceable by one code path
#: instead of by everybody's memory.
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


class CurrencyMismatch(ValueError):
    """Two amounts in different denominations were about to be combined.

    The one refusal this module exists to make. There is no rate anywhere in
    this repository, so the only honest outcome is to stop — a total that
    silently added USD to CU (or to ZC) would be a number no reader could
    reproduce and no judge could check.
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

    THE NORMALISER, and note what it does not touch: the currency is
    unchanged, always. It puts a venue's native time base onto a common one
    so that per-second and per-hour prices can sit in one column — nothing
    more. Two quotes in different currencies remain incomparable after this
    call, which is the point.

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
    """Whether these two may be put side by side at all.

    Same currency AND same dimension. Necessary, not sufficient — see
    ``_DIMENSIONS``: two SKUs can share a unit and still be different things.
    This function answers "would comparing these be arithmetic nonsense?",
    not "is this a fair comparison?", and only a human answers the second.
    """
    return (
        left.currency == right.currency
        and dimension(left.unit) == dimension(right.unit)
    )


def combine(components: Sequence[tuple[Quote, Decimal | int | str]]) -> Cost:
    """Add up a shape: e.g. 4 vCPU + 8 GiB of memory + 30 GiB of disk, per hour.

    THE ONLY PLACE IN THIS MODULE THAT ADDS TWO PRICES TOGETHER, and it
    refuses to do so across denominations. One currency for every component
    or :class:`CurrencyMismatch` — which is how "ZC and USD must never be
    summed" is enforced by a code path rather than by a convention. The rule
    is written against *any* two differing currencies, so USD/CU and any
    denomination added later inherit it without an edit.

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
            "refusing to add amounts in " + " and ".join(sorted(currencies))
            + ": no exchange rate is published in this system and inventing "
            "one would make the total unreproducible. Show them separately."
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
