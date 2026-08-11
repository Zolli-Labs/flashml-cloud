# Zolli Proof-Led Landing Expansion

**Date:** 2026-08-09
**Scope:** `flashml-cloud/apps/web` marketing surface and public information routes
**Status:** Approved for implementation
**Parent direction:** `2026-08-09-zolli-warm-technical-rebrand-design.md`

## 1. Objective

Expand the approved warm technical Zolli Cloud landing page from a product
manifesto into a complete commercial evaluation journey. The page must help a
technical buyer understand what Zolli does, see defensible evidence, determine
whether their machines and workloads fit, choose self-service or assisted
onboarding, and find standard company and legal information.

The visual language remains unchanged: graphite backgrounds, mineral surfaces,
orange interaction accents, Instrument Sans, Geist Mono, opaque panels, restrained
geometry, and no decorative animation.

## 2. Audience and conversion hierarchy

Primary audience: engineering teams and research groups combining cloud GPUs,
local workstations, and spare machines for fault-tolerant distributed work.

Conversion order:

1. **Open console** — the dominant CTA everywhere it appears; routes to
   `/workspaces` and preserves the existing authentication redirect.
2. **Talk to Zolli / Schedule with Zolli** — the secondary assisted path; opens
   `https://calendly.com/phongct1105/zolli-ai` in a new tab.
3. **Inspect recovery / Read docs / Open runtime** — evidence and education paths.

Public contact email: `phongct1105@gmail.com` until a domain mailbox replaces it.

## 3. Page narrative and section order

### 3.1 Navigation

Keep the fixed graphite navigation and reversed Zolli Cloud wordmark. Desktop
navigation exposes `How it works`, `Platform`, `Services`, and `Open runtime`,
followed by the primary `Open console` button. Mobile navigation contains the
same destinations and retains Escape-to-close and focus restoration.

### 3.2 Hero

Retain the approved headline, support copy, sample topology, and event ledger.
Replace the current secondary `Inspect recovery` action with `Talk to Zolli` so
the hero presents the two conversion modes directly. `Inspect recovery` remains
available through the proof narrative below.

### 3.3 Verified evidence band

Place a compact proof band immediately after the hero. It uses four large values
with short evidence qualifiers:

- **30** — production attempts recorded across the first two contributing hosts.
- **2** — production-proven architectures: macOS arm64 and Linux x86_64.
- **5** — steps of work lost in the checkpoint recovery demonstration instead of
  restarting from step 0 after a failure at step 35.
- **1** — accepted result per task through idempotent commit semantics.

The section must label these as product evidence, not customer or scale metrics.
No uptime, customer count, cost reduction, speedup, or success-rate claim may be
introduced without a source in `PROGRESS.md` or another authoritative record.

### 3.4 Platform support

Add a compatibility matrix with explicit evidence states instead of a logo wall.

**Production-proven hosts**

- macOS on arm64
- Linux on x86_64

**Preview host**

- Windows 11 with Docker Desktop and the WSL2 backend. This is compatibility
  support, not a claim that Windows has completed production work.

**Execution and integration**

- Python workloads
- Allowlisted Docker containers
- Public GitHub repositories with `flashml.yaml`
- NVIDIA GPU scheduling and the CUDA 12.4 curated PyTorch image
- Local machines, home rigs, and cloud GPU hosts under one workspace

Every platform label includes `Proven`, `Preview`, or `Supported` state copy so
the section remains accurate as the product matures.

### 3.5 How Zolli works

Retain the existing three-event sequence and strengthen its numbered spine:

1. A worker claims a bounded lease.
2. A missing heartbeat becomes an auditable interruption.
3. Another worker resumes from verified progress and one result is accepted.

The section remains protocol-led and must not introduce mascot roles or generic
three-feature cards.

### 3.6 Workload fit

Introduce four real workload families already represented in the project:

- Federated training across independent machines
- Hyperparameter search
- Sharded data processing and K-means
- Checkpointable model training

Each item explains the coordination pattern in one sentence and links the value
back to leases, checkpoints, or accepted results. Do not claim support for
arbitrary ML frameworks or private repositories.

### 3.7 Platform architecture

Reframe the six existing operational modules into three layers:

- **Control:** Coordinate and Enroll
- **Execution:** Execute and Checkpoint
- **Integrity:** Recover and Verify

Use a three-column architectural composition on desktop and a vertical sequence
on mobile. Each layer receives a two-digit index, one plain-language description,
and its two relevant protocol events. This replaces the current six-row list and
reduces the page's text-table repetition.

### 3.8 Recovery proof

Retain the real event-ledger component and sample-data disclosure. Promote the
verified `failure at step 35 → checkpoint at step 30 → 5 steps lost` result as the
section's numeric proof. Do not present sample timestamps or machine names as live
production telemetry.

### 3.9 Professional services

Add a restrained services section for teams that want assisted adoption:

- Architecture and workload assessment
- Machine and GPU fleet onboarding
- Runtime and job-spec integration
- Private deployment and recovery design

The section explains that engagement scope is agreed directly with Zolli; it must
not invent service tiers, response-time guarantees, prices, certifications, or
customer logos. Its primary local action is `Schedule with Zolli` to Calendly;
email is available as a secondary text link.

### 3.10 FAQ

Add an accessible native disclosure/accordion section covering:

1. What does Zolli coordinate?
2. Which machines are supported?
3. What happens when a machine disappears?
4. Does every machine need Docker?
5. How are code, artifacts, and credentials handled?
6. How is Zolli priced?
7. What support is available during early access?

Answers must distinguish shipped behavior from targets. Pricing states that the
product is early access and directs commercial questions to Calendly/email; it
must not imply a published plan. Security copy may describe implemented isolation
and credential boundaries but must not claim compliance certifications.

### 3.11 Final conversion and footer

Keep `Bring the fleet. Keep the progress.` as the closing line. Present
`Open console` first and `Talk to Zolli` second.

Expand the footer into four groups:

- **Product:** Console, Machines, Jobs, Platform
- **Resources:** Docs, GitHub, Open runtime, FAQ
- **Company:** Contact, Schedule a call
- **Legal:** Privacy, Terms, Security

The footer continues to disclose that Zolli Cloud is an early product and that
FlashML is the open runtime and wire protocol underneath.

## 4. Public information routes

Create public marketing-themed routes for `/contact`, `/privacy`, `/terms`, and
`/security`.

### Contact

Offer two clear paths: open the console or schedule through Calendly. Include the
temporary contact email. Do not add an unbacked contact form or promise response
times.

### Privacy

Write a concise product-specific notice that identifies the data visibly used by
the current application: account identity from the authentication provider,
workspace and machine metadata, job/protocol events, contribution records, and
operational logs. State purposes in plain language. Direct access, correction, and
deletion requests to the public email. Avoid claiming retention periods, cookie
practices, subprocessors, cross-border safeguards, or regulatory compliance that
have not been established.

### Terms

Provide early-access terms describing permitted use, user responsibility for code
and machines, service changes/availability, open-runtime separation, warranty
disclaimer, liability boundary, and contact. Do not invent a physical address,
registered entity number, governing jurisdiction, paid subscription terms, SLA,
or refund policy. Display a visible note that the terms are an early operational
baseline and should receive legal review before a paid public launch.

### Security

Describe only implemented controls: bounded leases, authenticated machine writes,
allowlisted workloads/images, network-disabled container execution where the
Docker tier is used, scrubbed task environments, hash-verified artifacts and
checkpoints, idempotent result acceptance, and an append-oriented event history.
Also state material boundaries: infrastructure remains early access, deployment
configuration matters, and no compliance certification is claimed. Provide the
contact email for responsible disclosure.

## 5. Visual system and responsive behavior

- Keep one dark marketing theme; sections may alternate `#0B0D0E`, `#111416`,
  and `#171A1D` without switching to a light page theme.
- Use large numbers as typographic anchors, not dashboard metric cards.
- Use grouped architectural blocks, compatibility rows, and disclosure elements
  to vary rhythm; avoid repeating long bordered row lists in every section.
- Orange remains reserved for the dominant CTA, active affordances, and small
  proof annotations.
- New sections remain static except for stateful FAQ disclosure and navigation.
- All links and buttons provide visible focus states and minimum 40px targets.
- At 375px, content is single-column, proof values become a two-column grid,
  compatibility states remain readable without horizontal scrolling, and footer
  groups stack cleanly.

## 6. Component boundaries

Add focused landing components rather than expanding the route file:

- `EvidenceBand`
- `PlatformSupport`
- `WorkloadFit`
- revised `SystemModules`
- `ProfessionalServices`
- `Faq`
- revised `ClosingCta`

Add a shared marketing information-page shell for contact/legal routes. Keep
Calendly URL and public email in one small marketing constants module so the hero,
services, footer, and information pages cannot drift.

## 7. Accessibility and metadata

- Use semantic sections with unique headings and stable anchor IDs.
- FAQ uses native `details`/`summary` or an equivalently keyboard-complete
  disclosure; JavaScript is not required to read answers.
- Platform states include text labels and never depend on color alone.
- External Calendly and GitHub links announce new-tab behavior to assistive
  technology where appropriate.
- Each public information route receives a specific page title and description.
- Footer navigation groups have accessible labels.

## 8. Testing and verification

Follow test-first implementation.

Automated contract tests must assert:

- new landing section order and stable anchor IDs;
- exact verified evidence values and their qualifiers;
- Windows is labeled preview rather than proven;
- Calendly and contact email come from shared constants;
- FAQ contains all seven questions and uses accessible disclosure markup;
- footer includes contact and legal routes;
- contact, privacy, terms, and security routes export appropriate metadata;
- retired character vocabulary and decorative blur/motion remain absent.

Final verification requires ESLint, `tsc --noEmit`, the full Vitest suite,
`git diff --check`, a production Next.js build, and browser QA at 1440, 1024,
768, 390, and 375 pixels. Browser QA covers navigation anchors, FAQ keyboard
behavior, Calendly targets, public information routes, console redirects, focus
visibility, console errors, and horizontal overflow.

## 9. Deliberate exclusions

- No invented testimonials, customer logos, uptime, benchmark, or savings claims.
- No pricing table until product packaging exists.
- No contact form or newsletter storage.
- No status page until a real status service exists.
- No cookie banner unless tracking or non-essential cookies are introduced.
- No certification badges or regulatory claims.
- No rewrite of API, database, protocol, CLI, or route-internal `pool` naming.
