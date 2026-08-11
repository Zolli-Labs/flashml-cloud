# Zolli B2 Production Hero Promotion Design

**Date:** 2026-08-10

## Goal

Promote the approved B2 compute-fabric Three.js concept from `/hero-lab` into the real landing-page hero, then remove the comparison route and all A2/C experiment code. The production hero must explain Zolli's core product at first glance: one control plane coordinates fragmented compute, retains a checkpoint, and resumes work when a node disappears.

## Locked product story

- Keep the existing hero headline: **Compute that finishes the job.**
- Keep the existing definition and CTA hierarchy: **Open console** remains primary and **Talk to Zolli** remains secondary.
- The 3D stage is the approved B2 asymmetric compute fabric: Everyday Machines, Owned Infrastructure, Rented GPU, and Cloud / HPC around a Zolli control plane.
- The story states remain: Job submitted, Zolli assigns, Checkpoint retained, Node lost, Resumed elsewhere, Result accepted.
- Assigned and resumed packets travel from the control plane toward the active worker. Verified and accepted paths keep their existing direction.
- The hero is a product surface, not a comparison. It must contain no A2/B2/C labels, concept tabs, strength/weakness analysis, or temporary-lab copy.

## Production layout

Desktop uses an asymmetric two-column hero: product copy and CTAs on the left, the compute fabric on the right. The right side contains the bounded 3D canvas, four selectable source controls, the active source explanation, the current story label, the Play/Pause control, and the six-step story rail.

At tablet widths, the hero becomes one column and gives the scene the full content width. At mobile widths, source controls become a compact two-column grid, the story rail becomes a two-column grid, the canvas remains front-facing, and the active state label remains visible. No viewport may introduce horizontal page overflow.

## Runtime behavior

- The production component owns only the compute-fabric variant; there is no variant state.
- The non-reduced-motion story starts automatically after capability detection and loops through all six states.
- Selecting a source or story step pauses autoplay. Play resumes from the current state.
- Reduced motion, a hidden document, or manual pause resolves to the Static render tier: DPR 1, demand rendering, no shadows, no bloom, no parallax, and a complete stable pose.
- High and Balanced tiers preserve the already verified budgets and route animation.
- WebGL2 detection, loading poster, context-loss fallback, and inspection-registration cleanup remain intact.
- Source buttons and story steps remain real keyboard-accessible DOM controls; the canvas is explanatory, not the only interface.

## Code boundary

- Production code must not import from `components/hero-lab` or `lib/hero-lab`.
- Move the B2 renderer into `components/landing/hero-fabric/` and move shared production story data into `lib/hero-story.ts`.
- Rename `HeroLabSourceKey`, `HeroLabJobStepKey`, and lab runtime names to production names.
- Remove `app/(marketing)/hero-lab`, `components/hero-lab`, `lib/hero-lab.ts`, `lib/hero-lab.test.ts`, the old CSS-only `HeroInfrastructureStack`, and its dead `hero-infra-*` global styles.
- Remove `/hero-lab` from the middleware public allowlist and its test matrix.
- Keep the five GLB assets and the existing `hero-fabric.ts` rendering contracts.
- Do not add dependencies.

## Verification contract

- A focused production contract must fail before implementation because the landing Hero still renders `HeroInfrastructureStack`, `/hero-lab` still exists, and production B2 components do not yet exist.
- Focused tests cover runtime reducer behavior, source/story data, production Hero integration, absence of lab routes/imports, and preservation of WebGL fallbacks.
- Full Vitest, TypeScript, targeted ESLint, GLB validation, `git diff --check`, and the environment-backed Next.js build must pass.
- Browser QA must cover `/` at 1440×900, 1024×768, and 390×844; source selection, story-step selection, Play/Pause, canvas/fallback presence, CTA order, overflow, and a post-clear console check.
- Three.js inspection on `/` must find `FabricHeroScene`, `ZolliControlPlane`, all four named islands, `CheckpointBeacon`, and `AcceptedMarker`.
- `/hero-lab` must no longer be generated or publicly allowlisted.
- Do not stage, commit, push, merge, clean the worktree, or modify unrelated dirty files.
