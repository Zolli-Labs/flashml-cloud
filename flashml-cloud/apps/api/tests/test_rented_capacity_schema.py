"""The acquisition ledger's shape. A rented machine costs money from the
moment it exists, so every column that teardown depends on is NOT NULL."""
from __future__ import annotations

from test_jobs_from_repo import db  # noqa: F401 - fixture


def _columns(db, table):
    with db.cursor() as cur:
        cur.execute(
            """
            select column_name, is_nullable
              from information_schema.columns
             where table_schema = 'public' and table_name = %s
            """,
            (table,),
        )
        return {r["column_name"]: r["is_nullable"] for r in cur.fetchall()}


def test_rented_capacity_carries_what_teardown_needs(db):
    cols = _columns(db, "rented_capacity")
    # The provider handle is how we destroy it. Without it we are billing
    # for something we cannot name.
    assert cols["venue_id"] == "NO"
    assert cols["state"] == "NO"
    assert cols["owner_id"] == "NO"
    assert cols["pool_id"] == "NO"
    # Nullable: the handle does not exist until the venue answers, which is
    # exactly the window the reconciler exists to close.
    assert cols["provider_handle"] == "YES"
    assert cols["machine_id"] == "YES"
    assert cols["released_at"] == "YES"


def test_state_is_constrained(db):
    with db.cursor() as cur:
        cur.execute(
            """
            select 1 from information_schema.check_constraints
             where constraint_name = 'rented_capacity_state_check'
            """
        )
        assert cur.fetchone() is not None
