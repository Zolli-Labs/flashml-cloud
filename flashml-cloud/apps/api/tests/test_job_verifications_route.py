"""``GET /v1alpha1/jobs/{job_id}/verifications`` — the read half of D-4.

Design: ``docs/superpowers/specs/2026-08-12-observability-and-verification-gaps.md``,
§3 D-4. ``db.record_verification`` (see ``test_verification.py``) has written
a per-slice ``pass``/``flag``/``unknown`` verdict on a task since 0006, but
until this route existed nothing in the product could read it back — the
layer's own docstring justification, "so an operator can look", was
satisfied by nothing.

This file is the route-level proof of the two rules that matter most here,
both stated in the design doc and in ``record_verification``'s own
docstring:

* **These are observations, never gates.** Nothing about this route implies
  otherwise — it is a plain read, scoped exactly like its sibling
  ``/contributions``.
* **``unknown`` survives verbatim.** It must never arrive at the caller as
  ``pass``, and it must never be silently dropped — a task with a real
  ``unknown`` row is different from a task with no rows at all, and this
  route must let a reader tell the two apart.

Self-contained the same way ``test_my_contributions.py`` is: this route
answers entirely from the ledger and never talks to the coordinator, so
there is no need for ``test_pool_visibility.py``'s ``FakeCoordinatorTransport``
— a ``SilentTransport`` that raises if contacted, plus jobs seeded directly
via ``dbmod.insert_job``, is enough and proves the route never round-trips
to the coordinator for something this local.
"""
from __future__ import annotations

import time
import uuid

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api import db as dbmod
from flashml_cloud_api.app import create_cloud_app
from flashml_cloud_api.settings import Settings

JWT_SECRET = "test-jwt-secret-long-enough-for-hs256-abcdef"


@pytest.fixture(scope="module")
def settings(postgres_dsn) -> Settings:
    return Settings(
        supabase_url="https://yualksqjjvlfscbbsygq.supabase.co",
        supabase_jwt_secret=JWT_SECRET,
        supabase_service_key="service-key-not-used-here",
        coordinator_url="http://coordinator.internal:8100",
        coordinator_operator_token="op-secret-do-not-leak-3f9c1b",
        require_auth=True,
        database_url=postgres_dsn,
        console_url="https://console.example",
    )


class SilentTransport(httpx.AsyncBaseTransport):
    """This route is answered entirely from ``public.verifications`` and
    ``public.jobs``. A coordinator round trip here would mean the route
    stopped trusting its own ledger."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"the verifications route contacted {request.url}")


@pytest.fixture
def client(settings, postgres_dsn):
    def connect() -> psycopg.Connection:
        conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        return conn

    app = create_cloud_app(settings, connect=connect, transport=SilentTransport())
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def _new_user(db) -> str:
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (%s)", (user_id,))
        cur.execute(
            "insert into public.profiles (id, admitted_at) values (%s, now())",
            (user_id,),
        )
    return user_id


def _jwt(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": time.time() + 3600},
        JWT_SECRET,
        algorithm="HS256",
    )


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_jwt(user_id)}"}


def _node_id(tag: str) -> str:
    return f"verif-route-{tag}-{uuid.uuid4().hex[:10]}"


def _machine(db, owner: str) -> str:
    return dbmod.insert_machine(
        db, owner_id=owner, node_id=_node_id("m"), name="laptop", platform="linux",
    )


def _job_id() -> str:
    return f"cjob-{uuid.uuid4().hex[:12]}"


def _seed_job(db, owner_id: str, job_id: str) -> None:
    """A real ``jobs`` row, written the same production path
    ``list_job_contributions``'s own route test uses to give
    ``fetch_job_for_viewer`` something to authorize against — direct
    ``dbmod.insert_job``, no coordinator round trip needed for a route
    that never calls one."""
    dbmod.insert_job(
        db, job_id=job_id, owner_id=owner_id, name="job-under-test",
        source=None, spec=None, status="RUNNING",
    )


def _get(client, user_id: str, job_id: str):
    return client.get(
        f"/v1alpha1/jobs/{job_id}/verifications", headers=_auth(user_id)
    )


# ---------------------------------------------------------------------------
# 1. the shape: verbatim verdicts, unknown preserved, empty is empty
# ---------------------------------------------------------------------------


def test_a_job_with_no_verifications_returns_an_empty_list(client, db):
    """Never a fabricated `pass` for a task nothing has ever checked —
    absent stays absent, and the console must be able to render that."""
    owner = _new_user(db)
    job_id = _job_id()
    _seed_job(db, owner, job_id)

    r = _get(client, owner, job_id)
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_a_flag_verdict_survives_the_route_verbatim(client, db):
    """A `flag` here withheld nothing and refused nobody — the route must
    hand it back exactly as recorded, not softened or renamed."""
    owner = _new_user(db)
    machine = _machine(db, owner)
    job_id = _job_id()
    _seed_job(db, owner, job_id)
    dbmod.record_verification(
        db, machine_id=machine, job_id=job_id, task_id="task-000",
        slice_name="timing", verdict="flag",
        detail={"observed_s": 0.3, "peer_median_s": 9.1},
    )

    body = _get(client, owner, job_id).json()
    assert len(body) == 1
    assert body[0]["task_id"] == "task-000"
    assert body[0]["slice"] == "timing"
    assert body[0]["verdict"] == "flag"
    assert body[0]["detail"] == {"observed_s": 0.3, "peer_median_s": 9.1}
    assert body[0]["machine_id"] == str(machine)


def test_an_unknown_verdict_survives_the_route_verbatim(client, db):
    """The property this whole layer exists to protect, proved at the
    route: `unknown` must not arrive at the caller as `pass`, and must not
    be dropped either — it is a real finding meaning "could not tell"."""
    owner = _new_user(db)
    machine = _machine(db, owner)
    job_id = _job_id()
    _seed_job(db, owner, job_id)
    dbmod.record_verification(
        db, machine_id=machine, job_id=job_id, task_id="task-001",
        slice_name="timing", verdict="unknown",
        detail={"reason": "too_few_peers", "peers": 1},
    )

    body = _get(client, owner, job_id).json()
    assert len(body) == 1
    assert body[0]["verdict"] == "unknown"
    assert body[0]["verdict"] != "pass"


def test_a_job_with_flag_and_unknown_on_different_tasks_shows_both(client, db):
    """The scenario the design doc frames the whole route around: a real
    mixed job, where one task's finding must not shadow or overwrite the
    other's."""
    owner = _new_user(db)
    machine = _machine(db, owner)
    job_id = _job_id()
    _seed_job(db, owner, job_id)
    dbmod.record_verification(
        db, machine_id=machine, job_id=job_id, task_id="task-000",
        slice_name="timing", verdict="flag", detail={"peers": 4},
    )
    dbmod.record_verification(
        db, machine_id=machine, job_id=job_id, task_id="task-001",
        slice_name="timing", verdict="unknown", detail={"peers": 0},
    )

    body = _get(client, owner, job_id).json()
    by_task = {row["task_id"]: row["verdict"] for row in body}
    assert by_task == {"task-000": "flag", "task-001": "unknown"}


def test_a_null_machine_id_reads_as_none_not_a_placeholder(client, db):
    """A redundancy finding names no machine (§8.5 of the verification
    design) — the route must not invent one to fill the field."""
    owner = _new_user(db)
    job_id = _job_id()
    _seed_job(db, owner, job_id)
    dbmod.record_verification(
        db, machine_id=None, job_id=job_id, task_id="shard-003",
        slice_name="redundancy", verdict="flag",
        detail={"machines": ["a", "b"]},
    )

    body = _get(client, owner, job_id).json()
    assert body[0]["machine_id"] is None


def test_multiple_slices_on_one_task_all_survive(client, db):
    owner = _new_user(db)
    machine = _machine(db, owner)
    job_id = _job_id()
    _seed_job(db, owner, job_id)
    for slice_name, verdict in (("timing", "pass"), ("evidence", "flag")):
        dbmod.record_verification(
            db, machine_id=machine, job_id=job_id, task_id="task-000",
            slice_name=slice_name, verdict=verdict, detail={},
        )

    body = _get(client, owner, job_id).json()
    assert {(row["slice"], row["verdict"]) for row in body} == {
        ("timing", "pass"), ("evidence", "flag"),
    }


# ---------------------------------------------------------------------------
# 2. visibility: same doctrine as every sibling read route
# ---------------------------------------------------------------------------


def test_a_stranger_gets_404_not_the_verdicts(client, db):
    """404, indistinguishably from a job that does not exist — the same
    doctrine `fetch_job_for_viewer` documents for every sibling route, and
    the same one `test_pool_visibility.py` pins for `/contributions`."""
    owner = _new_user(db)
    stranger = _new_user(db)
    job_id = _job_id()
    _seed_job(db, owner, job_id)
    dbmod.record_verification(
        db, machine_id=_machine(db, owner), job_id=job_id, task_id="task-000",
        slice_name="timing", verdict="flag", detail={"secret": "no"},
    )

    r = _get(client, stranger, job_id)
    assert r.status_code == 404
    assert "secret" not in r.text


def test_an_unknown_job_id_is_also_404(client, db):
    r = _get(client, _new_user(db), _job_id())
    assert r.status_code == 404


def test_a_pool_mate_reads_the_same_verdicts_as_the_owner(client, db):
    """Widened to the pool exactly like `/contributions` — the owner, or
    any member of the job's pool, may see what was found."""
    owner = _new_user(db)
    member = _new_user(db)
    pool_id = dbmod.create_pool(db, name="Team", owner_id=owner)["id"]
    with db.cursor() as cur:
        cur.execute(
            "insert into public.pool_members (pool_id, user_id) values (%s, %s)",
            (pool_id, member),
        )
    job_id = _job_id()
    _seed_job(db, owner, job_id)
    with db.cursor() as cur:
        cur.execute(
            "update public.jobs set pool_id = %s where id = %s", (pool_id, job_id)
        )
    dbmod.record_verification(
        db, machine_id=_machine(db, owner), job_id=job_id, task_id="task-000",
        slice_name="timing", verdict="flag", detail={},
    )

    owner_body = _get(client, owner, job_id).json()
    member_body = _get(client, member, job_id).json()
    assert owner_body == member_body
    assert len(owner_body) == 1


def test_the_route_requires_a_jwt(client, db):
    job_id = _job_id()
    _seed_job(db, _new_user(db), job_id)
    r = client.get(f"/v1alpha1/jobs/{job_id}/verifications")
    assert r.status_code == 401
