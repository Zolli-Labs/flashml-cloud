"""Artifact storage on Alibaba OSS, and the presigned URLs a sandbox uses.

Two jobs, and the second is the interesting one.

**Storage.** Committed artifacts — checkpoints, the final model, metrics —
currently live on the coordinator's own disk (`FLASHML_LOCAL_ARTIFACTS_DIR`):
5 GB of Render persistent disk on prod, and `/tmp` on dev, where every
redeploy destroys them. `render.yaml` says so plainly: "Without a disk, a
restart loses every in-flight lease and every committed artifact." A demo
whose whole premise is a model surviving a long wait should not keep that
model somewhere a redeploy can erase.

**Handing bytes to a sandbox without handing it a credential.** The FC
sandbox runs user-supplied ML code. An Alibaba access key placed inside it
is readable by that code, and it carries whatever the key's RAM user can do.
So the sandbox never receives one. It receives a **presigned URL**: a signed,
expiring, single-object grant that carries no identity at all. The signing
happens here, in the control plane, with a key scoped by RAM policy to one
bucket.

**The expiry rule that hibernation makes load-bearing.** A URL signed before
a deep hibernation is *expired when the sandbox wakes* — the sandbox is gone
for the whole wait and comes back to a dead grant. So URLs are minted on the
wake path, not carried across it, and `DEFAULT_TTL_S` is deliberately short
to make that non-negotiable rather than merely advised. The competition
rubric asks for "valid credentials after hibernation"; this is the mechanism
that answers it.

Nothing here widens `flashruntime.artifacts.ArtifactStore`. That protocol is
`put_file`/`get_file` and belongs upstream in the public runtime; signing is
a cloud-side concern and stays cloud-side.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .settings import Settings

log = logging.getLogger(__name__)

#: How long a presigned URL stays valid, in seconds.
#:
#: Short on purpose. The sandbox fetches its model immediately after waking,
#: so a long window buys nothing and only widens the blast radius of a URL
#: that leaks into a log line or an event payload. If a caller ever needs
#: longer than this, that is a signal the URL is being carried across a
#: lifecycle boundary it should be re-minted across instead.
DEFAULT_TTL_S = 900

#: Everything for one job lives under one prefix, which is what makes a
#: per-job RAM policy or a per-job cleanup possible later without moving data.
JOB_PREFIX = "jobs/{job_id}/"
SESSION_PREFIX = "sessions/{session_id}/"


class OSSUnavailable(RuntimeError):
    """OSS is not configured, or the bucket refused us.

    Raised instead of returning None so a caller cannot mistake "no storage"
    for "empty storage" — the difference matters when the thing being looked
    for is a model that a training run was supposed to have committed.
    """


@dataclass(frozen=True)
class StoredObject:
    """What we know about one object after writing or inspecting it.

    `sha256` is ours, computed over the bytes we sent — not OSS's ETag, which
    is an MD5 for single-part uploads and something else entirely for
    multipart. Artifact integrity is checked against this.
    """

    key: str
    size_bytes: int
    sha256: str | None
    etag: str | None
    last_modified: datetime | None

    @property
    def uri(self) -> str:
        """Backend-neutral URI, matching `flashruntime.artifacts`' scheme."""
        return f"artifact://{self.key}"


class OSSArtifacts:
    """Bucket-scoped OSS client for the control plane.

    Constructed from `Settings`; raises `OSSUnavailable` rather than
    half-working when the four required values are not all present, for the
    same reason `github_app_configured` is all-or-nothing.
    """

    def __init__(self, *, endpoint: str, bucket: str, access_key_id: str,
                 access_key_secret: str, security_token: str | None = None) -> None:
        if not (endpoint and bucket and access_key_id and access_key_secret):
            raise OSSUnavailable("OSS is not fully configured")
        try:
            import oss2
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise OSSUnavailable("oss2 is not installed") from exc

        self._oss2 = oss2
        self.bucket_name = bucket
        # Host-only in config, scheme added here: the same normalisation
        # `settings._with_default_scheme` does for COORDINATOR_URL, and for
        # the same reason — one place where the value enters the process.
        self._endpoint = endpoint if "://" in endpoint else f"https://{endpoint}"
        auth = (
            oss2.StsAuth(access_key_id, access_key_secret, security_token)
            if security_token
            else oss2.Auth(access_key_id, access_key_secret)
        )
        self._bucket = oss2.Bucket(auth, self._endpoint, bucket)

    @classmethod
    def from_settings(cls, settings: "Settings") -> "OSSArtifacts":
        if not settings.oss_configured:
            raise OSSUnavailable("OSS is not configured")
        return cls(
            endpoint=settings.oss_endpoint,
            bucket=settings.oss_bucket,
            access_key_id=settings.oss_access_key_id,
            access_key_secret=settings.oss_access_key_secret,
        )

    # -- keys ---------------------------------------------------------------

    @staticmethod
    def job_key(job_id: str, name: str) -> str:
        return JOB_PREFIX.format(job_id=job_id) + name.lstrip("/")

    @staticmethod
    def session_key(session_id: str, name: str) -> str:
        return SESSION_PREFIX.format(session_id=session_id) + name.lstrip("/")

    # -- bytes --------------------------------------------------------------

    def put_bytes(self, key: str, data: bytes) -> StoredObject:
        import hashlib

        digest = hashlib.sha256(data).hexdigest()
        result = self._bucket.put_object(key, data)
        return StoredObject(
            key=key,
            size_bytes=len(data),
            sha256=digest,
            etag=getattr(result, "etag", None),
            last_modified=None,
        )

    def get_bytes(self, key: str) -> bytes:
        try:
            return self._bucket.get_object(key).read()
        except Exception as exc:  # noqa: BLE001 - normalised below
            raise OSSUnavailable(f"cannot read {key}: {type(exc).__name__}") from exc

    def head(self, key: str) -> StoredObject | None:
        """Return metadata, or None when the object is not there.

        None means *absent*, which for the demo's wake trigger is a fact and
        not a failure: the evaluator waits precisely because the model does
        not exist yet. A transport or auth failure raises instead, so the two
        can never be confused.
        """
        try:
            meta = self._bucket.head_object(key)
        except self._oss2.exceptions.NoSuchKey:
            return None
        except self._oss2.exceptions.NotFound:
            return None
        return StoredObject(
            key=key,
            size_bytes=int(getattr(meta, "content_length", 0) or 0),
            sha256=None,
            etag=getattr(meta, "etag", None),
            last_modified=_as_datetime(getattr(meta, "last_modified", None)),
        )

    def exists(self, key: str) -> bool:
        return self.head(key) is not None

    def list_prefix(self, prefix: str, *, limit: int = 1000) -> list[str]:
        it = self._oss2.ObjectIterator(self._bucket, prefix=prefix, max_keys=limit)
        return [obj.key for _, obj in zip(range(limit), it)]

    # -- grants -------------------------------------------------------------

    def sign_get(self, key: str, *, ttl_s: int = DEFAULT_TTL_S) -> str:
        """A URL that can read exactly this object, for `ttl_s` seconds.

        This is what a sandbox is given instead of a credential. Re-mint it
        after a wake; never carry one across a hibernation.
        """
        return self._bucket.sign_url("GET", key, ttl_s, slash_safe=True)

    def sign_put(self, key: str, *, ttl_s: int = DEFAULT_TTL_S) -> str:
        """A URL that can write exactly this object, for `ttl_s` seconds."""
        return self._bucket.sign_url("PUT", key, ttl_s, slash_safe=True)

    # -- housekeeping -------------------------------------------------------

    def delete_prefix(self, prefix: str) -> int:
        """Delete everything under `prefix`. Returns how many objects went.

        Guarded against the empty prefix, which would mean the bucket.
        """
        if not prefix or prefix in ("/", "*"):
            raise ValueError("refusing to delete an unbounded prefix")
        keys = self.list_prefix(prefix)
        for key in keys:
            self._bucket.delete_object(key)
        return len(keys)

    def healthcheck(self) -> bool:
        """Round-trip a tiny object. Used at startup and by the demo runbook.

        Deliberately writes as well as reads: a read-only key would satisfy a
        GET-only probe and then fail at the first artifact commit, which is
        the worst moment to discover it.
        """
        key = f"_health/{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}"
        try:
            self.put_bytes(key, b"ok")
            data = self.get_bytes(key)
            self._bucket.delete_object(key)
        except Exception as exc:  # noqa: BLE001 - probe reports, never raises
            log.warning("OSS healthcheck failed: %s", type(exc).__name__)
            return False
        return data == b"ok"


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
