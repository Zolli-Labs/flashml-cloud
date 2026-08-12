"""The Zolli marketplace: a credit ledger, an order book, and priced
entitlements — and a very careful account of what each of those is not.

**A match is not an assignment.** ``flashruntime/scheduler/__init__.py:628``
says it outright: *"FlashML is a PULL system. Nodes claim; the coordinator
never picks a node for a task."* There is no push path in the runtime, so a
row in ``public.matches`` cannot cause a host to run anything. What it does is
make a task ELIGIBLE for a host at an agreed price; the host still has to ask.
Three consequences run through everything below:

- Match states are ``granted -> claimed -> settled``, and ``granted`` is a
  state of its own rather than a flag, because a buyer reading a match as a
  reservation is the most likely misunderstanding this module can cause.
- **Escrow is held on ``claimed``, never on ``granted``.** A grant costs a
  host nothing, so holding at grant would let a host who never shows up lock a
  buyer's balance with no attacker required.
- A matched host that never claims produces no work and no charge. That is
  ``expired``, and it is an ordinary outcome rather than a failure.

**Credits are not `contributions`.** ``contributions.py`` forbids a spendable
balance in its own words — *"It must never grow a `balance`, a `remaining`, or
a `credits`"* — because a name that implies a drawdown promises an exchange
rate this product has not designed and cannot honour. That rule stands and
this module obeys it literally: ``contributions`` remains the immutable record
of accepted work, and **nothing here writes or alters a row in it**. Credits
live in the separate double-entry ledger below, which references accepted work
and never touches the measurement of it. ``test_marketplace.py`` asserts that
no statement in this file writes that table, because the rule has to survive
everybody who read this docstring.

**Settlement is on accepted work, not elapsed time** (hard rule 4). A buyer is
never charged for an attempt that produced no accepted output; a host whose
machine dies mid-task earns nothing and the task requeues. Every other compute
market bills for time and pushes interruption risk onto the buyer. This one
can bill for results because the runtime already knows the difference — and,
since migration 0015, so does Postgres: ``attempts.outcome`` finally
distinguishes a resolved attempt from an in-flight one.

**Integers only.** Every money-shaped value is millicredits as ``int``: 1 ZC
(one hour of the reference class ``gpu-24gb``) is 1000 of them. No float ever
holds a price, a balance or a delta. The one place a non-integer appears is
:func:`effective_price`, which returns a :class:`~fractions.Fraction` — a
RANKING quantity that is compared and then thrown away, never stored and never
charged. It is a Fraction rather than a float so that two hosts whose
effective prices are genuinely equal compare equal, instead of ordering
themselves by the last bit of a division.

The shape follows ``contributions.py`` and ``metrics.py``: **policy first, as
pure functions over values a caller supplied, then the repository.** The
ladder, the grant, the ranking rule and the unproven cap are all policy
statements that must be readable and testable without a database. A rule that
lives inside a SQL string cannot be read or argued with.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

import psycopg

# ---------------------------------------------------------------------------
# The unit, and the grant
# ---------------------------------------------------------------------------

#: Millicredits in one Zolli credit. Balances are stored in the smaller unit
#: for the reason every ledger stores cents: an integer that cannot be divided
#: cannot be rounded, and a rounding error in money is invisible per row and
#: unfixable in aggregate.
MILLICREDITS_PER_CREDIT = 1_000

#: The reference class. **1 ZC buys one hour of ``gpu-24gb``** (design
#: §2.5.1). The unit is deliberately physical: defining "1 ZC = $1" would
#: create exactly the exchange rate M4 forbids and ``contributions.py`` warns
#: against — a number a person could believe they are owed.
REFERENCE_CLASS = "gpu-24gb"

#: **250 ZC, one time per account** (M10, design §2.5.3), in millicredits.
#:
#: Sized against what work costs: a 40-trial HPO sweep at six minutes each on
#: ``gpu-24gb`` is 4 ZC, and a 200-trial sweep on Hopper-class capacity is
#: 330 ZC — more than the whole grant. That is the design test a grant has to
#: pass: large enough that ordinary work is never blocked, small enough that
#: ambitious work forces a choice.
#:
#: One-time is ENFORCED, not advised: the expression unique index in migration
#: 0018 makes a second ``grant`` row for one account impossible at the schema
#: level. A refilling allowance would remove every reason to care what
#: anything costs, and scarcity is the only thing that makes a price mean
#: something.
STARTING_GRANT_ZC = 250_000

# ---------------------------------------------------------------------------
# The capability ladder (design §2.5.2)
#
# Derived from what a machine reported at registration, NEVER user-entered. A
# host who could type their own class would sell a 3070 as `gpu-80gb-hopper`
# for twenty times the price and the buyer would find out when the task OOMs.
# ---------------------------------------------------------------------------

#: Every book. One per class, because machines are not fungible and a single
#: book would price an H100 against a laptop.
CAPABILITY_CLASSES: tuple[str, ...] = (
    "cpu-small",
    "cpu-large",
    "gpu-8gb",
    "gpu-16gb",
    "gpu-24gb",
    "gpu-48gb",
    "gpu-80gb",
    "gpu-80gb-hopper",
)

#: Reference price per class in millicredits per machine-hour, from the
#: RunPod community rates captured 2026-08-11 (design §2.5.2). The RATIOS come
#: from a real market, which is what makes the ladder defensible; the UNIT is
#: an hour of compute, which is what stops any of it implying a dollar
#: somebody could claim.
#:
#: **This is a reference, not a floor and not a ceiling.** Hosts set their own
#: ask (M2) and a zero ask is legal (M13). Nothing in this module compares an
#: ask to these numbers; they exist so the planner and the console can price a
#: plan for a class nobody has listed in yet, and so §2.5.2 has a test.
#:
#: ``cpu-small`` and ``cpu-large`` share a price because §2.5.2 prices ``cpu-*``
#: as one line. They are separate BOOKS (design §4) because a 4-core laptop and
#: a 32-core workstation are not interchangeable supply, but nothing measured
#: says one hour of one is worth more than an hour of the other, so nothing
#: here says it either.
REFERENCE_ZC_PER_HOUR: Mapping[str, int] = {
    "cpu-small": 100,
    "cpu-large": 100,
    "gpu-8gb": 500,
    "gpu-16gb": 1_000,
    "gpu-24gb": 1_000,
    "gpu-48gb": 2_000,
    "gpu-80gb": 4_000,
    "gpu-80gb-hopper": 10_000,
}

#: Cores at which a CPU machine is ``cpu-large`` rather than ``cpu-small``.
#: Eight, because that is where a workstation stops looking like a laptop. It
#: is a threshold rather than a measurement and it is written here, once, so
#: that moving it is a decision somebody makes on purpose.
CPU_LARGE_MIN_CORES = 8

#: VRAM floors in MiB, richest first. Floors rather than nominal sizes because
#: a driver reports usable memory, not the number on the box: a 24GB 4090
#: reports 24564, a 48GB A6000 reports 49140, an 80GB H100 reports 81559.
#:
#: A card between two floors takes the LOWER class. A 40GB A100 is sold as
#: ``gpu-24gb``, which under-claims by a lot — and under-claiming is the only
#: safe direction, because the class is a promise about what a task will find
#: when it lands. (Design §4 lists a ``gpu-40gb`` book that §2.5.2's priced
#: ladder does not have; the priced ladder wins, and the mismatch is noted
#: rather than resolved by inventing a price for it.)
GPU_CLASS_FLOORS_MB: tuple[tuple[int, str], ...] = (
    (79_000, "gpu-80gb"),
    (46_000, "gpu-48gb"),
    (23_000, "gpu-24gb"),
    (15_000, "gpu-16gb"),
    (7_800, "gpu-8gb"),
)

#: Compute capability at which an 80GB card is Hopper rather than Ampere.
#: H100 and H200 report 9.0; A100 reports 8.0. The two are separated by a
#: factor of 2.5 in price and NOT by VRAM, so something else has to tell them
#: apart, and the driver's own compute capability is the only field that does
#: it without pattern-matching a marketing name. A card at 80GB whose compute
#: capability is unreadable is classed ``gpu-80gb`` — under-claiming again.
HOPPER_MIN_COMPUTE_CAPABILITY = 9.0


def capability_class(capabilities: Mapping[str, Any] | None) -> str | None:
    """The class a machine may be listed in, or ``None`` if it cannot be.

    ``capabilities`` is the ``machines.capabilities`` jsonb — the snapshot the
    agent reported at registration, in the shape of
    ``flashruntime.protocol.v1alpha1.NodeCapabilities``. It is read as a
    plain mapping rather than parsed into that model because this repo imports
    the protocol package and nothing else (``test_import_boundary``), and
    because a stored snapshot may predate any given field.

    **The class is the SMALLEST GPU on the machine, not the largest.** A rig
    with a 4090 and a 3070 can only promise 8GB to whichever task lands on
    whichever card, and placement matches on GPU COUNT alone (``GpuInfo``'s own
    docstring says so) — so the machine cannot steer a ``gpu-24gb`` task to the
    24GB device. Classing it by its best card would sell a promise it cannot
    keep, and the buyer would discover that as an OOM.

    **``None`` when a reported GPU has no readable memory.** Not a fallback to
    a CPU class, and the distinction is exactly the one this codebase draws
    everywhere between ``None`` and ``0``: a machine that reports GPUs is a
    machine somebody intends to sell as a GPU host, and quietly filing it in
    the CPU book at a tenth of the price is a worse answer than refusing to
    file it, because the host has no way to notice except from their earnings.
    Refusing is visible and has one obvious fix — upgrade the agent until the
    driver reading lands. A GPU that IS readable and falls below the bottom of
    the ladder (a 6GB card) is a different case entirely: the reading
    succeeded, the market does not price that card, and what the machine has
    to sell is genuinely its CPU. Those fall back to a CPU class.

    A machine with no GPUs and no ``cpu_cores`` is ``cpu-small``: unknown core
    count under-claims to the smaller class, never over-claims to the larger.
    """
    if not isinstance(capabilities, Mapping):
        return None

    gpus = capabilities.get("gpus")
    devices = [g for g in gpus if isinstance(g, Mapping)] if isinstance(gpus, list) else []

    if devices:
        memories = [_gpu_memory_mb(device) for device in devices]
        if any(memory is None for memory in memories):
            # We cannot promise what we could not read. See above.
            return None
        smallest = min(m for m in memories if m is not None)
        for floor, name in GPU_CLASS_FLOORS_MB:
            if smallest >= floor:
                if name == "gpu-80gb" and _is_hopper(devices):
                    return "gpu-80gb-hopper"
                return name
        # Readable, and below the bottom of the ladder. Fall through to the
        # CPU classes: this machine's compute is real, the market just does
        # not price its GPU.

    cores = capabilities.get("cpu_cores")
    if isinstance(cores, (int, float)) and not isinstance(cores, bool):
        return "cpu-large" if cores >= CPU_LARGE_MIN_CORES else "cpu-small"
    return "cpu-small"


def _gpu_memory_mb(device: Mapping[str, Any]) -> int | None:
    """One device's usable memory in MiB, or None if the driver did not say."""
    value = device.get("memory_total_mb")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _is_hopper(devices: Sequence[Mapping[str, Any]]) -> bool:
    """True only if EVERY device reports a Hopper-or-newer compute capability.

    Every, not any: the class is a promise about whichever card the task
    lands on, the same argument that makes the VRAM rule take the minimum. An
    unparseable or absent capability answers False, which sells a Hopper as
    ``gpu-80gb`` — under-claiming, and the only safe direction.
    """
    for device in devices:
        raw = device.get("compute_capability")
        try:
            if float(raw) < HOPPER_MIN_COMPUTE_CAPABILITY:  # type: ignore[arg-type]
                return False
        except (TypeError, ValueError):
            return False
    return True


# ---------------------------------------------------------------------------
# Prices, and the one that is legal at zero
# ---------------------------------------------------------------------------


def is_donated(ask_zc_per_hour: int) -> bool:
    """A zero ask is legal (M13) and is *donated*, not cheap.

    Volunteers are a real supply tier, and pricing them at a floor would
    misrepresent them — so there is no minimum ask anywhere in this module or
    in migration 0018, and the label is a fact about the price rather than a
    separate flag somebody has to remember to set.
    """
    return int(ask_zc_per_hour) == 0


def price_label(ask_zc_per_hour: int) -> str:
    """``'donated'`` or ``'priced'``. What a surface showing an ask must say."""
    return "donated" if is_donated(ask_zc_per_hour) else "priced"


def hold_zc(zc_per_hour: int, seconds: int) -> int:
    """Millicredits to hold for ``seconds`` of machine time at this rate.

    Rounded UP. A hold that is a millicredit short is an under-collateralised
    charge, and the remainder of an over-large hold is released back to the
    buyer at settlement anyway — so the two errors are not symmetric and the
    rounding goes the way whose mistake is refundable.

    Integer arithmetic end to end. ``rate * seconds`` is exact in Python's
    unbounded ints and the division is the only place a fraction could appear;
    ``ceil`` on an integer division keeps it from ever doing so.
    """
    rate = int(zc_per_hour)
    elapsed = int(seconds)
    if rate <= 0 or elapsed <= 0:
        return 0
    return -(-(rate * elapsed) // 3600)  # ceil, in integers


def charge_zc(zc_per_hour: int, accepted_seconds: int, *, held_zc: int) -> int:
    """What accepted work actually costs, never more than what is held.

    The cap is the buyer's protection and it is not a rounding detail: an
    attempt that ran far longer than its estimate would otherwise charge past
    the hold, which means past what the buyer agreed to when the claim was
    made. The excess is the host's risk, and a host who dislikes it can raise
    their ask.

    The cap is also why ``bids.est_task_seconds`` is constrained strictly
    positive in migration 0018. A zero estimate holds nothing, and a charge
    capped at nothing is free compute however long the work ran — the cap and
    the constraint are one mechanism and neither is safe without the other.

    Zero accepted seconds costs zero. That is not a special case — it is hard
    rule 4 arriving as arithmetic.
    """
    return min(hold_zc(zc_per_hour, accepted_seconds), max(int(held_zc), 0))


def can_cover(spendable_zc: int, *, zc_per_hour: int, seconds: int, tasks: int) -> bool:
    """Whether a balance could fund every task of a grant at this rate.

    A SOLVENCY CHECK, not a hold, and the difference is the whole point: a
    hold at grant time would let a host who never claims freeze the money, so
    the balance is only checked here and only committed when somebody actually
    claims. Between the two a buyer may spend the same credits elsewhere,
    which means an insolvent claim remains possible; it fails loudly with
    :class:`InsufficientCredits` rather than quietly charging less.
    """
    return int(spendable_zc) >= hold_zc(zc_per_hour, seconds) * max(int(tasks), 0)


# ---------------------------------------------------------------------------
# Ledger vocabulary
# ---------------------------------------------------------------------------

#: The two accounts every person has. ``escrow`` exists because a hold is a
#: transfer and a transfer needs a counterparty; with one account per person
#: the hold would be a debit with nothing on the other side of it and the
#: ledger could not be checked at all.
ACCOUNT_KINDS: tuple[str, ...] = ("spendable", "escrow")

#: Every legal ``credit_entries.reason``, matching the check constraint in
#: migration 0018.
REASONS: frozenset[str] = frozenset(
    {
        "grant",
        "escrow_hold",
        "escrow_release",
        "escrow_refund",
        "earned_accepted_work",
        "spent_accepted_work",
        "adjustment",
    }
)

#: The reasons that MINT — a movement whose legs are allowed not to sum to
#: zero. ``grant`` creates the credits that seed the economy; ``adjustment``
#: is reserved for a manual correction and nothing in this module writes one,
#: the same way ``abandoned`` is reserved and unwritten in ``attempts.outcome``.
#: Credits are conserved everywhere else, and :func:`post` enforces it.
MINTING_REASONS: frozenset[str] = frozenset({"grant", "adjustment"})

#: Listing states, and the transitions between them. ``withdrawn`` is terminal:
#: a machine taken back off the market cannot be quietly returned to the book
#: by a stale client that still thinks it owns the listing.
LISTING_STATES: tuple[str, ...] = ("open", "paused", "withdrawn")
LISTING_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "open": frozenset({"paused", "withdrawn"}),
    "paused": frozenset({"open", "withdrawn"}),
    "withdrawn": frozenset(),
}

#: Bid states. ``partial`` is first-class because a partial fill across
#: several machines at different prices is the EXPECTED outcome of matching
#: (design §4 rule 5), not a degraded one.
BID_STATES: tuple[str, ...] = ("open", "partial", "filled", "cancelled", "expired")
BID_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "open": frozenset({"partial", "filled", "cancelled", "expired"}),
    "partial": frozenset({"partial", "filled", "cancelled", "expired"}),
    # `filled -> partial` is legal, and it is the pull system showing through
    # again. A bid is "filled" when entitlements exist for all its tasks — NOT
    # when the work happened, because a match cannot make anybody run
    # anything. When a granted match expires unclaimed, that demand is live
    # again and the bid has to be able to say so. Making `filled` terminal
    # would strand a buyer whose matched hosts all went offline: their job
    # still needs machines and their bid could never look for more.
    "filled": frozenset({"partial", "cancelled", "expired"}),
    "cancelled": frozenset(),
    "expired": frozenset(),
}

#: Match states. ``granted -> claimed -> settled`` is the corrected lifecycle
#: from design §0.1, and every one of the five says something a reader needs:
#:
#:   granted  — the entitlement exists and NO MONEY HAS MOVED.
#:   claimed  — a host claimed a lease under it; escrow is held from here.
#:   settled  — the work is done and paid for what was accepted.
#:   refunded — nothing was accepted, or the job was cancelled; all returned.
#:   expired  — granted and never claimed. No work, no charge, not an error.
MATCH_STATES: tuple[str, ...] = (
    "granted",
    "claimed",
    "settled",
    "refunded",
    "expired",
)
MATCH_TRANSITIONS: Mapping[str, frozenset[str]] = {
    # A granted match may expire unclaimed, or be refunded if the job it
    # entitles is cancelled before anybody asks. It may NOT go straight to
    # settled: settlement is on accepted work, and no work can have been
    # accepted under an entitlement nobody claimed.
    "granted": frozenset({"claimed", "expired", "refunded"}),
    "claimed": frozenset({"settled", "refunded"}),
    "settled": frozenset(),
    "refunded": frozenset(),
    "expired": frozenset(),
}

#: Match states in which a machine is actually entitled to pull work.
LIVE_MATCH_STATES: frozenset[str] = frozenset({"granted", "claimed"})


def is_legal_match_transition(from_state: str, to_state: str) -> bool:
    """Whether the match state machine has an edge for this pair."""
    return to_state in MATCH_TRANSITIONS.get(from_state, frozenset())


class LedgerImbalance(ValueError):
    """A movement's legs did not sum to zero and it was not a mint.

    Raised before anything is written. A ledger that accepts one unbalanced
    movement cannot be audited afterwards, because from then on every check
    fails and none of them says which write was the bad one.
    """


class LedgerReplayConflict(RuntimeError):
    """Some legs of a movement already existed and some did not.

    Both legs of a transfer are written by one statement inside one
    transaction, so this is not reachable by any ordinary retry — it means
    something wrote half a movement under the same idempotency key, and the
    balances cannot be repaired by guessing which half is right.
    """


class InsufficientCredits(ValueError):
    """The debit would have taken a balance below zero.

    A database answer, not a Python one: the ``balance_zc >= 0`` check in
    migration 0018 is what refuses it, so there is no window between reading a
    balance and spending it in which two claims could both be affordable.
    """


class UnknownAccount(LookupError):
    """A leg named an account that does not exist."""


class IllegalTransition(ValueError):
    """A state machine has no edge for the pair named.

    Distinct from "you lost the race", which is not an error: a compare-and-set
    that finds the row already moved returns False, and a swapped argument pair
    reported as a lost race is a retry loop that never ends.
    """


class UnclassifiableMachine(ValueError):
    """The machine's reported capabilities do not place it in any class.

    See :func:`capability_class`: a machine reporting GPUs whose memory the
    driver never read cannot be listed, because every GPU class is a promise
    about VRAM and there is no reading to make it from.
    """


class AlreadyListed(ValueError):
    """The machine already has an open listing.

    Two open listings for one machine are two prices for the same hour with no
    rule for which one a match executes at. That is not more supply; it is an
    ambiguity, and the partial unique index in 0018 refuses it.
    """


# ---------------------------------------------------------------------------
# Matching (design §4)
#
# The gates come first, ALWAYS. This engine never sees a machine that the
# scheduler's placement gates — capability, pool, GPU, data locality,
# dependencies, isolation, exclusions — would refuse; the caller filters, and
# `match_bid` receives only what may legally run. A price never buys past a
# safety property, which is what lets the marketplace be wrong about money
# without ever being wrong about safety.
# ---------------------------------------------------------------------------

#: The share of one job's tasks that may go to hosts with no acceptance rate.
#:
#: THIS CAP IS THE WHOLE DEFENCE, and it exists because the obvious
#: alternative is forbidden. ``metrics.acceptance_rates`` returns ``None``
#: below five resolved observations and nothing may substitute a number for
#: it: substituting 1.0 makes unproven hosts win every book, and substituting
#: anything less means they never get work, never resolve an attempt, and
#: never stop being unproven. So an unproven host ranks on its ask alone —
#: which does rank it as if it were perfect — and the correction is a bound on
#: BLAST RADIUS rather than a fudged denominator. A quarter of a job's tasks
#: can be lost to newcomers and the job still finishes; the rest is placed on
#: hosts with evidence behind them.
#:
#: A Fraction, not a float, so `share * tasks` is exact and the cap for a given
#: task count is the same number on every machine that computes it.
UNPROVEN_TASK_SHARE = Fraction(1, 4)


def unproven_task_budget(tasks_wanted: int) -> int:
    """How many of a job's tasks may be matched to unproven hosts.

    **At least one, always.** ``floor(1/4 * 1)`` is zero, which would mean a
    single-task job never touches a new host — and since a host needs five
    RESOLVED attempts before ``metrics.acceptance_rates`` will report a number,
    a market that never gives a newcomer a first task has no newcomers in it
    after the first week. One task is also the smallest blast radius available,
    so the floor costs the buyer the least the rule can cost anybody.

    Never more than the job has: a two-task job cannot spend three.
    """
    tasks = max(int(tasks_wanted), 0)
    if tasks == 0:
        return 0
    return max(1, min(tasks, int(UNPROVEN_TASK_SHARE * tasks)))


@dataclass(frozen=True)
class Ask:
    """One candidate offer, already through the placement gates.

    ``acceptance_rate`` is the host's rate IN THIS CLASS, straight from
    ``metrics.acceptance_rates``, and ``None`` means "fewer than five resolved
    attempts". It arrives as None and stays None: nothing in this module
    replaces it, defaults it, or reports a number in its place.
    """

    listing_id: str
    machine_id: str
    host_id: str
    ask_zc_per_hour: int
    max_concurrent_tasks: int = 1
    acceptance_rate: float | None = None

    @property
    def unproven(self) -> bool:
        return self.acceptance_rate is None


@dataclass(frozen=True)
class Fill:
    """One machine's share of a bid, at that machine's own ask."""

    listing_id: str
    machine_id: str
    host_id: str
    tasks: int
    agreed_zc_per_hour: int
    unproven_host: bool

    @property
    def donated(self) -> bool:
        return is_donated(self.agreed_zc_per_hour)


@dataclass(frozen=True)
class MatchPlan:
    """What a bid would buy, and what it would not.

    ``tasks_unfilled`` is a first-class number rather than an error: a partial
    fill is the expected outcome (design §4 rule 5), and a bid that filled six
    of forty tasks has bought six tasks — the console says so and the bid stays
    live for the rest.
    """

    fills: tuple[Fill, ...]
    tasks_filled: int
    tasks_unfilled: int
    unproven_tasks: int
    unproven_task_cap: int


def effective_price(
    ask_zc_per_hour: int, acceptance_rate: float | None
) -> Fraction | None:
    """ZC per hour of ACCEPTED work, the number a buyer actually pays against.

    ``ask / acceptance_rate``: a machine asking 0.22 with a 0.81 rate is more
    expensive per result than one asking 0.30 with 0.98, and conflating the
    two is the classic marketplace mistake (design §3).

    Three cases that are not division:

    - **A donated ask is 0, whatever the rate.** Free work that fails is still
      free, and ``0 / rate`` is 0 for every rate that exists — including the
      rate that does not, which is why this case comes first.
    - **An unproven host ranks on its ask alone.** Not ``ask / 1.0`` under
      another name in any sense a reader could act on: nothing is stored,
      reported or displayed as a rate, and the over-ranking this produces is
      answered by :data:`UNPROVEN_TASK_SHARE` rather than by a denominator
      somebody made up. See that constant.
    - **A rate of exactly 0.0 returns None**, meaning "no finite bid clears
      this". It is not an exclusion, and the distinction matters because
      ``scheduler/__init__.py:637`` is explicit that reliability may rank and
      must never exclude: this host is in the book, it is ranked, and a buyer
      would need to offer infinity per accepted result because that is what
      the arithmetic says a host who has never had anything accepted costs.
      Its ask being 0 puts it back in the market immediately, which is the
      right answer for a volunteer with a bad week.

    The rate is converted through its own 4-decimal rounding — the precision
    ``metrics`` actually reports — so the result is exact rather than
    float-shaped.
    """
    ask = int(ask_zc_per_hour)
    if ask == 0:
        return Fraction(0)
    if acceptance_rate is None:
        return Fraction(ask)
    scaled = round(float(acceptance_rate) * 10_000)
    if scaled <= 0:
        return None
    return Fraction(ask * 10_000, scaled)


def _rank_key(ask: Ask) -> tuple[int, Fraction, int, str]:
    """Cheapest effective price first; unclearable last.

    The tiebreaks are not decoration. Equal asks are the ordinary case in a
    thin book, and without a deterministic order the same bid against the same
    book would fill different machines on two runs — which looks like the
    engine changing its mind and is not. Ask, then listing id, both stable.
    """
    price = effective_price(ask.ask_zc_per_hour, ask.acceptance_rate)
    if price is None:
        return (1, Fraction(0), int(ask.ask_zc_per_hour), str(ask.listing_id))
    return (0, price, int(ask.ask_zc_per_hour), str(ask.listing_id))


def rank_asks(asks: Iterable[Ask]) -> list[Ask]:
    """The book, in the order a bid consumes it."""
    return sorted(asks, key=_rank_key)


def match_bid(
    *,
    max_zc_per_hour: int,
    tasks_wanted: int,
    asks: Iterable[Ask],
    workspace_reserved: Collection[str] = (),
) -> MatchPlan:
    """Pair one bid against the book. Pure policy; no database, no side effects.

    The rule, from design §4:

    1. Candidates arrive already filtered through the placement gates.
    2. Rank by EFFECTIVE price (:func:`effective_price`), not headline ask.
    3. Match while ``bid >= effective_price``.
    4. **Execute at the host's ask.** The host gets the number they posted and
       the buyer never pays more than they bid. There is no spread and nothing
       for Zolli to take — no fee in v1 and no mechanism to add one (M11).
    5. Partial fills across several machines at several prices are normal and
       expected. A 40-trial sweep matching eight machines at four prices is
       the point, not a degradation.

    ``workspace_reserved`` is the ONE exclusion here, and it is not a
    reliability judgement: M12 says a workspace member may list on the open
    market while still serving their team free, and **workspace demand takes
    priority** — so a machine its own workspace currently needs is withheld
    from the open book entirely rather than merely priced out of it. The
    caller supplies the set because "does this team have queued work right
    now" is a question about the job queue, not about the offer; a column on
    the listing would be stale the moment somebody submitted.

    Nothing else is excluded. A host with a terrible acceptance rate stays in
    the book and simply costs more per accepted result, which is the whole
    difference between ranking and refusing.
    """
    reserved = {str(machine_id) for machine_id in workspace_reserved}
    bid = int(max_zc_per_hour)
    wanted = max(int(tasks_wanted), 0)
    budget = unproven_task_budget(wanted)

    fills: list[Fill] = []
    remaining = wanted
    unproven_used = 0

    for ask in rank_asks(a for a in asks if str(a.machine_id) not in reserved):
        if remaining <= 0:
            break

        price = effective_price(ask.ask_zc_per_hour, ask.acceptance_rate)
        if price is None or price > bid:
            # Does not clear. `continue` rather than `break` although the book
            # is sorted by exactly this quantity: the test is per candidate, so
            # a future change to the ordering cannot silently start matching
            # something the buyer never agreed to pay for.
            continue

        take = min(remaining, max(int(ask.max_concurrent_tasks), 0))
        if ask.unproven:
            take = min(take, budget - unproven_used)
        if take <= 0:
            continue

        if ask.unproven:
            unproven_used += take
        remaining -= take
        fills.append(
            Fill(
                listing_id=str(ask.listing_id),
                machine_id=str(ask.machine_id),
                host_id=str(ask.host_id),
                tasks=take,
                agreed_zc_per_hour=int(ask.ask_zc_per_hour),
                unproven_host=ask.unproven,
            )
        )

    return MatchPlan(
        fills=tuple(fills),
        tasks_filled=wanted - remaining,
        tasks_unfilled=remaining,
        unproven_tasks=unproven_used,
        unproven_task_cap=budget,
    )


def book(asks: Iterable[Ask], bids: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The order book as a surface would render it.

    ``best_ask`` and ``best_bid`` are ``None`` for an empty side, never 0. A
    zero ask is legal and means donated (M13), so "the best ask is free" and
    "nobody is selling" are different facts and must be different values —
    collapsing them draws a market full of volunteers exactly like a market
    with no supply at all.
    """
    ranked = rank_asks(asks)
    bid_prices = [int(b["max_zc_per_hour"]) for b in bids]
    return {
        "asks": [
            {
                "listing_id": a.listing_id,
                "machine_id": a.machine_id,
                "ask_zc_per_hour": a.ask_zc_per_hour,
                "label": price_label(a.ask_zc_per_hour),
                "acceptance_rate": a.acceptance_rate,
                "effective_price_zc": _as_millicredits(
                    effective_price(a.ask_zc_per_hour, a.acceptance_rate)
                ),
            }
            for a in ranked
        ],
        "best_ask_zc": ranked[0].ask_zc_per_hour if ranked else None,
        "best_bid_zc": max(bid_prices) if bid_prices else None,
        "open_listings": len(ranked),
        "open_bids": len(bid_prices),
    }


def _as_millicredits(price: Fraction | None) -> int | None:
    """An effective price rounded to a whole millicredit for DISPLAY only.

    Rounded up, and never used to charge anybody: matching compares the exact
    Fraction and executes at the integer ask. A displayed number that had been
    rounded before the comparison would show a host clearing a bid it did not.
    """
    if price is None:
        return None
    return math.ceil(price)


# ---------------------------------------------------------------------------
# Repository
#
# `db` is a live psycopg connection in autocommit mode, opened by
# `db.connect()`, exactly as in `db.py` and `sandbox_sessions.py`. Every read
# scoped to a person folds the owner into the query itself rather than
# filtering afterwards in Python: leaving it out has to be a missing argument,
# not a missing `if`. An owner-scoped read returns None both for a row that
# does not exist and for one belonging to somebody else, and the route above
# answers 404 to both — a 403 for "exists but not yours" confirms to a guesser
# that the id is real.
#
# NOTHING BELOW WRITES `public.contributions`. Two statements read it (a
# host's earnings history joins accepted work to what it paid); none inserts,
# updates or deletes. See the module docstring, and the test that enforces it.
# ---------------------------------------------------------------------------

LISTING_COLUMNS: tuple[str, ...] = (
    "id",
    "machine_id",
    "owner_id",
    "capability_class",
    "ask_zc_per_hour",
    "max_concurrent_tasks",
    "available_from",
    "available_until",
    "state",
    "created_at",
    "updated_at",
)

BID_COLUMNS: tuple[str, ...] = (
    "id",
    "job_id",
    "owner_id",
    "capability_class",
    "max_zc_per_hour",
    "tasks_wanted",
    "est_task_seconds",
    "deadline",
    "state",
    "created_at",
    "updated_at",
)

MATCH_COLUMNS: tuple[str, ...] = (
    "id",
    "bid_id",
    "listing_id",
    "machine_id",
    "buyer_id",
    "host_id",
    "capability_class",
    "agreed_zc_per_hour",
    "tasks_assigned",
    "est_task_seconds",
    "unproven_host",
    "state",
    "granted_at",
    "claimed_at",
    "closed_at",
)

_LISTING_SELECT = ", ".join(LISTING_COLUMNS)
_BID_SELECT = ", ".join(BID_COLUMNS)
_MATCH_SELECT = ", ".join(MATCH_COLUMNS)


@dataclass(frozen=True)
class Leg:
    """One side of a movement: an account, a signed delta, and why."""

    account_id: str
    delta_zc: int
    reason: str


# --- accounts and the ledger ------------------------------------------------


def ensure_accounts(db: psycopg.Connection, owner_id: str) -> dict[str, dict[str, Any]]:
    """This person's two accounts, creating them if they do not exist yet.

    Idempotent by ``on conflict do nothing`` against the ``(owner_id, kind)``
    unique constraint, so two requests racing to open an account for the same
    new user produce one pair, not two and not an error. Called by every write
    path rather than at signup: an account that only exists if some earlier
    step ran is an account that is missing exactly when somebody's first bid
    needs it.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.credit_accounts (owner_id, kind)
            values (%s::uuid, 'spendable'), (%s::uuid, 'escrow')
            on conflict (owner_id, kind) do nothing
            """,
            (owner_id, owner_id),
        )
        cur.execute(
            "select id, owner_id, kind, balance_zc, created_at, updated_at"
            "  from public.credit_accounts where owner_id = %s::uuid",
            (owner_id,),
        )
        return {str(row["kind"]): row for row in cur.fetchall()}


def balances(db: psycopg.Connection, owner_id: str) -> dict[str, int]:
    """``{'spendable': n, 'escrow': n}`` in millicredits, zero for a stranger.

    Zero is a real answer, not a 404: somebody who just signed up has no
    credits, that is the most common state this panel renders, and a field
    that disappears when it has nothing to say arrives at the console as
    ``undefined`` and draws as a blank tile that looks like a loading state.
    """
    with db.cursor() as cur:
        cur.execute(
            "select kind, balance_zc from public.credit_accounts"
            " where owner_id = %s::uuid",
            (owner_id,),
        )
        found = {str(row["kind"]): int(row["balance_zc"]) for row in cur.fetchall()}
    return {kind: found.get(kind, 0) for kind in ACCOUNT_KINDS}


def post(
    db: psycopg.Connection,
    *,
    ref_type: str | None,
    ref_id: str | None,
    legs: Sequence[Leg],
) -> int:
    """Write one movement. The only function in this module that moves credits.

    Returns the number of entries written — **0 means "already posted"**, not
    "failed". That is the idempotency contract: the expression unique index in
    0018 keys on ``(reason, ref_type, ref_id, account_id)`` with nulls folded
    to the empty string, so a retried callback, a restarted driver and a
    double-delivered webhook all produce one movement.

    Four properties, in the order they are enforced:

    **Conservation.** The legs must sum to zero unless every one of them is a
    minting reason. A ledger that accepts one unbalanced movement cannot be
    audited afterwards, because from then on every check fails and none of
    them says which write was the bad one. Mixing a mint and a transfer in one
    movement is refused for the same reason: the sum would tell you nothing.

    **Deterministic lock order.** The accounts are locked ``for update`` in id
    order before anything is written. Two settlements that touch the same pair
    of accounts from opposite directions are the ordinary shape of a busy
    market, and without a fixed order they deadlock — which surfaces as a
    random 500 on a route that was tested and worked.

    **Balances move by arithmetic in SQL**, ``balance_zc = balance_zc + %s``,
    never read-modify-write in Python. A read followed by a write has a window
    in it, and the window is where two concurrent holds both see the old
    balance and both succeed.

    **Overdraft is refused by the database.** ``balance_zc >= 0`` in 0018 is
    what raises, and it is translated to :class:`InsufficientCredits` here.
    Checking the balance in Python first would be a second, weaker guard that
    only appears to work.
    """
    if not legs:
        return 0

    for leg in legs:
        if leg.reason not in REASONS:
            raise LedgerImbalance(f"{leg.reason!r} is not a ledger reason")

    minting = [leg for leg in legs if leg.reason in MINTING_REASONS]
    if minting and len(minting) != len(legs):
        raise LedgerImbalance(
            "a movement may not mix a minting reason with a transfer; the sum "
            "of its legs would then prove nothing either way"
        )
    total = sum(int(leg.delta_zc) for leg in legs)
    if not minting and total != 0:
        raise LedgerImbalance(
            f"legs sum to {total}, not 0 — every debit needs a matching credit"
        )

    account_ids = sorted({str(leg.account_id) for leg in legs})

    try:
        with db.transaction():
            with db.cursor() as cur:
                cur.execute(
                    "select id from public.credit_accounts"
                    " where id = any(%s::uuid[]) order by id for update",
                    (account_ids,),
                )
                locked = {str(row["id"]) for row in cur.fetchall()}
                missing = [a for a in account_ids if a not in locked]
                if missing:
                    raise UnknownAccount(f"no credit account {missing[0]}")

                values = ", ".join(["(%s::uuid, %s, %s, %s, %s)"] * len(legs))
                params: list[Any] = []
                for leg in legs:
                    params.extend(
                        [
                            str(leg.account_id),
                            int(leg.delta_zc),
                            leg.reason,
                            ref_type,
                            ref_id,
                        ]
                    )
                cur.execute(
                    f"""
                    insert into public.credit_entries
                        (account_id, delta_zc, reason, ref_type, ref_id)
                    values {values}
                    on conflict do nothing
                    returning id, account_id, delta_zc
                    """,
                    params,
                )
                written = cur.fetchall()
                if not written:
                    return 0
                if len(written) != len(legs):
                    raise LedgerReplayConflict(
                        f"{len(written)} of {len(legs)} legs were new for "
                        f"({ref_type}, {ref_id}); half a movement already exists"
                    )

                for row in written:
                    cur.execute(
                        "update public.credit_accounts"
                        "   set balance_zc = balance_zc + %s, updated_at = now()"
                        " where id = %s",
                        (int(row["delta_zc"]), row["account_id"]),
                    )
                return len(written)
    except psycopg.errors.CheckViolation as exc:
        if (exc.diag.constraint_name or "").endswith("balance_zc_check"):
            raise InsufficientCredits(
                "the debit would take a balance below zero"
            ) from exc
        raise


def grant_starting_credits(db: psycopg.Connection, owner_id: str) -> int:
    """The one-time 250 ZC grant (M10). Returns what was granted, or 0.

    **0 means "already granted", and it is not an error.** Every caller may
    call this on every sign-in; the unique index is what makes the second call
    a no-op, so "have they had it?" never has to be asked as a separate
    question whose answer could be stale by the time it is used.

    Referenced as ``('account', <spendable account id>)`` so the row says what
    it is about, but the index would hold the line even if it said nothing:
    nulls are folded to the empty string precisely so that a reason with
    nothing natural to reference is still unique per account. That is the trap
    migration 0003 documented on ``contributions`` and it is the same trap
    here — the grant is the one reason with no natural ref and the one reason
    that must never be written twice.
    """
    accounts = ensure_accounts(db, owner_id)
    spendable = accounts["spendable"]
    written = post(
        db,
        ref_type="account",
        ref_id=str(spendable["id"]),
        legs=[Leg(str(spendable["id"]), STARTING_GRANT_ZC, "grant")],
    )
    return STARTING_GRANT_ZC if written else 0


def held_for_lease(db: psycopg.Connection, *, owner_id: str, lease_id: str) -> int:
    """Millicredits still held against one lease, read out of the ledger.

    Not tracked in a column anywhere, deliberately. A second count of
    something the entries already know would eventually disagree with them,
    and the number that is wrong is never the one you are looking at. The sum
    of a lease's escrow legs is the hold: it rises on ``escrow_hold`` and
    falls to exactly zero when the lease settles or is refunded, so a non-zero
    answer for a finished lease is itself the alarm.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select coalesce(sum(e.delta_zc), 0) as held
              from public.credit_entries e
              join public.credit_accounts a on a.id = e.account_id
             where a.owner_id = %s::uuid
               and a.kind = 'escrow'
               and e.ref_type = 'attempt'
               and e.ref_id = %s
            """,
            (owner_id, lease_id),
        )
        row = cur.fetchone()
    return int(row["held"]) if row else 0


def lease_settlement(db: psycopg.Connection, lease_id: str) -> dict[str, int]:
    """What one lease actually cost and earned, from the entries themselves.

    The audit answer to "why was I charged this?", and the only honest one:
    it is the ledger, not a summary of it.

    Grouped by ``(reason, kind)`` and read ONE SIDE of each movement, which is
    not a detail: every movement here is balanced, so summing both legs of a
    hold gives zero — a true statement about conservation and a useless one
    about what was held. The escrow account's side is what "held" means; the
    spendable side is what the buyer and the host actually saw move.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select e.reason, a.kind, coalesce(sum(e.delta_zc), 0) as total
              from public.credit_entries e
              join public.credit_accounts a on a.id = e.account_id
             where e.ref_type = 'attempt' and e.ref_id = %s
             group by e.reason, a.kind
            """,
            (lease_id,),
        )
        totals = {
            (str(row["reason"]), str(row["kind"])): int(row["total"])
            for row in cur.fetchall()
        }
    return {
        "held_zc": totals.get(("escrow_hold", "escrow"), 0),
        "charged_zc": -totals.get(("spent_accepted_work", "escrow"), 0),
        "earned_zc": totals.get(("earned_accepted_work", "spendable"), 0),
        "released_zc": totals.get(("escrow_release", "spendable"), 0),
        "refunded_zc": totals.get(("escrow_refund", "spendable"), 0),
    }


def verify_ledger(db: psycopg.Connection) -> list[dict[str, Any]]:
    """Every account whose ``balance_zc`` disagrees with its entries.

    The invariant is ``balance_zc == sum(delta_zc)``, always, and this is how
    it is checked rather than how it is imposed: there is no trigger, because
    a rule expressed in a trigger cannot be read, argued with, or tested
    without a database — the same call 0014 makes about state machines. The
    balance column is maintained by :func:`post` in the same transaction as
    the entries that move it, and this function is the evidence that the two
    agree. An empty list is the healthy answer and it is cheap to ask for;
    anything else is a bug that has already happened and must not be repaired
    by writing the sum over the balance.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select a.id, a.owner_id, a.kind, a.balance_zc,
                   coalesce(sum(e.delta_zc), 0) as entries_zc
              from public.credit_accounts a
              left join public.credit_entries e on e.account_id = a.id
             group by a.id, a.owner_id, a.kind, a.balance_zc
            having a.balance_zc <> coalesce(sum(e.delta_zc), 0)
             order by a.id
            """
        )
        return list(cur.fetchall())


def ledger_totals(db: psycopg.Connection) -> dict[str, int]:
    """Credits minted, and credits held across every live account.

    Conservation, stated as a query: the two are equal unless somebody wrote
    an unbalanced movement. **Live accounts only** — deleting a person cascades
    their accounts and entries away (0018's header says why the foreign keys
    must cascade), so this is not a claim about every credit ever minted and
    no reader should take it as one.
    """
    with db.cursor() as cur:
        cur.execute("select coalesce(sum(balance_zc), 0) as t from public.credit_accounts")
        held = int(cur.fetchone()["t"])
        cur.execute(
            "select coalesce(sum(delta_zc), 0) as t from public.credit_entries"
            " where reason = 'grant'"
        )
        minted = int(cur.fetchone()["t"])
    return {"minted_zc": minted, "held_zc": held}


# --- listings ---------------------------------------------------------------


def create_listing(
    db: psycopg.Connection,
    *,
    machine_id: str,
    owner_id: str,
    ask_zc_per_hour: int,
    max_concurrent_tasks: int = 1,
    available_from: Any = None,
    available_until: Any = None,
) -> dict[str, Any]:
    """List a machine on the open market at the host's own ask (M2).

    **There is no ``capability_class`` argument and there will not be one.**
    The class is computed here from ``machines.capabilities`` — the snapshot
    the agent itself reported — because a host who could name their own class
    would sell a 3070 as ``gpu-80gb-hopper``. If those capabilities do not
    place the machine (:func:`capability_class` returns None), the listing is
    refused with :class:`UnclassifiableMachine` rather than filed under a
    guess.

    Owner-scoped by the same rule as everything else: the machine lookup
    carries ``owner_id``, so listing somebody else's machine is
    indistinguishable from listing one that does not exist.

    A price observation is written in the same transaction (cause
    ``listing``). Recording it here rather than deriving the series later is
    the difference between evidence and a story — see
    :func:`record_price_observation`.
    """
    ask = int(ask_zc_per_hour)
    if ask < 0:
        raise ValueError("an ask may be zero (donated) but never negative")

    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                "select capabilities from public.machines"
                " where id = %s::uuid and owner_id = %s::uuid",
                (machine_id, owner_id),
            )
            machine = cur.fetchone()
            if machine is None:
                raise LookupError("no such machine for this owner")

            klass = capability_class(machine["capabilities"])
            if klass is None:
                raise UnclassifiableMachine(
                    "this machine reports GPUs whose memory its driver never "
                    "read, so no class can be promised for it"
                )

            try:
                cur.execute(
                    f"""
                    insert into public.listings
                        (machine_id, owner_id, capability_class,
                         ask_zc_per_hour, max_concurrent_tasks,
                         available_from, available_until, state)
                    values (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, 'open')
                    returning {_LISTING_SELECT}
                    """,
                    (
                        machine_id,
                        owner_id,
                        klass,
                        ask,
                        int(max_concurrent_tasks),
                        available_from,
                        available_until,
                    ),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise AlreadyListed(
                    "this machine already has an open listing"
                ) from exc
            row = cur.fetchone()
            assert row is not None
            _record_observation_locked(cur, klass, "listing")
    return row


def set_listing_state(
    db: psycopg.Connection,
    *,
    listing_id: str,
    owner_id: str,
    from_state: str,
    to_state: str,
) -> bool:
    """Move a listing, but only if it is still where the caller left it.

    Returns True if this call moved it and False if it did not — because it is
    in some other state already, does not exist, or is not this owner's.
    **Losing is not an error**, and neither is "not yours": both answer False
    and the route above answers the same thing to both, so a guesser cannot
    learn that an id is real.

    One statement does the check and the move together. A ``select`` followed
    by an ``update`` has a window in it, and here the window is a listing
    withdrawn by its owner and re-opened a millisecond later by a stale tab.

    Naming a pair the state machine has no edge for raises
    :class:`IllegalTransition` instead of returning False: a swapped argument
    pair reported as a lost race is a retry loop that never ends.
    """
    if to_state not in LISTING_TRANSITIONS.get(from_state, frozenset()):
        raise IllegalTransition(f"listing {from_state} -> {to_state}")

    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                """
                update public.listings
                   set state = %s, updated_at = now()
                 where id = %s::uuid and owner_id = %s::uuid and state = %s
                returning capability_class
                """,
                (to_state, listing_id, owner_id, from_state),
            )
            row = cur.fetchone()
            if row is None:
                return False
            _record_observation_locked(cur, str(row["capability_class"]), "listing")
    return True


def pause_listing(db: psycopg.Connection, *, listing_id: str, owner_id: str) -> bool:
    """Take a listing out of the book without giving it up."""
    return set_listing_state(
        db, listing_id=listing_id, owner_id=owner_id,
        from_state="open", to_state="paused",
    )


def resume_listing(db: psycopg.Connection, *, listing_id: str, owner_id: str) -> bool:
    """Put a paused listing back in the book at the price it already had."""
    return set_listing_state(
        db, listing_id=listing_id, owner_id=owner_id,
        from_state="paused", to_state="open",
    )


def withdraw_listing(
    db: psycopg.Connection, *, listing_id: str, owner_id: str, from_state: str = "open"
) -> bool:
    """Retire a listing for good. Terminal — a withdrawn listing never reopens."""
    return set_listing_state(
        db, listing_id=listing_id, owner_id=owner_id,
        from_state=from_state, to_state="withdrawn",
    )


def listings_for_owner(db: psycopg.Connection, owner_id: str) -> list[dict[str, Any]]:
    """This host's listings, newest first. Empty is a real answer."""
    with db.cursor() as cur:
        cur.execute(
            f"select {_LISTING_SELECT} from public.listings"
            " where owner_id = %s::uuid order by created_at desc, id",
            (owner_id,),
        )
        return list(cur.fetchall())


def open_asks(
    db: psycopg.Connection,
    capability_class_name: str,
    *,
    acceptance_rates: Mapping[tuple[str, str], float | None] | None = None,
) -> list[Ask]:
    """The live ask side of one book, as :class:`Ask` values.

    Joined to ``machines`` on ``status = 'active'``: a listing whose machine
    was revoked is not supply, and leaving it in the book would let a
    withdrawn host keep winning matches nobody can claim.

    ``acceptance_rates`` is keyed by ``(machine_id, capability_class)`` — the
    exact key ``metrics.acceptance_rates`` produces, and never aggregated
    across classes, because a host that accepts 95% of CPU work and 40% of
    GPU work is not a 0.9 host. A machine that is not in the mapping is
    unproven and its rate stays ``None``; this function will not invent one,
    and a caller that passes nothing gets a book of unproven hosts rather than
    a book of perfect ones.
    """
    rates = acceptance_rates or {}
    with db.cursor() as cur:
        cur.execute(
            """
            select l.id, l.machine_id, l.owner_id, l.ask_zc_per_hour,
                   l.max_concurrent_tasks
              from public.listings l
              join public.machines m on m.id = l.machine_id
             where l.capability_class = %s
               and l.state = 'open'
               and m.status = 'active'
             order by l.ask_zc_per_hour, l.id
            """,
            (capability_class_name,),
        )
        rows = cur.fetchall()

    return [
        Ask(
            listing_id=str(row["id"]),
            machine_id=str(row["machine_id"]),
            host_id=str(row["owner_id"]),
            ask_zc_per_hour=int(row["ask_zc_per_hour"]),
            max_concurrent_tasks=int(row["max_concurrent_tasks"]),
            acceptance_rate=rates.get(
                (str(row["machine_id"]), capability_class_name)
            ),
        )
        for row in rows
    ]


# --- bids -------------------------------------------------------------------


def create_bid(
    db: psycopg.Connection,
    *,
    job_id: str,
    owner_id: str,
    capability_class_name: str,
    max_zc_per_hour: int,
    tasks_wanted: int,
    est_task_seconds: int,
    deadline: Any = None,
) -> dict[str, Any]:
    """Post a job's willingness to pay.

    ``capability_class_name`` is the class the JOB needs — a property of the
    work, resolved by the caller from the job's requirements, not something
    the buyer is choosing about somebody else's hardware. It is validated
    against the ladder here and by a check constraint in 0018.

    A bid moves no credits. The balance is only committed when a host claims,
    which is why :func:`can_cover` exists as a check a caller may make and not
    as a gate this function imposes: refusing a bid on a balance would be a
    reservation in all but name.
    """
    if capability_class_name not in CAPABILITY_CLASSES:
        raise ValueError(f"{capability_class_name!r} is not a capability class")
    if int(max_zc_per_hour) < 0:
        raise ValueError("a bid may be zero (donated capacity only) but never negative")
    if int(tasks_wanted) <= 0:
        raise ValueError("a bid must want at least one task")
    if int(est_task_seconds) <= 0:
        # Not a validation nicety. The hold is sized from this, and the charge
        # is capped at the hold, so a zero estimate is free compute for as
        # long as the task cares to run. There is no default for the same
        # reason: saying nothing must not be a way to reach it.
        raise ValueError(
            "a bid must estimate how long one task takes; a zero estimate "
            "holds nothing and settlement is capped at what is held"
        )

    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                f"""
                insert into public.bids
                    (job_id, owner_id, capability_class, max_zc_per_hour,
                     tasks_wanted, est_task_seconds, deadline, state)
                values (%s, %s::uuid, %s, %s, %s, %s, %s, 'open')
                returning {_BID_SELECT}
                """,
                (
                    job_id,
                    owner_id,
                    capability_class_name,
                    int(max_zc_per_hour),
                    int(tasks_wanted),
                    int(est_task_seconds),
                    deadline,
                ),
            )
            row = cur.fetchone()
            assert row is not None
            _record_observation_locked(cur, capability_class_name, "bid")
    return row


def set_bid_state(
    db: psycopg.Connection,
    *,
    bid_id: str,
    from_state: str,
    to_state: str,
    owner_id: str | None = None,
) -> bool:
    """Compare-and-set on a bid, optionally scoped to its owner.

    ``owner_id`` is optional because two different callers move bids: the
    person cancelling their own (scoped) and the matching engine marking one
    partial or filled (not scoped, and not acting for anybody). Making the
    argument optional rather than having two functions keeps one place where
    the legality of a transition is checked.
    """
    if to_state not in BID_TRANSITIONS.get(from_state, frozenset()):
        raise IllegalTransition(f"bid {from_state} -> {to_state}")

    clause = " and owner_id = %s::uuid" if owner_id is not None else ""
    params: list[Any] = [to_state, bid_id, from_state]
    if owner_id is not None:
        params.append(owner_id)

    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                f"""
                update public.bids
                   set state = %s, updated_at = now()
                 where id = %s::uuid and state = %s{clause}
                returning capability_class
                """,
                params,
            )
            row = cur.fetchone()
            if row is None:
                return False
            _record_observation_locked(cur, str(row["capability_class"]), "bid")
    return True


def cancel_bid(db: psycopg.Connection, *, bid_id: str, owner_id: str) -> bool:
    """Withdraw a bid. Escrow already held against claims is NOT touched here.

    A cancelled bid stops new matches; it does not reach into work already in
    flight. Money held against a lease that is still running belongs to that
    lease and is released or refunded when it resolves — cancelling the bid
    that produced it must not charge a host for a task nobody stopped, nor
    refund a buyer for work that is about to be accepted.
    """
    for state in ("open", "partial"):
        if set_bid_state(
            db, bid_id=bid_id, from_state=state, to_state="cancelled",
            owner_id=owner_id,
        ):
            return True
    return False


def bids_for_owner(db: psycopg.Connection, owner_id: str) -> list[dict[str, Any]]:
    """This buyer's bids, newest first."""
    with db.cursor() as cur:
        cur.execute(
            f"select {_BID_SELECT} from public.bids"
            " where owner_id = %s::uuid order by created_at desc, id",
            (owner_id,),
        )
        return list(cur.fetchall())


def open_bids(
    db: psycopg.Connection, capability_class_name: str
) -> list[dict[str, Any]]:
    """Live demand in one book, highest bid first — the order matching walks."""
    with db.cursor() as cur:
        cur.execute(
            f"select {_BID_SELECT} from public.bids"
            " where capability_class = %s and state in ('open', 'partial')"
            " order by max_zc_per_hour desc, created_at, id",
            (capability_class_name,),
        )
        return list(cur.fetchall())


# --- matches ----------------------------------------------------------------


def grant_matches(
    db: psycopg.Connection,
    *,
    bid_id: str,
    plan: MatchPlan,
) -> list[dict[str, Any]]:
    """Write a plan's fills as priced entitlements. **No credits move.**

    This is the function most likely to be misread, so it says it twice: a row
    written here does not schedule anything and does not commit anybody's
    balance. It records that these machines may pull this bid's work at these
    prices. The host still claims, and if none of them ever does, every one of
    these rows ends as ``expired`` having cost nobody anything.

    The bid moves to ``partial`` or ``filled`` in the same transaction, and a
    price observation is written with cause ``match`` — a fill is exactly the
    kind of event M5 says moves a price, and the series has to record it as it
    happens or it cannot be evidence for the claim afterwards.
    """
    if not plan.fills:
        return []

    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                f"select {_BID_SELECT} from public.bids"
                " where id = %s::uuid for update",
                (bid_id,),
            )
            bid = cur.fetchone()
            if bid is None:
                raise LookupError("no such bid")
            if bid["state"] not in ("open", "partial"):
                raise IllegalTransition(
                    f"a {bid['state']} bid cannot be matched"
                )

            rows: list[dict[str, Any]] = []
            for fill in plan.fills:
                cur.execute(
                    f"""
                    insert into public.matches
                        (bid_id, listing_id, machine_id, buyer_id, host_id,
                         capability_class, agreed_zc_per_hour, tasks_assigned,
                         est_task_seconds, unproven_host, state)
                    values (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s::uuid,
                            %s, %s, %s, %s, %s, 'granted')
                    returning {_MATCH_SELECT}
                    """,
                    (
                        bid_id,
                        fill.listing_id,
                        fill.machine_id,
                        str(bid["owner_id"]),
                        fill.host_id,
                        str(bid["capability_class"]),
                        fill.agreed_zc_per_hour,
                        fill.tasks,
                        int(bid["est_task_seconds"]),
                        fill.unproven_host,
                    ),
                )
                row = cur.fetchone()
                assert row is not None
                rows.append(row)

            cur.execute(
                "select coalesce(sum(tasks_assigned), 0) as assigned"
                "  from public.matches"
                " where bid_id = %s::uuid and state <> 'expired'",
                (bid_id,),
            )
            assigned = int(cur.fetchone()["assigned"])
            next_state = "filled" if assigned >= int(bid["tasks_wanted"]) else "partial"
            cur.execute(
                "update public.bids set state = %s, updated_at = now()"
                " where id = %s::uuid",
                (next_state, bid_id),
            )
            _record_observation_locked(cur, str(bid["capability_class"]), "match")
    return rows


def live_matches_for_machine(
    db: psycopg.Connection, machine_id: str
) -> list[dict[str, Any]]:
    """Entitlements this machine currently holds — the pull gate's question.

    ``granted`` and ``claimed`` only. A settled or expired match entitles
    nobody to anything, and including them is how a machine keeps pulling work
    against a job that finished last week.
    """
    with db.cursor() as cur:
        cur.execute(
            f"select {_MATCH_SELECT} from public.matches"
            " where machine_id = %s::uuid and state in ('granted', 'claimed')"
            " order by granted_at, id",
            (machine_id,),
        )
        return list(cur.fetchall())


def hold_escrow_on_claim(
    db: psycopg.Connection, *, match_id: str, lease_id: str
) -> dict[str, Any]:
    """A host claimed a lease under this entitlement: hold the buyer's credits.

    **This is the moment money is committed, and it is the earliest moment it
    may be.** Holding at grant would let a host who never shows up lock a
    buyer's balance, and since a grant costs the host nothing that is a denial
    of service with no attacker required (design §0.1).

    The hold is keyed on the LEASE, not the match: a match may cover many
    tasks claimed at different times, and holding the whole estimate on the
    first claim over-collateralises everything nobody has asked for yet. It
    also gives every later movement the same idempotency key — one lease, one
    hold, one settlement, one release — so a retried claim callback holds once.

    The match moves ``granted -> claimed`` on the first claim and stays
    ``claimed`` for the rest. Both happen in one transaction with the hold: a
    match recorded as claimed with no hold behind it is work nobody is paying
    for, and a hold with no claim behind it is money nobody can explain.

    Raises :class:`InsufficientCredits` if the buyer cannot cover it — and the
    honest consequence is that the work may still run and earn nothing, because
    the runtime is a pull system and the claim has already happened at the
    coordinator. :func:`can_cover` at grant time bounds this; nothing can
    eliminate it while the balance is spendable between grant and claim.
    """
    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                f"select {_MATCH_SELECT} from public.matches"
                " where id = %s::uuid for update",
                (match_id,),
            )
            match = cur.fetchone()
            if match is None:
                raise LookupError("no such match")
            if match["state"] not in ("granted", "claimed"):
                raise IllegalTransition(
                    f"a {match['state']} match cannot take a new claim"
                )

            amount = hold_zc(
                int(match["agreed_zc_per_hour"]), int(match["est_task_seconds"])
            )
            accounts = ensure_accounts(db, str(match["buyer_id"]))
            written = post(
                db,
                ref_type="attempt",
                ref_id=lease_id,
                legs=[
                    Leg(str(accounts["spendable"]["id"]), -amount, "escrow_hold"),
                    Leg(str(accounts["escrow"]["id"]), amount, "escrow_hold"),
                ],
            )

            if match["state"] == "granted":
                cur.execute(
                    "update public.matches"
                    "   set state = 'claimed', claimed_at = now()"
                    " where id = %s::uuid and state = 'granted'",
                    (match_id,),
                )
    return {"held_zc": amount if written else 0, "already_held": not written}


def settle_accepted_work(
    db: psycopg.Connection,
    *,
    match_id: str,
    lease_id: str,
    accepted_seconds: int,
) -> dict[str, Any]:
    """Pay a host for work the coordinator ACCEPTED, and release the rest.

    **The caller's judgement, never this function's.** Like
    ``db.record_contributions``, this writes what it is told and does not
    re-decide who deserves credit: it must be called only for an attempt whose
    ``outcome`` is ``accepted`` (migration 0015), and calling it for anything
    else moves money for work that produced nothing. That is hard rule 4, and
    it is the one rule here that this module cannot enforce on its own.

    Two movements, both keyed on the lease:

    - ``spent_accepted_work`` / ``earned_accepted_work`` — the buyer's escrow
      pays the host, exactly once, for ``accepted_seconds`` at the agreed rate
      and never more than was held.
    - ``escrow_release`` — whatever is left of the hold goes back to the
      buyer's spendable balance. An estimate that was generous is not a
      windfall for anybody.

    Idempotent twice over: the unique index refuses a second settlement for
    the lease, and by then the hold has already fallen to zero, so even a
    caller that ignores the return value cannot pay twice.
    """
    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                f"select {_MATCH_SELECT} from public.matches"
                " where id = %s::uuid for update",
                (match_id,),
            )
            match = cur.fetchone()
            if match is None:
                raise LookupError("no such match")

            buyer = ensure_accounts(db, str(match["buyer_id"]))
            host = ensure_accounts(db, str(match["host_id"]))
            held = held_for_lease(db, owner_id=str(match["buyer_id"]), lease_id=lease_id)
            charge = charge_zc(
                int(match["agreed_zc_per_hour"]), accepted_seconds, held_zc=held
            )

            paid = post(
                db,
                ref_type="attempt",
                ref_id=lease_id,
                legs=[
                    Leg(str(buyer["escrow"]["id"]), -charge, "spent_accepted_work"),
                    Leg(str(host["spendable"]["id"]), charge, "earned_accepted_work"),
                ],
            )
            remainder = held - charge if paid else 0
            released = 0
            if remainder > 0:
                if post(
                    db,
                    ref_type="attempt",
                    ref_id=lease_id,
                    legs=[
                        Leg(str(buyer["escrow"]["id"]), -remainder, "escrow_release"),
                        Leg(
                            str(buyer["spendable"]["id"]), remainder, "escrow_release"
                        ),
                    ],
                ):
                    released = remainder

    return {
        "charged_zc": charge if paid else 0,
        "released_zc": released,
        "already_settled": not paid,
    }


def refund_unaccepted_work(
    db: psycopg.Connection, *, match_id: str, lease_id: str
) -> dict[str, int]:
    """An attempt produced nothing accepted: give the buyer every credit back.

    The host earns zero. That is not a penalty — it is the whole distinctive
    property of this market (design §3.1): every other compute marketplace
    bills for time and pushes interruption risk onto the buyer, and this one
    can decline to because the runtime knows the difference between attempted
    and accepted work. A machine that died mid-task requeues its task and
    somebody else earns it.

    Called for an attempt resolved ``failed``, ``expired`` or ``abandoned``,
    and for the leases of a cancelled job. Writing the pair even when nothing
    was held (a donated host, or a zero estimate) is deliberate: the entry is
    the record that the refund happened, and an event that leaves no trace
    when the amount is zero is an event nobody can prove ran.
    """
    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                "select buyer_id from public.matches where id = %s::uuid for update",
                (match_id,),
            )
            match = cur.fetchone()
            if match is None:
                raise LookupError("no such match")

            buyer_id = str(match["buyer_id"])
            buyer = ensure_accounts(db, buyer_id)
            held = held_for_lease(db, owner_id=buyer_id, lease_id=lease_id)
            written = post(
                db,
                ref_type="attempt",
                ref_id=lease_id,
                legs=[
                    Leg(str(buyer["escrow"]["id"]), -held, "escrow_refund"),
                    Leg(str(buyer["spendable"]["id"]), held, "escrow_refund"),
                ],
            )
    return {"refunded_zc": held if written else 0}


def close_match(
    db: psycopg.Connection, *, match_id: str, from_state: str, to_state: str
) -> bool:
    """Move a match to a terminal state. Compare-and-set, like every other one.

    ``expired`` is the state to reach for when a granted match was never
    claimed. It is not a failure and must not be logged as one: the host was
    entitled to pull and did not, which in a pull system is an ordinary
    Tuesday. No credits move here — anything held was released or refunded per
    lease long before a match closes.
    """
    if to_state not in MATCH_TRANSITIONS.get(from_state, frozenset()):
        raise IllegalTransition(f"match {from_state} -> {to_state}")
    with db.cursor() as cur:
        cur.execute(
            "update public.matches set state = %s, closed_at = now()"
            " where id = %s::uuid and state = %s",
            (to_state, match_id, from_state),
        )
        return cur.rowcount == 1


def matches_for_bid(db: psycopg.Connection, bid_id: str) -> list[dict[str, Any]]:
    """Every entitlement written for one bid, oldest first."""
    with db.cursor() as cur:
        cur.execute(
            f"select {_MATCH_SELECT} from public.matches"
            " where bid_id = %s::uuid order by granted_at, id",
            (bid_id,),
        )
        return list(cur.fetchall())


def matches_for_owner(
    db: psycopg.Connection, owner_id: str, *, side: str = "buyer"
) -> list[dict[str, Any]]:
    """One person's matches, as buyer or as host. Owner folded into the query."""
    column = {"buyer": "buyer_id", "host": "host_id"}.get(side)
    if column is None:
        raise ValueError("side must be 'buyer' or 'host'")
    with db.cursor() as cur:
        cur.execute(
            f"select {_MATCH_SELECT} from public.matches"
            f" where {column} = %s::uuid order by granted_at desc, id",
            (owner_id,),
        )
        return list(cur.fetchall())


#: Reasons that belong to one movement under DIFFERENT names: a settlement
#: debits the buyer's escrow as ``spent_accepted_work`` and credits the
#: host as ``earned_accepted_work``, sharing one ref. Everything else is
#: its own group. Mirrored in the SQL CASE in
#: :func:`ledger_movements_for_owner` — the two must move together.
_LEDGER_REASON_GROUP = {
    "spent_accepted_work": "settlement",
    "earned_accepted_work": "settlement",
}


def ledger_movements_for_owner(
    db: psycopg.Connection,
    owner_id: str,
    *,
    limit: int = 50,
    before: int | None = None,
) -> list[dict[str, Any]]:
    """This account's movements, newest first, each with ALL of its legs.

    A movement is one ``post`` — every leg that shares its
    ``(reason, ref_type, ref_id)`` — and the counterparty leg travels with
    the viewer's own, because a feed that collapses a movement into
    "balance went up" deletes the counterparty and the counterparty is the
    point of a double-entry ledger. A mint (``grant``) has one leg; that is
    a true statement about minting, not a missing counterparty.

    Each leg carries ``mine`` so the route can render both sides without a
    second query, and ``kind`` for BOTH accounts — the counterparty's kind
    is what tells a host "this came from a buyer's escrow" without naming
    the buyer.

    ``before`` is an exclusive cursor on the movement's newest entry id:
    the console pages by passing back the last movement's ``cursor``.
    Entries, not movements, are paged, so a page boundary can never split
    a movement — the join reassembles it whole on whichever page holds the
    viewer's leg.

    A movement is every leg of one ``post``, and the legs of a settlement
    carry DIFFERENT reasons (``spent_accepted_work`` on the buyer's escrow,
    ``earned_accepted_work`` on the host's spendable) while sharing the
    ref — so the join groups on the ref plus a reason-GROUP that folds the
    settlement pair into one movement and keeps every other reason its
    own. Two posts can never share a group and a ref: the per-leg unique
    index would refuse the second one.
    """
    with db.cursor() as cur:
        cur.execute(
            "select id, kind from public.credit_accounts"
            " where owner_id = %s::uuid",
            (owner_id,),
        )
        accounts = {str(row["id"]): str(row["kind"]) for row in cur.fetchall()}
    if not accounts:
        return []

    params: list[Any] = [sorted(accounts)]
    before_clause = ""
    if before is not None:
        before_clause = " and id < %s"
        params.append(int(before))
    params.append(int(limit))

    with db.cursor() as cur:
        cur.execute(
            f"""
            with paged as (
                select id, reason, ref_type, ref_id
                  from public.credit_entries
                 where account_id = any(%s::uuid[]){before_clause}
                 order by id desc
                 limit %s
            )
            select e.id, e.account_id, a.kind, e.delta_zc, e.reason,
                   e.ref_type, e.ref_id, e.created_at,
                   p.id as owner_entry_id, p.reason as owner_reason
              from paged p
              join public.credit_entries e
                on coalesce(e.ref_type, '') = coalesce(p.ref_type, '')
               and coalesce(e.ref_id, '') = coalesce(p.ref_id, '')
               and case when e.reason in ('spent_accepted_work',
                                          'earned_accepted_work')
                        then 'settlement' else e.reason end
                 = case when p.reason in ('spent_accepted_work',
                                          'earned_accepted_work')
                        then 'settlement' else p.reason end
              join public.credit_accounts a on a.id = e.account_id
             order by e.id
            """,
            params,
        )
        rows = cur.fetchall()

    movements: dict[tuple[str, str, str], dict[str, Any]] = {}
    legs_seen: dict[tuple[str, str, str], set[int]] = {}
    owner_ids: dict[tuple[str, str, str], set[int]] = {}
    for row in rows:
        key = (
            _LEDGER_REASON_GROUP.get(str(row["owner_reason"]), str(row["owner_reason"])),
            str(row["ref_type"] or ""),
            str(row["ref_id"] or ""),
        )
        movement = movements.setdefault(
            key,
            {
                "cursor": 0,
                "_newest": 0,
                "created_at": None,
                "reason": str(row["owner_reason"]),
                "ref_type": row["ref_type"],
                "ref_id": row["ref_id"],
                "legs": [],
            },
        )
        owner_entry_id = int(row["owner_entry_id"])
        if owner_entry_id > movement["_newest"]:
            movement["_newest"] = owner_entry_id
            movement["created_at"] = row["created_at"]
        # Every owner leg of the movement, including ones the page limit
        # did not select but the join reassembled: the cursor is their
        # minimum, so `id < cursor` skips the whole movement.
        if str(row["account_id"]) in accounts:
            owner_ids.setdefault(key, set()).add(int(row["id"]))
        entry_id = int(row["id"])
        if entry_id not in legs_seen.setdefault(key, set()):
            legs_seen[key].add(entry_id)
            movement["legs"].append(
                {
                    "kind": str(row["kind"]),
                    "delta_zc": int(row["delta_zc"]),
                    "mine": str(row["account_id"]) in accounts,
                }
            )
    for key, movement in movements.items():
        # The cursor is the OLDEST owner leg of the movement: paging with
        # `id < cursor` must skip every leg of it, and a two-legged movement
        # paged by its newest leg would reappear on the next page by its
        # older one.
        movement["cursor"] = min(owner_ids[key])
        movement.pop("_newest", None)
    return sorted(movements.values(), key=lambda m: m["cursor"], reverse=True)


def match_for_claim(
    db: psycopg.Connection, *, machine_id: str, job_id: str
) -> str | None:
    """The GRANTED entitlement this machine is about to pull under, or None.

    The mirror of ``db._live_match_for_attempt`` one hop earlier: at claim
    time the match is still ``granted`` (the hold is what moves it to
    ``claimed``), so this is the only state that names work nobody has
    committed money for yet. The ``(machine, bid.job)`` pair is the same
    join the settlement side uses, and the deterministic order matches a
    machine pulling twice under two entitlements to the older grant first.

    ``None`` is the ordinary answer — workspace and donated work carries no
    entitlement, and a claim with no match behind it holds nothing.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select m.id
              from public.matches m
              join public.bids b on b.id = m.bid_id
             where m.machine_id = %s::uuid
               and b.job_id = %s
               and m.state = 'granted'
             order by m.granted_at, m.id
             limit 1
            """,
            (machine_id, job_id),
        )
        row = cur.fetchone()
    return str(row["id"]) if row is not None else None


# --- price observations -----------------------------------------------------


def record_price_observation(
    db: psycopg.Connection, *, capability_class_name: str, cause: str
) -> dict[str, Any]:
    """Sample the book now, and say what moved it.

    Written on every book change and never derived afterwards. A price series
    assembled retroactively from matches is a story — it can only contain the
    prices that cleared, so it shows a market that never had a bad day. One
    recorded as it happened is evidence, including the observation where the
    best ask got worse because three machines went offline.

    ``cause`` is the point of the row as much as the prices are: M5 says
    prices move because somebody posted a better ask or a bid cleared, never
    on a timer, and a series whose points cannot be attributed is a synthetic
    demand curve however honestly it was drawn.
    """
    with db.cursor() as cur:
        return _record_observation_locked(cur, capability_class_name, cause)


def _record_observation_locked(
    cur: psycopg.Cursor, capability_class_name: str, cause: str
) -> dict[str, Any]:
    """The observation insert, on a cursor the caller already holds.

    One statement, so the six numbers are one consistent read of the book
    rather than six reads a concurrent listing could slip between — a row
    showing four open listings and a best ask from a fifth is worse than no
    row at all, because nothing about it looks wrong.

    NULL where the book is empty, never 0: a zero ask is legal and means
    donated, so an empty side recorded as 0 is indistinguishable from a side
    full of volunteers.
    """
    if capability_class_name not in CAPABILITY_CLASSES:
        raise ValueError(f"{capability_class_name!r} is not a capability class")
    cur.execute(
        """
        insert into public.price_observations
            (capability_class, best_ask_zc, best_bid_zc, last_match_zc,
             open_listings, open_bids, cause)
        select %s,
               (select min(ask_zc_per_hour) from public.listings
                 where capability_class = %s and state = 'open'),
               (select max(max_zc_per_hour) from public.bids
                 where capability_class = %s and state in ('open', 'partial')),
               (select agreed_zc_per_hour from public.matches
                 where capability_class = %s
                 order by granted_at desc, id desc limit 1),
               (select count(*) from public.listings
                 where capability_class = %s and state = 'open'),
               (select count(*) from public.bids
                 where capability_class = %s and state in ('open', 'partial')),
               %s
        returning id, capability_class, observed_at, best_ask_zc, best_bid_zc,
                  last_match_zc, open_listings, open_bids, cause
        """,
        (
            capability_class_name,
            capability_class_name,
            capability_class_name,
            capability_class_name,
            capability_class_name,
            capability_class_name,
            cause,
        ),
    )
    row = cur.fetchone()
    assert row is not None
    return row


def price_series(
    db: psycopg.Connection, capability_class_name: str, *, limit: int = 200
) -> list[dict[str, Any]]:
    """One class's recorded price history, newest first."""
    with db.cursor() as cur:
        cur.execute(
            """
            select id, capability_class, observed_at, best_ask_zc, best_bid_zc,
                   last_match_zc, open_listings, open_bids, cause
              from public.price_observations
             where capability_class = %s
             order by observed_at desc, id desc
             limit %s
            """,
            (capability_class_name, int(limit)),
        )
        return list(cur.fetchall())
