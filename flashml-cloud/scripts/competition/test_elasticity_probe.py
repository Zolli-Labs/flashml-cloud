"""`elasticity_probe.py`'s classification and arithmetic, offline.

    flashml-cloud/apps/api/.venv/bin/python -m pytest \
        flashml-cloud/scripts/competition/test_elasticity_probe.py -q

**No Alibaba, ever.** Nothing here creates a sandbox, reads a key, or opens a
socket. What is tested is the part of C-6.1 a reader would otherwise have to
take on trust:

* **the ladder.** `parse_ladder` must reject a descending or repeating ladder,
  because "stop ascending at the first saturated rung" is meaningless on one
  and `choose_cap` cannot answer on the other.
* **the failure classes.** A 429 or a quota refusal is the *result* of an
  elasticity probe. `classify_failure` is pinned here — including the
  documented 429-beats-quota precedence — so a future edit that folds a
  throttle into a generic bucket fails a test instead of quietly deleting the
  only number a judge wanted.
* **the verdicts.** `clean` requires zero failures AND a confirmed cleanup;
  `degraded` is not `clean` and is never offered as a cap; a kill failure
  dominates every other signal on the row.
* **the cap.** `choose_cap` must say "top of the ladder, not a measured
  ceiling" when nothing failed — the honest negative finding — and must return
  `None` rather than inventing a cap when no rung ran clean.
* **the cleanup discipline.** A failed kill records the sandbox id and the
  error, unconfirms the rung, and drives the exit code to 2.
* **the labelling.** Every field of a rung row must appear in
  `FIELD_PROVENANCE`, so no figure can ship without saying whether it was
  measured, derived, or quoted.
* **`--dry-run`.** Validates the config and prints the plan without importing
  the SDK, touching the network, or needing a key.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "elasticity_probe", HERE / "elasticity_probe.py"
    )
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: `@dataclass` looks its class's module up in
    # `sys.modules` and raises an unrelated AttributeError if it is not there.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load()
CreateOutcome = probe.CreateOutcome


def _ok(index: int, latency_ms: float = 900.0) -> "CreateOutcome":
    return CreateOutcome(index=index, sandbox_id=f"sbx_{index}", ok=True,
                         latency_ms=latency_ms, failure_class="", error="")


def _bad(index: int, cls: str, latency_ms: float = 5000.0) -> "CreateOutcome":
    return CreateOutcome(index=index, sandbox_id="", ok=False,
                         latency_ms=latency_ms, failure_class=cls, error="boom")


def _row(concurrency: int, outcomes: list, *, cleanup=None, threshold: float = 0.2,
         wall: float = 2.0) -> dict:
    return probe.rung_row(
        concurrency=concurrency, in_flight=concurrency, outcomes=outcomes,
        wall_clock_s=wall, cleanup=cleanup or probe.cleanup_result(
            len([o for o in outcomes if o.ok]),
            [(o.sandbox_id, True, "") for o in outcomes if o.ok],
        ),
        threshold=threshold,
    )


class _Listed:
    """The shape `Sandbox.list(...).next_items()` yields, as far as the sweep
    reads it."""

    def __init__(self, sandbox_id: str, metadata: dict | None = None) -> None:
        self.sandbox_id = sandbox_id
        self.metadata = metadata or {}


# ---------------------------------------------------------------------------
# the ladder
# ---------------------------------------------------------------------------


def test_parse_ladder_accepts_a_comma_list():
    assert probe.parse_ladder("1,5,10,25") == [1, 5, 10, 25]
    assert probe.parse_ladder(" 1 , 2 ,4 ") == [1, 2, 4]
    assert probe.parse_ladder(probe.DEFAULT_LADDER) == [1, 2, 4, 8]


def test_parse_ladder_rejects_a_descending_ladder():
    """The stop-ascending rule needs the ladder to ascend; on a descending one
    "do not climb further" would silently mean "do not descend further"."""
    with pytest.raises(ValueError, match="ascend"):
        probe.parse_ladder("8,4,1")


def test_parse_ladder_rejects_repeats_zero_negatives_and_junk():
    with pytest.raises(ValueError, match="repeats"):
        probe.parse_ladder("2,4,4")
    with pytest.raises(ValueError, match=">= 1"):
        probe.parse_ladder("1,0,4")
    with pytest.raises(ValueError, match=">= 1"):
        probe.parse_ladder("-3")
    with pytest.raises(ValueError, match="not an integer"):
        probe.parse_ladder("1,many")
    with pytest.raises(ValueError, match="empty ladder"):
        probe.parse_ladder("  ,, ")


def test_plan_rung_shapes_the_wave():
    # per_level 0 -> one wave of exactly the rung
    assert probe.plan_rung(8, 0) == (8, 8)
    # a larger per_level keeps the rung in flight and refills
    assert probe.plan_rung(4, 20) == (20, 4)
    # asking for fewer than the rung would label a measurement with a
    # concurrency the run never reached, so the total is raised to the rung
    assert probe.plan_rung(10, 3) == (10, 10)


def test_ladder_warnings_flag_a_rung_above_the_quoted_account_cap():
    warnings = probe.ladder_warnings([1, 200], per_level=0)
    assert any("200" in w and "quoted, not measured" in w for w in warnings)
    assert probe.ladder_warnings([1, 2, 4], per_level=0) == []


def test_estimated_cost_is_an_upper_bound():
    # 15 sandboxes x 120 s TTL x $0.08/hr, priced as if none were killed early
    assert probe.estimate_cost_usd(15, 120) == pytest.approx(0.04, abs=0.005)
    assert probe.estimate_cost_usd(0, 3600) == 0.0


# ---------------------------------------------------------------------------
# latency and rate
# ---------------------------------------------------------------------------


def test_p95_is_nearest_rank_and_never_interpolated():
    """An interpolated p95 over n=4 invents a value between two samples and
    reads as more precise than the data is."""
    values = [100.0, 200.0, 300.0, 400.0]
    assert probe.pct(values, 0.95) == 400.0
    assert probe.pct(values, 0.5) == 200.0
    assert probe.pct([], 0.95) is None


def test_latency_stats_on_no_samples_reports_none_not_zero():
    stats = probe.latency_stats([])
    assert stats["n"] == 0
    assert stats["p50_ms"] is None and stats["p95_ms"] is None
    assert stats["min_ms"] is None and stats["mean_ms"] is None


def test_creates_per_sec_is_derived_and_declines_to_divide_by_nothing():
    assert probe.creates_per_sec(8, 2.0) == 4.0
    assert probe.creates_per_sec(8, 0.0) is None
    assert probe.creates_per_sec(0, 2.0) == 0.0


# ---------------------------------------------------------------------------
# failure classification — the interesting result, never folded into "failed"
# ---------------------------------------------------------------------------


def test_a_429_is_a_throttle_and_never_a_generic_failure():
    assert probe.classify_failure("SandboxException", "429: too many requests") \
        == probe.CLASS_THROTTLE
    assert probe.classify_failure("RateLimitException", "rate limited") \
        == probe.CLASS_THROTTLE
    assert probe.CLASS_THROTTLE in probe.CEILING_CLASSES


def test_a_worded_quota_refusal_with_no_429_is_its_own_class():
    assert probe.classify_failure(
        "SandboxException", "403: concurrency limit for this account reached"
    ) == probe.CLASS_QUOTA
    assert probe.classify_failure(
        "SandboxException", "instance quota exceeded"
    ) == probe.CLASS_QUOTA
    assert probe.CLASS_QUOTA in probe.CEILING_CLASSES


def test_a_429_whose_body_also_says_quota_is_still_a_throttle():
    """Documented precedence: the status is the platform's own answer about
    why *now*, and it is retryable in a way a standing account cap is not.
    Pinned so the precedence cannot drift silently."""
    assert probe.classify_failure(
        "SandboxException", "429: concurrency quota exceeded"
    ) == probe.CLASS_THROTTLE


def test_ordinary_failures_stay_apart_from_each_other():
    assert probe.classify_failure("AuthenticationException", "bad key") == probe.CLASS_AUTH
    assert probe.classify_failure("SandboxException", "401: unauthorized") == probe.CLASS_AUTH
    assert probe.classify_failure("TimeoutError", "") == probe.CLASS_TIMEOUT
    assert probe.classify_failure("SandboxException", "503: upstream") == probe.CLASS_SERVER_ERROR
    assert probe.classify_failure("SandboxException", "400: bad template") == probe.CLASS_CLIENT_ERROR
    assert probe.classify_failure("ConnectionResetError", "peer reset") == probe.CLASS_TRANSPORT


def test_an_unrecognised_failure_is_unknown_never_a_ceiling():
    """Fails safe. Guessing `throttle` here would manufacture the exact
    finding this probe exists to report honestly."""
    cls = probe.classify_failure("WeirdError", "something we have never seen")
    assert cls == probe.CLASS_UNKNOWN
    assert cls not in probe.CEILING_CLASSES


def test_classes_that_may_still_have_created_a_sandbox_are_named():
    """`asyncio.wait_for` cancels the await, not the thread — a timed-out
    create may have succeeded server-side under an id we never saw."""
    assert probe.CLASS_TIMEOUT in probe.MAY_HAVE_CREATED_CLASSES
    assert probe.CLASS_TRANSPORT in probe.MAY_HAVE_CREATED_CLASSES
    assert probe.CLASS_AUTH not in probe.MAY_HAVE_CREATED_CLASSES


# ---------------------------------------------------------------------------
# cleanup discipline
# ---------------------------------------------------------------------------


def test_a_kill_failure_records_the_id_and_the_error_and_unconfirms():
    result = probe.cleanup_result(2, [("sbx_a", True, ""), ("sbx_b", False, "500: nope")])
    assert result["killed"] == 1
    assert result["failures"] == [{"sandbox_id": "sbx_b", "error": "500: nope"}]
    assert result["confirmed"] is False


def test_cleanup_is_unconfirmed_when_fewer_kills_were_attempted_than_handles():
    """A rung that never got round to killing one of its sandboxes must not
    report a confirmed cleanup just because nothing raised."""
    assert probe.cleanup_result(3, [("a", True, ""), ("b", True, "")])["confirmed"] is False
    assert probe.cleanup_result(0, [])["confirmed"] is True


# ---------------------------------------------------------------------------
# rung verdicts
# ---------------------------------------------------------------------------


def test_clean_needs_zero_failures_and_a_confirmed_cleanup():
    assert probe.rung_verdict(8, {}, True, 0.2) == probe.VERDICT_CLEAN


def test_a_failure_below_the_threshold_is_degraded_not_clean():
    """One in ten is a tail, not a ceiling — it does not stop the ladder, and
    it is not eligible to be reported as the chosen cap."""
    verdict = probe.rung_verdict(10, {probe.CLASS_UNKNOWN: 1}, True, 0.2)
    assert verdict == probe.VERDICT_DEGRADED
    assert probe.should_stop_ascending(verdict) is False


def test_a_failure_rate_at_the_threshold_saturates_and_stops_the_ladder():
    verdict = probe.rung_verdict(10, {probe.CLASS_THROTTLE: 2}, True, 0.2)
    assert verdict == probe.VERDICT_SATURATED
    assert probe.should_stop_ascending(verdict) is True


def test_a_kill_failure_dominates_every_other_signal():
    """All eight creates succeeded, but one sandbox is still billing. That is
    not a clean rung."""
    verdict = probe.rung_verdict(8, {}, False, 0.2)
    assert verdict == probe.VERDICT_CLEANUP_UNCONFIRMED
    assert probe.should_stop_ascending(verdict) is True


# ---------------------------------------------------------------------------
# the chosen cap + rationale — the C-6.1 evidence sentence
# ---------------------------------------------------------------------------


def test_the_cap_is_the_highest_CLEAN_rung_not_the_highest_rung_that_ran():
    rows = [
        _row(1, [_ok(0)]),
        _row(2, [_ok(0), _ok(1)]),
        _row(4, [_ok(i) for i in range(3)] + [_bad(3, probe.CLASS_THROTTLE)]),
    ]
    assert [r["verdict"] for r in rows] == [
        probe.VERDICT_CLEAN, probe.VERDICT_CLEAN, probe.VERDICT_SATURATED]
    chosen = probe.choose_cap(rows, [1, 2, 4, 8])
    assert chosen["chosen_cap"] == 2
    assert chosen["stopped_early"] is True
    assert "throttle" in chosen["cap_rationale"]


def test_a_degraded_rung_is_never_offered_as_the_cap():
    rows = [
        _row(1, [_ok(0)]),
        _row(10, [_ok(i) for i in range(9)] + [_bad(9, probe.CLASS_UNKNOWN)]),
    ]
    assert rows[1]["verdict"] == probe.VERDICT_DEGRADED
    assert probe.choose_cap(rows, [1, 10])["chosen_cap"] == 1


def test_an_unbroken_ladder_reports_a_floor_not_a_ceiling():
    """The honest negative finding: nothing failed, so the ladder ran out
    before the platform did and the real ceiling was never measured."""
    rows = [_row(1, [_ok(0)]), _row(2, [_ok(0), _ok(1)])]
    chosen = probe.choose_cap(rows, [1, 2])
    assert chosen["chosen_cap"] == 2
    assert "not a measured ceiling" in chosen["cap_rationale"]
    assert chosen["stopped_early"] is False


def test_no_clean_rung_yields_no_cap_and_says_so():
    rows = [_row(1, [_bad(0, probe.CLASS_AUTH)])]
    chosen = probe.choose_cap(rows, [1, 2, 4])
    assert chosen["chosen_cap"] is None
    assert "NO CAP CHOSEN" in chosen["cap_rationale"]


# ---------------------------------------------------------------------------
# rows, summary, provenance
# ---------------------------------------------------------------------------


def test_every_rung_field_is_labelled_measured_derived_or_quoted():
    """A figure that ships without a provenance entry is a figure a judge
    cannot check. Set equality, so a new field cannot slip in unlabelled and a
    stale label cannot outlive its field."""
    row = _row(4, [_ok(0), _ok(1), _bad(2, probe.CLASS_THROTTLE)])
    assert set(row) == set(probe.FIELD_PROVENANCE)
    for label in probe.FIELD_PROVENANCE.values():
        assert any(word in label for word in ("measured", "derived", "config", "quoted"))


def test_a_rung_row_keeps_success_and_failure_latencies_apart():
    row = _row(2, [_ok(0, 900.0), _bad(1, probe.CLASS_TIMEOUT, 120000.0)])
    assert row["latency_ms"]["n"] == 1 and row["latency_ms"]["p50_ms"] == 900.0
    assert row["failed_latency_ms"]["n"] == 1
    assert row["failed_latency_ms"]["p50_ms"] == 120000.0
    assert row["may_have_created"] == 1


def test_the_summary_sums_each_class_separately_and_never_collapses_them():
    rows = [
        _row(4, [_ok(0), _bad(1, probe.CLASS_THROTTLE), _bad(2, probe.CLASS_QUOTA)]),
        _row(8, [_bad(0, probe.CLASS_THROTTLE)]),
    ]
    summary = probe.summarize(rows, [4, 8], still_live=[])
    assert summary["failed_by_class"] == {probe.CLASS_QUOTA: 1, probe.CLASS_THROTTLE: 2}
    assert summary["ceiling_failures"] == 3
    assert summary["total_attempted"] == 4 and summary["total_created_ok"] == 1


# ---------------------------------------------------------------------------
# exit codes
# ---------------------------------------------------------------------------


def test_exit_zero_only_when_a_cap_was_measured_and_nothing_is_left_alive():
    rows = [_row(1, [_ok(0)]), _row(2, [_ok(0), _ok(1)])]
    summary = probe.summarize(rows, [1, 2], still_live=[])
    assert probe.overall_exit(summary, None) == 0


def test_a_throttled_top_rung_is_still_exit_zero_because_it_is_the_result():
    rows = [
        _row(1, [_ok(0)]),
        _row(4, [_bad(i, probe.CLASS_THROTTLE) for i in range(4)]),
    ]
    summary = probe.summarize(rows, [1, 4], still_live=[])
    assert summary["chosen_cap"] == 1
    assert probe.overall_exit(summary, None) == 0


def test_an_unconfirmed_cleanup_or_a_live_survivor_forces_exit_two():
    rows = [_row(1, [_ok(0)], cleanup=probe.cleanup_result(
        1, [("sbx_0", False, "kill refused")]))]
    summary = probe.summarize(rows, [1], still_live=[])
    assert probe.overall_exit(summary, None) == 2

    clean_rows = [_row(1, [_ok(0)])]
    live = probe.summarize(clean_rows, [1], still_live=["sbx_ghost"])
    assert probe.overall_exit(live, None) == 2


def test_no_rung_and_a_harness_error_both_force_exit_two():
    assert probe.overall_exit(probe.summarize([], [1, 2], still_live=[]), None) == 2
    good = probe.summarize([_row(1, [_ok(0)])], [1], still_live=[])
    assert probe.overall_exit(good, "ImportError: e2b missing") == 2


# ---------------------------------------------------------------------------
# the scoped sweep
# ---------------------------------------------------------------------------


def test_the_sweep_reaches_its_own_ids_and_its_own_tag_and_nothing_else():
    """The tag is how a create that timed out client-side but succeeded
    server-side still gets killed. Exact equality only — a probe running
    beside this one must be unreachable."""
    items = [
        _Listed("sbx_mine", {}),                                    # id we recorded
        _Listed("sbx_orphan", {probe.RUN_TAG_KEY: "elasticity-A"}), # our tag, unseen id
        _Listed("sbx_theirs", {probe.RUN_TAG_KEY: "elasticity-B"}), # another run
        _Listed("sbx_stranger", {}),                                # nothing of ours
        _Listed("", {probe.RUN_TAG_KEY: "elasticity-A"}),           # no id to kill
    ]
    targets = probe.sweep_targets(items, ["sbx_mine"], "elasticity-A")
    assert targets == ["sbx_mine", "sbx_orphan"]


def test_the_sweep_never_matches_on_an_empty_tag():
    """An empty run tag would otherwise match every untagged sandbox in the
    account, including another session's."""
    items = [_Listed("sbx_other", {})]
    assert probe.sweep_targets(items, [], "") == []


# ---------------------------------------------------------------------------
# redaction and the dry run
# ---------------------------------------------------------------------------


def test_redact_strips_the_key(monkeypatch):
    monkeypatch.setenv("E2B_API_KEY", "e2b_supersecretvalue_1234")
    assert "e2b_supersecretvalue_1234" not in probe.redact(
        'api_key: "e2b_supersecretvalue_1234"'
    )


def test_dry_run_validates_the_plan_without_a_key_a_socket_or_the_sdk(
    monkeypatch, capsys
):
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    monkeypatch.setattr(probe, "run_ladder", _forbidden)
    monkeypatch.setattr(sys, "argv",
                        ["elasticity_probe.py", "--dry-run", "--concurrency", "1,3"])
    assert probe.main() == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "no sandbox was created" in out
    assert "[1, 3]" in out
    assert "QUOTED" in out            # the 150 cap is never presented as measured
    assert "a live run would exit 1" in out


def test_a_bad_config_exits_one_and_is_not_a_verdict(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv",
                        ["elasticity_probe.py", "--dry-run", "--concurrency", "8,1"])
    assert probe.main() == 1
    assert "CONFIG ERROR" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv",
                        ["elasticity_probe.py", "--dry-run", "--failure-threshold", "0"])
    assert probe.main() == 1


async def _forbidden(*args, **kwargs):  # pragma: no cover - the point is that it never runs
    raise AssertionError("--dry-run must not reach the API")
