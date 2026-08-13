#!/usr/bin/env python3
"""C-6.2: a task that attempts, and fails, to leave its sandbox.

The requirement's own words: *a task that attempts and fails to read outside
its workspace, reach a forbidden host, and see host processes.* Everything
else in this directory measures what a sandbox can DO (hibernate, install a
dependency, run FlashNode). This one measures what it must NOT be able to do.

--------------------------------------------------------------------------
THE CONTRACT — read this before touching `denied`

`Attempt.denied == True` means **isolation held** — the desired outcome, the
thing we want to be able to claim. It is deliberately the *positive* name for
a *negative* action (the sandbox tried to escape and failed), because the
alternative naming (`escaped: bool`) inverts the moment a maintainer skims
past a `not`.

The one rule that overrides every convenience in this file: **a leak is never
encoded as `denied=True`.** Where a probe's two sub-checks disagree (the read
is refused but the write succeeds, or the metadata host times out but the
generic host answers), the attempt as a whole is only `denied` when *every*
sub-check failed. Any single success by the escaping task is a leak, full
stop, and the loudest, most literal signal string this script can produce.

--------------------------------------------------------------------------
THE THREE PROBES

  1. filesystem_escape  read a path no task should be able to read
                        (`/etc/shadow`), then write a path above the
                        workspace (`/`). `denied` iff BOTH fail.
  2. forbidden_host      reach Alibaba's ECS metadata IP (100.100.100.200)
                        and a second, generic link-local metadata address
                        (169.254.169.254, the address the SSRF literature
                        treats as universally forbidden regardless of cloud).
                        `denied` iff BOTH are refused, time out, or answer
                        anything but 2xx. Timeout and refusal are recorded
                        as distinct signals — they are different proofs (an
                        egress rule dropping the packet vs. a closed port
                        answering it) and conflating them overclaims which
                        one actually held.
  3. host_processes      look for processes and an init outside the PID
                        namespace. This one is a judgment on the output, not
                        an exit code: `classify_host_processes` is the rule,
                        stated once, as a pure function, so it can be tested
                        without a sandbox and read without guessing what
                        "small" or "the sandbox's own init" means.

`ps aux` is run too, but only as supplementary evidence in the signal text —
not as the input to the classifier. `hibernation_modes_probe.py` documents
that this template ships no procps and `ps -p` exits 127 reporting every
process as gone; relying on `ps aux` output as the primary signal here would
risk the same false read. `/proc`'s own numeric entries and `/proc/1/cmdline`
are read directly and are what `classify_host_processes` actually judges.

--------------------------------------------------------------------------
Usage
-----
    export E2B_API_KEY="<created in the Function Compute console>"
    python isolation_probe.py --region ap-southeast-1

Evidence lands in ../../.evidence/ (gitignored) as redacted JSON, plus a
`.sandboxes` id file written the instant the sandbox exists — a `kill -9` of
this script still leaves a human able to clean up. The sandbox is killed in a
`finally`, and the run ends with a scoped sweep of only the ids it created.

Exit codes: 0 = all three attempts denied (GO); 2 = a leak was observed, or
the run could not confirm cleanup / hit an allowlist block (NO-GO); 1 = a
harness/credential problem (no E2B_API_KEY) — a missing key is not a verdict.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

TEMPLATE = "code-interpreter-v1"
DEFAULT_REGION = "ap-southeast-1"

# Substrings that mean "your account may not do this", as opposed to a plain
# transport failure. Used only to make the printed NO-GO message precise —
# it does not change the exit code, which is 2 either way.
ALLOWLIST_HINTS = (
    "allowlist", "allow list", "whitelist", "not enabled", "not authorized",
    "forbidden", "permission", "unsupported", "not available", "preview",
)

# What "host_processes" treats as evidence of a real host init, as opposed to
# the sandbox's own PID-1. Substring match against /proc/1/cmdline.
HOST_INIT_MARKERS = ("/sbin/init", "/usr/sbin/init", "/lib/systemd/systemd", "systemd")

#: Above this many /proc entries the process table looks like a shared host,
#: not one sandbox. A container running one evaluator process typically shows
#: a handful of PIDs (the init, the shell, the command, maybe a daemon); a
#: real host commonly runs hundreds. There is no exact boundary Alibaba
#: publishes, so this is picked wide enough that a sandbox's own process tree
#: never crosses it and a real host's does.
HOST_PROC_COUNT_LEAK_THRESHOLD = 50


# ---------------------------------------------------------------------------
# Shared plumbing. Copied VERBATIM from hibernation_modes_probe.py rather than
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


def sh(sandbox, command: str, timeout: float = 180) -> str:
    """Run a command and return its combined output whatever it exits.

    `commands.run` raises `CommandExitException` on a non-zero exit, and that
    exception IS the result (it subclasses CommandResult) — so a non-zero exit
    is data here, never a crash.
    """
    try:
        result = sandbox.commands.run(command, timeout=timeout)
        return (result.stdout or "") + (result.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return ((getattr(exc, "stdout", "") or "") + (getattr(exc, "stderr", "") or "")
                or f"{type(exc).__name__}: {exc}")


def parse_kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def as_int(text: str | None) -> int | None:
    try:
        return int(str(text))
    except (TypeError, ValueError):
        return None


def looks_like_allowlist(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in ALLOWLIST_HINTS)


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


# ---------------------------------------------------------------------------
# The three shell commands. Module constants so the unit test can parse them
# with `/bin/sh -n` and this script never has to guess which sub-check failed
# from a single combined exit code.
# ---------------------------------------------------------------------------

#: 1. filesystem_escape — read a path no task should see, then write above
#: the workspace. Both use a temp file so `sh()`'s combined stdout+stderr
#: capture cannot smear the read's content into the exit-code parsing below.
FS_ESCAPE_CMD = r'''
echo "---read---"
cat /etc/shadow >/tmp/fm_read_out 2>/tmp/fm_read_err
echo "EXIT_READ=$?"
head -c 200 /tmp/fm_read_err 2>/dev/null
rm -f /tmp/fm_read_out /tmp/fm_read_err

echo "---write---"
touch /flashml_escape_probe 2>/tmp/fm_write_err
echo "EXIT_WRITE=$?"
head -c 200 /tmp/fm_write_err 2>/dev/null
rm -f /flashml_escape_probe /tmp/fm_write_err
'''

#: 2. forbidden_host — the Alibaba ECS metadata IP (doc 3045181's own
#: taxonomy names this as a host surface a user task must never reach), plus
#: 169.254.169.254 as a second, cloud-agnostic link-local metadata address.
#: `-o /dev/null -w '%{http_code}'` separates "curl could not even make the
#: request" (timeout/refusal, http_code stays 000) from "curl got an answer"
#: (a real HTTP status, which must not be 2xx).
FORBIDDEN_HOST_CMD = r'''
echo "---metadata (Alibaba ECS)---"
HTTP_META=$(curl -sS --max-time 5 -o /dev/null -w '%{http_code}' \
  http://100.100.100.200/latest/meta-data/ 2>/tmp/fm_meta_err)
echo "EXIT_META=$?"
echo "HTTP_META=$HTTP_META"
head -c 200 /tmp/fm_meta_err 2>/dev/null
rm -f /tmp/fm_meta_err

echo "---generic link-local metadata---"
HTTP_GENERIC=$(curl -sS --max-time 5 -o /dev/null -w '%{http_code}' \
  http://169.254.169.254/latest/meta-data/ 2>/tmp/fm_generic_err)
echo "EXIT_GENERIC=$?"
echo "HTTP_GENERIC=$HTTP_GENERIC"
head -c 200 /tmp/fm_generic_err 2>/dev/null
rm -f /tmp/fm_generic_err
'''

#: 3. host_processes — `ps aux` is captured for the record ONLY; this
#: template is documented elsewhere (hibernation_modes_probe.py) as shipping
#: no procps, so `ps` output is not trusted as the classifier's input. The
#: PID namespace (`/proc`'s own numeric entries, `/proc/1/cmdline`) is.
HOST_PROC_CMD = r'''
echo "---ps (supplementary only)---"
PS_OUT=$(ps aux 2>&1)
echo "PS_EXIT=$?"
echo "PS_LINES=$(printf '%s\n' "$PS_OUT" | wc -l | tr -d ' ')"
printf '%s\n' "$PS_OUT" | tail -5

echo "---proc namespace (authoritative)---"
echo "PROC_COUNT=$(ls /proc 2>/dev/null | grep -c '^[0-9]')"
echo "PID1_CMD=$(cat /proc/1/cmdline 2>/dev/null | tr '\0' ' ')"
'''


def classify_host_processes(proc_count: int, pid1_cmd: str) -> bool:
    """True (denied — isolation held) iff the process table looks like ONE
    sandbox's own, not a shared host.

    Two independent signals, both must hold:
      * `proc_count` — the number of numeric entries under `/proc`, i.e. the
        PIDs visible in THIS pid namespace. A single evaluator sandbox shows
        a handful; a real host commonly shows hundreds.
      * `pid1_cmd` — `/proc/1/cmdline`. A sandbox's own init is not a real
        host init; seeing `/sbin/init`, `/usr/sbin/init`, or systemd at PID 1
        is exactly what a task would see if it could look through to the
        host.

    Written as an explicit rule, not an exit code, because the requirement
    itself calls this "a judgment on the output" — and because a leak here
    must be as visible in the code as it would be in the evidence: a large
    count OR a real host init both force `False`, never `True`.
    """
    cmd = (pid1_cmd or "").strip()
    host_init_visible = any(marker in cmd for marker in HOST_INIT_MARKERS)
    process_table_small = proc_count < HOST_PROC_COUNT_LEAK_THRESHOLD
    return process_table_small and not host_init_visible


def _classify_curl(http_code: str, exit_code: int | None, raw: str) -> tuple[bool, str]:
    """One curl attempt -> (denied, signal). `denied` iff the host was never
    reached with a 2xx. Timeout and refusal are named distinctly on purpose —
    see the module docstring."""
    lowered = raw.lower()
    code = (http_code or "000").strip()
    if code and code != "000":
        if code.startswith("2"):
            return False, f"HTTP {code} (LEAK — reached and answered 2xx)"
        return True, f"HTTP {code}"
    if exit_code == 28 or "timed out" in lowered or "timeout" in lowered:
        return True, "timeout"
    if exit_code == 7 or "connection refused" in lowered or "couldn't connect" in lowered:
        return True, "Connection refused"
    if exit_code is not None and exit_code != 0:
        return True, f"curl exit={exit_code}"
    return False, "reached with no error reported (LEAK)"


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Attempt:
    name: str            # "filesystem_escape" | "forbidden_host" | "host_processes"
    command: str         # the exact shell run
    denied: bool         # True == isolation held (the desired outcome)
    signal: str          # the observed proof, e.g. "Permission denied", "exit=1", "Connection refused"
    raw_tail: str        # last ~200 chars of output, redacted


@dataclass(frozen=True)
class IsolationReport:
    region: str
    api_url: str
    domain: str
    template: str
    sdk_version: str
    sandbox_id: str
    started_at: str
    finished_at: str
    attempts: list[Attempt]
    all_denied: bool     # AND over attempts[].denied
    killed: bool
    error: str | None


# ---------------------------------------------------------------------------
# The three attempts. Each runs one command, parses the result, and returns
# an Attempt — never raising: an attempt that could not be judged is still a
# leak (`denied=False`), never silently dropped and never "denied" by default.
# ---------------------------------------------------------------------------


def _run_filesystem_escape(sandbox) -> Attempt:
    out = sh(sandbox, FS_ESCAPE_CMD, timeout=60)
    kv = parse_kv(out)
    read_exit = as_int(kv.get("EXIT_READ"))
    write_exit = as_int(kv.get("EXIT_WRITE"))
    read_denied = read_exit is not None and read_exit != 0
    write_denied = write_exit is not None and write_exit != 0
    denied = read_denied and write_denied

    lowered = out.lower()
    if "permission denied" in lowered:
        proof = "Permission denied"
    elif denied:
        proof = f"read exit={read_exit}, write exit={write_exit}"
    else:
        proof = f"read exit={read_exit}, write exit={write_exit} (LEAK)"
    return Attempt(name="filesystem_escape", command=FS_ESCAPE_CMD, denied=denied,
                   signal=proof, raw_tail=redact(out[-200:]))


def _run_forbidden_host(sandbox) -> Attempt:
    out = sh(sandbox, FORBIDDEN_HOST_CMD, timeout=30)
    kv = parse_kv(out)
    meta_denied, meta_signal = _classify_curl(
        kv.get("HTTP_META", "000"), as_int(kv.get("EXIT_META")), out)
    generic_denied, generic_signal = _classify_curl(
        kv.get("HTTP_GENERIC", "000"), as_int(kv.get("EXIT_GENERIC")), out)
    denied = meta_denied and generic_denied
    signal = f"metadata: {meta_signal}; generic: {generic_signal}"
    return Attempt(name="forbidden_host", command=FORBIDDEN_HOST_CMD, denied=denied,
                   signal=signal, raw_tail=redact(out[-200:]))


def _run_host_processes(sandbox) -> Attempt:
    out = sh(sandbox, HOST_PROC_CMD, timeout=30)
    kv = parse_kv(out)
    proc_count = as_int(kv.get("PROC_COUNT"))
    pid1_cmd = kv.get("PID1_CMD", "")
    denied = proc_count is not None and classify_host_processes(proc_count, pid1_cmd)

    ps_exit = as_int(kv.get("PS_EXIT"))
    ps_note = "ps unavailable" if ps_exit not in (None, 0) else f"ps_lines={kv.get('PS_LINES', '?')}"
    proof = (
        f"/proc entries={proc_count} pid1={pid1_cmd!r} ({ps_note}) — "
        f"{'sandbox-only process table' if denied else 'LEAK — host processes visible'}"
    )
    return Attempt(name="host_processes", command=HOST_PROC_CMD, denied=denied,
                   signal=proof, raw_tail=redact(out[-200:]))


# ---------------------------------------------------------------------------
# One run: create -> three attempts -> kill in `finally`.
# ---------------------------------------------------------------------------


def run_probe(Sandbox, conn: dict, *, region: str, api_url: str, domain: str,
             sdk_version: str, sandbox_timeout_s: int, ids: Ids) -> IsolationReport:
    started_at = datetime.now(timezone.utc).isoformat()
    sandbox = None
    sandbox_id = ""
    attempts: list[Attempt] = []
    error: str | None = None
    killed = False
    try:
        sandbox = Sandbox.create(template=TEMPLATE, timeout=sandbox_timeout_s,
                                 metadata={"flashml_probe": "isolation"}, **conn)
        sandbox_id = getattr(sandbox, "sandbox_id", "")
        ids.add(sandbox_id, "isolation probe")

        for name, fn in (
            ("filesystem_escape", _run_filesystem_escape),
            ("forbidden_host", _run_forbidden_host),
            ("host_processes", _run_host_processes),
        ):
            attempt = fn(sandbox)
            attempts.append(attempt)
            flag = "DENIED (held)" if attempt.denied else "LEAK"
            print(f"    [{name}] {flag}: {attempt.signal}", flush=True)
    except Exception as exc:  # noqa: BLE001 - a failed harness step is evidence
        error = redact(f"{type(exc).__name__}: {exc}")
        print(f"  ERROR: {error}", flush=True)
    finally:
        if sandbox is not None:
            _, err = _try(sandbox.kill)
            killed = err == ""
            print(f"  sandbox {sandbox_id} {'killed' if killed else 'KILL FAILED: ' + err}",
                  flush=True)

    finished_at = datetime.now(timezone.utc).isoformat()
    all_denied = bool(attempts) and all(a.denied for a in attempts)
    return IsolationReport(
        region=region, api_url=api_url, domain=domain, template=TEMPLATE,
        sdk_version=sdk_version, sandbox_id=sandbox_id, started_at=started_at,
        finished_at=finished_at, attempts=attempts, all_denied=all_denied,
        killed=killed, error=error,
    )


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--sandbox-timeout", type=int, default=300,
                        help="sandbox TTL in SECONDS (this SDK takes seconds, not ms)")
    parser.add_argument("--evidence-dir",
                        default=str(Path(__file__).resolve().parents[2] / ".evidence"))
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.evidence_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ids = Ids(out_dir / f"alibaba-isolation-{stamp}.sandboxes")

    try:
        key = api_key()
    except RuntimeError as exc:  # missing key — a harness problem, not a verdict
        print(str(exc))
        return 1

    api_url, domain = endpoints(args.region)
    conn = {"api_key": key, "api_url": api_url, "domain": domain, "validate_api_key": False}

    from e2b_code_interpreter import Sandbox

    try:
        import importlib.metadata as md
        sdk_version = md.version("e2b")
    except Exception:  # noqa: BLE001
        sdk_version = "unknown"

    print(f"=== {args.region} · isolation probe (C-6.2) ===", flush=True)
    report = run_probe(Sandbox, conn, region=args.region, api_url=api_url, domain=domain,
                       sdk_version=sdk_version, sandbox_timeout_s=args.sandbox_timeout, ids=ids)

    # Scoped sweep of ids THIS run created. A blanket sweep of everything
    # listed would reach across into a probe running beside it.
    mine = set(ids.ids)
    listing, _err = _try(lambda: list(Sandbox.list(**conn).next_items()))
    for item in (listing or []):
        sid = getattr(item, "sandbox_id", "")
        if sid in mine:
            print(f"  sweeping leftover {sid}")
            _try(lambda i=sid: Sandbox.kill(i, **conn))
    remaining, _err2 = _try(lambda: list(Sandbox.list(**conn).next_items()))
    live_after = [getattr(item, "sandbox_id", "") for item in (remaining or [])
                 if getattr(item, "sandbox_id", "") in mine]

    out_path = out_dir / f"alibaba-isolation-{stamp}.json"
    out_path.write_text(redact(json.dumps(
        {"captured_at": stamp, "report": asdict(report)}, indent=2, default=str)))

    print("\n" + "=" * 64)
    for attempt in report.attempts:
        flag = "DENIED (isolation held)" if attempt.denied else "LEAK"
        print(f"  {attempt.name:<20}{flag:<26}{attempt.signal}")
    print(f"\n  all_denied : {report.all_denied}")
    print(f"  killed     : {report.killed}")
    print(f"  still live : {len(live_after)} {live_after}")
    print(f"\nEvidence: {out_path}")
    ids.report()

    if report.error:
        blocked = looks_like_allowlist(report.error)
        print(f"\n{'ALLOWLIST-BLOCKED' if blocked else 'HARNESS ERROR'}: {report.error}")
        return 2

    leaked = [a.name for a in report.attempts if not a.denied]
    clean = report.all_denied and report.killed and not live_after

    if clean:
        print("\nGO — isolation held on every attempt.")
        return 0

    if leaked:
        print(f"\nNO-GO — a leak was observed: {', '.join(leaked)}. "
              f"This is reported honestly, never as denied.")
    else:
        print("\nNO-GO — attempts were denied but cleanup could not be confirmed "
              f"(killed={report.killed}, still live={live_after}).")
    return 2


if __name__ == "__main__":
    sys.exit(main())
