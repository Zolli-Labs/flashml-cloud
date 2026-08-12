#!/usr/bin/env python3
"""Can a real FlashNode agent live inside an Alibaba FC Agent Sandbox?

The competition architecture assumes an FC sandbox hosts a FlashNode worker
exactly the way a RunPod pod does. Everything downstream of that assumption —
the lifecycle controller, the demo script, the "elastic volunteer" story —
is worthless if the agent cannot start there. So this asks, in one sandbox:

  1. What IS this machine? (python, cpu, memory, disk, user, egress)
  2. Does `pip install flashnode` work, and how long does it take? If it is
     slow it must be baked into a template and never run on the demo path.
  3. Does `flashnode work --runner trusted` START and STAY UP? That is the
     only viable tier: there is no Docker inside a sandbox, and none can be
     installed, so the container tiers are unsatisfiable by construction.
  4. What does it do when the coordinator is unreachable — crash, or retry?
     A sandbox may well wake before its coordinator is routable.
  5. Does a running agent survive HIBERNATION? This is the whole product
     idea. If pause/resume kills the worker, "park the node between rounds"
     becomes "rebuild the node between rounds".

Test 3/4/5 need a coordinator that answers. Rather than expose the real one,
a stub is written INSIDE the sandbox implementing exactly the routes the
agent touches before it has work (register, node heartbeat, and a 204 lease
claim meaning "nothing for you"). That isolates the question to "does the
agent run here", which is the question.

THREE THINGS THIS TEMPLATE WILL TEACH YOU THE HARD WAY

* `pip` is pinned to `https://mirrors.aliyun.com/pypi/simple/` by
  `/etc/pip.conf`, and that mirror LAGS PyPI. It served flashnode 0.3.5 when
  0.4.0 was published, and flashruntime 0.5.0 when 0.6.0 was — so
  `pip install flashnode==0.4.0` fails outright with "no matching
  distribution", which reads like a broken package rather than a stale
  index. Every install here passes `-i https://pypi.org/simple/`.

* There is NO `ps`; procps is not in the image. `ps -p $PID` exits 127, so
  the idiom `ps -p $PID >/dev/null && echo ALIVE || echo GONE` reports every
  process on earth as GONE. An earlier run concluded from exactly that that
  background processes do not survive hibernation. They do. Liveness here is
  `[ -d /proc/$PID ]`.

* `commands.run("cd x && nohup daemon &")` NEVER RETURNS. The `&` backgrounds
  the whole AND-list, bash runs that list in a subshell, and the subshell
  inherits the command's stdout pipe — so envd waits for an EOF that only
  arrives when the daemon dies. The SDK's own `timeout=` does not rescue you.
  Group the redirect (`{ ...; } >/dev/null 2>&1 &`) or pass `background=True`.

Usage
-----
    python flashnode_in_sandbox_probe.py [--region ap-southeast-1]

Evidence lands in ../../.evidence/ (gitignored). The sandbox is killed in a
`finally` block; nothing is left billing.
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
FLASHNODE_SPEC = "flashnode==0.4.0"
UPSTREAM_INDEX = "https://pypi.org/simple/"
STUB_PORT = 8100

# The stub coordinator: only the routes `flashnode work` reaches before it is
# given a task. Everything else 404s loudly, so a silent mis-assumption shows
# up as a failure rather than as a pass.
STUB_SRC = r'''
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

HITS = {}

class H(BaseHTTPRequestHandler):
    def _count(self):
        key = self.path.split("?")[0]
        HITS[key] = HITS.get(key, 0) + 1
        with open("/home/user/stub_hits.json", "w") as fh:
            json.dump(HITS, fh)

    def do_POST(self):
        self._count()
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        p = self.path.split("?")[0]
        if p == "/v1alpha1/nodes/register":
            with open("/home/user/stub_registration.json", "wb") as fh:
                fh.write(raw or b"{}")
            return self._ok({"ok": True})
        if p.endswith("/heartbeat"):
            return self._ok({"ok": True})
        if p == "/v1alpha1/leases/claim":
            # 204: the coordinator answered and has no work. The agent must
            # keep polling rather than treat this as an outage.
            self.send_response(204); self.end_headers(); return
        self.send_response(404); self.end_headers()

    def do_GET(self):
        self._count()
        self.send_response(404); self.end_headers()

    def _ok(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

HTTPServer(("127.0.0.1", PORT), H).serve_forever()
'''

INVENTORY_CMD = r"""
echo "whoami=$(whoami)"
echo "uid=$(id -u)"
echo "sudo=$(command -v sudo >/dev/null && echo yes || echo no)"
echo "docker=$(command -v docker >/dev/null && echo yes || echo no)"
echo "ps_present=$(command -v ps >/dev/null && echo yes || echo NO-procps)"
echo "python=$(python3 -V 2>&1)"
echo "pip=$(python3 -m pip -V 2>&1 | cut -d' ' -f1-2)"
echo "pip_default_index=$(python3 -m pip config list 2>/dev/null | tr '\n' ' ')"
echo "nproc=$(nproc)"
echo "mem_total_kb=$(awk '/MemTotal/{print $2}' /proc/meminfo)"
echo "mem_avail_kb=$(awk '/MemAvailable/{print $2}' /proc/meminfo)"
echo "disk_root=$(df -Pm / | tail -1 | awk '{print $2"MB total, "$4"MB avail"}')"
echo "home_writable=$(touch /home/user/.wtest 2>/dev/null && echo yes || echo no)"
echo "kernel=$(uname -srm)"
echo "os=$(. /etc/os-release 2>/dev/null; echo $PRETTY_NAME)"
echo "egress_pypi_org=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://pypi.org/simple/flashnode/ || echo FAIL)"
echo "egress_github=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 https://api.github.com || echo FAIL)"
rm -f /home/user/.wtest 2>/dev/null
true
"""


@dataclass
class Probe:
    name: str
    ok: bool
    ms: float | None = None
    detail: str = ""


@dataclass
class Evidence:
    region: str
    api_url: str
    domain: str
    template: str = TEMPLATE
    sandbox_id: str = ""
    sdk_version: str = ""
    create_ms: float | None = None
    inventory: dict[str, str] = field(default_factory=dict)
    install_ms: float | None = None
    install_ok: bool = False
    install_from_default_index_error: str = ""
    installed_versions: str = ""
    installed_size: str = ""
    doctor_exit_code: int | None = None
    doctor_tail: str = ""
    no_coordinator_exit_code: int | None = None
    no_coordinator_seconds: float | None = None
    no_coordinator_behaviour: str = ""
    no_coordinator_error_class: str = ""
    agent_pid: int | None = None
    agent_alive_after_s: float | None = None
    agent_registered: bool = False
    registration: dict[str, Any] = field(default_factory=dict)
    stub_hits_before_pause: dict[str, int] = field(default_factory=dict)
    pause_ms: float | None = None
    wake_ms: float | None = None
    agent_survived_hibernation: bool = False
    stub_hits_after_wake: dict[str, int] = field(default_factory=dict)
    agent_resumed_polling: bool = False
    probes: list[Probe] = field(default_factory=list)
    cleanup_verified: bool = False
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
    except Exception as exc:  # noqa: BLE001 - probing on purpose
        return None, redact(f"{type(exc).__name__}: {exc}")


def endpoints(region: str) -> tuple[str, str]:
    return f"https://api.{region}.e2b.fc.aliyuncs.com", f"{region}.e2b.fc.aliyuncs.com"


def api_key() -> str:
    key = os.environ.get("E2B_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Missing E2B_API_KEY.")
    return key


def sh(sandbox, cmd: str, timeout: float = 120) -> str:
    """Run a shell command and return its combined output whatever it exits.

    `commands.run` raises `CommandExitException` on any non-zero exit, and
    that exception IS the result (it subclasses CommandResult) — so a
    non-zero exit is data here, never a crash.
    """
    try:
        r = sandbox.commands.run(cmd, timeout=timeout)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return ((getattr(exc, "stdout", "") or "") + (getattr(exc, "stderr", "") or "")
                or f"{type(exc).__name__}: {exc}")


def parse_kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def load_json(text: str) -> Any:
    try:
        return json.loads(text.strip())
    except Exception:  # noqa: BLE001
        return {}


def probe(region: str, sandbox_timeout_s: int, watch_s: float,
          hibernate_s: float) -> Evidence:
    from e2b_code_interpreter import Sandbox

    api_url, domain = endpoints(region)
    ev = Evidence(region=region, api_url=api_url, domain=domain)
    try:
        import importlib.metadata as md
        ev.sdk_version = md.version("e2b")
    except Exception:  # noqa: BLE001
        ev.sdk_version = "unknown"

    conn = {"api_key": api_key(), "api_url": api_url, "domain": domain}
    sandbox = None
    try:
        t0 = time.monotonic()
        sandbox = Sandbox.create(template=TEMPLATE, timeout=sandbox_timeout_s, **conn)
        ev.create_ms = (time.monotonic() - t0) * 1000
        ev.sandbox_id = sandbox.sandbox_id
        ev.probes.append(Probe("create", True, ev.create_ms, ev.sandbox_id))
        print(f"created {ev.sandbox_id} in {ev.create_ms:.0f} ms")

        # ---- 1. what is this machine? -----------------------------------
        ev.inventory = parse_kv(sh(sandbox, INVENTORY_CMD, timeout=90))
        ev.probes.append(Probe("inventory", True, None, f"{len(ev.inventory)} facts"))
        print("inventory:", json.dumps(ev.inventory, indent=2))

        # ---- 2. install ---------------------------------------------------
        # The default index first, recorded as the trap it is, then the one
        # that works. The failing call costs a second and stops the next
        # person diagnosing "flashnode is broken".
        default_out = sh(sandbox,
                         f"python3 -m pip install --no-input --disable-pip-version-check "
                         f"{FLASHNODE_SPEC} 2>&1 | tail -4", timeout=300)
        if "No matching distribution" in default_out or "Could not find a version" in default_out:
            ev.install_from_default_index_error = default_out.strip()[-500:]
            print("default index REFUSED the pin (stale Aliyun mirror) — using upstream")

        t0 = time.monotonic()
        out = sh(sandbox,
                 f"python3 -m pip install --no-input --disable-pip-version-check "
                 f"-i {UPSTREAM_INDEX} {FLASHNODE_SPEC} 2>&1 | tail -6", timeout=600)
        ev.install_ms = (time.monotonic() - t0) * 1000
        ev.install_ok = "Successfully installed" in out or "already satisfied" in out.lower()
        ev.probes.append(Probe("pip_install_upstream", ev.install_ok, ev.install_ms,
                               out.strip()[-400:]))
        print(f"pip install {FLASHNODE_SPEC} from upstream: "
              f"ok={ev.install_ok} in {ev.install_ms/1000:.1f}s")

        ev.installed_versions = sh(sandbox,
            "python3 - <<'PY'\n"
            "import importlib.metadata as m\n"
            "for n in ('flashnode','flashruntime','pydantic','psutil'):\n"
            "    try: print(n, m.version(n))\n"
            "    except Exception as e: print(n, 'MISSING')\n"
            "PY\n"
            "command -v flashnode || echo 'flashnode NOT on PATH'", timeout=90).strip()
        ev.installed_size = sh(sandbox, "du -sh ~/.local 2>/dev/null | cut -f1").strip()
        print(ev.installed_versions, "| size", ev.installed_size)

        # `doctor` is a DOCKER-tier instrument. It is expected to fail here,
        # and recording that is the point: a red doctor on a container host
        # is not a blocker, which is exactly why `flashnode work --runner
        # trusted` has its own gate.
        doc = sh(sandbox, "mkdir -p /home/user/work; FLASHNODE_WORKDIR=/home/user/work "
                          "flashnode doctor >/home/user/doctor.log 2>&1; echo EXIT=$?; "
                          "head -8 /home/user/doctor.log", timeout=180)
        m = re.search(r"EXIT=(\d+)", doc)
        ev.doctor_exit_code = int(m.group(1)) if m else None
        ev.doctor_tail = doc.strip()[:800]

        # ---- 3. credential store -----------------------------------------
        coord = f"http://127.0.0.1:{STUB_PORT}"
        sh(sandbox, "mkdir -p /home/user/.flashnode && "
                    f"printf '%s' '{{\"{coord}\": \"probe-machine-token\"}}' "
                    "> /home/user/.flashnode/credentials.json && "
                    "chmod 600 /home/user/.flashnode/credentials.json && "
                    "stat -c '%a %n' /home/user/.flashnode/credentials.json", timeout=60)

        # ---- 4. no coordinator: crash or retry? ---------------------------
        # 127.0.0.1:9 is the discard port — refused instantly, the fastest
        # honest "unreachable". A 60 s window is far longer than the agent's
        # own transport retry budget, so if it is still alive at the end it
        # is genuinely retrying rather than merely slow.
        t0 = time.monotonic()
        dead = sh(sandbox,
                  "cd /home/user && FLASHNODE_STATE_DIR=/home/user/.flashnode "
                  "FLASHNODE_WORKDIR=/home/user/work timeout 60 flashnode work "
                  "--runner trusted --coordinator http://127.0.0.1:9 --max-tasks 1 "
                  "--log-json >/home/user/nocoord.log 2>&1; echo EXIT=$?; "
                  "tail -4 /home/user/nocoord.log", timeout=150)
        ev.no_coordinator_seconds = round(time.monotonic() - t0, 1)
        m = re.search(r"EXIT=(\d+)", dead)
        ev.no_coordinator_exit_code = int(m.group(1)) if m else None
        if "CoordinatorUnreachable" in dead:
            ev.no_coordinator_error_class = "flashnode.executor.client.CoordinatorUnreachable"
        if ev.no_coordinator_exit_code == 124:
            ev.no_coordinator_behaviour = "RETRIES — still running when the 60s window closed"
        else:
            ev.no_coordinator_behaviour = (
                f"CRASHES — exited {ev.no_coordinator_exit_code} after "
                f"{ev.no_coordinator_seconds}s")
        ev.probes.append(Probe("no_coordinator", True, None, ev.no_coordinator_behaviour))
        print(f"no-coordinator: {ev.no_coordinator_behaviour}")

        # ---- 5. with a coordinator that answers ---------------------------
        sandbox.files.write("/home/user/stub_coordinator.py",
                            STUB_SRC.replace("PORT", str(STUB_PORT)))
        # Note the braces: the redirect covers the WHOLE backgrounded group.
        # Without them this call never returns — see the module docstring.
        sh(sandbox, "cd /home/user && { nohup python3 /home/user/stub_coordinator.py & } "
                    ">/dev/null 2>&1; sleep 2; "
                    f"curl -s -o /dev/null -w 'stub_http=%{{http_code}}\\n' "
                    f"http://127.0.0.1:{STUB_PORT}/ping", timeout=90)

        handle = sandbox.commands.run(
            "cd /home/user && FLASHNODE_STATE_DIR=/home/user/.flashnode "
            "FLASHNODE_WORKDIR=/home/user/work exec flashnode work --runner trusted "
            f"--coordinator {coord} --log-json --poll-seconds 2 "
            "> /home/user/agent.log 2>&1",
            background=True, timeout=0)
        ev.agent_pid = getattr(handle, "pid", None)
        print(f"agent started, pid={ev.agent_pid}")

        time.sleep(watch_s)
        state = parse_kv(sh(sandbox, f"""
echo alive=$( [ -d /proc/{ev.agent_pid} ] && echo ALIVE || echo DEAD )
echo hits=$(cat /home/user/stub_hits.json 2>/dev/null)
"""))
        ev.agent_alive_after_s = watch_s if state.get("alive") == "ALIVE" else None
        ev.stub_hits_before_pause = load_json(state.get("hits", "{}"))
        ev.registration = load_json(sh(sandbox, "cat /home/user/stub_registration.json 2>/dev/null"))
        ev.agent_registered = bool(ev.registration)
        ev.probes.append(Probe("agent_running", state.get("alive") == "ALIVE", watch_s * 1000,
                               f"registered={ev.agent_registered} hits={ev.stub_hits_before_pause}"))
        print(f"after {watch_s:.0f}s: {state.get('alive')} registered={ev.agent_registered} "
              f"hits={ev.stub_hits_before_pause}")

        # ---- 6. does the running agent survive hibernation? ---------------
        t0 = time.monotonic()
        sandbox.pause(keep_memory=True)
        ev.pause_ms = (time.monotonic() - t0) * 1000
        time.sleep(hibernate_s)
        t0 = time.monotonic()
        sandbox = Sandbox.connect(ev.sandbox_id, timeout=sandbox_timeout_s, **conn)
        ev.wake_ms = (time.monotonic() - t0) * 1000

        # Give it more than one poll interval to prove it is still WORKING,
        # not merely still present in the process table.
        time.sleep(8)
        after = parse_kv(sh(sandbox, f"""
echo alive=$( [ -d /proc/{ev.agent_pid} ] && echo ALIVE || echo DEAD )
echo hits=$(cat /home/user/stub_hits.json 2>/dev/null)
"""))
        ev.agent_survived_hibernation = after.get("alive") == "ALIVE"
        ev.stub_hits_after_wake = load_json(after.get("hits", "{}"))
        claims_before = ev.stub_hits_before_pause.get("/v1alpha1/leases/claim", 0)
        claims_after = ev.stub_hits_after_wake.get("/v1alpha1/leases/claim", 0)
        ev.agent_resumed_polling = claims_after > claims_before
        ev.probes.append(Probe("agent_survived_hibernation", ev.agent_survived_hibernation,
                               ev.wake_ms, f"claims {claims_before} -> {claims_after}"))
        print(f"pause={ev.pause_ms:.0f}ms wake={ev.wake_ms:.0f}ms "
              f"agent={'ALIVE' if ev.agent_survived_hibernation else 'DEAD'} "
              f"claims {claims_before} -> {claims_after}")
        return ev

    except Exception as exc:  # noqa: BLE001
        ev.error = redact(f"{type(exc).__name__}: {exc}")
        ev.probes.append(Probe("run", False, None, ev.error))
        print(f"ERROR: {ev.error}")
        return ev
    finally:
        if sandbox is not None:
            _, err = _try(sandbox.kill)
            ev.cleanup_verified = err == ""
            print(f"cleanup {'ok' if ev.cleanup_verified else 'FAILED: ' + err}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default="ap-southeast-1")
    ap.add_argument("--sandbox-timeout", type=int, default=900,
                    help="sandbox TTL in SECONDS (this SDK takes seconds, not ms)")
    ap.add_argument("--watch", type=float, default=25.0,
                    help="seconds to watch the agent before hibernating it")
    ap.add_argument("--hibernate", type=float, default=20.0)
    ap.add_argument("--evidence-dir",
                    default=str(Path(__file__).resolve().parents[2] / ".evidence"))
    args = ap.parse_args()

    ev = probe(args.region, args.sandbox_timeout, args.watch, args.hibernate)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.evidence_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"alibaba-flashnode-probe-{stamp}.json"
    out_path.write_text(redact(json.dumps(
        {"captured_at": stamp, "template": TEMPLATE, "flashnode_spec": FLASHNODE_SPEC,
         "upstream_index": UPSTREAM_INDEX, "evidence": asdict(ev)}, indent=2, default=str)))
    print(f"\nEvidence: {out_path}")

    go = (ev.install_ok and ev.agent_registered
          and ev.agent_alive_after_s is not None
          and ev.agent_survived_hibernation and ev.agent_resumed_polling)
    print("\nVERDICT:", "GO — flashnode runs inside an FC sandbox and survives hibernation"
          if go else "NOT PROVEN — see evidence")
    return 0 if go else 2


if __name__ == "__main__":
    sys.exit(main())
