"""Turning a `source:` into a pinned, checksummed manifest.

No network: every test drives a stub transport. The live contract is
pinned by `scripts/experiments/hf_dataset_origin_probe.py`, which measured
all of this against huggingface.co on 2026-08-10, and by the
`-m network` test at the bottom.

The revision pin is the point. `resolve/main` is mutable; a dataset that
changes under a running job is a class of bug we refuse to have, so
`main` is resolved to a commit SHA once, here, and every task in the run
addresses that SHA.

The other thing these tests are guarding is the *absence* of a credential.
v1 resolves public origins only and stores nothing it could authenticate
with, so a private or gated origin has to be refused by name at submit —
never by trying a token, and never by discovering the 401 on thirty
volunteers' machines forty minutes later.
"""

from __future__ import annotations

import httpx
import pytest

from flashml_cloud_api.datasets import (
    DatasetResolveError,
    Manifest,
    ManifestEntry,
    resolve,
)

TREE = [
    {"type": "file", "path": "plain_text/train-00000.parquet", "size": 300,
     "lfs": {"oid": "a" * 64}},
    {"type": "file", "path": "plain_text/train-00001.parquet", "size": 100,
     "lfs": {"oid": "b" * 64}},
    {"type": "directory", "path": "plain_text/nested"},
]
SHA = "e6281661ce1c48d982bc483cf8a173c1bbeb5d31"


def _hf_transport(tree=None, repo=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/tree/" in str(request.url):
            return httpx.Response(200, json=tree if tree is not None else TREE)
        return httpx.Response(200, json=repo if repo is not None
                              else {"sha": SHA, "private": False, "gated": False})
    return httpx.MockTransport(handler)


async def _resolve(dataset, transport):
    async with httpx.AsyncClient(transport=transport) as http:
        return await resolve(dataset, http=http)


def _hf(source="hf://stanfordnlp/imdb", **overrides):
    dataset = {"name": "imdb", "source": source, "select": None, "split": None}
    dataset.update(overrides)
    return dataset


# ---------------------------------------------------------------------------
# hf:// — the recommended origin, and the only one that hands us a real hash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hf_yields_sizes_and_sha256_per_file():
    manifest = await _resolve(
        {"name": "imdb", "source": "hf://stanfordnlp/imdb",
         "select": None, "split": None},
        _hf_transport(),
    )
    assert [e.size for e in manifest.entries] == [300, 100]
    assert manifest.entries[0].integrity == {"kind": "sha256", "value": "a" * 64}
    assert manifest.total_bytes == 400


@pytest.mark.asyncio
async def test_the_revision_is_pinned_to_a_commit_sha():
    manifest = await _resolve(
        {"name": "imdb", "source": "hf://stanfordnlp/imdb",
         "select": None, "split": None},
        _hf_transport(),
    )
    assert manifest.revision == SHA
    assert all(SHA in e.url for e in manifest.entries), "a URL still says main"


@pytest.mark.asyncio
async def test_no_entry_url_addresses_the_mutable_branch():
    """`resolve/main` moves. The whole point of pinning is that no URL in
    the compiled job can be re-pointed by a push mid-run."""
    manifest = await _resolve(_hf(), _hf_transport())
    assert not any("/resolve/main/" in e.url for e in manifest.entries)


@pytest.mark.asyncio
async def test_entries_are_sorted_by_path_for_determinism():
    """Two submissions of the same file must cut the same slices."""
    shuffled = list(reversed(TREE))
    manifest = await _resolve(
        {"name": "d", "source": "hf://a/b", "select": None, "split": None},
        _hf_transport(tree=shuffled),
    )
    assert [e.path for e in manifest.entries] == sorted(e.path for e in manifest.entries)


@pytest.mark.asyncio
async def test_select_filters_by_glob():
    manifest = await _resolve(
        {"name": "d", "source": "hf://a/b",
         "select": "plain_text/train-00000*", "split": None},
        _hf_transport(),
    )
    assert [e.path for e in manifest.entries] == ["plain_text/train-00000.parquet"]


@pytest.mark.asyncio
async def test_a_select_matching_nothing_is_an_error_not_an_empty_job():
    with pytest.raises(DatasetResolveError, match="matched no files"):
        await _resolve(
            {"name": "d", "source": "hf://a/b", "select": "*.csv", "split": None},
            _hf_transport(),
        )


@pytest.mark.asyncio
async def test_an_origin_that_lists_nothing_is_also_an_error():
    """Distinct from the `select` case, and it must not compile either: a
    task that fetches zero files trains on whatever the entrypoint falls
    back to."""
    with pytest.raises(DatasetResolveError, match="listed no files"):
        await _resolve(_hf(), _hf_transport(tree=[]))


@pytest.mark.asyncio
async def test_an_explicit_revision_is_honoured():
    manifest = await _resolve(
        {"name": "d", "source": "hf://a/b@v1.2", "select": None, "split": None},
        _hf_transport(),
    )
    assert manifest.revision == "v1.2"
    assert all("/resolve/v1.2/" in e.url for e in manifest.entries)


@pytest.mark.asyncio
async def test_a_file_with_no_lfs_block_is_not_called_sha256():
    """A small non-LFS file has only a git blob oid, which is a sha1 over a
    header plus the content — not a content hash. Calling it sha256 would
    make the host verify a digest that can never match."""
    tree = [{"type": "file", "path": "README.md", "size": 12, "oid": "f" * 40}]
    manifest = await _resolve(_hf(), _hf_transport(tree=tree))
    assert manifest.entries[0].integrity == {"kind": "none", "value": "f" * 40}


@pytest.mark.asyncio
async def test_a_gated_repo_is_refused_at_submit():
    """v1 is public-only. Discovering this on thirty machines instead of in
    the console is the failure being prevented."""
    transport = _hf_transport(repo={"sha": SHA, "private": False, "gated": "auto"})
    with pytest.raises(DatasetResolveError, match="gated"):
        await _resolve(
            {"name": "d", "source": "hf://a/b", "select": None, "split": None},
            transport,
        )


@pytest.mark.asyncio
async def test_a_private_repo_is_refused_with_the_reason():
    transport = _hf_transport(repo={"sha": SHA, "private": True, "gated": False})
    with pytest.raises(DatasetResolveError, match="private"):
        await _resolve(
            {"name": "d", "source": "hf://a/b", "select": None, "split": None},
            transport,
        )


@pytest.mark.asyncio
async def test_an_ungated_repo_reporting_gated_false_still_resolves():
    """`gated: false` is what the Hub sends for every public repo. A truthy
    test written as `if meta.get("gated")` is right; one written as
    `if "gated" in meta` refuses the entire Hub."""
    manifest = await _resolve(_hf(), _hf_transport())
    assert len(manifest.entries) == 2


@pytest.mark.asyncio
async def test_a_401_is_refused_as_not_public_rather_than_retried_with_a_token():
    def handler(request):
        return httpx.Response(401, json={"error": "Unauthorized"})

    with pytest.raises(DatasetResolveError, match="public"):
        await _resolve(_hf(), httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_resolution_never_sends_an_authorization_header():
    """The hard constraint of v1, asserted rather than assumed: there is no
    credential to send, so a request that carries one is a bug that could
    only be leaking somebody else's."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "/tree/" in str(request.url):
            return httpx.Response(200, json=TREE)
        return httpx.Response(200, json={"sha": SHA, "private": False,
                                         "gated": False})

    await _resolve(_hf(), httpx.MockTransport(handler))
    assert seen, "no request was made at all"
    for request in seen:
        assert "authorization" not in {k.lower() for k in request.headers}
        query = str(request.url.query).lower()
        assert "token" not in query and "signature" not in query


@pytest.mark.asyncio
async def test_a_404_names_the_source_rather_than_leaking_a_stack():
    def handler(request):
        return httpx.Response(404, json={"error": "Repository not found"})

    with pytest.raises(DatasetResolveError, match="hf://a/b"):
        await _resolve(_hf(source="hf://a/b"), httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_a_repo_with_no_commit_sha_is_refused_not_pinned_to_main():
    """Falling back to `main` here would produce a job that looks pinned and
    is not — the single worst outcome available."""
    transport = _hf_transport(repo={"private": False, "gated": False})
    with pytest.raises(DatasetResolveError, match="revision"):
        await _resolve(_hf(), transport)


@pytest.mark.asyncio
async def test_a_malformed_hf_id_is_refused_before_any_request():
    for bad in ("hf://", "hf://a/b/c", "hf://a/b@"):
        with pytest.raises(DatasetResolveError):
            await _resolve(
                _hf(source=bad),
                httpx.MockTransport(lambda r: httpx.Response(200, json={})),
            )


@pytest.mark.asyncio
async def test_the_hf_tree_listing_is_paginated_to_the_end():
    """The tree API caps a page at 1000 entries and continues via a `Link`
    header. Reading one page of a 4000-shard dataset silently trains the
    fleet on a quarter of it."""
    pages = [
        ([{"type": "file", "path": "p/0.bin", "size": 10, "lfs": {"oid": "0" * 64}}],
         '<https://huggingface.co/api/datasets/a/b/tree/x?cursor=NEXT>; rel="next"'),
        ([{"type": "file", "path": "p/1.bin", "size": 20, "lfs": {"oid": "1" * 64}}],
         None),
    ]
    calls = {"tree": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/tree/" not in str(request.url):
            return httpx.Response(200, json={"sha": SHA, "private": False,
                                             "gated": False})
        body, link = pages[min(calls["tree"], len(pages) - 1)]
        calls["tree"] += 1
        headers = {"Link": link} if link else {}
        return httpx.Response(200, json=body, headers=headers)

    manifest = await _resolve(_hf(), httpx.MockTransport(handler))
    assert [e.path for e in manifest.entries] == ["p/0.bin", "p/1.bin"]
    assert calls["tree"] == 2


@pytest.mark.asyncio
async def test_an_unreachable_origin_is_a_clean_error():
    def boom(request):
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(DatasetResolveError):
        await _resolve(
            {"name": "d", "source": "hf://a/b", "select": None, "split": None},
            httpx.MockTransport(boom),
        )


@pytest.mark.asyncio
async def test_a_timeout_is_a_clean_error_too():
    """Every httpx exception, not just the connect one — the route this
    feeds must never answer 500 because an origin was slow."""
    def boom(request):
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(DatasetResolveError):
        await _resolve(_hf(), httpx.MockTransport(boom))


@pytest.mark.asyncio
async def test_a_listing_that_is_not_json_is_a_clean_error():
    def handler(request):
        return httpx.Response(200, text="<html>maintenance</html>")

    with pytest.raises(DatasetResolveError):
        await _resolve(_hf(), httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_a_listing_path_that_escapes_the_dataset_directory_is_refused():
    """Manifest paths become files under /work/data/<name>/ on a
    volunteer's machine. The origin is a stranger; the path is its input."""
    tree = [{"type": "file", "path": "../../etc/passwd", "size": 1,
             "lfs": {"oid": "a" * 64}}]
    with pytest.raises(DatasetResolveError, match="path"):
        await _resolve(_hf(), _hf_transport(tree=tree))


@pytest.mark.asyncio
async def test_a_listing_with_no_size_is_refused():
    """The slice mapper weighs by bytes. A missing size is not a zero."""
    tree = [{"type": "file", "path": "a.bin", "lfs": {"oid": "a" * 64}}]
    with pytest.raises(DatasetResolveError, match="size"):
        await _resolve(_hf(), _hf_transport(tree=tree))


# ---------------------------------------------------------------------------
# s3:// and r2:// — ETags, which are NOT content hashes
# ---------------------------------------------------------------------------

S3_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
    "<Name>open-data</Name><Prefix>corpus/</Prefix><KeyCount>2</KeyCount>"
    "<MaxKeys>1000</MaxKeys><IsTruncated>false</IsTruncated>"
    "<Contents><Key>corpus/b.parquet</Key>"
    "<LastModified>2026-08-01T00:00:00.000Z</LastModified>"
    "<ETag>&quot;d41d8cd98f00b204e9800998ecf8427e&quot;</ETag>"
    "<Size>100</Size><StorageClass>STANDARD</StorageClass></Contents>"
    "<Contents><Key>corpus/a.parquet</Key>"
    "<LastModified>2026-08-01T00:00:00.000Z</LastModified>"
    "<ETag>&quot;9b2cf535f27731c974343645a3985328-14&quot;</ETag>"
    "<Size>300</Size><StorageClass>STANDARD</StorageClass></Contents>"
    "</ListBucketResult>"
)


def _xml_transport(body=S3_XML, record=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if record is not None:
            record.append(request)
        return httpx.Response(200, content=body.encode(),
                              headers={"content-type": "application/xml"})
    return httpx.MockTransport(handler)


def _s3(source="s3://open-data/corpus/", **overrides):
    dataset = {"name": "corpus", "source": source, "select": None, "split": None}
    dataset.update(overrides)
    return dataset


@pytest.mark.asyncio
async def test_s3_lists_keys_with_sizes_and_etags():
    manifest = await _resolve(_s3(), _xml_transport())
    assert [e.path for e in manifest.entries] == ["corpus/a.parquet",
                                                  "corpus/b.parquet"]
    assert [e.size for e in manifest.entries] == [300, 100]
    assert manifest.total_bytes == 400


@pytest.mark.asyncio
async def test_a_multipart_etag_keeps_its_part_suffix_and_is_never_called_sha256():
    """`"<md5-of-md5s>-<partcount>"` is a change-detection token, not a
    content hash. Relabelling it sha256 — or trimming the `-14` to make it
    look like one — promises the host a verification that cannot succeed and
    the reader a guarantee we never had."""
    manifest = await _resolve(_s3(), _xml_transport())
    multipart = next(e for e in manifest.entries if e.path == "corpus/a.parquet")
    assert multipart.integrity == {
        "kind": "etag", "value": "9b2cf535f27731c974343645a3985328-14"
    }
    single = next(e for e in manifest.entries if e.path == "corpus/b.parquet")
    assert single.integrity == {
        "kind": "etag", "value": "d41d8cd98f00b204e9800998ecf8427e"
    }
    assert all(e.integrity["kind"] == "etag" for e in manifest.entries)


@pytest.mark.asyncio
async def test_s3_uses_list_objects_v2_anonymously_against_the_bucket_host():
    seen: list[httpx.Request] = []
    await _resolve(_s3(), _xml_transport(record=seen))
    (request,) = seen
    assert request.url.host == "open-data.s3.amazonaws.com"
    assert request.url.params["list-type"] == "2"
    assert request.url.params["prefix"] == "corpus/"
    assert "authorization" not in {k.lower() for k in request.headers}


@pytest.mark.asyncio
async def test_s3_entry_urls_address_the_object_directly():
    manifest = await _resolve(_s3(), _xml_transport())
    assert manifest.entries[0].url == (
        "https://open-data.s3.amazonaws.com/corpus/a.parquet"
    )


@pytest.mark.asyncio
async def test_the_object_store_revision_is_a_digest_of_the_etag_set():
    """There is no commit SHA to pin, so the snapshot IS the etag set. Two
    resolutions of an unchanged bucket agree; one object replaced and they
    do not."""
    first = await _resolve(_s3(), _xml_transport())
    again = await _resolve(_s3(), _xml_transport())
    assert first.revision == again.revision
    moved = S3_XML.replace("d41d8cd98f00b204e9800998ecf8427e", "0" * 32)
    changed = await _resolve(_s3(), _xml_transport(body=moved))
    assert changed.revision != first.revision


@pytest.mark.asyncio
async def test_r2_addresses_the_account_scoped_bucket_endpoint():
    seen: list[httpx.Request] = []
    manifest = await _resolve(
        {"name": "d", "source": "r2://acct123/open-data/corpus/",
         "select": None, "split": None},
        _xml_transport(record=seen),
    )
    (request,) = seen
    assert request.url.host == "acct123.r2.cloudflarestorage.com"
    assert request.url.path == "/open-data"
    assert manifest.entries[0].url == (
        "https://acct123.r2.cloudflarestorage.com/open-data/corpus/a.parquet"
    )
    assert all(e.integrity["kind"] == "etag" for e in manifest.entries)


@pytest.mark.asyncio
async def test_a_truncated_listing_is_followed_to_the_last_page():
    """ListObjectsV2 caps at 1000 keys. Stopping at page one hands the fleet
    a silent prefix of the dataset."""
    page_one = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        "<IsTruncated>true</IsTruncated>"
        "<NextContinuationToken>PAGE2</NextContinuationToken>"
        "<Contents><Key>corpus/a.parquet</Key><ETag>&quot;aa&quot;</ETag>"
        "<Size>1</Size></Contents></ListBucketResult>"
    )
    page_two = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        "<IsTruncated>false</IsTruncated>"
        "<Contents><Key>corpus/z.parquet</Key><ETag>&quot;zz&quot;</ETag>"
        "<Size>2</Size></Contents></ListBucketResult>"
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = page_two if request.url.params.get("continuation-token") else page_one
        return httpx.Response(200, content=body.encode())

    manifest = await _resolve(_s3(), httpx.MockTransport(handler))
    assert [e.path for e in manifest.entries] == ["corpus/a.parquet",
                                                  "corpus/z.parquet"]
    assert seen[1].url.params["continuation-token"] == "PAGE2"


@pytest.mark.asyncio
async def test_directory_marker_keys_are_not_files():
    """The S3 console writes a zero-byte object ending in `/` for a folder.
    Fetching one creates a file where a directory has to go."""
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        "<IsTruncated>false</IsTruncated>"
        "<Contents><Key>corpus/</Key><ETag>&quot;d4&quot;</ETag>"
        "<Size>0</Size></Contents>"
        "<Contents><Key>corpus/a.parquet</Key><ETag>&quot;aa&quot;</ETag>"
        "<Size>5</Size></Contents></ListBucketResult>"
    )
    manifest = await _resolve(_s3(), _xml_transport(body=body))
    assert [e.path for e in manifest.entries] == ["corpus/a.parquet"]


@pytest.mark.asyncio
async def test_a_bucket_that_needs_a_signature_is_refused_as_not_public():
    def handler(request):
        return httpx.Response(
            403,
            content=b"<Error><Code>AccessDenied</Code></Error>",
        )

    with pytest.raises(DatasetResolveError, match="public"):
        await _resolve(_s3(), httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_a_listing_that_is_not_xml_is_a_clean_error():
    with pytest.raises(DatasetResolveError):
        await _resolve(_s3(), _xml_transport(body="not xml at all <<<"))


@pytest.mark.asyncio
async def test_a_malformed_object_store_source_is_refused():
    for bad in ("s3://", "r2://acct", "r2://"):
        with pytest.raises(DatasetResolveError):
            await _resolve(_s3(source=bad), _xml_transport())


# ---------------------------------------------------------------------------
# https:// — whatever the author declared, labelled as such
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_https_manifest_carries_declared_integrity():
    body = {"entries": [
        {"path": "a.bin", "url": "https://o.invalid/a.bin", "size": 5,
         "sha256": "c" * 64},
    ]}

    def handler(request):
        return httpx.Response(200, json=body)

    manifest = await _resolve(
        {"name": "d", "source": "https://o.invalid/manifest.json",
         "select": None, "split": None},
        httpx.MockTransport(handler),
    )
    assert manifest.entries[0].integrity == {
        "kind": "declared-sha256", "value": "c" * 64
    }


@pytest.mark.asyncio
async def test_an_https_entry_with_no_declared_hash_is_kind_none():
    body = {"entries": [
        {"path": "a.bin", "url": "https://o.invalid/a.bin", "size": 5},
    ]}
    manifest = await _resolve(
        {"name": "d", "source": "https://o.invalid/m.json",
         "select": None, "split": None},
        httpx.MockTransport(lambda r: httpx.Response(200, json=body)),
    )
    assert manifest.entries[0].integrity["kind"] == "none"


@pytest.mark.asyncio
async def test_the_https_revision_is_a_digest_of_the_manifest_document():
    body = {"entries": [
        {"path": "a.bin", "url": "https://o.invalid/a.bin", "size": 5},
    ]}
    manifest = await _resolve(
        {"name": "d", "source": "https://o.invalid/m.json",
         "select": None, "split": None},
        httpx.MockTransport(lambda r: httpx.Response(200, json=body)),
    )
    assert manifest.revision.startswith("sha256:")
    assert len(manifest.revision) == len("sha256:") + 64


@pytest.mark.asyncio
async def test_an_http_url_inside_an_https_manifest_is_refused():
    """The host fetches these over the open internet with no verification
    beyond whatever the author declared. Plaintext is not acceptable."""
    body = {"entries": [
        {"path": "a.bin", "url": "http://o.invalid/a.bin", "size": 5},
    ]}
    with pytest.raises(DatasetResolveError, match="https"):
        await _resolve(
            {"name": "d", "source": "https://o.invalid/m.json",
             "select": None, "split": None},
            httpx.MockTransport(lambda r: httpx.Response(200, json=body)),
        )


@pytest.mark.asyncio
async def test_a_document_that_is_not_a_manifest_says_so():
    with pytest.raises(DatasetResolveError, match="entries"):
        await _resolve(
            {"name": "d", "source": "https://o.invalid/m.json",
             "select": None, "split": None},
            httpx.MockTransport(lambda r: httpx.Response(200, json={"files": []})),
        )


@pytest.mark.asyncio
async def test_two_manifest_entries_may_not_share_a_path():
    """They land on the same file under /work/data/<name>/, so one silently
    replaces the other and which one wins depends on fetch order."""
    body = {"entries": [
        {"path": "a.bin", "url": "https://o.invalid/1", "size": 5},
        {"path": "a.bin", "url": "https://o.invalid/2", "size": 6},
    ]}
    with pytest.raises(DatasetResolveError, match="twice"):
        await _resolve(
            {"name": "d", "source": "https://o.invalid/m.json",
             "select": None, "split": None},
            httpx.MockTransport(lambda r: httpx.Response(200, json=body)),
        )


# ---------------------------------------------------------------------------
# shared shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unsupported_scheme_is_refused_without_a_request():
    def handler(request):  # pragma: no cover - must never run
        raise AssertionError("a request was made for an unsupported scheme")

    with pytest.raises(DatasetResolveError, match="ftp://"):
        await _resolve(
            {"name": "d", "source": "ftp://a/b", "select": None, "split": None},
            httpx.MockTransport(handler),
        )


@pytest.mark.asyncio
async def test_the_manifest_records_what_it_resolved():
    manifest = await _resolve(_hf(), _hf_transport())
    assert isinstance(manifest, Manifest)
    assert manifest.name == "imdb"
    assert manifest.source == "hf://stanfordnlp/imdb"
    assert all(isinstance(e, ManifestEntry) for e in manifest.entries)


def test_the_manifest_is_frozen_so_a_later_stage_cannot_edit_the_pin():
    entry = ManifestEntry(path="a", url="https://x/a", size=1,
                          integrity={"kind": "none", "value": ""})
    manifest = Manifest(name="d", source="hf://a/b", revision="c0ffee",
                        entries=(entry,))
    with pytest.raises(Exception):
        manifest.revision = "main"  # type: ignore[misc]


def test_total_bytes_of_an_empty_manifest_is_zero():
    assert Manifest(name="d", source="hf://a/b", revision="c0ffee",
                    entries=()).total_bytes == 0


# ---------------------------------------------------------------------------
# the live contract
# ---------------------------------------------------------------------------


@pytest.mark.network
@pytest.mark.asyncio
async def test_a_real_public_hf_dataset_resolves_and_pins():
    """Everything above is a stub agreeing with a stub. This is the only
    assertion that the Hub still behaves the way
    `scripts/experiments/hf_dataset_origin_probe.py` measured on
    2026-08-10. Excluded by default; CI runs it with `-m network`."""
    async with httpx.AsyncClient() as http:
        manifest = await resolve(
            {"name": "imdb", "source": "hf://stanfordnlp/imdb",
             "select": "plain_text/*.parquet", "split": None},
            http=http,
        )
    assert manifest.entries, "the Hub listed no parquet shards for imdb"
    assert len(manifest.revision) == 40, manifest.revision
    assert all(e.integrity["kind"] == "sha256" for e in manifest.entries)
    assert all(manifest.revision in e.url for e in manifest.entries)
    assert manifest.total_bytes > 0
