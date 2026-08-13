#!/usr/bin/env python3
"""Carry a published rate forward to a fresh `captured_at`. NOT a new reading.

    PY=flashml-cloud/apps/api/.venv/bin/python
    S=flashml-cloud/scripts/prices/restamp_price_quotes.py

    # What it WOULD append. Reads only. THIS IS THE DEFAULT.
    set -a; . flashml-cloud/.env.dev; set +a
    $PY $S

    # Append the rows.
    $PY $S --write

WHAT THIS IS FOR
----------------
`prices.is_stale` stops vouching for a quote at 24 hours and every USD figure
on the trade-off panel then renders `· STALE`. The seeded RunPod capture is
stamped `2026-08-12T03:55:00Z`, so the panel goes stale one day later, on its
own, with nothing wrong and nothing to fix — which on a projector reads as a
broken product rather than as a working freshness policy.

`price_quotes` is append-only and `prices.latest_quotes` takes the newest row
per (provider, sku, region, tier, currency, unit). So the fix is an INSERT.
Nothing is rewritten, nothing is deleted, and the original capture stays in the
history where `prices.quote_history` can still show it.

WHAT THIS IS NOT
----------------
**It does not observe a price.** It copies the amount that is already stored,
exactly, and stamps it with the current instant. If RunPod moved the RTX A5000
off $0.16 an hour ago, this script will happily carry $0.16 forward looking
brand new, because it never asked RunPod anything. That is the whole hazard,
and it is the one `prices.py` names everywhere — "a scraped price shown as live
is a lie with a delay".

So three things make the lie impossible to tell by accident:

1. **`observed_by` is `carried-forward`**, never `runpod-api`. The seed's own
   value says the evidence came from the vendor's catalogue at that instant.
   A copy is a different kind of evidence and says so, in the column the
   schema created for exactly that distinction.
2. **`source` names the reading it was copied from**, and says it was not
   re-observed. `components/market/PricesPanel.tsx` renders `source` beside the
   number, so the console shows the words "carried forward, not re-observed" to
   anyone who looks.
3. **It refuses to launder an old rate.** `--max-carry-days` (default 7) is
   measured from the ROOT capture — the vendor reading at the bottom of the
   chain, not the previous copy — so re-running this daily cannot keep a price
   from June looking fresh in September. Past the limit it refuses and tells
   you to re-pull from the venue.

The root instant and the root source travel in `attrs.restamped_from`, which
is where they have to live: `attrs` is documented as context for a human
checking the number, nothing prices anything off it, and the alternative —
parsing them back out of the `source` sentence — would nest one copy's prose
inside the next one's on every run.

WHY IT DOES NOT USE `Settings.from_env()`
-----------------------------------------
`db.connect` needs a `Settings`, and `Settings.from_env()` refuses to build
without `COORDINATOR_URL` and `COORDINATOR_OPERATOR_TOKEN`. This script touches
one table and never speaks to a coordinator; needing one up to run it five
minutes before a demo is how the demo does not get its price refreshed. It
reads `DATABASE_URL` and nothing else, and it hardcodes no connection string.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence

from flashml_cloud_api import prices as pricesmod

#: What the `observed_by` column says about a copy. Deliberately NOT one of
#: the seed's values (`runpod-api`, `alibaba-doc`): those name evidence that
#: came from the venue, and this did not.
CARRIED_FORWARD = "carried-forward"

#: Where the ROOT reading is remembered across repeats. See the docstring.
PROVENANCE_KEY = "restamped_from"

#: How far a rate may be carried from the instant it was actually read before
#: this script refuses. A week: long enough that a demo week does not need a
#: RunPod credential, short enough that nobody can run this on a cron and end
#: up with a price list of fossils that all look like they were pulled today.
DEFAULT_MAX_CARRY_DAYS = 7

#: The venue the trade-off panel's only USD figure comes from. Everything
#: RunPod publishes is a whole-machine `gpu-hour`, which is the one shape
#: `app._TRADEOFF_MACHINE_HOUR` will feed to the curve.
DEFAULT_PROVIDERS = ("runpod",)

EXIT_OK = 0
EXIT_REFUSED = 2


@dataclass(frozen=True)
class Restamp:
    """One quote, and the row that would carry it forward."""

    quote: pricesmod.Quote
    #: The vendor reading at the bottom of the chain — this quote itself on
    #: the first copy, and the same instant on every copy after it.
    root_captured_at: datetime
    root_source: str
    captured_at: datetime
    source: str
    attrs: Mapping[str, Any]

    @property
    def carried_days(self) -> float:
        return (self.captured_at - self.root_captured_at).total_seconds() / 86400.0


def root_of(quote: pricesmod.Quote) -> tuple[datetime, str]:
    """The vendor reading this quote descends from.

    A quote nothing has copied yet IS its own root. A copy carries its root
    forward verbatim, so a chain of ten re-stamps still measures its age from
    the one instant RunPod was actually asked.
    """
    recorded = quote.attrs.get(PROVENANCE_KEY)
    if isinstance(recorded, Mapping):
        when, what = recorded.get("captured_at"), recorded.get("source")
        if isinstance(when, str) and isinstance(what, str) and when and what:
            try:
                parsed = datetime.fromisoformat(when.replace("Z", "+00:00"))
            except ValueError:
                parsed = None
            if parsed is not None and parsed.tzinfo is not None:
                return parsed, what
    return quote.captured_at, quote.source


def plan(quotes: Sequence[pricesmod.Quote], now: datetime) -> list[Restamp]:
    """What would be appended, computed without touching a database.

    Pure, and the amount is passed through untouched — this function has no
    branch that can produce a number the stored quote did not already carry.
    """
    out: list[Restamp] = []
    for quote in quotes:
        root_at, root_source = root_of(quote)
        out.append(
            Restamp(
                quote=quote,
                root_captured_at=root_at,
                root_source=root_source,
                captured_at=now,
                source=(
                    f"carried forward, not re-observed: {root_source} at "
                    f"{root_at.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')}"
                ),
                attrs={
                    **dict(quote.attrs),
                    PROVENANCE_KEY: {
                        "captured_at": root_at.astimezone(timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "source": root_source,
                    },
                },
            )
        )
    return out


def too_old(plans: Sequence[Restamp], max_carry: timedelta) -> list[Restamp]:
    """The copies this script refuses to make. See `--max-carry-days`."""
    return [p for p in plans if p.captured_at - p.root_captured_at > max_carry]


# ---------------------------------------------------------------------------
# the operator surface
# ---------------------------------------------------------------------------


def say(line: str = "") -> None:
    print(line, flush=True)


def banner(title: str, lines: Sequence[str] = (), char: str = "=") -> None:
    say()
    say(char * 78)
    say(title)
    for line in lines:
        say(f"  {line}")
    say(char * 78)


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def report(plans: Sequence[Restamp], now: datetime, max_age: timedelta) -> None:
    say()
    say(
        f"{'sku':<28} {'tier':<10} {'amount':>10}  "
        f"{'stored capture':<21} {'age':>8}  now"
    )
    say("-" * 100)
    for p in plans:
        age = pricesmod.quote_age(p.quote, now)
        flag = "STALE" if pricesmod.is_stale(p.quote, now, max_age) else "ok"
        say(
            f"{p.quote.sku[:28]:<28} {(p.quote.tier or '-'):<10} "
            f"{format(p.quote.amount.normalize(), 'f'):>10}  "
            f"{_iso(p.quote.captured_at):<21} "
            f"{age.total_seconds() / 3600:>7.1f}h  {flag}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Append a fresh captured_at for prices already in price_quotes. "
            "Copies the stored rate; observes nothing."
        )
    )
    parser.add_argument(
        "--provider",
        action="append",
        metavar="NAME",
        help=(
            "Venue to carry forward, repeatable. "
            f"Default: {', '.join(DEFAULT_PROVIDERS)}."
        ),
    )
    parser.add_argument(
        "--sku",
        action="append",
        metavar="SKU",
        help="Only this SKU, repeatable. Default: every SKU the venue has.",
    )
    parser.add_argument(
        "--max-carry-days",
        type=float,
        default=DEFAULT_MAX_CARRY_DAYS,
        help=(
            "Refuse to copy a rate whose ORIGINAL vendor reading is older "
            f"than this. Default: {DEFAULT_MAX_CARRY_DAYS}."
        ),
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually append the rows. Without it this only reports.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    import psycopg
    from psycopg.rows import dict_row

    args = build_parser().parse_args(argv)
    providers = list(args.provider or DEFAULT_PROVIDERS)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    max_age = pricesmod.DEFAULT_MAX_AGE
    max_carry = timedelta(days=args.max_carry_days)

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        banner(
            "REFUSED: DATABASE_URL is not set",
            [
                "Source the environment first, e.g.:",
                "  set -a; . flashml-cloud/.env.dev; set +a",
            ],
            char="!",
        )
        return EXIT_REFUSED

    with psycopg.connect(dsn, row_factory=dict_row) as db:
        db.autocommit = True
        quotes = pricesmod.latest_quotes(db, providers=providers)
        if args.sku:
            wanted = set(args.sku)
            quotes = [q for q in quotes if q.sku in wanted]

        if not quotes:
            banner(
                "REFUSED: nothing to carry forward",
                [
                    f"No quote is recorded for: {', '.join(providers)}"
                    + (f", sku {', '.join(args.sku)}" if args.sku else ""),
                    "There is no rate to copy, and this script never invents "
                    "one. Record a real observation first.",
                ],
                char="!",
            )
            return EXIT_REFUSED

        plans = plan(quotes, now)

        banner(
            "RE-STAMP — copies a stored rate, observes nothing",
            [
                f"venues: {', '.join(providers)}",
                f"quotes: {len(plans)}",
                f"new captured_at: {_iso(now)}",
                (
                    "--write IS SET: rows will be appended."
                    if args.write
                    else "Dry run. Nothing will be written. Pass --write to append."
                ),
            ],
        )
        report(plans, now, max_age)

        refused = too_old(plans, max_carry)
        if refused:
            oldest = min(p.root_captured_at for p in refused)
            banner(
                "REFUSED: these rates were last actually read too long ago",
                [
                    f"{len(refused)} of {len(plans)} descend from a vendor "
                    f"reading older than {args.max_carry_days} days "
                    f"(oldest: {_iso(oldest)}).",
                    "Carrying them forward again would make a price nobody "
                    "has checked in that long look like it was pulled today.",
                    "Re-pull from the venue and record a real observation, or "
                    "raise --max-carry-days deliberately.",
                ],
                char="!",
            )
            return EXIT_REFUSED

        if not args.write:
            say()
            say(
                f"Dry run: {len(plans)} rows WOULD be appended with "
                f"captured_at={_iso(now)}, observed_by={CARRIED_FORWARD}."
            )
            say("Re-run with --write to append them.")
            return EXIT_OK

        written = 0
        for p in plans:
            stored = pricesmod.record_quote(
                db,
                provider=p.quote.provider,
                sku=p.quote.sku,
                region=p.quote.region,
                currency=p.quote.currency,
                # The stored Decimal, passed through. No float, no rounding,
                # no arithmetic of any kind between the read and the write.
                amount=p.quote.amount,
                unit=p.quote.unit,
                tier=p.quote.tier,
                attrs=p.attrs,
                captured_at=p.captured_at,
                source=p.source,
                observed_by=CARRIED_FORWARD,
            )
            assert isinstance(stored.amount, Decimal)
            if stored.amount != p.quote.amount:  # pragma: no cover - defensive
                raise SystemExit(
                    f"refusing to continue: {p.quote.sku} stored "
                    f"{stored.amount} for a carry-forward of {p.quote.amount}"
                )
            written += 1

        banner(
            "APPENDED",
            [
                f"{written} rows, captured_at={_iso(now)}, "
                f"observed_by={CARRIED_FORWARD}.",
                "Every amount is the one that was already stored. Nothing was "
                "observed, rewritten or deleted.",
                "The panel will read these as current for the next "
                f"{int(max_age.total_seconds() // 3600)} hours.",
            ],
        )
        return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
