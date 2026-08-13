"""``GET /v1alpha1/public/prices``: the landing page's curated GPU board.

The marketing page is pre-auth — the one surface in this product a visitor
reaches before signing in — so this is the one route in the whole comparison
story that must answer with **no credential at all**. The properties pinned
here are the ones whose failure would either break that promise (a route
that quietly starts demanding a sign-in) or widen it past what the task
brief allows (a route that leaks pool, wallet, ZC-ask or marketplace data
alongside the curated vendor quotes):

- **No authentication, contrasted with the sibling route that has one.** The
  same account of "public" a session share page already established
  (``test_public_job_share.py``): reachable signed out, and the authenticated
  twin still answers 401/403 for the same caller.
- **The row shape matches the endpoint's own JSON contract exactly** —
  including the worked example in the task brief itself (RTX 4090 moving
  0.36 → 0.34, -5.6%, down), reproduced here against the REAL migration seed
  plus one added observation, so the contract's own numbers are pinned
  against the real implementation rather than only against invented
  fixtures.
- **Every trend direction** — up, down, flat, and the single-observation
  ``"new"`` case that must carry nothing else.
- **Tier preference**: community over secure, secure only as a fallback,
  and a SKU with neither is OMITTED — never zeroed.
- **Amounts are the vendor's own decimal strings**, unrounded, never floats.
- **Empty curated list still answers 200** with ``{"rows": []}``.
- **The cache**, since a route the brief made responsible for its own
  DB-protection is only actually protecting the database if the cache being
  pinned really is being served.

Fixture wiring follows ``test_prices.py`` for the pure ``prices.landing_rows``
tests (the ``db`` fixture, real migrated Postgres, isolated test SKUs built
with ``prices.record_quote`` the same way ``test_latest_quotes_returns_the_
newest_observation_not_the_first`` does) and ``test_public_job_share.py`` for
the one test that needs an HTTP client and the no-auth contrast.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from flashml_cloud_api import app as app_module
from flashml_cloud_api import prices

from test_jobs_from_repo import (  # noqa: F401 - fixtures
    db,
    make_client,
    settings,
    transport,
)
from test_prices import NOW, RUNPOD_CAPTURE


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sku(label: str) -> str:
    """A globally-unique test SKU. Unlike ``test_prices.py``'s shared
    ``"gpu-1"``, `landing_rows` matches on SKU alone (no provider filter —
    the task brief's ``(display_label, sku_matcher)`` shape has no provider
    field), so a SKU reused across tests in this session-scoped database
    would let one test's fixture answer another test's query. Uniqueness
    here is what keeps each test's curated tuple pointed at only its own
    rows."""
    return f"test-landing-{label}-{uuid.uuid4().hex[:8]}"


def _record(
    db, *, sku: str, amount: str, captured_at, tier="community",
    provider="test-landing",
):
    return prices.record_quote(
        db, provider=provider, sku=sku, region="global",
        currency="USD", amount=amount, unit="gpu-hour", tier=tier,
        captured_at=captured_at, source="test",
    )


@pytest.fixture(autouse=True)
def _reset_price_cache():
    """The route's cache is module-level and process-wide by design (see
    `app.py`'s own comment on `_public_prices_cache`), which means it is
    ALSO shared across every test in this process. Without this, a request
    made by one test can still be serving a stale, cached response to the
    next one inside the 60s TTL window — exactly the flakiness the task
    brief's "small reset hook" requirement exists to prevent."""
    app_module.reset_public_prices_cache()
    yield


# ---------------------------------------------------------------------------
# (a) no authentication, contrasted with the authenticated sibling route
# ---------------------------------------------------------------------------


def test_the_public_board_is_reachable_signed_out_and_the_sibling_is_not(
    make_client,
):
    """G-1's requirement, restated for the landing board: a route the
    marketing page calls before anybody has signed in must answer 200 with
    no `Authorization` header, on a product where every console route
    redirects to sign-in. Paired with the authenticated `/v1alpha1/prices`
    read, because the property IS the contrast between the two."""
    client = make_client()

    public = client.get("/v1alpha1/public/prices")
    assert public.status_code == 200
    assert "authorization" not in {k.lower() for k in public.request.headers}

    body = public.json()
    assert set(body) == {"generated_at", "rows"}
    assert body["generated_at"].endswith("Z")
    # The default curated list against the real seed: not empty, and one of
    # the labels the brief names is really on it.
    assert any(row["gpu"] == "RTX 4090" for row in body["rows"])

    assert client.get("/v1alpha1/prices").status_code in (401, 403)


# ---------------------------------------------------------------------------
# (b) row shape matches the contract exactly — the brief's own worked
#     example, reproduced against the real seed plus one added observation
# ---------------------------------------------------------------------------


def test_row_matches_the_contract_exactly_for_the_seeded_rtx_4090(db):
    """Migration 0019 seeds RTX 4090 community at 0.34, captured
    2026-08-12T03:55:00Z. Adding the one earlier observation the task
    brief's contract example assumes (0.36, a day before) reproduces that
    example's numbers verbatim against the real implementation, not only
    against an invented fixture."""
    previous_captured_at = RUNPOD_CAPTURE - timedelta(days=1)
    _record(
        db, sku="NVIDIA GeForce RTX 4090", amount="0.36",
        captured_at=previous_captured_at, provider="runpod",
    )

    rows = prices.landing_rows(db, NOW, prices.LANDING_GPUS)
    row = next(r for r in rows if r["sku"] == "NVIDIA GeForce RTX 4090")

    assert row == {
        "gpu": "RTX 4090",
        "sku": "NVIDIA GeForce RTX 4090",
        "provider": "runpod",
        "tier": "community",
        "amount": "0.34",
        "currency": "USD",
        "unit": "gpu-hour",
        "captured_at": "2026-08-12T03:55:00Z",
        "age_seconds": (NOW - RUNPOD_CAPTURE).total_seconds(),
        "stale": False,
        "trend": {
            "direction": "down",
            "pct": "-5.6",
            "previous_amount": "0.36",
            "previous_captured_at": "2026-08-11T03:55:00Z",
        },
    }


def test_rows_are_ordered_by_the_curated_lists_own_order(db):
    """Not sorted by anything the database would pick — the curated tuple's
    own order, reversed here to prove it is not incidentally alphabetical or
    incidentally the seed's insertion order."""
    a, b = _sku("a"), _sku("b")
    _record(db, sku=a, amount="1.00", captured_at=NOW - timedelta(hours=1))
    _record(db, sku=b, amount="2.00", captured_at=NOW - timedelta(hours=1))

    curated = (("Second", b), ("First", a))
    rows = prices.landing_rows(db, NOW, curated)
    assert [r["gpu"] for r in rows] == ["Second", "First"]


# ---------------------------------------------------------------------------
# trend: up, flat, and single-observation "new"
# ---------------------------------------------------------------------------


def test_trend_up_down_flat_and_new(db):
    up_sku, flat_sku, new_sku = _sku("up"), _sku("flat"), _sku("new")
    old = NOW - timedelta(days=2)
    recent = NOW - timedelta(hours=1)

    _record(db, sku=up_sku, amount="0.20", captured_at=old)
    _record(db, sku=up_sku, amount="0.25", captured_at=recent)

    _record(db, sku=flat_sku, amount="0.42", captured_at=old)
    _record(db, sku=flat_sku, amount="0.42", captured_at=recent)

    _record(db, sku=new_sku, amount="0.99", captured_at=recent)

    curated = (("Up", up_sku), ("Flat", flat_sku), ("New", new_sku))
    rows = {r["sku"]: r for r in prices.landing_rows(db, NOW, curated)}

    assert rows[up_sku]["trend"] == {
        "direction": "up",
        "pct": "+25.0",
        "previous_amount": "0.2",
        "previous_captured_at": prices._iso(old),
    }
    assert rows[flat_sku]["trend"] == {
        "direction": "flat",
        "pct": "0.0",
        "previous_amount": "0.42",
        "previous_captured_at": prices._iso(old),
    }
    # A single observation carries no previous_* fields at all — not null,
    # absent — per the contract's "new (single observation) carries no
    # other trend fields."
    assert rows[new_sku]["trend"] == {"direction": "new"}
    assert set(rows[new_sku]["trend"]) == {"direction"}


def test_a_captured_at_tie_is_not_treated_as_a_previous_observation(db):
    """"Previous DISTINCT captured_at" per the brief: two rows at the exact
    same instant are the same observation (or two sources agreeing), not a
    trend. Only a genuinely earlier row counts as `previous`."""
    sku = _sku("tie")
    tied_at = NOW - timedelta(hours=1)
    _record(db, sku=sku, amount="0.50", captured_at=tied_at)
    # A second source, same instant, different amount — a real scenario
    # `record_quote` allows since `source` differs.
    prices.record_quote(
        db, provider="test-landing", sku=sku, region="global",
        currency="USD", amount="0.55", unit="gpu-hour", tier="community",
        captured_at=tied_at, source="test-second-source",
    )

    rows = prices.landing_rows(db, NOW, (("Tie", sku),))
    assert rows[0]["trend"] == {"direction": "new"}


# ---------------------------------------------------------------------------
# (e) tier preference: community over secure, secure as fallback, absent
#     is omitted
# ---------------------------------------------------------------------------


def test_tier_preference_community_then_secure_then_omitted(db):
    both, secure_only, none = _sku("both"), _sku("secure-only"), _sku("none")
    when = NOW - timedelta(hours=1)

    _record(db, sku=both, amount="1.00", captured_at=when, tier="community")
    _record(db, sku=both, amount="1.50", captured_at=when, tier="secure")
    _record(db, sku=secure_only, amount="2.00", captured_at=when, tier="secure")
    # `none` has no quote at all.

    curated = (
        ("Both", both), ("Secure only", secure_only), ("Absent", none),
    )
    rows = prices.landing_rows(db, NOW, curated)

    assert [r["gpu"] for r in rows] == ["Both", "Secure only"]
    both_row = next(r for r in rows if r["sku"] == both)
    secure_row = next(r for r in rows if r["sku"] == secure_only)
    assert both_row["tier"] == "community"
    assert both_row["amount"] == "1"
    assert secure_row["tier"] == "secure"
    assert secure_row["amount"] == "2"


# ---------------------------------------------------------------------------
# (f) amounts are the vendor's exact strings, unrounded, never floats
# ---------------------------------------------------------------------------


def test_amounts_are_exact_unrounded_strings(db):
    sku = _sku("exact")
    _record(
        db, sku=sku, amount="0.00031896", captured_at=NOW - timedelta(hours=1)
    )

    rows = prices.landing_rows(db, NOW, (("Exact", sku),))
    amount = rows[0]["amount"]
    assert isinstance(amount, str)
    assert amount == "0.00031896"


def test_amounts_normalize_trailing_zeros_without_losing_digits(db):
    """`numeric(20, 10)` pads every stored value to ten decimal places; the
    padding must come back off without touching a real digit."""
    sku = _sku("padded")
    _record(
        db, sku=sku, amount="2.71828000", captured_at=NOW - timedelta(hours=1)
    )

    rows = prices.landing_rows(db, NOW, (("Padded", sku),))
    assert rows[0]["amount"] == "2.71828"


# ---------------------------------------------------------------------------
# (d) empty table (curated list produces no matches) still answers 200
# ---------------------------------------------------------------------------


def test_pure_empty_curated_list_returns_no_rows(db):
    rows = prices.landing_rows(db, NOW, ())
    assert rows == []


def test_a_curated_list_matching_nothing_returns_no_rows(db):
    rows = prices.landing_rows(
        db, NOW, (("Nothing here", _sku("no-match")),)
    )
    assert rows == []


def test_the_route_answers_200_with_empty_rows_when_nothing_is_curated(
    make_client, monkeypatch
):
    """The HTTP-level version of "empty table → {'rows': []}, still 200":
    the route must not error, 404, or change status just because the
    curated list it was built to serve happens to resolve to nothing."""
    client = make_client()
    monkeypatch.setattr(prices, "LANDING_GPUS", ())

    r = client.get("/v1alpha1/public/prices")
    assert r.status_code == 200
    assert r.json()["rows"] == []


# ---------------------------------------------------------------------------
# the cache: the task brief's own DB-protection measure
# ---------------------------------------------------------------------------


def test_the_cache_serves_the_same_payload_object_until_reset(make_client):
    """Not a timing assertion (flaky by construction) — an identity one. A
    second request inside the TTL must return the SAME payload dict the
    first request built, not a freshly computed one; after
    `reset_public_prices_cache`, a new request must build a new one."""
    client = make_client()

    first = client.get("/v1alpha1/public/prices")
    assert first.status_code == 200
    cached_value = app_module._public_prices_cache["value"]
    assert cached_value is not None

    second = client.get("/v1alpha1/public/prices")
    assert second.json() == first.json()
    # Still the exact same cached object — no second DB round trip happened.
    assert app_module._public_prices_cache["value"] is cached_value

    app_module.reset_public_prices_cache()
    assert app_module._public_prices_cache["value"] is None

    third = client.get("/v1alpha1/public/prices")
    assert third.status_code == 200
    assert app_module._public_prices_cache["value"] is not cached_value
