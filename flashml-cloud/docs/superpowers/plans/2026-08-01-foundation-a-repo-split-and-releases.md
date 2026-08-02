# Foundation A — Repo split, releases, and a working POC

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to work through this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **This is a runbook, not a TDD plan.** It creates public repositories, publishes to PyPI, and archives repos that other people have cloned. Steps marked **🔒 HUMAN GATE** must be performed or explicitly approved by the repository owner. Do not run them unattended.

**Goal:** Collapse two divergent copies of `flashruntime`/`flashnode` into one public repo with versioned releases, excise them from the private repo, and prove the POC still works end to end afterwards.

**Architecture:** `Zolli-Labs/flashml` (new, public) holds `flashruntime`, `flashnode`, and the federated example, assembled from the existing public mirrors so history survives. `Zolli-Labs/flashml-cloud` (private) keeps the API, console, and e2e, and consumes the runtime as a **pinned published version** instead of a relative path. Subtree splitting stops existing.

**Tech Stack:** git subtree, GitHub, PyPI trusted publishing, GitHub Actions, uv, pip.

## Global Constraints

- **Never force-push a public repo.** Contributors have clones; a rewritten history breaks every existing checkout.
- **Never delete a public repo.** Archive read-only. Today's enrolment command points at `git+https://github.com/Zolli-Labs/flashruntime`, and archived GitHub repos still clone, so enrolled machines keep working.
- The POC must be demonstrably working at Task 1 and again at Task 9. "It probably still works" is not evidence — `PROGRESS.md`'s protocol requires counts and commands.
- No private code may enter the public repo. `flashruntime/scripts/audit_secrets.sh` does not catch this; the review in Task 3 Step 4 does.
- Distribution names: **`flashruntime`** and **`flashnode`**, unchanged from the current `pyproject.toml` files. Both verified free on PyPI 2026-08-01 (404). *Assumption — flag before Task 5 if you want different names, since PyPI names cannot be reused once claimed.*
- `flashml` on PyPI is **taken** (abandoned `flashML` 0.1.3, 2021). The future submit CLI needs another name. Not this plan's problem; do not rely on it.

## Version plan

| Package | Current in tree | Publish as | Why |
|---|---|---|---|
| `flashruntime` | 0.2.0 | **0.3.0** | The tree has moved well past what 0.2.0 described (fedavg driver, `build_round`). Never published, so nothing is superseded. |
| `flashnode` | 0.1.0 | **0.2.0** | Same. Its floor becomes `flashruntime>=0.3,<0.4`. |

---

## Scope

**In:** spec §3.1 (topology), §3.2 (migration), §3.3 (pinning), §3.4 (release pipeline), §3.6 (drift detection via e2e), §3.7 (dev loop), plus the repo hygiene the spec did not enumerate.

**Out, deliberately:**
- **Spec §3.5, the protocol-version handshake.** It is code in both `flashruntime` and `flashnode` and is not needed for the POC to work. It becomes Plan A2, and spec DoD item 6 stays open until then.
- Everything in Plan B1 (lease-store concurrency) and B2 (diskless control plane). **Parked** — the POC works on SQLite today.
- The submit CLI (S2), desktop app (S4), security close-out (S3).

---

### Task 1: Establish a green baseline

You cannot safely refactor what you cannot prove works. Everything after this compares against these numbers.

**Files:** none modified. Evidence only.

- [ ] **Step 1: Confirm the working tree is clean and note the commit**

```bash
cd /Users/phongcao/Work/Zolli-Labs/flashml-cloud
git status --porcelain
git rev-parse HEAD
```
Expected: only the untracked `docs/superpowers/` spec and plan files. Record the SHA — it is the rollback point for the entire plan.

- [ ] **Step 2: Run every suite and record real counts**

```bash
(cd flashruntime && .venv/bin/pytest -q 2>&1 | tail -3)
(cd flashnode    && .venv/bin/pytest -q 2>&1 | tail -3)
(cd e2e          && .venv/bin/pytest -q 2>&1 | tail -3)
(cd flashml-cloud/apps/api && .venv/bin/pytest -q 2>&1 | tail -3)
(cd flashml-cloud/apps/web && npm test 2>&1 | tail -5)
```

Write the actual numbers here before continuing — not "all pass":

```
flashruntime: ____    flashnode: ____    e2e: ____    api: ____    web: ____
```

If a venv is broken, repair it with the runbook in the workspace `CLAUDE.md` ("Venvs break when this directory moves") and **verify from a neutral working directory** — checking from the repo root resolves `flashruntime` out of the CWD and reports a broken venv as healthy.

- [ ] **Step 3: Prove the local stack runs**

```bash
cd /Users/phongcao/Work/Zolli-Labs/flashml-cloud
./scripts/dev.sh --all
```
Expected: coordinator on :8100, API on :8000, console on :3000. Open `http://localhost:3000`, sign in, and load `/jobs`. A console that shows "Failed to fetch" means the API or coordinator did not start — that is the failure this step exists to catch. Stop the stack when confirmed.

- [ ] **Step 4: Create the working branch**

```bash
git checkout -b foundation-a-repo-split
git add flashml-cloud/docs/superpowers/
git commit -m "docs: foundation spec and plans (S1)"
```

---

### Task 2: Repo hygiene in the private repo

Safe, local, reversible — and it is the part that makes the repo stop *feeling* messy. Done before the split so the split carries less junk.

**Files:**
- Delete (tracked): `flashml-cloud.db`, `flashml-cloud/apps/api/flashml_cloud_api.egg-info/` (5 files)
- Modify: `.gitignore`
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py` (remove the dead v0.1.0 factory)

- [ ] **Step 1: Remove committed build artifacts and databases**

```bash
git rm --cached flashml-cloud.db
git rm -r --cached flashml-cloud/apps/api/flashml_cloud_api.egg-info
rm -f flashml-cloud.db
rm -rf flashml-cloud/apps/api/flashml_cloud_api.egg-info
```

Append to the root `.gitignore`:

```gitignore
# Build artifacts and local databases — never tracked. flashml-cloud.db and
# apps/api/*.egg-info were committed by accident; the egg-info in particular
# goes stale the moment anyone reinstalls and produces confusing diffs.
*.db
*.egg-info/
.pytest_cache/
build/
.local-state/
```

- [x] **Step 2: ~~Delete the dead FastAPI factory~~ — CANCELLED 2026-08-01**

**The factory is not dead.** The check below was run and it failed, exactly as
this step's own guard intended. `create_app()` (`app.py:1402`) falls back to
`_create_legacy_app()` whenever `SUPABASE_URL` or `COORDINATOR_URL` is absent,
and `tests/test_agent_proxy.py:825` pins that by name. Deleting it would have
broken a tested code path.

What the check surfaced instead is a security concern for **S3**, recorded in
spec §1.1: the fallback means a deployed API that loses one environment
variable silently starts serving an open, unauthenticated node registry. The
fix is not deletion — it is refusing to boot on incomplete configuration, or
gating the legacy app behind an explicit opt-in rather than behind the
*absence* of config.

`app.py` therefore stays at 1418 lines for now. Splitting it is S3's job.

*Original step, retained for the record:*

`flashml-cloud/apps/api/flashml_cloud_api/app.py` is 1418 lines and defines **two** app factories: the live one at line 517 (`FastAPI(title="FlashML Cloud API", version="0.2.0")`) and a dead legacy one at line 1306 (`version="0.1.0"`). Only `create_app()` at line 1418 is exported.

Confirm the legacy factory is genuinely unreferenced before deleting:

```bash
cd flashml-cloud/apps/api
grep -rn "0\.1\.0\|legacy" flashml_cloud_api/app.py | head
grep -rn "create_legacy_app\|_legacy_app" flashml_cloud_api/ tests/ ../../../e2e/ | head
```
Expected: no references from `tests/` or `e2e/`. **If anything references it, stop and report** — it is not dead and this step needs rethinking.

Delete lines ~1292–1417 (the legacy section header through the end of that factory), leaving `app = create_app()` intact.

- [ ] **Step 3: Verify nothing broke**

```bash
cd flashml-cloud/apps/api && .venv/bin/pytest -q 2>&1 | tail -3
```
Expected: same count as Task 1 Step 2. A *lower* count means you deleted a tested code path — restore and re-check.

- [ ] **Step 4: Commit**

```bash
cd /Users/phongcao/Work/Zolli-Labs/flashml-cloud
git add -A
git commit -m "chore(repo): drop committed build artifacts and the dead v0.1.0 app factory

flashml-cloud.db and apps/api/*.egg-info were tracked by accident. app.py
carried a second, unreferenced FastAPI factory (version 0.1.0) alongside the
live 0.2.0 one; only create_app() was ever exported."
```

---

### Task 3: Final subtree sync, then build the public repo

The last `git subtree split` this project will ever run. The public mirrors may lag the monorepo, and the new repo is assembled *from the mirrors* — so they must be current first, or the new public repo starts out behind.

- [ ] **Step 1: Measure the drift before touching anything**

```bash
cd /Users/phongcao/Work/Zolli-Labs/flashml-cloud
git subtree split --prefix=flashruntime -b split-flashruntime
git subtree split --prefix=flashnode    -b split-flashnode

git fetch https://github.com/Zolli-Labs/flashruntime.git main:public-flashruntime
git fetch https://github.com/Zolli-Labs/flashnode.git    main:public-flashnode

git log --oneline public-flashruntime..split-flashruntime | wc -l
git log --oneline public-flashnode..split-flashnode | wc -l
```
Record both numbers. Non-zero means the mirror is behind — exactly the drift that caused the `images.py` incident.

- [ ] **Step 2: Verify fast-forward, then push**

```bash
git merge-base --is-ancestor public-flashruntime split-flashruntime && echo "runtime FF ok"
git merge-base --is-ancestor public-flashnode    split-flashnode    && echo "node FF ok"
```
Both must print. **If either fails, stop** — the mirror has commits the monorepo lacks, and pushing would need a merge, not a fast-forward. Report rather than forcing.

**🔒 HUMAN GATE — pushes to public repos:**
```bash
git push https://github.com/Zolli-Labs/flashruntime.git split-flashruntime:main
git push https://github.com/Zolli-Labs/flashnode.git    split-flashnode:main
```

- [ ] **Step 3: 🔒 HUMAN GATE — create the empty public repo**

Create `Zolli-Labs/flashml` on GitHub: **public**, no README, no .gitignore, no license (they arrive with the subtrees).

- [ ] **Step 4: Assemble it from the mirrors**

```bash
cd /Users/phongcao/Work/Zolli-Labs
git clone https://github.com/Zolli-Labs/flashml.git
cd flashml
git commit --allow-empty -m "chore: initialise the public FlashML monorepo"

git subtree add --prefix=flashruntime https://github.com/Zolli-Labs/flashruntime.git main
git subtree add --prefix=flashnode    https://github.com/Zolli-Labs/flashnode.git    main
git subtree add --prefix=examples/federated \
    https://github.com/Zolli-Labs/flashml-example-federated.git main
```

Confirm history survived — each should show real commits, not one squashed import:

```bash
git log --oneline -- flashruntime | wc -l
git log --oneline -- flashnode | wc -l
```

- [ ] **Step 5: Review for private content before the first push**

This is the irreversible moment. Once pushed, anything private is public forever, and the audit script does not catch it.

```bash
grep -rniE "supabase|render\.com|onrender|operator_token|service_role|DATABASE_URL" \
  --include='*.py' --include='*.md' --include='*.toml' --include='*.yaml' . | head -40
bash flashruntime/scripts/audit_secrets.sh
```
Expected: hits only in generic docs/examples, never a real credential, project ref (`yualksqjjvlfscbbsygq`), or hostname. **Read every hit.** Anything real must be removed *before* the first push, because removing it after requires a history rewrite, which this plan forbids.

- [ ] **Step 6: 🔒 HUMAN GATE — push the public monorepo**

```bash
git push origin main
```

---

### Task 4: Add a top-level README to the public repo

The repo is now the front door for volunteers and contributors. An unexplained directory of three projects is not.

**Files:** Create `README.md` in `Zolli-Labs/flashml`.

- [ ] **Step 1: Write it**

Cover, briefly: what FlashML is; that `flashruntime` is the protocol and fault-tolerant runtime, `flashnode` the host agent, `examples/federated` a submittable example; how to install the agent (`pip install flashnode` — true from Task 5 onward); that the managed control plane is a separate private service; and the Apache-2.0 license.

Do not describe the install as working until Task 5 has actually published. If Task 4 lands first, say "coming with the next release" rather than shipping a command that 404s — a broken first command is worse than no command.

- [ ] **Step 2: 🔒 HUMAN GATE — commit and push**

```bash
git add README.md && git commit -m "docs: explain the three components and how to install the agent"
git push origin main
```

---

### Task 5: First real PyPI releases

**Files (in `Zolli-Labs/flashml`):**
- Modify: `flashruntime/pyproject.toml` (version → 0.3.0)
- Modify: `flashnode/pyproject.toml` (version → 0.2.0; floor → `flashruntime>=0.3,<0.4`)
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: 🔒 HUMAN GATE — configure PyPI trusted publishing**

On PyPI, create pending publishers for **`flashruntime`** and **`flashnode`**, each bound to repository `Zolli-Labs/flashml`, workflow `release.yml`, environment `pypi`. Trusted publishing avoids a long-lived API token in repository secrets.

Both names verified free (404) on 2026-08-01. **Claiming them is irreversible** — confirm the names before this step.

- [ ] **Step 2: Bump versions**

`flashruntime/pyproject.toml`: `version = "0.3.0"`.

`flashnode/pyproject.toml`: `version = "0.2.0"`, and change the dependency to `"flashruntime>=0.3,<0.4"`. Update the comment above it — it currently explains the `>=0.2` floor in terms of `NodeRegistration.module_capable`; the reasoning still holds but the floor has moved.

- [ ] **Step 3: Add the release workflow**

Create `.github/workflows/release.yml`, triggered on tags `flashruntime-v*` and `flashnode-v*`. It should: check out, select the package directory from the tag prefix, `python -m build`, then `pypa/gh-action-pypi-publish` under `environment: pypi` with `permissions: id-token: write`. One workflow, two tag patterns — the package directory is derived from the tag, so there is nothing to keep in sync.

- [ ] **Step 4: 🔒 HUMAN GATE — tag and release**

```bash
git add -A && git commit -m "chore: release flashruntime 0.3.0 and flashnode 0.2.0"
git push origin main
git tag flashruntime-v0.3.0 && git push origin flashruntime-v0.3.0
# wait for the run to finish, then:
git tag flashnode-v0.2.0 && git push origin flashnode-v0.2.0
```

Release `flashruntime` **first and wait**. `flashnode` 0.2.0 declares `flashruntime>=0.3`, so publishing it first produces a package that cannot be installed.

- [ ] **Step 5: Verify the install a volunteer would actually run**

From a clean directory, with no repo checkout in sight:

```bash
cd $(mktemp -d)
python3 -m venv v && v/bin/python -m pip install flashnode
v/bin/flashnode --help
v/bin/python -c "import flashruntime, flashnode; print(flashruntime.__version__)"
```
Expected: installs from PyPI with no git URLs, `--help` works, version prints `0.3.0`.

**This step is the whole point of Plan A.** If it fails, the release is not done.

---

### Task 6: Excise the subtrees and pin the versions

Back in the private repo. This is where the two-copies problem actually ends.

**Files (in `flashml-cloud`):**
- Delete: `flashruntime/`, `flashnode/`
- Modify: `flashml-cloud/apps/api/pyproject.toml`, `render.yaml`, `e2e/` install config, `scripts/dev.sh`, `AGENTS.md`, workspace `CLAUDE.md`

- [ ] **Step 1: Remove the subtrees**

```bash
cd /Users/phongcao/Work/Zolli-Labs/flashml-cloud
git rm -r flashruntime flashnode
```
Their history stays in this repo's past and lives on in `Zolli-Labs/flashml`. Nothing is lost.

- [ ] **Step 2: Pin the API**

`flashml-cloud/apps/api/pyproject.toml`: change `"flashruntime>=0.1"` to `"flashruntime==0.3.0"`.

- [ ] **Step 3: Fix `render.yaml`**

Replace the API `buildCommand`:

```yaml
    buildCommand: pip install -e .
```

Delete the long comment above it that explains the relative-path install — including the claim about a stale published wheel, which is **factually wrong** (`flashruntime` had never been published; verified 404 on 2026-08-01). Replace with a short note that `flashruntime` now installs from PyPI at a pinned version.

The **coordinator** service also has `rootDir: flashruntime`, which no longer exists in this repo. It must now install the published package instead:

```yaml
    rootDir: .
    buildCommand: pip install "flashruntime[service]==0.3.0"
    startCommand: >-
      FLASHML_OPERATOR_TOKENS="flashml-api:$OPERATOR_TOKEN"
      uvicorn flashruntime.service.app:app
      --host 0.0.0.0 --port $PORT --workers 1
```

Leave `type: pserv`, `--workers 1`, the disk, and the absence of `healthCheckPath` **exactly as they are**. Each is load-bearing and documented in place; Plan B2 revisits them, this plan does not.

- [ ] **Step 4: Pin e2e**

`e2e` currently installs `-e ../flashruntime -e ../flashnode`. Point it at the published versions so it tests what is actually deployed (spec §3.6):

```bash
(cd e2e && uv pip install "flashruntime==0.3.0" "flashnode==0.2.0" \
   -e ../flashml-cloud/apps/api pytest requests torch numpy scikit-learn pandas scipy)
```
Update `e2e/README.md` and any Makefile target that references the relative paths.

- [ ] **Step 5: Preserve the fast inner loop (spec §3.7)**

Add `--local-runtime` to `scripts/dev.sh`: when passed **and** `../flashml/flashruntime` exists, editable-install from there; otherwise use the pin. Default is the pin. Print which one was used at startup — a silent switch here is precisely how the two-copies problem returns.

- [ ] **Step 6: Update the docs that describe the old layout**

Both `AGENTS.md` and the workspace `CLAUDE.md` describe the subtree arrangement and the split-and-push rule at length. That rule is now **gone**, and leaving it in place would send the next agent to re-create the drift this plan removed.

Rewrite both to describe: two repos, one direction of dependency, pinned versions, and the `--local-runtime` loop. Delete the subtree commands entirely rather than marking them deprecated.

- [ ] **Step 7: Verify**

```bash
(cd flashml-cloud/apps/api && .venv/bin/pytest -q 2>&1 | tail -3)
(cd e2e && .venv/bin/pytest -q 2>&1 | tail -3)
```
Expected: same counts as Task 1. e2e is now running against **published** artifacts — if it fails here but passed at Task 1, the published packages differ from the working tree, which is exactly the drift this plan exists to detect. Investigate; do not paper over it by reverting to editable installs.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(repo): excise the public subtrees, pin published versions

flashruntime and flashnode now live in the public Zolli-Labs/flashml repo and
install from PyPI at pinned versions. render.yaml no longer installs from a
relative path, e2e tests the published artifacts, and the subtree split-and-push
rule is deleted from AGENTS.md and CLAUDE.md rather than deprecated."
```

---

### Task 7: Fix the volunteer install command

The three-command venv dance existed **only** because neither package was on PyPI. That is no longer true.

**Files:** `flashml-cloud/apps/web/components/machines/EnrolInstructions.tsx`

- [ ] **Step 1: Simplify the steps**

The `REPOS` constant and the git-URL install go away. The venv **stays** — the file's header comment explains why, and every reason still holds: macOS ships no `pip` on PATH, and Homebrew Python refuses `pip3 install` with PEP 668 `externally-managed-environment`. Those are unrelated to PyPI.

New unix steps:

```
python3 -m venv flashml
flashml/bin/python -m pip install flashnode
flashml/bin/flashnode login --coordinator <base>
```

Windows equivalently with `py -m venv flashml` and `flashml\Scripts\...`.

- [ ] **Step 2: Rewrite the header comment to match reality**

The comment currently says: *"`pip install flashnode` → No solution found… neither package is published there. Naming both as git URLs is the only thing that works today."* That is now false and would mislead the next reader into restoring the git URLs.

Replace that bullet with a note that both packages publish to PyPI as of `flashruntime` 0.3.0 / `flashnode` 0.2.0, and keep the `pip` / `pip3` / PEP 668 bullets, which are still true and still the reason for the venv.

- [ ] **Step 3: Verify**

```bash
cd flashml-cloud/apps/web && npm test 2>&1 | tail -5
```
Then run `./scripts/dev.sh --all`, open `/machines`, and **copy each command out of the UI and run it** in a scratch directory. The point of this component is that its commands work when pasted; reading them is not verification.

- [ ] **Step 4: Commit**

```bash
git commit -am "fix(web): install the agent from PyPI, not git URLs

Both packages are published as of flashruntime 0.3.0 / flashnode 0.2.0, so the
git-URL workaround is gone. The venv stays: PATH and PEP 668 are why it exists,
and neither changed."
```

---

### Task 8: Archive the old public repos and consolidate the docs

**🔒 HUMAN GATE — this whole task.** Archiving is visible to anyone watching those repos.

- [ ] **Step 1: Add a pointer README to each old repo**

To `Zolli-Labs/flashruntime`, `Zolli-Labs/flashnode`, and `Zolli-Labs/flashml-example-federated`, commit a README stating development moved to `Zolli-Labs/flashml`, that the repo is archived and read-only, that existing clones and `git+https://` installs keep working, and that new installs should use `pip install flashnode`.

- [ ] **Step 2: Archive, do not delete**

GitHub → Settings → Archive this repository, for each. **Do not delete.** Existing enrolment commands point at these URLs; archived repos still clone, so machines enrolled before this migration keep working.

- [ ] **Step 3: Consolidate the top-level docs**

Nine markdown files plus a 1.2 MB `.docx` sit at the repo root with overlapping authority:

| File | Disposition |
|---|---|
| `AGENTS.md` / `CLAUDE.md` | Keep — rewritten in Task 6 |
| `PROGRESS.md` | Keep — authoritative status |
| `HANDBOOK.md` | Keep |
| `HANDOFF.md` | Move to `archive/` — a July 2026 session's exit notes, now largely historical |
| `PLAN_2WEEKS.md` | Move to `archive/` — its own banner says the local half is complete |
| `SPRINT_PLAN.md` | Move to `archive/` — superseded by the S1–S5 program |
| `M1_DECISIONS.md` | Keep — the decision record |
| `FLASHRUNTIME_EVALUATION.md` | Move to the **public** repo — it is flashruntime architecture, and ADR 0003 already summarises it there |
| `FlashML_Master_..._.docx` (1.2 MB) | Keep, but move to `docs/` — it is the strategy source of truth and does not belong at the root |

Update `AGENTS.md`'s document map in the same commit. A map pointing at moved files is worse than no map.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs: archive superseded plans, move the runtime evaluation to the public repo"
```

---

### Task 9: Prove the POC still works

The plan is not done because the commits landed. It is done when the loop runs.

- [ ] **Step 1: Local stack, from a clean install**

```bash
cd /Users/phongcao/Work/Zolli-Labs/flashml-cloud
rm -rf flashml-cloud/apps/api/.venv e2e/.venv
(cd flashml-cloud/apps/api && uv venv .venv && uv pip install -e ".[dev]")
(cd e2e && uv venv .venv && uv pip install "flashruntime==0.3.0" "flashnode==0.2.0" \
   -e ../flashml-cloud/apps/api pytest requests torch numpy scikit-learn pandas scipy)
./scripts/dev.sh --all
```

Note what changed: the API venv no longer needs `-e ../../../flashruntime`. It resolves from PyPI. That is the fix, made visible.

- [ ] **Step 2: Full suites, compared against the baseline**

```bash
(cd flashml-cloud/apps/api && .venv/bin/pytest -q 2>&1 | tail -3)
(cd e2e && .venv/bin/pytest -q 2>&1 | tail -3)
(cd flashml-cloud/apps/web && npm test 2>&1 | tail -5)
```
Every count must match Task 1 Step 2. `flashruntime` and `flashnode` suites now run in the **public** repo — run them there and record separately.

- [ ] **Step 3: The end-to-end loop with a real second process**

```bash
make e2e
```
Submit the federated example through the console, watch a machine claim and execute it, and confirm the job reaches a terminal state with artifacts.

- [ ] **Step 4: 🔒 HUMAN GATE — verify the deployed POC**

Trigger a Render deploy from the updated `render.yaml`. Both services must build **without any relative-path install**. Check:
- `flashml-api` `/healthz` returns healthy
- `flashml-coordinator` starts (visible only in deploy logs — it is a `pserv` with no public URL and, deliberately, no `healthCheckPath`)
- the console loads and lists jobs
- a real volunteer machine enrolls with the new `pip install flashnode` command

- [ ] **Step 5: Log it in `PROGRESS.md`**

Follow the logging protocol: what/why, how verified with **actual counts and commands**, gotchas, next. Update the stage checklist in the same edit.

- [ ] **Step 6: Merge**

```bash
git checkout main && git merge --no-ff foundation-a-repo-split
git push origin main
```

---

## Definition of done

1. `Zolli-Labs/flashml` is public and holds `flashruntime`, `flashnode`, and `examples/federated` with history intact.
2. The three old public repos are archived read-only with pointer READMEs. Nothing was deleted; nothing was force-pushed.
3. `flashml-cloud` contains no `flashruntime/` or `flashnode/` directory.
4. `pip install flashnode` works on a clean machine with no git URLs — verified from an empty directory.
5. `render.yaml` contains no relative-path install, and both services deploy from it.
6. `e2e` runs against published artifacts and matches its baseline count.
7. The enrolment UI's commands work when pasted, verified by pasting them.
8. `AGENTS.md` and `CLAUDE.md` describe the two-repo layout; the subtree split-and-push rule is deleted.
9. The local stack runs and a job completes end to end.
10. `PROGRESS.md` carries an entry with real evidence.

**Spec DoD items intentionally left open:** item 5 (release-blocks-SHA-pins check) and item 6 (protocol-version handshake) — both move to Plan A2. Items 8–10 belong to Plan B2.

## Rollback

Everything in the private repo is on `foundation-a-repo-split` and reverts by deleting the branch. Two things do not:

- **PyPI releases cannot be unpublished** — a version can be yanked, never reused. This is why Task 5 Step 1 is a gate.
- **The public repo, once pushed, is public.** Task 3 Step 5 is the only defense; treat it as the point of no return.

Archiving is reversible (a repo can be unarchived), so Task 8 is the least dangerous of the three.
