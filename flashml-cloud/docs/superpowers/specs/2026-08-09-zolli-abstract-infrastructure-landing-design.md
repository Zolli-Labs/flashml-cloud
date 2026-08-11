# Zolli Abstract Infrastructure Landing Redesign

**Date:** 2026-08-09

**Scope:** Zolli Cloud public landing page and its landing-only motion components

**Status:** Written specification awaiting user review

**Parent:** `2026-08-09-zolli-cinematic-motion-landing-design.md`

**Interaction reference:** Approved “interactive layer selector” concept shown in the local visual companion

## 1. Decision

Refine the current cinematic landing page around one product-specific visual idea:

> Zolli turns compatible compute from different infrastructure layers into one
> fault-tolerant fleet, then keeps a job moving when a machine disappears.

The hero will use an interactive abstract 3D infrastructure stack. Its four
planes represent cloud/HPC capacity, rented CPU/GPU, an owned GPU fleet, and
everyday machines. The planes contain shapes and motion only. All labels and
explanations live in a stable two-dimensional selector and detail panel.

The hero will no longer repeat the workflow topology. The later “How Zolli
works” section will become a seven-scene storyboard in which each scroll step
shows only the objects required for that step. The visitor first feels the
unification idea, then sees evidence and platform fit, then learns exactly how
fault-tolerant execution works.

This specification supersedes the hero, platform-support, workflow, workload
motion, and typography-wrap requirements in
`2026-08-09-zolli-cinematic-motion-landing-design.md`. It preserves that
document’s surface choreography, evidence limits, CTA hierarchy, accessibility
rules, public information routes, and preview-before-commit gate.

## 2. Why this work exists

### Who is affected

- ML engineers deciding whether Zolli fits an existing Python workload.
- Infrastructure operators deciding whether their mixed machines can become a
  usable compute fleet.
- Technical buyers evaluating whether Zolli is a control plane, a marketplace,
  or only a scheduler.
- Zolli, because the landing page is the first explanation of the product.

### Verified current behavior

Verified against the working tree on 2026-08-09:

| Area | Current implementation | User-visible gap |
|---|---|---|
| Page structure | Ten sections already alternate dark, ivory, sand, and orange in `apps/web/app/(marketing)/page.tsx:20` | Structure and color rhythm are correct and must stay |
| Hero | `HeroSystemStage.tsx:105` renders four labeled nodes, a control-plane ring, connecting paths, a task token, and five protocol events | It looks like a dense workflow diagram and duplicates the later workflow |
| Workflow | `SystemJourney.tsx:205` renders another four-node topology, control plane, job, checkpoint, failure, requeue path, result, and event ledger | Too many simultaneous objects make a quick scan difficult |
| Platform | `PlatformSupport.tsx:54` renders three host rows and `PlatformSupport.tsx:65` renders five integration rows | Eight text-heavy rows hide the familiar libraries and runtimes visitors recognize |
| Motion | GSAP 3.15.0, `@gsap/react` 2.1.2, and Motion 12.42.2 are already installed in `apps/web/package.json:14` | Most movement is an entrance reveal; few components respond meaningfully to user input or story progress |
| Workloads | `WorkloadFit.tsx:38` renders four offset editorial rows and a velocity rail | The composition is strong, but the rows do not tell a motion-led story and detached rules/numbers can look broken |
| Evidence | `EvidenceBand.tsx:3` contains exact values `30`, `2`, `5`, and `1` | Correct and credible; do not replace with invented percentages |
| Lower funnel | Services, seven FAQs, dominant console CTA, Calendly, contact, legal routes, and four-column footer already exist | Content stays; typography and motion may be polished without changing claims |

### Why now

The current version has the right amount of content but its two largest visuals
make the product harder to understand. Shipping more content or more animation
on top of those diagrams would amplify the problem. This redesign must establish
a clear visual grammar before the landing page is considered ready to commit.

### Definition of success

The redesign is complete when a first-time visitor can answer these questions in
order without decoding a dense diagram:

1. What is Zolli? A fault-tolerant control plane for fragmented compute.
2. What does it unify? Compatible cloud, rented, owned, and everyday machines.
3. Is it real? The exact verified evidence remains visible and qualified.
4. Will it fit? Supported runtimes, libraries, host states, and limitations are
   visible without reading eight rows.
5. How does it work? A seven-step story ends with recovery and one accepted result.
6. What should I do next? `Open console` remains the dominant action.

## 3. Product positioning contract

### Core statement

Zolli is a fault-tolerant distributed-compute control plane. It coordinates
bounded work across fragmented and unreliable machines, verifies checkpoints,
requeues work after node loss, and accepts one idempotent result.

### Supporting mechanism

Operators can connect machines they own or compatible cloud capacity they
provision. Machine supply supports the product; it is not the product itself.

### Claims the landing must not make

- Zolli is not presented as a general GPU marketplace.
- No “earn from your GPU” or public-hosting claim is introduced.
- No provider-native RunPod, Together AI, cloud, or HPC integration is implied
  unless such an integration exists in the product.
- No “every machine is supported” claim is allowed. Use “compatible machines.”
- No invented improvement percentage, customer count, speedup, uptime, savings,
  price, testimonial, or scale claim is allowed.
- The values `30`, `2`, `5`, and `1` remain evidence from documented runs, not
  generalized performance promises.

## 4. Scope

### In scope

1. Replace the hero topology with the approved abstract 3D infrastructure stack.
2. Add selectable hero layers and one stable explanatory panel.
3. Replace the dense workflow topology with a seven-scene scroll storyboard.
4. Replace text-heavy platform rows with recognizable runtime/library icons,
   concise host support, and an honest local machine check.
5. Add section-specific Motion and GSAP behavior that advances the story.
6. Fix detached rules, numbers, and heading line breaks at approved QA widths.
7. Review every landing section for its role in the narrative and remove
   repeated explanations.
8. Preserve the existing evidence, FAQ, services, contact, legal, navigation,
   CTA destinations, and footer disclosure.

### Out of scope

- Console pages, authentication, APIs, database behavior, coordinator behavior,
  node behavior, and the FlashML protocol.
- A real browser-based hardware diagnostic. Browser APIs cannot verify Docker,
  WSL2, CUDA, GPU drivers, CPU architecture, container pulls, or hardened runs.
- New cloud-provider integrations or provider-logo claims.
- WebGL, Three.js, React Three Fiber, shaders, canvas rendering, background video,
  stock imagery, smooth-scroll libraries, cursor replacement, or wheel hijacking.
- A pricing page, GPU marketplace, newsletter, customer-logo strip, testimonial,
  or new lead form.
- Redesigning `/contact`, `/privacy`, `/terms`, or `/security`.
- Committing, pushing, merging, or deploying before the user approves the local
  browser preview.

## 5. What is already working and must not be damaged

- The route’s surface order: dark hero, light evidence, sand platform, dark
  workflow, light workloads, dark architecture, light recovery, sand services,
  light FAQ, orange CTA, dark footer.
- The warm technical palette and mineral-light sections.
- `Open console` as the primary CTA and Calendly as the secondary assisted path.
- The exact Calendly URL, contact email, runtime repository link, and legal routes
  stored through the existing marketing constants.
- The exact evidence values and qualifiers in `EvidenceBand.tsx`.
- Native FAQ `details`/`summary` behavior.
- The early-product and FlashML-runtime disclosure in the footer.
- Server-visible headings, copy, links, and actions before animation hydration.
- Reduced-motion, keyboard, focus, and minimum-target requirements.

## 6. Page narrative

The section order stays unchanged. Each section answers one question and hands a
single idea to the next section.

| Order | Surface | Section | Question answered | Story handoff |
|---:|---|---|---|---|
| 1 | Graphite | Hero | What does Zolli unify? | Many compute layers become one fault-tolerant fleet |
| 2 | Ivory | Evidence | Is this only a concept? | Show the exact verified runs and recovery evidence |
| 3 | Sand | Platform support | Will my software and machine fit? | Familiar runtimes and honest host states reduce uncertainty |
| 4 | Graphite | How Zolli works | How does one job survive? | Seven scenes show capacity, work, checkpoint, failure, recovery, acceptance |
| 5 | Ivory | Workload fit | Which workloads benefit? | Connect the recovery contract to four real workload families |
| 6 | Graphite | Architecture | What makes the behavior possible? | Explain Control, Execution, and Integrity without replaying the workflow |
| 7 | Ivory | Recovery proof | What exactly happens after loss? | Show `35 → 30 → 5` and the real event sequence |
| 8 | Sand | Professional services | How can a team adopt it? | Offer assisted assessment and integration |
| 9 | Ivory | FAQ | What are the boundaries? | Resolve risk, support, pricing, and security questions |
| 10 | Orange | Closing action | What should I do now? | Open the console first; talk to Zolli second |

Hero, workflow, and recovery are distinct:

- **Hero:** infrastructure unification.
- **Workflow:** operational sequence.
- **Recovery:** proof of the fault-tolerance claim.

No section may reuse the same four-corner node topology.

## 7. Visual system

### 7.1 Surface balance

Retain the established target balance:

- approximately 35% graphite for active systems and mechanics;
- approximately 55% mineral ivory/sand for explanation and confidence;
- approximately 10% orange for focus, state transitions, and conversion.

This is a composition target, not a rigid alternation rule. Do not turn later
sections dark merely to make motion look more dramatic.

### 7.2 Color roles

| Token | Role |
|---|---|
| Graphite `#0B0D0E` | Hero, active system stages, footer |
| Raised graphite `#15191A` | Contained interfaces and selected infrastructure plane |
| Mineral ivory `#F2EFE6` | Evidence, workloads, recovery, FAQ |
| Sand `#DED8CB` | Platform support and services |
| Orange `#F36B32` | Control field, active tab, dominant action, key transition |
| Green `#4BA77B` | Proven, verified, accepted states only |
| Amber | Preview, warning, or lost-heartbeat states only |

Do not introduce neon purple, electric blue, aurora gradients, generic glowing
orbs, or multicolor AI imagery. Translucency is allowed inside the hero planes,
but text always sits on an opaque, contrast-safe surface.

### 7.3 Typography and line continuity

- Keep Instrument Sans for display/body and Geist Mono for protocol/state.
- Hero H1 remains no more than three lines at 1024px and no more than four lines
  at 375px.
- Section headings must not leave a one-word final line at 1440×900, 1024×768,
  768×1024, 390×844, or 375×812.
- Use balanced wrapping and semantic phrase spans. Do not add arbitrary `<br>`
  elements that break at another viewport.
- Body copy stays between 45 and 65 characters per line where space permits.
- A row number must remain visually attached to its heading. A horizontal rule
  must begin from the same content grid, not float in a distant column.
- The professional-services heading may reduce in size or widen its text column;
  it must not end with an isolated “together.” line.

### 7.4 Component language

Use a small vocabulary of editorial rows, rails, layered planes, stable detail
panels, and compact protocol states. Avoid repeated equal cards, oversized pills,
glassmorphism, fake terminal windows, and decorative charts.

## 8. Hero: layered compute fabric

### 8.1 Copy and conversion hierarchy

Keep the eyebrow `Fault-tolerant distributed compute` and the approved headline
`Compute that finishes the job.` Add one direct product-definition sentence:

> Zolli unifies compatible cloud capacity, rented compute, owned GPU
> infrastructure, and everyday machines under one control plane, then recovers
> work when a node disappears.

Use this sentence exactly so the four infrastructure sources, the control plane,
and recovery stay explicit.

Actions remain:

1. `Open console` — filled orange, first in DOM order and visual priority.
2. `Talk to Zolli` — outlined/text secondary action to the approved Calendly URL.

### 8.2 Layout

- At 1024px and wider, use a split hero: copy and actions occupy roughly 42%;
  the interactive stack occupies roughly 58%.
- The stack is allowed to exceed the copy column vertically, but may not cover
  navigation, copy, or actions.
- At 768–1023px, copy remains first and the stack becomes a full-width stage
  below it.
- Below 768px, reduce perspective depth and plane separation. The selector
  becomes a horizontally wrapping button group below the stack, followed by the
  detail panel. No horizontal scrolling is required.

### 8.3 The 3D object

Build the object with DOM, CSS 3D transforms, and inline SVG silhouettes. Use no
canvas or WebGL dependency.

The stack has four planes in this exact bottom-to-top order:

1. **Cloud & HPC services** — datacenter/cluster silhouettes and a low grid.
2. **Rented CPU / GPU** — generic provisioned-instance blocks; no vendor logo.
3. **Owned GPU fleet** — repeated GPU/server-bar silhouettes.
4. **Everyday machines** — laptop, workstation, and home-rig silhouettes.

The planes contain no visible words, sentences, provider names, badges, or tiny
labels. Shape, density, and material distinguish them. A vertical orange control
field and a small Zolli core pass through all four planes. This field represents
the control plane; it is not a fifth infrastructure layer.

Default “Unified compute fabric” mode shows all four aligned around the control
field. A restrained task pulse may travel through the aligned stack and resolve
as a green verified signal. The pulse demonstrates coordination, not a benchmark.

### 8.4 Selector and stable detail panel

Provide five selector states in this exact order:

1. Unified compute fabric
2. Everyday machines
3. Owned GPU fleet
4. Rented CPU / GPU
5. Cloud & HPC services

The selector is a semantic tab list. Each tab has a short label, active state,
visible focus state, and a minimum 40px target. Arrow keys move between tabs;
Home and End select the first and last tab. Pointer selection and keyboard
selection produce the same state.

When a layer is selected:

- that plane lifts toward the viewer by one consistent depth increment;
- the other planes remain visible at 20–35% emphasis;
- the control field remains continuous through the stack;
- the stable detail panel updates title, one sentence, and at most three tags;
- no text rotates, skews, or moves with the plane.

The unified state explains the outcome. Individual states explain the type of
capacity, not a provider integration. The rented and cloud/HPC descriptions must
say the operator provisions and runs compatible capacity.

### 8.5 Hero motion sequence

On a motion-capable desktop:

1. Copy and actions are already readable from server HTML.
2. Four separated planes assemble into one stack over 900–1300ms.
3. The orange control field connects the planes over 450–650ms.
4. One task pulse crosses the unified object and resolves once.
5. The stack settles into a low-amplitude idle drift while it is visible.

Pointer parallax is capped at 3 degrees of rotation and 8px of translation.
Selecting a layer takes 350–500ms and uses spring-like easing without bounce.
Idle motion pauses when the hero leaves the viewport or the document is hidden.

Under reduced motion, all planes render aligned, selection changes detail copy
without movement, and no task pulse or idle drift runs.

### 8.6 Accessibility fallback

The 3D object itself is decorative and `aria-hidden`. The tab list and detail
panel carry the meaning. Without JavaScript, the unified description and a
static four-item layer list remain visible. Essential copy and CTAs never depend
on animation state.

## 9. Evidence section

Preserve the current evidence ledger and exact values:

- `30` production attempts;
- `2` proven architectures;
- `5` steps lost, not 35;
- `1` accepted result per task.

Do not count the values upward. Motion is limited to rule expansion, a small
baseline shift, and staggered text reveal. The section is the calm proof beat
after the hero and must remain mineral ivory.

## 10. Platform support: recognizable, concise, honest

### 10.1 Section structure

Replace the eight-row list with three coordinated areas:

1. **Curated runtimes** — a compact icon field for the actual closed image set.
2. **Worker hosts** — three clear host states with short labels.
3. **Check this machine** — a user-triggered, client-only OS-family hint followed
   by the real `flashnode doctor` instruction.

### 10.2 Runtime and library icons

The product evidence in `apps/api/flashml_cloud_api/images.py:108` defines four
curated images: `python-slim`, `sklearn`, `pytorch-cpu`, and `pytorch-cuda`.

The icon field may display only the technologies supported by those image
manifests or the existing job path:

- Python 3.11 / Python workloads
- NumPy
- pandas
- scikit-learn
- SciPy
- PyTorch CPU
- PyTorch CUDA 12.4
- Docker execution
- public GitHub repositories with `flashml.yaml`

Use the installed `simple-icons` package when it contains the official mark and
a restrained text/monogram fallback when it does not. Every icon has a visible
short label. Icons are not a logo cloud and do not loop in an infinite marquee.

On hover or focus, one stable explanatory line identifies the curated image or
job path that provides the technology. Do not show every explanation at once.

### 10.3 Host states

Show exactly:

- macOS arm64 — `Proven`
- Linux x86_64 — `Proven`
- Windows 11 — `Preview`, requiring Docker Desktop with the WSL2 backend

State is always written as text and never communicated by color alone. Hosting
categories such as local machine, owned server, and operator-provisioned cloud
instance may use generic silhouettes. Do not show provider logos as integrations.

### 10.4 “Check this machine” interaction

The check runs only after the visitor selects `Check this machine`. It reads the
best available browser platform hint locally and sends nothing to an API.

The result contract is exact:

| Detected family | Required result copy |
|---|---|
| macOS | “macOS detected. Production proof covers arm64; this browser cannot verify CPU architecture.” |
| Linux | “Linux detected. Production proof covers x86_64; this browser cannot verify CPU architecture.” |
| Windows | “Windows detected. Current support is Preview through Docker Desktop and WSL2; this browser cannot verify those prerequisites.” |
| Other, mobile, or unavailable | “This browser cannot verify a supported worker host.” |

Every result ends with:

> Run `flashnode doctor` for a real host check.

The UI must never say “Your machine is supported” based only on browser data.
It must never infer Docker, CUDA, WSL2, GPU, driver, or container readiness.

### 10.5 Platform motion

On section entry, runtime icons assemble onto one execution rail in a 500–800ms
stagger. Selecting or focusing an icon moves only the active indicator and
updates one stable sentence. Host-state indicators reveal after the runtime rail.
The machine-check result uses a short opacity/translate transition.

## 11. How Zolli works: seven-scene storyboard

### 11.1 Reason for replacement

The current topology keeps nodes, connections, control plane, job, task tokens,
checkpoint, failed node, requeue path, accepted result, and protocol events in
one frame. The replacement must trade simultaneous completeness for sequential
clarity.

### 11.2 Desktop behavior

At 1024px and wider, a visual stage remains pinned while seven text steps scroll.
Each step swaps to a purpose-built scene. The stage may retain a small position
anchor, but it must not keep a fully populated network graph behind every scene.

Each scene may contain at most five primary objects, two connecting paths, one
active protocol label, and one short state sentence. Non-active objects fade out
or leave the stage. A first-time visitor should be able to describe the scene
from a one-second glance.

### 11.3 Exact seven steps

| Step | Heading | Visual scene | Protocol/state label |
|---:|---|---|---|
| 1 | Connect compatible machines | Four infrastructure-source silhouettes move toward one Zolli workspace boundary | `Machines connected` |
| 2 | Make capacity visible | Three compact node records report capability and health to one control-plane rail | `Capacity visible` |
| 3 | Submit one job | A repository tile and `flashml.yaml` tile combine into one job capsule | `Job ready` |
| 4 | Split and lease tasks | One job capsule divides into bounded task tokens assigned across available nodes | `LEASE_CLAIMED` |
| 5 | Verify a checkpoint | Parallel progress converges into one hash-verified checkpoint marker | `CHECKPOINT_MANIFEST_COMMITTED` |
| 6 | Recover after node loss | One node drops, its lease expires, and only the unfinished task resumes from the checkpoint on another node | `NODE_HEARTBEAT_LOST` then `TASK_REQUEUED` |
| 7 | Accept one result | The resumed task joins completed work and one final result resolves; a duplicate outline is visibly rejected | `TASK_COMMIT_ACCEPTED` |

The text source moves to one shared workflow data module. Hero, workflow, and
recovery must not maintain competing copies of protocol claims.

### 11.4 Scroll and transition rules

- Native vertical scroll remains authoritative.
- Pinning begins below the sticky navigation and ends when the seventh step ends.
- Each step occupies enough scroll distance to read, but no blank spacer exceeds
  one viewport height.
- Scene changes use transform, opacity, SVG stroke, and clip-path only.
- ScrollTrigger controls the seven scene milestones. Motion controls local
  presence transitions inside a milestone.
- Separate wrapper elements prevent GSAP and Motion from writing transforms to
  the same DOM node.
- Protocol labels appear only in the step where they matter; there is no
  five-column protocol ticker under the stage.

### 11.5 Tablet, mobile, reduced motion, and no JavaScript

Below 1024px, do not pin. Render seven ordered step articles. Each article pairs
its copy with a static simplified scene and enters once with Motion
`whileInView`. Under reduced motion, render final visible scenes with no entry
movement. Without JavaScript, the seven headings, descriptions, state labels,
and static scene fallbacks remain readable.

## 12. Workload-fit motion and line repair

Keep the four approved workload families:

1. Federated training
2. Hyperparameter search
3. Sharded data processing
4. Checkpointable model training

The section remains ivory and editorial. Improve it as follows:

- The velocity rail responds to native scroll without trapping horizontal input.
- Each row’s index, heading, body, and rule enter as one visual unit.
- The rule grows from the index toward the copy; it never appears detached.
- Rows reveal in the same order as reading order, even though their desktop
  positions remain offset.
- One key phrase in each description receives a short mask reveal; body copy does
  not bounce or continuously move.
- On mobile, remove horizontal rail movement and use a simple staggered entry.
- Static text remains fully visible before hydration and under reduced motion.

## 13. Architecture, recovery, and lower-funnel story check

### Architecture

Keep Control, Execution, and Integrity as the three modules. This section explains
the mechanisms that make the workflow true; it does not repeat the seven scenes.
Connections between modules may draw once on entry. Module copy must answer:

- Control: who owns work now?
- Execution: where can the bounded task run?
- Integrity: which checkpoint and result can be trusted?

### Recovery proof

Keep the exact sequence `failure at step 35 → checkpoint at step 30 → 5 steps
lost`, the real event names, and the sample-data disclosure. Motion may stack the
three recovery states once, then stop. This is the proof climax, not another full
workflow diagram.

### Professional services

Keep the four services and one shared Calendly action. Repair the large heading
wrap, preserve a calm sand surface, and use a restrained rule reveal. Do not add
sales claims, response-time promises, or service-level language.

### FAQ, closing action, and footer

Keep the seven native disclosures, the orange closing chapter, dominant console
action, secondary Calendly action, contact email, four footer groups, legal
routes, and early-product disclosure. FAQ motion remains short and local. The
footer has no spectacle.

## 14. Motion architecture

### 14.1 Library ownership

No new runtime dependency is allowed.

- **GSAP + ScrollTrigger:** page-entry sequencing, pinned workflow milestones,
  scroll-linked workload rail, and scoped SVG path drawing.
- **Motion (`motion/react`):** tab selection, layer presence, local hover/press,
  detail-panel transitions, `whileInView` row reveals, and small state changes.
- **CSS:** low-cost hero plane materials, grain, and visibility-controlled idle
  drift.

GSAP and Motion must not animate the same property on the same element. Use an
outer scroll wrapper and inner interactive wrapper when both systems contribute.

### 14.2 Story beats

| Section | Motion purpose | Motion ceiling |
|---|---|---|
| Hero | Assemble fragmented layers, bind them, allow inspection | One entrance sequence, one pulse, selected-layer motion, low idle drift while visible |
| Evidence | Establish credibility | Rule and text reveal only; no counters |
| Platform | Make familiar support scannable | Icon assembly and one active explanation |
| Workflow | Teach execution and recovery | Seven scroll milestones; no looping |
| Workloads | Connect use cases to the recovery contract | One scroll rail plus four row reveals |
| Architecture | Expose supporting mechanisms | One connection draw and module emphasis |
| Recovery | Prove the failure path | One stack/ledger sequence |
| Services/FAQ | Let the page breathe | Short local reveals only |
| Closing CTA | Resolve to action | One `TASK_COMMIT_ACCEPTED` signal |

### 14.3 Global motion rules

- Animate transforms, opacity, SVG stroke properties, and clip paths only.
- Never intercept wheel or touchmove events.
- No smooth-scroll layer.
- Essential text and actions are visible in server-rendered HTML.
- All continuous animation pauses offscreen and when `document.hidden` is true.
- Pointer effects run only for fine pointers.
- Resize and orientation changes rebuild scoped triggers without duplicates.
- Development remounts clean up every listener, timeline, and ScrollTrigger.

### 14.4 Reduced motion

When `prefers-reduced-motion: reduce` is active:

- hero planes are aligned and static;
- layer selection updates copy without spatial movement;
- no idle drift, pulses, pinning, scrubbing, velocity rail, or path drawing runs;
- all workflow scenes render as static ordered content;
- disclosure transitions are near-instant;
- section order, copy, evidence, actions, and status remain identical.

## 15. Component and data boundaries

Recommended component boundaries:

| File | Responsibility |
|---|---|
| `components/landing/Hero.tsx` | Server-rendered hero copy, actions, and client visual boundary |
| `components/landing/HeroInfrastructureStack.tsx` | Interactive 3D stack, selector, stable detail panel, and motion cleanup |
| `components/landing/HeroSystemStage.tsx` | Remove after the new stack replaces it; do not keep two hero visuals |
| `lib/landing/platform.ts` | One typed source for hero layers, curated runtime labels, host states, and detector result copy |
| `components/landing/PlatformSupport.tsx` | Server-rendered section composition and static fallback |
| `components/landing/MachineCompatibilityCheck.tsx` | Client-only, user-triggered local OS-family hint |
| `components/landing/SystemJourney.tsx` | Seven-step text/story orchestration and desktop pinning |
| `components/landing/WorkflowScene.tsx` | One visual scene renderer keyed by the shared workflow step |
| `lib/landing/workflow.ts` | Exact seven steps and protocol/state labels |
| `components/landing/WorkloadFit.tsx` | Workload copy, row alignment, and server-visible structure |
| `components/landing/WorkloadVelocityRail.tsx` | Scroll-linked rail with mobile/reduced fallback |
| `components/landing/motion/LandingMotionProvider.tsx` | Shared reduced-motion, pointer, visibility, and breakpoint signals |
| `app/globals.css` | Landing tokens, CSS 3D materials, surface resets, and reduced-motion fallback |

Do not put platform or workflow claims inside animation code. Motion components
receive typed data and render it. Browser detection logic is a pure mapping from
an observed platform family to one of four result objects, making it unit-testable.

## 16. Responsive and interaction requirements

### Desktop, 1440×900

- Hero copy, primary CTA, full stack, selector, and detail panel fit without
  clipping or overlap.
- The current selected plane remains identifiable at the most extreme allowed
  pointer tilt.
- Workflow pin leaves the sticky navigation unobstructed.

### Small desktop/tablet landscape, 1024×768

- Hero headline uses no more than three lines.
- Stack labels remain outside the 3D object.
- Workflow pin is allowed only if stage and active copy remain simultaneously
  readable; otherwise the non-pinned layout is used at exactly this width.

### Tablet portrait, 768×1024

- Hero is stacked vertically.
- No workflow pinning.
- Selector wraps without horizontal scrolling.

### Mobile, 390×844 and 375×812

- No horizontal overflow.
- Primary CTA appears before the hero visual in reading order.
- 3D depth is reduced; no plane leaves the viewport.
- All five selector states remain reachable with 40px targets.
- Runtime icons remain labeled and do not become an unlabeled logo cloud.
- Workflow scenes are static or one-shot and follow their text.
- No heading ends in a one-word orphan caused by the layout.

## 17. Accessibility

- Preserve the skip link, landmark order, heading order, visible focus ring, and
  40px minimum interactive targets.
- The hero selector follows the WAI-ARIA tab pattern and works with pointer,
  Tab, arrow keys, Home, and End.
- The 3D stack is decorative; equivalent meaning is present in selector/detail
  text.
- Platform icons have visible labels and accessible names.
- Proven, Preview, warning, verified, and accepted states include text.
- The machine check is initiated by a button and announces its result through a
  polite live region without moving focus.
- Pinned content never traps keyboard focus or obscures a focused element.
- Light and dark normal text meet WCAG AA contrast.
- Reduced-motion users receive identical information and actions.

## 18. Performance and failure handling

- Add zero runtime dependencies and zero hero image/video requests.
- Use DOM, inline SVG, and CSS transforms for the stack.
- The production build must contain no canvas/WebGL/Three.js import on the
  landing route.
- Capture the current landing route’s first-load JavaScript before implementation;
  the redesign may increase it by no more than 20KB gzip because the required
  libraries are already present.
- No layout shift may be introduced by hydration; the hero and workflow reserve
  their final responsive space in server HTML.
- No horizontal overflow is allowed at the five QA widths.
- After sequences settle, browser profiling must show no continuous work except
  the visible hero’s low-cost CSS drift.
- When the tab is hidden or the hero is offscreen, no continuous hero animation
  remains active.
- If Motion or GSAP initialization fails, all copy, links, statuses, and static
  diagrams remain visible and usable.

## 19. Testing plan

Implementation follows test-first development.

### Automated tests

| Layer | Minimum coverage | Required cases |
|---|---:|---|
| Static/SSR component contracts | 10 | Exact section order/surfaces; hero copy/CTAs; exact four layers plus unified state; no text in the visual stack subtree; server-visible fallback; exact evidence |
| Interaction/unit | 8 | Five hero tab states; keyboard mapping; four machine-detection result branches; no false “supported” result |
| Story/data contracts | 8 | Exact seven workflow steps; exact five protocol events; shared data use; platform image/library list matches the curated registry; host states remain Proven/Proven/Preview |
| Motion guards | 6 | Reduced-motion final state; no wheel/touch interception; no layout-property animation; visibility pause; cleanup; no WebGL/canvas import |

Update `landing-cinematic.test.ts` for surface and motion contracts, update
`landing-expansion.test.ts` for preserved evidence and lower-funnel contracts,
and add `landing-infrastructure-story.test.ts` for the hero-layer, machine-check,
workflow-scene, and anti-overclaim contracts.

### Browser QA

Capture and inspect at least these states:

1. Hero unified state.
2. Hero everyday-machines state.
3. Hero owned-GPU state.
4. Hero rented-compute state.
5. Hero cloud/HPC state.
6. Platform runtime field and each host status.
7. Machine-check macOS, Linux, Windows, and unknown results using stubbed platform
   hints.
8. All seven workflow milestones.
9. Workload section before, during, and after its scroll motion.
10. Recovery proof, services, FAQ, closing action, and footer.
11. Keyboard-only selector, FAQ, CTA, and footer navigation.
12. Normal-motion and reduced-motion variants.

Run the matrix at 1440×900, 1024×768, 768×1024, 390×844, and 375×812. Check
console errors, hydration warnings, focus visibility, route destinations,
overflow, heading wraps, and motion cleanup.

### Verification commands

- focused Vitest landing tests;
- full Vitest suite;
- ESLint;
- `tsc --noEmit`;
- production Next.js build;
- `git diff --check`.

## 20. Acceptance criteria

1. The hero shows an abstract four-plane infrastructure stack rather than a node
   topology or workflow diagram.
2. The stack contains no visible text; all explanation remains in a stable,
   readable two-dimensional selector/detail area.
3. The four planes appear in the required bottom-to-top infrastructure order.
4. The default unified state visibly aligns all four planes through one continuous
   control field.
5. All five selector states work by pointer and keyboard and retain visible focus.
6. Selecting a plane lifts only that plane, keeps the other layers contextually
   visible, and updates one stable detail panel.
7. The hero product sentence names compatible fragmented capacity, one control
   plane, and recovery after node disappearance.
8. `Open console` remains more visually prominent and earlier in reading order
   than Calendly.
9. Evidence remains exactly `30`, `2`, `5`, and `1`, with current qualifiers and
   no fabricated percentage.
10. Platform support presents the required runtime/library names with visible
    labels and concise explanations instead of eight long rows.
11. Platform host support remains macOS arm64 Proven, Linux x86_64 Proven, and
    Windows 11 Preview.
12. The machine check never claims compatibility from browser data and always
    directs the user to `flashnode doctor`.
13. The workflow contains exactly seven ordered steps and ends with
    `TASK_COMMIT_ACCEPTED`.
14. Each desktop workflow scene stays within the five-object, two-path, one-active-
    event density limit.
15. The hero, workflow, architecture, and recovery sections do not reuse the same
    topology composition.
16. Workload numbers, rules, headings, and descriptions enter and align as one
    unit; no detached number or rule remains.
17. No section heading has a one-word final line at the five approved QA widths.
18. Dark, ivory, sand, and orange chapter order remains unchanged.
19. Motion has a visible narrative purpose in hero, platform, workflow, workloads,
    architecture, recovery, and closing CTA, with calm intervals between peaks.
20. Reduced motion removes 3D movement, idle loops, pinning, scrubbing, and path
    drawing while preserving all information and actions.
21. JavaScript failure leaves readable product copy, support information, seven
    workflow steps, evidence, FAQs, and CTAs.
22. The redesign adds no runtime dependency, WebGL, canvas, video, smooth scroll,
    wheel interception, or provider-integration claim.
23. Services, FAQ, contact, legal routes, footer groups, contact email, Calendly,
    and FlashML disclosure remain correct.
24. Automated checks, production build, and the full browser QA matrix pass.
25. The user approves the completed local preview before any commit, push, merge,
    or deployment occurs.

## 21. Implementation sequence and dependencies

```text
1. Shared story/platform data contracts
   ├── 2. Hero infrastructure stack
   ├── 3. Platform support + machine hint
   └── 4. Seven workflow scenes
          └── 5. Workload/architecture/recovery motion polish
                 └── 6. Responsive, accessibility, and browser QA
```

The shared data contracts come first so hero, support, workflow, tests, and
accessible fallbacks cannot drift. Hero, platform, and workflow can then be built
as isolated slices. Cross-section motion polish follows only after each section is
clear in a static state. Browser QA is last because it validates the complete
surface and transition sequence.

## 22. Planning estimate

This is a planning estimate, not a delivery promise.

| Slice | Human-team estimate | Agent-assisted implementation estimate |
|---|---:|---:|
| Shared data and test contracts | 0.5 day | 1–2 hours |
| Hero 3D stack and interaction | 1.5–2 days | 3–5 hours plus visual review |
| Platform icons and machine hint | 1 day | 2–3 hours |
| Seven-scene workflow | 1.5–2 days | 3–5 hours plus visual review |
| Workload/architecture/recovery polish | 1 day | 2–3 hours |
| Accessibility, responsive QA, and fixes | 1.5 days | 3–5 hours |
| **Total** | **7–8 days** | **14–23 hours plus user review gates** |

## 23. Rollback

No backend, data, API, or infrastructure migration is involved. Rollback is a
landing-only revert:

1. restore `Hero.tsx` and `HeroSystemStage.tsx`;
2. restore the current PlatformSupport, SystemJourney, WorkloadFit, and motion
   files;
3. remove new landing-only data/components/tests;
4. restore landing CSS additions;
5. rerun landing tests and production build.

The preview-before-commit gate makes rollback during development immediate: keep
the existing components intact until the replacement preview passes visual and
behavioral review, then remove the superseded hero stage in the same approved
implementation slice.

## 24. File reference

| File | Planned change |
|---|---|
| `apps/web/app/(marketing)/page.tsx` | Preserve section order and surface wrappers; update imports only as needed |
| `apps/web/components/landing/Hero.tsx` | Replace topology boundary with infrastructure-stack boundary and product definition |
| `apps/web/components/landing/HeroSystemStage.tsx` | Remove after approved stack replacement |
| `apps/web/components/landing/HeroInfrastructureStack.tsx` | Add 3D stack, five-state selector, detail panel, and fallbacks |
| `apps/web/components/landing/PlatformSupport.tsx` | Replace eight rows with runtime icon field, host states, and machine hint boundary |
| `apps/web/components/landing/MachineCompatibilityCheck.tsx` | Add honest user-triggered local platform hint |
| `apps/web/components/landing/SystemJourney.tsx` | Replace dense topology with seven-scene orchestration |
| `apps/web/components/landing/WorkflowScene.tsx` | Add isolated visual scene renderer |
| `apps/web/components/landing/WorkloadFit.tsx` | Bind rows/rules and add story-led reveal hooks |
| `apps/web/components/landing/WorkloadVelocityRail.tsx` | Refine scroll response and mobile fallback |
| `apps/web/components/landing/SystemModules.tsx` | Clarify Control/Execution/Integrity motion without workflow repetition |
| `apps/web/components/landing/RecoveryDemo.tsx` | Preserve proof and separate it from workflow composition |
| `apps/web/components/landing/ProfessionalServices.tsx` | Repair heading wrap and add restrained rule reveal |
| `apps/web/components/landing/motion/LandingMotionProvider.tsx` | Expose visibility/pointer signals if not already available |
| `apps/web/lib/landing/workflow.ts` | Expand to exact seven-step source of truth |
| `apps/web/lib/landing/platform.ts` | Add typed layer/runtime/host/detection source of truth |
| `apps/web/app/globals.css` | Add CSS 3D materials, responsive rules, and reduced-motion fallbacks |
| `apps/web/lib/landing-cinematic.test.ts` | Update cinematic, motion, and responsive contracts |
| `apps/web/lib/landing-expansion.test.ts` | Preserve evidence, support, CTA, FAQ, footer, and public-route claims |
| `apps/web/lib/landing-infrastructure-story.test.ts` | Add focused layer, detector, seven-scene, and anti-overclaim contracts |
| `apps/web/package.json` | No dependency change |
