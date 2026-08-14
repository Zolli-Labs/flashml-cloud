"""Automatic location detection: the guesses it is allowed to make, and the
four it is not.

This feature exists in direct tension with migration 0029, which added the geo
columns and said in its own header that a detected value would never be one of
them. 0031 reversed that, and the reversal is only safe because of a small
number of properties that are easy to state and easy to lose. Each of the
sections below pins one of them, and each corresponds to a specific way this
could ship something worse than the blank map it replaces:

* **PRECEDENCE.** ``declared > venue > detected``. A host who typed a country
  must never see it replaced by a guess — not on the next sweep, not a week
  later, and not because a declaration landed in the seconds while the sweep
  was waiting on a third party. The guard is a predicate in the UPDATE's WHERE
  clause, which is the only kind that cannot be forgotten.

* **NO CITY, EVER.** A country is a fact about a network; a city is a fact
  about a person, and a volunteer donated compute rather than consenting to be
  placed on a street map. ``GeoResult`` has no city field and the UPDATE writes
  ``geo_city = null`` explicitly, so this is a property of the row rather than
  of anyone's intentions. Coordinates are coarsened to ~11km for the same
  reason: the evidence is a routing hop, and precision that is not evidence is
  a lie with a decimal point.

* **THE BUDGET IS REAL.** The sweep holds a database connection across
  third-party HTTP, and this deployment has exhausted its Postgres pooler
  before. A budget that silently did not bound the work would turn a slow
  provider into an outage.

* **A FAILURE IS A RESULT.** An unresolvable address stamps ``geo_checked_at``
  and is left alone for a week. Without that, one bad row consumes the whole
  budget on every tick for ever and the rest of the fleet never gets looked at.

* **OFF IS OFF.** ``FLASHML_GEOIP_PROVIDER`` is unset everywhere today, and
  with it unset nothing here reads a row or opens a socket.

NOTHING IN THIS FILE TOUCHES THE NETWORK. Every provider response is served by
an ``httpx.MockTransport`` injected through ``IpWhoIsProvider(client=...)``, the
same seam ``test_repo.py`` uses for ``fetch_repo_tarball``.

The database is shared with the rest of the suite and every sweep read is
FLEET-WIDE, so ``_only_these_candidates`` below clears ``last_seen_ip``
everywhere before each test. Nothing else in the suite writes that column
(``client_ip`` answers ``None`` for the ``TestClient`` peer, as it does for any
private address), so in practice it only isolates these tests from each other —
but a budget assertion that another module could steal from is not an
assertion.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import httpx
import pytest

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import geoip
from flashml_cloud_api import network

from test_jobs_from_repo import (  # noqa: F401 - fixtures
    _new_user,
    db,
    make_client,
    settings,
    transport,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _only_these_candidates(db):
    """No machine outside the test that is running may be a sweep candidate.

    Fleet-wide reads plus a shared database plus a budget assertion is a test
    that passes until somebody adds a fixture elsewhere. Clearing the column
    the sweep filters on is the cheapest way to make "the candidates are the
    ones I just created" true by construction rather than by luck.
    """
    with db.cursor() as cur:
        cur.execute("update public.machines set last_seen_ip = null")


def _machine(
    db,
    owner: str,
    *,
    ip: str | None = "8.8.8.8",
    status: str = "active",
    checked_days_ago: float | None = None,
) -> str:
    machine_id = dbmod.insert_machine(
        db,
        owner_id=owner,
        node_id=f"fn-{uuid.uuid4().hex[:16]}",
        name="a laptop",
        platform="linux",
    )
    with db.cursor() as cur:
        cur.execute(
            """
            update public.machines
               set status = %s,
                   last_seen_at = now(),
                   last_seen_ip = %s,
                   geo_checked_at = case when %s::interval is null then null
                                         else now() - %s::interval end
             where id = %s
            """,
            (
                status,
                ip,
                None if checked_days_ago is None else timedelta(days=checked_days_ago),
                None if checked_days_ago is None else timedelta(days=checked_days_ago),
                machine_id,
            ),
        )
    return machine_id


def _geo(db, machine_id: str) -> dict:
    with db.cursor() as cur:
        cur.execute(
            """
            select geo_country, geo_region, geo_city, geo_lat, geo_lon,
                   geo_source, geo_checked_at, last_seen_ip
              from public.machines
             where id = %s
            """,
            (machine_id,),
        )
        return cur.fetchone()


class RecordingProvider:
    """A provider that answers whatever it was told to, and remembers who
    asked. Standing in for the network at the PROVIDER seam rather than the
    HTTP one, so a sweep test is about the sweep — the HTTP contract has its
    own section below, against a real ``IpWhoIsProvider``."""

    def __init__(self, result: geoip.GeoResult | None = None, results: dict | None = None):
        self.result = result
        self.results = results or {}
        self.asked: list[str] = []

    def lookup(self, ip: str) -> geoip.GeoResult | None:
        self.asked.append(ip)
        if self.results:
            return self.results.get(ip)
        return self.result


def _munich() -> geoip.GeoResult:
    return geoip.GeoResult(country="DE", region="Bavaria", lat=48.1, lon=11.6)


def _ipwho(handler) -> geoip.IpWhoIsProvider:
    return geoip.IpWhoIsProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def _ok_payload(**overrides) -> dict:
    payload = {
        "success": True,
        "ip": "8.8.8.8",
        "country_code": "DE",
        "country": "Germany",
        "region": "Bavaria",
        "city": "Munich",
        "latitude": 48.1372,
        "longitude": 11.5756,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# the address: what may be recorded at all
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.4",           # RFC1918
        "10.255.255.255",
        "172.16.0.1",         # the 172.16/12 block, whose edges are the
        "172.31.255.254",     # ones a hand-rolled string check gets wrong
        "192.168.1.10",
        "127.0.0.1",          # every dev run
        "::1",
        "169.254.10.1",       # link-local
        "0.0.0.0",
        # RFC5737 / RFC3849 documentation ranges. Python's `ipaddress` files
        # these under `is_private`, which is why every address in this file is
        # a real public one — the natural choice of 203.0.113.x for a test is
        # silently filtered, and a suite written on it would assert that
        # detection works while never once exercising a lookup.
        "203.0.113.7",
        "198.51.100.1",
        "192.0.2.1",
        "2001:db8::1",
        "not-an-address",
        "",
        "   ",
        None,
        7,
    ],
)
def test_an_address_we_have_no_business_geolocating_is_not_recorded(address):
    """``None`` means the column is not written. Geolocating an RFC1918 address
    is meaningless, and without this filter every dev run and every e2e pass
    would fill ``last_seen_ip`` with 127.0.0.1 — so the first time anybody
    asked whether detection worked, the answer would be a column full of the
    developer's own laptop."""
    assert geoip.public_ip(address) is None


@pytest.mark.parametrize(
    "address,expected",
    [
        ("8.8.8.8", "8.8.8.8"),
        ("  8.8.8.8  ", "8.8.8.8"),
        ("2606:4700:4700::1111", "2606:4700:4700::1111"),
        ("[2606:4700:4700::1111]", "2606:4700:4700::1111"),
    ],
)
def test_a_public_address_is_recorded_as_given(address, expected):
    assert geoip.public_ip(address) == expected


class _Client:
    def __init__(self, host):
        self.host = host


class _Request:
    def __init__(self, headers=None, host=None):
        self.headers = headers or {}
        self.client = _Client(host) if host is not None else None


def test_the_first_forwarded_hop_wins_because_it_is_the_agent():
    """Behind Render's proxy the socket peer is the proxy. The FIRST hop is the
    original client; the later ones are the proxies that relayed it."""
    request = _Request(
        headers={"x-forwarded-for": "8.8.8.8, 70.41.3.18, 150.172.238.178"},
        host="10.0.0.5",
    )
    assert geoip.client_ip(request) == "8.8.8.8"


def test_without_the_header_the_socket_peer_is_the_truth():
    """A bare local run has no proxy in front of it."""
    assert geoip.client_ip(_Request(host="8.8.8.8")) == "8.8.8.8"


def test_a_private_or_forged_first_hop_falls_back_to_the_peer():
    """A header claiming 10.0.0.1 should not blind us to a perfectly good peer
    address. The header is client-controlled and is treated as a hint, never as
    evidence — and emphatically never as a rate-limiting or authorization key,
    which is why ``FixedWindowLimiter`` keys on ``request.client.host``."""
    request = _Request(
        headers={"x-forwarded-for": "10.0.0.1"}, host="8.8.8.8"
    )
    assert geoip.client_ip(request) == "8.8.8.8"


def test_a_request_with_no_client_and_no_header_records_nothing():
    assert geoip.client_ip(_Request()) is None


def test_client_ip_never_raises_whatever_it_is_handed():
    """It is called inline in the argument list of the heartbeat's database
    write. An exception here would cost the ``last_seen_at`` write that
    ``capacity.reconcile`` destroys live rented GPUs for not seeing."""
    class Hostile:
        @property
        def headers(self):
            raise RuntimeError("no")

    assert geoip.client_ip(Hostile()) is None
    assert geoip.client_ip(None) is None
    assert geoip.client_ip(object()) is None


# ---------------------------------------------------------------------------
# the provider: off by default, and what it will believe
# ---------------------------------------------------------------------------


def test_detection_is_off_when_nothing_is_configured():
    """The default that makes migration 0031 safe to apply to production
    without changing the behaviour of anything."""
    assert geoip.configured_provider({}) == "off"
    assert geoip.detection_enabled({}) is False
    assert geoip.lookup("8.8.8.8", env={}) is None


def test_an_unrecognised_provider_name_fails_closed():
    """A typo turning detection off is a feature that visibly does not work;
    guessing what was meant is a third party being called from a deployment
    that did not ask for it."""
    assert geoip.configured_provider({"FLASHML_GEOIP_PROVIDER": "ipwhois"}) == "off"
    assert geoip.detection_enabled({"FLASHML_GEOIP_PROVIDER": "ipwhois"}) is False


def test_the_configured_provider_is_read_from_the_environment():
    env = {"FLASHML_GEOIP_PROVIDER": "IpWho"}  # case and spacing tolerated
    assert geoip.configured_provider(env) == "ipwho"
    assert geoip.detection_enabled(env) is True


def test_a_successful_lookup_is_coarsened_and_carries_no_city():
    """48.1372 states a claim about a building. The evidence is a routing hop,
    and one decimal place (~11km) is what it supports.

    The payload deliberately CONTAINS a city, because "the provider did not
    send one" would be a test of the provider rather than of this module."""
    provider = _ipwho(lambda r: httpx.Response(200, json=_ok_payload()))

    result = provider.lookup("8.8.8.8")

    assert result == geoip.GeoResult(
        country="DE", region="Bavaria", lat=48.1, lon=11.6
    )
    assert not hasattr(result, "city")
    assert "Munich" not in repr(result)


def test_the_address_is_the_one_that_gets_asked_about():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_ok_payload())

    _ipwho(handler).lookup("8.8.8.8")
    assert seen["url"] == "https://ipwho.is/8.8.8.8"


@pytest.mark.parametrize(
    "payload",
    [
        {"success": False, "message": "Reserved range"},   # 200 + refusal
        {},                                                 # no success field
        {"success": True},                                  # no country
        _ok_payload(country_code="DEU"),                    # alpha-3
        _ok_payload(country_code="D"),
        _ok_payload(country_code="12"),
        _ok_payload(country_code=49),
        _ok_payload(country_code=None),
        _ok_payload(country_code=""),
        "a string, not an object",
        ["a", "list"],
        None,
    ],
)
def test_a_payload_this_module_will_not_believe_answers_none(payload):
    """The status code is not the answer — ipwho.is returns 200 with
    ``success: false`` for an address it will not resolve, the same shape as
    the coordinator's ``{"accepted": false}`` on a 200. And a country is
    required: a reading with no country places nothing, so there would be no
    reason to write it."""
    assert _ipwho(lambda r: httpx.Response(200, json=payload)).lookup(
        "8.8.8.8"
    ) is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="upstream is unwell"),
        httpx.Response(429, text="slow down"),
        httpx.Response(200, text="<html>not json at all</html>"),
        httpx.Response(301, text=""),
    ],
)
def test_a_provider_having_a_bad_day_answers_none_rather_than_raising(response):
    assert _ipwho(lambda r: response).lookup("8.8.8.8") is None


def test_a_transport_failure_answers_none_rather_than_raising():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    assert _ipwho(boom).lookup("8.8.8.8") is None


@pytest.mark.parametrize(
    "payload,expected_lat,expected_lon",
    [
        (_ok_payload(latitude=None), None, None),
        (_ok_payload(longitude="11.5"), None, None),   # a string is not a number
        (_ok_payload(latitude=True, longitude=True), None, None),
        (_ok_payload(latitude=91.0), None, None),      # out of range
        (_ok_payload(longitude=-181.0), None, None),
        (_ok_payload(latitude=-89.96, longitude=179.99), -90.0, 180.0),
    ],
)
def test_coordinates_are_both_or_neither_and_range_checked(
    payload, expected_lat, expected_lon
):
    """Half a coordinate pins nothing and would render as a marker on a
    meridian — the same refusal ``set_machine_location`` makes for a declared
    pair. A country with no coordinates is still a usable reading, so the
    result survives; only the pair is dropped."""
    result = _ipwho(lambda r: httpx.Response(200, json=payload)).lookup(
        "8.8.8.8"
    )
    assert result is not None and result.country == "DE"
    assert result.lat == expected_lat
    assert result.lon == expected_lon


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_coordinate_that_is_not_a_number_is_dropped(bad):
    """Tested at the normalisation seam rather than through a payload, because
    NaN and the infinities are not JSON — a provider cannot legally send one,
    but a future provider adapter computing a value could produce one, and the
    range test is what catches it (``not (-90 <= nan <= 90)`` is True, so no
    separate isnan branch is needed and none should be added)."""
    result = geoip._normalise(country="DE", region=None, lat=bad, lon=11.5)
    assert result == geoip.GeoResult(country="DE", region=None, lat=None, lon=None)


def test_a_region_is_taken_as_given_and_an_empty_one_is_no_region():
    """Not validated against anything: there is no list covering every
    country's subdivisions, and a half-right one would refuse correct input."""
    assert _ipwho(
        lambda r: httpx.Response(200, json=_ok_payload(region="  Bavaria  "))
    ).lookup("8.8.8.8").region == "Bavaria"
    assert _ipwho(
        lambda r: httpx.Response(200, json=_ok_payload(region=""))
    ).lookup("8.8.8.8").region is None
    assert _ipwho(
        lambda r: httpx.Response(200, json=_ok_payload(region=7))
    ).lookup("8.8.8.8").region is None


def test_a_lookup_retries_once_and_then_gives_up():
    """One retry for the one failure worth retrying — a dropped connection.
    No backoff and no third try: the sweep already has a far better backoff
    than any loop here could implement, in that it stamps ``geo_checked_at``
    and does not come back for a week."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    assert geoip.lookup("8.8.8.8", provider=_ipwho(handler)) is None
    assert calls["n"] == geoip.LOOKUP_ATTEMPTS == 2


def test_a_lookup_that_succeeds_on_the_retry_is_a_success():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("first one dropped")
        return httpx.Response(200, json=_ok_payload())

    result = geoip.lookup("8.8.8.8", provider=_ipwho(handler))
    assert result == geoip.GeoResult(country="DE", region="Bavaria", lat=48.1, lon=11.6)


def test_a_private_address_is_never_sent_to_a_provider():
    """The filter is applied on the way in as well as on the way out. Nothing
    should be able to make this process tell a third party about the shape of
    a private network."""
    provider = RecordingProvider(_munich())
    assert geoip.lookup("10.0.0.4", provider=provider) is None
    assert provider.asked == []


# ---------------------------------------------------------------------------
# the sweep
# ---------------------------------------------------------------------------


def test_the_sweep_is_a_no_op_when_detection_is_off(db, monkeypatch):
    """Not "reads the rows and writes nothing" — reads NOTHING. An
    unconfigured deployment must not pay a query for a feature it has not
    enabled, which is also why ``app.py`` never starts the loop at all."""
    monkeypatch.delenv(geoip.PROVIDER_ENV, raising=False)
    owner = _new_user(db)
    machine_id = _machine(db, owner)

    assert geoip.sweep(db) == 0
    assert _geo(db, machine_id)["geo_checked_at"] is None
    assert _geo(db, machine_id)["geo_source"] is None


def test_a_resolved_machine_is_written_as_detected_with_no_city(db):
    owner = _new_user(db)
    machine_id = _machine(db, owner)

    assert geoip.sweep(db, provider=RecordingProvider(_munich())) == 1

    row = _geo(db, machine_id)
    assert row["geo_country"] == "DE"
    assert row["geo_region"] == "Bavaria"
    assert row["geo_lat"] == 48.1 and row["geo_lon"] == 11.6
    assert row["geo_source"] == "detected"
    assert row["geo_checked_at"] is not None
    # THE PRIVACY BOUNDARY. A country is a fact about a network; a city is a
    # fact about a person, and a volunteer donated compute rather than
    # consenting to be placed on a street map.
    assert row["geo_city"] is None


def test_the_console_read_passes_a_detected_location_through_unchanged(db):
    """``network._location`` must not assume the two-value enum migration 0029
    shipped with. The whole reason 0031 could be written at all is that the
    reader treats ``geo_source`` as an opaque label rather than branching on
    it."""
    owner = _new_user(db)
    machine_id = _machine(db, owner)
    geoip.sweep(db, provider=RecordingProvider(_munich()))

    detail = network.provider_detail(db, machine_id, owner)
    assert detail["location"] == {
        "country": "DE",
        "region": "Bavaria",
        "city": None,
        "lat": 48.1,
        "lon": 11.6,
        "source": "detected",
    }


def test_a_detected_row_never_overwrites_a_declared_one(db):
    """A host who typed a country wins permanently: ``set_machine_location``
    writes ``geo_source = 'declared'``, and the sweep's WHERE clause then
    excludes that row for ever."""
    owner = _new_user(db)
    machine_id = _machine(db, owner)
    network.set_machine_location(
        db, machine_id, owner, country="FR", city="Paris", lat=48.85, lon=2.35
    )

    provider = RecordingProvider(_munich())
    assert geoip.sweep(db, provider=provider) == 0

    row = _geo(db, machine_id)
    assert row["geo_country"] == "FR"
    assert row["geo_city"] == "Paris"
    assert row["geo_source"] == "declared"
    assert provider.asked == [], "a declared machine was not even a candidate"


def test_a_detected_row_never_overwrites_a_venue_one(db):
    """``venue`` is a fact the venue published about hardware this control
    plane rented — strictly better evidence than the egress address of the
    pod, which is 0029's point and survives the reversal intact."""
    owner = _new_user(db)
    machine_id = _machine(db, owner)
    with db.cursor() as cur:
        cur.execute(
            """
            update public.machines
               set geo_country = 'US', geo_region = 'Kansas',
                   geo_source = 'venue'
             where id = %s
            """,
            (machine_id,),
        )

    provider = RecordingProvider(_munich())
    assert geoip.sweep(db, provider=provider) == 0

    row = _geo(db, machine_id)
    assert row["geo_country"] == "US" and row["geo_source"] == "venue"
    assert provider.asked == []


def test_a_declaration_that_lands_mid_sweep_still_wins(db):
    """THE RACE THE WHERE CLAUSE EXISTS FOR. The row was selected seconds ago
    and those seconds were spent waiting on a third party, so a host declaring
    a location in the middle of a sweep is not hypothetical. A check-then-write
    would lose it."""
    owner = _new_user(db)
    machine_id = _machine(db, owner)

    class DeclaresWhileWeWait:
        asked: list[str] = []

        def lookup(self, ip):
            network.set_machine_location(db, machine_id, owner, country="FR")
            return _munich()

    # The lookup succeeded, so the provider is not what stopped this — the
    # UPDATE matched no row, and the count reflects what the DATABASE did.
    assert geoip.sweep(db, provider=DeclaresWhileWeWait()) == 0

    row = _geo(db, machine_id)
    assert row["geo_country"] == "FR" and row["geo_source"] == "declared"


def test_the_sweep_respects_its_budget(db):
    """The budget bounds how long one sweep holds a database connection across
    third-party HTTP — ``budget`` x the per-lookup timeout x the retry. This
    deployment has exhausted its Postgres pooler before, so a budget that did
    not bound the work would turn a slow provider into an outage."""
    owner = _new_user(db)
    for _ in range(5):
        _machine(db, owner)

    provider = RecordingProvider(_munich())
    assert geoip.sweep(db, budget=2, provider=provider) == 2
    assert len(provider.asked) == 2

    # And the rest are still waiting, not lost.
    assert geoip.sweep(db, budget=2, provider=provider) == 2
    assert len(provider.asked) == 4


def test_a_budget_of_zero_does_nothing(db):
    owner = _new_user(db)
    _machine(db, owner)
    provider = RecordingProvider(_munich())

    assert geoip.sweep(db, budget=0, provider=provider) == 0
    assert provider.asked == []


def test_a_failed_lookup_is_stamped_and_not_retried_within_the_window(db):
    """The reason ``geo_checked_at`` exists. Without it a machine on an
    unresolvable address — a satellite range, an anycast egress — or a provider
    having a bad day matches the candidate query on every single tick, for
    ever, and consumes the whole budget while the rest of the fleet waits
    behind it."""
    owner = _new_user(db)
    machine_id = _machine(db, owner)
    provider = RecordingProvider(None)

    assert geoip.sweep(db, provider=provider) == 0

    row = _geo(db, machine_id)
    assert row["geo_checked_at"] is not None, "a failure is a result, and is recorded"
    assert row["geo_source"] is None, "a stamp is not a location"
    # One machine, one sweep, TWO calls: `lookup` retries a failure once. The
    # retry is inside a single sweep and is the whole retry policy.
    assert len(provider.asked) == geoip.LOOKUP_ATTEMPTS

    assert geoip.sweep(db, provider=provider) == 0
    assert len(provider.asked) == geoip.LOOKUP_ATTEMPTS, (
        "the same unanswerable question was re-asked on the next tick"
    )


def test_a_stale_failure_is_reconsidered_once_the_window_passes(db):
    """The cursor is a backoff, not a tombstone: a machine that moved, or an
    outage that ended, has to be picked up eventually."""
    owner = _new_user(db)
    machine_id = _machine(db, owner, checked_days_ago=8)

    provider = RecordingProvider(_munich())
    assert geoip.sweep(db, provider=provider) == 1
    assert _geo(db, machine_id)["geo_source"] == "detected"


def test_a_machine_checked_yesterday_is_left_alone(db):
    owner = _new_user(db)
    _machine(db, owner, checked_days_ago=1)

    provider = RecordingProvider(_munich())
    assert geoip.sweep(db, provider=provider) == 0
    assert provider.asked == []


def test_never_checked_machines_are_served_before_the_backlog(db):
    """``nulls first``: a machine that enrolled this morning must not sit
    behind a queue of rows waiting out their staleness window."""
    owner = _new_user(db)
    stale = _machine(db, owner, checked_days_ago=9)
    fresh = _machine(db, owner)

    provider = RecordingProvider(_munich())
    assert geoip.sweep(db, budget=1, provider=provider) == 1

    assert _geo(db, fresh)["geo_source"] == "detected"
    assert _geo(db, stale)["geo_source"] is None


@pytest.mark.parametrize("status", ["pending", "revoked", "deleted"])
def test_only_active_machines_are_candidates(db, status):
    """A pending machine has never redeemed a token, a revoked one holds a
    dead credential, and a deleted one is a tombstone whose device columns were
    scrubbed on purpose (0028). None of the three is capacity, and none of them
    should be placed on a map — the same filter ``providers_overview`` uses."""
    owner = _new_user(db)
    machine_id = _machine(db, owner, status=status)

    provider = RecordingProvider(_munich())
    assert geoip.sweep(db, provider=provider) == 0
    assert provider.asked == []
    assert _geo(db, machine_id)["geo_source"] is None


def test_a_machine_with_no_address_is_not_a_candidate(db):
    """Which is every machine in the fleet until 0031's columns start being
    written, and every machine in a local or e2e run for ever — the peer is
    loopback and ``client_ip`` filters it to ``None``."""
    owner = _new_user(db)
    machine_id = _machine(db, owner, ip=None)

    provider = RecordingProvider(_munich())
    assert geoip.sweep(db, provider=provider) == 0
    assert provider.asked == []
    assert _geo(db, machine_id)["geo_checked_at"] is None


def test_one_machines_failure_does_not_stop_the_next(db):
    owner = _new_user(db)
    bad = _machine(db, owner, ip="1.1.1.1")
    good = _machine(db, owner, ip="8.8.8.8")

    provider = RecordingProvider(results={"8.8.8.8": _munich()})
    assert geoip.sweep(db, budget=5, provider=provider) == 1

    assert _geo(db, good)["geo_source"] == "detected"
    assert _geo(db, bad)["geo_source"] is None
    assert _geo(db, bad)["geo_checked_at"] is not None


def test_the_sweep_writes_no_city_even_over_one_that_was_somehow_there(db):
    """A city with no ``geo_source`` is not a state anything legitimate
    produces, and it must not become a ``detected`` location wearing a city no
    detection ever found. The UPDATE says ``geo_city = null`` explicitly so the
    no-city rule is a property of the ROW."""
    owner = _new_user(db)
    machine_id = _machine(db, owner)
    with db.cursor() as cur:
        cur.execute(
            "update public.machines set geo_city = 'Munich' where id = %s",
            (machine_id,),
        )

    assert geoip.sweep(db, provider=RecordingProvider(_munich())) == 1
    assert _geo(db, machine_id)["geo_city"] is None


def test_the_database_still_refuses_a_source_it_has_never_heard_of(db):
    """0031 WIDENED the constraint; it did not remove it. ``detected`` is now
    legal and everything else is still not."""
    import psycopg

    owner = _new_user(db)
    machine_id = _machine(db, owner)

    with pytest.raises(psycopg.errors.CheckViolation):
        with db.cursor() as cur:
            cur.execute(
                "update public.machines set geo_source = 'geoip' where id = %s",
                (machine_id,),
            )


# ---------------------------------------------------------------------------
# the hot paths: recording an address, and never doing more than that
# ---------------------------------------------------------------------------


def test_the_heartbeat_records_an_address_without_disturbing_last_seen(db):
    """``last_seen_at`` is what stands between a live rented GPU and
    ``capacity.reconcile`` destroying it. The address rides the SAME UPDATE —
    one statement, one row version, no new hop."""
    owner = _new_user(db)
    machine_id = _machine(db, owner, ip=None)

    dbmod.touch_machine_last_seen(db, machine_id, ip="8.8.8.8")

    with db.cursor() as cur:
        cur.execute(
            "select last_seen_at, last_seen_ip from public.machines where id = %s",
            (machine_id,),
        )
        row = cur.fetchone()
    assert row["last_seen_ip"] == "8.8.8.8"
    assert row["last_seen_at"] is not None


def test_a_heartbeat_with_no_address_leaves_the_column_exactly_as_it_was(db):
    """``None`` never clears a value: a machine that beats once through a proxy
    that strips the header has not moved. And the default argument keeps every
    existing caller byte-identical."""
    owner = _new_user(db)
    machine_id = _machine(db, owner, ip="8.8.8.8")

    dbmod.touch_machine_last_seen(db, machine_id)

    assert _geo(db, machine_id)["last_seen_ip"] == "8.8.8.8"


def test_the_uptime_ledger_still_gets_its_bucket_when_an_address_rides_along(db):
    """0029's ledger and 0031's address are two nested savepoints on one
    heartbeat. Neither may cost the other, or the outer UPDATE."""
    owner = _new_user(db)
    machine_id = _machine(db, owner, ip=None)

    dbmod.touch_machine_last_seen(db, machine_id, ip="8.8.8.8")

    with db.cursor() as cur:
        cur.execute(
            "select beats from public.machine_uptime_hours where machine_id = %s",
            (machine_id,),
        )
        assert cur.fetchone()["beats"] == 1


def test_registration_records_the_address_beside_the_capability_snapshot(db):
    """The earliest moment an address can be recorded. It matters because a
    machine that enrols and then works for hours never returns to the node
    heartbeat route (``flashnode`` blocks inside ``execute_one``), so without
    this it would have no address for the sweep until its first idle beat."""
    owner = _new_user(db)
    machine_id = _machine(db, owner, ip=None)

    dbmod.set_machine_capabilities(
        db,
        machine_id=machine_id,
        sandbox_capable=True,
        argv_capable=False,
        unsandboxed_argv_capable=False,
        module_capable=True,
        dataset_cache_bytes=1024,
        reported={"cpu_cores": 8},
        last_seen_ip="8.8.8.8",
    )

    with db.cursor() as cur:
        cur.execute(
            "select last_seen_ip, sandbox_capable, capabilities "
            "  from public.machines where id = %s",
            (machine_id,),
        )
        row = cur.fetchone()
    assert row["last_seen_ip"] == "8.8.8.8"
    assert row["sandbox_capable"] is True
    assert row["capabilities"]["cpu_cores"] == 8
    assert row["capabilities"]["dataset_cache_bytes"] == 1024


def test_registration_without_an_address_writes_the_snapshot_unchanged(db):
    """The no-ip branch must be the statement this function has always issued,
    so that every existing caller and every test of them is untouched."""
    owner = _new_user(db)
    machine_id = _machine(db, owner, ip="8.8.8.8")

    dbmod.set_machine_capabilities(
        db,
        machine_id=machine_id,
        sandbox_capable=False,
        argv_capable=True,
        unsandboxed_argv_capable=False,
        module_capable=False,
        reported={"cpu_cores": 4},
    )

    with db.cursor() as cur:
        cur.execute(
            "select last_seen_ip, argv_capable, capabilities "
            "  from public.machines where id = %s",
            (machine_id,),
        )
        row = cur.fetchone()
    assert row["last_seen_ip"] == "8.8.8.8", "None never clears an address"
    assert row["argv_capable"] is True
    assert row["capabilities"]["cpu_cores"] == 4


# ---------------------------------------------------------------------------
# the wiring: off by default, at startup as well as in the sweep
# ---------------------------------------------------------------------------


def test_an_unconfigured_deployment_starts_no_sweeper(make_client, monkeypatch):
    """THE PROPERTY THAT MAKES MIGRATION 0031 SAFE TO APPLY TO PRODUCTION.
    Not a task that wakes up every fifteen minutes and decides it has nothing
    to do — no task at all, so nothing opens a database connection on a timer
    for a feature nobody enabled. This is the state of every deployment today.
    """
    monkeypatch.delenv(geoip.PROVIDER_ENV, raising=False)
    client = make_client()
    assert getattr(client.app.state, "geoip_sweeper", None) is None


def test_a_configured_deployment_starts_one(make_client, monkeypatch):
    """And the gate is the environment variable rather than the migration:
    setting it is the decision, with a name on it, rather than a side effect
    of a deploy."""
    import asyncio

    monkeypatch.setenv(geoip.PROVIDER_ENV, "ipwho")
    client = make_client()
    task = client.app.state.geoip_sweeper
    assert isinstance(task, asyncio.Task)
    assert not task.done()


def test_an_ip_is_not_exposed_by_any_machine_read(db):
    """``last_seen_ip`` is personal data about the host. It is deliberately
    absent from ``db.MACHINE_PUBLIC_COLUMNS`` and from
    ``network._PROVIDER_COLUMNS``, and no API response carries it — its only
    consumer is the sweep, in this same process."""
    assert "last_seen_ip" not in dbmod.MACHINE_PUBLIC_COLUMNS
    assert "last_seen_ip" not in network._PROVIDER_COLUMNS

    owner = _new_user(db)
    machine_id = _machine(db, owner)
    geoip.sweep(db, provider=RecordingProvider(_munich()))

    detail = network.provider_detail(db, machine_id, owner)
    assert "8.8.8.8" not in repr(detail)

    listed = dbmod.list_machines_for_owner(db, owner)
    assert all("last_seen_ip" not in row for row in listed)
