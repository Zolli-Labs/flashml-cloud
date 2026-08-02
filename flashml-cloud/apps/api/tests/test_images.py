"""The curated image registry.

M1 closes the image set: a repo names a short alias in flashml.yaml, and
this module is the single place that alias resolves to a pinned reference
and a package manifest. Preflight (Task 4) trusts ``packages`` to be
exhaustive enough that a legitimate ``import json`` is never flagged, and
trusts ``reference`` to be pinned enough that the image a node pulls is
provably the one preflight validated against.
"""
from __future__ import annotations

import json

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


# --- the reference must name something that actually exists ----------------
#
# Every reference in CURATED was previously wrong in a different way, and no
# test noticed because each one was a plausible-looking string: python-slim
# and pytorch-cpu named upstream bases that declare no non-root USER, and
# sklearn named `docker.io/library/flashml-sklearn`, which can never exist
# because docker.io/library is Docker's official-images namespace. These
# tests pin the properties that would have caught all three.


# The image SOURCES and their publish workflow moved to the public monorepo
# Zolli-Labs/flashml on 2026-08-01, so GHCR creates the packages public —
# visibility is inherited from the publishing repo, and a private package
# fails a volunteer's anonymous pull with an authentication error that looks
# nothing like "this package is private".
#
# The tests that used to read that workflow off disk are gone with it. They
# were weak anyway: they compared two strings in two files and could not tell
# whether an image EXISTS or is REACHABLE. All three were unpullable for
# fourteen hours while those assertions passed.
#
# What replaces them checks the thing that actually matters, from the position
# that actually matters: can a stranger, with no credentials, pull every
# reference this API is capable of emitting?


@pytest.mark.network
def test_every_curated_image_is_anonymously_pullable():
    """The check that would have caught the outage.

    A volunteer's Docker has no GitHub credentials and must never need any.
    This asks GHCR for an anonymous pull token and lists the tags — exactly
    what `docker pull` does first — for every reference the API can emit.

    Marked `network` and deselected by default: it reaches the public
    internet, so it must not fail a suite run on a plane. CI runs it with
    `-m network`, which is where a regression here needs to be caught.
    """
    import urllib.error
    import urllib.request

    from flashml_cloud_api.images import CURATED

    unreachable = []
    for alias, image in CURATED.items():
        # ghcr.io/zolli-labs/flashml-sklearn:TAG -> zolli-labs/flashml-sklearn
        repo = image.reference.split("/", 1)[1].rsplit(":", 1)[0]
        try:
            with urllib.request.urlopen(
                f"https://ghcr.io/token?scope=repository:{repo}:pull&service=ghcr.io",
                timeout=30,
            ) as r:
                token = json.load(r)["token"]
            req = urllib.request.Request(
                f"https://ghcr.io/v2/{repo}/tags/list",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                tags = json.load(r).get("tags") or []
        except urllib.error.HTTPError as exc:
            unreachable.append(f"{alias}: {repo} -> HTTP {exc.code} (private?)")
            continue

        expected = image.reference.rsplit(":", 1)[1]
        if expected not in tags:
            unreachable.append(f"{alias}: {repo} has no tag {expected!r}")

    assert not unreachable, (
        "a volunteer cannot pull these, so every job assigned to them fails "
        "at execution:\n  " + "\n  ".join(unreachable)
    )


def test_references_live_in_our_own_namespace():
    """Not an upstream base. Upstream images declare no non-root USER, which
    is the precondition flashnode relies on when it omits --user on Windows.
    """
    from flashml_cloud_api.images import CURATED, IMAGE_TAG, REGISTRY_PREFIX

    for alias, image in CURATED.items():
        assert image.reference.startswith(REGISTRY_PREFIX), (
            f"{alias} resolves to {image.reference!r}, outside our namespace"
        )
        assert "docker.io" not in image.reference
        assert image.reference.endswith(f":{IMAGE_TAG}")
