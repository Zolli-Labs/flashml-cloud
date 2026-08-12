#!/usr/bin/env python3
"""Does a paused FC Agent Sandbox survive a LONG hibernation, and does its
TTL clock really stop for that long — not just for 150 seconds?

Two clocks may exist and they disagree:

  MEASURED   (open_questions_probe.py, n=1, 150s pause): the sandbox TTL
             *stops* while paused. A 90s-TTL sandbox reconnected 64s past its
             own expiry was intact, and `end_at` read while paused (via
             `Sandbox.list`, which touches nothing) was byte-identical to
             `end_at` at create.

  DOCUMENTED (Alibaba doc 3028695): the FC *Session* TTL keeps accruing from
             creation — "仍从原始创建时间累计".

Those may describe different objects (Sandbox vs Session), so both can be
true. This script does not resolve that; it extends the MEASURED side from
150 seconds to 45+ minutes, because a demo claim of "a prepared environment
survives a long wait" cannot be supported by a 150s sample.

One sandbox, one pass:

    create(timeout=3600) -> write nonce marker + start bg process
    -> record end_at (get_info AND Sandbox.list, before touching anything)
    -> pause() -> sleep 45 min in the HARNESS, polling Sandbox.list() every
       ~5 min for end_at/state (this is the core evidence: stable end_at
       means the clock is stopped; a slide or a vanished sandbox means it
       is not) -> connect(timeout=3600) EXPLICITLY -> verify marker sha256,
       /proc/$PID, a continuation command, end_at after wake -> kill()

Known gotchas already found and NOT to be rediscovered (see
measure_lifecycle.py / open_questions_probe.py / flashnode_in_sandbox_probe.py
for the full history):

  - `create`/`connect` `timeout` is INTEGER SECONDS, not ms.
  - A BARE `connect()` re-arms the TTL to exactly 300s. Every connect() here
    passes `timeout` explicitly.
  - The SDK validates the API key client-side against an `e2b_`+hex pattern;
    `validate_api_key=False` is passed defensively.
  - The template has no `ps`. Liveness is `[ -d /proc/$PID ]`.
  - `commands.run("... nohup x &")` hangs forever if the redirect does not
    cover the whole backgrounded group (the parent shell inherits the pipe).
    Here: `nohup ... >/dev/null 2>&1 & echo $! > pidfile` — the redirect is
    on the backgrounded command itself, and the `echo` after `&` runs in the
    foreground, so `commands.run` returns.
  - `commands.run` raises `CommandExitException` on non-zero exit, and that
    exception IS the result object.

Usage
-----
    python long_hibernation_probe.py

Runs for roughly 47-48 minutes (mostly `time.sleep` in the harness). Evidence
lands in ../../.evidence/ (gitignored). The sandbox is killed in a `finally`,
and the script lists sandboxes at the end to prove it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

TEMPLATE = "code-interpreter-v1"
MARKER_PATH = "/home/user/flashml_long_hib_marker.json"
PIDFILE = "/home/user/flashml_long_hib.pid"
DEFAULT_REGION = "ap-southeast-1"
DEFAULT_SANDBOX_TIMEOUT_S = 3600
DEFAULT_WAIT_S = 45 * 60  # 2700s
DEFAULT_POLL_INTERVAL_S = 5 * 60  # 300s


# --------------------------------------------------------------------------
# shared plumbing (copied from measure_lifecycle.py / open_questions_probe.py)


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
    except Exception as exc:  # noqa: BLE001
        return None, redact(f"{type(exc).__name__}: {exc}")


def endpoints(region: str) -> tuple[str, str]:
    return f"https://api.{region}.e2b.fc.aliyuncs.com", f"{region}.e2b.fc.aliyuncs.com"


def api_key() -> str:
    key = os.environ.get("E2B_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Missing E2B_API_KEY.")
    return key


def iso(dt: Any) -> str:
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


def listed(Sandbox, conn: dict, sid: str) -> dict[str, Any] | None:
    """Find a sandbox in the account listing WITHOUT connecting to it.

    Load-bearing: `connect()` mutates the very field being measured (`end_at`
    re-arms to `timeout` on a successful connect), so every read of a paused
    sandbox's lifetime fields during the 45-minute wait has to come from a
    listing, never from `get_info()` on a freshly-connected handle.
    """
    from e2b.sandbox.sandbox_api import SandboxQuery, SandboxState

    for state in ([SandboxState.PAUSED], [SandboxState.RUNNING], None):
        query = SandboxQuery(state=state) if state else None
        page, _err = _try(lambda q=query: list(Sandbox.list(query=q, **conn).next_items()))
        for s in (page or []):
            if getattr(s, "sandbox_id", "") == sid:
                return {
                    "state": str(getattr(s, "state", "")),
                    "started_at": iso(getattr(s, "started_at", "")),
                    "end_at": iso(getattr(s, "end_at", "")),
                }
    return None


# --------------------------------------------------------------------------
# evidence


@dataclass
class Poll:
    offset_s: int
    wall_clock_utc: str
    found: bool
    state: str = ""
    started_at: str = ""
    end_at: str = ""


@dataclass
class Report:
    region: str
    template: str = TEMPLATE
    sdk_version: str = ""
    sandbox_id: str = ""
    sandbox_timeout_s: int = DEFAULT_SANDBOX_TIMEOUT_S

    create_ms: float | None = None
    prepare_ms: float | None = None
    marker_sha256_before: str = ""
    pid: str = ""

    end_at_at_create_get_info: str = ""
    started_at_at_create_get_info: str = ""
    end_at_before_pause_list: str = ""
    state_before_pause_list: str = ""

    pause_ms: float | None = None
    pause_return_value: str = ""
    state_immediately_after_pause_list: str = ""
    end_at_immediately_after_pause_list: str = ""

    planned_wait_s: int = DEFAULT_WAIT_S
    poll_interval_s: int = DEFAULT_POLL_INTERVAL_S
    polls: list[Poll] = field(default_factory=list)
    actual_wait_s: float | None = None
    end_at_distinct_values: list[str] = field(default_factory=list)
    end_at_stable: bool = False
    sandbox_vanished_during_pause: bool = False
    vanished_at_offset_s: int | None = None

    wake_timeout_s: int = DEFAULT_SANDBOX_TIMEOUT_S
    wake_ms: float | None = None
    marker_sha256_after: str = ""
    marker_intact: bool = False
    pid_alive_after_wake: bool = False
    continuation_ok: bool = False
    end_at_after_wake_get_info: str = ""
    started_at_after_wake_get_info: str = ""

    survived_45_min: bool = False
    killed: bool = False
    live_sandboxes_after: list[str] = field(default_factory=list)
    verdict: str = ""
    error: str = ""


# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default=DEFAULT_REGION,
                    help="deep hibernation is only enabled in ap-southeast-1")
    ap.add_argument("--sandbox-timeout", type=int, default=DEFAULT_SANDBOX_TIMEOUT_S,
                    help="sandbox TTL in SECONDS at create AND at the explicit "
                         "reconnect (this SDK takes seconds, not ms)")
    ap.add_argument("--wait", type=int, default=DEFAULT_WAIT_S,
                    help="seconds spent paused (default 2700 = 45 min)")
    ap.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL_S,
                    help="seconds between Sandbox.list() polls while paused")
    ap.add_argument("--evidence-dir",
                    default=str(Path(__file__).resolve().parents[2] / ".evidence"))
    args = ap.parse_args()

    from e2b_code_interpreter import Sandbox

    api_url, domain = endpoints(args.region)
    rep = Report(region=args.region, sandbox_timeout_s=args.sandbox_timeout,
                 planned_wait_s=args.wait, poll_interval_s=args.poll_interval,
                 wake_timeout_s=args.sandbox_timeout)
    try:
        import importlib.metadata as md
        rep.sdk_version = md.version("e2b")
    except Exception:  # noqa: BLE001
        rep.sdk_version = "unknown"

    conn = {"api_key": api_key(), "api_url": api_url, "domain": domain,
            "validate_api_key": False}

    print(f"=== {args.region}: one sandbox, timeout={args.sandbox_timeout}s, "
          f"pause wait={args.wait}s (poll every {args.poll_interval}s) ===")

    sandbox = None
    try:
        # ---- create -----------------------------------------------------
        t0 = time.monotonic()
        sandbox = Sandbox.create(template=TEMPLATE, timeout=args.sandbox_timeout, **conn)
        rep.create_ms = (time.monotonic() - t0) * 1000
        rep.sandbox_id = sandbox.sandbox_id
        print(f"created {rep.sandbox_id} in {rep.create_ms:.0f}ms")

        # ---- 1. nonce marker + long-lived background process ------------
        t0 = time.monotonic()
        nonce = secrets.token_hex(16)
        marker = json.dumps({
            "nonce": nonce,
            "probe": "long_hibernation",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        sandbox.commands.run(
            f"mkdir -p $(dirname {MARKER_PATH}) && cat > {MARKER_PATH} <<'EOF'\n{marker}\nEOF"
        )
        sandbox.commands.run(
            f"nohup sh -c 'while true; do sleep 5; done' >/dev/null 2>&1 & echo $! > {PIDFILE}"
        )
        before = sandbox.commands.run(f"sha256sum {MARKER_PATH} | cut -d' ' -f1; cat {PIDFILE}")
        blines = [l for l in before.stdout.strip().splitlines() if l.strip()]
        rep.marker_sha256_before = blines[0] if blines else ""
        rep.pid = blines[1].strip() if len(blines) > 1 else ""
        rep.prepare_ms = (time.monotonic() - t0) * 1000
        print(f"marker sha256={rep.marker_sha256_before} pid={rep.pid} "
              f"(prepared in {rep.prepare_ms:.0f}ms)")

        # ---- 2. lifetime fields BEFORE pause -----------------------------
        info = sandbox.get_info()
        rep.end_at_at_create_get_info = iso(info.end_at)
        rep.started_at_at_create_get_info = iso(info.started_at)
        seen = listed(Sandbox, conn, rep.sandbox_id)
        rep.end_at_before_pause_list = (seen or {}).get("end_at", "<not listed>")
        rep.state_before_pause_list = (seen or {}).get("state", "<not listed>")
        print(f"before pause: get_info.end_at={rep.end_at_at_create_get_info} "
              f"list.end_at={rep.end_at_before_pause_list} "
              f"list.state={rep.state_before_pause_list}")

        # ---- 3. pause -----------------------------------------------------
        t0 = time.monotonic()
        pause_ret = sandbox.pause(keep_memory=True)
        rep.pause_ms = (time.monotonic() - t0) * 1000
        rep.pause_return_value = str(pause_ret)
        seen0 = listed(Sandbox, conn, rep.sandbox_id)
        rep.state_immediately_after_pause_list = (seen0 or {}).get("state", "<not listed>")
        rep.end_at_immediately_after_pause_list = (seen0 or {}).get("end_at", "<not listed>")
        print(f"paused in {rep.pause_ms:.0f}ms; observed state={rep.state_immediately_after_pause_list} "
              f"end_at={rep.end_at_immediately_after_pause_list}")

        # ---- 4. wait, polling every ~poll_interval while paused ----------
        n_polls = max(1, args.wait // args.poll_interval)
        wait_start = time.monotonic()

        def do_poll(offset_s: int) -> Poll:
            seen_i = listed(Sandbox, conn, rep.sandbox_id)
            p = Poll(offset_s=offset_s,
                     wall_clock_utc=datetime.now(timezone.utc).isoformat(),
                     found=seen_i is not None)
            if seen_i:
                p.state = seen_i.get("state", "")
                p.started_at = seen_i.get("started_at", "")
                p.end_at = seen_i.get("end_at", "")
            elif rep.vanished_at_offset_s is None:
                rep.vanished_at_offset_s = offset_s
                rep.sandbox_vanished_during_pause = True
            rep.polls.append(p)
            print(f"  [t+{offset_s:>5d}s] found={p.found} state={p.state} end_at={p.end_at}")
            return p

        do_poll(0)
        for i in range(1, n_polls + 1):
            time.sleep(args.poll_interval)
            offset = int(time.monotonic() - wait_start)
            do_poll(offset)

        rep.actual_wait_s = round(time.monotonic() - wait_start, 1)
        end_ats = [p.end_at for p in rep.polls if p.found and p.end_at]
        rep.end_at_distinct_values = sorted(set(end_ats))
        rep.end_at_stable = (
            not rep.sandbox_vanished_during_pause
            and len(rep.end_at_distinct_values) <= 1
            and len(end_ats) == len(rep.polls)
        )
        print(f"waited {rep.actual_wait_s:.0f}s total; end_at values seen: "
              f"{rep.end_at_distinct_values}; stable={rep.end_at_stable}")

        # ---- 5. explicit reconnect (wake) ---------------------------------
        t0 = time.monotonic()
        sandbox = Sandbox.connect(rep.sandbox_id, timeout=args.sandbox_timeout, **conn)
        rep.wake_ms = (time.monotonic() - t0) * 1000
        print(f"woke in {rep.wake_ms:.0f}ms")

        # ---- 6. verify ------------------------------------------------------
        after = sandbox.commands.run(
            f"sha256sum {MARKER_PATH} | cut -d' ' -f1; "
            f"[ -d /proc/$(cat {PIDFILE}) ] && echo ALIVE || echo GONE; "
            f"python3 -c \"print('continued')\""
        )
        alines = [l for l in after.stdout.strip().splitlines() if l.strip()]
        rep.marker_sha256_after = alines[0] if alines else ""
        rep.marker_intact = (bool(rep.marker_sha256_before)
                              and rep.marker_sha256_before == rep.marker_sha256_after)
        rep.pid_alive_after_wake = len(alines) > 1 and alines[1] == "ALIVE"
        rep.continuation_ok = any("continued" in l for l in alines)

        info2 = sandbox.get_info()
        rep.end_at_after_wake_get_info = iso(info2.end_at)
        rep.started_at_after_wake_get_info = iso(info2.started_at)
        print(f"after wake: marker_intact={rep.marker_intact} "
              f"pid_alive={rep.pid_alive_after_wake} continuation_ok={rep.continuation_ok} "
              f"end_at={rep.end_at_after_wake_get_info}")

        rep.survived_45_min = (not rep.sandbox_vanished_during_pause) and rep.marker_intact

        if rep.survived_45_min and rep.end_at_stable:
            rep.verdict = (
                f"SURVIVED {rep.actual_wait_s:.0f}s ({rep.actual_wait_s/60:.1f} min) paused, "
                f"with end_at UNCHANGED across all {len(rep.polls)} polls "
                f"({rep.end_at_distinct_values}). The TTL clock is stopped, "
                "confirmed well past the earlier 150s n=1 result."
            )
        elif rep.survived_45_min and not rep.end_at_stable:
            rep.verdict = (
                f"SURVIVED {rep.actual_wait_s:.0f}s paused, but end_at was NOT stable across "
                f"polls — observed values: {rep.end_at_distinct_values}. The 'clock fully "
                "stops' claim needs revision; the sandbox stayed alive by some other "
                "mechanism than a frozen TTL."
            )
        else:
            rep.verdict = (
                f"DID NOT SURVIVE: sandbox vanished from Sandbox.list() at "
                f"~t+{rep.vanished_at_offset_s}s into a planned {args.wait}s pause "
                f"(marker_intact={rep.marker_intact}). The 150s result does NOT "
                "generalize to a 45-minute pause."
            )
        print("\n" + rep.verdict)

    except Exception as exc:  # noqa: BLE001
        rep.error = redact(f"{type(exc).__name__}: {exc}")
        print(f"ERROR: {rep.error}")
    finally:
        if sandbox is not None:
            _, err = _try(sandbox.kill)
            rep.killed = err == ""
            if not rep.killed:
                print(f"KILL FAILED: {err}")
        remaining, _lerr = _try(lambda: list(Sandbox.list(**conn).next_items()))
        rep.live_sandboxes_after = [
            getattr(s, "sandbox_id", "") for s in (remaining or [])
            if getattr(s, "sandbox_id", "") == rep.sandbox_id
        ]
        print(f"live sandboxes after: {rep.live_sandboxes_after}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.evidence_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"alibaba-long-hibernation-{stamp}.json"
    out_path.write_text(redact(json.dumps(
        {"captured_at": stamp, "report": asdict(rep)}, indent=2, default=str)))
    print(f"\nEvidence: {out_path}")

    clean = (not rep.error) and rep.killed and not rep.live_sandboxes_after
    return 0 if clean else 2


if __name__ == "__main__":
    sys.exit(main())
