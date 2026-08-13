"""PF-3: ``marketplace.can_cover`` gets its first HUMAN-path caller.

AG-3 wired ``can_cover`` into the agent spend path (``agent_wallet.py``); it
had, before that, exactly zero production callers anywhere. PF-3 closes the
matching human-path gap — "nothing refuses a submit an account cannot pay
for" — at the one chokepoint every human submit funnels through before a
byte reaches the coordinator: ``app._human_spend_guard``, called once from
``submit_job`` (the raw-spec route, ``POST /v1alpha1/jobs``) and once from
``_stage_compile_and_submit`` (the body shared by ``/jobs/from-repo`` and
``/jobs/from-upload``).

**This lands on a live path a demo is submitting against right now, so the
non-breaking claim below is not a slogan — every assertion in this file
that matters most is a negative one:**

- An owned/sandboxed-only submit (cost 0 by construction) never even
  reaches ``can_cover`` — ``test_owned_capacity_never_consults_can_cover``.
- A submit by an account with **no** ``credit_accounts`` row at all — which
  is every account this file's fixtures create, and reportedly every real
  account in prod as of 2026-08-13 — succeeds exactly as before, and
  ``can_cover`` is never even called for it (the row does not exist to read
  a balance from) — ``test_no_wallet_row_account_is_never_refused`` and
  ``test_no_wallet_row_account_never_reaches_can_cover``.
- A solvent account's submit succeeds unchanged, whether or not the spend
  guard's enforcement flag is on — ``test_solvent_account_submit_succeeds``
  and ``test_enforcement_on_still_allows_a_solvent_submit``.
- ``_HUMAN_SPEND_GUARD_ENFORCE`` — the one thing standing between "wired"
  and "live" — is False, checked directly by
  ``test_enforcement_flag_defaults_off``, and every insolvency scenario in
  this file is proven twice: once against the real shipped default (never
  refused) and once with the flag deliberately flipped on within the test
  (refused with 402), so the refusal branch is proven reachable rather than
  dead code, without ever running with it reachable in the default path.

The database is the real, freshly migrated ephemeral Postgres from
``conftest.py``. Fixtures (``make_client``, ``_new_user``, ``_jwt``, ``db``,
``transport``, ``CLEAN_REPO``, ``make_tarball``) are imported from
``test_jobs_from_repo`` — the same convention ``test_jobs_from_upload.py``
already uses — so this file observes the exact coordinator-fake and Postgres
setup the existing submit-route tests do, rather than a second copy that
could quietly drift from it.
"""
from __future__ import annotations

import uuid

import pytest

from flashml_cloud_api import app as app_module
from flashml_cloud_api import db as dbmod
from flashml_cloud_api import marketplace as marketplacemod

from test_jobs_from_repo import (  # noqa: F401 - fixtures
    CLEAN_REPO,
    _jwt,
    _job_rows,
    _new_user,
    db,
    make_client,
    make_tarball,
    settings,
    transport,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _submit_raw(client, token: str, *, name: str = "pf3-job", isolation=None):
    """One raw ``POST /v1alpha1/jobs``, with a minimal but SCHEMA-VALID
    inner spec (``image`` + ``workload`` are the only fields ``JobSpec``
    requires — see ``flashruntime.protocol.v1alpha1.JobSpecInner``).

    This is deliberately NOT the bare ``"spec": {}`` shape
    ``test_jobs.py``'s own ``_submit`` uses: that shape is accepted by the
    route (the fake coordinator does not validate it, and neither does the
    real one at submit time) but fails ``JobSpec.model_validate`` — which
    means `_human_spend_guard`'s own best-effort parse fails open on it
    before ever reaching `_tradeoff_rented_gate`, and a test built on it
    would silently prove nothing about the guard's actual logic. A minimal
    valid spec is what lets this file's `can_cover`-consulted assertions
    mean what they say.

    ``isolation`` is optional so a test can build the one shape
    ``_tradeoff_rented_gate`` excludes from rent candidacy (``tier:
    sandboxed``, no ``allowFallback``).
    """
    spec: dict = {
        "image": {"repository": "acme/trainer", "tag": "1.0.0"},
        "workload": {"type": "command", "parameters": {"command": ["true"]}},
    }
    if isolation is not None:
        spec["isolation"] = isolation
    return client.post(
        "/v1alpha1/jobs",
        json={
            "apiVersion": "flashml.dev/v1alpha1", "kind": "Job",
            "metadata": {"name": name}, "spec": spec,
        },
        headers={"Authorization": f"Bearer {token}"},
    )


def _fund(db, owner_id: str, zc: int) -> None:
    """Give ``owner_id`` a real ``credit_accounts`` row with ``zc``
    spendable millicredits — the SOLVENT-account fixture this file needs
    and ``test_jobs_from_repo``'s ``_new_user`` deliberately does not
    provide (no test file's users have a wallet row by default; that gap
    is exactly what PF-3 has to survive).

    Goes through ``marketplace.ensure_accounts`` + a real ledger leg with
    reason ``"grant"`` — the same two calls
    ``marketplace.grant_starting_credits`` makes — never a raw
    ``UPDATE ... SET balance_zc``, so a funded test account is
    indistinguishable from a real one to anything that reads the ledger
    instead of trusting a cached balance.
    """
    accounts = marketplacemod.ensure_accounts(db, owner_id)
    spendable = accounts["spendable"]
    marketplacemod.post(
        db,
        ref_type="account",
        ref_id=str(spendable["id"]),
        legs=[marketplacemod.Leg(str(spendable["id"]), zc, "grant")],
    )


def _zero_balance_row(db, owner_id: str) -> None:
    """A real ``credit_accounts`` row that exists and is exactly $0 — the
    one case PF-3's own ground-truth note says a flipped-on guard MAY
    refuse (as opposed to no row at all, which it must not)."""
    marketplacemod.ensure_accounts(db, owner_id)


def _spy_can_cover(monkeypatch):
    """Wrap ``marketplace.can_cover`` to record every call while still
    running the real function — a spy, not a stub, so a test asserting
    "not refused" is still exercising the real solvency arithmetic."""
    calls: list[tuple[int, dict]] = []
    original = marketplacemod.can_cover

    def spy(spendable_zc, **kwargs):
        calls.append((spendable_zc, kwargs))
        return original(spendable_zc, **kwargs)

    monkeypatch.setattr(marketplacemod, "can_cover", spy)
    return calls


# ---------------------------------------------------------------------------
# 0. the kill switch
# ---------------------------------------------------------------------------


def test_enforcement_flag_defaults_off():
    """The one line that makes every refusal below unreachable in the
    shipped default. If this ever reads True by accident, every other
    "not refused" test in this file would start failing loudly — this
    test is the one that says why, directly."""
    assert app_module._HUMAN_SPEND_GUARD_ENFORCE is False


# ---------------------------------------------------------------------------
# 1. owned/free capacity (cost 0) is NEVER a candidate for refusal
# ---------------------------------------------------------------------------


def test_owned_capacity_never_consults_can_cover(make_client, db, transport, monkeypatch):
    """A sandboxed-tier job with no ``allowFallback`` waiver can never be
    placed on a rented machine (``_tradeoff_rented_gate``) — cost is 0 by
    construction. ``can_cover`` must not even be called, on an account with
    no wallet row and nothing funded, and the submit must succeed exactly
    as it does today."""
    calls = _spy_can_cover(monkeypatch)
    client = make_client()
    alice = _new_user(db)

    r = _submit_raw(client, _jwt(alice), isolation={"tier": "sandboxed"})

    assert r.status_code == 201, r.text
    assert calls == []


# ---------------------------------------------------------------------------
# 2. THE load-bearing non-breaking test: owned/free AND the default,
#    unfunded, no-wallet-row submit succeeds exactly as before
# ---------------------------------------------------------------------------


def test_default_submit_by_an_unfunded_account_still_succeeds(make_client, db, transport):
    """The exact shape ``test_jobs.py``'s existing submit tests use: a
    fresh ``_new_user`` with no ``credit_accounts`` row, submitting a bare
    spec (default isolation tier ``standard`` — rent-candidate by
    ``_tradeoff_rented_gate``, since a standard-tier job may legally land
    on any host, rented ones included). This must return 201 today,
    unconditionally — it is what a live demo is doing right now."""
    client = make_client()
    alice = _new_user(db)

    r = _submit_raw(client, _jwt(alice))

    assert r.status_code == 201, r.text
    assert transport.requests, "the coordinator must still have been reached"


def test_no_wallet_row_account_is_never_refused(make_client, db, transport):
    """Same scenario, restated as its own test because PF-3's ground truth
    is that prod's wallet tables are freshly migrated and empty for
    essentially every account: this is not an edge case, it is the modal
    case, and it must never be refused."""
    client = make_client()
    alice = _new_user(db)

    r = _submit_raw(client, _jwt(alice))

    assert r.status_code == 201, r.text


def test_no_wallet_row_account_never_reaches_can_cover(make_client, db, transport, monkeypatch):
    """``_spendable_zc_or_none`` returns ``None`` for an owner with no
    ``credit_accounts`` row — never a folded 0 — and the guard fails open
    on ``None`` before ``can_cover`` is ever called. Distinguishes this
    from the zero-balance-but-real-row case below, which DOES call
    ``can_cover`` (and is still never refused, because the flag is off)."""
    calls = _spy_can_cover(monkeypatch)
    client = make_client()
    alice = _new_user(db)

    r = _submit_raw(client, _jwt(alice))

    assert r.status_code == 201, r.text
    assert calls == []


# ---------------------------------------------------------------------------
# 3. a solvent account's submit succeeds, even though it would rent
# ---------------------------------------------------------------------------


def test_solvent_account_submit_succeeds(make_client, db, transport, monkeypatch):
    calls = _spy_can_cover(monkeypatch)
    client = make_client()
    alice = _new_user(db)
    _fund(db, alice, 10_000)

    r = _submit_raw(client, _jwt(alice))

    assert r.status_code == 201, r.text
    # can_cover WAS consulted (see test 4 below for the dedicated proof),
    # and it said yes.
    assert calls and calls[0][0] == 10_000


def test_enforcement_on_still_allows_a_solvent_submit(make_client, db, transport, monkeypatch):
    """Flip the kill switch on, inside this test only, and prove a solvent
    account is unaffected either way — the hard constraint holds whether
    or not enforcement is live, not merely because it happens to be off."""
    monkeypatch.setattr(app_module, "_HUMAN_SPEND_GUARD_ENFORCE", True)
    client = make_client()
    alice = _new_user(db)
    _fund(db, alice, 10_000)

    r = _submit_raw(client, _jwt(alice))

    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# 4. can_cover is actually consulted on the human submit path
# ---------------------------------------------------------------------------


def test_can_cover_is_consulted_with_the_accounts_real_balance(
    make_client, db, transport, monkeypatch
):
    """PF-3's headline claim, proven directly: for a rent-candidate submit
    by an account that HAS a wallet row, ``marketplace.can_cover`` is
    called with that account's real spendable balance and the smallest
    nonzero grant the ledger can express (see ``_human_spend_guard``'s
    module note for why 1/1/1 and not a fabricated "precise" cost)."""
    calls = _spy_can_cover(monkeypatch)
    client = make_client()
    alice = _new_user(db)
    _fund(db, alice, 4_242)

    r = _submit_raw(client, _jwt(alice))

    assert r.status_code == 201, r.text
    assert calls == [(4_242, {"zc_per_hour": 1, "seconds": 1, "tasks": 1})]


# ---------------------------------------------------------------------------
# 5. a provably-insolvent account's rented submit — refused only when the
#    kill switch is deliberately flipped on; the mechanism is wired and the
#    default gate is off, so nothing above this line was ever refused.
# ---------------------------------------------------------------------------


def test_enforcement_on_refuses_a_provably_insolvent_rented_submit(
    make_client, db, transport, monkeypatch
):
    monkeypatch.setattr(app_module, "_HUMAN_SPEND_GUARD_ENFORCE", True)
    client = make_client()
    alice = _new_user(db)
    _zero_balance_row(db, alice)  # a REAL row, provably $0 — not "no row"

    r = _submit_raw(client, _jwt(alice))

    assert r.status_code == 402, r.text
    assert "credit" in r.json()["detail"]
    # No trace left, same three guarantees this file's siblings check for
    # every other pre-upload refusal.
    assert transport.requests == []
    assert _job_rows(db, alice) == []


def test_enforcement_off_never_refuses_the_same_insolvent_account(
    make_client, db, transport
):
    """The mirror of the test above with nothing patched — the real
    shipped state. Same account shape (a real, provably-$0 row), same
    rent-candidate job, and it still returns 201: the flag, not the
    account's solvency, is what decides the outcome today."""
    client = make_client()
    alice = _new_user(db)
    _zero_balance_row(db, alice)

    r = _submit_raw(client, _jwt(alice))

    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# 5b. the guard can never turn its own bug into a broken submit
# ---------------------------------------------------------------------------
#
# `_human_spend_guard` adds a real db read (`_spendable_zc_or_none`) to a
# route that did not touch the credit tables before. Both call sites wrap
# the guard so an UNEXPECTED exception (a transient db hiccup, a bug in
# `_tradeoff_rented_gate`, anything that is not the guard's own deliberate
# `HTTPException`) fails open — logged, then the submit proceeds exactly as
# it would have with no guard installed at all. This is the same
# non-breaking property tested above for insolvency, restated for "the
# guard is broken" rather than "the account is broke": either way, a submit
# that did not touch credit tables before must not start 500ing because
# this file's own new code raised.


def test_a_broken_spendable_read_fails_open_on_the_raw_route(
    make_client, db, transport, monkeypatch
):
    def boom(_db, _owner_id):
        raise RuntimeError("simulated db hiccup reading credit_accounts")

    monkeypatch.setattr(app_module, "_spendable_zc_or_none", boom)
    client = make_client()
    alice = _new_user(db)
    _fund(db, alice, 10_000)  # solvent — proves the crash, not the balance, mattered

    r = _submit_raw(client, _jwt(alice))

    assert r.status_code == 201, r.text
    assert transport.requests, "the coordinator must still have been reached"


def test_a_broken_can_cover_fails_open_on_the_raw_route(make_client, db, transport, monkeypatch):
    """Same property, a different failure point inside the guard: whatever
    breaks — the balance read or the solvency check itself — is caught the
    same way, because the wrapping happens around the whole guard call, not
    around one internal step of it."""

    def boom(_spendable_zc, **_kwargs):
        raise RuntimeError("simulated can_cover crash")

    monkeypatch.setattr(marketplacemod, "can_cover", boom)
    client = make_client()
    alice = _new_user(db)
    _fund(db, alice, 10_000)

    r = _submit_raw(client, _jwt(alice))

    assert r.status_code == 201, r.text
    assert transport.requests


def test_a_broken_spendable_read_fails_open_at_the_shared_chokepoint(
    make_client, db, transport, monkeypatch
):
    """The other call site — ``_stage_compile_and_submit``, reached through
    a pool-scoped upload — gets the same protection. Not a duplicate of the
    test above: a bug fixed at one call site and not the other is exactly
    the kind of drift 'one implementation, two call sites' is supposed to
    make impossible, and only a test at each site can catch it slipping."""

    def boom(_db, _owner_id):
        raise RuntimeError("simulated db hiccup reading credit_accounts")

    monkeypatch.setattr(app_module, "_spendable_zc_or_none", boom)
    client = make_client()
    alice = _new_user(db)
    _fund(db, alice, 500)
    pool_id = str(dbmod.create_pool(db, name="Ada's Team", owner_id=alice)["id"])

    r = client.post(
        "/v1alpha1/jobs/from-upload",
        files={"workspace": ("workspace.tar.gz", make_tarball(CLEAN_REPO), "application/gzip")},
        data={"pool": pool_id},
        headers={"Authorization": f"Bearer {_jwt(alice)}"},
    )

    assert r.status_code == 201, r.text
    assert transport.requests


def test_enforcement_on_a_broken_guard_still_fails_open_not_refuses(
    make_client, db, transport, monkeypatch
):
    """Enforcement being on does not turn "the guard crashed" into a
    refusal either: only the guard's OWN deliberate `HTTPException` — raised
    from inside `_human_spend_guard` when it actually determined
    insolvency — is allowed to propagate as a 402. An unrelated exception is
    never reinterpreted as "must be insolvent, refuse"."""
    monkeypatch.setattr(app_module, "_HUMAN_SPEND_GUARD_ENFORCE", True)

    def boom(_db, _owner_id):
        raise RuntimeError("simulated db hiccup reading credit_accounts")

    monkeypatch.setattr(app_module, "_spendable_zc_or_none", boom)
    client = make_client()
    alice = _new_user(db)
    _fund(db, alice, 10_000)

    r = _submit_raw(client, _jwt(alice))

    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# 6. the shared chokepoint: _stage_compile_and_submit (from-repo /
#    from-upload) wires the same guard, not a second copy of it
# ---------------------------------------------------------------------------


def test_from_upload_pool_submit_consults_can_cover(make_client, db, transport, monkeypatch):
    """A plain (no-pool) upload compiles to ``tier: sandboxed,
    allowFallback: False`` (``compile.py``) — never rent-candidate, exactly
    like section 1 above, and already covered by
    ``test_jobs_from_upload.test_a_clean_workspace_is_staged_submitted_and_recorded``
    running with no wallet row. A POOL-scoped upload compiles to
    ``allowFallback: True`` (allowFallback iff pool) and IS rent-candidate
    — this is the one shape that reaches ``_human_spend_guard`` through
    ``_stage_compile_and_submit`` rather than through ``submit_job``, so it
    is the proof the chokepoint is really shared and not a second,
    silently-diverging copy."""
    calls = _spy_can_cover(monkeypatch)
    client = make_client()
    alice = _new_user(db)
    _fund(db, alice, 500)
    pool_id = str(dbmod.create_pool(db, name="Ada's Team", owner_id=alice)["id"])

    r = client.post(
        "/v1alpha1/jobs/from-upload",
        files={"workspace": ("workspace.tar.gz", make_tarball(CLEAN_REPO), "application/gzip")},
        data={"pool": pool_id},
        headers={"Authorization": f"Bearer {_jwt(alice)}"},
    )

    assert r.status_code == 201, r.text
    assert calls == [(500, {"zc_per_hour": 1, "seconds": 1, "tasks": 1})]


def test_from_upload_pool_submit_by_an_unfunded_member_still_succeeds(
    make_client, db, transport
):
    """The non-breaking claim restated for the chokepoint: the exact
    scenario ``test_jobs_from_upload.py``'s own
    ``test_a_pool_member_uploads_with_pool_and_the_row_carries_it`` already
    exercises with an unfunded, no-wallet-row account, unchanged by this
    guard."""
    client = make_client()
    alice = _new_user(db)
    pool_id = str(dbmod.create_pool(db, name="Ada's Team", owner_id=alice)["id"])

    r = client.post(
        "/v1alpha1/jobs/from-upload",
        files={"workspace": ("workspace.tar.gz", make_tarball(CLEAN_REPO), "application/gzip")},
        data={"pool": pool_id},
        headers={"Authorization": f"Bearer {_jwt(alice)}"},
    )

    assert r.status_code == 201, r.text
