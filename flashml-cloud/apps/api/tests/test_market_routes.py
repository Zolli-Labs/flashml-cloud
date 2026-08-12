"""The marketplace's HTTP surface: seven browser routes over a repository
that has been tested without them since 2026-08-11.

The plan (2026-08-12-console-ui-plan.md §3.1) names the routes and the three
invariants the surface will not forgive, and this file pins both:

* a match is a PRICED ENTITLEMENT — ``GET /market/matches`` carries the state
  vocabulary (granted / claimed / settled / refunded / expired) verbatim,
  because a console that cannot show ``granted`` as "entitled, no money
  moved" will render it as an assignment;
* escrow holds on CLAIM — the ``/leases/claim`` proxy is the one hop that
  wires ``hold_escrow_on_claim``, asserted here end to end through the
  agent-tagged route rather than at repository level;
* settlement is on ACCEPTED work — the ``/attempts/{lease}/complete`` hop
  pays the host for what the coordinator accepted and releases the rest,
  and ``/fail`` refunds everything.

The ledger route pins the double-entry shape: every movement renders with
ALL of its legs, the viewer's and the counterparty's, because a feed that
collapses a movement into "balance went up" deletes the counterparty, and
the counterparty is the point.

Everything is built at runtime — users, machines, listings, bids — because
the owner has rejected fixture-shaped literals, and a credential-shaped
constant in a test is still one in the repo.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api import enrolment
from flashml_cloud_api import marketplace as marketmod
from flashml_cloud_api import prices as pricesmod
from flashml_cloud_api.app import create_cloud_app
from flashml_cloud_api.settings import Settings

JWT_SECRET = "test-jwt-secret-long-enough-for-hs256-abcdef"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def settings(postgres_dsn) -> Settings:
    return Settings(
        supabase_url="https://yualksqjjvlfscbbsygq.supabase.co",
        supabase_jwt_secret=JWT_SECRET,
        supabase_service_key="service-key-not-used-here",
        coordinator_url="http://coordinator.internal:8100",
        coordinator_operator_token="op-secret-do-not-leak-3f9c1b",
        require_auth=True,
        database_url=postgres_dsn,
        console_url="https://console.example",
    )


class ScriptedCoordinator(httpx.AsyncBaseTransport):
    """The coordinator hops the agent routes proxy, with canned answers.

    ``claim`` answers one lease whose ids the test set; ``complete`` answers
    ``{"accepted": true}``; ``fail`` answers an empty 200. Anything else is a
    test bug, not a deployment condition, so it raises rather than 404s.
    """

    def __init__(self) -> None:
        self.claim_body: dict | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1alpha1/leases/claim" and self.claim_body is not None:
            return httpx.Response(200, json=self.claim_body)
        if path.endswith("/complete"):
            return httpx.Response(200, json={"accepted": True})
        if path.endswith("/fail"):
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected coordinator call: {request.method} {path}")


@pytest.fixture
def coordinator() -> ScriptedCoordinator:
    return ScriptedCoordinator()


@pytest.fixture
def client(settings, postgres_dsn, coordinator):
    def connect() -> psycopg.Connection:
        conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        return conn

    app = create_cloud_app(settings, connect=connect, transport=coordinator)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def _new_user(db, *, admitted: bool = True) -> str:
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (%s)", (user_id,))
        cur.execute(
            "insert into public.profiles (id, admitted_at) values (%s, %s)",
            (user_id, "now()" if admitted else None),
        )
    return user_id


def _browser_jwt(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": time.time() + 3600},
        JWT_SECRET,
        algorithm="HS256",
    )


def _auth(user_id: str) -> dict:
    return {"Authorization": f"Bearer {_browser_jwt(user_id)}"}


def _machine(db, owner: str, *, capabilities: dict | None = None) -> str:
    """A machine row with the capabilities snapshot the ladder reads."""
    with db.cursor() as cur:
        cur.execute(
            "insert into public.machines (owner_id, node_id, name, status,"
            " capabilities)"
            " values (%s, %s, %s, 'active', %s) returning id",
            (
                owner,
                f"mkt-{uuid.uuid4().hex[:10]}",
                "laptop",
                json.dumps(capabilities or {}),
            ),
        )
        return str(cur.fetchone()["id"])


GPU_24GB = {"gpus": [{"memory_total_mb": 24564}]}


def _enrolled_machine(db, owner: str, *, capabilities: dict | None = None):
    """A machine through the real device flow, then a capabilities snapshot.

    Enrolment CREATES the machine row (a pre-existing row with the same
    node_id is NodeAlreadyEnrolled), so the capabilities the ladder reads
    are written onto the row the flow produced, afterwards.
    """
    node_id = f"mkt-{uuid.uuid4().hex[:10]}"
    started = enrolment.start_device_code(db, node_id, "laptop", "linux")
    enrolment.approve_device_code(db, started["user_code"], owner)
    token = enrolment.redeem_device_code(db, started["device_code"])
    with db.cursor() as cur:
        cur.execute(
            "select id from public.machines where node_id = %s", (node_id,)
        )
        machine_id = str(cur.fetchone()["id"])
        if capabilities is not None:
            cur.execute(
                "update public.machines set capabilities = %s"
                " where id = %s",
                (json.dumps(capabilities), machine_id),
            )
    return machine_id, token


def _listed_match(db, *, host: str, buyer: str, ask: int = 1000,
                  machine_id: str | None = None):
    """One open listing, one bid, one granted match — built at runtime.

    Returns (machine_id, listing, bid, match). The match is granted and
    stays granted until a claim moves it, which is exactly the surface the
    routes under test are meant to wire. ``machine_id`` lets a caller hand
    in an enrolled machine (the claim tests need its token).
    """
    if machine_id is None:
        machine_id = _machine(db, host, capabilities=GPU_24GB)
    listing = marketmod.create_listing(
        db, machine_id=machine_id, owner_id=host, ask_zc_per_hour=ask
    )
    bid = marketmod.create_bid(
        db,
        job_id=f"mktjob-{uuid.uuid4().hex[:10]}",
        owner_id=buyer,
        capability_class_name="gpu-24gb",
        max_zc_per_hour=ask,
        tasks_wanted=1,
        est_task_seconds=3600,
    )
    matches = marketmod.grant_matches(
        db,
        bid_id=str(bid["id"]),
        plan=marketmod.MatchPlan(
            fills=(
                marketmod.Fill(
                    listing_id=str(listing["id"]),
                    machine_id=machine_id,
                    host_id=host,
                    tasks=1,
                    agreed_zc_per_hour=ask,
                    unproven_host=True,
                ),
            ),
            tasks_filled=1,
            tasks_unfilled=0,
            unproven_tasks=1,
            unproven_task_cap=1,
        ),
    )
    return machine_id, listing, bid, matches[0]


# ---------------------------------------------------------------------------
# GET /v1alpha1/credits
# ---------------------------------------------------------------------------


def test_credits_requires_sign_in(client):
    assert client.get("/v1alpha1/credits").status_code == 401


def test_first_read_grants_the_starting_credits_once(client, db):
    got = client.get("/v1alpha1/credits", headers=_auth(_new_user(db)))
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["spendable_zc"] == marketmod.STARTING_GRANT_ZC
    assert body["held_zc"] == 0


def test_a_second_read_is_identical_so_the_grant_is_one_time(client, db):
    user = _new_user(db)
    first = client.get("/v1alpha1/credits", headers=_auth(user)).json()
    second = client.get("/v1alpha1/credits", headers=_auth(user)).json()
    assert first == second


def test_held_tracks_an_escrow_hold(client, db):
    host, buyer = _new_user(db), _new_user(db)
    _ = client.get("/v1alpha1/credits", headers=_auth(buyer))  # open accounts
    _, _, _, match = _listed_match(db, host=host, buyer=buyer)
    marketmod.hold_escrow_on_claim(db, match_id=str(match["id"]), lease_id="lease-1")

    body = client.get("/v1alpha1/credits", headers=_auth(buyer)).json()
    assert body["held_zc"] == 1000
    assert body["spendable_zc"] == marketmod.STARTING_GRANT_ZC - 1000


# ---------------------------------------------------------------------------
# GET /v1alpha1/credits/ledger
# ---------------------------------------------------------------------------


def test_ledger_requires_sign_in(client):
    assert client.get("/v1alpha1/credits/ledger").status_code == 401


def test_ledger_is_empty_not_absent_for_a_new_account(client, db):
    got = client.get("/v1alpha1/credits/ledger", headers=_auth(_new_user(db)))
    assert got.status_code == 200
    assert got.json() == {"movements": [], "next_before": None}


def test_the_grant_movement_is_a_single_minting_leg(client, db):
    user = _new_user(db)
    client.get("/v1alpha1/credits", headers=_auth(user))
    movements = client.get(
        "/v1alpha1/credits/ledger", headers=_auth(user)
    ).json()["movements"]
    assert len(movements) == 1
    grant = movements[0]
    assert grant["reason"] == "grant"
    assert len(grant["legs"]) == 1
    leg = grant["legs"][0]
    assert leg["kind"] == "spendable"
    assert leg["mine"] is True
    assert leg["delta_zc"] == marketmod.STARTING_GRANT_ZC


def test_a_hold_movement_carries_both_legs_of_the_pair(client, db):
    host, buyer = _new_user(db), _new_user(db)
    client.get("/v1alpha1/credits", headers=_auth(buyer))
    _, _, _, match = _listed_match(db, host=host, buyer=buyer)
    marketmod.hold_escrow_on_claim(db, match_id=str(match["id"]), lease_id="lease-2")

    movements = client.get(
        "/v1alpha1/credits/ledger", headers=_auth(buyer)
    ).json()["movements"]
    hold = next(m for m in movements if m["reason"] == "escrow_hold")
    kinds = {(leg["kind"], leg["delta_zc"]) for leg in hold["legs"]}
    assert kinds == {("spendable", -1000), ("escrow", 1000)}
    assert all(leg["mine"] for leg in hold["legs"])


def test_the_counterparty_leg_is_marked_not_mine_on_the_host_side(client, db):
    host, buyer = _new_user(db), _new_user(db)
    client.get("/v1alpha1/credits", headers=_auth(buyer))
    client.get("/v1alpha1/credits", headers=_auth(host))
    _, _, _, match = _listed_match(db, host=host, buyer=buyer)
    marketmod.hold_escrow_on_claim(db, match_id=str(match["id"]), lease_id="lease-3")
    marketmod.settle_accepted_work(
        db, match_id=str(match["id"]), lease_id="lease-3", accepted_seconds=3600
    )

    movements = client.get(
        "/v1alpha1/credits/ledger", headers=_auth(host)
    ).json()["movements"]
    earned = next(m for m in movements if m["reason"] == "earned_accepted_work")
    mine = [leg for leg in earned["legs"] if leg["mine"]]
    theirs = [leg for leg in earned["legs"] if not leg["mine"]]
    assert len(mine) == 1 and mine[0]["kind"] == "spendable"
    assert mine[0]["delta_zc"] == 1000
    assert len(theirs) == 1 and theirs[0]["kind"] == "escrow"


def test_ledger_pages_newest_first_without_repeats(client, db):
    user = _new_user(db)
    host = _new_user(db)
    client.get("/v1alpha1/credits", headers=_auth(user))  # the grant
    _, _, _, match = _listed_match(db, host=host, buyer=user)
    marketmod.hold_escrow_on_claim(db, match_id=str(match["id"]), lease_id="lease-4")

    first = client.get(
        "/v1alpha1/credits/ledger", headers=_auth(user), params={"limit": 1}
    ).json()
    assert len(first["movements"]) == 1
    assert first["movements"][0]["reason"] == "escrow_hold"
    assert first["next_before"] is not None

    second = client.get(
        "/v1alpha1/credits/ledger",
        headers=_auth(user),
        params={"limit": 1, "before": first["next_before"]},
    ).json()
    assert [m["reason"] for m in second["movements"]] == ["grant"]


# ---------------------------------------------------------------------------
# listings
# ---------------------------------------------------------------------------


def test_listings_requires_sign_in(client):
    assert client.get("/v1alpha1/market/listings").status_code == 401


def test_a_new_host_sees_no_listings_of_their_own(client, db):
    """``mine`` is owner-scoped, so a fresh account's is empty whatever the
    market holds; and none of the open asks may be hosted by it."""
    user = _new_user(db)
    body = client.get(
        "/v1alpha1/market/listings", headers=_auth(user)
    ).json()
    assert body["mine"] == []
    assert not any(ask["host_id"] == user for ask in body["asks"])


def test_listing_a_machine_requires_admission(client, db):
    user = _new_user(db, admitted=False)
    machine = _machine(db, user, capabilities=GPU_24GB)
    r = client.post(
        "/v1alpha1/market/listings",
        headers=_auth(user),
        json={"machine_id": machine, "ask_zc_per_hour": 100},
    )
    assert r.status_code == 403


def test_listing_an_unknown_machine_is_404(client, db):
    user = _new_user(db)
    r = client.post(
        "/v1alpha1/market/listings",
        headers=_auth(user),
        json={"machine_id": str(uuid.uuid4()), "ask_zc_per_hour": 100},
    )
    assert r.status_code == 404


def test_listing_somebody_elses_machine_is_the_same_404(client, db):
    owner, stranger = _new_user(db), _new_user(db)
    machine = _machine(db, owner, capabilities=GPU_24GB)
    r = client.post(
        "/v1alpha1/market/listings",
        headers=_auth(stranger),
        json={"machine_id": machine, "ask_zc_per_hour": 100},
    )
    assert r.status_code == 404


def test_a_negative_ask_is_400(client, db):
    user = _new_user(db)
    machine = _machine(db, user, capabilities=GPU_24GB)
    r = client.post(
        "/v1alpha1/market/listings",
        headers=_auth(user),
        json={"machine_id": machine, "ask_zc_per_hour": -1},
    )
    assert r.status_code == 400


def test_an_unclassifiable_machine_is_refused_not_guessed(client, db):
    user = _new_user(db)
    machine = _machine(db, user, capabilities={"gpus": [{"name": "mystery"}]})
    r = client.post(
        "/v1alpha1/market/listings",
        headers=_auth(user),
        json={"machine_id": machine, "ask_zc_per_hour": 100},
    )
    assert r.status_code == 409


def test_a_listing_is_classified_from_the_reported_capabilities(client, db):
    user = _new_user(db)
    machine = _machine(db, user, capabilities=GPU_24GB)
    r = client.post(
        "/v1alpha1/market/listings",
        headers=_auth(user),
        json={"machine_id": machine, "ask_zc_per_hour": 0},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["capability_class"] == "gpu-24gb"
    assert body["donated"] is True
    assert body["price_label"] == marketmod.price_label(0)


def test_the_book_shows_open_asks_with_their_record(client, db):
    host, buyer = _new_user(db), _new_user(db)
    machine = _machine(db, host, capabilities=GPU_24GB)
    created = marketmod.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=1200
    )

    body = client.get(
        "/v1alpha1/market/listings", headers=_auth(buyer)
    ).json()
    ask = next(a for a in body["asks"] if a["id"] == str(created["id"]))
    assert ask["capability_class"] == "gpu-24gb"
    assert ask["ask_zc_per_hour"] == 1200
    assert ask["donated"] is False
    # Unproven host: the rate is null, never an invented number.
    assert ask["acceptance_rate"] is None
    assert body["mine"] == []

    mine = client.get(
        "/v1alpha1/market/listings", headers=_auth(host)
    ).json()["mine"]
    assert str(created["id"]) in [m["id"] for m in mine]


def test_withdraw_removes_the_ask_and_a_second_withdraw_is_404(client, db):
    host, buyer = _new_user(db), _new_user(db)
    machine = _machine(db, host, capabilities=GPU_24GB)
    listing = marketmod.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=500
    )

    r = client.delete(
        f"/v1alpha1/market/listings/{listing['id']}", headers=_auth(host)
    )
    assert r.status_code == 200
    assert client.delete(
        f"/v1alpha1/market/listings/{listing['id']}", headers=_auth(host)
    ).status_code == 404
    # Somebody else's live listing is the same 404, not a 403.
    machine2 = _machine(db, host, capabilities=GPU_24GB)
    listing2 = marketmod.create_listing(
        db, machine_id=machine2, owner_id=host, ask_zc_per_hour=500
    )
    assert client.delete(
        f"/v1alpha1/market/listings/{listing2['id']}", headers=_auth(buyer)
    ).status_code == 404
    asks = client.get(
        "/v1alpha1/market/listings", headers=_auth(buyer)
    ).json()["asks"]
    ids = [a["id"] for a in asks]
    assert str(listing["id"]) not in ids
    assert str(listing2["id"]) in ids


# ---------------------------------------------------------------------------
# GET /v1alpha1/market/matches
# ---------------------------------------------------------------------------


def test_matches_requires_sign_in(client):
    assert client.get("/v1alpha1/market/matches").status_code == 401


def test_matches_are_empty_not_absent(client, db):
    got = client.get("/v1alpha1/market/matches", headers=_auth(_new_user(db)))
    assert got.status_code == 200
    assert got.json() == {"as_buyer": [], "as_host": []}


def test_a_match_appears_on_both_sides_with_its_state(client, db):
    host, buyer = _new_user(db), _new_user(db)
    _, _, _, match = _listed_match(db, host=host, buyer=buyer)

    body = client.get("/v1alpha1/market/matches", headers=_auth(buyer)).json()
    assert len(body["as_buyer"]) == 1
    assert body["as_buyer"][0]["id"] == str(match["id"])
    assert body["as_buyer"][0]["state"] == "granted"
    assert body["as_host"] == []

    body = client.get("/v1alpha1/market/matches", headers=_auth(host)).json()
    assert len(body["as_host"]) == 1
    assert body["as_host"][0]["agreed_zc_per_hour"] == 1000
    assert body["as_buyer"] == []


# ---------------------------------------------------------------------------
# GET /v1alpha1/prices
# ---------------------------------------------------------------------------


def test_prices_requires_sign_in(client):
    assert client.get("/v1alpha1/prices").status_code == 401


def test_every_quote_carries_its_provenance(client, db):
    body = client.get(
        "/v1alpha1/prices", headers=_auth(_new_user(db))
    ).json()
    assert body["quotes"], "the 0019 seed rows should be present"
    for quote in body["quotes"]:
        assert isinstance(quote["captured_at"], str) and quote["captured_at"]
        assert isinstance(quote["source"], str) and quote["source"]
        assert isinstance(quote["stale"], bool)
        assert isinstance(quote["amount"], str)


def test_venues_with_no_quote_render_not_observed(client, db):
    body = client.get(
        "/v1alpha1/prices", headers=_auth(_new_user(db))
    ).json()
    unpriced = {row["provider"] for row in body["unpriced"]}
    # The workspace has no scraped price, and no published fc-gpu number has
    # ever been cited; both must render as not observed, never as 0.
    assert {"owned", "fc-gpu"} <= unpriced
    for row in body["unpriced"]:
        assert row["amount"] is None
        assert row["state"] == "not observed"


def test_a_fresh_quote_is_not_stale(client, db):
    """Under its own provider: recording into a seeded series would make
    this test's "now" the newest observation of that series for every
    other test in the session, and their staleness math is pinned to the
    seed's capture time."""
    user = _new_user(db)
    pricesmod.record_quote(
        db,
        provider="test-venue",
        sku="example-gpu",
        region="global",
        currency="USD",
        amount="0.34",
        unit="gpu-hour",
        captured_at=datetime.now(timezone.utc),
        source="test capture",
    )
    body = client.get("/v1alpha1/prices", headers=_auth(user)).json()
    fresh = [q for q in body["quotes"] if q["provider"] == "test-venue"]
    assert len(fresh) == 1
    assert fresh[0]["stale"] is False
    assert fresh[0]["source"] == "test capture"


def test_the_zc_ladder_matches_the_live_book_and_never_zero_fills(
    client, db
):
    """The ZC side of the comparison: the reference ladder verbatim, and
    the best live ask per class — null where the book is empty, never 0,
    because a zero ask is legal and means donated."""
    user = _new_user(db)
    body = client.get("/v1alpha1/prices", headers=_auth(user)).json()
    asks = client.get(
        "/v1alpha1/market/listings", headers=_auth(user)
    ).json()["asks"]

    zc = {row["capability_class"]: row for row in body["zc"]}
    assert set(zc) == set(marketmod.CAPABILITY_CLASSES)
    for klass, row in zc.items():
        assert row["reference_zc_per_hour"] == (
            marketmod.REFERENCE_ZC_PER_HOUR[klass]
        )
        in_class = [a["ask_zc_per_hour"] for a in asks if a["capability_class"] == klass]
        assert row["best_ask_zc"] == (min(in_class) if in_class else None)


# ---------------------------------------------------------------------------
# the claim hop holds escrow (and only then)
# ---------------------------------------------------------------------------


def _claim(client, coordinator, token: str, *, lease_id: str, job_id: str,
           task_id: str = "task-000"):
    coordinator.claim_body = {
        "lease_id": lease_id,
        "job_id": job_id,
        "task_id": task_id,
        "deadline": None,
    }
    return client.post(
        "/v1alpha1/leases/claim",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )


def test_claiming_under_a_granted_match_holds_the_buyers_escrow(
    client, db, coordinator
):
    host, buyer = _new_user(db), _new_user(db)
    client.get("/v1alpha1/credits", headers=_auth(buyer))
    machine_id, token = _enrolled_machine(db, host, capabilities=GPU_24GB)
    _, _, bid, match = _listed_match(
        db, host=host, buyer=buyer, machine_id=machine_id
    )

    r = _claim(
        client, coordinator, token,
        lease_id=f"lease-{uuid.uuid4().hex[:8]}", job_id=str(bid["job_id"]),
    )
    assert r.status_code == 200, r.text

    body = client.get("/v1alpha1/credits", headers=_auth(buyer)).json()
    assert body["held_zc"] == 1000
    with db.cursor() as cur:
        cur.execute(
            "select state from public.matches where id = %s", (match["id"],)
        )
        assert cur.fetchone()["state"] == "claimed"


def test_claiming_without_a_match_moves_no_money(client, db, coordinator):
    host, buyer = _new_user(db), _new_user(db)
    client.get("/v1alpha1/credits", headers=_auth(buyer))
    machine_id, token = _enrolled_machine(db, host)

    r = _claim(
        client, coordinator, token,
        lease_id=f"lease-{uuid.uuid4().hex[:8]}", job_id="unmatched-job",
    )
    assert r.status_code == 200

    body = client.get("/v1alpha1/credits", headers=_auth(buyer)).json()
    assert body["held_zc"] == 0
    assert body["spendable_zc"] == marketmod.STARTING_GRANT_ZC


def test_a_retried_claim_holds_once(client, db, coordinator):
    host, buyer = _new_user(db), _new_user(db)
    client.get("/v1alpha1/credits", headers=_auth(buyer))
    machine_id, token = _enrolled_machine(db, host, capabilities=GPU_24GB)
    _, _, bid, _ = _listed_match(
        db, host=host, buyer=buyer, machine_id=machine_id
    )
    lease = f"lease-{uuid.uuid4().hex[:8]}"

    assert _claim(client, coordinator, token, lease_id=lease,
                  job_id=str(bid["job_id"])).status_code == 200
    assert _claim(client, coordinator, token, lease_id=lease,
                  job_id=str(bid["job_id"])).status_code == 200

    body = client.get("/v1alpha1/credits", headers=_auth(buyer)).json()
    assert body["held_zc"] == 1000


# ---------------------------------------------------------------------------
# settlement is on accepted work; failure refunds everything
# ---------------------------------------------------------------------------


def _held_claim(client, db, coordinator, host, buyer):
    """One claimed match with a lease, ready for the completion hop."""
    client.get("/v1alpha1/credits", headers=_auth(buyer))
    machine_id, token = _enrolled_machine(db, host, capabilities=GPU_24GB)
    _, _, bid, match = _listed_match(
        db, host=host, buyer=buyer, machine_id=machine_id
    )
    lease = f"lease-{uuid.uuid4().hex[:8]}"
    r = _claim(client, coordinator, token, lease_id=lease,
               job_id=str(bid["job_id"]))
    assert r.status_code == 200
    return machine_id, token, lease, match


def test_accepted_work_pays_the_host_and_releases_nothing_extra(
    client, db, coordinator
):
    host, buyer = _new_user(db), _new_user(db)
    machine_id, token, lease, _ = _held_claim(
        client, db, coordinator, host, buyer
    )
    # An hour of lease-held wall clock, so the charge is one hour at the
    # agreed 1000 mzc/h rather than a sub-second zero.
    with db.cursor() as cur:
        cur.execute(
            "update public.attempts set claimed_at = now() - interval '1 hour'"
            " where lease_id = %s",
            (lease,),
        )

    r = client.post(
        f"/v1alpha1/attempts/{lease}/complete",
        headers={"Authorization": f"Bearer {token}"},
        json={"output_sha256": "0" * 64},
    )
    assert r.status_code == 200, r.text

    host_credits = client.get("/v1alpha1/credits", headers=_auth(host)).json()
    buyer_credits = client.get("/v1alpha1/credits", headers=_auth(buyer)).json()
    assert host_credits["spendable_zc"] == (
        marketmod.STARTING_GRANT_ZC + 1000
    )
    assert buyer_credits["held_zc"] == 0
    assert buyer_credits["spendable_zc"] == marketmod.STARTING_GRANT_ZC - 1000


def test_a_failed_attempt_refunds_the_buyer_and_earns_nothing(
    client, db, coordinator
):
    host, buyer = _new_user(db), _new_user(db)
    machine_id, token, lease, _ = _held_claim(
        client, db, coordinator, host, buyer
    )

    r = client.post(
        f"/v1alpha1/attempts/{lease}/fail",
        headers={"Authorization": f"Bearer {token}"},
        json={},
    )
    assert r.status_code == 200, r.text

    buyer_credits = client.get("/v1alpha1/credits", headers=_auth(buyer)).json()
    host_credits = client.get("/v1alpha1/credits", headers=_auth(host)).json()
    assert buyer_credits["held_zc"] == 0
    assert buyer_credits["spendable_zc"] == marketmod.STARTING_GRANT_ZC
    assert host_credits["spendable_zc"] == marketmod.STARTING_GRANT_ZC


# ---------------------------------------------------------------------------
# v2 market terminal contract
# ---------------------------------------------------------------------------


def test_machine_gpu_label_reads_only_what_the_agent_reported():
    assert marketmod.machine_gpu_label(
        {"gpus": [{"name": "NVIDIA GeForce RTX 4090", "memory_total_mb": 24564}]}
    ) == "NVIDIA GeForce RTX 4090 · 24 GB"
    assert marketmod.machine_gpu_label({"cpu_cores": 8}) == "8 cores"
    # Unreadable memory is refused, same direction as the class ladder.
    assert marketmod.machine_gpu_label(
        {"gpus": [{"name": "x", "memory_total_mb": None}]}
    ) is None
    assert marketmod.machine_gpu_label({}) is None


def test_credits_carry_lifetime_sums_out_of_the_ledger(client, db, coordinator):
    host, buyer = _new_user(db), _new_user(db)
    client.get("/v1alpha1/credits", headers=_auth(buyer))
    machine_id, token = _enrolled_machine(db, host, capabilities=GPU_24GB)
    _, _, bid, _ = _listed_match(
        db, host=host, buyer=buyer, machine_id=machine_id
    )
    coordinator_lease = f"lease-{uuid.uuid4().hex[:8]}"
    _claim(client, coordinator, token, lease_id=coordinator_lease,
           job_id=str(bid["job_id"]))
    with db.cursor() as cur:
        cur.execute(
            "update public.attempts set claimed_at = now() - interval '1 hour'"
            " where lease_id = %s",
            (coordinator_lease,),
        )
    client.post(
        f"/v1alpha1/attempts/{coordinator_lease}/complete",
        headers={"Authorization": f"Bearer {token}"},
        json={"output_sha256": "0" * 64},
    )

    host_credits = client.get("/v1alpha1/credits", headers=_auth(host)).json()
    buyer_credits = client.get("/v1alpha1/credits", headers=_auth(buyer)).json()
    assert host_credits["lifetime"]["earned_zc"] == 1000
    assert buyer_credits["lifetime"]["spent_zc"] == 1000
    assert buyer_credits["lifetime"]["granted_zc"] == (
        marketmod.STARTING_GRANT_ZC
    )


def test_listings_carry_spec_line_and_effective_price(client, db):
    host, buyer = _new_user(db), _new_user(db)
    machine = _machine(
        db, host,
        capabilities={"gpus": [
            {"name": "NVIDIA GeForce RTX 4090", "memory_total_mb": 24564}
        ]},
    )
    marketmod.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=1000
    )
    body = client.get(
        "/v1alpha1/market/listings", headers=_auth(buyer)
    ).json()
    ask = next(a for a in body["asks"] if a["machine_id"] == machine)
    assert ask["gpu_label"] == "NVIDIA GeForce RTX 4090 · 24 GB"
    # Unproven host: effective price is null, never a fabricated number.
    assert ask["acceptance_rate"] is None
    assert ask["effective_zc_per_hour"] is None


CPU_ONLY = {"cpu_cores": 4}  # lands in cpu-small, a class no other test lists


def test_market_hint_composes_class_book_and_record(client, db):
    host = _new_user(db)
    machine = _machine(db, host, capabilities=CPU_ONLY)
    marketmod.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=1200
    )
    got = client.get(
        f"/v1alpha1/machines/{machine}/market-hint", headers=_auth(host)
    )
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["capability_class"] == "cpu-small"
    assert body["book"]["best_ask_zc"] == 1200
    assert body["book"]["median_ask_zc"] == 1200
    assert body["book"]["reference_zc_per_hour"] == (
        marketmod.REFERENCE_ZC_PER_HOUR["cpu-small"]
    )


def test_market_hint_is_404_for_a_stranger(client, db):
    host, stranger = _new_user(db), _new_user(db)
    machine = _machine(db, host, capabilities=GPU_24GB)
    got = client.get(
        f"/v1alpha1/machines/{machine}/market-hint", headers=_auth(stranger)
    )
    assert got.status_code == 404


def test_prices_board_carries_history_depth_and_change(client, db):
    host = _new_user(db)
    machine = _machine(db, host, capabilities=CPU_ONLY)
    marketmod.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=900
    )
    body = client.get(
        "/v1alpha1/prices", headers=_auth(_new_user(db))
    ).json()
    row = next(r for r in body["zc"] if r["capability_class"] == "cpu-small")
    # The hint test may also list cpu-small in the same session, so the
    # book can hold more than this test's ask; the min is still ours.
    assert row["best_ask_zc"] == 900
    assert row["depth"] >= 1
    assert row["history"], "listing a machine records an observation"
    assert row["history"][0]["best_ask_zc"] == 900
    # A single observation has no 24h-ago pair: change is null, not 0.
    assert row["change_zc"] is None
    assert body["board"]["open_asks_total"] >= 1
    assert body["board"]["live_classes"] >= 1
