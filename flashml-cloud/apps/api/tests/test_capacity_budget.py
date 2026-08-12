"""The gate that stands between a bug and the account.

Refuses. Never queues -- a queue that drains when budget frees up is the
same unbounded spend with a delay."""
from __future__ import annotations

import uuid

import pytest

from flashml_cloud_api.capacity.budget import BudgetRefused, assert_within_budget
from test_jobs_from_repo import db  # noqa: F401 - fixture


class _Settings:
    rented_usd_per_acquisition_max = 2.0
    rented_usd_window_max = 10.0
    rented_usd_window_hours = 24.0


@pytest.fixture
def spender(db):
    """A real profile and pool to charge against, and a promise to clean up.

    Two reasons this exists rather than `gen_random_uuid()` inline:

    1. `rented_capacity.owner_id`/`pool_id` are real foreign keys, so an
       invented uuid is refused by the database.
    2. **Anything committed here poisons every test file that runs after
       this one.** The Postgres fixture is session-scoped and never
       truncated between files, and `window_spend_usd` has no venue, owner or
       job filter *on purpose* -- it is a single global ceiling. Leave $20/hr
       of rows behind against a $10 cap and the next file's acquisitions are
       refused for reasons that have nothing to do with what it is testing.

    So the rows go back. Deleting the `auth.users` row would cascade them all
    away by itself; the explicit delete first is there so the intent survives
    a future change to the cascade.
    """
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (%s)", (user_id,))
        cur.execute("insert into public.profiles (id) values (%s)", (user_id,))
        cur.execute(
            "insert into public.pools (name, owner_id) values (%s, %s)"
            " returning id",
            ("rented-capacity-budget", user_id),
        )
        pool_id = str(cur.fetchone()["id"])
    try:
        yield user_id, pool_id
    finally:
        with db.cursor() as cur:
            cur.execute(
                "delete from public.rented_capacity where owner_id = %s",
                (user_id,),
            )
            cur.execute("delete from auth.users where id = %s", (user_id,))


def test_a_single_expensive_acquisition_is_refused(db):
    with pytest.raises(BudgetRefused) as exc:
        assert_within_budget(
            db, venue_id="runpod", usd_per_hour=5.0,
            settings=_Settings(),
        )
    # The reason must name the number it exceeded. "Refused" alone sends
    # somebody to read source to find out why.
    assert "2.0" in str(exc.value)


def test_an_unpriced_acquisition_is_refused_rather_than_treated_as_free(db):
    """A venue that will not quote is a venue we cannot bound, and "no price"
    must never read as "no cost". The message has to say which venue and
    that the price is what was missing -- otherwise this is indistinguishable
    from the two ceiling refusals in an operator's logs."""
    with pytest.raises(BudgetRefused) as exc:
        assert_within_budget(
            db, venue_id="runpod", usd_per_hour=None,
            settings=_Settings(),
        )
    message = str(exc.value)
    assert "runpod" in message
    assert "no price" in message
    assert "unpriced" in message


def test_within_both_ceilings_is_allowed(db):
    assert_within_budget(
        db, venue_id="runpod", usd_per_hour=0.5, settings=_Settings(),
    )


def test_the_window_ceiling_counts_prior_acquisitions(db, spender):
    owner_id, pool_id = spender
    # The `db` fixture is autocommit: each of these is committed the moment
    # it runs, which is exactly why the `spender` fixture has to take them
    # back out again afterwards.
    with db.cursor() as cur:
        for _ in range(20):
            cur.execute(
                """
                insert into public.rented_capacity
                    (venue_id, state, owner_id, pool_id, job_id, usd_per_hour)
                values ('runpod', 'ACTIVE', %s, %s, 'j', 1.0)
                """,
                (owner_id, pool_id),
            )
    with pytest.raises(BudgetRefused) as exc:
        assert_within_budget(
            db, venue_id="runpod", usd_per_hour=0.5,
            settings=_Settings(),
        )
    assert "window" in str(exc.value).lower()


def test_the_window_is_left_clean_for_the_next_file(db):
    """The guard on the guard. If this file ever commits rows it does not
    remove, every later test file inherits a ceiling it never spent -- and
    the failure lands somewhere else entirely, which is the worst possible
    place to debug it from."""
    assert_within_budget(
        db, venue_id="runpod", usd_per_hour=0.5, settings=_Settings(),
    )
