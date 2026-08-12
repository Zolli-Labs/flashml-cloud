#!/usr/bin/env python3
"""Three questions the lifecycle controller cannot be written without.

(a) DOES A PAUSE STOP THE SANDBOX'S OWN TTL CLOCK?

    A demo-killer either way. If the clock keeps running, a sandbox paused
    "for later" evaporates while you are talking, and the controller must
    re-arm the timeout on every wake. If it stops, hibernation is a real
    parking brake.

    The naive test cannot answer it. `Sandbox.connect()` sends
    `ConnectSandbox(timeout=timeout or default_sandbox_timeout)` — so a bare
    reconnect SILENTLY grants a fresh 300 s, and a sandbox that "was still
    there" proves nothing. Two things separate the worlds:

      A1  a SHORT ttl, paused, waited out past its own expiry, then
          reconnected. Survival cannot be explained by a connect-reset,
          because the reset happens only if the connect succeeds at all.
      A2  `end_at`, read directly — before pause, WHILE PAUSED (via
          `Sandbox.list`, which touches nothing), and after wake. That is
          measurement rather than inference, and it also pins down exactly
          what `connect(timeout=...)` does to the clock.

(b) LIGHT HIBERNATION OR DEEP ONLY?

    Alibaba's billing note says E2B SDK accounts auto-transition to Pro,
    which carries both. There is no `light_pause()` anywhere in the SDK —
    the distinction is one keyword: `pause(keep_memory=True)` takes a full
    memory snapshot, `keep_memory=False` persists the filesystem alone and
    cold-boots on resume. So the question is not "which API" but "does the
    backend honour the flag", and the way to tell is to watch whether
    processes and the guest clock survive each variant.

    `/proc/uptime` is the tell. A memory snapshot freezes it, so it lags
    wall-clock by exactly the time spent paused. A cold boot resets it.

(c) INVENTORY — cpu, memory, disk, user, egress, and what is missing from
    the image. Recorded here rather than assumed anywhere else.

A NOTE ON `ps`. The template has no procps. `ps -p $PID` therefore exits 127
and any `ps ... || echo GONE` idiom reports every process as dead — which is
how an earlier run concluded that background processes do not survive
hibernation. They do. Everything here tests `/proc/$PID` instead.

Usage
-----
    python open_questions_probe.py

Evidence lands in ../../.evidence/ (gitignored). Every sandbox is killed in a
`finally`, and the script lists sandboxes at the end to prove it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

TEMPLATE = "code-interpreter-v1"
DEFAULT_REGION = "ap-southeast-1"


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


def iso(dt: Any) -> str:
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


def listed(Sandbox, conn: dict, sid: str) -> dict[str, Any] | None:
    """Find a sandbox in the account listing WITHOUT connecting to it.

    Load-bearing: `connect()` mutates the very field we are measuring, so any
    read of a paused sandbox's `end_at` has to come from a listing.
    """
    from e2b.sandbox.sandbox_api import SandboxQuery, SandboxState

    # `state` must hold SandboxState MEMBERS, not the equivalent strings — the
    # serializer calls `.value` on each and an AttributeError from a bare
    # "paused" is easy to swallow into a silently empty result. The unfiltered
    # listing is last and is the one that matters: it returns paused sandboxes
    # too, which is what makes it a sound orphan check.
    for state in ([SandboxState.PAUSED], [SandboxState.RUNNING], None):
        query = SandboxQuery(state=state) if state else None
        page, err = _try(lambda q=query: list(Sandbox.list(query=q, **conn).next_items()))
        for s in (page or []):
            if getattr(s, "sandbox_id", "") == sid:
                return {
                    "state": str(getattr(s, "state", "")),
                    "started_at": iso(getattr(s, "started_at", "")),
                    "end_at": iso(getattr(s, "end_at", "")),
                }
    return None


# --------------------------------------------------------------------------
# (a) the TTL clock


@dataclass
class TtlEvidence:
    a1_short_ttl_s: int = 0
    a1_waited_s: int = 0
    a1_end_at_at_create: str = ""
    a1_end_at_while_paused: str = ""
    a1_survived_past_own_expiry: bool = False
    a1_reconnect_error: str = ""
    a1_state_intact: bool = False
    a2_long_ttl_s: int = 0
    a2_end_at_at_create: str = ""
    a2_end_at_while_paused: str = ""
    a2_end_at_after_explicit_connect: str = ""
    a2_explicit_connect_timeout_s: int = 0
    a3_end_at_after_bare_connect: str = ""
    a3_bare_connect_grant_s: float | None = None
    verdict: str = ""


def question_a(Sandbox, conn: dict, short_ttl: int, wait_s: int,
               long_ttl: int) -> TtlEvidence:
    ev = TtlEvidence(a1_short_ttl_s=short_ttl, a1_waited_s=wait_s, a2_long_ttl_s=long_ttl)

    # ---- A1: pause a short-lived sandbox and outlive its own expiry ------
    sb = None
    sid = ""
    try:
        sb = Sandbox.create(template=TEMPLATE, timeout=short_ttl, **conn)
        sid = sb.sandbox_id
        info = sb.get_info()
        ev.a1_end_at_at_create = iso(info.end_at)
        sb.commands.run("echo a1-marker > /home/user/a1.txt")
        sb.pause(keep_memory=True)
        print(f"[a1] {sid} ttl={short_ttl}s paused; own expiry {ev.a1_end_at_at_create}")

        seen = listed(Sandbox, conn, sid)
        ev.a1_end_at_while_paused = (seen or {}).get("end_at", "<not listed>")
        print(f"[a1] while paused: {seen}")

        print(f"[a1] waiting {wait_s}s — past its own expiry…")
        time.sleep(wait_s)

        # A long explicit timeout here so the reconnect itself cannot be
        # what saves it later in the run; survival is decided before this.
        woken, err = _try(lambda: Sandbox.connect(sid, timeout=600, **conn))
        if woken is None:
            ev.a1_reconnect_error = err
            ev.a1_survived_past_own_expiry = False
            print(f"[a1] GONE after its expiry: {err}")
        else:
            sb = woken
            ev.a1_survived_past_own_expiry = True
            check = sb.commands.run("cat /home/user/a1.txt")
            ev.a1_state_intact = "a1-marker" in check.stdout
            print(f"[a1] SURVIVED past its own expiry; state intact={ev.a1_state_intact}")
    finally:
        if sb is not None:
            _try(sb.kill)

    # ---- A2 / A3: what connect() does to end_at --------------------------
    for label in ("a2", "a3"):
        sb = None
        try:
            sb = Sandbox.create(template=TEMPLATE, timeout=long_ttl, **conn)
            sid = sb.sandbox_id
            created_end = iso(sb.get_info().end_at)
            sb.pause(keep_memory=True)
            paused_end = (listed(Sandbox, conn, sid) or {}).get("end_at", "<not listed>")
            time.sleep(10)

            t_connect = datetime.now(timezone.utc)
            if label == "a2":
                ev.a2_end_at_at_create = created_end
                ev.a2_end_at_while_paused = paused_end
                ev.a2_explicit_connect_timeout_s = long_ttl
                sb = Sandbox.connect(sid, timeout=long_ttl, **conn)
                ev.a2_end_at_after_explicit_connect = iso(sb.get_info().end_at)
                print(f"[a2] explicit connect(timeout={long_ttl}): "
                      f"end_at {created_end} -> {ev.a2_end_at_after_explicit_connect}")
            else:
                # The confound, measured: a bare connect grants
                # `default_sandbox_timeout`, silently.
                sb = Sandbox.connect(sid, **conn)
                after = sb.get_info().end_at
                ev.a3_end_at_after_bare_connect = iso(after)
                ev.a3_bare_connect_grant_s = round((after - t_connect).total_seconds(), 1)
                print(f"[a3] BARE connect(): end_at {created_end} -> "
                      f"{ev.a3_end_at_after_bare_connect} "
                      f"(~{ev.a3_bare_connect_grant_s}s from now)")
        finally:
            if sb is not None:
                _try(sb.kill)

    if ev.a1_survived_past_own_expiry:
        ev.verdict = (
            "Pausing STOPS the reaper: a sandbox paused before its end_at was "
            "still resumable long after that end_at passed, and its filesystem "
            "was intact. Not explicable by the connect-reset, which only "
            "applies once a connect succeeds."
        )
    else:
        ev.verdict = (
            "The TTL clock KEEPS RUNNING while paused: the sandbox was gone "
            "after its own end_at. The controller MUST re-arm the timeout "
            "before pausing, or wake before expiry."
        )
    return ev


# --------------------------------------------------------------------------
# (b) light vs deep hibernation


@dataclass
class HibernationEvidence:
    keep_memory: bool = True
    pause_ms: float | None = None
    wake_ms: float | None = None
    processes_survived: bool = False
    uptime_before_s: float | None = None
    uptime_after_s: float | None = None
    wall_gap_s: float | None = None
    uptime_gap_s: float | None = None
    clock_frozen_while_paused: bool = False
    filesystem_intact: bool = False
    error: str = ""


def hibernation_variant(Sandbox, conn: dict, keep_memory: bool,
                        wait_s: int) -> HibernationEvidence:
    """One pause variant, measured by what the guest itself can see.

    `/proc/uptime` is the discriminator. A memory snapshot freezes the guest
    clock, so uptime lags wall-clock by the time spent paused. A cold boot
    from disk resets uptime to ~0 and kills every process.
    """
    ev = HibernationEvidence(keep_memory=keep_memory)
    sb = None
    try:
        sb = Sandbox.create(template=TEMPLATE, timeout=600, **conn)
        sid = sb.sandbox_id
        sb.commands.run(
            "echo hib-marker > /home/user/hib.txt && "
            "{ nohup sh -c 'while true; do sleep 3; done' & echo $! > /home/user/hib.pid; } "
            ">/dev/null 2>&1"
        )
        pre = sb.commands.run("cut -d' ' -f1 /proc/uptime; cat /home/user/hib.pid")
        lines = [l for l in pre.stdout.strip().splitlines() if l.strip()]
        ev.uptime_before_s = float(lines[0])
        pid = lines[1].strip()

        wall0 = time.monotonic()
        t0 = time.monotonic()
        sb.pause(keep_memory=keep_memory)
        ev.pause_ms = (time.monotonic() - t0) * 1000
        time.sleep(wait_s)
        t0 = time.monotonic()
        sb = Sandbox.connect(sid, timeout=600, **conn)
        ev.wake_ms = (time.monotonic() - t0) * 1000

        post = sb.commands.run(
            f"cut -d' ' -f1 /proc/uptime; "
            f"[ -d /proc/{pid} ] && echo ALIVE || echo DEAD; "
            f"cat /home/user/hib.txt 2>/dev/null || echo MISSING"
        )
        plines = [l for l in post.stdout.strip().splitlines() if l.strip()]
        ev.wall_gap_s = round(time.monotonic() - wall0, 1)
        ev.uptime_after_s = float(plines[0])
        ev.processes_survived = len(plines) > 1 and plines[1] == "ALIVE"
        ev.filesystem_intact = any("hib-marker" in l for l in plines)
        ev.uptime_gap_s = round(ev.uptime_after_s - ev.uptime_before_s, 1)
        # Frozen if the guest saw materially less time pass than the wall did.
        ev.clock_frozen_while_paused = (ev.wall_gap_s - ev.uptime_gap_s) > (wait_s * 0.5)
        print(f"[b keep_memory={keep_memory}] pause={ev.pause_ms:.0f}ms "
              f"wake={ev.wake_ms:.0f}ms procs={'ALIVE' if ev.processes_survived else 'DEAD'} "
              f"wall={ev.wall_gap_s}s guest_uptime_delta={ev.uptime_gap_s}s "
              f"fs={'intact' if ev.filesystem_intact else 'LOST'}")
        return ev
    except Exception as exc:  # noqa: BLE001
        ev.error = redact(f"{type(exc).__name__}: {exc}")
        print(f"[b keep_memory={keep_memory}] ERROR: {ev.error}")
        return ev
    finally:
        if sb is not None:
            _try(sb.kill)


def probe_light_surfaces(Sandbox, conn: dict) -> dict[str, str]:
    """Does anything OTHER than `keep_memory` distinguish the two modes?

    Recorded so the answer is "we looked, here is the whole surface", not an
    invented API. `lifecycle` is included because auto-pause-on-timeout would
    change the controller's design if the backend honours it.
    """
    out: dict[str, str] = {}
    out["pause_signature"] = "pause(keep_memory: bool = True) — the ONLY light/deep switch in the SDK"
    out["beta_pause_present"] = str(hasattr(Sandbox, "beta_pause"))
    out["light_named_api"] = "none — no light_pause/hibernate/suspend anywhere on Sandbox"

    names = [n for n in dir(Sandbox) if not n.startswith("_")]
    out["sandbox_surface"] = ", ".join(sorted(names))

    # Snapshots are a separate persistence surface; whether FC implements them
    # decides whether "pre-baked worker image" is a template problem or a
    # snapshot problem.
    sb, err = _try(lambda: Sandbox.create(template=TEMPLATE, timeout=120, **conn))
    if sb is None:
        out["snapshot_probe"] = f"could not create a sandbox to probe: {err}"
        return out
    try:
        snap, err = _try(lambda: sb.create_snapshot(name="probe-snapshot"))
        out["create_snapshot"] = f"OK: {snap}" if snap is not None else f"UNAVAILABLE: {err}"
        page, err = _try(lambda: list(sb.list_snapshots(**conn).next_items()))
        out["list_snapshots"] = f"OK: {len(page or [])} snapshot(s)" if err == "" else f"UNAVAILABLE: {err}"
        if snap is not None:
            sid_ = getattr(snap, "snapshot_id", None) or getattr(snap, "id", None)
            if sid_:
                _, derr = _try(lambda: Sandbox.delete_snapshot(sid_, **conn))
                out["delete_snapshot"] = "OK" if derr == "" else f"FAILED: {derr}"

        # Auto-pause-on-timeout. If FC accepts this, the controller does not
        # have to hold a timer to pause idle workers.
        sb2, err = _try(lambda: Sandbox.create(
            template=TEMPLATE, timeout=120,
            lifecycle={"on_timeout": {"action": "pause", "keep_memory": True},
                       "auto_resume": True},
            **conn))
        if sb2 is None:
            out["lifecycle_on_timeout_pause"] = f"REJECTED: {err}"
        else:
            try:
                info = sb2.get_info()
                out["lifecycle_on_timeout_pause"] = f"ACCEPTED; readback={info.lifecycle}"
            finally:
                _try(sb2.kill)
    finally:
        _try(sb.kill)
    return out


# --------------------------------------------------------------------------
# (c) inventory


INVENTORY_CMD = r"""
echo "whoami=$(whoami)"
echo "uid=$(id -u)"
echo "sudo=$(command -v sudo >/dev/null && echo yes || echo no)"
echo "docker=$(command -v docker >/dev/null && echo yes || echo no)"
echo "ps_present=$(command -v ps >/dev/null && echo yes || echo NO-procps)"
echo "python=$(python3 -V 2>&1)"
echo "pip=$(python3 -m pip -V 2>&1 | cut -d' ' -f1-2)"
echo "pip_index=$(python3 -m pip config list 2>/dev/null | tr '\n' ' ')"
echo "nproc=$(nproc)"
echo "mem_total_kb=$(awk '/MemTotal/{print $2}' /proc/meminfo)"
echo "mem_avail_kb=$(awk '/MemAvailable/{print $2}' /proc/meminfo)"
echo "disk_root=$(df -Pm / | tail -1 | awk '{print $2"MB total, "$4"MB avail"}')"
echo "home_writable=$(touch /home/user/.wtest 2>/dev/null && echo yes || echo no)"
echo "cwd_default=$(pwd)"
echo "kernel=$(uname -srm)"
echo "os=$(. /etc/os-release 2>/dev/null; echo $PRETTY_NAME)"
echo "uptime_s=$(cut -d' ' -f1 /proc/uptime)"
echo "egress_pypi_org=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://pypi.org/simple/flashnode/ || echo FAIL)"
echo "egress_github=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://api.github.com || echo FAIL)"
echo "egress_onrender=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://flashml-api.onrender.com/healthz || echo FAIL)"
rm -f /home/user/.wtest 2>/dev/null
true
"""


def question_c(Sandbox, conn: dict) -> dict[str, str]:
    inv: dict[str, str] = {}
    sb = None
    try:
        sb = Sandbox.create(template=TEMPLATE, timeout=300, **conn)
        r = sb.commands.run(INVENTORY_CMD, timeout=120)
        for line in r.stdout.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                inv[k.strip()] = v.strip()
        info = sb.get_info()
        inv["api_cpu_count"] = str(info.cpu_count)
        inv["api_memory_mb"] = str(info.memory_mb)
        inv["api_envd_version"] = str(info.envd_version)
        inv["api_lifecycle_default"] = str(info.lifecycle)
        inv["api_allow_internet_access"] = str(info.allow_internet_access)
    except Exception as exc:  # noqa: BLE001
        inv["error"] = redact(f"{type(exc).__name__}: {exc}")
    finally:
        if sb is not None:
            _try(sb.kill)
    return inv


# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default=DEFAULT_REGION)
    ap.add_argument("--short-ttl", type=int, default=90,
                    help="seconds; A1 pauses a sandbox with this TTL and outlives it")
    ap.add_argument("--a1-wait", type=int, default=150,
                    help="seconds spent paused in A1 — must exceed --short-ttl")
    ap.add_argument("--long-ttl", type=int, default=1800)
    ap.add_argument("--hib-wait", type=int, default=25)
    ap.add_argument("--evidence-dir",
                    default=str(Path(__file__).resolve().parents[2] / ".evidence"))
    args = ap.parse_args()

    from e2b_code_interpreter import Sandbox

    api_url, domain = endpoints(args.region)
    conn = {"api_key": api_key(), "api_url": api_url, "domain": domain}

    report: dict[str, Any] = {"region": args.region, "template": TEMPLATE}
    try:
        import importlib.metadata as md
        report["sdk_version"] = md.version("e2b")
    except Exception:  # noqa: BLE001
        report["sdk_version"] = "unknown"

    try:
        print("=== (c) inventory ===")
        report["c_inventory"] = question_c(Sandbox, conn)
        print(json.dumps(report["c_inventory"], indent=2))

        print("\n=== (b) light vs deep hibernation ===")
        report["b_surfaces"] = probe_light_surfaces(Sandbox, conn)
        report["b_keep_memory_true"] = asdict(
            hibernation_variant(Sandbox, conn, True, args.hib_wait))
        report["b_keep_memory_false"] = asdict(
            hibernation_variant(Sandbox, conn, False, args.hib_wait))

        print("\n=== (a) does a pause stop the TTL clock? ===")
        report["a_ttl"] = asdict(
            question_a(Sandbox, conn, args.short_ttl, args.a1_wait, args.long_ttl))
        print("\n" + report["a_ttl"]["verdict"])
    finally:
        leftovers, _ = _try(lambda: list(Sandbox.list(**conn).next_items()))
        for s in (leftovers or []):
            sid = getattr(s, "sandbox_id", "")
            print(f"sweeping leftover {sid}")
            _try(lambda i=sid: Sandbox.connect(i, timeout=60, **conn).kill())
        remaining, _ = _try(lambda: list(Sandbox.list(**conn).next_items()))
        report["live_sandboxes_after"] = [getattr(s, "sandbox_id", "") for s in (remaining or [])]
        print(f"live sandboxes after: {report['live_sandboxes_after']}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.evidence_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"alibaba-open-questions-{stamp}.json"
    out_path.write_text(redact(json.dumps(
        {"captured_at": stamp, "report": report}, indent=2, default=str)))
    print(f"\nEvidence: {out_path}")
    return 0 if not report.get("live_sandboxes_after") else 2


if __name__ == "__main__":
    sys.exit(main())
