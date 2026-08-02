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
from pathlib import Path
from typing import Any, Callable, Iterator

import tempfile
import uuid

import httpx
import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from starlette.concurrency import run_in_threadpool

from flashruntime.protocol.v1alpha1 import JobSpec, NodeHeartbeat, NodeRegistration

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import enrolment
from flashml_cloud_api import fedavg as fedavgmod
from flashml_cloud_api import repo as repomod
from flashml_cloud_api.auth import (
    MACHINE_TOKEN_PREFIX,
    AuthError,
    verify_supabase_jwt,
)
from flashml_cloud_api.compile import (
    CompileError,
    compile_federated_round,
    compile_to_jobspec,
)
from flashml_cloud_api.db import Machine
from flashml_cloud_api.flashml_yaml import ConfigError, parse_flashml_yaml
from flashml_cloud_api.images import UnknownImage, resolve_image
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


def _scrub_identity(body: bytes, node_id: str, *, force: bool) -> tuple[bytes, str]:
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
    """
    if not body.strip():
        if not force:
            return body, "application/json"
        return json.dumps({"node_id": node_id}).encode(), "application/json"
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return body, "application/json"  # not JSON: the coordinator will refuse it
    if not isinstance(parsed, dict):
        return body, "application/json"
    if force or "node_id" in parsed:
        parsed["node_id"] = node_id
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


# ---------------------------------------------------------------------------
# the cloud app
# ---------------------------------------------------------------------------


def create_cloud_app(
    settings: Settings,
    connect: Callable[[], psycopg.Connection] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    fetch_repo: Callable[[str, str, str], bytes] | None = None,
    start_federated_job: Callable[..., Any] | None = None,
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
        """The signed-in user id from a verified Supabase JWT. A machine
        token is rejected without ever reaching the JWT decoder."""
        token = _bearer(request)
        if token is None or looks_like_machine_token(token):
            raise HTTPException(status_code=401, detail="sign-in required")
        try:
            return verify_supabase_jwt(token, settings)
        except AuthError:
            # The reason is not reported: "expired" vs "bad signature" is an
            # oracle, and the caller can do nothing different either way.
            raise HTTPException(status_code=401, detail="sign-in required") from None

    async def proxy(
        request: Request,
        machine: Machine,
        path: str,
        *,
        force_node_id: bool = False,
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
                body, machine.node_id, force=force_node_id
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
        payload = await _json_object(request)
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
        payload = await _json_object(request)
        device_code_value = payload.get("device_code")
        if not isinstance(device_code_value, str) or not device_code_value:
            raise HTTPException(status_code=400, detail="device_code required")
        token = enrolment.redeem_device_code(db, device_code_value)
        if token is None:
            # RFC 8628's polling shape. Unknown / unapproved / expired /
            # already-redeemed are one indistinguishable answer, so this
            # cannot be used to learn which codes exist.
            return Response(
                content=json.dumps(
                    {"error": "authorization_pending",
                     "interval": enrolment.POLL_INTERVAL_SECONDS}
                ),
                status_code=400,
                media_type="application/json",
            )
        return {"token": token, "token_type": "machine"}

    # -- browser-facing: Supabase JWT --------------------------------------

    @app.get("/v1alpha1/me", tags=["browser"])
    async def me(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        return _jsonable(dbmod.upsert_profile(db, user_id))

    @app.get("/v1alpha1/machines", tags=["browser"])
    async def list_machines(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        return [_jsonable(m) for m in dbmod.list_machines_for_owner(db, user_id)]

    @app.post("/v1alpha1/device/approve", tags=["browser"])
    async def approve(
        request: Request,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        payload = await _json_object(request)
        user_code = payload.get("user_code")
        if not isinstance(user_code, str) or not user_code:
            raise HTTPException(status_code=400, detail="user_code required")
        # Ownership is established here and nowhere else, from the verified
        # JWT sub. It never comes from the body.
        dbmod.upsert_profile(db, user_id)
        try:
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
        return {"machine_id": str(machine_id), "status": "approved"}

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
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        raw = await request.body()
        if len(raw) > MAX_JSON_BODY_BYTES:
            raise HTTPException(status_code=413, detail="request body too large")
        payload = await _json_object(request)
        # owner_id is never accepted from the body — whatever the caller
        # put there (if anything) is simply not forwarded or looked at.
        payload.pop("owner_id", None)
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
        user_id: str = Depends(current_user),
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
                spec = compile_federated_round(
                    config, image, code_uri, config.name,
                    round_index=0, weights_uri=None,
                )
            else:
                spec = compile_to_jobspec(config, image, code_uri, config.name)
        except CompileError as exc:
            raise HTTPException(status_code=400, detail=safe_text(exc, 500)) from None

        upload = await coordinator.forward(
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
            raise HTTPException(status_code=502, detail="could not stage the repo")

        if config.is_federated:
            # A federated run is N coordinator jobs, one per round, so there
            # is no single coordinator job id to own it by. The parent id is
            # minted here and each round's coordinator job is recorded
            # against it in `job_rounds`; nothing is submitted from this
            # request at all — the driver submits round 0 itself, so a run
            # can never end up with a round the driver does not know it owns.
            job_id = fedavgmod.new_federated_job_id()
            dbmod.insert_job(
                db,
                job_id=job_id,
                owner_id=user_id,
                name=spec["metadata"]["name"],
                source={
                    "type": "github",
                    "owner": owner,
                    "repo": name,
                    "ref": ref,
                    "code_artifact": code_uri,
                    # Only a federated row carries these. An independent
                    # row's `source` is byte-identical to what it has always
                    # been, so nothing reading it has to learn a new shape.
                    "mode": config.mode,
                    "rounds": config.rounds,
                    "shards": config.shards,
                    "min_participants": config.min_participants,
                },
                spec=spec,
                status="PENDING",
            )
            start_federated_job(
                fedavgmod.FederatedRun(
                    job_id=job_id,
                    job_name=config.name,
                    config=config,
                    image=image,
                    code_artifact_uri=code_uri,
                ),
                settings=settings,
                connect=request.app.state.connect,
            )
            return Response(
                content=json.dumps({
                    "job_id": job_id,
                    "state": "PENDING",
                    "mode": config.mode,
                    "rounds": config.rounds,
                    "shards": config.shards,
                    "min_participants": config.min_participants,
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

        dbmod.insert_job(
            db,
            job_id=job_id,
            # From the verified JWT, always. The body has no say in this,
            # and there is no branch here that could give it one.
            owner_id=user_id,
            name=spec["metadata"]["name"],
            source={
                "type": "github",
                "owner": owner,
                "repo": name,
                "ref": ref,
                "code_artifact": code_uri,
            },
            spec=spec,
            status=str(job.get("state") or "PENDING"),
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
        owned = dbmod.list_job_ids_for_owner(db, user_id)
        # A federated parent id names no coordinator job, so it can never
        # match anything in the coordinator's list; dropping it here is what
        # lets a user whose only jobs are federated skip the round trip
        # entirely instead of fetching a list to throw all of it away.
        owned = {j for j in owned if not fedavgmod.is_federated_job_id(j)}
        # A federated run is one coordinator job per round, so it is not in
        # the coordinator's list at all and has to be added from this table.
        # Empty for every user who has never submitted one, which is what
        # keeps this list byte-identical to before for them.
        federated = [
            {
                "job_id": row["id"],
                "name": row.get("name"),
                "state": row.get("status"),
                "mode": "federated",
            }
            for row in dbmod.list_federated_jobs_for_owner(db, user_id)
        ]
        if not owned:
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
        # unscoped behind the operator token; this table is the only place
        # the owner filter can be applied.
        return [
            j for j in jobs if isinstance(j, dict) and j.get("job_id") in owned
        ] + federated

    @app.get("/v1alpha1/jobs/{job_id}", tags=["browser"])
    async def get_job_route(
        job_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        row = dbmod.fetch_job_for_owner(db, job_id, user_id)
        if row is None:
            # Not found and not yours look identical: a 403 here would
            # confirm to a guesser that the id exists.
            raise HTTPException(status_code=404, detail="unknown job")
        if fedavgmod.is_federated_job_id(job_id):
            # A federated run has no single coordinator job — it is one job
            # per round — so forwarding this id would ask the coordinator
            # about something it has never heard of and answer 404 for a job
            # the user does own. The local row plus the round history IS the
            # job here. Unreachable for every non-federated id, so the
            # forwarding path below is unchanged.
            rounds = dbmod.list_job_rounds_for_owner(db, job_id, user_id)
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
            }
        r = await coordinator.forward("GET", f"/v1alpha1/jobs/{_seg(job_id)}")
        return _passthrough(r)

    @app.get("/v1alpha1/jobs/{job_id}/rounds", tags=["browser"])
    async def get_job_rounds(
        job_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """The federated-averaging history of a job the caller owns.

        Ownership is checked exactly as the rest of this block does it —
        against the ``jobs`` table, before anything else, answering 404 (not
        403) for a job that exists but belongs to someone else, so this
        route cannot be used to learn which job ids are real. The listing
        query joins on ownership a second time (``list_job_rounds_for_owner``)
        rather than trusting this check to have happened.
        """
        if dbmod.fetch_job_for_owner(db, job_id, user_id) is None:
            raise HTTPException(status_code=404, detail="unknown job")
        return [_jsonable(r)
                for r in dbmod.list_job_rounds_for_owner(db, job_id, user_id)]

    @app.get("/v1alpha1/jobs/{job_id}/events", tags=["browser"])
    async def get_job_events(
        job_id: str,
        since: int = 0,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """The coordinator's event ledger for a job the caller owns.

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

        Ownership is checked against the ``jobs`` table before the
        coordinator is contacted, answering 404 for a job that exists and
        is not yours, exactly as the sibling read routes do.
        """
        if dbmod.fetch_job_for_owner(db, job_id, user_id) is None:
            raise HTTPException(status_code=404, detail="unknown job")

        if fedavgmod.is_federated_job_id(job_id):
            # A federated run is one coordinator job PER ROUND, so there is
            # no single ledger to forward to. Fan out over the rounds that
            # have a coordinator job and tag each event with the round it
            # came from, which is the only way the client can order them.
            # Rounds without a coordinator_job_id are rounds that never
            # reached the coordinator; they contribute nothing rather than
            # an empty group.
            rounds = dbmod.list_job_rounds_for_owner(db, job_id, user_id)
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
        """Current task state for a job the caller owns.

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
        if dbmod.fetch_job_for_owner(db, job_id, user_id) is None:
            raise HTTPException(status_code=404, detail="unknown job")

        if fedavgmod.is_federated_job_id(job_id):
            rounds = dbmod.list_job_rounds_for_owner(db, job_id, user_id)
            pairs = [
                (row["round"], row["coordinator_job_id"])
                for row in rounds
                if row.get("coordinator_job_id")
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
        able to read artifacts under a job it owns. Ownership is checked
        here, against this table, before the key is ever forwarded — same
        404-not-403 rule as the rest of this block.
        """
        if dbmod.fetch_job_for_owner(db, job_id, user_id) is None:
            raise HTTPException(status_code=404, detail="unknown job")
        full_key = _artifact_key(f"{_seg(job_id)}/{key}")
        coordinator_key = f"jobs/{full_key}"
        r = await coordinator.forward("GET", f"/v1alpha1/artifacts/{coordinator_key}")
        return _passthrough(r)

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
        return await proxy(
            request, machine, "/v1alpha1/nodes/register", force_node_id=True
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
        # Best-effort: a display column must never be the reason a machine's
        # heartbeat fails and its leases start expiring.
        try:
            with contextlib.closing(app.state.connect()) as conn:
                dbmod.touch_machine_last_seen(conn, machine.id)
        except Exception:
            log.warning("could not record last_seen_at for machine %s", machine.id)

        return await proxy(
            request,
            machine,
            f"/v1alpha1/nodes/{machine.node_id}/heartbeat",
            force_node_id=True,
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
        return await proxy(
            request, machine, f"/v1alpha1/attempts/{_seg(lease_id)}/complete"
        )

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
