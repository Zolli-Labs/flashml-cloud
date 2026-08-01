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
OPTIONAL_KEYS = {"args", "sweep", "resources", "timeout_seconds"}
ALLOWED_KEYS = REQUIRED_KEYS | OPTIONAL_KEYS


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

    return FlashmlConfig(
        version=version,
        name=name,
        image=image,
        entrypoint=entrypoint,
        args=args,
        sweep=sweep,
        resources=resources,
        timeout_seconds=timeout_seconds,
    )


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
