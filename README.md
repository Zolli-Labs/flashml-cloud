# FlashML Cloud

> **The private managed application of the FlashML system.** FlashML Cloud
> authenticates users and hosts, matches workloads to capacity, applies trust
> and economic policy, records contribution, and handles billing, credits,
> and enterprise controls. Deployed at **zolliai.com**.

**PROPRIETARY — © Zolli Labs. All rights reserved. This repository is
private and must never be made public or have its contents copied into the
public repositories.**

FlashML Cloud is one of three components in the FlashML system:

- **[flashnode](https://github.com/Zolli-Labs/flashnode)** (public) — host
  agent installed by resource contributors.
- **[flashruntime](https://github.com/Zolli-Labs/flashruntime)** (public) —
  workload protocol and execution layer. This repo imports its versioned
  protocol package for all wire schemas.
- **flashml-cloud** (this repo, private) — the commercial product: control
  plane, marketplace, dashboard, and operations.

Read [`docs/SYSTEM_OVERVIEW.md`](docs/SYSTEM_OVERVIEW.md) for the full
product architecture, and [`AGENTS.md`](AGENTS.md) if you are an AI coding
agent working in this repo.

## Status

**Pre-release.** `apps/web` was seeded (July 2026) from the FlashML
prototype's Next.js dashboard (dark tech design system, training
visualizations). It is being rebuilt around the real objects of the system:
jobs, nodes, leases, recovery timelines, credits.

## Target structure

```
flashml-cloud/
├── apps/
│   ├── web/            # user + host dashboard (Next.js) — seeded
│   └── api/            # managed external API (FastAPI) — planned
├── services/           # control-plane, scheduler, node-registry,
│                       # metering-billing, reliability-graph — planned
├── packages/           # shared UI + internal contracts — planned
├── infrastructure/     # deploy configs — planned
└── docs/
```

## Surfaces (from the system design)

| Surface | Functions |
|---|---|
| Developer | Projects, API keys, job submission, live progress, logs, artifacts, recovery timeline, spend |
| Host | Node onboarding, diagnostics, eligible workloads, tasks, credits, reliability, earnings |
| Control plane | Job/node state, queue, scheduler, failure classifier, checkpoint catalog, event ledger, policy |
| Commercial | Usage metering, credits, invoices, settlement |
| Trust | Image allowlists, trust tiers, regional routing, audit history |

## Dev workflow

```bash
cd apps/web
npm install
npm run dev
```

## Boundary principle

The open runtime must stay genuinely useful without this cloud. This repo
wins by operating the network better — marketplace density, reliability
data, policy, support — not by crippling the public repositories. Anything
FlashNode needs on the wire lives in `flashruntime`, never here.
