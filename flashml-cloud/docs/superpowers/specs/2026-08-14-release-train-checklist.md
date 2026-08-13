# Release train — flashruntime 0.6.1 + flashnode 0.4.1 (prepared 2026-08-13 night)

The one document to open before releasing. Everything below was staged
tonight across three sessions; nothing here is speculative.

## 0. What is waiting to ship

| Branch (public repo) | Tip | State | Carries |
|---|---|---|---|
| `fix/trusted-tier-execution` | ac68d9d + one CLI-UX commit landing tonight | PUSHED | uv interpreter pin; FC-hold fixes (index-pinned+logged env builds, trusted timeout); `flashnode status`; CLI presentation layer; flashml jobs-renderer fix; interpreter provisioning (agent); flashruntime `python` payload forward; `flash` shortcuts + `install.sh` (in flight) |
| `fix/lease-zombie-hardening` | e31ea45 (18 commits from 9a943b5) | LOCAL ONLY — worktree `.worktrees/lease-zombie-hardening` | expiry-vs-failure budgets (churn no longer burns attempts); progress watchdog; force-expire; zombie fencing (410s); kill-on-lease-lost; process-GROUP kill; heartbeat from lease_seconds (clock-skew fix). SDD ledger: that worktree's `.superpowers/sdd/2026-08-13-lease-zombie-hardening/progress.md` |

Cloud side already LIVE on dev+prod (110bd54): compile emits `python`
(yaml > image table > absent); db pooler guard; both DATABASE_URLs on :6543.

## 1. Merge order (agreed between sessions)

Rebase/merge `fix/lease-zombie-hardening` ONTO `fix/trusted-tier-execution`'s
final tip, then both suites AGAIN on the merged tree. **Four cautions from the
lease session's whole-branch review — a merge tool will not defend any of
them:**

1. **trusted_runner.py env-merge ORDER is load-bearing**: the payload-env
   merge line must stay BELOW `env_with_interpreter_first_on_path`, or a
   submitter PATH shadows the provisioned interpreter on the no-container
   tier. Verify by inspection after rebase.
2. **`ensure()` gained keyword-only `on_spawn=None`** (build children join
   the killable group). The `python=` kwarg composes trivially — take the
   provisioning call site and add `on_spawn`. ANY new subprocess the
   provisioning added in environments.py (uv bootstrap, uv venv/pip) must
   route through `run_with_group_kill` and forward `on_spawn`, or the
   unbounded-reap orphan class returns silently.
3. **`environments._run` no longer accepts `capture_output=`/`stdout=`**
   (run_with_group_kill owns the pipes). The provisioning branch's `_run`
   also added `env=` — reconcile signatures; a stale kwarg TypeErrors at
   runtime, not at merge.
4. **Rollout ordering**: the checkpoint fence is unconditional — an OLD
   node's relay retries 410s every 0.3s after any natural lease expiry
   until the fleet upgrades. Land coordinator + fleet in the same window.
   (Also in flashruntime/CHANGELOG.md under Rollout.)

## 2. Gate

`make e2e LOCAL=1` against the MERGED tree (point LOCAL at the worktree or
merge to a branch the Makefile's `../flashml` path sees — verify which tree
LOCAL actually installs before trusting the green).

## 3. Release (the recorded gotchas apply)

- flashruntime **0.6.1**, then flashnode **0.4.1** (package-scoped tags).
- A RED release run STILL PUBLISHES; docs-deploy ALWAYS fails on tags —
  neither is a reason to panic or re-tag.

## 4. Pin bump — one commit, six files

`Makefile` RUNTIME_VERSION · render.yaml prod coordinator · render.yaml dev
coordinator · apps/api/pyproject.toml · workspace CLAUDE.md table · repo
CLAUDE.md table. Then: dev auto-deploys on push; prod api/web/coordinator are
MANUAL triggers — per caution 4, redeploy coordinators and reinstall fleet
agents in the same window.

## 5. Post-release proof

1. Flip the cloud's gap test `test_the_pinned_recipe_stops_the_interpreter_
   at_the_jobspec` (its docstring says how) — it fails on purpose the day
   the pin moves.
2. Reinstall Mac/pod/FC agents from PyPI (`pip install flashnode` /
   `install.sh` — flip the README curl URL from the fix branch to `main`).
3. Rerun the zolli-demo sweep on dev with ZERO hand-provisioning: three
   green bars = provisioning proven end-to-end through released artifacts.
4. Trusted-tier watchdog note for any future flashml.yaml mapping: the
   coordinator reads `checkpoint.watchdog_seconds` (explicit 0 disables >
   max(3600, 3×interval) > non-checkpoint unarmed) — owner's
   user-submission-wins rule, already honored server-side.

## 6. Not in this train (stays open in the registers)

Bounded DB connection pool at the `create_app(connect=...)` seam; console
polling → SSE; multi-arch (arm64) curated images; checkpoint-relay direct-to-
OSS; prod security-audit blockers (artifact reads!) before real users.
