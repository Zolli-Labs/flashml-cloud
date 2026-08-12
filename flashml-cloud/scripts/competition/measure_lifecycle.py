#!/usr/bin/env python3
"""Measure the FC Agent Sandbox hibernation cycle, repeatedly, for evidence.

The smoke script proved hibernation works ONCE. A demo needs a number you can
put on a slide and a claim you can defend, and one sample supports neither.
This runs the full cycle N times and reports the distribution:

    create -> prepare -> pause -> wait -> connect (wake) -> verify -> kill

What each measured phase means:

  create_ms   cold start of a fresh sandbox from the template. The cost you
              pay if you have NOT pre-warmed. The demo's fallback path.
  pause_ms    how long hibernating blocks the caller. Paid by the controller,
              not by the user, but it bounds how fast you can shed capacity.
  wake_ms     `Sandbox.connect()` on a paused sandbox. THE number — this is
              what a user waits through when a paused worker is needed again,
              and the entire "hibernate between rounds" story is priced on it.

and the one that is not a latency:

  state continuity   the marker file's sha256 must be identical across the
              pause. A fresh sandbox from the same template would pass every
              latency check and fail this one, which is why the marker carries
              a per-iteration nonce.

Process continuity is recorded separately from the file-continuity rate,
because they are different promises: one says the disk came back, the other
says the RUNNING PROGRAM came back. Both are measured here.

Read `process_survived_rate` with the 2026-08-11 correction in mind. The
smoke run that day reported processes as GONE, and it was wrong: the template
ships no procps, so its `ps -p $PID >/dev/null && echo ALIVE || echo GONE`
probe exited 127 on the `ps` itself and took the GONE branch every time.
Liveness here is `[ -d /proc/$PID ]`, and processes do in fact survive.

p95 from 5 samples is the maximum in all but name. It is reported because the
tail is what breaks a live demo, and labelled `n` so nobody quotes it as if it
came from a hundred runs.

Usage
-----
    python measure_lifecycle.py --iterations 5 --wait 20

Evidence lands in ../../.evidence/ (gitignored). Every sandbox is killed in a
`finally` block, and the script lists sandboxes at the end to prove it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import statistics
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

TEMPLATE = "code-interpreter-v1"
MARKER_PATH = "/home/user/flashml_prepared.json"
PIDFILE = "/home/user/flashml_bg.pid"
DEFAULT_REGION = "ap-southeast-1"


# --------------------------------------------------------------------------
# evidence


@dataclass
class Iteration:
    index: int
    sandbox_id: str = ""
    create_ms: float | None = None
    prepare_ms: float | None = None
    pause_ms: float | None = None
    wake_ms: float | None = None
    verify_ms: float | None = None
    state_continuous: bool = False
    process_after_wake: str = "UNKNOWN"
    usable_after_wake: bool = False
    killed: bool = False
    error: str = ""


@dataclass
class Report:
    region: str
    api_url: str
    domain: str
    template: str = TEMPLATE
    sdk_version: str = ""
    iterations: int = 0
    wait_s: int = 0
    results: list[Iteration] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    live_sandboxes_after: list[str] = field(default_factory=list)
    error: str = ""


def redact(text: str) -> str:
    """Never let a key reach stdout or the evidence file. (From the smoke script.)"""
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
    except Exception as exc:  # noqa: BLE001
        return None, redact(f"{type(exc).__name__}: {exc}")


def endpoints(region: str) -> tuple[str, str]:
    return f"https://api.{region}.e2b.fc.aliyuncs.com", f"{region}.e2b.fc.aliyuncs.com"


def api_key() -> str:
    key = os.environ.get("E2B_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Missing E2B_API_KEY.")
    return key


def pct(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile. Deliberately NOT interpolated.

    With n=5 an interpolated p95 invents a value between two samples and reads
    as more precise than the data is. Nearest-rank returns a number that was
    actually measured.
    """
    if not values:
        return None
    ordered = sorted(values)
    k = max(1, min(len(ordered), int(-(-p * len(ordered) // 1))))
    return ordered[k - 1]


# --------------------------------------------------------------------------
# one cycle


def one_iteration(Sandbox, conn: dict, index: int, wait_s: int,
                  sandbox_timeout_s: int) -> Iteration:
    it = Iteration(index=index)
    sandbox = None
    try:
        t0 = time.monotonic()
        sandbox = Sandbox.create(template=TEMPLATE, timeout=sandbox_timeout_s, **conn)
        it.create_ms = (time.monotonic() - t0) * 1000
        it.sandbox_id = getattr(sandbox, "sandbox_id", "")

        # prepare: a nonce marker (proves SAME filesystem, not a fresh clone of
        # the template) and a long-lived process (measures, does not assume,
        # process continuity).
        t0 = time.monotonic()
        nonce = secrets.token_hex(16)
        marker = json.dumps({"nonce": nonce, "iteration": index})
        sandbox.commands.run(
            f"mkdir -p $(dirname {MARKER_PATH}) && cat > {MARKER_PATH} <<'EOF'\n{marker}\nEOF"
        )
        sandbox.commands.run(
            f"nohup sh -c 'while true; do sleep 5; done' >/dev/null 2>&1 & echo $! > {PIDFILE}"
        )
        before = sandbox.commands.run(f"sha256sum {MARKER_PATH} | cut -d' ' -f1")
        sha_before = before.stdout.strip().splitlines()[0] if before.stdout.strip() else ""
        it.prepare_ms = (time.monotonic() - t0) * 1000

        t0 = time.monotonic()
        sandbox.pause()
        it.pause_ms = (time.monotonic() - t0) * 1000

        time.sleep(wait_s)

        # connect() auto-resumes a paused sandbox — this is the wake latency.
        #
        # `timeout` is passed EXPLICITLY. A bare `connect()` sends
        # `default_sandbox_timeout` and silently re-arms the sandbox's TTL to
        # 300 s, which would quietly contaminate any lifetime measurement
        # taken after a wake.
        t0 = time.monotonic()
        sandbox = Sandbox.connect(it.sandbox_id, timeout=sandbox_timeout_s, **conn)
        it.wake_ms = (time.monotonic() - t0) * 1000

        # `[ -d /proc/$PID ]`, NOT `ps -p`. The template ships no procps, so
        # `ps` exits 127 and `ps -p $PID && echo ALIVE || echo GONE` reports
        # every process as GONE — which is how an earlier run mis-measured
        # process continuity as lost.
        t0 = time.monotonic()
        after = sandbox.commands.run(
            f"sha256sum {MARKER_PATH} | cut -d' ' -f1; "
            f"[ -d /proc/$(cat {PIDFILE}) ] && echo ALIVE || echo GONE; "
            f"python3 -c \"print('continued')\""
        )
        it.verify_ms = (time.monotonic() - t0) * 1000
        lines = [l for l in after.stdout.strip().splitlines() if l.strip()]
        sha_after = lines[0] if lines else ""
        it.process_after_wake = lines[1] if len(lines) > 1 else "UNKNOWN"
        it.usable_after_wake = any("continued" in l for l in lines)
        it.state_continuous = bool(sha_before) and sha_before == sha_after

        print(f"  [{index}] create={it.create_ms:.0f}ms pause={it.pause_ms:.0f}ms "
              f"wake={it.wake_ms:.0f}ms state={'OK' if it.state_continuous else 'LOST'} "
              f"proc={it.process_after_wake}")
        return it

    except Exception as exc:  # noqa: BLE001
        it.error = redact(f"{type(exc).__name__}: {exc}")
        print(f"  [{index}] ERROR: {it.error}")
        return it
    finally:
        if sandbox is not None:
            _, err = _try(sandbox.kill)
            it.killed = err == ""
            if not it.killed:
                print(f"  [{index}] KILL FAILED: {err}")


# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default=DEFAULT_REGION,
                    help="deep hibernation is only enabled in ap-southeast-1; "
                         "us-west-1 answers 403 PauseSessionForbidden")
    ap.add_argument("--iterations", type=int, default=5)
    ap.add_argument("--wait", type=int, default=20, help="seconds spent hibernated")
    ap.add_argument("--sandbox-timeout", type=int, default=600,
                    help="sandbox TTL in SECONDS (this SDK takes seconds, not ms)")
    ap.add_argument("--evidence-dir",
                    default=str(Path(__file__).resolve().parents[2] / ".evidence"))
    args = ap.parse_args()

    from e2b_code_interpreter import Sandbox

    api_url, domain = endpoints(args.region)
    rep = Report(region=args.region, api_url=api_url, domain=domain,
                 iterations=args.iterations, wait_s=args.wait)
    try:
        import importlib.metadata as md
        rep.sdk_version = md.version("e2b")
    except Exception:  # noqa: BLE001
        rep.sdk_version = "unknown"

    conn = {"api_key": api_key(), "api_url": api_url, "domain": domain}

    print(f"=== {args.region}: {args.iterations} iterations, {args.wait}s hibernated ===")
    try:
        for i in range(1, args.iterations + 1):
            rep.results.append(
                one_iteration(Sandbox, conn, i, args.wait, args.sandbox_timeout)
            )
    finally:
        # Belt and braces on top of each iteration's own finally — but scoped
        # to the ids THIS run created. A blanket "kill everything listed"
        # sweep would reach across into a probe running beside it, and the
        # cheapest way to lose an hour is to have one cleanup routine murder
        # another experiment's sandbox mid-measurement.
        mine = {r.sandbox_id for r in rep.results if r.sandbox_id}
        listed, err = _try(lambda: list(Sandbox.list(**conn).next_items()))
        for s in (listed or []):
            sid = getattr(s, "sandbox_id", "")
            if sid in mine:
                print(f"  sweeping leftover {sid}")
                _try(lambda i=sid: Sandbox.connect(i, **conn).kill())
        remaining, _ = _try(lambda: list(Sandbox.list(**conn).next_items()))
        rep.live_sandboxes_after = [
            getattr(s, "sandbox_id", "") for s in (remaining or [])
            if getattr(s, "sandbox_id", "") in mine
        ]
        if err:
            rep.error = err

    ok = [r for r in rep.results if not r.error]
    creates = [r.create_ms for r in ok if r.create_ms is not None]
    pauses = [r.pause_ms for r in ok if r.pause_ms is not None]
    wakes = [r.wake_ms for r in ok if r.wake_ms is not None]

    def stats(name: str, values: list[float]) -> dict[str, Any]:
        return {
            "n": len(values),
            "p50_ms": round(statistics.median(values), 1) if values else None,
            "p95_ms": round(pct(values, 0.95), 1) if values else None,
            "min_ms": round(min(values), 1) if values else None,
            "max_ms": round(max(values), 1) if values else None,
            "mean_ms": round(statistics.fmean(values), 1) if values else None,
        }

    rep.summary = {
        "create": stats("create", creates),
        "pause": stats("pause", pauses),
        "wake": stats("wake", wakes),
        "state_continuity_rate": (
            f"{sum(r.state_continuous for r in ok)}/{len(ok)}" if ok else "0/0"
        ),
        "usable_after_wake_rate": (
            f"{sum(r.usable_after_wake for r in ok)}/{len(ok)}" if ok else "0/0"
        ),
        "process_survived_rate": (
            f"{sum(r.process_after_wake == 'ALIVE' for r in ok)}/{len(ok)}" if ok else "0/0"
        ),
        "iterations_failed": len(rep.results) - len(ok),
        "all_killed": all(r.killed for r in rep.results),
        "percentile_method": "nearest-rank, not interpolated",
    }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.evidence_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"alibaba-lifecycle-{stamp}.json"
    out_path.write_text(redact(json.dumps(
        {"captured_at": stamp, "report": asdict(rep)}, indent=2, default=str)))

    print("\n" + "=" * 64)
    print(f"{'phase':<10}{'n':>4}{'p50 ms':>12}{'p95 ms':>12}{'min':>10}{'max':>10}")
    for phase in ("create", "pause", "wake"):
        s = rep.summary[phase]
        print(f"{phase:<10}{s['n']:>4}{str(s['p50_ms']):>12}{str(s['p95_ms']):>12}"
              f"{str(s['min_ms']):>10}{str(s['max_ms']):>10}")
    print(f"\nstate continuity : {rep.summary['state_continuity_rate']}")
    print(f"usable after wake: {rep.summary['usable_after_wake_rate']}")
    print(f"process survived : {rep.summary['process_survived_rate']}")
    print(f"all killed       : {rep.summary['all_killed']}")
    print(f"live sandboxes   : {len(rep.live_sandboxes_after)} {rep.live_sandboxes_after}")
    print(f"\nEvidence: {out_path}")

    clean = (rep.summary["iterations_failed"] == 0
             and rep.summary["all_killed"]
             and not rep.live_sandboxes_after)
    return 0 if clean else 2


if __name__ == "__main__":
    sys.exit(main())
