"""Which venues this deployment can actually talk to. Today: none.

``reconcile.reconcile_rented`` takes ``providers: dict[str, ResourceProvider]``
keyed by ``rented_capacity.venue_id``, and something has to build it. This is
that seam, and it is deliberately the only one: a second place that maps
venues to adapters is a second answer to "can we destroy this machine?", and
the two would disagree on the day it mattered.

**IT IS EMPTY, AND THAT IS THE HONEST ANSWER, NOT A STUB.** ``FakeProvider``
is the only implementation of the protocol in this repository; the real
adapter is Task 7 of the on-demand-capacity plan and is blocked on an owner
decision about which venue to integrate. So there is nothing to map, and
nothing here invents one.

WHY NOT WIRE ``FakeProvider`` IN "FOR NOW"
------------------------------------------
Because it would answer ``destroyed=True`` to every release, about machines it
has never heard of, at a venue that is really billing. The sweep would then
mark those rows ``RELEASED`` -- and ``unreleased_rows`` selects
``REQUESTED``/``ACTIVE`` only, so nothing would ever look at them again. That
is precisely the "RELEASED row in front of a live machine" that
``reconcile.py`` is written end to end to prevent, and it would arrive through
the front door, from a module whose job is to be safe. A fake that lies about
money is worse than no fake at all.

WHAT AN EMPTY REGISTRY DOES TO THE SWEEP, AND WHY IT IS SAFE
-------------------------------------------------------------
``reconcile_rented`` looks each row's venue up here, finds nothing, logs, and
**leaves the row exactly where it is** -- visible, sweepable, and re-examined
on every pass. It does not close it, and it does not guess. So an unconfigured
deployment cannot destroy anything, cannot lose track of anything, and says so
once per row per pass. A row that stays for ever is a cheap, visible defect; a
row closed on no evidence is an invoice. Same trade the rest of the module
makes.

The corollary an operator needs: **while this is empty, nothing in this
process can stop a rental.** If rows are appearing in ``rented_capacity`` and
this returns ``{}``, the only thing that stops that money is a human in the
venue's own console. The sweep's log line is the alarm; it is not a fix.

WHEN AN ADAPTER LANDS
---------------------
Construct it here, key it by the ``venue_id`` its rows carry, and gate it on
its own credentials being present -- all-or-nothing, the way
``settings.fc_sandbox_configured`` and ``settings.github_app_configured``
already do it, because a half-configured venue adapter that cannot
authenticate is one that answers "I could not destroy it" for ever.

And the rule that has to survive that commit: the machine it rents must enrol
against **this API's** public URL (``settings.public_api_url``, falling back to
``settings.coordinator_url`` for a single-host dev run), never the
coordinator's. ``CapacityRequest.enrolment_url`` carries the full argument.
Reach for the coordinator and the rented host heartbeats past this API,
``machines.last_seen_at`` stays null for ever, and ``boot_grace_s`` destroys it
sixty minutes into a healthy job, silently, on every rental.
"""
from __future__ import annotations

import logging
from typing import Any

from flashml_cloud_api.capacity.provider import ResourceProvider

__all__ = ["providers_for"]

log = logging.getLogger(__name__)


def providers_for(settings: Any) -> dict[str, ResourceProvider]:
    """Venue id -> the adapter that can destroy machines at it.

    ``settings`` is taken now, unused now: every adapter that lands will be
    gated on credentials that live there, and a signature that has to change
    to accept them is one more thing to get right in the commit that finally
    spends real money.
    """
    return {}
