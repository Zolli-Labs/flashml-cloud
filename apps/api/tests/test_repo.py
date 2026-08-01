"""Fetching and safely extracting a GitHub repo tarball.

This is the one module in Plan 4 that handles attacker-controlled bytes
directly: any signed-in user can point ``fetch_repo_tarball`` at any
public repo, so ``extract_safely`` must never trust a member's path or a
symlink's target, and must never materialize more bytes than the caller
asked for — a decompression bomb is small on the wire and only
detectable while unpacking it.

Every malicious tarball here is built in-process with ``tarfile``. None
of these tests touch the network — ``fetch_repo_tarball``'s HTTP tests
inject an ``httpx.MockTransport`` instead of hitting codeload.github.com.
"""
from __future__ import annotations

import io
import tarfile

import httpx
import pytest

from flashml_cloud_api.repo import RepoError, extract_safely, fetch_repo_tarball


def _make_tarball(members: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


def _file_member(name: str, content: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name=name)
    info.size = len(content)
    info.type = tarfile.REGTYPE
    return info, content


def _dir_member(name: str) -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name=name.rstrip("/") + "/")
    info.type = tarfile.DIRTYPE
    return info, None


def _symlink_member(name: str, target: str) -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name=name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    return info, None


class _ZeroFile:
    """A file-like object that yields ``total`` zero bytes without ever
    materializing them all in memory at once — lets a test declare a huge
    (highly gzip-compressible) tar member without allocating that much
    RAM to build the fixture."""

    def __init__(self, total: int):
        self._remaining = total

    def read(self, n: int = -1) -> bytes:
        if n < 0 or n > self._remaining:
            n = self._remaining
        self._remaining -= n
        return b"\x00" * n


def _make_bomb_tarball(top: str, member_name: str, declared_size: int) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=f"{top}/{member_name}")
        info.size = declared_size
        info.type = tarfile.REGTYPE
        tar.addfile(info, _ZeroFile(declared_size))
    return buf.getvalue()


def _normal_repo_tarball(top: str = "acme-widgets-abc1234") -> bytes:
    return _make_tarball(
        [
            _dir_member(top),
            _file_member(f"{top}/README.md", b"# widgets\n"),
            _file_member(f"{top}/train.py", b"print('hi')\n"),
            _file_member(f"{top}/flashml.yaml", b"version: 1\n"),
        ]
    )


# --- extract_safely: malicious inputs -------------------------------------


def test_zip_slip_relative_dotdot_is_refused(tmp_path):
    tar_bytes = _make_tarball([_file_member("../evil.txt", b"pwned")])
    with pytest.raises(RepoError, match="evil.txt|unsafe|escap"):
        extract_safely(tar_bytes, tmp_path / "dest", max_bytes=10_000)


def test_zip_slip_absolute_path_is_refused(tmp_path):
    tar_bytes = _make_tarball([_file_member("/etc/evil.txt", b"pwned")])
    with pytest.raises(RepoError, match="evil.txt|unsafe|escap"):
        extract_safely(tar_bytes, tmp_path / "dest", max_bytes=10_000)

    # And nothing must have actually landed outside the destination.
    assert not (tmp_path.parent / "etc" / "evil.txt").exists()


def test_symlink_pointing_outside_destination_is_refused(tmp_path):
    top = "acme-widgets-abc1234"
    tar_bytes = _make_tarball(
        [
            _dir_member(top),
            _symlink_member(f"{top}/escape", "../../../../etc/passwd"),
        ]
    )
    with pytest.raises(RepoError, match="escape|symlink|unsafe"):
        extract_safely(tar_bytes, tmp_path / "dest", max_bytes=10_000)


def test_symlink_with_absolute_target_outside_destination_is_refused(tmp_path):
    top = "acme-widgets-abc1234"
    tar_bytes = _make_tarball(
        [
            _dir_member(top),
            _symlink_member(f"{top}/escape", "/etc/passwd"),
        ]
    )
    with pytest.raises(RepoError):
        extract_safely(tar_bytes, tmp_path / "dest", max_bytes=10_000)


def test_decompression_bomb_is_refused(tmp_path):
    # 200 MB of highly-compressible content, tiny on the wire, well over
    # a modest max_bytes cap.
    tar_bytes = _make_bomb_tarball(
        "acme-widgets-abc1234", "bomb.bin", declared_size=200 * 1024 * 1024
    )
    assert len(tar_bytes) < 1024 * 1024  # small on the wire

    with pytest.raises(RepoError, match="max_bytes|exceeds|too large|size"):
        extract_safely(tar_bytes, tmp_path / "dest", max_bytes=10 * 1024 * 1024)


def test_decompression_bomb_does_not_leave_the_oversized_file_behind(tmp_path):
    dest = tmp_path / "dest"
    tar_bytes = _make_bomb_tarball(
        "acme-widgets-abc1234", "bomb.bin", declared_size=200 * 1024 * 1024
    )
    with pytest.raises(RepoError):
        extract_safely(tar_bytes, dest, max_bytes=10 * 1024 * 1024)

    total_on_disk = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    assert total_on_disk < 10 * 1024 * 1024 + 1024 * 1024  # no runaway write


# --- extract_safely: the happy path ---------------------------------------


def test_normal_repo_extracts_and_returns_the_single_top_level_directory(tmp_path):
    top = "acme-widgets-abc1234"
    tar_bytes = _normal_repo_tarball(top)

    result = extract_safely(tar_bytes, tmp_path / "dest", max_bytes=10_000_000)

    assert result == (tmp_path / "dest" / top).resolve()
    assert (result / "train.py").read_bytes() == b"print('hi')\n"
    assert (result / "flashml.yaml").exists()


def test_multiple_top_level_entries_is_refused(tmp_path):
    tar_bytes = _make_tarball(
        [
            _file_member("one/a.txt", b"a"),
            _file_member("two/b.txt", b"b"),
        ]
    )
    with pytest.raises(RepoError, match="top-level"):
        extract_safely(tar_bytes, tmp_path / "dest", max_bytes=10_000)


# --- fetch_repo_tarball -----------------------------------------------------


def test_fetch_repo_tarball_returns_bytes_on_success():
    tar_bytes = _normal_repo_tarball()

    def handler(request: httpx.Request) -> httpx.Response:
        assert "acme" in str(request.url)
        assert "widgets" in str(request.url)
        return httpx.Response(200, content=tar_bytes)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = fetch_repo_tarball("acme", "widgets", "main", client=client)
    assert result == tar_bytes


def test_fetch_repo_tarball_404_raises_repo_error_naming_the_repo():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"Not Found")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RepoError, match="acme/widgets") as exc_info:
        fetch_repo_tarball("acme", "widgets", "main", client=client)

    # Naming the repo, not a stack trace.
    assert "Traceback" not in str(exc_info.value)


def test_fetch_repo_tarball_server_error_raises_repo_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"boom")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(RepoError):
        fetch_repo_tarball("acme", "widgets", "main", client=client)


def test_fetch_repo_tarball_passes_token_as_bearer_auth():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=b"tarball-bytes")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetch_repo_tarball("acme", "widgets", "main", token="ghp_secret", client=client)
    assert seen["auth"] == "Bearer ghp_secret"
