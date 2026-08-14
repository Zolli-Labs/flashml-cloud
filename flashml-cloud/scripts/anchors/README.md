# Community anchor machines

Persistent RunPod pods on the owner's account that host flashnode for the
whole network. This is the supply floor: providers that are always there,
accumulating real uptime and lease history for the provider-profile pages.

Anchors are NOT job-scoped rentals. The capacity layer (`apps/api/
flashml_cloud_api/capacity/`) destroys its rentals and must never manage
these; anchors live on a stop/resume lifecycle driven by `anchorctl.py`,
keeping their disk — and therefore their node identity and lease history —
across stops.

## Usage

```bash
export RUNPOD_API_KEY=...        # owner's key; never commit it
./anchorctl.py status
./anchorctl.py up zolli_anchor_gpu_a5000 --gpu    # create or resume
./anchorctl.py down zolli_anchor_gpu_a5000        # stop; billing -> disk-only
./anchorctl.py measure zolli_anchor_gpu_a5000 --cycles 3
```

`measure` writes stop→resume timing JSON into `evidence/`. Those numbers are
the manual half of the hibernation comparison: Alibaba FC performs the same
suspend/resume automatically (2026-08-13 hibernation-modes probe,
`scripts/competition/`), and on RunPod we replicate it with this script.
That asymmetry is itself competition evidence.

## Current anchors (2026-08-13)

| Pod | Kind | Cloud | $/hr running |
|---|---|---|---|
| `zolli_anchor_gpu_a5000` | RTX A5000 24 GB | secure, EU-SE-1 | 0.27 |
| `zolli_prod_pod_cpu` | 2 vCPU / 8 GB | secure, US-NC-1 | 0.08 |
| `still_coral_vole` | 2 vCPU / 8 GB | secure, EU-CZ-1 | 0.08 |

## Parked decisions

- **Enrolment target** (prod vs dev) is an owner decision not yet made;
  `anchorctl.py enrol` refuses until it is. Enrolment requires a pool —
  rented capacity is invisible to public jobs without one.
- **Grouping same-CPU-type pods under one shared lease class** is post-
  competition work, recorded in the device-profile spec, not here.
- Console Stop/Run buttons need a RunPod venue adapter in the API (phase 2
  of the device-profile spec). Delete already exists and is a tombstone:
  it scrubs the profile but keeps node_id + credit history by design.
