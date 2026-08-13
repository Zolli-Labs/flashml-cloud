"""Which venues this deployment can actually talk to. Today: ECS, if configured.

``reconcile.reconcile_rented`` takes ``providers: dict[str, ResourceProvider]``
keyed by ``rented_capacity.venue_id``, and something has to build it. This is
that seam, and it is deliberately the only one: a second place that maps
venues to adapters is a second answer to "can we destroy this machine?", and
the two would disagree on the day it mattered.

**IT IS STILL EMPTY UNLESS ALIBABA ECS IS CONFIGURED, AND THAT IS THE HONEST
ANSWER, NOT A STUB.** ``capacity/ecs.py`` landed on 2026-08-12 and is mapped
below — gated, all-or-nothing, on ``settings.ecs_configured``, because a
provider that cannot authenticate answers "I could not destroy it" for ever
and a provider built from half a configuration is worse than none. No
credential for it exists in this repository, so an unconfigured deployment
gets ``{}`` and behaves exactly as it did before that commit.

Everything below about an empty registry therefore still describes the
default deployment, and is not history.

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
authenticate is one that answers "I could not destroy it" for ever. That is
what the ECS block below does, and it is the shape a second venue should
copy.

And the rule that has to survive that commit: the machine it rents must enrol
against **this API's** public URL (``settings.public_api_url``, falling back to
``settings.coordinator_url`` for a single-host dev run), never the
coordinator's. ``CapacityRequest.enrolment_url`` carries the full argument.
Reach for the coordinator and the rented host heartbeats past this API,
``machines.last_seen_at`` stays null for ever, and ``boot_grace_s`` destroys it
sixty minutes into a healthy job, silently, on every rental.

**Nothing here builds that URL, deliberately.** It belongs to whoever
constructs the ``CapacityRequest``; a default invented in this module would be
a second answer to the question OC-9 exists to keep to one.
"""
from __future__ import annotations

import logging
from typing import Any

from flashml_cloud_api.capacity.provider import ResourceProvider

__all__ = ["providers_for"]

log = logging.getLogger(__name__)


def providers_for(settings: Any) -> dict[str, ResourceProvider]:
    """Venue id -> the adapter that can destroy machines at it.

    One entry today, and only when its credentials are all present. A venue
    that cannot be reached must be ABSENT from this mapping rather than
    present and failing: the sweep looks each row's venue up here, finds
    nothing, logs, and leaves the row visible -- while a provider that is
    present and broken produces a release outcome, which is an answer about
    money nobody has evidence for.

    Never raises. An adapter that will not construct is logged and skipped:
    this runs at app startup, and a deployment refusing to boot over a venue
    it was merely *offered* would take down submission, jobs and the console
    for a feature that is opt-in.
    """
    providers: dict[str, ResourceProvider] = {}

    if getattr(settings, "ecs_configured", False):
        # Imported here, not at module scope: an unconfigured deployment
        # should not pay for `router` and `sandbox_bootstrap` being pulled in
        # through a module it will not use, and an ImportError in the adapter
        # must not be able to take the whole registry with it.
        from flashml_cloud_api.capacity import ecs as ecsmod

        try:
            providers[ecsmod.VENUE_ID] = ecsmod.EcsGpuProvider.from_settings(
                settings
            )
        except Exception:  # noqa: BLE001 - logged; the sweep stays honest
            log.error(
                "capacity registry: Alibaba ECS is configured but its adapter "
                "would not construct; no ECS rental can be created OR "
                "destroyed by this process",
                exc_info=True,
            )

    return providers
