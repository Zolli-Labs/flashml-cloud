"""The fleet decides the split: slot counts and chunk layout.

Two derivations live in ``elastic.py`` and both are copies of arithmetic the
runtime performs inside ``run_fedavg`` and does not export — the round count
and, per round, the chunk offset every slot's rotation starts from. A copy
that silently disagrees with its original is the failure mode this file
exists to make loud, so the tests here assert against
``flashml_workloads.chunks`` itself rather than against a second hand-written
expectation.

The consequence of drift is not a crash: the driver credits a contribution
only for chunks it can prove it handed that slot, so a layout that disagrees
by one chunk trims every allotment, and a layout that disagrees badly credits
every machine zero and the round dies having burned real volunteer
electricity. See ``test_elastic_driver.py`` for the end-to-end pin.

Spec: ``docs/superpowers/specs/2026-08-09-elastic-work-distribution-design.md``
§4.
"""
from __future__ import annotations

import pytest
from flashml_workloads.chunks import slot_start

from flashml_cloud_api.compile import compile_federated_round, federated_task_ids
from flashml_cloud_api.elastic import (
    MAX_SLOTS,
    FleetShape,
    fleet_shape,
    round_chunk_offset,
)
from flashml_cloud_api.fedavg import slot_chunks_for
from flashml_cloud_api.flashml_yaml import parse_flashml_yaml
from flashml_cloud_api.images import resolve_image

FEDERATED_V2 = """
version: 2
name: acme-fed
image: python-slim
entrypoint: train.py
mode: federated
epochs: 2
"""


def _config(**fields: object):
    text = FEDERATED_V2 + "".join(f"{k}: {v}\n" for k, v in fields.items())
    return parse_flashml_yaml(text)


# ---------------------------------------------------------------------------
# how many slots a round opens
# ---------------------------------------------------------------------------


def test_every_online_machine_gets_a_slot():
    assert fleet_shape(6) == FleetShape(slots=6, total_chunks=6, expected_machines=6)


def test_an_empty_crew_still_opens_one_slot():
    """A job submitted while every machine is asleep must still be a job. One
    slot is the smallest round that can exist, and a machine waking up later
    claims it."""
    assert fleet_shape(0) == FleetShape(slots=1, total_chunks=1, expected_machines=1)


def test_the_slot_count_is_capped():
    """A Crew of thousands must not dispatch thousands of tasks per round:
    every unclaimed slot is a chunk of the round's coverage target that
    nobody is working on, so the round waits for its backstop."""
    shape = fleet_shape(MAX_SLOTS + 500)
    assert shape.slots == MAX_SLOTS
    assert shape.total_chunks == MAX_SLOTS


def test_a_negative_count_is_a_programming_error_not_a_clamp():
    with pytest.raises(ValueError, match="machines_online"):
        fleet_shape(-1)


# ---------------------------------------------------------------------------
# where each round's rotation starts
# ---------------------------------------------------------------------------


def test_one_combine_per_pass_leaves_the_layout_where_it_is_today():
    """``sync_every: 1.0`` is the default and must reproduce today's rounds
    exactly: a round covers a whole pass, so the next round starts back at
    chunk 0 and slot i trains stride i, round after round."""
    assert [round_chunk_offset(r, total_chunks=6, sync_every=1.0)
            for r in range(3)] == [0, 0, 0]


def test_combining_twice_a_pass_sweeps_the_offset_forward():
    """Half a pass per round means round 1 must start where round 0's target
    ended — otherwise every round retrains the same first half and the second
    half is never trained at all."""
    assert [round_chunk_offset(r, total_chunks=6, sync_every=0.5)
            for r in range(4)] == [0, 3, 0, 3]


# ---------------------------------------------------------------------------
# the compiled round agrees with the runtime's own arithmetic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("slots, sync_every, round_index",
                         [(6, 1.0, 0), (6, 0.5, 1), (4, 0.25, 3), (1, 1.0, 2)])
def test_a_compiled_slot_starts_where_the_driver_will_verify_it(
    slots, sync_every, round_index
):
    """The one assertion that matters in this file.

    ``slot_start`` is the runtime's function, imported here rather than
    reimplemented: the driver checks every reported chunk against it, so a
    compiled task told to train anything else is a contribution credited
    zero.
    """
    # The config keeps today's only accepted `sync_every`; the value under
    # test is handed to the layout directly. These functions have to be right
    # for the values the next slice turns on, not only for the one that
    # reaches them today — and a round's layout is not a config concern.
    config = _config()
    shape = fleet_shape(slots)
    offset = round_chunk_offset(round_index, total_chunks=shape.total_chunks,
                                sync_every=sync_every)

    body = compile_federated_round(
        config,
        resolve_image(config.image),
        "artifact://repo/code.tar.gz",
        "acme-fed",
        round_index=round_index,
        weights_uri=None,
        slot_chunks=slot_chunks_for(shape, round_index, sync_every),
        total_chunks=shape.total_chunks,
    )

    params = body["spec"]["workload"]["parameters"]
    handed_out = [int(p["shard"]) for p in params["task_params"]]
    assert handed_out == [
        slot_start(i, shape.total_chunks, shape.slots, offset)
        for i in range(shape.slots)
    ]


def test_the_round_dispatches_one_task_per_slot():
    """``run_fedavg`` refuses a builder whose task count disagrees with the
    slot count its chunk budget was sized from."""
    config = _config()
    shape = fleet_shape(5)
    body = compile_federated_round(
        config,
        resolve_image(config.image),
        "artifact://repo/code.tar.gz",
        "acme-fed",
        round_index=0,
        weights_uri=None,
        slot_chunks=slot_chunks_for(shape, 0, 1.0),
        total_chunks=shape.total_chunks,
    )
    params = body["spec"]["workload"]["parameters"]
    assert len(params["task_params"]) == shape.slots
    assert len(federated_task_ids(shape.slots)) == shape.slots


def test_the_task_argv_tells_the_entrypoint_the_whole_pass():
    """``--num-shards`` is the chunk count of a full pass, not the machine
    count: the user's ``shard_of`` strides the data by it, so it has to be
    the number the chunk ids were minted from."""
    config = _config()
    shape = fleet_shape(3)
    body = compile_federated_round(
        config,
        resolve_image(config.image),
        "artifact://repo/code.tar.gz",
        "acme-fed",
        round_index=0,
        weights_uri=None,
        slot_chunks=slot_chunks_for(shape, 0, 1.0),
        total_chunks=shape.total_chunks,
    )
    command = body["spec"]["workload"]["parameters"]["command"]
    assert "--num-shards" in command
    assert command[command.index("--num-shards") + 1] == str(shape.total_chunks)
