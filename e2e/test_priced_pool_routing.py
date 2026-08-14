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

THE FIRST SCENARIO (task-6-brief.md, restated): two enrolled agents whose machines
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

THE SECOND SCENARIO (``test_priced_job_spills_from_cpu_small_into_cpu_large``)
covers what only exists once a job accepts an ORDERED LIST of classes rather
than one. A ``gpus: 0`` job with no ``cpus`` derives
``("cpu-small", "cpu-large")``, and the first scenario above never reaches
the second entry: its single task fits in the first class, so the walk stops
there. The spill scenario gives the job a ``sweep`` (eight tasks) and puts
one listing in EACH class — the small one selling a single concurrent task,
the large one asking more but still under the cap — so the preferred class
demonstrably cannot hold the job and the remainder walks on. It asserts the
book carries labelled rows from both classes in walk order, that the fill
landing in ``cpu-large`` is agreed at THAT host's ask, that one bid exists
per class walked (each asked for only what its predecessors left), and that
``GET /jobs/{id}/routing`` re-ranks under the objective the BID STORED
(migration 0032) rather than the engine's own default. It runs no container:
its assertions are all about what the walk decided, and the first scenario
already proves a routed job really executes.

Both scenarios submit ``gpus: 0``. GPU capability classes are now real in
the ladder (``marketplace.CAPABILITY_CLASSES``, and
``routing._gpu_classes_cheapest_first`` orders them for a GPU job), so the
gap is no longer the engine — it is that this harness has no GPU host to
enrol, which is a fixture problem and not a routing one. Per-gate placement
reasons (why a *specific* gate refused a specific node) remain an upstream
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

#: The `cpu-large` ask in the spill scenario: UNDER the same 0.2 ZC/hr cap,
#: so the spill is a genuine fill and not another `ask-above-cap` exclusion.
#: The point of that scenario is that work walks into the next class and is
#: agreed at THAT host's price, which only happens if the price clears.
LARGE_ASK_ZC = 150          # 0.15 ZC/hr

#: Sweep width for the spill scenario, chosen against
#: `marketplace.unproven_task_budget`: it is `max(1, int(1/4 * tasks))`, and
#: every host here is unproven (no resolved attempts, so no acceptance rate).
#: At 8 tasks the JOB-level allowance is 2 — one task for the `cpu-small`
#: listing and one for the `cpu-large` listing it spills into. A smaller
#: sweep would cap the whole walk at a single task and no fill could ever
#: reach the second class, which would make a green test out of a scenario
#: that never happened.
SPILL_TASKS = 8

#: Deliberately NOT `marketplace.DEFAULT_RANK_OBJECTIVE` ("cheapest"), and
#: not `flashml_yaml.DEFAULT_PRICE_OBJECTIVE` ("balanced") either. The bid
#: stores whatever the yaml named (migration 0032), and
#: `GET /jobs/{id}/routing` re-ranks the live book off that stored column.
#: If the route ever falls back to the engine default again, this value is
#: what makes the difference visible instead of coincidentally identical.
SPILL_OBJECTIVE = "fastest"

#: Core counts that put the two seeded machines in DIFFERENT books.
#: `marketplace.CPU_LARGE_MIN_CORES` is 8; a machine at or above it classes
#: `cpu-large`, below it `cpu-small`.
SMALL_MACHINE_CORES = 2
LARGE_MACHINE_CORES = 16


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

#: The spill scenario's own real config. Same `gpus: 0` (so
#: `routing.job_accept_classes` derives the ordered pair `cpu-small`,
#: `cpu-large`), but a `sweep` so the job is worth more than one task, and an
#: explicit `price.objective` so the stored-objective assertion has something
#: to distinguish. The sweep key becomes a `--lr` flag on the compiled
#: command; `train.py` ignores argv, which is fine — this scenario asserts
#: about the book, the bids and the matches, and never runs the tasks.
SPILL_FLASHML_YAML = f"""\
version: 1
name: priced-pool-spill-e2e
image: python-slim
entrypoint: train.py
resources:
  gpus: 0
sweep:
  lr: [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08]
price:
  max_per_hour: {PRICE_CAP_MAX_PER_HOUR}
  objective: {SPILL_OBJECTIVE}
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


def _workspace_tarball(flashml_yaml: str = FLASHML_YAML) -> bytes:
    """A GitHub-shaped tarball: one top-level wrapper directory, exactly
    what `POST /jobs/from-upload` requires (and what GitHub's own tarballs
    look like) — see that route's docstring in app.py.

    The yaml is a parameter so the multi-class scenario below can submit its
    own real config through the SAME upload path, rather than reaching past
    the compiler with a hand-built JobSpec."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=TOP + "/")
        info.type = tarfile.DIRTYPE
        tar.addfile(info)
        for name, content in (("flashml.yaml", flashml_yaml), ("train.py", TRAIN_PY)):
            payload = content.encode()
            member = tarfile.TarInfo(name=f"{TOP}/{name}")
            member.size = len(payload)
            member.type = tarfile.REGTYPE
            tar.addfile(member, io.BytesIO(payload))
    return buf.getvalue()


def _register_reporting_cores(api: str, machine: dict, node_id: str, cores: float) -> None:
    """Register ``machine`` through the REAL agent-proxy route, reporting
    ``cores`` CPUs.

    A host does not choose which book it is listed in. The class is derived
    by ``marketplace.capability_class`` from ``machines.capabilities``, and
    that column is written by exactly one thing: this registration route
    (``db.set_machine_capabilities``, whose allowlist carries ``cpu_cores``).
    So the only honest way to get a machine into the ``cpu-large`` book is
    for the machine to register as one, the same call a real flashnode makes
    on start-up — never an UPDATE against ``machines``.

    ``discover()`` builds the whole real registration and then reports THIS
    Mac's actual core count, which would file every seeded machine in one
    class and leave the spill untestable. Overriding that single field is
    the smallest possible deviation: everything else on the wire — the
    protocol model, the runner capabilities, the environment classification —
    is exactly what the agent would have sent.
    """
    registration = discover(
        node_id, kubernetes_node="", node_meta=None,
        argv_capable=True, module_capable=False,
        unsandboxed_argv_capable=False, can_install_dependencies=False,
    )
    registration = registration.model_copy(update={
        "capabilities": registration.capabilities.model_copy(
            update={"cpu_cores": float(cores)}
        ),
    })
    CoordinatorClient(api, token=machine["token"]).register(registration)


def _create_listing(api: str, *, machine_id: str, owner_id: str, ask: int,
                    max_concurrent_tasks: int) -> dict:
    """One open ask, through the listings HTTP route — never SQL."""
    response = requests.post(
        f"{api}/v1alpha1/market/listings",
        json={"machine_id": machine_id, "ask_zc_per_hour": ask,
              "max_concurrent_tasks": max_concurrent_tasks},
        headers=_auth_header(owner_id),
    )
    assert response.status_code == 201, response.text
    return response.json()


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
    # A ``gpus: 0`` job that names no ``cpus`` derives the ORDERED pair
    # ``("cpu-small", "cpu-large")`` — small first because it is the cheaper
    # supply, large second so a thin small book cannot strand a job any
    # workstation could run (``routing.job_accept_classes``). Neither seeded
    # machine has ever registered, so ``machines.capabilities`` carries no
    # ``cpu_cores`` and ``marketplace.capability_class`` files both under
    # ``cpu-small``: the walk fills the single task in the first class it
    # visits and never reaches ``cpu-large``. The multi-class walk itself —
    # both classes visited, the book carrying rows from each — is the
    # sibling scenario below.
    assert routing["accept"] == ["cpu-small", "cpu-large"]
    assert routing["tasks_wanted"] == 1
    assert routing["tasks_filled"] == 1
    assert routing["tasks_unfilled"] == 0

    # One bid per class the walk actually VISITED, not per class accepted:
    # the task was filled in ``cpu-small``, so the walk stopped there and
    # posted exactly one bid.
    assert [b["capability_class"] for b in routing["bids"]] == ["cpu-small"]
    bid_id = routing["bids"][0]["bid_id"]
    assert bid_id

    book = routing["book"]
    book_by_listing = {row["listing_id"]: row for row in book}
    assert cheap_listing_id in book_by_listing
    assert expensive_listing_id in book_by_listing

    cheap_row = book_by_listing[cheap_listing_id]
    assert cheap_row["machine_id"] == machines["cheap"]["id"]
    assert cheap_row["ask_zc_per_hour"] == CHEAP_ASK_ZC
    assert cheap_row["capability_class"] == "cpu-small"
    assert cheap_row["excluded"] is None
    assert cheap_row["tasks_assigned"] == 1

    expensive_row = book_by_listing[expensive_listing_id]
    assert expensive_row["machine_id"] == machines["expensive"]["id"]
    assert expensive_row["capability_class"] == "cpu-small"
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

    # ``bids``, plural, each carrying its OWN matches — one per class the
    # walk visited, in creation (walk) order.
    inspected_bids = inspected["bids"]
    assert len(inspected_bids) == 1, inspected_bids
    inspected_bid = inspected_bids[0]
    assert inspected_bid["id"] == bid_id
    assert inspected_bid["capability_class"] == "cpu-small"
    assert inspected_bid["tasks_wanted"] == 1

    matches = inspected_bid["matches"]
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
    # ``class_plans`` carries MatchPlan dataclasses, which are not JSON-safe;
    # the handler pops it before responding and it must never leak.
    assert "class_plans" not in live_book
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
    assert routing_get_after.json()["bids"][0]["matches"][0]["state"] == "granted"


def test_priced_job_spills_from_cpu_small_into_cpu_large(coordinator, cloud_api, seed):
    """The multi-class walk, through the same authoring surface.

    The sibling scenario above proves a priced job routes against ONE book.
    This one proves the part that only exists once a job accepts an ORDERED
    LIST of classes (``routing.job_accept_classes`` /
    ``routing.plan_pool_routing``): a job whose first-choice class cannot
    hold it walks on into the next one, and the work it places there is
    agreed at THAT host's ask.

    No Docker and no executor here on purpose. Every assertion is about the
    book, the bids and the matches — what the walk decided — and none of
    them is about a task running, which the sibling scenario already covers
    end to end. Adding a second real container run would double the
    scenario's cost to re-prove something unrelated to spilling.
    """
    users = seed["users"]
    machines = seed["machines"]
    api = cloud_api
    run = seed["run"]

    # -- put the two machines in DIFFERENT books, by registering as agents --
    _register_reporting_cores(
        api, machines["cheap"], f"routing-e2e-cheap-{run}", SMALL_MACHINE_CORES,
    )
    _register_reporting_cores(
        api, machines["expensive"], f"routing-e2e-expensive-{run}", LARGE_MACHINE_CORES,
    )

    # -- one open ask in each class, both UNDER the job's cap ---------------
    # `max_concurrent_tasks=1` on the small listing is what makes the spill
    # happen at all: the class the job prefers physically cannot hold the
    # whole sweep, so the remainder has to walk on.
    small_listing = _create_listing(
        api, machine_id=machines["cheap"]["id"], owner_id=users["host_cheap"],
        ask=CHEAP_ASK_ZC, max_concurrent_tasks=1,
    )
    assert small_listing["capability_class"] == "cpu-small", small_listing
    small_listing_id = small_listing["id"]

    large_listing = _create_listing(
        api, machine_id=machines["expensive"]["id"], owner_id=users["host_expensive"],
        ask=LARGE_ASK_ZC, max_concurrent_tasks=4,
    )
    assert large_listing["capability_class"] == "cpu-large", large_listing
    large_listing_id = large_listing["id"]

    # -- submit the real multi-task flashml.yaml through the real compiler --
    submit = requests.post(
        f"{api}/v1alpha1/jobs/from-upload",
        files={"workspace": ("workspace.tar.gz",
                             _workspace_tarball(SPILL_FLASHML_YAML),
                             "application/gzip")},
        headers=_auth_header(users["submitter"]),
    )
    assert submit.status_code == 201, submit.text
    submitted = submit.json()
    job_id = submitted["job_id"]

    routing = submitted.get("routing")
    assert routing is not None, "no 'routing' block on a priced job's submit response"
    assert routing["state"] == "routed", routing
    assert routing["accept"] == ["cpu-small", "cpu-large"]
    assert routing["objective"] == SPILL_OBJECTIVE, (
        "the submit response must publish the objective the yaml named"
    )
    assert routing["tasks_wanted"] == SPILL_TASKS, (
        "the sweep must have expanded into the task count this scenario's "
        "unproven-budget arithmetic was chosen against"
    )

    # -- (a) the book carries rows from BOTH classes, labelled, small first -
    book = routing["book"]
    assert [row["capability_class"] for row in book] == ["cpu-small", "cpu-large"], (
        "the book is every walked class's rows concatenated in WALK order, "
        "and cpu-small is the job's first-choice class"
    )
    by_listing = {row["listing_id"]: row for row in book}
    assert set(by_listing) == {small_listing_id, large_listing_id}

    small_row = by_listing[small_listing_id]
    large_row = by_listing[large_listing_id]
    assert small_row["capability_class"] == "cpu-small"
    assert large_row["capability_class"] == "cpu-large"

    # -- (b) the spill: the small class fills what it can, the rest walks ---
    assert small_row["excluded"] is None, small_row
    assert small_row["tasks_assigned"] == 1, (
        "the cpu-small listing sells one concurrent task, so that is all the "
        "job's first-choice class can hold"
    )
    assert small_row["ask_zc_per_hour"] == CHEAP_ASK_ZC

    assert large_row["excluded"] is None, (
        "the cpu-large ask is under the cap, so the spill must be a real "
        "fill and not an exclusion"
    )
    assert large_row["tasks_assigned"] == 1, (
        "the job-level unproven-host allowance is 2 tasks across the WHOLE "
        "walk (8 tasks x 1/4); cpu-small spent one, so one is left to spend "
        "in cpu-large"
    )
    assert large_row["ask_zc_per_hour"] == LARGE_ASK_ZC

    assert routing["tasks_filled"] == 2, routing
    assert routing["tasks_unfilled"] == SPILL_TASKS - 2, routing

    # One bid per class the walk VISITED, in walk order.
    assert [b["capability_class"] for b in routing["bids"]] == ["cpu-small", "cpu-large"]
    small_bid_id, large_bid_id = (b["bid_id"] for b in routing["bids"])

    # -- (c) GET /routing: one bid per walked class, stored objective -------
    routing_get = requests.get(f"{api}/v1alpha1/jobs/{job_id}/routing",
                                headers=_auth_header(users["submitter"]))
    assert routing_get.status_code == 200, routing_get.text
    inspected = routing_get.json()

    inspected_bids = inspected["bids"]
    assert [b["capability_class"] for b in inspected_bids] == ["cpu-small", "cpu-large"]
    assert [b["id"] for b in inspected_bids] == [small_bid_id, large_bid_id]
    # The first class is asked for the whole job; each later one only for
    # what its predecessors could not fill.
    assert inspected_bids[0]["tasks_wanted"] == SPILL_TASKS
    assert inspected_bids[1]["tasks_wanted"] == SPILL_TASKS - 1
    # Stored on the ROW (migration 0032), not re-derived from the config.
    assert [b["objective"] for b in inspected_bids] == [SPILL_OBJECTIVE, SPILL_OBJECTIVE]

    small_matches = inspected_bids[0]["matches"]
    large_matches = inspected_bids[1]["matches"]
    assert len(small_matches) == 1, small_matches
    assert len(large_matches) == 1, large_matches

    assert small_matches[0]["listing_id"] == small_listing_id
    assert small_matches[0]["capability_class"] == "cpu-small"
    assert small_matches[0]["agreed_zc_per_hour"] == CHEAP_ASK_ZC
    assert small_matches[0]["state"] == "granted"

    # The whole point of the spill: the work that could not fit in the
    # preferred class is entitled in the next one at THAT host's ask — not
    # at the buyer's cap, and not at the cheaper class's price.
    assert large_matches[0]["listing_id"] == large_listing_id
    assert large_matches[0]["machine_id"] == machines["expensive"]["id"]
    assert large_matches[0]["capability_class"] == "cpu-large"
    assert large_matches[0]["agreed_zc_per_hour"] == LARGE_ASK_ZC
    assert large_matches[0]["state"] == "granted"

    # -- the live book is re-explained under the STORED objective -----------
    live_book = inspected["live_book"]
    assert "class_plans" not in live_book
    assert live_book["objective"] == SPILL_OBJECTIVE, (
        "GET /jobs/{id}/routing must re-rank under the objective the BID "
        "stored, not the engine's own default"
    )
    assert live_book["objective"] != "cheapest", (
        "guard against the pre-0032 behaviour reappearing: the engine "
        "fallback and the objective this job asked for are different words "
        "on purpose"
    )
    assert live_book["formula"] == routing["formula"]
    assert live_book["accept"] == ["cpu-small", "cpu-large"]
    assert live_book["book"] == routing["book"], (
        "with nothing in the book having moved since submission, the "
        "recomputation must reproduce the submit response's book exactly — "
        "across BOTH walked classes"
    )
