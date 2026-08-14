"""The provider network, as a viewer is allowed to see it.

Three reads and one write behind the network pages: a fleet-wide overview,
one provider's profile, and an owner declaring where their machine is.

WHAT THIS MODULE OWNS, AND WHAT IT DELIBERATELY DOES NOT
========================================================

**It owns no formula.** Every derived number here either comes from a module
that already owns it, or is a count. Three rules, each of which has been got
wrong somewhere before and is written down here so it is not got wrong again:

1. **ACCEPTANCE / SUCCESS RATE HAS EXACTLY ONE OWNER:
   ``metrics.acceptance_rates``.** It is keyed ``(machine_id,
   capability_class)``, it returns ``None`` below ``metrics.MIN_EVIDENCE`` (5)
   resolved attempts, and ``marketplace`` depends on that contract — ``None``
   means UNPROVEN and nothing may put a number in its place (see
   ``marketplace.Ask.acceptance_rate``). So this module CALLS it, over rows
   ``db.acceptance_rate_rows`` supplies, and never divides ``accepted`` by
   ``resolved`` itself. A second ratio computed here would disagree with the
   one the router prices on — the same host reading 0.95 on the network page
   and "unproven" in a plan — and the disagreement would be invisible until
   somebody asked why they were not getting work.

   The COUNTS below (``leases.total`` / ``resolved`` / ``active``) are queried
   straight from ``public.attempts``, and that is not an inconsistency: counts
   are facts about rows, rates are policy about evidence. Facts may be counted
   anywhere; policy has one home. Do not re-derive the rate from the counts
   sitting next to it, however tempting the arithmetic looks.

2. **NO SECOND GPU LADDER.** ``marketplace.capability_class`` and
   ``marketplace.machine_gpu_label`` already read ``machines.capabilities``,
   and ``router.estimator.hardware_class`` already labels hardware. This module
   adds no classifier of its own: ``specs.gpus`` is the agent's own reported
   ``gpus[]`` surfaced as-is (name + memory), and the one shared reading rule
   for "how much memory does this card have" is imported rather than copied.

3. **UNRESOLVED IS NEVER A FAILURE.** ``attempts.outcome IS NULL`` means in
   flight, or a row predating migration 0015 that nothing can classify. It is
   counted and reported (``leases.total`` minus ``leases.resolved``, and
   ``history.attempts.unresolved``) and it is excluded from every denominator.
   Read 0015's header: counting an unresolved attempt as a failure makes a
   busy machine's reliability fall while it works.

WHAT A STRANGER MAY SEE
=======================

A provider list is public to every signed-in viewer, and most of the rows in
it belong to somebody else. For a machine the viewer does not own, this module
never emits ``name``, never emits the full ``node_id``, and never emits
``owner_id`` in any form. What it emits instead is ``prov…`` plus the last six
characters of the node id — enough for a human to tell two rows apart and to
quote one in a support conversation, not enough to enrol as, look up, or
attribute to a person. ``own`` says which rows are the viewer's own, and the
owner-only block on the detail read is the only place identity appears at all.

There is exactly ONE exception, and it is a column rather than a convention:
``machines.official`` (migration 0030) marks PLATFORM-OPERATED ANCHOR CAPACITY
— a machine Zolli Labs runs itself — and such a machine shows its real ``name``
to everyone, owner and stranger alike, alongside ``official: true`` so the
console can render it as what it is. That is not a hole in the rule above, it
is the rule's own reasoning applied honestly: a name is withheld because it is
routinely a private person's name, and these machines have an accountable
operator who has published theirs. The exception buys nothing else — the full
``node_id`` and ``owner_id`` stay unemitted for an official machine exactly as
for any other, and an official machine with no name falls back to the ordinary
``prov…`` handle rather than leaking the node id in the name's place.

The anonymisation is applied where the row is built (``_provider``), not by a
caller remembering to strip fields afterwards. A field that has to be removed
downstream is a field that leaks the day somebody adds a second caller.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import psycopg

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import metrics as metricsmod

# The one reading rule for "how many MiB does this card report", imported
# rather than copied. It is private to `marketplace` because nothing outside
# the ladder had needed it until now; a second hand-rolled copy here would be
# the "agrees today, diverges silently" failure `test_import_boundary`'s own
# docstring argues against for the cross-repo case.
from flashml_cloud_api.marketplace import _gpu_memory_mb

#: The window an uptime percentage is quoted over: seven days of hour buckets.
#:
#: The DENOMINATOR IS THIS CONSTANT, not the number of buckets found. Nothing
#: writes ``beats = 0`` (migration 0029), so rows exist only for hours the
#: machine was up — dividing by the rows found would report 100% for every
#: machine that has ever heartbeated once, including one that beat twice last
#: Tuesday and has been dark since.
UPTIME_WINDOW_HOURS = 168

#: How many hour buckets the detail view's uptime strip covers.
UPTIME_DETAIL_HOURS = 24

#: How many days the detail view's lease series covers.
LEASE_SERIES_DAYS = 30

#: ISO-3166 alpha-2: two letters, uppercase. FORMAT only, deliberately — a
#: hardcoded member list is a thing that goes stale and starts refusing real
#: places (and this API has no business adjudicating which territories exist).
_ALPHA2 = re.compile(r"^[A-Z]{2}$")


class InvalidLocation(ValueError):
    """A declared location that is not one. Distinct from "not your machine":
    the route answers 400 for this and 404 for that, and folding the two would
    tell a caller their own input was fine when it was not."""


class InvalidOfficial(ValueError):
    """An ``official`` that is not a boolean. Same 400-versus-404 split as
    ``InvalidLocation``, for the same reason: a body this API cannot read is
    the caller's problem to fix, and answering 404 to it would send them
    hunting for a machine that is sitting right there."""


# ---------------------------------------------------------------------------
# small shared shapes
# ---------------------------------------------------------------------------


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _label(row: Mapping[str, Any], *, own: bool) -> str:
    """What this machine is called, to this viewer.

    The owner sees what they named it (or its node id, when they never named
    it). Everyone else sees ``prov…`` and six characters — a stable handle,
    not an identity. Six because it is enough to distinguish the rows a person
    is actually looking at while being far too little to enumerate a fleet or
    to recognise a machine somebody mentioned elsewhere.

    **``official`` is the one exception** (migration 0030): a platform-operated
    anchor machine shows its real name to EVERYONE, because the whole point of
    the flag is that this capacity has a named, accountable operator. Two
    things it deliberately does not do:

    * It does not unlock the node id. An official machine that was never named
      falls back to the ordinary ``prov…`` handle for a stranger — publishing
      the enrolment handle in a missing name's place would be a leak the flag
      was never asked for, and it would be the strangers-only branch that
      leaked, so nobody looking at their own console would ever see it.
    * It does not change what the OWNER sees. An owner already sees their own
      name, and the ``official`` branch running first for them would only make
      the two paths diverge on the blank-name case for no reason.
    """
    node_id = str(row.get("node_id") or "")
    name = row.get("name")
    # Blank-or-absent is one state ("never named"), and it is tested once
    # here so the owner branch and the official branch cannot drift on it.
    named = isinstance(name, str) and bool(name.strip())
    if own:
        return name if named else (node_id or "unnamed machine")
    if named and row.get("official"):
        return name
    return f"prov…{node_id[-6:]}" if node_id else "prov…"


def _badge(row: Mapping[str, Any]) -> str:
    """The practical trust tier, from the three capability booleans.

    A port of ``apps/web/lib/machine-badge.ts``, precedence included: an agent
    claiming both a sandbox and unsandboxed argv reads as ``sandboxed``,
    because a Docker-capable host never has to fall back to running work
    directly on the host and the safer path must win wherever it is available.

    Derived from the BOOLEAN columns, never from ``capabilities`` — that
    snapshot is the agent's free-form self-description and is display-only, as
    the TypeScript original says at length.

    The three values are exactly the TypeScript ``MachineBadge`` union, string
    for string, so the console can index ``MACHINE_BADGE_STYLES`` /
    ``MACHINE_BADGE_LABELS`` with what this returns instead of keeping a
    translation table that has to be updated in two repos at once.
    """
    if row.get("sandbox_capable") or row.get("argv_capable"):
        return "sandboxed"
    if row.get("unsandboxed_argv_capable"):
        return "trusted"
    return "modules-only"


def _location(row: Mapping[str, Any]) -> dict[str, Any]:
    """Where this machine is, and ON WHOSE AUTHORITY.

    ``source`` is passed through verbatim and is the whole point of the shape:
    it is one of ``declared`` (the owner typed it), ``venue`` (copied from the
    record of a machine this control plane rented), ``detected`` (inferred from
    the machine's public egress address by ``geoip``, migration 0031) or
    ``None``. In DESCENDING order of authority, and the ordering is enforced by
    the writers — ``geoip.sweep`` fills a NULL source and never overwrites one
    of the first two.

    **Nothing here may collapse those into a boolean, and no consumer may treat
    a missing ``source`` as equivalent to any of them.** Migration 0029 shipped
    with only two legal values and its own header said a third would never
    exist; 0031 added one, and the code that survived the change unmodified is
    exactly the code that read ``geo_source`` as an opaque label instead of
    branching on it.

    ``city`` is present for the same pass-through reason and is ALWAYS ``None``
    on a detected row — the detection path is forbidden from writing it (0031:
    a country is a fact about a network, a city is a fact about a person). A
    non-null city therefore means a human typed it, which is a distinction a
    renderer may rely on.
    """
    lat = row.get("geo_lat")
    lon = row.get("geo_lon")
    return {
        "country": row.get("geo_country"),
        "region": row.get("geo_region"),
        "city": row.get("geo_city"),
        "lat": float(lat) if lat is not None else None,
        "lon": float(lon) if lon is not None else None,
        "source": row.get("geo_source"),
    }


def _specs(capabilities: Any) -> dict[str, Any]:
    """The hardware the agent reported, surfaced and not interpreted.

    ``gpus`` is the agent's own ``gpus[]`` — one entry per device, in the
    order the driver listed them — and NOT a class, a tier or a label. Those
    exist already (``marketplace.capability_class``,
    ``marketplace.machine_gpu_label``, ``router.estimator.hardware_class``) and
    a fourth interpretation of the same jsonb invented here is exactly how a
    machine ends up described as ``gpu-24gb`` on one page and ``gpu-16gb`` on
    the next.

    ``memory_total_mb`` is ``None`` for a card whose driver did not report it.
    Not ``0``: this codebase draws that distinction everywhere — 0 GB is a
    claim about the hardware, ``None`` is the honest "the probe could not
    read it", and it is precisely the case ``capability_class`` refuses to
    classify rather than under-sell.

    ``os`` / ``architecture`` fold the empty string to ``None`` for the same
    reason. ``NodeCapabilities`` defaults them to ``""``, which is the absence
    of a reading rather than an operating system called "".
    """
    caps: Mapping[str, Any] = capabilities if isinstance(capabilities, Mapping) else {}

    devices = caps.get("gpus")
    gpus: list[dict[str, Any]] = []
    if isinstance(devices, list):
        for device in devices:
            if not isinstance(device, Mapping):
                continue
            name = device.get("name")
            gpus.append(
                {
                    "name": name if isinstance(name, str) and name else "GPU",
                    "memory_total_mb": _gpu_memory_mb(device),
                }
            )

    cores = caps.get("cpu_cores")
    memory = caps.get("memory_bytes")
    os_name = caps.get("os")
    arch = caps.get("architecture")

    return {
        "cpu_cores": (
            float(cores)
            if isinstance(cores, (int, float)) and not isinstance(cores, bool)
            else None
        ),
        "memory_bytes": (
            int(memory)
            if isinstance(memory, (int, float)) and not isinstance(memory, bool)
            else None
        ),
        "gpus": gpus,
        "os": os_name if isinstance(os_name, str) and os_name else None,
        "architecture": arch if isinstance(arch, str) and arch else None,
    }


def _uptime_pct(hours_up: int, hours_observed: int) -> float | None:
    """Percentage of the last ``UPTIME_WINDOW_HOURS`` this machine was up.

    ``None`` — never ``0.0`` — when there is no bucket at all. The same
    distinction ``metrics.goodput_ratio`` and ``metrics.acceptance_rates``
    draw, for the same reason: ``0.0`` is the worst reading this page can show
    ("it was never up"), and a machine that enrolled ten minutes ago has not
    earned it. Once a single bucket exists, the number is real and low numbers
    are reported as they are.

    A PERCENTAGE, 0..100, as the name says. The raw evidence rides alongside in
    the response (``uptime.hours_up_7d`` / ``hours_observed_7d``) precisely so
    that a consumer wanting a different derivation — the routing layer scores
    its own — computes it from the buckets rather than from this.
    """
    if hours_observed <= 0:
        return None
    return round(100.0 * hours_up / UPTIME_WINDOW_HOURS, 1)


def _display_acceptance_rate(records: Sequence[Mapping[str, Any]]) -> float | None:
    """One machine's several per-class rates, flattened for display only.

    ``metrics.acceptance_rates`` refuses to produce an all-class rollup and
    says why: a host that accepts 95% of CPU work and 40% of GPU work is not a
    0.9 host, and the aggregate is wrong at the only moment anyone consults it
    — when deciding whether to hand it the GPU task. It also says what a caller
    that needs one must do: "write the flattening down where it can be
    reviewed". This is that place, and it is used for a display number on a
    profile page and for nothing that places work.

    **Nothing that schedules, prices or matches may call this.** Placement
    reads the per-class row (``marketplace.Ask.acceptance_rate``,
    ``router.estimator``), which is the whole point of the key.

    Weighted by ``resolved`` so a class with 200 resolved attempts is not
    averaged flat against one with 6. Classes still below
    ``metrics.MIN_EVIDENCE`` arrive as ``None`` and are DROPPED rather than
    counted as zero — a rate the owning module refused to state must not be
    smuggled back in here as a number. When every class is ``None`` the answer
    is ``None``: unproven, not zero.
    """
    weighted = 0.0
    weight = 0
    for record in records:
        rate = record.get("acceptance_rate")
        if rate is None:
            continue
        resolved = int(record.get("resolved") or 0)
        if resolved <= 0:
            continue
        weighted += float(rate) * resolved
        weight += resolved
    if weight == 0:
        return None
    return round(weighted / weight, 4)


# ---------------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------------

#: Everything a provider row needs, and deliberately not ``token_hash`` or
#: anything else in ``public.machines`` — the same spelled-out discipline
#: ``db.MACHINE_PUBLIC_COLUMNS`` exists for. ``owner_id`` IS selected because
#: ``own`` cannot be computed without it, and it is consumed here and never
#: emitted.
_PROVIDER_COLUMNS = """
    m.id, m.node_id, m.name, m.owner_id, m.capabilities, m.last_seen_at,
    m.geo_country, m.geo_region, m.geo_city, m.geo_lat, m.geo_lon, m.geo_source,
    m.sandbox_capable, m.argv_capable, m.unsandboxed_argv_capable,
    m.official
"""


def _lease_counts(
    db: psycopg.Connection, machine_ids: Sequence[str]
) -> dict[str, dict[str, int]]:
    """Per-machine attempt counts. Counts only — see rule 1 in the module
    docstring for why no ratio is computed from them here.

    ``active`` is an attempt with no terminal outcome AND a lease deadline
    still in the future. Both halves are needed: an unresolved attempt whose
    deadline passed is not work in flight, it is work the coordinator has
    already stopped accepting (0015), and counting it would show a dead
    machine holding leases for ever. An attempt with no deadline on record is
    never counted active for the same reason ``reconcile_expired_attempts``
    never resolves it — there is no instant to compare against.
    """
    if not machine_ids:
        return {}
    ids = [str(m) for m in machine_ids]
    with db.cursor() as cur:
        cur.execute(
            """
            select machine_id,
                   count(*) as total,
                   count(*) filter (where outcome is not null) as resolved,
                   count(*) filter (where outcome = 'accepted') as accepted,
                   count(*) filter (
                       where outcome is null and lease_deadline > now()
                   ) as active
              from public.attempts
             where machine_id = any(%s::uuid[])
             group by machine_id
            """,
            (ids,),
        )
        rows = cur.fetchall()
    return {
        str(row["machine_id"]): {
            "total": int(row["total"]),
            "resolved": int(row["resolved"]),
            "accepted": int(row["accepted"]),
            "active": int(row["active"]),
        }
        for row in rows
    }


def _uptime_counts(
    db: psycopg.Connection, machine_ids: Sequence[str]
) -> dict[str, dict[str, int]]:
    """Hour buckets in the last ``UPTIME_WINDOW_HOURS`` per machine.

    ``observed`` counts the buckets that exist; ``up`` counts those with a
    beat. They are equal today — nothing writes ``beats = 0`` — and both are
    reported anyway, because the routing layer consumes raw evidence and
    because the day something DOES write a zero bucket (a machine observed
    absent, rather than merely unrecorded) the two stop being equal and every
    reader that assumed they were the same would be silently wrong.

    The window is expressed in buckets, not in "now minus seven days": both
    ends are truncated to the hour so the count is a whole number of buckets
    and does not wobble by one depending on what minute the page was loaded.
    """
    if not machine_ids:
        return {}
    ids = [str(m) for m in machine_ids]
    with db.cursor() as cur:
        cur.execute(
            f"""
            select machine_id,
                   count(*) as observed,
                   count(*) filter (where beats > 0) as up
              from public.machine_uptime_hours
             where machine_id = any(%s::uuid[])
               and hour_ts > date_trunc('hour', now())
                             - interval '{UPTIME_WINDOW_HOURS} hours'
             group by machine_id
            """,
            (ids,),
        )
        rows = cur.fetchall()
    return {
        str(row["machine_id"]): {
            "observed": int(row["observed"]),
            "up": int(row["up"]),
        }
        for row in rows
    }


def _rates_by_machine(
    db: psycopg.Connection, machine_ids: Sequence[str]
) -> dict[str, list[dict[str, Any]]]:
    """``metrics.acceptance_rates`` output, grouped by machine.

    One call for the whole page rather than one per provider: the producer
    (``db.acceptance_rate_rows``) already takes a machine list and scopes the
    read to it.
    """
    if not machine_ids:
        return {}
    records = metricsmod.acceptance_rates(
        dbmod.acceptance_rate_rows(db, machine_ids=[str(m) for m in machine_ids])
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record["machine_id"]), []).append(record)
    return grouped


def _provider(
    row: Mapping[str, Any],
    *,
    viewer_user_id: str | None,
    leases: Mapping[str, int] | None,
    uptime: Mapping[str, int] | None,
    rates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """One provider, as this viewer may see it. The single place a row becomes
    a response — see the module docstring on why anonymisation lives here and
    not in a caller."""
    own = viewer_user_id is not None and str(row["owner_id"]) == str(viewer_user_id)
    counts = dict(leases or {})
    total = int(counts.get("total", 0))
    resolved = int(counts.get("resolved", 0))
    active = int(counts.get("active", 0))

    hours_up = int((uptime or {}).get("up", 0))
    hours_observed = int((uptime or {}).get("observed", 0))

    return {
        "id": str(row["id"]),
        "label": _label(row, own=own),
        "own": own,
        # Emitted for every provider, not only the official ones: a key that
        # appears only when true is a key the console has to remember is
        # optional, and `official === true` is a cheaper contract than
        # `official ?? false`. See `_label` for what the flag actually does.
        "official": bool(row.get("official")),
        "online": bool(row.get("online")),
        "last_seen_at": _iso(row.get("last_seen_at")),
        "location": _location(row),
        "uptime_7d_pct": _uptime_pct(hours_up, hours_observed),
        "uptime": {
            "hours_up_7d": hours_up,
            "hours_observed_7d": hours_observed,
        },
        "leases": {
            "active": active,
            "total": total,
            "resolved": resolved,
            # NOT accepted/resolved computed here. See rule 1.
            "success_rate": _display_acceptance_rate(rates),
        },
        "specs": _specs(row.get("capabilities")),
        "badge": _badge(row),
    }


# ---------------------------------------------------------------------------
# the reads
# ---------------------------------------------------------------------------


def providers_overview(
    db: psycopg.Connection, viewer_user_id: str | None
) -> dict[str, Any]:
    """The whole provider network, plus what it adds up to.

    **``status = 'active'`` only.** A ``pending`` machine has never redeemed a
    token and may never; a ``revoked`` one holds a dead credential; a
    ``deleted`` one is a tombstone whose device columns were scrubbed on
    purpose (0028) and has nothing to show. None of the three is capacity, and
    counting any of them would inflate the one number on this page a buyer
    might act on.

    **``used`` is COMMITTED capacity, not utilisation.** It sums the specs of
    machines currently holding at least one active lease — the whole machine,
    not the share of it the task actually uses. That is deliberate and it is
    what the label has to say: this API does not measure a machine's CPU
    utilisation and has no way to, so a 12-core host running one task counts
    12 cores committed. Anything finer would be invented.

    A machine whose capabilities never reported a figure contributes nothing to
    that figure's total, in either column. It is not counted as zero — the
    total is "what the fleet has told us it has", and a machine that reported
    nothing has told us nothing.
    """
    online_predicate = dbmod.MACHINE_ONLINE_PREDICATE
    with db.cursor() as cur:
        cur.execute(
            f"""
            select {_PROVIDER_COLUMNS},
                   ({online_predicate}) as online
              from public.machines m
             where m.status = 'active'
             order by ({online_predicate}) desc,
                      m.last_seen_at desc nulls last,
                      m.id
            """
        )
        rows = list(cur.fetchall())

    machine_ids = [str(row["id"]) for row in rows]
    leases = _lease_counts(db, machine_ids)
    uptime = _uptime_counts(db, machine_ids)
    rates = _rates_by_machine(db, machine_ids)

    providers = [
        _provider(
            row,
            viewer_user_id=viewer_user_id,
            leases=leases.get(str(row["id"])),
            uptime=uptime.get(str(row["id"])),
            rates=rates.get(str(row["id"]), []),
        )
        for row in rows
    ]

    totals = {
        "cpu_cores": {"total": 0.0, "used": 0.0},
        "gpus": {"total": 0, "used": 0},
        "memory_bytes": {"total": 0, "used": 0},
        "providers_online": 0,
        "providers_total": len(providers),
    }
    for provider in providers:
        specs = provider["specs"]
        committed = provider["leases"]["active"] > 0
        if provider["online"]:
            totals["providers_online"] += 1
        cores = specs["cpu_cores"]
        if cores is not None:
            totals["cpu_cores"]["total"] += cores
            if committed:
                totals["cpu_cores"]["used"] += cores
        memory = specs["memory_bytes"]
        if memory is not None:
            totals["memory_bytes"]["total"] += memory
            if committed:
                totals["memory_bytes"]["used"] += memory
        gpu_count = len(specs["gpus"])
        totals["gpus"]["total"] += gpu_count
        if committed:
            totals["gpus"]["used"] += gpu_count

    totals["cpu_cores"]["total"] = round(totals["cpu_cores"]["total"], 2)
    totals["cpu_cores"]["used"] = round(totals["cpu_cores"]["used"], 2)

    return {"totals": totals, "providers": providers}


def provider_detail(
    db: psycopg.Connection, machine_id: str, viewer_user_id: str | None
) -> dict[str, Any] | None:
    """One provider's profile, with its history — or ``None``.

    ``None`` is the caller's 404 and covers three cases at once: not a uuid,
    no such machine, and a ``deleted`` tombstone. Folding them is the same
    doctrine every machine and pool lookup in ``app.py`` follows — a response
    that distinguishes "no such id" from "that id exists but is gone" tells a
    prober which ids are real.

    **A stranger may read this**, which is the point: a network of providers
    that only its own members can inspect is not a network anybody can choose
    from. What a stranger gets is the anonymised row — no name, no full node
    id, no owner — plus the history, which is about the machine's conduct
    rather than about its owner. The ``owner`` block is the only owner-only
    part, and it is absent (not nulled) for everyone else.
    """
    try:
        with db.cursor() as cur:
            cur.execute(
                f"""
                select {_PROVIDER_COLUMNS}, m.status,
                       ({dbmod.MACHINE_ONLINE_PREDICATE}) as online
                  from public.machines m
                 where m.id = %s::uuid
                """,
                (machine_id,),
            )
            row = cur.fetchone()
    except psycopg.errors.InvalidTextRepresentation:
        return None
    if row is None or row["status"] == "deleted":
        return None

    mid = str(row["id"])
    provider = _provider(
        row,
        viewer_user_id=viewer_user_id,
        leases=_lease_counts(db, [mid]).get(mid),
        uptime=_uptime_counts(db, [mid]).get(mid),
        rates=_rates_by_machine(db, [mid]).get(mid, []),
    )

    provider["series"] = {
        "leases_daily_30d": _leases_daily(db, mid),
        "uptime_24h": _uptime_hourly(db, mid),
    }
    provider["history"] = _history(db, mid)

    if provider["own"]:
        provider["owner"] = {
            "name": row.get("name"),
            "node_id": str(row["node_id"]),
            "credits_earned": _credits_earned(db, mid),
        }
    return provider


def _leases_daily(db: psycopg.Connection, machine_id: str) -> list[dict[str, Any]]:
    """Resolved and accepted attempts per day, for the last
    ``LEASE_SERIES_DAYS`` days.

    Every day in the window is present, including the ones with nothing in
    them. A sparse series would render as a chart whose x-axis silently
    compresses idle weeks, so a machine that worked on two days last month
    would look like a machine that worked constantly. ``0`` here is a fact
    about a day that happened, not the "no evidence" zero this codebase refuses
    elsewhere.

    Bucketed on ``resolved_at``, which for an expired attempt is the lease
    DEADLINE — the instant the work stopped counting — and never the moment a
    reconciler noticed. 0015's own reasoning; using the reconciler's clock
    would pile a month of expiries onto whatever day somebody first opened this
    page.
    """
    with db.cursor() as cur:
        cur.execute(
            f"""
            with days as (
                select generate_series(
                    date_trunc('day', now())
                        - interval '{LEASE_SERIES_DAYS - 1} days',
                    date_trunc('day', now()),
                    interval '1 day'
                ) as day
            )
            select to_char(d.day, 'YYYY-MM-DD') as date,
                   count(a.lease_id) as resolved,
                   count(a.lease_id) filter (where a.outcome = 'accepted')
                       as accepted
              from days d
              left join public.attempts a
                on a.machine_id = %s::uuid
               and a.outcome is not null
               and a.resolved_at >= d.day
               and a.resolved_at < d.day + interval '1 day'
             group by d.day
             order by d.day
            """,
            (machine_id,),
        )
        return [
            {
                "date": row["date"],
                "resolved": int(row["resolved"]),
                "accepted": int(row["accepted"]),
            }
            for row in cur.fetchall()
        ]


def _uptime_hourly(db: psycopg.Connection, machine_id: str) -> list[dict[str, Any]]:
    """The last ``UPTIME_DETAIL_HOURS`` hour buckets, dense.

    Generated from the clock and left-joined to the ledger rather than read
    out of it, so an hour with no row appears as ``up: false`` in its correct
    position instead of vanishing and letting the strip close up — which would
    draw a machine that was down all night as one that was up all night.
    """
    with db.cursor() as cur:
        cur.execute(
            f"""
            with hours as (
                select generate_series(
                    date_trunc('hour', now())
                        - interval '{UPTIME_DETAIL_HOURS - 1} hours',
                    date_trunc('hour', now()),
                    interval '1 hour'
                ) as hour_ts
            )
            select h.hour_ts, coalesce(u.beats, 0) > 0 as up
              from hours h
              left join public.machine_uptime_hours u
                on u.machine_id = %s::uuid and u.hour_ts = h.hour_ts
             order by h.hour_ts
            """,
            (machine_id,),
        )
        return [
            {"hour_ts": _iso(row["hour_ts"]), "up": bool(row["up"])}
            for row in cur.fetchall()
        ]


def _history(db: psycopg.Connection, machine_id: str) -> dict[str, Any]:
    """Lifetime attempt outcomes, and the time the accepted ones took.

    The four buckets are 0015's vocabulary minus ``abandoned``, which is
    reserved and deliberately unwritten — nothing can produce one today, so
    nothing is being dropped. If it ever starts being written it needs a key
    here, and this comment is the reminder.

    ``accepted_duration_s_total`` is lease-held wall clock (``resolved_at -
    claimed_at``) over ACCEPTED attempts only — the same expression
    ``db.acceptance_rate_rows`` uses, and for its reason: on a failed or
    expired attempt that interval is time WASTED, not time a task takes, and
    adding the two together produces a number that means neither.

    It is a FLOOR, not a total: ``fedavg.on_round`` credits federated
    contributors straight into ``contributions`` and writes no ``attempts``
    row at all, so federated work is absent here by construction (the same gap
    ``acceptance_rate_rows`` documents — production once read 26 credits
    against 16 attempts on one machine, and the ten-row difference was
    federated).
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select count(*) filter (where outcome = 'accepted') as accepted,
                   count(*) filter (where outcome = 'failed') as failed,
                   count(*) filter (where outcome = 'expired') as expired,
                   count(*) filter (where outcome is null) as unresolved,
                   coalesce(
                       sum(extract(epoch from (resolved_at - claimed_at)))
                           filter (where outcome = 'accepted'),
                       0
                   ) as accepted_duration_s_total
              from public.attempts
             where machine_id = %s::uuid
            """,
            (machine_id,),
        )
        row = cur.fetchone() or {}
    return {
        "attempts": {
            "accepted": int(row.get("accepted") or 0),
            "failed": int(row.get("failed") or 0),
            "expired": int(row.get("expired") or 0),
            "unresolved": int(row.get("unresolved") or 0),
        },
        "accepted_duration_s_total": round(
            float(row.get("accepted_duration_s_total") or 0.0), 3
        ),
    }


def _credits_earned(db: psycopg.Connection, machine_id: str) -> float | None:
    """What this machine has earned, in MILLICREDITS, or ``None``.

    **Per-machine attribution is possible, and this is the join that makes
    it.** ``credit_accounts`` is per PERSON, not per machine, so the ledger
    alone cannot answer "what did this machine earn" — but every earning leg
    carries ``(ref_type, ref_id) = ('attempt', lease_id)`` (migration 0018,
    written by ``marketplace.settle_accepted_work``), and ``public.attempts``
    maps a lease to the machine that claimed it. Lease → attempt → machine is
    therefore exact, not an apportionment: nothing is divided, estimated or
    spread across a fleet.

    ``reason = 'earned_accepted_work'`` and nothing else. It is the only leg
    that lands on a HOST's account, so the buyer's ``spent_accepted_work``,
    the holds, the releases and the refunds — which share the same ref — are
    all excluded by naming it.

    **Not filtered by whose account received it**, deliberately. ``matches``
    copies ``host_id`` at creation "so the ledger still reads correctly if
    somebody transferred a machine later" (0018), so filtering on the CURRENT
    owner would silently drop earnings a previous owner banked. The question
    asked here is what the MACHINE earned; the answer does not change when the
    machine changes hands, and this block is owner-only anyway.

    ``None``, not ``0``, when there is no earning leg at all: this deployment
    has no marketplace rows until somebody lists a machine, and "has never been
    paid for anything" is a different statement from "was paid zero" (which is
    a real outcome — a donated listing at 0 ZC/hour settles for nothing).

    UNIT: millicredits, the same unit every ``*_zc`` field in this API carries
    and the unit ``apps/web/lib/market-credits.ts::formatZc`` expects. Returned
    as a float only because the response contract says float; the value is
    always integral.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select count(*) as legs, coalesce(sum(e.delta_zc), 0) as earned_zc
              from public.credit_entries e
              join public.attempts a on a.lease_id = e.ref_id
             where e.reason = 'earned_accepted_work'
               and e.ref_type = 'attempt'
               and a.machine_id = %s::uuid
            """,
            (machine_id,),
        )
        row = cur.fetchone() or {}
    if int(row.get("legs") or 0) == 0:
        return None
    return float(row.get("earned_zc") or 0)


# ---------------------------------------------------------------------------
# the write
# ---------------------------------------------------------------------------


def _clean_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidLocation(f"{field} must be a string or null")
    trimmed = value.strip()
    # An empty string is how a form sends a cleared field; it means "no
    # value", and storing "" would make every reader downstream check for two
    # different kinds of nothing.
    return trimmed or None


def _coordinate(value: Any, field: str, limit: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidLocation(f"{field} must be a number or null")
    number = float(value)
    if not -limit <= number <= limit:
        raise InvalidLocation(f"{field} must be between {-limit} and {limit}")
    return number


def set_machine_location(
    db: psycopg.Connection,
    machine_id: str,
    owner_id: str,
    *,
    country: Any = None,
    region: Any = None,
    city: Any = None,
    lat: Any = None,
    lon: Any = None,
) -> bool:
    """Declare where one of your own ACTIVE machines is. Returns whether it
    wrote.

    ``False`` means the machine is not this caller's, does not exist, or is
    not ``active`` — one answer for all three, so the route's 404 cannot be
    used to discover which machine ids exist or which of them are somebody
    else's. Bad INPUT is not folded in with those: it raises
    ``InvalidLocation`` and the route answers 400, because "you typed a
    latitude of 500" and "that machine is not yours" are different problems and
    only one of them is the caller's to fix.

    **The ownership test is in the WHERE clause**, not an ``if`` above it. A
    check-then-write is a race and, worse, it is a check somebody can forget to
    write; a predicate in the statement cannot be omitted without the statement
    obviously changing.

    ``geo_source`` is set to ``'declared'`` here and is never a parameter. This
    function is the declared path by definition; the other legal values belong
    to their own writers — ``'venue'`` to whatever copies a rented machine's
    data centre from the venue record, and ``'detected'`` to ``geoip.sweep``
    (migration 0031) — and letting a caller name its own source would make the
    column's whole purpose a matter of trust in the caller rather than a
    property of the writer.

    **This write is also what makes detection back off for ever.**
    ``geoip.sweep`` selects on ``geo_source is null``, so the moment a host
    declares anything, that machine leaves the sweep's candidate set
    permanently — a declaration is never overwritten by a later guess, and no
    "prefer the human answer" logic is needed anywhere downstream because the
    weaker writer simply never runs on that row.

    **Both coordinates or neither.** Half a pair pins nothing and would render
    as a marker on a meridian; refusing is the honest answer and the fix is
    obvious to whoever sent it.

    Every field is overwritten, including with ``None``. Sending a location
    with no city clears the city — this is a declaration of where the machine
    is, entire, not a patch of individual fields, and a partial write would
    make it impossible to ever remove a value.
    """
    country_value = _clean_text(country, "country")
    if country_value is not None:
        country_value = country_value.upper()
        if not _ALPHA2.match(country_value):
            raise InvalidLocation(
                "country must be an ISO-3166 alpha-2 code, e.g. 'DE'"
            )

    latitude = _coordinate(lat, "lat", 90.0)
    longitude = _coordinate(lon, "lon", 180.0)
    if (latitude is None) != (longitude is None):
        raise InvalidLocation("lat and lon must be given together or not at all")

    try:
        with db.cursor() as cur:
            cur.execute(
                """
                update public.machines
                   set geo_country = %s,
                       geo_region = %s,
                       geo_city = %s,
                       geo_lat = %s,
                       geo_lon = %s,
                       geo_source = 'declared'
                 where id = %s::uuid
                   and owner_id = %s::uuid
                   and status = 'active'
                """,
                (
                    country_value,
                    _clean_text(region, "region"),
                    _clean_text(city, "city"),
                    latitude,
                    longitude,
                    machine_id,
                    owner_id,
                ),
            )
            return cur.rowcount > 0
    except psycopg.errors.InvalidTextRepresentation:
        # Not a uuid: the same "no such machine of yours" answer, for the same
        # reason `app.py` folds this case everywhere else.
        return False


def set_machine_official(
    db: psycopg.Connection,
    machine_id: str,
    owner_id: str,
    official: bool,
) -> bool:
    """Mark one of your own ACTIVE machines as platform-operated anchor
    capacity, or unmark it. Returns whether it wrote.

    **This PUBLISHES the machine's name.** ``official = true`` is the single
    documented exception to the anonymisation contract at the top of this
    module — see ``_label`` — so it is a disclosure, per machine, made
    deliberately. Migration 0030's header carries the argument for why it is a
    column rather than "whichever machines the Zolli Labs account happens to
    own".

    Everything about the authorisation is copied from
    ``set_machine_location``, deliberately and not by accident:

    * ``False`` means the machine is not this caller's, does not exist, or is
      not ``active`` — one answer for all three, so the route's 404 cannot be
      used to discover which machine ids exist or which are somebody else's.
    * **The ownership test is in the WHERE clause**, not an ``if`` above it. A
      check-then-write is a race and a thing somebody can forget to write.
    * A malformed uuid is that same ``False``, not a crash.

    Bad INPUT stays separate: a non-boolean raises ``InvalidOfficial`` and the
    route answers 400. ``"true"`` the string and ``1`` the integer are refused
    rather than coerced — this flag decides whether a name is public, and a
    truthiness rule is how ``official: "false"`` ends up publishing one.
    """
    if not isinstance(official, bool):
        raise InvalidOfficial("official must be true or false")

    try:
        with db.cursor() as cur:
            cur.execute(
                """
                update public.machines
                   set official = %s
                 where id = %s::uuid
                   and owner_id = %s::uuid
                   and status = 'active'
                """,
                (official, machine_id, owner_id),
            )
            return cur.rowcount > 0
    except psycopg.errors.InvalidTextRepresentation:
        return False
