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

## Vocabulary

The console UI says **workspace**; the API, the database and the TypeScript
types say **pool**. They are the same thing. The rename was deliberate and
UI-only — see
`docs/superpowers/specs/2026-08-03-workspace-console-design.md` §1.5. Do not
"fix" one side to match the other: renaming through the API would be a
breaking change to a shipped release plus a table migration, for a naming
win.

## Current state (July 2026)

- `apps/api/` — FastAPI control plane (:8000): node registry + heartbeats
  (SQLite), job submission/status/events proxied to FlashRuntime, Alibaba
  integration panel. Tests: `pytest` (7). Postgres/Redis remain the target
  for post-POC state (see hard rule 3 — SQLite is POC-scale only).
- `apps/web/` — POC pages `/nodes`, `/jobs`, `/jobs/[id]`, `/submit`,
  `/integration` on the existing dark design system (`lib/poc-api.ts` client;
  legacy prototype pages retained). `npm run dev` + NEXT_PUBLIC_CLOUD_API.
- `infra/` — kustomize base + local (Kind/MinIO/registry) + alibaba
  (ACK/ACR/OSS/SLS/sandbox) profiles; `scripts/local`, `scripts/alibaba`.
  Workspace-root Makefile drives everything (`poc-local-*`, `poc-ack-*`).

## Near-term direction (workspace `PROGRESS.md` is the authoritative log)

This repo stays a **thin business wrapper** — coordination (leases, task
expansion, checkpoints, recovery) lives in FlashRuntime; if this API dies
mid-job, running leases must keep working.

**Status note (July 2026):** the local milestone was delivered entirely in
FlashRuntime's *self-hosted profile* — the runtime service itself now hosts
the minimal node registry, join codes, local artifacts, and a built-in
dashboard at its `GET /`. This repo's job is unchanged but its integration
target moved: the POC-era `apps/api` proxies the old job surface only and
does **not** yet front the lease coordinator, checkpoint endpoints, or
device nodes.

What lands here next:
- **Stage 5 (Alibaba, next up):** ECS-first deployment — one small ECS runs
  coordinator + this API + web via compose (images in ACR); artifacts to
  OSS; this API mints short-lived STS upload creds (RAM role); logs to SLS.
  Re-point `apps/web` + `apps/api` at the current runtime surface (leases,
  nodes, checkpoints, artifacts) and layer business auth over the runtime's
  join codes. `poc-ack-*` profiles return with the ACK pool.
- **Stage 6 (cloud half):** SQLite → ApsaraDB RDS PostgreSQL (hard rule 3
  finally honored); SSE event streaming replaces 2-second polling; ACK node
  pool as the Mode B execution pool alongside device nodes.
- **Stage 8:** metrics page computed from the ledger (goodput, MTTD, MTTR,
  lost work, cost per completed job) — every needed event already exists.

## Dev workflow

```bash
cd apps/web && npm install && npm run dev
```

Stack: Next.js + TypeScript + Tailwind (web); FastAPI + Postgres + Redis
(services, planned); Supabase/Clerk for auth (planned).
