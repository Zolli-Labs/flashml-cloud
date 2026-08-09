# Dependency provisioning — the image is the base, extras are a host capability

**Date:** 2026-08-09
**Status:** approved design (brainstormed with the owner).
**Supersedes:** §4.1 of `2026-08-09-trusted-tier-execution-contract-design.md`.
That document's §2 and §3 shipped on 2026-08-09 and are unaffected. Its §4.2,
§4.3 and §5 survive with the amendments recorded here.
**Repos touched:** `flashml` (images, protocol, agent), `flashml-cloud` (compiler).

**Origin.** The superseded §4.1 had the submitter declare their job's Python
dependencies in `flashml.yaml`, resolved at submit time into the task
payload. Two objections from the owner, in order, each of which moved the
design:

1. *"For pytorch on CPU it's a different pytorch than on GPU when running
   the same job."* Correct, and fatal to a single resolved list.
   `torch==2.13.0` from the default index is the CUDA build plus ~2 GB of
   `nvidia-*` wheels; from `--index-url .../whl/cpu` it is a ~200 MB CPU
   build. One requirement string, two artifacts, chosen by where the task
   lands.
2. *"I thought we have the image for every machine… is that because this is
   trusted devices so no docker container support?"* Also correct, and it
   dissolves objection 1 rather than solving it. The curated images already
   encode the accelerator-specific dependency set. Having the submitter
   restate it would create two sources of truth for one environment.
3. *"For extra dependencies, avoiding them on host devices shouldn't be
   right — consider how a platform like RunPod supports this."* Correct
   again. The first draft of this document refused extras outright. RunPod's
   model — pick a base image, then install what else you need in the running
   pod — is the right one for any host that physically can, and only one of
   FlashML's tiers cannot.

---

## 1. Decisions

1. **A curated image's dependency manifest is the BASE.** The compiler
   resolves `image:` to that manifest at submit time. The trusted tier
   installs it into a venv, so an unsandboxed host runs the same library
   versions a container would have given it.
2. **The manifest and the Dockerfile are one file.** Each image directory
   gains a `requirements.txt`; its Dockerfile installs with `pip install -r`
   from that file, and the compiler reads the same file. There is no second
   copy to drift — the rule the whole repo topology already runs on.
3. **`dependencies:` in `flashml.yaml` are EXTRAS on top of the base**, and
   they are the normal case, not an exception. A curated image is a head
   start, never a whitelist.
4. **The ability to install extras is a HOST CAPABILITY**, advertised at
   registration and gating placement — the same shape as `gpus`,
   `local_datasets` and `pools`, all of which already work this way.
   Rejected: refusing extras platform-wide (treats the base as a closed set,
   blocks any job with an ordinary dependency) and allowing extras
   everywhere (impossible — see §3).
5. **The host never invents a requirement.** Everything it installs comes
   from a list resolved at submit time. No host-side `requirements.txt`
   read, no host-side index selection, no fallback rules on the machine.
6. **A volunteer's disk is bounded and visible.** Budget, LRU eviction, a
   refusal before a build that would exhaust the disk, and a `flashnode env`
   subcommand to inspect and purge. Consuming a stranger's disk invisibly is
   not acceptable even when they consented to run the code.

---

## 2. The accelerator problem, and why it stops existing

`images/pytorch-cpu/Dockerfile` and `images/pytorch-cuda/Dockerfile` today:

| | pytorch-cpu | pytorch-cuda |
|---|---|---|
| base | `python:3.11.9-slim` | `nvidia/cuda:12.4.1-runtime-ubuntu22.04` |
| index | `download.pytorch.org/whl/cpu` | `download.pytorch.org/whl/cu124` |
| torch | `2.3.1` | `2.4.1` |
| extras | `numpy==1.26.4` | — |
| `python` on PATH | yes (slim base) | via `python-is-python3` |

Choosing the image **is** choosing the accelerator, and that choice already
exists — it is how the sandboxed tiers have always worked. Once the base
comes from the image's own manifest, a CPU host installs the cpu wheel and a
CUDA host installs the cu124 wheel because they were sent different lists,
not because anyone wrote a rule about accelerators.

Two observations that fall out of the table and matter beyond this design:

- The two images pin **different torch versions**. "Same job, same
  dependencies" is not true across tiers today, and nothing reports it.
  Out of scope here; worth a task.
- The CUDA image installs `python-is-python3` precisely so a bare `python`
  works. That is the same trap that broke the 2026-08-09 pod, where `python`
  was `/usr/bin/python` and `pip` was `/usr/bin/python3.13`. The interpreter
  substitution in the superseded spec's §4.3 remains necessary and unchanged.

The CUDA **version** matrix — cu118 vs cu121 vs cu124 against a host's
driver — is NOT resolvable at submit time and is deferred. `GpuInfo` already
carries `driver_version` and `compute_capability`, collected from the start
"so the data is already flowing when matching rules arrive"
(`flashruntime/protocol/v1alpha1.py`). The hook exists; nothing reads it yet,
and there are no GPU hosts in the fleet.

---

## 3. Extras are a capability, not a policy

A sandboxed container cannot install anything at run time. `docker_runner.py`
runs with `--network none` and a read-only rootfs. This is not a rule that
could be relaxed by decision — it is what the sandbox *is*.

So the tiers divide by physics:

| Tier | Can install extras | Why |
|---|---|---|
| `trusted` | yes | no container, host network, writable disk |
| `subprocess` | yes | same |
| `docker` | no | `--network none`, read-only rootfs |
| `argv` | no | same |

Nodes advertise this as `NodeRegistration.can_install_dependencies: bool`,
defaulting to **False** — fail closed, the same polarity as
`unsandboxed_argv_capable` and for the same reason: a node that predates the
field must not be sent work it cannot do. (Contrast `module_capable`, which
defaults True because it is an availability gate, not a security one.) The
agent sets it from the runner tier, never inferred from anything else.

A task carrying extras places only on hosts advertising it — an eighth
placement gate, fail-closed like the seven before it. Unlike the cached-environment gate rejected in the
superseded spec §1.5, this one has no bootstrap problem: it is a stable
property of the host, true on a node that has never run anything.

**The posture concern resolves itself.** A job with extras is placeable only
on unsandboxed hosts, and unsandboxed placement already requires
`placement.pool` plus `isolation.allowFallback` — `CommandRecipe` refuses
`allowFallback` without a pool. So declaring an extra dependency forces a
decision the submitter already makes explicitly at submit time. It is not a
new hidden mechanism; it is the existing one, reached by a new route. The
console must show it, and the compiler must refuse the combination that
would be silent.

**Consent for the install itself is already given.** `flashnode work
--runner trusted` prints, and requires the operator to have typed a flag to
reach: *pool jobs from your team run UNSANDBOXED on this machine — no
container, no network isolation.* A host that has agreed to execute a
crewmate's arbitrary argv has accepted strictly more than a `pip install`
into a venv, including the `setup.py` execution that install implies.

---

## 4. Where the manifest lives, and how it cannot drift

Each curated image directory gains a `requirements.txt` containing exactly
what its Dockerfile installs, `--index-url` line included — pip and uv both
honour index directives inside a requirements file, so the accelerator's
index needs no schema of its own:

```
# images/pytorch-cpu/requirements.txt
--index-url https://download.pytorch.org/whl/cpu
torch==2.3.1
numpy==1.26.4
```

The Dockerfile installs from it (`COPY requirements.txt` + `pip install
--no-cache-dir -r requirements.txt`). One file, two readers, no possibility
of the manifest describing an image it does not build.

The compiler resolves a **full image reference including tag** to its
manifest. An unknown reference — a custom image, or a tag this build does
not know — yields **no base**, and the job then carries only its declared
extras. If a job names a non-curated image and declares nothing, the trusted
tier cannot reproduce its environment and the job is refused at submit with
that reason. Fail closed: guessing a base for an unknown tag is how the
trusted tier would silently install different versions than the container
has, which is the drift this whole design exists to prevent.

Distribution: the manifests ship as **package data inside `flashruntime`**,
which `flashml-cloud` already pins and imports for the protocol package. The
compiler reads them through that pin — no cross-repo file access, no network
fetch at submit time, and the manifests version with the same commit the
protocol does. The images themselves stay where they are; the build copies
each `requirements.txt` into the package at release, or the package reads
them from the repo tree, whichever the release workflow makes atomic. Pick
one in the plan and state why.

---

## 5. What the payload carries

Unchanged from the superseded §4.2/§4.3 except for its source: the task
payload gets `dependencies: list[str]` — **base manifest lines followed by
the job's extras**, resolved and concatenated at submit time. Downstream
nothing changes: the environment cache keys on the hash of that list, so a
CPU base and a CUDA base are naturally distinct entries, and a job that adds
one extra gets its own entry rather than mutating a shared one.

An empty list means no venv and today's behaviour, which is the path every
currently-deployed job takes.

---

## 6. Deferred

- **Derived images.** Building `base + extras` into a cached image would let
  sandboxed hosts run jobs with extras and close the tier asymmetry
  entirely. It is the correct end state and it needs a build service,
  registry credentials, a cache GC policy and a build queue. When it lands,
  the §3 capability gate opens on its own — no design change.
- **The CUDA version matrix**, per §2.
- **The cpu/cuda torch version divergence** in the curated images.
- **`flashnode env` UX** beyond list/purge — sizes, ages, per-job attribution.

---

## 7. Rejected

- **Submitter declares the full dependency list** (the superseded §4.1).
  Two sources of truth for one environment; a docker host and a pod could
  run different library versions for the same job with nothing detecting it.
- **An image→packages lookup table in the agent.** Correct instinct, wrong
  location: it lies for custom images and drifts from the Dockerfiles. The
  fix was moving the manifest next to the image and generating both from it.
- **Refusing extras platform-wide.** Treats a base image as a closed set.
  RunPod, the platform the owner is actually running on, does the opposite,
  and the objection that motivated the refusal — a silent shift to
  unsandboxed execution — is already handled by the existing
  pool + `allowFallback` requirement.
- **Extras allowed everywhere.** Not a policy choice. `--network none` and a
  read-only rootfs make it impossible in the sandboxed tiers.
