# Zolli vision-led landing story

- **Status:** approved in conversation on 2026-08-12
- **Scope:** landing-page narrative, copy hierarchy, section order, and one
  hero copy transition
- **Visual constraint:** preserve the current visual system and the existing
  coordinator-map SVG exactly

---

## 1. Purpose

The current landing page begins with the mechanism: fault-tolerant distributed
compute, leases, checkpoints, recovery events, and runtime architecture. Those
details are real, but a visitor has to understand the system before learning
why it matters.

The revised landing page leads with the market Zolli is building:

> Computing power should flow to where it is available, suitable, and
> competitively priced instead of remaining locked inside one provider or
> sitting unused on someone else's machine.

Zolli connects two groups:

1. people who need affordable computing capacity; and
2. people who have machines or GPUs they are willing to make available.

Zolli's allocation, routing, checkpointing, and recovery system makes that
market practical. These mechanisms support the vision; they are not the first
thing the page asks a visitor to understand.

## 2. Audience and conversion goal

The primary audience is a person seeking compute: a student, junior ML
developer, researcher, independent builder, or small AI team using some
combination of a laptop, lab workstation, rented GPU, or cloud account.

The secondary audience is a person or team with underused machines that could
provide capacity to the network.

The page must be understandable without prior knowledge of distributed
systems. It may still provide technical depth later for experienced engineers.

The two conversion paths are:

- **Get early access** for someone who needs compute.
- **Provide compute** for someone who wants to connect a machine.

"Talk to Zolli" remains a secondary route for teams that want help.

## 3. Narrative principles

### 3.1 Vision first, proof second, mechanism third

The page tells visitors, in order:

1. the change Zolli wants to create;
2. the problem that makes the change valuable;
3. how both sides benefit;
4. why Zolli can make the model reliable;
5. which workloads and machines fit; and
6. the technical evidence behind the claim.

### 3.2 Plain language before product vocabulary

Primary headings and introductory paragraphs must avoid unexplained terms such
as "bounded lease," "checkpoint manifest," "idempotent commit," "control
plane," and "federated training." These terms may remain in technical
demonstrations after their practical meaning has been explained.

For example:

- "Zolli records your progress" precedes "checkpoint manifest."
- "Another machine continues the work" precedes "task requeued."
- "One result is accepted" precedes "idempotent commit."

### 3.3 Ambitious but honest

The landing page represents the company vision, not merely a list of completed
features. It must also distinguish that vision from current availability.

- Use "Zolli is building" or an **Early network** label when describing the
  full open market.
- Host cash earnings are a vision. Early testing currently uses Zolli credits;
  cash withdrawal is not live.
- More competition is designed to lower compute prices. Do not promise that
  every Zolli job is always cheaper.
- Do not imply that every cloud provider, machine, or ML workload works today.
- Do not describe tightly synchronized large-model training across distant
  machines as a supported use case.

The page should feel confident because it explains the destination and shows
real evidence of progress, not because it hides the product's stage.

## 4. Page story

### 4.1 Hero — the open compute network

**Eyebrow**

> THE OPEN COMPUTE NETWORK

**Stable headline**

> Computing power, without the lock-in.

**Stable explanation**

> Zolli is building a network that connects people who need compute with
> machines ready to work—across personal hardware, community hosts, and cloud
> providers.

The stable headline gives the page one durable promise for comprehension,
search indexing, and accessibility. A two-state copy animation then explains
the two sides of the market.

**Demand state**

> Need computing power?<br>
> Access more compute at a competitive price.

**Supply state**

> Have unused computing power?<br>
> Host it and earn from the work it completes.

**Actions**

- Get early access
- Provide compute

#### Hero motion contract

The demand state appears first. It pauses long enough to read, then moves
upward while the supply state rises into the same reserved copy area. The two
states continue as a quiet loop.

- Animate only opacity and vertical translation.
- Reserve enough height for either state so the layout never jumps.
- Keep the stable headline and explanation motionless.
- Do not use an auto-updating live region; automatic announcements would
  repeatedly interrupt screen-reader users.
- With reduced motion enabled, show both states as ordinary stacked copy.
- The message remains understandable if JavaScript or the transition fails.

#### SVG freeze

The existing coordinator-map SVG is preserved exactly. Implementation must not
edit:

- `components/landing/coordinator-map/CoordinatorMap.tsx`;
- any file below `components/landing/coordinator-map/`; or
- `lib/coordinator-map` geometry, paths, phases, labels, or timing.

The new copy transition must not drive, synchronize with, wrap, or otherwise
change the SVG. The map remains the existing visual companion to the new story.

### 4.2 Problem — compute is fragmented

**Headline**

> Compute is everywhere. Access is not.

**Body direction**

People often have a laptop, a lab machine, a rented GPU account, and access to
other cloud providers, but each resource behaves like a separate island. The
user must compare prices manually, repeat setup work, monitor every machine,
and restart interrupted jobs. At the same time, useful machines elsewhere sit
idle.

This section establishes three problems without infrastructure jargon:

- compute is difficult to access when it is needed;
- capacity and pricing are locked into disconnected providers; and
- unused machines cannot easily become useful supply.

### 4.3 New model — an open allocation network

**Headline**

> From isolated machines to an open compute network.

**Body direction**

Zolli brings personally owned machines, team infrastructure, community hosts,
and rented cloud capacity into one allocation path. A user describes the work
and what matters—price, completion time, or hardware—and the network finds
suitable capacity.

This is the category statement. Zolli is not presented as another GPU vendor.
It is the allocation layer across possible sources of compute.

### 4.4 Two-sided value

Present two adjacent, equally understandable paths.

**For people who need compute**

> Access more machines, compare more choices, and avoid depending on one
> provider's price or availability.

Supporting outcomes:

- combine machines already available to the team;
- reach additional community or rented capacity when needed; and
- choose between lower cost and faster completion.

**For people who provide compute**

> Turn unused machines into productive capacity and earn when they complete
> useful work.

Supporting outcomes:

- opt a machine into the network;
- keep ownership visible; and
- receive credit for accepted work.

The supply path includes an **Early network** note explaining that cash payout
is not yet live.

### 4.5 How it works — three human steps

Replace the seven-step mechanism-first introduction with three conceptual
steps:

1. **Tell Zolli what you need.** Describe the work, required hardware, and
   whether price or finish time matters more.
2. **The network finds suitable machines.** Zolli can consider owned,
   community, and rented capacity that fits the work.
3. **Your work continues as capacity changes.** Progress can be recorded and
   interrupted work can continue on another compatible machine.

The existing detailed journey may remain below these steps as an expandable
or later technical explanation. Its event names must not be the visitor's
first explanation of the product.

### 4.6 Reliability — why an open market can work

**Headline**

> Affordable capacity matters only if the work finishes.

**Body direction**

Lower-cost and distributed capacity may disappear. Zolli divides suitable
work, records progress, detects interruption, and gives unfinished work to
another compatible machine. This turns fault tolerance into an economic
enabler: users can consider more kinds of capacity without treating every
machine loss as a complete restart.

The recovery demonstration may retain real protocol events inside its visual
surface. Its introductory copy must first translate them into outcomes.

### 4.7 Product evidence

**Headline**

> A growing network, proven with real work.

Use outcome-level evidence from verified runs, not unqualified scale claims:

- one six-trial model search divided work across a laptop and two rented GPUs;
- a training job moved from an RTX 4090 to an RTX 3090 after the first rented
  machine was destroyed;
- 58 completed epochs survived that machine loss instead of being recomputed;
- hardware-aware placement kept GPU work off an ineligible laptop; and
- completed outputs and checkpoints were mirrored for later access.

Detailed measurements, event names, and experiment qualifications can appear
below each plain-language result. Do not turn the approximately $0.89 test
spend into a general pricing claim.

### 4.8 Workload and machine fit

This section answers one visitor question:

> Can I use Zolli with the work and machines I already have?

**Introductory rule**

> Zolli works best when a job can be divided into separate pieces or can save
> its progress while running.

Pair every example with a concrete machine context.

| What the user wants to do | Plain example | Suitable machine context |
|---|---|---|
| Test model configurations | Run many versions with different settings | laptops, CPU workstations, or rented GPUs |
| Evaluate AI models | Test prompts, datasets, or model versions independently | CPU or GPU machines across a team |
| Process many files | embeddings, OCR, transcription, conversion, or data preparation | supported macOS, Linux, and compatible cloud machines |
| Run simulations | Monte Carlo experiments, research trials, or independent rollouts | mixed personal, lab, and cloud machines |
| Train with recovery | Save progress and continue on another compatible machine | Linux machines with supported NVIDIA GPUs |

#### Compatibility groups

**Proven today**

- macOS on Apple silicon (`arm64`);
- Linux `x86_64` machines;
- Linux hosts with tested NVIDIA GPUs, including RTX 3090, RTX 4090, and RTX
  4000 Ada runs;
- RunPod GPU machines;
- Docker-based workloads; and
- Python 3.11, NumPy, pandas, SciPy, scikit-learn, and PyTorch images already
  represented by the current runtime support surface.

**Preview**

- Windows 11 through Docker Desktop and WSL2.

**Network expansion**

- more cloud providers;
- more GPU and hardware configurations;
- automatic purchasing of capacity; and
- cash earnings for machine hosts.

The expansion list describes direction, not current support. It must be styled
and labelled differently from proven compatibility.

#### Honest exclusion

> Zolli is best for work that can be divided or resumed. It is not currently
> designed for tightly synchronized training where every GPU must communicate
> continuously over a very fast network.

The existing machine compatibility check remains useful after this context. It
must continue to say that browser detection is only a hint and direct visitors
to `flashnode doctor` for an actual host check.

### 4.9 Technical depth

Runtime architecture, platform modules, event-ledger terminology, checkpoint
validation, and deterministic recovery remain on the landing page for
technical evaluators. They move below the vision, benefits, proof, and fit
sections.

The section introduction answers "Why should I trust this?" before naming
internal modules. Technical terms may remain inside diagrams and readouts for
visitors who want the detail.

### 4.10 Professional services

Keep the existing services section near the end. Reframe its introduction as
help connecting an existing fleet or adapting a suitable workload, rather
than as a separate product story.

### 4.11 FAQ

The FAQ must answer the objections created by the vision:

1. **What is Zolli?** An allocation network connecting work with suitable
   owned, community, and rented computing capacity.
2. **Is Zolli another cloud provider?** No. Zolli is building the layer that
   can allocate work across multiple sources of compute.
3. **Can machine owners earn money today?** Host earnings are part of the
   market vision; early testing currently uses Zolli credits and cash payout
   is not live.
4. **Will Zolli always be cheaper?** No. More supply and comparable choices
   create price competition, but the best choice depends on the work,
   availability, and desired finish time.
5. **Which machines work?** Summarize the proven and preview groups, then link
   to the detailed compatibility check.
6. **Which workloads fit?** Work that divides into independent tasks or saves
   progress is strongest; continuously synchronized multi-machine training is
   not the current target.
7. **What happens if a machine disappears?** Zolli can reallocate unfinished
   work and resume supported jobs from recorded progress.
8. **How mature is the network?** Zolli is an early product with verified
   cross-machine runs and a growing provider and host network.

### 4.12 Closing choice

**Headline**

> Join the open compute network.

**Demand action**

> I need compute

Links to the existing early-access or console entry path.

**Supply action**

> I want to provide compute

Links to the existing machine-enrolment path. Supporting copy distinguishes
current testing credits from future cash earnings.

## 5. Recommended section order

The implementation should preserve the current visual language while
reordering or reframing sections into this narrative:

1. Hero
2. Problem
3. Open allocation model
4. Two-sided value
5. Three-step explanation
6. Reliability
7. Product evidence
8. Workload and machine fit
9. Technical depth
10. Professional services
11. FAQ
12. Closing choice

Existing landing components should be reused where their visuals serve this
order. New components are justified only where no existing section can carry
the problem, market model, or two-sided value clearly.

## 6. Scope boundaries

### In scope

- landing-page copy and content hierarchy;
- section ordering needed by the story;
- plain-language headings and explanations;
- the two-state hero copy transition;
- workload examples paired with supported machine contexts;
- current-versus-vision labels;
- CTA labels and destinations using existing routes; and
- tests that protect narrative, accessibility, and truthfulness.

### Out of scope

- visual redesign;
- new color, type, spacing, surface, card, or illustration systems;
- modification or replacement of the coordinator-map SVG;
- backend, scheduler, marketplace, credit, payout, or provider work;
- adding support for a machine, platform, provider, or workload;
- changing the FlashRuntime or FlashNode product identity; and
- presenting Zolli as a chatbot or autonomous agent.

The visible product remains Zolli/Zolli Cloud. FlashML remains the open runtime
and wire protocol underneath.

## 7. Acceptance criteria

### Narrative

- A visitor can explain the two sides of Zolli after reading only the hero and
  the next two sections.
- The problem and value appear before leases, manifests, event names, or
  runtime architecture.
- Fault tolerance is framed as what makes flexible capacity dependable, not
  as the entire market vision.
- Every workload example includes an understandable machine or platform
  context.
- Proven, preview, and expanding support are visually and verbally distinct.
- Cash earnings and broad automatic capacity purchasing are not represented as
  live features.

### Hero and accessibility

- The H1 and primary explanation remain stable while the demand/supply copy
  transitions.
- The transition causes no layout shift.
- Reduced-motion users see both messages without animation.
- Screen readers do not receive repeating automatic announcements.
- The landing remains understandable without the animated transition.
- No coordinator-map or geometry file changes in the implementation diff.

### Quality gates

- Existing landing tests are updated for the new story rather than deleted to
  avoid assertions.
- New tests cover the stable H1, both market states, status disclosures,
  compatibility groups, limitation copy, and both closing actions.
- Web unit tests, TypeScript, lint, production build, and `git diff --check`
  pass.
- Desktop and mobile review confirms that copy remains readable and that the
  existing SVG renders exactly as before.

## 8. Success signal

The page succeeds when a non-expert visitor can answer these questions without
reading the architecture section:

1. What problem does Zolli solve?
2. Why could it lower the cost of compute?
3. How can a machine owner benefit?
4. What kind of work fits?
5. Can my machine participate?
6. What is available now, and what is still the vision?

The first product conversion signal remains an early-access request or a
connected machine, not time spent reading technical diagrams.
