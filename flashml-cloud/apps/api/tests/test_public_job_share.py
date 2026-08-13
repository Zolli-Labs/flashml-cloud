"""The public run page for a JOB: what it publishes, and what it must not.

`GET /v1alpha1/public/share/{share_token}` is the second route in this API
that answers with no credential at all, and the first that answers about work
somebody else's code performed. That difference is the whole file. AS-16 puts
it in one line:

    A session's output is ours; a job's output is theirs.

We execute user-supplied code from a submitted repository, so a payload built
by widening `SESSION_SHARE_COLUMNS` to cover a job would publish the repo URL,
the spec, dataset paths, artifact keys, the owner, and — through
`machines.name`, which is the hostname a host supplied at enrolment — a
volunteer's own name. `flashml_cloud_api/job_share.py` carries the reasoning
per field; this file is the part that fails when the reasoning stops being
true.

**Four properties carry the weight here.**

- **A withheld field is ABSENT, asserted by name.** Not "redacted", not empty:
  absent. The tests below check individual field names AND search the raw
  response bytes for the values themselves, because a refactor that
  reintroduces a hostname under a different key passes the first check and
  fails the second — and it is the second one that matters.
- **Minting is idempotent.** A second token would not add a link; it would
  invalidate one somebody has already sent.
- **The three misses are byte-identical.** Unknown, the other kind, and
  revoked. A discriminated resolver is exactly where those want to diverge,
  which is why AS-16 wrote it down before it was built — and why the
  assertions here compare `.content`, not status codes.
- **The existing session share route is untouched.** This work is additive; a
  regression there is a regression in the shipped disqualifier insurance.

Fixture wiring follows `test_cli_token_routes.py`: the shared client/db/JWT
helpers live in `test_jobs_from_repo` and are imported from there. Two are
overridden locally and each for a reason — `settings`, because the session
half of the resolver is gated on the FC sandbox being configured, and
`transport`, because the coordinator's event ledger is part of the payload and
`test_jobs_from_repo`'s fake answers 404 to every read.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import job_share as jsmod
from flashml_cloud_api import sandbox_sessions as ss
from flashml_cloud_api.settings import Settings

from test_jobs_from_repo import (  # noqa: F401 - fixtures
    JWT_SECRET,
    COORDINATOR_URL,
    OPERATOR_TOKEN,
    _jwt,
    _new_user,
    db,
    make_client,
)

REGION = "ap-southeast-1"

#: A hostname of exactly the shape this page must never publish: a person's
#: name, supplied by their own agent at enrolment, stored verbatim in
#: `machines.name` (`enrolment.py`, `name=row["hostname"]`).
VOLUNTEER_HOSTNAME = "phong-macbook-air.local"
SECOND_HOSTNAME = "marias-gaming-rig"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


class LedgerCoordinator(httpx.AsyncBaseTransport):
    """Enough coordinator to answer one job's event ledger.

    The events carry a `message` and a `data` dict shaped exactly like the
    real ones (`flashruntime.leases` emits `{"task_id", "node_id"}`, and
    `TASK_ATTEMPT_FAILED`'s message is the worker's own failure reason), so
    the leak assertions below have something real to find.
    """

    def __init__(self) -> None:
        self.events: dict[str, list[dict]] = {}
        self.requests: list[httpx.Request] = []
        #: Set to make every ledger read fail, for the degrade-not-break test.
        self.broken = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        path = request.url.path
        prefix, suffix = "/v1alpha1/jobs/", "/events"
        if request.method == "GET" and path.startswith(prefix) \
                and path.endswith(suffix):
            if self.broken:
                return httpx.Response(503, json={"detail": "unavailable"})
            job_id = path[len(prefix):-len(suffix)]
            return httpx.Response(200, json=self.events.get(job_id, []))
        return httpx.Response(404, json={"detail": f"unhandled: {path}"})


@pytest.fixture
def transport() -> LedgerCoordinator:
    return LedgerCoordinator()


@pytest.fixture(scope="module")
def settings(postgres_dsn) -> Settings:
    """`test_jobs_from_repo`'s settings plus a configured FC sandbox.

    The resolver's session branch is gated on `fc_sandbox_configured`,
    deliberately and exactly as the sibling `/public/sandbox-sessions` route
    is: two routes reading one row must not disagree about whether it is
    readable. So a suite that left the sandbox unconfigured could not tell a
    working session share from a broken one.
    """
    return Settings(
        supabase_url="https://yualksqjjvlfscbbsygq.supabase.co",
        supabase_jwt_secret=JWT_SECRET,
        supabase_service_key="service-key-not-used-here",
        coordinator_url=COORDINATOR_URL,
        coordinator_operator_token=OPERATOR_TOKEN,
        require_auth=True,
        database_url=postgres_dsn,
        console_url="https://console.example",
        fc_sandbox_api_key="not-a-real-key",
        fc_sandbox_api_url=f"https://api.{REGION}.e2b.fc.aliyuncs.com",
        fc_sandbox_domain=f"{REGION}.e2b.fc.aliyuncs.com",
        fc_sandbox_region=REGION,
        fc_sandbox_template="code-interpreter-v1",
        fc_sandbox_pool_id=str(uuid.uuid4()),
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_jwt(user_id)}"}


def _machine(db, owner_id: str, hostname: str) -> str:
    """An enrolled machine whose display name IS its hostname — which is what
    `enrolment.approve_device_code` writes, and the reason a hostname can
    reach a payload through a key nobody thinks of as user input."""
    machine_id = dbmod.insert_machine(
        db,
        owner_id=owner_id,
        node_id=f"node-{uuid.uuid4().hex[:10]}",
        name=hostname,
        platform="Linux-6.8.0-x86_64",
    )
    # The leak assertions below are only worth anything if the value is really
    # on the row they would have to reach through. Checked here once rather
    # than restated in every test that searches for it.
    with db.cursor() as cur:
        cur.execute("select name from public.machines where id = %s::uuid",
                    (machine_id,))
        assert cur.fetchone()["name"] == hostname
    return machine_id


#: Everything about this job that belongs to the submitter. Every value here is
#: searched for in the raw response bytes.
JOB_NAME = "quarterly-revenue-forecast"
REPO_URL = "https://github.com/acme-internal/private-trainer"
REPO_REF = "feature/unreleased-model"
DATASET_PATH = "s3://acme-private/customers-2026.parquet"
IMAGE = "ghcr.io/acme-internal/trainer:secret-tag"


def _job(db, owner_id: str, *, status: str = "SUCCEEDED") -> str:
    job_id = uuid.uuid4().hex[:12]
    dbmod.insert_job(
        db,
        job_id=job_id,
        owner_id=owner_id,
        name=JOB_NAME,
        source={"repo": REPO_URL, "ref": REPO_REF, "mode": "repo"},
        spec={"image": IMAGE, "command": ["python", "train.py"],
              "inputs": {"data": DATASET_PATH}},
        status=status,
    )
    return job_id


def _attempt(db, *, job_id: str, machine_id: str, task_id: str,
             outcome: str | None) -> str:
    """One lease claimed, and resolved through the real writers.

    `claim_attempt_credit` and `record_attempt_failure` are the two functions
    that ever write an outcome from a request; `expired` has no request behind
    it by definition (the machine told nobody), so it is written the way
    `reconcile_expired_attempts` writes it — from the lease deadline.
    """
    lease_id = f"lease-{uuid.uuid4().hex[:12]}"
    dbmod.record_attempt(
        db, lease_id=lease_id, machine_id=machine_id, job_id=job_id,
        task_id=task_id,
    )
    if outcome == "accepted":
        assert dbmod.claim_attempt_credit(
            db, lease_id=lease_id, machine_id=machine_id
        ) is not None
    elif outcome == "failed":
        assert dbmod.record_attempt_failure(
            db, lease_id=lease_id, machine_id=machine_id
        ) is True
    elif outcome == "expired":
        db.execute(
            "update public.attempts set outcome = 'expired',"
            " resolved_at = now() where lease_id = %s",
            (lease_id,),
        )
    elif outcome is not None:
        raise AssertionError(f"unhandled outcome {outcome!r}")
    return lease_id


def _shared_job(client, db, owner_id: str) -> tuple[str, str]:
    """A job with the full fault-tolerance story on it, and a share link.

    Two machines, one task claimed twice — expired on the first, accepted on
    the second — which is exactly the sentence the public page exists to
    support, and it means every pseudonym, every outcome and every count has
    something real behind it.
    """
    job_id = _job(db, owner_id)
    dying = _machine(db, owner_id, VOLUNTEER_HOSTNAME)
    rescuer = _machine(db, owner_id, SECOND_HOSTNAME)
    _attempt(db, job_id=job_id, machine_id=dying, task_id="shard-000",
             outcome="expired")
    _attempt(db, job_id=job_id, machine_id=rescuer, task_id="shard-000",
             outcome="accepted")
    _attempt(db, job_id=job_id, machine_id=rescuer, task_id="shard-001",
             outcome="failed")
    r = client.post(f"/v1alpha1/jobs/{job_id}/share", headers=_headers(owner_id))
    assert r.status_code == 200, r.text
    return job_id, r.json()["share_token"]


def _shared_session(client, db, owner_id: str) -> str:
    """A sandbox session with a share token, created through the module that
    owns them. The route is not used because it drives a real orchestrator;
    what this file needs is a row of the other kind."""
    pool = dbmod.create_pool(db, name=f"pool-{uuid.uuid4().hex[:8]}",
                             owner_id=owner_id)
    session = ss.create_session(
        db,
        owner_id=owner_id,
        pool_id=str(pool["id"]),
        training_job_id=_job(db, owner_id),
        region=REGION,
        template="code-interpreter-v1",
    )
    return str(session["share_token"])


# ---------------------------------------------------------------------------
# 1. minting
# ---------------------------------------------------------------------------


def test_a_job_has_no_share_token_until_somebody_asks(make_client, db):
    """A job is PRIVATE until the route is called. The opposite of a session,
    which mints at create time because a session exists to be evidence."""
    make_client()
    owner = _new_user(db)
    job_id = _job(db, owner)

    with db.cursor() as cur:
        cur.execute("select share_token from public.jobs where id = %s", (job_id,))
        assert cur.fetchone()["share_token"] is None


def test_minting_returns_a_token_and_a_console_url(make_client, db):
    client = make_client()
    owner = _new_user(db)
    job_id = _job(db, owner)

    body = client.post(
        f"/v1alpha1/jobs/{job_id}/share", headers=_headers(owner)
    ).json()

    assert body["share_token"].startswith("shr_")
    # The CONSOLE path, one segment, matching the anchored middleware rule
    # `/^\/share\/[A-Za-z0-9_-]{1,128}$/`. Not `/share/job/<token>`, which
    # would need that rule loosened.
    assert body["url"] == f"https://console.example/share/{body['share_token']}"


def test_the_minted_token_fits_the_middleware_pattern(make_client, db):
    """Pinned here rather than trusted: the console's matcher is anchored and
    one-segment, so a token containing a `/`, a `.` or a `+` would produce a
    URL that redirects to sign-in — a share link that silently does not
    share."""
    import re

    client = make_client()
    owner = _new_user(db)
    token = client.post(
        f"/v1alpha1/jobs/{_job(db, owner)}/share", headers=_headers(owner)
    ).json()["share_token"]

    assert re.fullmatch(r"[A-Za-z0-9_-]{1,128}", token)


def test_minting_twice_returns_the_same_token(make_client, db):
    """The property, not an optimisation: a second token would silently
    invalidate a link somebody has already sent."""
    client = make_client()
    owner = _new_user(db)
    job_id = _job(db, owner)

    first = client.post(
        f"/v1alpha1/jobs/{job_id}/share", headers=_headers(owner)
    ).json()
    second = client.post(
        f"/v1alpha1/jobs/{job_id}/share", headers=_headers(owner)
    ).json()

    assert first == second


def test_a_non_owner_cannot_mint_a_share_link(make_client, db):
    client = make_client()
    owner = _new_user(db)
    stranger = _new_user(db)
    job_id = _job(db, owner)

    r = client.post(f"/v1alpha1/jobs/{job_id}/share", headers=_headers(stranger))
    assert r.status_code == 404

    # And nothing was written: a refused mint that still minted would leave a
    # live link on somebody else's job.
    with db.cursor() as cur:
        cur.execute("select share_token from public.jobs where id = %s", (job_id,))
        assert cur.fetchone()["share_token"] is None


def test_a_stranger_and_an_unknown_job_are_indistinguishable(make_client, db):
    client = make_client()
    owner = _new_user(db)
    stranger = _new_user(db)

    theirs = client.post(
        f"/v1alpha1/jobs/{_job(db, owner)}/share", headers=_headers(stranger)
    )
    missing = client.post(
        f"/v1alpha1/jobs/{uuid.uuid4().hex[:12]}/share", headers=_headers(stranger)
    )
    assert theirs.status_code == missing.status_code == 404
    assert theirs.content == missing.content


def test_minting_refuses_a_signed_out_caller(make_client, db):
    client = make_client()
    owner = _new_user(db)
    assert client.post(
        f"/v1alpha1/jobs/{_job(db, owner)}/share"
    ).status_code == 401


def test_a_pool_member_who_can_read_the_job_still_cannot_share_it(
    make_client, db
):
    """`fetch_job_for_viewer` admits every member of the job's pool. Publishing
    a teammate's run to the open internet is not a read, so the mint route
    scopes on the OWNER and the gap between the two is deliberate."""
    client = make_client()
    owner = _new_user(db)
    teammate = _new_user(db)
    pool = dbmod.create_pool(db, name=f"pool-{uuid.uuid4().hex[:8]}",
                             owner_id=owner)
    db.execute(
        "insert into public.pool_members (pool_id, user_id) values (%s, %s)",
        (pool["id"], teammate),
    )
    job_id = uuid.uuid4().hex[:12]
    dbmod.insert_job(db, job_id=job_id, owner_id=owner, name=JOB_NAME,
                     source=None, spec=None, status="SUCCEEDED",
                     pool_id=str(pool["id"]))

    # The teammate really can read it...
    assert dbmod.fetch_job_for_viewer(db, job_id, teammate) is not None
    # ...and still cannot publish it.
    assert client.post(
        f"/v1alpha1/jobs/{job_id}/share", headers=_headers(teammate)
    ).status_code == 404


# ---------------------------------------------------------------------------
# 2. the public payload — what it publishes
# ---------------------------------------------------------------------------


def test_the_public_job_page_is_reachable_signed_out(make_client, db):
    """G-1's whole requirement: a live URL that opens without a login, on a
    product where every console route redirects to sign-in. Paired with the
    authenticated read of the SAME job, because the property is the difference
    between the two."""
    client = make_client()
    job_id, token = _shared_job(client, db, _new_user(db))

    public = client.get(f"/v1alpha1/public/share/{token}")
    assert public.status_code == 200
    assert "authorization" not in {k.lower() for k in public.request.headers}

    assert client.get(f"/v1alpha1/jobs/{job_id}").status_code == 401


def test_the_payload_is_discriminated_and_its_envelope_is_fixed(
    make_client, db
):
    client = make_client()
    _, token = _shared_job(client, db, _new_user(db))

    body = client.get(f"/v1alpha1/public/share/{token}").json()
    assert body["kind"] == "job"
    assert set(body) == {"kind", "job", "attempts", "events"}


def test_a_session_token_resolves_through_the_same_url(make_client, db):
    """One token space, two kinds of thing — and the consumer branches on a
    field the API states rather than sniffing which keys arrived."""
    client = make_client()
    token = _shared_session(client, db, _new_user(db))

    body = client.get(f"/v1alpha1/public/share/{token}").json()
    assert body["kind"] == "session"
    assert set(body) == {"kind", "session", "events"}


def test_the_job_view_publishes_state_timings_and_counts(make_client, db):
    client = make_client()
    job_id, token = _shared_job(client, db, _new_user(db))

    job = client.get(f"/v1alpha1/public/share/{token}").json()["job"]

    assert job["job_id"] == job_id
    assert job["state"] == "SUCCEEDED"
    assert job["created_at"]
    # Two tasks, three leases, two machines: attempted and accepted counted
    # separately, which is hard rule 4 on the one page where collapsing them
    # would make every finished job look flawless.
    assert job["tasks_total"] == 2
    assert job["tasks_accepted"] == 1
    assert job["machines_claiming"] == 2
    assert job["attempts_total"] == 3
    assert job["attempts_accepted"] == 1
    assert job["attempts_failed"] == 1
    assert job["attempts_expired"] == 1
    assert job["attempts_unresolved"] == 0


def test_the_attempts_tell_the_recovery_story_with_pseudonyms(make_client, db):
    """"machine A stopped, machine B resumed" — assigned by us, from a
    position, carrying nothing from either host."""
    client = make_client()
    _, token = _shared_job(client, db, _new_user(db))

    attempts = client.get(f"/v1alpha1/public/share/{token}").json()["attempts"]

    assert [a["machine"] for a in attempts] == [
        "machine A", "machine B", "machine B",
    ]
    assert {a["task"] for a in attempts} == {"task 1", "task 2"}
    assert [a["outcome"] for a in attempts] == ["expired", "accepted", "failed"]
    for a in attempts:
        assert set(a) == {"machine", "task", "claimed_at", "resolved_at",
                          "outcome", "duration_s"}


def test_the_ledger_publishes_kind_and_position_and_nothing_else(
    make_client, db, transport
):
    client = make_client()
    job_id, token = _shared_job(client, db, _new_user(db))
    transport.events[job_id] = [
        {"job_id": job_id, "type": "LEASE_CLAIMED", "source": "flashruntime.leases",
         "timestamp": "2026-08-12T10:00:00+00:00", "message": "claimed",
         "data": {"task_id": "shard-000", "node_id": "node-secret"}},
        {"job_id": job_id, "type": "TASK_ATTEMPT_FAILED",
         "source": "flashruntime.leases",
         "timestamp": "2026-08-12T10:05:00+00:00",
         "message": "Traceback: KeyError('customer_ssn')",
         "data": {"task_id": "shard-000", "node_id": "node-secret"}},
        {"job_id": job_id, "type": "RECOVERY_ACTION_SELECTED",
         "source": "flashruntime.recovery",
         "timestamp": "2026-08-12T10:05:01+00:00",
         "message": "attempt-2: RETRY_ELSEWHERE — exit 137 looks like OOM",
         "data": {}},
    ]

    events = client.get(f"/v1alpha1/public/share/{token}").json()["events"]

    assert [e["kind"] for e in events] == [
        "LEASE_CLAIMED", "TASK_ATTEMPT_FAILED", "RECOVERY_ACTION_SELECTED",
    ]
    assert [e["seq"] for e in events] == [1, 2, 3]
    for e in events:
        # No timestamp. `seq` already carries ordering and is a position we
        # compute; an attempt's claimed_at/resolved_at already carry a clock
        # from our own table. The wire timestamp duplicated both while being
        # of uncertain origin (`Event.source` names "flashnode"), so it could
        # only agree — adding nothing — or disagree, and a skewed host clock
        # rendering an event dated next year discredits the page it sits on.
        assert set(e) == {"seq", "kind"}


def test_an_unrecognised_event_kind_is_dropped_entirely(make_client, db,
                                                        transport):
    """The allowlist is the safety property: a kind added upstream — or one a
    coordinator invented — is withheld until somebody reads it."""
    client = make_client()
    job_id, token = _shared_job(client, db, _new_user(db))
    transport.events[job_id] = [
        {"type": "JOB_ACCEPTED", "timestamp": "2026-08-12T10:00:00+00:00"},
        {"type": "SOMETHING_NEW_UPSTREAM", "message": "whatever it printed",
         "timestamp": "2026-08-12T10:00:01+00:00"},
    ]

    events = client.get(f"/v1alpha1/public/share/{token}").json()["events"]
    assert [e["kind"] for e in events] == ["JOB_ACCEPTED"]
    # And the seq is dense over what was PUBLISHED: the coordinator's own index
    # would leak how many events this page declined to describe.
    assert [e["seq"] for e in events] == [1]


def test_a_dead_coordinator_degrades_the_page_and_does_not_break_it(
    make_client, db, transport
):
    """The evidence that matters most is in our own Postgres. A coordinator
    that is down must cost the ledger section, never the page."""
    client = make_client()
    _, token = _shared_job(client, db, _new_user(db))
    transport.broken = True

    r = client.get(f"/v1alpha1/public/share/{token}")
    assert r.status_code == 200
    assert r.json()["events"] == []
    assert r.json()["attempts"]


# ---------------------------------------------------------------------------
# 3. the public payload — what it WITHHOLDS
#
# Two layers of assertion on purpose. The by-name checks pin the contract; the
# raw-bytes checks pin the property, and catch a value reintroduced under a key
# nobody thought to look at.
# ---------------------------------------------------------------------------


def test_the_public_job_page_leaks_no_machine_hostname(make_client, db,
                                                       transport):
    """`machines.name` IS the hostname the agent supplied at enrolment
    (`enrolment.py`: `name=row["hostname"]`), and volunteer hostnames are
    routinely personal. Asserted against the SERIALIZED response rather than
    against a field name, so a refactor that reintroduces it under another key
    still fails."""
    client = make_client()
    job_id, token = _shared_job(client, db, _new_user(db))

    raw = client.get(f"/v1alpha1/public/share/{token}").text

    assert VOLUNTEER_HOSTNAME not in raw
    assert SECOND_HOSTNAME not in raw
    assert "macbook" not in raw.lower()


def test_the_public_job_page_leaks_nothing_the_submitter_authored(
    make_client, db, transport
):
    client = make_client()
    owner = _new_user(db)
    job_id, token = _shared_job(client, db, owner)
    transport.events[job_id] = [
        {"job_id": job_id, "type": "TASK_ATTEMPT_FAILED",
         "source": "flashruntime.leases",
         "timestamp": "2026-08-12T10:05:00+00:00",
         "message": "Traceback: KeyError('customer_ssn')",
         "data": {"task_id": "shard-000", "node_id": "node-secret-identity"}},
    ]

    r = client.get(f"/v1alpha1/public/share/{token}")
    raw = r.text
    # Not vacuous: the ledger really is in the bytes being searched, so the
    # message and the `data` dict below had a chance to reach them.
    assert [e["kind"] for e in r.json()["events"]] == ["TASK_ATTEMPT_FAILED"]
    assert r.json()["attempts"]

    for secret in (
        JOB_NAME,                 # the submitter named their own run
        REPO_URL,                 # the URL alone confirms a private repo
        REPO_REF,
        DATASET_PATH,             # where their data lives
        IMAGE,                    # what they were running
        owner,                    # who owns it
        token,                    # the capability itself
        VOLUNTEER_HOSTNAME,       # whose machine
        "customer_ssn",           # what their code printed
        "node-secret-identity",   # an event `data` value
        "shard-000",              # a raw task id, replaced by a pseudonym
    ):
        assert secret not in raw, f"the public page leaked {secret!r}"


def test_the_public_view_serves_only_the_share_columns(make_client, db):
    """The security property is the column list, so the column list is what is
    asserted — and the withheld set is spelled out rather than derived, so
    quietly dropping a column OUT of the withheld set fails too."""
    client = make_client()
    _, token = _shared_job(client, db, _new_user(db))

    job = client.get(f"/v1alpha1/public/share/{token}").json()["job"]

    with db.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns"
            " where table_schema = 'public' and table_name = 'jobs'"
        )
        every_column = {r["column_name"] for r in cur.fetchall()}

    withheld = every_column - set(jsmod.JOB_SHARE_COLUMNS)
    assert withheld == {
        "name", "source", "spec", "owner_id", "pool_id", "share_token",
        "artifact_bytes", "artifact_bytes_recorded_at", "artifacts_mirrored_at",
        # 0026. Ours, so it passes Rule 7's provenance question — and withheld
        # on Rule 7's second one: it is the single value that joins this job to
        # every other piece of work on the same thread, so publishing it to a
        # link-holder would hand out the correlation this column exists to make
        # possible internally.
        "correlation_id",
    }
    assert not (withheld & set(job)), "a withheld column reached the page"
    # `status` is published under the name `state`, and `id` under `job_id`:
    # the wire never carries a column name that would invite `select *`.
    assert "status" not in job and "id" not in job


def test_no_attempt_field_names_a_machine_a_task_or_a_lease(make_client, db):
    client = make_client()
    job_id, token = _shared_job(client, db, _new_user(db))

    body = client.get(f"/v1alpha1/public/share/{token}").json()

    # SCOPED TO THIS JOB, and the scoping is the point rather than tidiness.
    #
    # Unscoped, this read every attempt row in the database and asserted none
    # of those values appeared in THIS job's page. `conftest.py` runs one
    # session-scoped Postgres and never truncates between files, so rows left
    # by unrelated modules were visible here — and a short or common
    # `task_id` from one of them collides by substring with something the
    # page legitimately renders. The test then failed for a job that had
    # leaked nothing.
    #
    # It failed in the full suite and passed alone, which is the dangerous
    # shape for an assertion like this one: it does not merely fail
    # sometimes, it PASSES sometimes, and the run it passes on is the one
    # somebody ships from. For a check whose job is keeping a volunteer's
    # hostname off an unauthenticated page, an unreliable green is worse than
    # a red — a red stops you.
    #
    # Scoping loses nothing. This page renders exactly one job, so only that
    # job's identifiers are reachable to leak; another job's ids cannot
    # escape through a route that never reads them.
    with db.cursor() as cur:
        cur.execute(
            "select lease_id, machine_id, task_id from public.attempts "
            "where job_id = %s",
            (job_id,),
        )
        rows = cur.fetchall()
    assert rows, "the fixture stopped producing attempts for this job"
    raw = client.get(f"/v1alpha1/public/share/{token}").text
    for row in rows:
        for value in (row["lease_id"], str(row["machine_id"]), row["task_id"]):
            assert value not in raw, f"the public page leaked {value!r}"

    for attempt in body["attempts"]:
        assert "machine_id" not in attempt
        assert "task_id" not in attempt
        assert "lease_id" not in attempt
        assert "machine_ordinal" not in attempt
        assert "task_ordinal" not in attempt


def test_no_checkpoint_step_number_is_published(make_client, db, transport):
    """AS-16.1's headline disqualification. `"resumed at step 16298"` looks
    like our integer and is not: `preflight.py` globs `step-*.json`, so the
    step is parsed from a filename the task's code writes. The event KIND is
    ours and is published; the number is theirs and is not."""
    client = make_client()
    job_id, token = _shared_job(client, db, _new_user(db))
    transport.events[job_id] = [
        {"job_id": job_id, "type": "CHECKPOINT_MANIFEST_COMMITTED",
         "source": "flashruntime.checkpoint",
         "timestamp": "2026-08-12T10:04:00+00:00",
         "message": "step 16298 committed",
         "data": {"step": 16298, "manifest_id": "mf-abc"}},
    ]

    raw = client.get(f"/v1alpha1/public/share/{token}").text

    assert "CHECKPOINT_MANIFEST_COMMITTED" in raw
    assert "16298" not in raw
    assert "mf-abc" not in raw


def test_no_geography_is_published_for_any_machine(make_client, db):
    """AS-16.2: provenance and re-identification are two different questions,
    and a region passes the first while failing the second for a volunteer's
    home rig. Nothing in this schema records one — this pins that nothing
    starts to."""
    client = make_client()
    _, token = _shared_job(client, db, _new_user(db))

    body = client.get(f"/v1alpha1/public/share/{token}").json()
    for attempt in body["attempts"]:
        assert "region" not in attempt
        assert "venue" not in attempt
        assert "country" not in attempt
    assert "region" not in body["job"]


# ---------------------------------------------------------------------------
# 4. the three misses answer identically
# ---------------------------------------------------------------------------


def test_unknown_wrong_kind_and_revoked_are_byte_identical(make_client, db):
    """The one property a discriminated resolver most wants to break. Compared
    on `.content`, not on status codes: two 404s with different bodies
    enumerate just as well as a 404 and a 403."""
    client = make_client()
    owner = _new_user(db)
    job_id, job_token = _shared_job(client, db, owner)

    # Revoked: the column goes back to NULL and the old link dies.
    assert client.delete(
        f"/v1alpha1/jobs/{job_id}/share", headers=_headers(owner)
    ).status_code == 204

    unknown = client.get(f"/v1alpha1/public/share/shr_{uuid.uuid4().hex}")
    revoked = client.get(f"/v1alpha1/public/share/{job_token}")
    # "exists, but is the other kind than this route was asked for" — a job
    # token handed to the SESSION route, which is the shape AS-16 names.
    wrong_kind_at_session_route = client.get(
        f"/v1alpha1/public/sandbox-sessions/{job_token}"
    )
    unknown_at_session_route = client.get(
        f"/v1alpha1/public/sandbox-sessions/shr_{uuid.uuid4().hex}"
    )

    assert unknown.status_code == revoked.status_code == 404
    assert unknown.content == revoked.content

    assert wrong_kind_at_session_route.status_code == 404
    assert (wrong_kind_at_session_route.content
            == unknown_at_session_route.content)


def test_a_live_session_token_is_not_readable_as_a_job(make_client, db):
    """The other direction of the same property: the resolver must not answer
    `kind: "job"` about a session, and a session token that resolves must
    resolve as a session or not at all."""
    client = make_client()
    token = _shared_session(client, db, _new_user(db))
    assert client.get(f"/v1alpha1/public/share/{token}").json()["kind"] == "session"


def test_an_empty_token_matches_nothing(make_client, db):
    client = make_client()
    _shared_job(client, db, _new_user(db))
    assert client.get("/v1alpha1/public/share/").status_code in (404, 405)


def test_a_revoked_link_stops_working_immediately(make_client, db):
    client = make_client()
    owner = _new_user(db)
    job_id, token = _shared_job(client, db, owner)

    assert client.get(f"/v1alpha1/public/share/{token}").status_code == 200
    client.delete(f"/v1alpha1/jobs/{job_id}/share", headers=_headers(owner))
    assert client.get(f"/v1alpha1/public/share/{token}").status_code == 404


def test_revoking_twice_is_not_an_error(make_client, db):
    """"The link does not work" is the state asked for, and it is already
    true. Revoking a job that was never shared succeeds for the same reason."""
    client = make_client()
    owner = _new_user(db)
    job_id, _ = _shared_job(client, db, owner)
    path = f"/v1alpha1/jobs/{job_id}/share"

    assert client.delete(path, headers=_headers(owner)).status_code == 204
    assert client.delete(path, headers=_headers(owner)).status_code == 204
    assert client.delete(
        f"/v1alpha1/jobs/{_job(db, owner)}/share", headers=_headers(owner)
    ).status_code == 204


def test_a_non_owner_cannot_revoke_a_share_link(make_client, db):
    client = make_client()
    owner = _new_user(db)
    stranger = _new_user(db)
    job_id, token = _shared_job(client, db, owner)

    assert client.delete(
        f"/v1alpha1/jobs/{job_id}/share", headers=_headers(stranger)
    ).status_code == 404
    # The link still works, which is the half a status-code-only assertion
    # would miss.
    assert client.get(f"/v1alpha1/public/share/{token}").status_code == 200


def test_a_minted_token_is_not_guessable_from_another(make_client, db):
    """256 bits from `secrets`, one minter shared with sandbox sessions. Two
    tokens minted back to back must share nothing but the prefix."""
    client = make_client()
    owner = _new_user(db)
    a = client.post(f"/v1alpha1/jobs/{_job(db, owner)}/share",
                    headers=_headers(owner)).json()["share_token"]
    b = client.post(f"/v1alpha1/jobs/{_job(db, owner)}/share",
                    headers=_headers(owner)).json()["share_token"]

    assert a != b
    assert a.startswith("shr_") and b.startswith("shr_")
    assert len(a) > 40


# ---------------------------------------------------------------------------
# 5. the route this work must not have broken
# ---------------------------------------------------------------------------


def test_the_existing_sandbox_session_share_route_still_works(make_client, db):
    """Additive, and this is the assertion that says so. The session page is
    the disqualifier insurance already in production."""
    client = make_client()
    token = _shared_session(client, db, _new_user(db))

    r = client.get(f"/v1alpha1/public/sandbox-sessions/{token}")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"session", "events"}
    assert body["session"]["region"] == REGION
    assert body["events"]


def test_both_routes_render_one_session_identically(make_client, db):
    """The resolver calls the sibling's renderers rather than reimplementing
    them: two public views of one row would drift, and the one that drifted
    would be the one nobody was looking at."""
    client = make_client()
    token = _shared_session(client, db, _new_user(db))

    old = client.get(f"/v1alpha1/public/sandbox-sessions/{token}").json()
    new = client.get(f"/v1alpha1/public/share/{token}").json()

    assert new["session"] == old["session"]
    assert new["events"] == old["events"]
