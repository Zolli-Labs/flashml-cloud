"""One-session machine credentials for a managed sandbox worker.

An FC Agent Sandbox hosts a real FlashNode. To register and claim work it
needs a machine token — the same credential a volunteer's laptop carries. The
difference is entirely in the lifetime: a laptop enrols once and keeps its
token for months, while a sandbox exists for one session, and its credential
must die with it on every path out, including the paths where the sandbox is
already gone.

WHY THIS IS A SEPARATE MODULE AND NOT MORE OF ``enrolment.py``
--------------------------------------------------------------
It reuses ``enrolment``'s credential *machinery* wholesale —
``auth.new_machine_token``/``hash_machine_token``, the ``machines`` row,
``authenticate_machine``, ``revoke_machine_row``. There is exactly one kind of
machine token in this system and this module does not invent a second one; a
credential minted here is indistinguishable, to every agent route, from one
redeemed through the device-code flow.

What it does not share is the *flow*. ``enrolment``'s whole argument is a
human in the loop: a person reads a code off a terminal and approves it, and
the module's ordering exists to make sure no token reaches a machine nobody
approved. There is no person here and there is no terminal. The control plane
decides, on its own authority, that a sandbox it is about to launch may claim
work — and then has to guarantee that the blast radius of that decision is one
pool containing one machine. Folding a no-human, self-authorising path into a
file whose docstring is "the human is the authorisation" would blur the one
property each half is there to state. They stay adjacent, not merged.

THE SECURITY BOUNDARY IS THE POOL, AND IT HAS A KNOWN EDGE
----------------------------------------------------------
Placement is fail-closed on pool membership: the coordinator's seventh gate
refuses a pool-scoped task to any node whose ``capabilities.pools`` stamp does
not list that pool, and this API builds that stamp itself from
``pool_ids_for_machine`` rather than believing what an agent claims. So:

- **This machine's task cannot be claimed elsewhere.** The task carries the
  pool; no other node is stamped with it, because the pool contains only this
  machine. That is what :func:`assert_pool_isolated` exists to guarantee.
- **Another team's pool job cannot claim this machine.** Same gate, other
  direction.

The edge: a **poolless** job — every public submission — carries no ``pool`` in
its payload at all, so the seventh gate does not run for it. What keeps a
public job off this machine is the gate below it: a public job compiles to
``isolation.tier = "sandboxed", allowFallback = false``, which is placeable
only on a node advertising ``sandbox_capable is True``. An FC Agent Sandbox
cannot nest a container runtime and must therefore run FlashNode in a mode
that advertises ``sandbox_capable = False``. **That is a deployment
requirement, not something this module can enforce**, and it is pinned by
``test_the_pool_gate_alone_does_not_confine_a_poolless_job`` so nobody reads
the pool binding as covering more than it does. The stamp lives on the
coordinator's registry, written from the agent's own registration; the
``machines.sandbox_capable`` column is a display-only snapshot and no
authority at all.

NOTHING STORED IS A CREDENTIAL
------------------------------
The raw token is returned once, in memory, and never written: only its sha256
digest and a 12-character prefix reach ``public.machines``. Same discipline
``github_app.py`` applies to installation tokens and for the same reason — a
database leak must not be replayable as a live credential.

LINKAGE TO THE SESSION LEDGER
-----------------------------
The provisioned machine is recorded on its session through the ledger's own
compare-and-set, not by a write from here::

    cred = provision_sandbox_machine(db, owner_id=..., pool_id=..., ...)
    sandbox_sessions.transition(
        db, session_id, "REQUESTED", "ACTIVE", machine_id=cred.machine_id
    )

``transition``'s column arguments are fill-in-only, so a later transition that
says nothing about the machine cannot erase it. There is no second place a
session's machine is recorded and no new column for one.

No FastAPI here — pure functions the app layer wraps into HTTP responses.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import psycopg

from flashml_cloud_api import db as dbmod
from flashml_cloud_api.auth import hash_machine_token, new_machine_token
from flashml_cloud_api.enrolment import NodeAlreadyEnrolled
from flashml_cloud_api.sandbox_sessions import DEFAULT_PROVIDER

__all__ = [
    "EphemeralMachineCredential",
    "NodeAlreadyEnrolled",
    "PoolNotFound",
    "PoolNotIsolated",
    "SandboxIdentityError",
    "assert_pool_isolated",
    "provision_sandbox_machine",
    "revoke_sandbox_machine",
]

#: What goes in ``machines.platform``. The same string the session ledger
#: files under ``provider``, imported rather than repeated so the two cannot
#: drift into describing one sandbox two ways.
SANDBOX_PLATFORM = DEFAULT_PROVIDER

#: How much of the raw token is kept for display. Matches what
#: ``redeem_device_code`` stores, so the console renders both kinds of
#: credential identically.
TOKEN_PREFIX_LENGTH = 12


class SandboxIdentityError(Exception):
    """Base class for provisioning failures the app layer must turn into a
    clean HTTP response. Never let one surface as an unhandled 500 — and
    never let the underlying integrity error do so either."""


class PoolNotFound(SandboxIdentityError):
    """The target pool does not exist, is not visible to this caller, or is
    visible but not theirs to mint into.

    All three, deliberately one exception: the repo's 404-not-403 doctrine
    (``fetch_pool_for_member``, ``fetch_machine_for_owner``). Splitting them
    would let a caller with a guessed id learn that it names a real pool.
    """


class PoolNotIsolated(SandboxIdentityError):
    """The pool does not contain exactly this one machine, or this machine
    serves more than this one pool.

    Raised *before* any token is minted, and inside the same transaction as
    the machine row, so a pool that fails this check is left with no machine,
    no binding and no credential.
    """


@dataclass(frozen=True)
class EphemeralMachineCredential:
    """What one session's worker is handed. ``raw_token`` exists in this
    process's memory and nowhere else — it is not in the database, it is not
    recoverable, and re-reading it is not a feature that was omitted.

    ``repr=False`` on the token is not decoration. These objects end up in
    exception chains and debug logs, and a dataclass's generated ``__repr__``
    is the shortest path from "we never store the token" to a live credential
    sitting in a log aggregator.
    """

    machine_id: str
    node_id: str
    raw_token: str = field(repr=False)


def assert_pool_isolated(
    db: psycopg.Connection, *, pool_id: str, machine_id: str
) -> None:
    """Raise :class:`PoolNotIsolated` unless ``pool_id`` contains exactly
    ``machine_id`` and ``machine_id`` serves exactly ``pool_id``.

    Both directions are load-bearing and neither implies the other:

    - *Nothing else in the pool.* Another machine bound here would be stamped
      with the same pool and would therefore be an eligible claimant for this
      session's tasks — the whole point of the isolation pool is that there is
      no second candidate.
    - *Nothing else for the machine.* A sandbox also bound to a team pool
      would be an eligible claimant for that team's pool jobs, which carry
      ``allowFallback`` and would then run unsandboxed inside the session's
      environment.

    Reads raw ``machine_pools`` rows (``machine_ids_bound_to_pool`` /
    ``pool_ids_bound_to_machine``), not the membership-narrowed view the
    placement stamp uses. A binding whose owner is not currently a pool member
    is inert *today* and live the moment they are invited back, with nothing
    written to ``machine_pools`` in between; an assertion that ignored it
    would be quietly revoked by an unrelated ``insert into pool_members``.

    Safe and cheap to call again at any point in the session — the invariant
    is claimed for the session's lifetime, not for the instant of minting, and
    nothing here mutates.
    """
    machine_id = str(machine_id)
    pool_id = str(pool_id)

    in_pool = dbmod.machine_ids_bound_to_pool(db, pool_id)
    if in_pool != [machine_id]:
        others = [m for m in in_pool if m != machine_id]
        if machine_id not in in_pool:
            raise PoolNotIsolated(
                f"machine {machine_id} is not bound to pool {pool_id}"
            )
        raise PoolNotIsolated(
            f"pool {pool_id} must contain only machine {machine_id}, but also "
            f"holds {', '.join(others)}"
        )

    serves = dbmod.pool_ids_bound_to_machine(db, machine_id)
    if serves != [pool_id]:
        others = [p for p in serves if p != pool_id]
        raise PoolNotIsolated(
            f"machine {machine_id} must serve only pool {pool_id}, but is "
            f"also bound to {', '.join(others)}"
        )


def provision_sandbox_machine(
    db: psycopg.Connection,
    *,
    owner_id: str,
    pool_id: str,
    node_id: str,
    label: str | None,
) -> EphemeralMachineCredential:
    """Mint one session's worker identity and return its token exactly once.

    ``owner_id`` comes from the verified JWT ``sub`` or from the controller's
    own record of who asked for the session — never from a request body, the
    same rule ``approve_device_code`` and ``create_session`` state.

    The order below is the security property, not an implementation detail:

    1. **Authorise and serialise.** ``lock_pool_for_owner`` refuses anyone who
       does not own the pool (404-shaped, see :class:`PoolNotFound`) and holds
       a row lock on it for the rest of the transaction. Without the lock, two
       concurrent provisions into one pool each see only their own binding at
       READ COMMITTED, each pass the isolation check, and both commit — an
       "isolated" pool with two machines. Requiring *membership* as well as
       ownership is not belt-and-braces either: ``pool_ids_for_machine``
       intersects bindings with ``pool_members``, so a machine minted into a
       pool its owner does not belong to would be stamped with nothing and
       could never claim the session's work at all.
    2. **Insert the machine, then bind it.** A ``node_id`` already taken is
       :class:`NodeAlreadyEnrolled` — the same refusal, and the same reason,
       as in the device-code flow: the column is globally unique precisely so
       one machine cannot adopt another's identity.
    3. **Assert isolation before minting.** A token that exists is a token
       that can be used; it is created last, once the boundary is known to
       hold.

    All of it in one transaction, so every failure leaves the pool exactly as
    it was — no orphan machine row burning a ``node_id``, no half-bound pool.
    """
    owner_id = str(owner_id)
    pool_id = str(pool_id)
    node_id = (node_id or "").strip()
    if not node_id:
        raise ValueError("node_id is required to provision a sandbox machine")

    with db.transaction():
        if dbmod.lock_pool_for_owner(db, pool_id, owner_id) is None:
            raise PoolNotFound(pool_id)

        try:
            machine_id = dbmod.insert_machine(
                db,
                owner_id=owner_id,
                node_id=node_id,
                name=label,
                platform=SANDBOX_PLATFORM,
            )
        except psycopg.errors.UniqueViolation as exc:
            raise NodeAlreadyEnrolled(node_id) from exc

        dbmod.bind_machine_pool(db, machine_id=machine_id, pool_id=pool_id)
        assert_pool_isolated(db, pool_id=pool_id, machine_id=machine_id)

        token = new_machine_token()
        dbmod.set_machine_token(
            db, machine_id, hash_machine_token(token), token[:TOKEN_PREFIX_LENGTH]
        )

    return EphemeralMachineCredential(
        machine_id=str(machine_id), node_id=node_id, raw_token=token
    )


def revoke_sandbox_machine(
    db: psycopg.Connection, *, machine_id: str, owner_id: str
) -> bool:
    """End a session's credential. Idempotent, total, and survivable.

    **Total.** Two things must stop being true, and they are independent: the
    token must stop authenticating (``status = 'revoked'``, which
    ``authenticate_machine`` refuses immediately rather than at some natural
    expiry — machine tokens have none), and the machine must stop being a
    member of anything (every ``machine_pools`` row dropped, so a pool that
    outlives the session is not left holding a dead claimant, and re-using the
    pool for the next session starts from an empty one).

    **Idempotent.** The unbinds run on every call, including calls where the
    row is already revoked — that is the case a crashed cleanup leaves behind,
    and "already revoked" must not mean "stop, the bindings are someone else's
    problem now". Only the status change reports; a second call unbinds
    nothing, revokes nothing, and returns False.

    **Survivable.** Nothing here touches Alibaba, the session ledger, or any
    state outside this database. A sandbox that has already expired, been
    killed, or never existed revokes exactly as cleanly as a live one, because
    cleanup paths run in a ``finally`` and the thing they must guarantee is
    that the credential is dead — not that the sandbox answered.

    Returns True only if this call performed the revocation. False covers an
    unknown machine, a machine belonging to someone else, and one already
    revoked, indistinguishably — ``revoke_machine_row``'s convention, kept so
    a caller cannot enumerate other people's machine ids with it.
    """
    machine_id = str(machine_id)
    owner_id = str(owner_id)

    with db.transaction():
        # Ownership first: unbinding is a write, and a write must never reach
        # a row this caller cannot see. Returns None for "no such machine" and
        # "not yours" alike, which is the same False either way.
        if dbmod.fetch_machine_for_owner(db, machine_id, owner_id) is None:
            return False

        for bound_pool in dbmod.pool_ids_bound_to_machine(db, machine_id):
            dbmod.unbind_machine_pool(
                db, machine_id=machine_id, pool_id=bound_pool
            )

        return dbmod.revoke_machine_row(db, machine_id, owner_id)
