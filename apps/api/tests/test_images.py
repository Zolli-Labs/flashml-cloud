"""The curated image registry.

M1 closes the image set: a repo names a short alias in flashml.yaml, and
this module is the single place that alias resolves to a pinned reference
and a package manifest. Preflight (Task 4) trusts ``packages`` to be
exhaustive enough that a legitimate ``import json`` is never flagged, and
trusts ``reference`` to be pinned enough that the image a node pulls is
provably the one preflight validated against.
"""
from __future__ import annotations

import pytest

from flashml_cloud_api.images import CURATED, CuratedImage, UnknownImage, resolve_image

EXPECTED_ALIASES = {"python-slim", "sklearn", "pytorch-cpu"}


def test_every_curated_alias_resolves():
    for alias in EXPECTED_ALIASES:
        image = resolve_image(alias)
        assert isinstance(image, CuratedImage)
        assert image.alias == alias


def test_curated_dict_has_exactly_the_three_m1_aliases():
    assert set(CURATED.keys()) == EXPECTED_ALIASES


def test_unknown_alias_raises_and_lists_the_real_ones():
    with pytest.raises(UnknownImage) as exc_info:
        resolve_image("totally-not-an-alias")
    message = str(exc_info.value)
    for alias in EXPECTED_ALIASES:
        assert alias in message


def test_every_reference_is_fully_pinned():
    # registry/name:tag, tag must not be "latest" — a floating tag would
    # let a node silently run a different image than the one preflight
    # validated against.
    for alias, image in CURATED.items():
        ref = image.reference
        assert "/" in ref, f"{alias}: reference {ref!r} missing a registry/path component"
        assert ":" in ref, f"{alias}: reference {ref!r} is not tagged"
        tag = ref.rsplit(":", 1)[1]
        assert tag != "latest", f"{alias}: reference {ref!r} floats on 'latest'"
        assert tag, f"{alias}: reference {ref!r} has an empty tag"


def test_packages_include_representative_stdlib_module():
    # So preflight doesn't flag `import json` in every user's repo.
    for alias, image in CURATED.items():
        assert "json" in image.packages, f"{alias} manifest is missing stdlib 'json'"


def test_packages_is_a_frozenset():
    for image in CURATED.values():
        assert isinstance(image.packages, frozenset)


def test_sklearn_image_provides_scientific_stack():
    image = resolve_image("sklearn")
    for pkg in ("numpy", "pandas", "sklearn", "scipy"):
        assert pkg in image.packages


def test_pytorch_cpu_image_provides_torch_and_numpy():
    image = resolve_image("pytorch-cpu")
    for pkg in ("torch", "numpy"):
        assert pkg in image.packages


def test_python_slim_does_not_claim_third_party_packages():
    image = resolve_image("python-slim")
    for pkg in ("torch", "numpy", "sklearn", "pandas", "scipy"):
        assert pkg not in image.packages


def test_each_curated_image_has_a_description():
    for alias, image in CURATED.items():
        assert isinstance(image.description, str) and image.description.strip()
