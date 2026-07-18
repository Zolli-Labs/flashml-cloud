# AGENTS.md — flashml-cloud

Context for AI coding agents (Claude Code, Codex) working in this repository.

## What this repo is

The **private managed application** of the FlashML system (Zolli Labs):
control plane, scheduler, marketplace, billing/credits, and the web dashboard
for users and hosts. Domain: zolliai.com. Full product context:
`docs/SYSTEM_OVERVIEW.md`.

Sibling repos (cloned side-by-side under `~/Work/Zolli-Labs/`):
- `../flashruntime` — public protocol + runtime. All wire schemas (job specs,
  node messages, leases, checkpoint manifests) come from its versioned
  protocol package. Install editable for local dev.
- `../flashnode` — public host agent. We never import it and it never
  imports us; we meet only over the flashruntime protocol.

## Hard rules

1. **This repo stays private.** Never copy its code into the public repos,
   and never let public-node compatibility depend on private code.
2. **Protocol lives in flashruntime.** This repo may add internal fields and
   services, but any schema a FlashNode must understand belongs upstream in
   `flashruntime.protocol` — contribute it there first.
3. Nodes are disposable; state is not. Durable state (jobs, attempts, leases,
   checkpoints, failure events, recovery actions, usage, contribution) lives
   in Postgres with an append-only event ledger — never only in memory.
4. Distinguish **attempted** work from **accepted** work everywhere money,
   credits, or metrics are involved. Idempotent commits; no double counting.
5. Recovery actions are typed, deterministic, logged. No LLM-driven recovery.

## Current state (July 2026)

- `apps/web/` — Next.js dashboard seeded from the FlashML prototype (dark
  tech design system, training visualizations). Rebuild pages around jobs,
  nodes, recovery timelines, and credits; keep the design system.
- `apps/api/`, `services/*` — planned (FastAPI, Postgres, Redis). See
  README for the target structure.

## Dev workflow

```bash
cd apps/web && npm install && npm run dev
```

Stack: Next.js + TypeScript + Tailwind (web); FastAPI + Postgres + Redis
(services, planned); Supabase/Clerk for auth (planned).
