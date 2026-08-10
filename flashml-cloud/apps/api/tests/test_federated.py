"""``mode: federated`` end to end inside the API: config → preflight →
compiled round → the driver running as a background task → persisted rounds
→ ``GET /rounds``.

The point of this file is the thing the deployed product could not do
before: `run_fedavg` was proven across two real agents but nothing in the
API ever called it, so a submitted repo could only ever be a sweep. Every
test here is about the *invocation* path, not the averaging arithmetic —
that is pinned upstream in flashruntime's own suite.

**No live coordinator.** The driver talks to a `StubCoordinator` that
records what was submitted and answers with a fixed round result. That is
deliberate: a test that needed a coordinator would be a test that skips on
a laptop, and a skipping test is not a regression guard.

The database is the real, freshly-migrated ephemeral Postgres from
``conftest.py`` — including ``0002_job_rounds.sql``, which is how that
migration gets exercised at all.
"""
from __future__ import annotations

import threading
import uuid

import pytest

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import fedavg as fedavgmod
from flashml_cloud_api.compile import (
    CompileError,
    compile_federated_round,
    federated_task_ids,
)
from flashml_cloud_api.elastic import fleet_shape
from flashml_cloud_api.flashml_yaml import ConfigError, parse_flashml_yaml
from flashml_cloud_api.images import resolve_image
from flashml_cloud_api.preflight import preflight

from test_jobs_from_repo import (  # reuse the proven from-repo harness
    CLEAN_TRAIN_PY,
    RecordingFetch,
    _job_rows,
    _jwt,
    _new_user,
    _post,
    db,  # noqa: F401 - fixture
    make_client,  # noqa: F401 - fixture
    make_tarball,
    settings,  # noqa: F401 - fixture
    transport,  # noqa: F401 - fixture
)

# ---------------------------------------------------------------------------
# fixture repos
# ---------------------------------------------------------------------------

FEDERATED_YAML = """
    version: 2
    name: acme-fed
    image: python-slim
    entrypoint: train.py
    mode: federated
    epochs: 3
"""

#: An entrypoint that speaks the delta protocol. It never has to *work* —
#: preflight is a static scan — but it is written as real code so the test
#: is pinned to the shape a user would actually write, not to a magic
#: comment containing the right substrings.
FEDERATED_TRAIN_PY = """
    import argparse
    import json
    import os

    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, default=0)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--num-shards", type=int, default=1)
    args = parser.parse_args()

    weights_path = "/work/inputs/weights.json"
    weights = None
    if os.path.exists(weights_path):
        with open(weights_path) as fh:
            weights = json.load(fh)

    delta = {"w": {"shape": [1], "data": [0.1]}}
    with open("/work/out/delta.json", "w") as fh:
        json.dump(delta, fh)
    with open("/work/out/metrics.json", "w") as fh:
        json.dump({"samples": 128, "loss": 0.5, "delta_file": "delta.json",
                   "chunks_done": [args.shard]}, fh)
"""

FEDERATED_REPO = {"flashml.yaml": FEDERATED_YAML, "train.py": FEDERATED_TRAIN_PY}

#: The same federated config with an entrypoint that ignores the contract —
#: exactly the ordinary sweep script from the independent tests.
OBLIVIOUS_REPO = {"flashml.yaml": FEDERATED_YAML, "train.py": CLEAN_TRAIN_PY}


def _config(text: str = FEDERATED_YAML):
    import textwrap

    return parse_flashml_yaml(textwrap.dedent(text))


# ---------------------------------------------------------------------------
# 1. the config
# ---------------------------------------------------------------------------


def test_a_federated_config_parses():
    config = _config()
    assert config.is_federated
    assert (config.epochs, config.sync_every, config.round_count) == (3, 1.0, 3)


def test_the_default_mode_is_independent_and_unchanged():
    config = _config("""
        version: 1
        name: acme
        image: python-slim
        entrypoint: train.py
    """)
    assert config.mode == "independent"
    assert not config.is_federated
    assert (config.epochs, config.sync_every) == (None, None)


def test_federated_keys_are_refused_under_the_default_mode():
    """Not ignored — refused. An author who wrote `rounds:` believes rounds
    are happening; running one round instead is the silent wrong answer this
    parser exists to prevent."""
    with pytest.raises(ConfigError, match="only apply to 'mode: federated'"):
        parse_flashml_yaml(
            "version: 2\nname: a\nimage: python-slim\nentrypoint: t.py\nepochs: 4\n"
        )


def test_a_sweep_cannot_be_federated():
    with pytest.raises(ConfigError, match="cannot combine 'sweep'"):
        _config("""
            version: 2
            name: acme
            image: python-slim
            entrypoint: train.py
            mode: federated
            epochs: 2
            sweep:
              lr: [0.1, 0.2]
        """)


# ---------------------------------------------------------------------------
# 2. compiling one round
# ---------------------------------------------------------------------------


#: The fleet these compile tests assume: two machines online, so a pass is
#: two chunks and a round has two slots — the shape every assertion below was
#: written against when it was spelled ``shards: 2``.
ROUND_FLEET = fleet_shape(2)


def _round(round_index: int, weights_uri: str | None):
    return compile_federated_round(
        _config(),
        resolve_image("python-slim"),
        "artifact://uploads/deadbeef/code.tar.gz",
        "acme-fed",
        round_index=round_index,
        weights_uri=weights_uri,
        slot_chunks=fedavgmod.slot_chunks_for(ROUND_FLEET, round_index, 1.0),
        total_chunks=ROUND_FLEET.total_chunks,
    )


def test_a_round_compiles_to_a_sandboxed_command_job():
    spec = _round(0, None)
    assert spec["spec"]["workload"]["type"] == "command"
    # Federation changes what the tasks compute, never what they may run as.
    assert spec["spec"]["isolation"] == {"tier": "sandboxed", "allowFallback": False}


def test_round_zero_stages_no_weights_input():
    """There is nothing to broadcast yet, and the input is *omitted* rather
    than pointed at an empty artifact — the user's code tests for the file's
    absence to know it is round 0."""
    params = _round(0, None)["spec"]["workload"]["parameters"]
    assert set(params["inputs"]) == {"code"}
    assert params["unpack_inputs"] == ["code"]


def test_later_rounds_stage_the_aggregated_weights():
    uri = "artifact://jobs/cjob-0/round-000/weights.json"
    params = _round(1, uri)["spec"]["workload"]["parameters"]
    assert params["inputs"]["weights"] == uri
    # ...and the weights are NOT unpacked: they are a JSON document the task
    # opens, not an archive.
    assert params["unpack_inputs"] == ["code"]


def test_each_shard_gets_its_index_and_the_round_number():
    params = _round(2, "artifact://jobs/c/round-001/weights.json")["spec"]["workload"]["parameters"]
    assert params["command"][-2:] == ["--shard", "{shard}"]
    assert "--round" in params["command"]
    assert params["command"][params["command"].index("--round") + 1] == "2"
    assert params["task_params"] == [{"shard": "0"}, {"shard": "1"}]


def test_the_compiled_task_ids_are_what_the_driver_will_look_for():
    """The driver filters committed artifacts against exactly this set — an
    artifact key outside it would mint a participant out of nothing — so a
    drift between the recipe's naming and this list is a quorum that can
    never be met."""
    assert federated_task_ids(2) == ["task-000", "task-001"]


def test_rounds_get_distinct_job_names():
    assert _round(0, None)["metadata"]["name"] != _round(1, "artifact://x")["metadata"]["name"]


def test_an_independent_config_cannot_be_compiled_as_a_round():
    config = _config("""
        version: 1
        name: acme
        image: python-slim
        entrypoint: train.py
    """)
    with pytest.raises(CompileError, match="mode: federated"):
        compile_federated_round(
            config, resolve_image("python-slim"), "artifact://a/b.tar.gz", "acme",
            round_index=0, weights_uri=None,
            slot_chunks=[0], total_chunks=1,
        )


# ---------------------------------------------------------------------------
# 3. preflight: the contract the user opts into
# ---------------------------------------------------------------------------


def _preflight(files: dict[str, str], tmp_path):
    import textwrap

    for name, content in files.items():
        (tmp_path / name).write_text(textwrap.dedent(content))
    config = _config(files["flashml.yaml"])
    return preflight(config, tmp_path, resolve_image("python-slim"))


def test_preflight_rejects_an_entrypoint_that_ignores_the_delta_contract(tmp_path):
    findings = _preflight(OBLIVIOUS_REPO, tmp_path)
    contract = [f for f in findings if f.code == "federated-contract"]
    assert len(contract) == 1
    assert contract[0].level == "error"
    # The finding has to say what to write, not merely that something is
    # missing: a refusal a user cannot act on is worse than no check.
    message = contract[0].message
    assert "/work/inputs/weights.json" in message
    assert "/work/out/delta.json" in message
    assert "samples" in message and "loss" in message


def test_preflight_accepts_an_entrypoint_that_speaks_the_protocol(tmp_path):
    findings = _preflight(FEDERATED_REPO, tmp_path)
    assert [f for f in findings if f.level == "error"] == []


def test_the_contract_check_does_not_fire_for_independent_jobs(tmp_path):
    files = {
        "flashml.yaml": """
            version: 1
            name: acme
            image: python-slim
            entrypoint: train.py
        """,
        "train.py": CLEAN_TRAIN_PY,
    }
    findings = _preflight(files, tmp_path)
    assert [f for f in findings if f.code == "federated-contract"] == []


# ---------------------------------------------------------------------------
# 4. the driver, against a stubbed coordinator
# ---------------------------------------------------------------------------


class StubCoordinator:
    """A coordinator that accepts round jobs and reports every slot done.

    Implements exactly the ``Coordinator`` protocol the driver uses. No
    HTTP, no live service: the round's committed artifacts are synthesised,
    which is enough because what is under test here is the API's invocation
    and persistence, not the averaging.

    ``chunks_done`` is read back out of the submitted body rather than
    invented, because the driver credits a slot only for chunks it can prove
    it handed that slot. A stub that reported a chunk it was never given would
    be credited zero, every round would sit until its duration backstop, and
    this file would hang rather than fail. ``test_elastic_driver.py`` is where
    that layout agreement is the point; here it is just the honest minimum.
    """

    #: Slots per round. Every ``FederatedRun`` in this file is built with
    #: ``fleet_shape(SLOTS)``, so the stub and the compiled round agree.
    shards = 2

    def __init__(self, fail_on_submit: bool = False):
        self.fail_on_submit = fail_on_submit
        self.submitted: list[dict] = []
        self.uploaded: dict[str, object] = {}
        #: ``{job_id: {task_id: chunk_id}}`` — what each round handed out.
        self.handed_out: dict[str, dict[str, int]] = {}

    def submit(self, body):
        if self.fail_on_submit:
            raise RuntimeError("coordinator unavailable")
        self.submitted.append(body)
        return {"job_id": self._record(f"cjob-{len(self.submitted) - 1:03d}", body)}

    def _record(self, job_id: str, body: dict) -> str:
        """Remember which chunk each task of ``job_id`` was handed.

        Separate from ``submit`` so a subclass that mints its own job ids —
        ``test_contributions.NamedNodeCoordinator`` — cannot forget it and
        leave ``get_artifact`` raising ``KeyError`` on its own round.
        """
        params = body["spec"]["workload"]["parameters"]
        self.handed_out[job_id] = {
            f"task-{i:03d}": int(p["shard"])
            for i, p in enumerate(params.get("task_params", []))
        }
        return job_id

    def job_state(self, job_id):
        return "RUNNING"

    def artifacts(self, job_id):
        return [
            {"key": f"jobs/{job_id}/task-{i:03d}/{name}"}
            for i in range(self.shards)
            for name in ("metrics.json", "delta.json")
        ]

    def tasks(self, job_id):
        return [
            {"task_id": f"task-{i:03d}", "state": "COMPLETED", "node_id": f"node-{i}"}
            for i in range(self.shards)
        ]

    def get_artifact(self, key):
        if key in self.uploaded:
            return self.uploaded[key]
        if key.endswith("metrics.json"):
            _, job_id, task_id, _ = key.split("/")
            return {"samples": 100, "loss": 0.25, "delta_file": "delta.json",
                    "chunks_done": [self.handed_out[job_id][task_id]]}
        if key.endswith("delta.json"):
            return {"w": {"shape": [1], "data": [0.5]}}
        from flashml_workloads.fedavg_driver import ArtifactNotFound

        raise ArtifactNotFound(key)

    def put_artifact(self, key, body):
        self.uploaded[key] = body


def _connect_factory(postgres_dsn):
    import psycopg
    from psycopg.rows import dict_row

    def connect():
        conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        return conn

    return connect


def _seed_job(db, owner_id: str) -> str:
    job_id = fedavgmod.new_federated_job_id()
    dbmod.insert_job(
        db, job_id=job_id, owner_id=owner_id, name="acme-fed",
        source={"type": "github", "mode": "federated", "rounds": 2},
        spec=None, status="PENDING",
    )
    return job_id


def _run(
    job_id: str, settings, postgres_dsn, coordinator, rounds: int = 2,
    pool: str | None = None,
):
    """Drive a run of ``rounds`` rounds.

    ``rounds`` is still the parameter every test here reads, because that is
    what these tests are about — but it is now expressed the way a submitter
    expresses it: at the default ``sync_every`` of one combine per pass,
    ``epochs`` and rounds are the same number.

    The fleet is fixed at ``StubCoordinator.shards`` slots so the stub's
    synthesised task set and the compiled round's task set agree; a mismatch
    is refused by ``run_fedavg`` rather than silently half-credited.
    """
    config = _config(FEDERATED_YAML.replace("epochs: 3", f"epochs: {rounds}"))
    run = fedavgmod.FederatedRun(
        job_id=job_id,
        job_name="acme-fed",
        config=config,
        image=resolve_image("python-slim"),
        code_artifact_uri="artifact://uploads/deadbeef/code.tar.gz",
        pool=pool,
        # The stub decides how many machines report, so the round must be cut
        # for exactly that many slots: `run_fedavg` refuses a builder whose
        # task count disagrees with the slot count it sized allotments from.
        fleet=fleet_shape(getattr(coordinator, "shards", StubCoordinator.shards)),
    )
    fedavgmod.run_federated_job(
        run,
        settings=settings,
        connect=_connect_factory(postgres_dsn),
        coordinator_factory=lambda _s: coordinator,
    )


def test_the_driver_persists_a_row_per_round(db, settings, postgres_dsn):
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    coordinator = StubCoordinator()

    _run(job_id, settings, postgres_dsn, coordinator, rounds=2)

    rows = dbmod.list_job_rounds_for_owner(db, job_id, owner)
    assert [r["round"] for r in rows] == [0, 1]
    assert [r["participants"] for r in rows] == [2, 2]
    assert rows[0]["mean_loss"] == pytest.approx(0.25)
    # Which machines actually contributed — accepted work, not attempted.
    assert rows[0]["contributors"] == ["node-0", "node-1"]
    assert rows[0]["coordinator_job_id"] == "cjob-000"
    # Two rounds, two coordinator jobs: a federated run is N jobs, and the
    # second one carries the first one's aggregated weights.
    assert len(coordinator.submitted) == 2
    second = coordinator.submitted[1]["spec"]["workload"]["parameters"]
    assert second["inputs"]["weights"] == \
        "artifact://jobs/cjob-000/round-000/weights.json"


def test_a_completed_run_marks_the_job_succeeded(db, settings, postgres_dsn):
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    _run(job_id, settings, postgres_dsn, StubCoordinator(), rounds=1)
    row = dbmod.fetch_job_for_owner(db, job_id, owner)
    assert row["status"] == "SUCCEEDED"
    assert row["finished_at"] is not None


def test_a_failed_driver_marks_the_job_failed(db, settings, postgres_dsn):
    """The whole reason the driver is wrapped rather than handed straight to
    a thread: an exception on a daemon thread is printed and forgotten, and
    the job would read RUNNING for ever with no round ever appearing."""
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    with pytest.raises(Exception):
        _run(job_id, settings, postgres_dsn, StubCoordinator(fail_on_submit=True))
    row = dbmod.fetch_job_for_owner(db, job_id, owner)
    assert row["status"] == "FAILED"
    assert row["finished_at"] is not None
    assert dbmod.list_job_rounds_for_owner(db, job_id, owner) == []


def test_a_restarted_driver_resumes_instead_of_retraining(db, settings, postgres_dsn):
    """``resume_state`` is wired up: a driver started against a job that
    already has rounds picks up after the last one whose weights were
    committed. (What is NOT wired is anything that starts a driver again
    after the API dies — see ``run_federated_job``'s docstring.)"""
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    coordinator = StubCoordinator()

    _run(job_id, settings, postgres_dsn, coordinator, rounds=1)
    assert len(coordinator.submitted) == 1

    # Same coordinator (so round 0's weights artifact is still there), same
    # job, now asked for two rounds: only round 1 may be submitted.
    _run(job_id, settings, postgres_dsn, coordinator, rounds=2)
    assert len(coordinator.submitted) == 2
    rounds = dbmod.list_job_rounds_for_owner(db, job_id, owner)
    assert [r["round"] for r in rounds] == [0, 1]


class SizedStubCoordinator(StubCoordinator):
    """StubCoordinator whose artifact listing reports sizes, as the real
    coordinator's ``GET /v1alpha1/jobs/{id}/artifacts`` does."""

    size_bytes = 100

    def artifacts(self, job_id):
        return [
            {**entry, "size_bytes": self.size_bytes}
            for entry in super().artifacts(job_id)
        ]


def test_a_finished_run_records_the_footprint_of_every_round(
    db, settings, postgres_dsn
):
    """A federated run is N coordinator jobs, and every round's outputs stay
    on the same shared disk. Measuring only the last round — or only the
    parent id, which names no coordinator job at all — would under-report an
    N-round run by roughly a factor of N, and that is precisely the account
    most able to fill the disk."""
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    # 2 shards x 2 files x 100 bytes per round, over 2 rounds.
    _run(job_id, settings, postgres_dsn, SizedStubCoordinator(), rounds=2)

    assert dbmod.storage_usage_for_owner(db, owner) == 800
    row = dbmod.fetch_job_for_owner(db, job_id, owner)
    assert row["artifact_bytes_recorded_at"] is not None


def test_a_run_that_failed_is_still_measured(db, settings, postgres_dsn):
    """A run that died after writing three rounds of weights still occupies
    that disk. Measuring only successful runs would make "submit, write,
    fail" a free way to fill it."""
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    coordinator = SizedStubCoordinator()
    _run(job_id, settings, postgres_dsn, coordinator, rounds=1)
    # A second run of the same job that blows up before recording anything
    # new: the footprint of what is already there must survive.
    coordinator.fail_on_submit = True
    with pytest.raises(Exception):
        _run(job_id, settings, postgres_dsn, coordinator, rounds=2)

    assert dbmod.fetch_job_for_owner(db, job_id, owner)["status"] == "FAILED"
    assert dbmod.storage_usage_for_owner(db, owner) == 400


def test_a_coordinator_that_cannot_list_artifacts_does_not_fail_the_run(
    db, settings, postgres_dsn
):
    """The run has already aggregated every round and written its weights.
    Losing all of that because a listing was momentarily unavailable would
    be the accounting tail wagging the dog — same rule as the round's own
    contributor and credit writes."""
    class Broken:
        def artifacts(self, job_id):
            raise RuntimeError("listing unavailable")

    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    dbmod.insert_job_round(
        db, job_id=job_id, round_index=0, participants=2, mean_loss=1.0,
        contributors=[], coordinator_job_id="cjob-000",
    )
    fedavgmod.record_run_footprint(
        Broken(), _connect_factory(postgres_dsn), job_id
    )

    row = dbmod.fetch_job_for_owner(db, job_id, owner)
    # Nothing recorded, and NOT stamped as measured: a failed listing is not
    # an answer, and the next finish of this job may still measure it.
    assert row["artifact_bytes_recorded_at"] is None
    assert dbmod.storage_usage_for_owner(db, owner) == 0


def test_a_run_with_no_rounds_costs_no_listing_call(db, settings, postgres_dsn):
    """A run that failed before its first round has nothing to measure, and
    must not spend a coordinator call finding that out."""
    calls: list[str] = []

    class Counting:
        def artifacts(self, job_id):
            calls.append(job_id)
            return []

    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    fedavgmod.record_run_footprint(
        Counting(), _connect_factory(postgres_dsn), job_id
    )
    assert calls == []


def test_re_running_a_finished_job_is_idempotent(db, settings, postgres_dsn):
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    coordinator = StubCoordinator()
    _run(job_id, settings, postgres_dsn, coordinator, rounds=1)
    _run(job_id, settings, postgres_dsn, coordinator, rounds=1)
    assert len(coordinator.submitted) == 1
    assert len(dbmod.list_job_rounds_for_owner(db, job_id, owner)) == 1


# ---------------------------------------------------------------------------
# 4b. pool threading — FederatedRun.pool must reach the run_fedavg call
# ---------------------------------------------------------------------------


def _capture_run_fedavg(monkeypatch) -> dict:
    """Replace ``fedavgmod.run_fedavg`` and return the dict its kwargs land
    in. Same boundary ``_run_reporting`` patches below, for the same reason:
    what is under test is the *call*, not the averaging arithmetic."""
    captured: dict = {}

    def fake_run_fedavg(coord, **kwargs):
        captured.update(kwargs)
        return {"weights": {}, "history": [], "job_ids": []}

    monkeypatch.setattr(fedavgmod, "run_fedavg", fake_run_fedavg)
    return captured


def test_a_pool_scoped_run_threads_pool_and_the_waiver_into_run_fedavg(
    db, settings, postgres_dsn, monkeypatch
):
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    captured = _capture_run_fedavg(monkeypatch)

    _run(job_id, settings, postgres_dsn, StubCoordinator(), rounds=1, pool="p-1")

    assert captured["pool"] == "p-1"
    assert captured["allow_fallback"] is True


def test_a_run_with_no_pool_passes_none_and_no_waiver(
    db, settings, postgres_dsn, monkeypatch
):
    """The regression guard: an independent (non-pool) run's call into
    ``run_fedavg`` is exactly what it was before this parameter existed —
    ``pool=None``, ``allow_fallback=False``."""
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    captured = _capture_run_fedavg(monkeypatch)

    _run(job_id, settings, postgres_dsn, StubCoordinator(), rounds=1)

    assert captured["pool"] is None
    assert captured["allow_fallback"] is False


def test_a_pool_scoped_run_stamps_every_submitted_round_body(
    db, settings, postgres_dsn
):
    """The functional half of pool confinement, exercised end to end with
    the REAL ``run_fedavg`` (no monkeypatch): ``run_fedavg``'s own
    ``pool``/``allow_fallback`` kwargs are inert once ``build_round`` is
    supplied (``fedavg.py`` always supplies one) — what actually stamps
    ``placement.pool``/``isolation.allowFallback`` onto a round is
    ``build_round_for`` threading ``run.pool`` into
    ``compile_federated_round``. The two tests above patch ``run_fedavg``
    away and would stay green even if that thread were deleted; this one
    would not, because it asserts on the bodies the stub coordinator
    actually received across every round of a multi-round run."""
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    coordinator = StubCoordinator()

    _run(job_id, settings, postgres_dsn, coordinator, rounds=2, pool="p-1")

    assert len(coordinator.submitted) == 2
    for body in coordinator.submitted:
        assert body["spec"]["placement"]["pool"] == "p-1"
        assert body["spec"]["isolation"]["allowFallback"] is True


def test_a_run_with_no_pool_stamps_any_on_every_submitted_round_body(
    db, settings, postgres_dsn
):
    """The regression guard for the test above: an independent run's round
    bodies are unchanged — ``placement.pool: "any"``,
    ``isolation.allowFallback: false`` — on every round, not just the
    first."""
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    coordinator = StubCoordinator()

    _run(job_id, settings, postgres_dsn, coordinator, rounds=2)

    assert len(coordinator.submitted) == 2
    for body in coordinator.submitted:
        assert body["spec"]["placement"]["pool"] == "any"
        assert body["spec"]["isolation"]["allowFallback"] is False


# ---------------------------------------------------------------------------
# 5. submitting a federated repo
# ---------------------------------------------------------------------------


class RecordingStarter:
    def __init__(self):
        self.runs: list[fedavgmod.FederatedRun] = []

    def __call__(self, run, **kwargs):
        self.runs.append(run)
        return None


@pytest.fixture
def federated_client(settings, postgres_dsn, transport):
    """A client whose federated driver is recorded rather than launched."""
    from fastapi.testclient import TestClient

    from flashml_cloud_api.app import create_cloud_app

    starter = RecordingStarter()

    def build(files=None):
        app = create_cloud_app(
            settings,
            connect=_connect_factory(postgres_dsn),
            transport=transport,
            fetch_repo=RecordingFetch(make_tarball(files or FEDERATED_REPO)),
            start_federated_job=starter,
        )
        client = TestClient(app)
        client.__enter__()
        client.starter = starter  # type: ignore[attr-defined]
        return client

    return build


def test_submitting_a_federated_repo_starts_the_driver(federated_client, db, transport):
    client = federated_client()
    owner = _new_user(db)
    r = _post(client, _jwt(owner))
    assert r.status_code == 201, r.text

    body = r.json()
    assert body["mode"] == "federated"
    # What the author asked for, and what the fleet turned it into. The slot
    # count is whatever was online — asserted as "at least the floor" rather
    # than a number, because the machines in this shared database belong to
    # whichever tests ran first.
    assert (body["epochs"], body["sync_every"]) == (3, 1.0)
    assert body["rounds"] == 3
    assert body["slots"] >= 1
    assert body["job_id"].startswith(fedavgmod.JOB_ID_PREFIX)

    # The driver was started, with everything it needs to rebuild any round.
    assert len(client.starter.runs) == 1
    run = client.starter.runs[0]
    # Including the fleet shape, without which every round would be recut
    # from a Crew that has moved on since the job was submitted.
    assert run.fleet.slots == body["slots"]
    assert run.job_id == body["job_id"]
    assert run.code_artifact_uri.startswith("artifact://uploads/")

    # The code was staged, but no job was submitted from the request: the
    # driver submits round 0 itself, so a run can never end up with a round
    # the driver does not know it owns.
    assert transport.artifacts
    assert transport.job_submissions == []

    rows = _job_rows(db, owner)
    assert len(rows) == 1
    assert rows[0]["source"]["mode"] == "federated"
    assert rows[0]["status"] == "PENDING"


def test_a_federated_repo_that_breaks_the_contract_is_refused(
    federated_client, db, transport
):
    client = federated_client(OBLIVIOUS_REPO)
    owner = _new_user(db)
    r = _post(client, _jwt(owner))
    assert r.status_code == 400
    assert any(f["code"] == "federated-contract" for f in r.json()["findings"])
    # Nothing staged, nothing started, no row: same all-or-nothing rule the
    # independent path already holds itself to.
    assert transport.requests == []
    assert client.starter.runs == []
    assert _job_rows(db, owner) == []


def test_an_independent_repo_still_submits_one_job_and_starts_no_driver(
    federated_client, db, transport
):
    """The regression guard for everything that already worked: with
    ``mode`` absent, the from-repo path is exactly what it was."""
    from test_jobs_from_repo import CLEAN_REPO

    client = federated_client(CLEAN_REPO)
    owner = _new_user(db)
    r = _post(client, _jwt(owner))
    assert r.status_code == 201
    assert len(transport.job_submissions) == 1
    assert client.starter.runs == []
    assert r.json()["job_id"].startswith("job-")


def test_a_pool_scoped_federated_submission_threads_pool_into_the_run(
    federated_client, db, transport
):
    """The from-repo route's ``pool`` body field has to reach the
    ``FederatedRun`` handed to the driver — the other half of the trip
    ``test_a_pool_scoped_run_threads_pool_and_the_waiver_into_run_fedavg``
    pins from ``FederatedRun`` onward."""
    client = federated_client()
    owner = _new_user(db)
    pool_id = str(dbmod.create_pool(db, name="Ada's Team", owner_id=owner)["id"])

    r = _post(client, _jwt(owner), pool=pool_id)
    assert r.status_code == 201, r.text

    assert len(client.starter.runs) == 1
    assert client.starter.runs[0].pool == pool_id


# ---------------------------------------------------------------------------
# 6. GET /v1alpha1/jobs/{id}/rounds
# ---------------------------------------------------------------------------


def test_rounds_are_returned_to_the_owner(make_client, db, settings, postgres_dsn):
    client = make_client()
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    _run(job_id, settings, postgres_dsn, StubCoordinator(), rounds=2)

    r = client.get(f"/v1alpha1/jobs/{job_id}/rounds",
                   headers={"Authorization": f"Bearer {_jwt(owner)}"})
    assert r.status_code == 200
    rounds = r.json()
    assert [x["round"] for x in rounds] == [0, 1]
    assert rounds[0]["contributors"] == ["node-0", "node-1"]
    assert rounds[0]["participants"] == 2


def test_another_users_rounds_are_404_never_403(make_client, db, settings, postgres_dsn):
    """A 403 would confirm to a guesser that the id is real — the same rule
    every other job route here already holds."""
    client = make_client()
    owner = _new_user(db)
    stranger = _new_user(db)
    job_id = _seed_job(db, owner)
    _run(job_id, settings, postgres_dsn, StubCoordinator(), rounds=1)

    r = client.get(f"/v1alpha1/jobs/{job_id}/rounds",
                   headers={"Authorization": f"Bearer {_jwt(stranger)}"})
    assert r.status_code == 404


def test_rounds_require_a_signed_in_user(make_client, db):
    client = make_client()
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    assert client.get(f"/v1alpha1/jobs/{job_id}/rounds").status_code == 401


def test_rounds_for_an_unknown_job_are_404(make_client, db):
    client = make_client()
    owner = _new_user(db)
    r = client.get(f"/v1alpha1/jobs/fed-{uuid.uuid4().hex[:12]}/rounds",
                   headers={"Authorization": f"Bearer {_jwt(owner)}"})
    assert r.status_code == 404


def test_a_federated_job_is_answered_locally_not_forwarded(
    make_client, db, transport, settings, postgres_dsn
):
    """A federated run has no single coordinator job, so forwarding its id
    would 404 a job the user does own."""
    client = make_client()
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    _run(job_id, settings, postgres_dsn, StubCoordinator(), rounds=2)
    before = len(transport.requests)

    r = client.get(f"/v1alpha1/jobs/{job_id}",
                   headers={"Authorization": f"Bearer {_jwt(owner)}"})
    assert r.status_code == 200
    assert r.json()["rounds_completed"] == 2
    assert r.json()["mode"] == "federated"
    assert len(transport.requests) == before  # never forwarded


def test_a_federated_job_appears_in_the_job_list(make_client, db):
    client = make_client()
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    r = client.get("/v1alpha1/jobs",
                   headers={"Authorization": f"Bearer {_jwt(owner)}"})
    assert r.status_code == 200
    assert [j["job_id"] for j in r.json()] == [job_id]


def test_start_federated_job_runs_on_a_background_thread(settings, postgres_dsn, db):
    """The launcher is a plain daemon thread, not the request threadpool: a
    run occupies it for hours and would starve every other blocking call."""
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    config = _config(FEDERATED_YAML.replace("rounds: 3", "rounds: 1"))
    thread = fedavgmod.start_federated_job(
        fedavgmod.FederatedRun(
            job_id=job_id, job_name="acme-fed", config=config,
            image=resolve_image("python-slim"),
            code_artifact_uri="artifact://uploads/deadbeef/code.tar.gz",
        ),
        settings=settings,
        connect=_connect_factory(postgres_dsn),
        coordinator_factory=lambda _s: StubCoordinator(),
    )
    assert isinstance(thread, threading.Thread) and thread.daemon
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert dbmod.fetch_job_for_owner(db, job_id, owner)["status"] == "SUCCEEDED"


# ---------------------------------------------------------------------------
# 7. bounded influence: recording which contributions the cap bound
# ---------------------------------------------------------------------------


def _clipped_rows(db, job_id: str) -> list[list[dict]]:
    """``job_rounds.clipped``, oldest round first.

    Read with SQL rather than through ``list_job_rounds_for_owner``: the
    clip record is deliberately NOT in ``JOB_ROUND_PUBLIC_COLUMNS``. Nothing
    is enforced from it and no console screen shows it — decision 2 of the
    design spec is "record, do not act", and the row exists so the operator
    can look. Putting it in the owner-facing response is a separate product
    decision, not a side effect of storing it.
    """
    with db.cursor() as cur:
        cur.execute(
            "select round, clipped from public.job_rounds"
            " where job_id = %s order by round",
            (job_id,),
        )
        return [r["clipped"] for r in cur.fetchall()]


def _run_reporting(job_id, settings, postgres_dsn, coordinator, clipped, monkeypatch):
    """Drive one round whose driver reports ``clipped``, and record it.

    The runtime pinned into this venv is 0.4.1, which predates
    ``RoundResult``'s ``clipped`` key entirely, so the real ``run_fedavg``
    cannot produce one to join against. What is under test here is the
    *cloud's* join — task id to node id — not the driver's clipping, which
    is pinned by flashruntime's own suite. So the driver is replaced by one
    that reports exactly the shape the real one now emits, and everything
    downstream of ``on_round`` is the production path.
    """
    def fake_run_fedavg(coord, **kwargs):
        kwargs["on_round"]({
            "round": 0,
            "participants": 2,
            "mean_loss": 0.25,
            "job_id": "cjob-000",
            "clipped": clipped,
        })
        return {"weights": {}, "history": [], "job_ids": ["cjob-000"]}

    monkeypatch.setattr(fedavgmod, "run_fedavg", fake_run_fedavg)
    _run(job_id, settings, postgres_dsn, coordinator, rounds=1)


def test_an_honest_round_records_no_clip_events(db, settings, postgres_dsn):
    """The governing property, cloud-side: a round nobody tried to steer
    stores the empty list.

    This runs the *real* driver from the installed runtime, which does not
    report a ``clipped`` key at all — so it also pins that a round from a
    runtime older than this feature is recorded normally rather than
    raising a KeyError inside ``on_round`` and failing an aggregation that
    already succeeded.
    """
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    _run(job_id, settings, postgres_dsn, StubCoordinator(), rounds=2)
    assert _clipped_rows(db, job_id) == [[], []]


def test_a_clipped_round_names_the_node_that_sent_it(
    db, settings, postgres_dsn, monkeypatch
):
    """The join this task exists for. The driver reports a task id — it has
    no node ids at all — and the node id is added here, from the same
    accepted-task list that feeds the credit ledger, so provenance and
    credit can never name different machines for the same task.
    """
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    _run_reporting(
        job_id, settings, postgres_dsn, StubCoordinator(),
        [{"task_id": "task-001", "norm": 1000.0, "cap": 3.15, "scale": 0.00315}],
        monkeypatch,
    )
    assert _clipped_rows(db, job_id) == [[
        {"task_id": "task-001", "norm": 1000.0, "cap": 3.15,
         "scale": 0.00315, "node_id": "node-1"},
    ]]


def test_a_clip_event_whose_task_has_no_accepted_node_is_kept_with_a_null(
    db, settings, postgres_dsn, monkeypatch
):
    """A missed join stores ``node_id: None``; it never drops the event.

    ``_accepted_tasks`` filters on COMPLETED *and* on having a node id, so
    a task that was clipped and then re-leased, or whose task view was
    momentarily unreadable, has no node to name. An event that vanished
    because the join missed would be an attempt to steer the model that
    left no trace anywhere — strictly worse than one recorded against an
    unknown machine, because the whole point is that the operator can look.
    The honest event beside it must still be attributed.
    """
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    _run_reporting(
        job_id, settings, postgres_dsn, StubCoordinator(),
        [
            {"task_id": "task-009", "norm": 1e6, "cap": 3.15, "scale": 3.15e-6},
            {"task_id": "task-000", "norm": 9.0, "cap": 3.15, "scale": 0.35},
        ],
        monkeypatch,
    )
    assert _clipped_rows(db, job_id) == [[
        {"task_id": "task-009", "norm": 1e6, "cap": 3.15,
         "scale": 3.15e-6, "node_id": None},
        {"task_id": "task-000", "norm": 9.0, "cap": 3.15,
         "scale": 0.35, "node_id": "node-0"},
    ]]


def test_a_round_recorded_without_clip_events_stores_an_empty_list(db):
    """``insert_job_round``'s argument is optional, and omitting it stores
    ``[]`` rather than null — the column's own default, not a second
    convention invented in Python."""
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    dbmod.insert_job_round(
        db, job_id=job_id, round_index=0, participants=1, mean_loss=0.5,
        contributors=["node-0"], coordinator_job_id="cjob-000",
    )
    assert _clipped_rows(db, job_id) == [[]]


def test_cancelling_a_federated_run_says_so_instead_of_lying_with_404(
    make_client, db, transport
):
    """Forwarding a parent id the coordinator has never seen would answer
    404 for a job the caller does own."""
    client = make_client()
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    before = len(transport.requests)
    r = client.post(f"/v1alpha1/jobs/{job_id}/cancel",
                    headers={"Authorization": f"Bearer {_jwt(owner)}"})
    assert r.status_code == 501
    assert "not implemented" in r.json()["detail"]
    assert len(transport.requests) == before
