# Strategy note — pooling Colab GPUs across a research group

**Date:** 2026-08-02
**Status:** exploration. Not a spec, not scheduled. Written to capture the idea
and, more importantly, the terms-of-service finding attached to it.
**Origin:** owner's idea during the M1 acceptance run — a research group where
each member has a Google account, each installs `flashnode` in a Colab
notebook, and they train distributed across the group's GPUs.

---

## 1. The idea

Colab hands a free GPU to anyone with a Google account. A ten-person lab
therefore has ten GPUs it is not using together. If each researcher runs
`flashnode` inside a notebook, FlashML could aggregate them into one pool and
train a model across all of them.

The appeal is obvious: GPU access is the binding constraint for most academic
ML, and this converts an idle per-person entitlement into shared capacity at no
marginal cost.

## 2. Why it fits this architecture unusually well

Three properties FlashML already has, which most distributed frameworks do not:

**The pull model needs no inbound network.** Colab has outbound internet and no
inbound. An agent that polls `/leases/claim` works with zero configuration —
no port forwarding, no NAT traversal, no tunnel. A push-based scheduler could
not reach a Colab runtime at all.

**Colab's worst property is the one FlashML is built for.** Free runtimes idle
out, cap at ~12 hours, and are preempted at Google's discretion. For a
conventional framework that is fatal. Here it is the designed case: the lease
expires, the sweep requeues, another machine resumes from the last checkpoint.
The pitch would be fault tolerance to people who experience failure constantly.

**Federated averaging suits the data story.** Researchers often cannot pool
data — different institutions, different consent. Exchanging weight deltas
rather than data is the shape they need anyway.

## 3. The finding that decides it

Google's Colab FAQ, read 2026-08-02, lists among restrictions **specific to the
free tier**:

> "running distributed computing workers"

It is named, explicitly, as a prohibited activity. Also listed, across all
runtimes:

> "using multiple accounts to work around access or resource usage restrictions"
> "employing techniques such as containerization to circumvent anti-abuse policies"

And the free tier additionally bars "remote control such as SSH shells, remote
desktops" and "bypassing the notebook UI to interact primarily via a web UI".

**So the version of this idea that is appealing — pool many FREE accounts — is
prohibited by name, twice.** Not a grey area, not an aggressive reading. The
penalty also lands on the wrong person: Google bans the *researcher's* account,
not ours. Shipping a product that quietly does this to its users is not
something to do by accident, and having found it, not something to do at all.

### 3.1 But the same FAQ opens a legitimate door

Immediately after that list:

> "These restrictions can be removed by purchasing a paid plan."

Distributed compute workers are therefore **explicitly permitted on paid
Colab**. That is a materially different product:

- A lab where members already hold Colab Pro subscriptions can pool them
  without violating anything.
- Paid tiers offer better hardware than the free T4.
- The users are people who have already decided to pay for GPU access — a
  qualified audience rather than a free-rider one.

What is lost is the "free GPUs" economics. What remains is real: several paid
subscriptions already bought, currently used one notebook at a time.

**Nothing here should be built against the free tier.** If this is pursued, it
is a paid-Colab feature, and the documentation must say so plainly, because a
user who assumes the free tier is fine is the one who gets banned.

## 4. What it would cost to support

Three gaps, none small:

**No GPU support at all.** `NodeCapabilities.gpus` is `list[dict]` and is always
empty — `flashnode/inventory/capabilities.py` has no GPU probe. There is no
CUDA image in the curated set. `IsolationAwarePlacement` cannot match a GPU job
to a GPU machine because it does not look at capabilities at all. This is D9,
deferred to M1.5.

**No Docker in Colab.** A Colab runtime is itself a container and cannot nest
one, so `--runner docker` and `--runner argv` are both unavailable — and those
carry the entire sandbox contract (`--network none`, read-only rootfs, non-root
uid, capped cpu/memory).

What remains is `--runner subprocess`: allowlisted modules, scrubbed
environment, no container. Unacceptable for strangers. **Reasonable for a
research group whose members already trust one another**, which is exactly the
audience here. That is a real path, but it means this feature ships with a
weaker isolation guarantee than the volunteer network, and the difference has
to be stated rather than glossed.

**Session lifetime is short and hostile.** 12-hour cap, idle timeouts,
preemption. The lease/checkpoint machinery handles it, but round sizing would
need to assume a task can die at any moment — which argues for smaller shards
and more frequent checkpoints than a laptop pool would need.

## 5. What this would change about priorities

If a research pool is the wedge, the ordering shifts:

| | Volunteer-network priority | Colab-pool priority |
|---|---|---|
| Desktop app (S4) | largest item, install funnel | **not needed** — `!pip install flashnode` in a cell |
| Bundled sandbox VM | required | **not possible**, and not needed among trusted peers |
| GPU support (D9) | deferred to M1.5 | **prerequisite** |
| Result verification (S5) | required before selling strangers' compute | **less urgent** — a lab trusts itself |
| Contributions ledger | credit for hosts | still needed — who contributed what |

That is close to an inversion. The desktop app, currently the biggest planned
build, becomes irrelevant to this audience; GPU support, currently deferred,
becomes the gate.

Worth noticing: this is the **"team's own fleet"** model, not the open
volunteer network. The owner chose the volunteer network earlier
(`2026-08-01-foundation-design.md` §2). This note does not reopen that —
it records that a second, adjacent market exists with a very different
technical shape.

## 6. Before anyone builds this

1. **Re-read the Colab FAQ.** It is a live document; the quotes above are dated
   2026-08-02. The whole assessment rests on them.
2. **Confirm the paid-tier permission in writing**, ideally with Google, before
   telling a customer their subscriptions may be pooled. "Restrictions can be
   removed by purchasing a paid plan" is encouraging but is not a considered
   answer to "may twelve people aggregate their Pro subscriptions into one
   training pool".
3. **Price the alternative.** Kaggle notebooks offer free weekly GPU hours
   under different terms; a rented A100 is a known quantity. If a lab's real
   need is "one big GPU for six hours", pooling twelve small ones may be worse
   on every axis than renting.
4. **Ask one researcher.** The idea assumes labs want this. Nothing here is
   evidence that they do.

## 7. Open questions

- Does federated averaging over 8–12 heterogeneous, frequently-preempted GPUs
  actually converge faster than one researcher's notebook running longer? The
  M1 spec is already honest that collaborative training is not necessarily
  faster; that caveat applies with more force here.
- Is a subprocess-tier pool something to ship publicly at all, or only to a
  named group under an explicit trust agreement?
- Does this belong in the open runtime or the private cloud? A lab pooling its
  own accounts needs no marketplace, no billing, and no accounts — which is an
  argument for it being a `flashruntime` self-hosted capability rather than a
  cloud product.
