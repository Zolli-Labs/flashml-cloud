# Preflight and From-Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a developer validate a `flashml.yaml` without pushing anything,
and submit a job from a working tree that lives in no public repo.

**Architecture:** Two new routes on the existing FastAPI control plane, both
reusing the machinery `from-repo` already runs on. `POST /v1alpha1/preflight`
writes the caller's two supplied files into a temp dir and calls the same
`parse_flashml_yaml` + `preflight` pair — creating no job, no artifact and no
coordinator call. `POST /v1alpha1/jobs/from-upload` takes a gzipped tarball
where `from-repo` takes a repo reference, and is byte-identical to it from the
extraction step onward.

**Tech Stack:** FastAPI + psycopg 3 + Postgres, pytest against a real ephemeral
Postgres, `tarfile` via the existing `repo.extract_safely`.

This is **the API half of Plan 2** from
`docs/superpowers/specs/2026-08-10-developer-surface-and-mcp-design.md`
(§5 and §7). The other half — the public `flashml` package: typed client,
credential store, and the nine CLI verbs — is its own plan, written after this
one lands. **Why split:** these are two repos, two languages, and two test
runners, and each produces working software alone. The endpoints are usable
from `curl` the day they merge; the package cannot be built at all until they
exist. Keeping them in one document would also have meant one reviewer gating
both.

Plan 1 (`fmu_` developer identity) is a hard prerequisite and has shipped —
without it these routes are reachable only from a browser, which defeats the
purpose.

## Global Constraints

Every task's requirements implicitly include this section.

- **Both routes are `browser`-tagged and use `Depends(admitted_user)`**, the
  same gate `from-repo` uses. `fmu_` tokens reach them because `current_user`
  resolves them (Plan 1); no per-route auth work is needed or wanted.
- **`POST /v1alpha1/preflight` creates nothing.** No `jobs` row, no artifact,
  no coordinator call, no storage charge. This is the property the tests exist
  to pin, not a nice-to-have.
- **Never trust, quote, or log repo-controlled text unsanitised.** Every string
  that came from a caller's file passes through `safe_text(...)` before it
  reaches a response or a log line — a tar member name is attacker-chosen.
- **Findings come back together, never one per round trip.** `from-repo`'s
  docstring is explicit: "a user fixing four problems should need one more
  submit, not four." Both routes preserve that.
- **404, never 403, for a pool that exists but is not the caller's** — the
  `fetch_pool_for_member` doctrine, so a guess cannot distinguish "no such
  pool" from "not yours". Copy `from-repo`'s block verbatim, including the
  re-bind to the database's canonical uuid spelling.
- **Body caps are per-route and explicit.** JSON routes cap at
  `MAX_JSON_BODY_BYTES` (1 MiB); the upload route caps at
  `MAX_REPO_TARBALL_BYTES` (32 MiB). There is no global body middleware — the
  `proxy` helper's limit at `app.py:985` is machine-facing only and does not
  cover these routes.
- **Migrations are append-only.** This plan adds none. If you think you need
  one, you have misread the design — `from-upload` records provenance in the
  existing `jobs.source` JSON column.
- **Tests run from `flashml-cloud/apps/api/` with its `.venv`**:
  `.venv/bin/pytest -q`. The suite stands at **1029 passed** before this plan.

---

## What the spec got wrong about this repo

Verified 2026-08-10, before writing. Both corrections are load-bearing.

**1. `from-repo` has no storage gate.** Spec §7 says `from-upload` is identical
to `from-repo` including "the same storage gate". There is no such gate on
`from-repo`. `_storage_gate` (`app.py:431`) is called from exactly one place —
`app.py:1847`, inside `POST /v1alpha1/jobs`, the raw-spec route. `from-repo`
stages a multi-megabyte code tarball with no quota check at all.

This plan does **not** silently inherit that. Task 4 adds the gate to
`from-upload` (where the spec asks for it) and Task 5 adds it to `from-repo`
as its own reviewable change, because the two routes upload the same kind of
artifact and a gate on one but not the other is a difference nobody will be
able to justify later.

**2. `preflight()` reads from disk, it does not take source text.** Its
signature is `preflight(config: FlashmlConfig, repo_root: Path, image: CuratedImage)`
(`preflight.py:517`) and it resolves `config.entrypoint` under `repo_root`
itself (`_resolve_entrypoint`, `preflight.py:206`), refusing anything that
escapes the root. The endpoint therefore materialises the caller's bytes into a
temp directory rather than passing text — which also means the endpoint gets
that path-escape guard for free instead of reimplementing it.

---

## File Structure

**Modified — `flashml-cloud/apps/api/flashml_cloud_api/`**

| File | Responsibility |
|---|---|
| `app.py` | Two new routes plus one shared helper. No new module: both routes are thin orchestration over `flashml_yaml`, `images`, `preflight` and `repo`, exactly as `from-repo` is, and a new module would only move the seam. |

**Created — `flashml-cloud/apps/api/tests/`**

| File | Responsibility |
|---|---|
| `test_preflight_endpoint.py` | That the dry run validates correctly **and creates nothing**. |
| `test_jobs_from_upload.py` | The upload path: tarball handling, parity with `from-repo`, and the refusals. |

**No new files in `flashml_cloud_api/`.** The one helper this plan extracts,
`_preflight_supplied_files`, lives beside `_fetch_and_extract` and
`_read_config_text` in `app.py` because it is the same kind of thing: a small
blocking helper the route runs in a threadpool.

---

## Task 1: The preflight dry run

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py` (add the route
  after `submit_job_from_repo`, which ends around `app.py:2130`; add the helper
  beside `_read_config_text` at `app.py:250`)
- Test: `flashml-cloud/apps/api/tests/test_preflight_endpoint.py` (create)

**Interfaces:**
- Consumes: `parse_flashml_yaml`, `ConfigError` (`flashml_yaml`);
  `resolve_image`, `UnknownImage` (`images`); `preflight`, `safe_text`
  (`preflight`); `dbmod.fetch_pool_for_member`.
- Produces (HTTP contract, consumed by the CLI's `flashml check`):
  `POST /v1alpha1/preflight` →
  `{"findings": [{level, code, message}], "config": {...}, "ok": bool}`.

- [ ] **Step 1: Write the failing test**

Create `flashml-cloud/apps/api/tests/test_preflight_endpoint.py`:

```python
"""`POST /v1alpha1/preflight` — validation with nothing created.

The endpoint's whole value is that it is a pure function of the bytes it
was handed. The tests that matter most here are therefore the negative
ones: no jobs row, no artifact, no coordinator request. A dry run that
quietly submits is worse than no dry run at all.

Fixture wiring follows test_pools_api.py: the shared helpers live in
test_jobs_from_repo and are imported from there.
"""
from __future__ import annotations

import uuid

from test_jobs_from_repo import (  # noqa: F401 - fixtures
    _jwt,
    _new_user,
    db,
    make_client,
    settings,
    transport,
)

CLEAN_CONFIG = """\
version: 1
name: hello
image: python-slim
entrypoint: train.py
"""

CLEAN_ENTRYPOINT = "import json\nprint('hi')\n"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _check(client, token: str, **overrides):
    body = {
        "config": CLEAN_CONFIG,
        "entrypoint": CLEAN_ENTRYPOINT,
        "entrypoint_path": "train.py",
    }
    body.update(overrides)
    return client.post("/v1alpha1/preflight", json=body, headers=_bearer(token))


def _job_count(db, owner_id: str) -> int:
    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.jobs where owner_id = %s", (owner_id,)
        )
        return int(cur.fetchone()["n"])


def test_a_clean_config_passes_with_no_findings(make_client, db):
    client = make_client()
    r = _check(client, _jwt(_new_user(db)))
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["findings"] == []


def test_the_dry_run_creates_no_job_no_artifact_and_calls_no_coordinator(
    make_client, db, transport
):
    """The property the endpoint exists for."""
    client = make_client()
    owner = _new_user(db)
    before = _job_count(db, owner)

    assert _check(client, _jwt(owner)).status_code == 200

    assert _job_count(db, owner) == before
    assert transport.requests == []


def test_the_normalized_config_comes_back_so_a_caller_can_show_it(make_client, db):
    """Derived values — the resolved image, the round count, the sweep
    combination count — are computed by the parser and are not recoverable
    from the caller's own YAML."""
    client = make_client()
    r = _check(client, _jwt(_new_user(db)))
    config = r.json()["config"]
    assert config["name"] == "hello"
    assert config["image"] == "python-slim"
    assert config["entrypoint"] == "train.py"
    assert config["mode"] == "independent"


def test_an_unparseable_config_is_a_clean_400_naming_the_problem(make_client, db):
    client = make_client()
    r = _check(client, _jwt(_new_user(db)), config="version: 1\nname: [oh no\n")
    assert r.status_code == 400
    assert r.json()["detail"]


def test_a_v1_federated_config_still_reports_its_migration(make_client, db):
    """The parser refuses `rounds` by name with the replacement. That
    message is the single most useful thing this endpoint can return, so it
    must survive the trip."""
    client = make_client()
    r = _check(
        client,
        _jwt(_new_user(db)),
        config=(
            "version: 1\nname: fed\nimage: python-slim\n"
            "entrypoint: train.py\nmode: federated\nrounds: 5\n"
        ),
    )
    assert r.status_code == 400
    assert "epochs" in r.json()["detail"]


def test_an_unknown_image_lists_the_real_ones(make_client, db):
    client = make_client()
    r = _check(
        client,
        _jwt(_new_user(db)),
        config=CLEAN_CONFIG.replace("python-slim", "pytorch-quantum"),
    )
    assert r.status_code == 400
    assert "python-slim" in r.json()["detail"]


def test_a_networking_import_is_an_error_finding_not_a_400(make_client, db):
    """Findings are the answer, not an exception: the caller gets every
    objection at once and the request itself succeeded."""
    client = make_client()
    r = _check(client, _jwt(_new_user(db)), entrypoint="import requests\n")
    assert r.status_code == 200
    assert r.json()["ok"] is False
    codes = [f["code"] for f in r.json()["findings"]]
    assert codes
    assert all(f["level"] in ("error", "warning") for f in r.json()["findings"])


def test_every_finding_comes_back_at_once(make_client, db):
    client = make_client()
    r = _check(
        client,
        _jwt(_new_user(db)),
        entrypoint="import requests\nimport socket\nimport urllib.request\n",
    )
    assert len(r.json()["findings"]) >= 2


def test_an_entrypoint_path_disagreeing_with_the_config_is_refused(make_client, db):
    """Preflighting bytes from a file the config does not name would bless
    a config that cannot run. Refuse rather than guess which is right."""
    client = make_client()
    r = _check(client, _jwt(_new_user(db)), entrypoint_path="other.py")
    assert r.status_code == 400
    assert "entrypoint" in r.json()["detail"]


def test_an_entrypoint_escaping_the_root_is_refused(make_client, db):
    client = make_client()
    r = _check(
        client,
        _jwt(_new_user(db)),
        config=CLEAN_CONFIG.replace("train.py", "../../etc/passwd"),
        entrypoint_path="../../etc/passwd",
    )
    assert r.status_code == 400


def test_an_unknown_pool_is_404(make_client, db):
    client = make_client()
    r = _check(client, _jwt(_new_user(db)), pool=str(uuid.uuid4()))
    assert r.status_code == 404


def test_a_pool_id_that_is_not_a_uuid_is_the_same_404(make_client, db):
    client = make_client()
    r = _check(client, _jwt(_new_user(db)), pool="not-a-uuid")
    assert r.status_code == 404


def test_an_un_admitted_account_is_refused(make_client, db):
    client = make_client()
    r = _check(client, _jwt(_new_user(db, admitted=False)))
    assert r.status_code == 403


def test_without_a_token_nothing_is_validated(make_client):
    client = make_client()
    r = client.post("/v1alpha1/preflight", json={"config": CLEAN_CONFIG})
    assert r.status_code == 401


def test_an_oversized_body_is_413(make_client, db):
    client = make_client()
    r = _check(client, _jwt(_new_user(db)), entrypoint="x = 1\n" * 400_000)
    assert r.status_code == 413
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest tests/test_preflight_endpoint.py -q
```

Expected: FAIL — 404 from FastAPI on every case, the route does not exist.

- [ ] **Step 3: Write the helper**

Add to `flashml_cloud_api/app.py`, immediately after `_read_config_text`
(around `app.py:265`):

```python
def _preflight_supplied_files(
    config_text: str, entrypoint_rel: str, entrypoint_text: str, dest: Path
) -> Path:
    """Materialise a caller's two files into ``dest`` and return the root.

    ``preflight`` reads from disk — it takes a ``repo_root`` and resolves
    ``config.entrypoint`` under it (``preflight._resolve_entrypoint``),
    refusing anything that escapes. Writing the bytes out rather than
    reworking preflight to accept text is deliberate: it keeps ONE code
    path shared with ``from-repo``, and it inherits that path-escape guard
    instead of reimplementing it here, where a second implementation could
    drift.

    Blocking on purpose — the caller runs it in a worker thread, same as
    ``_fetch_and_extract``.
    """
    root = Path(dest) / "src"
    root.mkdir(parents=True, exist_ok=True)

    target = (root / entrypoint_rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        # `../../etc/passwd`. preflight would refuse to READ this, but we
        # would have already WRITTEN it — so the guard has to be here too,
        # before the write, not only there.
        raise HTTPException(
            status_code=400, detail="entrypoint must be a path inside the project"
        ) from None

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(entrypoint_text, encoding="utf-8")
    (root / CONFIG_FILENAMES[0]).write_text(config_text, encoding="utf-8")
    return root
```

- [ ] **Step 4: Write the route**

Add to `app.py` immediately after `submit_job_from_repo`:

```python
    @app.post("/v1alpha1/preflight", tags=["browser"])
    async def preflight_route(
        request: Request,
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Validate a config and its entrypoint without submitting anything.

        THIS CREATES NOTHING. No jobs row, no artifact, no coordinator
        call, no storage charge — it is a pure function of the bytes
        supplied, which is what ``preflight.py``'s own docstring establishes
        the module already is: it never imports, never execs, never
        resolves a path it was not handed, and never opens a file it was
        not pointed at. Exposing it standalone adds no attack surface that
        ``from-repo`` did not already carry.

        It exists because the feedback loop was otherwise edit → commit →
        push → submit → read findings. For an agent iterating on a
        flashml.yaml that is four irreversible steps per guess.
        """
        raw = await request.body()
        if len(raw) > MAX_JSON_BODY_BYTES:
            raise HTTPException(status_code=413, detail="request body too large")
        payload = await _json_object(request)

        config_text = payload.get("config")
        if not isinstance(config_text, str) or not config_text:
            raise HTTPException(status_code=400, detail="config required")
        entrypoint_text = payload.get("entrypoint")
        if not isinstance(entrypoint_text, str):
            entrypoint_text = ""

        # Same pool doctrine as from-repo, checked first and for the same
        # reason: an unknown pool must not cost the caller a parse.
        pool = _opt_str(payload.get("pool"))
        if pool is not None:
            try:
                pool_row = dbmod.fetch_pool_for_member(db, pool, user_id)
            except psycopg.errors.InvalidTextRepresentation:
                pool_row = None
            if pool_row is None:
                raise HTTPException(status_code=404, detail="unknown pool")
            pool = str(pool_row["id"])

        try:
            config = parse_flashml_yaml(config_text)
        except ConfigError as exc:
            # The parse error IS the whole answer — there is nothing to
            # preflight against a config that does not exist.
            raise HTTPException(
                status_code=400, detail=safe_text(exc, 500)
            ) from None

        # The config names the entrypoint; `entrypoint_path` only reports
        # which file the caller actually read. If they disagree we would be
        # preflighting bytes from a file the config does not name, blessing
        # a config that cannot run — so refuse rather than pick a winner.
        declared = _opt_str(payload.get("entrypoint_path"))
        if declared is not None and declared != config.entrypoint:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"entrypoint_path {safe_text(declared, 120)!r} is not the "
                    f"entrypoint this config names "
                    f"({safe_text(config.entrypoint, 120)!r})"
                ),
            )

        try:
            image = resolve_image(config.image)
        except UnknownImage as exc:
            raise HTTPException(
                status_code=400, detail=safe_text(exc, 300)
            ) from None

        with tempfile.TemporaryDirectory(prefix="flashml-check-") as tmpdir:
            root = await run_in_threadpool(
                _preflight_supplied_files,
                config_text,
                config.entrypoint,
                entrypoint_text,
                Path(tmpdir),
            )
            findings = await run_in_threadpool(preflight, config, root, image)

        rendered = [f.as_dict() for f in findings]
        body: dict[str, Any] = {
            "ok": not any(f.level == "error" for f in findings),
            "findings": rendered,
            # The parsed and normalized config, so a caller can show the
            # derived round count, the sweep combination count and the
            # resolved image — none of which are recoverable from the
            # caller's own YAML.
            "config": _preflight_config_view(config, image),
        }
        if pool is not None:
            # Spec §5 asks for "the pool's placement feasibility". There is
            # no placement-feasibility engine in this codebase to call, and
            # inventing one here would be a second answer to a question the
            # scheduler already owns. What a caller actually needs to know
            # is whether this will queue forever, so report the number the
            # submit page reports, from the same predicate — and say no more
            # than that.
            body["pool"] = {
                "id": pool,
                "machines_online": dbmod.count_online_machines(db, pool_id=pool),
            }
        return body
```

- [ ] **Step 5: Write the config view**

`FlashmlConfig` is a dataclass with fields the API owns; returning it whole
would make every field a public contract by accident. Add beside the helper
from Step 3:

```python
def _preflight_config_view(
    config: FlashmlConfig, image: CuratedImage
) -> dict[str, Any]:
    """The parsed config as a caller may see it.

    An allowlist, not ``asdict``: ``FlashmlConfig`` is an internal
    dataclass, and serialising it whole would turn every field it ever
    grows into a wire contract nobody chose. Same discipline as
    ``MACHINE_PUBLIC_COLUMNS``.
    """
    combinations = 1
    for values in config.sweep.values():
        combinations *= len(values)

    return {
        "version": config.version,
        "name": config.name,
        "image": config.image,
        "image_reference": image.reference,
        "entrypoint": config.entrypoint,
        "mode": config.mode,
        "args": list(config.args),
        "epochs": config.epochs,
        "sync_every": config.sync_every,
        # Derived, and the reason a caller wants this block at all:
        # `epochs / sync_every`, which nobody types.
        "round_count": config.round_count,
        # Also derived. A sweep of three keys reads as three lines in YAML
        # and submits as their product — the number people get wrong.
        "sweep_combinations": combinations if config.sweep else 0,
        "timeout_seconds": config.timeout_seconds,
        "dependencies": list(config.dependencies),
        "allow_partial": config.allow_partial,
    }
```

Every attribute above is verified against `FlashmlConfig`
(`flashml_cloud_api/flashml_yaml.py:134-196`), including the `round_count`
property. `is_federated` is deliberately absent — `mode` already says it and
two spellings of one fact is how they drift apart.

- [ ] **Step 6: Run the tests**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest tests/test_preflight_endpoint.py -v
```

Expected: PASS, all fifteen.

- [ ] **Step 7: Run the whole suite and commit**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest -q
git add flashml-cloud/apps/api/flashml_cloud_api/app.py \
        flashml-cloud/apps/api/tests/test_preflight_endpoint.py
git commit -m "feat(api): POST /v1alpha1/preflight — validation without a push"
```

---

## Task 2: Preflight's findings must match what from-repo would say

A separate task from Task 1 because it tests a *relationship* rather than a
behaviour, and because a reviewer could reasonably accept Task 1 and reject
this one's approach.

**Files:**
- Test: `flashml-cloud/apps/api/tests/test_preflight_endpoint.py` (append)

**Interfaces:**
- Consumes: Task 1's route; `test_jobs_from_repo`'s `make_client(files=...)`,
  which builds a tarball from a dict of paths to contents.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_preflight_endpoint.py`:

```python
def test_the_dry_run_and_a_real_submit_agree_on_the_same_bytes(make_client, db):
    """The endpoint's credibility rests entirely on this.

    A `check` that passes and a `submit` that then fails on the same bytes
    would be worse than having no check — the developer would learn to
    distrust it and go back to push-and-see. One authority for the rules
    means one answer.
    """
    bad_entrypoint = "import requests\n"
    files = {"flashml.yaml": CLEAN_CONFIG, "train.py": bad_entrypoint}

    client = make_client(files=files)
    token = _jwt(_new_user(db))

    dry = _check(client, token, entrypoint=bad_entrypoint)
    assert dry.status_code == 200
    assert dry.json()["ok"] is False

    wet = client.post(
        "/v1alpha1/jobs/from-repo",
        json={"repo": "https://github.com/acme/trainer", "ref": "main"},
        headers=_bearer(token),
    )
    assert wet.status_code == 400

    # Byte-identical, not merely "both refused". A drifting message is the
    # first symptom of a second copy of the rules appearing somewhere.
    assert dry.json()["findings"] == wet.json()["findings"]
```

- [ ] **Step 2: Run the test to verify it fails, then passes**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest tests/test_preflight_endpoint.py -k agree -v
```

If Task 1 is correct this passes immediately. **That is the expected outcome
and is not a reason to skip the test** — it is a regression guard on a property
that has no other enforcement. If it FAILS, the two paths have already
diverged: fix Task 1's route rather than relaxing this assertion.

- [ ] **Step 3: Commit**

```bash
git add flashml-cloud/apps/api/tests/test_preflight_endpoint.py
git commit -m "test(api): the dry run and a real submit agree on the same bytes"
```

---

## Task 3: Submit from an uploaded working tree

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py` (add the route
  after `preflight_route`)
- Test: `flashml-cloud/apps/api/tests/test_jobs_from_upload.py` (create)

**Interfaces:**
- Consumes: `repomod.extract_safely`, `repomod.RepoError`;
  everything Task 1 consumes; `compile_to_jobspec`,
  `compile_federated_round`, `forward_idempotent`, `fedavgmod`,
  `fleet_shape`.
- Produces: `POST /v1alpha1/jobs/from-upload` → 201 with the same body
  `from-repo` returns, and `jobs.source` recording
  `{"type": "upload", "sha256": "<hex>", "code_artifact": "artifact://…"}`.
- Produces (Step 4, shared with `from-repo`):

  ```python
  async def _stage_and_submit(
      *,
      db: psycopg.Connection,
      user_id: str,
      tar_bytes: bytes,
      config: FlashmlConfig,
      image: CuratedImage,
      pool: str | None,
      source: dict[str, Any],
      findings: list[dict[str, str]],
      coordinator: Any,
  ) -> Response | dict[str, Any]:
  ```

  Keyword-only throughout: it takes nine arguments, four of which are
  strings or dicts, and a positional call site would be unreadable and
  easy to transpose.

- [ ] **Step 1: Write the failing test**

Create `flashml-cloud/apps/api/tests/test_jobs_from_upload.py`:

```python
"""Submitting a working tree that lives in no public repo.

Per ROADMAP.md §6.2 this is THE private-code path for the whole product —
the GitHub App is held until a team asks — so the tests here carry more
weight than a convenience endpoint's would.

The tarball shape is not incidental: `repo.extract_safely` requires
exactly one top-level directory, because that is what GitHub wraps every
repo tarball in. A CLI producing a flat archive would be refused, so the
shape is pinned here as a contract the client half must meet.
"""
from __future__ import annotations

import hashlib
import io
import json
import tarfile
import uuid

from test_jobs_from_repo import (  # noqa: F401 - fixtures
    _jwt,
    _new_user,
    db,
    make_client,
    settings,
    transport,
)

CLEAN_CONFIG = """\
version: 1
name: hello
image: python-slim
entrypoint: train.py
"""


def _tarball(files: dict[str, str], top: str = "project") -> bytes:
    """A gzipped tarball wrapping every file in one top-level directory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


CLEAN_TREE = {"flashml.yaml": CLEAN_CONFIG, "train.py": "print('hi')\n"}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _upload(client, token: str, tar_bytes: bytes, **params):
    return client.post(
        "/v1alpha1/jobs/from-upload",
        content=tar_bytes,
        headers={**_bearer(token), "content-type": "application/gzip"},
        params=params,
    )


def _job_rows(db, owner_id: str) -> list[dict]:
    with db.cursor() as cur:
        cur.execute("select * from public.jobs where owner_id = %s", (owner_id,))
        return list(cur.fetchall())


def test_a_clean_tree_is_submitted_and_recorded(make_client, db):
    client = make_client()
    owner = _new_user(db)
    r = _upload(client, _jwt(owner), _tarball(CLEAN_TREE))
    assert r.status_code == 201
    assert r.json()["job_id"]
    assert len(_job_rows(db, owner)) == 1


def test_the_source_records_an_upload_and_its_digest_not_a_repo(make_client, db):
    """Provenance is the one real cost of this endpoint: a job naming a repo
    and a ref is reproducible, one naming a tarball hash is not. So the
    source is recorded DISTINGUISHABLY rather than dressed up as a repo."""
    client = make_client()
    owner = _new_user(db)
    tar_bytes = _tarball(CLEAN_TREE)
    _upload(client, _jwt(owner), tar_bytes)

    source = _job_rows(db, owner)[0]["source"]
    assert source["type"] == "upload"
    assert source["sha256"] == hashlib.sha256(tar_bytes).hexdigest()
    assert source["code_artifact"].startswith("artifact://")
    assert "owner" not in source
    assert "repo" not in source


def test_the_uploaded_bytes_are_what_gets_staged(make_client, db, transport):
    client = make_client()
    tar_bytes = _tarball(CLEAN_TREE)
    _upload(client, _jwt(_new_user(db)), tar_bytes)

    puts = [r for r in transport.requests if r.method == "PUT"]
    assert len(puts) == 1
    assert puts[0].content == tar_bytes


def test_an_error_finding_refuses_the_whole_upload(make_client, db, transport):
    """Same ordering guarantee from-repo makes: preflight before anything
    leaves this process. A refused upload must not stage an artifact, must
    not call the coordinator, and must not leave a jobs row."""
    client = make_client()
    owner = _new_user(db)
    r = _upload(
        client,
        _jwt(owner),
        _tarball({**CLEAN_TREE, "train.py": "import requests\n"}),
    )
    assert r.status_code == 400
    assert r.json()["findings"]
    assert _job_rows(db, owner) == []
    assert transport.requests == []


def test_all_findings_come_back_at_once(make_client, db):
    client = make_client()
    r = _upload(
        client,
        _jwt(_new_user(db)),
        _tarball(
            {**CLEAN_TREE, "train.py": "import requests\nimport socket\n"}
        ),
    )
    assert len(r.json()["findings"]) >= 2


def test_a_tree_without_a_flashml_yaml_is_a_clean_400(make_client, db):
    client = make_client()
    r = _upload(client, _jwt(_new_user(db)), _tarball({"train.py": "print(1)\n"}))
    assert r.status_code == 400
    assert "flashml.yaml" in r.json()["detail"]


def test_a_flat_tarball_is_refused_with_a_message_that_says_why(make_client, db):
    """extract_safely requires exactly one top-level directory. The client
    half has to produce that shape, so the refusal must be legible rather
    than a bare 400."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = CLEAN_CONFIG.encode()
        info = tarfile.TarInfo("flashml.yaml")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    client = make_client()
    r = _upload(client, _jwt(_new_user(db)), buf.getvalue())
    assert r.status_code == 400
    assert "top-level" in r.json()["detail"]


def test_a_malicious_tarball_is_refused_without_a_stack_trace(make_client, db):
    """A path escaping the extraction root. The message may quote an
    attacker-chosen member name, so it goes through safe_text."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"pwned"
        info = tarfile.TarInfo("project/../../../../tmp/evil")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))

    client = make_client()
    r = _upload(client, _jwt(_new_user(db)), buf.getvalue())
    assert r.status_code == 400
    assert "Traceback" not in r.text


def test_not_a_tarball_at_all_is_a_400_not_a_500(make_client, db):
    client = make_client()
    r = _upload(client, _jwt(_new_user(db)), b"this is not a gzip stream")
    assert r.status_code == 400


def test_an_oversized_upload_is_413(make_client, db):
    client = make_client()
    # A little over MAX_REPO_TARBALL_BYTES of incompressible-enough content
    # is expensive to build; assert on the declared length instead, which is
    # what the route checks first.
    r = client.post(
        "/v1alpha1/jobs/from-upload",
        content=b"x" * 128,
        headers={
            **_bearer(_jwt(_new_user(db))),
            "content-type": "application/gzip",
            "content-length": str(64 * 1024 * 1024),
        },
    )
    assert r.status_code == 413


def test_an_unknown_pool_is_404_before_the_tarball_is_touched(make_client, db):
    client = make_client()
    r = _upload(
        client, _jwt(_new_user(db)), _tarball(CLEAN_TREE), pool=str(uuid.uuid4())
    )
    assert r.status_code == 404


def test_an_un_admitted_account_cannot_upload(make_client, db):
    client = make_client()
    r = _upload(client, _jwt(_new_user(db, admitted=False)), _tarball(CLEAN_TREE))
    assert r.status_code == 403


def test_a_machine_token_cannot_upload(make_client, db):
    from flashml_cloud_api import enrolment

    client = make_client()
    owner = _new_user(db)
    started = enrolment.start_device_code(db, f"n-{uuid.uuid4().hex[:8]}", "h", "linux")
    enrolment.approve_device_code(db, started["user_code"], owner)
    token = enrolment.redeem_device_code(db, started["device_code"])

    r = _upload(client, token, _tarball(CLEAN_TREE))
    assert r.status_code == 401


def test_an_fmu_token_can_upload(make_client, db):
    """The point of the whole developer surface: a program, holding a
    credential of its own, submitting code that was never pushed anywhere."""
    from flashml_cloud_api import cli_auth

    client = make_client()
    owner = _new_user(db)
    started = cli_auth.start_cli_code(db, "laptop")
    cli_auth.approve_cli_code(db, started["user_code"], owner)
    token = cli_auth.redeem_cli_code(db, started["device_code"])

    r = _upload(client, token, _tarball(CLEAN_TREE))
    assert r.status_code == 201
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest tests/test_jobs_from_upload.py -q
```

Expected: FAIL — 404 on every case, the route does not exist.

- [ ] **Step 3: Write the route**

The body is `from-repo`'s, with the fetch replaced by a read of the request
body. Add to `app.py` after `preflight_route`:

```python
    @app.post("/v1alpha1/jobs/from-upload", status_code=201, tags=["browser"])
    async def submit_job_from_upload(
        request: Request,
        user_id: str = Depends(admitted_user),
        db: psycopg.Connection = Depends(db_conn),
    ):
        """Submit a gzipped tarball of a working tree.

        Identical to ``from-repo`` from the extraction step onward — same
        ``extract_safely``, same parse, same preflight-before-anything
        ordering, same pool check, same compile, same staging. It differs
        only in where the bytes came from, and ``from-repo`` already treats
        those bytes as fully untrusted, so nothing downstream weakens.

        This is the private-code path for the product (ROADMAP.md §6.2:
        the GitHub App waits until a team asks). Provenance is its one real
        cost — a job naming a repo and a ref is reproducible, one naming a
        tarball digest is not — so the source is recorded DISTINGUISHABLY
        rather than dressed up to look like a repo.
        """
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_REPO_TARBALL_BYTES:
            # Refuse on the claimed length before reading a byte. The check
            # below is what enforces it, since Content-Length is the
            # client's claim, not a fact.
            raise HTTPException(status_code=413, detail="request body too large")
        tar_bytes = await request.body()
        if len(tar_bytes) > MAX_REPO_TARBALL_BYTES:
            raise HTTPException(status_code=413, detail="request body too large")
        if not tar_bytes:
            raise HTTPException(status_code=400, detail="empty upload")

        # Query parameters, not a body field: the body IS the tarball. Same
        # pool doctrine as from-repo, and checked first for the same reason —
        # a pool the caller does not belong to must not cost an extraction.
        pool = _opt_str(request.query_params.get("pool"))
        if pool is not None:
            try:
                pool_row = dbmod.fetch_pool_for_member(db, pool, user_id)
            except psycopg.errors.InvalidTextRepresentation:
                pool_row = None
            if pool_row is None:
                raise HTTPException(status_code=404, detail="unknown pool")
            pool = str(pool_row["id"])

        with tempfile.TemporaryDirectory(prefix="flashml-upload-") as tmpdir:
            dest = Path(tmpdir) / "src"
            try:
                repo_root = await run_in_threadpool(
                    repomod.extract_safely, tar_bytes, dest, MAX_REPO_TARBALL_BYTES
                )
            except repomod.RepoError as exc:
                # The message can quote a tar member's name, which is
                # caller-chosen: sanitise before it reaches a response or a
                # log line.
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
            # Refused before a single byte leaves this process. No artifact,
            # no coordinator submission, no jobs row.
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

        source = {
            "type": "upload",
            # The digest is the whole provenance story for an upload. It
            # cannot be re-fetched the way a repo+ref can, so recording it
            # is the difference between "we know what ran" and "we do not".
            "sha256": hashlib.sha256(tar_bytes).hexdigest(),
        }
        return await _stage_and_submit(
            db=db,
            user_id=user_id,
            tar_bytes=tar_bytes,
            config=config,
            image=image,
            pool=pool,
            source=source,
            findings=rendered,
            coordinator=coordinator,
        )
```

Add `import hashlib` to `app.py`'s import block if it is not already there.

- [ ] **Step 4: Extract `_stage_and_submit` from `from-repo`**

Both routes now need the identical tail: mint the artifact key, compile,
upload, submit, insert the jobs row, return. Extract it from
`submit_job_from_repo` (everything from `code_key = ...` to its return) into a
module-level `async def _stage_and_submit(...)` beside the other helpers, and
have `from-repo` call it with its own source dict:

```python
        source = {
            "type": "github",
            "owner": owner,
            "repo": name,
            "ref": ref,
        }
        return await _stage_and_submit(
            db=db, user_id=user_id, tar_bytes=tar_bytes, config=config,
            image=image, pool=pool, source=source, findings=rendered,
            coordinator=coordinator,
        )
```

The extraction is mechanical — **move the lines, do not retype them.** The
federated branch inside it already builds an extended source dict from the base
one (`mode`, `epochs`, `sync_every`, `rounds`, `slots`); keep that, merging into
whatever `source` it is handed rather than rebuilding a github-shaped one.
`code_artifact` is added inside the helper, since only it knows the key.

**Run `tests/test_jobs_from_repo.py` alone immediately after this step, before
touching anything else.** It is the regression detector for the extraction:

```bash
.venv/bin/pytest tests/test_jobs_from_repo.py -q
```

Expected: PASS, unchanged. If it does not, the extraction changed behaviour —
revert and redo it rather than adjusting the test.

- [ ] **Step 5: Run the tests**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest tests/test_jobs_from_upload.py tests/test_jobs_from_repo.py -v
```

Expected: PASS. `test_jobs_from_repo.py` passing unchanged is the evidence the
shared tail did not move.

- [ ] **Step 6: Run the whole suite and commit**

```bash
cd flashml-cloud/apps/api
.venv/bin/pytest -q
git add flashml-cloud/apps/api/flashml_cloud_api/app.py \
        flashml-cloud/apps/api/tests/test_jobs_from_upload.py
git commit -m "feat(api): POST /v1alpha1/jobs/from-upload — submit private code"
```

---

## Task 4: The upload path respects the storage budget

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py` (`submit_job_from_upload`)
- Test: `flashml-cloud/apps/api/tests/test_jobs_from_upload.py` (append)

**Interfaces:**
- Consumes: `_storage_gate(db, user_id)` (`app.py:431`).

- [ ] **Step 1: Write the failing test**

`public.profiles.storage_limit_bytes` is the per-account override
(`db.storage_limit_override_for`, `db.py:2574`). **0 and NULL are different
answers on purpose** — 0 is an admin freezing an account, NULL means "use the
deployment default" — so setting it to 0 is exactly the frozen case.

Append to `tests/test_jobs_from_upload.py`:

```python
def test_an_account_over_its_storage_budget_cannot_upload(make_client, db):
    """An upload stages a multi-megabyte artifact. An account already over
    quota must not be able to add another one — which is exactly what the
    storage gate exists to prevent on POST /v1alpha1/jobs."""
    client = make_client()
    owner = _new_user(db)
    with db.cursor() as cur:
        # 0, not NULL: `storage_limit_override_for` treats NULL as "use the
        # deployment default" and 0 as a real limit of zero bytes.
        cur.execute(
            "update public.profiles set storage_limit_bytes = 0 where id = %s",
            (owner,),
        )

    r = _upload(client, _jwt(owner), _tarball(CLEAN_TREE))
    assert r.status_code == 413
    assert _job_rows(db, owner) == []
```

413 is the right assertion, verified: `_storage_gate` raises
`HTTPException(status_code=413, detail=problem)` (`app.py:453`) and its
docstring says why — *"413 rather than 403: this is not a permissions
decision. The account is allowed to do this and has run out of room."*

- [ ] **Step 2: Run the test to verify it fails**

```bash
.venv/bin/pytest tests/test_jobs_from_upload.py -k storage -v
```

Expected: FAIL — 201, because the route has no gate.

- [ ] **Step 3: Add the gate**

In `submit_job_from_upload`, immediately after the pool block and **before**
the extraction:

```python
        # Before extraction, not after: an account with no room left should
        # not spend a 32 MiB decompression to be told so.
        _storage_gate(db, user_id)
```

- [ ] **Step 4: Run the tests and commit**

```bash
.venv/bin/pytest tests/test_jobs_from_upload.py -q
.venv/bin/pytest -q
git add flashml-cloud/apps/api/flashml_cloud_api/app.py \
        flashml-cloud/apps/api/tests/test_jobs_from_upload.py
git commit -m "fix(api): the upload path respects the storage budget"
```

---

## Task 5: So does `from-repo`, which never did

Its own task because it changes a shipped route's behaviour and a reviewer
should be able to reject it independently of everything above.

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py` (`submit_job_from_repo`)
- Test: `flashml-cloud/apps/api/tests/test_jobs_from_repo.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_jobs_from_repo.py`, using whatever budget-squeezing
mechanism Task 4 settled on:

```python
def test_an_account_over_its_storage_budget_cannot_submit_from_a_repo(
    make_client, db, transport
):
    """from-repo stages the same kind of artifact from-upload does and had
    no gate at all — `_storage_gate` was wired only to POST /v1alpha1/jobs.
    A quota enforced on one of three submit paths is not a quota."""
    client = make_client()
    owner = _new_user(db)
    with db.cursor() as cur:
        # 0, not NULL — NULL means "use the deployment default".
        cur.execute(
            "update public.profiles set storage_limit_bytes = 0 where id = %s",
            (owner,),
        )

    r = _post(client, _jwt(owner))
    assert r.status_code == 413
    assert _job_rows(db, owner) == []
    assert transport.requests == []
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
.venv/bin/pytest tests/test_jobs_from_repo.py -k storage -v
```

Expected: FAIL — 201.

- [ ] **Step 3: Add the gate**

In `submit_job_from_repo`, immediately after the pool block and before the
`tempfile.TemporaryDirectory` that fetches the repo:

```python
        # Before the fetch, not after: an account with no room left should
        # not spend a GitHub round trip and a 32 MiB extraction to be told
        # so. This route shipped without the gate `POST /v1alpha1/jobs` has
        # always had — a quota enforced on one of three submit paths is not
        # a quota.
        _storage_gate(db, user_id)
```

- [ ] **Step 4: Run the whole suite and commit**

```bash
.venv/bin/pytest -q
```

Expected: PASS. If a pre-existing `test_jobs_from_repo.py` test now fails
because its fixture account has no budget configured, that is the gate working
— check `storagemod.deployment_default()` and give the fixture a real limit
rather than removing the gate.

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/app.py \
        flashml-cloud/apps/api/tests/test_jobs_from_repo.py
git commit -m "fix(api): from-repo enforces the storage budget it never had"
```

---

## Task 6: Document both routes where a developer will look

**Files:**
- Modify: `flashml-cloud/apps/api/README.md`
- Modify: `flashml-cloud/flashml-cloud/CLAUDE.md` (the "Granting access and
  admin" section's neighbourhood — add a short "Submitting without a repo" note)
- Modify: `flashml-cloud/PROGRESS.md`

- [ ] **Step 1: Read what is already documented**

```bash
cd flashml-cloud/apps/api
grep -n "from-repo" README.md
```

- [ ] **Step 2: Add both routes to the API README**

Beside the existing `from-repo` entry, matching its style, and stating the
three facts a reader needs that are not obvious from the path:

- `POST /v1alpha1/preflight` — validates config + entrypoint text, **creates
  nothing**, returns every finding at once plus the normalized config.
- `POST /v1alpha1/jobs/from-upload` — body is the gzipped tarball itself
  (`content-type: application/gzip`), `?pool=` is a query parameter, and the
  tarball must wrap its contents in **exactly one top-level directory**, which
  is what `extract_safely` requires.
- Both are reachable with an `fmu_` developer token as well as a browser JWT.

- [ ] **Step 3: Log it in PROGRESS.md**

Follow the protocol at the top of that file: an entry with test counts per
suite, the root causes (the missing storage gate is one), and the single most
useful next action — **write the client-and-CLI plan**.

- [ ] **Step 4: Commit**

```bash
git add flashml-cloud/apps/api/README.md flashml-cloud/PROGRESS.md \
        flashml-cloud/flashml-cloud/CLAUDE.md
git commit -m "docs: preflight and from-upload, and the gate from-repo was missing"
```

---

## Done means

All six tasks committed, and:

```bash
cd flashml-cloud/apps/api && .venv/bin/pytest -q     # record the count
```

plus this loop by hand, which is the plan in one paste — no repo, no push, no
browser, using an `fmu_` token from Plan 1:

```bash
TOKEN=fmu_...            # from `flashml login`, or the /account/cli page
API=http://localhost:8000

# 1. validate, creating nothing
curl -s "$API/v1alpha1/preflight" -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d "$(jq -n --arg c "$(cat flashml.yaml)" --arg e "$(cat train.py)" \
        '{config:$c, entrypoint:$e, entrypoint_path:"train.py"}')"
# -> {"ok":true,"findings":[],"config":{...}}

# 2. submit the working tree itself
tar czf /tmp/tree.tgz --exclude-vcs -C .. "$(basename "$PWD")"
curl -s -X POST "$API/v1alpha1/jobs/from-upload" \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/gzip' \
  --data-binary @/tmp/tree.tgz
# -> 201 {"job_id":"..."}
```

Note `tar -C .. "$(basename "$PWD")"` rather than `tar czf … .` — the second
produces a flat archive that `extract_safely` refuses. That is the shape
contract the client half must meet, and it is why Task 3 tests the flat case
explicitly.

---

## Not in this plan

- **The `flashml` package** — typed client, `~/.flashml/credentials.json`,
  and the nine CLI verbs in spec §4.2. Its own plan, in the public repo,
  written next. These endpoints are its prerequisite, not its sibling.
- **Everything MCP** (spec §6) — Plan 3, still blocked on the live-logs open
  question (spec §10.1 / `ROADMAP.md` P0.4).
- **Rate limiting** (spec §10.3). `POST /v1alpha1/preflight` is the endpoint an
  agent loop will hammer and it has none. Flagged in the spec, designed
  nowhere, and `ROADMAP.md` P2.4 owns it. Do not bolt one on here.
- **A `.gitignore`-aware tarball builder.** That is client-side work and
  belongs with the CLI; this route takes whatever bytes it is given.
- **Real placement feasibility.** Spec §5 asks preflight to report "the pool's
  placement feasibility". No such engine exists to call — the scheduler decides
  placement at claim time from machine capabilities, and a second implementation
  here would be a prediction that disagrees with the decision. Task 1 reports
  `machines_online` instead, from the same `MACHINE_ONLINE_PREDICATE` the
  console counts with, because "will this queue forever" is the question a
  caller is really asking. Capability-level feasibility ("no machine in this
  pool can run `pytorch-cuda`") is a genuine gap and wants its own design.
- **Any change to `preflight.py` or `flashml_yaml.py`.** Both routes exist
  precisely so there stays exactly one authority for the rules.
