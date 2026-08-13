"""``POST /v1alpha1/jobs/from-upload`` — submit a working tree, get a job.

The GitHub App that would read a private repository was deferred, which
leaves this route as the **only** path private code has onto this product.
That is what raises the stakes on the negative assertions below: an upload
route is the one place where bytes chosen entirely by the caller are
unpacked on our disk, so "it extracts safely" is not a nice-to-have here,
it is the reason the route can exist at all.

What is pinned:

- **The same authority as ``from-repo``, after extraction.** A repo with an
  error finding and an upload with the same error finding get the same
  answer, from the same call; the fixtures assert both against the same
  tarball bytes so the two cannot quietly diverge.
- **Extraction refuses what ``repo.extract_safely`` refuses.** A member that
  escapes the destination writes nothing and submits nothing. This does not
  re-test that module (``test_repo.py`` does, thoroughly) — it tests that
  this route reaches for it rather than for a second extractor.
- **Nothing leaks on a refusal.** Every rejected upload leaves no artifact on
  the coordinator, no submitted job, and no ``jobs`` row. Same three
  guarantees ``from-repo`` makes, checked the same way.
- **``allow_fallback`` is answered, not ignored.** It follows the pool by
  construction (``compile.py``: allowFallback iff pool), so a caller who
  contradicts it is told, rather than being handed the opposite of what they
  asked for.

Fixture wiring follows ``test_cli_token_routes.py``: the shared helpers live
in ``test_jobs_from_repo`` and are imported from there, including the
coordinator fake, so the artifact PUT and the job submit are observed exactly
as they are for the repo path.
"""
from __future__ import annotations

import io
import tarfile
import uuid

from flashml_cloud_api import db as dbmod

from test_jobs_from_repo import (  # noqa: F401 - fixtures
    CLEAN_REPO,
    CLEAN_YAML,
    TOP,
    _jwt,
    _job_rows,
    _new_user,
    db,
    make_client,
    make_tarball,
    settings,
    transport,
)

NETWORKED_TRAIN_PY = """
    import urllib.request

    urllib.request.urlopen("https://example.com/data.csv")
"""


def _upload(client, token: str, tar_bytes: bytes, **fields):
    """One submission. ``fields`` become ordinary text parts, which is what a
    ``curl -F pool=...`` and every HTTP client's ``data=`` produce."""
    return client.post(
        "/v1alpha1/jobs/from-upload",
        files={"workspace": ("workspace.tar.gz", tar_bytes, "application/gzip")},
        data={k: v for k, v in fields.items() if v is not None},
        headers={"Authorization": f"Bearer {token}"},
    )


def _flat_tarball(files: dict[str, str]) -> bytes:
    """An archive with no wrapping directory — what ``tar -czf x.tgz *``
    produces, and what this route refuses."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            payload = content.encode()
            member = tarfile.TarInfo(name=name)
            member.size = len(payload)
            tar.addfile(member, io.BytesIO(payload))
    return buf.getvalue()


def _escaping_tarball() -> bytes:
    """A tarball whose member climbs out of the extraction root. The one
    shape that must never write a byte."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=TOP + "/")
        info.type = tarfile.DIRTYPE
        tar.addfile(info)
        payload = b"pwned\n"
        member = tarfile.TarInfo(name=f"{TOP}/../../../../tmp/flashml-escape.txt")
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. auth
# ---------------------------------------------------------------------------


def test_without_a_jwt_nothing_is_extracted_or_submitted(make_client, transport):
    client = make_client()
    r = client.post(
        "/v1alpha1/jobs/from-upload",
        files={"workspace": ("ws.tar.gz", make_tarball(CLEAN_REPO), "application/gzip")},
    )
    assert r.status_code == 401
    assert transport.requests == []


def test_an_unadmitted_account_is_refused(make_client, db, transport):
    client = make_client()
    user = _new_user(db, admitted=False)
    r = _upload(client, _jwt(user), make_tarball(CLEAN_REPO))
    assert r.status_code == 403
    assert transport.requests == []
    assert _job_rows(db, user) == []


# ---------------------------------------------------------------------------
# 2. the happy path
# ---------------------------------------------------------------------------


def test_a_clean_workspace_is_staged_submitted_and_recorded(
    make_client, db, transport
):
    """The whole loop: the uploaded bytes are the artifact the coordinator
    stages, the compiled spec points at that artifact, and the row records
    the job as an upload rather than as a repo it never came from."""
    client = make_client()
    alice = _new_user(db)
    tar_bytes = make_tarball(CLEAN_REPO)

    r = _upload(client, _jwt(alice), tar_bytes)

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["job_id"]

    # The staged artifact is the bytes that were uploaded, unmodified.
    assert list(transport.artifacts.values()) == [tar_bytes]
    code_key = next(iter(transport.artifacts))

    spec = transport.submitted[0]["spec"]
    assert spec["workload"]["parameters"]["inputs"]["code"] == f"artifact://{code_key}"

    row = _job_rows(db, alice)[0]
    assert row["source"]["type"] == "upload"
    assert row["source"]["filename"] == "workspace.tar.gz"
    assert row["source"]["code_artifact"] == f"artifact://{code_key}"
    # No invented provenance: there is no repo, so no owner/repo/ref is
    # recorded that somebody could later try to re-fetch.
    assert "owner" not in row["source"]
    assert "ref" not in row["source"]


def test_an_upload_and_a_repo_compile_to_the_same_spec(make_client, db, transport):
    """The point of sharing the pipeline rather than copying it. Same bytes
    in, same job out — only the staged artifact key (unguessable by design)
    and the recorded source differ."""
    client = make_client(files=CLEAN_REPO)
    alice = _new_user(db)

    from_repo = client.post(
        "/v1alpha1/jobs/from-repo",
        json={"repo": "https://github.com/acme/trainer", "ref": "main"},
        headers={"Authorization": f"Bearer {_jwt(alice)}"},
    )
    assert from_repo.status_code == 201, from_repo.text
    uploaded = _upload(client, _jwt(alice), make_tarball(CLEAN_REPO))
    assert uploaded.status_code == 201, uploaded.text

    repo_spec, upload_spec = (s["spec"] for s in transport.submitted)
    repo_inputs = repo_spec["workload"]["parameters"].pop("inputs")
    upload_inputs = upload_spec["workload"]["parameters"].pop("inputs")
    assert repo_inputs.keys() == upload_inputs.keys()
    assert repo_spec == upload_spec


# ---------------------------------------------------------------------------
# 3. the same preflight authority
# ---------------------------------------------------------------------------


def test_an_error_finding_refuses_the_upload_and_leaves_nothing_behind(
    make_client, db, transport
):
    """The negative assertion that matters. A workload preflight refuses must
    not stage an artifact, must not reach the coordinator at all, and must
    not leave a row — the identical guarantee ``from-repo`` makes."""
    client = make_client()
    alice = _new_user(db)
    tar_bytes = make_tarball(
        {"flashml.yaml": CLEAN_YAML, "train.py": NETWORKED_TRAIN_PY}
    )

    r = _upload(client, _jwt(alice), tar_bytes)

    assert r.status_code == 400, r.text
    body = r.json()
    assert any(f["level"] == "error" for f in body["findings"])
    assert transport.requests == []
    assert _job_rows(db, alice) == []


def test_a_workspace_with_no_config_is_refused(make_client, db, transport):
    client = make_client()
    alice = _new_user(db)

    r = _upload(client, _jwt(alice), make_tarball({"train.py": "print(1)\n"}))

    assert r.status_code == 400
    assert "flashml.yaml" in r.json()["detail"]
    assert transport.requests == []
    assert _job_rows(db, alice) == []


def test_an_unknown_image_is_refused_by_the_same_resolver(make_client, db, transport):
    client = make_client()
    alice = _new_user(db)
    tar_bytes = make_tarball({
        "flashml.yaml": "version: 1\nname: x\nimage: nope\nentrypoint: train.py\n",
        "train.py": "print(1)\n",
    })

    r = _upload(client, _jwt(alice), tar_bytes)

    assert r.status_code == 400
    assert "nope" in r.json()["detail"]
    assert transport.requests == []


# ---------------------------------------------------------------------------
# 4. the tarball is untrusted input
# ---------------------------------------------------------------------------


def test_a_member_escaping_the_destination_writes_nothing_and_submits_nothing(
    make_client, db, transport, tmp_path
):
    """``repo.extract_safely``'s job, reached through this route. The
    assertion is not that the module works — ``test_repo.py`` proves that —
    but that this route uses it instead of a second extractor that might
    not."""
    client = make_client()
    alice = _new_user(db)

    r = _upload(client, _jwt(alice), _escaping_tarball())

    assert r.status_code == 400, r.text
    assert "unsafe path" in r.json()["detail"]
    assert transport.requests == []
    assert _job_rows(db, alice) == []


def test_a_flat_archive_is_refused_with_the_reason(make_client, db, transport):
    """One top-level directory, like GitHub's — because the executor strips
    exactly one wrapping directory when it unpacks the staged artifact. A
    flat archive would run every job one directory off."""
    client = make_client()
    alice = _new_user(db)

    r = _upload(client, _jwt(alice), _flat_tarball(CLEAN_REPO))

    assert r.status_code == 400
    assert "top-level directory" in r.json()["detail"]
    assert transport.requests == []


def test_bytes_that_are_not_a_tarball_are_a_400_not_a_500(make_client, db, transport):
    client = make_client()
    alice = _new_user(db)

    r = _upload(client, _jwt(alice), b"this is not a gzip stream at all")

    assert r.status_code == 400
    assert transport.requests == []


def test_a_body_that_is_not_multipart_is_a_400(make_client, db, transport):
    client = make_client()
    alice = _new_user(db)

    r = client.post(
        "/v1alpha1/jobs/from-upload",
        json={"workspace": "not a file"},
        headers={"Authorization": f"Bearer {_jwt(alice)}"},
    )

    assert r.status_code == 400
    assert "workspace" in r.json()["detail"]
    assert transport.requests == []


def test_a_declared_length_over_the_upload_limit_is_refused_before_reading(
    make_client, db, transport, monkeypatch
):
    """The declared Content-Length is the client's claim, so it is not the
    enforcement — it is what keeps an honest oversized upload from costing a
    full transfer before it is rejected."""
    client = make_client()
    alice = _new_user(db)

    r = client.post(
        "/v1alpha1/jobs/from-upload",
        content=b"",
        headers={
            "Authorization": f"Bearer {_jwt(alice)}",
            "Content-Type": "multipart/form-data; boundary=x",
            "Content-Length": str(1024 * 1024 * 1024),
        },
    )

    assert r.status_code == 413
    assert transport.requests == []


# ---------------------------------------------------------------------------
# 5. pool scoping and the allow_fallback coupling
# ---------------------------------------------------------------------------


def test_a_pool_member_uploads_with_pool_and_the_row_carries_it(
    make_client, db, transport
):
    client = make_client()
    alice = _new_user(db)
    pool_id = str(dbmod.create_pool(db, name="Ada's Team", owner_id=alice)["id"])

    r = _upload(client, _jwt(alice), make_tarball(CLEAN_REPO), pool=pool_id)

    assert r.status_code == 201, r.text
    row = _job_rows(db, alice)[0]
    assert str(row["pool_id"]) == pool_id
    assert row["source"]["pool"] == pool_id
    spec = transport.submitted[0]["spec"]
    assert spec["isolation"] == {"tier": "sandboxed", "allowFallback": True}
    assert spec["placement"]["pool"] == pool_id


def test_a_non_member_uploading_to_a_pool_is_404_and_stages_nothing(
    make_client, db, transport
):
    client = make_client()
    owner = _new_user(db)
    outsider = _new_user(db)
    pool_id = str(dbmod.create_pool(db, name="Ada's Team", owner_id=owner)["id"])

    r = _upload(client, _jwt(outsider), make_tarball(CLEAN_REPO), pool=pool_id)

    assert r.status_code == 404
    assert transport.requests == []
    assert _job_rows(db, outsider) == []


def test_an_unknown_pool_is_404(make_client, db, transport):
    client = make_client()
    alice = _new_user(db)

    r = _upload(
        client, _jwt(alice), make_tarball(CLEAN_REPO), pool=str(uuid.uuid4())
    )

    assert r.status_code == 404
    assert transport.requests == []


def test_allow_fallback_true_without_a_pool_is_refused_rather_than_ignored(
    make_client, db, transport
):
    """`allowFallback` iff `pool`, enforced by `compile.py` and again by
    `CommandRecipe`. Accepting the field and quietly dropping it would hand
    back a job that can never use rented capacity to somebody who asked for
    exactly that."""
    client = make_client()
    alice = _new_user(db)

    r = _upload(
        client, _jwt(alice), make_tarball(CLEAN_REPO), allow_fallback="true"
    )

    assert r.status_code == 400
    assert "allow_fallback" in r.json()["detail"]
    assert transport.requests == []


def test_allow_fallback_false_inside_a_pool_is_refused_rather_than_ignored(
    make_client, db, transport
):
    """The other direction, which is the one that would cost money: a caller
    who says "do not rent" must not be given a job that may."""
    client = make_client()
    alice = _new_user(db)
    pool_id = str(dbmod.create_pool(db, name="Ada's Team", owner_id=alice)["id"])

    r = _upload(
        client, _jwt(alice), make_tarball(CLEAN_REPO),
        pool=pool_id, allow_fallback="false",
    )

    assert r.status_code == 400
    assert transport.requests == []


def test_allow_fallback_agreeing_with_the_pool_is_accepted(
    make_client, db, transport
):
    client = make_client()
    alice = _new_user(db)
    pool_id = str(dbmod.create_pool(db, name="Ada's Team", owner_id=alice)["id"])

    r = _upload(
        client, _jwt(alice), make_tarball(CLEAN_REPO),
        pool=pool_id, allow_fallback="TRUE",
    )

    assert r.status_code == 201, r.text
    assert transport.submitted[0]["spec"]["isolation"]["allowFallback"] is True


def test_an_uninterpretable_allow_fallback_is_refused(make_client, db, transport):
    """Not guessed at. `ture` must not be read as false — silently choosing
    a meaning for an unrecognised value is how a caller gets the opposite of
    what they typed on a field that decides who may run their code."""
    client = make_client()
    alice = _new_user(db)

    r = _upload(
        client, _jwt(alice), make_tarball(CLEAN_REPO), allow_fallback="ture"
    )

    assert r.status_code == 400
    assert "allow_fallback" in r.json()["detail"]
    assert transport.requests == []
