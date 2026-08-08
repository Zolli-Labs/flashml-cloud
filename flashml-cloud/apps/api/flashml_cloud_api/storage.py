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
from collections.abc import Iterable
from typing import Any

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


def sum_artifact_sizes(listing: Iterable[Any]) -> int:
    """A job's footprint, from the coordinator's artifact listing.

    The listing is ``[{uri, key, size_bytes}, …]`` — one entry per file the
    job left under ``jobs/{job_id}/`` — and the sum of its sizes is what the
    job costs the shared disk. Pure, and here rather than at either call
    site, because both the Mode A route and the federated driver reduce a
    listing to one number and two copies of that arithmetic would eventually
    disagree about the awkward entries below.

    **Every judgement here is "skip the entry, keep the rest".** This is
    data from another service, and the two failure modes it can produce are
    asymmetric: dropping one unreadable entry under-reports by that file,
    while refusing the whole listing under-reports by the entire job and
    would do so silently and for ever, since the caller records what it is
    given exactly once.

    - No ``size_bytes`` (a coordinator older than the field, or a file it
      could not stat) contributes 0. Anything else would be inventing bytes.
    - ``bool`` is excluded explicitly: ``True`` is an ``int`` in Python and
      would otherwise sum as one byte — a silent, permanent overcount that
      no test looking at plausible numbers would catch.
    - Negative sizes are impossible and are ignored rather than allowed to
      subtract from a real file's bytes.
    """
    total = 0
    for entry in listing:
        if not isinstance(entry, dict):
            continue
        size = entry.get("size_bytes")
        if isinstance(size, int) and not isinstance(size, bool) and size > 0:
            total += size
    return total


def deletion_counts(payload: Any) -> tuple[int, int]:
    """``(deleted_files, freed_bytes)`` from the coordinator's delete answer.

    **Reported, never accounted with.** These two numbers go into the
    response body so the console can say "freed 1.4 GB" and nowhere else.
    The recorded usage after a deletion is not `old - freed_bytes`; it is a
    fresh measurement of what is left, which is zero because the whole job's
    artifacts are what was deleted. That is the difference between a number
    the coordinator asserts and a fact this API is willing to store — and it
    is why a coordinator that lied about `freed_bytes`, or one older than
    the field, can make this response optimistic but can never make an
    account's budget wrong.

    Defensive in the same shape as ``sum_artifact_sizes`` and for the same
    reason — it parses another service's payload — with ``bool`` excluded
    explicitly (``True`` is an ``int`` and would report one file freed) and
    negatives dropped rather than allowed to subtract.
    """
    if not isinstance(payload, dict):
        return (0, 0)
    return (_count(payload.get("deleted_files")), _count(payload.get("freed_bytes")))


def _count(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 0


def _human(n: int) -> str:
    for unit, size in (("GB", GB), ("MB", 1024**2), ("KB", 1024)):
        if n >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} bytes"
