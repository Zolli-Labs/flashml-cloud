"""What KIND of work a job is, and the evidence for saying so.

The router prices a fleet; this module says what the fleet is *for*. Cost and
ETA cannot see the difference between forty six-second HPO trials and one
eight-hour fine-tune, and the product thesis is exactly that difference: work
goes to a venue by **fit**, and only then is the cheapest machine inside the
fit chosen.

**Classification lives here and not in the runtime.** ``WorkloadSpec.kind``
and the recipe registry sit in ``flashruntime``, the PUBLIC package this repo
consumes as an exact PyPI version, so adding a field there is merge → release
→ bump four pin sites. Nothing needs that: ``compile.py`` already knows
whether it is compiling an HPO sweep, a federated round or a plain command,
because the author declared it in ``flashml.yaml``. This module reads the same
declarations and adds no field to any wire schema.

**Every answer carries its evidence.** ``classify`` returns
``(kind, evidence)`` and never a bare enum, because "HPO" is not reviewable
and *"sweep over lr, batch_size expands to 40 independent trials sharing one
entrypoint"* is. Where the spec genuinely does not say, the answer is
``COMMAND`` and the evidence names what was absent — an honest refusal beats
a confident wrong classification, which would route a training job onto a
two-vCPU sandbox.

**Two adapters, one rule engine.** ``signals_from_config`` reads a parsed
``flashml.yaml`` (``flashml_yaml.FlashmlConfig`` — what ``compile.py`` is
handed); ``signals_from_job_spec`` reads a compiled ``JobSpec`` mapping (what
``POST /v1alpha1/jobs/preview-plans`` actually holds, since it takes a spec or
a job id and never the original YAML). Both produce the same ``Signals``
record and the rules below run over that, so the two entry points can never
disagree about what a sweep is. Neither adapter imports anything: the fields
are read by name, off whatever object it is given.

**What the spec genuinely cannot say**, recorded here rather than guessed
around:

- *There is no declared base-model input.* ``flashml.yaml`` carries
  ``datasets:`` (origins a host fetches) and ``local_inputs:`` (labels a host
  lends) and no third thing. The fine-tune signals available are a base-model
  flag the author wrote into ``args:`` themselves and — on a compiled spec —
  an ``inputs`` key naming a model artifact, which nothing produces today.
  Both are weak and both are labelled as such in the evidence. The
  misclassification is cheap either way: ``FINETUNE`` and ``TRAINING`` route
  identically (``venues.py``), so confusing them changes a label, not a
  placement.
- *Nothing can declare latency sensitivity.* FlashML is a pull system with no
  serving path — a task is claimed, run, committed. ``INFERENCE`` here means
  *many short forward passes in a batch*, which ``partition`` +
  ``reduce: concat`` does describe, and never *served* inference, which no
  spec in this product can express.
- *Nothing names the artifact under test.* ``EVALUATION`` can be read off a
  declared reducer and declared validators, but "scores job 8821's output" is
  not expressible, so the evidence says "scores its tasks' outputs" and no
  more.

Pure functions. No I/O, no clock, no database, no import of the parser or the
compiler — the same rule the rest of ``router/`` follows, so the policy stays
readable and arguable on its own.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from math import prod
from typing import Any

__all__ = [
    "BASE_MODEL_FLAGS",
    "MODEL_INPUT_NAMES",
    "REDUCE_AGGREGATE",
    "REDUCE_COLLECT",
    "REDUCE_CONCAT",
    "REDUCE_NONE",
    "REDUCE_RANK",
    "SHARD_PARAM",
    "SOURCE_CONFIG",
    "SOURCE_JOB_SPEC",
    "SOURCE_SIGNALS",
    "Signals",
    "WorkloadKind",
    "classify",
    "signals_for",
    "signals_from_config",
    "signals_from_job_spec",
]


class WorkloadKind(str, Enum):
    """The seven shapes the router can tell apart, and one of them is "no".

    A ``str`` enum so a value survives JSON, a database column and a template
    without a serialiser: ``WorkloadKind.HPO == "hpo"`` is true, which keeps
    the preview route's response a plain dict of primitives like every other
    field on it.
    """

    #: Many independent short trials over one entrypoint. The shape a pull
    #: fleet of ordinary machines is strictly good at.
    HPO = "hpo"
    #: One long checkpointed job that wants a real card for its duration.
    TRAINING = "training"
    #: Training that starts from someone else's weights. Routed exactly like
    #: ``TRAINING`` — see the module docstring on why the distinction is
    #: reported but not load-bearing.
    FINETUNE = "finetune"
    #: Many short forward passes in a batch. Never *served* inference.
    INFERENCE = "inference"
    #: Scores outputs rather than producing a model. Often wait-heavy and
    #: often CPU-bound.
    EVALUATION = "evaluation"
    #: Rounds of local training with an aggregation step between them.
    FEDERATED = "federated"
    #: The honest fallback: the spec does not say, so nothing is claimed.
    COMMAND = "command"


#: The reducer kinds ``flashml.yaml`` may name. Restated rather than imported
#: because the authoritative list lives in the runtime's reducer registry and
#: this module only needs to recognise two of them; the parser itself performs
#: no semantic validation beyond requiring ``kind`` to be present.
REDUCE_NONE = "none"
REDUCE_COLLECT = "collect"
REDUCE_RANK = "rank"
REDUCE_AGGREGATE = "aggregate"
REDUCE_CONCAT = "concat"

#: Argv flags that mean "start from these weights", and therefore the only
#: fine-tune signal a ``flashml.yaml`` can carry today.
#:
#: Deliberately narrow. ``--model`` is NOT here: it names an architecture as
#: often as a checkpoint, and a from-scratch ``--model resnet18`` read as a
#: fine-tune would be exactly the confident wrong answer this module refuses.
#: Every flag below says *pretrained* or *base* in so many words, which is
#: something an author only writes when it is true.
BASE_MODEL_FLAGS = (
    "--base-model",
    "--base_model",
    "--from-pretrained",
    "--from_pretrained",
    "--model-name-or-path",
    "--model_name_or_path",
    "--pretrained",
    "--pretrained-model",
    "--pretrained_model",
    "--pretrained_model_name_or_path",
)

#: Declared-input names that would mean a base model was staged as an
#: artifact — the STRONG fine-tune signal, and the one that does not exist
#: yet. ``compile.py`` stages exactly two inputs today, ``code`` and (for a
#: federated round) ``weights``; a ``model`` input has no producer. Recognised
#: here so the day one is added the classifier already reads it, and reported
#: as absent rather than substituted for by anything weaker.
MODEL_INPUT_NAMES = ("base-model", "base_model", "basemodel", "model", "pretrained")

#: The per-task parameter a compiled FEDERATED round carries
#: (``compile.compile_federated_round`` builds ``{"shard": str(chunk)}``).
#: A round is labelled ``flashml.dev/mode: federated`` and is caught long
#: before this matters — but if that label were ever dropped, an unguarded
#: reader would see two hundred ``task_params`` and call a training run a
#: two-hundred-trial sweep, then route it onto CPU sandboxes. The guard is
#: cheap and the failure it prevents is not.
SHARD_PARAM = "shard"

SOURCE_CONFIG = "flashml.yaml"
SOURCE_JOB_SPEC = "compiled JobSpec"
SOURCE_SIGNALS = "signals"

#: The label ``compile.compile_federated_round`` stamps on every round.
_MODE_LABEL = "flashml.dev/mode"
_MODE_FEDERATED = "federated"

#: The input name a federated round's aggregated weights arrive under.
_WEIGHTS_INPUT = "weights"


@dataclass(frozen=True)
class Signals:
    """Exactly what classification reads, and nothing else.

    Extracted once by an adapter so that the rules below are pure functions
    over a frozen record — which is what makes them testable without a parser,
    a compiler or a fixture repo, and what makes it possible to say in one
    place which fields a classification is allowed to rest on.

    ``None`` means *the spec does not say*, everywhere. It is never a zero and
    never a default: ``gpus_per_task=None`` is "no GPU requirement was
    written", which is a different fact from ``gpus_per_task=0``, and only the
    second is a statement by the author.
    """

    #: ``mode: federated``, declared. The one unambiguous kind signal.
    federated: bool = False
    #: Sweep axis names, in the order the author wrote them.
    sweep_axes: tuple[str, ...] = ()
    #: How many trials the sweep expands to, or ``None`` if not derivable.
    sweep_combinations: int | None = None
    #: The real expanded task count when a caller knows it (the preview route
    #: has it from ``expand_tasks``), else the best this module can derive.
    task_count: int | None = None
    #: Whether those tasks are independent of each other. ``False`` for a
    #: federated round, whose tasks are chunks of one model's data.
    independent: bool = True
    #: A ``partition:`` generator is declared — one task per shard of a range,
    #: expanded by the coordinator, so the count is not derivable here.
    partitioned: bool = False
    reduce_kind: str | None = None
    validator_keys: tuple[str, ...] = ()
    #: GPUs the author asked for per task. ``None`` when unstated.
    gpus_per_task: int | None = None
    #: Base-model flags found in the declared args, verbatim, for the evidence.
    base_model_flags: tuple[str, ...] = ()
    #: Declared artifact input names. Empty from a ``flashml.yaml``, which has
    #: no such field at all.
    input_names: tuple[str, ...] = ()
    #: Input names from ``input_names`` that name a base model.
    model_inputs: tuple[str, ...] = ()
    dataset_names: tuple[str, ...] = ()
    local_inputs: tuple[str, ...] = ()
    epochs: int | None = None
    sync_every: float | None = None
    rounds: int | None = None
    timeout_seconds: int | None = None
    #: Where these were read from, so the evidence can say so.
    source: str = SOURCE_SIGNALS

    @property
    def has_generator(self) -> bool:
        """Whether the spec fans one submission out into many tasks.

        ``sweep`` and ``partition`` are the two generators and the parser
        refuses to combine them (``_validate_partition``), so this is a plain
        or. A federated round fans out too, but into chunks of one model's
        data rather than independent tasks, which is why it is not counted
        here and is classified before anything reads this.
        """
        return bool(self.sweep_axes) or self.partitioned


# ---------------------------------------------------------------------------
# adapters — the only place a field is read off a foreign object
# ---------------------------------------------------------------------------


def signals_for(spec: Any) -> Signals:
    """``Signals`` from whichever of the three shapes was handed over.

    A ``Mapping`` is a compiled ``JobSpec``; anything else is treated as a
    parsed ``flashml.yaml`` config and read by attribute. A ``Signals`` passes
    through untouched, which is what lets a test state a case directly instead
    of building a config to imply one.
    """
    if isinstance(spec, Signals):
        return spec
    if isinstance(spec, Mapping):
        return signals_from_job_spec(spec)
    return signals_from_config(spec)


def signals_from_config(config: Any) -> Signals:
    """Read a parsed ``flashml.yaml`` — ``flashml_yaml.FlashmlConfig``.

    By attribute and by name, never by import. ``router/`` deliberately
    depends on nothing in this package except ``metrics.MIN_EVIDENCE``, and a
    classifier that imported the parser would make the parser's dataclass part
    of the router's contract for no gain — every field below is read
    defensively anyway, because a caller can hand this a config from a schema
    version that has not been written yet.

    Every field named here exists in the parser today:
    ``mode``/``is_federated``, ``sweep``, ``partition``, ``reduce``,
    ``validators``, ``resources``, ``args``, ``datasets``, ``local_inputs``,
    ``epochs``, ``sync_every``, ``round_count``, ``timeout_seconds``. Nothing
    is invented, and the two fields this module would most like — a declared
    base-model artifact and a statement of latency sensitivity — are absent
    and reported absent rather than inferred.
    """
    sweep = _mapping(getattr(config, "sweep", None))
    axes = tuple(str(key) for key in sweep)
    combinations = _combinations(sweep)
    federated = bool(getattr(config, "is_federated", False)) or (
        str(getattr(config, "mode", "")) == _MODE_FEDERATED
    )
    partitioned = bool(_mapping(getattr(config, "partition", None)))
    resources = _mapping(getattr(config, "resources", None))
    reduce_spec = _mapping(getattr(config, "reduce", None))
    validators = _mapping(getattr(config, "validators", None))
    datasets = getattr(config, "datasets", None) or ()

    return Signals(
        federated=federated,
        sweep_axes=axes,
        sweep_combinations=combinations,
        # A sweep's expansion is arithmetic this module can do; a partition's
        # is the coordinator's rule and a federated round's depends on who is
        # online, so both stay None rather than being guessed at. A job with
        # neither generator is one task.
        task_count=(
            combinations
            if axes
            else None
            if partitioned or federated
            else 1
        ),
        independent=not federated,
        partitioned=partitioned,
        reduce_kind=_text(reduce_spec.get("kind")),
        validator_keys=_strings(validators.get("keys")),
        gpus_per_task=_gpu_count(resources.get("gpus")),
        base_model_flags=_base_model_flags(getattr(config, "args", None)),
        dataset_names=tuple(
            name
            for item in datasets
            if isinstance(item, Mapping)
            for name in (_text(item.get("name")),)
            if name
        ),
        local_inputs=_strings(getattr(config, "local_inputs", None)),
        epochs=_int(getattr(config, "epochs", None)),
        sync_every=_float(getattr(config, "sync_every", None)),
        rounds=_int(getattr(config, "round_count", None)),
        timeout_seconds=_int(getattr(config, "timeout_seconds", None)),
        source=SOURCE_CONFIG,
    )


def signals_from_job_spec(spec: Mapping[str, Any]) -> Signals:
    """Read a compiled ``JobSpec`` mapping.

    The second adapter exists because the surface that plans is
    ``POST /v1alpha1/jobs/preview-plans``, and it takes a job id or a JobSpec
    — never the ``flashml.yaml`` those were compiled from. Without this, venue
    fit would be unreachable from the only route that calls the planner.

    Everything read here was **forwarded verbatim** by ``compile.py``:
    ``partition``, ``validators`` and ``reduce`` are ``dict(config.x)``,
    ``task_params`` is the sweep's own expansion, ``inputs`` is the declared
    input map, and ``flashml.dev/mode`` is stamped on every federated round.
    So this is the author's declaration read one hop later, not intent
    re-derived from argv.

    ``resources.gpuPerTask`` is read and does survive the pinned protocol
    (``ResourcesSpec`` declares it). ``compile._resources`` carries a stale
    note saying it does not; the pin has moved since. Absent still means
    unstated, so a spec compiled against an older pin reads as "no GPU
    requirement declared" rather than as "zero GPUs".
    """
    body = _mapping(spec.get("spec"))
    workload = _mapping(body.get("workload"))
    parameters = _mapping(workload.get("parameters"))
    metadata = _mapping(spec.get("metadata"))
    labels = _mapping(metadata.get("labels"))
    resources = _mapping(body.get("resources"))
    inputs = _mapping(parameters.get("inputs"))
    input_names = tuple(str(key) for key in inputs)

    rows = _rows(parameters.get("task_params"))

    federated = str(labels.get(_MODE_LABEL, "")) == _MODE_FEDERATED
    axes: tuple[str, ...] = ()
    if rows and not federated:
        keys = tuple(str(key) for key in rows[0])
        # A round that somehow lost its label still must not read as a sweep
        # — see SHARD_PARAM.
        if keys == (SHARD_PARAM,) and _WEIGHTS_INPUT in inputs:
            federated = True
        else:
            axes = keys

    partitioned = bool(_mapping(parameters.get("partition")))
    reduce_spec = _mapping(parameters.get("reduce"))
    validators = _mapping(parameters.get("validators"))

    return Signals(
        federated=federated,
        sweep_axes=axes,
        sweep_combinations=len(rows) if axes else None,
        task_count=(
            None
            if partitioned or federated
            else len(rows)
            if rows
            else 1
        ),
        independent=not federated,
        partitioned=partitioned,
        reduce_kind=_text(reduce_spec.get("kind")),
        validator_keys=_strings(validators.get("keys")),
        gpus_per_task=_gpu_count(resources.get("gpuPerTask")),
        base_model_flags=_base_model_flags(parameters.get("command")),
        input_names=input_names,
        model_inputs=tuple(
            name for name in input_names if name.lower() in MODEL_INPUT_NAMES
        ),
        local_inputs=_strings(parameters.get("local_inputs")),
        timeout_seconds=_int(parameters.get("timeout_seconds")),
        source=SOURCE_JOB_SPEC,
    )


# ---------------------------------------------------------------------------
# the rules
# ---------------------------------------------------------------------------


def classify(spec: Any, *, task_count: int | None = None) -> tuple[WorkloadKind, str]:
    """``(kind, evidence)`` for one job. The evidence is not optional.

    ``task_count`` is the caller's real expansion when it has one — the
    preview route runs the runtime's own ``expand_tasks`` and knows exactly
    how many tasks a spec becomes, which is a better number than anything
    derivable here and is the one a reader will check the sentence against.
    Passed in rather than computed, because expansion is the coordinator's
    rule and a second copy of it would drift.

    Deterministic and total: every input returns a kind, and the kind is
    ``COMMAND`` whenever the evidence would not survive being read out loud.
    """
    signals = signals_for(spec)
    if task_count is not None:
        signals = replace(signals, task_count=int(task_count))
    return _classify(signals)


def _classify(s: Signals) -> tuple[WorkloadKind, str]:
    """The ladder, strongest declaration first.

    Order is the contract, and it is an order of **how explicitly the author
    said it**, not of how much the router would like each answer:

    1. ``mode: federated`` is a word in the file. Nothing outranks it, and the
       parser already refuses to combine it with either generator, so there is
       no conflict left to resolve here.
    2. A ``sweep`` is a declared generator of independent trials. It outranks
       every hardware-shaped reading below: a sweep of GPU fine-tunes is still
       forty independent tasks, which is what decides where it can run.
    3. Scoring (a declared ``aggregate`` reducer, or validators over a fanned
       out job) outranks the map-shaped reading, because ``concat`` describes
       how outputs are joined and validators describe what they *are*.
    4. Only then the single-task hardware readings, weakest first-signal last:
       a base-model flag, then a bare GPU requirement.
    """
    if s.federated:
        return WorkloadKind.FEDERATED, _federated_evidence(s)

    if s.sweep_axes:
        return WorkloadKind.HPO, _hpo_evidence(s)

    if s.reduce_kind == REDUCE_AGGREGATE:
        return WorkloadKind.EVALUATION, (
            f"declares reduce.kind: aggregate{_validator_clause(s)} — the job's "
            f"result is a score combined from its tasks' outputs, not a model. "
            f"Nothing in {s.source} names the artifact under test, so this is "
            f"'scores its own tasks' outputs' and no more"
        )

    if s.validator_keys and not _is_single_gpu_job(s):
        return WorkloadKind.EVALUATION, (
            f"declares validators over {_join(s.validator_keys)} and asks for "
            f"no GPU — the tasks emit scores rather than weights"
            f"{_generator_clause(s)}"
        )

    if s.partitioned and s.reduce_kind == REDUCE_CONCAT:
        return WorkloadKind.INFERENCE, (
            "a partition of a range with reduce.kind: concat — each task maps "
            "its shard and the outputs are joined, which is a batch of forward "
            "passes. FlashML is a pull system with no serving path, so this "
            "means batch throughput and never per-request latency"
            + _gpu_clause(s)
        )

    if s.model_inputs:
        return WorkloadKind.FINETUNE, (
            f"stages a base model as the declared input "
            f"{_join(s.model_inputs)}{_gpu_clause(s)} — training that starts "
            f"from someone else's weights"
        )

    if s.base_model_flags:
        return WorkloadKind.FINETUNE, (
            f"declares {_join(s.base_model_flags)} in its own args"
            f"{_gpu_clause(s)} — training from existing weights. This rests on "
            f"a flag the author wrote, not on a declared model input: "
            f"{s.source} has no field for one. Fine-tune and training route "
            f"identically, so the reading is reported rather than relied on"
        )

    if _is_single_gpu_job(s):
        return WorkloadKind.TRAINING, (
            f"one task, no sweep and no partition, asking for "
            f"{s.gpus_per_task} GPU(s) per task{_timeout_clause(s)} — a single "
            f"long job that wants a real card for its duration"
        )

    return WorkloadKind.COMMAND, _command_evidence(s)


def _federated_evidence(s: Signals) -> str:
    if s.epochs is not None and s.sync_every is not None:
        rounds = s.rounds
        return (
            f"mode: federated — {s.epochs} pass(es) over the data combining "
            f"every {s.sync_every:g}"
            + (f", so {rounds} round(s)" if rounds else "")
            + ". A round's tasks are chunks of one model's data, not "
            "independent work, and this API aggregates between rounds"
            + (
                f"; the data stays on the host under {_join(s.local_inputs)}"
                if s.local_inputs
                else ""
            )
        )
    return (
        f"labelled {_MODE_LABEL}: {_MODE_FEDERATED} in the compiled spec — one "
        f"round of local training whose tasks are chunks of one model's data, "
        f"with aggregation between rounds"
    )


def _hpo_evidence(s: Signals) -> str:
    count = s.task_count if s.task_count is not None else s.sweep_combinations
    expansion = (
        f"expands to {count} independent trials"
        if count is not None
        else "expands to independent trials"
    )
    reduce_clause = ""
    if s.reduce_kind == REDUCE_RANK:
        reduce_clause = (
            ", ranked by reduce.kind: rank — the trials are being selected "
            "between, which is what makes this a search rather than a fan-out"
        )
    elif s.reduce_kind:
        reduce_clause = f", reduced with reduce.kind: {s.reduce_kind}"
    return (
        f"sweep over {_join(s.sweep_axes)} {expansion} sharing one entrypoint"
        f"{reduce_clause}{_gpu_clause(s)}"
    )


def _command_evidence(s: Signals) -> str:
    """Name what was absent. A fallback that does not say why is a shrug."""
    missing = []
    if not s.sweep_axes:
        missing.append("no sweep")
    if not s.partitioned:
        missing.append("no partition")
    if not s.federated:
        missing.append("no mode: federated")
    if not s.reduce_kind:
        missing.append("no reducer")
    if not s.validator_keys:
        missing.append("no validators")
    if not s.gpus_per_task:
        missing.append("no GPU requirement")
    if not s.base_model_flags and not s.model_inputs:
        missing.append("no base model")
    tail = ""
    if s.partitioned:
        tail = (
            ". It does declare a partition, but nothing says what the shards "
            "compute — no reducer names the output and no validator names a "
            "score"
        )
    elif s.reduce_kind:
        tail = (
            f". Its reduce.kind: {s.reduce_kind} says how outputs are joined "
            f"and not what produced them"
        )
    return (
        f"{s.source} says what to run and not what kind of work it is: "
        f"{', '.join(missing)}. Routed as a plain command, on the gates and "
        f"the price alone{tail}"
    )


# ---------------------------------------------------------------------------
# small readers
# ---------------------------------------------------------------------------


def _is_single_gpu_job(s: Signals) -> bool:
    """One task that asked for at least one GPU. The training shape.

    A generator disqualifies it however many GPUs each task wants: forty
    single-GPU trials are forty independent tasks, and where they can run is
    decided by that and not by the card.
    """
    return (
        not s.has_generator
        and s.gpus_per_task is not None
        and s.gpus_per_task >= 1
    )


def _gpu_clause(s: Signals) -> str:
    if s.gpus_per_task is None:
        return ""
    if s.gpus_per_task <= 0:
        return ", declared as needing no GPU"
    return f", {s.gpus_per_task} GPU(s) per task"


def _timeout_clause(s: Signals) -> str:
    if not s.timeout_seconds:
        return ""
    return f" under a {s.timeout_seconds}s timeout"


def _validator_clause(s: Signals) -> str:
    if not s.validator_keys:
        return ""
    return f" over {_join(s.validator_keys)}"


def _generator_clause(s: Signals) -> str:
    if s.sweep_axes:
        return f", fanned out by a sweep over {_join(s.sweep_axes)}"
    if s.partitioned:
        return ", fanned out over a partition of a range"
    return ", in a single task"


def _join(items: Sequence[str]) -> str:
    return ", ".join(str(item) for item in items)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _rows(value: Any) -> list[Mapping[str, Any]]:
    """``task_params`` as a list of mappings, or empty.

    One row per task, each keyed by the sweep axis names — the compiler's own
    expansion, so its length is the trial count and its keys are the axes.
    """
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item) for item in value if isinstance(item, str))


def _combinations(sweep: Mapping[str, Any]) -> int | None:
    """How many trials a sweep expands to, or ``None`` if it cannot be said.

    ``itertools.product`` over the axes is what ``compile.py`` does, so this
    is the same arithmetic and not an estimate of it. A malformed axis (the
    parser would have refused it, but this module is handed objects it did not
    parse) yields ``None`` rather than a number that would be wrong.
    """
    if not sweep:
        return None
    lengths = []
    for values in sweep.values():
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            return None
        lengths.append(len(values))
    if not lengths or any(length <= 0 for length in lengths):
        return None
    return prod(lengths)


def _gpu_count(value: Any) -> int | None:
    """A GPU requirement, or ``None`` for "the author did not say".

    ``bool`` before ``int`` for the reason ``compile._resources`` states: in
    Python ``True`` is an ``int``, and ``gpus: true`` read as "one GPU" would
    route a job to hardware nobody asked for. A float is refused rather than
    rounded, matching the compiler; an unreadable value is ``None``, which is
    "unstated", which is the safe direction — a job with no declared GPU
    requirement is never classified as training on the strength of one.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return int(value)


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _base_model_flags(args: Any) -> tuple[str, ...]:
    """Which base-model flags the author's own argv declares, verbatim.

    ``--base-model X`` and ``--base-model=X`` both count; the flag is returned
    and the value is not, because the value is the user's business and the
    evidence string is shown in a console.
    """
    if not isinstance(args, Sequence) or isinstance(args, (str, bytes)):
        return ()
    found: list[str] = []
    for token in args:
        if not isinstance(token, str):
            continue
        flag = token.split("=", 1)[0]
        if flag in BASE_MODEL_FLAGS and flag not in found:
            found.append(flag)
    return tuple(found)
