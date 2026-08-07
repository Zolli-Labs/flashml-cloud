# ZolliAI visual rebrand design

**Date:** 2026-08-05

**Status:** approved in conversation; visual POC only

**Scope:** `apps/web` public landing page and signed-in frontend

**Reference:** `PhongCT1105/ZolliAI` at `3d7e0df`, plus the supplied Zolli logo and character artifacts

## 1. Objective

Rebrand the FlashML Cloud frontend as **ZolliAI** using the warm, editorial
visual language established by the retired ZolliAI CRM project. The result
must feel like one coherent product before and after sign-in, while preserving
the current compute platform's behavior.

This is a visual proof of concept, not an underlying system rename. It changes
presentation, brand assets, safe user-facing copy, layout, and motion. It does
not change routes, API contracts, database names, package names, deployment
names, authentication behavior, authorization rules, or runtime protocols.

## 2. Brand architecture and vocabulary

- **ZolliAI** is the visible company and product brand.
- **ZolliAI Cloud** is the hosted distributed-compute service.
- A connected machine is presented as a **Zolli**.
- A team workspace and its combined compute are presented as a **Crew**.
- **FlashML** remains the underlying runtime name. It appears only where the
  technical distinction matters, such as documentation, protocol metadata,
  diagnostics, or an open-runtime link.
- Internal code continues to use the existing `pool`, `node`, and `machine`
  vocabulary. UI adapters and copy provide the visual rebrand boundary.

The frontend must never imply that the visual rename has changed the public
runtime, wire protocol, storage schema, or deployed service names.

## 3. Character system

The six supplied characters correspond to real platform mechanisms:

| Character | Product role | Existing system concept |
|---|---|---|
| Captain | Coordinates the crew | coordinator and task planning |
| Worker | Gets work done | executor and task lease holder |
| Scout | Adds a new Zolli | enrollment and machine activation |
| Keeper | Saves progress | verified checkpoints and manifests |
| Relay | Hands interrupted work forward | expiry, requeue, and recovery |
| Builder | Produces useful outputs | training and inference workloads |

Implement the visual system as reusable SVG React components rather than
embedding the supplied composite boards. A `ZolliLogo` component provides the
horizontal lockup and icon mark. A `ZolliCharacter` component accepts role,
size, expression, and motion-related presentation props. The components must
remain decorative where surrounding text already carries their meaning and
must expose accessible labels when the illustration itself communicates state.

Characters are functional guides, not background decoration. Use them for the
hero, role explanations, onboarding, empty states, recovery messages, and
select calls to action. Keep dense tables, logs, event ledgers, and forms free
of large mascot art.

## 4. Visual system

### 4.1 Palette

Replace the current near-black/indigo presentation with a warm light system:

- cream canvas: `#faf8f5`
- warm white surface: `#fffdf9`
- sand surface: `#f3ece4`
- warm ink: `#1a1714`
- muted warm ink: `#746b62`
- Zolli orange: `#ef6828`
- deep orange hover: `#d95319`
- evergreen success/healthy: approximately `#1f6e5d`
- amber warning/recovery: approximately `#e7ad2b`
- muted red destructive/failure: retained as a semantic state

Orange belongs to brand and interaction. Green, amber, and red remain
semantic system colors so status is still legible. Avoid generic blue-purple
AI gradients. Depth comes from warm surface steps, thin warm borders, subtle
tinted shadows, and a restrained paper-like texture.

### 4.2 Typography

Use the existing local/font-loading mechanism and keep additions minimal.
Human-facing display headings use a high-character editorial serif. Interface
copy uses a clean sans serif. IDs, timestamps, hashes, event types, and numeric
metrics remain monospaced with tabular figures.

Headlines use tight tracking and compact line height. Paragraphs remain within
approximately 60 characters. Section headings use sentence case.

### 4.3 Shape and iconography

- Outer marketing surfaces may use generous 18–24px radii.
- Console panels use quieter 8–12px radii.
- Primary actions are orange pills on marketing pages and compact rounded
  controls inside the console.
- Reuse the installed Phosphor icon set; do not add another icon library.
- The Zolli network mark replaces the current lightning-style FlashML mark.

## 5. Landing page

The landing page becomes a shorter editorial narrative.

### 5.1 Navigation

Use the Zolli horizontal logo, anchored links for **How it works**, **Meet the
crew**, and **Open runtime**, plus one primary **Build your crew** action. On
scroll, the full-width transparent navigation compresses into a floating warm
glass bar. Mobile receives a deliberate menu with the same links and actions.

### 5.2 Hero

Lead with:

> **Every machine has a part to play.**

Supporting copy explains that laptops, GPU rigs, and cloud instances form one
resilient compute crew, and that the crew keeps work moving when one Zolli
drops out. The six-character lineup is the main visual. Primary action:
**Build your crew**. Secondary action: **See how recovery works**.

### 5.3 Reliability story

Explain the existing mechanism in four steps:

1. Captain assigns a time-limited lease.
2. Keeper saves progress through verified checkpoints.
3. Relay hands interrupted work to another Zolli.
4. The Crew accepts only validated results.

The desktop experience may use a pinned scroll progression. Mobile renders the
same steps as a simple vertical sequence without scroll pinning.

### 5.4 Meet the Crew

Present all six characters with concise role-specific copy. Claims must map to
features that exist today; the character system must not invent autonomous AI
agents or capabilities.

### 5.5 Recovery proof

Retain the real event-ledger idea and current sample-data disclosure. Translate
events into a two-layer presentation: friendly narrative first and protocol
event name second. A representative sequence is lease claimed, heartbeat lost,
checkpoint found, task requeued, and commit accepted.

### 5.6 Closing action and footer

Close with **Give every machine a role in the crew** and **Create your crew**.
Keep legal links, product status, documentation/runtime links, and truthful
alpha language. Do not add testimonials, customer counts, uptime promises, or
pricing claims.

## 6. Signed-in frontend

### 6.1 App shell

Retain the current route-group and left-rail architecture. Reskin it with warm
surfaces and the Zolli logo. The switcher presents the current **Crew**.
Workspace-scoped navigation reads **Overview**, **Jobs**, **Zollis**, **People**,
and **Settings**. Personal account controls remain clearly separate.

The top bar displays the current page, a compact live-Zolli status, and the
page's primary action. It must remain usable at tablet and phone widths.

### 6.2 Overview

Use an editorial greeting followed by operational facts:

- Zollis connected
- jobs running
- tasks accepted
- current Crew roster
- active job progress
- recent event-ledger activity

Characters may identify a Zolli or explain an exceptional state. They must not
compete with live values.

### 6.3 Remaining routes

Apply the visual system across every frontend route: authentication, access
request states, activation, Crews, jobs, job detail, submission, Zollis,
people, settings, account, documentation, and admin. Preserve the current
functionality and information architecture.

Safe user-facing replacements include **Workspace → Crew** and **Machine →
Zolli** where the context is clear. Technical identifiers, CLI commands, API
payloads, protocol event names, environment variables, and troubleshooting
details remain exact.

### 6.4 Loading, empty, error, and recovery states

- Scout leads enrollment and first-machine states.
- Worker leads first-job and no-work states.
- Keeper leads checkpoint and progress-preserved messages.
- Relay leads reconnect/requeue explanations.
- Errors remain direct and actionable; mascot copy must never make failures
  sound cute or hide their cause.
- Existing skeleton behavior remains, recolored to the warm surface system.

## 7. Motion

Marketing motion may include:

- staggered hero entry
- gentle character bobbing
- infrequent blinking and a small Scout wave
- Relay handoff animation between two work tokens
- scroll-driven reliability progression
- navigation compression after meaningful scroll
- subtle reveal transitions for section content

Console motion is limited to navigation feedback, progress transitions,
loading/recovery state changes, and small character gestures. Use transforms
and opacity rather than layout properties. Honor `prefers-reduced-motion` by
removing continuous and scroll-coupled movement while preserving content and
state clarity.

## 8. Accessibility and responsive behavior

- Preserve the skip link and visible keyboard focus.
- Maintain WCAG-readable contrast for body copy and controls.
- Do not convey health or failure through color alone.
- Give meaningful illustrations accessible labels and mark redundant art as
  decorative.
- Keep all actions reachable by keyboard and touch.
- Simplify the character lineup and navigation on phones without hiding core
  content.
- Avoid fixed viewport heights that break mobile browser chrome.

## 9. Implementation boundaries

This POC may change files inside `apps/web` and the design/progress documents
needed to describe and verify the work. It must not change:

- backend code or database migrations
- API request/response fields
- middleware authorization rules
- URLs or route structure
- environment-variable names
- runtime or node package names
- deployment service names
- public documentation that would falsely announce an underlying rename

Any underlying ZolliAI rebrand becomes a separate project covering repository
names, packages, protocol branding, infrastructure, domains, migration and
compatibility policy.

## 10. Verification

Implementation follows test-first changes where behavior or reusable logic is
introduced. Visual primitives receive focused structural/accessibility tests
where supported by the existing test environment. Existing route, access,
auth, and data tests must remain unchanged and green.

Before completion, run fresh:

1. targeted tests for new brand/visual primitives
2. the complete web test suite
3. lint and TypeScript checking
4. a production Next.js build
5. browser QA at desktop and mobile widths, including reduced motion and
   keyboard navigation
6. a copy scan proving visible FlashML/workspace/machine terminology remains
   only where technically required

The main orchestrator reviews every delegated diff, resolves overlap with the
pre-existing dirty worktree, and performs the final verification rather than
accepting agent reports at face value.

## 11. Deferred underlying rebrand

A later design must decide whether and how to rename the public runtime,
packages, repositories, CLI, protocol metadata, domains, deployment services,
environment variables, and database terminology. That work needs explicit
compatibility and migration policy. This visual POC neither commits to nor
preempts those decisions.
