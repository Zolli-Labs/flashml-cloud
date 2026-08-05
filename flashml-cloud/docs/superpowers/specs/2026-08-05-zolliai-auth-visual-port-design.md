# ZolliAI Auth Visual Port Design

## Goal

Port the visual composition of `PhongCT1105/ZolliAI`'s authentication pages
into ZolliAI Cloud's existing `/sign-in` route without changing authentication
behavior, routes, fields, onboarding, or backend contracts.

## Source and scope

The visual source is the ZolliAI repository's `AuthShell.tsx`,
`AuthCharacters.tsx`, `interaction.tsx`, and `fields.tsx`. Reuse its split
layout, warm aurora panel, segmented mode control, elevated form card, and
interactive four-character Crew. Adapt imports and tokens to this repository's
existing `motion/react`, Fraunces/Inter typography, and Zolli brand palette.

This is visual-only. Keep the current single `/sign-in` route and its local
`signin`/`signup` mode state. Preserve `signInWithPassword`, `signUp`, Google
OAuth, `safeNext`, readable auth errors, email-confirmation recovery, and the
existing onboarding step. Do not add full-name or confirmation-password fields,
waitlist enforcement, API calls, migrations, dependencies, or new routes.

## Responsive and interaction design

At `lg` and above, use a 50/50 viewport split. The left panel contains the
ZolliAI wordmark, Back home link, distributed-compute badge, mode-specific
headline, contextual caption, and four characters anchored to the bottom. The
right panel contains the segmented mode switch and the existing form inside a
large warm white card.

Below `lg`, hide the large character scene and render a compact wordmark/Back
home bar above the switcher and form. All controls remain keyboard reachable.
Character pointer tracking, blinking, password-aware reactions, and form
entrance motion must honor `prefers-reduced-motion`.

## Copy

- Sign in: “The Crew's been waiting for you.” / “Welcome back.”
- Sign up: “Say hello to your new Crew.” / “Build your Crew.”
- Badge: “Distributed compute, one resilient Crew”
- Preserve the current form helper, Google, error, confirmation, and onboarding
  copy where it describes real product behavior.

## Verification

Run Vitest, ESLint, TypeScript, and the production build. Browser-check sign-in
and sign-up at desktop and phone widths, including mode switching, password
visibility, focus order, reduced-motion fallback, and horizontal overflow.
