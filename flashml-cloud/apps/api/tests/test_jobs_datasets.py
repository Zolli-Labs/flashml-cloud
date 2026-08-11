"""Submitting a job that declares a `datasets:` block.

Three things are being pinned here, and only the first is the happy path.

**Ordering.** A job refused for its dataset must leave NO staged artifact,
NO coordinator request and NO ``jobs`` row — exactly like an error finding,
and asserted the same three ways
``test_an_error_finding_blocks_the_submission_entirely`` asserts them. The
whole point of resolving at submit time is that a gated repo or a dataset
nothing can hold is a sentence in the console one second after the click,
rather than twenty machines each downloading for forty minutes and then
dying.

**Capacity is the MAX, never the sum.** A slice is fetched whole, by one
machine, into that machine's own cache. What the fleet could hold between
them is not a number any host can spend.

**The under-sharding warning measures the cut, not the file count.** A
dataset with as many files as there are slots can still strand machines,
because the cut is byte-weighted: one dominant file monopolises the middle
of the range and its neighbours get nothing. That case is
``test_one_dominant_file_strands_slots_even_with_a_file_per_slot``, and it
is the reason the warning is driven by ``effective_width`` rather than by
``cap_chunks_to_manifest``'s file-count comparison.

No test here reaches the network: ``datasets.resolve`` is stubbed on the
module, which is also what keeps these tests about the ROUTE rather than
about the Hugging Face API (``test_datasets_resolve.py`` owns that).
"""
from __future__ import annotations

import uuid

import pytest

from flashml_cloud_api import datasets as dsmod
from flashml_cloud_api import db as dbmod
from flashml_cloud_api.datasets import DatasetResolveError, Manifest, ManifestEntry
from flashml_cloud_api.elastic import cap_chunks_to_manifest

from test_federated import (  # the driver-recording client, not a live driver
    FEDERATED_TRAIN_PY,
    federated_client,  # noqa: F401 - fixture
)
from test_jobs_from_repo import (
    CLEAN_TRAIN_PY,
    _job_rows,
    _jwt,
    _new_user,
    _post,
    db,  # noqa: F401 - fixture
    make_client,  # noqa: F401 - fixture
    settings,  # noqa: F401 - fixture
    transport,  # noqa: F401 - fixture
)

GB = 1024 ** 3
MB = 1024 ** 2

REVISION = "e6281661ce1c48d982bc483cf8a173c1bbeb5d31"


# ---------------------------------------------------------------------------
# fixture repos
# ---------------------------------------------------------------------------


def _sweep_repo(values: list[float], *, split: str | None = None) -> dict[str, str]:
    """A `sweep:` over `lr` — one task per value — declaring one dataset.

    A sweep is used for most of these rather than a federated run because
    the two share the whole path under test (resolve, compile, admit) and a
    sweep's task count is written down in the file, so the width a slice is
    cut against is visible in the test instead of depending on which
    machines happen to be online.
    """
    split_line = f"    split: {split}\n" if split else ""
    return {
        "flashml.yaml": (
            "version: 1\n"
            "name: acme-trainer\n"
            "image: python-slim\n"
            "entrypoint: train.py\n"
            "sweep:\n"
            f"  lr: [{', '.join(str(v) for v in values)}]\n"
            "datasets:\n"
            "  - name: imdb\n"
            "    source: hf://stanfordnlp/imdb\n"
            + split_line
        ),
        "train.py": CLEAN_TRAIN_PY,
    }


FEDERATED_DATASET_REPO = {
    "flashml.yaml": (
        "version: 2\n"
        "name: acme-fed\n"
        "image: python-slim\n"
        "entrypoint: train.py\n"
        "mode: federated\n"
        "epochs: 1\n"
        "datasets:\n"
        "  - name: imdb\n"
        "    source: hf://stanfordnlp/imdb\n"
    ),
    "train.py": FEDERATED_TRAIN_PY,
}


# ---------------------------------------------------------------------------
# stubs and helpers
# ---------------------------------------------------------------------------


def _manifest(sizes: list[int], *, name: str = "imdb") -> Manifest:
    return Manifest(
        name=name,
        source=f"hf://stanfordnlp/{name}",
        revision=REVISION,
        entries=tuple(
            ManifestEntry(
                path=f"plain_text/train-{i:05d}.parquet",
                url=(
                    f"https://huggingface.co/datasets/stanfordnlp/{name}"
                    f"/resolve/{REVISION}/plain_text/train-{i:05d}.parquet"
                ),
                size=size,
                integrity={"kind": "sha256", "value": f"{i:064d}"},
            )
            for i, size in enumerate(sizes)
        ),
    )


class RecordingResolve:
    """Stands in for ``datasets.resolve``: records what it was asked for,
    answers from a canned manifest, and never opens a socket."""

    def __init__(self, manifest: Manifest | None = None,
                 error: Exception | None = None):
        self.manifest = manifest
        self.error = error
        self.calls: list[dict] = []

    async def __call__(self, dataset: dict, *, http) -> Manifest:
        self.calls.append(dataset)
        if self.error is not None:
            raise self.error
        assert self.manifest is not None
        return self.manifest


def _stub_resolve(monkeypatch, resolver: RecordingResolve) -> RecordingResolve:
    # Patched on the MODULE, which is the object `app.py` holds — it imports
    # `datasets as dsmod` and looks the attribute up per call, so this reaches
    # the route without the route knowing.
    monkeypatch.setattr(dsmod, "resolve", resolver)
    return resolver


def _pool_with(db, owner: str, *caches: int) -> str:
    """A fresh pool holding one online machine per advertised cache size.

    Pool-scoped throughout, deliberately: an unscoped submit takes the max
    over every online machine in the database, and this suite shares its
    Postgres with every other module in the run. A pool of this test's own
    making is the only capacity number a test can actually assert.
    """
    pool_id = str(dbmod.create_pool(db, name=f"crew-{uuid.uuid4().hex[:8]}",
                                    owner_id=owner)["id"])
    for cache in caches:
        machine_id = dbmod.insert_machine(
            db, owner_id=owner, node_id=f"node-{uuid.uuid4().hex[:10]}",
            name="a laptop", platform="linux",
        )
        dbmod.set_machine_capabilities(
            db, machine_id=machine_id, sandbox_capable=True, argv_capable=True,
            unsandboxed_argv_capable=False, module_capable=True,
            dataset_cache_bytes=cache,
        )
        with db.cursor() as cur:
            cur.execute(
                "update public.machines set status = 'active', "
                "last_seen_at = now() where id = %s",
                (machine_id,),
            )
        dbmod.bind_machine_pool(db, machine_id=machine_id, pool_id=pool_id)
    return pool_id


def _slices(spec: dict) -> list:
    return spec["spec"]["workload"]["parameters"]["dataset_slices"]


def _warnings(body: dict) -> list[dict]:
    return [f for f in body["findings"] if f["code"] == "dataset-under-sharded"]


# ---------------------------------------------------------------------------
# 1. the happy path — the slices reach the coordinator
# ---------------------------------------------------------------------------


def test_a_dataset_job_carries_slices_to_the_coordinator(
    make_client, db, transport, monkeypatch
):
    _stub_resolve(monkeypatch, RecordingResolve(_manifest([100, 100, 100, 100])))
    client = make_client(_sweep_repo([0.1, 0.2]))
    alice = _new_user(db)
    pool = _pool_with(db, alice, 1 * GB)

    r = _post(client, _jwt(alice), pool=pool)
    assert r.status_code == 201, r.text

    slices = _slices(transport.submitted[0])
    assert len(slices) == 2, "one slice list per sweep task"
    for task_slice in slices:
        assert [d["name"] for d in task_slice] == ["imdb"]
        # A sweep infers `replica`: every task needs the whole dataset.
        assert task_slice[0]["split"] == "replica"
        assert len(task_slice[0]["entries"]) == 4


def test_the_slices_carry_the_pinned_revision_not_a_branch(
    make_client, db, transport, monkeypatch
):
    """The URL a volunteer fetches is the one the manifest pinned. A job
    that addressed `resolve/main` could train on different bytes in round
    three than in round one and nothing would say so."""
    _stub_resolve(monkeypatch, RecordingResolve(_manifest([100, 100])))
    client = make_client(_sweep_repo([0.1]))
    alice = _new_user(db)
    pool = _pool_with(db, alice, 1 * GB)

    assert _post(client, _jwt(alice), pool=pool).status_code == 201
    entries = _slices(transport.submitted[0])[0][0]["entries"]
    assert entries and all(REVISION in e["url"] for e in entries)
    assert all(e["integrity"]["kind"] == "sha256" for e in entries)


def test_the_declared_dataset_is_what_gets_resolved(
    make_client, db, monkeypatch
):
    resolver = _stub_resolve(monkeypatch, RecordingResolve(_manifest([100])))
    client = make_client(_sweep_repo([0.1]))
    alice = _new_user(db)
    pool = _pool_with(db, alice, 1 * GB)

    assert _post(client, _jwt(alice), pool=pool).status_code == 201
    assert resolver.calls == [
        {"name": "imdb", "source": "hf://stanfordnlp/imdb",
         "select": None, "split": None}
    ]


def test_a_job_declaring_no_dataset_resolves_nothing(
    make_client, db, transport, monkeypatch
):
    """Absent stays absent. The overwhelming majority of jobs declare no
    data, and none of them may gain a network call, a capacity query, or a
    `dataset_slices` key nothing reads."""
    resolver = _stub_resolve(monkeypatch, RecordingResolve(_manifest([100])))
    client = make_client()
    alice = _new_user(db)

    r = _post(client, _jwt(alice))
    assert r.status_code == 201, r.text
    assert resolver.calls == []
    assert "dataset_slices" not in transport.submitted[0]["spec"]["workload"]["parameters"]


# ---------------------------------------------------------------------------
# 2. no host can hold a slice — refused, with both numbers
# ---------------------------------------------------------------------------


def test_a_job_no_host_can_hold_is_refused_with_both_numbers(
    make_client, db, transport, monkeypatch
):
    """Refused in the console in one second, rather than after twenty
    machines each download for forty minutes and die."""
    _stub_resolve(monkeypatch, RecordingResolve(_manifest([2 * GB] * 4)))
    client = make_client(_sweep_repo([0.1, 0.2]))
    alice = _new_user(db)
    pool = _pool_with(db, alice, 2 * GB)

    r = _post(client, _jwt(alice), pool=pool)
    assert r.status_code == 400, r.text

    detail = r.json()["detail"]
    assert "8.0 GB" in detail, "the bytes one host would have to hold"
    assert "2.0 GB" in detail, "the best any online machine advertises"
    assert "imdb" in detail

    # Nothing reached the coordinator: no artifact staged, no job submitted.
    assert transport.requests == []
    # And nothing was written to the database.
    assert _job_rows(db, alice) == []


def test_capacity_is_the_biggest_machine_not_the_fleets_total(
    make_client, db, transport, monkeypatch
):
    """Four machines with 2 GB each are not an 8 GB cache. A slice is
    fetched whole by ONE machine, so the sum is a number nobody can spend."""
    _stub_resolve(monkeypatch, RecordingResolve(_manifest([3 * GB])))
    client = make_client(_sweep_repo([0.1]))
    alice = _new_user(db)
    pool = _pool_with(db, alice, 2 * GB, 2 * GB, 2 * GB, 2 * GB)

    r = _post(client, _jwt(alice), pool=pool)
    assert r.status_code == 400, r.text
    assert "2.0 GB" in r.json()["detail"]
    assert transport.requests == []
    assert _job_rows(db, alice) == []


def test_the_biggest_machine_in_the_pool_is_the_one_that_decides(
    make_client, db, monkeypatch
):
    """The mirror of the test above: one machine large enough is enough,
    however small its neighbours are."""
    _stub_resolve(monkeypatch, RecordingResolve(_manifest([3 * GB])))
    client = make_client(_sweep_repo([0.1]))
    alice = _new_user(db)
    pool = _pool_with(db, alice, 1 * MB, 4 * GB, 1 * MB)

    assert _post(client, _jwt(alice), pool=pool).status_code == 201


def test_a_pool_advertising_nothing_refuses_and_explains_why(
    make_client, db, transport, monkeypatch
):
    """Fail closed, the same polarity the runtime's placement gate takes:
    a machine that advertises no dataset cache is saying "send me no dataset
    work", so a pool of them can run none of it. The message has to say that
    outright, or the number reads as a bug."""
    _stub_resolve(monkeypatch, RecordingResolve(_manifest([100])))
    client = make_client(_sweep_repo([0.1]))
    alice = _new_user(db)
    pool = _pool_with(db, alice)  # a pool with no machines at all

    r = _post(client, _jwt(alice), pool=pool)
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "0 bytes" in detail
    assert "advertises any dataset cache" in detail
    assert transport.requests == []
    assert _job_rows(db, alice) == []


def test_an_unscoped_submission_is_admitted_against_the_whole_crew(
    make_client, db, transport, monkeypatch
):
    """No `pool`, so the job goes to the public queue where any online
    machine may claim it and the capacity is the fleet-wide max.

    Four terabytes rather than a number close to a real machine's budget:
    this suite shares one Postgres, so a fleet-wide query sees whatever
    machines other modules left online. Nothing any of them advertises will
    ever be in this range, which is what makes the assertion stable while
    still exercising the unscoped branch.
    """
    _stub_resolve(monkeypatch, RecordingResolve(_manifest([4 * 1024 ** 4])))
    client = make_client(_sweep_repo([0.1]))
    alice = _new_user(db)

    r = _post(client, _jwt(alice))
    assert r.status_code == 400, r.text
    assert "4096.0 GB" in r.json()["detail"]
    assert "in the Crew" in r.json()["detail"]
    assert transport.requests == []
    assert _job_rows(db, alice) == []


def test_the_bytes_counted_are_the_slice_not_the_average(
    make_client, db, transport, monkeypatch
):
    """`total_bytes / slots` is the tempting estimate and it is wrong in the
    direction that matters. One 3 GB file among four tiny ones averages to
    under a gigabyte per slot, but the machine that gets it needs three —
    and a job admitted on the average is one the runtime's own placement
    gate then refuses on every host in the Crew."""
    sizes = [1 * MB, 1 * MB, 3 * GB, 1 * MB, 1 * MB]
    assert sum(sizes) // 4 < 1 * GB, "sanity: the average really is under 1 GB"
    _stub_resolve(monkeypatch, RecordingResolve(_manifest(sizes)))
    client = make_client(_sweep_repo([0.1, 0.2, 0.3, 0.4], split="shard"))
    alice = _new_user(db)
    pool = _pool_with(db, alice, 1 * GB)

    r = _post(client, _jwt(alice), pool=pool)
    assert r.status_code == 400, r.text
    assert "3.0 GB" in r.json()["detail"]
    assert transport.requests == []
    assert _job_rows(db, alice) == []


# ---------------------------------------------------------------------------
# 3. under-sharding warns, and still runs
# ---------------------------------------------------------------------------


def test_an_under_sharded_dataset_warns_but_still_runs(
    make_client, db, transport, monkeypatch
):
    """A two-file dataset over five slots caps the round and SAYS so.
    Warning, not refusal — a small dataset during development is a
    legitimate thing to run (owner decision)."""
    _stub_resolve(monkeypatch, RecordingResolve(_manifest([100, 100])))
    client = make_client(_sweep_repo([0.1, 0.2, 0.3, 0.4, 0.5], split="shard"))
    alice = _new_user(db)
    pool = _pool_with(db, alice, 1 * GB)

    r = _post(client, _jwt(alice), pool=pool)
    assert r.status_code == 201, r.text

    warnings = _warnings(r.json())
    assert len(warnings) == 1
    assert warnings[0]["level"] == "warning"
    message = warnings[0]["message"]
    assert "imdb" in message
    assert "only 2 of this job's 5" in message, message

    # It really ran: the job was submitted and recorded.
    assert len(transport.job_submissions) == 1
    assert len(_job_rows(db, alice)) == 1


def test_one_dominant_file_strands_slots_even_with_a_file_per_slot(
    make_client, db, monkeypatch
):
    """The reason the warning is not driven by the file count.

    Five files over five slots passes every count-based check — the cap
    below returns no warning at all — but the cut is byte-weighted, so the
    4200-byte file monopolises the middle of the range and two slots receive
    nothing. The number a user needs is how many slots the cut FILLS.
    """
    sizes = [300, 100, 4200, 50, 900]
    assert cap_chunks_to_manifest(5, len(sizes)) == (5, None), (
        "the file-count proxy sees nothing wrong here — that is the point"
    )

    _stub_resolve(monkeypatch, RecordingResolve(_manifest(sizes)))
    client = make_client(_sweep_repo([0.1, 0.2, 0.3, 0.4, 0.5], split="shard"))
    alice = _new_user(db)
    pool = _pool_with(db, alice, 1 * GB)

    r = _post(client, _jwt(alice), pool=pool)
    assert r.status_code == 201, r.text

    warnings = _warnings(r.json())
    assert len(warnings) == 1
    assert "only 3 of this job's 5" in warnings[0]["message"]


def test_a_well_spread_dataset_warns_about_nothing(
    make_client, db, monkeypatch
):
    """The negative half. A warning that fires on healthy jobs is noise, and
    noise is how the real one gets ignored."""
    _stub_resolve(monkeypatch, RecordingResolve(_manifest([100] * 8)))
    client = make_client(_sweep_repo([0.1, 0.2], split="shard"))
    alice = _new_user(db)
    pool = _pool_with(db, alice, 1 * GB)

    r = _post(client, _jwt(alice), pool=pool)
    assert r.status_code == 201, r.text
    assert _warnings(r.json()) == []


def test_a_replica_dataset_never_warns_about_sharding(
    make_client, db, monkeypatch
):
    """A replica lands whole on every task, so a one-file dataset strands
    nobody — there is nothing to spread."""
    _stub_resolve(monkeypatch, RecordingResolve(_manifest([100])))
    client = make_client(_sweep_repo([0.1, 0.2, 0.3, 0.4, 0.5]))
    alice = _new_user(db)
    pool = _pool_with(db, alice, 1 * GB)

    r = _post(client, _jwt(alice), pool=pool)
    assert r.status_code == 201, r.text
    assert _warnings(r.json()) == []


def test_the_warning_rides_along_with_a_refusal_too(
    make_client, db, transport, monkeypatch
):
    """Two problems, one answer — the same rule preflight already follows.
    A user who fixes the capacity and resubmits should not then discover the
    sharding problem for the first time."""
    _stub_resolve(monkeypatch, RecordingResolve(_manifest([4 * GB, 4 * GB])))
    client = make_client(_sweep_repo([0.1, 0.2, 0.3, 0.4, 0.5], split="shard"))
    alice = _new_user(db)
    pool = _pool_with(db, alice, 1 * GB)

    r = _post(client, _jwt(alice), pool=pool)
    assert r.status_code == 400, r.text
    assert len(_warnings(r.json())) == 1
    assert transport.requests == []
    assert _job_rows(db, alice) == []


# ---------------------------------------------------------------------------
# 4. an origin we cannot read is a clean 400, never a 500
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "hf://a/b is gated: the Hugging Face dataset API reports gating. "
        "FlashML v1 supports public origins only.",
        "hf://a/b is private: FlashML v1 supports public origins only.",
        "could not reach the Hugging Face dataset API for hf://a/b: "
        "ConnectError: no route",
        "hf://a/b: the file listing matched no files",
    ],
    ids=["gated", "private", "unreachable", "empty-select"],
)
def test_an_origin_we_cannot_read_is_a_clean_400(
    make_client, db, transport, monkeypatch, message
):
    """v1 is public-only, and an origin that is gated, private, renamed or
    simply down must never become a 500 — the user can act on all four."""
    _stub_resolve(monkeypatch, RecordingResolve(error=DatasetResolveError(message)))
    client = make_client(_sweep_repo([0.1]))
    alice = _new_user(db)
    pool = _pool_with(db, alice, 1 * GB)

    r = _post(client, _jwt(alice), pool=pool)
    assert r.status_code == 400, r.text
    assert "Traceback" not in r.json()["detail"]
    assert transport.requests == []
    assert _job_rows(db, alice) == []


def test_a_resolve_failure_leaves_no_artifact_even_on_the_last_dataset(
    make_client, db, transport, monkeypatch
):
    """Resolution happens before the code tarball is staged, so a second
    dataset failing cannot leave the first one's job half-created."""

    class _SecondFails(RecordingResolve):
        async def __call__(self, dataset, *, http):
            self.calls.append(dataset)
            if len(self.calls) > 1:
                raise DatasetResolveError(f"{dataset['source']} is gated")
            return _manifest([100], name=dataset["name"])

    files = _sweep_repo([0.1])
    files["flashml.yaml"] += "  - name: wiki\n    source: hf://a/wiki\n"
    _stub_resolve(monkeypatch, _SecondFails())
    client = make_client(files)
    alice = _new_user(db)
    pool = _pool_with(db, alice, 1 * GB)

    r = _post(client, _jwt(alice), pool=pool)
    assert r.status_code == 400
    assert transport.requests == []
    assert _job_rows(db, alice) == []


# ---------------------------------------------------------------------------
# 5. a federated round cuts the pass across the fleet
# ---------------------------------------------------------------------------


def test_a_federated_round_cuts_disjoint_slices_across_the_fleet(
    federated_client, db, monkeypatch
):
    """`mode: federated` infers `shard`: the slots partition one pass rather
    than each training all of it. The union has to be the whole dataset and
    the intersection has to be empty, or the round either misses data or
    trains the same bytes twice and calls it an epoch."""
    _stub_resolve(monkeypatch, RecordingResolve(_manifest([100] * 6)))
    client = federated_client(FEDERATED_DATASET_REPO)
    alice = _new_user(db)
    pool = _pool_with(db, alice, 1 * GB, 1 * GB, 1 * GB)

    r = _post(client, _jwt(alice), pool=pool)
    assert r.status_code == 201, r.text
    assert r.json()["slots"] == 3, "one slot per online machine in the pool"

    slices = _slices(_job_rows(db, alice)[0]["spec"])
    assert len(slices) == 3
    assert all(s[0]["split"] == "shard" for s in slices)
    paths = [e["path"] for s in slices for e in s[0]["entries"]]
    assert sorted(paths) == sorted(set(paths)), "a file was handed to two slots"
    assert len(paths) == 6, "the pass does not cover the dataset"


def test_a_federated_job_on_a_thin_dataset_warns_about_the_fleet(
    federated_client, db, monkeypatch
):
    """The complaint that killed the `shards:` knob, made visible: eleven
    machines online and a dataset that can only occupy two of them."""
    _stub_resolve(monkeypatch, RecordingResolve(_manifest([100, 100])))
    client = federated_client(FEDERATED_DATASET_REPO)
    alice = _new_user(db)
    pool = _pool_with(db, alice, *([1 * GB] * 5))

    r = _post(client, _jwt(alice), pool=pool)
    assert r.status_code == 201, r.text
    warnings = _warnings(r.json())
    assert len(warnings) == 1
    assert "only 2 of this job's 5" in warnings[0]["message"]


# ---------------------------------------------------------------------------
# 6. where the advertised capacity comes from
# ---------------------------------------------------------------------------


def test_dataset_capacity_in_pool_is_the_max_not_the_sum(db):
    owner = _new_user(db)
    pool = _pool_with(db, owner, 1 * GB, 4 * GB, 2 * GB)
    assert dbmod.dataset_capacity_in_pool(db, pool_id=pool) == 4 * GB


def test_an_empty_pool_advertises_nothing(db):
    owner = _new_user(db)
    pool = _pool_with(db, owner)
    assert dbmod.dataset_capacity_in_pool(db, pool_id=pool) == 0


def test_a_machine_that_stopped_reporting_is_not_capacity(db):
    """A closed laptop's disk is not somewhere a slice can go — the same
    predicate `count_online_machines` applies, for the same reason."""
    owner = _new_user(db)
    pool = _pool_with(db, owner, 4 * GB)
    with db.cursor() as cur:
        cur.execute(
            "update public.machines set last_seen_at = now() - interval '1 hour'"
        )
    assert dbmod.dataset_capacity_in_pool(db, pool_id=pool) == 0


def test_a_machine_outside_the_pool_is_not_capacity(db):
    """Owning a machine is not opting it into a pool — the same
    `machine_pools` intersection every other pool count applies."""
    owner = _new_user(db)
    pool = _pool_with(db, owner)
    unbound = dbmod.insert_machine(
        db, owner_id=owner, node_id=f"node-{uuid.uuid4().hex[:10]}",
        name="a laptop", platform="linux",
    )
    dbmod.set_machine_capabilities(
        db, machine_id=unbound, sandbox_capable=True, argv_capable=True,
        unsandboxed_argv_capable=False, module_capable=True,
        dataset_cache_bytes=8 * GB,
    )
    with db.cursor() as cur:
        cur.execute(
            "update public.machines set status = 'active', last_seen_at = now() "
            "where id = %s",
            (unbound,),
        )
    assert dbmod.dataset_capacity_in_pool(db, pool_id=pool) == 0


def test_a_machine_that_never_registered_a_cache_advertises_nothing(db):
    """`capabilities` defaults to `{}`. Reading a key that is not there must
    be zero, not a crash mid-submit."""
    owner = _new_user(db)
    pool = _pool_with(db, owner)
    machine_id = dbmod.insert_machine(
        db, owner_id=owner, node_id=f"node-{uuid.uuid4().hex[:10]}",
        name="a laptop", platform="linux",
    )
    with db.cursor() as cur:
        cur.execute(
            "update public.machines set status = 'active', last_seen_at = now() "
            "where id = %s",
            (machine_id,),
        )
    dbmod.bind_machine_pool(db, machine_id=machine_id, pool_id=pool)
    assert dbmod.dataset_capacity_in_pool(db, pool_id=pool) == 0


def test_a_type_confused_capability_advertises_nothing_rather_than_raising(db):
    """The value is jsonb written by an agent. A string where a number
    belongs must cost that machine its capacity, not cost the submitter a
    500."""
    owner = _new_user(db)
    pool = _pool_with(db, owner, 4 * GB)
    with db.cursor() as cur:
        cur.execute(
            "update public.machines set capabilities = "
            "'{\"dataset_cache_bytes\": \"lots\"}'::jsonb"
        )
    assert dbmod.dataset_capacity_in_pool(db, pool_id=pool) == 0


def test_re_registering_with_less_retracts_the_advertised_cache(db):
    """Overwrite, never merge: a machine that comes back with a smaller
    budget must show the smaller one. Absent means zero for the same
    reason."""
    owner = _new_user(db)
    pool = _pool_with(db, owner, 8 * GB)
    machine_id = str(dbmod.list_machines_for_owner(db, owner)[0]["id"])

    dbmod.set_machine_capabilities(
        db, machine_id=machine_id, sandbox_capable=True, argv_capable=True,
        unsandboxed_argv_capable=False, module_capable=True,
    )
    assert dbmod.dataset_capacity_in_pool(db, pool_id=pool) == 0


def test_the_registration_route_persists_the_advertised_cache(
    make_client, db
):
    """The other half of the loop: what an agent says at registration is
    what a submit is judged against. The `capabilities` jsonb was written by
    nothing before this."""
    from flashml_cloud_api import enrolment

    client = make_client()
    owner = _new_user(db)
    started = enrolment.start_device_code(
        db, f"n-{uuid.uuid4().hex[:8]}", "a laptop", "linux"
    )
    machine_id = enrolment.approve_device_code(db, started["user_code"], owner)
    token = enrolment.redeem_device_code(db, started["device_code"])

    # `dataset_cache_bytes` rides inside `capabilities`, unlike the four
    # booleans beside it — it is a field of the runtime's `NodeCapabilities`,
    # not of the registration envelope.
    client.post(
        "/v1alpha1/nodes/register",
        json={
            "schema_version": "v1alpha1",
            "sandbox_capable": True,
            "capabilities": {"cpu_cores": 4, "dataset_cache_bytes": 16 * GB},
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    with db.cursor() as cur:
        cur.execute(
            "select capabilities from public.machines where id = %s",
            (machine_id,),
        )
        assert cur.fetchone()["capabilities"]["dataset_cache_bytes"] == 16 * GB


def test_a_capability_typo_advertises_nothing(make_client, db):
    """`dataset_cache_bytes: true` is an `int` subclass away from reading as
    one byte of capacity — the same exclusion the runtime's gate makes
    explicitly, for the same reason."""
    from flashml_cloud_api import enrolment

    client = make_client()
    owner = _new_user(db)
    started = enrolment.start_device_code(
        db, f"n-{uuid.uuid4().hex[:8]}", "a laptop", "linux"
    )
    machine_id = enrolment.approve_device_code(db, started["user_code"], owner)
    token = enrolment.redeem_device_code(db, started["device_code"])

    client.post(
        "/v1alpha1/nodes/register",
        json={"capabilities": {"dataset_cache_bytes": True}},
        headers={"Authorization": f"Bearer {token}"},
    )

    with db.cursor() as cur:
        cur.execute(
            "select capabilities from public.machines where id = %s",
            (machine_id,),
        )
        assert cur.fetchone()["capabilities"]["dataset_cache_bytes"] == 0
