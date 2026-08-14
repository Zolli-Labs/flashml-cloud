"""Priced pool routing, end to end, through the real authoring surface.

This is the Task 6 rehearsal for the pool-routing-phase1 branch: everything
Tasks 1-5 built (``price:`` in flashml.yaml, submit-time routing, the ranked
book with exclusion reasons, and ``GET /jobs/{id}/routing``) lives entirely
in ``flashml-cloud/apps/api`` and has been proven by ``apps/api/tests``
against a real ephemeral Postgres with a *scripted* coordinator transport
(``httpx.AsyncBaseTransport`` fakes — see ``test_routing_routes.py``). What
none of those tests can show is the one thing this repo's whole ``e2e/``
suite exists for: whether the pieces still agree once a REAL coordinator, a
REAL cloud API process and REAL flashnode agents are talking to each other
over real HTTP, exactly the way a submitter, two hosts and a volunteer
machine would meet in production.

WHY THIS FILE NEEDS A SECOND VENV. ``e2e/.venv`` (built by ``make
e2e-setup``, no ``LOCAL=1`` per this branch's controller ruling — routing is
cloud-side only, no runtime changes) intentionally carries only
``flashruntime``/``flashnode`` and the sklearn stack; it has never had
``flashml_cloud_api`` or its dependencies (``psycopg``, ``pyjwt``, a
Postgres server) installed, and extending it would be new machinery this
branch does not need for anything else. ``flashml-cloud/apps/api/.venv``
(built by ``make setup``, already on this machine) already has all of that,
including a pinned ``flashruntime==0.6.0`` — the exact version ``e2e/.venv``
resolves too, so both processes below speak the same protocol without this
file naming the pin itself anywhere. So the cloud API and its own
coordinator dependency start as SEPARATE SUBPROCESSES under that OTHER
interpreter — precisely ``scripts/dev.sh``'s own recipe
(``"$API_VENV/bin/python" -m uvicorn flashruntime.service.app:app`` /
``flashml_cloud_api.app:app``) — and this test drives them with nothing but
HTTP: ``requests`` (already in ``e2e/.venv``) for listings, submission and
routing inspection, and a real ``flashnode.executor.ExecutorLoop`` for the
one agent that actually claims and runs the task. Handing the cloud API and
the coordinator SEPARATE real processes, rather than importing
``flashml_cloud_api`` in-process, is also what lets the volunteer machine
authenticate the way a real one does: a machine TOKEN presented to the
cloud API's agent-proxy routes, not a coordinator-level credential the
marketplace has no notion of (``settings.py``'s ``public_api_url`` docstring
spells out why the two are different doors).

Postgres schema/enrolment setup (``priced_pool_routing_seed.py``, run under
the SAME ``apps/api/.venv``) uses the real, unmodified
``flashml_cloud_api.migrate`` runner and the real
``enrolment.start_device_code``/``approve_device_code``/``redeem_device_code``
functions — the same ones ``apps/api/tests/test_agent_proxy.py``'s ``_enrol``
helper calls — never a hand-inserted machine row or a hand-rolled token
hash. Everything this test actually makes ASSERTIONS about (listings, job
submission, routing inspection, real task completion) happens over HTTP
against the live server, per the brief's "seed listings through the
listings HTTP route, not SQL" instruction.

THE SCENARIO (task-6-brief.md, restated): two enrolled agents whose machines
are LISTED at different asks — one clearly cheap, one clearly expensive —
plus a third enrolled agent that is never listed at all. A priced pool job
(``gpus: 0``, ``price.max_per_hour`` set above the cheap ask and below the
expensive one) is submitted through ``POST /v1alpha1/jobs/from-upload`` —
the real compiler path (``compile_to_jobspec``), not a hand-built JobSpec
(see the workspace memory on why hand-built specs hide real breakage). Then:

  (a) the submit response's ``routing.book`` ranks the cheap listing first
      (``excluded: null``, ``tasks_assigned: 1``) and marks the expensive
      one ``"ask-above-cap"``;
  (b) ``GET /jobs/{id}/routing`` shows a ``matches`` row for the cheap
      machine at ITS OWN ask (``agreed_zc_per_hour`` — the host's price, not
      the buyer's cap) and a ``live_book`` that agrees with (a);
  (c) the job still completes — claimed and run by the UNLISTED third
      machine, which M1 always allowed and this branch must not have
      broken. ``ArgvDockerRunner`` is real Docker (colima on this machine);
      ``from-upload`` always compiles a ``command`` workload, and
      ``CommandRecipe`` fixes its isolation tier at ``sandboxed`` — not
      configurable from flashml.yaml — so there is no lighter runner that
      would still exercise the real compiled path.

Two things this test deliberately leaves to their own follow-ups, named so
nobody mistakes their absence here for an oversight: GPU capability classes
are blocked on the 0.6.1 runtime pin (``routing.GpuRoutingUnavailable`` —
this file only ever submits ``gpus: 0``), and per-gate placement reasons
(why a *specific* gate refused a specific node) are an upstream
``flashruntime.scheduler`` concern this repo does not own.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path

import pytest
import requests

from flashnode.executor import CoordinatorClient, ExecutorLoop
from flashnode.executor.argv_runner import ArgvDockerRunner
from flashnode.inventory.capabilities import discover

WORKSPACE = Path(__file__).resolve().parent.parent
API_ROOT = WORKSPACE / "flashml-cloud" / "apps" / "api"
API_VENV_PYTHON = API_ROOT / ".venv" / "bin" / "python"
SEED_SCRIPT = Path(__file__).resolve().parent / "priced_pool_routing_seed.py"

#: Same HS256 shared secret shape ``apps/api/tests`` uses throughout
#: (``test_market_routes.py``, ``test_agent_proxy.py``, ...). Verification
#: of an HS256 token never touches Supabase's JWKS endpoint — see
#: ``auth.verify_supabase_jwt``'s dispatch on the token's own ``alg``
#: header — so this stays fully local, exactly like the ephemeral Postgres
#: it is paired with.
JWT_SECRET = "e2e-priced-pool-routing-shared-secret-abcdef0123"

#: Any value works: the ``coordinator`` fixture (conftest.py) runs the open,
#: no-auth self-hosted profile (no ``FLASHML_REQUIRE_NODE_AUTH``), so this
#: token is never actually checked by the process on the other end of it —
#: it exists only because ``Settings.from_env`` refuses to boot with auth on
#: and this field empty.
COORDINATOR_OPERATOR_TOKEN = "e2e-operator-token"

REQUIRED_PG_BINARIES = ("initdb", "pg_ctl", "pg_isready")

CHEAP_ASK_ZC = 100          # 0.10 ZC/hr
EXPENSIVE_ASK_ZC = 500      # 0.50 ZC/hr
PRICE_CAP_MAX_PER_HOUR = "0.2"  # 200 millicredits — above the cheap ask, below the expensive one


def _require_local_postgres() -> None:
    """Same skip-honesty contract as ``apps/api/tests/conftest.py``'s
    ``postgres_dsn`` fixture: name the missing binary, never fall back to a
    mock. This scenario's assertions are about real listing/bid/match rows
    surviving a real HTTP round trip, which a mock database cannot stand in
    for."""
    missing = [b for b in REQUIRED_PG_BINARIES if shutil.which(b) is None]
    if missing:
        pytest.skip(
            "Missing required local Postgres binaries for the ephemeral "
            f"test database: {', '.join(missing)}. Install PostgreSQL "
            "locally (e.g. `brew install postgresql@14`) to run this "
            "scenario; it will not fall back to a mock."
        )


def _require_api_venv() -> None:
    """The cloud API's OWN venv, not e2e/.venv — see the module docstring
    for why. Mirrors ``scripts/dev.sh``'s own missing-venv message."""
    if not API_VENV_PYTHON.exists():
        pytest.skip(
            f"no venv at {API_ROOT / '.venv'} — this scenario runs the real "
            "flashml_cloud_api server, which needs its own dependencies "
            "(psycopg, fastapi, pyjwt) that e2e/.venv deliberately does not "
            "carry. Run `make setup` at the repo root (or `cd "
            "flashml-cloud/apps/api && uv venv .venv && uv pip install -e "
            "../../../flashruntime -e '.[dev]'`) first."
        )


def _require_docker() -> None:
    """``from-upload`` always compiles a ``command`` workload, and
    ``CommandRecipe`` fixes its isolation tier at ``sandboxed`` — real
    Docker is the only way a claimed task in this scenario can actually
    run. Skip cleanly, naming the reason, rather than hanging in a claim
    loop nothing will ever answer."""
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not on PATH — required to run the compiled "
                     "command task for real (ArgvDockerRunner)")
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=10, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        pytest.skip("docker daemon not reachable (`docker info` failed) — "
                     "start Docker Desktop or colima to run this scenario")


def _free_port() -> int:
    """Same bind-then-release pattern as ``e2e/conftest.py``'s own."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _hs256_jwt(user_id: str, *, secret: str = JWT_SECRET, ttl: float = 3600.0) -> str:
    """A Supabase-shaped access token, built with nothing but stdlib —
    e2e/.venv carries no `pyjwt` (see the module docstring). HS256, `sub`,
    `aud: authenticated`, `exp` in the future: exactly the three claims
    `verify_supabase_jwt` requires present, decoded against `JWT_SECRET`
    with no network call — the SAME dispatch `apps/api/tests` rely on."""
    header = _b64u(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64u(json.dumps({
        "sub": user_id, "aud": "authenticated", "exp": time.time() + ttl,
    }).encode())
    signing_input = f"{header}.{payload}".encode()
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64u(signature)}"


def _auth_header(user_id: str) -> dict:
    return {"Authorization": f"Bearer {_hs256_jwt(user_id)}"}


def _wait_until(predicate, *, timeout: float = 60.0, interval: float = 1.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s; last observation: {last!r}")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def postgres_dsn(tmp_path: Path):
    """A real, throwaway local Postgres — same recipe as
    ``apps/api/tests/conftest.py``'s fixture of the same name, reimplemented
    here as pure subprocess calls (``e2e/.venv`` has no ``psycopg`` to run
    that fixture's own code with). Schema application and seeding happen
    separately, via ``priced_pool_routing_seed.py`` under the API's own
    venv."""
    _require_local_postgres()

    data_dir = tmp_path / "pgdata"
    log_path = tmp_path / "pg.log"
    port = _free_port()
    # Same short-named socket-dir trick as the fixture this mirrors: macOS
    # caps a unix socket path at ~103 bytes, and pytest's tmp_path nests
    # deep enough to blow that on its own.
    socket_dir = Path(tempfile.mkdtemp(prefix="flashml-e2e-pg-sock-"))
    started = False
    try:
        init = subprocess.run(
            ["initdb", "-D", str(data_dir), "-U", "postgres", "--auth=trust", "-E", "UTF8"],
            capture_output=True, text=True,
        )
        if init.returncode != 0:
            raise RuntimeError(f"initdb failed:\n{init.stdout}\n{init.stderr}")

        start = subprocess.run(
            ["pg_ctl", "-D", str(data_dir),
             "-o", f"-p {port} -k {socket_dir} -c listen_addresses=127.0.0.1",
             "-l", str(log_path), "start"],
            capture_output=True, text=True,
        )
        if start.returncode != 0:
            raise RuntimeError(f"pg_ctl start failed:\n{start.stdout}\n{start.stderr}")
        started = True

        deadline = time.monotonic() + 20.0
        ready = False
        while time.monotonic() < deadline:
            if subprocess.run(["pg_isready", "-h", "127.0.0.1", "-p", str(port)],
                               capture_output=True).returncode == 0:
                ready = True
                break
            time.sleep(0.2)
        if not ready:
            log = log_path.read_text() if log_path.exists() else "(no log file)"
            raise RuntimeError(f"local Postgres on 127.0.0.1:{port} never became "
                                f"ready in 20s. Server log:\n{log}")

        yield f"postgresql://postgres@127.0.0.1:{port}/postgres"
    finally:
        if started:
            subprocess.run(["pg_ctl", "-D", str(data_dir), "-m", "immediate", "stop"],
                            capture_output=True)
        shutil.rmtree(socket_dir, ignore_errors=True)


@pytest.fixture()
def seed(postgres_dsn):
    """Migrates the ephemeral database and enrols the scenario's three
    machines through the real device-code flow, run under the API's own
    venv. See ``priced_pool_routing_seed.py`` for why this cannot run
    in-process here."""
    _require_api_venv()
    result = subprocess.run(
        [str(API_VENV_PYTHON), str(SEED_SCRIPT), postgres_dsn],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"priced_pool_routing_seed.py failed (exit {result.returncode}):\n"
            f"{result.stderr}"
        )
    return json.loads(result.stdout)


@pytest.fixture()
def cloud_api(coordinator, postgres_dsn, seed):
    """The real ``flashml_cloud_api`` ASGI app, as a real uvicorn
    subprocess under the API's own venv — ``scripts/dev.sh``'s exact
    recipe, pointed at the real coordinator subprocess ``e2e/conftest.py``
    already gives every other file in this suite, and at the ephemeral
    Postgres above instead of Supabase.

    Depends on ``seed`` (not just ``postgres_dsn``) so the schema exists
    and the scenario's accounts/machines are enrolled BEFORE this process
    boots — an app instance starting against an unmigrated database has its
    own background sweeps (the rented-capacity reconciler) log a harmless
    but noisy ``UndefinedTable`` on their first tick otherwise.
    """
    _require_api_venv()
    port = _free_port()
    env = {
        **os.environ,
        "SUPABASE_URL": "https://e2e-priced-pool-routing.supabase.co",
        "SUPABASE_JWT_SECRET": JWT_SECRET,
        "COORDINATOR_URL": coordinator.base_url,
        "COORDINATOR_OPERATOR_TOKEN": COORDINATOR_OPERATOR_TOKEN,
        "DATABASE_URL": postgres_dsn,
        "FLASHML_CONSOLE_URL": "https://console.example",
        "FLASHML_REQUIRE_AUTH": "true",
    }
    proc = subprocess.Popen(
        [str(API_VENV_PYTHON), "-m", "uvicorn", "flashml_cloud_api.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(API_ROOT), env=env,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30.0
        healthy = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"cloud API process exited early with code {proc.returncode}"
                )
            try:
                r = requests.get(f"{base_url}/healthz", timeout=1)
                if r.status_code == 200:
                    healthy = True
                    break
            except requests.RequestException:
                pass
            time.sleep(0.2)
        if not healthy:
            raise RuntimeError("cloud API did not become healthy in 30s")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


# ---------------------------------------------------------------------------
# workspace fixture: a real, minimal flashml.yaml + entrypoint
# ---------------------------------------------------------------------------

TOP = "priced-pool-routing-e2e"

FLASHML_YAML = f"""\
version: 1
name: priced-pool-routing-e2e
image: python-slim
entrypoint: train.py
resources:
  gpus: 0
price:
  max_per_hour: {PRICE_CAP_MAX_PER_HOUR}
"""

#: Writes to the runner's real output location (`/work/out`), same shape as
#: `CLEAN_TRAIN_PY` in `test_repo_job_contract.py`/`test_routing_routes.py`
#: — stdlib only, so it runs unmodified inside the curated `python-slim`
#: image with no dependency installation.
TRAIN_PY = """\
import json
import pathlib

out = pathlib.Path("/work/out")
out.mkdir(parents=True, exist_ok=True)
(out / "metrics.json").write_text(json.dumps({"ok": True}))
"""


def _workspace_tarball() -> bytes:
    """A GitHub-shaped tarball: one top-level wrapper directory, exactly
    what `POST /jobs/from-upload` requires (and what GitHub's own tarballs
    look like) — see that route's docstring in app.py."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=TOP + "/")
        info.type = tarfile.DIRTYPE
        tar.addfile(info)
        for name, content in (("flashml.yaml", FLASHML_YAML), ("train.py", TRAIN_PY)):
            payload = content.encode()
            member = tarfile.TarInfo(name=f"{TOP}/{name}")
            member.size = len(payload)
            member.type = tarfile.REGTYPE
            tar.addfile(member, io.BytesIO(payload))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# the scenario
# ---------------------------------------------------------------------------


def test_priced_pool_job_routes_against_the_listing_book(coordinator, cloud_api, seed):
    """The full rehearsal: two listed machines at different asks, one
    unlisted machine, one priced pool job submitted through the real
    compiler, and the book/bid/match/completion all inspected over real
    HTTP against a real coordinator and a real cloud API process."""
    _require_docker()

    users = seed["users"]
    machines = seed["machines"]
    api = cloud_api

    # -- seed listings through the listings HTTP route, not SQL -------------
    cheap_listing = requests.post(
        f"{api}/v1alpha1/market/listings",
        json={"machine_id": machines["cheap"]["id"],
              "ask_zc_per_hour": CHEAP_ASK_ZC, "max_concurrent_tasks": 1},
        headers=_auth_header(users["host_cheap"]),
    )
    assert cheap_listing.status_code == 201, cheap_listing.text
    cheap_listing_id = cheap_listing.json()["id"]

    expensive_listing = requests.post(
        f"{api}/v1alpha1/market/listings",
        json={"machine_id": machines["expensive"]["id"],
              "ask_zc_per_hour": EXPENSIVE_ASK_ZC, "max_concurrent_tasks": 1},
        headers=_auth_header(users["host_expensive"]),
    )
    assert expensive_listing.status_code == 201, expensive_listing.text
    expensive_listing_id = expensive_listing.json()["id"]

    # The third machine is enrolled (seed script) and never listed — it
    # stays entirely out of the book on purpose (M1: unlisted machines
    # still claim pool work for free).

    # -- submit a real flashml.yaml pool job through the real compiler ------
    submit = requests.post(
        f"{api}/v1alpha1/jobs/from-upload",
        files={"workspace": ("workspace.tar.gz", _workspace_tarball(), "application/gzip")},
        headers=_auth_header(users["submitter"]),
    )
    assert submit.status_code == 201, submit.text
    submitted = submit.json()
    job_id = submitted["job_id"]

    # -- (a) the submit response's routing.book ranks correctly -------------
    routing = submitted.get("routing")
    assert routing is not None, "no 'routing' block on a priced job's submit response"
    assert routing["state"] == "routed", routing
    assert routing["capability_class"] == "cpu-small"
    assert routing["tasks_wanted"] == 1
    assert routing["tasks_filled"] == 1
    assert routing["tasks_unfilled"] == 0
    bid_id = routing["bid_id"]
    assert bid_id

    book = routing["book"]
    book_by_listing = {row["listing_id"]: row for row in book}
    assert cheap_listing_id in book_by_listing
    assert expensive_listing_id in book_by_listing

    cheap_row = book_by_listing[cheap_listing_id]
    assert cheap_row["machine_id"] == machines["cheap"]["id"]
    assert cheap_row["ask_zc_per_hour"] == CHEAP_ASK_ZC
    assert cheap_row["excluded"] is None
    assert cheap_row["tasks_assigned"] == 1

    expensive_row = book_by_listing[expensive_listing_id]
    assert expensive_row["machine_id"] == machines["expensive"]["id"]
    assert expensive_row["excluded"] == "ask-above-cap"
    assert expensive_row["tasks_assigned"] == 0

    # ranked first means it sorts ahead of the excluded listing in the book
    assert book.index(cheap_row) < book.index(expensive_row), (
        "the cheap listing must rank ahead of the excluded expensive one"
    )

    # -- (b) a match row exists for the cheap machine at ITS ask ------------
    routing_get = requests.get(f"{api}/v1alpha1/jobs/{job_id}/routing",
                                headers=_auth_header(users["submitter"]))
    assert routing_get.status_code == 200, routing_get.text
    inspected = routing_get.json()

    assert inspected["bid"] is not None
    assert inspected["bid"]["id"] == bid_id
    assert inspected["bid"]["capability_class"] == "cpu-small"
    assert inspected["bid"]["tasks_wanted"] == 1

    matches = inspected["matches"]
    assert len(matches) == 1, matches
    match = matches[0]
    assert match["bid_id"] == bid_id
    assert match["listing_id"] == cheap_listing_id
    assert match["machine_id"] == machines["cheap"]["id"]
    # the host's OWN ask, never the buyer's price cap — the buyer capped at
    # 200 millicredits/hr, the match is agreed at the cheap host's 100
    assert match["agreed_zc_per_hour"] == CHEAP_ASK_ZC
    assert match["state"] == "granted"

    # -- (d) GET /routing's live_book agrees with the submit response -------
    live_book = inspected["live_book"]
    assert "plan" not in live_book
    assert live_book["book"] == routing["book"], (
        "GET /jobs/{id}/routing's live_book must agree with the submit "
        "response's routing.book against the unchanged listing set"
    )
    assert live_book["nearest_miss"] == routing["nearest_miss"]

    # -- (c) the job still completes, claimed by the UNLISTED machine -------
    free_node_id = f"routing-e2e-free-{seed['run']}"
    client = CoordinatorClient(api, token=machines["free"]["token"])
    registration = discover(
        free_node_id, kubernetes_node="", node_meta=None,
        argv_capable=True, module_capable=False,
        unsandboxed_argv_capable=False, can_install_dependencies=False,
    )
    client.register(registration)

    workdir_base = Path.home() / ".cache" / "flashnode-e2e-routing" / seed["run"]
    runner = ArgvDockerRunner()
    loop = ExecutorLoop(
        client, free_node_id, runner=runner,
        poll_seconds=0.5, node_heartbeat_seconds=5.0, workdir_base=workdir_base,
    )
    thread = threading.Thread(target=loop.run, daemon=True)
    thread.start()
    try:
        def _tasks():
            r = requests.get(f"{api}/v1alpha1/jobs/{job_id}/tasks",
                              headers=_auth_header(users["submitter"]))
            assert r.status_code == 200, r.text
            rows = r.json()
            if all(t["state"] in ("COMPLETED", "FAILED") for t in rows):
                return rows
            return None

        tasks = _wait_until(_tasks, timeout=90.0, interval=1.0)
    finally:
        loop.stop_event.set()
        thread.join(timeout=10.0)
        shutil.rmtree(workdir_base, ignore_errors=True)

    assert len(tasks) == 1
    assert tasks[0]["state"] == "COMPLETED", (
        f"routing must not break the run: task ended in {tasks[0]['state']!r}"
    )
    # The point of M1: routing/matching is a pricing overlay, not a
    # placement gate. The task that actually ran was claimed by the
    # UNLISTED (never in the book) third machine, not either listed one.
    assert tasks[0]["node_id"] == free_node_id, (
        "expected the unlisted machine to have claimed and completed the "
        f"task; instead {tasks[0]['node_id']!r} did"
    )

    # The pricing entitlement for the cheap host is untouched by the free
    # claim above — nothing about M1's free-claim path should have moved
    # a match nobody with that listing's token ever claimed against.
    routing_get_after = requests.get(f"{api}/v1alpha1/jobs/{job_id}/routing",
                                      headers=_auth_header(users["submitter"]))
    assert routing_get_after.status_code == 200
    assert routing_get_after.json()["matches"][0]["state"] == "granted"
