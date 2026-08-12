"""Pool visibility (Task 13): member job reads and the credit view.

Task 9 widened ``fetch_job_for_owner`` into ``fetch_job_for_viewer`` — the
owner, or any member of the job's pool, may see it — and added
``list_pool_job_ids_for_member`` (the third source the jobs list has to
union in) and ``list_job_contributions`` (the joined credit view, with NO
viewer argument by design: it trusts its caller to have authorized first).
This file is the browser-route-level proof that those three actually reach
the surface: the four job read routes (get job, rounds, events, tasks) plus
the new contributions route accept a pool-mate exactly as they accept the
owner, cancel does not, and the jobs list shows a shared job once — never
twice for the owner, and not at all for a stranger.

Runs against the same migrated Postgres as the rest of the suite, reusing
``test_jobs_from_repo``'s ``db``/``settings``/``_jwt``/``_new_user`` exactly
as ``test_profile.py`` and ``test_pools_api.py`` do. The fake coordinator
here is ``test_jobs.py``'s, extended with the two forwarding routes
(``/events``, ``/tasks``) this file's read routes need that the original
had no reason to implement.

Every pool job here is built by submitting a real, ordinary job through
``POST /v1alpha1/jobs`` (so it is genuinely known to both the fake
coordinator and the ``jobs`` table, exactly like ``test_jobs.py``'s own
jobs) and then stamping ``pool_id`` onto that row with one direct SQL
``update`` — the same shortcut ``test_db_pools.py``'s ``_seed_job`` takes
for the same reason: the production route for a *pool-scoped* submission is
``/v1alpha1/jobs/from-repo`` (Task 12), full repo-tarball machinery the read
side under test here has no need to exercise.

The federated-jobs-list section below (added in the fix-loop pass —
``list_federated_jobs_for_viewer``) seeds its rows the same way
``test_federated.py``'s own ``_seed_job`` does — ``fedavgmod.new_federated_job_id``
plus ``dbmod.insert_job`` directly, ``pool_id`` included — rather than
running the actual driver (``StubCoordinator``/``run_federated_job``): these
tests are about listing visibility, not the run itself, and a federated
job's presence in ``GET /v1alpha1/jobs`` never touches the coordinator at
all (it comes from the ``jobs`` table alone), so there is nothing here for
a live driver to add.

No skips in this file, same rule ``test_jobs_from_repo.py`` states: a
security test that skips reads as green in CI and is worse than no test at
all.
"""
from __future__ import annotations

import json
import uuid

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import fedavg as fedavgmod
from flashml_cloud_api.app import create_cloud_app

from test_jobs_from_repo import (  # noqa: F401 - fixtures
    _jwt,
    _new_user,
    db,
    settings,
)

RUN_MARKER = uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# the fake coordinator
# ---------------------------------------------------------------------------


class FakeCoordinatorTransport(httpx.AsyncBaseTransport):
    """``test_jobs.py``'s fake, plus ``/events`` and ``/tasks`` forwarding.

    Job listing/detail/cancel/artifacts are unscoped, exactly like the real
    coordinator: it has no accounts, so any scoping a caller of this API
    sees must come from this API's own database, never from this fake
    pretending to enforce something the real coordinator does not.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._jobs: dict[str, dict] = {}
        # A fresh per-instance prefix, not just a counter reset to 1: the
        # ``jobs`` table is a real Postgres table shared by the whole test
        # session, so two tests both minting "job-0001" would collide on
        # the primary key even though each test's transport is otherwise
        # isolated.
        self._prefix = uuid.uuid4().hex[:10]
        self._next_id = 1
        self.artifacts: dict[str, bytes] = {}
        #: Job ids whose `checkpoints/latest` answers 404 — "no valid
        #: checkpoint", which is NOT the same as an unknown job and must
        #: reach the caller distinguishably.
        self.no_checkpoint_jobs: set[str] = set()

    def seed_artifact(self, key: str, content: bytes) -> None:
        self.artifacts[key] = content

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        method, path = request.method, request.url.path

        if method == "POST" and path == "/v1alpha1/jobs":
            body = json.loads(request.content or b"{}")
            job_id = f"job-{self._prefix}-{self._next_id:04d}"
            self._next_id += 1
            record = {
                "job_id": job_id,
                "spec": body,
                "state": "RUNNING",
                "backend": "leases",
            }
            self._jobs[job_id] = record
            return httpx.Response(201, json=record)

        if method == "GET" and path == "/v1alpha1/jobs":
            return httpx.Response(200, json=list(self._jobs.values()))

        if method == "GET" and path.startswith("/v1alpha1/jobs/") and path.count("/") == 3:
            job_id = path.rsplit("/", 1)[-1]
            record = self._jobs.get(job_id)
            if record is None:
                return httpx.Response(404, json={"detail": "no such job"})
            return httpx.Response(200, json=record)

        if method == "GET" and path.endswith("/events") and path.count("/") == 4:
            job_id = path.split("/")[-2]
            if job_id not in self._jobs:
                return httpx.Response(404, json={"detail": "no such job"})
            return httpx.Response(200, json=[{"seq": 0, "kind": "created"}])

        if method == "GET" and path.endswith("/tasks") and path.count("/") == 4:
            job_id = path.split("/")[-2]
            if job_id not in self._jobs:
                return httpx.Response(404, json={"detail": "no such job"})
            return httpx.Response(200, json=[{"task_id": "task-0", "state": "RUNNING"}])

        # The job-scoped artifact listing. Unscoped like everything else here:
        # the real coordinator has no accounts, so it answers this for any id
        # it is given, and any scoping the console sees is this API's alone.
        if method == "GET" and path.endswith("/artifacts") and path.count("/") == 4:
            prefix = f"jobs/{path.split('/')[-2]}/"
            return httpx.Response(200, json=[
                {"uri": f"artifact://{k}", "key": k, "size_bytes": len(v)}
                for k, v in sorted(self.artifacts.items())
                if k.startswith(prefix)
            ])

        # `/v1alpha1/jobs/<job>/tasks/<task>/checkpoints/latest` — seven
        # slashes. The browser route is `.../checkpoint` (singular); it is
        # the CLOUD that rewrites it to this, so the fake answers only the
        # coordinator's spelling and a test that forgot the rewrite fails.
        if (method == "GET" and path.endswith("/checkpoints/latest")
                and path.count("/") == 7):
            job_id = path.split("/")[3]
            if job_id not in self._jobs:
                return httpx.Response(404, json={"detail": "no such job"})
            if job_id in self.no_checkpoint_jobs:
                return httpx.Response(404, json={"detail": "no valid checkpoint"})
            return httpx.Response(200, json={"step": 42, "parts": [
                {"key": f"jobs/{job_id}/task-0/ckpt/step-42.json"}]})

        if method == "POST" and path.endswith("/cancel"):
            job_id = path.split("/")[-2]
            record = self._jobs.get(job_id)
            if record is None:
                return httpx.Response(404, json={"detail": "no such job"})
            record = dict(record, state="CANCELLED")
            self._jobs[job_id] = record
            return httpx.Response(200, json=record)

        if method == "GET" and path.startswith("/v1alpha1/artifacts/"):
            key = path[len("/v1alpha1/artifacts/"):]
            content = self.artifacts.get(key)
            if content is None:
                return httpx.Response(404, json={"detail": "no such artifact"})
            return httpx.Response(
                200, content=content,
                headers={"content-type": "application/octet-stream"},
            )

        return httpx.Response(
            404, json={"detail": f"unhandled fake coordinator route: {method} {path}"}
        )


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def transport() -> FakeCoordinatorTransport:
    return FakeCoordinatorTransport()


@pytest.fixture
def client(settings, postgres_dsn, transport):
    def connect() -> psycopg.Connection:
        conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        return conn

    app = create_cloud_app(settings, connect=connect, transport=transport)
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_jwt(user_id)}"}


def _node_id(tag: str) -> str:
    """A node id no other test can collide with on ``machines.node_id``."""
    return f"node-{RUN_MARKER}-{tag}-{uuid.uuid4().hex[:6]}"


def _pool(db, owner_id: str, name: str = "Team") -> str:
    return dbmod.create_pool(db, name=name, owner_id=owner_id)["id"]


def _add_member(db, pool_id: str, user_id: str) -> None:
    """Join ``user_id`` to ``pool_id`` directly, bypassing invites — for
    tests whose point is not the invite flow itself."""
    with db.cursor() as cur:
        cur.execute(
            "insert into public.pool_members (pool_id, user_id) values (%s, %s)",
            (pool_id, user_id),
        )


def _submit(client, jwt_token: str, name: str) -> dict:
    r = client.post(
        "/v1alpha1/jobs",
        json={"apiVersion": "flashml.dev/v1alpha1", "kind": "Job",
              "metadata": {"name": name}, "spec": {}},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _seed_pool_job(client, owner_id: str, pool_id: str, db, name: str) -> str:
    """Submit a real, ordinary job for ``owner_id`` and then stamp
    ``pool_id`` onto its row directly. See the module docstring for why
    this is the shortcut and not ``/v1alpha1/jobs/from-repo`` itself."""
    job_id = _submit(client, _jwt(owner_id), name)["job_id"]
    with db.cursor() as cur:
        cur.execute(
            "update public.jobs set pool_id = %s where id = %s",
            (pool_id, job_id),
        )
    return job_id


def _seed_federated_job(
    db, owner_id: str, name: str, pool_id: str | None = None
) -> str:
    """A federated ``jobs`` row via the same two production helpers
    ``test_federated.py``'s own ``_seed_job`` uses —
    ``fedavgmod.new_federated_job_id`` (so ``fedavgmod.is_federated_job_id``
    recognizes it, exactly as it would a real run's id) and
    ``dbmod.insert_job`` (the real write path, ``pool_id`` included) —
    without running the actual driver. A federated job's presence in
    ``GET /v1alpha1/jobs`` is read straight from this table
    (``list_federated_jobs_for_viewer``); no coordinator round trip is
    involved, so there is nothing a live driver run would add here.
    """
    job_id = fedavgmod.new_federated_job_id()
    dbmod.insert_job(
        db, job_id=job_id, owner_id=owner_id, name=name,
        source={"type": "github", "mode": "federated", "rounds": 2},
        spec=None, status="PENDING", pool_id=pool_id,
    )
    return job_id


# ---------------------------------------------------------------------------
# 1. a pool member reads what the owner reads
# ---------------------------------------------------------------------------


def test_pool_member_reads_job_rounds_events_tasks_and_contributions(
    client, db, transport
):
    owner = _new_user(db)
    member = _new_user(db)
    pool_id = _pool(db, owner)
    _add_member(db, pool_id, member)
    job_id = _seed_pool_job(client, owner, pool_id, db, "pool-job")

    dbmod.insert_job_round(
        db, job_id=job_id, round_index=0, participants=1, mean_loss=0.42,
        contributors=["node-x"], coordinator_job_id=None,
    )
    node_id = _node_id("credit")
    dbmod.insert_machine(
        db, owner_id=owner, node_id=node_id, name="laptop", platform="linux",
    )
    dbmod.record_contributions(
        db, job_id=job_id,
        entries=[{"node_id": node_id, "task_id": "task-000", "duration_s": 4.0}],
    )

    member_auth = _auth(member)

    r = client.get(f"/v1alpha1/jobs/{job_id}", headers=member_auth)
    assert r.status_code == 200, r.text
    assert r.json()["job_id"] == job_id

    r = client.get(f"/v1alpha1/jobs/{job_id}/rounds", headers=member_auth)
    assert r.status_code == 200, r.text
    rounds = r.json()
    assert len(rounds) == 1
    assert rounds[0]["round"] == 0
    # And the owner sees the exact same history — a pool member is not
    # silently getting a *different* (empty) answer than the owner.
    owner_rounds = client.get(
        f"/v1alpha1/jobs/{job_id}/rounds", headers=_auth(owner)
    ).json()
    assert rounds == owner_rounds

    r = client.get(f"/v1alpha1/jobs/{job_id}/events", headers=member_auth)
    assert r.status_code == 200, r.text
    assert r.json() == [{"seq": 0, "kind": "created"}]

    r = client.get(f"/v1alpha1/jobs/{job_id}/tasks", headers=member_auth)
    assert r.status_code == 200, r.text
    assert r.json() == [{"task_id": "task-0", "state": "RUNNING"}]

    r = client.get(f"/v1alpha1/jobs/{job_id}/contributions", headers=member_auth)
    assert r.status_code == 200, r.text
    contributions = r.json()
    assert len(contributions) == 1
    assert contributions[0]["node_id"] == node_id
    assert contributions[0]["tasks_credited"] == 1
    assert contributions[0]["total_duration_s"] == 4.0

    # Cancel stays owner-only: the same pool member gets 404, and the
    # coordinator is never contacted for it.
    before = len(transport.requests)
    r = client.post(f"/v1alpha1/jobs/{job_id}/cancel", headers=member_auth)
    assert r.status_code == 404
    assert len(transport.requests) == before


def test_owner_can_still_cancel_their_own_pool_job(client, db, transport):
    """Task 13 widens READS only. The owner's cancel path is untouched."""
    owner = _new_user(db)
    pool_id = _pool(db, owner)
    job_id = _seed_pool_job(client, owner, pool_id, db, "cancel-me")

    r = client.post(f"/v1alpha1/jobs/{job_id}/cancel", headers=_auth(owner))
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "CANCELLED"


def test_pool_member_can_read_a_pool_jobs_artifacts(client, db, transport):
    """Artifact reads authorize via the same ownership check as the other
    browser job routes (``fetch_job_for_owner`` in ``get_job_artifact``) —
    a read, so it moves to ``fetch_job_for_viewer`` along with the other
    four, and is covered here for the same reason they are."""
    owner = _new_user(db)
    member = _new_user(db)
    pool_id = _pool(db, owner)
    _add_member(db, pool_id, member)
    job_id = _seed_pool_job(client, owner, pool_id, db, "pool-job-with-artifacts")
    transport.seed_artifact(f"jobs/{job_id}/output.txt", b"pool output")

    r = client.get(
        f"/v1alpha1/jobs/{job_id}/artifacts/output.txt", headers=_auth(member)
    )
    assert r.status_code == 200
    assert r.content == b"pool output"


def test_pool_member_can_list_a_pool_jobs_artifacts(client, db, transport):
    """The LISTING route, which is what the console's Artifacts card reads.

    Its own visibility check, not the fetch-by-key route's: a `GET` on the
    collection path is a different route from a `GET` on a key beneath it (and
    from the `DELETE` on the same path, which is owner-only), so "the member
    can read one file" says nothing about whether they can see the list.
    """
    owner = _new_user(db)
    member = _new_user(db)
    pool_id = _pool(db, owner)
    _add_member(db, pool_id, member)
    job_id = _seed_pool_job(client, owner, pool_id, db, "pool-job-artifact-list")
    transport.seed_artifact(f"jobs/{job_id}/shard-000/model.bin", b"weights")
    transport.seed_artifact(f"jobs/{job_id}/reduced/best.json", b"{}")
    # Another job's files, seeded on the same fake coordinator. The listing is
    # asked per job, but the prefix filter is this API's, and a leak here
    # would put a teammate's key in a stranger's card.
    transport.seed_artifact("jobs/some-other-job/shard-000/model.bin", b"nope")

    r = client.get(f"/v1alpha1/jobs/{job_id}/artifacts", headers=_auth(member))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["artifacts"] == [
        {"key": "reduced/best.json", "size_bytes": 2},
        {"key": "shard-000/model.bin", "size_bytes": 7},
    ]
    # No OSS in this file's settings, so the card is told the plain truth:
    # every one of those keys is served from the coordinator.
    assert body["storage"] == "coordinator"
    assert body["mirrored_at"] is None


def test_a_federated_runs_artifact_listing_costs_no_coordinator_round_trip(
    client, db, transport
):
    """A federated run's parent id names no coordinator job — the coordinator
    sees N unrelated round jobs — so there is nothing under
    ``jobs/{parent}/`` to list and nothing to ask. Empty, from local rows,
    exactly as ``GET /v1alpha1/jobs/{id}`` answers a federated job. Fanning
    out over the rounds would return keys under a DIFFERENT job's prefix,
    which would not compose with the fetch-by-key route: every download link
    the console built from them would 404.
    """
    owner = _new_user(db)
    job_id = _seed_federated_job(db, owner, "fed-run")
    before = len(transport.requests)

    r = client.get(f"/v1alpha1/jobs/{job_id}/artifacts", headers=_auth(owner))

    assert r.status_code == 200, r.text
    assert r.json() == {
        "artifacts": [], "storage": "coordinator", "mirrored_at": None,
    }
    assert len(transport.requests) == before


# ---------------------------------------------------------------------------
# 2. a non-member gets 404, indistinguishably, everywhere
# ---------------------------------------------------------------------------


def test_a_pool_member_can_read_a_task_checkpoint(client, db, transport):
    """The console's whole fault-tolerance panel rests on this one read.

    It exists as a browser route at all because the agent's
    `.../checkpoints/latest` is machine-credentialed and answers 401 to a
    browser JWT — so before this, the panel could show nothing."""
    owner = _new_user(db)
    member = _new_user(db)
    pool_id = _pool(db, owner)
    _add_member(db, pool_id, member)
    job_id = _seed_pool_job(client, owner, pool_id, db, "ckpt-job")

    r = client.get(f"/v1alpha1/jobs/{job_id}/tasks/task-0/checkpoint",
                   headers=_auth(member))
    assert r.status_code == 200, r.text
    assert r.json()["step"] == 42
    assert transport.requests[-1].url.path == (
        f"/v1alpha1/jobs/{job_id}/tasks/task-0/checkpoints/latest"
    ), "the browser's singular path must be rewritten to the coordinator's"


def test_no_checkpoint_yet_is_a_404_not_an_error(client, db, transport):
    """404 means "no valid checkpoint", and the console distinguishes it
    from a failed read. Collapsing the two would make a task that has not
    checkpointed yet look identical to one whose state we could not fetch —
    and the honest empty state depends on telling them apart."""
    owner = _new_user(db)
    job_id = _seed_pool_job(client, owner, _pool(db, owner), db, "fresh-job")
    transport.no_checkpoint_jobs.add(job_id)

    r = client.get(f"/v1alpha1/jobs/{job_id}/tasks/task-0/checkpoint",
                   headers=_auth(owner))
    assert r.status_code == 404
    assert "no valid checkpoint" in r.text


def test_non_member_gets_404_on_every_read_and_on_cancel(client, db, transport):
    owner = _new_user(db)
    stranger = _new_user(db)
    pool_id = _pool(db, owner)
    job_id = _seed_pool_job(client, owner, pool_id, db, "private-pool-job")
    dbmod.insert_job_round(
        db, job_id=job_id, round_index=0, participants=1, mean_loss=0.1,
        contributors=[], coordinator_job_id=None,
    )
    transport.seed_artifact(f"jobs/{job_id}/secret.bin", b"not for strangers")

    stranger_auth = _auth(stranger)
    for path in (
        f"/v1alpha1/jobs/{job_id}",
        f"/v1alpha1/jobs/{job_id}/rounds",
        f"/v1alpha1/jobs/{job_id}/events",
        f"/v1alpha1/jobs/{job_id}/tasks",
        f"/v1alpha1/jobs/{job_id}/contributions",
        f"/v1alpha1/jobs/{job_id}/artifacts/secret.bin",
        # The listing is its own route with its own check. Reaching it would
        # not hand over a byte, and would still enumerate the filenames of
        # somebody else's job — which is enough to learn what they are
        # training.
        f"/v1alpha1/jobs/{job_id}/artifacts",
        # The checkpoint read is the newest member of this list and the one
        # most worth pinning: the coordinator's own `/latest` takes NO
        # credential and its manifest carries artifact keys, so this route's
        # viewer check is the only thing between one account's checkpoints
        # and another's.
        f"/v1alpha1/jobs/{job_id}/tasks/task-0/checkpoint",
    ):
        r = client.get(path, headers=stranger_auth)
        assert r.status_code == 404, f"{path} -> {r.status_code}: {r.text}"
        assert "secret" not in r.text and "private-pool-job" not in r.text

    before = len(transport.requests)
    r = client.post(f"/v1alpha1/jobs/{job_id}/cancel", headers=stranger_auth)
    assert r.status_code == 404
    assert len(transport.requests) == before, (
        "cancelling (or reading) an unreachable job must never contact the "
        "coordinator"
    )


# ---------------------------------------------------------------------------
# 3. the jobs list: one third source, deduplicated
# ---------------------------------------------------------------------------


def test_jobs_list_shows_a_pool_job_exactly_once_for_the_owner(client, db):
    """The owner is a member of their own pool (``create_pool`` seats them),
    so their pool job's id is in *both* ``list_job_ids_for_owner`` and
    ``list_pool_job_ids_for_member``. The union must still show it once."""
    owner = _new_user(db)
    pool_id = _pool(db, owner)
    job_id = _seed_pool_job(client, owner, pool_id, db, "owners-pool-job")

    r = client.get("/v1alpha1/jobs", headers=_auth(owner))
    assert r.status_code == 200, r.text
    ids = [j.get("job_id") for j in r.json() if isinstance(j, dict)]
    assert ids.count(job_id) == 1


def test_jobs_list_includes_a_pool_mates_job_for_the_member_only(client, db):
    owner = _new_user(db)
    member = _new_user(db)
    stranger = _new_user(db)
    pool_id = _pool(db, owner)
    _add_member(db, pool_id, member)

    pool_job_id = _seed_pool_job(client, owner, pool_id, db, "shared-pool-job")
    # The owner's job outside the pool must not leak to the member.
    private_job_id = _submit(client, _jwt(owner), "owners-private-job")["job_id"]
    # Nor may an unrelated user's job appear.
    stranger_job_id = _submit(client, _jwt(stranger), "strangers-job")["job_id"]

    r = client.get("/v1alpha1/jobs", headers=_auth(member))
    assert r.status_code == 200, r.text
    ids = [j.get("job_id") for j in r.json() if isinstance(j, dict)]
    assert ids.count(pool_job_id) == 1
    assert private_job_id not in ids
    assert stranger_job_id not in ids

    # And the stranger, in a different pool entirely (none), sees neither.
    r = client.get("/v1alpha1/jobs", headers=_auth(stranger))
    assert r.status_code == 200, r.text
    stranger_ids = [j.get("job_id") for j in r.json() if isinstance(j, dict)]
    assert pool_job_id not in stranger_ids
    assert private_job_id not in stranger_ids


# ---------------------------------------------------------------------------
# 4. the jobs list: federated runs, the flagship pool-visible workload
#
# Fix-loop pass: a federated run has no coordinator job id at all (one
# coordinator job per ROUND, never one for the parent), so its presence in
# GET /v1alpha1/jobs comes entirely from `list_federated_jobs_for_viewer` —
# the `owned`/`pool_ids`/`seen` union above never sees it (federated ids are
# filtered out of both, since they can never match the coordinator's list
# anyway). Before this pass that federated listing function was
# `..._for_owner`, so a pool member could open a teammate's federated run
# directly by id (`fetch_job_for_viewer` already admitted them) but could
# never discover it here — exactly backwards for federated learning, the
# design doc's flagship workload.
# ---------------------------------------------------------------------------


def test_jobs_list_lets_a_pool_member_discover_a_teammates_federated_job(client, db):
    owner = _new_user(db)
    member = _new_user(db)
    stranger = _new_user(db)
    pool_id = _pool(db, owner)
    _add_member(db, pool_id, member)
    job_id = _seed_federated_job(db, owner, "shared-federated-run", pool_id=pool_id)

    # The member discovers it, exactly once.
    r = client.get("/v1alpha1/jobs", headers=_auth(member))
    assert r.status_code == 200, r.text
    member_ids = [j.get("job_id") for j in r.json() if isinstance(j, dict)]
    assert member_ids.count(job_id) == 1

    # The owner still sees it exactly once — the widened query is one flat
    # `WHERE owner_id = %s OR EXISTS(...)` select, not a second source that
    # could double it the way the coordinator-sourced half's `owned` and
    # `pool_ids` sets could without their `seen` union.
    r = client.get("/v1alpha1/jobs", headers=_auth(owner))
    assert r.status_code == 200, r.text
    owner_ids = [j.get("job_id") for j in r.json() if isinstance(j, dict)]
    assert owner_ids.count(job_id) == 1

    # A stranger in no pool never sees it.
    r = client.get("/v1alpha1/jobs", headers=_auth(stranger))
    assert r.status_code == 200, r.text
    stranger_ids = [j.get("job_id") for j in r.json() if isinstance(j, dict)]
    assert job_id not in stranger_ids


def test_a_federated_job_with_no_pool_stays_owner_only_in_the_list(client, db):
    """Every pre-pools federated run (and every federated run submitted
    with no ``pool``) keeps exactly its old, owner-only visibility here —
    the null-``pool_id`` NULL-semantics note ``fetch_job_for_viewer``
    documents applies just as much to this query. ``pool_mate`` is
    deliberately a real member of a real pool ``owner`` belongs to, to
    prove the null ``pool_id`` on *this* job — not merely "no shared pool
    exists at all" — is what keeps them out."""
    owner = _new_user(db)
    pool_mate = _new_user(db)
    pool_id = _pool(db, owner)
    _add_member(db, pool_id, pool_mate)
    job_id = _seed_federated_job(db, owner, "solo-federated-run", pool_id=None)

    r = client.get("/v1alpha1/jobs", headers=_auth(owner))
    assert r.status_code == 200, r.text
    owner_ids = [j.get("job_id") for j in r.json() if isinstance(j, dict)]
    assert job_id in owner_ids

    r = client.get("/v1alpha1/jobs", headers=_auth(pool_mate))
    assert r.status_code == 200, r.text
    pool_mate_ids = [j.get("job_id") for j in r.json() if isinstance(j, dict)]
    assert job_id not in pool_mate_ids


# ---------------------------------------------------------------------------
# 5. job rows carry pool_id and submitter (Task 1, workspace console)
# ---------------------------------------------------------------------------


def _set_display_name(db, user_id: str, name: str) -> None:
    """This file's ``_new_user`` creates a profile with no display name, and
    the jobs list now renders one. No existing helper sets it, so set it
    directly — the same shape as ``_seed_pool_job``'s own stamping update."""
    with db.cursor() as cur:
        cur.execute(
            "update public.profiles set display_name = %s where id = %s",
            (name, user_id),
        )


def test_job_list_rows_carry_pool_id_and_submitter(client, db):
    """The console scopes jobs by workspace, which it can only do if the row
    says which workspace it is in. A pre-pools job reports None rather than
    omitting the field — "no workspace" is a real answer, not a missing one."""
    owner = _new_user(db)
    _set_display_name(db, owner, "Ada")
    pool_id = _pool(db, owner)

    in_pool = _seed_pool_job(client, owner, pool_id, db, "in-pool")
    orphan = _submit(client, _jwt(owner), "orphan")["job_id"]

    rows = {j["job_id"]: j for j in
            client.get("/v1alpha1/jobs", headers=_auth(owner)).json()}

    assert rows[in_pool]["pool_id"] == str(pool_id)
    assert rows[in_pool]["submitted_by"] == "Ada"
    assert rows[orphan]["pool_id"] is None
    assert rows[orphan]["submitted_by"] == "Ada"


def test_teammates_job_row_names_its_submitter(client, db):
    """The attribution that makes a workspace read as shared: a member seeing
    a teammate's job must see whose it is, not merely that it exists."""
    owner = _new_user(db)
    member = _new_user(db)
    _set_display_name(db, owner, "Ada")
    pool_id = _pool(db, owner)
    _add_member(db, pool_id, member)

    job_id = _seed_pool_job(client, owner, pool_id, db, "shared")

    rows = [j for j in client.get("/v1alpha1/jobs", headers=_auth(member)).json()
            if j.get("job_id") == job_id]
    assert len(rows) == 1
    assert rows[0]["submitted_by"] == "Ada"
    assert rows[0]["pool_id"] == str(pool_id)


def test_federated_row_carries_pool_id_and_submitter(client, db):
    """A federated parent never appears in the coordinator's list, so its
    label comes from the other branch of the route entirely. Stamping only
    the coordinator-sourced rows would leave every federated run unlabelled."""
    owner = _new_user(db)
    _set_display_name(db, owner, "Ada")
    pool_id = _pool(db, owner)
    job_id = _seed_federated_job(db, owner, "fed-run", pool_id=pool_id)

    rows = [j for j in client.get("/v1alpha1/jobs", headers=_auth(owner)).json()
            if j.get("job_id") == job_id]
    assert len(rows) == 1
    assert rows[0]["pool_id"] == str(pool_id)
    assert rows[0]["submitted_by"] == "Ada"


def test_job_detail_carries_pool_id_and_submitter(client, db):
    """The job detail page renders its own breadcrumb and may have been
    deep-linked, so it must name its workspace without consulting a list it
    never loaded."""
    owner = _new_user(db)
    _set_display_name(db, owner, "Ada")
    pool_id = _pool(db, owner)
    job_id = _seed_pool_job(client, owner, pool_id, db, "detail")

    body = client.get(f"/v1alpha1/jobs/{job_id}", headers=_auth(owner)).json()
    assert body["pool_id"] == str(pool_id)
    assert body["submitted_by"] == "Ada"
