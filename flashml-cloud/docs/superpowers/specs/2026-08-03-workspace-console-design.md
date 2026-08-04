# Workspace console — pool-scoped IA, personal fleet, tabbed surface

**Date:** 2026-08-03
**Status:** approved design (brainstormed with the owner).
**Origin:** the owner's own read of the console after
`2026-08-03-pools-v1-1-design.md` shipped. The complaint, verbatim: *"it's
so simple right now, everything is on the same page — the pools showing the
name, member, your machine, connect a machine, everything in the same page.
I want to create this like a workspace for collaboration and clean, with
different tabs for different purposes."*

The literal symptom is `app/(console)/pools/[poolId]/page.tsx` — 668 lines
stacking a member table, a per-device opt-in list, a Colab/RunPod connect
panel and an invite manager into one scroll. The real problem is one level
up: the console's information architecture is personal (my jobs, my
machines, and a *list of pools* off to the side), while the product is a
collaboration product. This design makes the pool the organising unit.

---

## 1. Decisions made during brainstorming

1. **The pool IS the workspace.** The whole console scopes to a selected
   workspace via a switcher at the top of the rail — not merely a tabbed
   pool detail page. Rejected: tabbing `/pools/[poolId]` alone (fixes the
   scroll, leaves the IA personal) and a per-pool sub-shell under a global
   nav (two navigation grammars).
2. **Machines are personal property; jobs always belong to a workspace.**
   The owner's call, and sharper than any option offered. You enrol, name
   and revoke machines in a permanent personal area, then tick which
   workspaces each one serves — which is exactly the `machine_pools` opt-in
   v1.1 already shipped. Jobs have no personal mode at all: **the "No pool —
   public queue" option is removed.** To run anything you create or join a
   workspace first.
3. **Pre-pools jobs stay visible, read-only.** Every job submitted before
   2026-08-03 has `pool_id = null` and cannot be retrofitted into a
   workspace without writing to live production rows on a shipped tester
   release. They surface under `My account → Earlier jobs`, readable, never
   extended. The section empties itself over time and can then be deleted.
   Rejected: a migration into auto-created workspaces (prod writes for a
   naming win) and dropping them from the UI (a tester's history vanishes
   with no explanation).
4. **Five tabs; Submit is a button, not a tab.** Overview · Jobs · Machines
   · People · Settings, with a persistent "New job" button in the workspace
   header. Submitting is an action, not a place — and it is the one nav item
   you leave immediately after using.
5. **"Workspace" is a UI word only.** The API keeps `/v1alpha1/pools`, the
   tables keep `pool_id`, the TypeScript keeps `Pool`. "Pool" names a supply
   of compute; "workspace" names a place people work together, and this
   design exists to make the console read as the second. Rejected: renaming
   through the API (a breaking change to a shipped release, a table rename,
   and every design record's terminology stale at once).
6. **Three API additions, no more.** `pool_id` + submitter on job-list rows,
   `GET /pools/{id}/machines`, and `PATCH /pools/{id}` for rename. The third
   was added mid-design: a Settings tab was specified before checking, and
   the API turns out to have exactly six pool routes, none of which can
   rename, delete, or remove a member — so Settings as specified was an
   empty room. Rejected: dropping Settings to four tabs (a workspace you
   cannot rename is a week-one papercut) and building rename + delete +
   leave + remove-member together (four routes with destructive-action
   semantics — its own project, not a tab).

## 2. Routes

```
app/(console)/
  layout.tsx                    WorkspaceShell (replaces ConsoleShell)
  w/[poolId]/
    layout.tsx                  WorkspaceProvider — one fetch for all tabs
    page.tsx                    → redirect to ./overview
    overview/page.tsx           stats · active jobs w/ submitter · who's online
    jobs/page.tsx               this workspace's jobs
    machines/page.tsx           pool fleet · your opt-ins · connect a machine
    people/page.tsx             members
    settings/page.tsx           rename · invite link · details
    submit/page.tsx             target of the "New job" button
  account/
    page.tsx                    unchanged
    machines/page.tsx           personal fleet: enrol, name, revoke, pool chips
    earlier-jobs/page.tsx       read-only, pool_id IS NULL
  onboarding/page.tsx           no workspace yet → create or join
  jobs/[jobId]/page.tsx         UNCHANGED LOCATION — see below
  activate/page.tsx             unchanged (already accepts ?pool=)
  pools/join/page.tsx           UNCHANGED — live invite links point here
  docs/page.tsx                 unchanged
```

**Job detail does not move.** It stays at `/jobs/[jobId]`, and this is
deliberate. An orphan job (§1.3) has no workspace to nest under, so moving
the route would force a second copy of the page under `/account`. Instead
the record now carries its own `pool_id` (§4a), so one page renders the
right breadcrumb for both cases — `Vision Lab › resnet-sweep` for a
workspace job, `Earlier jobs › old-run` for an orphan. Existing deep links
and bookmarks keep working, which a move would have broken.

**Redirects.** `/pools/[poolId]` → `/w/[poolId]/overview` and `/machines` →
`/account/machines` as static `next.config` redirects. `/overview`, `/jobs`,
`/submit` and `/pools` become client resolvers that pick a workspace (§3)
and `router.replace`. `/pools/join?token=` is not touched at all — invite
links minted under v1.1 are already out in the wild.

## 3. Workspace resolution

The URL is the source of truth, which is the property that makes a link
pasted into Slack open *your* workspace for a teammate rather than theirs.
A `flashml_last_workspace` cookie (`SameSite=Lax`; a pool id, which already
appears in URLs, so nothing sensitive moves) covers the entry points that
carry no id.

Resolution order, as a pure function in `lib/workspace-scope.ts`:

1. `poolId` in the path, if the viewer is a member of it
2. the cookie, if it still names a workspace they are a member of
3. the first workspace by name
4. none → `/onboarding`

`w/[poolId]/layout.tsx` mounts a **WorkspaceProvider** that fetches
`getPool`, `getMe`, `listJobs` and `listPoolMachines` once and shares them
through context. This is a net reduction in traffic, not an addition:
today `overview/page.tsx` and `jobs/page.tsx` each run their own poll, and
`pools/[poolId]/page.tsx` fetches `getMe()` a second time on top of the
shell's. One provider, one poll, reusing Overview's existing rule — 5s while
anything is in flight, stopped once everything is terminal.

A 404 from `getPool` renders "this workspace doesn't exist, or you're not a
member" and nothing else. The API 404s identically for both cases by design
(`fetch_pool_for_member`), and this copy must not be reworded into an
access-denied message that would confirm the id is real to a stranger.

Rail counts (People 4, Machines 6, Jobs 2) come from the provider's state.
No badge gets its own request.

## 4. API additions

### a. `pool_id` and submitter on job rows

`list_jobs_route` (`app.py:1584`) currently calls
`list_job_ids_for_owner` and `list_pool_job_ids_for_member` and unions the
results into a `seen` set. One query replaces both, and the map it returns
is exactly what stamps each row — so this is a simplification as much as an
addition:

```sql
select j.id, j.pool_id, p.display_name as submitted_by
  from public.jobs j
  left join public.profiles p on p.id = j.owner_id
 where j.owner_id = %s
    or exists (select 1 from public.pool_members pm
                where pm.pool_id = j.pool_id and pm.user_id = %s)
```

Both row sources get stamped: the coordinator-sourced rows and the
`list_federated_jobs_for_viewer` rows. `GET /jobs/{id}` gains the same two
fields from `fetch_job_for_viewer`, which already selects `*`.

Client: `JobRecord` gains `pool_id?: string | null` and
`submitted_by?: string | null`. Both optional, both tolerant of absence —
the same api/web deploy-race insurance the machines page's `pools ?? []`
already documents.

### b. `GET /v1alpha1/pools/{pool_id}/machines`

Member-scoped, `current_user` + `fetch_pool_for_member`, 404 doctrine — the
same shape as every other pool read. Returns every machine bound to this
pool **across all members**, which is what the Machines tab exists to show
and what `listMachines()` (caller-scoped) structurally cannot.

`db.list_pool_machines(db, pool_id)` joins `machine_pools` → `machines` →
`profiles`, and — critically — joins against live `pool_members` the same
way `pool_ids_for_machine` does: a binding left behind by someone who has
since left the pool grants nothing and must not be listed. Row shape:
`id, node_id, name, owner_id, owner_display_name, status, last_seen_at`
plus the four v1.1 badge booleans.

### c. `PATCH /v1alpha1/pools/{pool_id}`

Owner-only rename. Copies `revoke_pool_invites_route`'s ownership check
exactly — `fetch_pool_for_member`, then `str(pool["owner_id"]) != user_id`,
404 for all three of "doesn't exist" / "not a member" / "member but not
owner". `InvalidTextRepresentation` caught and treated as 404, like every
sibling route.

Body `{name}`, validated identically to `create_pool_route`: string,
non-empty after strip, ≤ 200 characters, 400 otherwise. Returns the updated
`Pool`. Not gated by `admitted_user`, for the reason
`create_pool_invite_route` states in its own docstring — owning a pool
already required admission at create time, and `admitted_user` names the
four routes that need the gate directly.

## 5. What each tab holds

**Overview** — three stats, active jobs **with who submitted them**, members
with online counts. The submitter attribution is new and is the single
change that makes the page read as shared rather than personal.

**Jobs** — the workspace's jobs, submitter column, same `StateBadge` and
polling behaviour as today.

**Machines** — three answers to three questions, where today's pool page
stacks them as three sections of one scroll: *what compute does this
workspace have* (the pool fleet, all members, from §4b), *what am I
contributing* (your machines with the v1.1 opt-in checkboxes), and *how do I
add more* (the existing `ConnectPanel`, unchanged, still auto-attaching to
this workspace per v1.1 §6).

**People** — the member table. For the owner, an "Invite a teammate →" link
pointing at Settings. The invite manager itself lives in Settings and is not
duplicated here.

**Settings** — rename (§4c), the invite link state + Regenerate + Revoke
moved wholesale from today's pool page, and read-only details: workspace id,
created, owner. Remove-member, leave and delete are absent, not stubbed —
they have no routes, and v1.1 §8 defers them deliberately.

**Submit** — reached from the "New job" button. The pool selector is gone;
the workspace comes from the route and is shown as a static line ("Runs in
Vision Lab"). `submitFromRepo` already accepts `poolId`, so no call changes.

`lib/pool-selection.ts` loses `NO_POOL` and `isPoolSelected` — both exist
solely to model the public-queue default that §1.2 removes.
`hasNoWorkersOnline` stays and keeps its amber banner.

## 6. Component work

`ConsoleShell` (299 lines) becomes `WorkspaceShell`: the same rail, plus a
`WorkspaceSwitcher` above the search box and a personal section below the
scoped nav. `InviteGate`, `CommandPalette`, `Shortcuts` and the `GET /me`
admission logic move across untouched — including the mount-only effect and
its comment explaining why it is deliberately not re-run per navigation.

The 668-line pool page dissolves into five pages of roughly 100–180 lines,
each fetching nothing (the provider owns the data). Its four existing
sections are extracted rather than rewritten:

| Today, in `pools/[poolId]/page.tsx` | Becomes |
|---|---|
| member table | `components/workspace/MemberTable.tsx` → People |
| `YourMachinesSection` + `MachineToggleRow` | `components/workspace/YourMachines.tsx` → Machines |
| `ConnectPanel` (already its own file) | unchanged → Machines |
| `InviteSection` | `components/workspace/InviteManager.tsx` → Settings |

The optimistic-toggle-with-revert logic, the revoked-machine filter and
their comments move verbatim. This is a re-parenting, not a rewrite.

## 7. First run

A user with no workspace cannot submit a job, which makes `/onboarding` a
real gate rather than an empty state: create a workspace, or paste an invite
link. Reached whenever resolution (§3) finds nothing, and it is the one
console route besides `/pools/join` that an admitted user with zero
workspaces must be able to sit on without redirect-looping.

## 8. Testing

House style — pure predicates with unit tests, as `pool-selection.ts` and
`machine-badge.ts` already establish. No component tests.

- **`lib/workspace-scope.ts`** — the four-step resolution order (§3),
  including "cookie names a workspace I was removed from" and "path id I am
  not a member of", plus which nav items render for a given workspace.
- **`lib/job-scope.ts`** — partition a job list into this-workspace / other
  workspace / earlier (`pool_id === null`), with rows missing `pool_id`
  entirely treated as earlier, never as belonging to the current workspace.
- **`lib/cloud-api.test.ts`** — shapes for `listPoolMachines` and
  `renamePool`.
- **`lib/pool-selection.test.ts`** — updated for the `NO_POOL` removal.
- **`lib/route-exports.test.ts`** — needs no change; it walks `app/`
  recursively and covers every new route automatically.
- **API (pytest)** — the merged job-scope query returns `pool_id` for owned
  and pool-visible rows and `null` for pre-pools ones; both row sources
  carry it; `GET /pools/{id}/machines` 404s for non-member and for a
  malformed id, and omits a machine whose owner has left the pool;
  `PATCH /pools/{id}` accepts an owner rename, 404s for a member who is not
  the owner, and 400s on empty and >200-character names.
- **e2e** — none. The coordinator sees only the stamped pool list, which the
  existing pool suite already exercises end to end; v1.1 §7 makes the same
  argument for the same reason.

## 9. Not in this design

Activity feed (needs a `pool_events` table and write sites across four
routes — deferred as its own slice); remove-member, leave, and delete-
workspace routes; roles beyond owner/member; cross-workspace search; a
migration of pre-pools jobs; workspace avatars or colours; any change to
`flashruntime`, `flashnode`, or the protocol — this design touches
`apps/web` and `apps/api` only.

## 10. Vocabulary note for `AGENTS.md`

To be added when this ships, so the split does not read as accidental drift:

> The console UI says **workspace**; the API, the database and the
> TypeScript types say **pool**. They are the same thing. The rename was
> deliberate and UI-only — see
> `docs/superpowers/specs/2026-08-03-workspace-console-design.md` §1.5.
> Do not "fix" one side to match the other.
