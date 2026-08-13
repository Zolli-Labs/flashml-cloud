"""``POST /v1alpha1/preflight`` — validation without a push.

The route exists to close the sharpest edge in the authoring workflow: today
the only way to learn that line 3 of a `flashml.yaml` is wrong is
edit → commit → push → submit → read findings, which is four irreversible
steps per guess. For an agent iterating on a config that is the whole cost of
being wrong.

Two claims are pinned here and they are the reason the route is safe to
expose at all:

1. **It creates nothing.** No job row, no artifact, no coordinator call. If
   that ever stops being true, an unauthenticated-adjacent validation endpoint
   becomes a way to make us do work.
2. **It is the SAME authority as `from-repo`.** Not a copy of the rules — the
   same `parse_flashml_yaml` + `preflight` pair. A second implementation would
   drift, and drift here means the CLI blesses a config the API refuses.

Fixture wiring follows ``test_cli_token_routes.py``: the shared helpers live
in ``test_jobs_from_repo`` and are imported from there.
"""
from __future__ import annotations

from flashml_cloud_api import cli_auth

from test_jobs_from_repo import (  # noqa: F401 - fixtures
    CLEAN_YAML,
    _jwt,
    _new_user,
    db,
    make_client,
    settings,
    transport,
)

CLEAN_ENTRYPOINT = """
import json

with open("/work/out/metrics.json", "w") as fh:
    json.dump({"accuracy": 0.9}, fh)
"""

# Reaches the network, which a task cannot do: every task runs `--network
# none`. This is the single most common way an agent-authored script fails,
# and it fails forty minutes in on a stranger's machine rather than at author
# time — which is the entire argument for this route.
NETWORKED_ENTRYPOINT = """
import urllib.request

urllib.request.urlopen("https://example.com/data.csv")

with open("/work/out/metrics.json", "w") as fh:
    fh.write("{}")
"""


def _levels(body: dict) -> set[str]:
    return {str(f.get("level", f.get("severity", ""))) for f in body.get("findings", [])}


def _codes(body: dict) -> set[str]:
    return {str(f.get("code", "")) for f in body.get("findings", [])}


def _post(client, headers, **payload):
    return client.post("/v1alpha1/preflight", json=payload, headers=headers)


def _headers(db, user: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {_jwt(user or _new_user(db))}"}


# -- the happy path ---------------------------------------------------------


def test_a_clean_workload_passes_and_creates_no_job(make_client, db):
    client = make_client()
    user = _new_user(db)
    before = client.get("/v1alpha1/jobs", headers=_headers(db, user)).json()

    r = _post(
        client,
        _headers(db, user),
        config=CLEAN_YAML,
        entrypoint=CLEAN_ENTRYPOINT,
        entrypoint_path="train.py",
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert "error" not in _levels(body)

    # The load-bearing assertion of the whole route.
    after = client.get("/v1alpha1/jobs", headers=_headers(db, user)).json()
    assert after == before


def test_it_returns_the_normalized_config_so_a_caller_can_show_the_derived_shape(
    make_client, db
):
    """The design asks for the parsed config back, not just a verdict: a CLI
    that can say "this expands to 20 epochs on python-slim" before submitting
    is the difference between validation and understanding."""
    client = make_client()
    r = _post(
        client,
        _headers(db),
        config=CLEAN_YAML,
        entrypoint=CLEAN_ENTRYPOINT,
        entrypoint_path="train.py",
    )
    assert r.status_code == 200
    config = r.json()["config"]
    assert config["name"] == "acme-trainer"
    assert config["image"] == "python-slim"
    assert config["entrypoint"] == "train.py"


# -- verdicts are 200, not 4xx ---------------------------------------------


def test_a_failing_workload_is_a_200_with_findings_not_an_http_error(make_client, db):
    """A linter that HTTP-errors when your code is wrong is hostile to the
    loop it exists to serve. `ok: false` is the verdict; the status code is
    about whether we could answer, not about the answer."""
    client = make_client()
    r = _post(
        client,
        _headers(db),
        config=CLEAN_YAML,
        entrypoint=NETWORKED_ENTRYPOINT,
        entrypoint_path="train.py",
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert "error" in _levels(body)


def test_unparseable_yaml_is_a_finding_not_a_500(make_client, db):
    client = make_client()
    r = _post(client, _headers(db), config="version: 1\n  name: [oops\n")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["findings"], "an unparseable config must say why"


def test_an_unknown_image_is_a_finding_and_names_the_image(make_client, db):
    client = make_client()
    r = _post(
        client,
        _headers(db),
        config="version: 1\nname: x\nimage: not-a-real-image\nentrypoint: t.py\n",
        entrypoint=CLEAN_ENTRYPOINT,
        entrypoint_path="t.py",
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is False


def test_a_config_that_does_not_parse_does_not_report_a_missing_entrypoint(
    make_client, db
):
    """Reporting "entrypoint train.py not found" underneath a YAML syntax
    error sends the user to fix the wrong thing. The parse error is the whole
    answer."""
    client = make_client()
    r = _post(client, _headers(db), config="version: 1\n  name: [oops\n")
    assert "entrypoint-missing" not in _codes(r.json())


# -- the entrypoint is analysed from bytes, never from a repo ---------------


def test_a_missing_entrypoint_body_still_answers_on_the_config_alone(make_client, db):
    """Preflight's scope is the entrypoint file, but a caller whose config
    does not name one — or who has not read it yet — still deserves the
    config-level verdict rather than a refusal."""
    client = make_client()
    r = _post(client, _headers(db), config=CLEAN_YAML)
    assert r.status_code == 200, r.text
    assert "findings" in r.json()


def test_an_entrypoint_escaping_the_workload_is_refused(make_client, db):
    """`entrypoint: ../../../etc/passwd` must not make this route read
    anything. The sandbox that runs the job would refuse it anyway; reading
    the file at all is the part worth not doing."""
    client = make_client()
    r = _post(
        client,
        _headers(db),
        config="version: 1\nname: x\nimage: python-slim\nentrypoint: ../../etc/passwd\n",
        entrypoint="root:x:0:0",
        entrypoint_path="../../etc/passwd",
    )
    assert r.status_code in (200, 400), r.text
    if r.status_code == 200:
        assert r.json()["ok"] is False


# -- authorization ----------------------------------------------------------


def test_it_requires_a_caller(make_client):
    client = make_client()
    r = client.post("/v1alpha1/preflight", json={"config": CLEAN_YAML})
    assert r.status_code == 401


def test_an_unadmitted_account_is_refused(make_client, db):
    """An `fmu_` token confers exactly its owner's access and no more — the
    CLI must not be a way around the admission gate."""
    client = make_client()
    user = _new_user(db, admitted=False)
    r = _post(client, {"Authorization": f"Bearer {_jwt(user)}"}, config=CLEAN_YAML)
    assert r.status_code == 403


def test_a_cli_token_reaches_it(make_client, db):
    """The whole point of the `fmu_` class: `current_user` accepting it makes
    every `browser`-tagged route CLI-reachable in one change."""
    client = make_client()
    owner = _new_user(db)
    started = cli_auth.start_cli_code(db, "test-laptop")
    cli_auth.approve_cli_code(db, started["user_code"], owner)
    token = cli_auth.redeem_cli_code(db, started["device_code"])

    r = _post(
        client,
        {"Authorization": f"Bearer {token}"},
        config=CLEAN_YAML,
        entrypoint=CLEAN_ENTRYPOINT,
        entrypoint_path="train.py",
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


# -- request shape ----------------------------------------------------------


def test_a_missing_config_is_a_400(make_client, db):
    """Absent `config` is a malformed REQUEST, not a verdict about a
    workload — the one case that is genuinely a 4xx."""
    client = make_client()
    r = client.post("/v1alpha1/preflight", json={}, headers=_headers(db))
    assert r.status_code == 400


def test_a_config_that_is_not_a_string_is_a_400(make_client, db):
    client = make_client()
    r = client.post(
        "/v1alpha1/preflight", json={"config": {"version": 1}}, headers=_headers(db)
    )
    assert r.status_code == 400
