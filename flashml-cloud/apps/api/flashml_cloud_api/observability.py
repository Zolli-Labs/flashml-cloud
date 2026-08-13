"""One correlation id, and one way to put it on a log line.

Design: ``docs/superpowers/specs/2026-08-12-observability-and-verification-gaps.md``,
§2 G-B/G-C and §3 D-1/D-2/D-3. Schema: ``migrations/0026_correlation_id.sql``.

Two things live here because they are one feature. An id that spans
``session_id → sandbox_id → job_id → task_id → lease_id`` is only worth having
if it reaches the places a person actually looks, and the place a person
actually looks is the log.

**Standard library only.** No database, no settings, no models. That is not
tidiness: a logging helper that imports the modules it is meant to be called
from cannot be called from them, and the first import cycle is the point where
somebody reaches for ``json.dumps`` by hand again.

---

## The id

:func:`new_correlation_id` is the **only definition of a mint in this
package** — ``test_the_package_defines_the_mint_exactly_once`` is what keeps it
that way. It is called at the outermost thing a user initiated: a job
submission, a sandbox session, an acquisition. Everything below inherits.

It is **not a request id.** A request id dies with the response; this outlives
the request because the work it names does. A sandbox session hibernates, the
instance is destroyed, and the process that would have held an in-memory id is
gone long before the evaluation runs — so the id lives in a column and is read
back out on the far side of the pause by a process that never minted anything.
That is the specific thing D-4 asks for and the specific thing a request id
cannot do.

**Absent is null.** :func:`correlation_id_or_none` parses; it never mints. No
writer in this system mints. A row with no correlation id says so, for ever,
and pre-existing rows stay null — because a manufactured thread makes two
unrelated pieces of work look related, and nothing downstream can tell an
invented thread from a real one afterwards. This is the same discipline
``db.record_verification`` applies to ``unknown``, and it fails the same way if
a tolerant writer papers over it.

---

## The log line

:func:`render_event` serialises; :func:`log_event` is the same thing pointed at
a logger. Both take the ids **explicitly**, so a line either carries the chain
or does not exist — there is no ambient context to forget to set, and no
thread-local to lose across an ``asyncio.to_thread``.

**NEVER LOG A VALUE THE SUBMITTER AUTHORED.** ``PROGRESS.md`` Rule 7, both
halves: *who assigned this value?* and *does this re-identify a person?* A log
is a publication like any other. A machine's ``name`` is the ``hostname`` its
host supplied at enrolment (``enrolment.py:164``); a task's stderr is the
task's bytes; an artifact key past ``jobs/{job}/{task}/`` is a path the
submitted code chose; a step number is globbed out of filenames that code wrote
(``preflight.py:125``) — and Rule 7 is explicit that numbers are not safer than
strings here, only harder to notice.

Rule 7 is also explicit that a rule you have to remember is not a rule: it was
authored, quoted, and broken by its own author in the same conversation. So the
enforcement is mechanical and it is a **closed vocabulary**:

* the event name must look like a dotted literal from our own source;
* the six ids are shape-checked (see :data:`_ID`);
* every other field must have an entry in :data:`FIELDS`, and its value must
  pass that entry's validator.

Anything else is **dropped**, and its (sanitised) name is recorded under
``_refused`` so the drop is loud rather than silent. Dropped, not raised: a
logging helper that can take down a request is a logging helper people stop
calling — and losing one field is always better than losing the ids on the same
line, which are the whole reason the line exists.

The vocabulary is deliberately small. Adding to it is a decision about what
this system is willing to publish, and it belongs in a commit that says so.
"""
from __future__ import annotations

import json
import logging
import math
import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable

__all__ = [
    "Chain",
    "FIELDS",
    "SESSION_STATES",
    "UNNAMED_EVENT",
    "VERDICTS",
    "correlation_id_or_none",
    "log_event",
    "new_correlation_id",
    "render_event",
]


# ---------------------------------------------------------------------------
# The id
# ---------------------------------------------------------------------------


def new_correlation_id() -> str:
    """Mint one. **The only mint in this package.**

    Call this at an edge — the outermost thing a user initiated — and pass the
    result down. Never call it from a writer: "no id was supplied" is
    information, and replacing it with a fresh id destroys that information in
    the one direction nothing downstream can detect.
    """
    return str(uuid.uuid4())


def correlation_id_or_none(value: object) -> str | None:
    """``value`` as a canonical correlation id, or ``None``. **Never mints.**

    Every shape of absent — ``None``, empty, whitespace, the wrong type — and
    every value that is not a uuid answers ``None``. A hostname, a path, a job
    name and a task's stderr are all "not a uuid", which is the point: this is
    the gate that keeps a submitter-authored string out of the column every
    log line is keyed on.

    Canonicalised to lowercase hyphenated form, because two spellings of one
    id split a thread in half at query time and neither half says so.
    """
    if isinstance(value, uuid.UUID):
        return str(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return None


def require_correlation_id(value: object) -> str | None:
    """``correlation_id_or_none``, but a *present and unusable* value raises.

    The difference matters and it is the whole of D-2 in one function.
    ``None`` in means ``None`` out: absent is a legitimate answer and a writer
    must record it as one. Anything else that fails to parse is a **caller
    bug** — somebody passed a hostname, a job name, or a request id where a
    correlation id goes — and silently storing ``NULL`` for it would hide that
    bug behind a column that looks merely unthreaded.

    So: absent stays absent, wrong fails loudly, and neither ever becomes a
    freshly minted id.
    """
    if value is None:
        return None
    parsed = correlation_id_or_none(value)
    if parsed is None:
        raise ValueError(
            "correlation_id must be a uuid or None; got a value that is "
            "neither. Absent is null — but a value that cannot be a "
            "correlation id is a bug in the caller, not an absence, and it "
            "must not be stored as one. Nothing here mints a replacement."
        )
    return parsed


@dataclass(frozen=True)
class Chain:
    """The five ids D-4 names, plus the id that spans them.

    Every field optional and defaulting to ``None``, because a call site
    genuinely holds only some of them — a job submission has no lease, a lease
    claim has no sandbox. An id that is ``None`` is **absent from the line**
    rather than present-and-null: a key with a null value invites a reader to
    filter on it and find nothing.
    """

    correlation_id: str | None = None
    session_id: str | None = None
    sandbox_id: str | None = None
    job_id: str | None = None
    task_id: str | None = None
    lease_id: str | None = None


# ---------------------------------------------------------------------------
# What a line may say
# ---------------------------------------------------------------------------

#: An event name we could not accept. The line still goes out — losing the ids
#: because the name was malformed would be the wrong trade — but it is named as
#: unusable rather than being given a plausible-looking substitute.
UNNAMED_EVENT = "event.unnamed"

#: A dotted, lowercase literal. Every event name in this codebase is written
#: out in source; nothing derived from input has this shape, and an f-string
#: interpolating a hostname or a path fails it on the dot-position, the case,
#: or the characters.
_EVENT = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_EVENT_MAX = 96

#: The shape of every id in the chain, and **the exclusion of the dot is the
#: load-bearing part.**
#:
#: A uuid, a ``job-``/``fed-``/``round-`` prefix plus hex, a ``trial-000``, an
#: ``it00-shard-000``, an ``i-sbx-1``, a ``lease-9``: none of them has ever
#: contained a dot, a slash, or a space. A *hostname* always contains a dot —
#: and a hostname arriving in an id slot is not a hypothetical, it is the exact
#: value Rule 7 was broken by. A path contains slashes; a job name somebody
#: typed contains spaces and capitals; stderr contains newlines. Excluding
#: those characters is what makes this check bite rather than merely look
#: careful.
#:
#: Note this is deliberately NARROWER than ``app.NODE_ID_RE``
#: (``[A-Za-z0-9][A-Za-z0-9._:-]{0,127}``), which permits the dot because a
#: node id may carry one. A node id is not in this chain and has no slot here.
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_:-]{0,127}$")

#: A token from our own source: lowercase, snake or kebab, nothing else. This
#: is the shape of a literal somebody typed into a Python file — a reason code,
#: an outcome, a provider name. It is not the shape of anything a submitter
#: writes.
_TOKEN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

#: A Python class name, for ``error_kind``. ``type(exc).__name__`` is assigned
#: by whoever wrote the class — us, or the standard library — and never by the
#: value that caused it. The *message* is a different matter and has no slot
#: here on purpose: an SDK exception echoes the request that produced it.
_TYPE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")

#: A field name safe to echo back in ``_refused``. Anything else is reported as
#: ``?`` — otherwise refusing a value while printing the key it arrived under
#: would put the string on the line anyway, which is the hole this closes.
_FIELD_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")

_UNNAMED_FIELD = "?"

#: ``sandbox_sessions.STATES``, restated. This module imports only the standard
#: library (see the module docstring), so the copy is checked against the
#: original by ``test_the_state_vocabulary_is_the_state_machine_s_own`` rather
#: than by an import that would forbid ever logging from that module.
SESSION_STATES = frozenset({
    "REQUESTED", "ACTIVE", "PREPARED", "HIBERNATED", "RESUMING",
    "EVALUATING", "SUCCEEDED", "FAILED", "TERMINATED",
})

#: ``public.verifications.verdict``. ``unknown`` is in it deliberately: a
#: "could not tell" that could not be logged would be reported as nothing at
#: all, which is the one mistake that whole layer exists to avoid.
VERDICTS = frozenset({"pass", "flag", "unknown"})


def _count(value: object) -> int | None:
    """A non-negative integer WE counted. ``bool`` is rejected outright — the
    same trap ``verify._as_finite_float`` closes: ``True`` would otherwise
    arrive as ``1`` and read as a plausible measurement."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _seconds(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _matching(pattern: re.Pattern[str]) -> Callable[[object], str | None]:
    def check(value: object) -> str | None:
        return value if isinstance(value, str) and pattern.match(value) else None
    return check


def _member_of(allowed: frozenset[str]) -> Callable[[object], str | None]:
    def check(value: object) -> str | None:
        return value if isinstance(value, str) and value in allowed else None
    return check


#: **The closed vocabulary.** A field name not in here is refused before its
#: value is even looked at, which is what makes "a submitter-authored value
#: cannot reach a log line" a property of the helper rather than a habit of its
#: callers: there is no ``name``, no ``hostname``, no ``stderr``, no ``key``,
#: no ``path``, no ``message`` and no ``step``, so a refactor reaching for any
#: of them drops the value and says it did.
#:
#: Every entry is a value this codebase assigns to itself: something we
#: counted, something we timed, one of our own enums, or the name of a class
#: somebody wrote.
FIELDS: dict[str, Callable[[object], Any]] = {
    # counted here, on bytes and objects that passed through this process
    "attempts": _count,
    "bytes": _count,
    "count": _count,
    "objects": _count,
    "sequence": _count,
    "status_code": _count,
    # timed by our clock, never reported to us
    "duration_s": _seconds,
    "latency_ms": _count,
    # our own enums and our own literals
    "outcome": _matching(_TOKEN),
    "provider": _matching(_TOKEN),
    "reason": _matching(_TOKEN),
    "slice": _matching(_TOKEN),
    "state": _member_of(SESSION_STATES),
    "verdict": _member_of(VERDICTS),
    # the name of a class, never the message of an instance
    "error_kind": _matching(_TYPE_NAME),
    # a decision this code made
    "ok": _flag,
}

_ID_FIELDS: tuple[str, ...] = (
    "correlation_id", "session_id", "sandbox_id", "job_id", "task_id",
    "lease_id",
)


def _safe_field_name(name: object) -> str:
    if isinstance(name, str) and _FIELD_NAME.match(name):
        return name
    return _UNNAMED_FIELD


# ---------------------------------------------------------------------------
# Emitting
# ---------------------------------------------------------------------------


def render_event(event: object, *, chain: Chain | None = None, **fields: Any) -> str:
    """One log line as JSON: ``event``, whichever ids are present, and
    whichever fields survived the vocabulary.

    Pure — no logger, no clock, no I/O — because the property that matters is
    a property of the *serialized output*, and a pure function is what lets a
    test assert on exactly the bytes. Keys are sorted so two identical events
    serialise identically.

    Never raises. Anything unacceptable is dropped and its sanitised name is
    listed under ``_refused``.
    """
    payload: dict[str, Any] = {}
    refused: set[str] = set()

    if isinstance(event, str) and len(event) <= _EVENT_MAX and _EVENT.match(event):
        payload["event"] = event
    else:
        payload["event"] = UNNAMED_EVENT
        refused.add("event")

    if chain is not None:
        for name in _ID_FIELDS:
            value = getattr(chain, name, None)
            if value is None:
                continue
            # `correlation_id` is held to the stricter of the two rules: it is
            # a uuid or it is nothing, which is the same gate the column
            # enforces.
            if name == "correlation_id":
                parsed = correlation_id_or_none(value)
            else:
                parsed = value if isinstance(value, str) and _ID.match(value) else None
            if parsed is None:
                refused.add(name)
            else:
                payload[name] = parsed

    for name, value in fields.items():
        validator = FIELDS.get(name) if isinstance(name, str) else None
        checked = validator(value) if validator is not None else None
        if checked is None:
            refused.add(_safe_field_name(name))
        else:
            payload[name] = checked

    if refused:
        payload["_refused"] = sorted(refused)

    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):  # pragma: no cover - defence in depth
        # Nothing above can produce an unserialisable payload: every value on
        # it came back from a validator that returned a str, int, float or
        # bool. This exists so that if one ever does, the ids are still lost
        # rather than the process.
        return json.dumps({"event": UNNAMED_EVENT, "_refused": ["*"]})


def log_event(
    logger: logging.Logger,
    event: object,
    *,
    level: int = logging.INFO,
    chain: Chain | None = None,
    **fields: Any,
) -> None:
    """:func:`render_event`, emitted on ``logger`` at ``level``.

    The logger is passed in rather than held here so each line keeps its own
    module's name — ``flashml_cloud_api.artifact_mirror`` is half of what makes
    a line findable, and a shared logger would throw that away.

    ``stacklevel=2`` for the other half: without it every line in the system
    reports ``observability.py`` as its origin, and a formatter configured with
    ``%(filename)s:%(lineno)d`` would point every reader at this function
    instead of at the code that had something to say.
    """
    logger.log(level, render_event(event, chain=chain, **fields), stacklevel=2)


def chain_of(row: object, **overrides: str | None) -> Chain:
    """A :class:`Chain` from a ``sandbox_sessions`` row, plus any overrides.

    The one mapping worth writing down, because the column names and the chain
    names differ in exactly the place a person would get it wrong: a session's
    ``id`` is the ``session_id`` and its ``external_sandbox_id`` is the
    ``sandbox_id``. Anything absent stays absent.
    """
    values: dict[str, str | None] = {}
    if isinstance(row, dict) or hasattr(row, "get"):
        get = row.get  # type: ignore[union-attr]
        for name, column in (
            ("correlation_id", "correlation_id"),
            ("session_id", "id"),
            ("sandbox_id", "external_sandbox_id"),
            ("job_id", "training_job_id"),
        ):
            value = get(column)
            values[name] = str(value) if value is not None else None
    values.update(overrides)
    return Chain(**{k: v for k, v in values.items() if k in _ID_FIELDS})


def _chain_fields() -> tuple[str, ...]:  # pragma: no cover - introspection aid
    return tuple(asdict(Chain()))
