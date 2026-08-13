"""`isolation_probe.py`'s classification rules and shell, offline.

    flashml-cloud/apps/api/.venv/bin/python -m pytest \
        flashml-cloud/scripts/competition/test_isolation_probe.py -q

**No Alibaba, ever.** Nothing here creates a sandbox. What is tested is the
part of C-6.2 a reader would otherwise have to take on trust:

* **the contract.** `Attempt.denied == True` means isolation HELD. `all_denied`
  is a plain AND over the three attempts, and a single leaked attempt must
  never be swallowed into a `True`.
* **the judgment call.** `host_processes` is "a judgment on the output, not an
  exit code" per the requirement — `classify_host_processes` is that judgment,
  pinned here so a future edit that widens the leak threshold or drops the
  PID-1 check fails a test instead of shipping quietly.
* **the redaction.** The same `redact()` used by every sibling probe, pinned
  again here because this file is the one place C-6.2's evidence gets written.
* **the shell.** The three probe commands are `/bin/sh` scripts assembled as
  plain strings (no `.format()` placeholders to lose a brace on), parsed here
  with `/bin/sh -n` so a shell typo costs a test run instead of a live sandbox.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "isolation_probe", HERE / "isolation_probe.py"
    )
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: `@dataclass` looks its class's module up in
    # `sys.modules` and raises an unrelated AttributeError if it is not there.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load()
Attempt = probe.Attempt
IsolationReport = probe.IsolationReport


def _attempt(name: str, denied: bool, signal: str = "s") -> "Attempt":
    return Attempt(name=name, command="c", denied=denied, signal=signal, raw_tail="")


def _report(attempts: list, **overrides) -> "IsolationReport":
    kwargs = dict(
        region="ap-southeast-1", api_url="https://api", domain="dom",
        template=probe.TEMPLATE, sdk_version="2.31.0", sandbox_id="sbx_1",
        started_at="t0", finished_at="t1", attempts=attempts,
        all_denied=all(a.denied for a in attempts) if attempts else False,
        killed=True, error=None,
    )
    kwargs.update(overrides)
    return IsolationReport(**kwargs)


# ---------------------------------------------------------------------------
# the contract: all_denied is a plain AND, never a swallowed leak
# ---------------------------------------------------------------------------


def test_all_denied_is_the_and_over_attempts():
    denied = [_attempt("a", True), _attempt("b", True)]
    report = _report(denied)
    assert report.all_denied is True

    mixed = denied + [_attempt("c", False, "leaked!")]
    assert all(a.denied for a in mixed) is False


def test_a_leak_is_never_reported_as_denied():
    # the classifier for host_processes: a large process count, or a real
    # host init at PID 1, means the probe FAILED (isolation leaked).
    assert probe.classify_host_processes(proc_count=412, pid1_cmd="/sbin/init") is False
    assert probe.classify_host_processes(proc_count=3, pid1_cmd="/opt/e2b/init") is True


def test_a_small_table_with_a_real_host_init_is_still_a_leak():
    """Both signals must hold. A small count alone is not enough if PID 1
    looks like a real host's init — a task that saw only a handful of host
    processes has still seen the host."""
    assert probe.classify_host_processes(proc_count=5, pid1_cmd="/sbin/init") is False
    assert probe.classify_host_processes(proc_count=5, pid1_cmd="systemd") is False


def test_a_large_table_with_a_sandbox_init_is_still_a_leak():
    """And the reverse: a sandbox-shaped PID 1 does not excuse a process
    count that looks like a shared host."""
    assert probe.classify_host_processes(proc_count=500, pid1_cmd="/opt/e2b/init") is False


def test_run_probe_never_marks_all_denied_true_with_zero_attempts():
    """An empty attempts list must not vacuously satisfy `all()`."""
    report = _report([])
    assert report.all_denied is False


# ---------------------------------------------------------------------------
# redaction
# ---------------------------------------------------------------------------


def test_redact_strips_the_key(monkeypatch):
    monkeypatch.setenv("E2B_API_KEY", "e2b_supersecretvalue_1234")
    assert "e2b_supersecretvalue_1234" not in probe.redact(
        'api_key: "e2b_supersecretvalue_1234"'
    )


def test_redact_strips_an_oss_secret_env_value(monkeypatch):
    monkeypatch.setenv("OSS_SECRET_KEY", "sekrit-value-0123456789")
    assert "sekrit-value-0123456789" not in probe.redact(
        "boom: sekrit-value-0123456789 refused"
    )


# ---------------------------------------------------------------------------
# curl classification (forbidden_host)
# ---------------------------------------------------------------------------


def test_a_curl_timeout_is_denied_and_named_timeout():
    denied, signal = probe._classify_curl("000", 28, "curl: (28) Operation timed out")
    assert denied is True
    assert signal == "timeout"


def test_a_curl_refusal_is_denied_and_named_distinctly_from_a_timeout():
    denied, signal = probe._classify_curl("000", 7, "curl: (7) Failed to connect")
    assert denied is True
    assert signal == "Connection refused"


def test_a_2xx_response_from_the_forbidden_host_is_a_leak():
    denied, signal = probe._classify_curl("200", 0, "")
    assert denied is False
    assert "LEAK" in signal


def test_a_non_2xx_response_still_counts_as_denied():
    denied, signal = probe._classify_curl("403", 0, "")
    assert denied is True
    assert "403" in signal


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def test_parse_kv_keeps_values_containing_equals():
    parsed = probe.parse_kv("a=1\nb=x=y\nnoise\n")
    assert parsed == {"a": "1", "b": "x=y"}


def test_numeric_coercion_returns_none_rather_than_raising():
    assert probe.as_int("7") == 7
    assert probe.as_int(None) is None
    assert probe.as_int("") is None


def test_looks_like_allowlist_matches_case_insensitively():
    assert probe.looks_like_allowlist("Forbidden: your account is not authorized")
    assert not probe.looks_like_allowlist("connection reset by peer")


# ---------------------------------------------------------------------------
# ids bookkeeping
# ---------------------------------------------------------------------------


def test_ids_are_recorded_to_disk_the_instant_a_sandbox_exists(tmp_path, capsys):
    """A `kill -9` between create and cleanup must still leave a human a list."""
    ids = probe.Ids(tmp_path / "run.sandboxes")
    ids.add("sbx_abc", "isolation probe")
    ids.add("", "never created")
    assert ids.ids == ["sbx_abc"]
    assert "sbx_abc" in (tmp_path / "run.sandboxes").read_text()
    assert "sbx_abc" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# the shell
# ---------------------------------------------------------------------------


def test_the_shell_commands_are_valid_sh():
    for cmd in (probe.FS_ESCAPE_CMD, probe.FORBIDDEN_HOST_CMD, probe.HOST_PROC_CMD):
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
            f.write(cmd)
            p = f.name
        try:
            assert subprocess.run(["/bin/sh", "-n", p]).returncode == 0
        finally:
            os.unlink(p)


def test_the_filesystem_escape_command_checks_both_read_and_write():
    assert "EXIT_READ=" in probe.FS_ESCAPE_CMD
    assert "EXIT_WRITE=" in probe.FS_ESCAPE_CMD


def test_the_forbidden_host_command_checks_two_distinct_hosts():
    assert "100.100.100.200" in probe.FORBIDDEN_HOST_CMD
    assert "169.254.169.254" in probe.FORBIDDEN_HOST_CMD
    assert "EXIT_META=" in probe.FORBIDDEN_HOST_CMD
    assert "EXIT_GENERIC=" in probe.FORBIDDEN_HOST_CMD


def test_the_host_processes_command_reads_the_pid_namespace_not_just_ps():
    assert "PROC_COUNT=" in probe.HOST_PROC_CMD
    assert "PID1_CMD=" in probe.HOST_PROC_CMD
    assert "/proc/1/cmdline" in probe.HOST_PROC_CMD
