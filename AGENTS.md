# Zolli Labs workspace

Three sibling repos forming the FlashML system (see any repo's
docs/SYSTEM_OVERVIEW.md for full product context):

- flashruntime/  — PUBLIC at launch. Protocol + fault-tolerant runtime. The
                   other two depend on it; it depends on neither.
- flashnode/     — PUBLIC at launch. Host agent contributors install.
- flashml-cloud/ — PRIVATE forever. Managed control plane + dashboard.

Dependency rule: flashml-cloud and flashnode import flashruntime's versioned
protocol package. Nothing else crosses repo boundaries. Never copy private
code into the public repos.

Local Python setup (per repo): uv venv && uv pip install -e ".[dev]"
Cross-repo editable: uv pip install -e ../flashruntime -e .

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
