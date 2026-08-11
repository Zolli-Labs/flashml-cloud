# Zolli B2 Production Three.js Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace B2's low-detail primitive scene in `/hero-lab` with a production-quality hybrid Three.js compute fabric using custom optimized GLB assets and programmable recovery storytelling.

**Architecture:** Keep `HeroLabCanvas` and the existing B2 prop contract. A new `b2/` scene owns asset-driven islands, a modeled control-plane chassis, procedural routes, checkpoint/failure/resume effects, camera/lighting, and fallbacks. Pure functions in `lib/hero-fabric.ts` remain the single source of truth for story snapshots, source geometry, quality selection, and asset metadata.

**Tech Stack:** Next.js 16, TypeScript, React 19, Three.js 0.185, React Three Fiber 9, `@react-three/drei`, `@react-three/postprocessing`, GLTFExporter, `@gltf-transform/cli`, Vitest, ESLint, and the installed Three.js MCP inspection server.

## Global Constraints

- Implement B2 only in `/hero-lab`; A2, C, `apps/web/components/landing/Hero.tsx`, and the production homepage remain unchanged.
- Preserve the six exact states: `submitted`, `assigned`, `checkpointed`, `lost`, `resumed`, and `accepted`.
- The checkpoint remains in Zolli's control plane; the everyday node fails; work resumes on rented capacity; only the accepted result turns green.
- Essential labels, controls, CTAs, and state explanations remain semantic DOM. Canvas labels are redundant decoration.
- Use local first-party assets. Do not add an unlicensed marketplace asset or a remote runtime asset.
- The five GLBs plus textures must transfer in no more than 1.8 MB total.
- High quality: at most 150,000 triangles, 120 draw calls, DPR 1.5, and no sustained frame below 50 FPS on the development desktop.
- Balanced quality: at most 60,000 triangles, 70 draw calls, DPR 1, and a 30 FPS floor on representative mid-tier mobile hardware.
- WebGL2 failure renders the poster and accessible DOM; it does not attempt a WebGL1 Canvas.
- Reduced motion uses the Static tier, `frameloop="demand"`, and no looping animation.
- Important Three.js objects use the exact names from the spec so MCP `scene_tree` output is readable.
- Follow strict RED → GREEN TDD for behavior. Visual-only work must be backed by pure scene-state contracts plus browser/MCP verification.
- Preserve all unrelated dirty worktree changes.
- Do not stage, commit, push, merge, delete the worktree, or replace the production hero.

---

### Task 1: Define the production fabric state and quality contracts

**Files:**
- Create: `apps/web/lib/hero-fabric.ts`
- Create: `apps/web/lib/hero-fabric.test.ts`
- Modify: `apps/web/lib/hero-lab.ts`

**Interfaces:**
- Consumes: `HeroLabJobStepKey` and `HeroLabSourceKey` from `lib/hero-lab.ts`.
- Produces: `FabricQualityTier`, `FabricRenderDecision`, `FabricCapabilityInput`, `FabricStorySnapshot`, `FABRIC_SOURCE_LAYOUT`, `FABRIC_ASSET_URLS`, `getFabricStorySnapshot(step)`, and `getFabricRenderDecision(input)`.

- [x] **Step 1: Write a failing table-driven story test**

  Add six literal expected snapshots. Each snapshot asserts `activeSource`, `failedSource`, `resumeSource`, `checkpointVisible`, `failureVisible`, `acceptedVisible`, and route modes for `everyday`, `owned`, `rented`, and `cloud`. The `lost` fixture must keep `checkpointVisible: true`, set `failedSource: "everyday"`, and never set `acceptedVisible`.

- [x] **Step 2: Write failing quality-tier boundary tests**

  Assert these literal outcomes:

  ```ts
  expect(getFabricRenderDecision({ webgl2: true, reducedMotion: false, documentVisible: true, desktop: true, finePointer: true })).toEqual({ mode: "canvas", quality: "high" });
  expect(getFabricRenderDecision({ webgl2: true, reducedMotion: false, documentVisible: true, desktop: false, finePointer: false })).toEqual({ mode: "canvas", quality: "balanced" });
  expect(getFabricRenderDecision({ webgl2: true, reducedMotion: true, documentVisible: true, desktop: true, finePointer: true })).toEqual({ mode: "canvas", quality: "static" });
  expect(getFabricRenderDecision({ webgl2: false, reducedMotion: false, documentVisible: true, desktop: true, finePointer: true })).toEqual({ mode: "poster", reason: "webgl2-unavailable" });
  ```

- [x] **Step 3: Run RED and record the expected failure**

  Run: `cd apps/web && npm test -- lib/hero-fabric.test.ts`

  Expected: FAIL because `hero-fabric.ts` and its exported functions do not exist.

- [x] **Step 4: Implement the minimal pure model**

  Define:

  ```ts
  export type FabricQualityTier = "high" | "balanced" | "static";
  export type FabricRenderDecision =
    | { mode: "canvas"; quality: FabricQualityTier }
    | { mode: "poster"; reason: "webgl2-unavailable" };
  export type FabricRouteMode = "idle" | "active" | "failed" | "verified";

  export interface FabricStorySnapshot {
    step: HeroLabJobStepKey;
    activeSource: HeroLabSourceKey | null;
    failedSource: HeroLabSourceKey | null;
    resumeSource: HeroLabSourceKey | null;
    checkpointVisible: boolean;
    failureVisible: boolean;
    acceptedVisible: boolean;
    routes: Record<HeroLabSourceKey, FabricRouteMode>;
  }
  ```

  Use an explicit immutable record keyed by all six job states. Do not derive one state from another at runtime.

- [x] **Step 5: Run GREEN and mutation-check the failure state**

  Run: `cd apps/web && npm test -- lib/hero-fabric.test.ts`

  Temporarily change `lost.checkpointVisible` to `false`, confirm the test fails, restore it, and confirm the focused test passes.

- [x] **Step 6: Run type checking and record the uncommitted checkpoint**

  Run: `cd apps/web && npx tsc --noEmit`

  Do not stage or commit. Write RED/GREEN evidence and changed files to the task report.

### Task 2: Build and validate the custom GLB asset pipeline

**Files:**
- Modify: `apps/web/package.json`
- Modify: `apps/web/package-lock.json`
- Create: `apps/web/scripts/hero-assets/fabric-asset-contract.mjs`
- Create: `apps/web/scripts/hero-assets/build-fabric-assets.mjs`
- Create: `apps/web/scripts/hero-assets/validate-fabric-assets.mjs`
- Create: `apps/web/scripts/hero-assets/fabric-asset-contract.test.ts`
- Create: `apps/web/public/models/hero/fabric/everyday-machines.glb`
- Create: `apps/web/public/models/hero/fabric/owned-infrastructure.glb`
- Create: `apps/web/public/models/hero/fabric/rented-gpu.glb`
- Create: `apps/web/public/models/hero/fabric/cloud-hpc.glb`
- Create: `apps/web/public/models/hero/fabric/control-plane.glb`
- Create: `apps/web/public/models/hero/fabric/asset-manifest.json`
- Create: `apps/web/public/models/hero/fabric/LICENSES.md`

**Interfaces:**
- Consumes: asset URLs and source keys from Task 1.
- Produces: deterministic first-party GLBs; `REQUIRED_FABRIC_ASSETS`; `validateFabricManifest(manifest, stats)`; `npm run hero:assets`; and `npm run hero:assets:validate`.

- [x] **Step 1: Write a failing real manifest validator test**

  Import `validateFabricManifest` and pass literal in-memory file statistics. Assert that the complete five-asset fixture succeeds and fixtures with a missing `Rented_GPU_A` mesh, total bytes above `1_800_000`, or a missing license file return specific failures.

- [x] **Step 2: Run RED**

  Run: `cd apps/web && npm test -- scripts/hero-assets/fabric-asset-contract.test.ts`

  Expected: FAIL because the contract module does not exist.

- [x] **Step 3: Install only the approved asset/runtime dependencies**

  Run:

  ```bash
  cd apps/web
  npm install @react-three/drei @react-three/postprocessing postprocessing
  npm install --save-dev @gltf-transform/cli
  ```

  Add scripts:

  ```json
  "hero:assets": "node scripts/hero-assets/build-fabric-assets.mjs",
  "hero:assets:validate": "node scripts/hero-assets/validate-fabric-assets.mjs"
  ```

- [x] **Step 4: Implement the asset contract and deterministic generator**

  Use Three.js `RoundedBoxGeometry`, `BoxGeometry`, `CylinderGeometry`, `ExtrudeGeometry`, named PBR materials, and `GLTFExporter`. The script accepts `--output <directory>` for tests and defaults to `public/models/hero/fabric/`.

  Required stable mesh names include:

  ```text
  Everyday_Laptop
  Everyday_Workstation
  Everyday_Tower
  Everyday_HomeServer
  Owned_Workstation
  Owned_Rack
  Rented_GPU_A
  Rented_GPU_B
  Cloud_Rack_A
  FabricControlPlane_Chassis
  ```

  Repeated rack bays, keyboard keys, LEDs, and fans reuse geometries and materials. Add bevelled silhouettes, visible screen recesses, vents, rack bays, and GPU fans; do not export plain placeholder boxes.

- [x] **Step 5: Generate, optimize, and validate the assets**

  Run:

  ```bash
  cd apps/web
  npm run hero:assets
  npm run hero:assets:validate
  ```

  Expected: five GLBs and `asset-manifest.json` are created; validation exits 0; combined bytes remain at or below 1,800,000.

- [x] **Step 6: Run GREEN, type checking, and targeted lint**

  Run:

  ```bash
  cd apps/web
  npm test -- scripts/hero-assets/fabric-asset-contract.test.ts
  npx tsc --noEmit
  npx eslint scripts/hero-assets
  ```

  Do not stage or commit. Record dependency versions, asset sizes, triangle counts, RED/GREEN evidence, and generated files in the task report.

### Task 3: Replace primitive source devices with asset-driven interactive islands

**Files:**
- Create: `apps/web/components/hero-lab/b2/FabricAsset.tsx`
- Create: `apps/web/components/hero-lab/b2/FabricIsland.tsx`
- Create: `apps/web/components/hero-lab/b2/FabricHeroScene.tsx`
- Create: `apps/web/components/hero-lab/b2/FabricFallback.tsx`
- Create: `apps/web/components/hero-lab/b2/FabricFallback.test.tsx`
- Modify: `apps/web/components/hero-lab/scenes/TopologyScene.tsx`

**Interfaces:**
- Consumes: `FABRIC_ASSET_URLS`, `FABRIC_SOURCE_LAYOUT`, `FabricQualityTier`, and the existing `SceneConceptProps` contract.
- Produces: `FabricHeroScene(props)`, `FabricAsset({ source, quality })`, `FabricIsland({ source, selected, ... })`, and a semantic `FabricFallback`.

- [x] **Step 1: Write the failing fallback behavior test**

  Render the real `FabricFallback` with `reason="loading"` and `reason="webgl"`. Assert the poster has accessible alt text, the visible status is respectively `Preparing the compute fabric…` and `Interactive 3D is unavailable`, and the supplied children containing source controls remain rendered.

- [x] **Step 2: Run RED**

  Run: `cd apps/web && npm test -- components/hero-lab/b2/FabricFallback.test.tsx`

  Expected: FAIL because the B2 components do not exist.

- [x] **Step 3: Implement the asset loader and poster fallback**

  Use `useGLTF` with local URLs, clone loaded scenes before per-island material changes, and dispose only owned clones. The fallback uses `/images/hero/fabric-poster.webp`; create a compressed poster from the approved B2 reference without loading the full design PNG at runtime.

- [x] **Step 4: Implement named interactive islands**

  Each island has a bevelled platform, redundant decal label, asset cluster, generous invisible hit target, and stable group name: `EverydayIsland`, `OwnedIsland`, `RentedIsland`, or `CloudIsland`. Clicking calls the existing `onSelectSource`; selection uses damped lift and restrained orange emissive response. Balanced mode loads the same optimized assets but disables nonessential micro-animation.

- [x] **Step 5: Compose B2 and replace only the topology implementation**

  `TopologyScene` becomes a thin adapter that renders `FabricHeroScene` with the unchanged `SceneConceptProps`. Keep A2 and C imports and code untouched.

- [x] **Step 6: Run GREEN and integration checks**

  Run:

  ```bash
  cd apps/web
  npm test -- components/hero-lab/b2/FabricFallback.test.tsx lib/hero-fabric.test.ts lib/hero-lab.test.ts
  npx tsc --noEmit
  npx eslint components/hero-lab/b2 components/hero-lab/scenes/TopologyScene.tsx
  ```

  Start the local app, select B2, and confirm all four GLBs render and each 3D island selects the corresponding DOM source. Record screenshots and test evidence; do not stage or commit.

### Task 4: Build the modeled control plane and synchronized recovery routes

**Files:**
- Create: `apps/web/components/hero-lab/b2/FabricControlPlane.tsx`
- Create: `apps/web/components/hero-lab/b2/FabricExecutionRoutes.tsx`
- Create: `apps/web/components/hero-lab/b2/FabricStoryObjects.tsx`
- Modify: `apps/web/components/hero-lab/b2/FabricHeroScene.tsx`
- Modify: `apps/web/lib/hero-fabric.ts`
- Modify: `apps/web/lib/hero-fabric.test.ts`

**Interfaces:**
- Consumes: `FabricStorySnapshot` from Task 1 and the control-plane GLB from Task 2.
- Produces: `FABRIC_ROUTE_POINTS`, `getFabricRouteSegments(snapshot)`, `FabricControlPlane`, `FabricExecutionRoutes`, `CheckpointBeacon`, `FailureBranch`, and `AcceptedMarker`.

- [x] **Step 1: Write failing route-contract tests**

  Assert every source route begins at its island socket and ends at a unique control-plane socket. Assert `lost` produces one failed everyday branch and no verified exit; `resumed` produces an active rented route while the checkpoint remains visible; `accepted` produces exactly one verified exit segment.

- [x] **Step 2: Run RED**

  Run: `cd apps/web && npm test -- lib/hero-fabric.test.ts`

  Expected: FAIL because route points and `getFabricRouteSegments` do not exist.

- [x] **Step 3: Implement the modeled control plane**

  Load `control-plane.glb` as `FabricControlPlane`. Add a procedural emissive grid, four named sockets, instanced job cells, and the exact named `CheckpointBeacon`. The checkpoint stays inside the control plane for `checkpointed`, `lost`, `resumed`, and `accepted`.

- [x] **Step 4: Implement execution routes from declarative snapshots**

  Use `CatmullRomCurve3` plus reusable tube geometry for route bodies. Use a small generated dash texture or packet instances for direction. Route colors and shapes follow `FabricRouteMode`: graphite idle, orange active, red dashed failed, verified green exit. Name the break `FailureBranch` and the final marker `AcceptedMarker`.

- [x] **Step 5: Implement all six visual states**

  Drive visibility and route material targets entirely from `getFabricStorySnapshot(jobStep)`. Never infer semantic state from animation progress. Reduced motion and paused stories jump to the complete requested snapshot without looping.

- [x] **Step 6: Run GREEN, type checking, and targeted lint**

  Run:

  ```bash
  cd apps/web
  npm test -- lib/hero-fabric.test.ts lib/hero-lab.test.ts
  npx tsc --noEmit
  npx eslint components/hero-lab/b2 lib/hero-fabric.ts lib/hero-fabric.test.ts
  ```

  In the browser, manually select all six story steps and confirm the DOM label and scene state agree. Record RED/GREEN and visual evidence; do not stage or commit.

### Task 5: Add camera choreography, rendering tiers, lighting, and failure containment

**Files:**
- Create: `apps/web/components/hero-lab/b2/FabricCameraRig.tsx`
- Create: `apps/web/components/hero-lab/b2/FabricLighting.tsx`
- Create: `apps/web/components/hero-lab/b2/FabricCanvasBoundary.tsx`
- Create: `apps/web/components/hero-lab/b2/FabricCanvasBoundary.test.tsx`
- Modify: `apps/web/components/hero-lab/b2/FabricHeroScene.tsx`
- Modify: `apps/web/components/hero-lab/HeroLabCanvas.tsx`
- Modify: `apps/web/components/hero-lab/HeroLab.tsx`

**Interfaces:**
- Consumes: `FabricQualityTier`, `getFabricRenderDecision`, `FabricFallback`, and the existing `LandingMotionProvider` capability signals.
- Produces: named camera framing, High/Balanced/Static renderer behavior, selective bloom, shadow policy, WebGL context-loss fallback, and pause-on-manual-selection.

- [x] **Step 1: Write a failing real error-boundary test**

  Render `FabricCanvasBoundary` with a child that throws. Assert the fallback poster and supplied DOM controls remain visible. Render it again with `failure="webgl"` and assert the Canvas child is absent while the same controls remain available.

- [x] **Step 2: Run RED**

  Run: `cd apps/web && npm test -- components/hero-lab/b2/FabricCanvasBoundary.test.tsx`

  Expected: FAIL because the boundary does not exist.

- [x] **Step 3: Implement authored camera and entrance motion**

  Use one perspective camera without orbit controls. Cap pointer parallax at 2.5 degrees, enable it only for High quality with a fine pointer, keep all four islands visible, and finish the entrance within 1.8 seconds. Static mode sets the final camera and object positions immediately.

- [x] **Step 4: Implement quality-specific lighting and effects**

  High uses a 1024 shadow map, DPR at most 1.5, selective bloom, and route packets. Balanced uses DPR 1, at most a 512 shadow map, fewer packets, and no parallax. Static uses no dynamic shadows or continuous animation. Bloom applies only to emissive orange/green objects and must remain optional.

- [x] **Step 5: Implement failure containment and lifecycle suspension**

  Wrap the hero-lab experience in the existing `LandingMotionProvider` instead of duplicating media-query observers. Detect WebGL2 before constructing the Canvas. On asset error or `webglcontextlost`, set `failure="webgl"` and render `FabricFallback` with controls intact without automatically remounting the Canvas. Stop the animation loop when the document is hidden, B2 is not selected, the story is paused, or reduced motion is active.

- [x] **Step 6: Make manual inspection authoritative**

  Selecting a source or a story step pauses autoplay. The Play button resumes from the current step. DOM buttons remain keyboard accessible and are the semantic source of truth.

- [x] **Step 7: Run GREEN and responsive browser checks**

  Run:

  ```bash
  cd apps/web
  npm test -- components/hero-lab/b2/FabricCanvasBoundary.test.tsx components/hero-lab/b2/FabricFallback.test.tsx lib/hero-fabric.test.ts lib/hero-lab.test.ts
  npx tsc --noEmit
  npx eslint components/hero-lab/b2 components/hero-lab/HeroLab.tsx components/hero-lab/HeroLabCanvas.tsx
  ```

  Verify desktop, tablet, mobile, reduced motion, and WebGL-failure poster behavior. Record evidence; do not stage or commit.

### Task 6: Complete MCP inspection, performance hardening, and delivery verification

**Files:**
- Modify only when a measured acceptance failure requires it: `apps/web/components/hero-lab/b2/*`, `apps/web/lib/hero-fabric.ts`, and their focused tests.
- Modify: `docs/design-references/zolli-hero-lab/README.md`
- Modify: `docs/superpowers/specs/2026-08-10-zolli-b2-production-threejs-design.md`

**Interfaces:**
- Consumes: the completed B2 scene and installed Three.js MCP.
- Produces: named inspectable scene, performance evidence, responsive screenshots, final verification report, and a locally running `/hero-lab` comparison.

- [x] **Step 1: Run the complete automated verification baseline**

  Run:

  ```bash
  cd apps/web
  npm test
  npx tsc --noEmit
  npx eslint components/hero-lab lib/hero-fabric.ts lib/hero-fabric.test.ts scripts/hero-assets
  npm run hero:assets:validate
  ```

  Any failure caused by B2 must receive a failing regression test before its implementation fix.

- [x] **Step 2: Inspect the named scene through the Three.js MCP**

  Point the MCP proxy to the isolated local port, open `/hero-lab`, select B2, and run `scene_tree`. Confirm the output includes `FabricHeroScene`, `FabricControlPlane`, all four named islands, `CheckpointBeacon`, `FailureBranch`, and `AcceptedMarker`.

- [x] **Step 3: Verify renderer and performance budgets**

  Run MCP `renderer_settings` and `performance_snapshot` for High and Balanced quality. Confirm shadows, tone mapping, DPR, draw calls, triangles, texture memory, and FPS stay within Global Constraints. Optimize only measured violations.

- [x] **Step 4: Capture every required visual state**

  At 1440×900, 1280×800, 1024×768, and 390×844, capture submitted, checkpointed, lost, resumed, and accepted. Confirm source selection, keyboard control, reduced motion, loading poster, and WebGL failure fallback.

- [x] **Step 5: Run the environment-backed production build**

  Run:

  ```bash
  set -a
  source /Users/phongcao/Work/Zolli-Labs/flashml-cloud/.env.dev
  set +a
  cd apps/web
  npm run build
  ```

  Expected: build exits 0 and `/hero-lab` is generated without changing the production landing hero.

- [x] **Step 6: Perform final scope and integrity checks**

  Run `git diff --check`, verify `apps/web/components/landing/Hero.tsx` received no task-plan change, and preserve unrelated dirty files. Update the design-reference README with asset generation, MCP inspection, and local-view instructions.

- [x] **Step 7: Leave the comparison running locally**

  Start the documented development server on an available port, verify `/hero-lab` returns HTTP 200, and report the URL. Do not stage, commit, push, merge, or replace the production hero.

**Completion evidence (2026-08-10):** 42 test files / 483 tests passed; TypeScript, targeted ESLint, five-asset validation (131,360 bytes), and `git diff --check` passed. The environment-backed production build generated all 27 static pages, including `/hero-lab`. Independent final review found no Critical or Important defects after the route-direction correction. Live Three.js inspection confirmed the named B2 scene, fixed packets in Static mode, moving packets during playback, and a clean post-clear console. The comparison remains available at `http://127.0.0.1:3018/hero-lab`.
