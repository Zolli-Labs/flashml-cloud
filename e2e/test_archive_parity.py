"""Cross-repo archive-extractor parity guard.

The same attacker-controlled bytes are unpacked twice in this system: once
by the cloud API (``flashml_cloud_api.repo.extract_safely``, inspecting a
submitted GitHub repo) and once by the host agent
(``flashnode.executor.archives.extract_archive_safely``, staging that same
tarball onto a *volunteer's laptop*). The second one is where a miss
actually hurts — the API runs on our hardware, flashnode runs on someone
else's.

The two extractors cannot share code: flashnode may import
``flashruntime.protocol`` and nothing else (flashnode/AGENTS.md hard rule
2), and the cloud API is private forever. So the guard is duplicated, and
duplication drifts. This file is the only place in the workspace allowed to
import both repos, which makes it the only place the drift can be caught —
the same reason ``test_allowlist_parity.py`` exists, and the same class of
outage it was written after.

The corpus is built once, in-process, with ``tarfile``. It is fed to both
extractors. A hand-mirrored list of "attacks we believe both handle" is not
a guard; running the identical bytes through both is.

Two things keep this test honest:

- a **benign control** that both must ACCEPT. Without it, an extractor that
  refused everything would pass every assertion above.
- every attack tarball is the benign control **plus one hostile member**,
  so a refusal is attributable to that member and not to some unrelated
  structural rule (the API, for instance, also requires exactly one
  top-level directory — a real rule, but not a security one).
"""

from __future__ import annotations

import io
import sys
import tarfile
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
CLOUD_API_SRC = WORKSPACE / "flashml-cloud" / "apps" / "api"

# The private API is not installed into e2e/.venv (its dependency tree —
# fastapi, psycopg — has no business in the harness). `repo.py` needs only
# httpx, which flashruntime[service] already brings, so putting its source
# on the path is enough and keeps the harness light.
if str(CLOUD_API_SRC) not in sys.path:
    sys.path.insert(0, str(CLOUD_API_SRC))

from flashml_cloud_api.repo import RepoError, extract_safely  # noqa: E402
from flashnode.executor.archives import (  # noqa: E402
    ArchiveError,
    extract_archive_safely,
)

TOP = "repo-abc123"
MAX_BYTES = 1 << 20
MAX_MEMBERS = 1000


# -- corpus ------------------------------------------------------------------

def _file(name: str, content: bytes = b"x"):
    info = tarfile.TarInfo(name=name)
    info.size = len(content)
    info.type = tarfile.REGTYPE
    return info, content


def _dir(name: str):
    info = tarfile.TarInfo(name=name.rstrip("/") + "/")
    info.type = tarfile.DIRTYPE
    return info, None


def _special(name: str, kind: int, linkname: str = ""):
    info = tarfile.TarInfo(name=name)
    info.type = kind
    info.linkname = linkname
    return info, None


def _tar(members) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for info, body in members:
            tar.addfile(info, io.BytesIO(body) if body is not None else None)
    return buf.getvalue()


#: The shape a legitimate GitHub tarball has: one top-level directory with
#: ordinary files under it. Every attack below is this, plus one member.
BENIGN = [_dir(TOP), _file(f"{TOP}/train.py", b"print(1)\n")]


def _attack(*members) -> bytes:
    return _tar(BENIGN + list(members))


def corpus(escape_target: Path) -> dict[str, bytes]:
    """Attack tarballs both extractors must refuse.

    ``escape_target`` is a path outside either destination — the canary the
    attacks try to write to, checked afterwards.
    """
    return {
        "relative-traversal": _attack(_file(f"{TOP}/../../pwned.txt", b"owned")),
        "absolute-path": _attack(_file(str(escape_target), b"owned")),
        # pathlib's absolute-join trap in its purest form: joining a
        # destination with "/etc/evil.txt" yields "/etc/evil.txt" — the
        # destination is silently discarded, so a join-then-compare guard
        # inspects a path that was never inside it and sees nothing wrong.
        "absolute-path-etc": _attack(_file("/etc/flashml-pwned.txt", b"owned")),
        "deep-traversal": _attack(_file(f"{TOP}/a/b/../../../../../pwned.txt", b"owned")),
        "symlink-relative-escape": _attack(
            _special(f"{TOP}/link", tarfile.SYMTYPE, "../../../../etc/passwd")),
        "symlink-absolute-escape": _attack(
            _special(f"{TOP}/link", tarfile.SYMTYPE, "/etc/passwd")),
        "symlink-to-parent": _attack(
            _special(f"{TOP}/link", tarfile.SYMTYPE, str(escape_target.parent))),
        "hardlink-escape": _attack(
            _special(f"{TOP}/hard", tarfile.LNKTYPE, "../../../etc/passwd")),
        "fifo-member": _attack(_special(f"{TOP}/pipe", tarfile.FIFOTYPE)),
        "chardev-member": _attack(_special(f"{TOP}/dev", tarfile.CHRTYPE)),
        "blockdev-member": _attack(_special(f"{TOP}/blk", tarfile.BLKTYPE)),
        # ~4 MiB of zeros in a few KB on the wire: only detectable while
        # unpacking, which is why both caps are enforced mid-extraction.
        "decompression-bomb": _attack(
            _file(f"{TOP}/bomb.bin", b"\0" * (4 * 1024 * 1024))),
        "not-an-archive": b"PK\x03\x04 this is not a tarball at all",
    }


# -- the two extractors, behind one interface --------------------------------

def _run_flashnode(data: bytes, dest: Path, max_bytes: int) -> None:
    src = dest.parent / "input.bin"
    src.write_bytes(data)
    extract_archive_safely(src, dest, max_bytes, MAX_MEMBERS)


def _run_cloud_api(data: bytes, dest: Path, max_bytes: int) -> None:
    extract_safely(data, dest, max_bytes)


EXTRACTORS = {
    "flashnode": (_run_flashnode, ArchiveError),
    "cloud-api": (_run_cloud_api, RepoError),
}


@pytest.mark.parametrize("side", sorted(EXTRACTORS))
@pytest.mark.parametrize("attack", sorted(corpus(Path("/nonexistent/canary.txt"))))
def test_both_extractors_refuse_the_same_attack(tmp_path, side, attack):
    """One corpus, two extractors, identical verdict required.

    The bomb is given a cap small enough to trip; every other attack is run
    with the full cap so that a refusal can only come from the hostile
    member, never from a size limit doing the work by accident.
    """
    canary = tmp_path / "canary" / "pwned.txt"
    canary.parent.mkdir()
    data = corpus(canary)[attack]

    run, error_type = EXTRACTORS[side]
    dest = tmp_path / side / "dest"
    dest.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = 64 * 1024 if attack == "decompression-bomb" else MAX_BYTES

    with pytest.raises(error_type):
        run(data, dest, max_bytes)

    # Nothing escaped, and nothing half-written was left behind for a later
    # stage to mistake for a successful unpack.
    assert not canary.exists(), f"{side} let {attack} write outside its destination"
    assert not Path("/etc/flashml-pwned.txt").exists()
    assert not dest.exists(), f"{side} left a partial tree behind after refusing {attack}"


@pytest.mark.parametrize("side", sorted(EXTRACTORS))
def test_both_extractors_accept_the_benign_control(tmp_path, side):
    """The assertion that keeps every refusal above meaningful: an
    extractor that rejected all input would satisfy them all and fail
    here."""
    run, _ = EXTRACTORS[side]
    dest = tmp_path / side / "dest"
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(_tar(BENIGN), dest, MAX_BYTES)
    assert (dest / TOP / "train.py").read_bytes() == b"print(1)\n"


@pytest.mark.parametrize("side", sorted(EXTRACTORS))
def test_both_extractors_agree_on_the_content_root(tmp_path, side):
    """Both return the wrapper directory, not the destination.

    This is the functional half of the same seam: the API reads
    ``flashml.yaml`` from that root and compiles argv as
    ``python /work/inputs/code/<entrypoint>`` — no wrapper in it. If
    flashnode returned the destination instead, every repo job would look
    for its entrypoint one directory too high and fail exactly the way the
    missing unpacking made it fail.
    """
    dest = tmp_path / side / "dest"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if side == "flashnode":
        src = dest.parent / "input.bin"
        src.write_bytes(_tar(BENIGN))
        root = extract_archive_safely(src, dest, MAX_BYTES, MAX_MEMBERS)
    else:
        root = extract_safely(_tar(BENIGN), dest, MAX_BYTES)
    assert root.name == TOP
    assert (root / "train.py").is_file()


# -- formerly divergent, now closed -----------------------------------------
#
# These three started life as *documented divergences*: cases flashnode
# refused and the API did not, asserted in the safe direction (flashnode, the
# one running on a stranger's machine, being the stricter side) and recorded
# as the weaker side's known gaps. The API has since grown all three, so they
# are now ordinary parity assertions — and they must stay parity assertions:
# a refusal removed from either side fails here.

def test_both_extractors_cap_member_count(tmp_path):
    """Empty files cost inodes, not bytes, so a byte cap alone does not
    bound them. Was: flashnode-only."""
    data = _tar(BENIGN + [_file(f"{TOP}/f{i}", b"") for i in range(60)])

    src = tmp_path / "input.bin"
    src.write_bytes(data)
    with pytest.raises(ArchiveError):
        extract_archive_safely(src, tmp_path / "fn", MAX_BYTES, max_members=10)

    with pytest.raises(RepoError):
        extract_safely(data, tmp_path / "api", MAX_BYTES, max_members=10)


def test_both_extractors_refuse_contained_hardlinks(tmp_path):
    """A hard link whose target stays *inside* the destination is still
    refused by both: nothing in a code archive needs one, and a member that
    is an alias for a file something else is about to rewrite is not worth
    tolerating for zero legitimate use. Was: flashnode-only (the API
    accepted and silently skipped it)."""
    data = _tar(BENIGN + [_special(f"{TOP}/alias", tarfile.LNKTYPE, f"{TOP}/train.py")])

    src = tmp_path / "input.bin"
    src.write_bytes(data)
    with pytest.raises(ArchiveError):
        extract_archive_safely(src, tmp_path / "fn", MAX_BYTES, MAX_MEMBERS)

    with pytest.raises(RepoError):
        extract_safely(data, tmp_path / "api", MAX_BYTES)


def test_the_bare_root_member_is_a_clean_refusal_on_both_sides(tmp_path):
    """GNU tar emits a ``./`` member for the archive root. Its path has zero
    components, so ``PurePosixPath(name).parts[0]`` used to raise IndexError
    in the API — not a ``RepoError``, so a malformed upload came back as a
    500 instead of a 400. Not a containment break, but a crash a submitter
    controls.

    flashnode never shared the crash: it treats such a member as having no
    top level and extracts the rest. The API now refuses the archive
    outright (GitHub never produces one). Different verdicts, both clean —
    what this asserts is that neither side raises something its caller does
    not know how to turn into a user-facing error.
    """
    data = _tar([_dir("."), _file("./train.py", b"print(1)\n")])

    src = tmp_path / "input.bin"
    src.write_bytes(data)
    root = extract_archive_safely(src, tmp_path / "fn", MAX_BYTES, MAX_MEMBERS)
    assert (root / "train.py").is_file()

    with pytest.raises(RepoError):
        extract_safely(data, tmp_path / "api", MAX_BYTES)
