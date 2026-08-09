# Dependency Declaration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every task payload carry the exact dependency list its job
needs — the curated image's own manifest as the base, plus whatever extras
the job declared — resolved at submit time.

**Architecture:** Each curated image directory gains a `requirements.txt`
that its Dockerfile installs from, so the manifest and the image cannot
disagree. Those files ship as `flashruntime` package data, which
`flashml-cloud` already pins. The compiler resolves `image:` to its manifest,
appends the job's declared extras, and puts the result in
`workload.parameters`; `CommandRecipe` forwards it into every task payload.

**Tech Stack:** Docker, GitHub Actions, Python 3.11+, pytest, pydantic.

**Spec:** `docs/superpowers/specs/2026-08-09-dependency-provisioning-design.md`
§1, §2, §4 and §5. This plan is the **declaration half** only.

**The execution half is a separate plan** —
`2026-08-09-dependency-execution.md`, not yet written: the
`can_install_dependencies` capability and its placement gate,
`EnvironmentCache`, runner integration, the attributable build failure with
per-job cooldown, and `flashnode env list/purge`. Nothing in THIS plan
changes host behaviour: agents that ignore the new payload key behave
exactly as they do today.

## Two repos

| Tasks | Repo |
|---|---|
| 1, 2, 3 | `flashml` (public) — `~/Work/Zolli-Labs/flashml` |
| 4 | `flashml-cloud` (private) — `~/Work/Zolli-Labs/flashml-cloud` |

Tasks 1-3 must merge and `flashruntime` must be re-pinned before Task 4 can
read a manifest. Hard rule 2 — schemas a FlashNode must understand live
upstream — is why this order is not negotiable.

**Test commands.** `flashml`: `python -m pytest tests/ -v` from
`flashruntime/`. `flashml-cloud`: `pytest` from `apps/api/`. Record each
baseline before starting; any drop is a regression.

## Global Constraints

- **`IMAGE_TAG` is immutable and global.** `.github/workflows/images.yml`
  sets `IMAGE_TAG: "2026.08.2"` for every image and the workflow already
  refuses to publish if it disagrees with `flashml_cloud_api/images.py`.
  Never repush an existing tag; bump it for any image content change,
  including a manifest edit.
- **One file, two readers.** A curated image's `requirements.txt` is
  installed by its Dockerfile AND read by the compiler. Never introduce a
  second list describing the same image.
- **Manifest lookup keys on the full image reference including tag.** An
  unknown reference yields NO base — never a guessed one. Guessing is how a
  venv would silently hold different versions than the container.
- **Resolution happens at submit time, in the compiler.** Nothing in this
  plan may make a host read a manifest, a requirements file, or an index.
- **An empty resolved list leaves the payload key absent.** Every job
  deployed today takes that path and must be byte-identical.
- The `pytorch-cuda` image is multi-gigabyte and its CI leg already runs
  several times longer than the others. Do not add work to it.

---

### Task 1: Each image carries its own manifest

**Repo:** `flashml`. **Files:**
- Create: `images/pytorch-cpu/requirements.txt`
- Create: `images/pytorch-cuda/requirements.txt`
- Create: `images/sklearn/requirements.txt`
- Create: `images/python-slim/requirements.txt` (may be empty — see below)
- Modify: each of those directories' `Dockerfile`
- Create: `tests/test_image_manifests.py` at the repo root, or the nearest
  existing repo-level test module

**Interfaces:**
- Consumes: nothing.
- Produces: `images/<alias>/requirements.txt`, one per curated image, in pip
  requirements-file syntax including any `--index-url` line.

**Read first.** `images/pytorch-cpu/Dockerfile` installs inline:

```dockerfile
RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch==2.3.1 \
    && pip install --no-cache-dir numpy==1.26.4 \
    && groupadd ...
```

`images/pytorch-cuda/Dockerfile` does the same with
`--index-url .../whl/cu124` and `torch==2.4.1`. Those pins are the source of
truth for what the image contains, and they move into the manifest verbatim.
Note the two images pin **different torch versions** — that is existing
behaviour, and this task must preserve it exactly, not harmonise it. A
version change here changes what every sandboxed job runs.

- [ ] **Step 1: Write the failing test**

Create `tests/test_image_manifests.py`:

```python
"""A curated image's requirements.txt is what its Dockerfile installs.

The compiler reads these files to tell an unsandboxed host what to install,
so a manifest that disagrees with its image means a pod runs different
library versions than a container does — silently, for the same job.
"""

from __future__ import annotations

from pathlib import Path

import pytest

IMAGES = Path(__file__).resolve().parents[1] / "images"
ALIASES = sorted(p.name for p in IMAGES.iterdir() if (p / "Dockerfile").is_file())


def test_every_curated_image_has_a_manifest():
    missing = [a for a in ALIASES if not (IMAGES / a / "requirements.txt").is_file()]
    assert missing == [], f"images without a requirements.txt: {missing}"


@pytest.mark.parametrize("alias", ALIASES)
def test_the_dockerfile_installs_from_the_manifest(alias):
    """Not 'a pip install exists' — the ONE that installs the manifest. An
    inline `pip install torch==...` beside it would be a second source of
    truth, which is the whole failure this design removes."""
    dockerfile = (IMAGES / alias / "Dockerfile").read_text()
    assert "requirements.txt" in dockerfile
    for line in dockerfile.splitlines():
        stripped = line.strip()
        if stripped.startswith("RUN pip install") or " pip install" in stripped:
            assert "-r requirements.txt" in dockerfile, (
                f"{alias}: pip install present that does not read the manifest"
            )


@pytest.mark.parametrize("alias", ALIASES)
def test_manifest_lines_are_requirements_syntax(alias):
    """Only requirement lines, comments, and index directives. A shell
    fragment here would install silently wrong or not at all."""
    for raw in (IMAGES / alias / "requirements.txt").read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        assert not line.endswith("\\"), f"{alias}: line continuation {line!r}"
        assert "&&" not in line, f"{alias}: shell fragment {line!r}"


def test_pytorch_images_keep_their_distinct_indexes():
    """The cpu/cuda split is the accelerator choice. Collapsing these two
    onto one index is what this whole design exists to keep from happening
    by accident."""
    cpu = (IMAGES / "pytorch-cpu" / "requirements.txt").read_text()
    cuda = (IMAGES / "pytorch-cuda" / "requirements.txt").read_text()
    assert "download.pytorch.org/whl/cpu" in cpu
    assert "download.pytorch.org/whl/cu124" in cuda
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_image_manifests.py -v` from the repo root
(use `flashruntime/.venv` or `flashnode/.venv`; this test imports nothing
from either package).
Expected: FAIL — no `requirements.txt` exists in any image directory.

- [ ] **Step 3: Write the implementation**

Create each manifest from what its Dockerfile installs today, verbatim.
`images/pytorch-cpu/requirements.txt`:

```
# Installed by this directory's Dockerfile AND read by the flashml-cloud
# compiler, which sends it to unsandboxed hosts so they reproduce this
# image's environment without running it. One file, two readers: a second
# copy of these pins is how a pod ends up on different versions than a
# container, for the same job, with nothing detecting it.
--index-url https://download.pytorch.org/whl/cpu
torch==2.3.1
numpy==1.26.4
```

`images/pytorch-cuda/requirements.txt` mirrors it with
`--index-url https://download.pytorch.org/whl/cu124` and `torch==2.4.1`.
Read each Dockerfile for `sklearn` and `python-slim` and transcribe exactly
what they install; if `python-slim` installs nothing, create the file with
only the header comment so the "every image has a manifest" rule holds
without inventing a dependency.

Then change each Dockerfile's install to read it:

```dockerfile
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && rm requirements.txt \
    && groupadd ...
```

Copy to the build root, not `/tmp` — the test above asserts the literal
substring `-r requirements.txt`, which a `/tmp/`-prefixed path does not
contain. Same effect, and the assertion stays simple enough to be obviously
right.

Keep every other line — the uid/gid arguments, the `/work` chown, the user
creation — exactly as it is. Docker's build context is `images/<alias>`
(`images.yml`: `context: images/${{ matrix.alias }}`), so `COPY
requirements.txt` resolves with no workflow change.

- [ ] **Step 4: Run the test to verify it passes**

Run the new test module, then the full `flashruntime` suite to confirm
nothing else moved.

- [ ] **Step 5: Verify an image still builds**

```bash
docker build -t flashml-manifest-check images/pytorch-cpu
docker run --rm flashml-manifest-check python -c "import torch, numpy; print(torch.__version__, numpy.__version__)"
```

Expected: `2.3.1+cpu 1.26.4` — the versions the Dockerfile pinned before this
task. Do NOT build `pytorch-cuda` locally; it is multi-gigabyte and CI covers
it.

- [ ] **Step 6: Commit**

```bash
git add images/ tests/test_image_manifests.py
git commit -m "refactor(images): each curated image installs from its own manifest

One requirements.txt per image, installed by its Dockerfile and readable by
the compiler, so an unsandboxed host can reproduce the image's environment
without a second copy of the pins."
```

---

### Task 2: Manifests ship inside `flashruntime`

**Repo:** `flashml`. **Files:**
- Modify: `flashruntime/pyproject.toml` (`[tool.setuptools.package-data]`)
- Create: `flashruntime/scripts/collect_image_manifests.py`
- Create: `flashruntime/flashruntime/images/__init__.py`
- Modify: `.github/workflows/release-flashruntime.yml`
- Create: `flashruntime/tests/test_image_manifest_lookup.py`

**Interfaces:**
- Consumes: `images/<alias>/requirements.txt` from Task 1.
- Produces: `flashruntime.images.manifest_for(image_reference: str) -> list[str] | None`
  — the manifest's lines for a known reference, `None` for anything else.

**Follow the `viewer/_docs` precedent.** `pyproject.toml` already documents a
generated artifact shipped as package data:

> viewer/_docs/** is the built docs site so the packaged viewer can serve
> /docs offline — build it (scripts/build_docs.py) BEFORE `python -m build`,
> or the wheel builds fine but ships docs-less (the release workflow does
> this in order and asserts the wheel contains viewer/_docs/index.html).

Manifests get the same treatment, including the assertion. A wheel that
builds fine and ships manifest-less is the failure mode to design against:
the compiler would then resolve every image to no base and every trusted
host would install nothing.

- [ ] **Step 1: Write the failing test**

Create `flashruntime/tests/test_image_manifest_lookup.py`:

```python
from flashruntime.images import manifest_for


def test_a_known_reference_resolves_to_its_manifest():
    lines = manifest_for("ghcr.io/zolli-labs/flashml-pytorch-cpu:2026.08.2")
    assert lines is not None
    assert "torch==2.3.1" in lines
    assert "--index-url https://download.pytorch.org/whl/cpu" in lines


def test_cpu_and_cuda_resolve_to_different_manifests():
    cpu = manifest_for("ghcr.io/zolli-labs/flashml-pytorch-cpu:2026.08.2")
    cuda = manifest_for("ghcr.io/zolli-labs/flashml-pytorch-cuda:2026.08.2")
    assert cpu != cuda


def test_an_unknown_tag_resolves_to_nothing():
    """Fail closed. Guessing a base for an unpublished tag is how a venv
    ends up holding different versions than the container it stands in for."""
    assert manifest_for("ghcr.io/zolli-labs/flashml-pytorch-cpu:1999.01.1") is None


def test_a_custom_image_resolves_to_nothing():
    assert manifest_for("docker.io/someone/their-own-image:v1") is None


def test_comments_and_blank_lines_are_dropped():
    lines = manifest_for("ghcr.io/zolli-labs/flashml-pytorch-cpu:2026.08.2")
    assert all(line.strip() and not line.startswith("#") for line in lines)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_image_manifest_lookup.py -v` from `flashruntime/`
Expected: FAIL — `ModuleNotFoundError: flashruntime.images`

- [ ] **Step 3: Write the implementation**

`flashruntime/scripts/collect_image_manifests.py` copies
`images/*/requirements.txt` from the repo root into
`flashruntime/flashruntime/images/<alias>.txt`, mirroring
`scripts/build_docs.py`'s role. It must fail loudly if the `images/`
directory is missing rather than producing an empty package.

`flashruntime/flashruntime/images/__init__.py`:

```python
"""Curated image manifests, as package data.

Copied from `images/<alias>/requirements.txt` at release time by
`scripts/collect_image_manifests.py` — the same pattern `viewer/_docs`
follows, and for the same reason: the source of truth stays beside the
thing it describes, and the package ships a copy so consumers do not need
the repo.

`flashml-cloud` reads this through the flashruntime pin it already resolves,
which is what keeps a manifest and its image on the same commit.
"""

from __future__ import annotations

from importlib import resources

_PREFIX = "ghcr.io/zolli-labs/flashml-"


def manifest_for(image_reference: str) -> list[str] | None:
    """The requirement lines for a curated image, or None.

    Keyed on the FULL reference including tag. An unknown tag returns None
    rather than the newest manifest: a job pinned to an older image must
    never be told to install what a newer one contains.
    """
    if not image_reference.startswith(_PREFIX):
        return None
    alias, sep, tag = image_reference[len(_PREFIX):].partition(":")
    if not sep or not alias or "/" in alias:
        return None
    if tag != _shipped_tag():
        return None
    try:
        raw = resources.files(__package__).joinpath(f"{alias}.txt").read_text()
    except (FileNotFoundError, OSError):
        return None
    return [
        line.strip()
        for line in raw.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _shipped_tag() -> str:
    """The IMAGE_TAG these manifests were collected from.

    Written by the collector at build time, never maintained by hand: the
    tag already lives in `.github/workflows/images.yml` and in
    flashml-cloud's `images.py`, and a third hand-edited copy is one more
    place for the three to disagree.
    """
    return resources.files(__package__).joinpath("TAG").read_text().strip()
```

The collector writes `TAG` beside the `<alias>.txt` files, parsing
`IMAGE_TAG` out of `.github/workflows/images.yml` rather than accepting it as
an argument — read from the one place that already owns it, never retyped.

Add to `pyproject.toml`:

```toml
flashruntime = ["py.typed", "viewer/_docs/**", "images/*.txt", "images/TAG"]
```

`images/TAG` must be listed explicitly — it has no extension, so `*.txt`
does not match it, and a wheel shipping the alias files without it makes
`_shipped_tag()` raise an uncaught `FileNotFoundError` on every lookup
rather than resolving or returning `None`. Build the wheel and list its
contents to confirm both are present; the release workflow asserts the same.

In `.github/workflows/release-flashruntime.yml`, run the collector **before**
`python -m build`, beside the docs build, and assert the wheel contains
`flashruntime/images/pytorch-cpu.txt` exactly as it already asserts
`viewer/_docs/index.html`.

- [ ] **Step 4: Run the test to verify it passes**

Run the collector, then the new module, then the full `flashruntime` suite.

- [ ] **Step 5: Commit**

```bash
git add flashruntime/ .github/workflows/release-flashruntime.yml
git commit -m "feat(flashruntime): ship curated image manifests as package data"
```

---

### Task 3: The payload carries the resolved list

**Repo:** `flashml`. **Files:**
- Modify: `flashruntime/flashruntime/recipes/command.py` — `validate_params`, `expand`
- Test: the existing command-recipe test module

**Interfaces:**
- Consumes: nothing from Tasks 1-2 (this is the wire format alone).
- Produces: task payload key `dependencies: list[str]`, present only when
  the resolved list is non-empty.

**Read this before writing code.** `command.py`'s `local_inputs` forward
carries the design warning for this task:

> Dropping it does NOT fail closed. The gate sees a task requiring nothing,
> places it on any node, and flashnode mounts nothing — so the task runs
> without the data it asked for. Both ends of this hop have tests that pass
> while it is broken, because each constructs the payload directly.

`dependencies` fails identically: drop the forward and every host installs
nothing, the workload dies on `ModuleNotFoundError`, and both sides' unit
tests still pass. Step 1 therefore includes an end-to-end forward test.

- [ ] **Step 1: Write the failing tests**

```python
def test_dependencies_reach_every_task_payload():
    """The forward, end to end from JobSpec to TaskSpec — see the
    local_inputs comment in command.py for why a shape test is not enough."""
    spec = _job_spec(parameters={
        "command": ["python", "/work/inputs/code/train.py"],
        "task_params": [{"shard": "0"}, {"shard": "1"}],
        "dependencies": ["--index-url https://download.pytorch.org/whl/cpu",
                         "torch==2.3.1"],
    })
    tasks = CommandRecipe().expand("job-1", spec)
    assert len(tasks) == 2
    for t in tasks:
        assert t.payload["dependencies"] == [
            "--index-url https://download.pytorch.org/whl/cpu", "torch==2.3.1"
        ]


def test_absent_dependencies_leave_the_key_absent():
    """Absent stays absent, never an empty list — the rule unpack_inputs
    follows, so the no-dependency path keeps being exercised."""
    spec = _job_spec(parameters={"command": ["python", "x.py"]})
    assert "dependencies" not in CommandRecipe().expand("job-1", spec)[0].payload


def test_index_directives_survive_the_forward():
    """A manifest's --index-url line is what makes a CPU wheel a CPU wheel.
    A forward that filtered it would install the CUDA build instead."""
    spec = _job_spec(parameters={
        "command": ["python", "x.py"],
        "dependencies": ["--index-url https://download.pytorch.org/whl/cpu",
                         "torch==2.3.1"],
    })
    payload = CommandRecipe().expand("job-1", spec)[0].payload
    assert payload["dependencies"][0].startswith("--index-url")


def test_dependencies_must_be_a_list_of_strings():
    problems = CommandRecipe().validate_params(
        {"command": ["python", "x.py"], "dependencies": "torch==2.3.1"}
    )
    assert any("dependencies" in p for p in problems)


def test_dependencies_rejects_non_string_members():
    problems = CommandRecipe().validate_params(
        {"command": ["python", "x.py"], "dependencies": ["torch", 3]}
    )
    assert any("dependencies" in p for p in problems)
```

Find the existing command-recipe test module and its `_job_spec` helper and
reuse them; do not invent a second builder.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/ -k dependencies -v` from `flashruntime/`
Expected: FAIL — `KeyError: 'dependencies'`, empty problem lists.

- [ ] **Step 3: Write the implementation**

In `validate_params`, beside the `command` and `inputs` checks:

```python
        deps = params.get("dependencies")
        if deps is not None:
            if not isinstance(deps, list) or isinstance(deps, (str, bytes)):
                problems.append(
                    "'dependencies' must be a list of requirements-file lines "
                    "(a list, not a single string)"
                )
            elif not all(isinstance(d, str) for d in deps):
                problems.append("'dependencies' must be a list of strings")
```

In `expand`, beside the `unpack_inputs` and `local_inputs` forwards:

```python
            if p.get("dependencies"):
                # Requirements-FILE lines, not bare requirement strings: an
                # `--index-url` line is what makes a CPU torch wheel a CPU
                # wheel, and pip and uv both honour index directives inside a
                # requirements file. Filtering them here would install the
                # CUDA build on a laptop.
                #
                # Resolved by the flashml-cloud compiler at submit time from
                # the image's own manifest plus the job's extras — never on
                # the host. Same failure shape as `local_inputs` above: drop
                # this line and every host installs nothing, the workload
                # dies on ModuleNotFoundError, and both ends' unit tests
                # still pass.
                payload["dependencies"] = list(p["dependencies"])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run the full `flashruntime` suite. Expected: PASS, no drop from baseline.

- [ ] **Step 5: Commit**

```bash
git add flashruntime/recipes/command.py flashruntime/tests/
git commit -m "feat(flashruntime): tasks carry their resolved dependency list"
```

---

### Task 4: The compiler resolves base + extras

**Repo:** `flashml-cloud`. **Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/flashml_yaml.py`
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/compile.py`
- Test: the existing `flashml_yaml` and `compile` test modules

**Prerequisite:** Tasks 1-3 merged and the `flashruntime` pin bumped in
`apps/api/pyproject.toml`, `render.yaml` and the `Makefile`'s `FLASHML_PIN`
— **all three, or the API and the coordinator run different code.**

**Interfaces:**
- Consumes: `flashruntime.images.manifest_for` (Task 2), the payload key
  (Task 3).
- Produces: `FlashmlConfig.dependencies: list[str]` and
  `workload.parameters["dependencies"]` = base manifest lines + extras.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_curated_image_supplies_the_base():
    config = _config(image="ghcr.io/zolli-labs/flashml-pytorch-cpu:2026.08.2")
    params = _compile(config)
    assert "torch==2.3.1" in params["dependencies"]
    assert "--index-url https://download.pytorch.org/whl/cpu" in params["dependencies"]


def test_extras_are_appended_after_the_base():
    """Order matters: the base's --index-url applies to what follows, and an
    extra must not be resolved before the base pins land."""
    config = _config(
        image="ghcr.io/zolli-labs/flashml-pytorch-cpu:2026.08.2",
        dependencies=["transformers==4.44.0"],
    )
    deps = _compile(config)["dependencies"]
    assert deps[-1] == "transformers==4.44.0"
    assert deps.index("torch==2.3.1") < deps.index("transformers==4.44.0")


def test_the_cuda_image_supplies_a_different_base():
    """Choosing the image IS choosing the accelerator — the compiler does
    not know what a GPU is and must not need to."""
    deps = _compile(_config(
        image="ghcr.io/zolli-labs/flashml-pytorch-cuda:2026.08.2"))["dependencies"]
    assert "--index-url https://download.pytorch.org/whl/cu124" in deps
    assert "torch==2.4.1" in deps


def test_a_custom_image_with_no_extras_is_refused():
    """An unsandboxed host cannot reproduce an environment nobody described."""
    with pytest.raises(CompileError, match="dependencies"):
        _compile(_config(image="docker.io/someone/theirs:v1"))


def test_a_custom_image_with_extras_carries_only_the_extras():
    deps = _compile(_config(image="docker.io/someone/theirs:v1",
                            dependencies=["numpy==1.26.4"]))["dependencies"]
    assert deps == ["numpy==1.26.4"]


def test_dependencies_must_be_a_list_not_a_string():
    with pytest.raises(ConfigError, match="dependencies"):
        parse_flashml_yaml(
            "version: 1\nname: j\nimage: img:1\nentrypoint: train.py\n"
            "dependencies: torch==2.3.1\n"
        )
```

Adapt helper names to whatever the existing tests use. Do not invent new
public entry points.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/ -k dependencies -v` from `apps/api/`
Expected: FAIL — no key produced, no `ConfigError` raised.

- [ ] **Step 3: Write the implementation**

In `flashml_yaml.py`, add `_validate_dependencies` beside `_validate_args`,
following its shape and its reasoning verbatim (a list, never a single
string — `dependencies: torch==2.3.1` is a plausible typo that would
otherwise read as a list of characters). Add
`dependencies: list[str] = []` to `FlashmlConfig` and wire it in.

In `compile.py`, add beside `_local_inputs` (whose comment about
`CommandRecipe` not forwarding unrecognised parameters applies here too, so
the key name must match what Task 3 reads):

```python
DEPENDENCIES_PARAM = "dependencies"


def _dependencies(config: FlashmlConfig, parameters: dict[str, Any]) -> None:
    """Base manifest from the image, then the job's extras.

    The base comes from the image's OWN requirements.txt, shipped in
    flashruntime — so an unsandboxed host installs what the container would
    have contained, rather than a second list someone maintained by hand.
    Extras follow, so the base's --index-url is already in effect.

    A custom image with no extras is refused HERE, at submit time, naming
    the reason. The alternative is a trusted host claiming the task, failing
    to reproduce anything, and reporting it three hops later.
    """
    base = manifest_for(config.image)
    extras = list(config.dependencies)
    # `is None`, NOT truthiness. `manifest_for` returns [] for a CURATED
    # image that genuinely installs nothing (python-slim) and None for one
    # it does not recognise. `if not base` would conflate them and refuse
    # every python-slim job at submit time.
    if base is None and not extras:
        raise CompileError(
            f"image {config.image!r} is not a curated FlashML image and the "
            f"job declares no 'dependencies:' — an unsandboxed host cannot "
            f"reproduce its environment. Either use a curated image or list "
            f"what the job needs."
        )
    resolved = (base or []) + extras
    if resolved:
        parameters[DEPENDENCIES_PARAM] = resolved
```

Call it from the same place `_local_inputs` is called.

**Add a test that every image reference this API can emit resolves.** The
wheel freezes `IMAGE_TAG` at build time, so bumping the images without
cutting a `flashruntime` release and re-pinning makes every curated
reference resolve to `None` — the compiler emits no base, trusted hosts
install nothing, and nothing fails loudly. Put it beside the existing
`test_every_curated_image_is_anonymously_pullable`:

```python
def test_every_image_this_api_can_emit_has_a_manifest():
    """Version skew between the images release and the flashruntime pin is
    silent otherwise: manifest_for returns None, the base is empty, and
    hosts install nothing while every other test stays green."""
    from flashruntime.images import manifest_for

    for ref in every_curated_image_reference():
        assert manifest_for(ref) is not None, (
            f"{ref} has no manifest in the pinned flashruntime — bump the pin"
        )
```

Also add `test_a_curated_image_with_no_dependencies_is_not_refused`, using
`python-slim`, which is the case the `is None` check above exists to protect.

- [ ] **Step 4: Run the tests to verify they pass**

Run the full `apps/api` suite. Expected: PASS, no drop from baseline.

- [ ] **Step 5: Commit**

```bash
git add flashml-cloud/apps/api/
git commit -m "feat(api): resolve a job's dependencies from its image manifest plus extras"
```

---

## Manual verification

After Task 4, with the pin bumped:

1. Submit the federated example unchanged. Inspect the coordinator:
   `curl <coordinator>/v1alpha1/jobs/<id>` — the workload parameters must
   carry the pytorch-cpu manifest lines, and every task payload must carry
   the same list.
2. Add `dependencies: ["transformers==4.44.0"]` to its `flashml.yaml` and
   resubmit. The list must end with that line, after the torch pins.
3. Point a job at a non-curated image with no `dependencies:`. Submit must
   fail with the message naming the image — not accept and fail later.
4. Confirm nothing changed for hosts: agents ignore the new key, and the
   same jobs still complete on your Mac exactly as before.

Step 4 is the one that matters. This plan must be invisible at run time.

## Out of scope

Everything that makes a host act on the payload is the execution plan:
the `can_install_dependencies` capability and its placement gate,
`EnvironmentCache`, runner integration, the attributable build failure and
per-job cooldown, and `flashnode env list/purge`. Also deferred, per spec §6:
derived images, the CUDA version matrix, and the cpu/cuda torch version
divergence.
