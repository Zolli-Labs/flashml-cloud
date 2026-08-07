# ZolliAI Auth Visual Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reproduce the ZolliAI repository's split-screen authentication design on the existing ZolliAI Cloud `/sign-in` route without changing auth behavior.

**Architecture:** Add three focused client components for auth interaction state, the four-character scene, and the responsive shell. Wrap the existing `SignInCard` credentials UI with those components and publish its existing input/reveal state to the scene. Keep every Supabase call and redirect in `SignInCard` unchanged.

**Tech Stack:** Next.js 16 App Router, React 19, Tailwind CSS v4, `motion/react`, Supabase SSR/browser auth.

## Global Constraints

- Visual-only: no new fields, routes, API calls, migrations, dependencies, or auth behavior.
- Preserve `signInWithPassword`, `signUp`, Google OAuth, `safeNext`, error translation, confirmation recovery, and onboarding.
- Reuse the source repository's composition and character behavior, adapted to existing ZolliAI Cloud tokens.
- Honor reduced motion, keyboard focus, autocomplete, labels, and mobile overflow.
- Do not touch the user's existing unrelated dirty files.

---

### Task 1: Port the ZolliAI auth visual primitives

**Files:**
- Create: `flashml-cloud/apps/web/components/auth/AuthInteraction.tsx`
- Create: `flashml-cloud/apps/web/components/auth/AuthCharacters.tsx`
- Create: `flashml-cloud/apps/web/components/auth/AuthShell.tsx`

**Interfaces:**
- Produces: `AuthInteractionProvider`, `useAuthInteraction()`, `AuthCharacters`, and `AuthShell({ mode, onModeChange, children })`.
- Consumes: existing ZolliAI color/font utilities, `Wordmark`, `motion/react`, and `next/link`.

- [ ] **Step 1: Add interaction state**

Port the source repository's three values—`typing`, `hasPassword`, and
`passwordVisible`—into `AuthInteraction.tsx`, with setters exposed by
`useAuthInteraction()`.

- [ ] **Step 2: Port the character scene**

Port the four source characters, pointer-following eyes/bodies, blink timing,
typing/password reactions, and contextual caption. Replace `framer-motion`
imports with `motion/react` and disable pointer/blink timers when reduced motion
is preferred.

- [ ] **Step 3: Build the responsive shell**

Port the 50/50 layout, aurora backdrop, wordmark, Back home link, distributed
compute badge, mode-specific headlines, mobile header, segmented control, and
form card. Use buttons for mode switching because both states remain on
`/sign-in`.

- [ ] **Step 4: Run static checks**

Run `cd flashml-cloud/apps/web && npx tsc --noEmit && npm run lint`.
Expected: exit 0.

### Task 2: Integrate the existing auth form and verify

**Files:**
- Modify: `flashml-cloud/apps/web/app/(auth)/sign-in/SignInCard.tsx`
- Modify: `flashml-cloud/apps/web/app/(auth)/sign-in/page.tsx`

**Interfaces:**
- Consumes: Task 1's provider, hook, and shell.
- Preserves: all existing Supabase calls, payloads, redirects, notices, and onboarding transitions.

- [ ] **Step 1: Wrap the credentials state**

Render the existing credentials form inside `AuthInteractionProvider` and
`AuthShell`. Feed the existing `mode` state to the segmented switcher and reuse
the current reset logic when switching modes.

- [ ] **Step 2: Publish form interaction without changing submissions**

On email/password focus and blur, update `typing`; on password input update
`hasPassword`; on reveal update `passwordVisible`. Do not change
`submitPassword`, `signInWithGoogle`, `safeNext`, or their payloads.

- [ ] **Step 3: Preserve non-credential screens**

Keep onboarding and email-confirmation states readable in a centered warm
surface even though the page-level credentials wrapper becomes full viewport.

- [ ] **Step 4: Run the complete verification**

Run `npm test`, `npm run lint`, `npx tsc --noEmit`, and a production build with
the documented public API environment. Expected: 23 test files / 243 tests,
zero lint/type errors, and 19/19 static pages.

- [ ] **Step 5: Browser QA**

At 1440×1000 and 390×844, verify sign-in/sign-up switching, password reveal,
Google button, focus order, no horizontal overflow, character visibility,
reduced-motion source behavior, and no browser console errors.

- [ ] **Step 6: Commit**

Stage only the five auth files and commit with:

```bash
git commit -m "feat(web): port ZolliAI auth experience"
```
