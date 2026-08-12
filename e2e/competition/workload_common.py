"""Shared, dependency-free machinery for the competition demo workloads.

Imported by both `train_checkpointed.py` and `evaluate_model.py`, which sit
beside it at the repo root — preflight's `_is_repo_local_module` resolves a
repo-local import against exactly that layout, so this file costs neither
workload its `image: python-slim` classification.

Three properties everything here is built around, because the demo rests on
them rather than on the ML being interesting:

**Determinism is structural, not a convention.** No `random`, no `numpy`, no
wall clock, no dict iteration order, no set iteration order, and no
data-dependent branch that could reorder a float addition. The PRNG is a
splitmix64 written out in integer arithmetic rather than `random.Random`,
whose stream the standard library explicitly does not promise across
versions. Same seed, same bytes — on this laptop, on a volunteer's, in a
container, next year.

**The model's identity is the sha256 of `model.json`'s bytes**, never a hash
over a re-serialised structure. A byte hash is something two processes that
share no code can agree on: the trainer hashes what it wrote, the evaluator
hashes what it downloaded, and if those differ the evaluation is refused. It
is the whole evidential tie between "a model was trained" and "this model was
evaluated", so it is deliberately the cheapest possible thing to verify.

**A half-written checkpoint is the failure mode this exists to survive.**
`atomic_write_json` never lets a reader see a partial file (temp + fsync +
rename + directory fsync), and `load_checkpoint` verifies a digest carried
inside the document, so a file that survives truncation *and* stays parseable
is still refused rather than resumed from.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

#: Bumped whenever the synthesised dataset changes in any way. A model
#: carries the value it was trained under and the evaluator refuses a value
#: it does not recognise: an evaluator scoring a model against a *different*
#: dataset than it was trained on would report a plausible, wrong number.
GENERATOR_SCHEMA = "flashml.competition.synth/v1"

CHECKPOINT_SCHEMA = "flashml.competition.checkpoint/v1"
MODEL_SCHEMA = "flashml.competition.model/v1"
TRAIN_METRICS_SCHEMA = "flashml.competition.train-metrics/v1"
EVAL_METRICS_SCHEMA = "flashml.competition.eval-metrics/v1"

#: What flashnode binds a task's workdir at, and what the trusted tier
#: rewrites `/work`-prefixed argv tokens onto. Read from the environment
#: first because that is the one value the agent sets for a workload that
#: resolves its own paths (`TrustedArgvRunner.task_env`).
def work_dir() -> str:
    return os.environ.get("FLASHML_WORK_DIR", "/work")


class WorkloadError(Exception):
    """A refusal. Every one of these is a case where continuing would produce
    a number that looks fine and means nothing."""


# ---------------------------------------------------------------------------
# Deterministic PRNG — splitmix64, integer arithmetic only
# ---------------------------------------------------------------------------

_MASK64 = (1 << 64) - 1
_GOLDEN = 0x9E3779B97F4A7C15


class Rng:
    """splitmix64. Chosen over `random.Random` for one reason: CPython
    documents that "most of the random module's algorithms and seeding
    functions are subject to change across Python versions", and a demo whose
    central claim is byte-identical reproduction cannot rest on that. Eleven
    lines of integer arithmetic reproduce anywhere Python runs."""

    __slots__ = ("_state", "_spare")

    def __init__(self, seed: int) -> None:
        self._state = int(seed) & _MASK64
        self._spare: float | None = None

    def next_u64(self) -> int:
        self._state = (self._state + _GOLDEN) & _MASK64
        z = self._state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK64
        return z ^ (z >> 31)

    def random(self) -> float:
        """A double in [0, 1). 53 bits, the same construction CPython uses,
        so the mapping from bits to float is exact rather than rounded."""
        return (self.next_u64() >> 11) * (2.0**-53)

    def gauss(self) -> float:
        """Box-Muller, both variates kept. Rejecting `u1 == 0.0` rather than
        nudging it: `log(0)` is the one input that turns a deterministic
        stream into an exception, and it is reachable (probability 2^-53, and
        this draws millions)."""
        if self._spare is not None:
            value, self._spare = self._spare, None
            return value
        while True:
            u1 = self.random()
            if u1 > 0.0:
                break
        u2 = self.random()
        radius = math.sqrt(-2.0 * math.log(u1))
        angle = 2.0 * math.pi * u2
        self._spare = radius * math.sin(angle)
        return radius * math.cos(angle)

    def permutation(self, n: int) -> list[int]:
        """Fisher-Yates over `range(n)`, unbiased by rejection sampling.

        `next_u64() % bound` would be biased, and while the bias is far too
        small to change an accuracy number it would make the shuffle depend on
        the modulus in a way that is tedious to reason about later. Rejection
        costs a redraw about once in 2^64 tries.
        """
        items = list(range(n))
        for i in range(n - 1, 0, -1):
            bound = i + 1
            limit = _MASK64 - (_MASK64 % bound)
            while True:
                draw = self.next_u64()
                if draw <= limit:
                    break
            j = draw % bound
            items[i], items[j] = items[j], items[i]
        return items


def derive_seed(*parts: Any) -> int:
    """A 64-bit seed from a tuple of labels and integers.

    Used to give every independent stream (weights, training data, held-out
    data, epoch 7's shuffle) its own generator rather than threading one
    mutable PRNG through the run. That is what makes epoch-boundary resume
    exact: epoch 7's batch order is a pure function of (seed, 7), so an
    attempt that starts at epoch 7 draws the same permutation as the attempt
    that died before reaching it — with no PRNG state to serialise, and so no
    way for the serialised state to be subtly wrong.
    """
    material = "|".join(str(p) for p in parts).encode()
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


# ---------------------------------------------------------------------------
# The task: a three-sector pinwheel in a random linear embedding
# ---------------------------------------------------------------------------


def default_recipe(seed: int, samples: int, features: int) -> dict[str, Any]:
    """The dataset description that gets embedded in `model.json`.

    Embedded rather than shipped alongside, because the evaluator has to
    rebuild the held-out split with no network and no data file, and the only
    thing it is given is the model. A recipe inside the model means the
    evaluation is *of that model on its own stated task* — swap the model and
    the eval set follows it, which is the property that makes the reported
    accuracy attributable.
    """
    return {
        "generator": GENERATOR_SCHEMA,
        "seed": int(seed),
        "samples": int(samples),
        "features": int(features),
        "classes": 3,
        "noise": 0.35,
        "holdout_samples": 600,
    }


def _embedding(recipe: dict[str, Any]) -> list[list[float]]:
    """The fixed `features x 2` projection carrying the 2-D latent into
    feature space. Drawn once from the recipe's seed, so train and holdout
    live in the same space."""
    rng = Rng(derive_seed("embedding", recipe["seed"], recipe["features"]))
    scale = 1.0 / math.sqrt(2.0)
    return [
        [rng.gauss() * scale, rng.gauss() * scale]
        for _ in range(int(recipe["features"]))
    ]


def synth_dataset(recipe: dict[str, Any], split: str) -> tuple[list[list[float]], list[int]]:
    """`(X, y)` for `split` in {"train", "holdout"}.

    A pinwheel: the class is the angular sector of a 2-D latent, so the
    decision boundary is three rays from the origin. Deliberately *not*
    linearly separable — a linear model tops out around 60-70% here, which is
    what makes the trained network's accuracy a number that says something
    rather than a number every model would reach.

    The two splits draw from disjoint streams, so the holdout is genuinely
    unseen rather than a resample of the training set.
    """
    if recipe.get("generator") != GENERATOR_SCHEMA:
        raise WorkloadError(
            f"dataset recipe declares generator {recipe.get('generator')!r}, and "
            f"this code implements {GENERATOR_SCHEMA!r} — refusing to synthesise a "
            f"different dataset than the model was trained on"
        )
    if split not in ("train", "holdout"):
        raise WorkloadError(f"unknown split {split!r}")

    n = int(recipe["samples"] if split == "train" else recipe["holdout_samples"])
    features = int(recipe["features"])
    classes = int(recipe["classes"])
    noise = float(recipe["noise"])
    projection = _embedding(recipe)
    rng = Rng(derive_seed("data", recipe["seed"], split, n, features))

    sector = 2.0 * math.pi / classes
    xs: list[list[float]] = []
    ys: list[int] = []
    for i in range(n):
        # Balanced by construction rather than by chance: the label is chosen
        # first and the latent is drawn to match it. An imbalanced holdout
        # would make accuracy depend on the draw as much as on the model.
        label = i % classes
        radius = 0.6 + 1.4 * rng.random()
        angle = -math.pi + sector * (label + rng.random())
        u = radius * math.cos(angle)
        v = radius * math.sin(angle)
        row = [
            projection[j][0] * u + projection[j][1] * v + noise * rng.gauss()
            for j in range(features)
        ]
        xs.append(row)
        ys.append(label)
    return xs, ys


# ---------------------------------------------------------------------------
# The model: one tanh hidden layer, softmax out
# ---------------------------------------------------------------------------


def init_params(seed: int, features: int, hidden: int, classes: int) -> dict[str, Any]:
    """Xavier-ish init from its own stream."""
    rng = Rng(derive_seed("init", seed, features, hidden, classes))
    s1 = math.sqrt(1.0 / features)
    s2 = math.sqrt(1.0 / hidden)
    return {
        "W1": [[rng.gauss() * s1 for _ in range(features)] for _ in range(hidden)],
        "b1": [0.0] * hidden,
        "W2": [[rng.gauss() * s2 for _ in range(hidden)] for _ in range(classes)],
        "b2": [0.0] * classes,
    }


def forward(params: dict[str, Any], x: list[float]) -> tuple[list[float], list[float]]:
    """`(hidden activations, class probabilities)` for one sample.

    Written out as loops rather than comprehensions over zip() so the order of
    every float addition is fixed and obvious; that order is what byte-identity
    across two runs actually depends on.
    """
    w1, b1, w2, b2 = params["W1"], params["b1"], params["W2"], params["b2"]
    h: list[float] = []
    for k in range(len(w1)):
        row = w1[k]
        total = b1[k]
        for j in range(len(row)):
            total += row[j] * x[j]
        h.append(math.tanh(total))

    logits: list[float] = []
    for c in range(len(w2)):
        row = w2[c]
        total = b2[c]
        for k in range(len(row)):
            total += row[k] * h[k]
        logits.append(total)

    peak = max(logits)
    exps = [math.exp(v - peak) for v in logits]
    denom = 0.0
    for v in exps:
        denom += v
    return h, [v / denom for v in exps]


def evaluate_params(
    params: dict[str, Any], xs: list[list[float]], ys: list[int]
) -> tuple[float, float]:
    """`(mean cross-entropy, accuracy)`. Shared by trainer and evaluator so
    the two report the same number for the same model on the same data — the
    cross-check `test_workloads.py` makes."""
    loss_total = 0.0
    correct = 0
    for i in range(len(xs)):
        _, probs = forward(params, xs[i])
        target = ys[i]
        loss_total += -math.log(max(probs[target], 1e-12))
        best = 0
        best_p = probs[0]
        for c in range(1, len(probs)):
            if probs[c] > best_p:
                best_p = probs[c]
                best = c
        if best == target:
            correct += 1
    n = len(xs) or 1
    return loss_total / n, correct / n


def param_shapes(params: dict[str, Any]) -> dict[str, list[int]]:
    return {
        "W1": [len(params["W1"]), len(params["W1"][0])],
        "b1": [len(params["b1"])],
        "W2": [len(params["W2"]), len(params["W2"][0])],
        "b2": [len(params["b2"])],
    }


def assert_finite(params: dict[str, Any], where: str) -> None:
    """Refuse to persist a diverged model.

    Not a style check: `float('nan')` serialises to the bare token `NaN`,
    which is not JSON, which every strict parser downstream refuses — so a
    divergence would surface as a mysterious parse error on a volunteer's
    machine instead of as the training failure it is.
    """
    for name in ("W1", "W2"):
        for row in params[name]:
            for value in row:
                if not math.isfinite(value):
                    raise WorkloadError(
                        f"{where}: {name} contains a non-finite value — training "
                        f"diverged, refusing to write a checkpoint that cannot be "
                        f"resumed from"
                    )
    for name in ("b1", "b2"):
        for value in params[name]:
            if not math.isfinite(value):
                raise WorkloadError(f"{where}: {name} contains a non-finite value")


# ---------------------------------------------------------------------------
# Serialisation: canonical bytes, atomic writes, verified reads
# ---------------------------------------------------------------------------


def canonical_bytes(document: Any) -> bytes:
    """The one serialisation used for anything that gets hashed or compared.

    `sort_keys` because a dict's insertion order is not part of a document's
    meaning and must not be part of its identity; a fixed separator because
    the default one is version-dependent whitespace; a trailing newline so the
    file is a well-formed text file. Floats go through `repr`, which is the
    shortest string that round-trips exactly — so a load/dump cycle is
    lossless and a resumed run holds bit-identical weights.
    """
    return (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write `data` to `path` so that no reader ever sees it half-written.

    Temp file in the *same directory* (rename is only atomic within a
    filesystem), fsync the file so the bytes are on the device before the name
    points at them, `os.replace` for the atomic swap, then fsync the directory
    so the rename itself survives a power cut. The demo kills a worker
    mid-run; without this, "the checkpoint is there" and "the checkpoint is
    complete" would be different questions.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def atomic_write_json(path: Path, document: Any) -> bytes:
    """`atomic_write_bytes` over `canonical_bytes`; returns what was written
    so a caller can hash exactly the bytes on disk rather than re-serialising
    and hoping the two agree."""
    data = canonical_bytes(document)
    atomic_write_bytes(path, data)
    return data


#: The field carrying a checkpoint's self-digest. Excluded from its own
#: computation, obviously — the digest is over the document without it.
DIGEST_FIELD = "content_sha256"


def seal(document: dict[str, Any]) -> dict[str, Any]:
    """Return `document` with a `content_sha256` over everything else."""
    body = {k: v for k, v in document.items() if k != DIGEST_FIELD}
    sealed = dict(body)
    sealed[DIGEST_FIELD] = sha256_hex(canonical_bytes(body))
    return sealed


def verify_seal(document: dict[str, Any], source: str) -> None:
    declared = document.get(DIGEST_FIELD)
    if not isinstance(declared, str) or not declared:
        raise WorkloadError(
            f"{source}: no {DIGEST_FIELD} — this is not a checkpoint this "
            f"trainer wrote, and resuming from it would silently restart "
            f"training from someone else's weights"
        )
    body = {k: v for k, v in document.items() if k != DIGEST_FIELD}
    actual = sha256_hex(canonical_bytes(body))
    if actual != declared:
        raise WorkloadError(
            f"{source}: content_sha256 mismatch (declared {declared[:16]}…, "
            f"computed {actual[:16]}…) — the file is damaged; refusing to "
            f"resume from it"
        )


def load_json_document(path: Path, source: str) -> dict[str, Any]:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise WorkloadError(f"{source}: cannot be read ({exc})") from None
    return parse_json_document(raw, source)


def parse_json_document(raw: bytes, source: str) -> dict[str, Any]:
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        # The overwhelmingly likely shape of a file that lost a race with
        # SIGKILL. Named as such, because "Expecting value: line 1 column 1"
        # on a volunteer's machine tells the submitter nothing.
        raise WorkloadError(
            f"{source}: not parseable as JSON ({exc}) — most likely truncated"
        ) from None
    if not isinstance(document, dict):
        raise WorkloadError(f"{source}: expected a JSON object, got {type(document).__name__}")
    return document


def load_checkpoint(path: Path) -> dict[str, Any]:
    """A checkpoint that is complete, intact and this schema — or an exception.

    Three separate refusals rather than one, because they mean different
    things to whoever is reading the failure: truncated (the kill landed
    mid-write and the atomic rename did not save us — a bug), wrong schema
    (a stale artifact from an older run), damaged (bit rot or a hostile
    upload). None of them may fall through to "resume anyway": a run that
    resumed from garbage would train to completion and commit a model whose
    provenance is fiction.
    """
    source = f"checkpoint {Path(path).name}"
    document = load_json_document(Path(path), source)
    if document.get("schema") != CHECKPOINT_SCHEMA:
        raise WorkloadError(
            f"{source}: schema is {document.get('schema')!r}, expected "
            f"{CHECKPOINT_SCHEMA!r}"
        )
    verify_seal(document, source)
    for field in ("run", "progress", "optimizer", "model"):
        if not isinstance(document.get(field), dict):
            raise WorkloadError(f"{source}: missing or malformed {field!r} section")
    return document
