"""Fetching a GitHub repo tarball and extracting it safely.

This is the one module in the "GitHub repo -> job" path that handles
attacker-controlled bytes directly: any signed-in user can point
``fetch_repo_tarball`` at any public repo they like, and the tarball that
comes back is untrusted input in every sense — member paths, symlink
targets, and declared sizes are all attacker-chosen.

Two separable concerns, two functions:

- ``fetch_repo_tarball`` only fetches. It does not look inside the bytes.
- ``extract_safely`` only extracts. It never touches the network, so the
  malicious-tarball tests can build one entirely with ``tarfile`` and
  feed it straight in.

``extract_safely`` checks size *while* extracting, not after: a
decompression bomb is small on the wire and only detectable as it is
unpacked, so checking a total after the fact would already have written
an unbounded amount of data to disk (or exhausted memory) before the
check ever ran.
"""
from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path, PurePosixPath

import httpx

# Read/write in bounded chunks so a single oversized member is caught
# partway through rather than only after fully materializing it.
_CHUNK_SIZE = 1 << 16  # 64 KiB

_FETCH_TIMEOUT_SECONDS = 30.0


class RepoError(Exception):
    """Raised for any repo-fetch or extraction failure the caller should
    turn into a clean, user-facing error — a 404, a malformed tarball, a
    path-traversal or symlink attempt, or a tarball over the size cap.
    Never lets a raw ``httpx`` or ``tarfile`` exception (or a stack
    trace) reach the caller."""


def fetch_repo_tarball(
    owner: str,
    name: str,
    ref: str,
    token: str | None = None,
    *,
    client: httpx.Client | None = None,
) -> bytes:
    """Fetch the gzipped tarball GitHub serves for a repo at a given ref
    (branch, tag, or commit SHA) via codeload.github.com — the same
    endpoint ``git archive`` and GitHub's own "Download ZIP" ultimately
    front, no API rate limit tier required for public repos.

    ``token`` is accepted but M1 ships public-repo-only (see the plan's
    Self-Review): it is the seam a future private-repo flow hangs off of,
    passed as a Bearer token when present.

    ``client`` lets tests inject an ``httpx.MockTransport`` instead of
    reaching the real network; production callers can leave it unset.
    """
    url = f"https://codeload.github.com/{owner}/{name}/tar.gz/{ref}"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    owns_client = client is None
    http_client = client if client is not None else httpx.Client(timeout=_FETCH_TIMEOUT_SECONDS)
    try:
        try:
            response = http_client.get(url, headers=headers, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise RepoError(f"failed to fetch {owner}/{name}@{ref}: {exc}") from exc

        if response.status_code == 404:
            raise RepoError(f"repo {owner}/{name}@{ref} not found (404)")
        if response.status_code != 200:
            raise RepoError(
                f"failed to fetch {owner}/{name}@{ref}: "
                f"HTTP {response.status_code} from GitHub"
            )
        return response.content
    finally:
        if owns_client:
            http_client.close()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_member_path(member_name: str, dest_root: Path) -> Path:
    """Resolve a tar member's name against the extraction root and refuse
    anything that would land outside it — a relative ``../`` escape, an
    absolute path (which bypasses the destination entirely when joined
    with ``/``, so it's caught by the same containment check), or any
    other traversal trick a crafted name might try."""
    joined = dest_root / member_name
    resolved = joined.resolve()
    if not _is_within(resolved, dest_root):
        raise RepoError(f"refusing tar member with unsafe path: {member_name!r}")
    return resolved


def _resolve_symlink_target(member_path: Path, link_name: str, dest_root: Path) -> Path:
    """A symlink's target is resolved relative to the link's own
    directory (standard symlink semantics), or as given if absolute."""
    if os.path.isabs(link_name):
        target = Path(link_name).resolve()
    else:
        target = (member_path.parent / link_name).resolve()
    if not _is_within(target, dest_root):
        raise RepoError(
            f"refusing symlink member {member_path.name!r} whose target "
            f"{link_name!r} escapes the destination"
        )
    return target


def _resolve_hardlink_target(link_name: str, dest_root: Path) -> Path:
    """A tar hard-link's target names another member of the same
    archive, i.e. it is archive-root-relative, not relative to the
    linking member's own directory."""
    target = (dest_root / link_name).resolve()
    if not _is_within(target, dest_root):
        raise RepoError(
            f"refusing hardlink member whose target {link_name!r} escapes "
            f"the destination"
        )
    return target


def extract_safely(tar_bytes: bytes, dest: Path, max_bytes: int) -> Path:
    """Extract a (trusted-format, untrusted-content) gzipped tarball under
    ``dest``, refusing anything that could write outside it or exhaust
    disk/memory, and return the path to the single top-level directory
    GitHub wraps every repo tarball in.

    Raises ``RepoError`` for: a member whose path escapes ``dest``
    (zip-slip, relative or absolute), a symlink whose target escapes
    ``dest``, a malformed tarball, total extracted bytes over
    ``max_bytes`` (checked incrementally, mid-extraction — see module
    docstring), or a tarball that doesn't wrap its contents in exactly
    one top-level directory.

    On any refusal, anything already written under ``dest`` during this
    call is removed — a rejected tarball must not leave partial content
    behind for something else to stumble over later.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    dest_root = dest.resolve()

    total_bytes = 0
    top_level_names: set[str] = set()

    try:
        try:
            tar = tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*")
        except tarfile.TarError as exc:
            raise RepoError(f"not a valid tarball: {exc}") from exc

        with tar:
            for member in tar:
                top_level_names.add(PurePosixPath(member.name).parts[0])
                member_path = _resolve_member_path(member.name, dest_root)

                if member.issym():
                    _resolve_symlink_target(member_path, member.linkname, dest_root)
                    # A safe target is still not extracted as a real
                    # symlink on disk: nothing downstream needs a link,
                    # and not creating one removes an entire class of
                    # later-stage TOCTOU risk for free.
                    continue

                if member.islnk():
                    _resolve_hardlink_target(member.linkname, dest_root)
                    continue

                if member.isdir():
                    member_path.mkdir(parents=True, exist_ok=True)
                    continue

                if not member.isfile():
                    raise RepoError(
                        f"refusing tar member of unsupported type: {member.name!r}"
                    )

                member_path.parent.mkdir(parents=True, exist_ok=True)
                src = tar.extractfile(member)
                if src is None:
                    continue
                with open(member_path, "wb") as out:
                    while True:
                        chunk = src.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        total_bytes += len(chunk)
                        if total_bytes > max_bytes:
                            raise RepoError(
                                f"tarball exceeds max_bytes ({max_bytes}) during "
                                f"extraction — refusing the rest"
                            )
                        out.write(chunk)

        if len(top_level_names) != 1:
            raise RepoError(
                f"expected exactly one top-level directory in the tarball, "
                f"found {sorted(top_level_names)}"
            )

        return dest_root / next(iter(top_level_names))
    except Exception:
        _cleanup(dest_root)
        raise


def _cleanup(dest_root: Path) -> None:
    import shutil

    shutil.rmtree(dest_root, ignore_errors=True)
