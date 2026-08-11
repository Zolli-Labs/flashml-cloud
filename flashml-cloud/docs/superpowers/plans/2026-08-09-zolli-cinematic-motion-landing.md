# Zolli Cinematic Motion Landing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform the existing proof-led Zolli landing into the approved balanced cinematic page with meaningful light/dark chapters, a scroll-pinned end-to-end workflow, and three controlled attention peaks.

**Architecture:** Keep the route and marketing copy server-rendered, introduce landing-scoped surface tokens, and isolate browser animation in small client components. Use GSAP ScrollTrigger for pinned/scrubbed sequences and the existing Motion package for micro-interactions; mobile and reduced-motion modes render complete non-pinned alternatives.

**Tech Stack:** Next.js 16.2.9 App Router, React 19.2.4, Tailwind CSS 4, GSAP 3.15.0, `@gsap/react` 2.1.2, Motion 12.42.2, Vitest 4.1.10, TypeScript 5.

## Global Constraints

- Work only in `/Users/phongcao/Work/Zolli-Labs/flashml-cloud-zolli-rebrand` on branch `zolli-warm-technical-rebrand`.
- Preview-first override: do not commit, push, merge, or deploy; every task ends with an uncommitted review checkpoint.
- Preserve the exact evidence values `30`, `2`, `5`, and `1`; do not add percentages, customer counts, uptime, speedup, savings, pricing, or testimonials.
- Preserve `Open console` → `/workspaces` as the dominant CTA and `Talk/Schedule with Zolli` → `https://calendly.com/phongct1105/zolli-ai` as secondary.
- Preserve `phongct1105@gmail.com`, all public information routes, middleware behavior, FAQ answers, and legal boundaries.
- Surface sequence: hero dark, evidence ivory, platform sand, workflow dark, workloads ivory, architecture graphite, recovery ivory, services sand, FAQ ivory, closing CTA orange, footer graphite.
- Animate only transform, opacity, clip path, and SVG stroke properties; do not intercept wheel/touch scrolling or add smooth-scroll, WebGL, video, shaders, stock images, or generic particles.
- Essential content must be server-visible before hydration and remain readable when JavaScript fails.
- At widths below 768px, remove pinning and horizontal scrub; use a normal vertical workflow.
- `prefers-reduced-motion: reduce` renders final states, disables pinning/scrubbing/loops, and preserves all content and actions.
- All controls retain visible focus and at least 40px targets; no horizontal overflow at 1440, 1024, 768, 390, or 375 pixels.

---

## File Structure

### Create

- `apps/web/components/landing/motion/LandingMotionProvider.tsx` — shared reduced-motion and desktop-pin preferences.
- `apps/web/components/landing/motion/SectionReveal.tsx` — progressive shutter/rule reveal wrapper.
- `apps/web/components/landing/HeroSystemStage.tsx` — client-only animated hero topology.
- `apps/web/components/landing/SystemJourney.tsx` — client-only six-stage pinned workflow and mobile fallback.
- `apps/web/components/landing/WorkloadVelocityRail.tsx` — client-only scroll-linked workload rail.
- `apps/web/components/landing/RecoveryStack.tsx` — one-shot recovery proof stack.
- `apps/web/components/landing/CommitSignal.tsx` — closing accepted-commit signal.
- `apps/web/lib/landing/workflow.ts` — exact workflow stages and protocol event data.
- `apps/web/lib/landing-cinematic.test.ts` — surface, workflow, motion-boundary, and copy contracts.

### Modify

- `apps/web/package.json`, `apps/web/package-lock.json` — exact GSAP dependencies.
- `apps/web/app/globals.css` — landing surface tokens, grain, light-section resets, and reduced-motion fallbacks.
- `apps/web/app/(marketing)/layout.tsx` — retain marketing chrome; do not apply landing-only colors to legal pages.
- `apps/web/app/(marketing)/page.tsx` — provider, overflow containment, and revised component order.
- `apps/web/components/nav/Navbar.tsx` — transparent-over-hero behavior on `/` and opaque behavior elsewhere.
- `apps/web/components/landing/Hero.tsx` — editorial split and `HeroSystemStage`.
- `apps/web/components/landing/EvidenceBand.tsx` — ivory evidence ledger.
- `apps/web/components/landing/PlatformSupport.tsx` — sand machine lanes.
- `apps/web/components/landing/WorkloadFit.tsx` — ivory editorial rows plus velocity rail.
- `apps/web/components/landing/SystemModules.tsx` — dense architecture composition.
- `apps/web/components/landing/RecoveryDemo.tsx` — ivory proof plus graphite ledger inset.
- `apps/web/components/landing/ProfessionalServices.tsx` — two editorial service rows.
- `apps/web/components/landing/Faq.tsx` — light typographic disclosures.
- `apps/web/components/landing/ClosingCta.tsx` — orange action chapter and graphite footer.
- `apps/web/lib/landing-expansion.test.ts` — preserve existing commercial, credibility, FAQ, and footer contracts.
- `apps/web/lib/landing-rebrand.test.ts` — preserve headline, CTA, protocol, and anti-mascot contracts.
- `PROGRESS.md` — final verified implementation/QA evidence only.

### Delete after callers migrate

- `apps/web/components/landing/SystemTopology.tsx` — replaced by `HeroSystemStage`.
- `apps/web/components/landing/SystemStory.tsx` — replaced by `SystemJourney`.

---

### Task 1: Motion foundation and landing-scoped surfaces

**Files:**
- Create: `apps/web/components/landing/motion/LandingMotionProvider.tsx`
- Create: `apps/web/components/landing/motion/SectionReveal.tsx`
- Create: `apps/web/lib/landing-cinematic.test.ts`
- Modify: `apps/web/package.json`
- Modify: `apps/web/package-lock.json`
- Modify: `apps/web/app/globals.css`
- Modify: `apps/web/app/(marketing)/page.tsx`

**Interfaces:**
- Produces: `LandingMotionProvider({ children }: { children: ReactNode })`.
- Produces: `useLandingMotion(): { reduced: boolean; desktop: boolean }`.
- Produces: `SectionReveal({ children, className }: { children: ReactNode; className?: string })`.
- Produces section attributes `data-surface="dark|light|sand|orange"` and motion hooks under `data-motion`.
- Consumes no new application state or API.

- [ ] **Step 1: Write the failing dependency and surface tests**

Add these contracts to `lib/landing-cinematic.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Home from "@/app/(marketing)/page";

const root = process.cwd();
const source = (path: string) => readFileSync(`${root}/${path}`, "utf8");
const renderLanding = () => renderToStaticMarkup(createElement(Home));

describe("cinematic landing foundation", () => {
  it("pins the approved motion packages", () => {
    const pkg = JSON.parse(source("package.json"));
    expect(pkg.dependencies.gsap).toBe("3.15.0");
    expect(pkg.dependencies["@gsap/react"]).toBe("2.1.2");
    expect(pkg.dependencies.motion).toBe("^12.42.2");
  });

  it("contains landing motion without hiding server content", () => {
    const markup = renderLanding();
    expect(markup).toContain('data-landing="cinematic"');
    expect(markup).toContain("Compute that");
    expect(markup).toContain("Open console");
    expect(markup).not.toContain('style="opacity:0"');
  });

  it("defines all four landing surfaces and reduced-motion fallback", () => {
    const css = source("app/globals.css");
    for (const token of [
      "--landing-graphite: #0b0d0e",
      "--landing-ivory: #f2efe6",
      "--landing-sand: #ded8cb",
      "--landing-orange: #f36b32",
    ]) expect(css).toContain(token);
    expect(css).toContain(".landing-surface-light");
    expect(css).toContain(".landing-surface-sand");
    expect(css).toContain(".landing-surface-orange");
    expect(css).toContain("prefers-reduced-motion: reduce");
  });
});
```

- [ ] **Step 2: Run the test and verify RED**

Run from `apps/web`:

```bash
npx vitest run lib/landing-cinematic.test.ts
```

Expected: FAIL because GSAP dependencies, provider markup, and landing surface tokens do not exist.

- [ ] **Step 3: Install exact dependencies**

Run from `apps/web`:

```bash
npm install --save-exact gsap@3.15.0 @gsap/react@2.1.2
```

Verify both exact versions are present in `package.json` and the lockfile records resolved integrity values.

- [ ] **Step 4: Add the provider and reveal boundary**

Implement `LandingMotionProvider.tsx` with this public contract:

```tsx
"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";

type LandingMotionState = { reduced: boolean; desktop: boolean };
const LandingMotionContext = createContext<LandingMotionState>({ reduced: true, desktop: false });

export function LandingMotionProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<LandingMotionState>({ reduced: true, desktop: false });

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    const desktop = window.matchMedia("(min-width: 768px)");
    const sync = () => setState({ reduced: reduced.matches, desktop: desktop.matches });
    sync();
    reduced.addEventListener("change", sync);
    desktop.addEventListener("change", sync);
    return () => {
      reduced.removeEventListener("change", sync);
      desktop.removeEventListener("change", sync);
    };
  }, []);

  const value = useMemo(() => state, [state]);
  return <LandingMotionContext.Provider value={value}>{children}</LandingMotionContext.Provider>;
}

export function useLandingMotion() {
  return useContext(LandingMotionContext);
}
```

Implement `SectionReveal.tsx` as a client wrapper that uses `useGSAP()` scoped to one ref. When `reduced` is true, call `gsap.set()` to the final state and do not create a ScrollTrigger. When motion is enabled, animate `[data-reveal-line]` from `clipPath: "inset(0 100% 0 0)"` and `[data-reveal-content]` from `yPercent: 10` and `opacity: 0`; the server markup itself must not contain hidden inline styles.

- [ ] **Step 5: Add landing tokens and route containment**

Add landing-scoped CSS variables and utilities under `.landing-cinematic`. The light/sand utilities must set `--background`, `--foreground`, `--surface`, `--surface-2`, `--muted-foreground`, `--border`, `--input`, and `--ring` so nested existing utilities remain readable. Add a fixed low-opacity grain pseudo-element with `pointer-events: none`.

Wrap the landing component sequence in:

```tsx
<LandingMotionProvider>
  <div data-landing="cinematic" className="landing-cinematic w-full max-w-full overflow-x-clip">
    {/* existing landing sections remain in their current order */}
  </div>
</LandingMotionProvider>
```

Do not put `.landing-cinematic` on the marketing route-group layout; contact/legal pages are outside this redesign.

- [ ] **Step 6: Verify GREEN and foundation safety**

```bash
npx vitest run lib/landing-cinematic.test.ts lib/landing-expansion.test.ts lib/landing-rebrand.test.ts
npx tsc --noEmit
```

Expected: all selected tests pass; typecheck exits 0.

- [ ] **Step 7: Record an uncommitted review checkpoint**

Run `git diff --check` and record Task 1 evidence in the SDD/implementation ledger. Do not commit.

---

### Task 2: Editorial hero and animated system stage

**Files:**
- Create: `apps/web/components/landing/HeroSystemStage.tsx`
- Modify: `apps/web/components/landing/Hero.tsx`
- Modify: `apps/web/components/nav/Navbar.tsx`
- Modify: `apps/web/lib/landing-cinematic.test.ts`
- Delete: `apps/web/components/landing/SystemTopology.tsx`

**Interfaces:**
- Consumes: `useLandingMotion()` from Task 1.
- Produces: `<HeroSystemStage />` with `data-motion="hero-system"` and an accessible topology label.
- Produces: hero `id="hero" data-surface="dark"`.
- Navbar preserves the exact five desktop/mobile destinations and current menu behavior.

- [ ] **Step 1: Add failing hero contracts**

```ts
it("renders the editorial hero and system stage", () => {
  const markup = renderLanding();
  const hero = markup.match(/<section[^>]*id="hero"[\s\S]*?<\/section>/)?.[0] ?? "";
  expect(hero).toContain('data-surface="dark"');
  expect(hero).toContain('data-motion="hero-system"');
  expect(hero).toContain("Compute that");
  expect(hero).toContain("finishes the job.");
  expect(hero).toContain('href="/workspaces"');
  expect(hero).toContain('href="https://calendly.com/phongct1105/zolli-ai"');
  expect(hero).not.toMatch(/data-evidence-value|production attempts/);
});

it("keeps the hero motion scoped and reduced-motion aware", () => {
  const heroMotion = source("components/landing/HeroSystemStage.tsx");
  expect(heroMotion).toContain("useGSAP");
  expect(heroMotion).toContain("ScrollTrigger");
  expect(heroMotion).toContain("useLandingMotion");
  expect(heroMotion).toContain("gsap.registerPlugin");
  expect(heroMotion).not.toMatch(/WebGL|canvas|getContext\(|addEventListener\(["']wheel/);
});
```

- [ ] **Step 2: Run RED**

```bash
npx vitest run lib/landing-cinematic.test.ts lib/landing-expansion.test.ts
```

Expected: FAIL because hero attributes and `HeroSystemStage` do not exist.

- [ ] **Step 3: Implement the server-visible stage**

Create a client component with four exact machine nodes:

```ts
const HERO_NODES = [
  ["cloud-pod-07", "A100 · us-west", "healthy"],
  ["home-rig-02", "RTX 4090 · local", "healthy"],
  ["lab-node-11", "L40S · campus", "healthy"],
  ["spot-gpu-04", "A10G · recovering", "warning"],
] as const;
```

Render an SVG with a central control-plane ring, four labelled node rectangles,
connection paths, one task token, and a compact real-event strip. The complete
stage exists in server markup. In `useGSAP()`:

- register `useGSAP` and `ScrollTrigger`;
- scope selectors to the component ref;
- reduced mode sets final stroke/opacity/transform values;
- normal mode reveals the stage with a single entrance timeline;
- pointer-fine devices may map local pointer coordinates to at most 8px stage parallax;
- clean up pointer listeners in the hook return.

- [ ] **Step 4: Recompose the hero and navbar**

Change `Hero` to a wide editorial split. Use `max-w-[78rem]` for the headline and keep it two lines at 1440px. Keep the current headline, supporting paragraph, exact CTA attributes, and `Open console` before Calendly. Remove the large embedded `EventLedger` from the hero; the protocol strip in the stage provides the B-derived terminal energy.

In `Navbar`, use `usePathname()` so non-home marketing pages remain opaque. On `/`, start transparent and set an opaque data/class state after `window.scrollY > 48`, using a passive scroll listener scheduled through `requestAnimationFrame`. Preserve Escape close, focus restoration, and exact link order.

- [ ] **Step 5: Verify GREEN**

```bash
npx vitest run lib/landing-cinematic.test.ts lib/landing-expansion.test.ts lib/landing-rebrand.test.ts
npx tsc --noEmit
```

- [ ] **Step 6: Browser checkpoint**

At 1440×900 and 390×844 verify headline wrapping, CTA hierarchy, topology visibility, nav readability before/after 48px scroll, no overflow, and reduced-motion final state. Record screenshots; do not commit.

---

### Task 3: Ivory evidence ledger and sand machine lanes

**Files:**
- Modify: `apps/web/components/landing/EvidenceBand.tsx`
- Modify: `apps/web/components/landing/PlatformSupport.tsx`
- Modify: `apps/web/lib/landing-cinematic.test.ts`
- Modify: `apps/web/lib/landing-expansion.test.ts`

**Interfaces:**
- Produces: evidence `data-surface="light"` and platform `data-surface="sand"`.
- Preserves exact evidence values and `Proven` / `Preview` / `Supported` pairing.
- Consumes `SectionReveal` for one-shot rule/content entry.

- [ ] **Step 1: Add failing surface and structure contracts**

```ts
it("uses light evidence and sand machine lanes without metric cards", () => {
  const markup = renderLanding();
  const evidence = markup.match(/<section[^>]*id="evidence"[\s\S]*?<\/section>/)?.[0] ?? "";
  const platform = markup.match(/<section[^>]*id="platform"[\s\S]*?<\/section>/)?.[0] ?? "";
  expect(evidence).toContain('data-surface="light"');
  expect(platform).toContain('data-surface="sand"');
  expect(evidence.match(/data-evidence-value=/g)).toHaveLength(4);
  expect(evidence).toContain('data-layout="evidence-ledger"');
  expect(platform).toContain('data-layout="machine-lanes"');
  expect(platform).toContain("macOS arm64");
  expect(platform).toContain("Windows 11");
});
```

- [ ] **Step 2: Run RED**

```bash
npx vitest run lib/landing-cinematic.test.ts lib/landing-expansion.test.ts
```

- [ ] **Step 3: Implement the evidence ledger**

Keep the existing `EVIDENCE` tuple unchanged. Replace the card grid with one border-free typographic ledger using one top and bottom rule. Use large tabular values, label/details beneath, `SectionReveal`, and `data-layout="evidence-ledger"`. Do not animate values from zero.

- [ ] **Step 4: Implement machine lanes**

Keep `HOSTS` and `INTEGRATIONS` exact. Render three horizontal host lanes that visually connect into one control-plane rule. Keep state text in every lane. Render integrations as a lower technical strip, not five equal cards, with `data-layout="machine-lanes"`.

- [ ] **Step 5: Verify GREEN and claim guards**

```bash
npx vitest run lib/landing-cinematic.test.ts lib/landing-expansion.test.ts
```

Expected: exact `30/2/5/1`, all platform state tests, and unsupported-comparison guards pass.

- [ ] **Step 6: Browser checkpoint**

Verify the dark→ivory→sand transition at five approved widths, platform labels by text, and no horizontal overflow. Do not commit.

---

### Task 4: Signature six-stage system journey

**Files:**
- Create: `apps/web/lib/landing/workflow.ts`
- Create: `apps/web/components/landing/SystemJourney.tsx`
- Modify: `apps/web/app/(marketing)/page.tsx`
- Modify: `apps/web/lib/landing-cinematic.test.ts`
- Modify: `apps/web/lib/landing-expansion.test.ts`
- Delete: `apps/web/components/landing/SystemStory.tsx`

**Interfaces:**
- Produces `WORKFLOW_STEPS` with exact keys `connect`, `register`, `submit`, `parallel`, `checkpoint`, `recover`.
- Produces `WORKFLOW_EVENTS` with exact real event names.
- Produces `<SystemJourney />` at `id="how-it-works" data-surface="dark" data-motion="system-journey"`.
- Consumes `useLandingMotion()`.

- [ ] **Step 1: Define failing workflow contracts**

```ts
it("explains the complete machine-to-result workflow in order", () => {
  const markup = renderLanding();
  const journey = markup.match(/<section[^>]*id="how-it-works"[\s\S]*?<\/section>/)?.[0] ?? "";
  expect(journey).toContain('data-surface="dark"');
  expect(journey).toContain('data-motion="system-journey"');
  for (const phrase of [
    "Connect machines",
    "Register capacity",
    "Submit one job",
    "Split and lease tasks",
    "Checkpoint progress",
    "Recover and accept",
  ]) expect(journey).toContain(phrase);
  for (const event of [
    "LEASE_CLAIMED",
    "CHECKPOINT_MANIFEST_COMMITTED",
    "NODE_HEARTBEAT_LOST",
    "TASK_REQUEUED",
    "TASK_COMMIT_ACCEPTED",
  ]) expect(journey).toContain(event);
  expect(journey).not.toMatch(/NODE_REGISTERED|NODE_HEARTBEAT\b|JOB_SUBMITTED/);
  expect((journey.match(/<li\b/g) ?? [])).toHaveLength(6);
});

it("keeps the workflow progressive and does not hijack scrolling", () => {
  const journey = source("components/landing/SystemJourney.tsx");
  expect(journey).toContain("pin:");
  expect(journey).toContain("scrub:");
  expect(journey).toContain("desktop");
  expect(journey).toContain("reduced");
  expect(journey).not.toMatch(/preventDefault\(|ScrollSmoother|addEventListener\(["']wheel|touchmove/);
});
```

- [ ] **Step 2: Run RED**

```bash
npx vitest run lib/landing-cinematic.test.ts lib/landing-expansion.test.ts
```

- [ ] **Step 3: Create exact workflow data**

In `workflow.ts`, export immutable records with these titles and state changes:

```ts
export const WORKFLOW_STEPS = [
  { key: "connect", title: "Connect machines", event: null },
  { key: "register", title: "Register capacity", event: null },
  { key: "submit", title: "Submit one job", event: null },
  { key: "parallel", title: "Split and lease tasks", event: "LEASE_CLAIMED" },
  { key: "checkpoint", title: "Checkpoint progress", event: "CHECKPOINT_MANIFEST_COMMITTED" },
  { key: "recover", title: "Recover and accept", event: "TASK_COMMIT_ACCEPTED" },
] as const;

export const WORKFLOW_EVENTS = [
  "LEASE_CLAIMED",
  "CHECKPOINT_MANIFEST_COMMITTED",
  "NODE_HEARTBEAT_LOST",
  "TASK_REQUEUED",
  "TASK_COMMIT_ACCEPTED",
] as const;
```

The first three stages use plain-language state captions rather than invented
protocol tokens. Uppercase event styling is reserved for the five real events
in `WORKFLOW_EVENTS`.

Add the exact explanatory bodies from §6.5 of the design spec; do not introduce additional platform behavior.

- [ ] **Step 4: Implement server-visible journey markup**

Render a semantic ordered list beside one SVG/DOM topology. Each step has `data-workflow-step={key}`. The stage includes four labelled nodes, one control-plane element, task tokens, checkpoint state, failed-node state, and the real-event ticker. All are present before hydration.

- [ ] **Step 5: Implement the scoped GSAP timeline**

Register `ScrollTrigger` and `useGSAP`. Under `desktop && !reduced`, create one timeline with `pin: stageRef.current`, `scrub: 0.6`, `start: "top top+=96"`, and an end based on the steps container height. Use labels matching the six keys. Each label updates opacity/transform/stroke state for nodes, task tokens, checkpoint, failed node, and accepted result. Under mobile or reduced motion, create no ScrollTrigger and set the final accessible diagram state.

Do not pin an ancestor with `transform` or `will-change`; GSAP documentation warns this breaks fixed-position pinning.

- [ ] **Step 6: Replace `SystemStory` and verify GREEN**

Update the route import/order, delete the old component after no imports remain, then run:

```bash
npx vitest run lib/landing-cinematic.test.ts lib/landing-expansion.test.ts lib/landing-rebrand.test.ts
npx tsc --noEmit
```

- [ ] **Step 7: Browser workflow checkpoint**

At 1440×900 capture all six scroll milestones and confirm the pinned stage releases after the section. At 768 and 390 widths confirm normal vertical flow, full text, keyboard reachability, and no trapped scrolling. Repeat with reduced motion. Do not commit.

---

### Task 5: Velocity workloads and dense architecture layers

**Files:**
- Create: `apps/web/components/landing/WorkloadVelocityRail.tsx`
- Modify: `apps/web/components/landing/WorkloadFit.tsx`
- Modify: `apps/web/components/landing/SystemModules.tsx`
- Modify: `apps/web/lib/landing-cinematic.test.ts`
- Modify: `apps/web/lib/landing-expansion.test.ts`

**Interfaces:**
- Produces `<WorkloadVelocityRail labels={readonly string[]} />` with `data-motion="velocity-rail"`.
- Produces architecture `data-layout="dense-architecture"` and exact `01 Control`, `02 Execution`, `03 Integrity` text.
- Consumes `useLandingMotion()` and existing workload/layer copy.

- [ ] **Step 1: Add failing workload and architecture contracts**

```ts
it("pairs an ivory workload rail with a gapless graphite architecture", () => {
  const markup = renderLanding();
  const workloads = markup.match(/<section[^>]*id="workloads"[\s\S]*?<\/section>/)?.[0] ?? "";
  const architecture = markup.match(/<section[^>]*id="architecture"[\s\S]*?<\/section>/)?.[0] ?? "";
  expect(workloads).toContain('data-surface="light"');
  expect(workloads).toContain('data-motion="velocity-rail"');
  expect(architecture).toContain('data-surface="dark"');
  expect(architecture).toContain('data-layout="dense-architecture"');
  for (const value of ["01 Control", "02 Execution", "03 Integrity"])
    expect(architecture).toContain(value);
  expect(architecture).toContain("lg:col-span-7");
  expect(architecture).toContain("lg:col-span-5");
  expect(architecture).toContain("lg:row-span-2");
  expect(architecture).toContain("grid-flow-dense");
});
```

- [ ] **Step 2: Run RED**

```bash
npx vitest run lib/landing-cinematic.test.ts lib/landing-expansion.test.ts
```

- [ ] **Step 3: Implement the velocity rail**

Keep all four existing workload names and bodies. Render the names twice in an `aria-hidden` motion rail for a seamless visual strip while retaining one semantic list for assistive technology. Desktop normal-motion mode scrubs `xPercent` from `0` to `-28` as the section crosses the viewport. Mobile and reduced modes render a wrapped static strip. Do not use an independent infinite loop.

- [ ] **Step 4: Implement the dense architecture composition**

Keep `LAYERS` copy/events. Apply these exact desktop spans:

```ts
const LAYOUT = [
  "lg:col-span-7 lg:row-span-2",
  "lg:col-span-5",
  "lg:col-span-5",
] as const;
```

Use `grid-flow-dense lg:grid-cols-12 lg:grid-rows-2`. Control occupies the left two-row field; Execution and Integrity fill the right column. Use `SectionReveal` for expanding rules, not equal card lift animations.

- [ ] **Step 5: Verify GREEN**

```bash
npx vitest run lib/landing-cinematic.test.ts lib/landing-expansion.test.ts
npx tsc --noEmit
```

- [ ] **Step 6: Browser checkpoint**

Verify workload scrub follows normal vertical scroll, architecture has no dead grid cells, copy remains visible before/after motion, and mobile has no horizontal overflow. Do not commit.

---

### Task 6: Recovery stack, editorial lower funnel, and orange action

**Files:**
- Create: `apps/web/components/landing/RecoveryStack.tsx`
- Create: `apps/web/components/landing/CommitSignal.tsx`
- Modify: `apps/web/components/landing/RecoveryDemo.tsx`
- Modify: `apps/web/components/landing/ProfessionalServices.tsx`
- Modify: `apps/web/components/landing/Faq.tsx`
- Modify: `apps/web/components/landing/ClosingCta.tsx`
- Modify: `apps/web/components/nav/Navbar.tsx`
- Modify: `apps/web/lib/landing-cinematic.test.ts`
- Modify: `apps/web/lib/landing-expansion.test.ts`

**Interfaces:**
- Produces recovery `data-surface="light" data-motion="recovery-stack"`.
- Produces services `data-surface="sand"`, FAQ `data-surface="light"`, CTA `data-surface="orange"`, and footer `data-surface="dark"`.
- Produces `<CommitSignal event="TASK_COMMIT_ACCEPTED" />`.
- Preserves exact four service titles, seven FAQ answers, and footer groups.

- [ ] **Step 1: Add failing lower-funnel contracts**

```ts
it("finishes with light buying sections and one orange action peak", () => {
  const markup = renderLanding();
  const surfaces = [...markup.matchAll(/<section[^>]*id="(recover|services|faq|start)"[^>]*data-surface="([^"]+)"/g)]
    .map(([, id, surface]) => [id, surface]);
  expect(surfaces).toEqual([
    ["recover", "light"],
    ["services", "sand"],
    ["faq", "light"],
    ["start", "orange"],
  ]);
  expect(markup).toContain('data-motion="recovery-stack"');
  expect(markup).toContain('data-motion="commit-signal"');
  expect(markup).toContain("TASK_COMMIT_ACCEPTED");
});

it("uses editorial services instead of four equal cards", () => {
  const markup = renderLanding();
  const services = markup.match(/<section[^>]*id="services"[\s\S]*?<\/section>/)?.[0] ?? "";
  expect(services).toContain('data-layout="service-rows"');
  expect((services.match(/data-service-row=/g) ?? [])).toHaveLength(2);
  expect((services.match(/<article\b/g) ?? [])).toHaveLength(4);
});
```

- [ ] **Step 2: Run RED**

```bash
npx vitest run lib/landing-cinematic.test.ts lib/landing-expansion.test.ts
```

- [ ] **Step 3: Implement recovery composition and one-shot stack**

Keep `RECOVERY_PROOF`, `RECOVERY_EVENTS`, exact proof copy, and sample disclosure. Set the section to ivory and keep the `EventLedger` in one graphite inset. `RecoveryStack` wraps three proof rows and uses one `useGSAP()` entrance timeline that moves rows from `y: 28/56/84` to `0`; reduced motion sets all to zero immediately. The rows stop after entry and do not remain sticky.

- [ ] **Step 4: Recompose services and FAQ**

Keep all four service records exact. Group records `[0,1]` and `[2,3]` into two `data-service-row` containers separated by rules; preserve four semantic `article` elements. Keep one shared Calendly action and email.

Keep native `details`/`summary`, exact seven questions/answers, 40px targets, and keyboard behavior. Use ivory tokens and CSS driven by `[open]` for rule expansion and reveal; do not replace native disclosure with a custom accordion library.

- [ ] **Step 5: Implement the orange action and restrained footer**

Give the closing section `id="start" data-surface="orange"`. Keep `Bring the fleet. Keep the progress.`, exact body copy, and `Open console` before Calendly. The console button is graphite with mineral-white text; Calendly is a dark text link. `CommitSignal` renders `TASK_COMMIT_ACCEPTED` and uses a one-shot line/opacity reveal. The footer remains graphite with the exact four accessible navigation groups and link destinations.

- [ ] **Step 6: Verify GREEN and commercial regressions**

```bash
npx vitest run lib/landing-cinematic.test.ts lib/landing-expansion.test.ts lib/landing-rebrand.test.ts lib/public-information.test.ts
npx tsc --noEmit
```

- [ ] **Step 7: Browser checkpoint**

At five widths verify recovery proof readability, services row composition, FAQ click/Enter/Space, CTA contrast/order/URLs, footer links, focus rings, reduced motion, and no overflow. Do not commit.

---

### Task 7: Full verification, browser motion QA, and local preview

**Files:**
- Modify: `apps/web/lib/landing-cinematic.test.ts` only if a missing acceptance contract is found before implementation changes.
- Modify: `PROGRESS.md`
- Create: `.superpowers/sdd/2026-08-09-zolli-cinematic-motion-landing/task-7-report.md`
- Create: `.superpowers/sdd/2026-08-09-zolli-cinematic-motion-landing/progress.md`

**Interfaces:**
- Consumes all prior tasks.
- Produces final automated/browser evidence and a live local preview.
- Does not create a commit, push, PR, merge, or deployment.

- [ ] **Step 1: Run static safety scans**

```bash
rg -n "[0-9]+%|[0-9]+x|99\.|customers|uptime|speedup|savings|testimonial" components/landing app/\(marketing\)/page.tsx
rg -n "ScrollSmoother|preventDefault\(|addEventListener\([^)]*(wheel|touchmove)|getContext\(|WebGL" components/landing
rg -n "top:|left:|width:|height:" components/landing -g "*.tsx"
git diff --check
```

Interpret CSS utility positioning separately; the final scan must show no unsupported marketing claims, scroll interception, canvas/WebGL, or GSAP animation of layout properties.

- [ ] **Step 2: Run automated verification**

From `apps/web`:

```bash
npm run lint
npx tsc --noEmit
npm test
```

Expected: zero lint/type errors and every Vitest file/test passes.

- [ ] **Step 3: Build with the existing environment**

```bash
set -a
source /Users/phongcao/Work/Zolli-Labs/flashml-cloud/.env.dev
set +a
npm run build
```

Expected: build exit 0 and all current static pages generated.

- [ ] **Step 4: Start a fresh preview without touching unrelated servers**

Use port 3003 if free; otherwise choose the next free local port and report it. Start from `apps/web` with the existing `.env.dev`. Do not stop a process unless its command and worktree path prove it belongs to this worktree.

- [ ] **Step 5: Run browser route and overflow matrix**

For widths 1440×900, 1024×768, 768×1024, 390×844, and 375×812 verify:

- `/`, `/contact`, `/privacy`, `/terms`, `/security`, and `/sign-in` return/render correctly;
- `/workspaces` redirects exactly to `/sign-in?next=%2Fworkspaces` signed out;
- no page has horizontal overflow or console errors;
- all eight landing anchors exist and shared navigation resolves from public routes;
- all Calendly links retain exact URL, `_blank`, `noreferrer`, and accessible announcement;
- mobile menu Escape and focus restoration remain correct;
- FAQ works by pointer, Enter, and Space.

- [ ] **Step 6: Run normal-motion milestone QA**

Capture screenshots and state observations at:

1. hero before and after entrance;
2. dark→ivory evidence transition;
3. sand platform lanes;
4. each of six workflow timeline labels;
5. workload rail start/end;
6. architecture fully revealed;
7. recovery stack and ledger;
8. FAQ open state;
9. orange closing action and graphite footer.

Verify the workflow pins only on desktop, releases after its section, and never obscures focused content.

- [ ] **Step 7: Run reduced-motion QA**

Emulate `prefers-reduced-motion: reduce` at 1440 and 390 widths. Verify all content is immediately visible, no pin/scrub/loop remains, navigation and FAQ still work, and the topology shows a meaningful final state.

- [ ] **Step 8: Record exact evidence**

Append a dated `PROGRESS.md` entry containing exact test file/test counts, build page count, routes, viewport matrix, motion/reduced-motion results, preview URL, and remaining concerns. Write the Task 7 report and SDD ledger. Do not claim authenticated-console visual QA.

- [ ] **Step 9: Independent final review**

Request a diff review against the design spec and this plan. Fix every Critical/Important finding through a single scoped fix round, rerun affected checks, and request one scoped re-review.

- [ ] **Step 10: Hand off the uncommitted local preview**

Provide the local URL, summary, exact verification evidence, and disclosed concerns. State explicitly that no commit, push, PR, merge, or deployment occurred. Wait for the user's visual approval before any integration action.
