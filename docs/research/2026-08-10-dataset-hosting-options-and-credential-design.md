# Where the bytes physically live — hosting options and the credential design

**Date:** 2026-08-10
**Status:** research note + design proposal. §9 is a design awaiting owner review.
**Companion to:** `2026-08-10-dataset-access-for-volunteer-compute.md`, written
earlier the same day. That note answered *who fetches, on whose authority, and
how the fleet agrees on which machine gets which part.* It left the physical
hosting question at "the user hosts their own bytes" and listed four open
questions. This note answers the hosting question with numbers, and closes two
of the four.
**Roadmap item:** `ROADMAP.md` P1.7, second half.

---

## 0. What changed since the morning note

Four things, three of them verified against the live services rather than the
docs. They move the design materially.

1. **Hugging Face charges nothing for egress, ever.** The pricing page states
   it flatly: *"Egress and CDN included at no extra cost."* This is not true of
   S3, and it is the single largest economic fact in this note.
2. **HF hands out a shard manifest for free.** The tree API returns per-file
   path, byte size and sha256 for every file in a dataset repo. The user does
   not have to write a manifest; FlashML can build one from a repo name.
   *Verified* — output in §3.5.
3. **HF's own `resolve` endpoint already mints short-lived signed URLs.** A
   resolve request 302s to a CDN edge with a **60-minute**, single-resource,
   **no-IP-condition** signed URL. That is a presigned-URL broker, working
   today, for free. *Verified* — decoded policy in §3.7. It is the mechanism
   that answers open question #1 from the morning note.
4. **HF shipped Storage Buckets** — S3-compatible object storage on the Hub,
   at `s3.hf.co`. Promising, and **not usable for our private-data case**, for
   a specific reason (§3.8).

The net: Hugging Face is a far better fit than I credited this morning, but
only for public data and only if we respect what it is. For private data the
answer is Cloudflare R2, and the reason is not price.

---

## 1. The frame: three questions, not one

"Where do we host it" collapses three independent questions that have
different answers. Every option below is scored on all three.

| | The question | Why it bites on volunteer compute |
|---|---|---|
| **Capacity** | Will it hold the data, and at what price? | The boring one. Usually solvable with money. |
| **Access control** | Can I say *"this machine, these shards, for the next 40 minutes"*? | The hard one. A volunteer's laptop is an untrusted host. A credential that reads the whole dataset forever is a credential we have handed to a stranger. |
| **Egress** | Who pays when 30 machines each pull 10 GB, ten rounds running? | The one that silently kills the unit economics. FlashML's pitch is zero marginal cost. |

Capacity is where every comparison article stops. Access control is where the
design actually lives, and it is the axis on which the options separate most
sharply.

---

## 2. What FlashML is asking the store to do

Concretely, so the evaluation is not abstract. A federated run:

- **N hosts, disjoint slices.** 20 machines, a 200 GB dataset, each pulls ~10 GB.
- **Rounds sweep the offset.** `round_chunk_offset()` moves the window each
  round so the fleet covers an epoch. Without a cache and locality scheduling,
  10 rounds re-pull the dataset 10 times — **2 TB of egress for one job.**
- **Hosts die mid-round.** Reassignment must re-fetch a *named range*, not
  resume a stream.
- **The task has no network.** `--network none`, enforced, and preflight treats
  a networking import as an error. Only the host agent fetches. This does not
  change.
- **The origin is user-controlled and mutable.** An HF repo can be
  force-pushed; a bucket object can be overwritten. Two hosts training on
  different bytes for the same declared shard produces an averaged model that
  is silently wrong.

So the store must support: **range requests**, **content addressing or a
stable revision**, **cheap listing with sizes**, **many concurrent readers from
many IPs**, and ideally **per-object, per-host, expiring credentials**.

---

## 3. Hugging Face, in detail

Taking the owner's questions in order.

### 3.1 Can a dataset be private? Yes, and the free tier is real

| Account | Public storage | Private storage |
|---|---|---|
| Free user or org | Best-effort\* | **100 GB** |
| PRO ($9/mo) | 10 TB included + add-on | 1 TB + pay-as-you-go |
| Team org | 12 TB + 1 TB/seat | 1 TB **per seat** |
| Enterprise | 200 TB + 1 TB/seat | 1 TB per seat |

Add-on public storage is $12/TB/mo, falling to $10/TB/mo at 50 TB. Private
overage is $18/TB/mo, falling to $12/TB/mo at 500 TB+.

100 GB of free private storage is more than most of our early users will have.
For the tester cohort, HF private repos are a genuinely sufficient answer.

### 3.2 Can you put a large dataset on it? Yes — with a social contract attached

There is **no per-repo size limit**. The technical limits are shape limits:

| Characteristic | Limit |
|---|---|
| Files per repo | <100k recommended |
| Entries per folder | **10k hard cap** |
| File size | <200 GB recommended, **500 GB hard** |
| Files per commit | ~50–100 (60 s HTTP timeout per commit) |
| Commits per repo | degrades past a few thousand |

The constraint that actually matters is not technical. HF's own words on
public storage:

> *"Beyond the first few gigabytes, please use this resource responsibly by
> uploading content that offers genuine value to other users."*

and, for large datasets, they **require** a dataset card, adherence to the repo
limits, HF-native formats (Parquet/WebDataset), and:

> *"You are sharing the dataset to enable community reuse. If you plan to
> upload a dataset you anticipate won't have any further reuse, other platforms
> are likely more suitable."*

**This is a real product constraint, not boilerplate.** It means FlashML must
never tell a user "just push it to HF" as a generic workaround for private
working data. Public HF is a commons; using it as free CDN for one-off training
scratch is free-riding, it violates the stated terms, and it breaks the day HF
enforces. Our docs must say *public data → HF, private data → your bucket*, and
mean it.

### 3.3 Rate limits — the fleet question

5-minute fixed windows. The `resolvers` bucket is what file downloads use.

| Plan | API | **Resolvers** | Pages |
|---|---|---|---|
| Anonymous (**per IP**) | 500 | **3,000** | 100 |
| Free user | 1,000 | **5,000** | 200 |
| PRO | 2,500 | 12,000 | 400 |
| Team org | 3,000 | 20,000 | 400 |
| Enterprise | 6,000 | 50,000 | 600 |

Two consequences, and they point in opposite directions:

- **Public data scales beautifully.** Anonymous limits are **per IP address**.
  Thirty volunteers on thirty home connections get 3,000 resolver requests each
  per 5 minutes. At one request per shard, the fleet is nowhere near the limit.
  The load naturally distributes because the *clients* are distributed. This is
  the rare case where volunteer compute is structurally advantaged.
- **Private data shares one quota.** Every host authenticating with the user's
  token draws on that one token's 5,000/5-min. Still comfortable at shard
  granularity — 5,000 shard fetches per 5 minutes is far more than a 30-machine
  fleet generates — but it is a shared, exhaustible resource, and it is a
  reason to fetch whole shards rather than chase row-group ranges.

Guidance for the agent: always send a token when one exists, back off on the
`RateLimit` header (429 responses carry seconds-until-reset), and prefer
`resolve` calls over API calls — resolvers have 5–10× the quota.

### 3.4 Egress: free

> *"Egress and CDN included at no extra cost."*

No bandwidth metering, no per-GB charge, global CloudFront-backed CDN. For a
workload whose defining characteristic is *the same bytes pulled by many
machines in many places*, this is close to ideal, and it is the thing GitHub
LFS gets catastrophically wrong (§4).

### 3.5 HF gives us the manifest for free — verified

The morning note assumed the user writes a manifest. They do not have to. Two
HTTP calls produce a fully checksummed, revision-pinned shard list:

```console
$ curl -s "https://huggingface.co/api/datasets/stanfordnlp/imdb/tree/main/plain_text?recursive=1"
[{"type":"file","size":20470363,
  "lfs":{"oid":"b52e26e2f872d282ffac460bf9770b25ac6f102cda0e6ca7158df98c94e8b3da","size":20470363},
  "xetHash":"c349f42f…","path":"plain_text/test-00000-of-00001.parquet"}, …]

$ curl -s "https://huggingface.co/api/datasets/stanfordnlp/imdb" | jq -r .sha
e6281661ce1c48d982bc483cf8a173c1bbeb5d31
```

`path` + `size` + `lfs.oid` (sha256) per file **is** the manifest schema from
the morning note. The user writes one line of YAML; FlashML derives the rest.

### 3.6 Pin the revision — free correctness

`resolve/main` is mutable. `resolve/<commit-sha>` is immutable. Resolving
`main` → sha **once at submit time** and pinning it for the run removes the
entire class of "the dataset changed under a running job" bugs at zero cost,
and gives provenance something real to record. This should be mandatory, not
optional. It is the one thing HF has that a raw bucket does not.

### 3.7 The credential broker already exists — verified

This is the most useful finding in the note. A resolve request does not serve
bytes; it 302s to a signed CDN URL:

```console
$ curl -sI ".../resolve/e6281661…/plain_text/test-00000-of-00001.parquet"
HTTP/2 302
location: https://us.aws.cdn.hf.co/xet-bridge-us/…?Expires=1786394852&Policy=…&Signature=…
accept-ranges: bytes
x-linked-size: 20470363
x-linked-etag: "b52e26e2f872d282ffac460bf9770b25ac6f102cda0e6ca7158df98c94e8b3da"
```

Decoding the policy:

```json
{"Statement":[{"Resource":"https://us.aws.cdn.hf.co/xet-bridge-us/…",
               "Condition":{"DateLessThan":{"EpochTime":1786394852}}}]}
```

Measured properties:

- **TTL ≈ 60 minutes** from issue.
- **Scoped to exactly one resource.** Not the repo, not a prefix — one object.
- **No IP condition.** The URL is a transferable bearer token.
- **Range requests work through it** — `accept-ranges: bytes`, and a
  `Range: bytes=0-15` returns `206` with exactly 16 bytes.
- **`x-linked-etag` is the sha256**, so integrity is checkable without the
  tree API.

Put together: **the coordinator can hold the user's HF token, call `resolve`
without following the redirect, and hand the volunteer host the `Location`
value.** The host gets a URL that reads one shard for one hour and nothing
else. The token never leaves the control plane.

That is precisely the least-privilege primitive the morning note said we would
need presigned S3 URLs for — and it is available on HF, for free, today.

**Caveat, stated honestly:** all of the above is verified against a *public*
repo, where the signed URL carries `user_id=public`. The mechanism is the same
code path for private repos and the `user_id` field strongly implies the URL is
minted per identity, but **I have not verified it against a private repo** — we
have no private HF dataset to test with. See §11; it is a ten-minute check and
it gates the whole L1 path on HF.

### 3.8 Storage Buckets: interesting, and not the answer here

HF shipped S3-compatible object storage on the Hub. Non-versioned, mutable,
Xet-backed, public or private, addressed via `https://s3.hf.co/<namespace>`,
with CDN pre-warming into AWS/GCP regions. Credentials are AWS-style keys
(`HFAK…`) derived from an HF access token, inheriting that token's permissions.

For our case it fails on one line of its own documentation:

> *"Unsupported features: **ACLs, bucket policies**, object tagging, object
> versioning, lifecycle rules, server-side encryption, and bucket
> notifications."*

No bucket policies and no documented presigning means **the narrowest
credential you can issue is "this token, this bucket, read"** — repo-wide, and
reusable by whoever holds it. That is exactly the credential we must not give a
volunteer. Buckets are a good fit for *our own* artifacts and checkpoints
someday; they are not the private-dataset answer.

(Whether the `s3.hf.co` gateway validates SigV4 query-string presignatures is
undocumented. If it does, this conclusion changes. Worth ten minutes at the
same time as the §11 experiment.)

### 3.9 Hugging Face — scorecard

| | |
|---|---|
| Capacity | Excellent public, 100 GB free private, no per-repo cap |
| **Access control** | **Excellent for public (none needed). Good for private via the resolve broker — pending §11. Bad if we hand out raw tokens.** |
| Egress | Free, CDN, per-IP anonymous limits that favour a distributed fleet |
| Bonus | Manifest + sha256 + immutable revision pin, all from the API |
| Watch out | Public storage is a commons with a stated reuse expectation |

---

## 4. Git LFS: the answer that looks right and is not

The owner asked specifically. It is worth being precise about why not, because
"put it in git" is the intuitive answer and it fails on a number, not a
principle.

**GitHub LFS free/pro: 10 GiB storage and 10 GiB bandwidth per month.**
Per-file cap 2 GB (4 GB Team, 5 GB Enterprise Cloud). The prepaid $5/50 GiB
data packs were discontinued in June 2024 in favour of metered billing.

Run the FlashML scenario from §2 against that: 20 hosts, 200 GB dataset, one
round.

> **The first round of one job exceeds a month of GitHub LFS bandwidth by 20×.**

Ten rounds is 2 TB against a 10 GiB allowance — a 200× overrun, metered. And
LFS has no range requests, no per-object credentials, and a 2 GB file ceiling
that forces awkward splitting.

Git LFS is designed for *a few developers checking out a few large files
occasionally*. FlashML is *many machines pulling many large files
continuously*. It is the wrong shape, not merely the wrong price. Same applies
to GitLab LFS and to committing data directly.

The one place git-shaped data versioning is right: **DVC**, which keeps small
pointer files in git and pushes bytes to an object-storage remote. That is not
"git LFS" — it is object storage with a git-friendly index, and it composes
fine with §5. HF Buckets are documented as a DVC remote.

---

## 5. Object storage: where private data belongs

| | Storage /GB-mo | Egress /GB | Free tier | Scoped temp credentials |
|---|---|---|---|---|
| **Cloudflare R2** | $0.015 | **$0.00** | 10 GB + 10M reads | **Yes — bucket + prefix/object, read-only, TTL ≤ 7 d** |
| Backblaze B2 | $0.005–0.006 | $0.01 (free via Cloudflare) | 10 GB | Application keys: bucket + name-prefix, optional expiry |
| AWS S3 | $0.023 | **$0.09** | — | Presigned URLs (seconds→hours) + STS (≥15 min floor) |

### The R2 primitive is the one we want

Cloudflare's temporary credentials API takes an R2 API token and returns
short-lived SigV4 credentials that are:

- bound to **exactly one bucket**,
- optionally **restricted to specific prefixes or objects**,
- permissioned as **`object-read-only`**,
- expiring after **`ttlSeconds`, up to 604,800 (7 days)**.

Map that onto a FlashML lease and it reads like it was designed for us:

> *bucket = the user's dataset bucket; prefix = the shards this chunk covers;
> permission = object-read-only; TTL = the lease duration.*

That is a strictly better fit than S3 presigned URLs, because it is **one
credential for a range of objects** rather than one URL per object — a chunk
covering 64 shards needs one credential, not 64 signatures. And R2's egress is
zero, which matters when a re-fetch storm is one dead node away.

S3 works and is the most familiar, but $0.09/GB egress means the 2 TB scenario
from §2 costs **$184 per job** — paid by the user, on a platform whose entire
pitch is that compute is free. B2 is the cheapest at rest and fine if the user
already lives there.

**Recommendation for private data: Cloudflare R2**, with S3 and B2 supported
because users arrive with what they have.

---

## 6. The long tail, briefly

Surveyed so the decision is informed, not so we build for them.

- **Zenodo** — 50 GB per record, 100 files per record, one-time increase to
  200 GB on request. Free, DOI-issuing, CERN-backed. Correct for *publishing a
  finished dataset alongside a paper*; wrong as a training origin (no range
  request guarantees, no credentials, not built for repeated bulk pulls).
- **Kaggle** — 200 GB public and 200 GB private per account since the 2025
  increase. Reasonable capacity; the API is not built for many-machine
  concurrent pulls, and there is no scoped credential model. Low priority.
- **Academic Torrents / BitTorrent + webseed (BEP 19)** — genuinely the right
  *shape*: N machines wanting identical bytes is what BitTorrent was invented
  for, and webseed lets an HTTP origin bootstrap the swarm so it works with one
  seeder. The morning note deferred this and that stands — it adds NAT
  traversal, a peer protocol and a new abuse surface, and BOINC has survived
  twenty years without it. **Revisit when origin bandwidth is a measured
  bottleneck, not before.** Worth noting that if we ever do, the manifest
  design in §9 is already torrent-compatible: a list of hashed, fixed pieces.
- **DVC / LakeFS / Oxen** — index layers over object storage. Complementary,
  not alternatives. If a user already uses DVC, their data is already in a
  bucket and §5 applies.
- **Google Drive / Dropbox** — no. Rate-limited, no range guarantees, OAuth
  scopes that cannot express "one folder, read, one hour", and a history of
  breaking automated downloaders.

---

## 7. The comparison, on the three axes

| Option | Capacity | Access control | Egress cost | Verdict |
|---|---|---|---|---|
| **HF public dataset repo** | No cap; commons expectations | None needed — best possible | **Free**, CDN, per-IP limits | **Default for public** |
| **HF private dataset repo** | 100 GB free / 1 TB PRO | Broker via `resolve` (§11 pending); raw token otherwise | **Free** | **Good for small private** |
| **Cloudflare R2** | $0.015/GB-mo | **Prefix-scoped, read-only, TTL-bounded** | **Free** | **Default for private** |
| Backblaze B2 | $0.005/GB-mo | Prefix-scoped app keys | $0.01/GB, free via CF | Support it |
| AWS S3 | $0.023/GB-mo | Presigned per object | **$0.09/GB** | Support it, warn on cost |
| HF Storage Buckets | Hub plans | **No policies, no presigning** | Free | Not for untrusted hosts |
| GitHub LFS | 10 GiB, 2 GB/file | None | **10 GiB/mo cap** | **No** |
| Zenodo | 50 GB/record | None | Free | Publishing, not training |
| Kaggle | 200 GB | None | Free | Low priority |
| Torrent + webseed | Unbounded | None (public only) | Free-ish | Later, if measured |

### Cost of one real job

200 GB dataset, 20 hosts, 10 rounds. Worst case (no cache, no locality
scheduling) is 2 TB of egress; best case (warm cache + locality) is 200 GB.

| Origin | Worst case | Best case |
|---|---|---|
| HF (public or private) | **$0** | **$0** |
| Cloudflare R2 | **$0** + $3/mo storage | $0 + $3/mo |
| Backblaze B2 | $20 (or $0 via CF) | $2 |
| AWS S3 | **$184** | $18 |
| GitHub LFS | quota exceeded in round 1 | quota exceeded |

Two readings. First, **caching and locality scheduling are worth 10× on the
only axis that costs real money** — which retroactively justifies steps 4 and 5
of the morning note's build order. Second, **on HF and R2 the whole column is
zero**, which means we can ship the feature without anyone getting a bill, and
that is worth constraining the recommendation for.

---

## 8. What this changes about the morning note

- §5 Tier 1 said *"Private data → their own S3/R2/GCS bucket … exchanged for
  presigned URLs."* Refine to **R2 first**, because prefix-scoped temporary
  credentials fit a chunk-of-many-shards better than per-object presigning, and
  because zero egress removes the re-fetch-storm risk.
- §5 said the user supplies a manifest. **They should not have to** — for HF we
  derive it (§3.5), and for S3/R2 we derive it from `ListObjectsV2` + ETags.
  A hand-written manifest becomes the escape hatch, not the interface.
- Open question #1 (*"does FlashML hold users' storage credentials at all?"*)
  now has a sharper answer: **we hold one narrow token and never hand it on.**
  See §9.4 — the brokered pattern means the host receives capabilities, never
  credentials, which makes holding the token defensible in a way "we hand it to
  volunteers" never would be.
- Open question #3 (*"do we verify shard checksums?"*) → **yes, and it is
  nearly free.** HF gives sha256 in both the tree API and the
  `x-linked-etag` header; S3/R2 give ETags. Not verifying is a choice to allow
  silent corruption.
- The §4 chunk→shard formula `[c*S//C, (c+1)*S//C)` divides by **file count**.
  That is wrong for real datasets, where shard sizes are uneven — one host gets
  10× the bytes. Split on **cumulative bytes**, which the manifest now carries.

---

## 9. The design

### 9.1 What the user writes

```yaml
datasets:
  - name: imdb
    source: hf://stanfordnlp/imdb          # or r2:// s3:// https://
    select: "plain_text/train-*.parquet"   # optional glob; default: all files
    split: shard                           # shard | replica
```

One required line. `name` becomes the directory the task sees at
`/work/data/<name>/`.

### 9.2 `split:` — the part the morning note missed

The owner's question mentioned *"fsdp or ddp depend or independent mode"*. FSDP
is out of scope between hosts (morning note §3.2 — it needs a datacentre
interconnect; what survives the internet is DDP-shaped data parallelism with
infrequent averaging, which is what FedAvg already is). But the underlying
instinct is right: **the sharding policy depends on what the job is doing**, and
one policy does not fit.

| `split` | Each host gets | For |
|---|---|---|
| `shard` (default) | A disjoint, contiguous, byte-balanced slice | Federated / data-parallel training. Union over the fleet = one epoch. |
| `replica` | The whole dataset | Hyperparameter sweeps, evaluation, embarrassingly-parallel jobs where every worker needs everything. |

This is not cosmetic. `replica` means the *entire* dataset must fit inside
every host's cache budget, which changes admission (§9.6) from "can the fleet
hold this" to "can every machine hold this". Getting it wrong means a job that
admits and then fails on every node.

### 9.3 Resolution, at submit time, in the API

1. **Expand** `source` + `select` → file list with size and sha256.
   HF: tree API. S3/R2: `ListObjectsV2` + ETag.
2. **Pin the revision.** HF: `main` → commit sha. S3/R2: record per-object
   ETags. The run is now reproducible and immune to origin mutation.
3. **Store the manifest** as an ordinary artifact. It is JSON in the kilobytes —
   it fits the diskless control plane without touching the 2 GiB budget.
4. **Compute the chunk→shard map** by cumulative bytes, contiguous, offset by
   `round_chunk_offset()`. Central, once, not reinvented per user.

### 9.4 Access: three trust levels, one rule

**The rule: the credential stays in the control plane. The host receives a
capability, never a credential.**

| Level | Mechanism | Host receives | Use for |
|---|---|---|---|
| **L0 — public** | No auth. Host fetches anonymously from its own IP. | A URL | Public HF, public buckets. **Preferred whenever possible.** |
| **L1 — brokered** | Coordinator holds a narrow read-only token; per lease it mints a short-lived, shard-scoped capability. HF: the `resolve` 302 (60 min, one object). R2: temporary credentials (prefix-scoped, `object-read-only`, TTL = lease). | An expiring URL or temp credential | **Private data on open volunteer pools.** The recommended private path. |
| **L2 — delegated** | Host receives the user's repo-wide read token. | A reusable credential | `--runner trusted` and self-hosted pools **only**. Never an open pool. |

The token the user gives us should itself be minimum-scope: an HF **fine-grained
token scoped read-only to one repo**, or an R2 API token scoped read-only to one
bucket. Blast radius if our store is breached: read access to datasets users
explicitly enrolled, nothing else. That is a bounded, explainable risk, and it
is a different conversation from "we hold your cloud keys".

### 9.5 Fetch: the existing seam, not a new one

The host agent already fetches the code tarball, container images and (as of
the dependency work) pip dependencies before the sandbox closes. Datasets join
that seam. Two constraints:

- **Direct to origin.** `download_artifact()` goes through
  `/v1alpha1/artifacts/{key}` — the coordinator. Datasets must **not**. Pushing
  multi-GB through the coordinator puts one user's dataset on the shared disk
  that `_storage_gate` exists to protect, and turns it into everyone's outage.
  Dataset bytes never touch our infrastructure.
- **`--network none` is untouched.** The task sees files. It never sees a URL,
  a token, or a socket.

`DatasetCache` in flashnode, modelled directly on `EnvironmentCache`
(`environments.py`) — the morning note's biggest reuse finding stands:

- **Keyed by sha256**, not by URL. Content-addressed, so two jobs using the
  same public dataset — or two revisions sharing unchanged shards — share cache
  entries for free.
- **`FLASHNODE_DATA_BUDGET_GB`, separate from `FLASHNODE_ENV_BUDGET_GB`.** A
  host owner should be able to say yes to 8 GB of venvs and no to 200 GB of
  someone's images. Different orders of magnitude, different consent.
- **LRU eviction and `_refuse_if_disk_is_short`**, unchanged in spirit. *"Disk
  is somebody else's."*

### 9.6 Admit at submit time, not at run time

FlashML knows the manifest bytes and the hosts' advertised free disk. So:

> `bytes_per_host` = total/chunks (`shard`) or total (`replica`).
> If no host in the pool can hold that, **refuse the job at submit** with the
> number.

Failing in the console in one second beats twenty machines each downloading for
forty minutes and then refusing. This is cheap, and it is the difference
between a feature and a demo.

### 9.7 Integrity and provenance

Verify sha256 on every fetched shard. A mismatch fails the **task**, not the
node — a bad origin costs the submitter their job, never the volunteer their
machine, consistent with how `archives.py` already treats hostile input.
Record the pinned revision and the manifest digest in provenance
(`2026-08-02-provenance-and-local-data-design.md`), so "what did this model
train on" has an answer that is a hash rather than a URL.

### 9.8 What the user is told

> **Public data?** Push it to Hugging Face and give us the repo name.
> **Private and under 100 GB?** A private HF repo works, free.
> **Private and larger, or you care about it?** Cloudflare R2, and give us a
> read-only token for that one bucket.
> **Can't move it at all?** Host the machine yourself and use `local_inputs`.

Four sentences, four tiers, and the second and third differ on a question the
user can actually answer about themselves.

---

## 10. Build order

Amends the morning note's §7 rather than replacing it.

1. **Run the §11 experiment.** It gates the HF private path and costs ten
   minutes. Do it before writing the spec, not after.
2. **Write the `datasets:` spec** — manifest schema, `split:` policy, the three
   trust levels, cache semantics, admission check. After the dependency plans
   land; same seam, build it once.
3. **Clear the `local_inputs` security blocker.** Still the only real-data path
   that exists, still not cleared to ship.
4. **Move the chunk→shard mapping into FlashML** — byte-weighted, contiguous.
   Worth doing on its own merits before datasets exist.
5. **`DatasetCache` in flashnode.**
6. **L0 public HF only**, end to end. Ship the zero-credential path first: it
   is most of the value, and it carries none of the risk.
7. **L1 brokered private** — HF broker and R2 temporary credentials.
8. **Locality scheduling.** Last; it is a 10× cost optimisation (§7) but
   correctness comes first.

---

## 11. The experiment to run first

Ten minutes, one throwaway private HF dataset repo, one fine-grained read-only
token:

```bash
# Does an authenticated resolve on a PRIVATE repo return a signed CDN URL,
# and is that URL usable WITHOUT the token?
LOC=$(curl -sI -H "Authorization: Bearer $HF_TOKEN" \
  "https://huggingface.co/datasets/<me>/<private>/resolve/<sha>/<file>" \
  | grep -i '^location:' | cut -d' ' -f2 | tr -d '\r')

curl -s -r 0-15 -o /dev/null -w "%{http_code}\n" "$LOC"    # expect 206, no token
```

- **206** → the broker works on private repos. L1 on HF is real, and the
  private story costs nothing.
- **403** → the signed URL is identity-bound and non-transferable. HF private
  then supports only L2 (hand the host a repo-wide token), which is acceptable
  for trusted pools and **not** for open volunteer pools — and R2 becomes the
  only private answer for the open case.

While there: check whether `s3.hf.co` honours SigV4 query-string presignatures
(§3.8). If it does, HF Buckets re-enter the comparison.

---

## 12. Open decisions for the owner

1. **Do we accept holding narrow, per-dataset read tokens?** §9.4 makes this
   much more defensible than the morning framing — we hold one repo-scoped or
   bucket-scoped read-only token and hand out only expiring capabilities. But
   it is still a secrets-custody commitment, and `all-keys-are-secrets` applies.
2. **Default `FLASHNODE_DATA_BUDGET_GB`?** Venvs default to 8. Datasets are an
   order of magnitude bigger and it is the volunteer's disk. My instinct is a
   low default (16?) with a prominent prompt at `flashnode join`, because a
   host owner who is surprised by disk usage never comes back.
3. **Do we recommend R2 by name in the docs?** It is the technically correct
   answer (§5) and naming one option is worth far more to a user than listing
   five. It is also a dependency on a third party we do not control.
4. **Is `flashml datasets prepare` in scope?** Unchanged from the morning note,
   and §3.2 sharpens it: HF *requires* Parquet/WebDataset shapes for large
   datasets, so the conversion step is not optional for users going the HF
   route — it is just a question of whether they do it or we help.

---

## Sources

- [Hugging Face — Storage limits](https://huggingface.co/docs/hub/en/storage-limits)
- [Hugging Face — Hub rate limits](https://huggingface.co/docs/hub/en/rate-limits)
- [Hugging Face — Pricing](https://huggingface.co/pricing)
- [Hugging Face — Storage Buckets](https://huggingface.co/docs/hub/en/storage-buckets)
- [Hugging Face — Storage Buckets S3 compatibility](https://huggingface.co/docs/hub/en/storage-buckets-s3)
- [Hugging Face — Introducing Storage Buckets (blog)](https://huggingface.co/blog/storage-buckets)
- [Hugging Face — Xet storage backend](https://huggingface.co/docs/hub/en/storage-backends)
- [Hugging Face — User access tokens (fine-grained)](https://huggingface.co/docs/hub/en/security-tokens)
- [Hugging Face — Datasets streaming](https://huggingface.co/docs/hub/en/datasets-streaming)
- [Cloudflare R2 — Temporary credentials](https://developers.cloudflare.com/r2/api/s3/temporary-credentials/)
- [Cloudflare R2 — Authentication and API tokens](https://developers.cloudflare.com/r2/api/tokens/)
- [GitHub Docs — About Git Large File Storage](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-git-large-file-storage)
- [GitHub Docs — Git LFS billing](https://docs.github.com/billing/managing-billing-for-git-large-file-storage/about-billing-for-git-large-file-storage)
- [Zenodo — File size limitations](https://support.zenodo.org/help/en-gb/1-upload-deposit/80-what-are-the-size-limitations-of-zenodo)
- [Kaggle — Doubling of private quota and public file size limit](https://www.kaggle.com/product-announcements/512322)
- [Academic Torrents: Scalable Data Distribution](https://arxiv.org/pdf/1603.04395)
- [BitTorrent webseeding (BEP 17 / BEP 19)](https://wiki.theory.org/BitTorrent_Magnet-URI_Webseeding)
- [DVC — data version control](https://dvc.org/)
- Prior note: `docs/research/2026-08-10-dataset-access-for-volunteer-compute.md`

Empirical checks in §3.5 and §3.7 were run against `huggingface.co` on
2026-08-10 and are reproducible with the commands shown.
