"""What kind of job is this, and is the evidence worth reading out loud?

Two things this file pins.

**Every kind is classified from a spec somebody could actually write**, and
from the REAL parser — ``parse_flashml_yaml`` builds every config below, so a
rule that reads a field the parser does not produce fails here rather than in
front of a user. Several cases are also compiled through the real
``compile.py`` and classified a second time from the compiled ``JobSpec``, to
pin that the two adapters agree: the preview route holds a JobSpec and never
the YAML, so a classifier that only understood one of them would be
unreachable from the one surface that plans.

**Ambiguity produces ``COMMAND``, and says why.** A confident wrong answer
routes a training run onto a two-vCPU sandbox, so the fallback is tested as
carefully as the hits — including the evidence, which has to name what was
missing rather than shrug.
"""
from __future__ import annotations

import pytest

from flashml_cloud_api import compile as compilemod
from flashml_cloud_api.flashml_yaml import parse_flashml_yaml
from flashml_cloud_api.images import resolve_image
from flashml_cloud_api.router import workload as W

CODE = "artifact://jobs/j1/code.tgz"


def _config(body: str):
    return parse_flashml_yaml(body)


def _compiled(config, name: str = "job"):
    return compilemod.compile_to_jobspec(
        config, resolve_image(config.image), CODE, name
    )


HPO_YAML = """
version: 1
name: cifar sweep
image: pytorch-cpu
entrypoint: train.py
sweep:
  lr: [0.1, 0.01, 0.001]
  batch_size: [32, 64]
reduce: {kind: rank, metric: accuracy}
"""

FEDERATED_YAML = """
version: 2
name: hospital rounds
image: pytorch-cpu
entrypoint: round.py
mode: federated
epochs: 4
local_inputs: [patients]
"""

TRAINING_YAML = """
version: 1
name: pretrain
image: pytorch-cuda
entrypoint: train.py
resources: {gpus: 1}
timeout_seconds: 28800
"""

FINETUNE_YAML = """
version: 1
name: adapt
image: pytorch-cuda
entrypoint: train.py
args: ["--base-model", "meta-llama/Llama-3-8B"]
resources: {gpus: 1}
"""

EVALUATION_YAML = """
version: 1
name: score run
image: python-slim
entrypoint: evaluate.py
validators: {keys: [accuracy]}
reduce: {kind: aggregate, metric: accuracy}
"""

INFERENCE_YAML = """
version: 1
name: caption everything
image: pytorch-cuda
entrypoint: infer.py
partition: {range: [0, 100000], shards: 50}
reduce: {kind: concat}
resources: {gpus: 1}
"""

COMMAND_YAML = """
version: 1
name: just run it
image: python-slim
entrypoint: main.py
"""


# ---------------------------------------------------------------------------
# each kind, from a spec somebody could write
# ---------------------------------------------------------------------------


class TestEachKind:
    def test_a_sweep_is_hpo_and_the_evidence_counts_the_trials(self):
        kind, evidence = W.classify(_config(HPO_YAML))
        assert kind is W.WorkloadKind.HPO
        # The whole point of returning evidence: a reader can check the
        # number against the file. 3 x 2 = 6.
        assert "6 independent trials" in evidence
        assert "lr" in evidence and "batch_size" in evidence
        assert "one entrypoint" in evidence

    def test_a_rank_reducer_is_named_as_the_search_signal(self):
        _, evidence = W.classify(_config(HPO_YAML))
        assert "reduce.kind: rank" in evidence

    def test_the_real_task_count_beats_the_derived_one(self):
        """The preview route expands with the runtime's own ``expand_tasks``
        and knows the real number. Passed in, it is what the sentence says."""
        _, evidence = W.classify(_config(HPO_YAML), task_count=6)
        assert "6 independent trials" in evidence

    def test_federated_mode_is_the_one_unambiguous_declaration(self):
        kind, evidence = W.classify(_config(FEDERATED_YAML))
        assert kind is W.WorkloadKind.FEDERATED
        assert "mode: federated" in evidence
        assert "4 pass" in evidence
        # Not independent work, and the evidence has to say so — this is the
        # sentence that keeps a round off a CPU sandbox.
        assert "not independent work" in evidence
        assert "patients" in evidence

    def test_one_gpu_task_with_no_generator_is_training(self):
        kind, evidence = W.classify(_config(TRAINING_YAML))
        assert kind is W.WorkloadKind.TRAINING
        assert "1 GPU(s) per task" in evidence
        assert "28800s" in evidence

    def test_a_base_model_flag_is_the_finetune_signal(self):
        kind, evidence = W.classify(_config(FINETUNE_YAML))
        assert kind is W.WorkloadKind.FINETUNE
        assert "--base-model" in evidence
        # And the evidence admits what it rests on. This is the weakest rule
        # in the module and it says so.
        assert "no field for one" in evidence

    def test_an_aggregate_reducer_is_evaluation(self):
        kind, evidence = W.classify(_config(EVALUATION_YAML))
        assert kind is W.WorkloadKind.EVALUATION
        assert "reduce.kind: aggregate" in evidence
        assert "accuracy" in evidence
        # It cannot name the artifact under test, and does not pretend to.
        assert "names the artifact under test" in evidence

    def test_a_partition_that_concatenates_is_batch_inference(self):
        kind, evidence = W.classify(_config(INFERENCE_YAML))
        assert kind is W.WorkloadKind.INFERENCE
        assert "reduce.kind: concat" in evidence
        # And it refuses to imply a serving path that does not exist.
        assert "never per-request latency" in evidence

    def test_a_plain_command_is_command_and_names_what_was_missing(self):
        kind, evidence = W.classify(_config(COMMAND_YAML))
        assert kind is W.WorkloadKind.COMMAND
        for absent in (
            "no sweep",
            "no partition",
            "no mode: federated",
            "no reducer",
            "no validators",
            "no GPU requirement",
            "no base model",
        ):
            assert absent in evidence


# ---------------------------------------------------------------------------
# ambiguity falls back, and says so
# ---------------------------------------------------------------------------


class TestAmbiguityFallsBack:
    def test_a_partition_with_no_reducer_is_not_guessed_at(self):
        """A fan-out whose output nothing describes could be inference,
        evaluation or arbitrary data processing. Naming one would be a
        guess, and the guess would move where the work runs."""
        config = _config(
            """
version: 1
name: shards
image: python-slim
entrypoint: work.py
partition: {range: [0, 1000], shards: 10}
"""
        )
        kind, evidence = W.classify(config)
        assert kind is W.WorkloadKind.COMMAND
        assert "nothing says what the shards compute" in evidence

    def test_a_reducer_alone_describes_joining_and_not_producing(self):
        config = _config(
            """
version: 1
name: collected
image: python-slim
entrypoint: work.py
reduce: {kind: collect}
"""
        )
        kind, evidence = W.classify(config)
        assert kind is W.WorkloadKind.COMMAND
        assert "how outputs are joined" in evidence

    def test_a_cpu_job_with_no_signal_at_all_is_not_training(self):
        """`gpus: 0` is a statement, and the statement is 'no card'. Nothing
        may read it as the training shape."""
        config = _config(
            """
version: 1
name: cpu thing
image: python-slim
entrypoint: main.py
resources: {gpus: 0, cpus: 4}
"""
        )
        kind, _ = W.classify(config)
        assert kind is W.WorkloadKind.COMMAND

    def test_a_bare_model_flag_is_not_enough(self):
        """``--model resnet18`` names an architecture as often as a
        checkpoint. It is deliberately not in BASE_MODEL_FLAGS."""
        config = _config(
            """
version: 1
name: scratch
image: pytorch-cuda
entrypoint: train.py
args: ["--model", "resnet18"]
resources: {gpus: 1}
"""
        )
        kind, _ = W.classify(config)
        assert kind is W.WorkloadKind.TRAINING

    def test_an_object_carrying_none_of_the_fields_is_command(self):
        class Nothing:
            pass

        kind, evidence = W.classify(Nothing())
        assert kind is W.WorkloadKind.COMMAND
        assert "no sweep" in evidence


# ---------------------------------------------------------------------------
# precedence — which declaration outranks which
# ---------------------------------------------------------------------------


class TestPrecedence:
    def test_a_gpu_sweep_is_still_hpo(self):
        """Forty independent tasks is what decides where the work can run.
        The card is a per-task requirement the placement gate enforces, and
        it does not turn a search into a training run."""
        config = _config(
            """
version: 1
name: gpu sweep
image: pytorch-cuda
entrypoint: train.py
sweep: {lr: [0.1, 0.01]}
resources: {gpus: 1}
"""
        )
        kind, evidence = W.classify(config)
        assert kind is W.WorkloadKind.HPO
        # The GPU ask is still reported — it is what keeps the sweep off the
        # CPU sandbox one layer down.
        assert "1 GPU(s) per task" in evidence

    def test_a_sweep_of_finetunes_is_hpo_not_finetune(self):
        config = _config(
            """
version: 1
name: lora sweep
image: pytorch-cuda
entrypoint: train.py
args: ["--base-model", "mistral-7b"]
sweep: {rank: [4, 8, 16]}
resources: {gpus: 1}
"""
        )
        kind, _ = W.classify(config)
        assert kind is W.WorkloadKind.HPO

    def test_validators_over_a_scored_fanout_beat_the_map_reading(self):
        config = _config(
            """
version: 1
name: sharded eval
image: python-slim
entrypoint: evaluate.py
partition: {range: [0, 100], shards: 10}
validators: {keys: [accuracy, loss]}
reduce: {kind: concat}
"""
        )
        kind, evidence = W.classify(config)
        assert kind is W.WorkloadKind.EVALUATION
        assert "accuracy" in evidence

    def test_a_training_run_that_declares_validators_is_still_training(self):
        """Declaring what a task must emit is not the same as being a
        scoring job, and a single GPU run that says both is the first."""
        config = _config(
            """
version: 1
name: train with checks
image: pytorch-cuda
entrypoint: train.py
validators: {keys: [loss]}
resources: {gpus: 2}
"""
        )
        kind, _ = W.classify(config)
        assert kind is W.WorkloadKind.TRAINING


# ---------------------------------------------------------------------------
# the compiled-spec adapter, against specs the real compiler produced
# ---------------------------------------------------------------------------


class TestCompiledSpecAdapter:
    def test_a_compiled_sweep_still_reads_as_hpo(self):
        config = _config(HPO_YAML)
        from_config, _ = W.classify(config)
        from_spec, evidence = W.classify(_compiled(config))
        assert from_config is from_spec is W.WorkloadKind.HPO
        # task_params carries the expansion, so the count survives compiling.
        assert "6 independent trials" in evidence

    def test_a_compiled_federated_round_is_never_read_as_a_sweep(self):
        """The failure this guards is specific and expensive: a round's
        ``task_params`` is one row per shard, so an unguarded reader sees N
        'trials' and routes real gradient work as an embarrassingly parallel
        search."""
        config = _config(FEDERATED_YAML)
        spec = compilemod.compile_federated_round(
            config,
            resolve_image(config.image),
            CODE,
            "hospital",
            round_index=1,
            weights_uri="artifact://jobs/j1/round-000/weights.json",
            slot_chunks=[0, 1, 2],
            total_chunks=3,
        )
        kind, evidence = W.classify(spec)
        assert kind is W.WorkloadKind.FEDERATED
        assert "chunks of one model's data" in evidence

    def test_the_shard_guard_holds_even_without_the_label(self):
        config = _config(FEDERATED_YAML)
        spec = compilemod.compile_federated_round(
            config,
            resolve_image(config.image),
            CODE,
            "hospital",
            round_index=1,
            weights_uri="artifact://jobs/j1/round-000/weights.json",
            slot_chunks=[0, 1, 2],
            total_chunks=3,
        )
        spec["metadata"]["labels"].pop("flashml.dev/mode")
        kind, _ = W.classify(spec)
        assert kind is W.WorkloadKind.FEDERATED

    def test_the_gpu_requirement_survives_the_compiler(self):
        config = _config(TRAINING_YAML)
        signals = W.signals_from_job_spec(_compiled(config))
        assert signals.gpus_per_task == 1
        assert W.classify(_compiled(config))[0] is W.WorkloadKind.TRAINING

    def test_a_model_input_would_be_the_strong_finetune_signal(self):
        """``inputs`` is the field a staged base model would arrive in.
        Nothing produces one today — ``compile.py`` stages ``code`` and, for
        a round, ``weights`` — so this states the contract for whoever adds
        it rather than pretending it already exists."""
        spec = _compiled(_config(TRAINING_YAML))
        assert set(spec["spec"]["workload"]["parameters"]["inputs"]) == {"code"}
        spec["spec"]["workload"]["parameters"]["inputs"]["base_model"] = (
            "artifact://models/llama-3-8b"
        )
        kind, evidence = W.classify(spec)
        assert kind is W.WorkloadKind.FINETUNE
        assert "base_model" in evidence


# ---------------------------------------------------------------------------
# determinism and the shape of the answer
# ---------------------------------------------------------------------------


class TestDeterminism:
    @pytest.mark.parametrize(
        "body",
        [
            HPO_YAML,
            FEDERATED_YAML,
            TRAINING_YAML,
            FINETUNE_YAML,
            EVALUATION_YAML,
            INFERENCE_YAML,
            COMMAND_YAML,
        ],
    )
    def test_the_same_spec_always_gives_the_same_answer(self, body):
        first = W.classify(_config(body))
        for _ in range(5):
            assert W.classify(_config(body)) == first

    @pytest.mark.parametrize(
        "body",
        [
            HPO_YAML,
            FEDERATED_YAML,
            TRAINING_YAML,
            FINETUNE_YAML,
            EVALUATION_YAML,
            INFERENCE_YAML,
            COMMAND_YAML,
        ],
    )
    def test_the_evidence_is_never_empty(self, body):
        """A bare enum is not auditable. There is no path that returns one."""
        kind, evidence = W.classify(_config(body))
        assert isinstance(kind, W.WorkloadKind)
        assert isinstance(evidence, str)
        assert len(evidence.strip()) > 20

    def test_a_kind_is_its_own_string(self):
        """So the value survives JSON and a database column with no
        serialiser, like every other field on the preview response."""
        assert W.WorkloadKind.HPO == "hpo"
        assert W.WorkloadKind.COMMAND.value == "command"

    def test_signals_pass_through_unchanged(self):
        signals = W.Signals(federated=True, epochs=2, sync_every=1.0, rounds=2)
        assert W.signals_for(signals) is signals


# ---------------------------------------------------------------------------
# the readers, at their edges
# ---------------------------------------------------------------------------


class TestReaders:
    def test_a_boolean_gpu_count_is_not_one_gpu(self):
        """``gpus: true`` is a typo. Read as 1 it would route a job to
        hardware nobody asked for — the same refusal ``compile._resources``
        makes, for the same reason."""
        assert W._gpu_count(True) is None
        assert W._gpu_count(1) == 1
        assert W._gpu_count(1.5) is None
        assert W._gpu_count(-1) is None
        assert W._gpu_count(0) == 0

    def test_an_unstated_gpu_count_is_none_and_not_zero(self):
        signals = W.signals_from_config(_config(COMMAND_YAML))
        assert signals.gpus_per_task is None

    def test_a_flag_with_an_equals_sign_still_counts(self):
        assert W._base_model_flags(["--base-model=x"]) == ("--base-model",)

    def test_a_malformed_sweep_yields_no_count_rather_than_a_wrong_one(self):
        assert W._combinations({"lr": "not a list"}) is None
        assert W._combinations({"lr": []}) is None
        assert W._combinations({"lr": [1, 2], "b": [1, 2, 3]}) == 6
