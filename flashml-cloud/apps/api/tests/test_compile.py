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

import pytest

from flashruntime.protocol.v1alpha1 import JobSpec

from flashml_cloud_api.compile import (
    CompileError,
    compile_federated_round,
    compile_to_jobspec,
    sanitize_job_name,
)
from flashml_cloud_api.flashml_yaml import parse_flashml_yaml
from flashml_cloud_api.images import resolve_image

CODE_URI = "artifact://uploads/deadbeef/code.tar.gz"
PYTORCH = resolve_image("pytorch-cpu")
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
    spec = compile_to_jobspec(_config(image="python-slim"), image, CODE_URI, "demo")
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
        "version: 1\nname: fed\nimage: pytorch-cpu\nentrypoint: train.py\n"
        "mode: federated\nrounds: 2\nmin_participants: 2\n"
        'local_inputs: ["patients"]\n'
    )
    spec = compile_federated_round(
        config, PYTORCH, CODE_URI, "fed", round_index=1,
        weights_uri="artifact://jobs/j/round-000/weights.json",
    )
    params = _params(spec)
    assert params["local_inputs"] == ["patients"]
    assert set(params["inputs"]) == {"code", "weights"}
    assert params["unpack_inputs"] == ["code"]
    assert json.dumps(spec).count("patients") == 1


def test_federated_rounds_without_local_inputs_are_unchanged():
    config = parse_flashml_yaml(
        "version: 1\nname: fed\nimage: pytorch-cpu\nentrypoint: train.py\n"
        "mode: federated\nrounds: 2\nmin_participants: 2\n"
    )
    spec = compile_federated_round(
        config, PYTORCH, CODE_URI, "fed", round_index=0, weights_uri=None,
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
