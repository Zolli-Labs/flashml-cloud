# Device profiles and the provider network

2026-08-13. Owner-approved direction: capture what each volunteer or rented
machine really is, show it Akash-style (map, capacity, per-provider
analytics), and feed the routing layer. Phase 1 is BUILT and live on dev as
of this doc; phases 2+ are the contract for what comes next. Companion:
`docs/research/2026-08-13-automatic-routing-marketplace-matching.md` (the
routing research this spec supplies; its §6 Phase 0 names this work as the
substrate) and `2026-08-11-zolli-marketplace-design.md` (vocabulary M1–M14).

## Principles (evidence-derived, adopted with the routing team)

1. **Declared, not detected.** Location and every future subjective field is
   owner-declared or venue-resolved (`geo_source in ('declared','venue')`),
   per the standing rule that user/host choices drive knobs and internal
   tables only resolve. No IP sniffing in v1.
2. **Store raw events, derive scores at read time.** `machine_uptime_hours`
   holds hour buckets; percentages are computed per query. A score formula
   must never be frozen into a migration or an API.
3. **One owner per formula.** Acceptance rates come from
   `metrics.acceptance_rates` only (None below MIN_EVIDENCE=5 — unproven,
   never zero). GPU class derivation stays OUT of this layer entirely: the
   profile surfaces raw nvidia-smi identity; unifying the two existing
   classifiers (`marketplace.capability_class` smallest-GPU vs
   `router/estimator.hardware_class` largest-GPU) is a named open dependency,
   and nothing here may become a third.
4. **Anonymity by default.** Non-owners see `prov…XXXXXX`, coarse location,
   specs, uptime, lease counts — never names, node ids, owner identity, or
   credits. Signed-in users only.
5. **The money ledger is append-only.** Machine Delete is a tombstone: the
   profile is scrubbed, `node_id` + credit history stay. "Cancel deletes the
   history" is answered by scrubbing identity, not by erasing the ledger.
6. **No per-job-opportunity cost for hosts** (Akash bid-economics lesson).

## Phase 1 — built (commits 45d84c1, 95eb3bd, dc7cf74)

- **Migration 0029**: `machines.geo_*` (country/region/city/lat/lon/source),
  `machine_uptime_hours(machine_id, hour_ts, beats)` written inside
  `db.touch_machine_last_seen` under a savepoint (an uptime write may never
  cost the `last_seen_at` write that `capacity.reconcile` destroys rentals
  on).
- **API**: `GET /v1alpha1/network/providers` (bulk snapshot; hard filters
  only — judgment stays in the matcher), `GET …/providers/{machine_id}`,
  `PATCH /v1alpha1/machines/{machine_id}/location`. Owner block carries
  per-machine credits from the `earned_accepted_work` ledger leg
  (millicredits), joined via `attempts.lease_id`.
- **Console**: `/market/providers` (world map with arcs to the coordinator,
  committed-capacity donuts, providers table) and `…/[id]` detail (30-day
  lease chart, 24h uptime strip, attempts breakdown, specs, owner panel with
  set-location). Honest refusals: "n/a — low evidence" under 5 resolved,
  "no data yet" for an empty uptime ledger.
- **Anchors**: persistent owner-operated RunPod pods under
  `scripts/anchors/anchorctl.py` (stop≈1s, resume≈15s measured — see
  `2026-08-13-anchor-resume-vs-fc-hibernation.md`). Anchors are exempt from
  the capacity layer's destroy-only rule by never entering it.

## Phase 2 — the contract (in rough priority order)

1. **Trust lifecycle**: `machines.trust_state` in
   `('unverified','verified','deverified')`, automated transitions, failures
   carrying machine-readable reasons; auto-restored when the cause clears
   (Vast/io.net pattern). **Deverified means excluded from the MARKET BOOK**
   — `open_asks` omits it, so no new priced matches or entitlements — and
   nothing more: a deverified machine still pulls workspace-free jobs in its
   own pools, because the coordinator's capability gates remain the only
   claim-side authority (M1 workspace-free and reliability-ranks-never-
   excludes both survive on the free path; the trust gate governs the priced
   path only).
2. **Allocatable vs host-visible CPU**: rented pods report the host's cores
   (measured: 96 on a 2-vCPU pod). Two fields; `capability_class` reads
   allocatable. Needs a flashnode change → release-gated (four pin sites).
3. **Contribution limits**: wire the existing, fully-unwired
   `flashnode.config.HostPolicy` (max_task_cpus/memory, workdir quota,
   active_hours) — detect defaults, let the host override, advertise the
   result in `NodeCapabilities`. This is the original "how much can this
   laptop give" ask; release-gated.
4. **Benchmark probes**: implement the four existing probe names
   (cpu_hash/mem_bandwidth/disk_write/net_down mbps) + net_up +
   rtt_to_coordinator_ms (free at heartbeat); store raw with `measured_at`;
   connectivity tier derived at read time.
5. **`resume_seconds`** as a machine attribute (measured, with measured_at)
   so wake latency is priceable for anchors and future hibernating venues.
6. **Abandoned visibility**: coordinator tells the cloud ledger the
   difference between expired and abandoned (`attempts.outcome='abandoned'`
   is reserved and unwritten); churn history is the volunteer-hardware
   differentiator.
7. **RunPod venue adapter + console Stop/Run**: a second entry in
   `capacity/registry.py` with a stop/resume-capable persistent mode for
   anchors, powering machine Stop/Run buttons. Until then `anchorctl` is the
   only driver.
8. **Listing pause/resume endpoints**: `marketplace.pause_listing`/
   `resume_listing` exist with no routes and no buttons; two endpoints + two
   buttons finish a host's "stop offering without withdrawing".
9. **Storage/disk capture** (agent-side detection + `storage_bytes` in
   totals — the console's fourth donut appears by itself when the API sends
   it), coarse IP-geo fallback for hosts who never declare, and a
   `.console-instrument` alias in `globals.css` for the dark panel scope.
10. **Grant-mechanism prerequisites** (from routing Phase 1's final review;
    load-bearing the moment `live_matches_for_machine` gets its production
    caller): `can_cover` enforced at grant time (`marketplace.py` ~1932's
    docstring already assumes it) and a reaper for stale granted matches
    (`close_match` exists with no caller). Also then: an Entitlements column
    on `/market/providers` — matches-by-machine, state in granted/claimed —
    beside the claim-side active-lease count, which it leads by design.

## Non-goals

Latency-matrix placement (coarse geo + bandwidth is confirmed sufficient;
checkpoints relay through the coordinator), free-form provider attributes
(Akash's schema-drift lesson), any GPU classification in this layer, and
public-to-the-internet provider pages before the pre-launch security audit
closes.
