# Private datasets — design note

**Date:** 2026-08-12
**Status:** design note, nothing implemented
**Extends:** `2026-08-10-declared-datasets-design.md` (decisions 1 and 9, and
the `DatasetSlice` protocol hook). Read that first — this does not restate it.

---

## 1. What is true today, and why

v1 is **public origins only**, deliberately. `datasets.py` holds no credential
at all, and a private or gated origin is refused **at submit, by name** rather
than by attempting a token we do not have. `_resolve_hf` reads the repo
endpoint specifically to answer "is it private, is it gated" and refuses on
`private is True`.

The governing insight, from the v1 spec: **the scheme is addressing, the
credential is authorization, and they are orthogonal.** `https://` is not
"public"; it is "addressed by URL, with empty authorization". The private work
adds *auth* to schemes that already resolve — it does not add schemes.

Private data currently keeps `local_inputs`.

## 2. What the demo forced us to learn (2026-08-12)

Publishing the demo dataset to Alibaba OSS surfaced two facts worth keeping:

- **Bucket ACL is not the effective permission.** `zolli-flashml-datasets` was
  created with `--acl public-read` and `stat` reports exactly that, while every
  anonymous GET still returned *"no right to access this object because of
  bucket acl"*. **Block Public Access is on by default for new buckets and
  silently overrides the ACL.** Any future "publish a dataset" flow must verify
  with a real unauthenticated fetch, never by reading back the ACL it just set.
- **Public buckets are read *and list*.** `public-read` lets anyone who learns
  the bucket name enumerate it, not merely fetch a URL they were given. Hence
  the standing rule below.

**Standing rule: `zolli-flashml-datasets` is dataset-only, forever.** Job
artifacts live in `zolli-flashml-artifacts-zrs`, which keeps Block Public
Access **on**. Two buckets, because one bucket with mixed exposure is one
mistake away from publishing customer output.

## 2.5 Owner decision, 2026-08-12: **FlashML is not a dataset host**

Stated plainly because it decides most of what follows:

> We do not host users' data. They already host it — usually privately, for
> their own use — and they **grant us access** to it.

`zolli-flashml-datasets` is not a counter-example. It holds **our own example
data** for `examples/demo-suite/`, the way any project ships fixtures. It is
not a place users upload to, and there is no plan to offer one. If a "publish
your dataset" feature is ever proposed, it should be refused on this basis
unless the decision is explicitly revisited: hosting user data means holding
their bytes, their egress bill, their retention obligations and their takedown
requests, none of which the product wants.

This is the same instinct as the v1 line *"We never store your bytes"* — the
control plane reads a file listing and hands out URLs; the shards go from the
origin straight to the machine that needs them.

**Consequence for the private path:** the question is never "how do users get
data into FlashML". It is only ever **"how does a user grant FlashML read
access to data that is already theirs, without handing over a long-lived
credential"** — which is exactly §5.

**Scope today:** both `hf://` and `https://` are supported, **public origins
only**, unchanged from v1 decision 9. Private is deferred, not designed away —
the refusal in §7 is what ships until it lands.

## 3. The requirement

Users will want private datasets — most real training data is not publishable.
Supporting it must not weaken the property that makes the current design safe:

> **No credential of the user's ever reaches the machine that runs the
> workload.**

Note the two different "machines" in that sentence. The **host agent** is
software the host owner installed and can inspect; it is the courier. The
**task** is third-party code running network-isolated. The agent may hold a
short-lived capability. The task must never hold anything.

## 4. The shape: an expiring, single-object capability

The protocol already accommodates this. `DatasetSlice` entries carry
`{path, size, integrity, url}`, and the v1 spec states the `url` field exists
"from the start so the private spec can substitute an expiring capability
without a protocol change".

So a private dataset is: the same manifest, with `url` holding a presigned,
single-object, expiring GET instead of a plain public URL. This is already the
house pattern — `alibaba_oss.sign_get`, and the sandbox path where *"a sandbox
running user ML code never receives the key; it gets presigned, expiring,
single-object URLs minted here"*.

### 4.1 The trap: URLs are minted at submit, fetched at lease

**This is the part that will break if nobody plans for it.**

The manifest is resolved **once, at submit**, and pinned. The URL then travels
inside the task payload. But the node fetches at **lease** time, which may be
minutes later under queue pressure, or *days* later on a retry after a machine
died. `DEFAULT_TTL_S` is 900 seconds.

A presigned URL baked into the task payload at submit therefore expires before
a large fraction of real tasks ever start — and **it will not show up in
testing**, where the lease is granted instantly. It fails only under the queue
depth and retry behaviour of production, which is the worst possible place to
discover it.

Three ways out, in preference order:

1. **The node asks for a URL when it needs one.** A cloud route that takes a
   live lease and a dataset entry and returns a fresh signed GET, authorized
   exactly like `authorize_task_write` — which already requires a live lease on
   `(job_id, task_id)`. The capability then lives as long as it is needed and no
   longer, and a lost lease revokes it implicitly. This mirrors the checkpoint
   and artifact paths rather than inventing a fourth pattern.
2. **Mint on the claim path.** The cloud already proxies every agent call, so it
   could inject fresh URLs into the payload it returns on a successful claim.
   Fewer round trips; but it puts dataset knowledge into the claim path and
   re-signs everything even when the node has the shards cached.
3. **Long TTL.** Rejected. A multi-day presigned URL is a bearer token with a
   long life, and it would be persisted in the task payload — i.e. in the
   database, in logs, and in any event ledger that echoes a payload.

### 4.2 What must not change

The pin. The v1 design resolves to a **specific revision** so that "pushing to
the dataset mid-run cannot change what this job trains on". A URL minted later
must address the *pinned* revision, never "latest". Minting fresh URLs is a
refresh of authorization, not a re-resolution of content.

## 5. Where the user's credential comes from

Three models, and they are not equivalent:

| Model | We hold | Notes |
|---|---|---|
| **User's long-lived key** (S3/R2 secret, HF token) | a real credential, at rest | Highest liability. `0013_github_installations.sql`'s rule — *"no token, no liability"* — was written to avoid exactly this. If we ever do it, it needs the same treatment as the GitHub App: mint short-lived, never persist. |
| **Delegated role** (Alibaba RAM role / AWS AssumeRole with external id) | nothing at rest; we assume a role the user granted | Best for object stores. The user can revoke without rotating anything of theirs. |
| **User pre-signs** | nothing | Simplest and worst UX: the URLs expire on the user's schedule and jobs fail later for reasons they cannot see. |

**Recommendation:** delegated role for `s3://`/`r2://`/OSS. For `hf://`
private, HF offers only a token, so it must follow the GitHub App precedent —
short-lived, minted per use, never a stored column. This matches the recorded
hosting strategy (HF for public, R2 for private).

## 6. There is no public/private toggle in `flashml.yaml`

Worth stating because it is the intuitive design and it is wrong. Privacy is a
property of **where the data lives**, not a flag the author sets. A toggle
would let a `flashml.yaml` claim "private" about a bucket that is world
readable, which is a lie the product would then repeat back to the user.

What the console should show instead is what we **observed**: the resolver
already learns whether an HF repo is private or gated, and an object store
answers plainly. Report that, and let it be a fact rather than an assertion.

## 7. Ordering

The v1 refusal message is the feature until this lands: a private origin is
refused **at submit, by name**, so the failure is legible and early. Do not
soften it into a warning that lets a job start and fail thirty times across
thirty machines.
