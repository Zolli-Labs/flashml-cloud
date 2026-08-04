# Pools v1.1 — device control, trust visibility, group links, connect panel

**Date:** 2026-08-03
**Status:** approved design (brainstormed with the owner, same day the v1
release shipped).
**Origin:** the owner's own dogfooding of team pools
(`2026-08-03-team-pools-design.md`). Four rough edges surfaced in the first
hour of real use: no way to choose which machines serve a pool, nothing
showing which workers are sandboxed vs trusted, one-time-per-person invite
links, and out-of-band Colab/RunPod onboarding.

---

## 1. Decisions made during brainstorming

1. **All four pieces ship together** — per-device control, trust badges,
   standing group link, connect panel. They are one coherent "control +
   visibility + onboarding" pass over the pool surface.
2. **Per-device control is OPT-IN** (owner's call, against the
   continuity-first recommendation): a machine serves NO pool until its
   owner ticks it in. This replaces v1's owner-inheritance ("join a pool,
   all your machines serve it"). Explicitness beats continuity; the v1
   behavior surprised its own designer.
3. **Enrolling through a pool's Connect panel auto-attaches** the new
   machine to that pool — opt-in stays the rule, but the natural flow
   performs the opt-in, so onboarding stays one step.
4. **Architecture: the binding narrows the stamp.** No protocol,
   coordinator, or flashnode change of any kind. Rejected: agent-side pool
   selection via env var (self-reported pool participation is exactly what
   the server-side stamp exists to prevent) and coordinator-side bindings
   (pools are a cloud concept; protocol churn for nothing).

## 2. Architecture

One new table and one changed query, both inside `apps/api`; everything
downstream is untouched because placement only ever sees the stamped list.

- **Migration 0008**: `machine_pools (machine_id uuid references
  public.machines(id) on delete cascade, pool_id uuid references
  public.pools(id) on delete cascade, created_at, primary key (machine_id,
  pool_id))`, RLS enabled with zero policies, index on `pool_id`. Plus four
  boolean columns on `public.machines` for the badges (§4):
  `sandbox_capable`, `argv_capable`, `unsandboxed_argv_capable`,
  `module_capable`, all `not null default false`.
- **The stamp narrows**: `pool_ids_for_machine(db, machine_id)` replaces
  `pool_ids_for_machine_owner` at both proxy call sites (register,
  heartbeat). It returns bindings **joined against the owner's live
  memberships** — membership remains the authority; a binding to a pool the
  owner has left (or was removed from) grants nothing. Same str-cast sorted
  output, same fail-closed-to-`[]` on lookup failure, same
  overwrite-never-merge stamping. The seventh placement gate, the heartbeat
  refresh, and the e2e revocation proof all apply unchanged.
- **Migration behavior change, stated plainly**: on deploy, existing pools
  go quiet — machines serve nothing until ticked in. Acceptable and
  intended at alpha scale (one pool, one owner today); the migration header
  and PROGRESS entry both say so.

## 3. Per-device control (UI + API)

- Pool page gains **"Your machines"**: each member sees only their OWN
  machines, one checkbox per machine (with the trust badge beside it, §4).
  Tick = `PUT /v1alpha1/pools/{pool_id}/machines/{machine_id}` (create
  binding), untick = `DELETE` (remove binding). Both member-scoped: the
  pool must be fetchable via `fetch_pool_for_member` AND the machine owned
  by the caller (`fetch_machine_for_owner`) — 404 doctrine on both.
- A binding change reaches placement at the machine's next heartbeat — the
  mechanism the e2e revocation test already pins. The UI says so ("takes
  effect within ~30s while the agent is running").
- Machines page shows read-only pool chips per machine (which pools this
  machine is ticked into), from a `pools` field added to the machines
  listing response.

## 4. Trust/status badges

- The register proxy already parses the body to stamp pools; it now also
  persists the four capability booleans from the registration onto the
  machines row — **best-effort, same contract as `last_seen_at`** (display
  data must never fail a registration), and **overwrite-not-merge** (the
  row reflects the latest registration; a lying agent only mislabels its
  own display row — placement never reads these columns).
- UI derives one badge per machine:
  - **Sandboxed** — `argv_capable` or `sandbox_capable` (runs pool jobs in
    Docker).
  - **Trusted — unsandboxed** — `unsandboxed_argv_capable`, amber, the same
    warning tone as the submit page's notice.
  - **Modules only** — neither of the above (plain `subprocess` runner;
    pool repo jobs never place here).
- Badge derivation is a pure exported predicate with unit tests (house
  style, like `pool-selection.ts`). Shown on the machines page rows and
  beside each checkbox in the pool page's "Your machines".

## 5. Standing group link

- `POST /v1alpha1/pools/{id}/invites` gains `uses` (default **10**, cap
  **100**; bool rejected, bounds enforced like `expires_hours`). Expiry
  default moves to **30 days** (cap stays 90). Existing `uses=1` calls keep
  working — no API break.
- New `DELETE /v1alpha1/pools/{id}/invites` — owner-only (404 doctrine),
  deletes ALL outstanding invites for the pool. Regenerate = revoke + mint,
  in the UI.
- New owner-only `GET /v1alpha1/pools/{id}/invites` returns the outstanding
  link's state — `{uses_remaining, expires_at, created_at}` or empty —
  **never any token material** (only the hash exists server-side).
- Pool page invite section becomes: link state line ("8 uses left · expires
  in 24 days"), **Regenerate** (shows the new link once, as today), and
  **Revoke**. The mint-per-person UI goes away.
- The bearer-secret guidance moves with it: the link is a password for
  pool membership; regenerate if it leaks.

## 6. Connect panel + auto-attach

- Pool page gains **"Connect a machine"**: two tabs, content from the v1
  guides, in-product with copy buttons and the API URL pre-filled —
  **Colab** (the paid-tier ToS box FIRST, then the three cells) and
  **RunPod** (the three terminal commands, no ToS caveat, "pods cannot nest
  Docker so `--runner trusted` is correct here").
- Auto-attach rides the existing device-approve hop: the panel's activate
  link is `/activate?pool=<id>`; the activate page forwards it;
  `POST /v1alpha1/device/approve` accepts optional `pool_id`. Server-side:
  the APPROVER must be a member of that pool (`is_pool_member`, 404
  doctrine — approving is a browser action by the signed-in member, so the
  authority is theirs, never the machine's); the binding is written in the
  same transaction as the approval. An ABSENT `pool_id` approves without a
  binding, exactly as today; a PRESENT but malformed/unknown/not-your-pool
  `pool_id` refuses the approval with 404 ("unknown pool") rather than
  silently approving unbound — a panel-driven flow always carries a valid
  pool, so failing loud beats a machine that enrolls and then sits idle
  with no visible reason. (Corrected during planning: an earlier draft said
  invalid approves without binding; the 404 rule governs.)
- No agent change: the machine still enrolls with the same three commands;
  attachment is decided browser-side at approval.

## 7. Testing

- **API**: binding CRUD with both-sided 404 scoping; the narrowed stamp
  (bound+member stamps; bound+non-member does NOT; member+unbound does NOT
  — the opt-in default; forged agent `pools` still overwritten); approve
  with valid/invalid/other-pools `pool_id`; invite `uses` bounds + revoke
  → consume returns None + regenerate lifecycle; badge columns persisted
  on register, overwritten on re-register, never trusted from the agent
  row for anything but display.
- **Web**: badge-derivation and link-state predicates (pure-logic house
  style); client tests for the new calls' shapes.
- **e2e**: none needed — the coordinator sees only the stamped list, which
  the existing pool e2e suite already exercises end to end. That is the
  architecture's payoff, stated here so nobody adds a redundant one.

## 8. Not in v1.1

Leave/remove-member routes (SQL runbook stands); request-to-join approval;
per-pool machine caps; generated `.ipynb` downloads (copy-cells only);
badge-based placement (placement reads capabilities from registration, as
in v1 — the badge columns are display-only by construction).
