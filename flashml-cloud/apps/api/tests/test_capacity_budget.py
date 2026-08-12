"""The gate that stands between a bug and the account.

Refuses. Never queues -- a queue that drains when budget frees up is the
same unbounded spend with a delay."""
from __future__ import annotations

import pytest

from flashml_cloud_api.capacity.budget import BudgetRefused, assert_within_budget
from test_jobs_from_repo import db  # noqa: F401 - fixture


class _Settings:
    rented_usd_per_acquisition_max = 2.0
    rented_usd_window_max = 10.0
    rented_usd_window_hours = 24.0


def test_a_single_expensive_acquisition_is_refused(db):
    with pytest.raises(BudgetRefused) as exc:
        assert_within_budget(
            db, venue_id="runpod", usd_per_hour=5.0,
            settings=_Settings(),
        )
    # The reason must name the number it exceeded. "Refused" alone sends
    # somebody to read source to find out why.
    assert "2.0" in str(exc.value)


def test_within_both_ceilings_is_allowed(db):
    assert_within_budget(
        db, venue_id="runpod", usd_per_hour=0.5, settings=_Settings(),
    )


def test_the_window_ceiling_counts_prior_acquisitions(db):
    with db.cursor() as cur:
        for _ in range(20):
            cur.execute(
                """
                insert into public.rented_capacity
                    (venue_id, state, owner_id, pool_id, job_id, usd_per_hour)
                values ('runpod', 'ACTIVE', gen_random_uuid(),
                        gen_random_uuid(), 'j', 1.0)
                """
            )
    db.commit()
    with pytest.raises(BudgetRefused) as exc:
        assert_within_budget(
            db, venue_id="runpod", usd_per_hour=0.5,
            settings=_Settings(),
        )
    assert "window" in str(exc.value).lower()
