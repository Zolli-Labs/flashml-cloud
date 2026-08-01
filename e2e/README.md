# End-to-end harness (local, cloud-free)

Lives **outside all three repos** on purpose: it exercises the system the
way a user meets it — over the network, across processes — with zero cloud
and zero Kubernetes. The coordinator runs as a real uvicorn process; agents
speak real HTTP to it; shared data is hosted by the coordinator's local
artifact endpoint.

The loop it proves (the master report's §15 promise, entirely local):

1. Coordinator starts (FlashRuntime service, KubeRay disabled).
2. A shared dataset is uploaded to the coordinator — **local artifact
   hosting instead of any cloud store**.
3. The planner is consulted (`flash.plan`) and selects the lease runtime.
4. A 12-trial hyperparameter-search job is submitted; it expands into
   leased tasks.
5. Two "machines" register, discover their capabilities, and pull work.
6. One machine **dies mid-task** (network severed, no goodbye).
7. Its lease expires, the task requeues, the survivor finishes 12/12 —
   exactly once, late commits rejected.
8. Results, per-node contribution, and the recovery timeline are all
   visible in the dashboard at the coordinator URL.

```bash
make e2e-setup    # one-time: builds e2e/.venv with both repos editable
make e2e          # run the pytest (~1 min; includes the kill)
make e2e-demo     # watchable version: real `flashnode work` subprocesses,
                  # dashboard URL printed, one agent SIGKILLed mid-run
```

Files: `conftest.py` (coordinator subprocess fixture + dataset generator),
`test_local_loop.py` (kill-a-machine sweep), `test_kmeans_loop.py`
(distributed K-means: 3 Lloyd iterations as 3 lease jobs across 2 agents),
`run_demo.py` (the human-watchable version),
`test_allowlist_parity.py` and `test_archive_parity.py` (cross-repo drift
guards — the latter feeds one in-process attack corpus to *both* untrusted-
archive extractors, flashnode's and the cloud API's, because the two cannot
share code across the repo boundary). The kill in the pytest is
simulated by severing the dying agent's network (every call raises), which
exercises the *lease-expiry* recovery path — the harshest one. The demo
uses a real SIGKILL.

## A real second machine (LAN)

The whole point of the pull model is that any machine with outbound HTTP
can join. To prove it on real hardware:

1. On this Mac: `make local-coordinator JOIN_CODE=LOCAL-2026` — binds
   0.0.0.0:8100, requires the join code for registration. Dashboard at
   `http://<this-mac-ip>:8100/`.
2. On the second machine (laptop, Linux box — needs git + uv):
   ```bash
   git clone <flashruntime> && git clone <flashnode>   # side by side
   uv venv .venv && uv pip install -e ./flashruntime -e ./flashnode
   FLASHNODE_JOIN_CODE=LOCAL-2026 \
     .venv/bin/flashnode work --coordinator http://<this-mac-ip>:8100
   ```
   (`--runner docker` + `FLASHNODE_ALLOWED_IMAGES=...` for the container
   tier; on macOS with colima also set `FLASHNODE_WORKDIR=$HOME/.cache/flashnode`
   so the Docker VM can see task workdirs.)
3. Submit from this Mac (`make local-agent` optionally adds this Mac as a
   worker too) and watch both machines in the dashboard; close the second
   machine's lid mid-run to watch its lease expire and the task requeue.

No inbound ports on the worker, no SSH, no shared filesystem — outbound
HTTP only.
