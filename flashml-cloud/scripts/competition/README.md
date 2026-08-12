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
