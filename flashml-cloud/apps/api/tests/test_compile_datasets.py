"""Datasets in the compiled job spec.

Two properties matter more than the rest:

* the federated argv KEEPS `--shard` and `--num-shards`. The worker
  reports `chunks_done: [args.shard]`, and a contribution reporting none
  is averaged in with zero weight — so dropping the integers would zero
  every federated dataset job's credit while everything looked healthy.
* `split` is INFERRED from `mode`, because federated means disjoint slices
  and a sweep means every task needs the whole thing.

A third, quieter one is pinned at the bottom of the file: a federated
round cuts the manifest against ``total_chunks`` — the size of the whole
pass — and then hands each slot the chunk id it was given. Cutting against
the number of *slots* instead would look correct in every round where the
two happen to be equal, and would silently train a fraction of the data in
every round where they are not.
"""
from __future__ import annotations

import inspect

import pytest

from flashml_cloud_api.compile import (
    CompileError,
    compile_federated_round,
    compile_to_jobspec,
)
from flashml_cloud_api.datasets import Manifest, ManifestEntry
from flashml_cloud_api.flashml_yaml import parse_flashml_yaml
from flashml_cloud_api.images import resolve_image

CODE_URI = "artifact://uploads/deadbeef/code.tar.gz"
PYTORCH = resolve_image("pytorch-cpu")


def _manifest(name="imdb", n=4, sizes=None):
    return Manifest(
        name=name, source=f"hf://a/{name}", revision="c0ffee",
        entries=tuple(
            ManifestEntry(
                path=f"part-{i:03d}.parquet",
                url=f"https://huggingface.co/datasets/a/{name}/resolve/c0ffee/part-{i:03d}.parquet",
                size=100 if sizes is None else sizes[i],
                integrity={"kind": "sha256", "value": f"{i:064d}"},
            )
            for i in range(n)
        ),
    )


def _params(spec):
    return spec["spec"]["workload"]["parameters"]


def test_a_sweep_gets_a_replica_slice_per_task():
    config = parse_flashml_yaml(
        "version: 1\nname: s\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "sweep:\n  lr: [0.1, 0.2, 0.3]\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    spec = compile_to_jobspec(
        config, PYTORCH, CODE_URI, "s", manifests={"imdb": _manifest()}
    )
    slices = _params(spec)["dataset_slices"]
    assert len(slices) == 3, "one slice list per sweep task"
    for task_slice in slices:
        assert task_slice[0]["split"] == "replica"
        assert len(task_slice[0]["entries"]) == 4, "replica means the whole thing"


def test_a_single_task_job_still_gets_its_slice():
    """No sweep is one task, not zero — and that task needs the data too."""
    config = parse_flashml_yaml(
        "version: 1\nname: s\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    spec = compile_to_jobspec(
        config, PYTORCH, CODE_URI, "s", manifests={"imdb": _manifest()}
    )
    slices = _params(spec)["dataset_slices"]
    assert len(slices) == 1
    assert len(slices[0][0]["entries"]) == 4


def test_a_slice_entry_carries_everything_the_host_needs_to_fetch_it():
    """URL, byte count and integrity travel with the path.

    The host has no other source for any of them: it never sees the
    manifest, only the slice, and a slice missing the URL is a list of
    filenames.
    """
    config = parse_flashml_yaml(
        "version: 1\nname: s\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    spec = compile_to_jobspec(
        config, PYTORCH, CODE_URI, "s", manifests={"imdb": _manifest(n=1)}
    )
    entry = _params(spec)["dataset_slices"][0][0]["entries"][0]
    assert entry == {
        "path": "part-000.parquet",
        "url": (
            "https://huggingface.co/datasets/a/imdb/resolve/c0ffee/"
            "part-000.parquet"
        ),
        "size": 100,
        "integrity": {"kind": "sha256", "value": f"{0:064d}"},
    }


def test_a_federated_round_gets_disjoint_shards():
    config = parse_flashml_yaml(
        "version: 2\nname: f\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "mode: federated\nepochs: 1\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    spec = compile_federated_round(
        config, PYTORCH, CODE_URI, "f",
        round_index=0, weights_uri=None,
        slot_chunks=[0, 1], total_chunks=2,
        manifests={"imdb": _manifest()},
    )
    slices = _params(spec)["dataset_slices"]
    assert len(slices) == 2
    paths = [e["path"] for s in slices for e in s[0]["entries"]]
    assert sorted(paths) == sorted(set(paths)), "a file was handed to two tasks"
    assert len(paths) == 4, "the pass does not cover the dataset"
    assert all(s[0]["split"] == "shard" for s in slices)


def test_a_round_narrower_than_the_pass_cuts_against_the_pass():
    """The subtlety the plan calls out, and the reason it is a bug.

    One slot online, four chunks in the pass. The slot is training chunk 0,
    and chunk 0 is a QUARTER of the data — not all of it. Cutting against
    ``len(slot_chunks)`` instead would hand this task the whole manifest
    under the label "shard 0 of 4", and `--num-shards 4` in its argv would
    still say otherwise. Every later round would then retrain the same
    bytes while the driver believed the pass was being swept.
    """
    config = parse_flashml_yaml(
        "version: 2\nname: f\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "mode: federated\nepochs: 1\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    spec = compile_federated_round(
        config, PYTORCH, CODE_URI, "f",
        round_index=0, weights_uri=None,
        slot_chunks=[0], total_chunks=4,
        manifests={"imdb": _manifest()},
    )
    slices = _params(spec)["dataset_slices"]
    assert len(slices) == 1
    assert [e["path"] for e in slices[0][0]["entries"]] == ["part-000.parquet"]


def test_a_later_slot_gets_its_own_chunk_not_its_own_index():
    """Slot 0 of round 3 is not chunk 0.

    ``fedavg.slot_chunks_for`` rotates the round's slots through the pass,
    so the chunk ids a round is given are frequently not ``0..n``. The
    slice must follow the chunk id, because ``--shard`` does: a task told
    ``--shard 3`` reports ``chunks_done: [3]``, and if it was handed chunk
    1's files the driver credits a chunk that was never trained.
    """
    config = parse_flashml_yaml(
        "version: 2\nname: f\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "mode: federated\nepochs: 1\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    spec = compile_federated_round(
        config, PYTORCH, CODE_URI, "f",
        round_index=1, weights_uri=None,
        slot_chunks=[3, 0], total_chunks=4,
        manifests={"imdb": _manifest()},
    )
    slices = _params(spec)["dataset_slices"]
    assert [e["path"] for e in slices[0][0]["entries"]] == ["part-003.parquet"]
    assert [e["path"] for e in slices[1][0]["entries"]] == ["part-000.parquet"]
    # And the argv agrees with the slice it was cut for.
    assert _params(spec)["task_params"] == [{"shard": "3"}, {"shard": "0"}]


def test_the_shard_split_is_byte_weighted_not_file_counted():
    """One giant file and three small ones go to different slots.

    The mapper's own tests pin this; it is asserted again here because the
    compiler chooses what to hand it, and handing it file *positions*
    instead of sizes would pass every one of those tests.
    """
    config = parse_flashml_yaml(
        "version: 2\nname: f\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "mode: federated\nepochs: 1\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    spec = compile_federated_round(
        config, PYTORCH, CODE_URI, "f",
        round_index=0, weights_uri=None,
        slot_chunks=[0, 1], total_chunks=2,
        manifests={"imdb": _manifest(n=4, sizes=[1000, 1, 1, 1])},
    )
    slices = _params(spec)["dataset_slices"]
    assert [sum(e["size"] for e in s[0]["entries"]) for s in slices] == [1000, 3]


def test_the_federated_argv_still_carries_shard_and_num_shards():
    """Removing these silently zeroes chunks_done and credits nothing."""
    config = parse_flashml_yaml(
        "version: 2\nname: f\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "mode: federated\nepochs: 1\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    spec = compile_federated_round(
        config, PYTORCH, CODE_URI, "f",
        round_index=0, weights_uri=None,
        slot_chunks=[0], total_chunks=1,
        manifests={"imdb": _manifest()},
    )
    command = _params(spec)["command"]
    assert "--num-shards" in command
    assert "--shard" in command


def test_an_explicit_split_overrides_the_inference():
    config = parse_flashml_yaml(
        "version: 2\nname: f\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "mode: federated\nepochs: 1\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n    split: replica\n"
    )
    spec = compile_federated_round(
        config, PYTORCH, CODE_URI, "f",
        round_index=0, weights_uri=None,
        slot_chunks=[0, 1], total_chunks=2,
        manifests={"imdb": _manifest()},
    )
    slices = _params(spec)["dataset_slices"]
    assert all(len(s[0]["entries"]) == 4 for s in slices)
    assert all(s[0]["split"] == "replica" for s in slices)


def test_an_explicit_shard_split_overrides_the_inference_the_other_way():
    """A sweep that asks for disjoint slices gets them.

    The inference is a default, not a rule, and it has to be overridable in
    both directions or ``split:`` is decoration on every non-federated job.
    """
    config = parse_flashml_yaml(
        "version: 1\nname: s\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "sweep:\n  lr: [0.1, 0.2]\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n    split: shard\n"
    )
    spec = compile_to_jobspec(
        config, PYTORCH, CODE_URI, "s", manifests={"imdb": _manifest()}
    )
    slices = _params(spec)["dataset_slices"]
    assert [len(s[0]["entries"]) for s in slices] == [2, 2]
    assert all(s[0]["split"] == "shard" for s in slices)


def test_two_datasets_are_both_cut_into_every_task():
    config = parse_flashml_yaml(
        "version: 2\nname: f\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "mode: federated\nepochs: 1\n"
        "datasets:\n"
        "  - name: imdb\n    source: hf://a/imdb\n"
        "  - name: refs\n    source: hf://a/refs\n    split: replica\n"
    )
    spec = compile_federated_round(
        config, PYTORCH, CODE_URI, "f",
        round_index=0, weights_uri=None,
        slot_chunks=[0, 1], total_chunks=2,
        manifests={"imdb": _manifest("imdb"), "refs": _manifest("refs", n=2)},
    )
    for task_slice in _params(spec)["dataset_slices"]:
        assert [d["name"] for d in task_slice] == ["imdb", "refs"]
        assert len(task_slice[0]["entries"]) == 2   # sharded half
        assert len(task_slice[1]["entries"]) == 2   # replicated whole


def test_absent_stays_absent():
    config = parse_flashml_yaml(
        "version: 1\nname: s\nimage: pytorch-cpu\nentrypoint: t.py\n"
    )
    spec = compile_to_jobspec(config, PYTORCH, CODE_URI, "s", manifests={})
    assert "dataset_slices" not in _params(spec)


def test_absent_stays_absent_with_no_manifests_argument_at_all():
    """Every existing caller passes nothing, and must keep compiling."""
    config = parse_flashml_yaml(
        "version: 1\nname: s\nimage: pytorch-cpu\nentrypoint: t.py\n"
    )
    assert "dataset_slices" not in _params(
        compile_to_jobspec(config, PYTORCH, CODE_URI, "s")
    )
    federated = parse_flashml_yaml(
        "version: 2\nname: f\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "mode: federated\nepochs: 1\n"
    )
    assert "dataset_slices" not in _params(
        compile_federated_round(
            federated, PYTORCH, CODE_URI, "f",
            round_index=0, weights_uri=None,
            slot_chunks=[0], total_chunks=1,
        )
    )


def test_declared_but_unresolved_is_refused_not_silently_dataless():
    """The fail-open trap. A job that declares data and resolves none must
    not compile: its tasks would fetch nothing, find an empty
    /work/data/, and either die on a missing path or quietly train on
    whatever the entrypoint falls back to."""
    config = parse_flashml_yaml(
        "version: 1\nname: s\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    with pytest.raises(CompileError, match="no data"):
        compile_to_jobspec(config, PYTORCH, CODE_URI, "s", manifests={})


def test_declared_but_unresolved_is_refused_in_a_federated_round_too():
    """The same guard on the other compiler.

    A federated round is where this fails worst: N rounds of N machines
    each training nothing, every one of them reporting a contribution the
    driver averages in.
    """
    config = parse_flashml_yaml(
        "version: 2\nname: f\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "mode: federated\nepochs: 1\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    with pytest.raises(CompileError, match="no data"):
        compile_federated_round(
            config, PYTORCH, CODE_URI, "f",
            round_index=0, weights_uri=None,
            slot_chunks=[0], total_chunks=1,
            manifests=None,
        )


def test_a_dataset_resolved_under_a_different_name_is_refused():
    config = parse_flashml_yaml(
        "version: 1\nname: s\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    with pytest.raises(CompileError, match="imdb"):
        compile_to_jobspec(
            config, PYTORCH, CODE_URI, "s", manifests={"other": _manifest("other")}
        )


def test_one_resolved_dataset_out_of_two_is_still_refused():
    """The partial case the `not manifests` guard cannot see.

    A truthy manifests dict passes the first check, so the per-dataset
    lookup is what stands between a half-resolved job and a fleet that
    trains on half the data it was told about.
    """
    config = parse_flashml_yaml(
        "version: 1\nname: s\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "datasets:\n"
        "  - name: imdb\n    source: hf://a/imdb\n"
        "  - name: refs\n    source: hf://a/refs\n"
    )
    with pytest.raises(CompileError, match="refs"):
        compile_to_jobspec(
            config, PYTORCH, CODE_URI, "s", manifests={"imdb": _manifest()}
        )


def test_the_slices_survive_the_jobspec_round_trip():
    """``compile_to_jobspec`` returns ``JobSpec.model_validate(...)`` dumped,
    and pydantic's ``extra="ignore"`` drops what the model does not declare.

    ``workload.parameters`` is a free-form ``dict[str, Any]``, so the slices
    live in the one part of the spec that cannot be silently dropped by a
    pin that has not caught up. This test is what would notice if that ever
    stopped being true — the way ``gpuPerTask`` did.
    """
    config = parse_flashml_yaml(
        "version: 1\nname: s\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    spec = compile_to_jobspec(
        config, PYTORCH, CODE_URI, "s", manifests={"imdb": _manifest(n=2)}
    )
    assert [e["path"] for e in _params(spec)["dataset_slices"][0][0]["entries"]] == [
        "part-000.parquet", "part-001.parquet",
    ]


# ---------------------------------------------------------------------------
# the hop into the task payload
#
# ``CommandRecipe.expand`` builds each task payload from a fixed key list and
# forwards nothing it does not recognise, so ``dataset_slices`` reaches
# ``task.payload["datasets"]`` only once the companion runtime plan's Task 2
# lands and the pin moves (the same shape as the ``gpuPerTask`` gap already
# recorded in ``test_compile.py``, and the same shape the recipe's own
# comments warn about for ``local_inputs``).
#
# The end-to-end assertion is written anyway, against the REAL recipe, and
# gated on the pin — so the day the pin moves the coverage turns itself on
# instead of being quietly absent. It is the only test that proves the two
# repos agree about the wire.
# ---------------------------------------------------------------------------


def _pin_forwards_dataset_slices() -> bool:
    """Does the PINNED ``CommandRecipe`` know about ``dataset_slices``?

    A source probe rather than a behavioural one on purpose: asking the
    recipe to expand a spec and then skipping when the key is missing would
    skip for a *broken* forward exactly as readily as for an absent one,
    which is the failure the test below exists to catch.
    """
    from flashruntime.recipes.command import CommandRecipe

    return "dataset_slices" in inspect.getsource(CommandRecipe.expand)


def test_the_recipe_forwards_the_slice_to_the_task_payload():
    """End to end through the REAL upstream recipe — what the compiler
    emits must be what a node is actually told."""
    from flashruntime.protocol.v1alpha1 import JobSpec
    from flashruntime.recipes.command import CommandRecipe

    if not _pin_forwards_dataset_slices():
        pytest.skip(
            "the pinned flashruntime's CommandRecipe does not forward "
            "dataset_slices yet, so the slice stops at the JobSpec. "
            "Unblocked by the companion runtime plan "
            "(flashml/flashruntime/docs/superpowers/plans/"
            "2026-08-11-declared-datasets-runtime.md, Task 2) plus its "
            "release and a four-site pin bump."
        )
    config = parse_flashml_yaml(
        "version: 1\nname: s\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "sweep:\n  lr: [0.1, 0.2]\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    spec = compile_to_jobspec(
        config, PYTORCH, CODE_URI, "s", manifests={"imdb": _manifest()}
    )
    tasks = CommandRecipe().expand("job-1", JobSpec.model_validate(spec))
    assert tasks[0].payload["datasets"][0]["name"] == "imdb"
    assert len(tasks[0].payload["datasets"][0]["entries"]) == 4


def test_the_pin_gap_is_a_dropped_forward_and_not_a_rejected_spec():
    """Documents WHY the test above skips, so the gap cannot be misread.

    The slices do not make the compiled spec invalid — ``validate_params``
    ignores parameters it does not know — so a submitted job expands, leases
    and runs today. It simply runs without the data, which is precisely the
    "does not fail closed" shape the recipe's own comments keep warning
    about. If this test fails, the pin has moved and the one above has
    taken over.
    """
    from flashruntime.protocol.v1alpha1 import JobSpec
    from flashruntime.recipes.command import CommandRecipe

    if _pin_forwards_dataset_slices():
        pytest.skip("pin now forwards dataset_slices; the test above asserts it")
    config = parse_flashml_yaml(
        "version: 1\nname: s\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    spec = compile_to_jobspec(
        config, PYTORCH, CODE_URI, "s", manifests={"imdb": _manifest()}
    )
    tasks = CommandRecipe().expand("job-1", JobSpec.model_validate(spec))
    assert "datasets" not in tasks[0].payload


# ---------------------------------------------------------------------------
# the driver hop — invisible to every test above
# ---------------------------------------------------------------------------


def test_the_federated_driver_carries_manifests_into_every_round():
    """The route answers 201 and then the DRIVER builds each round.

    `FederatedRun` had no `manifests` field, so `build_round_for` called the
    compiler without one — and since the compiler correctly refuses
    declared-but-unresolved rather than emitting a dataless job, EVERY round
    of a `mode: federated` job that declared `datasets:` raised CompileError
    inside the driver thread. The submitter saw a 201 and a job that died in
    the background with nothing in the response explaining why.

    Nothing above catches this: the route tests stop at the response, and the
    compile tests pass `manifests=` directly.
    """
    import json

    from flashml_cloud_api.fedavg import FederatedRun, build_round_for

    config = parse_flashml_yaml(
        "version: 2\nname: imdb-fed\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "mode: federated\nepochs: 3\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    run = FederatedRun(
        job_id="j1", job_name="imdb-fed", config=config, image=PYTORCH,
        code_artifact_uri=CODE_URI, pool=None,
        manifests={"imdb": _manifest()},
    )
    build = build_round_for(run)
    for round_index, weights in ((0, None), (1, "artifact://w"), (2, "artifact://w")):
        plan = build(round_index, weights)
        assert "dataset_slices" in json.dumps(plan), (
            f"round {round_index} lost the manifests"
        )


def test_a_federated_run_without_datasets_still_builds():
    """Absent stays absent through the driver too — every job deployed today
    declares no datasets and must be byte-identical."""
    import json

    from flashml_cloud_api.fedavg import FederatedRun, build_round_for

    config = parse_flashml_yaml(
        "version: 2\nname: plain\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "mode: federated\nepochs: 1\n"
    )
    run = FederatedRun(
        job_id="j2", job_name="plain", config=config, image=PYTORCH,
        code_artifact_uri=CODE_URI, pool=None,
    )
    assert "dataset_slices" not in json.dumps(build_round_for(run)(0, None))
