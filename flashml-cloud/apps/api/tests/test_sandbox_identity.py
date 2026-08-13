"""Machine credentials this control plane mints on its own authority.

Two paths, and most of this file is about the first:

- :func:`provision_sandbox_machine` — an FC sandbox worker, which holds a
  session credential and runs code the submitter wrote. It ends by asserting
  pool isolation, and that assertion is a security boundary.
- :func:`provision_rented_machine` — a GPU the operator rented into the user's
  own workspace. It asserts nothing about the pool, because being an eligible
  claimant alongside the user's own machines is the whole point of renting
  (D6). Its own section is at the bottom of this file, together with the tests
  that prove the first function's invariant survived the neighbourhood
  changing.

The properties pinned here are the ones whose failure hands someone a working
credential they should not have, or leaves one alive after the session that
justified it is over:

- **The raw token is returned once and stored nowhere.** Not "stored
  encrypted", not "stored in a column nothing selects" — absent. The test that
  says so scans every table in the schema rather than the one column we
  happened to think of.
- **The pool contains exactly one machine.** Pool membership is the
  coordinator's seventh, fail-closed placement gate; this suite proves the
  gate against the real ``flashruntime`` predicate rather than restating what
  we believe it does, the same way ``test_compile`` checks its output against
  the real ``CommandRecipe``.
- **Ownership is checked, and refusal is shaped like absence.** A non-owner
  gets a 404-shaped nothing, not a 403 confirming the pool is real.
- **Revocation is idempotent, total, and works after the sandbox is gone.**
  Cleanup runs in a ``finally``; a revoke that needed the sandbox to answer
  would be a revoke that never happens on the paths that most need it.
"""
from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import sandbox_identity as si
from flashml_cloud_api import sandbox_sessions as ss
from flashml_cloud_api.auth import hash_machine_token
from flashml_cloud_api.enrolment import NodeAlreadyEnrolled, authenticate_machine


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def _user(db) -> str:
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id, email) values (%s, %s)",
            (user_id, f"{user_id[:8]}@example.com"),
        )
        cur.execute("insert into public.profiles (id) values (%s)", (user_id,))
    return user_id


def _pool(db, owner_id: str) -> str:
    """A pool through the real constructor, which seats its owner as a
    member. That membership is not decoration here: ``pool_ids_for_machine``
    intersects bindings with it, so a machine minted into a raw-inserted pool
    would be stamped with no pools at all."""
    return str(dbmod.create_pool(db, name="fc-sandbox", owner_id=owner_id)["id"])


def _provision(db, owner_id: str, pool_id: str, node_id: str | None = None):
    return si.provision_sandbox_machine(
        db,
        owner_id=owner_id,
        pool_id=pool_id,
        node_id=node_id or f"fc-sandbox-{uuid.uuid4()}",
        label="fc session worker",
    )


def _rent(db, owner_id: str, pool_id: str, node_id: str | None = None,
          platform: str | None = "runpod"):
    """The sibling path: a GPU rented into the submitter's own pool."""
    return si.provision_rented_machine(
        db,
        owner_id=owner_id,
        pool_id=pool_id,
        node_id=node_id or f"rented-{uuid.uuid4()}",
        label="rented runpod for job j-1",
        platform=platform,
    )


def _machine_row(db, machine_id: str) -> dict:
    with db.cursor() as cur:
        cur.execute("select * from public.machines where id = %s", (machine_id,))
        row = cur.fetchone()
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------


def test_provisioning_returns_an_active_machine_bound_to_exactly_its_pool(db):
    owner = _user(db)
    pool = _pool(db, owner)

    cred = _provision(db, owner, pool, node_id="fc-sandbox-alpha")

    row = _machine_row(db, cred.machine_id)
    assert row["status"] == "active"
    assert row["node_id"] == "fc-sandbox-alpha"
    assert row["name"] == "fc session worker"
    assert row["platform"] == si.SANDBOX_PLATFORM == ss.DEFAULT_PROVIDER
    assert str(row["owner_id"]) == owner

    # The binding, and the placement stamp the register proxy builds from it.
    assert dbmod.pool_ids_bound_to_machine(db, cred.machine_id) == [pool]
    assert dbmod.pool_ids_for_machine(db, cred.machine_id) == [pool]


def test_the_token_authenticates_as_this_machine_and_only_this_machine(db):
    owner = _user(db)
    cred = _provision(db, owner, _pool(db, owner))

    machine = authenticate_machine(db, cred.raw_token)
    assert machine is not None
    assert str(machine.id) == cred.machine_id
    assert machine.node_id == cred.node_id

    # It is an ordinary machine token — the same credential type the
    # device-code flow issues, not a second one that agent routes would have
    # to learn about.
    assert cred.raw_token.startswith("fmk_")
    assert authenticate_machine(db, cred.raw_token + "x") is None


def test_each_provision_mints_a_fresh_token_and_none_can_be_read_back(db):
    """"Returned once" is a property of the API surface, not a promise. Two
    provisions never produce the same token, and the only exported reader of a
    machine row cannot carry a token digest back out of the API at all."""
    owner = _user(db)
    first = _provision(db, owner, _pool(db, owner))
    second = _provision(db, owner, _pool(db, owner))

    assert first.raw_token != second.raw_token
    assert "token_hash" not in dbmod.MACHINE_PUBLIC_COLUMNS
    # Nothing in this module's exported surface hands a credential back.
    assert si.__all__ == sorted(si.__all__)
    assert [name for name in si.__all__ if "token" in name.lower()] == []


def test_the_credential_repr_does_not_carry_the_token(db):
    """These objects land in exception chains and debug logs. A dataclass's
    generated repr is the shortest path from "we never store the token" to a
    live credential sitting in a log aggregator."""
    owner = _user(db)
    cred = _provision(db, owner, _pool(db, owner))

    assert cred.raw_token not in repr(cred)
    assert cred.machine_id in repr(cred)


def test_only_the_hash_and_a_prefix_reach_the_machines_row(db):
    owner = _user(db)
    cred = _provision(db, owner, _pool(db, owner))

    row = _machine_row(db, cred.machine_id)
    assert row["token_hash"] == hash_machine_token(cred.raw_token)
    assert row["token_prefix"] == cred.raw_token[: si.TOKEN_PREFIX_LENGTH]
    assert len(row["token_prefix"]) == si.TOKEN_PREFIX_LENGTH


def test_no_raw_token_is_stored_anywhere_in_the_schema(db):
    """The whole database, every table, every column — not the one column we
    thought to check. A credential that leaks does so through the column
    nobody remembered, so the assertion is made against the schema itself and
    keeps holding when a later migration adds a table.
    """
    owner = _user(db)
    cred = _provision(db, owner, _pool(db, owner))
    ss.create_session(
        db,
        owner_id=owner,
        pool_id=_pool(db, owner),
        training_job_id="job-train-1",
        region="ap-southeast-1",
        template="flashnode-base",
        machine_id=cred.machine_id,
    )

    with db.cursor() as cur:
        cur.execute(
            """
            select table_name
              from information_schema.tables
             where table_schema = 'public' and table_type = 'BASE TABLE'
            """
        )
        tables = [r["table_name"] for r in cur.fetchall()]
    assert "machines" in tables  # the scan below is only worth what it covers

    for table in tables:
        with db.cursor() as cur:
            cur.execute(
                sql.SQL("select * from public.{}").format(sql.Identifier(table))
            )
            dumped = "\n".join(repr(row) for row in cur.fetchall())
        assert cred.raw_token not in dumped, f"raw token found in public.{table}"
        # The suffix alone, in case something stored it with the prefix
        # stripped or re-encoded around it.
        assert cred.raw_token[si.TOKEN_PREFIX_LENGTH :] not in dumped, table

    # ...and the digest that legitimately IS stored proves the scan looks
    # where a plaintext token would have been written.
    assert hash_machine_token(cred.raw_token) in repr(
        _machine_row(db, cred.machine_id)
    )


def test_a_node_id_already_enrolled_is_refused_and_leaves_nothing_behind(db):
    """The same refusal the device-code flow makes, reusing its exception:
    ``machines.node_id`` is globally unique so that one machine cannot adopt
    another's identity, and a sandbox is not an exception to that."""
    owner = _user(db)
    first_pool, second_pool = _pool(db, owner), _pool(db, owner)
    _provision(db, owner, first_pool, node_id="fc-sandbox-taken")

    with pytest.raises(NodeAlreadyEnrolled):
        _provision(db, owner, second_pool, node_id="fc-sandbox-taken")

    assert dbmod.machine_ids_bound_to_pool(db, second_pool) == []


# ---------------------------------------------------------------------------
# Pool isolation
# ---------------------------------------------------------------------------


def test_the_isolation_invariant_holds_for_a_freshly_provisioned_machine(db):
    owner = _user(db)
    pool = _pool(db, owner)
    cred = _provision(db, owner, pool)

    si.assert_pool_isolated(db, pool_id=pool, machine_id=cred.machine_id)


def test_a_second_machine_in_the_pool_breaks_the_invariant(db):
    owner = _user(db)
    pool = _pool(db, owner)
    cred = _provision(db, owner, pool)

    intruder = dbmod.insert_machine(
        db, owner_id=owner, node_id="laptop-1", name="laptop", platform="linux"
    )
    dbmod.bind_machine_pool(db, machine_id=intruder, pool_id=pool)

    with pytest.raises(si.PoolNotIsolated):
        si.assert_pool_isolated(db, pool_id=pool, machine_id=cred.machine_id)


def test_a_second_pool_for_the_machine_breaks_the_invariant(db):
    """The other direction, and it is not implied by the first. A sandbox also
    bound to a team pool is an eligible claimant for that team's pool jobs —
    which carry ``allowFallback`` and would run unsandboxed inside the
    session's environment."""
    owner = _user(db)
    pool = _pool(db, owner)
    cred = _provision(db, owner, pool)

    dbmod.bind_machine_pool(db, machine_id=cred.machine_id, pool_id=_pool(db, owner))

    with pytest.raises(si.PoolNotIsolated):
        si.assert_pool_isolated(db, pool_id=pool, machine_id=cred.machine_id)


def test_a_dormant_binding_by_a_non_member_still_breaks_the_invariant(db):
    """``pool_ids_for_machine`` correctly ignores a binding whose owner is not
    a pool member — it is inert for placement today. The isolation assertion
    must not: the day that person is invited in, the binding goes live with no
    write to ``machine_pools`` to notice, and an assertion that had ignored it
    would have been revoked by an unrelated ``insert into pool_members``."""
    owner = _user(db)
    pool = _pool(db, owner)
    cred = _provision(db, owner, pool)

    stranger = _user(db)
    lurker = dbmod.insert_machine(
        db, owner_id=stranger, node_id="lurker-1", name=None, platform=None
    )
    dbmod.bind_machine_pool(db, machine_id=lurker, pool_id=pool)

    # Inert for placement...
    assert dbmod.pool_ids_for_machine(db, lurker) == []
    # ...and still a violation.
    with pytest.raises(si.PoolNotIsolated):
        si.assert_pool_isolated(db, pool_id=pool, machine_id=cred.machine_id)


def test_provisioning_into_an_occupied_pool_mints_nothing(db):
    """The assertion runs before the token, inside the transaction, so a pool
    that fails it is left with no machine, no binding and no credential."""
    owner = _user(db)
    pool = _pool(db, owner)
    squatter = str(
        dbmod.insert_machine(
            db, owner_id=owner, node_id="laptop-2", name=None, platform=None
        )
    )
    dbmod.bind_machine_pool(db, machine_id=squatter, pool_id=pool)

    with pytest.raises(si.PoolNotIsolated):
        _provision(db, owner, pool, node_id="fc-sandbox-doomed")

    assert dbmod.machine_ids_bound_to_pool(db, pool) == [squatter]
    with db.cursor() as cur:
        cur.execute(
            "select id from public.machines where node_id = %s",
            ("fc-sandbox-doomed",),
        )
        assert cur.fetchone() is None


def test_provisioning_serialises_on_the_pool_row(db, postgres_dsn):
    """The check-then-write window, closed by the row lock in
    ``lock_pool_for_owner``.

    Without it two provisions into one pool read ``machine_pools`` at READ
    COMMITTED, neither sees the other's uncommitted binding, both pass the
    isolation assertion, and both commit — an "isolated" pool holding two
    machines and two live credentials. A controller retrying a slow provision
    is the ordinary way to get two of these, not a hypothetical.

    Asserted by holding the lock and watching a provision fail to get in,
    rather than by racing two threads: a race that happens to finish in order
    passes whether the lock exists or not, which is a test that would not
    notice the lock being deleted.
    """
    owner = _user(db)
    pool = _pool(db, owner)

    blocked = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    blocked.autocommit = True
    blocked.execute("set lock_timeout = '250ms'")
    try:
        with db.transaction():
            assert dbmod.lock_pool_for_owner(db, pool, owner) is not None

            with pytest.raises(psycopg.errors.LockNotAvailable):
                _provision(blocked, owner, pool, node_id="fc-sandbox-blocked")

        # The lock is gone with the transaction, and the shut-out attempt left
        # nothing behind — not a machine row, not a burned node_id.
        with db.cursor() as cur:
            cur.execute(
                "select id from public.machines where node_id = %s",
                ("fc-sandbox-blocked",),
            )
            assert cur.fetchone() is None

        cred = _provision(blocked, owner, pool, node_id="fc-sandbox-after")
        assert dbmod.machine_ids_bound_to_pool(db, pool) == [cred.machine_id]
    finally:
        blocked.close()


# --- the invariant, against the real placement gate ------------------------
#
# `flashruntime.scheduler` rather than a restatement of what we think it does,
# the same reason `test_compile` validates its output against the real
# `CommandRecipe`. This is the seventh, fail-closed gate; if it ever stops
# reading `capabilities.pools`, this suite must fail here rather than keep
# asserting a boundary that has moved.


def _node_view(node_id: str, pools: list[str], *, sandbox_capable: bool = False):
    """A node as the coordinator sees it. ``capabilities.pools`` is stamped by
    THIS API from ``pool_ids_for_machine`` — never from what the agent claims
    about itself (see ``app._stamp_pools``).

    ``sandbox_capable=False`` is the FC sandbox's real posture: an Agent
    Sandbox cannot nest a container runtime, so the worker runs a no-container
    tier and advertises no sandbox.
    """
    return {
        "node_id": node_id,
        "sandbox_capable": sandbox_capable,
        "argv_capable": False,
        "unsandboxed_argv_capable": True,
        "module_capable": True,
        "capabilities": {"pools": list(pools)},
    }


def _task(payload: dict):
    from flashruntime.protocol.v1alpha1 import TaskSpec

    return TaskSpec(
        task_id="t-1", job_id="j-1", commit_key="ck-1", payload=payload
    )


def _pool_task(pool_id: str):
    """What `compile.py` emits for a pool job: the pool named, and the
    `allowFallback` waiver that only a pool job carries."""
    return _task(
        {
            "pool": pool_id,
            "isolation": {"tier": "sandboxed", "allowFallback": True},
        }
    )


def _public_task():
    """What `compile.py` emits for a public job: `placement.pool = "any"`,
    which expands to a payload with no `pool` key at all, and no waiver."""
    return _task({"isolation": {"tier": "sandboxed", "allowFallback": False}})


def test_the_sessions_task_cannot_be_claimed_by_any_other_machine(db):
    from flashruntime.scheduler import IsolationAwarePlacement

    owner = _user(db)
    pool = _pool(db, owner)
    cred = _provision(db, owner, pool)
    policy = IsolationAwarePlacement()

    sandbox_node = _node_view(cred.node_id, dbmod.pool_ids_for_machine(db, cred.machine_id))
    assert policy.eligible(_pool_task(pool), sandbox_node) is True

    # A volunteer laptop, and a machine in someone else's pool. Neither is
    # stamped with this pool, so neither may claim the task.
    assert policy.eligible(_pool_task(pool), _node_view("laptop-1", [])) is False
    assert (
        policy.eligible(_pool_task(pool), _node_view("laptop-2", [str(uuid.uuid4())]))
        is False
    )

    # ...and this is exactly why the pool must contain only one machine: a
    # second one bound here would be stamped with the pool and would be an
    # eligible claimant. The gate confines the task to the pool; only
    # `assert_pool_isolated` confines the pool to one machine.
    assert policy.eligible(_pool_task(pool), _node_view("laptop-3", [pool])) is True


def test_another_teams_pool_job_cannot_claim_the_sandbox_machine(db):
    from flashruntime.scheduler import IsolationAwarePlacement

    owner = _user(db)
    pool = _pool(db, owner)
    cred = _provision(db, owner, pool)

    other_pool = str(uuid.uuid4())
    sandbox_node = _node_view(cred.node_id, dbmod.pool_ids_for_machine(db, cred.machine_id))
    assert IsolationAwarePlacement().eligible(_pool_task(other_pool), sandbox_node) is False


def test_a_public_job_cannot_claim_the_sandbox_machine(db):
    from flashruntime.scheduler import IsolationAwarePlacement

    owner = _user(db)
    pool = _pool(db, owner)
    cred = _provision(db, owner, pool)

    sandbox_node = _node_view(cred.node_id, dbmod.pool_ids_for_machine(db, cred.machine_id))
    assert IsolationAwarePlacement().eligible(_public_task(), sandbox_node) is False


def test_the_pool_gate_alone_does_not_confine_a_poolless_job(db):
    """The edge of the boundary, written down rather than assumed away.

    A public job carries no `pool` in its payload, so the seventh gate never
    runs for it — pool binding is one-directional and does NOT keep public
    work off a bound machine. What keeps it off is the gate below: a public
    job is `tier: sandboxed, allowFallback: false`, placeable only on a node
    advertising `sandbox_capable is True`, which an FC Agent Sandbox cannot
    do and must not claim.

    That is a deployment requirement on how FlashNode is launched inside the
    sandbox, not something `sandbox_identity` can enforce — the stamp the
    coordinator judges comes from the agent's own registration, and
    `machines.sandbox_capable` is a display-only snapshot with no authority.
    The assertion below is what would have to change if that ever stopped
    being true.
    """
    from flashruntime.scheduler import IsolationAwarePlacement

    owner = _user(db)
    pool = _pool(db, owner)
    cred = _provision(db, owner, pool)

    pretending = _node_view(
        cred.node_id,
        dbmod.pool_ids_for_machine(db, cred.machine_id),
        sandbox_capable=True,
    )
    assert IsolationAwarePlacement().eligible(_public_task(), pretending) is True


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def test_a_stranger_cannot_provision_into_someone_elses_pool(db):
    owner = _user(db)
    pool = _pool(db, owner)
    stranger = _user(db)

    with pytest.raises(si.PoolNotFound):
        _provision(db, stranger, pool, node_id="fc-sandbox-stranger")

    assert dbmod.machine_ids_bound_to_pool(db, pool) == []


def test_a_plain_member_cannot_provision_into_a_pool_they_do_not_own(db):
    """Membership is not administration. Minting a machine into a pool changes
    what every other member of it is exposed to, so the check is ownership —
    and the refusal is shaped like absence, not like a 403."""
    owner = _user(db)
    pool = _pool(db, owner)
    member = _user(db)
    with db.cursor() as cur:
        cur.execute(
            "insert into public.pool_members (pool_id, user_id) values (%s, %s)",
            (pool, member),
        )
    assert dbmod.is_pool_member(db, pool, member) is True

    with pytest.raises(si.PoolNotFound):
        _provision(db, member, pool, node_id="fc-sandbox-member")

    assert dbmod.machine_ids_bound_to_pool(db, pool) == []


def test_an_unknown_pool_id_is_refused_the_same_way_as_someone_elses(db):
    """Same exception, no distinguishing detail: a caller with a guessed id
    must not learn that it names a real pool."""
    owner = _user(db)
    theirs = _pool(db, _user(db))

    with pytest.raises(si.PoolNotFound) as unknown:
        _provision(db, owner, str(uuid.uuid4()), node_id="fc-sandbox-a")
    with pytest.raises(si.PoolNotFound) as forbidden:
        _provision(db, owner, theirs, node_id="fc-sandbox-b")

    assert type(unknown.value) is type(forbidden.value)


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------


def test_revocation_kills_the_token_and_every_binding(db):
    owner = _user(db)
    pool = _pool(db, owner)
    cred = _provision(db, owner, pool)

    assert si.revoke_sandbox_machine(db, machine_id=cred.machine_id, owner_id=owner)

    assert _machine_row(db, cred.machine_id)["status"] == "revoked"
    assert dbmod.machine_ids_bound_to_pool(db, pool) == []
    assert dbmod.pool_ids_bound_to_machine(db, cred.machine_id) == []


def test_a_revoked_token_authenticates_as_nothing(db):
    owner = _user(db)
    cred = _provision(db, owner, _pool(db, owner))
    assert authenticate_machine(db, cred.raw_token) is not None

    si.revoke_sandbox_machine(db, machine_id=cred.machine_id, owner_id=owner)

    assert authenticate_machine(db, cred.raw_token) is None


def test_revoking_twice_is_a_no_op_not_an_error(db):
    owner = _user(db)
    cred = _provision(db, owner, _pool(db, owner))

    assert si.revoke_sandbox_machine(db, machine_id=cred.machine_id, owner_id=owner) is True
    assert si.revoke_sandbox_machine(db, machine_id=cred.machine_id, owner_id=owner) is False
    assert si.revoke_sandbox_machine(db, machine_id=cred.machine_id, owner_id=owner) is False

    assert _machine_row(db, cred.machine_id)["status"] == "revoked"
    assert authenticate_machine(db, cred.raw_token) is None


def test_revocation_is_total_even_when_a_previous_attempt_half_finished(db):
    """A crashed cleanup leaves a revoked row still bound to its pool. The
    next call must finish the job — "already revoked" cannot mean "stop, the
    bindings are someone else's problem now"."""
    owner = _user(db)
    pool = _pool(db, owner)
    cred = _provision(db, owner, pool)

    dbmod.revoke_machine_row(db, cred.machine_id, owner)  # status only
    assert dbmod.machine_ids_bound_to_pool(db, pool) == [cred.machine_id]

    assert si.revoke_sandbox_machine(db, machine_id=cred.machine_id, owner_id=owner) is False
    assert dbmod.machine_ids_bound_to_pool(db, pool) == []


def test_revocation_works_after_the_sandbox_is_already_gone(db):
    """Cleanup runs in a ``finally`` and the sandbox may have expired, been
    killed, or never come up. Revocation touches nothing outside this
    database, so a session already at TERMINATED with no external id revokes
    exactly as cleanly as a live one."""
    owner = _user(db)
    pool = _pool(db, owner)
    cred = _provision(db, owner, pool)

    session = ss.create_session(
        db,
        owner_id=owner,
        pool_id=pool,
        training_job_id="job-train-1",
        region="ap-southeast-1",
        template="flashnode-base",
        machine_id=cred.machine_id,
    )
    assert ss.transition(db, session["id"], "REQUESTED", "TERMINATED")
    assert ss.fetch_session(db, session["id"])["external_sandbox_id"] is None

    assert si.revoke_sandbox_machine(db, machine_id=cred.machine_id, owner_id=owner) is True
    assert authenticate_machine(db, cred.raw_token) is None


def test_a_stranger_cannot_revoke_someone_elses_machine(db):
    owner = _user(db)
    pool = _pool(db, owner)
    cred = _provision(db, owner, pool)
    stranger = _user(db)

    assert si.revoke_sandbox_machine(db, machine_id=cred.machine_id, owner_id=stranger) is False

    # Nothing moved: not the status, not the bindings, not the credential.
    assert _machine_row(db, cred.machine_id)["status"] == "active"
    assert dbmod.machine_ids_bound_to_pool(db, pool) == [cred.machine_id]
    assert authenticate_machine(db, cred.raw_token) is not None


def test_revoking_an_unknown_machine_is_false_not_an_error(db):
    assert (
        si.revoke_sandbox_machine(
            db, machine_id=str(uuid.uuid4()), owner_id=_user(db)
        )
        is False
    )


# ---------------------------------------------------------------------------
# Linkage to the session ledger
# ---------------------------------------------------------------------------


def test_the_machine_id_is_recorded_on_the_session_through_transition(db):
    """No new column and no second home for the fact: the ledger's own
    compare-and-set carries it, and its fill-in-only semantics mean a later
    transition that says nothing about the machine cannot erase it."""
    owner = _user(db)
    pool = _pool(db, owner)
    session = ss.create_session(
        db,
        owner_id=owner,
        pool_id=pool,
        training_job_id="job-train-1",
        region="ap-southeast-1",
        template="flashnode-base",
    )
    assert session["machine_id"] is None

    cred = _provision(db, owner, pool)
    assert ss.transition(
        db, session["id"], "REQUESTED", "ACTIVE", machine_id=cred.machine_id
    )

    assert str(ss.fetch_session(db, session["id"])["machine_id"]) == cred.machine_id

    # Fill-in-only: a later move that mentions no machine leaves it alone.
    assert ss.transition(db, session["id"], "ACTIVE", "PREPARED")
    assert str(ss.fetch_session(db, session["id"])["machine_id"]) == cred.machine_id


# ---------------------------------------------------------------------------
# Renting: the sibling path, and what it deliberately does not assert
#
# `provision_rented_machine` mints the identity of a GPU the operator rented
# into the user's OWN workspace. Everything `provision_sandbox_machine` does to
# authorise, serialise and mint is kept; the isolation assertion is not, and
# its absence is the feature (D6). The section after this one proves the
# assertion still holds where it belongs.
# ---------------------------------------------------------------------------


def test_a_rental_is_minted_into_a_pool_that_already_holds_a_machine(db):
    """**The refusal this decision existed to remove.**

    A submitter's workspace holds their laptop. Renting means adding a GPU to
    *that* workspace, so the pool is occupied by construction — and
    `provision_sandbox_machine`'s closing `assert_pool_isolated` refused it,
    which meant renting only ever worked into an empty pool.
    """
    owner = _user(db)
    pool = _pool(db, owner)
    laptop = str(
        dbmod.insert_machine(
            db, owner_id=owner, node_id="laptop-beside-a-rental",
            name="laptop", platform="linux",
        )
    )
    dbmod.bind_machine_pool(db, machine_id=laptop, pool_id=pool)

    cred = _rent(db, owner, pool, node_id="rented-alongside")

    assert sorted(dbmod.machine_ids_bound_to_pool(db, pool)) == sorted(
        [laptop, cred.machine_id]
    )
    # Bound AND stamped: `pool_ids_for_machine` is what the register proxy
    # copies onto the node, and a binding it ignores would be decoration.
    assert dbmod.pool_ids_for_machine(db, cred.machine_id) == [pool]
    assert _machine_row(db, cred.machine_id)["status"] == "active"


def test_two_rentals_can_serve_one_pool(db):
    """What `gpu_count > 1` needs. A fleet is several machines in one pool,
    and the isolation assertion allowed exactly one."""
    owner = _user(db)
    pool = _pool(db, owner)

    first = _rent(db, owner, pool, node_id="rented-1-of-2")
    second = _rent(db, owner, pool, node_id="rented-2-of-2")

    assert first.machine_id != second.machine_id
    assert first.raw_token != second.raw_token
    assert sorted(dbmod.machine_ids_bound_to_pool(db, pool)) == sorted(
        [first.machine_id, second.machine_id]
    )
    # Both stamped with the pool, so both are eligible claimants for its work.
    assert dbmod.pool_ids_for_machine(db, first.machine_id) == [pool]
    assert dbmod.pool_ids_for_machine(db, second.machine_id) == [pool]


def test_both_the_laptop_and_the_rental_may_claim_the_pools_work(db):
    """The positive statement, against the real placement gate rather than a
    restatement of it.

    `test_the_sessions_task_cannot_be_claimed_by_any_other_machine` notes that
    a second machine bound to a pool IS an eligible claimant, and treats that
    as the hazard an isolation pool exists to remove. For a rental it is the
    goal: "add more GPUs and the job finishes sooner" is exactly two nodes
    stamped with one pool, both eligible for the same task.
    """
    from flashruntime.scheduler import IsolationAwarePlacement

    owner = _user(db)
    pool = _pool(db, owner)
    laptop = str(
        dbmod.insert_machine(
            db, owner_id=owner, node_id="laptop-claimant", name=None,
            platform="linux",
        )
    )
    dbmod.bind_machine_pool(db, machine_id=laptop, pool_id=pool)
    cred = _rent(db, owner, pool)

    policy = IsolationAwarePlacement()
    for machine_id, node_id in ((laptop, "laptop-claimant"), (cred.machine_id, cred.node_id)):
        node = _node_view(node_id, dbmod.pool_ids_for_machine(db, machine_id))
        assert policy.eligible(_pool_task(pool), node) is True

    # And the boundary that does still hold: neither is eligible for another
    # team's pool job.
    other = str(uuid.uuid4())
    assert policy.eligible(
        _pool_task(other),
        _node_view(cred.node_id, dbmod.pool_ids_for_machine(db, cred.machine_id)),
    ) is False


# --- the lease ------------------------------------------------------------


def test_a_rental_is_minted_as_a_lease_and_a_laptop_as_a_deed(db):
    """A laptop's binding says whose machine this is, permanently. A rental's
    is true while we hold the hardware and false the moment we give it back,
    and `machines.lifecycle` is the only column that can say which."""
    owner = _user(db)
    pool = _pool(db, owner)

    laptop = str(
        dbmod.insert_machine(
            db, owner_id=owner, node_id="laptop-deed", name=None,
            platform="linux",
        )
    )
    rental = _rent(db, owner, pool)

    assert _machine_row(db, rental.machine_id)["lifecycle"] == "leased"
    assert si.LEASED_LIFECYCLE == "leased"
    assert _machine_row(db, laptop)["lifecycle"] == "persistent"
    # An evaluation sandbox is neither: its credential dies with its session's
    # own cleanup, not with a rental row.
    assert _machine_row(db, _provision(db, owner, _pool(db, owner)).machine_id)[
        "lifecycle"
    ] == "persistent"
    # And the venue is recorded rather than the Alibaba sandbox platform the
    # shared path used to hard-code.
    assert _machine_row(db, rental.machine_id)["platform"] == "runpod"


def test_no_heartbeat_timer_ever_expires_a_lease(db):
    """**Why `leased` is not `ephemeral`, stated as a guarantee.**

    `expire_stale_ephemeral_machines` measures from
    `coalesce(last_seen_at, created_at)`, and a rented host has NO
    `last_seen_at` until it has booted, pulled a multi-gigabyte image and
    enrolled — routinely longer than the fifteen-minute default. Filing
    rentals as `ephemeral` would revoke the credentials of machines that are
    still starting up, on hardware already paid for.

    Both machines below have never been seen and are two hours old — past any
    plausible window, and exactly the shape of a host mid-boot. Only the
    ephemeral one is revoked.

    The window is an hour rather than zero, and the assertions are membership
    rather than equality, because this sweep is GLOBAL and the test database
    outlives every file in the session: a zeroed window would revoke another
    file's rental session, and an equality would report on its rows.
    """
    owner = _user(db)
    pool = _pool(db, owner)
    leased = _rent(db, owner, pool)
    session = str(
        dbmod.insert_machine(
            db, owner_id=owner, node_id=f"rental-session-{uuid.uuid4()}",
            name=None, platform="runpod", lifecycle="ephemeral",
        )
    )
    dbmod.bind_machine_pool(db, machine_id=session, pool_id=_pool(db, owner))
    assert _machine_row(db, leased.machine_id)["last_seen_at"] is None
    with db.cursor() as cur:
        cur.execute(
            "update public.machines set created_at = now() - interval '2 hours'"
            " where id = any(%s)",
            ([leased.machine_id, session],),
        )

    expired = dbmod.expire_stale_ephemeral_machines(db, stale_seconds=3600.0)

    assert session in expired, "the sweep must actually be sweeping"
    assert leased.machine_id not in expired
    assert _machine_row(db, leased.machine_id)["status"] == "active"
    assert dbmod.pool_ids_bound_to_machine(db, leased.machine_id) == [pool]
    assert authenticate_machine(db, leased.raw_token) is not None


def test_a_returned_lease_leaves_the_owners_fleet_and_a_revoked_laptop_stays(db):
    """The lease is over, so the machine is not theirs to list any more.

    The owner's words: an identity we hand out "just stays in our account after
    we're done with the job". A revoked laptop is the opposite case and stays
    visible — its owner revoked it on purpose, and hiding it would hide the
    evidence that they did.
    """
    owner = _user(db)
    pool = _pool(db, owner)
    rental = _rent(db, owner, pool)
    laptop = str(
        dbmod.insert_machine(
            db, owner_id=owner, node_id="laptop-revoked-on-purpose",
            name=None, platform="linux",
        )
    )

    visible = {str(m["id"]) for m in dbmod.list_machines_for_owner(db, owner)}
    assert {rental.machine_id, laptop} <= visible

    si.revoke_sandbox_machine(db, machine_id=rental.machine_id, owner_id=owner)
    dbmod.revoke_machine_row(db, laptop, owner)

    visible = {str(m["id"]) for m in dbmod.list_machines_for_owner(db, owner)}
    assert rental.machine_id not in visible
    assert laptop in visible


def test_revoking_one_lease_leaves_the_pools_other_machines_alone(db):
    """Now that a pool may hold several machines, "revoke" has to mean this
    one. A rental ending must not unbind the submitter's laptop or the
    sibling GPU still working on the same job."""
    owner = _user(db)
    pool = _pool(db, owner)
    laptop = str(
        dbmod.insert_machine(
            db, owner_id=owner, node_id="laptop-untouched", name=None,
            platform="linux",
        )
    )
    dbmod.bind_machine_pool(db, machine_id=laptop, pool_id=pool)
    first = _rent(db, owner, pool, node_id="rented-ending")
    second = _rent(db, owner, pool, node_id="rented-still-working")

    assert si.revoke_sandbox_machine(
        db, machine_id=first.machine_id, owner_id=owner
    )

    assert _machine_row(db, first.machine_id)["status"] == "revoked"
    assert authenticate_machine(db, first.raw_token) is None
    assert sorted(dbmod.machine_ids_bound_to_pool(db, pool)) == sorted(
        [laptop, second.machine_id]
    )
    assert authenticate_machine(db, second.raw_token) is not None


# --- everything the sibling KEPT ------------------------------------------


def test_a_rented_token_authenticates_once_and_is_stored_only_as_a_digest(db):
    owner = _user(db)
    cred = _rent(db, owner, _pool(db, owner))

    machine = authenticate_machine(db, cred.raw_token)
    assert machine is not None
    assert str(machine.id) == cred.machine_id

    row = _machine_row(db, cred.machine_id)
    assert row["token_hash"] == hash_machine_token(cred.raw_token)
    assert row["token_prefix"] == cred.raw_token[: si.TOKEN_PREFIX_LENGTH]
    assert cred.raw_token not in repr(row)
    assert cred.raw_token not in repr(cred)


def test_a_stranger_cannot_rent_into_someone_elses_pool(db):
    """Same authorisation, same 404 shape. Dropping the isolation assertion
    dropped nothing about who may mint."""
    owner = _user(db)
    pool = _pool(db, owner)
    stranger = _user(db)

    with pytest.raises(si.PoolNotFound):
        _rent(db, stranger, pool, node_id="rented-stranger")

    assert dbmod.machine_ids_bound_to_pool(db, pool) == []


def test_a_plain_member_cannot_rent_into_a_pool_they_do_not_own(db):
    """Ownership AND membership, both still required. Membership is not
    belt-and-braces: `pool_ids_for_machine` intersects bindings with
    `pool_members`, so a machine minted into a pool its owner does not belong
    to is stamped with nothing and could never claim the job it was rented
    for."""
    owner = _user(db)
    pool = _pool(db, owner)
    member = _user(db)
    with db.cursor() as cur:
        cur.execute(
            "insert into public.pool_members (pool_id, user_id) values (%s, %s)",
            (pool, member),
        )
    assert dbmod.is_pool_member(db, pool, member) is True

    with pytest.raises(si.PoolNotFound):
        _rent(db, member, pool, node_id="rented-member")

    assert dbmod.machine_ids_bound_to_pool(db, pool) == []


def test_an_unknown_pool_is_refused_the_same_way_as_someone_elses_for_rentals(db):
    owner = _user(db)
    theirs = _pool(db, _user(db))

    with pytest.raises(si.PoolNotFound) as unknown:
        _rent(db, owner, str(uuid.uuid4()), node_id="rented-a")
    with pytest.raises(si.PoolNotFound) as forbidden:
        _rent(db, owner, theirs, node_id="rented-b")

    assert type(unknown.value) is type(forbidden.value)


def test_a_node_id_already_enrolled_is_refused_for_a_rental_too(db):
    """The column is globally unique so one machine cannot adopt another's
    identity, and a rental is not an exception. The whole mint is one
    transaction, so the refusal burns nothing."""
    owner = _user(db)
    pool = _pool(db, owner)
    _rent(db, owner, pool, node_id="rented-taken")

    with pytest.raises(NodeAlreadyEnrolled):
        _rent(db, owner, pool, node_id="rented-taken")

    # Exactly the first machine: the second attempt left no half-bound row.
    assert len(dbmod.machine_ids_bound_to_pool(db, pool)) == 1


def test_renting_serialises_on_the_pool_row(db, postgres_dsn):
    """The row lock is kept even though the isolation assertion is gone, and
    it is not vestigial: two acquisitions into one pool must not interleave
    their reads and writes of that pool's bindings, and a controller retrying
    a slow provision is the ordinary way to get two.

    Asserted by holding the lock and watching a mint fail to get in, rather
    than by racing two threads — a race that happens to finish in order passes
    whether the lock exists or not.
    """
    owner = _user(db)
    pool = _pool(db, owner)

    blocked = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    blocked.autocommit = True
    blocked.execute("set lock_timeout = '250ms'")
    try:
        with db.transaction():
            assert dbmod.lock_pool_for_owner(db, pool, owner) is not None
            with pytest.raises(psycopg.errors.LockNotAvailable):
                _rent(blocked, owner, pool, node_id="rented-blocked")

        with db.cursor() as cur:
            cur.execute(
                "select id from public.machines where node_id = %s",
                ("rented-blocked",),
            )
            assert cur.fetchone() is None

        cred = _rent(blocked, owner, pool, node_id="rented-after")
        assert dbmod.machine_ids_bound_to_pool(db, pool) == [cred.machine_id]
    finally:
        blocked.close()


# ---------------------------------------------------------------------------
# The invariant, in its new neighbourhood
#
# Relaxing isolation for rentals must not relax it for the thing it protects.
# These are the tests that fail if the sibling ever grows into a shared code
# path with a flag on it.
# ---------------------------------------------------------------------------


def test_an_evaluation_session_is_still_refused_a_pool_holding_a_rental(db):
    """The assertion is unchanged and this is the case that proves it in the
    world D6 created: a pool that now legitimately holds a rented machine is
    still not somewhere an evaluation sandbox may mint.

    The refusal is total — no machine row, no binding, no credential — because
    the assertion runs inside the same transaction as the insert.
    """
    owner = _user(db)
    pool = _pool(db, owner)
    rental = _rent(db, owner, pool, node_id="rented-sitting-tenant")

    with pytest.raises(si.PoolNotIsolated):
        _provision(db, owner, pool, node_id="fc-sandbox-refused")

    assert dbmod.machine_ids_bound_to_pool(db, pool) == [rental.machine_id]
    with db.cursor() as cur:
        cur.execute(
            "select id from public.machines where node_id = %s",
            ("fc-sandbox-refused",),
        )
        assert cur.fetchone() is None


def test_a_rental_arriving_beside_a_sandbox_breaks_the_sessions_invariant(db):
    """The other direction, and the reason `assert_pool_isolated` documents
    itself as safe to call again at any point in the session.

    Nothing stops `provision_rented_machine` being handed an isolation pool —
    it asserts nothing about the pool, by design. What catches it is the
    session's own re-check (`sandbox_orchestrator` calls it right after
    minting, and the boundary is claimed for the session's lifetime, not for
    the instant of minting). It must still fail closed.
    """
    owner = _user(db)
    pool = _pool(db, owner)
    sandbox = _provision(db, owner, pool)
    si.assert_pool_isolated(db, pool_id=pool, machine_id=sandbox.machine_id)

    _rent(db, owner, pool, node_id="rented-into-an-isolation-pool")

    with pytest.raises(si.PoolNotIsolated):
        si.assert_pool_isolated(db, pool_id=pool, machine_id=sandbox.machine_id)


def test_the_isolation_assertion_cannot_be_switched_off_by_an_argument(db):
    """D6's shape, pinned: a SEPARATE function, not a flag.

    A boolean can be passed wrongly, and the cost of passing it wrongly on the
    sandbox path is an evaluation session whose tasks a second machine can
    claim. Two names cannot be confused by a keyword argument. If this ever
    fails because somebody added `assert_isolated=True` to the sandbox path,
    the question to answer first is what happens the day a caller passes False.
    """
    import inspect

    sandbox = list(inspect.signature(si.provision_sandbox_machine).parameters)
    assert sandbox == ["db", "owner_id", "pool_id", "node_id", "label"]

    rented = list(inspect.signature(si.provision_rented_machine).parameters)
    assert "assert_pool_isolated" not in rented
    assert si.provision_rented_machine is not si.provision_sandbox_machine
    # Both are exported, so neither is the private half of the other.
    assert {"provision_sandbox_machine", "provision_rented_machine"} <= set(
        si.__all__
    )
    assert si.__all__ == sorted(si.__all__)
