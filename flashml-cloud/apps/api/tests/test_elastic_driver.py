"""The real ``run_fedavg``, driven by this API, crediting real chunks.

``test_elastic_layout.py`` checks the cloud's arithmetic against the
runtime's ``slot_start``. This file checks the thing that actually matters:
that a round this API compiles is a round the driver *credits*. The stub
coordinator here reports, for each task, exactly the chunk the compiled argv
told that task to train — nothing invented — so if the cloud's chunk offset
and the driver's disagree by even one chunk, every contribution falls outside
its verified allotment and the round reduces nothing.

That is why this suite pins ``derived_round_count`` and
``round_chunk_offset``, which are copies of arithmetic ``run_fedavg`` keeps
to itself: the copies cannot drift without one of these tests going red.

No live coordinator, for the reason ``test_federated.py`` states: a test
needing one is a test that skips on a laptop.
"""
from __future__ import annotations

import pytest

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import fedavg as fedavgmod
from flashml_cloud_api.elastic import fleet_shape
from flashml_cloud_api.flashml_yaml import parse_flashml_yaml
from flashml_cloud_api.images import resolve_image

from test_federated import (  # reuse the proven driver harness
    _connect_factory,
    _new_user,
    _seed_job,
    db,  # noqa: F401 - fixture
    settings,  # noqa: F401 - fixture
    transport,  # noqa: F401 - fixture
)

#: Six machines online, so a pass is six chunks and a slot's allotment is
#: small enough that a wrong offset lands outside it. Two slots would not
#: catch drift: their allotments would overlap the whole pass.
MACHINES = 6


def _config(epochs: int = 2, sync_every: float | None = None):
    text = f"""
version: 2
name: acme-fed
image: python-slim
entrypoint: train.py
mode: federated
epochs: {epochs}
"""
    if sync_every is not None:
        text += f"sync_every: {sync_every}\n"
    return parse_flashml_yaml(text)


class ChunkEchoingCoordinator:
    """A coordinator whose machines report the chunks they were handed.

    The whole point: ``chunks_done`` is read out of the compiled body's
    ``task_params`` rather than assumed, so this stub is honest about what the
    round asked for. A machine cannot report a chunk it was never given, which
    is exactly the property ``run_fedavg``'s allotment check enforces against
    real volunteers.
    """

    def __init__(self) -> None:
        self.submitted: list[dict] = []
        #: ``{job_id: {task_id: chunk_id}}`` — what each round handed out.
        self.handed_out: dict[str, dict[str, int]] = {}
        self.uploaded: dict[str, object] = {}

    def submit(self, body):
        job_id = f"cjob-{len(self.submitted):03d}"
        self.submitted.append(body)
        params = body["spec"]["workload"]["parameters"]
        self.handed_out[job_id] = {
            f"task-{i:03d}": int(p["shard"])
            for i, p in enumerate(params["task_params"])
        }
        return {"job_id": job_id}

    def job_state(self, job_id):
        return "RUNNING"

    def artifacts(self, job_id):
        return [
            {"key": f"jobs/{job_id}/{task_id}/{name}"}
            for task_id in self.handed_out[job_id]
            for name in ("metrics.json", "delta.json")
        ]

    def tasks(self, job_id):
        return [
            {"task_id": task_id, "state": "COMPLETED", "node_id": f"node-{i}"}
            for i, task_id in enumerate(self.handed_out[job_id])
        ]

    def get_artifact(self, key):
        if key in self.uploaded:
            return self.uploaded[key]
        _, job_id, task_id, name = key.split("/")
        if name == "metrics.json":
            return {
                "samples": 100,
                "loss": 0.25,
                "delta_file": "delta.json",
                # The one line this class exists for.
                "chunks_done": [self.handed_out[job_id][task_id]],
            }
        if name == "delta.json":
            return {"w": {"shape": [1], "data": [0.5]}}
        from flashml_workloads.fedavg_driver import ArtifactNotFound
        raise ArtifactNotFound(key)

    def put_artifact(self, key, value):
        self.uploaded[key] = value


def _drive(job_id, settings, postgres_dsn, coordinator, config):
    run = fedavgmod.FederatedRun(
        job_id=job_id,
        job_name="acme-fed",
        config=config,
        image=resolve_image("python-slim"),
        code_artifact_uri="artifact://uploads/deadbeef/code.tar.gz",
        fleet=fleet_shape(MACHINES),
    )
    fedavgmod.run_federated_job(
        run,
        settings=settings,
        connect=_connect_factory(postgres_dsn),
        coordinator_factory=lambda _s: coordinator,
    )


def test_every_machine_is_credited_for_the_chunk_it_was_handed(
    db, settings, postgres_dsn
):
    """Six slots, six chunks, six contributors per round — the round closes on
    coverage, and every machine's work counts."""
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    coordinator = ChunkEchoingCoordinator()

    _drive(job_id, settings, postgres_dsn, coordinator, _config(epochs=2))

    rows = dbmod.list_job_rounds_for_owner(db, job_id, owner)
    assert [r["round"] for r in rows] == [0, 1]
    assert [r["participants"] for r in rows] == [MACHINES, MACHINES]


def test_the_round_count_the_api_derives_is_the_one_the_driver_runs(
    db, settings, postgres_dsn
):
    """``derived_round_count`` is a copy of the driver's own formula. If the
    two ever disagree, the API's resume check either finishes a run with
    training left to do or loops past the end of one."""
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    coordinator = ChunkEchoingCoordinator()
    config = _config(epochs=3)

    _drive(job_id, settings, postgres_dsn, coordinator, config)

    assert config.round_count == 3
    assert len(coordinator.submitted) == config.round_count


def test_one_chunk_per_slot_covers_the_whole_pass_every_round(
    db, settings, postgres_dsn
):
    """What this slice actually delivers, stated so the next one can change it.

    A pass is cut into as many chunks as there are machines, so every round
    dispatches the whole pass and closes when all of it is covered. The
    rotation between rounds moves which slot trains which chunk; it does not
    (yet) move which chunks a round covers, because that needs a round to
    cover only part of a pass — see ``_validate_sync_every``.
    """
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    coordinator = ChunkEchoingCoordinator()

    _drive(job_id, settings, postgres_dsn, coordinator, _config(epochs=2))

    per_round = [sorted(chunks.values())
                 for chunks in coordinator.handed_out.values()]
    assert per_round == [list(range(MACHINES))] * 2


def test_a_default_config_runs_one_round_per_epoch(db, settings, postgres_dsn):
    """The default has to be the behaviour every shipped federated job
    already had: one combine per pass, so rounds == epochs."""
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    coordinator = ChunkEchoingCoordinator()

    _drive(job_id, settings, postgres_dsn, coordinator, _config(epochs=3))

    assert len(coordinator.submitted) == 3
