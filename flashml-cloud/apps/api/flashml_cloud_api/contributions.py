"""What one account has contributed — a counter, and deliberately not a wallet.

``public.contributions`` has recorded accepted work per (machine, job, task)
since the first migration, and until this module existed a person could not
see their own total anywhere. Credit surfaced only inside one job's panel —
"which machine did the work on THIS job" — so somebody who had donated forty
hours across five jobs had no way to learn it. That is the gap this fills,
and the barter premise of the product does not survive without it.

**Nothing in this system debits these numbers.** There is no spend path, no
balance, no ceiling and no expiry: every credit ever written is still there
and always will be. So the shape below counts *tasks* and *jobs* and says so
in its field names. It must never grow a `balance`, a `remaining`, or a
`credits` — a name that implies a drawdown promises an exchange rate this
product has not designed and cannot honour, and a person who believes they
have a balance is owed one.

The arithmetic lives here as pure functions over rows a query supplied, for
the same reason ``storage.py`` keeps the budget rule out of ``db`` and
``metrics.py`` keeps the goodput rule out of it: the rule is a policy
statement that must be readable and testable without a database, and the
counts are facts. A policy that lives inside a SQL string cannot be read or
argued with, and the ordering, the summing and the null-handling below are
all policy.

**What this counts, and what it refuses to count.** The measurement in ``db``
reads ``contributions``, the credit ledger, and never ``attempts``. Those are
different things with different guarantees:

- ``contributions`` is accepted work. Its unique index (migration 0003) makes
  a duplicate credit impossible at the schema level, so the total here cannot
  drift upward under a retried round callback or a restarted driver.
- ``attempts`` is the lease → (job, task) mapping the completion proxy needs
  because the complete hop carries no job id. It is written by ONE of the two
  credit paths. ``fedavg.on_round`` credits a federated round's contributors
  straight into the ledger and writes no attempts row at all — which is why
  production on 2026-08-03 read 26 credits against 16 attempts on one machine,
  the ten-row gap being federated work. Counting leases would have silently
  dropped every one of them, and it would also have counted a claim that was
  never accepted, which is hard rule 4 exactly backwards.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any


def report(
    machines: Iterable[Mapping[str, Any]], jobs_contributed_to: int
) -> dict[str, Any]:
    """Assemble the response body from the rows a query supplied.

    Every key is always present, including for an account that has
    contributed nothing: **zero is a real answer**, not a 404 and not an
    error. Somebody who just signed up has contributed nothing, that is the
    single most common state this panel renders, and a field that disappears
    when it has nothing to say arrives at the console as ``undefined`` and
    draws as a blank tile that looks like a loading state.

    ``accepted_tasks`` is DERIVED from the machine rows rather than counted a
    second time in SQL. Two independent counts of the same thing eventually
    disagree, and a headline number that does not add up to the list beneath
    it reads as a bug in whichever half is actually right. ``machines`` is
    scoped to one owner and every contribution row belongs to exactly one
    machine, so the sum is not an approximation — it is the same count.

    ``jobs_contributed_to`` cannot be derived the same way: two machines can
    work the same job, and summing per-machine job counts would report it
    twice. It arrives as a distinct count from the query and is passed
    through.
    """
    rows = [_machine(row) for row in machines]

    # Most-contributed first, with the id as tiebreak. The tiebreak is not
    # decoration: equal counts are the ordinary case on a two-laptop fleet,
    # and without it the panel reorders itself between polls, which looks
    # like work moving between machines and is not. Sorted here rather than
    # in the ORDER BY so the guarantee can be tested without a database.
    rows.sort(key=lambda row: (-row["accepted_tasks"], row["machine_id"]))

    return {
        "accepted_tasks": sum(row["accepted_tasks"] for row in rows),
        "jobs_contributed_to": int(jobs_contributed_to),
        "machines": rows,
    }


def _machine(row: Mapping[str, Any]) -> dict[str, Any]:
    """One machine's entry, with both of its nullable fields left null.

    ``hostname`` and ``last_seen_at`` are genuinely absent for real machines:
    an agent supplies its hostname at enrolment and may not have one, and
    ``last_seen_at`` stays null until the first heartbeat. A placeholder in
    either is worse than the null — "Unknown machine" is indistinguishable
    from a host actually called that, and a substituted ``now()`` renders as
    a moment the machine was never seen at.

    ``machine_id`` is stringified because psycopg hands back ``uuid.UUID``,
    which the JSON encoder cannot serialise: left alone, this route would 500
    for the first account that ever contributed and work perfectly for
    everyone who has not.
    """
    return {
        "machine_id": str(row["machine_id"]),
        "hostname": row.get("hostname"),
        "accepted_tasks": int(row["accepted_tasks"]),
        "last_seen_at": _instant(row.get("last_seen_at")),
    }


def _instant(value: Any) -> str | None:
    """A timestamp as unambiguous ISO-8601 UTC, or None.

    ``_jsonable`` in ``app`` renders datetimes with ``str()``, which gives
    ``2026-08-08 00:00:00+00:00`` — a space instead of a ``T``, which Safari's
    ``new Date()`` rejects outright. The older machine routes are stuck with
    that spelling because consumers already parse it; this route is new, so it
    can serve the form every runtime agrees on.

    Converted to UTC, never relabelled: psycopg returns the value in the
    database session's timezone, and stamping a ``Z`` on an offset that is not
    zero is an error of hours that looks exactly like a correct answer.
    """
    if not isinstance(value, datetime):
        return None
    return (
        value.astimezone(timezone.utc)
        .replace(tzinfo=None)
        .isoformat(timespec="seconds")
        + "Z"
    )
