#!/usr/bin/env bash
#
# The recovery beat, against a real coordinator and two real host agents.
#
#     e2e/competition/run_local_recovery.sh
#     e2e/competition/run_local_recovery.sh --quick       # smaller, ~20s of work
#     e2e/competition/run_local_recovery.sh --keep        # leave it up to poke at
#
# Submit training → wait for a COMMITTED checkpoint → make the first worker
# vanish → watch its lease expire on its own deadline → watch a second node
# restore from the checkpoint → assert the final model hash equals an
# uninterrupted run's.
#
# ---------------------------------------------------------------------------
# WHY `kill -9` ON THE PROCESS GROUP, AND NOT Ctrl-C
# ---------------------------------------------------------------------------
#
# `ExecutorLoop.run()` tests `stop_event` only at the top of the claim loop,
# and `execute_one(lease)` runs inside it. So a SIGINT drains *gracefully*:
# the task finishes, commits, and the agent exits — no recovery happens at
# all. Ctrl-C at a terminal is worse than useless here for a second reason:
# it signals the whole foreground process group, killing the training child
# too, and the agent then REPORTS the failure with `fail()`.
#
# Both are the easy case. Somebody announced the death, the coordinator is
# told, and the task requeues immediately. The demo needs the hard case — the
# machine vanishes, nobody tells anyone, and the lease has to expire on its
# own deadline before anyone may touch the task. That is the property worth
# proving, because it is the one that holds when a volunteer closes a laptop
# lid in another timezone.
#
# `kill -9` on the process group is how you get it: SIGKILL cannot be caught,
# so no `fail()` is sent and no heartbeat is stopped politely — it simply
# stops arriving. The GROUP rather than the pid because the agent has a
# training child holding the workdir, and killing only the parent would leave
# it running and still writing checkpoints for a lease nobody holds.
#
# Each agent is therefore started with `setsid` (via the interpreter, which is
# portable where the `setsid` binary is not), so its pid is also its process
# group id and `kill -9 -$pid` is exactly its own subtree.
#
# ---------------------------------------------------------------------------
# WHY THE JOBSPEC IS BUILT HERE AND NOT COMPILED FROM flashml.train.yaml
# ---------------------------------------------------------------------------
#
# WHAT THIS REHEARSES, AND WHAT IT DOES NOT
#
# flashnode gates BOTH the checkpoint relay and the `resume.json` staging on
# `payload["checkpoint"]` being non-None (`flashnode/executor/loop.py`), and
# `CommandRecipe.expand` forwards that workload parameter verbatim.
#
# Until 2026-08-11 nothing in the cloud ever set it: it was absent from
# `flashml_yaml.ALLOWED_KEYS` and `compile.py` never emitted it, so a repo job
# authored through the console trained, checkpointed to its own disk, and lost
# all of it when the machine died. **That is fixed** — both compilers now emit
# `parameters["checkpoint"] = {}` unconditionally, and there is deliberately
# no `checkpoint:` key to author (the relay's directory and glob are hardcoded
# in the agent, so a key could only accept values nobody reads).
#
# This script still hand-builds its JobSpec and posts it STRAIGHT TO THE
# COORDINATOR, for one reason only: the e2e venv installs the pinned public
# artifacts and cannot import `flashml_cloud_api`, so there is no compiler
# here to call. Everything else about the spec is what `compile_to_jobspec`
# produces — and `checkpoint` is now written as the SAME `{}` the compiler
# emits, so what this rehearses is byte-identical to what a real job carries.
#
# The gap that remains: this proves the RUNTIME resumes. It does not exercise
# the authoring surface, which is exactly how the bug above survived a green
# `e2e/test_training_resume.py` for months — that test hand-builds its spec
# too. A test that would have caught it has to go THROUGH `compile_to_jobspec`,
# not around it. `apps/api/tests/test_compile.py` now does.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
E2E="$(cd "$HERE/.." && pwd)"
PY="$E2E/.venv/bin/python"
FLASHNODE="$E2E/.venv/bin/flashnode"

# THE DEFAULT IS THE DEMO SIZING — the argv `flashml.train.yaml` declares, and
# the one every timing claim in the runbook is measured against. Measured at
# 37.0 s and 60 checkpoints on an M-series laptop under CPython 3.12.
#
# Rehearsing at a smaller size was tried and abandoned: 24 epochs x 1800
# samples finishes in 1.8 s, which is faster than one claim/poll cycle, so the
# worker was gone before the kill could land and the run proved nothing. A
# recovery rehearsal has to be slower than the machinery it is rehearsing.
# `--quick` is the smallest sizing that still comfortably loses that race.
EPOCHS=60; SAMPLES=8000; FEATURES=24; HIDDEN=64; BATCH=24
KILL_AFTER=25         # committed checkpoints before the worker vanishes
# The deadline the vanished lease must expire on, and the slowest part of this
# script. It cannot go below 30: `flashruntime.envelope.MIN_COMPUTE_SECONDS` is
# a hard floor and `CommandRecipe.expand` refuses a shorter one with a 422 —
# "caps compute below the 30s floor". Anything under it would also be a lie
# about the demo, where the interesting number is how long a task sits
# unclaimable after a machine stops answering.
LEASE_SECONDS=40
KEEP=0
POOL="competition-demo"

usage() {
    sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --demo)  shift ;;   # the default; accepted so the runbook can be explicit
        --quick) EPOCHS=45; SAMPLES=6000; HIDDEN=56; KILL_AFTER=10; shift ;;
        --epochs) EPOCHS="$2"; shift 2 ;;
        --samples) SAMPLES="$2"; shift 2 ;;
        --hidden) HIDDEN="$2"; shift 2 ;;
        --kill-after) KILL_AFTER="$2"; shift 2 ;;
        --lease-seconds) LEASE_SECONDS="$2"; shift 2 ;;
        --keep)  KEEP=1; shift ;;
        -h|--help) usage 0 ;;
        *) echo "unknown argument: $1" >&2; usage 2 ;;
    esac
done

[ -x "$PY" ] || { echo "missing $PY — run 'make e2e-setup' first" >&2; exit 1; }
[ -x "$FLASHNODE" ] || { echo "missing $FLASHNODE — run 'make e2e-setup' first" >&2; exit 1; }
# Refused here rather than as a 422 forty lines later, where it reads as a
# platform fault instead of the one number the caller chose.
[ "$LEASE_SECONDS" -ge 30 ] || {
    echo "--lease-seconds must be >= 30 (flashruntime.envelope.MIN_COMPUTE_SECONDS);" >&2
    echo "CommandRecipe refuses anything shorter." >&2
    exit 2
}

RUN="$(mktemp -d "${TMPDIR:-/tmp}/flashml-recovery-XXXXXX")"
PIDS=()

cleanup() {
    local status=$?
    if [ "$KEEP" = "1" ] && [ "$status" = "0" ]; then
        echo
        echo "▶ --keep: coordinator still at $BASE/  (state: $RUN)"
        echo "  stop it with: kill ${PIDS[*]:-}"
        return
    fi
    # Groups, not pids: every child here was started with its own session, and
    # a bare `kill` would orphan any training subprocess still running.
    for pid in "${PIDS[@]:-}"; do
        [ -n "$pid" ] && kill -9 -"$pid" 2>/dev/null || true
    done
    [ "$KEEP" = "1" ] || rm -rf "$RUN"
}
trap cleanup EXIT

# --- tiny HTTP/JSON helpers -------------------------------------------------
#
# curl for transport, the venv interpreter for JSON. No `jq` dependency: this
# script has to run on a machine that was set up for the demo and nothing else.

jkey() {  # …| jkey a b c   → prints d["a"]["b"]["c"], or "" for any miss
    # An empty or non-JSON body prints "" rather than raising. This is called
    # in poll loops against endpoints that legitimately answer 404 until the
    # thing exists (there is no checkpoint before the first one is committed),
    # and a traceback per half-second buries whatever actually went wrong.
    "$PY" -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)
for k in sys.argv[1:]:
    if isinstance(d, list):
        d = d[int(k)] if k.lstrip("-").isdigit() and abs(int(k)) < len(d) + 1 else None
    elif isinstance(d, dict):
        d = d.get(k)
    else:
        d = None
    if d is None:
        break
print("" if d is None else d)
' "$@"
}

get() { curl -fsS --max-time 15 "$BASE$1"; }

# NOT `curl -f`: a refused JobSpec answers 422 with the reason in the body,
# and `-f` throws exactly that body away. Every 422 this script has produced
# so far was a one-line fix hiding behind a "(22) error 422".
post_json() {
    local response http body
    response="$(curl -sS --max-time 30 -w $'\n%{http_code}' \
        -X POST -H 'Content-Type: application/json' \
        --data-binary "@$2" "$BASE$1")"
    http="${response##*$'\n'}"
    body="${response%$'\n'*}"
    case "$http" in
        200|201) printf '%s' "$body" ;;
        *) echo "coordinator refused POST $1 (HTTP $http):" >&2
           echo "$body" >&2
           return 1 ;;
    esac
}

# The single task's row, as "state attempts node_id".
#
# Tolerant of an empty or unparseable body on purpose: this is called in a
# poll loop, and a coordinator that is momentarily busy must produce one more
# "?" line rather than a traceback per second that buries the real failure.
task_row() {
    get "/v1alpha1/jobs/$JOB/tasks" 2>/dev/null | "$PY" -c '
import json, sys
try:
    rows = json.load(sys.stdin)
except Exception:
    rows = []
row = rows[0] if rows else {}
print(row.get("state", "?"), row.get("attempts", 0), row.get("node_id") or "-")
' || echo "? 0 -"
}

# Every attempt of this task that failed, and why. Called on any failure path.
#
# The agent logs are the only place a task's own stderr survives a run this
# script tore down, and every failure so far has been one line in them
# ("can't open file …/train_checkpointed.py"). Printing them is the difference
# between a five-second diagnosis and re-running the whole rehearsal blind.
diagnose() {
    echo
    echo "--- what the coordinator saw ---" >&2
    get "/v1alpha1/jobs/$JOB/events" 2>/dev/null | "$PY" -c '
import json, sys
try:
    events = json.load(sys.stdin)
except Exception:
    events = []
for e in events:
    detail = (e.get("detail") or "")[:200]
    print("  %-28s %s" % (e.get("type"), detail), file=sys.stderr)
' || true
    for name in "${AGENT_NAMES[@]}"; do
        echo "--- $name ---" >&2
        grep -a "task failed\|error" "$RUN/$name/log" 2>/dev/null | tail -5 >&2 || true
    done
}

committed_step() {
    curl -fsS --max-time 15 \
        "$BASE/v1alpha1/jobs/$JOB/tasks/task-000/checkpoints/latest" 2>/dev/null \
        | jkey step || true
}

event_count() {  # how many events of a given type the JOB's ledger holds
    get "/v1alpha1/jobs/$JOB/events" 2>/dev/null | "$PY" -c '
import json, sys
try:
    events = json.load(sys.stdin)
except Exception:
    events = []
print(sum(1 for e in events if e.get("type") == sys.argv[1]))
' "$1" || echo 0
}
# NOTE, because it cost a debugging round and will cost the next person one:
# CHECKPOINT_MANIFEST_COMMITTED is NOT in that feed. `CheckpointCatalog` is
# addressed by a composite scope — `service/checkpoints._scope` builds
# `"<job_id>::<task_id>"` — and emits its events under that string as their
# job_id, so they never appear in `GET /v1alpha1/jobs/<job_id>/events`.
# Checkpoint progress is observable only through the checkpoints endpoint
# below, which is what `committed_step` polls.

step() { printf "\n\033[1m▶ %s\033[0m\n" "$*"; }

# `\r` redraws one line on a terminal and produces one unreadable 4000-column
# line in a pipe or a log file. The demo is watched live and the CI capture is
# read afterwards, so both have to be legible.
if [ -t 1 ]; then EOL=$'\r'; else EOL=$'\n'; fi
progress() { printf "  … %s%s" "$*" "$EOL"; }

# --- 0. the uninterrupted baseline -----------------------------------------
#
# Computed FIRST and locally, before anything distributed exists. It is the
# thing the whole run is asserted against, and a baseline produced by the same
# machinery under test would prove only that the machinery is consistent.

step "baseline: one uninterrupted run of the same workload, locally"
BASELINE_OUT="$RUN/baseline/out"
"$PY" "$HERE/train_checkpointed.py" \
    --out "$BASELINE_OUT" --resume "$RUN/baseline/inputs/resume.json" \
    --epochs "$EPOCHS" --samples "$SAMPLES" --features "$FEATURES" \
    --hidden "$HIDDEN" --batch-size "$BATCH" | tail -1
BASELINE_SHA="$("$PY" -c '
import json, sys
print(json.load(open(sys.argv[1]))["model_sha256"])
' "$BASELINE_OUT/metrics.json")"
echo "  baseline model sha256: $BASELINE_SHA"

# --- 1. coordinator ---------------------------------------------------------

step "coordinator"
PORT="$("$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
BASE="http://127.0.0.1:$PORT"

# cwd is the state dir, never the repo: running from a checkout lets the
# source *directories* shadow the installed packages on sys.path.
mkdir -p "$RUN/coordinator"
env FLASHML_ENABLE_KUBERAY=0 \
    FLASHML_SERVICE_AUTOINIT=1 \
    FLASHML_LEDGER_PATH="$RUN/coordinator/ledger.db" \
    FLASHML_LOCAL_ARTIFACTS_DIR="$RUN/artifacts" \
    "$PY" -c 'import os, sys; os.setsid(); os.execv(sys.argv[1], sys.argv[1:])' \
    "$PY" -m uvicorn flashruntime.service.app:app \
    --host 127.0.0.1 --port "$PORT" --log-level warning \
    >"$RUN/coordinator/log" 2>&1 &
PIDS+=("$!")
# `disown` so bash does not announce "Killed: 9" for a death this script
# performed on purpose. The process group id is already captured, so nothing
# about cleanup or the kill depends on bash still tracking the job.
disown %% 2>/dev/null || true

for _ in $(seq 1 100); do
    curl -fsS --max-time 2 "$BASE/healthz" >/dev/null 2>&1 && break
    sleep 0.2
done
curl -fsS --max-time 2 "$BASE/healthz" >/dev/null || {
    echo "coordinator never became healthy; log:" >&2
    cat "$RUN/coordinator/log" >&2
    exit 1
}
echo "  up — dashboard: $BASE/"

# --- 2. stage the workload as a repo tarball --------------------------------
#
# A single wrapper directory, exactly like GitHub's codeload tarballs, so the
# agent's wrapper-stripping path (`_staged_directory` → `extract_archive_
# safely`) is the one this rehearsal exercises rather than a shape only this
# script produces.

step "staging the workload"
mkdir -p "$RUN/pkg/competition"
cp "$HERE/train_checkpointed.py" "$HERE/evaluate_model.py" \
   "$HERE/workload_common.py" "$RUN/pkg/competition/"
# COPYFILE_DISABLE=1 is load-bearing on macOS, and it cost an afternoon.
#
# BSD tar stores each file's extended attributes as a sibling AppleDouble
# member — `._competition`, `._train_checkpointed.py`. `tar -tzf` does not
# list them and Python's `tarfile` extracts them, so the archive that looks
# like one top-level directory unpacks as TWO. flashnode's wrapper strip is
# `if len(tops) == 1`, so it declines to strip, the code lands at
# `inputs/code/competition/` and every attempt dies with
#
#     python: can't open file '…/inputs/code/train_checkpointed.py'
#
# after burning all four. It never bites in production — GitHub's codeload
# tarballs are built on Linux — which is exactly why it bites here, on the
# laptop the demo is given, and nowhere the CI would catch it.
COPYFILE_DISABLE=1 tar -czf "$RUN/code.tar.gz" -C "$RUN/pkg" competition
"$PY" -c '
import sys, tarfile
with tarfile.open(sys.argv[1]) as tf:
    tops = {n.split("/")[0] for n in tf.getnames()}
if len(tops) != 1:
    sys.exit(
        "the code tarball has %d top-level entries (%s) — flashnode strips a "
        "wrapper directory only when there is exactly one, so the entrypoint "
        "would land one level too deep" % (len(tops), ", ".join(sorted(tops)))
    )
' "$RUN/code.tar.gz"
curl -fsS --max-time 30 -X PUT -H 'Content-Type: application/octet-stream' \
    --data-binary "@$RUN/code.tar.gz" \
    "$BASE/v1alpha1/artifacts/code/competition.tar.gz" >/dev/null
echo "  uploaded artifact://code/competition.tar.gz ($(wc -c <"$RUN/code.tar.gz" | tr -d ' ') bytes)"

# --- 3. two host agents -----------------------------------------------------
#
# `--runner trusted`, not `--runner argv`, and this is a real constraint
# rather than a shortcut: the argv tier runs the task inside a container, and
# a rehearsal that needs a Docker daemon is a rehearsal that does not run on
# the machine the demo is given. The trusted tier is the tier a RunPod pod or
# a Colab VM uses for exactly the same reason — it is already a container and
# cannot nest one.
#
# The venv's bin goes first on PATH for both of them. `task_env()` passes PATH
# through to the workload, so this is what makes the compiled argv's bare
# `python` resolve to an interpreter that exists; it is also what satisfies
# the agent's own `check_interpreter_present("python")` startup gate on a
# macOS host, where `python` (unsuffixed) is frequently absent.

step "two host agents"
AGENT_NAMES=(machine-a machine-b)
AGENT_PIDS=()
AGENT_NODE_IDS=()
for name in "${AGENT_NAMES[@]}"; do
    mkdir -p "$RUN/$name/state" "$RUN/$name/work"
    env FLASHNODE_STATE_DIR="$RUN/$name/state" \
        FLASHNODE_WORKDIR="$RUN/$name/work" \
        PATH="$E2E/.venv/bin:$PATH" \
        "$PY" -c 'import os, sys; os.setsid(); os.execv(sys.argv[1], sys.argv[1:])' \
        "$FLASHNODE" work --runner trusted --coordinator "$BASE" \
        --poll-seconds 0.5 --log-json \
        >"$RUN/$name/log" 2>&1 &
    pid=$!
    AGENT_PIDS+=("$pid")
    PIDS+=("$pid")
    disown %% 2>/dev/null || true   # see the coordinator launch above
done

# The node id is written by `load_or_create_node_id()` before registration, so
# waiting for the file is waiting for the agent to have an identity.
for i in 0 1; do
    name="${AGENT_NAMES[$i]}"
    for _ in $(seq 1 100); do
        [ -s "$RUN/$name/state/node-id" ] && break
        sleep 0.2
    done
    [ -s "$RUN/$name/state/node-id" ] || {
        echo "$name never registered; log:" >&2; cat "$RUN/$name/log" >&2; exit 1
    }
    AGENT_NODE_IDS+=("$(tr -d '[:space:]' <"$RUN/$name/state/node-id")")
    echo "  $name  pid=${AGENT_PIDS[$i]}  node=${AGENT_NODE_IDS[$i]}"
done

# --- 4. put both machines in the pool ---------------------------------------
#
# The seventh placement gate refuses a pool-scoped task unless the claiming
# node's `capabilities.pools` lists the pool, and the trusted tier is reachable
# ONLY through a pool-scoped task (three legs, all fail-closed: `payload
# ["pool"]`, `isolation.allowFallback is True`, and the node's own
# `unsandboxed_argv_capable`).
#
# `flashnode work` has no `--pool` flag — in production the cloud control
# plane stamps membership on the delegation path — so the pool is set here the
# same way that path sets it: one `NodeHeartbeat` carrying `pools`, which
# `modea` treats as a wholesale replacement. The agent's own heartbeats leave
# `pools` as None and therefore preserve it.

step "joining both machines to pool '$POOL'"
for node in "${AGENT_NODE_IDS[@]}"; do
    for _ in $(seq 1 50); do
        if curl -fsS --max-time 5 -X POST -H 'Content-Type: application/json' \
             -d "{\"node_id\":\"$node\",\"pools\":[\"$POOL\"]}" \
             "$BASE/v1alpha1/nodes/$node/heartbeat" >/dev/null 2>&1; then
            break
        fi
        sleep 0.2   # the node row does not exist until the agent registers
    done
done
# `unsandboxed_argv_capable` is deliberately not printed: the placement gate
# reads it off the registration (modea's claim-path `node_view`), and the
# `GET /nodes` listing simply does not carry it. Printing it here would show
# `None` for a node that has it set, which reads as a misconfiguration and is
# not one.
get "/v1alpha1/nodes" | "$PY" -c '
import json, sys
for n in json.load(sys.stdin):
    caps = n.get("capabilities") or {}
    print("  %s  online=%s  pools=%s" % (
        n.get("node_id"), n.get("online"), caps.get("pools")))
'

# --- 5. submit -------------------------------------------------------------

step "submitting the training job"
cat >"$RUN/jobspec.json" <<JSON
{
  "apiVersion": "flashml.dev/v1alpha1",
  "kind": "Job",
  "metadata": {"name": "competition-recovery"},
  "spec": {
    "execution": {"backend": "leases", "environment": "auto"},
    "image": {"repository": "ghcr.io/zolli-labs/flashml-python-slim", "tag": "2026.08.2"},
    "workload": {
      "type": "command",
      "parameters": {
        "command": [
          "python", "/work/inputs/code/train_checkpointed.py",
          "--out", "/work/out",
          "--resume", "/work/inputs/resume.json",
          "--epochs", "$EPOCHS",
          "--samples", "$SAMPLES",
          "--features", "$FEATURES",
          "--hidden", "$HIDDEN",
          "--batch-size", "$BATCH"
        ],
        "inputs": {"code": "artifact://code/competition.tar.gz"},
        "unpack_inputs": ["code"],
        "env": {},
        "validators": {"keys": ["accuracy", "model_sha256"]},
        "checkpoint": {},
        "lease_seconds": $LEASE_SECONDS
      }
    },
    "resources": {},
    "retryPolicy": {"maxTaskAttempts": 4, "retryWorkerLoss": true},
    "isolation": {"tier": "sandboxed", "allowFallback": true},
    "placement": {"pool": "$POOL"},
    "artifacts": {"outputPrefix": "artifact://jobs/{job_id}/"}
  }
}
JSON
# `checkpoint` above is what turns the relay on. Its VALUE is opaque —
# flashnode tests only that it is not None (`payload.get("checkpoint") is not
# None`) and then watches `<workdir>/out/ckpt/step-*.json` unconditionally.
#
# So it is `{}`, matching what `compile_to_jobspec` emits. It used to spell
# out `{"dir": "out/ckpt", "glob": "step-*.json"}` to show a reader the
# contract the trainer honours — but those fields are parsed by nothing, and
# a demo that sends a shape production never sends is not rehearsing
# production. The contract lives in the comment, where it cannot be mistaken
# for configuration.

JOB="$(post_json "/v1alpha1/jobs" "$RUN/jobspec.json" | jkey job_id)"
[ -n "$JOB" ] || { echo "submission returned no job id" >&2; exit 1; }
echo "  job $JOB — lease $LEASE_SECONDS s, up to 4 attempts"

# --- 6. wait for a COMMITTED checkpoint ------------------------------------
#
# Committed on the COORDINATOR, not merely written on the worker's disk. The
# distinction is the whole recovery story: a checkpoint the relay has not
# shipped is a checkpoint that dies with the machine, and killing before the
# first commit would prove nothing except that the job restarts from zero.

# The target is a STEP, not a count of events: the trainer commits one
# checkpoint per epoch at step `(epoch + 1) * steps_per_epoch`, and
# `steps_per_epoch` is `samples // batch_size` — `run_config` in the trainer.
# So "checkpoint number N has landed" is exactly "the coordinator's latest
# valid manifest is at step >= N * steps_per_epoch".
STEPS_PER_EPOCH=$(( SAMPLES / BATCH ))
TARGET_STEP=$(( KILL_AFTER * STEPS_PER_EPOCH ))

step "waiting for checkpoint #$KILL_AFTER (step $TARGET_STEP) to be committed"
DEADLINE=$(( $(date +%s) + 900 ))
COMMITTED=""
VICTIM_NODE=""
while :; do
    committed="$(committed_step)"
    read -r state attempts node <<<"$(task_row)"
    progress "$(printf 'task %s attempt %s on %s — committed through step %s/%s' \
        "$state" "$attempts" "${node:0:19}" "${committed:-0}" "$TARGET_STEP")"
    if [ -n "$committed" ] && [ "$committed" -ge "$TARGET_STEP" ]; then
        COMMITTED="$committed"
        VICTIM_NODE="$node"
        echo
        break
    fi
    case "$state" in
        COMPLETED)
            echo
            echo "the task FINISHED before checkpoint #$KILL_AFTER could be" >&2
            echo "observed — the training run is faster than the poll loop, so" >&2
            echo "nothing about recovery was exercised. Raise the sizing (or" >&2
            echo "lower --kill-after); do not treat this as a pass." >&2
            exit 1 ;;
        FAILED|CANCELLED)
            echo
            echo "the task ended $state before any checkpoint was committed" >&2
            diagnose
            exit 1 ;;
    esac
    [ "$(date +%s)" -gt "$DEADLINE" ] && {
        echo; echo "timed out waiting for checkpoints" >&2; diagnose; exit 1
    }
    sleep 0.5
done
[ -n "$VICTIM_NODE" ] && [ "$VICTIM_NODE" != "-" ] || {
    echo "no node holds the task — cannot pick a machine to kill" >&2
    diagnose
    exit 1
}
echo "  committed through step $COMMITTED, held by $VICTIM_NODE"

# --- 7. the machine vanishes ------------------------------------------------

step "the machine vanishes"
VICTIM_PID=""
VICTIM_NAME=""
for i in 0 1; do
    if [ "${AGENT_NODE_IDS[$i]}" = "$VICTIM_NODE" ]; then
        VICTIM_PID="${AGENT_PIDS[$i]}"
        VICTIM_NAME="${AGENT_NAMES[$i]}"
    fi
done
[ -n "$VICTIM_PID" ] || { echo "no agent matches node $VICTIM_NODE" >&2; exit 1; }

EXPIRED_BEFORE="$(event_count LEASE_EXPIRED)"
KILLED_AT=$(date +%s)
# The whole group, uncatchably. No `fail()` is sent, no heartbeat is stopped
# politely, and the training child dies with its parent — this is a machine
# that stopped existing, not a worker that resigned.
kill -9 -"$VICTIM_PID"
echo "  kill -9 -$VICTIM_PID  ($VICTIM_NAME, node $VICTIM_NODE)"
echo "  nothing was reported to the coordinator. The lease must now expire on"
echo "  its own $LEASE_SECONDS s deadline before anyone may touch the task."

# --- 8. the lease expires on its deadline ----------------------------------

step "waiting for the lease to expire"
DEADLINE=$(( KILLED_AT + LEASE_SECONDS + 90 ))
while :; do
    expired="$(event_count LEASE_EXPIRED)"
    now=$(date +%s)
    if [ "$expired" -gt "$EXPIRED_BEFORE" ]; then
        echo
        echo "  LEASE_EXPIRED after $(( now - KILLED_AT ))s — nobody announced anything"
        break
    fi
    progress "$(( now - KILLED_AT ))s since the kill, no lease expiry yet"
    [ "$now" -gt "$DEADLINE" ] && {
        echo; echo "the lease never expired — recovery is broken" >&2
        exit 1
    }
    sleep 1
done

# The expiry must not have been instant: an immediate requeue would mean the
# death WAS reported, which is the easy case this script exists not to test.
ELAPSED=$(( $(date +%s) - KILLED_AT ))
if [ "$ELAPSED" -lt 2 ]; then
    echo "the lease expired in ${ELAPSED}s — that is a reported failure, not a" >&2
    echo "vanished machine. The kill was caught somewhere." >&2
    exit 1
fi

# --- 9. the second machine restores ----------------------------------------

step "waiting for the surviving machine to restore and finish"
DEADLINE=$(( $(date +%s) + 900 ))
while :; do
    read -r state attempts node <<<"$(task_row)"
    job_state="$(get "/v1alpha1/jobs/$JOB" | jkey state)"
    progress "$(printf 'task %s attempt %s on %s (job %s)' \
        "$state" "$attempts" "${node:0:19}" "$job_state")"
    case "$state" in
        COMPLETED) echo; break ;;
        FAILED|CANCELLED) echo; echo "task ended $state" >&2; diagnose; exit 1 ;;
    esac
    [ "$(date +%s)" -gt "$DEADLINE" ] && {
        echo; echo "timed out waiting for the restore" >&2; diagnose; exit 1
    }
    sleep 1
done

read -r state attempts node <<<"$(task_row)"
echo "  finished on $node after $attempts attempt(s)"
[ "$node" != "$VICTIM_NODE" ] || {
    echo "the task completed on the machine that was killed — impossible" >&2
    exit 1
}
[ "$attempts" -ge 2 ] || {
    echo "the task completed in one attempt — no recovery happened" >&2
    exit 1
}

# --- 10. the assertion the whole thing exists for ---------------------------

step "verdict"
curl -fsS --max-time 30 "$BASE/v1alpha1/artifacts/jobs/$JOB/task-000/metrics.json" \
    >"$RUN/recovered-metrics.json"
"$PY" - "$RUN/recovered-metrics.json" "$BASELINE_SHA" <<'PY'
import json, sys

metrics = json.load(open(sys.argv[1]))
baseline = sys.argv[2]
recovered = metrics["model_sha256"]

print(f"  uninterrupted  {baseline}")
print(f"  recovered      {recovered}")
print(f"  resumed from step {metrics['resumed_from_step']}, "
      f"ran {metrics['epochs_executed']}/{metrics['epochs']} epochs, "
      f"recomputed {metrics['recomputed_steps']} step(s)")
print(f"  holdout accuracy {metrics['accuracy']}")

problems = []
if recovered != baseline:
    problems.append(
        "the recovered model is NOT the uninterrupted model. Resume "
        "equivalence is the demo's central claim; do not soften this check."
    )
if not metrics.get("resumed"):
    problems.append(
        "the surviving machine restarted from scratch instead of resuming — "
        "the checkpoint was committed but never staged as resume.json"
    )
if metrics.get("epochs_executed", 0) >= metrics.get("epochs", 0):
    problems.append("every epoch was re-run: no work survived the kill")
if problems:
    print()
    for p in problems:
        print(f"  FAIL: {p}", file=sys.stderr)
    raise SystemExit(1)

print()
print("  PASS — a machine vanished mid-training, its lease expired on its own")
print("         deadline, another machine restored from the last committed")
print("         checkpoint, and the model that came out is byte-for-byte the")
print("         model an uninterrupted run produces.")
PY

# --- 11. the structured artifact --------------------------------------------
#
# ADDITIVE, and never fatal. Everything above this line behaves exactly as it
# did; this only stops the run's numbers from evaporating. Until now the one
# recovery figure it produced was the "LEASE_EXPIRED after Ns" line on stdout,
# and `$RUN` is deleted on exit unless --keep — so a rehearsal left nothing an
# evidence pack could cite.
#
# Placed HERE and not beside the expiry check on purpose: `reclaim_s` and
# `resume_to_progress_s` — how long until another machine picked the task up,
# and how long until it was demonstrably progressing — are the intervals the
# whole demo exists to show, and at the instant the lease expires neither has
# happened yet. The ledger is complete only once the surviving machine has
# committed, which the verdict above just proved it did.
#
# The evidence lands in the REPO's `.evidence/`, not in `$RUN`: the ledger dump
# is a working file and dies with the temp dir, the analysis is the artifact
# and has to outlive it.
step "structured recovery evidence"
EVENTS_JSON="$RUN/job-events.json"
RECOVERY_TOOL="$E2E/../flashml-cloud/scripts/competition/recovery_latency.py"
EVIDENCE_DIR="$E2E/../flashml-cloud/.evidence"
if get "/v1alpha1/jobs/$JOB/events" >"$EVENTS_JSON" 2>/dev/null; then
    echo "  ledger: $EVENTS_JSON"
    if [ -f "$RECOVERY_TOOL" ]; then
        # `--kill-at` takes epoch seconds, which is exactly what KILLED_AT
        # already is — no `date` conversion, and so no BSD-vs-GNU trap.
        "$PY" "$RECOVERY_TOOL" \
            --events-json "$EVENTS_JSON" \
            --kill-at "$KILLED_AT" \
            --out-dir "$EVIDENCE_DIR" \
            || echo "  (recovery_latency.py failed — the verdict above still stands)" >&2
    else
        echo "  no recovery_latency.py at $RECOVERY_TOOL — ledger dumped, not analysed"
    fi
else
    echo "  could not read the job's events — nothing to analyse" >&2
fi

echo
echo "▶ dashboard: $BASE/   (job $JOB)"
