# Venue capability findings — which venue the first ResourceProvider targets

**Status: research, 2026-08-12. Answers the three verification questions from
`2026-08-11-alibaba-integration-spec.md` §5 and `2026-08-12-on-demand-capacity-design.md`
§5, for Task 1 of the on-demand-capacity plan.** Consumed by Task 7
(`ResourceProvider` implementation), which cannot start until this lands.

Method: Alibaba's own documentation (fetched live via the Alibaba Cloud
OpenAPI MCP tools) and read-only Alibaba Cloud API calls against account
`5055584162230015` — the same account named in `venues.py` and the Alibaba
integration spec — plus read-only RunPod MCP calls against the account this
environment is already authenticated to. **No resource was created, started,
or rented for this document.** Every API call below was a `Describe*` /
`List*` / `Get*` read, or a documentation fetch.

---

## Step 1 — Can an FC GPU function hold `flashnode work`'s polling loop?

**No. FC GPU is disqualified for this phase.** This is not "probably" — it is
Alibaba's own documented execution model, and it directly contradicts what a
background polling loop needs.

**The mechanism, as Alibaba describes it:**

- A function instance **freezes** the moment it finishes handling requests
  and has nothing in flight, and only **thaws** when the next request
  arrives: *"函数实例中没有请求执行时，当函数执行返回时，函数实例会进入冻结状态"*
  ("when the function instance has no request being processed, once the
  function's execution returns, the instance enters a frozen state") — 函数计算
  FAQ, *"为什么我的异步代码在函数计算中执行异常？"*
  (<https://help.aliyun.com/functioncompute/why-does-asynchronous-code-fail-to-run>,
  doc 2513787). While frozen, code does not run at all until the next
  inbound request. This is stated as the general reason background/async
  work misbehaves in FC — it is not a corner case, it is the billing and
  scheduling model.
- This freeze is confirmed **GPU-specific**, not just a general-FC
  footnote: *"Function Compute introduces a billable item for shallow
  hibernation (formerly idle) GPU usage... When a provisioned
  GPU-accelerated instance is waiting between requests, its GPU resources
  are frozen and billed at USD 0.000007 per compute unit (CU)"* — Product
  Announcement, 2024-06-14
  (<https://www.alibabacloud.com/help/en/functioncompute/the-idle-gpu-usage-billable-item-is-added-to-function-compute>,
  doc 2799727). The Chinese original gives the concrete rate for a Tesla
  card: **0.00011 元/CU active vs 0.00004 元/CU 浅休眠(闲置)** (doc 2799727,
  zh). This is the same active/idle CU split `venues.py` already cites
  (`ada.1: 1.7 CU active against 0.2 CU idle`) — consistent, and it means
  even the *idle* rate is a **frozen, non-executing** state, not a cheap
  running one.
- **Execution Timeout** — the bound on how long a single invocation may run
  — defaults to 60 s and maxes at **86,400 s (24 h)**: *"Set the timeout
  period. The default Execution Timeout is 60 seconds, and the maximum is
  86400 seconds."* — *Create a GPU function*
  (<https://www.alibabacloud.com/help/en/functioncompute/creating-a-gpu-function/>,
  doc 2856906). This is a **per-invocation** ceiling, not a persistent-process
  budget: it bounds one request/response cycle, and `flashnode work` is not
  a request handler waiting on one caller — it is a loop that itself makes
  outbound calls to the coordinator on its own schedule, with no inbound
  request to hold open.
- On-demand (elastic) instances are reclaimed once idle, by design: *"函数计算
  默认使用弹性实例，即通过请求自动触发实例的创建... 无请求后实例自动回收"*
  ("Function Compute uses elastic instances by default — an instance is
  created automatically by an inbound request... and automatically reclaimed
  once there are no more requests") — *函数计算冷启动优化最佳实践*
  (doc 2513659). A November 2025 feature softens this only slightly: GPU
  instances can be configured with a **delayed release** window (default 5
  minutes) after the last request, specifically to reduce cold-start churn
  for bursty-but-not-continuous traffic — *"允许设置在请求结束后的实例延时释放时长（默认5分钟释放）"*
  (doc 2992046). That is a bounded grace period, not a mechanism for keeping
  an instance resident indefinitely while it does its own outbound polling.

**Why the FC Agent Sandbox measurements (2026-08-11) do not transfer.** The
sandbox's survival of a 45-minute `pause()`/`connect()` cycle, with a live
`flashnode work` still claiming leases across it, is a *different product*
with an *explicit, caller-driven* lifecycle API (`create`/`pause`/`connect`/
`kill`) — the controller decides when to hibernate and when to wake, and the
measurement showed state and process both surviving that deliberate
transition. A regular FC GPU function has no equivalent caller-controlled
pause/resume; its freeze/thaw is automatic, tied to inbound HTTP
invocations, and — per the FAQ — freezes as soon as there is no request in
flight. There is no lifecycle hook a `ResourceProvider.acquire()` could use
to say "keep this process alive without an inbound request."

**A second, independent reason FC GPU cannot carry a `ResourceProvider`:**
even setting the freeze problem aside, FC's unit of API control is the
**function plus its elastic-scaling policy**, not a single acquirable
machine. The closest thing to a per-instance create/destroy pair is
`PutProvisionConfig` / `DeleteProvisionConfig` (function API, confirmed via
`ListApis`), which sets a **reserved-instance floor on an already-deployed
function** — it does not create one job-scoped machine and hand back a
handle the way `RunInstances`/`DeleteInstance` (ECS) or `POST /v2/pods`/
`DELETE /v2/pods/{id}` (RunPod) do. The `ResourceProvider.acquire()`/
`release()` shape (§2.1 of the design doc) assumes exactly the latter.

**Conclusion:** FC GPU is unusable for this phase on two independent
grounds — the freeze/thaw execution model cannot hold a spontaneous
polling loop, and the API surface has no per-job acquire/release primitive.
This confirms, with a citable mechanism, what `venues.py` already states as
an absence (`acquisition: none` — "nothing in this repo creates an FC GPU
function") and what the on-demand-capacity design doc treated as an open
question in §5.2. **Marked unusable — not "probably," established.**

---

## Step 2 — Can the instance metadata endpoint be blocked? (D3 requirement 3)

| Venue | Can it be blocked / zero-permission? | Evidence |
|---|---|---|
| **Alibaba ECS GPU** | **Yes, cleanly.** | See below. |
| **RunPod** | **Partially — no network metadata endpoint found, but a credential is auto-injected as an env var with undocumented scope.** | See below. |
| **Alibaba FC GPU** | Moot (disqualified in Step 1); structurally worse than ECS if it mattered. | See below. |

### Alibaba ECS GPU — passes

Two independent controls, confirmed via the live `ModifyInstanceMetadataOptions`
API definition (`Ecs::2014-05-26`):

1. **The metadata endpoint can be fully disabled**, not just hardened:
   `HttpEndpoint` accepts `enabled` / `disabled` — *"是否启用实例元数据的访问通道...
   disabled：禁用"*. Setting it to `disabled` removes the endpoint entirely, at
   creation (as an instance attribute) or after, via this API.
2. Short of full disable, Alibaba's **hardened-only mode** is IMDSv2-equivalent:
   `HttpTokens=required` forces a token-authenticated fetch and rejects the
   unauthenticated legacy path — *"设置该取值后，普通模式无法访问实例元数据"*
   ("with this value set, the normal mode cannot access instance metadata") —
   *使用仅加固模式访问实例元数据*
   (<https://help.aliyun.com/zh/ecs/user-guide/access-instance-metadata-using-hardened-only-mode>,
   doc 2976490). The documented reason to prefer hardening over disabling
   entirely is defense against SSRF via IP-spoofed requests — *"普通方式访问实例元数据，
   请求通过IP地址鉴权，意味着攻击者可以伪造请求的源IP地址，绕过IP地址鉴权，进行SSRF攻击"*
   (same doc).
3. **The zero-permission role is trivial, not just possible.** `RunInstances`'
   `RamRoleName` parameter is optional (`"required": false`, confirmed via
   `GetApiDefinition` on `Ecs::2014-05-26::RunInstances`) — an instance created
   for a rented job simply never gets an `InstanceRamRole` attached, so there
   is no credential behind the metadata endpoint even before `HttpEndpoint`
   is touched. Belt and suspenders: no role **and** the endpoint disabled.

**D3.3 is fully satisfiable on ECS, with two independent, already-existing
API controls (not new Alibaba capability that would need to be requested).**

### RunPod — the network vector is clean, the env-var vector is an open question

No evidence was found, in RunPod's public documentation or in web search, of
a cloud-metadata-service-style network endpoint (the `169.254.169.254` /
`100.100.100.200` pattern) reachable from inside a pod. RunPod pods are not
documented as running atop a metadata-service-exposing hypervisor the way
AWS/Alibaba VMs are; Secure Cloud pods run in dedicated T3/T4-datacenter
capacity and Community Cloud pods are container-isolated on shared hosts,
per RunPod's own security page (via DeepWiki mirror,
<https://deepwiki.com/runpod/docs/11.3-security-and-compliance>). This is
the closest thing to "no metadata endpoint to block" — genuinely absent, not
merely unblocked.

**But RunPod auto-injects a credential a different way.** RunPod's
environment-variables reference
(<https://docs.runpod.io/pods/templates/environment-variables>) lists
`RUNPOD_API_KEY` among the variables "Runpod automatically sets" in every
pod, described only as a **"Pod-scoped API key."** What that scope actually
permits is **not documented publicly** — RunPod's separate "Scoped API Keys"
feature (<https://www.runpod.io/blog/scoped-api-keys-runpod>) describes
account-level GraphQL access ("create, edit, and delete numerous items
associated with your account, including endpoints and pods") and
per-endpoint access grants, but never states whether the *automatically
injected* per-pod key uses that scoping mechanism or is something else
entirely. This is a genuine, recorded unknown, not a guess dressed up as an
answer.

**What is known regardless of that unknown:** this is an environment
variable, not a network service — nothing to firewall, but also nothing that
survives being unset. Two independent mitigations already exist or are cheap
to add, and do not depend on resolving the scope question first:

1. **`--runner trusted` already strips host credentials from job children.**
   Per `2026-08-11-alibaba-integration-spec.md` D4: *"The job child receives
   FlashNode's existing environment allowlist. It never sees the machine
   token, the Alibaba key, or any coordinator credential."* This is a
   property of `flashnode`'s trusted runner generically, not of the Alibaba
   sandbox specifically — if `RUNPOD_API_KEY` is outside that allowlist (the
   expected case, since it is not one of the four allowlisted categories
   named), task code never sees it regardless of what it can do.
2. **The `ResourceProvider`'s own bootstrap can unset it before launching
   `flashnode`**, belt-and-suspenders on top of (1), and does not require
   RunPod to expose a way to suppress the injection (no such setting was
   found).

**Recorded unknown, with a recommended cheap resolution:** before this venue
carries real budget, verify empirically what `RUNPOD_API_KEY` can actually
do — from inside a single short-lived pod (well under $1), call the RunPod
API with the injected key and confirm it cannot create/delete pods outside
itself or read billing. This was **not** done here because creating a pod
to test it was out of scope for a read-only research pass, per this task's
constraints. Until verified, treat it as a real credential and rely on
mitigation (1) and (2) above rather than on RunPod's own scoping.

### Alibaba FC (GPU function) — moot, and structurally worse if it mattered

No network metadata endpoint was found for FC either. Function Role
temporary credentials, when a role is attached, are delivered **directly as
environment variables and via the function's `Context` object** —
`ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET` /
`ALIBABA_CLOUD_SECURITY_TOKEN` — per *配置环境变量*
(<https://help.aliyun.com/zh/functioncompute/environment-variables>, doc
2513539) and *使用函数角色授予函数计算访问其他云服务的权限* (doc 2513600).
Unlike ECS's IMDS, there is no network hop to block — the credential is
already sitting in the process's own environment/context from the moment
the instance starts. The only zero-permission control is the same one ECS
has (do not attach a Function Role — the field is optional), with no
network-layer backstop available even in principle. Moot given Step 1's
disqualification, but worth recording: FC GPU would have been the hardest
of the three to satisfy D3.3 on, not the easiest.

---

## Step 3 — Which venue can be created/destroyed through an already-authenticated API, and at what price?

| Venue | Create call | Destroy call | Repo already holds working credentials? | Live price (measured 2026-08-12) |
|---|---|---|---|---|
| **RunPod** | `POST /v2/pods` (RunPod REST v2; wrapped by the `runpod` MCP server's `create-pod`) | `DELETE /v2/pods/{id}` (`delete-pod`) — confirmed in the OpenAPI spec as *"Permanently terminates and deletes a pod... releasing compute and destroying host-local storage"*, i.e. destroy, not stop, satisfying D3.2 by construction | **No.** No `RUNPOD_API_KEY` anywhere in `.env`, `.env.dev`, `.env.prod`, or `settings.py` (grepped directly). The RunPod MCP tool used for this research authenticates with a credential configured for *this coding session*, not one `flashml-cloud`'s deployed API process can reach. | RTX A5000 24 GB: **$0.16/hr** community / $0.27/hr secure. RTX 3090 24 GB: $0.22/$0.50. RTX 4090 24 GB: $0.34/$0.74. A100 80 GB: $1.19–1.59/hr. H100 80 GB: $2.69–3.29/hr. (`list-gpu-types`, live, 2026-08-12) |
| **Alibaba ECS GPU** | `RunInstances` (Ecs 2014-05-26) — supports `UserData` (base64 cloud-init, confirmed via `GetApiDefinition`) for pull-style enrolment, and `RamRoleName` is optional | `DeleteInstance` / `DeleteInstances` (Ecs 2014-05-26) — a true delete, distinct from `StopInstance` | **No.** `settings.py`'s only Alibaba credential fields are `fc_sandbox_*` (an E2B-style sandbox API key) and `oss_*` (OSS bucket access) — nothing scoped to `ecs:RunInstances`/`ecs:DeleteInstance*`/`ecs:ModifyInstanceMetadataOptions`. | `ecs.gn6i-c4g1.xlarge` (1× Tesla T4, 16 GB), pay-as-you-go, `ap-southeast-1`: **$1.279/hr** (`DescribePrice`, live, 2026-08-12) |
| **Alibaba FC GPU** | No per-job create/destroy primitive exists at all (Step 1) — `CreateFunction` deploys a function definition once, not a rentable instance | N/A | **No.** Same settings.py gap as ECS — no FC-*function*-creation-scoped credential (the `fc_sandbox_*` fields are for the *Sandbox* product, a different API surface than `CreateFunction`/GPU function config). | GPU CU rate confirmed (Tesla tier): 0.00011 元/CU active, 0.00004 元/CU idle (doc 2799727) — moot, since acquisition is impossible per Step 1 |

**The Alibaba credential used for this research is not this repo's
credential, and it is broader than any credential this repo holds.**
`sts GetCallerIdentity` against the account the Alibaba MCP tools are
configured with returns `AccountId: 5055584162230015`,
`Arn: acs:ram::5055584162230015:root` — the **root** account, the same
account referenced throughout `venues.py` and the Alibaba integration spec,
but authenticated as root rather than as a scoped RAM user. This confirms
two separate facts that are easy to conflate: (a) this is genuinely the same
Alibaba account the rest of the system already targets, so an ECS-scoped RAM
user *can* be minted against it without opening a new account; and (b) that
RAM user does not exist yet, and neither `settings.py` nor any `.env*` file
in this repo holds one. **Provisioning a scoped credential (RAM policy
limited to `ecs:RunInstances`, `ecs:DeleteInstance*`,
`ecs:ModifyInstanceMetadataOptions`, in `ap-southeast-1`, added to
`settings.py` + `.env.dev`/`.env.prod` + `render.yaml` together) is
unstarted work for ECS, and the equivalent (`RUNPOD_API_KEY` in the same
three places) is unstarted work for RunPod.** Neither venue has a head
start here; this is a wash between the two live candidates.

---

## Step 4 — Enrolment style per venue (design doc §2.2)

| Venue | Style | Why |
|---|---|---|
| **RunPod** | **Pull** | Already the proven 2026-08-12 recipe (design doc §2.2): mint the FlashNode credential before the pod exists, seed `node-id` + `credentials.json` via the pod's env/start command, install into a venv, fetch the bootstrap script over HTTP so a push can repair a running host. `create-pod`'s `dockerStartCmd`/template mechanism is exactly the "boot with a start command that fetches a bootstrap over HTTP" shape §2.2 names. |
| **Alibaba ECS GPU** | **Pull** | `RunInstances`' `UserData` field (base64 cloud-init, confirmed above) is precisely the "ECS user-data" pull channel §2.2 already names as a fit alongside RunPod. No exec/write-file channel exists for ECS from this API surface — cloud-init on first boot is the only entry point, so pull is not a choice, it is the only option. |
| **Alibaba FC GPU** | Would have to be **pull** (baked into the container image's own startup command) if it were usable at all — FC's deploy-an-image model has no external exec/write-file channel into a running instance the way the FC *Sandbox*'s `SandboxGateway.run`/`write_file` does. Moot: disqualified in Step 1. | — |
| *(FC Agent Sandbox, for contrast — not a GPU venue, already `automatic`)* | **Push** | `SandboxGateway.create`/`connect` plus `run`/`write_file` is a genuine exec channel; `bootstrap_worker` already targets it directly. This is why the Sandbox needed no new enrolment work and the GPU venues do. |

---

## Step 5 — Recommendation

**First provider: RunPod. Runner-up: Alibaba ECS GPU. Alibaba FC GPU is
disqualified.**

**Alibaba FC GPU — disqualified.** Per Step 1: the platform freezes a
function instance the moment it has no in-flight request and only thaws it
on the next one (Alibaba's own documented behavior, confirmed GPU-specific
via the shallow-hibernation billing announcement), which is incompatible
with `flashnode work`'s self-scheduled polling loop; and independently, FC's
API surface has no per-job create/destroy-one-machine primitive for
`ResourceProvider.acquire()`/`release()` to call — `PutProvisionConfig`
manages a floor on an already-deployed function, not a rentable unit.

**Alibaba ECS GPU — capable, and the honest runner-up, not a loser.** It
satisfies every requirement checked here: a clean `RunInstances`/
`DeleteInstance` create/destroy pair, a fully disable-able metadata endpoint
plus an optional (skippable) RAM role, and a pull-style enrolment channel
(`UserData`) already named in the design doc. What holds it back from being
first is pure sequencing, not capability: this repo has zero ECS-scoped
credentials today, and — unlike RunPod — nothing in this system has ever
actually booted `flashnode work --runner trusted` on an ECS GPU instance, so
the GPU-driver/image question (which Alibaba GPU image, whether drivers are
pre-installed the way the FC Sandbox template was probed to be Docker-less
and `ps`-less) is still open and unmeasured. It is also structurally the
more expensive of the two for comparable hardware: $1.279/hr for a single
16 GB T4 (`ecs.gn6i-c4g1.xlarge`) against RunPod's $0.16–0.34/hr for 24 GB
cards.

**RunPod — the recommended first provider.** Three reasons, each
independent of the others:

1. **Already proven, not merely capable.** The design doc states
   `TrustedArgvRunner` was "proven on three RunPod GPUs on 2026-08-12," and
   this research independently corroborates real spend against the exact
   account this environment is connected to: `get-billing` shows
   `podGpuAmount: $0.718` charged on 2026-08-12, consistent with that claim.
   No equivalent live-fire evidence exists for ECS GPU.
2. **Cheaper for equivalent-or-better hardware** across the whole range
   checked (24 GB community cards at $0.16–0.34/hr vs. ECS's $1.279/hr for a
   16 GB T4), which matters because D4's budget gate is a hard dollar
   ceiling, not a preference.
3. **Its one open question (Step 2's `RUNPOD_API_KEY` scope) already has a
   mitigation that does not depend on resolving the question first** —
   `flashnode`'s existing trusted-runner environment allowlist plus a
   one-line `unset` in the bootstrap script — whereas ECS's open question
   (does `flashnode work --runner trusted` actually boot cleanly on an
   Alibaba GPU image) has no such shortcut; it can only be resolved by
   trying it.

**This reverses the design doc's tentative lean.** §5.2 of
`2026-08-12-on-demand-capacity-design.md` guessed that *if* FC GPU could not
hold a process, "Alibaba ECS GPU... is the likely first provider" — written
before this venue-capability check existed, and reasoning from "it's already
named as Stage 5" rather than from a head-to-head comparison. That guess is
superseded by this document, on the evidence above: RunPod is both cheaper
and already working end-to-end; ECS remains a sound **second** provider once
someone has actually booted `flashnode` on one of its GPU images and minted
the scoped RAM credential.

**What Task 7 needs before writing the RunPod `ResourceProvider`:**
1. `RUNPOD_API_KEY` added to `settings.py` + `.env.dev`/`.env.prod` +
   `render.yaml`, all-or-nothing, following the existing pattern for
   `fc_sandbox_*`/`oss_*`.
2. A cheap (sub-$1, single short-lived pod) empirical check of what the
   platform's auto-injected `RUNPOD_API_KEY` can actually do, per Step 2 —
   this is the one item in this document that public documentation could
   not answer and only an experiment can.
3. Confirmation that `flashnode`'s trusted-runner environment allowlist
   excludes `RUNPOD_API_KEY` by default (a code-reading check in the public
   `flashml` repo, not an Alibaba/RunPod question — out of scope for this
   document since it requires reading `flashnode` source, which lives
   outside this repo).
