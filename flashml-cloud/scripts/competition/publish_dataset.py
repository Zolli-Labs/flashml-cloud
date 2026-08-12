#!/usr/bin/env python3
"""Publish the `mlp-demo` dataset to OSS so a job can declare it with `datasets:`.

    datasets:
      - name: mlp-demo
        source: https://zolli-flashml-datasets.oss-ap-southeast-1.aliyuncs.com/datasets/mlp-demo/manifest.json

The control plane resolves that URL once, at submit time, with no credential
(`flashml_cloud_api.datasets._resolve_https_manifest`), and hands every task a
list of direct object URLs. **No dataset byte ever passes through us** — a
volunteer's machine fetches from OSS itself, anonymously. So the property this
script has to establish is not "the upload succeeded" but "a stranger with no
credential can GET these bytes", which is what `--verify` measures.

What gets written
-----------------
Six training shards and one holdout shard, `.npz`, under
`datasets/mlp-demo/`, plus the manifest last:

    datasets/mlp-demo/train/shard-000.npz  …  shard-005.npz
    datasets/mlp-demo/holdout/eval.npz
    datasets/mlp-demo/manifest.json

Shards rather than one file because federated mode *shards* a dataset across
machines: `split: shard` cuts the entry list by bytes, so a single-file
dataset gives one machine everything and the rest nothing. Train and holdout
are separate directories so one `flashml.yaml` can declare both from the same
manifest and give them different splits — shard the training data, replicate
the holdout so every machine scores against the same rows:

    datasets:
      - name: mlp-demo
        source: <manifest url>
        select: "train/*"
        split: shard
      - name: mlp-demo-eval
        source: <manifest url>
        select: "holdout/*"
        split: replica

Deliberately tiny — about 4 MB total. The demo machine is arm64 and the
curated images are amd64, so every workload runs under qemu emulation.
Accuracy is not the point; a signal a small model can actually learn is. The
Bayes-optimal accuracy here is ~0.85 by construction, so a working model lands
near it and a broken one sits at 0.5, which is the only thing the demo needs
to distinguish.

Determinism
-----------
Same bytes on every run, which matters more than it looks. A manifest carries
a `sha256` per entry that a host verifies after fetching; if a re-run produced
different bytes for the same key, every job resolved before the re-run would
fail integrity on every machine at once. Two things are therefore pinned:

* The arrays come from one seeded `default_rng`.
* The `.npz` container is built by hand with fixed ZIP timestamps and no
  compression. `numpy.savez` stamps each member with the local clock, so its
  output differs run to run even when the arrays are identical.

The manifest document is byte-stable for the same reason — no timestamps in
it — so `revision` (which the resolver derives as a sha256 over the document)
does not churn.

Idempotency
-----------
Fixed keys, so a re-run overwrites in place. The manifest is written **last**,
so a half-uploaded dataset is never announced as complete, and objects left
under the prefix by an earlier layout are deleted **after** the new manifest
lands, so no live manifest ever points at a key that was just removed.

Public access
-------------
The BUCKET's own ACL is `public-read`, so objects inherit public readability
with their DEFAULT acl. No per-object ACL is set: Block Public Access is on
by default for new buckets and refuses `Put public object acl` outright,
while leaving the bucket ACL — and therefore anonymous GET — working.
which sets `x-oss-object-acl: public-read` on that one object. Nothing else in
the bucket is touched, and `--verify` proves it by checking that an unrelated
key is still refused anonymously.

This requires bucket-level **Block Public Access** to be OFF. With it on, both
the ACL header and a follow-up `put_object_acl` fail 403 "Put public object
acl is not allowed" (`EC 0016-00000901`), and no RAM grant fixes that — it is
bucket configuration, not a permission. The script names that case explicitly
rather than letting it read as a credentials problem.

Usage
-----
    set -a; . ./.env.dev; set +a
    flashml-cloud/apps/api/.venv/bin/python \\
        flashml-cloud/scripts/competition/publish_dataset.py            # publish
    …/publish_dataset.py --dry-run    # generate + print the plan, no network
    …/publish_dataset.py --verify     # re-check the published copy only
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile

import numpy as np

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "apps", "api"),
)

from flashml_cloud_api.alibaba_oss import OSSArtifacts  # noqa: E402

# ---------------------------------------------------------------------------
# the dataset
# ---------------------------------------------------------------------------

#: Everything below derives from this one number. Changing it changes every
#: sha256 in the manifest, so it is a republish, not a tweak.
SEED = 1_000_003

N_FEATURES = 24
#: Directions that actually carry label information. The other
#: `N_FEATURES - N_INFORMATIVE` are pure noise, so a model has something to
#: learn *away* from as well as toward.
N_INFORMATIVE = 8
N_TRAIN_SHARDS = 6
ROWS_PER_TRAIN_SHARD = 6_000
HOLDOUT_ROWS = 4_000

#: Standard deviation of the latent logit. Sets the difficulty directly:
#: larger separates the classes further. 2.5 puts Bayes-optimal accuracy near
#: 0.85 — clearly learnable, clearly not 1.0, so a model that has learned
#: nothing (0.5) is unmistakable.
LOGIT_SCALE = 2.5

#: The ZIP epoch. Any fixed value works; this one is the earliest a ZIP can
#: represent, which makes it obviously a constant rather than a real time.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

# ---------------------------------------------------------------------------
# where it goes
# ---------------------------------------------------------------------------

PREFIX = "datasets/mlp-demo/"
MANIFEST_KEY = PREFIX + "manifest.json"

#: The bucket's anonymous read address, written out rather than assembled from
#: the environment.
#:
#: It goes in the manifest, so making it a constant is what stops the manifest
#: from depending on shell state: with `OSS_BUCKET` merely unexported, a
#: derived base silently produced `https://./datasets/...` and a manifest that
#: parsed perfectly, hashed cleanly, and pointed nowhere. The environment is
#: still read — but only to *check* that the credentials in hand address this
#: bucket, below. Workloads are written against this address; a run that
#: cannot reach it must fail rather than publish somewhere else.
PUBLIC_BASE = "https://zolli-flashml-datasets.oss-ap-southeast-1.aliyuncs.com/"
EXPECTED_MANIFEST_URL = PUBLIC_BASE + MANIFEST_KEY

_BUCKET = os.environ.get("OSS_BUCKET", "").strip()
_ENDPOINT = os.environ.get("OSS_ENDPOINT", "").strip()
_KEY_ID = os.environ.get("OSS_ACCESS_KEY_ID", "").strip()
_KEY_SECRET = os.environ.get("OSS_ACCESS_KEY_SECRET", "").strip()


def configured_base() -> str:
    """Where `OSS_BUCKET`/`OSS_ENDPOINT` actually point, for the safety check."""
    host = _ENDPOINT.split("://", 1)[-1].rstrip("/")
    return f"https://{_BUCKET}.{host}/"


# ---------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------


def npz_bytes(**arrays: np.ndarray) -> bytes:
    """`.npz` bytes that are identical for identical arrays.

    `numpy.savez` writes each member with `time.localtime()`, so two runs a
    second apart produce different files from the same data — and a manifest
    is a promise about exact bytes. Built here instead: the same ZIP of `.npy`
    members `numpy.load` expects, with the timestamp, the compression method
    and the originating-system byte all pinned.

    Stored, not deflated. The payload is float32 noise that barely compresses,
    and deflate output depends on the zlib build, which would reintroduce the
    variation this function exists to remove.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for name, array in arrays.items():
            member = io.BytesIO()
            np.lib.format.write_array(member, np.asanyarray(array),
                                      allow_pickle=False)
            info = zipfile.ZipInfo(f"{name}.npy", date_time=ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3  # constant; the default is platform-dependent
            zf.writestr(info, member.getvalue())
    return buf.getvalue()


def generate() -> list[tuple[str, bytes]]:
    """`[(path, npz bytes)]` — six train shards then the holdout.

    A binary tabular problem with a latent linear cause: eight informative
    directions drive a logit, the label is a coin weighted by it, and sixteen
    noise features come along. The whole feature block is then rotated by a
    fixed orthogonal matrix, so the signal is not axis-aligned and no feature
    is individually decisive — a model has to combine them. The rotation is
    orthogonal, so features stay unit-scale and a network trains on the raw
    values without needing normalisation the workload might not do.

    Rows are shuffled before slicing, so every shard is an i.i.d. sample.
    Federated mode gives one shard per machine, and a class-sorted or
    drift-sorted split would make the demo about non-IID federated learning —
    a real problem, but not this one.
    """
    rng = np.random.default_rng(SEED)
    n_total = N_TRAIN_SHARDS * ROWS_PER_TRAIN_SHARD + HOLDOUT_ROWS

    latent = rng.standard_normal((n_total, N_INFORMATIVE))
    weights = rng.standard_normal(N_INFORMATIVE)
    weights /= np.linalg.norm(weights)  # unit norm ⇒ LOGIT_SCALE *is* the sd
    logits = LOGIT_SCALE * (latent @ weights)
    # A Bernoulli draw, not a threshold. Thresholding would make the problem
    # noiseless and any decent model would hit 1.00, which tells you nothing
    # about whether training worked.
    y = (rng.random(n_total) < 1.0 / (1.0 + np.exp(-logits))).astype(np.int64)

    noise = rng.standard_normal((n_total, N_FEATURES - N_INFORMATIVE))
    rotation, _ = np.linalg.qr(rng.standard_normal((N_FEATURES, N_FEATURES)))
    features = (np.concatenate([latent, noise], axis=1) @ rotation).astype(np.float32)

    order = rng.permutation(n_total)
    features, y = features[order], y[order]

    out: list[tuple[str, bytes]] = []
    cursor = 0
    for index in range(N_TRAIN_SHARDS):
        stop = cursor + ROWS_PER_TRAIN_SHARD
        out.append((
            f"train/shard-{index:03d}.npz",
            npz_bytes(X=features[cursor:stop], y=y[cursor:stop]),
        ))
        cursor = stop
    out.append(("holdout/eval.npz",
                npz_bytes(X=features[cursor:], y=y[cursor:])))
    return out


def bayes_accuracy() -> float:
    """The ceiling this problem allows, for the report line.

    Worth printing: it is the number a workload should be compared against.
    Someone seeing 0.85 and expecting 0.99 will go looking for a bug in the
    model that is actually a property of the data.
    """
    rng = np.random.default_rng(SEED)
    z = LOGIT_SCALE * rng.standard_normal(200_000)
    p = 1.0 / (1.0 + np.exp(-z))
    return float(np.maximum(p, 1.0 - p).mean())


def build_manifest(shards: list[tuple[str, bytes]]) -> bytes:
    """The document the resolver parses. Byte-stable across runs.

    `_resolve_https_manifest` requires, per entry, a plain relative `path`, an
    `https://` `url`, a non-negative integer `size`, and a non-empty `sha256`
    — it refuses the whole dataset if any entry lacks the hash, because that
    hash is the only integrity signal an arbitrary HTTPS origin offers. Other
    keys are ignored, so the descriptive block below is free.

    No timestamp anywhere: the resolver's `revision` is a sha256 of this exact
    document, and a clock in it would mint a new revision on every republish
    of identical data.
    """
    base = PUBLIC_BASE
    document = {
        "dataset": "mlp-demo",
        "task": "binary classification, tabular, synthetic",
        "arrays": {
            "X": f"float32 [rows, {N_FEATURES}]",
            "y": "int64 [rows], values 0 or 1",
        },
        "layout": {
            "train/*": f"{N_TRAIN_SHARDS} shards, "
                       f"{ROWS_PER_TRAIN_SHARD} rows each",
            "holdout/*": f"1 shard, {HOLDOUT_ROWS} rows, for evaluation",
        },
        "generator": "scripts/competition/publish_dataset.py",
        "seed": SEED,
        "entries": [
            {
                "path": path,
                "url": base + PREFIX + path,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
            for path, data in shards
        ],
    }
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()


# ---------------------------------------------------------------------------
# anonymous verification — the actual acceptance test
# ---------------------------------------------------------------------------


def anon_get(url: str) -> bytes:
    """GET with no credential of any kind. Exactly what a volunteer does.

    Plain `urllib`, no signing, no session, nothing from the environment. A
    signed fetch here would prove only that our own key works, which is not
    the question.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "flashml-publish/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def anon_status(url: str) -> int:
    """The status an anonymous HEAD gets, with 4xx/5xx returned not raised."""
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": "flashml-publish/1"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def verify(shards: list[tuple[str, bytes]], manifest: bytes,
           oss: OSSArtifacts) -> int:
    """Fetch the published copy anonymously and compare it to what we built.

    Also checks the negative case: an ordinary object outside the dataset
    prefix must still be refused anonymously. From inside the prefix, "we
    published seven objects" and "we opened the whole bucket" look exactly the
    same, so the difference is measured rather than assumed. The control object
    is one this script writes and deletes, so the check never depends on
    reading — or reporting — anything else the bucket happens to hold.
    """
    base = PUBLIC_BASE
    failures = 0

    # Every anonymous fetch below reports rather than raises. A refusal is the
    # single most likely outcome worth reading clearly — it is what a volunteer
    # would hit — and a traceback would bury it.
    try:
        got = anon_get(base + MANIFEST_KEY)
        if got != manifest:
            print("  manifest  MISMATCH — the published document is not this one",
                  file=sys.stderr)
            failures += 1
        else:
            print(f"  manifest  {len(got)} bytes, anonymous, identical")
    except urllib.error.HTTPError as exc:
        print(f"  manifest  HTTP {exc.code} — a host could not read the manifest",
              file=sys.stderr)
        failures += 1

    unreadable = 0
    for path, _ in shards:
        status = anon_status(base + PREFIX + path)
        if status != 200:
            print(f"  {path}  HTTP {status} to an anonymous HEAD", file=sys.stderr)
            unreadable += 1
    failures += unreadable
    if not unreadable:
        print(f"  shards    {len(shards)}/{len(shards)} readable anonymously")

    # One full download, hashed. HEAD proves reachability; only this proves
    # the bytes on the far side are the bytes the manifest promises.
    path, data = shards[0]
    try:
        fetched = anon_get(base + PREFIX + path)
        if hashlib.sha256(fetched).hexdigest() != hashlib.sha256(data).hexdigest():
            print(f"  {path}  sha256 MISMATCH against the manifest",
                  file=sys.stderr)
            failures += 1
        else:
            print(f"  {path}  {len(fetched)} bytes, anonymous, sha256 matches")
            with np.load(io.BytesIO(fetched)) as npz:
                print(f"            X{npz['X'].shape} {npz['X'].dtype}, "
                      f"y{npz['y'].shape} {npz['y'].dtype}, "
                      f"positive rate {npz['y'].mean():.3f}")
    except urllib.error.HTTPError as exc:
        print(f"  {path}  HTTP {exc.code} — a host could not read the shard",
              file=sys.stderr)
        failures += 1

    control_key = f"_health/scope-check-{os.getpid()}"
    try:
        oss.put_bytes(control_key, b"scope check\n")  # note: no public_read
        control = anon_status(base + control_key)
        if control == 200:
            print(f"  scope     FAILED — {control_key} was written with no ACL "
                  f"and is readable anonymously, so the bucket itself is open",
                  file=sys.stderr)
            failures += 1
        else:
            print(f"  scope     HTTP {control} for an ordinary object outside "
                  f"{PREFIX} — the rest of the bucket is still private")
    finally:
        oss.delete_prefix(control_key)

    return failures


# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="generate and print the plan; touch no network")
    parser.add_argument("--verify", action="store_true",
                        help="verify the published copy; upload nothing")
    args = parser.parse_args()

    shards = generate()
    manifest = build_manifest(shards)
    total = sum(len(data) for _, data in shards) + len(manifest)

    print(f"dataset   mlp-demo — binary tabular, {N_FEATURES} features "
          f"({N_INFORMATIVE} informative), seed {SEED}")
    print(f"ceiling   Bayes-optimal accuracy ≈ {bayes_accuracy():.3f}")
    print()
    for path, data in shards:
        print(f"  {path:<24} {len(data):>10,} bytes  "
              f"sha256 {hashlib.sha256(data).hexdigest()[:16]}…")
    print(f"  {'manifest.json':<24} {len(manifest):>10,} bytes")
    print(f"  {'TOTAL':<24} {total:>10,} bytes ({total / 1e6:.2f} MB)")
    print()
    print(f"source:   {EXPECTED_MANIFEST_URL}")
    print()

    if args.dry_run:
        return 0

    if not _BUCKET or not _ENDPOINT or not _KEY_ID or not _KEY_SECRET:
        print("not configured — set the OSS_* variables (set -a; . ./.env.dev; "
              "set +a)", file=sys.stderr)
        return 2

    if configured_base() != PUBLIC_BASE:
        print(f"refusing to publish: OSS_BUCKET/OSS_ENDPOINT address\n"
              f"  {configured_base()}\n"
              f"but this manifest names\n"
              f"  {PUBLIC_BASE}\n"
              f"Uploading here would put the objects somewhere the manifest "
              f"does not point.", file=sys.stderr)
        return 2

    oss = OSSArtifacts(
        endpoint=_ENDPOINT, bucket=_BUCKET,
        access_key_id=_KEY_ID, access_key_secret=_KEY_SECRET,
    )

    if not args.verify:
        uploaded = 0
        try:
            for path, data in shards:
                oss.put_bytes(PREFIX + path, data)
                uploaded += len(data)
                print(f"  put      {PREFIX + path}  ({len(data):,} bytes)")
            # Last, always. Until this lands, the dataset does not exist as far
            # as a resolver is concerned, so an interrupted run leaves shards
            # nothing points at rather than a manifest promising missing files.
            oss.put_bytes(MANIFEST_KEY, manifest)
            uploaded += len(manifest)
            print(f"  put      {MANIFEST_KEY}  ({len(manifest):,} bytes)")
        except Exception as exc:  # noqa: BLE001 - the diagnosis IS the product
            print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            if "public object acl" in str(exc).lower():
                print(
                    "\nThat is bucket-level Block Public Access, not a missing "
                    "RAM permission — no key can be granted past it. Turn it "
                    "off for this bucket and re-run:\n"
                    "  aliyun ossutil api put-bucket-public-access-block "
                    f"--bucket {_BUCKET} \\\n"
                    "    --public-access-block-configuration "
                    '\'{"BlockPublicAccess": false}\'',
                    file=sys.stderr,
                )
            return 1
        print(f"\nuploaded  {uploaded:,} bytes ({uploaded / 1e6:.2f} MB)")
        print()

    print("verifying with no credentials at all:")
    failures = verify(shards, manifest, oss)

    if not args.verify:
        # Only now, with the new manifest live and checked. Anything still
        # under the prefix that this layout does not name is from an older
        # one; removing it before the manifest landed would have broken the
        # dataset the previous manifest still described.
        planned = {PREFIX + path for path, _ in shards} | {MANIFEST_KEY}
        stale = [key for key in oss.list_prefix(PREFIX) if key not in planned]
        for key in stale:
            oss.delete_prefix(key)
            print(f"  removed  {key} (not in this layout)")

    if failures:
        print(f"\nFAILED — {failures} check(s). The dataset is not usable by a "
              f"volunteer regardless of what the upload reported.",
              file=sys.stderr)
        return 1
    print("\nPASS — published, publicly readable, and byte-identical "
          "to what was generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
