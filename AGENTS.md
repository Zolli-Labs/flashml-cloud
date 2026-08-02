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
it.** Both `flashml-cloud/apps/api/pyproject.toml` and `render.yaml` pin the
same commit of `Zolli-Labs/flashml`. When you change the pin, change it in
**both**, plus `FLASHML_PIN` in the `Makefile` — the API and the coordinator
running different protocol versions is precisely the failure this removed.

Commit pins are acceptable while `flashruntime` is unpublished. Once it is on
PyPI, move to `flashruntime==0.3.0` and treat a surviving commit pin as a
release blocker. See
`flashml-cloud/docs/superpowers/specs/2026-08-01-foundation-design.md` §3.3.

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
