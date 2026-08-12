#!/usr/bin/env python3
"""Prove FC Agent Sandbox hibernation on THIS account, before anything is built on it.

This script answers exactly one question and refuses to answer any others:

    Can this Alibaba account pause a sandbox and reconnect to it with state intact?

Alibaba documents pause/resume as allowlist-gated
(https://www.alibabacloud.com/help/en/functioncompute/pause-and-resume):

    "Pause and resume is currently available only to allowlisted accounts."

while the 2026-07-31 billing announcement says existing E2B SDK instances
auto-transition to the Pro tier, which carries both hibernation modes. Those two
statements may describe the same gate or two different ones. Reading more
documentation will not settle it; one API call will. That is this script.

**Why it runs before the lifecycle controller.** Every architecture decision in
`docs/superpowers/plans/2026-08-11-alibaba-competition-demo.md` assumes
hibernation works. Writing the controller first and discovering `pause()` is
blocked would cost one to two days out of four. This costs about a cent.

It also decides the region. `us-west-1` is Silicon Valley and Demo Day is in
Palo Alto; `ap-southeast-1` is what the rest of the field used. Pass both and
pick on measured wake latency rather than on convention.

Usage
-----
    export E2B_API_KEY="<created in the Function Compute console>"
    python alibaba_fc_sandbox_smoke.py --regions us-west-1,ap-southeast-1

A region-specific key may be supplied as E2B_API_KEY_US_WEST_1 etc.; the script
falls back to E2B_API_KEY. Evidence lands in ../../.evidence/ (gitignored).

Exit codes: 0 = GO (hibernation proven somewhere), 2 = NO-GO, 1 = harness error.
"""
from __future__ import annotations

import argparse
import hashlib
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
MARKER_PATH = "/home/user/flashml_prepared.json"
PIDFILE = "/home/user/flashml_bg.pid"
HIBERNATE_WAIT_S = 30

# Substrings that mean "your account may not call this", as opposed to a
# transport failure. Matched case-insensitively against the exception text.
ALLOWLIST_HINTS = (
    "allowlist", "allow list", "whitelist", "not enabled", "not authorized",
    "forbidden", "permission", "unsupported", "not available", "preview",
)


# --------------------------------------------------------------------------
# evidence


@dataclass
class Step:
    name: str
    ok: bool
    ms: float | None = None
    detail: str = ""


@dataclass
class RegionEvidence:
    region: str
    api_url: str
    domain: str
    sdk_version: str = ""
    template: str = TEMPLATE
    sandbox_id: str = ""
    create_ms: float | None = None
    pause_ms: float | None = None
    resume_ms: float | None = None
    marker_sha256_before: str = ""
    marker_sha256_after: str = ""
    pid_before: int | None = None
    pid_after: int | None = None
    state_observations: dict[str, str] = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)
    hibernation_proven: bool = False
    allowlist_blocked: bool = False
    cleanup_verified: bool = False
    error: str = ""


def redact(text: str) -> str:
    """Never let a key reach stdout or the evidence file.

    Belt and braces: strip anything that looks like a key, then strip the
    literal values of every E2B_* environment variable we read.
    """
    out = re.sub(r"(?i)(api[_-]?key\"?\s*[:=]\s*\"?)[A-Za-z0-9_\-]{8,}", r"\1<redacted>", text)
    for name, value in os.environ.items():
        if name.startswith("E2B_") and "KEY" in name and value and len(value) >= 8:
            out = out.replace(value, "<redacted>")
    return out


def _try(fn: Callable[[], Any]) -> tuple[Any, str]:
    """Run a probe that is allowed to fail.

    Part of this script's job is discovering which SDK surfaces this account and
    SDK version actually expose, so an AttributeError here is data, not a crash.
    """
    try:
        return fn(), ""
    except Exception as exc:  # noqa: BLE001 - probing on purpose
        return None, redact(f"{type(exc).__name__}: {exc}")


def looks_like_allowlist(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(hint in text for hint in ALLOWLIST_HINTS)


# --------------------------------------------------------------------------
# the run


def endpoints(region: str) -> tuple[str, str]:
    return f"https://api.{region}.e2b.fc.aliyuncs.com", f"{region}.e2b.fc.aliyuncs.com"


def api_key_for(region: str) -> str:
    specific = f"E2B_API_KEY_{region.upper().replace('-', '_')}"
    key = os.environ.get(specific) or os.environ.get("E2B_API_KEY", "")
    if not key.strip():
        raise RuntimeError(f"Missing {specific} and E2B_API_KEY. Create a key in the FC console.")
    return key.strip()


def run_region(region: str, wait_s: int) -> RegionEvidence:
    from e2b_code_interpreter import Sandbox  # imported late so --help works without the SDK

    api_url, domain = endpoints(region)
    ev = RegionEvidence(region=region, api_url=api_url, domain=domain)

    try:
        import e2b
        ev.sdk_version = getattr(e2b, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        ev.sdk_version = "unknown"

    key = api_key_for(region)
    conn = {"api_key": key, "api_url": api_url, "domain": domain}
    sandbox = None

    try:
        # 1. create -------------------------------------------------------
        t0 = time.monotonic()
        sandbox = Sandbox.create(template=TEMPLATE, **conn)
        ev.create_ms = (time.monotonic() - t0) * 1000
        ev.sandbox_id = getattr(sandbox, "sandbox_id", "") or getattr(sandbox, "id", "")
        ev.steps.append(Step("create", True, ev.create_ms, f"sandbox_id={ev.sandbox_id}"))
        print(f"[{region}] created {ev.sandbox_id} in {ev.create_ms:.0f} ms")

        # 2. prepare: a nonce marker and a long-lived background process ---
        # The nonce proves the filesystem is the SAME one after the wake, not a
        # fresh sandbox from the same template. The PID proves (or disproves)
        # process continuity, which the plan's risk R3 says we must not assume.
        nonce = secrets.token_hex(16)
        marker = json.dumps({"nonce": nonce, "template": TEMPLATE, "region": region})
        sandbox.commands.run(f"mkdir -p $(dirname {MARKER_PATH}) && cat > {MARKER_PATH} <<'EOF'\n{marker}\nEOF")
        sandbox.commands.run(
            f"nohup sh -c 'while true; do sleep 5; done' >/dev/null 2>&1 & echo $! > {PIDFILE}"
        )
        before = sandbox.commands.run(f"sha256sum {MARKER_PATH} | cut -d' ' -f1; cat {PIDFILE}")
        lines = [l for l in before.stdout.strip().splitlines() if l.strip()]
        ev.marker_sha256_before = lines[0] if lines else ""
        ev.pid_before = int(lines[1]) if len(lines) > 1 and lines[1].strip().isdigit() else None
        ev.steps.append(Step("prepare", True, None, f"pid={ev.pid_before}"))
        print(f"[{region}] prepared: marker={ev.marker_sha256_before[:12]}… pid={ev.pid_before}")

        # 3. observe the active state -------------------------------------
        for probe in ("get_info", "get_metrics"):
            value, err = _try(lambda p=probe: getattr(sandbox, p)())
            ev.state_observations[f"active.{probe}"] = redact(str(value)) if err == "" else err

        # 4. THE QUESTION -------------------------------------------------
        t0 = time.monotonic()
        try:
            sandbox.pause()
            ev.pause_ms = (time.monotonic() - t0) * 1000
            ev.steps.append(Step("pause", True, ev.pause_ms))
            print(f"[{region}] paused in {ev.pause_ms:.0f} ms")
        except Exception as exc:  # noqa: BLE001
            ev.allowlist_blocked = looks_like_allowlist(exc)
            ev.error = redact(f"{type(exc).__name__}: {exc}")
            ev.steps.append(Step("pause", False, None, ev.error))
            verdict = "ALLOWLIST-BLOCKED" if ev.allowlist_blocked else "FAILED"
            print(f"[{region}] pause {verdict}: {ev.error}")
            return ev

        # 5. wait outside the sandbox -------------------------------------
        print(f"[{region}] waiting {wait_s}s while hibernated…")
        time.sleep(wait_s)

        # 6. reconnect — docs say connect() auto-resumes a paused sandbox --
        t0 = time.monotonic()
        sandbox = Sandbox.connect(ev.sandbox_id, **conn)
        ev.resume_ms = (time.monotonic() - t0) * 1000
        ev.steps.append(Step("resume", True, ev.resume_ms))
        print(f"[{region}] resumed in {ev.resume_ms:.0f} ms")

        # 7. did state survive? -------------------------------------------
        after = sandbox.commands.run(
            f"sha256sum {MARKER_PATH} | cut -d' ' -f1; cat {PIDFILE}; "
            f"ps -p $(cat {PIDFILE}) >/dev/null 2>&1 && echo ALIVE || echo GONE"
        )
        alines = [l for l in after.stdout.strip().splitlines() if l.strip()]
        ev.marker_sha256_after = alines[0] if alines else ""
        ev.pid_after = int(alines[1]) if len(alines) > 1 and alines[1].strip().isdigit() else None
        proc_state = alines[2] if len(alines) > 2 else "UNKNOWN"
        ev.state_observations["process_after_resume"] = proc_state

        marker_ok = bool(ev.marker_sha256_before) and ev.marker_sha256_before == ev.marker_sha256_after
        ev.steps.append(Step("state_continuity", marker_ok, None, f"process={proc_state}"))
        print(f"[{region}] marker {'MATCH' if marker_ok else 'MISMATCH'}, process {proc_state}")

        # 8. can it still do work? ----------------------------------------
        cont = sandbox.commands.run("python3 -c \"print('continued')\"")
        cont_ok = "continued" in cont.stdout
        ev.steps.append(Step("continue", cont_ok, None, cont.stdout.strip()[:80]))

        ev.hibernation_proven = marker_ok and cont_ok
        return ev

    except Exception as exc:  # noqa: BLE001
        ev.error = redact(f"{type(exc).__name__}: {exc}")
        ev.steps.append(Step("run", False, None, ev.error))
        print(f"[{region}] ERROR: {ev.error}")
        return ev

    finally:
        # Never leave a sandbox running. A forgotten sandbox bills by the second
        # against a voucher that expires 2026-08-15.
        if sandbox is not None:
            _, err = _try(sandbox.kill)
            ev.cleanup_verified = err == ""
            ev.steps.append(Step("kill", ev.cleanup_verified, None, err))
            print(f"[{region}] cleanup {'ok' if ev.cleanup_verified else 'FAILED: ' + err}")


# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regions", default="us-west-1,ap-southeast-1",
                    help="comma-separated; measured wake latency decides which we use")
    ap.add_argument("--wait", type=int, default=HIBERNATE_WAIT_S, help="seconds to stay hibernated")
    ap.add_argument("--evidence-dir", default=str(Path(__file__).resolve().parents[2] / ".evidence"))
    args = ap.parse_args()

    regions = [r.strip() for r in args.regions.split(",") if r.strip()]
    results: list[RegionEvidence] = []
    for region in regions:
        print(f"\n=== {region} ===")
        try:
            results.append(run_region(region, args.wait))
        except RuntimeError as exc:  # missing key — a harness problem, not a verdict
            print(f"[{region}] {exc}")
            return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.evidence_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"alibaba-sandbox-smoke-{stamp}.json"
    out_path.write_text(json.dumps(
        {"captured_at": stamp, "template": TEMPLATE, "regions": [asdict(r) for r in results]},
        indent=2, default=str,
    ))

    print("\n" + "=" * 64)
    proven = [r for r in results if r.hibernation_proven]
    for r in results:
        wake = f"{r.resume_ms:.0f} ms" if r.resume_ms else "—"
        flag = "GO " if r.hibernation_proven else ("BLOCKED" if r.allowlist_blocked else "no")
        print(f"  {r.region:<18} hibernation={flag:<8} wake={wake:<10} cleanup={r.cleanup_verified}")
    print(f"\nEvidence: {out_path}")

    if proven:
        best = min(proven, key=lambda r: r.resume_ms or float("inf"))
        print(f"\nGO — hibernation proven. Fastest wake: {best.region} "
              f"({best.resume_ms:.0f} ms). Use that region.")
        print("Next: Task 1 (freeze the workload) and Task 2 (SandboxGateway).")
        return 0

    if any(r.allowlist_blocked for r in results):
        print("\nNO-GO — pause/resume is allowlist-gated on this account.")
        print("Do NOT write the lifecycle controller. Instead, today:")
        print("  1. Request enablement: account 5055584162230015, region(s) above, deadline 2026-08-15")
        print("  2. Ask DingTalk group 179855020297 whether the Pro tier transition satisfies the gate")
        print("  3. Ask the same in the hackathon Discord")
        print("  4. Exercise risk R1 in the plan. Never simulate hibernation.")
        return 2

    print("\nNO-GO — hibernation not proven, and it does not look like the allowlist.")
    print("Check the key, the region, and that the endpoint matches the key's region.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
