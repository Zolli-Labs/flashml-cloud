"""The estimator's refusals, which are most of what it is for.

Roughly 29 credited tasks exist in the whole ledger, the coordinator's
reliability record is wiped by every deploy, and ``contributions`` holds only
work that succeeded. Those three facts mean nearly every question this module
is asked has no defensible answer, and the tests below are mostly about the
answers it declines to give: no point estimate from two runs, no confidence
percentage from any number of them, no rate borrowed from a neighbouring
capability class, no denominator synthesised for a host nobody has asked yet.

``None`` is a first-class answer here, exactly as it is in ``metrics.py``.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from flashml_cloud_api import metrics as metricsmod
from flashml_cloud_api.router import estimator as est


def _obs(seconds, klass="gpu-24gb", federated=False):
    return est.Observation(
        seconds=seconds, capability_class=klass, federated=federated
    )


def _rung(rung, values, klass="gpu-24gb"):
    return est.Evidence(rung=rung, observations=tuple(_obs(v, klass) for v in values))


# ---------------------------------------------------------------------------
# the constants are not allowed to drift from the runtime's
# ---------------------------------------------------------------------------


def test_the_thresholds_match_the_runtime_that_argues_them():
    """The router restates three scheduler constants instead of importing
    them, because importing ``flashruntime.scheduler`` would widen the
    protocol-only repo boundary that ``test_import_boundary`` enforces.

    A restatement is only safe if it cannot drift, and this is where that is
    pinned. Tests are outside the boundary check, so this file may reach for
    the real values; the router may not.
    """
    from flashruntime import scheduler

    assert est.MIN_EVIDENCE == scheduler.RELIABILITY_MIN_EVIDENCE
    assert est.RELIABILITY_STEADY_AT == scheduler.RELIABILITY_STEADY_AT
    assert est.RELIABILITY_SHAKY_BELOW == scheduler.RELIABILITY_SHAKY_BELOW
    assert est.TIER_UNPROVEN == scheduler.TIER_UNPROVEN
    assert est.TIER_STEADY == scheduler.TIER_STEADY
    assert est.TIER_MIXED == scheduler.TIER_MIXED
    assert est.TIER_SHAKY == scheduler.TIER_SHAKY


def test_min_evidence_is_the_one_metrics_already_uses():
    """One threshold for "enough evidence", not two. ``metrics.py`` owns it,
    and the router IMPORTS it rather than restating a second 5 — the one
    constant it could safely have copied is the one it does not, because
    ``acceptance_rates`` and this module have to agree about what counts as
    evidence or the same host reads proven on one page and unproven on
    another."""
    assert est.MIN_EVIDENCE == metricsmod.MIN_EVIDENCE

    assigned = {
        target.id
        for node in ast.walk(ast.parse(pathlib.Path(est.__file__).read_text()))
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "MIN_EVIDENCE" not in assigned, "a second copy of the threshold appeared"


# ---------------------------------------------------------------------------
# the ladder, at n = 0 / 1 / 4 / 5 / 20
# ---------------------------------------------------------------------------


def test_no_evidence_at_all_is_none_not_a_guess():
    """The common case at 29 credited tasks, and the whole reason the canary
    exists. Not zero, not a default, not a plausible substitute."""
    assert (
        est.estimate_task_seconds([], capability_class="gpu-24gb") is None
    )


def test_evidence_that_is_all_unusable_is_also_none():
    thin = [
        est.Evidence(
            rung=est.RUNG_SAME_JOB,
            observations=(_obs(None), _obs(0.0), _obs(float("nan")), _obs(-3.0)),
        )
    ]
    assert est.estimate_task_seconds(thin, capability_class="gpu-24gb") is None


def test_one_observation_is_a_projected_range_never_a_point():
    got = est.estimate_task_seconds(
        [_rung(est.RUNG_SAME_JOB, [6.0])], capability_class="gpu-24gb"
    )
    assert got is not None
    assert got.n == 1
    assert got.basis == est.BASIS_PROJECTED
    assert got.low < got.high, "one run produced a point estimate"
    assert got.low == 3.0 and got.high == 12.0


def test_four_observations_are_still_projected():
    """One short of MIN_EVIDENCE is not "nearly enough" — it is the same
    answer as one, because the threshold is where a number starts meaning
    something rather than where it starts being computable."""
    got = est.estimate_task_seconds(
        [_rung(est.RUNG_SAME_JOB, [5.0, 5.5, 6.0, 6.5])],
        capability_class="gpu-24gb",
    )
    assert got is not None
    assert got.n == 4
    assert got.basis == est.BASIS_PROJECTED
    assert got.low == 2.5 and got.high == 13.0


def test_five_observations_earn_a_measured_p50_and_iqr():
    got = est.estimate_task_seconds(
        [_rung(est.RUNG_SAME_JOB, [4.0, 5.0, 6.0, 7.0, 8.0])],
        capability_class="gpu-24gb",
    )
    assert got is not None
    assert got.n == 5
    assert got.basis == est.BASIS_MEASURED
    assert (got.low, got.high) == (5.0, 7.0)
    assert "p50" in got.note


def test_twenty_observations_narrow_the_band_without_changing_the_rules():
    got = est.estimate_task_seconds(
        [_rung(est.RUNG_SAME_JOB, [float(i) for i in range(1, 21)])],
        capability_class="gpu-24gb",
    )
    assert got is not None
    assert got.n == 20
    assert got.basis == est.BASIS_MEASURED
    assert got.low < got.high


def test_no_estimate_at_any_n_carries_a_confidence_percentage():
    """A "91% confidence" from n=2 is exactly what ``metrics.py`` was written
    to prevent. The shape has nowhere to put one, and the note must not
    smuggle one in either."""
    for count in (1, 2, 4, 5, 20):
        got = est.estimate_task_seconds(
            [_rung(est.RUNG_SAME_JOB, [6.0] * count)],
            capability_class="gpu-24gb",
        )
        assert got is not None
        assert "%" not in got.note
        assert not hasattr(got, "confidence")


# ---------------------------------------------------------------------------
# the rungs, in order
# ---------------------------------------------------------------------------


def test_the_strongest_rung_with_anything_to_say_answers():
    got = est.estimate_task_seconds(
        [
            _rung(est.RUNG_CLASS_SHAPE, [100.0] * 20),
            _rung(est.RUNG_SAME_JOB, [6.0] * 8),
        ],
        capability_class="gpu-24gb",
    )
    assert got is not None
    assert got.n == 8
    assert est.RUNG_SAME_JOB in got.note


def test_a_shuffled_ladder_cannot_let_a_weaker_rung_answer_first():
    """The order of the argument list is not the ladder. The ladder is."""
    strong = _rung(est.RUNG_SAME_JOB, [6.0] * 8)
    weak = _rung(est.RUNG_CLASS_SHAPE, [100.0] * 20)
    assert est.estimate_task_seconds(
        [strong, weak], capability_class="gpu-24gb"
    ) == est.estimate_task_seconds([weak, strong], capability_class="gpu-24gb")


def test_a_weaker_rung_answers_when_the_stronger_is_empty():
    got = est.estimate_task_seconds(
        [
            est.Evidence(rung=est.RUNG_SAME_JOB, observations=()),
            _rung(est.RUNG_USER_SHAPE, [9.0] * 6),
        ],
        capability_class="gpu-24gb",
    )
    assert got is not None
    assert got.basis == est.BASIS_ESTIMATED, "another job's history is not a measurement"


def test_an_unknown_rung_raises_rather_than_being_skipped():
    """Skipping it would let a weaker rung answer and look correct."""
    with pytest.raises(ValueError, match="unknown evidence rung"):
        est.estimate_task_seconds(
            [est.Evidence(rung="vibes", observations=(_obs(6.0),))],
            capability_class="gpu-24gb",
        )


# ---------------------------------------------------------------------------
# never across capability class
# ---------------------------------------------------------------------------


def test_observations_from_another_class_are_dropped_not_pooled():
    got = est.estimate_task_seconds(
        [
            est.Evidence(
                rung=est.RUNG_SAME_JOB,
                observations=(
                    _obs(6.0, "gpu-24gb"),
                    _obs(600.0, "cpu-small"),
                    _obs(600.0, "cpu-small"),
                    _obs(600.0, "cpu-small"),
                    _obs(600.0, "cpu-small"),
                ),
            )
        ],
        capability_class="gpu-24gb",
    )
    assert got is not None
    assert got.n == 1, "cpu observations reached a gpu estimate"
    assert got.high < 100.0


def test_a_whole_rung_of_the_wrong_class_falls_through_to_the_next():
    got = est.estimate_task_seconds(
        [
            _rung(est.RUNG_SAME_JOB, [600.0] * 9, klass="cpu-small"),
            _rung(est.RUNG_USER_SHAPE, [6.0] * 9, klass="gpu-24gb"),
        ],
        capability_class="gpu-24gb",
    )
    assert got is not None
    assert got.n == 9
    assert est.RUNG_USER_SHAPE in got.note


def test_select_acceptance_never_borrows_a_neighbouring_class():
    """The failure this exists to prevent, stated in ``metrics.py``: a host at
    0.95 on cpu work and 0.40 on gpu work reads ~0.9 aggregated, and then
    fails the gpu task."""
    rows = [
        {"machine_id": "m", "capability_class": "cpu-small",
         "resolved": 40, "accepted": 38, "acceptance_rate": 0.95},
        {"machine_id": "m", "capability_class": "gpu-24gb",
         "resolved": 10, "accepted": 4, "acceptance_rate": 0.40},
    ]
    gpu = est.select_acceptance(rows, machine_id="m", capability_class="gpu-24gb")
    assert gpu is not None and gpu["acceptance_rate"] == 0.40
    assert est.reliability_tier(gpu) == est.TIER_SHAKY

    absent = est.select_acceptance(
        rows, machine_id="m", capability_class="gpu-80gb"
    )
    assert absent is None, "a rate was borrowed from another class"
    assert est.reliability_tier(absent) == est.TIER_UNPROVEN


def test_an_unclassifiable_machine_matches_no_row():
    rows = [{"machine_id": "m", "capability_class": "gpu-24gb",
             "resolved": 40, "accepted": 40, "acceptance_rate": 1.0}]
    assert est.select_acceptance(rows, machine_id="m", capability_class=None) is None


# ---------------------------------------------------------------------------
# federated work is not timing evidence
# ---------------------------------------------------------------------------


def test_federated_contributions_are_excluded_from_timing():
    """``fedavg.on_round`` credits from the coordinator's task view, which
    reports no duration — production read 26 credits against 16 attempts on
    one machine on 2026-08-03, and the ten-row gap was federated."""
    got = est.estimate_task_seconds(
        [
            est.Evidence(
                rung=est.RUNG_SAME_JOB,
                observations=(
                    _obs(6.0),
                    _obs(None, federated=True),
                    _obs(None, federated=True),
                ),
            )
        ],
        capability_class="gpu-24gb",
    )
    assert got is not None and got.n == 1


def test_a_federated_row_that_somehow_has_a_duration_is_still_excluded():
    """Belt and braces: the flag is checked before the duration is looked at,
    so a credit path that started recording a duration cannot quietly become
    timing evidence."""
    got = est.estimate_task_seconds(
        [
            est.Evidence(
                rung=est.RUNG_SAME_JOB,
                observations=tuple(_obs(0.4, federated=True) for _ in range(9)),
            )
        ],
        capability_class="gpu-24gb",
    )
    assert got is None


# ---------------------------------------------------------------------------
# survivorship bias, disclosed every time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("count", [1, 4, 5, 20])
def test_every_ledger_estimate_discloses_that_it_is_biased_fast(count):
    got = est.estimate_task_seconds(
        [_rung(est.RUNG_SAME_JOB, [6.0] * count)], capability_class="gpu-24gb"
    )
    assert got is not None
    assert "biased fast" in got.note


def test_the_projection_discloses_it_too():
    got = est.project_across_class(
        est.Prior(seconds=6.0, capability_class="gpu-24gb"), "gpu-80gb"
    )
    assert got is not None and "biased fast" in got.note


# ---------------------------------------------------------------------------
# unproven is not zero, and never gets a denominator
# ---------------------------------------------------------------------------


def test_a_host_nobody_has_asked_is_unproven_not_shaky():
    """``scheduler/__init__.py:747`` — 0.0 is "it failed everything", None is
    "it has not been asked yet", and treating the second as the first is how
    a pool stops accepting new volunteers."""
    assert est.reliability_tier(None) == est.TIER_UNPROVEN


def test_thin_evidence_is_unproven_even_when_it_is_all_failures():
    row = {"machine_id": "m", "capability_class": "gpu-24gb",
           "resolved": 4, "accepted": 0, "acceptance_rate": None}
    assert est.reliability_tier(row) == est.TIER_UNPROVEN


def test_enough_evidence_and_all_failures_really_is_shaky():
    row = {"machine_id": "m", "capability_class": "gpu-24gb",
           "resolved": 6, "accepted": 0, "acceptance_rate": 0.0}
    assert est.reliability_tier(row) == est.TIER_SHAKY


@pytest.mark.parametrize(
    "rate,tier",
    [
        (1.0, est.TIER_STEADY),
        (0.9, est.TIER_STEADY),
        (0.89, est.TIER_MIXED),
        (0.75, est.TIER_MIXED),
        (0.74, est.TIER_SHAKY),
    ],
)
def test_the_tier_boundaries(rate, tier):
    row = {"machine_id": "m", "capability_class": "gpu-24gb",
           "resolved": 20, "accepted": int(20 * rate), "acceptance_rate": rate}
    assert est.reliability_tier(row) == tier


def test_the_tier_is_never_a_number():
    """A float invites ``ask ÷ goodput``, which the design removed for
    manufacturing a quantity the ledger does not hold."""
    row = {"machine_id": "m", "capability_class": "gpu-24gb",
           "resolved": 20, "accepted": 19, "acceptance_rate": 0.95}
    assert isinstance(est.reliability_tier(row), str)


# ---------------------------------------------------------------------------
# the last rung: transplanting across classes
# ---------------------------------------------------------------------------


def test_a_cross_class_prior_widens_rather_than_scales():
    got = est.project_across_class(
        est.Prior(seconds=6.0, capability_class="gpu-24gb"), "gpu-48gb"
    )
    assert got is not None
    assert got.basis == est.BASIS_PROJECTED
    assert (got.low, got.high) == (3.0, 12.0), "one ladder step, symmetric"
    assert "not a scaled prediction" in got.note


def test_the_band_gets_useless_as_the_ladder_distance_grows():
    """And looking useless is correct: no measured speed ratio between any two
    capability classes exists anywhere in this system."""
    near = est.project_across_class(
        est.Prior(seconds=6.0, capability_class="gpu-24gb"), "gpu-48gb"
    )
    far = est.project_across_class(
        est.Prior(seconds=6.0, capability_class="cpu-small"), "gpu-80gb-hopper"
    )
    assert near is not None and far is not None
    assert far.high > near.high * 10


def test_a_prior_already_in_the_target_class_is_not_a_projection():
    assert (
        est.project_across_class(
            est.Prior(seconds=6.0, capability_class="gpu-24gb"), "gpu-24gb"
        )
        is None
    )


def test_a_prior_off_the_ladder_projects_nothing():
    assert (
        est.project_across_class(
            est.Prior(seconds=6.0, capability_class="quantum-annealer"), "gpu-24gb"
        )
        is None
    )


def test_the_prior_is_only_consulted_after_every_rung_is_exhausted():
    prior = est.Prior(seconds=600.0, capability_class="cpu-small")
    got = est.estimate_task_seconds(
        [_rung(est.RUNG_CLASS_SHAPE, [6.0] * 9)],
        capability_class="gpu-24gb",
        prior=prior,
    )
    assert got is not None and got.n == 9

    fallback = est.estimate_task_seconds(
        [], capability_class="gpu-24gb", prior=prior
    )
    assert fallback is not None and fallback.basis == est.BASIS_PROJECTED


# ---------------------------------------------------------------------------
# the canary
# ---------------------------------------------------------------------------


def test_one_measured_canary_run_is_projected_at_n_equals_one():
    """The honest version of the demo's best moment: we measured one trial,
    and the other thirty-nine are projected from it. Not "we measured them"."""
    got = est.from_canary(6.0, capability_class="gpu-24gb", tasks_remaining=39)
    assert got is not None
    assert got.n == 1
    assert got.basis == est.BASIS_PROJECTED
    assert got.low < got.high
    assert "1 measured canary run" in got.note
    assert "39 tasks" in got.note


def test_the_canary_band_is_narrower_than_a_lone_historical_row():
    """Same shape, same class, minutes ago beats one row that could be
    anything — but both are still bands and both are still projected."""
    canary = est.from_canary(6.0, capability_class="gpu-24gb", tasks_remaining=39)
    historical = est.estimate_task_seconds(
        [_rung(est.RUNG_SAME_JOB, [6.0])], capability_class="gpu-24gb"
    )
    assert canary is not None and historical is not None
    assert canary.high < historical.high
    assert canary.basis == historical.basis == est.BASIS_PROJECTED


def test_a_probe_that_measured_nothing_calibrates_nothing():
    for bad in (0.0, -1.0, float("nan"), None, True):
        assert est.from_canary(bad, capability_class="gpu-24gb", tasks_remaining=39) is None


# ---------------------------------------------------------------------------
# hardware class, from the fields NodeCapabilities actually carries
# ---------------------------------------------------------------------------


def test_a_3090_lands_in_the_reference_class_despite_reporting_under_nominal():
    """24564 MB, not 24576. A strict >= would file the densest card in the
    reference class as gpu-16gb."""
    assert est.hardware_class(
        {"gpus": [{"index": 0, "name": "RTX 3090", "memory_total_mb": 24564}]}
    ) == "gpu-24gb"


@pytest.mark.parametrize(
    "megabytes,klass",
    [
        (8192, "gpu-8gb"),
        (10240, "gpu-8gb"),
        (16376, "gpu-16gb"),
        (24564, "gpu-24gb"),
        (49140, "gpu-48gb"),
        (81559, "gpu-80gb"),
    ],
)
def test_the_gpu_ladder(megabytes, klass):
    assert est.hardware_class(
        {"gpus": [{"index": 0, "memory_total_mb": megabytes}]}
    ) == klass


def test_hopper_is_told_apart_by_compute_capability_not_by_name():
    assert est.hardware_class(
        {"gpus": [{"index": 0, "memory_total_mb": 81559, "compute_capability": "8.0"}]}
    ) == "gpu-80gb"
    assert est.hardware_class(
        {"gpus": [{"index": 0, "memory_total_mb": 143771, "compute_capability": "9.0"}]}
    ) == "gpu-80gb-hopper"


def test_a_multi_gpu_host_takes_the_class_of_its_largest_device():
    assert est.hardware_class(
        {"gpus": [
            {"index": 0, "memory_total_mb": 24564},
            {"index": 1, "memory_total_mb": 81559},
        ]}
    ) == "gpu-80gb"


def test_cpu_hosts_split_on_cores():
    assert est.hardware_class({"cpu_cores": 4.0}) == "cpu-small"
    assert est.hardware_class({"cpu_cores": 16.0}) == "cpu-large"


@pytest.mark.parametrize(
    "capabilities",
    [
        None,
        {},
        {"gpus": []},
        {"gpus": [{"index": 0}]},                       # driver answered, unparseably
        {"gpus": [{"index": 0, "memory_total_mb": 6144}]},  # below the ladder floor
        {"cpu_cores": None},
        "not-a-mapping",
    ],
)
def test_an_underivable_class_is_none_not_a_default(capabilities):
    """A machine with no class is fully eligible and simply has no historical
    evidence pooled to it. Clamping a 6GB card up to gpu-8gb would let it
    inherit the durations of cards half again its size."""
    assert est.hardware_class(capabilities) is None


def test_class_is_never_derived_from_an_admission_probe():
    """The ``flashnode/benchmark/`` probes (cpu_hash_mbps, net_down_mbps, …)
    have no concrete implementations, nothing calls ``run_admission`` outside
    flashnode's own tests, and ``NodeCapabilities`` has no field to carry a
    result. A class derived from them would be derived from nothing."""
    assert est.hardware_class(
        {"cpu_hash_mbps": 900.0, "net_down_mbps": 400.0, "disk_write_mbps": 1200.0}
    ) is None


# ---------------------------------------------------------------------------
# planning policy, and determinism
# ---------------------------------------------------------------------------


def test_a_plan_is_built_on_the_top_of_the_band():
    """Ledger durations are biased fast already, and an overrun costs the
    trust the panel exists to build while beating a quote costs nothing."""
    estimate = est.Estimate(low=4.0, high=9.0, basis=est.BASIS_MEASURED, n=7, note="")
    assert est.planning_seconds(estimate) == 9.0


def test_the_same_rows_produce_the_same_estimate_every_time():
    rungs = [_rung(est.RUNG_SAME_JOB, [4.1, 5.2, 6.3, 7.4, 8.5, 9.6, 10.7])]
    first = est.estimate_task_seconds(rungs, capability_class="gpu-24gb")
    second = est.estimate_task_seconds(rungs, capability_class="gpu-24gb")
    assert first == second


def test_the_order_observations_arrive_in_does_not_change_the_answer():
    forward = [_rung(est.RUNG_SAME_JOB, [4.0, 5.0, 6.0, 7.0, 8.0, 9.0])]
    backward = [_rung(est.RUNG_SAME_JOB, [9.0, 8.0, 7.0, 6.0, 5.0, 4.0])]
    assert est.estimate_task_seconds(
        forward, capability_class="gpu-24gb"
    ) == est.estimate_task_seconds(backward, capability_class="gpu-24gb")


def test_the_estimate_is_frozen():
    """A value that renders on a page and is quoted back at settlement time
    must not be editable by whoever holds it."""
    got = est.estimate_task_seconds(
        [_rung(est.RUNG_SAME_JOB, [6.0] * 6)], capability_class="gpu-24gb"
    )
    assert got is not None
    with pytest.raises(Exception):
        got.low = 0.0  # type: ignore[misc]
