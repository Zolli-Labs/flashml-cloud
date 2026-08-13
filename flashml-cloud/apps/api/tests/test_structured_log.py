"""The one structured-log helper, and the rule it exists to enforce.

`observability.render_event` is a pure serialiser and `observability.log_event`
is the same thing pointed at a logger. Everything below asserts on the
**serialized line**, never on a field name, for the reason `PROGRESS.md` Rule 7
gives in full: the rule was authored, quoted, and then broken by its own author
in the same conversation, because a machine's display *name* turned out to be
the `hostname` its host supplied at enrolment (`enrolment.py:164`). A check
that asserts "the payload has no `hostname` key" passes the moment a refactor
reintroduces that value under `label`, `node`, or `text`. A check that asserts
the *value* is absent from the bytes does not.

So the helper carries a **closed vocabulary**: a fixed set of field names, each
with a validator for the values it will accept. An unknown name is refused, and
a known name holding a value that does not look like something this codebase
assigned is refused too. Refusal drops the value and records the (sanitised)
name under `_refused`, so a dropped field is loud in the log rather than
silent — and a log call never raises, because a logging helper that can take
down a request is a logging helper people stop calling.
"""
from __future__ import annotations

import json
import logging

import pytest

from flashml_cloud_api import observability as obs
from flashml_cloud_api import sandbox_sessions as ss

#: A hostname of the shape a volunteer's laptop actually reports. This exact
#: class of value is what Rule 7 was broken by, so it is what every "cannot
#: reach a log line" assertion below uses.
POISON = "phongs-macbook-pro.local"

#: Two more shapes a submitter authors: a path the task's own code chose, and
#: a job name typed into the console.
POISON_PATH = "jobs/j1/trial-000/out/ckpt/step-16298.json"
POISON_NAME = "Phong's Big Run (v2)"


def _payload(line: str) -> dict:
    return json.loads(line)


# ---------------------------------------------------------------------------
# It emits JSON, and it carries the chain
# ---------------------------------------------------------------------------


def test_the_helper_emits_parseable_json_carrying_every_id_in_the_chain():
    line = obs.render_event(
        "sandbox.resumed",
        chain=obs.Chain(
            correlation_id="7f1a4a26-0d1f-4a1e-9a8e-1b2c3d4e5f60",
            session_id="0d5a6b7c-1111-2222-3333-444455556666",
            sandbox_id="i-sbx-1",
            job_id="job-train-abc123",
            task_id="trial-000",
            lease_id="lease-9",
        ),
    )
    assert _payload(line) == {
        "event": "sandbox.resumed",
        "correlation_id": "7f1a4a26-0d1f-4a1e-9a8e-1b2c3d4e5f60",
        "session_id": "0d5a6b7c-1111-2222-3333-444455556666",
        "sandbox_id": "i-sbx-1",
        "job_id": "job-train-abc123",
        "task_id": "trial-000",
        "lease_id": "lease-9",
    }


def test_an_absent_id_is_absent_from_the_line_rather_than_null():
    """The chain is what makes a line findable. A key present with a null
    value invites a reader to filter on it and find nothing; an absent key
    says the same thing without the invitation."""
    payload = _payload(obs.render_event("job.submitted", chain=obs.Chain(job_id="j1")))
    assert payload == {"event": "job.submitted", "job_id": "j1"}


def test_a_line_with_no_chain_at_all_is_still_a_line():
    assert _payload(obs.render_event("mirror.started")) == {"event": "mirror.started"}


def test_log_event_puts_exactly_that_json_through_the_logger(caplog):
    log = logging.getLogger("test.observability")
    with caplog.at_level(logging.INFO, logger="test.observability"):
        obs.log_event(log, "mirror.completed", chain=obs.Chain(job_id="j1"), objects=3)
    assert len(caplog.records) == 1
    assert _payload(caplog.records[0].getMessage()) == {
        "event": "mirror.completed", "job_id": "j1", "objects": 3,
    }


def test_log_event_honours_the_level_it_is_given(caplog):
    log = logging.getLogger("test.observability.level")
    with caplog.at_level(logging.DEBUG, logger="test.observability.level"):
        obs.log_event(log, "a.b", level=logging.WARNING)
    assert caplog.records[0].levelno == logging.WARNING


def test_the_keys_are_sorted_so_two_identical_events_serialise_identically():
    a = obs.render_event("a.b", chain=obs.Chain(job_id="j", task_id="t"), objects=1)
    b = obs.render_event("a.b", objects=1, chain=obs.Chain(task_id="t", job_id="j"))
    assert a == b


# ---------------------------------------------------------------------------
# THE rule: a submitter-authored value cannot reach a log line.
#
# Asserted against the serialized output, in every slot the helper has.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [POISON, POISON_PATH, POISON_NAME])
@pytest.mark.parametrize(
    "key",
    # Every name a future refactor might plausibly reach for. None of them is
    # in the vocabulary, so none of them survives — which is the property, not
    # the list. The list only makes the failure legible.
    ["name", "hostname", "node_name", "label", "stderr", "stdout", "text",
     "message", "detail", "key", "path", "file", "repo", "email", "spec",
     "error", "output", "command", "url", "region", "platform"],
)
def test_a_submitter_authored_value_cannot_reach_a_log_line_under_any_key(key, value):
    line = obs.render_event("a.b", **{key: value})
    assert value not in line
    assert key in _payload(line)["_refused"]


@pytest.mark.parametrize("value", [POISON, POISON_PATH, POISON_NAME])
@pytest.mark.parametrize(
    "slot",
    ["correlation_id", "session_id", "sandbox_id", "job_id", "task_id", "lease_id"],
)
def test_a_submitter_authored_value_cannot_reach_a_log_line_through_an_id(slot, value):
    """The id slots are the only string-shaped parameters the helper has, so
    they are the only remaining way in — and they are shape-checked.

    The shape deliberately excludes the dot. Every id in this chain is a uuid,
    a `fed-`/`job-` prefix plus hex, a `trial-000`, or an `i-sbx-1`; none of
    them has ever contained one. A *hostname* always does, and so does every
    path a task writes. Excluding the dot is what makes this check bite on the
    exact value Rule 7 was broken by, rather than merely looking careful.
    """
    line = obs.render_event("a.b", chain=obs.Chain(**{slot: value}))
    assert value not in line
    assert slot in _payload(line)["_refused"]


@pytest.mark.parametrize("value", [POISON, POISON_PATH, POISON_NAME])
def test_a_submitter_authored_value_cannot_reach_a_log_line_as_the_event_name(value):
    line = obs.render_event(f"job.failed.{value}")
    assert value not in line
    assert _payload(line)["event"] == obs.UNNAMED_EVENT
    assert "event" in _payload(line)["_refused"]


@pytest.mark.parametrize("value", [POISON, POISON_PATH, POISON_NAME])
def test_a_submitter_authored_value_cannot_ride_an_allowlisted_field(value):
    """`reason` IS in the vocabulary — so the name being allowed is not
    enough. Its validator accepts a lowercase snake/kebab token and nothing
    else, which is the shape a literal in our own source has and no hostname,
    path or human-typed name does."""
    line = obs.render_event("a.b", reason=value)
    assert value not in line
    assert "reason" in _payload(line)["_refused"]


@pytest.mark.parametrize("value", [POISON, POISON_PATH, POISON_NAME])
def test_a_submitter_authored_value_cannot_reach_a_log_line_as_a_FIELD_NAME(value):
    """The hole the `_refused` list would otherwise open: refusing a value but
    printing the key it arrived under puts the string in the line anyway. A
    name that is not itself a lowercase identifier is reported as `?`."""
    line = obs.render_event("a.b", **{value: 1})
    assert value not in line
    assert _payload(line)["_refused"] == ["?"]


def test_a_number_a_task_authored_is_refused_like_a_string():
    """Rule 7: "Numbers are not safer than strings here; they are just harder
    to notice." `step` — globbed out of filenames the task's own code writes
    (`preflight.py:125`) — has no entry in the vocabulary, so it is refused on
    its name, before its type is ever considered."""
    line = obs.render_event("ckpt.resumed", step=16298)
    assert "16298" not in line
    assert "step" in _payload(line)["_refused"]


# ---------------------------------------------------------------------------
# The vocabulary itself
# ---------------------------------------------------------------------------


def test_the_fields_that_are_ours_do_survive():
    payload = _payload(obs.render_event(
        "mirror.completed", objects=3, bytes=4096, duration_s=1.5,
        outcome="accepted", error_kind="MirrorError", state="HIBERNATED",
        verdict="unknown",
    ))
    assert payload == {
        "event": "mirror.completed", "objects": 3, "bytes": 4096,
        "duration_s": 1.5, "outcome": "accepted", "error_kind": "MirrorError",
        "state": "HIBERNATED", "verdict": "unknown",
    }


def test_a_counted_field_refuses_a_value_that_was_never_a_count():
    for bad in ("3", True, -1, float("nan")):
        line = obs.render_event("a.b", objects=bad)
        assert "objects" in _payload(line)["_refused"], bad


def test_a_state_outside_the_state_machine_is_refused():
    line = obs.render_event("a.b", state="MOSTLY_FINE")
    assert "MOSTLY_FINE" not in line
    assert "state" in _payload(line)["_refused"]


def test_the_state_vocabulary_is_the_state_machine_s_own():
    """`observability` imports nothing but the standard library — a logging
    helper that drags the session module in cannot be called *from* it — so
    the set is restated here and this test is what keeps the copy honest."""
    assert obs.SESSION_STATES == frozenset(ss.STATES)


def test_the_verdict_vocabulary_is_the_verification_layer_s_own():
    """`unknown` is in it, and that is the point: a "could not tell" that
    could not be logged would be reported as nothing at all, which is the one
    mistake `record_verification` is built to avoid."""
    assert obs.VERDICTS == frozenset({"pass", "flag", "unknown"})


def test_a_refused_field_never_takes_the_rest_of_the_line_with_it():
    """A dropped value must not cost the ids. The whole point of the line is
    that the chain is on it."""
    payload = _payload(obs.render_event(
        "a.b", chain=obs.Chain(job_id="j1", task_id="t1"), hostname=POISON,
    ))
    assert payload["job_id"] == "j1" and payload["task_id"] == "t1"
    assert POISON not in json.dumps(payload)


def test_logging_never_raises_whatever_it_is_handed(caplog):
    """A helper that can raise is a helper that turns an observation into an
    outage. Everything unacceptable is dropped and named; nothing propagates.
    """
    log = logging.getLogger("test.observability.safe")
    with caplog.at_level(logging.INFO, logger="test.observability.safe"):
        obs.log_event(
            log, 12345, chain=obs.Chain(job_id=object()),  # type: ignore[arg-type]
            **{"": None, "objects": {"nested": "thing"}},
        )
    assert len(caplog.records) == 1
    json.loads(caplog.records[0].getMessage())


# ---------------------------------------------------------------------------
# Minting, and the one thing that is allowed to do it
# ---------------------------------------------------------------------------


def test_new_correlation_id_is_a_uuid_and_is_never_the_same_twice():
    first, second = obs.new_correlation_id(), obs.new_correlation_id()
    assert first != second
    assert obs.correlation_id_or_none(first) == first


def test_correlation_id_or_none_never_mints():
    """The parser is the read path, and D-2 forbids the read path inventing an
    id. Absent in, absent out — for every shape of absent there is."""
    for absent in (None, "", "   ", b"", [], {}, 0, False):
        assert obs.correlation_id_or_none(absent) is None


def test_correlation_id_or_none_refuses_anything_that_is_not_a_uuid():
    for bad in (POISON, POISON_PATH, "not-a-uuid", "1234"):
        assert obs.correlation_id_or_none(bad) is None


def test_a_uuid_is_normalised_to_its_canonical_lowercase_form():
    """Two spellings of one id would split a thread in half at query time."""
    canonical = "7f1a4a26-0d1f-4a1e-9a8e-1b2c3d4e5f60"
    assert obs.correlation_id_or_none(canonical.upper()) == canonical
    assert obs.correlation_id_or_none(canonical.replace("-", "")) == canonical


def test_the_package_defines_the_mint_exactly_once():
    """"Who started this thread?" must have one answer, and D-2's "never mint
    on write" must be checkable by reading one function. Callers of
    `new_correlation_id` are fine and expected; a *second definition* of it is
    how the rule quietly acquires a second, laxer interpretation.
    """
    import pathlib

    package = pathlib.Path(obs.__file__).parent
    definers = [
        path.relative_to(package).as_posix()
        for path in sorted(package.rglob("*.py"))
        if "def new_correlation_id" in path.read_text()
    ]
    assert definers == ["observability.py"]
