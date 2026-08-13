"""What a place capacity comes from must be able to do.

Three methods, because teardown is the expensive half and it needs two of
them: `release` to destroy, and `observe` to find what we lost track of.
`observe` reads the VENUE, never our own rows — a reconciler that trusted
our rows could not by construction find an orphan, which is the one thing
it exists for.

Enrolment style is deliberately NOT in this interface. A push-style venue
(an exec channel exists) reuses `sandbox_bootstrap.bootstrap_worker`; a
pull-style venue boots with a start command that self-enrols. Both end at
the same observable state -- a registered node claiming leases in the right
pool -- and `acquire` returns only once that is true.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

__all__ = [
    "AcquiredMachine",
    "CapacityRequest",
    "FakeProvider",
    "ProviderState",
    "ReleaseOutcome",
    "ResourceProvider",
]


@dataclass(frozen=True)
class CapacityRequest:
    venue_id: str
    owner_id: str
    #: The pool the job was submitted against. Never invented, never
    #: defaulted -- a job with no pool is public and cannot use rented
    #: capacity at all (spec OC-2).
    pool_id: str
    job_id: str
    gpu_count: int
    min_vram_gb: float
    #: The base URL the rented machine's flashnode must enrol against. This
    #: is THIS API's public URL -- never the coordinator's. A machine token
    #: means nothing to the coordinator, and on Render the coordinator is a
    #: private service a rented host cannot route to at all; the same
    #: reasoning `app.py` writes out in full where it builds
    #: `sandbox_enrolment_url` for the E2B sandbox path.
    #: `settings.coordinator_url` is right for a single-host dev run and
    #: wrong for every deployed one -- reaching for it here would produce a
    #: rented host that enrols against the wrong service, never records a
    #: heartbeat, and is silently destroyed by the reconciler's
    #: ``boot_grace_s`` window on every rental.
    enrolment_url: str
    #: What this is expected to cost, read from the venue's price board
    #: BEFORE anything is created. The budget gate needs a number before
    #: the venue has been asked for anything, so the quote travels with the
    #: request rather than coming back with the machine. `None` is refused
    #: by the gate, deliberately: a venue that will not quote is a venue
    #: whose spend cannot be bounded.
    quoted_usd_per_hour: float | None = None
    #: The identity the machine must come up wearing, minted by
    #: ``acquire.acquire_for_job`` BEFORE the venue is asked for anything and
    #: attached to the request it passes on. Both are ``None`` on a request
    #: built by anything else, and an adapter that needs them must refuse
    #: rather than invent them.
    #:
    #: **Only a pull-style venue needs these, and it needs them absolutely.**
    #: A push-style venue (the FC sandbox) has an exec channel and writes the
    #: credential after the machine exists, so ``bootstrap_worker`` takes it
    #: as an argument and nothing has to travel here. ECS has no such channel:
    #: the credential goes into ``UserData`` at ``RunInstances`` or the host
    #: never learns who it is, and there is no second chance to tell it. That
    #: is why the token is minted first and why it is on the request rather
    #: than coming back with the machine.
    node_id: str | None = None
    #: The machine token itself. ``repr=False`` is not decoration: this
    #: object ends up in exception chains and debug logs, and a dataclass's
    #: generated ``__repr__`` is the shortest path from "the token exists in
    #: one process's memory" to a live credential in a log aggregator —
    #: exactly the reasoning ``MintedMachineCredential`` already carries.
    machine_token: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class AcquiredMachine:
    provider_handle: str
    machine_id: str | None
    node_id: str | None
    #: What the venue says it will actually charge, which is not necessarily
    #: what it quoted. ``acquire_for_job`` re-runs the budget ceilings against
    #: this number when it is higher than the quote and destroys the machine
    #: rather than keep one that breaks a ceiling, so the two null-ish values
    #: are NOT interchangeable: ``None`` means the venue did not restate the
    #: price and the quote stands, while ``0.0`` is a positive claim that the
    #: machine is free -- it overwrites the quote on the row and contributes
    #: nothing to the rolling-window ceiling. An adapter that has no number
    #: must return ``None``.
    usd_per_hour: float | None


@dataclass(frozen=True)
class ProviderState:
    exists: bool
    running: bool
    detail: str = ""


@dataclass(frozen=True)
class ReleaseOutcome:
    destroyed: bool
    detail: str = ""


@runtime_checkable
class ResourceProvider(Protocol):
    venue_id: str

    async def acquire(self, *, request: CapacityRequest) -> AcquiredMachine:
        """Create one machine and return once it is claiming leases.

        **An implementation that raises must first destroy whatever it
        created.** Between creating the instance and returning, the handle
        exists only inside this call: it is not on the row, nothing else knows
        it, and the caller cannot name what it does not receive. An adapter
        that gives up after creating something -- registration timed out is the
        usual way -- and lets the handle die with the exception has produced a
        machine that bills until a human reads the venue's console.

        The caller does not *rely* on this. ``acquire.acquire_for_job`` keeps
        the row in a state the reconciler selects whenever this call raises,
        precisely because the obligation cannot be enforced from outside. But
        an unnameable machine is one no sweep can destroy, so an implementation
        that cannot honour it should at minimum log the handle it is about to
        lose.
        """
        ...

    async def release(self, *, handle: str) -> ReleaseOutcome:
        """Destroy the machine ``handle`` names. Idempotent.

        ``destroyed=True`` is a claim that nothing is running any more, and a
        row is closed on it -- an already-gone handle is a success, but "the
        call returned" is not. Report ``destroyed=False`` rather than raising
        when the venue answered and refused; both are treated as *unknown* and
        leave the row to be swept again.
        """
        ...

    async def observe(self, *, handle: str) -> ProviderState:
        ...


@dataclass
class FakeProvider:
    """In-memory provider. The suite's stand-in for a venue that bills."""

    venue_id: str = "fake"
    fail_after_create: bool = False
    _live: set[str] = field(default_factory=set)

    def live_handles(self) -> list[str]:
        return sorted(self._live)

    async def acquire(self, *, request: CapacityRequest) -> AcquiredMachine:
        handle = f"fake-{uuid.uuid4().hex[:12]}"
        self._live.add(handle)
        if self.fail_after_create:
            # The contract: destroy what was created before raising.
            self._live.discard(handle)
            raise RuntimeError("injected failure after create")
        return AcquiredMachine(
            provider_handle=handle,
            machine_id=None,
            node_id=None,
            usd_per_hour=0.0,
        )

    async def release(self, *, handle: str) -> ReleaseOutcome:
        self._live.discard(handle)
        return ReleaseOutcome(destroyed=True)

    async def observe(self, *, handle: str) -> ProviderState:
        live = handle in self._live
        return ProviderState(exists=live, running=live)
