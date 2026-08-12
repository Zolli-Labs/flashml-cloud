"""The provider contract, and the fake every other test leans on."""
from __future__ import annotations

import pytest

from flashml_cloud_api.capacity.provider import (
    AcquiredMachine,
    CapacityRequest,
    FakeProvider,
    ResourceProvider,
)


def _request(**over):
    base = dict(
        venue_id="fake", owner_id="o1", pool_id="p1", job_id="j1",
        gpu_count=1, min_vram_gb=24.0, coordinator_url="http://c",
        quoted_usd_per_hour=0.5,
    )
    base.update(over)
    return CapacityRequest(**base)


def test_fake_satisfies_the_protocol():
    assert isinstance(FakeProvider(), ResourceProvider)


@pytest.mark.asyncio
async def test_acquire_returns_a_handle_and_release_is_idempotent():
    p = FakeProvider()
    got = await p.acquire(request=_request())
    assert isinstance(got, AcquiredMachine)
    assert got.provider_handle
    first = await p.release(handle=got.provider_handle)
    second = await p.release(handle=got.provider_handle)
    assert first.destroyed is True
    # Releasing something already gone is success, not an error: the
    # reconciler will call this again and must not raise on a clean sweep.
    assert second.destroyed is True


@pytest.mark.asyncio
async def test_release_of_an_unknown_handle_is_not_an_error():
    p = FakeProvider()
    assert (await p.release(handle="never-existed")).destroyed is True


@pytest.mark.asyncio
async def test_a_failing_acquire_leaves_nothing_behind():
    """Any failure destroys what it created before raising — a half-created
    machine bills exactly like a whole one."""
    p = FakeProvider(fail_after_create=True)
    with pytest.raises(RuntimeError):
        await p.acquire(request=_request())
    assert p.live_handles() == []
