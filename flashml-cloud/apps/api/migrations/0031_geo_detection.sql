-- 0031_geo_detection.sql
--
-- A THIRD, WEAKEST SOURCE OF LOCATION, AND THE TWO COLUMNS THAT FEED IT.
-- Two nullable columns on public.machines and one widened check constraint.
--
-- NOTE ON THE NUMBER. This file was specified as `0030_geo_detection.sql` and
-- is `0031`: `0030_official_providers.sql` already exists and is already
-- applied. The runner (`flashml_cloud_api.migrate`) discovers `*.sql` sorted
-- BY FILENAME, so a second `0030_` would have sorted BEFORE the applied one
-- and inserted a new migration into the middle of a history that has already
-- run. Version numbers are allocated by whoever lands first; this one landed
-- second.
--
-- ---------------------------------------------------------------------------
-- WHY THIS EXISTS, GIVEN THAT 0029 SAYS THE OPPOSITE.
--
-- Read 0029's header first. It is emphatic — "GEO IS DECLARED, NEVER
-- DETECTED" — and its `machines_geo_source_check` was written specifically to
-- make a third value impossible to write by accident. This migration adds that
-- third value. That is a reversal, it was made deliberately, and the argument
-- has to survive next to 0029's rather than quietly replace it.
--
-- What 0029 got right, and what stands unchanged here:
--
--   * An egress IP is the address of a NAT, a VPN or a corporate gateway, and
--     geolocating it produces a confident, specific, wrong answer. TRUE, and
--     it is why detection is the LOWEST-PRECEDENCE source and why it is
--     labelled in the row rather than blended into the others.
--   * A volunteer has not agreed to have their town inferred from their
--     connection. TRUE, and it is why THIS MIGRATION HAS NO CITY. See below —
--     that is the single most important line in this file.
--   * A rented GPU's location is a fact the venue publishes, and copying the
--     venue's own answer beats guessing. TRUE, and `venue` still outranks
--     `detected` for exactly that reason.
--
-- What changed is the alternative being compared against. 0029 compared
-- "detected" with "declared" and correctly preferred declared. The live
-- deployment compares it with NOTHING: almost no host ever opens the console
-- and types a country, so the honest-but-empty column renders as "unknown" on
-- essentially every row, and a provider network whose map is blank gives a
-- buyer nothing to choose on — which was the entire reason 0029 added the
-- columns. `docs/superpowers/specs/2026-08-13-device-profiles-provider-network
-- -design.md` carries the direction: its Principles §1 states the v1 rule
-- ("No IP sniffing in v1") and its Phase 2 item 9 names the successor
-- ("coarse IP-geo fallback for hosts who never declare"). This is item 9.
--
-- SO THE RULE IS NO LONGER "NEVER DETECTED". IT IS:
--
--     declared  >  venue  >  detected  >  nothing
--
-- and the ordering is enforced by the WRITER, not by this schema. Detection
-- fills a NULL `geo_source` and nothing else: it never overwrites `declared`,
-- never overwrites `venue`, and never overwrites its own earlier answer with a
-- fresher guess while a human answer is absent (it does refresh a `detected`
-- row after the staleness window, which is the same source replacing itself).
-- A host who types a country later wins permanently, because `set_machine_
-- location` writes `geo_source = 'declared'` and the sweep's WHERE clause then
-- excludes that row for ever. `flashml_cloud_api/geoip.py` is the only writer
-- of `detected`, exactly as `network.set_machine_location` is the only writer
-- of `declared`.
--
-- ---------------------------------------------------------------------------
-- WHY DETECTED NEVER CARRIES A CITY. THIS IS THE PRIVACY BOUNDARY.
--
-- `machines.geo_city` exists (0029) and a detected location MUST NOT WRITE IT.
-- Not "should not", not "does not today" — the column is left NULL by the
-- detection path on purpose and the reason is not squeamishness:
--
--   * A country and a region are facts about a network's routing. A city is a
--     fact about a PERSON — where they live — and a volunteer who installed a
--     host agent consented to donate compute, not to be placed on a street map
--     by an API they never opened.
--   * A declared city is a sentence somebody chose to write. A detected city
--     is a sentence written ABOUT somebody, and the two render identically:
--     nothing downstream of `network._location` can tell a typed "Munich" from
--     an inferred one except by reading `geo_source`, which no console pixel
--     does. The asymmetry of the mistake settles it — a missing city costs a
--     line of a tooltip; a wrong or unconsented one is the kind of thing that
--     ends a volunteer's participation and deserves to.
--   * Precision that is not evidence is a lie with a decimal point.
--     Coordinates are stored COARSENED TO ONE DECIMAL PLACE (~11 km) by the
--     writer for the same reason: the honest claim is "somewhere in this
--     region", and 48.1372 would state a claim about a building that the
--     evidence — a routing hop — cannot support.
--
-- If a future reader wants city-level detection, that needs host consent, not
-- a code change. Do not read this migration as permission.
--
-- ---------------------------------------------------------------------------
-- machines.last_seen_ip — TEXT, NOT inet, AND THAT IS THE POINT.
--
-- The most recently observed PUBLIC address of this machine, written
-- best-effort on the same beats that already write `last_seen_at`
-- (`db.touch_machine_last_seen`) and on registration.
--
-- The obvious column type is `inet`. It is refused because of where the value
-- comes from: `X-Forwarded-For`, a header the client controls, parsed for its
-- first hop. `inet` would make a MALFORMED VALUE A FAILED STATEMENT — and the
-- statement it would fail is the one that also writes `last_seen_at`, which
-- `capacity/reconcile.py` destroys live rented GPUs for not seeing. An agent
-- (or anything upstream of it) sending `X-Forwarded-For: not-an-address` would
-- turn a display-and-billing-critical heartbeat write into a
-- `psycopg.errors.InvalidTextRepresentation`. Best-effort wrappers catch it
-- and the beat is lost anyway. `text` cannot do that: the worst a malformed
-- value achieves is a row the geo sweep's provider declines to resolve.
--
-- The API filters before writing regardless — private and loopback ranges
-- (10/8, 172.16/12, 192.168/16, 127/8, ::1 and friends) are stored as NULL,
-- because geolocating an RFC1918 address is meaningless and every dev run and
-- every e2e pass would otherwise fill this column with 127.0.0.1. The type is
-- the backstop for the filter, not a substitute for it.
--
-- NOT IN `db.MACHINE_PUBLIC_COLUMNS`, and it must not be added there. An IP
-- address is personal data about the host; nothing in the console, the
-- provider network read (`network._PROVIDER_COLUMNS`) or any API response
-- selects this column, and the only consumer is the sweep in the same process.
--
-- ---------------------------------------------------------------------------
-- machines.geo_checked_at — THE SWEEP'S DEDUP CURSOR, NOT A SUCCESS RECORD.
--
-- When detection last RAN for this machine, whatever the outcome. Stamped on
-- success and on failure alike, and the failure half is the whole reason the
-- column exists: without it, a machine whose address the provider cannot
-- resolve (a satellite range, an anycast egress, a provider that is simply
-- down) matches the sweep's WHERE clause on every single tick, for ever, and
-- the budget is spent re-asking the same unanswerable question while every
-- other machine in the fleet waits behind it.
--
-- So it is a CURSOR: "we have already spent an attempt on this row". A row is
-- reconsidered only after the staleness window (7 days), which is also what
-- lets a machine that moved, or an outage that ended, eventually be picked up.
-- Do not read it as "geo was resolved at": a row with `geo_checked_at` set and
-- `geo_source` still NULL is the ordinary record of a lookup that failed.
--
-- ---------------------------------------------------------------------------
-- NO INDEX, deliberately, following 0030's reasoning for `official`. The one
-- read is the sweep's `status = 'active' and geo_source is null and
-- last_seen_ip is not null and (geo_checked_at is null or geo_checked_at <
-- now() - interval '7 days')`, which runs a handful of times an hour over a
-- table the fleet-wide provider list ALREADY scans in full on every page load.
-- A partial index on `(geo_checked_at) where geo_source is null` is the right
-- one to add if this table ever reaches a size where that is not true; adding
-- it now would be maintained on every heartbeat in the fleet for a query
-- nobody is waiting on.
--
-- Row Level Security on `machines` is unchanged (0001: enabled, no policies).
-- `anon` and `authenticated` reach neither new column directly.
--
-- Idempotent: safe to re-run (`add column if not exists`, `drop constraint if
-- exists` + `add constraint`, re-runnable `comment on`).
--
-- HOW THIS IS APPLIED: by the migration runner,
-- `python -m flashml_cloud_api.migrate`, which records it in
-- public.schema_migrations. There are TWO databases, dev (auto-migrated on
-- merge to `develop`) and production (gated behind a manual workflow). Do not
-- apply it by hand to either — the runner is what keeps the two honest.
--
-- ORDERING WITH THE API. This migration goes FIRST, and unlike 0029's uptime
-- ledger the degradation is not symmetrical:
--
--   * API before migration: `touch_machine_last_seen` would name a column that
--     does not exist and fail the heartbeat's `last_seen_at` write — the one
--     that stands between a live rental and `capacity.reconcile` destroying
--     it. The API therefore catches `UndefinedColumn` around the IP half and
--     carries on without it, exactly as 0029's uptime write catches
--     `UndefinedTable`. That is tested, and it is a safety net rather than a
--     licence to deploy out of order.
--   * Migration before API: nothing writes the columns and nothing reads them.
--     The fleet behaves byte-identically to today. This is the correct order.
--
-- AND NOTE THAT NOTHING HERE TURNS DETECTION ON. `geoip.sweep` is inert unless
-- FLASHML_GEOIP_PROVIDER is set to a real provider in the environment; the
-- default is `off`. Applying this migration to production changes the
-- behaviour of nothing at all — it only makes the environment variable
-- meaningful when somebody decides to set it.
--
-- Do not edit this file after it has been applied anywhere: the runner
-- checksums it, and an edit reads as drift and blocks every later migration.

-- ---------------------------------------------------------------------------
-- The two columns detection needs: an address to ask about, and a cursor.
-- ---------------------------------------------------------------------------
alter table public.machines
    add column if not exists last_seen_ip text;

alter table public.machines
    add column if not exists geo_checked_at timestamptz;

-- ---------------------------------------------------------------------------
-- The third source. `drop constraint if exists` + `add constraint` is the
-- idiom 0015 established and 0029 used for this very constraint; a check
-- constraint cannot be widened in place.
--
-- WIDENED, NOT REPLACED. `declared` and `venue` keep their meanings exactly.
-- Every geo_source already in either database is one of those two, so no row
-- is revalidated into legality by this change and nothing needs a backfill.
-- ---------------------------------------------------------------------------
alter table public.machines
    drop constraint if exists machines_geo_source_check;
alter table public.machines
    add constraint machines_geo_source_check
        check (geo_source in ('declared', 'venue', 'detected'));

comment on column public.machines.geo_source is
    'How the location got here, in DESCENDING order of authority: `declared` '
    '(the owner typed it), `venue` (copied from the record of a machine this '
    'control plane rented, whose venue publishes its data centre), or '
    '`detected` (inferred from the machine''s public egress address by '
    'flashml_cloud_api.geoip, migration 0031). NULL when there is no location '
    'at all. THE ORDER IS ENFORCED BY THE WRITERS, NOT BY THIS COLUMN: '
    'detection fills a NULL geo_source only and never overwrites `declared` '
    'or `venue`, so a host who types a country wins permanently. A `detected` '
    'row is the WEAKEST reading here — an egress address is the address of '
    'whatever NAT, VPN or corporate gateway the agent sits behind — which is '
    'why it is labelled rather than blended, why its coordinates are coarsened '
    'to ~11km, and why IT NEVER CARRIES A CITY: a country is a fact about a '
    'network, a city is a fact about a person, and a volunteer consented to '
    'donate compute rather than to be placed on a street map. Migration 0029 '
    'originally forbade this third value outright; 0031''s header carries the '
    'argument for the reversal and what of 0029''s reasoning still stands.';

comment on column public.machines.last_seen_ip is
    'The public IP most recently observed for this machine, written '
    'best-effort beside last_seen_at on the heartbeat and register paths, or '
    'NULL. TEXT RATHER THAN inet ON PURPOSE: the value is parsed from the '
    'client-controlled X-Forwarded-For header, and `inet` would let a '
    'malformed value fail the statement that also writes last_seen_at — the '
    'column capacity/reconcile.py destroys live rented GPUs for not seeing. '
    'Private and loopback addresses are stored as NULL by the writer '
    '(geolocating an RFC1918 address is meaningless, and every dev and e2e run '
    'would otherwise fill this column with 127.0.0.1). Personal data about the '
    'host: it is deliberately absent from db.MACHINE_PUBLIC_COLUMNS and from '
    'network._PROVIDER_COLUMNS, no API response carries it, and its only '
    'consumer is geoip.sweep in this same process. Do not add it to a read.';

comment on column public.machines.geo_checked_at is
    'When automatic geo detection last RAN for this machine — on success AND '
    'on failure. It is the sweep''s dedup cursor, not a record that geo was '
    'resolved: a row with geo_checked_at set and geo_source still NULL is the '
    'ordinary record of a lookup that failed, and stamping it is what stops a '
    'dead provider or an unresolvable address consuming the whole per-tick '
    'budget on every tick for ever. A row is reconsidered only after the '
    'staleness window (7 days), which is also what lets a machine that moved, '
    'or an outage that ended, eventually be picked up.';
