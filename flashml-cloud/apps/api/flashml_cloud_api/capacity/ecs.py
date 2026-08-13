"""Alibaba ECS GPU — the first venue this API can rent from by itself.

``ResourceProvider`` for ``ecs-gpu``: one pay-as-you-go ECS instance per job,
enrolled against THIS API, destroyed rather than stopped, and destroyed again
by the reconciler if this module ever loses track of it.

WHY ECS AND NOT RUNPOD
----------------------
Owner decision. The venue research
(``docs/superpowers/specs/2026-08-12-venue-capability-findings.md``)
recommends RunPod on price and on evidence — $0.16–0.34/hr for 24 GB cards
against **$1.279/hr for a 16 GB T4** (``ecs.gn6i-c4g1.xlarge``,
``DescribePrice``, live, ``ap-southeast-1``, 2026-08-12) — and the owner
chose ECS anyway, because demonstrating **Alibaba** compute is the point. The
price is a deliberate trade, not an oversight, and the budget gate
(``capacity/budget.py``, default $2.00/hr per acquisition) is sized so a
bigger card is refused rather than silently bought.

ECS clears the requirement that disqualified FC GPU: it is a real VM, so it
can hold ``flashnode work``'s self-scheduled polling loop, which an FC
function cannot — a function instance freezes the moment no request is in
flight (findings, Step 1). And it has a per-job create/destroy pair,
``RunInstances``/``DeleteInstance``, which is the shape ``acquire``/
``release`` assume.

WHAT D3 REQUIRES OF THE INSTANCE, AND WHERE EACH ONE IS
-------------------------------------------------------
The rented instance IS the isolation boundary — there is no container inside
it, because a rented host is normally itself a container and ``--runner
argv`` needs a Docker daemon it does not have (D2).

1. *One job per instance, never reused across submitters.* One
   ``RunInstances`` per ``acquire``, ``Amount=1``, and the identity it boots
   with belongs to one rental. Nothing here ever hands an instance back to a
   pool of instances, because there is no such pool (D1).
2. *Destroyed, not stopped.* :meth:`EcsGpuProvider.release` calls
   ``DeleteInstance`` with ``Force=true`` and this module never calls
   ``StopInstance`` at all. A stopped instance keeps its disk and keeps
   billing for it, and — the part that matters more — a ``RELEASED`` row in
   front of a stopped instance is exactly the invoice ``reconcile.py`` is
   written end to end to prevent.
3. *No path from task code to cloud credentials.* Two independent controls,
   both confirmed against the live API definition:
   ``HttpEndpoint=disabled`` on ``RunInstances`` removes the instance
   metadata endpoint outright, and ``RamRoleName`` is optional and is never
   sent, so there is no credential behind that endpoint even if it were
   reachable. Task code runs unsandboxed on a machine on OUR account; this
   is the whole security argument, and the container was never protecting
   against it.

   ``HttpEndpoint=disabled`` earns its place a second time, for a reason the
   findings do not raise: **the machine token is seeded through
   ``UserData``**, and user-data is readable from inside the instance
   through that same metadata endpoint. Disabling it is what stops a job's
   own code reading the credential we booted the host with.

D9, WHICH IS THE ONE THAT SILENTLY DESTROYS FLEETS
--------------------------------------------------
The host enrols against ``request.enrolment_url`` — THIS API's public URL —
and never against ``settings.coordinator_url``. The field is named
``enrolment_url`` for that reason, ``CapacityRequest`` carries the full
argument, and ``app.py`` already warns that ``coordinator_url`` is "right for
a single-host dev run and wrong for every deployed one". Nothing in this
module reads a coordinator URL from anywhere; the only URL it can put in a
launch is the one the request carries, which is the structural version of
that rule. ``test_capacity_ecs.py`` asserts the launch configuration carries
it.

Point a rented host at the coordinator instead and everything still *works* —
jobs run, tasks complete — while every teardown guard reads "no rows, so it
is not working" and destroys a busy machine an hour in. One line, applied
identically to every rental: it does not lose one GPU, it loses all of them.

ENROLMENT IS PULL, AND IT HAS TO BE
-----------------------------------
ECS gives no exec channel from this API, so ``sandbox_bootstrap.
bootstrap_worker`` — which is written against a ``SandboxGateway`` with
``run``/``write_file`` — cannot be reused, and forcing it to would mean
inventing a channel the venue does not have. The instance boots with
``UserData`` (base64 cloud-init, confirmed on the live ``RunInstances``
definition) and enrols itself.

The proven 2026-08-12 RunPod recipe transfers, and every part of it is here
for the reason it was learned:

* **Mint the token before the machine exists.** ``acquire_for_job`` mints the
  identity in step 3 and hands it over on ``CapacityRequest.node_id`` /
  ``machine_token``; a pull-style host has no second chance to be told who it
  is.
* **Seed the node id and ``credentials.json``.** Written by the user-data,
  in the exact bytes ``sandbox_bootstrap.credential_bytes`` produces —
  reused rather than re-derived, because the agent looks the token up under
  a normalised URL key and a trailing slash is the difference between a file
  that parses and a bare 401.
* **Install into a venv.** A system ``pip install`` fought a
  distro-packaged ``cryptography`` on the RunPod image and crash-looped.
  A venv costs seconds and removes the whole class.
* **Fetch the bootstrap over HTTP when one is configured**
  (``settings.ecs_bootstrap_url``) so a fix reaches hosts that have not
  booted yet without redeploying this API. Unset, the sequence is inline and
  self-contained.

WHAT IS NOT VERIFIED, AND MUST NOT BE READ AS IF IT WERE
--------------------------------------------------------
**Nothing has ever booted flashnode on an Alibaba GPU image**, and nothing in
this module was exercised against the live venue — no instance was created,
started or rented to write it, deliberately and under instruction. What IS
checked offline: the request signer reproduces Alibaba's own published
worked example byte for byte (``OLeaidS1JvxuMvnyHOwuJ+uX5qY=``, doc 148150),
and every parameter name below was read off the live API definition for
``Ecs::2014-05-26`` rather than remembered.

What is therefore unproven, in the order it will bite:

1. Which ECS GPU image ships working NVIDIA drivers, and whether it has
   ``python3``, ``python3-venv`` and ``curl`` — hence no default
   ``ecs_image_id`` and the ``apt-get`` fallback in the user-data.
2. Whether cloud-init on that image runs a ``#!``-prefixed user-data script
   as root on first boot (documented behaviour; unobserved here).
3. How long boot-to-registration actually takes. The default 15-minute
   window is sized against ``reconcile.DEFAULT_BOOT_GRACE_S`` (one hour), not
   against a measurement.
4. Whether ``DescribePrice`` answers in USD on this account. It is only ever
   believed when it says ``Currency: USD``; anything else yields ``None`` and
   the quote stands. See :meth:`EcsGpuProvider._hourly_usd`.

**``usd_per_hour`` is never ``0.0``.** ``0.0`` is a positive claim that a
machine is free: it overwrites the approved quote on the row and contributes
nothing to the rolling-window ceiling, which is how a fleet of $1.279/hr T4s
adds up to nothing at all in the one place that bounds spend. Absent a real
rate this returns ``None``, which means "the venue did not restate the price"
and leaves the quote in place.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Protocol, runtime_checkable
from urllib.parse import quote

from flashml_cloud_api.alibaba_sandbox import redact
from flashml_cloud_api.capacity.provider import (
    AcquiredMachine,
    CapacityRequest,
    ProviderState,
    ReleaseOutcome,
)
from flashml_cloud_api.router.venues import VENUE_ECS_GPU
from flashml_cloud_api.sandbox_bootstrap import (
    DEFAULT_FLASHNODE_VERSION,
    RUNNER,
    UPSTREAM_INDEX,
    credential_bytes,
    normalise_coordinator,
)

__all__ = [
    "AliyunEcsClient",
    "EcsApiError",
    "EcsClient",
    "EcsError",
    "EcsGpuProvider",
    "EcsLaunchConfig",
    "EcsRegistrationTimeout",
    "EcsUnconfigured",
    "FakeEcsClient",
    "VENUE_ID",
    "db_registration_probe",
    "string_to_sign",
    "user_data_script",
]

log = logging.getLogger(__name__)

#: The venue id these rentals carry on ``rented_capacity.venue_id``. Imported
#: from the venue registry rather than spelled again: the registry is what a
#: plan reads and this is what a row records, and two copies of that string
#: would disagree on the day a sweep had to find a machine.
VENUE_ID = VENUE_ECS_GPU

#: ECS is an RPC-style API, one version for every action here.
ECS_API_VERSION = "2014-05-26"

#: What one control-plane call may take. Well above anything ECS answers in,
#: and well below any caller's patience: the point is that a hung socket must
#: not hold a rental open for ever.
DEFAULT_OP_TIMEOUT_S = 30.0

#: How long ``acquire`` waits for the host to enrol before giving up and
#: destroying it. Sized under ``reconcile.DEFAULT_BOOT_GRACE_S`` (3600 s) on
#: purpose: this module should give up on a machine that will not enrol well
#: before the sweep starts making that decision for it, so the failure is
#: recorded by the acquisition that caused it. **It is not a measurement** —
#: nothing has booted flashnode on an Alibaba GPU image.
DEFAULT_REGISTRATION_TIMEOUT_S = 900.0

#: Between registration polls. Every poll is one database read.
DEFAULT_POLL_INTERVAL_S = 10.0

#: How often, in polls, the venue itself is asked whether the instance still
#: exists while we wait. A host that was never going to enrol because ECS
#: released it must not consume the whole window before anyone notices.
INSTANCE_CHECK_EVERY = 6

#: Pay-as-you-go. The only charge type this module will create: a subscription
#: instance cannot be destroyed on the same terms and would bill past the job
#: by design.
INSTANCE_CHARGE_TYPE = "PostPaid"

#: The agent's poll interval on the rented host, seconds. Matches
#: ``sandbox_bootstrap.POLL_SECONDS`` rather than the CLI default of 1.
AGENT_POLL_SECONDS = 2.0

#: Where the agent lives on the instance. Root-owned, 0700, and outside any
#: home directory: cloud-init runs as root and there may be no unprivileged
#: user on a bare GPU image.
AGENT_ROOT = "/opt/flashml"
AGENT_VENV = f"{AGENT_ROOT}/venv"
AGENT_STATE_DIR = f"{AGENT_ROOT}/.flashnode"
AGENT_WORKDIR = f"{AGENT_ROOT}/work"
AGENT_CREDENTIALS = f"{AGENT_STATE_DIR}/credentials.json"
AGENT_NODE_ID_FILE = f"{AGENT_STATE_DIR}/node-id"
AGENT_LOG = "/var/log/flashml-flashnode.log"
BOOTSTRAP_LOG = "/var/log/flashml-bootstrap.log"

#: How long the seeded credential file is left on disk before it is removed.
#: ``flashnode work`` reads it once at startup — the same property
#: ``sandbox_bootstrap._delete_credential`` relies on — so removing it later
#: costs nothing while the agent is alive and takes the token out of reach of
#: the job's own code, which runs unsandboxed on this box under the trusted
#: runner. **Unverified for a pull-style host**: if the agent dies after this
#: fires it cannot re-authenticate, and the rental then fails in the safe
#: direction — no heartbeat, so the reconciler destroys it.
CREDENTIAL_REAP_AFTER_S = 300

#: Error codes that mean the venue says this instance does not exist. Only
#: these count as "already gone"; ``InvalidParameter`` on ``InstanceId`` is
#: deliberately NOT here, because a malformed id is our bug and reading it as
#: a successful destroy would close a row over a machine we mis-named.
GONE_CODES = frozenset({
    "InvalidInstanceId.NotFound",
    "InvalidInstanceIds.NotFound",
    "InvalidInstance.NotFound",
})

#: ECS instance statuses that mean the machine is up. ``Pending`` and
#: ``Stopped`` are existence without running, and both still bill.
RUNNING_STATUSES = frozenset({"Running", "Starting"})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class EcsError(Exception):
    """Base class. Every message raised from this module has been through
    ``redact`` with the account's own secret — an ECS error body echoes the
    parameters it rejected, and the parameters include ``AccessKeyId``."""


class EcsUnconfigured(EcsError):
    """This provider was asked to do something it has not been given the
    inputs for. Raised BEFORE the venue is called, always, so nothing can
    exist as a result of one."""


class EcsRegistrationTimeout(EcsError):
    """The instance was created and never enrolled inside the window.

    The machine is destroyed before this is raised — see
    :meth:`EcsGpuProvider.acquire`. It is a distinct class because it is the
    single likeliest orphan there is, and an operator reading a failed
    acquisition needs to know the difference between "the venue refused" and
    "the venue gave us a machine that never called home".
    """


class EcsApiError(EcsError):
    """ECS answered, and the answer was an error.

    ``code`` is Alibaba's own, e.g. ``InvalidInstanceId.NotFound``, and it is
    what :func:`_is_gone` reads. ``status`` is the HTTP status. Both are kept
    as attributes rather than folded into the message, because a caller
    deciding whether a machine still bills must not have to parse prose.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "",
        status: int = 0,
        request_id: str = "",
        action: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.request_id = request_id
        self.action = action


def _is_gone(exc: BaseException) -> bool:
    """Whether an error is the venue saying the instance does not exist.

    A 404 or one of :data:`GONE_CODES`. Nothing else: "we could not ask" and
    "it is not there" are the two answers that must never merge, because one
    of them closes a row.
    """
    if not isinstance(exc, EcsApiError):
        return False
    return exc.status == 404 or exc.code in GONE_CODES


# ---------------------------------------------------------------------------
# Signing. Verified against Alibaba's own published vector — see
# `test_capacity_ecs.py::test_the_signer_reproduces_alibabas_published_vector`.
# ---------------------------------------------------------------------------


def percent_encode(value: Any) -> str:
    """RFC 3986 encoding as Alibaba's RPC signature defines it.

    ``A-Z a-z 0-9 - _ . ~`` unescaped, everything else ``%XY``, a space as
    ``%20`` rather than ``+``. Python's ``quote`` differs from the spec in
    exactly one place — it leaves ``/`` alone — so ``safe="~"`` is not
    decoration: without it ``percentEncode("/")`` returns ``/`` and every
    signature is wrong in a way that reads as bad credentials.
    """
    return quote(str(value), safe="~")


def canonical_query(params: Mapping[str, str]) -> str:
    """Parameters sorted by name, each side encoded, joined with ``&``."""
    return "&".join(
        f"{percent_encode(key)}={percent_encode(params[key])}"
        for key in sorted(params)
    )


def string_to_sign(method: str, params: Mapping[str, str]) -> str:
    """``METHOD & %2F & percentEncode(canonicalized query)``.

    Doc 148150, "签名机制": the whole request, sorted and encoded twice, is
    what the HMAC covers. Exposed (and tested) separately from
    :func:`sign_params` because a signature that is wrong is indistinguishable
    from a credential that is wrong, and the only way to tell them apart
    offline is to check this string.
    """
    return (
        f"{method}&{percent_encode('/')}&{percent_encode(canonical_query(params))}"
    )


def sign_params(
    method: str, params: Mapping[str, str], access_key_secret: str
) -> dict[str, str]:
    """The request parameters with ``Signature`` added.

    The HMAC key is the secret **plus a literal ``&``** (doc 148150, step 2).
    Omitting that byte produces a signature that is correctly shaped, always
    rejected, and impossible to tell from a mistyped key.
    """
    signature = base64.b64encode(
        hmac.new(
            f"{access_key_secret}&".encode(),
            string_to_sign(method, params).encode(),
            hashlib.sha1,
        ).digest()
    ).decode()
    return {**params, "Signature": signature}


# ---------------------------------------------------------------------------
# The client seam
# ---------------------------------------------------------------------------


@runtime_checkable
class EcsClient(Protocol):
    """One method: make one ECS API call.

    Deliberately not a method per action. The provider owns the parameter
    names — they are the part a reviewer has to check against the API
    definition — and the client owns signing, transport and error mapping.
    Splitting it the other way would put ``HttpEndpoint=disabled`` inside a
    class whose fake nobody reads.

    **Everything that touches the network is behind this**, so the suite runs
    with no credentials and no socket. ``FakeEcsClient`` is the stand-in and
    it keeps real state rather than mocking calls, exactly as the sandbox
    gateway's own fake does.
    """

    region: str

    async def call(self, action: str, params: Mapping[str, str]) -> dict[str, Any]:
        ...


class AliyunEcsClient:
    """:class:`EcsClient` over the ECS RPC API, signed here, no SDK.

    ``alibabacloud_ecs20140526`` would work and is not used: it is a large
    dependency for four actions, and the signature it would perform is
    fifteen lines that this repo can test against Alibaba's own published
    vector. ``httpx`` is already a dependency for every other outbound call.

    POST, form-encoded, because ``UserData`` is base64 of a script and a
    signed GET would put the whole thing in a URL.

    **Never verified against the live venue.** No credentials for it exist in
    this repository — see the module docstring.
    """

    def __init__(
        self,
        *,
        access_key_id: str,
        access_key_secret: str,
        region: str,
        endpoint: str = "",
        timeout_s: float = DEFAULT_OP_TIMEOUT_S,
        transport: Any = None,
    ) -> None:
        if not (access_key_id and access_key_secret and region):
            raise EcsUnconfigured("ECS credentials and a region are required")
        self._access_key_id = access_key_id
        self._access_key_secret = access_key_secret
        self.region = region
        self._endpoint = endpoint or f"https://ecs.{region}.aliyuncs.com/"
        self._timeout_s = timeout_s
        self._transport = transport

    def __repr__(self) -> str:
        """Explicit, because the default prints ``__dict__`` and the second
        thing in it is the secret. ``Settings`` marks the field ``repr=False``
        for the same reason."""
        return f"<AliyunEcsClient region={self.region!r} endpoint={self._endpoint!r}>"

    __str__ = __repr__

    def _common(self, action: str) -> dict[str, str]:
        return {
            "Action": action,
            "Version": ECS_API_VERSION,
            "Format": "JSON",
            "AccessKeyId": self._access_key_id,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureVersion": "1.0",
            "SignatureNonce": uuid.uuid4().hex,
            "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def _redact(self, text: object) -> str:
        return redact(text, self._access_key_secret, self._access_key_id)

    async def call(self, action: str, params: Mapping[str, str]) -> dict[str, Any]:
        import httpx  # local: keeps import cost off a deploy that never rents

        body = sign_params(
            "POST", {**self._common(action), **{k: str(v) for k, v in params.items()}},
            self._access_key_secret,
        )
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_s, transport=self._transport
            ) as http:
                response = await http.post(self._endpoint, data=body)
        except Exception as exc:  # noqa: BLE001 - classified, never leaked raw
            raise EcsError(
                f"{action}: {type(exc).__name__}: {self._redact(exc)}"
            ) from None

        try:
            payload = response.json()
        except Exception:  # noqa: BLE001 - a proxy's HTML error page, say
            payload = {}

        if response.status_code >= 400 or "Code" in payload:
            raise EcsApiError(
                f"{action}: {response.status_code} "
                f"{self._redact(payload.get('Code') or '')} "
                f"{self._redact(payload.get('Message') or response.text[:500])}",
                code=str(payload.get("Code") or ""),
                status=response.status_code,
                request_id=str(payload.get("RequestId") or ""),
                action=action,
            )
        return payload if isinstance(payload, dict) else {}


# ---------------------------------------------------------------------------
# The launch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EcsLaunchConfig:
    """Everything about the machine that is deployment configuration rather
    than a property of the job.

    Frozen and separate from ``Settings`` so the launch a test asserts on is
    the launch production builds, constructed the same way from the same
    fields — :meth:`from_settings` is the only translation and it is four
    lines long.
    """

    region: str
    image_id: str
    instance_type: str
    security_group_id: str
    vswitch_id: str
    zone_id: str = ""
    system_disk_gb: int = 100
    #: ESSD. Not a preference: several GPU families refuse the older
    #: ``cloud_efficiency`` category outright, and a system disk that will not
    #: attach fails the whole ``RunInstances``.
    system_disk_category: str = "cloud_essd"
    #: Mbit/s of public egress. **Zero means no public IP**, which means a
    #: host that cannot reach this API and can therefore never enrol.
    internet_mbps: int = 10
    flashnode_version: str = DEFAULT_FLASHNODE_VERSION
    #: Optional. Fetched over HTTP by the user-data when set — a push then
    #: repairs hosts that have not booted yet.
    bootstrap_url: str = ""
    index_url: str = UPSTREAM_INDEX

    @classmethod
    def from_settings(cls, settings: Any) -> "EcsLaunchConfig":
        return cls(
            region=settings.ecs_region,
            image_id=settings.ecs_image_id,
            instance_type=settings.ecs_instance_type,
            security_group_id=settings.ecs_security_group_id,
            vswitch_id=settings.ecs_vswitch_id,
            zone_id=getattr(settings, "ecs_zone_id", "") or "",
            system_disk_gb=int(getattr(settings, "ecs_system_disk_gb", 100) or 100),
            internet_mbps=int(getattr(settings, "ecs_internet_mbps", 10) or 10),
            bootstrap_url=getattr(settings, "ecs_bootstrap_url", "") or "",
        )


def user_data_script(
    *,
    enrolment_url: str,
    pool_id: str,
    node_id: str,
    machine_token: str,
    config: EcsLaunchConfig,
) -> str:
    """The cloud-init script the instance boots with. Pure — no clock, no id.

    Pure so a test can assert on the exact text, which is the only place
    several load-bearing facts are actually written down:

    * ``enrolment_url`` and nothing else is used as the coordinator the agent
      is pointed at (D9). This function has no access to
      ``settings.coordinator_url`` and that is structural, not stylistic.
    * ``--runner trusted`` (D2). ``--runner argv`` needs a Docker daemon; a
      rented host is normally a container and this one has no daemon
      installed either.
    * **``FLASHNODE_SANDBOX_CAPABLE`` is never written, in any form.**
      ``capabilities.discover`` reads it as ``== "true"``, so an unset
      variable already registers ``sandbox_capable: false`` — and the one
      way this host could ever come to claim otherwise is a human editing a
      launch template that already mentions the name. The name does not
      appear. A sandbox-capable stamp on an unsandboxed rented box is what
      lets a stranger's public job land on it.
    * The credential is written as the agent's own credential store expects
      it (``credential_bytes``, keyed by the normalised URL) and removed
      after :data:`CREDENTIAL_REAP_AFTER_S`.

    The token is in this text, so this text is a secret: it goes into
    ``UserData`` and nowhere else — never a log line, never an observation,
    never an error message.
    """
    url = normalise_coordinator(enrolment_url)
    if not url:
        raise EcsUnconfigured("an enrolment URL is required to launch a host")
    credentials = credential_bytes(url, machine_token).decode()
    env = " ".join(
        f"{name}={_sh(value)}"
        for name, value in (
            ("FLASHNODE_CREDENTIALS", AGENT_CREDENTIALS),
            ("FLASHNODE_STATE_DIR", AGENT_STATE_DIR),
            ("FLASHNODE_WORKDIR", AGENT_WORKDIR),
            ("FLASHNODE_COORDINATOR_URL", url),
            ("FLASHNODE_POOL", str(pool_id)),
            ("FLASHNODE_RUNNER", RUNNER),
        )
    )
    fetch = ""
    if config.bootstrap_url:
        # Fetched, not baked: a fix reaches a host that has not booted yet
        # without redeploying this API. A fetch that fails falls through to
        # the inline sequence rather than failing the boot, because an
        # unreachable script must not cost a machine we are already paying
        # for.
        fetch = (
            f"if curl -fsSL --retry 5 --retry-delay 5 -o {AGENT_ROOT}/bootstrap.sh "
            f"{_sh(config.bootstrap_url)}; then\n"
            f"  chmod 700 {AGENT_ROOT}/bootstrap.sh\n"
            f"  exec env {env} {AGENT_ROOT}/bootstrap.sh\n"
            "fi\n"
            'echo "flashml: bootstrap fetch failed; continuing inline"\n'
        )
    return f"""#!/bin/bash
# FlashML rented-capacity bootstrap. Generated by capacity/ecs.py — do not
# edit on the instance; edit the generator or the fetched bootstrap script.
set -euo pipefail
exec >>{BOOTSTRAP_LOG} 2>&1
umask 077

mkdir -p {AGENT_STATE_DIR} {AGENT_WORKDIR}
chmod 700 {AGENT_ROOT} {AGENT_STATE_DIR}

# The identity, minted before this machine existed. `credentials.json` is
# keyed by the NORMALISED enrolment URL, which is what the agent looks up.
cat > {AGENT_CREDENTIALS} <<'FLASHML_CREDENTIALS'
{credentials}
FLASHML_CREDENTIALS
chmod 600 {AGENT_CREDENTIALS}
# Seeded rather than left to the agent: `load_or_create_node_id` invents an
# id of its own on first run, and a host registering under an id this control
# plane never issued is a machine row nothing can match to this rental — so
# `db_registration_probe` would wait out the window and destroy a working
# machine. Trailing newline and mode 0600, byte-identical to the sandbox path
# that is already proven.
printf '%s\\n' {_sh(node_id)} > {AGENT_NODE_ID_FILE}
chmod 600 {AGENT_NODE_ID_FILE}

# The agent reads the credential once, at startup. Taking it off disk after
# that keeps it away from the job's own code, which runs unsandboxed here.
( sleep {CREDENTIAL_REAP_AFTER_S}; rm -f {AGENT_CREDENTIALS} ) &

{fetch}
# A venv, not a system pip: a system install fought a distro-packaged
# `cryptography` on the RunPod image and crash-looped. Seconds, and the whole
# class of failure goes away.
if ! python3 -m venv {AGENT_VENV}; then
  (apt-get update -y && apt-get install -y python3-venv curl) || true
  python3 -m venv {AGENT_VENV}
fi
{AGENT_VENV}/bin/pip install --no-input --disable-pip-version-check \\
  -i {_sh(config.index_url)} {_sh(f'flashnode=={config.flashnode_version}')}

# `exec` so the agent is pid 1 of this unit rather than a child of a shell
# that has already exited. `--runner trusted` because a rented host has no
# Docker daemon (D2), and this API is the coordinator it enrols against (D9).
exec env {env} {AGENT_VENV}/bin/flashnode work \\
  --runner {RUNNER} \\
  --coordinator {_sh(url)} \\
  --log-json --poll-seconds {AGENT_POLL_SECONDS:g} \\
  >>{AGENT_LOG} 2>&1
"""


def _sh(value: str) -> str:
    """Single-quote for the shell. ``shlex.quote`` would do, and this says the
    same thing without importing a module for one call — the values here are
    ids and URLs from our own configuration, and quoting them is about the day
    something else starts supplying one."""
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


# ---------------------------------------------------------------------------
# Has it enrolled?
# ---------------------------------------------------------------------------

#: ``node_id -> has this machine spoken to THIS API yet``.
RegistrationProbe = Callable[[str], Awaitable[bool]]


def db_registration_probe(settings: Any) -> RegistrationProbe:
    """The production probe: ``machines.last_seen_at`` for this node id.

    **This is the one place the adapter reads our own rows, and it is the
    right one.** ``observe`` reads the venue because the question there is
    "does a machine exist that bills"; the question here is "has it enrolled
    with *us*", which is a fact about our database by definition. The column
    is the same one ``capacity/reconcile.py`` keys its teardown on, so
    ``acquire`` returning and the sweep leaving the machine alone rest on
    exactly one piece of evidence rather than two that can disagree.

    ``revoked_at`` is checked too: a credential revoked while an acquisition
    is in flight — ``reconcile`` names that race — means the host can never
    enrol, so waiting out the window and destroying it is the only honest
    outcome.

    One connection per poll, closed immediately. A rental polls a few dozen
    times; holding a connection open across a fifteen-minute boot to save
    them is the kind of saving that ends in a socket nobody noticed had died.
    """
    from flashml_cloud_api import db as dbmod

    def _check(node_id: str) -> bool:
        conn = dbmod.connect(settings)
        try:
            row = dbmod.fetch_machine_by_node_id(conn, node_id)
        finally:
            conn.close()
        if row is None:
            return False
        if row.get("revoked_at") is not None:
            log.warning(
                "capacity ecs: machine %s was revoked while its host was "
                "still booting; it can never enrol", node_id,
            )
            return False
        return row.get("last_seen_at") is not None

    async def registered(node_id: str) -> bool:
        return await asyncio.to_thread(_check, node_id)

    return registered


# ---------------------------------------------------------------------------
# The provider
# ---------------------------------------------------------------------------


class EcsGpuProvider:
    """``ResourceProvider`` for ``ecs-gpu``. One instance, one job.

    Construct it through :meth:`from_settings` in production;
    ``capacity/registry.py`` is the only caller and it builds nothing at all
    unless ``settings.ecs_configured``.
    """

    venue_id = VENUE_ID

    def __init__(
        self,
        *,
        client: EcsClient,
        config: EcsLaunchConfig,
        registered: RegistrationProbe | None = None,
        registration_timeout_s: float = DEFAULT_REGISTRATION_TIMEOUT_S,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._config = config
        self._registered = registered
        self._registration_timeout_s = registration_timeout_s
        self._poll_interval_s = poll_interval_s
        self._sleep = sleep
        self._monotonic = monotonic

    @classmethod
    def from_settings(cls, settings: Any) -> "EcsGpuProvider":
        if not settings.ecs_configured:
            raise EcsUnconfigured("Alibaba ECS is not configured")
        return cls(
            client=AliyunEcsClient(
                access_key_id=settings.ecs_access_key_id,
                access_key_secret=settings.ecs_access_key_secret,
                region=settings.ecs_region,
            ),
            config=EcsLaunchConfig.from_settings(settings),
            registered=db_registration_probe(settings),
        )

    def __repr__(self) -> str:
        return (
            f"<EcsGpuProvider region={self._config.region!r} "
            f"instance_type={self._config.instance_type!r}>"
        )

    __str__ = __repr__

    # -- acquire ------------------------------------------------------------

    def run_params(self, request: CapacityRequest) -> dict[str, str]:
        """The exact ``RunInstances`` parameters, so a test can read them.

        Every name here was read off the live ``Ecs::2014-05-26::RunInstances``
        definition. The three that are load-bearing rather than routine:

        * ``HttpEndpoint=disabled`` — the metadata endpoint is off (D3.3),
          which also puts the token in this very ``UserData`` out of reach of
          the job's own code.
        * **no ``RamRoleName``** — the parameter is optional and is not sent,
          so there is no credential behind that endpoint even if it were on.
          Its absence is the security property; a test asserts the key is not
          in this dict, because "we forgot to add it" and "we deliberately do
          not add it" look identical in a diff.
        * ``ClientToken`` — idempotency. A ``RunInstances`` whose response is
          lost is the one failure here with an ongoing cost, and a retry
          under the same token returns the same instance instead of renting a
          second one. It is derived from the node id, which is minted once
          per rental and never reused.
        """
        config = self._config
        params: dict[str, str] = {
            "RegionId": config.region,
            "ImageId": config.image_id,
            "InstanceType": config.instance_type,
            "SecurityGroupId": config.security_group_id,
            "VSwitchId": config.vswitch_id,
            "Amount": "1",
            "MinAmount": "1",
            "InstanceChargeType": INSTANCE_CHARGE_TYPE,
            "SystemDisk.Category": config.system_disk_category,
            "SystemDisk.Size": str(config.system_disk_gb),
            "InternetChargeType": "PayByTraffic",
            "InternetMaxBandwidthOut": str(config.internet_mbps),
            "InstanceName": f"flashml-{request.node_id}"[:63],
            "Description": f"FlashML rental for job {request.job_id}"[:255],
            "HttpEndpoint": "disabled",
            "ClientToken": f"flashml-{request.node_id}"[:64],
            "UserData": base64.b64encode(
                user_data_script(
                    enrolment_url=request.enrolment_url,
                    pool_id=request.pool_id,
                    node_id=str(request.node_id),
                    machine_token=str(request.machine_token),
                    config=config,
                ).encode()
            ).decode(),
            # Findable from the venue's own console by a human reconciling an
            # orphan against `unreleased_rows` — which is the list this
            # module's failure paths are written to keep worth reading.
            "Tag.1.Key": "flashml-node-id",
            "Tag.1.Value": str(request.node_id),
            "Tag.2.Key": "flashml-job-id",
            "Tag.2.Value": str(request.job_id),
        }
        if config.zone_id:
            params["ZoneId"] = config.zone_id
        return params

    async def acquire(self, *, request: CapacityRequest) -> AcquiredMachine:
        """Create one instance and return once it has enrolled with this API.

        The order is the contract:

        1. Refuse anything missing BEFORE the venue is called. Nothing can
           exist at a venue that was never asked.
        2. ``RunInstances``. From here on a machine may exist.
        3. Wait for the host to enrol. "The API call returned" is not
           acquisition — a machine that exists and never enrolled is an
           orphan that bills.
        4. **Any failure destroys what was created before re-raising.** The
           handle exists only inside this call until it is returned: the row
           does not have it, nothing else knows it, and a handle that dies
           with the exception is a machine no sweep can ever name.
        """
        if self._registered is None:
            raise EcsUnconfigured(
                "no registration probe: this provider cannot tell whether a "
                "host enrolled, and returning before it has is how an orphan "
                "is created"
            )
        if not request.node_id or not request.machine_token:
            raise EcsUnconfigured(
                "the minted identity did not reach the provider: a pull-style "
                "venue seeds the token at boot and has no second chance"
            )
        if not str(request.enrolment_url or "").strip():
            raise EcsUnconfigured(
                "no enrolment URL: a host with nowhere to enrol bills for "
                "work this API can never see (D9)"
            )

        params = self.run_params(request)
        handle = ""
        try:
            response = await self._client.call("RunInstances", params)
            handle = _first_instance_id(response)
            if not handle:
                # The call succeeded and we cannot name what it made. Log the
                # ClientToken and the tag: those are what a human searches the
                # venue's console with, and they are the only route back to
                # this machine.
                log.error(
                    "capacity ecs: RunInstances returned no instance id for "
                    "node %s (ClientToken=%s). If an instance exists it must "
                    "be found by that tag and deleted by hand.",
                    request.node_id, params["ClientToken"],
                )
                raise EcsError(
                    "RunInstances returned no instance id; anything it created "
                    "cannot be named from here"
                )

            await self._await_registration(request, handle)
            return AcquiredMachine(
                provider_handle=handle,
                machine_id=None,
                node_id=str(request.node_id),
                # Never 0.0. See `_hourly_usd`.
                usd_per_hour=await self._hourly_usd(),
            )
        except BaseException:
            if handle:
                await self._destroy_after_failure(handle)
            raise

    async def _await_registration(
        self, request: CapacityRequest, handle: str
    ) -> None:
        """Poll until the host has spoken to this API, or give up.

        The venue is asked about the instance every
        :data:`INSTANCE_CHECK_EVERY` polls as well, so a machine ECS has
        already released does not consume the whole window before anything
        notices — and so an instance that reached ``Stopped`` on its own is
        not waited on for fifteen minutes.
        """
        assert self._registered is not None  # guarded in `acquire`
        deadline = self._monotonic() + self._registration_timeout_s
        polls = 0
        while True:
            if await self._registered(str(request.node_id)):
                return
            polls += 1
            if polls % INSTANCE_CHECK_EVERY == 0:
                try:
                    state = await self.observe(handle=handle)
                except EcsError as exc:
                    # Could not ask. That is not evidence of anything, so the
                    # wait continues rather than destroying on a failed read.
                    log.warning(
                        "capacity ecs: could not observe %s while waiting: %s",
                        handle, type(exc).__name__,
                    )
                else:
                    if not state.exists:
                        raise EcsRegistrationTimeout(
                            f"{handle} no longer exists at the venue and never "
                            f"enrolled as {request.node_id}"
                        )
            if self._monotonic() >= deadline:
                raise EcsRegistrationTimeout(
                    f"{handle} did not enrol as {request.node_id} within "
                    f"{self._registration_timeout_s:.0f}s"
                )
            await self._sleep(self._poll_interval_s)

    async def _destroy_after_failure(self, handle: str) -> None:
        """Destroy what this acquisition created, and never raise doing it.

        Runs from an ``except`` block whose job is to re-raise the original
        failure. An exception thrown while cleaning up would replace the
        reason with the reason for the reason — and the caller
        (``acquire_for_job``) would then record the wrong one against a row
        that is its only route back to the money.

        **The one failure it cannot cover is cancellation.** If the task
        running ``acquire`` is cancelled, the ``await`` below re-raises
        immediately and the destroy may never be issued. That is not fixed
        here, and it is not meant to be: ``acquire_for_job``'s ``_abandon``
        has exactly the same shape one level up, and the row it leaves
        sweepable is the backstop both of them rely on. Adding a shield in one
        of the two would only make it look covered.
        """
        try:
            outcome = await self.release(handle=handle)
        except Exception:  # noqa: BLE001 - defensive; release does not raise
            log.error(
                "capacity ecs: release(%s) raised while abandoning an "
                "acquisition; the instance may still be billing",
                handle, exc_info=True,
            )
            return
        if not outcome.destroyed:
            log.error(
                "capacity ecs: could not destroy %s after a failed "
                "acquisition (%s); it may still be billing and must be "
                "reconciled against the venue's own listing",
                handle, outcome.detail or "no detail",
            )

    async def _hourly_usd(self) -> float | None:
        """What ECS says this instance costs per hour, or ``None``.

        **Never ``0.0``.** ``0.0`` overwrites the approved quote on the row
        with a claim that the machine is free and contributes nothing to the
        rolling-window ceiling — the one number that bounds a loop of
        correct-looking acquisitions. ``None`` means "the venue did not
        restate the price", and the quote stands.

        Only a ``Currency: USD`` answer is believed. ``DescribePrice`` answers
        in the account's settlement currency, and converting CNY to dollars
        here would invent an exchange rate — the thing this codebase refuses
        to do anywhere else (``prices.py``: CU and CNY remain unconverted).

        Best effort, always: a price read that fails must not fail an
        acquisition that has already produced a working machine.
        """
        config = self._config
        try:
            payload = await self._client.call(
                "DescribePrice",
                {
                    "RegionId": config.region,
                    "ResourceType": "instance",
                    "InstanceType": config.instance_type,
                    "PriceUnit": "Hour",
                    "Period": "1",
                    "Amount": "1",
                    "InstanceNetworkType": "vpc",
                    "InternetChargeType": "PayByTraffic",
                    "InternetMaxBandwidthOut": str(config.internet_mbps),
                    "ImageId": config.image_id,
                    "SystemDisk.Category": config.system_disk_category,
                    "SystemDisk.Size": str(config.system_disk_gb),
                },
            )
        except Exception as exc:  # noqa: BLE001 - the quote stands
            log.warning(
                "capacity ecs: DescribePrice failed (%s); the quote stands",
                type(exc).__name__,
            )
            return None

        price = ((payload.get("PriceInfo") or {}).get("Price") or {})
        currency = str(price.get("Currency") or "").upper()
        if currency != "USD":
            log.info(
                "capacity ecs: DescribePrice answered in %r, not USD; "
                "reporting no rate rather than converting one",
                currency or "nothing",
            )
            return None
        try:
            rate = float(price.get("TradePrice"))
        except (TypeError, ValueError):
            return None
        # A zero is not a free machine, it is a price we did not get.
        return rate if rate > 0 else None

    # -- release and observe ------------------------------------------------

    async def release(self, *, handle: str) -> ReleaseOutcome:
        """Destroy the instance. Idempotent, and it never raises.

        ``DeleteInstance`` with ``Force=true``: **destroy, never stop** (D3.2).
        A stopped instance keeps its disk and keeps billing for it, so a
        ``RELEASED`` row in front of one is precisely the invoice this module
        exists to prevent. ``StopInstance`` is not called anywhere in this
        file and a test asserts it.

        An instance the venue says does not exist is a SUCCESS: the sweep
        calls this repeatedly by design, and a second call raising would make
        a clean sweep look like a failure for ever.

        Everything else — the venue answered and refused, or we could not
        reach it — is ``destroyed=False`` with the reason in ``detail``.
        Reporting rather than raising is what the protocol asks for, and both
        are treated as *unknown* by the caller, which leaves the row
        sweepable. Nothing here ever reports a destroy it did not get.
        """
        handle = str(handle or "").strip()
        if not handle:
            return ReleaseOutcome(
                destroyed=False, detail="no instance id to destroy"
            )
        try:
            await self._client.call(
                "DeleteInstance",
                {
                    "RegionId": self._config.region,
                    "InstanceId": handle,
                    # Running instances cannot be deleted without it, and a
                    # rental is destroyed mid-run by definition.
                    "Force": "true",
                    "TerminateSubscription": "false",
                },
            )
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            if _is_gone(exc):
                return ReleaseOutcome(
                    destroyed=True,
                    detail=f"{handle} no longer exists at the venue",
                )
            log.error(
                "capacity ecs: DeleteInstance(%s) failed: %s",
                handle, type(exc).__name__, exc_info=True,
            )
            return ReleaseOutcome(
                destroyed=False, detail=f"{type(exc).__name__}: {exc}"
            )
        return ReleaseOutcome(
            destroyed=True, detail=f"DeleteInstance {handle} (Force=true)"
        )

    async def observe(self, *, handle: str) -> ProviderState:
        """What ECS says about this instance. The VENUE, never our own rows.

        A reconciler that trusted our rows could not by construction find a
        machine we lost track of, which is the one thing ``observe`` exists
        for. So this is a ``DescribeInstances`` and nothing else.

        **A failed call raises rather than reporting absence.** ``exists=
        False`` closes rows; "we could not ask" must never be able to look
        like "it is not there". ``reconcile._venue_says_gone`` catches the
        exception and treats it as no evidence, which is the correct reading.

        ``running`` is ``Running``/``Starting`` only. ``Stopped`` is
        existence without running and still bills — treating it as released
        would close a row over a machine on the invoice.
        """
        payload = await self._client.call(
            "DescribeInstances",
            {
                "RegionId": self._config.region,
                "InstanceIds": json.dumps([str(handle)]),
            },
        )
        instances = ((payload.get("Instances") or {}).get("Instance") or [])
        if not instances:
            return ProviderState(
                exists=False,
                running=False,
                detail="DescribeInstances returned no instance",
            )
        status = str(instances[0].get("Status") or "")
        return ProviderState(
            exists=True,
            running=status in RUNNING_STATUSES,
            detail=f"Status={status or 'unknown'}",
        )


def _first_instance_id(payload: Mapping[str, Any]) -> str:
    """The one instance id out of a ``RunInstances`` response.

    ``{"InstanceIdSets": {"InstanceIdSet": ["i-..."]}}`` — the nested shape is
    the JSON rendering of the XML original and is what the API definition's
    own response example shows.
    """
    ids = ((payload.get("InstanceIdSets") or {}).get("InstanceIdSet") or [])
    return str(ids[0]) if ids else ""


# ---------------------------------------------------------------------------
# The fake
# ---------------------------------------------------------------------------


@dataclass
class FakeEcsClient:
    """An :class:`EcsClient` with no network and no credentials. **Tests only.**

    Not a mock: it keeps instances, so a test can run one, observe it, delete
    it, and observe that it is gone — and can assert that deleting a deleted
    instance answers the way ECS does (``InvalidInstanceId.NotFound``, 404)
    rather than the way a convenient stub would.

    It lives beside the real client for the same reason the sandbox's fake
    lives beside ``E2BSandboxGateway``: a fake kept in the test tree drifts
    from the interface it stands in for, and the drift is invisible until
    production 500s.

    Nothing in the package may default to it — ``test_alibaba_sandbox.py``
    greps for exactly that, about the other fake, and the rule is the same
    here: a fake reachable from a route is a route that silently succeeds
    against machines that do not exist.
    """

    region: str = "ap-southeast-1"
    #: Every call, in order, as (action, params). What a test asserts the
    #: launch on — including that `StopInstance` is never among them.
    calls: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    #: instance id -> {"Status": ...}
    instances: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: action -> the exception raised on the NEXT call to it.
    faults: dict[str, BaseException] = field(default_factory=dict)
    #: What `DescribePrice` answers, as (TradePrice, Currency). None → the
    #: action fails, which is the "venue would not quote" case.
    price: tuple[float, str] | None = None
    #: The status a newly run instance reports.
    launch_status: str = "Pending"
    _next: int = 0

    def fail_next(self, action: str, exc: BaseException) -> None:
        self.faults[action] = exc

    async def call(self, action: str, params: Mapping[str, str]) -> dict[str, Any]:
        self.calls.append((action, dict(params)))
        fault = self.faults.pop(action, None)
        if fault is not None:
            raise fault

        if action == "RunInstances":
            self._next += 1
            instance_id = f"i-fake{self._next:04d}"
            self.instances[instance_id] = {
                "InstanceId": instance_id,
                "Status": self.launch_status,
                "UserData": params.get("UserData", ""),
            }
            return {
                "RequestId": "fake-request",
                "InstanceIdSets": {"InstanceIdSet": [instance_id]},
            }

        if action == "DescribeInstances":
            wanted = json.loads(params.get("InstanceIds") or "[]")
            found = [
                self.instances[i] for i in wanted if i in self.instances
            ]
            return {"RequestId": "fake-request", "Instances": {"Instance": found}}

        if action == "DeleteInstance":
            instance_id = str(params.get("InstanceId") or "")
            if instance_id not in self.instances:
                raise EcsApiError(
                    "DeleteInstance: 404 InvalidInstanceId.NotFound",
                    code="InvalidInstanceId.NotFound",
                    status=404,
                    action=action,
                )
            del self.instances[instance_id]
            return {"RequestId": "fake-request"}

        if action == "DescribePrice":
            if self.price is None:
                raise EcsApiError(
                    "DescribePrice: 400 InvalidParameter",
                    code="InvalidParameter", status=400, action=action,
                )
            amount, currency = self.price
            return {
                "RequestId": "fake-request",
                "PriceInfo": {
                    "Price": {"TradePrice": amount, "Currency": currency}
                },
            }

        raise EcsApiError(
            f"{action}: 400 InvalidAction",
            code="InvalidAction", status=400, action=action,
        )

    def live_handles(self) -> list[str]:
        return sorted(self.instances)

    def actions(self) -> list[str]:
        return [action for action, _ in self.calls]

    def params_for(self, action: str) -> dict[str, str]:
        for name, params in self.calls:
            if name == action:
                return params
        raise AssertionError(f"{action} was never called")
