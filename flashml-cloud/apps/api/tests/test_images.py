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

EXPECTED_ALIASES = {"python-slim", "sklearn", "pytorch-cpu", "pytorch-cuda"}


def test_every_curated_alias_resolves():
    for alias in EXPECTED_ALIASES:
        image = resolve_image(alias)
        assert isinstance(image, CuratedImage)
        assert image.alias == alias


def test_curated_dict_has_exactly_the_expected_aliases():
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


def test_pytorch_cuda_image_provides_torch_and_numpy():
    image = resolve_image("pytorch-cuda")
    for pkg in ("torch", "numpy"):
        assert pkg in image.packages


def test_pytorch_cuda_and_pytorch_cpu_declare_the_same_packages():
    """The two Dockerfiles deliberately install the same set.

    ``images/pytorch-cuda/Dockerfile`` differs from ``pytorch-cpu`` only in
    its base, its CUDA index-url, and a torch version bump forced by cu124
    wheels starting at 2.4.0 — not in what it makes importable. Preflight
    validates a user's imports against these manifests, so a divergence here
    would let the same repo pass against one image and be rejected against
    the other purely because someone edited one entry.
    """
    assert resolve_image("pytorch-cuda").packages == resolve_image("pytorch-cpu").packages


def test_pytorch_cuda_does_not_claim_the_scientific_stack():
    # Guards against the entry being copy-pasted from `sklearn`.
    image = resolve_image("pytorch-cuda")
    for pkg in ("sklearn", "pandas", "scipy"):
        assert pkg not in image.packages


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


# --- the tag lives in THREE files, in two repos -----------------------------
#
# `.github/workflows/images.yml` IMAGE_TAG   (public flashml)
# `flashml_cloud_api/images.py`  IMAGE_TAG   (this repo)
# `flashnode/flashnode/doctor.py` PROBE_IMAGE (public flashml, tag inline)
#
# The third was found in review on 2026-08-02 and nothing documented it. It
# does not break on a bump — old tags persist — so the drift is SILENT: a
# host's doctor probes an image two releases old while the fleet runs current
# ones, and every check passes.
#
# The tests below are best-effort. They read a sibling checkout of the public
# repo when one is present and skip when it is not, so they cannot run in this
# repo's CI, which has no such checkout. That is the point and the limit: the
# bump is performed by someone with both repos open, which is exactly where
# these fire. They do not replace `test_every_curated_image_is_anonymously_
# pullable` above, which is the check that proves an image EXISTS — a string
# comparison between files never could, and the predecessors of these tests
# were deleted for claiming otherwise.


def _public_repo_root():
    """Locate a sibling checkout of the PUBLIC Zolli-Labs/flashml, or None."""
    import pathlib

    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "flashml"
        if (candidate / ".github/workflows/images.yml").is_file():
            return candidate
    return None


def _skip_without_public_repo():
    root = _public_repo_root()
    if root is None:
        pytest.skip(
            "no sibling checkout of the public Zolli-Labs/flashml; clone it "
            "next to this repo to check the cross-repo IMAGE_TAG agreement"
        )
    return root


@pytest.mark.xfail(
    strict=True,
    reason=(
        "IMAGE_TAG bumped to 2026.08.2 here to publish the new pytorch-cuda "
        "image; the PUBLIC repo still says 2026.08.1. Bump IMAGE_TAG in "
        "flashml/.github/workflows/images.yml to 2026.08.2 and let it publish. "
        "strict=True: this FAILS as XPASS the day that lands, which is the "
        "signal to delete this marker."
    ),
)
def test_image_tag_agrees_with_the_public_images_workflow():
    import re

    from flashml_cloud_api.images import IMAGE_TAG

    root = _skip_without_public_repo()
    workflow = (root / ".github/workflows/images.yml").read_text()
    match = re.search(r"^\s*IMAGE_TAG:\s*[\"']?([^\"'\s]+)", workflow, re.M)
    assert match, "no IMAGE_TAG in the public images workflow"
    assert match.group(1) == IMAGE_TAG, (
        f"IMAGE_TAG is {IMAGE_TAG!r} here and {match.group(1)!r} in the public "
        "workflow. The registry tag is immutable and the workflow refuses a "
        "repush, so both must be bumped together."
    )


def test_the_workflow_builds_every_curated_alias():
    """A CURATED entry the workflow never builds is an image that cannot exist.

    This is the failure `pytorch-cuda` would have had: the API happily emits
    a reference for any alias in CURATED, and a volunteer's pull is the first
    thing that finds out nobody published it.
    """
    root = _skip_without_public_repo()
    workflow = (root / ".github/workflows/images.yml").read_text()
    missing = [alias for alias in CURATED if alias not in workflow]
    assert not missing, (
        f"in CURATED but not in the public images workflow matrix: {missing}. "
        "Every job assigned this image fails at pull time."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "The third copy, and the one nobody documented. doctor.py still "
        "hardcodes :2026.08.1 while this repo moved to :2026.08.2. Owned by "
        "the PUBLIC repo and not fixable here — see the flashnode worker. "
        "The durable fix is for doctor.py to derive PROBE_IMAGE from a single "
        "tag constant instead of baking one into a string. strict=True: this "
        "FAILS as XPASS once that lands, which is the signal to delete this "
        "marker."
    ),
)
def test_flashnode_doctor_probes_a_tag_this_repo_still_publishes():
    """The third, undocumented copy of the tag.

    `doctor.py` hardcodes `PROBE_IMAGE = ghcr.io/zolli-labs/flashml-python-slim:<tag>`.
    It keeps working after a bump because old tags persist, so nothing fails
    — the host is simply probing an image the fleet no longer runs, which is
    precisely the class of drift `doctor` exists to detect and would itself
    be exhibiting.

    Owned by the public repo; this repo can only observe it. When this fails,
    the fix belongs there.
    """
    import re

    from flashml_cloud_api.images import IMAGE_TAG

    root = _skip_without_public_repo()
    doctor = root / "flashnode/flashnode/doctor.py"
    if not doctor.is_file():
        pytest.skip("public checkout has no flashnode/flashnode/doctor.py")
    match = re.search(r"^PROBE_IMAGE\s*=\s*[\"']([^\"']+)[\"']", doctor.read_text(), re.M)
    assert match, "no PROBE_IMAGE in flashnode/doctor.py"
    probe_tag = match.group(1).rsplit(":", 1)[1]
    assert probe_tag == IMAGE_TAG, (
        f"flashnode doctor probes tag {probe_tag!r} but the API publishes "
        f"{IMAGE_TAG!r}. Old tags persist, so this drifts silently: hosts "
        "verify an image the fleet no longer uses. Fix in the PUBLIC repo, "
        "flashnode/flashnode/doctor.py."
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
