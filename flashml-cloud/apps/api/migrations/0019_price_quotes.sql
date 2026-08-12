-- 0019_price_quotes.sql
--
-- What compute costs at each venue, as OBSERVATIONS with a timestamp on
-- every one of them. One table in `public`, append-only, plus the first
-- captures: RunPod's GPU catalogue and two Alibaba Function Compute billing
-- documents.
--
-- WHY THIS EXISTS. The comparison view is the visible half of the product
-- and it has had no prices behind it. The rule it has to satisfy comes from
-- the design review in one line: **a scraped price presented as live is a
-- lie with a delay**. A number on that page is only defensible if the page
-- can answer "where did $0.34 come from, and when?" — so every external
-- price carries (provider, sku, region, currency, unit, captured_at,
-- source), and a price older than the reading threshold renders as STALE
-- rather than silently as current. `flashml_cloud_api/prices.py` is where
-- that threshold lives (24 h, `prices.DEFAULT_MAX_AGE`); this table is what
-- makes it answerable at all.
--
-- APPEND ONLY, for `credit_entries`' reason (0018) and one of its own. A
-- quote is an observation of what a third party published at an instant. It
-- does not become false when the vendor changes the price — it becomes OLD,
-- and the old value is exactly what proves the new one moved. There is no
-- update path in `prices.py` and there must never be one: overwriting a
-- quote destroys the only evidence that the comparison was ever right, and
-- destroys it precisely when somebody asks.
--
-- ---------------------------------------------------------------------------
-- THE FOUR DECISIONS A READER MUST TAKE FROM THIS FILE
-- ---------------------------------------------------------------------------
--
-- 1. **`amount` IS `numeric`, NOT INTEGER MINOR UNITS, AND NEVER A FLOAT.**
--
--    0018 is emphatic that every money-shaped column in the LEDGER is
--    `bigint` millicredits, and that rule stands where it was made: a ledger
--    must balance, and a half-unit rounding error there is invisible per row
--    and unfixable in aggregate. This table is not a ledger. Nothing here is
--    summed into a balance, nothing is owed to anyone, and no row is ever
--    the counterparty of another.
--
--    What it holds instead is other people's published numbers at wildly
--    different scales: RunPod quotes 0.34 USD per GPU-hour (2 decimal
--    places), Alibaba quotes 0.00025308 USD per GiB-hour (8), and Alibaba's
--    GPU factors are 2.31 CU per GB-VRAM-second — a unit that is not money
--    and HAS no minor unit to count in. One integer scale wide enough for
--    all of them makes every reader carry a different divisor per unit, and
--    the first reader to use the wrong one publishes a price off by three
--    orders of magnitude on the page whose whole job is to be checkable.
--
--    `numeric` in Postgres is exact decimal arithmetic — not binary floating
--    point — and psycopg hands it back as `decimal.Decimal`, so the
--    exactness survives into Python. The precedent is in this schema
--    already: `contributions.duration_s` is `numeric` and 0018 says it stays
--    that way because it measures seconds rather than money. A published
--    price we did not set is a measurement in exactly that sense.
--
--    What is forbidden is unchanged and absolute: no `double precision`, no
--    `real`, and no Python `float` at the boundary. `prices.record_quote`
--    REFUSES a float argument rather than coercing it, because 0.1 + 0.2 is
--    not a rounding style, it is a wrong number, and the whole point of this
--    table is that the number can be checked against the source.
--
-- 2. **A MISSING QUOTE IS A MISSING ROW. IT IS NEVER A ZERO.**
--
--    This is not theoretical, and the live capture below is what proved it.
--    RunPod's catalogue returns `price.secure: 0` for `NVIDIA
--    A100-SXM4-40GB`, alongside `secure: false` and `maxCount.secure: 0`.
--    That zero means NOT OFFERED. Written down as a price it would make a
--    card you cannot rent on secure cloud the cheapest secure GPU in the
--    world, and every "FlashML vs RunPod" number computed from it would be
--    wrong in our favour. There is therefore no secure row for that SKU
--    below, and the comparison view must render it `not observed`.
--
--    The same catalogue uses `0.5` the same way in rows this migration does
--    not seed (`RTX A2000` secure, `H200 NVL` community, `MI300X`
--    community): a placeholder beside `secure: false` / `maxCount: 0`.
--    Anything that ever writes to this table from that API must filter on
--    the offered flags, not on the price being non-zero.
--
--    A REAL zero is also in the seed and the two must not be confused:
--    Alibaba's idle vCPU CU factor is 0 because the document says idle vCPU
--    is not charged. That is a fact with a source. The schema tells the two
--    apart the only way that is honest — by whether a row exists — which is
--    why there is no "unavailable" flag column here. A flag would make
--    "absent" and "present but meaningless" both storable, and every reader
--    would have to remember which.
--
-- 3. **CURRENCY AND UNIT ARE CONSTRAINED; PROVIDER, SKU AND TIER ARE NOT.**
--
--    The rule is: constrain what the CODE REASONS ABOUT, and leave what the
--    VENUE NAMES alone.
--
--    `currency` and `unit` are branched on. The no-conversion rule
--    ("`ZC` and `USD` must never be summed") is enforceable only if the set
--    of denominations is closed, and the unit normaliser must never be
--    handed a unit it has no exact factor for. So both are check
--    constraints, and adding a venue that quotes in a new denomination is a
--    deliberate migration — which is a feature, because it forces somebody
--    to answer "and what may this be compared with?" before the first row
--    lands.
--
--    `tier` and `provider` and `sku` are the venue's own labels. A closed
--    list there would need a migration per venue, and — the damaging part —
--    would tempt a writer to squeeze Alibaba's `Pro` plan into the nearest
--    listed value. A mislabelled tier reads as a fact and cannot be
--    detected afterwards. They are free text with only an anti-corruption
--    check (non-empty, no surrounding whitespace), so ` runpod` and `''`
--    cannot enter and split a key silently.
--
-- 4. **`captured_at` HAS NO DEFAULT, ON PURPOSE.**
--
--    `default now()` is the bug this table exists to prevent, spelled in
--    SQL: it stamps the moment the row was WRITTEN onto a number that may
--    have been read from a vendor document weeks earlier, and the staleness
--    check then certifies it fresh. Not-null with no default makes every
--    writer state when the observation happened. `recorded_at` (which does
--    default to now()) is the other half: with both, a quote captured long
--    before it was stored is visible as such instead of being laundered.
--    Age is ALWAYS measured from `captured_at`. Nothing may measure it from
--    `recorded_at`.
--
-- ---------------------------------------------------------------------------
-- WHY THE SEED IS IN THE MIGRATION
-- ---------------------------------------------------------------------------
-- Because a seed that needs somebody to run it is a seed some database does
-- not have. 0016's header records the failure mode from the other side —
-- `artifact_mirror.py` "shipped complete, tested, and with no callers at
-- all" — and a separate seeder script would be that again, with the added
-- twist that "does prod have the prices?" would be a question rather than a
-- query. Here, dev and production get the same captured rows through the
-- same checksummed runner, once, and the migration ledger is the answer.
--
-- The inserts are `on conflict do nothing` against the natural-key unique
-- index below, so re-running is a no-op rather than a duplicate. Note what
-- that key does NOT do: it does not stop a later, genuinely newer capture
-- of the same price. `captured_at` is part of the key, so tomorrow's pull
-- lands beside today's, which is the whole point of an append-only series.
--
-- RE-PULLING IS EXPECTED AND IS NOT THIS FILE'S JOB. New captures go in
-- through `prices.record_quote`. Do not add rows to this migration later:
-- the runner checksums it and an edit reads as drift.
--
-- ---------------------------------------------------------------------------
-- RLS, and how this is applied
-- ---------------------------------------------------------------------------
-- Row Level Security is enabled with DELIBERATELY no policies, exactly as
-- 0001, 0014 and 0018 leave their tables. RLS with zero policies denies
-- every role except the table owner and BYPASSRLS (the `service_role` key
-- this API uses); the database is API-only.
-- `test_schema.py::test_no_migration_creates_any_policy_at_all` forbids any
-- policy at all and that test is the doctrine.
--
-- These prices are public information — RunPod publishes them and Alibaba
-- documents them — so RLS here is not protecting a secret. It is protecting
-- the WRITE side: a table in `public` with RLS off is writable by `anon`,
-- whose key ships in the browser bundle, and a forged quote is a forged
-- comparison. There is no owner column for the same reason: a quote belongs
-- to nobody, and giving it an owner would invite per-account prices, which
-- is a different product.
--
-- No foreign keys, deliberately. This table names third-party SKUs and
-- nothing in this schema; it also means the migration applies under the
-- baseline flow that
-- test_migrate.py::test_baseline_through_then_apply_runs_only_the_remainder
-- exercises, where most tables are stand-ins.
--
-- Idempotent: `create table if not exists`, `create index if not exists`,
-- re-runnable `enable row level security`, `comment on`, and inserts guarded
-- by `on conflict do nothing`.
--
-- HOW THIS IS APPLIED: by the migration runner,
-- `python -m flashml_cloud_api.migrate`, in filename order, in one
-- transaction per file. There are TWO databases, dev (auto-migrated on merge
-- to `develop`) and production (gated behind a manual workflow); the runner
-- is what keeps them honest, so do not apply this by hand to either.
--
-- Do not edit this file after it has been applied anywhere: the runner
-- checksums it, and an edit reads as drift and blocks every later migration.

create table if not exists public.price_quotes (
    -- `bigserial`, like `credit_entries` and `price_observations`. Insertion
    -- order is meaningful in an append-only series and a uuid throws it away;
    -- it also keeps this migration free of `gen_random_uuid()`, so it applies
    -- on a database where 0001's `create extension pgcrypto` was baselined
    -- rather than executed.
    id          bigserial primary key,

    -- The venue. Free text by decision 3; `''` and ` runpod` are refused so a
    -- typo cannot silently open a second series for the same provider.
    provider    text not null
                check (provider = btrim(provider) and provider <> ''),

    -- THE VENUE'S OWN IDENTIFIER, not our label for it. RunPod rows carry
    -- `NVIDIA GeForce RTX 4090` — the `gpuTypeId` you would pass back to
    -- their API — rather than "RTX 4090", because a SKU that cannot be fed
    -- back to the venue cannot be re-verified, and re-verification is the
    -- only thing separating this table from a spreadsheet. Alibaba rows use
    -- `<service>/<billable item>`, which is as close to an identifier as a
    -- billing document gets.
    sku         text not null check (sku = btrim(sku) and sku <> ''),

    -- NOT NULL, because "which region does this price apply to" always has an
    -- answer — including "all of them", which is spelled `global`. That is a
    -- statement about the QUOTE'S SCOPE (the venue published one price with no
    -- region dimension), not an invented location.
    --
    -- Nullable would have been worse than verbose: `tier` is already nullable
    -- and NULLs never collide in a unique index (the hole 0003 had to fix on
    -- `contributions` and 0018 works around with `coalesce`). A second
    -- nullable column in the same key doubles that hazard for no gain.
    region      text not null check (region = btrim(region) and region <> ''),

    -- The denomination `amount` is expressed in. CLOSED, by decision 3.
    --
    -- `CU` is Alibaba's Compute Unit and IS NOT MONEY. It sits in this column
    -- because it is the denomination of the number, and it is the sharpest
    -- reason the no-conversion rule is in the schema rather than in a comment:
    -- there is no published CU price we have cited, so a CU quote can never be
    -- added to, subtracted from, or ranked against a USD quote. `prices.py`
    -- refuses to combine amounts whose currency differs, and refuses it
    -- generically, so `ZC` vs `USD` is the same refusal by the same code path.
    --
    -- `ZC` is reserved and nothing writes it, the way `abandoned` is reserved
    -- in `attempts.outcome` (0015) and `adjustment` in `credit_entries.reason`
    -- (0018). The internal book lives in `price_observations` (0018) and is
    -- NOT duplicated here. The value exists so that the day FlashML's own
    -- price joins the comparison, no constraint migration is needed and
    -- nobody is tempted to record it as USD.
    currency    text not null
                check (currency in ('USD', 'CNY', 'ZC', 'CU')),

    -- Exact decimal. See decision 1 for why this is not `bigint` minor units,
    -- and why it is emphatically not a float.
    --
    -- 10 decimal places covers the smallest number any venue here publishes
    -- (0.00025308, eight places) with room; 20 total digits makes a typo of
    -- forty digits an error rather than a stored fact.
    --
    -- `>= 0` and not `> 0`: zero is a legal price. 0018's M13 says so for
    -- donated capacity and Alibaba says so for idle vCPU. A zero that means
    -- "not offered" is not stored at all — decision 2.
    amount      numeric(20, 10) not null check (amount >= 0),

    -- What one `amount` buys. CLOSED, because `prices.to_hourly` branches on
    -- it and an unknown unit must fail loudly rather than be normalised by
    -- guess.
    --
    -- GB AND GiB ARE DIFFERENT UNITS HERE AND ARE NOT BRIDGED. Alibaba's
    -- sandbox document quotes USD per GiB-hour and its Function Compute
    -- document quotes CU per GB-second; treating them as one unit would
    -- misstate by 7.4% silently. Each row says what its own source said.
    --
    -- `gb-month` is reserved and unwritten. It is also the unit
    -- `prices.to_hourly` REFUSES: a month is not a fixed number of hours, so
    -- "$/GB-month to $/GB-hour" requires picking 730 or 720 or 744 — an
    -- invented conversion wearing the clothes of arithmetic.
    unit        text not null check (unit in (
                    'gpu-hour',
                    'vcpu-hour', 'vcpu-second',
                    'gib-hour', 'gib-second',
                    'gb-hour', 'gb-second',
                    'gb-vram-hour', 'gb-vram-second',
                    'gb-month')),

    -- The venue's name for the priced service class: `community` / `secure`
    -- on RunPod, `eco` / `std` / `pro` on the FC sandbox, `elastic-active` /
    -- `elastic-idle` for FC's two instance states. Free text by decision 3.
    --
    -- NULL means the venue quotes ONE price across its classes — the FC disk
    -- price, which is identical for all three sandbox plans. It does not mean
    -- "unknown", and a reader joining a plan to its disk price must treat the
    -- null row as applying to every plan rather than as a missing quote.
    tier        text check (tier is null or (tier = btrim(tier) and tier <> '')),

    -- Whatever else the source stated about this SKU: vram_gb, max_cards,
    -- fp16_tflops, the venue's display name, a free allowance. NOT NULL with
    -- a `{}` default for the reason 0005 gives `job_rounds.clipped`: every
    -- quote has an answer to "what else did the source say", and on a sparse
    -- source that answer is "nothing". A nullable column would make every
    -- reader distinguish two kinds of empty.
    --
    -- Nothing prices anything off `attrs`. It is context for a human checking
    -- the number, and a normaliser that started reading `vram_gb` to convert
    -- per-card prices into per-GB prices would be inventing exactly the kind
    -- of cross-venue arithmetic this table refuses.
    attrs       jsonb not null default '{}'::jsonb,

    -- WHEN THE PRICE WAS OBSERVED. No default — decision 4.
    captured_at timestamptz not null,

    -- WHERE THE NUMBER CAME FROM: an API endpoint or a document id, precise
    -- enough that a reader can go and check. Not null and non-empty, because
    -- an unsourced quote is the thing this table was built to make
    -- impossible; it would be indistinguishable from a number somebody
    -- remembered.
    source      text not null check (source = btrim(source) and source <> ''),

    -- HOW it was observed: `runpod-api`, `alibaba-doc`, `invoice`. Distinct
    -- from `source`, which says WHICH endpoint or document — this says what
    -- KIND of evidence it is, and the kinds are not equally strong. An
    -- invoice is what was actually charged; a documented list price is what
    -- the vendor advertises; an API catalogue is what it was quoting at that
    -- instant. Nullable: a future importer may not know, and a wrong answer
    -- here is worse than an absent one.
    observed_by text,

    -- When the ROW was written. The counterweight to `captured_at`: a large
    -- gap between the two is a backfill, and it must be visible rather than
    -- laundered into freshness. Never used to compute age.
    recorded_at timestamptz not null default now()
);

comment on table public.price_quotes is
    'APPEND ONLY record of what compute costs at each external venue. One '
    'row is one OBSERVATION of a published price: (provider, sku, region, '
    'currency, unit, tier) identifies what was priced, `amount` is the '
    'number, and captured_at + source are what make it checkable. Nothing '
    'updates or deletes a row — a price that moved is a NEW row, and the old '
    'one is the evidence that it moved. A venue with no row is NOT priced at '
    'zero: it is unobserved, and must render as such.';

comment on column public.price_quotes.amount is
    'Exact decimal (numeric), never a float and never integer minor units. '
    'Not the `bigint` millicredits rule from 0018 because this is not a '
    'ledger: nothing here is summed into a balance, and the published '
    'numbers span 0.00025308 USD/GiB-hour to 6.94 USD/GPU-hour plus CU '
    'factors that have no minor unit at all. Zero is a legal price (idle '
    'vCPU is documented as free); "not offered" is stored as NO ROW.';

comment on column public.price_quotes.currency is
    'Denomination of `amount`. CU is Alibaba''s Compute Unit and is NOT '
    'money — no published CU price has been cited, so a CU quote may never '
    'be converted to or compared with a USD one. ZC is reserved and '
    'unwritten; FlashML''s own book lives in price_observations (0018). '
    'prices.py refuses to combine amounts across any two currencies, which '
    'is what makes the ZC/USD rule enforceable rather than advisory.';

comment on column public.price_quotes.unit is
    'What one `amount` buys. GB and GiB are DIFFERENT units here and are '
    'never bridged — one document says GiB and another says GB, and '
    'silently equating them misstates by 7.4%. gb-month is reserved, '
    'unwritten, and is the unit prices.to_hourly deliberately refuses: a '
    'month is not a fixed number of hours.';

comment on column public.price_quotes.tier is
    'The venue''s own name for the priced class (community/secure, '
    'eco/std/pro, elastic-active/elastic-idle). Deliberately unconstrained: '
    'a closed list would force a migration per venue and, worse, tempt a '
    'writer to squeeze `pro` into the nearest listed value — a mislabel that '
    'reads as a fact. NULL means the venue quotes ONE price across its '
    'classes (the FC disk price), not that the tier is unknown.';

comment on column public.price_quotes.captured_at is
    'When the price was OBSERVED at the venue. No default, deliberately: '
    '`default now()` would stamp the write time onto a number read from a '
    'document weeks earlier and the staleness check would then certify it '
    'fresh — "a scraped price presented as live is a lie with a delay". Age '
    'is always measured from this column, never from recorded_at.';

comment on column public.price_quotes.source is
    'The API endpoint or document id the number came from, precise enough to '
    're-check. An unsourced quote is indistinguishable from a remembered '
    'one, which is why this is NOT NULL and non-empty.';

comment on column public.price_quotes.observed_by is
    'The KIND of evidence: runpod-api, alibaba-doc, invoice. Not the same '
    'question as `source`, and the kinds are not equally strong — an invoice '
    'is what was charged, a doc is what is advertised, an API catalogue is '
    'what was being quoted at that instant.';

comment on column public.price_quotes.recorded_at is
    'When the ROW was written, as distinct from when the price was observed. '
    'A large gap is a backfill and must stay visible. Never used to compute '
    'age.';

alter table public.price_quotes enable row level security;

-- ---------------------------------------------------------------------------
-- Indexes.
--
-- `price_quotes_latest_idx` IS THE COMPARISON VIEW'S READ: the newest quote
-- for each thing priced. The query is
--
--   select distinct on (provider, sku, region, tier, currency, unit) *
--     from public.price_quotes
--    order by provider, sku, region, tier, currency, unit, captured_at desc
--
-- and this index is exactly that sort order, so the read is a scan of the
-- index in order rather than a sort of the whole history.
--
-- THE KEY IS SIX COLUMNS, NOT THE FOUR THE BRIEF ASKED FOR, and the two
-- extras are load-bearing. Alibaba prices the SAME venue in two
-- denominations — dollars for the sandbox, CU for the GPU — and could
-- price one SKU per-second and per-hour. With `currency` and `unit` outside
-- the key, `distinct on` keeps whichever row sorts first and DROPS the
-- other: a venue's price silently disappearing from the comparison, which
-- is the same failure `unpriced()` exists to prevent, one level down.
--
-- `tier` is nullable and that is fine HERE — `distinct on` and `order by`
-- treat NULLs as equal to each other and sort them last, which matches this
-- index's default NULLS LAST. It is NOT fine in a unique index, hence the
-- `coalesce` in the next one.
-- ---------------------------------------------------------------------------
create index if not exists price_quotes_latest_idx
    on public.price_quotes
       (provider, sku, region, tier, currency, unit, captured_at desc);

-- ---------------------------------------------------------------------------
-- `price_quotes_observation_idx` makes RE-RECORDING THE SAME OBSERVATION a
-- no-op, which is what lets the seed below and every retried importer insert
-- unconditionally with `on conflict do nothing` — the discipline
-- `credit_entries` established in 0018 and `contributions` in 0003.
--
-- `coalesce(tier, '')` for the reason 0018 spells out at length: NULLs never
-- collide in a unique index, so an un-tiered quote (the FC disk price) could
-- be inserted an unbounded number of times and the index would permit every
-- one. Folding null to the empty string gives those rows a single shared key.
--
-- `captured_at` and `source` ARE in the key, deliberately. Two readings of
-- the same price at different instants are two observations and both belong
-- here; two readings of the same price at the same instant from the same
-- source are one observation reported twice.
--
-- `amount` is NOT in the key, and that is the interesting part: it means the
-- same source claiming two different prices for the same thing at the same
-- instant is a CONFLICT rather than two rows. `prices.record_quote` detects
-- exactly that case and raises rather than letting `do nothing` swallow it —
-- a contradiction dropped in silence is how a comparison ends up showing a
-- number nobody can reproduce.
-- ---------------------------------------------------------------------------
create unique index if not exists price_quotes_observation_idx
    on public.price_quotes
       (provider, sku, region, coalesce(tier, ''), currency, unit,
        captured_at, source);

-- ===========================================================================
-- SEED: the first captures.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- RunPod, pulled live from the catalogue API at 2026-08-12T03:55Z (the local
-- afternoon of 2026-08-11). USD per GPU-hour, community and secure cloud.
--
-- These thirteen SKUs were re-pulled rather than transcribed, and every one
-- of the twenty-five prices matched the figures the brief carried — worth
-- recording, because "we checked and it had not moved" is itself an
-- observation and the reason this row set can be trusted for a day.
--
-- `region` is `global`: RunPod's catalogue quotes one price per SKU per
-- cloud, with no region dimension. Per-datacentre availability exists in
-- their API and per-datacentre PRICE does not, so `global` is what was
-- observed rather than a simplification.
--
-- NO SECURE ROW FOR `NVIDIA A100-SXM4-40GB`. The API returned
-- `price.secure: 0` beside `secure: false` and `maxCount.secure: 0` — a
-- not-offered sentinel, not a free GPU. See decision 2 in the header.
--
-- `max_cards` in `attrs` is that tier's own `maxCount`, because the two
-- clouds differ (an A40 was rentable one-at-a-time on community and
-- ten-at-a-time on secure at capture) and a per-SKU figure would be wrong
-- for one of them.
-- ---------------------------------------------------------------------------
insert into public.price_quotes
    (provider, sku, region, currency, amount, unit, tier, attrs,
     captured_at, source, observed_by)
values
    ('runpod', 'NVIDIA RTX A5000', 'global', 'USD', 0.16, 'gpu-hour',
     'community', '{"vram_gb": 24, "max_cards": 10, "display_name": "RTX A5000"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),
    ('runpod', 'NVIDIA RTX A5000', 'global', 'USD', 0.27, 'gpu-hour',
     'secure', '{"vram_gb": 24, "max_cards": 10, "display_name": "RTX A5000"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),

    ('runpod', 'NVIDIA GeForce RTX 3090', 'global', 'USD', 0.22, 'gpu-hour',
     'community', '{"vram_gb": 24, "max_cards": 8, "display_name": "RTX 3090"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),
    ('runpod', 'NVIDIA GeForce RTX 3090', 'global', 'USD', 0.50, 'gpu-hour',
     'secure', '{"vram_gb": 24, "max_cards": 8, "display_name": "RTX 3090"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),

    ('runpod', 'NVIDIA RTX A6000', 'global', 'USD', 0.33, 'gpu-hour',
     'community', '{"vram_gb": 48, "max_cards": 8, "display_name": "RTX A6000"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),
    ('runpod', 'NVIDIA RTX A6000', 'global', 'USD', 0.53, 'gpu-hour',
     'secure', '{"vram_gb": 48, "max_cards": 10, "display_name": "RTX A6000"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),

    ('runpod', 'NVIDIA GeForce RTX 4090', 'global', 'USD', 0.34, 'gpu-hour',
     'community', '{"vram_gb": 24, "max_cards": 8, "display_name": "RTX 4090"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),
    ('runpod', 'NVIDIA GeForce RTX 4090', 'global', 'USD', 0.74, 'gpu-hour',
     'secure', '{"vram_gb": 24, "max_cards": 8, "display_name": "RTX 4090"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),

    ('runpod', 'NVIDIA A40', 'global', 'USD', 0.35, 'gpu-hour',
     'community', '{"vram_gb": 48, "max_cards": 1, "display_name": "A40"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),
    ('runpod', 'NVIDIA A40', 'global', 'USD', 0.44, 'gpu-hour',
     'secure', '{"vram_gb": 48, "max_cards": 10, "display_name": "A40"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),

    ('runpod', 'NVIDIA GeForce RTX 5090', 'global', 'USD', 0.69, 'gpu-hour',
     'community', '{"vram_gb": 32, "max_cards": 8, "display_name": "RTX 5090"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),
    ('runpod', 'NVIDIA GeForce RTX 5090', 'global', 'USD', 0.99, 'gpu-hour',
     'secure', '{"vram_gb": 32, "max_cards": 8, "display_name": "RTX 5090"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),

    ('runpod', 'NVIDIA L40', 'global', 'USD', 0.69, 'gpu-hour',
     'community', '{"vram_gb": 48, "max_cards": 10, "display_name": "L40"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),
    ('runpod', 'NVIDIA L40', 'global', 'USD', 0.82, 'gpu-hour',
     'secure', '{"vram_gb": 48, "max_cards": 8, "display_name": "L40"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),

    ('runpod', 'NVIDIA L40S', 'global', 'USD', 0.79, 'gpu-hour',
     'community', '{"vram_gb": 48, "max_cards": 8, "display_name": "L40S"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),
    ('runpod', 'NVIDIA L40S', 'global', 'USD', 0.99, 'gpu-hour',
     'secure', '{"vram_gb": 48, "max_cards": 8, "display_name": "L40S"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),

    -- Community only. The secure figure the API returns for this SKU is a
    -- not-offered sentinel and is deliberately absent — decision 2.
    ('runpod', 'NVIDIA A100-SXM4-40GB', 'global', 'USD', 1.00, 'gpu-hour',
     'community', '{"vram_gb": 40, "max_cards": 2, "display_name": "A100 SXM 40GB"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),

    ('runpod', 'NVIDIA A100 80GB PCIe', 'global', 'USD', 1.19, 'gpu-hour',
     'community', '{"vram_gb": 80, "max_cards": 8, "display_name": "A100 PCIe"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),
    ('runpod', 'NVIDIA A100 80GB PCIe', 'global', 'USD', 1.39, 'gpu-hour',
     'secure', '{"vram_gb": 80, "max_cards": 8, "display_name": "A100 PCIe"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),

    ('runpod', 'NVIDIA A100-SXM4-80GB', 'global', 'USD', 1.39, 'gpu-hour',
     'community', '{"vram_gb": 80, "max_cards": 8, "display_name": "A100 SXM"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),
    ('runpod', 'NVIDIA A100-SXM4-80GB', 'global', 'USD', 1.59, 'gpu-hour',
     'secure', '{"vram_gb": 80, "max_cards": 8, "display_name": "A100 SXM"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),

    ('runpod', 'NVIDIA H100 80GB HBM3', 'global', 'USD', 2.69, 'gpu-hour',
     'community', '{"vram_gb": 80, "max_cards": 1, "display_name": "H100 SXM"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),
    ('runpod', 'NVIDIA H100 80GB HBM3', 'global', 'USD', 3.29, 'gpu-hour',
     'secure', '{"vram_gb": 80, "max_cards": 8, "display_name": "H100 SXM"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),

    ('runpod', 'NVIDIA H200', 'global', 'USD', 3.59, 'gpu-hour',
     'community', '{"vram_gb": 141, "max_cards": 8, "display_name": "H200 SXM"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api'),
    ('runpod', 'NVIDIA H200', 'global', 'USD', 4.59, 'gpu-hour',
     'secure', '{"vram_gb": 141, "max_cards": 8, "display_name": "H200 SXM"}',
     '2026-08-12T03:55:00Z', 'runpod REST v2 GPU catalogue (list-gpu-types)', 'runpod-api')
on conflict do nothing;

-- ---------------------------------------------------------------------------
-- Alibaba Function Compute — FC Agent Sandbox, preview pay-as-you-go.
-- Document 3045213, read 2026-08-12T03:58Z. USD, per hour.
--
-- All three published plans are recorded, not only Pro. They come from one
-- table in one document at one capture, and recording a subset would leave
-- the cheapest and the middle plan looking unobserved when they are simply
-- unrecorded — the distinction this whole table exists to keep sharp.
--
-- THE DISK PRICE HAS NO TIER because the document quotes one figure for all
-- three plans. It has two REGIONS, because that is the dimension it does
-- vary on: 0.00031896 inside mainland China, 0.00025308 outside.
--
-- TWO THINGS THIS SCHEMA CANNOT SAY, both of them real:
--
--   * The 15 GiB FREE DISK ALLOWANCE per sandbox (active and light
--     hibernation only). A free tier is a price that depends on QUANTITY,
--     and a `(unit, amount)` pair cannot express a band. It is recorded in
--     `attrs` so a human reading the row sees it, and NOTHING computes with
--     it — a comparison that applied the allowance silently would understate
--     Alibaba, and one that ignored it silently overstates. Whoever wires
--     the view has to decide in the open.
--   * Deep hibernation's billed quantity, `memory x 2 + disk`. That is a
--     rule for computing HOW MUCH of a resource is billable, not a price per
--     unit, and it belongs to whatever models a sandbox's lifecycle. The
--     disk unit price it multiplies is the row below.
-- ---------------------------------------------------------------------------
insert into public.price_quotes
    (provider, sku, region, currency, amount, unit, tier, attrs,
     captured_at, source, observed_by)
values
    ('alibaba-fc', 'fc-agent-sandbox/vcpu', 'cn-mainland', 'USD', 0.00936,
     'vcpu-hour', 'eco', '{"preview": true, "billing_granularity_s": 1}',
     '2026-08-12T03:58:00Z',
     'alibabacloud.com/help/functioncompute/pay-as-you-go-of-fc-agent-sandbox (doc 3045213)',
     'alibaba-doc'),
    ('alibaba-fc', 'fc-agent-sandbox/vcpu', 'cn-mainland', 'USD', 0.01224,
     'vcpu-hour', 'std', '{"preview": true, "billing_granularity_s": 1}',
     '2026-08-12T03:58:00Z',
     'alibabacloud.com/help/functioncompute/pay-as-you-go-of-fc-agent-sandbox (doc 3045213)',
     'alibaba-doc'),
    ('alibaba-fc', 'fc-agent-sandbox/vcpu', 'cn-mainland', 'USD', 0.01872,
     'vcpu-hour', 'pro', '{"preview": true, "billing_granularity_s": 1}',
     '2026-08-12T03:58:00Z',
     'alibabacloud.com/help/functioncompute/pay-as-you-go-of-fc-agent-sandbox (doc 3045213)',
     'alibaba-doc'),

    ('alibaba-fc', 'fc-agent-sandbox/memory', 'cn-mainland', 'USD', 0.004608,
     'gib-hour', 'eco', '{"preview": true, "billing_granularity_s": 1}',
     '2026-08-12T03:58:00Z',
     'alibabacloud.com/help/functioncompute/pay-as-you-go-of-fc-agent-sandbox (doc 3045213)',
     'alibaba-doc'),
    ('alibaba-fc', 'fc-agent-sandbox/memory', 'cn-mainland', 'USD', 0.006012,
     'gib-hour', 'std', '{"preview": true, "billing_granularity_s": 1}',
     '2026-08-12T03:58:00Z',
     'alibabacloud.com/help/functioncompute/pay-as-you-go-of-fc-agent-sandbox (doc 3045213)',
     'alibaba-doc'),
    ('alibaba-fc', 'fc-agent-sandbox/memory', 'cn-mainland', 'USD', 0.009360,
     'gib-hour', 'pro', '{"preview": true, "billing_granularity_s": 1}',
     '2026-08-12T03:58:00Z',
     'alibabacloud.com/help/functioncompute/pay-as-you-go-of-fc-agent-sandbox (doc 3045213)',
     'alibaba-doc'),

    -- No tier: one disk price for eco, std and pro alike.
    ('alibaba-fc', 'fc-agent-sandbox/disk', 'cn-mainland', 'USD', 0.00031896,
     'gib-hour', null,
     '{"preview": true, "billing_granularity_s": 1, "free_allowance_gib": 15,
       "free_allowance_states": ["active", "light-hibernation"],
       "free_allowance_applied_by_this_system": false}',
     '2026-08-12T03:58:00Z',
     'alibabacloud.com/help/functioncompute/pay-as-you-go-of-fc-agent-sandbox (doc 3045213)',
     'alibaba-doc'),
    ('alibaba-fc', 'fc-agent-sandbox/disk', 'outside-cn-mainland', 'USD', 0.00025308,
     'gib-hour', null,
     '{"preview": true, "billing_granularity_s": 1, "free_allowance_gib": 15,
       "free_allowance_states": ["active", "light-hibernation"],
       "free_allowance_applied_by_this_system": false}',
     '2026-08-12T03:58:00Z',
     'alibabacloud.com/help/functioncompute/pay-as-you-go-of-fc-agent-sandbox (doc 3045213)',
     'alibaba-doc')
on conflict do nothing;

-- ---------------------------------------------------------------------------
-- Alibaba Function Compute — serverless GPU, elastic instances.
-- Document 2512929, read 2026-08-12T03:57Z. **CU, NOT DOLLARS.**
--
-- FC bills everything in Compute Units: `CU usage = SUM(resource usage x
-- conversion factor)`, and the CU itself is then priced on a MONTHLY TIERED
-- basis, accumulated PER REGION. These rows are the conversion factors, in
-- their own unit, and that is as far as this capture goes. An unconverted CU
-- figure is honest; a dollar figure derived from a CU rate we did not find
-- and cannot cite is not, and it would be the exact "made-up comparison"
-- this table was built to replace. `currency = 'CU'` is what stops any
-- arithmetic from crossing that line by accident.
--
-- Two further reasons a CU→USD constant would be wrong even if somebody
-- found one: the price is banded by monthly volume (so it depends on how
-- much you bought, which is not a property of the SKU), and it is
-- accumulated per region without merging (so it depends on where). Neither
-- is expressible as one number per SKU, which is the shape this table has.
--
-- ONE NUMBER DIFFERS FROM THE BRIEF, AND THE DOCUMENT WINS. The brief that
-- commissioned this table listed `ada.1` at **1.5 active / 0.25 idle**.
-- Document 2512929 read today says **1.7 / 0.2**. A number cited to a
-- document must be what that document says, so 1.7/0.2 is what is recorded,
-- with today's `captured_at`. The other five factors the brief named
-- (vCPU 1.0/0, memory 0.15/0.10, disk 0.05/0.05, tesla.1 2.1/0.5,
-- ampere.1 1.8/0.3) matched exactly. This is what a dated series is FOR: had
-- an earlier capture existed, the move would be visible instead of arguable.
--
-- Six GPU series the brief did not name are in the same table and are
-- recorded with it — ada.2, ada.3, blackwell.1, hopper.1, hopper.2, xpu.1.
--
-- ACTIVE AND IDLE ARE BOTH PRICES, and the idle one is not a discount. An
-- idle elastic instance is still holding its VRAM, and for a system whose
-- pitch is about keeping capacity warm, dropping the idle factor would
-- understate the cost of the thing being compared. They are two tiers of the
-- same SKU: `elastic-active` and `elastic-idle`.
--
-- The idle vCPU factor of 0 IS A REAL ZERO — the document states idle vCPU
-- is not charged. Contrast the RunPod sentinel in the block above, which is
-- an absent row. Same digit, opposite meanings, and the schema keeps them
-- apart by presence rather than by value.
--
-- `region = 'global'`: the conversion FACTORS are stated without a region.
-- The CU price is regional; we do not have it, so we do not imply it.
-- Memory and disk are per GB-second here because the document says GB —
-- the sandbox document above says GiB, and the two are not the same unit.
-- ---------------------------------------------------------------------------
insert into public.price_quotes
    (provider, sku, region, currency, amount, unit, tier, attrs,
     captured_at, source, observed_by)
values
    ('alibaba-fc', 'fc-gpu/vcpu', 'global', 'CU', 1.0, 'vcpu-second',
     'elastic-active', '{"instance_kind": "elastic"}', '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),
    -- A documented zero, not an absence: idle vCPU is explicitly not charged.
    ('alibaba-fc', 'fc-gpu/vcpu', 'global', 'CU', 0, 'vcpu-second',
     'elastic-idle', '{"instance_kind": "elastic"}', '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),

    ('alibaba-fc', 'fc-gpu/memory', 'global', 'CU', 0.15, 'gb-second',
     'elastic-active', '{"instance_kind": "elastic"}', '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),
    ('alibaba-fc', 'fc-gpu/memory', 'global', 'CU', 0.10, 'gb-second',
     'elastic-idle', '{"instance_kind": "elastic"}', '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),

    ('alibaba-fc', 'fc-gpu/disk', 'global', 'CU', 0.05, 'gb-second',
     'elastic-active', '{"instance_kind": "elastic"}', '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),
    ('alibaba-fc', 'fc-gpu/disk', 'global', 'CU', 0.05, 'gb-second',
     'elastic-idle', '{"instance_kind": "elastic"}', '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),

    ('alibaba-fc', 'fc-gpu/tesla.1', 'global', 'CU', 2.1, 'gb-vram-second',
     'elastic-active', '{"instance_kind": "elastic", "gpu_series": "tesla.1"}',
     '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),
    ('alibaba-fc', 'fc-gpu/tesla.1', 'global', 'CU', 0.5, 'gb-vram-second',
     'elastic-idle', '{"instance_kind": "elastic", "gpu_series": "tesla.1"}',
     '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),

    ('alibaba-fc', 'fc-gpu/ampere.1', 'global', 'CU', 1.8, 'gb-vram-second',
     'elastic-active', '{"instance_kind": "elastic", "gpu_series": "ampere.1"}',
     '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),
    ('alibaba-fc', 'fc-gpu/ampere.1', 'global', 'CU', 0.3, 'gb-vram-second',
     'elastic-idle', '{"instance_kind": "elastic", "gpu_series": "ampere.1"}',
     '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),

    -- 1.7 / 0.2 as document 2512929 reads today. See the header note.
    ('alibaba-fc', 'fc-gpu/ada.1', 'global', 'CU', 1.7, 'gb-vram-second',
     'elastic-active', '{"instance_kind": "elastic", "gpu_series": "ada.1"}',
     '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),
    ('alibaba-fc', 'fc-gpu/ada.1', 'global', 'CU', 0.2, 'gb-vram-second',
     'elastic-idle', '{"instance_kind": "elastic", "gpu_series": "ada.1"}',
     '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),

    ('alibaba-fc', 'fc-gpu/ada.2', 'global', 'CU', 1.95, 'gb-vram-second',
     'elastic-active', '{"instance_kind": "elastic", "gpu_series": "ada.2"}',
     '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),
    ('alibaba-fc', 'fc-gpu/ada.2', 'global', 'CU', 0.23, 'gb-vram-second',
     'elastic-idle', '{"instance_kind": "elastic", "gpu_series": "ada.2"}',
     '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),

    ('alibaba-fc', 'fc-gpu/ada.3', 'global', 'CU', 1.95, 'gb-vram-second',
     'elastic-active', '{"instance_kind": "elastic", "gpu_series": "ada.3"}',
     '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),
    ('alibaba-fc', 'fc-gpu/ada.3', 'global', 'CU', 0.23, 'gb-vram-second',
     'elastic-idle', '{"instance_kind": "elastic", "gpu_series": "ada.3"}',
     '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),

    ('alibaba-fc', 'fc-gpu/blackwell.1', 'global', 'CU', 2.1, 'gb-vram-second',
     'elastic-active', '{"instance_kind": "elastic", "gpu_series": "blackwell.1"}',
     '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),
    ('alibaba-fc', 'fc-gpu/blackwell.1', 'global', 'CU', 0.28, 'gb-vram-second',
     'elastic-idle', '{"instance_kind": "elastic", "gpu_series": "blackwell.1"}',
     '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),

    ('alibaba-fc', 'fc-gpu/hopper.1', 'global', 'CU', 2.31, 'gb-vram-second',
     'elastic-active', '{"instance_kind": "elastic", "gpu_series": "hopper.1"}',
     '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),
    ('alibaba-fc', 'fc-gpu/hopper.1', 'global', 'CU', 0.315, 'gb-vram-second',
     'elastic-idle', '{"instance_kind": "elastic", "gpu_series": "hopper.1"}',
     '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),

    ('alibaba-fc', 'fc-gpu/hopper.2', 'global', 'CU', 2.31, 'gb-vram-second',
     'elastic-active', '{"instance_kind": "elastic", "gpu_series": "hopper.2"}',
     '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),
    ('alibaba-fc', 'fc-gpu/hopper.2', 'global', 'CU', 0.315, 'gb-vram-second',
     'elastic-idle', '{"instance_kind": "elastic", "gpu_series": "hopper.2"}',
     '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),

    ('alibaba-fc', 'fc-gpu/xpu.1', 'global', 'CU', 1.2, 'gb-vram-second',
     'elastic-active', '{"instance_kind": "elastic", "gpu_series": "xpu.1"}',
     '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc'),
    ('alibaba-fc', 'fc-gpu/xpu.1', 'global', 'CU', 0.23, 'gb-vram-second',
     'elastic-idle', '{"instance_kind": "elastic", "gpu_series": "xpu.1"}',
     '2026-08-12T03:57:00Z',
     'alibabacloud.com/help/functioncompute/billing-overview-of-fc (doc 2512929)',
     'alibaba-doc')
on conflict do nothing;
