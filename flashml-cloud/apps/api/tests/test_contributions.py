"""The credit ledger: who actually gets recorded for a round's accepted work.

Until this file existed, ``public.contributions`` was empty after a
successful five-round federated run — the schema was there, the acceptance
judgement was there in ``fedavg._contributors``, and nothing ever wrote a
row. No host could be credited for anything.

Two levels are tested here and both matter:

* ``dbmod.record_contributions`` on its own, against a real, freshly
  migrated Postgres (the ``postgres_dsn`` fixture) — because every property
  worth pinning is a property of the database. "Calling it twice writes no
  duplicates" is enforced by the unique index in ``0003``, not by anything
  in Python, so a mock would prove nothing about it.
* the driver end to end, so the wiring is covered too: a ledger function
  nobody calls is exactly the bug this file exists to fix.

Node ids are namespaced per test. ``machines.node_id`` is globally unique,
so two tests both enrolling a machine called ``node-0`` would collide on the
schema rather than on anything under test.
"""
from __future__ import annotations

import uuid

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import fedavg as fedavgmod

from flashml_cloud_api.images import resolve_image

from test_federated import (  # the proven driver harness
    FEDERATED_YAML,
    StubCoordinator,
    _config,
    _connect_factory,
    _run,
    _seed_job,
)
from test_jobs_from_repo import (
    _new_user,
    db,  # noqa: F401 - fixture
    settings,  # noqa: F401 - fixture
)

RUN_MARKER = uuid.uuid4().hex[:8]


def _node_id(tag: str) -> str:
    """A node id no other test can collide with on ``machines.node_id``."""
    return f"node-{RUN_MARKER}-{tag}-{uuid.uuid4().hex[:6]}"


def _enrol(db, node_id: str) -> str:
    """Give ``node_id`` a cloud enrolment and return its machine id."""
    owner = _new_user(db)
    return dbmod.insert_machine(
        db, owner_id=owner, node_id=node_id, name="laptop", platform="linux"
    )


def _rows(db, job_id: str) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "select machine_id, job_id, task_id, duration_s"
            "  from public.contributions where job_id = %s"
            " order by task_id",
            (job_id,),
        )
        return list(cur.fetchall())


def _job() -> str:
    return f"cjob-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# 1. record_contributions against real Postgres
# ---------------------------------------------------------------------------


def test_one_row_per_accepted_contributor_resolved_to_a_machine(db):
    node_a, node_b = _node_id("a"), _node_id("b")
    machine_a, machine_b = _enrol(db, node_a), _enrol(db, node_b)
    job_id = _job()

    dbmod.record_contributions(
        db,
        job_id=job_id,
        entries=[
            {"node_id": node_a, "task_id": "task-000", "duration_s": 12.5},
            {"node_id": node_b, "task_id": "task-001", "duration_s": 30.0},
        ],
    )

    rows = _rows(db, job_id)
    assert [r["task_id"] for r in rows] == ["task-000", "task-001"]
    # The node id is the coordinator's name for the machine; the ledger is
    # keyed by the cloud's, which is the only id an owner can be paid on.
    assert [str(r["machine_id"]) for r in rows] == [str(machine_a), str(machine_b)]
    assert [float(r["duration_s"]) for r in rows] == [12.5, 30.0]


def test_calling_it_twice_writes_no_duplicates(db):
    """The property migration 0003 exists for, asserted explicitly.

    A round callback can be retried and a driver can be restarted onto a run
    whose rounds are already recorded. If that appended a second credit row
    the ledger would drift upward silently, with no way afterwards to tell a
    real contribution from a replayed one.
    """
    node = _node_id("twice")
    _enrol(db, node)
    job_id = _job()
    entries = [{"node_id": node, "task_id": "task-000", "duration_s": 5.0}]

    dbmod.record_contributions(db, job_id=job_id, entries=entries)
    dbmod.record_contributions(db, job_id=job_id, entries=entries)

    assert len(_rows(db, job_id)) == 1


def test_a_null_task_id_is_also_written_only_once(db):
    """``coalesce(task_id, '')`` in the index, tested rather than trusted.

    NULLs never collide with each other in a plain unique index, so without
    the coalesce this is the one shape that could be appended without bound.
    """
    node = _node_id("nulltask")
    _enrol(db, node)
    job_id = _job()
    entries = [{"node_id": node, "task_id": None, "duration_s": None}]

    dbmod.record_contributions(db, job_id=job_id, entries=entries)
    dbmod.record_contributions(db, job_id=job_id, entries=entries)

    rows = _rows(db, job_id)
    assert len(rows) == 1
    assert rows[0]["task_id"] is None


def test_a_node_with_no_machine_row_is_skipped_and_the_call_succeeds(db):
    """A node registered against a self-hosted coordinator has no cloud
    enrolment. That is a valid deployment, not an error: raising here would
    fail a round for a machine that is behaving exactly as designed."""
    known = _node_id("known")
    machine = _enrol(db, known)
    job_id = _job()

    dbmod.record_contributions(
        db,
        job_id=job_id,
        entries=[
            {"node_id": known, "task_id": "task-000", "duration_s": 1.0},
            {"node_id": _node_id("selfhosted"), "task_id": "task-001",
             "duration_s": 2.0},
        ],
    )

    rows = _rows(db, job_id)
    assert len(rows) == 1
    assert str(rows[0]["machine_id"]) == str(machine)
    assert rows[0]["task_id"] == "task-000"


def test_no_entry_resolves_and_the_call_still_succeeds(db):
    job_id = _job()
    dbmod.record_contributions(
        db,
        job_id=job_id,
        entries=[{"node_id": _node_id("nobody"), "task_id": "task-000",
                  "duration_s": 1.0}],
    )
    assert _rows(db, job_id) == []


def test_an_empty_entry_list_is_a_no_op_not_an_error(db):
    job_id = _job()
    dbmod.record_contributions(db, job_id=job_id, entries=[])
    assert _rows(db, job_id) == []


# ---------------------------------------------------------------------------
# 2. one resolve query, however many entries
# ---------------------------------------------------------------------------


class _CountingCursor:
    def __init__(self, cursor, statements: list[str]):
        self._cursor = cursor
        self._statements = statements

    def execute(self, query, *args, **kwargs):
        self._statements.append(str(query))
        return self._cursor.execute(query, *args, **kwargs)

    def executemany(self, query, *args, **kwargs):
        self._statements.append(str(query))
        return self._cursor.executemany(query, *args, **kwargs)

    def __enter__(self):
        self._cursor.__enter__()
        return self

    def __exit__(self, *exc):
        return self._cursor.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _CountingConnection:
    """Records every statement issued through this connection."""

    def __init__(self, conn):
        self._conn = conn
        self.statements: list[str] = []

    def cursor(self, *args, **kwargs):
        return _CountingCursor(self._conn.cursor(*args, **kwargs), self.statements)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _statements_for(db, count: int) -> list[str]:
    entries = []
    for i in range(count):
        node = _node_id(f"n{i}")
        _enrol(db, node)
        entries.append({"node_id": node, "task_id": f"task-{i:03d}",
                        "duration_s": 1.0})
    counting = _CountingConnection(db)
    dbmod.record_contributions(counting, job_id=_job(), entries=entries)
    return counting.statements


def test_node_ids_are_resolved_in_one_query_not_one_per_row(db):
    """Per-row resolution is a round trip per contributor per round. It is
    also the shape that quietly becomes O(n) database load the moment the
    pool grows, which is precisely when it is hardest to notice."""
    two = _statements_for(db, 2)
    ten = _statements_for(db, 10)

    assert sum("public.machines" in s for s in two) == 1
    assert sum("public.machines" in s for s in ten) == 1
    # The whole write does not grow with the number of contributors either.
    assert len(ten) == len(two)


# ---------------------------------------------------------------------------
# 3. the driver: rounds actually credit their contributors
# ---------------------------------------------------------------------------


class NamedNodeCoordinator(StubCoordinator):
    """``StubCoordinator`` with the node ids (and task states) named by the
    test, so a test can enrol exactly the machines it expects credited.

    Its round job ids are namespaced too. ``contributions.job_id`` is the
    round's coordinator job, and every ``StubCoordinator`` calls its first
    round ``cjob-000`` — so without this, one test would read another test's
    credit rows out of the shared database and call them its own.
    """

    def __init__(self, node_ids: list[str], states: list[str] | None = None):
        super().__init__()
        self.node_ids = node_ids
        self.states = states or ["COMPLETED"] * len(node_ids)
        self.shards = len(node_ids)
        self._prefix = f"cjob-{uuid.uuid4().hex[:10]}"

    def round_job_id(self, round_index: int) -> str:
        return f"{self._prefix}-{round_index:03d}"

    def submit(self, body):
        if self.fail_on_submit:
            raise RuntimeError("coordinator unavailable")
        self.submitted.append(body)
        return {"job_id": self.round_job_id(len(self.submitted) - 1)}

    def tasks(self, job_id):
        return [
            {"task_id": f"task-{i:03d}", "state": state, "node_id": node_id}
            for i, (node_id, state) in enumerate(zip(self.node_ids, self.states))
        ]


def test_a_completed_round_credits_every_accepted_contributor(
    db, settings, postgres_dsn
):
    nodes = [_node_id("r0"), _node_id("r1")]
    machines = [_enrol(db, n) for n in nodes]
    owner = _new_user(db)
    job_id = _seed_job(db, owner)
    coordinator = NamedNodeCoordinator(nodes)

    _run(job_id, settings, postgres_dsn, coordinator, rounds=1)

    rows = _rows(db, coordinator.round_job_id(0))
    assert [r["task_id"] for r in rows] == ["task-000", "task-001"]
    assert [str(r["machine_id"]) for r in rows] == [str(m) for m in machines]
    # The coordinator's task view reports no duration, and the ledger says so
    # rather than inventing one.
    assert [r["duration_s"] for r in rows] == [None, None]


def test_each_round_is_credited_separately(db, settings, postgres_dsn):
    """Credit is keyed on the *round's* coordinator job, not the parent run.

    Keyed on the parent ``fed-`` id instead, round 1's ``task-000`` would
    collide with round 0's under the unique index and only the first round of
    a five-round run would ever be paid.
    """
    nodes = [_node_id("m0"), _node_id("m1")]
    _enrol(db, nodes[0])
    _enrol(db, nodes[1])
    owner = _new_user(db)
    job_id = _seed_job(db, owner)

    coordinator = NamedNodeCoordinator(nodes)
    _run(job_id, settings, postgres_dsn, coordinator, rounds=2)

    assert len(_rows(db, coordinator.round_job_id(0))) == 2
    assert len(_rows(db, coordinator.round_job_id(1))) == 2


def _run_with_quorum_of_one(job_id, settings, postgres_dsn, coordinator):
    """``test_federated._run`` with ``min_participants: 1``.

    Needed by the failed-task test and nowhere else: the shared harness asks
    for a quorum of two, and a round in which one of two tasks failed can
    never reach it — the driver would sit in its quorum poll until the round
    timeout rather than reaching the ledger at all.
    """
    config = _config(
        FEDERATED_YAML.replace("rounds: 3", "rounds: 1")
                      .replace("min_participants: 2", "min_participants: 1")
    )
    fedavgmod.run_federated_job(
        fedavgmod.FederatedRun(
            job_id=job_id,
            job_name="acme-fed",
            config=config,
            image=resolve_image("python-slim"),
            code_artifact_uri="artifact://uploads/deadbeef/code.tar.gz",
        ),
        settings=settings,
        connect=_connect_factory(postgres_dsn),
        coordinator_factory=lambda _s: coordinator,
    )


def test_a_node_that_failed_its_task_is_not_credited(db, settings, postgres_dsn):
    """Accepted work, never attempted — AGENTS.md hard rule 4, at the ledger.

    The coordinator's task view still names the last node that held a task
    that failed and was retried elsewhere. Crediting it would pay a machine
    for work the system threw away.
    """
    accepted, attempted = _node_id("ok"), _node_id("failed")
    machine = _enrol(db, accepted)
    _enrol(db, attempted)
    owner = _new_user(db)
    job_id = _seed_job(db, owner)

    coordinator = NamedNodeCoordinator(
        [accepted, attempted], states=["COMPLETED", "FAILED"]
    )
    _run_with_quorum_of_one(job_id, settings, postgres_dsn, coordinator)

    rows = _rows(db, coordinator.round_job_id(0))
    assert [str(r["machine_id"]) for r in rows] == [str(machine)]


def test_a_self_hosted_pool_records_the_round_and_credits_nobody(
    db, settings, postgres_dsn
):
    """Nothing about the round changes when no node has a cloud enrolment."""
    nodes = [_node_id("sh0"), _node_id("sh1")]
    owner = _new_user(db)
    job_id = _seed_job(db, owner)

    coordinator = NamedNodeCoordinator(nodes)
    _run(job_id, settings, postgres_dsn, coordinator, rounds=1)

    assert _rows(db, coordinator.round_job_id(0)) == []
    rounds = dbmod.list_job_rounds_for_owner(db, job_id, owner)
    assert [r["round"] for r in rounds] == [0]
    assert dbmod.fetch_job_for_owner(db, job_id, owner)["status"] == "SUCCEEDED"


def test_a_credit_write_that_fails_never_fails_the_round(
    db, settings, postgres_dsn, monkeypatch
):
    """A round that already aggregated must not be thrown away because a
    credit row could not be written — the same rule ``_contributors`` holds
    itself to, for the same reason."""
    nodes = [_node_id("boom0"), _node_id("boom1")]
    _enrol(db, nodes[0])
    _enrol(db, nodes[1])
    owner = _new_user(db)
    job_id = _seed_job(db, owner)

    def explode(*args, **kwargs):
        raise RuntimeError("ledger unavailable")

    coordinator = NamedNodeCoordinator(nodes)
    monkeypatch.setattr(fedavgmod.dbmod, "record_contributions", explode)
    _run(job_id, settings, postgres_dsn, coordinator, rounds=1)

    rounds = dbmod.list_job_rounds_for_owner(db, job_id, owner)
    assert [r["round"] for r in rounds] == [0]
    assert rounds[0]["contributors"] == nodes
    assert dbmod.fetch_job_for_owner(db, job_id, owner)["status"] == "SUCCEEDED"
    assert _rows(db, coordinator.round_job_id(0)) == []


def test_the_round_row_still_records_the_contributor_node_ids(
    db, settings, postgres_dsn
):
    """``job_rounds.contributors`` is unchanged by any of this: it is the
    provenance the console renders, and it names nodes, not machines."""
    nodes = [_node_id("prov0"), _node_id("prov1")]
    owner = _new_user(db)
    job_id = _seed_job(db, owner)

    _run(job_id, settings, postgres_dsn, NamedNodeCoordinator(nodes), rounds=1)

    rounds = dbmod.list_job_rounds_for_owner(db, job_id, owner)
    assert rounds[0]["contributors"] == nodes
