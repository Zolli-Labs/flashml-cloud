"""`hibernation_modes_probe.py`'s arithmetic and its honesty, offline.

    flashml-cloud/apps/api/.venv/bin/python -m pytest \
        flashml-cloud/scripts/competition/test_hibernation_modes_probe.py -q

**No Alibaba, ever.** Nothing here creates a sandbox. What is tested is the
part of that script a reader would otherwise have to take on trust:

* **the money.** `hourly_rates` reproduces the published-rate table in
  `docs/superpowers/specs/2026-08-11-alibaba-submission-dossier.md` §3 to the
  last decimal. That table is what a cost claim is quoted from, and an
  arithmetic slip in it is a wrong number on a slide that nobody re-derives.
  The deep-hibernation rule in particular — `memory x 2 + disk`, and the 15 GiB
  free allowance **not** applying — is the one everybody gets wrong.

* **the honesty.** `summarise` decides, from the measured numbers alone,
  whether light hibernation may be claimed. Three tests pin the three
  outcomes: sub-100 ms resumes say MEASURED, ~1 s resumes say NOT REACHED and
  say plainly that no comparison was run, and an empty run says UNVERIFIED
  rather than reporting a zero. Those strings are the house rules made
  executable — a future edit that softens one fails here.

* **the shell.** The in-sandbox scripts are `str.format` templates containing
  awk braces and shell arithmetic, which is exactly the shape that silently
  loses a brace. They are compiled (Python) and parsed (`/bin/sh -n`) here so
  a formatting mistake costs a test run instead of a 25-minute sandbox run.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "hibernation_modes_probe", HERE / "hibernation_modes_probe.py"
    )
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: `@dataclass` looks its class's module up in
    # `sys.modules` and raises an unrelated AttributeError if it is not there.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load()


# ---------------------------------------------------------------------------
# the money
# ---------------------------------------------------------------------------


def test_rates_reproduce_the_dossier_table_exactly():
    """2 vCPU / 4 GiB / 30 GiB, mainland — the shape §3 of the dossier prices."""
    rates = probe.hourly_rates(vcpu=2, memory_gib=4.0, disk_gib=30.0, mainland=True)
    assert round(rates["active"], 6) == 0.079664
    assert round(rates["light_hibernation"], 6) == 0.042224
    assert round(rates["deep_hibernation"], 6) == 0.012120


def test_rates_reproduce_the_dossier_table_outside_mainland():
    rates = probe.hourly_rates(vcpu=2, memory_gib=4.0, disk_gib=30.0, mainland=False)
    assert round(rates["active"], 6) == 0.078676
    assert round(rates["deep_hibernation"], 6) == 0.009617


def test_free_disk_applies_to_active_and_light_but_never_to_deep():
    """A 10 GiB sandbox is under the 15 GiB allowance, so active and light pay
    no disk at all — while deep pays for `memory x 2 + disk`, all of it."""
    rates = probe.hourly_rates(vcpu=2, memory_gib=2.0, disk_gib=10.0)
    assert rates["active"] == pytest.approx(2 * probe.RATE_VCPU_HR + 2 * probe.RATE_MEM_GIB_HR)
    assert rates["light_hibernation"] == pytest.approx(2 * probe.RATE_MEM_GIB_HR)
    assert rates["deep_hibernation"] == pytest.approx(
        (2 * 2 + 10) * probe.RATE_DISK_GIB_HR_INTL
    )


def test_local_bytes_are_paid_for_through_the_whole_hibernation():
    """The arithmetic behind "environment stays, data streams" (dossier §7.3).

    Deep hibernation bills `memory x 2 + disk` with **no** free allowance, so
    every byte parked on the sandbox's own disk is paid for, per hour, for the
    entire wait. Moving the model and dataset to OSS shrinks a 4 GiB / 30 GiB
    evaluator to 4 GiB / 15 GiB and takes ~39.5% off the hibernation bill,
    changing nothing else. Pinned here because it is a published claim.
    """
    fat = probe.hourly_rates(vcpu=2, memory_gib=4.0, disk_gib=30.0, mainland=True)
    lean = probe.hourly_rates(vcpu=2, memory_gib=4.0, disk_gib=15.0, mainland=True)
    assert round(fat["deep_hibernation"], 6) == 0.012120
    assert round(lean["deep_hibernation"], 6) == 0.007336
    saving = 1 - lean["deep_hibernation"] / fat["deep_hibernation"]
    assert round(100 * saving, 1) == 39.5


def test_cost_for_is_pro_rated_and_never_negative():
    assert probe.cost_for(3600, 0.05616) == pytest.approx(0.05616)
    assert probe.cost_for(1800, 0.05616) == pytest.approx(0.02808)
    assert probe.cost_for(-5, 0.05616) == 0.0


def test_crossover_is_where_rebuilding_becomes_cheaper_than_waiting():
    rates = probe.hourly_rates(vcpu=2, memory_gib=2.0, disk_gib=10.0)
    seconds = probe.crossover_seconds(30.0, rates["active"], rates["deep_hibernation"])
    # Equal cost at the crossover, by construction.
    assert probe.cost_for(30.0, rates["active"]) == pytest.approx(
        probe.cost_for(seconds, rates["deep_hibernation"])
    )


def test_crossover_is_none_when_hibernating_is_free():
    assert probe.crossover_seconds(30.0, 0.05, 0.0) is None


# ---------------------------------------------------------------------------
# parsing and statistics
# ---------------------------------------------------------------------------


def test_percentile_is_nearest_rank_so_it_returns_a_real_sample():
    values = [100.0, 200.0, 300.0, 400.0, 5000.0]
    assert probe.pct(values, 0.95) in values
    assert probe.pct(values, 0.5) in values
    assert probe.pct([], 0.95) is None


def test_parse_kv_keeps_values_containing_equals():
    parsed = probe.parse_kv("a=1\nb=x=y\nnoise\n")
    assert parsed == {"a": "1", "b": "x=y"}


def test_numeric_coercion_returns_none_rather_than_raising():
    assert probe.as_float("1.5") == 1.5
    assert probe.as_float("") is None
    assert probe.as_int("7") == 7
    assert probe.as_int(None) is None


# ---------------------------------------------------------------------------
# the in-sandbox scripts
# ---------------------------------------------------------------------------


def test_the_python_templates_format_and_compile():
    compile(probe.DAEMON_SRC.format(state=probe.STATE_DIR), "daemon", "exec")
    compile(probe.SHARD_SRC.format(warm=probe.WARM_DIR, state=probe.STATE_DIR),
            "shard", "exec")
    compile(probe.IMPORT_SRC.format(modules=list(probe.WARM_PACKAGES)),
            "import", "exec")


def test_the_import_check_covers_every_package_the_cache_holds():
    source = probe.IMPORT_SRC.format(modules=list(probe.WARM_PACKAGES))
    for package in probe.WARM_PACKAGES:
        assert repr(package) in source


def test_the_verify_script_is_valid_shell(tmp_path):
    script = tmp_path / "fm_verify.sh"
    script.write_text(probe.VERIFY_SRC.format(warm=probe.WARM_DIR, state=probe.STATE_DIR))
    assert subprocess.run(["/bin/sh", "-n", str(script)]).returncode == 0


def test_the_verify_script_never_signals_process_group_zero():
    """`kill -USR1 0` signals the whole process group — envd included.

    An unreadable pidfile leaves PID=0, so the guard is what stands between a
    missing file and signalling the sandbox's own agent.
    """
    body = probe.VERIFY_SRC.format(warm=probe.WARM_DIR, state=probe.STATE_DIR)
    assert '[ "$PID" -gt 0 ]' in body
    assert 'kill -USR1 "$PID"' in body


def test_the_daemon_writes_only_the_digest_to_disk():
    """The whole point of the RAM check: the secret must never be on disk, or
    surviving the wake would prove nothing beyond the filesystem surviving."""
    source = probe.DAEMON_SRC.format(state=probe.STATE_DIR)
    assert "secret.sha256" in source and "hashlib.sha256(SECRET.encode())" in source
    # The only write of SECRET itself is inside the signal handler.
    before_handler, _, handler = source.partition("def _reveal")
    assert "fh.write(SECRET)" not in before_handler
    assert "fh.write(SECRET)" in handler


# ---------------------------------------------------------------------------
# the evidence record
# ---------------------------------------------------------------------------


def test_state_is_fully_intact_only_when_all_seven_checks_pass():
    kwargs = dict(
        marker_intact=True, pid_alive=True, pid_same_process=True,
        boot_id_unchanged=True, ram_secret_recovered=True,
        heartbeat_advanced=True, cache_manifest_unchanged=True,
    )
    assert probe.Boundary(index=1, keep_memory=True, planned_wait_s=1,
                          **kwargs).state_fully_intact
    for missing in kwargs:
        partial = dict(kwargs, **{missing: False})
        assert not probe.Boundary(index=1, keep_memory=True, planned_wait_s=1,
                                  **partial).state_fully_intact


def test_ids_are_recorded_to_disk_the_instant_a_sandbox_exists(tmp_path, capsys):
    """A `kill -9` between create and cleanup must still leave a human a list."""
    ids = probe.Ids(tmp_path / "run.sandboxes")
    ids.add("sbx_abc", "cell one")
    ids.add("", "never created")
    assert ids.ids == ["sbx_abc"]
    assert "sbx_abc" in (tmp_path / "run.sandboxes").read_text()
    assert "sbx_abc" in capsys.readouterr().out


def _report(wake_ms_true: list[float], wake_ms_false: list[float]) -> "probe.Report":
    report = probe.Report(long_wait_s=300.0, short_wait_s=15.0)
    for name, keep, waves in (("snapshot/long", True, wake_ms_true),
                              ("nomemory/long", False, wake_ms_false)):
        cell = probe.Cell(name=name, keep_memory=keep, wait_s=300.0,
                          repeats=len(waves), sandbox_id=f"sbx_{name}")
        cell.inventory = {"nproc": "2", "mem_total_kb": "2140076", "disk_total_mb": "10044"}
        cell.baseline = {"cache_files": "3", "cache_bytes": "40000000",
                         "deps_versions": "numpy==2.3.1 scipy==1.14.0"}
        cell.cold_install_ms = 9000.0
        cell.warm_rebuild_ms = 1500.0
        cell.rebuild_seconds = 30.0
        for index, wake in enumerate(waves, start=1):
            cell.boundaries.append(probe.Boundary(
                index=index, keep_memory=keep, planned_wait_s=300.0,
                pause_ms=2600.0, wake_ms=wake, hibernated_s=300.0,
                marker_intact=True, pid_alive=True, pid_same_process=True,
                boot_id_unchanged=True, ram_secret_recovered=True,
                heartbeat_advanced=True, cache_manifest_unchanged=True,
                shard_ok=True, guest_clock_frozen=True,
            ))
        report.cells.append(cell)
    return report


def test_a_one_second_resume_refuses_to_claim_light_hibernation():
    report = _report([1085.0, 1100.0], [1200.0, 1150.0])
    probe.summarise(report)
    finding = report.mode_finding
    assert "NOT REACHED" in finding["light_hibernation_status"]
    assert "no comparison between the two\nwas run" in finding["light_hibernation_status"] \
        or "no comparison" in finding["light_hibernation_status"]
    assert finding["consistent_with"].startswith("deep hibernation")
    assert finding["what_would_settle_it"]


def test_two_barely_disjoint_ranges_are_not_reported_as_a_difference():
    """The n=2 trap, and the reason the separation test is not "ranges disjoint".

    Two samples of one distribution rarely interleave, so disjoint ranges alone
    would report a mode difference on pure noise — which is precisely a
    comparison we did not run being implied.
    """
    report = _report([1353.5, 1354.9], [1364.4, 1486.9])
    probe.summarise(report)
    finding = report.mode_finding
    assert finding["separation_gap_ms"] == 9.5      # the ranges ARE disjoint
    assert finding["within_mode_spread_ms"] == 122.5  # ...by less than the noise
    assert finding["selectors_separated_beyond_noise"] is False


def test_a_gap_that_beats_the_noise_is_reported_as_separated():
    report = _report([1000.0, 1010.0], [4000.0, 4020.0])
    probe.summarise(report)
    assert report.mode_finding["selectors_separated_beyond_noise"] is True


def test_a_millisecond_resume_does_claim_light_hibernation():
    report = _report([40.0, 45.0], [42.0, 38.0])
    probe.summarise(report)
    assert report.mode_finding["light_hibernation_status"].startswith("MEASURED")
    assert "light hibernation" in report.mode_finding["consistent_with"]


def test_a_run_that_measured_nothing_reports_unverified_not_zero():
    report = probe.Report(long_wait_s=300.0, short_wait_s=15.0)
    probe.summarise(report)
    assert report.mode_finding["light_hibernation_status"].startswith("UNVERIFIED")
    assert report.summary["wake_ms_by_mode"]["keep_memory=True"]["n"] == 0
    assert report.summary["wake_ms_by_mode"]["keep_memory=True"]["p50_ms"] is None


def test_summary_counts_every_continuity_check_over_every_boundary():
    report = _report([1085.0, 1100.0], [1200.0, 1150.0])
    probe.summarise(report)
    continuity = report.summary["state_continuity"]
    assert continuity["all_seven_intact"] == "4/4"
    assert continuity["ram_secret_recovered"] == "4/4"
    assert continuity["cache_manifest_unchanged"] == "4/4"


def test_cost_names_its_baseline_and_volunteers_the_inconvenient_one():
    report = _report([1085.0, 1100.0], [1200.0, 1150.0])
    probe.summarise(report)
    probe.cost_section(report, mainland=False)
    cost = report.cost
    assert cost["status"].startswith("MODELLED")
    assert cost["baseline"]["chosen"].startswith("Baseline A")
    # 4 boundaries x 300 s hibernated, priced at both rates because which SKU
    # applied is not observable.
    assert cost["baseline"]["measured_hibernated_seconds"] == 1200.0
    assert cost["baseline"]["active_cost_usd"] > cost["baseline"]["deep_hibernation_cost_usd"]
    assert "UNVERIFIED" in cost["baseline"]["which_rate_applied"]
    assert cost["baseline_b"]["measured_rebuild_seconds"] == 30.0
    assert cost["baseline_b"]["crossover_seconds_vs_deep"] > 0
    assert cost["measured_sandbox_shape"]["vcpu"] == 2.0


def test_cost_says_unverified_when_no_sandbox_reported_its_shape():
    report = probe.Report()
    probe.summarise(report)
    probe.cost_section(report, mainland=False)
    assert report.cost["status"].startswith("UNVERIFIED")


def test_redaction_removes_the_key_from_anything_printed(monkeypatch):
    monkeypatch.setenv("E2B_API_KEY", "sk-not-a-real-key-0123456789")
    assert "sk-not-a-real-key" not in probe.redact(
        "boom: sk-not-a-real-key-0123456789 refused"
    )
