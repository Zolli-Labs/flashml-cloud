# Sprint Plan — next two weeks (2026-07-20 → 2026-08-02)

> Owner view: PM/scrum breakdown of what happens next, now that the local
> milestone is complete (`PROGRESS.md` has the full log). Each day ends
> with a **demo** and a PROGRESS entry, or it isn't done. Cut scope from
> the bottom of a day, never from its demo. Research items (R#) refer to
> the register in `HANDBOOK.md` §7.

**Sprint goal:** close the local board (metrics + real hardware), then put
the unchanged loop on Alibaba — same code, new address — and finish with a
recorded, numbers-backed story.

**Hard external dependency:** Alibaba credentials (ACK/ACR/OSS/RAM — see
`.env.alibaba.example`). Needed by Day 4. If late, swap Week 2 forward:
Days 8–10 (recipe work) are cloud-independent.

---

## Week 1 — close local, open cloud

**Day 1 — Stage 8a: metrics engine (R3 first).**
Resolve R3 on paper *before* code: write the exact event-pair definitions
(MTTD = kill signal→LEASE_EXPIRED; MTTR = LEASE_EXPIRED→next
TASK_COMMIT_ACCEPTED for that task; goodput = accepted-task wall-clock ÷
total leased wall-clock; lost-work from checkpoint manifests) into
`flashruntime/docs/metrics.md`. Then TDD `service/metrics.py`: pure
functions over ledger events → `GET /v1alpha1/jobs/{id}/metrics` +
fleet-level `GET /v1alpha1/metrics`.
*Demo:* run the e2e kill-sweep, curl the metrics endpoint, see real
MTTD/MTTR numbers from the actual events. *DoD:* unit tests pin each
metric against a hand-built event sequence.

**Day 2 — Stage 8b: dashboard metrics + case study.**
Metrics section on the dashboard (plain numbers/tables — no chart library).
Write `CASE_STUDY.md`: local-sequential baseline vs FlashML on the 12-trial
sweep + the resume story (5 steps lost, not 35), with reproduction
commands. Record the three demos (`make e2e-demo` runs) as the narrative.
*Demo:* dashboard shows goodput/MTTD/MTTR live during a sweep.

**Day 3 — real second machine + manifest persistence (R1).**
Morning: run the `e2e/README.md` LAN runbook with the user's second device
(join code, lid-close mid-run, watch requeue). **Needs the user.** Log
observed NAT/firewall friction. Afternoon: R1 → TDD manifest persistence
(SQLite beside leases.db, same save() pattern) so checkpoint files are
never orphaned by a restart.
*Demo:* coordinator restart mid-training; resume still finds the manifest.

**Day 4 — Stage 5a: Alibaba bootstrap (blocked on credentials).**
ECS-first per `PLAN_2WEEKS.md` Stage 5: provision 1 small ECS + OSS bucket
+ ACR namespace; push images (`scripts/alibaba/acr-*`); coordinator up via
compose with `FLASHML_ARTIFACT_BACKEND=oss`; join code set; SLS Logtail.
*Demo:* public dashboard URL; healthz from a phone browser.

**Day 5 — Stage 5b: real devices → cloud coordinator.**
Point both local machines' `flashnode work` at the ECS URL. Run the
12-trial sweep + kill-one demo *over the internet*. Verify OSS artifacts +
SLS logs by job_id. Start R2 (STS-scoped uploads): design note + minimal
implementation (cloud API mints STS, agent uses it for OSS PUT) — closes
the open-PUT hole from HANDBOOK §6.
*Demo:* the master report's §15 demo, on real cloud + real homes.

**Day 6 — Stage 5c: training resume on cloud + hardening pass.**
Cross-machine sgd_trainer resume against the ECS coordinator. Then a
deliberate chaos hour: restart the ECS coordinator mid-job (durability),
fill an artifact over the cap, wrong join code, kill -9 an agent — confirm
every path degrades as designed; file PROGRESS notes on anything that
doesn't.
*Demo:* chaos checklist all green, on cloud.

**Day 7 — buffer + retro.**
Catch-up for slips (most likely: credentials, ECS networking). Sprint
retro entry in PROGRESS: what the cloud move actually cost vs. the "only
the address changes" claim. Update HANDBOOK §4 with as-deployed reality.

---

## Week 2 — first real-DL workload + cloud depth

**Day 8 — R5 research: LoRA recipe design (cloud-independent).**
Written design note before code: HF Trainer callback vs bare loop; Trainer
checkpoint dir ↔ relay contract (multi-file checkpoints ⇒ multi-part
manifests — the catalog already supports parts); checkpoint sizes vs relay
cadence; decide smallest credible model (e.g. 0.5B QLoRA on one 24 GB GPU
or CPU-tiny for the contract test). Output: `flashruntime/docs/adr/0004-lora-recipe.md`.

**Day 9–10 — LoRA recipe implementation (TDD).**
`recipes/hf_lora.py` task module obeying the executor contract (spec.json
in, metrics.json + ckpt/ out; resume via `inputs.resume`); multi-part
manifest support in the relay if Day 8 says needed; planner's `qlora_1gpu`
plan drives the config (first `flash.plan` → payload linkage — closes the
`flash.run` gap for Mode A).
*Demo:* kill a LoRA fine-tune mid-epoch; it resumes on the other machine;
loss curve continuous; lost-work reported in real steps.

**Day 11 — cloud Stage 6a: Postgres (R4).**
RDS PostgreSQL (or in-cluster PG if ACK arrives early): port `Ledger` +
`SqliteLeaseStore` behind their existing seams (same append-only schema,
same 5-method store protocol); config-switched, SQLite remains the local
default. *Demo:* the restart-survival test green against Postgres.

**Day 12 — cloud Stage 6b: ACK pool + hybrid.**
Stand up the ACK overlay (`infra/alibaba/ack/`), KubeRay Mode B smoke
(the POC's kill-worker demo on real ACK — first time off kind), FlashNode
DaemonSet telemetry. *Demo:* one dashboard showing device nodes AND the
ACK pool; a lease job and a Ray job side by side.

**Day 13 — recovery wiring + SSE (debt burn-down).**
Wire `recovery.classify()/decide()` into the coordinator's failure paths so
FAILURE_CLASSIFIED / RECOVERY_ACTION_SELECTED events actually fire (the
policy table finally earns its keep); SSE event stream endpoint replacing
dashboard polling if time allows.
*Demo:* kill a worker; the ledger shows the *typed decision*, not just the
expiry.

**Day 14 — ship the story.**
Re-run every suite + the cloud chaos checklist; record the cloud demos;
finalize `CASE_STUDY.md` with cloud numbers; update HANDBOOK/AGENTS
missing-lists; write the next sprint's seed (candidates: metrics → ARMS,
join → real auth, R6 long-poll, R8 isolation ladder). Sprint review entry
in PROGRESS.

---

## Standing rules

- **Blocked ≠ idle:** if credentials block Days 4–6, pull Days 8–10
  forward; log the swap in PROGRESS.
- **No silent scope growth:** anything not on this plan goes to the
  PROGRESS "parking lot" line of the day's entry, not into the code.
- **Every day's entry** follows the logging protocol in `PROGRESS.md` —
  including the verification evidence line.
- **Costs:** ECS/ACK torn down when idle (`poc-ack-destroy`); smallest
  instance sizes; artifact bucket lifecycle rule from Day 4.
