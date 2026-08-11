# Zolli B2 Production Three.js Hero Design

## Status

Approved direction: Option 3, a hybrid asset-driven Three.js scene. This specification defines the production-quality version of B2 — Orchestrated Compute Fabric. Implementation begins in `/hero-lab`; replacing the homepage hero is a separate approval gate.

## Purpose

Turn the existing B2 concept into a high-fidelity interactive infrastructure scene that immediately communicates Zolli's core product idea:

> Fragmented compute becomes one recoverable execution fabric. Zolli retains the checkpoint, detects a lost node, and resumes the job on compatible capacity.

The target is the supplied reference's visual quality: dimensional black-metal devices, substantial compute islands, a luminous central control plane, routed work, a visible failure, a retained checkpoint, a resumed job, and an accepted result. It must remain a real product explanation rather than an ornamental network diagram.

## Current baseline

The current B2 implementation is already WebGL rendered with Three.js through React Three Fiber. It is not CSS pretending to be 3D. Its visual limitation is that almost everything is assembled from low-detail runtime primitives.

The Three.js MCP baseline on 2026-08-10 reported:

- 60 FPS on the development machine
- 74 draw calls
- 6,684 triangles
- 80 meshes and 35 groups
- 81 geometries and one texture
- five point or directional light sources plus ambient and hemisphere light
- shadows disabled
- unnamed scene objects, which makes scene inspection unnecessarily difficult

The upgrade must preserve the current clarity and performance while replacing the placeholder-like devices, flat control field, uniform platforms, and simple route tubes.

## Implemented lab verification

The Option 3 implementation was verified in `/hero-lab` on 2026-08-10. It remains behind the comparison-lab selection gate; `components/landing/Hero.tsx`, A2, and C are outside this implementation scope.

The asset pipeline deterministically produces five first-party GLBs and validates their names, bounds, triangle counts, hashes, and size budget. The verified set totals 131,360 bytes: Everyday Machines 43,616 bytes, Owned Infrastructure 27,976 bytes, Rented GPU 17,648 bytes, Cloud/HPC 19,888 bytes, and the control plane 22,232 bytes.

The authored scene is published to Three.js inspection with stable names. Live inspection found `FabricHeroScene`, `ZolliControlPlane`, `EverydayIsland`, `OwnedIsland`, `RentedIsland`, `CloudIsland`, and `CheckpointBeacon`; `FailureBranch` appeared in the lost state and `AcceptedMarker` appeared in the accepted state.

Measured renderer results were within the binding budgets:

- Balanced, 390×844: 62 actual draw calls, 41,504 triangles, DPR 1, 60 FPS average, and 56.2 FPS minimum over the measured 10-second interval.
- High, 1440×900: 84 authored main-plus-shadow calls and 71,764 triangles, DPR 1, and 60 FPS average. Selective bloom uses an estimated maximum of 22 additional full-screen or mask passes, giving a conservative bound of 106 calls and fewer than 73,000 triangles. The measured High minimum was 53.5 FPS over the 10-second interval.

The postprocessing composer resets `renderer.info` after its final full-screen pass, so the raw High counter is not an authoritative total. The High acceptance result above combines read-only visible-scene traversal, shadow-caster counts, and a conservative source-verified postprocessing allowance; no runtime MCP setting was changed to obtain it.

Browser acceptance used 20 exact-size screenshots: submitted, checkpointed, lost, resumed, and accepted at 1440×900, 1280×800, 1024×768, and 390×844. Every file was visually inspected. Live interaction verified Play from the current checkpoint, pointer source selection pausing autoplay and updating `aria-pressed`, keyboard `Tab` + `Enter` source selection, all five GLBs returning HTTP 200, and a fresh console with no application errors. Live reduced-motion media emulation was unavailable because the browser automation denylisted `Emulation.setEmulatedMedia`; deterministic quality-provider and runtime tests remain the acceptance evidence for that path.

The environment-backed Next.js production build compiled successfully, completed TypeScript, generated all 27 static pages, and listed `/hero-lab` as a static route. Its only build warning was the existing Next.js middleware-to-proxy convention deprecation.

## Chosen rendering strategy

Use a hybrid scene:

1. Custom local GLB assets provide the visually important device clusters and control-plane chassis.
2. Procedural Three.js components provide source islands, execution routes, checkpoint state, failure state, job packets, selection effects, and responsive choreography.
3. Upright DOM copy remains the source of truth for labels, controls, CTAs, and accessible state. In-scene labels are redundant decorative decals only.

This approach is preferred over a fully procedural scene because modeled assets are necessary to reach the reference fidelity. It is preferred over one monolithic GLB because Zolli's routing, selection, recovery, responsive layout, and reduced-motion behavior must remain programmable.

## Product story

### Hero promise

The production copy remains:

- Eyebrow: `FAULT-TOLERANT DISTRIBUTED COMPUTE`
- Headline: `Compute that finishes the job.`
- Primary CTA: `Open console`
- Secondary CTA: `Talk to Zolli`

The visual must make the promise believable before the visitor reads detailed body copy.

### Source islands

The complete system always shows four physically separate sources:

1. **Everyday Machines** — laptop, display/workstation, desktop tower, and a small home server. It is the nearest and largest island because bringing normal compatible machines into the pool is Zolli's most distinctive supply story.
2. **Owned Infrastructure** — workstation, tower, and a compact private server or rack.
3. **Rented GPU** — two recognizable GPU server units with visible fan and heat-sink geometry.
4. **Cloud / HPC** — a denser rack cluster representing external high-compute capacity.

The islands must not imply a hierarchy. Their separation communicates fragmentation; their active routes communicate unification.

### Zolli control plane

The control plane is the visual and semantic center. It must be a substantial upright object with:

- a modeled black-metal frame and beveled glass or dark acrylic face;
- an emissive orange routing grid;
- animated job cells that move through the grid;
- one persistent checkpoint object that remains visible across failure and resumption;
- connection sockets aligned with the four islands;
- a restrained `Zolli control plane` decorative label, duplicated by accessible DOM copy outside the canvas.

It must read as software coordination represented physically, not as a fifth compute provider or a network router.

## Asset system

### Required local assets

Store first-party assets under `apps/web/public/models/hero/fabric/`:

- `everyday-machines.glb`
- `owned-infrastructure.glb`
- `rented-gpu.glb`
- `cloud-hpc.glb`
- `control-plane.glb`

Store texture atlases under `apps/web/public/textures/hero/fabric/`. Use at most 1024-pixel atlases for desktop assets and 512-pixel alternatives only when a separate mobile texture materially reduces memory.

Store the immediate non-WebGL fallback at `apps/web/public/images/hero/fabric-poster.webp`. The fallback uses the approved B2 composition and must be compressed separately for desktop and responsive delivery rather than loading a full-resolution design reference.

### Reproducible authoring pipeline

The custom models must not be opaque binary-only artifacts. Add `apps/web/scripts/hero-assets/build-fabric-assets.mjs`, which creates the source device assemblies with Three.js geometry, bevelled and extruded parts, named materials, and consistent origins, then exports the five GLBs with `GLTFExporter`. Add an `npm run hero:assets` command that generates the same asset set deterministically.

Run the generated files through `@gltf-transform/cli` for deduplication, pruning, Meshopt compression, and texture optimization. Commit both the authoring script and the optimized runtime GLBs. The script is the editable source of truth; runtime React components must not rebuild the detailed device geometry on every page load.

Generate `apps/web/public/models/hero/fabric/asset-manifest.json` with each asset's file size, mesh names, bounding box, triangle count, and content hash. Asset validation compares the checked-in files with this manifest.

### Asset construction contract

- Assets are custom Zolli assets. Do not import unlicensed marketplace models.
- Any approved third-party CC0 asset must be listed in `apps/web/public/models/hero/fabric/LICENSES.md` with its source URL and license.
- Use physically plausible scale consistently across all source assets.
- Apply bevels to silhouette edges; avoid perfectly sharp placeholder boxes.
- Use repeated geometry for vents, keys, rack bays, and GPU fans without modeling invisible internals.
- Use a small shared PBR material set: graphite metal, dark polymer, smoked glass, muted screen, Zolli orange emissive, and verified green emissive.
- Origins sit at each asset's island-contact point. Forward direction and scale must be consistent.
- Meshes and materials use stable names such as `Everyday_Laptop`, `Rented_GPU_A`, and `Mat_GraphiteMetal`.
- Decorative screens use a shared atlas rather than separate textures per device.

### Optimization contract

- Optimize GLBs before checking them into the repository.
- Reuse geometry and materials inside each asset.
- Use Meshopt compression when browser decoding support is included in the loader.
- Use WebP textures through glTF extensions only when the fallback and browser support are verified.
- The five GLBs plus their textures must transfer in no more than 1.8 MB total.
- Desktop texture memory must stay at or below 32 MB; mobile texture memory must stay at or below 12 MB.
- Desktop triangles must stay at or below 150,000; the balanced/mobile scene must stay at or below 60,000.

## Scene architecture

### Component boundaries

The implementation is split into focused B2 components under `apps/web/components/hero-lab/b2/`:

- `FabricHeroScene.tsx` — composes the B2 scene and exposes the existing scene contract.
- `FabricAsset.tsx` — loads, clones, names, and disposes optimized GLB assets.
- `FabricIsland.tsx` — renders a source platform, selection state, hit target, label decal, and asset cluster.
- `FabricControlPlane.tsx` — combines the control-plane GLB with its procedural grid, job cells, sockets, and checkpoint.
- `FabricExecutionRoutes.tsx` — renders source routes, the active execution route, moving packets, interruption, resumption, and accepted exit.
- `FabricCameraRig.tsx` — owns intro framing, limited pointer parallax, responsive camera targets, and scroll-linked dolly.
- `FabricLighting.tsx` — owns studio lighting, shadow quality, environment treatment, and optional post-processing.
- `FabricFallback.tsx` — provides the loading, WebGL-failure, asset-failure, and reduced-capability poster treatment.

Shared product data and pure state mapping live in `apps/web/lib/hero-fabric.ts`. Runtime components consume the mapping; they do not independently interpret job states.

### Public interface

`FabricHeroScene` preserves the current comparison contract:

```ts
interface FabricHeroSceneProps {
  selectedSource: HeroLabSourceKey;
  jobStep: HeroLabJobStepKey;
  reducedMotion: boolean;
  quality: FabricQualityTier;
  onSelectSource: (source: HeroLabSourceKey) => void;
}

type FabricQualityTier = "high" | "balanced" | "static";
```

`HeroLabCanvas` remains the owner of the Canvas. B2 replaces only `TopologyScene`; A2, C, the comparison shell, and the production landing hero remain intact during this phase.

## Job-story state machine

The existing six named states remain the only story states:

```ts
type HeroLabJobStepKey =
  | "submitted"
  | "assigned"
  | "checkpointed"
  | "lost"
  | "resumed"
  | "accepted";
```

`getFabricStorySnapshot(step)` returns a complete declarative snapshot used by the control plane, routes, islands, and DOM rail. Components must not infer state from elapsed animation time.

### State behavior

1. **Job submitted** — one orange packet enters the control plane. The four islands remain visible and neutral.
2. **Zolli assigns** — the Everyday Machines socket and route illuminate. A packet travels from the control plane to the everyday island, which lifts subtly.
3. **Checkpoint retained** — the checkpoint locks into the control-plane grid and continues glowing. Its position does not move to the worker.
4. **Node lost** — the everyday route stops, becomes a restrained red dashed branch, and the failed device dims. The checkpoint remains orange and stable in the control plane.
5. **Resumed elsewhere** — a packet returns from the checkpoint and travels through a newly illuminated route to Rented GPU. The rented island lifts; the failed island remains readable but inactive.
6. **Result accepted** — a verified-green packet exits the rented island to an accepted marker. The control plane and all four sources remain visible so the outcome still reads as one fabric.

Manual selection of a source pauses autoplay. Manual selection changes source emphasis and the detail panel but does not rewrite the recovery story. Selecting a story step changes both the DOM rail and the Three.js snapshot.

## Motion and camera direction

### Entrance choreography

The first meaningful frame must appear before decorative animation begins. Once assets are ready:

1. the control plane resolves from dark graphite to a low orange grid;
2. islands settle into place with 70–110 ms stagger and no elastic bounce;
3. source routes draw toward the control plane;
4. the six-state job story begins.

The entrance lasts no more than 1.8 seconds. It must not delay CTA interaction.

### Camera

- Use one authored perspective camera, not free orbit controls.
- Desktop begins in a wide three-quarter view and performs a shallow dolly toward the control plane.
- Pointer parallax is capped at 2.5 degrees and is enabled only for a fine pointer when reduced motion is off.
- Scroll progress may move the camera a short distance toward the fabric and lower the pitch, but the hero is not pinned and the system never leaves frame.
- Tablet uses a tighter camera with reduced depth separation.
- Mobile uses a front-biased three-quarter view, fewer particles, and no scroll-linked camera motion.
- Source selection may shift the camera target slightly; it never zooms tightly enough to hide the other sources.

### Object motion

- Island selection uses damped lift and emissive response, not springy floating cards.
- Job packets move at a constant readable speed with short ease-in and ease-out at sockets.
- Checkpoint retention uses a restrained breathing glow.
- Failure uses a single route break, short red pulse, and device dim. Do not add explosions, shaking, sparks, or alarm-screen clutter.
- Acceptance changes only the final route and marker to verified green.

## Materials, lighting, and effects

### Materials

- Infrastructure uses graphite and black materials with roughness variation; pure black is reserved for gaps and background.
- Orange is exclusive to Zolli orchestration, active selection, active routes, and retained checkpoints.
- Red appears only during the lost-node state.
- Green appears only for verified acceptance.
- Screens and LEDs remain subordinate to the route and checkpoint.

### Lighting

- Use a soft warm key, cool graphite fill, and subtle rim light.
- Enable one shadow-casting directional light on high quality and cap its map at 1024×1024.
- Balanced quality uses either a 512×512 shadow map or baked/contact shadow planes.
- Static quality uses no dynamic shadows.
- Avoid stacking several intense point lights; emissive materials and post-processing carry the route glow.

### Post-processing

- Use selective bloom for orange and green emissive elements only.
- Bloom must not soften device silhouettes or make text-like decals unreadable.
- Chromatic aberration, depth-of-field blur, film grain, and heavy vignette are out of scope.
- If post-processing is unavailable or disabled, emissive materials must still communicate every state.

## Quality tiers and capability detection

Choose quality before scene construction:

- **High** — desktop-width, fine pointer, visible document, no reduced motion, and a functioning WebGL2 context. Use desktop assets, DPR capped at 1.5, shadows, selective bloom, route packets, and pointer parallax.
- **Balanced** — coarse pointer, tablet/mobile width, or constrained rendering capability. Use DPR 1, lower texture variants when available, reduced shadow cost, fewer route packets, no pointer parallax, and no continuous decorative animation.
- **Static** — reduced motion, hidden document, or a paused story. Render the selected complete state with `frameloop="demand"` and no looping animation.

Capability detection must remain conservative. A missing API or uncertain device is assigned to Balanced, not High.

WebGL2 is required by the installed Three.js renderer. When WebGL2 context creation fails, do not construct a lower-quality Canvas; render the poster and accessible DOM explanation instead.

## Loading and failure behavior

- Keep the existing dynamic client-only Canvas boundary.
- Preload B2 assets only when B2 is selected or likely to be selected; A2 and C must not pay the asset cost unnecessarily.
- While loading, show an art-directed poster derived from the approved B2 reference with the current state explained in DOM copy.
- Place the Canvas behind an error boundary. Asset failure or context creation failure must preserve the headline, CTAs, source controls, story rail, and poster.
- Listen for `webglcontextlost`; stop animation and show the poster without repeatedly recreating the renderer.
- Retry asset loading only after explicit user action.
- No remote asset request is required for the hero to render.

## Accessibility

- All essential copy, source names, state names, CTAs, and status explanations remain semantic DOM.
- Canvas has one concise accessible label and is otherwise decorative to screen readers.
- Source buttons and story steps keep keyboard operation, visible focus, `aria-pressed`, and `aria-current` behavior.
- Clicking a 3D island invokes the same selection callback as its DOM button.
- The scene never uses color alone: route shape, visibility, marker geometry, and adjacent DOM status also change.
- Reduced motion renders a complete static accepted state by default and supports direct inspection of every named step.

## Performance budgets

The following are acceptance limits, not aspirations:

- 60 FPS target on the development desktop; no sustained frame below 50 FPS during the story.
- 30 FPS floor on a representative mid-tier mobile device in Balanced mode.
- no more than 120 draw calls in High and 70 draw calls in Balanced;
- no more than 150,000 triangles in High and 60,000 in Balanced;
- DPR capped at 1.5 in High and 1 in Balanced;
- five GLBs and textures no larger than 1.8 MB transferred in total;
- first meaningful scene frame within 1.5 seconds on desktop broadband and 2.5 seconds on a throttled mobile profile, with the poster available immediately;
- no animation loop when the document is hidden, the scene is Static, or the component is outside the active hero viewport.

Repeated server bays, LEDs, fans, and job packets should use instancing where it reduces draw calls. Avoid premature instancing for unique hero objects.

## Three.js MCP workflow

The implementation must use the installed Three.js MCP as an inspection and acceptance tool, not as a source of runtime-only fixes.

For each visual milestone:

1. run the isolated web development server with the documented `.env.dev` workflow;
2. point the MCP proxy to that port and open `/hero-lab` through the proxy;
3. select B2 and inspect `scene_tree`;
4. inspect `renderer_settings` and `performance_snapshot`;
5. capture the six job states at required desktop and mobile sizes;
6. translate every accepted visual adjustment into source code;
7. reload and verify that no runtime-only MCP mutation is required.

All important objects must be named so `scene_tree` reports at least:

- `FabricHeroScene`
- `FabricControlPlane`
- `EverydayIsland`
- `OwnedIsland`
- `RentedIsland`
- `CloudIsland`
- `CheckpointBeacon`
- `FailureBranch`
- `AcceptedMarker`

## Testing strategy

### Unit tests

- Verify the six `getFabricStorySnapshot` outputs exactly.
- Verify source-to-island and source-to-asset mappings.
- Verify conservative quality-tier selection.
- Verify that reduced motion produces a Static tier and no autoplay.

### Component contract tests

- Verify B2 loads only when selected.
- Verify DOM source selection and story selection pass the expected props to `FabricHeroScene`.
- Verify manual state inspection pauses autoplay.
- Verify the Canvas error boundary preserves the poster, controls, and copy.
- Verify the production `components/landing/Hero.tsx` is not changed during the lab phase.

### Asset validation

Add a validation script that fails when:

- a required GLB is missing;
- a GLB or combined asset budget is exceeded;
- required mesh names are absent;
- an asset has a materially incorrect bounding box or origin;
- textures exceed the stated dimensions;
- a third-party asset is present without a license entry.

### Browser and visual QA

Verify at 1440×900, 1280×800, 1024×768, and 390×844:

- all four islands remain distinguishable;
- the control plane is the visual center without covering a source;
- source selection never hides the full fabric;
- every route connects to a visible socket;
- node loss, retained checkpoint, resumption, and acceptance are understandable without reading the lower explanation cards;
- no label, CTA, control, route, or accepted marker is clipped;
- keyboard selection mirrors pointer selection;
- reduced motion is complete and stable;
- browser console contains no WebGL, hydration, asset, or unhandled promise errors.

## Delivery phases

### Phase 1 — Asset and scene foundation

Create, optimize, validate, and load the five first-party assets. Replace the primitive source devices and control-plane chassis while preserving the current static B2 layout and interaction contract.

### Phase 2 — Execution story

Implement the pure snapshot mapper, control-plane grid, checkpoint, active routes, failure branch, resume route, accepted marker, and synchronized DOM behavior.

### Phase 3 — Cinematic polish

Add authored entrance choreography, restrained camera motion, lighting, shadows, selection response, and selective bloom. Establish High, Balanced, and Static paths.

### Phase 4 — Hardening and approval

Add asset failure handling, context-loss behavior, asset validation, performance gates, responsive QA, and MCP scene inspection. Leave B2 in `/hero-lab` for user approval.

### Phase 5 — Separate homepage integration gate

Only after explicit approval, create a separate task to place the accepted B2 scene in the production landing hero, connect its scroll lifecycle, measure landing performance, and remove or archive the rejected hero concept. Phase 5 is not part of this implementation scope.

## Acceptance criteria

1. B2 visibly reaches the supplied reference's level of dimensionality through modeled device assets, beveled islands, a substantial control plane, authored lighting, and readable routes.
2. The six-state recovery story remains technically honest: Zolli retains the checkpoint, the everyday node is lost, work resumes on rented capacity, and only the accepted result turns green.
3. All four source classes remain visible and individually selectable throughout the story.
4. The scene is genuine, inspectable Three.js with named objects and no CSS-generated fake geometry.
5. Essential meaning remains available through accessible DOM and a static poster when WebGL or assets fail.
6. High, Balanced, and Static tiers satisfy their behavior and performance contracts.
7. The Three.js MCP reports the required named objects and performance within the stated budgets.
8. All tests, asset validation, type checking, targeted lint, production build, and required browser QA pass.
9. A2, C, and `components/landing/Hero.tsx` remain unchanged during this phase.
10. No commit, push, merge, or production replacement occurs without a separate user instruction.

## Out of scope

- free-orbit camera controls;
- a full 3D product configurator;
- live backend or scheduler data inside the hero;
- photorealistic vendor-branded hardware;
- explosions, physics simulation, or game-like failure effects;
- remote runtime assets;
- WebXR;
- replacing the production homepage hero before the `/hero-lab` approval gate.
