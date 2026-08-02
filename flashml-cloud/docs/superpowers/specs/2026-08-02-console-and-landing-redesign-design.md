# Design — console and landing redesign

**Date:** 2026-08-02
**Status:** design, approved in conversation. Implementation plan not yet written.
**Supersedes:** the visual language in `apps/web/app/globals.css` (teal accent,
glass-everywhere) and the landing copy in `components/landing/*`.
**Depends on:** `2026-08-02-supply-side-positioning-note.md` — the positioning in
§2 is that note's conclusion, not a fresh claim.

---

## 1. Why

Two problems, one visual and one structural.

**Visual.** The console is `max-w-4xl` of stacked flat cards on every page. There
is no overview screen at all: signing in lands you on a marketing page. The
theme file already defines `.glass`, `.grain`, elevation, skeletons and a
react-flow theme, and almost nothing uses them — only the navbar is glass. The
single accent (teal) is applied to the nav, the buttons, the links, the artifact
URIs, the loss bars, the logo and the headings, so it carries no meaning.

**Structural, and larger.** `flashruntime.protocol.v1alpha1` records a 30-type
event ledger (`LEASE_CLAIMED`, `LEASE_EXPIRED`, `TASK_REQUEUED`,
`TASK_COMMIT_REJECTED`, `NODE_HEARTBEAT_LOST`, `FAILURE_CLASSIFIED`,
`RECOVERY_ACTION_SELECTED`, `RECOVERY_FROZEN`, `CHECKPOINT_MANIFEST_COMMITTED`,
…), a task/lease/attempt state machine that distinguishes *committed* from
*accepted*, checkpoint manifests with `hash_verified` / `restore_verified` /
`invalid`, a deterministic `FailureClass × mode → RecoveryDecision` table with
written reasons, and a `lost-work` endpoint.

The console surfaces none of it. `/jobs/[jobId]` shows a state badge, three
stats, an artifact list, and — for federated jobs — a round list. Everything
that makes the runtime worth using is invisible in the product built on top of
it.

The coordinator routes that hold this data exist but are agent-token only. So
"make the coordinator transparent" is mostly an API problem wearing a UI
problem's clothes, and this design covers both halves.

---

## 2. Positioning

Taken from `2026-08-02-supply-side-positioning-note.md` §3 and §6.

**What we sell:** an aggregation layer for **fault-tolerant, shardable
training** — hyperparameter sweeps, federated rounds, embarrassingly-parallel
work — across rented pods, home GPU rigs, preemptible instances, and spare
machines.

**The line the whole site hangs on**, from that note:

> Cheap supply and unreliable supply are the same supply.

This is the reframe. Fault tolerance stops being feature #7 and becomes the
reason the product can use compute nobody else can. It also inverts the demo:
not "look, we survived a flaky laptop" but "this is why we can run on what you
can actually afford."

**What we must not claim.** Not speed. The note and the M1 spec both say plainly
that over home links with small models, pooling is usually *slower* than one
machine, because every federated round ships weights out and deltas back and
bandwidth dominates. Not "run your 70B fine-tune across strangers' machines" —
the note says that claim would not survive a first customer and would burn the
credibility the fault-tolerance story earns.

**Volunteer laptops are one supported tier, not the pitch.** The current site
leads with them; that tier is rated worst on value and worst on support burden.

### 2.1 What this changes in existing copy

| Location | Now | Problem |
|---|---|---|
| `Hero.tsx` eyebrow | "Powered by RunPod Flash" | Advertises a competitor as our differentiator; also stale |
| `Hero.tsx` headline | "Distributed ML training as simple as a function call" | Modal's position, and simplicity is not what's hard here |
| `Hero.tsx` sub + code | K-Means, "built-in dataset and model" | July POC; we run arbitrary PyTorch from a repo |
| `layout.tsx` metadata | "machines people lend you… donated laptops" | The tier we've decided not to lead with |

---

## 3. Two registers

One design system, two tunings, split at the sign-in boundary.

**Landing — cinematic.** Deep near-black with a single atmospheric hero.
Oversized display type, glass panels floating over a live node graph, isometric
diagrams. Sells.

**Console — flat.** Same near-black, zero atmosphere, no gradients, no glass
behind data. Hairline borders, surface steps, dense rows. Works.

RunPod does exactly this and it is why their console is allowed to be plain —
the landing already did the selling. The only things crossing the boundary are
the accent and the typeface.

**Rule: glass floats, data doesn't.** `.glass` survives on the landing, the
command palette, sticky headers, drawers and toasts. It never sits behind a
number, a table row, a log line or an error.

---

## 4. Design system

Replaces the `:root` block in `apps/web/app/globals.css` wholesale.

### 4.1 Tokens

```css
--bg-rail        oklch(0.155 0.004 285)   /* left rail                     */
--bg             oklch(0.175 0.004 285)   /* content column                */
--surface        oklch(0.195 0.005 285)   /* card                          */
--surface-2      oklch(0.235 0.006 285)   /* chip, hover, input            */
--border         oklch(1 0 0 / 0.09)      /* the primary separator         */

--fg             oklch(0.97 0 0)
--fg-muted       oklch(0.70 0.008 285)

--accent         oklch(0.55 0.21 285)     /* indigo — INTERACTIVE ONLY     */
--accent-soft    oklch(0.55 0.21 285 / 0.14)
--accent-text    oklch(0.80 0.13 285)     /* two-tone headline second half */

--ok             oklch(0.72 0.17 150)     /* accepted, healthy, succeeded  */
--warn           oklch(0.78 0.14 80)      /* recovering, degraded, expired */
--danger         oklch(0.62 0.20 25)      /* failed, rejected              */

--radius         8px    /* cards */
--radius-sm      6px    /* buttons, chips */
--radius-tile    10px   /* icon tiles */
--radius-pill    999px  /* landing buttons only */
```

Depth comes from **border + surface step**, never shadow or bloom. The existing
`--shadow-*` and `.glow-*` rules go; `.glass` stays but is scoped to the landing
and to overlays.

**Colour discipline, enforced in review:** indigo appears only on things you can
click or that are currently selected — primary buttons, icon tiles, links,
active nav, active tab underline. Every status uses a semantic hue. If the
chrome uses colour, state stops reading. This is the failure of the current
teal.

**"Live" is motion, not hue.** A draining lease-deadline ring, an event row
appending, a task moving state. No colour is reserved for "running", which keeps
green/amber/red unambiguous.

### 4.2 Typography

Keep Geist Sans + Geist Mono. One rule, applied everywhere:

> **If a human wrote it, sans. If a machine emitted it, mono.**

Mono + `tabular-nums`: job ids, node ids, lease ids, hashes, losses, step
counts, byte counts, durations, timestamps, event type names, state names.
Sans: headings, descriptions, empty-state copy, button labels, form labels.

The current code does the inverse half — page titles are `font-mono` — and that
is a large part of why it reads as templated.

**Landing only:** two-tone headlines. First sentence `--fg`, second
`--accent-text`. Used once per section, never twice in a row.

### 4.3 Components

| Component | Shape |
|---|---|
| **Button** | Console: two variants — solid indigo, and ghost/outline. Landing: two pill variants — solid indigo pill, dark pill + white text + arrow. No third variant anywhere. |
| **Chip** | `--surface-2` fill, hairline border, 11px, `--radius-sm`. Used for spec facts and counts. |
| **Icon tile** | Rounded square, `--accent-soft` fill, `--radius-tile`, indigo glyph. Leads every list row and feature block. |
| **Eyebrow** | Landing only. Dark pill, 11px uppercase, `0.07em` tracking. |
| **Tabs** | Underline, indigo active. The default sub-navigation everywhere in the console. Never a dropdown where tabs fit. |
| **Segmented filter** | Chip group below tabs (`All · Running · Failed`). |
| **List row** | icon tile → name → chip meta row → status dot → kebab. |
| **Stat** | mono value, `label-caps` beneath. |
| **Status dot** | Still by default. The slow halo is reserved for genuinely live work — keep that rule from the current CSS. |
| **Empty state** | Outlined icon, heading, one-sentence explanation, primary action. Centred. |
| **Ledger row** | Coloured circular icon → sentence (actor `--fg`, verb `--fg-muted`) → mono id on line two → right-aligned timestamp. One bordered row per event. |
| **Summary panel** | Sticky right column on form pages: title, headline number, collapsible label→value breakdown, primary action pinned bottom. |

### 4.4 Motion

Motion only where it carries information: lease countdowns, events appending,
state transitions, the landing's scroll-linked demo. `prefers-reduced-motion`
handling in the current CSS is correct and stays. Decorative animation inside
the console is out — in a reliability console it reads as noise.

---

## 5. Landing

Dark, cinematic, one page. Atmosphere in the hero only; everything below is flat
with hairline-ruled sections.

**1 · Hero.** Two-tone headline on the positioning line. One sentence of
mechanism. Two pill CTAs. The proof element is a **live event ledger** streaming
down the right — real protocol type names, not invented ones. Node graph behind
it (reuse `NodeBackground`, but driven by lease/heartbeat state instead of random
drift).

**2 · The failure demo — the centrepiece.** Scroll-linked, three stages, the
active one bright with an indigo left rail and the others dimmed, synced to a
visual on the right:

1. `LEASE_CLAIMED` — work is claimed, not pushed
2. `NODE_HEARTBEAT_LOST` → `LEASE_EXPIRED` — a machine vanishes
3. `TASK_REQUEUED` → `TASK_COMMIT_ACCEPTED` — another resumes from the last
   valid manifest; `lost_steps` shown honestly

This is the page's argument. If only one thing gets built well, this is it.

**3 · How it works.** Three steps: point at a GitHub repo → preflight checks it
→ it runs across whatever compute you've attached, checkpointing as it goes.

**4 · What the runtime guarantees.** Full-bleed hairline grid, four blocks,
naming mechanisms rather than adjectives — leases not assignments, commit keys,
manifests not paths, a policy table not an agent. **Copy comes verbatim from
`recovery/policy.py` reason strings**, which are already better than marketing
prose:

> "deterministic application error — retrying burns money on a bug"
> "storage outage — pause instead of burning compute against a dead dependency"
> "a lost rank stops the group — restart all workers from latest valid checkpoint"

**5 · Where the compute comes from.** The three tiers, honestly rated — rented
pods, home rigs, spare machines — with what each is good for. This is the
aggregation story and it replaces the old "two doors" block.

**6 · Open source.** Apache-2.0, the public repo, the protocol package. Flower's
play: this is the credibility available today, when there are no customer logos.

**7 · Honest state.** Alpha; what works, what doesn't; **the bandwidth caveat
stated plainly**. For a reliability product with no logos, admitting limits is
the trust signal, and it pre-empts the vapour read.

**8 · Footer.** Pitch line, email capture, `● All systems operational` status
pill, socials, legal.

**Not built:** logo wall, testimonials, "trusted by N developers", uptime SLA
claims, pricing table, case studies, FAQ accordion. Each is either untrue today
or category filler.

---

## 6. Console

### 6.1 Shell

Replaces the top navbar. Full-width app shell.

```
⚡ FlashML                    [⇤]     │  Jobs              🔔  ● 3 online · 1 running   ◯
  Search                      ⌘K     │
 ─────────────────────────────────   │
  Overview                           │
                                     │
  RUN                          ⌃     │
   Jobs                              │
   Submit                            │
                                     │
  FLEET                        ⌃     │
   Machines                          │
   Activate                          │
   Activity                          │
                                     │
  INSIGHT                      ⌃     │
   Reliability                       │
 ─────────────────────────────────   │
  Docs · Status · Feedback           │
```

Rail is `--bg-rail`, collapsible groups with chevrons, active item is a filled
rounded block (no accent bar), utility group pinned at the bottom behind a
hairline.

The **fleet pill** in the top bar sits where RunPod puts the credit balance:
always-visible system state, so you never have to navigate to learn whether
anything is alive. It is the smallest, highest-value transparency affordance in
the whole design.

**One banner maximum**, dismissible.

### 6.2 Pages

| Route | Content |
|---|---|
| `/` (signed in) | **Overview.** Fleet strip (machines online / leases held / jobs running), active jobs list card, recent activity (last ~10 ledger rows), reliability sparklines. Replaces landing-on-sign-in. |
| `/jobs` | Table, not stacked cards. Columns: name · state · mode · progress chips · started · duration. Segmented filter (`All · Running · Recovering · Failed`). |
| `/jobs/[jobId]` | Header + state, then **underline tabs**: `Timeline · Tasks · Checkpoints · Artifacts · Spec`. Detailed in §7. |
| `/submit` | Two-column. Left: repo, ref, collapsible advanced sections. Right: **sticky summary** — preflight findings, resolved image, worker range, isolation tier, `Submit job` pinned bottom. |
| `/machines` | Table: name · platform · status · last seen · **reliability segments** (accepted/attempted) · revoke. Install instructions stay in the empty state, as today. |
| `/activate` | Device-code approval. **Route name is fixed** — it is the `verification_uri` the API returns and `flashnode login` prints, so renaming it breaks in-flight enrolments. Restyled only, stays phone-first. |
| `/activity` | Account-wide event ledger. Search + type filter + date range. Ledger rows. |
| `/reliability` | Stage 8 metrics — goodput, MTTD, MTTR, lost work, cost per completed job. |

No cross-job `/artifacts` page. Artifacts stay on their job; a global view has no
demonstrated need yet.

### 6.3 Empty states matter more than usual

The console is empty until someone attaches compute. `/machines` and `/jobs`
with zero rows become **centred explainer pages** — heading, one-paragraph
explanation, a diagram, the enrol command — not a sad empty card. RunPod does
this on Clusters and Storage and it is the right move for a product whose
console starts empty for every new user.

---

## 7. Coordinator transparency

The design centre. Everything below already exists in the runtime; none of it is
reachable from a browser today.

### 7.1 Job detail tabs

**Timeline.** The event ledger for one job, newest first, streaming. Ledger
rows, grouped by minute. Filterable by type group (lease / task / checkpoint /
recovery / artifact). A `RECOVERY_ACTION_SELECTED` row renders the *stored*
decision — action, scope, policy version, and the reason string from the policy
table — and never a generated explanation. Hard rule 5 (recovery actions are
typed, deterministic, logged) is a UI constraint here, not just a backend one.

**Tasks.** One row per task: `task_id` · state · attempts (`2/3`) · current
lease holder · lease deadline as a draining ring · `accepted` badge. Expanding a
row lists its attempts with `node_id`, `outcome`, `output_sha256`, and — per
hard rule 4 — **`accepted` distinguished from `committed`**, because that
distinction is where money and credit are decided.

**Checkpoints.** Latest valid manifest per task: `step`, `world_size`,
`framework`, part count, total bytes, and the validation level rendered as a
three-state badge — `hash_verified` / `restore_verified` / `invalid`. Plus
**lost work**: `failed_at_step − manifest.step`, shown as steps and as elapsed
time. This is the number that proves the product's claim.

**Artifacts.** As today, plus `sha256` and backend.

**Spec.** As today. Absent on federated jobs, which is already handled correctly.

### 7.2 Read endpoints to add

All owner-scoped on `apps/api`. All follow the established rule from
`lib/cloud-api.ts`: **a job that is not yours returns 404, never 403**, so a
guesser cannot learn which id is real.

| Endpoint | Source | Notes |
|---|---|---|
| `GET /v1alpha1/jobs/{id}/events` | coordinator `/jobs/{id}/events` | Paginated, `?since=`, `?types=`. |
| `GET /v1alpha1/jobs/{id}/events/stream` | same | SSE. Replaces the 2.5s poll in `app/jobs/[jobId]/page.tsx`. Named as a Stage 6 target in `AGENTS.md`. |
| `GET /v1alpha1/jobs/{id}/tasks` | coordinator `/jobs/{id}/tasks` | Task + live lease + attempt history. |
| `GET /v1alpha1/jobs/{id}/checkpoints` | checkpoint catalog | Latest valid manifest per task + validation level. |
| `GET /v1alpha1/jobs/{id}/lost-work` | `.../checkpoints/lost-work` | Aggregated across the job's tasks. |
| `GET /v1alpha1/fleet` | machines + coordinator nodes | Drives the top-bar pill. Cheap, cacheable. |
| `GET /v1alpha1/metrics/reliability` | event ledger | Stage 8. Every event it needs already exists. |

**No new wire schema.** Per hard rule 2, anything a FlashNode must understand
belongs upstream in `flashruntime.protocol`. These are read projections of
existing types, added to the cloud API only.

**Typed client additions** go in `lib/cloud-api.ts`, which stays the single
source of response types; pages define no shapes of their own. Federated jobs
carry no spec and no tasks — every new type must model that as legitimately
absent, the way `JobRecord` already does, rather than assuming a coordinator job.

---

## 8. Out of scope

- Light theme. Decided against; tokens are structured so it remains possible.
- Billing, credits, marketplace UI.
- Any change to `flashruntime` or `flashnode`.
- GPU capability display — `NodeCapabilities.gpus` is always empty today
  (supply-side note §5.1). The machines table leaves room for it and shows
  nothing until the probe lands.
- Result verification UI (S5).

---

## 9. Decomposition

This is too large for one implementation plan. It splits into four, in
dependency order. Each is independently shippable and leaves the app working.

| Plan | Contents | Depends on |
|---|---|---|
| **P1 — design system + shell** | Token replacement in `globals.css`, component inventory (§4.3), left-rail shell replacing `Navbar`, restyle of the five existing pages with no new data. | — |
| **P2 — read endpoints** | The seven endpoints in §7.2 on `apps/api`, owner scoping, typed client additions in `lib/cloud-api.ts`, tests. No UI. | — (parallel with P1) |
| **P3 — coordinator transparency UI** | Overview page, job-detail tabs, activity page, machines table, SSE swap. | P1 + P2 |
| **P4 — landing** | Positioning rewrite, section build-out, the failure demo. Needs the recorded run named in §10.1. | P1 |

P1 and P2 can run concurrently. P4 is the only one gated on evidence rather than
code.

## 10. Risks and open questions

### 10.1 The failure demo needs a real recorded run

Fabricating one would be dishonest on a page whose entire argument is honesty.
It needs a captured event ledger from an e2e run with a real mid-round machine
loss — `make e2e-demo` plus a deliberately killed agent. This is the gate on P4,
and it is evidence work, not UI work.

### 10.2 SSE through Render

Buffering and idle timeouts on the platform's proxy need checking before the
2.5s poll is removed. Keep polling as the fallback path rather than deleting it.

### 10.3 Reliability metrics need definitions before they need a chart

Goodput, MTTD and MTTR each have several defensible definitions. Pick one each,
write it down in the plan for that page, and only then draw anything. A
plausible-looking number with an unstated definition is worse than no number on
a page selling measurement.

### 10.4 Cost of the inversion

The current `globals.css` is a carefully reasoned dark system. This keeps its
structure — surface steps, tabular numerals, reduced-motion handling, the
two-ring focus treatment — and replaces its palette and its glass usage. Every
badge, chart and panel still needs a contrast pass; the token swap is not the
whole job.

### 10.5 Unverified demand

The supply-side note §7 is explicit that nothing in it is demand evidence. This
design commits the site to the aggregation story on the strength of supply
reasoning alone. Worth asking one real user before P4 ships, not before it
starts.

---

## 11. Implementation notes

Tools verified present on this machine, to be used when building rather than
now: `ui-ux-pro-max` (`design`, `ui-styling`, `design-system`),
`design-taste-frontend`, `frontend-design`.

**Pending:** the 21st.dev MCP is registered as `magic` and failing auth
(`-32001`). It needs a fresh key from https://21st.dev/mcp, added by the owner
directly so the key stays out of agent transcripts. Useful for component
scaffolding at implementation time; not required for this spec.

Existing stack is already right for all of the above — Next 16, React 19,
Tailwind v4, shadcn, `@xyflow/react` for the node graph, `motion` for the
scroll-linked demo. No new dependencies are needed.
