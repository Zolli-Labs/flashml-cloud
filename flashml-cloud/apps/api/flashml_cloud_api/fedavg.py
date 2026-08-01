"""Running federated averaging as a hosted background task.

This is the module that closes the gap between "federated averaging works"
and "the deployed product can do it": ``fedavg_driver.run_fedavg`` has been
proven across two real ``flashnode`` agents for a while, but nothing in the
API ever called it, so a submitted repo could only ever be a sweep.

**Where the driver runs, and why here.** Inside this process, as a
background thread keyed to the job — never on a volunteer machine (design
spec §5.4.5). Round aggregation decides how much weight each machine's
contribution gets in the shared model, and until result verification lands
a node's reported numbers are simply believed; moving the reduce onto
untrusted hardware would make one volunteer able to rewrite everybody's
model. The driver holds the operator credential for the same reason: it
writes the round's aggregated weights, an artifact that belongs to no task,
so no lease could ever scope it.

**The repo boundary — read this before adding an import.** The workspace
dependency rule is that flashml-cloud imports ``flashruntime.protocol`` and
nothing else. This module imports ``flashml_workloads.fedavg_driver``, which
is a *second* cross-repo surface, and it is deliberately the ONLY place in
this repo that does (``tests/test_import_boundary.py`` enforces that). The
design spec sanctions the coupling in §5.4.5 — "``fedavg_driver`` itself
belongs in flashruntime … flashml-cloud only *invokes* it as a hosted
background task" — because federated averaging is a runtime capability that
a self-hosted user must get too, not a commercial one. What has NOT happened
is the rule being updated to say so; that is a documentation change for a
human to make, not one an agent should quietly perform, so until then this
docstring is the record. Nothing here may reach back the other way: the
driver knows nothing about Supabase, this schema, or accounts.

**What one round is.** ``compile.compile_federated_round`` turns the user's
repo into an ordinary sandboxed ``command`` job — their entrypoint, their
image, their code staged as an artifact — with the round's aggregated
weights added as an input and one task per shard. The driver submits it,
waits for quorum, averages the committed deltas, writes the next round's
weights, and repeats. The user's entrypoint is the round worker, which is
why ``mode: federated`` is a contract they opt into rather than something
the platform can infer (``preflight``'s ``federated-contract`` rule).
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import psycopg

# The one deliberate cross-repo import beyond `flashruntime.protocol`. See
# the module docstring; do not add a second one anywhere in this repo
# without moving the rule first.
from flashml_workloads.fedavg_driver import (
    Coordinator,
    HttpCoordinator,
    RoundPlan,
    RoundResult,
    resume_state,
    run_fedavg,
)

from flashml_cloud_api import db as dbmod
from flashml_cloud_api.compile import compile_federated_round, federated_task_ids
from flashml_cloud_api.flashml_yaml import FlashmlConfig
from flashml_cloud_api.images import CuratedImage
from flashml_cloud_api.settings import Settings

log = logging.getLogger("flashml-cloud-api")

__all__ = [
    "FederatedRun",
    "JOB_ID_PREFIX",
    "build_round_for",
    "default_coordinator",
    "is_federated_job_id",
    "new_federated_job_id",
    "run_federated_job",
    "start_federated_job",
]

#: A federated run's parent job id is minted here, not by the coordinator.
#: It has to be: the coordinator has no concept of a run that spans jobs —
#: it sees N unrelated round jobs — so the id a user owns, lists and polls
#: is this one, and each round's coordinator job id is recorded against it
#: in ``job_rounds``. The prefix is what lets a route tell the two apart
#: without a database round trip.
JOB_ID_PREFIX = "fed-"

#: How long one round may take before it is declared a quorum failure, as a
#: multiple of the per-task time budget. A round is not just training: the
#: tasks have to be queued, claimed by however many volunteers are awake,
#: run, uploaded and committed, and a task that dies gets retried inside the
#: same round. Four times the task budget is slack for all of that without
#: letting a permanently-empty pool hold a driver thread forever.
ROUND_TIMEOUT_MULTIPLIER = 4

#: Task budget assumed when the config sets no ``timeout_seconds``. Matches
#: ``compile.DEFAULT_FEDERATED_LEASE_SECONDS``.
DEFAULT_TASK_SECONDS = 600.0

#: Poll cadence against the coordinator, matched to its own 2-second lease
#: sweeper: polling faster cannot observe anything new.
POLL_SECONDS = 2.0

STATUS_RUNNING = "RUNNING"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"


def new_federated_job_id() -> str:
    return f"{JOB_ID_PREFIX}{uuid.uuid4().hex[:12]}"


def is_federated_job_id(job_id: Any) -> bool:
    return isinstance(job_id, str) and job_id.startswith(JOB_ID_PREFIX)


@dataclass(frozen=True)
class FederatedRun:
    """Everything a driver needs to rebuild any round of one federated run.

    Frozen and self-contained on purpose: it is handed to a background
    thread that outlives the request, so it must not hold a database
    connection, a request object, or anything else with a lifetime.
    """

    job_id: str
    job_name: str
    config: FlashmlConfig
    image: CuratedImage
    code_artifact_uri: str


def build_round_for(run: FederatedRun) -> Callable[[int, str | None], RoundPlan]:
    """The driver's ``build_round``: round index + weights URI → a job body.

    Rebuilt from ``run`` every round rather than compiled once and mutated,
    so a round can never inherit the previous round's weights input by
    accident — the single most damaging silent bug available here, since a
    run that broadcast round 3's weights forever would still converge to
    *something* and still report falling loss.
    """

    def build(round_index: int, weights_uri: str | None) -> RoundPlan:
        body = compile_federated_round(
            run.config,
            run.image,
            run.code_artifact_uri,
            run.job_name,
            round_index=round_index,
            weights_uri=weights_uri,
        )
        return {"body": body,
                "task_ids": federated_task_ids(run.config.shards or 0)}

    return build


def default_coordinator(settings: Settings) -> Coordinator:
    """The driver's coordinator client, carrying the operator credential.

    The driver talks to the coordinator *directly*, not through this API's
    own agent routes: those forward on behalf of a machine identity, and the
    driver is not a machine — it is the trusted control loop, and the
    weights it writes belong to no lease.
    """
    return HttpCoordinator(
        settings.coordinator_url,
        headers={"Authorization": f"Bearer {settings.coordinator_operator_token}"},
    )


def _round_timeout_seconds(config: FlashmlConfig) -> float:
    task_seconds = float(config.timeout_seconds or DEFAULT_TASK_SECONDS)
    return task_seconds * ROUND_TIMEOUT_MULTIPLIER


def _contributors(coord: Coordinator, coordinator_job_id: str) -> list[str]:
    """Node ids whose commit the coordinator ACCEPTED for this round.

    Filtered on ``COMPLETED``, never on "has a node_id": a task that was
    leased, failed and got retried elsewhere still reports the last node
    that held it, and crediting that machine would count attempted work as
    accepted work — the one thing the workspace rules say never to blur.

    Never fatal. This is provenance for the UI and for later credit
    accounting; failing the round because the task list was momentarily
    unavailable would throw away an aggregation that already succeeded.
    """
    try:
        tasks = coord.tasks(coordinator_job_id)
    except Exception:  # noqa: BLE001 - provenance is not worth a failed round
        log.warning(json.dumps(
            {"text": "could not read round contributors", "job": coordinator_job_id}
        ))
        return []
    seen: list[str] = []
    for task in tasks:
        if not isinstance(task, dict) or task.get("state") != "COMPLETED":
            continue
        node_id = task.get("node_id")
        if isinstance(node_id, str) and node_id and node_id not in seen:
            seen.append(node_id)
    return seen


def run_federated_job(
    run: FederatedRun,
    *,
    settings: Settings,
    connect: Callable[[], psycopg.Connection],
    coordinator_factory: Callable[[Settings], Coordinator] | None = None,
) -> None:
    """Drive one federated run to completion, recording every round.

    Blocking, and called on a background thread — a run is hours long.

    **Restart behaviour.** The run always begins by reading its own
    persisted round history and asking ``resume_state`` where to pick up, so
    a driver started against a job that already has rounds continues from
    the last one whose weights were committed instead of retraining from
    scratch. That makes the *resume* path real and tested. What is
    deliberately NOT wired is anything that starts a driver again after this
    process dies: nothing scans for jobs stuck in ``RUNNING`` at startup,
    because with more than one API replica that scan is a race — two
    replicas would both resume the same run and submit two round jobs per
    round, doubling every machine's work and averaging each round twice.
    Electing one driver owner is real work (a lease on the job row, or an
    external scheduler) and is not in this slice. Until it exists, an API
    restart leaves the run stopped with its history intact and its status
    still ``RUNNING``, and resuming it is an explicit operator action.

    Any failure marks the job FAILED. That is the whole reason this wrapper
    exists rather than handing ``run_fedavg`` straight to a thread: an
    exception on a daemon thread is printed and forgotten, and the job would
    sit at ``RUNNING`` for ever with no round ever appearing.
    """
    coord = (coordinator_factory or default_coordinator)(settings)

    def on_round(result: RoundResult) -> None:
        # A fresh connection per round rather than one held for the run's
        # whole duration: a driver thread can sit idle for an hour between
        # rounds, which is long enough for any pooler or firewall to drop a
        # connection underneath it.
        db = connect()
        try:
            dbmod.insert_job_round(
                db,
                job_id=run.job_id,
                round_index=int(result["round"]),
                participants=int(result["participants"]),
                mean_loss=float(result["mean_loss"]),
                contributors=_contributors(coord, str(result["job_id"])),
                coordinator_job_id=str(result["job_id"]),
            )
        finally:
            db.close()

    try:
        db = connect()
        try:
            prior = dbmod.list_round_job_ids(db, run.job_id)
            dbmod.set_job_status(db, run.job_id, STATUS_RUNNING, finished=False)
        finally:
            db.close()

        start_round, weights, weights_uri = resume_state(coord, prior)
        if start_round >= (run.config.rounds or 0):
            # Every requested round is already recorded. Finishing rather
            # than running a negative-length loop keeps a re-triggered
            # driver idempotent.
            _finish(connect, run.job_id, STATUS_SUCCEEDED)
            return

        run_fedavg(
            coord,
            rounds=int(run.config.rounds or 0),
            num_shards=int(run.config.shards or 0),
            min_participants=int(run.config.min_participants or 0),
            # Inputs to the *default* round body, which `build_round`
            # replaces wholesale; passed empty so nothing here looks like it
            # is configuring the user's tasks.
            worker_params={},
            initial_weights=weights,
            start_round=start_round,
            weights_uri=weights_uri,
            prior_job_ids=prior,
            round_timeout_s=_round_timeout_seconds(run.config),
            poll_seconds=POLL_SECONDS,
            on_round=on_round,
            build_round=build_round_for(run),
        )
    except BaseException as exc:  # noqa: BLE001
        # BaseException, not Exception: a driver thread killed by anything
        # at all must not leave the job reading RUNNING for ever. The status
        # write is best-effort and the original failure is re-raised.
        log.error(json.dumps({
            "text": "federated driver failed",
            "job_id": run.job_id,
            "error": type(exc).__name__,
        }))
        _finish(connect, run.job_id, STATUS_FAILED)
        raise
    else:
        _finish(connect, run.job_id, STATUS_SUCCEEDED)


def _finish(
    connect: Callable[[], psycopg.Connection], job_id: str, status: str
) -> None:
    try:
        db = connect()
        try:
            dbmod.set_job_status(db, job_id, status, finished=True)
        finally:
            db.close()
    except Exception:  # noqa: BLE001 - already on the failure path
        log.error(json.dumps(
            {"text": "could not record final job status",
             "job_id": job_id, "status": status}
        ))


def start_federated_job(
    run: FederatedRun,
    *,
    settings: Settings,
    connect: Callable[[], psycopg.Connection],
    coordinator_factory: Callable[[Settings], Coordinator] | None = None,
) -> threading.Thread:
    """Start ``run_federated_job`` on a background thread and return it.

    A plain daemon thread, not FastAPI's ``BackgroundTasks`` and not the
    Starlette threadpool: both are sized for work that finishes within a
    request or two, and a run that occupies one of their workers for hours
    would starve every other request that needs to block. Daemon so a
    shutdown is not held open by a run that would be abandoned anyway — see
    ``run_federated_job`` on what that costs.
    """
    def target() -> None:
        try:
            run_federated_job(
                run,
                settings=settings,
                connect=connect,
                coordinator_factory=coordinator_factory,
            )
        except BaseException:  # noqa: BLE001
            # Already logged, and the job row is already FAILED. Swallowed
            # only so the default thread excepthook does not print a second,
            # unstructured traceback into a JSON-per-line log stream.
            pass

    thread = threading.Thread(
        target=target,
        name=f"fedavg-{run.job_id}",
        daemon=True,
    )
    thread.start()
    return thread
