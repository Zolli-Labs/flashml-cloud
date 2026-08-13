"""Compiling a flashml.yaml into a CommandRecipe JobSpec.

The argv is the contract. ``ArgvDockerRunner`` hands the list straight to
``docker run <image> *argv`` with no shell anywhere in the path, so these
tests assert the exact list rather than a rendered string — a test that
checked a joined string would pass on a spec that is subtly wrong about
where one argument ends and the next begins.

The isolation assertions are not ceremony. ``CommandRecipe.expand``
refuses any tier but ``sandboxed`` and rejects the ``allowFallback``
waiver outright; a compiler that emitted anything else would either be
refused upstream or, worse, place a stranger's arbitrary code on an
unsandboxed volunteer machine.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest

from flashruntime.protocol.v1alpha1 import JobSpec

from flashml_cloud_api.compile import (
    CURATED_IMAGE_PYTHON,
    CompileError,
    _resources,
    compile_federated_round,
    compile_to_jobspec,
    sanitize_job_name,
)
from flashml_cloud_api.flashml_yaml import parse_flashml_yaml
from flashml_cloud_api.images import CURATED, CuratedImage, resolve_image

CODE_URI = "artifact://uploads/deadbeef/code.tar.gz"

#: The smallest round that can exist: one slot training the whole pass. Every
#: federated assertion in this file is about isolation, placement, inputs or
#: dependencies — none is about how the data is split — so the split is held
#: at its floor here and exercised in ``test_elastic_layout.py`` instead.
ONE_SLOT = {"slot_chunks": [0], "total_chunks": 1}
PYTORCH = resolve_image("pytorch-cpu")
CUDA = resolve_image("pytorch-cuda")
SLIM = resolve_image("python-slim")


def _config(**over) -> object:
    fields = {
        "version": 1,
        "name": "cifar-sweep",
        "image": "pytorch-cpu",
        "entrypoint": "train.py",
    }
    fields.update(over)
    lines = []
    for key, value in fields.items():
        if isinstance(value, str):
            lines.append(f"{key}: {value}")
        elif isinstance(value, dict):
            lines.append(f"{key}:")
            for k, v in value.items():
                lines.append(f"  {k}: {v}")
        elif isinstance(value, list):
            lines.append(f"{key}: {value!r}".replace("'", '"'))
        else:
            lines.append(f"{key}: {value}")
    return parse_flashml_yaml("\n".join(lines) + "\n")


def _params(spec: dict) -> dict:
    return spec["spec"]["workload"]["parameters"]


# ---------------------------------------------------------------------------
# the plain case
# ---------------------------------------------------------------------------


def test_plain_argv_is_exactly_python_entrypoint_then_args():
    spec = compile_to_jobspec(
        _config(args=["--epochs", "20"]), PYTORCH, CODE_URI, "cifar-sweep"
    )
    assert _params(spec)["command"] == [
        "python",
        "/work/inputs/code/train.py",
        "--epochs",
        "20",
    ]


def test_a_plain_job_has_no_task_params():
    """No sweep means one task, and the recipe expands `task_params: None`
    into exactly that."""
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo")
    assert "task_params" not in _params(spec)


def test_the_workload_type_is_command():
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo")
    assert spec["spec"]["workload"]["type"] == "command"


def test_a_nested_entrypoint_keeps_its_directories():
    spec = compile_to_jobspec(
        _config(entrypoint="src/models/train.py"), PYTORCH, CODE_URI, "demo"
    )
    assert _params(spec)["command"][1] == "/work/inputs/code/src/models/train.py"


def test_the_code_tarball_is_the_only_input_and_is_an_artifact_uri():
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo")
    assert _params(spec)["inputs"] == {"code": CODE_URI}


def test_a_non_artifact_code_uri_is_refused():
    with pytest.raises(CompileError, match="artifact://"):
        compile_to_jobspec(_config(), PYTORCH, "https://example.com/code.tgz", "demo")


def test_the_code_input_is_marked_for_unpacking():
    """The staged artifact is a tarball; without this the executor leaves it
    on disk as one file and the argv points inside a gzip blob."""
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo")
    assert _params(spec)["unpack_inputs"] == ["code"]


@pytest.mark.parametrize(
    "entrypoint", ["train.py", "src/models/train.py"], ids=["flat", "nested"]
)
def test_unpack_inputs_and_the_argv_path_name_the_same_input(entrypoint):
    """The two halves of one contract, asserted together.

    ``unpack_inputs`` decides which input becomes a *directory* under
    ``/work/inputs/``; the argv's second token is a path *inside* that
    directory. They agreed by coincidence until nothing emitted
    ``unpack_inputs`` at all — at which point the argv named a path that
    could not exist. Asserting either one alone would still have passed.
    """
    spec = compile_to_jobspec(_config(entrypoint=entrypoint), PYTORCH, CODE_URI, "demo")
    params = _params(spec)

    unpacked = params["unpack_inputs"]
    assert len(unpacked) == 1
    name = unpacked[0]
    assert name in params["inputs"], "unpacking an input that was never declared"

    argv_path = params["command"][1]
    assert argv_path == f"/work/inputs/{name}/{entrypoint}"


def test_the_recipe_forwards_unpack_inputs_into_the_task_payload():
    """End of the coordinator half: what the compiler emits is what a node
    is actually told, via the real upstream recipe."""
    from flashruntime.recipes.command import CommandRecipe

    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo")
    task = CommandRecipe().expand("job-123", JobSpec.model_validate(spec))[0]
    assert task.payload["unpack_inputs"] == ["code"]
    assert task.payload["argv"][1] == "/work/inputs/code/train.py"


# ---------------------------------------------------------------------------
# isolation: fixed, and not the submitter's to choose
# ---------------------------------------------------------------------------


def test_isolation_tier_is_sandboxed_with_no_fallback():
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo")
    assert spec["spec"]["isolation"] == {"tier": "sandboxed", "allowFallback": False}


def test_the_config_cannot_downgrade_isolation():
    """`isolation` is not even a key flashml.yaml accepts — the parser
    refuses unknown top-level keys, so there is no path from a repo to a
    weaker tier."""
    from flashml_cloud_api.flashml_yaml import ConfigError

    with pytest.raises(ConfigError):
        parse_flashml_yaml(
            "version: 1\nname: x\nimage: python-slim\nentrypoint: t.py\n"
            "isolation: {tier: standard, allowFallback: true}\n"
        )


def test_the_compiled_spec_is_accepted_by_the_real_command_recipe():
    """Not a shape assertion — the actual upstream recipe, which raises on
    a non-sandboxed tier or a malformed argv."""
    from flashruntime.recipes.command import CommandRecipe

    spec = compile_to_jobspec(
        _config(sweep={"lr": "[0.1, 0.2]"}), PYTORCH, CODE_URI, "demo"
    )
    tasks = CommandRecipe().expand("job-123", JobSpec.model_validate(spec))
    assert len(tasks) == 2
    assert tasks[0].payload["isolation"] == {"tier": "sandboxed", "allowFallback": False}


# ---------------------------------------------------------------------------
# pool: the one exception to "fixed and not configurable" — allowFallback
# iff pool, bidirectional and pinned both ways.
# ---------------------------------------------------------------------------


def test_pool_sets_placement_and_couples_the_waiver():
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo", pool="p-1")
    assert spec["spec"]["placement"]["pool"] == "p-1"
    assert spec["spec"]["isolation"] == {"tier": "sandboxed", "allowFallback": True}


def test_no_pool_is_byte_identical_to_before():
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo")
    assert spec["spec"]["isolation"] == {"tier": "sandboxed", "allowFallback": False}
    assert spec["spec"]["placement"]["pool"] == "any"


def test_the_pool_spec_is_accepted_by_the_real_command_recipe():
    from flashruntime.recipes.command import CommandRecipe

    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo", pool="p-1")
    tasks = CommandRecipe().expand("job-123", JobSpec.model_validate(spec))
    assert tasks[0].payload["pool"] == "p-1"
    assert tasks[0].payload["isolation"]["allowFallback"] is True


def test_federated_round_pool_sets_placement_and_couples_the_waiver():
    """Both compilers carry the same rule — a federated round is an
    ordinary command job, and the pool coupling is not special-cased away
    for it."""
    config = parse_flashml_yaml(
        "version: 2\nname: fed\nimage: pytorch-cpu\nentrypoint: train.py\n"
        "mode: federated\nepochs: 2\n"
    )
    spec = compile_federated_round(
        config, PYTORCH, CODE_URI, "fed", round_index=0, weights_uri=None,
        pool="p-1", **ONE_SLOT,
    )
    assert spec["spec"]["placement"]["pool"] == "p-1"
    assert spec["spec"]["isolation"] == {"tier": "sandboxed", "allowFallback": True}


def test_federated_round_without_pool_is_byte_identical_to_before():
    config = parse_flashml_yaml(
        "version: 2\nname: fed\nimage: pytorch-cpu\nentrypoint: train.py\n"
        "mode: federated\nepochs: 2\n"
    )
    spec = compile_federated_round(
        config, PYTORCH, CODE_URI, "fed", round_index=0, weights_uri=None,
        **ONE_SLOT,
    )
    assert spec["spec"]["isolation"] == {"tier": "sandboxed", "allowFallback": False}
    assert spec["spec"]["placement"]["pool"] == "any"


# ---------------------------------------------------------------------------
# sweeps
# ---------------------------------------------------------------------------


def test_a_sweep_expands_to_one_task_per_combination():
    from flashruntime.recipes.command import CommandRecipe

    spec = compile_to_jobspec(
        _config(sweep={"lr": "[0.001, 0.01, 0.1]", "batch_size": "[32, 64]"}),
        PYTORCH,
        CODE_URI,
        "demo",
    )
    assert len(_params(spec)["task_params"]) == 6
    tasks = CommandRecipe().expand("job-1", JobSpec.model_validate(spec))
    assert len(tasks) == 6


def test_the_sweep_argv_is_exactly_right_after_expansion():
    from flashruntime.recipes.command import CommandRecipe

    spec = compile_to_jobspec(
        _config(args=["--epochs", "20"], sweep={"lr": "[0.001, 0.01]"}),
        PYTORCH,
        CODE_URI,
        "demo",
    )
    assert _params(spec)["command"] == [
        "python",
        "/work/inputs/code/train.py",
        "--epochs",
        "20",
        "--lr",
        "{lr}",
    ]
    tasks = CommandRecipe().expand("job-1", JobSpec.model_validate(spec))
    assert [t.payload["argv"] for t in tasks] == [
        ["python", "/work/inputs/code/train.py", "--epochs", "20", "--lr", "0.001"],
        ["python", "/work/inputs/code/train.py", "--epochs", "20", "--lr", "0.01"],
    ]


def test_every_sweep_combination_appears_exactly_once():
    from flashruntime.recipes.command import CommandRecipe

    spec = compile_to_jobspec(
        _config(sweep={"lr": "[0.1, 0.2]", "seed": "[1, 2, 3]"}),
        PYTORCH,
        CODE_URI,
        "demo",
    )
    tasks = CommandRecipe().expand("job-1", JobSpec.model_validate(spec))
    combos = {(t.payload["argv"][-3], t.payload["argv"][-1]) for t in tasks}
    assert combos == {
        (lr, seed) for lr in ("0.1", "0.2") for seed in ("1", "2", "3")
    }


def test_a_brace_in_a_user_arg_survives_a_sweep():
    """Every token is run through str.format upstream, not only the ones
    with placeholders. Without escaping, `--filter={"a":1}` would raise a
    KeyError inside the recipe and read to the user as a platform bug."""
    from flashruntime.recipes.command import CommandRecipe

    config = parse_flashml_yaml(
        'version: 1\nname: demo\nimage: pytorch-cpu\nentrypoint: train.py\n'
        'args: ["--filter", "{\\"a\\": 1}"]\n'
        "sweep:\n  lr: [0.1]\n"
    )
    spec = compile_to_jobspec(config, PYTORCH, CODE_URI, "demo")
    tasks = CommandRecipe().expand("job-1", JobSpec.model_validate(spec))
    assert tasks[0].payload["argv"] == [
        "python",
        "/work/inputs/code/train.py",
        "--filter",
        '{"a": 1}',
        "--lr",
        "0.1",
    ]


def test_a_boolean_sweep_value_does_not_become_a_number():
    from flashruntime.recipes.command import CommandRecipe

    config = parse_flashml_yaml(
        "version: 1\nname: demo\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "sweep:\n  amp: [true, false]\n"
    )
    spec = compile_to_jobspec(config, PYTORCH, CODE_URI, "demo")
    tasks = CommandRecipe().expand("job-1", JobSpec.model_validate(spec))
    assert [t.payload["argv"][-1] for t in tasks] == ["true", "false"]


def test_a_sweep_key_that_is_not_an_identifier_is_refused():
    config = parse_flashml_yaml(
        "version: 1\nname: demo\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "sweep:\n  'learning-rate': [0.1]\n"
    )
    with pytest.raises(CompileError, match="identifier"):
        compile_to_jobspec(config, PYTORCH, CODE_URI, "demo")


# ---------------------------------------------------------------------------
# entrypoint hardening: this string is user-supplied
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entrypoint", ["../train.py", "a/../../train.py", "/etc/passwd"])
def test_an_entrypoint_escaping_the_repo_never_reaches_argv(entrypoint):
    config = parse_flashml_yaml(
        f"version: 1\nname: demo\nimage: pytorch-cpu\nentrypoint: {entrypoint!r}\n"
    )
    if entrypoint.startswith("/"):
        # An absolute path is normalised to a repo-relative one rather than
        # refused — but it must still land under /work/inputs/code.
        spec = compile_to_jobspec(config, PYTORCH, CODE_URI, "demo")
        assert _params(spec)["command"][1].startswith("/work/inputs/code/")
    else:
        with pytest.raises(CompileError):
            compile_to_jobspec(config, PYTORCH, CODE_URI, "demo")


# ---------------------------------------------------------------------------
# image, name, resources
# ---------------------------------------------------------------------------


def test_the_image_is_the_curated_pinned_reference():
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo")
    image = spec["spec"]["image"]
    assert f"{image['repository']}:{image['tag']}" == PYTORCH.reference
    assert image["tag"] != "latest"


def test_a_registry_port_is_not_mistaken_for_a_tag():
    from flashml_cloud_api.images import CuratedImage

    image = CuratedImage(
        alias="test", reference="registry.local:5000/team/img:1.2.3",
        packages=SLIM.packages, description="",
    )
    # This reference is not a curated GHCR image, so `manifest_for` returns
    # None; a `dependencies:` declaration is required to avoid the refusal
    # that exists for exactly that case (see the "dependencies" tests below)
    # — orthogonal to what this test actually checks, which is the
    # repository/tag split.
    spec = compile_to_jobspec(
        _config(image="python-slim", dependencies=["numpy==1.26.4"]),
        image, CODE_URI, "demo",
    )
    assert spec["spec"]["image"]["repository"] == "registry.local:5000/team/img"
    assert spec["spec"]["image"]["tag"] == "1.2.3"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("CIFAR Sweep #2", "cifar-sweep-2"),
        ("---weird---", "weird"),
        ("!!!", "job"),
        ("a" * 90, "a" * 63),
    ],
)
def test_job_names_are_coerced_to_dns_labels(raw, expected):
    assert sanitize_job_name(raw) == expected


def test_a_users_free_text_name_never_breaks_the_spec():
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "CIFAR Sweep #2")
    assert spec["metadata"]["name"] == "cifar-sweep-2"
    JobSpec.model_validate(spec)  # would raise on a non-DNS-1123 name


def test_resources_are_translated_into_the_upstream_units():
    spec = compile_to_jobspec(
        _config(resources={"cpus": 2, "memory_gb": 4}), PYTORCH, CODE_URI, "demo"
    )
    assert spec["spec"]["resources"]["cpuPerTask"] == 2.0
    assert spec["spec"]["resources"]["memoryPerTask"] == "4096Mi"


@pytest.mark.parametrize("bad", [0, -1, "two", True])
def test_a_nonsensical_cpu_count_is_refused(bad):
    config = parse_flashml_yaml(
        "version: 1\nname: demo\nimage: pytorch-cpu\nentrypoint: t.py\n"
        f"resources: {{cpus: {str(bad).lower() if isinstance(bad, bool) else repr(bad)}}}\n"
    )
    with pytest.raises(CompileError, match="cpus"):
        compile_to_jobspec(config, PYTORCH, CODE_URI, "demo")


# --- resources.gpus -> gpuPerTask ------------------------------------------
#
# ``compile_to_jobspec`` returns ``JobSpec.model_validate(...).model_dump()``,
# so its output carries only the fields the PINNED flashruntime declares.
# ``ResourcesSpec.gpuPerTask`` lands in flashruntime 0.5.0 (plan Task 1); the
# pin is still 0.4.0 (plan Task 10 moves it). Pydantic's default
# ``extra="ignore"`` means the field is dropped SILENTLY in between — no
# error, no warning, just a GPU job that compiles to a CPU spec.
#
# So the emission is asserted against ``_resources``, the function that owns
# the translation, and the survival through the round-trip is asserted
# separately and gated on the pin, where it becomes visible the moment the pin
# moves instead of being quietly untested.


def _gpu_pin_supports_gpu_per_task() -> bool:
    from flashruntime.protocol.v1alpha1 import ResourcesSpec

    return "gpuPerTask" in ResourcesSpec.model_fields


def test_a_gpu_request_is_translated_into_gpu_per_task():
    assert _resources(_config(resources={"gpus": 1}))["gpuPerTask"] == 1


def test_gpu_per_task_is_an_int_not_a_float():
    # The placement gate upstream refuses a non-int requirement fail-closed,
    # so emitting 1.0 here would make every GPU job unplaceable everywhere.
    value = _resources(_config(resources={"gpus": 1}))["gpuPerTask"]
    assert isinstance(value, int) and not isinstance(value, bool)


def test_no_gpu_request_means_no_gpu_per_task():
    # THE API never injects the key. That is this module's contract and it
    # holds regardless of what the pinned runtime declares.
    assert "gpuPerTask" not in _resources(_config())

    # The compiled SPEC is a different question, and the answer changed with
    # the pin. `compile_to_jobspec` returns `JobSpec.model_validate(...)
    # .model_dump_json()`, which materialises every DECLARED field's default.
    # On flashruntime 0.4.0 `ResourcesSpec` had no `gpuPerTask`, so the field
    # vanished; on 0.4.1 it is declared `int = Field(ge=0, default=0)` and
    # surfaces as 0 — the same way `cpuPerTask` and `minimumWorkers` already
    # did. The `local_inputs` analogy this test used to draw does not hold:
    # that one lives in the free-form payload, not in the model.
    #
    # 0 is not a weaker absent. `IsolationAwarePlacement` engages the GPU gate
    # only for `required_gpus > 0`; the runtime's own docstring says `gpus: 0`
    # "requires nothing and runs anywhere". Nothing hashes or diffs a spec, so
    # a materialised default perturbs no existing job.
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo")
    assert spec["spec"]["resources"]["gpuPerTask"] == 0


def test_an_explicit_zero_gpus_is_allowed_and_means_none():
    # Non-negative, not positive: unlike cpus/memory_gb, 0 is the meaningful
    # default and a user writing it out is not making an error.
    assert _resources(_config(resources={"gpus": 0}))["gpuPerTask"] == 0


def test_cpus_and_gpus_are_translated_side_by_side():
    resources = _resources(_config(resources={"cpus": 2, "memory_gb": 4, "gpus": 1}))
    assert resources == {
        "cpuPerTask": 2.0,
        "memoryPerTask": "4096Mi",
        "gpuPerTask": 1,
    }


@pytest.mark.parametrize("bad", [-1, "one", 1.5, True])
def test_a_nonsensical_gpu_count_is_refused(bad):
    # `True` is in this list deliberately: bool is an int subclass, so a bare
    # isinstance(x, int) check would read `gpus: true` as "one GPU".
    config = parse_flashml_yaml(
        "version: 1\nname: demo\nimage: pytorch-cpu\nentrypoint: t.py\n"
        f"resources: {{gpus: {str(bad).lower() if isinstance(bad, bool) else repr(bad)}}}\n"
    )
    with pytest.raises(CompileError, match="gpus"):
        compile_to_jobspec(config, PYTORCH, CODE_URI, "demo")


def test_a_gpu_request_survives_the_jobspec_round_trip():
    """The hop that is currently broken by the pin, and will not announce it.

    Plan Task 10 bumps the pin to flashruntime 0.5.0. Until then this skips;
    after it, it is the assertion that catches the field being dropped again.
    """
    if not _gpu_pin_supports_gpu_per_task():
        pytest.skip(
            "pinned flashruntime has no ResourcesSpec.gpuPerTask, so the "
            "JobSpec round-trip in compile_to_jobspec drops it silently. "
            "Unblocked by plan Task 10 (release flashruntime 0.5.0 and re-pin "
            "in apps/api/pyproject.toml, render.yaml, and Makefile FLASHML_PIN)."
        )
    spec = compile_to_jobspec(
        _config(resources={"gpus": 1}), PYTORCH, CODE_URI, "demo"
    )
    assert spec["spec"]["resources"]["gpuPerTask"] == 1


def test_the_pin_gap_is_a_silent_drop_and_not_a_validation_error():
    """Documents WHY the test above skips, so the gap cannot be misread.

    If this ever fails, the pin has moved and
    ``test_a_gpu_request_survives_the_jobspec_round_trip`` has taken over.
    """
    if _gpu_pin_supports_gpu_per_task():
        pytest.skip("pin now carries gpuPerTask; the round-trip test asserts it")
    # No CompileError: pydantic's default extra="ignore" drops the unknown
    # field rather than refusing the spec. That is the whole hazard.
    spec = compile_to_jobspec(
        _config(resources={"gpus": 1}), PYTORCH, CODE_URI, "demo"
    )
    assert "gpuPerTask" not in spec["spec"]["resources"]


def test_a_timeout_is_carried_into_the_workload_parameters():
    spec = compile_to_jobspec(
        _config(timeout_seconds=1800), PYTORCH, CODE_URI, "demo"
    )
    assert _params(spec)["timeout_seconds"] == 1800


def test_the_backend_is_leases_so_the_job_reaches_volunteer_nodes():
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo")
    assert spec["spec"]["execution"]["backend"] == "leases"


# ---------------------------------------------------------------------------
# local_inputs — carried to the placement gate, and uploaded NOWHERE
# ---------------------------------------------------------------------------


def _local_config(**over):
    return _config(local_inputs=["patients"], **over)


def test_local_inputs_reach_the_workload_parameters():
    # This is the only channel by which the coordinator's fourth placement
    # gate can learn what the task needs.
    spec = compile_to_jobspec(_local_config(), PYTORCH, CODE_URI, "demo")
    assert _params(spec)["local_inputs"] == ["patients"]


def test_local_inputs_are_absent_when_the_job_asks_for_none():
    # Absent, never `[]` — the same judgement `unpack_inputs` records
    # upstream: an omitted key keeps every pre-existing job's payload byte for
    # byte what it was, so the untouched path stays exercised.
    assert "local_inputs" not in _params(compile_to_jobspec(
        _config(), PYTORCH, CODE_URI, "demo"))


def test_a_local_input_is_not_an_artifact_and_nothing_is_uploaded():
    """Definition-of-done item 7, asserted rather than assumed.

    The entire premise of the feature is that the host's directory never
    leaves the host. If a label were ever added to `inputs`, the compiler
    would be declaring an `artifact://` URI for it — which means the API
    would expect the bytes to have been staged, i.e. uploaded. If it were
    added to `unpack_inputs`, flashnode would run an archive extractor over
    a download that must never exist. Neither may happen, and neither is
    something a reader can confirm by looking at the compiler.
    """
    spec = compile_to_jobspec(_local_config(), PYTORCH, CODE_URI, "demo")
    params = _params(spec)

    # The staged code tarball is the ONLY artifact this job has.
    assert params["inputs"] == {"code": CODE_URI}
    assert params["unpack_inputs"] == ["code"]
    assert "patients" not in params["inputs"]
    assert "patients" not in params["unpack_inputs"]

    # No artifact:// URI anywhere mentions the label, under any spelling.
    for uri in params["inputs"].values():
        assert "patients" not in uri
    assert spec["spec"]["artifacts"]["outputPrefix"] == "artifact://jobs/{job_id}/"

    # And the label appears in exactly one place in the whole compiled spec.
    flat = json.dumps(spec)
    assert flat.count("patients") == 1


def test_local_inputs_do_not_change_the_argv():
    # A local input is mounted by the agent, not passed on the command line:
    # the job's code opens `inputs/<label>/` exactly as it would an
    # artifact-sourced input, so argv must be identical either way.
    plain = _params(compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo"))
    local = _params(compile_to_jobspec(_local_config(), PYTORCH, CODE_URI, "demo"))
    assert local["command"] == plain["command"]


def test_several_local_inputs_keep_their_order_and_are_all_carried():
    config = _config(local_inputs=["patients", "labs"])
    params = _params(compile_to_jobspec(config, PYTORCH, CODE_URI, "demo"))
    assert params["local_inputs"] == ["patients", "labs"]
    assert params["inputs"] == {"code": CODE_URI}


def test_a_spec_carrying_local_inputs_still_validates_upstream():
    # `local_inputs` is an extra workload parameter; the upstream JobSpec must
    # accept it, or the feature is unshippable regardless of everything else.
    spec = compile_to_jobspec(_local_config(), PYTORCH, CODE_URI, "demo")
    JobSpec.model_validate(spec)


def test_local_inputs_survive_a_sweep_expansion():
    spec = compile_to_jobspec(
        _config(local_inputs=["patients"], sweep={"lr": [0.1, 0.2]}),
        PYTORCH, CODE_URI, "demo",
    )
    params = _params(spec)
    assert params["local_inputs"] == ["patients"]
    assert len(params["task_params"]) == 2


def test_federated_rounds_carry_local_inputs_and_upload_nothing():
    # Federated training over data that cannot be pooled is the use case this
    # feature exists for, so the round compiler must carry it too — and must
    # still stage only the code tarball and the aggregated weights.
    config = parse_flashml_yaml(
        "version: 2\nname: fed\nimage: pytorch-cpu\nentrypoint: train.py\n"
        "mode: federated\nepochs: 2\n"
        'local_inputs: ["patients"]\n'
    )
    spec = compile_federated_round(
        config, PYTORCH, CODE_URI, "fed", round_index=1,
        weights_uri="artifact://jobs/j/round-000/weights.json",
        **ONE_SLOT,
    )
    params = _params(spec)
    assert params["local_inputs"] == ["patients"]
    assert set(params["inputs"]) == {"code", "weights"}
    assert params["unpack_inputs"] == ["code"]
    assert json.dumps(spec).count("patients") == 1


def test_federated_rounds_without_local_inputs_are_unchanged():
    config = parse_flashml_yaml(
        "version: 2\nname: fed\nimage: pytorch-cpu\nentrypoint: train.py\n"
        "mode: federated\nepochs: 2\n"
    )
    spec = compile_federated_round(
        config, PYTORCH, CODE_URI, "fed", round_index=0, weights_uri=None,
        **ONE_SLOT,
    )
    assert "local_inputs" not in _params(spec)


def test_a_local_inputs_spec_is_accepted_by_the_real_recipe():
    """The pinned ``CommandRecipe`` must not reject the extra parameter.

    A workload parameter the recipe refuses would surface as a 422 from the
    coordinator on every local-data job — the failure mode this module's
    docstring exists to prevent. Asserted against the real recipe, not a
    stub, because "an unknown key is ignored" is a property of that code and
    not a rule anyone wrote down.

    Note what this does NOT assert: that ``local_inputs`` reaches
    ``task.payload``. It does not, on the pinned runtime — ``expand`` builds
    payloads from a fixed key list. See ``compile._local_inputs``.
    """
    from flashruntime.recipes.command import CommandRecipe

    spec = compile_to_jobspec(_local_config(), PYTORCH, CODE_URI, "demo")
    tasks = CommandRecipe().expand("job-1", JobSpec.model_validate(spec))
    assert len(tasks) == 1
    # Whatever else is true, no artifact was invented for the local dataset.
    assert tasks[0].payload["inputs"] == {"code": CODE_URI}


# ---------------------------------------------------------------------------
# partition / validators / reduce — the §4–§6 surface
#
# All three are forwarded VERBATIM into workload parameters. The coordinator
# owns their semantics (which reducers exist, how a range splits, what a
# schema means); this module's job is to carry them there without editing.
# ---------------------------------------------------------------------------


def test_a_partition_is_forwarded_for_the_coordinator_to_expand():
    """Not expanded here. `expand_partition` lives upstream, and computing
    the shard list in two places is how the console and the coordinator come
    to disagree about how many tasks a job has."""
    spec = compile_to_jobspec(
        _config(partition={"range": "[0, 1000]", "shards": 8}),
        PYTORCH, CODE_URI, "j",
    )
    assert _params(spec)["partition"] == {"range": [0, 1000], "shards": 8}
    assert "task_params" not in _params(spec)


def test_a_partitioned_job_keeps_its_placeholders_unescaped():
    """`--out shard-{shard_index}.jsonl` must survive as a placeholder. The
    brace-escaping that protects a plain job's argv would turn it into the
    literal text `{{shard_index}}` and every shard would write one filename."""
    spec = compile_to_jobspec(
        _config(
            partition={"range": "[0, 10]", "shards": 2},
            args=["--out", "shard-{shard_index}.jsonl"],
        ),
        PYTORCH, CODE_URI, "j",
    )
    assert "--out" in _params(spec)["command"]
    assert "shard-{shard_index}.jsonl" in _params(spec)["command"]


def test_validators_are_forwarded():
    spec = compile_to_jobspec(
        _config(validators={"keys": '["accuracy"]'}), PYTORCH, CODE_URI, "j"
    )
    assert _params(spec)["validators"] == {"keys": ["accuracy"]}


def test_reduce_is_forwarded():
    spec = compile_to_jobspec(
        _config(reduce={"kind": "rank", "metric": "accuracy"}),
        PYTORCH, CODE_URI, "j",
    )
    assert _params(spec)["reduce"] == {"kind": "rank", "metric": "accuracy"}


def test_a_job_declaring_none_of_them_carries_none_of_them():
    """Absent stays absent, never an empty dict — the same rule the payload
    forwards upstream follow, so the no-declaration path keeps being the one
    that is exercised."""
    params = _params(compile_to_jobspec(_config(), PYTORCH, CODE_URI, "j"))
    assert "partition" not in params
    assert "validators" not in params
    assert "reduce" not in params


# ---------------------------------------------------------------------------
# dependencies — the curated image's own manifest as the base, the job's
# declared `dependencies:` appended as extras (Task 4 of the
# dependency-declaration plan). See flashml_cloud_api.compile._dependencies.
# ---------------------------------------------------------------------------


def _custom_image(reference: str = "docker.io/someone/theirs:v1") -> CuratedImage:
    """A CuratedImage naming a reference `manifest_for` does not recognise —
    the unit-test stand-in for "a non-curated image", built the same way
    `test_a_registry_port_is_not_mistaken_for_a_tag` builds one above."""
    return CuratedImage(alias="custom", reference=reference, packages=frozenset(), description="")


def test_a_curated_image_supplies_the_base():
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo")
    deps = _params(spec)["dependencies"]
    assert "torch==2.3.1" in deps
    assert "--index-url https://download.pytorch.org/whl/cpu" in deps


def test_extras_are_appended_after_the_base():
    """Order matters: the base's --index-url applies to what follows, and an
    extra must not be resolved before the base pins land."""
    spec = compile_to_jobspec(
        _config(dependencies=["transformers==4.44.0"]), PYTORCH, CODE_URI, "demo"
    )
    deps = _params(spec)["dependencies"]
    assert deps[-1] == "transformers==4.44.0"
    assert deps.index("torch==2.3.1") < deps.index("transformers==4.44.0")


def test_the_cuda_image_supplies_a_different_base():
    """Choosing the image IS choosing the accelerator — the compiler does
    not know what a GPU is and must not need to."""
    spec = compile_to_jobspec(_config(), CUDA, CODE_URI, "demo")
    deps = _params(spec)["dependencies"]
    assert "--index-url https://download.pytorch.org/whl/cu124" in deps
    assert "torch==2.4.1" in deps


def test_a_curated_image_with_no_dependencies_is_not_refused():
    """The case `base is None` (vs `base == []`) exists to protect:
    python-slim genuinely installs nothing beyond its base, and that must
    never be refused as though it were an unrecognised image."""
    spec = compile_to_jobspec(_config(image="python-slim"), SLIM, CODE_URI, "demo")
    assert "dependencies" not in _params(spec)


def test_a_custom_image_with_no_extras_is_refused():
    """An unsandboxed host cannot reproduce an environment nobody described."""
    with pytest.raises(CompileError, match="dependencies"):
        compile_to_jobspec(_config(), _custom_image(), CODE_URI, "demo")


def test_a_custom_image_with_extras_carries_only_the_extras():
    spec = compile_to_jobspec(
        _config(dependencies=["numpy==1.26.4"]), _custom_image(), CODE_URI, "demo"
    )
    assert _params(spec)["dependencies"] == ["numpy==1.26.4"]


def test_a_curated_image_with_no_declared_extras_still_carries_its_base():
    # Every job deployed today declares no `dependencies:`; a curated image
    # must still resolve to its own manifest without the submitter asking.
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo")
    assert "dependencies" in _params(spec)


def test_federated_rounds_also_resolve_dependencies():
    """A federated round is an ordinary command job (see compile_federated_
    round's docstring), so the same resolution applies to it."""
    config = parse_flashml_yaml(
        "version: 2\nname: fed\nimage: pytorch-cpu\nentrypoint: train.py\n"
        "mode: federated\nepochs: 2\n"
        'dependencies: ["transformers==4.44.0"]\n'
    )
    spec = compile_federated_round(
        config, PYTORCH, CODE_URI, "fed", round_index=0, weights_uri=None,
        **ONE_SLOT,
    )
    deps = _params(spec)["dependencies"]
    assert deps[-1] == "transformers==4.44.0"
    assert "torch==2.3.1" in deps


# ---------------------------------------------------------------------------
# extra_dependencies — the extras ALONE, keyed separately from `dependencies`
# so the coordinator's placement gate can tell "the job's own extras" apart
# from "the whole install list". A container host already has the base
# baked into its image; only the extras can ever be missing. See
# flashruntime's `IsolationAwarePlacement` (eighth gate, keys on
# `extra_dependencies`, not `dependencies`) and `_dependencies` above.
# ---------------------------------------------------------------------------


def test_a_curated_image_with_no_extras_emits_no_extra_dependencies_key():
    """Table row 1: curated image, nothing declared. `dependencies` carries
    the base so a no-container host can still materialise it, but there are
    no extras — and this is the case that MUST keep a container host
    eligible, so `extra_dependencies` must be absent, not `[]`."""
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo")
    params = _params(spec)
    assert "dependencies" in params
    assert "extra_dependencies" not in params


def test_a_curated_image_with_an_extra_emits_only_the_extra():
    """Table row 2: curated image + `transformers==4.44.0`. `dependencies`
    is base+extras (unchanged); `extra_dependencies` is the extra ALONE —
    the base (torch and its --index-url line) must not leak into it, or a
    container host that already has the base would be wrongly refused."""
    spec = compile_to_jobspec(
        _config(dependencies=["transformers==4.44.0"]), PYTORCH, CODE_URI, "demo"
    )
    params = _params(spec)
    assert params["dependencies"][-1] == "transformers==4.44.0"
    assert "torch==2.3.1" in params["dependencies"]
    assert params["extra_dependencies"] == ["transformers==4.44.0"]


def test_a_custom_image_with_extras_has_matching_dependencies_and_extras():
    """Table row 3: custom image + extras (base is None). The whole install
    list IS the extras here, so both keys carry the same thing."""
    spec = compile_to_jobspec(
        _config(dependencies=["numpy==1.26.4"]), _custom_image(), CODE_URI, "demo"
    )
    params = _params(spec)
    assert params["dependencies"] == ["numpy==1.26.4"]
    assert params["extra_dependencies"] == ["numpy==1.26.4"]


def test_a_curated_image_with_empty_base_and_no_extras_emits_neither_key():
    """Table row 4: curated image with an empty base (python-slim), no
    extras. The resolved list is empty, so `dependencies` stays absent
    (existing rule) and `extra_dependencies` — also empty — stays absent
    too, never `[]`."""
    spec = compile_to_jobspec(_config(image="python-slim"), SLIM, CODE_URI, "demo")
    params = _params(spec)
    assert "dependencies" not in params
    assert "extra_dependencies" not in params


def test_federated_rounds_also_emit_extra_dependencies():
    """A federated round is an ordinary command job, so the same
    extra_dependencies resolution applies to it too."""
    config = parse_flashml_yaml(
        "version: 2\nname: fed\nimage: pytorch-cpu\nentrypoint: train.py\n"
        "mode: federated\nepochs: 2\n"
        'dependencies: ["transformers==4.44.0"]\n'
    )
    spec = compile_federated_round(
        config, PYTORCH, CODE_URI, "fed", round_index=0, weights_uri=None,
        **ONE_SLOT,
    )
    assert _params(spec)["extra_dependencies"] == ["transformers==4.44.0"]


# ---------------------------------------------------------------------------
# python — the interpreter the job's pins were resolved against
#
# 2026-08-13, live (findings §6.1): the Alibaba FC sandbox ships Python
# 3.13.13; the curated sklearn manifest pins `numpy==1.26.4`, whose wheels
# stop at cp312. Every trusted-tier env build on that node fell into a source
# compile and died, and the scheduler re-fed the same task to the same node
# four times until TASK_EXHAUSTED. A pod on Ubuntu 20.04's Python 3.8 hit the
# inverse. `dependencies` guarantees identical PACKAGES and says nothing about
# the INTERPRETER they were pinned against.
#
# Precedence: the submitter's `python:` > the curated-image table > absent.
# Emitted only alongside `dependencies` (no install list, no advice to give),
# and never guessed. See `compile.PYTHON_PARAM`.
# ---------------------------------------------------------------------------

SKLEARN = resolve_image("sklearn")

#: `_config` writes each string value into the YAML verbatim, so a version
#: has to arrive already quoted — which is also the real authoring rule: an
#: unquoted `python: 3.10` is a YAML float and means 3.1. Pinned in
#: tests/test_flashml_yaml.py.
QUOTED_312 = '"3.12"'


def test_a_curated_image_job_declares_the_interpreter_its_pins_assume():
    """The sklearn image is the one §6.1 caught: its manifest pins numpy
    1.26.4, and that pin is only reproducible on the interpreter it was
    resolved against."""
    spec = compile_to_jobspec(_config(image="sklearn"), SKLEARN, CODE_URI, "demo")
    params = _params(spec)
    assert params["python"] == "3.11"
    assert re.fullmatch(r"3\.\d+", params["python"])


def test_the_cuda_image_declares_a_different_interpreter_than_the_others():
    """Not a copy-paste row: `images/pytorch-cuda/Dockerfile` builds on
    `nvidia/cuda:12.4.1-runtime-ubuntu22.04` and apt-installs python3, and
    says in as many words that "Ubuntu 22.04 carries python 3.10, not the
    3.11.9 the other curated images use". Assuming the four agree is the
    mistake this table exists to prevent."""
    assert _params(compile_to_jobspec(_config(), CUDA, CODE_URI, "demo"))["python"] == "3.10"
    assert _params(compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo"))["python"] == "3.11"


@pytest.mark.parametrize("alias", sorted(CURATED))
def test_every_curated_image_has_an_interpreter_recorded_for_it(alias):
    """The tripwire for the mirror table. `CURATED_IMAGE_PYTHON` is a
    hand-maintained copy of the public repo's Dockerfile `FROM` lines
    (TODO(flashruntime): export python in the manifest), so adding a curated
    image without recording its interpreter must break a test rather than
    silently ship a job that says nothing about its Python.

    If an image's interpreter genuinely cannot be established, leave it out of
    the table AND out of this test deliberately — absent stays absent, and
    guessing is the one thing that must not happen."""
    image = CURATED[alias]
    assert image.reference in CURATED_IMAGE_PYTHON
    assert re.fullmatch(r"3\.\d+", CURATED_IMAGE_PYTHON[image.reference])


def test_the_interpreter_table_carries_no_row_for_an_image_that_is_gone():
    """The other direction, and the one a coverage check alone would miss: a
    row keyed on an alias or a tag this API no longer resolves is dead weight
    that reads as a maintained fact. The table is derived from `CURATED`, so
    the two sets must be identical."""
    assert set(CURATED_IMAGE_PYTHON) == {image.reference for image in CURATED.values()}


def test_a_job_that_resolves_no_dependencies_declares_no_interpreter():
    """python-slim with no extras resolves an empty install list, so there is
    no environment build to advise. Absent, not None and not "": the key is
    metadata about `dependencies`, and it does not exist without it."""
    params = _params(compile_to_jobspec(_config(image="python-slim"), SLIM, CODE_URI, "demo"))
    assert "dependencies" not in params
    assert "python" not in params


def test_a_custom_image_with_extras_and_no_declaration_declares_no_interpreter():
    """Never guessed. Nothing here knows what interpreter a stranger's image
    ships, and inventing one would provision the wrong Python with total
    confidence — the exact failure §6.1 records, self-inflicted."""
    params = _params(compile_to_jobspec(
        _config(dependencies=["numpy==1.26.4"]), _custom_image(), CODE_URI, "demo"
    ))
    assert params["dependencies"] == ["numpy==1.26.4"]
    assert "python" not in params


def test_a_declared_python_makes_a_custom_image_provisionable():
    """The user-driven path, and the reason the key is not a fleet-wide
    hardcode: a submitter who names their own image can still say what its
    wheels were built for."""
    params = _params(compile_to_jobspec(
        _config(dependencies=["numpy==1.26.4"], python=QUOTED_312),
        _custom_image(), CODE_URI, "demo",
    ))
    assert params["python"] == "3.12"


def test_a_declared_python_wins_over_the_curated_default():
    """The table resolves the submitter's IMAGE choice; it does not overrule
    the submitter. A default that cannot be overruled is a constraint."""
    params = _params(compile_to_jobspec(
        _config(image="sklearn", python=QUOTED_312), SKLEARN, CODE_URI, "demo"
    ))
    assert CURATED_IMAGE_PYTHON[SKLEARN.reference] == "3.11"
    assert params["python"] == "3.12"


def test_a_declaration_on_a_job_with_no_dependencies_is_still_not_emitted():
    """Precedence does not defeat the emission rule: a declared interpreter
    is advice about an install list, and python-slim with no extras has
    none."""
    params = _params(compile_to_jobspec(
        _config(image="python-slim", python=QUOTED_312), SLIM, CODE_URI, "demo"
    ))
    assert "dependencies" not in params
    assert "python" not in params


@pytest.mark.parametrize("bad", ["3", "3.11.4", "pypy3.11", "4.0", "", 311, 3.11])
def test_the_compiler_re_checks_the_declaration_it_is_handed(bad):
    """`parse_flashml_yaml` already refused these, and this checks again —
    the judgement `_entrypoint_path` records. A `FlashmlConfig` is an
    ordinary dataclass any caller can build, and this is the module that
    decides what reaches a host's interpreter provisioning."""
    config = replace(_config(dependencies=["numpy==1.26.4"]), python=bad)
    with pytest.raises(CompileError, match="python"):
        compile_to_jobspec(config, PYTORCH, CODE_URI, "demo")


def test_a_malformed_declaration_is_refused_even_when_nothing_would_be_emitted():
    """Validated before the emission decision, not inside it: a value this
    module would never send must still never be silently accepted."""
    config = replace(_config(image="python-slim"), python="3.11.4")
    with pytest.raises(CompileError, match="python"):
        compile_to_jobspec(config, SLIM, CODE_URI, "demo")


def test_the_interpreter_rides_with_dependencies_through_the_jobspec_round_trip():
    """Not ceremony — `gpuPerTask` is dropped silently by this exact
    round-trip on some pins (see the resources section above), so "the
    compiler set it" and "the coordinator is told" are two different claims.
    This asserts the key sits in the same parameters dict `dependencies`
    does, on the far side of pydantic."""
    spec = compile_to_jobspec(_config(image="sklearn"), SKLEARN, CODE_URI, "demo")
    reparsed = json.loads(JobSpec.model_validate(spec).model_dump_json())
    params = reparsed["spec"]["workload"]["parameters"]
    assert params["python"] == "3.11"
    assert "numpy==1.26.4" in params["dependencies"]


def test_federated_rounds_also_declare_the_interpreter():
    """A federated round is an ordinary command job (see
    `compile_federated_round`'s docstring), and its tasks build the same
    environment on the same fleet."""
    config = parse_flashml_yaml(
        "version: 2\nname: fed\nimage: sklearn\nentrypoint: train.py\n"
        "mode: federated\nepochs: 2\n"
    )
    spec = compile_federated_round(
        config, SKLEARN, CODE_URI, "fed", round_index=0, weights_uri=None,
        **ONE_SLOT,
    )
    assert _params(spec)["python"] == "3.11"


def test_the_pinned_recipe_stops_the_interpreter_at_the_jobspec():
    """KNOWN GAP, asserted rather than assumed — the one difference from
    `checkpoint`.

    `CommandRecipe.expand` builds each task payload from a fixed key list and
    this key is not on it in flashruntime 0.6.0, so the value reaches the
    JobSpec (test above) and stops there. Same shape as `local_inputs`'s gap:
    the submitter's half of the contract is complete and correct, and the
    hosts are simply not told yet. Closing it is a one-line forward in the
    PUBLIC repo and must land with the flashnode side that reads the key,
    then a release and a four-site pin bump.

    WHEN THE PIN MOVES, THIS TEST FAILS ON PURPOSE. That is the signal the
    gap closed: flip the second assertion to `== "3.11"` and delete this
    paragraph."""
    from flashruntime.recipes.command import CommandRecipe

    spec = compile_to_jobspec(_config(image="sklearn"), SKLEARN, CODE_URI, "demo")
    tasks = CommandRecipe().expand("job-123", JobSpec.model_validate(spec))
    payload = tasks[0].payload
    # The sibling key IS forwarded, so this is a gap in the forwarding list
    # and not a spec the recipe rejected.
    assert "numpy==1.26.4" in payload["dependencies"]
    assert "python" not in payload


def test_allow_partial_reaches_the_retry_policy():
    """It is a `retryPolicy` field upstream, not a workload parameter —
    forwarding it into `parameters` would put it where nothing reads it."""
    spec = compile_to_jobspec(_config(allow_partial=True), PYTORCH, CODE_URI, "j")
    assert spec["spec"]["retryPolicy"]["allowPartial"] is True


def test_a_job_that_did_not_ask_for_partial_does_not_say_so():
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "j")
    assert spec["spec"].get("retryPolicy", {}).get("allowPartial") in (None, False)


# ---------------------------------------------------------------------------
# checkpoint — the one parameter emitted UNCONDITIONALLY
#
# `flashnode/executor/loop.py` gates BOTH the incremental relay of
# `out/ckpt/step-*.json` and the staging of `inputs["resume"]` on
# `payload.get("checkpoint") is not None`. Without the key a job ships no
# checkpoint while it runs and restarts from step 0 after a machine dies —
# silently, with fault tolerance being the one claim FlashML makes about
# volunteer machines. So this is the module's deliberate exception to
# "absent stays absent": here the pre-existing path IS the bug.
#
# The VALUE is never read (the relay's directory, glob and poll interval are
# all hardcoded), so these tests assert `{}` exactly — not truthiness, and
# not a `{dir, glob}` mapping that would read as configuration nothing
# honours.
# ---------------------------------------------------------------------------


def test_every_compiled_job_carries_the_checkpoint_parameter():
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo")
    assert _params(spec)["checkpoint"] == {}


def test_the_checkpoint_value_is_an_empty_object_and_not_a_flag():
    """`{}` is the shape e2e/test_training_resume.py already sends and the
    shape `flashruntime/workloads/command.py` emits. `true` would be a
    second, undocumented spelling of the same non-None test; a `{dir, glob}`
    mapping would read as configuration and be silently ignored, since
    flashnode hardcodes both."""
    value = _params(compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo"))["checkpoint"]
    assert isinstance(value, dict)
    assert value == {}
    assert value is not True


@pytest.mark.parametrize("over", [
    {},
    {"args": ["--epochs", "20"]},
    {"sweep": {"lr": "[0.1, 0.2]"}},
    {"timeout_seconds": 900},
    {"local_inputs": ["patients"]},
    {"allow_partial": True},
    {"partition": {"range": "[0, 100]", "shards": 4}},
])
def test_checkpointing_is_not_conditional_on_anything_in_the_config(over):
    """Unconditional means unconditional: no shape of flashml.yaml, and no
    argument to the compiler, can produce a job that silently loses its work
    to a machine dying."""
    spec = compile_to_jobspec(_config(**over), PYTORCH, CODE_URI, "demo")
    assert _params(spec)["checkpoint"] == {}


def test_a_pool_job_is_checkpointed_too():
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo", pool="team-a")
    assert _params(spec)["checkpoint"] == {}


def test_the_checkpoint_parameter_survives_the_jobspec_round_trip():
    """Not ceremony — `gpuPerTask` is dropped silently by this exact
    round-trip on the current pin (see the resources section above), so
    "the compiler set it" and "the coordinator is told" are two different
    claims. `compile_to_jobspec` returns
    `JobSpec.model_validate(spec).model_dump_json()`, so this asserts the
    key is still there on the far side of pydantic."""
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo")
    reparsed = json.loads(
        JobSpec.model_validate(spec).model_dump_json()
    )
    assert reparsed["spec"]["workload"]["parameters"]["checkpoint"] == {}


def test_the_real_recipe_forwards_checkpoint_into_the_task_payload():
    """The end of the loop, against the pinned runtime rather than a stub.

    `CommandRecipe.expand` already forwards this key verbatim
    (`if p.get("checkpoint") is not None`), which is what makes the fix
    cloud-only: no public-repo change, no PyPI release, no version bump.
    `task.payload["checkpoint"]` is the exact dict flashnode's gate reads.
    """
    from flashruntime.recipes.command import CommandRecipe

    spec = compile_to_jobspec(
        _config(sweep={"lr": "[0.1, 0.2]"}), PYTORCH, CODE_URI, "demo"
    )
    tasks = CommandRecipe().expand("job-123", JobSpec.model_validate(spec))
    assert len(tasks) == 2
    for task in tasks:
        assert task.payload["checkpoint"] == {}


# --- federated rounds ------------------------------------------------------
#
# In scope, and it had the identical bug. Task-level resume is safe here even
# though slot->chunk assignment rotates between rounds: a checkpoint's scope
# is (coordinator job_id, task_id) (`flashruntime/service/checkpoints.py`),
# and every round is a SEPARATE coordinator job — `fedavg` submits one per
# round and records its own `coordinator_job_id` (`db.list_round_job_ids`),
# which the `-rNNN` job-name suffix reflects. So round N+1's `task-000`
# cannot resume round N's `task-000`; only a retried attempt of the same task
# in the same round can, and that attempt trains the same chunk against the
# same broadcast weights.


def _fed_config():
    return parse_flashml_yaml(
        "version: 2\nname: fed\nimage: pytorch-cpu\nentrypoint: train.py\n"
        "mode: federated\nepochs: 2\n"
    )


def test_a_federated_round_is_checkpointed():
    spec = compile_federated_round(
        _fed_config(), PYTORCH, CODE_URI, "fed", round_index=0, weights_uri=None,
        **ONE_SLOT,
    )
    assert _params(spec)["checkpoint"] == {}


def test_every_federated_round_is_checkpointed_including_later_ones():
    for round_index in (0, 1, 7):
        spec = compile_federated_round(
            _fed_config(), PYTORCH, CODE_URI, "fed", round_index=round_index,
            weights_uri=None if round_index == 0
            else "artifact://jobs/j/round-000/weights.json",
            **ONE_SLOT,
        )
        assert _params(spec)["checkpoint"] == {}


def test_each_round_is_its_own_job_so_resume_cannot_cross_a_round():
    """The safety property stated as an assertion rather than a comment.

    Checkpoints are scoped per (job_id, task_id) upstream, so two rounds
    sharing a job would let round N+1's `task-000` resume round N's — with a
    different chunk and stale weights. They do not: each round compiles to a
    distinct job, and `fedavg` submits each one separately and stores its own
    `coordinator_job_id`.
    """
    names = {
        compile_federated_round(
            _fed_config(), PYTORCH, CODE_URI, "fed", round_index=i,
            weights_uri=None, **ONE_SLOT,
        )["metadata"]["name"]
        for i in range(3)
    }
    assert len(names) == 3


def test_a_federated_round_spec_is_accepted_and_forwarded_by_the_real_recipe():
    from flashruntime.recipes.command import CommandRecipe

    spec = compile_federated_round(
        _fed_config(), PYTORCH, CODE_URI, "fed", round_index=1,
        weights_uri="artifact://jobs/j/round-000/weights.json",
        slot_chunks=[0, 1], total_chunks=2,
    )
    tasks = CommandRecipe().expand("job-fed-r001", JobSpec.model_validate(spec))
    assert len(tasks) == 2
    for task in tasks:
        assert task.payload["checkpoint"] == {}
