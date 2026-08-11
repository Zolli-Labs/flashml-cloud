"""Turning a declared ``source:`` into a pinned, checksummed manifest.

This is the only network call the control plane makes on the dataset path.
It happens once, at submit time, against a public endpoint, with **no
credential** — and it produces the list of URLs a volunteer's machine will
fetch directly from the origin. No dataset byte ever passes through us,
which is what keeps a job's marginal cost at zero.

Spec: ``docs/superpowers/specs/2026-08-10-declared-datasets-design.md``
§3–§5, plus the §14 amendments. The live behaviour of the Hugging Face Hub
was measured, not assumed, by
``scripts/experiments/hf_dataset_origin_probe.py`` on 2026-08-10; the
``-m network`` test in ``tests/test_datasets_resolve.py`` is what keeps
that measurement honest as the Hub changes.

**Three things this module refuses to do.**

*It never authenticates.* v1 addresses four schemes and holds zero
credentials — the scheme says where the bytes are, the credential says who
may read them, and they are orthogonal (spec decision 2). A private or
gated origin is therefore refused **at submit, by name**, never by
attempting a token we do not have and never by letting thirty machines
discover the 401 forty minutes into a round.

*It never pins a moving target.* ``hf://ns/name`` resolves ``main`` to a
commit SHA exactly once, here, and every entry URL addresses that SHA.
``resolve/main`` is mutable; a dataset changing under a running job is the
class of bug this whole path exists to prevent. Object stores have no
commit, so their snapshot is a digest over the ETag set they listed; an
``https://`` manifest's snapshot is a digest of the document itself.

*It never overstates integrity.* ``kind`` is part of the value because the
three origins genuinely differ (spec §4), and a reader deserves to know
which guarantee they got rather than infer one we never provided:

===================  ====================================================
``sha256``           ``hf://`` LFS oid — a real content hash. Verify after
                     fetch and fail the task on a mismatch.
``etag``             ``s3://``/``r2://``. MD5 for a single-part upload but
                     ``<md5-of-md5s>-<partcount>`` for a multipart one, so
                     it is a change-detection token and **not** a content
                     hash. Never relabelled, never trimmed to look like one.
``declared-sha256``  ``https://`` manifest — exactly as trustworthy as the
                     author, who is the submitter. Fine, since the data is
                     theirs.
``none``             A small non-LFS file on the Hub, or an ``https://``
                     entry that declared nothing. The value may still carry
                     the origin's own token (a git blob oid) for provenance,
                     but nothing may verify against it.
===================  ====================================================

Every ``httpx`` failure leaves here as ``DatasetResolveError``. The caller
turns that into a 400 naming the origin; a bad dataset URL is a submitter
mistake and must never surface as a 500.
"""
from __future__ import annotations

import fnmatch
import hashlib
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import httpx

HF_API = "https://huggingface.co/api"
HF_HUB = "https://huggingface.co"

#: Per-request ceiling. The submit route waits on this synchronously, so a
#: slow origin must cost the submitter a clear refusal rather than a hung
#: console. Matches ``repo.py``'s fetch timeout.
RESOLVE_TIMEOUT_SECONDS = 30.0

#: We identify ourselves. Anonymous does not mean anonymous-and-rude, and
#: an origin operator seeing this in their logs can find out what it is.
USER_AGENT = "flashml-cloud/1 (dataset resolve; +https://github.com/Zolli-Labs)"

#: Entries a single dataset may contribute to a job spec.
#:
#: Not a fetch limit — a ceiling on what the *compiler* will carry. Every
#: entry is copied into ``parameters["dataset_slices"]``, which is embedded
#: in the job spec, stored, and forwarded to every task. A million-file
#: manifest is a multi-hundred-megabyte job spec, so the honest answer is a
#: refusal naming the number rather than a timeout somewhere downstream.
MAX_MANIFEST_ENTRIES = 50_000

#: Listing pages to follow before giving up. Bounds a paginating origin
#: (or a buggy one that returns the same continuation token forever)
#: without capping any plausible real dataset: 1000 keys a page.
MAX_LIST_PAGES = 200

_OBJECT_STORE_SCHEMES = ("s3://", "r2://")

#: Path components a manifest may not contain. ``..`` escapes the dataset
#: directory, ``.`` and the empty string are noise that resolves to a
#: different file than the one the listing named.
_UNSAFE_PATH_PARTS = frozenset({"", ".", ".."})


class DatasetResolveError(Exception):
    """A declared ``source:`` could not be turned into a usable manifest.

    Carries a message written for the person who typed the ``source:`` —
    the origin is named, the reason is named, and no ``httpx`` internals
    or stack frames leak through. The submit route renders it as a 400.
    """


@dataclass(frozen=True)
class ManifestEntry:
    """One file the host will fetch, at a URL that cannot be re-pointed.

    ``path`` is where it lands under ``/work/data/<name>/`` and is always a
    plain relative path. ``integrity`` is ``{"kind": ..., "value": ...}``
    — see the module docstring for why the kind travels with the value.
    """

    path: str
    url: str
    size: int
    integrity: dict


@dataclass(frozen=True)
class Manifest:
    """A dataset, pinned: everything the compiler needs and nothing else.

    Frozen because ``revision`` is the guarantee. Once a submit has decided
    which snapshot the job trains on, no later stage gets to move it.

    ``revision`` is scheme-shaped and self-describing:

    * ``hf://`` — a bare 40-character commit SHA.
    * ``s3://``/``r2://`` — ``etagset:<sha256>`` over the listed
      ``key``/``ETag`` pairs. Computed over the whole *listing*, before
      ``select``, so it identifies the state of the origin rather than the
      slice one job asked for.
    * ``https://`` — ``sha256:<digest>`` of the manifest document as
      served, for the same reason.
    """

    name: str
    source: str
    revision: str
    entries: tuple[ManifestEntry, ...]

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries)


async def resolve(dataset: dict, *, http: httpx.AsyncClient) -> Manifest:
    """Resolve one entry of ``flashml.yaml``'s ``datasets:`` block.

    ``dataset`` is what ``flashml_yaml._validate_datasets`` produced:
    ``{"name", "source", "select", "split"}``. ``split`` is not consulted
    here — it decides how the manifest is *cut*, which is the compiler's
    job, not the resolver's.

    ``http`` is supplied by the caller so the submit route can reuse its
    client and the tests can drive an ``httpx.MockTransport`` instead of
    the network.
    """
    source = dataset.get("source")
    if not isinstance(source, str) or not source:
        raise DatasetResolveError(
            f"dataset {dataset.get('name')!r} has no 'source' to resolve"
        )
    if source.startswith("hf://"):
        return await _resolve_hf(dataset, http=http)
    if source.startswith(_OBJECT_STORE_SCHEMES):
        return await _resolve_object_store(dataset, http=http)
    if source.startswith("https://"):
        return await _resolve_https_manifest(dataset, http=http)
    raise DatasetResolveError(
        f"dataset {_name(dataset)!r} has an unsupported source {source!r}; "
        f"supported schemes are ['hf://', 's3://', 'r2://', 'https://']"
    )


# ---------------------------------------------------------------------------
# hf://
# ---------------------------------------------------------------------------


async def _resolve_hf(dataset: dict, *, http: httpx.AsyncClient) -> Manifest:
    """``hf://<namespace>/<name>[@revision]``.

    Two calls. The repo endpoint answers three questions at once — is it
    private, is it gated, and what commit is ``main`` right now — and the
    tree endpoint turns that commit into a file listing with sizes and LFS
    oids. Both are anonymous; both are what the origin probe measured.

    An explicit ``@revision`` is honoured verbatim, including when it names
    a branch or tag rather than a SHA. That is the submitter saying "this
    one", and second-guessing it would make ``@`` mean something other than
    what the Hub means by it. The repo call still happens, because gating
    and privacy are properties of the repo, not of the revision.
    """
    source = dataset["source"]
    repo, explicit_revision = _split_hf_id(source)

    meta_response = await _get(
        http, f"{HF_API}/datasets/{urllib.parse.quote(repo, safe='/')}",
        source=source, what="the Hugging Face dataset API",
    )
    _refuse_status(meta_response, source=source,
                   what="the Hugging Face dataset API")
    meta = _as_json(meta_response, source=source,
                    what="the Hugging Face dataset API")
    if not isinstance(meta, dict):
        raise DatasetResolveError(
            f"{source}: the Hugging Face dataset API returned {type(meta).__name__}, "
            f"not a repository description"
        )

    # Public-only, refused by name. `gated` is `false` on every public repo
    # and `"auto"`/`"manual"` on a gated one, so the test is truthiness —
    # not key presence, which would refuse the entire Hub.
    if meta.get("private") is True:
        raise DatasetResolveError(
            f"{source} is a private Hugging Face repository. FlashML resolves "
            f"public origins only and stores no credential of any kind, so "
            f"there is nothing it could authenticate with. Make the dataset "
            f"public, or keep the data on a machine you host yourself and use "
            f"local_inputs."
        )
    gated = meta.get("gated")
    if gated:
        raise DatasetResolveError(
            f"{source} is a gated Hugging Face repository (gated={gated!r}). "
            f"Gated repositories refuse anonymous reads, and FlashML stores no "
            f"credential of any kind, so every machine in your Crew would be "
            f"turned away. Use an ungated dataset, or keep the data on a "
            f"machine you host yourself and use local_inputs."
        )

    revision = explicit_revision or meta.get("sha")
    if not isinstance(revision, str) or not revision:
        raise DatasetResolveError(
            f"{source}: the Hugging Face API returned no commit SHA, so there "
            f"is no immutable revision to pin. Refusing rather than addressing "
            f"'main', which moves the moment anyone pushes to the dataset."
        )

    quoted_revision = urllib.parse.quote(revision, safe="/")
    listing = await _hf_tree(
        http,
        f"{HF_API}/datasets/{urllib.parse.quote(repo, safe='/')}"
        f"/tree/{quoted_revision}?recursive=1",
        source=source,
    )

    rows: list[tuple[str, str, int, dict]] = []
    for item in listing:
        if not isinstance(item, dict) or item.get("type") != "file":
            continue
        path = _checked_path(item.get("path"), source=source)
        lfs = item.get("lfs") if isinstance(item.get("lfs"), dict) else {}
        raw_size = item.get("size")
        if raw_size is None:
            raw_size = lfs.get("size")
        size = _checked_size(raw_size, source=source, path=path)
        if lfs.get("oid"):
            integrity = {"kind": "sha256", "value": str(lfs["oid"])}
        else:
            # No LFS pointer means a small file stored in git itself. Its
            # `oid` is a git blob sha1 — sha1 over a header plus the
            # content, so it is not a hash of the bytes the host will
            # download. Recorded for provenance, labelled unverifiable.
            integrity = {"kind": "none", "value": str(item.get("oid") or "")}
        url = (
            f"{HF_HUB}/datasets/{urllib.parse.quote(repo, safe='/')}"
            f"/resolve/{quoted_revision}/{urllib.parse.quote(path, safe='/')}"
        )
        rows.append((path, url, size, integrity))

    return _build(dataset, revision=revision, rows=rows)


def _split_hf_id(source: str) -> tuple[str, str | None]:
    """``hf://stanfordnlp/imdb@v1.2`` → ``("stanfordnlp/imdb", "v1.2")``."""
    rest = source[len("hf://"):]
    repo, at, revision = rest.partition("@")
    repo = repo.strip("/")
    if not repo or repo.count("/") > 1 or any(not p for p in repo.split("/")):
        raise DatasetResolveError(
            f"{source!r} is not a Hugging Face dataset id — expected "
            f"hf://<namespace>/<name> with an optional @<revision>"
        )
    if at and not revision:
        raise DatasetResolveError(
            f"{source!r} ends in '@' with no revision after it. Drop the '@' "
            f"to pin the current commit of the default branch."
        )
    return repo, (revision or None)


async def _hf_tree(
    http: httpx.AsyncClient, url: str, *, source: str
) -> list[Any]:
    """Every page of the tree listing, concatenated.

    The tree endpoint caps a page at 1000 entries and continues through a
    ``Link: <...>; rel="next"`` header. Reading only the first page of a
    4000-shard dataset does not fail — it silently trains the whole fleet
    on a quarter of the data, which is worse.
    """
    items: list[Any] = []
    seen: set[str] = set()
    for _ in range(MAX_LIST_PAGES):
        if url in seen:
            break
        seen.add(url)
        response = await _get(http, url, source=source,
                              what="the Hugging Face tree API")
        _refuse_status(response, source=source, what="the Hugging Face tree API")
        page = _as_json(response, source=source, what="the Hugging Face tree API")
        if not isinstance(page, list):
            raise DatasetResolveError(
                f"{source}: the Hugging Face tree API returned "
                f"{type(page).__name__}, not a file listing"
            )
        items.extend(page)
        next_link = response.links.get("next", {}).get("url")
        if not next_link:
            return items
        url = str(httpx.URL(response.url).join(next_link))
    raise DatasetResolveError(
        f"{source}: the file listing did not end after {MAX_LIST_PAGES} pages. "
        f"Narrow it with 'select:', or use a smaller dataset."
    )


# ---------------------------------------------------------------------------
# s3:// and r2://
# ---------------------------------------------------------------------------


async def _resolve_object_store(
    dataset: dict, *, http: httpx.AsyncClient
) -> Manifest:
    """``s3://bucket/prefix`` and ``r2://account/bucket/prefix``.

    One mechanism, two address shapes: an anonymous ``ListObjectsV2`` over
    the bucket's S3-compatible endpoint, parsed as XML. Both are the public
    -bucket pattern (AWS Open Data; an R2 bucket with anonymous access
    enabled). A bucket that requires a signature answers 403, which is
    refused as "not public" rather than retried with a credential we do not
    have.

    ``path`` is the full object key, not the key with ``prefix`` removed.
    That matches ``hf://``, where a file keeps its repo-relative directory,
    and it means ``select:`` globs against exactly the string the origin
    printed.
    """
    source = dataset["source"]
    list_base, object_base, prefix = _object_store_endpoint(source)

    rows: list[tuple[str, str, int, dict]] = []
    etag_pairs: list[tuple[str, str]] = []
    token: str | None = None
    for _ in range(MAX_LIST_PAGES):
        params: dict[str, str] = {"list-type": "2", "max-keys": "1000"}
        if prefix:
            params["prefix"] = prefix
        if token:
            params["continuation-token"] = token
        url = f"{list_base}?{urllib.parse.urlencode(params)}"
        response = await _get(http, url, source=source, what="the bucket")
        _refuse_status(response, source=source, what="the bucket")
        root = _as_xml(response, source=source)

        for contents in _children(root, "Contents"):
            key = _child_text(contents, "Key")
            size_text = _child_text(contents, "Size")
            etag = _unquote_etag(_child_text(contents, "ETag"))
            if key.endswith("/") and size_text in ("", "0"):
                # A console-created "folder": a zero-byte object whose key
                # ends in a separator. Fetching it would put a file where a
                # directory has to go.
                continue
            path = _checked_path(key, source=source)
            size = _checked_size(_parse_int(size_text), source=source, path=path)
            etag_pairs.append((path, etag))
            rows.append((
                path,
                object_base + urllib.parse.quote(path, safe="/"),
                size,
                # NEVER "sha256". A multipart ETag is <md5-of-md5s>-<parts>,
                # and the "-14" suffix stays: trimming it to 32 hex
                # characters produces something that looks like an md5 of
                # the object and is not.
                {"kind": "etag", "value": etag},
            ))

        if _child_text(root, "IsTruncated").lower() != "true":
            break
        token = _child_text(root, "NextContinuationToken")
        if not token:
            raise DatasetResolveError(
                f"{source}: the bucket reported a truncated listing but sent "
                f"no continuation token, so the file list cannot be completed"
            )
    else:
        raise DatasetResolveError(
            f"{source}: the bucket listing did not end after {MAX_LIST_PAGES} "
            f"pages. Narrow the prefix, or use a smaller dataset."
        )

    digest = hashlib.sha256()
    for path, etag in sorted(etag_pairs):
        digest.update(f"{path}\t{etag}\n".encode())
    return _build(dataset, revision=f"etagset:{digest.hexdigest()}", rows=rows)


def _object_store_endpoint(source: str) -> tuple[str, str, str]:
    """``(listing URL base, object URL base, key prefix)``.

    S3 is addressed virtual-host style, R2 path-style under the account's
    endpoint — the two forms each provider documents.
    """
    if source.startswith("s3://"):
        bucket, _, prefix = source[len("s3://"):].partition("/")
        if not bucket:
            raise DatasetResolveError(
                f"{source!r} names no bucket — expected s3://<bucket>/<prefix>"
            )
        root = f"https://{urllib.parse.quote(bucket, safe='')}.s3.amazonaws.com"
        return f"{root}/", f"{root}/", prefix

    account, _, tail = source[len("r2://"):].partition("/")
    bucket, _, prefix = tail.partition("/")
    if not account or not bucket:
        raise DatasetResolveError(
            f"{source!r} is not an R2 location — expected "
            f"r2://<account-id>/<bucket>/<prefix>"
        )
    root = (
        f"https://{urllib.parse.quote(account, safe='')}"
        f".r2.cloudflarestorage.com/{urllib.parse.quote(bucket, safe='')}"
    )
    return root, f"{root}/", prefix


def _local_name(tag: str) -> str:
    """``{http://s3.amazonaws.com/doc/2006-03-01/}Key`` → ``Key``.

    S3 and R2 both namespace the response, and matching on the qualified
    name would make this break the day either changes the namespace URI for
    a reason that has nothing to do with us.
    """
    return tag.rsplit("}", 1)[-1]


def _children(node: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in node if _local_name(child.tag) == name]


def _child_text(node: ET.Element, name: str) -> str:
    for child in node:
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _unquote_etag(raw: str) -> str:
    """Strip the surrounding quotes S3 wraps an ETag in — and nothing else.

    Specifically not ``.strip('"')``, which would also eat a quote that is
    part of the value, and specifically not touching the ``-<partcount>``
    suffix, which is what tells a reader the token is a multipart digest
    rather than an md5.
    """
    value = raw.strip()
    if value.startswith('W/'):
        value = value[2:]
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def _parse_int(text: str) -> int | None:
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# https://
# ---------------------------------------------------------------------------


async def _resolve_https_manifest(
    dataset: dict, *, http: httpx.AsyncClient
) -> Manifest:
    """``https://…/manifest.json`` — the author's own file listing.

    ``{"entries": [{"path", "url", "size", "sha256"}]}`` (a bare list is
    accepted too, since it is the obvious shape to write). The declared
    hash is labelled ``declared-sha256``: it is exactly as trustworthy as
    whoever wrote the manifest, which is fine, because that is the person
    whose job it is.
    """
    source = dataset["source"]
    response = await _get(http, source, source=source, what="the manifest URL")
    _refuse_status(response, source=source, what="the manifest URL")
    document = _as_json(response, source=source, what="the manifest")
    revision = "sha256:" + hashlib.sha256(response.content).hexdigest()

    if isinstance(document, dict):
        raw_entries = document.get("entries")
    elif isinstance(document, list):
        raw_entries = document
    else:
        raw_entries = None
    if not isinstance(raw_entries, list):
        raise DatasetResolveError(
            f"{source} is not a dataset manifest: expected a JSON object with "
            f'an "entries" list of {{"path", "url", "size", "sha256"}} objects '
            f"(or that list on its own)"
        )

    rows: list[tuple[str, str, int, dict]] = []
    for item in raw_entries:
        if not isinstance(item, dict):
            raise DatasetResolveError(
                f"{source}: manifest entry {item!r} is not an object"
            )
        path = _checked_path(item.get("path"), source=source)
        size = _checked_size(item.get("size"), source=source, path=path)
        url = item.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise DatasetResolveError(
                f"{source}: manifest entry {path!r} has url {url!r}. Every URL "
                f"in a manifest must be https:// — a volunteer fetches it over "
                f"the open internet, and the only thing standing between that "
                f"fetch and a tampered file is the transport."
            )
        declared = item.get("sha256")
        if not isinstance(declared, str) or not declared:
            # Refused here, not left to the host. `fetch_shard` rejects an
            # empty integrity value, so minting {"kind": "none", "value": ""}
            # produced a job that compiled cleanly, admitted cleanly, and then
            # failed identically on EVERY machine it was placed on — the
            # slowest possible way to learn that a manifest is incomplete.
            #
            # Unlike a listing API, this manifest is the submitter's own
            # document: they can put a hash in it. And they must, because it
            # is the only integrity signal an arbitrary HTTPS origin gives us
            # at all — there is no ETag contract and no revision to pin.
            raise DatasetResolveError(
                f"{source}: manifest entry {path!r} has no 'sha256'. Every "
                f"entry in an https:// manifest needs one — it is the only "
                f"way a host can tell your file from a corrupted or swapped "
                f"one, and a host will refuse the shard without it."
            )
        integrity = {"kind": "declared-sha256", "value": declared}
        rows.append((path, url, size, integrity))

    return _build(dataset, revision=revision, rows=rows)


# ---------------------------------------------------------------------------
# shared
# ---------------------------------------------------------------------------


def _name(dataset: dict) -> str:
    name = dataset.get("name")
    return name if isinstance(name, str) and name else "<unnamed>"


async def _get(
    http: httpx.AsyncClient, url: str, *, source: str, what: str
) -> httpx.Response:
    """One anonymous GET, with every transport failure renamed.

    No ``Authorization`` header is ever set here, and there is no code path
    that could set one: that is the v1 guarantee, and
    ``test_resolution_never_sends_an_authorization_header`` is what keeps
    it true.
    """
    try:
        return await http.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=RESOLVE_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
    except (httpx.HTTPError, httpx.InvalidURL, httpx.CookieConflict) as exc:
        raise DatasetResolveError(
            f"could not reach {what} for {source}: {type(exc).__name__}: {exc}"
        ) from exc


def _refuse_status(response: httpx.Response, *, source: str, what: str) -> None:
    status = response.status_code
    if status == 200:
        return
    if status in (401, 403):
        raise DatasetResolveError(
            f"{source} is not publicly readable: {what} answered HTTP {status} "
            f"to an anonymous request. FlashML v1 supports public origins only "
            f"and stores no credential of any kind, so it cannot sign in on "
            f"your behalf. Make the data public, or keep it on a machine you "
            f"host yourself and use local_inputs."
        )
    if status == 404:
        raise DatasetResolveError(
            f"{source} does not exist: {what} answered HTTP 404. Check the "
            f"spelling, and note that a repository you can see while signed in "
            f"may still be private to an anonymous reader."
        )
    raise DatasetResolveError(
        f"could not list {source}: {what} answered HTTP {status}"
    )


def _as_json(response: httpx.Response, *, source: str, what: str) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise DatasetResolveError(
            f"{what} for {source} did not return JSON ({exc}). The origin may "
            f"be serving an error page."
        ) from exc


def _as_xml(response: httpx.Response, *, source: str) -> ET.Element:
    try:
        return ET.fromstring(response.content)
    except ET.ParseError as exc:
        raise DatasetResolveError(
            f"the bucket listing for {source} is not valid XML ({exc}). The "
            f"origin may be serving an error page."
        ) from exc


def _checked_path(raw: object, *, source: str) -> str:
    """A listing path, checked before it becomes a path on someone's disk.

    The origin is a stranger and this string is its input. It ends up as a
    file under ``/work/data/<name>/`` on every volunteer that takes a slice,
    so ``..``, an absolute path, or a backslash is refused here rather than
    relied on being refused three components downstream.
    """
    if not isinstance(raw, str) or not raw:
        raise DatasetResolveError(
            f"{source} listed a file with no usable path ({raw!r})"
        )
    if "\\" in raw or "\x00" in raw or any(
        part in _UNSAFE_PATH_PARTS for part in raw.split("/")
    ):
        raise DatasetResolveError(
            f"{source} listed a file at path {raw!r}. A manifest path becomes "
            f"a file inside /work/data/<name>/ on a volunteer's machine, so it "
            f"must be a plain relative path: no leading '/', no '.' or '..' "
            f"component, no empty component, no backslash."
        )
    return raw


def _checked_size(raw: object, *, source: str, path: str) -> int:
    """A byte count, and only a byte count.

    ``dataset_chunks`` weighs the split by bytes, so a missing size is not
    a zero — it is an unknown that would quietly land every unsized file in
    the first slice.
    """
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise DatasetResolveError(
            f"{source} reported size {raw!r} for {path!r}. A manifest needs a "
            f"non-negative byte count for every file, because the slices are "
            f"balanced by bytes rather than by file count."
        )
    return raw


def _build(
    dataset: dict,
    *,
    revision: str,
    rows: list[tuple[str, str, int, dict]],
) -> Manifest:
    """Apply ``select``, refuse the empty cases, and sort.

    Sorting by path is what makes two submissions of the same dataset cut
    identical slices — the property the whole byte-weighted mapper rests
    on. It happens here, once, rather than being an assumption every caller
    has to remember.
    """
    name = _name(dataset)
    source = dataset["source"]
    select = dataset.get("select")

    if select is not None:
        # `fnmatchcase`, not `fnmatch`: the latter normalises case through
        # `os.path.normcase`, so the same flashml.yaml would select
        # different files depending on the machine the API happens to run
        # on. `*` deliberately crosses '/', which is what makes
        # "plain_text/train-*" read the way people expect.
        kept = [row for row in rows if fnmatch.fnmatchcase(row[0], select)]
        if not kept:
            listed = ", ".join(sorted(row[0] for row in rows)[:5])
            raise DatasetResolveError(
                f"dataset {name!r}: select {select!r} matched no files in "
                f"{source}, which listed {len(rows)} file(s)"
                + (f" (for example: {listed})" if listed else "")
                + ". Refusing rather than running a job with no data."
            )
        rows = kept
    elif not rows:
        raise DatasetResolveError(
            f"dataset {name!r}: {source} listed no files at all. Refusing "
            f"rather than running a job with no data."
        )

    if len(rows) > MAX_MANIFEST_ENTRIES:
        raise DatasetResolveError(
            f"dataset {name!r}: {source} resolves to {len(rows)} files, over "
            f"the {MAX_MANIFEST_ENTRIES} a job spec carries. Narrow it with "
            f"'select:', or combine the shards into larger files."
        )

    seen: set[str] = set()
    for path, *_ in rows:
        if path in seen:
            raise DatasetResolveError(
                f"dataset {name!r}: {source} lists {path!r} twice. Both would "
                f"land on the same file under /work/data/{name}/ and which one "
                f"won would depend on fetch order."
            )
        seen.add(path)

    entries = tuple(
        ManifestEntry(path=path, url=url, size=size, integrity=integrity)
        for path, url, size, integrity in sorted(rows, key=lambda row: row[0])
    )
    return Manifest(name=name, source=source, revision=revision, entries=entries)
