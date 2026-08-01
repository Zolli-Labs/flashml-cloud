"""The curated image registry.

M1 does not build images from a repo's ``Dockerfile`` (see the plan's "Why
no image build": no build host, no registry, no allowlist propagation to
volunteer nodes). Instead a ``flashml.yaml`` names one of a short, closed
set of pre-pulled images by alias, and this module is the only place that
alias resolves to a concrete, pinned reference.

``packages`` is the top-level importable module name each image provides,
**including the standard library** — Task 4's ``unknown-import`` check
diffs a repo's top-level imports against this set, so it must be generous
enough that a plain ``import json`` is never a false positive, and honest
enough that ``import torch`` is never a false negative against
``python-slim``.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

# The interpreter's own module manifest is the stdlib baseline every
# curated image shares — no need to hand-maintain a list of ~300 names,
# and this stays correct if the base image's Python version changes.
_STDLIB = frozenset(sys.stdlib_module_names)


@dataclass(frozen=True)
class CuratedImage:
    """One entry in the closed M1 image set.

    ``reference`` must always be fully pinned (registry, name, and tag —
    never a bare ``latest``): a floating tag would let a node pull a
    different image than the one preflight validated a repo's imports
    against, silently invalidating the preflight guarantee.
    """

    alias: str
    reference: str
    packages: frozenset[str]
    description: str


class UnknownImage(Exception):
    """Raised by ``resolve_image`` for an alias outside the closed M1 set.

    The message lists the real aliases so the caller (ultimately a user's
    flashml.yaml validation error) is actionable rather than a bare KeyError.
    """

    def __init__(self, alias: str):
        available = ", ".join(sorted(CURATED))
        super().__init__(
            f"unknown image alias {alias!r}; available aliases: {available}"
        )
        self.alias = alias


CURATED: dict[str, CuratedImage] = {
    "python-slim": CuratedImage(
        alias="python-slim",
        reference="docker.io/library/python:3.11.9-slim",
        packages=frozenset(_STDLIB),
        description="Bare CPython 3.11 with only the standard library. "
        "For jobs whose dependencies are entirely stdlib.",
    ),
    "sklearn": CuratedImage(
        alias="sklearn",
        reference="docker.io/library/flashml-sklearn:2026.07.1",
        packages=frozenset(_STDLIB)
        | {
            "numpy",
            "pandas",
            "sklearn",
            "scipy",
            "joblib",
            "threadpoolctl",
        },
        description="Classical ML: numpy, pandas, scikit-learn, scipy.",
    ),
    "pytorch-cpu": CuratedImage(
        alias="pytorch-cpu",
        reference="docker.io/pytorch/pytorch:2.3.1-cpu",
        packages=frozenset(_STDLIB)
        | {
            "torch",
            "numpy",
        },
        description="PyTorch (CPU-only build) plus numpy, for training jobs "
        "that don't need a GPU node.",
    ),
}


def resolve_image(alias: str) -> CuratedImage:
    """Resolve a flashml.yaml ``image:`` alias to its pinned reference and
    package manifest. Raises ``UnknownImage`` — never returns None and
    never raises a bare KeyError — for anything outside the closed set."""
    try:
        return CURATED[alias]
    except KeyError:
        raise UnknownImage(alias) from None
