"""The public, unauthenticated run page for ONE job — its data access and its
authorship rules, in one file so both can be reviewed together.

**Why this exists.** The competition submission needs a live URL that opens
without a login (gate G-1), and every console route redirects to sign-in.
Migration 0014 already solved that for a *sandbox session* and the mechanism
has shipped: `shr_` + 256 bits from `secrets`, the column narrowed in SQL by
``sandbox_sessions.SESSION_SHARE_COLUMNS``, one `middleware.ts` rule anchored
at both ends. AS-7 confirmed the gap is narrower than "build a public
surface": the token is scoped to a session, so the FC hibernation story is
publicly viewable and the FAULT-TOLERANCE story — a machine dying and the work
resuming elsewhere, which is the product's actual claim — is not. This module
extends the same mechanism to a job and invents nothing.

---------------------------------------------------------------------------
THE ONE RULE THIS FILE ENFORCES (AS-16)
---------------------------------------------------------------------------

    A session's output is ours; a job's output is theirs.

We execute user-supplied code from a submitted repository. That code prints
whatever it prints, writes whatever filenames it likes, and was configured by
somebody who never agreed to publish any of it. ``SESSION_SHARE_COLUMNS`` was
chosen against an object with a handful of fields, all of them written by this
codebase; a job reaches repo URL and ref, dataset paths, artifact keys,
machine ids, owner id, task parameters and task stderr. Widening the session's
column list to cover a job would have been the whole bug.

So the line is NOT failure-versus-success — failure IS the demo, and excluding
"failure detail" wholesale would delete the story the page exists to tell. The
line is **authorship**, and AS-16.1 gives the test, applied per field and not
per key:

    Can you name the line in OUR code that assigned this value, with no user
    input reaching it?

Type is not evidence. A number is not safer than a string, only harder to
notice. Two worked examples of the trap, both found by review rather than by
reasoning:

* ``machines.name`` looks like a label we chose. ``enrolment.py`` sets
  ``name=row["hostname"]``, and that hostname arrives in the enrolment request
  body (`app.py`'s device-code handler, ``_opt_str(payload.get("hostname"))``);
  ``contributions.py`` says it outright — *"an agent supplies its hostname at
  enrolment"*. Volunteer hostnames are routinely personal. Publishing one puts
  somebody's name on a page anyone with a link can read. WITHHELD, and
  ``test_public_job_share`` asserts the exact string is absent from the
  serialized response rather than merely absent under one key.
* ``"resumed at step 16298"`` looks like our integer. ``preflight.py:125``
  and ``:149`` record that the relay globs ``step-*.json`` under
  ``workdir/out/ckpt`` — the number is parsed from a FILENAME THE TASK'S CODE
  WRITES. Its key is ours; its value is the submitter's. No step number is
  published here, and the fault-tolerance claim rests on control-plane facts
  instead, all of which are ours and all of which are sufficient.

---------------------------------------------------------------------------
WHAT IS PUBLISHED, AND THE LINE THAT ASSIGNS IT
---------------------------------------------------------------------------

* ``job_id``          — ``jobs.id``. The coordinator mints it as
                        ``uuid.uuid4().hex[:12]`` (`flashruntime`
                        ``service/app.py``), or ``fedavg.new_federated_job_id``
                        for a federated run. No user input reaches either.
* ``state``           — ``jobs.status``, written by ``db.insert_job`` /
                        ``db.set_job_status`` / ``db.sync_observed_job_states``
                        from the protocol's own ``JobState`` spelling.
* ``created_at``      — ``jobs.created_at``, ``default now()`` in 0001.
* ``finished_at``     — ``jobs.finished_at``, stamped ``now()`` by
                        ``db.set_job_status``.
* the counts          — computed below, by counting rows.
* ``machine``         — a PSEUDONYM this module assigns ("machine A",
                        "machine B"), numbered by first appearance within this
                        job. See :func:`_pseudonym`.
* ``task``            — likewise ("task 1", "task 2").
* ``claimed_at`` /
  ``resolved_at``     — ``attempts``, written by ``db.record_attempt`` /
                        ``claim_attempt_credit`` / ``record_attempt_failure`` /
                        ``reconcile_expired_attempts``.
* ``outcome``         — ``attempts.outcome``, a four-value enum constrained in
                        the database by migration 0015 and written only by
                        those same three functions.
* ``duration_s``      — subtraction of the two timestamps above, in SQL.
* the event ``kind``  — matched against :data:`PUBLIC_EVENT_TYPES`, a constant
                        in this file. Anything else is dropped, so the
                        published value is chosen here even when the
                        coordinator says something else.
* the event ``at``    — ``flashruntime.protocol.v1alpha1.Event.timestamp``,
                        ``default_factory=utcnow``.
* the event ``seq``   — this module's own position counter.

---------------------------------------------------------------------------
WHAT IS WITHHELD, AND WHY EACH ONE
---------------------------------------------------------------------------

* ``jobs.name``       — the submitter names their own run.
* ``jobs.source``     — repo URL and ref. The URL alone confirms a private
                        repository exists.
* ``jobs.spec``       — image, command, parameters, dataset source paths.
* ``jobs.owner_id`` / ``jobs.pool_id`` — who, and which team.
* ``jobs.share_token`` — the capability itself. A page that echoes its own
                        bearer token hands it to every proxy, referrer header
                        and screenshot in the chain (0014 says the same).
* ``jobs.artifact_bytes`` and its two timestamps — a measurement OF the
                        submitter's output. Our code did the summing, but the
                        magnitude describes files their code wrote, and the
                        page's story does not need it.
* every ``machines`` column — ``name``/hostname and ``node_id`` are
                        host-authored (see above), ``platform`` arrives in the
                        same enrolment body and is treated as theirs on the
                        same evidence, ``capabilities`` is the agent's own
                        self-description, and the raw ``id`` names live
                        infrastructure to somebody holding only a link.
* ``attempts.lease_id`` / ``machine_id`` / ``task_id`` — raw identifiers,
                        replaced by the pseudonyms above.
* every event's ``data`` dict, IN FULL — ``{task_id, node_id}`` today, and
                        the point of withholding the dict rather than
                        allowlisting keys is that the next field added
                        upstream is withheld by default.
* every event's ``message`` — ``TASK_ATTEMPT_FAILED`` carries the worker's own
                        failure ``reason``; ``FAILURE_CLASSIFIED`` interpolates
                        the process exit code. Both are the submitter's.
* every event's ``source`` and ``job_id`` — not needed, so not published;
                        this list is an allowlist, not a denylist.
* any stdout/stderr tail, artifact key, and any checkpoint step number.

**One gap recorded rather than papered over.** The failure CLASS inside
``FAILURE_CLASSIFIED`` lives only in that event's ``message``, beside the exit
code — so it is withheld, and the classification this page does publish is
``attempts.outcome``, which our own code assigns.

---------------------------------------------------------------------------
THE SECOND TEST: DOES THIS RE-IDENTIFY?
---------------------------------------------------------------------------

AS-16.1's provenance question is necessary and **not sufficient**, and
geography is the field family that proves it: a region can be ours by
construction and still name a person.

    "machine B, Czechia" is anonymous only if several machines could
    plausibly be in Czechia.

For rented capacity that holds — the value is assigned by our own venue table
and the pod population is large. For a **volunteer's home rig it does not**: a
small volunteer population makes a country a near-unique identifier, and the
pseudonym above then labels the person instead of protecting them. A judge does
not need the geography, and a volunteer never consented to it.

So the rule for anything geography-shaped (AS-16.2), and the discriminator is
``machines.lifecycle`` (migration 0020, widened by 0023):

* ``leased``     — the control plane rented it. Confirmed end to end:
                   ``sandbox_identity.provision_rented_machine`` is the only
                   writer and ``capacity.acquire.acquire_for_job`` its only
                   caller, so the region is ours by construction and the pod
                   population is large. Publishable.
* ``persistent`` — a volunteer's own machine. Venue at most (``owned``);
                   **omit region entirely**, because a country is a near-unique
                   identifier against a small volunteer population.
* ``ephemeral``  — **fail closed, omit.** Two creators, so the answer cannot be
                   read off the name: ``sandbox_orchestrator`` mints these for
                   evaluation sessions, and the device-code path mints them for
                   rental sessions a host enrols itself — and 0023 records that
                   ``device_codes.lifecycle`` "carries a value an *agent* asks
                   for during enrolment". One of the two creators is not us, and
                   nothing on the row distinguishes them after the fact, so the
                   set as a whole does not clear the provenance test.

**This implementation publishes no geography at all**, and for this payload the
rule above is currently moot rather than merely satisfied: ``public.machines``
carries no region and no venue column for ANY lifecycle, and the one place a
venue id lives (``rented_capacity.venue_id``, migration 0022) belongs to the
capacity subsystem this change does not touch. So there is no honest region to
publish for a `leased` machine either — the rule is written down here for
whoever adds the column, not as an account of a choice made between available
values. Adding one later means reading BOTH tests, not just the provenance one.

The demo loses nothing: "machine A stopped, machine B resumed thirty seconds
later" is the whole recovery narrative and it needs no map.

---------------------------------------------------------------------------
ENFORCEMENT IS IN SQL
---------------------------------------------------------------------------

:data:`JOB_SHARE_COLUMNS` and the queries below narrow in the SELECT, exactly
as ``SESSION_SHARE_COLUMNS`` does, and NOT in the route handler. A filter that
lives in the handler is one refactor away from being skipped; a column that
never leaves the database cannot be leaked by a serializer somebody added
later. That is the security property, and it is why the render functions in
this file are a second narrowing rather than the only one.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

import psycopg

# The one token minter, imported rather than copied. Both kinds of share token
# then have one format (`shr_` + 256 bits from `secrets`), one entropy source
# and one secret-scanner prefix — and a change to either is a change to both,
# which is the property a second local copy would quietly lose.
from .sandbox_sessions import new_share_token

__all__ = [
    "JOB_SHARE_COLUMNS",
    "PUBLIC_EVENT_TYPES",
    "fetch_job_by_share_token",
    "mint_share_token",
    "new_share_token",
    "public_attempts_for_job",
    "public_ledger_view",
    "public_job_view",
    "revoke_share_token",
]


#: What the PUBLIC run page may read off ``public.jobs``. Twelve columns exist;
#: four are here. The eight that are missing are missing for the reasons the
#: module docstring gives one at a time — ``name``, ``source``, ``spec``,
#: ``owner_id``, ``pool_id``, ``share_token``, ``artifact_bytes`` and its two
#: measurement timestamps.
#:
#: ``id`` stays because the route needs it to read the job's attempts and its
#: ledger. Unlike a session id it is rendered whole: a coordinator job id is
#: ``uuid4().hex[:12]`` and names nothing a caller can then read — every
#: authenticated job route is owner- or pool-scoped, and the coordinator is a
#: private service holding the operator token. Truncating a 12-character id to
#: a 12-character "suffix" would be theatre.
#:
#: **Adding a column here is TWO questions, not one.** (1) Can you name the
#: line in our code that assigned this value, with no user input reaching it?
#: (2) Could the value, combined with the rest of this payload, re-identify a
#: person? They come apart — a machine's region passes the first and fails the
#: second for a volunteer's home rig — and the module docstring works both
#: through with the cases that produced them. Anything that fails either is
#: omitted, and "omitted" is the default while you are still deciding.
JOB_SHARE_COLUMNS: tuple[str, ...] = (
    "id",
    "status",
    "created_at",
    "finished_at",
)

_JOB_SHARE_SELECT = ", ".join(JOB_SHARE_COLUMNS)

#: The event kinds this page may name. An ALLOWLIST, so a kind added upstream
#: in a future `flashruntime` release is withheld until somebody reads it —
#: the opposite default from a denylist, which publishes first and asks later.
#:
#: The four AS-16 names are here (`TASK_ATTEMPT_FAILED`, `TASK_EXHAUSTED`,
#: `FAILURE_CLASSIFIED`, `RECOVERY_ACTION_SELECTED`) together with the lease
#: transitions that make them legible: a failure with no claim before it and no
#: requeue after it is not a story. The checkpoint kinds are included as KINDS
#: ONLY and deliberately carry no step number — see AS-16.1 and the module
#: docstring.
#:
#: Everything about an event except its kind and its timestamp is dropped, so
#: adding a name here publishes the fact that something of that kind happened
#: and nothing else.
PUBLIC_EVENT_TYPES: frozenset[str] = frozenset({
    # job lifecycle
    "JOB_ACCEPTED",
    "JOB_SUCCEEDED",
    "JOB_FAILED",
    # Mode A lease lifecycle — the fault-tolerance narrative itself
    "TASK_CREATED",
    "LEASE_CLAIMED",
    "LEASE_RENEWED",
    "LEASE_EXPIRED",
    "TASK_REQUEUED",
    "TASK_ATTEMPT_STARTED",
    "TASK_ATTEMPT_COMPLETED",
    "TASK_ATTEMPT_FAILED",
    "TASK_ATTEMPT_RETRIED",
    "TASK_COMMIT_ACCEPTED",
    "TASK_COMMIT_REJECTED",
    "TASK_EXHAUSTED",
    # recovery policy
    "FAILURE_CLASSIFIED",
    "RECOVERY_ACTION_SELECTED",
    "RECOVERY_FROZEN",
    # checkpoint catalogue — kinds only, never a step
    "CHECKPOINT_PARTS_REGISTERED",
    "CHECKPOINT_MANIFEST_COMMITTED",
    "CHECKPOINT_RESTORE_VERIFIED",
    "CHECKPOINT_REJECTED",
})


# ---------------------------------------------------------------------------
# minting and revoking
#
# Owner-scoped IN THE STATEMENT, never by a check the caller is trusted to have
# run first — the same discipline as `fetch_job_for_owner` and
# `fetch_session_for_owner`. A caller who is not the owner gets None, exactly
# as they would for a job that does not exist, and the route answers 404 to
# both: a 403 for "exists but not yours" confirms to a guesser that the id is
# real.
# ---------------------------------------------------------------------------


def mint_share_token(
    db: psycopg.Connection, job_id: str, owner_id: str
) -> str | None:
    """This job's share token, minting one only if it has none.

    **IDEMPOTENT, AND THAT IS A CORRECTNESS PROPERTY RATHER THAN A NICETY.**
    A second token would not add a link — it would silently invalidate one
    somebody has already sent. So the UPDATE writes only where the column is
    NULL (``coalesce`` would rewrite it every call) and the RETURNING hands
    back whatever the row ends up holding, which for an already-shared job is
    the token it already had.

    One statement, so two concurrent mints cannot both observe NULL and both
    write: the loser's UPDATE matches no row under the row lock the winner
    holds and it reads the winner's value. ``on conflict`` has nothing to do
    here — this is an UPDATE, and the unique index on the column is a
    backstop against the impossible rather than the mechanism.

    Returns ``None`` when the job does not exist *or* belongs to somebody
    else; the caller must not distinguish those two in a response.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            update public.jobs
               set share_token = case
                       when share_token is null then %s
                       else share_token
                   end
             where id = %s and owner_id = %s
            returning share_token
            """,
            (new_share_token(), job_id, owner_id),
        )
        row = cur.fetchone()
    return None if row is None else str(row["share_token"])


def revoke_share_token(
    db: psycopg.Connection, job_id: str, owner_id: str
) -> bool:
    """Kill this job's public link. ``True`` when the job was the caller's.

    NULL is the whole revocation: there is no ``revoked_at`` for a reader to
    forget to consult, and a token that is gone cannot be served by a query
    written next year. Idempotent — revoking a job that was never shared is a
    successful no-op, because "the link does not work" is the state the caller
    asked for and it is already true.
    """
    with db.cursor() as cur:
        cur.execute(
            "update public.jobs set share_token = null"
            " where id = %s and owner_id = %s",
            (job_id, owner_id),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# the public read
# ---------------------------------------------------------------------------


def fetch_job_by_share_token(
    db: psycopg.Connection, share_token: str
) -> dict[str, Any] | None:
    """The public run page's read: no auth, narrowed columns.

    The token IS the authorization, so this is the one read here with no owner
    in it — which is exactly why it returns :data:`JOB_SHARE_COLUMNS` rather
    than ``select *``. The narrowing is in the query and not in a serializer
    the next person to add a column would have to remember.

    An empty or missing token matches nothing. Without the guard a revoked
    job — ``share_token`` back to NULL — would be handed to any caller who
    sent no token at all, which is the one way a revocation could fail open.
    """
    if not share_token:
        return None
    with db.cursor() as cur:
        cur.execute(
            f"select {_JOB_SHARE_SELECT} from public.jobs"
            " where share_token = %s",
            (share_token,),
        )
        return cur.fetchone()


def public_attempts_for_job(
    db: psycopg.Connection, job_id: str
) -> list[dict[str, Any]]:
    """Every lease this job's work was claimed under, pseudonymised in SQL.

    This is the fault-tolerance story and all of it is ours: who held which
    task, when they got it, when it resolved and how. Nothing the submitter or
    a host wrote is in the SELECT list — the machine and the task are
    ORDINALS computed here, and the outcome is the enum migration 0015
    constrains.

    **THE FEDERATED JOIN IS NOT OPTIONAL**, for the reason
    ``metrics_counts_for_owner`` gives at length: a federated run is one
    coordinator job PER ROUND, and ``attempts.job_id`` carries the ROUND's id —
    an id that is not a row in ``public.jobs`` at all. Selecting on the parent
    id alone would report zero attempts for every federated run ever submitted,
    which is a public page that says a job did no work.

    ``extract(epoch …)`` is ``numeric`` and psycopg hands it back as
    ``Decimal``, which must never reach a JSON response; cast in SQL, the same
    call ``claim_attempt_credit`` documents.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            with coordinator_jobs as (
                select %s::text as job_id
                 union
                select r.coordinator_job_id
                  from public.job_rounds r
                 where r.job_id = %s::text
                   and r.coordinator_job_id is not null
            ),
            mine as (
                select a.machine_id, a.task_id, a.claimed_at, a.resolved_at,
                       a.outcome
                  from public.attempts a
                  join coordinator_jobs c on c.job_id = a.job_id
            ),
            -- First appearance decides the ordinal, so the narrative reads in
            -- the order it happened. `machine_id` / `task_id` break the tie so
            -- the numbering is deterministic for two claims in the same
            -- microsecond — a page whose labels shuffle between reloads is a
            -- page nobody can quote.
            machine_order as (
                select machine_id,
                       row_number() over (order by min(claimed_at), machine_id)
                           as n
                  from mine group by machine_id
            ),
            task_order as (
                select task_id,
                       row_number() over (order by min(claimed_at), task_id)
                           as n
                  from mine group by task_id
            )
            select m.n as machine_ordinal,
                   t.n as task_ordinal,
                   mine.claimed_at,
                   mine.resolved_at,
                   mine.outcome,
                   extract(
                       epoch from (mine.resolved_at - mine.claimed_at)
                   )::float8 as duration_s
              from mine
              join machine_order m on m.machine_id = mine.machine_id
              join task_order t on t.task_id = mine.task_id
             order by mine.claimed_at, m.n, t.n
            """,
            (job_id, job_id),
        )
        return list(cur.fetchall())


# ---------------------------------------------------------------------------
# rendering
#
# A SECOND narrowing, on top of the SQL one. Neither is redundant: the queries
# above decide what leaves the database, and these decide what the wire calls
# it — which is where a raw ordinal becomes "machine A" and a column that
# survived the cut can still be dropped.
# ---------------------------------------------------------------------------

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _pseudonym(ordinal: int) -> str:
    """1 -> "A", 26 -> "Z", 27 -> "AA". Spreadsheet columns.

    A pseudonym rather than a name because the fault-tolerance story needs to
    DISTINGUISH two machines, not IDENTIFY them: "machine A stopped, machine B
    resumed 30 s later" is the whole claim, and it survives knowing nothing
    about either host. Assigned by this function from a position, so there is
    no path by which anything a host or a submitter wrote reaches it.

    Stable within one payload and meaningless outside it. That is deliberate:
    a pseudonym stable ACROSS jobs would be a persistent identifier for a
    machine, which is the thing being avoided.

    A pseudonym only protects while nothing beside it re-identifies. Do not
    pair this with a geography, a hostname or a hardware string without
    reading the "does this re-identify?" section of the module docstring —
    "machine B" plus one distinguishing attribute is a name.
    """
    if ordinal < 1:
        return "?"
    out = ""
    while ordinal > 0:
        ordinal, rem = divmod(ordinal - 1, 26)
        out = _ALPHABET[rem] + out
    return out


def _isoformat(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def public_job_view(
    row: Mapping[str, Any], attempts: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """One job as the UNAUTHENTICATED page may see it.

    ``fetch_job_by_share_token`` has already dropped eight columns in SQL. This
    renders the four that survive, plus counts derived from the attempt rows —
    every one of them a count of rows this codebase wrote.

    ``state`` is the last state this control plane OBSERVED, not a live read.
    A non-federated job's end is only ever observed, never reported (the poll
    on ``GET /v1alpha1/jobs/{id}`` is the hook), so a job nobody has opened
    since it finished can still read ``RUNNING`` here. The ledger below carries
    ``JOB_SUCCEEDED`` / ``JOB_FAILED`` when the coordinator recorded one, and
    the two together are honest; a public page that forced a live coordinator
    read to keep this field fresh would be an unauthenticated caller driving a
    round trip per hit.

    **Attempted and accepted are counted separately** — hard rule 4, the same
    distinction the credit ledger draws. ``attempts_total`` is every lease
    handed out; ``tasks_accepted`` counts distinct tasks that actually
    committed. Collapsing them would drive every finished job to "100%" and
    erase the wasted work this page exists to show.
    """
    outcomes = [a.get("outcome") for a in attempts]
    accepted_tasks = {
        a.get("task_ordinal") for a in attempts if a.get("outcome") == "accepted"
    }
    return {
        "job_id": row.get("id"),
        "state": row.get("status"),
        "created_at": _isoformat(row.get("created_at")),
        "finished_at": _isoformat(row.get("finished_at")),
        "tasks_total": len({a.get("task_ordinal") for a in attempts}),
        "tasks_accepted": len(accepted_tasks),
        "machines_claiming": len({a.get("machine_ordinal") for a in attempts}),
        "attempts_total": len(attempts),
        "attempts_accepted": outcomes.count("accepted"),
        "attempts_failed": outcomes.count("failed"),
        "attempts_expired": outcomes.count("expired"),
        "attempts_abandoned": outcomes.count("abandoned"),
        # Unresolved is in flight or predates migration 0015. Reported as its
        # own number and never folded into failures: counting an attempt that
        # has not finished as one that went wrong is survivorship bias
        # inverted, and 0015 exists precisely to keep the two apart.
        "attempts_unresolved": outcomes.count(None),
    }


def public_attempt_view(attempt: Mapping[str, Any]) -> dict[str, Any]:
    """One lease as the public page may see it: a pseudonym, two instants and
    our own outcome enum. No lease id, no machine id, no task id."""
    return {
        "machine": f"machine {_pseudonym(int(attempt['machine_ordinal']))}",
        "task": f"task {int(attempt['task_ordinal'])}",
        "claimed_at": _isoformat(attempt.get("claimed_at")),
        "resolved_at": _isoformat(attempt.get("resolved_at")),
        "outcome": attempt.get("outcome"),
        "duration_s": attempt.get("duration_s"),
    }


def public_ledger_view(events: Iterable[Any]) -> list[dict[str, Any]]:
    """The coordinator's ledger, reduced to KIND and TIMING.

    Takes the whole list and returns the whole list because the ``seq`` is a
    position in the PUBLISHED sequence — assigned here, dense from 1, and not
    the coordinator's own index. Exposing the coordinator's index would leak
    how many events were dropped, which is a count of things about a job the
    page has declined to describe.

    Everything else is discarded, not filtered: ``data`` in full, ``message``
    (``TASK_ATTEMPT_FAILED`` carries the worker's own failure reason and
    ``FAILURE_CLASSIFIED`` interpolates the process exit code), ``source`` and
    ``job_id``. An entry whose kind is not in :data:`PUBLIC_EVENT_TYPES` is
    dropped entirely — which is also what makes a coordinator answering
    something unexpected harmless here.
    """
    out: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        kind = event.get("type")
        if not isinstance(kind, str) or kind not in PUBLIC_EVENT_TYPES:
            continue
        at = event.get("timestamp")
        out.append({
            "seq": len(out) + 1,
            "kind": kind,
            "at": _isoformat(at) if isinstance(at, (str, datetime)) else None,
        })
    return out
