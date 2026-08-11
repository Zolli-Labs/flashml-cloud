#!/usr/bin/env python3
"""Probe Hugging Face as a dataset origin for FlashML.

Every mechanism `2026-08-10-declared-datasets-design.md` depends on is an
assumption about a service we do not control. This script turns each one into
a measurement against the live Hub, so the spec is built on evidence rather
than on documentation that may be aspirational or stale.

    python3 scripts/experiments/hf_dataset_origin_probe.py
    python3 scripts/experiments/hf_dataset_origin_probe.py --json evidence.json

Stdlib only, on purpose: this has to run on a bare machine with no venv, and
adding a dependency to an evidence script is how evidence stops being
reproducible.

STAGE A (public, no credential) gates *building* v1. It is the default and
needs nothing but network.

STAGE B (private broker) gates the *next* spec — whether the coordinator can
hand a volunteer an expiring per-shard URL instead of a reusable token. It
needs a throwaway private dataset repo and a fine-grained read token, so it is
opt-in:

    HF_TOKEN=hf_… python3 …/hf_dataset_origin_probe.py \
        --private-repo <your-namespace>/<private-dataset> --private-file <path>

Exit status is 0 when every check that ran passed, 1 otherwise. Skipped checks
do not fail the run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

API = "https://huggingface.co/api"
HUB = "https://huggingface.co"

#: Small enough that the integrity check is a few seconds, real enough that the
#: shard sizes are uneven — which is the whole point of check A6.
DEFAULT_REPO = "stanfordnlp/imdb"
DEFAULT_SUBPATH = "plain_text"

#: Refuse to download something huge just to prove a hash matches.
MAX_INTEGRITY_DOWNLOAD = 64 * 1024 * 1024


# --------------------------------------------------------------------------
# result plumbing
# --------------------------------------------------------------------------


@dataclass
class Check:
    id: str
    gates: str
    question: str
    status: str = "skip"  # pass | fail | skip
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class Report:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def add(self, check: Check) -> Check:
        self.checks.append(check)
        icon = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[check.status]
        print(f"  [{icon}] {check.id}  {check.question}")
        if check.detail:
            for line in check.detail.splitlines():
                print(f"         {line}")
        return check

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status == "fail"]

    @property
    def passed(self) -> list[Check]:
        return [c for c in self.checks if c.status == "pass"]

    @property
    def skipped(self) -> list[Check]:
        return [c for c in self.checks if c.status == "skip"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """The 302 *is* the finding. Following it destroys the measurement."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def hget(headers: dict[str, str], name: str) -> str:
    """Case-insensitive header lookup.

    Not a nicety. The Hub sends `x-linked-etag` over HTTP/2 and
    `X-Linked-ETag` over HTTP/1.1, and urllib gives us a plain case-sensitive
    dict — so a naive `.get()` silently reports a missing checksum and this
    script fails a check the service actually passes.
    """
    lowered = {k.lower(): v for k, v in headers.items()}
    return lowered.get(name.lower(), "")


def request(
    url: str,
    *,
    method: str = "GET",
    token: str | None = None,
    headers: dict[str, str] | None = None,
    follow: bool = True,
    timeout: int = 30,
) -> tuple[int, dict[str, str], bytes]:
    """Return (status, headers, body). Never raises on an HTTP error status."""
    hdrs = dict(headers or {})
    hdrs.setdefault("User-Agent", "flashml-dataset-origin-probe/1")
    if token:
        hdrs["Authorization"] = f"Bearer {token}"

    opener = urllib.request.build_opener(
        *( [] if follow else [NoRedirect] )
    )
    req = urllib.request.Request(url, method=method, headers=hdrs)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()


# --------------------------------------------------------------------------
# STAGE A — public origin mechanics
# --------------------------------------------------------------------------


def stage_a(report: Report, repo: str, subpath: str) -> None:
    print(f"\nSTAGE A — public origin mechanics  ({repo})")
    print("-" * 72)

    # ---- A1: can we derive a manifest at all? ----------------------------
    tree_url = f"{API}/datasets/{repo}/tree/main/{subpath}?recursive=1"
    status, _, body = request(tree_url)
    entries: list[dict[str, Any]] = []
    if status == 200:
        listing = json.loads(body)
        entries = [e for e in listing if e.get("type") == "file"]

    have_sizes = bool(entries) and all(isinstance(e.get("size"), int) for e in entries)
    have_hashes = bool(entries) and all(
        (e.get("lfs") or {}).get("oid") for e in entries
    )
    a1 = Check(
        "A1",
        "spec §3, §5 — manifest is derived, not hand-written",
        "Does the tree API yield path + size + sha256 for every file?",
    )
    if status == 200 and have_sizes and have_hashes:
        a1.status = "pass"
        total = sum(e["size"] for e in entries)
        a1.detail = f"{len(entries)} files, {total / 1e6:.1f} MB total, all with lfs.oid"
        a1.data = {"files": len(entries), "total_bytes": total}
    else:
        a1.status = "fail"
        a1.detail = (
            f"HTTP {status}; files={len(entries)} sizes={have_sizes} hashes={have_hashes}"
        )
    report.add(a1)
    if a1.status == "fail":
        print("\n  A1 failed — the remaining Stage A checks depend on it. Stopping.")
        return

    manifest = sorted(
        (
            {
                "path": e["path"],
                "size": e["size"],
                "integrity": {"kind": "sha256", "value": e["lfs"]["oid"]},
            }
            for e in entries
        ),
        key=lambda m: m["path"],
    )

    # ---- A2: can we pin an immutable revision? ---------------------------
    status, _, body = request(f"{API}/datasets/{repo}")
    sha = json.loads(body).get("sha") if status == 200 else None
    gated = json.loads(body).get("gated") if status == 200 else None
    a2 = Check(
        "A2",
        "spec decision 5 — every run is pinned to an immutable revision",
        "Does the repo API return a commit SHA we can pin?",
    )
    if sha:
        a2.status = "pass"
        a2.detail = f"main -> {sha}   (gated={gated!r})"
        a2.data = {"sha": sha, "gated": gated}
    else:
        a2.status = "fail"
        a2.detail = f"HTTP {status}, no sha in response"
    report.add(a2)

    revision = sha or "main"
    probe = min(manifest, key=lambda m: m["size"])
    file_url = f"{HUB}/datasets/{repo}/resolve/{revision}/{urllib.parse.quote(probe['path'])}"

    # ---- A3: is there a signed-URL broker, and do two hash sources agree? -
    status, headers, _ = request(file_url, method="HEAD", follow=False)
    location = hget(headers, "Location")
    linked_etag = hget(headers, "X-Linked-ETag").strip('"')
    a3 = Check(
        "A3",
        "spec §4 + research §3.7 — integrity cross-check, broker exists",
        "Does resolve 302 to a signed CDN URL whose etag matches the tree API?",
    )
    agrees = linked_etag == probe["integrity"]["value"]
    if status in (301, 302, 307, 308) and location and agrees:
        a3.status = "pass"
        a3.detail = (
            f"302 -> {urllib.parse.urlsplit(location).netloc}\n"
            f"x-linked-etag == tree api lfs.oid ({linked_etag[:16]}…)"
        )
        a3.data = {"redirect_host": urllib.parse.urlsplit(location).netloc}
    else:
        a3.status = "fail"
        a3.detail = (
            f"HTTP {status}; location={'yes' if location else 'no'}; "
            f"etag agrees={agrees} ({linked_etag[:16]}… vs {probe['integrity']['value'][:16]}…)"
        )
    report.add(a3)

    # The signed URL's TTL and scope decide whether a broker is even possible.
    if location:
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
        expires = qs.get("Expires", [None])[0]
        a3b = Check(
            "A3b",
            "research §3.7 — the capability is short-lived and transferable",
            "What is the signed URL's TTL, and is it IP-bound?",
        )
        if expires and expires.isdigit():
            ttl = int(expires) - int(time.time())
            policy_raw = qs.get("Policy", [""])[0]
            ip_bound = "IpAddress" in _b64_policy(policy_raw)
            a3b.status = "pass" if not ip_bound else "fail"
            a3b.detail = (
                f"TTL ~{ttl / 60:.0f} min; IP-bound={ip_bound} "
                f"({'transferable' if not ip_bound else 'NOT transferable'})"
            )
            a3b.data = {"ttl_seconds": ttl, "ip_bound": ip_bound}
        else:
            a3b.status = "skip"
            a3b.detail = "no Expires parameter on the redirect"
        report.add(a3b)

    # ---- A4: partial fetch ------------------------------------------------
    status, headers, body = request(file_url, headers={"Range": "bytes=0-15"})
    a4 = Check(
        "A4",
        "spec §8.3 — ranged, resumable fetch",
        "Does a Range request return 206 with exactly the bytes asked for?",
    )
    if status == 206 and len(body) == 16:
        a4.status = "pass"
        a4.detail = "206 Partial Content, 16 bytes"
    else:
        a4.status = "fail"
        a4.detail = f"HTTP {status}, {len(body)} bytes (wanted 206 / 16)"
    report.add(a4)

    # ---- A5: does the checksum we would trust actually verify? -----------
    a5 = Check(
        "A5",
        "spec §4, §8.3 — integrity verification is real, not assumed",
        "Does a full anonymous download hash to the manifest's sha256?",
    )
    if probe["size"] > MAX_INTEGRITY_DOWNLOAD:
        a5.status = "skip"
        a5.detail = f"smallest file is {probe['size'] / 1e6:.0f} MB, over the probe cap"
    else:
        started = time.time()
        status, _, blob = request(file_url, timeout=180)
        digest = hashlib.sha256(blob).hexdigest()
        elapsed = time.time() - started
        if status == 200 and digest == probe["integrity"]["value"]:
            a5.status = "pass"
            a5.detail = (
                f"{probe['path']}  {len(blob) / 1e6:.1f} MB in {elapsed:.1f}s, "
                f"sha256 matches"
            )
            a5.data = {"bytes": len(blob), "seconds": round(elapsed, 2)}
        else:
            a5.status = "fail"
            a5.detail = f"HTTP {status}; got {digest[:16]}… want {probe['integrity']['value'][:16]}…"
    report.add(a5)

    # ---- A6: the mapping, on real uneven shard sizes ---------------------
    a6 = Check(
        "A6",
        "spec §6.2 — byte-weighted contiguous chunk mapping",
        "Does the chunk map cover the manifest exactly once, balanced by bytes?",
    )
    sizes = {m["path"]: m["size"] for m in manifest}
    problems, rows = [], []
    for chunks in (1, 2, 3, 7, len(manifest), len(manifest) + 3):
        slices = byte_weighted_chunks(manifest, chunks)
        if [p for s in slices for p in s] != [m["path"] for m in manifest]:
            problems.append(f"C={chunks}: coverage is not an exact partition")
        loads = [sum(sizes[p] for p in s) for s in slices]
        busy = [b for b in loads if b]
        rows.append(
            {
                "chunks": chunks,
                "empty": loads.count(0),
                "spread": round(max(busy) / min(busy), 2) if busy else 0.0,
            }
        )
    if problems:
        a6.status = "fail"
        a6.detail = "\n".join(problems)
    else:
        a6.status = "pass"
        a6.detail = "exact partition at every C. Per C — (empty chunks, byte spread):\n" + ", ".join(
            f"C={r['chunks']}:({r['empty']},{r['spread']}x)" for r in rows
        )
        a6.data = {"rows": rows}
    report.add(a6)

    # ---- A6b: the finding A6 would otherwise hide -------------------------
    # A partition can be perfectly valid and still leave machines idle. With
    # more chunks than manifest entries the surplus chunks are empty, so those
    # hosts fetch nothing, train on nothing, and report nothing. Averaging over
    # a fleet where half the members contributed no gradient is not a smaller
    # round; it is a silently different experiment.
    starved = [r for r in rows if r["empty"]]
    a6b = Check(
        "A6b",
        "spec §6.2 — cap total_chunks at the manifest length",
        "Does a fleet larger than the shard count leave hosts with empty chunks?",
    )
    if starved:
        worst = max(starved, key=lambda r: r["empty"])
        a6b.status = "pass"  # the *measurement* succeeded; it documents a real limit
        a6b.detail = (
            f"Yes. {len(manifest)} shards over C={worst['chunks']} leaves "
            f"{worst['empty']} chunks empty.\n"
            "This is why the spec caps total_chunks at len(manifest) for "
            "split: shard.\nGranularity is a property of the dataset, not of "
            "the fleet."
        )
        a6b.data = {"manifest_files": len(manifest), "starved": starved}
    else:
        a6b.status = "skip"
        a6b.detail = "no empty chunks at the C values probed"
    report.add(a6b)

    # ---- A7: what budget must the agent back off against? ----------------
    status, headers, _ = request(f"{HUB}/datasets/{repo}/resolve/{revision}/{urllib.parse.quote(probe['path'])}", method="HEAD", follow=False)
    policy = hget(headers, "RateLimit-Policy")
    limit = hget(headers, "RateLimit")
    a7 = Check(
        "A7",
        "spec §8.3 — honour RateLimit headers instead of hammering",
        "Are RateLimit headers present on an anonymous resolve?",
    )
    if policy or limit:
        a7.status = "pass"
        a7.detail = f"RateLimit: {limit or '(absent)'}\nRateLimit-Policy: {policy or '(absent)'}"
        a7.data = {"ratelimit": limit, "policy": policy}
    else:
        a7.status = "skip"
        a7.detail = "no RateLimit headers on this response (quota may only surface near the limit)"
    report.add(a7)

    # ---- A8: can we detect a gated repo at submit time? ------------------
    status, _, _ = request(
        f"{HUB}/datasets/meta-llama/Llama-2-7b/resolve/main/README.md",
        method="HEAD",
        follow=False,
    )
    a8 = Check(
        "A8",
        "spec §3 — gated repos are refused at submit, not on 30 machines",
        "Does a gated repo refuse anonymously with a distinguishable status?",
    )
    if status in (401, 403):
        a8.status = "pass"
        a8.detail = f"HTTP {status} anonymously — detectable at submit"
    else:
        a8.status = "skip"
        a8.detail = f"HTTP {status} — probe repo may no longer be gated; pick another"
    report.add(a8)


def _b64_policy(raw: str) -> str:
    import base64

    try:
        return base64.b64decode(raw.replace("-", "+").replace("_", "=") + "==").decode(
            "utf-8", "replace"
        )
    except Exception:
        return ""


def byte_weighted_chunks(manifest: list[dict], chunks: int) -> list[list[str]]:
    """Spec §6.2. Contiguous runs split on cumulative bytes.

    Deliberately the same arithmetic the compiler will use, so this check is a
    test of the real algorithm rather than of a lookalike.
    """
    total = sum(m["size"] for m in manifest)
    out: list[list[str]] = [[] for _ in range(chunks)]
    if total == 0 or chunks <= 0:
        return out
    cursor = 0
    running = 0
    for m in manifest:
        midpoint = running + m["size"] / 2
        target = min(int(midpoint * chunks / total), chunks - 1)
        cursor = max(cursor, target)
        out[cursor].append(m["path"])
        running += m["size"]
    return out


# --------------------------------------------------------------------------
# STAGE B — the private broker probe
# --------------------------------------------------------------------------


def stage_b(report: Report, repo: str, path: str, token: str) -> None:
    print(f"\nSTAGE B — private broker probe  ({repo})")
    print("-" * 72)

    status, _, body = request(f"{API}/datasets/{repo}", token=token)
    if status != 200:
        report.add(
            Check(
                "B0",
                "gates the private-dataset spec",
                "Is the private repo readable with the supplied token?",
                status="fail",
                detail=f"HTTP {status} — check the repo name and that the token can read it",
            )
        )
        return
    meta = json.loads(body)
    report.add(
        Check(
            "B0",
            "preconditions",
            "Is the private repo readable with the supplied token?",
            status="pass" if meta.get("private") else "fail",
            detail=f"private={meta.get('private')!r} sha={meta.get('sha')}"
            + ("" if meta.get("private") else "  <-- repo is NOT private; result proves nothing"),
        )
    )
    if not meta.get("private"):
        return

    url = f"{HUB}/datasets/{repo}/resolve/{meta['sha']}/{urllib.parse.quote(path)}"
    status, headers, _ = request(url, method="HEAD", token=token, follow=False)
    location = hget(headers, "Location")
    b1 = Check(
        "B1",
        "research §11 — does the broker mechanism exist for private repos?",
        "Does an AUTHENTICATED resolve on a private repo 302 to a signed URL?",
    )
    if status in (301, 302, 307, 308) and location:
        b1.status = "pass"
        b1.detail = f"302 -> {urllib.parse.urlsplit(location).netloc}"
    else:
        b1.status = "fail"
        b1.detail = f"HTTP {status}; no redirect — bytes served inline, no capability to hand on"
    report.add(b1)
    if b1.status == "fail":
        return

    # THE question. Strip the token entirely and see if the URL still works.
    status, _, body = request(location, headers={"Range": "bytes=0-15"}, follow=True)
    b2 = Check(
        "B2",
        "DECIDES the private design — L1 broker vs R2-only",
        "Is that signed URL usable with NO Authorization header?",
    )
    if status in (200, 206):
        b2.status = "pass"
        b2.detail = (
            f"HTTP {status}, {len(body)} bytes without a token.\n"
            "=> The capability is TRANSFERABLE. The coordinator can hold the token\n"
            "   and hand volunteers expiring per-shard URLs. L1 on HF is viable."
        )
    else:
        b2.status = "fail"
        b2.detail = (
            f"HTTP {status} without a token.\n"
            "=> The URL is identity-bound. HF private supports only L2 (hand the\n"
            "   host a repo-wide token), which is acceptable for trusted pools and\n"
            "   NOT for open volunteer pools. R2 becomes the only private answer."
        )
    report.add(b2)

    qs = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
    expires = qs.get("Expires", [None])[0]
    if expires and expires.isdigit():
        ttl = int(expires) - int(time.time())
        report.add(
            Check(
                "B3",
                "private spec — lease TTL must not exceed capability TTL",
                "How long does the private capability live?",
                status="pass",
                detail=f"TTL ~{ttl / 60:.0f} min. Leases outliving this need re-minting mid-job.",
                data={"ttl_seconds": ttl},
            )
        )


# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=DEFAULT_REPO, help=f"public dataset repo (default {DEFAULT_REPO})")
    ap.add_argument("--subpath", default=DEFAULT_SUBPATH, help="subdirectory to list")
    ap.add_argument("--private-repo", help="private dataset repo, enables Stage B")
    ap.add_argument("--private-file", help="a file path inside the private repo")
    ap.add_argument("--json", dest="json_out", help="write structured evidence here")
    ap.add_argument("--skip-public", action="store_true", help="run Stage B only")
    args = ap.parse_args()

    report = Report()
    print("=" * 72)
    print("FlashML — Hugging Face dataset origin probe")
    print(f"UTC {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}")
    print("=" * 72)

    if not args.skip_public:
        stage_a(report, args.repo, args.subpath)

    token = os.environ.get("HF_TOKEN")
    if args.private_repo and args.private_file and token:
        stage_b(report, args.private_repo, args.private_file, token)
    else:
        print("\nSTAGE B — private broker probe")
        print("-" * 72)
        missing = []
        if not args.private_repo:
            missing.append("--private-repo")
        if not args.private_file:
            missing.append("--private-file")
        if not token:
            missing.append("HF_TOKEN")
        print(f"  [SKIP] not run (missing: {', '.join(missing)})")
        print("         Stage B gates the PRIVATE dataset spec, not v1.")

    print("\n" + "=" * 72)
    print(
        f"{len(report.passed)} passed, {len(report.failed)} failed, "
        f"{len(report.skipped)} skipped"
    )
    if report.failed:
        print("\nFAILED — a spec assumption does not hold:")
        for c in report.failed:
            print(f"  {c.id}  {c.question}")
            print(f"        gates: {c.gates}")
    else:
        print("\nEvery check that ran holds. Stage A assumptions in")
        print("2026-08-10-declared-datasets-design.md are measured, not assumed.")
    print("=" * 72)

    if args.json_out:
        payload = {
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "repo": args.repo,
            "checks": [
                {
                    "id": c.id,
                    "status": c.status,
                    "question": c.question,
                    "gates": c.gates,
                    "detail": c.detail,
                    "data": c.data,
                }
                for c in report.checks
            ],
        }
        with open(args.json_out, "w") as fh:
            json.dump(payload, fh, indent=2)
        print(f"\nEvidence written to {args.json_out}")

    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
