#!/usr/bin/env python3
"""D-9 generalized: wake/recovery latency and its cost, for EVERY venue.

The hibernation half of this question is already measured. `hibernation_
modes_probe.py` timed an Alibaba FC Agent Sandbox pausing and waking (deep
hibernation wake p50 **1109 ms**) and modelled what it costs to sit
hibernated (**0.00351553 USD/hr** against **0.05654376 USD/hr** active — a
93.78% saving), and `cost_worksheet.py` renders those with their sources.
That covers exactly two of the fleet's five venues: `fc-sandbox` and
`fc-gpu`, the only ones whose `hibernates` flag is True in
`apps/api/flashml_cloud_api/router/venues.py`.

**This script covers the other three.** An owned FlashNode host, a RunPod
pod and an ECS GPU instance cannot hibernate. Their analogue of
hibernate→wake is *die→another machine resumes from the checkpoint*, and
FlashML has its own form of hibernation for them: **release the machine,
keep the state.** The machine leaves the fleet (and stops billing) while the
job's committed checkpoint waits in the artifact store for whoever claims
the task next. So the numbers that answer "what does idling cost here" are
not pause/resume timings — they are the lease ledger's own recovery
intervals, and the rate of the machine that has to redo the uncommitted
work.

Together the two artifacts cover every machine type the fleet controls.

WHAT IS DERIVED FROM THE LEDGER (no extra instrumentation, no clock skew)
------------------------------------------------------------------------
The coordinator already writes an append-only event ledger, exposed at
``GET /v1alpha1/jobs/{id}/events``. `LeaseManager._emit` stamps
``data.task_id`` and ``data.node_id`` on every lease event and timestamps it
server-side, so all four intervals below are differences between two
timestamps taken by **the same clock** — nothing here can be skewed by a
worker's wall clock:

``detection_s``
    ``LEASE_EXPIRED`` − the dying node's last ``LEASE_RENEWED``/
    ``LEASE_CLAIMED`` for that task. The ledger-derivable analogue of MTTD:
    how long the fleet believed a dead machine was alive. It is bounded by
    the job's lease window plus the 2 s sweeper period.

``reclaim_s``
    ``LEASE_EXPIRED`` → the next ``LEASE_CLAIMED`` on the same task. **This
    is the wake.** A different machine picks the task up; the interval is
    the non-hibernating venue's answer to "how long until capacity is warm
    again", and it is the one directly comparable to FC's 1109 ms.

``resume_to_progress_s``
    That ``LEASE_CLAIMED`` → the first following
    ``CHECKPOINT_MANIFEST_COMMITTED`` or ``TASK_COMMIT_ACCEPTED`` for the
    task. Claiming is not progressing; this is the time until the resumed
    work is *demonstrably* moving again.

``recomputed_s``
    The wall-clock span of work that was in flight and never committed:
    last proof of life − the last committed progress marker before the
    death (or, if nothing was ever committed on that attempt, − the attempt's
    own ``LEASE_CLAIMED``). This is the work the next machine has to redo,
    and it is billed at the venue's ACTIVE rate. Derived from event gaps;
    a step-count derivation would need step numbers the job feed does not
    carry, so none is invented.

WHAT WOULD NEED AN EXTERNAL CLOCK
---------------------------------
The ledger cannot see the instant a machine actually stopped — only the
instant the coordinator stopped hearing from it. So "true kill → expiry" is
**not** derivable, and every interval above is labelled ``derived`` rather
than ``measured``. Supply ``--kill-at`` (from a harness that did the killing,
e.g. `e2e/competition/run_local_recovery.sh`'s ``KILLED_AT``) and the two
intervals that anchor on that instant — ``kill_to_expire_s`` and
``kill_to_progress_s`` — are computed and labelled ``measured``. Without it
they are ``None`` with a named reason. They are never guessed.

A KNOWN GAP IN THE JOB FEED, WORTH READING BEFORE YOU TRUST A None
------------------------------------------------------------------
``CHECKPOINT_MANIFEST_COMMITTED`` does **not** appear in
``GET /v1alpha1/jobs/{id}/events``. `service/checkpoints._scope` addresses the
catalog by a composite scope — ``"<job_id>::<task_id>"`` — and emits checkpoint
events under that string as their ``job_id``, so they land in the ledger under
a different key. (`e2e/competition/run_local_recovery.sh` carries the same note;
it cost a debugging round there.) In practice the progress marker this tool
finds in a job feed is ``TASK_COMMIT_ACCEPTED``. Concatenate both feeds into
the input file and the manifest events are used too — the parser takes whatever
events it is given.

HOUSE RULES OBSERVED
--------------------
* An underivable number is ``None`` with a named reason. Never ``0.0``.
* A venue with no supplied rate is unpriced, with a named reason. No rate is
  ever invented; `rented_capacity.usd_per_hour` is itself nullable and its
  own column comment says a null "means the venue quoted no price".
* Every figure carries a unit, a kind (``measured``/``derived``), and a
  source trail — the convention `cost_worksheet.py` established.

Usage
-----
    # file mode — what the tests and the local rehearsal use. No network.
    python3 recovery_latency.py --events-json events.json \\
        --kill-at 1755091200 --rates rates.json --out-dir ../../.evidence

    # fetch mode — owner-run, against YOUR OWN coordinator or API.
    python3 recovery_latency.py --api-base https://flashml-api-dev.onrender.com \\
        --job-id <job-id> --token "$TOKEN"

Fetch mode issues exactly one HTTP GET, against a FlashML coordinator/API you
operate. **No paid provider API is ever called from this script.**
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

#: `.evidence/` sits two levels up from `scripts/competition/`, the same depth
#: every other script in this directory uses.
EVIDENCE_DIR = Path(__file__).resolve().parents[2] / ".evidence"

SCHEMA = "recovery_latency_v1"

#: The FC hibernation evidence this tool defers to for the two venues it does
#: NOT cover. Named in the header of every artifact so the pair reads as one
#: answer rather than two unrelated files.
HIBERNATION_EVIDENCE_GLOB = "alibaba-hibernation-modes-*.json"

# --- the event vocabulary, as `flashruntime/protocol/v1alpha1.py` spells it --

LEASE_CLAIMED = "LEASE_CLAIMED"
LEASE_RENEWED = "LEASE_RENEWED"
LEASE_EXPIRED = "LEASE_EXPIRED"
TASK_REQUEUED = "TASK_REQUEUED"
TASK_COMMIT_ACCEPTED = "TASK_COMMIT_ACCEPTED"
TASK_ATTEMPT_FAILED = "TASK_ATTEMPT_FAILED"
CHECKPOINT_MANIFEST_COMMITTED = "CHECKPOINT_MANIFEST_COMMITTED"

#: Proof that the holder was alive. The attempt-start marker in the lease path
#: is `LEASE_CLAIMED` — there is no TASK_ATTEMPT_STARTED in this ledger.
LIVENESS_TYPES = (LEASE_CLAIMED, LEASE_RENEWED)

#: "The resumed work is demonstrably progressing." Whichever lands first.
PROGRESS_TYPES = (CHECKPOINT_MANIFEST_COMMITTED, TASK_COMMIT_ACCEPTED)

# --- venue shape ------------------------------------------------------------

#: A MIRROR of `apps/api/flashml_cloud_api/router/venues.py` (`Venue.hibernates`),
#: verified 2026-08-13 and pinned by `test_recovery_latency.py`, which imports
#: the real table and fails if these drift. Mirrored rather than imported
#: because this script must run under a bare `python3` from the e2e rehearsal,
#: where `flashml_cloud_api` is not installed.
DEFAULT_VENUE_HIBERNATES = {
    "owned": False,
    "runpod": False,
    "fc-sandbox": True,
    "fc-gpu": True,
    "ecs-gpu": False,
}

#: Machines somebody already owns and runs. There is no hourly rate to look
#: up because nobody is billed one — that is a fact about the venue, not a
#: missing measurement, so it prices at exactly 0.00 with a reason that says so.
VOLUNTEER_VENUES = ("owned",)
VOLUNTEER_SOURCE = "volunteer hardware, no hourly rate"

#: Short, because it appears once per unpriced venue and a paragraph repeated
#: five times is a table nobody reads. The long form is `WHERE_RATES_COME_FROM`,
#: printed once in the artifact.
UNPRICED_REASON = (
    "no usd_per_hour supplied for venue '{venue}' — this tool never invents a "
    "rate; pass one with --rates"
)
WHERE_RATES_COME_FROM = (
    "**Where a rate would come from.** RunPod's is a per-rental fact in "
    "`public.rented_capacity.usd_per_hour` — itself nullable, and its own column "
    "comment says \"Null means the venue quoted no price\". ECS GPU's is whatever "
    "`DescribePrice` answered for the instance type this deployment is pointed at "
    "($1.279/hr for the default `ecs.gn6i-c4g1.xlarge`, measured 2026-08-12). The "
    "FC pair's is measured in the hibernation evidence and read from it when that "
    "file is present. None of them is hard-coded here, because a plausible rate "
    "nobody measured is worse than a blank cell."
)


# ---------------------------------------------------------------------------
# Metric — a number that is allowed to be absent, but never allowed to be fake
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Metric:
    """One figure, its unit, how it was obtained, and — if absent — why.

    ``kind`` is ``"derived"`` for anything computed from two ledger
    timestamps, ``"measured"`` for anything anchored on an externally supplied
    kill instant, and ``"unavailable"`` when ``value is None``. The house rule
    this type exists to enforce: an unavailable number is ``None`` with a
    ``reason``, never ``0.0`` — a zero recovery latency or a zero cost is the
    most flattering value either metric can take, and a reader scanning a
    column of numbers will not notice a footnote.
    """

    value: float | None
    unit: str
    kind: str
    source: str
    reason: str | None = None

    @classmethod
    def absent(cls, unit: str, reason: str) -> "Metric":
        return cls(value=None, unit=unit, kind="unavailable", source="", reason=reason)

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"value": self.value, "unit": self.unit, "kind": self.kind}
        if self.source:
            out["source"] = self.source
        if self.reason:
            out["reason"] = self.reason
        return out


# ---------------------------------------------------------------------------
# Parsing the wire shape of GET /v1alpha1/jobs/{id}/events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LedgerEvent:
    index: int              # position in the input, the stable tiebreaker
    seq: int | None         # SQLite `events.seq`, when the payload carries it
    type: str
    timestamp: datetime
    task_id: str | None
    node_id: str | None
    message: str
    raw: dict


def _parse_timestamp(value: Any) -> datetime | None:
    """ISO-8601 (with or without a trailing ``Z``), or epoch seconds.

    The wire carries `Event.timestamp` as `datetime.isoformat()`. A naive
    value is read as UTC — the coordinator stamps `utcnow()`, and refusing
    a naive timestamp would reject the ledger's own historical rows.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_events(payload: Any) -> tuple[list[LedgerEvent], list[str]]:
    """The exact wire shape of ``GET /v1alpha1/jobs/{id}/events``, normalised.

    That endpoint returns ``ledger.events_for(job_id)`` — a JSON list of
    `Event` models: ``{schema_version, job_id, type, timestamp, source,
    message, data}``, with ``data`` carrying ``task_id`` and (on lease events)
    ``node_id``. A dict with an ``events`` key is accepted too, so a hand-saved
    capture that wrapped the list still parses.

    Events with an unreadable timestamp are dropped and counted in the
    returned warnings rather than silently ignored: a dropped lease event
    changes an interval, and a reader must be able to see that it happened.
    """
    warnings: list[str] = []
    if isinstance(payload, dict):
        rows = payload.get("events")
        if rows is None:
            rows = payload.get("items")
        if rows is None:
            return [], ["input JSON was an object with no 'events' key — nothing to read"]
    else:
        rows = payload
    if not isinstance(rows, list):
        return [], [f"input JSON was a {type(rows).__name__}, expected a list of events"]

    events: list[LedgerEvent] = []
    dropped = 0
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            dropped += 1
            continue
        ts = _parse_timestamp(row.get("timestamp"))
        etype = row.get("type")
        if ts is None or not isinstance(etype, str):
            dropped += 1
            continue
        data = row.get("data")
        if not isinstance(data, dict):
            data = {}
        seq = row.get("seq")
        events.append(LedgerEvent(
            index=index,
            seq=int(seq) if isinstance(seq, int) and not isinstance(seq, bool) else None,
            type=etype,
            timestamp=ts,
            task_id=data.get("task_id") if isinstance(data.get("task_id"), str) else None,
            node_id=data.get("node_id") if isinstance(data.get("node_id"), str) else None,
            message=str(row.get("message") or ""),
            raw=row,
        ))
    if dropped:
        warnings.append(
            f"{dropped} event(s) dropped: not an object, or no readable "
            f"'type'/'timestamp' — intervals spanning them may be wrong"
        )
    return events, warnings


def order_events(events: Sequence[LedgerEvent]) -> tuple[list[LedgerEvent], str]:
    """Ledger order, and the name of the key that produced it.

    ``events.seq`` is the ledger's true order (`events_for` SELECTs ``ORDER BY
    seq``), so it wins whenever the payload carries it — reading straight out
    of the SQLite table, say. **The HTTP wire shape does not carry it today**:
    `Event` in `protocol/v1alpha1.py` has no ``seq`` field, so a captured
    response sorts by timestamp with the input position as a stable
    tiebreaker. Both paths are exercised by the tests; neither trusts the
    order the file happened to arrive in.
    """
    if events and all(e.seq is not None for e in events):
        return sorted(events, key=lambda e: e.seq), "seq"
    return sorted(events, key=lambda e: (e.timestamp, e.index)), "timestamp"


# ---------------------------------------------------------------------------
# Death cycles
# ---------------------------------------------------------------------------


@dataclass
class DeathCycle:
    """One machine death on one task, and every interval it produced.

    A task may die more than once — a second machine can vanish the same way
    — so cycles are numbered per task and each carries its own dying and
    resuming node. Nothing here is aggregated yet.
    """

    task_id: str
    cycle: int
    died_node: str | None
    resumed_node: str | None
    expired_at: datetime
    last_liveness_at: datetime | None
    last_liveness_type: str | None
    reclaimed_at: datetime | None
    progress_at: datetime | None
    progress_type: str | None
    detection_s: Metric = field(default_factory=lambda: Metric.absent("s", "not computed"))
    reclaim_s: Metric = field(default_factory=lambda: Metric.absent("s", "not computed"))
    resume_to_progress_s: Metric = field(default_factory=lambda: Metric.absent("s", "not computed"))
    recomputed_s: Metric = field(default_factory=lambda: Metric.absent("s", "not computed"))
    kill_to_expire_s: Metric = field(default_factory=lambda: Metric.absent("s", "not computed"))
    kill_to_progress_s: Metric = field(default_factory=lambda: Metric.absent("s", "not computed"))

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "cycle": self.cycle,
            "died_node": self.died_node,
            "resumed_node": self.resumed_node,
            "expired_at": self.expired_at.isoformat(),
            "last_liveness_at": self.last_liveness_at.isoformat() if self.last_liveness_at else None,
            "last_liveness_type": self.last_liveness_type,
            "reclaimed_at": self.reclaimed_at.isoformat() if self.reclaimed_at else None,
            "progress_at": self.progress_at.isoformat() if self.progress_at else None,
            "progress_type": self.progress_type,
            "detection_s": self.detection_s.to_dict(),
            "reclaim_s": self.reclaim_s.to_dict(),
            "resume_to_progress_s": self.resume_to_progress_s.to_dict(),
            "recomputed_s": self.recomputed_s.to_dict(),
            "kill_to_expire_s": self.kill_to_expire_s.to_dict(),
            "kill_to_progress_s": self.kill_to_progress_s.to_dict(),
        }


#: Every interval the cycle carries, in report order.
INTERVAL_FIELDS = (
    "detection_s",
    "reclaim_s",
    "resume_to_progress_s",
    "recomputed_s",
    "kill_to_expire_s",
    "kill_to_progress_s",
)


def _delta_s(later: datetime, earlier: datetime) -> float:
    """Seconds between two ledger timestamps, floored at 0.

    Floored because a negative interval is not a measurement, it is a sign
    that two events arrived out of the order their timestamps claim; zero is
    the honest reading of "these are the same instant as far as this ledger
    can tell". The ordering pass makes this rare, but a hand-edited capture
    can still produce it.
    """
    return max(0.0, (later - earlier).total_seconds())


def _find_backwards(events: Sequence[LedgerEvent], start: int, types: Iterable[str],
                    node_id: str | None = None, stop: int = -1) -> LedgerEvent | None:
    wanted = tuple(types)
    for j in range(start, stop, -1):
        e = events[j]
        if e.type not in wanted:
            continue
        if node_id is not None and e.node_id is not None and e.node_id != node_id:
            continue
        return e
    return None


def _find_forwards(events: Sequence[LedgerEvent], start: int, types: Iterable[str]) -> tuple[int, LedgerEvent] | None:
    wanted = tuple(types)
    for j in range(start, len(events)):
        if events[j].type in wanted:
            return j, events[j]
    return None


def _index_of(events: Sequence[LedgerEvent], target: LedgerEvent | None) -> int:
    if target is None:
        return -1
    for j, e in enumerate(events):
        if e is target:
            return j
    return -1


def death_cycles_for_task(task_id: str, events: Sequence[LedgerEvent]) -> list[DeathCycle]:
    """Every death on one task, in ledger order. `events` must already be that
    task's events, ordered. Pure — no clocks, no I/O."""
    cycles: list[DeathCycle] = []
    for i, e in enumerate(events):
        if e.type != LEASE_EXPIRED:
            continue
        cycle_no = len(cycles)

        # Who died. The expiry event names the node itself (`LeaseManager`
        # passes `lease.node_id` on both the sweep and the force-expire path);
        # falling back to the most recent claim covers a ledger that lost it.
        prior_claim = _find_backwards(events, i - 1, (LEASE_CLAIMED,))
        died_node = e.node_id or (prior_claim.node_id if prior_claim else None)
        attempt_start = _find_backwards(events, i - 1, (LEASE_CLAIMED,), node_id=died_node)
        attempt_start_idx = _index_of(events, attempt_start)

        # --- detection: the ledger's MTTD analogue -------------------------
        liveness = _find_backwards(events, i - 1, LIVENESS_TYPES, node_id=died_node)
        if liveness is None:
            detection = Metric.absent(
                "s",
                f"no LEASE_CLAIMED/LEASE_RENEWED precedes this LEASE_EXPIRED for "
                f"task {task_id}"
                + (f" on node {died_node}" if died_node else "")
                + " — the ledger never recorded the dying attempt starting, so "
                  "there is no last-proof-of-life to subtract",
            )
        else:
            detection = Metric(
                value=round(_delta_s(e.timestamp, liveness.timestamp), 3),
                unit="s",
                kind="derived",
                source=(
                    f"derived: LEASE_EXPIRED@{e.timestamp.isoformat()} − "
                    f"{liveness.type}@{liveness.timestamp.isoformat()} "
                    f"(task {task_id}, node {died_node or 'unknown'})"
                ),
            )

        # --- reclaim: the wake ---------------------------------------------
        found_claim = _find_forwards(events, i + 1, (LEASE_CLAIMED,))
        if found_claim is None:
            reclaimed_at = None
            resumed_node = None
            claim_idx = -1
            reclaim = Metric.absent(
                "s",
                f"no LEASE_CLAIMED follows this LEASE_EXPIRED for task {task_id} — "
                "nothing ever picked the task up again (attempt budget exhausted, "
                "job cancelled, or the ledger simply ends here). A wait with no "
                "wake has no wake latency; it is not zero",
            )
        else:
            claim_idx, claim_event = found_claim
            reclaimed_at = claim_event.timestamp
            resumed_node = claim_event.node_id
            reclaim = Metric(
                value=round(_delta_s(reclaimed_at, e.timestamp), 3),
                unit="s",
                kind="derived",
                source=(
                    f"derived: LEASE_CLAIMED@{reclaimed_at.isoformat()} − "
                    f"LEASE_EXPIRED@{e.timestamp.isoformat()} "
                    f"(task {task_id}, resumed on {resumed_node or 'unknown'})"
                ),
            )

        # --- resume → demonstrable progress --------------------------------
        progress_at = None
        progress_type = None
        if claim_idx < 0:
            resume_to_progress = Metric.absent(
                "s", "the task was never reclaimed, so there is no resumed attempt to time"
            )
        else:
            found_progress = _find_forwards(events, claim_idx + 1, PROGRESS_TYPES)
            if found_progress is None:
                resume_to_progress = Metric.absent(
                    "s",
                    f"no {' or '.join(PROGRESS_TYPES)} follows the reclaim of task "
                    f"{task_id}. Note that CHECKPOINT_MANIFEST_COMMITTED is emitted "
                    f"under the composite scope '<job_id>::<task_id>' and therefore "
                    f"never appears in GET /v1alpha1/jobs/<job_id>/events — if the "
                    f"resumed attempt did commit a checkpoint, its event is in the "
                    f"other feed",
                )
            else:
                _, progress_event = found_progress
                progress_at = progress_event.timestamp
                progress_type = progress_event.type
                resume_to_progress = Metric(
                    value=round(_delta_s(progress_at, reclaimed_at), 3),
                    unit="s",
                    kind="derived",
                    source=(
                        f"derived: {progress_type}@{progress_at.isoformat()} − "
                        f"LEASE_CLAIMED@{reclaimed_at.isoformat()} (task {task_id})"
                    ),
                )

        # --- recomputed work ------------------------------------------------
        recomputed = _recomputed_metric(
            task_id, events, i, attempt_start, attempt_start_idx, liveness
        )

        cycles.append(DeathCycle(
            task_id=task_id,
            cycle=cycle_no,
            died_node=died_node,
            resumed_node=resumed_node,
            expired_at=e.timestamp,
            last_liveness_at=liveness.timestamp if liveness else None,
            last_liveness_type=liveness.type if liveness else None,
            reclaimed_at=reclaimed_at,
            progress_at=progress_at,
            progress_type=progress_type,
            detection_s=detection,
            reclaim_s=reclaim,
            resume_to_progress_s=resume_to_progress,
            recomputed_s=recomputed,
        ))
    return cycles


def _recomputed_metric(task_id: str, events: Sequence[LedgerEvent], expiry_idx: int,
                       attempt_start: LedgerEvent | None, attempt_start_idx: int,
                       liveness: LedgerEvent | None) -> Metric:
    """Wall-clock span of work that was in flight and never committed.

    Anchored on the last committed progress marker inside the dying attempt —
    everything after it has to be redone. With nothing committed on that
    attempt, the anchor is the attempt's own ``LEASE_CLAIMED``: all of it is
    lost. The closing end is the last proof of life, not the expiry: the
    machine stopped working when it stopped answering, and billing the lease
    window as recomputed work would double-count `detection_s`.
    """
    if liveness is None:
        return Metric.absent(
            "s", f"no proof-of-life event for the dying attempt of task {task_id} — "
                 "the span of uncommitted work has no closing end"
        )
    stop = attempt_start_idx if attempt_start_idx >= 0 else -1
    anchor = _find_backwards(events, expiry_idx - 1, PROGRESS_TYPES, stop=stop)
    if anchor is not None:
        basis = f"last committed progress on the dying attempt ({anchor.type})"
    elif attempt_start is not None:
        anchor = attempt_start
        basis = "the dying attempt's own LEASE_CLAIMED — nothing was ever committed on it"
    else:
        return Metric.absent(
            "s", f"neither a committed progress marker nor a LEASE_CLAIMED was found "
                 f"for the dying attempt of task {task_id} — there is no anchor from "
                 f"which lost work could be measured"
        )
    return Metric(
        value=round(_delta_s(liveness.timestamp, anchor.timestamp), 3),
        unit="s",
        kind="derived",
        source=(
            f"derived (event gap): {liveness.type}@{liveness.timestamp.isoformat()} − "
            f"{anchor.type}@{anchor.timestamp.isoformat()} — {basis}"
        ),
    )


@dataclass
class Coverage:
    tasks_seen: int
    tasks_with_a_death: int
    tasks_without_a_death: int
    task_ids_without_a_death: list[str]
    death_cycles: int
    events_read: int
    ordering: str

    def to_dict(self) -> dict:
        return {
            "tasks_seen": self.tasks_seen,
            "tasks_with_a_death": self.tasks_with_a_death,
            "tasks_without_a_death": self.tasks_without_a_death,
            "task_ids_without_a_death": self.task_ids_without_a_death,
            "death_cycles": self.death_cycles,
            "events_read": self.events_read,
            "ordering": self.ordering,
            "note": (
                "tasks that never lost a machine are EXCLUDED from every interval "
                "above and counted here — averaging a zero into a recovery latency "
                "for a task that never recovered would understate every number"
            ),
        }


def analyse(events: Sequence[LedgerEvent], kill_at: datetime | None = None
            ) -> tuple[list[DeathCycle], Coverage]:
    """Every death cycle in a ledger, plus what was excluded and why."""
    ordered, ordering = order_events(events)

    by_task: dict[str, list[LedgerEvent]] = {}
    for e in ordered:
        if e.task_id:
            by_task.setdefault(e.task_id, []).append(e)

    cycles: list[DeathCycle] = []
    without_death: list[str] = []
    for task_id in sorted(by_task):
        task_cycles = death_cycles_for_task(task_id, by_task[task_id])
        if task_cycles:
            cycles.extend(task_cycles)
        else:
            without_death.append(task_id)

    if kill_at is not None:
        _apply_kill_timestamp(cycles, kill_at)

    coverage = Coverage(
        tasks_seen=len(by_task),
        tasks_with_a_death=len(by_task) - len(without_death),
        tasks_without_a_death=len(without_death),
        task_ids_without_a_death=without_death,
        death_cycles=len(cycles),
        events_read=len(ordered),
        ordering=ordering,
    )
    return cycles, coverage


def _apply_kill_timestamp(cycles: list[DeathCycle], kill_at: datetime) -> None:
    """The only ``measured`` intervals this tool produces.

    An external kill timestamp names ONE death. It is attached to the earliest
    expiry at or after that instant and to no other — a second death cycle
    minutes later did not happen at the moment somebody ran ``kill -9``, and
    labelling its interval ``measured`` against that clock would be a fiction
    with a real number attached.
    """
    candidates = [c for c in cycles if c.expired_at >= kill_at]
    stamp = kill_at.isoformat()
    if not candidates:
        for c in cycles:
            c.kill_to_expire_s = Metric.absent(
                "s", f"--kill-at {stamp} is after every LEASE_EXPIRED in this ledger — "
                     "no expiry can be attributed to that kill"
            )
            c.kill_to_progress_s = Metric.absent(
                "s", f"--kill-at {stamp} is after every LEASE_EXPIRED in this ledger"
            )
        return

    killed = min(candidates, key=lambda c: c.expired_at)
    for c in cycles:
        if c is not killed:
            reason = (
                f"the external kill timestamp names one death; this is cycle "
                f"{c.cycle} of task {c.task_id}, which is not it"
            )
            c.kill_to_expire_s = Metric.absent("s", reason)
            c.kill_to_progress_s = Metric.absent("s", reason)
            continue
        c.kill_to_expire_s = Metric(
            value=round(_delta_s(c.expired_at, kill_at), 3),
            unit="s",
            kind="measured",
            source=(
                f"measured: LEASE_EXPIRED@{c.expired_at.isoformat()} − "
                f"externally supplied kill instant @{stamp}"
            ),
        )
        if c.progress_at is None:
            c.kill_to_progress_s = Metric.absent(
                "s", "the resumed attempt never reached a committed progress marker in "
                     "this feed, so kill → progressing-again has no closing end"
            )
        else:
            c.kill_to_progress_s = Metric(
                value=round(_delta_s(c.progress_at, kill_at), 3),
                unit="s",
                kind="measured",
                source=(
                    f"measured: {c.progress_type}@{c.progress_at.isoformat()} − "
                    f"externally supplied kill instant @{stamp} — the whole "
                    f"die→another-machine-is-progressing arc, the non-hibernating "
                    f"venue's answer to a hibernating venue's wake latency"
                ),
            )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def nearest_rank(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile, NOT interpolated — the same method
    `hibernation_modes_probe.py` records for its own wake percentiles, so the
    two artifacts' latencies are comparable rather than merely adjacent."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


@dataclass(frozen=True)
class Stat:
    metric: str
    n: int
    n_absent: int
    p50: float | None
    p90: float | None
    minimum: float | None
    maximum: float | None
    unit: str
    kind: str
    reason: str | None = None

    def to_dict(self) -> dict:
        out: dict[str, Any] = {
            "metric": self.metric, "n": self.n, "n_absent": self.n_absent,
            "p50": self.p50, "p90": self.p90, "min": self.minimum, "max": self.maximum,
            "unit": self.unit, "kind": self.kind,
            "percentile_method": "nearest-rank, not interpolated",
        }
        if self.reason:
            out["reason"] = self.reason
        return out


def summarise(cycles: Sequence[DeathCycle], name: str) -> Stat:
    """One interval across a set of cycles. Absent values are COUNTED, never
    imputed: `n_absent` is on the row precisely so a p50 over two of five
    cycles cannot be read as a p50 over five."""
    metrics = [getattr(c, name) for c in cycles]
    values = [m.value for m in metrics if m.value is not None]
    absent = len(metrics) - len(values)
    kinds = {m.kind for m in metrics if m.value is not None}
    kind = kinds.pop() if len(kinds) == 1 else ("mixed" if kinds else "unavailable")
    if not values:
        reasons = [m.reason for m in metrics if m.reason]
        return Stat(
            metric=name, n=0, n_absent=absent, p50=None, p90=None,
            minimum=None, maximum=None, unit="s", kind="unavailable",
            reason=(reasons[0] if reasons else "no death cycle produced this interval"),
        )
    return Stat(
        metric=name, n=len(values), n_absent=absent,
        p50=round(nearest_rank(values, 0.50), 3),
        p90=round(nearest_rank(values, 0.90), 3),
        minimum=round(min(values), 3), maximum=round(max(values), 3),
        unit="s", kind=kind,
    )


def fleet_stats(cycles: Sequence[DeathCycle]) -> dict[str, Stat]:
    return {name: summarise(cycles, name) for name in INTERVAL_FIELDS}


def per_task_stats(cycles: Sequence[DeathCycle]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for c in cycles:
        out.setdefault(c.task_id, {"deaths": 0, "cycles": []})
        out[c.task_id]["deaths"] += 1
        out[c.task_id]["cycles"].append(c.cycle)
    for task_id, row in out.items():
        mine = [c for c in cycles if c.task_id == task_id]
        row["intervals"] = {n: summarise(mine, n).to_dict() for n in INTERVAL_FIELDS}
    return out


def per_node_stats(cycles: Sequence[DeathCycle]) -> dict[str, dict]:
    """Two roles, kept apart. A node that dies contributes ``detection_s``; a
    node that picks the task up contributes ``reclaim_s`` and
    ``resume_to_progress_s``. Merging them would attribute one machine's
    slowness to another machine's death."""
    nodes: set[str] = set()
    for c in cycles:
        if c.died_node:
            nodes.add(c.died_node)
        if c.resumed_node:
            nodes.add(c.resumed_node)
    out: dict[str, dict] = {}
    for node in sorted(nodes):
        as_victim = [c for c in cycles if c.died_node == node]
        as_rescuer = [c for c in cycles if c.resumed_node == node]
        out[node] = {
            "deaths_as_holder": len(as_victim),
            "reclaims_as_rescuer": len(as_rescuer),
            "as_victim": {
                n: summarise(as_victim, n).to_dict()
                for n in ("detection_s", "recomputed_s", "kill_to_expire_s")
            },
            "as_rescuer": {
                n: summarise(as_rescuer, n).to_dict()
                for n in ("reclaim_s", "resume_to_progress_s", "kill_to_progress_s")
            },
        }
    return out


# ---------------------------------------------------------------------------
# The cost layer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VenueRate:
    venue: str
    usd_per_hour: float | None
    hibernates: bool
    hibernated_usd_per_hour: float | None = None
    volunteer: bool = False
    source: str = ""

    @property
    def is_volunteer(self) -> bool:
        return self.volunteer or self.venue in VOLUNTEER_VENUES

    @property
    def kind(self) -> str:
        """``measured`` only when the source says a run produced the number.

        A rate typed into a `--rates` file is ``supplied`` — it may well be a
        real invoice line, but this script did not see the invoice and must
        not promote somebody's JSON to the same standing as
        `hibernation_modes_probe.py`'s own output.
        """
        return "measured" if self.source.startswith("measured:") else "supplied"


def parse_rates(payload: Any) -> tuple[list[VenueRate], list[str]]:
    """A rate table from JSON: either ``{"venues": [...]}``, a bare list, or a
    mapping of ``{venue: {...}}``.

    A venue whose ``usd_per_hour`` is absent or null stays ``None`` — it is
    NOT read as free. `public.rented_capacity.usd_per_hour` is nullable for
    exactly this reason and its own column comment says so: "Null means the
    venue quoted no price". An explicit ``0.0`` is a different statement and
    is honoured as free.
    """
    warnings: list[str] = []
    rows: list[Any]
    if isinstance(payload, dict) and "venues" in payload:
        rows = payload["venues"] if isinstance(payload["venues"], list) else []
    elif isinstance(payload, dict):
        rows = [{"venue": k, **(v if isinstance(v, dict) else {})} for k, v in payload.items()]
    elif isinstance(payload, list):
        rows = payload
    else:
        return [], [f"rate table was a {type(payload).__name__}, expected an object or a list"]

    rates: list[VenueRate] = []
    for row in rows:
        if not isinstance(row, dict):
            warnings.append(f"rate row {row!r} is not an object — skipped")
            continue
        venue = row.get("venue") or row.get("id")
        if not isinstance(venue, str) or not venue:
            warnings.append(f"rate row {row!r} names no venue — skipped")
            continue
        rate = row.get("usd_per_hour")
        # An explicit null stays null WITHOUT a warning — "the venue quoted no
        # price" is a legitimate statement, not a malformed row. A non-number
        # (a string, a bool) IS malformed, and is refused rather than coerced:
        # `float("0")` would turn a typo into a free machine.
        if rate is not None and (not isinstance(rate, (int, float)) or isinstance(rate, bool)):
            warnings.append(
                f"venue {venue}: usd_per_hour {rate!r} is not a number — "
                f"read as unpriced, NOT as zero"
            )
            rate = None
        hib_rate = row.get("hibernated_usd_per_hour")
        if hib_rate is not None and (not isinstance(hib_rate, (int, float)) or isinstance(hib_rate, bool)):
            warnings.append(f"venue {venue}: hibernated_usd_per_hour {hib_rate!r} is not a number")
            hib_rate = None
        hibernates = row.get("hibernates")
        if not isinstance(hibernates, bool):
            hibernates = DEFAULT_VENUE_HIBERNATES.get(venue, False)
        rates.append(VenueRate(
            venue=venue,
            usd_per_hour=float(rate) if rate is not None else None,
            hibernates=hibernates,
            hibernated_usd_per_hour=float(hib_rate) if hib_rate is not None else None,
            volunteer=bool(row.get("volunteer", False)),
            source=str(row.get("source") or ""),
        ))
    return rates, warnings


def default_rates() -> list[VenueRate]:
    """Every venue the fleet knows, with `hibernates` filled in and NO rate.

    Deliberately unpriced. RunPod's rate is a per-rental fact living in
    `public.rented_capacity.usd_per_hour` and ECS's is whatever `DescribePrice`
    answered for the instance type this deployment is pointed at; hard-coding
    either here would produce a plausible number nobody measured. Supply them
    with ``--rates``. `owned` is the exception, and not because a rate was
    found: nobody bills for a machine they already own.
    """
    return [
        VenueRate(
            venue=v, usd_per_hour=(0.0 if v in VOLUNTEER_VENUES else None),
            hibernates=h,
            volunteer=v in VOLUNTEER_VENUES,
            source=(VOLUNTEER_SOURCE if v in VOLUNTEER_VENUES else ""),
        )
        for v, h in DEFAULT_VENUE_HIBERNATES.items()
    ]


def rates_from_hibernation_evidence(evidence: dict, filename: str) -> list[VenueRate]:
    """The FC pair, priced from the hibernation evidence rather than guessed.

    `hibernation_modes_probe.py` writes ``report.cost.per_state.<state>.
    rate_usd_per_hour`` for the sandbox shape it actually measured. Reading it
    here is what makes the two artifacts one answer: this tool times the
    venues that die, that one times the venues that hibernate, and the cost
    table can show both without either inventing the other's number.
    """
    cost = ((evidence or {}).get("report") or {}).get("cost") or {}
    per_state = cost.get("per_state") if isinstance(cost.get("per_state"), dict) else {}
    active = (per_state.get("active") or {}).get("rate_usd_per_hour")
    deep = (per_state.get("deep_hibernation") or {}).get("rate_usd_per_hour")
    if not isinstance(active, (int, float)) or isinstance(active, bool):
        return []
    hib = float(deep) if isinstance(deep, (int, float)) and not isinstance(deep, bool) else None
    return [VenueRate(
        venue="fc-sandbox",
        usd_per_hour=float(active),
        hibernates=True,
        hibernated_usd_per_hour=hib,
        source=f"measured: {filename}#report.cost.per_state.*.rate_usd_per_hour",
    )]


@dataclass
class VenueCost:
    venue: str
    hibernates: bool
    active_rate: Metric
    hibernated_rate: Metric
    wait_s: Metric
    death_s: Metric
    cost_of_wait: Metric
    cost_of_death: Metric

    def to_dict(self) -> dict:
        return {
            "venue": self.venue,
            "hibernates": self.hibernates,
            "active_usd_per_hour": self.active_rate.to_dict(),
            "hibernated_usd_per_hour": self.hibernated_rate.to_dict(),
            "wait_s": self.wait_s.to_dict(),
            "death_s": self.death_s.to_dict(),
            "cost_of_wait_usd": self.cost_of_wait.to_dict(),
            "cost_of_death_usd": self.cost_of_death.to_dict(),
        }


def _sum_p50(stats: dict[str, Stat], names: Sequence[str]) -> tuple[float | None, list[str]]:
    total = 0.0
    used: list[str] = []
    for n in names:
        s = stats.get(n)
        if s is None or s.p50 is None:
            continue
        total += s.p50
        used.append(f"{n}.p50={s.p50}")
    if not used:
        return None, []
    return round(total, 3), used


def venue_costs(stats: dict[str, Stat], rates: Sequence[VenueRate]) -> list[VenueCost]:
    """Per venue: what the wait costs, and what the death costs.

    **cost-of-wait** — the interval in which nobody is computing:
    ``detection_s + reclaim_s``. For a hibernating venue the machine is still
    there, idling at the hibernated rate, so the wait has a price. For a
    non-hibernating venue it is exactly **0.00**, and that zero is a finding
    rather than a missing number: the machine LEAVES the fleet. Releasing the
    machine and keeping the state in the checkpoint store is FlashML's own
    form of hibernation, and the thing it hibernates costs nothing to hold.

    **cost-of-death** — ``detection_s + reclaim_s + resume_to_progress_s +
    recomputed_s`` at the venue's ACTIVE rate: the whole arc from a machine
    dying to another machine being back where the first one was, priced on
    the machine that has to redo it.
    """
    wait_s, wait_terms = _sum_p50(stats, ("detection_s", "reclaim_s"))
    death_s, death_terms = _sum_p50(
        stats, ("detection_s", "reclaim_s", "resume_to_progress_s", "recomputed_s")
    )
    rows: list[VenueCost] = []
    for rate in rates:
        rows.append(_one_venue_cost(rate, wait_s, wait_terms, death_s, death_terms))
    return rows


def _one_venue_cost(rate: VenueRate, wait_s: float | None, wait_terms: list[str],
                    death_s: float | None, death_terms: list[str]) -> VenueCost:
    wait_metric = (
        Metric(wait_s, "s", "derived", f"derived: {' + '.join(wait_terms)}")
        if wait_s is not None else
        Metric.absent("s", "no death cycle produced both a detection and a reclaim interval")
    )
    death_metric = (
        Metric(death_s, "s", "derived", f"derived: {' + '.join(death_terms)}")
        if death_s is not None else
        Metric.absent("s", "no death cycle produced any priceable interval")
    )

    active = (
        Metric(rate.usd_per_hour, "USD/hr", rate.kind,
               rate.source or f"supplied for venue {rate.venue}")
        if rate.usd_per_hour is not None else
        Metric.absent("USD/hr", UNPRICED_REASON.format(venue=rate.venue))
    )
    hibernated = (
        Metric(rate.hibernated_usd_per_hour, "USD/hr", rate.kind,
               rate.source or f"supplied for venue {rate.venue}")
        if rate.hibernated_usd_per_hour is not None else
        Metric.absent(
            "USD/hr",
            "this venue does not hibernate — there is no hibernated rate to have"
            if not rate.hibernates else
            f"venue '{rate.venue}' hibernates but no hibernated_usd_per_hour was "
            f"supplied; see the FC hibernation evidence for the measured pair",
        )
    )

    if rate.is_volunteer:
        cost_of_wait = Metric(0.0, "USD", "derived", VOLUNTEER_SOURCE)
        cost_of_death = Metric(0.0, "USD", "derived", VOLUNTEER_SOURCE)
        return VenueCost(rate.venue, rate.hibernates, active, hibernated,
                         wait_metric, death_metric, cost_of_wait, cost_of_death)

    # --- cost of the wait ---------------------------------------------------
    if not rate.hibernates:
        cost_of_wait = Metric(
            0.0, "USD", "derived",
            "derived: a non-hibernating venue's machine LEAVES the fleet on death — "
            "releasing the machine and keeping the state in the checkpoint store is "
            "FlashML's own hibernation, and holding a committed checkpoint has no "
            "hourly rate. Nothing bills during the wait",
        )
    elif rate.hibernated_usd_per_hour is None or wait_s is None:
        cost_of_wait = Metric.absent(
            "USD",
            f"venue '{rate.venue}' hibernates, so its wait DOES bill — but "
            + ("no hibernated_usd_per_hour was supplied"
               if rate.hibernated_usd_per_hour is None
               else "no wait interval was derivable from the ledger"),
        )
    else:
        # Rounded at 12 dp, not the 8 the renderer PRINTS: a 42-second wait at
        # the hibernated rate is 4.1e-05 USD, and rounding the stored value to
        # 8 would quietly discard the digits that make the figure reproducible.
        # Display precision and stored precision are different decisions.
        cost_of_wait = Metric(
            round(rate.hibernated_usd_per_hour * wait_s / 3600.0, 12), "USD", "derived",
            f"derived: hibernated {rate.hibernated_usd_per_hour} USD/hr x {wait_s} s / 3600",
        )

    # --- cost of the death --------------------------------------------------
    if rate.usd_per_hour is None:
        cost_of_death = Metric.absent(
            "USD", f"unpriced venue: {active.reason}"
        )
    elif death_s is None:
        cost_of_death = Metric.absent(
            "USD", "no death cycle produced a priceable interval to multiply by the rate"
        )
    else:
        cost_of_death = Metric(
            round(rate.usd_per_hour * death_s / 3600.0, 12), "USD", "derived",
            f"derived: active {rate.usd_per_hour} USD/hr x {death_s} s / 3600 "
            f"(detection + reclaim + resume-to-progress + recomputed)",
        )

    return VenueCost(rate.venue, rate.hibernates, active, hibernated,
                     wait_metric, death_metric, cost_of_wait, cost_of_death)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _fmt(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    if unit in ("USD", "USD/hr"):
        return f"{value:.8f}"
    return f"{value:g}"


def _cell(m: Metric) -> str:
    return _fmt(m.value, m.unit) if m.value is not None else "— _(none)_"


def header_text(inputs: dict, hibernation_file: str | None) -> str:
    """The honest header every artifact carries. Not decoration — it is the
    difference between a number a reader can act on and a number they have to
    take on trust."""
    cited = hibernation_file or f"the newest .evidence/{HIBERNATION_EVIDENCE_GLOB}"
    kill = inputs.get("kill_at") or "not supplied"
    return (
        "**What is derived.** `detection_s`, `reclaim_s`, `resume_to_progress_s` "
        "and `recomputed_s` are differences between two timestamps in the "
        "coordinator's own append-only event ledger "
        "(`GET /v1alpha1/jobs/{id}/events`). Both ends of every interval are "
        "stamped by the same clock, so none of them can be skewed by a worker's "
        "wall clock. They are labelled `derived`.\n\n"
        "**What needs an external clock.** The ledger cannot see the instant a "
        "machine actually stopped — only the instant the coordinator stopped "
        "hearing from it. `kill_to_expire_s` and `kill_to_progress_s` therefore "
        "exist only when a kill timestamp is supplied from outside "
        f"(`--kill-at`: {kill}), and only those two are labelled `measured`. "
        "Absent one, they are blank with a reason — never zero.\n\n"
        "**What this file does NOT cover.** Alibaba FC sandboxes and FC GPU "
        "functions hibernate; their wake latency and hibernated rate are "
        f"measured separately in `{cited}` (deep-hibernation wake p50 1109 ms; "
        "hibernated 0.00351553 USD/hr against 0.05654376 USD/hr active — a "
        "93.78% saving). This file covers the venues that CANNOT hibernate — "
        "owned/volunteer machines, RunPod pods, ECS GPU instances — where the "
        "analogue of hibernate→wake is die→another-machine-resumes-from-"
        "checkpoint. Together the two cover every machine type the fleet "
        "controls.\n\n"
        "**A `None` that is not a failure.** `CHECKPOINT_MANIFEST_COMMITTED` is "
        "emitted under the composite scope `\"<job_id>::<task_id>\"` and never "
        "appears in a job's own event feed, so `resume_to_progress_s` normally "
        "resolves against `TASK_COMMIT_ACCEPTED`. Concatenate both feeds into "
        "the input to use manifest events too."
    )


def render_markdown(cycles: Sequence[DeathCycle], coverage: Coverage,
                    stats: dict[str, Stat], venues: Sequence[VenueCost],
                    inputs: dict, warnings: Sequence[str],
                    hibernation_file: str | None, stamp: str) -> str:
    out: list[str] = []
    out.append("# Cross-venue recovery latency + cost (D-9 generalized)\n")
    out.append(f"Generated {stamp}. Source: `{inputs.get('events_source', 'unknown')}`, "
               f"{coverage.events_read} events, ordered by `{coverage.ordering}`.\n")
    out.append(header_text(inputs, hibernation_file) + "\n")

    out.append("## Coverage\n")
    out.append("| Tasks seen | With a death | Without a death (excluded) | Death cycles |")
    out.append("|---|---|---|---|")
    out.append(f"| {coverage.tasks_seen} | {coverage.tasks_with_a_death} | "
               f"{coverage.tasks_without_a_death} | {coverage.death_cycles} |\n")

    out.append("## Per death cycle\n")
    out.append("| Task | Cycle | Died on | Resumed on | detection_s | reclaim_s | "
               "resume_to_progress_s | recomputed_s | kill_to_expire_s | kill_to_progress_s |")
    out.append("|---|---|---|---|---|---|---|---|---|---|")
    if not cycles:
        out.append("| _(no death cycle in this ledger)_ |  |  |  |  |  |  |  |  |  |")
    for c in cycles:
        out.append(
            f"| {c.task_id} | {c.cycle} | {c.died_node or '—'} | {c.resumed_node or '—'} | "
            f"{_cell(c.detection_s)} | {_cell(c.reclaim_s)} | "
            f"{_cell(c.resume_to_progress_s)} | {_cell(c.recomputed_s)} | "
            f"{_cell(c.kill_to_expire_s)} | {_cell(c.kill_to_progress_s)} |"
        )
    out.append("")

    out.append("## Fleet aggregate (nearest-rank, not interpolated)\n")
    out.append("| Metric | n | absent | p50 | p90 | min | max | Unit | Kind |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for name in INTERVAL_FIELDS:
        s = stats[name]
        out.append(
            f"| {s.metric} | {s.n} | {s.n_absent} | {_fmt(s.p50, s.unit)} | "
            f"{_fmt(s.p90, s.unit)} | {_fmt(s.minimum, s.unit)} | "
            f"{_fmt(s.maximum, s.unit)} | {s.unit} | {s.kind} |"
        )
    out.append("")

    out.append("## Per venue\n")
    # One `Kind` column would have to describe two different figures. It used
    # to, and it reported cost-of-death's kind next to a cost-of-wait that was
    # a perfectly good derived 0.00 — a column that lies about the cell beside
    # it. Each priced figure now names its own kind.
    out.append("| Venue | Hibernates | Active USD/hr | Rate kind | Hibernated USD/hr | "
               "Cost of wait (USD) | Wait kind | Cost of death (USD) | Death kind | Rate source |")
    out.append("|---|---|---|---|---|---|---|---|---|---|")
    for v in venues:
        out.append(
            f"| {v.venue} | {'yes' if v.hibernates else 'no'} | "
            f"{_cell(v.active_rate)} | {v.active_rate.kind} | "
            f"{_cell(v.hibernated_rate)} | "
            f"{_cell(v.cost_of_wait)} | {v.cost_of_wait.kind} | "
            f"{_cell(v.cost_of_death)} | {v.cost_of_death.kind} | "
            f"{v.active_rate.source or '—'} |"
        )
    out.append("")
    out.append("A non-hibernating venue's **cost of wait is a real 0.00**, not a "
               "missing number: the machine leaves the fleet and stops billing while "
               "the committed checkpoint waits for whoever claims the task next. A "
               "blank cell is an absent number with a reason, listed below.\n")
    out.append(WHERE_RATES_COME_FROM + "\n")

    absent_rows: list[tuple[str, str, str]] = [
        ("fleet", name, stats[name].reason)
        for name in INTERVAL_FIELDS
        if stats[name].p50 is None and stats[name].reason
    ]
    absent_rows += [
        (v.venue, m_name, m.reason)
        for v in venues
        for m_name, m in (("cost_of_wait_usd", v.cost_of_wait),
                          ("cost_of_death_usd", v.cost_of_death),
                          ("active_usd_per_hour", v.active_rate))
        if m.value is None and m.reason
    ]
    if absent_rows:
        out.append("### Why a cell is blank\n")
        out.append("| Scope | Field | Reason |")
        out.append("|---|---|---|")
        for venue, name, reason in absent_rows:
            out.append(f"| {venue} | {name} | {reason} |")
        out.append("")

    if warnings:
        out.append("### Warnings\n")
        for w in warnings:
            out.append(f"- {w}")
        out.append("")
    return "\n".join(out) + "\n"


def build_report(cycles: Sequence[DeathCycle], coverage: Coverage,
                 stats: dict[str, Stat], venues: Sequence[VenueCost],
                 inputs: dict, warnings: Sequence[str],
                 hibernation_file: str | None, stamp: str) -> dict:
    return {
        "schema": SCHEMA,
        "captured_at": stamp,
        "generated_by": "flashml-cloud/scripts/competition/recovery_latency.py",
        "header": header_text(inputs, hibernation_file),
        "where_rates_come_from": WHERE_RATES_COME_FROM,
        "hibernation_evidence_cited": hibernation_file,
        "inputs": inputs,
        "warnings": list(warnings),
        "coverage": coverage.to_dict(),
        "cycles": [c.to_dict() for c in cycles],
        "per_task": per_task_stats(cycles),
        "per_node": per_node_stats(cycles),
        "fleet": {name: stats[name].to_dict() for name in INTERVAL_FIELDS},
        "venues": [v.to_dict() for v in venues],
    }


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def read_events_json(path: str) -> Any:
    """``-`` reads stdin. Everything else is a file. No network in this path."""
    if path == "-":
        return json.loads(sys.stdin.read())
    return json.loads(Path(path).read_text())


def fetch_events(api_base: str, job_id: str, token: str | None = None,
                 timeout: float = 30.0) -> Any:
    """The ONLY network call in this script: one GET against a FlashML
    coordinator or control-plane API **you operate**.

    No paid provider API is ever contacted from here — not Alibaba, not
    RunPod. Fetch mode exists so the owner can point the tool at dev; the
    tests and the local rehearsal use file mode and never enter this function.
    """
    url = f"{api_base.rstrip('/')}/v1alpha1/jobs/{job_id}/events"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode())


def load_hibernation_evidence(evidence_dir: Path, explicit: str | None
                              ) -> tuple[str | None, dict]:
    """The newest `alibaba-hibernation-modes-*.json`, or the one named.

    Same lexicographic-newest rule `cost_worksheet.py` uses: the
    ``%Y%m%dT%H%M%SZ`` stamp in the filename sorts chronologically.
    """
    if explicit:
        path = Path(explicit)
        return path.name, json.loads(path.read_text())
    candidates = sorted(evidence_dir.glob(HIBERNATION_EVIDENCE_GLOB))
    if not candidates:
        return None, {}
    return candidates[-1].name, json.loads(candidates[-1].read_text())


def parse_kill_at(value: str) -> datetime:
    """ISO-8601, or bare epoch seconds.

    Epoch is accepted on purpose: `e2e/competition/run_local_recovery.sh`
    records ``KILLED_AT=$(date +%s)``, and making that script convert would
    mean `date -u -r` on BSD and `date -u -d @` on GNU — a portability trap
    inside a patch whose whole point is to stay small.
    """
    text = value.strip()
    # Epoch only when the text cannot be a date: "2026" is a float and would
    # otherwise become 1970-01-01T00:33:46Z, a wrong answer with no error.
    if not any(ch in text for ch in "-:TZz"):
        try:
            return datetime.fromtimestamp(float(text), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            pass
    parsed = _parse_timestamp(text)
    if parsed is None:
        raise argparse.ArgumentTypeError(
            f"--kill-at {value!r} is neither ISO-8601 nor epoch seconds"
        )
    return parsed


def merge_rates(supplied: Sequence[VenueRate], extra: Sequence[VenueRate]) -> list[VenueRate]:
    """Defaults first, then anything supplied overrides by venue id. Order is
    the default table's, so a report always lists the fleet's venues in the
    same order whether or not a rate was passed for each."""
    merged: dict[str, VenueRate] = {r.venue: r for r in default_rates()}
    for r in extra:
        merged[r.venue] = r
    for r in supplied:
        merged[r.venue] = r
    ordered = [merged[v] for v in DEFAULT_VENUE_HIBERNATES if v in merged]
    ordered += [merged[v] for v in sorted(merged) if v not in DEFAULT_VENUE_HIBERNATES]
    return ordered


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="recovery_latency.py",
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--events-json", metavar="FILE|-",
                   help="a saved GET /v1alpha1/jobs/{id}/events response ('-' = stdin)")
    p.add_argument("--job-id", help="fetch mode: the job whose ledger to read")
    p.add_argument("--api-base", help="fetch mode: coordinator or control-plane base URL")
    p.add_argument("--token", help="fetch mode: bearer token, when the API needs one")
    p.add_argument("--kill-at", type=parse_kill_at, default=None,
                   help="ISO-8601 or epoch seconds; enables the two 'measured' intervals")
    p.add_argument("--rates", metavar="FILE",
                   help="venue rate table JSON: {venue, usd_per_hour, hibernates, "
                        "hibernated_usd_per_hour?}")
    p.add_argument("--hibernation-evidence", metavar="FILE",
                   help="the FC evidence to cite and price fc-sandbox from "
                        "(default: newest in --out-dir)")
    p.add_argument("--out-dir", default=str(EVIDENCE_DIR),
                   help=f"where the artifacts land (default: {EVIDENCE_DIR})")
    p.add_argument("--quiet", action="store_true", help="print only the two paths written")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.events_json and not (args.job_id and args.api_base):
        print("recovery_latency: give --events-json FILE (file mode), or both "
              "--job-id and --api-base (fetch mode)", file=sys.stderr)
        return 2

    warnings: list[str] = []
    # A one-line refusal, not a traceback. This is invoked at the end of
    # `e2e/competition/run_local_recovery.sh`, after that script has already
    # printed its own verdict; a stack trace there reads as the rehearsal
    # having failed when in fact only the input file was bad.
    try:
        if args.events_json:
            payload = read_events_json(args.events_json)
            events_source = args.events_json
        else:
            payload = fetch_events(args.api_base, args.job_id, args.token)
            events_source = f"{args.api_base.rstrip('/')}/v1alpha1/jobs/{args.job_id}/events"
    except (OSError, ValueError) as exc:
        print(f"recovery_latency: could not read the event ledger: {exc}", file=sys.stderr)
        return 1

    events, parse_warnings = parse_events(payload)
    warnings.extend(parse_warnings)

    cycles, coverage = analyse(events, kill_at=args.kill_at)
    stats = fleet_stats(cycles)

    supplied: list[VenueRate] = []
    if args.rates:
        supplied, rate_warnings = parse_rates(json.loads(Path(args.rates).read_text()))
        warnings.extend(rate_warnings)

    out_dir = Path(args.out_dir)
    try:
        hibernation_file, hibernation = load_hibernation_evidence(
            out_dir, args.hibernation_evidence
        )
    except (OSError, ValueError) as exc:
        hibernation_file, hibernation = None, {}
        warnings.append(f"could not read the hibernation evidence: {exc}")
    extra = rates_from_hibernation_evidence(hibernation, hibernation_file or "") if hibernation else []

    rates = merge_rates(supplied, extra)
    venues = venue_costs(stats, rates)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    inputs = {
        "events_source": events_source,
        "event_count": len(events),
        "kill_at": args.kill_at.isoformat() if args.kill_at else None,
        "rates_source": args.rates or "defaults only (no rate supplied)",
        "hibernation_evidence": hibernation_file,
    }

    report = build_report(cycles, coverage, stats, venues, inputs, warnings,
                          hibernation_file, stamp)
    doc = render_markdown(cycles, coverage, stats, venues, inputs, warnings,
                          hibernation_file, stamp)

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"recovery-latency-{stamp}.json"
    md_path = out_dir / f"recovery-latency-{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n")
    md_path.write_text(doc)

    if not args.quiet:
        print(f"recovery_latency: {coverage.death_cycles} death cycle(s) over "
              f"{coverage.tasks_with_a_death}/{coverage.tasks_seen} task(s); "
              f"{coverage.tasks_without_a_death} task(s) never lost a machine "
              f"(excluded, counted)")
        for name in ("detection_s", "reclaim_s", "resume_to_progress_s", "recomputed_s"):
            s = stats[name]
            print(f"  {name:<22} n={s.n:<3} p50={_fmt(s.p50, s.unit)} s  ({s.kind})")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
