# Join a pool from Google Colab

Contribute a Colab GPU to your team's pool. Three cells, no local install.

> **Paid Colab only.** Google's Colab FAQ prohibits "running distributed
> computing workers" on the free tier, and prohibits "using multiple
> accounts to work around access or resource usage restrictions" on every
> tier. Enforcement lands on **your Google account**. Run this only on a
> paid Colab plan, one account, yours.
>
> FAQ wording as read 2026-08-02 — re-check it before relying on this.

If you are not on a paid plan, stop here — see the
[RunPod guide](join-a-pool-runpod.md) for a rented alternative with no ToS
caveat instead.

## What you need first

- A paid Colab runtime, already started (Runtime → Change runtime type →
  pick a GPU if the job needs one).
- An invite to a pool. Someone on your team creates it from
  [`/pools`](/pools) and sends you a link shaped like
  `/pools/join?token=...`. Open that link, sign in, and you're a member —
  this notebook is what turns your Colab session into one of the pool's
  machines, not what joins the pool itself. Treat that link like a
  password: anyone holding it can join your pool, and it can end up in
  browser history — send it over a private channel, and prefer minting a
  short-lived invite over a long-lived one.
- The cloud API URL your team is using. The examples below use
  `https://flashml-api.onrender.com`, the hosted default — if your team
  self-hosts, use the URL from your own console's
  [Docs page](/docs#attach) instead, which always shows the exact one you're
  looking at.

## The three cells

Run these in order, each in its own cell.

```python
!pip install flashnode
```

```python
!flashnode login --coordinator https://flashml-api.onrender.com
```

This prints a short code and a URL — `flashnode login: ... Your code: XXXX-XXXX`,
`Approve at: https://...`. Open that URL on your phone or any other signed-in
device, approve the code, and come back to the cell. It sits waiting; once
approved it prints `Approved. This machine is enrolled` and exits on its
own. (`flashnode login` requires `--coordinator`; there's no default it
falls back to.)

It then prints the exact command to run next, with the coordinator
already filled in (flashnode 0.3.5 — earlier versions printed a bare
`flashnode work --runner docker`, which connected to localhost and
401'd). The suggestion says `--runner docker`; this box has no Docker,
so use the trusted command below instead. Since this machine enrolled
against exactly one coordinator, `--coordinator` is now optional.

```python
!flashnode work --coordinator https://flashml-api.onrender.com --runner trusted
```

This is the one that actually contributes the machine. It runs until the
cell is interrupted or the runtime disconnects — normal for a background
Colab cell, and exactly what you want: it keeps polling for your pool's work
the whole time it's running.

## What `--runner trusted` means, plainly

Colab cannot nest a Docker daemon inside its own container, so the sandboxed
tiers (`--runner argv`, `--runner docker`) that a home volunteer would use
are not available here at all — `trusted` is not a shortcut, it's the only
tier that works on this host.

`--runner trusted` runs your pool's jobs **unsandboxed**: no container, no
network isolation, no filesystem confinement. Whatever the job's command
does, it does directly inside this Colab runtime. That command prints its
own warning to the same effect every time it starts, so you'll see it again
in the cell's output.

The coordinator is what keeps this bounded, not the sandbox: no job from
outside your pool ever **runs** here — argv work is confined to your pool by
three fail-closed checks. (A trusted worker can still be offered a public,
non-argv task; it cannot execute one, so that costs it a wasted attempt, not
a stranger's code running on your machine — closing that gap fully is
upstream flashnode work, tracked separately.) That's the whole trust model
here: you're trusting your pool's members the way you'd trust anyone with a
shell account.

## Verify it worked

Open [`/machines`](/machines) in the console — this Colab session should
appear in the list within a few seconds of the third cell starting (it
registers before it claims anything). Then open [`/pools`](/pools): the
pool's **workers online** count should tick up by one. If a job is queued
for the pool, watch it move once this machine claims a task.
