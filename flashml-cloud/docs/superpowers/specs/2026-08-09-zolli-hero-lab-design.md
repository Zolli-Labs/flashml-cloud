# Zolli Hero Lab Design

## Purpose

Create an isolated `/hero-lab` comparison page for three interactive infrastructure metaphors. The production homepage hero remains unchanged until the user chooses a concept.

## Shared visual contract

- The scene is a bounded WebGL stage with responsive camera framing. Geometry cannot overlap the navbar, comparison controls, explanation, or page edge.
- Zolli orange represents orchestration, verified green represents an accepted result, graphite represents infrastructure, and warm cream carries readable interface text.
- All important labels and descriptions are upright DOM controls outside the 3D geometry.
- Everyday Machines are visually prominent and include laptop, desktop/workstation, and home-server silhouettes.
- Source selection moves the chosen source forward, keeps the complete system visible, and updates one concise detail panel.
- The job has a visible start, route, interruption/recovery, and accepted end. It never appears as an unrelated line or particle effect.
- Pointer and keyboard interactions expose the same information. Reduced motion freezes decorative movement while preserving selection and job-state meaning.

## Variant A2 — Orchestrated Compute Stack

Four thick infrastructure decks are separated vertically: Cloud/HPC, Rented GPU, Owned Infrastructure, and Everyday Machines. A substantial translucent Zolli software-control spine passes through every deck and retains the visible checkpoint. The job enters the spine, routes to an everyday node, returns to the retained checkpoint after that node is lost, resumes on rented capacity, and exits as an accepted result. Selecting a layer pulls that deck forward and highlights its branch without hiding the rest of the system. This keeps the stack's memorable silhouette while making orchestration the behavior of the object rather than a decorative orange line.

## Variant B2 — Orchestrated Compute Fabric

Four physically separate and deliberately asymmetric compute islands connect through a substantial upright Zolli software-control field. Everyday Machines are nearest and slightly larger; the other islands remain distinct and readable. The control field retains the checkpoint while the job route visibly leaves a lost everyday node, returns to Zolli, resumes on rented capacity, and completes. Selecting an island lifts it and brightens its route while the other sources remain visible. This best communicates fragmentation, routing, and recovery without making Zolli resemble a tiny chip or physical router.

## Variant C — Unified Runtime Backplane

Four heterogeneous source bays plug into one substantial horizontal execution backplane. The job enters once, moves along a shared rail, reroutes around a failed socket, and exits as an accepted result. Selecting a source slides its bay forward. This best communicates Zolli as a common runtime rather than a cloud marketplace.

## Comparison experience

The page provides three top-level variant tabs, a bounded stage, source controls, a synchronized six-step job story, and the requested concise explanation fields: Concept, What the user understands, Interaction, Strength, and Weakness. The six states are Job submitted, Zolli assigns, Checkpoint retained, Node lost, Resumed elsewhere, and Result accepted. The active state must control both the DOM story rail and the WebGL scene so the explanation never drifts away from the animation. Desktop presents the stage and explanation side by side. Mobile uses a front-facing camera and stacks controls below the stage.

## Acceptance criteria

1. `/hero-lab` switches among all three rendered concepts without changing the homepage.
2. Each concept exposes all four infrastructure sources and makes Everyday Machines prominent.
3. Source selection has a visible scene response and an accessible selected state.
4. The job sequence has named states and an obvious route tied to scene objects.
5. The page remains legible at 1440x900, 1280x800, 1024x768, and 390x844.
6. Reduced-motion users receive static, complete scenes without looping motion.
7. Variants A2 and B2 show one technically honest recovery sequence: the checkpoint remains with Zolli, the failed node is explicit, and work resumes elsewhere before acceptance.
8. The A2 and B2 reference renders remain design-only assets under `docs/design-references/zolli-hero-lab/`; the live comparison is built from accessible DOM and Three.js geometry.
