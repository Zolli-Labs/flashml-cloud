# Team Pools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Invite-only team pools — a group shares all its members' workers (laptops, paid-Colab sessions, RunPod pods) and any member's repo jobs run only on the group's fleet, unsandboxed where a worker has no Docker.

**Architecture:** The private API owns pool truth (Postgres) and stamps membership server-side onto the agent proxy; flashruntime 0.4.3 adds the wire fields and a fail-closed seventh placement gate; flashnode 0.3.4 adds an operator-opt-in trusted runner so Docker-less workers can execute pool-scoped argv payloads. Spec: `../specs/2026-08-03-team-pools-design.md` (including its Corrections section — the spec's original §3/§7 predate two findings this plan implements).

**Tech Stack:** Python 3.12 (pydantic v2, FastAPI, psycopg 3), Next.js 16 App Router + vitest, pytest, uv.

## Global Constraints

- **Fail-closed polarity** for every new placement check: copy the argv/local-data/gpu/exclusion gates' `is True` / isinstance patterns, never the module gate's fail-open one. Type-confused values refuse, never crash. (flashruntime `CLAUDE.md` hard rule 3: "Security-relevant fields fail closed.")
- **The coupled invariant:** `allowFallback` may be true **iff** the job is pool-scoped. Enforced at compile (cloud) AND in `CommandRecipe.expand` (runtime), pinned by tests in both repos.
- **Pools are stamped server-side, never merged.** Agent-supplied `pools` values are overwritten on the proxy. On a lookup failure stamp `[]` (node serves no pool) — never skip the stamp.
- **Additive wire changes stay in v1alpha1 / 0.4.x** (precedent: `local_datasets` 0.4.0, `GpuInfo` 0.4.1, `ExecutionEvidence` 0.4.2). Bump on any wire-visible change, even additive. flashruntime → **0.4.3**, flashnode → **0.3.4** floor `>=0.4.3,<0.5`.
- **Migrations:** `create table if not exists`, `comment on table`, `enable row level security`, **zero `create policy`** anywhere, index FK columns, the two boilerplate header paragraphs. Never edit an applied migration.
- **404-not-403 doctrine:** a resource that exists but is not yours returns the same 404 as one that doesn't exist.
- **Secrets:** invite tokens follow machine tokens — random urlsafe, store sha256 hex only, return raw exactly once. Raw tokens never in a public-columns tuple.
- **Four pin sites move together:** `Makefile` (RUNTIME_VERSION, NODE_VERSION), `render.yaml` (both coordinators), `apps/api/pyproject.toml`. Blueprint sync **before** deploy.
- **`make e2e-setup LOCAL=1` is not release evidence.** Cloud tasks that need unreleased runtime fields develop against `LOCAL=1` and the sibling checkout; CI and deploy resolve the pins.
- **Docs rule:** every Colab-facing document states paid-tier-only, quoting the FAQ finding; nothing automates multiple accounts.
- Paths: public repo `~/Work/Zolli-Labs/flashml` (branch off `main`); private repo `~/Work/Zolli-Labs/flashml-cloud` branch `develop`; API package at `flashml-cloud/apps/api/flashml_cloud_api/`, web at `flashml-cloud/apps/web/`, e2e at `e2e/`.

---

## Phase 1 — flashruntime 0.4.3 (public repo)

Work on a branch: `cd ~/Work/Zolli-Labs/flashml && git checkout -b feat/team-pools main`.
Setup once: `cd flashruntime && uv venv && uv pip install -e ".[sklearn,dev,service]"` (the `service` extra is required — the placement end-to-end tests import `flashruntime.service.modea` and error on collection without it).

### Task 1: Protocol fields

**Files:**
- Modify: `flashruntime/flashruntime/protocol/v1alpha1.py` (NodeCapabilities ~line 304, NodeRegistration ~line 316, NodeHeartbeat, PlacementSpec ~line 98)
- Test: `flashruntime/tests/test_protocol_pools.py` (new)

**Interfaces:**
- Consumes: nothing new.
- Produces: `NodeCapabilities.pools: list[str]` (default `[]`), `NodeRegistration.unsandboxed_argv_capable: bool` (default `False`), `NodeHeartbeat.pools: list[str] | None` (default `None`), `PlacementSpec.pool: str` (default `"any"`, was a closed Literal). Tasks 2–4, 11, 12 rely on these exact names.

- [ ] **Step 1: Write the failing tests**

```python
"""Wire fields for team pools (AGENTS.md rule 3: security fields fail closed).

`pools` (plural, on capabilities) names TEAM pools minted by the cloud
control plane and is stamped server-side by its API proxy. It is not
`NodeRegistration.pool` (singular), a deployment-profile label ("local")
that predates teams and is only ever logged.
"""

from flashruntime.protocol.v1alpha1 import (
    NodeCapabilities,
    NodeHeartbeat,
    NodeRegistration,
    PlacementSpec,
)


def test_capabilities_pools_defaults_empty_and_round_trips():
    assert NodeCapabilities().pools == []
    caps = NodeCapabilities.model_validate({"pools": ["p-1", "p-2"]})
    assert caps.model_dump()["pools"] == ["p-1", "p-2"]


def test_capabilities_pools_default_is_not_shared_between_instances():
    a, b = NodeCapabilities(), NodeCapabilities()
    a.pools.append("p-1")
    assert b.pools == []


def test_registration_unsandboxed_argv_capable_defaults_false():
    reg = NodeRegistration(
        node_id="n", kubernetes_node="", hostname="h",
        capabilities=NodeCapabilities(),
    )
    assert reg.unsandboxed_argv_capable is False


def test_heartbeat_pools_defaults_none_meaning_no_statement():
    hb = NodeHeartbeat.model_validate({"node_id": "n"})
    assert hb.pools is None
    hb2 = NodeHeartbeat.model_validate({"node_id": "n", "pools": []})
    assert hb2.pools == []


def test_placement_pool_accepts_a_team_pool_id():
    spec = PlacementSpec.model_validate({"pool": "3f2a7b1e-team"})
    assert spec.pool == "3f2a7b1e-team"
    assert PlacementSpec().pool == "any"


def test_old_wire_shapes_still_validate():
    """An agent on 0.3.3 sends none of the new fields; nothing may break."""
    reg = NodeRegistration.model_validate(
        {"node_id": "n", "kubernetes_node": "", "hostname": "h",
         "capabilities": {"cpu_cores": 4}}
    )
    assert reg.capabilities.pools == []
    assert reg.unsandboxed_argv_capable is False
```

If `NodeHeartbeat` requires more fields than `node_id`, read its definition first and satisfy the minimum — do not loosen the model.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Work/Zolli-Labs/flashml/flashruntime && pytest tests/test_protocol_pools.py -q`
Expected: FAIL (`pools`/`unsandboxed_argv_capable` not fields; `PlacementSpec` Literal rejects the id).

- [ ] **Step 3: Implement the fields**

In `NodeCapabilities`, after `gpus`:

```python
    #: Team pools this node serves, as pool ids minted by the cloud control
    #: plane. Stamped SERVER-SIDE by the cloud API's agent proxy from the
    #: machine owner's memberships — an agent's self-reported value is
    #: overwritten there, so the field is only as trustworthy as the
    #: operator fronting registration. Empty means "serves no pool", and the
    #: seventh placement gate refuses pool-scoped work (fail closed). NOT
    #: the same thing as NodeRegistration.pool (singular), a deployment
    #: profile label that predates teams; the two never interact.
    pools: list[str] = Field(default_factory=list)
```

In `NodeRegistration`, after `argv_capable`:

```python
    #: The operator explicitly opted this host into executing POOL-SCOPED
    #: argv payloads without a container (`flashnode work --runner trusted`).
    #: Defaults False so every existing agent is excluded until its owner
    #: opts in (security fields fail closed). Distinct from argv_capable,
    #: which asserts the CONTAINERISED argv contract — this asserts the
    #: opposite: no sandbox, trusted-pool work only. The scheduler requires
    #: pool + allowFallback + this flag together; any one alone places nothing.
    unsandboxed_argv_capable: bool = False
```

In `NodeHeartbeat`:

```python
    #: Optional pool-membership refresh, stamped by the cloud API on the
    #: heartbeat proxy so joining or leaving a pool reaches placement
    #: without an agent restart. None means "no statement" — the
    #: coordinator keeps whatever registration said. A list (even empty)
    #: replaces `capabilities.pools` wholesale. Agents never set this.
    pools: list[str] | None = None
```

Replace `PlacementSpec.pool`'s Literal:

```python
class PlacementSpec(BaseModel):
    #: "any" (the default, and every pre-pools job) or a team-pool id minted
    #: by the control plane. Until 0.4.3 this was a closed Literal of
    #: infrastructure pool names that nothing ever read; widened when it
    #: gained its first reader. Non-"any" makes every task of the job carry
    #: payload["pool"] (recipes stamp it) and place only on nodes listing
    #: that id in capabilities.pools.
    pool: str = "any"
    architectures: list[Literal["amd64", "arm64"]] = Field(default_factory=lambda: ["amd64"])
```

- [ ] **Step 4: Run tests to verify they pass, then run the whole suite**

Run: `pytest tests/test_protocol_pools.py -q` → PASS, then `pytest` → no regressions (expect ~661 passing as of 0.4.2 plus these).

- [ ] **Step 5: Commit**

```bash
git add flashruntime/flashruntime/protocol/v1alpha1.py flashruntime/tests/test_protocol_pools.py
git commit -m "feat(protocol): team-pool wire fields — pools, unsandboxed_argv_capable, heartbeat refresh"
```

### Task 2: The seventh placement gate — pool

**Files:**
- Modify: `flashruntime/flashruntime/scheduler/__init__.py` (docstring `:93-257`; code after the `exclude_nodes` block ~`:324`, before `isolation = task.payload.get("isolation")`)
- Test: `flashruntime/tests/test_placement_pool.py` (new)

**Interfaces:**
- Consumes: Task 1's `NodeCapabilities.pools` (reaches the gate as `node["capabilities"]["pools"]` — the claim node view forwards capabilities wholesale via `model_dump()`, so no node-view change is needed).
- Produces: `IsolationAwarePlacement.eligible` refuses a task whose `payload["pool"]` the node's `capabilities.pools` does not list. Task 12's compiled jobs and Task 16's e2e depend on exactly this key.

- [ ] **Step 1: Write the failing tests** — model the file on `tests/test_placement_gpu.py`: `_task` passes the requirement RAW so type-confusion can be poisoned; `_node` documents the `model_dump()` shape; imports inside function bodies; assert `is True`/`is False`, never truthiness.

```python
"""Seventh placement gate: team pools (AGENTS.md rule 3 — fail closed).

A pool-scoped task must land only on a node whose stamped capabilities list
that pool. The failure mode this prevents is the design's worst one: pool
jobs carry allowFallback, so a task escaping the pool boundary would run
UNSANDBOXED on a stranger's machine.
"""

import pytest


def _task(pool=None, **payload_extra):
    """`pool` is passed RAW so tests can poison it (the `_gpu_task` trick).
    None means the key is absent entirely."""
    from flashruntime.protocol.v1alpha1 import TaskSpec

    payload = {"module": "flashml_workloads.sklearn_trial"}
    if pool is not None:
        payload["pool"] = pool
    payload.update(payload_extra)
    return TaskSpec(
        task_id="task-000", job_id="job-a", commit_key="job-a/task-000/m.json",
        payload=payload,
    )


def _node(pools=None, **extra):
    """Node view shaped like the claim endpoint's: pools lives under
    `capabilities`, which is NodeCapabilities.model_dump() — plain dicts."""
    capabilities = {"cpu_cores": 8}
    if pools is not None:
        capabilities["pools"] = pools
    node = {"node_id": "n1", "capabilities": capabilities}
    node.update(extra)
    return node


def test_task_without_pool_places_anywhere():
    from flashruntime.scheduler import IsolationAwarePlacement

    assert IsolationAwarePlacement().eligible(_task(), _node()) is True


def test_pool_task_places_on_a_member_node():
    from flashruntime.scheduler import IsolationAwarePlacement

    assert IsolationAwarePlacement().eligible(
        _task("p-1"), _node(pools=["p-1", "p-2"])
    ) is True


def test_pool_task_refuses_a_non_member_node():
    from flashruntime.scheduler import IsolationAwarePlacement

    assert IsolationAwarePlacement().eligible(
        _task("p-1"), _node(pools=["p-2"])
    ) is False


def test_pool_task_refuses_a_node_with_no_pools_at_all():
    from flashruntime.scheduler import IsolationAwarePlacement

    assert IsolationAwarePlacement().eligible(_task("p-1"), _node()) is False


@pytest.mark.parametrize(
    "advertised",
    [None, "p-1", {"p-1": True}, 1, [None], [1], ["p-1", None], b"p-1"],
)
def test_type_confused_advertisement_fails_closed(advertised):
    from flashruntime.scheduler import IsolationAwarePlacement

    assert IsolationAwarePlacement().eligible(
        _task("p-1"), _node(pools=advertised)
    ) is False


@pytest.mark.parametrize("required", ["", 1, True, ["p-1"], {"id": "p-1"}])
def test_type_confused_requirement_fails_closed(required):
    from flashruntime.scheduler import IsolationAwarePlacement

    assert IsolationAwarePlacement().eligible(
        _task(required), _node(pools=["p-1"])
    ) is False


def test_capabilities_absent_or_confused_fails_closed():
    from flashruntime.scheduler import IsolationAwarePlacement

    policy = IsolationAwarePlacement()
    assert policy.eligible(_task("p-1"), {"node_id": "n1"}) is False
    assert policy.eligible(
        _task("p-1"), {"node_id": "n1", "capabilities": "confused"}
    ) is False


def test_allow_fallback_does_not_waive_the_pool_gate():
    """The waiver and the boundary must never trade places: allowFallback is
    what pool jobs CARRY, so it waiving this gate would unsandbox strangers."""
    from flashruntime.scheduler import IsolationAwarePlacement

    task = _task("p-1", isolation={"tier": "sandboxed", "allowFallback": True})
    assert IsolationAwarePlacement().eligible(task, _node(pools=["p-2"])) is False


def test_claim_endpoint_confines_a_pool_task_to_member_nodes():
    """End to end over the claim endpoint (the hop that broke local_datasets
    three times): capabilities.pools must survive register → node view →
    gate. Model on test_placement_gpu's claim test."""
    import pathlib

    import fastapi
    from fastapi.testclient import TestClient

    from flashruntime.leases import LeaseManager
    from flashruntime.service.modea import ModeAState, build_router

    state = ModeAState(LeaseManager(), artifacts_dir=pathlib.Path("/tmp"))
    app = fastapi.FastAPI()
    app.include_router(build_router(state))
    client = TestClient(app)
    state.manager.add_task(_task("p-1"))

    def register(node_id: str, pools: list[str]):
        r = client.post(
            "/v1alpha1/nodes/register",
            json={"node_id": node_id, "kubernetes_node": "", "hostname": node_id,
                  "capabilities": {"cpu_cores": 8, "pools": pools}},
        )
        assert r.status_code == 200

    register("outsider", [])
    register("member", ["p-1"])

    assert client.post("/v1alpha1/leases/claim", json={"node_id": "outsider"}).status_code == 204
    r = client.post("/v1alpha1/leases/claim", json={"node_id": "member"})
    assert r.status_code == 200
    assert r.json()["task_id"] == "task-000"


def test_heartbeat_pools_refresh_reaches_placement():
    """Membership change without an agent restart: a heartbeat carrying
    pools=[] must strip eligibility on the next claim. Read the heartbeat
    handler in service/modea.py first for the exact route shape."""
    import pathlib

    import fastapi
    from fastapi.testclient import TestClient

    from flashruntime.leases import LeaseManager
    from flashruntime.service.modea import ModeAState, build_router

    state = ModeAState(LeaseManager(), artifacts_dir=pathlib.Path("/tmp"))
    app = fastapi.FastAPI()
    app.include_router(build_router(state))
    client = TestClient(app)
    state.manager.add_task(_task("p-1"))

    r = client.post(
        "/v1alpha1/nodes/register",
        json={"node_id": "member", "kubernetes_node": "", "hostname": "member",
              "capabilities": {"cpu_cores": 8, "pools": ["p-1"]}},
    )
    assert r.status_code == 200

    hb = client.post("/v1alpha1/nodes/member/heartbeat",
                     json={"node_id": "member", "pools": []})
    assert hb.status_code == 200

    assert client.post("/v1alpha1/leases/claim", json={"node_id": "member"}).status_code == 204
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_placement_pool.py -q`
Expected: the fail-closed tests FAIL (gate absent → everything eligible); the heartbeat test fails (no `pools` handling).

- [ ] **Step 3: Implement the gate + heartbeat refresh**

In `scheduler/__init__.py`, insert **after** the `exclude_nodes` block and **before** `isolation = task.payload.get("isolation")`:

```python
        # Fail-closed like every gate above, and checked before the
        # allowFallback waiver below for the sharpest reason yet: pool jobs
        # are exactly the ones that CARRY the waiver, so a pool task that
        # slipped this gate would run unsandboxed on a machine outside the
        # trust boundary that made the waiver acceptable.
        required_pool = task.payload.get("pool")
        if required_pool is not None:
            if not isinstance(required_pool, str) or not required_pool:
                return False  # type-confused requirement ⇒ fail closed, no crash
            capabilities = node.get("capabilities")
            # isinstance, not `or {}` — a string capabilities value has no
            # `.get` and must fail closed rather than crash the predicate.
            advertised = (
                capabilities.get("pools") if isinstance(capabilities, dict) else None
            )
            if not isinstance(advertised, list):
                return False  # absent/type-confused ⇒ serves no pool
            if not all(isinstance(p, str) for p in advertised):
                # A non-name member means the stamp was built wrong; the pool
                # it meant to serve is precisely what a membership test would
                # now get wrong. Refuse the node, not the one member.
                return False
            if required_pool not in advertised:
                return False
```

Add the class-docstring paragraph following the six-gate convention (ordinal, polarity declaration naming the gates it copies, the type-confusion bullets, why `allowFallback` does not waive it — the test above is the argument). Then in `service/modea.py`, read the heartbeat handler and, where it updates `entry.last_heartbeat`, apply:

```python
        if hb.pools is not None:
            # Server-stamped membership refresh (cloud proxy). A list, even
            # empty, replaces the registration's pools wholesale; None means
            # no statement. Replace the capabilities object rather than
            # mutating it in place so the registration model stays the
            # single source the claim node view dumps from.
            entry.registration = entry.registration.model_copy(
                update={
                    "capabilities": entry.registration.capabilities.model_copy(
                        update={"pools": list(hb.pools)}
                    )
                }
            )
```

(Adapt local names to the handler's actual parameter — read it first; it validates `NodeHeartbeat`.)

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_placement_pool.py -q` → PASS. Then `pytest tests/test_placement_gpu.py tests/test_placement_exclusion.py tests/test_placement_local_data.py tests/test_scheduler_isolation.py -q` → no regressions.

- [ ] **Step 5: Commit**

```bash
git add flashruntime/flashruntime/scheduler/__init__.py flashruntime/flashruntime/service/modea.py flashruntime/tests/test_placement_pool.py
git commit -m "feat(scheduler): the seventh placement gate — team pools, fail closed"
```

### Task 3: Trusted-pool argv placement (the argv gate learns one alternative)

**Files:**
- Modify: `flashruntime/flashruntime/scheduler/__init__.py` (the argv gate block — find it by reading `eligible`; it requires `node.get("argv_capable") is True` for payloads carrying `argv`), and its class docstring
- Modify: `flashruntime/flashruntime/service/modea.py` claim `node_view` (~`:656-671`) — add `"unsandboxed_argv_capable": entry.registration.unsandboxed_argv_capable,`
- Test: `flashruntime/tests/test_placement_trusted_argv.py` (new)

**Interfaces:**
- Consumes: Task 1's `NodeRegistration.unsandboxed_argv_capable`; Task 2's pool gate (both gates apply independently).
- Produces: an argv task with `payload["pool"]` set AND `payload["isolation"]["allowFallback"] is True` may place on a node with `unsandboxed_argv_capable is True` even when `argv_capable` is False. Tasks 6–7 (flashnode) and 16 (e2e) rely on this exact three-leg rule.

- [ ] **Step 1: Write the failing tests**

```python
"""Trusted-pool argv placement (AGENTS.md rule 3 — every leg fails closed).

An argv payload normally requires the containerised argv contract
(argv_capable). Inside a team pool the host's OPERATOR may opt into running
pool argv work unsandboxed. Three legs, all required: the task is
pool-scoped, its submitter waived the tier (allowFallback), and the node
opted in. Any one alone must place nothing — a waiver without a pool is
refused upstream by CommandRecipe, but this gate must not rely on that.
"""

import pytest


def _argv_task(pool=None, allow_fallback=True, **payload_extra):
    from flashruntime.protocol.v1alpha1 import TaskSpec

    payload = {
        "argv": ["python", "/work/inputs/code/train.py"],
        "isolation": {"tier": "sandboxed", "allowFallback": allow_fallback},
    }
    if pool is not None:
        payload["pool"] = pool
    payload.update(payload_extra)
    return TaskSpec(
        task_id="task-000", job_id="job-a", commit_key="job-a/task-000/m.json",
        payload=payload,
    )


def _node(pools=None, **extra):
    capabilities = {"cpu_cores": 8}
    if pools is not None:
        capabilities["pools"] = pools
    node = {"node_id": "n1", "capabilities": capabilities}
    node.update(extra)
    return node


def test_docker_argv_node_still_takes_pool_argv_work():
    from flashruntime.scheduler import IsolationAwarePlacement

    node = _node(pools=["p-1"], argv_capable=True, sandbox_capable=True)
    assert IsolationAwarePlacement().eligible(_argv_task("p-1"), node) is True


def test_trusted_node_takes_pool_argv_work_without_docker():
    from flashruntime.scheduler import IsolationAwarePlacement

    node = _node(pools=["p-1"], unsandboxed_argv_capable=True)
    assert IsolationAwarePlacement().eligible(_argv_task("p-1"), node) is True


def test_trusted_node_never_takes_a_public_sandboxed_argv_job():
    """The leg that keeps strangers' code off trusting hosts: no pool, no
    trusted placement — even though the node opted in."""
    from flashruntime.scheduler import IsolationAwarePlacement

    node = _node(unsandboxed_argv_capable=True)
    assert IsolationAwarePlacement().eligible(
        _argv_task(None, allow_fallback=False), node
    ) is False


def test_pool_without_waiver_does_not_unlock_trusted_argv():
    from flashruntime.scheduler import IsolationAwarePlacement

    node = _node(pools=["p-1"], unsandboxed_argv_capable=True)
    assert IsolationAwarePlacement().eligible(
        _argv_task("p-1", allow_fallback=False), node
    ) is False


def test_node_that_did_not_opt_in_is_refused():
    from flashruntime.scheduler import IsolationAwarePlacement

    node = _node(pools=["p-1"])  # member, subprocess-only, no opt-in
    assert IsolationAwarePlacement().eligible(_argv_task("p-1"), node) is False


@pytest.mark.parametrize("optin", [1, "true", None, [True]])
def test_type_confused_opt_in_fails_closed(optin):
    from flashruntime.scheduler import IsolationAwarePlacement

    node = _node(pools=["p-1"], unsandboxed_argv_capable=optin)
    assert IsolationAwarePlacement().eligible(_argv_task("p-1"), node) is False


def test_trusted_placement_still_respects_the_pool_gate():
    """Both gates apply: a trusted opted-in node OUTSIDE the pool refuses."""
    from flashruntime.scheduler import IsolationAwarePlacement

    node = _node(pools=["p-2"], unsandboxed_argv_capable=True)
    assert IsolationAwarePlacement().eligible(_argv_task("p-1"), node) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_placement_trusted_argv.py -q`
Expected: `test_trusted_node_takes_pool_argv_work_without_docker` FAILS (argv gate refuses); the refusal tests may already pass — that is fine, they pin the polarity.

- [ ] **Step 3: Implement**

Read the argv gate block in `eligible`. Where it currently refuses a node whose `argv_capable` is not `True`, allow the one alternative:

```python
            if node.get("argv_capable") is True:
                pass  # the containerised argv contract — always acceptable
            else:
                # Trusted-pool alternative: the host OPERATOR opted into
                # unsandboxed pool argv (`flashnode work --runner trusted`).
                # Three legs, each `is`-checked, each fails closed. The pool
                # leg here is a guard, not the boundary — the seventh gate
                # independently confines the task to pool members; this leg
                # exists so a waiver that somehow escaped compile/recipe
                # coupling still unlocks nothing outside a pool.
                task_pool = task.payload.get("pool")
                isolation_payload = task.payload.get("isolation")
                if not (
                    isinstance(task_pool, str)
                    and task_pool
                    and isinstance(isolation_payload, dict)
                    and isolation_payload.get("allowFallback") is True
                    and node.get("unsandboxed_argv_capable") is True
                ):
                    return False
```

Keep the surrounding gate's structure intact — this replaces only its refusal arm. Update the class docstring's argv-gate paragraph to name the alternative and its three legs. Add the `node_view` line in `service/modea.py`:

```python
            "unsandboxed_argv_capable": entry.registration.unsandboxed_argv_capable,
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_placement_trusted_argv.py tests/test_scheduler_isolation.py -q` → PASS, no regressions in the original argv-gate tests.

- [ ] **Step 5: Commit**

```bash
git add flashruntime/flashruntime/scheduler/__init__.py flashruntime/flashruntime/service/modea.py flashruntime/tests/test_placement_trusted_argv.py
git commit -m "feat(scheduler): trusted-pool argv — operator opt-in alternative to the container contract"
```

### Task 4: Pool travels JobSpec → task payload; the recipe's coupled invariant

**Files:**
- Modify: `flashruntime/flashruntime/recipes/command.py` (`expand`, the allowFallback refusal at ~`:84-101` and the payload stamps at ~`:149-182`)
- Modify: `flashruntime/flashruntime/service/modea.py` — the three expanders (`_expand` hyperparameter_search ~`:162`, `_expand_kmeans` ~`:218`, `_expand_fedavg` ~`:283`) stamp `pool` beside their existing `isolation` stamp
- Test: `flashruntime/tests/test_recipe_pool.py` (new)

**Interfaces:**
- Consumes: Task 1's `PlacementSpec.pool`.
- Produces: every task of a job whose `spec.spec.placement.pool != "any"` carries `payload["pool"] = <that id>`; `CommandRecipe.expand` accepts `allowFallback` **iff** pool-scoped. Task 12 (cloud compile) relies on both.

- [ ] **Step 1: Write the failing tests**

```python
"""Pool must survive the JobSpec → task-payload hop, and the waiver is
coupled to it. The local_inputs/gpus comments in recipes/command.py warn
that both ends of this hop have tests that pass while it is broken — so
these tests exercise the real expander, not a hand-built payload."""

import pytest

from flashruntime.protocol.v1alpha1 import JobSpec
from flashruntime.recipes.command import CommandRecipe


def _spec(pool="any", allow_fallback=False):
    return JobSpec.model_validate({
        "apiVersion": "flashml.dev/v1alpha1",
        "kind": "TrainingJob",
        "metadata": {"name": "demo"},
        "spec": {
            "workload": {
                "type": "command",
                "parameters": {
                    "argv": ["python", "/work/inputs/code/train.py"],
                    "image": "ghcr.io/zolli-labs/flashml-python-slim:2026.08.2",
                    "inputs": {"code": "artifact://jobs/demo/code.tar.gz"},
                    "unpack_inputs": ["code"],
                },
            },
            "resources": {"minimumWorkers": 1, "maximumWorkers": 1},
            "placement": {"pool": pool},
            "isolation": {"tier": "sandboxed", "allowFallback": allow_fallback},
        },
    })


def test_pool_is_stamped_into_every_task_payload():
    tasks = CommandRecipe().expand("job-1", _spec(pool="p-1", allow_fallback=True))
    assert all(t.payload["pool"] == "p-1" for t in tasks)


def test_any_stays_absent_never_stamped():
    """Absent stays absent — the no-pool path must keep exercising the
    key-missing branch, exactly as gpus and local_inputs do."""
    tasks = CommandRecipe().expand("job-1", _spec(pool="any"))
    assert all("pool" not in t.payload for t in tasks)


def test_waiver_without_a_pool_is_refused():
    with pytest.raises(ValueError, match="allowFallback"):
        CommandRecipe().expand("job-1", _spec(pool="any", allow_fallback=True))


def test_waiver_with_a_pool_is_accepted_and_travels():
    tasks = CommandRecipe().expand("job-1", _spec(pool="p-1", allow_fallback=True))
    assert tasks[0].payload["isolation"] == {"tier": "sandboxed", "allowFallback": True}
```

Adjust `_spec` to the real minimum `JobSpec` shape — read an existing `CommandRecipe` test for the exact keys (`tests/` has several; copy its skeleton rather than inventing one).

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_recipe_pool.py -q`
Expected: stamp tests FAIL (no `pool` key); `test_waiver_with_a_pool_is_accepted_and_travels` FAILS (unconditional `raise`).

- [ ] **Step 3: Implement**

In `CommandRecipe.expand`, replace the unconditional refusal:

```python
        pool = spec.spec.placement.pool
        if isolation_spec.allowFallback and pool == "any":
            # The waiver is only acceptable INSIDE a pool: members chose to
            # trust each other, and the seventh gate confines the task to
            # them. Without a pool it would let a submitter place arbitrary
            # code on any opted-in host — the exact thing the original
            # unconditional refusal existed to prevent.
            raise ValueError(
                "command jobs may not set isolation.allowFallback without "
                "placement.pool — unsandboxed argv is confined to team pools"
            )
```

Beside the existing `gpus` stamp:

```python
            if pool != "any":
                # Same hop, same warning as local_inputs/gpus above: dropping
                # this does NOT fail closed — the seventh gate would see a
                # task requiring nothing and place it anywhere, carrying the
                # waiver with it. Absent stays absent for "any".
                payload["pool"] = pool
```

In each of the three `modea.py` expanders, beside their `isolation` stamp:

```python
        if spec.spec.placement.pool != "any":
            payload["pool"] = spec.spec.placement.pool
```

(Read each expander first; they build `payload` dicts with different local names.)

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_recipe_pool.py -q && pytest -q` → PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add flashruntime/flashruntime/recipes/command.py flashruntime/flashruntime/service/modea.py flashruntime/tests/test_recipe_pool.py
git commit -m "feat(recipes): pool travels to task payloads; allowFallback coupled to placement.pool"
```

### Task 5: Release flashruntime 0.4.3

**Files:**
- Modify: `flashruntime/pyproject.toml` (version + the comment block convention)

**Interfaces:**
- Produces: `flashruntime==0.4.3` on PyPI. Tasks 6–7 floor on it; Task 18 pins it.

- [ ] **Step 1: Bump the version with the house comment**

At the top of the existing comment block above `version`:

```toml
# 0.4.3 adds team pools: NodeCapabilities.pools + NodeHeartbeat.pools
# (stamped server-side by the cloud proxy, never by agents),
# NodeRegistration.unsandboxed_argv_capable (operator opt-in, fail closed),
# PlacementSpec.pool widened from a dead closed Literal to str, the seventh
# placement gate, and the trusted-pool argv alternative. Additive and
# optional on the wire, so the compatibility range does not move — the bump
# is 0.4.1's rule again: bump on any wire-visible change, even additive.
# Agents need nothing from this release unless they opt into
# `--runner trusted` (flashnode 0.3.4, whose floor moves to >=0.4.3).
```

Set `version = "0.4.3"`.

- [ ] **Step 2: Full suites green, merge, tag**

```bash
cd ~/Work/Zolli-Labs/flashml/flashruntime && pytest
cd ../flashnode && pytest   # cross-package drift check against the local tree
cd .. && git add flashruntime/pyproject.toml && git commit -m "chore(release): flashruntime 0.4.3 — team pools on the wire"
git checkout main && git merge --no-ff feat/team-pools && git push origin main
git tag flashruntime-v0.4.3 && git push origin flashruntime-v0.4.3
```

- [ ] **Step 3: Verify the release**

Watch the release workflow (`gh run list --workflow release-flashruntime.yml`). The `resolvable` job gates publish; PyPI's simple index can lag its JSON API by minutes — re-run the failed job before debugging a phantom floor error (recorded 2026-08-02). Known cosmetic failure: `docs-deploy (GitHub Pages)` fails on every flashruntime tag (environment protection rejects tag refs) — red there does not mean the release failed.
Expected: `pip install flashruntime==0.4.3` resolves in a clean venv.

## Phase 2 — flashnode 0.3.4 (public repo, same branch flow)

### Task 6: TrustedArgvRunner

**Files:**
- Create: `flashnode/flashnode/executor/trusted_runner.py`
- Test: `flashnode/tests/test_trusted_runner.py` (new)

**Interfaces:**
- Consumes: the runner contract `run(payload: dict, workdir: Path, inputs: dict[str, Path]) -> Path` (`executor/runner.py`), `task_env()`, `TaskExecutionError`, and the loop's staging (inputs already unpacked under `workdir/inputs/<name>/` before `run` is called).
- Produces: `TrustedArgvRunner(timeout_seconds=600.0)` with that same contract, plus `last_exit_code` / `last_image_digest` evidence attributes. Task 7 wires it to `--runner trusted`.

- [ ] **Step 1: Write the failing tests**

```python
"""TrustedArgvRunner: pool argv payloads, no container, /work rewritten.

The compiled argv names CONTAINER paths (`python /work/inputs/code/train.py`
— the docker runners bind the workdir at /work). Here there is no container,
so every argv token starting with /work is rewritten onto the real workdir.
Rewriting tokens, not substrings: an argument that merely CONTAINS "/work"
is the submitter's business.
"""

import json
from pathlib import Path

import pytest

from flashnode.executor.runner import TaskExecutionError
from flashnode.executor.trusted_runner import TrustedArgvRunner


def _payload(argv):
    return {"argv": argv, "isolation": {"tier": "sandboxed", "allowFallback": True},
            "pool": "p-1"}


def test_runs_the_argv_with_work_prefix_rewritten(tmp_path):
    workdir = tmp_path
    code = workdir / "inputs" / "code"
    code.mkdir(parents=True)
    (code / "train.py").write_text(
        "import json, pathlib, sys\n"
        "out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'metrics.json').write_text(json.dumps({'ok': True}))\n"
    )
    runner = TrustedArgvRunner()
    outdir = runner.run(
        _payload(["python", "/work/inputs/code/train.py", "/work/out"]),
        workdir, {"code": code},
    )
    assert json.loads((outdir / "metrics.json").read_text()) == {"ok": True}
    assert runner.last_exit_code == 0


def test_refuses_a_payload_without_argv(tmp_path):
    with pytest.raises(TaskExecutionError):
        TrustedArgvRunner().run({"module": "flashml_workloads.sklearn_trial"},
                                tmp_path, {})


def test_refuses_a_non_list_argv(tmp_path):
    with pytest.raises(TaskExecutionError):
        TrustedArgvRunner().run(_payload("python /work/x.py"), tmp_path, {})


def test_nonzero_exit_raises_and_records_the_code(tmp_path):
    (tmp_path / "inputs").mkdir()
    runner = TrustedArgvRunner()
    with pytest.raises(TaskExecutionError):
        runner.run(_payload(["python", "-c", "raise SystemExit(3)"]), tmp_path, {})
    assert runner.last_exit_code == 3


def test_environment_is_scrubbed(tmp_path, monkeypatch):
    """The task must not inherit the agent's secrets — same whitelist as
    SubprocessRunner (task_env)."""
    monkeypatch.setenv("FLASHNODE_MACHINE_TOKEN", "fmk_secret")
    out = tmp_path / "probe"
    runner = TrustedArgvRunner()
    runner.run(
        _payload(["python", "-c",
                  "import os, pathlib; pathlib.Path('/work/out').mkdir(exist_ok=True);"
                  "pathlib.Path('/work/out/env.txt').write_text("
                  "str('FLASHNODE_MACHINE_TOKEN' in os.environ))"]),
        tmp_path, {},
    )
    assert (tmp_path / "out" / "env.txt").read_text() == "False"


def test_image_digest_is_always_empty():
    """No container ran; claiming an image digest would be fabricated
    evidence (the SubprocessRunner rule)."""
    assert TrustedArgvRunner().last_image_digest == ""
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ~/Work/Zolli-Labs/flashml/flashnode && pytest tests/test_trusted_runner.py -q`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement**

```python
"""Trusted-pool argv execution: no container, no sandbox, by explicit
operator opt-in only.

This runner exists for hosts that CANNOT run Docker — Colab notebooks and
provider pods are themselves containers — inside a team pool whose members
chose to trust each other. It is not a security boundary and never claims
to be: the placement contract (pool + allowFallback + the operator's
--runner trusted opt-in) is what keeps strangers' code away from it.

Same interface as SubprocessRunner/ArgvDockerRunner:
run(payload, workdir, inputs) -> outdir.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from flashnode.executor.runner import TaskExecutionError, task_env

_CONTAINER_WORKDIR = "/work"


class TrustedArgvRunner:
    def __init__(self, timeout_seconds: float = 600.0):
        self.timeout_seconds = timeout_seconds
        # Evidence attributes, same contract and same reset rule as
        # SubprocessRunner: a stale value is a measurement of a DIFFERENT
        # run wearing this one's name.
        self.last_exit_code: int | None = None
        #: Always "" — no image bytes executed; echoing the payload's image
        #: reference would claim a container ran when none did.
        self.last_image_digest: str = ""

    def run(self, payload: dict, workdir: Path, inputs: dict[str, Path]) -> Path:
        self.last_exit_code = None
        argv = payload.get("argv")
        if not isinstance(argv, list) or not argv or not all(
            isinstance(a, str) for a in argv
        ):
            raise TaskExecutionError(
                "trusted runner requires a payload with a string-list 'argv'"
            )
        # Rewrite /work-prefixed TOKENS onto the real workdir. Token-wise,
        # never substring: an argument that merely contains "/work" belongs
        # to the submitter. The compiled argv uses /work because the docker
        # runners bind the workdir there; this runner has no container, so
        # /work is a naming convention to honour, not a mount to make.
        rewritten = [
            str(workdir) + a[len(_CONTAINER_WORKDIR):]
            if a == _CONTAINER_WORKDIR or a.startswith(_CONTAINER_WORKDIR + "/")
            else a
            for a in argv
        ]
        outdir = workdir / "out"
        outdir.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.run(
                rewritten,
                cwd=workdir,
                env=task_env(),
                timeout=self.timeout_seconds,
                capture_output=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise TaskExecutionError(
                f"task exceeded {self.timeout_seconds}s"
            ) from exc
        except OSError as exc:
            raise TaskExecutionError(f"could not start task: {exc}") from exc
        self.last_exit_code = proc.returncode
        if proc.returncode != 0:
            tail = proc.stderr.decode(errors="replace")[-2000:]
            raise TaskExecutionError(
                f"task exited {proc.returncode}: {tail}"
            )
        return outdir
```

Match `SubprocessRunner.run`'s ending (it returns the out directory; read it and mirror any `metrics.json` expectations the loop has — if the loop requires `metrics.json`, that is the workload's contract, not the runner's, so do not synthesize one here).

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_trusted_runner.py -q` → PASS. Then `pytest -q` → no regressions.

- [ ] **Step 5: Commit**

```bash
git add flashnode/flashnode/executor/trusted_runner.py flashnode/tests/test_trusted_runner.py
git commit -m "feat(flashnode): TrustedArgvRunner — pool argv without a container, opt-in only"
```

### Task 7: `flashnode work --runner trusted`, registration, floor, release 0.3.4

**Files:**
- Modify: `flashnode/flashnode/agent/cli.py` (`--runner` choices ~`:176`, runner construction ~`:195-254`, `discover(...)` call ~`:274-284`)
- Modify: `flashnode/flashnode/inventory/capabilities.py` (`discover` signature + `NodeRegistration(...)` kwargs)
- Modify: `flashnode/pyproject.toml` (version 0.3.4, floor `>=0.4.3,<0.5`, comment)
- Test: `flashnode/tests/test_cli_trusted.py` (new)

**Interfaces:**
- Consumes: Task 6's `TrustedArgvRunner`; Task 1's `unsandboxed_argv_capable`.
- Produces: `flashnode work --runner trusted` runs the trusted runner and registers `unsandboxed_argv_capable=True, argv_capable=False, sandbox_capable=False, module_capable=True`. The Colab/RunPod docs (Task 17) print exactly this command.

- [ ] **Step 1: Write the failing tests**

```python
"""--runner trusted: the opt-in wiring. The flag is the ONLY path to
unsandboxed_argv_capable=True — discover() must default it False for every
other caller (security fields fail closed)."""

from flashnode.inventory.capabilities import discover


def test_discover_defaults_unsandboxed_argv_capable_false():
    reg = discover("n-1", kubernetes_node="")
    assert reg.unsandboxed_argv_capable is False


def test_discover_can_opt_in():
    reg = discover("n-1", kubernetes_node="", unsandboxed_argv_capable=True)
    assert reg.unsandboxed_argv_capable is True
    assert reg.argv_capable is False  # trusted is NOT the containerised contract


def test_trusted_runner_selected_for_runner_trusted(monkeypatch):
    """Read agent/cli.py's runner-selection tests first and copy their
    harness (they construct opts and assert on the runner type / the
    registration passed to client.register). Assert:
      - opts.runner == "trusted" builds a TrustedArgvRunner
      - the registration carries unsandboxed_argv_capable=True
      - the docker doctor gate is NOT invoked (trusted is not in the
        ("docker", "argv") branch)
      - a plain-words warning is printed that pool jobs run unsandboxed.
    """
```

(The third test's body depends on the existing CLI test harness — `flashnode/tests/` has runner-selection tests for `--runner argv`; mirror one exactly. Writing it as a stub docstring here would be a plan failure, so: copy `test_agent`'s `--runner argv` selection test wholesale, change the flag to `trusted`, and assert the four facts listed.)

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_cli_trusted.py -q` → FAIL (`discover` has no such parameter; CLI rejects the choice).

- [ ] **Step 3: Implement**

`capabilities.py`: add keyword `unsandboxed_argv_capable: bool = False` to `discover`'s signature and pass it through to `NodeRegistration(...)` with a comment matching the `argv_capable` one ("Set by the agent only for `--runner trusted` — never inferred"). `cli.py`: add `"trusted"` to the `--runner` choices; in the construction block:

```python
    elif opts.runner == "trusted":
        from flashnode.executor.trusted_runner import TrustedArgvRunner

        print(
            "trusted runner: pool jobs from your team run UNSANDBOXED on this\n"
            "machine — no container, no network isolation. Only continue if\n"
            "every member of your pool is someone you trust to run code here."
        )
        runner = TrustedArgvRunner()
```

(placed OUTSIDE the `("docker", "argv")` branch so the Docker doctor gate is skipped — same as subprocess). Update the `discover(...)` call:

```python
        argv_capable=(opts.runner == "argv"),
        module_capable=(opts.runner != "argv"),
        unsandboxed_argv_capable=(opts.runner == "trusted"),
```

`pyproject.toml`: version `0.3.4`, floor `"flashruntime>=0.4.3,<0.5"`, comment noting the floor moves because `discover` passes a field 0.4.2 does not declare (pydantic would refuse the kwarg — same import-time-hard-failure shape as 0.3.2/0.3.3, and the same reason the floor must move IN THIS COMMIT, not at tag time).

- [ ] **Step 4: Run tests** — `pytest -q` (flashnode full suite) → PASS.

- [ ] **Step 5: Commit, merge, tag, verify**

```bash
git add -A && git commit -m "feat(flashnode): --runner trusted; release 0.3.4 with floor >=0.4.3"
git checkout main && git merge --no-ff feat/team-pools-node && git push origin main
git tag flashnode-v0.3.4 && git push origin flashnode-v0.3.4
```

Verify like Task 5 (release workflow, `resolvable` gate, clean-venv install).

## Phase 3 — cloud API (private repo, branch `develop`)

Until Task 18 moves the pins, develop against the sibling checkout: `cd ~/Work/Zolli-Labs/flashml-cloud/flashml-cloud/apps/api && uv pip install -e "../../../../flashml/flashruntime[service]"` (and `make e2e-setup LOCAL=1` for e2e). A green run that way is not release evidence; CI stays green because Tasks 5/7 released first.

### Task 8: Migration 0007 — pools, members, invites, job binding, admission

**Files:**
- Create: `flashml-cloud/apps/api/migrations/0007_pools.sql`
- Modify: `flashml-cloud/apps/api/tests/test_schema.py:27` (`ALL_TABLES`)

**Interfaces:**
- Produces: tables `public.pools`, `public.pool_members`, `public.pool_invites`; columns `jobs.pool_id`, `profiles.admitted_at`. Tasks 9–13 depend on these exact names.

- [ ] **Step 1: Extend the schema test first**

In `test_schema.py`, extend `ALL_TABLES`:

```python
ALL_TABLES = TABLES + ["job_rounds", "pools", "pool_members", "pool_invites"]
```

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_schema.py -q` → FAIL (tables missing).

- [ ] **Step 2: Write the migration**

Copy the header shape of `0004_attempts.sql` (WHY block, then the two boilerplate paragraphs "HOW THIS IS APPLIED…" and "Do not edit this file after it has been applied anywhere…"). Before writing, check `0001_initial.sql` for the uuid default expression `machines.id` uses and copy it exactly. Body:

```sql
create table if not exists public.pools (
    id         uuid primary key default gen_random_uuid(),
    name       text not null,
    owner_id   uuid not null references public.profiles(id) on delete cascade,
    created_at timestamptz not null default now()
);
comment on table public.pools is
    'A team: members pool all their machines and any member''s pool-scoped '
    'jobs run only on the team''s workers. Owned by the profile that created '
    'it; the owner is also a pool_members row.';
alter table public.pools enable row level security;
create index if not exists pools_owner_id_idx on public.pools (owner_id);

create table if not exists public.pool_members (
    pool_id   uuid not null references public.pools(id) on delete cascade,
    user_id   uuid not null references public.profiles(id) on delete cascade,
    joined_at timestamptz not null default now(),
    primary key (pool_id, user_id)
);
comment on table public.pool_members is
    'Membership. A machine''s pools are its OWNER''s memberships resolved at '
    'stamp time (machines.owner_id -> pool_members.user_id) — machines are '
    'never members directly.';
alter table public.pool_members enable row level security;
create index if not exists pool_members_user_id_idx on public.pool_members (user_id);

create table if not exists public.pool_invites (
    token_hash     text primary key,
    pool_id        uuid not null references public.pools(id) on delete cascade,
    created_by     uuid not null references public.profiles(id) on delete cascade,
    expires_at     timestamptz not null,
    uses_remaining integer not null,
    created_at     timestamptz not null default now()
);
comment on table public.pool_invites is
    'One invite link: sha256 of the raw token (raw returned exactly once, '
    'like machine tokens). Consuming an invite both ADMITS the account '
    'through the alpha signup gate and joins it to the pool.';
alter table public.pool_invites enable row level security;
create index if not exists pool_invites_pool_id_idx on public.pool_invites (pool_id);

-- A pool-scoped job remembers its pool; null is every pre-pools job.
alter table public.jobs add column if not exists
    pool_id uuid references public.pools(id);
create index if not exists jobs_pool_id_idx on public.jobs (pool_id);

-- The invite-only alpha gate. Everyone who existed before the gate is
-- grandfathered: locking out the owner's own accounts teaches nothing.
alter table public.profiles add column if not exists admitted_at timestamptz;
update public.profiles set admitted_at = now() where admitted_at is null;
```

- [ ] **Step 3: Run the suite** — `.venv/bin/pytest tests/test_schema.py tests/test_migrate.py -q` → PASS (conftest applies real migrations; `test_discover_finds_every_real_migration_in_order` picks 0007 up automatically).

- [ ] **Step 4: Commit**

```bash
git add flashml-cloud/apps/api/migrations/0007_pools.sql flashml-cloud/apps/api/tests/test_schema.py
git commit -m "feat(db): 0007 — pools, members, invites, jobs.pool_id, admitted_at"
```

### Task 9: db.py pool functions

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/db.py` (new `# pools` section after `# contributions`)
- Test: `flashml-cloud/apps/api/tests/test_db_pools.py` (new)

**Interfaces:**
- Consumes: Task 8's tables; house patterns (`db: psycopg.Connection` first, keyword-only writers, owner-in-the-query, single-statement claims).
- Produces (exact signatures Tasks 10–13 call):

```python
def create_pool(db, *, name: str, owner_id: str) -> dict[str, Any]         # pool row; also inserts owner membership
def list_pools_for_user(db, user_id: str) -> list[dict[str, Any]]          # id, name, owner_id, member_count, machines_online, created_at
def fetch_pool_for_member(db, pool_id: str, user_id: str) -> dict | None   # None for non-members AND unknown ids (404 doctrine)
def list_pool_members(db, pool_id: str) -> list[dict[str, Any]]            # user_id, display_name, joined_at, machine_count, machines_online
def is_pool_member(db, pool_id: str, user_id: str) -> bool
def pool_ids_for_machine_owner(db, owner_id: str) -> list[str]             # sorted, for the proxy stamp
def create_pool_invite(db, *, pool_id: str, created_by: str, token_hash: str,
                       expires_at: datetime, uses: int) -> None
def consume_pool_invite(db, *, token_hash: str, user_id: str) -> dict | None
    # single UPDATE...RETURNING decrement (claim_attempt_credit idiom):
    # valid+unexpired+uses>0 -> decrements, inserts membership, admits the
    # profile (admitted_at = coalesce(admitted_at, now())), returns
    # {"pool_id", "name"}; else None for every do-not-admit case at once
def profile_is_admitted(db, user_id: str) -> bool
def fetch_job_for_viewer(db, job_id: str, user_id: str) -> dict | None
    # owner OR member of the job's pool; None otherwise (404 doctrine)
def list_pool_job_ids_for_member(db, user_id: str) -> list[str]
def list_job_contributions(db, job_id: str) -> list[dict[str, Any]]
    # node_id, machine_name, member_display_name, tasks_credited, total_duration_s
```

`machines_online` uses `status = 'active' and last_seen_at > now() - interval '90 seconds'` — the 90s threshold matching the console's `ONLINE_WITHIN_MS`, now asserted server-side in one place. `POOL_PUBLIC_COLUMNS` excludes nothing sensitive today but `pool_invites` never gets a public-columns tuple containing `token_hash`.

- [ ] **Step 1: Write failing tests** — model on existing db-layer tests (they run against the migrated ephemeral Postgres via the `db` fixture from `test_jobs_from_repo.py`). Cover at minimum: create_pool inserts the owner membership; list_pools counts members and online machines (insert a machine row with `last_seen_at = now()` and one stale); fetch_pool_for_member returns None for a non-member; consume_pool_invite decrements exactly once under two concurrent consumers of a 1-use invite (two sequential calls: second returns None), refuses expired, admits the profile; fetch_job_for_viewer allows owner, allows pool member, refuses others; list_job_contributions joins names.

- [ ] **Step 2: Run to verify failure** — `.venv/bin/pytest tests/test_db_pools.py -q` → FAIL (functions absent).

- [ ] **Step 3: Implement** following the verbatim house style (section banner comment, why-docstrings naming the failure each guard prevents, `on conflict do nothing` for the membership insert, `assert row is not None` after infallible RETURNING). `consume_pool_invite` is one transaction: the decrement UPDATE...RETURNING, then membership insert, then the profile admit — all inside `with db.transaction():` despite autocommit (psycopg supports explicit transactions on autocommit connections).

- [ ] **Step 4: Run** — `.venv/bin/pytest tests/test_db_pools.py -q` → PASS; full suite no regressions.

- [ ] **Step 5: Commit** — `git commit -m "feat(db): pool queries — membership, invites, admission, viewer scope, credit view"`

### Task 10: Invites, admission gate, pool CRUD endpoints

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py` (new browser routes; a new `admitted_user` dependency beside `current_user` ~`:609`)
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/auth.py` (invite token helpers beside the machine-token ones ~`:180`)
- Test: `flashml-cloud/apps/api/tests/test_pools_api.py` (new)

**Interfaces:**
- Consumes: Task 9's functions; `current_user`; `_json_object`.
- Produces:
  - `POST /v1alpha1/pools` `{name}` → 201 pool (admitted users)
  - `GET /v1alpha1/pools` → list with `member_count`, `machines_online`
  - `GET /v1alpha1/pools/{pool_id}` → pool + members (member-scoped, 404 doctrine)
  - `POST /v1alpha1/pools/{pool_id}/invites` `{expires_hours?}` → `{token}` raw once (pool owner only, 404 doctrine)
  - `POST /v1alpha1/invites/accept` `{token}` → `{pool_id, name}` (any signed-in user; this is the admission bootstrap, so `current_user` NOT `admitted_user`)
  - `GET /v1alpha1/me` response gains `"admitted": bool`
  - dependency `admitted_user` → 403 `{"detail": "invite required"}`; applied to pool create, `POST /v1alpha1/jobs`, `POST /v1alpha1/jobs/from-repo`, `POST /v1alpha1/device/approve`
- Token helpers: `new_invite_token()` → `"fmi_" + secrets.token_urlsafe(32)`, `hash_invite_token()` = sha256 hex, `looks_like_invite_token()` — mirror the `fmk_` trio verbatim.

- [ ] **Step 1: Write failing tests** — model on `test_profile.py` (JWT helper, `make_client`, `_new_user`). Cover: create → list → get round trip; a second user cannot GET a pool they are not in (404, not 403); invite create by non-owner is 404; accept with a valid token admits an un-admitted account AND joins (assert `GET /me` flips `admitted`); accept with a consumed/expired token is 404; an un-admitted account gets 403 from pool create, from-repo submit, and device approve, but 200 from `GET /me` and `GET /v1alpha1/jobs`; the raw token appears exactly once in the create-invite response and `token_hash` never appears in any response body.

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** `admitted_user`:

```python
    def admitted_user(
        user_id: str = Depends(current_user),
        db: psycopg.Connection = Depends(db_conn),
    ) -> str:
        """current_user plus the alpha's invite gate. Reads (jobs, machines,
        /me) stay open to un-admitted accounts — the console needs /me to
        know to SHOW the enter-invite screen — but everything that creates
        state requires admission. 403, not 404: unlike a resource id, the
        gate's existence is not a secret."""
        if not dbmod.profile_is_admitted(db, user_id):
            raise HTTPException(status_code=403, detail="invite required")
        return user_id
```

Swap `Depends(current_user)` → `Depends(admitted_user)` on exactly the four routes named above. The accept route calls `dbmod.upsert_profile` first (the account may be brand-new), then `consume_pool_invite`.

- [ ] **Step 4: Run** — `tests/test_pools_api.py -q` then full suite (watch `test_profile.py` — `/me` gains a key, additive so existing assertions hold).

- [ ] **Step 5: Commit** — `git commit -m "feat(api): pools, invites, and the invite-only admission gate"`

### Task 11: Proxy stamping — membership reaches the coordinator

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py` (`_scrub_identity` ~`:372`, `register_node` ~`:1416`, `node_heartbeat` ~`:1424`)
- Test: extend `flashml-cloud/apps/api/tests/test_agent_proxy.py`

**Interfaces:**
- Consumes: Task 9's `pool_ids_for_machine_owner`; `Machine.owner_id`.
- Produces: register bodies carry `capabilities.pools = <owner's memberships>` (agent value overwritten, never merged); heartbeat bodies carry top-level `pools = <same>`. Task 2's coordinator honors both.

- [ ] **Step 1: Write failing tests** — beside `test_register_body_node_id_is_overwritten`, same harness (`RecordingTransport`, real enrolment):

```python
def test_register_body_pools_is_stamped_from_membership(client, machine, transport, db):
    pool = dbmod.create_pool(db, name="team", owner_id=machine["owner"])
    client.post(
        "/v1alpha1/nodes/register",
        json={"schema_version": "v1alpha1", "node_id": machine["node_id"],
              "hostname": "h",
              "capabilities": {"cpu_cores": 4, "pools": ["forged-pool"]}},
        headers={"Authorization": f"Bearer {machine['token']}"},
    )
    body = json.loads(transport.last.read())
    assert body["capabilities"]["pools"] == [pool["id"]]
    assert "forged-pool" not in json.dumps(body)


def test_register_with_no_membership_stamps_empty(client, machine, transport):
    """[] not absent: an agent-supplied value must be OVERWRITTEN even when
    the truthful answer is 'no pools'."""
    client.post(
        "/v1alpha1/nodes/register",
        json={"schema_version": "v1alpha1", "node_id": machine["node_id"],
              "hostname": "h", "capabilities": {"pools": ["forged-pool"]}},
        headers={"Authorization": f"Bearer {machine['token']}"},
    )
    body = json.loads(transport.last.read())
    assert body["capabilities"]["pools"] == []


def test_heartbeat_carries_the_membership_refresh(client, machine, transport, db):
    pool = dbmod.create_pool(db, name="team", owner_id=machine["owner"])
    client.post(
        f"/v1alpha1/nodes/{machine['node_id']}/heartbeat",
        json={"schema_version": "v1alpha1", "node_id": machine["node_id"]},
        headers={"Authorization": f"Bearer {machine['token']}"},
    )
    body = json.loads(transport.last.read())
    assert body["pools"] == [pool["id"]]
```

(Use unique pool names / the module fixture's teardown pattern; check how other tests in the file create db rows.)

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Extend `_scrub_identity(body, node_id, *, force, pools=None)`: when `pools is not None`, on the empty-body branch emit `{"node_id": node_id, "pools"/"capabilities": ...}` per route; on the parsed branch set `parsed.setdefault("capabilities", {})` then `parsed["capabilities"]["pools"] = list(pools)` for register, and `parsed["pools"] = list(pools)` for heartbeat — pick the placement with a `where: Literal["capabilities", "top"]` parameter rather than sniffing the path. In the two route handlers resolve memberships before proxying:

```python
        try:
            with contextlib.closing(app.state.connect()) as conn:
                pools = dbmod.pool_ids_for_machine_owner(conn, machine.owner_id)
        except Exception:
            # Fail CLOSED: a node we cannot vouch for serves no pool this
            # cycle. Never skip the stamp — skipping would forward whatever
            # the agent claimed.
            log.warning("could not resolve pools for machine %s", machine.id)
            pools = []
```

For heartbeat, fold the `touch_machine_last_seen` call into the same connection open. `proxy()` gains a `pools=None, pools_where="capabilities"` pass-through to `_scrub_identity`. The anonymous-traffic test (`test_anonymous_traffic_costs_no_database_connection`) must stay green — the lookup sits after `current_machine`, so it does.

- [ ] **Step 4: Run** — extended `test_agent_proxy.py -q`, then full suite.

- [ ] **Step 5: Commit** — `git commit -m "feat(api): stamp pool membership onto register and heartbeat — overwritten, never merged"`

### Task 12: Compile coupling + pool-scoped submission

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/compile.py` (both compilers)
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py` (`POST /v1alpha1/jobs/from-repo` ~`:919`; `POST /v1alpha1/jobs` ~`:871`; `insert_job` call sites)
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/db.py` (`insert_job` gains keyword-only `pool_id: str | None = None`)
- Test: extend `tests/test_compile.py`; extend `tests/test_jobs_from_repo.py`

**Interfaces:**
- Consumes: Task 4's recipe behaviour (0.4.3), Task 9's `is_pool_member`/`fetch_pool_for_member`, Task 10's `admitted_user`.
- Produces: `compile_to_jobspec(..., pool: str | None = None)` and `compile_federated_round(..., pool: str | None = None)` — pool set ⇒ spec carries `placement.pool` and `isolation.allowFallback: true`; pool None ⇒ byte-identical to today. Request field `pool` on from-repo. `jobs.pool_id` persisted. Task 13's visibility and Task 15's UI depend on these.

- [ ] **Step 1: Write failing tests**

In `test_compile.py`:

```python
def test_pool_sets_placement_and_couples_the_waiver():
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo", pool="p-1")
    assert spec["spec"]["placement"]["pool"] == "p-1"
    assert spec["spec"]["isolation"] == {"tier": "sandboxed", "allowFallback": True}


def test_no_pool_is_byte_identical_to_before():
    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo")
    assert spec["spec"]["isolation"] == {"tier": "sandboxed", "allowFallback": False}
    assert spec["spec"]["placement"]["pool"] == "any"


def test_the_pool_spec_is_accepted_by_the_real_command_recipe():
    from flashruntime.recipes.command import CommandRecipe

    spec = compile_to_jobspec(_config(), PYTORCH, CODE_URI, "demo", pool="p-1")
    tasks = CommandRecipe().expand("job-123", JobSpec.model_validate(spec))
    assert tasks[0].payload["pool"] == "p-1"
    assert tasks[0].payload["isolation"]["allowFallback"] is True
```

In `test_jobs_from_repo.py`: pool member submits with `"pool": <id>` → 2xx and the jobs row has `pool_id`; non-member submitting to that pool → 404; un-admitted → 403; `POST /v1alpha1/jobs` with a spec carrying `allowFallback: true` or `placement.pool != "any"` → 400 naming from-repo as the pool path.

- [ ] **Step 2: Run to verify failure** (the recipe test needs the 0.4.3 editable install from the phase header).

- [ ] **Step 3: Implement.** Both compilers gain keyword-only `pool: str | None = None`; the isolation line becomes the invariant with its comment updated:

```python
            # The one exception to "fixed and not configurable": a POOL job
            # carries the waiver, because the seventh placement gate confines
            # it to machines whose owners joined the submitter's team. The
            # invariant is bidirectional and pinned by tests both ways:
            # allowFallback iff pool. CommandRecipe enforces the same rule
            # upstream, so a spec that violates it cannot even expand.
            "isolation": {"tier": "sandboxed", "allowFallback": pool is not None},
            "placement": {"pool": pool if pool is not None else "any"},
```

From-repo route: read `pool` from the body (optional string); when present, `fetch_pool_for_member(db, pool, user_id)` → 404 "unknown pool" on None (404 doctrine); pass `pool=pool` into whichever compiler runs, and `pool_id=pool` into `insert_job` (also add `"pool": pool` into the `source` dict beside `mode`). Raw `POST /v1alpha1/jobs`: after `payload.pop("owner_id", None)`, refuse pool/waiver specs:

```python
        spec_inner = payload.get("spec") if isinstance(payload.get("spec"), dict) else {}
        isolation = spec_inner.get("isolation") or {}
        placement = spec_inner.get("placement") or {}
        if isolation.get("allowFallback") or placement.get("pool", "any") != "any":
            raise HTTPException(
                status_code=400,
                detail="pool jobs must be submitted via /v1alpha1/jobs/from-repo",
            )
```

- [ ] **Step 4: Run** — `tests/test_compile.py tests/test_jobs_from_repo.py -q`, then full suite. `test_isolation_tier_is_sandboxed_with_no_fallback` must still pass (no-pool path unchanged).

- [ ] **Step 5: Commit** — `git commit -m "feat(api): pool-scoped submission — the waiver exists iff the pool does"`

### Task 13: Pool visibility — member job reads and the credit view

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py` (`GET /v1alpha1/jobs` ~`:1121`, the four job read endpoints using `fetch_job_for_owner`, new `GET /v1alpha1/jobs/{job_id}/contributions`)
- Test: `flashml-cloud/apps/api/tests/test_pool_visibility.py` (new)

**Interfaces:**
- Consumes: Task 9's `fetch_job_for_viewer`, `list_pool_job_ids_for_member`, `list_job_contributions`.
- Produces: pool members can read (never cancel) each other's pool jobs; `GET /v1alpha1/jobs` unions a third source (pool jobs); the contributions endpoint returns `[{node_id, machine_name, member_display_name, tasks_credited, total_duration_s}]`. Task 15 renders it.

- [ ] **Step 1: Write failing tests** — member of the job's pool: 200 on job/rounds/events/tasks/contributions, 404 on cancel (owner-only); non-member: 404 on all; the jobs list shows a pool-mate's pool job exactly once (no duplicate when viewer is also the owner).

- [ ] **Step 2–4:** swap `fetch_job_for_owner` → `fetch_job_for_viewer` on the four **read** endpoints only (cancel keeps owner scope — read each endpoint before editing); union `list_pool_job_ids_for_member` into the jobs list with a `seen` set; add the contributions route (viewer-scoped, `_jsonable` like rounds). Run, PASS, no regressions.

- [ ] **Step 5: Commit** — `git commit -m "feat(api): pool members read pool jobs; the per-member credit view"`

## Phase 4 — web console

Run tests with `cd flashml-cloud/apps/web && npm test`. Every new page: thin server `layout.tsx` exporting only `metadata`, `"use client"` page, no stray page exports (`route-exports.test.ts` enforces this automatically).

### Task 14: Client functions, admission gate screen, /pools pages

**Files:**
- Modify: `flashml-cloud/apps/web/lib/cloud-api.ts` (+ `lib/cloud-api.test.ts`)
- Create: `flashml-cloud/apps/web/app/(console)/pools/page.tsx` + `layout.tsx`; `app/(console)/pools/[poolId]/page.tsx` + `layout.tsx`
- Modify: `flashml-cloud/apps/web/components/shell/ConsoleShell.tsx` (`GROUPS`: add Pools to the Fleet group — "Routes that do not exist yet are NOT listed", so this lands in the same commit as the pages)
- Modify: the console shell (or `(console)/layout.tsx`) for the invite gate

**Interfaces:**
- Consumes: Task 10's endpoints; `getMe().admitted`.
- Produces (types in `cloud-api.ts`, the single source of response shapes):

```ts
export interface Pool { id: string; name: string; owner_id: string;
  member_count: number; machines_online: number; created_at: string; }
export interface PoolMember { user_id: string; display_name: string | null;
  joined_at: string; machine_count: number; machines_online: number; }
export function listPools(): Promise<Pool[]>
export function createPool(name: string): Promise<Pool>
export function getPool(poolId: string): Promise<{pool: Pool; members: PoolMember[]}>
export function createPoolInvite(poolId: string): Promise<{token: string}>
export function acceptInvite(token: string): Promise<{pool_id: string; name: string}>
```

and `Me` gains `admitted: boolean`.

- [ ] **Step 1: Failing client tests** — in `cloud-api.test.ts`, reuse the mocked-supabase + mocked-fetch harness verbatim: `listPools` attaches the bearer; `acceptInvite` POSTs `{token}` to `/v1alpha1/invites/accept`; `createPoolInvite` never logs/echoes the token beyond returning it.

- [ ] **Step 2: Implement client functions** (thin `request<T>()` wrappers, same file section as `listMachines`). Run `npm test` → green.

- [ ] **Step 3: The pages.** `/pools`: copy the `MachinesPage` skeleton wholesale (title row + refresh, `metric-lg` stats "Pools / Workers online", four-branch body, `min-w-[620px]` table: Name / Members / Workers online / Created). Create-pool uses a `Card` + one `Input` + primary button; invite link renders as `${location.origin}/pools/join?token=${token}` in a read-only input with a copy button and the one-time warning ("this link is shown once"). `/pools/[poolId]`: `use(params)`, members table (Member / Machines / Online / Joined), invite section for the owner. **Invite gate:** in the console shell, when `getMe()` resolves with `admitted: false`, render an `InviteGate` component (one `Card`: "FlashML is invite-only right now", an `Input` for the invite link or token — parse the token out of a pasted URL — and a button calling `acceptInvite` then reloading) instead of `children`. Also create `app/(console)/pools/join/page.tsx` that reads `?token=` and calls `acceptInvite` on mount, redirecting to the pool.

- [ ] **Step 4: Run** — `npm test` (route-exports picks the new pages up; add pure-logic tests for the token-out-of-URL parser you write for InviteGate).

- [ ] **Step 5: Commit** — `git commit -m "feat(web): pools pages, invite links, and the invite gate"`

### Task 15: Submit pool selector; job page credit view

**Files:**
- Modify: `flashml-cloud/apps/web/app/(console)/submit/page.tsx`; `lib/cloud-api.ts` (`submitFromRepo(repo, ref?, pool?)`, `listJobContributions(jobId)`)
- Modify: `flashml-cloud/apps/web/app/(console)/jobs/[jobId]/page.tsx` (+ a small `components/jobs/MemberCredits.tsx`)

**Interfaces:**
- Consumes: Tasks 12–13's endpoints; `listPools()`.
- Produces: the submit form's third field (pool `Select`, defaulting to "No pool — public queue"), the amber zero-workers warning, the plain-words unsandboxed notice, and the job page's "Member credits" section.

- [ ] **Step 1: Widen `submitFromRepo`** to include `pool` in the POST body only when set; client test asserting the body shape both ways.

- [ ] **Step 2: The selector.** Third `space-y-1.5` div after Branch: `Label` "Pool" + `Select` (from `components/ui/select.tsx`) fed by `listPools()` on mount. When a pool with `machines_online === 0` is selected, render the amber variant of the error banner div (`border-amber-400/30 bg-amber-400/10 text-amber-400`) above the submit button: "0 workers online in this pool right now — the job will queue until one connects." When any pool is selected, render a muted one-liner under the selector: "Pool jobs run without a container sandbox on your team's machines. Every member you invited can run code this job stages." Submission itself is never blocked — the warning informs.

- [ ] **Step 3: Member credits.** `MemberCredits` renders `listJobContributions(jobId)` as a table (Member / Machine / Tasks credited / Time) below `RoundProgress`'s contributors section, fetched with the same `soft` error pattern as rounds/tasks/events. Empty result renders nothing (non-pool jobs).

- [ ] **Step 4: Run** — `npm test`; then manually: `./scripts/dev.sh --all`, submit against a local pool, see the selector, warning, and notice.

- [ ] **Step 5: Commit** — `git commit -m "feat(web): pool selector with live eligibility, unsandboxed notice, member credits"`

## Phase 5 — e2e, docs, deploy

### Task 16: e2e pool scoping

**Files:**
- Create: `e2e/test_pool_scoping.py`

**Interfaces:**
- Consumes: the `coordinator` fixture (`e2e/conftest.py`), `ExecutorLoop`/`CoordinatorClient` inline-agent pattern (`test_kmeans_loop.py`), the feature-probe skip pattern (`test_gpu_placement.py`).

- [ ] **Step 1: Write the tests** (they PASS immediately under `LOCAL=1`, and self-skip on the released pin until Task 18 — that switch-on is the point):

```python
def _require_pool_support() -> None:
    """Feature probe, not a version string (the test_gpu_placement rule):
    NodeCapabilities.pools is what the whole chain hangs off."""
    proto = pytest.importorskip("flashruntime.protocol.v1alpha1")
    if "pools" not in proto.NodeCapabilities.model_fields:
        pytest.skip(
            "the resolved flashruntime has no NodeCapabilities.pools — this "
            "is the released pin, which predates team pools. Re-run after "
            "the release, or now with `make e2e-setup LOCAL=1`."
        )
```

Three tests, each calling `_require_pool_support()` first, registering nodes over real HTTP with `capabilities: {"pools": [...]}`:
1. **Two pools, one agent each** — a task with `payload["pool"] = "pool-a"`: agent B (pool-b) polls and gets 204s; agent A claims and completes it. Assert B's accepted count is 0.
2. **A trusted worker claims a pool argv job and never a public one** — node registered `unsandboxed_argv_capable=True, pools=["pool-a"]`, running `ExecutorLoop` with `TrustedArgvRunner`; a pool argv task (with `allowFallback: true`) completes; a public sandboxed argv task (no pool) stays unclaimed by it (claim returns 204).
3. **Membership revocation via heartbeat** — after a heartbeat with `pools: []`, the next claim is 204.

- [ ] **Step 2: Run** — `make e2e-setup LOCAL=1 && make e2e` → new tests PASS; the suite's existing tests stay green.

- [ ] **Step 3: Commit** — `git commit -m "test(e2e): pool scoping — the boundary, the trusted worker, the revocation"`

### Task 17: Docs — Colab, RunPod, console copy

**Files:**
- Create: `flashml-cloud/docs/guides/join-a-pool-colab.md`, `flashml-cloud/docs/guides/join-a-pool-runpod.md`
- Modify: `flashml-cloud/apps/web/app/(console)/docs/page.tsx` (volunteer copy — it currently documents `--runner argv`; add the pool section and the trusted-runner command)

- [ ] **Step 1: Write the guides.** Colab guide opens with the ToS box before any command:

> **Paid Colab only.** Google's Colab FAQ prohibits "running distributed computing workers" on the free tier, and prohibits "using multiple accounts to work around access or resource usage restrictions" on every tier. Enforcement lands on **your Google account**. Run this only on a paid Colab plan, one account, yours.

Then the three cells: `!pip install flashnode`, `!flashnode login` (prints the device-code URL — approve it from your phone), `!flashnode work --runner trusted`. State plainly what trusted means (pool jobs run unsandboxed; only your pool's jobs are ever placed here — the coordinator refuses everything else, fail closed). RunPod guide: same three commands in the pod terminal, note that a rented pod needs no ToS caveat, and that `--runner trusted` is the right choice because pods cannot nest Docker. Both end with "verify": the machine appears on `/machines` and the pool's workers-online count moves.

- [ ] **Step 2: Console docs page** — add the pool section beside the existing volunteer instructions; keep `flashnode doctor` ahead of the Docker path, `--runner trusted` for notebook/pod hosts.

- [ ] **Step 3: Commit** — `git commit -m "docs: join a pool from Colab (paid only) and RunPod"`

### Task 18: Pins, blueprint sync, deploy

**Files:**
- Modify: `Makefile` (`RUNTIME_VERSION := 0.4.3`, `NODE_VERSION := 0.3.4`), `render.yaml` (both coordinator `buildCommand` pins), `flashml-cloud/apps/api/pyproject.toml` (`flashruntime[service]==0.4.3`)

- [ ] **Step 1: Move all four pin sites in one commit.** Reinstall the api venv from the pin (undo the editable install): `make setup`. Run everything against the pins:

```bash
cd ~/Work/Zolli-Labs/flashml-cloud
make e2e-setup && make e2e      # pool e2e switches itself on — 0 skips expected from the new file
cd flashml-cloud/apps/api && .venv/bin/pytest -q
cd ../web && npm test
```

- [ ] **Step 2: Commit and merge** — `git commit -m "chore(pins): flashruntime 0.4.3 + flashnode 0.3.4 — team pools deployable"`, push `develop`, merge to `main` via the normal flow. CI (`api,web,e2e,secrets → migrate-prod → deploy-prod` gating) must be green.

- [ ] **Step 3: Blueprint sync BEFORE deploy** (the 2026-08-02 lesson: autoDeploy never re-reads `render.yaml`; the coordinators' `buildCommand` only changes on an explicit sync, and a "successful" deploy would reinstall 0.4.2). Render dashboard → Blueprint → Sync from `main`. Verify each coordinator's `/openapi.json` now carries `pools` (e.g. in `NodeCapabilities`) — the same check that caught the 0.4.0/0.4.1 drift.

- [ ] **Step 4: Deploy** — run `deploy-prod` (workflow_dispatch; it refuses without green CI for the SHA). Migration 0007 applies via the runner in the pipeline. Post-deploy: `/healthz` on api, sign in, create a pool, generate an invite, and run the acceptance loop from Task 17's guides with one real second account.

---

## Execution order and independence

Tasks 1→5 strictly ordered (public repo), 6→7 after 5. Cloud tasks 8→13 strictly ordered but independent of 6–7 until 16. Web 14–15 after 10/13. 16 after 7 (needs the trusted runner) — run under `LOCAL=1` until 18. 17 anytime after 7. 18 last.

## Self-review notes (already applied)

- Spec §3's "no new tier logic" and "released agents work unchanged" claims are superseded by the spec's Corrections section (argv gate + CommandRecipe refusal → Tasks 3, 4, 6, 7). Docker-capable hosts on flashnode 0.3.3 still work unchanged; only Docker-less hosts need 0.3.4.
- Spec §3 "rides the pending 0.4.2 release" → 0.4.2 shipped without the gate; this plan releases 0.4.3 (Task 5).
- Spec §5 "membership propagates at next heartbeat" required `NodeHeartbeat.pools` (Task 1) — the heartbeat message had no capabilities field to stamp.
- The `NodeRegistration.pool` (singular, "local") name collision is documented in Task 1's field docstrings.
