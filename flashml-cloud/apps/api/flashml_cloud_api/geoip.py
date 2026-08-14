"""Automatic, coarse, lowest-precedence location for machines nobody declared.

OFF BY DEFAULT, AND THAT IS NOT A PLACEHOLDER
=============================================

``FLASHML_GEOIP_PROVIDER`` defaults to ``"off"``. With it unset — which is
every checkout, every test run, every e2e pass, and production until somebody
deliberately sets it on Render — :func:`lookup` returns ``None`` without
touching the network and :func:`sweep` returns ``0`` without reading a row.
Nothing in this module changes the behaviour of anything until that variable
says otherwise. Migration 0031 can therefore land on production days before
anybody decides to switch detection on, and the day it is switched on is a
decision with a name on it rather than a side effect of a deploy.

The legal values are ``off`` and ``ipwho``. An unrecognised value is treated as
``off`` and logged once per call site — a typo must fail closed (no detection)
rather than open (some other provider, or a crash on a hot path).

WHAT IT WRITES, AND WHAT IT REFUSES TO WRITE
============================================

``declared > venue > detected``. Migration 0031's header carries the full
argument, including why 0029 originally forbade this and what of that argument
still stands. In this module the rule shows up as three specific refusals:

* **It fills a NULL ``geo_source`` and nothing else.** The predicate
  ``geo_source is null`` is in the WHERE clause of the UPDATE, not in an
  ``if`` above it — the same discipline ``network.set_machine_location`` uses
  for ownership, and for the same reason: a check-then-write is a race (a host
  can declare a location in the seconds between this sweep's SELECT and its
  UPDATE) and, worse, it is a check somebody can forget to write.
* **It never carries a city.** ``geo_country`` and ``geo_region`` are facts
  about a network's routing; a city is a fact about a person. The UPDATE
  writes ``geo_city = null`` explicitly rather than merely omitting it, so
  that "a ``detected`` row has no city" is true of the ROW and not only of
  this writer's intentions.
* **It coarsens coordinates to one decimal place (~11 km).** The evidence is a
  routing hop. ``48.1`` is the honest rendering of what that supports;
  ``48.1372`` states a claim about a building. Precision that is not evidence
  is a lie with a decimal point.

WHY NO HOT PATH EVER CALLS THIS
===============================

Detection costs an outbound HTTP request to a third party. The heartbeat, the
register proxy and every console read must never make one: the heartbeat is
what stands between a live rented GPU and ``capacity.reconcile`` destroying it
(see ``db.touch_machine_last_seen``), and putting a third party's availability
in front of that write would mean a provider outage looked exactly like a fleet
going dark. So the split is:

* the hot paths record ``machines.last_seen_ip`` and nothing more — one column
  on an UPDATE they were already issuing, no new statement, no new hop;
* :func:`sweep` runs on a background timer, resolves at most ``budget``
  machines per tick, and is allowed to be slow, to fail, and to be turned off.

A note on the connection, since this is the one sweep in this process that
holds one across third-party I/O: worst case is ``budget`` machines x 2
attempts x :data:`LOOKUP_TIMEOUT_S`, so 50 seconds at the defaults. The caller
owns the connection's lifecycle (``app.py``'s loop opens it and closes it with
``db.close_when_idle``, exactly as the ephemeral-machine sweep does), and
``budget`` is deliberately small because that product is the number that
matters, not the row count.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
import psycopg

log = logging.getLogger("flashml-cloud-api")

#: Which provider resolves an address, or ``off``. See the module docstring:
#: the default is ``off`` and an unrecognised value is treated as ``off``.
PROVIDER_ENV = "FLASHML_GEOIP_PROVIDER"
PROVIDER_OFF = "off"
PROVIDER_IPWHO = "ipwho"
LEGAL_PROVIDERS = (PROVIDER_OFF, PROVIDER_IPWHO)

#: ipwho.is: no key, no account, generous free tier, and it answers the three
#: fields this module is allowed to keep. It is addressed by IP in the path.
IPWHO_ENDPOINT = "https://ipwho.is/{ip}"

#: Per-attempt timeout. Two attempts (one retry) is the whole retry policy —
#: see :func:`lookup` for why there is no backoff and no third try.
LOOKUP_TIMEOUT_S = 5.0
LOOKUP_ATTEMPTS = 2

#: Decimal places kept on a detected coordinate. ONE, ~11km. The honesty of
#: the centroid: this places a region, not a house.
COORDINATE_DECIMALS = 1

#: How long a machine is left alone after detection ran for it — successfully
#: or not. See migration 0031 on ``geo_checked_at`` being a cursor rather than
#: a success record.
STALE_AFTER_INTERVAL = "7 days"

#: Machines resolved per :func:`sweep` call. Small on purpose: it multiplies
#: the per-lookup timeout into the time this holds a database connection.
DEFAULT_BUDGET = 5

#: ISO-3166 alpha-2: exactly two ASCII letters. FORMAT only, matching
#: ``network._ALPHA2`` — a hardcoded member list goes stale and starts refusing
#: real places, and this API has no business adjudicating which territories
#: exist. Deliberately not imported from ``network``: that module is the
#: reader, this one is a writer, and the shared thing is a two-letter rule
#: rather than a dependency worth creating.
_ALPHA2 = re.compile(r"^[A-Z]{2}$")


@dataclass(frozen=True)
class GeoResult:
    """A normalised, coarsened, city-free reading. Never constructed directly
    from a provider payload — :func:`_normalise` is the only door, so every
    validation rule applies to every provider that is ever added.

    ``country`` is required: a reading with no country places nothing and there
    would be no reason to write it. ``region``, ``lat`` and ``lon`` are
    independently optional, matching 0029's reasoning about the declared
    columns — a partial answer is still an answer.

    THERE IS NO ``city`` FIELD, and that is the enforcement rather than a
    convention. A field that does not exist cannot be written by a careless
    caller, cannot be added by a provider payload that happens to carry one,
    and cannot be smuggled into the UPDATE by somebody who did not read
    migration 0031.
    """

    country: str
    region: str | None = None
    lat: float | None = None
    lon: float | None = None


# ---------------------------------------------------------------------------
# the address
# ---------------------------------------------------------------------------


def public_ip(value: Any) -> str | None:
    """``value`` if it is a public IP address, else ``None``.

    Private, loopback, link-local, multicast, reserved and unspecified ranges
    all answer ``None`` — 10/8, 172.16/12, 192.168/16, 127/8, ``::1`` and the
    rest. Two reasons, and the second is the one that bites:

    * Geolocating an RFC1918 address is meaningless. There is no answer to
      give and a provider that returns one is guessing about the internet's
      shape, not about this machine.
    * **Every dev run and every e2e pass would otherwise fill this column with
      ``127.0.0.1``.** ``machines.last_seen_ip`` would then be mostly loopback,
      the sweep would spend its budget on it, and the first real deployment
      question ("is detection working?") would be answered by a column full of
      the developer's own laptop.

    IPv6 is accepted. A scoped address (``fe80::1%eth0``) parses as link-local
    and is refused on its own merits, not by the ``%``.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    # Brackets are how a v6 address travels in a host header; strip them
    # before parsing rather than after failing.
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return None
    return str(address)


def client_ip(request: Any) -> str | None:
    """The public address this request appears to come from, or ``None``.

    THE FIRST ``X-Forwarded-For`` HOP, then ``request.client.host``. On Render
    (and behind any reverse proxy) the socket peer is the proxy, so the header
    is the only thing that carries the agent's own address; on a bare local run
    there is no header and the socket peer is the truth.

    **The header is client-controlled and nothing here pretends otherwise.**
    An agent can put whatever it likes in it, and the worst it achieves is a
    wrong flag on its own machine's profile — a row it already controls by
    other means, that no gate reads, that no money depends on, and that
    ``geo_source = 'detected'`` already labels as the weakest reading on the
    page. It is emphatically NOT a basis for rate limiting, authorization or
    abuse decisions; ``FixedWindowLimiter`` deliberately keys on
    ``request.client.host`` for exactly that reason and must keep doing so.

    ``None`` on anything unparseable or private, so the caller writes NULL. It
    never raises: this is called inline in the argument list of a heartbeat's
    database write, and an exception here would cost the ``last_seen_at``
    write that ``capacity.reconcile`` destroys rented GPUs for not seeing.
    """
    try:
        headers = getattr(request, "headers", None)
        forwarded = headers.get("x-forwarded-for") if headers is not None else None
        if isinstance(forwarded, str) and forwarded.strip():
            # First hop = the original client. Later hops are the proxies that
            # relayed it, and the LAST hop is the one an attacker cannot forge
            # — but it is also our own proxy, which is not what we want to
            # place on a map.
            candidate = public_ip(forwarded.split(",")[0])
            if candidate is not None:
                return candidate
            # A forged or private first hop falls through to the socket peer
            # rather than answering None: a header that says "10.0.0.1" should
            # not blind us to a perfectly good peer address.
        client = getattr(request, "client", None)
        return public_ip(getattr(client, "host", None)) if client is not None else None
    except Exception:  # noqa: BLE001 - see the docstring: never raise on a beat
        return None


# ---------------------------------------------------------------------------
# the provider
# ---------------------------------------------------------------------------


def configured_provider(env: Any = None) -> str:
    """The provider name in force: ``off`` unless the environment says
    otherwise, and ``off`` for anything unrecognised.

    Fail CLOSED on a typo. ``FLASHML_GEOIP_PROVIDER=ipwhois`` (a plausible
    slip) turning detection off is a feature that visibly does not work;
    guessing what was meant is a third party being called from a deployment
    that did not ask for it.
    """
    source = os.environ if env is None else env
    name = str(source.get(PROVIDER_ENV, PROVIDER_OFF) or PROVIDER_OFF).strip().lower()
    if name not in LEGAL_PROVIDERS:
        log.warning(
            "unknown %s=%r; geo detection stays off (legal values: %s)",
            PROVIDER_ENV, name, ", ".join(LEGAL_PROVIDERS),
        )
        return PROVIDER_OFF
    return name


def detection_enabled(env: Any = None) -> bool:
    """Whether anything in this module should do work. Read by ``app.py``'s
    lifespan so an unconfigured deployment creates no background task at all —
    not a task that wakes up and decides to do nothing, which would still open
    a database connection on every tick."""
    return configured_provider(env) != PROVIDER_OFF


class IpWhoIsProvider:
    """ipwho.is, the default and currently only real provider.

    A CLASS RATHER THAN A FUNCTION so a second provider is a second class with
    the same two methods, and so tests inject one without monkeypatching the
    module. ``fetch`` is the only part that touches the network; everything
    that decides what a payload MEANS lives in :func:`_normalise`, shared by
    every provider that will ever exist. A provider that owned its own
    normalisation would be a second place the no-city and coarsening rules
    could be got wrong.
    """

    name = PROVIDER_IPWHO

    def __init__(
        self,
        client: httpx.Client | None = None,
        timeout: float = LOOKUP_TIMEOUT_S,
    ) -> None:
        self._client = client
        # As `mailer.Mailer` does it: a bare timeout applies independently to
        # connect/read/write/pool, so "5 seconds" is really up to ~20 without
        # capping connect separately.
        self._timeout = httpx.Timeout(timeout, connect=min(timeout, 5.0))

    def fetch(self, ip: str) -> Any:
        """The raw decoded payload, or ``None`` on any failure whatsoever.

        Never raises. A geolocation provider is the least important dependency
        in this process and every one of its failure modes — down, slow, rate
        limiting, HTML error page, JSON that is not an object — is the same
        event here: no reading this time.
        """
        url = IPWHO_ENDPOINT.format(ip=ip)
        try:
            if self._client is not None:
                response = self._client.get(url, timeout=self._timeout)
            else:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.get(url)
        except Exception:  # noqa: BLE001 - a third party never breaks a sweep
            return None
        if response.status_code >= 300:
            return None
        try:
            return response.json()
        except Exception:  # noqa: BLE001 - including a non-JSON error page
            return None

    def lookup(self, ip: str) -> GeoResult | None:
        payload = self.fetch(ip)
        # ipwho.is answers 200 with `{"success": false, "message": ...}` for an
        # address it will not resolve, so the STATUS CODE IS NOT THE ANSWER —
        # the same shape as `attempt_complete`'s `{"accepted": false}` on a 200
        # and the same rule: read the body field the API documents.
        if not isinstance(payload, dict) or payload.get("success") is not True:
            return None
        return _normalise(
            country=payload.get("country_code"),
            region=payload.get("region"),
            lat=payload.get("latitude"),
            lon=payload.get("longitude"),
        )


def provider_for(name: str) -> IpWhoIsProvider | None:
    """The provider object for a name, or ``None`` for ``off``."""
    return IpWhoIsProvider() if name == PROVIDER_IPWHO else None


# ---------------------------------------------------------------------------
# normalisation: the one place a payload becomes a reading
# ---------------------------------------------------------------------------


def _coarse(value: Any, limit: float) -> float | None:
    """A coordinate, validated to its range and rounded to
    :data:`COORDINATE_DECIMALS`. ``None`` for anything else.

    ``bool`` is refused where a number is expected, the same refusal
    ``db._reported_capabilities`` and ``verify._as_finite_float`` make:
    ``latitude: true`` would arrive as 1.0, a plausible number derived from
    something that was never a measurement. A string is refused too — a
    provider that changed its payload type has changed its contract, and
    coercing quietly is how that goes unnoticed.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    # NaN and the infinities: `not (-limit <= nan <= limit)` is True, so the
    # range test rejects them without a separate isnan branch.
    if not -limit <= number <= limit:
        return None
    return round(number, COORDINATE_DECIMALS)


def _normalise(
    *, country: Any, region: Any, lat: Any, lon: Any
) -> GeoResult | None:
    """A provider payload's fields as a :class:`GeoResult`, or ``None``.

    THE ONLY CONSTRUCTOR OF A DETECTED READING. Every rule that makes a
    detected location honest is applied here, once, for every provider:

    * ``country`` is uppercased and must match exactly two ASCII letters.
      Anything else — an alpha-3, a name, a number, an empty string — makes the
      whole reading ``None`` rather than a location with a blank country.
    * ``region`` is passed through, trimmed, empty folded to ``None``. Not
      validated against anything: there is no list covering every country's
      subdivisions and a half-right one would refuse correct input (0029 says
      the same about the declared column).
    * ``lat``/``lon`` are range-checked and coarsened, and are BOTH OR NEITHER.
      Half a coordinate pins nothing and would render as a marker on a
      meridian — the same refusal ``network.set_machine_location`` makes for a
      declared pair.
    * There is no city. See the module docstring and migration 0031.
    """
    if not isinstance(country, str):
        return None
    code = country.strip().upper()
    if not _ALPHA2.match(code):
        return None

    region_value: str | None = None
    if isinstance(region, str):
        region_value = region.strip() or None

    latitude = _coarse(lat, 90.0)
    longitude = _coarse(lon, 180.0)
    if latitude is None or longitude is None:
        # Both or neither, so a provider that returned one usable number and
        # one unusable one contributes no coordinate at all.
        latitude = longitude = None

    return GeoResult(
        country=code, region=region_value, lat=latitude, lon=longitude
    )


# ---------------------------------------------------------------------------
# the lookup
# ---------------------------------------------------------------------------


def lookup(
    ip: str, *, provider: Any = None, env: Any = None
) -> GeoResult | None:
    """Where this address appears to be, or ``None``.

    ``None`` for: detection off, a private or malformed address, a provider
    that failed, a provider that refused, and a payload this module will not
    believe. The caller cannot distinguish them and does not need to — every
    one of them means the same thing, which is "no reading this time", and
    :func:`sweep` records that identically in all cases.

    TWO ATTEMPTS, NO BACKOFF, NO THIRD TRY. The retry is there for the one
    failure worth retrying — a dropped connection or a momentary 5xx — and the
    sweep already has a far better backoff than any loop here could implement:
    it stamps ``geo_checked_at`` and does not return to this machine for a
    week. Spending longer inside a single call would only extend the time a
    database connection is held.
    """
    resolved = provider
    if resolved is None:
        resolved = provider_for(configured_provider(env))
    if resolved is None:
        return None
    if public_ip(ip) is None:
        return None
    for _ in range(LOOKUP_ATTEMPTS):
        result = resolved.lookup(ip)
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# the sweep
# ---------------------------------------------------------------------------


#: Candidates, in the order they should be spent on. NEVER-CHECKED FIRST
#: (``nulls first``) so a newly enrolled machine is not stuck behind a queue of
#: rows waiting out their staleness window, then oldest-check first so the
#: backlog drains in a stable order rather than re-picking the same rows.
_CANDIDATES_SQL = f"""
    select id, last_seen_ip
      from public.machines
     where status = 'active'
       and geo_source is null
       and last_seen_ip is not null
       and (geo_checked_at is null
            or geo_checked_at < now() - interval '{STALE_AFTER_INTERVAL}')
     order by geo_checked_at asc nulls first, last_seen_at desc nulls last, id
     limit %s
"""


def sweep(
    db: psycopg.Connection, *, budget: int = DEFAULT_BUDGET, provider: Any = None
) -> int:
    """Resolve up to ``budget`` undeclared machines. Returns how many got a
    location.

    Returns ``0`` immediately when detection is off, WITHOUT READING A ROW —
    an unconfigured deployment must not pay a query for a feature it has not
    enabled, and ``app.py`` additionally never starts the loop at all.

    **A failed lookup is recorded, not forgotten.** ``geo_checked_at`` is
    stamped either way. Without that, a machine on an unresolvable address (a
    satellite range, an anycast egress) or a provider having a bad day would
    match this query on every tick for ever and consume the whole budget while
    the rest of the fleet waited behind it. Migration 0031 says the same at
    greater length; this is the code it describes.

    **Both UPDATEs re-test ``geo_source is null`` in the WHERE clause.** The
    row was selected some seconds ago and a host may have declared a location
    in between — a real race, because the seconds in question are spent waiting
    on a third party. Re-testing in the statement means a declaration that
    landed mid-sweep wins, and means the guard cannot be omitted without the
    statement obviously changing. The same goes for ``status = 'active'``: a
    machine revoked or tombstoned (0028) mid-sweep gets nothing written.

    **``geo_city = null`` is written explicitly.** A detected reading has no
    city, and stating that in the UPDATE makes it a property of the row rather
    than of this function's good intentions — including for a row that somehow
    carries a city with no ``geo_source``, which nothing legitimate produces
    and which would otherwise become a ``detected`` location wearing a city
    that no detection ever found.

    Never raises for a provider failure. It CAN raise for a database failure,
    deliberately: the caller is a background loop that logs and continues (as
    every other sweep in ``app.py`` does), and a database fault is worth a log
    line rather than being silently counted as zero resolutions.
    """
    resolved_provider = provider
    if resolved_provider is None:
        resolved_provider = provider_for(configured_provider())
    if resolved_provider is None:
        return 0
    if budget <= 0:
        return 0

    with db.cursor() as cur:
        cur.execute(_CANDIDATES_SQL, (int(budget),))
        candidates = list(cur.fetchall())

    resolved = 0
    for row in candidates:
        machine_id = str(row["id"])
        result = lookup(str(row["last_seen_ip"]), provider=resolved_provider)
        with db.cursor() as cur:
            if result is None:
                cur.execute(
                    """
                    update public.machines
                       set geo_checked_at = now()
                     where id = %s
                       and geo_source is null
                       and status = 'active'
                    """,
                    (machine_id,),
                )
                continue
            cur.execute(
                """
                update public.machines
                   set geo_country = %s,
                       geo_region = %s,
                       geo_city = null,
                       geo_lat = %s,
                       geo_lon = %s,
                       geo_source = 'detected',
                       geo_checked_at = now()
                 where id = %s
                   and geo_source is null
                   and status = 'active'
                """,
                (
                    result.country,
                    result.region,
                    result.lat,
                    result.lon,
                    machine_id,
                ),
            )
            # Counted from what the DATABASE did, not from what the provider
            # answered: a row whose owner declared a location mid-sweep is a
            # lookup that succeeded and a write that correctly did not land,
            # and reporting it as resolved would overstate the sweep in the
            # one log line anybody reads.
            if cur.rowcount > 0:
                resolved += 1
    return resolved
