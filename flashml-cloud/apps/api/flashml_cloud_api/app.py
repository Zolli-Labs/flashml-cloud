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
                           FLASHML_SLS_ENABLED, FLASHML_PROMETHEUS_ENABLED,
                           FLASHML_SANDBOX_POOL

  FLASHML_ARTIFACT_BACKEND and FLASHML_OSS_BUCKET were listed here and are
  gone: no code in this repo or in the pinned runtime's managed path reads
  either, and the integration panel that displayed them was reporting a
  storage arrangement anybody could fabricate by exporting a variable. The
  panel now reports where the coordinator actually writes and what
  ``settings`` (``OSS_BUCKET``, not ``FLASHML_OSS_BUCKET``) says about the
  mirror.

Environment (cloud): see ``settings.Settings.from_env``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import secrets
import time
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

import tempfile
import uuid

import httpx
import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
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
from flashml_cloud_api import datasets as dsmod
from flashml_cloud_api import db as dbmod
from flashml_cloud_api import enrolment
from flashml_cloud_api import fedavg as fedavgmod
from flashml_cloud_api import metrics as metricsmod
from flashml_cloud_api import marketplace as marketplacemod
from flashml_cloud_api import repo as repomod
from flashml_cloud_api import placement as placementmod
from flashml_cloud_api import prices as pricesmod
from flashml_cloud_api import router as routermod
from flashml_cloud_api import sandbox_orchestrator as orchmod
from flashml_cloud_api import sandbox_sessions as ssmod
from flashml_cloud_api import storage as storagemod
from flashml_cloud_api import verify as verifymod
from flashml_cloud_api.alibaba_oss import OSSArtifacts, OSSUnavailable
from flashml_cloud_api.alibaba_sandbox import (
    E2BSandboxGateway,
    SandboxGateway,
    SandboxUnavailable,
)
from flashml_cloud_api.artifact_mirror import (
    CoordinatorArtifactSource,
    MirrorError,
    job_prefix,
    mirror_job,
    presign_mirrored_artifact,
    unmirror_job,
)
from flashml_cloud_api.auth import (
    MACHINE_TOKEN_PREFIX,
    AuthError,
    hash_invite_token,
    looks_like_user_token,
    new_invite_token,
    verify_supabase_jwt,
)
from flashml_cloud_api.capacity import reconcile as capacitymod
from flashml_cloud_api.capacity import registry as capacityregistrymod
from flashml_cloud_api.capacity import settle as capacitysettlemod
from flashml_cloud_api.capacity.provider import ResourceProvider
from flashml_cloud_api.compile import (
    DATASET_SLICES_PARAM,
    CompileError,
    compile_federated_round,
    compile_to_jobspec,
    sanitize_job_name,
)
from flashml_cloud_api.db import Machine
from flashml_cloud_api.elastic import (
    FleetShape,
    cap_chunks_to_manifest,
    effective_width,
    fleet_shape,
)
from flashml_cloud_api.emails import derive_email_facts
from flashml_cloud_api.flashml_yaml import (
    SPLIT_SHARD,
    ConfigError,
    parse_flashml_yaml,
)
from flashml_cloud_api.images import UnknownImage, resolve_image
from .github_app import GitHubApp, GitHubAppError
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


#: How long a GitHub install redirect stays valid. Long enough to read
#: GitHub's permission screen and think about it; short enough that a state
#: captured from a browser's history is dead by the time it is useful.
GITHUB_STATE_TTL = timedelta(minutes=15)


async def _installation_token_for(
    db: psycopg.Connection,
    github_app: GitHubApp,
    user_id: str,
    owner: str,
) -> str | None:
    """A token that can read `owner`'s private repos on this user's behalf,
    or None — which is the ordinary case and means an anonymous fetch.

    **Never raises.** A submit must not fail because GitHub had a bad
    minute: without a token the fetch simply proceeds anonymously, which
    succeeds for a public repo and produces the same 404 a private repo has
    always produced. Turning a transient GitHub outage into a hard submit
    failure would take the public path down with the private one.

    The lookup is scoped by user_id AND owner together. Either half alone is
    a real hole: without owner, connecting one organisation would
    authenticate fetches of every other; without user_id, one person's
    connection would authenticate everybody's.
    """
    if not github_app.configured:
        return None

    row = await run_in_threadpool(
        dbmod.fetch_github_installation_for_owner, db, user_id, owner
    )
    if row is None:
        return None

    try:
        return await github_app.installation_token(int(row["installation_id"]))
    except GitHubAppError as exc:
        log.warning(
            "could not mint a GitHub token for installation %s (%s); "
            "falling back to an anonymous fetch",
            row["installation_id"],
            exc.kind,
        )
        return None


def _fetch_and_extract(
    fetch_repo: Callable[..., bytes],
    owner: str,
    name: str,
    ref: str,
    dest: Path,
    token: str | None = None,
) -> tuple[bytes, Path]:
    """Fetch and unpack a repo. Blocking on purpose — the caller runs it in
    a worker thread so a 32 MB tarball does not stall the event loop for
    every other request in the process.

    ``token`` is a GitHub App installation token when the submitter has
    connected one covering this owner, and None otherwise. None is the
    ordinary case and means exactly what it always meant: an anonymous
    fetch of a public repo.
    """
    tar_bytes = fetch_repo(owner, name, ref, token)
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


# ---------------------------------------------------------------------------
# declared datasets: what a host must hold, and what a host says it can
# ---------------------------------------------------------------------------


def _human_bytes(value: int) -> str:
    """Bytes in the units the person reading the refusal thinks in.

    A refusal that says ``8589934592`` and ``2147483648`` makes the reader do
    the arithmetic that decides whether their job runs. It says ``8.0 GB`` and
    ``2.0 GB`` instead.
    """
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if abs(value) >= size:
            return f"{value / size:.1f} {unit}"
    return f"{value} bytes"


def _advertised_dataset_cache_bytes(parsed: object) -> int:
    """``capabilities.dataset_cache_bytes`` off a registration body, or 0.

    Mirrors the runtime scheduler's own reading of this field, leg for leg,
    because the two numbers have to agree: this one decides whether a job is
    admitted at all, that one decides whether a machine may claim its tasks,
    and a machine admitted here but refused there is a job that sits PENDING
    with nothing to explain it.

    - ``capabilities`` may be absent or type-confused. ``isinstance``, never
      ``(parsed.get("capabilities") or {})`` — a string has no ``.get`` and
      would take down a registration over a display field.
    - ``bool`` is excluded explicitly. It is an ``int`` subclass, so a
      ``dataset_cache_bytes: true`` typo would otherwise advertise one byte
      of capacity and quietly refuse every dataset job on that host.
    - Anything else — a float, a string of digits, a negative — advertises
      nothing. Guessing what a malformed advertisement meant is how a host
      ends up committed to a fetch it has no room for.
    """
    if not isinstance(parsed, dict):
        return 0
    capabilities = parsed.get("capabilities")
    if not isinstance(capabilities, dict):
        return 0
    advertised = capabilities.get("dataset_cache_bytes")
    if not isinstance(advertised, int) or isinstance(advertised, bool):
        return 0
    return max(0, advertised)


def _admit_datasets(
    db: psycopg.Connection,
    *,
    spec: dict[str, Any],
    manifests: dict[str, Any],
    fleet: FleetShape | None,
    pool: str | None,
    findings: list[dict[str, str]],
) -> Response | None:
    """Decide whether the Crew can actually run this job's data.

    Returns a 400 ``Response`` to refuse, or ``None`` to let the submit
    continue. Appends any advisory findings to ``findings`` in place, so a
    warning rides back on the 201 as well as on a refusal.

    **Both numbers are read off the compiled slices, not estimated from the
    manifest.** ``total_bytes / chunks`` is the obvious estimate and it is
    wrong in the direction that matters: the cut is byte-weighted and
    contiguous, so a manifest of ``[300, 100, 4200, 50, 900]`` over five
    slots puts 4200 in one slice while the average says 1110. A job admitted
    on the average is one the runtime's own placement gate — which sums the
    real entry sizes of the real slice — then refuses on every host in the
    Crew, leaving it PENDING with nothing in the console to explain it.
    Reading the compiled spec also means there is exactly one cutter: this
    function never re-derives a layout ``compile._dataset_slices`` already
    produced, including which split each dataset was given.

    The two questions are different and both are asked:

    * **Can one host hold a slice?** The MAX over tasks of the SUM over that
      task's datasets — a host holds every dataset its task declares, at
      once, and a slice is fetched whole by one machine. Refusal.
    * **Can the fleet spread out over it?** A dataset with three files, or
      with one dominant file, strands machines whatever the pool size. That
      is the complaint that killed the ``shards:`` knob, so it is said out
      loud — but it is a warning, because a small dataset during development
      is a legitimate thing to run (owner decision).
    """
    parameters = spec.get("spec", {}).get("workload", {}).get("parameters", {})
    slices = parameters.get(DATASET_SLICES_PARAM) or []
    if not slices:
        return None

    # The compiler's own split decision, read back. Re-deriving "federated →
    # shard, everything else → replica" here would be a second copy of a rule
    # that is free to drift from the one that cut the bytes.
    splits = {
        declared["name"]: declared["split"]
        for task_slice in slices
        for declared in task_slice
    }

    # How wide the pass is cut. For a federated round that is the fleet's
    # chunk count, which is not the number of slots this round happens to
    # open; for anything else it is one chunk per task.
    width = fleet.total_chunks if fleet is not None else len(slices)
    for name in sorted(splits):
        if splits[name] != SPLIT_SHARD:
            continue  # a replica lands whole on every task; it cannot strand
        manifest = manifests.get(name)
        if manifest is None:
            continue
        sizes = [entry.size for entry in manifest.entries]
        # The cap answers "more slots than files"; `effective_width` answers
        # the question the cap only approximates — how many slots the cut
        # actually FILLS. They differ whenever one file dominates: five files
        # over five slots caps to five and warns about nothing, while the
        # byte-weighted cut fills three and strands two.
        capped, _ = cap_chunks_to_manifest(width, len(sizes))
        usable = effective_width(sizes, capped)
        if usable >= width:
            continue
        findings.append({
            "level": "warning",
            "code": "dataset-under-sharded",
            "message": (
                f"dataset {name!r} has {len(sizes)} file(s), and their bytes "
                f"spread across only {usable} of this job's {width} parallel "
                f"slot(s) — the other {width - usable} would fetch nothing "
                f"and train nothing. Split it into more, and more evenly "
                f"sized, files to use the whole fleet."
            ),
        })

    needed = max(
        (
            sum(int(entry["size"]) for d in task_slice for entry in d["entries"])
            for task_slice in slices
        ),
        default=0,
    )
    best = dbmod.dataset_capacity_in_pool(db, pool_id=pool)
    if needed <= best:
        return None

    where = "in this workspace" if pool is not None else "in the Crew"
    nobody = (
        " In fact no online machine advertises any dataset cache at all, so "
        "either the Crew is asleep or its agents predate the release that "
        "advertises one."
        if best <= 0
        else ""
    )
    return Response(
        content=json.dumps({
            "detail": (
                f"this job's largest task would have to hold "
                f"{_human_bytes(needed)} of {', '.join(sorted(splits))} at "
                f"once, and the largest dataset cache advertised by an online "
                f"machine {where} is {_human_bytes(best)}. A slice is fetched "
                f"whole by one host, so the fleet's combined space does not "
                f"help.{nobody} Raise FLASHNODE_DATA_BUDGET_GB on a machine, "
                f"bring a bigger one online, or declare a smaller dataset."
            ),
            "findings": findings,
        }),
        status_code=400,
        media_type="application/json",
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

# The same idea, sized for an AGENT waiting on the far end rather than a
# browser — and it has to be shorter, not longer.
#
# flashnode's HTTP timeout is 15s (`executor/client.py`), and it does NOT
# retry a status it received: `_request` catches `HTTPError` and returns the
# code straight to the caller, retrying only when nothing answered at all.
# So a ladder outlasting 15s cannot help anyone — the agent has already given
# up and is raising by the time the last forward goes out. 1+2+4 = 7s leaves
# room for the four forwards themselves inside that budget.
#
# Why an agent GET needs this at all: `forward` turns "coordinator did not
# answer" into a 502 *answer* (see its except clause). To flashnode that is
# an HTTPError, which it trusts as a decision and does not retry — so our
# gateway shape defeats the transport retry the agent already has.
AGENT_RETRY_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0)


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
    *,
    delays: tuple[float, ...] = GATEWAY_RETRY_DELAYS,
    **kwargs: Any,
) -> httpx.Response:
    """``forward`` plus a retry while the coordinator is still coming up.

    ONLY for calls that are safe to repeat. The artifact PUT qualifies
    because its key is a freshly minted uuid: a retry writes the same bytes
    to the same never-before-used key, so repeating it cannot overwrite
    another user's staged code, nor an earlier attempt of this same request.
    Do not reach for this on job submission — a repeated POST there is a
    duplicate job.

    ``delays`` is the ladder to walk. It is a parameter because the right
    one depends on who is waiting: a browser will sit through a cold start,
    an agent with a 15s socket timeout will not (``AGENT_RETRY_DELAYS``).
    """
    last = await coordinator.forward(method, path, **kwargs)
    if last.status_code not in GATEWAY_STATUSES:
        return last

    for delay in delays:
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


async def _mirror_job_artifacts(
    coordinator: CoordinatorClient,
    db: psycopg.Connection,
    job_id: str,
    settings: Settings,
) -> None:
    """Copy a finished job's accepted artifacts to OSS, once, and write it down.

    THE SAME SHAPE AS ``_record_artifact_footprint`` ABOVE, and for the same
    reasons — which is why it sits beside it rather than inside the route.
    The caller's guard is ``jobs.artifacts_mirrored_at`` (migration 0016),
    read off the row it already fetched for the visibility check, so a page
    left polling a finished job re-enters this at most once.

    NEVER FATAL, AND NOTHING STAMPED ON FAILURE. A mirror is best-effort in
    exactly the way usage accounting is: the coordinator accepted the commit
    with no knowledge of this API, and the artifacts are on its disk whether
    or not the copy succeeds. A job page that 500s — or worse, a job reported
    FAILED — because a bucket in Singapore was slow would be strictly worse
    than a mirror that lands one poll later. ``MirrorError`` is the module's
    only exit for every cause, and the correct response to all of them is the
    same: log it, leave the marker null, let the next observation retry.

    ``NOT_CONFIGURED`` IS NOT SUCCESS AND IS NOT STAMPED. On a deployment
    with no OSS this returns having made no coordinator call and opened no
    socket (``mirror_job`` gates on ``settings.oss_configured`` before
    anything else), so re-entering it on every poll costs a property read —
    and leaving the marker null is what lets those jobs be mirrored the day
    OSS is configured rather than being excluded for ever.

    Non-federated jobs only, like the footprint hook: a federated run is N
    coordinator jobs under a parent id the coordinator has never heard of,
    and mirroring it means ``mirror_jobs`` over the round jobs from the
    driver that observes the run ending.
    """
    try:
        result = await mirror_job(
            job_id, CoordinatorArtifactSource(coordinator), settings
        )
    except MirrorError as exc:
        # Logged rather than raised, and deliberately not stamped: the next
        # observation of this terminal job runs the whole thing again, and
        # `mirror_job` is idempotent, so a retry re-copies only what is
        # missing. A mirror recorded as done is the one failure here that
        # loses data silently.
        log.warning(
            json.dumps({"text": "could not mirror artifacts to OSS",
                        "job_id": job_id, "error": str(exc)})
        )
        return
    except Exception:  # noqa: BLE001 - a mirror must never fail the user's read
        # `mirror_job` promises MirrorError for every failure it knows about.
        # This catch is for the ones it does not — the caller's rule is
        # "never fail the request", and that rule cannot depend on another
        # module's exception discipline holding for ever.
        log.warning(
            json.dumps({"text": "could not mirror artifacts to OSS",
                        "job_id": job_id, "error": "unexpected"})
        )
        return

    if not result.mirrored:
        # NOT_CONFIGURED. Nothing happened and nothing is claimed.
        return
    try:
        dbmod.mark_job_artifacts_mirrored(db, job_id)
    except Exception:  # noqa: BLE001 - same rule: never fail the user's read
        # The objects and their manifest ARE in OSS; only the note saying so
        # failed. The next poll re-enters, finds the current manifest, and
        # answers ALREADY_MIRRORED without copying anything — so the cost of
        # this is one HEAD, not a second transfer.
        log.warning(
            json.dumps({"text": "mirrored, but could not record it",
                        "job_id": job_id})
        )


def _relative_artifacts(job_id: str, listing: Any) -> list[dict[str, Any]]:
    """The coordinator's artifact listing, keyed RELATIVE to the job prefix.

    The coordinator answers absolute keys (``jobs/{job_id}/shard-000/out.bin``)
    because that is how its store is addressed. The browser is handed the
    remainder (``shard-000/out.bin``) for one concrete reason: it is exactly
    what ``GET /v1alpha1/jobs/{job_id}/artifacts/{key}`` takes, so a console
    that lists a job's files can build every download link by concatenation
    and never has to know the store's layout. Returning the absolute key would
    make every caller strip the prefix itself, and the first one to forget
    would ask for ``jobs/{job}/jobs/{job}/…``.

    **Nothing is invented and nothing is inferred.** An entry with no usable
    ``key``, or one that does not sit under this job's prefix, is dropped —
    a listing that disagrees with the job it was asked about is not evidence
    about this job. An entry whose ``size_bytes`` is missing or nonsensical is
    KEPT, at 0: the file demonstrably exists and hiding it would be the worse
    error, while a guessed size would be a number nobody measured. That is the
    same judgement — and the same explicit ``bool`` exclusion, since ``True``
    is an ``int`` — that ``storage.sum_artifact_sizes`` makes about the very
    same payload, and the two must not disagree about one listing.
    """
    if not isinstance(listing, list):
        return []
    prefix = job_prefix(job_id)
    out: list[dict[str, Any]] = []
    for entry in listing:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if not isinstance(key, str) or not key.startswith(prefix):
            continue
        relative = key[len(prefix):]
        if not relative:
            continue
        size = entry.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            size = 0
        out.append({"key": relative, "size_bytes": size})
    out.sort(key=lambda e: e["key"])
    return out


def _artifact_filename(key: str) -> str:
    """What a downloaded artifact is saved as: the key with ``/`` flattened.

    ``shard-000/stdout.txt`` becomes ``shard-000__stdout.txt``. The last
    segment alone would be the obvious choice and is the wrong one: every task
    of a job writes ``stdout.txt``, so a run with twenty shards would save
    twenty files the browser silently renames ``stdout (1).txt`` …
    ``stdout (19).txt``, erasing the one fact — which task — the person opened
    them to learn. The console's ``lib/bulk-download.ts`` already flattens the
    same way for its ``download`` attribute; the two agree deliberately, so a
    file saves under the same name whichever path it came down.

    **The result is header-safe because ``_artifact_key`` already said so.**
    Every segment of a key that reaches here has matched
    ``PATH_SEGMENT_RE`` — ``[A-Za-z0-9][A-Za-z0-9._:-]{0,255}`` — which admits
    no quote, no backslash, no control character and nothing outside ASCII, so
    the quoted-string form below needs no escaping and no RFC 5987 fallback.
    That is a dependency between two functions, not a coincidence: loosening
    that alphabet without revisiting this would let a key inject a header
    parameter. A test pins the pair together.
    """
    return key.replace("/", "__")


def _attachment_disposition(key: str) -> str:
    """``Content-Disposition`` for one artifact download, in both directions.

    WITHOUT THIS A DOWNLOAD NAVIGATES THE CONSOLE AWAY. Both download paths
    end in a navigation for a mirrored object (a presigned OSS URL, which is a
    cross-origin GET the browser performs itself), and a navigation to
    ``metrics.json`` served as ``application/json`` renders it in the tab the
    console was in. ``attachment`` is what turns that into a save. It is set on
    the proxy path too, so the two paths behave the same way rather than
    differing by whether a bucket happens to be configured.
    """
    return f'attachment; filename="{_artifact_filename(key)}"'


async def _mirrored_artifact_url(
    job_id: str,
    coordinator_key: str,
    row: dict[str, Any],
    settings: Settings,
    *,
    content_disposition: str | None = None,
) -> str | None:
    """A presigned OSS URL for one artifact, or None to serve it as before.

    WHY A REDIRECT AND NOT A SERVER-SIDE FETCH. The point of the mirror, for
    downloads, is that the bytes stop travelling through this process: a 500 MB
    model pulled from OSS and re-streamed to the browser costs the API the
    whole transfer twice over and holds a worker for its duration, which is how
    one person downloading a model makes the control plane unavailable to
    everybody else. A 307 hands the browser a single-object, expiring grant and
    the transfer happens between the browser and OSS. The API's part is one
    signature.

    NONE IS THE NORMAL ANSWER, NOT THE EXCEPTIONAL ONE. It means "serve this
    from the coordinator exactly as before", and four different situations
    reach it, all of them ordinary:

      * no OSS configured — the deployment default, and the gate is FIRST so
        that such a deployment does not build a client or open a socket;
      * this job was never mirrored (``artifacts_mirrored_at`` null, read off
        the row the visibility check already fetched — no extra query);
      * the key is not in the job's manifest, which is what an unaccepted
        task's output IS: hard rule 4 keeps a failed shard's bytes on the
        coordinator's disk deliberately, and this route is how they stay
        reachable;
      * OSS is configured and unwell.

    That last one is why every failure here is logged and swallowed rather than
    raised. The coordinator still holds every byte this route can be asked for
    — the mirror is an ADDITIONAL copy, never a migration — so a bucket having
    a bad minute must cost a slower download, not a broken one. The inverse,
    a 502 for an artifact that is sitting on a disk we can reach, would make
    configuring OSS strictly worse than not configuring it.
    """
    if not settings.oss_configured or row.get("artifacts_mirrored_at") is None:
        return None
    try:
        oss = OSSArtifacts.from_settings(settings)
        return await presign_mirrored_artifact(
            job_id, coordinator_key, oss,
            content_disposition=content_disposition,
        )
    except (OSSUnavailable, MirrorError) as exc:
        log.warning(
            json.dumps({"text": "could not presign a mirrored artifact; "
                                "serving it from the coordinator",
                        "job_id": job_id, "error": str(exc)})
        )
        return None
    except Exception:  # noqa: BLE001 - a download must never fail on the mirror
        # The narrow catch above covers what the two modules promise. This one
        # exists because the promise is another module's to keep and this
        # route's fallback has to hold whether or not it does.
        log.warning(
            json.dumps({"text": "could not presign a mirrored artifact; "
                                "serving it from the coordinator",
                        "job_id": job_id, "error": "unexpected"})
        )
        return None


def _passthrough(
    r: httpx.Response, *, headers: dict[str, str] | None = None
) -> Response:
    """Return the coordinator's answer verbatim: status *and* body.

    Status fidelity is load-bearing — ``claim`` answers 204 for "nothing to
    do right now", and flattening that to 200 would make an idle agent think
    it had been given work.

    ``headers`` adds to the answer without touching it; the only caller is the
    artifact proxy, adding the ``Content-Disposition`` the coordinator has no
    reason to know about (it serves a store, not a browser). It is deliberately
    additive and never a rewrite — nothing here may edit what the coordinator
    said, only say something further.
    """
    return Response(
        content=r.content,
        status_code=r.status_code,
        media_type=r.headers.get("content-type"),
        headers=headers,
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
# FC Sandbox: the evaluation, and the public evidence view
# ---------------------------------------------------------------------------
#
# `sandbox_orchestrator` deliberately holds no HTTP client and adds no routes.
# Everything in this section is the half it left to `app`: the operator
# credential lives here, so submitting the evaluation and reading its verdict
# live here too, behind the `EvaluationDriver` protocol it takes injected.


#: Prefix of the deterministic coordinator job name a session's evaluation is
#: submitted under. See `evaluation_job_name` for why the name — and not a
#: header — is what makes `submit` idempotent.
EVALUATION_NAME_PREFIX = "fc-eval-"

#: The workload input naming what is being evaluated. It is the training job's
#: artifact prefix, not one file: the sandbox reads the model through the
#: presigned URLs `sandbox_orchestrator` writes into `artifacts.json`, and this
#: names the same key space those URLs cover (`artifact_mirror.job_prefix`
#: produces the coordinator's own layout, so a mirrored key IS the coordinator
#: key and nothing has to translate).
EVALUATION_MODEL_INPUT = "model"

#: How much of `marker_sha256` the PUBLIC page shows. A hash prefix is enough
#: to eyeball two renderings of the same session against each other and short
#: enough not to be the value itself.
PUBLIC_HASH_CHARS = 12

#: How much of a session id the PUBLIC page shows. `SESSION_SHARE_COLUMNS`
#: keeps `id` because the route needs it to read the session's events, with the
#: explicit instruction that the route renders a SUFFIX and never the whole
#: value — so the key is named for what it is and the full uuid never leaves.
PUBLIC_ID_SUFFIX_CHARS = 12

#: Public-view rate limit: requests per window, per client address. Overridable
#: with FLASHML_PUBLIC_RATE_LIMIT / FLASHML_PUBLIC_RATE_WINDOW_S.
DEFAULT_PUBLIC_RATE_LIMIT = 60
DEFAULT_PUBLIC_RATE_WINDOW_S = 60.0

#: How often the reconciler sweeps. The thing it is racing is money: an
#: abandoned sandbox bills by the second, so this is minutes and not hours.
#: Overridable with FLASHML_SANDBOX_RECONCILE_S; <= 0 runs the startup sweep
#: and no loop.
DEFAULT_RECONCILE_INTERVAL_S = 300.0

#: Rental sessions trade persistence for safe reuse. Fifteen minutes is long
#: enough to ride out ordinary network gaps and short enough that a terminated
#: paid pod does not remain attached to an account for days.
DEFAULT_EPHEMERAL_MACHINE_TTL_S = 15 * 60.0
DEFAULT_EPHEMERAL_RECONCILE_S = 60.0

#: How often the RENTED-CAPACITY sweep runs — a different reconciler from the
#: sandbox one above, over a different table, racing the same thing. Five
#: minutes for the same reason: a rented GPU bills by the second and this is
#: the only backstop that stops one. Overridable with
#: FLASHML_RENTED_RECONCILE_S; <= 0 runs a single immediate sweep and no loop.
#:
#: **Not a teardown window.** `capacity.reconcile` takes four of those and
#: none of them is this number; passing a poll interval as one would destroy
#: live machines, which is why that module's windows all have safe defaults
#: and this file passes none of them.
DEFAULT_RENTED_RECONCILE_S = 300.0


class EvaluationSpecError(ValueError):
    """The stored ``evaluation_spec`` cannot be compiled into a JobSpec.

    Raised by :func:`build_evaluation_jobspec`, which the CREATE route calls
    **before** anything is provisioned precisely so this surfaces as a 400 on a
    request that cost nothing, rather than as a session that spends ten seconds
    and a real sandbox to discover it on the far side of a hibernation.
    """


def evaluation_job_name(session_id: str) -> str:
    """The coordinator job name this session's evaluation is submitted under.

    **This is the idempotency mechanism**, and it is a fallback rather than a
    preference: the coordinator's ``POST /v1alpha1/jobs`` takes a bare
    ``JobSpec`` and mints ``uuid.uuid4().hex[:12]`` — it reads no
    ``Idempotency-Key``, and there is no key on the spec either. So there is no
    server-side dedupe to ask for, and the only thing a resubmission can be
    recognised by is something the submitter chose deterministically. A name
    derived from the session id is that thing: :meth:`EvaluationDriver.submit`
    lists the coordinator's jobs and returns the existing id when this name is
    already present.

    Derived, never random, for ``sandbox_orchestrator.node_id_for``'s reason —
    a controller that died between the coordinator accepting the job and the
    ledger append landing has no record of the id, and must be able to
    recognise its own submission on the way back up.

    A session id is a uuid: 36 lowercase hex-and-hyphen characters, which with
    the prefix is 44 — inside ``JobMetadata``'s DNS-1123 limit of 63, and
    already a legal label. ``sanitize_job_name`` is applied anyway so the
    invariant is enforced here rather than assumed.
    """
    return sanitize_job_name(f"{EVALUATION_NAME_PREFIX}{session_id}")


def build_evaluation_jobspec(
    *,
    session_id: str,
    pool_id: str,
    training_job_id: str,
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile a session's ``evaluation_spec`` into a lease-mode JobSpec.

    The submitter chooses **what to run** — image and command, plus the
    ordinary workload parameters ``compile_to_jobspec`` also forwards verbatim.
    This function chooses everything that is not theirs to choose, and every
    one of those is load-bearing:

    * ``metadata.name`` is derived from the session id, because it is the
      idempotency anchor (:func:`evaluation_job_name`).
    * ``execution.backend`` is ``leases``. A sandbox session is one worker
      pulling one task; there is no cluster here to select.
    * ``placement.pool`` is the isolation pool, and ``isolation.allowFallback``
      is true because the two move together — ``CommandRecipe.expand`` enforces
      allowFallback-iff-pool, and ``compile_to_jobspec`` pins the same rule.
      Dropping the pool would offer this task to any volunteer laptop online,
      which is the seventh placement gate's whole job.
    * ``resources`` is one worker. The pool contains exactly one machine; a
      spec asking for two would never place.
    * ``inputs`` names the model artifact (:data:`EVALUATION_MODEL_INPUT`).

    A spec that supplies neither image nor command is refused rather than
    defaulted: a default image would run somebody's evaluation against a
    container they did not pick and could not see.
    """
    if not isinstance(spec, Mapping):
        raise EvaluationSpecError("evaluation_spec must be a JSON object")

    image = spec.get("image")
    if isinstance(image, str):
        repository, _, tag = image.rpartition(":")
        if not repository or not tag:
            raise EvaluationSpecError(
                "evaluation_spec.image must be 'repository:tag' with a pinned "
                "tag, or an object with 'repository' and 'tag'"
            )
    elif isinstance(image, Mapping):
        repository = str(image.get("repository") or "")
        tag = str(image.get("tag") or "")
    else:
        raise EvaluationSpecError("evaluation_spec.image is required")
    if not repository or not tag:
        raise EvaluationSpecError(
            "evaluation_spec.image needs both a repository and a pinned tag"
        )

    command = spec.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(token, str) for token in command)
    ):
        raise EvaluationSpecError(
            "evaluation_spec.command must be a non-empty list of strings"
        )

    env = spec.get("env") or {}
    if not isinstance(env, Mapping):
        raise EvaluationSpecError("evaluation_spec.env must be an object")

    parameters: dict[str, Any] = {
        "command": list(command),
        "inputs": {
            EVALUATION_MODEL_INPUT: f"artifact://{job_prefix(training_job_id)}"
        },
        "env": {str(k): str(v) for k, v in env.items()},
    }
    timeout_seconds = spec.get("timeout_seconds")
    if timeout_seconds is not None:
        try:
            parameters["timeout_seconds"] = int(timeout_seconds)
        except (TypeError, ValueError):
            raise EvaluationSpecError(
                "evaluation_spec.timeout_seconds must be an integer"
            ) from None
    # Forwarded verbatim, exactly as `compile_to_jobspec` forwards them: the
    # coordinator owns their semantics and a second copy of those rules here
    # would be a second thing to keep aligned. Absent stays absent.
    for passthrough in ("dependencies", "extra_dependencies", "validators",
                        "reduce", "retention"):
        if spec.get(passthrough):
            parameters[passthrough] = spec[passthrough]

    jobspec: dict[str, Any] = {
        "apiVersion": "flashml.dev/v1alpha1",
        "kind": "Job",
        "metadata": {
            "name": evaluation_job_name(session_id),
            "labels": {"flashml.dev/source": "fc-sandbox-evaluation"},
        },
        "spec": {
            "execution": {"backend": "leases", "environment": "auto"},
            "image": {"repository": repository, "tag": tag},
            "workload": {"type": "command", "parameters": parameters},
            "resources": {"minimumWorkers": 1, "maximumWorkers": 1},
            "isolation": {"tier": "sandboxed", "allowFallback": True},
            "placement": {"pool": pool_id},
            "artifacts": {"outputPrefix": "artifact://jobs/{job_id}/"},
        },
    }
    try:
        validated = JobSpec.model_validate(jobspec)
    except Exception as exc:  # pydantic ValidationError and anything under it
        raise EvaluationSpecError(
            f"the compiled evaluation JobSpec is invalid: {exc}"
        ) from None
    return json.loads(validated.model_dump_json())


class CoordinatorEvaluationDriver:
    """``sandbox_orchestrator.EvaluationDriver`` over :class:`CoordinatorClient`.

    Injected rather than imported by the orchestrator, for the two reasons
    ``artifact_mirror.ArtifactSource`` gives: this module imports the
    orchestrator, so importing back would close a cycle; and every ordering
    rule over there stays testable with a dictionary.

    **Idempotency: a deterministic job NAME, looked up before submitting.**
    The coordinator exposes no idempotency key — ``POST /v1alpha1/jobs`` takes
    a bare ``JobSpec`` and mints its own id — so there is nothing to send. What
    there is instead is ``GET /v1alpha1/jobs``, which returns each record with
    the spec it was submitted with; :func:`evaluation_job_name` derives a name
    from the session id, and a job already carrying that name IS this session's
    evaluation. That closes the window the orchestrator cannot: between the
    coordinator accepting a submission and the ``evaluation.submitted`` append
    landing, a restarted controller finds the job by name rather than placing a
    second one on a pool that holds a single machine.

    The lookup is not free (one listing per submission) and it is only paid on
    the path where the orchestrator has already failed to find a recorded id —
    which is a resume, not the ordinary case.
    """

    def __init__(self, coordinator: CoordinatorClient) -> None:
        self._coordinator = coordinator

    async def submit(self, request: Any) -> str:
        name = evaluation_job_name(request.session_id)
        existing = await self._job_id_named(name)
        if existing:
            log.info(json.dumps({
                "text": "evaluation already submitted for this session",
                "session_id": request.session_id, "evaluation_job_id": existing,
            }))
            return existing

        spec = build_evaluation_jobspec(
            session_id=request.session_id,
            pool_id=request.pool_id,
            training_job_id=request.training_job_id,
            spec=request.spec,
        )
        r = await self._coordinator.forward(
            "POST", "/v1alpha1/jobs",
            content=json.dumps(spec).encode(),
            media_type="application/json",
        )
        if r.status_code >= 300:
            # Re-check by name before giving up: a 5xx from a coordinator that
            # had already accepted the job is exactly the transport failure
            # whose call succeeded, and resubmitting on it is how one session
            # ends up with two evaluations.
            recovered = await self._job_id_named(name)
            if recovered:
                return recovered
            raise RuntimeError(
                f"the coordinator refused the evaluation ({r.status_code})"
            )
        try:
            job = r.json()
        except ValueError:
            raise RuntimeError(
                "the coordinator accepted the evaluation with an unreadable body"
            ) from None
        job_id = job.get("job_id") if isinstance(job, dict) else None
        if not job_id:
            raise RuntimeError(
                "the evaluation was accepted with no job id to own it by"
            )
        return str(job_id)

    async def poll(self, evaluation_job_id: str) -> Any:
        """The settled outcome, or ``None`` while the job is still running.

        ``None`` means *not yet* and never *failed* — a job that ended badly is
        an outcome with ``accepted=False``. A coordinator that cannot be read
        at all is also ``None``: it is not evidence the job is over, and the
        orchestrator's own deadline is what stops the wait.

        ``accepted`` is ``state == SUCCEEDED`` and nothing looser. PARTIAL is
        specifically NOT accepted: it means some tasks exhausted their attempts
        under ``allowPartial``, and an evaluation that lost shards has not
        produced the verdict somebody asked for (repo hard rule 4).
        """
        r = await self._coordinator.forward(
            "GET", f"/v1alpha1/jobs/{_seg(evaluation_job_id)}"
        )
        if r.status_code >= 300:
            return None
        try:
            job = r.json()
        except ValueError:
            return None
        if not isinstance(job, dict):
            return None
        state = str(job.get("state") or "")
        try:
            terminal = JobState(state).terminal
        except ValueError:
            # An unrecognised state is not terminal. That direction cannot
            # publish an unfinished run as a result.
            return None
        if not terminal:
            return None

        data: dict[str, Any] = {"state": state}
        claim_ms = await self._submit_to_claim_ms(evaluation_job_id)
        if claim_ms is not None:
            data["submit_to_claim_ms"] = claim_ms
        return orchmod.EvaluationOutcome(
            evaluation_job_id=evaluation_job_id,
            accepted=state == JobState.SUCCEEDED.value,
            detail=state,
            data=data,
        )

    async def _job_id_named(self, name: str) -> str | None:
        """The coordinator job carrying this exact ``metadata.name``, if any.

        Read from the listing rather than from a search endpoint because there
        is no search endpoint. Failure answers ``None``, which makes the caller
        submit — the alternative, treating an unreadable listing as "already
        submitted", would strand a session waiting on a job that was never
        placed.
        """
        try:
            r = await self._coordinator.forward("GET", "/v1alpha1/jobs")
        except HTTPException:
            return None
        if r.status_code >= 300:
            return None
        try:
            jobs = r.json()
        except ValueError:
            return None
        if not isinstance(jobs, list):
            return None
        for job in jobs:
            if not isinstance(job, dict):
                continue
            spec = job.get("spec")
            metadata = spec.get("metadata") if isinstance(spec, dict) else None
            if not isinstance(metadata, dict):
                continue
            if metadata.get("name") == name and job.get("job_id"):
                return str(job["job_id"])
        return None

    async def _submit_to_claim_ms(self, evaluation_job_id: str) -> float | None:
        """Milliseconds from the coordinator accepting this job to the first
        lease being claimed on it, read off the coordinator's own ledger.

        **The only honest source for time-to-first-claim.** Both endpoints of
        the interval are the coordinator's own timestamps (``JOB_ACCEPTED`` and
        the first ``LEASE_CLAIMED``), so the number is not contaminated by this
        API's clock, the hop to it, or the wake that preceded it — which is
        measured separately, on ``sandbox.woken``. Absent when the ledger does
        not carry both, rather than estimated from anything here.
        """
        r = await self._coordinator.forward(
            "GET", f"/v1alpha1/jobs/{_seg(evaluation_job_id)}/events"
        )
        if r.status_code >= 300:
            return None
        try:
            events = r.json()
        except ValueError:
            return None
        if not isinstance(events, list):
            return None
        accepted_at: datetime | None = None
        claimed_at: datetime | None = None
        for event in events:
            if not isinstance(event, dict):
                continue
            stamp = _parse_timestamp(event.get("timestamp"))
            if stamp is None:
                continue
            type_ = event.get("type")
            if type_ == "JOB_ACCEPTED" and accepted_at is None:
                accepted_at = stamp
            elif type_ == "LEASE_CLAIMED" and claimed_at is None:
                claimed_at = stamp
        if accepted_at is None or claimed_at is None:
            return None
        return round((claimed_at - accepted_at).total_seconds() * 1000, 3)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def public_session_view(row: Mapping[str, Any]) -> dict[str, Any]:
    """One session as the UNAUTHENTICATED evidence page may see it.

    ``fetch_session_by_share_token`` has already narrowed the columns in SQL —
    owner, pool, machine, sandbox id, evaluation spec and the share token
    itself never leave the database. This narrows twice more, on the two
    columns that survive that cut but must not be rendered whole:

    * ``id`` is kept by :data:`sandbox_sessions.SESSION_SHARE_COLUMNS` only so
      the route can read the session's events, with the explicit instruction
      that a SUFFIX is rendered and never the value. So the key is called
      ``id_suffix`` and there is no ``id`` — a field named ``id`` holding half
      a uuid is the kind of thing a later refactor "fixes" by putting the whole
      one back.
    * ``error_message`` is dropped entirely. It is redacted on the way into the
      ledger, but redaction is a key-name matcher over provider exceptions and
      this page has no login in front of it; the sanitized ``error_code`` is
      what the console links to the evidence page for, and it is enough.

    ``marker_sha256`` is truncated to :data:`PUBLIC_HASH_CHARS`.
    """
    marker = row.get("marker_sha256")
    return {
        "id_suffix": str(row["id"])[-PUBLIC_ID_SUFFIX_CHARS:],
        "training_job_id": row.get("training_job_id"),
        "evaluation_job_id": row.get("evaluation_job_id"),
        "provider": row.get("provider"),
        "region": row.get("region"),
        "template": row.get("template"),
        "state": row.get("state"),
        "marker_sha256_prefix": (
            str(marker)[:PUBLIC_HASH_CHARS] if marker else None
        ),
        "created_at": _isoformat(row.get("created_at")),
        "updated_at": _isoformat(row.get("updated_at")),
        "terminated_at": _isoformat(row.get("terminated_at")),
        "error_code": row.get("error_code"),
    }


def public_event_view(event: Mapping[str, Any]) -> dict[str, Any]:
    """One ledger event as the public page may see it.

    ``data`` is passed through because ``sandbox_sessions.redact_data`` already
    scrubbed it on the way in — that is the module's third property and the
    reason there is no second sanitiser here to drift from it. The event's own
    ``id`` and its ``session_id`` are dropped for the reason the session's id
    is: they are full identifiers, and nothing on this page needs them.
    """
    return {
        "sequence": event.get("sequence"),
        "type": event.get("type"),
        "source": event.get("source"),
        "observed_at": _isoformat(event.get("observed_at")),
        "latency_ms": event.get("latency_ms"),
        "data": event.get("data") or {},
    }


def _isoformat(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


class FixedWindowLimiter:
    """A per-key fixed-window counter. **In-process, and that is the whole of
    what it claims.**

    There is no rate limiter anywhere in this deployment and no library for one
    in the dependency set, so this is what could be added cheaply rather than
    what a public endpoint deserves. Its limits are honest about that:

    * it counts inside ONE process, so N API instances allow N x the limit, and
      a restart forgets everything;
    * it keys on the client address, which behind Render's proxy is whatever
      Starlette resolved — good enough to stop a loop, useless against a
      distributed source.

    What it does buy is real: the public evidence route is the only
    unauthenticated route in this API that reads the database, and without this
    a single `while true; do curl` opens a Postgres round trip per iteration.
    A shared limiter (Redis, or Render's own edge) is the correct fix and this
    is not it.
    """

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = int(limit)
        self.window_s = float(window_s)
        self._hits: dict[str, tuple[float, int]] = {}

    def allow(self, key: str, *, now: float | None = None) -> bool:
        if self.limit <= 0 or self.window_s <= 0:
            return True
        current = time.monotonic() if now is None else now
        window = int(current // self.window_s)
        seen, count = self._hits.get(key, (window, 0))
        if seen != window:
            seen, count = window, 0
        count += 1
        self._hits[key] = (seen, count)
        if len(self._hits) > 4096:
            # Bounded, because the key is attacker-chosen: a source cycling
            # addresses must not be able to grow this dict without limit. The
            # cheapest correct answer is to drop everything and start the
            # window again — worst case one window is under-counted, which is
            # strictly better than an unbounded map in a web process.
            self._hits = {key: (seen, count)}
        return count <= self.limit


# ---------------------------------------------------------------------------
# the plan preview, as JSON
#
# Read-only all the way down. Nothing below writes a row, matches a listing,
# holds a credit or submits anything — the comparison view proves routing
# without touching money, which is the whole reason it can be shown to
# somebody before they have decided anything.
#
# Two rules run through every function here:
#
# **Settlement totals stay separate; comparison uses fixed cash value.**
# `router.plan.Cost` keeps ZC and USD as their original source totals and adds
# `total_usd_value` under the active 1 ZC = 1 USD policy. Budget verdicts stay
# per settlement currency: normalized value compares plans; it does not decide
# which account or provider gets charged.
#
# **Every figure carries its basis and its n, or it is null.** `null` renders
# as *not observed*; 0 is a claim, and on this surface it is usually a
# flattering one.
# ---------------------------------------------------------------------------

#: Strongest first. Used only to take the WEAKEST basis behind a plan: a plan
#: is never better founded than the thinnest evidence it rests on.
_BASIS_STRENGTH = {
    routermod.BASIS_MEASURED: 2,
    routermod.BASIS_ESTIMATED: 1,
    routermod.BASIS_PROJECTED: 0,
}

#: Most tasks a preview will expand and plan. A sweep past this is planned
#: from its first `_PREVIEW_MAX_TASKS` tasks and says so, rather than
#: spending a request body's worth of CPU inside a page load.
_PREVIEW_MAX_TASKS = 5000


def _preview_cost(cost: routermod.Cost) -> dict[str, float]:
    """Source settlement totals and their fixed-rate comparable USD value."""
    rounded = cost.rounded()
    return {
        "zc": rounded.zc,
        "usd": rounded.usd,
        "total_usd_value": round(rounded.total_usd_value(), 4),
    }


def _within_budget(spent: float, budget: float | None) -> bool | None:
    """Whether one currency's spend fits one currency's budget.

    ``None`` when no budget was given for that currency — a fact about a
    question nobody asked, not a pass. A ZC budget says how much may leave
    the wallet and a USD budget says how much may reach an external provider.
    The normalized total is for routing comparison, not display on the job
    routing card and not a replacement settlement budget.
    """
    if budget is None:
        return None
    return float(spent) <= float(budget)


def _weakest_basis(
    estimates: Sequence[routermod.Estimate],
) -> tuple[str | None, int | None]:
    """The basis and sample size a plan may honestly claim.

    The weakest basis among the machines it actually allocates to, and the
    SMALLEST n behind them. A plan that places thirty tasks on measured
    machines and ten on a projected one is a projected plan: the number a
    reader acts on is the total, and the total is only as good as its worst
    input. ``(None, None)`` when nothing was placed — never ``("measured", 0)``.
    """
    if not estimates:
        return (None, None)
    weakest = min(
        estimates, key=lambda e: (_BASIS_STRENGTH.get(e.basis, -1), e.n)
    )
    return (weakest.basis, min(int(e.n) for e in estimates))


def _preview_plan(
    plan: routermod.Plan,
    *,
    estimates_by_machine: Mapping[str, routermod.Estimate],
    recommended: str | None,
    budget_zc: float | None,
    budget_usd: float | None,
) -> dict[str, Any]:
    """One plan as the console renders it, bases and all."""
    used = [
        estimates_by_machine[allocation.machine_id]
        for allocation in plan.allocations
        if allocation.machine_id in estimates_by_machine
    ]
    basis, n = _weakest_basis(used)
    return {
        "name": plan.name,
        "recommended": plan.name == recommended,
        "tasks_placed": plan.tasks_placed,
        "tasks_unplaced": plan.tasks_unplaced,
        "cost": _preview_cost(plan.cost),
        "within_budget": {
            "zc": _within_budget(plan.cost.zc, budget_zc),
            "usd": _within_budget(plan.cost.usd, budget_usd),
        },
        "makespan_seconds": plan.makespan_seconds,
        "deadline_seconds": plan.deadline_seconds,
        "deadline_met": plan.deadline_met,
        "achievable_deadline_seconds": plan.achievable_deadline_seconds,
        "basis": basis,
        "n": n,
        "duration_basis": plan.duration_basis,
        "dominated_by": plan.dominated_by,
        "allocations": [
            {
                "machine_id": allocation.machine_id,
                "tasks": allocation.tasks,
                "finish_seconds": allocation.finish_seconds,
                "cost": _preview_cost(allocation.cost),
                "venue": allocation.venue,
                "currency": allocation.currency,
                "reliability_tier": allocation.reliability_tier,
                "basis": (
                    estimates_by_machine[allocation.machine_id].basis
                    if allocation.machine_id in estimates_by_machine
                    else None
                ),
                "n": (
                    estimates_by_machine[allocation.machine_id].n
                    if allocation.machine_id in estimates_by_machine
                    else None
                ),
            }
            for allocation in plan.allocations
        ],
        "notes": list(plan.notes),
    }


def _preview_estimate(estimate: routermod.Estimate | None) -> dict[str, Any] | None:
    if estimate is None:
        return None
    return {
        "low_seconds": estimate.low,
        "high_seconds": estimate.high,
        "basis": estimate.basis,
        "n": estimate.n,
        "note": estimate.note,
    }


def _preview_canary(canary: routermod.Canary | None) -> dict[str, Any] | None:
    if canary is None:
        return None
    return {
        "machine_id": canary.machine_id,
        "tasks_to_calibrate": canary.tasks_to_calibrate,
        "reason": canary.reason,
        "current_basis": canary.current_basis,
    }


def _preview_node_view(row: Mapping[str, Any]) -> dict[str, Any]:
    """The node view the placement predicate reads, from a registry row.

    ``capabilities.pools`` is stamped from the machine's bindings intersected
    with its owner's live memberships — the same value the register proxy
    stamps onto the registration the coordinator gates against, computed the
    same way, so the seventh gate answers here what it will answer there.

    **Two capabilities this deployment does not record**, and both fail
    closed: ``local_datasets`` (the fourth gate) and
    ``can_install_dependencies`` (the eighth). A task that needs either shows
    an empty fleet in the preview even where the coordinator would place it.
    Under-claiming is the only safe direction for a gate, and the route says
    so in a note rather than defaulting either to something permissive.
    """
    capabilities = dict(row.get("capabilities") or {})
    capabilities["pools"] = list(row.get("pool_ids") or [])
    return {
        "node_id": row.get("node_id"),
        "capabilities": capabilities,
        "sandbox_capable": row.get("sandbox_capable"),
        "argv_capable": row.get("argv_capable"),
        "unsandboxed_argv_capable": row.get("unsandboxed_argv_capable"),
        "module_capable": row.get("module_capable"),
    }


def _preview_deadline(payload: Mapping[str, Any]) -> float | None:
    """``deadline`` in SECONDS from now, or None. 400 on anything else.

    Seconds rather than a timestamp because that is what the planner takes
    and because a clock skew between a browser and this process would
    otherwise silently move a deadline. Refused rather than clamped: a
    negative or non-numeric deadline is a caller bug, and answering a
    different question than the one asked produces a page whose label and
    contents disagree.
    """
    raw = payload.get("deadline_seconds", payload.get("deadline"))
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise HTTPException(
            status_code=400, detail="deadline must be a number of seconds"
        )
    if not math.isfinite(raw) or raw <= 0:
        raise HTTPException(
            status_code=400, detail="deadline must be a positive number of seconds"
        )
    return float(raw)


def _preview_budget(payload: Mapping[str, Any], key: str) -> float | None:
    raw = payload.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise HTTPException(status_code=400, detail=f"{key} must be a number")
    if not math.isfinite(raw) or raw < 0:
        raise HTTPException(status_code=400, detail=f"{key} must not be negative")
    return float(raw)


# ---------------------------------------------------------------------------
# the cloud app
# ---------------------------------------------------------------------------


def create_cloud_app(
    settings: Settings,
    connect: Callable[[], psycopg.Connection] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    fetch_repo: Callable[..., bytes] | None = None,
    start_federated_job: Callable[..., Any] | None = None,
    mailer: Mailer | None = None,
    github_app: GitHubApp | None = None,
    sandbox_gateway: SandboxGateway | None = None,
    evaluation_driver: Any | None = None,
    placement_eligible: routermod.EligibilityPredicate | None = None,
    expand_tasks: Callable[[str, JobSpec], list[Any]] | None = None,
    capacity_providers: dict[str, ResourceProvider] | None = None,
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
    that it was started — without a live one. ``sandbox_gateway`` and
    ``evaluation_driver`` are the FC Sandbox pair: the gateway is built lazily
    from ``Settings`` on first use (so a deployment with no sandbox configured
    never needs the SDK), and the driver defaults to
    :class:`CoordinatorEvaluationDriver` over the same operator credential
    every other coordinator call uses.

    ``placement_eligible`` and ``expand_tasks`` are the ROUTING seam, and
    they are injected for a reason that is not testability. Both real
    implementations live in ``flashruntime.scheduler`` and
    ``flashruntime.service.modea``, and this repo may import
    ``flashruntime.protocol`` and nothing else — ``tests/test_import_boundary``
    enforces it by name, over function bodies too, so a deferred import would
    not dodge it. Widening that boundary is a decision to record in a commit
    of its own, not one to take inside a route, so this module does not take
    it: with neither injected, ``POST /v1alpha1/jobs/preview-plans`` reports
    that routing is unconfigured and quotes nothing. There is deliberately no
    fallback predicate — a permissive default would be a second, absent copy
    of seven fail-closed placement gates, and a preview built on it would
    show a submitter machines the real scheduler will refuse.

    ``capacity_providers`` maps ``rented_capacity.venue_id`` to the adapter
    that can destroy machines at that venue, and defaults to
    ``capacity.registry.providers_for(settings)`` — which is **empty**, because
    no real adapter exists yet. That is not a stub to be filled in here: an
    empty registry makes the rented-capacity sweep report and never destroy,
    which is the honest behaviour for a deployment that cannot reach any
    venue. Injected only so a test can drive the loop against a fake; see
    ``capacity/registry.py`` for why a fake must never be the default.

    On ``settings.require_auth``: it governs *startup validation of the
    environment*, not whether requests are authenticated. There is no open
    mode here. Checking credentials is the entire reason this service
    exists, so an "auth off" switch would be a single env var that turns the
    security model off in production.
    """
    # The routing seam, resolved through the ONE module allowed to reach into
    # `flashruntime.scheduler` — see `placement.py` and the `SANCTIONED_
    # EXCEPTIONS` entry that permits it (owner decision, 2026-08-11).
    #
    # Injected wins, so a test can still drive the planner with a stub. But an
    # un-injected PRODUCTION app now gets the real seven gates instead of
    # silently degrading to "no plans" — which is what happened before this
    # line existed, and which looked identical to an empty fleet.
    #
    # `placement.py` returns None when the runtime is not importable at all.
    # That stays None here on purpose: the preview route reports it, and
    # nothing substitutes a permissive predicate, because a stand-in that
    # answers True is seven absent safety gates wearing the name of seven
    # present ones.
    placement_eligible = placement_eligible or placementmod.placement_predicate()
    expand_tasks = expand_tasks or placementmod.task_expander()

    connect = connect or (lambda: dbmod.connect(settings))
    coordinator = CoordinatorClient(settings, transport=transport)
    # Its own transport, not the coordinator's: these are two unrelated
    # hosts, and a test fake for one must not have to answer for the other.
    mailer = mailer or Mailer(settings)
    github_app = github_app or GitHubApp(settings)
    fetch_repo = fetch_repo or (
        lambda owner, name, ref, token=None: repomod.fetch_repo_tarball(
            owner, name, ref, token=token
        )
    )
    start_federated_job = start_federated_job or fedavgmod.start_federated_job
    max_upload_bytes = int(
        os.environ.get("FLASHML_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES)
    )

    # -- FC Sandbox --------------------------------------------------------
    #
    # The gateway is built on FIRST USE, not here. `E2BSandboxGateway` imports
    # the e2b SDK lazily and raises `SandboxUnavailable` when the deployment
    # is unconfigured — building it eagerly would make an unconfigured deploy
    # fail at import of a module it does not use, which is exactly the
    # "unconfigured changes nothing" rule this feature is held to. One
    # instance per process, cached: the gateway keeps a connection cache whose
    # whole point is that a second `connect` is not paid.
    _gateway_cache: dict[str, SandboxGateway] = {}
    if sandbox_gateway is not None:
        _gateway_cache["gateway"] = sandbox_gateway

    def sandbox() -> SandboxGateway:
        cached = _gateway_cache.get("gateway")
        if cached is None:
            cached = E2BSandboxGateway.from_settings(settings)
            _gateway_cache["gateway"] = cached
        return cached

    driver = evaluation_driver or CoordinatorEvaluationDriver(coordinator)

    #: Strong references to in-flight background evaluations. `asyncio` keeps
    #: only a weak reference to a task, so one that nothing holds can be
    #: garbage-collected mid-await — a fifteen-minute evaluation silently
    #: cancelled while its sandbox keeps billing. Discarded on completion.
    _background_tasks: set[asyncio.Task] = set()

    # The base URL the sandbox's flashnode enrols against. It is THIS API — a
    # machine token means nothing to the coordinator, and on Render the
    # coordinator is a private service an Alibaba sandbox cannot route to at
    # all. `sandbox_orchestrator.start_session` defaults the argument to
    # `settings.coordinator_url`, which is right for a single-host dev run and
    # wrong for every deployed one, so it is passed explicitly here.
    #
    # Now a real `Settings` field (2026-08-11) rather than a bare environment
    # read: the value is normalised in exactly one place, and the reason it
    # cannot be `coordinator_url` is written down where the next person
    # configuring a deploy will meet it. The fallback is unchanged, so an
    # unset deployment behaves byte-identically to before.
    sandbox_enrolment_url = settings.public_api_url or settings.coordinator_url

    public_limiter = FixedWindowLimiter(
        int(os.environ.get("FLASHML_PUBLIC_RATE_LIMIT", DEFAULT_PUBLIC_RATE_LIMIT)),
        float(
            os.environ.get(
                "FLASHML_PUBLIC_RATE_WINDOW_S", DEFAULT_PUBLIC_RATE_WINDOW_S
            )
        ),
    )
    reconcile_interval_s = float(
        os.environ.get("FLASHML_SANDBOX_RECONCILE_S", DEFAULT_RECONCILE_INTERVAL_S)
    )
    ephemeral_ttl_s = float(
        os.environ.get(
            "FLASHML_EPHEMERAL_MACHINE_TTL_SECONDS",
            DEFAULT_EPHEMERAL_MACHINE_TTL_S,
        )
    )
    ephemeral_reconcile_s = float(
        os.environ.get(
            "FLASHML_EPHEMERAL_MACHINE_RECONCILE_SECONDS",
            DEFAULT_EPHEMERAL_RECONCILE_S,
        )
    )
    rented_reconcile_s = float(
        os.environ.get("FLASHML_RENTED_RECONCILE_S", DEFAULT_RENTED_RECONCILE_S)
    )
    # Empty in production, and see `capacity/registry.py` for why that is the
    # right answer rather than a gap: with no adapter the sweep reports what it
    # would destroy and destroys nothing, which is what an unconfigured
    # deployment should do about a venue it cannot reach.
    capacity_providers = (
        capacityregistrymod.providers_for(settings)
        if capacity_providers is None else capacity_providers
    )
    # `settings.rented_capacity_destroy` is FALSE by default, so the sweep and
    # the settle hook both ship log-only. One flag, read in both places, so a
    # deployment cannot be armed on one path and disarmed on the other — the
    # settle path destroys on the same evidence the sweep does, minutes
    # earlier, and arming half of that is not a safer half.
    rented_destroy = bool(settings.rented_capacity_destroy)

    async def _reconcile_once() -> list[str]:
        """One sweep, on its own connection, never fatal.

        Its own connection because it does not belong to a request: the
        request-scoped one from ``db_conn`` is closed the moment the response
        is sent, and a sweep holding it would be reading a closed socket the
        first time the timer fired between requests.
        """
        conn = await run_in_threadpool(app.state.connect)
        try:
            return await orchmod.reconcile(conn, sandbox(), settings)
        finally:
            await run_in_threadpool(conn.close)

    async def _reconcile_loop() -> None:
        """Sweep at startup and then on a timer, for ever.

        **This is the only backstop against a sandbox billing after a crashed
        controller**, so it runs on BOTH edges: once at startup, because a
        redeploy is exactly the event that abandons a session mid-hibernation
        and the surviving evidence is a database row; and then periodically,
        because a controller can also die between deploys.

        An asyncio task rather than ``fedavg``'s daemon thread — the two are
        different shapes of work. A federated run blocks for hours and would
        occupy an event-loop slot the whole time, so it gets a thread;
        ``reconcile`` is short, already async, and does its blocking database
        work in ``asyncio.to_thread`` internally. There is no scheduler in this
        deployment to hand it to.

        Every failure is swallowed and logged. A sweep that raised would kill
        the task and silently remove the backstop for the life of the process,
        which is worse than any single failed sweep.
        """
        while True:
            try:
                touched = await _reconcile_once()
                if touched:
                    log.info(json.dumps({
                        "text": "sandbox reconciler settled sessions",
                        "sessions": touched,
                    }))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a failed sweep must not end them
                log.warning(
                    json.dumps({"text": "sandbox reconcile sweep failed"}),
                    exc_info=True,
                )
            if reconcile_interval_s <= 0:
                return
            await asyncio.sleep(reconcile_interval_s)

    async def _ephemeral_machine_loop() -> None:
        # Unlike a billing sandbox, a stale rental row is not costing money
        # during this first minute. Delay the first sweep so merely starting
        # the app does not open Postgres before any authenticated work exists;
        # the public agent routes deliberately reject malformed credentials
        # without spending a database connection.
        if ephemeral_reconcile_s > 0:
            await asyncio.sleep(ephemeral_reconcile_s)
        while True:
            conn = None
            try:
                conn = await run_in_threadpool(app.state.connect)
                expired = await run_in_threadpool(
                    dbmod.expire_stale_ephemeral_machines,
                    conn,
                    stale_seconds=ephemeral_ttl_s,
                )
                if expired:
                    log.info(json.dumps({
                        "text": "expired stale ephemeral machines",
                        "machines": expired,
                    }))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one failed sweep must not end it
                log.warning("ephemeral machine sweep failed", exc_info=True)
            finally:
                if conn is not None:
                    await run_in_threadpool(conn.close)
            if ephemeral_reconcile_s <= 0:
                return
            await asyncio.sleep(ephemeral_reconcile_s)

    async def _rented_capacity_loop() -> None:
        """Sweep rented GPUs at startup and then on a timer, for ever.

        **The only thing in this deployment that stops a rented machine
        billing.** `capacity.settle_finished_jobs` runs on the job routes and
        is faster, but it needs somebody to have a page open; this needs
        nobody. A redeploy is exactly the event that abandons a rental
        mid-flight, and the surviving evidence is a `rented_capacity` row.

        BOTH EDGES, following `_reconcile_loop` above and not
        `_ephemeral_machine_loop`. The startup edge is not a nicety here: a
        process that redeploys or crash-loops more often than
        FLASHML_RENTED_RECONCILE_S never reaches its first sweep AT ALL, and
        the abandoned rental this loop exists for is created by exactly that
        event. A crash-looping API with a rented GPU behind it is the shape of
        the unbounded bill.

        It was delayed by one interval for a while because a startup sweep
        opens a Postgres connection before any request arrives, and
        `test_anonymous_traffic_costs_no_database_connection` asserted that
        merely starting the app opened none. That test's own subject is a PER
        REQUEST property — an anonymous flood must not cost connections — and
        one connection at startup, unreachable by any attacker, was never part
        of it. The test now takes its baseline after startup and the sweep has
        its edge back.

        Not gated on any venue being configured. With an empty registry the
        pass is one indexed query that reports what it cannot reach, and that
        report is the only alarm an operator would ever get for a row nothing
        in this process can destroy.

        Every failure is swallowed and logged, as `_ephemeral_machine_loop`
        does: a sweep that raised would kill the task and silently remove the
        backstop for the life of the process — and here that is the difference
        between a bounded bill and an unbounded one.
        """
        while True:
            conn = None
            try:
                conn = await run_in_threadpool(app.state.connect)
                settled = await capacitymod.reconcile_rented(
                    conn, capacity_providers,
                    # Windows deliberately not passed: `capacity/reconcile.py`
                    # owns them and its defaults are the safe ones. The only
                    # number this file contributes is how often to look.
                    dry_run=not rented_destroy,
                )
                if settled:
                    log.info(json.dumps({
                        "text": "rented capacity reconciler settled rentals",
                        "rentals": settled,
                    }))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one failed sweep must not end it
                log.warning("rented capacity sweep failed", exc_info=True)
            finally:
                if conn is not None:
                    await run_in_threadpool(conn.close)
            if rented_reconcile_s <= 0:
                return
            await asyncio.sleep(rented_reconcile_s)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        tasks: list[asyncio.Task] = []
        tasks.append(asyncio.create_task(
            _ephemeral_machine_loop(), name="ephemeral-machine-reconcile"
        ))
        # Unconditional, like the ephemeral loop and unlike the sandbox one:
        # there is no "rented capacity is not configured" state that makes an
        # unreleased row safe to stop looking at. A deployment with no venue
        # adapter reports; a deployment with rows and no adapter needs to be
        # told, every pass, that it is paying for something it cannot destroy.
        rented_task = asyncio.create_task(
            _rented_capacity_loop(), name="rented-capacity-reconcile"
        )
        tasks.append(rented_task)
        _app.state.rented_capacity_reconciler = rented_task
        # Gated on the deployment being configured for sandboxes at all, which
        # is what keeps "unconfigured changes nothing" true of startup as well
        # as of the routes: with no FC configuration there is no sandbox that
        # could be billing, and no gateway to ask.
        if settings.fc_sandbox_configured:
            task = asyncio.create_task(_reconcile_loop(), name="fc-sandbox-reconcile")
            tasks.append(task)
            _app.state.sandbox_reconciler = task
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    app = FastAPI(title="FlashML Cloud API", version="0.2.0", lifespan=lifespan)

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
    # Readable so a deployment can be checked for what it can actually destroy
    # — and so a test can prove the un-injected default is the empty registry
    # rather than a fake that would answer "destroyed" about real machines.
    app.state.capacity_providers = capacity_providers

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
        retry_delays: tuple[float, ...] | None = None,
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
        forward_kwargs: dict[str, Any] = dict(
            on_behalf_of=machine.node_id,
            content=body if body else None,
            query=request.url.query,
            media_type=media_type,
        )
        if retry_delays is None:
            r = await coordinator.forward(request.method, path, **forward_kwargs)
        else:
            # Caller has asserted this path is safe to repeat. Nothing here
            # checks that — `proxy` cannot know — so the assertion lives at
            # the route, next to the reason.
            r = await forward_idempotent(
                coordinator, request.method, path,
                delays=retry_delays, **forward_kwargs,
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
            lifecycle = payload.get("lifecycle", "persistent")
            if lifecycle not in ("persistent", "ephemeral"):
                raise HTTPException(status_code=400, detail="invalid lifecycle")
            started = enrolment.start_device_code(
                db,
                node_id,
                _opt_str(payload.get("hostname")),
                _opt_str(payload.get("platform")),
                lifecycle=lifecycle,
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

    @app.post("/v1alpha1/jobs/preview-plans", tags=["browser"])
    async def preview_plans(
        request: Request,
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Three ways to run this job, priced and timed. **Nothing is
        submitted, matched, held or charged.**

        This is the comparison view — the surface where owned capacity, a
        teammate's idle machine and the open market appear on one page in
        their own currencies. It is read-only by construction, not by
        convention: the only writes reachable from here are none, and that is
        what makes it safe to show somebody before they have decided anything.

        **Body.** ``{"job_id": "..."}`` for a job this account already
        submitted, or ``{"spec": {...}}`` for a JobSpec it has not. Optional:
        ``deadline`` (seconds from now), ``budget_zc``, ``budget_usd``.

        **Response.** ``plans`` (cheapest / balanced / fastest — balanced only
        when a deadline was given, because with nothing to balance against it
        would be "cheapest" shown twice), ``candidates`` (the fleet, each with
        its venue, price, class and record), and ``canary`` (the probe that
        converts "we cannot predict this" into a measurement, or null).

        **Every figure carries its ``basis`` and its ``n``**, and anything not
        derivable is ``null`` — *not observed*, never 0. A plan's basis is the
        WEAKEST behind any machine it allocates to.

        **ZC and USD settlement totals remain side by side.** Under the fixed
        1 ZC = 1 USD testing-credit policy, each cost also carries
        ``total_usd_value`` for comparison. ``within_budget`` stays per source
        currency so the response still distinguishes wallet debits from
        external-provider charges.

        **Gates before price, always**, and the gate is the runtime's own —
        injected, never reimplemented (see ``create_cloud_app``). With no
        predicate configured this route quotes nothing and says so rather
        than falling back to a permissive one: a preview that shows machines
        the scheduler will refuse is worse than no preview.

        Evidence is rung 1 only — other machines' recorded durations on THIS
        job. Rungs 2 and 3 (the same task SHAPE elsewhere) have no producer in
        this schema: nothing records what shape a task was, so there is no key
        to group by, and inventing one from a job name would pool unrelated
        work. A job with no peer durations yet is therefore ``not observed``
        and answered with a canary, which is the honest answer at 29 credited
        tasks in the whole ledger.
        """
        payload = await _json_object(request)
        deadline_seconds = _preview_deadline(payload)
        budget_zc = _preview_budget(payload, "budget_zc")
        budget_usd = _preview_budget(payload, "budget_usd")

        job_id = payload.get("job_id")
        raw_spec = payload.get("spec")
        if job_id is not None:
            if not isinstance(job_id, str) or not job_id:
                raise HTTPException(status_code=400, detail="job_id must be a string")
            row = dbmod.fetch_job_for_owner(db, job_id, user_id)
            if row is None:
                raise HTTPException(status_code=404, detail="unknown job")
            raw_spec = row.get("spec")
            if not isinstance(raw_spec, dict):
                raise HTTPException(
                    status_code=409,
                    detail="this job has no stored spec to plan against",
                )
        elif not isinstance(raw_spec, dict):
            raise HTTPException(
                status_code=400, detail="pass either job_id or spec"
            )

        notes: list[str] = []

        def _degraded(reason: str) -> dict[str, Any]:
            """Answer the question that can be answered, and say which one
            could not. A 200 with empty plans and a reason, rather than a 500
            or a silent empty page: "nothing is quotable here, and here is
            why" is actionable; a stack trace on a comparison view is not."""
            return {
                "job_id": job_id,
                "tasks": None,
                "duration": None,
                "plans": [],
                "candidates": [],
                "canary": None,
                "recommended": None,
                "eligible_machines": 0,
                "excluded_machines": 0,
                "unplannable_machines": 0,
                "notes": notes + [reason],
            }

        if placement_eligible is None or expand_tasks is None:
            return _degraded(
                "routing is not configured on this deployment: the placement "
                "gates and the task expansion both live in the runtime, and "
                "this API imports only its protocol package. Nothing is "
                "quoted rather than quoting a fleet a permissive stand-in "
                "would have approved."
            )

        try:
            spec = JobSpec.model_validate(raw_spec)
        except Exception:
            raise HTTPException(status_code=400, detail="invalid job spec") from None

        try:
            tasks = list(expand_tasks(job_id or "preview", spec))
        except Exception:
            log.warning("could not expand a spec for preview by %s", user_id)
            return _degraded(
                "this spec could not be expanded into tasks, so there is "
                "nothing to plan"
            )
        if not tasks:
            return _degraded("this spec expands to no tasks")
        if len(tasks) > _PREVIEW_MAX_TASKS:
            notes.append(
                f"planned from the first {_PREVIEW_MAX_TASKS} of "
                f"{len(tasks)} tasks"
            )
            tasks = tasks[:_PREVIEW_MAX_TASKS]

        # The gates are per task, and the tasks of one job differ only in
        # their payload's trial parameters — except for a verification pair's
        # `exclude_nodes`, which narrows the fleet for one task and not the
        # job. The first task is the representative and the fleet below is
        # the job's, not that task's.
        task = tasks[0]

        rows = dbmod.router_candidates_for_owner(db, user_id)
        if not rows:
            return _degraded(
                "no active machine is available to this account: nothing is "
                "enrolled, nothing is shared by a workspace, and nothing is "
                "listed on the open market"
            )

        machine_ids = [row["machine_id"] for row in rows]
        rates = metricsmod.acceptance_rates(
            dbmod.acceptance_rate_rows(db, machine_ids=machine_ids)
        )

        # Rung 1: other machines' recorded durations on this job, each
        # labelled with the class of the machine that produced it. Passed
        # whole to the estimator, which drops every observation from a class
        # other than the one it is asked about — the filtering rule stays in
        # one place rather than being re-implemented per class here.
        observations = tuple(
            routermod.Observation(
                seconds=item["duration_s"],
                capability_class=item["capability_class"],
                federated=item["federated"],
            )
            for item in (
                dbmod.peer_task_observations(db, job_id=job_id) if job_id else []
            )
        )
        evidence = [
            routermod.Evidence(rung=routermod.RUNG_SAME_JOB, observations=observations)
        ]

        estimates: dict[str, routermod.Estimate] = {}
        for capability_class in {
            routermod.hardware_class(row["capabilities"]) for row in rows
        }:
            if capability_class is None:
                continue
            estimate = routermod.estimate_task_seconds(
                evidence, capability_class=capability_class
            )
            if estimate is not None:
                estimates[capability_class] = estimate

        candidates: list[routermod.Candidate] = []
        estimates_by_machine: dict[str, routermod.Estimate] = {}
        classes_present: set[str | None] = set()
        for row in rows:
            capability_class = routermod.hardware_class(row["capabilities"])
            classes_present.add(capability_class)
            estimate = estimates.get(capability_class) if capability_class else None
            if estimate is not None:
                estimates_by_machine[row["machine_id"]] = estimate
            candidates.append(
                routermod.Candidate(
                    machine_id=row["machine_id"],
                    node=_preview_node_view(row),
                    venue=row["venue"],
                    currency=routermod.CURRENCY_ZC,
                    # Millicredits on the wire, credits in a quote: the ledger
                    # settles in integers so it can never round, and a page
                    # showing "14200" where the design says "14.2 ZC" is the
                    # same number in a unit nobody reads.
                    price_per_hour=(
                        row["ask_zc_per_hour"]
                        / marketplacemod.MILLICREDITS_PER_CREDIT
                    ),
                    max_concurrent_tasks=row["max_concurrent_tasks"],
                    seconds_per_task=(
                        routermod.planning_seconds(estimate)
                        if estimate is not None
                        else None
                    ),
                    reliability_tier=routermod.reliability_tier(
                        routermod.select_acceptance(
                            rates,
                            machine_id=row["machine_id"],
                            capability_class=capability_class,
                        )
                    ),
                    capability_class=capability_class,
                )
            )

        # The job-wide fallback duration is offered ONLY when every candidate
        # sits in the one class that has evidence. Otherwise it is None, and
        # each machine is quoted from its own class or not at all: a fallback
        # applied across classes is exactly the pooling the estimator refuses
        # everywhere else, arriving through the back door of a default.
        duration: routermod.Estimate | None = None
        if len(classes_present) == 1:
            only = next(iter(classes_present))
            duration = estimates.get(only) if only is not None else None

        eligible_ids = {
            candidate.machine_id
            for candidate in routermod.eligible_fleet(
                task, candidates, eligible=placement_eligible
            )
        }
        # What KIND of work is this, and therefore which venues can do it?
        #
        # Without this the planner narrows on nothing and prices every machine
        # as if any of them could run any job — which is how a GPU fine-tune
        # gets quoted on a 2 vCPU CPU sandbox. `kind=None` is a deliberate
        # no-op inside the planner, so before this line venue fit was built,
        # tested, and unreachable.
        #
        # `raw_spec` rather than the validated model: `signals_from_job_spec`
        # reads a compiled JobSpec mapping, which is what this route holds. It
        # never sees the flashml.yaml those were compiled from.
        #
        # `task_count` is passed explicitly because we have just run the
        # runtime's own `expand_tasks` and know the real number. Re-deriving it
        # inside the classifier would be a second copy of the coordinator's
        # expansion rule, and the two would eventually disagree.
        kind, kind_evidence = routermod.classify(raw_spec, task_count=len(tasks))
        plan_set = routermod.plan_job(
            routermod.PlanRequest(
                task=task,
                tasks=len(tasks),
                candidates=tuple(candidates),
                duration=duration,
                deadline_seconds=deadline_seconds,
                kind=kind,
                kind_evidence=kind_evidence,
                # `resources.gpuPerTask` is the hardware refusal: a venue with
                # no GPU is not eligible for a job that needs one, whatever it
                # costs. Read off the validated model rather than `raw_spec`
                # so a missing or malformed value becomes the schema default
                # (0 = no GPU required) instead of a KeyError on a read-only
                # preview route.
                gpus_per_task=getattr(spec.spec.resources, "gpuPerTask", 0) or None,
            ),
            eligible=placement_eligible,
        )

        if task.payload.get("local_inputs"):
            notes.append(
                "this job wants host-local datasets, and this API does not "
                "record which datasets a machine holds — that gate fails "
                "closed here, so the fleet below is narrower than the "
                "coordinator's will be"
            )
        if task.payload.get("extra_dependencies"):
            notes.append(
                "this job declares extra dependencies, and this API does not "
                "record which machines can install them — that gate fails "
                "closed here, so the fleet below is narrower than the "
                "coordinator's will be"
            )

        return {
            "job_id": job_id,
            "tasks": len(tasks),
            # WHAT KIND OF WORK, AND WHY — never the enum alone.
            #
            # "hpo" tells a reader nothing they can check. "hpo, because the
            # spec expands to 40 independent trials over 2 sweep axes" is a
            # sentence they can hold against the job they submitted, and
            # disagree with if it is wrong. A classifier that cannot be
            # audited from its own output is one nobody will trust the day it
            # routes something surprisingly.
            "kind": plan_set.kind.value if plan_set.kind else None,
            "kind_evidence": plan_set.kind_evidence,
            # Every venue, INCLUDING the refused ones, each with its reason.
            #
            # A surface that lists only usable venues cannot answer "why isn't
            # RunPod here?" — and the two refusals mean opposite things:
            # `suited=False` is "this venue physically cannot run this work",
            # `acquirable=False` is "we cannot get capacity there yet". The
            # second is a roadmap item and the first is a fact about hardware.
            # Collapsing them would let the UI imply we chose not to use a
            # venue we simply cannot reach.
            "venues": [
                {
                    "id": fit.venue.id,
                    "display": fit.venue.display,
                    "currency": fit.venue.currency,
                    "suited": fit.suited,
                    "acquirable": fit.acquirable,
                    "usable": fit.usable,
                    "acquisition": fit.venue.acquisition,
                    "reason": fit.reason,
                }
                for fit in (plan_set.venue_fits or ())
            ],
            "venue_excluded_machines": plan_set.venue_excluded_machines,
            "duration": _preview_estimate(plan_set.duration),
            "plans": [
                _preview_plan(
                    plan,
                    estimates_by_machine=estimates_by_machine,
                    recommended=plan_set.recommended,
                    budget_zc=budget_zc,
                    budget_usd=budget_usd,
                )
                for plan in plan_set.plans()
            ],
            "candidates": [
                {
                    "machine_id": candidate.machine_id,
                    "name": row["name"],
                    "venue": candidate.venue,
                    "currency": candidate.currency,
                    "price_per_hour": candidate.price_per_hour,
                    "price_zc_per_hour": candidate.price_per_hour,
                    "price_usd_per_hour": candidate.price_per_hour,
                    "price_label": pricesmod.zc_ask_price_label(
                        row["ask_zc_per_hour"]
                    ),
                    "usd_equivalent_label": (
                        pricesmod.zc_ask_usd_equivalent_label(
                            row["ask_zc_per_hour"]
                        )
                    ),
                    "listing_id": row["listing_id"],
                    "capability_class": candidate.capability_class,
                    "reliability_tier": candidate.reliability_tier,
                    "acceptance_rate": (rate or {}).get("acceptance_rate"),
                    "n": (rate or {}).get("resolved"),
                    "max_concurrent_tasks": candidate.max_concurrent_tasks,
                    "seconds_per_task": candidate.seconds_per_task,
                    "basis": (
                        estimates_by_machine[candidate.machine_id].basis
                        if candidate.machine_id in estimates_by_machine
                        else None
                    ),
                    "eligible": candidate.machine_id in eligible_ids,
                }
                for candidate, row in zip(candidates, rows)
                for rate in (
                    routermod.select_acceptance(
                        rates,
                        machine_id=candidate.machine_id,
                        capability_class=candidate.capability_class,
                    ),
                )
            ],
            "canary": _preview_canary(plan_set.canary),
            "recommended": plan_set.recommended,
            "eligible_machines": plan_set.eligible_machines,
            "excluded_machines": plan_set.excluded_machines,
            "unplannable_machines": plan_set.unplannable_machines,
            "notes": notes + list(plan_set.notes),
        }

    # -- GitHub App: connecting an installation ------------------------------
    #
    # The whole security argument sits in `POST /installations`. Read its
    # docstring before changing anything here.

    @app.post("/v1alpha1/github/install-url", tags=["browser"])
    async def github_install_url(
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Where to send this user to install the App, with a state bound
        to them."""
        if not github_app.configured:
            # 404, not 500: on a deployment with no App this route does not
            # exist as a capability, and the console asks `configured` first
            # precisely so it never gets here.
            raise HTTPException(
                status_code=404, detail="GitHub is not configured on this deployment"
            )

        state = f"st_{secrets.token_urlsafe(32)}"
        expires_at = datetime.now(timezone.utc) + GITHUB_STATE_TTL
        await run_in_threadpool(
            dbmod.insert_github_install_state, db, state, user_id, expires_at
        )
        return {"url": github_app.install_url(state)}

    @app.get("/v1alpha1/github/installations", tags=["browser"])
    async def list_github_installations(
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """This user's connections, and whether connecting is possible here.

        `configured` is what the console reads to decide whether to render a
        Connect button at all — offering one on a deployment with no App
        walks somebody to a dead end.
        """
        rows = await run_in_threadpool(
            dbmod.list_github_installations, db, user_id
        )
        return {
            "configured": github_app.configured,
            "installations": [
                {
                    "installation_id": row["installation_id"],
                    "account_login": row["account_login"],
                    "account_type": row["account_type"],
                    "repository_selection": row["repository_selection"],
                    "created_at": row["created_at"].isoformat(),
                }
                for row in rows
            ],
        }

    @app.post("/v1alpha1/github/installations", status_code=201, tags=["browser"])
    async def connect_github_installation(
        request: Request,
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Bind an installation to this account.

        **Why the state check comes first and is not optional.** An
        `installation_id` is not a secret — GitHub puts it in its own URLs
        and in the redirect that lands here. If this route bound whatever id
        it was handed, anyone who learned an id could attach another
        organisation's installation to their own account and read all of its
        private source. The state is minted by `install-url` against this
        user, is single-use, and expires; claiming it proves the person
        finishing the flow is the person who started it, in this session.

        GitHub only permits an install on an account the person administers.
        State proves they started it. Together, the binding user administers
        the account being bound.

        The order matters too: the state is claimed BEFORE GitHub is asked
        anything, so a caller spraying installation ids cannot use this route
        to probe which ones exist.
        """
        payload = await _json_object(request)

        raw_id = payload.get("installation_id")
        try:
            installation_id = int(raw_id)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail="installation_id must be an integer"
            ) from None

        state = _opt_str(payload.get("state"))
        if not state:
            raise HTTPException(status_code=400, detail="state is required")

        claimed = await run_in_threadpool(
            dbmod.claim_github_install_state, db, state, user_id
        )
        if not claimed:
            # One answer for expired, replayed, unknown, and belonging to
            # somebody else. Distinguishing them would tell a prober which
            # states exist.
            raise HTTPException(
                status_code=403,
                detail="this GitHub install link is not valid for your "
                       "account, or has already been used",
            )

        try:
            details = await github_app.installation_details(installation_id)
        except GitHubAppError as exc:
            if exc.kind == "misconfigured":
                raise HTTPException(
                    status_code=502,
                    detail="this deployment's GitHub App is misconfigured",
                ) from None
            raise HTTPException(
                status_code=400,
                detail="GitHub does not recognise that installation",
            ) from None

        await run_in_threadpool(
            dbmod.insert_github_installation,
            db,
            installation_id=installation_id,
            user_id=user_id,
            account_login=details["account_login"],
            account_type=details["account_type"],
            repository_selection=details["repository_selection"],
        )
        return {
            "installation_id": installation_id,
            "account_login": details["account_login"],
        }

    @app.delete(
        "/v1alpha1/github/installations/{installation_id}",
        status_code=204,
        tags=["browser"],
    )
    async def disconnect_github_installation(
        installation_id: int,
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Forget this user's connection.

        Deliberately does NOT uninstall the App on GitHub: one installation
        is shared by every colleague who connected it, so uninstalling would
        disconnect people who never asked, and it is the account admin's
        call rather than a job console's.
        """
        removed = await run_in_threadpool(
            dbmod.delete_github_installation, db, user_id, installation_id
        )
        if not removed:
            raise HTTPException(status_code=404, detail="unknown installation")
        return Response(status_code=204)

    @app.post("/v1alpha1/jobs/from-repo", status_code=201, tags=["browser"])
    async def submit_job_from_repo(
        request: Request,
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Paste a GitHub repo, get a job — or get told exactly what is
        wrong with it, all of it, in one answer.

        The order below is the whole point of the endpoint. Fetch, extract,
        parse, **preflight**, resolve and **admit any declared dataset**, and
        only then touch the coordinator: a repo with an error finding — or a
        dataset that is gated, unreachable, or too large for any single host
        in the Crew — must not upload an artifact, must not submit, and must
        not leave a ``jobs`` row behind. Findings come back *together*, never
        one per round trip, because a user fixing four problems should need
        one more submit, not four.
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

        # A GitHub App installation token, but only for an owner THIS user
        # has connected. Resolved before the fetch and scoped by both
        # user_id and owner: connecting `acme` must not quietly authenticate
        # a fetch of `someone-else/...`, and Alice connecting `acme` must not
        # let Bob read `acme` through our App.
        repo_token = await _installation_token_for(db, github_app, user_id, owner)

        with tempfile.TemporaryDirectory(prefix="flashml-repo-") as tmpdir:
            dest = Path(tmpdir) / "src"
            try:
                # Blocking network + tar work, off the event loop.
                tar_bytes, repo_root = await run_in_threadpool(
                    _fetch_and_extract,
                    fetch_repo,
                    owner,
                    name,
                    ref,
                    dest,
                    repo_token,
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

        # Declared datasets are resolved HERE — after preflight, before
        # anything is staged and before the coordinator is asked for
        # anything. A `source:` that turns out to be gated, private,
        # renamed or simply unreachable is a 400 in the console a second
        # after the submit, and it leaves no artifact, no coordinator
        # request and no jobs row behind, exactly like an error finding.
        #
        # No dataset byte passes through this process. What is fetched is a
        # file LISTING — sizes, checksums and a pinned revision — and what
        # is handed on is a list of URLs the host resolves itself.
        manifests: dict[str, dsmod.Manifest] = {}
        if config.datasets:
            async with httpx.AsyncClient() as http:
                try:
                    for declared in config.datasets:
                        manifests[declared["name"]] = await dsmod.resolve(
                            declared, http=http
                        )
                except dsmod.DatasetResolveError as exc:
                    # `safe_text`, not `str`: the message quotes a `source:`
                    # the submitter typed, and an origin's own error text
                    # travels inside it.
                    raise HTTPException(
                        status_code=400, detail=safe_text(exc, 500)
                    ) from None

        # How this run's rounds are cut, decided once here from the machines
        # online right now — never by the submitter, who cannot see the Crew,
        # and never re-counted per round, which would move the chunk layout
        # underneath a resumed run. Hoisted out of the compile below because
        # the dataset admission check needs the same width the compiler will
        # cut against; `None` for a job that has no rounds.
        fleet = (
            fleet_shape(dbmod.count_online_machines(db, pool_id=pool))
            if config.is_federated
            else None
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
            # `fleet is not None` is exactly `config.is_federated` — see
            # where it is computed. Spelled this way so the narrowing is
            # visible to a reader (and a type checker) at the two `fleet.`
            # accesses below.
            if fleet is not None:
                spec = compile_federated_round(
                    config, image, code_uri, config.name,
                    round_index=0, weights_uri=None,
                    slot_chunks=fedavgmod.slot_chunks_for(
                        fleet, 0, float(config.sync_every or 1.0)
                    ),
                    total_chunks=fleet.total_chunks,
                    pool=pool,
                    manifests=manifests,
                )
            else:
                spec = compile_to_jobspec(
                    config, image, code_uri, config.name, pool=pool,
                    manifests=manifests,
                )
        except CompileError as exc:
            raise HTTPException(status_code=400, detail=safe_text(exc, 500)) from None

        if manifests:
            refusal = _admit_datasets(
                db, spec=spec, manifests=manifests,
                fleet=fleet, pool=pool, findings=rendered,
            )
            if refusal is not None:
                # Same three guarantees as the preflight refusal above: no
                # artifact staged, no coordinator request, no jobs row.
                # Everything up to this point has been reads.
                return refusal

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
                    manifests=manifests,
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
        finished = [j for j in visible if is_terminal_state(j.get("state"))]
        dbmod.sync_observed_job_states(
            db, [(j["job_id"], str(j["state"])) for j in finished],
        )
        # THE SETTLE HOOK, list half. A rented machine keeps billing after the
        # job it was rented for ends — flashnode goes on heartbeating, so no
        # liveness check can notice — and this is one of the two places this
        # API ever learns a job stopped. One batched query for the whole page,
        # for the same reason the state sync above is batched: this route is
        # polled every two seconds.
        #
        # It is an OPTIMISATION, not the guarantee. Nobody has to open this
        # page; `capacity.reconcile`'s JOB_FINISHED and IDLE branches are what
        # stop the money when nobody does.
        await capacitysettlemod.settle_finished_jobs(
            db, capacity_providers,
            job_ids=[j["job_id"] for j in finished],
            dry_run=not rented_destroy,
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
        # The three are separate on purpose. Recording the STATE is a cheap
        # local write and is what `GET /me/metrics` counts outcomes from,
        # so it happens on every terminal observation and does not care
        # whether the measurement succeeded. Recording the FOOTPRINT costs a
        # coordinator round trip and is guarded by its own marker, so a
        # listing that failed is retried on the next poll instead of being
        # remembered as a measurement. MIRRORING the artifacts to OSS costs
        # a copy of every accepted object and is guarded by a THIRD marker
        # (`artifacts_mirrored_at`, migration 0016) for the same reason
        # squared: it is the one of the three that writes to a system
        # outside this deployment, so it is the one most able to fail after
        # its neighbours have succeeded. Sharing 0011's marker would leave
        # such a failure permanently unretried — 0016's comment is the long
        # version.
        #
        # THIS IS THE ONLY PLACE A NON-FEDERATED JOB IS EVER OBSERVED TO
        # HAVE STOPPED, which is exactly why the mirror hangs here and not
        # on the commit path: a volunteer's commit is accepted by the
        # coordinator with no knowledge of this API, and that is the
        # property that keeps running leases alive when this process dies.
        # The copy happens after the fact, on an observation, and cannot
        # turn a finished job into a failed one.
        if is_terminal_state(job.get("state")):
            dbmod.sync_observed_job_states(db, [(job_id, str(job["state"]))])
            if row.get("artifact_bytes_recorded_at") is None:
                await _record_artifact_footprint(coordinator, db, job_id)
            if row.get("artifacts_mirrored_at") is None:
                await _mirror_job_artifacts(coordinator, db, job_id, settings)
            # A FOURTH thing, and the only one that costs money to skip: give
            # back any machine rented for this job. It needs no marker column
            # of its own — a released rental leaves the state the query selects,
            # so a page left polling a finished job re-runs one indexed lookup
            # that finds nothing.
            #
            # Awaited inline rather than backgrounded, deliberately. The
            # request-scoped connection is closed the moment this response is
            # sent, so a background task would need its own; and the cost is
            # paid once, only by a job that actually rented capacity, at the
            # first observation of its end. `settle_finished_jobs` never
            # raises, so this cannot turn a finished job's page into a 500 —
            # the same rule the two hooks above follow.
            await capacitysettlemod.settle_finished_jobs(
                db, capacity_providers, job_ids=[job_id],
                dry_run=not rented_destroy,
            )
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

    @app.get("/v1alpha1/jobs/{job_id}/tasks/{task_id}/checkpoint", tags=["browser"])
    async def get_task_checkpoint(
        job_id: str, task_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """The latest committed checkpoint for a task the caller can see.

        Answers the only question the console can ask about fault tolerance
        while a job is still running: *how much work would this task lose if
        its machine vanished right now?* Checkpointing is on for every job,
        so this is live for all of them.

        **Why this exists next to an identical agent route.** The agent's
        ``.../checkpoints/latest`` is machine-credentialed —
        ``current_machine`` answers 401 to a browser JWT on purpose, the two
        credential kinds never cross over — so the console could not read it
        at all. Same path with a second auth mode would put both kinds on one
        route, which is exactly the mixing this API refuses everywhere else;
        a separate browser path keeps them apart. It is a route wrapper, not
        a protocol change: the coordinator's own ``/latest`` takes no
        credential.

        That last fact is why the visibility check here is load-bearing
        rather than decorative. A checkpoint manifest carries artifact keys,
        and the coordinator will hand it to anyone who asks; ``job_id`` is
        the only thing standing between one account's manifests and another,
        so this route resolves the job through ``fetch_job_for_viewer``
        first, exactly as the sibling task and event routes do.

        **404 is passed through untouched** and means "no valid checkpoint",
        which is not the same as "this workload does not checkpoint" — the
        coordinator cannot tell those apart and neither can this route, so
        neither invents a distinction. The console renders the ambiguity
        rather than guessing.

        Not retried, unlike the agent's copy: this is a 2.5s browser poll,
        and holding a request through a retry ladder would stack polls on a
        coordinator that is already struggling. The next tick is the retry.
        """
        row = dbmod.fetch_job_for_viewer(db, job_id, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown job")

        if fedavgmod.is_federated_job_id(job_id):
            # Checkpoint scope is `<coordinator_job_id>::<task_id>`, and a
            # federated run has one coordinator job PER ROUND — so this
            # umbrella id addresses no scope at all. 409 rather than 404:
            # "there is no checkpoint here" would be indistinguishable from
            # a task that has not committed one yet, and the caller would
            # believe it.
            raise HTTPException(
                status_code=409,
                detail="a federated run checkpoints per round; read the "
                       "round's coordinator job",
            )

        r = await coordinator.forward(
            "GET",
            f"/v1alpha1/jobs/{_seg(job_id)}/tasks/{_seg(task_id)}"
            f"/checkpoints/latest",
        )
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

        **One field is still always null**, and that is the most important
        thing about this route. ``mttd_seconds`` needs the instant a machine
        actually stopped, which nothing in this deployment records;
        ``metrics.report`` documents exactly which event it is waiting for.
        It is null rather than 0 because this page's entire purpose is to
        prove a claim about reliability, and a fabricated MTTD is
        indistinguishable from a measured one. ``lost_task_seconds`` and
        ``mttr_seconds`` became real on 2026-08-11, when migration 0015 gave
        an attempt a terminal outcome — not by relaxing that standard but by
        recording the events it named.

        **The expiry reconciliation runs first, and it is a write on a read
        route.** Lease expiry is decided by the coordinator's sweeper, which
        never calls this API, so an attempt whose machine simply vanished is
        resolved here or nowhere. The same shape as
        ``sync_observed_job_states`` on the jobs list, for the same reason and
        with the same guard: it is one statement over a partial index of
        UNRESOLVED attempts, so after the first pass a polling console costs a
        statement that matches no rows. Best-effort — a reconciliation that
        fails must not take the page down with it, it only means the numbers
        below still carry those attempts as unresolved, which is the honest
        reading of a row nothing has classified.

        Unscoped by owner, deliberately: expiry is a fact about a lease, not
        about whose console is open, and scoping it would make one account's
        numbers depend on another account visiting the site.
        """
        try:
            dbmod.reconcile_expired_attempts(db)
        except Exception:
            log.warning("could not reconcile expired attempts for %s", user_id)
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

    @app.get("/v1alpha1/jobs/{job_id}/artifacts", tags=["browser"])
    async def list_job_artifacts(
        job_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """What a finished job left behind, for the console's Artifacts card.

        THE GAP THIS CLOSES. Until now the console read ``job.artifacts``,
        a field on ``JobRecord`` whose only writer sits on the KubeRay ingest
        path this deployment never takes — so it was ``[]`` for every job this
        product has ever run, and the card was empty for a job whose model was
        sitting on the coordinator's disk the whole time. The bytes were never
        missing; there was no route that would say they were there.

        THE ANSWER COMES FROM THE COORDINATOR, WHICH IS THE ONLY THING THAT
        KNOWS. Not from ``jobs``, which stores a footprint total and no keys,
        and not from the OSS manifest, which describes the *accepted* subset
        and does not exist at all on a deployment with no OSS. One listing, and
        every key and size in the answer came out of it — see
        ``_relative_artifacts`` for what is dropped and what is kept.

        ``storage`` AND ``mirrored_at`` DESCRIBE WHERE THE NEXT DOWNLOAD COMES
        FROM, which is a different question from what exists. ``"oss"`` is
        claimed only when this deployment can actually sign an OSS URL *and*
        this job has a manifest — the same two conditions the download route
        checks, deliberately read the same way, because a card that said "OSS"
        while every download proxied would be describing a system nobody is
        running. With no OSS configured the pair is fixed at
        ``("coordinator", null)`` and this route behaves exactly as it would
        have before the mirror existed.

        A FEDERATED RUN ANSWERS EMPTY, and honestly so. Its parent id names no
        coordinator job — a run is N round jobs the coordinator sees as
        unrelated — so there is nothing to list under ``jobs/{parent}/``, and
        forwarding the parent id would spend a round trip to be told so.
        Fanning out over the rounds, the way ``/events`` and ``/tasks`` do,
        would return keys under a DIFFERENT job's prefix: they would not
        compose with the fetch-by-key route below, so every link the console
        built from them would 404. A known gap, stated rather than papered
        over — a federated run's outputs need a per-round shape both routes
        agree on, which is its own task.
        """
        row = dbmod.fetch_job_for_viewer(db, job_id, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown job")

        # Read before the coordinator is asked anything, off the row already
        # in hand: `oss_configured` is a property, `artifacts_mirrored_at` is
        # a column of the row the visibility check just fetched, so knowing
        # where downloads will come from costs nothing at all.
        mirrored_at = (
            row.get("artifacts_mirrored_at") if settings.oss_configured else None
        )
        answer: dict[str, Any] = {
            "artifacts": [],
            "storage": "oss" if mirrored_at is not None else "coordinator",
            # `_isoformat`, not `str()`: the sibling job routes render a
            # timestamp with `str()` and get `2026-08-11 22:15:00+00:00`,
            # which is not ISO 8601 and lands in the corner of `Date` parsing
            # that is implementation-defined. This field is new, so it can
            # simply be right.
            "mirrored_at": _isoformat(mirrored_at),
        }
        if fedavgmod.is_federated_job_id(job_id):
            return answer

        r = await coordinator.forward(
            "GET", f"/v1alpha1/jobs/{_seg(job_id)}/artifacts"
        )
        if r.status_code >= 300:
            # The coordinator's own answer, unedited. An empty list here would
            # render as "this job produced nothing", which is the one thing a
            # failed listing does not establish — and the console cannot tell
            # the difference from a body it did not receive.
            return _passthrough(r)
        try:
            listing = r.json()
        except ValueError:
            return _passthrough(r)
        if not isinstance(listing, list):
            return _passthrough(r)
        answer["artifacts"] = _relative_artifacts(job_id, listing)
        return answer

    @app.get("/v1alpha1/jobs/{job_id}/artifact-url/{key:path}", tags=["browser"])
    async def get_job_artifact_url(
        job_id: str,
        key: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Where the console should send the browser for ONE artifact.

        THE 401 THIS EXISTS TO REMOVE. The download route below sits on
        ``current_user``, which reads a bearer token from the ``Authorization``
        header and from nothing else. That is correct and stays correct — but
        a browser NAVIGATION sends no such header, so a plain ``<a href>`` to
        it answers 401 every time, and a navigation is the only thing that
        follows the route's 307 into OSS cleanly. Fetching the bytes with the
        header instead only moves the failure: the fetch would follow that same
        307 to a third origin whose response carries no CORS grant of ours, and
        it would pull a multi-gigabyte checkpoint through browser memory on the
        way. So the two facts are separated. This route is an ORDINARY
        authenticated JSON call — same origin as every other call the console
        makes, header and all — and it answers with a URL that needs no header
        of its own.

        WHY A PRESIGNED URL MAY TRAVEL WITHOUT ONE. It is not this API's
        credential and it grants nothing this API grants: it reads exactly one
        object, it expires by itself after ``alibaba_oss.DEFAULT_TTL_S``, and
        it is minted only after the same ``fetch_job_for_viewer`` check every
        other job route runs. Nothing about this weakens ``current_user`` — the
        caller proved who they were to get here.

        ``storage`` IS THE ANSWER, ``url`` IS ONLY SOMETIMES. ``"coordinator"``
        with a null ``url`` is a completely ordinary reply and the console must
        handle it as the normal case, not an error: no OSS configured (the
        deployment default), this job never mirrored, the bucket unwell, or —
        the one that has nothing to do with configuration — **this key is not
        in the manifest**. A task that failed produced no accepted output, so
        hard rule 4 deliberately leaves its ``stderr.txt`` on the coordinator's
        disk while the job as a whole is stamped mirrored. A console that
        assumed a mirrored job means a mirrored key would fail on exactly the
        file somebody opens after a failure. ``_mirrored_artifact_url``
        collapses all four to None on purpose; the caller's fallback is one
        branch, not four.

        WHY ITS OWN PATH SEGMENT AND NOT A SUFFIX ON THE DOWNLOAD ROUTE.
        ``{key:path}`` is greedy and an artifact key is arbitrary, so
        ``…/artifacts/{key:path}/download-url`` would be ambiguous by
        construction: a job that wrote a file named ``download-url`` would find
        its own bytes unreachable, because that URL parses as a URL request for
        the parent directory. A sibling literal segment cannot collide with
        anything, since the segment is matched before any wildcard is
        considered — ``…/artifacts/anything/at/all`` and
        ``…/artifact-url/anything/at/all`` are told apart by ``artifacts`` vs
        ``artifact-url``, one path component earlier than the greedy part.
        """
        row = dbmod.fetch_job_for_viewer(db, job_id, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown job")
        # Built exactly as the download route builds it, one line for one
        # line: the URL this hands back and the bytes that route would serve
        # have to name the same object, and two spellings of the same key is
        # how they would stop doing so.
        full_key = _artifact_key(f"{_seg(job_id)}/{key}")
        coordinator_key = f"jobs/{full_key}"
        signed = await _mirrored_artifact_url(
            job_id, coordinator_key, row, settings,
            content_disposition=_attachment_disposition(key),
        )
        # Named from what actually happened, not from what the job row claims.
        # The listing route reports a job-level `storage`; this reports a
        # key-level one, and the two legitimately disagree for an unaccepted
        # task's output. Saying "oss" here without a URL, or "coordinator"
        # beside one, would each describe a system nobody is running.
        return {
            "storage": "coordinator" if signed is None else "oss",
            "url": signed,
        }

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

        A MIRRORED ARTIFACT IS ANSWERED 307, NOT PROXIED. Once the bytes are
        in OSS there is no reason for them to travel through this process
        again, and a strong reason for them not to — see
        ``_mirrored_artifact_url``, which also lists the four ordinary ways
        this falls back to the proxy below. The URL it returns is a
        single-object grant good for ``alibaba_oss.DEFAULT_TTL_S``; the
        redirect is 307 rather than 302 so the method is preserved verbatim,
        the same reason ``_passthrough`` refuses to normalise a status.

        The visibility check runs BEFORE the redirect, not just before the
        proxy. A presigned URL carries no identity of its own, so minting one
        for a caller who cannot see the job would hand out exactly the access
        the check exists to refuse — and it would do it in a form that keeps
        working after they are removed from the pool.

        WHO CALLS THIS, NOW THAT ``artifact-url`` EXISTS. A signed-in console
        reaches it with a real ``fetch`` and the bearer header, for a key that
        is NOT mirrored — coordinator-only bytes have to come through here,
        there is nowhere else they live, and those files sit on a 5 GB disk so
        pulling one through this process is bounded by construction. The 307 is
        unchanged for every other client (the CLI follows it happily, having no
        CORS to fail); the console simply asks ``artifact-url`` first and never
        needs the redirect. ``Content-Disposition`` is set on both answers, so
        a file saves under the same name whichever way it was fetched.
        """
        row = dbmod.fetch_job_for_viewer(db, job_id, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown job")
        full_key = _artifact_key(f"{_seg(job_id)}/{key}")
        coordinator_key = f"jobs/{full_key}"
        disposition = _attachment_disposition(key)
        signed = await _mirrored_artifact_url(
            job_id, coordinator_key, row, settings,
            content_disposition=disposition,
        )
        if signed is not None:
            return RedirectResponse(signed, status_code=307)
        r = await coordinator.forward("GET", f"/v1alpha1/artifacts/{coordinator_key}")
        # Only on an answer that carries the bytes. Labelling the
        # coordinator's 404 body as an attachment would offer to save the
        # error message as if it were the file.
        if r.status_code >= 300:
            return _passthrough(r)
        return _passthrough(r, headers={"Content-Disposition": disposition})

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
        # THE SETTLE HOOK, third site — see `capacity/settle.py`'s inventory of
        # where a terminal state is observed at all. This one is the strongest
        # evidence of the four: the coordinator was asked directly, just above,
        # rather than a cached column being read. Somebody deleting a finished
        # job's outputs should not leave a rented GPU billing for it, and the
        # cost when there is no rental — the overwhelming majority — is one
        # indexed lookup that matches nothing. Never raises, so it cannot turn
        # a delete into a 500.
        await capacitysettlemod.settle_finished_jobs(
            db, capacity_providers, job_ids=[job_id],
            dry_run=not rented_destroy,
        )

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

        **THE OSS MIRROR GOES WITH THEM.** Freeing the coordinator's disk is
        only half of deleting a job's artifacts once a mirror exists: the
        other copy is in a bucket, it is still listed by a manifest that
        certifies it complete, and ``presign_job_artifacts`` would keep
        minting readable URLs for it after its owner deleted it. That is a
        data-deletion correctness problem, not a tidiness one, so
        ``unmirror_job`` runs for every target below and a bucket that
        refuses answers 502 exactly as a coordinator that refuses does —
        telling someone their data is gone when it is not is the one answer
        this route may never give.

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

        # THE OTHER COPY. Reached only when every target's coordinator-side
        # delete answered, so the disk is already free by here; what is left
        # is the OSS mirror of the same bytes, which nothing else in this
        # system ever deletes.
        #
        # A SECOND LOOP, NOT A LINE INSIDE THE FIRST ONE, and the difference
        # is the retry. The loop above `continue`s on 404 — "the coordinator
        # has no artifacts for this job", which is the second click and,
        # crucially, the round that a PREVIOUS attempt at this route already
        # freed before failing on a later round. Those targets are precisely
        # the ones whose mirror is still there, so an unmirror hung off the
        # success branch would skip exactly the case it exists to fix and
        # orphan a round's copy for ever. Mirror deletion is idempotent (a
        # prefix that is already gone deletes 0 objects, not an error), so
        # running it for every target is free where skipping one is not.
        for coordinator_job_id in targets:
            try:
                await unmirror_job(coordinator_job_id, settings)
            except MirrorError as exc:
                # NOT swallowed, unlike every other mirror call in this file.
                # Everywhere else a failed mirror costs a retry and the bytes
                # are safe on the coordinator's disk meanwhile; here the
                # coordinator's copy is ALREADY GONE, so reporting success
                # would tell an owner their artifacts are deleted while a
                # presignable copy of them sits in a bucket. Nothing is
                # recorded, matching the partial-failure rule above: the
                # usage stays over-stated and the whole call is safe to retry.
                log.warning(
                    json.dumps({"text": "could not delete the OSS mirror",
                                "job_id": coordinator_job_id,
                                "error": str(exc)})
                )
                raise HTTPException(
                    status_code=502,
                    detail="this job's files were deleted but its mirrored "
                           "copy could not be, so its recorded usage is "
                           "unchanged — try again",
                )

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

    # -- FC Sandbox sessions ------------------------------------------------
    #
    # `sandbox_orchestrator` is the reducer; these are the six authenticated
    # doors onto it plus one unauthenticated evidence page.
    #
    # TWO RULES GOVERN EVERY ROUTE BELOW.
    #
    # 1. OWNER SCOPING IS DONE HERE, FIRST, ALWAYS. `on_model_ready` and
    #    `cleanup_session` take a BARE session id — deliberately, because the
    #    reconciler acts for the deployment rather than for a person, and
    #    `unfinished_sessions` is not owner-scoped either. That means the
    #    orchestrator cannot tell whose session it is being handed, and the
    #    only thing standing between two accounts is that every route here
    #    calls `fetch_session_for_owner` and 404s on None BEFORE the
    #    orchestrator is touched. A route that skipped it would let anyone
    #    holding a session id kill or resume somebody else's sandbox.
    #
    # 2. UNCONFIGURED IS 404, IN THE GITHUB-APP SHAPE. On a deployment with no
    #    FC configuration this capability does not exist, so it answers as
    #    though the routes do not — same reasoning as `github_install_url`.
    #    Nothing else in the API changes shape.

    def _sandbox_or_404() -> None:
        if not settings.fc_sandbox_configured:
            raise HTTPException(
                status_code=404,
                detail="the FC sandbox is not configured on this deployment",
            )

    def _sandbox_http_error(exc: Exception) -> HTTPException:
        """The orchestrator's errors, mapped once.

        `TrainingJobNotAuthorised` and `SessionNotFound` are both 404 and both
        say nothing more: the first already folds "no such job" and "somebody
        else's job" into one exception on purpose (the repo's 404-not-403
        doctrine), and splitting them at the HTTP layer would undo that.

        `SessionFailed` is 502 carrying `{"session_id", "code"}` rather than a
        bare message, because the session it names has a row, a ledger and a
        share link, and those are the evidence of what went wrong — the console
        links straight to them. The sanitized `code` travels; the free-text
        detail does not.
        """
        if isinstance(exc, orchmod.SandboxUnconfigured):
            return HTTPException(
                status_code=404,
                detail="the FC sandbox is not configured on this deployment",
            )
        if isinstance(exc, (orchmod.TrainingJobNotAuthorised,
                            orchmod.SessionNotFound)):
            return HTTPException(status_code=404, detail="unknown session")
        if isinstance(exc, OSSUnavailable):
            return HTTPException(
                status_code=503,
                detail="the artifact store is unavailable, so the trained "
                       "model cannot be reached",
            )
        if isinstance(exc, orchmod.SessionFailed):
            return HTTPException(
                status_code=502,
                detail={"session_id": exc.session_id, "code": exc.code},
            )
        if isinstance(exc, orchmod.EvaluationUnavailable):
            return HTTPException(
                status_code=500, detail="no evaluation driver is configured"
            )
        if isinstance(exc, SandboxUnavailable):
            return HTTPException(
                status_code=404,
                detail="the FC sandbox is not configured on this deployment",
            )
        raise exc

    async def _session_for_owner_or_404(
        db: psycopg.Connection, session_id: str, user_id: str
    ) -> dict[str, Any]:
        row = await run_in_threadpool(
            ssmod.fetch_session_for_owner, db, session_id, user_id
        )
        if row is None:
            # Not 403. These ids sit in shareable URLs, and "exists but not
            # yours" confirms to a guesser that the id is real.
            raise HTTPException(status_code=404, detail="unknown session")
        return row

    # -- Marketplace: credits, ledger, listings, matches, prices ------------
    #
    # The HTTP surface over marketplace.py and prices.py (plan
    # 2026-08-12-console-ui-plan.md §3.1). The repository had 105 tests and
    # zero routes; the console's marketplace section reads only these.
    # Same doctrine as every browser route here: current_user (admitted_user
    # for writes), 404 never 403 on resource ids, _jsonable on rows.

    @app.get("/v1alpha1/credit-requests", tags=["browser"])
    async def get_credit_requests(
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        return [
            _jsonable(row)
            for row in marketplacemod.list_credit_requests(db, user_id)
        ]

    @app.post("/v1alpha1/credit-requests", status_code=201, tags=["browser"])
    async def create_credit_request(
        request: Request,
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        payload = await _json_object(request)
        try:
            created = marketplacemod.create_credit_request(
                db,
                user_id,
                requested_zc=payload.get("requested_zc"),
                purpose=payload.get("purpose"),
            )
        except marketplacemod.InvalidCreditRequest as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except marketplacemod.PendingCreditRequestExists as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return _jsonable(created)

    @app.get("/v1alpha1/admin/credit-requests", tags=["admin"])
    async def get_admin_credit_requests(
        status: str = "pending",
        _admin: str = Depends(admin_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        try:
            requests = marketplacemod.list_admin_credit_requests(db, status=status)
        except marketplacemod.InvalidCreditRequest as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return [_jsonable(row) for row in requests]

    @app.post("/v1alpha1/admin/credit-requests/{request_id}/approve", tags=["admin"])
    async def approve_credit_request(
        request_id: str,
        request: Request,
        admin_id: str = Depends(admin_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        payload = await _json_object(request)
        try:
            decided = marketplacemod.approve_credit_request(
                db,
                request_id,
                admin_id=admin_id,
                approved_zc=payload.get("approved_zc"),
            )
        except marketplacemod.InvalidCreditRequest as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except marketplacemod.CreditRequestNotPending as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return _jsonable(decided)

    @app.post("/v1alpha1/admin/credit-requests/{request_id}/decline", tags=["admin"])
    async def decline_credit_request(
        request_id: str,
        admin_id: str = Depends(admin_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        try:
            decided = marketplacemod.decline_credit_request(
                db, request_id, admin_id=admin_id
            )
        except marketplacemod.CreditRequestNotPending as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return _jsonable(decided)

    @app.get("/v1alpha1/credits", tags=["browser"])
    async def get_credits(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """This account's balance, spendable vs held, in millicredits.

        The one-time grant rides along: ``grant_starting_credits`` is a
        no-op from its second call on — the unique index says so, not the
        caller — so the first read opens the account and every later read
        is correct without depending on some other hop having run first.
        """
        marketplacemod.grant_starting_credits(db, user_id)
        found = marketplacemod.balances(db, user_id)
        spendable_usd = Decimal(found["spendable"]) / Decimal(1000)
        held_usd = Decimal(found["escrow"]) / Decimal(1000)
        return {
            "spendable_zc": found["spendable"],
            "held_zc": found["escrow"],
            "usd_per_zc": format(Decimal(1), ".2f"),
            "spendable_usd": format(spendable_usd, ".2f"),
            "held_usd": format(held_usd, ".2f"),
            # Lifetime sums read out of the ledger, for the wallet's
            # earned/spent tiles. True zeros for a fresh account, labelled
            # "lifetime" on the client so 0 reads as "nothing yet".
            "lifetime": marketplacemod.lifetime_for_owner(db, user_id),
        }

    @app.get("/v1alpha1/credits/ledger", tags=["browser"])
    async def get_credits_ledger(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
        limit: int = Query(50, ge=1, le=200),
        before: int | None = Query(None, ge=0),
    ):
        """Movements, newest first, each with ALL of its legs.

        The counterparty leg travels with the viewer's own — a feed that
        collapses a movement into "balance went up" deletes the
        counterparty, and the counterparty is the point of a double-entry
        ledger. ``next_before`` is the cursor for the next page, present
        only when this page was full.
        """
        movements = marketplacemod.ledger_movements_for_owner(
            db, user_id, limit=limit, before=before
        )
        return {
            "movements": [_jsonable(m) for m in movements],
            "next_before": (
                movements[-1]["cursor"] if len(movements) == limit else None
            ),
        }

    @app.get("/v1alpha1/market/listings", tags=["browser"])
    async def list_market_listings(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """The open ask side of every book, plus this host's own listings.

        ``asks`` is what a buyer shops: class, ask, and the host's record
        in that class — ``acceptance_rate`` null for an unproven host,
        never an invented number. ``mine`` is what a host manages,
        including paused and withdrawn rows the open book hides.
        """
        rate_rows = metricsmod.acceptance_rates(
            dbmod.acceptance_rate_rows(db, machine_ids=None)
        )
        rate_by_key = {
            (str(row["machine_id"]), str(row["capability_class"])): row
            for row in rate_rows
        }
        raw_asks: list[tuple[str, Any, Any]] = []
        for klass in marketplacemod.CAPABILITY_CLASSES:
            for ask in marketplacemod.open_asks(
                db,
                klass,
                acceptance_rates={
                    key: row["acceptance_rate"]
                    for key, row in rate_by_key.items()
                },
            ):
                raw_asks.append(
                    (klass, ask, rate_by_key.get((ask.machine_id, klass)))
                )
        machine_by_id = marketplacemod.machines_for_ids(
            db, {ask.machine_id for _, ask, _ in raw_asks}
        )
        asks: list[dict[str, Any]] = []
        for klass, ask, record in raw_asks:
            machine = machine_by_id.get(ask.machine_id)
            # The effective price is ask/rate, the number a buyer actually
            # pays per accepted hour. ``effective_price`` ranks an unproven
            # host on the ask alone, but a surface must not print that as a
            # per-accepted figure — there is no rate behind it — so the
            # field is null for an unproven host and for a rate of 0
            # (unclearable); both render as words, not a number.
            effective = (
                marketplacemod.effective_price(
                    ask.ask_zc_per_hour, ask.acceptance_rate
                )
                if ask.acceptance_rate is not None
                else None
            )
            asks.append(
                {
                    "id": ask.listing_id,
                    "machine_id": ask.machine_id,
                    "host_id": ask.host_id,
                    "capability_class": klass,
                    "machine_name": (
                        machine["name"] if machine is not None else None
                    ),
                    "gpu_label": (
                        marketplacemod.machine_gpu_label(
                            machine["capabilities"]
                        )
                        if machine is not None
                        else None
                    ),
                    "ask_zc_per_hour": ask.ask_zc_per_hour,
                    "donated": marketplacemod.is_donated(ask.ask_zc_per_hour),
                    "ask_usd_per_hour": pricesmod.zc_ask_usd_amount_text(
                        ask.ask_zc_per_hour
                    ),
                    "price_label": pricesmod.zc_ask_price_label(
                        ask.ask_zc_per_hour
                    ),
                    "usd_equivalent_label": (
                        pricesmod.zc_ask_usd_equivalent_label(
                            ask.ask_zc_per_hour
                        )
                    ),
                    "max_concurrent_tasks": ask.max_concurrent_tasks,
                    "acceptance_rate": ask.acceptance_rate,
                    "resolved_n": (
                        record["resolved"] if record is not None else None
                    ),
                    "effective_zc_per_hour": (
                        int(effective) if effective is not None else None
                    ),
                }
            )
        mine = [
            {
                **_jsonable(row),
                "donated": marketplacemod.is_donated(
                    int(row["ask_zc_per_hour"])
                ),
                "ask_usd_per_hour": pricesmod.zc_ask_usd_amount_text(
                    int(row["ask_zc_per_hour"])
                ),
                "price_label": pricesmod.zc_ask_price_label(
                    int(row["ask_zc_per_hour"])
                ),
                "usd_equivalent_label": (
                    pricesmod.zc_ask_usd_equivalent_label(
                        int(row["ask_zc_per_hour"])
                    )
                ),
            }
            for row in marketplacemod.listings_for_owner(db, user_id)
        ]
        return {"asks": asks, "mine": mine}

    @app.post("/v1alpha1/market/listings", status_code=201, tags=["browser"])
    async def create_market_listing(
        request: Request,
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """List a machine you own at your own ask (M2).

        The class is NOT an argument and never will be: it is computed
        from the capabilities the agent reported, because a host who could
        name their own class would sell a 3070 as Hopper-class. Refusals
        are 409, not 400 — the request was well formed, the machine is
        simply not something the ladder can promise.
        """
        payload = await _json_object(request)
        machine_id = payload.get("machine_id")
        ask = payload.get("ask_zc_per_hour")
        max_tasks = payload.get("max_concurrent_tasks", 1)
        if (
            not isinstance(machine_id, str)
            or isinstance(ask, bool)
            or not isinstance(ask, int)
            or isinstance(max_tasks, bool)
            or not isinstance(max_tasks, int)
            or max_tasks < 1
        ):
            raise HTTPException(
                status_code=400,
                detail="machine_id, an integer ask_zc_per_hour and a "
                "positive integer max_concurrent_tasks are required",
            )
        try:
            row = marketplacemod.create_listing(
                db,
                machine_id=machine_id,
                owner_id=user_id,
                ask_zc_per_hour=ask,
                max_concurrent_tasks=max_tasks,
            )
        except psycopg.errors.InvalidTextRepresentation:
            raise HTTPException(status_code=404, detail="unknown machine")
        except LookupError:
            raise HTTPException(status_code=404, detail="unknown machine")
        except marketplacemod.UnclassifiableMachine as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except marketplacemod.AlreadyListed as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {
            **_jsonable(row),
            "donated": marketplacemod.is_donated(int(row["ask_zc_per_hour"])),
            "ask_usd_per_hour": pricesmod.zc_ask_usd_amount_text(
                int(row["ask_zc_per_hour"])
            ),
            "price_label": pricesmod.zc_ask_price_label(
                int(row["ask_zc_per_hour"])
            ),
            "usd_equivalent_label": pricesmod.zc_ask_usd_equivalent_label(
                int(row["ask_zc_per_hour"])
            ),
        }

    @app.delete("/v1alpha1/market/listings/{listing_id}", tags=["browser"])
    async def withdraw_market_listing(
        listing_id: str,
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Withdraw your listing, open or paused. 404 for every reason it
        is not yours to withdraw — the id's existence is not revealed."""
        try:
            for from_state in ("open", "paused"):
                if marketplacemod.withdraw_listing(
                    db,
                    listing_id=listing_id,
                    owner_id=user_id,
                    from_state=from_state,
                ):
                    return {"withdrawn": True}
        except psycopg.errors.InvalidTextRepresentation:
            pass
        raise HTTPException(status_code=404, detail="unknown listing")

    @app.get("/v1alpha1/machines/{machine_id}/market-hint", tags=["browser"])
    async def machine_market_hint(
        machine_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """What the market says about a machine you might list.

        The class and the book are composed server-side from the same
        ladder and the same order book a listing would join, so the
        "suggestion" is the market itself — best/median/reference — never a
        model. 404 for an unknown or someone-else's machine, same doctrine
        as the machines routes: the hint must not confirm which ids exist.
        """
        try:
            rows = marketplacemod.machines_for_ids(db, {machine_id})
        except psycopg.errors.InvalidTextRepresentation:
            rows = {}
        machine = rows.get(machine_id)
        if machine is None or str(machine["owner_id"]) != user_id:
            raise HTTPException(status_code=404, detail="unknown machine")
        klass = marketplacemod.capability_class(machine["capabilities"])
        board = (
            marketplacemod.class_board(db, klass) if klass is not None else None
        )
        rates = metricsmod.acceptance_rates(
            dbmod.acceptance_rate_rows(db, machine_ids=[machine_id])
        )
        record = rates[0] if rates else None
        return {
            "capability_class": klass,
            "unclassifiable": (
                None
                if klass is not None
                else "This machine's reported capabilities do not place it "
                "in a capability class, so the market cannot price it."
            ),
            "book": (
                None
                if board is None
                else {
                    "open_asks": board["depth"],
                    "best_ask_zc": board["last_zc"],
                    "median_ask_zc": board["median_ask_zc"],
                    "reference_zc_per_hour": (
                        marketplacemod.REFERENCE_ZC_PER_HOUR[klass]
                    ),
                }
            ),
            "your_record": (
                None
                if record is None or record["acceptance_rate"] is None
                else {
                    "acceptance_rate": record["acceptance_rate"],
                    "resolved_n": record["resolved"],
                }
            ),
        }

    @app.get("/v1alpha1/market/matches", tags=["browser"])
    async def list_market_matches(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """This account's priced entitlements, on both sides, state verbatim.

        ``granted`` reaches the console as granted — an entitlement no
        money has moved for — because the one misunderstanding this
        surface can cause is a buyer reading a match as an assignment.
        """
        return {
            "as_buyer": [
                _jsonable(m)
                for m in marketplacemod.matches_for_owner(db, user_id, side="buyer")
            ],
            "as_host": [
                _jsonable(m)
                for m in marketplacemod.matches_for_owner(db, user_id, side="host")
            ],
        }

    @app.get("/v1alpha1/prices", tags=["browser"])
    async def get_prices(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """External quotes with provenance, the venues with none, and the
        ZC ladder beside them with fixed 1:1 equivalents.

        Every quote carries ``captured_at`` and ``source`` and its own
        ``stale`` verdict, so a scraped price can never sit on the page
        looking live. Venues with no quote render *not observed*, never 0.
        The ZC side is the reference ladder plus the best live ask per
        class (null where the book is empty). Original quote currencies and
        integer-millicredit ZC asks remain intact; USD quotes add ZC
        equivalents and ZC asks add exact decimal USD equivalents only for
        this wallet/marketplace comparison surface.
        """
        now = datetime.now(timezone.utc)
        quotes = pricesmod.latest_quotes(db)
        zc = []
        open_total = 0
        live_classes = 0
        observations_24h = 0
        for klass in marketplacemod.CAPABILITY_CLASSES:
            board = marketplacemod.class_board(db, klass)
            reference_zc = marketplacemod.REFERENCE_ZC_PER_HOUR[klass]
            best_ask_zc = board["last_zc"]
            open_total += board["depth"]
            if best_ask_zc is not None:
                live_classes += 1
            observations_24h += sum(
                1
                for point in board["history"]
                if now - point["at"] <= timedelta(hours=24)
            )
            zc.append(
                {
                    "capability_class": klass,
                    "reference_zc_per_hour": reference_zc,
                    "reference_usd_per_hour": (
                        pricesmod.zc_ask_usd_amount_text(reference_zc)
                    ),
                    "best_ask_zc": best_ask_zc,
                    "best_ask_usd": (
                        pricesmod.zc_ask_usd_amount_text(best_ask_zc)
                        if best_ask_zc is not None
                        else None
                    ),
                    "change_zc": board["change_zc"],
                    "depth": board["depth"],
                    "history": [
                        {
                            **_jsonable(point),
                            "best_ask_usd": (
                                pricesmod.zc_ask_usd_amount_text(
                                    point["best_ask_zc"]
                                )
                                if point["best_ask_zc"] is not None
                                else None
                            ),
                        }
                        for point in board["history"]
                    ],
                }
            )
        return {
            "quotes": [pricesmod.render(q, now) for q in quotes],
            "unpriced": [
                pricesmod.render_unpriced(venue)
                for venue in pricesmod.unpriced(
                    [venue.id for venue in routermod.VENUES], quotes
                )
            ],
            "zc": zc,
            "board": {
                "open_asks_total": open_total,
                "live_classes": live_classes,
                "observations_24h": observations_24h,
            },
        }

    @app.post("/v1alpha1/sandbox-sessions", status_code=201, tags=["browser"])
    async def create_sandbox_session(
        request: Request,
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Create, prepare and hibernate one evaluation sandbox.

        **Inline, and it blocks for about ten seconds** (create ~0.9 s,
        bootstrap, register, pause ~2.6 s — measured 2026-08-11 in
        ap-southeast-1). That is acceptable here and nowhere else on this
        feature: the session id does not exist until `create_session` has run,
        so there is nothing to hand back early and nothing for a 202 to point
        at. `model-ready`, whose wait is bounded by fifteen minutes rather than
        ten seconds, is the opposite case and is backgrounded.

        The evaluation spec is compiled to a JobSpec BEFORE anything is
        provisioned. It is submitted on the far side of the hibernation, so an
        unusable spec would otherwise cost a sandbox, a bootstrap and a
        credential to discover — and would surface as a session that failed
        after the model was already trained.
        """
        _sandbox_or_404()
        payload = await _json_object(request)

        training_job_id = payload.get("training_job_id")
        if not isinstance(training_job_id, str) or not training_job_id:
            raise HTTPException(
                status_code=400, detail="training_job_id is required"
            )
        evaluation_spec = payload.get("evaluation_spec")
        if not isinstance(evaluation_spec, dict):
            raise HTTPException(
                status_code=400, detail="evaluation_spec must be an object"
            )
        try:
            build_evaluation_jobspec(
                # Any well-formed id compiles the same spec; this one is
                # thrown away. What is being checked is the SUBMITTER's half —
                # image, command, parameters — and the session id contributes
                # only the name.
                session_id="00000000-0000-0000-0000-000000000000",
                pool_id=settings.fc_sandbox_pool_id or "sandbox",
                training_job_id=training_job_id,
                spec=evaluation_spec,
            )
        except EvaluationSpecError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

        try:
            session_id = await orchmod.start_session(
                db, sandbox(), settings,
                owner_id=user_id,
                training_job_id=training_job_id,
                evaluation_spec=evaluation_spec,
                coordinator_url=sandbox_enrolment_url,
            )
        except (orchmod.OrchestratorError, OSSUnavailable,
                SandboxUnavailable) as exc:
            raise _sandbox_http_error(exc) from None

        row = await _session_for_owner_or_404(db, session_id, user_id)
        return {
            "session_id": session_id,
            "state": row["state"],
            "share_token": row["share_token"],
        }

    @app.post(
        "/v1alpha1/sandbox-sessions/{session_id}/model-ready",
        status_code=202, tags=["browser"],
    )
    async def sandbox_session_model_ready(
        session_id: str,
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """The trained model exists — wake the sandbox and evaluate.

        **Backgrounded, and that is not an optimisation.** `on_model_ready`
        wakes the sandbox, verifies the worker, re-mints the presigned URLs,
        submits the evaluation and then WAITS for an accepted result, bounded
        by `evaluation_timeout_s` (900 s by default). No HTTP client, browser
        or proxy holds a request open for fifteen minutes, and the ones that
        try give up somewhere in the middle — leaving the caller unable to
        tell a timed-out socket from a failed evaluation while the work
        carries on regardless. So this returns 202 immediately and the
        session's own ledger is where the answer appears.

        Safe to call repeatedly, and safe to call concurrently: the first thing
        the orchestrator does is compare-and-set HIBERNATED -> RESUMING, and a
        loser returns having woken nothing.

        The two failures that are knowable synchronously are answered
        synchronously — an unknown-or-not-yours session (404) and an
        unconfigured artifact store (503) — rather than being discovered by a
        background task nobody is watching.
        """
        _sandbox_or_404()
        row = await _session_for_owner_or_404(db, session_id, user_id)
        if not settings.oss_configured:
            raise _sandbox_http_error(
                OSSUnavailable(
                    "OSS is not configured, so there is no artifact to observe"
                )
            )

        async def _evaluate() -> None:
            conn = await run_in_threadpool(app.state.connect)
            try:
                await orchmod.on_model_ready(
                    conn, sandbox(), settings,
                    session_id=session_id, driver=driver,
                )
            except Exception:  # noqa: BLE001 - the ledger already says why
                # `on_model_ready` records FAILED and runs cleanup before it
                # raises, so by here the session's own evidence is already
                # written and there is no caller left to tell. Logged and
                # swallowed so the task does not also print an unstructured
                # traceback into a JSON-per-line log stream.
                log.warning(
                    json.dumps({"text": "sandbox evaluation failed",
                                "session_id": session_id}),
                    exc_info=True,
                )
            finally:
                await run_in_threadpool(conn.close)

        # Its own connection, not the request's: `db_conn` closes on the way
        # out of this response, and this task outlives it by up to fifteen
        # minutes.
        task = asyncio.create_task(_evaluate(), name=f"fc-eval-{session_id}")
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        return {"session_id": session_id, "state": row["state"]}

    @app.post(
        "/v1alpha1/sandbox-sessions/{session_id}/cleanup", tags=["browser"]
    )
    async def cleanup_sandbox_session(
        session_id: str,
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Kill the sandbox and revoke the credential. Both, independently.

        Inline: `cleanup_session` is two bounded calls (kill p50 210 ms plus a
        local revocation) and it never raises — it is called from `finally`
        blocks, where an exception would mask the failure that sent us in. So
        the state that comes back is read from the row afterwards rather than
        assumed from the call, which is the same rule the orchestrator applies
        to the provider: TERMINATED appears only once the API confirmed the
        sandbox is gone, and a cleanup that could not finish leaves the session
        where it is for `reconcile` to pick up again.
        """
        _sandbox_or_404()
        await _session_for_owner_or_404(db, session_id, user_id)
        try:
            await orchmod.cleanup_session(
                db, sandbox(), settings, session_id=session_id
            )
        except (orchmod.OrchestratorError, SandboxUnavailable) as exc:
            raise _sandbox_http_error(exc) from None
        row = await _session_for_owner_or_404(db, session_id, user_id)
        return {"session_id": session_id, "state": row["state"]}

    @app.get("/v1alpha1/sandbox-sessions/{session_id}", tags=["browser"])
    async def get_sandbox_session(
        session_id: str,
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """One session, owner-scoped. The full row — this caller owns it."""
        _sandbox_or_404()
        row = await _session_for_owner_or_404(db, session_id, user_id)
        return _jsonable(dict(row))

    @app.get("/v1alpha1/sandbox-sessions/{session_id}/events", tags=["browser"])
    async def get_sandbox_session_events(
        session_id: str,
        after_sequence: int = Query(0, ge=0),
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """One session's ledger, owner-scoped in SQL.

        The existence check comes first even though `events_for_owner` joins
        the owner in itself: without it an unknown session and somebody else's
        session both answer `[]`, which reads to a console as "nothing has
        happened yet" rather than "this is not yours". Both still answer 404
        and remain indistinguishable from each other.

        `after_sequence` is what makes the console's poll cheap over a
        hibernation that lasts hours.
        """
        _sandbox_or_404()
        await _session_for_owner_or_404(db, session_id, user_id)
        events = await run_in_threadpool(
            lambda: ssmod.events_for_owner(
                db, session_id, user_id, after_sequence=int(after_sequence)
            )
        )
        return [_jsonable(dict(e)) for e in events]

    @app.get("/v1alpha1/jobs/{job_id}/sandbox-sessions", tags=["browser"])
    async def list_sandbox_sessions_for_job(
        job_id: str,
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Every session this account opened for one training job, newest first.

        `[]` for a job with no session, and `[]` for a job that is not this
        caller's — absence is a normal state here, not an error, and a job
        page asks this before any session exists. A 404 would make "no
        evaluation yet" indistinguishable from "this job is gone" on the one
        screen where the difference matters.
        """
        _sandbox_or_404()
        rows = await run_in_threadpool(
            lambda: ssmod.list_sessions_for_owner(
                db, user_id, training_job_id=job_id
            )
        )
        return [_jsonable(dict(row)) for row in rows]

    # -- the public evidence page: NO AUTHENTICATION ------------------------
    #
    # The only route in this API that reads the database with no credential at
    # all, and it is deliberate: the submission needs a link that opens for
    # somebody who has no account, and every console route redirects to
    # sign-in. The token IS the authorization.
    #
    # Four things make that safe, and all four are load-bearing:
    #
    #   * `fetch_session_by_share_token` narrows the columns IN SQL. Five never
    #     leave the database — owner, pool, machine, sandbox id, evaluation
    #     spec — and neither does the token itself. Read the comment above
    #     `SESSION_SHARE_COLUMNS` before touching that set.
    #   * `public_session_view` narrows twice more: the session id is rendered
    #     as a suffix, `marker_sha256` as a 12-character prefix, and
    #     `error_message` is dropped entirely in favour of the sanitized
    #     `error_code`.
    #   * An unknown token, a withdrawn one (the column is nullable precisely
    #     so a page can be revoked without a migration) and a wrong one all
    #     answer the same 404.
    #   * A rate limiter, such as it is — see `FixedWindowLimiter` for exactly
    #     what it does and does not claim.

    @app.get(
        "/v1alpha1/public/sandbox-sessions/{share_token}", tags=["public"]
    )
    async def public_sandbox_session(
        share_token: str,
        request: Request,
        db: psycopg.Connection = Depends(db_conn),
    ):
        """One session and its ledger, for anybody holding the link.

        The envelope is fixed at ``{"session", "events"}``. Not negotiable and
        not conditional on there being events: a consumer that has to sniff
        which of two shapes it received is a consumer with a branch that will
        eventually be wrong.
        """
        _sandbox_or_404()
        client = request.client.host if request.client else "unknown"
        if not public_limiter.allow(client):
            raise HTTPException(status_code=429, detail="too many requests")

        row = await run_in_threadpool(
            ssmod.fetch_session_by_share_token, db, share_token
        )
        if row is None:
            # Unknown, withdrawn and wrong are one answer. A 403 for
            # "withdrawn" would confirm the token was once real.
            raise HTTPException(status_code=404, detail="unknown session")
        events = await run_in_threadpool(
            ssmod.events_for_session, db, str(row["id"])
        )
        return {
            "session": public_session_view(row),
            "events": [public_event_view(e) for e in events],
        }

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
                            # Nested, unlike the four above: this one is a
                            # field of `NodeCapabilities`, not of the
                            # registration. Read off the RAW body rather
                            # than through the pinned protocol model on
                            # purpose — the model only learns the field the
                            # release the pin moves, and until then
                            # validating through it would drop the number
                            # silently and leave every dataset job refused
                            # for want of capacity nobody could advertise.
                            dataset_cache_bytes=(
                                _advertised_dataset_cache_bytes(parsed)
                            ),
                            # The hardware description itself, allowlisted in
                            # `db._REPORTED_CAPABILITY_FIELDS`. Until this was
                            # passed, `machines.capabilities` held only the
                            # dataset cache figure, so every classifier that
                            # reads that column — the marketplace ladder and
                            # the router's — saw a machine with no cores and
                            # no GPUs and filed a 4090 rig as `cpu-small`.
                            # Read off the RAW body for the same reason
                            # `dataset_cache_bytes` is: the pinned protocol
                            # model drops any field it does not yet know.
                            reported=(
                                parsed.get("capabilities")
                                if isinstance(parsed.get("capabilities"), dict)
                                else None
                            ),
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
        # AND IT IS NO LONGER ONLY A DISPLAY COLUMN. `capacity/reconcile.py`
        # decides whether to DESTROY a rented GPU by reading it: quiet for
        # `quiet_after_s` is swept, never seen by `boot_grace_s` is swept. This
        # one statement is what tells the reconciler that a machine three hours
        # into a training run is alive. Stop writing it — or write it from
        # somewhere that is not really the machine speaking — and the failure
        # is money, silent, and irreversible in one direction. Pinned by
        # `test_agent_proxy.py::test_heartbeat_records_last_seen_so_the_console
        # _can_show_online`, whose docstring now carries the same warning.
        #
        # The pool membership refresh rides the same connection open, but
        # its own try/except, separate from `touch_machine_last_seen`'s: a
        # best-effort liveness write must never fail the pools stamp CLOSED —
        # only a genuine membership-lookup failure does that, below.
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
                        # The coordinator's own deadline for this lease. It is
                        # the ONLY instant that lets an attempt nobody ever
                        # reports on be resolved at all: lease expiry happens
                        # in the coordinator's sweeper, which never calls us,
                        # so without this a machine unplugged mid-task leaves
                        # an open row for ever and the reliability page counts
                        # its work as neither accepted nor lost. `.get`, not
                        # `[...]`: a lease body missing the field must still
                        # record the mapping the credit path needs.
                        deadline=lease.get("deadline"),
                    )
            except Exception:
                log.warning("could not record attempt for machine %s", machine.id)
            # ESCROW IS HELD ON CLAIM, NEVER ON GRANT. This is the single
            # hop where a lease first exists, so it is the earliest moment
            # money may be committed — and a granted match with no claim
            # behind it holds nothing, on purpose. The match is found the
            # same way settlement finds it later (machine + the bid's job),
            # one hop earlier in the state machine: still `granted`, which
            # the hold itself moves to `claimed`. Best-effort, exactly like
            # the attempt row: an accounting write must never be the reason
            # a machine fails to pick up work, and the movement is
            # idempotent per lease, so a retry completes rather than
            # doubles it.
            if response.status_code == 200:
                try:
                    claimed = json.loads(response.body)
                    with contextlib.closing(app.state.connect()) as conn:
                        match_id = marketplacemod.match_for_claim(
                            conn,
                            machine_id=machine.id,
                            job_id=str(claimed.get("job_id")),
                        )
                        if match_id is not None:
                            marketplacemod.hold_escrow_on_claim(
                                conn,
                                match_id=match_id,
                                lease_id=str(claimed["lease_id"]),
                            )
                except Exception:
                    log.warning(
                        "could not hold escrow for machine %s; the claim "
                        "stands and the hold is idempotently retryable",
                        machine.id,
                    )
        return response

    @app.post("/v1alpha1/attempts/{lease_id}/heartbeat", tags=["agent"])
    async def attempt_heartbeat(
        lease_id: str, request: Request, machine: Machine = Depends(current_machine)
    ):
        response = await proxy(
            request, machine, f"/v1alpha1/attempts/{_seg(lease_id)}/heartbeat"
        )
        # A heartbeat the coordinator ACCEPTS extends the lease, and its
        # response is the renewed `Lease`. Carrying that forward is what keeps
        # the expiry reconciler honest: without it the deadline recorded at
        # claim time would stand for ever, and a healthy hour-long task would
        # be resolved as expired sixty seconds in.
        #
        # Only on 200. A 410 means the lease is already dead — the one answer
        # that must NOT push a deadline forward — and every other status is
        # not a renewal either.
        #
        # Best-effort, exactly like the attempt row itself: an accounting
        # column must never be the reason a working task is told to stop.
        if response.status_code == 200:
            try:
                renewed = json.loads(response.body)
                with contextlib.closing(app.state.connect()) as conn:
                    dbmod.note_attempt_deadline(
                        conn,
                        lease_id=lease_id,
                        machine_id=machine.id,
                        deadline=renewed.get("deadline"),
                    )
            except Exception:
                log.warning(
                    "could not record a renewed deadline for machine %s", machine.id
                )
        return response

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
        response = await proxy(
            request, machine, f"/v1alpha1/attempts/{_seg(lease_id)}/fail"
        )
        # THE ONE MOMENT THIS API IS TOLD AN ATTEMPT FAILED. Until migration
        # 0015 this route was a pure proxy and wrote nothing, so a failed
        # attempt and an in-flight one were the same row (`accepted_at is
        # null`) and `tasks_accepted / tasks_attempted` had a denominator that
        # grew with every claim and shrank for nothing.
        #
        # AFTER the coordinator answers, and only for an answer it accepted.
        # A fail it refused — unknown lease, already dead, wrong holder —
        # describes nothing that happened; recording it would resolve an
        # attempt on the strength of a rejected request, and for a lease that
        # is still alive somewhere it would resolve one that is still running.
        #
        # Idempotent in the database rather than by a convention that this
        # route is called once: `record_attempt_failure` will not move a
        # terminal outcome that is already written, so an agent's retry
        # describes one failure and not a second, longer one.
        #
        # Best-effort, exactly like the credit write next door: the agent's
        # own error path must never fail because of an accounting row.
        if 200 <= response.status_code < 300:
            try:
                with contextlib.closing(app.state.connect()) as conn:
                    dbmod.record_attempt_failure(
                        conn, lease_id=lease_id, machine_id=machine.id
                    )
            except Exception:
                log.warning(
                    "could not record a failed attempt for machine %s", machine.id
                )
        return response

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
        """The resume point for a task that is about to start.

        Retried, and it is the only agent route that is. Checkpointing is
        now on for every job, so this GET runs at the START of every task —
        and flashnode treats a non-404, non-200 as fatal: it raises, and
        that exception is caught neither as a task failure nor as a lost
        lease, so the lease is held to expiry instead of being requeued and
        the host takes a strike toward quarantine. A momentary gateway blip
        would do that fleet-wide.

        A GET of the latest checkpoint is idempotent, so repeating it is
        free. Answering 404 on a gateway error would NOT be free and is the
        tempting wrong fix: 404 means "no checkpoint exists", the agent
        would believe it, and a resumable task would silently restart from
        step 0 — losing exactly the work this feature exists to protect.
        Better to stay honest and keep the (now much narrower) failure.

        The residual window is real: a coordinator down for longer than the
        ladder still produces that fatal raise. Closing it properly means
        making the raise non-fatal in flashnode, which is a public-repo
        change and a release.
        """
        return await proxy(
            request, machine,
            f"/v1alpha1/jobs/{_seg(job_id)}/tasks/{_seg(task_id)}/checkpoints/latest",
            retry_delays=AGENT_RETRY_DELAYS,
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


#: What the coordinator actually stores committed artifacts to, in every
#: deployment this repo has.
#:
#: A CONSTANT, NOT AN ENVIRONMENT LOOKUP, and that is the fix rather than an
#: oversight. The panel used to answer `FLASHML_ARTIFACT_BACKEND` (defaulting
#: to "minio"), and NOTHING IN THE MANAGED PATH READS THAT VARIABLE.
#: `flashruntime.artifacts.store_from_env()` has exactly one caller in the
#: whole runtime — `service/app.py`'s `ingest_workload_artifacts`, which
#: belongs to the optional KubeRay path and is disabled
#: (`FLASHML_ENABLE_KUBERAY=0`) on both deployed coordinators. Every artifact
#: this product produces travels `flashnode -> PUT /v1alpha1/artifacts/{key}
#: -> the coordinator's own filesystem` under `FLASHML_LOCAL_ARTIFACTS_DIR`,
#: whatever that variable says. Setting it to "oss" changed the panel and
#: nothing else, which is worse than not having the panel: it let someone
#: read "artifact_store: oss" off a dashboard and believe a redeploy could no
#: longer destroy their models.
#:
#: The one thing that DOES put artifacts in OSS is this repo's own mirror
#: (`artifact_mirror.py`), and it is reported separately below because it is
#: a separate, additional copy — never a replacement for this one.
COORDINATOR_ARTIFACT_STORE = "coordinator-local-disk"

#: `artifact_mirror` panel values.
MIRROR_NOT_OBSERVED = "not observed"
MIRROR_NOT_CONFIGURED = "not configured"
MIRROR_UNUSABLE = "configured-but-unusable"
MIRROR_READY = "configured"


def _artifact_mirror_panel() -> dict[str, Any]:
    """What the OSS mirror's configuration actually is, for the panel.

    READ OFF ``Settings``, NEVER OFF AN ENVIRONMENT VARIABLE, which is the
    second half of this route's fix. The panel used to report
    ``FLASHML_OSS_BUCKET`` — a name nothing in this process has ever read.
    ``settings.from_env`` reads ``OSS_BUCKET``, so a fully configured
    deployment reported an empty bucket, and a deployment that had set only
    ``FLASHML_OSS_BUCKET`` reported a bucket while the mirror was off. Both
    directions were wrong, and the second is the dangerous one: it is a
    dashboard telling somebody their artifacts are being copied off a disk
    that a redeploy erases, when nothing is copying them.

    Four answers, and the distinction between the first two is the point:

    ``not observed`` — the environment does not describe a deployment this
      process can build ``Settings`` from at all, so the honest answer is
      that nobody looked. This is the normal case for the legacy app, which
      `create_app` only ever returns when SUPABASE_URL/COORDINATOR_URL are
      absent — exactly the condition `Settings.from_env` refuses to build
      under while auth is required. Reporting "not configured" here would be
      a claim about the mirror derived from a fact about Supabase.
    ``not configured`` — settings were read and the four OSS values are not
      all present. The mirror is off; the coordinator's disk is the only
      store. Not an error (``artifact_mirror``'s rule 1).
    ``configured-but-unusable`` — the values are there and a client cannot be
      built from them: ``oss2`` is not installed, or the endpoint/bucket pair
      is malformed. This is the state that most needs a name, because
      ``oss_configured`` alone would report it as working right up until the
      first job finished.
    ``configured`` — a bucket-scoped client constructs. NO NETWORK CALL is
      made to prove more than that: ``OSSArtifacts.healthcheck`` writes,
      reads and deletes a real object, which is not something a display route
      may do on every request. What this claims is exactly what it checked.
    """
    try:
        settings = Settings.from_env()
    except RuntimeError:
        # `from_env` refuses to build when auth is required and the
        # Supabase/coordinator secrets are missing. Nothing about the mirror
        # is knowable from here, so nothing about it is asserted.
        return {"artifact_mirror": MIRROR_NOT_OBSERVED, "oss_bucket": None}

    if not settings.oss_configured:
        return {"artifact_mirror": MIRROR_NOT_CONFIGURED, "oss_bucket": None}
    try:
        OSSArtifacts.from_settings(settings)
    except OSSUnavailable:
        state = MIRROR_UNUSABLE
    else:
        state = MIRROR_READY
    return {"artifact_mirror": state, "oss_bucket": settings.oss_bucket}


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
        """What this deployment is actually wired to.

        A panel is only worth having if every line of it is checkable, so
        the two lines that were not are gone. ``artifact_store`` no longer
        reads ``FLASHML_ARTIFACT_BACKEND`` and ``oss_bucket`` no longer reads
        ``FLASHML_OSS_BUCKET``: nothing in this system consumes either
        variable, so both reported a storage arrangement that could be set by
        typing one env var and was never true. See
        ``COORDINATOR_ARTIFACT_STORE`` and ``_artifact_mirror_panel`` for what
        replaced them and why.

        The remaining ``FLASHML_*`` lookups are unchanged and are honest as
        display-only flags: they describe the Kind/ACK POC profile this
        legacy app belongs to, and nothing reads them for behaviour either —
        but they were never claims about where a model is stored.
        """
        env = os.environ
        profile = env.get("FLASHML_PROFILE", "local")
        return {
            "profile": profile,
            "backend": "ray/kuberay",
            "environment": "Alibaba ACK" if profile == "alibaba-ack" else "Local Kind",
            # Where a committed artifact actually lands, in every deployment
            # this repo has. Not a preference, not an env var: a fact about
            # the runtime's Mode A path.
            "artifact_store": COORDINATOR_ARTIFACT_STORE,
            "image_registry": env.get("FLASHML_ACR_IMAGE", "local"),
            "ack_connected": profile == "alibaba-ack",
            # `artifact_mirror` + `oss_bucket`, from Settings. The mirror is
            # an ADDITIONAL copy of accepted artifacts, never a replacement
            # for the coordinator's disk above.
            **_artifact_mirror_panel(),
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
