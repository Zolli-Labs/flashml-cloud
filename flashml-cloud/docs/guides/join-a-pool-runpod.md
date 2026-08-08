# Join a pool from RunPod

Contribute a rented RunPod GPU to your team's pool. Same three commands as
the Colab guide, run in a pod's terminal instead of a notebook cell.

Unlike Colab, a rented pod is compute you're paying for directly — there's
no free-tier ToS clause about distributed workers to navigate, and no
shared-account restriction to worry about. Start any pod (community or
secure cloud, CPU or GPU depending on what your pool needs) and open its
terminal — a web terminal from the RunPod console, or SSH if the pod exposes
it.

## What you need first

- A running RunPod pod with a terminal open.
- An invite to a pool. Someone on your team creates it from
  [`/pools`](/pools) and sends you a link shaped like
  `/pools/join?token=...`. Open that link in a browser, sign in, and you're
  a member — the commands below are what turns this pod into one of the
  pool's machines, not what joins the pool itself. Treat that link like a
  password: anyone holding it can join your pool, and it can end up in
  browser history — send it over a private channel, and prefer minting a
  short-lived invite over a long-lived one.
- The cloud API URL your team is using. The examples below use
  `https://flashml-api.onrender.com`, the hosted default — if your team
  self-hosts, use the URL from your own console's
  [Docs page](/docs#attach) instead, which always shows the exact one you're
  looking at.

## The three commands

```bash
pip install flashnode
```

```bash
flashnode login --coordinator https://flashml-api.onrender.com
```

This prints a short code and a URL — `Your code: XXXX-XXXX`,
`Approve at: https://...`. Open that URL on your phone or laptop, approve
the code, and the terminal prints `Approved. This machine is enrolled` on
its own. (`--coordinator` is required; there's no default it falls back
to.)

It then prints the exact command to run next, with the coordinator
already filled in (flashnode 0.3.5 — earlier versions printed a bare
`flashnode work --runner docker`, which connected to localhost and
401'd). The suggestion says `--runner docker`; this box has no Docker,
so use the trusted command below instead. Since this machine enrolled
against exactly one coordinator, `--coordinator` is now optional.

```bash
flashnode work --coordinator https://flashml-api.onrender.com --runner trusted
```

This is the command that contributes the machine. Run it in a `tmux`/`screen`
session or with `nohup ... &` if you want it to survive closing the
terminal — it polls for your pool's work for as long as it's running, and a
pod's terminal closing does not stop the pod itself.

## Why `--runner trusted`, specifically

A RunPod pod is itself already a container (or a full VM on secure cloud),
and it cannot nest a second Docker daemon inside itself the way the
sandboxed tiers (`--runner argv`, `--runner docker`) require — so those
tiers aren't available here, and `--runner trusted` is the correct choice,
not a shortcut around anything.

`--runner trusted` runs your pool's jobs **unsandboxed** on this pod: no
container, no network isolation, no filesystem confinement beyond what the
pod itself already provides. The command prints its own warning to that
effect every time it starts. The coordinator is what bounds this, not the
sandbox: no job from outside your pool ever **runs** on this pod — argv work
is confined to your pool by three fail-closed checks. (A trusted worker can
still be offered a public, non-argv task; it cannot execute one, so that
costs it a wasted attempt, not a stranger's code actually running here —
closing that gap fully is upstream flashnode work, tracked separately.)
Because you're already paying for and controlling this pod outright, the
practical risk is close to what you already accept by renting it; the pool
boundary just means argv work here also only ever comes from people your
pool operator invited.

## Verify it worked

Open [`/machines`](/machines) in the console — this pod should appear
within a few seconds of the third command starting. Then open
[`/pools`](/pools): the pool's **workers online** count should tick up by
one. If a job is queued for the pool, watch it move once this pod claims a
task.
