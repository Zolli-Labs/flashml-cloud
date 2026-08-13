#!/usr/bin/env python3
"""C-6.1: extreme elasticity, measured as a bounded concurrency ladder.

The requirement's own words: *bounded concurrent sandbox creation, each
hosting a FlashNode that claims one independent task. Report measured create
rate, p50/p95 latency, failure rate, and the cap we chose and why. All killed
in `finally`.*

--------------------------------------------------------------------------
WHY THIS IS AN HONEST DEMO AND NOT A STRESS TEST BOLTED ON

Elasticity demos are contrived when the workload is one agent conversation —
you invent parallelism to have something to scale. Ours does not need
inventing. Hyperparameter search and evaluation are **N genuinely independent
tasks**: shard k does not read shard j's memory, does not order against it,
and does not need it to finish. That is the workload's natural shape. One
sandbox per shard is the obvious implementation, not a contrivance, which is
why this requirement is cheap for us and expensive for a chat product.

--------------------------------------------------------------------------
WHAT THIS PROBE MEASURES, AND WHAT IT DOES NOT

Measures: how many sandboxes this account can be asked to create at once,
how fast they come back, and what the platform says when we ask for too many.

Does **not** measure: a FlashNode inside each sandbox claiming a task. That
half is `flashnode_in_sandbox_probe.py`, deliberately separate — folding it in
here would put a FlashNode's install time inside a number labelled "create
latency", and the create rate is the figure C-6.1 asks for. Stated as a
negative finding in the evidence (`scope.not_measured`) rather than left for a
reader to discover.

Does **not** reproduce Alibaba's published headline. This is one account, one
region, one ladder, run for a few minutes. The ceiling it reports is the
highest rung THIS LADDER reached cleanly under THIS account's limits. A higher
number may well be available and was not attempted. The documented per-account
concurrency cap (150, quoted from the integration spec's cut-order fallback)
is **quoted, never measured** — this probe's default ladder does not reach it
and the probe refuses to pretend it did.

--------------------------------------------------------------------------
THE LADDER, AND WHY IT STOPS

Rungs ascend: 1, 2, 4, 8 by default. Each rung creates its sandboxes
concurrently, measures them, and kills every one of them in a `finally` before
the next rung starts — the rungs are independent samples, not a cumulative
allocation, so rung 8 is 8 concurrent creates and not 15 live sandboxes.

If a rung's failure rate reaches `--failure-threshold` (default 20%) the
ladder **stops** rather than climbing into a wall. The number reported as the
chosen cap is the highest rung that created every sandbox it attempted **and**
confirmed cleanup of all of them — `degraded` is not `clean`, and a rung with
one failure out of ten is not offered as a cap.

A 429 or a quota refusal is **the interesting result of an elasticity probe,
not noise.** `throttle` and `quota` are their own failure classes and are
never folded into a generic failure count: a probe that reports "3 failures"
where the platform said "you are at your concurrency limit" has thrown away
the only number a judge actually wanted.

--------------------------------------------------------------------------
Usage
-----
    export E2B_API_KEY="<created in the Function Compute console>"
    python elasticity_probe.py --dry-run                 # plan only, no API
    python elasticity_probe.py --region ap-southeast-1 \
        --concurrency 1,2,4,8 --per-level 0

Evidence lands in ../../.evidence/ (gitignored) as redacted JSON, plus a
`.sandboxes` id file written the instant each sandbox exists — a `kill -9` of
this script still leaves a human able to clean up. Every sandbox is killed in
a `finally`; the run ends with a sweep scoped to this run's own ids and its
own metadata tag, never a blanket sweep that would reach into a probe running
beside it.

Exit codes: 0 = the ladder ran, at least one rung was clean, and every
sandbox this run created is confirmed gone; 2 = cleanup could not be
confirmed, the harness errored, or not one rung ran clean; 1 = a
harness/config problem (no `E2B_API_KEY`, an unparseable ladder) — neither is
a verdict about the platform.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

TEMPLATE = "code-interpreter-v1"
DEFAULT_REGION = "ap-southeast-1"

#: Modest on purpose. Doubling rungs find an order of magnitude in four steps,
#: and 15 sandboxes alive for a couple of minutes each is pennies. Widen it
#: with --concurrency when the owner is watching the console.
DEFAULT_LADDER = "1,2,4,8"

#: A rung at or above this failure rate stops the ladder. 20% is the point at
#: which "some creates failed" stops being a tail and starts being a ceiling.
DEFAULT_FAILURE_THRESHOLD = 0.20

#: Ceiling on ONE create. A hung create must become a classified `timeout`
#: failure, not a hung rung.
DEFAULT_CREATE_TIMEOUT_S = 120.0

#: Sandbox TTL. Short: this probe creates and kills, it does not run work.
DEFAULT_SANDBOX_TTL_S = 120

#: Published active rate, doc 3045213. Used only for the dry-run's upper-bound
#: cost line, which is labelled `derived` and is an over-estimate by design —
#: every sandbox here is killed seconds after it is created, not at its TTL.
ACTIVE_HOURLY_USD = 0.08

#: QUOTED, NOT MEASURED. `docs/superpowers/specs/2026-08-11-alibaba-integration-spec.md`
#: §7 names this as the fallback if the probe is cut: "quote the 150-concurrent
#: account cap instead". It is Alibaba's documented per-account ceiling, it is
#: not something this probe has ever observed, and it is carried into the
#: evidence with `kind: "quoted"` so no reader can mistake it for a result.
DOCUMENTED_ACCOUNT_CONCURRENCY_CAP = 150

#: Metadata key stamped on every sandbox this run creates. The final sweep
#: matches on it, which is how a create that timed out client-side — and may
#: therefore have succeeded server-side under an id we never saw — still gets
#: cleaned up. Exact equality only: a prefix match would reach into a probe
#: running beside this one.
RUN_TAG_KEY = "flashml_run"

# -- failure classes --------------------------------------------------------
# Distinct on purpose. Throttle and quota are the RESULT of an elasticity
# probe; collapsing them into "failed" is the single most expensive thing this
# file could do.
CLASS_THROTTLE = "throttle"
CLASS_QUOTA = "quota"
CLASS_AUTH = "auth"
CLASS_TIMEOUT = "timeout"
CLASS_SERVER_ERROR = "server_error"
CLASS_CLIENT_ERROR = "client_error"
CLASS_TRANSPORT = "transport"
CLASS_UNKNOWN = "unknown"

#: The two classes that mean "the platform answered, and its answer was a
#: ceiling". Reported separately in every summary.
CEILING_CLASSES = (CLASS_THROTTLE, CLASS_QUOTA)

#: Classes where a sandbox may exist server-side under an id we never
#: received. Same idea as the gateway's `may_have_applied`: a request that
#: timed out may still have been applied.
MAY_HAVE_CREATED_CLASSES = (CLASS_TIMEOUT, CLASS_TRANSPORT, CLASS_SERVER_ERROR)

THROTTLE_MARKERS = (
    "too many requests", "rate limit", "ratelimit", "throttl", "slow down",
)
QUOTA_MARKERS = (
    "quota", "concurrency limit", "concurrent limit", "limit exceeded",
    "exceeded the maximum", "resource limit", "out of capacity",
    "insufficient capacity", "capacity limit",
)

# -- rung verdicts ----------------------------------------------------------
VERDICT_CLEAN = "clean"                              # every create succeeded, all killed
VERDICT_DEGRADED = "degraded"                        # some failed, below threshold
VERDICT_SATURATED = "saturated"                      # failure rate >= threshold: stop
VERDICT_CLEANUP_UNCONFIRMED = "cleanup_unconfirmed"  # a kill failed: dominates
VERDICT_NOT_RUN = "not_run"

#: Every field a rung row carries, and whether it was measured, derived from
#: measurements, or quoted from a document. `test_elasticity_probe.py` asserts
#: this covers the row exactly, so a new figure cannot ship unlabelled.
FIELD_PROVENANCE: dict[str, str] = {
    "concurrency": "config (the rung requested)",
    "in_flight_ceiling": "config (max creates in flight at this rung)",
    "attempted": "measured (creates actually issued)",
    "created_ok": "measured (creates that returned a sandbox id)",
    "failed": "measured (creates that raised)",
    "failed_by_class": "measured (classified from the platform's own error)",
    "ceiling_failures": "derived (throttle + quota, summed from failed_by_class)",
    "may_have_created": "derived (failures whose class leaves a sandbox possible)",
    "failure_rate": "derived (failed / attempted)",
    "wall_clock_s": "measured (monotonic, first create issued to last settled)",
    "creates_per_sec": "derived (created_ok / wall_clock_s)",
    "latency_ms": "measured per create (successes only), p95 derived nearest-rank",
    "failed_latency_ms": "measured per create (failures only) — kept separate",
    "cleanup": "measured (one kill call per sandbox, in a finally)",
    "cleanup_confirmed": "derived (no kill failure and every handle killed)",
    "verdict": "derived (rung_verdict over failure_rate + cleanup)",
    "sandbox_ids": "measured (ids this rung created)",
    "errors": "measured (redacted platform errors, one per failed create)",
    "harness_error": "measured (a failure in this script, not in the platform)",
}

WORKLOAD_ARGUMENT = (
    "HPO and evaluation are N genuinely independent tasks — shard k does not "
    "read shard j's memory, does not order against it, and does not need it to "
    "finish. One sandbox per shard is the natural implementation of this "
    "workload, not a stress test bolted on to have something to scale."
)

SCOPE_NOTE = {
    "measured": (
        "concurrent sandbox CREATION on one account in one region: per-create "
        "latency, batch wall clock, creates/sec, and the platform's own refusal "
        "classes."
    ),
    "not_measured": (
        "a FlashNode running inside each sandbox and claiming a task. That is "
        "flashnode_in_sandbox_probe.py, kept separate so a FlashNode's install "
        "time never lands inside a figure labelled 'create latency'."
    ),
}

HONESTY_CAVEAT = (
    "A BOUNDED PROBE, NOT A HEADLINE REPRODUCED. This measures one account, one "
    "region, one short ladder. It is not Alibaba's published instances-per-minute "
    "figure reproduced and must never be presented as such. The cap reported here "
    "is the highest rung this ladder reached cleanly; if the ladder ran out before "
    "the platform did, that is said in the rationale and the real ceiling remains "
    "unmeasured. The 150-concurrent per-account cap is quoted from Alibaba's "
    "documentation, not observed here."
)


# ---------------------------------------------------------------------------
# Shared plumbing. Copied VERBATIM from isolation_probe.py rather than
# imported: these scripts are deliberately standalone, run from a throwaway
# venv that has the E2B SDK and nothing of ours.
# ---------------------------------------------------------------------------


def redact(text: str) -> str:
    """Never let a key reach stdout or the evidence file."""
    out = re.sub(r"(?i)(api[_-]?key\"?\s*[:=]\s*\"?)[A-Za-z0-9_\-]{8,}", r"\1<redacted>", text)
    for name, value in os.environ.items():
        if name.startswith("E2B_") and "KEY" in name and value and len(value) >= 8:
            out = out.replace(value, "<redacted>")
        if name.startswith("OSS_") and "SECRET" in name and value and len(value) >= 8:
            out = out.replace(value, "<redacted>")
    return out


def _try(fn: Callable[[], Any]) -> tuple[Any, str]:
    try:
        return fn(), ""
    except Exception as exc:  # noqa: BLE001 - probing on purpose
        return None, redact(f"{type(exc).__name__}: {exc}")


def endpoints(region: str) -> tuple[str, str]:
    return f"https://api.{region}.e2b.fc.aliyuncs.com", f"{region}.e2b.fc.aliyuncs.com"


def api_key() -> str:
    key = os.environ.get("E2B_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Missing E2B_API_KEY.")
    return key


class Ids:
    """Every sandbox id, printed at birth and appended to a file at birth.

    Not a nicety. A `kill -9` between `create` and the `finally` leaves a
    sandbox billing by the second under an id that exists only in this
    process's memory. The file is written before anything else happens to the
    sandbox, so the worst case is a human with a list and a console.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.ids: list[str] = []

    def add(self, sandbox_id: str, note: str = "") -> None:
        if not sandbox_id:
            return
        self.ids.append(sandbox_id)
        print(f"  SANDBOX {sandbox_id}" + (f"  ({note})" if note else ""), flush=True)
        try:
            with self.path.open("a") as fh:
                fh.write(f"{datetime.now(timezone.utc).isoformat()} {sandbox_id} {note}\n")
        except OSError as exc:  # noqa: BLE001 - the print above is the fallback
            print(f"  (could not append to {self.path}: {exc})")

    def report(self) -> None:
        print(f"\n  sandbox ids created by this run ({len(self.ids)}): "
              f"{', '.join(self.ids) if self.ids else 'none'}")
        print(f"  also recorded in: {self.path}")


#: Matches the status at the start of the message, or after an `operation: `
#: prefix. `e2b.api.handle_api_exception` formats every non-401/429 failure as
#: f"{status}: {body}" and raises a bare SandboxException, so the status code
#: is not available as an attribute anywhere — parsing the prefix is the only
#: way to see it. Fails safe: an unparsed status classifies as `unknown`.
_STATUS_RE = re.compile(r"(?:\A|:\s)(\d{3})\s*:")


def status_from_message(text: str) -> int | None:
    match = _STATUS_RE.search(text or "")
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Pure functions. Everything a reader would otherwise have to take on trust
# lives here, testable without a sandbox, a key, or a network.
# ---------------------------------------------------------------------------


def parse_ladder(text: str) -> list[int]:
    """"1,5,10,25" -> [1, 5, 10, 25].

    Must ASCEND and must not repeat. Both are load-bearing rather than fussy:
    "stop ascending at the first saturated rung" is meaningless on a ladder
    that descends, and a repeated rung would make `choose_cap` report a cap
    that two different samples disagreed about.
    """
    rungs: list[int] = []
    for chunk in (text or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            value = int(chunk)
        except ValueError:
            raise ValueError(f"not an integer rung: {chunk!r}") from None
        if value < 1:
            raise ValueError(f"a rung must be >= 1, got {value}")
        rungs.append(value)
    if not rungs:
        raise ValueError("empty ladder — pass something like --concurrency 1,2,4,8")
    if len(set(rungs)) != len(rungs):
        raise ValueError(f"ladder repeats a rung: {rungs}")
    if rungs != sorted(rungs):
        raise ValueError(
            f"ladder must ascend (the stop-ascending rule needs it to): {rungs}"
        )
    return rungs


def plan_rung(concurrency: int, per_level: int) -> tuple[int, int]:
    """(total creates, max in flight) for one rung.

    `per_level <= 0` means one wave: exactly `concurrency` creates, all in
    flight together. A larger `per_level` keeps `concurrency` in flight and
    refills as they land, which measures a sustained rate rather than a single
    burst.

    Asking for FEWER creates than the rung's concurrency would label a
    measurement with a concurrency the run never actually reached, so the
    total is raised to the rung rather than silently under-running it.
    """
    if concurrency < 1:
        raise ValueError(f"concurrency must be >= 1, got {concurrency}")
    total = concurrency if per_level <= 0 else max(per_level, concurrency)
    return total, concurrency


def ladder_warnings(
    ladder: Sequence[int],
    per_level: int,
    documented_cap: int = DOCUMENTED_ACCOUNT_CONCURRENCY_CAP,
) -> list[str]:
    """Things the owner should read before spending voucher. Warnings, not
    errors — the owner may deliberately want to find the account ceiling."""
    out: list[str] = []
    for rung in ladder:
        if rung > documented_cap:
            out.append(
                f"rung {rung} is above the DOCUMENTED per-account concurrency cap "
                f"of {documented_cap}; expect {CLASS_QUOTA}/{CLASS_THROTTLE} "
                f"failures, and note that cap is quoted, not measured"
            )
    total = sum(plan_rung(rung, per_level)[0] for rung in ladder)
    if total > 64:
        out.append(
            f"this ladder creates {total} sandboxes in total — check the FC console "
            f"afterwards and confirm the voucher can carry it"
        )
    return out


def estimate_cost_usd(
    total_sandboxes: int, ttl_s: float, hourly: float = ACTIVE_HOURLY_USD
) -> float:
    """UPPER BOUND, and deliberately a bad estimate in the safe direction.

    Prices every sandbox as if it lived its whole TTL. It will not: each is
    killed seconds after it is created. A cost line that under-promises is the
    only kind worth printing before spending someone's voucher.
    """
    return round(total_sandboxes * (max(ttl_s, 0.0) / 3600.0) * hourly, 4)


def pct(values: Sequence[float], p: float) -> float | None:
    """Nearest-rank percentile. Deliberately NOT interpolated — with n this
    small an interpolated p95 invents a value between two samples and reads as
    more precise than the data is. Same rule as `hibernation_modes_probe.py`."""
    if not values:
        return None
    ordered = sorted(values)
    k = max(1, min(len(ordered), int(-(-p * len(ordered) // 1))))
    return ordered[k - 1]


def latency_stats(values: Sequence[float]) -> dict[str, Any]:
    vals = list(values)
    return {
        "n": len(vals),
        "p50_ms": round(statistics.median(vals), 1) if vals else None,
        "p95_ms": round(pct(vals, 0.95), 1) if vals else None,
        "min_ms": round(min(vals), 1) if vals else None,
        "mean_ms": round(statistics.fmean(vals), 1) if vals else None,
        "max_ms": round(max(vals), 1) if vals else None,
    }


def creates_per_sec(created_ok: int, wall_clock_s: float) -> float | None:
    """Derived. `None` rather than a division by ~zero: a rung that took no
    measurable time did not measure a rate."""
    if wall_clock_s <= 0:
        return None
    return round(created_ok / wall_clock_s, 3)


def failure_rate(attempted: int, failed: int) -> float:
    if attempted <= 0:
        return 0.0
    return failed / attempted


def classify_failure(exc_name: str, message: str) -> str:
    """One create's exception -> one failure class.

    The whole point of this function is that a ceiling never disappears into a
    generic bucket. Precedence, stated once so it can be argued with:

      1. **throttle** — a 429, an SDK `RateLimitException`, or explicit
         rate-limit language. A 429 whose body *also* mentions quota is still
         `throttle`: the status is the platform's own answer about why *now*,
         and it is retryable in a way a standing account cap is not.
      2. **quota** — an account/concurrency ceiling stated in words, carrying
         no 429. This is the class the "150-concurrent account cap" would
         show up as if we ever climbed to it.
      3. auth / timeout / server_error / client_error / transport / unknown —
         ordinary failures, kept apart from each other so a broken key never
         reads as a capacity finding.

    Fails safe: anything unrecognised is `unknown`, never `throttle` and never
    silently dropped.
    """
    text = (message or "").lower()
    name = (exc_name or "").strip()
    status = status_from_message(message or "")

    if (
        name == "RateLimitException"
        or status == 429
        or any(marker in text for marker in THROTTLE_MARKERS)
    ):
        return CLASS_THROTTLE
    if any(marker in text for marker in QUOTA_MARKERS):
        return CLASS_QUOTA
    if name == "AuthenticationException" or status in (401, 403):
        return CLASS_AUTH
    if (
        name in ("TimeoutException", "TimeoutError", "ReadTimeout", "ConnectTimeout")
        or "timed out" in text
        or "timeout" in text
    ):
        return CLASS_TIMEOUT
    if status is not None and status >= 500:
        return CLASS_SERVER_ERROR
    if status is not None and 400 <= status < 500:
        return CLASS_CLIENT_ERROR
    if name in (
        "ConnectionError", "ConnectionResetError", "ConnectError",
        "OSError", "RemoteProtocolError",
    ):
        return CLASS_TRANSPORT
    return CLASS_UNKNOWN


def cleanup_result(attempted: int, kills: Sequence[tuple[str, bool, str]]) -> dict[str, Any]:
    """One rung's `finally`, as data.

    A kill failure is recorded with the sandbox id and the error — never
    counted and discarded — because the id is the only thing a human can act
    on in the console. `confirmed` is False if ANY kill failed or if fewer
    kills were attempted than sandboxes exist, and `confirmed=False` is what
    flips the run to exit 2.
    """
    failures = [{"sandbox_id": sid, "error": err} for sid, ok, err in kills if not ok]
    killed = sum(1 for _, ok, _ in kills if ok)
    return {
        "attempted": attempted,
        "killed": killed,
        "failures": failures,
        "confirmed": not failures and killed == attempted,
    }


def rung_verdict(
    attempted: int,
    failed_by_class: dict[str, int],
    cleanup_confirmed: bool,
    threshold: float,
) -> str:
    """One rung -> one word.

    `cleanup_unconfirmed` DOMINATES. A rung whose creates all succeeded but
    whose kill failed is not a clean rung; it is a sandbox still billing. The
    creation signal is not thrown away by that — it stays visible in
    `created_ok` / `failed_by_class` on the same row — but the verdict, which
    is what `choose_cap` reads, refuses to call it clean.
    """
    if not cleanup_confirmed:
        return VERDICT_CLEANUP_UNCONFIRMED
    if attempted <= 0:
        return VERDICT_NOT_RUN
    failed = sum(failed_by_class.values())
    if failed == 0:
        return VERDICT_CLEAN
    if failure_rate(attempted, failed) >= threshold:
        return VERDICT_SATURATED
    return VERDICT_DEGRADED


def should_stop_ascending(verdict: str) -> bool:
    """Climb no further after a saturated rung or an unconfirmed cleanup.

    `degraded` does NOT stop the ladder — a single failure in ten is a tail,
    not a ceiling, and stopping there would under-report the platform. It does
    not qualify as a cap either; see `choose_cap`.
    """
    return verdict in (VERDICT_SATURATED, VERDICT_CLEANUP_UNCONFIRMED)


def dominant_class(failed_by_class: dict[str, int]) -> str:
    if not failed_by_class:
        return ""
    return max(sorted(failed_by_class), key=lambda k: failed_by_class[k])


def choose_cap(rows: Sequence[dict], ladder: Sequence[int]) -> dict[str, Any]:
    """The C-6.1 evidence sentence: the cap we chose, and why.

    The cap is the highest rung with verdict `clean` — every create succeeded
    AND every sandbox was confirmed killed. `degraded` rungs are excluded on
    purpose: offering a rung that dropped a create as "the concurrency we
    support" is exactly the overclaim this probe exists to avoid.

    Three rationales, and the third is the one that keeps us honest: if
    nothing failed anywhere, the ladder ran out before the platform did, and
    the cap is a floor on the truth rather than the truth.
    """
    clean = [r for r in rows if r.get("verdict") == VERDICT_CLEAN]
    cap = max((int(r["concurrency"]) for r in clean), default=None)
    stopped = [r for r in rows if should_stop_ascending(str(r.get("verdict", "")))]
    ran = [int(r["concurrency"]) for r in rows]

    if cap is None:
        rationale = (
            "NO CAP CHOSEN — not one rung created every sandbox it attempted and "
            "confirmed cleanup. This probe measured no usable concurrency; that is "
            "a negative finding about this run, not a measurement of the platform."
        )
    elif stopped:
        row = stopped[0]
        cls = dominant_class(row.get("failed_by_class") or {})
        rationale = (
            f"Rung {row['concurrency']} came back {row['verdict']} at "
            f"{failure_rate(int(row['attempted']), sum((row.get('failed_by_class') or {}).values())):.0%} "
            f"failures"
            + (f" (dominant class: {cls})" if cls else "")
            + f", so the ladder stopped there. {cap} is the highest rung that created "
            f"every sandbox it attempted and confirmed cleanup of all of them."
        )
    elif ran and cap == max(ran) and cap == max(ladder):
        rationale = (
            f"{cap} is the TOP OF THE LADDER, not a measured ceiling: every rung ran "
            f"clean and the ladder ran out before the platform did. The real ceiling "
            f"is above {cap} and this run did not measure it."
        )
    else:
        rationale = (
            f"{cap} is the highest rung that ran clean. The ladder did not complete "
            f"({sorted(set(ladder) - set(ran))} not attempted), so nothing here rules "
            f"out a higher ceiling."
        )
    return {
        "chosen_cap": cap,
        "cap_rationale": rationale,
        "stopped_early": bool(stopped),
        "stop_reason": (
            f"rung {stopped[0]['concurrency']} verdict={stopped[0]['verdict']}"
            if stopped else ""
        ),
    }


def rung_row(
    *,
    concurrency: int,
    in_flight: int,
    outcomes: Sequence["CreateOutcome"],
    wall_clock_s: float,
    cleanup: dict[str, Any],
    threshold: float,
    harness_error: str = "",
) -> dict[str, Any]:
    """One rung's evidence row. Pure, so the shape is testable offline and
    every field it carries can be checked against FIELD_PROVENANCE."""
    ok = [o for o in outcomes if o.ok]
    bad = [o for o in outcomes if not o.ok]
    by_class = dict(sorted(Counter(o.failure_class for o in bad).items()))
    confirmed = bool(cleanup.get("confirmed"))
    return {
        "concurrency": concurrency,
        "in_flight_ceiling": in_flight,
        "attempted": len(outcomes),
        "created_ok": len(ok),
        "failed": len(bad),
        "failed_by_class": by_class,
        "ceiling_failures": sum(by_class.get(c, 0) for c in CEILING_CLASSES),
        "may_have_created": sum(by_class.get(c, 0) for c in MAY_HAVE_CREATED_CLASSES),
        "failure_rate": round(failure_rate(len(outcomes), len(bad)), 4),
        "wall_clock_s": round(wall_clock_s, 3),
        "creates_per_sec": creates_per_sec(len(ok), wall_clock_s),
        "latency_ms": latency_stats([o.latency_ms for o in ok]),
        "failed_latency_ms": latency_stats([o.latency_ms for o in bad]),
        "cleanup": cleanup,
        "cleanup_confirmed": confirmed,
        "verdict": rung_verdict(len(outcomes), by_class, confirmed, threshold),
        "sandbox_ids": [o.sandbox_id for o in ok if o.sandbox_id],
        "errors": [
            {"index": o.index, "failure_class": o.failure_class, "error": o.error}
            for o in bad
        ],
        "harness_error": harness_error,
    }


def summarize(
    rows: Sequence[dict], ladder: Sequence[int], still_live: Sequence[str]
) -> dict[str, Any]:
    totals: Counter[str] = Counter()
    for row in rows:
        totals.update(row.get("failed_by_class") or {})
    rates = [r["creates_per_sec"] for r in rows if r.get("creates_per_sec") is not None]
    summary = {
        "rungs_planned": list(ladder),
        "rungs_run": [int(r["concurrency"]) for r in rows],
        "total_attempted": sum(int(r["attempted"]) for r in rows),
        "total_created_ok": sum(int(r["created_ok"]) for r in rows),
        "total_failed": sum(int(r["failed"]) for r in rows),
        "failed_by_class": dict(sorted(totals.items())),
        "ceiling_failures": sum(totals.get(c, 0) for c in CEILING_CLASSES),
        "may_have_created": sum(
            totals.get(c, 0) for c in MAY_HAVE_CREATED_CLASSES
        ),
        "best_creates_per_sec": max(rates) if rates else None,
        "cleanup_confirmed_all": all(bool(r.get("cleanup_confirmed")) for r in rows)
        if rows else False,
        "kill_failures": [
            failure
            for r in rows
            for failure in (r.get("cleanup") or {}).get("failures", [])
        ],
        "still_live": list(still_live),
    }
    summary.update(choose_cap(rows, ladder))
    return summary


def overall_exit(summary: dict[str, Any], harness_error: str | None) -> int:
    """0 clean · 2 anomaly · (1 is decided before this, in main).

    A throttle at the top rung is NOT an anomaly — it is the result. What
    earns a 2 is a sandbox we cannot account for, a broken harness, or a
    ladder that never produced a single clean rung to report a cap from.
    """
    if harness_error:
        return 2
    if not summary.get("rungs_run"):
        return 2
    if not summary.get("cleanup_confirmed_all") or summary.get("still_live"):
        return 2
    if summary.get("chosen_cap") is None:
        return 2
    return 0


def sweep_targets(
    items: Iterable[Any], mine_ids: Iterable[str], run_tag: str
) -> list[str]:
    """Ids from an account listing that THIS run is allowed to kill.

    Two ways in, and both are scoped to this run: an id we recorded at create
    time, or a sandbox carrying this run's exact metadata tag. The second
    catches the ugly case — a create that timed out client-side and succeeded
    server-side under an id this process never saw.

    Exact tag equality, never a prefix: a probe running beside this one must
    be unreachable from here.
    """
    mine = set(mine_ids)
    out: list[str] = []
    for item in items:
        sid = str(getattr(item, "sandbox_id", "") or "")
        if not sid:
            continue
        meta = getattr(item, "metadata", None) or {}
        tag = ""
        if isinstance(meta, dict):
            tag = str(meta.get(RUN_TAG_KEY, "") or "")
        if sid in mine or (run_tag and tag == run_tag):
            out.append(sid)
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------------------
# The live half. asyncio + to_thread because the SDK is synchronous httpx —
# the same reason `alibaba_sandbox.E2BSandboxGateway` does it: called from a
# coroutine, a blocking create stops the loop for the whole round trip, which
# would serialise the very thing this probe is trying to run concurrently.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateOutcome:
    index: int
    sandbox_id: str
    ok: bool
    latency_ms: float
    failure_class: str   # "" when ok
    error: str           # redacted, "" when ok


async def _create_one(
    Sandbox,
    conn: dict,
    *,
    index: int,
    template: str,
    ttl_s: int,
    run_tag: str,
    create_timeout_s: float,
    ids: Ids,
) -> tuple[CreateOutcome, Any]:
    """One create. Never raises (except cancellation): a failed create is
    evidence, and evidence that raises out of a gather is evidence lost.

    `asyncio.wait_for` cancels the AWAIT, not the thread — the SDK call keeps
    running in the executor after we stop waiting, and it may still create a
    sandbox. That is why the failure is classed `timeout` (one of
    MAY_HAVE_CREATED_CLASSES) and why the end-of-run sweep matches on the
    metadata tag as well as on ids.

    Latency is measured from after the semaphore is acquired, so it times the
    platform and not this script's own queueing.
    """
    metadata = {
        "flashml_probe": "elasticity",
        RUN_TAG_KEY: run_tag,
        "flashml_index": str(index),
    }

    def _call():
        return Sandbox.create(
            template=template, timeout=ttl_s, metadata=metadata, **conn
        )

    started = time.monotonic()
    try:
        sandbox = await asyncio.wait_for(
            asyncio.to_thread(_call), timeout=create_timeout_s
        )
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # noqa: BLE001 - a failed create is the data
        elapsed_ms = (time.monotonic() - started) * 1000.0
        name = type(exc).__name__
        message = redact(f"{name}: {exc}")
        return (
            CreateOutcome(
                index=index,
                sandbox_id="",
                ok=False,
                latency_ms=round(elapsed_ms, 1),
                failure_class=classify_failure(name, message),
                error=message,
            ),
            None,
        )

    elapsed_ms = (time.monotonic() - started) * 1000.0
    sandbox_id = str(getattr(sandbox, "sandbox_id", "") or "")
    ids.add(sandbox_id, f"elasticity index={index}")
    return (
        CreateOutcome(
            index=index,
            sandbox_id=sandbox_id,
            ok=True,
            latency_ms=round(elapsed_ms, 1),
            failure_class="",
            error="",
        ),
        sandbox,
    )


async def _kill_one(sandbox: Any, sandbox_id: str, timeout_s: float) -> tuple[str, bool, str]:
    try:
        await asyncio.wait_for(asyncio.to_thread(sandbox.kill), timeout=timeout_s)
        return sandbox_id, True, ""
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # noqa: BLE001 - a failed kill is the data
        return sandbox_id, False, redact(f"{type(exc).__name__}: {exc}")


async def _kill_all(
    handles: Sequence[tuple[str, Any]], timeout_s: float
) -> dict[str, Any]:
    if not handles:
        return cleanup_result(0, [])
    kills = await asyncio.gather(
        *(_kill_one(sandbox, sid, timeout_s) for sid, sandbox in handles)
    )
    for sid, ok, err in kills:
        if not ok:
            print(f"    KILL FAILED {sid}: {err}", flush=True)
    return cleanup_result(len(handles), kills)


async def run_rung(
    Sandbox,
    conn: dict,
    *,
    concurrency: int,
    per_level: int,
    template: str,
    ttl_s: int,
    run_tag: str,
    create_timeout_s: float,
    threshold: float,
    ids: Ids,
) -> dict[str, Any]:
    """One rung: N concurrent creates, measured, then every handle killed in a
    `finally` — including on a KeyboardInterrupt out of the gather."""
    total, in_flight = plan_rung(concurrency, per_level)
    sem = asyncio.Semaphore(in_flight)
    outcomes: list[CreateOutcome] = []
    handles: list[tuple[str, Any]] = []
    harness_error = ""

    async def _bounded(index: int) -> None:
        async with sem:
            outcome, sandbox = await _create_one(
                Sandbox, conn, index=index, template=template, ttl_s=ttl_s,
                run_tag=run_tag, create_timeout_s=create_timeout_s, ids=ids,
            )
        # Appended OUTSIDE the semaphore and immediately: the `finally` below
        # must be able to see a partially finished rung.
        outcomes.append(outcome)
        if sandbox is not None:
            handles.append((outcome.sandbox_id, sandbox))

    print(f"\n  rung concurrency={concurrency} "
          f"(creating {total}, {in_flight} in flight)", flush=True)

    started = time.monotonic()
    wall_clock_s = 0.0
    try:
        try:
            await asyncio.gather(*(_bounded(i) for i in range(total)))
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001
            harness_error = redact(f"{type(exc).__name__}: {exc}")
        wall_clock_s = time.monotonic() - started
    finally:
        if wall_clock_s == 0.0:
            wall_clock_s = time.monotonic() - started
        cleanup = await _kill_all(handles, create_timeout_s)

    row = rung_row(
        concurrency=concurrency, in_flight=in_flight, outcomes=outcomes,
        wall_clock_s=wall_clock_s, cleanup=cleanup, threshold=threshold,
        harness_error=harness_error,
    )
    p50 = row["latency_ms"]["p50_ms"]
    p95 = row["latency_ms"]["p95_ms"]
    print(
        f"    {row['created_ok']}/{row['attempted']} created in "
        f"{row['wall_clock_s']}s · {row['creates_per_sec']}/s · "
        f"p50={f'{p50}ms' if p50 is not None else 'n/a'} "
        f"p95={f'{p95}ms' if p95 is not None else 'n/a'} · "
        f"failures={row['failed_by_class'] or '{}'} · "
        f"cleanup={'confirmed' if row['cleanup_confirmed'] else 'UNCONFIRMED'} · "
        f"{row['verdict'].upper()}",
        flush=True,
    )
    return row


async def run_ladder(
    Sandbox,
    conn: dict,
    *,
    ladder: Sequence[int],
    per_level: int,
    template: str,
    ttl_s: int,
    run_tag: str,
    create_timeout_s: float,
    threshold: float,
    ids: Ids,
) -> tuple[list[dict], str]:
    rows: list[dict] = []
    harness_error = ""
    for rung in ladder:
        try:
            row = await run_rung(
                Sandbox, conn, concurrency=rung, per_level=per_level,
                template=template, ttl_s=ttl_s, run_tag=run_tag,
                create_timeout_s=create_timeout_s, threshold=threshold, ids=ids,
            )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001
            harness_error = redact(f"{type(exc).__name__}: {exc}")
            print(f"  HARNESS ERROR at rung {rung}: {harness_error}", flush=True)
            break
        rows.append(row)
        if should_stop_ascending(row["verdict"]):
            print(
                f"  STOP — rung {rung} came back {row['verdict']}; not climbing "
                f"further. This is the finding, not a failure of the run.",
                flush=True,
            )
            break
    return rows, harness_error


# ---------------------------------------------------------------------------


def build_plan(args, ladder: Sequence[int]) -> dict[str, Any]:
    total = sum(plan_rung(rung, args.per_level)[0] for rung in ladder)
    api_url, domain = endpoints(args.region)
    return {
        "region": args.region,
        "api_url": api_url,
        "domain": domain,
        "template": args.template,
        "ladder": list(ladder),
        "per_level": args.per_level,
        "per_rung": {
            str(rung): {
                "total_creates": plan_rung(rung, args.per_level)[0],
                "in_flight": plan_rung(rung, args.per_level)[1],
            }
            for rung in ladder
        },
        "total_sandboxes": total,
        "sandbox_ttl_s": args.sandbox_ttl_s,
        "create_timeout_s": args.timeout_s,
        "failure_threshold": args.failure_threshold,
        "estimated_cost_usd_upper_bound": estimate_cost_usd(total, args.sandbox_ttl_s),
        "documented_account_concurrency_cap": {
            "value": DOCUMENTED_ACCOUNT_CONCURRENCY_CAP,
            "kind": "quoted",
            "source": "docs/superpowers/specs/2026-08-11-alibaba-integration-spec.md §7",
        },
        "warnings": ladder_warnings(ladder, args.per_level),
        "api_key_present": bool(os.environ.get("E2B_API_KEY", "").strip()),
    }


def print_plan(plan: dict[str, Any]) -> None:
    print("=== elasticity probe (C-6.1) · PLAN ===")
    print(f"  region              : {plan['region']}")
    print(f"  api_url             : {plan['api_url']}")
    print(f"  template            : {plan['template']}")
    print(f"  ladder              : {plan['ladder']}")
    print(f"  per-level           : {plan['per_level'] or '(one wave per rung)'}")
    for rung, shape in plan["per_rung"].items():
        print(f"    rung {rung:>4} -> {shape['total_creates']} creates, "
              f"{shape['in_flight']} in flight")
    print(f"  total sandboxes     : {plan['total_sandboxes']}")
    print(f"  sandbox TTL         : {plan['sandbox_ttl_s']}s")
    print(f"  per-create ceiling  : {plan['create_timeout_s']}s")
    print(f"  stop-ascending at   : {plan['failure_threshold']:.0%} failures")
    print(f"  cost (upper bound)  : ${plan['estimated_cost_usd_upper_bound']} "
          f"(derived; every sandbox is killed long before its TTL)")
    cap = plan["documented_account_concurrency_cap"]
    print(f"  account cap         : {cap['value']} concurrent — {cap['kind'].upper()}, "
          f"never measured here ({cap['source']})")
    print(f"  E2B_API_KEY present : {plan['api_key_present']}")
    for warning in plan["warnings"]:
        print(f"  WARNING: {warning}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--concurrency", default=DEFAULT_LADDER,
                        help=f"ascending comma list of rungs (default {DEFAULT_LADDER})")
    parser.add_argument("--per-level", type=int, default=0,
                        help="sandboxes created per rung; 0 (default) means one "
                             "wave of exactly the rung's concurrency")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--template", default=TEMPLATE)
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_CREATE_TIMEOUT_S,
                        help="ceiling on ONE create, in seconds (not the sandbox TTL)")
    parser.add_argument("--sandbox-ttl-s", type=int, default=DEFAULT_SANDBOX_TTL_S,
                        help="sandbox lifetime in SECONDS (this SDK takes seconds)")
    parser.add_argument("--failure-threshold", type=float,
                        default=DEFAULT_FAILURE_THRESHOLD,
                        help="a rung at or above this failure rate stops the ladder")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate the config, print the plan, call no API")
    parser.add_argument("--evidence-dir",
                        default=str(Path(__file__).resolve().parents[2] / ".evidence"))
    args = parser.parse_args()

    try:
        ladder = parse_ladder(args.concurrency)
        if not 0.0 < args.failure_threshold <= 1.0:
            raise ValueError(
                f"--failure-threshold must be in (0, 1], got {args.failure_threshold}")
        if args.timeout_s <= 0 or args.sandbox_ttl_s <= 0:
            raise ValueError("--timeout-s and --sandbox-ttl-s must be positive")
        plan = build_plan(args, ladder)
    except ValueError as exc:  # a config problem, not a verdict
        print(f"CONFIG ERROR: {exc}")
        return 1

    print_plan(plan)

    if args.dry_run:
        print("\nDRY RUN — no API call was made, no sandbox was created.")
        if not plan["api_key_present"]:
            print("  (a live run would exit 1: E2B_API_KEY is not set)")
        return 0

    try:
        key = api_key()
    except RuntimeError as exc:  # missing key — a harness problem, not a verdict
        print(str(exc))
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_tag = f"elasticity-{stamp}"
    out_dir = Path(args.evidence_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ids = Ids(out_dir / f"alibaba-elasticity-{stamp}.sandboxes")

    api_url, domain = endpoints(args.region)
    conn = {"api_key": key, "api_url": api_url, "domain": domain,
            "validate_api_key": False}

    from e2b_code_interpreter import Sandbox

    try:
        import importlib.metadata as md
        sdk_version = md.version("e2b")
    except Exception:  # noqa: BLE001
        sdk_version = "unknown"

    print(f"\n=== {args.region} · elasticity probe (C-6.1) · run {run_tag} ===",
          flush=True)
    rows, harness_error = asyncio.run(run_ladder(
        Sandbox, conn, ladder=ladder, per_level=args.per_level,
        template=args.template, ttl_s=args.sandbox_ttl_s, run_tag=run_tag,
        create_timeout_s=args.timeout_s, threshold=args.failure_threshold, ids=ids,
    ))

    # Scoped sweep: ids this run recorded, plus anything carrying this run's
    # exact metadata tag (a create that timed out client-side may have
    # succeeded server-side under an id we never saw).
    listing, _err = _try(lambda: list(Sandbox.list(**conn).next_items()))
    for sid in sweep_targets(listing or [], ids.ids, run_tag):
        print(f"  sweeping leftover {sid}")
        _try(lambda i=sid: Sandbox.kill(i, **conn))
    remaining, _err2 = _try(lambda: list(Sandbox.list(**conn).next_items()))
    still_live = sweep_targets(remaining or [], ids.ids, run_tag)

    summary = summarize(rows, ladder, still_live)

    evidence = {
        "captured_at": stamp,
        "probe": "elasticity (C-6.1)",
        "run_tag": run_tag,
        "region": args.region,
        "api_url": api_url,
        "domain": domain,
        "template": args.template,
        "sdk_version": sdk_version,
        "config": plan,
        "workload_argument": WORKLOAD_ARGUMENT,
        "scope": SCOPE_NOTE,
        "caveat": HONESTY_CAVEAT,
        "provenance": FIELD_PROVENANCE,
        "rungs": rows,
        "summary": summary,
        "error": harness_error or None,
    }
    out_path = out_dir / f"alibaba-elasticity-{stamp}.json"
    out_path.write_text(redact(json.dumps(evidence, indent=2, default=str)))

    print("\n" + "=" * 78)
    print(f"  {'rung':>6}{'ok/att':>10}{'/sec':>9}{'p50 ms':>10}{'p95 ms':>10}"
          f"{'fail%':>8}  {'verdict':<20}failures")
    for row in rows:
        print(
            f"  {row['concurrency']:>6}"
            f"{str(row['created_ok']) + '/' + str(row['attempted']):>10}"
            f"{str(row['creates_per_sec']):>9}"
            f"{str(row['latency_ms']['p50_ms']):>10}"
            f"{str(row['latency_ms']['p95_ms']):>10}"
            f"{row['failure_rate'] * 100:>7.0f}%  "
            f"{row['verdict']:<20}{row['failed_by_class'] or '-'}"
        )
    print(f"\n  chosen cap : {summary['chosen_cap']}")
    print(f"  rationale  : {summary['cap_rationale']}")
    print(f"  ceilings   : {summary['ceiling_failures']} throttle/quota refusals "
          f"across the run ({summary['failed_by_class'] or 'none'})")
    print(f"  cleanup    : {'confirmed on every rung' if summary['cleanup_confirmed_all'] else 'UNCONFIRMED'}")
    if summary["kill_failures"]:
        for failure in summary["kill_failures"]:
            print(f"    kill failed: {failure['sandbox_id']} — {failure['error']}")
    if summary["may_have_created"]:
        print(f"  NOTE: {summary['may_have_created']} create(s) failed in a way that "
              f"may still have produced a sandbox; the tag sweep above is what "
              f"catches those.")
    print(f"  still live : {len(still_live)} {still_live}")
    print(f"\n  {HONESTY_CAVEAT}")
    print(f"\nEvidence: {out_path}")
    ids.report()

    exit_code = overall_exit(summary, harness_error)
    if exit_code == 0:
        print(f"\nGO — bounded elasticity measured; chosen cap {summary['chosen_cap']} "
              f"concurrent creates, every sandbox accounted for.")
    elif harness_error:
        print(f"\nNO-GO — harness error: {harness_error}")
    elif summary["chosen_cap"] is None:
        print("\nNO-GO — not one rung ran clean. Reported as a negative finding "
              "about this run, never as a cap.")
    else:
        print("\nNO-GO — the ladder ran but cleanup could not be confirmed "
              f"(kill failures={len(summary['kill_failures'])}, "
              f"still live={still_live}).")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
