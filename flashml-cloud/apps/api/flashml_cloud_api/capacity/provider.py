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
    #: capacity at all (spec D2).
    pool_id: str
    job_id: str
    gpu_count: int
    min_vram_gb: float
    coordinator_url: str
    #: What this is expected to cost, read from the venue's price board
    #: BEFORE anything is created. The budget gate needs a number before
    #: the venue has been asked for anything, so the quote travels with the
    #: request rather than coming back with the machine. `None` is refused
    #: by the gate, deliberately: a venue that will not quote is a venue
    #: whose spend cannot be bounded.
    quoted_usd_per_hour: float | None = None


@dataclass(frozen=True)
class AcquiredMachine:
    provider_handle: str
    machine_id: str | None
    node_id: str | None
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
        ...

    async def release(self, *, handle: str) -> ReleaseOutcome:
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
