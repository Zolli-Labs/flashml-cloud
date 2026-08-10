# apps/api/tests/test_schema.py
"""The migration file is the reviewable artifact; these tests pin the
invariants that matter rather than re-describing every column."""
import pathlib
import re

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

ALL_TABLES = TABLES + ["job_rounds", "pools", "pool_members", "pool_invites", "machine_pools"]


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
    """The attempt ledger: the API's durable lease -> (job, task) mapping.

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
        "job_id": "text",
        "lease_id": "text",
        "machine_id": "uuid",
        "task_id": "text",
    }

    with db.cursor() as cur:
        cur.execute(
            "select relrowsecurity from pg_class"
            " where oid = 'public.attempts'::regclass"
        )
        assert cur.fetchone()["relrowsecurity"] is True


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
