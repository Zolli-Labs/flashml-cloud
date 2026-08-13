"""The agent wallet: `marketplace.can_cover` gets its first caller (AG-3),
then one shared approver for every principal kind (PF-3).

AS-17 named three layers that should stand between an agent and someone
else's money and found only two built: the AS-9 confirmation gate ("are you
sure?", built into `agent_loop.AgentLoop.step()` from the start) and the
capacity budget gate ("is this rate sane?"). The third — "can they afford
it?" — did not exist: `marketplace.can_cover` was defined and had **no
production caller anywhere**, only test callers and two docstrings that
described a bound nothing enforced. This module is that caller, and it is
the only thing this module does.

**PF-3 — one vocabulary for every principal.** AG-3 built the can_cover
caller for exactly one principal kind: an agent, spending under a human's
`AgentAllowance`. AS-10 asks for the same allowance/spend/refusal vocabulary
whether the principal is an agent, a human spending directly, or a pool
spending on a job's behalf. `SpendAllowance` and `make_spend_approver` are
that generalization — the same two guards (allowance, solvency), the same
OC-4 and OC-9 rules, keyed by `principal_kind` + `principal_id` instead of
hardcoded to `agent_id`. `AgentAllowance` and `make_agent_approver` are
unchanged in signature and behavior; internally, `make_agent_approver` is now
a thin adapter that builds a `SpendAllowance` with `principal_kind="agent"`
and calls `make_spend_approver`. There is exactly one approval code path;
every principal kind runs it.

**What this is not.** `agent_loop.AgentLoop` already refuses a spend when no
`approve_spend` is configured, or when one is configured and returns
`False` (AS-9). That gate asks a human "are you sure?" — it is a
confirmation, not a solvency check, and `AS-17` is explicit that it "does
not, and cannot, check that the spend is affordable." This module produces
the `approve_spend` callback the loop calls, and inside it adds exactly the
two things AS-9 alone cannot provide: a hard ceiling on what this agent may
spend at all (the allowance, AS-9's own "the allowance IS the human's
consent, bounded"), and a solvency check against the account's real balance
(`marketplace.can_cover`, OC-9's ledger read). Neither layer replaces the
other; `AgentLoop` still calls whatever `approve_spend` this module builds,
and a caller who wires no approver at all still gets refused exactly as
before.

**OC-4 — refuse, never queue.** Over budget or unparseable both return
`False`, full stop. There is no retry, no partial approval, no deferred
decision. A spend either clears both guards right now or it does not happen.

**OC-9 — every guard is a ledger read.** `make_agent_approver` never mutates
anything; both of its checks are reads. The allowance check reads
`allowance.spent_zc`, a value the caller is expected to have sourced from
the ledger (see `AgentAllowance`'s field doc) — this module does not
recompute it, and does not trust a number that arrived any other way. The
solvency check reads the account's live spendable balance through
`read_spendable()`, the same reader (`marketplace.balances` /
`marketplace.credit_balances`) every other spendable-balance read in this
package uses — never an invented or cached number — and hands it straight to
`marketplace.can_cover` (`marketplace.py:324`), which is the "solvency
check, not a hold" the module docstring there describes: nothing here holds
or reserves anything, it only asks whether the balance could cover the
proposed grant right now.

**The honesty rule.** A spend whose cost cannot be parsed out of the
proposed tool call is never treated as free. `spend_cost_zc` returns `None`
for the cost — not `0` — when it cannot find `zc_per_hour`, `seconds` and
`tasks` in the tool's proposed args, and `make_agent_approver` refuses on
`None` before either guard runs. The alternative — defaulting an
unparseable spend to 0 — would let a malformed or adversarial tool call
sail through both checks for free, which is a worse failure than refusing a
spend that might have been fine.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable

from . import marketplace
from .agent_loop import ToolResult

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentAllowance:
    """One agent's pre-authorization ceiling under one human's consent (AS-9).

    `allowance_zc` is not a balance and not a price — it is the most this
    particular agent may ever spend under this particular human act of
    authorizing it. Raising it requires a fresh human act (a new allowance,
    or a decision elsewhere to grant a bigger one); nothing in this module
    raises it, and nothing in this module can spend past it.

    `spent_zc` is what this agent has already spent under this allowance —
    a running total the caller is expected to source from the ledger (the
    settled cost of every prior approved spend), the same discipline OC-9
    asks of every other guard in this system. `make_agent_approver` treats
    it as a read, never writes it, and never guesses it: an `AgentAllowance`
    constructed with a stale or invented `spent_zc` is a caller bug this
    module has no way to detect, exactly as `marketplace.can_cover` has no
    way to detect a stale `spendable_zc`.
    """

    agent_id: str
    allowance_zc: int
    spent_zc: int


@dataclass(frozen=True)
class SpendAllowance:
    """The principal-neutral generalization of `AgentAllowance` (PF-3).

    Same two fields, same discipline — `allowance_zc` is a ceiling, not a
    balance or a price, and `spent_zc` is a running total the caller must
    source from the ledger, never guessed or invented here — plus a
    `principal_kind` / `principal_id` pair identifying *whose* ceiling this
    is: `"agent"` for an agent spending under a human's pre-authorization
    (AG-3's original case), `"human"` for a human spending directly, or
    `"pool"` for a pool spending on a job's behalf. AS-10 asks for one
    vocabulary across all three; this is that vocabulary. `make_spend_approver`
    treats every field as a read and writes none of them, exactly as
    `make_agent_approver` treats `AgentAllowance`.
    """

    principal_kind: str
    principal_id: str
    allowance_zc: int
    spent_zc: int


def spend_cost_zc(result: ToolResult) -> tuple[int | None, dict[str, int]]:
    """The proposed spend's cost in millicredits, and the grant shape it was
    parsed from — or `(None, {})` if it could not be parsed.

    `result.content` is the JSON-encoded `args` of the proposed tool call —
    exactly what `AgentLoop.step()` builds as the `intent` it hands to
    `approve_spend()` before a spending tool ever runs (see
    `agent_loop.py`'s "Spend gate" docstring section). This function looks
    for exactly three integer fields in those args — `zc_per_hour`,
    `seconds`, `tasks` — the same three keyword arguments
    `marketplace.can_cover` takes, because a tool call that cannot state
    what it would rent, for how long, and how many times has not stated a
    price at all.

    **`None`, never `0`, on anything unparseable**: content that is not
    JSON, a JSON value that is not an object, a missing field, a field that
    is not an integer (booleans included — `True` is not a millicredit
    count), or a field that came through as some other JSON type. This is
    the honesty rule the module docstring states: an unparseable spend costs
    "unknown", and unknown is never treated as free.

    The returned cost is `marketplace.hold_zc(zc_per_hour, seconds) * tasks`
    — the same total `marketplace.can_cover` itself checks a balance
    against — so a caller comparing this number to an allowance is
    comparing it against the identical quantity the solvency check uses,
    not an approximation of it.
    """
    try:
        args = json.loads(result.content)
    except (TypeError, ValueError):
        return None, {}
    if not isinstance(args, dict):
        return None, {}

    parsed: dict[str, int] = {}
    for field in ("zc_per_hour", "seconds", "tasks"):
        value = args.get(field)
        # `bool` is a subclass of `int` in Python; a JSON `true`/`false`
        # here is not a millicredit rate, a duration or a task count, and
        # treating it as 1/0 would silently invent a price.
        if isinstance(value, bool) or not isinstance(value, int):
            return None, {}
        parsed[field] = value

    total_zc = marketplace.hold_zc(
        parsed["zc_per_hour"], parsed["seconds"]
    ) * max(parsed["tasks"], 0)
    return total_zc, parsed


def make_spend_approver(
    *,
    allowance: SpendAllowance,
    read_spendable: Callable[[], int],
    cost_of: Callable[[Any], tuple[int | None, dict[str, int]]],
) -> Callable[[Any], bool]:
    """The general spend approver (PF-3): the same two guards AG-3 built for
    an agent, generalized to run identically for any principal — agent,
    human, or pool — under one vocabulary (AS-10): allowance, spend,
    refusal.

    There is no `db` parameter here at all, not merely an unused one: every
    fact this approver needs arrives already read, through `allowance`,
    `read_spendable()`, and `cost_of()`. A future edit cannot make this
    function touch a database, because there is no name in its signature
    that resolves to one (OC-9: every guard is a ledger read).

    `cost_of` plays the role `spend_cost_zc` plays for `make_agent_approver`:
    given the proposed spend (a `ToolResult`, or whatever shape a given
    principal's spend proposals take), it returns `(cost_zc, grant)` where
    `grant` carries the `zc_per_hour` / `seconds` / `tasks` triple
    `marketplace.can_cover` checks a balance against, or `(None, {})` when
    the proposed spend cannot be parsed — never `(0, {})`. `None` refuses
    immediately, before either guard below runs, exactly as the module
    docstring's honesty rule states.

    Returns `True` only when BOTH hold, checked in this order (OC-4: refuse
    the instant either fails, never queue, never partially approve):

    1. **Parseable.** `cost_of(result)` must find a cost.
    2. **Within allowance.** `allowance.spent_zc + cost <= allowance.allowance_zc`
       — the human's (or pool's, or agent's) consent, bounded, with no path
       through this function past it no matter how solvent the account is.
    3. **Solvent.** `marketplace.can_cover(read_spendable(), ...)` — the same
       ledger-backed solvency check AG-3 wired in, now reachable by every
       principal kind through this one function.
    """

    def approve_spend(result: Any) -> bool:
        cost_zc, grant = cost_of(result)
        if cost_zc is None:
            log.info(
                "%s %s refused: unparseable spend for tool %r "
                "(unknown cost is never treated as zero)",
                allowance.principal_kind,
                allowance.principal_id,
                getattr(result, "name", None),
            )
            return False

        if allowance.spent_zc + cost_zc > allowance.allowance_zc:
            log.info(
                "%s %s refused: over allowance (spent=%d + cost=%d > "
                "allowance=%d) — a ledger read, not a hold",
                allowance.principal_kind,
                allowance.principal_id,
                allowance.spent_zc,
                cost_zc,
                allowance.allowance_zc,
            )
            return False

        spendable_zc = read_spendable()
        solvent = marketplace.can_cover(
            spendable_zc,
            zc_per_hour=grant["zc_per_hour"],
            seconds=grant["seconds"],
            tasks=grant["tasks"],
        )
        if not solvent:
            log.info(
                "%s %s refused: insolvent for tool %r "
                "(spendable=%d, would need cost=%d) — can_cover, a ledger read",
                allowance.principal_kind,
                allowance.principal_id,
                getattr(result, "name", None),
                spendable_zc,
                cost_zc,
            )
        return solvent

    return approve_spend


def make_human_approver(
    *,
    allowance: SpendAllowance,
    read_spendable: Callable[[], int],
    cost_of: Callable[[Any], tuple[int | None, dict[str, int]]],
) -> Callable[[Any], bool]:
    """The human-facing name for `make_spend_approver` (PF-3).

    Behaviorally identical to calling `make_spend_approver` directly — this
    exists only so the site that wires a human's direct spend (not an
    agent's, not a pool's) reads its intent without decoding a generic name.
    `allowance.principal_kind` is expected to be `"human"`, but this function
    does not check that: the caller building the `SpendAllowance` is the one
    place that fact is known, and `make_spend_approver` treats it as an
    opaque label used only for logging, not a value it branches on. No HTTP
    wiring lives here; that is the caller's job.
    """
    return make_spend_approver(allowance=allowance, read_spendable=read_spendable, cost_of=cost_of)


def make_agent_approver(
    db: Any,
    *,
    agent_id: str,
    allowance: AgentAllowance,
    read_spendable: Callable[[], int],
) -> Callable[[ToolResult], bool]:
    """Build the `approve_spend` callback `agent_loop.AgentLoop` calls before
    running any tool whose `spends_money` is true.

    `db` is accepted, and deliberately unused: every fact this approver
    needs — the allowance, the live spendable balance — arrives already
    read, through `allowance` and `read_spendable()`. That is what makes
    "the approver never writes" true by construction rather than by
    discipline: there is no statement in this function that could touch
    `db` even if a future edit tried, because nothing here ever names it.
    (`db` stays in the signature so a caller building this approver reads
    the same shape every other `make_*`/`create_*` factory in this package
    takes, and so a future guard that genuinely needs a fresh read has
    somewhere to put it without changing every call site.)

    **PF-3: this is now a thin adapter.** All the logic — the two guards, the
    honesty rule, OC-4, OC-9 — lives in `make_spend_approver`. This function's
    only job is to translate an `AgentAllowance` into the `SpendAllowance`
    shape (`principal_kind="agent"`, `principal_id=agent_id`) and hand
    `spend_cost_zc` in as `cost_of`. Signature and behavior are unchanged
    from before the refactor — AG-2's loop and every existing caller keep
    working exactly as they did.

    Returns `True` only when BOTH hold, checked in this order (OC-4: refuse
    the instant either fails, never queue, never partially approve):

    1. **Parseable.** `spend_cost_zc(result)` must find a cost. `None`
       refuses immediately — see the honesty rule in the module docstring.
    2. **Within allowance.** `allowance.spent_zc + cost <= allowance.allowance_zc`.
       This is AS-9's bound made numeric: the allowance is the human's
       consent, and spending past it has no path through this function no
       matter how solvent the account is.
    3. **Solvent.** `marketplace.can_cover(read_spendable(), ...)` — the
       call that makes this module AS-17's closer. A spend can be within
       an agent's allowance and still be refused here if the account
       itself cannot cover it, which is the whole reason AS-17 says the
       allowance and the wallet are two different layers.

    For a tool call this loop never gates through here at all — one whose
    `spends_money` is false — `AgentLoop` never calls this. This function
    only ever sees proposed spends.
    """
    del db  # deliberately unused — see docstring above and `make_spend_approver`

    spend_allowance = SpendAllowance(
        principal_kind="agent",
        principal_id=agent_id,
        allowance_zc=allowance.allowance_zc,
        spent_zc=allowance.spent_zc,
    )
    return make_spend_approver(
        allowance=spend_allowance,
        read_spendable=read_spendable,
        cost_of=spend_cost_zc,
    )
