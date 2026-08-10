# Zolli warm technical rebrand design

**Date:** 2026-08-09

**Status:** draft for owner review; visual direction approved from the standalone HTML preview

**Scope:** `apps/web` marketing, authentication, onboarding, console, and shared frontend presentation

**Supersedes:** the visual language, character system, and motion direction in
`2026-08-05-zolliai-visual-rebrand-design.md`

**Preserves:** the previous specification's implementation boundary. This is a
frontend presentation change, not an API, route, data, runtime, deployment, or
authorization redesign.

**Approved visual reference:**
`~/.gstack/projects/Zolli-Labs-flashml-cloud/designs/zolli-style-preview-20260809/finalized.html`

**Reference SHA-256:**
`656be8c695afb0437ec0f3f2fd137221c3dd53262b91b52108582b78ab4e8e0c`

---

## 1. Objective

Rebrand the complete Zolli Cloud frontend from the current warm,
character-led editorial treatment to the approved **warm technical
minimalist** system.

The new presentation must communicate:

1. Zolli is infrastructure for fault-tolerant distributed compute.
2. The product is observable, deterministic, and trustworthy.
3. Zolli has a recognizable orange signature without looking playful,
   consumer-oriented, or like a generic purple AI platform.
4. The operational proof is the product: leases, heartbeats, verified
   checkpoints, requeueing, accepted commits, and goodput.

The memorable impression is:

> **Graphite infrastructure, one orange network signal, and real system
> evidence.**

The implementation is complete when every public and authenticated frontend
surface belongs to this system and none of the current character-led or
decorative-motion treatment remains in normal product use.

## 2. Why this rebrand is necessary

The 2026-08-05 visual POC was executed as designed, but its source language
was the retired Zolli real-estate CRM. Warm cream surfaces, editorial serif
headings, large rounded cards, six character roles, “Crew” metaphors, magnetic
links, aurora backgrounds, and staged reveals make the compute platform feel
friendly but not operationally serious.

The current code confirms that this is a system-level issue rather than one
hero-section problem:

- `app/layout.tsx` loads Inter, Fraunces, and Geist Mono and applies a fixed
  paper grain to the entire app.
- `app/globals.css` defines the cream/sand palette, glass surfaces, generous
  radii, shadows, and character-era compatibility tokens.
- 12 frontend files import `motion/react`.
- Character presentation reaches landing, auth, onboarding, activation,
  workspace fleet, and console shell surfaces.
- The app has 24 routed `page.tsx` files, so changing only `/` would leave two
  visual products on either side of sign-in.

The page structure and working interaction model are not the problem. This
spec preserves them and replaces the visual register, vocabulary boundary,
illustration system, and motion discipline.

## 3. Product and naming architecture

### 3.1 Visible brand

- **Zolli** is the company brand.
- **Zolli Cloud** is the hosted compute product.
- **FlashML** remains the public runtime and protocol name where the technical
  distinction matters.
- Keep the existing connected-node Zolli symbol and wordmark. The symbol fits
  the distributed-compute product; the character system does not.

### 3.2 User-facing object names

The brand must not rename normal infrastructure objects into mascots.

| Concept | Required UI term | Internal/API term |
|---|---|---|
| Team boundary | Workspace | pool |
| Connected host | Machine | node / machine |
| Executing host | Worker | node / attempt owner |
| Submitted work | Job | job |
| Schedulable unit | Task | task |
| Bounded ownership | Lease | lease |
| Durable progress | Checkpoint | checkpoint manifest |

The normal UI must not call a machine a “Zolli” or a workspace a “Crew.” The
terms Captain, Scout, Keeper, Relay, Builder, and Crew are removed from primary
product copy. Existing API payloads, database fields, route helpers, and
protocol event names remain unchanged.

### 3.3 Copy posture

Copy is direct, technical, and evidence-led:

- Prefer mechanism names over adjectives.
- Prefer “verified checkpoint” to “safely saved progress.”
- Prefer “lease expired and task requeued” to “the Crew kept moving.”
- Never claim speed where the product evidence is fault tolerance.
- Never imply synchronous training across arbitrary machines.
- Never invent customer logos, uptime numbers, savings claims, or benchmarks.
- Keep alpha limitations and sample-data disclosures visible.

## 4. Scope and boundaries

### 4.1 In scope

- Global design tokens, font loading, selection, focus, shadows, radii, and
  shared utilities in `app/globals.css` and `app/layout.tsx`.
- Marketing navigation and all sections currently rendered by
  `app/(marketing)/page.tsx`.
- Sign-in and sign-up presentation.
- Access-request, pending, declined, activation, join, and empty states.
- Console rail, top bar, workspace switcher, command palette, status pill,
  toasts, banners, dialogs, tables, forms, metrics, job detail, topology,
  documentation, and admin pages.
- Brand components and the visible vocabulary adapters.
- Favicon/manifest theme colors and social preview artwork.
- Responsive, keyboard, reduced-motion, and contrast behavior.
- Structural/accessibility tests for shared visual primitives and updated copy
  contracts.

### 4.2 Explicitly out of scope

- Backend, database, migration, API, protocol, or coordinator changes.
- Route, permission, session, admission, workspace, invite, job, machine, or
  billing behavior changes.
- A new navigation hierarchy or new product pages.
- Underlying repository, package, CLI, environment-variable, deployment, or
  service renames.
- New pricing, testimonials, customer logos, uptime promises, or performance
  claims.
- Deleting the existing character image files. They may remain as unused brand
  archive assets until a separate cleanup is approved.
- Rebuilding the existing Zolli logo.
- A user-selectable application theme. Marketing is dark and the operational
  console is light by design.

### 4.3 Behavioral invariant

This rebrand must be behavior-neutral. If a route or action works before the
visual migration, it must work afterward with the same URL, request payload,
response handling, authorization check, error path, and keyboard behavior.

## 5. Design direction

The system has two coordinated registers.

### 5.1 Marketing: graphite infrastructure

The public landing page uses near-black graphite surfaces, off-white type,
hairline borders, and one restrained orange signal. Atmosphere is permitted
only as a subtle radial orange tint in the hero. Product proof, not
illustration, supplies visual interest.

### 5.2 Product: quiet operational light

Authentication forms and the signed-in console use warm mineral-white
surfaces with a dark graphite rail where applicable. Data remains flat,
opaque, and high contrast. No glass, texture, glow, or decorative movement
sits behind operational information.

### 5.3 Coherence rule

The registers share:

- the Zolli orange;
- Instrument Sans and Geist Mono;
- the same spacing and radius scale;
- the same hairline border logic;
- the same semantic status colors;
- the connected-node mark;
- the rule that machine-produced information is monospaced.

## 6. Exact color system

### 6.1 Dark marketing tokens

```css
--z-bg: #0b0d0e;
--z-surface: #111416;
--z-surface-raised: #171a1d;
--z-surface-hover: #1d2125;
--z-border: #292e33;
--z-border-strong: #3a4046;

--z-text: #f3f1ec;
--z-text-secondary: #a5a39e;
--z-text-dim: #6d706f;

--z-orange: #f36b32;
--z-orange-bright: #ff7427;
--z-orange-muted: #2b1912;
```

`#f36b32` is the interaction accent approved in the preview. The existing
brand asset orange `#ff7427` remains the brighter mark/hover value. Do not
introduce a third orange.

### 6.2 Light console tokens

```css
--z-app-bg: #f1efe9;
--z-app-canvas: #f7f6f2;
--z-app-surface: #fbfaf7;
--z-app-surface-hover: #f0eee8;
--z-app-border: #ddd9d1;
--z-app-border-strong: #cfcac0;

--z-app-text: #1c1c1b;
--z-app-text-secondary: #6f706d;
--z-app-text-dim: #8a8b87;
--z-app-rail: #111416;
--z-app-rail-active: #202428;
```

### 6.3 Semantic colors

```css
--z-healthy: #4ba77b;
--z-warning: #d5a33f;
--z-failure: #d6665e;
--z-info: #6e95b8;
```

Semantic colors are reserved for state. Orange means Zolli interaction,
selection, or network flow; it must not mean success, warning, or failure.
Every semantic state also carries a label or icon.

### 6.4 Color prohibitions

- No purple or blue default gradient.
- No cream-and-sand marketing canvas.
- No rainbow role colors.
- No glowing text.
- No orange wash behind whole sections.
- No transparency behind tables, logs, metrics, errors, or form copy.

## 7. Typography

### 7.1 Families

- **Display, body, controls, and human-authored copy:** Instrument Sans,
  weights 400–700.
- **Machine-authored output:** Geist Mono, weights 400–600.
- Remove Fraunces and Inter from `app/layout.tsx`.
- Remove `font-display` usage. Headings use the sans family.

The typography rule is:

> If a human wrote it, Instrument Sans. If the system emitted it, Geist
> Mono.

### 7.2 Type scale

| Role | Desktop | Mobile | Weight | Line height | Tracking |
|---|---:|---:|---:|---:|---:|
| Marketing hero | `clamp(58px, 7.3vw, 106px)` | `46–64px` | 590–600 | `0.91` | `-0.062em` |
| Marketing section | `clamp(42px, 5.4vw, 76px)` | `40–46px` | 560–600 | `0.99` | `-0.052em` |
| Console page title | `28–34px` | `24–28px` | 600 | `1.05` | `-0.035em` |
| Body lead | `17–18px` | `15–16px` | 400 | `1.55–1.60` | `-0.01em` |
| Body/UI | `13–16px` | `13–16px` | 400–600 | `1.45–1.55` | normal |
| Eyebrow | `11px` | `10–11px` | 500 | `1` | `0.13em` |
| Machine label | `9–12px` | `9–11px` | 400–600 | `1.4–1.5` | normal |

Headlines remain sentence case. Paragraphs use a maximum readable line length
of approximately 58–65 characters.

## 8. Spacing, shape, borders, and elevation

### 8.1 Spacing

Use a 4px base unit:

```text
2, 4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 80, 112
```

Marketing sections use 80–112px vertical space on desktop and 64–84px on
mobile. Console surfaces use compact 12–28px internal spacing.

### 8.2 Radius

```css
--z-radius-sm: 4px;   /* badges, status labels */
--z-radius-md: 7px;   /* buttons, inputs, node cards */
--z-radius-lg: 10px;  /* large panels and previews */
```

`999px` pills are allowed only for a true status/count chip. Marketing CTAs,
navigation items, auth controls, cards, inputs, and console actions must not
default to pills.

### 8.3 Borders and elevation

- Primary depth mechanism: opaque surface step plus a 1px border.
- Marketing panels use `--z-border` or `--z-border-strong`.
- Console panels use `--z-app-border` or `--z-app-border-strong`.
- Shadows are limited to menus, dialogs, command palette, and toasts that
  physically overlay other content.
- Remove grain, glass, glow, spotlight, and generic card shadows.

## 9. Brand and illustration

### 9.1 Keep

- Connected-node Zolli symbol.
- Existing horizontal Zolli wordmark where it is legible.
- Reversed white wordmark on dark surfaces.
- Existing favicon/app icon if it contains only the mark.

### 9.2 Remove from normal product use

- Six-character hero lineup.
- Captain, Worker, Scout, Keeper, Relay, and Builder illustrations.
- Character eye tracking, bobbing, bouncing, waving, or mode transitions.
- Character-led auth, onboarding, activation, empty-state, and recovery
  explanations.

Character image files remain untouched as archived source assets. The runtime
character system is retired: delete `ZolliCharacter.tsx`,
`AuthCharacters.tsx`, and `lib/zolli-brand.ts` after their call sites are
replaced. Rewrite the associated brand tests so they verify only the retained
mark and wordmark contracts. Do not leave unused character React modules or
role definitions in the production source tree.

### 9.3 Replacement visual language

Use only visuals that explain the system:

- live-looking topology diagrams driven by real node/lease state;
- event-ledger rows using real protocol event names;
- code or configuration excerpts;
- job progress, goodput, storage, recovery, and contribution metrics;
- thin-line Phosphor icons;
- product screenshots;
- compact diagrams with labelled connections.

The marketing hero's primary visual is a topology plus event ledger, based on
the approved preview. It must not be a generic server illustration.

## 10. Marketing application

The current marketing structure and narrative order remain, with character-era
component names replaced by technical names:

```text
Navbar
Hero
SystemStory
SystemModules
RecoveryDemo
ClosingCta
```

Rename `CrewStory.tsx` to `SystemStory.tsx` and `CrewRoles.tsx` to
`SystemModules.tsx`; move their imports and tests in the same slice. The
section order, CTA destinations, and short one-page narrative remain.

### 10.1 Navigation

- Stable sticky graphite bar with a hairline bottom border.
- No scroll compression, floating rounded glass state, or vertical motion.
- Desktop links: How it works (`#how-it-works`), Compute (`#compute`), and Open
  runtime (the existing public repository URL).
- The renamed module section uses `id="compute"`. Keep `#crew` as a non-visible
  legacy fragment alias that lands on the same section so old shared links do
  not break; no navigation or visible copy uses the word “Crew.”
- Primary action: Open console, linking to `/workspaces`. Existing middleware
  remains responsible for sending signed-out users through sign-in.
- Mobile menu keeps the current Escape, focus-return, `aria-expanded`, and
  keyboard behavior.
- The connected-node mark and Zolli Cloud wordmark stay left aligned.

### 10.2 Hero

Preserve the current hero's headline, support copy, two actions, and proof
area. Apply the approved content direction:

> **Compute that finishes the job.**

Supporting copy:

> Pool cloud GPUs, home rigs, and spare machines. Zolli leases work,
> verifies progress, and resumes from checkpoints when a node disappears.

The proof area replaces the character lineup with:

- a coordinator and four representative machines;
- mixed supply labels;
- live, warning, and recovering states;
- a real event ledger containing `LEASE_CLAIMED`,
  `NODE_HEARTBEAT_LOST`, `CHECKPOINT_MANIFEST_COMMITTED` or the exact
  currently emitted checkpoint event, `TASK_REQUEUED`, and
  `TASK_COMMIT_ACCEPTED`;
- an explicit sample-data label until backed by a captured run.

The current `SAMPLE_LEDGER` remains acceptable if every event is a real
protocol member and sample status is visible.

Hero actions are fixed:

- primary: **Open the console** → `/workspaces`;
- secondary: **Inspect recovery** → `#recover`.

### 10.3 How the system works

Preserve the current split or sticky narrative structure, but replace
character cards with three horizontally ruled operational stages:

1. `LEASE_CLAIMED`: a worker claims work for a bounded window.
2. `NODE_HEARTBEAT_LOST` and `LEASE_EXPIRED`: the interruption becomes an
   auditable state transition.
3. `TASK_REQUEUED` and `TASK_COMMIT_ACCEPTED`: another worker resumes from
   verified progress and one result wins.

Desktop may keep a sticky explanatory column. Mobile becomes a simple
vertical sequence.

### 10.4 System roles

Preserve the current six-item section structure, but convert it from mascot
roles to technical system modules:

| Module | What the block explains |
|---|---|
| Coordinate | task expansion, scheduling, and bounded leases |
| Execute | workers claim and run tasks |
| Enroll | machines join through the existing activation flow |
| Checkpoint | parts verify before a manifest is committed |
| Recover | expiry, requeue, and controlled restart |
| Verify | hashes and idempotent commits determine accepted work |

Each module uses one thin-line icon, a one-word title, one mechanism-level
sentence, and an optional real event or protocol name. No large illustration,
spotlight card, hover-follow effect, or rainbow color coding.

### 10.5 Recovery proof

Retain the existing event-ledger proof and disclosure. Remove Relay character
art and friendly handoff copy. Present:

- narrative sentence first;
- exact event type second;
- actor/task/lease ID where available;
- timestamp;
- semantic status;
- sample or live provenance.

### 10.6 Closing action and footer

Approved closing direction:

> **Bring the fleet. Keep the progress.**

Keep two actions: **Open the console** → `/workspaces` and **Read the runtime
docs** → the existing public runtime documentation/repository destination.
Footer retains company, open-runtime, documentation, legal, and system-status
links.

## 11. Authentication and onboarding

### 11.1 Authentication

Replace the current animated aurora and character stage with a restrained
split layout:

- dark graphite evidence panel on desktop;
- warm mineral-white form panel;
- one compact topology, ledger excerpt, or protocol statement on the dark
  side;
- no character illustration;
- no continuous animation;
- no 28px form-card radius or pill mode switch;
- preserve all sign-in/sign-up behavior, Google failure handling, password
  messaging, next-route safety, errors, and keyboard flow.

At mobile widths, collapse to a single light form surface with a compact dark
brand header. Touch targets remain at least 44px.

### 11.2 Access request and waiting states

- Preserve the current forms and state machine.
- Replace mascot illustrations with a status icon, direct heading, exact next
  action, and optional request metadata.
- Pending must read as an operational queue state, not a playful waiting
  scene.
- Declined and error states remain direct and unambiguous.

### 11.3 Activation, machine enrollment, and empty states

- Use Machine consistently in visible copy.
- Keep exact CLI commands, device codes, URLs, and technical identifiers.
- Replace Scout imagery with a terminal/code panel or thin-line machine icon.
- Empty states use one icon, one sentence, and one primary action.

## 12. Console application

### 12.1 Shell

Use the approved quiet light canvas with a dark graphite left rail:

- rail: `--z-app-rail`;
- active navigation: opaque `--z-app-rail-active` with a small orange dot or
  orange icon, not an orange background;
- content canvas: `--z-app-canvas`;
- cards/tables: opaque `--z-app-surface` with 1px borders;
- top bar: stable, flat, and separated by a hairline;
- fleet status stays permanently visible;
- mobile drawer preserves current behavior.

Remove the Scout CTA character from the rail. Replace it with a compact bordered
“Add machine” row using the existing route.

### 12.2 Data presentation

- Human labels and descriptions use Instrument Sans.
- IDs, hashes, timestamps, durations, event names, state names, counts, and
  metrics use Geist Mono with tabular figures.
- Tables do not use shadows or rounded row containers.
- Status uses semantic color plus explicit text.
- Numbers never sit on translucent surfaces.
- Dense pages remain dense; the rebrand must not turn tables into card grids.

### 12.3 Route coverage

The migration covers all 24 current routed pages, including:

- marketing and sign-in;
- overview, account, and personal machines;
- activation and workspace join;
- workspaces/pools and every `/w/[poolId]` tab;
- jobs list and job detail;
- submit;
- metrics;
- documentation and how-it-works;
- admin requests;
- global error and not-found states.

No route is complete if it still depends visually on cream/sand marketing
tokens, Fraunces, character art, glass, grain, oversized radii, or decorative
motion.

### 12.4 Functional diagrams

Existing functional motion in `FleetTopology`, progress bars, lease rings,
event append behavior, and state transitions may remain when it communicates
real state. Decorative drifting nodes and arbitrary animated dashed edges must
be removed. A live edge may animate only when backed by an actual live state
or clearly labelled sample playback.

## 13. Component contracts

### 13.1 Buttons

- Primary: orange fill, dark text, 6–7px radius.
- Secondary: graphite/light surface with a visible border.
- Destructive: semantic red treatment, never orange.
- Minimum touch target: 44px on mobile; 40px on desktop.
- No gradient button, glow, magnetic following, or default pill.

### 13.2 Inputs and selects

- Opaque surface, 1px border, 6–7px radius.
- Focus uses a high-contrast orange ring and must not rely on color alone.
- Error state uses red plus message text.
- Placeholder text meets readable contrast or is non-essential.

### 13.3 Cards and panels

- Cards exist only where containment clarifies hierarchy.
- Default is border plus surface step, not shadow.
- Maximum default radius: 10px.
- Section layouts may use open ruled rows instead of a card for every item.

### 13.4 Badges and statuses

- 4px radius; a full pill is allowed only when the content is genuinely a
  compact status/count.
- 9–11px Geist Mono.
- State text is mandatory.

### 13.5 Overlays

Dialogs, command palette, menus, drawers, and toasts may use a restrained
shadow because they overlay other content. They remain opaque and use the
same border/radius scale.

## 14. Motion

### 14.1 Timing

```css
--z-motion-fast: 120ms;
--z-motion-ui: 180ms;
--z-motion-panel: 240ms;
--z-motion-ease: cubic-bezier(0.16, 1, 0.3, 1);
```

### 14.2 Allowed

- Hover and pressed feedback.
- Menu/dialog/drawer enter and exit.
- Navigation selection.
- Progress changes.
- Ledger events arriving.
- Job, lease, checkpoint, and machine state transitions.
- One restrained topology signal in the marketing hero.

### 14.3 Prohibited

- Magnetic links.
- Generic section reveal-on-scroll.
- Character bobbing, bouncing, blinking, eye tracking, or waving.
- Floating/compressing navigation.
- Spotlight cards that follow the pointer.
- Animated aurora or blur fields.
- Decorative node drift.
- Continuous animation unrelated to real or labelled sample state.

`prefers-reduced-motion` must reduce all non-essential transitions to near-zero
while preserving state and content.

The `motion` dependency may remain if functional components still require it.
After call sites migrate, delete unused motion helpers rather than preserving
dead abstractions.

## 15. Accessibility and responsive behavior

### 15.1 Accessibility

- Preserve the skip link and current heading hierarchy.
- Meet WCAG AA contrast for normal text and controls in both registers.
- Focus is always visible on dark and light surfaces.
- Do not communicate health, warning, failure, selection, or live state by
  color alone.
- Decorative topology lines and icons are hidden from assistive technology.
- Meaningful diagrams receive concise accessible labels or equivalent text.
- Mobile navigation keeps `aria-expanded`, Escape close, and focus return.
- Authentication tabs keep their current semantic selected/pressed state.
- Tables retain real table semantics where the data is tabular.

### 15.2 Responsive checkpoints

Verify at minimum:

- 1440×900 desktop;
- 1024×768 compact desktop/tablet landscape;
- 768×1024 tablet;
- 390×844 phone;
- 375×667 short phone.

At narrow widths:

- large headings reflow without clipping;
- topology becomes vertically spacious rather than unreadably small;
- ledger stacks below topology;
- recovery rows become vertical;
- console rail becomes the existing mobile drawer;
- tables preserve critical columns and provide intentional horizontal
  scrolling or responsive disclosure for the rest;
- there is no horizontal page overflow.

## 16. Implementation map

### 16.1 Foundation first

Primary files:

- `apps/web/app/globals.css`
- `apps/web/app/layout.tsx`
- `apps/web/public/brand/brand-tokens.json`
- `apps/web/components/brand/Mark.tsx`
- shared `components/ui/*` primitives where their defaults conflict with
  this specification

Required outcome:

- new dark and light token registers;
- Instrument Sans + Geist Mono;
- no global grain;
- no default glass/glow utilities;
- shared radii, border, focus, shadow, and motion values.

### 16.2 Shared chrome

Primary files:

- `components/nav/Navbar.tsx`
- `components/shell/ConsoleShell.tsx`
- `components/shell/FleetPill.tsx`
- `components/shell/WorkspaceSwitcher.tsx`
- `components/shell/CommandPalette.tsx`
- `components/nav/UserMenu.tsx`

Required outcome: stable dark marketing navigation, dark console rail, light
operational canvas, unchanged route behavior.

### 16.3 Marketing

Primary files:

- `app/(marketing)/page.tsx`
- `components/landing/Hero.tsx`
- `components/landing/SystemStory.tsx` (renamed from `CrewStory.tsx`)
- `components/landing/SystemModules.tsx` (renamed from `CrewRoles.tsx`)
- `components/landing/RecoveryDemo.tsx`
- `components/landing/EventLedger.tsx`
- `components/landing/ClosingCta.tsx`
- `lib/landing/sample-ledger.ts`

Remove the old `CrewStory.tsx` and `CrewRoles.tsx` paths after imports and tests
move. The legacy `#crew` fragment alias is the only retained marketing use of
that term.

### 16.4 Auth, onboarding, and access states

Primary files:

- `components/auth/AuthShell.tsx`
- `app/(auth)/sign-in/SignInCard.tsx`
- `components/onboarding/*`
- activation and join pages
- relevant empty/error states

Delete `AuthCharacters.tsx`, `ZolliCharacter.tsx`, and `lib/zolli-brand.ts`
after their production call sites are replaced. Update or replace
`lib/zolli-brand.test.ts` in the same slice so the retained mark and wordmark
remain covered without preserving the retired role system.

### 16.5 Console routes

Migrate shared primitives before individual pages. Then cover all route groups
without changing loaders, mutations, API clients, providers, or state logic.

### 16.6 Metadata and exported brand surfaces

- Update `metadata`, Open Graph, Twitter, manifest theme colors, and social
  preview artwork to the new dark technical treatment.
- Retain SEO claims that remain truthful.
- Remove character art and CRM-like cream styling from social previews.

## 17. Ordering and rollout

Implement as reviewable vertical slices:

1. **Foundations:** tokens, fonts, mark treatment, focus, shared primitives.
2. **Marketing:** navigation and five existing landing sections.
3. **Authentication/access:** sign-in, onboarding, pending, declined,
   activation, join.
4. **Console shell:** rail, top bar, workspace switcher, overlays.
5. **Console routes:** tables, forms, metrics, job detail, docs, admin.
6. **Exported brand surfaces:** metadata, manifest, social images.
7. **Cleanup and verification:** dead character/motion modules, copy scan,
   full tests, production build, browser QA.

Every slice must leave the app runnable. Do not mix backend work into these
commits.

## 18. Failure modes and rollback

### 18.1 Main risks

- Dark marketing tokens leak into the light console or vice versa.
- Removing motion helpers breaks mobile navigation, auth mode transitions, or
  functional topology behavior.
- Vocabulary changes accidentally touch API/type names instead of visible
  copy only.
- Character component removal leaves broken imports or missing empty states.
- Font metrics change headings or table density enough to create overflow.
- A visual-only edit overwrites concurrent functional console work.

### 18.2 Mitigation

- Scope tokens through route-level wrappers or explicit dark/light theme
  classes rather than relying on one mutable global theme.
- Separate behavioral hooks from visual motion before deleting helpers.
- Search visible JSX strings separately from internal identifiers.
- Preserve existing data loaders, event handlers, providers, and API calls.
- Exercise every route and key state at the responsive checkpoints.
- Review the dirty worktree before every edit and stage only intentional
  files.

### 18.3 Rollback

No migration or backend state changes exist. The rebrand can be rolled back by
reverting its frontend commits. Each rollout slice must be independently
revertible and must not require data cleanup.

## 19. Verification

### 19.1 Automated

Run fresh from `apps/web`:

```bash
npm test
npm run lint
npx tsc --noEmit
npm run build
```

Update or add tests for:

- brand token values;
- wordmark/mark accessibility;
- marketing navigation keyboard behavior;
- no unsafe next-route regression in auth;
- route exports and page availability;
- visible vocabulary adapters;
- semantic labels on status-only components;
- any new topology or event-ledger transformation logic.

### 19.2 Deterministic source scans

The final implementation must satisfy:

```bash
# Retired character modules and role definitions are absent.
test ! -e apps/web/components/brand/ZolliCharacter.tsx
test ! -e apps/web/components/auth/AuthCharacters.tsx
test ! -e apps/web/lib/zolli-brand.ts
rg "ZolliCharacter|AuthCharacters|ZOLLI_ROLES|ZolliRole" \
  apps/web/app apps/web/components apps/web/lib

# No character-era visual primitives in production markup/styles.
rg "font-display|glass-strong|\bglass\b|\bgrain\b|MagneticLink|SpotlightCard" \
  apps/web/app apps/web/components

# Review every remaining motion import. Each must explain real state or an
# overlay transition; decorative call sites are failures.
rg "motion/react" apps/web/app apps/web/components apps/web/lib

# Visible object vocabulary must use Workspace and Machine. Exact technical
# docs/protocol contexts and the single legacy `#crew` fragment alias are
# reviewed rather than blindly replaced.
rg "Crew|Captain|Scout|Keeper|Relay|Builder|Zolli(s)?" \
  apps/web/app apps/web/components
```

The three `test` commands must exit zero. The first two `rg` scans must return
no matches. The motion scan and visible-vocabulary scan are review lists, not
automatic zero-count requirements; every remaining match must be annotated in
the implementation handoff with its technical or compatibility reason.

### 19.3 Browser QA

Exercise:

- landing navigation, anchors, both CTAs, and open-runtime link;
- topology and ledger at all five viewport checkpoints;
- sign-in/sign-up switch, validation, errors, Google fallback, and preserved
  `next` redirect;
- access-request form, pending, declined, activation, and join states;
- admitted console navigation, workspace switching, command palette, mobile
  drawer, toasts, dialogs, and banners;
- every routed page, including loading, empty, error, and populated states;
- keyboard-only navigation;
- `prefers-reduced-motion`;
- no horizontal overflow;
- readable contrast on both theme registers.

Authenticated workspace routes must be exercised against the dev stack in this
rebrand. The 2026-08-05 POC explicitly did not complete that verification, so
repeating the gap is not acceptable.

## 20. Acceptance criteria

The rebrand is complete only when all statements are true:

1. The approved dark marketing and light console palettes are implemented with
   the exact tokens in §6.
2. Instrument Sans and Geist Mono are the only primary UI font families;
   Fraunces and Inter are no longer loaded.
3. The marketing page preserves its current five-section structure and route
   behavior while matching the approved preview's visual language.
4. The hero uses system topology and an event ledger instead of the six
   characters.
5. No normal product surface renders a Zolli character or the Captain/Worker/
   Scout/Keeper/Relay/Builder role system.
6. Visible UI uses Workspace, Machine, Worker, Job, Task, Lease, and Checkpoint
   according to §3. Internal `pool`/`node` contracts remain unchanged.
7. Marketing navigation is stable and non-floating; auth has no aurora or
   character stage; console data sits on opaque light surfaces with a dark rail.
8. Decorative motion listed in §14.3 is gone. Remaining motion is functional,
   reduced-motion safe, and reviewed call by call.
9. All 24 current routed pages, plus global error/not-found surfaces, visibly
   belong to the new system.
10. Existing API calls, permissions, routes, and user workflows behave exactly
    as before.
11. Test, lint, TypeScript, and production build commands pass and report their
    fresh counts/output.
12. Browser QA passes at 1440, 1024, 768, 390, and 375px with no horizontal
    overflow.
13. Authenticated workspace routes are verified against the dev stack.
14. Metadata, manifest colors, and social preview artwork match the rebrand.
15. `PROGRESS.md` records the completed slice with exact verification evidence.

## 21. Settled decisions

- The approved visual direction is **warm technical minimalism**.
- Marketing is dark graphite; the console is operational light with a dark
  rail.
- Zolli orange remains the signature accent.
- The connected-node mark remains.
- Characters and Crew metaphors leave primary product use.
- Current page structure and working behavior remain.
- This is a complete frontend migration, not a landing-only skin.
- Backend and underlying FlashML naming remain out of scope.

Any reversal of these decisions requires owner approval and an update to this
spec before implementation.
