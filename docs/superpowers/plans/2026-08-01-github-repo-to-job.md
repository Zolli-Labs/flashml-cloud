# GitHub Repo → Job, with Preflight — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Paste a GitHub repo URL, hit submit, and watch your own PyTorch code train across the pool — with anything that *cannot* work told to you before the job is queued, not after it fails on a stranger's machine.

**Architecture:** A repo declares its job in `flashml.yaml`. On submit, the API fetches the repo tarball, runs static preflight checks against it, stores it as an `artifact://` input, and compiles the whole thing into a `CommandRecipe` JobSpec. **No image is built** — the code is staged into a curated, pinned image at `/work/inputs/`, which is the contract `docs/guides/bring-your-code.md` already specifies and `flashnode`'s executor already implements.

**Tech Stack:** FastAPI, PyYAML, `ast` (stdlib), httpx, pytest.

This is **Plan 4 of 7** for M1, implementing §5.3 of
`docs/superpowers/specs/2026-07-31-deployed-multi-user-poc-design.md`.

---

## Why no image build

The obvious design is Vercel-style: read the repo's `Dockerfile`, build, push, run. It is rejected for this milestone (`M1_DECISIONS.md` D5) because it needs a build host with Docker (Render standard instances cannot), a registry, a build queue, gigabyte pulls on volunteers' home connections, and allowlist propagation to every node — and that last one already caused an outage here when a module was allowlisted on one side of a repo boundary and not the other.

Staging the code as an input instead costs nothing: `flashnode`'s executor already downloads declared inputs to `/work/inputs/` before running (`executor/loop.py`), and volunteer nodes pre-pull three known images rather than arbitrary ones from strangers. The limitation is real — dependencies must exist in a curated image — and **preflight exists to make that limit legible at submit time.**

## Global Constraints

- **Volunteer nodes run with `--network none`.** A job cannot `pip install`, fetch a dataset, or reach HuggingFace once it starts. Everything must be in the image or staged as an input. Preflight must catch violations of this, because the failure otherwise happens on someone else's machine and reads as a node fault.
- **`metrics.json` at `/work/out/` is the commit artifact.** A task that does not write it cannot commit — the coordinator validates its sha256. Preflight warns when the entrypoint never mentions it.
- **The curated image set is closed in M1.** `flashml.yaml` names a short alias; the API resolves it to a pinned reference. Users never write a registry path, which is what keeps node allowlists satisfiable.
- **Never execute user code during preflight.** Every check is static (`ast` parse). A submit-time sandbox escape would be a far worse bug than the ones preflight prevents.
- **Fetch only what is needed.** Repo tarballs are size-capped before extraction, and extraction is path-traversal-safe — a malicious tarball must not write outside its directory (zip-slip).
- **Baselines — do not reduce:** flashruntime **528**, flashnode **85 passed 1 skipped**, e2e **15**, cloud API **35+** (record the current number before starting). No skips.

## File Structure

| File | Responsibility |
|---|---|
| `apps/api/flashml_cloud_api/images.py` (new) | The curated image registry: alias → pinned reference + the package manifest preflight checks imports against. |
| `apps/api/flashml_cloud_api/flashml_yaml.py` (new) | Parse and validate `flashml.yaml` into a typed object. No I/O. |
| `apps/api/flashml_cloud_api/repo.py` (new) | Fetch a GitHub tarball, safely extract, cap size. |
| `apps/api/flashml_cloud_api/preflight.py` (new) | Static checks over the extracted tree. Returns a list of findings; never raises on user error. |
| `apps/api/flashml_cloud_api/compile.py` (new) | `flashml.yaml` + repo → `CommandRecipe` JobSpec, including sweep expansion. |
| `apps/api/flashml_cloud_api/app.py` (modify) | `POST /v1alpha1/jobs/from-repo`. |

---

### Task 1: The curated image registry

**Files:** create `apps/api/flashml_cloud_api/images.py`, `apps/api/tests/test_images.py`

**Produces:**
- `CURATED: dict[str, CuratedImage]` keyed by alias
- `CuratedImage(alias, reference, packages: frozenset[str], description)`
- `resolve_image(alias) -> CuratedImage` raising `UnknownImage` with the available aliases in the message

Three images for M1: `python-slim` (stdlib only), `sklearn` (numpy, pandas, scikit-learn, scipy), `pytorch-cpu` (torch, numpy). `packages` is the **top-level importable module names** each provides, including the stdlib, because preflight compares against it.

Tests pin: every alias resolves; an unknown alias raises and the message lists the real ones; every reference is fully pinned (registry, name, and tag — no bare `latest`, which would silently change what a node runs); `packages` includes a representative stdlib module so preflight does not flag `import json`.

---

### Task 2: `flashml.yaml` parsing and validation

**Files:** create `apps/api/flashml_cloud_api/flashml_yaml.py`, `apps/api/tests/test_flashml_yaml.py`

**Produces:** `parse_flashml_yaml(text) -> FlashmlConfig`, `ConfigError`.

```yaml
version: 1
name: cifar-sweep
image: pytorch-cpu
entrypoint: train.py
args: ["--epochs", "20"]
sweep:                     # optional
  lr: [0.001, 0.01, 0.1]
  batch_size: [32, 64]
resources: {cpus: 2, memory_gb: 4}
timeout_seconds: 1800
```

Tests pin, each as its own case: a minimal valid config parses; `version` other than 1 is refused (so a future format cannot be silently misread); a missing `entrypoint` or `image` is refused; `args` must be a list of strings, not a shell string — a string would invite shell-injection thinking where there is no shell; `sweep` values must be non-empty lists; the sweep's **cartesian product size is capped** (e.g. 100) with a message naming the computed size, because `num_shards` is bounded at 999 downstream and a 5×5×5×5 sweep is more likely a mistake than an intention; `timeout_seconds` is bounded; unknown top-level keys are refused rather than ignored, so a typo'd `entrypint` fails loudly instead of yielding a config that means something else.

---

### Task 3: Fetching the repo safely

**Files:** create `apps/api/flashml_cloud_api/repo.py`, `apps/api/tests/test_repo.py`

**Produces:** `fetch_repo_tarball(owner, name, ref, token=None) -> bytes`, `extract_safely(tar_bytes, dest, max_bytes) -> Path`, `RepoError`.

This handles attacker-controlled input — a repo URL any signed-in user can supply.

Tests pin: extraction refuses an entry whose resolved path escapes the destination (**zip-slip**, both `../` and an absolute path); refuses a symlink entry pointing outside; refuses when the **uncompressed** total exceeds `max_bytes` (a decompression bomb is small on the wire); a normal repo extracts and returns the single top-level directory GitHub tarballs wrap everything in; a 404 raises `RepoError` naming the repo, not a stack trace.

Build the malicious tarballs in the tests with `tarfile` — do not fetch anything from the network in a unit test.

---

### Task 4: Preflight

**Files:** create `apps/api/flashml_cloud_api/preflight.py`, `apps/api/tests/test_preflight.py`

**Produces:** `preflight(config, repo_root, image) -> list[Finding]`, `Finding(level, code, message)` with `level` in `("error", "warning")`.

**This task is the reason the plan exists.** Its job is to convert "your job failed on a stranger's laptop 40 minutes in" into "line 3 of your config is wrong" before anything is queued.

Checks:

| Code | Level | What |
|---|---|---|
| `entrypoint-missing` | error | `entrypoint` does not exist in the repo at that ref |
| `unknown-import` | error | a top-level import is not in the image's package manifest — message names the package **and** which curated image does provide it, if any |
| `network-use` | error | imports `requests`/`urllib`/`httpx`/`socket`/`huggingface_hub`, or shells out to `curl`/`wget`. Volunteer nodes run `--network none`, so this cannot work |
| `no-metrics-json` | warning | the entrypoint never mentions `metrics.json`; without it the task cannot commit |
| `writes-outside-out` | warning | writes to an absolute path or outside `/work/out` |

Every check is a static `ast` walk. **Never import or execute the user's code.** A syntax error in the user's file is itself a finding (`unparseable`), not a crash.

Tests pin each check firing on a crafted fixture repo **and** not firing on a clean one — a check that always fires is as useless as one that never does. Include: a relative import must not be reported as an unknown package; `import os.path` must resolve against `os`; a `try: import torch except ImportError:` guarded import should be a warning rather than an error, since the code clearly anticipates absence.

---

### Task 5: Compile to a JobSpec, and the endpoint

**Files:** create `apps/api/flashml_cloud_api/compile.py`, modify `app.py`, tests `test_compile.py` + `test_jobs_from_repo.py`

**Produces:** `compile_to_jobspec(config, image, code_artifact_uri, job_name) -> dict`.

Compiles to `workload.type: "command"` — `CommandRecipe` (`flashruntime/recipes/command.py`) takes `command` as an **argv list of strings** and `inputs` as a map of `artifact://` URIs, and refuses `isolation.tier != "sandboxed"` for command jobs while rejecting the `allowFallback` waiver. A submitter can never downgrade their own isolation; do not attempt to.

`command` is `["python", "/work/inputs/code/<entrypoint>", *args, *sweep_flags]`. A sweep expands to one task per combination, each with its own flags.

`POST /v1alpha1/jobs/from-repo` requires a Supabase JWT, then: fetch → extract → parse config → preflight → **refuse on any `error` finding, returning all findings at once** so the user fixes everything in one pass rather than one round trip per problem → upload the tarball as an input artifact → compile → submit to the coordinator → write the `jobs` row owned by the caller.

Tests pin: a repo with an error finding is **not** submitted and no `jobs` row is written; warnings alone do not block; the compiled argv is exactly as expected for both the plain and sweep cases; `isolation.tier` is `sandboxed`; and the job row's `owner_id` comes from the JWT, never the body.

---

## Self-Review

**Spec coverage.** §5.3's `flashml.yaml` shape → Task 2; curated images → Task 1; every preflight row in the spec's table → Task 4; the `artifact://` staging contract → Tasks 3 and 5.

**Deliberately not here:** private repos (needs the GitHub OAuth link — public repos need no token at all, so M1 ships public-only and the `token` parameter in Task 3 is the seam); image builds (D5); the web UI that calls this (Plan 5).

**Type consistency.** `CuratedImage.packages` (Task 1) is what `unknown-import` checks against (Task 4). `FlashmlConfig` (Task 2) is consumed by Tasks 4 and 5. `Finding.level` gates submission in Task 5.

**Known risk.** Task 4's `unknown-import` is the check most likely to produce false positives, and a false positive blocks a legitimate submission. Keep the manifests generous, make the message name a curated image that *would* work, and prefer `warning` over `error` wherever the code visibly anticipates the absence.
