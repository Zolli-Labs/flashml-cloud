# Extra dependencies on sandboxed hosts — research and options

**Date:** 2026-08-09
**Status:** research complete, no decision taken.
**Informs:** `flashml-cloud/docs/superpowers/specs/2026-08-09-dependency-provisioning-design.md` §6
(*Deferred: derived images*).
**Question:** a job needs a Python library the curated image does not contain
(the motivating case is `peft`, for LoRA fine-tuning). A sandboxed Docker host
cannot install it. Today such a job is refused on every Docker host in the
fleet and places only on `--runner trusted`. The fleet is laptops. That is the
whole problem.

---

## 0. Summary

Three options were on the table at the start. Research replaced them with a
different decomposition, because the original three conflated two independent
choices:

1. **Where dependency resolution happens** — the host, or the coordinator.
2. **Where the bytes come from** — PyPI reached by the host, or a wheelhouse
   relayed by the coordinator.

These are separable, and separating them is the main result of this research.
The recommended architecture resolves centrally and treats the byte source as
a swappable detail, which makes the "does a volunteer laptop touch PyPI?"
question a deployment setting rather than an architectural commitment.

The security finding that dominates everything: **a read-only mount is not a
security boundary.** Verified locally — see §2.

---

## 1. What the comparable systems actually do

| System | Where deps are resolved | Where bytes come from | Worker touches index? |
|---|---|---|---|
| Ray `runtime_env` | worker node, per-node agent | PyPI | yes |
| Modal | control plane, builder containers | control plane | no (custom FUSE fs) |
| Beam.cloud | control plane, CPU build machines | control plane | no |
| Replicate/Cog | client `docker build` | registry | no |
| Flyte/Union `ImageSpec` | client or cluster ImageBuilder | registry | no |
| RunPod | user bakes image; or manual pip on a network volume | registry / PyPI | yes, if manual |
| SageMaker | worker, `requirements.txt` at job start | PyPI | yes |
| Databricks | worker, `%pip` per notebook session | PyPI | yes |
| Colab | worker, live VM | PyPI | yes |
| **Metaflow `@pypi`** | **client, ahead of time** | **S3 artifact cache** | **no** |

Two camps, and we belong to neither by default:

- **Bake an image centrally** (Modal, Beam, Cog, Flyte). Both Modal and Beam
  independently converged on content-addressed, FUSE-lazy-loaded filesystems
  with cross-user layer sharing. That is a serious engineering programme.
- **Let the worker talk to PyPI** (Ray, SageMaker, Databricks, Colab, RunPod).
  Ruled out for us by `--network none`, which is what the sandbox *is*.

**Metaflow `@pypi` is the only precedent for the third shape** — resolve and
pre-fetch centrally, ship artifacts to a worker that never contacts PyPI. Its
documented constraint is wheels-only: cross-architecture resolution fails for
anything without a matching wheel.

### Ray is the closest analogue and worth reading in detail

Ray's `RuntimeEnvAgent` builds a venv in a side process, then execs the worker
into it. That is the same separation we would build.

- It **always** passes `--system-site-packages`
  (`python/ray/_private/runtime_env/virtualenv_utils.py`), which is the
  technique for resolving extras against the image's existing torch.
- Its cache eviction is **not** LRU despite intent — a standing
  `TODO(architkulkarni): Evict least recently used URI instead` in
  `uri_cache.py`. Ours already is. We are ahead here.
- Concurrent installs of the same env corrupted each other
  ([#24513](https://github.com/ray-project/ray/issues/24513)) until a per-hash
  `asyncio.Lock` was added. We need the same lock.
- **[#56418](https://github.com/ray-project/ray/issues/56418)**: packages whose
  *build* requires another package already installed — `flash-attn` needing
  torch present — cannot be expressed as one `pip install`. Unfixed;
  maintainer response is "wait for more signals from the community". The
  sanctioned workaround is to pre-build the environment outside the mechanism.
- **[#47432](https://github.com/ray-project/ray/issues/47432)**: a large enough
  runtime_env overflows `execve()` with `E2BIG` and **hangs silently**.
- Ray's own community consensus for network-restricted clusters is *don't use
  `runtime_env`, bake an image*. Nobody runs it air-gapped in production.

---

## 2. VERIFIED: a read-only mount is not a security boundary

Ran locally on 2026-08-09. Built a venv, dropped a `.pth` file into its
`site-packages`, made the entire tree unwritable, ran an ordinary command:

```
job code running
--- did payload fire? ---
payload ran at interpreter start
```

A `.pth` file is executed by the `site` module **at interpreter startup** —
before the job's own code, and whether or not anything imports the package
that shipped it. Read-only prevents *modification*; the payload was already
present when the directory was written.

Consequences:

- `--only-binary=:all:` closes the sdist / `setup.py` / PEP 517 build-time
  execution vector — verified against the PyPA binary-distribution-format
  spec, which has unpack and spread phases and **no** code-execution phase.
- It does **not** close the `.pth` vector, because dropping a `.pth` into
  site-packages is a legitimate wheel payload.
- Two confirmed 2026 campaigns used exactly this: the June PyPI `.pth`
  campaign across 5 bioinformatics packages ("You do not need to import the
  malicious package. Installing it is enough"), and the compromised `litellm`
  1.82.7/1.82.8 releases in March, chosen specifically because a `.pth` fires
  at interpreter start regardless of import.

**Mitigation, required under every option:** before an environment is trusted
by a job, scan it for `.pth`, `sitecustomize.py`, `usercustomize.py` and
`.egg-link`, and refuse or strip. This does not distinguish the options — it
is a precondition for all of them.

---

## 3. VERIFIED: pip cannot be trusted for cross-platform resolution

Run from macOS arm64, targeting linux x86_64 / cp311.

`pip download --platform manylinux2014_x86_64 --python-version 311
--implementation cp --abi cp311` for `peft`'s graph pulled the correct
`torch-2.6.0-...manylinux1_x86_64.whl` and **silently omitted all 13
`nvidia-cu12-*` packages and `triton`**.

Cause: torch's metadata requires those via
`Requires-Dist: ...; platform_system == "Linux" and platform_machine == "x86_64"`,
and pip evaluates environment markers against the **host** (Darwin), not the
requested target. It concludes the markers are false and drops them. The
result is a wheelhouse that looks complete and is not.

Tracked upstream as pypa/pip
[#4304](https://github.com/pypa/pip/issues/4304),
[#6117](https://github.com/pypa/pip/issues/6117),
[#11310](https://github.com/pypa/pip/issues/11310),
[#13262](https://github.com/pypa/pip/issues/13262).

**uv resolves correctly.** `uv pip compile --python-platform linux
--python-version 3.11` evaluated markers for the *target*: it included
`triton` and the CUDA bindings for the default-index torch, and correctly
excluded all of them once torch was constrained to `2.9.1+cpu` with the CPU
extra-index. uv's own docs describe this as "best-effort... may lose fidelity
for complex package and platform combinations" — not a guarantee, but
empirically correct on this graph.

Second verified wrinkle: **one manylinux tag is not enough.**
`numpy==2.4.6` ships only `manylinux_2_27`/`2_28`; `hf-xet` ships only
`manylinux2014`/`2_17`. A single `--platform` value fails one or the other.
The full applicable tag ladder must be passed.

---

## 4. VERIFIED: sizes, and why the constraint file is load-bearing

| Artifact | Size |
|---|---|
| generic PyPI (CUDA) torch wheel alone | 766.7 MB |
| PyTorch CPU-index torch wheel | 184.5 MB |
| `peft` + `accelerate` extras, torch excluded via constraints | **50 MB / 35 wheels** |

50 MB per job over the relay is sane. 766 MB is not, and that is what we get
if resolution ever fails to exclude what the image already has. The technique:

1. At image build time, export the installed set — `pip list --format=freeze`
   — as a constraints file shipped with the image manifest.
2. Resolve extras with `uv pip compile --constraint <that file>
   --extra-index-url <the image's real index> --no-emit-package torch`.
   Pointing at the image's own index matters: it is what makes torch's real,
   OS-specific metadata available to the resolver.
3. Fetch bytes with the fully-pinned output and `--no-deps`. At that point no
   graph resolution remains, so pip's host-marker bug cannot bite.

uv resolves, pip or uv fetches exact pins. Mixing the tools this way is the
load-bearing pattern.

---

## 5. VERIFIED: a built venv is not a portable artifact

`python3 -m venv --system-site-packages /venv` inside the target image
produces a `pyvenv.cfg` with hard absolute paths:

```
home = /usr/local/bin
include-system-site-packages = true
executable = /usr/local/bin/python3.11
```

and console-script shebangs of the literal form `#!/venv/bin/python3`. This
is fine when the builder and the runner are the same image at the same path —
true by construction for a prepare-container design — and is *not* a portable
artifact that can be handed to an arbitrary host.

`conda-pack` and `venv-pack` both document that relocation is unsafe across
machines and paths. PEX and shiv are genuinely relocatable but still need
target-platform wheels underneath, so the §3 caveats still apply.

**Therefore: the coordinator must ship wheels, not a built environment.** The
host still performs the install; it just installs from a local wheelhouse
instead of from the network. This is the single most important structural
consequence in this document — see §7.

This also confirms the standing `CLAUDE.md` warning about absolute paths in
editable installs and console scripts is a general property, not a quirk of
this workspace.

---

## 6. `flash-attn` is unreachable by any option, and must fail fast

Verified via the PyPI JSON API. The LoRA graph — `peft`, `accelerate`,
`transformers`, `tokenizers`, `safetensors`, `huggingface-hub`, `numpy`,
`sentencepiece`, `datasets`, `trl`, `bitsandbytes` — is fully wheel-covered
for linux x86_64 cp311. `trl`, `datasets` and `accelerate` are universal
`py3-none-any`. `bitsandbytes==0.50.0` ships a Linux wheel and *no sdist at
all*.

`flash-attn` ships **zero wheels on PyPI at any recent version** — sdist only.
Building it needs a matching CUDA toolkit, nvcc, and the exact installed torch
build, and takes 30 minutes to 2+ hours on correct hardware. The ecosystem
works around this with per-(CUDA, torch, cxx11abi, cpython) prebuilt wheels
hosted off PyPI.

`--only-binary=:all:` fails outright rather than attempting a build, which is
the correct posture. It means flash-attn needs a curated exception table per
base image, and a job naming it on an uncurated combination must be refused at
submit with that reason — not discovered after a two-hour build on a
volunteer's laptop. This is the same class as Ray's #56418 and gets the same
answer.

---

## 7. The result: resolution and byte-source are separable

§5 forces the coordinator to ship *wheels*, not environments. Which means the
host runs an install step under every option. Which means:

> **Option D is option A with the network removed from the prepare container
> and `--find-links /wheels --no-index` added.**

The same machinery serves both. That collapses the decision from "pick an
architecture" to "pick a byte source", and the byte source can change later
without redesign.

Three configurations of one mechanism:

| | Resolution | Bytes | Host touches PyPI | Coordinator stores wheels |
|---|---|---|---|---|
| **A** | host | PyPI | yes | no |
| **A′** | coordinator (uv, hashed lock) | PyPI, exact pins | yes | no |
| **D** | coordinator (uv, hashed lock) | relayed wheelhouse | **no** | yes |

`A′ → D` is a change of where `--find-links` points.

**A is not recommended**, despite being the smallest change: it puts
resolution on a laptop, and §3 shows resolution is precisely the step that
fails silently and produces a wrong answer that looks right. Resolution
belongs in one place, on Linux, with uv, once.

**A′ is the recommended first step.** The coordinator produces a hash-pinned
lock; the host installs with `--require-hashes --no-deps --only-binary=:all:`
from exact pins. This gets correct resolution, determinism, and hash
verification without any wheel storage or relay bandwidth. The host still
reaches PyPI.

**D is the end state** if we decide volunteer laptops must never contact a
third-party index. It costs coordinator storage, relay bandwidth (~50 MB per
distinct extras set), and a GC policy on that store.

---

## 8. Trust: what the survey settles, regardless of option

Every security-conscious system surveyed refuses to trust a *name*.

- **BOINC** binds trust to an RSA keypair generated at project creation,
  delivered to the volunteer at attach time, with the private half kept on a
  machine documented as never network-connected. Verification is local; no
  live check at execution. Nothing published later can claim that identity.
  Its VM guidance is "task-focused" VMs with guest networking disabled as the
  default, with network-fetching VMs an explicitly discouraged exception.
- **Nix / Guix** bind trust to a hash of the entire input closure; the
  `<name>` in a store path is cosmetic. Signing happens in a root-only daemon
  the build never touches, and a bad signature causes a local rebuild rather
  than accept-with-warning.
- **Bacalhau** has literally our three-way network model (`None` / `HTTP` with
  a `--domain` allowlist / `Full`). Its documented answer to "my job needs a
  library" is: bake it into the image.
- **Golem** permits outbound network but requires *dual* opt-in — the
  submitter declares exact domains in a computation manifest, and the provider
  must independently have been configured to offer them.

### The cautionary case is our exact shape done wrong

**GitHub Actions cache poisoning** (Adnan Khan, 2024): entries addressed by a
*caller-supplied* key with no signing and no verification on restore. A
low-trust fork PR steals a cache token, floods the 10 GB quota to force LRU
eviction of the legitimate entry, uploads a poisoned archive under the same
key, and a later high-trust workflow restores it. Restore-key prefix fallback
keeps the poisoned entry alive for up to ~7 days.

That is our cache design with the integrity check removed — and the attack
runs *through the eviction policy*, which we also have.

### Consequences

1. **Option B as originally scoped is not defensible.** Publishing
   user-dependency-driven images under `ghcr.io/zolli-labs/flashml-` — a
   prefix `flashnode/executor/images.py:67` trusts by default — is trust-by-
   name. It is precisely what BOINC's offline key and Nix's daemon-only
   signing exist to prevent, and what Docker Hub's `library/` separation and
   conda-forge's staged-recipes review gate encode as convention. If B is ever
   built, derived images need a separate namespace (ideally a separate
   account, so a compromised push credential cannot touch the curated images'
   reputation), a different signing key, and hosts verifying a **digest the
   control plane computed** rather than a tag.
2. **Whatever we ship, the host verifies content, not location.** A hash the
   coordinator computed, checked before use. This is free under A′ and D,
   since the coordinator already produces the lock.
3. **Golem's dual opt-in is the right shape for A/A′'s network question.** The
   submitter declares that the job needs extras; the host operator must have
   separately agreed to allow the fetch. Not a platform-level decision made on
   the volunteer's behalf.

On the social question — whether it is acceptable to cause a volunteer's
machine to download from a third-party index — no documented incident or
precedent was found. BOINC documents resource *controls* (disk caps, network
timing preferences, no computation while the keyboard is active) and an
explicit privacy goal, but no consent protocol for third-party fetches. Treat
this as an open norm, not a settled one, and build the controls rather than
citing precedent.

---

## 9. What must be true under every option

Independent of A / A′ / D:

- Scan the built environment for `.pth`, `sitecustomize.py`,
  `usercustomize.py`, `.egg-link` before a job trusts it (§2).
- `--only-binary=:all:` and `--require-hashes`.
- A per-cache-key lock so two jobs needing the same uncached environment do
  not build concurrently (Ray #24513). Our write-then-rename covers corruption
  but not duplicated work.
- **The docker-tier cache key must include the image reference.** Today
  `environment_key()` hashes the dependency list only — correct for the
  trusted tier, where the venv is self-contained. For the docker tier the venv
  holds extras *only* and sits on the image's packages, so `peft` on
  `pytorch-cpu` (torch 2.3.1) and `peft` on `pytorch-cuda` (torch 2.4.1) would
  collide on one entry and hand a job the wrong torch.
- Apply the same cpu / pids / memory / wall-clock caps to any prepare step as
  to the job sandbox; a malicious install can mine during its window.
- Confirm fleet runc is patched past CVE-2019-5736 and CVE-2024-21626
  ("Leaky Vessels", affects runc ≤ 1.1.11).
- Refuse at submit, naming the reason, for dependency sets that cannot be
  resolved to wheels (§6).

## 10. Already built, and reusable

`flashnode/executor/environments.py` (386 lines) is a content-hash-keyed
environment store with LRU eviction, a disk budget
(`FLASHNODE_ENV_BUDGET_GB`, default 8), refusal before a build that would
exhaust the disk, write-then-rename so a partial venv is never published,
stale-build sweeping, and `flashnode env` list/purge. Everything the design's
§1.6 asked for exists. The work is teaching it to build inside a container and
to key on the image reference as well as the dependency list.

---

## 11. Open questions for the owner

1. Must volunteer laptops never contact PyPI? Yes → D. No → A′ first, D later
   if the answer changes; the step between them is small by construction.
2. Does the host operator opt in to the fetch separately from the submitter
   declaring extras (the Golem shape), or is it implied by running the agent?
3. Is `flash-attn`-class (compiled CUDA kernel, no wheels) in scope at all, or
   permanently refused with a named reason?

## 12. Verification gaps

- The end-to-end Docker test of a read-only venv bind-mount under
  `--read-only` + `--network none` was **not completed** — the host disk hit
  100% mid-test. Path divergence cannot occur when builder and runner share
  one image, so no surprise is expected, but this has not been proven and
  should be run once before implementation.
- Whether Modal's `pip_install` extends or isolates from the base image's
  site-packages — not determinable from public docs.
- Databricks' internal isolation primitive for notebook-scoped libraries —
  not published.
- BOINC `docker_wrapper`'s registry-pull path and default container network
  policy — not found in primary docs.
