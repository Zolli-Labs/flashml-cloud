# Zolli Compute Fabric Clarity and Focus Design

**Date:** 2026-08-10

**Status:** Approved direction — Option A, cinematic source focus

## Goal

Improve the production landing hero so its Three.js compute fabric fills the available stage, renders sharply, and gives every source a recognizable infrastructure-specific silhouette. The hero must still explain one Zolli control plane coordinating fragmented compute and recovering work across those sources.

This is a targeted refinement of the existing production hero. It does not replace the product story, landing-page copy, CTA hierarchy, source controls, six-step recovery rail, or the established warm technical visual system.

## User problem

The current production scene has three visible failures:

1. The canvas is large, but the topology occupies only a small central area, leaving substantial unused space.
2. Device edges and small details appear soft because the camera is distant and High-tier post-processing renders below canvas resolution with broad bloom.
3. The Everyday laptop is recognizable, but Owned Infrastructure, Rented GPU, and Cloud / HPC collapse into similar dark rectangular forms at landing-page scale.

The redesign succeeds only when a visitor can identify the four source categories from their silhouettes before reading the detail panel.

## Locked approach

Use **cinematic source focus**:

- The initial state is a substantially larger overview that keeps the control plane and all four sources visible.
- Selecting a source pauses autoplay, emphasizes that island, and smoothly reframes the camera toward it without hiding the other sources or the control plane.
- Selecting a recovery-story step or pressing Play returns the camera to the overview because the recovery story spans multiple machines.
- Reduced-motion, hidden-document, paused Static-tier, and coarse mobile paths snap to complete stable poses instead of running camera or object interpolation.

This is preferred over a permanent global zoom because users need both a system overview and a readable close inspection. It is preferred over a second magnified viewport because a duplicate scene would make the hero denser and increase rendering cost.

## Product and interaction contract

### Preserved production content

Keep unchanged:

- eyebrow: `FAULT-TOLERANT DISTRIBUTED COMPUTE`
- headline: `Compute that finishes the job.`
- exact product definition below the headline
- primary CTA: `Open console`
- secondary CTA: `Talk to Zolli`
- source labels and their existing descriptions
- story sequence: submitted, assigned, checkpointed, lost, resumed, accepted

There is no comparison UI, lab terminology, free-orbit camera, 3D editor control, or separate detail modal.

### Overview state

The first stable frame must show:

- the complete control plane;
- all four source platforms and their primary device silhouettes;
- the active execution route and current story object;
- enough separation that no source reads as part of another source.

At 1440×900, the projected topology, excluding the DOM detail panel, must occupy between 72% and 92% of the canvas width and between 58% and 86% of its height. It must retain at least a 20-pixel visual safe area on every canvas edge.

At 1024×768 and 390×844, the topology must occupy at least 76% of the usable canvas width without clipping, page-level horizontal overflow, or overlap with the DOM source controls.

### Source-focus state

Selecting a source through either its DOM button or its canvas island must:

1. synchronously pause autoplay through the existing manual-interaction path;
2. update the selected source and accessible detail panel;
3. set that source as the camera focus;
4. scale the selected asset cluster and platform to 1.12–1.16 of their overview size;
5. lift the selected island by 0.28–0.34 scene units;
6. shift the camera target 30–40% of the distance from the overview target toward that island;
7. reduce camera distance by 10–15%;
8. keep the control plane and at least part of every other source platform in frame.

The transition lasts 480–620 milliseconds with critically damped motion and no elastic overshoot. The selected source must never be clipped by the canvas or obscured by the DOM panel.

Selecting another source moves directly between focus poses. Selecting a story step or pressing Play clears the camera focus and returns to the overview. Pausing through the Play/Pause button does not itself create a source focus.

### Responsive motion

- **Desktop, at least 1024 CSS pixels with a fine pointer:** full focus reframe plus the existing capped pointer parallax.
- **Tablet, 560–1023 CSS pixels:** the same target shift with at most 8% camera-distance reduction and no increase in parallax.
- **Mobile, below 560 CSS pixels:** use a front-biased overview; source selection scales and lifts the chosen island but does not translate the camera laterally.
- **Reduced motion or Static quality:** apply the final overview or focus pose in the first rendered frame; no entrance dolly, parallax, bloom animation, or damped settling.

## Source-specific asset design

Continue using the five deterministic first-party GLBs under `apps/web/public/models/hero/fabric/`. Their generator remains the editable source of truth. Replace weak assemblies rather than adding downloaded marketplace models or building detailed geometry inside React.

All visible device faces must share a camera-facing orientation, use bevelled silhouette edges, and preserve plausible relative scale. Dark gaps may use near-black, but exterior surfaces must be bright enough to separate from the background under the production lighting.

### Everyday Machines

The cluster contains four deliberately familiar objects:

- an open laptop with visible keyboard deck and screen bezel;
- a desktop monitor on a stand;
- a separate workstation tower with a front fan or vent field;
- a compact home server with stacked status lights.

This asset may reuse the current recognizable laptop, but the monitor, tower, and home server need enough spacing and tonal separation to read as different devices. The cluster's horizontal span remains greater than 2.7 times its average device depth.

### Owned Infrastructure

The cluster contains:

- one substantial workstation tower with handle, vent field, and two visible internal GPU bays or side-panel recesses;
- one short 10U–12U private rack with at least six clearly separated horizontal server trays, vertical rack rails, and status lights.

The workstation and rack must have visibly different width and height. The rack is shorter and less dense than the Cloud / HPC bank, so the two categories cannot be confused.

### Rented GPU

Replace the current upright boxes with two low, horizontal 2U–4U GPU server chassis on a shared provider sled. Each chassis must show:

- a width-to-height ratio of at least 2.2:1;
- three or four circular front fan modules or fan recesses;
- a rear or top power/interconnect rail;
- a narrow provider-colored identification plate.

The silhouette must read as rackmount GPU capacity, not as a desktop tower, laptop, or miniature cloud rack.

### Cloud / HPC

Use a bank of three tall rack cabinets. Each rack must contain:

- at least eight visible horizontal compute bays;
- two vertical rails or edge frames;
- restrained per-bay status lights;
- a cross-rack topology spine or fabric bus that visibly joins the bank.

The complete bank must be at least 1.35 times as tall as the Owned rack and at least 1.25 times as wide as a single Owned rack. It should read as external institutional capacity rather than another machine owned by the visitor.

### Zolli control plane

Increase the control-plane visual mass by 10–15% while preserving its upright frame, routing grid, checkpoint socket, and semantic role. It remains the central software-coordination anchor, not a fifth compute source. Orange stays reserved for Zolli orchestration, active paths, and selection.

## Rendering clarity

### Camera and composition

Move framing calculations into pure functions in `lib/hero-fabric.ts`. The camera rig consumes an overview or source-focus pose; it does not own product-state interpretation.

The implementation will tune the final numeric camera position against the required projected-occupancy bounds rather than preserving the current distant values. Desktop FOV must remain between 33 and 37 degrees, tablet between 38 and 42 degrees, and mobile between 42 and 46 degrees. Camera near and far planes must preserve depth precision around the compact scene.

The scene root remains centered around the control plane. Asset positions may move inward by no more than 12% if camera changes alone cannot meet the framing contract without clipping.

### Resolution and post-processing

- High quality uses a device-pixel-ratio range of `[1.25, 2]`, capped at 2.
- Balanced quality remains DPR 1 to protect mobile and lower-capability performance.
- Static quality remains DPR 1 with demand rendering.
- High-tier post-processing renders at full canvas resolution (`resolutionScale=1`).
- Selective bloom intensity must be between 0.22 and 0.30, with luminance smoothing no greater than 0.28.
- Only orchestration routes, checkpoint, accepted marker, and selected control-plane cells participate in bloom. Device bodies and decals do not.
- Balanced and Static tiers render without EffectComposer bloom.
- Depth-of-field blur, full-scene blur, chromatic aberration, and motion blur remain prohibited.

Antialiasing and tone mapping must preserve hard silhouette edges. If full-resolution bloom causes the High-tier performance budget to fail, reduce bloom samples or remove bloom before reducing canvas resolution.

### Materials and lighting

Use the existing small shared PBR material system, refined into distinct tonal families:

- graphite metal for racks and server chassis;
- darker polymer for laptop and workstation shells;
- smoked glass only for screens and rack doors;
- muted cool screen surfaces;
- restrained orange emissive accents;
- verified green only for accepted output.

Increase separation between lit faces and background through a warm key, cool fill, and narrow rim light from one consistent direction. Exterior graphite must not resolve below a contrast ratio of 1.25:1 against its immediate canvas background in the accepted visual captures. Dynamic shadows remain limited to the established single-light quality policy.

## Architecture and state flow

### Pure contracts

Extend `lib/hero-fabric.ts` with pure, deterministic contracts for:

- responsive overview camera pose;
- responsive source-focus camera pose;
- focus transition target and snap behavior;
- selected-island scale and lift;
- source-specific silhouette requirements used by asset validation.

The functions accept viewport width, quality tier, motion capability, and focused source. They return data only and must not import React or Three.js runtime state.

### Production controller

`HeroComputeFabric.tsx` owns `focusedSource: HeroSourceKey | null` alongside the existing selected source and story state.

- Initial value is `null` for overview.
- DOM or canvas source selection sets `focusedSource` and uses the existing manual-interaction latch.
- Story-step selection clears `focusedSource` before selecting the step.
- Pressing Play clears `focusedSource` before resuming the existing step.
- Capability detection, autoplay ordering, visibility behavior, and fallback behavior remain unchanged.

### Three.js components

- `HeroFabricCanvas.tsx` passes the focused source through the existing production Canvas boundary.
- `FabricCameraRig.tsx` interpolates between pure camera poses and snaps when motion is disabled.
- `FabricIsland.tsx` applies the pure scale/lift target to the complete platform and asset cluster while retaining its transparent hit target.
- `FabricAsset.tsx` continues loading and batching generated GLBs; it must not compensate for poor models with per-source arbitrary transforms.
- `FabricHeroScene.tsx` tightens selective bloom and scene composition but does not interpret DOM events.
- The asset generator and validator own recognizable source geometry and silhouette constraints.

No new runtime dependency is required.

## Loading, error, and accessibility behavior

- Preserve the existing loading poster, WebGL2 failure poster, asset-error boundary, and inspection-registration cleanup.
- A failed asset must never leave an empty canvas or an active autoplay story behind a poster.
- Source and story controls remain native DOM buttons with current `aria-pressed`, `aria-current`, and live status behavior.
- Canvas selection is a redundant pointer interaction; every focus state remains reachable without using the canvas.
- The 3D canvas remains decorative/explanatory and is not the only source of product meaning.
- Touch behavior continues to permit vertical page panning.
- Keyboard focus indicators and minimum 40-pixel control targets remain visible at all responsive sizes.

## Performance and asset budgets

The visual upgrade may use more geometry than the current 131,360-byte asset set, but it must stay within the established production limits:

- all five GLBs and their textures: at most 1.8 MB transferred;
- High desktop: at most 120 conservative main, shadow, and post-processing draw calls and 150,000 visible triangles;
- Balanced/mobile: at most 70 calls and 60,000 visible triangles;
- High desktop sustained measurement: 55 FPS average or better with no application-caused jank cluster;
- Balanced mobile sustained measurement: 50 FPS average or better;
- no orphaned geometries, materials, textures, animation loops, or inspection globals after fallback or unmount.

If a category exceeds its budget, reduce invisible detail, instance repeated fans/bays, share materials, or simplify shadow casting. Do not solve a budget failure by making the scene small or blurry again.

## Test-first implementation contract

### Unit and component tests

Before production edits, add failing tests for:

1. overview and source-focus camera poses at desktop, tablet, mobile, and Static quality;
2. source selection setting focus while story-step selection and Play clear it;
3. exact snap behavior for reduced motion and Static quality;
4. selected-island scale and lift targets;
5. High, Balanced, and Static render-resolution/post-processing contracts;
6. every required asset silhouette and stable semantic mesh name;
7. loader, WebGL, and asset-failure fallbacks remaining reachable;
8. DOM source controls preserving keyboard and accessibility behavior.

The asset validator must parse the generated GLBs and check bounding boxes, semantic mesh names, triangle counts, byte lengths, consistent forward orientation, and the ratio constraints in this document. Source-string and CSS-regex checks alone are not sufficient proof of runtime behavior.

### Automated gates

After implementation, run:

- the focused hero-story, hero-fabric, infrastructure-story, asset-generator, and asset-validator tests;
- full Vitest;
- `npx tsc --noEmit`;
- targeted ESLint for all changed production, test, and script files;
- deterministic asset generation followed by asset validation;
- `git diff --check`;
- the environment-backed Next.js production build.

## Responsive local QA

Run the production landing page, not a lab route, at:

- 1440×900 desktop High tier;
- 1024×768 tablet/desktop transition;
- 390×844 mobile Balanced tier.

For every viewport:

1. capture and inspect the overview;
2. select and capture Everyday Machines;
3. select and capture Owned Infrastructure;
4. select and capture Rented GPU;
5. select and capture Cloud / HPC;
6. verify each category is recognizable without relying on its text label;
7. verify selected-source camera framing, control-plane context, DOM detail agreement, and no clipping or horizontal overflow;
8. verify story-step selection and Play return to overview;
9. verify keyboard source selection, Play/Pause, and visible focus treatment;
10. clear the console and verify no application, WebGL, hydration, or asset-loading errors.

Three.js inspection must find `FabricHeroScene`, `ZolliControlPlane`, `EverydayIsland`, `OwnedIsland`, `RentedIsland`, `CloudIsland`, `CheckpointBeacon`, and `AcceptedMarker`, and must confirm all required semantic asset meshes are present in the appropriate island.

Capture the final 15-view matrix under `apps/web/.artifacts/hero-production-clarity/` using stable filenames for viewport and source. Record renderer resolution, DPR, geometry, texture, program, draw-call, triangle, and sustained frame measurements in the implementation report.

## Scope boundaries

In scope:

- production hero camera, focus state, canvas composition, island emphasis, asset generator, five GLBs, asset validation, selective bloom, lighting, and responsive hero-stage CSS where required for framing;
- focused tests, final artifacts, and a factual implementation report.

Out of scope:

- landing copy or CTA changes;
- redesigning other landing sections;
- reintroducing `/hero-lab` or comparison variants;
- adding orbit controls, user-authored scenes, video, new analytics, or a new 3D dependency;
- changing authentication, console pages, middleware behavior, or deployment configuration;
- staging, committing, pushing, merging, or cleaning unrelated worktree changes without explicit user approval.

## Acceptance checklist

- [ ] The default topology visibly fills the stage within the occupancy bounds.
- [ ] Source selection performs the approved cinematic focus and preserves system context.
- [ ] Everyday, Owned, Rented GPU, and Cloud / HPC are recognizable from silhouette at all required viewports.
- [ ] The control plane remains the visual and semantic anchor.
- [ ] High-tier rendering is sharp at full post-processing resolution with restrained bloom.
- [ ] Tablet and mobile remain unclipped, readable, vertically scrollable, and free of horizontal overflow.
- [ ] Reduced-motion and Static paths snap to complete stable poses.
- [ ] All accessibility, fallback, cleanup, asset, and performance contracts pass.
- [ ] The full automated gate, production build, 15-view visual matrix, interaction QA, console check, and Three.js inspection pass.
- [ ] No out-of-scope or unrelated dirty worktree changes are modified.
