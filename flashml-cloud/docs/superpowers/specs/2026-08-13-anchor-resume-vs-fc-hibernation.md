# Suspend/resume: FC does automatically what RunPod needs a script for

2026-08-13. Companion evidence to the D-9 cost worksheet
(`.evidence/cost-worksheet-20260813T124945Z.md`) and the hibernation-modes
probe (`.evidence/alibaba-hibernation-modes-20260813T041325Z.json`). The
RunPod half is `scripts/anchors/evidence/anchor-resume-20260813-233153.json`,
measured tonight on the first community anchor pod.

## The claim this evidence supports

Both venues can park idle capacity and bring it back. The difference is who
does the work and how fast it wakes:

- **Alibaba FC decides by itself.** The platform hibernates an idle sandbox
  and wakes it on the next request — no controller code of ours in the loop
  for the transition itself. Wake is ~1 second.
- **On RunPod we had to build the loop ourselves** (`anchorctl.py`): decide
  when to stop, issue the stop, decide when to resume, poll until ready.
  Wake is ~15 seconds, plus whatever latency our own decision-making adds.

That asymmetry — a platform capability versus an operator script replicating
it — is the point being demonstrated, and it is a bonus for the FC side.

## Measured numbers

| | Alibaba FC (measured) | RunPod anchor (measured) |
|---|---|---|
| Wake / resume latency | **1.11 s** p50 (keep_memory), 1.22 s p50 (without) | **11–18 s** to runtime-ready (3 cycles: 11/15/18) |
| Suspend latency | platform-managed | ~1 s (stop returns EXITED synchronously) |
| Idle rate | **$0.0035/hr** deep (93.8% below active), $0.0191/hr light | ~**$0.008/hr** modelled disk-only (30 GiB at published stopped rate) vs $0.27/hr running (~97% below) |
| Who drives the transition | the platform, automatically | our script, manually |
| Shape measured | 2 vCPU / 2 GiB CPU sandbox | RTX A5000 24 GB GPU pod, secure cloud, EU-SE-1 |

Absolute dollar rates are NOT comparable across the two columns — a CPU
sandbox and a 24 GB GPU pod are different machines. The comparable rows are
the mechanism rows: wake latency, who initiates, and the *relative* idle
saving (93.8% automatic vs ~97% manual-with-a-script).

## Caveats, stated plainly

- RunPod "ready" means the pod's runtime is reporting (uptime counter and
  port map present), back-computed from the uptime counter, not poll-bounded.
  SSH-level readiness was not verified (outbound SSH blocked from the
  measuring environment).
- RunPod's container disk is documented as **ephemeral across stop/start**;
  only volumes persist. The measured pod had no volume, so tonight's cycles
  say nothing about state surviving a stop — `anchorctl.py` now creates
  anchors with a `/workspace` volume, and the flashnode state dir must live
  there. Recreating the current anchor with a volume awaits owner approval.
- FC numbers are Pro-tier preview pay-as-you-go, modelled from doc 3045213,
  not reconciled against an invoice (the worksheet says the same).
- One venue was measured once, on one night, in one region each. These are
  demo-grade numbers, not a benchmark.

## Where this goes next

The device-profile spec (in design) gives every machine a stop/resume
lifecycle and a durable uptime ledger; anchors are its first residents. The
console Stop/Run buttons require a RunPod venue adapter in the API — until
then `anchorctl.py` is the only driver, which is exactly the manual-vs-
automatic contrast documented above.
