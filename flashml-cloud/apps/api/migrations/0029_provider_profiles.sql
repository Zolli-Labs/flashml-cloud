-- 0029_provider_profiles.sql
--
-- WHERE A PROVIDER IS, AND WHETHER IT WAS THERE. Six nullable columns on
-- public.machines and one append-mostly ledger table.
--
-- WHY THIS EXISTS. The network page has to answer two questions about a
-- machine that this schema could not answer at all:
--
--   * WHERE IS IT. Nothing recorded a location. `machines.platform` is a
--     kernel string and `capabilities` is a hardware snapshot; neither says
--     anything about geography, and a buyer choosing between two equal asks
--     has data-residency and latency reasons to care which continent the
--     work runs on.
--   * WAS IT THERE YESTERDAY. `machines.last_seen_at` is a single instant,
--     overwritten by every heartbeat. It answers "is this machine online
--     right now" and it is structurally incapable of answering "how much of
--     last week was it online" — the previous value is gone the moment the
--     next beat lands. A machine that has been up for a month and one that
--     booted forty seconds ago are the same row.
--
-- GEO IS DECLARED, NEVER DETECTED, and that is a product rule rather than an
-- implementation shortcut. There is no IP geolocation here and none should be
-- added:
--
--   * The agent's IP address is the address of whatever NAT, VPN or corporate
--     egress it happens to sit behind. Geolocating it produces a confident,
--     specific, wrong answer — the failure mode that survives review, because
--     nothing about the output says it was guessed.
--   * A volunteer running this on a home machine has not agreed to have their
--     town inferred from their connection. Asking is the difference between a
--     field somebody filled in and surveillance they did not opt into, and
--     the honest version of that field is one they can leave empty.
--   * A rented GPU's location is a FACT THE VENUE PUBLISHES — RunPod names
--     its data centre, and so does every other venue this control plane
--     acquires from. Copying the venue's own answer is strictly better
--     evidence than geolocating the pod's egress IP.
--
-- Hence `geo_source`, and hence exactly two legal values. `declared` means the
-- owner typed it; `venue` means it was copied from the venue record of a
-- machine this control plane rented (migration 0022/0023). A third value
-- meaning "we worked it out from the network" is the thing this constraint
-- exists to make impossible to write by accident. NULL is the ordinary state
-- and means nobody has said: every machine enrolled before today is NULL, and
-- an empty location renders as unknown rather than as a guess.
--
-- The five location columns are nullable INDEPENDENTLY on purpose. A host who
-- is willing to say "DE" and not willing to say which city has told the truth,
-- and a schema that demanded all-or-nothing would push them to either
-- over-share or say nothing. Coordinates are the same: a country with no
-- lat/lon still sorts and filters, it just does not pin on a map.
--
-- ---------------------------------------------------------------------------
-- public.machine_uptime_hours: RAW BUCKETS, DERIVED SCORES.
--
-- One row per (machine, hour) in which the machine spoke to us at all, and
-- `beats` counts how many times. It is written by
-- `db.touch_machine_last_seen` — the same best-effort call both heartbeat
-- proxies already make, so this ledger costs no new hop from any agent and
-- covers a machine between tasks (the node route) and a machine on one (the
-- attempt route) alike.
--
-- **WHY BUCKETS AND NOT A SCORE.** The obvious alternative is an
-- `uptime_pct` column on `machines`, recomputed on write. It is wrong twice:
--
--   1. The formula is not settled. Today's reading is "hours with a beat, out
--      of the last 168". Tomorrow it may weight recent hours more heavily,
--      exclude hours in which nothing was offered to the machine, or split
--      into an availability figure and a reliability one. Every one of those
--      is a new number over the SAME evidence — and with buckets stored, each
--      is a query change. With a score stored, each is a migration plus a
--      backfill that cannot be done, because the evidence it needed was
--      thrown away at write time.
--   2. A stored score cannot be audited. "This provider is 94%" is a claim
--      the host is ranked by and the buyer decides on; the rows behind it are
--      what makes that claim checkable, and they are also the only way to
--      answer a host who says the number is wrong.
--
-- **WHY AN HOUR.** It is the coarsest bucket that still resolves a working
-- day, and it bounds the table: one machine's ledger grows by at most 24 rows
-- a day, 8,760 a year, however often it beats. A minute bucket would be 60x
-- that for no decision anybody makes at minute resolution.
--
-- **`beats` IS EVIDENCE, NOT A SCORE EITHER.** `beats > 0` is the whole
-- reading today — the machine was there during that hour — and the count
-- rides along because it distinguishes a machine that beat once at the top of
-- the hour from one that beat throughout it, which is exactly the sort of
-- thing a later formula wants and cannot reconstruct. Nothing reads it yet,
-- and a reader must not infer a duty cycle from it: heartbeat CADENCE is not
-- part of the agent contract, so `beats = 30` and `beats = 4` may be two
-- healthy machines on different agent versions.
--
-- **A MISSING ROW IS NOT A ZERO ROW.** Nothing writes `beats = 0`; the
-- default exists so the column can never be null, not because anybody inserts
-- one. So "hours observed" and "hours up" are the same count today, and a
-- reader that wants "fraction of the last week up" must divide by the WINDOW
-- (168), never by the rows it found — dividing by the rows it found reports
-- 100% for every machine that has ever beaten once.
--
-- **APPEND-MOSTLY, and never a foreign key anything cascades through
-- meaningfully.** `on delete cascade` matches every other machine-referencing
-- table (0001, 0004, 0006, 0008, 0018) — but note that machines are NOT
-- deleted (0028: the row is tombstoned, never removed), so in practice this
-- cascade fires for nothing. It is here so that if the day ever comes when a
-- row is genuinely removed, an orphaned uptime ledger does not outlive it.
--
-- NO RETENTION POLICY, deliberately, and this matches 0015's stance on the
-- attempts history: the evidence is small (8,760 rows per machine-year at the
-- absolute ceiling), it is the only record there is of a provider's history,
-- and a sweep that trims it would silently reset a long-serving host's
-- standing. If it ever needs one, it needs a decision about what a host's
-- record means, not a cron job.
--
-- INDEXES: the PRIMARY KEY IS THE INDEX, and there is deliberately no second
-- one. Both reads this table has — "the last 168 buckets for these machines"
-- and "the last 24 buckets for this machine" — are range scans whose leading
-- column is `machine_id` and whose second is `hour_ts`, which is exactly the
-- primary key's own btree. A separate `(machine_id, hour_ts)` index would be
-- a byte-identical copy maintained on every heartbeat in the fleet, which is
-- the write path this table is on. 0015 made the same call for the same
-- reason ("one index, leading column shared, rather than two").
--
-- Row Level Security is enabled with NO policies, exactly as 0001 leaves
-- `machines` and 0004 leaves `attempts`. `anon` and `authenticated` reach
-- nothing here directly; the API is the only door, and it is the API that
-- decides whose uptime a viewer may see.
--
-- Idempotent: safe to re-run (`add column if not exists`, `drop constraint if
-- exists` + `add constraint`, `create table if not exists`, re-runnable
-- `comment on`).
--
-- HOW THIS IS APPLIED: by the migration runner,
-- `python -m flashml_cloud_api.migrate`, which records it in
-- public.schema_migrations. There are TWO databases, dev (auto-migrated on
-- merge to `develop`) and production (gated behind a manual workflow).
-- Project refs deliberately not named here: they belong in the gitignored env
-- files and in Render's `sync: false` prompts, nowhere in git. The runner is
-- what keeps the two honest, so do not apply this by hand to either.
--
-- Note the ordering dependency this creates with the API: the uptime write in
-- `db.touch_machine_last_seen` catches `UndefinedTable` and carries on, so an
-- API deployed BEFORE this migration lands keeps heartbeating normally and
-- simply records no buckets. That degradation is deliberate and tested; it is
-- not a licence to leave the two out of step.
--
-- Do not edit this file after it has been applied anywhere: the runner
-- checksums it, and an edit reads as drift and blocks every later migration.

-- ---------------------------------------------------------------------------
-- Where the machine is, according to somebody who knows.
-- ---------------------------------------------------------------------------
alter table public.machines
    add column if not exists geo_country text;

alter table public.machines
    add column if not exists geo_region text;

alter table public.machines
    add column if not exists geo_city text;

alter table public.machines
    add column if not exists geo_lat double precision;

alter table public.machines
    add column if not exists geo_lon double precision;

alter table public.machines
    add column if not exists geo_source text;

alter table public.machines
    drop constraint if exists machines_geo_source_check;
alter table public.machines
    add constraint machines_geo_source_check
        check (geo_source in ('declared', 'venue'));

-- Coordinates that are not coordinates. The API validates these too
-- (`network.set_machine_location`), and this is the same belt-and-braces
-- `machines_status_check` applies to `status`: an invalid state must be
-- impossible to write even given a bug above. A swapped lat/lon pair is the
-- realistic mistake — 51.5, -0.13 typed backwards puts London at latitude
-- -0.13 and longitude 51.5, which is in Kenya and passes any check that only
-- looks at one of the two ranges.
alter table public.machines
    drop constraint if exists machines_geo_lat_check;
alter table public.machines
    add constraint machines_geo_lat_check
        check (geo_lat >= -90 and geo_lat <= 90);

alter table public.machines
    drop constraint if exists machines_geo_lon_check;
alter table public.machines
    add constraint machines_geo_lon_check
        check (geo_lon >= -180 and geo_lon <= 180);

comment on column public.machines.geo_country is
    'ISO-3166 alpha-2, uppercase, or NULL. FORMAT is validated (two letters); '
    'MEMBERSHIP is not — a hardcoded country list is a thing that goes stale '
    'and starts refusing real places. NULL means nobody has said where this '
    'machine is, which is the state every machine enrolled before this '
    'migration is in and a perfectly acceptable state to stay in.';

comment on column public.machines.geo_region is
    'Free-text state / province / region as the owner wrote it, or NULL. Not '
    'validated against anything: there is no list that covers every country''s '
    'subdivisions, and a half-right one would refuse correct input.';

comment on column public.machines.geo_city is
    'Free-text city as the owner wrote it, or NULL. Independently nullable '
    'from country: a host willing to name a country and not a city has told '
    'the truth, and all-or-nothing would push them to over-share or say '
    'nothing.';

comment on column public.machines.geo_lat is
    'Latitude in decimal degrees, -90..90, or NULL. Only ever as precise as '
    'the owner chose to be — a city centroid is the expected value, not a '
    'street address, and nothing here should be treated as a device''s actual '
    'position.';

comment on column public.machines.geo_lon is
    'Longitude in decimal degrees, -180..180, or NULL. Set together with '
    'geo_lat or not at all: half a coordinate pins nothing.';

comment on column public.machines.geo_source is
    'How the location got here: `declared` (the owner typed it) or `venue` '
    '(copied from the record of a machine this control plane rented, whose '
    'venue publishes its data centre). NULL when there is no location. There '
    'is deliberately NO value meaning "inferred from the network": geo is '
    'DECLARED, NEVER DETECTED. IP geolocation reports the egress of whatever '
    'NAT or VPN the agent sits behind — a confident, specific, wrong answer '
    'nothing downstream can tell apart from a true one — and a volunteer has '
    'not agreed to have their town inferred from their connection.';

-- ---------------------------------------------------------------------------
-- The uptime ledger. Raw hour buckets; every score is derived at read time.
-- ---------------------------------------------------------------------------
create table if not exists public.machine_uptime_hours (
    machine_id uuid not null
               references public.machines(id) on delete cascade,

    -- Truncated to the hour by the writer (`date_trunc('hour', now())`), which
    -- is what makes (machine_id, hour_ts) a natural key rather than a
    -- timestamp that happens to collide sometimes.
    hour_ts    timestamptz not null,

    -- How many times the machine spoke to us during that hour. See the header:
    -- `> 0` is the whole reading today, and heartbeat cadence is not part of
    -- the agent contract, so the magnitude is not a duty cycle.
    beats      integer not null default 0,

    primary key (machine_id, hour_ts)
);

comment on table public.machine_uptime_hours is
    'Append-mostly uptime ledger: one row per (machine, hour) in which the '
    'machine heartbeated at all, upserted by db.touch_machine_last_seen from '
    'both heartbeat proxies. RAW BUCKETS ONLY — every uptime score is derived '
    'from these at read time, so the formula can change without a migration '
    'and without a backfill that could not be done. A row with beats > 0 means '
    'the machine was there during that hour; a MISSING row means it was not, '
    'and nothing ever writes beats = 0, so a reader computing "fraction of a '
    'window up" must divide by the WINDOW and never by the rows it found.';

comment on column public.machine_uptime_hours.hour_ts is
    'The hour this bucket covers, truncated by the writer. Together with '
    'machine_id it is the primary key, which is also the only index this table '
    'needs: every read is a range scan on (machine_id, hour_ts) in that order.';

comment on column public.machine_uptime_hours.beats is
    'How many heartbeats landed in this hour. Evidence kept alongside the '
    'boolean reading, not a score: a later formula may want to tell a machine '
    'that beat once from one that beat throughout, and that cannot be '
    'reconstructed later if only the boolean is stored. Do NOT read a duty '
    'cycle out of it — heartbeat cadence is not part of the agent contract, so '
    'two healthy machines on different agent versions report different counts.';

alter table public.machine_uptime_hours enable row level security;
