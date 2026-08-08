"""Per-account artifact storage budget.

The exposure this closes: nothing in this system limited how much a single
account could store. Artifacts land on a 5 GB disk attached to the prod
coordinator, shared by every workspace, so one person submitting a hundred
shards that each write 200 MB fills it — and a full disk does not degrade
one job, it takes the coordinator down and with it every other workspace's
running work.

The rule is pure arithmetic and lives here; *measuring* what an account has
stored is a database query and lives in `db`. Keeping them apart is what
makes the rule testable without a database, and it is also the honest
split: the rule is a policy decision, the measurement is a fact.

**Three places this fails open if written carelessly**, all of them tested:

- `limit or default` restores the default for an account an admin
  deliberately froze at 0.
- `0` read as "unlimited" inverts an operator's intention at exactly the
  moment they were trying to stop something. `None` is the only way to say
  unlimited, and it has to be unmistakable.
- `>` instead of `>=` lets a job start at exactly the limit. A task's
  output size is not known until after it has run, so the last safe moment
  to refuse is before the job starts, not after it overruns.
"""

from __future__ import annotations

import os

GB = 1024**3

#: What one account may store when the deployment sets nothing else.
#:
#: Deliberately well under the coordinator's 5 GB disk, which every account
#: shares: a per-account default at or above the disk size guarantees the
#: first two accounts can fill it between them, which is the outage this
#: module exists to prevent.
DEFAULT_LIMIT_BYTES = 2 * GB


def deployment_default() -> int | None:
    """The limit for accounts with no override, from the environment.

    `FLASHML_STORAGE_LIMIT_BYTES=` (empty) or `unlimited` disables the
    budget entirely — an explicit escape hatch for a self-hosted operator
    who owns the disk and does not want a quota. Anything unparseable falls
    back to the default rather than to unlimited: a typo in a deployment
    variable must not silently remove the protection.
    """
    raw = os.environ.get("FLASHML_STORAGE_LIMIT_BYTES")
    if raw is None:
        return DEFAULT_LIMIT_BYTES
    raw = raw.strip().lower()
    if raw in ("", "unlimited", "none"):
        return None
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_LIMIT_BYTES


def limit_for(override: int | None, default: int | None) -> int | None:
    """This account's limit: its own override, else the deployment default.

    `is None`, never truthiness — an override of 0 means "this account may
    store nothing", which is a thing an admin does on purpose and which
    `override or default` would silently undo.
    """
    return default if override is None else override


def budget_problem(used: int, limit: int | None) -> str | None:
    """Why this account may not submit, or None.

    `>=` is deliberate: see the module docstring.
    """
    if limit is None:
        return None
    if used < limit:
        return None
    return (
        f"this account is using {_human(used)} of its {_human(limit)} "
        f"storage budget, so new jobs cannot start — delete a finished "
        f"job's artifacts to free space, or ask an admin to raise the limit"
    )


def percent_used(used: int, limit: int | None) -> float | None:
    """Fill percentage for the console, or None when unlimited.

    None rather than 0.0: an empty progress bar implies a limit exists and
    is barely touched, which is the opposite of the truth for an account
    with no limit at all. The UI needs to tell those apart.

    Clamped to 100. An account CAN exceed its budget — usage is measured
    after tasks commit, not predicted before — but a bar rendering 137% is
    a bug report rather than information.
    """
    if limit is None:
        return None
    if limit <= 0:
        return 100.0
    return min(100.0, round(used / limit * 100, 1))


def _human(n: int) -> str:
    for unit, size in (("GB", GB), ("MB", 1024**2), ("KB", 1024)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} bytes"
