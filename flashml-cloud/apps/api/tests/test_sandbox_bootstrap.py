"""What `sandbox_bootstrap` must keep true, against a simulated sandbox.

The gateway below is `FakeSandboxGateway` with a shell bolted on: it records
every command, answers the ones this module issues out of the file state the
fake already keeps, and lets a test break exactly one of them. That shape is
deliberate — a mock that returns canned strings for a command it never looked
at would pass just as happily if the module wrote its credential with `echo`,
and "the token is never in a command string" is the guarantee most worth
pinning here.

Four of these tests exist because the live probe in `ap-southeast-1` found the
behaviour the hard way, and each is named for it: the stale Aliyun mirror, the
missing `ps`, the `&` that never returns, and the unguarded `register()`.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import dataclass, field

import pytest

from flashml_cloud_api.alibaba_sandbox import (
    CommandEvidence,
    FakeSandboxGateway,
    SandboxTerminalError,
    SandboxTransportError,
)
from flashml_cloud_api.sandbox_bootstrap import (
    DEFAULT_FLASHNODE_VERSION,
    DEFAULT_PATHS,
    UPSTREAM_INDEX,
    BootstrapResult,
    WorkerHealth,
    bootstrap_worker,
    credential_bytes,
    install_command,
    launch_command,
    marker_bytes,
    marker_document,
    normalise_coordinator,
    probe_command,
    verify_worker,
)
from flashml_cloud_api.sandbox_sessions import Observation, redact_data

#: A token of the shape `sandbox_identity` mints. `fmk_` is on
#: `sandbox_sessions`' redaction list, which is the second line of defence —
#: these tests are about the first, so the assertions look for this exact
#: string rather than for the placeholder.
TOKEN = "fmk_bootstrap_test_do_not_log_2f9c1e"
COORDINATOR = "https://api.flashml.test/"
POOL_ID = "pool-0001"
NONCE = "nonce-abcdef0123456789"


@dataclass(frozen=True)
class _Credential:
    """Structurally `sandbox_identity.EphemeralMachineCredential`, which this
    module takes as a parameter rather than importing — so the test can build
    one with no database anywhere near it."""

    machine_id: str = "11111111-1111-1111-1111-111111111111"
    node_id: str = "fn-sandbox-0001"
    raw_token: str = field(default=TOKEN, repr=False)


class _Sandbox(FakeSandboxGateway):
    """`FakeSandboxGateway` that can answer a shell command.

    Answers are computed from the fake's own file state wherever possible —
    `sha256sum` really hashes the bytes that were written, `stat -c %a` really
    reports the mode `write_file` recorded, `rm -f` really removes the file.
    A test that broke the module's file handling would therefore fail here
    rather than sail past a canned string.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.commands: list[str] = []
        self.background_commands: list[str] = []
        self.live_pids: set[int] = set()
        self.launches = 0
        #: Knobs, one per failure the live probe actually produced.
        self.install_exit = 0
        self.install_stdout = "Successfully installed flashnode-0.4.0"
        self.installed_version = DEFAULT_FLASHNODE_VERSION
        self.coordinator_http = "200"
        self.log_bytes = 480
        self.fatal_hits = 0
        self.unreachable_hits = 0
        self.worker_active = True
        self.dies_after_launch = 0
        self.capabilities = {
            "sandbox_capable": False,
            "unsandboxed_argv_capable": True,
            "module_capable": False,
            "can_install_dependencies": True,
            "agent_version": DEFAULT_FLASHNODE_VERSION,
        }
        self.capability_exit = 0
        self.credential_mode_override: int | None = None
        self.fail_credential_write_with: str | None = None

    # -- recording -----------------------------------------------------------

    async def run(self, sandbox_id, command, *, timeout_s=300, background=False):
        self.commands.append(command)
        if background:
            self.background_commands.append(command)
        else:
            self.command_results[command] = self._answer(sandbox_id, command)
        evidence: CommandEvidence = await super().run(
            sandbox_id, command, timeout_s=timeout_s, background=background
        )
        if background and evidence.background_pid:
            self.launches += 1
            if self.launches > self.dies_after_launch:
                self.live_pids.add(evidence.background_pid)
        return evidence

    async def write_file(self, sandbox_id, path, data, *, mode=0o600):
        if path == DEFAULT_PATHS.credentials and self.fail_credential_write_with:
            self.fail_next(
                "write_file", kind="terminal", message=self.fail_credential_write_with
            )
        await super().write_file(sandbox_id, path, data, mode=mode)
        if path == DEFAULT_PATHS.credentials and self.credential_mode_override:
            self.sandboxes[sandbox_id].modes[path] = self.credential_mode_override

    # -- a very small shell --------------------------------------------------

    def _answer(self, sandbox_id: str, command: str) -> tuple[int, str, str]:
        files = self.sandboxes[sandbox_id].files
        modes = self.sandboxes[sandbox_id].modes

        if command.startswith("python3 -m pip install"):
            return (
                self.install_exit,
                self.install_stdout if self.install_exit == 0 else "",
                "" if self.install_exit == 0 else self.install_stdout,
            )
        if "importlib.metadata" in command:
            return (0, self.installed_version + "\n", "")
        if command.startswith("mkdir -p"):
            return (0, "", "")
        if command.startswith("cat "):
            path = shlex.split(command)[1]
            return (0, (files.get(path) or b"").decode(), "")
        if command.startswith("sha256sum"):
            path = shlex.split(command)[1]
            data = files.get(path)
            return (0, hashlib.sha256(data).hexdigest() + "\n" if data else "", "")
        if command.startswith("stat -c %a"):
            path = shlex.split(command)[3]
            return (0, f"{modes.get(path, 0o644):o}\n", "")
        if command.startswith("echo \"coordinator_http="):
            return (0, f"coordinator_http={self.coordinator_http}\n", "")
        if "flashnode.inventory.capabilities" in command:
            return (
                self.capability_exit,
                json.dumps(self.capabilities) + "\n" if not self.capability_exit else "",
                "",
            )
        if command.startswith("rm -f "):
            # `rm -f <path>; echo ...` — split on the `;` first, because
            # shlex keeps it attached to the token in front of it.
            path = shlex.split(command.split(";", 1)[0])[2]
            files.pop(path, None)
            present = "yes" if path in files else "no"
            return (0, f"credential_present={present}\n", "")
        if command.startswith("kill "):
            self.live_pids.discard(int(command.split()[1]))
            return (0, "", "")
        if command.startswith("P="):
            return (0, self._probe_output(command, files), "")
        raise AssertionError(f"the fake sandbox was asked something new: {command!r}")

    def _probe_output(self, command: str, files: dict[str, bytes]) -> str:
        pid = int(re.match(r"P=(\d*)", command).group(1) or 0)
        alive = pid in self.live_pids
        marker = files.get(DEFAULT_PATHS.marker)
        io_before = 100_000
        io_after = io_before + (2_048 if (alive and self.worker_active) else 0)
        return "\n".join([
            f"io_before={io_before}",
            "cpu_before=42",
            f"unreachable_before={self.unreachable_hits}",
            f"alive={'yes' if alive else 'no'}",
            f"io_after={io_after}",
            "cpu_after=43",
            f"unreachable_after={self.unreachable_hits}",
            "marker_sha256="
            + (hashlib.sha256(marker).hexdigest() if marker else ""),
            "credential_present="
            + ("yes" if DEFAULT_PATHS.credentials in files else "no"),
            f"log_bytes={self.log_bytes}",
            f"fatal={self.fatal_hits}",
            "---flashml-log-tail---",
            "worker log tail",
        ]) + "\n"


async def _new_sandbox(**kwargs) -> tuple[_Sandbox, str]:
    gateway = _Sandbox(**kwargs)
    observed = await gateway.create(template="code-interpreter-v1",
                                    timeout_ms=900_000, metadata={})
    return gateway, observed.sandbox_id


async def _bootstrap(gateway: _Sandbox, sandbox_id: str, **overrides):
    kwargs = dict(
        credential=_Credential(),
        coordinator_url=COORDINATOR,
        pool_id=POOL_ID,
        marker_nonce=NONCE,
        # Zeroed so the suite does not sleep. The production defaults exist
        # to outlast a ~4 s crash window that this fake reproduces
        # instantaneously.
        register_settle_s=0.0,
        poll_interval_s=0.0,
        claim_window_s=0.0,
    )
    kwargs.update(overrides)
    return await bootstrap_worker(gateway, sandbox_id, **kwargs)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_bare_sandbox_becomes_a_registered_worker():
    gateway, sandbox_id = await _new_sandbox()
    events: list[Observation] = []

    result = await _bootstrap(gateway, sandbox_id, on_observation=events.append)

    assert isinstance(result, BootstrapResult)
    assert result.sandbox_id == sandbox_id
    assert result.node_id == "fn-sandbox-0001"
    assert result.flashnode_version == DEFAULT_FLASHNODE_VERSION
    assert result.worker_pid in gateway.live_pids
    assert result.install_ms >= 0 and result.register_ms >= 0
    assert not result.reused

    files = gateway.sandboxes[sandbox_id].files
    # The marker is on disk and its hash is the one the caller was handed.
    assert hashlib.sha256(files[DEFAULT_PATHS.marker]).hexdigest() == result.marker_sha256
    # The node id is seeded rather than invented by the agent: a worker that
    # registers under a `fn-<random>` of its own is a machine row nothing can
    # match to a session.
    assert files[DEFAULT_PATHS.node_id_file] == b"fn-sandbox-0001\n"
    # And the credential is gone.
    assert DEFAULT_PATHS.credentials not in files

    types = [e.type for e in events]
    assert types == [
        "worker.install.completed",
        "worker.marker.written",
        "worker.credential.written",
        "worker.coordinator.probed",
        "worker.launched",
        "worker.registered",
        # Deleted before the capability probe, which does not need it: every
        # extra step the token stays on disk is a step it did not need to.
        "worker.credential.deleted",
        "worker.capabilities.verified",
    ]
    assert result.observations == tuple(events)
    # Every observation is a measurement, not an intention.
    assert all(e.latency_ms is not None and e.latency_ms >= 0 for e in events)
    assert all(e.source == "controller" for e in events)


@pytest.mark.asyncio
async def test_the_marker_hash_is_reproducible_from_what_the_session_stores():
    """Nothing in the marker is generated inside the module, so somebody
    investigating a mismatch can rebuild the document and compare."""
    gateway, sandbox_id = await _new_sandbox()
    result = await _bootstrap(gateway, sandbox_id)

    rebuilt = marker_bytes(marker_document(
        nonce=NONCE, node_id="fn-sandbox-0001",
        flashnode_version=DEFAULT_FLASHNODE_VERSION,
    ))
    assert hashlib.sha256(rebuilt).hexdigest() == result.marker_sha256
    document = json.loads(gateway.sandboxes[sandbox_id].files[DEFAULT_PATHS.marker])
    assert document["nonce"] == NONCE
    assert document["template_version"]


# ---------------------------------------------------------------------------
# The stale Aliyun mirror
# ---------------------------------------------------------------------------


def test_every_install_names_real_pypi():
    """`/etc/pip.conf` pins pip to `https://mirrors.aliyun.com/pypi/simple/`
    and that mirror lags: measured serving flashnode 0.3.5 while 0.4.0 was
    current. Without `-i` the install fails with "No matching distribution",
    which reads as a broken package rather than a stale index."""
    command = install_command("0.4.0")
    assert f"-i {UPSTREAM_INDEX}" in command
    assert "mirrors.aliyun.com" not in command
    # Not piped into `tail`: a pipeline reports tail's exit status, and the
    # exit status is what tells us the install failed at all.
    assert "|" not in command


@pytest.mark.asyncio
async def test_the_install_command_sent_to_the_sandbox_carries_the_index():
    gateway, sandbox_id = await _new_sandbox()
    await _bootstrap(gateway, sandbox_id)

    installs = [c for c in gateway.commands if c.startswith("python3 -m pip install")]
    assert len(installs) == 1
    assert f"-i {UPSTREAM_INDEX}" in installs[0]
    assert f"flashnode=={DEFAULT_FLASHNODE_VERSION}" in installs[0]


@pytest.mark.asyncio
async def test_a_stale_index_that_refuses_the_pin_says_so():
    gateway, sandbox_id = await _new_sandbox()
    gateway.install_exit = 1
    gateway.install_stdout = (
        "ERROR: Could not find a version that satisfies the requirement "
        "flashnode==0.4.0\nERROR: No matching distribution found for flashnode==0.4.0"
    )

    with pytest.raises(SandboxTerminalError) as caught:
        await _bootstrap(gateway, sandbox_id)

    assert "aliyun" in str(caught.value).lower()
    assert UPSTREAM_INDEX in str(caught.value)
    assert not gateway.background_commands, "nothing may be launched after a failed install"


@pytest.mark.asyncio
async def test_a_mirror_that_silently_serves_an_older_agent_is_caught():
    """The failure mode a lost `-i` produces once the mirror has *something*
    to offer: pip succeeds, and the fleet quietly runs an agent nobody
    released. The version is read back for exactly this."""
    gateway, sandbox_id = await _new_sandbox()
    gateway.installed_version = "0.3.5"

    with pytest.raises(SandboxTerminalError) as caught:
        await _bootstrap(gateway, sandbox_id)

    assert "0.3.5" in str(caught.value)
    assert not gateway.background_commands


# ---------------------------------------------------------------------------
# Launching: the `&` that never returns, and the missing `ps`
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_worker_is_launched_with_background_not_an_ampersand():
    """`commands.run("cd x && nohup daemon &")` never returns: the `&`
    backgrounds the whole AND-list, bash runs it in a subshell that inherits
    the command's stdout pipe, and envd waits for an EOF that arrives when
    the daemon dies. The SDK's own `timeout=` does not rescue it."""
    gateway, sandbox_id = await _new_sandbox()
    await _bootstrap(gateway, sandbox_id)

    assert len(gateway.background_commands) == 1
    launch = gateway.background_commands[0]
    assert not launch.rstrip().endswith("&")
    assert "nohup" not in launch
    # `exec` so the pid we are handed is the agent's own — the only handle
    # that survives a hibernation.
    assert " exec flashnode work " in launch
    assert "--runner trusted" in launch


def test_liveness_is_never_probed_with_ps():
    """There is no `ps` in this template; procps is absent. `ps -p $PID`
    exits 127, so the usual idiom reports every process on earth as gone —
    which is how an earlier measurement concluded that background processes
    do not survive hibernation. They do."""
    command = probe_command(pid=4321, paths=DEFAULT_PATHS)
    assert "[ -d /proc/$P ]" in command
    assert not re.search(r"\bps\b", command)
    assert not re.search(r"\bpgrep\b", command)


# ---------------------------------------------------------------------------
# Registration: the one unguarded call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unreachable_coordinator_is_refused_before_anything_is_launched():
    """`flashnode work` exits 1 in ~4 s with an unhandled
    `CoordinatorUnreachable` out of `client.register()` — the executor loop
    below it backs off fine, but registration is unguarded. Checking first
    turns a crash loop into an error somebody can read."""
    gateway, sandbox_id = await _new_sandbox()
    gateway.coordinator_http = "000"

    with pytest.raises(SandboxTransportError) as caught:
        await _bootstrap(gateway, sandbox_id)

    assert caught.value.may_have_applied is False
    assert not gateway.background_commands


@pytest.mark.asyncio
async def test_registration_that_never_completes_times_out_and_says_it_may_be_running():
    """The worker is up and its claim loop is logging "coordinator
    unreachable" every iteration. That line only ever runs after
    registration returned — so it registered and then lost the coordinator,
    which is still not a worker anybody can use."""
    gateway, sandbox_id = await _new_sandbox()
    gateway.unreachable_hits = 3

    with pytest.raises(SandboxTransportError) as caught:
        await _bootstrap(gateway, sandbox_id, register_timeout_s=0.0)

    assert caught.value.may_have_applied is True
    assert "not claiming" in str(caught.value)
    assert gateway.launches == 1, "a worker that is alive must not be relaunched"


@pytest.mark.asyncio
async def test_a_worker_that_never_gets_far_enough_to_log_times_out_too():
    gateway, sandbox_id = await _new_sandbox()
    gateway.log_bytes = 0  # not even the trusted-runner banner

    with pytest.raises(SandboxTransportError) as caught:
        await _bootstrap(gateway, sandbox_id, register_timeout_s=0.0)

    assert caught.value.may_have_applied is True
    assert "evidence of registering" in str(caught.value)


@pytest.mark.asyncio
async def test_a_worker_that_dies_before_registering_is_relaunched_once():
    """The transient blip in the four seconds that matter. Bounded, because
    a coordinator that refuses this credential refuses it every time."""
    gateway, sandbox_id = await _new_sandbox()
    gateway.dies_after_launch = 1  # the first launch leaves no live pid

    result = await _bootstrap(gateway, sandbox_id)

    assert gateway.launches == 2
    assert result.worker_pid in gateway.live_pids


@pytest.mark.asyncio
async def test_a_worker_that_never_survives_a_launch_fails_terminally():
    gateway, sandbox_id = await _new_sandbox()
    gateway.dies_after_launch = 99

    with pytest.raises(SandboxTerminalError) as caught:
        await _bootstrap(gateway, sandbox_id, launch_attempts=2)

    assert gateway.launches == 2
    assert "register" in str(caught.value)


# ---------------------------------------------------------------------------
# The credential
# ---------------------------------------------------------------------------


def test_the_credential_file_is_keyed_the_way_the_agent_reads_it():
    """`flashnode.identity.credentials` normalises by stripping trailing
    slashes. A file keyed `https://api.example/` when the agent asks for
    `https://api.example` parses fine and yields no token — and the agent
    then dies on a bare 401 running the command we gave it."""
    data = json.loads(credential_bytes("https://api.example/", TOKEN))
    assert data == {"https://api.example": TOKEN}
    assert normalise_coordinator("https://api.example//") == "https://api.example"


@pytest.mark.asyncio
async def test_the_credential_is_written_0600_and_then_deleted():
    gateway, sandbox_id = await _new_sandbox()
    modes: list[int] = []

    original = gateway.write_file

    async def _spy(sid, path, data, *, mode=0o600):
        if path == DEFAULT_PATHS.credentials:
            modes.append(mode)
        return await original(sid, path, data, mode=mode)

    gateway.write_file = _spy  # type: ignore[method-assign]
    result = await _bootstrap(gateway, sandbox_id)

    assert modes == [0o600]
    assert DEFAULT_PATHS.credentials not in gateway.sandboxes[sandbox_id].files
    # Deletion happens only after registration is confirmed — that is the
    # evidence the agent has the token in memory.
    order = [e.type for e in result.observations]
    assert order.index("worker.registered") < order.index("worker.credential.deleted")


@pytest.mark.asyncio
async def test_a_credential_left_world_readable_stops_the_bootstrap():
    """`write_file` sets the mode with a second `chmod`, because the SDK's
    `files.write` has no mode parameter — so there is a real round trip in
    which it can fail, and "we asked for 0600" is not "it is 0600"."""
    gateway, sandbox_id = await _new_sandbox()
    gateway.credential_mode_override = 0o644

    with pytest.raises(SandboxTerminalError) as caught:
        await _bootstrap(gateway, sandbox_id)

    assert "644" in str(caught.value)
    assert not gateway.background_commands


@pytest.mark.asyncio
async def test_a_credential_that_survives_its_own_deletion_is_an_error():
    gateway, sandbox_id = await _new_sandbox()

    async def _undeletable(sid, command, *, timeout_s=300, background=False):
        evidence = await _Sandbox.run(
            gateway, sid, command, timeout_s=timeout_s, background=background
        )
        if command.startswith("rm -f "):
            return CommandEvidence(
                sandbox_id=sid, command=command, exit_code=0,
                stdout="credential_present=yes\n", stderr="",
                duration_ms=evidence.duration_ms, observed_at=evidence.observed_at,
            )
        return evidence

    gateway.run = _undeletable  # type: ignore[method-assign]

    with pytest.raises(SandboxTerminalError) as caught:
        await _bootstrap(gateway, sandbox_id)

    assert "still on disk" in str(caught.value)
    # And the worker it had already started is not left running. There is no
    # receipt for a bootstrap that failed, so the idempotency check cannot
    # see it — a retry would launch a second worker under one node id.
    assert not gateway.live_pids
    assert any(c.startswith("kill ") for c in gateway.commands)


# ---------------------------------------------------------------------------
# The capability the placement gate trusts
# ---------------------------------------------------------------------------


def test_the_launch_sets_sandbox_capable_false_explicitly():
    """Unset already means false to `capabilities.discover`, which reads it
    as `== "true"`. Setting it anyway is the point: unset is a value somebody
    else can supply, and an inherited `true` would make this box placeable
    for public jobs it would run unsandboxed."""
    command = launch_command(
        coordinator_url=COORDINATOR, pool_id=POOL_ID, paths=DEFAULT_PATHS
    )
    assert "FLASHNODE_SANDBOX_CAPABLE=false" in command
    assert "FLASHNODE_SANDBOX_CAPABLE=true" not in command
    assert f"FLASHNODE_POOL={POOL_ID}" in command


@pytest.mark.asyncio
async def test_the_capability_probe_runs_in_the_launch_environment():
    """The read-back is only meaningful if it reproduces the environment the
    agent registered under — two separately-maintained env strings would make
    it an assertion about a different process."""
    gateway, sandbox_id = await _new_sandbox()
    await _bootstrap(gateway, sandbox_id)

    launch = gateway.background_commands[0]
    probe = next(c for c in gateway.commands
                 if "flashnode.inventory.capabilities" in c)
    for assignment in (
        "FLASHNODE_SANDBOX_CAPABLE=false",
        f"FLASHNODE_POOL={POOL_ID}",
        "FLASHNODE_RUNNER=trusted",
    ):
        assert assignment in launch and assignment in probe


@pytest.mark.asyncio
async def test_a_worker_that_registered_sandbox_capable_is_killed_and_refused():
    """The pool gate does NOT keep public work off this machine — a public
    job's payload has no `pool` key, so that gate never runs for it. What
    does is the isolation tier: a public job is placeable only on a node
    whose registration says `sandbox_capable`. An FC sandbox cannot nest a
    container runtime, so a node here that claims it would be handed
    strangers' code to run unsandboxed."""
    gateway, sandbox_id = await _new_sandbox()
    gateway.capabilities = dict(gateway.capabilities, sandbox_capable=True)

    with pytest.raises(SandboxTerminalError) as caught:
        await _bootstrap(gateway, sandbox_id)

    assert "sandbox_capable" in str(caught.value)
    assert any(c.startswith("kill ") for c in gateway.commands)
    assert not gateway.live_pids, "the worker must stop claiming immediately"


@pytest.mark.asyncio
async def test_a_capability_probe_that_will_not_run_fails_closed():
    """An unverifiable capability claim is not a verified one."""
    gateway, sandbox_id = await _new_sandbox()
    gateway.capability_exit = 1

    with pytest.raises(SandboxTerminalError):
        await _bootstrap(gateway, sandbox_id)

    assert not gateway.live_pids


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrapping_a_sandbox_that_already_works_installs_nothing():
    """Two workers under one node id claim leases as each other and fail half
    of them. Detecting the one that is already there is cheaper than
    reconciling that."""
    gateway, sandbox_id = await _new_sandbox()
    first = await _bootstrap(gateway, sandbox_id)
    installs_before = len([c for c in gateway.commands if "pip install" in c])

    second = await _bootstrap(gateway, sandbox_id)

    assert second.reused is True
    assert second.worker_pid == first.worker_pid
    assert second.marker_sha256 == first.marker_sha256
    assert second.node_id == first.node_id
    assert len([c for c in gateway.commands if "pip install" in c]) == installs_before
    assert gateway.launches == 1
    assert [e.type for e in second.observations][-1] == "worker.bootstrap.reused"


@pytest.mark.asyncio
async def test_a_receipt_whose_worker_died_bootstraps_again():
    gateway, sandbox_id = await _new_sandbox()
    first = await _bootstrap(gateway, sandbox_id)
    gateway.live_pids.discard(first.worker_pid)

    second = await _bootstrap(gateway, sandbox_id)

    assert second.reused is False
    assert gateway.launches == 2
    assert second.worker_pid != first.worker_pid


# ---------------------------------------------------------------------------
# The wake path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_confirms_a_worker_that_survived_a_hibernation():
    """Measured 5/5: a running `flashnode work` kept serving lease claims
    straight across a pause, 13 -> 19. The wake re-verifies; it must not
    re-bootstrap."""
    gateway, sandbox_id = await _new_sandbox()
    result = await _bootstrap(gateway, sandbox_id)
    before = len(gateway.commands)

    health = await verify_worker(
        gateway, sandbox_id, expected_marker_sha256=result.marker_sha256,
        expected_pid=result.worker_pid, claim_window_s=0.0,
    )

    assert isinstance(health, WorkerHealth)
    assert health.healthy
    assert health.marker_matches and health.pid_alive and health.claiming
    assert health.credential_absent
    # One round trip, and not one of them an install or a launch.
    assert len(gateway.commands) == before + 1
    assert "pip install" not in gateway.commands[-1]


@pytest.mark.asyncio
async def test_verify_reports_a_marker_that_does_not_match():
    gateway, sandbox_id = await _new_sandbox()
    result = await _bootstrap(gateway, sandbox_id)

    health = await verify_worker(
        gateway, sandbox_id, expected_marker_sha256="0" * 64,
        expected_pid=result.worker_pid, claim_window_s=0.0,
    )

    assert not health.healthy
    assert not health.marker_matches
    assert health.marker_sha256 == result.marker_sha256
    assert health.pid_alive, "the pid check is independent of the marker check"
    assert "marker" in health.detail


@pytest.mark.asyncio
async def test_verify_reports_a_dead_pid():
    gateway, sandbox_id = await _new_sandbox()
    result = await _bootstrap(gateway, sandbox_id)
    gateway.live_pids.discard(result.worker_pid)

    health = await verify_worker(
        gateway, sandbox_id, expected_marker_sha256=result.marker_sha256,
        expected_pid=result.worker_pid, claim_window_s=0.0,
    )

    assert not health.healthy
    assert not health.pid_alive
    assert health.marker_matches, "the filesystem survived even though the worker did not"
    assert not health.claiming
    assert f"pid {result.worker_pid}" in health.detail


@pytest.mark.asyncio
async def test_verify_reports_a_worker_that_is_up_and_not_claiming():
    gateway, sandbox_id = await _new_sandbox()
    result = await _bootstrap(gateway, sandbox_id)
    gateway.unreachable_hits = 7
    gateway.worker_active = False

    health = await verify_worker(
        gateway, sandbox_id, expected_marker_sha256=result.marker_sha256,
        expected_pid=result.worker_pid, claim_window_s=0.0,
    )

    assert not health.healthy
    assert health.pid_alive and health.marker_matches
    assert not health.claiming
    assert "claiming" in health.detail


@pytest.mark.asyncio
async def test_verify_never_writes_anything():
    gateway, sandbox_id = await _new_sandbox()
    result = await _bootstrap(gateway, sandbox_id)
    writes_before = [c for c in gateway.calls if c[0] == "write_file"]

    await verify_worker(
        gateway, sandbox_id, expected_marker_sha256=result.marker_sha256,
        expected_pid=result.worker_pid, claim_window_s=0.0,
    )

    assert [c for c in gateway.calls if c[0] == "write_file"] == writes_before


# ---------------------------------------------------------------------------
# The token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_token_reaches_the_sandbox_and_nowhere_else():
    """The guarantee this module exists for. The token is written as file
    bytes — never interpolated into a command, which would put it in
    `CommandEvidence.command` and from there into the event ledger."""
    gateway, sandbox_id = await _new_sandbox()
    events: list[Observation] = []

    result = await _bootstrap(gateway, sandbox_id, on_observation=events.append)

    # It did arrive: that is the point of the whole module.
    assert TOKEN.encode() in credential_bytes(COORDINATOR, TOKEN)

    for command in gateway.commands:
        assert TOKEN not in command
    for observation in events:
        rendered = json.dumps(redact_data(dict(observation.data)), default=str)
        assert TOKEN not in rendered
        assert TOKEN not in json.dumps(dict(observation.data), default=str)
    assert TOKEN not in repr(result)
    assert TOKEN not in str(result)
    assert TOKEN not in json.dumps([o.type for o in result.observations])


@pytest.mark.asyncio
async def test_a_gateway_error_that_echoes_the_token_is_scrubbed():
    """The SDK is entitled to echo the request it could not complete, and the
    request we hand it has a bearer token in its body. That message must not
    come back out — a token in a traceback is a token in a bug report."""
    gateway, sandbox_id = await _new_sandbox()
    gateway.fail_credential_write_with = (
        f"500: upstream rejected body {{\"https://api.flashml.test\": \"{TOKEN}\"}}"
    )

    with pytest.raises(SandboxTerminalError) as caught:
        await _bootstrap(gateway, sandbox_id)

    assert TOKEN not in str(caught.value)
    assert TOKEN not in repr(caught.value)
    assert TOKEN not in "".join(str(a) for a in caught.value.args)
    assert "[redacted]" in str(caught.value)


@pytest.mark.asyncio
async def test_observations_survive_a_failure_part_way_through():
    """A bootstrap that dies at registration must still leave the install and
    the marker in the ledger — which a tuple returned by a call that raised
    would not."""
    gateway, sandbox_id = await _new_sandbox()
    gateway.capabilities = dict(gateway.capabilities, sandbox_capable=True)
    events: list[Observation] = []

    with pytest.raises(SandboxTerminalError):
        await _bootstrap(gateway, sandbox_id, on_observation=events.append)

    types = [e.type for e in events]
    assert "worker.install.completed" in types
    assert "worker.marker.written" in types
    assert types[-1] == "worker.capabilities.rejected"


@pytest.mark.asyncio
async def test_a_failing_event_sink_does_not_fail_the_bootstrap():
    """Recording is best effort; the sandbox is not. An exception from a
    ledger write on a path that has already put a worker in a sandbox would
    leave the caller unable to tell whether to clean up."""
    gateway, sandbox_id = await _new_sandbox()

    def _explode(_observation):
        raise RuntimeError("the ledger is down")

    result = await _bootstrap(gateway, sandbox_id, on_observation=_explode)

    assert result.worker_pid in gateway.live_pids
