# Zolli Abstract Infrastructure Landing Implementation Plan

> **Execution note:** Use `superpowers:subagent-driven-development` for task-by-task execution in this session, or `superpowers:executing-plans` in a separate session. Do not begin implementation until the user chooses an execution mode.

**Goal:** Rebuild the Zolli landing-page story around an attention-catching, selectable 3D infrastructure stack, then explain the product through a simpler seven-scene workflow, credible platform support, and purposeful motion.

**Architecture:** Keep the current ten-section page and alternating surface rhythm. Model infrastructure, platform, and workflow content as typed data in `apps/web/lib/landing`; render it through narrowly owned interactive components. GSAP owns scroll timelines and pinning, Motion owns local state transitions and in-view reveals, and CSS owns materials, perspective, and ambient motion. Every enhanced visual has a readable static/reduced-motion path.

**Tech stack:** Next.js App Router, React, TypeScript, GSAP + ScrollTrigger, `@gsap/react`, Motion for React, CSS 3D transforms, inline SVG paths, `simple-icons`, Vitest, Testing Library, ESLint.

## Global constraints

- Work only in `/Users/phongcao/Work/Zolli-Labs/flashml-cloud-zolli-rebrand/flashml-cloud`.
- Preserve unrelated changes in the existing dirty worktree.
- Do not add dependencies, WebGL, canvas, Three.js, video, smooth scrolling, or unverified provider integrations.
- Keep this exact surface order: dark hero, light evidence, sand platform, dark workflow, light workloads, dark architecture, light recovery, sand services, light FAQ, orange CTA, dark footer.
- Use only verified evidence `30 / 2 / 5 / 1`; do not invent percentages, scale, customer, performance, or provider claims.
- The primary CTA remains **Open console**. **Talk to Zolli** opens `https://calendly.com/phongct1105/zolli-ai`; contact uses `phongct1105@gmail.com`.
- Core copy must remain readable while JavaScript loads. Only decoration may animate from hidden states.
- Respect `prefers-reduced-motion`, coarse pointers, document visibility, and offscreen state.
- Verify at 1440×900, 1024×768, 768×1024, 390×844, and 375×812. No one-word heading orphan at those widths.
- Preview-first: do not commit, push, merge, or deploy before explicit user approval of the local result.
- For each behavior change: add a failing focused test, observe the intended failure, make the smallest implementation, and rerun the test.

## Product story

Zolli is the fault-tolerant control plane for fragmented compute. It unifies compatible cloud/HPC capacity, rented CPU/GPU, owned GPU infrastructure, and everyday machines; leases work across them; checkpoints accepted progress; and recovers work when a node disappears.

The page must tell that story in this order:

1. **Promise:** compute that finishes the job.
2. **Unification:** four kinds of compute become one recoverable fleet.
3. **Proof:** current verified evidence without inflated claims.
4. **Compatibility:** runtimes and host states, plus a careful local machine hint.
5. **Mechanism:** connect → register → submit → parallel → checkpoint → recover → accept.
6. **Fit:** workloads this mechanism enables.
7. **Trust:** architecture, recovery, services, FAQ, legal, and contact paths.

## File and interface map

### Typed content and capability logic

- Create `apps/web/lib/landing/platform.ts` for infrastructure layers, runtime support, host support, platform-family inference, and machine-check copy.
- Modify `apps/web/lib/landing/workflow.ts` so the canonical workflow is exactly seven steps and five protocol events.
- Extend `apps/web/components/landing/LandingMotionProvider.tsx` so every animation consumes one capability state.

### Hero

- Create `apps/web/components/landing/HeroInfrastructureStack.tsx` for the selectable 3D stack and stable detail panel.
- Modify `apps/web/components/landing/Hero.tsx` to use the stack and direct product definition.
- Delete `apps/web/components/landing/HeroSystemStage.tsx` only after the replacement passes tests and has no imports.

### Platform support

- Create `apps/web/components/landing/RuntimeSupportExplorer.tsx` for nine labeled runtime controls.
- Create `apps/web/components/landing/MachineCompatibilityCheck.tsx` for the button-triggered browser-family hint.
- Refactor `apps/web/components/landing/PlatformSupport.tsx` around runtime support, host states, and machine check.

### Workflow and later-page motion

- Create `apps/web/components/landing/WorkflowScene.tsx` for seven low-density diagrams.
- Refactor `apps/web/components/landing/SystemJourney.tsx` to coordinate copy, scenes, and desktop scroll state.
- Create `apps/web/components/landing/WorkloadRows.tsx` and compose it in `WorkloadFit.tsx`.
- Create `apps/web/components/landing/ArchitectureSignal.tsx` and compose it in `SystemModules.tsx`.
- Modify `apps/web/components/landing/ProfessionalServices.tsx` for readable heading scale and section reveal.
- Modify `apps/web/app/globals.css` for stack materials, workflow scenes, responsive layouts, and reduced-motion states.

### Tests and evidence

- Create `apps/web/lib/landing-infrastructure-story.test.ts` for new data, behavior, and source contracts.
- Modify `apps/web/lib/landing-cinematic.test.ts`, `landing-expansion.test.ts`, and `landing-rebrand.test.ts` where old assumptions change.
- Write browser findings to `.superpowers/sdd/2026-08-09-zolli-abstract-infrastructure-landing/task-8-browser-qa.md`.

## Task 1: Lock content contracts and capture a baseline

**Files:**

- Create: `apps/web/lib/landing/platform.ts`
- Modify: `apps/web/lib/landing/workflow.ts`
- Create: `apps/web/lib/landing-infrastructure-story.test.ts`
- Verify evidence in: `apps/api/flashml_cloud_api/images.py`

### Step 1: Record the current baseline

From `apps/web`, run:

```bash
npx vitest run lib/landing-rebrand.test.ts lib/landing-expansion.test.ts lib/landing-cinematic.test.ts
npm run build
```

Save the `/` route-size line from the build in the Task 8 QA report. Do not repair unrelated pre-existing failures; record their exact command and output.

### Step 2: Write failing infrastructure and platform tests

Create `landing-infrastructure-story.test.ts` with this contract:

```ts
import { describe, expect, it } from "vitest";
import {
  HERO_LAYER_ORDER,
  HERO_SELECTION_ORDER,
  HOST_SUPPORT,
  MACHINE_HINTS,
  RUNTIME_SUPPORT,
  inferPlatformFamily,
} from "./landing/platform";
import { WORKFLOW_EVENTS, WORKFLOW_STEPS } from "./landing/workflow";

describe("the infrastructure story", () => {
  it("orders the stack from foundational to personal compute", () => {
    expect(HERO_LAYER_ORDER).toEqual(["external", "rented", "owned", "everyday"]);
    expect(HERO_SELECTION_ORDER).toEqual([
      "unified", "everyday", "owned", "rented", "external",
    ]);
  });

  it("uses curated runtime labels and registered images", () => {
    expect(RUNTIME_SUPPORT.map(({ label }) => label)).toEqual([
      "Python 3.11", "NumPy", "pandas", "scikit-learn", "SciPy",
      "PyTorch CPU", "PyTorch CUDA 12.4", "Docker", "GitHub",
    ]);
    expect(RUNTIME_SUPPORT.flatMap(({ imageAlias }) => imageAlias ?? [])).toEqual([
      "python-slim", "sklearn", "pytorch-cpu", "pytorch-cuda",
    ]);
  });

  it("qualifies every host state", () => {
    expect(HOST_SUPPORT.map(({ platform, state }) => [platform, state])).toEqual([
      ["macOS arm64", "Proven"],
      ["Linux x86_64", "Proven"],
      ["Windows 11", "Preview"],
    ]);
  });

  it("maps browser signals without claiming hardware verification", () => {
    expect(inferPlatformFamily({ userAgent: "iPhone", platform: "MacIntel", maxTouchPoints: 5 })).toBe("mobile");
    expect(inferPlatformFamily({ userAgent: "Macintosh", platform: "MacIntel", maxTouchPoints: 0 })).toBe("macos");
    expect(inferPlatformFamily({ userAgent: "X11; Linux", platform: "Linux x86_64", maxTouchPoints: 0 })).toBe("linux");
    expect(inferPlatformFamily({ userAgent: "Windows NT 10.0", platform: "Win32", maxTouchPoints: 0 })).toBe("windows");
    expect(Object.values(MACHINE_HINTS).every(({ body, nextStep }) =>
      !body.includes("Your machine is supported") &&
      nextStep === "Run flashnode doctor for a real host check."
    )).toBe(true);
  });

  it("uses seven scenes and five recovery events", () => {
    expect(WORKFLOW_STEPS.map(({ id }) => id)).toEqual([
      "connect", "register", "submit", "parallel", "checkpoint", "recover", "accept",
    ]);
    expect(WORKFLOW_EVENTS).toEqual([
      "LEASE_CLAIMED",
      "CHECKPOINT_MANIFEST_COMMITTED",
      "NODE_HEARTBEAT_LOST",
      "TASK_REQUEUED",
      "TASK_COMMIT_ACCEPTED",
    ]);
  });
});
```

Run `npx vitest run lib/landing-infrastructure-story.test.ts`.

Expected: fail because the platform module and seven-step workflow do not exist.

### Step 3: Implement typed data

Create these public types and orders:

```ts
export type InfrastructureLayerKey = "external" | "rented" | "owned" | "everyday";
export type HeroSelectionKey = "unified" | InfrastructureLayerKey;
export type RuntimeIconKey =
  | "python" | "numpy" | "pandas" | "scikitlearn" | "scipy"
  | "pytorch" | "nvidia" | "docker" | "github";
export type PlatformFamily = "macos" | "linux" | "windows" | "mobile" | "other";

export const HERO_LAYER_ORDER = ["external", "rented", "owned", "everyday"] as const;
export const HERO_SELECTION_ORDER = ["unified", "everyday", "owned", "rented", "external"] as const;
```

Add `HERO_LAYER_DETAILS` with one short label, one plain-English source description, and one outcome per layer. Keep provider names out of the hero. Define `RUNTIME_SUPPORT` with `as const satisfies readonly ...[]`, using only the four aliases verified in `images.py`. Add `HOST_SUPPORT`, `MACHINE_HINTS`, and pure `inferPlatformFamily(signals)`.

Check touch/mobile signals before macOS so an iPad reporting `MacIntel` is not shown as a Mac. Windows copy says Preview through Docker Desktop + WSL2 and that prerequisites cannot be verified. macOS/Linux copy says CPU architecture cannot be verified. Fallback copy says the browser cannot verify the host.

Update `workflow.ts` to export the seven ordered steps plus:

```ts
export const WORKFLOW_EVENTS = [
  "LEASE_CLAIMED",
  "CHECKPOINT_MANIFEST_COMMITTED",
  "NODE_HEARTBEAT_LOST",
  "TASK_REQUEUED",
  "TASK_COMMIT_ACCEPTED",
] as const;
```

Every step gets a short eyebrow, headline, body, and outcome. Do not add throughput, provider discovery, arbitrary-binary, or guarantee claims.

### Step 4: Verify and checkpoint locally

Run:

```bash
npx vitest run lib/landing-infrastructure-story.test.ts
git diff --check
git status --short
```

Expected: focused test passes and only intended local files changed. Do not commit.

## Task 2: Centralize animation capability state

**Files:**

- Modify: `apps/web/components/landing/LandingMotionProvider.tsx`
- Modify: `apps/web/lib/landing-infrastructure-story.test.ts`

### Step 1: Add a failing provider contract test

Read the provider source in the test and assert that its public state is:

```ts
type LandingMotionState = {
  reduced: boolean;
  desktop: boolean;
  finePointer: boolean;
  documentVisible: boolean;
};
```

Also assert that the source contains `(min-width: 1024px)`, `(pointer: fine)`, and `visibilitychange`.

Run `npx vitest run lib/landing-infrastructure-story.test.ts`.

Expected: fail because the provider currently exposes two fields and uses a 768px desktop boundary.

### Step 2: Implement conservative capability detection

Initialize server and first-client render to:

```ts
const INITIAL_MOTION_STATE: LandingMotionState = {
  reduced: true,
  desktop: false,
  finePointer: false,
  documentVisible: true,
};
```

After mount, subscribe to reduced motion, desktop, fine pointer, and `document.visibilityState`. Clean up every media-query and document listener. Expensive ambient animation runs only when `!reduced && documentVisible`; pointer tilt additionally requires `desktop && finePointer`.

### Step 3: Verify the provider

Run:

```bash
npx vitest run lib/landing-infrastructure-story.test.ts
npx tsc --noEmit
```

Expected: both commands pass. Do not commit.

## Task 3: Build the selectable abstract 3D infrastructure hero

**Files:**

- Create: `apps/web/components/landing/HeroInfrastructureStack.tsx`
- Modify: `apps/web/components/landing/Hero.tsx`
- Modify: `apps/web/app/globals.css`
- Modify: `apps/web/lib/landing-infrastructure-story.test.ts`
- Delete after verification: `apps/web/components/landing/HeroSystemStage.tsx`

### Step 1: Add failing hero tests

Render `HeroInfrastructureStack` and assert:

- four visual planes exist through `[data-hero-plane]`;
- five tabs exist in Unified, Everyday, Owned, Rented, Cloud/HPC order;
- Unified is selected initially;
- selecting a layer updates one stable `[data-hero-detail]` region;
- the decorative stack is `aria-hidden="true"` and contains no SVG `<text>`;
- a `<noscript>` fallback lists the four infrastructure sources;
- separate `[data-stack-entry]` and `[data-hero-plane]` wrappers exist.

Add a Hero test for this exact definition:

```text
Zolli unifies compatible cloud capacity, rented compute, owned GPU infrastructure, and everyday machines under one control plane, then recovers work when a node disappears.
```

Run `npx vitest run lib/landing-infrastructure-story.test.ts`.

Expected: fail because the new hero component is absent.

### Step 2: Implement semantic selection

Use a client component with:

- `activeSelection` defaulting to `"unified"`;
- a labeled `role="tablist"` and five `role="tab"` buttons;
- Left/Right Arrow, Home, and End navigation;
- one stable detail panel outside the perspective stack;
- a `<noscript>` static list for non-enhanced access;
- Motion `AnimatePresence` only for detail content and selection feedback;
- `useLandingMotion()` for capability gates;
- `useGSAP()` for the first-entry sequence.

Use separate ownership wrappers:

```tsx
<div data-stack-entry>
  <motion.div data-hero-plane>
    <div className="hero-infra-plane" aria-hidden="true" />
  </motion.div>
</div>
```

GSAP transforms `data-stack-entry`; Motion transforms `data-hero-plane`; CSS transforms only `.hero-infra-plane` and decorative children.

### Step 3: Implement the visual language

Create a perspective stage around `1100px`. Each plane is a translucent technical slab with a border, subtle grid, and empty `<i>` capacity silhouettes. Planes are bottom-to-top:

1. Cloud & HPC services
2. Rented CPU / GPU
3. Owned GPU fleet
4. Everyday machines

Do not put copy inside tilted planes. The tab and stable detail panel carry all text.

The Unified state shows an orange control field spanning all layers and a restrained green task pulse resolving through the stack. A selected layer lifts while other layers dim but remain visible. Cap pointer tilt at 3° rotation and 8px translation using CSS variables. Disable tilt unless desktop, fine pointer, visible document, and non-reduced motion are all true.

### Step 4: Compose responsive and reduced-motion paths

Keep the headline `Compute that finishes the job.` plus the exact definition from Step 1. Keep **Open console** primary and **Talk to Zolli** secondary.

Below 1024px, reduce perspective and stack depth. At phone widths, render a compact front-facing stack above the selector without overlap. Under reduced motion, show the final Unified state immediately, remove pulse and tilt, and keep selection usable.

### Step 5: Verify, then remove the old visual

Run:

```bash
npx vitest run lib/landing-infrastructure-story.test.ts lib/landing-cinematic.test.ts
npx tsc --noEmit
npx eslint components/landing/Hero.tsx components/landing/HeroInfrastructureStack.tsx
rg "HeroSystemStage" apps/web
```

When the only match is the old file, delete it with `apply_patch`, rerun the checks, then run `git diff --check` and `git status --short`. Do not commit.

## Task 4: Replace platform text walls with an icon explorer and careful machine hint

**Files:**

- Create: `apps/web/components/landing/RuntimeSupportExplorer.tsx`
- Create: `apps/web/components/landing/MachineCompatibilityCheck.tsx`
- Modify: `apps/web/components/landing/PlatformSupport.tsx`
- Modify: `apps/web/app/globals.css`
- Modify: `apps/web/lib/landing-infrastructure-story.test.ts`
- Modify: `apps/web/lib/landing-expansion.test.ts`

### Step 1: Add failing platform UI tests

Render `PlatformSupport` and assert:

- nine labeled runtime buttons and one stable runtime detail panel;
- three host cards with visible Proven or Preview labels;
- the machine result is absent before activation;
- clicking **Check this browser** creates a polite live status;
- every result ends with `Run flashnode doctor for a real host check.`;
- no result contains `Your machine is supported`.

Replace tests that expect the old five integration rows. Run:

```bash
npx vitest run lib/landing-infrastructure-story.test.ts lib/landing-expansion.test.ts
```

Expected: fail against the old text-row component.

### Step 2: Build the runtime explorer

Import these icons directly from `simple-icons/icons`:

```ts
import {
  siDocker,
  siGithub,
  siNumpy,
  siNvidia,
  siPandas,
  siPython,
  siPytorch,
  siScikitlearn,
  siScipy,
} from "simple-icons/icons";
```

Map `RuntimeIconKey` to those icons. Render each icon as decoration beside a real text label; never use logo-only buttons. Selecting a runtime updates a stable description area and shows a registered image alias only when the typed data includes one.

### Step 3: Build the local machine hint

Do not access `navigator` during server render. Only after **Check this browser** is clicked, read `navigator.userAgent`, `navigator.platform`, and `navigator.maxTouchPoints`, pass them to `inferPlatformFamily`, and render `MACHINE_HINTS[family]` in `role="status"` with `aria-live="polite"`.

This is an OS-family hint, not compatibility detection. It must never claim CPU architecture, Docker/WSL availability, GPU/CUDA state, or support verdict.

### Step 4: Recompose the section

Use a concise three-part composition:

1. nine-icon runtime explorer;
2. three short host-state cards;
3. optional machine-check panel.

Keep the sand section surface. Use green only for Proven, amber/orange for Preview, and neutral styling before the user requests a check.

### Step 5: Verify the platform section

Run:

```bash
npx vitest run lib/landing-infrastructure-story.test.ts lib/landing-expansion.test.ts
npx tsc --noEmit
npx eslint components/landing/PlatformSupport.tsx components/landing/RuntimeSupportExplorer.tsx components/landing/MachineCompatibilityCheck.tsx
git diff --check
```

Expected: focused tests, typecheck, lint, and diff check pass. Do not commit.


## Task 5: Replace the dense topology with seven understandable workflow scenes

**Files:**

- Create: `apps/web/components/landing/WorkflowScene.tsx`
- Modify: `apps/web/components/landing/SystemJourney.tsx`
- Modify: `apps/web/lib/landing/workflow.ts`
- Modify: `apps/web/app/globals.css`
- Modify: `apps/web/lib/landing-infrastructure-story.test.ts`
- Modify: `apps/web/lib/landing-cinematic.test.ts`

### Step 1: Add failing density and chronology tests

Export `WORKFLOW_SCENES` from the new component and test it directly:

```ts
expect(WORKFLOW_SCENES.map(({ id }) => id)).toEqual([
  "connect", "register", "submit", "parallel", "checkpoint", "recover", "accept",
]);
expect(WORKFLOW_SCENES.every(({ objects, paths }) =>
  objects.length <= 5 && paths.length <= 2
)).toBe(true);
expect(WORKFLOW_SCENES.flatMap(({ events }) => events)).toEqual([
  "LEASE_CLAIMED",
  "CHECKPOINT_MANIFEST_COMMITTED",
  "NODE_HEARTBEAT_LOST",
  "TASK_REQUEUED",
  "TASK_COMMIT_ACCEPTED",
]);
```

Render `SystemJourney` and assert seven ordered steps. Assert that the old topology header and protocol ticker are absent. Run:

```bash
npx vitest run lib/landing-infrastructure-story.test.ts lib/landing-cinematic.test.ts
```

Expected: fail because the existing journey uses a dense shared topology and six steps.

### Step 2: Define one visual idea per scene

Use these maximum object counts and event assignments:

| Scene | Primary objects | Paths | Event shown |
|---|---:|---:|---|
| connect | 5 | 2 | none |
| register | 4 | 1 | none |
| submit | 3 | 1 | `LEASE_CLAIMED` |
| parallel | 5 | 2 | none |
| checkpoint | 4 | 1 | `CHECKPOINT_MANIFEST_COMMITTED` |
| recover | 4 | 2 | `NODE_HEARTBEAT_LOST`, `TASK_REQUEUED` |
| accept | 3 | 1 | `TASK_COMMIT_ACCEPTED` |

The drawing for each scene must answer only one question:

- connect: which capacity joins;
- register: what the control plane learns;
- submit: what work enters;
- parallel: how tasks fan out;
- checkpoint: what progress is saved;
- recover: what happens after one node disappears;
- accept: which final result is retained.

### Step 3: Implement `WorkflowScene`

Use semantic HTML for titles and state labels. The diagram itself may be `aria-hidden` if the adjacent copy contains the same meaning. Put `data-scene-object`, `data-scene-path`, and `data-scene-event` on primary visual elements so density remains testable.

Use Motion presence for scene-to-scene local transitions. Do not use Motion layout animations. Core copy remains mounted and readable. In reduced motion, replace without interpolation. CSS must target `[data-scene-path]`, matching the rendered data attribute.

### Step 4: Refactor desktop and mobile journey behavior

In `SystemJourney`:

- keep `activeIndex` in React state;
- on desktop and non-reduced motion, use GSAP ScrollTrigger to pin only the scene stage;
- create one trigger per text step; `onEnter` and `onEnterBack` set the active index;
- leave the section heading and text column in normal document flow;
- destroy all triggers through the `useGSAP` scope cleanup.

Below 1024px or under reduced motion, do not pin. Render each step followed immediately by its compact static scene. This prevents mobile readers from relating a paragraph to a distant shared picture.

### Step 5: Verify narrative continuity

Run:

```bash
npx vitest run lib/landing-infrastructure-story.test.ts lib/landing-cinematic.test.ts
npx tsc --noEmit
npx eslint components/landing/SystemJourney.tsx components/landing/WorkflowScene.tsx
git diff --check
```

Then manually read only the headlines and outcomes in sequence. They must independently communicate: capacity joins, becomes known, receives work, runs in parallel, saves progress, survives disappearance, and accepts one result. Do not commit.

## Task 6: Add purposeful motion to workloads, architecture, and services

**Files:**

- Create: `apps/web/components/landing/WorkloadRows.tsx`
- Modify: `apps/web/components/landing/WorkloadFit.tsx`
- Modify: `apps/web/components/landing/WorkloadVelocityRail.tsx`
- Create: `apps/web/components/landing/ArchitectureSignal.tsx`
- Modify: `apps/web/components/landing/SystemModules.tsx`
- Modify: `apps/web/components/landing/ProfessionalServices.tsx`
- Modify: `apps/web/app/globals.css`
- Modify: `apps/web/lib/landing-cinematic.test.ts`
- Modify: `apps/web/lib/landing-infrastructure-story.test.ts`

### Step 1: Add failing motion and copy-safety tests

Read the new component sources and assert:

- workload copy has no initial `opacity: 0` or `autoAlpha: 0` state;
- decorative workload rules use `[data-animated]` and can grow independently;
- the architecture visual contains exactly three signal paths;
- the services headline uses `landing-heading-balance`;
- no source adds unsupported percentage, fleet-scale, provider, or guarantee claims.

Run:

```bash
npx vitest run lib/landing-infrastructure-story.test.ts lib/landing-cinematic.test.ts
```

Expected: fail because the components do not exist or do not expose the new contracts.

### Step 2: Animate workload rows without hiding copy

Move the four workload rows into `WorkloadRows.tsx`. Use Motion `whileInView` for small x/y settling and emphasis, but keep all text fully visible in initial HTML. Let a separate decorative rule grow from 0 to 100% through its own wrapper. Trigger once with a generous negative viewport margin.

Keep the four workload messages factual:

- federated training;
- hyperparameter search;
- shared data processing;
- checkpointable model training.

Update `WorkloadVelocityRail` to use the shared provider and treat desktop as 1024px or wider. Set `data-animated="true"` only when the animation path is active.

### Step 3: Add the architecture signal

Create a restrained three-path SVG/DOM signal showing lease, checkpoint, and recovery flowing through the architecture modules. GSAP draws the three paths once when the visual enters view. Under reduced motion, paths render complete. Keep copy outside the SVG and do not introduce a second topology map.

### Step 4: Improve services hierarchy

Apply `landing-heading-balance` to the large services headline and reduce the maximum clamp enough to avoid the single-word drops seen in the reference capture. Wrap the supporting copy/actions in the existing `SectionReveal`, preserving the Calendly URL and contact email. Motion should direct attention toward scheduling while **Open console** remains the page-wide primary CTA.

### Step 5: Verify the supporting sections

Run:

```bash
npx vitest run lib/landing-infrastructure-story.test.ts lib/landing-cinematic.test.ts lib/landing-expansion.test.ts
npx tsc --noEmit
npx eslint components/landing/WorkloadRows.tsx components/landing/WorkloadFit.tsx components/landing/WorkloadVelocityRail.tsx components/landing/ArchitectureSignal.tsx components/landing/SystemModules.tsx components/landing/ProfessionalServices.tsx
git diff --check
```

Expected: focused tests, typecheck, lint, and diff check pass. Do not commit.
## Task 7: Run integrated accessibility, motion, and claim guards

**Files:**

- Modify: `apps/web/lib/landing-infrastructure-story.test.ts`
- Modify: `apps/web/lib/landing-cinematic.test.ts`
- Modify: `apps/web/lib/landing-expansion.test.ts`
- Modify: `apps/web/lib/landing-rebrand.test.ts`
- Modify only if a guard finds a defect: files changed in Tasks 2–6

### Step 1: Add page-level integration tests

Statically import the marketing `Home` page and relevant client components. Do not use dynamic `await import` inside non-async tests.

Add assertions for:

- all ten landing sections appear in the approved order;
- their surfaces remain dark, light, sand, dark, light, dark, light, sand, light, orange, followed by dark footer;
- the hero uses four visual layers and five selector states;
- platform support has nine runtimes and three qualified hosts;
- the workflow has seven chronological scenes and the five exact events;
- the page retains FAQ, contact, legal, **Open console**, and Calendly paths;
- no new percentage, fleet-scale, customer, performance, provider, or universal-support claim appears;
- GSAP, Motion, and CSS transform ownership stays on separate wrappers;
- no new `canvas`, WebGL, Three.js, Motion layout animation, or smooth-scroll code appears;
- reduced-motion and hidden-document branches exist;
- tabs, status regions, labels, and keyboard controls are accessible by role and name.

### Step 2: Run the complete automated gate

From `apps/web`, run:

```bash
npm run lint
npx tsc --noEmit
npm test
npm run build
git diff --check
```

Expected: all commands exit 0. Compare the final `/` route size with the Task 1 baseline. The landing route may grow by at most 20 KB gzip. If it exceeds that budget, confirm every icon is imported directly and remove duplicated animation/runtime code before changing the budget.

### Step 3: Check repository integrity

Run `git status --short` and inspect every modified path. Confirm that no unrelated dirty file was staged, reverted, or overwritten. Do not commit.

## Task 8: Perform browser QA at all required states and sizes

**Files:**

- Create: `.superpowers/sdd/2026-08-09-zolli-abstract-infrastructure-landing/task-8-browser-qa.md`
- Modify only to repair reproduced defects: files from Tasks 2–7

### Step 1: Start a stable local preview

From `apps/web`, run:

```bash
npm run dev -- -p 3003
```

Confirm `http://127.0.0.1:3003/` returns successfully before browser inspection. Keep the process running in its own terminal session. If the required browser skill reports its one-time setup gate, ask the user for approval before installing or launching it.

### Step 2: Verify interaction and story at desktop size

At 1440×900, inspect and record:

- Hero: Unified plus Everyday, Owned, Rented, and Cloud/HPC selections; correct detail copy; pointer tilt bounded; keyboard tab navigation; primary CTA hierarchy.
- Evidence: exactly `30 / 2 / 5 / 1` and no fake comparison metric.
- Platform: nine icon labels, four registered image aliases where applicable, three qualified host states, no result before click, and careful machine hint after click.
- Workflow: all seven scenes in order; no scene exceeds five objects or two paths; recovery shows node loss before requeue; accepted result comes last.
- Workloads: four readable uses with motion that emphasizes rather than obscures.
- Architecture: three one-shot signal paths and readable module content.
- Recovery, services, FAQ, CTA, footer, legal, contact, Calendly, and console links.

Capture screenshots for the hero Unified state, one selected hero layer, platform support, checkpoint scene, recovery scene, workloads, and services.

### Step 3: Verify the responsive matrix

Repeat layout inspection at:

| Viewport | Required behavior |
|---|---|
| 1024×768 | Desktop scene pinning is stable; no hero/control collision |
| 768×1024 | No pinning; every workflow paragraph is adjacent to its compact scene |
| 390×844 | Front-facing hero stack; reachable selector; no horizontal overflow |
| 375×812 | No one-word heading orphan; CTA/footer/legal remain usable |

At every width, verify no clipped copy, overlapped controls, accidental line drop, unreadable plane label, or unexplained visual state.

### Step 4: Verify motion fallbacks

Emulate reduced motion and confirm:

- the hero loads in its final Unified state;
- selectors still update details;
- workflow uses static inline scenes and no pin;
- architecture paths render complete;
- all content is readable without waiting for animation.

Move the page offscreen and hide the document; confirm ambient animation pauses. Return and confirm it resumes without duplicate GSAP triggers or console errors.

### Step 5: Document and repair findings test-first

Write the QA report with:

- date, branch, local URL, and tested commit state (`uncommitted preview`);
- automated command results;
- baseline/final route sizes;
- five-width matrix;
- hero selector, machine hint, keyboard, reduced-motion, and document-visibility results;
- screenshot paths;
- every defect with severity, reproduction, cause, repair, and verification;
- remaining concerns, explicitly writing `None observed` when there are none.

For each defect, add or strengthen a failing test, reproduce it, apply the smallest fix, then rerun the focused and complete gates. Do not commit.

## Task 9: Stop at the user preview gate

**Files:** none unless the user requests revisions.

### Step 1: Present the local result

Give the user:

- `http://127.0.0.1:3003/`;
- a short list of the hero, platform, workflow, and motion changes;
- the exact automated test/build status;
- the browser QA matrix status;
- a reminder that the work remains uncommitted.

Then stop and wait for explicit visual approval.

### Step 2: Route feedback to the smallest task

If the user requests changes, return to the narrowest affected task, add a failing regression test, implement the revision, rerun focused checks, update browser QA, and present the preview again. Do not commit during this loop.

### Step 3: Only after explicit approval, run final verification

Run:

```bash
npm run lint
npx tsc --noEmit
npm test
npm run build
git diff --check
```

If the user then explicitly authorizes a commit, stage only the exact landing implementation, test, spec, plan, and QA-report paths. Do not use `git add -A`. Inspect `git diff --cached --stat` and `git diff --cached --check` before committing.

Suggested commit message, only after separate authorization:

```bash
git commit -m "feat(web): tell the Zolli compute story in motion"
```

Do not push, merge, or deploy without separate authority.

## Acceptance checklist

- [ ] Hero immediately communicates that Zolli unifies fragmented compute and recovers interrupted work.
- [ ] Hero has four readable abstract layers, five usable selector states, and no copy trapped inside tilted planes.
- [ ] Hero detail text, keyboard behavior, small-screen layout, and reduced-motion behavior are verified.
- [ ] Platform support uses nine icons/labels, four verified image aliases, and three qualified host states.
- [ ] Machine check is opt-in, local, cautious, and ends with the real `flashnode doctor` next step.
- [ ] Workflow is seven scenes with at most five objects and two paths per scene.
- [ ] Node loss precedes requeue; checkpoint precedes recovery; acceptance is the final state.
- [ ] Workloads, architecture, and services use motion to direct attention without hiding content.
- [ ] The exact surface rhythm and ten-section structure remain intact.
- [ ] No fabricated numbers, provider support, scale, performance, or compatibility verdict appears.
- [ ] All five viewport sizes, keyboard access, reduced motion, visibility pause, and console health pass.
- [ ] Lint, typecheck, full tests, production build, route-size budget, and diff check pass.
- [ ] User has reviewed the local preview before any commit is considered.
