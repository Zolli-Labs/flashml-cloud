"""Which venues suit which work — and the four ways that can go wrong.

**A GPU job never reaches ``fc-sandbox``.** The account's FC functions report
``gpuConfig: null``, 2 vCPU and 2 GB RAM; a fit table that let a declared GPU
requirement through would be routing gradient work onto a two-core box. Pinned
at the venue layer and again through a whole plan.

**A venue nothing can acquire from never appears as usable.** ``fc-gpu`` fits
inference better than anything else in the product and there is no code in
this repo that creates an FC GPU function, so it is *suited* and never
*usable*. Those two words are separate fields precisely so a surface can say
both, and no plan may rest on the first.

**Fit narrows and never widens.** It runs after the placement gates, only ever
removes, and with no workload kind supplied removes nothing at all — the
planner behaves exactly as it did before this layer existed.

**Every fit carries a fact.** The reasons are checked for the measurement or
the documented number behind them, because an unsourced preference is a slogan
and a slogan is what this layer replaces.
"""
from __future__ import annotations

import pytest
from flashruntime.protocol.v1alpha1 import TaskSpec
from flashruntime.scheduler import IsolationAwarePlacement

from flashml_cloud_api.router import estimator as est
from flashml_cloud_api.router import plan as P
from flashml_cloud_api.router import venues as V
from flashml_cloud_api.router import workload as W

ELIGIBLE = IsolationAwarePlacement().eligible

ALL_KINDS = tuple(W.WorkloadKind)

MEASURED = est.Estimate(
    low=6.0, high=6.0, basis=est.BASIS_MEASURED, n=9, note="p50/IQR of 9 runs"
)


def _task(**payload) -> TaskSpec:
    return TaskSpec(task_id="t1", job_id="j1", commit_key="c1", payload=payload)


def _gpu_node(node_id: str, count: int = 1, megabytes: int = 24564) -> dict:
    return {
        "node_id": node_id,
        "capabilities": {
            "gpus": [{"index": i, "memory_total_mb": megabytes} for i in range(count)]
        },
    }


def _cpu_node(node_id: str, cores: float = 2.0) -> dict:
    return {"node_id": node_id, "capabilities": {"cpu_cores": cores, "gpus": []}}


def _machine(
    machine_id: str,
    *,
    venue_id: str | None = None,
    node: dict | None = None,
    venue: str = P.VENUE_MARKET,
    currency: str = P.CURRENCY_ZC,
    price_per_hour: float = 600.0,
    seconds: float | None = 6.0,
) -> P.Candidate:
    return P.Candidate(
        machine_id=machine_id,
        node=node if node is not None else _gpu_node(machine_id),
        venue=venue,
        venue_id=venue_id,
        currency=currency,
        price_per_hour=price_per_hour,
        max_concurrent_tasks=1,
        seconds_per_task=seconds,
        reliability_tier=est.TIER_UNPROVEN,
    )


# ---------------------------------------------------------------------------
# the venues themselves
# ---------------------------------------------------------------------------


class TestVenueData:
    def test_the_four_venues_are_the_four_venues(self):
        assert {item.id for item in V.VENUES} == {
            V.VENUE_OWNED,
            V.VENUE_RUNPOD,
            V.VENUE_FC_SANDBOX,
            V.VENUE_FC_GPU,
        }

    @pytest.mark.parametrize("item", V.VENUES, ids=lambda item: item.id)
    def test_every_venue_states_its_acquisition_and_its_currency(self, item):
        assert item.acquisition in (
            V.ACQUISITION_AUTOMATIC,
            V.ACQUISITION_MANUAL,
            V.ACQUISITION_NONE,
        )
        assert item.currency in (V.CURRENCY_ZC, V.CURRENCY_USD)

    @pytest.mark.parametrize("item", V.VENUES, ids=lambda item: item.id)
    def test_every_venue_carries_the_fact_behind_it(self, item):
        """Notes are data, not decoration: they are what a page shows under
        a venue's name, and a venue described without a number is a claim
        nobody can check."""
        assert len(item.notes) > 80
        assert any(character.isdigit() for character in item.notes)

    def test_the_sandbox_is_cpu_only_and_says_so_in_the_data(self):
        sandbox = V.venue(V.VENUE_FC_SANDBOX)
        assert sandbox.has_gpu is False
        # 0.0 is a claim ("no GPU"), None would be "not a venue-level fact".
        assert sandbox.max_vram_gb == 0.0
        assert "gpuConfig: null" in sandbox.notes
        assert "2 vCPU" in sandbox.notes
        assert sandbox.hibernates is True

    def test_the_gpu_function_is_not_integrated(self):
        fc_gpu = V.venue(V.VENUE_FC_GPU)
        assert fc_gpu.acquisition == V.ACQUISITION_NONE
        assert "NOT INTEGRATED" in fc_gpu.notes

    def test_runpod_needs_a_human(self):
        runpod = V.venue(V.VENUE_RUNPOD)
        assert runpod.acquisition == V.ACQUISITION_MANUAL
        assert "ResourceProvider" in runpod.notes

    def test_vram_none_and_vram_zero_mean_different_things(self):
        # Not a venue-level fact: the fleet spans the whole class ladder.
        assert V.venue(V.VENUE_OWNED).max_vram_gb is None
        assert V.venue(V.VENUE_RUNPOD).max_vram_gb is None
        # A claim: this venue has no GPU.
        assert V.venue(V.VENUE_FC_SANDBOX).max_vram_gb == 0.0

    def test_an_unknown_venue_raises_rather_than_defaulting(self):
        with pytest.raises(KeyError):
            V.venue("aws")

    def test_a_listing_maps_onto_a_physical_venue_or_onto_nothing(self):
        assert V.venue_for_listing(P.VENUE_WORKSPACE) == V.VENUE_OWNED
        assert V.venue_for_listing(P.VENUE_MARKET) == V.VENUE_OWNED
        # "rented cloud" is not a venue: RunPod and FC are not
        # interchangeable, so nothing is guessed.
        assert V.venue_for_listing(P.VENUE_RENTED) is None
        assert V.venue_for_listing(None) is None


# ---------------------------------------------------------------------------
# fit, per kind
# ---------------------------------------------------------------------------


class TestFit:
    @pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda kind: kind.value)
    def test_every_kind_judges_every_venue_with_a_reason(self, kind):
        """All four are returned, including the ones ruled out. A page that
        shows only survivors cannot say why RunPod is missing."""
        fits = V.venues_for(kind)
        assert [fit.venue.id for fit in fits] != []
        assert {fit.venue.id for fit in fits} == {item.id for item in V.VENUES}
        for fit in fits:
            assert len(fit.reason) > 40

    @pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda kind: kind.value)
    def test_usable_venues_come_first(self, kind):
        buckets = [
            0 if fit.usable else 1 if fit.suited else 2
            for fit in V.venues_for(kind)
        ]
        assert buckets == sorted(buckets)

    def test_hpo_belongs_on_the_machines_that_already_exist(self):
        fits = V.venues_for(W.WorkloadKind.HPO)
        assert fits[0].venue.id == V.VENUE_OWNED
        assert "independent" in fits[0].reason

    def test_evaluation_leads_with_the_venue_that_hibernates(self):
        fits = V.venues_for(W.WorkloadKind.EVALUATION)
        assert fits[0].venue.id == V.VENUE_FC_SANDBOX
        # Grounded in the measurement, not in a preference.
        assert "2.6 s" in fits[0].reason and "1.1 s" in fits[0].reason

    def test_training_never_belongs_on_the_sandbox(self):
        fit = _fit_for(W.WorkloadKind.TRAINING, V.VENUE_FC_SANDBOX)
        assert fit.suited is False
        assert "2 GB RAM" in fit.reason

    def test_federated_rounds_never_belong_on_the_sandbox_either(self):
        """A round is local training. The venue's limit is hardware, and the
        reason says what it would take to change that answer."""
        fit = _fit_for(W.WorkloadKind.FEDERATED, V.VENUE_FC_SANDBOX)
        assert fit.suited is False

    def test_inference_belongs_where_idle_gpu_is_nearly_free(self):
        fits = V.venues_for(W.WorkloadKind.INFERENCE)
        assert fits[0].venue.id == V.VENUE_OWNED  # the best USABLE one
        fc_gpu = _fit_for(W.WorkloadKind.INFERENCE, V.VENUE_FC_GPU)
        assert fc_gpu.suited is True
        assert fc_gpu.usable is False
        assert "0.25 CU" in fc_gpu.reason

    def test_finetune_and_training_route_identically(self):
        """Reported apart because the distinction is worth telling a reader,
        routed together because nothing about the venue changes. Aliased
        rather than copied so they cannot drift into disagreeing."""
        assert V.venues_for(W.WorkloadKind.FINETUNE) == V.venues_for(
            W.WorkloadKind.TRAINING
        )

    def test_command_rules_nothing_out_on_fit(self):
        """The honest fallback does not get to exclude anything: nothing was
        learned, so nothing is claimed."""
        for fit in V.venues_for(W.WorkloadKind.COMMAND):
            assert fit.suited is True

    def test_a_venue_is_never_unsuited_for_being_expensive(self):
        """Fit is capability, price is the planner's. RunPod is the wrong
        deal for wait-heavy CPU scoring and it is still capable of it —
        collapsing the two is the mistake this whole layer exists to avoid."""
        fit = _fit_for(W.WorkloadKind.EVALUATION, V.VENUE_RUNPOD)
        assert fit.suited is True
        assert "price argument" in fit.reason


def _fit_for(kind: W.WorkloadKind, venue_id: str, **kwargs) -> V.VenueFit:
    return next(
        fit for fit in V.venues_for(kind, **kwargs) if fit.venue.id == venue_id
    )


# ---------------------------------------------------------------------------
# a GPU job never reaches a venue with no GPU
# ---------------------------------------------------------------------------


class TestGpuNeverReachesTheSandbox:
    @pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda kind: kind.value)
    def test_a_declared_gpu_requirement_refuses_the_sandbox_for_every_kind(
        self, kind
    ):
        fit = _fit_for(kind, V.VENUE_FC_SANDBOX, gpus_per_task=1)
        assert fit.suited is False
        assert fit.usable is False
        # Either the kind already ruled it out on hardware or the declared
        # requirement did; every one of those reasons cites the same fact.
        assert "gpuConfig: null" in fit.reason
        assert V.VENUE_FC_SANDBOX not in V.usable_venue_ids(
            kind, gpus_per_task=1
        )

    def test_the_hardware_refusal_overrides_even_the_fallback(self):
        """``COMMAND`` rules nothing out on fit — and a hardware fact is not
        a fit reading. An unclassified job that asked for a card still
        cannot have one here."""
        assert _fit_for(W.WorkloadKind.COMMAND, V.VENUE_FC_SANDBOX).suited is True
        assert (
            _fit_for(
                W.WorkloadKind.COMMAND, V.VENUE_FC_SANDBOX, gpus_per_task=1
            ).suited
            is False
        )

    def test_zero_gpus_is_not_a_gpu_requirement(self):
        assert (
            _fit_for(W.WorkloadKind.HPO, V.VENUE_FC_SANDBOX, gpus_per_task=0).suited
            is True
        )

    def test_a_gpu_sweep_never_plans_onto_a_sandbox_machine(self):
        """End to end: classify a GPU sweep, plan it against a fleet whose
        only cheap machine is an FC sandbox, and check the sandbox is not in
        the answer at any price."""
        kind, evidence = W.classify(
            W.Signals(sweep_axes=("lr",), sweep_combinations=4, gpus_per_task=1)
        )
        assert kind is W.WorkloadKind.HPO
        plan_set = P.plan_job(
            P.PlanRequest(
                task=_task(gpus=1),
                tasks=4,
                candidates=(
                    # Free, and cannot run this. The gate refuses it for
                    # having no GPU; fit would refuse it for the same fact.
                    _machine(
                        "sandbox",
                        venue_id=V.VENUE_FC_SANDBOX,
                        node=_cpu_node("sandbox"),
                        price_per_hour=0.0,
                    ),
                    _machine("rig", venue_id=V.VENUE_OWNED, price_per_hour=600.0),
                ),
                duration=MEASURED,
                kind=kind,
                kind_evidence=evidence,
                gpus_per_task=1,
            ),
            eligible=ELIGIBLE,
        )
        for plan in plan_set.plans():
            assert V.VENUE_FC_SANDBOX not in plan.venues
            assert [a.machine_id for a in plan.allocations] == ["rig"]


# ---------------------------------------------------------------------------
# a venue nothing can acquire from is never presented as usable
# ---------------------------------------------------------------------------


class TestUnacquirableVenues:
    @pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda kind: kind.value)
    def test_fc_gpu_is_never_usable_however_well_it_fits(self, kind):
        assert V.VENUE_FC_GPU not in V.usable_venue_ids(kind)
        fit = _fit_for(kind, V.VENUE_FC_GPU)
        assert fit.acquirable is False
        assert fit.usable is False

    def test_it_is_still_reported_as_a_fit_because_that_is_true(self):
        """The honest sentence is 'this is where inference belongs, and we
        cannot put anything there yet'. One boolean cannot say it."""
        fit = _fit_for(W.WorkloadKind.INFERENCE, V.VENUE_FC_GPU)
        assert (fit.suited, fit.acquirable) == (True, False)

    def test_no_plan_ever_places_work_in_it(self):
        plan_set = P.plan_job(
            P.PlanRequest(
                task=_task(gpus=1),
                tasks=4,
                candidates=(
                    # Free capacity in a venue that does not exist. If fit
                    # were a price adjustment rather than a set, this would
                    # win every plan.
                    _machine(
                        "phantom", venue_id=V.VENUE_FC_GPU, price_per_hour=0.0
                    ),
                    _machine("rig", venue_id=V.VENUE_OWNED, price_per_hour=600.0),
                ),
                duration=MEASURED,
                kind=W.WorkloadKind.TRAINING,
                gpus_per_task=1,
            ),
            eligible=ELIGIBLE,
        )
        assert plan_set.venue_excluded_machines == 1
        for plan in plan_set.plans():
            assert plan.venues == (V.VENUE_OWNED,)
            assert all(
                allocation.venue_id != V.VENUE_FC_GPU
                for allocation in plan.allocations
            )


# ---------------------------------------------------------------------------
# fit narrows, and only narrows
# ---------------------------------------------------------------------------


class TestNarrowingNeverWidens:
    def test_no_kind_means_no_narrowing_at_all(self):
        """The default must leave every existing number byte-identical: a
        classifier nobody asked for must not start removing capacity."""
        candidates = [
            _machine("sandbox", venue_id=V.VENUE_FC_SANDBOX),
            _machine("rig", venue_id=V.VENUE_OWNED),
            _machine("phantom", venue_id=V.VENUE_FC_GPU),
        ]
        kept, refused = P.venue_admitted(candidates, kind=None)
        assert kept == tuple(candidates)
        assert refused == ()

    @pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda kind: kind.value)
    def test_the_kept_set_is_always_a_subset(self, kind):
        candidates = [
            _machine(item.id, venue_id=item.id) for item in V.VENUES
        ] + [_machine("unattributed", venue=P.VENUE_RENTED)]
        kept, refused = P.venue_admitted(candidates, kind=kind)
        ids = [candidate.machine_id for candidate in candidates]
        kept_ids = [candidate.machine_id for candidate in kept]
        refused_ids = [candidate.machine_id for candidate in refused]
        assert set(kept_ids) <= set(ids)
        assert set(kept_ids).isdisjoint(refused_ids)
        assert len(kept) + len(refused) == len(candidates)
        # Order is preserved on both sides — a pure partition.
        assert kept_ids == [i for i in ids if i in set(kept_ids)]

    def test_fit_cannot_resurrect_a_machine_the_gates_refused(self):
        """Gates first, always. A CPU machine refused for a GPU task stays
        refused, and it is counted as a GATE exclusion, not a venue one —
        two different reasons that must never merge into one number."""
        plan_set = P.plan_job(
            P.PlanRequest(
                task=_task(gpus=1),
                tasks=2,
                candidates=(
                    _machine("cpu", venue_id=V.VENUE_OWNED, node=_cpu_node("cpu")),
                    _machine("rig", venue_id=V.VENUE_OWNED),
                ),
                duration=MEASURED,
                kind=W.WorkloadKind.COMMAND,
                gpus_per_task=1,
            ),
            eligible=ELIGIBLE,
        )
        assert plan_set.excluded_machines == 1
        assert plan_set.venue_excluded_machines == 0
        assert plan_set.eligible_machines == 1

    def test_an_unattributed_machine_is_planned_rather_than_dropped(self):
        """``rented`` names no venue, so there is no venue fact to exclude it
        on. Dropping capacity for a reason nobody can check is worse than
        planning it on the gates and the price alone — and the note says so
        rather than letting it pass silently."""
        plan_set = P.plan_job(
            P.PlanRequest(
                task=_task(gpus=1),
                tasks=2,
                candidates=(
                    _machine(
                        "somewhere",
                        venue=P.VENUE_RENTED,
                        currency=P.CURRENCY_USD,
                        price_per_hour=1.0,
                    ),
                ),
                duration=MEASURED,
                kind=W.WorkloadKind.TRAINING,
            ),
            eligible=ELIGIBLE,
        )
        assert plan_set.eligible_machines == 1
        assert plan_set.venue_excluded_machines == 0
        assert any("not attributable to a venue" in note for note in plan_set.notes)

    def test_narrowing_removes_the_venue_that_cannot_do_the_work(self):
        plan_set = P.plan_job(
            P.PlanRequest(
                task=_task(),
                tasks=4,
                candidates=(
                    _machine(
                        "sandbox",
                        venue_id=V.VENUE_FC_SANDBOX,
                        node=_cpu_node("sandbox"),
                        price_per_hour=0.0,
                    ),
                    _machine("rig", venue_id=V.VENUE_OWNED, price_per_hour=600.0),
                ),
                duration=MEASURED,
                kind=W.WorkloadKind.FEDERATED,
            ),
            eligible=ELIGIBLE,
        )
        assert plan_set.venue_excluded_machines == 1
        assert plan_set.cheapest is not None
        # The free machine would have won on price. It is not an option.
        assert plan_set.cheapest.cost.zc > 0
        assert any("venue fit" in note for note in plan_set.notes)

    def test_the_same_fleet_is_kept_for_a_kind_every_venue_can_do(self):
        candidates = [
            _machine("sandbox", venue_id=V.VENUE_FC_SANDBOX),
            _machine("rig", venue_id=V.VENUE_OWNED),
        ]
        kept, refused = P.venue_admitted(candidates, kind=W.WorkloadKind.HPO)
        assert kept == tuple(candidates)
        assert refused == ()


# ---------------------------------------------------------------------------
# what the plan output carries
# ---------------------------------------------------------------------------


class TestPlanOutput:
    def _planned(self):
        kind, evidence = W.classify(
            W.Signals(sweep_axes=("lr", "batch"), sweep_combinations=4, task_count=4)
        )
        return P.plan_job(
            P.PlanRequest(
                task=_task(),
                tasks=4,
                candidates=(
                    _machine(
                        "sandbox",
                        venue_id=V.VENUE_FC_SANDBOX,
                        node=_cpu_node("sandbox"),
                        price_per_hour=0.0,
                    ),
                    _machine("rig", venue_id=V.VENUE_OWNED, price_per_hour=600.0),
                ),
                duration=MEASURED,
                kind=kind,
                kind_evidence=evidence,
            ),
            eligible=ELIGIBLE,
        )

    def test_the_kind_and_its_evidence_travel_with_the_plans(self):
        plan_set = self._planned()
        assert plan_set.kind is W.WorkloadKind.HPO
        assert "independent trials" in (plan_set.kind_evidence or "")
        assert any("routed as hpo work" in note for note in plan_set.notes)

    def test_every_venue_is_reported_including_the_excluded_ones(self):
        plan_set = self._planned()
        assert {fit.venue.id for fit in plan_set.venue_fits} == {
            item.id for item in V.VENUES
        }
        for fit in plan_set.venue_fits:
            assert fit.reason

    def test_a_plan_names_the_venues_it_actually_used(self):
        plan_set = self._planned()
        cheapest = plan_set.cheapest
        assert cheapest is not None
        # Free sandbox capacity wins the cheapest plan for CPU trials, which
        # is the whole product thesis in one assertion.
        assert cheapest.venues == (V.VENUE_FC_SANDBOX,)
        assert cheapest.cost.zc == 0.0

    def test_allocations_carry_the_physical_venue_as_well_as_the_listing(self):
        plan_set = self._planned()
        fastest = plan_set.fastest
        assert fastest is not None
        for allocation in fastest.allocations:
            assert allocation.venue == P.VENUE_MARKET
            assert allocation.venue_id in (V.VENUE_FC_SANDBOX, V.VENUE_OWNED)

    def test_venue_ids_are_derived_when_the_caller_did_not_attribute_them(self):
        """Today's preview route sets no ``venue_id``; every machine it plans
        is a FlashNode host, so both listing venues resolve to ``owned`` and
        the layer is live without a change to the route."""
        assert (
            P.candidate_venue_id(_machine("m", venue=P.VENUE_WORKSPACE))
            == V.VENUE_OWNED
        )
        assert (
            P.candidate_venue_id(_machine("m", venue=P.VENUE_MARKET))
            == V.VENUE_OWNED
        )
        assert P.candidate_venue_id(_machine("m", venue=P.VENUE_RENTED)) is None
        assert (
            P.candidate_venue_id(
                _machine("m", venue=P.VENUE_RENTED, venue_id=V.VENUE_RUNPOD)
            )
            == V.VENUE_RUNPOD
        )


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    @pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda kind: kind.value)
    def test_the_same_question_gives_the_same_ordered_answer(self, kind):
        first = V.venues_for(kind)
        for _ in range(5):
            assert V.venues_for(kind) == first
            assert V.venues_for(kind, gpus_per_task=1) == V.venues_for(
                kind, gpus_per_task=1
            )

    @pytest.mark.parametrize("kind", ALL_KINDS, ids=lambda kind: kind.value)
    def test_usable_ids_are_a_frozen_set_and_stable(self, kind):
        assert isinstance(V.usable_venue_ids(kind), frozenset)
        assert V.usable_venue_ids(kind) == V.usable_venue_ids(kind)

    def test_a_venue_fit_is_frozen(self):
        fit = V.venues_for(W.WorkloadKind.HPO)[0]
        with pytest.raises(Exception):
            fit.suited = False  # type: ignore[misc]
