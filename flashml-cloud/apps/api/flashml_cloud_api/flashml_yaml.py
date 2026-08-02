"""Parsing and validation for a repo's ``flashml.yaml``.

Pure text in, typed config out — no filesystem or network I/O, so the repo
fetch (Task 3) and preflight (Task 4) can each hand this raw bytes decoded
to text without this module knowing where they came from.

Every rule here is a deliberate refusal rather than a lenient guess:
malformed or ambiguous input should fail loudly with a message that names
the offending key, not be coerced into whatever the parser could make of
it. See the plan's Task 2 section for the rationale behind the two rules
worth calling out specifically: ``args`` is an argv list because there is
no shell to hand a string to, and unknown top-level keys are refused
(rather than ignored) so a typo like ``entrypint`` cannot silently mean
something the author didn't intend.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from math import prod

import yaml

SUPPORTED_VERSION = 1

# num_shards is bounded at 999 downstream (flashruntime's federated
# averaging service); a sweep anywhere near that size is far more likely
# a typo (a stray extra axis, a pasted range) than an intentional launch,
# so the cap is set an order of magnitude below that for headroom.
MAX_SWEEP_COMBINATIONS = 100

# One day. Long enough for a real training run on a volunteer node,
# short enough that a runaway timeout can't hold a lease hostage
# indefinitely.
MAX_TIMEOUT_SECONDS = 24 * 60 * 60

REQUIRED_KEYS = {"version", "name", "image", "entrypoint"}
OPTIONAL_KEYS = {"args", "sweep", "resources", "timeout_seconds",
                 "mode", "rounds", "min_participants", "shards",
                 "local_inputs"}
ALLOWED_KEYS = REQUIRED_KEYS | OPTIONAL_KEYS

#: Today's behaviour, and the default: one round of independent tasks (a
#: sweep, or a single task). Nothing about an ``independent`` config changes
#: because ``federated`` now exists.
MODE_INDEPENDENT = "independent"

#: Federated averaging: the same entrypoint runs once per shard per round,
#: and the API drives the rounds. This is a **contract the user opts into**
#: — see ``preflight``'s ``federated-contract`` check — not something that
#: can be inferred from arbitrary repo code.
MODE_FEDERATED = "federated"

MODES = (MODE_INDEPENDENT, MODE_FEDERATED)

#: A federated run submits one coordinator job per round and the driver
#: holds the loop in memory for its whole duration. The cap is not about
#: arithmetic — it is that each round is a full submit/lease/commit cycle
#: across volunteer machines, so a four-digit round count is a runaway, not
#: a plan.
MAX_ROUNDS = 500

#: The alphabet a ``local_inputs`` label may use.
#:
#: **This is a deliberate duplicate** of flashnode's
#: ``flashnode/config/local_data.py::LABEL_RE``. It cannot be imported: the
#: agent lives in the public ``Zolli-Labs/flashml`` repo and this repo never
#: imports it (AGENTS.md hard rule 2 — the only thing that crosses that
#: boundary is ``flashruntime.protocol``, and a label alphabet is not a wire
#: schema). So the rule is restated here, on purpose, with the two copies
#: pinned to each other by this comment.
#:
#: Both ends must check, because they refuse at different moments. flashnode
#: is the last line of defence — it is the process that holds the directory
#: and joins nothing to a host path. But by the time it refuses, the job has
#: been submitted, compiled, leased and claimed, and its author sees an
#: opaque node-side failure. Refusing here is the earliest point at which a
#: job author can be told they typed the label wrong.
#:
#: Narrower than any filesystem: ``/``, ``..`` and whitespace are exactly the
#: characters that turn a name into a traversal, and they are not expressible.
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ConfigError(Exception):
    """Raised for any flashml.yaml that fails validation, including
    malformed YAML itself — callers never need to catch a raw
    ``yaml.YAMLError`` separately."""


@dataclass(frozen=True)
class FlashmlConfig:
    version: int
    name: str
    image: str
    entrypoint: str
    args: list[str] = field(default_factory=list)
    sweep: dict[str, list] = field(default_factory=dict)
    resources: dict = field(default_factory=dict)
    timeout_seconds: int | None = None
    #: Labels naming data the *host* lends to the task, mounted read-only by
    #: the agent. Never uploaded, never an artifact, never a path: a job names
    #: a label and the host owner alone decides what directory it means.
    #: Empty is the default and the overwhelmingly common case.
    local_inputs: list[str] = field(default_factory=list)
    #: ``independent`` (default, unchanged) or ``federated``.
    mode: str = MODE_INDEPENDENT
    #: Federated only. ``None`` under ``independent``, so a caller that
    #: reads these without checking the mode gets a TypeError rather than a
    #: plausible-looking default.
    rounds: int | None = None
    min_participants: int | None = None
    shards: int | None = None

    @property
    def is_federated(self) -> bool:
        return self.mode == MODE_FEDERATED


def _require_string(raw: dict, key: str) -> str:
    if key not in raw:
        raise ConfigError(f"flashml.yaml is missing required key {key!r}")
    value = raw[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"flashml.yaml {key!r} must be a non-empty string, got {value!r}")
    return value


def parse_flashml_yaml(text: str) -> FlashmlConfig:
    """Parse and fully validate a flashml.yaml's text. Raises
    ``ConfigError`` — never a bare ``yaml.YAMLError``, ``KeyError``, or
    ``TypeError`` — for anything that doesn't produce a safe, complete
    config."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"flashml.yaml is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("flashml.yaml must be a mapping at the top level")

    unknown = set(raw) - ALLOWED_KEYS
    if unknown:
        raise ConfigError(
            f"flashml.yaml has unknown key(s) {sorted(unknown)!r}; "
            f"allowed keys are {sorted(ALLOWED_KEYS)!r}"
        )

    if "version" not in raw:
        raise ConfigError("flashml.yaml is missing required key 'version'")
    version = raw["version"]
    if version != SUPPORTED_VERSION:
        raise ConfigError(
            f"flashml.yaml 'version' must be {SUPPORTED_VERSION}, got {version!r}"
        )

    name = _require_string(raw, "name")
    image = _require_string(raw, "image")
    entrypoint = _require_string(raw, "entrypoint")

    args = _validate_args(raw.get("args", []))
    sweep = _validate_sweep(raw.get("sweep", {}))
    resources = _validate_resources(raw.get("resources", {}))
    timeout_seconds = _validate_timeout_seconds(raw.get("timeout_seconds"))
    local_inputs = _validate_local_inputs(raw.get("local_inputs", []))
    mode, rounds, min_participants, shards = _validate_mode(raw)

    return FlashmlConfig(
        version=version,
        name=name,
        image=image,
        entrypoint=entrypoint,
        args=args,
        sweep=sweep,
        resources=resources,
        timeout_seconds=timeout_seconds,
        local_inputs=local_inputs,
        mode=mode,
        rounds=rounds,
        min_participants=min_participants,
        shards=shards,
    )


def _positive_int(raw: dict, key: str, maximum: int) -> int:
    value = raw[key]
    # bool before int: `True` is an int in Python, and `rounds: true` is a
    # typo that would otherwise silently mean one round.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"flashml.yaml {key!r} must be an integer, got {value!r}")
    if value < 1:
        raise ConfigError(f"flashml.yaml {key!r} must be >= 1, got {value!r}")
    if value > maximum:
        raise ConfigError(
            f"flashml.yaml {key!r} is {value}, above the cap of {maximum}"
        )
    return value


def _validate_mode(raw: dict) -> tuple[str, int | None, int | None, int | None]:
    """``(mode, rounds, min_participants, shards)``.

    Federated-only keys are refused under ``independent`` rather than
    ignored, for the same reason unknown top-level keys are: a config that
    names ``rounds`` has an author who believes rounds are happening, and
    silently running a single round instead is the kind of "worked, but not
    the thing you asked for" outcome this parser exists to prevent.
    """
    mode = raw.get("mode", MODE_INDEPENDENT)
    if mode not in MODES:
        raise ConfigError(
            f"flashml.yaml 'mode' must be one of {list(MODES)!r}, got {mode!r}"
        )

    federated_keys = ("rounds", "min_participants", "shards")
    if mode == MODE_INDEPENDENT:
        present = [k for k in federated_keys if k in raw]
        if present:
            raise ConfigError(
                f"flashml.yaml sets {sorted(present)!r}, which only apply to "
                f"'mode: {MODE_FEDERATED}'; the default mode "
                f"('{MODE_INDEPENDENT}') runs one round of independent tasks"
            )
        return MODE_INDEPENDENT, None, None, None

    missing = [k for k in ("rounds", "min_participants") if k not in raw]
    if missing:
        raise ConfigError(
            f"flashml.yaml 'mode: {MODE_FEDERATED}' also requires "
            f"{sorted(missing)!r} — federated averaging has no sensible "
            f"default for how many rounds to run or how many machines a "
            f"round needs before it averages"
        )

    rounds = _positive_int(raw, "rounds", MAX_ROUNDS)
    min_participants = _positive_int(raw, "min_participants", MAX_SWEEP_COMBINATIONS)
    if "shards" in raw:
        shards = _positive_int(raw, "shards", MAX_SWEEP_COMBINATIONS)
    else:
        # One shard per required participant. Deliberately not "some
        # generous multiple": every extra shard is a task that has to find a
        # volunteer, and a default that dispatched more work than the user
        # asked for would make an idle pool look like a stuck job.
        shards = min_participants
    if min_participants > shards:
        raise ConfigError(
            f"flashml.yaml 'min_participants' ({min_participants}) exceeds "
            f"'shards' ({shards}): a round would need more contributions than "
            f"it dispatches tasks, so quorum could never be reached"
        )

    if raw.get("sweep"):
        # A sweep expands to one task per hyperparameter combination; a
        # federated round expands to one task per shard. Both cannot be the
        # meaning of the same task list, and quietly picking one would train
        # something the author did not describe.
        raise ConfigError(
            f"flashml.yaml cannot combine 'sweep' with 'mode: {MODE_FEDERATED}': "
            f"a sweep's tasks are independent trials, a federated round's tasks "
            f"are shards of one model"
        )

    return MODE_FEDERATED, rounds, min_participants, shards


def _validate_args(args: object) -> list[str]:
    # An argv list, not a shell string: there is no shell on the other
    # end (the executor runs `python <entrypoint> *args` directly), so a
    # string here would either be silently wrong (passed as one giant
    # argument) or invite an entirely unnecessary shell-injection worry.
    if not isinstance(args, list) or isinstance(args, (str, bytes)):
        raise ConfigError(
            f"flashml.yaml 'args' must be a list of strings (an argv list, "
            f"not a shell string), got {args!r}"
        )
    if not all(isinstance(a, str) for a in args):
        raise ConfigError(f"flashml.yaml 'args' must be a list of strings, got {args!r}")
    return list(args)


def _validate_local_inputs(value: object) -> list[str]:
    """``local_inputs: ["patients"]`` → ``["patients"]``; absent → ``[]``.

    A list of **labels**, never a mapping and never a path. The distinction
    is the security property of the whole feature: a job says *what* it needs
    by name, and the host owner alone decides which directory that name means
    (``FLASHNODE_LOCAL_DATA`` on their machine). A mapping here would let a
    submitter name a host path, so the type check below refuses one for the
    same reason ``args`` refuses a shell string — the shape carries meaning.

    Labels are validated against ``LABEL_RE``, flashnode's alphabet restated
    (see its comment). Refusing at submit time is the point: the author is
    here now, and a label rejected on a volunteer's machine ten minutes later
    reads as a platform fault rather than a typo.
    """
    if not isinstance(value, list) or isinstance(value, (str, bytes)):
        raise ConfigError(
            f"flashml.yaml 'local_inputs' must be a list of dataset labels "
            f"(names the host advertises, never paths), got {value!r}"
        )
    for label in value:
        if not isinstance(label, str):
            raise ConfigError(
                f"flashml.yaml 'local_inputs' must contain only strings, got "
                f"{label!r} in {value!r}"
            )
        # `.` and `..` are named explicitly as well as being unmatched by
        # LABEL_RE: flashnode does the same, and a belt-and-braces check on
        # the two strings that mean "a directory other than this one" is
        # worth keeping identical at both ends.
        if not LABEL_RE.match(label) or label in (".", ".."):
            raise ConfigError(
                f"flashml.yaml 'local_inputs' contains an illegal dataset "
                f"label {label!r}: a label is a name, not a path — it must "
                f"start with a letter or digit and use only [A-Za-z0-9._-]"
            )
    return list(value)


def _validate_sweep(sweep: object) -> dict[str, list]:
    if not isinstance(sweep, dict):
        raise ConfigError(f"flashml.yaml 'sweep' must be a mapping, got {sweep!r}")

    validated: dict[str, list] = {}
    for key, values in sweep.items():
        if not isinstance(values, list) or len(values) == 0:
            raise ConfigError(
                f"flashml.yaml 'sweep.{key}' must be a non-empty list, got {values!r}"
            )
        validated[key] = list(values)

    if validated:
        combinations = prod(len(v) for v in validated.values())
        if combinations > MAX_SWEEP_COMBINATIONS:
            raise ConfigError(
                f"flashml.yaml 'sweep' would expand to {combinations} combinations "
                f"(cap is {MAX_SWEEP_COMBINATIONS}); a sweep this large is more "
                f"likely a mistake than an intention"
            )

    return validated


def _validate_resources(resources: object) -> dict:
    if not isinstance(resources, dict):
        raise ConfigError(f"flashml.yaml 'resources' must be a mapping, got {resources!r}")
    return dict(resources)


def _validate_timeout_seconds(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"flashml.yaml 'timeout_seconds' must be an integer, got {value!r}")
    if value <= 0:
        raise ConfigError(f"flashml.yaml 'timeout_seconds' must be positive, got {value!r}")
    if value > MAX_TIMEOUT_SECONDS:
        raise ConfigError(
            f"flashml.yaml 'timeout_seconds' {value} exceeds the cap of "
            f"{MAX_TIMEOUT_SECONDS} ({MAX_TIMEOUT_SECONDS // 3600}h)"
        )
    return value
