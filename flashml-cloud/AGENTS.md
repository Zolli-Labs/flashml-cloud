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

## Granting access and admin (2026-08-04)

Admission and workspace membership are **separate**. An invite joins a pool;
only an admin grants access to the product. `admitted_at` is written in
exactly one place — `approve_access_request`, behind `admin_user`.

**Granting yourself admin.** There is no UI for `is_admin`, deliberately:

```sql
UPDATE public.profiles SET is_admin = true WHERE id = '<your-user-id>';
```

Run it against an account that is **already admitted**. The console replaces
the page for any non-console access state, so a fresh un-onboarded admin
lands on the onboarding form and cannot reach their own queue. It fails
closed and the same SQL digs you out, but it looks like a broken deploy.

**Admitting by hand.** `access_state_for` falls back to `admitted_at` only
when there is **no** `access_requests` row. If a row already exists, it wins:
setting `admitted_at` by SQL on someone with a pending request leaves the
console showing them the waiting screen forever while the API considers them
admitted. Once a request row exists, admit through the queue, never by SQL.

**Revoking is not the inverse.** Clearing `admitted_at` while the request row
still says `admitted` produces an account every API gate refuses while
`GET /me` reports `access: "admitted"` and the console shows it the product.
Row and flag must move together, in one transaction. There is no revoke route
yet; it needs its own task, an audit trail, and a decision about what happens
to the person's pool memberships and running jobs.

**Approval sends mail.** Approving or declining emails the account through
Resend (`mailer.py`), configured by `RESEND_API_KEY` + `EMAIL_FROM`. Both
routes return `emailed`, and copy must reflect that flag rather than assume
either outcome — mail is skipped when no provider is configured, when the
account has no address in `auth.users`, and when the provider refuses. With
mail unconfigured the product behaves exactly as before: the flag is false
and telling the person is manual. Supabase's built-in SMTP (~2/hour
project-wide) is still not usable for this; custom SMTP in the Supabase
dashboard covers auth mail only.

## Private repositories: a GitHub App, not Supabase's GitHub provider

A submitter's private repo is read through a **GitHub App installation**
(`github_app.py`, `migrations/0013`). Three env vars, all or none:
`GITHUB_APP_ID`, `GITHUB_APP_SLUG`, `GITHUB_APP_PRIVATE_KEY`. Unset, private
repos are unavailable and everything else behaves exactly as before.

**This is a second, separate GitHub registration.** It is NOT Supabase's
GitHub sign-in provider, and enabling that provider would not help: an OAuth
`repo` scope is all-or-nothing, tied to one person's account, and tangles
sign-in with authorization. Supabase stays identity-only. Reaching for the
sign-in provider here is the anti-pattern this section exists to prevent.

**Nothing stored is a credential.** `github_installations` holds installation
ids, which are useless without the App private key from the environment. Do
not add a token column — a one-hour installation token is minted at submit
time and cached in memory, and persisting one would recreate exactly the
liability the App design removes.

**The state binding is not optional.** An `installation_id` is not a secret;
GitHub puts it in its own URLs and in the redirect back to us. `POST
/v1alpha1/github/installations` claims a single-use, user-bound state
*before* it asks GitHub anything. Remove that and anyone who learns an id can
attach another organisation's installation to their account and read its
private source. Expired, replayed, unknown and someone-else's all answer the
same 403 on purpose — distinguishing them tells a prober which states exist.

**The composite primary key is load-bearing.** `(installation_id, user_id)`,
because GitHub installs on an *account*: colleagues in one org share an
installation id, and a single-column key locks out everyone after the first.

**Authenticated fetches go to `api.github.com`, not codeload.** codeload is
not the documented host for an App token and 404s with one. See `repo.py`.

Design: `docs/superpowers/specs/2026-08-10-github-app-private-repos-design.md`.

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
make setup                 # once: api venv + web deps
./scripts/dev.sh --all     # coordinator :8100 + API :8000 + console :3000
```

Both from the **repo root**. `cd apps/web && npm run dev` no longer works on
its own: `apps/web/.env.local` was removed on 2026-08-04 (it duplicated the
`NEXT_PUBLIC_*` trio already in `.env.dev`), and `dev.sh` is what exports
those into the environment Next.js reads. Run it bare and the console builds
with no Supabase project, no key and no API base.

Stack: Next.js + TypeScript + Tailwind (web); FastAPI + Postgres + Redis
(services, planned); Supabase/Clerk for auth (planned).
