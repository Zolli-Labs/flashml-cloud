"""`scripts/capacity/first_rental.py` — the refusals, and only the refusals.

**Nothing here rents anything, and nothing here can.** Every test is either a
pure function or `preflight`, which by construction contacts no venue: the
whole design of that script is that Stage 0 answers before Stage 1 is reached
and Stage 1 creates nothing. A test that exercised Stage 2 would create an
instance that bills at $1.279/hr, so the stages that spend money are tested by
running the script by hand, once, with a human watching — which is what the
script exists for.

What IS worth testing is the part that stops the money:

* the refusals themselves, especially `FLASHML_PUBLIC_API_URL`. A rented host
  pointed at `localhost` boots, works, and writes nothing to `public.attempts`
  — and then every teardown guard in `capacity/reconcile.py` reads it as idle.
  That is the failure this check exists to make impossible, so it is the one
  with the most cases here;
* that renting is opt-in. `stage_plan` is the single answer to "will this
  invocation spend money", and the default must not contain `rent`;
* that the machine token never reaches the terminal. Stage 1 prints the launch
  so a human can read it, and the launch carries the credential in `UserData`.

The script lives outside `apps/api`, so it is loaded by path. It is tested
from here rather than from beside itself because CI runs `pytest` in
`apps/api` and nowhere else: a test next to the script would never run again
after the day it was written.
"""
from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

from flashml_cloud_api import db as dbmod
from test_jobs_from_repo import db  # noqa: F401 - fixture

#: apps/api/tests -> apps/api -> apps -> flashml-cloud -> scripts/capacity
SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "scripts" / "capacity" / "first_rental.py"
)


def _load():
    """Import the script as a module.

    Registered in `sys.modules` before `exec_module` because the script uses
    `@dataclass`, and dataclasses resolve their annotations through
    `sys.modules[cls.__module__]` — a module that is not there yet raises an
    `AttributeError` that reads like a bug in the script and is not.
    """
    spec = importlib.util.spec_from_file_location("first_rental_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fr = _load()


def test_the_script_is_where_the_report_says_it_is():
    assert SCRIPT.exists(), SCRIPT


# ---------------------------------------------------------------------------
# the enrolment URL: the check that matters more than it looks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "http://localhost:8000",
        "https://localhost",
        "http://127.0.0.1:8000",
        "http://127.1.2.3",
        "https://[::1]:8000",
        "http://0.0.0.0:8000",
        "http://10.0.0.4:8000",
        "http://192.168.1.20",
        "http://172.16.4.4",
        "http://169.254.169.254",
        # TEST-NET-3. Not private, not loopback, and just as unroutable — the
        # case `is_global` catches and a list of named properties does not.
        "http://203.0.113.9:8000",
        "http://my-laptop.local:8000",
        "http://api.localhost",
        "http://box.internal",
        "ftp://example.com",
        "not a url at all",
    ],
)
def test_a_url_a_rented_host_could_never_reach_is_refused(url):
    """Every one of these boots a machine that can never phone home.

    It is worth being exhaustive here rather than representative: the cost of
    a miss is not a failed rental, it is a rental that works while being
    invisible to the ledger — `capacity/ecs.py`'s D9 section calls that the
    failure that does not lose one GPU but loses all of them.
    """
    problem = fr.public_url_problem(url)
    assert problem, f"{url!r} was accepted"
    assert "FLASHML_PUBLIC_API_URL" in problem


@pytest.mark.parametrize(
    "url",
    [
        "https://api.zolliai.com",
        "https://flashml-api.onrender.com",
        "https://sudden-word-nine.trycloudflare.com",
        "http://8.8.8.8:8000",
    ],
)
def test_a_publicly_routable_url_is_accepted(url):
    assert fr.public_url_problem(url) is None


def test_the_refusal_names_the_variable_and_the_repair():
    """A refusal an operator cannot act on is a refusal they will override."""
    problem = fr.public_url_problem("")
    assert "FLASHML_PUBLIC_API_URL is unset" in problem
    assert "cloudflared" in problem


# ---------------------------------------------------------------------------
# renting is opt-in
# ---------------------------------------------------------------------------


def _args(*extra: str):
    parsed = fr.build_parser().parse_args(
        ["--owner", "someone@example.com", "--pool", str(uuid.uuid4()), *extra]
    )
    parsed.resolved_owner_id = ""
    return parsed


def test_the_default_invocation_does_not_rent():
    """The whole safety property of the command line. An accidental run — a
    shell-history recall, a copy-paste without the last flag — must cost
    nothing."""
    plan = fr.stage_plan(_args())
    assert "rent" not in plan
    assert plan == ("preflight", "dryrun")


def test_renting_takes_a_deliberate_flag():
    plan = fr.stage_plan(_args("--rent-for-real"))
    assert plan == ("preflight", "dryrun", "rent", "teardown")
    assert "teardown" in plan, "teardown is not optional on a run that rents"


def test_preflight_only_stops_before_the_venue():
    assert fr.stage_plan(_args("--preflight-only")) == ("preflight",)


def test_the_two_flags_cannot_be_asked_for_together():
    """"Stop before the venue" and "spend money" in one command line is a
    contradiction, and argparse refuses it rather than picking one."""
    with pytest.raises(SystemExit):
        fr.build_parser().parse_args(
            ["--owner", "a@b.c", "--pool", "p", "--preflight-only",
             "--rent-for-real"]
        )


def test_there_is_no_way_to_finish_a_run_with_the_machine_alive():
    """No `--keep`, no `--no-teardown`, no `--leave-running`. Teardown is the
    feature; a flag that skipped it would be the bug."""
    options = {
        action.dest for action in fr.build_parser()._actions  # noqa: SLF001
    }
    assert not (options & {"keep", "keep_instance", "no_teardown", "leave_running"})


# ---------------------------------------------------------------------------
# money arithmetic
# ---------------------------------------------------------------------------


def test_the_projection_covers_the_whole_window_not_just_the_hold():
    """ECS bills from creation, so the registration window is money even when
    nothing ever enrols."""
    projected = fr.projected_spend_usd(
        1.279, registration_timeout_s=900.0, hold_s=600.0
    )
    assert projected == pytest.approx(1.279 * (900 + 600 + fr.SPEND_SLACK_S) / 3600)
    assert projected < 1.0


def test_a_run_that_would_cost_more_than_its_ceiling_is_refused():
    settings = _Settings()
    args = _args("--hold-s", "36000", "--max-spend-usd", "1.00")
    checks = fr.preflight(settings, None, args)
    check = _check(checks, "projected spend")
    assert not check.ok
    assert "--hold-s" in check.detail


# ---------------------------------------------------------------------------
# the dry run's success code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["DryRunOperation", "DRYRUN.SUCCESS", "DryRunSuccess"])
def test_a_dry_run_that_would_have_worked_is_read_as_a_pass(code):
    """Alibaba reports a successful `DryRun` as an ERROR code, and its own
    products disagree about the spelling. Reading it as a failure would send
    an operator hunting a configuration problem that does not exist."""
    assert fr.dry_run_passed(code)


@pytest.mark.parametrize(
    "code",
    ["InvalidVSwitchId.NotFound", "Forbidden.RAM", "QuotaExceed.ElasticQuota", ""],
)
def test_a_real_refusal_is_not_read_as_a_pass(code):
    assert not fr.dry_run_passed(code)


# ---------------------------------------------------------------------------
# the launch is printed, and the credential in it is not
# ---------------------------------------------------------------------------


def _real_params():
    """The launch the shipped adapter builds, not a hand-written dict."""
    from flashml_cloud_api.capacity.ecs import (
        EcsGpuProvider, EcsLaunchConfig, FakeEcsClient,
    )
    from flashml_cloud_api.capacity.provider import CapacityRequest

    provider = EcsGpuProvider(
        client=FakeEcsClient(),
        config=EcsLaunchConfig(
            region="ap-southeast-1",
            image_id="m-fake",
            instance_type="ecs.gn6i-c4g1.xlarge",
            security_group_id="sg-fake",
            vswitch_id="vsw-fake",
        ),
    )
    request = CapacityRequest(
        venue_id="ecs-gpu", owner_id="owner", pool_id="pool", job_id="job",
        gpu_count=1, min_vram_gb=16.0,
        enrolment_url="https://api.example.com",
        quoted_usd_per_hour=1.279,
        node_id="rented-abc123def456",
        machine_token="fmm_thisisthesecretmachinetoken",
    )
    return provider.run_params(request), request


def test_the_printed_launch_never_contains_the_machine_token():
    """Stage 1 prints the launch so a human can read it. The launch carries
    the credential the host boots with, and `capacity/ecs.py` says in as many
    words that that text is a secret."""
    params, request = _real_params()
    printed = fr.format_run_params(params)
    assert request.machine_token not in printed
    assert params["UserData"] not in printed
    assert "NOT PRINTED" in printed
    # ...and everything a reviewer actually needs still is.
    assert "HttpEndpoint" in printed and "disabled" in printed
    assert "vsw-fake" in printed
    assert "m-fake" in printed


def test_a_long_base64_blob_is_scrubbed_out_of_any_message():
    """An ECS error body echoes the parameters it rejected. The client redacts
    the access key and its secret — it cannot redact a machine token it never
    minted, so anything token-shaped is removed here."""
    params, _ = _real_params()
    message = f"InvalidParameter: UserData={params['UserData']}"
    scrubbed = fr.scrub(message)
    assert params["UserData"] not in scrubbed
    assert "<redacted-blob>" in scrubbed
    assert "InvalidParameter" in scrubbed


def test_the_launch_self_checks_pass_on_the_real_adapter_output():
    params, _ = _real_params()
    results = fr.launch_self_checks(params, "https://api.example.com")
    failed = [what for ok, what in results if not ok]
    assert not failed, failed


@pytest.mark.parametrize(
    "mutate,expect",
    [
        (lambda p: p.__setitem__("RamRoleName", "some-role"), "RamRoleName"),
        (lambda p: p.__setitem__("HttpEndpoint", "enabled"), "HttpEndpoint"),
        (lambda p: p.__setitem__("InternetMaxBandwidthOut", "0"), "public IP"),
        (lambda p: p.__setitem__("InstanceChargeType", "PrePaid"), "PostPaid"),
    ],
)
def test_a_launch_that_lost_a_safety_property_is_caught_before_it_is_sent(
    mutate, expect
):
    """These are the properties the adapter's docstrings claim. The suite
    asserts them against a fake; this asserts them against the bytes THIS
    deployment is about to send, which is where a value from the environment
    can still make a reviewed module send something else."""
    params, _ = _real_params()
    mutate(params)
    results = fr.launch_self_checks(params, "https://api.example.com")
    failed = [what for ok, what in results if not ok]
    assert any(expect in what for what in failed), failed


def test_a_launch_pointed_somewhere_else_fails_the_d9_check():
    params, _ = _real_params()
    results = fr.launch_self_checks(params, "https://someone-elses-api.example")
    failed = [what for ok, what in results if not ok]
    assert any("D9" in what for what in failed), failed


# ---------------------------------------------------------------------------
# the ledger verdict — the loudest line the script prints
# ---------------------------------------------------------------------------


def test_an_attempt_row_is_the_pass():
    verdict, why = fr.attempts_verdict(
        [{"lease_id": "l1", "job_id": "j", "task_id": "t"}], None,
        expect_claim=False,
    )
    assert verdict == "LEDGER SEES IT"
    assert any("reconcile" in line for line in why)


def test_no_attempt_row_while_work_was_offered_is_the_failure():
    verdict, why = fr.attempts_verdict([], {"id": "j", "status": "RUNNING"},
                                       expect_claim=False)
    assert verdict == "LEDGER IS BLIND"
    assert any("RENTED_CAPACITY_DESTROY" in line for line in why)


def test_no_attempt_row_and_no_work_offered_is_neither():
    """The honest third answer. Reported as a pass it is a lie; reported as a
    failure it cries wolf on every run where nobody submitted a job."""
    verdict, why = fr.attempts_verdict([], None, expect_claim=False)
    assert verdict == "NOT ANSWERED"
    assert any("NOT a pass" in line for line in why)


def test_expect_claim_turns_the_unanswered_case_into_a_failure():
    verdict, _ = fr.attempts_verdict([], None, expect_claim=True)
    assert verdict == "LEDGER IS BLIND"


# ---------------------------------------------------------------------------
# preflight, against settings
# ---------------------------------------------------------------------------


class _Settings:
    """Everything `preflight` reads, configured so that only the thing under
    test is wrong."""

    ecs_access_key_id = "LTAI-fake"
    ecs_access_key_secret = "fake-secret"
    ecs_image_id = "m-fake"
    ecs_instance_type = "ecs.gn6i-c4g1.xlarge"
    ecs_security_group_id = "sg-fake"
    ecs_vswitch_id = "vsw-fake"
    ecs_region = "ap-southeast-1"
    ecs_zone_id = ""
    ecs_system_disk_gb = 100
    ecs_internet_mbps = 10
    ecs_bootstrap_url = ""
    ecs_configured = True
    rented_capacity_destroy = False
    public_api_url = "https://api.example.com"
    database_url = ""
    rented_usd_per_acquisition_max = 2.0
    rented_usd_window_max = 10.0
    rented_usd_window_hours = 24.0


def _check(checks, fragment):
    matches = [c for c in checks if fragment in c.name]
    assert len(matches) == 1, [c.name for c in checks]
    return matches[0]


def test_preflight_names_every_missing_ecs_variable():
    settings = _Settings()
    settings.ecs_image_id = ""
    settings.ecs_vswitch_id = ""
    check = _check(fr.preflight(settings, None, _args("--skip-api-probe")),
                   "Alibaba ECS is fully configured")
    assert not check.ok
    assert "ECS_IMAGE_ID" in check.detail
    assert "ECS_VSWITCH_ID" in check.detail
    assert "ECS_ACCESS_KEY_ID" not in check.detail


def test_preflight_refuses_an_armed_sweep():
    """The background sweep stays disarmed for a first rental: this script is
    the teardown, and an armed sweep can destroy the machine on a window
    nobody is watching."""
    settings = _Settings()
    settings.rented_capacity_destroy = True
    check = _check(fr.preflight(settings, None, _args("--skip-api-probe")),
                   "RENTED_CAPACITY_DESTROY")
    assert not check.ok
    assert "ARMED" in check.detail


def test_preflight_refuses_a_localhost_enrolment_url_before_anything_is_asked():
    settings = _Settings()
    settings.public_api_url = "http://127.0.0.1:8000"
    checks = fr.preflight(settings, None, _args("--skip-api-probe"))
    assert not _check(checks, "public address").ok
    # ...and the probe does not then go looking for it.
    assert _check(checks, "/healthz").skipped


def test_preflight_refuses_a_missing_database():
    settings = _Settings()
    check = _check(fr.preflight(settings, None, _args("--skip-api-probe")),
                   "DATABASE_URL")
    assert not check.ok


def test_the_skipped_probe_says_that_nothing_was_proven():
    settings = _Settings()
    check = _check(fr.preflight(settings, None, _args("--skip-api-probe")),
                   "/healthz")
    assert check.skipped
    assert "Nothing has confirmed" in check.detail


def test_an_unreachable_public_api_is_refused_without_touching_the_network():
    """A name that does not resolve is what a dead `cloudflared` leaves
    behind, and it is the likeliest failure of a laptop rehearsal. `.invalid`
    is reserved by RFC 2606 precisely so a test can assert this without a DNS
    round trip that could succeed on a hijacking resolver."""
    check = fr.probe_public_api("https://no-such-host.invalid")
    assert not check.ok
    assert "resolves" in check.name


# ---------------------------------------------------------------------------
# preflight, against a real database
# ---------------------------------------------------------------------------


@pytest.fixture
def an_owner(db):
    """A real profile, cleaned up after. The pattern (and the promise) is
    `test_capacity_acquire.py`'s: the Postgres fixture is session-scoped and
    `window_spend_usd` has no owner filter, so a row left behind here is a
    budget refusal in a file that has no idea why."""
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id, email) values (%s, %s)",
            (user_id, f"{user_id[:8]}@example.com"),
        )
        cur.execute("insert into public.profiles (id) values (%s)", (user_id,))
    try:
        yield user_id
    finally:
        with db.cursor() as cur:
            cur.execute(
                "delete from public.rented_capacity where owner_id = %s", (user_id,)
            )
            cur.execute("delete from public.machines where owner_id = %s", (user_id,))
            cur.execute("delete from public.pools where owner_id = %s", (user_id,))
            cur.execute("delete from auth.users where id = %s", (user_id,))


@pytest.fixture
def a_pool(db, an_owner):
    """Through the real constructor, which seats its owner as a member —
    `provision_rented_machine` calls `lock_pool_for_owner`, which needs
    both."""
    return str(dbmod.create_pool(db, name="first-rental", owner_id=an_owner)["id"])


def _db_args(owner_id, pool_id, *extra):
    parsed = fr.build_parser().parse_args(
        ["--owner", str(owner_id), "--pool", str(pool_id), "--skip-api-probe",
         *extra]
    )
    parsed.resolved_owner_id = str(owner_id)
    return parsed


def _db_settings(db_url="postgres://ignored"):
    settings = _Settings()
    settings.database_url = db_url
    return settings


def test_preflight_passes_for_an_owner_who_owns_and_is_in_the_pool(db, an_owner, a_pool):
    checks = fr.preflight(_db_settings(), db, _db_args(an_owner, a_pool))
    refused = [c.name for c in checks if not c.ok and not c.skipped]
    assert refused == [], refused


def test_preflight_refuses_a_pool_the_owner_does_not_own(db, an_owner, a_pool):
    """`lock_pool_for_owner` is owner-scoped, so this fails inside
    `provision_rented_machine` — after the row is open and the budget is
    committed. Catching it here costs nothing and catching it there costs a
    FAILED row to explain."""
    stranger = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id, email) values (%s, %s)",
            (stranger, f"{stranger[:8]}@example.com"),
        )
        cur.execute("insert into public.profiles (id) values (%s)", (stranger,))
    try:
        check = _check(
            fr.preflight(_db_settings(), db, _db_args(stranger, a_pool)),
            "the owner owns AND is in it",
        )
        assert not check.ok
        assert "does not OWN" in check.detail
    finally:
        with db.cursor() as cur:
            cur.execute("delete from auth.users where id = %s", (stranger,))


def test_preflight_refuses_an_owner_who_left_the_pool(db, an_owner, a_pool):
    """Ownership without membership mints a machine that is stamped with
    nothing: `pool_ids_for_machine` intersects bindings with `pool_members`,
    so the rental could never claim the job it was rented for."""
    with db.cursor() as cur:
        cur.execute(
            "delete from public.pool_members where pool_id = %s and user_id = %s",
            (a_pool, an_owner),
        )
    check = _check(
        fr.preflight(_db_settings(), db, _db_args(an_owner, a_pool)),
        "the owner owns AND is in it",
    )
    assert not check.ok
    assert "not a MEMBER" in check.detail


def test_preflight_refuses_a_pool_that_does_not_exist(db, an_owner):
    check = _check(
        fr.preflight(_db_settings(), db, _db_args(an_owner, uuid.uuid4())),
        "the owner owns AND is in it",
    )
    assert not check.ok
    assert "no public.pools row" in check.detail


def test_preflight_runs_the_real_budget_gate(db, an_owner, a_pool):
    """Not a restatement of the ceiling — `assert_within_budget` itself, so a
    quote this deployment would refuse is refused here rather than after the
    row is open."""
    settings = _db_settings()
    settings.rented_usd_per_acquisition_max = 0.10
    check = _check(
        fr.preflight(settings, db, _db_args(an_owner, a_pool)),
        "budget gate",
    )
    assert not check.ok
    assert "per-acquisition ceiling" in check.detail


def test_an_email_resolves_to_the_profile_and_a_stranger_does_not(db, an_owner):
    with db.cursor() as cur:
        cur.execute("select email from auth.users where id = %s", (an_owner,))
        email = cur.fetchone()["email"]
    assert fr.resolve_owner(db, email) == an_owner
    assert fr.resolve_owner(db, an_owner) == an_owner
    assert fr.resolve_owner(db, "nobody@example.com") == ""
    assert fr.resolve_owner(db, "") == ""


def test_a_nonsense_owner_is_an_unresolved_owner_not_a_traceback(db):
    """`--owner not-a-uuid` is `invalid input syntax for type uuid`. A stack
    trace there sends an operator looking at the database instead of at their
    own command line."""
    assert fr.resolve_owner(db, "not-a-uuid") == ""


def test_a_check_that_cannot_be_answered_is_refused_rather_than_raised(db, an_owner, a_pool):
    """Found on the first real run: a dev project two migrations behind
    answered `relation "public.rented_capacity" does not exist` and the script
    died with a traceback in the middle of Stage 0. Unanswerable is a
    refusal."""
    class _Exploding:
        def cursor(self):
            raise RuntimeError("boom")

    check = fr.guarded("a check", lambda: _Exploding().cursor())
    assert not check.ok
    assert "the check itself failed" in check.detail
    assert "migrations" in check.detail


# ---------------------------------------------------------------------------
# teardown — the part that must not fail
# ---------------------------------------------------------------------------


class _StubVenue:
    """An `EcsClient` for the questions the PROVIDER does not answer.

    `FakeEcsClient` models instances properly and is what the provider gets;
    this one stands in for the venue's own listing, which is the only thing
    this script will call a run clean on. It exists so a test can say "the
    venue still shows the instance" — the case our own rows can never report
    and the whole reason teardown ends with a `DescribeInstances`.
    """

    def __init__(self, instances=(), *, raises=False):
        self.region = "ap-southeast-1"
        self.instances = list(instances)
        self.raises = raises
        self.calls = []

    async def call(self, action, params):
        self.calls.append((action, dict(params)))
        if self.raises:
            raise RuntimeError("the venue could not be reached")
        if action == "DescribeInstances":
            wanted = params.get("Tag.1.Value")
            found = [
                i for i in self.instances
                if not wanted or fr._tag_value(i, fr.NODE_TAG_KEY) == wanted
            ]
            return {"Instances": {"Instance": found}}
        return {}


def _instance(instance_id, node_id, status="Running"):
    return {
        "InstanceId": instance_id,
        "Status": status,
        "InstanceType": "ecs.gn6i-c4g1.xlarge",
        "CreationTime": "2026-08-12T00:00Z",
        "Tags": {"Tag": [{"TagKey": fr.NODE_TAG_KEY, "TagValue": node_id}]},
    }


def _provider_over(client):
    from flashml_cloud_api.capacity.ecs import EcsGpuProvider, EcsLaunchConfig

    return EcsGpuProvider(
        client=client,
        config=EcsLaunchConfig(
            region="ap-southeast-1", image_id="m-fake",
            instance_type="ecs.gn6i-c4g1.xlarge", security_group_id="sg-fake",
            vswitch_id="vsw-fake",
        ),
    )


@pytest.fixture
def a_rental(db, an_owner, a_pool):
    """An ACTIVE rental with a minted lease, exactly as `acquire_for_job`
    leaves one — so `release_capacity` has all three halves to do: destroy the
    machine, end the lease, move the row."""
    from flashml_cloud_api import sandbox_identity as si

    def build(handle: str | None):
        credential = si.provision_rented_machine(
            db, owner_id=an_owner, pool_id=a_pool, node_id=f"rented-{uuid.uuid4().hex[:12]}",
            label="first-rental test", platform="ecs-gpu",
        )
        with db.cursor() as cur:
            cur.execute(
                """
                insert into public.rented_capacity
                    (venue_id, state, owner_id, pool_id, job_id, gpu_count,
                     usd_per_hour, provider_handle, machine_id, acquired_at)
                values ('ecs-gpu', 'ACTIVE', %s, %s, %s, 1, 1.279, %s, %s, now())
                returning id
                """,
                (an_owner, a_pool, f"job-{uuid.uuid4().hex[:8]}", handle,
                 credential.machine_id),
            )
            rented_id = str(cur.fetchone()["id"])
        return rented_id, credential

    return build


def _row_state(db, rented_id):
    with db.cursor() as cur:
        cur.execute(
            "select state, released_at from public.rented_capacity where id = %s",
            (rented_id,),
        )
        return cur.fetchone()


@pytest.mark.asyncio
async def test_teardown_destroys_through_release_capacity_and_the_lease_ends(
    db, a_rental
):
    """Not a raw DeleteInstance. `release_capacity` is what also revokes the
    credential — and since D6 that revoke is the ONLY thing that ends a lease,
    so a teardown that stopped the money and left the token alive would leave
    a working machine credential bound to a user's workspace for hardware
    somebody else rents next."""
    from flashml_cloud_api.capacity.ecs import FakeEcsClient

    fake = FakeEcsClient()
    fake.instances["i-abc123"] = {"InstanceId": "i-abc123", "Status": "Running"}
    rented_id, credential = a_rental("i-abc123")

    ledger = fr.Ledger(region="ap-southeast-1", rented_id=rented_id,
                       instance_id="i-abc123", machine_id=credential.machine_id)
    ok = await fr.teardown(
        _Settings(), db, _provider_over(fake), _StubVenue(), ledger
    )

    assert ok and ledger.destroyed
    assert fake.live_handles() == []
    assert "DeleteInstance" in fake.actions()
    assert "StopInstance" not in fake.actions()
    assert _row_state(db, rented_id)["state"] == "RELEASED"
    with db.cursor() as cur:
        cur.execute(
            "select status from public.machines where id = %s",
            (credential.machine_id,),
        )
        assert cur.fetchone()["status"] == "revoked"


@pytest.mark.asyncio
async def test_teardown_destroys_an_instance_the_row_never_learned_about(db, a_rental):
    """The orphan the watcher exists for.

    `release_capacity`'s no-handle branch deliberately refuses to guess, so a
    row that never recorded its handle can only be settled by something
    holding the id from elsewhere — here, the tag lookup the watcher does
    while `acquire` is still blocked.
    """
    from flashml_cloud_api.capacity.ecs import FakeEcsClient

    fake = FakeEcsClient()
    fake.instances["i-orphan"] = {"InstanceId": "i-orphan", "Status": "Running"}
    rented_id, credential = a_rental(None)

    ledger = fr.Ledger(region="ap-southeast-1", rented_id=rented_id,
                       instance_id="i-orphan", machine_id=credential.machine_id)
    ok = await fr.teardown(
        _Settings(), db, _provider_over(fake), _StubVenue(), ledger
    )

    assert ok, "an instance the row could not name was left running"
    assert fake.live_handles() == []


@pytest.mark.asyncio
async def test_a_run_is_not_clean_while_the_venue_still_shows_the_instance(
    db, a_rental
):
    """The claim this script refuses to make on the strength of its own rows.

    Here everything OUR side believes is green: the provider reported the
    destroy, the row reached RELEASED, the lease is revoked. And the venue's
    own listing still carries the node tag. That must read as NOT torn down,
    because the money is a fact about the venue and never about
    `rented_capacity.state`.
    """
    from flashml_cloud_api.capacity.ecs import FakeEcsClient

    fake = FakeEcsClient()
    fake.instances["i-stubborn"] = {"InstanceId": "i-stubborn", "Status": "Running"}
    rented_id, credential = a_rental("i-stubborn")
    node_id = f"rented-{rented_id[:12]}"
    still_there = _StubVenue([_instance("i-stubborn", node_id)])

    ledger = fr.Ledger(region="ap-southeast-1", rented_id=rented_id,
                       node_id=node_id, instance_id="i-stubborn",
                       machine_id=credential.machine_id)
    ok = await fr.teardown(_Settings(), db, _provider_over(fake), still_there, ledger)

    assert not ok
    assert not ledger.destroyed


@pytest.mark.asyncio
async def test_a_venue_that_cannot_be_asked_is_not_a_clean_run(db, a_rental):
    """"We could not ask" must never read as "nothing is there" — the rule
    `reconcile._venue_says_gone` applies to a row, applied here to a run."""
    from flashml_cloud_api.capacity.ecs import FakeEcsClient

    fake = FakeEcsClient()
    fake.instances["i-quiet"] = {"InstanceId": "i-quiet", "Status": "Running"}
    rented_id, credential = a_rental("i-quiet")

    ledger = fr.Ledger(region="ap-southeast-1", rented_id=rented_id,
                       instance_id="i-quiet", machine_id=credential.machine_id)
    ok = await fr.teardown(
        _Settings(), db, _provider_over(fake), _StubVenue(raises=True), ledger
    )
    assert not ok
    assert not ledger.destroyed


@pytest.mark.asyncio
async def test_teardown_never_raises_even_with_nothing_to_tear_down():
    """It runs from a `finally` whose job is to stop money. An exception here
    would replace the teardown with the reason the teardown failed."""
    ledger = fr.Ledger(region="ap-southeast-1")
    assert await fr.teardown(_Settings(), None, None, _StubVenue(), ledger) is True


# ---------------------------------------------------------------------------
# the watcher — what closes the orphan window
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_watcher_names_the_instance_before_acquire_returns(
    db, postgres_dsn, an_owner, a_pool
):
    """`EcsGpuProvider.acquire` holds the instance id inside the call. The row
    exists first, the node id is derivable from it, and every instance is
    tagged with that node id — so the venue can be asked who this rental
    created while the acquisition is still waiting for the host to enrol.

    Without this the terminal has no id to print, and a `Ctrl-C` fifteen
    minutes into a boot leaves a machine nothing in the system can name.
    """
    import asyncio

    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.rented_capacity
                (venue_id, state, owner_id, pool_id, job_id, gpu_count,
                 usd_per_hour)
            values ('ecs-gpu', 'REQUESTED', %s, %s, 'watched-job', 1, 1.279)
            returning id
            """,
            (an_owner, a_pool),
        )
        rented_id = str(cur.fetchone()["id"])
    node_id = f"rented-{rented_id[:12]}"

    settings = _Settings()
    settings.database_url = postgres_dsn
    ledger = fr.Ledger(region="ap-southeast-1", job_id="watched-job")
    client = _StubVenue([_instance("i-inflight", node_id)])
    stop = asyncio.Event()

    task = asyncio.create_task(fr.watch(settings, ledger, client, stop))
    for _ in range(50):
        if ledger.instance_id:
            break
        await asyncio.sleep(0.05)
    stop.set()
    await task

    assert ledger.rented_id == rented_id
    assert ledger.node_id == node_id
    assert ledger.instance_id == "i-inflight", (
        "the watcher did not find the instance by its flashml-node-id tag"
    )

    with db.cursor() as cur:
        cur.execute("delete from public.rented_capacity where id = %s", (rented_id,))
