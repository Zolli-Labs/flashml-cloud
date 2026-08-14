"""A job picks its coordinator, and everything about that job follows it.

`test_coordinator_venues.py` pins the CLIENT: a venue selects a base URL and
an operator token together, unconfigured refuses rather than falling back, and
the default did not move. This file pins the LAYER ABOVE it — which venue each
request actually asks for.

Four properties, and they are what every test here is an instance of:

  1. **The default did not move.** A submission that says nothing about a
     coordinator produces byte-identical traffic to yesterday's, records a
     venue that reads back as `render`, and never opens a socket to anywhere
     else.
  2. **The JOB is pinned, never the request.** `jobs.coordinator` is written
     once at submission and every later call — status, events, tasks, cancel,
     artifacts, the agent's own lease and checkpoint hops — is resolved from
     that row. A job that changed venue mid-flight would not error: its leases
     live inside whichever coordinator issued them, so the other one has never
     heard of it, the lease ages out in the original's sweeper and the task is
     silently requeued. Nothing downstream can tell. So the assertions are on
     the HOST of every outbound request, not on the status the caller saw.
  3. **A venue this deployment cannot address is refused at the edge**, with a
     4xx that says which problem it is, and never by quietly running the job
     somewhere else and reporting the venue that was asked for.
  4. **An idle coordinator sees no traffic at all** — no claim, and no
     heartbeat. FC's whole value is a measured duty cycle. A fleet polling it
     every few seconds while it holds no work would inflate its invocation
     count until that number said nothing — so nothing this API sends on its
     own initiative reaches a non-default venue unless that venue has a
     non-terminal job. Heartbeats are the sharper edge of this: a claim stops
     when a machine is busy, a heartbeat never stops at all.

The coordinator here is one fake transport serving TWO hosts, which is what
makes the host assertion meaningful: a request that went to the wrong venue
still succeeds, so only looking at where it went can catch it. The database is
the real, freshly migrated ephemeral Postgres from `conftest.py`.

No skips: a test that asserts a request was NOT sent is worthless if it
silently does not run.
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
from flashml_cloud_api import enrolment
from flashml_cloud_api.app import (
    COORDINATOR_VENUES,
    DEFAULT_COORDINATOR_VENUE,
    create_cloud_app,
)
from flashml_cloud_api.settings import Settings

from test_federated import FEDERATED_TRAIN_PY, FEDERATED_YAML
from test_jobs_from_repo import (  # noqa: F401 - fixtures and builders
    CLEAN_REPO,
    JWT_SECRET,
    OPERATOR_TOKEN,
    RecordingFetch,
    _jwt,
    _new_user,
    db,
    make_tarball,
)

RENDER_URL = "http://coordinator.internal:8100"
FC_URL = "https://coordinator-fc.fcapp.run"
FC_HOST = "coordinator-fc.fcapp.run"
FC_TOKEN = "fc-operator-token-not-the-render-one"

RUN_MARKER = uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# a fake coordinator that is really two coordinators
# ---------------------------------------------------------------------------


class TwoVenueTransport(httpx.AsyncBaseTransport):
    """One transport, two independent control planes keyed by host.

    Separate job stores per venue on purpose: the real deployments have
    separate DATABASES, so a job submitted to one is a 404 at the other. A
    shared store would make every misrouted request succeed and every test
    here vacuous.

    `venues_hit` is the assertion surface. It is a list, not a set, because
    "FC was asked once" and "FC was asked on every poll" are different facts
    and the duty-cycle property is about the second one.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._jobs: dict[str, dict[str, dict]] = {"render": {}, "fc": {}}
        self.artifacts: dict[str, dict[str, bytes]] = {"render": {}, "fc": {}}
        #: Venues whose claim answers 200 with a lease. Everything else
        #: answers 204 — "nothing claimable right now", the ordinary case.
        self.claimable: set[str] = set()
        #: Nodes each venue has been told about, node_id -> registration body.
        #: Per venue, because a coordinator's registry is its own process
        #: memory: registering with one tells the other nothing.
        self.registered: dict[str, dict[str, dict]] = {"render": {}, "fc": {}}
        #: Venues that enforce the registry on `claim`, answering the real
        #: coordinator's `403 "unregistered node — register first"`. Empty by
        #: default so every test written before this behaves exactly as it did.
        self.requires_registration: set[str] = set()
        #: Venues whose `register` answers 200 but records NOTHING — a venue
        #: rebuilt between the registration and the retry, which is how a
        #: second 403 happens in the field.
        self.register_is_amnesiac: set[str] = set()
        #: Venues whose `register` fails outright, and with what status.
        self.register_status: dict[str, int] = {}
        #: Venues whose `claim` answers 403 with a detail of the test's
        #: choosing — for the refusals that registering would NOT fix.
        self.refuse_claim_with: dict[str, str] = {}
        #: Venues whose `heartbeat` answers 404 with a detail of the test's
        #: choosing — a 404 that is NOT a registry miss (a mistyped path, a
        #: coordinator behind a proxy that answers its own 404s).
        self.refuse_heartbeat_with: dict[str, str] = {}
        #: Venues that are simply not answering: every request to them raises
        #: at the socket, the way a cold, wedged or deleted FC function does.
        self.dead: set[str] = set()
        self._prefix = uuid.uuid4().hex[:10]
        self._next_id = 1

    # -- assertion helpers --------------------------------------------------

    @staticmethod
    def _venue_of(request: httpx.Request) -> str:
        return "fc" if request.url.host == FC_HOST else "render"

    @property
    def venues_hit(self) -> list[str]:
        return [self._venue_of(r) for r in self.requests]

    def paths_on(self, venue: str) -> list[str]:
        return [
            r.url.path for r in self.requests if self._venue_of(r) == venue
        ]

    def reset(self) -> None:
        self.requests.clear()

    def seed_artifact(self, venue: str, key: str, content: bytes) -> None:
        self.artifacts[venue][key] = content

    # -- the coordinator ----------------------------------------------------

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        venue = self._venue_of(request)
        if venue in self.dead:
            # Recorded first, deliberately: "FC was asked and did not answer"
            # is a different fact from "FC was never asked", and the mirror
            # tests are about which of the two happened.
            raise httpx.ConnectError("connection refused", request=request)
        jobs = self._jobs[venue]
        artifacts = self.artifacts[venue]
        method, path = request.method, request.url.path

        if method == "PUT" and path.startswith("/v1alpha1/artifacts/"):
            key = path[len("/v1alpha1/artifacts/"):]
            artifacts[key] = request.content
            return httpx.Response(200, json={"uri": f"artifact://{key}"})

        if method == "GET" and path.startswith("/v1alpha1/artifacts/"):
            key = path[len("/v1alpha1/artifacts/"):]
            content = artifacts.get(key)
            if content is None:
                return httpx.Response(404, json={"detail": "no such artifact"})
            return httpx.Response(200, content=content)

        if method == "POST" and path == "/v1alpha1/jobs":
            body = json.loads(request.content or b"{}")
            job_id = f"job-{self._prefix}-{self._next_id:04d}"
            self._next_id += 1
            record = {"job_id": job_id, "spec": body, "state": "RUNNING"}
            jobs[job_id] = record
            return httpx.Response(201, json=record)

        if method == "GET" and path == "/v1alpha1/jobs":
            return httpx.Response(200, json=list(jobs.values()))

        if method == "POST" and path == "/v1alpha1/nodes/register":
            status = self.register_status.get(venue, 200)
            if status >= 400:
                return httpx.Response(status, json={"detail": "no"})
            reg = json.loads(request.content or b"{}")
            if venue not in self.register_is_amnesiac:
                self.registered[venue][reg.get("node_id")] = reg
            return httpx.Response(
                200, json={"node_id": reg.get("node_id"), "status": "registered"}
            )

        if (
            method == "POST"
            and path.startswith("/v1alpha1/nodes/")
            and path.endswith("/heartbeat")
        ):
            detail = self.refuse_heartbeat_with.get(venue)
            if detail is not None:
                return httpx.Response(404, json={"detail": detail})
            node = path.split("/")[-2]
            if (
                venue in self.requires_registration
                and node not in self.registered[venue]
            ):
                # Verbatim from `service/modea.py`, which answers a heartbeat
                # from an unknown node 404 where a claim answers 403. Same
                # registry, same repair, different status — a paraphrase here
                # would test nothing.
                return httpx.Response(
                    404, json={"detail": f"unknown node {node} — register first"}
                )
            return httpx.Response(200, json={"status": "ok"})

        if method == "POST" and path == "/v1alpha1/leases/claim":
            detail = self.refuse_claim_with.get(venue)
            if detail is not None:
                return httpx.Response(403, json={"detail": detail})
            claimed_by = json.loads(request.content or b"{}").get("node_id")
            if (
                venue in self.requires_registration
                and claimed_by not in self.registered[venue]
            ):
                # Verbatim from `service/modea.py`. The API matches on these
                # words, so a paraphrase here would test nothing.
                return httpx.Response(
                    403, json={"detail": "unregistered node — register first"}
                )
            if venue not in self.claimable:
                return httpx.Response(204)
            return httpx.Response(200, json={
                "lease_id": f"lease-{venue}-{uuid.uuid4().hex[:8]}",
                "job_id": next(iter(jobs), "unknown-job"),
                "task_id": "task-000",
                "deadline": None,
            })

        if path.startswith("/v1alpha1/attempts/"):
            return httpx.Response(200, json={"accepted": True})

        if method == "GET" and path.endswith("/events"):
            return httpx.Response(200, json=[])

        if method == "GET" and path.endswith("/tasks"):
            return httpx.Response(200, json=[])

        if method == "GET" and path.endswith("/result"):
            return httpx.Response(200, json={"result": None})

        if method == "POST" and path.endswith("/cancel"):
            job_id = path.split("/")[-2]
            record = jobs.get(job_id)
            if record is None:
                return httpx.Response(404, json={"detail": "no such job"})
            jobs[job_id] = dict(record, state="CANCELLED")
            return httpx.Response(200, json=jobs[job_id])

        if method == "GET" and path.endswith("/artifacts"):
            job_id = path.split("/")[-2]
            prefix = f"jobs/{job_id}/"
            return httpx.Response(200, json=[
                {"uri": f"artifact://{k}", "key": k, "size_bytes": len(v)}
                for k, v in sorted(artifacts.items()) if k.startswith(prefix)
            ])

        if method == "GET" and path.count("/") == 3 and "/jobs/" in path:
            job_id = path.rsplit("/", 1)[-1]
            record = jobs.get(job_id)
            if record is None:
                return httpx.Response(404, json={"detail": "no such job"})
            return httpx.Response(200, json=record)

        if "checkpoints" in path:
            return httpx.Response(404, json={"detail": "no checkpoint"})

        return httpx.Response(
            404, json={"detail": f"unhandled: {venue} {method} {path}"}
        )


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _settings(postgres_dsn, *, fc: bool) -> Settings:
    return Settings(
        supabase_url="https://yualksqjjvlfscbbsygq.supabase.co",
        supabase_jwt_secret=JWT_SECRET,
        supabase_service_key="service-key-not-used-here",
        coordinator_url=RENDER_URL,
        coordinator_operator_token=OPERATOR_TOKEN,
        coordinator_url_fc=FC_URL if fc else "",
        coordinator_operator_token_fc=FC_TOKEN if fc else "",
        require_auth=True,
        database_url=postgres_dsn,
        console_url="https://console.example",
    )


@pytest.fixture
def transport() -> TwoVenueTransport:
    return TwoVenueTransport()


@pytest.fixture
def make_client(postgres_dsn, transport):
    """A client whose deployment has both venues configured, or only the
    default. `fc=False` is what every deployment looks like today, and it is
    what the refusal tests need."""
    clients: list[TestClient] = []

    def build(*, fc: bool = True, files: dict[str, str] | None = None):
        def connect() -> psycopg.Connection:
            conn = psycopg.connect(
                postgres_dsn, row_factory=dict_row, connect_timeout=5
            )
            conn.autocommit = True
            return conn

        app = create_cloud_app(
            _settings(postgres_dsn, fc=fc),
            connect=connect,
            transport=transport,
            fetch_repo=RecordingFetch(make_tarball(files or CLEAN_REPO)),
        )
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def _submit(client, token: str, **body):
    payload = {"repo": "https://github.com/acme/trainer", "ref": "main"}
    payload.update(body)
    return client.post(
        "/v1alpha1/jobs/from-repo",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


def _job_row(db, job_id: str) -> dict:
    with db.cursor() as cur:
        cur.execute("select * from public.jobs where id = %s", (job_id,))
        row = cur.fetchone()
    assert row is not None, f"no jobs row for {job_id}"
    return row


def _quiesce_non_default_jobs(db) -> None:
    """Retire every non-default-venue job this session has left running.

    `active_job_venues` is DEPLOYMENT-WIDE, and correctly so: a claim is a
    fleet-wide question, not an account-scoped one. That makes the claim tests
    below sensitive to jobs other tests in this module submitted, so each of
    them starts from a quiet fleet and creates whatever work it needs itself.

    Scoped to non-default venues: this is the only module that writes them, so
    nothing else in the session can be disturbed, and the default venue's rows
    (which every other module creates) are left completely alone.
    """
    with db.cursor() as cur:
        cur.execute(
            "update public.jobs"
            "   set status = 'SUCCEEDED', finished_at = now()"
            " where coordinator is not null and coordinator <> %s"
            "   and finished_at is null",
            (DEFAULT_COORDINATOR_VENUE,),
        )


def _enrol(db, owner_id: str) -> tuple[str, str]:
    node_id = f"venue-{RUN_MARKER}-{uuid.uuid4().hex[:8]}"
    started = enrolment.start_device_code(db, node_id, "host-" + node_id, "linux")
    machine_id = enrolment.approve_device_code(db, started["user_code"], owner_id)
    token = enrolment.redeem_device_code(db, started["device_code"])
    assert token is not None
    return str(machine_id), token


def _node_id_of(db, machine_id: str) -> str:
    """The node_id the token resolves to — the only one that can appear in an
    outbound path, since every agent route replaces what the agent said."""
    with db.cursor() as cur:
        cur.execute(
            "select node_id from public.machines where id = %s", (machine_id,)
        )
        row = cur.fetchone()
    assert row is not None
    return row["node_id"]


# ---------------------------------------------------------------------------
# 1. the default did not move
# ---------------------------------------------------------------------------


def test_a_submit_with_no_coordinator_field_touches_only_the_default_venue(
    make_client, db, transport
):
    """The single most important test in this file. Every client in existence
    sends no `coordinator`, and every one of them must produce exactly the
    traffic it produced before this field existed — not "mostly", and not
    "plus one harmless extra call to a venue nobody asked for"."""
    client = make_client()
    owner = _new_user(db)

    r = _submit(client, _jwt(owner))

    assert r.status_code == 201, r.text
    assert set(transport.venues_hit) == {"render"}
    # Both halves of the submit — the artifact staging PUT and the job POST.
    assert any(p.startswith("/v1alpha1/artifacts/")
               for p in transport.paths_on("render"))
    assert "/v1alpha1/jobs" in transport.paths_on("render")


def test_an_absent_coordinator_field_records_and_reports_the_default(
    make_client, db
):
    """Absent means `render` — stated in the row and in the response, so no
    client ever has to know that NULL is a value with a meaning."""
    client = make_client()
    owner = _new_user(db)

    body = _submit(client, _jwt(owner)).json()

    assert body["coordinator"] == DEFAULT_COORDINATOR_VENUE
    assert _job_row(db, body["job_id"])["coordinator"] == DEFAULT_COORDINATOR_VENUE


def test_a_pre_0034_row_with_a_null_venue_reads_back_as_the_default(
    make_client, db, transport
):
    """Every job in both databases predates this column. NULL is not a gap to
    be filled in — it is a row that ran on Render because there was nowhere
    else — so it must read back as `render` and be FORWARDED to render."""
    client = make_client()
    owner = _new_user(db)
    job_id = _submit(client, _jwt(owner)).json()["job_id"]
    with db.cursor() as cur:
        cur.execute(
            "update public.jobs set coordinator = null where id = %s", (job_id,)
        )
    transport.reset()

    r = client.get(f"/v1alpha1/jobs/{job_id}",
                   headers={"Authorization": f"Bearer {_jwt(owner)}"})

    assert r.status_code == 200, r.text
    assert r.json()["coordinator"] == DEFAULT_COORDINATOR_VENUE
    assert set(transport.venues_hit) == {"render"}


# ---------------------------------------------------------------------------
# 2. an explicit venue is used, and recorded
# ---------------------------------------------------------------------------


def test_an_explicit_fc_submit_stages_and_submits_on_fc_and_nowhere_else(
    make_client, db, transport
):
    """Both calls, on one venue. Staging the code on one coordinator and
    submitting the job to the other is the failure that reads as a broken
    repo: the job is accepted, a task is leased, and the executor cannot
    fetch an `artifact://` URI its coordinator has never stored."""
    client = make_client()
    owner = _new_user(db)

    r = _submit(client, _jwt(owner), coordinator="fc")

    assert r.status_code == 201, r.text
    assert set(transport.venues_hit) == {"fc"}
    assert any(p.startswith("/v1alpha1/artifacts/")
               for p in transport.paths_on("fc"))
    assert "/v1alpha1/jobs" in transport.paths_on("fc")


def test_an_fc_submit_carries_the_fc_operator_token_and_not_renders(
    make_client, db, transport
):
    """The likely bug is not "wrong URL" — it is the FC URL carrying the
    Render operator token, which leaks the private control plane's credential
    to a public endpoint and fails in a way that looks like an FC
    misconfiguration."""
    client = make_client()
    _submit(client, _jwt(_new_user(db)), coordinator="fc")

    for request in transport.requests:
        assert request.headers["Authorization"] == f"Bearer {FC_TOKEN}"
        assert OPERATOR_TOKEN not in request.headers["Authorization"]


def test_an_fc_submit_is_recorded_on_the_row_and_reported_in_the_response(
    make_client, db
):
    client = make_client()
    owner = _new_user(db)

    body = _submit(client, _jwt(owner), coordinator="fc").json()

    assert body["coordinator"] == "fc"
    assert _job_row(db, body["job_id"])["coordinator"] == "fc"


def test_the_coordinator_field_is_never_forwarded_into_the_jobspec(
    make_client, db, transport
):
    """`POST /v1alpha1/jobs` forwards its body to the coordinator verbatim.
    The venue is addressed to THIS API and the coordinator has no idea what
    it is, so it must be consumed here rather than pushed into a spec."""
    client = make_client()
    owner = _new_user(db)

    r = client.post(
        "/v1alpha1/jobs",
        json={"apiVersion": "flashml.dev/v1alpha1", "kind": "Job",
              "metadata": {"name": "venue-not-in-spec"}, "spec": {},
              "coordinator": "fc"},
        headers={"Authorization": f"Bearer {_jwt(owner)}"},
    )

    assert r.status_code == 201, r.text
    submitted = [
        req for req in transport.requests
        if req.method == "POST" and req.url.path == "/v1alpha1/jobs"
    ]
    assert len(submitted) == 1
    assert "coordinator" not in json.loads(submitted[0].content)
    assert submitted[0].url.host == FC_HOST


def test_from_upload_takes_the_venue_as_an_ordinary_form_field(
    make_client, db, transport
):
    """A multipart envelope, so the field is a text part — the same shape
    `pool` takes on this route."""
    client = make_client()
    owner = _new_user(db)

    r = client.post(
        "/v1alpha1/jobs/from-upload",
        files={"workspace": ("ws.tar.gz", make_tarball(CLEAN_REPO),
                             "application/gzip")},
        data={"coordinator": "fc"},
        headers={"Authorization": f"Bearer {_jwt(owner)}"},
    )

    assert r.status_code == 201, r.text
    assert r.json()["coordinator"] == "fc"
    assert set(transport.venues_hit) == {"fc"}


# ---------------------------------------------------------------------------
# 3. refusals, at the edge, with the right status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["render ", "RENDER", "aws", "", 3, True, []])
def test_an_unknown_coordinator_value_is_400_and_nothing_is_submitted(
    make_client, db, transport, bad
):
    """400, naming the allowed values, and — the half that matters — no
    artifact staged and no job submitted anywhere."""
    client = make_client()

    r = _submit(client, _jwt(_new_user(db)), coordinator=bad)

    assert r.status_code == 400, r.text
    for venue in COORDINATOR_VENUES:
        assert venue in r.json()["detail"]
    assert transport.requests == []


def test_an_unconfigured_venue_is_409_and_never_runs_on_the_other_one(
    make_client, db, transport
):
    """The refusal that keeps the measurement honest. A deployment with no
    `COORDINATOR_URL_FC` must not accept an `fc` job and run it on Render:
    that job would be labelled with a venue it never touched, and nothing
    downstream could tell.

    409, not 400: the request is well-formed and it is the DEPLOYMENT that
    cannot serve it, which is a different thing for a caller to be told."""
    client = make_client(fc=False)

    r = _submit(client, _jwt(_new_user(db)), coordinator="fc")

    assert r.status_code == 409, r.text
    assert "COORDINATOR_URL_FC" in r.json()["detail"]
    assert transport.requests == []


def test_the_unconfigured_refusal_is_the_same_on_all_three_submit_routes(
    make_client, db, transport
):
    """Three doors, one rule. A route that forgot the check would accept the
    job and only fail deep inside `CoordinatorClient` as a 500."""
    client = make_client(fc=False)
    token = _jwt(_new_user(db))
    auth = {"Authorization": f"Bearer {token}"}

    raw = client.post(
        "/v1alpha1/jobs",
        json={"metadata": {"name": "x"}, "spec": {}, "coordinator": "fc"},
        headers=auth,
    )
    repo = _submit(client, token, coordinator="fc")
    upload = client.post(
        "/v1alpha1/jobs/from-upload",
        files={"workspace": ("ws.tar.gz", make_tarball(CLEAN_REPO),
                             "application/gzip")},
        data={"coordinator": "fc"},
        headers=auth,
    )

    assert [raw.status_code, repo.status_code, upload.status_code] == [409] * 3
    assert transport.requests == []


def test_a_configured_deployment_still_refuses_a_venue_that_is_not_a_venue(
    make_client, db, transport
):
    """Configuring FC does not widen the allowlist."""
    client = make_client()

    r = _submit(client, _jwt(_new_user(db)), coordinator="fc2")

    assert r.status_code == 400
    assert transport.requests == []


# ---------------------------------------------------------------------------
# 4. every later call follows the job
# ---------------------------------------------------------------------------


def test_every_later_call_about_an_fc_job_goes_back_to_fc(
    make_client, db, transport
):
    """THE POINT OF THE WHOLE FEATURE. Six routes, one job, and every one of
    them must reach the coordinator that accepted it — a call to the other
    one answers 404 for a job that is running fine, or worse, succeeds
    against a coordinator that has never heard of it."""
    client = make_client()
    owner = _new_user(db)
    auth = {"Authorization": f"Bearer {_jwt(owner)}"}
    job_id = _submit(client, _jwt(owner), coordinator="fc").json()["job_id"]
    transport.reset()

    assert client.get(f"/v1alpha1/jobs/{job_id}", headers=auth).status_code == 200
    assert client.get(
        f"/v1alpha1/jobs/{job_id}/events", headers=auth).status_code == 200
    assert client.get(
        f"/v1alpha1/jobs/{job_id}/tasks", headers=auth).status_code == 200
    assert client.get(
        f"/v1alpha1/jobs/{job_id}/artifacts", headers=auth).status_code == 200
    assert client.get(
        f"/v1alpha1/jobs/{job_id}/result", headers=auth).status_code == 200
    assert client.post(
        f"/v1alpha1/jobs/{job_id}/cancel", headers=auth).status_code == 200

    assert transport.requests, "nothing was forwarded at all"
    assert set(transport.venues_hit) == {"fc"}


def test_a_render_job_and_an_fc_job_are_read_from_their_own_coordinators(
    make_client, db, transport
):
    """Two jobs, one account, one page. The venue is a property of the JOB,
    so the two reads must diverge even though the caller and the request are
    otherwise identical."""
    client = make_client()
    owner = _new_user(db)
    auth = {"Authorization": f"Bearer {_jwt(owner)}"}
    render_job = _submit(client, _jwt(owner)).json()["job_id"]
    fc_job = _submit(client, _jwt(owner), coordinator="fc").json()["job_id"]
    transport.reset()

    assert client.get(f"/v1alpha1/jobs/{render_job}",
                      headers=auth).status_code == 200
    assert transport.venues_hit == ["render"]
    transport.reset()

    assert client.get(f"/v1alpha1/jobs/{fc_job}", headers=auth).status_code == 200
    assert transport.venues_hit == ["fc"]


def test_the_jobs_list_asks_each_venue_the_user_actually_has_jobs_on(
    make_client, db, transport
):
    """A listing per venue, and no listing for a venue this account has never
    used. Asking only the default would make every FC job vanish from the
    console — a bug that gets diagnosed as "the job was never submitted"."""
    client = make_client()
    owner = _new_user(db)
    auth = {"Authorization": f"Bearer {_jwt(owner)}"}
    render_job = _submit(client, _jwt(owner)).json()["job_id"]
    fc_job = _submit(client, _jwt(owner), coordinator="fc").json()["job_id"]
    transport.reset()

    listed = client.get("/v1alpha1/jobs", headers=auth).json()

    assert sorted(transport.venues_hit) == ["fc", "render"]
    by_id = {j["job_id"]: j for j in listed}
    assert by_id[render_job]["coordinator"] == "render"
    assert by_id[fc_job]["coordinator"] == "fc"


def test_a_user_with_only_render_jobs_never_makes_the_list_ask_fc(
    make_client, db, transport
):
    """The duty-cycle rule applied to the browser side: a jobs page polled
    every two seconds must not be a reason FC wakes up."""
    client = make_client()
    owner = _new_user(db)
    auth = {"Authorization": f"Bearer {_jwt(owner)}"}
    _submit(client, _jwt(owner))
    transport.reset()

    client.get("/v1alpha1/jobs", headers=auth)

    assert set(transport.venues_hit) == {"render"}


def test_the_artifact_download_for_an_fc_job_reads_fcs_store(
    make_client, db, transport
):
    """Artifacts live on the coordinator's disk. The wrong venue is a 404 for
    a file that exists."""
    client = make_client()
    owner = _new_user(db)
    auth = {"Authorization": f"Bearer {_jwt(owner)}"}
    job_id = _submit(client, _jwt(owner), coordinator="fc").json()["job_id"]
    transport.seed_artifact("fc", f"jobs/{job_id}/out/metrics.json", b"{}")
    transport.reset()

    r = client.get(f"/v1alpha1/jobs/{job_id}/artifacts/out/metrics.json",
                   headers=auth)

    assert r.status_code == 200, r.text
    assert set(transport.venues_hit) == {"fc"}


# ---------------------------------------------------------------------------
# 5. agent routes follow the work
# ---------------------------------------------------------------------------


def test_an_attempt_commit_goes_to_the_coordinator_that_issued_the_lease(
    make_client, db, transport
):
    """A lease lives inside the coordinator that issued it. Committing to the
    other one is not an error anybody sees: the far side has never heard of
    the lease, the real holder's lease ages out in its own sweeper, and the
    task is silently requeued somewhere else."""
    client = make_client()
    owner = _new_user(db)
    machine_id, machine_token = _enrol(db, owner)
    job_id = _submit(client, _jwt(owner), coordinator="fc").json()["job_id"]
    lease_id = f"lease-{uuid.uuid4().hex[:10]}"
    dbmod.record_attempt(
        db, lease_id=lease_id, machine_id=machine_id,
        job_id=job_id, task_id="task-000",
    )
    transport.reset()

    r = client.post(
        f"/v1alpha1/attempts/{lease_id}/complete",
        json={"accepted": True},
        headers={"Authorization": f"Bearer {machine_token}"},
    )

    assert r.status_code == 200, r.text
    assert set(transport.venues_hit) == {"fc"}


def test_an_attempt_on_a_render_job_still_goes_to_render(
    make_client, db, transport
):
    """The same lookup, the other answer — a lease with no venue recorded
    anywhere (and every lease from before this shipped) must land exactly
    where it always did."""
    client = make_client()
    owner = _new_user(db)
    machine_id, machine_token = _enrol(db, owner)
    job_id = _submit(client, _jwt(owner)).json()["job_id"]
    lease_id = f"lease-{uuid.uuid4().hex[:10]}"
    dbmod.record_attempt(
        db, lease_id=lease_id, machine_id=machine_id,
        job_id=job_id, task_id="task-000",
    )
    transport.reset()

    client.post(
        f"/v1alpha1/attempts/{lease_id}/heartbeat",
        json={},
        headers={"Authorization": f"Bearer {machine_token}"},
    )

    assert set(transport.venues_hit) == {"render"}


def test_a_lease_this_api_never_recorded_falls_back_to_the_default(
    make_client, db, transport
):
    """The claim's accounting write is best-effort, so an unrecorded lease is
    a real state. It must not fail the agent — it must answer the venue the
    route would have used before any of this existed."""
    client = make_client()
    _owner = _new_user(db)
    _machine_id, machine_token = _enrol(db, _owner)

    r = client.post(
        f"/v1alpha1/attempts/lease-never-seen-{uuid.uuid4().hex[:8]}/fail",
        json={"error": "x"},
        headers={"Authorization": f"Bearer {machine_token}"},
    )

    assert r.status_code == 200, r.text
    assert set(transport.venues_hit) == {"render"}


def test_an_agent_upload_goes_to_the_venue_of_the_lease_it_is_working(
    make_client, db, transport
):
    """The artifact routes carry neither a job id nor a lease id — an output
    key is `jobs/<job_id>/…` but an INPUT key is `uploads/<uuid>/code.tar.gz`
    — so the venue comes from what this machine is currently working on."""
    client = make_client()
    owner = _new_user(db)
    machine_id, machine_token = _enrol(db, owner)
    job_id = _submit(client, _jwt(owner), coordinator="fc").json()["job_id"]
    dbmod.record_attempt(
        db, lease_id=f"lease-{uuid.uuid4().hex[:10]}", machine_id=machine_id,
        job_id=job_id, task_id="task-000",
    )
    transport.reset()

    r = client.put(
        "/v1alpha1/artifacts/uploads/deadbeef/out.bin",
        content=b"bytes",
        headers={"Authorization": f"Bearer {machine_token}"},
    )

    assert r.status_code == 200, r.text
    assert set(transport.venues_hit) == {"fc"}


def test_a_checkpoint_hop_follows_its_jobs_venue(make_client, db, transport):
    client = make_client()
    owner = _new_user(db)
    _machine_id, machine_token = _enrol(db, owner)
    job_id = _submit(client, _jwt(owner), coordinator="fc").json()["job_id"]
    transport.reset()

    client.post(
        f"/v1alpha1/jobs/{job_id}/tasks/task-000/checkpoints/commit",
        json={},
        headers={"Authorization": f"Bearer {machine_token}"},
    )

    assert set(transport.venues_hit) == {"fc"}


# ---------------------------------------------------------------------------
# 6. claims: no work, no traffic
# ---------------------------------------------------------------------------


def test_a_claim_never_touches_fc_while_fc_has_no_work(
    make_client, db, transport
):
    """THE DUTY-CYCLE PROPERTY. FC exists to be measured on invocations. A
    fleet claiming against it every few seconds while it holds nothing would
    inflate that count until the number this feature exists to produce meant
    nothing — so an idle venue must see ZERO claim traffic, not "a little"."""
    client = make_client()
    owner = _new_user(db)
    _machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner))  # a render job, and only a render job
    transport.reset()

    for _ in range(3):
        client.post("/v1alpha1/leases/claim", json={},
                    headers={"Authorization": f"Bearer {machine_token}"})

    assert transport.venues_hit == ["render", "render", "render"]


def test_a_claim_reaches_fc_while_an_fc_job_is_running(
    make_client, db, transport
):
    """The other half: a venue with work has to be offered the claim, or the
    job sits there for ever and the measurement never happens."""
    client = make_client()
    owner = _new_user(db)
    _machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner), coordinator="fc")
    transport.claimable = {"fc"}
    transport.reset()

    r = client.post("/v1alpha1/leases/claim", json={},
                    headers={"Authorization": f"Bearer {machine_token}"})

    assert r.status_code == 200, r.text
    # FC answered, so render is never asked — a second venue must not hand
    # the same machine a second lease it can only work one of.
    assert transport.venues_hit == ["fc"]


def test_a_claim_falls_through_to_the_default_when_the_other_venue_is_empty(
    make_client, db, transport
):
    """An FC job exists but FC has nothing claimable this instant. The
    machine must still be offered the default's work rather than being told
    to come back later."""
    client = make_client()
    owner = _new_user(db)
    _machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner), coordinator="fc")
    transport.claimable = set()
    transport.reset()

    client.post("/v1alpha1/leases/claim", json={},
                headers={"Authorization": f"Bearer {machine_token}"})

    assert transport.venues_hit == ["fc", "render"]


def test_a_finished_fc_job_stops_attracting_claim_traffic(
    make_client, db, transport
):
    """"Has work" means a NON-TERMINAL job. A venue whose only job succeeded
    is idle again, and an idle venue is one nobody polls."""
    client = make_client()
    owner = _new_user(db)
    _machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    job_id = _submit(client, _jwt(owner), coordinator="fc").json()["job_id"]
    dbmod.set_job_status(db, job_id, "SUCCEEDED", finished=True)
    _quiesce_non_default_jobs(db)
    transport.reset()

    client.post("/v1alpha1/leases/claim", json={},
                headers={"Authorization": f"Bearer {machine_token}"})

    assert transport.venues_hit == ["render"]


def test_a_claim_never_touches_a_venue_this_deployment_cannot_address(
    make_client, db, transport
):
    """A job row can name a venue this process has no URL for — written by a
    deployment that had one, or read after the variable was removed.
    `forward` would refuse it, and refusing a claim is worse than not
    offering it one."""
    fc_client = make_client()
    owner = _new_user(db)
    _machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    _submit(fc_client, _jwt(owner), coordinator="fc")

    render_only = make_client(fc=False)
    transport.reset()

    r = render_only.post("/v1alpha1/leases/claim", json={},
                         headers={"Authorization": f"Bearer {machine_token}"})

    assert r.status_code in (200, 204), r.text
    assert transport.venues_hit == ["render"]


# ---------------------------------------------------------------------------
# 7. lazy registration: a venue that has never heard of this machine
#
# NOTHING REGISTERS A NODE ANYWHERE BUT THE DEFAULT. `register_node` forwards
# with no venue, an agent has no idea venues exist, and a coordinator's node
# registry is process memory — `service/modea.py` keeps it in `ModeAState.nodes`
# and refuses a claim from a node that is not in it with
# `403 "unregistered node — register first"`.
#
# So every claim `_claimable_venues` sends to FC would 403 for ever, the FC job
# would sit there, and nothing would report an error: a 403 from one venue is
# indistinguishable from "busy" once the claim falls through to the next. And it
# cannot be fixed by registering once by hand — FC instances have a 36-hour
# ceiling and are rebuilt on any config change, timeout or OOM, so a manual
# registration is correct until the next rebuild and then silently is not.
#
# The property the tests below exist for, in priority order: the DEFAULT venue's
# claim is exactly the one call it has always been; a 403-unregistered from a
# non-default venue costs one registration and one retry, never a loop; the
# registration carries the pool stamp a real one would, because pool membership
# is the placement gate the demo job is selected by; and no failure of any of it
# ever reaches the agent as anything but "this venue did not answer 200".
# ---------------------------------------------------------------------------


def _register(client, token: str, **body):
    """Register a machine the ordinary way — through the agent route, which
    forwards to the default venue and nowhere else."""
    payload = {
        "schema_version": "v1alpha1",
        "node_id": "whatever-the-agent-says",
        "kubernetes_node": "k8s-node",
        "hostname": "host",
        "capabilities": {},
    }
    payload.update(body)
    return client.post(
        "/v1alpha1/nodes/register",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


def _claim(client, token: str):
    return client.post(
        "/v1alpha1/leases/claim",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )


def _registration_sent_to(transport: TwoVenueTransport, venue: str) -> list[dict]:
    return [
        json.loads(r.content)
        for r in transport.requests
        if transport._venue_of(r) == venue
        and r.url.path == "/v1alpha1/nodes/register"
    ]


def test_the_default_venues_claim_is_still_exactly_one_call(
    make_client, db, transport
):
    """THE PROPERTY THAT MUST NOT BREAK. Every machine in the fleet claims
    from the default venue, thousands of times a day. Lazy registration must
    cost that path nothing at all — not a lookup, not a second hop, not a new
    way to fail — so the assertion is on the exact request list, not on the
    status the agent saw."""
    client = make_client()
    owner = _new_user(db)
    _machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner))
    transport.reset()

    for _ in range(3):
        _claim(client, machine_token)

    assert transport.paths_on("render") == ["/v1alpha1/leases/claim"] * 3
    assert transport.venues_hit == ["render"] * 3


def test_a_403_from_the_default_venue_is_handed_back_untouched(
    make_client, db, transport
):
    """The default venue is excluded by NAME, before anything looks at the
    body. Even the exact refusal that triggers a registration on FC must, on
    render, be passed straight back to the agent with no registration and no
    retry — the incumbent control plane's 403s are its own business and have
    been answered this way since before venues existed."""
    client = make_client()
    owner = _new_user(db)
    _machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner))
    transport.requires_registration = {"render"}
    transport.reset()

    r = _claim(client, machine_token)

    assert r.status_code == 403
    assert "register first" in r.json()["detail"]
    assert transport.paths_on("render") == ["/v1alpha1/leases/claim"]


def test_a_claim_to_fc_from_an_unknown_node_registers_it_once_and_retries_once(
    make_client, db, transport
):
    """The whole feature in one test: claim, 403, register, claim, lease.

    Exactly those four — a registration per claim would be a second hop on
    every poll of a venue measured by invocation count, and a retry loop would
    be worse than the 403 it is trying to fix."""
    client = make_client()
    owner = _new_user(db)
    _machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner), coordinator="fc")
    transport.requires_registration = {"fc"}
    transport.claimable = {"fc"}
    transport.reset()

    r = _claim(client, machine_token)

    assert r.status_code == 200, r.text
    assert transport.paths_on("fc") == [
        "/v1alpha1/leases/claim",
        "/v1alpha1/nodes/register",
        "/v1alpha1/leases/claim",
    ]
    # And the venue that answered is the only one that was asked: a lease is a
    # commitment the far side has already made, and a second venue must not be
    # offered a claim that has already been satisfied.
    assert transport.paths_on("render") == []


def test_the_lazy_registration_carries_the_pool_stamp_a_real_one_would(
    make_client, db, transport
):
    """Pool membership is the seventh placement gate. A machine registered
    into FC with no pool bindings is a node FC knows about and will still
    never lease the pool-scoped demo job to — which looks exactly like having
    no capacity, and is the failure this assertion exists to catch.

    Compared against a REAL registration rather than against a literal: the
    two must be produced by the same resolution (`pool_ids_for_machine`) and
    the same stamp (`_scrub_identity(..., pools_where="capabilities")`), so
    the test that means something is the one that fails if they diverge."""
    client = make_client()
    owner = _new_user(db)
    machine_id, machine_token = _enrol(db, owner)
    pool = dbmod.create_pool(db, name=f"anchors-{uuid.uuid4().hex[:8]}",
                             owner_id=owner)
    dbmod.bind_machine_pool(db, machine_id=machine_id, pool_id=str(pool["id"]))
    # A real registration, through the real route, to the default venue. The
    # agent forges a pool it does not belong to; the route overwrites it.
    _register(client, machine_token,
              capabilities={"cpu_cores": 8, "pools": ["forged-pool"]})
    real = _registration_sent_to(transport, "render")[-1]

    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner), coordinator="fc")
    transport.requires_registration = {"fc"}
    transport.claimable = {"fc"}
    transport.reset()

    _claim(client, machine_token)

    lazy = _registration_sent_to(transport, "fc")
    assert len(lazy) == 1
    assert lazy[0]["capabilities"]["pools"] == real["capabilities"]["pools"]
    # Not vacuously equal: the machine really is in a pool, and both bodies
    # say so.
    assert lazy[0]["capabilities"]["pools"] == [str(pool["id"])]
    assert "forged-pool" not in json.dumps(lazy[0])
    assert lazy[0]["node_id"] == real["node_id"]


def test_the_lazy_registration_restates_what_the_machine_registered(
    make_client, db, transport
):
    """Pools are not the only gate. `IsolationAwarePlacement` reads the
    capability booleans, so a lazy registration that shipped the protocol's
    fail-closed defaults would register a machine FC then refuses every task
    to — the same dead end as no pools, one gate along.

    The durable copy is `public.machines`, written by the register route's own
    capability snapshot. This is the read side of it."""
    client = make_client()
    owner = _new_user(db)
    _machine_id, machine_token = _enrol(db, owner)
    _register(client, machine_token, sandbox_capable=True, argv_capable=True,
              unsandboxed_argv_capable=True, module_capable=True,
              capabilities={"cpu_cores": 8, "os": "linux"})

    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner), coordinator="fc")
    transport.requires_registration = {"fc"}
    transport.reset()

    _claim(client, machine_token)

    lazy = _registration_sent_to(transport, "fc")[0]
    assert lazy["sandbox_capable"] is True
    assert lazy["argv_capable"] is True
    assert lazy["unsandboxed_argv_capable"] is True
    assert lazy["module_capable"] is True
    assert lazy["capabilities"]["cpu_cores"] == 8
    assert lazy["capabilities"]["os"] == "linux"


def test_a_second_403_after_the_retry_falls_through_instead_of_looping(
    make_client, db, transport
):
    """A venue rebuilt between the registration and the retry answers the same
    403 again. That is a real state on Function Compute — instances are
    replaced on any config change, timeout or OOM — and it must cost exactly
    one more call, not a loop: the claim falls through to the next venue like
    any other non-200."""
    client = make_client()
    owner = _new_user(db)
    _machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner), coordinator="fc")
    transport.requires_registration = {"fc"}
    transport.register_is_amnesiac = {"fc"}
    transport.reset()

    r = _claim(client, machine_token)

    assert transport.paths_on("fc") == [
        "/v1alpha1/leases/claim",
        "/v1alpha1/nodes/register",
        "/v1alpha1/leases/claim",
    ]
    # Fell through, and what the agent sees is the LAST venue's answer — the
    # default's, exactly as it would have been before any of this existed.
    assert transport.paths_on("render") == ["/v1alpha1/leases/claim"]
    assert r.status_code == 204


def test_a_registration_failure_never_reaches_the_agent(
    make_client, db, transport
):
    """An FC fault is not an agent's fault. A refused registration is
    swallowed into "this venue did not answer 200" — no retry, no 5xx, and the
    machine still gets whatever the default venue has for it."""
    client = make_client()
    owner = _new_user(db)
    _machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner), coordinator="fc")
    transport.requires_registration = {"fc"}
    transport.register_status = {"fc": 500}
    transport.reset()

    r = _claim(client, machine_token)

    assert r.status_code == 204, r.text
    # No retry against a venue that would not take the registration.
    assert transport.paths_on("fc") == [
        "/v1alpha1/leases/claim",
        "/v1alpha1/nodes/register",
    ]
    assert transport.paths_on("render") == ["/v1alpha1/leases/claim"]


def test_a_403_that_registering_would_not_fix_does_not_trigger_a_registration(
    make_client, db, transport
):
    """403 is also what a wrong join code answers, and registering does not
    fix that — it just spends a hop on a venue whose invocations are the
    measurement. So the trigger is the coordinator's WORDS, not its status."""
    client = make_client()
    owner = _new_user(db)
    _machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner), coordinator="fc")
    transport.refuse_claim_with = {"fc": "invalid or missing join code"}
    transport.reset()

    _claim(client, machine_token)

    assert transport.paths_on("fc") == ["/v1alpha1/leases/claim"]
    assert transport.paths_on("render") == ["/v1alpha1/leases/claim"]


# ---------------------------------------------------------------------------
# 7b. mirrored heartbeats: registration alone does not keep a node alive
#
# A COORDINATOR TRACKS LIVENESS BY HEARTBEAT, and registering only sets the
# clock once. `ModeAState` stamps `last_heartbeat` on register and on every
# heartbeat, and `node_view` reports a node quiet for longer than
# FLASHML_NODE_OFFLINE_SECONDS as `online: false`, after which placement stops
# considering it.
#
# MEASURED, not theorised. Minutes after both dev machines were lazily
# registered on the live FC coordinator with the right pools and capability
# booleans, FC reported them `online=False` with a heartbeat age of ~336s,
# while this API's own `machines.last_seen_at` said each had been seen seconds
# ago. `register_node` and `node_heartbeat` both forward to the DEFAULT venue
# and nowhere else, because an agent holds one base URL — this API — and has
# no idea venues exist.
#
# So the heartbeat is mirrored, and the properties below are what keeps that
# from being a bad idea, in priority order: an IDLE non-default venue receives
# nothing at all (the duty-cycle measurement is the whole point of the second
# venue, and heartbeats never stop the way claims do); the default venue's
# heartbeat is exactly the one call it has always been and its answer is the
# only one the agent ever sees; a 404 from a non-default venue costs one
# registration and one retry, built by the SAME helper the claim path uses;
# and no failure of any of it — refusal, timeout, a venue that has stopped
# answering entirely — reaches the agent.
# ---------------------------------------------------------------------------


def _heartbeat(client, token: str, **body):
    """Heartbeat the ordinary way — the agent's own route, which knows about
    one coordinator and never says which."""
    payload: dict = {"schema_version": "v1alpha1", "node_id": "whatever"}
    payload.update(body)
    return client.post(
        "/v1alpha1/nodes/whatever/heartbeat",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


def _heartbeats_sent_to(transport: TwoVenueTransport, venue: str) -> list[dict]:
    return [
        json.loads(r.content)
        for r in transport.requests
        if transport._venue_of(r) == venue
        and r.url.path.endswith("/heartbeat")
        and r.url.path.startswith("/v1alpha1/nodes/")
    ]


def _hb_path(node_id: str) -> str:
    return f"/v1alpha1/nodes/{node_id}/heartbeat"


def test_a_heartbeat_never_reaches_fc_while_fc_has_no_work(
    make_client, db, transport
):
    """THE MOST IMPORTANT TEST IN THIS SECTION. A heartbeat, unlike a claim,
    never stops: a machine deep in a training run keeps sending them. So a
    mirror that ignored the work rule would not merely inflate FC's invocation
    count, it would pin it at the fleet's heartbeat rate for ever and destroy
    the one measurement the second venue exists to produce.

    Three heartbeats while only the default venue has a job: three requests,
    all of them to the default, and FC's request list empty — not short."""
    client = make_client()
    owner = _new_user(db)
    machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner))  # a render job, and only a render job
    transport.reset()

    for _ in range(3):
        r = _heartbeat(client, machine_token)
        assert r.status_code == 200, r.text

    assert transport.venues_hit == ["render", "render", "render"]
    assert transport.paths_on("fc") == []


def test_a_heartbeat_stops_reaching_fc_the_moment_its_job_is_finished(
    make_client, db, transport
):
    """"Has work" means a NON-TERMINAL job, on the heartbeat path exactly as
    on the claim path. A venue whose only job succeeded is idle again, and an
    idle venue is one nobody heartbeats — otherwise the traffic that follows a
    demo job would never end."""
    client = make_client()
    owner = _new_user(db)
    machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    job_id = _submit(client, _jwt(owner), coordinator="fc").json()["job_id"]
    _heartbeat(client, machine_token)
    assert transport.paths_on("fc"), "precondition: fc was being heartbeated"

    dbmod.set_job_status(db, job_id, "SUCCEEDED", finished=True)
    transport.reset()

    _heartbeat(client, machine_token)

    assert transport.venues_hit == ["render"]


def test_a_heartbeat_is_never_mirrored_to_a_venue_this_deployment_cannot_address(
    make_client, db, transport
):
    """A job row can name a venue this process has no URL for. `forward`
    would refuse it, and the refusal would be raised inside an agent's
    heartbeat — so the venue is filtered out before anything is dispatched,
    by the same `venue_configured` check the claim path uses."""
    fc_client = make_client()
    owner = _new_user(db)
    machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    _submit(fc_client, _jwt(owner), coordinator="fc")

    render_only = make_client(fc=False)
    transport.reset()

    r = _heartbeat(render_only, machine_token)

    assert r.status_code == 200, r.text
    assert transport.venues_hit == ["render"]


def test_the_default_venues_heartbeat_is_still_exactly_one_call(
    make_client, db, transport
):
    """THE PROPERTY THAT MUST NOT BREAK. Every machine in the fleet heartbeats
    the default venue every few seconds, for ever. Mirroring must cost that
    path nothing — not a second hop, not a different body, not a new way to
    fail — so the assertion is on the exact request list."""
    client = make_client()
    owner = _new_user(db)
    machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner), coordinator="fc")  # fc IS holding work
    transport.reset()

    r = _heartbeat(client, machine_token)

    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok"}
    # One call to the default, and it is the FIRST one: its answer is what
    # the agent gets, and it is computed before anything else happens.
    assert transport.paths_on("render") == [_hb_path(_node_id_of(db, machine_id))]
    assert transport.venues_hit[0] == "render"


def test_a_heartbeat_is_mirrored_to_fc_while_an_fc_job_is_running(
    make_client, db, transport
):
    """The other half. Without this, a machine registers on FC, goes stale
    five minutes later and is never a live candidate there again — which
    looks exactly like having no capacity."""
    client = make_client()
    owner = _new_user(db)
    machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner), coordinator="fc")
    transport.reset()

    r = _heartbeat(client, machine_token)

    assert r.status_code == 200, r.text
    node_id = _node_id_of(db, machine_id)
    assert transport.venues_hit == ["render", "fc"]
    assert transport.paths_on("fc") == [_hb_path(node_id)]


def test_the_mirrored_heartbeat_is_byte_for_byte_the_default_one(
    make_client, db, transport
):
    """Same body, same identity, same server-stamped pools. Pool membership
    is the seventh placement gate and a heartbeat REPLACES it wholesale on the
    coordinator's side (`NodeHeartbeat.pools`), so a mirror that dropped the
    stamp would quietly empty the node's pools on FC and leave it registered,
    online, and refused every pool-scoped task."""
    client = make_client()
    owner = _new_user(db)
    machine_id, machine_token = _enrol(db, owner)
    pool = dbmod.create_pool(db, name=f"anchors-{uuid.uuid4().hex[:8]}",
                             owner_id=owner)
    dbmod.bind_machine_pool(db, machine_id=machine_id, pool_id=str(pool["id"]))
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner), coordinator="fc")
    transport.reset()

    # The agent forges a pool it does not belong to, on both copies.
    _heartbeat(client, machine_token, pools=["forged-pool"])

    default = _heartbeats_sent_to(transport, "render")
    mirrored = _heartbeats_sent_to(transport, "fc")
    assert len(default) == 1 and len(mirrored) == 1
    assert mirrored[0] == default[0]
    # Not vacuously equal: the machine really is in a pool and both say so.
    assert mirrored[0]["pools"] == [str(pool["id"])]
    assert "forged-pool" not in json.dumps(mirrored[0])


def test_a_pools_lookup_failure_mirrors_nothing_rather_than_an_empty_stamp(
    make_client, db, transport, monkeypatch
):
    """The default venue fails CLOSED — it is sent an empty pool list, and
    corrected by the next successful heartbeat seconds later. The MIRROR must
    not do that: a heartbeat replaces the node's pools wholesale on the
    coordinator's side, so an empty stamp would unbind the machine from every
    pool on FC, and FC might not be heartbeated again for a while. So a
    failure here mirrors nothing at all."""
    client = make_client()
    owner = _new_user(db)
    machine_id, machine_token = _enrol(db, owner)
    pool = dbmod.create_pool(db, name=f"anchors-{uuid.uuid4().hex[:8]}",
                             owner_id=owner)
    dbmod.bind_machine_pool(db, machine_id=machine_id, pool_id=str(pool["id"]))
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner), coordinator="fc")

    def raiser(*_a, **_kw):
        raise RuntimeError("simulated pool lookup failure")

    monkeypatch.setattr(dbmod, "pool_ids_for_machine", raiser)
    transport.reset()

    r = _heartbeat(client, machine_token)

    assert r.status_code == 200, r.text
    assert _heartbeats_sent_to(transport, "render")[0]["pools"] == []
    assert transport.paths_on("fc") == []


def test_a_404_from_fc_registers_the_machine_once_and_retries_once(
    make_client, db, transport
):
    """FC's node registry is process memory and its instances are rebuilt on
    a 36h ceiling or any config change, so a heartbeat from a node it has
    forgotten is a NORMAL state, not an error. It answers
    `404 unknown node … — register first` — a different status from the
    claim's 403, from the same registry — and the repair is the same one:
    register, retry, once each. Never a loop."""
    client = make_client()
    owner = _new_user(db)
    machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner), coordinator="fc")
    transport.requires_registration = {"fc"}
    transport.reset()

    r = _heartbeat(client, machine_token)

    assert r.status_code == 200, r.text
    node_id = _node_id_of(db, machine_id)
    assert transport.paths_on("fc") == [
        _hb_path(node_id),
        "/v1alpha1/nodes/register",
        _hb_path(node_id),
    ]
    assert transport.paths_on("render") == [_hb_path(node_id)]


def test_the_mirrors_registration_is_the_claim_paths_registration(
    make_client, db, transport
):
    """One body-builder, not two. The registration a forgotten node gets from
    the heartbeat mirror must be the one it would have got from the claim —
    same pools, same capability booleans, same identity — because two
    implementations is exactly how a venue comes to hold a stale idea of what
    a machine can do. Asserted as EQUALITY against a real claim-path
    registration rather than against a literal, so the test fails if either
    side ever grows a field the other does not."""
    client = make_client()
    owner = _new_user(db)
    machine_id, machine_token = _enrol(db, owner)
    pool = dbmod.create_pool(db, name=f"anchors-{uuid.uuid4().hex[:8]}",
                             owner_id=owner)
    dbmod.bind_machine_pool(db, machine_id=machine_id, pool_id=str(pool["id"]))
    _register(client, machine_token, sandbox_capable=True, module_capable=True,
              capabilities={"cpu_cores": 8, "os": "linux"})
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner), coordinator="fc")
    transport.requires_registration = {"fc"}
    transport.register_is_amnesiac = {"fc"}  # so both repairs still fire

    transport.reset()
    _claim(client, machine_token)
    from_claim = _registration_sent_to(transport, "fc")

    transport.reset()
    _heartbeat(client, machine_token)
    from_heartbeat = _registration_sent_to(transport, "fc")

    assert len(from_claim) == 1 and len(from_heartbeat) == 1
    assert from_heartbeat[0] == from_claim[0]
    # Not vacuously equal: it carries the facts the placement gates read.
    assert from_heartbeat[0]["capabilities"]["pools"] == [str(pool["id"])]
    assert from_heartbeat[0]["sandbox_capable"] is True
    assert from_heartbeat[0]["module_capable"] is True


def test_a_second_404_after_the_registration_does_not_loop(
    make_client, db, transport
):
    """A venue rebuilt between the registration and the retry answers 404
    again. One more call, then nothing: the next heartbeat is seconds away
    and will try once more, which is a retry ladder that costs nothing."""
    client = make_client()
    owner = _new_user(db)
    machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner), coordinator="fc")
    transport.requires_registration = {"fc"}
    transport.register_is_amnesiac = {"fc"}
    transport.reset()

    r = _heartbeat(client, machine_token)

    assert r.status_code == 200, r.text
    node_id = _node_id_of(db, machine_id)
    assert transport.paths_on("fc") == [
        _hb_path(node_id),
        "/v1alpha1/nodes/register",
        _hb_path(node_id),
    ]


def test_a_404_that_registering_would_not_fix_does_not_trigger_a_registration(
    make_client, db, transport
):
    """404 is also what a mistyped path and a proxy in front of a stopped
    function answer, and registering fixes neither — it just spends a hop on
    a venue whose invocations are the measurement. So the trigger is the
    coordinator's WORDS, not its status."""
    client = make_client()
    owner = _new_user(db)
    machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner), coordinator="fc")
    transport.refuse_heartbeat_with = {"fc": "Not Found"}
    transport.reset()

    r = _heartbeat(client, machine_token)

    assert r.status_code == 200, r.text
    assert transport.paths_on("fc") == [_hb_path(_node_id_of(db, machine_id))]


def test_a_dead_fc_changes_nothing_the_agent_sees(make_client, db, transport):
    """THE PROPERTY THE AGENT DEPENDS ON. A second venue that is refusing
    connections outright — cold, wedged, deleted, misconfigured — must be
    completely invisible to flashnode: same status, same body, and the
    default venue still asked exactly once. `machines.last_seen_at`, which
    `capacity/reconcile` destroys rented GPUs by reading, is written before
    any of this and must survive it."""
    client = make_client()
    owner = _new_user(db)
    machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner), coordinator="fc")
    transport.dead = {"fc"}
    transport.reset()

    r = _heartbeat(client, machine_token)

    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok"}
    assert transport.paths_on("render") == [_hb_path(_node_id_of(db, machine_id))]
    with db.cursor() as cur:
        cur.execute(
            "select last_seen_at from public.machines where id = %s", (machine_id,)
        )
        assert cur.fetchone()["last_seen_at"] is not None


def test_an_fc_that_refuses_the_registration_costs_the_agent_nothing(
    make_client, db, transport
):
    """A refused registration is swallowed into "this venue did not answer" —
    no retry against it, and the agent's heartbeat is untouched."""
    client = make_client()
    owner = _new_user(db)
    machine_id, machine_token = _enrol(db, owner)
    _quiesce_non_default_jobs(db)
    _submit(client, _jwt(owner), coordinator="fc")
    transport.requires_registration = {"fc"}
    transport.register_status = {"fc": 500}
    transport.reset()

    r = _heartbeat(client, machine_token)

    assert r.status_code == 200, r.text
    node_id = _node_id_of(db, machine_id)
    assert transport.paths_on("fc") == [
        _hb_path(node_id),
        "/v1alpha1/nodes/register",
    ]
    assert transport.paths_on("render") == [_hb_path(node_id)]


# ---------------------------------------------------------------------------
# 8. federated runs cannot pick a venue, and say so
# ---------------------------------------------------------------------------


def test_a_federated_submit_that_names_a_venue_is_refused_before_anything(
    make_client, db, transport
):
    """A federated run is N coordinator jobs submitted by an in-process
    driver that addresses `settings.coordinator_url` directly. Accepting the
    field would stage the code on FC, record `fc` on the parent row, and then
    run every round on Render — a job whose recorded venue is a lie, which is
    the exact corruption the no-fallback rule exists to prevent."""
    client = make_client(files={"flashml.yaml": FEDERATED_YAML,
                                "train.py": FEDERATED_TRAIN_PY})

    r = _submit(client, _jwt(_new_user(db)), coordinator="fc")

    assert r.status_code == 409, r.text
    assert "federated" in r.json()["detail"]
    # No artifact staged, no job submitted, on either venue.
    assert transport.requests == []
