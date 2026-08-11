# Implementation plan — Zolli coordinator map hero

**Spec:** `../specs/2026-08-11-zolli-coordinator-map-hero-design.md`
**Branch:** `agent/zolli-frontend-landing`
**Working dir:** `flashml-cloud/apps/web`

Executed as a multi-agent workflow. File ownership is disjoint per stage so
agents never contend for the same file.

---

## Stage 1 — foundations (parallel, no interdependencies)

**1a · Pure logic.** Write `lib/coordinator-map.ts` implementing §3.1 of the
spec exactly, and `lib/coordinator-map.test.ts` covering every invariant listed
there. No DOM, no `window`, no `Date.now()`. Reuses `HeroSourceKey` from
`lib/hero-story.ts`.

**1b · Icon data.** Write `lib/coordinator-map-icons.ts` exporting
`PLATFORM_ICONS: Record<string, string>` of 24×24 SVG path data. Extract the
real paths from `node_modules/simple-icons/icons/{apple,linux,nvidia,googlecloud,docker,ubuntu}.svg`.
Add a hand-drawn four-pane `windows` glyph. Export
`PLATFORM_LABELS` for marks with no icon (`AWS`), rendered as type.

**1c · Removal.** Delete everything in spec §4. Drop the seven dependencies and
the two `hero:assets*` scripts from `package.json`. Do **not** touch
`lib/hero-story.ts`. Leave tests that reference removed modules failing — stage
4 repairs them.

## Stage 2 — the map components (after 1a + 1b)

Single agent owning all of `components/landing/coordinator-map/`. Port the
approved prototype at `apps/web/.artifacts/inspiration/coordinator.html`, then
apply every refinement in spec §2.1: ground grid, elbow routes, four distinct
silhouettes, the checkpoint held inside the core, depth cues, type hierarchy,
particle trails, hover/focus states, and the one-orange rule.

All geometry comes from `lib/coordinator-map.ts` — no projection maths in
components. Client components, Tailwind for layout, brand tokens from
`app/globals.css` for colour (`--z-orange`, `--z-healthy`, `--z-failure`,
`--z-text`, `--z-border`).

## Stage 3 — story + wiring (after 2)

`useMapStory.ts`: scroll progress → `MapPhase` via `phaseForProgress`, with the
timer fallback and the `prefers-reduced-motion` static frame. Then rewrite
`components/landing/Hero.tsx` to render `CoordinatorMap` instead of
`HeroComputeFabric`, delete `HeroComputeFabric.tsx` and its CSS module, and put
the sticky scroll container into `app/(marketing)/page.tsx`.

## Stage 4 — repair the test suite (after 3)

Update `lib/landing-fabric-clarity.test.ts`,
`lib/landing-cinematic.test.ts` and `lib/landing-infrastructure-story.test.ts`
to assert the coordinator map rather than the deleted fabric. Coverage of the
hero must not regress — replace assertions, do not delete them.

## Stage 5 — verify (after 4)

`npm test`, then `npm run build` with `NEXT_PUBLIC_CLOUD_API` set. Check every
acceptance criterion in spec §5, including grepping the built chunks for
`WebGLRenderer` and confirming `three` is gone from the lockfile. Fix what
fails; report honestly what does not.

## Stage 6 — visual QA

Screenshot `/` at 1440×900, 1024×768 and 390×844. Check for clipped or
overlapping labels, horizontal body scroll, and that orange appears only on the
job path. Report findings with evidence.

---

## Risks

- **Stage 1c breaks the build until stage 3 lands.** Expected. Nothing is
  verified until stage 5.
- **Elbow routing can look wrong at the diamond corners** where both segments
  collapse. `elbowPath` must handle the degenerate case where `from` and `to`
  share an isometric axis.
- **The compact layout is a different composition, not a scaled one.** Below
  880px the diamond stacks vertically; this needs its own screenshot pass.
