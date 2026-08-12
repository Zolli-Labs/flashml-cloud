#!/usr/bin/env python3
"""Prove the OSS artifact path works, against the REAL bucket.

Exercises every operation `alibaba_oss.OSSArtifacts` actually performs —
`put_object`, `head_object`, `get_object`, `sign_url`, `delete_object` — in
the order the mirror uses them, through our own class rather than through
`ossutil`. A green `ossutil` run proves the *account* works; only this proves
the RAM user, its policy, and our client configuration work together, which
is where the three ways to get this wrong actually live:

  * a bucket in the wrong REGION            (plan offsets nothing)
  * a bucket with the wrong REDUNDANCY      (plan offsets nothing)
  * a policy scoped to a DIFFERENT bucket   (403 on the first write)

The third is the one that fails silently until a job finishes, because
mirroring is best-effort and swallows its own errors by design — a job just
quietly stays `storage: "coordinator"` for ever.

Reads config from the environment, so run it with the same file the API
uses:

    set -a; . ./.env.dev; set +a
    flashml-cloud/apps/api/.venv/bin/python \\
        flashml-cloud/scripts/competition/oss_smoke.py

Costs a few hundred bytes of PUT/GET and deletes what it wrote, including on
failure. It never prints a key, and the presigned URL is printed with its
signature elided — the whole point of that URL is that it *is* a credential.
"""
from __future__ import annotations

import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "apps", "api"),
)

from flashml_cloud_api.alibaba_oss import OSSArtifacts  # noqa: E402

#: The five names `settings.from_env` reads. Read DIRECTLY here rather than
#: through `Settings.from_env()`, which refuses to build without a
#: coordinator URL and operator token — correct for the API, absurd as a
#: precondition for checking a bucket. Reading them here also makes this a
#: truer test: it exercises the exact variable names a deployment sets, so a
#: `FLASHML_`-prefixed typo fails here instead of looking configured.
_BUCKET = os.environ.get("OSS_BUCKET", "").strip()
_ENDPOINT = os.environ.get("OSS_ENDPOINT", "").strip()
_REGION = os.environ.get("OSS_REGION", "").strip() or "ap-southeast-1"
_KEY_ID = os.environ.get("OSS_ACCESS_KEY_ID", "").strip()
_KEY_SECRET = os.environ.get("OSS_ACCESS_KEY_SECRET", "").strip()


def _redact_url(url: str) -> str:
    """Keep the shape, drop the signature."""
    head, _, query = url.partition("?")
    if not query:
        return head
    kept = [
        p if not p.lower().startswith(("signature=", "x-oss-signature="))
        else p.split("=", 1)[0] + "=<elided>"
        for p in query.split("&")
    ]
    return f"{head}?{'&'.join(kept)}"


def main() -> int:
    missing = [
        name for name, value in (
            ("OSS_BUCKET", _BUCKET),
            ("OSS_ENDPOINT", _ENDPOINT),
            ("OSS_ACCESS_KEY_ID", _KEY_ID),
            ("OSS_ACCESS_KEY_SECRET", _KEY_SECRET),
        ) if not value
    ]
    if missing:
        print(f"not configured — missing {', '.join(missing)}", file=sys.stderr)
        return 2

    print(f"bucket   {_BUCKET}")
    print(f"endpoint {_ENDPOINT}")
    print(f"region   {_REGION}")
    print(f"key id   {_KEY_ID[:8]}…  (secret not shown)")
    print()

    oss = OSSArtifacts(
        endpoint=_ENDPOINT, bucket=_BUCKET,
        access_key_id=_KEY_ID, access_key_secret=_KEY_SECRET,
    )
    key = f"_smoke/{int(time.time())}.txt"
    body = b"flashml oss smoke\n"
    wrote = False

    try:
        oss.put_bytes(key, body)
        wrote = True
        print(f"  put      {key}  ({len(body)} bytes)")

        meta = oss.head(key)
        if meta is None:
            print("  head     ABSENT right after a successful put", file=sys.stderr)
            return 1
        print(f"  head     present")

        got = oss.get_bytes(key)
        if got != body:
            print(f"  get      MISMATCH: {got!r} != {body!r}", file=sys.stderr)
            return 1
        print(f"  get      {len(got)} bytes, identical")

        url = oss.sign_get(key)
        print(f"  signed   {_redact_url(url)}")

        # The presigned URL is what a browser is redirected to, so fetch it
        # with NO credentials at all — that is the property under test. A
        # signed URL that only works for an authenticated caller would send
        # every download to a 403 the moment it left our process.
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                fetched = resp.read()
        except urllib.error.HTTPError as exc:
            print(f"  fetch    HTTP {exc.code} — the signed URL was refused",
                  file=sys.stderr)
            return 1
        if fetched != body:
            print("  fetch    MISMATCH through the signed URL", file=sys.stderr)
            return 1
        print(f"  fetch    {len(fetched)} bytes, unauthenticated, identical")

    except Exception as exc:  # noqa: BLE001 - the report IS the product here
        print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("\nIf this is a 403 on the very first put, the RAM policy is "
              "almost certainly still scoped to a different bucket name.",
              file=sys.stderr)
        return 1
    finally:
        if wrote:
            try:
                # `delete_prefix`, not a single-key delete — that is the only
                # delete this class exposes. The prefix is the full key, so
                # it matches exactly the one object this run created.
                n = oss.delete_prefix(key)
                print(f"  delete   {key}  ({n} object(s))")
            except Exception as exc:  # noqa: BLE001
                print(f"  delete   FAILED, object left behind: {exc}",
                      file=sys.stderr)

    print("\nPASS — put, head, get, presign, unauthenticated fetch, delete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
