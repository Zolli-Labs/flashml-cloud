# Zolli Vision-Led Landing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reframe the existing Zolli landing page around an open compute
allocation network, while preserving its visual system and coordinator-map
SVG.

**Architecture:** Keep the existing landing components and surface system.
Add one isolated CSS-driven hero role switch and two focused narrative
components before moving the existing proof, workload, compatibility, and
technical sections into the approved order. Update existing copy/data at their
current ownership boundaries; do not introduce backend or coordinator work.

**Tech Stack:** Next.js 16, React 19, TypeScript, Tailwind CSS v4, existing
landing CSS, Vitest, React server rendering, existing lightweight React DOM
test harness.

## Global Constraints

- Preserve the current visual system: no new color, type, spacing, surface,
  card, or illustration system.
- Do not edit any file below
  `flashml-cloud/apps/web/components/landing/coordinator-map/`.
- Do not edit `flashml-cloud/apps/web/lib/coordinator-map.ts` or any
  coordinator-map geometry, paths, phases, labels, or timing.
- The hero copy switch is independent of the SVG and animates only opacity and
  vertical translation.
- Keep the stable H1 and explanation motionless. With reduced motion, show
  both market-role messages as ordinary stacked copy.
- The primary audience is a person seeking compute. The secondary audience is
  a person providing unused machines.
- Host cash earnings and broad automatic capacity purchasing are vision, not
  live capability. Early testing currently uses Zolli credits; cash payout is
  not live.
- More competition is designed to lower prices; never promise every job is
  cheaper.
- Proven, Preview, and Network expansion support must remain explicitly
  distinct.
- Do not claim tightly synchronized training across distant machines as a
  supported workload.
- Keep Zolli/Zolli Cloud as the visible product and FlashML as the open runtime
  and wire protocol underneath.
- Follow strict TDD for behavior changes: add a focused failing test, observe
  the expected failure, implement the smallest change, then rerun the focused
  test before the wider landing suite.
- Preserve unrelated work. Every commit stages only the files named by its
  task.

---

## File structure

**Create**

- `flashml-cloud/apps/web/components/landing/HeroMarketSwitch.tsx` — the two
  hero roles and their accessible static markup.
- `flashml-cloud/apps/web/components/landing/MarketStory.tsx` — problem, open
  allocation model, and buyer/provider value.
- `flashml-cloud/apps/web/components/landing/SimpleJourney.tsx` — the three
  plain-language steps before technical detail.

**Modify**

- `flashml-cloud/apps/web/app/(marketing)/page.tsx` — approved section order.
- `flashml-cloud/apps/web/app/globals.css` — only the isolated hero text
  transition and reduced-motion fallback.
- `flashml-cloud/apps/web/components/landing/Hero.tsx` — vision copy, role
  switch, and demand/supply CTAs; coordinator map invocation unchanged.
- `flashml-cloud/apps/web/components/landing/EvidenceBand.tsx` — verified
  cross-machine outcomes in plain language.
- `flashml-cloud/apps/web/components/landing/PlatformSupport.tsx` — practical
  proven/preview/expansion context.
- `flashml-cloud/apps/web/components/landing/SystemJourney.tsx` — technical
  detail framing and non-primary section id.
- `flashml-cloud/apps/web/components/landing/WorkloadFit.tsx` — plain fit rule.
- `flashml-cloud/apps/web/components/landing/WorkloadRows.tsx` — machine
  context for every workload.
- `flashml-cloud/apps/web/components/landing/SystemModules.tsx` — trust-first
  technical introduction.
- `flashml-cloud/apps/web/components/landing/RecoveryDemo.tsx` — reliability as
  the market enabler.
- `flashml-cloud/apps/web/components/landing/ProfessionalServices.tsx` — help
  applying an existing fleet/workload.
- `flashml-cloud/apps/web/components/landing/Faq.tsx` — market-stage and fit
  objections.
- `flashml-cloud/apps/web/components/landing/ClosingCta.tsx` — two-sided
  closing choice.
- `flashml-cloud/apps/web/lib/landing/platform.ts` — four qualified host lanes
  and expansion statements.
- `flashml-cloud/apps/web/lib/landing/workloads.ts` — five plain-language
  workload records with machine context.
- `flashml-cloud/apps/web/lib/marketing.ts` — canonical machine-enrolment path.
- Existing landing Vitest files — replace superseded mechanism-first
  assertions with user-visible narrative behavior; retain map behavior tests.
- `PROGRESS.md` — verified completion entry after the full gate.

---

### Task 1: Build the vision-led hero without touching the SVG

**Files:**

- Create: `flashml-cloud/apps/web/components/landing/HeroMarketSwitch.tsx`
- Modify: `flashml-cloud/apps/web/components/landing/Hero.tsx:1-150`
- Modify: `flashml-cloud/apps/web/app/globals.css`
- Modify: `flashml-cloud/apps/web/lib/marketing.ts:1-6`
- Test: `flashml-cloud/apps/web/lib/landing-infrastructure-story.test.ts`
- Test: `flashml-cloud/apps/web/lib/landing-cinematic.test.ts`
- Test: `flashml-cloud/apps/web/lib/landing-expansion.test.ts`

**Interfaces:**

- Produces: `HeroMarketSwitch(): JSX.Element` with both roles always present in
  the DOM, `data-market-role="demand|supply"`, and no live region.
- Produces: `MARKETING.machinesPath === "/account/machines"` for later CTA use.
- Preserves: the existing `CoordinatorMap` call, its `phase`, and its viewport
  selection exactly.

- [ ] **Step 1: Add failing hero behavior tests**

Replace the superseded hero-definition assertion with server-rendered
behavior assertions:

```ts
const markup = renderToStaticMarkup(createElement(Hero));
const text = markup.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

expect(text).toContain("Computing power, without the lock-in.");
expect(text).toContain("Need computing power?");
expect(text).toContain("Access more compute at a competitive price.");
expect(text).toContain("Have unused computing power?");
expect(text).toContain("Host it and earn from the work it completes.");
expect(markup).toContain('data-market-role="demand"');
expect(markup).toContain('data-market-role="supply"');
expect(markup).not.toContain("aria-live");
expect(markup).toContain('href="/workspaces"');
expect(markup).toContain('href="/account/machines"');
expect(markup.indexOf("Get early access")).toBeLessThan(
  markup.indexOf("Provide compute"),
);
```

Update the cinematic hero assertion to require the stable H1, both roles, the
existing map nodes/readout, and server-visible content without an inline
`opacity:0` style.

- [ ] **Step 2: Run the focused tests and verify the expected failure**

Run from `flashml-cloud/apps/web`:

```bash
npm test -- lib/landing-infrastructure-story.test.ts lib/landing-cinematic.test.ts
```

Expected: FAIL because the current hero still says "Compute that finishes the
job," has no market-role elements, and has no machine-enrolment CTA.

- [ ] **Step 3: Add the canonical machine path**

Extend `MARKETING` without changing existing values:

```ts
export const MARKETING = {
  consolePath: "/workspaces",
  machinesPath: "/account/machines",
  calendlyUrl: "https://calendly.com/phongct1105/zolli-ai",
  contactEmail: "phongct1105@gmail.com",
  runtimeRepo: "https://github.com/Zolli-Labs/flashml",
} as const;
```

Update the one exact-object test for `MARKETING` to include `machinesPath`.

- [ ] **Step 4: Create the isolated role switch**

Create a presentational component containing this exact semantic structure:

```tsx
export function HeroMarketSwitch() {
  return (
    <div className="hero-market-switch" aria-label="Two ways to join Zolli">
      <div className="hero-market-role" data-market-role="demand">
        <p>Need computing power?</p>
        <p>Access more compute at a competitive price.</p>
      </div>
      <div className="hero-market-role" data-market-role="supply">
        <p>Have unused computing power?</p>
        <p>Host it and earn from the work it completes.</p>
      </div>
    </div>
  );
}
```

Use existing font, foreground, and muted-foreground utility classes inside the
component. Do not add state, timers, `aria-live`, or any dependency on
`useMapStory`.

- [ ] **Step 5: Add only the role-switch motion CSS**

Add isolated `.hero-market-switch` / `.hero-market-role` rules and two
keyframes. At the start of a 10-second loop, demand is visible and supply is
translated below; at the midpoint, demand is translated above and supply is
visible. Only `opacity` and vertical `transform` values may animate. Reserve
the role area with grid overlap so its height does not change.

```css
.hero-market-switch {
  display: grid;
  min-height: 5.25rem;
  overflow: hidden;
}

.hero-market-role {
  grid-area: 1 / 1;
  animation: hero-market-demand 10s ease-in-out infinite;
  will-change: opacity, transform;
}

.hero-market-role[data-market-role="supply"] {
  animation-name: hero-market-supply;
}

@keyframes hero-market-demand {
  0%, 40% { opacity: 1; transform: translateY(0); }
  48%, 92% { opacity: 0; transform: translateY(-1rem); }
  92.01% { opacity: 0; transform: translateY(1rem); }
  100% { opacity: 1; transform: translateY(0); }
}

@keyframes hero-market-supply {
  0%, 40% { opacity: 0; transform: translateY(1rem); }
  48%, 92% { opacity: 1; transform: translateY(0); }
  100% { opacity: 0; transform: translateY(-1rem); }
}
```

Inside the existing `@media (prefers-reduced-motion: reduce)` block, disable
the role animations, change the switch to normal block flow, and render both
roles at `opacity: 1` and `transform: none`.

```css
.hero-market-switch {
  display: block;
  min-height: 0;
  overflow: visible;
}

.hero-market-role {
  animation: none;
  opacity: 1;
  transform: none;
}

.hero-market-role + .hero-market-role {
  margin-top: 1.25rem;
}
```

- [ ] **Step 6: Replace only the hero copy and actions**

In `Hero.tsx`:

- eyebrow: `The open compute network`;
- H1: `Computing power,` / `without the lock-in.`;
- explanation: `Zolli is building a network that connects people who need
  compute with machines ready to work—across personal hardware, community
  hosts, and cloud providers.`;
- insert `<HeroMarketSwitch />` after the explanation;
- primary action: `Get early access` → `MARKETING.consolePath`;
- secondary action: `Provide compute` → `MARKETING.machinesPath`.

Leave the full map wrapper and existing `CoordinatorMap` invocation unchanged.

- [ ] **Step 7: Run the focused and map behavior suites**

```bash
npm test -- lib/landing-infrastructure-story.test.ts lib/landing-cinematic.test.ts lib/hero-story.test.ts
```

Expected: PASS; all existing coordinator-map phase, scroll, reduced-motion,
focus, cleanup, geometry, and readout assertions remain green.

- [ ] **Step 8: Commit Task 1**

```bash
git add flashml-cloud/apps/web/components/landing/HeroMarketSwitch.tsx \
  flashml-cloud/apps/web/components/landing/Hero.tsx \
  flashml-cloud/apps/web/app/globals.css \
  flashml-cloud/apps/web/lib/marketing.ts \
  flashml-cloud/apps/web/lib/landing-infrastructure-story.test.ts \
  flashml-cloud/apps/web/lib/landing-cinematic.test.ts \
  flashml-cloud/apps/web/lib/landing-expansion.test.ts
git commit -m "feat(web): lead the landing with the open compute network"
```

---

### Task 2: Put market context and a three-step explanation before mechanics

**Files:**

- Create: `flashml-cloud/apps/web/components/landing/MarketStory.tsx`
- Create: `flashml-cloud/apps/web/components/landing/SimpleJourney.tsx`
- Modify: `flashml-cloud/apps/web/app/(marketing)/page.tsx:1-60`
- Modify: `flashml-cloud/apps/web/components/landing/SystemJourney.tsx:60-80`
- Test: `flashml-cloud/apps/web/lib/landing-expansion.test.ts`
- Test: `flashml-cloud/apps/web/lib/landing-infrastructure-story.test.ts`

**Interfaces:**

- Produces: `<section id="network" data-surface="light">` containing problem,
  allocation model, demand value, supply value, and the early-network payout
  disclosure.
- Produces: `<section id="how-it-works" data-surface="dark">` containing
  exactly three `data-human-step` items.
- Changes: detailed `SystemJourney` id to `technical-workflow`; it retains all
  seven existing workflow scenes and protocol events.

- [ ] **Step 1: Write failing page-story tests**

Change the landing order assertion to require:

```ts
const anchors = [
  'id="network"',
  'id="how-it-works"',
  'id="recover"',
  'id="evidence"',
  'id="workloads"',
  'id="platform"',
  'id="technical-workflow"',
  'id="architecture"',
  'id="services"',
  'id="faq"',
  'id="start"',
];
```

Add rendered behavior assertions that the network section explains all three
ideas—`Compute is everywhere. Access is not.`, `From isolated machines to an
open compute network.`, and both buyer/provider paths—and contains the exact
disclosure `Early testing uses Zolli credits. Cash payout is not live.`

Assert the primary `how-it-works` section has exactly three human steps with
these visible titles in order:

```ts
[
  "Tell Zolli what you need.",
  "The network finds suitable machines.",
  "Your work continues as capacity changes.",
]
```

Assert the seven `data-workflow-step` elements still exist under
`technical-workflow`, after `platform`.

- [ ] **Step 2: Run the focused tests and verify the expected failure**

```bash
npm test -- lib/landing-expansion.test.ts lib/landing-infrastructure-story.test.ts
```

Expected: FAIL because `network`, the three human steps, and
`technical-workflow` do not yet exist.

- [ ] **Step 3: Create `MarketStory` using existing surfaces**

Build one light section with three editorial rows:

1. **Problem:** `Compute is everywhere. Access is not.` Explain that laptops,
   lab machines, rented GPUs, and cloud accounts behave like separate islands
   while useful machines elsewhere sit idle.
2. **New model:** `From isolated machines to an open compute network.` Explain
   that Zolli is building one allocation path across personally owned, team,
   community, and rented capacity, guided by price, completion time, and
   hardware.
3. **Two-sided value:** adjacent demand/supply columns using:
   - `Access more machines, compare more choices, and avoid depending on one
     provider's price or availability.`
   - `Turn unused machines into productive capacity and earn when they
     complete useful work.`
   - visible label `Early network` and disclosure `Early testing uses Zolli
     credits. Cash payout is not live.`

Reuse the current `max-w-[1240px]`, section padding, border, typography, and
light/sand token classes. Add no color literals or new design tokens.

- [ ] **Step 4: Create `SimpleJourney`**

Render a dark section with eyebrow `How Zolli works`, headline `From a compute
need to finished work.`, and exactly three ordered items:

```ts
const STEPS = [
  {
    title: "Tell Zolli what you need.",
    body: "Describe the work, required hardware, and whether price or finish time matters more.",
  },
  {
    title: "The network finds suitable machines.",
    body: "Zolli can consider owned, community, and rented capacity that fits the work.",
  },
  {
    title: "Your work continues as capacity changes.",
    body: "Progress can be recorded so supported work can continue on another compatible machine.",
  },
] as const;
```

Use `data-human-step={index + 1}` and an ordered list. Do not add animation or
reuse the seven-step `WorkflowScene` here.

- [ ] **Step 5: Reorder the page and demote the technical journey**

Set the page component order to:

```tsx
<Hero />
<MarketStory />
<SimpleJourney />
<RecoveryDemo />
<EvidenceBand />
<WorkloadFit />
<PlatformSupport />
<SystemJourney />
<SystemModules />
<ProfessionalServices />
<Faq />
<ClosingCta />
```

Keep the existing hero scroll track and all surface wrapper classes. Change
`SystemJourney` to `id="technical-workflow"`, eyebrow `Technical workflow`,
headline `See how one job moves through the runtime.`, and introductory body
`For technical evaluators, seven scenes show how capacity joins, work moves,
progress survives, and one result is accepted.` Leave its seven-step data,
pinning, and `WorkflowScene` behavior unchanged.

- [ ] **Step 6: Run the focused landing suites**

```bash
npm test -- lib/landing-expansion.test.ts lib/landing-infrastructure-story.test.ts lib/landing-cinematic.test.ts
```

Expected: PASS with the new story order and all seven technical workflow
scenes intact.

- [ ] **Step 7: Commit Task 2**

```bash
git add 'flashml-cloud/apps/web/app/(marketing)/page.tsx' \
  flashml-cloud/apps/web/components/landing/MarketStory.tsx \
  flashml-cloud/apps/web/components/landing/SimpleJourney.tsx \
  flashml-cloud/apps/web/components/landing/SystemJourney.tsx \
  flashml-cloud/apps/web/lib/landing-expansion.test.ts \
  flashml-cloud/apps/web/lib/landing-infrastructure-story.test.ts \
  flashml-cloud/apps/web/lib/landing-cinematic.test.ts
git commit -m "feat(web): explain the Zolli compute market before its mechanics"
```

---

### Task 3: Pair every workload with supported machine context

**Files:**

- Modify: `flashml-cloud/apps/web/lib/landing/workloads.ts:1-26`
- Modify: `flashml-cloud/apps/web/lib/landing/platform.ts:1-153`
- Modify: `flashml-cloud/apps/web/components/landing/WorkloadFit.tsx:1-29`
- Modify: `flashml-cloud/apps/web/components/landing/WorkloadRows.tsx:1-53`
- Modify: `flashml-cloud/apps/web/components/landing/PlatformSupport.tsx:1-97`
- Test: `flashml-cloud/apps/web/lib/landing-expansion.test.ts`
- Test: `flashml-cloud/apps/web/lib/landing-infrastructure-story.test.ts`

**Interfaces:**

- Changes: every `WORKLOADS` record gains `machineContext: string`.
- Changes: `HOST_SUPPORT` has four qualified cards: three Proven and one
  Preview.
- Produces: visible Proven today, Preview, Network expansion, and honest
  exclusion groups in `#platform`.
- Preserves: `inferPlatformFamily`, `MACHINE_HINTS`, browser-only detection,
  and `flashnode doctor` guidance.

- [ ] **Step 1: Write failing fit and compatibility tests**

Assert `WORKLOADS` has five records and every record has non-empty
`machineContext`. Render `WorkloadRows` and require one
`data-workload-machines` element per record.

Require these five user-facing titles:

```ts
[
  "Model configuration search",
  "AI model evaluation",
  "Independent file processing",
  "Simulations and research trials",
  "Checkpointable model training",
]
```

Render `PlatformSupport` and assert:

- visible groups `Proven today`, `Preview`, and `Network expansion`;
- proven labels `macOS Apple silicon`, `Linux x86_64`, and `RunPod NVIDIA
  GPUs`;
- preview label `Windows 11`;
- expansion copy `More cloud providers`, `More GPU and hardware
  configurations`, `Automatic capacity purchasing`, and `Cash earnings for
  machine hosts`;
- the honest exclusion includes `not currently designed for tightly
  synchronized training`;
- the machine check still says the browser cannot verify architecture,
  Docker, or GPU availability and still points to `flashnode doctor` after a
  click.

- [ ] **Step 2: Run focused tests and verify the expected failure**

```bash
npm test -- lib/landing-expansion.test.ts lib/landing-infrastructure-story.test.ts
```

Expected: FAIL because the current four workload records lack machine context,
RunPod is not a qualified host card, and expansion/exclusion groups do not
exist.

- [ ] **Step 3: Replace the workload records**

Use these exact title/body/machine-context relationships while preserving the
existing number/layout fields:

1. **Model configuration search** — run many model settings independently;
   `Laptops, CPU workstations, or rented GPUs.`
2. **AI model evaluation** — test prompts, datasets, or model versions as
   separate tasks; `CPU or GPU machines across a team.`
3. **Independent file processing** — embeddings, OCR, transcription,
   conversion, or data preparation; `Supported macOS, Linux, and compatible
   cloud machines.`
4. **Simulations and research trials** — Monte Carlo experiments and
   independent rollouts; `Mixed personal, lab, and cloud machines.`
5. **Checkpointable model training** — save progress and continue after an
   interruption; `Linux machines with supported NVIDIA GPUs.`

- [ ] **Step 4: Render machine context beside every workload**

Change the section headline to `Can your work run on Zolli?` and add the fit
rule `Zolli works best when a job can be divided into separate pieces or can
save its progress while running.`

In every workload row, render:

```tsx
<p data-workload-machines className="text-sm leading-relaxed text-muted-foreground">
  Suitable machines: {workload.machineContext}
</p>
```

Keep the decorative velocity rail and existing row layout/motion.

- [ ] **Step 5: Qualify host support data**

Set the host cards to:

```ts
[
  { platform: "macOS Apple silicon", state: "Proven", body: "Verified on macOS arm64 machines." },
  { platform: "Linux x86_64", state: "Proven", body: "Verified on Linux CPU and compatible GPU hosts." },
  { platform: "RunPod NVIDIA GPUs", state: "Proven", body: "Verified with RTX 3090, RTX 4090, and RTX 4000 Ada machines." },
  { platform: "Windows 11", state: "Preview", body: "Preview through Docker Desktop and WSL2." },
] as const;
```

Do not change runtime labels, curated-image aliases, platform inference, or
machine hints.

- [ ] **Step 6: Reframe platform support as an applicability answer**

Use eyebrow `Workload and machine fit`, headline `Bring the machines you
already use.`, and plain introduction `See what is proven today, what is in
preview, and where the network is expanding.`

Keep host state badges. Add a clearly separate `Network expansion` list with
the four exact items from Step 1. Add this exclusion after the groups:

> Zolli is best for work that can be divided or resumed. It is not currently
> designed for tightly synchronized training where every GPU must communicate
> continuously over a very fast network.

Keep the runtime explorer and machine compatibility check below this context.

- [ ] **Step 7: Run focused landing tests**

```bash
npm test -- lib/landing-expansion.test.ts lib/landing-infrastructure-story.test.ts lib/landing-cinematic.test.ts
```

Expected: PASS; browser detection remains click-triggered and qualified.

- [ ] **Step 8: Commit Task 3**

```bash
git add flashml-cloud/apps/web/lib/landing/workloads.ts \
  flashml-cloud/apps/web/lib/landing/platform.ts \
  flashml-cloud/apps/web/components/landing/WorkloadFit.tsx \
  flashml-cloud/apps/web/components/landing/WorkloadRows.tsx \
  flashml-cloud/apps/web/components/landing/PlatformSupport.tsx \
  flashml-cloud/apps/web/lib/landing-expansion.test.ts \
  flashml-cloud/apps/web/lib/landing-infrastructure-story.test.ts \
  flashml-cloud/apps/web/lib/landing-cinematic.test.ts
git commit -m "feat(web): show which workloads and machines fit Zolli"
```

---

### Task 4: Translate proof, boundaries, and conversion into the market story

**Files:**

- Modify: `flashml-cloud/apps/web/components/landing/EvidenceBand.tsx:1-58`
- Modify: `flashml-cloud/apps/web/components/landing/RecoveryDemo.tsx:1-69`
- Modify: `flashml-cloud/apps/web/components/landing/SystemModules.tsx:31-51`
- Modify: `flashml-cloud/apps/web/components/landing/ProfessionalServices.tsx:34-63`
- Modify: `flashml-cloud/apps/web/components/landing/Faq.tsx:1-102`
- Modify: `flashml-cloud/apps/web/components/landing/ClosingCta.tsx:1-205`
- Test: `flashml-cloud/apps/web/lib/landing-expansion.test.ts`
- Test: `flashml-cloud/apps/web/lib/landing-rebrand.test.ts`
- Test: `flashml-cloud/apps/web/lib/landing-cinematic.test.ts`

**Interfaces:**

- Produces: four outcome-level evidence items with values `6`, `3`, `58`, and
  `1`.
- Produces: eight FAQ disclosures covering category, provider distinction,
  earnings status, price uncertainty, machines, workload fit, recovery, and
  maturity.
- Consumes: `MARKETING.consolePath` and `MARKETING.machinesPath` for the final
  demand/supply choice.
- Preserves: sample-data disclosure in the recovery event ledger and the
  footer statement that FlashML remains the runtime and wire protocol.

- [ ] **Step 1: Write failing proof and conversion tests**

Update the evidence behavior test to require values `6`, `3`, `58`, `1` and
these outcomes:

- six trials completed in one model search;
- a laptop and two rented GPUs shared the work;
- 58 epochs survived a destroyed machine;
- one accepted result per task.

Require the reliability heading `Affordable capacity matters only if the work
finishes.` and recovery facts `RTX 4090`, `RTX 3090`, and `58 epochs
preserved`, while retaining `sample data` beside the displayed event values.

Render the FAQ and require eight `<details>` disclosures whose questions are:

```ts
[
  "What is Zolli?",
  "Is Zolli another cloud provider?",
  "Can machine owners earn money today?",
  "Will Zolli always be cheaper?",
  "Which machines work?",
  "Which workloads fit?",
  "What happens if a machine disappears?",
  "How mature is the network?",
]
```

Assert the FAQ says cash payout is not live, never guarantees a lower price,
and names tightly synchronized training as outside the current target.

Require the closing section to contain `Join the open compute network.`, `I
need compute` → `/workspaces`, and `I want to provide compute` →
`/account/machines`, with the demand action first.

- [ ] **Step 2: Run focused tests and verify the expected failure**

```bash
npm test -- lib/landing-expansion.test.ts lib/landing-rebrand.test.ts lib/landing-cinematic.test.ts
```

Expected: FAIL on the old evidence values, mechanism-first recovery heading,
seven old FAQ questions, and old closing actions.

- [ ] **Step 3: Replace the evidence band with verified outcomes**

Set the eyebrow to `Product evidence`, headline to `A growing network, proven
with real work.`, and supporting sentence to explain that the figures are
documented runs rather than scale claims.

Use:

```ts
const EVIDENCE = [
  ["6", "trials completed", "One model search completed all six independent trials."],
  ["3", "machines shared the work", "A laptop and two rented GPUs completed the same search."],
  ["58", "epochs preserved", "Completed training progress survived when a rented GPU was destroyed."],
  ["1", "accepted result per task", "Duplicate outcomes are rejected instead of counted twice."],
] as const;
```

- [ ] **Step 4: Reframe the recovery proof**

Use headline `Affordable capacity matters only if the work finishes.` and
explain that lower-cost distributed machines may disappear, so Zolli records
progress and lets another compatible machine continue supported work.

Replace the proof stack with:

```ts
[
  "RTX 4090 machine destroyed",
  "Resumed on an RTX 3090",
  "58 epochs preserved",
] as const;
```

Keep the event ledger and its explicit sample-data qualification unchanged.

- [ ] **Step 5: Reframe technical and service introductions**

In `SystemModules`, change only the introduction to answer trust before naming
modules: eyebrow `Technical depth`, headline `The machinery behind a reliable
compute market.`, body `For technical evaluators, Zolli separates allocation,
execution, and integrity so changing capacity can still produce one accepted
outcome.` Keep the architecture signal, three layers, module labels, and event
names.

In `ProfessionalServices`, use headline `Start with the machines and workloads
you already have.` and explain that Zolli can help determine fit, connect a
fleet, and adapt a divisible or checkpointable workload. Keep the existing
service rows, contact paths, and visual structure.

- [ ] **Step 6: Replace the FAQ with the eight approved objections**

Use the questions from Step 1 and answers that state:

- Zolli is an allocation network across owned, community, and rented compute;
- it is not another single cloud provider;
- early testing uses Zolli credits and cash payout is not live;
- competition creates choices but cannot guarantee every job is cheaper;
- proven hosts are macOS Apple silicon, Linux x86_64, and tested RunPod NVIDIA
  GPUs, with Windows 11 in Preview;
- divisible or checkpointable work fits, while tightly synchronized
  multi-machine training is not the current target;
- supported work can be reallocated and resumed from recorded progress; and
- Zolli is early, with verified cross-machine runs and a growing network.

- [ ] **Step 7: Replace the closing choice**

Use eyebrow `Zolli Cloud`, headline `Join the open compute network.`, and
supporting copy `Access compute from more sources, or make a machine available
to the network. Zolli is currently in early access.`

Render:

- primary `I need compute` → `MARKETING.consolePath`;
- secondary `I want to provide compute` → `MARKETING.machinesPath`;
- a small disclosure near the provider action: `Host cash payout is not live;
  early testing uses Zolli credits.`

Keep `CommitSignal`, the full footer, legal links, runtime link, and early
product identity statement.

- [ ] **Step 8: Run every landing-specific test**

```bash
npm test -- lib/landing-expansion.test.ts \
  lib/landing-infrastructure-story.test.ts \
  lib/landing-cinematic.test.ts \
  lib/landing-rebrand.test.ts \
  lib/hero-story.test.ts \
  lib/landing-fabric-clarity.test.ts
```

Expected: PASS. Update superseded exact-copy/order assertions; do not delete
coordinator-map behavior, accessibility, qualification, or overclaim guards.

- [ ] **Step 9: Commit Task 4**

```bash
git add flashml-cloud/apps/web/components/landing/EvidenceBand.tsx \
  flashml-cloud/apps/web/components/landing/RecoveryDemo.tsx \
  flashml-cloud/apps/web/components/landing/SystemModules.tsx \
  flashml-cloud/apps/web/components/landing/ProfessionalServices.tsx \
  flashml-cloud/apps/web/components/landing/Faq.tsx \
  flashml-cloud/apps/web/components/landing/ClosingCta.tsx \
  flashml-cloud/apps/web/lib/landing-expansion.test.ts \
  flashml-cloud/apps/web/lib/landing-rebrand.test.ts \
  flashml-cloud/apps/web/lib/landing-cinematic.test.ts
git commit -m "feat(web): connect Zolli proof and conversion to the market vision"
```

---

### Task 5: Run the full gate and record verified completion

**Files:**

- Modify: `PROGRESS.md`

**Interfaces:**

- Consumes: completed Tasks 1–4.
- Produces: one current progress-log entry with exact test/build evidence.

- [ ] **Step 1: Confirm the frozen SVG boundary**

Run from the repository root. Resolve the exact branch base into a
task-specific variable first:

```bash
ZOLLI_LANDING_BASE="$(git merge-base develop HEAD)"
git diff --name-only "$ZOLLI_LANDING_BASE"..HEAD -- \
  flashml-cloud/apps/web/components/landing/coordinator-map \
  flashml-cloud/apps/web/lib/coordinator-map.ts
```

Expected: no output.

- [ ] **Step 2: Run the complete web test suite**

From `flashml-cloud/apps/web`:

```bash
npm test
```

Expected: exit 0 with the exact passing file/test count recorded from output.

- [ ] **Step 3: Run TypeScript and lint**

```bash
npx tsc --noEmit --incremental false
npm run lint
```

Expected: both exit 0.

- [ ] **Step 4: Regenerate route types and run the production build**

Source the canonical development environment from the original checkout, then
run:

```bash
set -a
source /Users/phongcao/Work/Zolli-Labs/flashml-cloud/.env.dev
set +a
npx next typegen
npm run build
```

Expected: type generation and production build exit 0.

- [ ] **Step 5: Run diff validation**

```bash
ZOLLI_LANDING_BASE="$(git merge-base develop HEAD)"
git diff --check "$ZOLLI_LANDING_BASE"..HEAD
```

Expected: diff check prints no errors.

- [ ] **Step 6: Inspect responsive behavior**

Run the web app with the canonical environment and inspect desktop and mobile:

- stable hero H1 does not move;
- demand copy appears before supply copy;
- reduced motion shows both roles without overlap;
- no hero or section layout shift;
- all new copy remains readable;
- the coordinator map looks and behaves exactly as before; and
- both closing actions reach the intended routes.

Record the URL and viewport sizes used in the report. If browser inspection is
unavailable, report that explicitly rather than claiming visual verification.

- [ ] **Step 7: Add the progress entry**

Prepend one `PROGRESS.md` entry using the repository protocol. State what
changed, the exact test counts and commands, the frozen-SVG result, visual
inspection evidence or limitation, the current-versus-vision disclosure, and
the next useful action.

- [ ] **Step 8: Commit Task 5**

```bash
git add PROGRESS.md
git commit -m "docs: record the vision-led landing verification"
```
