# Declared datasets — the host fetches, the fleet agrees, and we store no bytes and no keys

**Date:** 2026-08-10
**Status:** approved design (brainstormed with the owner).
**Repos touched:** `flashml` (protocol, agent), `flashml-cloud` (compiler, API).
**Research input:** `docs/research/2026-08-10-dataset-access-for-volunteer-compute.md`
(architecture) and `docs/research/2026-08-10-dataset-hosting-options-and-credential-design.md`
(hosting, egress, credentials). Numbers quoted here are sourced there.
**Roadmap item:** `ROADMAP.md` P1.7, second half. Depends on the dependency
provisioning work landing first — same staging seam, built once.

**Origin.** A model needs data. Today a task gets bytes from three places and
only one works for real data: committed in the repo (32 MiB ceiling),
synthesised at runtime (demos), or `local_inputs` (only when the person with
the data also owns the machine). Everything else has no path, because a task
runs `--network none` and preflight treats a networking import as an error.
That constraint is most of what makes hosting a stranger's job defensible and
nothing here weakens it.

Three owner decisions on 2026-08-10 shaped the scope:

1. *Public-only for v1.* The private path needs FlashML to hold a user's
   storage credential. `0013_github_installations.sql` says **"No token, no
   refresh token, no OAuth grant"**, and every token column in the schema is a
   sha256 of a token *we issued* — the API has never stored a third-party
   credential. Reversing that is a security decision, not a data-plumbing one,
   and it gets its own spec.
2. *All four URI schemes.* Not a contradiction with (1), and this is the
   insight that shapes the document: **the scheme is addressing, the credential
   is authorization, and they are orthogonal.** v1 ships complete addressing
   and empty authorization. The private spec later adds auth to schemes that
   already exist, instead of adding both at once and coupling them forever.
3. *Both `split:` modes.* `replica` is a few lines in the mapper and a
   different *shape* of admission check. Cheap now, expensive to retrofit.

---

## 1. Decisions

1. **FlashML stores no dataset bytes.** Not in Supabase, not on the
   coordinator disk. The user hosts the data; we hold a manifest measured in
   kilobytes. The coordinator's shared disk exists for artifacts and
   `_storage_gate` protects it; a multi-GB dataset there turns one user's data
   into every workspace's outage.
2. **The host agent fetches, direct to origin.** Never through
   `/v1alpha1/artifacts/{key}`. Dataset bytes never touch our infrastructure —
   which is what keeps the marginal cost of a job at zero.
3. **The task still has no network.** `--network none` is untouched. The task
   sees files under `/work/data/<name>/`. It never sees a URL, a token or a
   socket.
4. **FlashML derives the manifest; the user does not write one.** For `hf://`
   the tree API yields path + size + sha256. For `s3://`/`r2://`,
   `ListObjectsV2` yields path + size + ETag. A hand-written manifest is the
   escape hatch (`https://`), not the interface.
5. **Every run is pinned to an immutable revision.** `hf://` resolves `main` →
   commit SHA at submit time. Object stores record per-object ETags. A dataset
   that changes mid-run is a class of bug we refuse to have.
6. **The chunk→shard map is central and byte-weighted.** Tasks stop receiving
   bare `--num-shards K --shard N` integers and receive a manifest slice.
   Contiguous ranges split on cumulative *bytes*, not file count.
7. **Admission happens at submit time.** We know the manifest bytes and the
   hosts' advertised cache capacity. A job that cannot fit is refused in the
   console in one second, not after twenty machines download for forty minutes.
8. **A volunteer's disk is bounded, visible and advertised.**
   `DatasetCache` mirrors `EnvironmentCache`: separate budget, LRU eviction,
   refusal before a fetch that would exhaust the disk. The budget is a
   registration field, so it gates placement and feeds admission.
9. **v1 is public origins only.** No credential is stored, sent or minted.
   Private data keeps `local_inputs` until the private spec lands.

---

## 2. What the user writes

```yaml
datasets:
  - name: imdb
    source: hf://stanfordnlp/imdb          # required
    select: "plain_text/train-*.parquet"   # optional glob; default: every file
    split: shard                           # shard | replica; default shard
```

One required line. `name` is the directory the task sees at
`/work/data/<name>/`, and must be a plain identifier — it becomes a path
component on someone else's machine, so it is validated as strictly as archive
members are in `archives.py`.

Multiple datasets per job are allowed. Each resolves and caches independently.

---

## 3. Four schemes, one resolution path

The scheme decides *how we list and address*, nothing else.

| Scheme | Listing | Size | Integrity token | Revision pin |
|---|---|---|---|---|
| `hf://ns/name[@rev]` | tree API | yes | **sha256** (`lfs.oid`, `x-linked-etag`) | commit SHA |
| `s3://bucket/prefix` | `ListObjectsV2` | yes | ETag (**weak** — see §4) | per-object ETag set |
| `r2://account/bucket/prefix` | `ListObjectsV2` | yes | ETag (**weak**) | per-object ETag set |
| `https://…/manifest.json` | the manifest itself | declared | **whatever the user declares** | manifest digest |

All four are reachable anonymously in v1. Concretely that means public HF
dataset repos, public S3 buckets (the AWS Open Data pattern), R2 buckets with a
public dev URL or custom domain, and any static HTTP host.

**Gated HF repos are not public.** They 401 without a token and are refused at
submit with that reason, rather than failing on thirty machines at once.

### Why `hf://` is the recommended default

It is the only origin that hands us everything for free: a manifest with real
sha256 checksums, an immutable commit SHA to pin, `accept-ranges: bytes`, a
global CDN, **zero egress cost**, and anonymous rate limits applied **per IP**
— so a distributed fleet is structurally advantaged rather than throttled. The
docs should say so plainly.

The counterweight, which the docs must also say: HF's public storage is a
commons with a stated reuse expectation (*"If you plan to upload a dataset you
anticipate won't have any further reuse, other platforms are likely more
suitable"*). We must never advise pushing private working data to a public HF
repo as a workaround. That is free-riding, it violates their terms, and it
breaks the day they enforce.

---

## 4. Integrity is not equal across schemes, and we say so

This is the part most likely to be quietly fudged, so it is stated up front.

- **sha256 (`hf://`)** — a real content hash. Verify after fetch, fail the task
  on mismatch.
- **ETag (`s3://`, `r2://`)** — MD5 for single-part uploads, but
  `<md5-of-md5s>-<partcount>` for multipart. It is **not** a content hash and
  must not be presented as one. It is a *change-detection token*: sufficient to
  pin a revision and detect that an object moved under us, insufficient to
  prove two hosts fetched the same bytes.
- **Declared (`https://`)** — exactly as trustworthy as the manifest author,
  who is the submitter. Fine, since the submitter is who the data is for.

So the manifest carries a typed integrity field, not a bare string:

```json
{"path": "plain_text/train-00000-of-00001.parquet",
 "size": 20979968,
 "integrity": {"kind": "sha256", "value": "db47d16b…"}}
```

`kind` ∈ `sha256 | etag | declared-sha256 | none`. The console renders the
weaker kinds honestly, and provenance records which one was used. A job whose
integrity is `etag` is still a valid job — it is just a job whose "what did
this train on" answer is weaker, and the person reading the provenance record
deserves to know that rather than infer a guarantee we did not provide.

---

## 5. Resolution, at submit time, in the compiler

1. **Expand** `source` + `select` → the file list.
2. **Pin** the revision. `hf://` without `@rev` resolves `main` → commit SHA
   and the pinned SHA is recorded; object stores snapshot their ETag set.
3. **Build the manifest** — an ordered list of `{path, size, integrity}`,
   sorted by path so the ordering is deterministic across resubmissions.
4. **Store the manifest** as an ordinary artifact. It is JSON in the kilobytes
   and does not meaningfully touch the 2 GiB per-account budget.
5. **Admit or refuse** (§7).

Resolution is the only network call the control plane makes on this path, it
happens once per submit, and it is against a public endpoint with no
credential. If the origin is unreachable the job is refused at submit — which
is the correct place to discover that a dataset URL is wrong.

---

## 6. `split:`, and the mapping

### 6.1 The two modes

| `split` | Each host gets | Bytes per host | For |
|---|---|---|---|
| `shard` (default) | a disjoint, contiguous, byte-balanced slice | `total / chunks` | federated / data-parallel training; the union over the fleet is one epoch |
| `replica` | the whole dataset | `total` | hyperparameter sweeps, evaluation, embarrassingly-parallel jobs where every worker needs everything |

These are not cosmetic variants. They change the **shape** of the admission
check from "can the fleet hold this between them" to "can *every* machine hold
this", which is why both ship now rather than `replica` being retrofitted onto
an admission path that assumed division.

On FSDP, since it comes up: FSDP shards the *model* and needs an all-gather per
layer per step. It does not survive the public internet between hosts, and
Prime Intellect's INTELLECT-1/2 confirm the shape that does — FSDP inside one
host, low-communication averaging between hosts, which is what FedAvg already
is. So this is a DDP/data-sharding problem. FSDP stays the host's business
inside one multi-GPU box and needs nothing from this design.

### 6.2 Byte-weighted contiguous ranges

`fleet_shape()` sets `total_chunks` from machines online, capped at 100. A
dataset has a fixed natural shard count. The mapping is arithmetic FlashML does
once, centrally, rather than every user reinventing striding — which the
`flashml-examples` federated job already got wrong badly enough to report no
`chunks_done` at all and be credited as nothing.

Chunk `c` of `C` gets the contiguous run of manifest entries whose cumulative
byte offsets fall in `[c·T/C, (c+1)·T/C)` where `T` is total bytes.

**Byte-weighted, not count-weighted.** Real shards are uneven; dividing by file
count hands one host 10× the work and stalls the round on the straggler. The
manifest carries sizes precisely so this is free.

**Contiguous, not strided**, for two reasons: contiguous ranges are what make a
host's cache useful across rounds, and `round_chunk_offset()` already sweeps
the window forward each round so the fleet covers an epoch rather than
retraining the same prefix.

A chunk is therefore a *named range*, which is what makes reassignment exact: a
dead machine's chunk is re-fetched whole by its replacement, not resumed from
wherever the corpse got to.

### 6.3 Granularity belongs to the dataset, not the fleet

**For `split: shard`, `total_chunks = min(fleet_chunks, len(manifest))`.**

`fleet_shape()` sizes a round from machines online, capped at 100. Nothing
today stops that exceeding the number of shards a dataset actually has, and
when it does the surplus chunks are *empty* — those hosts fetch nothing, train
on nothing and report nothing. The partition is still perfectly valid, which is
exactly why this is easy to miss.

It is not a smaller round. It is a silently different experiment: FedAvg
averages over members whose gradients do not exist, and the run looks healthy
while a fraction of the fleet contributed nothing. Capping is the whole fix,
and it is one line at the place that already caps at 100.

Measured on a real repo — `stanfordnlp/imdb` has 3 shards, and at `C=7` the
mapper leaves **4 of 7 chunks empty** (probe check A6b, §11). This is also the
strongest argument for `flashml datasets prepare` eventually existing: a
3-shard dataset cannot use a 20-machine fleet no matter how good the scheduler
is, and the fix is more shards, which is a preparation-time decision.

The console should say so when it bites — *"this dataset has 3 shards, so at
most 3 machines can work on it"* — rather than silently running a 3-machine
round on a 20-machine pool.

`split: replica` is unaffected: every chunk gets everything, so there is
nothing to run out of.

---

## 7. Admission, at submit time

```
bytes_per_host = total_bytes / chunks   (shard)
bytes_per_host = total_bytes            (replica)
```

Refuse the job at submit unless enough hosts in the target pool advertise a
dataset cache budget ≥ `bytes_per_host`. The refusal names the two numbers.

`replica` on a large dataset will refuse where `shard` admits, and that is the
feature: discovering it in the console beats discovering it thirty times in
parallel on other people's laptops.

This check is advisory about the *future* — hosts come and go, and a host that
qualified at submit may be gone by round three. It is a cheap filter against
the obviously impossible, not a guarantee, and the spec does not pretend
otherwise. Runtime refusal (§8.2) remains the backstop.

---

## 8. The host side

### 8.1 A new registration field

`NodeRegistration.dataset_cache_gb: float`, advertised at registration, the
same shape as `gpus`, `local_datasets`, `pools` and
`can_install_dependencies`. It is simultaneously:

- the **placement gate** — a task whose slice exceeds it does not place there,
- the **admission input** for §7,
- the host owner's **consent surface**, visible and settable.

Default **16.0 GB**, from `FLASHNODE_DATA_BUDGET_GB`. `0` disables dataset
work on that machine entirely.

This is the one number in the spec I chose rather than asked about, so the
reasoning is on the record: `EnvironmentCache` defaults to 8 GB without asking,
and a host that has accepted a job has accepted that the job needs inputs.
Datasets are an order of magnitude larger than venvs, so they get their own
budget rather than sharing one — a host owner should be able to say yes to 8 GB
of virtualenvs and no to 200 GB of a stranger's images. The real protection
against a full disk is not the budget but the free-disk floor that
`_refuse_if_disk_is_short` already enforces, and that is inherited unchanged.
Revisit the default after the first real fleet measurement.

### 8.2 `DatasetCache`

Modelled directly on `flashnode/executor/environments.py`, whose docstring
already states the governing principle: *"Disk is somebody else's."*

- **Keyed by integrity value, not URL.** Content-addressed, so two jobs using
  the same public dataset — and two revisions sharing unchanged shards — share
  cache entries for free. Entries with `kind: none` are keyed by
  `(source, path, etag)` and are the only ones that cannot be shared.
- **Bounded by `FLASHNODE_DATA_BUDGET_GB`**, separate from the env budget.
- **LRU eviction**, with the same fix `EnvironmentCache` needed: touch on *use*,
  not on write, or the cache evicts its most recently used entry.
- **Refuses before fetching** when the disk is short, rather than filling it.
- **`flashnode data` subcommand** — list, sizes, ages, purge. Consuming a
  stranger's disk invisibly is not acceptable even when they consented to run
  the code. The same reasoning that produced `flashnode env`.

### 8.3 Fetch

Before the sandbox closes, on the same seam that already pulls the code
tarball, container images and pip dependencies:

1. For each assigned manifest entry, check the cache by integrity value.
2. Miss → fetch from origin with ranged, resumable HTTP. Retry with backoff;
   honour `RateLimit` headers on 429 rather than hammering.
3. Verify integrity per §4. Mismatch → **fail the task, never the node**. A
   hostile or broken origin costs the submitter their job, not the volunteer
   their machine — the polarity `archives.py` already establishes.
4. Materialise as read-only files under `/work/data/<name>/`, preserving
   relative paths from the manifest.

Fetching needs no new host *capability* — unlike installing dependencies, every
tier's agent has network access before the sandbox closes, so this works on
docker, argv, subprocess and trusted alike. Capacity, not capability, is the
gate, and that is what `dataset_cache_gb` expresses.

---

## 9. Protocol and payload

Hard rule 2: any schema a FlashNode must understand belongs in
`flashruntime.protocol`, contributed there first. This design adds:

- `NodeRegistration.dataset_cache_gb: float` (default `0.0` on the wire so a
  node predating the field is never sent dataset work — fail closed, the same
  polarity as `can_install_dependencies`).
- A `DatasetSlice` in the task payload: `{name, split, entries: [{path, size,
  integrity, url}]}`. The URL is a plain public URL in v1; the field exists
  from the start so the private spec can substitute an expiring capability
  without a protocol change.
- The existing `--num-shards`/`--shard` integers stay for jobs with no
  `datasets:` block. They are not removed; they are superseded for jobs that
  declare data.

Because the pin is a published `flashruntime` version, this lands as: merge →
release to PyPI → bump all four pin sites together.

---

## 10. Provenance

Record the pinned revision, the manifest digest, and the integrity `kind` per
dataset, feeding `2026-08-02-provenance-and-local-data-design.md`. "What did
this model train on" should answer with a hash and a commit SHA, not a URL that
resolved to something once.

---

## 11. Evidence

Every assumption this spec makes about Hugging Face is measured, not read off a
docs page, by `scripts/experiments/hf_dataset_origin_probe.py` — stdlib only,
no venv, re-runnable by anyone:

```console
$ python3 scripts/experiments/hf_dataset_origin_probe.py
9 passed, 0 failed, 0 skipped      # 2026-08-10, stanfordnlp/imdb
```

| Check | Gates | Result |
|---|---|---|
| A1 | manifest is derived (decision 4) | tree API gives path + size + sha256 for every file |
| A2 | revision pinning (decision 5) | `main` → `e6281661…` |
| A3 | integrity cross-check (§4) | `X-Linked-ETag` == tree API `lfs.oid` — two independent sources agree |
| A3b | capability shape | signed CDN URL, **~60 min TTL, not IP-bound** |
| A4 | ranged fetch (§8.3) | `206`, exact byte count |
| A5 | integrity is real (§4) | 20.5 MB downloaded anonymously, sha256 matches |
| A6 | chunk mapping (§6.2) | exact partition at every C, on real uneven shard sizes |
| A6b | **§6.3, which this check produced** | 3 shards at C=7 leaves 4 chunks empty |
| A7 | backoff (§8.3) | `RateLimit-Policy: "fixed window";"resolvers";q=3000;w=300` |
| A8 | gated detection (§3) | gated repo `401`s anonymously |

Two of these earned their keep immediately. **A3** initially failed and the
cause was the probe reading `X-Linked-Etag` when HTTP/1.1 sends
`X-Linked-ETag` — a case-sensitivity bug that would have shipped into the agent
as "HF sometimes omits the checksum" and quietly disabled verification.
**A6b did not exist** until A6's output was read carefully; §6.3 is its
consequence.

Stage B of the same script probes the private broker and is **not run**. It
gates the private-dataset spec, not this one.

---

## 12. Deferred

- **Private datasets and credential custody.** Its own spec, its own security
  review. Blocked on Stage B of the §11 probe and on an owner decision about
  storing a third-party credential for the first time.
- **Locality scheduling** — preferring hosts whose cache already covers a
  chunk. BOINC's twenty-year-old lesson and worth up to 10× on egress, but it
  is an optimisation and steps 1–8 here are correctness. Deferred, not
  dismissed; the byte-weighted contiguous mapping in §6.2 is what makes it
  possible later.
- **Row-group / column ranges.** Parquet supports fetching only the row groups
  and columns you need. Real, and it interacts with rate limits (many small
  ranged requests instead of one shard fetch). Whole-shard fetching first.
- **`flashml datasets prepare`** — converting a folder of images into
  WebDataset shards is the step most users will get wrong, and HF *requires*
  Parquet/WebDataset shapes for large datasets, so somebody does this work
  either way, and §6.3 sharpens the case: a 3-shard dataset cannot use a
  20-machine fleet, and only re-sharding fixes that. It is also a data
  engineering product this workspace has non-goals about growing. Separate
  decision.
- **Torrent / webseed distribution.** The right *shape* for N machines wanting
  identical bytes, and the manifest here is already a list of hashed fixed
  pieces, so it stays compatible. Revisit when origin bandwidth is a *measured*
  bottleneck. BOINC has survived twenty years without peer transfer.
- **HF Storage Buckets** as an origin. S3-compatible and Xet-backed, but it
  supports no ACLs, no bucket policies and no documented presigning, so the
  narrowest credential it can issue is repo-wide. Irrelevant for public v1;
  re-evaluate for the private spec if the `s3.hf.co` presigning side-check
  (research note §11) says otherwise.

---

## 13. Rejected

- **Host datasets on Supabase.** 500 MB–1 GB free tier, relational store,
  and `ROADMAP.md` P2.5 already establishes it holds only small relational
  rows. Not a close call.
- **Host datasets on the coordinator disk.** One shared disk across every
  workspace, and `_storage_gate` exists because filling it stops the
  coordinator and every running job in every other workspace.
- **Proxy dataset bytes through the coordinator.** Same disk, plus it puts our
  bandwidth in the data path and converts a zero-marginal-cost business into a
  CDN business.
- **Let the task download its own data.** Requires dropping `--network none`,
  the single control that makes the volunteer story defensible.
- **Git LFS as an origin.** GitHub gives 10 GiB of LFS bandwidth per month. A
  200 GB dataset across 20 hosts exceeds a month's allowance in the first round
  by 20×, and ten rounds overruns by 200×. Add no range requests and a 2 GB
  per-file cap. It is the wrong shape, not merely the wrong price.
- **User-written manifests as the primary interface.** Every origin except
  `https://` can be listed programmatically. Making users hand-write what we
  can derive is how checksums get omitted and sizes get wrong.
- **Count-weighted shard division.** Uneven shard sizes hand one host 10× the
  bytes and every round waits on it.
- **A FlashML-operated dataset CDN.** A real product someday; today it converts
  a zero-marginal-cost business into a storage business before there is
  revenue.

---

## 14. Amendments after reading the code (2026-08-11)

Four assumptions in §1–§9 did not survive contact with the source. Recorded
here rather than edited in place, so the reasoning stays auditable.

**14.1 There is no typed task payload.** §9 proposed a `DatasetSlice` model in
`flashruntime.protocol`. Wrong shape: `TaskSpec.payload` is
`dict[str, Any]`, assembled in `CommandRecipe.expand`, and every existing
feature — `local_inputs`, `dependencies`, `gpus`, `pool` — travels as a plain
payload key validated in `recipes/command.py`. Datasets follow that pattern.
The only genuine *protocol* addition is one capabilities field.

**14.2 The capacity field is `NodeCapabilities.dataset_cache_bytes: int = 0`,**
not `NodeRegistration.dataset_cache_gb`. `NodeCapabilities` is where "what this
machine has" lives (`cpu_cores`, `memory_bytes`, `gpus`), and the house spells
capacities in bytes. Defaults to `0` — fail closed, so an agent predating the
field advertises no capacity and is never sent dataset work.

**14.3 Per-task payload variation does not exist today, and datasets need it.**
Every task in a job gets the same `local_inputs`/`dependencies`; only argv
varies, through `task_params[i]` + `str.format`. But each task needs a
*different* slice. Resolution: the compiler emits
`parameters["dataset_slices"]`, a list indexed by task, and `expand()`
forwards `payload["datasets"] = dataset_slices[i]`. `dataset_slices` is opaque
to flashruntime — it forwards, it does not compute.

This keeps the byte-weighted mapper in **one** place, `flashml-cloud`'s
`elastic.py`, beside `fleet_shape` and `round_chunk_offset`. §6.2 implied the
mapper was runtime-side; `elastic.py` is in flashml-cloud, not flashruntime.
A second implementation in the agent is exactly the drift `elastic.py`'s own
docstring warns about.

**14.4 Dataset jobs KEEP `--shard` and `--num-shards`.** §1 decision 6 said
tasks "stop receiving bare integers". Taken literally that is a silent-failure
bug: the documented federated contract requires the worker to report
`chunks_done: [args.shard]`, and *"a contribution reporting none is averaged
in with zero weight"*. Removing the integers would zero the credit of every
federated dataset job while everything still looked healthy. The integers stay
and keep their meaning (chunk identity for accounting); the files at
`/work/data/<name>/` are what change.

**14.5 Two owner decisions (2026-08-11).**

- **`split:` is inferred, overridable.** `mode: federated` → `shard`;
  `sweep`/`independent` → `replica`. An explicit `split:` wins. The console
  shows what was inferred, since the behaviour is otherwise invisible.
- **Under-sharding warns, it does not refuse.** A dataset with fewer shards
  than the fleet has machines caps `total_chunks` at the shard count and says
  so plainly: *"this dataset has 3 shards, so at most 3 of your 20 machines can
  work on it."* This is the same complaint that killed the `shards:` knob
  (`REMOVED_FEDERATED_KEYS`) — the difference is that it is now visible instead
  of eight machines silently idling.

**14.6 Two engineering hazards the mirror must not inherit.**

- `EnvironmentCache._dir_size` is O(files) and runs for every entry on every
  `entries()` call. Fine for a handful of venvs; unacceptable for a dataset of
  a million files. `DatasetCache` writes a `size` marker at publish time.
- `client.download_artifact` does `resp.read()` into memory and must never be
  reused for dataset bytes. Dataset fetch streams via `shutil.copyfileobj`, and
  goes direct to origin — never through `_request`, which would attach the
  coordinator bearer token to a third-party host.
