# Zolli B2 Production Hero Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the CSS infrastructure stack in the real Zolli landing hero with the approved B2 Three.js compute fabric and remove the temporary hero comparison surface.

**Architecture:** Extract the B2 renderer and its six-state runtime from the lab namespace into production landing modules, then wrap it in a responsive, accessible hero control shell. After the landing is wired, delete A2/C, the lab route, old stack code/styles, and lab-only middleware/test contracts.

**Tech Stack:** Next.js 16 App Router, React 19, TypeScript, CSS Modules, Tailwind v4, React Three Fiber, Three.js, Motion/GSAP through the existing landing provider, Vitest.

## Global Constraints

- Follow `docs/superpowers/specs/2026-08-10-zolli-b2-production-hero-promotion-design.md` exactly.
- Preserve the headline **Compute that finishes the job.**, the current definition, and Open console before Talk to Zolli.
- Ship only the B2 compute fabric; no concept tabs, lab labels, analysis cards, A2, or C.
- Production code must contain no import from `components/hero-lab` or `lib/hero-lab`.
- Preserve the verified B2 GLBs, render tiers, route semantics, WebGL fallback, and Three.js inspection bridge.
- Paused, reduced-motion, and hidden-document states use Static quality; active visible motion uses the capability-selected High or Balanced quality.
- Keep source and story controls keyboard accessible and visible outside the canvas.
- No horizontal overflow at 1440×900, 1024×768, or 390×844.
- Add no dependencies.
- Preserve unrelated dirty files. Do not stage, commit, push, merge, reset, clean, or remove the worktree.

---

### Task 1: Extract the approved fabric into a production namespace

**Files:**
- Create: `apps/web/lib/hero-story.ts`
- Create: `apps/web/lib/hero-story.test.ts`
- Modify: `apps/web/lib/hero-fabric.ts`
- Modify: `apps/web/lib/hero-fabric.test.ts`
- Create/move: `apps/web/components/landing/hero-fabric/FabricAsset.tsx`
- Create/move: `apps/web/components/landing/hero-fabric/FabricAsset.test.ts`
- Create/move: `apps/web/components/landing/hero-fabric/FabricCameraRig.tsx`
- Create/move: `apps/web/components/landing/hero-fabric/FabricCanvasBoundary.tsx`
- Create/move: `apps/web/components/landing/hero-fabric/FabricCanvasBoundary.test.tsx`
- Create/move: `apps/web/components/landing/hero-fabric/FabricControlPlane.tsx`
- Create/move: `apps/web/components/landing/hero-fabric/FabricExecutionRoutes.tsx`
- Create/move: `apps/web/components/landing/hero-fabric/FabricFallback.tsx`
- Create/move: `apps/web/components/landing/hero-fabric/FabricFallback.test.tsx`
- Create/move: `apps/web/components/landing/hero-fabric/FabricHeroScene.tsx`
- Create/move: `apps/web/components/landing/hero-fabric/FabricInspectionBridge.tsx`
- Create/move: `apps/web/components/landing/hero-fabric/FabricIsland.tsx`
- Create/move: `apps/web/components/landing/hero-fabric/FabricLighting.tsx`
- Create/move: `apps/web/components/landing/hero-fabric/FabricStoryObjects.tsx`
- Create: `apps/web/components/landing/hero-fabric/HeroFabricCanvas.tsx`

**Interfaces:**
- Produces `HeroSourceKey`, `HeroJobStepKey`, `HeroRuntimeState`, `HeroRuntimeEvent`, `HERO_SOURCES`, `HERO_JOB_STEPS`, `createHeroRuntimeState`, `reduceHeroRuntime`, `getHeroSource`, `getHeroJobStep`, and `getNextHeroJobStep` from `lib/hero-story.ts`.
- Produces `HeroFabricCanvas` with props for source, story step, render decision, runtime quality inputs, entrance/failure state, and source selection.
- Consumes the existing `hero-fabric.ts` pure render contracts and the five `/models/hero-fabric/*.glb` assets.

- [x] **Step 1: Write the failing production-story contract**

  Add tests that import the production names above, assert the source order `cloud, rented, owned, everyday`, assert the six exact job states, assert source/step selection pauses playback, assert Play preserves the selected step, and assert failure/entrance completion latch once. Add a source-boundary assertion that production fabric files do not import `hero-lab`.

- [x] **Step 2: Run RED**

  Run:

  ```bash
  cd apps/web
  npm test -- --run lib/hero-story.test.ts lib/hero-fabric.test.ts
  ```

  Expected: fail because `lib/hero-story.ts` and production fabric paths do not exist.

- [x] **Step 3: Create the production story model**

  Copy only source and job-step data from the lab model. Remove variant types and variant events. Use this initial state:

  ```ts
  {
    selectedSource: "everyday",
    jobStep: "accepted",
    storyPlaying: false,
    fabricFailed: false,
    fabricEntranceComplete: false,
  }
  ```

  `initialize-motion` sets `jobStep: "submitted"` and `storyPlaying: true`. Source and explicit job-step selection set `storyPlaying: false`. Reduced-motion toggle is a no-op.

- [x] **Step 4: Move the B2 renderer and rename its types**

  Move only `components/hero-lab/b2/*` into `components/landing/hero-fabric/*`. Replace `HeroLabSourceKey` with `HeroSourceKey`, `HeroLabJobStepKey` with `HeroJobStepKey`, and `getHeroLabSource` with `getHeroSource`. Update `lib/hero-fabric.ts` to import production types. Preserve scene names, material batching, route directions, quality tiers, inspection ownership, and asset URLs byte-for-byte unless a rename is required.

- [x] **Step 5: Create the production-only Canvas boundary**

  Extract the B2 `FabricCanvas` path from `HeroLabCanvas.tsx` into `HeroFabricCanvas.tsx`. It always renders the fabric—there is no `variant` prop and no A2/C import. Keep WebGL loading/failure posters, context-loss handling, renderer configuration, the shared effective quality, inspection registration, and `FabricRuntime` unchanged.

- [x] **Step 6: Run GREEN and integrity checks**

  Run:

  ```bash
  cd apps/web
  npm test -- --run lib/hero-story.test.ts lib/hero-fabric.test.ts components/landing/hero-fabric/FabricAsset.test.ts components/landing/hero-fabric/FabricCanvasBoundary.test.tsx components/landing/hero-fabric/FabricFallback.test.tsx
  npx tsc --noEmit
  npx eslint lib/hero-story.ts lib/hero-story.test.ts lib/hero-fabric.ts lib/hero-fabric.test.ts components/landing/hero-fabric
  npm run hero:assets:validate
  git diff --check
  ```

  Expected: focused tests pass, TypeScript/lint/diff checks exit 0, and five GLBs validate at 131,360 bytes total.

### Task 2: Integrate the compute fabric into the real landing hero

**Files:**
- Create: `apps/web/components/landing/HeroComputeFabric.tsx`
- Create: `apps/web/components/landing/HeroComputeFabric.module.css`
- Modify: `apps/web/components/landing/Hero.tsx`
- Modify: `apps/web/lib/landing-infrastructure-story.test.ts`
- Modify: `apps/web/lib/landing-cinematic.test.ts`

**Interfaces:**
- Consumes `HeroFabricCanvas`, `HeroRuntimeState`, `HERO_SOURCES`, `HERO_JOB_STEPS`, and `getFabricRenderDecision`.
- Produces a client `HeroComputeFabric` used once by the server-rendered `Hero` component.

- [x] **Step 1: Write the failing production-hero contract**

  Add assertions that `Hero.tsx` imports and renders `HeroComputeFabric`, no longer imports `HeroInfrastructureStack`, preserves the exact headline/definition/CTA order, and exposes production source/story controls. Assert that the production shell contains no concept tabs, `B2`, `HeroLab`, `CONCEPT`, `STRENGTH`, or `WEAKNESS`.

- [x] **Step 2: Run RED**

  Run:

  ```bash
  cd apps/web
  npm test -- --run lib/landing-infrastructure-story.test.ts lib/landing-cinematic.test.ts lib/hero-story.test.ts
  ```

  Expected: fail because the landing still renders `HeroInfrastructureStack` and the production shell does not exist.

- [x] **Step 3: Build `HeroComputeFabric`**

  Make it a client component and dynamically import `HeroFabricCanvas` with `ssr: false`. Reuse `useLandingMotion` for reduced/desktop/fine-pointer/visibility capability. Detect WebGL2 after mount. Autostart the story once when motion is allowed. Keep source buttons, active source detail, Play/Pause, and the six-step rail as real DOM controls with `aria-pressed`, `aria-current="step"`, and polite active-state announcements.

- [x] **Step 4: Create production styling**

  Adapt the approved lab scene styling without the page header, comparison tabs, or analysis grid. Desktop uses a bounded canvas plus a compact right source panel. Tablet stacks the hero copy above the full-width scene. Mobile keeps a minimum 22rem canvas, two-column source buttons, two-column job rail, a visible active story label, 40px minimum interactive targets, no page overflow, and no decorative overlay intercepting pointer events.

- [x] **Step 5: Replace the old hero visual**

  In `Hero.tsx`, keep the headline, definition, and CTAs unchanged. Replace `<HeroInfrastructureStack />` with `<HeroComputeFabric />`. Adjust only the hero grid, spacing, and minimum-height classes needed to give the 3D fabric a readable desktop stage and clean tablet/mobile stacking.

- [x] **Step 6: Run GREEN**

  Run:

  ```bash
  cd apps/web
  npm test -- --run lib/landing-infrastructure-story.test.ts lib/landing-cinematic.test.ts lib/hero-story.test.ts lib/hero-fabric.test.ts
  npx tsc --noEmit
  npx eslint components/landing/Hero.tsx components/landing/HeroComputeFabric.tsx lib/hero-story.ts lib/hero-fabric.ts
  git diff --check
  ```

  Expected: all focused contracts pass and no production file imports the lab namespace.

### Task 3: Delete the experiment surface and verify production delivery

**Files:**
- Delete: `apps/web/app/(marketing)/hero-lab/page.tsx`
- Delete: `apps/web/components/hero-lab/`
- Delete: `apps/web/components/landing/HeroInfrastructureStack.tsx`
- Delete: `apps/web/lib/hero-lab.ts`
- Delete: `apps/web/lib/hero-lab.test.ts`
- Modify: `apps/web/app/globals.css`
- Modify: `apps/web/middleware.ts`
- Modify: `apps/web/middleware.test.ts`
- Modify: `docs/design-references/zolli-hero-lab/README.md`
- Modify: `docs/superpowers/plans/2026-08-10-zolli-b2-production-hero-promotion.md`
- Modify: `../PROGRESS.md`

**Interfaces:**
- Consumes the production hero from Task 2.
- Produces a landing root `/` with no `/hero-lab` route or experiment-only source tree.

- [x] **Step 1: Write the failing removal contract**

  Assert that `app/(marketing)/hero-lab/page.tsx`, `components/hero-lab`, `components/landing/HeroInfrastructureStack.tsx`, and `lib/hero-lab.ts` do not exist; `/hero-lab` is absent from `PUBLIC_PATHS`; the production source tree contains no imports matching `@/components/hero-lab` or `@/lib/hero-lab`; and `globals.css` contains no `hero-infra-` selector.

- [x] **Step 2: Run RED**

  Run:

  ```bash
  cd apps/web
  npm test -- --run lib/hero-story.test.ts middleware.test.ts lib/landing-infrastructure-story.test.ts lib/landing-cinematic.test.ts
  ```

  Expected: fail because the lab route/tree, old stack, middleware entry, and old global styles still exist.

- [x] **Step 3: Remove obsolete experiment code and styles**

  Delete the listed route/files and all A2/C scene code. Remove only the contiguous `hero-infra-*` rules and `hero-infra-task-resolve` keyframes from `globals.css`; preserve all neighboring landing styles. Remove `/hero-lab` from middleware and update its public-route expectation.

- [x] **Step 4: Update durable documentation**

  Mark this plan's checkboxes as complete only after evidence exists. Update the design-reference README to say B2 was promoted to production and give the local `/` command. Add one newest-first `PROGRESS.md` entry with exact verification counts, build pages, browser sizes, gotchas, and the next useful action.

- [x] **Step 5: Run the complete automated gate**

  Run:

  ```bash
  cd apps/web
  npm test
  npx tsc --noEmit
  npx eslint components/landing/Hero.tsx components/landing/HeroComputeFabric.tsx components/landing/hero-fabric lib/hero-story.ts lib/hero-story.test.ts lib/hero-fabric.ts lib/hero-fabric.test.ts middleware.ts middleware.test.ts
  npm run hero:assets:validate
  git diff --check
  ```

- [x] **Step 6: Run the environment-backed production build**

  Run:

  ```bash
  set -a
  source /Users/phongcao/Work/Zolli-Labs/flashml-cloud/.env.dev
  set +a
  cd apps/web
  npm run build
  ```

  Expected: `/` is generated, `/hero-lab` is absent, and all other existing marketing/console routes remain.

- [x] **Step 7: Run live browser and Three.js QA**

  Run the environment-backed dev server on `127.0.0.1:3018`. At 1440×900, 1024×768, and 390×844, inspect `/` for scene readability, source and step selection, Play/Pause, CTA order, no horizontal overflow, no loading/fallback regression, and no post-clear console errors. Through the Three.js MCP, confirm `FabricHeroScene`, `ZolliControlPlane`, EverydayIsland, OwnedIsland, RentedIsland, CloudIsland, CheckpointBeacon, and AcceptedMarker. Save final screenshots under `apps/web/.artifacts/hero-production/`.

- [x] **Step 8: Leave production preview running**

  Verify `http://127.0.0.1:3018/` returns HTTP 200 and report that URL. Do not stage or commit.
