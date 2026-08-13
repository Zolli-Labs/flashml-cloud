"""Agent principals: an agent's own scoped, revocable identity (AG-6).

WHAT THIS CLOSES. Until now, an agent loop (`agent_loop.py`, AG-2) or a
wallet-gated submit (`agent_wallet.py`, AG-3) ran under whatever credential
the calling process already held — in practice the human's own Supabase JWT,
borrowed for the run's duration. Borrowing is total: an agent holding it can
read every pool the human can read, submit into every pool the human can
submit into, and (absent AG-3's allowance) spend against the human's full
balance, because the JWT carries no distinction between "the human, at a
keyboard" and "a loop the human started an hour ago and is not watching."
AG-3's allowance narrows what an agent may DO; it does not change who the
agent is claimed to BE. This module is the other half: `AgentPrincipal` is an
identity separate from the human's sign-in, scoped to exactly what it was
minted to do and revocable on its own, without touching the human's session.

THIS MODULE HOLDS NO DATABASE CONNECTION AND NEVER WILL. Everything here is a
pure value type and pure validation — the same separation `access.py` keeps
for `OnboardingSubmission` (a validator for an untrusted request body, with no
`db` parameter anywhere in it). `db.py`'s `create_agent_principal` /
`authenticate_agent_token` / `revoke_agent_principal` / `list_agent_principals`
/ `get_agent_principal` are the only functions that touch
`public.agent_principals` (migration 0027), and they import `AgentPrincipal`
and `normalise_scopes` from here rather than duplicating either. Keeping
validation here and storage there means a scope-checking bug is a diff in one
small, dependency-free file — not a diff buried inside a SQL statement.

THREE SCOPES, EACH A DIFFERENT BLAST RADIUS. See migration 0027's header for
the full argument; restated briefly because it is what :func:`normalise_scopes`
and :meth:`AgentPrincipal.has_scope` exist to enforce:

- ``read`` — see what the owner can see. No write of any kind.
- ``submit`` — submit work into exactly one named pool. Requires a
  ``pool_id`` at creation; the database's own CHECK constraint refuses a row
  that claims this scope without one, so this is enforced twice, not once.
- ``spend`` — authorise spend up to an ``allowance_zc`` ceiling. Requires
  ``allowance_zc > 0`` at creation, for the same double-enforcement reason:
  ``0`` is the value that means "may not spend at all," and it is also the
  column's default, so a principal that never asked for ``spend`` can never
  be mistaken for one with an unbounded wallet.

A principal may hold any subset, including neither write scope.

WHY VALIDATION LIVES HERE RATHER THAN BEING LEFT TO THE DATABASE ALONE.
Migration 0027 enforces every one of these rules again, independently, as
CHECK constraints — deliberately, because a bug in this module must not be
able to write an invalid row just because nobody thought to add a matching
test here. But a CHECK violation is a bare `psycopg.errors.CheckViolation`
with no room for a caller-facing explanation of *which* rule broke; raising
:class:`InvalidScope` here, before the query ever runs, is what lets a mint
route return "the 'submit' scope needs a pool_id" instead of a raw
PostgreSQL error string.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

__all__ = [
    "AgentPrincipal",
    "InvalidScope",
    "VALID_SCOPES",
    "normalise_scopes",
]

#: The complete, closed set of scopes this system understands. Order here is
#: the CANONICAL order :func:`normalise_scopes` returns — not insertion
#: order — so two callers requesting the same set in a different order
#: produce byte-identical, directly comparable tuples.
VALID_SCOPES: tuple[str, ...] = ("read", "submit", "spend")


class InvalidScope(Exception):
    """A requested scope set cannot be granted: an unknown scope name, an
    empty scope list, or a scope missing the parameter it requires
    (``submit`` with no ``pool_id``, ``spend`` with no positive
    ``allowance_zc``).

    Raised by :func:`normalise_scopes` and by ``db.create_agent_principal``
    — never let this surface as an unhandled 500; a mint route must turn it
    into a clean 400 naming the rule that failed.
    """


@dataclass(frozen=True)
class AgentPrincipal:
    """An agent's own identity: who minted it, what it may do, and whether
    it is still alive. Constructed only from a row this control plane already
    wrote — never from caller-supplied data, the same rule `db.Machine` and
    `db.CliCredential` state for themselves.

    Deliberately carries no credential material. ``token_hash`` and
    ``token_prefix`` live in ``public.agent_principals`` and nowhere in this
    type — a value that never reaches this dataclass cannot leak through a
    log line or an exception chain that happens to repr one.
    """

    id: str
    owner_id: str
    label: str
    scopes: tuple[str, ...]
    pool_id: str | None
    allowance_zc: int
    status: str

    def has_scope(self, scope: str) -> bool:
        """True only for an ACTIVE principal that was granted this scope.

        Both halves matter and neither implies the other: a revoked
        principal that once held ``spend`` must not authorise a spend just
        because nobody re-checked ``status`` at the call site, and an active
        principal that was never granted ``submit`` must not submit just
        because nobody re-checked its scope list. Checking both here, in one
        place, means every caller gets the AND for free instead of having to
        remember to write it themselves.
        """
        return self.status == "active" and scope in self.scopes


def normalise_scopes(raw: Iterable[object]) -> tuple[str, ...]:
    """Validate a requested scope list and return it in canonical order.

    Deliberately does NOT see ``pool_id`` or ``allowance_zc`` — this function
    only judges scope NAMES. The parameter-presence rules (``submit`` needs a
    pool, ``spend`` needs a positive allowance) are the caller's job, because
    only the caller has those two values; ``db.create_agent_principal`` is
    that caller, and it raises the same :class:`InvalidScope` for both checks
    so a route never has to distinguish "bad scope name" from "scope missing
    its parameter" in its own error handling.

    Raises :class:`InvalidScope` for:

    - anything that is not a list/tuple/set of strings (a bare string is
      explicitly rejected even though Python would happily iterate its
      characters — ``"read"`` must never silently become
      ``('r', 'e', 'a', 'd')``);
    - any element that is not one of :data:`VALID_SCOPES`, spelled and cased
      exactly — ``'Read'`` and ``'reads'`` are both unknown scopes, not
      typos this function corrects on a caller's behalf;
    - an empty result — a principal with no scope at all authenticates as a
      credential that can do nothing, which is a confusing kind of "valid"
      and is refused outright instead.

    Duplicates are silently collapsed (asking for ``['read', 'read']`` is not
    a more dangerous request than asking for ``['read']`` once), and the
    return value is ordered per :data:`VALID_SCOPES` rather than by first
    appearance, so equal requests always produce an equal tuple.
    """
    if raw is None or isinstance(raw, (str, bytes)) or not isinstance(
        raw, (list, tuple, set, frozenset)
    ):
        raise InvalidScope(
            f"scopes must be a list of strings, got {raw!r}"
        )

    requested: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise InvalidScope(f"scope must be a string, got {item!r}")
        if item not in VALID_SCOPES:
            raise InvalidScope(
                f"unknown scope {item!r}; valid scopes are {VALID_SCOPES}"
            )
        requested.add(item)

    if not requested:
        raise InvalidScope("at least one scope is required")

    return tuple(scope for scope in VALID_SCOPES if scope in requested)
