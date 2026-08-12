"""Turn a bare FC Agent Sandbox into an enrolled, working FlashNode.

A sandbox arrives as a generic Debian box with Python and nothing else. This
module is the sequence that makes it a node of somebody's pool: install the
agent, write the evidence a wake can be checked against, hand it one
short-lived credential, start the worker, prove it registered, prove it is
not advertising an isolation guarantee it cannot honour, and take the
credential file away again.

Everything below was measured in ``ap-southeast-1`` on 2026-08-11 by
``scripts/competition/flashnode_in_sandbox_probe.py``, which is the reference
implementation for this file. Where a number appears it is that probe's.

**What the environment is, and the four things it will teach you the hard
way.** Debian 13, Python 3.13, 2 vCPU / 2 GB / 10 GB, user ``user`` (uid
1000, non-root, sudo present), ``/home/user`` writable.

1. **There is no Docker and none can be installed**, so the container tiers
   are unsatisfiable by construction and ``--runner trusted`` is the only
   viable one. That single fact drives the security note below.
2. **There is no ``ps``** — procps is not in the image, so ``ps -p $PID``
   exits 127 and the idiom ``ps -p $PID >/dev/null && echo ALIVE || echo
   GONE`` reports every process on earth as GONE. An earlier measurement
   concluded from exactly that that background processes do not survive
   hibernation; they do. Every liveness check here is ``[ -d /proc/$PID ]``.
3. **``/etc/pip.conf`` pins pip to ``https://mirrors.aliyun.com/pypi/simple/``
   and that mirror lags.** It served flashnode 0.3.5 when 0.4.0 was
   published, so a plain ``pip install flashnode==0.4.0`` fails with "no
   matching distribution", which reads like a broken package rather than a
   stale index. Every install here passes ``-i https://pypi.org/simple/``,
   and :func:`install_command` is the only place that is spelled — with the
   installed version read back afterwards, so a future install that loses the
   flag and silently resolves an older agent is caught rather than deployed.
4. **``commands.run("cd x && nohup daemon &")`` never returns.** The ``&``
   backgrounds the whole AND-list, bash runs that list in a subshell, and the
   subshell inherits the command's stdout pipe — so envd waits for an EOF
   that only arrives when the daemon dies, and the SDK's own ``timeout=``
   does not rescue it. The launch here uses ``background=True`` and ``exec``,
   the form the probe measured; ``exec`` also means the pid we are handed is
   the agent's own rather than a shell that is about to disappear.

**The security property this module is solely responsible for.** The
coordinator's placement gates are one-directional: the pool gate only runs
for a task whose payload names a pool, and a PUBLIC job compiles to
``placement.pool = "any"``, which expands to a payload with no ``pool`` key
at all. Binding this sandbox's machine to a dedicated single-member pool
therefore keeps other teams' pool-scoped work off it and keeps this session's
evaluation on it — but it does **not** keep public work off it. What does
that is the isolation-tier gate underneath: a public job is ``tier:
sandboxed, allowFallback: false`` and is placeable only on a node whose
registration says ``sandbox_capable`` is true.

That stamp comes from the agent's own registration payload and from nowhere
else. ``machines.sandbox_capable`` in our database is a display-only
snapshot with no authority over placement. **So a sandbox that advertises
sandbox capability it cannot provide would be handed public jobs to run
unsandboxed, and only how this module launches the agent can prevent it.**
Hence two belts: ``FLASHNODE_SANDBOX_CAPABLE=false`` is set *explicitly*
rather than left unset — a template change or an inherited environment must
not be able to flip it — and after registration
:func:`bootstrap_worker` reads the capability payload back and refuses the
whole bootstrap if it is not false. Killing the worker and failing is
strictly better than a node that lies about its isolation. A correct
registration was measured as ``unsandboxed_argv_capable: true``,
``can_install_dependencies: true``, ``module_capable: false``,
``sandbox_capable: false``.

**The token reaches the sandbox and nowhere else.** It is written with
:meth:`SandboxGateway.write_file` — never interpolated into a command string,
which would put it in ``CommandEvidence.command`` and from there into the
event ledger — and the file is deleted as soon as registration proves the
agent has read it. The agent holds it in memory from then on, across the
hibernation included, so the file has no further job and a file with no job
is a file that should not exist. Every string this module returns, logs or
raises goes through :func:`_scrub`.

**The wake path never re-bootstraps.** :func:`verify_worker` re-reads the
marker, compares the hash it was given, checks ``[ -d /proc/$PID ]`` and
looks for evidence the worker is still claiming. A running ``flashnode work``
was measured serving lease claims straight across a hibernation (13 → 19
claims, 5/5 runs), so restarting it on wake would throw away a working worker
and re-register a node the coordinator already has.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shlex
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from .alibaba_sandbox import (
    CommandEvidence,
    SandboxError,
    SandboxGateway,
    SandboxTerminalError,
    SandboxTransportError,
)
from .sandbox_sessions import Observation

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Policy: versions, paths, timeouts. Everything a caller might reasonably
# need to move is a parameter with a default here, so the numbers are
# readable in one place rather than spread through the sequence.
# ---------------------------------------------------------------------------

#: The agent version installed into a sandbox. Kept equal to ``NODE_VERSION``
#: in the repo Makefile, which is what ``e2e`` installs — a sandbox running a
#: different agent from the one the suite proves is a fleet nobody tested.
DEFAULT_FLASHNODE_VERSION = "0.4.0"

#: Real PyPI, passed on every install. See point 3 in the module docstring.
#: The Aliyun mirror in ``/etc/pip.conf`` is not merely slower — it serves a
#: version set that cannot satisfy a current pin at all.
UPSTREAM_INDEX = "https://pypi.org/simple/"

#: Bumped whenever what this module *does* to a sandbox changes: a different
#: agent version, a different launch, a different directory layout. It goes
#: into the marker, so a sandbox prepared by an older controller and woken by
#: a newer one produces a hash mismatch instead of a plausible-looking node
#: running a setup nobody deployed.
TEMPLATE_VERSION = "1"

#: The only viable tier — see point 1.
RUNNER = "trusted"

#: How often the agent asks for work. Two seconds rather than the CLI default
#: of one: the evidence window in :func:`verify_worker` has to be longer than
#: this to see any activity at all, and a longer window costs a wake more
#: billed seconds than a slightly slower first claim costs the demo.
POLL_SECONDS = 2.0


@dataclass(frozen=True)
class SandboxPaths:
    """Where everything this module writes lives.

    The marker and the receipt deliberately sit OUTSIDE the agent's state
    directory. ``flashnode env purge`` and ``flashnode data purge`` promise
    only that they will not touch ``node-id`` and ``credentials.json``; our
    continuity evidence is not on that list, and a hash mismatch caused by
    the agent tidying its own cache would read as a compromised sandbox.
    """

    home: str = "/home/user"
    state_dir: str = "/home/user/.flashnode"
    workdir: str = "/home/user/work"
    control_dir: str = "/home/user/.flashml"
    credentials: str = "/home/user/.flashnode/credentials.json"
    node_id_file: str = "/home/user/.flashnode/node-id"
    marker: str = "/home/user/.flashml/prepared.json"
    receipt: str = "/home/user/.flashml/bootstrap.json"
    agent_log: str = "/home/user/.flashml/flashnode-work.log"


DEFAULT_PATHS = SandboxPaths()

#: Ceilings. Every one of them exists so a hung sandbox cannot hang the
#: controller: `alibaba_sandbox.run` takes the command's own timeout and adds
#: the control-plane one on top, so these are what the sandbox gets, not what
#: the caller waits.
INSTALL_TIMEOUT_S = 300
PROBE_TIMEOUT_S = 60
#: The install measured 1.7 s and 21 MB into ``~/.local``; 300 s is not a
#: budget, it is the point at which we stop believing the mirror.

#: How long registration may take before we give up on it. `flashnode work`
#: registers within a second of starting on a reachable coordinator, and
#: crashes in ~4 s against an unreachable one, so 60 s is generous by an
#: order of magnitude — it is sized for a coordinator that is slow, not one
#: that is absent.
REGISTER_TIMEOUT_S = 60.0

#: A worker that is going to die of a failed registration dies in ~4 s
#: (measured: exit 1 with an unhandled ``CoordinatorUnreachable`` out of
#: ``client.register()``, which is the one unguarded call in the CLI). We
#: therefore do not believe "it is alive" until it has been alive for longer
#: than that.
REGISTER_SETTLE_S = 6.0

#: Between polls of the sandbox while waiting for registration.
POLL_INTERVAL_S = 1.5

#: Bounded restarts. Registration is the only unguarded call in `flashnode
#: work` — the executor loop below it backs off from an unreachable
#: coordinator perfectly well — so a network blip during those first four
#: seconds is the one failure that silently leaves a sandbox with no worker.
#: Two attempts, each preceded by its own reachability check.
LAUNCH_ATTEMPTS = 2

#: How long :func:`verify_worker` watches for evidence of work. Must exceed
#: :data:`POLL_SECONDS` or a healthy idle worker looks stalled.
CLAIM_WINDOW_S = 5.0

#: Lines of agent log carried into an observation or an error message. Enough
#: to see the traceback that killed it, few enough not to put a training log
#: into Postgres.
LOG_TAIL_LINES = 12

#: Strings in the agent log that mean the worker will not recover on its own.
#: ``registration failed`` is the CLI's own message for a coordinator that
#: answered non-200; ``CoordinatorUnreachable`` is the one that answered
#: nothing; ``cannot run tasks`` is the startup health gate refusing the
#: tier. All three exit the process.
_FATAL_LOG_MARKERS = (
    "Traceback",
    "CoordinatorUnreachable",
    "registration failed",
    "cannot run tasks",
)

#: The executor loop logs this once per failed claim and nothing at all for a
#: successful one, so its ABSENCE is the evidence that claiming works. That
#: asymmetry is why the checks below count it rather than looking for a
#: success line: there is no success line to look for.
_UNREACHABLE_LOG_MARKER = "coordinator unreachable"

_TAIL_SENTINEL = "---flashml-log-tail---"


# ---------------------------------------------------------------------------
# The credential, structurally
# ---------------------------------------------------------------------------


@runtime_checkable
class MachineCredential(Protocol):
    """What ``sandbox_identity.EphemeralMachineCredential`` looks like.

    Taken structurally, and deliberately not imported. This module has no
    database, mints nothing and revokes nothing; it is handed a credential
    and puts it in a sandbox. Importing the provisioner to name its type
    would couple the bootstrap sequence to psycopg and to whatever that
    module's constructor requires, for a type annotation.
    """

    machine_id: str
    node_id: str
    raw_token: str


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BootstrapResult:
    """What one bootstrap produced, and what a wake will be checked against.

    Contains no token and nothing derived from one. ``marker_sha256`` is the
    hash of the exact bytes written to the sandbox: it is the value
    :func:`verify_worker` compares on the other side of a hibernation, and
    the value the session row stores.

    ``install_ms`` and ``register_ms`` are measured, never derived.
    ``register_ms`` is the wall time from launching the worker to the
    observation that confirmed it — see :func:`_await_registration` for what
    that observation actually is and what it is not.
    """

    sandbox_id: str
    node_id: str
    marker_sha256: str
    flashnode_version: str
    install_ms: float
    register_ms: float
    worker_pid: int
    #: True when a healthy worker was already there and nothing was installed.
    reused: bool = False
    #: Emitted in order, for `sandbox_sessions.append_event`. Also delivered
    #: live through `on_observation`, which is the copy that survives a
    #: failure part-way through.
    observations: tuple[Observation, ...] = ()


@dataclass(frozen=True)
class WorkerHealth:
    """What the wake path saw. Never a claim that anything was repaired.

    ``healthy`` is the conjunction of the four checks below, and each of them
    is reported separately because they fail for different reasons and a
    caller has to be able to tell "this is not the sandbox we prepared"
    (marker mismatch) from "the worker died" (pid gone) from "the worker is
    there and cannot reach the coordinator" (not claiming).
    """

    sandbox_id: str
    healthy: bool
    marker_matches: bool
    marker_sha256: str
    pid_alive: bool
    claiming: bool
    credential_absent: bool
    checked_ms: float
    detail: str = ""
    observations: tuple[Observation, ...] = ()


# ---------------------------------------------------------------------------
# Documents and commands. Pure functions — no gateway, no clock — so a test
# can assert on the exact bytes and the exact command line.
# ---------------------------------------------------------------------------


def normalise_coordinator(url: str) -> str:
    """The key the agent's credential store will look this token up under.

    ``flashnode.identity.credentials`` normalises by stripping trailing
    slashes and nothing else. A file keyed by ``https://api.example/`` when
    the agent asks for ``https://api.example`` is a file that exists, parses,
    and yields no token — and the agent then dies on a bare 401 running the
    exact command we gave it.
    """
    return (url or "").strip().rstrip("/")


def marker_document(*, nonce: str, node_id: str, flashnode_version: str,
                    template_version: str = TEMPLATE_VERSION) -> dict[str, str]:
    """The prepared-marker payload.

    Deliberately has no timestamp and no random field of its own. The hash of
    this document is the continuity evidence, and a document that hashes
    differently every time it is written cannot be re-derived from what the
    session row stored — which is exactly what somebody investigating a
    mismatch needs to do. The only unpredictable part is ``nonce``, which the
    caller supplies and keeps.
    """
    return {
        "template_version": str(template_version),
        "nonce": str(nonce),
        "flashnode_version": str(flashnode_version),
        "node_id": str(node_id),
        "runner": RUNNER,
    }


def marker_bytes(document: dict[str, str]) -> bytes:
    """Canonical bytes for a marker. Sorted keys, one trailing newline, so
    the hash depends on the values and not on dict ordering."""
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def credential_bytes(coordinator_url: str, raw_token: str) -> bytes:
    """The agent's credential file: JSON keyed by normalised coordinator URL.

    Matches ``flashnode.identity.credentials._write_all`` byte-for-byte in
    shape (``indent=2, sort_keys=True``) so that anything the agent later
    writes to this file — a ``flashnode login`` against a second coordinator,
    say — leaves a file of the same form.

    These bytes are the secret. They go to :meth:`SandboxGateway.write_file`
    and nowhere else: never a command string, never a log, never an
    observation.
    """
    return json.dumps(
        {normalise_coordinator(coordinator_url): raw_token}, indent=2, sort_keys=True
    ).encode()


def install_command(version: str, *, index_url: str = UPSTREAM_INDEX) -> str:
    """The one place ``-i <real PyPI>`` is spelled.

    Without it this resolves against the Aliyun mirror in ``/etc/pip.conf``,
    which lags PyPI by whole releases: measured serving flashnode 0.3.5 while
    0.4.0 was current, so the install fails outright with "No matching
    distribution found", naming the package rather than the index. Not piped
    into ``tail`` — a pipeline reports the exit status of ``tail``, and the
    exit status is the whole point.
    """
    return (
        "python3 -m pip install --no-input --disable-pip-version-check "
        f"-i {shlex.quote(index_url)} {shlex.quote(f'flashnode=={version}')}"
    )


def _env_assignments(
    *, coordinator_url: str, pool_id: str, paths: SandboxPaths
) -> list[str]:
    """The environment both the launch and the capability read-back use.

    One list, two consumers, on purpose. The read-back's whole value is that
    it reproduces the payload the agent registered, and it can only do that
    if it ran under the same environment — two separately-maintained env
    strings would make it an assertion about a different process.

    ``FLASHNODE_SANDBOX_CAPABLE=false`` is set explicitly rather than left
    unset. ``capabilities.discover`` reads it as ``== "true"``, so unset
    already means false — but unset is a value someone else can supply, and
    the one thing this environment must never inherit is a true here. See the
    module docstring for what it would cost.
    """
    return [
        f"FLASHNODE_CREDENTIALS={shlex.quote(paths.credentials)}",
        f"FLASHNODE_STATE_DIR={shlex.quote(paths.state_dir)}",
        f"FLASHNODE_WORKDIR={shlex.quote(paths.workdir)}",
        f"FLASHNODE_COORDINATOR_URL={shlex.quote(normalise_coordinator(coordinator_url))}",
        f"FLASHNODE_POOL={shlex.quote(str(pool_id))}",
        f"FLASHNODE_RUNNER={RUNNER}",
        "FLASHNODE_SANDBOX_CAPABLE=false",
        # pip installs into ~/.local for a non-root user; the console script
        # lands in ~/.local/bin. Prepending is harmless when it is already
        # there and is the difference between a launch and a "command not
        # found" when a template changes its default PATH.
        f"PATH={shlex.quote(paths.home)}/.local/bin:$PATH",
    ]


def launch_command(
    *, coordinator_url: str, pool_id: str, paths: SandboxPaths,
    poll_seconds: float = POLL_SECONDS,
) -> str:
    """The measured launch form. Do not simplify it.

    ``exec`` so the pid handed back by ``background=True`` is the agent's own
    and stays valid for the life of the worker — including across a
    hibernation, where it is the only handle we have on it. ``> log 2>&1`` so
    both the trusted-runner banner (stdout) and the JSON log (stderr) land in
    one file, which is the only thing the health probes can read. ``--log-json``
    because the alternative on a TTY is a cursor-driven status view, and this
    is not a TTY.

    Truncating rather than appending is deliberate: on a relaunch the
    previous attempt's traceback must not be re-read as this attempt's. The
    tail of the failed attempt is captured before the relaunch.
    """
    env = " ".join(_env_assignments(
        coordinator_url=coordinator_url, pool_id=pool_id, paths=paths))
    return (
        f"cd {shlex.quote(paths.home)} && {env} exec flashnode work "
        f"--runner {RUNNER} "
        f"--coordinator {shlex.quote(normalise_coordinator(coordinator_url))} "
        f"--log-json --poll-seconds {poll_seconds:g} "
        f"> {shlex.quote(paths.agent_log)} 2>&1"
    )


def capability_command(
    *, node_id: str, coordinator_url: str, pool_id: str, paths: SandboxPaths
) -> str:
    """Re-derive the registration payload the worker sent.

    This calls ``flashnode.inventory.capabilities.discover`` with the same
    arguments ``flashnode work --runner trusted`` calls it with, under the
    same environment as the launch. It is the closest thing to a read-back
    that exists on this side: the coordinator holds the payload, our
    ``machines`` row holds a display-only copy with no authority over
    placement, and the agent does not write it to disk.

    What it proves, precisely: on this host, under this environment, an agent
    started this way registers ``sandbox_capable: false``. What it does not
    prove: that the bytes on the wire were these. Those two differ only if
    the environment differs, which is why :func:`_env_assignments` is shared
    rather than duplicated.
    """
    env = " ".join(_env_assignments(
        coordinator_url=coordinator_url, pool_id=pool_id, paths=paths))
    return (
        f"{env} python3 - <<'FLASHML_PY'\n"
        "import json\n"
        "from flashnode.inventory.capabilities import discover\n"
        f"r = discover({node_id!r}, kubernetes_node='', node_meta=None,\n"
        "             argv_capable=False, module_capable=False,\n"
        "             unsandboxed_argv_capable=True,\n"
        "             can_install_dependencies=True)\n"
        "print(json.dumps(json.loads(r.model_dump_json())))\n"
        "FLASHML_PY"
    )


def prepare_command(paths: SandboxPaths) -> str:
    """Create the three directories everything else assumes.

    ``files.write`` is documented to create parents, but the credential write
    is not the place to discover it does not: a failure there is a failure
    holding a token.
    """
    return (
        f"mkdir -p {shlex.quote(paths.state_dir)} "
        f"{shlex.quote(paths.control_dir)} {shlex.quote(paths.workdir)} && "
        f"chmod 700 {shlex.quote(paths.state_dir)} {shlex.quote(paths.control_dir)}"
    )


def probe_command(
    *, pid: int | None, paths: SandboxPaths, window_s: float = 0.0
) -> str:
    """One round trip that answers every health question at once.

    Liveness is ``[ -d /proc/$PID ]`` and never ``ps`` — there is no ``ps``
    (module docstring, point 2).

    With ``window_s > 0`` the probe samples twice around a sleep INSIDE the
    sandbox, which is what makes "is it still claiming" answerable in one
    call rather than two round trips and a controller-side wait. The evidence
    is byte counters from ``/proc/<pid>/io``, falling back to scheduler ticks
    from ``/proc/<pid>/stat`` when the kernel was built without task IO
    accounting: an idle-but-working agent posts a claim every
    :data:`POLL_SECONDS`, so its counters move; a wedged one's do not. The
    unreachable-warning count is sampled at both ends too, so "moving" and
    "moving because it is failing" are distinguishable.

    ``grep -c`` exits 1 on zero matches, which would abort the ``&&`` chain
    the caller is not writing; every count is therefore ``|| true``-guarded
    and an empty value is read as zero.
    """
    log_path = shlex.quote(paths.agent_log)
    marker = shlex.quote(paths.marker)
    cred = shlex.quote(paths.credentials)
    fatal_re = "|".join(_FATAL_LOG_MARKERS)
    pid_expr = str(int(pid)) if pid is not None else ""

    lines = [
        f"P={shlex.quote(pid_expr)}",
        # /proc/<pid>/io needs the same uid; it is the agent's own user here.
        'io() { [ -n "$P" ] && awk \'/^wchar/{print $2}\' /proc/$P/io 2>/dev/null'
        " || true; }",
        'cpu() { [ -n "$P" ] && awk \'{print $14+$15}\' /proc/$P/stat 2>/dev/null'
        " || true; }",
        f"unreach() {{ grep -c {shlex.quote(_UNREACHABLE_LOG_MARKER)} {log_path}"
        " 2>/dev/null || true; }",
        'echo "io_before=$(io)"',
        'echo "cpu_before=$(cpu)"',
        'echo "unreachable_before=$(unreach)"',
    ]
    if window_s > 0:
        lines.append(f"sleep {window_s:g}")
    lines += [
        'echo "alive=$( [ -n "$P" ] && [ -d /proc/$P ] && echo yes || echo no )"',
        'echo "io_after=$(io)"',
        'echo "cpu_after=$(cpu)"',
        'echo "unreachable_after=$(unreach)"',
        f'echo "marker_sha256=$(sha256sum {marker} 2>/dev/null | cut -d" " -f1)"',
        f'echo "credential_present=$( [ -e {cred} ] && echo yes || echo no )"',
        f'echo "log_bytes=$(stat -c %s {log_path} 2>/dev/null || echo 0)"',
        f'echo "fatal=$(grep -c -E {shlex.quote(fatal_re)} {log_path} 2>/dev/null'
        " || true)\"",
        f'echo "{_TAIL_SENTINEL}"',
        f"tail -n {LOG_TAIL_LINES} {log_path} 2>/dev/null || true",
    ]
    return "\n".join(lines)


def reachability_command(coordinator_url: str, *, timeout_s: int = 10) -> str:
    """Is the coordinator routable from inside the sandbox?

    Any HTTP status counts as reachable — a 401 or a 404 is the coordinator
    answering, which is all this question asks. ``000`` is curl's code for
    "no response", and that is the one that must stop us launching: a worker
    started against an unreachable coordinator exits 1 in about four seconds
    with an unhandled ``CoordinatorUnreachable`` out of ``client.register()``,
    the single unguarded call in the CLI.
    """
    url = shlex.quote(normalise_coordinator(coordinator_url) + "/healthz")
    return (
        "echo \"coordinator_http=$(curl -s -o /dev/null -w '%{http_code}' "
        f"--max-time {int(timeout_s)} {url} || echo 000)\""
    )


# ---------------------------------------------------------------------------
# Parsing and scrubbing
# ---------------------------------------------------------------------------


def _scrub(text: object, *secrets: str) -> str:
    """`text` with every supplied secret removed, unconditionally.

    Belt to the ledger's braces. ``sandbox_sessions.redact`` already strips
    anything shaped like a credential from what is stored, but an exception
    that escapes this module reaches a log handler and a traceback before it
    reaches any of that, and a token in a traceback is a token in a bug
    report.
    """
    out = str(text)
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        out = out.replace(secret, "[redacted]")
    return out


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _last_line(text: str | None) -> str:
    """The last non-empty line of a command's output.

    Commands here print one value, but pip and the Python launcher are both
    entitled to print a warning first — and an empty output must return ""
    rather than raise, because "the command produced nothing" is one of the
    outcomes every caller of this has to handle.
    """
    lines = [line.strip() for line in str(text or "").strip().splitlines()]
    lines = [line for line in lines if line]
    return lines[-1] if lines else ""


@dataclass(frozen=True)
class _ProbeReading:
    """One parsed probe. Every field is what the sandbox said, not what we
    hoped: ``alive`` is False when the key is missing, because an answer we
    could not read is not an answer that says yes."""

    alive: bool = False
    marker_sha256: str = ""
    credential_present: bool = True
    log_bytes: int = 0
    fatal: int = 0
    unreachable_before: int = 0
    unreachable_after: int = 0
    io_before: str = ""
    io_after: str = ""
    cpu_before: str = ""
    cpu_after: str = ""
    log_tail: str = ""

    @property
    def new_unreachable(self) -> int:
        return max(0, self.unreachable_after - self.unreachable_before)

    @property
    def counters_moved(self) -> bool | None:
        """True/False when a counter could be read, None when none could.

        None is not False. A kernel without task IO accounting and a
        ``/proc/<pid>/stat`` we could not parse mean we have no evidence
        either way, and reporting that as "not working" would quarantine a
        healthy worker on the strength of a missing file.
        """
        for before, after in ((self.io_before, self.io_after),
                              (self.cpu_before, self.cpu_after)):
            if before.strip() and after.strip():
                try:
                    return int(after) > int(before)
                except ValueError:
                    continue
        return None


def _parse_probe(stdout: str) -> _ProbeReading:
    body, _, tail = stdout.partition(_TAIL_SENTINEL)
    values: dict[str, str] = {}
    for line in body.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    return _ProbeReading(
        alive=values.get("alive") == "yes",
        marker_sha256=values.get("marker_sha256", ""),
        credential_present=values.get("credential_present", "yes") == "yes",
        log_bytes=_as_int(values.get("log_bytes")),
        fatal=_as_int(values.get("fatal")),
        unreachable_before=_as_int(values.get("unreachable_before")),
        unreachable_after=_as_int(values.get("unreachable_after")),
        io_before=values.get("io_before", ""),
        io_after=values.get("io_after", ""),
        cpu_before=values.get("cpu_before", ""),
        cpu_after=values.get("cpu_after", ""),
        log_tail=tail.strip(),
    )


def _kv(stdout: str, key: str) -> str:
    for line in stdout.splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == key:
            return value.strip()
    return ""


# ---------------------------------------------------------------------------
# Observation plumbing
# ---------------------------------------------------------------------------

ObservationSink = Callable[[Observation], Any]


class _Recorder:
    """Collects observations and hands each one to the caller's sink as it
    happens.

    Live delivery is the point: a bootstrap that fails at registration must
    leave the install and the marker in the ledger, and a tuple returned by a
    call that raised is a tuple nobody receives.

    A sink that raises is logged and ignored. The alternative — letting it
    propagate — turns a ledger hiccup into an exception on a path that has
    already put a worker in a sandbox, and the caller would then not know
    whether to clean up. Recording is best effort; the sandbox is not.
    """

    def __init__(self, sink: ObservationSink | None, *secrets: str) -> None:
        self.sink = sink
        self._secrets = tuple(s for s in secrets if s)
        self.observations: list[Observation] = []

    def emit(
        self,
        type_: str,
        *,
        latency_ms: float | None = None,
        source: str = "controller",
        **data: Any,
    ) -> Observation:
        observation = Observation(
            type=type_,
            source=source,
            latency_ms=None if latency_ms is None else max(0.0, float(latency_ms)),
            data={k: self._clean(v) for k, v in data.items() if v is not None},
        )
        self.observations.append(observation)
        if self.sink is not None:
            try:
                self.sink(observation)
            except Exception:  # noqa: BLE001 - a ledger failure is not a bootstrap failure
                log.warning(
                    "sandbox bootstrap: could not record observation %s", type_,
                    exc_info=True,
                )
        return observation

    def _clean(self, value: Any) -> Any:
        if isinstance(value, str):
            return _scrub(value, *self._secrets)
        if isinstance(value, (list, tuple)):
            return [self._clean(v) for v in value]
        if isinstance(value, dict):
            return {k: self._clean(v) for k, v in value.items()}
        return value

    def frozen(self) -> tuple[Observation, ...]:
        return tuple(self.observations)


# ---------------------------------------------------------------------------
# The sequence
# ---------------------------------------------------------------------------


async def bootstrap_worker(
    gateway: SandboxGateway,
    sandbox_id: str,
    *,
    credential: MachineCredential,
    coordinator_url: str,
    pool_id: str,
    marker_nonce: str,
    flashnode_version: str = DEFAULT_FLASHNODE_VERSION,
    template_version: str = TEMPLATE_VERSION,
    paths: SandboxPaths = DEFAULT_PATHS,
    poll_seconds: float = POLL_SECONDS,
    register_timeout_s: float = REGISTER_TIMEOUT_S,
    register_settle_s: float = REGISTER_SETTLE_S,
    poll_interval_s: float = POLL_INTERVAL_S,
    launch_attempts: int = LAUNCH_ATTEMPTS,
    claim_window_s: float = CLAIM_WINDOW_S,
    on_observation: ObservationSink | None = None,
) -> BootstrapResult:
    """Make this sandbox an enrolled, working FlashNode. Idempotent.

    In order, and the order matters:

    0. **Is one already here?** A receipt from an earlier bootstrap is read
       and, if it names a marker and a pid that still check out, this returns
       that result untouched. Installing twice is cheap; registering a second
       worker against the same node id is not — two processes claiming leases
       as one node is a node that fails half of them.
    1. **Install** the pinned agent from real PyPI, then read the installed
       version back. The read-back is what catches a stale mirror quietly
       satisfying the requirement with an older build.
    2. **Marker**, whose hash is returned and is the only thing a wake has to
       compare against.
    3. **Credential**, at 0600, with the mode verified rather than assumed —
       ``write_file`` sets it with a second ``chmod`` because the SDK has no
       mode parameter, so there is a real round trip in which it could fail.
    4. **Launch**, preceded by a reachability check and backed by bounded
       restarts, because ``client.register()`` is unguarded and a blip in the
       first four seconds otherwise leaves a sandbox with no worker and
       nothing saying so.
    5. **Wait for registration**, bounded.
    6. **Delete the credential file.** The agent has it in memory by now —
       that is precisely what step 5 established — and a file that no longer
       needs to exist should not.
    7. **Read the capability payload back** and refuse everything if
       ``sandbox_capable`` is not false. See the module docstring: this is
       the only place that property can be enforced.
    8. **Write the receipt**, last, because it is what a later call reads to
       decide a healthy worker is already here.

    Raises the typed errors from ``alibaba_sandbox``:
    :class:`SandboxTerminalError` for anything a retry cannot fix (a bad
    install, a worker that will not register, a capability violation) and
    :class:`SandboxTransportError` for a coordinator that was unreachable or
    a registration that ran out of time — the latter with
    ``may_have_applied`` set, because a worker may be running in there
    regardless of what we managed to observe.

    **Any failure after the launch kills the worker before raising**, because
    the receipt that makes step 0 work is written last: a failed bootstrap
    leaves nothing for a later call to recognise, so a live worker left
    behind would be joined by a second one under the same node id. The one
    exception is the registration timeout, which says so in its message —
    there the worker may be recovering on its own backoff, and the caller has
    the sandbox id and the choice. The caller also still owns the two things
    this module cannot do: revoke the machine credential and kill the
    sandbox.
    """
    token = getattr(credential, "raw_token", "")
    node_id = getattr(credential, "node_id", "")
    if not node_id:
        raise SandboxTerminalError(
            "the credential carries no node_id, so the worker would register "
            "under an identity this deployment has never issued",
            operation="bootstrap", sandbox_id=sandbox_id,
        )
    recorder = _Recorder(on_observation, token)

    document = marker_document(
        nonce=marker_nonce, node_id=node_id, flashnode_version=flashnode_version,
        template_version=template_version,
    )
    payload = marker_bytes(document)
    marker_sha256 = sha256_hex(payload)

    # -- 0. already bootstrapped? -------------------------------------------
    existing = await _existing_result(
        gateway, sandbox_id, paths=paths, recorder=recorder,
        claim_window_s=claim_window_s,
    )
    if existing is not None:
        return existing

    # -- 1. install ----------------------------------------------------------
    install_ms = await _install(
        gateway, sandbox_id, version=flashnode_version, recorder=recorder,
    )

    # -- 2. directories, node identity, marker -------------------------------
    await _run_checked(
        gateway, sandbox_id, prepare_command(paths),
        timeout_s=PROBE_TIMEOUT_S, operation="bootstrap.prepare",
        secrets=(token,),
    )
    # Seeded rather than left to the agent: `load_or_create_node_id` invents a
    # `fn-<random>` on first run, and a worker registering under an id the
    # control plane never issued is a machine row nobody can match to a
    # session.
    await _write_file(
        gateway, sandbox_id, paths.node_id_file, (node_id + "\n").encode(),
        mode=0o600, operation="bootstrap.node_id", secrets=(token,),
    )
    await _write_marker(
        gateway, sandbox_id, payload=payload, expected=marker_sha256,
        paths=paths, recorder=recorder, secrets=(token,),
    )

    # -- 3. credential -------------------------------------------------------
    await _write_credential(
        gateway, sandbox_id, coordinator_url=coordinator_url, token=token,
        paths=paths, recorder=recorder,
    )

    # -- 4/5. launch and wait for registration -------------------------------
    worker_pid, register_ms = await _launch_and_register(
        gateway, sandbox_id, coordinator_url=coordinator_url, pool_id=pool_id,
        paths=paths, poll_seconds=poll_seconds, recorder=recorder,
        register_timeout_s=register_timeout_s, register_settle_s=register_settle_s,
        poll_interval_s=poll_interval_s, launch_attempts=launch_attempts,
        secrets=(token,),
    )

    # Everything from here on happens with a live worker in the sandbox, and
    # a failure that left it running would be double-launched by the next
    # attempt: there is no receipt yet, so the idempotency check at the top
    # cannot see it. Two processes claiming leases under one node id fail
    # half of them. So any failure past this point stops the worker first.
    try:
        # -- 6. take the credential away ---------------------------------
        # Before the capability probe, not after: registration is already the
        # evidence that the agent read the token, the probe does not need the
        # file, and every extra step it stays on disk is a step it did not
        # need to.
        await _delete_credential(
            gateway, sandbox_id, paths=paths, recorder=recorder, secrets=(token,),
        )

        # -- 7. the capability the placement gate trusts ------------------
        await _assert_not_sandbox_capable(
            gateway, sandbox_id, node_id=node_id, coordinator_url=coordinator_url,
            pool_id=pool_id, paths=paths, recorder=recorder, worker_pid=worker_pid,
            secrets=(token,),
        )

        # -- 8. the receipt, which is what makes this idempotent ----------
        # Written last on purpose. It says "a complete, verified bootstrap
        # happened here", and a receipt written before the capability check
        # would let a later call reuse a worker this one refused.
        await _write_file(
            gateway, sandbox_id, paths.receipt,
            _receipt_bytes(BootstrapResult(
                sandbox_id=sandbox_id, node_id=node_id,
                marker_sha256=marker_sha256, flashnode_version=flashnode_version,
                install_ms=install_ms, register_ms=register_ms,
                worker_pid=worker_pid,
            )),
            mode=0o600, operation="bootstrap.receipt", secrets=(token,),
        )
    except SandboxError:
        await _kill_worker(gateway, sandbox_id, pid=worker_pid)
        raise

    return BootstrapResult(
        sandbox_id=sandbox_id,
        node_id=node_id,
        marker_sha256=marker_sha256,
        flashnode_version=flashnode_version,
        install_ms=install_ms,
        register_ms=register_ms,
        worker_pid=worker_pid,
        observations=recorder.frozen(),
    )


async def verify_worker(
    gateway: SandboxGateway,
    sandbox_id: str,
    *,
    expected_marker_sha256: str,
    expected_pid: int,
    paths: SandboxPaths = DEFAULT_PATHS,
    claim_window_s: float = CLAIM_WINDOW_S,
    on_observation: ObservationSink | None = None,
) -> WorkerHealth:
    """The wake path. Checks, reports, and repairs nothing.

    A hibernated sandbox comes back with its filesystem and its processes
    intact — measured 5/5, with a running ``flashnode work`` serving lease
    claims straight across the pause, 13 → 19 — so the correct action on wake
    is to confirm that and get out of the way. **This never re-bootstraps**:
    a fresh install would be a second worker under the same node id, and a
    fresh registration would tell the coordinator to forget the leases the
    surviving one is already serving.

    Four questions, each reported separately because each fails for its own
    reason:

    * *Is this the sandbox we prepared?* The marker hash. A mismatch means
      the filesystem is not the one whose hash the session recorded, and no
      amount of the worker looking healthy makes it the right worker.
    * *Is the worker there?* ``[ -d /proc/$PID ]``, never ``ps`` — there is
      no ``ps`` in this image, and the idiom that assumes there is reports
      every process as gone.
    * *Is it still claiming?* Counters that only move when it does work, plus
      the absence of new "coordinator unreachable" warnings across the same
      window. There is no success line in the agent's log to look for — a
      claim that returns 204 logs nothing — so absence of failure plus
      evidence of activity is the honest form of this question.
    * *Is the credential file gone?* It should have been deleted at bootstrap
      and nothing recreates it. A file that came back is a fact worth
      surfacing even though it does not, on its own, mean the worker is
      broken.

    Returns a :class:`WorkerHealth` rather than raising for any of those:
    "the worker died" is an outcome the caller has to record and act on, not
    an exception. Gateway failures — an unreachable sandbox, a refused wake —
    still raise, because those are not observations of a worker at all.
    """
    recorder = _Recorder(on_observation)
    started = time.monotonic()
    evidence = await _run(
        gateway, sandbox_id,
        probe_command(pid=expected_pid, paths=paths, window_s=claim_window_s),
        timeout_s=PROBE_TIMEOUT_S + int(claim_window_s) + 5,
        operation="verify",
    )
    reading = _parse_probe(evidence.stdout)
    checked_ms = (time.monotonic() - started) * 1000

    marker_matches = bool(
        expected_marker_sha256
        and reading.marker_sha256
        and reading.marker_sha256 == expected_marker_sha256
    )
    moved = reading.counters_moved
    # `moved is None` — no readable counter — is treated as "no contrary
    # evidence" rather than as a failure. The unreachable count is the check
    # that still bites in that case.
    claiming = bool(reading.alive and reading.new_unreachable == 0
                    and moved is not False)

    reasons = []
    if not marker_matches:
        reasons.append(
            f"marker {reading.marker_sha256 or 'missing'} != "
            f"{expected_marker_sha256}"
        )
    if not reading.alive:
        reasons.append(f"pid {expected_pid} is not in /proc")
    if not claiming:
        reasons.append(
            f"no evidence of claiming ({reading.new_unreachable} new "
            "'coordinator unreachable' warnings"
            + ("" if moved is not False else ", counters flat") + ")"
        )
    if reading.credential_present:
        reasons.append("the credential file is present again")

    healthy = marker_matches and reading.alive and claiming
    detail = "; ".join(reasons)
    recorder.emit(
        "worker.verified" if healthy else "worker.unhealthy",
        latency_ms=checked_ms,
        marker_matches=marker_matches,
        marker_sha256=reading.marker_sha256,
        pid=expected_pid,
        pid_alive=reading.alive,
        claiming=claiming,
        credential_absent=not reading.credential_present,
        detail=detail or None,
        log_tail=None if healthy else reading.log_tail or None,
    )
    return WorkerHealth(
        sandbox_id=sandbox_id,
        healthy=healthy,
        marker_matches=marker_matches,
        marker_sha256=reading.marker_sha256,
        pid_alive=reading.alive,
        claiming=claiming,
        credential_absent=not reading.credential_present,
        checked_ms=checked_ms,
        detail=detail,
        observations=recorder.frozen(),
    )


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def _receipt_bytes(result: BootstrapResult) -> bytes:
    """What a later bootstrap reads to discover a worker is already here.

    Separate from the marker because it cannot be part of it: the marker is
    written before the worker exists, and its hash is the thing a wake
    compares, so it can never learn a pid. Carries nothing secret — a node
    id, a version, a pid and two latencies.
    """
    return (json.dumps({
        "sandbox_id": result.sandbox_id,
        "node_id": result.node_id,
        "marker_sha256": result.marker_sha256,
        "flashnode_version": result.flashnode_version,
        "install_ms": round(result.install_ms, 3),
        "register_ms": round(result.register_ms, 3),
        "worker_pid": result.worker_pid,
        "template_version": TEMPLATE_VERSION,
    }, sort_keys=True, indent=2) + "\n").encode()


async def _existing_result(
    gateway: SandboxGateway,
    sandbox_id: str,
    *,
    paths: SandboxPaths,
    recorder: _Recorder,
    claim_window_s: float,
) -> BootstrapResult | None:
    """The idempotency check: a receipt whose worker still checks out.

    Deliberately routed through :func:`verify_worker` rather than a private
    copy of the same checks. Two implementations of "is this worker healthy"
    would eventually disagree, and the disagreement would show up as a
    bootstrap that reinstalls over a working node.

    Anything unreadable — no receipt, corrupt JSON, a receipt from a
    different template version — returns None and the caller bootstraps from
    scratch. A half-understood receipt is not evidence of anything.
    """
    evidence = await _run(
        gateway, sandbox_id, f"cat {shlex.quote(paths.receipt)} 2>/dev/null || true",
        timeout_s=PROBE_TIMEOUT_S, operation="bootstrap.receipt",
    )
    raw = (evidence.stdout or "").strip()
    if not raw:
        return None
    try:
        receipt = json.loads(raw)
    except ValueError:
        log.warning("sandbox %s has an unreadable bootstrap receipt", sandbox_id)
        return None
    if not isinstance(receipt, dict):
        return None
    if str(receipt.get("template_version", "")) != TEMPLATE_VERSION:
        return None
    pid = _as_int(receipt.get("worker_pid"), 0)
    marker = str(receipt.get("marker_sha256") or "")
    if not pid or not marker:
        return None

    health = await verify_worker(
        gateway, sandbox_id, expected_marker_sha256=marker, expected_pid=pid,
        paths=paths, claim_window_s=claim_window_s, on_observation=recorder.sink,
    )
    recorder.observations.extend(health.observations)
    if not health.healthy:
        log.info(
            "sandbox %s has a bootstrap receipt but no healthy worker (%s); "
            "bootstrapping again", sandbox_id, health.detail,
        )
        return None

    recorder.emit(
        "worker.bootstrap.reused",
        latency_ms=health.checked_ms,
        node_id=str(receipt.get("node_id") or ""),
        pid=pid,
        marker_sha256=marker,
        flashnode_version=str(receipt.get("flashnode_version") or ""),
    )
    return BootstrapResult(
        sandbox_id=sandbox_id,
        node_id=str(receipt.get("node_id") or ""),
        marker_sha256=marker,
        flashnode_version=str(receipt.get("flashnode_version") or ""),
        install_ms=float(receipt.get("install_ms") or 0.0),
        register_ms=float(receipt.get("register_ms") or 0.0),
        worker_pid=pid,
        reused=True,
        observations=recorder.frozen(),
    )


async def _install(
    gateway: SandboxGateway, sandbox_id: str, *, version: str, recorder: _Recorder,
) -> float:
    """Install the pinned agent, then prove which version actually landed.

    The read-back is not paranoia about pip. It is the guard against this
    module's own most likely future regression: somebody removes the
    ``-i https://pypi.org/simple/`` because "pip works fine", the Aliyun
    mirror satisfies ``flashnode`` with whatever it has, and a fleet quietly
    runs an agent nobody released. A version that fails to resolve is loud; a
    version that resolves to something older is not.
    """
    started = time.monotonic()
    evidence = await _run(
        gateway, sandbox_id, install_command(version),
        timeout_s=INSTALL_TIMEOUT_S, operation="bootstrap.install",
    )
    install_ms = (time.monotonic() - started) * 1000
    if not evidence.ok:
        tail = _tail(evidence.stderr or evidence.stdout)
        stale = (
            "No matching distribution" in tail or "Could not find a version" in tail
        )
        recorder.emit(
            "worker.install.failed", latency_ms=install_ms, version=version,
            index=UPSTREAM_INDEX, exit_code=evidence.exit_code, detail=tail,
        )
        raise SandboxTerminalError(
            f"installing flashnode=={version} failed (exit {evidence.exit_code})"
            + (
                " — the index served no such version. /etc/pip.conf pins pip to "
                "the Aliyun mirror, which lags PyPI; every install here must "
                f"pass -i {UPSTREAM_INDEX}."
                if stale else f": {tail}"
            ),
            operation="bootstrap.install", sandbox_id=sandbox_id,
        )

    check = await _run(
        gateway, sandbox_id,
        "python3 -c \"import importlib.metadata as m; print(m.version('flashnode'))\"",
        timeout_s=PROBE_TIMEOUT_S, operation="bootstrap.install.verify",
    )
    installed = _last_line(check.stdout) if check.ok else ""
    if installed != version:
        recorder.emit(
            "worker.install.failed", latency_ms=install_ms, version=version,
            installed=installed or None, index=UPSTREAM_INDEX,
        )
        raise SandboxTerminalError(
            f"asked for flashnode=={version} and got "
            f"{installed or 'nothing importable'} — the install resolved "
            "against a stale index",
            operation="bootstrap.install", sandbox_id=sandbox_id,
        )

    recorder.emit(
        "worker.install.completed", latency_ms=install_ms, version=installed,
        index=UPSTREAM_INDEX,
    )
    return install_ms


async def _write_marker(
    gateway: SandboxGateway,
    sandbox_id: str,
    *,
    payload: bytes,
    expected: str,
    paths: SandboxPaths,
    recorder: _Recorder,
    secrets: tuple[str, ...],
) -> None:
    """Write the marker and read its hash back from the sandbox.

    The read-back costs one round trip and buys the only thing that makes the
    wake check mean anything: that the bytes we hashed are the bytes on that
    disk. Comparing a hash we computed locally against a file we never
    confirmed would make every later verification a comparison of our own
    arithmetic with itself.
    """
    started = time.monotonic()
    await _write_file(
        gateway, sandbox_id, paths.marker, payload, mode=0o600,
        operation="bootstrap.marker", secrets=secrets,
    )
    evidence = await _run(
        gateway, sandbox_id,
        f"sha256sum {shlex.quote(paths.marker)} | cut -d' ' -f1",
        timeout_s=PROBE_TIMEOUT_S, operation="bootstrap.marker.verify",
    )
    seen = _last_line(evidence.stdout) if evidence.ok else ""
    latency_ms = (time.monotonic() - started) * 1000
    if seen != expected:
        recorder.emit(
            "worker.marker.failed", latency_ms=latency_ms,
            expected_sha256=expected, observed_sha256=seen or None,
        )
        raise SandboxTerminalError(
            f"the prepared marker did not survive the write: expected "
            f"{expected}, the sandbox reports {seen or 'nothing'}",
            operation="bootstrap.marker", sandbox_id=sandbox_id,
        )
    recorder.emit(
        "worker.marker.written", latency_ms=latency_ms, marker_sha256=expected,
        path=paths.marker,
    )


async def _write_credential(
    gateway: SandboxGateway,
    sandbox_id: str,
    *,
    coordinator_url: str,
    token: str,
    paths: SandboxPaths,
    recorder: _Recorder,
) -> None:
    """Place the credential at 0600 and verify the mode.

    ``write_file`` writes at the agent's umask and then chmods, because the
    E2B SDK's ``files.write`` has no mode parameter — so there is a real
    window, and a real second call that can fail. Reading ``stat -c %a`` back
    is how "we asked for 0600" becomes "it is 0600".

    Nothing in this function's output, observations or exceptions contains
    the token: the bytes travel as a ``write_file`` payload, and the only
    strings that come back are a path and three octal digits.
    """
    started = time.monotonic()
    await _write_file(
        gateway, sandbox_id, paths.credentials,
        credential_bytes(coordinator_url, token), mode=0o600,
        operation="bootstrap.credential", secrets=(token,),
    )
    evidence = await _run(
        gateway, sandbox_id, f"stat -c %a {shlex.quote(paths.credentials)}",
        timeout_s=PROBE_TIMEOUT_S, operation="bootstrap.credential.verify",
        secrets=(token,),
    )
    mode = _last_line(evidence.stdout) if evidence.ok else ""
    latency_ms = (time.monotonic() - started) * 1000
    if mode != "600":
        recorder.emit(
            "worker.credential.failed", latency_ms=latency_ms,
            path=paths.credentials, mode=mode or None,
        )
        raise SandboxTerminalError(
            f"the credential file is mode {mode or 'unreadable'}, not 600",
            operation="bootstrap.credential", sandbox_id=sandbox_id,
        )
    recorder.emit(
        "worker.credential.written", latency_ms=latency_ms, path=paths.credentials,
        mode=mode, coordinator=normalise_coordinator(coordinator_url),
    )


async def _launch_and_register(
    gateway: SandboxGateway,
    sandbox_id: str,
    *,
    coordinator_url: str,
    pool_id: str,
    paths: SandboxPaths,
    poll_seconds: float,
    recorder: _Recorder,
    register_timeout_s: float,
    register_settle_s: float,
    poll_interval_s: float,
    launch_attempts: int,
    secrets: tuple[str, ...],
) -> tuple[int, float]:
    """Start the worker and wait until it has registered. Bounded restarts.

    ``client.register()`` is the one unguarded call in ``flashnode work``: an
    unreachable coordinator there is an unhandled ``CoordinatorUnreachable``
    and exit 1 in about four seconds, while everything below it — the claim
    loop, the heartbeats, the artifact transfers — backs off correctly. So a
    momentary blip in that four-second window is the single failure that
    leaves a prepared sandbox with no worker in it and nothing to say so.

    Two defences, both needed. The reachability check before each attempt
    turns "the coordinator is down" into an error the caller can read,
    instead of a crash loop. The bounded relaunch covers the blip that
    happens between the check and the register. Unbounded restarts are not
    the answer: a coordinator that refuses this credential refuses it every
    time, and a loop around that is how a configuration mistake becomes a
    rate-limit ban.
    """
    last_tail = ""
    for attempt in range(1, max(1, launch_attempts) + 1):
        await _require_reachable(
            gateway, sandbox_id, coordinator_url=coordinator_url,
            recorder=recorder, attempt=attempt,
        )
        started = time.monotonic()
        launch = await _run(
            gateway, sandbox_id,
            launch_command(coordinator_url=coordinator_url, pool_id=pool_id,
                           paths=paths, poll_seconds=poll_seconds),
            timeout_s=PROBE_TIMEOUT_S, operation="bootstrap.launch",
            background=True, secrets=secrets,
        )
        pid = launch.background_pid
        if not pid:
            raise SandboxTerminalError(
                "the sandbox started the worker but reported no pid, so there "
                "is nothing to supervise or verify on wake",
                operation="bootstrap.launch", sandbox_id=sandbox_id,
            )
        recorder.emit(
            "worker.launched", latency_ms=launch.duration_ms, pid=pid,
            runner=RUNNER, attempt=attempt, log=paths.agent_log,
        )

        outcome, reading = await _await_registration(
            gateway, sandbox_id, pid=pid, paths=paths,
            register_timeout_s=register_timeout_s,
            register_settle_s=register_settle_s, poll_interval_s=poll_interval_s,
        )
        register_ms = (time.monotonic() - started) * 1000
        last_tail = _tail(reading.log_tail)

        if outcome == "registered":
            recorder.emit(
                "worker.registered", latency_ms=register_ms, pid=pid,
                attempt=attempt, coordinator=normalise_coordinator(coordinator_url),
                pool_id=str(pool_id),
            )
            return pid, register_ms

        recorder.emit(
            "worker.registration.failed", latency_ms=register_ms, pid=pid,
            attempt=attempt, outcome=outcome, log_tail=last_tail or None,
        )
        if outcome == "fatal":
            # A fatal marker in the log while the process is STILL ALIVE. The
            # loop's last-line-of-defence handler logs a traceback without
            # dying, so this is not necessarily a corpse — and relaunching
            # over a live worker would put two processes on one node id,
            # each claiming leases as the other. Kill first, then retry.
            await _kill_worker(gateway, sandbox_id, pid=pid)
        if outcome == "timeout":
            # The process is alive and not getting through. Relaunching would
            # not change that, and killing it would throw away a worker that
            # may recover on its own backoff — so this is reported, not
            # retried, and the caller decides.
            registered_then_lost = reading.unreachable_after > 0
            raise SandboxTransportError(
                (
                    "the worker registered and then lost the coordinator — "
                    f"{reading.unreachable_after} 'coordinator unreachable' "
                    "warnings from its claim loop, which only runs after "
                    "registration returned. It is still running and still "
                    "not claiming"
                    if registered_then_lost else
                    "the worker did not produce evidence of registering "
                    f"within {register_timeout_s:.0f}s and is still running"
                ) + ". Kill this sandbox rather than retrying — a second "
                f"bootstrap would launch a second worker under one node id: "
                f"{last_tail}",
                operation="bootstrap.register", sandbox_id=sandbox_id,
                may_have_applied=True,
            )
        # outcome == "died": the process is gone, so a relaunch is safe and
        # is exactly the transient-blip case this loop exists for. "fatal"
        # was made safe by the kill above.

    raise SandboxTerminalError(
        f"the worker failed to register {launch_attempts} times — it exited "
        f"or logged a fatal error before registering: {last_tail}",
        operation="bootstrap.register", sandbox_id=sandbox_id,
    )


async def _require_reachable(
    gateway: SandboxGateway,
    sandbox_id: str,
    *,
    coordinator_url: str,
    recorder: _Recorder,
    attempt: int,
) -> None:
    started = time.monotonic()
    evidence = await _run(
        gateway, sandbox_id, reachability_command(coordinator_url),
        timeout_s=PROBE_TIMEOUT_S, operation="bootstrap.reachability",
    )
    code = _kv(evidence.stdout, "coordinator_http")
    latency_ms = (time.monotonic() - started) * 1000
    reachable = bool(code) and code != "000"
    recorder.emit(
        "worker.coordinator.probed", latency_ms=latency_ms, http_status=code or None,
        reachable=reachable, attempt=attempt,
        coordinator=normalise_coordinator(coordinator_url),
    )
    if not reachable:
        raise SandboxTransportError(
            "the coordinator is not routable from this sandbox "
            f"(curl reported {code or 'nothing'}); `flashnode work` would exit "
            "1 within seconds on an unguarded registration",
            operation="bootstrap.reachability", sandbox_id=sandbox_id,
            may_have_applied=False,
        )


async def _await_registration(
    gateway: SandboxGateway,
    sandbox_id: str,
    *,
    pid: int,
    paths: SandboxPaths,
    register_timeout_s: float,
    register_settle_s: float,
    poll_interval_s: float,
) -> tuple[str, _ProbeReading]:
    """Poll until the worker has registered, died, or run out of time.

    **What "registered" is inferred from, stated plainly.** Nothing in this
    sandbox can observe the registration itself: the agent does not write the
    payload to disk, it logs no success line, and asking the coordinator
    would need the credential we are about to delete. What it does do is
    *die* if registration fails — ``client.register()`` is the one unguarded
    call in ``flashnode work``, so a coordinator that is unreachable or that
    refuses the credential is an unhandled exception and exit 1 in about four
    seconds. Survival past that window is therefore the evidence, together
    with a non-empty log: the trusted-runner banner is printed before any
    network call, so an empty log means the process never reached the
    startup it would have died in.

    **A "coordinator unreachable" warning means the opposite of what it looks
    like.** That line comes from the claim loop, which only ever runs *after*
    registration returned — so seeing it proves the agent registered, and
    then lost the coordinator. It is still not a worker anybody can use, so
    it does not satisfy this wait; it is reported as what it is rather than
    as a failure to register, and the wait keeps going because the loop's own
    backoff may well recover.

    Returns one of ``registered`` / ``died`` / ``fatal`` / ``timeout`` with
    the last reading, so the caller can act on the difference: a corpse may
    be relaunched, a live worker may not.
    """
    deadline = time.monotonic() + register_timeout_s
    launched_at = time.monotonic()
    reading = _ProbeReading()
    while True:
        evidence = await _run(
            gateway, sandbox_id, probe_command(pid=pid, paths=paths),
            timeout_s=PROBE_TIMEOUT_S, operation="bootstrap.register.probe",
        )
        reading = _parse_probe(evidence.stdout)
        if not reading.alive:
            return "died", reading
        if reading.fatal:
            # Alive AND carrying a fatal marker: the caller must stop it
            # before starting another, which is why this is not "died".
            return "fatal", reading
        settled = (time.monotonic() - launched_at) >= register_settle_s
        if settled and reading.log_bytes > 0 and reading.unreachable_after == 0:
            return "registered", reading
        if time.monotonic() >= deadline:
            return "timeout", reading
        await asyncio.sleep(max(0.0, poll_interval_s))


async def _assert_not_sandbox_capable(
    gateway: SandboxGateway,
    sandbox_id: str,
    *,
    node_id: str,
    coordinator_url: str,
    pool_id: str,
    paths: SandboxPaths,
    recorder: _Recorder,
    worker_pid: int,
    secrets: tuple[str, ...],
) -> None:
    """Refuse a worker that advertises isolation it cannot provide.

    The coordinator places a public job — ``tier: sandboxed, allowFallback:
    false`` — only on a node whose registration says ``sandbox_capable``.
    Nothing else keeps public work off this machine: the pool gate does not
    run for a public job at all, because a public job's payload has no
    ``pool`` key for it to read. And the stamp the gate reads comes from the
    agent's own registration, not from our ``machines`` table, which is a
    display-only snapshot.

    So a sandbox that registered ``sandbox_capable: true`` would be handed
    strangers' code to run unsandboxed on a box that cannot sandbox anything.
    That is worse than a sandbox that fails to start, which is why this kills
    the worker and raises rather than warning. The caller still has to revoke
    the credential and kill the sandbox — this module holds neither.

    A probe that will not run is also a failure. Fail closed: an unverifiable
    capability claim is not a verified one.
    """
    started = time.monotonic()
    evidence = await _run(
        gateway, sandbox_id,
        capability_command(node_id=node_id, coordinator_url=coordinator_url,
                           pool_id=pool_id, paths=paths),
        timeout_s=PROBE_TIMEOUT_S, operation="bootstrap.capabilities",
        secrets=secrets,
    )
    latency_ms = (time.monotonic() - started) * 1000
    payload: dict[str, Any] = {}
    if evidence.ok:
        for line in reversed((evidence.stdout or "").strip().splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    parsed = json.loads(line)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    payload = parsed
                break

    sandbox_capable = payload.get("sandbox_capable")
    if payload and sandbox_capable is False:
        recorder.emit(
            "worker.capabilities.verified", latency_ms=latency_ms,
            sandbox_capable=False,
            unsandboxed_argv_capable=payload.get("unsandboxed_argv_capable"),
            module_capable=payload.get("module_capable"),
            can_install_dependencies=payload.get("can_install_dependencies"),
            agent_version=payload.get("agent_version"),
        )
        return

    recorder.emit(
        "worker.capabilities.rejected", latency_ms=latency_ms,
        sandbox_capable=sandbox_capable,
        detail=_tail(evidence.stderr or evidence.stdout) or None,
    )
    await _kill_worker(gateway, sandbox_id, pid=worker_pid)
    raise SandboxTerminalError(
        "this worker registered sandbox_capable="
        f"{sandbox_capable!r}; a node that claims container isolation it "
        "cannot provide is placeable for public jobs and would run "
        "strangers' code unsandboxed. The worker has been killed — revoke "
        "this machine's credential and kill the sandbox.",
        operation="bootstrap.capabilities", sandbox_id=sandbox_id,
    )


async def _kill_worker(
    gateway: SandboxGateway, sandbox_id: str, *, pid: int
) -> None:
    """Stop the worker claiming, best effort.

    Best effort because this runs on a path that is already failing, and an
    exception here would replace a precise diagnosis with a vague one. The
    kill is what closes the window; the caller killing the sandbox is what
    closes it permanently.
    """
    try:
        await _run(
            gateway, sandbox_id, f"kill {int(pid)} 2>/dev/null || true",
            timeout_s=PROBE_TIMEOUT_S, operation="bootstrap.kill_worker",
        )
    except SandboxError:
        log.warning(
            "sandbox %s: could not stop worker pid %s after a capability "
            "violation; the sandbox itself must be killed", sandbox_id, pid,
        )


async def _delete_credential(
    gateway: SandboxGateway,
    sandbox_id: str,
    *,
    paths: SandboxPaths,
    recorder: _Recorder,
    secrets: tuple[str, ...],
) -> None:
    """Remove the credential file, and confirm it is gone.

    Safe by the time this runs, and only by then: ``flashnode work`` reads
    the token once, before ``client.register()``, and holds it on the
    ``CoordinatorClient`` for the life of the process — including through the
    re-registration it performs when a node heartbeat is refused, and
    including across a hibernation, where the process itself survives. So
    registration having been confirmed is exactly the evidence that the file
    has done its job.

    Confirmed rather than assumed. ``rm -f`` succeeds on a path it did not
    remove, and "we ran the delete" is not "the token is not on that disk".
    """
    started = time.monotonic()
    path = shlex.quote(paths.credentials)
    evidence = await _run(
        gateway, sandbox_id,
        f"rm -f {path}; echo \"credential_present=$( [ -e {path} ] && echo yes "
        "|| echo no )\"",
        timeout_s=PROBE_TIMEOUT_S, operation="bootstrap.credential.delete",
        secrets=secrets,
    )
    latency_ms = (time.monotonic() - started) * 1000
    if _kv(evidence.stdout, "credential_present") != "no":
        recorder.emit(
            "worker.credential.delete_failed", latency_ms=latency_ms,
            path=paths.credentials,
        )
        raise SandboxTerminalError(
            "the credential file is still on disk after being deleted",
            operation="bootstrap.credential.delete", sandbox_id=sandbox_id,
        )
    recorder.emit(
        "worker.credential.deleted", latency_ms=latency_ms, path=paths.credentials,
    )


# ---------------------------------------------------------------------------
# Gateway helpers. Every call goes through one of these, so the token cannot
# escape through an error path that forgot about it.
# ---------------------------------------------------------------------------


def _tail(text: str | None, lines: int = LOG_TAIL_LINES) -> str:
    if not text:
        return ""
    return "\n".join(str(text).strip().splitlines()[-lines:])


async def _run(
    gateway: SandboxGateway,
    sandbox_id: str,
    command: str,
    *,
    timeout_s: int,
    operation: str,
    background: bool = False,
    secrets: tuple[str, ...] = (),
) -> CommandEvidence:
    """One command, with every failure re-raised scrubbed.

    The SDK is entitled to echo a request it could not complete, and this
    module hands it a file whose contents are a bearer token. `_scrub` on the
    way out is what makes "the token reaches the sandbox and nowhere else" a
    property of the code rather than a promise about it.
    """
    try:
        return await gateway.run(
            sandbox_id, command, timeout_s=timeout_s, background=background
        )
    except SandboxError as exc:
        raise _rewrap(exc, operation=operation, sandbox_id=sandbox_id,
                      secrets=secrets) from None


async def _run_checked(
    gateway: SandboxGateway,
    sandbox_id: str,
    command: str,
    *,
    timeout_s: int,
    operation: str,
    secrets: tuple[str, ...] = (),
) -> CommandEvidence:
    evidence = await _run(
        gateway, sandbox_id, command, timeout_s=timeout_s, operation=operation,
        secrets=secrets,
    )
    if not evidence.ok:
        raise SandboxTerminalError(
            f"{operation} failed (exit {evidence.exit_code}): "
            f"{_scrub(_tail(evidence.stderr or evidence.stdout), *secrets)}",
            operation=operation, sandbox_id=sandbox_id,
        )
    return evidence


async def _write_file(
    gateway: SandboxGateway,
    sandbox_id: str,
    path: str,
    data: bytes,
    *,
    mode: int,
    operation: str,
    secrets: tuple[str, ...] = (),
) -> None:
    try:
        await gateway.write_file(sandbox_id, path, data, mode=mode)
    except SandboxError as exc:
        raise _rewrap(exc, operation=operation, sandbox_id=sandbox_id,
                      secrets=secrets) from None


def _rewrap(
    exc: SandboxError, *, operation: str, sandbox_id: str, secrets: tuple[str, ...]
) -> SandboxError:
    """Re-raise a gateway error of the same class, with the secrets removed
    and this module's operation name on it.

    Same class, deliberately: the caller's retry decision hinges on transport
    versus terminal, and rewriting one into the other to add a scrub would be
    a worse bug than the leak it prevents. ``may_have_applied`` is carried
    across for the same reason.
    """
    message = _scrub(exc, *secrets)
    if isinstance(exc, SandboxTransportError):
        return SandboxTransportError(
            message, operation=operation, sandbox_id=sandbox_id or exc.sandbox_id,
            may_have_applied=exc.may_have_applied,
        )
    return type(exc)(
        message, operation=operation, sandbox_id=sandbox_id or exc.sandbox_id,
    )
