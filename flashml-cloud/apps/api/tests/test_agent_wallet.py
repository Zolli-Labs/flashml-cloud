"""AG-3: the agent wallet, and `marketplace.can_cover`'s first caller.

AS-17 found `can_cover` (`marketplace.py:324`) with no production caller
anywhere — only test callers and two docstrings describing a bound nothing
enforced. `test_can_cover_gets_its_first_caller` below is the test that
closes that finding: it proves a spending `ToolResult` drives `can_cover`,
not merely that the wallet's own arithmetic agrees with what `can_cover`
would say.

No real database anywhere in this file — `_PoisonDB` stands in for `db` and
raises on any attribute access at all, which is what makes
`test_the_approver_never_writes` a structural proof rather than a call-count
assertion: if `make_agent_approver` ever touched `db`, every test in this
file would fail with an `AssertionError` from `_PoisonDB`, not just the one
named for it.
"""
from __future__ import annotations

import json

import pytest

from flashml_cloud_api import marketplace
from flashml_cloud_api.agent_loop import AgentLoop, FakeTool, ToolRegistry, ToolResult
from flashml_cloud_api.agent_wallet import (
    AgentAllowance,
    make_agent_approver,
    spend_cost_zc,
)
from flashml_cloud_api.model_provider import FakeModelProvider


class _PoisonDB:
    """A `db` that raises the instant anything touches it.

    Not a `Mock` with call assertions bolted on afterward: a mutation the
    approver made and nobody checked for is still a mutation. This fails
    loudly, synchronously, at the moment of the touch — including a mere
    read — so "the approver never writes" is provable by construction: the
    approver's every source of truth is `AgentAllowance` and
    `read_spendable()`, never `db` directly.
    """

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"the approver touched db.{name!r}; it must only read "
                              f"through `allowance` and `read_spendable()`")


def _spend_result(
    name: str = "rent_gpu", **args: object
) -> ToolResult:
    """A proposed-spend `ToolResult`, shaped exactly as `AgentLoop.step()`
    builds `intent` before calling `approve_spend()` — see the "Spend gate"
    section of `agent_loop.py`'s module docstring: `content` is the
    JSON-encoded `args`, `ok=True` because nothing has failed yet, `spends`
    is always `True` for a proposed spend."""
    return ToolResult(name=name, ok=True, content=json.dumps(args), spends=True)


def _allowance(*, allowance_zc: int, spent_zc: int = 0) -> AgentAllowance:
    return AgentAllowance(agent_id="agent-1", allowance_zc=allowance_zc, spent_zc=spent_zc)


# ---------------------------------------------------------------------------
# spend_cost_zc — parsing (and the honesty rule)
# ---------------------------------------------------------------------------


def test_spend_cost_zc_parses_a_valid_grant():
    result = _spend_result(zc_per_hour=1_000, seconds=3_600, tasks=2)

    cost_zc, grant = spend_cost_zc(result)

    assert cost_zc == marketplace.hold_zc(1_000, 3_600) * 2
    assert grant == {"zc_per_hour": 1_000, "seconds": 3_600, "tasks": 2}


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        "[]",
        "42",
        json.dumps({"zc_per_hour": 1_000, "seconds": 3_600}),  # missing tasks
        json.dumps({"zc_per_hour": "1000", "seconds": 3_600, "tasks": 1}),  # str, not int
        json.dumps({"zc_per_hour": True, "seconds": 3_600, "tasks": 1}),  # bool, not int
        json.dumps({"zc_per_hour": 1.5, "seconds": 3_600, "tasks": 1}),  # float, not int
    ],
)
def test_spend_cost_zc_refuses_to_parse_and_returns_none_never_zero(content):
    result = ToolResult(name="rent_gpu", ok=True, content=content, spends=True)

    cost_zc, grant = spend_cost_zc(result)

    assert cost_zc is None
    assert grant == {}


# ---------------------------------------------------------------------------
# can_cover gets its first caller
# ---------------------------------------------------------------------------


def test_can_cover_gets_its_first_caller(monkeypatch):
    """AS-17's finding, closed: a spending `ToolResult` drives `can_cover`,
    not just arithmetic that happens to agree with it."""
    calls: list[dict[str, object]] = []
    real_can_cover = marketplace.can_cover

    def spy(spendable_zc, *, zc_per_hour, seconds, tasks):
        calls.append(
            {
                "spendable_zc": spendable_zc,
                "zc_per_hour": zc_per_hour,
                "seconds": seconds,
                "tasks": tasks,
            }
        )
        return real_can_cover(
            spendable_zc, zc_per_hour=zc_per_hour, seconds=seconds, tasks=tasks
        )

    monkeypatch.setattr(marketplace, "can_cover", spy)

    approve = make_agent_approver(
        _PoisonDB(),
        agent_id="agent-1",
        allowance=_allowance(allowance_zc=10_000),
        read_spendable=lambda: 10_000,
    )
    result = _spend_result(zc_per_hour=1_000, seconds=3_600, tasks=1)

    assert approve(result) is True
    assert calls == [
        {"spendable_zc": 10_000, "zc_per_hour": 1_000, "seconds": 3_600, "tasks": 1}
    ]


# ---------------------------------------------------------------------------
# The two guards
# ---------------------------------------------------------------------------


def test_a_spend_within_allowance_and_solvent_is_approved():
    approve = make_agent_approver(
        _PoisonDB(),
        agent_id="agent-1",
        allowance=_allowance(allowance_zc=5_000, spent_zc=1_000),
        read_spendable=lambda: 50_000,
    )
    # cost = hold_zc(1000, 3600) * 1 = 1000; 1000 (spent) + 1000 (cost) <= 5000
    result = _spend_result(zc_per_hour=1_000, seconds=3_600, tasks=1)

    assert approve(result) is True


def test_a_spend_over_the_allowance_is_refused_even_if_solvent():
    approve = make_agent_approver(
        _PoisonDB(),
        agent_id="agent-1",
        allowance=_allowance(allowance_zc=500, spent_zc=0),  # cost will be 1000
        read_spendable=lambda: 1_000_000,  # trivially solvent
    )
    result = _spend_result(zc_per_hour=1_000, seconds=3_600, tasks=1)

    assert approve(result) is False


def test_an_insolvent_spend_is_refused_even_if_within_allowance():
    approve = make_agent_approver(
        _PoisonDB(),
        agent_id="agent-1",
        allowance=_allowance(allowance_zc=1_000_000, spent_zc=0),  # plenty of room
        read_spendable=lambda: 1,  # can_cover will say no
    )
    result = _spend_result(zc_per_hour=1_000, seconds=3_600, tasks=1)

    assert approve(result) is False


def test_an_unparseable_spend_is_refused_not_treated_as_zero():
    # A generous allowance and a generous balance: if the unparseable spend
    # were ever treated as costing 0, both guards would trivially pass it.
    approve = make_agent_approver(
        _PoisonDB(),
        agent_id="agent-1",
        allowance=_allowance(allowance_zc=1_000_000, spent_zc=0),
        read_spendable=lambda: 1_000_000,
    )
    result = ToolResult(name="rent_gpu", ok=True, content="not json", spends=True)

    assert approve(result) is False


def test_the_approver_never_writes():
    """`_PoisonDB` raises on any attribute access; a passing approval here
    is itself the proof the approver read only `allowance` and
    `read_spendable()`, never `db`."""
    approve = make_agent_approver(
        _PoisonDB(),
        agent_id="agent-1",
        allowance=_allowance(allowance_zc=10_000, spent_zc=0),
        read_spendable=lambda: 10_000,
    )
    result = _spend_result(zc_per_hour=1_000, seconds=3_600, tasks=1)

    assert approve(result) is True

    # And the refusal paths never write either.
    over_budget = make_agent_approver(
        _PoisonDB(),
        agent_id="agent-1",
        allowance=_allowance(allowance_zc=1, spent_zc=0),
        read_spendable=lambda: 10_000,
    )
    assert over_budget(result) is False

    unparseable = make_agent_approver(
        _PoisonDB(),
        agent_id="agent-1",
        allowance=_allowance(allowance_zc=10_000, spent_zc=0),
        read_spendable=lambda: 10_000,
    )
    assert unparseable(ToolResult(name="rent_gpu", ok=True, content="{}", spends=True)) is False


# ---------------------------------------------------------------------------
# End to end: AgentLoop calling the approver this module builds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_end_to_end_with_agentloop_over_budget_is_refused_and_tool_never_runs(
    tmp_path,
):
    spender = FakeTool("rent_gpu", spends_money=True, content="rented")
    provider = FakeModelProvider(
        replies=[json.dumps({"tool": "rent_gpu", "args": {
            "zc_per_hour": 1_000, "seconds": 3_600, "tasks": 1,
        }})]
    )
    registry = ToolRegistry()
    registry.register(spender)

    approve = make_agent_approver(
        _PoisonDB(),
        agent_id="agent-1",
        allowance=_allowance(allowance_zc=500),  # cost 1000 > allowance 500
        read_spendable=lambda: 1_000_000,  # would be solvent — allowance is the blocker
    )
    loop = AgentLoop(
        provider,
        registry,
        goal="rent a gpu",
        workdir=tmp_path / "over-budget",
        approve_spend=approve,
    )

    state = await loop.step()

    assert state.status == "refused"
    assert spender.calls == []  # the tool never ran


@pytest.mark.asyncio
async def test_end_to_end_with_agentloop_within_budget_proceeds(tmp_path):
    spender = FakeTool("rent_gpu", spends_money=True, content="rented")
    provider = FakeModelProvider(
        replies=[json.dumps({"tool": "rent_gpu", "args": {
            "zc_per_hour": 1_000, "seconds": 3_600, "tasks": 1,
        }})]
    )
    registry = ToolRegistry()
    registry.register(spender)

    approve = make_agent_approver(
        _PoisonDB(),
        agent_id="agent-1",
        allowance=_allowance(allowance_zc=10_000),  # cost 1000 <= allowance
        read_spendable=lambda: 1_000_000,  # solvent
    )
    loop = AgentLoop(
        provider,
        registry,
        goal="rent a gpu",
        workdir=tmp_path / "within-budget",
        approve_spend=approve,
    )

    state = await loop.step()

    assert state.status == "running"
    # The tool ran, exactly once, with the args the model proposed.
    assert spender.calls == [{"zc_per_hour": 1_000, "seconds": 3_600, "tasks": 1}]
