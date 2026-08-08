# Writing a `flashml.yaml`

The complete reference for the file that defines a job. One page, every key,
and the two things about this format that surprise everyone.

## The two surprises, first

**One repo is one job.** `flashml.yaml` lives at the repo root and *is* the
job definition. There is no way to put two jobs in one repo at one commit.

**So different jobs are different git branches.** That is what the "Branch"
field on the submit form is for. `Zolli-Labs/flashml-examples` is laid out
exactly this way — `main` is a hello-world, `sweep` is a hyperparameter
search, `federated` is a multi-machine training run, and they are the same
repository.

This is not most people's mental model, which is "a project containing
several experiments". Nothing in the product corrects that assumption until
a submission fails, so it is worth saying twice.

## The contract with your code

Two things, and no FlashML imports:

- **CLI flags in.** Whatever you put in `args`, plus one `--flag value` pair
  per `sweep` or `partition` variable.
- **`metrics.json` out**, written to `/work/out/`. It is the artifact the
  coordinator validates by sha256 at commit time, so a task that does not
  produce it cannot commit and will be retried elsewhere until its attempts
  run out.

Your repo is staged at `/work/inputs/code/`. Anything else you declared as
an input lands beside it. **A task has no network** — it cannot `pip
install`, fetch a dataset, or reach HuggingFace once it starts. Everything
comes from the image or from a staged input.

## Every key

### Required

| Key | Type | Notes |
|---|---|---|
| `version` | int | Must be `1` |
| `name` | string | Becomes the job name |
| `image` | string | One of the curated images below |
| `entrypoint` | string | Path inside your repo, e.g. `jobs/train.py`. Subdirectories are fine |

### The curated images

You cannot bring your own image, and preflight statically checks every
import in your entrypoint against what the image actually provides — so
asking for `python-slim` and importing numpy is refused at submit time
rather than discovered on someone else's machine an hour later.

| `image` | What you can import |
|---|---|
| `python-slim` | the standard library, nothing else |
| `sklearn` | numpy, pandas, sklearn, scipy, joblib, threadpoolctl |
| `pytorch-cpu` | torch, numpy |
| `pytorch-cuda` | torch, numpy — and it only ever lands on a host that advertised a GPU |

If your code needs something outside these lists, it cannot run yet. That is
the real ceiling on what FlashML can do today, and it is a known one.

### Optional

| Key | Type | Notes |
|---|---|---|
| `args` | list of strings | Passed verbatim after the entrypoint. **A list, not a shell string** — there is no shell on the other side |
| `sweep` | map of lists | One task per combination. Capped at 100 combinations |
| `partition` | map | One task per shard of a range. See below |
| `validators` | map | What a valid result looks like. See below |
| `reduce` | map | How to turn many task results into one answer. See below |
| `allow_partial` | bool | Accept a run where some tasks failed. See below |
| `resources` | map | e.g. `gpus: 1` |
| `timeout_seconds` | int | Wall clock per task, capped at 24h |
| `local_inputs` | list of strings | Labels for data a *host* lends, mounted read-only. Never uploaded |
| `mode` | string | `independent` (default) or `federated` |
| `rounds`, `min_participants`, `shards` | int | Federated only |

Unknown keys are **refused**, not ignored — so `entrypint:` fails loudly
instead of silently meaning nothing.

## Fanning out: `sweep` or `partition`, never both

A job has exactly one generator. Declaring both is refused.

### `sweep` — one task per parameter combination

```yaml
sweep:
  n_estimators: [50, 100, 200]
  max_depth: [4, 8, 16]          # → 9 tasks
```

Each key becomes a CLI flag. Your script sees `--n_estimators 100
--max_depth 8`.

### `partition` — one task per shard of a range

```yaml
partition:
  range: [0, 1000000]
  shards: 24                      # or: shard_size: 5000
```

Each task gets `--start`, `--end`, and `--shard_index` — use them in `args`
as `{start}`, `{end}`, `{shard_index}`. Intervals are half-open and tile the
range exactly: no gaps, no overlap, every index handled once.

## Saying what a good result looks like: `validators`

```yaml
validators:
  keys: [accuracy]                # metrics.json must carry these, non-null
```

A task whose `metrics.json` is intact and hashes correctly but does not
carry `accuracy` fails its attempt and is retried elsewhere. Without this,
"the file arrived" is the only check there is.

`json_schema` is also available where the coordinator has the optional
`jsonschema` package; it is refused at submission if not.

## Getting one answer back: `reduce`

Without this, a finished job hands you a directory of task outputs and no
answer.

```yaml
reduce:
  kind: rank                      # rank | aggregate | concat | collect | none
  metric: accuracy
  maximize: true
```

| `kind` | What you get |
|---|---|
| `rank` | The winning task named, plus the full ordering. Needs `metric` |
| `aggregate` | mean / sum / min / max across tasks. Needs `metric` |
| `concat` | Shard files merged in shard order into one artifact. Needs `file` |
| `collect` | A manifest of what was produced |
| `none` | The task artifacts are the deliverable (the default) |

`reduce` without `kind` is refused — `reduce: {metric: acc}` is a plausible
typo that would otherwise silently mean no reduction at all, discovered
after the job ran.

## Surviving a dead machine: `allow_partial`

```yaml
allow_partial: true
```

By default, one task exhausting its attempts fails the whole job. On a fleet
of volunteer laptops that is not an edge case — it is Tuesday, and it throws
away the ninety-nine machines' worth of work that succeeded.

With `allow_partial`, the job finishes as **PARTIAL**: a distinct terminal
state, deliberately not "succeeded", because a badge reading succeeded over
a run that lost six of twenty-four shards misrepresents what you got.

## A complete hyperparameter search

Everything above, in the shape most people want first:

```yaml
version: 1
name: rf-sweep
image: sklearn
entrypoint: train.py

sweep:
  n_estimators: [50, 100, 200]
  max_depth: [4, 8, 16]           # 9 trials, spread across the Crew

validators:
  keys: [accuracy]                # a trial with no accuracy is retried

reduce:
  kind: rank
  metric: accuracy                # the console names the winner

allow_partial: true               # one closed laptop doesn't lose the other 8
timeout_seconds: 1800
```

And the whole of `train.py`:

```python
import argparse, json
from sklearn.datasets import make_classification
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import cross_val_score

p = argparse.ArgumentParser()
p.add_argument("--n_estimators", type=int)
p.add_argument("--max_depth", type=int)
a = p.parse_args()

X, y = make_classification(n_samples=2000, random_state=0)
model = RandomForestClassifier(
    n_estimators=a.n_estimators, max_depth=a.max_depth, random_state=0
)
score = cross_val_score(model, X, y, cv=5).mean()

json.dump({"accuracy": float(score)}, open("/work/out/metrics.json", "w"))
```

No FlashML import anywhere. Read flags, write `metrics.json`.

## Where things run, and who sees them

A job belongs to a **Crew** (a workspace) and runs only on machines whose
owners opted them into that Crew. Machines belong to people, not to Crews,
and credit for completed work goes back to the person who owns the machine.
`/how-it-works` in the console draws the whole loop.

## Related

- `join-a-pool-colab.md`, `join-a-pool-runpod.md` — contributing a machine
- `pool-ops-runbook.md` — operating a Crew
- `Zolli-Labs/flashml-examples` — one runnable branch per job type
