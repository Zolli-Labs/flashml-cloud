"""The Alibaba ECS GPU adapter — every claim it makes about real money.

**No test here touches the network and none needs a credential.** Every venue
call goes through `EcsClient`, and `FakeEcsClient` keeps real instances rather
than mocking calls, so a test can run one, watch it fail to enrol, and assert
that the instance is *gone* afterwards — which is the property the whole
module exists for.

The questions, in the order they cost money if the answer is wrong:

1. *Does a failed acquisition leave a machine behind?* A half-created
   instance bills exactly like a whole one, and the handle exists only inside
   `acquire` until it is returned — a handle that dies with the exception is
   a machine no sweep can ever name.
2. *Does the host enrol against THIS API?* D9. One line, applied identically
   to every rental: pointed at the coordinator, jobs still run and every
   teardown guard reads "not working" and destroys a busy machine.
3. *Is the machine destroyed or merely stopped?* D3.2. A stopped instance
   keeps its disk and keeps billing for it.
4. *Can task code reach cloud credentials?* D3.3 — metadata endpoint
   disabled, no RAM role, and neither is visible in a diff unless something
   asserts it.
5. *Can `usd_per_hour` ever be 0.0?* It overwrites the approved quote and
   contributes nothing to the ceiling that bounds a loop of acquisitions.
"""
from __future__ import annotations

import base64
import json

import httpx
import pytest

from flashml_cloud_api.capacity import ecs as E
from flashml_cloud_api.capacity import registry as R
from flashml_cloud_api.capacity.provider import CapacityRequest, ResourceProvider
from flashml_cloud_api.router import venues as V

ENROLMENT_URL = "https://api.flashml.example"
#: The URL the adapter must NEVER use. On Render this is a private service a
#: rented host cannot route to at all, and a machine token means nothing to
#: it — see D9 and `CapacityRequest.enrolment_url`.
COORDINATOR_URL = "http://flashml-coordinator:10000"


def _config(**over) -> E.EcsLaunchConfig:
    base = dict(
        region="ap-southeast-1",
        image_id="m-gpu-image",
        instance_type="ecs.gn6i-c4g1.xlarge",
        security_group_id="sg-1",
        vswitch_id="vsw-1",
    )
    base.update(over)
    return E.EcsLaunchConfig(**base)


def _request(**over) -> CapacityRequest:
    base = dict(
        venue_id=V.VENUE_ECS_GPU,
        owner_id="owner-1",
        pool_id="pool-1",
        job_id="job-1",
        gpu_count=1,
        min_vram_gb=16.0,
        enrolment_url=ENROLMENT_URL,
        quoted_usd_per_hour=1.279,
        node_id="rented-abc123",
        machine_token="mt_secret_token_value",
    )
    base.update(over)
    return CapacityRequest(**base)


class _Clock:
    """A monotonic clock a test drives, so a fifteen-minute timeout costs
    nothing. Every `sleep` advances it by exactly what was slept — nothing
    sleeps for real."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def _provider(client, *, registered=None, clock=None, **over):
    clock = clock or _Clock()
    return E.EcsGpuProvider(
        client=client,
        config=over.pop("config", None) or _config(),
        registered=registered,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        **over,
    )


def _always(value: bool):
    async def registered(node_id: str) -> bool:
        return value

    return registered


def _after(polls: int):
    """Enrols on the Nth poll, so a test can watch the wait actually wait."""
    seen = {"n": 0}

    async def registered(node_id: str) -> bool:
        seen["n"] += 1
        return seen["n"] >= polls

    return registered


# ---------------------------------------------------------------------------
# the contract itself
# ---------------------------------------------------------------------------


def test_the_provider_satisfies_the_protocol_and_carries_the_venue_id():
    provider = _provider(E.FakeEcsClient())
    assert isinstance(provider, ResourceProvider)
    # One string, two places: the registry a plan reads and the row a sweep
    # finds a machine by. Two copies would disagree on the day it mattered.
    assert provider.venue_id == V.VENUE_ECS_GPU == E.VENUE_ID


# ---------------------------------------------------------------------------
# signing — the one part of this module that IS verified
# ---------------------------------------------------------------------------


def test_the_signer_reproduces_alibabas_published_vector():
    """Alibaba's own worked example for `DescribeRegions` on `Ecs
    2014-05-26`: AccessKeyId `testid`, secret `testsecret`, signature
    `OLeaidS1JvxuMvnyHOwuJ+uX5qY=` (doc 148150, "签名机制").

    This is the only end-to-end check available offline, and it is worth
    more than it looks: a signature that is wrong is indistinguishable from
    a credential that is wrong, so without a vector the first real rental
    would fail with "InvalidAccessKeyId" and nobody could tell which half
    was at fault.
    """
    params = {
        "Timestamp": "2016-02-23T12:46:24Z",
        "Format": "XML",
        "AccessKeyId": "testid",
        "Action": "DescribeRegions",
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": "3ee8c1b8-83d3-44af-a94f-4e0ad82fd6cf",
        "Version": "2014-05-26",
        "SignatureVersion": "1.0",
    }
    signed = E.sign_params("GET", params, "testsecret")
    assert signed["Signature"] == "OLeaidS1JvxuMvnyHOwuJ+uX5qY="
    # And the parameters are carried through untouched: a signer that
    # normalised what it signed would sign one request and send another.
    assert {k: v for k, v in signed.items() if k != "Signature"} == params


def test_percent_encoding_follows_the_rpc_rules_not_form_encoding():
    """The four differences from `application/x-www-form-urlencoded`, each of
    which silently breaks every signature: `/` is encoded, a space is `%20`
    and not `+`, `*` is encoded, `~` is not."""
    assert E.percent_encode("/") == "%2F"
    assert E.percent_encode("a b") == "a%20b"
    assert E.percent_encode("*") == "%2A"
    assert E.percent_encode("~") == "~"
    assert E.percent_encode("2016-02-23T12:46:24Z") == "2016-02-23T12%3A46%3A24Z"


def test_string_to_sign_sorts_by_name_and_encodes_twice():
    sts = E.string_to_sign("POST", {"B": "2", "A": "1"})
    assert sts == "POST&%2F&A%3D1%26B%3D2"


# ---------------------------------------------------------------------------
# D9 — the host enrols against THIS API
# ---------------------------------------------------------------------------


class TestEnrolmentUrl:
    def test_the_launch_carries_this_apis_url_and_not_the_coordinators(self):
        """**The one that silently destroys fleets.** The launch is the only
        place the address is decided, it is decided from the request, and
        `settings.coordinator_url` is not reachable from this module at
        all."""
        script = E.user_data_script(
            enrolment_url=ENROLMENT_URL,
            pool_id="pool-1",
            node_id="rented-abc123",
            machine_token="mt_secret_token_value",
            config=_config(),
        )
        assert f"--coordinator '{ENROLMENT_URL}'" in script
        assert f"FLASHNODE_COORDINATOR_URL='{ENROLMENT_URL}'" in script
        assert COORDINATOR_URL not in script
        assert "flashml-coordinator" not in script

    @pytest.mark.asyncio
    async def test_the_run_request_carries_it_too_not_just_the_generator(self):
        """Through the real `acquire`, base64 and all: a generator that is
        right and a launch that does not use it would pass the test above."""
        client = E.FakeEcsClient()
        await _provider(client, registered=_always(True)).acquire(
            request=_request()
        )
        user_data = base64.b64decode(
            client.params_for("RunInstances")["UserData"]
        ).decode()
        assert ENROLMENT_URL in user_data
        assert COORDINATOR_URL not in user_data

    def test_a_launch_with_no_enrolment_url_is_refused_outright(self):
        """Not defaulted, not guessed. A host with nowhere to enrol bills for
        work this API can never see."""
        with pytest.raises(E.EcsUnconfigured):
            E.user_data_script(
                enrolment_url="", pool_id="p", node_id="n",
                machine_token="t", config=_config(),
            )


# ---------------------------------------------------------------------------
# what the machine boots with
# ---------------------------------------------------------------------------


class TestUserData:
    def _script(self, **over):
        return E.user_data_script(
            enrolment_url=ENROLMENT_URL,
            pool_id="pool-1",
            node_id="rented-abc123",
            machine_token="mt_secret_token_value",
            config=_config(**over),
        )

    def test_it_runs_the_trusted_runner(self):
        """D2. `--runner argv` needs a Docker daemon that a rented host does
        not have, and `trusted` is the tier that was actually proven."""
        script = self._script()
        assert "flashnode work" in script
        assert "--runner trusted" in script
        assert "--runner argv" not in script
        assert "FLASHNODE_RUNNER='trusted'" in script

    def test_it_never_mentions_sandbox_capable_in_any_form(self):
        """`capabilities.discover` reads the variable as `== "true"`, so
        unset already registers false. The one way this host could ever come
        to claim otherwise is a human editing a launch template that already
        names it — so the name does not appear. A sandbox-capable stamp on an
        unsandboxed rented box is what lets a stranger's public job land on
        it."""
        assert "SANDBOX_CAPABLE" not in self._script()

    def test_it_seeds_the_credential_in_the_agents_own_format(self):
        """Byte-for-byte what `sandbox_bootstrap.credential_bytes` produces,
        because the agent looks the token up under a NORMALISED URL key: a
        file keyed with a trailing slash exists, parses, yields no token, and
        the agent dies on a bare 401."""
        from flashml_cloud_api.sandbox_bootstrap import credential_bytes

        expected = credential_bytes(ENROLMENT_URL, "mt_secret_token_value")
        assert expected.decode() in self._script()

    def test_it_seeds_the_node_id_and_the_pool(self):
        """The node id is the only thing that ties this host back to the
        rental: `db_registration_probe` looks the machine up by it, so a host
        that invents its own id enrols invisibly and is destroyed as one that
        never enrolled. Written exactly as the proven sandbox path writes it,
        trailing newline and all."""
        script = self._script()
        assert r"printf '%s\n' 'rented-abc123'" in script
        assert "/opt/flashml/.flashnode/node-id" in script
        assert "FLASHNODE_POOL='pool-1'" in script

    def test_it_installs_into_a_venv_and_not_into_the_system(self):
        """The RunPod gotcha, transferred: a system `pip install` fought a
        distro-packaged `cryptography` and crash-looped."""
        script = self._script()
        assert "python3 -m venv /opt/flashml/venv" in script
        assert "/opt/flashml/venv/bin/pip install" in script
        assert "/opt/flashml/venv/bin/flashnode work" in script

    def test_it_pins_the_agent_version_the_rest_of_the_repo_installs(self):
        from flashml_cloud_api.sandbox_bootstrap import DEFAULT_FLASHNODE_VERSION

        assert f"flashnode=={DEFAULT_FLASHNODE_VERSION}" in self._script()

    def test_it_installs_from_real_pypi(self):
        """The Aliyun mirror in an Alibaba image's `/etc/pip.conf` lags PyPI
        by whole releases — measured serving flashnode 0.3.5 while 0.4.0 was
        current — so the pin above cannot be satisfied from it at all."""
        assert "-i 'https://pypi.org/simple/'" in self._script()

    def test_it_takes_the_credential_off_disk_after_the_agent_has_read_it(self):
        """The job's code runs unsandboxed on this box. `flashnode work`
        reads the credential once at startup, so removing it afterwards costs
        nothing and takes the token out of that code's reach."""
        script = self._script()
        assert f"sleep {E.CREDENTIAL_REAP_AFTER_S}" in script
        assert "rm -f /opt/flashml/.flashnode/credentials.json" in script

    def test_a_bootstrap_url_is_fetched_over_http_when_one_is_configured(self):
        """So a fix reaches hosts that have not booted yet without
        redeploying this API — and a fetch that fails falls through to the
        inline sequence rather than costing a machine we are already paying
        for."""
        script = self._script(bootstrap_url="https://cdn.example/bootstrap.sh")
        assert "curl -fsSL" in script and "https://cdn.example/bootstrap.sh" in script
        assert "continuing inline" in script
        # The inline path is still there behind the fetch.
        assert "/opt/flashml/venv/bin/pip install" in script

    def test_without_one_the_script_is_self_contained(self):
        assert "curl -fsSL" not in self._script()


# ---------------------------------------------------------------------------
# D3 — what the instance is, and what it cannot reach
# ---------------------------------------------------------------------------


class TestRunInstancesParameters:
    def _params(self) -> dict[str, str]:
        return _provider(E.FakeEcsClient()).run_params(_request())

    def test_the_metadata_endpoint_is_disabled(self):
        """D3.3, and twice over: it is the documented path from unsandboxed
        task code to cloud credentials, AND the path to the `UserData` this
        machine's own token was seeded through."""
        assert self._params()["HttpEndpoint"] == "disabled"

    def test_no_ram_role_is_attached(self):
        """`RamRoleName` is optional on RunInstances and is never sent, so
        there is nothing behind the metadata endpoint even if it were on.
        Asserted by absence on purpose: "we forgot to add it" and "we
        deliberately do not add it" look identical in a diff."""
        assert "RamRoleName" not in self._params()

    def test_it_is_one_pay_as_you_go_instance(self):
        """D1 and D3.1: on demand, one job per instance. A subscription
        instance cannot be destroyed on the same terms and would bill past
        the job by design."""
        params = self._params()
        assert params["Amount"] == "1"
        assert params["MinAmount"] == "1"
        assert params["InstanceChargeType"] == "PostPaid"

    def test_it_carries_an_idempotency_token_derived_from_the_rental(self):
        """A RunInstances whose response is lost is the one failure here with
        an ongoing cost. A retry under the same token returns the same
        instance rather than renting a second one."""
        params = self._params()
        assert params["ClientToken"] == "flashml-rented-abc123"
        assert len(params["ClientToken"]) <= 64

    def test_it_asks_for_public_egress(self):
        """Zero here means no public IP, which means a host that cannot reach
        this API and can therefore never enrol — a rental that bills for a
        machine invisible to us until the reconciler destroys it."""
        assert int(self._params()["InternetMaxBandwidthOut"]) > 0

    def test_it_is_findable_from_the_venues_own_console(self):
        """The rows an operator reconciles against the venue's listing are
        named by node id and job id; without the tags, an orphan in the ECS
        console is an anonymous instance."""
        params = self._params()
        assert params["Tag.1.Key"] == "flashml-node-id"
        assert params["Tag.1.Value"] == "rented-abc123"
        assert params["Tag.2.Value"] == "job-1"

    def test_the_zone_is_sent_only_when_one_is_configured(self):
        assert "ZoneId" not in self._params()
        params = _provider(
            E.FakeEcsClient(), config=_config(zone_id="ap-southeast-1a")
        ).run_params(_request())
        assert params["ZoneId"] == "ap-southeast-1a"


# ---------------------------------------------------------------------------
# acquire
# ---------------------------------------------------------------------------


class TestAcquire:
    @pytest.mark.asyncio
    async def test_it_returns_only_once_the_host_has_enrolled(self):
        """"The API call returned" is not acquisition. A machine that exists
        and never enrolled is an orphan that bills."""
        client = E.FakeEcsClient()
        clock = _Clock()
        got = await _provider(
            client, registered=_after(3), clock=clock
        ).acquire(request=_request())
        assert got.provider_handle in client.live_handles()
        assert got.node_id == "rented-abc123"
        # It really waited: two polls answered no, and each was a sleep.
        assert clock.now == pytest.approx(2 * E.DEFAULT_POLL_INTERVAL_S)

    @pytest.mark.asyncio
    async def test_a_host_that_never_enrols_is_destroyed_before_raising(self):
        """The likeliest orphan there is: the instance is created, then
        waiting for it to register times out. The handle exists only inside
        this call — letting it die with the exception produces a machine that
        bills until a human reads the venue's console."""
        client = E.FakeEcsClient()
        with pytest.raises(E.EcsRegistrationTimeout):
            await _provider(client, registered=_always(False)).acquire(
                request=_request()
            )
        assert client.live_handles() == []
        assert "DeleteInstance" in client.actions()

    @pytest.mark.asyncio
    async def test_it_gives_up_early_when_the_venue_has_already_lost_it(self):
        """An instance ECS released on its own must not consume the whole
        fifteen-minute window before anything notices."""
        client = E.FakeEcsClient()
        clock = _Clock()

        async def registered(node_id: str) -> bool:
            # Never enrols, and the instance vanishes from the venue.
            client.instances.clear()
            return False

        with pytest.raises(E.EcsRegistrationTimeout) as caught:
            await _provider(client, registered=registered, clock=clock).acquire(
                request=_request()
            )
        assert "no longer exists" in str(caught.value)
        assert clock.now < E.DEFAULT_REGISTRATION_TIMEOUT_S

    @pytest.mark.asyncio
    async def test_a_refused_run_creates_nothing_and_says_so(self):
        client = E.FakeEcsClient()
        client.fail_next(
            "RunInstances",
            E.EcsApiError(
                "RunInstances: 403 OperationDenied", code="OperationDenied",
                status=403,
            ),
        )
        with pytest.raises(E.EcsApiError):
            await _provider(client, registered=_always(True)).acquire(
                request=_request()
            )
        assert client.live_handles() == []
        # Nothing was created, so nothing was destroyed: a DeleteInstance here
        # would be a call about a machine that never existed.
        assert "DeleteInstance" not in client.actions()

    @pytest.mark.asyncio
    async def test_a_run_that_names_no_instance_refuses_to_pretend(self):
        """The call succeeded and we cannot name what it made. That is the
        one case this module cannot clean up after itself, so it fails loudly
        rather than returning a handle it invented."""
        client = E.FakeEcsClient()

        async def call(action, params):
            client.calls.append((action, dict(params)))
            if action == "RunInstances":
                return {"RequestId": "r", "InstanceIdSets": {"InstanceIdSet": []}}
            raise AssertionError(action)

        client.call = call  # type: ignore[assignment]
        with pytest.raises(E.EcsError):
            await _provider(client, registered=_always(True)).acquire(
                request=_request()
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "missing", ["node_id", "machine_token", "enrolment_url"]
    )
    async def test_a_request_missing_its_identity_never_reaches_the_venue(
        self, missing
    ):
        """Refused BEFORE anything is created. Nothing can exist at a venue
        that was not called, which is what lets `acquire_for_job` close the
        row instead of leaving it for the sweep."""
        client = E.FakeEcsClient()
        with pytest.raises(E.EcsUnconfigured):
            await _provider(client, registered=_always(True)).acquire(
                request=_request(**{missing: None if missing != "enrolment_url" else ""})
            )
        assert client.calls == []

    @pytest.mark.asyncio
    async def test_a_provider_with_no_registration_probe_refuses_to_acquire(self):
        """It cannot tell whether a host enrolled, and returning before it has
        is precisely how an orphan is made. Refusing costs nothing; guessing
        costs a machine."""
        client = E.FakeEcsClient()
        with pytest.raises(E.EcsUnconfigured):
            await _provider(client).acquire(request=_request())
        assert client.calls == []


class TestTheAnsweredRate:
    """`usd_per_hour` is the number the row records and the ceiling adds up.

    `0.0` is a positive claim that a machine is free: it overwrites the
    approved quote and contributes nothing to the rolling window, which is
    how a fleet of $1.279/hr T4s sums to nothing in the one place that bounds
    spend. `None` means "the venue did not restate the price" and leaves the
    quote standing. The two are not interchangeable.
    """

    async def _rate(self, price):
        client = E.FakeEcsClient(price=price)
        got = await _provider(client, registered=_always(True)).acquire(
            request=_request()
        )
        return got.usd_per_hour

    @pytest.mark.asyncio
    async def test_a_real_usd_rate_is_reported(self):
        assert await self._rate((1.279, "USD")) == pytest.approx(1.279)

    @pytest.mark.asyncio
    async def test_a_price_the_venue_would_not_give_is_none_not_zero(self):
        assert await self._rate(None) is None

    @pytest.mark.asyncio
    async def test_a_zero_is_never_believed(self):
        assert await self._rate((0.0, "USD")) is None

    @pytest.mark.asyncio
    async def test_a_non_usd_answer_is_not_converted(self):
        """CNY into USD would invent an exchange rate — the thing this
        codebase refuses to do everywhere else (`prices.py`: CU and CNY
        remain unconverted). The quote stands instead."""
        assert await self._rate((9.2, "CNY")) is None

    @pytest.mark.asyncio
    async def test_a_price_failure_never_fails_an_acquisition(self):
        """The machine is up and working. Losing the rate is a worse row, not
        a reason to destroy it."""
        client = E.FakeEcsClient(price=(1.279, "USD"))
        client.fail_next("DescribePrice", RuntimeError("boom"))
        got = await _provider(client, registered=_always(True)).acquire(
            request=_request()
        )
        assert got.provider_handle in client.live_handles()
        assert got.usd_per_hour is None


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------


class TestRelease:
    @pytest.mark.asyncio
    async def test_it_destroys_and_never_stops(self):
        """D3.2. A stopped instance retains its disk and keeps billing for
        it, and a RELEASED row in front of one is the invoice this module
        exists to prevent."""
        client = E.FakeEcsClient()
        client.instances["i-1"] = {"InstanceId": "i-1", "Status": "Running"}
        outcome = await _provider(client).release(handle="i-1")
        assert outcome.destroyed is True
        assert client.live_handles() == []
        assert client.params_for("DeleteInstance")["Force"] == "true"
        assert "StopInstance" not in client.actions()

    @pytest.mark.asyncio
    async def test_releasing_something_already_gone_is_success(self):
        """The sweep calls this repeatedly by design. A second call raising
        would make a clean sweep look like a failure for ever."""
        client = E.FakeEcsClient()
        client.instances["i-1"] = {"InstanceId": "i-1", "Status": "Running"}
        provider = _provider(client)
        assert (await provider.release(handle="i-1")).destroyed is True
        second = await provider.release(handle="i-1")
        assert second.destroyed is True
        assert "no longer exists" in second.detail

    @pytest.mark.asyncio
    async def test_a_venue_that_answers_and_refuses_is_not_a_destroy(self):
        """`destroyed=False`, not an exception and never `True`: the caller
        reads it as *unknown* and leaves the row for the next sweep."""
        client = E.FakeEcsClient()
        client.instances["i-1"] = {"InstanceId": "i-1", "Status": "Running"}
        client.fail_next(
            "DeleteInstance",
            E.EcsApiError(
                "DeleteInstance: 403 IncorrectInstanceStatus",
                code="IncorrectInstanceStatus", status=403,
            ),
        )
        outcome = await _provider(client).release(handle="i-1")
        assert outcome.destroyed is False
        assert "IncorrectInstanceStatus" in outcome.detail
        # And it is still there. A False that had actually destroyed it would
        # be a different, quieter bug.
        assert client.live_handles() == ["i-1"]

    @pytest.mark.asyncio
    async def test_a_transport_failure_is_reported_not_raised(self):
        client = E.FakeEcsClient()
        client.instances["i-1"] = {"InstanceId": "i-1", "Status": "Running"}
        client.fail_next("DeleteInstance", E.EcsError("connection reset"))
        outcome = await _provider(client).release(handle="i-1")
        assert outcome.destroyed is False

    @pytest.mark.asyncio
    async def test_an_empty_handle_is_not_a_destroy(self):
        client = E.FakeEcsClient()
        outcome = await _provider(client).release(handle="")
        assert outcome.destroyed is False
        assert client.calls == []


# ---------------------------------------------------------------------------
# observe — the venue, never our rows
# ---------------------------------------------------------------------------


class TestObserve:
    @pytest.mark.asyncio
    async def test_a_running_instance_exists_and_runs(self):
        client = E.FakeEcsClient()
        client.instances["i-1"] = {"InstanceId": "i-1", "Status": "Running"}
        state = await _provider(client).observe(handle="i-1")
        assert (state.exists, state.running) == (True, True)

    @pytest.mark.asyncio
    async def test_a_stopped_instance_exists(self):
        """It is not running and it is still on the invoice — a stopped ECS
        instance keeps its disk. `reconcile._venue_says_gone` acts only on
        `exists=False` for exactly this reason."""
        client = E.FakeEcsClient()
        client.instances["i-1"] = {"InstanceId": "i-1", "Status": "Stopped"}
        state = await _provider(client).observe(handle="i-1")
        assert (state.exists, state.running) == (True, False)

    @pytest.mark.asyncio
    async def test_an_unknown_instance_does_not_exist(self):
        state = await _provider(E.FakeEcsClient()).observe(handle="i-nope")
        assert (state.exists, state.running) == (False, False)

    @pytest.mark.asyncio
    async def test_a_failed_read_raises_rather_than_reporting_absence(self):
        """"We could not ask" must never look like "it is not there":
        `exists=False` is what closes a row, and a transient 500 read as
        absence closes it over a live machine."""
        client = E.FakeEcsClient()
        client.fail_next("DescribeInstances", E.EcsError("gateway timeout"))
        with pytest.raises(E.EcsError):
            await _provider(client).observe(handle="i-1")

    @pytest.mark.asyncio
    async def test_it_asks_the_venue_and_not_the_database(self):
        client = E.FakeEcsClient()
        client.instances["i-1"] = {"InstanceId": "i-1", "Status": "Running"}
        await _provider(client).observe(handle="i-1")
        params = client.params_for("DescribeInstances")
        assert json.loads(params["InstanceIds"]) == ["i-1"]


# ---------------------------------------------------------------------------
# the real client: signing, transport, redaction
# ---------------------------------------------------------------------------


class _Settings:
    """Just enough of `Settings` for the registry and the client."""

    ecs_access_key_id = "LTAI5tFAKEFAKEFAKE"
    ecs_access_key_secret = "s3cr3t-ecs-key"
    ecs_region = "ap-southeast-1"
    ecs_zone_id = ""
    ecs_image_id = "m-gpu-image"
    ecs_instance_type = "ecs.gn6i-c4g1.xlarge"
    ecs_security_group_id = "sg-1"
    ecs_vswitch_id = "vsw-1"
    ecs_system_disk_gb = 100
    ecs_internet_mbps = 10
    ecs_bootstrap_url = ""
    ecs_configured = True


class TestTheRealClient:
    def _client(self, handler):
        return E.AliyunEcsClient(
            access_key_id=_Settings.ecs_access_key_id,
            access_key_secret=_Settings.ecs_access_key_secret,
            region="ap-southeast-1",
            transport=httpx.MockTransport(handler),
        )

    @pytest.mark.asyncio
    async def test_it_posts_a_signed_form_to_the_regional_endpoint(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = request.content.decode()
            return httpx.Response(200, json={"RequestId": "r"})

        await self._client(handler).call("DescribeInstances", {"RegionId": "x"})
        assert seen["url"] == "https://ecs.ap-southeast-1.aliyuncs.com/"
        # POST, because `UserData` is a base64 script and a signed GET would
        # put the whole thing in a URL.
        assert "Action=DescribeInstances" in seen["body"]
        assert "Signature=" in seen["body"]
        assert "Version=2014-05-26" in seen["body"]

    @pytest.mark.asyncio
    async def test_an_error_body_becomes_a_typed_error_with_its_code(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                json={
                    "Code": "InvalidInstanceId.NotFound",
                    "Message": "The specified InstanceId does not exist.",
                    "RequestId": "r",
                },
            )

        with pytest.raises(E.EcsApiError) as caught:
            await self._client(handler).call("DeleteInstance", {"InstanceId": "i"})
        assert caught.value.code == "InvalidInstanceId.NotFound"
        assert caught.value.status == 404
        # And the provider reads it as "already gone", which is what makes
        # `release` idempotent against the real venue and not just the fake.
        assert E._is_gone(caught.value) is True

    @pytest.mark.asyncio
    async def test_the_secret_never_reaches_an_error_message(self):
        """An ECS error body echoes the parameters it rejected, and the
        parameters include the AccessKeyId. That message is exactly what
        somebody pastes into an issue."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={
                    "Code": "InvalidAccessKeyId.NotFound",
                    "Message": (
                        f"AccessKeyId {_Settings.ecs_access_key_id} with secret "
                        f"{_Settings.ecs_access_key_secret} is invalid"
                    ),
                },
            )

        with pytest.raises(E.EcsApiError) as caught:
            await self._client(handler).call("DescribeInstances", {})
        message = str(caught.value)
        assert _Settings.ecs_access_key_secret not in message
        assert _Settings.ecs_access_key_id not in message

    @pytest.mark.asyncio
    async def test_a_transport_failure_is_typed_too(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        with pytest.raises(E.EcsError):
            await self._client(handler).call("DescribeInstances", {})

    def test_it_refuses_to_construct_without_credentials(self):
        with pytest.raises(E.EcsUnconfigured):
            E.AliyunEcsClient(
                access_key_id="", access_key_secret="", region="ap-southeast-1"
            )

    def test_neither_the_client_nor_the_provider_prints_its_secret(self):
        client = self._client(lambda request: httpx.Response(200, json={}))
        assert _Settings.ecs_access_key_secret not in repr(client)
        assert _Settings.ecs_access_key_secret not in str(client)
        assert "ap-southeast-1" in repr(_provider(client))


# ---------------------------------------------------------------------------
# the registry — an unconfigured deployment gets nothing, and that is right
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_an_unconfigured_deployment_yields_no_provider(self):
        """Not a fake, not a stub. A provider answering `destroyed=True`
        about a real billing machine is the failure the module exists to
        prevent; with no adapter the sweep reports and the rows stay
        visible."""
        class Unconfigured(_Settings):
            ecs_configured = False

        assert R.providers_for(Unconfigured()) == {}

    def test_a_configured_deployment_gets_the_ecs_adapter(self, monkeypatch):
        # The probe would open a database connection; the registry's job is
        # the wiring, and that is what this asserts.
        monkeypatch.setattr(E, "db_registration_probe", lambda settings: None)
        providers = R.providers_for(_Settings())
        assert set(providers) == {V.VENUE_ECS_GPU}
        assert isinstance(providers[V.VENUE_ECS_GPU], E.EcsGpuProvider)

    def test_an_adapter_that_will_not_construct_is_skipped_not_fatal(
        self, monkeypatch
    ):
        """This runs at app startup. A deployment refusing to boot over a
        venue it was merely offered would take down submission, jobs and the
        console for an opt-in feature."""
        def explode(settings):
            raise RuntimeError("bad credentials")

        monkeypatch.setattr(E.EcsGpuProvider, "from_settings", staticmethod(explode))
        assert R.providers_for(_Settings()) == {}

    def test_the_fake_client_is_never_the_default_anywhere(self):
        """`FakeEcsClient` is tests-only, and the same rule the sandbox's
        fake is held to: a fake reachable from a route is a route that
        silently succeeds against machines that do not exist — and here the
        machines bill."""
        import pathlib

        package = pathlib.Path(E.__file__).parent.parent
        offenders = [
            path.name
            for path in sorted(package.rglob("*.py"))
            if path.name != "ecs.py" and "FakeEcsClient" in path.read_text()
        ]
        assert offenders == []

    def test_nothing_in_the_capacity_package_reaches_for_the_coordinator_url(
        self,
    ):
        """D9, as a grep, because that is the shape of the mistake.

        `settings.coordinator_url` exists, sounds right, and passes local
        testing because jobs really do run — and a rented host pointed at it
        does work this API never records, so every teardown guard reads "not
        working" and destroys it an hour in. One line, applied identically to
        every rental. The only address any adapter may use is the one on the
        request.
        """
        import pathlib

        capacity = pathlib.Path(E.__file__).parent
        offenders = [
            path.name
            for path in sorted(capacity.rglob("*.py"))
            # Prose about why not is exactly what these modules should carry
            # — every one of them argues the point at length. An attribute
            # access is the bug, so quoted mentions are stripped first (which
            # covers the ``double`` form too, since it contains the single).
            if "settings.coordinator_url" in path.read_text().replace(
                "`settings.coordinator_url`", ""
            )
        ]
        assert offenders == []

    def test_settings_gate_it_all_or_nothing(self):
        from flashml_cloud_api.settings import Settings

        assert Settings.ecs_configured.fget(_Settings()) is True
        for field in (
            "ecs_access_key_id", "ecs_access_key_secret", "ecs_image_id",
            "ecs_instance_type", "ecs_security_group_id", "ecs_vswitch_id",
        ):
            class Partial(_Settings):
                pass

            setattr(Partial, field, "")
            assert Settings.ecs_configured.fget(Partial()) is False, field
