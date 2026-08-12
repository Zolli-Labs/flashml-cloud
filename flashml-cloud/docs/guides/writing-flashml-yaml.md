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

There is a third thing, and it is not a key in this file at all: if you want
your task to survive a machine dying, it has to write and read checkpoints
at two fixed paths. See "Not losing the work" below.

Your repo is staged at `/work/inputs/code/`. Anything else you declared as
an input lands beside it. **A task has no network** — it cannot `pip
install`, fetch a dataset, or reach HuggingFace once it starts. Everything
comes from the image, from a staged input, or from a `datasets:` block the
host fetched for you before your code started.

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
| `datasets` | list of maps | Public data the *host* fetches before your task starts. See below |
| `mode` | string | `independent` (default) or `federated` |
| `epochs` | int | Federated only, required. Passes over your data |
| `sync_every` | float | Federated only. Passes between combines. Only `1.0` today |

Unknown keys are **refused**, not ignored — so `entrypint:` fails loudly
instead of silently meaning nothing.

### `rounds`, `min_participants` and `shards` are gone

They asked you how many pieces to cut the work into and how many machines to
wait for. Neither is answerable: you cannot see the Crew when you write the
file, and the Crew changes while your job runs — eleven machines online with
`shards: 3` left eight of them doing nothing, and a closed laptop left a hole
in the round until its lease expired.

Set `epochs` instead. The split and the machine count are now decided at
submit time from whoever is actually online. A file that still names any of
the three is refused with a message naming its replacement.

Because the meaning of a federated file changed, `mode: federated` requires
`version: 2`. Sweeps and independent jobs are untouched and stay `version: 1`.

## Federated: training one model across machines

```yaml
version: 2
name: acme-fed
image: pytorch-cpu
entrypoint: train.py
mode: federated
epochs: 5
```

Five passes over your data. Each pass is one **round**: the platform hands
every online machine a chunk of the data, waits until the whole pass is
covered, averages what came back, and starts the next round from the average.

**Your entrypoint is the round worker**, so it has to speak four things:

1. Read the round's weights from `/work/inputs/weights.json`. **The file is
   absent on round 0** — that is where you start from your own initialisation.
2. Train on the chunk you were given: `--shard` is its id and `--num-shards`
   is how many chunks the pass has, so the slice is
   `arange(shard, len(data), num_shards)`.
3. Write your weight *change* to `/work/out/delta.json` as
   `{"<param>": {"shape": [...], "data": [...]}}`. On round 0, where you were
   given no weights, that is your trained weights themselves.
4. Write `/work/out/metrics.json` with `samples` (a positive integer), `loss`
   (a number), and **`chunks_done`** — the list of chunk ids you finished,
   which is `[args.shard]`.

`chunks_done` is the one people miss, and it fails silently: a round credits
your machine only for chunks it can prove it was handed, so a contribution
reporting none is averaged in with zero weight no matter how long it trained.
Preflight refuses a federated entrypoint that never mentions it.

### `sync_every`

Passes of data between combines. `1.0` — one combine per pass — is the
default and currently the only accepted value. Combining more often needs an
entrypoint that trains a *sequence* of chunks and reports every id it
finished; until that contract lands, a smaller value is refused rather than
honoured, because honouring it would silently either discard the work of
machines still training when a round closed or leave part of your data never
trained at all.

## Bringing your own data: `datasets`

Until now the only real data a job could see was `local_inputs` — which works
only when the person with the data also owns the machine. `datasets:` is the
other half, and it is one line:

```yaml
datasets:
  - name: imdb
    source: hf://stanfordnlp/imdb
```

**We never store your data.** At submit time we read a *file listing* from
your origin — paths, byte sizes, checksums — and hand each machine the list of
URLs it needs. The bytes go from your origin straight to the machine that
trains on them and never pass through us. What we keep is a manifest measured
in kilobytes.

**Your task still has no network.** Nothing in the contract above changes:
`--network none` is still enforced, your code still cannot `pip install` or
call `load_dataset()`. The host agent fetches *before* the sandbox closes, so
by the time your code starts the files are simply there, at
`/work/data/<name>/`, with the directory shape the origin listed. A file at
`plain_text/train-00000.parquet` lands at
`/work/data/imdb/plain_text/train-00000.parquet`.

Treat those files as read-only. The host hard-links them out of a cache it
shares between jobs, so writing to one in place can corrupt the copy the next
job gets.

### Where to put your data

| Your data | What you write |
|---|---|
| Public | `hf://org/name` — the recommendation, for reasons below |
| Public bucket | `s3://bucket/prefix` or `r2://account-id/bucket/prefix` |
| Public, anything else | `https://you.example/manifest.json` — you list the files |
| **Private** | **No path yet.** `local_inputs`, on a machine you host yourself |

**Hugging Face is the recommendation, and not out of habit.** Egress is free
and CDN-backed, the tree API hands us a real sha256 for every file, and `main`
resolves to a commit SHA we can pin. Then the unintuitive part: anonymous rate
limits on the Hub are applied **per IP** — 3,000 file requests per five-minute
window, per address — so thirty machines on thirty home connections get thirty
separate budgets. A fleet spread across the internet is *structurally
advantaged* there, which is rarely true of anything.

The counterweight, which is not fine print: public Hub storage is a commons
with a stated reuse expectation. Do not push private working data to a public
repo to get free bandwidth out of us. That is free-riding, it breaks their
terms, and it breaks the day they enforce.

**A public bucket works too**, listed anonymously. A bucket that requires a
signature answers 403 and is refused at submit as "not public" — we do not
retry with a credential, because we have none. Note who pays: S3 charges
$0.09/GB of egress, and twenty machines pulling a 200 GB dataset over ten
rounds is 2 TB, about **$184 for one job**. R2 and Hugging Face charge nothing
for egress at all.

**`https://` is the escape hatch.** Point it at a JSON document you host:

```json
{"entries": [
  {"path": "train/shard-000.npy",
   "url": "https://you.example/shard-000.npy",
   "size": 4194304,
   "sha256": "db47d16b…"}
]}
```

Every `url` must be `https://` — a volunteer fetches it over the open
internet, and the transport is the only thing standing between that fetch and
a tampered file. Give every entry a real `sha256`: an entry without one is
accepted at submit and then *refused by the host agent*, which is the worst
possible place to find out.

**Private data has no path yet.** v1 resolves public origins only and stores
no credential of any kind — not a token, not a key, nothing. A private or
gated Hub repo is therefore refused at submit *by name, with the reason*,
rather than 401-ing on thirty machines forty minutes into a round. Until the
private-data work lands, private means `local_inputs` and a machine you host.

### The revision is pinned at submit

`hf://stanfordnlp/imdb` resolves to a 40-character commit SHA once, when you
submit, and every URL in the job addresses that SHA. Pushing to the dataset
while the job runs cannot change what it trains on. Object stores have no
commit, so their pin is a digest over the key/ETag set that was listed; an
`https://` manifest is pinned by a hash of the document as served.

Pin it yourself with `hf://stanfordnlp/imdb@<rev>`. Whatever you put after the
`@` is honoured verbatim, because `@` is you saying "this one" — and that cuts
both ways: `@main` is **not** a pin, it is a branch name we will use as you
wrote it, so writing nothing pins harder than writing `@main` does. A tag is
only as immutable as whoever can move it. A commit SHA cannot be re-pointed at
all, which is why it is what you get by default.

### `select` narrows the listing

```yaml
select: "plain_text/train-*.parquet"
```

A glob against the full path the origin printed, case-sensitive, and `*`
deliberately crosses `/` so the line above reads the way you expect. For a
bucket the path is the whole object key, prefix included. A `select` that
matches nothing is **refused at submit**, naming a few of the paths that were
listed — a job with no data is not a smaller job.

One dataset may resolve to at most 50,000 files. Past that, narrow it with
`select:` or combine your shards into bigger ones.

### `split` is inferred from `mode`

| `mode` | inferred `split` | Each machine gets |
|---|---|---|
| `federated` | `shard` | a *different*, disjoint, byte-balanced piece; the fleet covers one pass |
| anything else | `replica` | the whole dataset |

Write `split: shard` or `split: replica` to override; explicit always wins.

The two shapes ask different questions of the Crew. `shard` asks whether one
machine can hold one slice; `replica` asks whether **every** machine can hold
the whole thing — so `replica` on a big dataset is refused where `shard`
admits, and that is the feature rather than a quirk.

Either way the check happens at submit: if the largest task's share is bigger
than the biggest dataset cache any online machine advertises, the job is
refused right there, naming both numbers. A slice is fetched whole by one
host, so the fleet's combined space does not help. That is one second in the
console instead of twenty machines each downloading for forty minutes and then
giving up. Hosts set the budget with `FLASHNODE_DATA_BUDGET_GB` (16 GB by
default, `0` to take no dataset work at all), and an agent too old to know
about the field advertises nothing and is never sent any.

**A `partition:` job cannot declare `datasets:` yet.** The slices are cut per
task at submit, and a partition's tasks are expanded later, by the
coordinator — so the counts disagree and the job fails when it expands rather
than being refused when you submit it. Use `sweep` or `mode: federated`.

### A dataset with few files caps your fleet

Three files means at most three machines, whatever the pool size. This is the
same complaint that removed `shards:` — the difference is that it is now said
out loud rather than leaving eight machines silently idling. You get a warning
and not a refusal, because a small dataset during development is a legitimate
thing to run.

The subtle version, and the one that will actually bite you: **the split
balances bytes, not files.** It has to — real shards are uneven, and dividing
by file count hands one machine ten times the work while every round waits on
it. But it means a dataset with *enough* files can still strand machines if
one file dominates. Five files of 300, 100, 4200, 50 and 900 bytes, cut five
ways, fill three chunks and leave two empty.

An empty chunk is a machine that fetches nothing, trains nothing and reports
nothing — and FedAvg then averages in a member whose gradient does not exist.
Not a smaller round: a different experiment, with everything looking healthy
throughout. So the warning is measured after the cut rather than guessed from
the file count, and it names both numbers:

> dataset `'imdb'` has 3 file(s), and their bytes spread across only 3 of this
> job's 20 parallel slot(s) — the other 17 would fetch nothing and train
> nothing. Split it into more, and more evenly sized, files to use the whole
> fleet.

Which is the fix, in both cases, and it is a decision you make when you
prepare the data rather than when you submit: **more files, of roughly equal
size.**

### `--shard` and `--num-shards` still mean what they meant

They are still in your argv, carrying the same integers. What changed is what
they are *for*: they name the chunk of the pass you were handed, so
`chunks_done: [args.shard]` credits your machine for it. They are no longer
how you slice, because the slicing already happened — your
`/work/data/<name>/` contains your files and nobody else's.

So **do not stride the data again.** The federated section above tells you the
slice is `arange(shard, len(data), num_shards)`; that is right for a job with
no `datasets:` block and wrong for one with it. Read every file in your
directory. Striding your slice a second time trains a fraction of a fraction,
and nothing anywhere reports an error.

### What "verified" means, and where it does not

Integrity is genuinely not equal across origins, so we label it rather than
imply a guarantee we did not get:

| Origin | Token we record | Checked after download? |
|---|---|---|
| `hf://`, LFS file | a real sha256 | **Yes** — a mismatch fails the task |
| `hf://`, small non-LFS file | a git blob oid | No — it hashes a header plus the content, not the bytes you receive |
| `s3://`, `r2://` | the object's ETag | No |
| `https://` | the sha256 *you* declared | Recorded; only `hf://`'s sha256 is verified today |

An ETag is **not** a content hash. For a multipart upload it is
`<md5-of-md5s>-<partcount>`, which is enough to pin a revision and to notice
that an object moved under you, and not enough to prove two machines fetched
the same bytes. We use it for the first thing and never claim the second.

When a checksum does fail, it fails **the task**, never the machine: a broken
or hostile origin costs the submitter their job, not a volunteer their laptop.

### At most four datasets per job

Each declared source is resolved against its origin, one after another, while
your submit request waits — up to 30 seconds apiece. Four keeps the worst case
at two minutes without us needing to get clever, and nobody legitimately
trains one job on ten origins.

Two datasets in one job may not share a `name`: both would mount at
`/work/data/<name>/` and overwrite each other. A `name` is a name and not a
path — it starts with a letter or digit and uses only `[A-Za-z0-9._-]`,
because it becomes a directory on a stranger's machine. `name`, `source`,
`select` and `split` are the only keys an entry may carry; anything else is
refused, the same way an unknown top-level key is.

### A worked federated example

```yaml
version: 2
name: acme-fed
image: pytorch-cpu
entrypoint: train.py
mode: federated
epochs: 3

datasets:
  - name: tokens
    source: hf://acme/tokens-2026        # pinned to a commit at submit
    select: "train/shard-*.npy"          # skip the eval split
```

Three passes over `acme/tokens-2026`, cut into byte-balanced contiguous
pieces, one per machine online when each round starts. The dataset-shaped part
of `train.py`:

```python
import argparse, glob, json
import numpy as np

p = argparse.ArgumentParser()
p.add_argument("--round", type=int)
p.add_argument("--shard", type=int)        # this chunk's id — for reporting
p.add_argument("--num-shards", type=int)   # chunks in the whole pass
a = p.parse_args()

# Every file here is already yours: the slice was cut at submit time.
# Do NOT stride it again by --shard.
files = sorted(glob.glob("/work/data/tokens/train/shard-*.npy"))
data = np.concatenate([np.load(f) for f in files])

# ... read /work/inputs/weights.json (absent on round 0), train, write
#     /work/out/delta.json — the federated contract above, unchanged ...

json.dump(
    {"samples": int(len(data)), "loss": float(loss), "chunks_done": [a.shard]},
    open("/work/out/metrics.json", "w"),
)
```

**A note on file formats.** Most Hub datasets are Parquet, and none of the
curated images can read it — `pytorch-cpu` gives you torch and numpy, and
neither speaks Parquet. Declaring a dataset does not lift the image ceiling
described above, so today that means shipping your shards as something a
curated image can open (`.npy`, `.npz`, `.csv`, text) and, ideally, many
roughly-equal files rather than three big ones.

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

## Not losing the work: checkpoints

`allow_partial` below decides what happens to a **job** when a task is lost.
Checkpoints decide how much of the **task** is lost in the first place.

Every task runs with the checkpoint relay on. There is no `checkpoint:` key,
nothing to switch on and nothing to configure — the relay is always
watching, and the only question is whether your code gives it anything to
watch. If it does not, a machine that gets closed or reclaimed takes the
whole task with it and the retry starts again from step 0, however far the
first attempt got. Preflight warns about this at submit time
(`no-checkpoint`), because nothing else in the product would.

Two halves. Both are required, and both paths are fixed.

**Write your state to `/work/out/ckpt/step-<N>.json`.** That directory, that
filename, `N` the integer step you reached, one file per checkpoint. The
relay globs `step-*.json` in that one directory and ships each new file off
the machine the moment it appears. Anything else — including
`/work/out/checkpoints/`, which is the plausible spelling — is not a
checkpoint. It is collected as an ordinary artifact when the task finishes,
and lost with the machine if it does not.

**Read `/work/inputs/resume.json` on start.** When a previous attempt died,
the agent stages that attempt's last committed checkpoint there before your
code runs. On a first attempt the file is simply absent, so "no such file"
means "start from scratch", not an error.

```python
ckpt_dir = Path(args.out) / "ckpt"          # args.out is /work/out
ckpt_dir.mkdir(parents=True, exist_ok=True)

start_step = 0
resume = Path("/work/inputs/resume.json")
if resume.exists():                          # absent on a first attempt
    state = json.loads(resume.read_text())
    start_step = state["step"]

for step in range(start_step, total_steps):
    ...
    if step % 100 == 0:
        write_atomically(ckpt_dir / f"step-{step}.json", state)
```

### Write it atomically

The relay ships whatever it finds, and it can find your file a millisecond
after you create it. A checkpoint caught half-written is a checkpoint
nothing can resume from — and the run it kills is the *next* one, which
makes it a bug you discover long after you wrote it.

Write to a temp file **in the same directory**, then `os.replace` it into
place:

```python
def write_atomically(path: Path, state: dict) -> None:
    tmp = path.with_name(f".{path.name}.tmp")   # same dir; not a step-*.json
    tmp.write_text(json.dumps(state))
    os.replace(tmp, path)     # atomic within one filesystem
```

Two details that are easy to get wrong: rename is only atomic *within* a
filesystem, so the temp file has to be next to the target and not in
`/tmp`; and the temp name must not itself match `step-*.json`, or the relay
will ship the half-written file you were trying to hide.

`e2e/competition/workload_common.py` has the full-strength version — it
fsyncs the file before the rename and the directory after it, so the
checkpoint survives a power cut and not just a killed process.

### `flashruntime.torch.checkpoint()` is not this

It is the closest thing to a trap in the system, because it gets the
directory *right*. Its root defaults to `<output dir>/ckpt` — exactly where
the relay is looking — and then it writes `step-000100/` **directories**
holding `model.pt` and `optimizer.pt`. The relay globs `step-*.json`
**files**. So reaching for the runtime's own helper lands you in the right
place in a shape nothing can see: none of it is ever shipped, no error is
raised, and the first sign is a retry that starts from zero.

If you call it, you still have to write your own `step-<N>.json` beside it.

### Checkpoint small resumable state, not the model

A task's whole output directory is capped at **2 GiB**, and checkpoints sit
inside it and count against that cap. They also cross the network twice on
a task that succeeds — once from the relay while you are running, once as
part of the final output.

So checkpoint what you need to *resume*: the step, the optimizer state, the
RNG state, and the weights only if they are small. A 1.5 GiB snapshot every
hundred steps does not make the task likelier to survive — it fails the
output cap and saturates a volunteer's uplink on the way there.

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
