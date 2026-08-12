"""The acquisition ledger's shape. A rented machine costs money from the
moment it exists, so every column that teardown depends on is NOT NULL, the
rows point at real owners, and the table is not readable by the browser."""
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


def test_state_is_constrained_to_exactly_the_four_states(db):
    """Names AND values. A test that only asserts the constraint exists
    passes against a schema missing 'FAILED' — and 'FAILED' is load-bearing:
    `window_spend_usd` counts failed acquisitions on purpose, because a
    failed acquisition cannot be proven to have cost nothing."""
    with db.cursor() as cur:
        cur.execute(
            """
            select pg_get_constraintdef(oid) as definition
              from pg_constraint
             where conname = 'rented_capacity_state_check'
               and conrelid = 'public.rented_capacity'::regclass
            """
        )
        row = cur.fetchone()
    assert row is not None, "rented_capacity_state_check is missing"

    # `CHECK ((state = ANY (ARRAY['REQUESTED'::text, ...])))` — every quoted
    # literal in the definition is a permitted state.
    permitted = set(
        part for part in row["definition"].split("'")[1::2]
    )
    assert permitted == {"REQUESTED", "ACTIVE", "RELEASED", "FAILED"}


def test_the_owner_and_pool_are_real_rows_and_the_machine_link_survives_them(db):
    """Foreign keys, and the delete behaviour each one needs.

    `owner_id`/`pool_id` cascade like every other child of profiles and pools
    (0007, 0014, 0018) — 0018 explains why a `restrict` is not available to
    us: `profiles.id` references `auth.users(id) on delete cascade`, so a
    restrict here would make Supabase's own user delete fail on our table.

    `machine_id` is `set null` for the reason 0014 gives for
    `sandbox_sessions.machine_id`: a row whose machine was deleted still
    holds a live `provider_handle` that must be destroyed at the venue, and
    cascading would delete the only record that says so.

    `job_id` deliberately has NO foreign key — it may name a coordinator-side
    job rather than a `public.jobs` row — which is why it is absent below.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select a.attname                    as column_name,
                   c.confrelid::regclass::text  as target,
                   c.confdeltype                as on_delete
              from pg_constraint c
              join unnest(c.conkey) as k(attnum) on true
              join pg_attribute a
                on a.attrelid = c.conrelid and a.attnum = k.attnum
             where c.conrelid = 'public.rented_capacity'::regclass
               and c.contype = 'f'
            """
        )
        keys = {
            r["column_name"]: (r["target"].split(".")[-1], r["on_delete"])
            for r in cur.fetchall()
        }

    # confdeltype: 'c' = cascade, 'n' = set null.
    assert keys == {
        "owner_id": ("profiles", "c"),
        "pool_id": ("pools", "c"),
        "machine_id": ("machines", "n"),
    }


def test_row_level_security_is_on_with_no_policies(db):
    """This table holds `owner_id`, `usd_per_hour` and `provider_handle`
    together: cross-tenant billing data, exposed through PostgREST to anyone
    holding the `anon` key that ships inside the browser bundle if RLS is
    off. RLS with ZERO policies is what denies every role but the owner and
    BYPASSRLS — a policy would reopen it, so the count must be zero too.

    Asserted here, directly, rather than leaning on the suite-wide guard in
    `test_schema.py`: this table is not registered in that file's table list
    yet, so the guard would report green on a table it never looked at.
    """
    with db.cursor() as cur:
        cur.execute(
            "select relrowsecurity from pg_class"
            " where oid = 'public.rented_capacity'::regclass"
        )
        assert cur.fetchone()["relrowsecurity"] is True

        cur.execute(
            "select count(*)::int as n from pg_policies"
            " where schemaname = 'public' and tablename = 'rented_capacity'"
        )
        assert cur.fetchone()["n"] == 0
