"""The Stage 8 reliability report, and the field it still refuses to fill.

This is the page that has to prove the product's central claim — that work
survives machines dying. That makes it the surface where an invented number
does the most damage: a confidently wrong MTTR looks exactly like a measured
one, and nobody downstream can tell them apart. So the shape below is fixed
and complete, and every field in it is either derived from an event this
deployment actually records or is ``None``.

**``None`` means "not derivable from the events this deployment has", and it
is a first-class answer.** It is not "zero", not "unknown yet", and not a
placeholder to be filled in with something plausible. Every null is
documented with the specific event that would make it real; when somebody
records that event, the field becomes computable and the docstring tells
them so.

**Two of the three nulls became real on 2026-08-11, and how says more than
that they did.** Migration 0015 gave ``public.attempts`` a terminal outcome:
the fail proxy stopped being a pure proxy and now records ``failed``, the
credit path records ``accepted`` in the statement that already existed, and
an attempt whose coordinator-issued lease deadline passed with nothing
reported is resolved as ``expired`` against that deadline. Nothing here was
made computable by relaxing a standard — the missing events named below got
recorded, exactly as this module said they would have to be.

``mttd_seconds`` is still ``None``, and the reason is unchanged and specific:
detection needs the instant a machine actually STOPPED, and nothing writes
that down. Knowing when a lease ran out is not knowing when the host died.

The arithmetic lives here, as pure functions over counts, for the same
reason ``storage.py`` keeps the budget rule out of ``db``: the rule is a
policy statement that must be readable and testable without a database, and
the counts are facts a query supplies. Mixing them is how a page ends up
quietly deciding what a metric means inside a SQL string.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: What the console asks for when it asks for nothing.
DEFAULT_WINDOW_DAYS = 30

#: The widest window the route will answer. Not a performance limit — the
#: queries are indexed counts — but a correctness one: `jobs.artifact_bytes`,
#: terminal statuses and (since 0015) attempt outcomes only started being
#: recorded recently, so a window reaching back years would present a mostly
#: unmeasured period as a measured one.
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
    "tasks_resolved",
    "tasks_accepted",
    "machines_contributing",
)

#: How many decimal places a ratio keeps. Four is well past what a page
#: renders (it draws a percentage) and stops 2/3 arriving as seventeen
#: digits of false precision.
_RATIO_PLACES = 4

#: How many decimal places a duration keeps. Milliseconds: the underlying
#: instants are database timestamps around network hops, so anything finer
#: is precision the measurement does not have.
_SECONDS_PLACES = 3

#: Resolved attempts a (machine, capability class) pair needs before its
#: acceptance rate is allowed to mean anything. Below it, `acceptance_rates`
#: reports the counts and ``None``.
#:
#: The same 5 as `flashruntime.scheduler.RELIABILITY_MIN_EVIDENCE`, and
#: deliberately the same argument: **0.0 means "it failed everything", None
#: means "it has not been asked yet", and treating the second as the first is
#: how a pool stops accepting new volunteers.** A new host's first four
#: attempts must not be able to produce a number anybody acts on. Small on
#: purpose too — it has to be reachable in one sitting on a three-machine
#: pool, or the rule never engages where it is needed most.
MIN_EVIDENCE = 5


def goodput_ratio(accepted: int, resolved: int) -> float | None:
    """The share of RESOLVED work that was accepted, or None.

    ``None`` when nothing has resolved, and this is the distinction the whole
    field turns on: ``0.0`` means "everything that finished was thrown away",
    which is the worst number this page can show, and an account that has
    simply not run anything yet must never be shown it. It is also the
    divide-by-zero, so the guard is load-bearing twice over.

    "Resolved" is counted in ATTEMPTS, not distinct tasks: a task retried
    three times after two machines died was attempted three times and
    accepted once, and collapsing that to one-for-one would erase precisely
    the wasted work this ratio exists to expose.

    **The denominator is ``resolved``, not ``attempted``, and that changed on
    2026-08-11.** It used to be every ``attempts`` row, which meant an
    in-flight attempt was indistinguishable from a failed one: the ratio fell
    the moment work was handed out and only recovered if that exact attempt
    was accepted. On a busy account it drifted downward for ever. Dividing by
    the attempts that actually reached a terminal state makes the number
    bounded, recoverable, and about reliability rather than about how much
    work is currently in flight. Attempts that are unresolved because they
    predate migration 0015 fall out for the same reason: nothing recorded how
    they ended, and "unknown" must not be spent as "failed".
    """
    if resolved <= 0:
        return None
    return round(accepted / resolved, _RATIO_PLACES)


def lost_task_seconds(seconds: float | None, resolved: int) -> float | None:
    """Seconds spent on resolved work that was thrown away, or None.

    ``None`` when nothing has resolved, for exactly the reason
    ``goodput_ratio`` returns None there: ``0.0`` is a claim — "no work was
    wasted" — and it is a flattering one, so an account whose reliability has
    never been tested must not be shown it. Once anything has resolved, 0.0
    is the truth and is reported as such.

    **What it sums, and what it cannot.** Every attempt that reached a
    terminal state without being accepted, over ``resolved_at - claimed_at``.
    For a ``failed`` attempt both instants are observed. For an ``expired``
    one the end is the coordinator's own lease deadline — the instant the
    attempt stopped counting, past which the coordinator refuses the
    heartbeat and rejects the commit. What it still cannot see is an attempt
    with no deadline on record (claimed before 0015, or a claim response that
    could not be parsed): those stay unresolved and contribute nothing here,
    which makes this a floor on wasted time rather than a total.

    ``now() - claimed_at`` remains the tempting substitute for an unresolved
    attempt and remains worse than nothing: it would report a three-week-old
    abandoned attempt as three weeks of burned compute, and the number would
    grow every time somebody loaded the page.
    """
    if resolved <= 0:
        return None
    return round(float(seconds or 0.0), _SECONDS_PLACES)


def mean_time_to_recovery(seconds: float | None, recoveries: int) -> float | None:
    """Mean seconds from a failure being resolved to the replacement attempt
    being accepted, or None.

    ``None`` when no recovery has been observed — never 0.0, which would read
    as "recovery is instantaneous" on the page that exists to prove recovery
    happens at all.

    **A mean over RECOVERIES, not over failures.** A task that failed and was
    never picked up again has no recovery interval; it contributes to
    ``lost_task_seconds`` and to ``goodput_ratio`` and to nothing here.
    Substituting "time since the failure" for it would let one abandoned task
    drag this number up for ever, which is the same growing-forever mistake
    ``lost_task_seconds`` refuses next door.

    The pairing rule lives in ``db.metrics_counts_for_owner``: the earliest
    accepted attempt on the same ``(job_id, task_id)`` that was CLAIMED at or
    after the failure resolved. Claimed-after rather than merely
    accepted-after, so an attempt already running when the failure landed is
    not credited as a response to it.
    """
    if recoveries <= 0:
        return None
    return round(float(seconds or 0.0) / recoveries, _SECONDS_PLACES)


def acceptance_rates(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Per-machine, per-capability-class acceptance rate over resolved
    attempts.

    One entry per ``(machine_id, capability_class)`` pair, sorted by that
    pair, each carrying ``resolved``, ``accepted``, ``acceptance_rate`` and
    ``median_seconds``. A pure function over rows a query supplied, for the
    reason the rest of this module is: the thresholds below are policy, and a
    policy inside a SQL string cannot be read or argued with.

    Each row is expected to carry ``machine_id``, ``capability_class``,
    ``outcome``, and optionally ``duration_s``.

    **NEVER AGGREGATED ACROSS CAPABILITY CLASS, and there is no total.** A
    host that accepts 95% of cpu work and 40% of gpu-24gb work is not a 0.9
    host; it is a good CPU host that fails GPU tasks, and the aggregate
    number is exactly wrong at the only moment anybody consults it — when
    deciding whether to hand it the GPU task. The pair is the key, no
    all-class rollup is produced, and none should be added: a caller that
    wants one has to write the flattening down where it can be reviewed.

    The class labels the WORK, not the hardware. The same machine appears
    once per class of task it has attempted, which is what makes the split
    above meaningful — a host with a GPU still runs CPU tasks. Labels are
    opaque here: this function never parses one, so a caller may not smuggle
    a hierarchy in and have it silently flattened.

    **Below ``MIN_EVIDENCE`` resolved attempts the rate is ``None``, not a
    number.** Read the constant's own note: 0.0 is "it failed everything" and
    None is "it has not been asked yet", and a pool that confuses the two
    stops accepting new volunteers on their first unlucky task. The counts
    are still reported — "1 of 2" is useful to a human and useless to a
    threshold, and returning them separately from the rate is what keeps a
    caller from computing the ratio itself.

    **Unresolved attempts are not evidence.** A row with no ``outcome`` is in
    flight, or predates migration 0015; it is dropped entirely rather than
    counted in the denominator, so a machine's rate cannot fall merely
    because it is busy.

    **Federated work is absent by construction, and timing excludes anything
    untimed anyway.** ``fedavg.on_round`` credits a round's contributors
    straight into ``contributions`` and writes no ``attempts`` row at all —
    production read 26 credits against 16 attempts on one machine on
    2026-08-03, and the ten-row gap was federated work — so no federated
    contribution can reach this function. Belt and braces, a row that carries
    no usable ``duration_s`` (which is exactly the shape a federated credit
    has: ``fedavg`` credits from the coordinator's task view, which reports no
    duration) is excluded from ``median_seconds`` while still counting toward
    the rate. A group with nothing timed reports ``None`` rather than 0.0,
    the same distinction ``peer_task_durations`` draws for the same reason.
    """
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        outcome = row.get("outcome")
        if not outcome:
            continue  # unresolved: in flight or pre-0015, never "failed"
        key = (str(row.get("machine_id")), str(row.get("capability_class")))
        group = groups.setdefault(
            key, {"resolved": 0, "accepted": 0, "durations": []}
        )
        group["resolved"] += 1
        if outcome == "accepted":
            group["accepted"] += 1
        duration = row.get("duration_s")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            group["durations"].append(float(duration))

    return [
        {
            "machine_id": machine_id,
            "capability_class": capability_class,
            "resolved": group["resolved"],
            "accepted": group["accepted"],
            "acceptance_rate": (
                round(group["accepted"] / group["resolved"], _RATIO_PLACES)
                if group["resolved"] >= MIN_EVIDENCE
                else None
            ),
            "median_seconds": _median(group["durations"]),
        }
        for (machine_id, capability_class), group in sorted(groups.items())
    ]


def _median(values: list[float]) -> float | None:
    """The middle of ``values``, or None when there is nothing to take a
    middle of.

    Median rather than mean, matching ``verify.timing_verdict``: one machine
    that hung until its lease expired should move the typical duration by
    nothing, and with a mean it moves it by a lot.
    """
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], _SECONDS_PLACES)
    return round((ordered[middle - 1] + ordered[middle]) / 2, _SECONDS_PLACES)


def report(window_days: int, counts: Mapping[str, Any]) -> dict[str, Any]:
    """Assemble the response body from the counts a query supplied.

    Every key is always present. A field that disappears when it has nothing
    to say arrives at the console as ``undefined`` rather than as an error,
    and renders as a blank tile that looks like a loading state.
    """
    accepted = int(counts.get("tasks_accepted", 0))
    resolved = int(counts.get("tasks_resolved", 0))
    body: dict[str, Any] = {"window_days": window_days}
    body.update({name: int(counts.get(name, 0)) for name in COUNT_FIELDS})
    body["goodput_ratio"] = goodput_ratio(accepted=accepted, resolved=resolved)

    #: Seconds spent on work that was thrown away.
    #:
    #: REAL since 2026-08-11. It needed an end time for an attempt that did
    #: not succeed, and migration 0015 supplies one: the fail proxy records
    #: `outcome='failed'` with the instant it was told, and an attempt whose
    #: coordinator-issued lease deadline passed unreported is resolved as
    #: `expired` AGAINST THAT DEADLINE. A floor rather than a total —
    #: attempts with no deadline on record stay unresolved and contribute
    #: nothing — and `lost_task_seconds` says so.
    body["lost_task_seconds"] = lost_task_seconds(
        seconds=counts.get("lost_seconds_total"), resolved=resolved
    )

    #: Mean seconds from a machine actually stopping to the system noticing.
    #:
    #: STILL NULL. NEEDS: the instant the machine stopped. Only the second
    #: half of the interval arrived with 0015 — a lease deadline tells us
    #: when the attempt stopped counting, not when the host died, and the
    #: difference between them is the entire quantity being measured.
    #: `machines.last_seen_at` is a single mutable column overwritten by
    #: every heartbeat, so it carries no history of when beats stopped, and
    #: the coordinator's LEASE_EXPIRED / NODE_HEARTBEAT_LOST events live in
    #: its own ledger, reachable only one job at a time over HTTP — which a
    #: page summarising a month of jobs cannot do.
    #:
    #: The tempting substitute is `deadline - lease_seconds`, i.e. "it must
    #: have died at its last heartbeat". That is not a detection time; it is
    #: the lease window written twice, and it would report the same constant
    #: for every host on the network.
    body["mttd_seconds"] = None

    #: Mean seconds from a failure being resolved to the replacement work
    #: being accepted.
    #:
    #: REAL since 2026-08-11. Both instants are now recorded: the failure's
    #: `resolved_at` (observed on the fail hop, or the lease deadline for an
    #: expired attempt) and the replacement attempt's own `resolved_at`,
    #: paired on `(job_id, task_id)`. A mean over recoveries, not over
    #: failures — see `mean_time_to_recovery`.
    body["mttr_seconds"] = mean_time_to_recovery(
        seconds=counts.get("recovery_seconds_total"),
        recoveries=int(counts.get("recoveries_observed", 0)),
    )

    return body
