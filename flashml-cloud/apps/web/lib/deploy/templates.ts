/** The five demo workloads, as `flashml.yaml` text the Deploy gallery hands
 * to the submit flow.
 *
 * PROVENANCE — copied 2026-08-13 from the public `flashml` repo,
 * `examples/demo-suite/{train,hpo,federated,evaluate,gpu-train}/flashml.yaml`,
 * **verbatim, comments included**. That repository remains the SOURCE OF
 * TRUTH: these strings are a copy for prefilling a textarea, never a second
 * definition of the examples. If a template here disagrees with the file it
 * was copied from, the file is right and this module is stale.
 *
 * The comments are copied along with the keys deliberately. Each of these
 * files spends most of its lines explaining why a key is written the way it
 * is — why `sklearn` and not `pytorch-cpu`, why there is no `checkpoint:`
 * key, why `shards` and `min_participants` no longer exist. Stripping them
 * would leave a reader a config they can run and cannot change.
 *
 * WHY THE YAML IS NOT FETCHED. It could be read from GitHub at render time
 * and always be current. It is not, because the gallery would then need the
 * network to draw a card, would show five spinners on a screen whose whole
 * job is "pick one and go", and would break entirely for a console served
 * where raw.githubusercontent.com is not reachable. A stale string is a
 * worse example; a gallery that cannot render is a worse product.
 *
 * WHAT IS NOT HERE: the entrypoints. Each of these five directories also
 * carries a `train.py` / `trial.py` / `evaluate.py`, and a job is the config
 * AND the code. See `TEMPLATE_SUBMIT_GAP` below for what that means for the
 * Deploy button, which is a fact about this API rather than about this file.
 */

/** The seven shapes the router can tell apart, uppercased.
 *
 * These are the member names of `WorkloadKind` in the API's
 * `flashml_cloud_api/router/workload.py`, which is the only place the
 * vocabulary is defined. The wire values are lowercase (`kind: "hpo"` is
 * what `preview-plans` returns and what `RoutingCard` renders verbatim); the
 * uppercase spellings are how a tag is set on a card, matching the demo
 * suite's own README table.
 *
 * Restated rather than imported because nothing crosses from the API into
 * the console at build time — but restated as a CLOSED list with a test, so
 * a tag invented for a card is a test failure rather than a chip nobody
 * notices is fiction. */
export const WORKLOAD_TAGS = [
  "HPO",
  "TRAINING",
  "FINETUNE",
  "INFERENCE",
  "EVALUATION",
  "FEDERATED",
  "COMMAND",
] as const;

export type WorkloadTag = (typeof WORKLOAD_TAGS)[number];

/** The lowercase wire value the router answers with for a tag, so a card's
 * chip and a job's `kind` can be compared without a second mapping. */
export function workloadKind(tag: WorkloadTag): string {
  return tag.toLowerCase();
}

/** Whether a template's tasks run on a card or not.
 *
 * A property of the config, not a preference: `gpu-train` is the only one of
 * the five that writes `resources: {gpus: 1}`, and that single line is what
 * makes it TRAINING and what makes a GPU-less venue refuse it by name. */
export type TemplateHardware = "CPU" | "GPU";

export interface DeployTemplate {
  /** The demo-suite directory this came from. Also the gallery's React key
   * and the value the submit page holds while a template is loaded. */
  id: string;
  /** What the card is called. Short — the tag and the line under it carry
   * the detail. */
  name: string;
  /** How the router classifies this job. See `WORKLOAD_TAGS`. */
  tag: WorkloadTag;
  /** One line, compressed from the example's own README. What the workload
   * DEMONSTRATES, never what it trains — none of these five produce a
   * result worth having, and saying otherwise would oversell a demo. */
  demonstrates: string;
  /** How many tasks the spec expands to, when that is a property of the
   * config rather than of the fleet.
   *
   * `null` for `federated` and it is not an omission: a federated round is
   * cut into as many chunks as the machines that show up can finish, so the
   * task count is decided at run time by the fleet. A number here would be
   * a guess rendered in the same typeface as the four real ones. */
  tasks: number | null;
  /** Said in place of a task count where there is none. `null` where
   * `tasks` carries the answer. */
  tasksNote: string | null;
  hardware: TemplateHardware;
  /** The file this text was copied from, relative to the public repo root. */
  source: string;
  /** The `flashml.yaml`, exactly as the source file has it. */
  yaml: string;
}

const TRAIN_YAML = `# One task, checkpointed and resumable — the fault-tolerance story.
#
# version 1, not 2: the bump exists only for \`mode: federated\`, and this is
# an ordinary independent job. Sweeps and single tasks stay on 1.
version: 1
name: demo-train

# \`sklearn\` rather than \`pytorch-cpu\`, and the reason is the demo fleet
# rather than the model: those machines are arm64 and the curated images are
# amd64, so every task runs under qemu emulation. numpy ships in \`sklearn\`,
# \`pytorch-cpu\` and \`pytorch-cuda\` alike; torch does not need emulating here
# to make the point, so it is not imported. \`examples/federated/\` is the
# torch spelling of the same idea.
image: sklearn
entrypoint: train.py

# Passed through verbatim, ahead of anything FlashML appends. Deliberately
# tiny: this suite demonstrates a working loop, not a result.
args: ["--epochs", "10", "--lr", "0.5", "--hidden", "8", "--batch-size", "128"]

# The data. \`name\` becomes the directory the task reads (/work/data/demo/);
# \`source\` is resolved ONCE at submit time and pinned, so publishing to the
# bucket mid-run cannot change what this job trains on.
#
# \`split\` is not written here because it is inferred: a non-federated job
# infers \`replica\` — every task gets the whole listing. With one task that
# is the whole dataset, holdout shard included, which is why train.py
# excludes the holdout by name rather than trusting the slice.
#
# We never store your bytes. The control plane reads the manifest and hands
# each machine a list of URLs; the shards go from the origin straight to the
# machine that needs them.
datasets:
  - name: demo
    source: https://zolli-flashml-datasets.oss-ap-southeast-1.aliyuncs.com/datasets/mlp-demo/manifest.json

# There is deliberately no \`checkpoint:\` key, and adding one is refused with
# an explanation rather than ignored. Checkpointing is unconditional: every
# compiled job carries it. What decides whether this task is resumable is
# entirely in train.py — /work/out/ckpt/step-<N>.json out, and
# /work/inputs/resume.json in.

# Generous: a volunteer's first task also pays for the image pull, and under
# qemu emulation everything costs several times its native price.
timeout_seconds: 900
`;

const HPO_YAML = `# A hyperparameter search: two axes, six independent trials, one winner.
#
# This is the shape a pull fleet of ordinary machines is strictly good at —
# many short trials that never have to talk to each other. It is also the
# shape the router has to recognise, because kind decides which venues the
# work is offered to before price is considered at all.
version: 1
name: demo-hpo

# numpy is what this needs, and \`sklearn\` carries it. See ../train/ for why
# the suite avoids torch on the demo fleet.
image: sklearn
entrypoint: trial.py

# Fixed for every trial. The swept axes are appended by FlashML on top of
# these — do not name \`--lr\` or \`--hidden\` here, or each task would receive
# the flag twice.
args: ["--epochs", "8", "--batch-size", "128"]

# Two axes, 3 x 2 = 6 trials. Each key becomes a CLI flag, so trial.py sees
# \`--lr 0.3 --hidden 16\`. Keys must be plain identifiers: they are both a
# flag name and a substitution field.
#
# Six is deliberate. The cap is 100 combinations, and a sweep that large on
# a demo fleet under qemu emulation would take an afternoon to say something
# these six already say.
sweep:
  lr: [0.1, 0.3, 1.0]
  hidden: [4, 16]

# Every trial gets the whole dataset — \`split: replica\` is inferred for a
# non-federated job — and decides for itself which shards are training data
# and which is the holdout. See trial.py.
datasets:
  - name: demo
    source: https://zolli-flashml-datasets.oss-ap-southeast-1.aliyuncs.com/datasets/mlp-demo/manifest.json

# What a valid result is. A trial whose metrics.json arrives intact and
# hashes correctly but carries no \`accuracy\` fails its attempt and is
# retried elsewhere. Without this, "the file arrived" is the only check
# there is — and a trial that crashed after writing half its numbers would
# rank silently at the bottom.
validators:
  keys: [accuracy]

# What makes this a SEARCH rather than a fan-out: the trials are being
# selected between. The console names the winner and shows the full
# ordering; the classifier quotes this line back as its evidence.
reduce:
  kind: rank
  metric: accuracy
  maximize: true

# One closed laptop must not discard the other five trials' work. The job
# then finishes as PARTIAL — a distinct terminal state, deliberately not
# "succeeded".
allow_partial: true

timeout_seconds: 900
`;

const FEDERATED_YAML = `# Federated averaging: one model, several machines, a different shard each.
#
# version 2, not 1, because \`mode: federated\` is the thing that changed. A
# version-1 federated config typed \`rounds\`, \`min_participants\` and
# \`shards\`; none of the three survives, so the platform refuses them with
# the migration rather than guessing what you meant.
version: 2
name: demo-federated

# numpy only — see ../train/ for why this suite avoids torch on the demo
# fleet. \`examples/federated/\` is the torch spelling of the same protocol.
image: sklearn
entrypoint: train.py

# FlashML appends --round, --num-shards and --shard on top of these.
args: ["--epochs", "4", "--lr", "0.5", "--hidden", "8"]

# \`federated\` asks the platform to average your model across machines every
# round. It is a contract, not a flag: train.py has to read
# /work/inputs/weights.json and write /work/out/delta.json for it to mean
# anything, and preflight refuses the job outright if the entrypoint never
# mentions them. Without this key, \`mode\` defaults to \`independent\` and the
# tasks would run once, in parallel, exchanging nothing.
mode: federated

# How much training, in passes over the data. Four, because the point is
# that the loss moves between rounds, not where it ends up.
epochs: 4

# Passes between combines. 1.0 — the default, and the only value that works
# today — is one combine per pass. Round count is derived (epochs /
# sync_every) and shown in the console; you never type it.
sync_every: 1.0

# \`split: shard\` is INFERRED from \`mode: federated\` — each machine gets a
# different slice of the listing, and their union is one pass. That is the
# whole difference from the other three workloads in this suite, which infer
# \`replica\` and each get everything.
#
# Note the ceiling this puts on the fleet: the pass cannot be cut into more
# chunks than the dataset has files, so a dataset of nine files is nine
# machines at most however large the Crew is. The split also balances BYTES
# rather than file count, so one dominant file can strand machines even when
# there are as many files as chunks.
datasets:
  - name: demo
    source: https://zolli-flashml-datasets.oss-ap-southeast-1.aliyuncs.com/datasets/mlp-demo/manifest.json

# There is deliberately no shard count and no quorum here. \`shards\` was a
# guess about the fleet made before submitting, by the person with the least
# information about it; the platform now cuts a pass into chunks and hands
# each machine that shows up as many as it can finish. \`min_participants\`
# went with it: a round closes when the chunks that came back COVER
# \`sync_every\` of a pass, which is the property a headcount was standing in
# for and cannot be satisfied by one fast machine reporting first.

# Per-round wall clock. A volunteer's first task also pays for the image
# pull, and everything on the demo fleet runs under qemu emulation.
timeout_seconds: 900
`;

const EVALUATE_YAML = `# Score a trained model against the held-out shard.
#
# The one workload here that produces a number instead of a model. The
# router reads that off the \`validators:\` line below and labels the job
# EVALUATION in the console, with the evidence quoted back.
version: 1
name: demo-evaluate

image: sklearn
entrypoint: evaluate.py

# --hidden only matters when there is no model.json to load, in which case
# this scores the untrained initialisation as a floor. See evaluate.py.
args: ["--hidden", "8"]

# \`split: replica\` is inferred, so this single task receives the whole
# listing and picks the holdout shard out of it by name. The training
# shards arrive too and are ignored — a few hundred kilobytes, and not
# worth a \`select:\` glob that would have to be kept in step with the
# dataset's filenames.
datasets:
  - name: demo
    source: https://zolli-flashml-datasets.oss-ap-southeast-1.aliyuncs.com/datasets/mlp-demo/manifest.json

# What a valid result is. An attempt whose metrics.json arrives intact but
# carries no \`accuracy\` fails and is retried elsewhere. It is also the whole
# of this job's kind signal: tasks that emit scores rather than weights.
validators:
  keys: [accuracy]

# There is deliberately no \`reduce:\` here. One task's metrics.json IS the
# answer, and \`reduce: {kind: aggregate, metric: accuracy}\` over a single
# task would report a mean of one number. Add it the day this fans out over
# several holdout shards — it also changes the console's evidence sentence,
# from "the tasks emit scores rather than weights" to "the job's result is a
# score combined from its tasks' outputs".

timeout_seconds: 900
`;

const GPU_TRAIN_YAML = `# One GPU task, checkpointed and resumable — the routing story.
#
# version 1, not 2: the bump exists only for \`mode: federated\`, and this is
# an ordinary independent job. Sweeps and single tasks stay on 1.
version: 1
name: demo-gpu-train

# \`pytorch-cuda\`, and here the image is a decision rather than a detail.
#
# It is the only curated image built on a CUDA base, so it is the only one
# whose torch can see a card — the other three would import torch (or not)
# and run the whole thing on the CPU while every log line still said
# "training". It is also the image whose requirements.txt carries
# \`--index-url https://download.pytorch.org/whl/cu124\`, which is what an
# UNSANDBOXED host installs from when it reproduces this environment
# without running a container. See README.md, "The unsandboxed host".
#
# It is several gigabytes and shares no layer with the other three curated
# images. A host only ever pulls it after being placed a \`gpus: N\` task, so
# a CPU-only volunteer never sees it.
image: pytorch-cuda
entrypoint: train.py

# THE LINE THIS WORKLOAD EXISTS FOR.
#
# \`resources.gpus >= 1\` on a job with no sweep and no partition is the only
# way a flashml.yaml can say "this is training". The console classifies it
# TRAINING rather than COMMAND, and every venue is then asked whether it
# fits: a machine with no card is refused with a reason naming the missing
# hardware, before any price is computed. The other four workloads in this
# suite are all CPU-only and all classify as something else — see
# ../README.md's table, which says in as many words that a CPU training run
# has no way to declare itself training.
#
# An integer, and non-negative rather than positive: \`gpus: 0\` is a
# meaningful statement ("no card needed") and is not this job. \`gpus: true\`
# is refused rather than read as one card, and a float is refused rather
# than rounded.
resources:
  gpus: 1

# Passed through verbatim, ahead of anything FlashML appends. Deliberately
# tiny: this demonstrates where the work is placed and that it survives an
# interruption, not how fast a card is. See README.md, "Not a benchmark".
args: ["--epochs", "8", "--lr", "0.5", "--hidden", "64", "--batch-size", "128"]

# The same dataset the other four workloads declare, resolved ONCE at submit
# time and pinned. \`name\` becomes the directory the task reads
# (/work/data/demo/); the shards land one level down as train/*.npz and
# holdout/eval.npz.
#
# \`split\` is not written here because it is inferred: a non-federated job
# infers \`replica\` — every task gets the whole listing, holdout shard
# included — which is why train.py excludes the holdout by path rather than
# trusting the slice.
#
# We never store your bytes. The control plane reads the manifest and hands
# each machine a list of URLs; the shards go from the origin straight to the
# machine that needs them.
datasets:
  - name: demo
    source: https://zolli-flashml-datasets.oss-ap-southeast-1.aliyuncs.com/datasets/mlp-demo/manifest.json

# There is deliberately no \`checkpoint:\` key, and adding one is refused with
# an explanation rather than ignored. Checkpointing is unconditional: every
# compiled job carries it. What decides whether this task is resumable is
# entirely in train.py — /work/out/ckpt/step-<N>.json out, and
# /work/inputs/resume.json in.

# There is deliberately no \`dependencies:\` key either, and that one took
# checking rather than taste: \`pytorch-cuda\` is a curated image, so the
# compiler resolves its requirements.txt as the job's dependency base and an
# unsandboxed host installs exactly that. Declaring extras on top would also
# emit \`extra_dependencies\`, which the coordinator's placement gate reads as
# "this job needs a host that can install" — routing it AWAY from the GPU
# container hosts that run it correctly today. README.md records what the
# base actually resolved to.

# The training itself is seconds. This budget is for everything around it on
# a machine seeing this job for the first time: on a container host, pulling
# several gigabytes of CUDA image; on an unsandboxed host, a torch cu124
# wheel and CUDA runtime libraries. Neither is the model's fault and both
# are paid once per machine.
timeout_seconds: 1800
`;

/** The gallery, in the order it is drawn.
 *
 * The order is the demo suite's own: the fault-tolerance story first,
 * because it is the one claim this product is actually built on; the two
 * fan-out shapes next; then the two that need something the CPU fleet does
 * not have on its own — a model to score, and a card. */
export const DEPLOY_TEMPLATES: readonly DeployTemplate[] = [
  {
    id: "train",
    name: "Train",
    tag: "COMMAND",
    demonstrates:
      "One task, killable at any point and resumed elsewhere — the model it finally writes is byte-for-byte the uninterrupted one.",
    tasks: 1,
    tasksNote: null,
    hardware: "CPU",
    source: "examples/demo-suite/train/flashml.yaml",
    yaml: TRAIN_YAML,
  },
  {
    id: "hpo",
    name: "HPO sweep",
    tag: "HPO",
    demonstrates:
      "Two axes, six short trials that never have to reach each other, ranked into one winner.",
    tasks: 6,
    tasksNote: null,
    hardware: "CPU",
    source: "examples/demo-suite/hpo/flashml.yaml",
    yaml: HPO_YAML,
  },
  {
    id: "federated",
    name: "Federated",
    tag: "FEDERATED",
    demonstrates:
      "Every machine trains a different shard alone; the platform averages the changes between rounds. Weights cross the network once per round, not once per batch.",
    tasks: null,
    tasksNote: "4 rounds · one shard per machine",
    hardware: "CPU",
    source: "examples/demo-suite/federated/flashml.yaml",
    yaml: FEDERATED_YAML,
  },
  {
    id: "evaluate",
    name: "Evaluate",
    tag: "EVALUATION",
    demonstrates:
      "The one workload here that produces a number instead of a model, scored against the held-out shard.",
    tasks: 1,
    tasksNote: null,
    hardware: "CPU",
    source: "examples/demo-suite/evaluate/flashml.yaml",
    yaml: EVALUATE_YAML,
  },
  {
    id: "gpu-train",
    name: "GPU train",
    tag: "TRAINING",
    demonstrates:
      "The same shape as Train, on a real card. Asking for one GPU is the only way a config can say this is training, and a venue with no card is refused by name before any price is computed.",
    tasks: 1,
    tasksNote: null,
    hardware: "GPU",
    source: "examples/demo-suite/gpu-train/flashml.yaml",
    yaml: GPU_TRAIN_YAML,
  },
];

/** One template by its id, or `undefined`.
 *
 * The submit page keeps the SELECTED ID in state rather than the template
 * object, and calls this to get the record back. One copy of a template, in
 * the registry: a second copy held in component state is how a card ends up
 * labelled one thing while the editor holds another. */
export function templateById(id: string | null): DeployTemplate | undefined {
  if (!id) return undefined;
  return DEPLOY_TEMPLATES.find((t) => t.id === id);
}

/** The card's second line: what this costs a fleet to run, in the two terms
 * a reader can act on before they know anything else.
 *
 * Pure, and it never invents the task count it does not have — `federated`
 * reports its rounds and its shard rule instead, because the number of tasks
 * is decided by the machines that turn up. */
export function templateMeta(template: DeployTemplate): string {
  const work =
    template.tasks == null
      ? (template.tasksNote ?? "")
      : `${template.tasks} task${template.tasks === 1 ? "" : "s"}`;
  return `${work} · ${template.hardware}`;
}

/** Why a prefilled `flashml.yaml` is not a submitted job on this API, said
 * once, where both the panel and a test can read it.
 *
 * THE FACT: every route that creates a job takes a whole working tree —
 * `POST /v1alpha1/jobs/from-repo` fetches a repository, `from-upload` takes
 * a tarball — because a job is the config AND the entrypoint the config
 * names. There is no route that accepts `flashml.yaml` text and returns a
 * job, so the console cannot offer one, and a Deploy button that appeared to
 * submit would be a button that lies.
 *
 * What the console CAN do with the text is exactly what
 * `POST /v1alpha1/preflight` does: parse it, resolve its image and run the
 * same checks `from-repo` refuses on, creating nothing. That is what the
 * yaml tab's action is wired to. */
export const TEMPLATE_SUBMIT_GAP =
  "A config is only half a job — the other half is the entrypoint it names. Checking it here creates nothing; running it means pushing the directory this came from and submitting it as a repo.";
