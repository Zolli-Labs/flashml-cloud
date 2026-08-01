"""Every image the cloud can name, a volunteer's node will actually run.

This assertion cannot live in either repo. flashml-cloud must not import
flashnode and flashnode must not import flashml-cloud (hard rule 1 on both
sides), so each suite can only check its own half — and each half passes
perfectly while disagreeing with the other. That is not hypothetical here:
this exact seam has broken four times (M1_DECISIONS.md D12), including
`fedavg_worker` being allowlisted by the coordinator but not by flashnode,
and `unpack_inputs` being honoured by flashnode but emitted by nobody.

The failure mode is always the same shape. Both test suites are green, the
deploy succeeds, and the first real job dies on a stranger's laptop with an
error that reads like their machine is broken.
"""
from __future__ import annotations

import pytest

from flashml_cloud_api.images import CURATED, REGISTRY_PREFIX
from flashnode.executor.images import (
    DEFAULT_ALLOWED_IMAGE_PREFIXES,
    image_is_allowed,
    split_reference,
)


@pytest.mark.parametrize("alias", sorted(CURATED))
def test_every_curated_reference_is_runnable_by_an_unconfigured_node(alias):
    """The volunteer's actual configuration: nothing set, defaults only."""
    reference = CURATED[alias].reference
    assert image_is_allowed(reference), (
        f"the cloud resolves {alias!r} to {reference!r}, which a default "
        "flashnode install refuses to run"
    )


@pytest.mark.parametrize("alias", sorted(CURATED))
def test_every_curated_reference_parses_the_same_way_on_both_sides(alias):
    """flashnode authorizes the parsed NAME, not the raw string. If the two
    sides disagreed about where the name ends, the allowlist would be
    checking something other than what docker pulls."""
    reference = CURATED[alias].reference
    name, version = split_reference(reference)
    assert reference == f"{name}:{version}"
    assert name.startswith(REGISTRY_PREFIX.rstrip("-").rsplit("/", 1)[0])


def test_the_two_namespace_constants_agree():
    """Both sides hardcode the namespace. Nothing but this test makes them
    equal, so a rename on one side has to fail here rather than in
    production."""
    assert any(
        REGISTRY_PREFIX.startswith(prefix) or prefix.startswith(REGISTRY_PREFIX)
        for prefix in DEFAULT_ALLOWED_IMAGE_PREFIXES
    ), (
        f"cloud publishes to {REGISTRY_PREFIX!r} but nodes trust "
        f"{sorted(DEFAULT_ALLOWED_IMAGE_PREFIXES)!r}"
    )


def test_a_reference_outside_the_namespace_is_still_refused():
    """Guards against 'fixing' the seam by widening the node until anything
    passes — which would make every test above vacuous."""
    assert image_is_allowed("docker.io/library/python:3.11.9-slim") is False
    assert image_is_allowed("ghcr.io/someone-else/flashml-sklearn:1") is False
