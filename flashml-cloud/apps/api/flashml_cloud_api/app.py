"""FlashML Cloud API.

Two apps live in this module, and which one you get depends on the
environment:

``create_cloud_app`` — the real thing (Plan 3). The cloud API is the
**only** public door: the coordinator sits on a private network and is not
reachable from the internet at all. Browsers authenticate with Supabase
JWTs; agents authenticate with machine tokens issued by the device-code
enrolment flow. An agent's request is *forwarded* to the coordinator using
the API's operator credential plus ``X-FlashML-On-Behalf-Of: <node_id>``,
where the node_id is resolved from the token — never from the request.

``_create_legacy_app`` — the pre-accounts POC control plane, which keeps its
own SQLite node registry and proxies job traffic unauthenticated. It is kept
working (and tested) because the local demo profile still uses it, but it is
not what gets deployed once real accounts exist.

``create_app()`` picks: if ``SUPABASE_URL`` and ``COORDINATOR_URL`` are both
set, you get the cloud app; otherwise the legacy one. There is deliberately
no middle state where half the routes are authenticated.

Environment (legacy):
  FLASHML_RUNTIME_API      FlashRuntime base URL (default http://localhost:8100)
  FLASHML_CLOUD_DB         SQLite path (default /data/flashml-cloud.db)
  FLASHML_NODE_OFFLINE_SECONDS  heartbeat-loss threshold (default 30)
  FLASHML_PROFILE          local | alibaba-ack (display only)
  Alibaba panel display:   FLASHML_ACK_CONNECTED, FLASHML_ACR_IMAGE,
                           FLASHML_OSS_BUCKET, FLASHML_SLS_ENABLED,
                           FLASHML_PROMETHEUS_ENABLED, FLASHML_SANDBOX_POOL

Environment (cloud): see ``settings.Settings.from_env``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

import tempfile
import uuid

import httpx
import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from starlette.concurrency import run_in_threadpool

from flashruntime.protocol.v1alpha1 import (
    JobSpec,
    JobState,
    NodeHeartbeat,
    NodeRegistration,
)

from flashml_cloud_api import access
from flashml_cloud_api import cli_auth
from flashml_cloud_api import contributions as contribmod
from flashml_cloud_api import db as dbmod
from flashml_cloud_api import enrolment
from flashml_cloud_api import fedavg as fedavgmod
from flashml_cloud_api import metrics as metricsmod
from flashml_cloud_api import repo as repomod
from flashml_cloud_api import storage as storagemod
from flashml_cloud_api import verify as verifymod
from flashml_cloud_api.auth import (
    MACHINE_TOKEN_PREFIX,
    AuthError,
    hash_invite_token,
    looks_like_user_token,
    new_invite_token,
    verify_supabase_jwt,
)
from flashml_cloud_api.compile import (
    CompileError,
    compile_federated_round,
    compile_to_jobspec,
)
from flashml_cloud_api.db import Machine
from flashml_cloud_api.elastic import fleet_shape
from flashml_cloud_api.emails import derive_email_facts
from flashml_cloud_api.flashml_yaml import ConfigError, parse_flashml_yaml
from flashml_cloud_api.images import UnknownImage, resolve_image
from .mail_templates import admitted_email, declined_email
from .mailer import Mailer
from flashml_cloud_api.preflight import preflight, safe_text
from flashml_cloud_api.settings import Settings
from flashml_cloud_api.store import NodeStore

log = logging.getLogger("flashml-cloud-api")
logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","service":"flashml-cloud-api","msg":%(message)s}',
)

#: The header the coordinator reads to learn which machine an operator is
#: forwarding for. Spelled out here rather than imported: flashml-cloud may
#: only import ``flashruntime.protocol`` (workspace CLAUDE.md dependency
#: rule), so the two spellings are kept in sync by a test rather than by the
#: type system — see test_agent_proxy and ``modea.DELEGATION_HEADER``.
DELEGATION_HEADER = "X-FlashML-On-Behalf-Of"

#: What a node_id is allowed to look like. This matters more than it looks:
#: a node_id is supplied by an *unauthenticated* machine at device-code time,
#: is stored, and later becomes an HTTP **header value** on a request
#: carrying the operator credential. A CR/LF in it would be request
#: splitting against the coordinator. It is checked both where it enters
#: (device code) and where it leaves (``CoordinatorClient.forward``); the
#: second check is what covers rows written before this rule existed.
NODE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

#: Path segments the API interpolates into the forwarded URL. Starlette has
#: already percent-decoded them, so a ``%2f`` in the agent's URL arrives as a
#: real ``/`` — without this, a lease_id could re-target the forwarded
#: request at a different coordinator route entirely.
PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")

#: A media type this API is willing to *repeat* to the coordinator. The
#: inbound value is agent-controlled, and the outbound request carries the
#: operator credential; nothing exotic needs to survive the hop, so anything
#: that is not a plain ``type/subtype`` is replaced with the default rather
#: than forwarded.
MEDIA_TYPE_RE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]{1,64}/[A-Za-z0-9!#$&^_.+-]{1,64}$")

#: Largest JSON control-plane body the API will buffer. Claims, heartbeats
#: and completions are a few hundred bytes; nothing legitimate is close.
MAX_JSON_BODY_BYTES = 1 * 1024 * 1024

#: Bounds on POST /v1alpha1/pools/{id}/invites' expires_hours. 30 days by
#: default — long enough that a standing team invite does not quietly go
#: stale between the rare occasions someone actually shares it, short
#: enough that a forgotten link does not stay live indefinitely; the cap
#: keeps a caller from minting a de-facto permanent credential by mistake.
DEFAULT_INVITE_EXPIRES_HOURS = 24 * 30
MAX_INVITE_EXPIRES_HOURS = 24 * 90

#: Bounds on the same route's ``uses``. Ten by default — a standing invite
#: meant to onboard a small team, not a single guest — capped well short of
#: "effectively unlimited" for the same reason ``MAX_INVITE_EXPIRES_HOURS``
#: exists: a typo should not mint a credential nobody meant to hand out.
DEFAULT_POOL_INVITE_USES = 10
MAX_POOL_INVITE_USES = 100

#: Largest artifact body the API will buffer, overridable per deployment.
#: Every proxied upload is read fully into memory before it is forwarded, so
#: without a ceiling one authenticated volunteer could exhaust the API's
#: memory — a cost the coordinator's own 413 does not prevent, because it
#: only applies after the bytes have already arrived here.
DEFAULT_MAX_UPLOAD_BYTES = 256 * 1024 * 1024

#: Caps for the GitHub-repo path. The wire cap bounds what this process
#: buffers from codeload; the extracted cap bounds what a decompression
#: bomb can write to disk (``extract_safely`` enforces it incrementally,
#: mid-extraction, because a bomb is small on the wire).
MAX_REPO_TARBALL_BYTES = 32 * 1024 * 1024
MAX_REPO_EXTRACTED_BYTES = 128 * 1024 * 1024

#: A flashml.yaml is a few hundred bytes. This cap exists so a repo cannot
#: hand the YAML parser a gigabyte.
MAX_CONFIG_BYTES = 256 * 1024

#: The config file, in the two spellings people actually use.
CONFIG_FILENAMES = ("flashml.yaml", "flashml.yml")

#: GitHub's own limits, tightened. These values are interpolated into the
#: codeload URL, so anything outside a conservative alphabet — a slash, a
#: ``..``, a ``@``, a control character — is refused rather than escaped:
#: the request that carries them is made by this server, not the browser.
_GH_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
_GH_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_GH_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")

_GH_PREFIXES = (
    "https://github.com/",
    "http://github.com/",
    "git@github.com:",
    "ssh://git@github.com/",
    "github.com/",
)


def _parse_repo_ref(value: Any, ref: Any) -> tuple[str, str, str]:
    """``(owner, name, ref)`` from whatever the caller wrote, or 400.

    Accepts a browser-pasted URL as well as a bare ``owner/name``, because
    pasting the URL out of the address bar is what people will actually do.
    Everything that comes back is regex-validated: these three strings end
    up in a URL this server fetches.
    """
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=400, detail="repo is required")
    text = value.strip()
    for prefix in _GH_PREFIXES:
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.split("?", 1)[0].split("#", 1)[0]
    text = text.strip("/")
    if text.endswith(".git"):
        text = text[: -len(".git")]

    parts = text.split("/")
    if len(parts) != 2 or not all(parts):
        raise HTTPException(
            status_code=400,
            detail="repo must be a GitHub URL or 'owner/name'",
        )
    owner, name = parts
    if not _GH_OWNER_RE.match(owner) or not _GH_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="invalid GitHub owner or repo name")

    ref_value = "main" if ref is None else ref
    if not isinstance(ref_value, str) or not ref_value.strip():
        raise HTTPException(status_code=400, detail="invalid ref")
    ref_value = ref_value.strip()
    if not _GH_REF_RE.match(ref_value) or ".." in ref_value or ref_value.endswith("/"):
        raise HTTPException(status_code=400, detail="invalid ref")
    return owner, name, ref_value


def _fetch_and_extract(
    fetch_repo: Callable[[str, str, str], bytes],
    owner: str,
    name: str,
    ref: str,
    dest: Path,
) -> tuple[bytes, Path]:
    """Fetch and unpack a repo. Blocking on purpose — the caller runs it in
    a worker thread so a 32 MB tarball does not stall the event loop for
    every other request in the process."""
    tar_bytes = fetch_repo(owner, name, ref)
    if len(tar_bytes) > MAX_REPO_TARBALL_BYTES:
        raise repomod.RepoError(
            f"repo tarball is {len(tar_bytes)} bytes, over the "
            f"{MAX_REPO_TARBALL_BYTES} byte limit"
        )
    root = repomod.extract_safely(tar_bytes, dest, MAX_REPO_EXTRACTED_BYTES)
    return tar_bytes, root


def _read_config_text(repo_root: Path) -> str:
    """The repo's flashml.yaml as text, or a 400 naming what is missing."""
    for filename in CONFIG_FILENAMES:
        path = repo_root / filename
        if not path.is_file():
            continue
        if path.stat().st_size > MAX_CONFIG_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"{filename} is larger than {MAX_CONFIG_BYTES} bytes",
            )
        return path.read_bytes().decode("utf-8", errors="replace")
    raise HTTPException(
        status_code=400,
        detail="repo has no flashml.yaml at its root — add one to describe the job",
    )


def _clean_media_type(value: str | None, default: str) -> str:
    if not value:
        return default
    base = value.split(";", 1)[0].strip().lower()
    return base if MEDIA_TYPE_RE.match(base) else default


def valid_node_id(node_id: Any) -> bool:
    return isinstance(node_id, str) and NODE_ID_RE.match(node_id) is not None


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------


def _bearer(request: Request) -> str | None:
    """The single bearer token on this request, or None.

    Two ``Authorization`` headers is not "first one wins" — it is an
    ambiguity the caller controls, so it is no credential at all.
    """
    values = request.headers.getlist("authorization")
    if len(values) != 1:
        return None
    scheme, _, token = values[0].partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def looks_like_machine_token(token: str | None) -> bool:
    return bool(token) and token.startswith(MACHINE_TOKEN_PREFIX)


# ---------------------------------------------------------------------------
# the coordinator client
# ---------------------------------------------------------------------------


# Backoff between retries of an idempotent coordinator call that answered
# with a gateway error. Sized for a cold start, not for a network blip: the
# dev coordinator runs on a Render free plan and takes ~21s to wake, and a
# submission that fails while it boots is indistinguishable to the user from
# a broken repo. The sum outlasts a measured cold start with room to spare.
GATEWAY_RETRY_DELAYS: tuple[float, ...] = (2.0, 5.0, 12.0)

# Gateway-shaped statuses: the hop in front of the coordinator answered, the
# coordinator itself did not. Anything else — a 4xx especially — is a real
# answer and repeating it just multiplies a request that cannot succeed.
GATEWAY_STATUSES = frozenset({502, 503, 504})


class CoordinatorClient:
    """The only holder of the operator credential.

    Every outbound header is *constructed here*, from scratch. No inbound
    header is copied through — not the agent's ``Authorization`` (which
    would hand a machine token to a service that has no idea what one is),
    and above all not its ``X-FlashML-On-Behalf-Of``, which is the whole
    attack: the coordinator answers 400 on a duplicated delegation header
    rather than picking a winner, and building the dict here makes emitting
    two structurally impossible.
    """

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
    ):
        self._base = settings.coordinator_url.rstrip("/")
        self._token = settings.coordinator_operator_token
        self._transport = transport
        self._timeout = timeout

    async def forward(
        self,
        method: str,
        path: str,
        *,
        on_behalf_of: str | None = None,
        content: bytes | None = None,
        query: str = "",
        media_type: str | None = None,
    ) -> httpx.Response:
        """Forward one request to the coordinator with the operator token.

        ``on_behalf_of`` is the node identity to delegate as — required for
        every agent (machine-token) route, where lease scoping depends on
        it. It is ``None`` for the browser-facing job routes: job
        submission/list/cancel are plain operator-token operations with no
        node identity to assert, and sending an empty or made-up header
        value there would be worse than sending none.
        """
        headers = {"Authorization": f"Bearer {self._token}"}
        if on_behalf_of is not None:
            if not valid_node_id(on_behalf_of):
                # Unreachable from a well-formed enrolment; if it ever is
                # reached, refusing beats emitting an attacker-shaped header.
                log.error(
                    json.dumps({"text": "refusing to delegate: malformed node_id"})
                )
                raise HTTPException(status_code=500, detail="internal identity error")
            headers[DELEGATION_HEADER] = on_behalf_of
        if media_type:
            headers["Content-Type"] = media_type

        url = f"{self._base}{path}"
        if query:
            url = f"{url}?{query}"

        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as client:
            try:
                return await client.request(
                    method, url, headers=headers, content=content
                )
            except httpx.HTTPError:
                # The exception string routinely contains the coordinator
                # URL, and a library that ever put a request header in one
                # would put the operator token there too. So it is neither
                # logged nor returned: the agent learns only that the hop
                # failed, which is all it can act on anyway.
                log.error(
                    json.dumps(
                        {"text": "coordinator request failed",
                         "method": method, "path": path}
                    )
                )
                raise HTTPException(
                    status_code=502, detail="coordinator unavailable"
                ) from None


async def forward_idempotent(
    coordinator: CoordinatorClient,
    method: str,
    path: str,
    **kwargs: Any,
) -> httpx.Response:
    """``forward`` plus a retry while the coordinator is still coming up.

    ONLY for calls that are safe to repeat. The artifact PUT qualifies
    because its key is a freshly minted uuid: a retry writes the same bytes
    to the same never-before-used key, so repeating it cannot overwrite
    another user's staged code, nor an earlier attempt of this same request.
    Do not reach for this on job submission — a repeated POST there is a
    duplicate job.
    """
    last = await coordinator.forward(method, path, **kwargs)
    if last.status_code not in GATEWAY_STATUSES:
        return last

    for delay in GATEWAY_RETRY_DELAYS:
        await asyncio.sleep(delay)
        last = await coordinator.forward(method, path, **kwargs)
        if last.status_code not in GATEWAY_STATUSES:
            return last
    return last


def _storage_gate(db: psycopg.Connection, user_id: str) -> None:
    """Refuse a submission from an account that is out of storage.

    Placed BEFORE the coordinator is asked to expand anything: artifacts
    share one disk across every workspace, so a full disk is not one
    person's problem — it stops the coordinator and with it every other
    workspace's running jobs. The last safe moment to say no is before the
    job starts, because a task's output size is not known until after it
    has run.

    413 rather than 403: this is not a permissions decision. The account is
    allowed to do this and has run out of room, which is what 413 means and
    what distinguishes it from every other refusal on this route.
    """
    problem = storagemod.budget_problem(
        used=dbmod.storage_usage_for_owner(db, user_id),
        limit=storagemod.limit_for(
            override=dbmod.storage_limit_override_for(db, user_id),
            default=storagemod.deployment_default(),
        ),
    )
    if problem is not None:
        raise HTTPException(status_code=413, detail=problem)


def is_terminal_state(state: Any) -> bool:
    """Has this job stopped changing?

    ``JobState.terminal`` from the protocol package rather than a tuple of
    strings kept here: the set of terminal states is a wire fact, and a
    private copy of it would drift the first time the runtime adds one.

    An unrecognised state — a coordinator newer than the pinned protocol —
    is NOT terminal. That is the safe direction for every caller below: the
    worst case is a footprint measured late or not at all, whereas guessing
    "terminal" for a state we do not understand would record a number for a
    job still writing to the disk and then never look again.
    """
    if not isinstance(state, str):
        return False
    try:
        return JobState(state).terminal
    except ValueError:
        return False


async def _record_artifact_footprint(
    coordinator: CoordinatorClient,
    db: psycopg.Connection,
    job_id: str,
) -> None:
    """Measure a finished job's disk footprint, once, and write it down.

    WHY HERE AND NOT ON EVERY POLL. The measurement is an HTTP call to the
    coordinator's artifact listing, and its answer is fixed from the moment
    the job is terminal — so it must happen exactly at the transition, not
    on the two-second poll that follows it for as long as the tab stays
    open. The caller's guard is ``jobs.artifact_bytes_recorded_at``
    (migration 0011), read off the row it already fetched for the
    visibility check: no extra query to decide, and no coordinator call at
    all on a repeat.

    Non-federated jobs only. A federated run is N coordinator jobs under
    one local id and is measured by its driver instead — see
    ``fedavg.record_run_footprint``.

    NEVER FATAL, and NOTHING RECORDED ON FAILURE. Usage accounting is
    best-effort by design: a job page that 500s because the artifact
    listing was slow is strictly worse than usage that lags by one poll.
    Every failure path here returns quietly *without* stamping the marker,
    so the next poll retries — a failed listing remembered as a measurement
    would make that job free for ever.
    """
    try:
        r = await coordinator.forward("GET", f"/v1alpha1/jobs/{_seg(job_id)}/artifacts")
    except Exception:  # noqa: BLE001 - accounting must not fail the request
        return
    if r.status_code >= 300:
        return
    try:
        listing = r.json()
    except ValueError:
        return
    if not isinstance(listing, list):
        return

    try:
        dbmod.record_job_artifact_bytes(
            db, job_id, storagemod.sum_artifact_sizes(listing)
        )
    except Exception:  # noqa: BLE001 - same rule: never fail the user's read
        log.warning(
            json.dumps({"text": "could not record artifact footprint",
                        "job_id": job_id})
        )


def _passthrough(r: httpx.Response) -> Response:
    """Return the coordinator's answer verbatim: status *and* body.

    Status fidelity is load-bearing — ``claim`` answers 204 for "nothing to
    do right now", and flattening that to 200 would make an idle agent think
    it had been given work.
    """
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type"),
    )


def _stamp_pools(
    parsed: dict[str, Any],
    pools: list[str],
    where: Literal["capabilities", "top"],
) -> None:
    """OVERWRITE — never merge — whatever the agent claimed about its own
    pool membership. ``where`` picks the shape: register nests it under
    ``capabilities.pools`` (matching ``NodeRegistration.capabilities``);
    heartbeat carries it top-level (matching ``NodeHeartbeat.pools``).

    Always assigns, including ``[]`` — the caller (route handlers) resolves
    "no pools" and "lookup failed" to the same empty list on purpose, and
    this function has no way to tell them apart nor any reason to.
    """
    if where == "top":
        parsed["pools"] = list(pools)
        # NodeHeartbeat has no `capabilities.pools` field today, but an
        # agent that rides a `capabilities` object along on a heartbeat body
        # anyway must not get to smuggle a forged nested copy through
        # unscrubbed — same overwrite rule as the top-level field, so a
        # future NodeHeartbeat.capabilities field cannot silently revive
        # this hole.
        caps = parsed.get("capabilities")
        if isinstance(caps, dict):
            caps["pools"] = list(pools)
        elif caps is not None:
            parsed["capabilities"] = {}
        return
    caps = parsed.get("capabilities")
    if not isinstance(caps, dict):
        # A forged/malformed `capabilities` (not a dict at all) is replaced
        # outright rather than merged into — there is nothing sane to merge
        # with, and leaving it in place would ship it to the coordinator
        # unexamined.
        caps = {}
    caps["pools"] = list(pools)
    parsed["capabilities"] = caps


def _scrub_identity(
    body: bytes,
    node_id: str,
    *,
    force: bool,
    pools: list[str] | None = None,
    pools_where: Literal["capabilities", "top"] = "capabilities",
) -> tuple[bytes, str]:
    """Replace any ``node_id`` the agent put in its own body with the one
    its token resolves to. Returns ``(body, media_type)``.

    A disagreement is not an error to report back: the body was never
    authoritative, so there is nothing here to disagree *with*. This mirrors
    the coordinator's own rule on ``claim``, and is repeated on this side
    because the coordinator only overwrites the body while it is enforcing —
    an API that forwarded a lying body to a non-enforcing coordinator would
    reintroduce exactly the hole.

    ``force`` inserts the field on the identity-bearing calls (register /
    heartbeat / claim) even when the agent omitted it, so the coordinator
    never has to fall back to a default.

    ``pools``, when not ``None``, is stamped onto the body the same way:
    OVERWRITTEN, never merged with whatever the agent sent, and stamped even
    onto an otherwise-empty body (the ``force`` branch) — a node that skips
    the field must not thereby dodge the stamp.
    """
    if not body.strip():
        if not force:
            return body, "application/json"
        parsed: dict[str, Any] = {"node_id": node_id}
        if pools is not None:
            _stamp_pools(parsed, pools, pools_where)
        return json.dumps(parsed).encode(), "application/json"
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return body, "application/json"  # not JSON: the coordinator will refuse it
    if not isinstance(parsed, dict):
        return body, "application/json"
    if force or "node_id" in parsed:
        parsed["node_id"] = node_id
    if pools is not None:
        _stamp_pools(parsed, pools, pools_where)
    return json.dumps(parsed).encode(), "application/json"


# ---------------------------------------------------------------------------
# helpers used by both the routes and the tests
# ---------------------------------------------------------------------------


async def _json_object(request: Request) -> dict[str, Any]:
    """Parse a JSON object body, or 400.

    Hand-rolled instead of a Pydantic body parameter on purpose: FastAPI
    parses a declared body *before* the endpoint's dependencies have
    finished, so on an authenticated route a malformed body could answer 422
    to a caller who should only ever see 401. Every route in the cloud app
    therefore takes a raw ``Request``.
    """
    raw = await request.body()
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="invalid JSON body") from None
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="expected a JSON object")
    return parsed


def _validated_pool_name(payload: dict[str, Any]) -> str:
    """The one definition of a legal pool name, shared by the create and
    rename routes.

    They must agree — a name create would reject is a name rename must
    reject too — and one function is what makes that structural instead of
    two blocks that happen to match today.
    """
    raw = payload.get("name")
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(status_code=400, detail="name is required")
    name = raw.strip()
    if len(name) > 200:
        raise HTTPException(
            status_code=400, detail="name is limited to 200 characters"
        )
    return name


def _opt_str(value: Any, limit: int = 256) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value[:limit]


def _seg(value: str) -> str:
    """A single URL path segment, refusing anything that could change the
    shape of the forwarded URL (see ``PATH_SEGMENT_RE``)."""
    if not isinstance(value, str) or not PATH_SEGMENT_RE.match(value):
        raise HTTPException(status_code=400, detail="invalid path segment")
    return value


def _uuid_or_400(value: str) -> str:
    """A path segment that reaches a WHERE clause. psycopg parameterises it
    safely, but a malformed uuid raises a DataError that would surface as a
    500 — a 400 is the honest answer."""
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="invalid user id") from None
    return value


def _artifact_key(key: str) -> str:
    """A multi-segment artifact key, validated before it is interpolated
    into the forwarded URL.

    This is not defence in depth, it closes a real hole. Starlette
    percent-decodes a ``{key:path}`` parameter, so ``..%2F..%2Fleases%2Fclaim``
    arrives here as ``../../leases/claim`` — and httpx resolves dot segments
    when it builds the request, which would turn an artifact PUT into a call
    to *any* coordinator route, carrying the operator credential. A ``%3F``
    likewise arrives as a literal ``?`` and would start a query string. So:
    no dot segments, no query/fragment characters, no absolute paths,
    nothing outside a conservative alphabet.
    """
    if not isinstance(key, str) or not key or len(key) > 1024:
        raise HTTPException(status_code=400, detail="invalid artifact key")
    if key.startswith("/") or "//" in key:
        raise HTTPException(status_code=400, detail="invalid artifact key")
    segments = key.split("/")
    if any(seg in ("", ".", "..") for seg in segments):
        raise HTTPException(status_code=400, detail="invalid artifact key")
    if not all(PATH_SEGMENT_RE.match(seg) for seg in segments):
        raise HTTPException(status_code=400, detail="invalid artifact key")
    return key


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    """psycopg hands back uuid/datetime/Decimal as Python objects; render
    them as strings rather than letting the JSON encoder guess."""
    out: dict[str, Any] = {}
    for key, value in row.items():
        if value is None or isinstance(value, (str, int, float, bool, dict, list)):
            out[key] = value
        else:
            out[key] = str(value)
    return out


async def _send_decision_email(
    db: psycopg.Connection,
    mailer: Mailer,
    settings: Settings,
    user_id: str,
    *,
    admitted: bool,
) -> bool:
    """Tell the account what was decided. Returns whether mail went out.

    The database write has already committed by the time this runs, and
    nothing here may undo it — an account that is admitted stays admitted
    whether or not the provider answered. The boolean travels back to the
    console so the admin's toast can say which of the two actually
    happened instead of assuming.
    """
    address = dbmod.email_for_user(db, user_id)
    if address is None:
        return False
    message = admitted_email(settings.console_url) if admitted else declined_email()
    return await mailer.send(
        to=address,
        subject=message.subject,
        html=message.html,
        text=message.text,
        user_id=user_id,
    )


# ---------------------------------------------------------------------------
# the cloud app
# ---------------------------------------------------------------------------


def create_cloud_app(
    settings: Settings,
    connect: Callable[[], psycopg.Connection] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    fetch_repo: Callable[[str, str, str], bytes] | None = None,
    start_federated_job: Callable[..., Any] | None = None,
    mailer: Mailer | None = None,
) -> FastAPI:
    """The public door. Agents and browsers both arrive here; nothing else
    is exposed to the internet.

    ``connect`` is a factory returning a live psycopg connection (injected
    so tests can point it at an ephemeral Postgres). ``transport`` is an
    httpx transport for the coordinator hop, injected so tests can record
    exactly what leaves this process without needing a live coordinator.
    ``fetch_repo`` is the GitHub tarball fetch, injected for the same
    reason: the from-repo tests build their fixture tarballs in-process and
    never reach codeload. ``start_federated_job`` launches the in-API
    federated-averaging driver (``fedavg.start_federated_job``), injected so
    a test can run the driver against a stubbed coordinator — or observe
    that it was started — without a live one.

    On ``settings.require_auth``: it governs *startup validation of the
    environment*, not whether requests are authenticated. There is no open
    mode here. Checking credentials is the entire reason this service
    exists, so an "auth off" switch would be a single env var that turns the
    security model off in production.
    """
    connect = connect or (lambda: dbmod.connect(settings))
    coordinator = CoordinatorClient(settings, transport=transport)
    # Its own transport, not the coordinator's: these are two unrelated
    # hosts, and a test fake for one must not have to answer for the other.
    mailer = mailer or Mailer(settings)
    fetch_repo = fetch_repo or (
        lambda owner, name, ref: repomod.fetch_repo_tarball(owner, name, ref)
    )
    start_federated_job = start_federated_job or fedavgmod.start_federated_job
    max_upload_bytes = int(
        os.environ.get("FLASHML_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES)
    )

    app = FastAPI(title="FlashML Cloud API", version="0.2.0")

    # ORDER MATTERS HERE, and it is not cosmetic.
    #
    # Starlette runs the LAST-added middleware outermost, and its own
    # ServerErrorMiddleware sits outside all of them. So an exception that
    # escapes a route is turned into a 500 ABOVE CORSMiddleware, and that 500
    # carries no Access-Control-Allow-Origin. A browser then refuses to read
    # the response at all and reports `TypeError: Failed to fetch` — the same
    # message it gives for a wrong host, DNS failure, or being offline.
    #
    # That is not hypothetical: the deployed console showed a bare "Failed to
    # fetch" on every page while the API was in fact returning a perfectly
    # legible 500 (a database connection failure). curl saw the 500; the
    # browser could not. Hours went into looking for a network problem that
    # did not exist.
    #
    # So: catch exceptions INSIDE the CORS layer and return a real Response.
    # It then travels back out through CORSMiddleware and gets the headers,
    # and the browser can show the status and the body.
    @app.middleware("http")
    async def cors_visible_errors(request: Request, call_next):
        try:
            return await call_next(request)
        except HTTPException:
            raise  # already a Response; FastAPI's handler adds the headers
        except Exception:
            log.exception("unhandled error on %s %s", request.method, request.url.path)
            return JSONResponse(
                status_code=500,
                content={"detail": "internal error — see server logs"},
            )

    # Added AFTER the handler above, so CORS is the outer of the two and
    # decorates the 500 it produces. Do not reorder.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            o for o in os.environ.get("FLASHML_CORS_ORIGINS", "*").split(",") if o
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.settings = settings
    app.state.connect = connect
    app.state.coordinator = coordinator

    # -- dependencies -------------------------------------------------------

    def db_conn(request: Request) -> Iterator[psycopg.Connection]:
        conn = request.app.state.connect()
        try:
            yield conn
        finally:
            conn.close()

    def current_machine(request: Request) -> Machine:
        """The machine this request *is*. 401 for anything else — including
        a perfectly valid browser JWT, which is a different credential kind
        and must not open agent routes.

        Deliberately *not* declared as ``Depends(db_conn)``: FastAPI would
        then resolve the connection before this function runs, and every
        anonymous request to a public agent route would cost a Postgres
        connection before anyone had checked whether it carried a
        credential at all. Cheap for the attacker, expensive for us. The
        connection is opened only once the token is at least the right
        shape.
        """
        token = _bearer(request)
        if not looks_like_machine_token(token):
            # Refused before any database work, so the two credential kinds
            # never share a code path: a browser JWT is not hashed and
            # looked up as if it might be a machine token.
            raise HTTPException(status_code=401, detail="machine token required")
        db = request.app.state.connect()
        try:
            machine = enrolment.authenticate_machine(db, token)
        finally:
            db.close()
        if machine is None:
            # Unknown token and revoked machine give the same answer, on
            # purpose. Revocation flips ``status`` in the row this reads, so
            # it takes effect on the very next request — there is no cache
            # to expire and no refresh to wait for.
            raise HTTPException(status_code=401, detail="invalid machine token")
        return machine

    def current_user(request: Request) -> str:
        """The signed-in user id, from either a verified Supabase JWT (a
        browser) or an `fmu_` developer token (a CLI, or the MCP server
        built on it). A machine token is rejected without ever reaching
        either.

        THE THREE KINDS NEVER SHARE A CODE PATH. Each is selected by its
        prefix before any work happens, for the reason ``machine_caller``
        documents at length: opening a database connection before checking
        the credential's shape makes every anonymous request cost a
        Postgres connection, which is cheap for an attacker and expensive
        for us. It is also why a browser JWT is never hashed and looked up
        as though it might be a token.

        An `fmu_` token grants EXACTLY its owner's access. This function
        returns a user id and nothing else, so every gate layered on top —
        ``admitted_user``, ``admin_user``, every per-resource ownership
        check — applies to a CLI caller identically and with no second
        implementation to keep aligned.
        """
        token = _bearer(request)
        if token is None or looks_like_machine_token(token):
            raise HTTPException(status_code=401, detail="sign-in required")

        if looks_like_user_token(token):
            db = request.app.state.connect()
            try:
                credential = cli_auth.authenticate_cli(db, token)
            finally:
                db.close()
            if credential is None:
                # Unknown token and revoked credential give the same answer,
                # on purpose — same doctrine as ``machine_caller``.
                raise HTTPException(status_code=401, detail="sign-in required")
            return credential.owner_id

        try:
            return verify_supabase_jwt(token, settings)
        except AuthError:
            # The reason is not reported: "expired" vs "bad signature" is an
            # oracle, and the caller can do nothing different either way.
            raise HTTPException(status_code=401, detail="sign-in required") from None

    def admitted_user(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ) -> str:
        """current_user plus the account-admission gate. Reads (jobs,
        machines, /me) stay open to un-admitted accounts — the console needs
        /me to know which screen to show instead of the product — but
        everything that creates state requires admission. Admission is no
        longer invite-driven: an admin grants it by deciding the account's
        access request (see ``access_state_for`` / 0009). 403, not 404:
        unlike a resource id, the gate's existence is not a secret."""
        if not dbmod.profile_is_admitted(db, user_id):
            raise HTTPException(status_code=403, detail="access not yet approved")
        return user_id

    def admin_user(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ) -> str:
        """current_user plus the admin flag. 403, not 404, for the same
        reason `admitted_user` gives: unlike a resource id, the gate's
        existence is not a secret.

        `is_admin` has no granting route anywhere in this API, deliberately.
        It is set with one UPDATE against the owner's own row.

        Admission is NOT admin: an ordinary account that is fully through
        the gate still fails here, which is the whole point — the queue is
        the only surface that grants product access, so anything less than
        this would let any signed-in account admit itself.
        """
        if not dbmod.profile_is_admin(db, user_id):
            raise HTTPException(status_code=403, detail="admin required")
        return user_id

    async def proxy(
        request: Request,
        machine: Machine,
        path: str,
        *,
        force_node_id: bool = False,
        pools: list[str] | None = None,
        pools_where: Literal["capabilities", "top"] = "capabilities",
    ) -> Response:
        is_artifact = path.startswith("/v1alpha1/artifacts/")
        limit = max_upload_bytes if is_artifact else MAX_JSON_BODY_BYTES
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            # Refuse on the declared length before reading a byte; the check
            # below is what actually enforces it, since Content-Length is
            # the client's claim.
            raise HTTPException(status_code=413, detail="request body too large")
        body = await request.body()
        if len(body) > limit:
            raise HTTPException(status_code=413, detail="request body too large")

        media_type: str | None
        if is_artifact:
            # Artifact bodies are opaque bytes — never parsed, never
            # rewritten. Identity for them lives in the header alone, and
            # the coordinator confines the key to the delegated node's live
            # leases.
            media_type = _clean_media_type(
                request.headers.get("content-type"), "application/octet-stream"
            )
        elif request.method in ("POST", "PUT", "PATCH"):
            body, media_type = _scrub_identity(
                body, machine.node_id, force=force_node_id,
                pools=pools, pools_where=pools_where,
            )
        else:
            media_type = None
        r = await coordinator.forward(
            request.method,
            path,
            on_behalf_of=machine.node_id,
            content=body if body else None,
            query=request.url.query,
            media_type=media_type,
        )
        return _passthrough(r)

    # -- health -------------------------------------------------------------

    @app.get("/healthz")
    async def healthz():
        """Healthy means *able to serve requests*, which requires the database.

        This used to return {"status": "ok"} unconditionally. Render's health
        check passed, the deploy went live, and every authenticated route
        returned 500 because DATABASE_URL pointed at a database that does not
        exist. The service reported healthy while being completely unusable,
        and the only visible symptom was "Failed to fetch" in a browser.

        Checking the dependency here means a deploy that cannot reach Postgres
        FAILS instead of replacing a working one — Render keeps the previous
        deploy serving when a health check never passes. That is the whole
        point: a broken config should not be able to take production down.

        Cheap on purpose: one round trip, no query planning, short timeout.
        """
        try:
            conn = await run_in_threadpool(request_scoped_connect)
        except Exception as exc:
            log.error("healthz: database unreachable: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="database unreachable — check DATABASE_URL",
            ) from exc
        try:
            await run_in_threadpool(lambda: conn.execute("SELECT 1").fetchone())
        finally:
            await run_in_threadpool(conn.close)
        return {"status": "ok", "database": "ok"}

    def request_scoped_connect() -> psycopg.Connection:
        return app.state.connect()

    # -- enrolment: unauthenticated by necessity ----------------------------
    #
    # The only two unauthenticated routes with a body. Neither one grants
    # anything: ``code`` mints a pair of random strings bound to nobody, and
    # ``token`` hands back a credential only for a code a *signed-in human*
    # has already approved.

    @app.post("/v1alpha1/device/code", tags=["enrolment"])
    async def device_code(
        request: Request, db: psycopg.Connection = Depends(db_conn)
    ):
        """Start a device-code flow, for a machine enrolling or a CLI
        logging in. ``kind`` selects which; it defaults to ``machine``, so
        every agent already in the field keeps working byte-for-byte."""
        payload = await _json_object(request)
        kind = payload.get("kind", "machine")
        if kind not in ("machine", "cli"):
            # Refused, not coerced to the default: a typo'd kind must not
            # silently start the wrong flow and hand back the wrong token.
            raise HTTPException(status_code=400, detail="unknown kind")

        if kind == "cli":
            started = cli_auth.start_cli_code(db, _opt_str(payload.get("label")))
        else:
            node_id = payload.get("node_id")
            if not valid_node_id(node_id):
                # See NODE_ID_RE: this value later becomes a header value on a
                # request carrying the operator credential.
                raise HTTPException(status_code=400, detail="invalid node_id")
            started = enrolment.start_device_code(
                db,
                node_id,
                _opt_str(payload.get("hostname")),
                _opt_str(payload.get("platform")),
            )

        base = settings.console_url.rstrip("/")
        # /activate, not /enrol. The console has never served /enrol —
        # apps/web/app/activate/page.tsx is the page — so this URL was
        # printed on the volunteer's terminal, typed into a browser, and
        # 404'd, at the one moment they are most likely to give up. Nothing
        # caught it because each side was self-consistent: the API had a
        # route name, the web app had a page, and no test compared them.
        # tests/test_device_code.py now pins this against the filesystem.
        return {
            "device_code": started["device_code"],
            "user_code": started["user_code"],
            "verification_uri": f"{base}/activate" if base else "/activate",
            "interval": started["interval"],
            "expires_at": started["expires_at"].isoformat(),
        }

    @app.post("/v1alpha1/device/token", tags=["enrolment"])
    async def device_token(
        request: Request, db: psycopg.Connection = Depends(db_conn)
    ):
        """Redeem a device_code. Which flow it belongs to is read off the
        stored row, never off the request — a caller holding a machine's
        device_code must not be able to ask for a user token with it."""
        payload = await _json_object(request)
        device_code_value = payload.get("device_code")
        if not isinstance(device_code_value, str) or not device_code_value:
            raise HTTPException(status_code=400, detail="device_code required")

        row = dbmod.fetch_device_code(db, device_code_value)
        kind = row["kind"] if row else "machine"
        if kind == "cli":
            token = cli_auth.redeem_cli_code(db, device_code_value)
            token_type = "cli"
        else:
            token = enrolment.redeem_device_code(db, device_code_value)
            token_type = "machine"

        if token is None:
            # RFC 8628's polling shape. Unknown / unapproved / expired /
            # already-redeemed are one indistinguishable answer, so this
            # cannot be used to learn which codes exist. An unknown code
            # takes the machine branch above and lands here identically.
            return Response(
                content=json.dumps(
                    {"error": "authorization_pending",
                     "interval": enrolment.POLL_INTERVAL_SECONDS}
                ),
                status_code=400,
                media_type="application/json",
            )
        return {"token": token, "token_type": token_type}

    # -- browser-facing: Supabase JWT --------------------------------------

    @app.get("/v1alpha1/me", tags=["browser"])
    async def me(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        # Additive: every existing key from upsert_profile is unchanged, and
        # this is the one route an un-admitted account MUST be able to
        # read — it is how the console learns which screen to show instead
        # of the product itself.
        profile = _jsonable(dbmod.upsert_profile(db, user_id))
        profile["admitted"] = dbmod.profile_is_admitted(db, user_id)
        # `access` is the four-state version `admitted` cannot express:
        # a signed-in account that has not filled the form is neither
        # admitted nor refused.
        profile["access"] = dbmod.access_state_for(db, user_id)
        # Read-only, and the console's only source for whether to draw the
        # admin queue's entry in its rail. Still granted by one manual SQL
        # UPDATE and by nothing else: `PATCH /me` never writes it, and
        # the `admin_user` dependency re-checks it on every queue route, so
        # exposing it here changes what is *drawn*, never what is allowed.
        profile["is_admin"] = dbmod.profile_is_admin(db, user_id)
        return profile

    #: Fields a user owns. Everything absent from this map is either the
    #: identity provider's (email, avatar), written by enrolment
    #: (github_login), or a role rather than a preference (is_host,
    #: is_developer, is_admin, admitted_at). A client handing us one of
    #: those is not rejected with an error naming it; it is never read.
    _PATCHABLE_TEXT = {"first_name": 80, "last_name": 80, "company_name": 160}
    _PATCHABLE_ENUM = {"role": access.ROLES, "team_size": access.TEAM_SIZES}

    @app.patch("/v1alpha1/me", tags=["browser"])
    async def update_me(
        request: Request,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        payload = await _json_object(request)
        fields: dict[str, str] = {}

        raw = payload.get("display_name")
        if raw is not None and not isinstance(raw, str):
            raise HTTPException(
                status_code=400, detail="display_name must be a string or null"
            )
        if isinstance(raw, str):
            name = raw.strip()
            if len(name) > 80:
                raise HTTPException(
                    status_code=400, detail="display_name is limited to 80 characters"
                )
            # An empty string is a user clearing the field, not a request to
            # leave it alone. `upsert_profile` coalesces null to "keep the
            # existing value", so an empty submission has to be rejected
            # rather than silently doing nothing the user can see.
            if name == "":
                raise HTTPException(
                    status_code=400, detail="display_name cannot be empty"
                )
            fields["display_name"] = name

        for field, cap in _PATCHABLE_TEXT.items():
            value = payload.get(field)
            if value is None:
                continue
            if not isinstance(value, str):
                raise HTTPException(status_code=400, detail=f"{field} must be a string")
            trimmed = value.strip()
            if not trimmed:
                raise HTTPException(status_code=400, detail=f"{field} cannot be empty")
            if len(trimmed) > cap:
                raise HTTPException(
                    status_code=400, detail=f"{field} is limited to {cap} characters"
                )
            fields[field] = trimmed

        for field, allowed in _PATCHABLE_ENUM.items():
            value = payload.get(field)
            if value is None:
                continue
            if value not in allowed:
                raise HTTPException(
                    status_code=400,
                    detail=f"{field} must be one of: {', '.join(sorted(allowed))}",
                )
            fields[field] = value

        return _jsonable(dbmod.update_profile_fields(db, user_id, **fields))

    # `current_user`, not `admitted_user`: this route is how an un-admitted
    # account asks to be admitted. Gating it behind admission would make the
    # only way in require already being in.
    @app.post("/v1alpha1/access-request", tags=["browser"])
    async def create_access_request(
        request: Request,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        # A POSITIVE allow-list, not a denylist of decided states: only
        # "needs_onboarding" (never asked) and "pending" (already asked,
        # not yet decided — resubmitting to edit the answer is allowed)
        # may proceed. `access_state_for` is the single source of truth for
        # this account's state, and any state this route doesn't
        # explicitly recognise as submittable — "admitted", "declined", or
        # one added later — must fail closed (refused) rather than open
        # (silently allowed through), which a denylist of just
        # ("admitted", "declined") would not guarantee.
        #
        # This also closes a real defect: `submit_access_request`'s
        # bare-INSERT branch (the first time an account ever submits) has
        # no `where status = 'pending'` guard, because there is no existing
        # row for it to guard. An account carrying `admitted_at` with NO
        # access_requests row — exactly what a hand-run
        # `UPDATE public.profiles SET admitted_at = now()` produces — would
        # otherwise sail through that bare INSERT and manufacture a fresh
        # `pending` row for an account that is already admitted.
        state = dbmod.access_state_for(db, user_id)
        if state not in ("needs_onboarding", "pending"):
            # Re-submitting after a decision would reset it to pending —
            # silently un-deciding something an admin decided. An admitted
            # account edits these fields through PATCH /v1alpha1/me.
            raise HTTPException(
                status_code=409, detail="this account's access is already decided"
            )

        payload = await _json_object(request)
        try:
            submission = access.parse_submission(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

        # Derived, never accepted from the body: the domain is a fact about
        # the verified signup address, not a claim the client gets to make.
        domain, personal = derive_email_facts(dbmod.email_for_user(db, user_id))

        dbmod.upsert_profile(db, user_id)  # the FK target must exist
        dbmod.submit_access_request(
            db, user_id, submission,
            email_domain=domain, is_personal_email=personal,
        )
        return {"access": dbmod.access_state_for(db, user_id)}

    # -- the admin queue ----------------------------------------------------
    #
    # EVERY route below sits on ``admin_user``. One of them left on
    # ``current_user`` would not be a smaller bug than three: this is the
    # only surface in the system that grants product access, so a single
    # ungated write is a privilege escalation for every signed-in account.

    @app.get("/v1alpha1/admin/access-requests", tags=["admin"])
    async def list_requests(
        status: str = "pending",
        _admin: str = Depends(admin_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        # Validated HERE, because `list_access_requests` does not check its
        # argument against the CHECK constraint — an unknown status matches
        # no row and returns [], which renders as "nobody is waiting". A
        # typo must not look like an empty queue.
        if status not in ("pending", "admitted", "declined"):
            raise HTTPException(status_code=400, detail="unknown status")
        return [_jsonable(r) for r in dbmod.list_access_requests(db, status=status)]

    @app.post("/v1alpha1/admin/access-requests/{user_id}/approve", tags=["admin"])
    async def approve_request(
        user_id: str,
        admin_id: str = Depends(admin_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        _uuid_or_400(user_id)
        # 404, not 200, when nothing was pending: reporting success for a
        # call that changed nothing is how a queue silently stops working.
        #
        # It is also what makes the email exactly-once. The guard below
        # matches only a row still in 'pending', so a second approve returns
        # here and never reaches the mailer — no sent-log table needed.
        if not dbmod.approve_access_request(db, user_id, decided_by=admin_id):
            raise HTTPException(status_code=404, detail="no pending request")
        emailed = await _send_decision_email(db, mailer, settings, user_id, admitted=True)
        return {"user_id": user_id, "status": "admitted", "emailed": emailed}

    @app.post("/v1alpha1/admin/access-requests/{user_id}/decline", tags=["admin"])
    async def decline_request(
        user_id: str,
        admin_id: str = Depends(admin_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        _uuid_or_400(user_id)
        if not dbmod.decline_access_request(db, user_id, decided_by=admin_id):
            raise HTTPException(status_code=404, detail="no pending request")
        emailed = await _send_decision_email(db, mailer, settings, user_id, admitted=False)
        return {"user_id": user_id, "status": "declined", "emailed": emailed}

    @app.get("/v1alpha1/machines", tags=["browser"])
    async def list_machines(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        # One extra query for every bound machine's chips, not one per
        # machine — same reasoning ``pools_for_machines_of_owner`` itself
        # gives for being a single aggregate rather than N lookups.
        chips = dbmod.pools_for_machines_of_owner(db, user_id)
        out = []
        for m in dbmod.list_machines_for_owner(db, user_id):
            item = _jsonable(m)
            item["pools"] = chips.get(item["id"], [])
            out.append(item)
        return out

    @app.post("/v1alpha1/device/approve", tags=["browser"])
    async def approve(
        request: Request,
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        payload = await _json_object(request)
        user_code = payload.get("user_code")
        if not isinstance(user_code, str) or not user_code:
            raise HTTPException(status_code=400, detail="user_code required")

        # Which flow is being approved is read off the stored row, not the
        # request body — the approver types a code, and nothing else about
        # it is theirs to assert.
        code_row = dbmod.fetch_device_code_by_user_code(db, user_code.strip().upper())
        if code_row is not None and code_row.get("kind") == "cli":
            if payload.get("pool_id") is not None:
                # pool_id binds a MACHINE to a pool. A credential is never
                # placed on, so accepting and ignoring it would confirm a
                # request that did not do what it said.
                raise HTTPException(
                    status_code=400, detail="pool_id does not apply to a CLI login"
                )
            dbmod.upsert_profile(db, user_id)
            try:
                credential_id = cli_auth.approve_cli_code(
                    db, user_code.strip().upper(), user_id
                )
            except cli_auth.CliCodeNotFound:
                raise HTTPException(status_code=404, detail="unknown code") from None
            except cli_auth.CliCodeExpired:
                raise HTTPException(status_code=410, detail="code expired") from None
            return {
                "credential_id": str(credential_id),
                "kind": "cli",
                "status": "approved",
            }

        # An optional pool to auto-attach the approved machine to. Checked
        # BEFORE the device code is ever touched: a malformed, unknown, or
        # not-a-member pool_id must refuse the whole approval, and refusing
        # AFTER the code was already consumed would strand the volunteer's
        # agent — its one-shot code burned on a request the caller gets to
        # retry, with no way back except starting device code from scratch.
        raw_pool_id = payload.get("pool_id")
        pool_id: str | None = None
        if raw_pool_id is not None:
            if not isinstance(raw_pool_id, str) or not raw_pool_id:
                raise HTTPException(status_code=404, detail="unknown pool")
            try:
                pool = dbmod.fetch_pool_for_member(db, raw_pool_id, user_id)
            except psycopg.errors.InvalidTextRepresentation:
                pool = None
            if pool is None:
                # Same fold as every other pool lookup in this file: unknown,
                # not-a-uuid, and "real pool but you're not in it" all read
                # identically, so a guesser cannot use this to learn which
                # pool ids are real.
                raise HTTPException(status_code=404, detail="unknown pool")
            pool_id = raw_pool_id

        # Ownership is established here and nowhere else, from the verified
        # JWT sub. It never comes from the body.
        dbmod.upsert_profile(db, user_id)
        try:
            if pool_id is not None:
                # Approve and bind as one unit: a bind failure must roll the
                # approval back rather than leave a machine approved but not
                # attached to the pool the caller specifically asked for.
                with db.transaction():
                    machine_id = enrolment.approve_device_code(
                        db, user_code.strip().upper(), user_id
                    )
                    # approve_device_code short-circuits on an already-approved
                    # code, returning its machine_id with no ownership check —
                    # by design, so re-approving your own code stays a no-op.
                    # But that means machine_id here is not guaranteed to be
                    # one user_id owns: without this check, any admitted user
                    # who ever saw this user_code (already redeemed by someone
                    # else) could bind that stranger's machine into their own
                    # pool. Same 404 fold as every other pool/machine lookup
                    # in this file — this route must not distinguish "no such
                    # code" from "that code's machine isn't yours".
                    if dbmod.fetch_machine_for_owner(
                        db, str(machine_id), user_id
                    ) is None:
                        raise HTTPException(
                            status_code=404, detail="unknown code"
                        )
                    dbmod.bind_machine_pool(
                        db, machine_id=str(machine_id), pool_id=pool_id
                    )
            else:
                machine_id = enrolment.approve_device_code(
                    db, user_code.strip().upper(), user_id
                )
        except enrolment.DeviceCodeNotFound:
            raise HTTPException(status_code=404, detail="unknown code") from None
        except enrolment.DeviceCodeExpired:
            raise HTTPException(status_code=410, detail="code expired") from None
        except enrolment.NodeAlreadyEnrolled:
            raise HTTPException(
                status_code=409, detail="this machine is already enrolled"
            ) from None
        # `kind` rides alongside machine_id rather than replacing it, so the
        # console can branch on one key in both cases and no agent already
        # in the field has to be updated in lockstep.
        return {"machine_id": str(machine_id), "kind": "machine", "status": "approved"}

    @app.post("/v1alpha1/machines/{machine_id}/revoke", tags=["browser"])
    async def revoke(
        machine_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        try:
            revoked = enrolment.revoke_machine(db, machine_id, user_id)
        except psycopg.errors.InvalidTextRepresentation:
            # A machine_id that is not even a uuid. Same answer as one that
            # simply is not yours.
            revoked = False
        if not revoked:
            # 404 for "not yours" as well as "does not exist": a 403 split
            # would confirm to a guesser that the id is real.
            raise HTTPException(status_code=404, detail="unknown machine")
        return {"machine_id": machine_id, "status": "revoked"}

    @app.get("/v1alpha1/cli-credentials", tags=["browser"])
    async def list_cli_credentials(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Every CLI credential this account holds. ``current_user``, not
        ``admitted_user``: an account still waiting on approval must be
        able to see and revoke a credential it has already minted."""
        return [_jsonable(r) for r in dbmod.list_cli_credentials_for_owner(db, user_id)]

    @app.post("/v1alpha1/cli-credentials/{credential_id}/revoke", tags=["browser"])
    async def revoke_cli_credential(
        credential_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Revoke a credential. 404 — not 403 — when it belongs to someone
        else or does not exist, indistinguishably, so this cannot be used to
        learn which credential ids are real. Takes effect on the revoked
        token's very next request: ``authenticate_cli`` reads ``status`` on
        every call and there is no cache in front of it."""
        if not dbmod.revoke_cli_credential_row(db, credential_id, user_id):
            raise HTTPException(status_code=404, detail="unknown credential")
        return {"revoked": True}

    # -- browser-facing: pools and invites -----------------------------------
    #
    # A pool is a team; membership is what every read below scopes on, never
    # ``pools.owner_id`` alone (see ``create_pool``'s own docstring on that).
    # Creating a pool requires admission — it is state creation, the thing
    # the alpha gate exists to block — but reading one you already belong to
    # does not, the same "reads stay open" rule ``admitted_user`` documents.

    @app.post("/v1alpha1/pools", status_code=201, tags=["browser"])
    async def create_pool_route(
        request: Request,
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        payload = await _json_object(request)
        name = _validated_pool_name(payload)
        pool = dbmod.create_pool(db, name=name, owner_id=user_id)
        return _jsonable(pool)

    @app.get("/v1alpha1/pools", tags=["browser"])
    async def list_pools_route(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        return [_jsonable(p) for p in dbmod.list_pools_for_user(db, user_id)]

    @app.get("/v1alpha1/pools/{pool_id}", tags=["browser"])
    async def get_pool_route(
        pool_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        # Authorize BEFORE listing: list_pool_members takes no viewer
        # param by design, so membership has to be established here, first,
        # or it would list any pool's roster to anyone who could guess an
        # id. 404, not 403 — see fetch_pool_for_member's own docstring.
        try:
            pool = dbmod.fetch_pool_for_member(db, pool_id, user_id)
        except psycopg.errors.InvalidTextRepresentation:
            pool = None  # not even a uuid; same answer as "not found"
        if pool is None:
            raise HTTPException(status_code=404, detail="unknown pool")
        members = dbmod.list_pool_members(db, pool_id)
        return {**_jsonable(pool), "members": [_jsonable(m) for m in members]}

    @app.get("/v1alpha1/pools/{pool_id}/machines", tags=["browser"])
    async def list_pool_machines_route(
        pool_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Every machine this pool has, across all of its members.

        Authorize BEFORE listing, exactly as ``get_pool_route`` does:
        ``list_pool_machines`` takes no viewer param by design, so membership
        has to be established here, first, or any pool's fleet would be
        readable by anyone who could guess an id. 404, not 403 — see
        ``fetch_pool_for_member``'s own docstring.
        """
        try:
            pool = dbmod.fetch_pool_for_member(db, pool_id, user_id)
        except psycopg.errors.InvalidTextRepresentation:
            # A pool_id that is not even a uuid. Same answer as one that
            # simply is not yours.
            pool = None
        if pool is None:
            raise HTTPException(status_code=404, detail="unknown pool")
        return [_jsonable(m) for m in dbmod.list_pool_machines(db, pool_id)]

    @app.patch("/v1alpha1/pools/{pool_id}", tags=["browser"])
    async def rename_pool_route(
        pool_id: str,
        request: Request,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Rename a pool. Owner only — checked here, against this pool's
        row, before anything is written. 404, not 403, whether the pool does
        not exist, the caller is a stranger to it, or the caller is a member
        who simply isn't its owner: the same doctrine, and for the same
        reason, as the three invite routes.

        Not gated by ``admitted_user``, for the reason
        ``create_pool_invite_route`` states: renaming a pool already requires
        owning one, and owning one already required admission at create time.
        """
        try:
            pool = dbmod.fetch_pool_for_member(db, pool_id, user_id)
        except psycopg.errors.InvalidTextRepresentation:
            pool = None
        if pool is None or str(pool["owner_id"]) != user_id:
            raise HTTPException(status_code=404, detail="unknown pool")

        # Ownership first, validation second: a non-owner must get the same
        # 404 for a well-formed name as for a malformed one, or the error
        # code itself tells them the pool is real.
        name = _validated_pool_name(await _json_object(request))

        updated = dbmod.rename_pool(db, pool_id=pool_id, name=name)
        if updated is None:
            raise HTTPException(status_code=404, detail="unknown pool")
        return _jsonable(updated)

    @app.put(
        "/v1alpha1/pools/{pool_id}/machines/{machine_id}",
        status_code=204, tags=["browser"],
    )
    async def bind_pool_machine_route(
        pool_id: str,
        machine_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Opt one of the caller's own machines into serving one of the
        caller's own pools. Both halves are scoped to this same caller —
        pool via membership, machine via ownership — and both read 404,
        never 403, for the usual reason: a 403 on either would confirm to a
        guesser that the id is real. Not gated by ``admitted_user``: as with
        minting an invite, owning the pool or the machine already required
        admission at create time.
        """
        try:
            pool = dbmod.fetch_pool_for_member(db, pool_id, user_id)
        except psycopg.errors.InvalidTextRepresentation:
            pool = None
        if pool is None:
            raise HTTPException(status_code=404, detail="unknown pool")
        try:
            machine = dbmod.fetch_machine_for_owner(db, machine_id, user_id)
        except psycopg.errors.InvalidTextRepresentation:
            machine = None
        if machine is None:
            raise HTTPException(status_code=404, detail="unknown machine")
        dbmod.bind_machine_pool(db, machine_id=machine_id, pool_id=pool_id)
        return Response(status_code=204)

    @app.delete(
        "/v1alpha1/pools/{pool_id}/machines/{machine_id}",
        status_code=204, tags=["browser"],
    )
    async def unbind_pool_machine_route(
        pool_id: str,
        machine_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """The inverse of ``bind_pool_machine_route`` — same scoping, same
        404 doctrine. Unbinding a pair that was never bound is a no-op
        (``unbind_machine_pool``'s own tolerant-delete stance), not an
        error, as long as both the pool and the machine are the caller's."""
        try:
            pool = dbmod.fetch_pool_for_member(db, pool_id, user_id)
        except psycopg.errors.InvalidTextRepresentation:
            pool = None
        if pool is None:
            raise HTTPException(status_code=404, detail="unknown pool")
        try:
            machine = dbmod.fetch_machine_for_owner(db, machine_id, user_id)
        except psycopg.errors.InvalidTextRepresentation:
            machine = None
        if machine is None:
            raise HTTPException(status_code=404, detail="unknown machine")
        dbmod.unbind_machine_pool(db, machine_id=machine_id, pool_id=pool_id)
        return Response(status_code=204)

    @app.post("/v1alpha1/pools/{pool_id}/invites", status_code=201, tags=["browser"])
    async def create_pool_invite_route(
        pool_id: str,
        request: Request,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Mint a one-time invite link. Owner only — checked here, against
        this pool's row, before anything is written. 404, not 403, whether
        the pool does not exist, the caller is a stranger to it, or the
        caller is a member who simply isn't its owner: a 403 for the last
        case would confirm the pool is real to someone who isn't in it at
        all, and the caller here has no way to tell the three apart from
        each other regardless.

        Not gated by ``admitted_user``: minting an invite already requires
        owning a pool, and owning a pool already required admission at
        create time. The four routes that need the gate directly are named
        exhaustively on ``admitted_user`` itself.
        """
        payload = await _json_object(request)
        try:
            pool = dbmod.fetch_pool_for_member(db, pool_id, user_id)
        except psycopg.errors.InvalidTextRepresentation:
            pool = None
        if pool is None or str(pool["owner_id"]) != user_id:
            raise HTTPException(status_code=404, detail="unknown pool")

        raw_hours = payload.get("expires_hours")
        if raw_hours is None:
            hours = DEFAULT_INVITE_EXPIRES_HOURS
        elif (
            isinstance(raw_hours, (int, float))
            and not isinstance(raw_hours, bool)
            and 0 < raw_hours <= MAX_INVITE_EXPIRES_HOURS
        ):
            hours = raw_hours
        else:
            raise HTTPException(
                status_code=400,
                detail=f"expires_hours must be a positive number, at most "
                       f"{MAX_INVITE_EXPIRES_HOURS}",
            )

        raw_uses = payload.get("uses")
        if raw_uses is None:
            uses = DEFAULT_POOL_INVITE_USES
        elif (
            isinstance(raw_uses, int)
            and not isinstance(raw_uses, bool)
            and 0 < raw_uses <= MAX_POOL_INVITE_USES
        ):
            uses = raw_uses
        else:
            raise HTTPException(
                status_code=400,
                detail=f"uses must be a positive integer, at most "
                       f"{MAX_POOL_INVITE_USES}",
            )

        token = new_invite_token()
        dbmod.create_pool_invite(
            db,
            pool_id=pool_id,
            created_by=user_id,
            token_hash=hash_invite_token(token),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=hours),
            uses=uses,
        )
        # The raw token appears in a response exactly this once. It is
        # never stored (only its hash is), so this is also the only place
        # it could ever be recovered from.
        return {"token": token}

    @app.get("/v1alpha1/pools/{pool_id}/invites", tags=["browser"])
    async def get_pool_invite_route(
        pool_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """The pool's current standing invite, owner only — same 404
        doctrine and ownership check as minting one. Returns
        ``fetch_outstanding_invite``'s narrow dict (``uses_remaining``,
        ``expires_at``, ``created_at`` — never a token or its hash) or
        ``{}`` when nothing is currently redeemable, so the console can
        show "generate a link" instead of a dead one.
        """
        try:
            pool = dbmod.fetch_pool_for_member(db, pool_id, user_id)
        except psycopg.errors.InvalidTextRepresentation:
            pool = None
        if pool is None or str(pool["owner_id"]) != user_id:
            raise HTTPException(status_code=404, detail="unknown pool")
        invite = dbmod.fetch_outstanding_invite(db, pool_id)
        return _jsonable(invite) if invite is not None else {}

    @app.delete("/v1alpha1/pools/{pool_id}/invites", tags=["browser"])
    async def revoke_pool_invites_route(
        pool_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Kill every invite ever issued for this pool, owner only — same
        404 doctrine and ownership check as the other two invite routes."""
        try:
            pool = dbmod.fetch_pool_for_member(db, pool_id, user_id)
        except psycopg.errors.InvalidTextRepresentation:
            pool = None
        if pool is None or str(pool["owner_id"]) != user_id:
            raise HTTPException(status_code=404, detail="unknown pool")
        revoked = dbmod.revoke_pool_invites(db, pool_id=pool_id)
        return {"revoked": revoked}

    @app.post("/v1alpha1/invites/accept", tags=["browser"])
    async def accept_invite(
        request: Request,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Redeem a workspace invite — deliberately on ``current_user``, not
        ``admitted_user``: a not-yet-admitted account is exactly who calls
        this, and gating it behind admission would make the only path in
        require already being in.

        Accepting no longer admits (0009). An admitted caller joins the
        pool outright; anybody else has the join banked on their access
        request and lands in the pool when an admin approves them. That is
        what ``joined`` reports, so the console can tell "you are in the
        workspace" from "you will be, once you are approved".
        """
        payload = await _json_object(request)
        token = payload.get("token")
        if not isinstance(token, str) or not token:
            raise HTTPException(status_code=400, detail="token required")
        # The account may be signing in for the very first time — upsert
        # the profile row before consuming the invite. Both of consume_pool_
        # invite's outcomes carry a FK to it: pool_members.user_id when the
        # join lands, access_requests.user_id when it is only banked.
        dbmod.upsert_profile(db, user_id)
        result = dbmod.consume_pool_invite(
            db, token_hash=hash_invite_token(token), user_id=user_id
        )
        if result is None:
            # Unknown, expired, and exhausted all land here, indistinguishably
            # — same fold consume_pool_invite itself documents.
            raise HTTPException(status_code=404, detail="invalid or expired invite")
        return {
            "pool_id": str(result["pool_id"]),
            "name": result["name"],
            "joined": result["admitted"],
        }

    # -- browser-facing: job ownership --------------------------------------
    #
    # A developer submits a job with a Supabase JWT; the row this writes is
    # the *only* record of who owns it — the coordinator has no accounts
    # and forwards every job route unscoped behind the operator token. So
    # every read, cancel, and artifact fetch below consults this table
    # *before* ever forwarding, and refuses with 404 (never 403 — a 403
    # would confirm the id exists) rather than let ownership be decided by
    # whatever the coordinator happens to answer.

    @app.post("/v1alpha1/jobs", status_code=201, tags=["browser"])
    async def submit_job(
        request: Request,
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        raw = await request.body()
        if len(raw) > MAX_JSON_BODY_BYTES:
            raise HTTPException(status_code=413, detail="request body too large")
        payload = await _json_object(request)
        _storage_gate(db, user_id)
        # owner_id is never accepted from the body — whatever the caller
        # put there (if anything) is simply not forwarded or looked at.
        payload.pop("owner_id", None)
        # A pool waiver requires `fetch_pool_for_member` to have confirmed
        # membership first, and this route never looks the caller up in
        # `pool_members` — only /v1alpha1/jobs/from-repo does. So a raw spec
        # carrying either half of the pool coupling is refused outright,
        # named at the path that CAN grant it, rather than forwarded to a
        # coordinator that would place a stranger's arbitrary code on a
        # volunteer machine sandboxed only because the placement gate never
        # got the chance to confine it to a team.
        spec_inner = payload.get("spec") if isinstance(payload.get("spec"), dict) else {}
        isolation_raw = spec_inner.get("isolation")
        isolation = isolation_raw if isinstance(isolation_raw, dict) else {}
        placement_raw = spec_inner.get("placement")
        placement = placement_raw if isinstance(placement_raw, dict) else {}
        if isolation.get("allowFallback") or placement.get("pool", "any") != "any":
            raise HTTPException(
                status_code=400,
                detail="pool jobs must be submitted via /v1alpha1/jobs/from-repo",
            )
        r = await coordinator.forward(
            "POST",
            "/v1alpha1/jobs",
            content=json.dumps(payload).encode(),
            media_type="application/json",
        )
        if r.status_code >= 300:
            return _passthrough(r)
        try:
            job = r.json()
        except ValueError:
            return _passthrough(r)
        job_id = job.get("job_id") if isinstance(job, dict) else None
        if not job_id:
            # The coordinator accepted the job but did not hand back an id
            # to own it by. Nothing safe to record; refuse rather than
            # silently create an unowned job no one can ever list or cancel
            # through this API.
            log.error(json.dumps({"text": "job accepted with no job_id in response"}))
            raise HTTPException(status_code=502, detail="coordinator returned no job id")
        spec = job.get("spec") if isinstance(job.get("spec"), dict) else None
        name = None
        if spec and isinstance(spec.get("metadata"), dict):
            name = spec["metadata"].get("name")
        dbmod.insert_job(
            db,
            job_id=job_id,
            owner_id=user_id,
            name=name,
            source=None,
            spec=spec,
            status=str(job.get("state") or "PENDING"),
        )
        return _passthrough(r)

    @app.post("/v1alpha1/jobs/from-repo", status_code=201, tags=["browser"])
    async def submit_job_from_repo(
        request: Request,
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Paste a GitHub repo, get a job — or get told exactly what is
        wrong with it, all of it, in one answer.

        The order below is the whole point of the endpoint. Fetch, extract,
        parse, **preflight**, and only then touch the coordinator: a repo
        with an error finding must not upload an artifact, must not submit,
        and must not leave a ``jobs`` row behind. Findings come back
        *together*, never one per round trip, because a user fixing four
        problems should need one more submit, not four.
        """
        raw = await request.body()
        if len(raw) > MAX_JSON_BODY_BYTES:
            raise HTTPException(status_code=413, detail="request body too large")
        payload = await _json_object(request)
        owner, name, ref = _parse_repo_ref(payload.get("repo"), payload.get("ref"))

        # Optional pool scoping. Checked before a single network call: a
        # pool id the caller does not belong to (or that does not exist —
        # 404 in both cases, same doctrine as `fetch_pool_for_member`
        # itself, so a guess cannot distinguish them) must not spend the
        # cost of fetching and preflighting the repo first.
        pool = _opt_str(payload.get("pool"))
        if pool is not None:
            try:
                pool_row = dbmod.fetch_pool_for_member(db, pool, user_id)
            except psycopg.errors.InvalidTextRepresentation:
                pool_row = None  # not even a uuid; same answer as "not found"
            if pool_row is None:
                raise HTTPException(status_code=404, detail="unknown pool")
            # Rebind to the database's canonical spelling, not the caller's.
            # Postgres accepts uppercase/braced/hyphen-less uuids and the
            # membership check above passes on any of them, but the
            # scheduler's gate compares exact strings against the
            # canonical-lowercase ids `pool_ids_for_machine` returns.
            # An un-normalized `pool` here would pass this check and then
            # never match that gate, leaving the job PENDING forever.
            pool = str(pool_row["id"])

        with tempfile.TemporaryDirectory(prefix="flashml-repo-") as tmpdir:
            dest = Path(tmpdir) / "src"
            try:
                # Blocking network + tar work, off the event loop.
                tar_bytes, repo_root = await run_in_threadpool(
                    _fetch_and_extract, fetch_repo, owner, name, ref, dest
                )
            except repomod.RepoError as exc:
                # The message can quote a tar member's name, which is
                # attacker-chosen: sanitise before it reaches a response or
                # a log line.
                raise HTTPException(
                    status_code=400, detail=safe_text(exc, 300)
                ) from None

            config_text = _read_config_text(repo_root)
            try:
                config = parse_flashml_yaml(config_text)
            except ConfigError as exc:
                raise HTTPException(
                    status_code=400, detail=safe_text(exc, 500)
                ) from None

            try:
                image = resolve_image(config.image)
            except UnknownImage as exc:
                raise HTTPException(
                    status_code=400, detail=safe_text(exc, 300)
                ) from None

            findings = await run_in_threadpool(preflight, config, repo_root, image)

        rendered = [f.as_dict() for f in findings]
        if any(f.level == "error" for f in findings):
            # Refused here, before a single byte leaves this process. No
            # artifact upload, no coordinator submission, no jobs row.
            return Response(
                content=json.dumps(
                    {
                        "detail": "preflight found problems that would make this "
                                  "job fail on a volunteer node",
                        "findings": rendered,
                    }
                ),
                status_code=400,
                media_type="application/json",
            )

        # An unguessable key, and deliberately not derived from the user id
        # or the repo name: it is a public-ish artifact namespace, and a
        # predictable key would let one user overwrite another's staged code
        # between compile and claim.
        code_key = f"uploads/{uuid.uuid4().hex}/code.tar.gz"
        code_uri = f"artifact://{code_key}"

        try:
            # Compiled *before* the upload: a config this module cannot
            # compile should not leave a multi-megabyte orphan behind in the
            # artifact store. For a federated run this compiles round 0 —
            # the round the driver is about to submit — so a config that
            # cannot become a valid round fails here, in the response, and
            # not silently on a background thread nobody is watching.
            if config.is_federated:
                # How this run's rounds are cut, decided once here from the
                # machines online right now — never by the submitter, who
                # cannot see the Crew, and never re-counted per round, which
                # would move the chunk layout underneath a resumed run.
                fleet = fleet_shape(
                    dbmod.count_online_machines(db, pool_id=pool)
                )
                spec = compile_federated_round(
                    config, image, code_uri, config.name,
                    round_index=0, weights_uri=None,
                    slot_chunks=fedavgmod.slot_chunks_for(
                        fleet, 0, float(config.sync_every or 1.0)
                    ),
                    total_chunks=fleet.total_chunks,
                    pool=pool,
                )
            else:
                spec = compile_to_jobspec(
                    config, image, code_uri, config.name, pool=pool
                )
        except CompileError as exc:
            raise HTTPException(status_code=400, detail=safe_text(exc, 500)) from None

        upload = await forward_idempotent(
            coordinator,
            "PUT",
            f"/v1alpha1/artifacts/{code_key}",
            content=tar_bytes,
            media_type="application/gzip",
        )
        if upload.status_code >= 300:
            log.error(
                json.dumps({"text": "staging the repo artifact failed",
                            "status": upload.status_code})
            )
            # Two different failures, two different things for the user to do.
            # A gateway status means the coordinator is still booting and
            # waiting is the whole fix; the old copy ("could not stage the
            # repo") sent people off to debug a repo that was never the
            # problem.
            if upload.status_code in GATEWAY_STATUSES:
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "the coordinator is starting up and did not accept the "
                        "upload in time — try again in about a minute"
                    ),
                )
            raise HTTPException(status_code=502, detail="could not stage the repo")

        if config.is_federated:
            # A federated run is N coordinator jobs, one per round, so there
            # is no single coordinator job id to own it by. The parent id is
            # minted here and each round's coordinator job is recorded
            # against it in `job_rounds`; nothing is submitted from this
            # request at all — the driver submits round 0 itself, so a run
            # can never end up with a round the driver does not know it owns.
            job_id = fedavgmod.new_federated_job_id()
            federated_source: dict[str, Any] = {
                "type": "github",
                "owner": owner,
                "repo": name,
                "ref": ref,
                "code_artifact": code_uri,
                # Only a federated row carries these. An independent
                # row's `source` is byte-identical to what it has always
                # been, so nothing reading it has to learn a new shape.
                "mode": config.mode,
                # `epochs`/`sync_every` are what the author asked for;
                # `rounds` and `slots` are what the fleet turned that into,
                # recorded because they are not recoverable later — the Crew's
                # online count has moved on by the time anyone reads this row.
                "epochs": config.epochs,
                "sync_every": config.sync_every,
                "rounds": config.round_count,
                "slots": fleet.slots,
            }
            if pool is not None:
                federated_source["pool"] = pool
            dbmod.insert_job(
                db,
                job_id=job_id,
                owner_id=user_id,
                name=spec["metadata"]["name"],
                source=federated_source,
                spec=spec,
                status="PENDING",
                pool_id=pool,
            )
            start_federated_job(
                fedavgmod.FederatedRun(
                    job_id=job_id,
                    job_name=config.name,
                    config=config,
                    image=image,
                    code_artifact_uri=code_uri,
                    pool=pool,
                    fleet=fleet,
                ),
                settings=settings,
                connect=request.app.state.connect,
            )
            return Response(
                content=json.dumps({
                    "job_id": job_id,
                    "state": "PENDING",
                    "mode": config.mode,
                    "epochs": config.epochs,
                    "sync_every": config.sync_every,
                    "rounds": config.round_count,
                    "slots": fleet.slots,
                    "findings": rendered,
                }),
                status_code=201,
                media_type="application/json",
            )

        r = await coordinator.forward(
            "POST",
            "/v1alpha1/jobs",
            content=json.dumps(spec).encode(),
            media_type="application/json",
        )
        if r.status_code >= 300:
            return _passthrough(r)
        try:
            job = r.json()
        except ValueError:
            return _passthrough(r)
        job_id = job.get("job_id") if isinstance(job, dict) else None
        if not job_id:
            log.error(json.dumps({"text": "job accepted with no job_id in response"}))
            raise HTTPException(status_code=502, detail="coordinator returned no job id")

        independent_source: dict[str, Any] = {
            "type": "github",
            "owner": owner,
            "repo": name,
            "ref": ref,
            "code_artifact": code_uri,
        }
        if pool is not None:
            independent_source["pool"] = pool
        dbmod.insert_job(
            db,
            job_id=job_id,
            # From the verified JWT, always. The body has no say in this,
            # and there is no branch here that could give it one.
            owner_id=user_id,
            name=spec["metadata"]["name"],
            source=independent_source,
            spec=spec,
            status=str(job.get("state") or "PENDING"),
            pool_id=pool,
        )
        return Response(
            content=json.dumps({**job, "findings": rendered}),
            status_code=201,
            media_type="application/json",
        )

    @app.get("/v1alpha1/jobs", tags=["browser"])
    async def list_jobs_route(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        # One query for both halves of visibility — owned, and reachable
        # through a shared pool — carrying the pool_id and submitter the
        # console renders. This replaced two queries whose ids were unioned
        # in Python; the union is now in the SQL, and it no longer discards
        # the columns that came back with it.
        scopes = dbmod.list_job_scopes_for_viewer(db, user_id)
        # A federated parent id names no coordinator job, so it can never
        # match anything in the coordinator's list; dropping it here is what
        # lets a user whose only jobs are federated skip the round trip
        # entirely instead of fetching a list to throw all of it away.
        seen = {j for j in scopes if not fedavgmod.is_federated_job_id(j)}

        # A federated run is one coordinator job per round, so it is not in
        # the coordinator's list at all and has to be added from this table.
        # `list_federated_jobs_for_viewer` applies the same owner-or-member
        # predicate as `scopes`, so every id it returns is already a key
        # there — the `.get` default is belt-and-braces, not a real branch.
        federated = [
            {
                "job_id": row["id"],
                "name": row.get("name"),
                "state": row.get("status"),
                "mode": "federated",
                **scopes.get(row["id"], {"pool_id": None, "submitted_by": None}),
            }
            for row in dbmod.list_federated_jobs_for_viewer(db, user_id)
        ]
        if not seen:
            # Nothing to scope down to; skip the coordinator round trip
            # rather than fetch a list of jobs we would only throw away.
            return federated
        r = await coordinator.forward("GET", "/v1alpha1/jobs")
        if r.status_code >= 300:
            return _passthrough(r)
        try:
            jobs = r.json()
        except ValueError:
            return _passthrough(r)
        if not isinstance(jobs, list):
            return _passthrough(r)
        # The coordinator has no notion of accounts and returns every job
        # unscoped behind the operator token; `scopes` (owned or reachable
        # through a shared pool) is the only place that filter can be
        # applied — and now also the only place the workspace label comes
        # from, since the coordinator has never heard of pools.
        visible = [
            {**j, **scopes[j["job_id"]]}
            for j in jobs
            if isinstance(j, dict) and j.get("job_id") in seen
        ]
        # Write down every terminal state this page just learned. No extra
        # network call — the coordinator's list already carries the states —
        # and one batched, self-guarding UPDATE for the whole page, so the
        # marginal cost of an open jobs list is a statement that matches no
        # rows. It is here as well as on the detail route because a job's
        # outcome must not depend on somebody having opened its own page:
        # `GET /me/metrics` counts succeeded/partial/failed out of this
        # column, and outcomes only recorded for jobs that were being
        # watched would make that page a survey of browsing habits.
        dbmod.sync_observed_job_states(
            db,
            [(j["job_id"], str(j["state"])) for j in visible
             if is_terminal_state(j.get("state"))],
        )
        return visible + federated

    @app.get("/v1alpha1/jobs/{job_id}", tags=["browser"])
    async def get_job_route(
        job_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        row = dbmod.fetch_job_for_viewer(db, job_id, user_id)
        if row is None:
            # Not found and not yours look identical: a 403 here would
            # confirm to a guesser that the id exists. "Yours" now means
            # "owned by you, or in a pool you belong to" — fetch_job_for_viewer
            # is the one place that widened scope is applied.
            raise HTTPException(status_code=404, detail="unknown job")
        if fedavgmod.is_federated_job_id(job_id):
            # A federated run has no single coordinator job — it is one job
            # per round — so forwarding this id would ask the coordinator
            # about something it has never heard of and answer 404 for a job
            # the user does own. The local row plus the round history IS the
            # job here. Unreachable for every non-federated id, so the
            # forwarding path below is unchanged.
            #
            # `row["owner_id"]` here, not `user_id`: list_job_rounds_for_owner
            # is scoped to the job's actual owner, and a viewing pool member
            # is not that owner. fetch_job_for_viewer above is the
            # authorization check; this is a data query for a job already
            # confirmed visible, so it must use the id that query actually
            # requires to return anything.
            #
            # No storage-accounting hook here, deliberately. A federated run
            # is measured by the driver that runs it, at the moment it
            # finishes (`fedavg.record_run_footprint`) — not from this route.
            # Two reasons: this route answers a federated job entirely from
            # local rows and must keep costing zero coordinator round trips,
            # and the driver observes the run ending whether or not anybody
            # has a page open, which a poll-driven hook cannot.
            rounds = dbmod.list_job_rounds_for_owner(db, job_id, row["owner_id"])
            source = row.get("source") or {}
            return {
                "job_id": job_id,
                "state": row.get("status"),
                "mode": source.get("mode"),
                "rounds_requested": source.get("rounds"),
                "rounds_completed": len(rounds),
                "spec": row.get("spec"),
                "created_at": str(row["created_at"]) if row.get("created_at") else None,
                "finished_at": (
                    str(row["finished_at"]) if row.get("finished_at") else None
                ),
                "pool_id": (
                    None if row.get("pool_id") is None else str(row["pool_id"])
                ),
                "submitted_by": dbmod.display_name_for(db, row["owner_id"]),
            }
        r = await coordinator.forward("GET", f"/v1alpha1/jobs/{_seg(job_id)}")
        # Merge the workspace label in rather than passing the coordinator's
        # body straight through: the detail page renders its own breadcrumb
        # and may have been deep-linked, so it cannot rely on having loaded
        # the list. `row` is already in hand from the visibility check above,
        # so this costs one profile lookup and no extra job query.
        if r.status_code >= 300:
            return _passthrough(r)
        try:
            job = r.json()
        except ValueError:
            return _passthrough(r)
        if not isinstance(job, dict):
            return _passthrough(r)
        job["pool_id"] = (
            None if row.get("pool_id") is None else str(row["pool_id"])
        )
        job["submitted_by"] = dbmod.display_name_for(db, row["owner_id"])

        # THE RECORDING HOOK, Mode A half — and the only place in this API
        # where a non-federated job is ever observed to have stopped. The
        # coordinator's answer is already in hand, so noticing costs
        # nothing; both writes below are guarded on a column of the row
        # fetched for the visibility check above, so a page left polling a
        # finished job re-runs neither.
        #
        # The two are separate on purpose. Recording the STATE is a cheap
        # local write and is what `GET /me/metrics` counts outcomes from,
        # so it happens on every terminal observation and does not care
        # whether the measurement succeeded. Recording the FOOTPRINT costs a
        # coordinator round trip and is guarded by its own marker, so a
        # listing that failed is retried on the next poll instead of being
        # remembered as a measurement.
        if is_terminal_state(job.get("state")):
            dbmod.sync_observed_job_states(db, [(job_id, str(job["state"]))])
            if row.get("artifact_bytes_recorded_at") is None:
                await _record_artifact_footprint(coordinator, db, job_id)
        return job

    @app.get("/v1alpha1/jobs/{job_id}/rounds", tags=["browser"])
    async def get_job_rounds(
        job_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """The federated-averaging history of a job the caller can see.

        Visibility is checked exactly as the rest of this block does it —
        against the ``jobs`` table, before anything else, answering 404 (not
        403) for a job that exists but the caller cannot see, so this route
        cannot be used to learn which job ids are real. The owner, or any
        member of the job's pool, may read it; only the owner may cancel it.

        The listing query itself (``list_job_rounds_for_owner``) is still
        owner-scoped, not viewer-scoped — it has no notion of pools — so it
        is called with the job's own ``owner_id`` from the row just fetched,
        not with ``user_id``. A viewing pool member is not that owner, and
        passing ``user_id`` through here would silently return an empty
        list to every teammate instead of the job's real history.
        """
        row = dbmod.fetch_job_for_viewer(db, job_id, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return [_jsonable(r)
                for r in dbmod.list_job_rounds_for_owner(db, job_id, row["owner_id"])]

    @app.get("/v1alpha1/jobs/{job_id}/events", tags=["browser"])
    async def get_job_events(
        job_id: str,
        since: int = 0,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """The coordinator's event ledger for a job the caller can see.

        This is the read side of everything the console shows about *how* a
        job ran: which node claimed which task, which lease expired, which
        commit was accepted, what the recovery policy decided and why. The
        coordinator persists it (``events`` table, ordered by ``seq``), so it
        is real history rather than a live tail, and it survives a restart.

        ``since`` is an offset into that append-only list, which is what a
        poller wants: pass back the count you already have and get only what
        arrived after. It is an offset and not a timestamp deliberately —
        several events share a timestamp to the millisecond (a sweep expires
        a lease and requeues the task in one pass), so a time cursor either
        replays them or drops them.

        Visibility is checked against the ``jobs`` table before the
        coordinator is contacted, answering 404 for a job that exists and
        the caller cannot see, exactly as the sibling read routes do. The
        owner, or any member of the job's pool, may read it.
        """
        row = dbmod.fetch_job_for_viewer(db, job_id, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown job")

        if fedavgmod.is_federated_job_id(job_id):
            # A federated run is one coordinator job PER ROUND, so there is
            # no single ledger to forward to. Fan out over the rounds that
            # have a coordinator job and tag each event with the round it
            # came from, which is the only way the client can order them.
            # Rounds without a coordinator_job_id are rounds that never
            # reached the coordinator; they contribute nothing rather than
            # an empty group.
            #
            # `row["owner_id"]`, not `user_id`: same reason as the rounds
            # route — the listing query is owner-scoped and a viewing pool
            # member is not that owner.
            rounds = dbmod.list_job_rounds_for_owner(db, job_id, row["owner_id"])
            pairs = [
                (row["round"], row["coordinator_job_id"])
                for row in rounds
                if row.get("coordinator_job_id")
            ]
            if not pairs:
                return []
            responses = await asyncio.gather(
                *(
                    coordinator.forward("GET", f"/v1alpha1/jobs/{_seg(cid)}/events")
                    for _, cid in pairs
                ),
                return_exceptions=True,
            )
            merged: list[dict[str, Any]] = []
            for (round_no, _), r in zip(pairs, responses):
                # One unreachable round must not take the whole ledger down.
                # A partial history is useful; a 500 is not.
                if isinstance(r, BaseException) or r.status_code >= 300:
                    continue
                try:
                    events = r.json()
                except ValueError:
                    continue
                if not isinstance(events, list):
                    continue
                for e in events:
                    if isinstance(e, dict):
                        merged.append({**e, "round": round_no})
            # Round first, then the coordinator's own order within a round.
            # Sorting by timestamp alone would interleave rounds that ran
            # back to back.
            merged.sort(key=lambda e: e.get("round") or 0)
            return merged[max(since, 0):]

        r = await coordinator.forward("GET", f"/v1alpha1/jobs/{_seg(job_id)}/events")
        if r.status_code >= 300:
            return _passthrough(r)
        try:
            events = r.json()
        except ValueError:
            return _passthrough(r)
        if not isinstance(events, list):
            return _passthrough(r)
        return events[max(since, 0):]

    @app.get("/v1alpha1/jobs/{job_id}/tasks", tags=["browser"])
    async def get_job_tasks(
        job_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Current task state for a job the caller can see.

        Deliberately *current* state only, which is what the coordinator
        exposes: task id, state, attempts used against the cap, the node
        holding it now (or the last node to hold it), and the live lease
        deadline. Attempt HISTORY is not here and cannot be, because the
        coordinator's task view does not carry it — the console derives
        per-attempt history from the event ledger above instead.

        A federated run has one coordinator job per round, so tasks are
        returned per round with a ``round`` key rather than flattened: task
        ids repeat across rounds and merging them would silently collapse
        distinct work into one row.
        """
        row = dbmod.fetch_job_for_viewer(db, job_id, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown job")

        if fedavgmod.is_federated_job_id(job_id):
            # `row["owner_id"]`, not `user_id` — same reason as the rounds
            # and events routes.
            rounds = dbmod.list_job_rounds_for_owner(db, job_id, row["owner_id"])
            pairs = [
                (r["round"], r["coordinator_job_id"])
                for r in rounds
                if r.get("coordinator_job_id")
            ]
            if not pairs:
                return []
            responses = await asyncio.gather(
                *(
                    coordinator.forward("GET", f"/v1alpha1/jobs/{_seg(cid)}/tasks")
                    for _, cid in pairs
                ),
                return_exceptions=True,
            )
            merged: list[dict[str, Any]] = []
            for (round_no, _), r in zip(pairs, responses):
                if isinstance(r, BaseException) or r.status_code >= 300:
                    continue
                try:
                    tasks = r.json()
                except ValueError:
                    continue
                if not isinstance(tasks, list):
                    continue
                for t in tasks:
                    if isinstance(t, dict):
                        merged.append({**t, "round": round_no})
            return merged

        r = await coordinator.forward("GET", f"/v1alpha1/jobs/{_seg(job_id)}/tasks")
        return _passthrough(r)

    @app.get("/v1alpha1/me/storage", tags=["browser"])
    async def get_my_storage(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """What this account is using, and against what ceiling.

        A quota nobody can see is a quota that surprises people at exactly
        the wrong moment. `limit_bytes` is null for an unlimited account
        and `percent_used` is null with it — the console has to be able to
        tell "no limit" from "0% of a limit", which look identical if
        either is coerced to a number.
        """
        used = dbmod.storage_usage_for_owner(db, user_id)
        limit = storagemod.limit_for(
            override=dbmod.storage_limit_override_for(db, user_id),
            default=storagemod.deployment_default(),
        )
        return {
            "used_bytes": used,
            "limit_bytes": limit,
            "percent_used": storagemod.percent_used(used, limit),
        }

    @app.get("/v1alpha1/me/metrics", tags=["browser"])
    async def get_my_metrics(
        window_days: int = Query(
            default=metricsmod.DEFAULT_WINDOW_DAYS,
            ge=1,
            le=metricsmod.MAX_WINDOW_DAYS,
        ),
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """This account's reliability report, computed from the ledger.

        Owner-scoped from the verified JWT ``sub``, exactly like
        ``/v1alpha1/me/storage`` beside it: there is no id in the path and
        no account in the body, so there is nothing to scope wrongly.

        The coordinator is not contacted, and cannot be. Every number here
        is a count over ``jobs``, ``attempts`` and ``job_rounds``, which is
        what makes a page covering a month of work one query rather than one
        HTTP call per job — hard rule 3, durable state lives in Postgres.

        ``window_days`` is bounded rather than clamped: a request for 0 days
        or 10 000 is a caller bug, and silently answering a different
        question than the one asked produces a page whose label and contents
        disagree. 422 says which.

        **Three of the fields are always null**, and that is the most
        important thing about this route. ``lost_task_seconds``,
        ``mttd_seconds`` and ``mttr_seconds`` need events nothing in this
        deployment records; ``metrics.report`` documents exactly which event
        each one is waiting for. They are null rather than 0 because this
        page's entire purpose is to prove a claim about reliability, and a
        fabricated MTTR is indistinguishable from a measured one.
        """
        return metricsmod.report(
            window_days=window_days,
            counts=dbmod.metrics_counts_for_owner(db, user_id, window_days),
        )

    @app.get("/v1alpha1/me/contributions", tags=["browser"])
    async def get_my_contributions(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """What this account has contributed, across everything it worked on.

        Owner-scoped from the verified JWT ``sub``, exactly like
        ``/v1alpha1/me/storage`` and ``/v1alpha1/me/metrics`` beside it: no id
        in the path, no account in the body, nothing to scope wrongly.

        **This is the barter side of the product, and it was invisible.**
        ``public.contributions`` has recorded every machine's accepted work
        since the first migration, but credit surfaced only inside one job's
        panel — "which machine did the work on THIS job" — so somebody who had
        donated forty hours across five jobs had nowhere to learn it.

        Scoped on the MACHINE's owner, not the job's, which is the opposite of
        the metrics page next door and deliberately so: those are the jobs you
        submitted, these are the jobs you helped run, and they are mostly
        other people's. Both are ``/me`` and they answer different questions.

        **A counter, not a currency.** Nothing debits these numbers — there is
        no spend path anywhere in this system — so the body counts tasks and
        jobs and never names a balance. Any copy or field that implies a
        drawdown promises an exchange rate this product has not designed.

        The coordinator is not contacted, and must not be: a person's whole
        contribution history is two indexed queries here, against one HTTP
        call per job it would otherwise take.
        """
        measured = dbmod.contributions_for_owner(db, user_id)
        return contribmod.report(
            machines=measured["machines"],
            jobs_contributed_to=measured["jobs_contributed_to"],
        )

    @app.get("/v1alpha1/jobs/{job_id}/result", tags=["browser"])
    async def get_job_result(
        job_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """The job-level answer: the declared reducer run over accepted work.

        This is what the whole job exists to produce — the winning trial of
        a sweep, the mean across folds, the concatenated shards of a batch
        run. Without it the console can show every task's output and still
        not answer the question the submitter actually asked.

        Answers while the job is still running, because a sweep's leader is
        useful early; the coordinator reports ``accepted``/``total`` beside
        the result so a partial answer always says it is partial.

        A federated job has no Mode A reduction: its aggregation IS the
        round loop, and the driver already performed it. Saying so is
        better than reducing one arbitrary round's tasks and presenting
        that as the job's answer.
        """
        row = dbmod.fetch_job_for_viewer(db, job_id, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown job")

        if fedavgmod.is_federated_job_id(job_id):
            raise HTTPException(
                status_code=409,
                detail="a federated job's aggregation is its round history, "
                       "not a Mode A reduction — see this job's rounds",
            )

        r = await coordinator.forward("GET", f"/v1alpha1/jobs/{_seg(job_id)}/result")
        return _passthrough(r)

    @app.get("/v1alpha1/jobs/{job_id}/contributions", tags=["browser"])
    async def get_job_contributions(
        job_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """The per-machine credit view for a job the caller can see.

        Visibility is checked exactly as the sibling read routes do it — the
        owner, or any member of the job's pool, may see who did the work —
        answering 404 (not 403) for a job that exists and the caller cannot
        see. ``list_job_contributions`` itself takes no viewer argument by
        design (Task 9): it trusts its caller to have authorized first, so
        this check must run before it, never after or not at all.
        """
        if dbmod.fetch_job_for_viewer(db, job_id, user_id) is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return [_jsonable(r) for r in dbmod.list_job_contributions(db, job_id)]

    @app.post("/v1alpha1/jobs/{job_id}/cancel", tags=["browser"])
    async def cancel_job_route(
        job_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        if dbmod.fetch_job_for_owner(db, job_id, user_id) is None:
            # Ownership is checked *before* the coordinator is ever
            # contacted: cancelling a job you don't own must not reach the
            # coordinator at all, let alone actually cancel it.
            raise HTTPException(status_code=404, detail="unknown job")
        if fedavgmod.is_federated_job_id(job_id):
            # Say so, rather than forwarding an id the coordinator has never
            # seen and answering 404 for a job the caller does own. The
            # driver is an in-process loop with no cancellation channel;
            # stopping one mid-round is cooperative cancel, which the design
            # spec (§6.4) puts in M3 alongside result verification.
            raise HTTPException(
                status_code=501,
                detail="cancelling a federated run is not implemented yet; "
                       "the current round finishes and the run stops when the "
                       "API process does",
            )
        r = await coordinator.forward("POST", f"/v1alpha1/jobs/{_seg(job_id)}/cancel")
        return _passthrough(r)

    @app.get("/v1alpha1/jobs/{job_id}/artifacts/{key:path}", tags=["browser"])
    async def get_job_artifact(
        job_id: str,
        key: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """The one deliberate residual the Task 5 report flagged: agent
        artifact reads stay open at the coordinator (an agent legitimately
        reads inputs for the task it holds), but a *browser* must only be
        able to read artifacts under a job it can see. Visibility is checked
        here, against this table, before the key is ever forwarded — same
        404-not-403 rule as the rest of this block. This is a read, so it
        uses ``fetch_job_for_viewer`` like its siblings: the owner, or any
        member of the job's pool, may fetch a job's artifacts.
        """
        if dbmod.fetch_job_for_viewer(db, job_id, user_id) is None:
            raise HTTPException(status_code=404, detail="unknown job")
        full_key = _artifact_key(f"{_seg(job_id)}/{key}")
        coordinator_key = f"jobs/{full_key}"
        r = await coordinator.forward("GET", f"/v1alpha1/artifacts/{coordinator_key}")
        return _passthrough(r)

    async def _require_stopped(job_id: str, db: psycopg.Connection) -> None:
        """Refuse unless the coordinator says this job has stopped writing.

        THE COORDINATOR, NOT ``jobs.status``. That column is a cache written
        only when somebody looks (``sync_observed_job_states``), so a job
        that finished last week and whose page nobody opened still reads
        ``RUNNING`` locally. Trusting it would refuse to free exactly the
        forgotten jobs this route exists to free — the deadlock, rebuilt
        one layer down. One extra round trip is affordable here in a way it
        is not on the two-second poll: this is a deliberate, rare,
        irreversible action, not a page refresh.

        404 from the coordinator is a pass, not a failure. It means the
        coordinator has no such job, so nothing can be writing to it, and
        the delete below will answer 404 too — which is how deleting twice
        stays a no-op instead of an error.

        Every other non-2xx, and any unparseable body, is a 502: "I could
        not find out whether this job has stopped" is not "it has stopped",
        and the cost of being wrong is asymmetric. Refusing costs a retry;
        proceeding deletes files out from under a task that has not
        committed yet and produces a job that fails for a reason nobody can
        reconstruct afterwards.
        """
        r = await coordinator.forward("GET", f"/v1alpha1/jobs/{_seg(job_id)}")
        if r.status_code == 404:
            return
        if r.status_code >= 300:
            raise HTTPException(
                status_code=502,
                detail="could not confirm this job has finished, so nothing "
                       "was deleted — try again",
            )
        try:
            job = r.json()
        except ValueError:
            job = None
        if not isinstance(job, dict):
            raise HTTPException(
                status_code=502,
                detail="could not confirm this job has finished, so nothing "
                       "was deleted — try again",
            )
        state = job.get("state")
        if not is_terminal_state(state):
            raise HTTPException(
                status_code=409,
                detail="this job is still running, and deleting the files a "
                       "task is writing would fail it for a reason nobody "
                       "could reconstruct — cancel it first, then delete",
            )
        # Free: the state is already in hand and this is the same fact the
        # poll-driven hook writes. `GET /me/metrics` counts outcomes out of
        # this column, so recording it here as well means a job whose only
        # visit was its deletion still counts.
        dbmod.sync_observed_job_states(db, [(job_id, str(state))])

    @app.delete("/v1alpha1/jobs/{job_id}/artifacts", tags=["browser"])
    async def delete_job_artifacts(
        job_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Free a finished job's artifacts and correct the recorded usage.

        THE RELEASE VALVE. The storage budget (0010) refuses a submission
        with "delete a finished job's artifacts to free space", and until
        this route there was no way to delete either. An account that
        reached its limit could not submit again until an operator ran SQL,
        which makes the quota worse than no quota: it fails permanently
        closed, and the failure lands on the person least able to fix it.

        **OWNER ONLY, not viewer.** ``fetch_job_for_owner``, deliberately,
        where every read route beside it uses ``fetch_job_for_viewer``.
        Seeing is wider than owning — a pool member can read a teammate's
        job, which is what a workspace is for — but this is irreversible
        destruction of somebody else's outputs, and the bytes are charged
        to the OWNER's budget, so a member deleting them would be silently
        editing another account's usage row. ``cancel`` drew the same line
        for the same reason: everyone in the workspace may watch, only the
        owner may act. 404 rather than 403, like every other job route
        here, so a caller still cannot learn which ids are real — including
        the pool member, who gets the stranger's answer.

        **REFUSED FOR A JOB THAT HAS NOT STOPPED** — see ``_require_stopped``
        for what "stopped" means and why the coordinator, not the local
        status column, is asked.

        **A FEDERATED RUN IS N COORDINATOR JOBS**, one per round, under a
        parent id the coordinator has never heard of. Forwarding the parent
        id would delete nothing and report success; deleting only the last
        round would free one round out of twenty while recording the whole
        run as empty. So every round's coordinator job is deleted, and the
        run's terminal state comes from the parent row, which is the only
        statement about it that exists (the driver writes it; there is no
        coordinator job to ask).

        **PARTIAL FAILURE: over-report, never under-report.** If some
        deletes succeed and one fails, this route records NOTHING and
        answers 502. The recorded usage then over-states what is on the
        disk — the owner is charged for bytes that are already gone, and
        can retry. The alternative, crediting the bytes we did manage to
        free, sets a number nobody measured and is wrong in the direction
        that lets an account keep writing to a disk every workspace shares,
        which is the outage the budget exists to prevent. It is also the
        judgement ``fedavg.record_run_footprint`` already made about a
        partial read, for the same reason, and the two must not disagree.
        The retry is what corrects the number: a round already deleted
        answers 404 (not an error), so only the round that failed has to
        succeed once.

        **IDEMPOTENT.** Deleting twice answers 200 with zeros. Browsers
        double-submit, people re-click, and an error on the second click
        would send someone hunting for a problem that does not exist.
        """
        row = dbmod.fetch_job_for_owner(db, job_id, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown job")

        if fedavgmod.is_federated_job_id(job_id):
            if not is_terminal_state(row.get("status")):
                # The parent row is the only account of a federated run's
                # state. A driver mid-round is writing weights this would
                # delete under it, and unlike Mode A there is no second
                # opinion to ask — so an in-flight run is refused on the
                # local status, and a run whose driver died with the API
                # process stays refused. That is the fail-closed direction:
                # the cost is an operator UPDATE on one row, against
                # deleting the checkpoints of a run that is still going.
                raise HTTPException(
                    status_code=409,
                    detail="this federated run has not finished, and deleting "
                           "a round's weights while the driver is still "
                           "averaging would corrupt the run",
                )
            targets = [cid for _round, cid in dbmod.list_round_job_ids(db, job_id)]
        else:
            await _require_stopped(job_id, db)
            targets = [job_id]

        deleted_files = 0
        freed_bytes = 0
        for coordinator_job_id in targets:
            r = await coordinator.forward(
                "DELETE", f"/v1alpha1/jobs/{_seg(coordinator_job_id)}/artifacts"
            )
            if r.status_code == 404:
                # Nothing there to delete. The contract's own words: not an
                # error. This is the second click, and the round that was
                # already freed by the retry's predecessor.
                continue
            if r.status_code >= 300:
                # Nothing recorded — see the docstring on which way to be
                # wrong. Whatever earlier targets freed really is freed and
                # this account is still charged for it until a retry gets
                # all the way through.
                raise HTTPException(
                    status_code=502,
                    detail="some of this job's artifacts could not be deleted, "
                           "so its recorded usage is unchanged — try again",
                )
            try:
                payload = r.json()
            except ValueError:
                payload = None
            files, bytes_ = storagemod.deletion_counts(payload)
            deleted_files += files
            freed_bytes += bytes_

        # THE MEASUREMENT, not a decrement. `record_job_artifact_bytes` SETS
        # (its docstring says why), and what it is set to here is 0 because
        # every artifact of this job is what was just deleted — a fact, not
        # `old - freed_bytes`, which would trust the coordinator's arithmetic
        # and would go negative for a job whose footprint was never measured
        # in the first place (`artifact_bytes` defaults to 0).
        #
        # It also STAMPS `artifact_bytes_recorded_at`, in the same statement,
        # and that stamp is the half that stops the freed bytes coming back:
        # the Mode A recording hook re-lists exactly the jobs whose marker is
        # null, so a job deleted before anyone ever opened its page would
        # otherwise be re-measured on the next poll. Reached only when every
        # target answered — see the loop above.
        dbmod.record_job_artifact_bytes(db, job_id, 0)
        return {"deleted_files": deleted_files, "freed_bytes": freed_bytes}

    # -- agent-facing: machine token, forwarded with delegation ------------
    #
    # Every route below is tagged "agent", and test_agent_proxy enumerates
    # that tag to assert each one 401s without a machine token and never
    # reaches the coordinator. A route added here without the
    # ``current_machine`` dependency fails that test rather than quietly
    # becoming an open door.

    @app.post("/v1alpha1/nodes/register", tags=["agent"])
    async def register_node(
        request: Request, machine: Machine = Depends(current_machine)
    ):
        # Resolved BEFORE proxying and stamped onto the body — never trust
        # what the agent claims about its own pool membership. Machine-
        # scoped, not owner-scoped: this machine's own bindings (narrowed by
        # its owner's live memberships), not everything its owner belongs to
        # — see `pool_ids_for_machine`'s docstring for why a stale binding
        # must be inert.
        body = await request.body()
        try:
            with contextlib.closing(app.state.connect()) as conn:
                pools = dbmod.pool_ids_for_machine(conn, machine.id)
                # Display-only capability snapshot from the agent's own
                # self-description, best-effort — its OWN try, same
                # contract as `touch_machine_last_seen` in the heartbeat
                # handler below: a write to a column nothing ever reads for
                # authorization must never fail the registration itself,
                # and must never zero the (unrelated) pools stamp above —
                # which is why this sits INSIDE the pools-lookup try rather
                # than wrapping it.
                try:
                    # Gated on size, not just non-empty. `proxy()` below is
                    # the real 413 authority — it re-checks the same
                    # constant and rejects the request — but that check
                    # runs AFTER this block, once `body` is already fully
                    # buffered in this process. Without this guard a
                    # machine-token holder could force a multi-hundred-MB
                    # `json.loads` AND a DB write on every request, merely
                    # by padding a body that is about to be rejected as too
                    # large anyway. `body.strip()`/`len()` are cheap
                    # byte-level scans; `json.loads` and the write are not.
                    if len(body) <= MAX_JSON_BODY_BYTES:
                        parsed = json.loads(body) if body.strip() else {}
                        dbmod.set_machine_capabilities(
                            conn, machine_id=machine.id,
                            sandbox_capable=parsed.get("sandbox_capable") is True,
                            argv_capable=parsed.get("argv_capable") is True,
                            unsandboxed_argv_capable=(
                                parsed.get("unsandboxed_argv_capable") is True
                            ),
                            module_capable=parsed.get("module_capable") is True,
                        )
                except Exception:
                    log.warning(
                        "could not persist capability snapshot for %s", machine.id
                    )
        except Exception:
            # Fail CLOSED: a node we cannot vouch for serves no pool this
            # cycle. Never skip the stamp — skipping would forward whatever
            # the agent claimed.
            log.warning("could not resolve pools for machine %s", machine.id)
            pools = []

        return await proxy(
            request, machine, "/v1alpha1/nodes/register", force_node_id=True,
            pools=pools, pools_where="capabilities",
        )

    @app.post("/v1alpha1/nodes/{node_id}/heartbeat", tags=["agent"])
    async def node_heartbeat(
        node_id: str, request: Request, machine: Machine = Depends(current_machine)
    ):
        # The URL segment is replaced, not validated-and-rejected, for the
        # same reason the body field is: a machine may only ever heartbeat
        # itself, so the only node_id that could be correct is the token's.
        # (A heartbeat for somebody else would falsify the pool view by
        # keeping a dead node marked online.)
        #
        # Record liveness for the CONSOLE here, before forwarding. The
        # coordinator keeps its own liveness view for scheduling, and the two
        # are separate on purpose — but only the coordinator's was ever
        # written, so `machines.last_seen_at` stayed null and every machine
        # rendered "Offline / Last seen never" no matter how healthy it was.
        #
        # The pool membership refresh rides the same connection open, but
        # its own try/except, separate from `touch_machine_last_seen`'s: a
        # display-column failure is best-effort and must never fail the
        # pools stamp CLOSED — only a genuine membership-lookup failure
        # does that, below.
        try:
            with contextlib.closing(app.state.connect()) as conn:
                try:
                    dbmod.touch_machine_last_seen(conn, machine.id)
                except Exception:
                    log.warning(
                        "could not record last_seen_at for machine %s", machine.id
                    )
                pools = dbmod.pool_ids_for_machine(conn, machine.id)
        except Exception:
            # Fail CLOSED: a node we cannot vouch for serves no pool this
            # cycle. Never skip the stamp — skipping would forward whatever
            # the agent claimed.
            log.warning("could not resolve pools for machine %s", machine.id)
            pools = []

        return await proxy(
            request,
            machine,
            f"/v1alpha1/nodes/{machine.node_id}/heartbeat",
            force_node_id=True,
            pools=pools,
            pools_where="top",
        )

    @app.post("/v1alpha1/leases/claim", tags=["agent"])
    async def claim(request: Request, machine: Machine = Depends(current_machine)):
        response = await proxy(
            request, machine, "/v1alpha1/leases/claim", force_node_id=True
        )
        # Remember what this machine was handed. The completion hop reports
        # only `{"accepted": bool}` against a lease id, so THIS is the single
        # point at which the API can learn which job and task a lease covers
        # — and without that mapping no non-federated job can credit anybody.
        # 204 ("nothing claimable right now") carries no lease and is skipped.
        #
        # Best-effort, exactly like last_seen_at above: an accounting row must
        # never be the reason a machine fails to pick up work.
        if response.status_code == 200:
            try:
                lease = json.loads(response.body)
                with contextlib.closing(app.state.connect()) as conn:
                    dbmod.record_attempt(
                        conn,
                        lease_id=lease["lease_id"],
                        machine_id=machine.id,
                        job_id=lease["job_id"],
                        task_id=lease["task_id"],
                    )
            except Exception:
                log.warning("could not record attempt for machine %s", machine.id)
        return response

    @app.post("/v1alpha1/attempts/{lease_id}/heartbeat", tags=["agent"])
    async def attempt_heartbeat(
        lease_id: str, request: Request, machine: Machine = Depends(current_machine)
    ):
        return await proxy(
            request, machine, f"/v1alpha1/attempts/{_seg(lease_id)}/heartbeat"
        )

    @app.post("/v1alpha1/attempts/{lease_id}/complete", tags=["agent"])
    async def attempt_complete(
        lease_id: str, request: Request, machine: Machine = Depends(current_machine)
    ):
        response = await proxy(
            request, machine, f"/v1alpha1/attempts/{_seg(lease_id)}/complete"
        )
        # ACCEPTANCE IS THE BODY FIELD, NEVER THE STATUS CODE.
        #
        # The coordinator answers 200 with `{"accepted": false}` in two
        # ordinary cases: the output's sha256 did not match what was
        # committed (the attempt is requeued elsewhere), and the commit
        # arrived after another attempt had already won the task. Both are
        # successful HTTP hops reporting unsuccessful WORK. Crediting on
        # `2xx` would pay for a failed hash check and pay twice for a task
        # two machines both finished — hard rule 4, attempted work is not
        # accepted work.
        if response.status_code == 200:
            try:
                accepted = json.loads(response.body).get("accepted") is True
            except Exception:
                accepted = False
            if accepted:
                try:
                    with contextlib.closing(app.state.connect()) as conn:
                        # Takes the right to credit this lease exactly once,
                        # and only for the machine that CLAIMED it. None is
                        # the ordinary answer for a repeated commit or a
                        # lease this machine never held — not an error.
                        credit = dbmod.claim_attempt_credit(
                            conn, lease_id=lease_id, machine_id=machine.id
                        )
                        if credit is not None:
                            dbmod.record_contributions(
                                conn,
                                job_id=credit["job_id"],
                                entries=[{
                                    "node_id": machine.node_id,
                                    "task_id": credit["task_id"],
                                    "duration_s": credit["duration_s"],
                                }],
                            )
                            # VERIFICATION, SLICE 1 — advisory, and it stays
                            # advisory. The verdict is written to a row and
                            # that is the whole of its effect: the credit
                            # above has already been written and is never
                            # taken back, the response below is the
                            # coordinator's unaltered, and no lease, no
                            # placement and no payout consults this. A
                            # verifier that can refuse work takes the fleet
                            # down on a false positive, and the API cannot
                            # tell "cheated" from "the input was cached and
                            # the machine is fast".
                            #
                            # Its own try/except rather than leaning on the
                            # outer one, for two reasons: an exception here
                            # would otherwise be logged as a failure to
                            # CREDIT, which by this point is a lie; and
                            # nesting makes it structurally impossible for
                            # this block to skip anything the agent depends
                            # on. Best-effort, exactly like
                            # `touch_machine_last_seen` above.
                            #
                            # Ordering matters and is deliberate: this runs
                            # AFTER `record_contributions`, and the peer
                            # query excludes this machine — so the row just
                            # written cannot become part of the baseline this
                            # machine is measured against.
                            try:
                                peers = dbmod.peer_task_durations(
                                    conn,
                                    job_id=credit["job_id"],
                                    machine_id=machine.id,
                                )
                                verdict, detail = verifymod.timing_verdict(
                                    peers, credit["duration_s"]
                                )
                                dbmod.record_verification(
                                    conn,
                                    machine_id=machine.id,
                                    job_id=credit["job_id"],
                                    task_id=credit["task_id"],
                                    slice_name="timing",
                                    verdict=verdict,
                                    detail=detail,
                                )
                            except Exception:
                                log.warning(
                                    "could not record a timing verdict for "
                                    "machine %s; the credit above stands",
                                    machine.id,
                                )
                except Exception:
                    log.warning(
                        "could not credit accepted attempt for machine %s",
                        machine.id,
                    )
        return response

    @app.post("/v1alpha1/attempts/{lease_id}/fail", tags=["agent"])
    async def attempt_fail(
        lease_id: str, request: Request, machine: Machine = Depends(current_machine)
    ):
        return await proxy(
            request, machine, f"/v1alpha1/attempts/{_seg(lease_id)}/fail"
        )

    @app.put("/v1alpha1/artifacts/{key:path}", tags=["agent"])
    async def put_artifact(
        key: str, request: Request, machine: Machine = Depends(current_machine)
    ):
        # The coordinator confines this key to the caller's live leases —
        # against the *delegated* identity, which is why forwarding the
        # header correctly is the whole security property here.
        return await proxy(request, machine,
                           f"/v1alpha1/artifacts/{_artifact_key(key)}")

    @app.get("/v1alpha1/artifacts/{key:path}", tags=["agent"])
    async def get_artifact(
        key: str, request: Request, machine: Machine = Depends(current_machine)
    ):
        # Reads stay open at the coordinator, but not open *here*: this is
        # the public door, so an anonymous internet caller must not be able
        # to enumerate artifacts through it. Per-job read scoping is Task 6.
        return await proxy(request, machine,
                           f"/v1alpha1/artifacts/{_artifact_key(key)}")

    @app.post("/v1alpha1/jobs/{job_id}/tasks/{task_id}/checkpoints/parts",
              tags=["agent"])
    async def checkpoint_part(
        job_id: str, task_id: str, request: Request,
        machine: Machine = Depends(current_machine),
    ):
        return await proxy(
            request, machine,
            f"/v1alpha1/jobs/{_seg(job_id)}/tasks/{_seg(task_id)}/checkpoints/parts",
        )

    @app.post("/v1alpha1/jobs/{job_id}/tasks/{task_id}/checkpoints/commit",
              tags=["agent"])
    async def checkpoint_commit(
        job_id: str, task_id: str, request: Request,
        machine: Machine = Depends(current_machine),
    ):
        return await proxy(
            request, machine,
            f"/v1alpha1/jobs/{_seg(job_id)}/tasks/{_seg(task_id)}/checkpoints/commit",
        )

    @app.get("/v1alpha1/jobs/{job_id}/tasks/{task_id}/checkpoints/latest",
             tags=["agent"])
    async def checkpoint_latest(
        job_id: str, task_id: str, request: Request,
        machine: Machine = Depends(current_machine),
    ):
        return await proxy(
            request, machine,
            f"/v1alpha1/jobs/{_seg(job_id)}/tasks/{_seg(task_id)}/checkpoints/latest",
        )

    @app.get("/v1alpha1/jobs/{job_id}/tasks/{task_id}/checkpoints/lost-work",
             tags=["agent"])
    async def checkpoint_lost_work(
        job_id: str, task_id: str, request: Request,
        machine: Machine = Depends(current_machine),
    ):
        return await proxy(
            request, machine,
            f"/v1alpha1/jobs/{_seg(job_id)}/tasks/{_seg(task_id)}/checkpoints/lost-work",
        )

    return app


# ---------------------------------------------------------------------------
# the legacy (pre-accounts) app
# ---------------------------------------------------------------------------


def _create_legacy_app() -> FastAPI:
    runtime_api = os.environ.get("FLASHML_RUNTIME_API", "http://localhost:8100").rstrip("/")
    db_path = os.environ.get("FLASHML_CLOUD_DB", "flashml-cloud.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    store = NodeStore(
        db_path,
        offline_after_seconds=float(os.environ.get("FLASHML_NODE_OFFLINE_SECONDS", "30")),
    )

    app = FastAPI(title="FlashML Cloud API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("FLASHML_CORS_ORIGINS", "*").split(","),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.store = store

    async def _proxy(method: str, path: str, body: dict | None = None) -> Response:
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                r = await client.request(method, f"{runtime_api}{path}", json=body)
            except httpx.ConnectError as exc:
                raise HTTPException(status_code=502,
                                    detail=f"FlashRuntime unreachable: {exc}")
        return Response(content=r.content, status_code=r.status_code,
                        media_type="application/json")

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    # -- nodes -------------------------------------------------------------

    @app.post("/v1alpha1/nodes/register", status_code=201)
    async def register_node(registration: NodeRegistration):
        store.register(registration)
        log.info(json.dumps({"text": "node registered",
                             "node_id": registration.node_id,
                             "k8s_node": registration.kubernetes_node}))
        return {"node_id": registration.node_id, "status": "registered"}

    @app.post("/v1alpha1/nodes/{node_id}/heartbeat")
    async def node_heartbeat(node_id: str, hb: NodeHeartbeat):
        if hb.node_id != node_id:
            raise HTTPException(status_code=422, detail="node_id mismatch")
        if not store.heartbeat(hb):
            raise HTTPException(status_code=404,
                                detail=f"unregistered node: {node_id}")
        return {"status": "ok"}

    @app.get("/v1alpha1/nodes")
    async def list_nodes():
        return store.list_nodes()

    # -- jobs (delegated to FlashRuntime) ----------------------------------

    @app.post("/v1alpha1/jobs", status_code=201)
    async def submit_job(spec: JobSpec):
        return await _proxy("POST", "/v1alpha1/jobs",
                            body=json.loads(spec.model_dump_json()))

    @app.get("/v1alpha1/jobs")
    async def list_jobs():
        return await _proxy("GET", "/v1alpha1/jobs")

    @app.get("/v1alpha1/jobs/{job_id}")
    async def get_job(job_id: str):
        return await _proxy("GET", f"/v1alpha1/jobs/{job_id}")

    @app.get("/v1alpha1/jobs/{job_id}/events")
    async def get_events(job_id: str):
        return await _proxy("GET", f"/v1alpha1/jobs/{job_id}/events")

    @app.get("/v1alpha1/jobs/{job_id}/logs")
    async def get_logs(job_id: str):
        return await _proxy("GET", f"/v1alpha1/jobs/{job_id}/logs")

    @app.post("/v1alpha1/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        return await _proxy("POST", f"/v1alpha1/jobs/{job_id}/cancel")

    # -- deployment / Alibaba integration panel ----------------------------

    @app.get("/v1alpha1/integration")
    async def integration_status():
        env = os.environ
        profile = env.get("FLASHML_PROFILE", "local")
        return {
            "profile": profile,
            "backend": "ray/kuberay",
            "environment": "Alibaba ACK" if profile == "alibaba-ack" else "Local Kind",
            "artifact_store": env.get("FLASHML_ARTIFACT_BACKEND", "minio"),
            "image_registry": env.get("FLASHML_ACR_IMAGE", "local"),
            "ack_connected": profile == "alibaba-ack",
            "oss_bucket": env.get("FLASHML_OSS_BUCKET", ""),
            "sls_enabled": env.get("FLASHML_SLS_ENABLED", "false") == "true",
            "prometheus_enabled": env.get("FLASHML_PROMETHEUS_ENABLED", "false") == "true",
            "sandbox_pool_available": env.get("FLASHML_SANDBOX_POOL", "") != "",
            "paidlc_adapter": "not implemented",
        }

    return app


def create_app() -> FastAPI:
    """The cloud app when the environment describes a real deployment, the
    legacy app otherwise. Never a mixture: a half-authenticated API is the
    worst of both, because the open half is still a public door."""
    if os.environ.get("SUPABASE_URL") and os.environ.get("COORDINATOR_URL"):
        settings = Settings.from_env()

        def connect() -> psycopg.Connection:
            conn = psycopg.connect(settings.database_url, row_factory=dict_row)
            conn.autocommit = True
            return conn

        return create_cloud_app(settings, connect=connect)
    return _create_legacy_app()


app = create_app()
