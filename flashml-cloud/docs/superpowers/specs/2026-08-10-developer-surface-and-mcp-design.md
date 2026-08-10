# The developer surface — a client, a CLI, and an MCP server

**Date:** 2026-08-10
**Status:** approved design (brainstormed with the owner).
**Repos touched:** `flashml` (new `flashml` package), `flashml-cloud` (API auth,
one new endpoint, one new console page, one migration).
**Supersedes:** nothing. Additive throughout.

**Origin.** The owner's ask, 2026-08-10: *"I want to support users in developing
with FlashML also — like having an MCP server for their agents."*

The premise behind it is that the people this product is for do not write
training code alone. They write it with a coding agent, in an editor, against a
repo. Today FlashML has nothing to say to that agent. Everything a job author
can do lives behind a browser session in the console, and the two CLIs that
exist — `flashruntime` and `flashnode` — are the coordinator and the host agent.
Neither is for the person submitting work.

So this is not really "add an MCP server". It is: **FlashML has no developer
client at all, and an MCP server is the shape the owner wants the first one to
take.** The MCP server is the last of four things, and the thinnest.

---

## 1. Decisions

1. **One new public package, `flashml`,** in `Zolli-Labs/flashml`, released
   under the package-scoped tag `flashml-v0.1.0` alongside `flashruntime-v*`
   and `flashnode-v*`. It contains a typed client, a human CLI, and the MCP
   server.
2. **The MCP server is a subcommand of the CLI (`flashml mcp`), not a separate
   distribution.** One version, one credential store, one client core. Tool
   surface and CLI surface stay in step by construction rather than by
   discipline. The `mcp` dependency is an optional extra.
3. **`flashml` depends on neither `flashruntime` nor `flashml-cloud`.** It
   speaks JSON over HTTP. This adds **no new version-pin sites** — the
   four-site rule in `CLAUDE.md` is untouched — and keeps `uvx flashml mcp`
   fast enough to sit in a coding agent's config.
4. **A new credential class, `fmu_`,** minted by a developer variant of the
   existing device-code flow. `flashml login` prints a short code, the console
   approves it, the raw token is returned exactly once and only its hash is
   stored — the discipline `enrolment.py` already runs on. No secret is ever
   pasted by a human or shown to an agent.
5. **`current_user` learns to accept `fmu_`.** This is the load-bearing move:
   every route already tagged `browser` becomes CLI-reachable in one change,
   with no per-route edit and no second authorization model to keep aligned.
6. **Validation moves off the push.** A new `POST /v1alpha1/preflight` takes
   local bytes, runs the existing `parse_flashml_yaml` + `preflight` and
   creates nothing. One authority for the rules; the CLI never carries a copy.
7. **`POST /v1alpha1/jobs/from-upload`** accepts a tarball of the working tree
   so a developer can submit without a public GitHub repo. ~~Deferrable~~ —
   **NO LONGER DEFERRABLE, amended 2026-08-10.** The owner decided that day
   (`ROADMAP.md` §6.2) to hold the GitHub App until a team asks for it, which
   makes `from-upload` **the** private-code path for the entire product rather
   than a convenience. §7's "if this is cut" analysis still describes the cost
   accurately; it is now simply not an option. See §7.
8. **The MCP surface is author / validate / submit / observe / cancel-own.**
   Pool administration, invites, machine revocation and artifact deletion are
   reachable from the CLI and the console and are **absent from the MCP tool
   list entirely.** Cancel is present because it is the brake.
9. **Every MCP tool prints its CLI equivalent in its result.** An agent that
   loses the server can fall back to bash, and a human reading the transcript
   can reproduce what was done.

---

## 2. What exists today, and the four gaps

Verified against `flashml-cloud/apps/api/flashml_cloud_api/` at 2026-08-10.

The control plane is complete on the job-author side. `app.py` exposes, all
tagged `browser`: submit (`POST /v1alpha1/jobs`, `POST /v1alpha1/jobs/from-repo`),
list and read (`GET /v1alpha1/jobs`, `/jobs/{id}`), the event ledger
(`/jobs/{id}/events?since=N`), task state (`/jobs/{id}/tasks`), federated rounds
(`/jobs/{id}/rounds`), results (`/jobs/{id}/result`), artifacts
(`/jobs/{id}/artifacts/{key}`), cancel, pools, machines, storage and metrics.

Four things are missing, and they are what this design supplies.

**Gap 1 — there is no developer credential.** `auth.py` knows exactly two kinds
of caller: a Supabase JWT (browsers) and `fmk_` machine tokens (enrolled
workers). `fmi_` is a pool invite, not a caller identity. `current_user`
explicitly rejects anything that looks like a machine token and hands the rest
to the JWT decoder. A CLI has nothing to present.

**Gap 2 — you must push before you can be told your config is wrong.**
`preflight.py` is good at its job: a static `ast` walk that never imports or
executes user code, returning *every* finding in one answer so a user fixing
four problems needs one more submit rather than four. But it only runs inside
`POST /v1alpha1/jobs/from-repo`, over a tarball fetched from GitHub. The
feedback loop is therefore: edit → commit → push → submit → read findings. For
an agent iterating on a `flashml.yaml`, that is four irreversible steps per
guess.

**Gap 3 — the repo must be public.** `repo.py` accepts a `token` parameter and
its docstring records that M1 ships public-repo-only. `fetch_repo_tarball` is
called without one. A developer working on anything private cannot submit at
all.

**Gap 4 — job stdout/stderr is not exposed to job authors.** The coordinator
app in the same file serves `GET /v1alpha1/jobs/{job_id}/logs`; the cloud API
does not proxy it. Authors get the event ledger instead, which carries
`TASK_ATTEMPT_FAILED`, `TASK_EXHAUSTED`, `FAILURE_CLASSIFIED` and
`RECOVERY_ACTION_SELECTED`, each with a `message` and a `data` dict
(`flashruntime.protocol.v1alpha1.Event`). Whether that is enough to diagnose a
user's own bug is an open question — see §10.

---

## 3. Identity: the `fmu_` token class

### 3.1 Why a new class rather than a Supabase JWT

A CLI could sign in to Supabase directly with email and password and cache the
access token. Rejected: those tokens are short-lived, refresh becomes our
problem, and there is nothing independently revocable — killing a leaked CLI
credential would mean invalidating the user's browser session too.

A console-minted PAT pasted into `FLASHML_TOKEN` was also rejected. It puts the
raw secret through a clipboard, a shell history, a `.env`, and — the reason
that matters here — an agent's context window.

The device-code flow avoids all of that and already exists.

### 3.2 The flow

```
$ flashml login
  Open https://console.../activate and enter:  QK7M-2XPD
  Waiting…                                     (polls every 5s)
  Signed in as phong@… — token stored in ~/.flashml/credentials.json
```

Three steps, mirroring `enrolment.py`'s documented order of operations:

1. The CLI calls `POST /v1alpha1/device/code` with `kind: "cli"` and gets a long
   `device_code` for itself and a short `user_code` for a human. Neither
   identifies anyone yet.
2. A signed-in person enters the `user_code` at `/activate`. This is the only
   place `owner_id` enters the flow, and it comes from the verified JWT `sub`,
   never from a request body.
3. The CLI polls `POST /v1alpha1/device/token`. Only after approval does this
   return a token, and it returns the raw value **exactly once** — thereafter
   only the sha256 hash exists, in the database.

`redeem_device_code`'s existing RFC 8628 shape is kept: unknown, unapproved,
expired and already-redeemed are one indistinguishable `authorization_pending`,
so polling cannot be used to learn which codes are real.

### 3.3 Schema — migration `0012_cli_credentials.sql`

A new table, mirroring the token columns of `public.machines`:

```sql
create table if not exists public.cli_credentials (
    id            uuid primary key default gen_random_uuid(),
    owner_id      uuid not null references public.profiles(id) on delete cascade,
    label         text,                    -- e.g. "phong's laptop"
    token_hash    text not null unique,
    token_prefix  text not null,
    status        text not null default 'active'
                  check (status in ('active', 'revoked')),
    last_used_at  timestamptz,
    created_at    timestamptz not null default now(),
    revoked_at    timestamptz
);
```

`device_codes` gains `kind text not null default 'machine' check (kind in
('machine','cli'))`, and `node_id` is relaxed to nullable guarded by
`check (kind <> 'machine' or node_id is not null)`.

**One table for both flows, not two.** A CLI code has no `node_id`, so a
separate `cli_auth_codes` table is tempting and would leave the volunteer
enrolment path untouched. Rejected: `user_code` must be unique across *both*
kinds or `/activate` cannot tell which flow an entered code belongs to, and
enforcing uniqueness across two tables is a constraint Postgres will not write
for you. The default on `kind` means every existing row and every existing
insert keeps its current meaning.

`last_used_at` is written at most once per minute per credential — a timestamp
update on every request would put a write in front of every read.

### 3.4 The verification path

`auth.py` gains `USER_TOKEN_PREFIX = "fmu_"`, `new_user_token`,
`hash_user_token` and `looks_like_user_token`, each mirroring the machine and
invite equivalents exactly. Same entropy, same "prefix on the outside, nothing
recoverable from it" shape.

`current_user` becomes:

```
token = _bearer(request)
if looks_like_machine_token(token):   -> 401   (unchanged; kinds never share a path)
if looks_like_user_token(token):      -> resolve via cli_credentials
otherwise                             -> verify_supabase_jwt   (unchanged)
```

The `fmu_` branch opens a database connection only after the prefix matches, for
the reason `machine_caller` already documents: resolving a connection before
checking the credential's shape makes every anonymous request cost a Postgres
connection.

An unknown token and a revoked credential return the same 401, as with machines.
Revocation flips `status` in the row this reads, so it takes effect on the next
request — no cache, no refresh.

`admitted_user` and `admin_user` both `Depends(current_user)` and therefore need
no change at all. **An `fmu_` token confers exactly its owner's access, no more:
an un-admitted account gets the same 403 through the CLI that it gets through
the console.**

### 3.5 The console page

`app/(console)/account/cli/page.tsx`, beside the existing
`account/machines/page.tsx` and modelled on it: label, prefix, created, last
used, revoke. New routes `GET /v1alpha1/cli-credentials` and
`POST /v1alpha1/cli-credentials/{id}/revoke`, tagged `browser`.

`/activate` gains a branch on the code's `kind` so one page serves both. What
the human is approving must be stated plainly and differ between them — "this
will let a machine run jobs for you" and "this will let a program submit jobs
as you" are different consents.

---

## 4. The client core, the CLI, and the MCP server

### 4.1 Layout

```
flashml/                       # new top-level package in Zolli-Labs/flashml
├── pyproject.toml             # name = "flashml"; [project.scripts] flashml = ...
│                              # [project.optional-dependencies] mcp = ["mcp>=1.0"]
├── flashml/
│   ├── client.py              # typed HTTP client. no CLI, no MCP.
│   ├── credentials.py         # ~/.flashml/credentials.json, 0600
│   ├── config.py              # profile / base URL resolution
│   ├── errors.py              # one exception per API failure shape
│   ├── cli.py                 # click entry point
│   ├── commands/              # one module per verb
│   └── mcp/
│       ├── server.py          # tool registration + dispatch
│       ├── tools.py           # thin adapters over client.py
│       └── resources.py       # schema, constraints, worked examples
└── tests/
```

`client.py` is the only module that knows the wire format. `cli.py` and `mcp/`
are both consumers of it and neither calls `httpx` directly. That is what makes
"the MCP tool and the CLI command do the same thing" a property of the code
rather than a promise in a document.

### 4.2 The CLI

```
flashml login / logout / whoami
flashml check [PATH]            # local validate + preflight, no push, no job
flashml submit [PATH|REPO]      # --pool, --ref, --watch
flashml jobs [--pool] [--state]
flashml job <id>                # status, tasks, findings
flashml watch <id>              # poll the event ledger, stream to stdout
flashml results <id> [--out DIR]
flashml cancel <id>
flashml pools                   # read-only listing; admin stays in the console
flashml mcp                     # run the MCP server on stdio
```

Every command is `--json`-capable. The MCP adapters call the same functions the
human commands do, so `--json` output and tool output are the same objects.

### 4.3 Credential storage

`~/.flashml/credentials.json`, mode 0600, one entry per profile:

```json
{ "default": { "base_url": "https://api...", "token": "fmu_…", "user_id": "…" } }
```

`FLASHML_TOKEN` and `FLASHML_API_URL` override the file when set, for CI. The
MCP server reads the same file: **a coding agent never receives, stores, or
sees the token.** It calls a tool; the process it spawned holds the credential.
This is the concrete payoff of decision 4 over a pasted PAT.

---

## 5. `POST /v1alpha1/preflight` — validation without a push

Request (`browser`-tagged, `admitted_user`, subject to `MAX_JSON_BODY_BYTES`):

```json
{ "config": "<flashml.yaml text>",
  "entrypoint": "<entrypoint file text>",
  "entrypoint_path": "train.py",
  "pool": "<uuid|null>" }
```

Response: the same findings array `from-repo` produces, plus the parsed and
normalized config (so the caller can show the derived round count, the sweep
combination count, the resolved image) and the pool's placement feasibility if
one was named.

**It creates nothing.** No job row, no artifact, no coordinator call, no storage
charge. It is a pure function of the bytes supplied, which is exactly what
`preflight.py`'s own docstring establishes it already is — the module never
imports, never `exec`s, never resolves a path, and never opens a file it was not
handed. Exposing it standalone adds no attack surface that `from-repo` did not
already carry, and it removes the sharpest edge in the current workflow.

The entrypoint text is sent because preflight's scope is the entrypoint file
only — deliberately, since a repo-wide scan is a false-positive machine. The CLI
resolves `entrypoint:` from the parsed config and reads that one file. If the
config does not parse, the parse error is the whole answer and no file is read.

**Why not validate locally.** The parser lives in the private API and the rules
are large — `flashml_yaml.py` alone encodes two schema versions, the removed
federated keys with their migration prose, a sweep-combination cap and a timeout
cap. Copying it into a public CLI creates a third copy of a thing this workspace
has already been burned by duplicating, and drift here means the CLI blesses a
config the API refuses. Moving it down into `flashruntime` would make it shared
and offline-capable, but then every message tweak needs a PyPI release plus the
four-site version bump before the API can use it. A round trip is the cheaper
price.

---

## 6. The MCP surface

### 6.1 Tools

| Tool | Maps to | Notes |
|---|---|---|
| `flashml_whoami` | `GET /v1alpha1/me` | also reports admission state and pools |
| `flashml_check` | `POST /v1alpha1/preflight` | the agent's inner loop |
| `flashml_submit` | `from-upload` or `from-repo` | requires `check` to have passed |
| `flashml_list_jobs` | `GET /v1alpha1/jobs` | |
| `flashml_job_status` | `GET /v1alpha1/jobs/{id}` + `/tasks` | one call, merged |
| `flashml_job_events` | `GET /jobs/{id}/events?since=N` | returns the next cursor |
| `flashml_job_result` | `GET /jobs/{id}/result` | |
| `flashml_fetch_artifact` | `GET /jobs/{id}/artifacts/{key}` | writes to a local path |
| `flashml_cancel_job` | `POST /jobs/{id}/cancel` | owner-only, enforced server-side |
| `flashml_list_pools` | `GET /v1alpha1/pools` | read-only |

Not present, on purpose: pool create/patch/delete, invite mint and revoke,
machine revoke, artifact delete, admin access-request decisions. They exist in
the CLI. An agent that needs one asks its human to run it.

`flashml_submit` refusing to run until `flashml_check` has passed on the same
bytes is a guard against the specific failure this whole design is about: an
agent that submits a guess, waits eight minutes, and learns line 3 was wrong.

### 6.2 Resources and prompts

The tools are the smaller half. The reason agent-authored FlashML workloads fail
is not that the agent cannot call an API — it is that the execution contract is
invisible from inside a training script. A job runs `--network none`, from a
pinned image nobody can add to, and commits only what lands in `/work/out`. An
agent that has never been told this writes `wget`, writes to `./output/`, and
fails on a stranger's laptop forty minutes in.

So the server publishes, as MCP resources:

- `flashml://schema/flashml.yaml` — every key, both schema versions, the removed
  federated keys with their migration text, the caps.
- `flashml://contract/execution` — no network, read-only rootfs, `/work/out` is
  the only durable path, uid mapping, cpu/mem limits, the timeout ceiling.
- `flashml://images` — the curated images and their dependency manifests.
- `flashml://examples/federated` — the worked example from `examples/federated/`.

And one prompt, `port_training_script`, that walks the agent through turning an
existing script into a workload: read the script, name the entrypoint, choose an
image, redirect outputs to `/work/out`, remove network calls, write the yaml,
run `flashml_check`.

These are served **from the package**, not fetched, so they work before login
and cannot fail because the API is down. Their content is generated from the
same sources the API uses, checked in, and pinned by a test that fails when the
schema and the resource disagree.

### 6.3 Long-running jobs

A FlashML job takes minutes to hours. No tool blocks. `flashml_job_events`
returns `{events, next_cursor}` and the agent polls; the `since` parameter is
already an integer offset into an append-only list rather than a timestamp,
which the API's own docstring explains is the right cursor here because several
events share a millisecond. Tool descriptions state the expected duration so an
agent schedules a poll instead of spinning.

---

## 7. `from-upload`, and what is lost without it

`POST /v1alpha1/jobs/from-upload` takes a gzipped tarball of the working tree
and is otherwise identical to `from-repo` from the extraction step onward: the
same `extract_safely`, the same parse, the same preflight-before-anything
ordering, the same pool membership check before any expensive work, the same
storage gate. It differs only in where the bytes came from — and `from-repo`
already treats those bytes as fully untrusted, so nothing downstream weakens.

The CLI tars the working tree honouring `.gitignore`, refuses above a size cap,
and records the source as `upload:<sha256>` where `from-repo` records the repo
and ref.

**If this is cut,** the loop still closes but only for work in a **public**
GitHub repo, and every iteration costs a commit and a push. `repo.py` is
public-repo-only today (§2, gap 3), so a developer on anything private has no
path at all and the MCP server has nothing to offer them. Provenance is the
argument for cutting it — a job that names a repo and a ref is reproducible, and
one that names a tarball hash is not — which is why the source is recorded
distinguishably rather than uploads being made to look like repos.

Recommendation: keep it. It is the difference between a tool a developer uses
and a demo.

---

## 8. Error handling

Three failure classes, three distinct shapes, because an agent that cannot tell
them apart retries the wrong one:

1. **Your config is wrong** — preflight findings. Actionable, complete in one
   answer, never truncated to the first. The tool returns them as structured
   items with file, line and message, not as prose.
2. **Your credential is wrong** — 401 or 403. The CLI says `run flashml login`
   for the former and `your account is awaiting approval` for the latter, which
   is the distinction `admitted_user` already draws with 403-not-404.
3. **We are broken** — 5xx, coordinator unavailable, timeout. Retriable, and
   labelled retriable, with the CLI equivalent to re-run.

Never leak an oracle: the API's existing discipline of answering 404 for a job
that exists but the caller cannot see is preserved verbatim by the client, which
does not attempt to distinguish "no such job" from "not yours".

---

## 9. Testing

- **`flashml` package** — unit tests against a stub transport for every client
  method; CLI tests via click's runner. No live API.
- **API** — `tests/test_cli_auth.py`: an `fmu_` token reaches a `browser` route;
  a revoked one gets 401; an un-admitted owner's token gets 403 from
  `admitted_user`; a machine token still gets 401 from `current_user`; a
  Supabase JWT still works unchanged. `tests/test_preflight_endpoint.py`: the
  dry-run creates no job row, no artifact and makes no coordinator call, and
  returns byte-identical findings to what `from-repo` produces for the same
  bytes.
- **Parity test** — every MCP tool resolves to a CLI command, asserted by
  enumerating both registries. This is what keeps decision 2 true over time.
- **Resource freshness** — the checked-in schema resource is compared against
  the API's parser; the test fails when a key is added on one side only.
- **e2e** — one new case in `e2e/`: `flashml login` against a stub, `check`,
  `submit`, `watch` to completion, `results`. Runs against the pin.

---

## 10. Open questions

1. **Is the event ledger enough to debug your own bug?** Gap 4. The coordinator
   has `/jobs/{id}/logs`; the cloud API does not proxy it. If `TASK_ATTEMPT_FAILED`
   does not carry a stderr tail in its `data`, then an agent watching its job
   fail learns *that* it failed and not *why*, and the whole loop stops one step
   short. **Resolve this before Plan 3 is written** — it may add a fifth gap and
   a proxied logs route.
2. **Private repos.** `repo.py` accepts a `token` it is never given. If
   `from-upload` ships, this stays closed and that is fine. If it is cut, this
   becomes urgent.
3. **Rate limiting.** An agent in a loop is a different traffic shape from a
   human clicking. `POST /v1alpha1/preflight` is the cheap-to-call endpoint and
   the one most likely to be hammered. Not designed here; flagged.
4. **Naming.** `flashml` as a PyPI package name may be taken. Fallback:
   `flashml-cli`, keeping the `flashml` command name.

---

## 11. Implementation plans

Three, in dependency order. Each ends with something runnable.

**Plan 1 — developer identity.** `fmu_` token class in `auth.py`; migration
0012; `kind` on the device-code flow; `current_user` extended; CLI-credentials
routes; the `account/cli` console page; `/activate` branching. Demo: approve a
code in the console and `curl` a `browser` route with the resulting token.

**Plan 2 — client core and CLI.** The `flashml` package; `POST /v1alpha1/preflight`;
`POST /v1alpha1/jobs/from-upload`; every command in §4.2 except `mcp`. Demo:
`flashml login && flashml check && flashml submit --watch` against a real pool,
from a directory that was never pushed anywhere.

**Plan 3 — the MCP server.** `flashml mcp`; the tools in §6.1; the resources and
prompt in §6.2; the parity and freshness tests. Demo: a coding agent, given only
the MCP server and a bare training script, produces a workload that passes
preflight and completes on a pool.
