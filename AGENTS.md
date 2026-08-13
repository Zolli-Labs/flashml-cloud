# Zolli Labs — `flashml-cloud`

**This repository is `Zolli-Labs/flashml-cloud` (PRIVATE).** It holds the
managed application: the control-plane API, the web console, and the
cross-component end-to-end suite.

```
flashml-cloud/            THIS REPO — private
├── flashml-cloud/apps/api/   FastAPI control plane
├── flashml-cloud/apps/web/   Next.js console
├── e2e/                      the whole loop, against PINNED public artifacts
└── render.yaml               three services: web, api, coordinator
```

The runtime and the host agent are **not here**. They live in the public
monorepo `github.com/Zolli-Labs/flashml` and are consumed as ordinary pinned
dependencies.

## The rule that replaced the old rule

Until 2026-08-01 this repo contained `flashruntime/` and `flashnode/` as git
subtrees, mirrored by hand to two public repos with `git subtree split` +
push. **That is gone. Do not reintroduce it.**

It shipped a real bug: `flashnode/executor/images.py` existed here and not in
the public mirror, so a volunteer's install demanded an image allowlist this
repo had already removed. The deployed coordinator and the agents talking to
it were running different code, and nothing detected it.

**There is now exactly one copy of the runtime, and this repo does not hold
it.** It is consumed as an **exact PyPI version** — not a git ref — and
**four** sites carry that version. All four move together:

| Site | Line as of **0.6.0** (verified 2026-08-11) |
|---|---|
| `Makefile` | `RUNTIME_VERSION := 0.6.0` |
| `render.yaml` | **prod** coordinator `buildCommand` — `flashruntime[service]==0.6.0` |
| `render.yaml` | **dev** coordinator `buildCommand` — `flashruntime[service]==0.6.0` |
| `flashml-cloud/apps/api/pyproject.toml` | `"flashruntime==0.6.0"` |

Bumping three of the four ships an API and a coordinator speaking different
protocol versions, which is precisely the failure this consolidation removed.

**`FLASHML_PIN` no longer exists.** Anything still naming it — or saying the
pin lives in "three places", or in "both" of two files — predates the dev
coordinator joining on 2026-08-02 and is wrong. `render.yaml` is the
authority. Stale mentions survive in `docs/superpowers/plans/`; those are
history, not instructions.

When you bump the pin, **bump this table and the identical one in the
workspace `CLAUDE.md` in the same commit.** A stale version on the page an
agent reads before touching a pin is how a correct set of four gets
"corrected" back to a version that no longer exists on PyPI.

Because the pin is a *published* version, this repo cannot consume unreleased
runtime work at all: merge → release to PyPI → bump all four.
`make e2e-setup LOCAL=1` installs from `../flashml` for local rehearsal only
and is not release evidence. See
`flashml-cloud/docs/superpowers/specs/2026-08-01-foundation-design.md` §3.3.

## Git in a shared checkout — repo-scoped commands are the hazard

Several agents and sessions work in this one checkout at the same time.

**Commit with explicit paths. Never `git add -A`, never `git commit -a`.** A
sweeping commit on 2026-08-12 pulled in half of another session's slice and
left HEAD internally broken.

**Explicit-path `git add` is not enough — the staged index is also
repo-wide.** On 2026-08-13 two sessions each ran a correct explicit-path
`git add`; the adds interleaved, and whichever `git commit` ran first swept
BOTH sessions' staged files into one commit (`2b9c02d` — content intact,
message and attribution wrong; the second session's commit then failed with
"no changes added"). Pass the pathspec to the commit itself —
`git commit -m "..." -- <path> <path>` — which commits exactly those paths
regardless of what else is sitting in the shared index.

**And never run a REPOSITORY-SCOPED command here:** `git stash`,
`git checkout <ref>`, `git reset`, `git clean`, `git restore`, `git rebase`.
On 2026-08-12 a subagent ran `git stash push` in this checkout while trying
to establish whether a `tsc` error was pre-existing. For a few seconds it
reverted three sessions' uncommitted work. Recovered with nothing lost — by
luck, not by design.

That agent had a file allowlist and obeyed it. **A file allowlist cannot
constrain `git stash`, because `git stash` does not take files.** Isolation
at the file level is not isolation at the repository level, and only the
second survives an agent reaching for a repo-wide command. State both when
you dispatch one.

**Name the read-only tool when you brief an agent.** A subagent reaches for
`stash` while trying to answer a legitimate question; give it the safe answer
before it improvises the unsafe one:

| Question | Safe answer |
|---|---|
| "Was this error pre-existing?" | `git show <ref>:<path>` — never stash to find out |
| "What did this look like before my change?" | `git show HEAD:<path>` |
| "Is this diff mine?" | `git log -1 --format=%s -- <path>`, or ask the other session. **Never infer ownership from `git status`** — a file being modified in a shared checkout says nothing about who modified it |
| "What did MY branch change?" | `git diff develop...HEAD` — **three dots.** See below |
| "Is my work intact after an incident?" | **Run the suite.** Presence is not integrity — a green typecheck and a passing suite over the exact files is evidence a file listing cannot give |

**Two dots versus three is the nastiest of these, because the wrong answer is
specific, plausible, and accuses someone.** On 2026-08-12 a session checked its
own branch with `git diff develop..HEAD` and was told it had **838 deletions of
`test_public_job_share.py`** — the test guarding the G-1 no-login route. It had
deleted nothing, and had never touched `apps/api` at all. `develop` had simply
moved 15 commits past its merge-base, and **two-dot renders "develop has
something I don't" identically to "I deleted it."**

Three-dot diffs against the merge-base and answers the question you meant —
*what did my commits change?* Two-dot answers *how do these two tips differ?*,
which on a branch cut hours ago from a repo three sessions are committing to is
mostly other people's work, reported as your deletions.


**Work in a worktree when your task is broad** (`.worktrees/` is gitignored).
That removes the whole class — but only if your agents are also told which
checkout they may run git in.

**A worktree also means your work has never met the other suite.** A branch
built in `apps/web` runs the web tests and nothing else; the API suite has
never seen it. Before landing, merge the base branch **in** and run **both** —
a green measured before the merge is a green about a tree that no longer
exists.

## Dependency direction

`flashml-cloud` imports `flashruntime`'s versioned protocol package. Nothing
else crosses a component boundary, and nothing imports this repo.

## Working on the runtime at the same time

Clone the public repo as a sibling and point the local tooling at it:

```bash
git clone https://github.com/Zolli-Labs/flashml.git ../flashml
make e2e-setup LOCAL=1     # e2e against your checkout instead of the pin
```

`LOCAL=1` prints a warning, deliberately: a green run against a working tree
is not release evidence. The default — and everything deployed — uses the pin.

## Running it

```bash
make setup                 # api venv (from the pin) + web deps
./scripts/dev.sh --all     # coordinator :8100 + API :8000 + console :3000
```

`npm run dev` alone is not enough — every page after sign-in calls the API,
and the API will not start without a coordinator behind it. A lone console
shows "Failed to fetch" on every screen, which looks like a bug and is not.

Needs a `.env` at the repo root (gitignored; copy `.env.example`).

## Naming history

Until 2026-08-01 this repo was `Zolli-Labs/flashml-poc`, and a separate,
now-superseded `Zolli-Labs/flashml-cloud` held only the cloud component. Its
final commit `159ff30` is an ancestor of this history, so nothing was lost; it
was renamed `flashml-cloud-legacy`.

The Supabase project is still *named* `flashml-poc`. That is a different thing
from the repo, its ref `yualksqjjvlfscbbsygq` is what anything actually
resolves, and renaming it would only invalidate the docs citing it.

## Document map (read in this order)

- `HANDBOOK.md` — READ FIRST, once: product + per-component breakdown,
  as-built architecture, cloud target, edge-case and research registers.
- `PROGRESS.md` — AUTHORITATIVE status: stage checklist, dated work log, and
  the LOGGING PROTOCOL every agent must follow.
- `M1_DECISIONS.md` — the M1 decision record (D1–D15). Read before re-opening
  any M1 design choice.
- `flashml-cloud/docs/superpowers/specs/2026-08-01-foundation-design.md` —
  the current architecture work: repo topology, releases, and the diskless
  control plane. Plans A/B1/B2 sit beside it in `plans/`.
- `flashml-cloud/docs/superpowers/specs/POSITIONING_LOG.md` — **READ THIS
  BEFORE re-opening any product-direction question.** One dated trail through
  what FlashML is for and whose machines supply it, newest first, including
  the turns that were later corrected. Direction moved three times on
  2026-08-02 alone; the log records what triggered each move so nobody
  re-argues a settled point or re-adopts a discarded one. Append, never
  rewrite.
- Two strategy notes from 2026-08-02, both of which **disagree with that
  spec's S4-first ordering**. Read them before scheduling the desktop app:
  - `2026-08-02-supply-side-positioning-note.md` — rented providers and home
    GPU rigs are where the compute is; volunteer laptops are the tier we
    optimised for and the one worth least. Makes GPU support the gate and the
    desktop app the least urgent item.
  - `2026-08-02-colab-gpu-pooling-strategy-note.md` — pooling Colab GPUs
    across a research group. Google's FAQ names "running distributed
    computing workers" as prohibited on the FREE tier; paid plans lift it.
    Reaches the same roadmap conclusion from a different direction.
- `e2e/` — the whole loop against pinned artifacts (`make e2e`, `make
  e2e-demo`) + the real-second-machine runbook in `e2e/README.md`.
- `archive/` — historical: HANDOFF, PLAN_2WEEKS, SPRINT_PLAN, POC records.

Product context (`SYSTEM_OVERVIEW.md`) lives in the public repo and is linked
from `flashml-cloud/docs/SYSTEM_OVERVIEW.md`. It is no longer copied here —
`make sync-docs` and `make check-docs` are gone with it.
