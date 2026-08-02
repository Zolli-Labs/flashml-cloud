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

#: Registry namespace for the curated images. Ours, and PUBLIC — a private
#: package would make every volunteer authenticate to GHCR before their
#: first job, which is exactly the per-device setup cost these images exist
#: to avoid. flashnode's built-in allowlist trusts this prefix, so
#: publishing a new curated image needs no change on any host.
REGISTRY_PREFIX = "ghcr.io/zolli-labs/flashml-"

#: Version of the curated image set. IMMUTABLE: bump for any change, never
#: repush an existing tag. Docker serves a cached image when the tag string
#: is unchanged, so a repushed tag would reach only hosts that had never
#: pulled it — and preflight validates a user's imports against the manifest
#: below for THIS version, a guarantee that a mutated tag silently voids.
#:
#: THIS TAG EXISTS IN THREE PLACES, IN TWO REPOS. All three must agree:
#:
#:   1. this constant                                  (PRIVATE, here)
#:   2. `IMAGE_TAG` in .github/workflows/images.yml    (PUBLIC flashml)
#:   3. `PROBE_IMAGE` in flashnode/flashnode/doctor.py (PUBLIC flashml)
#:      — `ghcr.io/zolli-labs/flashml-python-slim:<tag>`, inline in the string
#:
#: (3) was found in review on 2026-08-02 and nothing documented it, here or
#: there; the public `images/README.md` publish checklist still lists only
#: (1) and (2). It is the dangerous one precisely because it never breaks:
#: old tags persist, so after a bump a host's `doctor` cheerfully verifies it
#: can pull an image the fleet no longer runs, and every check passes. That
#: is the exact class of undetected divergence `doctor` was written to catch.
#:
#: How the agreement is enforced, and where each check stops:
#:   * tests/test_images.py compares all three against a sibling checkout of
#:     the public repo. Best-effort — it skips when no such checkout exists,
#:     so it cannot run in this repo's CI. It fires where the bump is actually
#:     performed, by someone with both repos open.
#:   * `test_every_curated_image_is_anonymously_pullable` (marked `network`,
#:     run in CI with `-m network`) is the check that proves an image EXISTS
#:     and is reachable without credentials. A string comparison between files
#:     never could — all three references were unpullable for fourteen hours
#:     while assertions like the above passed, which is why the predecessors
#:     of these tests were deleted.
#:
#: ORDERING, and it matters: this constant is a CONSUMER pin naming what the
#: API emits, so it may only move to a tag that already exists. The public
#: workflow (2) is the producer and bumps FIRST. Moving this one ahead of
#: publication points every job at four tags nobody pushed.
IMAGE_TAG = "2026.08.2"


def _reference(alias: str) -> str:
    """Full pinned reference for a curated image alias."""
    return f"{REGISTRY_PREFIX}{alias}:{IMAGE_TAG}"


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
        reference=_reference("python-slim"),
        packages=frozenset(_STDLIB),
        description="Bare CPython 3.11 with only the standard library. "
        "For jobs whose dependencies are entirely stdlib.",
    ),
    "sklearn": CuratedImage(
        alias="sklearn",
        reference=_reference("sklearn"),
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
        reference=_reference("pytorch-cpu"),
        packages=frozenset(_STDLIB)
        | {
            "torch",
            "numpy",
        },
        description="PyTorch (CPU-only build) plus numpy, for training jobs "
        "that don't need a GPU node.",
    ),
    "pytorch-cuda": CuratedImage(
        alias="pytorch-cuda",
        reference=_reference("pytorch-cuda"),
        # Deliberately IDENTICAL to pytorch-cpu above. images/pytorch-cuda's
        # Dockerfile installs the same two packages on a CUDA build of the
        # same library — it differs in its base, its --index-url, and a torch
        # version forced by cu124 wheels starting at 2.4.0, not in what it
        # makes importable. Preflight validates a user's imports against these
        # manifests, so a divergence would let one repo pass against
        # pytorch-cpu and be rejected against pytorch-cuda for no real reason.
        packages=frozenset(_STDLIB)
        | {
            "torch",
            "numpy",
        },
        description="PyTorch (CUDA 12.4 build) plus numpy, for jobs that "
        "request a GPU. Only ever pulled by a host that advertised one; it "
        "is several gigabytes and shares no layer with the other images.",
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
