# Where the data lives — dataset access on volunteer compute

**Date:** 2026-08-10
**Status:** research note. No decision taken; §7 is a recommendation awaiting
owner review.
**Question asked:** a model needs data. Users can have datasets that are large,
or merely bigger than a toy. Supabase cannot host them and should not.
Where does the data live, and what does FlashML do about it?
**Roadmap item:** `ROADMAP.md` P1.7's second half ("dataset extension: needs
design"), which this note is the design input for.

---

## 1. The problem, stated precisely

Today a task gets its bytes from exactly three places, and only one of them is
usable for real data.

| Path | Cap | Works when |
|---|---|---|
| Committed in the repo, staged as the code tarball | **32 MiB** for the whole repo (`MAX_REPO_TARBALL_BYTES`) | The data is a CSV |
| Generated at runtime | — | It is a demo (`flashml-examples` synthesises tensors) |
| `local_inputs:` — a label the host bind-mounts read-only | Host's disk | The person with the data is also the person hosting the machine |

Everything else has **no path at all**. And the reason is not an oversight: a
task runs with `--network none`, and preflight treats a networking import as an
**error**, not a style note (`preflight.py:52`). A task cannot fetch its own
data. Ever. That constraint is load-bearing — it is most of what makes hosting
a stranger's job safe enough to ask a volunteer to do — and nothing in this
note proposes weakening it.

So the question is not "how does the task download data". It is:

> **Who fetches the bytes, from where, on whose authority, and how does the
> fleet agree on which machine gets which part?**

---

## 2. What FlashML already has that constrains the answer

Read before proposing anything, because three of these are load-bearing and one
is a trap.

**2.1 Chunks are shaped by the fleet, not by the data.** `fleet_shape()`
(`elastic.py:69`) sets `total_chunks = slots = min(max(1, machines_online), 100)`.
A round's chunk count is decided at submit time from **who happens to be
online**, and the task is handed `--num-shards K --shard N` as bare integers
(`compile.py:592-603`). The user's own code does the mapping —
`shard_of(x, y, args.shard, args.num_shards)` strides the array.

This is the crux of the whole problem. A dataset has a **fixed, natural** shard
structure (files, row groups, tar members) decided when it was prepared. A
FlashML round has a **variable** chunk count decided seconds before it runs.
Any design has to map one onto the other, deterministically, without the user
having to think about it.

**2.2 There is no shared disk, and there must not be one.** Artifacts move over
HTTP through the coordinator. This has already cost one benchmarking effort
that assumed a filesystem. Any dataset design that quietly reintroduces "and
then they all read from the same mount" is wrong before it starts.

**2.3 The host agent already has a bounded, LRU, disk-aware cache.**
`flashnode/executor/environments.py` caches per-dependency-set virtualenvs:
keyed on a hash, bounded by `FLASHNODE_ENV_BUDGET_GB`, evicted LRU, and it
**refuses rather than fills the disk** (`_refuse_if_disk_is_short`, `:326`).
Its module docstring says the quiet part: *"Disk is somebody else's."*

A dataset cache is the same object with a different key. This is the single
biggest piece of reuse available, and it means the hard part (not destroying a
volunteer's laptop) is already solved and tested.

**2.4 The host has network; the task does not.** The agent already fetches
things — the code tarball, container images, and now pip dependencies — before
the sandbox closes. This is the seam. Data fetching belongs here and nowhere
else.

**2.5 `local_inputs` is a release blocker.** The 2026-08 pre-launch security
audit flags it. The one real path for real data today is not cleared to ship.

---

## 3. How comparable systems solve this

### 3.1 Volunteer computing — BOINC's locality scheduling

BOINC hit this exact problem twenty years ago with Einstein@home, which has
large input files each used by many workunits. Its answer is
**[locality scheduling](https://github.com/BOINC/boinc/wiki/WorkDistribution)**:
the scheduler RPC carries a list of **sticky files** already resident on the
host, and the server *preferentially sends jobs that use those files*.

The insight generalises past BOINC: **on volunteer hardware, the scarce
resource is not compute, it is the bandwidth to move data to compute.** A
scheduler that ignores what a host already has will re-download the same shards
forever. BOINC's own docs also name what it lacks — awareness of client
proximity and client-to-client transfer — which is worth knowing before
inventing either.

### 3.2 Decentralized training — Prime Intellect

[INTELLECT-1](https://arxiv.org/pdf/2412.01152) and
[INTELLECT-2](https://www.primeintellect.ai/blog/intellect-2) trained across
continents on heterogeneous nodes. Two findings matter here:

- Their hybrid is **FSDP for intra-node efficiency, DiLoCo for inter-node** —
  low-communication outer steps (100 inner steps per sync) with 8-bit gradient
  quantization, up to 400× less communication than naive data-parallel.
- Nodes join and leave mid-run; the `ElasticDeviceMesh` exists because the
  world size is not fixed.

**This is a correction to the framing in the question.** FSDP shards the
*model* and needs an all-gather per layer per step — it is a datacentre
interconnect technique and does not survive the public internet between nodes.
What survives is **DDP-shaped data parallelism with infrequent averaging**,
which is what FlashML's FedAvg already is. So the data question is a
**DDP/data-sharding** question, not an FSDP one. FSDP stays relevant only
*inside* one multi-GPU host, where it is the host's business and not FlashML's.

### 3.3 The dataset formats that were built for exactly this

Three formats, one shared idea: **a dataset is a list of independently
addressable shards plus an index.**

- **[WebDataset](https://pytorch.org/blog/efficient-pytorch-io-library-for-large-datasets-many-files-many-gpus/)** —
  plain POSIX tar shards. Sequential reads, trivially cacheable, works over
  plain HTTP. Weak on deterministic multi-node assignment: you need extra
  tooling for exact epochs and unequal shard counts.
- **[MosaicML Streaming (MDS)](https://docs.mosaicml.com/projects/streaming/en/stable/getting_started/main_concepts.html)** —
  built for this problem specifically. Splits samples between nodes, ranks and
  workers, with **deterministic sample ordering** so a run resumes exactly
  where it stopped without replaying the epoch. Ultra-low random-access
  latency.
- **[HuggingFace parquet + HTTP range requests](https://huggingface.co/docs/hub/en/datasets-streaming)** —
  row groups of ~100 MB, pages of ~1 MB, and range requests mean a reader
  fetches only the row groups and columns it needs. Free hosting, CDN-backed.

MDS's deterministic ordering is the property FlashML needs most, because a
volunteer machine dying mid-round is the normal case here, not the exception.

### 3.4 Serverless GPU platforms

Modal, RunPod and SkyPilot all resolve this the same way: **the user's data
lives in the user's object storage**, and the platform provides a mount, a
persistent volume, or a cache. Modal does filesystem snapshotting; RunPod does
NVMe caching of weights to avoid re-downloading from S3. None of them stores
customer datasets as a product feature — they cache them near compute.

That is the industry answer to the question, and it is available to FlashML:
**users host their data; the platform is a cache and a scheduler.**

### 3.5 Authorization for untrusted compute

[Presigned URLs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url)
are the standard tool: time-limited, scoped to one object and one operation,
and they carry no credential the holder can reuse elsewhere. Notably they can
be **shorter-lived than STS temporary credentials**, whose floor is 15 minutes.
Cloudflare R2 supports the same S3 presigning.

This matters enormously here, because "untrusted compute node" is not a
hypothetical in FlashML — it is the product. A volunteer's machine must never
hold a credential that reads more than the shards it was assigned.

---

## 4. The crux: fleet-shaped chunks vs data-shaped shards

Concretely. A user prepares 512 WebDataset shards. Seven machines are online at
submit time. `fleet_shape(7)` returns `total_chunks = 7`.

Today the task receives `--num-shards 7 --shard 3` — two integers with no
relationship to the 512. The user's code must invent the mapping, and every
user invents a different one.

**The fix is to stop handing the task integers and start handing it a manifest
slice.** FlashML knows the manifest length (it read the manifest); it knows the
chunk count (it computed the fleet shape). The mapping is arithmetic it should
do once, centrally, rather than 512 users doing it 512 ways:

```
chunk c of C, over a manifest of S shards
  → shards [ c*S//C , (c+1)*S//C )
```

Contiguous, not strided, deliberately: contiguous ranges are what make a host's
cache useful across rounds, and `round_chunk_offset()` (`elastic.py:99`)
already sweeps the offset forward each round so the fleet covers the whole
dataset over an epoch rather than retraining the same prefix.

Two properties fall out for free once the mapping is central:

- **Locality scheduling becomes possible.** The coordinator can prefer a host
  that already holds shards 128–191 for the chunk covering 128–191. That is
  BOINC's twenty-year-old lesson, and it is the difference between a fleet that
  re-downloads a dataset every round and one that downloads it once.
- **Resumption is exact.** A chunk is a named range, not "wherever that machine
  got to". A dead machine's chunk is reassigned whole.

---

## 5. Recommended strategy

**FlashML never stores datasets. It stores a manifest, mints short-lived
access, schedules for locality, and caches on the host.**

Three tiers, and a user picks by what they have rather than by reading docs:

### Tier 0 — in the repo (exists today)

≤32 MiB, committed. Correct for a config, a small CSV, a lookup table. No work
needed, and the docs should say plainly that this is the ceiling so nobody
discovers it by hitting it.

### Tier 1 — a declared remote dataset (the proposal)

```yaml
datasets:
  - name: imagenet-subset
    manifest: https://huggingface.co/datasets/me/mine/resolve/main/manifest.json
```

- The manifest is a **list of addressable shards** with sizes and checksums —
  WebDataset tar, MDS, or parquet row groups all reduce to this.
- The **host agent** fetches its assigned shards before the sandbox closes.
  The task keeps `--network none` and sees files at `/work/data/<name>/`.
- **The user hosts the bytes.** Public data → HuggingFace (free, CDN, range
  requests). Private data → their own S3/R2/GCS bucket with a scoped read-only
  key held by FlashML and exchanged for **presigned URLs valid minutes, scoped
  to the shards that host was assigned**.
- The agent caches shards under the same budget-and-LRU discipline
  `EnvironmentCache` already implements, and refuses rather than fills a
  volunteer's disk.
- The coordinator prefers hosts whose cache already covers the chunk.

### Tier 2 — `local_inputs` (exists, blocked)

Data that must never move: the host advertises a label, the agent bind-mounts
it read-only, nothing crosses the network. This is the genuinely federated
case and it is already designed. It needs the security-audit blocker cleared,
not a redesign.

### What the user is told to do

> Small enough to commit? Commit it. Public? Push it to HuggingFace and give us
> the URL. Private? Put it in a bucket and give us a read-only key. Can't move
> at all? Host the machine yourself and use `local_inputs`.

Four sentences, and each maps to a tier.

---

## 6. Why not the alternatives

- **Host datasets on Supabase.** Free tier is 500 MB–1 GB total, it is a
  relational store, and `ROADMAP.md` P2.5 already establishes that Supabase
  holds "only small relational rows". Not a close call.
- **Host datasets on the coordinator disk.** Artifacts already share one disk
  across every workspace, and `_storage_gate` exists because a full disk stops
  the coordinator and with it *every other workspace's running jobs*. Adding
  multi-GB datasets to that disk turns one user's dataset into everyone's
  outage. The 2 GiB per-account budget is not a number to raise; it is a number
  that exists for this reason.
- **Let the task download its own data.** Requires dropping `--network none`.
  That is the single control that makes the volunteer story defensible.
  Non-starter.
- **A FlashML-operated dataset CDN.** A real product someday, a distraction
  now, and it converts a zero-marginal-cost business into a storage business
  before there is revenue.
- **Client-to-client (BitTorrent-style) shard transfer.** BOINC explicitly
  lacks this and has survived; it adds NAT traversal, a peer protocol, and a
  new abuse surface. Revisit only if bandwidth to the origin becomes the
  measured bottleneck.

---

## 7. What to build, in order

Nothing here is scheduled — this is the shape a plan would take.

1. **Write the `datasets:` spec.** Manifest schema, cache semantics, the
   chunk→shard mapping, and the auth story. Depends on the
   dependency-provisioning plans landing first, because the host-fetches-before-
   the-sandbox seam is the same seam and should not be built twice.
2. **Clear the `local_inputs` security blocker.** It is the only real-data path
   that exists and it cannot ship.
3. **Move the chunk→shard mapping into FlashML** and stop handing tasks bare
   integers. This is worth doing on its own merits even before datasets exist:
   the current contract makes every user reinvent striding, and
   `flashml-examples`' own federated job got it wrong (it reported no
   `chunks_done` at all and was worth nothing).
4. **`DatasetCache` in flashnode**, modelled directly on `EnvironmentCache` —
   same budget variable shape, same LRU, same disk-reserve refusal.
5. **Locality scheduling in the coordinator** — prefer hosts whose cache covers
   the chunk. Last, because it is an optimisation and steps 1–4 are correctness.

---

## 8. Open questions for the owner

1. **Does FlashML hold users' storage credentials at all?** Presigned-URL
   minting requires it. The alternative — the user pastes a presigned URL per
   run — is safe but unusable for a job that runs for hours. This is the same
   shape of question the GitHub App decision (§6.2) answered by waiting.
2. **What is the per-host dataset cache budget by default?** `FLASHNODE_ENV_BUDGET_GB`
   has an answer for venvs; datasets are an order of magnitude larger and the
   host owner is lending their disk.
3. **Do we verify shard checksums?** The manifest can carry them cheaply. It is
   the only defence against a mid-run dataset mutation silently changing what
   the model trained on.
4. **Is a "prepare my dataset" tool in scope?** Converting a folder of images
   into WebDataset shards is the step most users will get wrong, and
   `flashml datasets prepare` is a natural CLI verb — but it is a data
   engineering product, and this workspace has non-goals about growing those.

---

## Sources

- [BOINC WorkDistribution — locality scheduling and sticky files](https://github.com/BOINC/boinc/wiki/WorkDistribution)
- [BOINC: A Platform for Volunteer Computing (Anderson)](https://arxiv.org/pdf/1903.01699)
- [INTELLECT-1 Technical Report](https://arxiv.org/pdf/2412.01152)
- [INTELLECT-2: globally decentralized RL training](https://www.primeintellect.ai/blog/intellect-2)
- [Epoch AI — How far can decentralized training over the internet scale?](https://epoch.ai/gradient-updates/how-far-can-decentralized-training-over-the-internet-scale)
- [MosaicML StreamingDataset — main concepts](https://docs.mosaicml.com/projects/streaming/en/stable/getting_started/main_concepts.html)
- [MosaicML StreamingDataset — Databricks blog](https://www.databricks.com/blog/mosaicml-streamingdataset)
- [WebDataset / Efficient PyTorch I/O for large datasets](https://pytorch.org/blog/efficient-pytorch-io-library-for-large-datasets-many-files-many-gpus/)
- [Hugging Face — streaming datasets](https://huggingface.co/docs/hub/en/datasets-streaming)
- [Hugging Face — Parquet in action](https://huggingface.co/blog/cfahlgren1/intro-to-parquet-format)
- [AWS — presigned URLs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url)
- [Cloudflare R2 — presigned URLs](https://developers.cloudflare.com/r2/api/s3/presigned-urls/)
- [RunPod × SkyPilot integration](https://www.runpod.io/blog/runpod-skypilot-integration)
