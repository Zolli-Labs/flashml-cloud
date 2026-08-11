# Zolli Proof-Led Landing Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the approved Zolli Cloud marketing page into a complete proof-led B2B platform site with verified evidence, platform compatibility, workload fit, professional services, FAQ, contact, and public information routes.

**Architecture:** Keep the marketing route server-rendered and compose it from focused static React components. Centralize public URLs and email in one typed constants module, render all evidence from explicit data arrays, use native disclosure elements for FAQ, and share one information-page shell across contact, privacy, terms, and security routes.

**Tech Stack:** Next.js 16 App Router, React 19 Server Components, TypeScript 5, Tailwind CSS 4, Phosphor SSR icons, Vitest server-render contract tests.

## Global Constraints

- Preserve the graphite/mineral/orange warm technical design system from `2026-08-09-zolli-warm-technical-rebrand-design.md`.
- `Open console` routes to `/workspaces` and remains the visually dominant CTA.
- `Talk to Zolli` and `Schedule with Zolli` open `https://calendly.com/phongct1105/zolli-ai` in a new tab.
- Public contact email is `phongct1105@gmail.com` until a domain mailbox replaces it.
- Only repository-verified numbers may appear: `30` production attempts, `2` proven architectures, `5` steps lost in the recovery demonstration, and `1` accepted result per task.
- macOS arm64 and Linux x86_64 are `Proven`; Windows 11 with Docker Desktop/WSL2 is `Preview`.
- Do not add customer logos, testimonials, uptime, speedup, savings, pricing tiers, SLAs, certifications, or regulatory claims.
- Keep FlashML as the open runtime/protocol name; do not rename API, database, CLI, protocol, or route-internal `pool` identifiers.
- Do not add decorative motion, blur, glass, mascot roles, or character runtime code.
- Do not commit, push, or merge until the user approves the completed local preview.

---

### Task 1: Pin the public marketing contract and shared destinations

**Files:**
- Create: `flashml-cloud/apps/web/lib/marketing.ts`
- Create: `flashml-cloud/apps/web/lib/landing-expansion.test.ts`
- Modify: `flashml-cloud/apps/web/lib/landing-rebrand.test.ts`

**Interfaces:**
- Produces: `MARKETING.calendlyUrl`, `MARKETING.contactEmail`, `MARKETING.runtimeRepo`, `MARKETING.consolePath` as readonly strings.
- Produces: a server-render contract test that later tasks satisfy through the real `Home` route.

- [ ] **Step 1: Write the failing shared-destination and page-contract tests**

Create `lib/landing-expansion.test.ts` with helpers that render the real home route:

```ts
import { createElement } from "react";
import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Home from "@/app/(marketing)/page";
import { MARKETING } from "@/lib/marketing";

const renderLanding = () => renderToStaticMarkup(createElement(Home));
const visibleText = (markup: string) =>
  markup.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

describe("proof-led Zolli landing", () => {
  it("uses one canonical console, schedule, runtime, and contact destination", () => {
    expect(MARKETING).toEqual({
      consolePath: "/workspaces",
      calendlyUrl: "https://calendly.com/phongct1105/zolli-ai",
      contactEmail: "phongct1105@gmail.com",
      runtimeRepo: "https://github.com/Zolli-Labs/flashml",
    });

    const markup = renderLanding();
    expect(markup).toContain(`href="${MARKETING.consolePath}"`);
    expect(markup).toContain(`href="${MARKETING.calendlyUrl}"`);
    expect(markup).toContain('target="_blank"');
  });

  it("orders evaluation content from proof through conversion", () => {
    const markup = renderLanding();
    const anchors = [
      'id="evidence"',
      'id="platform"',
      'id="how-it-works"',
      'id="workloads"',
      'id="architecture"',
      'id="recover"',
      'id="services"',
      'id="faq"',
    ];

    anchors.reduce((previous, anchor) => {
      const current = markup.indexOf(anchor);
      expect(current).toBeGreaterThan(previous);
      return current;
    }, -1);
  });

  it("shows only the approved evidence and platform states", () => {
    const text = visibleText(renderLanding());
    for (const claim of [
      "30 production attempts",
      "2 proven architectures",
      "5 steps lost, not 35",
      "1 accepted result per task",
      "macOS arm64",
      "Linux x86_64",
      "Windows 11",
      "Preview",
      "NVIDIA CUDA 12.4",
    ]) expect(text).toContain(claim);
    expect(text).not.toContain("Windows 11 Proven");
  });
});
```

Update the old hero-action test in `landing-rebrand.test.ts` to expect
`Talk to Zolli` and the Calendly URL instead of `Inspect recovery` as the
secondary hero action.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cd flashml-cloud/apps/web
npm test -- lib/landing-expansion.test.ts lib/landing-rebrand.test.ts
```

Expected: FAIL because `@/lib/marketing` and the new landing sections do not exist, and the hero still links to `#recover`.

- [ ] **Step 3: Add the shared constants module**

Create `lib/marketing.ts`:

```ts
export const MARKETING = {
  consolePath: "/workspaces",
  calendlyUrl: "https://calendly.com/phongct1105/zolli-ai",
  contactEmail: "phongct1105@gmail.com",
  runtimeRepo: "https://github.com/Zolli-Labs/flashml",
} as const;
```

- [ ] **Step 4: Re-run the focused test and record the expected remaining failures**

Run the same Vitest command. Expected: module import succeeds; landing-section and hero-action assertions remain RED because production components are unchanged.

- [ ] **Step 5: Review checkpoint**

Run `git diff --check` and inspect `git diff -- lib/marketing.ts lib/landing-expansion.test.ts lib/landing-rebrand.test.ts`. Do not commit.

---

### Task 2: Add verified evidence, platform support, and workload fit

**Files:**
- Create: `flashml-cloud/apps/web/components/landing/EvidenceBand.tsx`
- Create: `flashml-cloud/apps/web/components/landing/PlatformSupport.tsx`
- Create: `flashml-cloud/apps/web/components/landing/WorkloadFit.tsx`
- Modify: `flashml-cloud/apps/web/app/(marketing)/page.tsx`
- Test: `flashml-cloud/apps/web/lib/landing-expansion.test.ts`

**Interfaces:**
- Consumes: `Home` route and approved copy contract from Task 1.
- Produces: stable `#evidence`, `#platform`, and `#workloads` sections.

- [ ] **Step 1: Extend the failing tests for exact labels and prohibited claims**

Add assertions:

```ts
it("qualifies platform compatibility without overstating Windows", () => {
  const text = visibleText(renderLanding());
  expect(text).toContain("Production-proven hosts");
  expect(text).toContain("macOS arm64 Proven");
  expect(text).toContain("Linux x86_64 Proven");
  expect(text).toContain("Windows 11 Preview");
  expect(text).toContain("Docker containers");
  expect(text).toContain("Public GitHub repositories");
  expect(text).not.toMatch(/customers|uptime|faster|savings/i);
});

it("names four workloads already represented in the project", () => {
  const text = visibleText(renderLanding());
  for (const workload of [
    "Federated training",
    "Hyperparameter search",
    "Sharded data processing",
    "Checkpointable model training",
  ]) expect(text).toContain(workload);
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Expected: FAIL because evidence, compatibility, and workload copy is absent.

- [ ] **Step 3: Implement the evidence band**

Create a static `EVIDENCE` array with these exact value/label/detail tuples:

```ts
const EVIDENCE = [
  ["30", "production attempts", "Recorded across the first two contributing hosts."],
  ["2", "proven architectures", "macOS arm64 and Linux x86_64."],
  ["5", "steps lost, not 35", "Recovered from the last verified checkpoint."],
  ["1", "accepted result per task", "Idempotent commits reject duplicate outcomes."],
] as const;
```

Render it in `<section id="evidence">` as a four-column desktop grid, two columns at 375px, with large Instrument Sans numerals, mono labels, and one visible sentence identifying the figures as verified product evidence rather than scale metrics.

- [ ] **Step 4: Implement platform support**

Create typed groups for `Production-proven hosts`, `Preview host`, and
`Execution and integration`. Render state badges as text plus color:

```ts
const HOSTS = [
  { name: "macOS arm64", state: "Proven", detail: "Completed credited production work." },
  { name: "Linux x86_64", state: "Proven", detail: "Completed credited production work." },
  { name: "Windows 11", state: "Preview", detail: "Docker Desktop with the WSL2 backend." },
] as const;
```

The integration group names Python workloads, Docker containers, public GitHub repositories with `flashml.yaml`, NVIDIA CUDA 12.4, and local/cloud machine supply. Use grouped panels, not brand logos.

- [ ] **Step 5: Implement workload fit**

Render four asymmetrical numbered blocks under `<section id="workloads">` with these descriptions:

```ts
const WORKLOADS = [
  ["01", "Federated training", "Coordinate independent machines while aggregating accepted model updates."],
  ["02", "Hyperparameter search", "Lease isolated trials across mixed compute and retain each accepted result."],
  ["03", "Sharded data processing", "Distribute map work, collect verified partials, and coordinate deterministic reduction."],
  ["04", "Checkpointable model training", "Resume long-running training from the latest verified checkpoint after interruption."],
] as const;
```

- [ ] **Step 6: Insert the sections in the route**

Update `app/(marketing)/page.tsx` to render:

```tsx
<Hero />
<EvidenceBand />
<PlatformSupport />
<SystemStory />
<WorkloadFit />
<SystemModules />
<RecoveryDemo />
<ClosingCta />
```

- [ ] **Step 7: Verify GREEN for this slice**

Run the focused Vitest command. The evidence/platform/workload assertions must pass; section-order assertions may remain RED until later sections receive their IDs.

- [ ] **Step 8: Review checkpoint**

Run ESLint on the three new components plus `app/(marketing)/page.tsx`, then run `git diff --check`. Do not commit.

---

### Task 3: Reframe architecture and strengthen recovery proof

**Files:**
- Modify: `flashml-cloud/apps/web/components/landing/SystemModules.tsx`
- Modify: `flashml-cloud/apps/web/components/landing/RecoveryDemo.tsx`
- Test: `flashml-cloud/apps/web/lib/landing-expansion.test.ts`

**Interfaces:**
- Produces: stable `#architecture` and existing `#recover` sections.
- Preserves: real `EventLedger` data and mandatory `sample data` disclosure.

- [ ] **Step 1: Add failing architecture and recovery assertions**

```ts
it("groups the runtime into control, execution, and integrity layers", () => {
  const text = visibleText(renderLanding());
  for (const layer of ["01 Control", "02 Execution", "03 Integrity"])
    expect(text).toContain(layer);
  for (const module of ["Coordinate", "Enroll", "Execute", "Checkpoint", "Recover", "Verify"])
    expect(text).toContain(module);
});

it("connects the recovery ledger to the verified five-step result", () => {
  const text = visibleText(renderLanding());
  expect(text).toContain("Failure at step 35");
  expect(text).toContain("Checkpoint at step 30");
  expect(text).toContain("5 steps of work lost");
  expect(text).toContain("sample data");
});
```

- [ ] **Step 2: Run focused tests and verify RED**

Expected: FAIL because `SystemModules` is still a six-row module list and recovery does not expose all three numbers.

- [ ] **Step 3: Replace six rows with three architectural layers**

Use:

```ts
const LAYERS = [
  {
    index: "01",
    title: "Control",
    body: "Turn submitted work and available machines into bounded ownership.",
    modules: [["Coordinate", "LEASE_CLAIMED"], ["Enroll", "DEVICE_CODE · ACTIVATION"]],
  },
  {
    index: "02",
    title: "Execution",
    body: "Run isolated attempts and preserve durable progress while they work.",
    modules: [["Execute", "LEASE_RENEWED"], ["Checkpoint", "CHECKPOINT_MANIFEST_COMMITTED"]],
  },
  {
    index: "03",
    title: "Integrity",
    body: "Recover interrupted work and accept one verified outcome.",
    modules: [["Recover", "TASK_REQUEUED"], ["Verify", "TASK_COMMIT_ACCEPTED"]],
  },
] as const;
```

Change the section ID from `compute` to `architecture`; preserve an invisible
`id="compute"` alias so existing links do not break.

- [ ] **Step 4: Add the recovery evidence triplet**

Above the ledger, render `Failure at step 35`, `Checkpoint at step 30`, and
`5 steps of work lost` as a compact causal sequence. Keep the ledger copy clear
that protocol names are real while displayed values are sample data.

- [ ] **Step 5: Verify GREEN and review**

Run focused tests, ESLint on both components, and `git diff --check`. Do not commit.

---

### Task 4: Add professional services, FAQ, complete navigation, and conversion footer

**Files:**
- Create: `flashml-cloud/apps/web/components/landing/ProfessionalServices.tsx`
- Create: `flashml-cloud/apps/web/components/landing/Faq.tsx`
- Modify: `flashml-cloud/apps/web/components/landing/Hero.tsx`
- Modify: `flashml-cloud/apps/web/components/landing/ClosingCta.tsx`
- Modify: `flashml-cloud/apps/web/components/nav/Navbar.tsx`
- Modify: `flashml-cloud/apps/web/app/(marketing)/page.tsx`
- Test: `flashml-cloud/apps/web/lib/landing-expansion.test.ts`

**Interfaces:**
- Consumes: `MARKETING` constants from Task 1.
- Produces: stable `#services` and `#faq` anchors and complete footer navigation.

- [ ] **Step 1: Add failing conversion, FAQ, and footer tests**

```ts
it("offers assisted adoption without displacing self service", () => {
  const markup = renderLanding();
  const text = visibleText(markup);
  expect(text).toContain("Professional services");
  expect(text).toContain("Architecture and workload assessment");
  expect(text).toContain("Machine and GPU fleet onboarding");
  expect(text).toContain("Runtime and job-spec integration");
  expect(text).toContain("Private deployment and recovery design");
  expect(markup.indexOf("Open console")).toBeLessThan(markup.indexOf("Talk to Zolli"));
});

it("answers the seven buyer questions with native disclosures", () => {
  const markup = renderLanding();
  expect((markup.match(/<details/g) ?? [])).toHaveLength(7);
  for (const question of [
    "What does Zolli coordinate?",
    "Which machines are supported?",
    "What happens when a machine disappears?",
    "Does every machine need Docker?",
    "How are code, artifacts, and credentials handled?",
    "How is Zolli priced?",
    "What support is available during early access?",
  ]) expect(markup).toContain(question);
});

it("provides complete product, resource, company, and legal navigation", () => {
  const markup = renderLanding();
  for (const href of ["/contact", "/privacy", "/terms", "/security", "#platform", "#faq"])
    expect(markup).toContain(`href="${href}"`);
});
```

- [ ] **Step 2: Run focused tests and verify RED**

Expected: FAIL because services, disclosures, Calendly hero action, and footer groups are absent.

- [ ] **Step 3: Implement professional services**

Create `<section id="services">` with the four approved services. Use one large
editorial heading plus a two-by-two capability layout. Add a `Schedule with Zolli`
anchor using `MARKETING.calendlyUrl` and a `mailto:` link using
`MARKETING.contactEmail`. Do not render tiers, prices, or response promises.

- [ ] **Step 4: Implement the FAQ with native disclosure**

Create a seven-item `FAQS` array and render semantic `<details><summary>…` elements.
Answers must state:

- Zolli coordinates jobs, tasks, leases, checkpoints, recovery, and accepted results.
- macOS arm64 and Linux x86_64 are proven; Windows 11 is preview.
- a missing heartbeat expires ownership and requeues from the last verified checkpoint.
- subprocess execution exists for trusted pools, while allowlisted Docker is the isolation path for shared machines.
- task environments are scrubbed, machine writes are authenticated and lease-scoped, and artifacts/checkpoints are hash-verified; deployment configuration still matters.
- pricing is not published during early access; schedule or email for scope.
- early-access support covers onboarding, workload integration, deployment, and recovery design by agreement, without an SLA claim.

- [ ] **Step 5: Update hero, navigation, route order, and closing CTA**

- Hero secondary CTA: `Talk to Zolli`, external Calendly URL, new tab.
- Navbar links: `How it works`, `Platform`, `Services`, `Open runtime`, then `Open console`.
- Insert `ProfessionalServices` after `RecoveryDemo` and `Faq` after services.
- Closing CTA: `Open console` first, `Talk to Zolli` second.
- Footer groups: Product, Resources, Company, Legal, with the exact routes from the spec.

- [ ] **Step 6: Verify GREEN for the whole landing contract**

Run:

```bash
npm test -- lib/landing-expansion.test.ts lib/landing-rebrand.test.ts
```

Expected: all landing contract tests pass, including section order.

- [ ] **Step 7: Review checkpoint**

Run ESLint on every file in this task and `git diff --check`. Do not commit.

---

### Task 5: Add contact, privacy, terms, and security routes

**Files:**
- Create: `flashml-cloud/apps/web/components/marketing/InformationPage.tsx`
- Create: `flashml-cloud/apps/web/app/(marketing)/contact/page.tsx`
- Create: `flashml-cloud/apps/web/app/(marketing)/privacy/page.tsx`
- Create: `flashml-cloud/apps/web/app/(marketing)/terms/page.tsx`
- Create: `flashml-cloud/apps/web/app/(marketing)/security/page.tsx`
- Create: `flashml-cloud/apps/web/lib/public-information.test.ts`

**Interfaces:**
- Consumes: `MARKETING.consolePath`, `MARKETING.calendlyUrl`, and `MARKETING.contactEmail`.
- Produces: `InformationPage({ eyebrow, title, intro, children })` and four public routes with route-specific `Metadata`.

- [ ] **Step 1: Write failing route-render and metadata tests**

```ts
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Contact, { metadata as contactMetadata } from "@/app/(marketing)/contact/page";
import Privacy, { metadata as privacyMetadata } from "@/app/(marketing)/privacy/page";
import Terms, { metadata as termsMetadata } from "@/app/(marketing)/terms/page";
import Security, { metadata as securityMetadata } from "@/app/(marketing)/security/page";

const render = (component: () => ReactNode) =>
  renderToStaticMarkup(createElement(component));

describe("public Zolli information routes", () => {
  it("exports specific metadata", () => {
    expect(contactMetadata.title).toBe("Contact | Zolli Cloud");
    expect(privacyMetadata.title).toBe("Privacy | Zolli Cloud");
    expect(termsMetadata.title).toBe("Terms | Zolli Cloud");
    expect(securityMetadata.title).toBe("Security | Zolli Cloud");
  });

  it("offers console, Calendly, and email contact paths", () => {
    const markup = render(Contact);
    expect(markup).toContain('href="/workspaces"');
    expect(markup).toContain('href="https://calendly.com/phongct1105/zolli-ai"');
    expect(markup).toContain('href="mailto:phongct1105@gmail.com"');
  });

  it("keeps privacy, terms, and security claims inside known boundaries", () => {
    expect(render(Privacy)).toContain("account identity");
    expect(render(Privacy)).toContain("job and protocol events");
    expect(render(Terms)).toContain("early-access operational baseline");
    expect(render(Terms)).not.toMatch(/SOC 2|HIPAA|GDPR compliant/);
    expect(render(Security)).toContain("bounded leases");
    expect(render(Security)).toContain("No compliance certification is claimed");
  });
});
```

- [ ] **Step 2: Run the route test and verify RED**

Run `npm test -- lib/public-information.test.ts`. Expected: FAIL because all imports are missing.

- [ ] **Step 3: Implement the shared information-page shell**

Create a server component that renders a narrow `max-w-[860px]` article under the
fixed marketing nav, with a mono eyebrow, large title, intro, `prose`-independent
content spacing, and a bottom link back to the homepage. The shell accepts only
React content; it owns presentation, not legal copy.

- [ ] **Step 4: Implement `/contact`**

Export:

```ts
export const metadata: Metadata = {
  title: "Contact | Zolli Cloud",
  description: "Open the Zolli console, schedule a technical conversation, or contact Zolli Labs.",
};
```

Render `Open console`, `Schedule with Zolli`, and the public `mailto:` link. State
that scheduling is for architecture, onboarding, workload integration, and private
deployment discussions. Do not render a form.

- [ ] **Step 5: Implement `/privacy`**

Use sections `Information used by the service`, `Why it is used`, `Infrastructure
and service providers`, `Your requests`, and `Contact`. Describe account identity,
workspace/machine metadata, job/protocol events, contributions, and operational
logs. Do not state a fixed retention period or regulatory compliance.

- [ ] **Step 6: Implement `/terms`**

Use sections `Early access`, `Your code and machines`, `Acceptable use`, `Service
changes and availability`, `Open runtime`, `Warranty and liability`, and `Contact`.
Display: `These terms are an early-access operational baseline and require legal
review before a paid public launch.` Do not invent governing law or corporate registration details.

- [ ] **Step 7: Implement `/security`**

Use sections `Execution boundaries`, `Machine identity`, `Artifact integrity`,
`Recovery history`, `Current boundaries`, and `Report a concern`. Name implemented
controls from the spec and state: `No compliance certification is claimed.` Link
responsible disclosure to the public email.

- [ ] **Step 8: Verify GREEN and review**

Run `npm test -- lib/public-information.test.ts`, ESLint on the shell and four routes,
`npx tsc --noEmit`, and `git diff --check`. Do not commit.

---

### Task 6: Complete full verification, browser QA, and project logging

**Files:**
- Modify: `PROGRESS.md`
- Modify only if QA exposes defects: files created or modified in Tasks 1–5

**Interfaces:**
- Consumes: the complete landing and public information routes.
- Produces: fresh automated evidence, responsive screenshots, and one authoritative progress entry.

- [ ] **Step 1: Run static audits**

Run:

```bash
rg -n "ZolliCharacter|AuthCharacters|ZOLLI_ROLES|Captain|Scout|Keeper|Relay|Builder|backdrop-filter|\bglass\b|\bgrain\b" flashml-cloud/apps/web/app flashml-cloud/apps/web/components flashml-cloud/apps/web/lib
rg -n "customers|uptime|SOC 2|HIPAA|GDPR compliant|money-back|guaranteed response" flashml-cloud/apps/web/app/'(marketing)' flashml-cloud/apps/web/components/landing flashml-cloud/apps/web/components/marketing
git diff --check
```

Expected: no retired visual-language or unsupported commercial/legal claims; `git diff --check` exits 0.

- [ ] **Step 2: Run the complete automated suite**

```bash
cd flashml-cloud/apps/web
npm run lint
npx tsc --noEmit
npm test
```

Expected: all commands exit 0. Record exact test-file and test counts from Vitest.

- [ ] **Step 3: Build with the intended environment**

```bash
set -a
source /Users/phongcao/Work/Zolli-Labs/flashml-cloud/.env.dev
set +a
npm run build
```

Expected: optimized Next.js build exits 0 and includes `/contact`, `/privacy`, `/terms`, and `/security` in the route table. The existing middleware deprecation warning is non-blocking.

- [ ] **Step 4: Run production-browser QA**

Start `npm run start -- -p 3004` with the same environment and inspect `/`,
`/contact`, `/privacy`, `/terms`, `/security`, and `/sign-in` at 1440×900,
1024×768, 768×1024, 390×844, and 375×812.

For each width verify:

- no horizontal overflow;
- hero and proof values do not collide;
- platform states remain attached to the correct host;
- all eight landing anchors navigate correctly;
- mobile navigation opens, closes on Escape, and restores focus;
- FAQ summaries open with click, Enter, and Space;
- Calendly links have the exact URL, `_blank`, and `rel="noreferrer"`;
- footer routes return 200;
- `/workspaces` redirects unauthenticated users to `/sign-in?next=%2Fworkspaces`;
- production console reports no errors.

- [ ] **Step 5: Fix browser defects test-first**

For any semantic or copy defect, add a failing Vitest assertion before editing the
component. For purely responsive CSS defects, capture the failing viewport,
apply the smallest class/token change, and recapture the same viewport.

- [ ] **Step 6: Re-run all verification after the final code change**

Repeat Steps 1–3 and the affected browser sizes. Evidence predating the final code
change does not count.

- [ ] **Step 7: Add one `PROGRESS.md` entry**

Add a newest-first entry titled `Complete the Zolli proof-led commercial landing`
with: what/why, exact Vitest count, lint/type/build results, browser widths and
interactions, legal-page caveat, authenticated-console limitation if still present,
and the single next action. Do not change the stage checklist because this work
does not alter a platform milestone.

- [ ] **Step 8: Preserve the preview-first handoff**

Report the local URL, branch, worktree, exact verification evidence, and remaining
limitations. Do not commit, push, merge, or remove the worktree until the user has
reviewed the local result and explicitly chooses an integration action.
