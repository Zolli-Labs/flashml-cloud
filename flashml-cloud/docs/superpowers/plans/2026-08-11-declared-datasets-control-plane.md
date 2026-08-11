# Declared datasets — control-plane half — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a `flashml.yaml` declare a public dataset, resolve it at submit time into a checksummed manifest pinned to an immutable revision, cut it into per-task slices, and refuse the job in the console when no host can hold one.

**Architecture:** All the thinking happens here, once, at submit time. The compiler resolves `hf://` / `s3://` / `r2://` / `https://` into a manifest of `{path, size, integrity}`, pins the revision, cuts a byte-weighted contiguous slice per task, and emits `dataset_slices` into the workload parameters. The runtime forwards it; the agent fetches it. **No dataset byte ever touches our infrastructure** — that is what keeps a job's marginal cost at zero.

**Tech Stack:** Python ≥3.10, FastAPI, pydantic v2 (protocol only), `httpx` (already a dependency), pytest.

**Spec:** `docs/superpowers/specs/2026-08-10-declared-datasets-design.md`, including **§14 amendments**, which override §1–§9 where they conflict.

**Companion plan:** `flashml/flashruntime/docs/superpowers/plans/2026-08-11-declared-datasets-runtime.md` consumes the `dataset_slices` this plan produces. Build in parallel; only release+pin is ordered.

## Global Constraints

- **v1 is PUBLIC ORIGINS ONLY.** No credential is stored, sent, or minted. `0013_github_installations.sql` establishes *"No token, no refresh token, no OAuth grant"* and every token column in this schema is a sha256 of a token **we issued**. This plan must not be the first to break that.
- **The import boundary is test-enforced.** `flashml_cloud_api/*` may import `flashruntime.protocol.*` and nothing else, except the two entries in `tests/test_import_boundary.py::SANCTIONED_EXCEPTIONS`. Deferred imports inside function bodies are caught too.
- **`from __future__ import annotations` in every file.** PEP 604 / 585 hints.
- **Config errors are `ConfigError`** with the house message shape: `f"flashml.yaml {key!r} must be ..., got {value!r}"` — always prefixed `flashml.yaml`, always `!r` on key and value.
- **Absent stays absent.** A parameter must not appear as `[]`/`0` when unused, so the key-missing branch keeps being exercised. (`_local_inputs` and `_dependencies` are the pattern.)
- **A new optional key must not widen the parser.** Every new key gets a `test_..._did_not_widen_the_allowed_keys` companion.
- **No dataset bytes through the coordinator.** The manifest is a few KB of JSON and may be staged as an artifact; the shards never are.
- Tests: plain pytest, no classes. `test_flashml_yaml.py` and `test_compile.py` use no fixtures. Network tests are marked `network` and excluded by default.

---

### Task 1: `datasets:` in `flashml.yaml`

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/flashml_yaml.py` (`OPTIONAL_KEYS` line ~79; `FlashmlConfig` ~line 133; `parse_flashml_yaml` ~line 226; add `_validate_datasets`)
- Test: `flashml-cloud/apps/api/tests/test_flashml_yaml.py` (extend)

**Interfaces:**
- Produces: `FlashmlConfig.datasets: list[dict]`, each `{"name": str, "source": str, "select": str | None, "split": str | None}`. `split` is `None` when the user did not write one — inference happens in Task 4, not here, because it depends on `mode`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_flashml_yaml.py`:

```python
# ---------------------------------------------------------------------------
# datasets — a public origin the HOST fetches, never us
# ---------------------------------------------------------------------------


def test_datasets_defaults_to_empty():
    assert parse_flashml_yaml(MINIMAL).datasets == []


def test_a_minimal_dataset_needs_only_a_name_and_source():
    text = MINIMAL + (
        "\ndatasets:\n"
        "  - name: imdb\n"
        "    source: hf://stanfordnlp/imdb\n"
    )
    config = parse_flashml_yaml(text)
    assert config.datasets == [
        {"name": "imdb", "source": "hf://stanfordnlp/imdb",
         "select": None, "split": None}
    ]


def test_split_is_not_defaulted_here():
    """Inference needs `mode`, which is validated separately — a default
    baked in at parse time would silently win over the inference."""
    text = MINIMAL + "\ndatasets:\n  - name: d\n    source: hf://a/b\n"
    assert parse_flashml_yaml(text).datasets[0]["split"] is None


def test_an_explicit_split_is_kept():
    text = MINIMAL + (
        "\ndatasets:\n  - name: d\n    source: hf://a/b\n    split: replica\n"
    )
    assert parse_flashml_yaml(text).datasets[0]["split"] == "replica"


def test_an_unknown_split_is_refused():
    text = MINIMAL + (
        "\ndatasets:\n  - name: d\n    source: hf://a/b\n    split: sideways\n"
    )
    with pytest.raises(ConfigError, match="split"):
        parse_flashml_yaml(text)


def test_datasets_must_be_a_list_of_mappings():
    with pytest.raises(ConfigError, match="datasets"):
        parse_flashml_yaml(MINIMAL + "\ndatasets: hf://a/b\n")


def test_a_dataset_name_is_a_name_not_a_path():
    """It becomes a directory on a volunteer's disk — same rule as
    `local_inputs`, and for the same reason."""
    for bad in ("../evil", "/abs", "a/b", "."):
        text = MINIMAL + f'\ndatasets:\n  - name: "{bad}"\n    source: hf://a/b\n'
        with pytest.raises(ConfigError, match="name"):
            parse_flashml_yaml(text)


def test_an_unsupported_scheme_is_refused_with_the_supported_list():
    text = MINIMAL + "\ndatasets:\n  - name: d\n    source: ftp://a/b\n"
    with pytest.raises(ConfigError, match="hf://"):
        parse_flashml_yaml(text)


def test_a_dataset_needs_a_source():
    with pytest.raises(ConfigError, match="source"):
        parse_flashml_yaml(MINIMAL + "\ndatasets:\n  - name: d\n")


def test_two_datasets_may_not_share_a_name():
    """They would mount to the same /work/data/<name>/ and silently
    overwrite each other."""
    text = MINIMAL + (
        "\ndatasets:\n"
        "  - name: d\n    source: hf://a/b\n"
        "  - name: d\n    source: hf://c/e\n"
    )
    with pytest.raises(ConfigError, match="name"):
        parse_flashml_yaml(text)


def test_datasets_did_not_widen_the_allowed_keys():
    with pytest.raises(ConfigError, match="dataset"):
        parse_flashml_yaml(MINIMAL + "\ndataset:\n  - name: d\n")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_flashml_yaml.py -k dataset -v`
Expected: FAIL — `AttributeError: 'FlashmlConfig' object has no attribute 'datasets'`.

- [ ] **Step 3: Write the implementation**

Add `"datasets"` to `OPTIONAL_KEYS`. Add to `FlashmlConfig`:

```python
    datasets: list[dict] = field(default_factory=list)
```

Add constants beside `LABEL_RE`:

```python
#: The four addressing schemes v1 understands. All four are fetched
#: ANONYMOUSLY: the scheme says where the bytes are, not who may read them,
#: and v1 stores no credential of any kind.
DATASET_SCHEMES = ("hf://", "s3://", "r2://", "https://")
SPLIT_SHARD = "shard"
SPLIT_REPLICA = "replica"
SPLITS = (SPLIT_SHARD, SPLIT_REPLICA)
```

Add the validator:

```python
def _validate_datasets(value: object) -> list[dict]:
    """``datasets:`` — public origins the HOST fetches, never this API.

    ``split`` is deliberately NOT defaulted here. It is inferred from
    ``mode`` in the compiler (federated → shard, everything else →
    replica), and a default written in at parse time would silently
    outrank that inference. ``None`` means "the user did not say".
    """
    if value is None:
        return []
    if not isinstance(value, list) or isinstance(value, (str, bytes)):
        raise ConfigError(
            f"flashml.yaml 'datasets' must be a list of mappings, each with a "
            f"'name' and a 'source', got {value!r}"
        )
    out: list[dict] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ConfigError(
                f"flashml.yaml 'datasets' entries must be mappings, got {item!r}"
            )
        unknown = set(item) - {"name", "source", "select", "split"}
        if unknown:
            raise ConfigError(
                f"flashml.yaml dataset has unknown key(s) {sorted(unknown)!r}; "
                f"allowed keys are ['name', 'select', 'source', 'split']"
            )
        name = item.get("name")
        if not isinstance(name, str) or not LABEL_RE.match(name) or name in (".", ".."):
            raise ConfigError(
                f"flashml.yaml dataset 'name' must be a name, not a path — it "
                f"becomes a directory on a volunteer's machine, so it must "
                f"start with a letter or digit and use only [A-Za-z0-9._-]; "
                f"got {name!r}"
            )
        if name in seen:
            raise ConfigError(
                f"flashml.yaml declares two datasets called {name!r}; they "
                f"would both mount at /work/data/{name}/ and overwrite each "
                f"other"
            )
        seen.add(name)
        source = item.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ConfigError(
                f"flashml.yaml dataset {name!r} must have a 'source', got "
                f"{source!r}"
            )
        if not source.startswith(DATASET_SCHEMES):
            raise ConfigError(
                f"flashml.yaml dataset {name!r} has an unsupported source "
                f"{source!r}; supported schemes are {list(DATASET_SCHEMES)!r}"
            )
        select = item.get("select")
        if select is not None and not isinstance(select, str):
            raise ConfigError(
                f"flashml.yaml dataset {name!r} 'select' must be a glob "
                f"string, got {select!r}"
            )
        split = item.get("split")
        if split is not None and split not in SPLITS:
            raise ConfigError(
                f"flashml.yaml dataset {name!r} 'split' must be one of "
                f"{list(SPLITS)!r}, got {split!r}"
            )
        out.append({"name": name, "source": source, "select": select, "split": split})
    return out
```

Wire it in `parse_flashml_yaml` beside the other validators and pass `datasets=datasets` to the constructor.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_flashml_yaml.py -v`
Expected: PASS — the whole file.

- [ ] **Step 5: Commit**

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/flashml_yaml.py flashml-cloud/apps/api/tests/test_flashml_yaml.py
git commit -m "feat(api): accept a datasets: block in flashml.yaml"
```

---

### Task 2: Resolve an origin into a pinned manifest

**Files:**
- Create: `flashml-cloud/apps/api/flashml_cloud_api/datasets.py`
- Test: `flashml-cloud/apps/api/tests/test_datasets_resolve.py`

**Interfaces:**
- Produces:
  - `class DatasetResolveError(Exception)`
  - `@dataclass(frozen=True) class ManifestEntry: path: str; url: str; size: int; integrity: dict`
  - `@dataclass(frozen=True) class Manifest: name: str; source: str; revision: str; entries: tuple[ManifestEntry, ...]` with `total_bytes` property
  - `async def resolve(dataset: dict, *, http: httpx.AsyncClient) -> Manifest`

  Task 3 consumes `Manifest`; Task 5 consumes `resolve`.

**The three integrity kinds and why they differ** (spec §4): `hf://` gives real sha256; `s3://`/`r2://` give ETags, which are md5-of-md5s for multipart uploads and therefore change-detection tokens, **not** content hashes; `https://` gives whatever the manifest author declared. The `kind` field carries this honestly rather than implying a guarantee we did not provide.

- [ ] **Step 1: Write the failing test**

```python
# flashml-cloud/apps/api/tests/test_datasets_resolve.py
"""Turning a `source:` into a pinned, checksummed manifest.

No network: every test drives a stub transport. The live contract is
pinned by `scripts/experiments/hf_dataset_origin_probe.py`, which measured
all of this against huggingface.co on 2026-08-10, and by the
`-m network` test at the bottom.

The revision pin is the point. `resolve/main` is mutable; a dataset that
changes under a running job is a class of bug we refuse to have, so
`main` is resolved to a commit SHA once, here, and every task in the run
addresses that SHA.
"""

from __future__ import annotations

import json

import httpx
import pytest

from flashml_cloud_api.datasets import DatasetResolveError, resolve

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
async def test_an_explicit_revision_is_honoured():
    manifest = await _resolve(
        {"name": "d", "source": "hf://a/b@v1.2", "select": None, "split": None},
        _hf_transport(),
    )
    assert manifest.revision == "v1.2"


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
async def test_an_unreachable_origin_is_a_clean_error():
    def boom(request):
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(DatasetResolveError):
        await _resolve(
            {"name": "d", "source": "hf://a/b", "select": None, "split": None},
            httpx.MockTransport(boom),
        )


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
```

> **Note for the implementer:** `s3://` and `r2://` resolution (ListObjectsV2 XML → ETags, `kind: "etag"`) is part of this task. Add tests mirroring the `hf` ones with a stub XML body, and make the ETag `kind` `"etag"` — never `"sha256"`. A multipart ETag looks like `"abc123-14"`; a test must cover that shape and must NOT strip the suffix.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_datasets_resolve.py -v`
Expected: FAIL — `ModuleNotFoundError: flashml_cloud_api.datasets`.

- [ ] **Step 3: Write the implementation**

Create `flashml_cloud_api/datasets.py`. Key rules:

- `hf://ns/name[@rev]` → `GET /api/datasets/{repo}` for `sha`/`private`/`gated`, then `GET /api/datasets/{repo}/tree/{rev}?recursive=1`. Entry URL is `https://huggingface.co/datasets/{repo}/resolve/{sha}/{path}`.
- Refuse `private is True` and any truthy `gated` with a message naming the reason (v1 is public-only).
- `size` comes from `lfs.oid`'s sibling `size`; `integrity = {"kind": "sha256", "value": lfs["oid"]}`. A file with **no** `lfs` block is a small non-LFS file — use `{"kind": "none", "value": entry["oid"]}` and let Task 3 still count its bytes.
- Sort entries by `path` — determinism is what makes two submissions of the same file cut the same slices.
- Wrap every `httpx` exception in `DatasetResolveError` so the route never leaks a 500.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_datasets_resolve.py tests/test_import_boundary.py -v`
Expected: PASS — including the boundary test, which proves this module imported nothing it should not.

- [ ] **Step 5: Commit**

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/datasets.py flashml-cloud/apps/api/tests/test_datasets_resolve.py
git commit -m "feat(api): resolve a public dataset source into a pinned manifest"
```

---

### Task 3: The byte-weighted mapper, and the under-sharding cap

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/elastic.py`
- Test: `flashml-cloud/apps/api/tests/test_dataset_slices.py` (create)

**Interfaces:**
- Consumes: `Manifest` (Task 2).
- Produces:
  - `def dataset_chunks(sizes: Sequence[int], chunks: int) -> list[list[int]]` — contiguous runs of manifest indices, split on cumulative bytes.
  - `def cap_chunks_to_manifest(total_chunks: int, shard_count: int) -> tuple[int, str | None]` — returns the capped count and a human warning (or `None`).

  Task 4 consumes both.

**Why here:** `fleet_shape` and `round_chunk_offset` already live in this module, and its docstring is explicit that duplicated derivations are a bug pinned by tests. One mapper, one repo.

- [ ] **Step 1: Write the failing test**

```python
# flashml-cloud/apps/api/tests/test_dataset_slices.py
"""Cutting a manifest into per-task slices.

Byte-weighted, not count-weighted: real shards are uneven, and dividing by
file count hands one host 10x the bytes and stalls every round on that
straggler.

Contiguous, not strided: a contiguous range is what makes a host's cache
useful across rounds, and `round_chunk_offset` already sweeps the window
forward so the fleet covers an epoch rather than retraining a prefix.
"""

from __future__ import annotations

import pytest

from flashml_cloud_api.elastic import cap_chunks_to_manifest, dataset_chunks


def _cover(slices, n):
    return sorted(i for s in slices for i in s) == list(range(n))


@pytest.mark.parametrize("chunks", [1, 2, 3, 4, 7, 16])
def test_every_file_is_assigned_exactly_once(chunks):
    sizes = [10, 90, 40, 60, 5]
    assert _cover(dataset_chunks(sizes, chunks), len(sizes))


def test_slices_are_contiguous():
    sizes = [10, 90, 40, 60, 5, 100]
    for s in dataset_chunks(sizes, 3):
        assert s == list(range(s[0], s[-1] + 1)) if s else True


def test_the_split_balances_bytes_not_file_count():
    """One huge file and three tiny ones: a count-weighted split would put
    two files with the giant one."""
    sizes = [1000, 1, 1, 1]
    slices = dataset_chunks(sizes, 2)
    loads = [sum(sizes[i] for i in s) for s in slices]
    assert loads[0] == 1000 and loads[1] == 3


def test_more_chunks_than_files_leaves_empty_chunks():
    """Documented, not prevented, here — the CAP is what prevents it, and
    it is applied by the caller so this function stays total."""
    slices = dataset_chunks([1, 2, 3], 7)
    assert _cover(slices, 3)
    assert sum(1 for s in slices if not s) == 4


def test_an_empty_manifest_is_not_a_crash():
    assert dataset_chunks([], 3) == [[], [], []]


def test_zero_byte_files_still_get_assigned():
    """A manifest of empty files has total 0 — the divisor must not be it."""
    assert _cover(dataset_chunks([0, 0, 0], 2), 3)


def test_the_cap_reduces_chunks_to_the_shard_count_and_explains():
    capped, warning = cap_chunks_to_manifest(20, 3)
    assert capped == 3
    assert warning is not None
    assert "3" in warning and "20" in warning


def test_no_cap_and_no_warning_when_the_data_is_fine_grained_enough():
    assert cap_chunks_to_manifest(3, 512) == (3, None)


def test_the_cap_never_returns_zero():
    """A one-slot round is a real round; a zero-slot round is not."""
    capped, _ = cap_chunks_to_manifest(4, 0)
    assert capped == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_dataset_slices.py -v`
Expected: FAIL — `ImportError: cannot import name 'dataset_chunks'`.

- [ ] **Step 3: Write the implementation**

Append to `elastic.py`:

```python
def dataset_chunks(sizes: Sequence[int], chunks: int) -> list[list[int]]:
    """Manifest indices per chunk: contiguous runs split on cumulative BYTES.

    Byte-weighted rather than count-weighted because real shards are
    uneven — dividing 512 files evenly by count hands whoever gets the big
    ones several times the work, and a round waits for its slowest slot.

    Contiguous rather than strided because a contiguous range is what makes
    a host's cache worth having across rounds; ``round_chunk_offset``
    already moves the window so the fleet still covers a whole pass.

    Total, never raising: more chunks than files yields empty chunks, which
    is a real (if wasteful) layout. ``cap_chunks_to_manifest`` is what
    prevents it, and it is the caller's decision because only the caller
    knows whether the split is ``shard`` or ``replica``.
    """
    out: list[list[int]] = [[] for _ in range(max(0, chunks))]
    if not out or not sizes:
        return out
    total = sum(sizes)
    if total <= 0:
        # Every file is empty: fall back to position so nothing is dropped.
        for index in range(len(sizes)):
            out[min(index * chunks // len(sizes), chunks - 1)].append(index)
        return out
    cursor = 0
    running = 0
    for index, size in enumerate(sizes):
        midpoint = running + size / 2
        target = min(int(midpoint * chunks / total), chunks - 1)
        cursor = max(cursor, target)  # contiguity: never step backwards
        out[cursor].append(index)
        running += size
    return out


def cap_chunks_to_manifest(total_chunks: int, shard_count: int) -> tuple[int, str | None]:
    """Bound a round's chunk count by the data's own granularity.

    ``flashml.yaml`` used to ask for ``shards`` and the answer was always
    wrong — "eleven machines online with shards: 3 left eight of them doing
    nothing" is why the knob was removed. A dataset reintroduces the same
    fixed number through the back door: it has however many shards it has.

    So the number is capped rather than obeyed, and the caller is TOLD.
    Silently running a 3-machine round on a 20-machine pool is the exact
    complaint that killed the knob; the difference now is that it is
    visible.
    """
    if shard_count < 1:
        return max(1, total_chunks), None
    if total_chunks <= shard_count:
        return total_chunks, None
    return shard_count, (
        f"this dataset has {shard_count} shard(s), so at most {shard_count} "
        f"of the {total_chunks} machines available can work on it. Split the "
        f"dataset into more files to use the whole fleet."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_dataset_slices.py tests/test_elastic_layout.py -v`
Expected: PASS — both, the second proving the existing layout is untouched.

- [ ] **Step 5: Commit**

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/elastic.py flashml-cloud/apps/api/tests/test_dataset_slices.py
git commit -m "feat(api): byte-weighted contiguous dataset slices, capped by shard count"
```

---

### Task 4: Compile the slices into the job spec

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/compile.py` (add `DATASET_SLICES_PARAM`; add `_dataset_slices`; call from both `compile_to_jobspec` and `compile_federated_round`)
- Test: `flashml-cloud/apps/api/tests/test_compile_datasets.py` (create)

**Interfaces:**
- Consumes: `Manifest` (Task 2), `dataset_chunks`/`cap_chunks_to_manifest` (Task 3).
- Produces: `parameters["dataset_slices"]` — a list with one entry per task, each a list of `{"name", "split", "entries": [...]}`. Consumed by the runtime plan's Task 2.

**Split inference (spec §14.5):** `mode: federated` → `shard`; everything else → `replica`; an explicit `split:` wins.

**`--shard`/`--num-shards` stay (spec §14.4).** Removing them would silently zero the credit of every federated dataset job, because the worker reports `chunks_done: [args.shard]` and a contribution reporting none is averaged in with zero weight.

- [ ] **Step 1: Write the failing test**

```python
# flashml-cloud/apps/api/tests/test_compile_datasets.py
"""Datasets in the compiled job spec.

Two properties matter more than the rest:

* the federated argv KEEPS `--shard` and `--num-shards`. The worker
  reports `chunks_done: [args.shard]`, and a contribution reporting none
  is averaged in with zero weight — so dropping the integers would zero
  every federated dataset job's credit while everything looked healthy.
* `split` is INFERRED from `mode`, because federated means disjoint slices
  and a sweep means every task needs the whole thing.
"""

from __future__ import annotations

import pytest

from flashml_cloud_api.compile import (
    CompileError,
    compile_federated_round,
    compile_to_jobspec,
)
from flashml_cloud_api.datasets import Manifest, ManifestEntry
from flashml_cloud_api.flashml_yaml import parse_flashml_yaml
from flashml_cloud_api.images import resolve_image

CODE_URI = "artifact://uploads/deadbeef/code.tar.gz"
PYTORCH = resolve_image("pytorch-cpu")


def _manifest(name="imdb", n=4):
    return Manifest(
        name=name, source=f"hf://a/{name}", revision="c0ffee",
        entries=tuple(
            ManifestEntry(
                path=f"part-{i:03d}.parquet",
                url=f"https://huggingface.co/datasets/a/{name}/resolve/c0ffee/part-{i:03d}.parquet",
                size=100,
                integrity={"kind": "sha256", "value": f"{i:064d}"},
            )
            for i in range(n)
        ),
    )


def _params(spec):
    return spec["spec"]["workload"]["parameters"]


def test_a_sweep_gets_a_replica_slice_per_task():
    config = parse_flashml_yaml(
        "version: 1\nname: s\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "sweep:\n  lr: [0.1, 0.2, 0.3]\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    spec = compile_to_jobspec(
        config, PYTORCH, CODE_URI, "s", manifests={"imdb": _manifest()}
    )
    slices = _params(spec)["dataset_slices"]
    assert len(slices) == 3, "one slice list per sweep task"
    for task_slice in slices:
        assert task_slice[0]["split"] == "replica"
        assert len(task_slice[0]["entries"]) == 4, "replica means the whole thing"


def test_a_federated_round_gets_disjoint_shards():
    config = parse_flashml_yaml(
        "version: 2\nname: f\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "mode: federated\nepochs: 1\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    spec = compile_federated_round(
        config, PYTORCH, CODE_URI, "f",
        round_index=0, weights_uri=None,
        slot_chunks=[0, 1], total_chunks=2,
        manifests={"imdb": _manifest()},
    )
    slices = _params(spec)["dataset_slices"]
    assert len(slices) == 2
    paths = [e["path"] for s in slices for e in s[0]["entries"]]
    assert sorted(paths) == sorted(set(paths)), "a file was handed to two tasks"
    assert len(paths) == 4, "the pass does not cover the dataset"
    assert all(s[0]["split"] == "shard" for s in slices)


def test_the_federated_argv_still_carries_shard_and_num_shards():
    """Removing these silently zeroes chunks_done and credits nothing."""
    config = parse_flashml_yaml(
        "version: 2\nname: f\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "mode: federated\nepochs: 1\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    spec = compile_federated_round(
        config, PYTORCH, CODE_URI, "f",
        round_index=0, weights_uri=None,
        slot_chunks=[0], total_chunks=1,
        manifests={"imdb": _manifest()},
    )
    command = _params(spec)["command"]
    assert "--num-shards" in command
    assert "--shard" in command


def test_an_explicit_split_overrides_the_inference():
    config = parse_flashml_yaml(
        "version: 2\nname: f\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "mode: federated\nepochs: 1\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n    split: replica\n"
    )
    spec = compile_federated_round(
        config, PYTORCH, CODE_URI, "f",
        round_index=0, weights_uri=None,
        slot_chunks=[0, 1], total_chunks=2,
        manifests={"imdb": _manifest()},
    )
    slices = _params(spec)["dataset_slices"]
    assert all(len(s[0]["entries"]) == 4 for s in slices)


def test_absent_stays_absent():
    config = parse_flashml_yaml(
        "version: 1\nname: s\nimage: pytorch-cpu\nentrypoint: t.py\n"
    )
    spec = compile_to_jobspec(config, PYTORCH, CODE_URI, "s", manifests={})
    assert "dataset_slices" not in _params(spec)


def test_declared_but_unresolved_is_refused_not_silently_dataless():
    """The fail-open trap. A job that declares data and resolves none must
    not compile: its tasks would fetch nothing, find an empty
    /work/data/, and either die on a missing path or quietly train on
    whatever the entrypoint falls back to."""
    config = parse_flashml_yaml(
        "version: 1\nname: s\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    with pytest.raises(CompileError, match="no data"):
        compile_to_jobspec(config, PYTORCH, CODE_URI, "s", manifests={})


def test_a_dataset_resolved_under_a_different_name_is_refused():
    config = parse_flashml_yaml(
        "version: 1\nname: s\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    with pytest.raises(CompileError, match="imdb"):
        compile_to_jobspec(
            config, PYTORCH, CODE_URI, "s", manifests={"other": _manifest("other")}
        )


def test_the_recipe_forwards_the_slice_to_the_task_payload():
    """End to end through the REAL upstream recipe — what the compiler
    emits must be what a node is actually told."""
    from flashruntime.protocol.v1alpha1 import JobSpec
    from flashruntime.recipes.command import CommandRecipe

    config = parse_flashml_yaml(
        "version: 1\nname: s\nimage: pytorch-cpu\nentrypoint: t.py\n"
        "sweep:\n  lr: [0.1, 0.2]\n"
        "datasets:\n  - name: imdb\n    source: hf://a/imdb\n"
    )
    spec = compile_to_jobspec(
        config, PYTORCH, CODE_URI, "s", manifests={"imdb": _manifest()}
    )
    tasks = CommandRecipe().expand("job-1", JobSpec.model_validate(spec))
    assert tasks[0].payload["datasets"][0]["name"] == "imdb"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_compile_datasets.py -v`
Expected: FAIL — `compile_to_jobspec() got an unexpected keyword argument 'manifests'`.

- [ ] **Step 3: Write the implementation**

Add `DATASET_SLICES_PARAM = "dataset_slices"` beside the other param constants, add a `manifests: dict[str, Manifest] | None = None` keyword to both compile functions, and add:

```python
def _dataset_slices(
    config: FlashmlConfig,
    manifests: dict[str, Any] | None,
    parameters: dict[str, Any],
    *,
    task_count: int,
) -> None:
    """Cut every declared dataset into one slice per task.

    ``split`` is inferred from ``mode`` when the file does not say:
    federated means disjoint slices whose union is one pass, and anything
    else means each task needs the whole dataset. An explicit ``split:``
    wins — the inference is a default, not a rule.

    Absent stays absent, the same judgement `_local_inputs` records.
    """
    if not config.datasets:
        return
    # NOT `or not manifests`. Declared-but-unresolved must fail LOUD: an
    # early return here emits a job whose tasks fetch nothing, run against
    # an empty /work/data/, and fail on a missing path — or worse, train on
    # whatever the entrypoint falls back to. Same "does not fail closed"
    # shape the payload forwards in `recipes/command.py` are all commented
    # against. The per-dataset `manifest is None` check below cannot save us
    # if we return before reaching it.
    if not manifests:
        raise CompileError(
            f"job declares {len(config.datasets)} dataset(s) but none were "
            f"resolved — refusing to compile a job that would run with no data"
        )
    default_split = SPLIT_SHARD if config.is_federated else SPLIT_REPLICA
    per_task: list[list[dict[str, Any]]] = [[] for _ in range(task_count)]
    for declared in config.datasets:
        manifest = manifests.get(declared["name"])
        if manifest is None:
            raise CompileError(
                f"dataset {declared['name']!r} was declared but not resolved"
            )
        split = declared.get("split") or default_split
        entries = [
            {"path": e.path, "url": e.url, "size": e.size, "integrity": dict(e.integrity)}
            for e in manifest.entries
        ]
        if split == SPLIT_REPLICA:
            for index in range(task_count):
                per_task[index].append(
                    {"name": manifest.name, "split": split, "entries": list(entries)}
                )
            continue
        groups = dataset_chunks([e.size for e in manifest.entries], task_count)
        for index, group in enumerate(groups):
            per_task[index].append({
                "name": manifest.name,
                "split": split,
                "entries": [entries[i] for i in group],
            })
    parameters[DATASET_SLICES_PARAM] = per_task
```

Call it from `compile_to_jobspec` with `task_count=len(task_params) if task_params else 1`, and from `compile_federated_round` with `task_count=len(slot_chunks)`.

> **Careful:** for `shard` in a federated round, the slice must be cut against `total_chunks` and then selected by the round's `slot_chunks`, not against `len(slot_chunks)` — otherwise a round with fewer slots than chunks silently trains only part of the pass. Cut with `dataset_chunks(sizes, total_chunks)` and index by each slot's chunk id. Add a test for `slot_chunks=[0]` with `total_chunks=4` asserting the task receives only chunk 0's quarter.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_compile_datasets.py tests/test_compile.py -v`
Expected: PASS — both files.

- [ ] **Step 5: Commit**

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/compile.py flashml-cloud/apps/api/tests/test_compile_datasets.py
git commit -m "feat(api): compile dataset slices into the job spec"
```

---

### Task 5: Route wiring, admission, and the under-sharding warning

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py` (`submit_job_from_repo`, after the preflight block ~line 2209, before the artifact upload ~line 2230)
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/db.py` (add `dataset_capacity_in_pool`)
- Test: `flashml-cloud/apps/api/tests/test_jobs_datasets.py` (create)

**Interfaces:**
- Consumes: `resolve` (Task 2), `cap_chunks_to_manifest` (Task 3), compile kwargs (Task 4).
- Produces: a 400 with `findings` when no host can hold a slice; a warning finding when the dataset is under-sharded.

**Ordering is the point** (the route's own docstring): resolve and admit **before** any artifact is staged and before the coordinator is touched. A refused job must leave no artifact, no job row, and no coordinator state.

- [ ] **Step 1: Write the failing test**

```python
# flashml-cloud/apps/api/tests/test_jobs_datasets.py
"""Submitting a job that declares a dataset.

The ordering assertion in each refusal test is not ceremony: a job refused
for its dataset must leave NO staged artifact, NO coordinator request and
NO jobs row, exactly like a preflight error.
"""

from __future__ import annotations

# Uses the make_client / _new_user / _jwt / _post / _job_rows helpers from
# tests/test_jobs_from_repo.py — import them rather than reimplementing.


def test_a_dataset_job_carries_slices_to_the_coordinator(make_client, db, transport):
    """The happy path: a public dataset resolves, slices reach the spec."""


def test_a_job_no_host_can_hold_is_refused_with_both_numbers(make_client, db, transport):
    """Refused in the console in one second, rather than after twenty
    machines each download for forty minutes."""
    # assert r.status_code == 400
    # assert transport.requests == []
    # assert _job_rows(db, user) == []
    # detail names the per-host bytes AND the best advertised capacity


def test_an_under_sharded_dataset_warns_but_still_runs(make_client, db, transport):
    """A 3-shard dataset on a 20-machine pool caps the round and SAYS so.
    Warning, not refusal — a small dataset during development is a
    legitimate thing to run."""
    # assert r.status_code == 201
    # findings contain a level="warning" finding mentioning the shard count


def test_a_gated_dataset_is_a_clean_400(make_client, db, transport):
    """v1 is public-only."""


def test_an_unreachable_origin_does_not_500(make_client, db, transport):
```

> **Note for the implementer:** write these out fully against the existing fixtures in `tests/test_jobs_from_repo.py` — it already provides `make_client(files=...)`, a `FakeCoordinatorTransport` recording `transport.requests`, and `_job_rows(db, owner)`. You will also need to stub `flashml_cloud_api.datasets.resolve` (patch it on the module) so the route test does not reach the network, and to insert machines with a `dataset_cache_bytes` capability into the pool. Follow `test_jobs_from_repo.py`'s `_new_user`/pool-insertion helpers.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_jobs_datasets.py -v`
Expected: FAIL — datasets are parsed but never resolved, so no slices appear.

- [ ] **Step 3: Write the implementation**

In `db.py`:

```python
def dataset_capacity_in_pool(
    db: psycopg.Connection, *, pool_id: str | None
) -> int:
    """The largest dataset cache any ONLINE machine in the pool advertises.

    The MAX, not the sum: a slice is fetched whole by one machine, so what
    matters is whether any single host can hold one — not whether the fleet
    could hold it between them.
    """
```

Read it from `machines.capabilities->>'dataset_cache_bytes'` filtered by `MACHINE_ONLINE_PREDICATE`. **The `capabilities` jsonb column is currently written by nothing** — extend `set_machine_capabilities` (called at `app.py:3178`) to persist `dataset_cache_bytes` from the registration, and add a migration only if a column is genuinely needed. Prefer the existing `capabilities` jsonb over a new column.

In `submit_job_from_repo`, after the preflight refusal and before `code_key = ...`:

```python
        dataset_findings: list[dict[str, str]] = []
        manifests = {}
        if config.datasets:
            try:
                for declared in config.datasets:
                    manifests[declared["name"]] = await dsmod.resolve(
                        declared, http=http_client
                    )
            except dsmod.DatasetResolveError as exc:
                raise HTTPException(status_code=400, detail=safe_text(exc, 500))

            # Admission. Before a byte is staged and before the coordinator
            # is asked for anything: the last cheap moment to say no.
            machines_online = dbmod.count_online_machines(db, pool_id=pool)
            fleet = fleet_shape(machines_online)
            best = dbmod.dataset_capacity_in_pool(db, pool_id=pool)
            for name, manifest in manifests.items():
                declared = next(d for d in config.datasets if d["name"] == name)
                split = declared.get("split") or (
                    "shard" if config.is_federated else "replica"
                )
                if split == "shard":
                    capped, warning = cap_chunks_to_manifest(
                        fleet.total_chunks, len(manifest.entries)
                    )
                    if warning:
                        dataset_findings.append(
                            {"level": "warning", "code": "dataset-under-sharded",
                             "message": f"{name}: {warning}"}
                        )
                    per_host = -(-manifest.total_bytes // max(1, capped))
                else:
                    per_host = manifest.total_bytes
                if per_host > best:
                    return Response(
                        content=json.dumps({
                            "detail": (
                                f"dataset {name!r} needs {per_host // 1024**2} MB "
                                f"on a single host, and the largest dataset cache "
                                f"advertised by an online machine is "
                                f"{best // 1024**2} MB. Raise "
                                f"FLASHNODE_DATA_BUDGET_GB on a host, or use a "
                                f"smaller dataset."
                            ),
                            "findings": rendered + dataset_findings,
                        }),
                        status_code=400,
                        media_type="application/json",
                    )
```

Pass `manifests=manifests` to both compile calls, and merge `dataset_findings` into the `findings` the successful response returns.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_jobs_datasets.py tests/test_jobs_from_repo.py -v`
Expected: PASS — both files.

- [ ] **Step 5: Commit**

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/app.py flashml-cloud/apps/api/flashml_cloud_api/db.py flashml-cloud/apps/api/tests/test_jobs_datasets.py
git commit -m "feat(api): resolve and admit declared datasets at submit time"
```

---

### Task 6: The user-facing guide

**Files:**
- Modify: `flashml-cloud/docs/guides/writing-flashml-yaml.md`

**Interfaces:** none. This is the deliverable a user actually reads.

- [ ] **Step 1: Add `datasets` to the optional-keys table**

```
| `datasets` | list of maps | Public data the *host* fetches before your task starts. See below |
```

- [ ] **Step 2: Write the section**

Place it after `local_inputs` and before `Fanning out`. It must say, in the guide's voice — plain, second person, leading with the surprise:

- **Where to put your data.** Public → Hugging Face (free, CDN-backed, and its anonymous rate limits are per-IP, so a spread-out fleet is an advantage). Public bucket → `s3://`/`r2://`. Anything else → a `https://` manifest. **Private data has no path yet** — use `local_inputs` and host the machine yourself.
- **We never store your data.** We read a file listing at submit time and hand each machine a list of URLs. The bytes go from your origin straight to the machine that needs them.
- **Your task still has no network.** The files are already there, at `/work/data/<name>/`, before your code starts.
- **The revision is pinned.** `hf://org/name` resolves to a commit SHA once, at submit; pushing to the dataset mid-run cannot change what a running job trains on. Pin it yourself with `hf://org/name@<rev>`.
- **`split` is inferred.** `mode: federated` → each machine gets a *different* slice. A `sweep` → every task gets the whole dataset. Override with `split: shard|replica`.
- **A dataset with few files caps your fleet.** Three files means at most three machines, whatever the pool size — the same reason `shards:` was removed. Split into more files to use the whole Crew.
- **`--shard` and `--num-shards` still mean what they meant.** They identify your chunk for `chunks_done`; they are no longer how you slice, because the slicing already happened.

Include a worked example:

```yaml
version: 2
name: imdb-fed
image: pytorch-cpu
entrypoint: train.py
mode: federated
epochs: 3
datasets:
  - name: imdb
    source: hf://stanfordnlp/imdb
    select: "plain_text/train-*.parquet"
```

- [ ] **Step 3: Verify the claims against the spec**

Re-read spec §3, §4, §6.3 and §14. Every number and behaviour in the guide must match. In particular do not promise sha256 verification for `s3://`/`r2://` — those carry ETags, which are not content hashes.

- [ ] **Step 4: Commit**

```bash
git add flashml-cloud/docs/guides/writing-flashml-yaml.md
git commit -m "docs: how to declare a dataset and where to host it"
```

---

## Self-review notes carried from writing this plan

- **Spec §7's admission check said "enough hosts ... advertise ≥ bytes_per_host".** Task 5 implements the **max**, not a count, because a slice is fetched whole by one machine. The spec's phrasing is looser than the implementation; the implementation is right.
- **`_storage_gate` is not called in `submit_job_from_repo`** (it is on `POST /v1alpha1/jobs`). Out of scope here, but it is a real asymmetry worth a task.
- **`machines.capabilities` jsonb is written by nothing today.** Task 5 depends on changing that. If it turns out to be load-bearing elsewhere, add a dedicated column instead and say so in the commit.
