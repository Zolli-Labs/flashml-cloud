# apps/api/tests/test_schema.py
"""The migration file is the reviewable artifact; these tests pin the
invariants that matter rather than re-describing every column."""
import pathlib
import re

SQL = (pathlib.Path(__file__).parent.parent / "migrations" / "0001_initial.sql").read_text()

TABLES = ["profiles", "machines", "device_codes", "jobs", "contributions"]


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
        assert not re.search(rf"create policy.*\bto\s+{role}\b", SQL, re.I | re.S), role


def test_machines_store_a_hash_not_a_token():
    assert "token_hash" in SQL
    assert not re.search(r"\btoken\s+text", SQL, re.I), "raw token column present"


def test_node_id_is_unique():
    assert re.search(r"node_id\s+text\s+(not null\s+)?unique", SQL, re.I)


def test_machine_status_is_constrained():
    assert re.search(r"status.*check.*pending.*active.*revoked", SQL, re.I | re.S)


def test_owner_columns_cascade_from_profiles():
    assert SQL.lower().count("references public.profiles(id)") >= 2
