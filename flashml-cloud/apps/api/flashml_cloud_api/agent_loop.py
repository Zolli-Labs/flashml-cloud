"""The agent loop: a workload, not a wrapper (AS-2, AS-3).

This module is the whole of what AG-2 builds. It is a small, provider-
agnostic ReAct-shaped loop — `complete() -> optional tool call -> execute ->
append -> checkpoint -> step+1` — meant to run *as a task's own process*
inside a hibernating FC Agent Sandbox: deep hibernation across the long wait
(nothing happening, the sandbox paused), light hibernation across the short
gap between one tool call and the next. Nothing in this file knows about
hibernation — that is a property of the process being paused and resumed by
the platform underneath it, not something this loop schedules. What this
loop owes that story is exactly one thing: state that survives the pause,
which is what the checkpoint convention below is for.

**Tool-call format.** A model reply is a tool call iff its entire text,
stripped of leading/trailing whitespace, parses as one JSON object shaped
exactly:

    {"tool": "<registered tool name>", "args": {...}}

`args` is optional and defaults to `{}`. Anything else — prose, markdown
fences, a JSON value that isn't a top-level object, an object with no
string `"tool"` key — is a plain reply: the model's final answer, and the
turn ends with `status="done"`. There is no half-way "reasoning plus a
call" shape in this minimal version, and no streaming/partial-call parsing;
a `system` message built by `_system_message()` states this format to the
model once, at the start of the conversation, naming the registered tools
and which of them spend money. `FakeModelProvider` in `test_agent_loop.py`
is scripted with this exact shape, so the parser and the tests describe the
same contract.

**Checkpoint convention (AG-2's actual point).** After every `step()`, the
new `AgentState` is written to `<workdir>/out/ckpt/step-<n>.json` — the
identical structure `preflight.CKPT_DIR` / `preflight.CHECKPOINT_CONTRACT`
documents for *any* FlashML task, just rooted at this loop's own `workdir`
instead of a sandboxed task's `/work`. `flashnode/executor/loop.py` globs
`step-*.json` files directly inside `out/ckpt` to ship checkpoints off a
dying machine; an agent's own conversation, written in that exact shape
from day one, is preserved by that same machinery for free. That is the
entire content of AG-2 and the seed FT-1 (fault-tolerant agents) grows
from — no new relay, no new wire format, one file convention reused. The
write is atomic (temp file in the *same* directory, then `os.replace`),
because `preflight.CHECKPOINT_CONTRACT` is explicit that a half-written
file is a checkpoint nothing can resume from, and a process paused or
killed mid-write must never leave one behind. `AgentLoop.resume()` globs
that same directory, picks the highest numeric `step-<n>`, and restores
from it — proven by hand in
`test_resume_restores_the_full_conversation_from_the_latest_checkpoint`,
which is the fault-tolerant-agent property this whole module exists to
demonstrate.

**Spend gate (AS-9).** An agent must not spend rented money without an
explicit human approval. `AgentTool.spends_money` is a plain attribute,
read *before* the tool ever runs. When it is true, `AgentLoop.step()` calls
`approve_spend(intent)` — a synchronous `Callable[[ToolResult], bool]`
supplied by the caller — with a `ToolResult` describing the proposed call
(`ok=True`, `content` the JSON-encoded args, `spends=True`) and refuses
(`status="refused"`, tool never invoked) if it returns `False` **or if no
approver was configured at all**. There is no "spend by default" path.
This is deliberately a thin, injectable seam and not a wallet: AG-3 wires
`marketplace.can_cover` behind exactly this callback next; nothing here
should need to change when it does.

**Provenance (AS-16).** `AgentState` holds only `goal`, `messages`, `step`
and `status`, and `messages` holds only `{"role", "content"}` dicts this
module builds itself — the goal string the caller supplied, the model's
own reply text, and `ToolResult` fields a registered tool returned. No
`ModelProvider`, no `ToolRegistry`, no API key, no tool's private state
(a mounted credential, an open connection — see `FakeTool.env` in the
tests) is ever serialized. `AgentState.to_json()`/`from_json()` round-trip
exactly those four JSON-safe fields, which is what makes
`test_no_secret_or_raw_provider_object_reaches_the_checkpoint_file`
decidable: nothing that *could* carry a secret is ever in scope to write.
A tool's `ToolResult.content` is a different matter — it is whatever the
tool chooses to return, and a real tool that touches a real credential
must redact before returning it, the same discipline `model_provider.py`'s
`redact()` applies on every exception path there. This module has no way
to enforce that on a tool it did not write; it only guarantees it never
adds a leak of its own.

**Precondition for wiring a real `ModelProvider` (frame-locals key-capture
caveat, AG-1 security finding #1).** Every test in this module runs against
`FakeModelProvider` only — no real key exists in this process, ever. When
this loop is later constructed with `QwenModelProvider` and a real
`qwen_api_key`, be aware that `model_provider.redact()` scrubs strings that
pass *through* it (log lines, raised exception messages) — it does **not**
protect a key sitting in a *frame local* (`self._api_key`, a local `api_key`
variable) at the moment an unrelated, unhandled exception propagates
through this call stack. Any error reporter attached to the deployment that
snapshots local variables on exceptions (Sentry-style breadcrumbs,
structured-log exception middleware) will capture that frame local
verbatim unless it is configured to scrub `api_key`/`_api_key` (and the
other names `model_provider._LABELLED_SECRET_RE` matches) before this loop
is ever run with a real provider. That configuration is a **precondition**
for this module going into production with a key behind it, not a defect
in this file — stated here because this is the module that first puts a
real `ModelProvider` on a call stack an agent's own tool code also runs on.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable

from .model_provider import ModelProvider, ModelReply

log = logging.getLogger(__name__)

#: Relative to `workdir`. Deliberately the same two path components
#: `preflight.OUT_DIR` / `preflight.CKPT_DIR` use, rooted differently — see
#: the module docstring's "Checkpoint convention" section.
OUT_DIRNAME = "out"
CKPT_DIRNAME = "ckpt"

#: What a tool-call reply must look like, quoted once for the system
#: message and the docstring to stay in sync.
TOOL_CALL_EXAMPLE = '{"tool": "<name>", "args": {}}'

#: A checkpoint filename as `resume()` recognises it. `search`, not
#: `fullmatch`: the full path (e.g. `.../out/ckpt/step-3.json`) is passed
#: through this, not the bare filename.
_STEP_RE = re.compile(r"step-(\d+)\.json$")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolResult:
    """What one tool invocation produced — the same role `ModelReply` plays
    for a completion: a small, storable, arguable-with record.

    `spends` marks whether this particular result came from a spending
    tool. It is reused, before the tool ever runs, as the shape of the
    *intent* passed to `approve_spend()` (see `AgentLoop.step()`) — `ok`
    is `True` there because nothing has failed yet, only been proposed —
    so the approver signature stays one type instead of two.
    """

    name: str
    ok: bool
    content: str
    spends: bool = False


@runtime_checkable
class AgentTool(Protocol):
    """One capability the agent may invoke.

    `runtime_checkable` for the same reason `ModelProvider` is: a fake
    missing this shape should fail an `isinstance` assertion in a test,
    not surface as a caller 500ing in production. `spends_money` is read
    *before* the tool ever runs (`AgentLoop.step()`'s spend gate, AS-9), so
    it has to be a plain attribute rather than something only knowable
    after execution.
    """

    name: str
    spends_money: bool

    async def __call__(self, **kwargs: Any) -> ToolResult: ...


class ToolRegistry:
    """Name -> `AgentTool`. Nothing more: no capability discovery, no
    per-caller scoping (that is AG-6's job, later). One registry per
    `AgentLoop`."""

    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> AgentTool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"no such tool registered: {name!r}") from None

    def names(self) -> list[str]:
        return sorted(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


class FakeTool:
    """An `AgentTool` with no side effects. **Tests only.**

    Not a mock: `calls` is a real log of every invocation's kwargs (the
    same shape `FakeModelProvider.calls` takes in `model_provider.py`), so
    a test asserts on behaviour rather than on mock call counts. `ok` /
    `content` / `spends_money` are scripted so one class stands in for
    either a free tool or a spending one.

    `env` stands in for a real tool's private state — a mounted
    credential, an open connection — that `AgentLoop` never reads: a
    tool's only channel back into the conversation is the `ToolResult` it
    returns. `test_no_secret_or_raw_provider_object_reaches_the_checkpoint_file`
    plants a secret-shaped string here and proves it never reaches a
    checkpoint, precisely because nothing in this module ever looks at it.
    """

    def __init__(
        self,
        name: str = "noop",
        *,
        ok: bool = True,
        content: str = "done",
        spends_money: bool = False,
        env: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.spends_money = spends_money
        self.ok = ok
        self.content = content
        self.env: dict[str, str] = dict(env) if env else {}
        self.calls: list[dict[str, Any]] = []

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"<FakeTool name={self.name!r} spends_money={self.spends_money}>"

    __str__ = __repr__

    async def __call__(self, **kwargs: Any) -> ToolResult:
        self.calls.append(kwargs)
        return ToolResult(
            name=self.name, ok=self.ok, content=self.content, spends=self.spends_money
        )


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentState:
    """Everything the loop needs to resume, and nothing else it does not
    (AS-16 — see the module docstring's "Provenance" section).

    `messages` holds only `{"role": ..., "content": ...}` dicts built by
    this module — the exact shape `ModelProvider.complete()` already
    accepts, so a state restored by `from_json()` needs no translation
    before it can talk to a provider again.

    `status`:
      - `"running"` — more steps to take.
      - `"waiting"` — reserved for a future tool that itself blocks on an
        external event (AG-5's `wait_for`, say). No code path in this
        module emits it yet; declared now rather than invented under time
        pressure later, the same premise `model_provider.ModelState.UNKNOWN`
        is declared on.
      - `"done"` — the model gave a plain-text final answer.
      - `"refused"` — a spending tool was proposed and a human did not (or
        could not) approve it (AS-9).
    """

    goal: str
    messages: list[dict[str, str]]
    step: int
    status: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "goal": self.goal,
                "messages": [dict(m) for m in self.messages],
                "step": self.step,
                "status": self.status,
            },
            indent=2,
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, data: str) -> "AgentState":
        obj = json.loads(data)
        return cls(
            goal=obj["goal"],
            messages=[dict(m) for m in obj["messages"]],
            step=int(obj["step"]),
            status=obj["status"],
        )


class NoCheckpointToResume(RuntimeError):
    """Raised by `AgentLoop.resume()` when `<workdir>/out/ckpt` holds no
    `step-*.json` file — there is no prior run to restore."""


# ---------------------------------------------------------------------------
# Checkpointing
# ---------------------------------------------------------------------------


def _write_checkpoint(workdir: Path, state: AgentState) -> Path:
    """Write `state` to `<workdir>/out/ckpt/step-<n>.json`, atomically.

    Temp file in the *same* directory (so `os.replace` is same-filesystem
    and therefore atomic), then rename over the final name. A process
    paused or killed between the write and the rename leaves only the
    `.tmp` file behind — `resume()`'s glob (`step-*.json`) never matches
    it, so a half-written checkpoint is simply invisible rather than
    corrupting the one `resume()` would otherwise pick.
    """
    ckpt_dir = Path(workdir) / OUT_DIRNAME / CKPT_DIRNAME
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    final_path = ckpt_dir / f"step-{state.step}.json"

    fd, tmp_name = tempfile.mkstemp(
        dir=ckpt_dir, prefix=f".step-{state.step}-", suffix=".json.tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(state.to_json())
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, final_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp_name)
        raise
    return final_path


def _latest_checkpoint(workdir: Path) -> Path | None:
    """The highest-numbered `step-*.json` under `<workdir>/out/ckpt`, or
    `None` if the directory does not exist or holds none. Ties (should
    never happen — filenames are unique per step) resolve to whichever
    `glob` yields last, which is fine because they would be byte-identical
    anyway."""
    ckpt_dir = Path(workdir) / OUT_DIRNAME / CKPT_DIRNAME
    if not ckpt_dir.is_dir():
        return None
    best_step = -1
    best_path: Path | None = None
    for path in ckpt_dir.glob("step-*.json"):
        m = _STEP_RE.search(path.name)
        if not m:
            continue
        n = int(m.group(1))
        if n >= best_step:
            best_step = n
            best_path = path
    return best_path


# ---------------------------------------------------------------------------
# Tool-call parsing
# ---------------------------------------------------------------------------


def _parse_tool_call(text: str) -> tuple[str, dict[str, Any]] | None:
    """Return `(tool_name, args)` if `text` is exactly one tool-call JSON
    object (see the module docstring), else `None` — meaning "treat this as
    a plain final reply", never an error. A model that emits malformed JSON
    is indistinguishable here from one that decided to just answer in
    prose; either way the loop advances rather than raising."""
    stripped = text.strip()
    if not stripped:
        return None
    try:
        obj = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    name = obj.get("tool")
    if not isinstance(name, str) or not name:
        return None
    args = obj.get("args", {})
    if not isinstance(args, dict):
        return None
    return name, args


def _tool_message(result: ToolResult) -> dict[str, str]:
    """A `ToolResult` folded into the one message shape this module ever
    appends: our own typed fields, JSON-encoded, never the tool's raw
    return value pasted in unstructured."""
    return {
        "role": "tool",
        "content": json.dumps(
            {"tool": result.name, "ok": result.ok, "content": result.content}
        ),
    }


def _system_message(tools: ToolRegistry) -> dict[str, str]:
    """States the tool-call format once, at the start of the conversation,
    so the format lives in exactly two places: this function and the
    module docstring — never re-derived by a model guessing."""
    lines = [
        "You are an autonomous agent working toward the stated goal.",
        "To call a tool, reply with EXACTLY one JSON object and nothing "
        f"else: {TOOL_CALL_EXAMPLE}",
        "To give your final answer, reply with plain text containing no "
        "such JSON object — that ends the run.",
    ]
    names = tools.names()
    if names:
        described = ", ".join(
            f"{n} (spends money)" if tools.get(n).spends_money else n for n in names
        )
        lines.append(f"Available tools: {described}.")
    else:
        lines.append("No tools are registered.")
    return {"role": "system", "content": " ".join(lines)}


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


class AgentLoop:
    """One agent, one conversation, one checkpoint trail.

    Deliberately not multi-agent and not a wallet (that is AG-3): this is
    the smallest honest version of "the agent decides what the human
    wants" (AS-2) with the one hard boundary that may never be soft —
    money never moves without `approve_spend()` saying yes.
    """

    def __init__(
        self,
        provider: ModelProvider,
        tools: ToolRegistry,
        *,
        goal: str,
        workdir: Path,
        model: str = "qwen-plus",
        max_steps: int = 20,
        approve_spend: Callable[[ToolResult], bool] | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.workdir = Path(workdir)
        self.model = model
        self.max_steps = max_steps
        self.approve_spend = approve_spend
        self.state = AgentState(
            goal=goal,
            messages=[_system_message(tools), {"role": "user", "content": goal}],
            step=0,
            status="running",
        )

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"<AgentLoop step={self.state.step} status={self.state.status!r} "
            f"model={self.model!r}>"
        )

    __str__ = __repr__

    async def step(self) -> AgentState:
        """One turn: `complete()` -> optional tool call -> execute -> append
        -> write checkpoint -> the returned state's `step` is one higher
        than before this call. A no-op (returns the current state
        unchanged, writes nothing) once `status` has left `"running"` or
        `max_steps` has been reached — calling `step()` again on a finished
        loop is safe, not an error."""
        state = self.state
        if state.status != "running" or state.step >= self.max_steps:
            return state

        reply: ModelReply = await self.provider.complete(
            state.messages, model=self.model
        )
        messages: list[dict[str, str]] = list(state.messages)
        messages.append({"role": "assistant", "content": reply.text})

        status = state.status
        call = _parse_tool_call(reply.text)

        if call is None:
            # A plain reply is the model's final answer — nobody is going
            # to feed this loop another human turn, so there is nothing
            # left to advance the conversation toward.
            status = "done"
        else:
            name, args = call
            try:
                tool = self.tools.get(name)
            except KeyError:
                messages.append(
                    _tool_message(
                        ToolResult(
                            name=name, ok=False, content=f"unknown tool: {name!r}"
                        )
                    )
                )
            else:
                if tool.spends_money:
                    intent = ToolResult(
                        name=tool.name, ok=True, content=json.dumps(args), spends=True
                    )
                    approved = (
                        self.approve_spend(intent)
                        if self.approve_spend is not None
                        else False
                    )
                    if not approved:
                        # AS-9: no approver configured is refused exactly
                        # like an approver saying no. There is no
                        # "spend by default" path through this loop.
                        status = "refused"
                        messages.append(
                            _tool_message(
                                ToolResult(
                                    name=tool.name,
                                    ok=False,
                                    content=(
                                        "refused: spending tool has no human "
                                        "approval"
                                    ),
                                    spends=True,
                                )
                            )
                        )
                    else:
                        result = await tool(**args)
                        messages.append(_tool_message(result))
                else:
                    result = await tool(**args)
                    messages.append(_tool_message(result))

        new_state = AgentState(
            goal=state.goal, messages=messages, step=state.step + 1, status=status
        )
        _write_checkpoint(self.workdir, new_state)
        self.state = new_state
        return new_state

    async def run(self) -> AgentState:
        """Loop `step()` until `status` leaves `"running"` or `max_steps`
        is reached. `max_steps` is an external ceiling, not the agent
        deciding it is finished — reaching it leaves `status` exactly as
        it was, `"running"`, rather than inventing a new terminal value for
        "we stopped asking"."""
        while self.state.status == "running" and self.state.step < self.max_steps:
            await self.step()
        return self.state

    @classmethod
    def resume(
        cls,
        provider: ModelProvider,
        tools: ToolRegistry,
        *,
        workdir: Path,
        **kwargs: Any,
    ) -> "AgentLoop":
        """Rebuild an `AgentLoop` from the latest checkpoint under
        `<workdir>/out/ckpt`. This is the whole of what makes an agent's
        conversation survive its process dying: the checkpoint IS the
        conversation, written by the same convention any FlashML task's
        state is, and this is nothing more than `AgentState.from_json()`
        pointed at the newest file the relay would also have shipped.

        Raises `NoCheckpointToResume` if there is nothing to restore —
        callers deciding whether to resume or start fresh should catch it
        rather than treat a missing checkpoint as a crash.
        """
        latest = _latest_checkpoint(workdir)
        if latest is None:
            raise NoCheckpointToResume(
                f"no step-*.json checkpoint under "
                f"{Path(workdir) / OUT_DIRNAME / CKPT_DIRNAME} to resume from"
            )
        state = AgentState.from_json(latest.read_text())
        loop = cls(provider, tools, goal=state.goal, workdir=workdir, **kwargs)
        loop.state = state
        return loop
