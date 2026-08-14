#!/usr/bin/env python3
"""anchorctl — manage the community anchor machines rented on RunPod.

Anchors are PERSISTENT pods on the owner's RunPod account that host flashnode
for the whole network. Unlike job-scoped rentals (capacity/ecs.py: destroy,
never stop), anchors live on a stop/resume lifecycle: `down` stops the pod
and keeps its disk, so the node keeps its identity — and its lease history —
and billing drops to disk-only until the next `up`.

    RUNPOD_API_KEY=... anchorctl.py status
    RUNPOD_API_KEY=... anchorctl.py up zolli_anchor_gpu_a5000 --gpu
    RUNPOD_API_KEY=... anchorctl.py down zolli_anchor_gpu_a5000
    RUNPOD_API_KEY=... anchorctl.py measure zolli_anchor_gpu_a5000 --cycles 3

`measure` records stop→resume cycles (time to stopped, time to RUNNING, time
to runtime-ready) into evidence/ JSON. Those numbers are the manual half of
the comparison with Alibaba FC hibernation, which does the same transition
automatically (see the 2026-08-13 hibernation-modes probe + cost worksheet
under scripts/competition/).

Enrolment of an anchor into the network (flashnode install + device-code
approval + pool join) is a deliberate separate step, parked until the owner
picks the target environment — see `enrol`.

CPU pods: the v2 API cannot create them yet; create via console/MCP, after
which every other command here manages them the same as GPU pods.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.runpod.io/v2"

# The volume is load-bearing: RunPod resets the container disk on stop/start,
# so anything that must survive a stop — the flashnode state dir, its node
# identity, cached environments — has to live under the volume mount.
GPU_DEFAULTS = {
    "cloudType": "SECURE",
    "gpuTypeIds": ["NVIDIA RTX A5000"],
    "gpuCount": 1,
    "imageName": "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404",
    "containerDiskInGb": 30,
    "volumeInGb": 20,
    "volumeMountPath": "/workspace",
    "ports": ["22/tcp"],
}

POLL_INTERVAL_S = 3
POLL_TIMEOUT_S = 300


def _key() -> str:
    key = os.environ.get("RUNPOD_API_KEY", "").strip()
    if not key:
        sys.exit("RUNPOD_API_KEY is not set")
    return key


def _request(method: str, path: str, body: dict | None = None) -> dict | list:
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {_key()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as err:
        sys.exit(f"{method} {path} -> {err.code}: {err.read().decode(errors='replace')[:500]}")
    return json.loads(raw) if raw else {}


def _pods() -> list[dict]:
    got = _request("GET", "/pods")
    return got if isinstance(got, list) else got.get("pods", got.get("items", []))


def _find(name: str) -> dict:
    matches = [p for p in _pods() if p.get("name") == name]
    if not matches:
        sys.exit(f"no pod named {name!r} (try: anchorctl.py status)")
    if len(matches) > 1:
        sys.exit(f"{len(matches)} pods named {name!r}; rename in the console first")
    return matches[0]


def _is_running(pod: dict) -> bool:
    return (pod.get("desiredStatus") or pod.get("status")) == "RUNNING"


def _runtime_ready(pod: dict) -> bool:
    runtime = pod.get("runtime") or {}
    return bool(runtime.get("uptimeInSeconds") or runtime.get("uptime") or runtime.get("ports"))


def _wait(pod_id: str, predicate, label: str) -> float:
    start = time.monotonic()
    while time.monotonic() - start < POLL_TIMEOUT_S:
        if predicate(_request("GET", f"/pods/{pod_id}")):
            return time.monotonic() - start
        time.sleep(POLL_INTERVAL_S)
    sys.exit(f"timed out after {POLL_TIMEOUT_S}s waiting for {label} on {pod_id}")


def cmd_status(_: argparse.Namespace) -> None:
    rows = []
    for pod in _pods():
        runtime = pod.get("runtime") or {}
        rows.append((
            pod.get("name", "?"),
            pod.get("id", "?"),
            (pod.get("desiredStatus") or pod.get("status") or "?"),
            pod.get("gpu", {}).get("displayName") if isinstance(pod.get("gpu"), dict) else pod.get("gpuTypeId") or "cpu",
            f"${pod.get('costPerHr', pod.get('cost', 0))}/hr",
            pod.get("dataCenterId", "?"),
            f"{(runtime.get('uptimeInSeconds') or runtime.get('uptime') or 0) // 60}m up" if runtime else "-",
        ))
    if not rows:
        print("no pods")
        return
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    for row in rows:
        print("  ".join(str(cell).ljust(width) for cell, width in zip(row, widths)))


def cmd_up(args: argparse.Namespace) -> None:
    existing = [p for p in _pods() if p.get("name") == args.name]
    if existing:
        pod = existing[0]
        if _is_running(pod):
            print(f"{args.name} already running ({pod['id']})")
            return
        _request("POST", f"/pods/{pod['id']}/action", {"action": "start"})
        took = _wait(pod["id"], _is_running, "RUNNING")
        print(f"{args.name} resumed in {took:.0f}s ({pod['id']})")
        return
    if not args.gpu:
        sys.exit("pod does not exist; anchorctl only creates GPU anchors (--gpu). "
                 "Create CPU pods via the RunPod console, then manage them here.")
    body = dict(GPU_DEFAULTS, name=args.name)
    if args.gpu_type:
        body["gpuTypeIds"] = [args.gpu_type]
    created = _request("POST", "/pods", body)
    pod_id = created.get("id") or created.get("podId")
    print(f"created {args.name} ({pod_id}), waiting for RUNNING…")
    took = _wait(pod_id, _is_running, "RUNNING")
    print(f"{args.name} running after {took:.0f}s")


def cmd_down(args: argparse.Namespace) -> None:
    pod = _find(args.name)
    if not _is_running(pod):
        print(f"{args.name} already stopped")
        return
    _request("POST", f"/pods/{pod['id']}/action", {"action": "stop"})
    took = _wait(pod["id"], lambda p: not _is_running(p), "stopped")
    print(f"{args.name} stopped in {took:.0f}s — disk kept, billing is disk-only until `up`")


def cmd_measure(args: argparse.Namespace) -> None:
    pod = _find(args.name)
    if not _is_running(pod):
        sys.exit(f"{args.name} must be running before measure (anchorctl.py up {args.name})")
    running_cost = pod.get("costPerHr", pod.get("cost"))
    cycles = []
    for i in range(args.cycles):
        print(f"cycle {i + 1}/{args.cycles}: stopping…", flush=True)
        _request("POST", f"/pods/{pod['id']}/action", {"action": "stop"})
        to_stopped = _wait(pod["id"], lambda p: not _is_running(p), "stopped")
        stopped_pod = _request("GET", f"/pods/{pod['id']}")
        time.sleep(args.settle)
        print(f"  stopped in {to_stopped:.0f}s; starting…", flush=True)
        _request("POST", f"/pods/{pod['id']}/action", {"action": "start"})
        to_running = _wait(pod["id"], _is_running, "RUNNING")
        to_ready = to_running + _wait(pod["id"], _runtime_ready, "runtime-ready")
        print(f"  RUNNING in {to_running:.0f}s, runtime-ready in {to_ready:.0f}s")
        cycles.append({
            "stop_seconds": round(to_stopped, 1),
            "resume_running_seconds": round(to_running, 1),
            "resume_ready_seconds": round(to_ready, 1),
            "stopped_cost_per_hr": stopped_pod.get("costPerHr", stopped_pod.get("cost")),
        })
        time.sleep(args.settle)
    evidence = {
        "pod": {"name": args.name, "id": pod["id"],
                "gpu": pod.get("gpu"), "dataCenterId": pod.get("dataCenterId")},
        "running_cost_per_hr": running_cost,
        "cycles": cycles,
        "method": "anchorctl measure: POST /action stop|start, polled every "
                  f"{POLL_INTERVAL_S}s; resume_ready = RUNNING plus runtime reporting",
    }
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evidence")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"anchor-resume-{time.strftime('%Y%m%d-%H%M%S')}.json")
    with open(out, "w") as handle:
        json.dump(evidence, handle, indent=2)
    print(f"evidence -> {out}")


def cmd_enrol(_: argparse.Namespace) -> None:
    sys.exit(
        "parked: enrolment target (prod vs dev) is an owner decision not yet made.\n"
        "When decided: SSH in, `pip install flashnode`, run the device-code enrol\n"
        "against the chosen API, approve in the console, join the community pool\n"
        "(rented capacity is invisible to public jobs without a pool), and run the\n"
        "agent under a supervisor so it survives stop/resume."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status").set_defaults(func=cmd_status)
    up = sub.add_parser("up")
    up.add_argument("name")
    up.add_argument("--gpu", action="store_true", help="create as GPU anchor if missing")
    up.add_argument("--gpu-type", help="override GPU type id (default: NVIDIA RTX A5000)")
    up.set_defaults(func=cmd_up)
    down = sub.add_parser("down")
    down.add_argument("name")
    down.set_defaults(func=cmd_down)
    measure = sub.add_parser("measure")
    measure.add_argument("name")
    measure.add_argument("--cycles", type=int, default=3)
    measure.add_argument("--settle", type=int, default=15,
                         help="seconds to wait between transitions (default 15)")
    measure.set_defaults(func=cmd_measure)
    sub.add_parser("enrol").set_defaults(func=cmd_enrol)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
