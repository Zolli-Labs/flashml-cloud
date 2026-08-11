# Zolli Cinematic Motion Landing Redesign

**Date:** 2026-08-09
**Scope:** Zolli Cloud public landing route and the shared marketing navigation/footer it uses
**Status:** Approved direction; written specification awaiting review
**Parent:** `2026-08-09-zolli-proof-led-landing-expansion-design.md`

## 1. Decision

Redesign the expanded Zolli landing page as a balanced cinematic product story.
The page keeps the current information architecture, exact credibility evidence,
public routes, and conversion hierarchy, but replaces the repeated dark-section
and bordered-card treatment with intentional surface changes, one signature
scroll sequence, and a small number of high-impact motion systems.

The selected visual direction combines:

- **Signal / Canvas:** graphite, mineral ivory, sand, and orange chapters;
- **Kinetic Terminal:** live protocol language, velocity type, and observable
  state transitions;
- **Pinned system journey:** one topology transforms while the visitor follows
  how machines become nodes and parallel work becomes one accepted result.

This specification supersedes only the landing-page restrictions that require
one dark marketing theme and prohibit decorative or scroll-linked motion. It
does not loosen the console's motion discipline, the behavior-neutral rebrand
rule, accessibility requirements, evidence boundaries, or legal-copy limits.

## 2. Goals

1. Restore visual rhythm through meaningful dark, light, sand, and orange
   surfaces rather than a sequence of nearly identical black sections.
2. Make the product workflow the most memorable visual on the page.
3. Explain Zolli in operational order: connect machines, register capacity,
   submit a job, schedule parallel tasks, checkpoint progress, recover failure,
   and accept one result.
4. Add attention-catching motion without looking like a generic AI landing page
   or obscuring technical credibility.
5. Preserve `Open console` as the dominant conversion action and Calendly as
   the secondary assisted path.
6. Keep the landing useful when JavaScript, animation, or pointer input is
   unavailable.

## 3. Non-goals

- No change to console routes, APIs, database behavior, authentication, or the
  runtime protocol.
- No WebGL, video background, stock imagery, particle field, aurora gradient,
  cursor replacement, or smooth-scroll hijacking.
- No imported 21st.dev component wholesale. Registry ideas may inform motion
  and composition, but Zolli owns the final markup and visual vocabulary.
- No invented percentage, speedup, customer, uptime, savings, pricing, or
  testimonial claim.
- No redesign of `/contact`, `/privacy`, `/terms`, or `/security` beyond shared
  navigation/footer compatibility.
- No new carousel, pricing table, customer-logo strip, or newsletter form.

## 4. Pre-flight design plan

The deterministic seed is the 368-character approved design brief used for the
visual comparison.

```text
python rng(seed=368) -> hero="Editorial Split", typography="Satoshi + Geist Mono"
python rng(seed=368) -> components=["Recovery card stack", "Pinned system journey", "Expanding architecture layers"]
python rng(seed=368) -> motion=["Shutter text reveal", "Scroll-pinned topology transformation"]
```

### AIDA check

- **Navigation:** minimal graphite navigation with a dominant console action.
- **Attention:** editorial split hero with topology motion.
- **Interest:** light evidence and platform chapters followed by the pinned
  system journey.
- **Desire:** workload velocity rail, expanding architecture layers, and the
  recovery proof stack.
- **Action:** full-width orange closing chapter and restrained footer.

### Hero math

The desktop headline uses `max-w-[78rem]` and a responsive display size capped
at `7.5rem`. The approved copy must occupy two lines at 1440px and no more than
three lines at 1024px. It has no stamp icon, stat row, pill cluster, or badge.

### Dense-grid math

The architecture composition uses a 12-column, two-row dense grid:

- Control: 7 columns × 2 rows = 14 cells;
- Execution: 5 columns × 1 row = 5 cells;
- Integrity: 5 columns × 1 row = 5 cells.

All 24 cells are occupied. The grid uses `grid-flow-dense`; mobile replaces it
with a single ordered column.

### Label and contrast sweep

There are no labels such as `SECTION 01` or `QUESTION 05`. Operational labels
such as `LEASE_CLAIMED` remain because they are product evidence. Buttons always
use dark text on orange/light fills or mineral-white text on graphite fills.

## 5. Visual system

### 5.1 Surface choreography

The target page-area balance is approximately:

- **35% graphite/dark:** active systems and mechanics;
- **55% mineral ivory/sand:** explanation, proof, and buying confidence;
- **10% orange:** transitions and conversion.

This is a composition target, not runtime measurement or a rigid alternating
pattern. Adjacent light sections may use ivory and sand to form a coherent
chapter. A surface changes only when the narrative mode changes.

Use the existing tokens where possible and add landing-scoped aliases:

```css
--landing-graphite: #0b0d0e;
--landing-graphite-raised: #15191a;
--landing-ivory: #f2efe6;
--landing-sand: #ded8cb;
--landing-ink: #111415;
--landing-orange: #f36b32;
--landing-green: #4ba77b;
```

Light sections must reset foreground, muted foreground, border, selection, and
focus colors locally. Do not fake light sections by placing isolated white cards
on the global dark canvas.

### 5.2 Texture and geometry

- Apply one subtle fixed grain layer across the landing at low opacity.
- Use hairline rules, clipped rectangles, SVG paths, and squared status marks.
- Reserve rounded corners for real contained interfaces such as the ledger.
- Avoid repeated equal-height cards. Use lanes, bands, ledgers, dense grids,
  and editorial rows instead.
- Maintain a single lighting direction for raised dark insets.

### 5.3 Typography

- The pre-flight exercise selected Satoshi, but the repository has no approved
  Satoshi asset or pinned source. This implementation therefore retains the
  existing Instrument Sans for marketing display and body copy rather than
  introducing a remote font or unreviewed license.
- Machine state and protocol events: existing Geist Mono.
- Headlines remain wide, tightly tracked, and limited to two or three lines.
- Light sections use dark ink; dark sections use mineral white.

## 6. Page narrative

### 6.1 Navigation — graphite

Keep the current destinations and behavior. On the landing only, the bar begins
transparent over the hero and gains an opaque graphite surface and hairline
border after leaving the hero. Do not turn it into a floating glass pill.

### 6.2 Hero — graphite

Keep `Compute that finishes the job.` and the existing supporting claim. Use an
editorial split: wide copy on the left and a Zolli topology on the right. The
topology is an SVG/DOM system diagram, not an illustration or particle effect.

Entrance sequence:

1. a shutter reveal exposes the two headline lines;
2. the control-plane ring draws on;
3. machine nodes resolve from low opacity and connect;
4. one task token travels from the control plane to a worker;
5. the primary `Open console` action enters before Calendly.

After entry, motion settles. Pointer parallax is limited to the topology and
never moves text or actions.

### 6.3 Evidence — mineral ivory

Keep the exact values `30`, `2`, `5`, and `1` and their approved qualifiers.
Replace four bordered metric cards with a typographic evidence ledger: large
numbers on one baseline, expanding rules, and source context beneath. Values may
reveal on entry but must not count upward because the displayed numbers are
evidence, not live telemetry.

### 6.4 Platform — sand

Replace the matrix-card appearance with machine lanes. macOS arm64 and Linux
x86_64 connect to the same control-plane rail as `Proven`; Windows 11 remains
`Preview`. Execution integrations appear as a lower technical strip, each with
the exact `Supported` label.

### 6.5 How Zolli works — graphite signature sequence

This is the page's main interaction. At desktop widths, a single system stage is
pinned while six narrative steps scroll beside it:

1. **Connect machines:** run a Zolli node on infrastructure the operator owns or
   connect compatible cloud capacity.
2. **Register capacity:** the control plane sees node capabilities, health, and
   supported execution paths.
3. **Submit one job:** the operator supplies the repository and workload
   definition through the existing console flow.
4. **Split and lease tasks:** the control plane assigns bounded parallel work to
   available nodes.
5. **Checkpoint progress:** workers execute in parallel and commit verified
   progress.
6. **Recover and accept:** a missing heartbeat expires ownership, another worker
   resumes from the verified checkpoint, and one result is accepted.

The same topology transforms at every step. Nodes enter, task tokens fan out,
progress bars advance, one node visibly drops, work returns to the last verified
checkpoint, and the accepted commit resolves the sequence. A narrow protocol
ticker uses real event names such as `LEASE_CLAIMED`,
`CHECKPOINT_MANIFEST_COMMITTED`, `NODE_HEARTBEAT_LOST`, `TASK_REQUEUED`, and
`TASK_COMMIT_ACCEPTED`.

At widths below 768px, pinning and horizontal movement are removed. The stage
becomes a static diagram followed by six progressive step panels. Each panel
animates only on entry and remains readable without interaction.

### 6.6 Workload fit — mineral ivory

Borrow the kinetic-terminal energy through a horizontally moving typography
rail for the four approved workload families. Normal vertical scroll may scrub
the rail on desktop, but it must not trap the user in a sideways-scrolling area.
The supporting descriptions use offset editorial rows instead of cards.

### 6.7 Architecture — graphite raised

Use the verified 12-column dense composition from the pre-flight plan. The
Control layer anchors the left side across two rows. Execution and Integrity
expand on the right as their relevant workflow state enters. The three layers
remain visible and understandable before animation runs.

### 6.8 Recovery — mineral ivory with graphite inset

Use an asymmetrical split: large `Lost machine. Verified recovery.` copy and the
exact `failure at step 35 → checkpoint at step 30 → 5 steps lost` proof on the
light surface; the real event ledger sits in a dark inset. Three recovery cards
stack subtly as the section enters, then stop. The sample-data disclosure stays
adjacent to the ledger.

### 6.9 Services — sand

Replace the equal four-card grid with two editorial service rows separated by
rules. Each row contains two services, a short outcome, and one shared Calendly
action. This remains calm after the recovery sequence.

### 6.10 FAQ — mineral ivory

Keep native `details`/`summary` semantics, exact seven questions, and answer
boundaries. Style them as large typographic rows, not dark accordion cards.
Opening a row expands a rule and reveals copy with a short opacity/clip transition.

### 6.11 Closing action — orange

Use a full-width orange chapter with the approved line `Bring the fleet. Keep
the progress.` The background is solid, not gradient. `Open console` is a large
dark high-contrast action. `Talk to Zolli` is a secondary text action. A compact
protocol line resolves into `TASK_COMMIT_ACCEPTED` as the section enters.

### 6.12 Footer — graphite

Retain the existing product, resource, company, and legal destinations. Reduce
the visual treatment to one typographic grid and a strong top rule. No animated
footer spectacle competes with the closing action.

## 7. Motion architecture

### 7.1 Libraries

- Add pinned versions of `gsap` and `@gsap/react` for ScrollTrigger timelines,
  pinning, scrubbing, and scoped cleanup.
- Retain the installed `motion` package for hover, press, menu, and small
  component transitions.
- Do not add a smooth-scroll library, WebGL renderer, or shader dependency.

### 7.2 Component boundaries

Keep the route and most content server-rendered. Client boundaries are limited
to motion that needs browser state:

- `LandingMotionContext` — reduced-motion and breakpoint coordination;
- `HeroSystemStage` — hero topology and entrance timeline;
- `SystemJourney` — pinned workflow stage and six steps;
- `WorkloadVelocityRail` — scroll-linked typography;
- `RecoveryStack` — one-shot stacked recovery reveal;
- `SectionReveal` — reusable shutter/rule reveal with no content dependency.

Static data arrays own machine labels, integration states, workflow steps, and
protocol events. Motion components consume that data; they do not duplicate
marketing claims.

### 7.3 Motion rules

- Animate only `transform`, `opacity`, SVG stroke properties, and clip paths.
- Use `gsap.context()` / `useGSAP()` cleanup so development remounts do not
  duplicate triggers.
- Never hide essential content before the client timeline initializes.
- Continuous animation is limited to a restrained settled topology signal and
  stops when the tab is hidden.
- Pointer effects apply only to pointer-fine devices.
- Native scroll remains authoritative; no wheel or touch event interception.

### 7.4 Reduced motion

Under `prefers-reduced-motion: reduce`:

- all content renders in its final visible state;
- the workflow does not pin or scrub;
- task tokens and tickers do not loop;
- disclosure and navigation transitions remain near-instant;
- section order, evidence, and actions remain unchanged.

## 8. Progressive enhancement and failure handling

- Server-rendered headings, copy, links, evidence, and FAQ remain available if
  client JavaScript fails.
- A GSAP import or timeline failure must leave the final static layout visible.
- Resize/orientation changes rebuild scoped triggers without duplicating them.
- Font failure uses the existing Instrument Sans fallback without layout loss.
- No animation state controls authentication, navigation, or CTA destinations.
- Existing public-route middleware behavior remains unchanged.

## 9. Accessibility

- Preserve skip-link behavior, semantic section headings, focus indicators, and
  minimum 40px targets.
- Workflow steps are an ordered list. The topology is decorative when the same
  state is present in text; otherwise it receives a concise accessible label.
- Color never carries platform or protocol state alone.
- Pinned content must not trap keyboard focus or obscure focused elements.
- External Calendly actions retain exact URL, target, rel, and accessible
  new-tab announcement.
- Light and dark token pairs must meet WCAG AA contrast for normal copy.

## 10. Performance targets

- Load GSAP only on the landing route and keep motion components behind small
  client boundaries.
- Avoid canvas/WebGL, large image assets, and layout-property animation.
- The hero must be useful before animation hydration.
- No horizontal overflow at the five approved QA widths.
- Browser profiling must show no sustained animation work after sequences settle.
- Pause continuous visual updates when the document is hidden.

## 11. Testing and verification

Follow test-first implementation.

Automated contracts cover:

- exact section order and landing surface assignments;
- exact evidence values and unsupported-claim guards;
- the seven workflow steps and real protocol event names;
- all platform state labels;
- dominant/secondary CTA destinations and attributes;
- server-visible content before motion hydration;
- mobile and reduced-motion fallbacks;
- no layout-property animation or smooth-scroll interception;
- preserved FAQ semantics and public route behavior.

Final verification requires:

- ESLint, `tsc --noEmit`, full Vitest, and `git diff --check`;
- production Next.js build;
- browser QA at 1440×900, 1024×768, 768×1024, 390×844, and 375×812;
- screenshots at hero, evidence/platform transition, each workflow milestone,
  workload rail, recovery proof, FAQ, and closing action;
- keyboard, focus, FAQ, CTA, route, console-error, and overflow checks;
- normal-motion and reduced-motion passes;
- authenticated console visual QA remains a separate concern because this scope
  does not change console pages.

## 12. Acceptance criteria

1. The landing does not read as a continuous dark page.
2. Dark, light, sand, and orange chapters match the approved balanced cinematic
   choreography.
3. The pinned system journey accurately demonstrates the end-to-end Zolli
   workflow without inventing product behavior.
4. The page has three clear attention peaks—hero, workflow, and closing action—
   with quieter explanatory sections between them.
5. Components no longer repeat a generic equal-card pattern.
6. Motion is smooth, scoped, progressive, mobile-safe, and reduced-motion-safe.
7. Every existing CTA, legal route, credibility statement, and public-route
   behavior remains correct.
8. No commit, push, merge, or deployment occurs before the user approves the
   completed local preview.
