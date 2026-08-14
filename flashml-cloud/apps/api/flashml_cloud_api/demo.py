"""The judges' demo: one fixed job, two coordinators, no login.

**What this is for.** A hackathon judge opens a public URL with no account,
presses one button, and watches nine independent tasks spread across the four
Alibaba ECS anchors we operate in Singapore — once through the always-on
Render coordinator and once through the Alibaba Function Compute one, so the
two can be compared on wall-clock. The page is about COMPUTE: the fleet, the
tasks, where each one ran, how long it took, and what came out.

Two routes consume this module, both unauthenticated:

* ``GET  /v1alpha1/public/demo``     — the fleet and the latest run per venue
* ``POST /v1alpha1/public/demo/run`` — submit the fixed spec to one venue

---------------------------------------------------------------------------
THE ONE DEVIATION FROM ``job_share``, AND ITS EXACT LIMIT
---------------------------------------------------------------------------

``job_share.py`` is the reviewed public-data module and its rules hold here:
a job's output is the submitter's, so nothing this page publishes about a run
is a value user-supplied code produced. Read its docstring before adding a
field; the two questions it asks (*can you name the line in our code that
assigned this?* and *could it re-identify a person?*) are the whole review.

It pseudonymises machines — "machine A", "machine B" — because
``machines.name`` is the hostname a volunteer's agent supplied at enrolment,
and volunteer hostnames are personal. **That reasoning does not apply to this
fleet and the deviation is exactly that fact, not a relaxation of the rule.**
The four demo machines are ours: we rented them, we named them
(``alibaba-sgp-1`` … ``-4``), and they already carry ``machines.official =
true``, which migration 0030 defines as *platform-operated anchor capacity
with a named, accountable operator* and which ALREADY publishes the name to
strangers through ``network._label``. So this module calls that same
function rather than growing a second naming rule:

    :func:`public_machine_name` -> ``network._label(row, own=False)``

which returns the real name only for a row that is ``official`` AND named,
and the ordinary ``prov…`` handle for anything else. **A non-official machine
that somehow joins the demo pool is therefore anonymised automatically**, and
that is the property that keeps this deviation from widening: it is enforced
by the choke point, not by the fact that today's pool happens to hold only
our own hardware.

Nothing else is widened. No repo URL, no spec, no owner, no task parameters,
no stderr, no artifact bytes belonging to a submitter's files — except that
the artifact NAMES and SIZES of this one run ARE published, because the
"submitter" of a demo run is this codebase: the spec is fixed below, the
caller controls nothing but the venue, and the repository is a public example
we publish ourselves.

---------------------------------------------------------------------------
WHY THE STATE READS MATTER MORE THAN THEY LOOK
---------------------------------------------------------------------------

``jobs.finished_at`` is a CACHE, written when somebody's poll observes the
coordinator reporting a terminal state (``db.sync_observed_job_states``).
``app._claimable_venues`` reads ``public.jobs`` to decide whether the fleet
should poll Function Compute at all — *no FC job, no FC traffic*. A demo run
that is abandoned mid-flight therefore leaves a non-terminal FC row behind
for ever, and the entire fleet keeps invoking Function Compute indefinitely
(measured 2026-08-14: ~167 invocations per 5 minutes, with nobody watching).

So the demo's own reads refresh that cache — the route asks the coordinator
for state on every GET and writes a terminal answer down — and
:data:`DEMO_MAX_AGE_S` is the backstop for the run nobody ever looks at
again: past that age a non-terminal demo job is cancelled. The fixed spec
takes ~390 s on this fleet, so the age limit is a wide multiple of a healthy
run and can only ever catch a genuinely stuck one.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import psycopg

from . import db as dbmod
from . import network as networkmod

__all__ = [
    "DEMO_MAX_AGE_S",
    "DEMO_POOL_ID",
    "DEMO_REF",
    "DEMO_REPO_NAME",
    "DEMO_REPO_OWNER",
    "GUEST_ARCHITECTURES",
    "GUEST_POOL_NAME",
    "GUEST_REF",
    "GUEST_REPO_NAME",
    "GUEST_REPO_OWNER",
    "GUEST_SOURCE_KEY",
    "demo_owner_id",
    "demo_pool_exists",
    "demo_source",
    "ensure_guest_pool",
    "fleet_rows",
    "guest_pool_id",
    "guest_run",
    "guest_run_by_id",
    "guest_source",
    "is_demo_source",
    "is_guest_source",
    "latest_run_per_venue",
    "normalise_user_code",
    "public_machine_name",
    "render_artifact",
    "render_fleet",
    "render_joined_machine",
    "render_run",
    "render_task",
    "run_by_id",
    "run_for_venue",
    "task_rows",
]


# ---------------------------------------------------------------------------
# THE FIXED SPEC. The caller controls the venue and NOTHING ELSE.
#
# Every value here is a constant in this file rather than a field in the
# request body, and that is the security property of the POST route: it is an
# unauthenticated button that submits work onto machines we pay for, so the
# only thing a caller may influence is which of two control planes runs it.
# A body key that reached any of these would turn a demo into an open
# arbitrary-code-execution endpoint.
# ---------------------------------------------------------------------------

#: The public example repository. `sweep` is a 3x3 hyperparameter grid —
#: nine INDEPENDENT tasks on the `sklearn` image, measured at ~390 s on this
#: fleet.
#:
#: **It must stay independent.** A federated example cannot run on the `fc`
#: venue at all: `fedavg` drives its rounds against `settings.coordinator_url`
#: directly, so `_stage_compile_and_submit` refuses a non-default venue for a
#: federated config with a 409 — which would make half this demo impossible.
DEMO_REPO_OWNER = "Zolli-Labs"
DEMO_REPO_NAME = "flashml-examples"
DEMO_REF = "sweep"

#: Test-1 — the pool holding all four Alibaba anchors. A pool is what makes
#: named capacity reachable at all ("rented capacity needs a pool"), and it is
#: what confines the demo to hardware we operate instead of spraying an
#: unauthenticated button across volunteer machines.
DEMO_POOL_ID = "da904282-4c2c-4431-93b7-c106fef05acb"

#: The marker written into ``jobs.source`` so a demo run can be found again.
#: In ``source`` rather than a column because ``source`` is already the jsonb
#: this route's submit path controls end to end — see ``demo_source``.
DEMO_SOURCE_KEY = "demo"

#: How long a non-terminal demo run may live before it is cancelled. The
#: fixed spec finishes in ~390 s; this is a wide multiple of that, so it can
#: only catch a run that is genuinely stuck — and it exists because a stuck
#: FC run is not merely a stale row, it is a permanent invocation drip across
#: the whole fleet (see the module docstring).
DEMO_MAX_AGE_S = 1800.0


def demo_source() -> dict[str, Any]:
    """The ``jobs.source`` descriptor for a demo run.

    Identical in shape to what ``from-repo`` records for any GitHub submit,
    plus the one marker key. Written through the ordinary submit path, so a
    demo job is an ordinary job in every other respect — cancellable,
    listable, and settled by the same hooks.
    """
    return {
        "type": "github",
        "owner": DEMO_REPO_OWNER,
        "repo": DEMO_REPO_NAME,
        "ref": DEMO_REF,
        DEMO_SOURCE_KEY: True,
    }


def is_demo_source(source: Any) -> bool:
    """Is this ``jobs.source`` a demo run's?"""
    return isinstance(source, Mapping) and source.get(DEMO_SOURCE_KEY) is True


# ---------------------------------------------------------------------------
# who owns a demo run
# ---------------------------------------------------------------------------


def demo_owner_id(db: psycopg.Connection) -> str | None:
    """The single admitted profile, which owns every demo run.

    **Not an anonymous identity.** This deployment is invite-only and has one
    real user; minting a synthetic owner would create a profile no admission
    decision was ever made about, and every owner-scoped query in this API
    would then be answering for a person who does not exist. The demo is a
    job that user submitted, because it is.

    ``order by created_at`` so repeated calls agree, and ``None`` when no
    profile has been admitted yet — the route answers 503 rather than
    inventing one.
    """
    with db.cursor() as cur:
        cur.execute(
            "select id from public.profiles where admitted_at is not null"
            " order by created_at limit 1"
        )
        row = cur.fetchone()
    return None if row is None else str(row["id"])


# ---------------------------------------------------------------------------
# the fleet
# ---------------------------------------------------------------------------

#: Everything the fleet card needs and nothing else. ``node_id``/``name``/
#: ``official`` are consumed by :func:`public_machine_name` and only ``name``
#: ever reaches the wire — through that function, never directly.
_FLEET_COLUMNS = """
    m.id, m.node_id, m.name, m.official, m.capabilities, m.geo_country
"""


def fleet_rows(
    db: psycopg.Connection, *, pool_id: str = DEMO_POOL_ID
) -> list[dict[str, Any]]:
    """The demo pool's machines, with liveness.

    ``online`` comes from ``db.MACHINE_ONLINE_PREDICATE`` — the one
    server-side definition of the word in this codebase — rather than a
    freshness threshold invented here, so the count a judge sees and the count
    a submit is judged against can never drift apart.

    Ordered by name so the four cards do not shuffle between polls. A pool id
    that is not a uuid, or names no pool, yields no rows: the route renders an
    empty fleet rather than failing.
    """
    with db.cursor() as cur:
        try:
            cur.execute(
                f"""
                select {_FLEET_COLUMNS},
                       ({dbmod.MACHINE_ONLINE_PREDICATE}) as online
                  from public.machines m
                  join public.machine_pools mp on mp.machine_id = m.id
                 where mp.pool_id = %s::uuid
                 order by m.name nulls last, m.node_id
                """,
                (pool_id,),
            )
        except psycopg.errors.InvalidTextRepresentation:
            return []
        return list(cur.fetchall())


def public_machine_name(row: Mapping[str, Any]) -> str:
    """What a stranger may call this machine.

    **The one choke point, called rather than copied.** ``network._label``
    already decides this for every unauthenticated viewer in the product: an
    ``official`` machine that has been named shows its real name, and
    everything else shows the ``prov…`` handle. Reusing it is what keeps the
    deviation from ``job_share``'s pseudonyms confined to the fleet we
    operate — a volunteer machine that ends up in the demo pool is anonymised
    by this call without anybody having to remember to anonymise it.
    """
    return networkmod._label(row, own=False)


def render_fleet(row: Mapping[str, Any]) -> dict[str, Any]:
    """One machine as the demo page sees it.

    ``cpus``/``memory_gb`` are the agent's own self-reported capability
    numbers, surfaced and not interpreted — the same two fields
    ``network._specs`` reads, converted to the units a card shows. ``None``
    for a probe that reported nothing, never ``0``: this codebase keeps that
    distinction everywhere, and 0 GB is a claim about the hardware while
    ``None`` is the honest "we were not told".
    """
    caps = row.get("capabilities")
    caps = caps if isinstance(caps, Mapping) else {}
    cores = caps.get("cpu_cores")
    memory = caps.get("memory_bytes")
    return {
        "name": public_machine_name(row),
        "online": bool(row.get("online")),
        "region": row.get("geo_country"),
        "cpus": (
            int(cores)
            if isinstance(cores, (int, float)) and not isinstance(cores, bool)
            else None
        ),
        "memory_gb": (
            round(int(memory) / (1024**3), 1)
            if isinstance(memory, (int, float)) and not isinstance(memory, bool)
            else None
        ),
        "official": bool(row.get("official")),
    }


# ---------------------------------------------------------------------------
# the runs
# ---------------------------------------------------------------------------

#: What a demo run publishes off ``public.jobs``. The same four columns
#: ``job_share.JOB_SHARE_COLUMNS`` allows, plus ``coordinator`` — which the
#: share page deliberately withholds ("which control plane this deployment
#: runs a job on is infrastructure") and which this page exists to compare.
#: That is a decision about THIS payload only and does not widen the other.
_RUN_COLUMNS = "id, status, created_at, finished_at, coordinator"

#: `coordinator` is nullable (migration 0034) and NULL means the default
#: venue. Folded in SQL here because this query GROUPS by it; every other
#: reader in the API folds through ``app._venue_of``, which stays the one
#: place the fold is defined for a row in hand.
_VENUE_EXPR = "coalesce(coordinator, 'render')"


def latest_run_per_venue(db: psycopg.Connection) -> list[dict[str, Any]]:
    """The most recent demo run for each coordinator, newest first.

    At most two rows — one ``render``, one ``fc`` — because that is the
    comparison the page makes. History is deliberately not offered: a judge is
    looking at two runs side by side, and a list that grew without bound would
    be a second feature nobody asked for on an unauthenticated route.
    """
    with db.cursor() as cur:
        cur.execute(
            f"""
            select distinct on ({_VENUE_EXPR}) {_RUN_COLUMNS}
              from public.jobs
             where source->>'{DEMO_SOURCE_KEY}' = 'true'
             order by {_VENUE_EXPR}, created_at desc
            """
        )
        rows = list(cur.fetchall())
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return rows


def run_for_venue(db: psycopg.Connection, venue: str) -> dict[str, Any] | None:
    """The most recent demo run on ``venue``, or ``None``.

    The gate behind the POST route: if this row is non-terminal, the button
    hands its ``job_id`` back instead of submitting a second run.
    """
    with db.cursor() as cur:
        cur.execute(
            f"""
            select {_RUN_COLUMNS}
              from public.jobs
             where source->>'{DEMO_SOURCE_KEY}' = 'true'
               and {_VENUE_EXPR} = %s
             order by created_at desc
             limit 1
            """,
            (venue,),
        )
        return cur.fetchone()


def demo_pool_exists(db: psycopg.Connection, *, pool_id: str = DEMO_POOL_ID) -> bool:
    """Does the demo pool exist in this deployment?

    Checked BEFORE the repo is fetched, because ``jobs.pool_id`` is a foreign
    key (migration 0007) and ``insert_job`` runs AFTER the artifact has been
    staged and the coordinator has accepted the job. A missing pool would
    therefore not be a clean refusal — it would be a running job with no row
    to cancel it by. One indexed lookup buys the route the same "nothing
    written yet" guarantee every other refusal in the submit path gives.
    """
    with db.cursor() as cur:
        try:
            cur.execute(
                "select 1 from public.pools where id = %s::uuid", (pool_id,)
            )
        except psycopg.errors.InvalidTextRepresentation:
            return False
        return cur.fetchone() is not None


def run_by_id(db: psycopg.Connection, job_id: str) -> dict[str, Any] | None:
    """One demo run, re-read.

    Exists so a route that has just written a state observation can render the
    row the database now holds instead of the one it had in hand — in
    particular ``finished_at``, which ``sync_observed_job_states`` stamps with
    ``now()`` inside the UPDATE and which the caller therefore cannot know.
    Guessing it locally would put a number on the page that is close to the
    truth and is not it, and ``elapsed_s`` is the whole comparison.

    Filtered on the demo marker like every other read here: this must never
    become a general "any job by id" reader on an unauthenticated route.
    """
    with db.cursor() as cur:
        cur.execute(
            f"""
            select {_RUN_COLUMNS}
              from public.jobs
             where id = %s and source->>'{DEMO_SOURCE_KEY}' = 'true'
            """,
            (job_id,),
        )
        return cur.fetchone()


def task_rows(db: psycopg.Connection, job_id: str) -> list[dict[str, Any]]:
    """Per-task timing and placement for one demo run, from ``attempts``.

    The SAME evidence ``job_share.public_attempts_for_job`` publishes — every
    value here is written by ``db.record_attempt`` /
    ``claim_attempt_credit`` / ``record_attempt_failure`` /
    ``reconcile_expired_attempts`` — reshaped from one row per LEASE to one
    row per TASK, which is what a nine-task grid reads as.

    * ``started_at`` is the FIRST claim. A task that was requeued started when
      it first started, not when it was retried.
    * ``finished_at``/``outcome``/the machine come from the LAST attempt,
      because that is the one that decided the task's fate.
    * ``attempts`` is reported so a retried task is visibly a retried task
      rather than a slow one — the same reason ``public_job_view`` refuses to
      collapse "attempted" into "accepted".

    No federated join, and none is needed: the demo spec is independent by
    construction (see :data:`DEMO_REF`). The machine columns are selected for
    :func:`public_machine_name` and are never emitted raw.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            with mine as (
                select a.lease_id, a.task_id, a.machine_id, a.claimed_at,
                       a.resolved_at, a.outcome
                  from public.attempts a
                 where a.job_id = %s
            ),
            first_claim as (
                select task_id, min(claimed_at) as started_at,
                       count(*) as attempts
                  from mine group by task_id
            ),
            final as (
                select distinct on (task_id)
                       task_id, machine_id, resolved_at, outcome
                  from mine
                 order by task_id, claimed_at desc, lease_id desc
            )
            select f.task_id, c.started_at, c.attempts,
                   f.resolved_at as finished_at, f.outcome,
                   m.id as machine_id, m.node_id, m.name, m.official
              from final f
              join first_claim c on c.task_id = f.task_id
              left join public.machines m on m.id = f.machine_id
             order by f.task_id
            """,
            (job_id,),
        )
        return list(cur.fetchall())


# ---------------------------------------------------------------------------
# rendering
#
# A second narrowing on top of the SELECT lists above, exactly as
# ``job_share`` does it: the queries decide what leaves the database and these
# decide what the wire calls it.
# ---------------------------------------------------------------------------


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _elapsed_s(created_at: Any, finished_at: Any) -> float | None:
    """Wall-clock seconds — THE NUMBER THE WHOLE DEMO IS ABOUT.

    Measured against ``now()`` while a run is in flight so the page has a
    ticking clock, and frozen at ``finished_at`` once it stops. ``None`` only
    when the row has no ``created_at``, which cannot happen (``default now()``
    in 0001) and is handled anyway rather than raising from inside an
    unauthenticated read.
    """
    if not isinstance(created_at, datetime):
        return None
    end = finished_at if isinstance(finished_at, datetime) else _now(created_at)
    return round((end - created_at).total_seconds(), 1)


def _now(reference: datetime) -> datetime:
    """``now()`` in the same awareness as ``reference``. Postgres hands back
    aware datetimes for ``timestamptz``; the naive branch exists so a fixture
    that inserted a naive value cannot raise a TypeError on subtraction."""
    if reference.tzinfo is None:
        return datetime.now()
    return datetime.now(timezone.utc)


#: Coordinator task states that mean "this task is over". Used only to derive
#: a state when the coordinator could not be reached — see ``render_task``.
_TASK_STATE_BY_OUTCOME = {
    "accepted": "COMPLETED",
    "failed": "FAILED",
}


def render_task(
    task_id: str,
    *,
    state: str | None,
    attempt: Mapping[str, Any] | None,
    machine_name: str | None,
) -> dict[str, Any]:
    """One task as the demo page sees it.

    ``state`` is the coordinator's own ``TaskState`` when the route could read
    it. When it could not, the state is DERIVED from our attempt outcome —
    ``accepted`` -> ``COMPLETED``, ``failed`` -> ``FAILED``, an unresolved
    attempt -> ``LEASED``, nothing at all -> ``PENDING`` — so the page
    degrades to something honest instead of blanking. The derivation is only
    ever a fallback: the coordinator is the authority on task state, and an
    expired attempt looks exactly like a pending task from here, which is
    correct (the task IS claimable again) but is not something to infer when
    the real answer is available.
    """
    outcome = attempt.get("outcome") if attempt else None
    if state is None:
        if attempt is None:
            state = "PENDING"
        else:
            state = _TASK_STATE_BY_OUTCOME.get(str(outcome or ""), "LEASED")
    return {
        "task_id": task_id,
        "state": state,
        "machine": machine_name,
        "started_at": _iso(attempt.get("started_at")) if attempt else None,
        "finished_at": _iso(attempt.get("finished_at")) if attempt else None,
        "outcome": outcome,
        "attempts": int(attempt.get("attempts") or 1) if attempt else 0,
    }


def render_artifact(entry: Any, job_id: str) -> dict[str, Any] | None:
    """One entry of the coordinator's artifact listing as ``{name, bytes}``.

    The listing is ``[{uri, key, size_bytes}, …]``. The key is prefixed with
    ``jobs/{job_id}/`` by the coordinator, and the prefix is stripped because
    it is the same on every row and the judge is looking for a filename.

    ``None`` for an entry this function cannot read, and the caller drops it —
    the same "skip the entry, keep the rest" rule ``storage.sum_artifact_sizes``
    applies to the same data, and for the same reason: this comes from another
    service, and losing one row beats losing the section.
    """
    if not isinstance(entry, Mapping):
        return None
    key = entry.get("key") or entry.get("uri")
    if not isinstance(key, str) or not key:
        return None
    prefix = f"jobs/{job_id}/"
    name = key[len(prefix):] if key.startswith(prefix) else key
    size = entry.get("size_bytes")
    return {
        "name": name,
        "bytes": (
            int(size)
            if isinstance(size, int) and not isinstance(size, bool) and size >= 0
            else None
        ),
    }


def render_run(
    row: Mapping[str, Any],
    *,
    venue: str,
    tasks: Sequence[Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """One demo run, whole.

    ``state`` is the last state this control plane observed. The route
    refreshes it from the coordinator before calling this, which is not a
    nicety — see the module docstring on why a stale non-terminal FC row keeps
    the entire fleet invoking Function Compute.
    """
    return {
        "job_id": str(row.get("id")),
        "coordinator": venue,
        "state": row.get("status"),
        "created_at": _iso(row.get("created_at")),
        "finished_at": _iso(row.get("finished_at")),
        "elapsed_s": _elapsed_s(row.get("created_at"), row.get("finished_at")),
        "tasks": list(tasks),
        "artifacts": list(artifacts),
    }


# ===========================================================================
# THE GUEST HALF: a judge hosts their OWN machine
# ===========================================================================
#
# Everything above is about OUR four anchors. This half is about the judge's
# laptop: they install flashnode, run `flashnode login`, paste the printed
# user code into the page, and the page approves it for them — no signed-in
# human in the loop, because the whole premise is that they do not have an
# account.
#
# THE APPROVAL IS THE EXISTING ONE. `enrolment.approve_device_code` is called
# unchanged, with `demo_owner_id` in the seat a signed-in approver would
# occupy, so the machine row, the node_id uniqueness rule, the revoked/deleted
# re-enrolment branch and the one-shot token mint are all the code that
# already ships. No token is minted here; the agent still redeems its own
# device_code through `redeem_device_code` exactly as it does for a console
# approval. This module contributes a POOL and a SPEC, and nothing else.
#
# WHY A SEPARATE POOL. `DEMO_POOL_ID` is Test-1, which holds the four Alibaba
# anchors, and the headline sweep is scoped to it. If a judge's laptop landed
# there, the sweep — the measured, comparable, 9-task run this whole page
# exists to show — would start placing tasks on a stranger's machine that
# joined thirty seconds ago and may leave in ten. That corrupts the
# measurement AND the demo. So guests get `demo-guests`, the guest job is
# scoped to `demo-guests`, and the two never meet.


#: The pool a joined judge's machine is bound to. Looked up BY NAME under
#: `demo_owner_id`, not by a hardcoded uuid like `DEMO_POOL_ID`: that one is
#: a pool a human created in production and the id is the durable reference,
#: whereas this one is created on demand by `ensure_guest_pool` and would not
#: exist in a fresh deployment (or in a test database) for an id to name.
GUEST_POOL_NAME = "demo-guests"

#: The guest job. `main` is the `hello-world` branch of the same public
#: example repository: `image: python-slim`, `entrypoint: jobs/hello.py`, no
#: sweep, so it compiles to exactly ONE task that finishes in seconds.
#:
#: ONE TASK IS THE POINT, not a limitation to be fixed later. The moment
#: being staged is "the machine I just plugged in did work on this network",
#: and one task on one laptop is the shortest path from pressing a button to
#: seeing your own hostname's handle against a COMPLETED row. The nine-task
#: sweep is the other half of the page and already makes the parallelism
#: argument on hardware that is guaranteed to be there.
GUEST_REPO_OWNER = DEMO_REPO_OWNER
GUEST_REPO_NAME = DEMO_REPO_NAME
GUEST_REF = "main"

#: The marker in ``jobs.source`` for a guest run. DISTINCT from
#: :data:`DEMO_SOURCE_KEY` and deliberately not set alongside it: a guest run
#: is on the default venue, and if it also carried the demo marker it would
#: become "the latest run on `render`" and displace the sweep from the venue
#: comparison the page is built around.
GUEST_SOURCE_KEY = "demo_guest"

#: What the guest job declares it can run on. **THE ONE LINE THAT DECIDES
#: WHETHER THIS FEATURE WORKS AT ALL FOR MOST JUDGES.**
#:
#: `PlacementSpec.architectures` defaults to `["amd64"]` in the pinned
#: protocol (flashruntime 0.6.1, `protocol/v1alpha1.py:107`), this API never
#: set it, and `compile_to_jobspec` returns `model_dump_json()` — so every job
#: this control plane has ever compiled says `architectures: ["amd64"]` in so
#: many words. Most hackathon judges are on Apple Silicon.
#:
#: Nothing in flashruntime 0.6.1's scheduler READS the field (verified: the
#: eight placement gates key on sandbox/argv/module/local_inputs/gpus/
#: exclude_nodes/pool/datasets, and `architecture` appears in the package only
#: as two unread declarations), so today the pin excludes nobody. That is a
#: fact about one release, not a property of the system, and a demo whose
#: correctness rests on a field being ignored is a demo that breaks on the
#: release that stops ignoring it. So the guest job says what it means.
GUEST_ARCHITECTURES = ("amd64", "arm64")


def guest_source() -> dict[str, Any]:
    """The ``jobs.source`` descriptor for a guest run."""
    return {
        "type": "github",
        "owner": GUEST_REPO_OWNER,
        "repo": GUEST_REPO_NAME,
        "ref": GUEST_REF,
        GUEST_SOURCE_KEY: True,
    }


def is_guest_source(source: Any) -> bool:
    """Is this ``jobs.source`` a guest run's?"""
    return isinstance(source, Mapping) and source.get(GUEST_SOURCE_KEY) is True


# ---------------------------------------------------------------------------
# the code a judge types
# ---------------------------------------------------------------------------


def normalise_user_code(raw: Any) -> str:
    """The eight characters ``device_codes.user_code`` actually holds.

    A judge is reading a code off a terminal and typing it into a browser, so
    they will paste it with the dash the page prints, in whatever case they
    happen to be in, with a trailing space from the copy. None of that is a
    different code. ``USER_CODE_ALPHABET`` contains no dash, no space and no
    lowercase letter, so stripping exactly those three cannot collide two
    distinct codes into one.

    Returns ``""`` for anything that is not a string — the caller answers 400.
    """
    if not isinstance(raw, str):
        return ""
    return "".join(ch for ch in raw.upper() if ch.isalnum())


# ---------------------------------------------------------------------------
# the guest pool
# ---------------------------------------------------------------------------


def guest_pool_id(db: psycopg.Connection, owner_id: str) -> str | None:
    """The ``demo-guests`` pool's id, or ``None`` if nobody has joined yet.

    Read-only, so the public GET can render an empty guest fleet without the
    side effect of creating a pool for a page nobody has used. Scoped to
    ``owner_id`` as well as the name because ``pools.name`` is not unique —
    any user may name a pool anything — and the only ``demo-guests`` this
    module means is the one under the demo's own owner.
    """
    with db.cursor() as cur:
        try:
            cur.execute(
                "select id from public.pools"
                " where name = %s and owner_id = %s::uuid"
                " order by created_at limit 1",
                (GUEST_POOL_NAME, owner_id),
            )
        except psycopg.errors.InvalidTextRepresentation:
            return None
        row = cur.fetchone()
    return None if row is None else str(row["id"])


def ensure_guest_pool(db: psycopg.Connection, owner_id: str) -> str:
    """The ``demo-guests`` pool, created on first use.

    ``db.create_pool`` rather than a bare insert, because that function seats
    the owner in ``pool_members`` inside the same transaction — and that
    membership row is not bookkeeping here, it is load-bearing:
    ``pool_ids_for_machine`` intersects a machine's bindings with its OWNER's
    live memberships before the agent proxy stamps ``capabilities.pools``, so
    a pool whose owner is not a member would leave every joined machine
    advertising no pool at all and the seventh placement gate would refuse the
    guest job on every one of them. The machine's owner here IS the demo
    owner, so the two rows have to agree.

    Re-read after a failed create so two judges pasting codes at the same
    moment converge on one pool instead of one of them getting an exception.
    """
    existing = guest_pool_id(db, owner_id)
    if existing is not None:
        return existing
    try:
        row = dbmod.create_pool(db, name=GUEST_POOL_NAME, owner_id=owner_id)
    except psycopg.Error:
        raced = guest_pool_id(db, owner_id)
        if raced is None:
            raise
        return raced
    return str(row["id"])


def render_joined_machine(row: Mapping[str, Any]) -> dict[str, Any]:
    """What the join route hands back to the judge who just joined.

    ``name`` is the machine's REAL hostname and ``label`` is the ``prov…``
    handle every stranger sees. Both, because they answer different questions
    and only one of them is publishable:

    * the judge needs to recognise the machine they are standing in front of,
      and they proved possession of its one-shot device code to get this
      response — this is not disclosure, it is an echo;
    * the ``guests`` array on the public GET is read by everybody, so it
      carries the handle and only the handle (``render_fleet`` ->
      :func:`public_machine_name`). ``label`` is what lets the page find the
      judge's own row in that list without the list naming anyone.
    """
    name = row.get("name")
    return {
        "name": name if isinstance(name, str) and name.strip() else None,
        "node_id": str(row.get("node_id") or ""),
        "label": public_machine_name(row),
    }


# ---------------------------------------------------------------------------
# the guest run
# ---------------------------------------------------------------------------


def guest_run(db: psycopg.Connection) -> dict[str, Any] | None:
    """The most recent guest run, or ``None``.

    No per-venue split, unlike :func:`latest_run_per_venue`: the guest job
    always goes to the default coordinator. The venue comparison is the
    anchors' story, and asking a judge's single laptop to run the same task
    twice on two control planes would say nothing about either.
    """
    with db.cursor() as cur:
        cur.execute(
            f"""
            select {_RUN_COLUMNS}
              from public.jobs
             where source->>'{GUEST_SOURCE_KEY}' = 'true'
             order by created_at desc
             limit 1
            """
        )
        return cur.fetchone()


def guest_run_by_id(db: psycopg.Connection, job_id: str) -> dict[str, Any] | None:
    """One guest run, re-read. The guest twin of :func:`run_by_id`, and
    filtered on the guest marker for the same reason: neither may become a
    general "any job by id" reader on an unauthenticated route."""
    with db.cursor() as cur:
        cur.execute(
            f"""
            select {_RUN_COLUMNS}
              from public.jobs
             where id = %s and source->>'{GUEST_SOURCE_KEY}' = 'true'
            """,
            (job_id,),
        )
        return cur.fetchone()
