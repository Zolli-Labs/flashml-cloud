"""Per-account storage budget: the check that stops one job filling the disk.

There is no quota anywhere in this system today. Artifacts land on a 5 GB
Render disk attached to the prod coordinator, and one person submitting a
hundred shards that each write 200 MB fills it — taking the coordinator,
and therefore every other workspace's running jobs, down with it.

The policy lives here as pure arithmetic so it can be reasoned about
without a database. Deciding *whether* to refuse and *finding out* how
much is stored are deliberately separate: one is a rule, the other is a
query, and mixing them makes the rule untestable.
"""

from __future__ import annotations

import pytest

from flashml_cloud_api.storage import (
    DEFAULT_LIMIT_BYTES,
    budget_problem,
    limit_for,
    percent_used,
    sum_artifact_sizes,
)

GB = 1024**3


# -- the refusal rule --------------------------------------------------------


def test_an_account_inside_its_budget_may_submit():
    assert budget_problem(used=1 * GB, limit=5 * GB) is None


def test_an_account_over_its_budget_is_refused():
    problem = budget_problem(used=6 * GB, limit=5 * GB)
    assert problem is not None
    # The message has to carry both numbers. "Quota exceeded" tells someone
    # nothing about whether to delete one job or ask for more room.
    assert "6" in problem and "5" in problem


def test_the_message_says_how_to_get_unstuck():
    """A refusal that names no remedy is a dead end — this account cannot
    submit again until something changes, and only the person can change
    it."""
    problem = budget_problem(used=6 * GB, limit=5 * GB)
    assert "delete" in problem.lower() or "remove" in problem.lower()


def test_exactly_at_the_limit_is_refused_not_allowed():
    """`>=`, not `>`. At exactly the limit the next byte written overruns
    it, and a task's output size is not known until after it has run — so
    the last safe moment to say no is before the job starts."""
    assert budget_problem(used=5 * GB, limit=5 * GB) is not None


def test_a_zero_limit_means_blocked_not_unlimited():
    """A deployment that sets the limit to 0 means "nobody may store
    anything". Reading 0 as "no limit" would invert an operator's
    intention at exactly the moment they were trying to stop something."""
    assert budget_problem(used=1, limit=0) is not None


def test_a_negative_limit_is_treated_as_unlimited_only_when_explicit():
    """`None` is the one way to say unlimited. It has to be unmistakable,
    because every other reading of "no value" here fails open."""
    assert budget_problem(used=100 * GB, limit=None) is None


# -- resolving the limit -----------------------------------------------------


def test_an_account_with_no_override_gets_the_deployment_default():
    assert limit_for(override=None, default=7 * GB) == 7 * GB


def test_an_account_override_wins():
    assert limit_for(override=20 * GB, default=7 * GB) == 20 * GB


def test_an_override_of_zero_is_honoured_not_ignored():
    """Same trap as above, one layer up: `0 or default` would silently
    restore the default for an account an admin deliberately froze."""
    assert limit_for(override=0, default=7 * GB) == 0


def test_the_default_is_smaller_than_the_disk_it_shares():
    """The prod coordinator's disk is 5 GB and every account shares it. A
    per-account default at or above that guarantees the first two accounts
    can fill it between them."""
    assert DEFAULT_LIMIT_BYTES < 5 * GB


# -- what the console shows --------------------------------------------------


def test_percent_used_is_reported_for_a_progress_bar():
    assert percent_used(used=1 * GB, limit=4 * GB) == 25.0


def test_percent_used_of_an_unlimited_account_is_none_not_zero():
    """0% would draw an empty bar and imply a limit exists. There isn't
    one, and the UI needs to be able to tell those apart."""
    assert percent_used(used=100 * GB, limit=None) is None


def test_percent_used_never_exceeds_one_hundred():
    """An account can go over — usage is measured after the fact — but a
    progress bar rendering 137% is a bug report, not information."""
    assert percent_used(used=8 * GB, limit=4 * GB) == 100.0


def test_percent_used_of_a_zero_limit_is_full_not_a_crash():
    assert percent_used(used=1, limit=0) == 100.0


# -- reading a coordinator artifact listing ----------------------------------
#
# The listing is the one thing standing between "there is a budget" and "the
# budget can trip", and it arrives over the wire from another service. What
# it must never do is turn a malformed entry into a plausible-looking
# number.


def test_a_jobs_footprint_is_the_sum_of_its_artifacts():
    assert sum_artifact_sizes([
        {"uri": "artifact://jobs/j/a.bin", "key": "jobs/j/a.bin", "size_bytes": 700},
        {"uri": "artifact://jobs/j/b.bin", "key": "jobs/j/b.bin", "size_bytes": 300},
    ]) == 1000


def test_a_job_that_wrote_nothing_has_a_footprint_of_zero():
    assert sum_artifact_sizes([]) == 0


def test_an_entry_with_no_size_contributes_nothing_rather_than_guessing():
    """A coordinator older than the size field, or an entry it could not
    stat, reports no size. Counting it as anything but 0 would be inventing
    bytes; refusing the whole listing would throw away the sizes it did
    report."""
    assert sum_artifact_sizes([
        {"key": "jobs/j/a.bin"},
        {"key": "jobs/j/b.bin", "size_bytes": 42},
    ]) == 42


def test_a_boolean_size_is_not_counted_as_one_byte():
    """`True` is an `int` in Python and would sum as 1 under a naive
    isinstance check — a silent, tiny, permanent overcount."""
    assert sum_artifact_sizes([{"key": "k", "size_bytes": True}]) == 0


def test_nonsense_entries_are_skipped_not_raised_on():
    assert sum_artifact_sizes(
        ["not-a-dict", {"size_bytes": "big"}, {"size_bytes": -5}, {"size_bytes": 8}]
    ) == 8


# -- accounting, against a real database -------------------------------------
#
# The rule above is arithmetic. This is the half that has to be true of
# actual rows: what an account is using, and that recording a footprint is
# idempotent (a job's state is polled repeatedly, so "record it" runs many
# times for one job and must not accumulate).


import uuid

import psycopg
from psycopg.rows import dict_row

from flashml_cloud_api import db as dbmod


@pytest.fixture()
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
        cur.execute("insert into auth.users (id) values (%s)", (user_id,))
        cur.execute(
            "insert into public.profiles (id, admitted_at) values (%s, now())",
            (user_id,),
        )
    return user_id


def _job(db, owner_id: str, job_id: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            "insert into public.jobs (id, owner_id, status) values (%s, %s, 'running')",
            (job_id, owner_id),
        )


def test_a_new_account_is_using_nothing(db):
    assert dbmod.storage_usage_for_owner(db, _user(db)) == 0


def test_usage_sums_every_job_this_account_owns(db):
    owner = _user(db)
    _job(db, owner, f"j-{uuid.uuid4().hex[:8]}")
    _job(db, owner, f"j-{uuid.uuid4().hex[:8]}")
    jobs = [r["id"] for r in db.execute(
        "select id from public.jobs where owner_id = %s", (owner,)).fetchall()]
    dbmod.record_job_artifact_bytes(db, jobs[0], 1000)
    dbmod.record_job_artifact_bytes(db, jobs[1], 2500)
    assert dbmod.storage_usage_for_owner(db, owner) == 3500


def test_recording_the_same_job_twice_does_not_double_count(db):
    """A job's state is polled every two seconds while the console is open,
    and the recording hook runs each time. Adding instead of setting would
    make an idle browser tab inflate someone's usage without bound."""
    owner = _user(db)
    job_id = f"j-{uuid.uuid4().hex[:8]}"
    _job(db, owner, job_id)
    dbmod.record_job_artifact_bytes(db, job_id, 1000)
    dbmod.record_job_artifact_bytes(db, job_id, 1000)
    assert dbmod.storage_usage_for_owner(db, owner) == 1000


def test_one_accounts_usage_is_not_anothers(db):
    a, b = _user(db), _user(db)
    job_a = f"j-{uuid.uuid4().hex[:8]}"
    _job(db, a, job_a)
    dbmod.record_job_artifact_bytes(db, job_a, 9999)
    assert dbmod.storage_usage_for_owner(db, b) == 0


def test_an_account_with_no_override_reports_none_not_zero(db):
    """NULL means "use the deployment default". Reading it as 0 would
    freeze every account that never had an override set."""
    assert dbmod.storage_limit_override_for(db, _user(db)) is None


def test_an_override_of_zero_reads_back_as_zero(db):
    owner = _user(db)
    with db.cursor() as cur:
        cur.execute(
            "update public.profiles set storage_limit_bytes = 0 where id = %s",
            (owner,),
        )
    assert dbmod.storage_limit_override_for(db, owner) == 0
