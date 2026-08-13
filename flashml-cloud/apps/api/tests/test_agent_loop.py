"""`AgentLoop`, `AgentState`, `ToolRegistry` and the checkpoint round trip.

Mirrors `test_model_provider.py`'s seam: `FakeModelProvider` (already
committed there) and `FakeTool` (in-module here) keep real state — a call
log, scripted replies — so these tests are about behaviour, not mock call
counts. Nothing here ever constructs `QwenModelProvider`; no key exists in
this process, ever.

`workdir=tmp_path` throughout — every checkpoint this file writes lands
under pytest's own throwaway directory and is gone with it.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from flashml_cloud_api.agent_loop import (
    AgentLoop,
    AgentState,
    AgentTool,
    FakeTool,
    NoCheckpointToResume,
    ToolRegistry,
    ToolResult,
)
from flashml_cloud_api.model_provider import FakeModelProvider, ModelProvider


def _shaped(prefix: str, length: int) -> str:
    """Build a credential-SHAPED value without writing one down. See
    `test_model_provider.py::_shaped` — assembled at run time so a source
    scanner never finds a literal secret sitting in this file."""
    return prefix + "".join(chr(ord("a") + i % 26) for i in range(length))


def _call(tool: str, **args: object) -> str:
    """One scripted `FakeModelProvider` reply in the tool-call format
    `agent_loop.py`'s module docstring defines."""
    return json.dumps({"tool": tool, "args": args})


def _registry(*tools: AgentTool) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


# ---------------------------------------------------------------------------
# Tool call -> execution -> appended result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_tool_call_reply_runs_the_tool_and_appends_the_result(tmp_path):
    tool = FakeTool("noop", ok=True, content="did the thing")
    provider = FakeModelProvider(replies=[_call("noop", x=1)])
    loop = AgentLoop(
        provider, _registry(tool), goal="do it", workdir=tmp_path
    )

    state = await loop.step()

    assert tool.calls == [{"x": 1}]
    assert state.status == "running"
    last = state.messages[-1]
    assert last["role"] == "tool"
    payload = json.loads(last["content"])
    assert payload == {"tool": "noop", "ok": True, "content": "did the thing"}


@pytest.mark.asyncio
async def test_a_plain_reply_ends_the_run_as_done(tmp_path):
    provider = FakeModelProvider(replies=["the answer is 42"])
    loop = AgentLoop(provider, _registry(), goal="what is the answer", workdir=tmp_path)

    state = await loop.step()

    assert state.status == "done"
    assert state.messages[-1] == {"role": "assistant", "content": "the answer is 42"}


# ---------------------------------------------------------------------------
# Checkpointing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_step_writes_a_numeric_checkpoint_under_out_ckpt(tmp_path):
    tool = FakeTool("noop")
    provider = FakeModelProvider(replies=[_call("noop"), _call("noop")])
    loop = AgentLoop(provider, _registry(tool), goal="go", workdir=tmp_path)

    await loop.step()
    ckpt_1 = tmp_path / "out" / "ckpt" / "step-1.json"
    assert ckpt_1.is_file()
    restored_1 = AgentState.from_json(ckpt_1.read_text())
    assert restored_1.step == 1

    await loop.step()
    ckpt_2 = tmp_path / "out" / "ckpt" / "step-2.json"
    assert ckpt_2.is_file()
    restored_2 = AgentState.from_json(ckpt_2.read_text())
    assert restored_2.step == 2

    # Both numeric, both real files directly inside out/ckpt — the exact
    # glob `flashnode/executor/loop.py` and `AgentLoop.resume()` both use.
    names = sorted(p.name for p in (tmp_path / "out" / "ckpt").glob("step-*.json"))
    assert names == ["step-1.json", "step-2.json"]


@pytest.mark.asyncio
async def test_resume_restores_the_full_conversation_from_the_latest_checkpoint(
    tmp_path,
):
    tool_a = FakeTool("noop")
    provider_a = FakeModelProvider(replies=[_call("noop", n=1), _call("noop", n=2)])
    loop_a = AgentLoop(provider_a, _registry(tool_a), goal="survive a restart", workdir=tmp_path)

    await loop_a.step()
    await loop_a.step()
    assert loop_a.state.step == 2
    assert loop_a.state.status == "running"

    # A brand new AgentLoop, brand new provider and tool objects, knowing
    # nothing except where to look on disk. This is the fault-tolerant-
    # agent property by hand: the conversation survives loop_a never being
    # touched again.
    tool_b = FakeTool("noop")
    provider_b = FakeModelProvider(replies=[_call("noop", n=3)])
    loop_b = AgentLoop.resume(provider_b, _registry(tool_b), workdir=tmp_path)

    assert loop_b.state.step == 2
    assert loop_b.state.goal == "survive a restart"
    assert loop_b.state.messages == loop_a.state.messages

    state = await loop_b.step()

    assert state.step == 3
    # Every message loop_a ever produced is still there, in order, plus
    # exactly the new turn from this step.
    assert state.messages[: len(loop_a.state.messages)] == loop_a.state.messages
    assert len(state.messages) == len(loop_a.state.messages) + 2  # assistant + tool


def test_resume_with_no_checkpoint_raises_a_named_error(tmp_path):
    with pytest.raises(NoCheckpointToResume):
        AgentLoop.resume(FakeModelProvider(), ToolRegistry(), workdir=tmp_path)


# ---------------------------------------------------------------------------
# The spend gate (AS-9)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_spending_tool_is_refused_without_an_approver(tmp_path):
    spender = FakeTool("rent_gpu", spends_money=True, content="rented")
    provider = FakeModelProvider(replies=[_call("rent_gpu")])
    loop = AgentLoop(
        provider, _registry(spender), goal="rent a gpu", workdir=tmp_path
    )  # no approve_spend passed at all

    state = await loop.step()

    assert state.status == "refused"
    assert spender.calls == []  # the tool never ran


@pytest.mark.asyncio
async def test_a_spending_tool_is_refused_when_the_human_declines(tmp_path):
    spender = FakeTool("rent_gpu", spends_money=True, content="rented")
    provider = FakeModelProvider(replies=[_call("rent_gpu")])
    loop = AgentLoop(
        provider,
        _registry(spender),
        goal="rent a gpu",
        workdir=tmp_path,
        approve_spend=lambda intent: False,
    )

    state = await loop.step()

    assert state.status == "refused"
    assert spender.calls == []


@pytest.mark.asyncio
async def test_a_spending_tool_runs_when_the_human_approves(tmp_path):
    spender = FakeTool("rent_gpu", spends_money=True, content="rented")
    approvals: list[ToolResult] = []

    def approve(intent: ToolResult) -> bool:
        approvals.append(intent)
        return True

    provider = FakeModelProvider(replies=[_call("rent_gpu", gpu="a100")])
    loop = AgentLoop(
        provider,
        _registry(spender),
        goal="rent a gpu",
        workdir=tmp_path,
        approve_spend=approve,
    )

    state = await loop.step()

    assert state.status == "running"
    assert spender.calls == [{"gpu": "a100"}]
    # The approver saw the proposed spend before the tool ran, not after.
    assert len(approvals) == 1
    assert approvals[0].name == "rent_gpu"
    assert approvals[0].spends is True


# ---------------------------------------------------------------------------
# Provenance (AS-16) — no secret, no provider, no tool object on disk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_secret_or_raw_provider_object_reaches_the_checkpoint_file(tmp_path):
    secret = _shaped("sk-", 40)
    tool = FakeTool("noop", content="fine", env={"OPENAI_API_KEY": secret})
    provider = FakeModelProvider(replies=[_call("noop")])
    loop = AgentLoop(provider, _registry(tool), goal="go", workdir=tmp_path)

    await loop.step()

    ckpt_path = tmp_path / "out" / "ckpt" / "step-1.json"
    on_disk = ckpt_path.read_text()
    assert secret not in on_disk
    # And nothing that could carry one is on disk either: only the four
    # AgentState fields, never a repr of the tool or the provider.
    payload = json.loads(on_disk)
    assert set(payload.keys()) == {"goal", "messages", "step", "status"}


# ---------------------------------------------------------------------------
# The fakes are never the production default
# ---------------------------------------------------------------------------


def test_the_fake_tool_and_fake_provider_are_never_the_default():
    """Mirrors `test_model_provider.py`'s
    `test_the_fake_is_never_the_default_anywhere`.

    `FakeTool` is defined in `agent_loop.py` for tests, exactly as
    `FakeModelProvider` is defined in `model_provider.py`. Two checks:

    1. `FakeTool` must appear nowhere else in the package (excluding
       `agent_loop.py`, where it is legitimately defined) — a fake tool
       reachable from anywhere else is a code path that silently succeeds
       against a tool that never ran.
    2. `agent_loop.py`'s module namespace must not contain
       `FakeModelProvider` — the loop depends on the `ModelProvider`
       Protocol alone and must never import its test double, only ever
       (legitimately) mention it in prose, which is what a source-text grep
       for this half would wrongly penalise; a runtime check of what was
       actually imported is the honest version.
    """
    import flashml_cloud_api.agent_loop as agent_loop_module

    assert not hasattr(agent_loop_module, "FakeModelProvider")

    pkg = pathlib.Path(__file__).resolve().parents[1] / "flashml_cloud_api"
    hits = [
        p.name
        for p in sorted(pkg.rglob("*.py"))
        if p.name != "agent_loop.py" and "FakeTool" in p.read_text()
    ]
    assert hits == []


def test_both_fakes_satisfy_their_protocols():
    """Cheap and load-bearing, same reasoning as
    `test_model_provider.py::test_both_providers_satisfy_the_protocol`: a
    fake that has drifted from the Protocol it stands in for is a suite
    that stays green while the caller 500s."""
    assert isinstance(FakeModelProvider(), ModelProvider)
    assert isinstance(FakeTool(), AgentTool)


# ---------------------------------------------------------------------------
# AgentState round trip, in isolation
# ---------------------------------------------------------------------------


def test_agent_state_round_trips_through_json():
    state = AgentState(
        goal="a goal",
        messages=[{"role": "user", "content": "a goal"}],
        step=3,
        status="running",
    )
    restored = AgentState.from_json(state.to_json())
    assert restored == state


@pytest.mark.asyncio
async def test_calling_step_on_a_finished_loop_is_a_safe_no_op(tmp_path):
    provider = FakeModelProvider(replies=["done talking"])
    loop = AgentLoop(provider, ToolRegistry(), goal="finish fast", workdir=tmp_path)

    first = await loop.step()
    assert first.status == "done"

    second = await loop.step()
    assert second is first
    # No second checkpoint was written for a step that never happened.
    assert not (tmp_path / "out" / "ckpt" / "step-2.json").exists()
