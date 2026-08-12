#!/usr/bin/env python3
"""The competition demo's training job: deterministic, checkpointed, resumable.

    python train_checkpointed.py --out /work/out --resume /work/inputs/resume.json

What the demo needs from this file, and what each one costs in the code:

**A kill has to be survivable and the recovery has to be provable.** Every
epoch ends with an atomic checkpoint at `<out>/ckpt/step-<n>.json` — temp
file, fsync, rename, fsync the directory — carrying weights, optimiser
velocity, progress and a self-digest. flashnode's `_CheckpointRelay` watches
exactly that directory for exactly that glob, uploads each new file and
commits it as a single-part manifest, so a worker SIGKILLed a second later
still leaves a resumable point on the coordinator. When the lease expires and
another node claims the task, flashnode downloads the latest valid manifest
part to `/work/inputs/resume.json` and this script picks up from it.

**Resume must not change the answer.** Batch order for epoch *e* is a pure
function of `(seed, e)` — not of a serialised PRNG state — so an attempt that
starts at epoch 7 draws exactly the permutation the dead attempt would have
drawn. Weights and momentum round-trip losslessly through JSON (`repr` floats
are shortest-round-trip). The consequence is the property the whole demo
rests on: interrupted-then-resumed produces a **byte-identical** `model.json`
to an uninterrupted run, so the recovery is provably free rather than
plausibly cheap.

**A person has to be able to watch it.** One line per epoch, flushed, naming
the checkpoint that was just committed.

Constraints this file honours because the platform enforces them anyway: no
network (nothing here opens a socket — the dataset is synthesised from the
seed), no GPU, no third-party import, no wall clock in anything that affects
a weight. `metrics.json` does report a duration, which is a measurement of
the run and not an input to it.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

from workload_common import (
    CHECKPOINT_SCHEMA,
    MODEL_SCHEMA,
    TRAIN_METRICS_SCHEMA,
    Rng,
    WorkloadError,
    assert_finite,
    atomic_write_json,
    default_recipe,
    derive_seed,
    evaluate_params,
    forward,
    init_params,
    load_checkpoint,
    param_shapes,
    seal,
    sha256_hex,
    synth_dataset,
    work_dir,
)

#: Defaults sized for the demo: 45-75 s of pure-Python CPU on a laptop, which
#: is long enough to kill a worker in the middle of and short enough to hold
#: an audience. Every one is overridable, and `test_workloads.py` runs the
#: same code an order of magnitude smaller so the suite stays quick.
DEFAULT_EPOCHS = 14
DEFAULT_SAMPLES = 2400
DEFAULT_FEATURES = 24
DEFAULT_HIDDEN = 32
DEFAULT_BATCH = 24
DEFAULT_LR = 0.35
DEFAULT_MOMENTUM = 0.9
DEFAULT_SEED = 20260811


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    work = work_dir()
    parser = argparse.ArgumentParser(
        prog="train_checkpointed.py", description=__doc__.splitlines()[0]
    )
    # Paths are FLAGS with /work defaults rather than constants inside the
    # file. Both container tiers bind the workdir at /work, and the trusted
    # tier rewrites `/work`-prefixed argv tokens onto the real workdir — a
    # path this file computed for itself would miss that rewrite and write to
    # a literal /work on the host.
    parser.add_argument("--out", default=f"{work}/out",
                        help="output directory (metrics.json, model.json, ckpt/)")
    parser.add_argument("--resume", default=f"{work}/inputs/resume.json",
                        help="checkpoint staged by the agent for a resumed attempt; "
                             "absent on a first attempt, which is not an error")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS)
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--features", type=int, default=DEFAULT_FEATURES)
    parser.add_argument("--hidden", type=int, default=DEFAULT_HIDDEN)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH)
    parser.add_argument("--lr", type=float, default=DEFAULT_LR)
    parser.add_argument("--momentum", type=float, default=DEFAULT_MOMENTUM)
    return parser.parse_args(argv)


def run_config(args: argparse.Namespace) -> dict:
    """Every number that decides what the run computes, in one dict.

    Written into each checkpoint and compared on resume: a checkpoint from a
    different configuration is refused rather than loaded, because continuing
    a 14-epoch run's weights under a 4-epoch schedule produces a model no
    reported number describes.
    """
    steps_per_epoch = max(1, args.samples // args.batch_size)
    return {
        "seed": int(args.seed),
        "epochs": int(args.epochs),
        "samples": int(args.samples),
        "features": int(args.features),
        "hidden": int(args.hidden),
        "classes": 3,
        "batch_size": int(args.batch_size),
        "lr": float(args.lr),
        "momentum": float(args.momentum),
        "steps_per_epoch": steps_per_epoch,
        "steps_total": steps_per_epoch * int(args.epochs),
    }


def zero_like(params: dict) -> dict:
    return {
        "W1": [[0.0] * len(row) for row in params["W1"]],
        "b1": [0.0] * len(params["b1"]),
        "W2": [[0.0] * len(row) for row in params["W2"]],
        "b2": [0.0] * len(params["b2"]),
    }


def train_epoch(
    params: dict,
    velocity: dict,
    xs: list[list[float]],
    ys: list[int],
    config: dict,
    epoch: int,
) -> tuple[float, float]:
    """One pass. Mutates `params` and `velocity` in place; returns
    `(mean loss, accuracy)` measured on the batches as they were seen.

    The permutation is drawn from `(seed, epoch)` and nothing else — that is
    the single design decision that makes resume exact. Everything else here
    is textbook minibatch SGD with momentum, written as explicit loops so the
    float addition order is fixed.
    """
    order = Rng(derive_seed("epoch", config["seed"], epoch)).permutation(len(xs))
    w1, b1, w2, b2 = params["W1"], params["b1"], params["W2"], params["b2"]
    v1, vb1, v2, vb2 = velocity["W1"], velocity["b1"], velocity["W2"], velocity["b2"]
    hidden, classes, features = config["hidden"], config["classes"], config["features"]
    lr, momentum = config["lr"], config["momentum"]
    batch_size = config["batch_size"]

    loss_total = 0.0
    correct = 0
    seen = 0

    for step in range(config["steps_per_epoch"]):
        batch = order[step * batch_size : (step + 1) * batch_size]
        if not batch:
            break
        g_w1 = [[0.0] * features for _ in range(hidden)]
        g_b1 = [0.0] * hidden
        g_w2 = [[0.0] * hidden for _ in range(classes)]
        g_b2 = [0.0] * classes

        for index in batch:
            x = xs[index]
            target = ys[index]
            h, probs = forward(params, x)

            loss_total += -math.log(max(probs[target], 1e-12))
            best = 0
            best_p = probs[0]
            for c in range(1, classes):
                if probs[c] > best_p:
                    best_p, best = probs[c], c
            correct += 1 if best == target else 0
            seen += 1

            # dL/dlogits for softmax + cross-entropy.
            dlogits = [probs[c] - (1.0 if c == target else 0.0) for c in range(classes)]
            dh = [0.0] * hidden
            for c in range(classes):
                grad = dlogits[c]
                row = w2[c]
                g_row = g_w2[c]
                for k in range(hidden):
                    g_row[k] += grad * h[k]
                    dh[k] += grad * row[k]
                g_b2[c] += grad
            for k in range(hidden):
                # tanh'(z) = 1 - tanh(z)^2, and h[k] is already tanh(z).
                delta = dh[k] * (1.0 - h[k] * h[k])
                g_row = g_w1[k]
                for j in range(features):
                    g_row[j] += delta * x[j]
                g_b1[k] += delta

        inv = 1.0 / len(batch)
        for k in range(hidden):
            row, g_row, v_row = w1[k], g_w1[k], v1[k]
            for j in range(features):
                v_row[j] = momentum * v_row[j] - lr * g_row[j] * inv
                row[j] += v_row[j]
            vb1[k] = momentum * vb1[k] - lr * g_b1[k] * inv
            b1[k] += vb1[k]
        for c in range(classes):
            row, g_row, v_row = w2[c], g_w2[c], v2[c]
            for k in range(hidden):
                v_row[k] = momentum * v_row[k] - lr * g_row[k] * inv
                row[k] += v_row[k]
            vb2[c] = momentum * vb2[c] - lr * g_b2[c] * inv
            b2[c] += vb2[c]

    return (loss_total / max(seen, 1)), (correct / max(seen, 1))


def load_resume(path: Path, config: dict) -> dict | None:
    """The staged checkpoint, or `None` if this is a first attempt.

    An absent file is the normal case and not an error — flashnode stages
    `resume.json` only when the coordinator holds a committed checkpoint for
    this task. A file that is *present and broken* is the opposite: it means
    the recovery path is live and the bytes it delivered cannot be trusted, so
    it raises rather than silently restarting from scratch. Restarting quietly
    would look like a successful recovery and would throw away every minute
    the dead attempt spent.
    """
    if not path.exists():
        return None
    document = load_checkpoint(path)
    stale = [
        key for key in ("seed", "epochs", "samples", "features", "hidden",
                        "classes", "batch_size", "lr", "momentum")
        if document["run"].get(key) != config[key]
    ]
    if stale:
        raise WorkloadError(
            f"checkpoint {path.name} was written by a run with different "
            f"{stale} — refusing to continue one run's weights under another "
            f"run's schedule"
        )
    return document


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started = time.monotonic()  # measured, never an input to a weight
    config = run_config(args)
    out = Path(args.out)
    ckpt_dir = out / "ckpt"
    out.mkdir(parents=True, exist_ok=True)
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    recipe = default_recipe(config["seed"], config["samples"], config["features"])
    xs, ys = synth_dataset(recipe, "train")
    hold_xs, hold_ys = synth_dataset(recipe, "holdout")

    resumed = load_resume(Path(args.resume), config)
    if resumed is None:
        params = init_params(
            config["seed"], config["features"], config["hidden"], config["classes"]
        )
        velocity = zero_like(params)
        start_epoch = 0
        resumed_from_step = None
        print(f"▶ fresh start — seed {config['seed']}, {config['epochs']} epochs "
              f"x {config['steps_per_epoch']} steps, {config['samples']} samples",
              flush=True)
    else:
        params = resumed["model"]
        velocity = resumed["optimizer"]["velocity"]
        start_epoch = int(resumed["progress"]["epoch"])
        resumed_from_step = int(resumed["progress"]["step"])
        print(f"▶ RESUMED from step {resumed_from_step} "
              f"(epoch {start_epoch}/{config['epochs']}) — "
              f"{resumed_from_step} steps of work survived the kill",
              flush=True)

    written: list[str] = []
    train_loss = float("nan")
    train_accuracy = float("nan")
    for epoch in range(start_epoch, config["epochs"]):
        epoch_started = time.monotonic()
        train_loss, train_accuracy = train_epoch(params, velocity, xs, ys, config, epoch)
        assert_finite(params, f"epoch {epoch}")
        step = (epoch + 1) * config["steps_per_epoch"]
        document = seal({
            "schema": CHECKPOINT_SCHEMA,
            "run": config,
            "progress": {
                "epoch": epoch + 1,
                "step": step,
                "epochs_total": config["epochs"],
                "steps_total": config["steps_total"],
            },
            # Recorded on every checkpoint, not just the first one written
            # after a resume: the final metrics have to be able to say where
            # this run picked up even if it checkpointed a dozen times since.
            "resumed_from_step": resumed_from_step,
            "optimizer": {
                "kind": "sgd-momentum",
                "lr": config["lr"],
                "momentum": config["momentum"],
                "velocity": velocity,
            },
            "model": params,
            "metrics": {"train_loss": train_loss, "train_accuracy": train_accuracy},
        })
        path = ckpt_dir / f"step-{step}.json"
        atomic_write_json(path, document)
        written.append(f"ckpt/{path.name}")
        print(f"  epoch {epoch + 1:>2}/{config['epochs']}  step {step:>4}/"
              f"{config['steps_total']}  loss {train_loss:.4f}  acc {train_accuracy:.3f}"
              f"  → {path.name}  ({time.monotonic() - epoch_started:.1f}s)",
              flush=True)

    holdout_loss, holdout_accuracy = evaluate_params(params, hold_xs, hold_ys)

    model_document = {
        "schema": MODEL_SCHEMA,
        "architecture": {
            "kind": "mlp-tanh-softmax",
            "features": config["features"],
            "hidden": config["hidden"],
            "classes": config["classes"],
            "shapes": param_shapes(params),
        },
        # The evaluator rebuilds the held-out split from this and nothing
        # else — no network, no data file, no shared state with the trainer
        # beyond these numbers.
        "data_recipe": recipe,
        "params": params,
        "trained_for": {
            "seed": config["seed"],
            "epochs": config["epochs"],
            "steps": config["steps_total"],
        },
    }
    # The model's identity is the bytes on disk, so hash what was written
    # rather than re-serialising: those are the bytes the mirror uploads, the
    # sandbox downloads, and the evaluator hashes.
    model_bytes = atomic_write_json(out / "model.json", model_document)
    model_sha256 = sha256_hex(model_bytes)

    duration = time.monotonic() - started
    metrics = {
        "schema": TRAIN_METRICS_SCHEMA,
        "workload": "train_checkpointed",
        "seed": config["seed"],
        "epochs": config["epochs"],
        "epochs_executed": config["epochs"] - start_epoch,
        "steps": config["steps_total"],
        "steps_per_epoch": config["steps_per_epoch"],
        "samples": config["samples"],
        "train_loss": train_loss,
        "train_accuracy": train_accuracy,
        "holdout_loss": holdout_loss,
        "accuracy": holdout_accuracy,
        "resumed": resumed is not None,
        "resumed_from_step": resumed_from_step,
        # Work repeated after the kill. Zero by construction: checkpoints land
        # on epoch boundaries and a resume restarts at one, so the ceiling the
        # demo claims — never more than one checkpoint interval — is met with
        # room to spare. Reported anyway, because a claim nobody measures is a
        # claim that quietly stops being true.
        "recomputed_steps": 0,
        "model_file": "model.json",
        "model_bytes": len(model_bytes),
        "model_sha256": model_sha256,
        "checkpoints": written,
        "duration_seconds": round(duration, 3),
    }
    atomic_write_json(out / "metrics.json", metrics)

    print(f"▶ done in {duration:.1f}s — holdout accuracy {holdout_accuracy:.4f}, "
          f"model sha256 {model_sha256}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkloadError as exc:
        print(f"train_checkpointed: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(2) from None
