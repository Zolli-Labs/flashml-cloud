# Zolli Hero Lab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a temporary `/hero-lab` route with three bounded, interactive WebGL concepts that explain how Zolli unifies fragmented compute.

**Architecture:** Keep the comparison entirely outside the production landing route. A serializable concept model drives accessible DOM controls and three focused React Three Fiber scene components; shared scene primitives provide hardware silhouettes, paths, lighting, and camera framing.

**Tech Stack:** Next.js 16, React 19, TypeScript, React Three Fiber, Three.js, CSS Modules, Vitest, Chrome browser QA.

## Global Constraints

- Do not import the hero lab from `components/landing/Hero.tsx` or replace the production hero.
- Preserve the current Zolli palette and typography.
- Keep all explanatory text outside tilted 3D geometry.
- Use exactly four source classes in bottom-to-top product order: Cloud/HPC, Rented GPU, Owned Infrastructure, Everyday Machines.
- Cap canvas device pixel ratio and provide reduced-motion/static behavior.
- Do not commit, stage, or push the preview work.

---

### Task 1: Concept model and isolation contract

**Files:**
- Create: `apps/web/lib/hero-lab.ts`
- Create: `apps/web/lib/hero-lab.test.ts`
- Modify: `apps/web/package.json`
- Modify: `apps/web/package-lock.json`

**Interfaces:**
- Produces `HERO_LAB_VARIANTS`, `HERO_LAB_SOURCES`, `HERO_LAB_JOB_STEPS`, `HeroLabVariantKey`, and `HeroLabSourceKey`.

- [ ] Write a failing Vitest contract that requires three variant keys, four ordered sources, the complete job story, and no hero-lab import in the production `Hero.tsx`.
- [ ] Run `npm test -- lib/hero-lab.test.ts` and confirm it fails because the concept module does not exist.
- [ ] Add the typed concept model and install `three`, `@react-three/fiber`, and `@types/three`.
- [ ] Re-run the focused test and confirm it passes.

### Task 2: Accessible comparison shell

**Files:**
- Create: `apps/web/app/(marketing)/hero-lab/page.tsx`
- Create: `apps/web/components/hero-lab/HeroLab.tsx`
- Create: `apps/web/components/hero-lab/HeroLab.module.css`
- Modify: `apps/web/lib/hero-lab.test.ts`

**Interfaces:**
- Consumes the concept model.
- Produces the `HeroLab` comparison UI and `data-hero-lab` browser contract.

- [ ] Extend the failing test to require the route, three accessible variant tabs, four source buttons, the concise comparison explanation fields, and a bounded stage marker.
- [ ] Run the focused test and confirm the missing route/shell assertions fail.
- [ ] Implement the page and responsive shell with keyboard-operable tabs, source selection, upright descriptions, and reduced-motion detection.
- [ ] Re-run the focused test and confirm it passes.

### Task 3: Three interactive WebGL concepts

**Files:**
- Create: `apps/web/components/hero-lab/HeroLabCanvas.tsx`
- Create: `apps/web/components/hero-lab/scenes/ScenePrimitives.tsx`
- Create: `apps/web/components/hero-lab/scenes/StackScene.tsx`
- Create: `apps/web/components/hero-lab/scenes/TopologyScene.tsx`
- Create: `apps/web/components/hero-lab/scenes/BackplaneScene.tsx`
- Modify: `apps/web/lib/hero-lab.test.ts`

**Interfaces:**
- `HeroLabCanvas({ variant, selectedSource, reducedMotion })` renders one scene inside a fixed camera boundary.
- Each scene consumes `selectedSource` and `reducedMotion` and exposes no explanatory text inside WebGL.

- [ ] Extend the test to require one Canvas, three scene components, bounded camera values, a capped DPR, source selection props, and reduced-motion props.
- [ ] Run the focused test and confirm the scene assertions fail.
- [ ] Implement shared platform, machine, rack, GPU, path, and job-token primitives.
- [ ] Implement the stack, topology, and backplane scenes with source emphasis and a meaningful job route.
- [ ] Re-run focused tests, `npx tsc --noEmit`, and targeted ESLint.

### Task 4: Browser comparison verification

**Files:**
- Modify only hero-lab files if QA reveals a defect.

**Interfaces:**
- Consumes the running local Next.js route.
- Produces a user-viewable `/hero-lab` comparison.

- [ ] Verify variant switching and source selection in Chrome.
- [ ] Capture and inspect 1440x900, 1280x800, 1024x768, and 390x844.
- [ ] Confirm no horizontal overflow, scene escape, console errors, unreadable labels, or hidden controls.
- [ ] Run the focused test, typecheck, targeted lint, and production build.
- [ ] Leave `/hero-lab` open as the user-facing comparison page and stop without replacing the production hero.

### Task 5: Refine A and B into synchronized orchestration stories

**Files:**
- Modify: `apps/web/lib/hero-lab.ts`
- Modify: `apps/web/lib/hero-lab.test.ts`
- Modify: `apps/web/components/hero-lab/HeroLab.tsx`
- Modify: `apps/web/components/hero-lab/HeroLabCanvas.tsx`
- Modify: `apps/web/components/hero-lab/HeroLab.module.css`
- Modify: `apps/web/components/hero-lab/scenes/ScenePrimitives.tsx`
- Modify: `apps/web/components/hero-lab/scenes/StackScene.tsx`
- Modify: `apps/web/components/hero-lab/scenes/TopologyScene.tsx`
- Modify: `docs/design-references/zolli-hero-lab/README.md`
- Create: `docs/design-references/zolli-hero-lab/variant-a2-orchestrated-compute-stack.png`
- Create: `docs/design-references/zolli-hero-lab/variant-b2-orchestrated-compute-fabric.png`

**Interfaces:**
- `HERO_LAB_JOB_STEPS` produces six ordered recovery states and `HERO_LAB_JOB_TIMINGS_MS` supplies their presentation timing.
- `HeroLabCanvas({ variant, selectedSource, jobStep, reducedMotion, onSelectSource })` keeps the DOM story rail and active WebGL state synchronized.
- `ControlSpine`, `ControlField`, `CheckpointBeacon`, `FailureSocket`, and `AcceptedMarker` expose readable orchestration states to A2 and B2 without embedding explanatory copy in tilted geometry.

- [x] Write failing tests that require A2/B2 labels, six job states, synchronized `jobStep` props, one substantial control object per scene, and no repeated per-layer control cores.
- [x] Run `npm test -- lib/hero-lab.test.ts` and confirm the new assertions fail for the missing A2/B2 behavior.
- [x] Update the concept model and comparison shell so one timed story state drives both the job rail and the canvas; keep the rail directly selectable and pause autoplay after manual inspection.
- [x] Replace A's passive line with one checkpoint-bearing control spine and replace B's small core with one upright software-control field; show the same submit, checkpoint, loss, resume, and accepted sequence in both.
- [x] Re-run the focused test, `npx tsc --noEmit`, targeted ESLint, and browser QA at desktop, tablet, and mobile widths.
- [x] Preserve Variant C and the production `components/landing/Hero.tsx` unchanged.
