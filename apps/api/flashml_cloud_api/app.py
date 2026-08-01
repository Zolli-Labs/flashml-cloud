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

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, Iterator

import httpx
import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row

from flashruntime.protocol.v1alpha1 import JobSpec, NodeHeartbeat, NodeRegistration

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import enrolment
from flashml_cloud_api.auth import (
    MACHINE_TOKEN_PREFIX,
    AuthError,
    verify_supabase_jwt,
)
from flashml_cloud_api.db import Machine
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
) -> FastAPI:
    """The public door. Agents and browsers both arrive here; nothing else
    is exposed to the internet.

    ``connect`` is a factory returning a live psycopg connection (injected
    so tests can point it at an ephemeral Postgres). ``transport`` is an
    httpx transport for the coordinator hop, injected so tests can record
    exactly what leaves this process without needing a live coordinator.

    On ``settings.require_auth``: it governs *startup validation of the
    environment*, not whether requests are authenticated. There is no open
    mode here. Checking credentials is the entire reason this service
    exists, so an "auth off" switch would be a single env var that turns the
    security model off in production.
    """
    connect = connect or (lambda: dbmod.connect(settings))
    coordinator = CoordinatorClient(settings, transport=transport)
    max_upload_bytes = int(
        os.environ.get("FLASHML_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES)
    )

    app = FastAPI(title="FlashML Cloud API", version="0.2.0")
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
        return {"status": "ok"}

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
        return {
            "device_code": started["device_code"],
            "user_code": started["user_code"],
            "verification_uri": f"{base}/enrol" if base else "/enrol",
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

    @app.get("/v1alpha1/jobs", tags=["browser"])
    async def list_jobs_route(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        owned = dbmod.list_job_ids_for_owner(db, user_id)
        if not owned:
            # Nothing to scope down to; skip the coordinator round trip
            # rather than fetch a list of jobs we would only throw away.
            return []
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
        return [j for j in jobs if isinstance(j, dict) and j.get("job_id") in owned]

    @app.get("/v1alpha1/jobs/{job_id}", tags=["browser"])
    async def get_job_route(
        job_id: str,
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        if dbmod.fetch_job_for_owner(db, job_id, user_id) is None:
            # Not found and not yours look identical: a 403 here would
            # confirm to a guesser that the id exists.
            raise HTTPException(status_code=404, detail="unknown job")
        r = await coordinator.forward("GET", f"/v1alpha1/jobs/{_seg(job_id)}")
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
        return await proxy(
            request,
            machine,
            f"/v1alpha1/nodes/{machine.node_id}/heartbeat",
            force_node_id=True,
        )

    @app.post("/v1alpha1/leases/claim", tags=["agent"])
    async def claim(request: Request, machine: Machine = Depends(current_machine)):
        return await proxy(
            request, machine, "/v1alpha1/leases/claim", force_node_id=True
        )

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
