# Competition scripts

Narrow, disposable scripts for the Beta × Alibaba Cloud × AMD submission.
Requirements live in `docs/superpowers/specs/2026-08-11-competition-requirements.md`;
nothing here is product code.

## `alibaba_fc_sandbox_smoke.py` — run this first

Answers one question: **can this Alibaba account pause a sandbox and reconnect
to it with state intact?** Requirement §6.4 / iteration 1. Everything else in
the plan is blocked on the answer.

### Prerequisites

1. An **API key created in the Function Compute console** (Alibaba doc 3045205).
   This is *not* the Alibaba access key and *not* the MCP CLI credential — the
   E2B-compatible endpoint takes its own key.
2. The E2B SDK at the documented pins, in a throwaway environment. Do **not**
   add it to the API dependency set until the version is proven:

   ```bash
   python3 -m venv /tmp/fcsmoke && . /tmp/fcsmoke/bin/activate
   pip install e2b==2.31.0 e2b-code-interpreter==2.8.1
   ```

### Run

```bash
export E2B_API_KEY="<from the FC console>"
python alibaba_fc_sandbox_smoke.py --regions us-west-1,ap-southeast-1
```

If the key is region-scoped, set `E2B_API_KEY_US_WEST_1` /
`E2B_API_KEY_AP_SOUTHEAST_1` instead; the script falls back to `E2B_API_KEY`.

Both regions are tested on purpose. `us-west-1` is Silicon Valley and Demo Day
is in Palo Alto; `ap-southeast-1` is what the rest of the field used. **Pick on
measured wake latency, not on convention.**

### What it does

create → write nonce marker + start a background process → observe active state
→ **`pause()`** → wait 30 s outside → `Sandbox.connect(id)` → verify marker hash
and process identity → run a continuation command → `kill()` in `finally`.

### Reading the result

| Exit | Meaning | Do next |
|---|---|---|
| **0** | GO — hibernation proven | Start Task 1 (freeze the workload) and Task 2 (`SandboxGateway`). Use the region with the faster wake |
| **2**, `allowlist_blocked: true` | The account cannot call `pause()` | **Do not write the lifecycle controller.** Request enablement, ask DingTalk `179855020297` whether the Pro-tier transition satisfies the gate, ask Discord, and exercise plan risk R1 |
| **2**, otherwise | Something else failed | Check key, region, and that the endpoint region matches the key's region |
| 1 | Missing credential | Harness problem, not a verdict |

**If it comes back blocked, never simulate hibernation.** A demo that fakes it
fails the rubric's own anti-pattern list, and the failure mode when a judge asks
is worse than the lower score.

### Output

Sanitized JSON in `../../.evidence/` (gitignored — it names sandbox ids and
account state). API keys are redacted twice: by pattern, and by substituting the
literal value of every `E2B_*KEY*` variable read. Nothing here is safe to
publish unreviewed.

### Cost

A sandbox is roughly $0.08/hour active. Two regions × ~2 minutes is a fraction
of a cent. Every sandbox is killed in `finally`; if the script is interrupted,
check the FC console for survivors — the voucher expires 2026-08-15 and a
forgotten sandbox bills by the second.

## `hibernation_modes_probe.py` — C-6.5 and C-6.4

The requirement's own words: *both modes, for two genuinely different waits,
measured wake latency for each, cost quantified against the right baseline*
(C-6.5) and *marker nonce hash, background process identity, and a warm
artifact/dependency cache all intact across the hibernation boundary* (C-6.4).

The three scripts above measure ONE hibernation mode over ONE wait and check
the marker and the pid. This one runs a 2 × 2 — `{pause(keep_memory=True),
pause(keep_memory=False)}` × `{a long wait, a short inter-shard gap}` — in four
separate sandboxes, and verifies **seven** independent continuity properties
across every boundary:

| check | what it rules out |
|---|---|
| marker sha256 | a fresh sandbox from the same template |
| `[ -d /proc/$PID ]` | a dead worker (never `ps -p` — this template has no procps, so `ps` exits 127 and the idiom reports every process as gone) |
| `/proc/$PID/stat` field 22 | a restarted process that reused the pid |
| `boot_id` | a cold boot with the disk intact |
| a secret held **only in the daemon's RAM**, revealed on SIGUSR1 | the filesystem alone explaining the result |
| the heartbeat counter advancing | a process that came back present but not working |
| a sha256 manifest over the warm cache tree | a cache that is present but changed |

Plus the cache is *used* after every wake — an import from it and a shard
computation that reads its artifact — because a cache that cannot be used is
not warm.

```bash
python hibernation_modes_probe.py                    # ~25 min, the evidence run
python hibernation_modes_probe.py --long-wait 20 --short-wait 5 \
    --long-repeats 1 --short-repeats 1               # ~3 min, shape check
python hibernation_modes_probe.py --auto-pause-idle-s 600   # + platform auto-pause
```

**What it will not do is claim two modes it could not select.** `pause(keep_memory)`
is the only hibernation selector in `e2b==2.31.0`, nothing on `get_info()`
reports which hibernation SKU a pause billed as, and Alibaba's own taxonomy
(doc 3045181) separates light from deep by *resume latency* — millisecond-level
versus "approximately the 1 s level". So the script measures the latency and
lets it decide: a sub-100 ms resume reports `MEASURED`, a ~1 s resume reports
`NOT REACHED` for light hibernation and says plainly that no comparison between
the modes was run. `mode_finding.what_would_settle_it` names what would.

Cost is computed from the measured hibernated seconds and the published rates
(doc 3045213) against a **named** baseline — Baseline A, holding the sandbox
active across the same wait — with Baseline B (destroy and rebuild) volunteered
beside it, priced from this run's own measured create+prepare time and its
crossover point.

`test_hibernation_modes_probe.py` covers the arithmetic and the honesty rules
offline — it creates no sandbox:

```bash
../../apps/api/.venv/bin/python -m pytest test_hibernation_modes_probe.py -q
```

### Cleanup

Every sandbox id is printed the instant it is created **and** appended to
`../../.evidence/alibaba-hibernation-modes-<stamp>.sandboxes` before anything
else happens to that sandbox. A `kill -9` of the script therefore still leaves a
human with a list and a console. Each cell kills its own sandbox in a `finally`,
the run then sweeps by id — only ids it created, never a blanket sweep that
would reach into a probe running beside it — and the last thing it prints is
what is still alive.
