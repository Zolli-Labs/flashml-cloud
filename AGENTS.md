# Zolli Labs — `flashml-cloud`

**This repository is `Zolli-Labs/flashml-cloud` (PRIVATE).** It holds all
three components of the FlashML system as git subtrees with their history
intact (see any component's docs/SYSTEM_OVERVIEW.md for product context):

- flashruntime/  — **PUBLIC**, mirrored standalone at
                   `Zolli-Labs/flashruntime`. Protocol + fault-tolerant
                   runtime. The other two depend on it; it depends on
                   neither.
- flashnode/     — **PUBLIC**, mirrored standalone at
                   `Zolli-Labs/flashnode`. The host agent contributors
                   install.
- flashml-cloud/ — PRIVATE forever. Managed control plane + dashboard.
                   Never mirrored anywhere public.

## The public repos are downstream of this one

Work happens here. The public copies are updated by splitting a subtree out
and pushing:

```bash
git subtree split --prefix=flashnode -b split-flashnode
git merge-base --is-ancestor <public-main> split-flashnode   # must pass
git push https://github.com/Zolli-Labs/flashnode.git split-flashnode:main
```

Never force-push them. Contributors clone those repos, so a rewritten
history breaks every existing checkout.

**Push after any change under `flashruntime/` or `flashnode/`.** Volunteers
install from the public repos, so an unsynced subtree means the agent
running on someone's laptop is not the agent this repo's tests cover. That
has already happened: `flashnode/executor/images.py` existed here and not
there, so a public install still demanded the hand-maintained image
allowlist this repo had already removed.

Dependency rule: flashml-cloud and flashnode import flashruntime's versioned
protocol package. Nothing else crosses component boundaries. Never copy
private code into the public subtrees — one repo makes that easy to do by
accident, and `flashruntime/scripts/audit_secrets.sh` does not catch it.

## Naming history

Until 2026-08-01 this repo was `Zolli-Labs/flashml-poc`, and a separate,
now-superseded `Zolli-Labs/flashml-cloud` held only the cloud component. Its
final commit `159ff30` is an ancestor of this history, so nothing was lost;
it was renamed `flashml-cloud-legacy`.

The Supabase project is still *named* `flashml-poc`. That is a different
thing from the repo, its ref `yualksqjjvlfscbbsygq` is what anything
actually resolves, and renaming it would only invalidate the docs citing it.

Local Python setup (per component): uv venv && uv pip install -e ".[dev]"
Cross-component editable: uv pip install -e ../flashruntime -e .
Run the whole stack locally: ./scripts/dev.sh --all

Workspace document map (read in this order when starting work):
- HANDBOOK.md               — READ FIRST, once: product + per-component
                              breakdown, as-built local architecture, cloud
                              target, implementation recipes, edge-case
                              register, research register (R1–R10), DoD.
- HANDOFF.md                — READ SECOND: the builder's exit notes —
                              ranked risks, hard-won gotchas, judgment
                              calls, per-sprint-item tips, git-branch state
                              (all work on local-milestone-2026-07).
- PROGRESS.md               — AUTHORITATIVE status: stage checklist, dated
                              work log, and the LOGGING PROTOCOL every
                              agent must follow.
- SPRINT_PLAN.md            — the next two weeks, day by day, with demos
                              and acceptance criteria.
- M1_DECISIONS.md           — the M1 (deployed multi-user POC) decision
                              record: what was decided, why, what it costs,
                              and what would make us revisit. Read before
                              re-opening any M1 design choice. Companion to
                              flashml-cloud/docs/superpowers/specs/
                              2026-07-31-deployed-multi-user-poc-design.md.
                              NOTE: M1 ships on Supabase + Render; Alibaba
                              is deferred, not abandoned (see D1).
- PLAN_2WEEKS.md            — the original staged plan; local half complete
                              (status banner at top). Still the Alibaba
                              (Stage 5) runbook detail.
- FLASHRUNTIME_EVALUATION.md — architecture decisions for flashruntime
                              (reliability runtime first; planner = explainable
                              feasibility filter; four axes; StrategyPlan;
                              library stances). Summarized in
                              flashruntime/docs/adr/0003.
- archive/ — historical: POC_PLAN.md + POC_REPORT.md (July 17–18 POC record).
- FlashML_Master_..._Report.docx — product strategy source of truth.
- e2e/                      — cloud-free end-to-end proof of the whole loop
                              (make e2e / e2e-demo) + the real-second-machine
                              runbook in e2e/README.md.
- Each repo's docs/SYSTEM_OVERVIEW.md is synced FROM flashruntime's copy
  (make sync-docs); never edit the copies in flashnode/flashml-cloud.
