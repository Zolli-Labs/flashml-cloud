# apps/api/tests/test_schema.py
"""The migration file is the reviewable artifact; these tests pin the
invariants that matter rather than re-describing every column."""
import pathlib
import re

MIGRATIONS = pathlib.Path(__file__).parent.parent / "migrations"

SQL = (MIGRATIONS / "0001_initial.sql").read_text()

#: Every migration concatenated. The deny-all-by-default RLS property is a
#: property of the *schema*, not of one file — a later migration that added
#: a permissive policy would leave the 0001 checks below passing while the
#: database was wide open, so the policy check runs over all of them.
ALL_SQL = "\n".join(p.read_text() for p in sorted(MIGRATIONS.glob("*.sql")))

TABLES = ["profiles", "machines", "device_codes", "jobs", "contributions"]

ALL_TABLES = TABLES + ["job_rounds"]


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
