"""The judges' demo, second half: A JUDGE HOSTS THEIR OWN MACHINE.

`test_public_demo.py` covers the anchors — our four Alibaba machines, one
fixed nine-task sweep, two coordinators. This file covers the other claim the
page makes, which is the harder one to believe and the better one to show: a
person with no account plugs in **their own laptop** and watches it do work on
this network.

    flashnode login  ->  a user code on their terminal
    paste it         ->  POST /v1alpha1/public/demo/join      (no auth)
    flashnode work   ->  they appear in GET .../demo `guests` (no auth)
    press Run        ->  POST /v1alpha1/public/demo/run-mine  (no auth)

**Six properties carry the weight here, and every test is an instance of one.**

1. **No credential, ever** — same as the sibling file, and the same reason:
   "works without one" is not the property, "never asks" is.
2. **The APPROVAL IS THE EXISTING ONE.** `join` calls
   `enrolment.approve_device_code` with the demo owner in the seat a signed-in
   approver would occupy. Nothing here mints a token: the agent still redeems
   its own device code through `redeem_device_code`, and the test proves it by
   redeeming one after a public join.
3. **THE POOL SPLIT IS THE ARCHITECTURE.** A guest lands in `demo-guests`,
   never in Test-1, and the guest job is scoped to `demo-guests`, never to the
   anchors. Both directions are asserted, because either one failing corrupts
   the measured sweep this page exists to publish.
4. **A stranger's code is a 404.** `approve_device_code` short-circuits on an
   already-approved code with no ownership check, so without the owner check
   in the route anyone who learned a redeemed user_code could bind somebody
   else's machine into a public pool.
5. **`guests` NAMES NOBODY.** A judge's laptop is not `official`, so it
   renders as a `prov…` handle. The hostname a volunteer's agent reported is
   personal and this is an unauthenticated page.
6. **THE GUEST JOB MUST NOT DECLARE ITSELF AMD64-ONLY.** Most judges are on
   Apple Silicon. `PlacementSpec.architectures` defaults to `["amd64"]` in the
   pinned protocol and every job this API has ever compiled carries that
   default, so the assertion below reads the spec that reached the coordinator
   and insists on `arm64` being in it.

Fixtures, the coordinator fake and the database are `test_public_demo`'s,
imported rather than rebuilt — one deployment shape for both halves of one
page.
"""
from __future__ import annotations

import json
import uuid

import pytest

from flashml_cloud_api import cli_auth
from flashml_cloud_api import db as dbmod
from flashml_cloud_api import demo as demomod
from flashml_cloud_api import enrolment

from test_jobs_from_repo import (  # noqa: F401 - fixtures
    JWT_SECRET,
    RecordingFetch,
    _new_user,
    db,
    make_tarball,
)
from test_public_demo import (  # noqa: F401 - fixtures and the two-venue fake
    NO_AUTH,
    DemoTransport,
    _demo_ready,
    make_client,
    transport,
)

#: A volunteer hostname of exactly the shape this page must never publish.
#: `enrolment.py` writes `name=row["hostname"]`, and hostnames are personal.
JUDGE_HOSTNAME = "judge-macbook-pro.local"


# ---------------------------------------------------------------------------
# a quiet deployment
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def quiet_guests(db):
    """No guest run and no guest pool left over from a previous test.

    The demo's reads are DEPLOYMENT-WIDE by design — one guest pool for the
    whole product, not per account — so a pool a previous test created would
    be found by `guest_pool_id` in the next one and the "nobody has joined
    yet" refusals could never be observed. Scoped to the guest marker and the
    guest pool NAME, so nothing another module wrote is touched.
    """
    def _clear() -> None:
        with db.cursor() as cur:
            cur.execute(
                "delete from public.jobs where source->>%s = 'true'",
                (demomod.GUEST_SOURCE_KEY,),
            )
            cur.execute(
                "delete from public.machine_pools where pool_id in"
                " (select id from public.pools where name = %s)",
                (demomod.GUEST_POOL_NAME,),
            )
            cur.execute(
                "delete from public.pool_members where pool_id in"
                " (select id from public.pools where name = %s)",
                (demomod.GUEST_POOL_NAME,),
            )
            cur.execute(
                "delete from public.pools where name = %s",
                (demomod.GUEST_POOL_NAME,),
            )

    _clear()
    yield
    _clear()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _machine_code(db, *, hostname: str = JUDGE_HOSTNAME) -> dict:
    """What `flashnode login` leaves behind: a real device_codes row.

    Through `enrolment.start_device_code`, not an INSERT — the whole claim of
    this feature is that it drives the enrolment machinery that already
    exists, and a hand-written row would be a test of a schema rather than of
    a flow.
    """
    return enrolment.start_device_code(
        db, f"node-{uuid.uuid4().hex[:10]}", hostname, "macOS-15.3-arm64"
    )


def _expire(db, user_code: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            "update public.device_codes set expires_at = now() - interval"
            " '1 hour' where user_code = %s",
            (user_code,),
        )


def _join(client, user_code):
    """The paste. NO AUTHORIZATION HEADER, ever, in this file."""
    return client.post(
        "/v1alpha1/public/demo/join",
        json={"user_code": user_code},
        headers=NO_AUTH,
    )


def _run_mine(client, **body):
    return client.post(
        "/v1alpha1/public/demo/run-mine", json=body, headers=NO_AUTH
    )


def _read(client):
    return client.get("/v1alpha1/public/demo", headers=NO_AUTH)


def _guest_pool(db, owner_id: str) -> str | None:
    return demomod.guest_pool_id(db, owner_id)


def _online(db, machine_id: str, device_code: str) -> None:
    """Make a joined machine look like one that ran `flashnode work`.

    The redemption is not a shortcut — it is the step: `insert_machine` writes
    `status = 'pending'` and only `set_machine_token` promotes it to `active`,
    which is half of `MACHINE_ONLINE_PREDICATE`. A machine that joined and
    never redeemed is genuinely not online, and the page is right to say so.
    """
    assert enrolment.redeem_device_code(db, device_code)
    with db.cursor() as cur:
        cur.execute(
            "update public.machines set last_seen_at = now(),"
            " capabilities = %s::jsonb, geo_country = 'SG'"
            " where id = %s::uuid",
            (json.dumps({"cpu_cores": 8, "memory_bytes": 16 * 1024**3}),
             machine_id),
        )


def _submitted_specs(transport, venue: str = "render") -> list[dict]:
    return [
        json.loads(r.content) for r in transport.job_submissions_on(venue)
    ]


# ---------------------------------------------------------------------------
# 1. joining: the machinery that already exists, driven by nobody
# ---------------------------------------------------------------------------


def test_join_with_an_unknown_code_is_a_clean_404(make_client, db):
    _demo_ready(db)
    client = make_client()

    r = _join(client, "ZZZZZZZZ")

    assert r.status_code == 404, r.text
    # Nothing about this deployment's internals, and no stack trace.
    assert r.json()["detail"] == "unknown code"


def test_join_binds_the_machine_to_demo_guests_and_names_it_back(
    make_client, db
):
    """The whole flow, and the three things that must be true after it."""
    owner_id = _demo_ready(db)
    client = make_client()
    code = _machine_code(db)

    r = _join(client, code["user_code"])

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["pool"] == demomod.GUEST_POOL_NAME

    # (a) the judge gets to recognise their own machine. They proved
    #     possession of its one-shot device code to get this response, so the
    #     hostname here is an echo, not a disclosure — and `label` is the
    #     handle the public list will show, so the page can find their row.
    assert body["machine"]["name"] == JUDGE_HOSTNAME
    assert body["machine"]["node_id"]
    assert body["machine"]["label"].startswith("prov")
    assert JUDGE_HOSTNAME not in body["machine"]["label"]
    # No credential, of any kind, in the response.
    assert "token" not in json.dumps(body)

    # (b) a real machine row, owned by the demo owner, bound to demo-guests
    #     AND ONLY to demo-guests.
    pool_id = _guest_pool(db, owner_id)
    assert pool_id is not None
    with db.cursor() as cur:
        cur.execute(
            "select m.id, m.owner_id, m.status from public.machines m"
            " join public.machine_pools mp on mp.machine_id = m.id"
            " where mp.pool_id = %s::uuid",
            (pool_id,),
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert str(rows[0]["owner_id"]) == owner_id
    machine_id = str(rows[0]["id"])

    # (c) THE POOL SPLIT. A judge's laptop must never be able to claim a task
    #     of the measured nine-task sweep, which is scoped to Test-1.
    assert dbmod.pool_ids_for_machine(db, machine_id) == [pool_id]
    assert demomod.DEMO_POOL_ID not in dbmod.pool_ids_for_machine(db, machine_id)


def test_join_lets_the_agent_redeem_its_own_token_unchanged(make_client, db):
    """NOTHING HERE MINTS A TOKEN.

    The public route approves; the AGENT still exchanges its own device_code
    for its own token through `redeem_device_code`, exactly as it does after a
    console approval. If `join` had hand-rolled the approval, this redemption
    would come back None.
    """
    _demo_ready(db)
    client = make_client()
    code = _machine_code(db)

    assert _join(client, code["user_code"]).status_code == 201

    token = enrolment.redeem_device_code(db, code["device_code"])
    assert token, "the agent could not redeem the code the demo approved"
    machine = enrolment.authenticate_machine(db, token)
    assert machine is not None
    assert machine.name == JUDGE_HOSTNAME
    # Exactly once, still. The public route did not change that.
    assert enrolment.redeem_device_code(db, code["device_code"]) is None


def test_join_normalises_what_a_judge_actually_types(make_client, db):
    """Dashes, lowercase and a trailing space are not a different code."""
    _demo_ready(db)
    client = make_client()
    code = _machine_code(db)
    typed = f" {code['user_code'][:4].lower()}-{code['user_code'][4:].lower()} "

    r = _join(client, typed)

    assert r.status_code == 201, r.text


def test_join_refuses_an_expired_code_without_enrolling_anything(
    make_client, db
):
    owner_id = _demo_ready(db)
    client = make_client()
    code = _machine_code(db)
    _expire(db, code["user_code"])

    r = _join(client, code["user_code"])

    assert r.status_code == 404, r.text
    assert "expired" in r.json()["detail"]
    # And the refusal left nothing behind: no pool full of a machine that was
    # never approved.
    pool_id = _guest_pool(db, owner_id)
    if pool_id is not None:
        with db.cursor() as cur:
            cur.execute(
                "select count(*) as n from public.machine_pools"
                " where pool_id = %s::uuid",
                (pool_id,),
            )
            assert cur.fetchone()["n"] == 0


def test_join_refuses_a_cli_login_code(make_client, db):
    """A different flow. It mints a credential for a PERSON, not a worker for
    a machine, and accepting it while doing something else would be worse than
    refusing it."""
    _demo_ready(db)
    client = make_client()
    code = cli_auth.start_cli_code(db, "a judge's laptop")

    r = _join(client, code["user_code"])

    assert r.status_code == 400, r.text
    assert "flashnode login" in r.json()["detail"]


def test_join_cannot_adopt_a_machine_somebody_else_already_approved(
    make_client, db
):
    """`approve_device_code` short-circuits on an already-approved code and
    returns its machine_id WITH NO OWNERSHIP CHECK — correct there, so that
    re-approving your own code stays a no-op, and a hole here without the
    route's own owner check: anybody who learned a redeemed user_code could
    bind that stranger's machine into a public pool."""
    owner_id = _demo_ready(db)
    stranger = _new_user(db)
    client = make_client()
    code = _machine_code(db)
    # The stranger approves it first, through the ordinary path.
    stranger_machine = enrolment.approve_device_code(
        db, code["user_code"], stranger
    )

    r = _join(client, code["user_code"])

    assert r.status_code == 404, r.text
    # Same fold as every other pool/machine lookup: a distinguishable answer
    # would tell a prober which codes are real.
    assert r.json()["detail"] == "unknown code"
    # And the stranger's machine is where it was.
    assert dbmod.pool_ids_for_machine(db, str(stranger_machine)) == []
    pool_id = _guest_pool(db, owner_id)
    if pool_id is not None:
        with db.cursor() as cur:
            cur.execute(
                "select count(*) as n from public.machine_pools"
                " where pool_id = %s::uuid",
                (pool_id,),
            )
            assert cur.fetchone()["n"] == 0


def test_join_carries_no_pool_key_a_caller_could_aim(make_client, db):
    """The body carries a code and NOTHING ELSE.

    There is no caller identity here to scope a pool against, so the console
    route's `fetch_pool_for_member` check has no counterpart — which is why
    the key does not exist rather than being validated.
    """
    owner_id = _demo_ready(db)
    client = make_client()
    code = _machine_code(db)
    with db.cursor() as cur:
        cur.execute(
            "insert into public.pools (id, name, owner_id) values"
            " (%s::uuid, 'Test-1', %s::uuid) on conflict (id) do nothing",
            (demomod.DEMO_POOL_ID, owner_id),
        )

    r = client.post(
        "/v1alpha1/public/demo/join",
        json={
            "user_code": code["user_code"],
            "pool_id": demomod.DEMO_POOL_ID,
            "pool": demomod.DEMO_POOL_ID,
            "owner_id": str(uuid.uuid4()),
        },
        headers=NO_AUTH,
    )

    assert r.status_code == 201, r.text
    guest_pool = _guest_pool(db, owner_id)
    with db.cursor() as cur:
        cur.execute(
            "select mp.pool_id from public.machine_pools mp"
            " join public.machines m on m.id = mp.machine_id"
            " where m.node_id = %s",
            (r.json()["machine"]["node_id"],),
        )
        bound = [str(row["pool_id"]) for row in cur.fetchall()]
    assert bound == [guest_pool]


# ---------------------------------------------------------------------------
# 2. seeing yourself: the `guests` fleet
# ---------------------------------------------------------------------------


def test_the_read_shows_a_joined_machine_without_naming_it(make_client, db):
    _demo_ready(db)
    client = make_client()
    code = _machine_code(db)
    joined = _join(client, code["user_code"])
    assert joined.status_code == 201, joined.text
    # By node_id, not by name: earlier tests in this session left machines
    # with the same hostname behind, and `machines.node_id` is the globally
    # unique one.
    with db.cursor() as cur:
        cur.execute(
            "select id from public.machines where node_id = %s",
            (joined.json()["machine"]["node_id"],),
        )
        machine_id = str(cur.fetchone()["id"])
    _online(db, machine_id, code["device_code"])

    body = _read(client).json()

    # The envelope is FIXED — every key always, never conditional.
    assert set(body) == {"fleet", "runs", "guests", "guest_run"}
    assert len(body["guests"]) == 1
    guest = body["guests"][0]
    assert guest["online"] is True
    assert guest["official"] is False
    assert guest["cpus"] == 8
    # PROPERTY 5. A judge's laptop is not `official`, so `network._label`
    # anonymises it — and it does so without this route owning a branch.
    assert guest["name"].startswith("prov")
    assert JUDGE_HOSTNAME not in json.dumps(body)


def test_the_read_has_an_empty_guest_fleet_before_anybody_joins(
    make_client, db
):
    """`[]` and `null`, not absent keys and not a 500. And the GET must not
    have CREATED the pool on its way to answering — it is a read."""
    owner_id = _demo_ready(db)
    client = make_client()

    body = _read(client).json()

    assert body["guests"] == []
    assert body["guest_run"] is None
    assert _guest_pool(db, owner_id) is None


# ---------------------------------------------------------------------------
# 3. running on your own machine
# ---------------------------------------------------------------------------


def test_run_mine_refuses_before_anybody_has_joined(make_client, db):
    _demo_ready(db)
    client = make_client()

    r = _run_mine(client)

    assert r.status_code == 503, r.text
    assert "flashnode login" in r.json()["detail"]
    # Nothing was fetched, compiled or submitted.
    assert client.fetch.calls == []


def test_run_mine_targets_the_guest_pool_and_not_the_anchors(
    make_client, db, transport
):
    """THE ASSERTION THIS WHOLE FEATURE IS FOR.

    `placement.pool` is what the coordinator's seventh gate matches against a
    claiming node's `capabilities.pools`, and this API stamps those from the
    machine's own bindings. A guest job carrying Test-1 would run on our
    anchors and prove nothing; a sweep carrying `demo-guests` would corrupt
    the measurement. So: the guest job names the guest pool, and only it.
    """
    owner_id = _demo_ready(db)
    client = make_client()
    code = _machine_code(db)
    assert _join(client, code["user_code"]).status_code == 201
    pool_id = _guest_pool(db, owner_id)

    r = _run_mine(client)

    assert r.status_code == 201, r.text
    job_id = r.json()["job_id"]
    assert r.json()["coordinator"] == "render"

    specs = _submitted_specs(transport)
    assert len(specs) == 1
    placement = specs[0]["spec"]["placement"]
    assert placement["pool"] == pool_id
    assert placement["pool"] != demomod.DEMO_POOL_ID
    # allowFallback iff pool — the invariant `compile.py` states both ways.
    assert specs[0]["spec"]["isolation"]["allowFallback"] is True

    # The row agrees with the spec.
    with db.cursor() as cur:
        cur.execute("select * from public.jobs where id = %s", (job_id,))
        row = cur.fetchone()
    assert str(row["pool_id"]) == pool_id
    assert demomod.is_guest_source(row["source"]) is True
    # And NOT a demo-marked run: the venue comparison is the anchors' story,
    # and a guest run carrying the demo marker would displace the sweep from
    # it.
    assert demomod.is_demo_source(row["source"]) is False


def test_run_mine_does_not_declare_itself_amd64_only(
    make_client, db, transport
):
    """PROPERTY 6, AND THE ONE THAT DECIDES WHETHER THIS SHIPS.

    `PlacementSpec.architectures` defaults to `["amd64"]` in the pinned
    protocol and `compile_to_jobspec` returns a full `model_dump`, so every
    job this API has ever compiled says amd64-only in so many words. Most
    hackathon judges are on Apple Silicon. Nothing in flashruntime 0.6.1's
    scheduler reads the field today — but a demo whose correctness rests on a
    declared field being ignored is a demo that breaks on the release that
    stops ignoring it.
    """
    _demo_ready(db)
    client = make_client()
    code = _machine_code(db)
    assert _join(client, code["user_code"]).status_code == 201

    assert _run_mine(client).status_code == 201

    architectures = _submitted_specs(transport)[0]["spec"]["placement"][
        "architectures"
    ]
    assert "arm64" in architectures, architectures
    assert architectures != ["amd64"]


def test_run_mine_uses_the_one_task_hello_world_ref(make_client, db):
    """`main`, not `sweep`. One task, seconds, `python-slim`.

    The nine-task grid is the anchors' half and already makes the parallelism
    argument on hardware guaranteed to be there. What a judge's own laptop has
    to do is FINISH, quickly, while they are watching.
    """
    _demo_ready(db)
    client = make_client()
    code = _machine_code(db)
    assert _join(client, code["user_code"]).status_code == 201

    assert _run_mine(client).status_code == 201

    assert len(client.fetch.calls) == 1
    call = client.fetch.calls[0]
    assert (call.owner, call.name, call.ref) == (
        "Zolli-Labs", "flashml-examples", "main",
    )
    # A public repository: no installation token, ever, on this path.
    assert call.token is None


def test_run_mine_ignores_everything_in_the_body(make_client, db, transport):
    """The caller controls NOTHING — not even the venue, which is the one knob
    its sibling `run` offers."""
    owner_id = _demo_ready(db)
    client = make_client()
    code = _machine_code(db)
    assert _join(client, code["user_code"]).status_code == 201
    pool_id = _guest_pool(db, owner_id)

    r = _run_mine(
        client,
        coordinator="fc",
        pool=demomod.DEMO_POOL_ID,
        repo="attacker/evil",
        owner="attacker",
        ref="main",
        image="pytorch-cuda",
    )

    assert r.status_code == 201, r.text
    assert r.json()["coordinator"] == "render"
    assert transport.job_submissions_on("fc") == []
    assert _submitted_specs(transport)[0]["spec"]["placement"]["pool"] == pool_id
    assert client.fetch.calls[0].owner == "Zolli-Labs"


def test_a_second_press_joins_the_first_run(make_client, db, transport):
    _demo_ready(db)
    client = make_client()
    code = _machine_code(db)
    assert _join(client, code["user_code"]).status_code == 201

    first = _run_mine(client)
    assert first.status_code == 201, first.text
    second = _run_mine(client)

    assert second.status_code == 200, second.text
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.json()["already_running"] is True
    # The assertion that matters: no second submission on the coordinator.
    assert len(transport.job_submissions_on("render")) == 1


def test_a_finished_run_does_not_block_the_button_for_ever(
    make_client, db, transport
):
    """The gate is decided against what the COORDINATOR says, not against the
    status column recorded at submit."""
    _demo_ready(db)
    client = make_client()
    code = _machine_code(db)
    assert _join(client, code["user_code"]).status_code == 201
    first = _run_mine(client).json()["job_id"]
    transport.set_state("render", first, "SUCCEEDED")

    second = _run_mine(client)

    assert second.status_code == 201, second.text
    assert second.json()["job_id"] != first
    assert len(transport.job_submissions_on("render")) == 2


def test_the_read_renders_the_guest_run(make_client, db, transport):
    """A judge who pressed Run has something to watch, and its `finished_at`
    is the one the DATABASE holds after the observation — not a local guess,
    because `elapsed_s` is the whole point of the card."""
    _demo_ready(db)
    client = make_client()
    code = _machine_code(db)
    assert _join(client, code["user_code"]).status_code == 201
    job_id = _run_mine(client).json()["job_id"]
    transport.set_state("render", job_id, "SUCCEEDED")

    body = _read(client).json()

    assert body["guest_run"] is not None
    assert body["guest_run"]["job_id"] == job_id
    assert body["guest_run"]["state"] == "SUCCEEDED"
    assert body["guest_run"]["finished_at"] is not None
    assert body["guest_run"]["elapsed_s"] is not None
    # And the guest run is NOT one of the venue-comparison runs.
    assert [r["job_id"] for r in body["runs"]] == []


# ---------------------------------------------------------------------------
# 4. no credential, on either route
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/v1alpha1/public/demo/join", "/v1alpha1/public/demo/run-mine"]
)
def test_neither_route_ever_asks_for_a_credential(make_client, db, path):
    """Not 401, not 403. A judge's browser sends nothing and gets an answer
    about the thing they asked about."""
    _demo_ready(db)
    client = make_client()

    r = client.post(path, json={"user_code": "ZZZZZZZZ"}, headers=NO_AUTH)

    assert r.status_code not in (401, 403), r.text
