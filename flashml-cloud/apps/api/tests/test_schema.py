# apps/api/tests/test_schema.py
"""The migration file is the reviewable artifact; these tests pin the
invariants that matter rather than re-describing every column."""
import pathlib
import re
import uuid

import psycopg
import pytest

from flashml_cloud_api import db as dbmod

# One test below asserts against a real, freshly-migrated database rather
# than against the SQL text: a column list read out of `information_schema`
# is the only check that proves the migration *applies*, not merely that it
# was typed. The `db` fixture lives in `test_jobs_from_repo` (it is the
# session Postgres from `conftest.py` wrapped in a dict-row connection);
# `test_contributions.py` already borrows it the same way.
from test_jobs_from_repo import db  # noqa: F401 - fixture

MIGRATIONS = pathlib.Path(__file__).parent.parent / "migrations"

SQL = (MIGRATIONS / "0001_initial.sql").read_text()

#: Every migration concatenated. The deny-all-by-default RLS property is a
#: property of the *schema*, not of one file — a later migration that added
#: a permissive policy would leave the 0001 checks below passing while the
#: database was wide open, so the policy check runs over all of them.
ALL_SQL = "\n".join(p.read_text() for p in sorted(MIGRATIONS.glob("*.sql")))

TABLES = ["profiles", "machines", "device_codes", "jobs", "contributions"]

ALL_TABLES = TABLES + [
    "job_rounds", "pools", "pool_members", "pool_invites", "machine_pools",
    "sandbox_sessions", "sandbox_events", "credit_requests", "rented_capacity",
    "agent_principals",
]


def test_every_table_is_created():
    for t in TABLES:
        assert re.search(rf"create table\s+(if not exists\s+)?public\.{t}\b", SQL, re.I), t


def test_rls_is_enabled_on_every_table():
    for t in TABLES:
        assert re.search(rf"alter table\s+public\.{t}\s+enable row level security", SQL, re.I), t


def test_no_policy_grants_anon_or_authenticated():
    """Database access is API-only. A browser holding a valid JWT must not be
    able to read Postgres directly — every read goes through the API, which
    filters on owner_id. A policy naming these roles would silently open that."""
    for role in ("anon", "authenticated"):
        assert not re.search(
            rf"create policy.*\bto\s+{role}\b", ALL_SQL, re.I | re.S
        ), role


def test_no_migration_creates_any_policy_at_all():
    """Stronger than the role check next door, and deliberately so: RLS with
    *zero* policies is what denies every role but the owner and BYPASSRLS.
    A policy with any other role name — or one written without a `to` clause,
    which defaults to `public` — reopens direct access just as effectively."""
    assert not re.search(r"\bcreate\s+policy\b", ALL_SQL, re.I)


def test_rls_is_enabled_on_every_table_in_every_migration():
    for t in ALL_TABLES:
        assert re.search(
            rf"alter table\s+public\.{t}\s+enable row level security",
            ALL_SQL, re.I,
        ), t


def test_job_rounds_is_owned_by_a_job():
    """Rounds have no owner column of their own; ownership is the job's, via
    this FK, which is what makes the owner-scoped listing a join rather than
    a filter the API is trusted to remember."""
    assert re.search(
        r"job_id\s+text\s+not null\s+references\s+public\.jobs\(id\)"
        r"\s+on delete cascade",
        ALL_SQL, re.I,
    )


def test_a_round_can_only_be_recorded_once():
    """`on_round` fires after a round is aggregated and durable. A driver
    resumed onto a run whose history is already written must not append a
    second, contradictory curve."""
    assert re.search(r"unique\s*\(\s*job_id\s*,\s*round\s*\)", ALL_SQL, re.I)


def test_machines_store_a_hash_not_a_token():
    assert "token_hash" in SQL
    assert not re.search(r"\btoken\s+text", SQL, re.I), "raw token column present"


def test_node_id_is_unique():
    assert re.search(r"node_id\s+text\s+(not null\s+)?unique", SQL, re.I)


def test_machine_status_is_constrained():
    assert re.search(r"status.*check.*pending.*active.*revoked", SQL, re.I | re.S)


def test_machine_lifecycle_admits_leased_and_still_refuses_anything_else(db):
    """0023 widened `machines.lifecycle` to accept `'leased'`, and nothing
    asserted the widening directly — it was proved only by rentals inserting
    successfully somewhere else, which is a test of `provision_rented_machine`
    that happens to touch this constraint.

    Both halves matter and they fail differently. If the migration had not
    applied, every rental would be refused at mint time and the failure would
    at least be loud. If the constraint had been dropped rather than widened —
    `drop constraint if exists` runs first, and a typo in the `add` after it is
    a legal file — nothing would refuse a lifecycle at all, and a value no
    sweep in this schema selects would be writable: `expire_stale_ephemeral_
    machines` reads `'ephemeral'`, `reconcile.orphaned_leases` reads
    `'leased'`, and a machine filed as neither is a credential nothing ever
    ends. That failure is silent, which is why this asserts the refusal too.

    Against the real database rather than the SQL text, for the reason given
    at the top of this file: only an insert proves the migration *applied*.
    `test_machine_status_is_constrained` is the same invariant one column
    over.
    """
    owner = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id, email) values (%s::uuid, %s)",
            (owner, f"{owner}@example.test"),
        )
        cur.execute("insert into public.profiles (id) values (%s::uuid)", (owner,))

    def _insert(lifecycle: str) -> None:
        db.execute(
            "insert into public.machines (owner_id, node_id, lifecycle)"
            " values (%s::uuid, %s, %s)",
            (owner, f"node-{lifecycle}-{owner[:8]}", lifecycle),
        )

    try:
        # The three the schema knows, all still accepted: 0023 REPLACED the
        # constraint, so a widening that dropped the two 0020 values would be
        # just as broken as one that never added the third.
        for lifecycle in ("persistent", "ephemeral", "leased"):
            _insert(lifecycle)

        with db.cursor() as cur:
            cur.execute(
                "select lifecycle from public.machines where owner_id = %s::uuid"
                " order by lifecycle",
                (owner,),
            )
            assert [r["lifecycle"] for r in cur.fetchall()] == [
                "ephemeral", "leased", "persistent",
            ]

        # ...and nothing else. 'rented' is the plausible near-miss — it is what
        # the feature is called everywhere except in this column.
        for garbage in ("rented", "Leased", "leased "):
            with pytest.raises(psycopg.errors.CheckViolation):
                with db.transaction():
                    _insert(garbage)
    finally:
        with db.cursor() as cur:
            cur.execute("delete from auth.users where id = %s::uuid", (owner,))


def test_owner_columns_cascade_from_profiles():
    assert SQL.lower().count("references public.profiles(id)") >= 2


def test_job_rounds_records_what_the_aggregation_clipped(db):
    """Bounded influence is RECORDED, never enforced (design spec §4): no
    machine is quarantined, no credit withheld, no lease refused. So this
    column *is* the mechanism as far as this repo is concerned, and a
    migration that failed to apply would leave the whole feature silently
    doing nothing.

    `not null default '[]'` because every round has an answer to "what did
    the cap bind?", and on an honest round — which is every round unless
    somebody tried — that answer is the empty list. A nullable column would
    make every reader distinguish two kinds of nothing.
    """
    with db.cursor() as cur:
        cur.execute(
            "select data_type, is_nullable, column_default"
            "  from information_schema.columns"
            " where table_schema = 'public' and table_name = 'job_rounds'"
            "   and column_name = 'clipped'"
        )
        row = cur.fetchone()
    assert row is not None, "migration 0005 did not apply"
    assert row["data_type"] == "jsonb"
    assert row["is_nullable"] == "NO"
    assert row["column_default"] == "'[]'::jsonb"


def test_attempts_table_exists_with_rls(db):
    """The attempt ledger: the API's durable lease -> (job, task) mapping,
    with a terminal outcome since 0015.

    Without it the API can see that a completion was ACCEPTED but not what
    was completed, because the coordinator's complete response carries only
    `{"accepted": bool}`.
    """
    with db.cursor() as cur:
        cur.execute(
            "select column_name, data_type from information_schema.columns"
            " where table_schema = 'public' and table_name = 'attempts'"
            " order by column_name"
        )
        cols = {r["column_name"]: r["data_type"] for r in cur.fetchall()}
    assert cols == {
        "accepted_at": "timestamp with time zone",
        "claimed_at": "timestamp with time zone",
        # 0026: the thread this lease belongs to, or null. Inherited from the
        # job in `record_attempt`'s own INSERT — never minted here, and null
        # when the coordinator job id names no row this database holds.
        "correlation_id": "uuid",
        "job_id": "text",
        "lease_deadline": "timestamp with time zone",
        "lease_id": "text",
        "machine_id": "uuid",
        "outcome": "text",
        "resolved_at": "timestamp with time zone",
        "task_id": "text",
    }

    with db.cursor() as cur:
        cur.execute(
            "select relrowsecurity from pg_class"
            " where oid = 'public.attempts'::regclass"
        )
        assert cur.fetchone()["relrowsecurity"] is True


def test_an_attempt_outcome_is_constrained_to_the_four_the_ledger_knows(db):
    """Constrained in the database, the same discipline as `machines.status`
    in 0001 and `sandbox_sessions.state` in 0014: a bug in `db.py` must not be
    able to invent a fifth terminal state that every later reader then has to
    guess the meaning of.

    `abandoned` is in the list and nothing writes it — the coordinator can
    tell an abandoned lease from an expired one and this API cannot, so the
    value is reserved rather than manufactured. It is checked here so that the
    day the distinction becomes visible, the vocabulary is already there.
    """
    match = re.search(
        r"attempts_outcome_check\s+check\s*\(outcome in \(([^)]*)\)", ALL_SQL, re.I
    )
    assert match, "attempts.outcome is not constrained at all"
    assert set(re.findall(r"'(\w+)'", match.group(1))) == {
        "accepted", "failed", "expired", "abandoned",
    }


def test_an_unresolved_attempt_is_storable_and_is_not_a_failure(db):
    """NULL is a legal outcome and it is the one every reader has to handle.

    An attempt in flight, and an attempt claimed before 0015, both have no
    terminal state on record. The column must accept that rather than force a
    writer to pick a bucket — bucketing them is precisely the manufactured
    number this migration exists to prevent.
    """
    with db.cursor() as cur:
        cur.execute(
            "select is_nullable from information_schema.columns"
            " where table_schema = 'public' and table_name = 'attempts'"
            "   and column_name in ('outcome', 'resolved_at', 'lease_deadline')"
        )
        assert {r["is_nullable"] for r in cur.fetchall()} == {"YES"}


def test_the_expiry_reconciler_has_an_index_on_exactly_its_predicate(db):
    """PARTIAL, on unresolved rows only. The reconciler runs on a page a
    console polls, so after the first pass it has to be a statement that
    matches nothing cheaply; an index over the whole history would grow with
    every attempt ever made and be scanned for the few that are open."""
    with db.cursor() as cur:
        cur.execute(
            "select indexdef from pg_indexes"
            " where schemaname = 'public' and tablename = 'attempts'"
            "   and indexname = 'attempts_unresolved_deadline_idx'"
        )
        row = cur.fetchone()
    assert row is not None, "migration 0015 did not apply"
    definition = row["indexdef"].lower()
    assert "where (resolved_at is null)" in definition, definition
    assert "lease_deadline" in definition, definition


def test_a_job_records_when_its_artifacts_were_mirrored(db):
    """Migration 0016, checked against the applied schema.

    The mirror's once-only guard IS this column: `app._mirror_job_artifacts`
    is entered only for a job whose `artifacts_mirrored_at` is null, so a
    migration that failed to apply would either take the job route down or —
    if the read were made forgiving — copy every finished job to OSS again on
    every two-second poll.

    Nullable and with no default, deliberately. NULL is the answer for every
    pre-0016 job, for every deployment with no OSS configured, and for a
    mirror that failed and is owed a retry; a default would assert one of
    those three about all of them.
    """
    with db.cursor() as cur:
        cur.execute(
            "select is_nullable, column_default, data_type"
            "  from information_schema.columns"
            " where table_schema = 'public' and table_name = 'jobs'"
            "   and column_name = 'artifacts_mirrored_at'"
        )
        row = cur.fetchone()
    assert row is not None, "migration 0016 did not apply"
    assert row["is_nullable"] == "YES"
    assert row["column_default"] is None
    assert row["data_type"] == "timestamp with time zone"


def test_the_mirror_marker_is_not_the_footprint_marker(db):
    """Two columns, and 0016 exists because they cannot be one.

    `artifact_bytes_recorded_at` is stamped by the footprint measurement AND
    by `DELETE /jobs/{id}/artifacts`, neither of which copies a byte to OSS.
    Collapsing the two would record a mirror for every job anybody deleted,
    and — the damaging direction — would hide a mirror that FAILED behind a
    footprint call that succeeded on the same poll, so the copy would never
    be retried and that job's model would stay on a disk a redeploy erases.
    """
    with db.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns"
            " where table_schema = 'public' and table_name = 'jobs'"
            "   and column_name in "
            "       ('artifact_bytes_recorded_at', 'artifacts_mirrored_at')"
        )
        columns = {r["column_name"] for r in cur.fetchall()}
    assert columns == {"artifact_bytes_recorded_at", "artifacts_mirrored_at"}


def test_verifications_table_exists_with_rls(db):
    """The verification ledger. Nothing is enforced from it (design spec
    §5), so — exactly as with `job_rounds.clipped` — this table *is* the
    feature, and a migration that failed to apply would leave the whole
    verification layer silently doing nothing at all."""
    with db.cursor() as cur:
        cur.execute(
            "select column_name, data_type, is_nullable"
            "  from information_schema.columns"
            " where table_schema = 'public' and table_name = 'verifications'"
            " order by column_name"
        )
        cols = {r["column_name"]: r for r in cur.fetchall()}
    assert {c: r["data_type"] for c, r in cols.items()} == {
        "created_at": "timestamp with time zone",
        "detail": "jsonb",
        "id": "uuid",
        "job_id": "text",
        "machine_id": "uuid",
        "slice": "text",
        "task_id": "text",
        "verdict": "text",
    }
    # A redundancy mismatch is about a PAIR and names neither member as the
    # liar (§8.5), so a row may legitimately blame no machine.
    assert cols["machine_id"]["is_nullable"] == "YES"
    # ...but never "we do not know whose task this was", or "no verdict".
    for column in ("job_id", "task_id", "slice", "verdict", "detail"):
        assert cols[column]["is_nullable"] == "NO", column

    with db.cursor() as cur:
        cur.execute(
            "select relrowsecurity from pg_class"
            " where oid = 'public.verifications'::regclass"
        )
        assert cur.fetchone()["relrowsecurity"] is True


def test_unknown_is_one_of_the_three_verdicts_the_schema_allows():
    """`unknown` must be storable as itself. If the constraint listed only
    `pass` and `flag`, every "we could not tell" would have to be squeezed
    into one of them — and the one it would be squeezed into is `pass`."""
    match = re.search(
        r"verdict\s+text\s+not null\s+check\s*\(([^)]*)\)", ALL_SQL, re.I
    )
    assert match, "verifications.verdict is not constrained at all"
    allowed = set(re.findall(r"'(\w+)'", match.group(1)))
    assert allowed == {"pass", "flag", "unknown"}


def test_cli_credentials_table_exists_with_the_expected_shape(postgres_dsn):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(postgres_dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select column_name, is_nullable
                  from information_schema.columns
                 where table_schema = 'public'
                   and table_name = 'cli_credentials'
                """
            )
            cols = {r["column_name"]: r["is_nullable"] for r in cur.fetchall()}

    assert cols["id"] == "NO"
    assert cols["owner_id"] == "NO"
    assert cols["token_hash"] == "NO"
    assert cols["token_prefix"] == "NO"
    assert cols["status"] == "NO"
    assert cols["label"] == "YES"
    assert cols["last_used_at"] == "YES"
    assert cols["revoked_at"] == "YES"


def test_device_codes_carries_a_kind_defaulting_to_machine(postgres_dsn):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(postgres_dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select column_name, is_nullable, column_default
                  from information_schema.columns
                 where table_schema = 'public'
                   and table_name = 'device_codes'
                   and column_name in ('kind', 'node_id', 'credential_id')
                """
            )
            cols = {r["column_name"]: r for r in cur.fetchall()}

    assert cols["kind"]["is_nullable"] == "NO"
    assert "machine" in (cols["kind"]["column_default"] or "")
    # Relaxed so a CLI code, which has no node, can be inserted at all.
    assert cols["node_id"]["is_nullable"] == "YES"
    assert cols["credential_id"]["is_nullable"] == "YES"


def test_sandbox_sessions_stores_no_credential_of_any_kind(db):
    """0014's central promise, checked against the applied schema rather than
    against the file that was typed.

    A sandbox reaches this system as an ordinary machine — hashed `fmk_` token
    in `public.machines` (0001) — and reaches OSS through presigned URLs that
    expire on their own (spec D5). A token, key or signed URL parked here
    would recreate exactly the liability the GitHub App design removes: a
    table whose leak grants somebody something. See AGENTS.md, "Nothing stored
    is a credential".
    """
    with db.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns"
            " where table_schema = 'public'"
            "   and table_name in ('sandbox_sessions', 'sandbox_events')"
        )
        columns = {r["column_name"] for r in cur.fetchall()}

    assert columns, "migration 0014 did not apply"
    for forbidden in ("token", "token_hash", "api_key", "access_key",
                      "secret", "credential", "password", "signed_url"):
        assert forbidden not in columns, forbidden
    # share_token is the one bearer value on the table and it is a capability
    # for a deliberately public, read-only view — not a credential for
    # anything the holder could act with.
    assert "share_token" in columns


def test_a_sandbox_session_cannot_be_written_in_an_invented_state(db):
    """Constrained in the database so a bug in `sandbox_sessions.py` cannot
    write a state the state machine has never heard of — the same discipline
    as `machines.status` in 0001."""
    match = re.search(
        r"state\s+text\s+not null\s+check\s*\(state in \(([^)]*)\)", ALL_SQL, re.I
    )
    assert match, "sandbox_sessions.state is not constrained at all"
    allowed = set(re.findall(r"'(\w+)'", match.group(1)))
    assert allowed == {
        "REQUESTED", "ACTIVE", "PREPARED", "HIBERNATED", "RESUMING",
        "EVALUATING", "SUCCEEDED", "FAILED", "TERMINATED",
    }


def test_an_event_belongs_to_one_session_and_one_sequence(db):
    """Append-only and idempotent by schema, the same argument 0003 makes for
    the credit ledger: a controller retrying after a lost round trip must
    produce one row, and that has to be a property of the table rather than of
    the caller's discipline."""
    with db.cursor() as cur:
        cur.execute(
            "select conname, pg_get_constraintdef(oid) as def"
            "  from pg_constraint"
            " where conrelid = 'public.sandbox_events'::regclass"
            "   and contype = 'u'"
        )
        uniques = [r["def"].replace('"', "") for r in cur.fetchall()]
    assert any("(session_id, sequence)" in d for d in uniques), uniques


def test_a_sandbox_session_survives_losing_its_machine(db):
    """`on delete set null`, not cascade. A session whose machine row was
    deleted still holds a live `external_sandbox_id` that has to be woken and
    killed; cascading would delete the only record that says so, turning a
    lost pointer into a leaked, billing sandbox nobody can find."""
    with db.cursor() as cur:
        cur.execute(
            "select confdeltype from pg_constraint"
            " where conrelid = 'public.sandbox_sessions'::regclass"
            "   and contype = 'f'"
            "   and confrelid = 'public.machines'::regclass"
        )
        row = cur.fetchone()
    assert row is not None, "sandbox_sessions has no machine foreign key"
    assert row["confdeltype"] == "n", "expected ON DELETE SET NULL"


def test_a_machine_device_code_still_requires_a_node_id(postgres_dsn):
    """The check constraint that keeps relaxing node_id from weakening the
    machine flow: only kind='cli' may omit it."""
    import psycopg
    from datetime import datetime, timedelta, timezone

    expires = datetime.now(timezone.utc) + timedelta(minutes=10)
    with psycopg.connect(postgres_dsn) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    insert into public.device_codes
                        (device_code, user_code, kind, node_id, expires_at)
                    values ('dc-test-1', 'UC-TEST1', 'machine', null, %s)
                    """,
                    (expires,),
                )
            except psycopg.errors.CheckViolation:
                conn.rollback()
            else:
                conn.rollback()
                raise AssertionError(
                    "a machine device code was accepted with a null node_id"
                )


def test_starter_grant_eligibility_marks_only_post_migration_profiles(db):
    """Existing profiles receive false when 0021 adds the column; only then
    does true become the default for profiles created after launch."""
    migration = (MIGRATIONS / "0021_credit_requests.sql").read_text().lower()
    false_boundary = migration.index(
        "starter_grant_eligible boolean not null default false"
    )
    future_default = migration.index(
        "alter column starter_grant_eligible set default true"
    )
    assert false_boundary < future_default

    with db.cursor() as cur:
        cur.execute(
            "select is_nullable, column_default"
            " from information_schema.columns"
            " where table_schema = 'public' and table_name = 'profiles'"
            " and column_name = 'starter_grant_eligible'"
        )
        column = cur.fetchone()
    assert column == {"is_nullable": "NO", "column_default": "true"}


def test_credit_requests_use_millicredits_and_constrained_decision_states(db):
    """Amounts and state shape are database invariants, so a future writer
    cannot bypass repository validation and leave an impossible review row."""
    with db.cursor() as cur:
        cur.execute(
            "select column_name, data_type from information_schema.columns"
            " where table_schema = 'public' and table_name = 'credit_requests'"
        )
        columns = {row["column_name"]: row["data_type"] for row in cur.fetchall()}
        cur.execute(
            "select pg_get_constraintdef(oid) as def from pg_constraint"
            " where conrelid = 'public.credit_requests'::regclass"
            "   and contype = 'c'"
        )
        checks = " ".join(row["def"].lower() for row in cur.fetchall())

    assert columns["id"] == "uuid"
    assert columns["requested_zc"] == "bigint"
    assert columns["approved_zc"] == "bigint"
    assert "requested_zc > 0" in checks
    assert "approved_zc > 0" in checks
    assert "char_length(btrim(purpose)) >= 1" in checks
    assert "char_length(btrim(purpose)) <= 2000" in checks
    assert "purpose = btrim(purpose)" in checks
    assert all(status in checks for status in ("pending", "approved", "declined"))
    assert "approved_zc is null" in checks
    assert "approved_zc is not null" in checks
    assert "decided_at is null" in checks
    assert "decided_at is not null" in checks
    assert "decided_by is null" in checks


def test_credit_request_foreign_keys_have_the_required_delete_actions(db):
    with db.cursor() as cur:
        cur.execute(
            "select a.attname as column_name, c.confdeltype"
            "  from pg_constraint c"
            "  join unnest(c.conkey) with ordinality as key(attnum, ord) on true"
            "  join pg_attribute a on a.attrelid = c.conrelid and a.attnum = key.attnum"
            " where c.conrelid = 'public.credit_requests'::regclass"
            "   and c.confrelid = 'public.profiles'::regclass"
            "   and c.contype = 'f'"
        )
        actions = {row["column_name"]: row["confdeltype"] for row in cur.fetchall()}

    assert actions == {"user_id": "c", "decided_by": "n"}


def test_credit_request_indexes_match_history_admin_and_pending_queries(db):
    with db.cursor() as cur:
        cur.execute(
            "select indexname, indexdef from pg_indexes"
            " where schemaname = 'public' and tablename = 'credit_requests'"
        )
        indexes = {
            row["indexname"]: row["indexdef"].lower().replace('"', "")
            for row in cur.fetchall()
        }

    assert "(user_id, requested_at desc)" in indexes[
        "credit_requests_user_history_idx"
    ]
    assert "(status, requested_at)" in indexes["credit_requests_admin_queue_idx"]
    pending = indexes["credit_requests_one_pending_per_user_idx"]
    assert "unique index" in pending
    assert "(user_id)" in pending
    assert "where (status = 'pending'::text)" in pending


def test_credit_request_pending_uniqueness_preserves_decided_history(db):
    owner = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id, email) values (%s::uuid, %s)",
            (owner, f"{owner}@example.test"),
        )
        cur.execute("insert into public.profiles (id) values (%s::uuid)", (owner,))
        cur.execute(
            "insert into public.credit_requests (user_id, requested_zc, purpose)"
            " values (%s::uuid, 1000, 'First test')",
            (owner,),
        )

    with pytest.raises(psycopg.errors.UniqueViolation):
        with db.transaction():
            db.execute(
                "insert into public.credit_requests (user_id, requested_zc, purpose)"
                " values (%s::uuid, 2000, 'Second test')",
                (owner,),
            )

    with db.cursor() as cur:
        cur.execute(
            "update public.credit_requests"
            "   set status = 'declined', decided_at = now()"
            " where user_id = %s::uuid and status = 'pending'",
            (owner,),
        )
        cur.execute(
            "insert into public.credit_requests (user_id, requested_zc, purpose)"
            " values (%s::uuid, 2000, 'Second test')",
            (owner,),
        )
        cur.execute(
            "select status from public.credit_requests"
            " where user_id = %s::uuid order by requested_at",
            (owner,),
        )
        assert [row["status"] for row in cur.fetchall()] == ["declined", "pending"]


# ---------------------------------------------------------------------------
# agent_principals (0027, AG-6)
# ---------------------------------------------------------------------------


def _agent_owner(db) -> str:
    owner = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id, email) values (%s::uuid, %s)",
            (owner, f"{owner}@example.test"),
        )
        cur.execute("insert into public.profiles (id) values (%s::uuid)", (owner,))
    return owner


def _agent_pool(db, owner: str) -> str:
    return str(dbmod.create_pool(db, name="agent-schema-test", owner_id=owner)["id"])


#: Sentinel meaning "derive a fresh value" — distinct from an explicit
#: ``None``, which several tests below pass on purpose to prove the
#: active/revoked coupling check refuses it.
_AUTO = object()


def _insert_agent_principal(
    db,
    owner: str,
    *,
    scopes: list[str],
    pool_id: str | None = None,
    allowance_zc: int = 0,
    status: str = "active",
    token_hash=_AUTO,
    token_prefix=_AUTO,
    revoked_at: str | None = None,
) -> None:
    """Raw insert, bypassing agent_identity.py entirely, so these tests prove
    the CHECK constraints hold even against a caller that skips the Python
    validation layer altogether — the same posture
    ``test_machine_lifecycle_admits_leased_and_still_refuses_anything_else``
    takes toward ``machines.lifecycle``.

    ``token_hash`` defaults to a freshly-random value for an ``active`` row
    (``token_hash`` is ``unique``, so a fixed constant would collide across
    the many active rows these tests insert) and to ``None`` for a
    ``revoked`` one, matching what a real create/revoke pair would leave
    behind. Pass either explicitly to test a row that violates that shape on
    purpose.
    """
    if token_hash is _AUTO:
        token_hash = f"test-hash-{uuid.uuid4()}" if status == "active" else None
    if token_prefix is _AUTO:
        token_prefix = f"fmk_{token_hash[:8]}" if token_hash else None
    db.execute(
        """
        insert into public.agent_principals
            (owner_id, label, token_hash, token_prefix, scopes, pool_id,
             allowance_zc, status, revoked_at)
        values (%s::uuid, 'test principal', %s, %s, %s, %s::uuid, %s, %s,
                %s::timestamptz)
        """,
        (owner, token_hash, token_prefix, scopes, pool_id, allowance_zc,
         status, revoked_at),
    )


def test_agent_principals_table_exists_with_the_expected_shape(db):
    """Migration 0027, checked against the applied schema rather than the
    file that was typed — the same discipline every other table test in this
    file follows, for the reason stated at the top of the file: only an
    insert (or, here, a column listing) proves the migration *applied*."""
    with db.cursor() as cur:
        cur.execute(
            "select column_name, data_type, is_nullable"
            "  from information_schema.columns"
            " where table_schema = 'public' and table_name = 'agent_principals'"
        )
        cols = {r["column_name"]: r for r in cur.fetchall()}
    assert cols, "migration 0027 did not apply"

    assert cols["id"]["is_nullable"] == "NO"
    assert cols["owner_id"]["is_nullable"] == "NO"
    assert cols["label"]["is_nullable"] == "NO"
    assert cols["scopes"]["data_type"] == "ARRAY"
    assert cols["scopes"]["is_nullable"] == "NO"
    assert cols["allowance_zc"]["data_type"] == "bigint"
    assert cols["allowance_zc"]["is_nullable"] == "NO"
    assert cols["status"]["is_nullable"] == "NO"
    # Nullable so revoke can clear a live credential down to nothing.
    assert cols["token_hash"]["is_nullable"] == "YES"
    assert cols["token_prefix"]["is_nullable"] == "YES"
    assert cols["pool_id"]["is_nullable"] == "YES"
    assert cols["revoked_at"]["is_nullable"] == "YES"

    with db.cursor() as cur:
        cur.execute(
            "select relrowsecurity from pg_class"
            " where oid = 'public.agent_principals'::regclass"
        )
        assert cur.fetchone()["relrowsecurity"] is True


def test_agent_principals_owner_id_cascades_from_profiles(db):
    with db.cursor() as cur:
        cur.execute(
            "select confdeltype from pg_constraint"
            " where conrelid = 'public.agent_principals'::regclass"
            "   and contype = 'f'"
            "   and confrelid = 'public.profiles'::regclass"
        )
        row = cur.fetchone()
    assert row is not None, "agent_principals has no owner_id foreign key"
    assert row["confdeltype"] == "c", "expected ON DELETE CASCADE"


def test_agent_principals_scopes_are_constrained_to_exactly_the_three_known(db):
    owner = _agent_owner(db)

    pool = _agent_pool(db, owner)
    for scopes in (["read"], ["submit"], ["spend"], ["read", "submit", "spend"]):
        with db.transaction():
            _insert_agent_principal(
                db, owner, scopes=scopes,
                pool_id=pool if "submit" in scopes else None,
                allowance_zc=100 if "spend" in scopes else 0,
            )

    # A near-miss that is not one of the three, and an empty list: both
    # refused at the schema, not merely by agent_identity.normalise_scopes.
    for garbage in (["reads"], ["Read"], [], ["read", "unknown"]):
        with pytest.raises(psycopg.errors.CheckViolation):
            with db.transaction():
                _insert_agent_principal(db, owner, scopes=garbage)


def test_agent_principals_submit_scope_requires_a_pool_id(db):
    owner = _agent_owner(db)

    with pytest.raises(psycopg.errors.CheckViolation):
        with db.transaction():
            _insert_agent_principal(db, owner, scopes=["submit"], pool_id=None)

    # The same request, with a pool named, succeeds.
    with db.transaction():
        _insert_agent_principal(
            db, owner, scopes=["submit"], pool_id=_agent_pool(db, owner)
        )


def test_agent_principals_spend_scope_requires_a_positive_allowance(db):
    owner = _agent_owner(db)

    for bad_allowance in (0, -1):
        with pytest.raises(psycopg.errors.CheckViolation):
            with db.transaction():
                _insert_agent_principal(
                    db, owner, scopes=["spend"], allowance_zc=bad_allowance
                )

    with db.transaction():
        _insert_agent_principal(db, owner, scopes=["spend"], allowance_zc=1)


def test_agent_principals_active_and_revoked_states_are_mutually_exclusive(db):
    """The coupling `revoke_agent_principal` relies on, enforced as a row
    invariant: `token_hash`/`token_prefix` are present if and only if
    `status = 'active'`, and `revoked_at` is set if and only if it is not.
    A bug that flips one half without the other must be unwritable, not
    merely untested."""
    owner = _agent_owner(db)

    # Active with no credential material: refused.
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.transaction():
            _insert_agent_principal(
                db, owner, scopes=["read"], status="active",
                token_hash=None, token_prefix=None,
            )

    # Active with a revoked_at timestamp already set: refused.
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.transaction():
            _insert_agent_principal(
                db, owner, scopes=["read"], status="active",
                revoked_at="2026-01-01T00:00:00Z",
            )

    # Revoked but still carrying a hash: refused — the exact shape a
    # half-finished revoke must never be allowed to leave behind.
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.transaction():
            _insert_agent_principal(
                db, owner, scopes=["read"], status="revoked",
                token_hash="still-here", token_prefix="fmk_still",
                revoked_at="2026-01-01T00:00:00Z",
            )

    # Revoked with no revoked_at: refused.
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.transaction():
            _insert_agent_principal(
                db, owner, scopes=["read"], status="revoked",
                token_hash=None, token_prefix=None, revoked_at=None,
            )

    # The only two legal shapes: active-with-credential and
    # revoked-with-nothing-and-a-timestamp.
    with db.transaction():
        _insert_agent_principal(db, owner, scopes=["read"], status="active")
    with db.transaction():
        _insert_agent_principal(
            db, owner, scopes=["read"], status="revoked",
            token_hash=None, token_prefix=None,
            revoked_at="2026-01-01T00:00:00Z",
        )


def test_agent_principals_token_hash_allows_multiple_nulls(db):
    """`token_hash` is `unique`, and Postgres treats every NULL as distinct
    from every other NULL — so many revoked principals coexisting with a
    cleared hash must not collide with each other. If this ever failed it
    would mean the column had been declared NOT NULL UNIQUE by mistake, which
    would make the second revoke in a session fail with a UniqueViolation
    instead of a clean no-op."""
    owner = _agent_owner(db)

    with db.transaction():
        _insert_agent_principal(
            db, owner, scopes=["read"], status="revoked",
            token_hash=None, token_prefix=None,
            revoked_at="2026-01-01T00:00:00Z",
        )
    with db.transaction():
        _insert_agent_principal(
            db, owner, scopes=["read"], status="revoked",
            token_hash=None, token_prefix=None,
            revoked_at="2026-01-01T00:00:00Z",
        )
