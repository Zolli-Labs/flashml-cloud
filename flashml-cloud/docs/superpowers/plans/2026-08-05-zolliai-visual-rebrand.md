# ZolliAI Visual Rebrand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a complete visual POC that presents the existing FlashML Cloud frontend as ZolliAI, with connected machines shown as Zollis and team workspaces shown as Crews.

**Architecture:** Add one reusable brand layer (`zolli-brand.ts`, `Mark.tsx`, and `ZolliCharacter.tsx`), then consume it from a shortened character-led landing page and from the existing console route tree. Preserve all routes, API payloads, internal `pool`/`machine` vocabulary, and business behavior; the rebrand stops at the rendering boundary.

**Tech Stack:** Next.js 16 App Router, React 19, TypeScript 5, Tailwind CSS 4, Motion 12, Phosphor Icons, Vitest 4, `next/font/google` Inter + Fraunces.

## Global constraints

- Work only in `flashml-cloud/apps/web` plus this plan, the approved design spec, and `PROGRESS.md`.
- Do not change backend code, database migrations, API fields, middleware authorization, URLs, route structure, environment-variable names, runtime packages, deployment names, or public protocol names.
- Use **ZolliAI** as the visible brand, **ZolliAI Cloud** for the hosted service, **Zolli** for a connected machine, and **Crew** for the user-facing team workspace.
- Keep `pool`, `machine`, `node`, and `FlashML` wherever exact technical vocabulary is required by code, API payloads, CLI commands, event names, diagnostics, or runtime documentation.
- Orange `#ef6828` is brand/action. Evergreen, amber, and red remain semantic status colors.
- Characters may explain onboarding, empty states, recovery, and role concepts. Do not place large mascots in dense tables, event logs, or forms.
- Use only installed dependencies. Do not add a package.
- Use transforms and opacity for motion and honor `prefers-reduced-motion`.
- Preserve the user's pre-existing dirty changes, especially in `ConsoleShell.tsx`, `OnboardingForm.tsx`, account/workspace components, and their tests.
- Because all agents share one worktree, implementation agents must not stage or commit. The main orchestrator reviews, stages exact paths, and commits each accepted slice.
- The user approved an exception from automated tests for visual-only layout,
  copy, CSS, metadata, and font configuration. Do not add tests that grep source
  text. Test new reusable logic and accessible SVG behavior; validate visual
  work through the existing behavioral suite, build, and browser QA.
- Dispatch fresh implementers sequentially in this shared dirty worktree. Use
  multiple agents across tasks plus independent review agents, but never run
  two implementation agents concurrently.

## File structure and ownership

### Shared foundation — must finish before page work

- `apps/web/lib/zolli-brand.ts` — role names, labels, descriptions, and colors.
- `apps/web/lib/zolli-brand.test.ts` — exact role and accessible-name contract.
- `apps/web/components/brand/Mark.tsx` — Zolli network mark and horizontal wordmark.
- `apps/web/components/brand/ZolliCharacter.tsx` — reusable SVG character primitive.
- `apps/web/app/globals.css` — warm theme, display typography, surface utilities, and mascot motion.
- `apps/web/app/layout.tsx` — fonts, metadata, light color scheme, and light toast theme.

### Workstream A — public landing

- `apps/web/app/(marketing)/page.tsx`
- `apps/web/components/nav/Navbar.tsx`
- `apps/web/components/landing/Hero.tsx`
- `apps/web/components/landing/CrewStory.tsx` (new)
- `apps/web/components/landing/CrewRoles.tsx` (new)
- `apps/web/components/landing/RecoveryDemo.tsx`
- `apps/web/components/landing/EventLedger.tsx`
- `apps/web/components/landing/ClosingCta.tsx`

### Workstream B — shell, auth, and access states

- `apps/web/components/shell/ConsoleShell.tsx`
- `apps/web/components/shell/WorkspaceSwitcher.tsx`
- `apps/web/components/shell/FleetPill.tsx`
- `apps/web/components/shell/CommandPalette.tsx`
- `apps/web/components/shell/Shortcuts.tsx`
- `apps/web/components/nav/UserMenu.tsx`
- `apps/web/app/(auth)/sign-in/SignInCard.tsx`
- `apps/web/components/onboarding/OnboardingForm.tsx`
- `apps/web/components/onboarding/PendingScreen.tsx`
- `apps/web/components/onboarding/DeclinedScreen.tsx`
- `apps/web/app/(console)/activate/page.tsx`
- `apps/web/app/(console)/pools/join/page.tsx`
- `apps/web/app/(console)/workspaces/page.tsx`

### Workstream C — console pages and product surfaces

- `apps/web/app/(console)/w/[poolId]/**`
- `apps/web/app/(console)/jobs/[jobId]/page.tsx`
- `apps/web/app/(console)/account/**`
- `apps/web/app/(console)/admin/requests/page.tsx`
- `apps/web/app/(console)/docs/page.tsx`
- `apps/web/app/error.tsx`
- `apps/web/app/not-found.tsx`
- `apps/web/components/jobs/**`
- `apps/web/components/machines/EnrolInstructions.tsx`
- `apps/web/components/pools/ConnectPanel.tsx`
- `apps/web/components/workspace/**`

### Orchestrator-owned integration

- `PROGRESS.md` — verified work-journal entry after all checks pass.

---

### Task 1: Build the Zolli brand primitives

**Files:**
- Create: `flashml-cloud/apps/web/lib/zolli-brand.ts`
- Create: `flashml-cloud/apps/web/lib/zolli-brand.test.ts`
- Create: `flashml-cloud/apps/web/components/brand/ZolliCharacter.tsx`
- Modify: `flashml-cloud/apps/web/components/brand/Mark.tsx`

**Interfaces:**
- Produces: `type ZolliRole = "captain" | "worker" | "scout" | "keeper" | "relay" | "builder"`.
- Produces: `ZOLLI_ROLES: Record<ZolliRole, ZolliRoleDefinition>` where each definition has `label`, `subtitle`, `description`, and `color`.
- Produces: `ZolliCharacter({ role, size?, mood?, animated?, className?, label? })`.
- Produces: `Mark({ size?, className? })` and `Wordmark({ size?, className?, product? })` where `product` optionally renders “Cloud”.

- [ ] **Step 1: Write the failing role contract test**

```ts
import { describe, expect, it } from "vitest";
import { ZOLLI_ROLES } from "@/lib/zolli-brand";

describe("Zolli role system", () => {
  it("defines the six approved product roles", () => {
    expect(Object.keys(ZOLLI_ROLES)).toEqual([
      "captain", "worker", "scout", "keeper", "relay", "builder",
    ]);
    expect(ZOLLI_ROLES.keeper.subtitle).toBe("Checkpoint");
    expect(ZOLLI_ROLES.relay.subtitle).toBe("Handoff");
  });
});
```

- [ ] **Step 2: Run the test and verify RED**

Run: `cd flashml-cloud/apps/web && npm test -- lib/zolli-brand.test.ts`

Expected: FAIL because `@/lib/zolli-brand` does not exist.

- [ ] **Step 3: Add the typed role data**

Use the exact role order from the test. Use these approved labels and colors:

```ts
export const ZOLLI_ROLES = {
  captain: { label: "Captain", subtitle: "Coordinator", description: "Plans the work and keeps the crew in sync.", color: "#ef6828" },
  worker: { label: "Worker", subtitle: "Executor", description: "Claims tasks, computes, and delivers results.", color: "#1f6e5d" },
  scout: { label: "Scout", subtitle: "New Zolli", description: "Helps a new machine join the crew.", color: "#e7ad2b" },
  keeper: { label: "Keeper", subtitle: "Checkpoint", description: "Preserves progress through verified checkpoints.", color: "#b8b2ac" },
  relay: { label: "Relay", subtitle: "Handoff", description: "Hands interrupted work to the next Zolli.", color: "#252321" },
  builder: { label: "Builder", subtitle: "Training / Inference", description: "Turns code and data into completed models.", color: "#f48b68" },
} as const;
```

- [ ] **Step 4: Implement the SVG brand primitives**

Draw the connected-node `Z` mark with SVG lines and circles so it remains crisp
at favicon size. Build the six characters from shared rounded body, eye, mouth,
arm, and role-accessory SVG groups. `label` renders `role="img"` plus
`aria-label`; without `label`, render `aria-hidden="true"`.

- [ ] **Step 5: Run the focused test and TypeScript**

Run: `cd flashml-cloud/apps/web && npm test -- lib/zolli-brand.test.ts`

Expected: PASS.

Run: `cd flashml-cloud/apps/web && npx tsc --noEmit`

Expected: exit 0.

- [ ] **Step 6: Orchestrator review and commit**

Review the SVG at 16px, 24px, and 96px. Stage only the four Task 1 paths and commit:

```bash
git commit -m "feat(web): add Zolli brand primitives"
```

### Task 2: Install the warm ZolliAI theme and metadata

**Files:**
- Modify: `flashml-cloud/apps/web/app/globals.css`
- Modify: `flashml-cloud/apps/web/app/layout.tsx`

**Interfaces:**
- Consumes: `Mark` and `Wordmark` from Task 1 only indirectly through pages.
- Produces: Tailwind-backed tokens `bg-cream`, `bg-surface`, `bg-surface-2`,
  `text-ink`, `text-muted-foreground`, accessible small-copy brand/status
  foregrounds, `text-evergreen`, and `font-display`. `bg-muted` remains the
  sand compatibility surface; no `text-muted` alias is created because
  Tailwind color tokens cannot safely mean different colors by utility type.
- Preserves: existing compatibility tokens such as `background`, `foreground`, `primary`, `border`, `node-green`, and `warning` so unconverted components remain legible during the page phase.

- [ ] **Step 1: Record the existing behavioral baseline**

Run: `cd flashml-cloud/apps/web && npm test`

Expected: 22 test files and 240 tests pass before the visual change.

- [ ] **Step 2: Replace the token layer without removing compatibility names**

Use Inter for UI/body, Fraunces for `--font-display`, and Geist Mono for
machine-emitted data. Set `color-scheme: light`. Retune grain, glass, focus,
shadows, scrollbars, skeletons, flow controls, and rules for warm surfaces.
Add `.zolli-bob`, `.zolli-blink`, `.zolli-wave`, and `.zolli-handoff` using
transform/opacity keyframes. Keep the existing reduced-motion override.

- [ ] **Step 3: Update root layout**

Set title to `ZolliAI`, template to `%s · ZolliAI`, and truthful distributed
compute copy. Remove the `dark` class. Set the Toaster to `theme="light"` and
warm token classes. Preserve skip-link behavior and providers.

- [ ] **Step 4: Verify behavior and compile**

Run: `cd flashml-cloud/apps/web && npm test`

Expected: all baseline behavioral tests remain green.

Run: `cd flashml-cloud/apps/web && npx tsc --noEmit`

Expected: exit 0.

- [ ] **Step 5: Orchestrator review and commit**

Stage only Task 2 paths and commit:

```bash
git commit -m "feat(web): install ZolliAI visual system"
```

### Task 3: Rebuild the public landing narrative

**Files:**
- Create: `flashml-cloud/apps/web/components/landing/CrewStory.tsx`
- Create: `flashml-cloud/apps/web/components/landing/CrewRoles.tsx`
- Modify: `flashml-cloud/apps/web/app/(marketing)/page.tsx`
- Modify: `flashml-cloud/apps/web/components/nav/Navbar.tsx`
- Modify: `flashml-cloud/apps/web/components/landing/Hero.tsx`
- Modify: `flashml-cloud/apps/web/components/landing/RecoveryDemo.tsx`
- Modify: `flashml-cloud/apps/web/components/landing/EventLedger.tsx`
- Modify: `flashml-cloud/apps/web/components/landing/ClosingCta.tsx`

**Interfaces:**
- Consumes: `Wordmark`, `ZolliCharacter`, `ZOLLI_ROLES`, existing `Reveal`, existing Motion timing, and `SAMPLE_LEDGER`.
- Produces: anchors `#how-it-works`, `#crew`, and `#recover` used by the marketing navbar.
- Preserves: sample-data disclosure and exact protocol event names.

- [ ] **Step 1: Implement the responsive compressing navbar and hero**

Use Motion's `useScroll`/`useMotionValueEvent` pattern from the ZolliAI
reference. Desktop compresses after 100px; mobile uses an accessible menu with
`aria-expanded`, escape/selection close behavior, and the same real links.
The hero renders the approved headline, two actions, all six characters, and
only transform/opacity motion.

- [ ] **Step 2: Implement the four-step Crew story and six-role section**

Desktop may pin the step sequence; mobile must be ordinary document flow.
Every role card consumes `ZOLLI_ROLES` and `ZolliCharacter` rather than
duplicating role copy or SVG.

- [ ] **Step 3: Retheme recovery proof and closing footer**

Render friendly event sentences as the primary line and protocol names as
secondary mono data. Keep the sample-run disclosure. Close with “Give every
machine a role in the crew” and links to create/open a Crew, docs, GitHub, and
existing product routes. Do not invent privacy/terms routes in this visual POC
and do not claim live system status.

- [ ] **Step 4: Verify behavior and compile**

Run: `cd flashml-cloud/apps/web && npm test`

Expected: all web tests pass.

Run: `cd flashml-cloud/apps/web && npx tsc --noEmit`

Expected: exit 0.

- [ ] **Step 5: Orchestrator visual review and commit**

Review at 1440px, 768px, and 390px. Stage only Task 3 paths and commit:

```bash
git commit -m "feat(web): introduce the Zolli Crew landing"
```

### Task 4: Rebrand the app shell, authentication, and entry states

**Files:**
- Modify: all files listed under workstream B.

**Interfaces:**
- Consumes: `Wordmark`, `ZolliCharacter`, `ZOLLI_ROLES`, warm theme tokens.
- Preserves: `WORKSPACE_TABS`, `workspacePath`, `workspaceIdFromPath`, access gating, auth callbacks, invite tokens, and all API calls.
- Produces: visible navigation labels `Crew`, `Zollis`, `My Zollis`, and `Build your crew` without renaming route or API identifiers.

- [ ] **Step 1: Capture the owned-file diff before editing**

Run `git diff --` over the workstream-B paths and save the output path in the
task report. This is the evidence used to preserve the user's existing changes.

- [ ] **Step 2: Reskin the shell while preserving the user's dirty changes**

Keep the current access-state fetch, admin loading behavior, workspace hint,
mobile drawer, and personal-machine ownership semantics. Apply the warm rail,
Crew switcher, Zolli labels, orange active state, and warm hover/focus classes.
Add a compact Scout card linking to the existing activation flow.

- [ ] **Step 3: Reskin auth, onboarding, and access screens**

Use one character per state: Scout for signup/enrollment, Keeper for pending,
and a direct non-playful declined state. Keep every field, validation rule,
notice, redirect, and submission call intact. Change presentation and safe copy
only.

- [ ] **Step 4: Reskin activation, join, and Crew selection screens**

Use Crew/Zolli visible language but preserve exact enrollment commands, tokens,
pool IDs, URLs, and API error meanings. Never replace a technical string inside
`code`, CLI blocks, or request objects.

- [ ] **Step 5: Verify existing behavior and TypeScript**

Run: `cd flashml-cloud/apps/web && npm test`

Expected: all web tests pass, including the pre-existing access and onboarding tests.

Run: `cd flashml-cloud/apps/web && npx tsc --noEmit`

Expected: exit 0.

- [ ] **Step 6: Orchestrator behavior review and commit**

Diff against the pre-task worktree to prove no access/auth behavior changed.
Stage only Task 4 paths and commit:

```bash
git commit -m "feat(web): rebrand the ZolliAI entry experience"
```

### Task 5: Reskin all Crew, job, Zolli, account, docs, and admin pages

**Files:**
- Modify: all files listed under workstream C.

**Interfaces:**
- Consumes: brand primitives and theme tokens from Tasks 1–2.
- Preserves: every component prop, provider, API request, query parameter,
  route segment, event name, ID, and command string.
- Produces: coherent warm panels, tables, forms, empty states, and safe visible
  Crew/Zolli vocabulary across all remaining frontend routes.

- [ ] **Step 1: Capture the owned-file diff before editing**

Run `git diff --` over the workstream-C paths and save the output path in the
task report. Preserve every pre-existing identity, pluralization, account, and
workspace change.

- [ ] **Step 2: Reskin Crew-scoped routes and shared workspace components**

Convert headings, descriptions, breadcrumbs, empty states, and button labels to
Crew/Zolli language. Retain internal variable names and API calls. Preserve the
user's current identity/pluralization fixes in `MemberTable.tsx`,
`WorkspaceHeader.tsx`, `member-identity.ts`, and `plural.ts`.

- [ ] **Step 3: Reskin jobs and visualization components**

Use warm neutral data surfaces and semantic status colors. Keep event names,
job IDs, loss values, credit counts, graph behavior, and topology logic exact.
Characters may appear only in an empty state or recovery summary, not inside
the topology, swimlanes, or ledger rows.

- [ ] **Step 4: Reskin personal Zollis, account, admin, docs, error, and 404**

Preserve form fields, access explanations, admin decisions, and enrollment
commands. Use Scout for no-Zolli states and restrained logo/character art for
404. Errors remain direct and actionable.

- [ ] **Step 5: Verify existing behavior and TypeScript**

Run: `cd flashml-cloud/apps/web && npm test`

Expected: all web tests pass, including job, workspace, invite, identity, and route tests.

Run: `cd flashml-cloud/apps/web && npx tsc --noEmit`

Expected: exit 0.

- [ ] **Step 6: Orchestrator behavior review and commit**

Inspect route/API diffs and stage only Task 5 paths. Commit:

```bash
git commit -m "feat(web): apply the Zolli Crew console system"
```

### Task 6: Integrate, audit visible copy, and remove dark-theme residue

**Files:**
- Modify: only frontend files required by concrete failures found in this audit.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: an allowlisted proof that stale visible copy remains only in
  technical contexts and that no incompatible dark-only utility remains on a
  user-facing warm surface.

- [ ] **Step 1: Audit visible vocabulary and dark-only utilities**

Run `rg -n 'FlashML|Workspace|My machines|Machines|bg-black|bg-white/\\[|text-white/\\[|border-white/\\[' app components -g '*.tsx'`.
Inspect every match and record the technical/visible classification in the task
report. This is a review artifact, not a permanent source-grep test.

- [ ] **Step 2: Fix only confirmed visual residue**

For each failure, classify it as visible brand copy, technical vocabulary, a
comment, or a valid overlay. Change visible brand copy; add narrow allowlist
entries for exact technical uses; replace dark-only utility classes with theme
tokens. Do not blanket-replace source text.

- [ ] **Step 3: Run the complete static verification**

Run: `cd flashml-cloud/apps/web && npm test`

Expected: all tests pass.

Run: `cd flashml-cloud/apps/web && npm run lint`

Expected: exit 0 with no errors.

Run: `cd flashml-cloud/apps/web && npx tsc --noEmit`

Expected: exit 0.

Run: `cd flashml-cloud/apps/web && npm run build`

Expected: production build exits 0 and lists all existing routes.

- [ ] **Step 4: Orchestrator review and commit**

Review the complete diff for backend/API/route changes and commit the exact
integration files:

```bash
git commit -m "test(web): audit ZolliAI visual rebrand"
```

### Task 7: Browser QA and final documentation

**Files:**
- Modify: `PROGRESS.md`
- Modify: frontend files only when a browser-reproduced visual/accessibility defect requires it.

**Interfaces:**
- Consumes: the integrated application.
- Produces: evidence for desktop, tablet, phone, reduced-motion, and keyboard behavior.

- [ ] **Step 1: Start the real development stack**

From the repository root, load the documented development environment and run
`./scripts/dev.sh --all`. Confirm the landing page responds before QA.

- [ ] **Step 2: Test the public site at three widths**

Check 1440×1000, 768×1024, and 390×844. Verify navbar compression/menu,
headline wrapping, character visibility, anchor navigation, recovery sequence,
CTA routes, and horizontal overflow.

- [ ] **Step 3: Test console and edge states**

Verify sign-in, onboarding/pending screens available in the environment, Crew
switcher, overview, jobs, job detail, Zollis, people, settings, account, docs,
admin route behavior, 404, loading, empty, and error surfaces. Confirm existing
actions still invoke their original flows.

- [ ] **Step 4: Test accessibility and motion**

Navigate by keyboard from the skip link through nav and primary actions. Check
visible focus, menu escape behavior, meaningful image names, status not encoded
by color alone, reduced-motion rendering, and contrast on warm surfaces.

- [ ] **Step 5: Re-run final verification after QA fixes**

Run fresh `npm test`, `npm run lint`, `npx tsc --noEmit`, and `npm run build`.
Record exact counts and exit results.

- [ ] **Step 6: Update the progress log**

Add one newest-first `2026-08-05` entry describing the visual-only rebrand,
explicitly naming the deferred underlying rebrand, exact automated test counts,
build result, browser widths, and any environmental limitations. Do not claim a
state that was not observed.

- [ ] **Step 7: Final orchestrator commit**

Stage only verified QA fixes and `PROGRESS.md`, then commit:

```bash
git commit -m "docs: record ZolliAI visual POC verification"
```
