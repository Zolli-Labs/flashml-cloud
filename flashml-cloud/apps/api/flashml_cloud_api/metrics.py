"""The Stage 8 reliability report, and the three fields it refuses to fill.

This is the page that has to prove the product's central claim — that work
survives machines dying. That makes it the surface where an invented number
does the most damage: a confidently wrong MTTR looks exactly like a measured
one, and nobody downstream can tell them apart. So the shape below is fixed
and complete, and every field in it is either derived from an event this
deployment actually records or is ``None``.

**``None`` means "not derivable from the events this deployment has", and it
is a first-class answer.** It is not "zero", not "unknown yet", and not a
placeholder to be filled in with something plausible. Each of the three
nulls is documented with the specific event that would make it real; when
somebody records that event, the field becomes computable and the docstring
tells them so.

The arithmetic lives here, as pure functions over counts, for the same
reason ``storage.py`` keeps the budget rule out of ``db``: the rule is a
policy statement that must be readable and testable without a database, and
the counts are facts a query supplies. Mixing them is how a page ends up
quietly deciding what a metric means inside a SQL string.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: What the console asks for when it asks for nothing.
DEFAULT_WINDOW_DAYS = 30

#: The widest window the route will answer. Not a performance limit — the
#: queries are indexed counts — but a correctness one: `jobs.artifact_bytes`
#: and terminal statuses only started being recorded recently, so a window
#: reaching back years would present a mostly-unmeasured period as a
#: measured one.
MAX_WINDOW_DAYS = 365

#: The counts the report renders straight through. Named here so that
#: `report` can fill a missing one with 0 — which is honest for every one of
#: them, because each comes from a query that returns 0 for an account with
#: no rows, and 0 genuinely means "none of these happened".
COUNT_FIELDS = (
    "jobs_total",
    "jobs_succeeded",
    "jobs_partial",
    "jobs_failed",
    "tasks_attempted",
    "tasks_accepted",
    "machines_contributing",
)

#: How many decimal places a ratio keeps. Four is well past what a page
#: renders (it draws a percentage) and stops 2/3 arriving as seventeen
#: digits of false precision.
_RATIO_PLACES = 4


def goodput_ratio(accepted: int, attempted: int) -> float | None:
    """The share of attempted work that was accepted, or None.

    ``None`` when nothing was attempted, and this is the distinction the
    whole field turns on: ``0.0`` means "everything attempted was thrown
    away", which is the worst number this page can show, and an account that
    has simply not run anything yet must never be shown it. It is also the
    divide-by-zero, so the guard is load-bearing twice over.

    "Attempted" is counted in ATTEMPTS, not distinct tasks: a task retried
    three times after two machines died was attempted three times and
    accepted once, and collapsing that to one-for-one would erase precisely
    the wasted work this ratio exists to expose.
    """
    if attempted <= 0:
        return None
    return round(accepted / attempted, _RATIO_PLACES)


def report(window_days: int, counts: Mapping[str, int]) -> dict[str, Any]:
    """Assemble the response body from the counts a query supplied.

    Every key is always present. A field that disappears when it has nothing
    to say arrives at the console as ``undefined`` rather than as an error,
    and renders as a blank tile that looks like a loading state.
    """
    accepted = int(counts.get("tasks_accepted", 0))
    attempted = int(counts.get("tasks_attempted", 0))
    body: dict[str, Any] = {"window_days": window_days}
    body.update({name: int(counts.get(name, 0)) for name in COUNT_FIELDS})
    body["goodput_ratio"] = goodput_ratio(accepted=accepted, attempted=attempted)

    # ---------------------------------------------------------------------
    # The three that cannot be computed. Read the reason before "fixing" one.
    # ---------------------------------------------------------------------

    #: Seconds spent on work that was thrown away.
    #:
    #: NEEDS: an end time for an attempt that did not succeed. `attempts`
    #: (migration 0004) records `claimed_at` and `accepted_at` and nothing
    #: else — its own comment says so: "failed and expired attempts leave no
    #: mark". `POST /v1alpha1/attempts/{lease_id}/fail` is a pure proxy that
    #: writes no row, and lease EXPIRY is decided by the coordinator's
    #: sweeper, which never informs this API at all. So for the attempts
    #: that make up lost work there is a start and no stop.
    #:
    #: `now() - claimed_at` is the tempting substitute and is worse than
    #: nothing: it would report a three-week-old abandoned attempt as three
    #: weeks of burned compute, and the number would grow every time
    #: somebody loaded the page.
    body["lost_task_seconds"] = None

    #: Mean seconds from a machine actually stopping to the system noticing.
    #:
    #: NEEDS: both of those instants. Neither is written down here.
    #: `machines.last_seen_at` is a single mutable column overwritten by
    #: every heartbeat, so it carries no history of when beats stopped, and
    #: the coordinator's LEASE_EXPIRED / NODE_HEARTBEAT_LOST events live in
    #: its own ledger, reachable only one job at a time over HTTP — which a
    #: page summarising a month of jobs cannot do.
    body["mttd_seconds"] = None

    #: Mean seconds from noticing a failure to the replacement work being
    #: accepted.
    #:
    #: NEEDS: the first of those two instants — the same one MTTD is missing.
    #: The second half exists (`attempts.accepted_at` on the replacement
    #: attempt), which makes this the closest of the three to being real:
    #: recording a detection timestamp would deliver MTTD and MTTR together.
    #: Half an interval is not a duration, so until then it is null.
    body["mttr_seconds"] = None

    return body
