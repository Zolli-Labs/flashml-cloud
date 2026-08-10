# FlashML — product roadmap to real users

**Date:** 2026-08-10. **Status:** reviewed with the owner 2026-08-10 — all
five §6 decisions made; no open product questions remain.
**Owner of this doc:** whoever is acting PM. Update it when priorities move;
log the *why* here, log shipped slices in `PROGRESS.md`.

**The premise.** Everything below starts from a 2026-08-10 audit of the actual
user-facing surface (console routes, API, submission path, host agent, docs).
The core loop is real and complete: paste a public repo URL, preflight, run
sandboxed on pooled machines, artifacts come back, contributions are credited.
What is missing is not the machine — it is the *first hour* and the *tenth
run*. A stranger who signs up today hits, in order: a silent manual-approval
queue with no email, no way to run anything without first recruiting hardware,
no live logs while a job runs, and no way to touch the API outside a browser
tab. Each of those alone loses most of a cohort.

**How to use this doc.** Items marked **designed** have an approved spec in
`flashml-cloud/docs/superpowers/specs/` — execute their plans. Items marked
**needs design** go through the normal brainstorm → spec → plan flow before
code. Items marked **needs decision** are blocked on a product call listed in
§6 — make the call first, cheap to change now and expensive later.

---

## 1. Personas and the funnel

Three personas, in the order the money cares about them:

- **The author** — an ML researcher or engineer with a training script and no
  spare GPUs. Increasingly they write code *with a coding agent*; the
  developer-surface spec (2026-08-10) is built on exactly this premise. They
  judge us in the first session: time-to-first-successful-job is their whole
  impression.
- **The team lead** — pools a lab's or startup's machines for the group. Team
  pools shipped 2026-08-04; this persona exists in the product already. They
  judge us on trust surface: docs, security posture, status page, a domain
  that looks real.
- **The host** — donates or lends a machine. Safety-conscious by definition;
  the sandbox story is strong (`--network none`, read-only rootfs, cap-drop
  ALL) and mostly unadvertised. They judge us on respect: visibility into what
  ran, control over when, and recognition for what they gave.

The funnel, with today's failure point for each stage:

| Stage | Today's wall |
|---|---|
| Sign up | Manual approval, **silent** — no email exists anywhere in the stack |
| First job | Needs a machine the user must enrol first; no demo path |
| Watch it run | Polling + artifacts-after-the-fact; no live logs |
| Come back tomorrow | No re-run, no comparison, no notifications |
| Bring the team | Public repos only; no API access; thin trust surface |
| Keep the hosts | No recognition, no controls, three CLI verbs that error "not implemented" |

## 2. Metrics — what "working" means

- **North star: external successful jobs per week** — jobs from non-team
  accounts reaching `succeeded`. Everything on this roadmap should move it.
- **Activation: time from signup to first successful job (TTFJ).** Target
  **< 15 minutes**, measurable today from `profiles.created_at` and the first
  `succeeded` job per user. Current value is effectively unbounded (approval
  is manual and supply is DIY).
- **Supply health: online machine-hours/week and 4-week host retention.**
  Heartbeats already exist; this is a query, not a feature.
- Guardrail metric: storage-gate rejections and preflight failure rate — a
  rising preflight failure rate means the docs/examples are failing, not the
  users.

## 3. P0 — unblock the first hour

The theme: a stranger with a training script gets to a green job in one
sitting, with no hardware and no human in the loop.

**P0.1 Transactional email.** *(Scope set 2026-08-10: v1 sends **access
approved + declined only**. Job-completion mail and any digest are deferred.)*
Fixes what the code already apologizes for — `PendingScreen.tsx` and
`admin/requests/page.tsx` both carry comments that approval is silent.

Cost review, 2026-08-10 — **everything needed runs on the Supabase free
tier.** Verified against Supabase's live pricing and the org's own account:
custom SMTP, social OAuth (Google), password reset and 500k Edge Function
invocations are all Free-tier features, *identical* to Pro. The one real
constraint is that Supabase's built-in mailer is capped at **2 messages per
hour** and its docs call it "best-effort only… not for production", so a
third-party SMTP provider is required **at any tier** — Pro would have
bought zero additional email capability. **Resend's free tier** (3,000
emails/month, 100/day, 1 custom domain) covers this scope with large
headroom, at $0. Sender domain: `mail.zolliai.com` per §6.5 — verifiable
without moving the website.
Size S. *Metric: signup→return rate after approval.*
**Designed 2026-08-10** —
`specs/2026-08-10-transactional-email-design.md`. Sends from the existing
approve/decline handlers via Resend's HTTP API (not an Edge Function — the
repo has no Supabase deploy surface); no migration, no runtime release.

**P0.2 "Run a sample" + a guided bring-your-own-compute quickstart.**
*(Reshaped by decision §6.4, 2026-08-10: no house-hosted compute for now —
users bring their own machines via Colab, RunPod, or hardware they own.)*
Two pieces: (a) a button on `/w/[poolId]/submit` that pre-fills a known-good
repo from `Zolli-Labs/flashml-examples` — the form already takes a URL, this
is nearly free; (b) a guided two-step first run that chains what already
exists: step 1 "attach compute" (the `ConnectPanel` Colab/RunPod tabs — Colab
free tier is the zero-cost path for both sides), step 2 "run the sample".
Consequence of the decision: Colab/RunPod need `--runner trusted`, and the
trusted-tier execution contract (approved 2026-08-09) documents three bugs
that keep that runner from finishing a task — **those fixes are now on the P0
critical path**, because BYO compute makes trusted-tier the most likely first
machine for exactly the GPU-poor persona we are courting. Size S (button) +
M (guided flow) + the trusted-tier plan. *Metric: TTFJ, now measured
including attaching a first machine — target < 30 minutes.*
Parked deliberately: a house demo pool remains a future growth lever once
there are users worth subsidizing; revisit after launch.
**Designed 2026-08-10** — `specs/2026-08-10-first-run-quickstart-design.md`.
**Gated:** the sample job cannot pass on Colab or RunPod until trusted-tier
§3 (`FLASHML_WORK_DIR`) ships in a flashnode release — its plan is written
and entirely unexecuted. Build the console work in parallel; do not announce
before that release.

**P0.3 Admission that doesn't feel broken.** *(Decided §6.1, 2026-08-10:
manual review stays.)* Build the visible form of it: an explicit "you're
#N, expect an email" pending state, plus the approval email from P0.1.
Depends on P0.1. Size S. *Metric: signup→first-job conversion.*

**P0.4 Live logs.** The single highest-leverage engineering item. Today
stdout/stderr arrive as artifacts after the run, classified client-side by
guessing filenames (`lib/task-artifacts.ts` admits the names are not
contractual). The developer-surface spec's open question #1 flags the same
gap from the agent side: a watcher learns *that* an attempt failed, not
*why*. Design one answer that serves both consumers: relay a bounded log
tail attempt→coordinator→API (consistent with the everything-over-HTTP
architecture — there is no shared disk and must never be), expose it as SSE
on the job page and as `flashml logs -f` later. Also put a stderr tail in
`TASK_ATTEMPT_FAILED.data` so the ledger alone can answer "why". Size L.
**Needs design** — and the spec asks for this to be resolved before its
Plan 3 (MCP) is written, so it sequences early. *Metric: job-page dwell
during runs; support questions of the form "is it doing anything?"*

## 4. P1 — the working loop (why they stay)

**P1.1 Execute the developer surface.** Already **designed** —
`specs/2026-08-10-developer-surface-and-mcp-design.md`, three plans: `fmu_`
developer tokens riding the existing device-code discipline, a `flashml`
package (typed client + CLI + `flashml mcp`), `POST /v1alpha1/preflight`
(validate without a push), and `POST /v1alpha1/jobs/from-upload` (submit a
working tree without a public repo). This one spec closes three walls at
once: no API access, no CI path, no private code. Nothing to decide; build
Plans 1→2→3.

**P1.2 Private code, the second half.** `from-upload` (P1.1) covers the
individual; a **GitHub App** covers the team (per-repo consent, org install,
no pasted tokens — the industry shape). The seam already exists:
`repo.py::fetch_repo_tarball` takes a `token` it is never given. Sequence
*after* `from-upload` ships and only if teams ask — the spec's §7 makes the
same call. Size M. *Decided §6.2 (2026-08-10): pulled by demand — build
when a team asks; `from-upload` is the private-code path until then.*
Shape note (so nobody reaches for the wrong tool): this is **not** Supabase's
GitHub sign-in provider. Supabase remains identity only. The App is a
separate registration on GitHub (permissions: Contents + Metadata,
read-only); the console gets a "Connect GitHub" button that redirects to
GitHub's install screen and stores the returned `installation_id`; at submit
time the API mints a short-lived installation token from the App's private
key and hands it to the existing `token` seam. OAuth `repo`-scope tokens via
Supabase login are the anti-pattern — all-or-nothing access, tied to one
user's account, and they tangle sign-in with authorization.
Cost: GitHub Apps are free to register and operate (rate limits, not fees —
≥5,000 requests/hour per installation); the entire cost of P1.2 is
engineering time, which is why §6.2 is a timing decision, not a budget one.

**P1.3 Metrics as a first-class object.** The code contract already demands
`metrics.json`; that is a schema we own and then throw away into a zip. Chart
it on the job page (loss/accuracy per round for federated), add a sweep
leaderboard (we support ≤100 combos and render the results as 100 archive
files), and a compare view across jobs of one repo. Not W&B — one line
chart, one sortable table, one diff. Size M. **Needs design** (small spec:
what part of metrics.json is contractual). *Metric: jobs per returning user.*

**P1.4 Clone / re-run with overrides.** A "clone" button pre-filling the
submit form, plus in-console override of `args`/`sweep` values, removes the
commit-per-experiment tax. Size S. No design needed beyond a page sketch.

**P1.5 Owner-facing checkpoints.** Checkpoints exist and move over the
coordinator; only agents can reach them. "Download latest checkpoint" on the
job page is a natural artifact and the on-ramp to warm-restarting a
cancelled job later. Size M.

**P1.6 Federated cancel.** `POST /jobs/{id}/cancel` is 501 for federated
jobs. The brake pedal cannot be persona-dependent, and the MCP surface lists
cancel as "the brake". Size M.

**P1.7 Dependencies and data in.** Both halves already have paper:
**designed** — `specs/2026-08-09-dependency-provisioning-design.md` (image
manifest as base, `flashml.yaml` declares extras, installing is a host
capability) and the trusted-tier execution contract (2026-08-09). Execute
those. Then extend the same staging idea to **datasets**: a declared
`datasets:` key (e.g. Hugging Face refs) the host agent fetches and caches
*before* the sandbox closes — tasks keep `--network none`, and our storage
bill stays flat. Dataset extension: **needs design**, after the dependency
plans land.

## 5. P2 — supply, trust, and opening the doors

**P2.1 Host recognition before host payment.** `contributions.py` is
emphatic that credits are a counter and must never grow a balance — keep
that. The BOINC/Folding@home lesson is that recognition, not money, retains
volunteers: per-pool leaderboards, a public "contributed N accepted
task-hours" badge, streaks. `MemberCredits` already computes everything;
this is rendering. Size S–M. *Metric: 4-week host retention.*

**P2.2 Host respect.** (a) A per-machine history page — job name, pool,
image, duration, outcome — turning "you run code you cannot inspect" into
"you can audit what ran". (b) Scheduling windows and a pause verb. (c) Ship
or delete `flashnode join/status/leave`, which are advertised in the usage
text and hard-error "not implemented yet" (`agent/cli.py:648`) — the worst
possible first impression for the most safety-conscious persona. Size M.

**P2.3 The trust surface.** A status page (hosted, free tier is fine); a
security page that finally brags about the hardening we actually do; public
docs for the cloud (`writing-flashml-yaml.md` and the pool guides live in
the *private* repo today; the runtime docs site deploys but the cloud has no
public reference and nothing links the OpenAPI); a changelog; the stale
public READMEs fixed (`flashnode/README.md` still denies the device flow —
our best shipped feature); the **vocabulary sweep** (decided §6.3 as
amended — retire the invented "Zolli" and "Crew" from the interface;
**keep** workspace, and keep the intentional UI-workspace/API-pool split;
`/how-it-works` currently apologizes for the three-way mix in a callout);
a real domain (zolliai.com serves an unrelated product; signup on
`flashml-web.onrender.com` reads as a weekend project); and a **real**
recovery demo — `components/landing/sample-ledger.ts` is synthetic
placeholder data for our headline differentiator. Capture one genuine
preemption ledger and replay it. Mostly S items; the domain is a purchase,
not a feature.

**P2.5 Supabase Pro — a launch-readiness purchase, not a feature.** ($25/mo,
upgradable instantly, so deferring costs nothing.) The org is on `free`
today and that is correct for building. Two Free-tier properties become
unacceptable the moment external users' data lands in `flashml-poc`
(`yualksqjjvlfscbbsygq`, the production project): **Free takes no backups
at all** (Pro: 7 daily), and **free projects pause after one week of
inactivity** — two of the org's four projects are already paused, so this
is demonstrated, not theoretical. Log retention also goes 1 day → 7, which
is what a Tuesday bug report needs on Friday. The headline limits are *not*
the reason: 50k MAU and 500 MB are far away, since artifacts live on the
coordinator disk and jobs/events in FlashRuntime's ledger — Supabase holds
only small relational rows. Buy it when the doors open, not before.
Note: the org sits at Free's **2-active-project cap** (`flashml-poc` +
`flashml-dev`); any third active project forces this purchase early.

**P2.4 Guardrails before open signup.** There is no per-user rate limiting
anywhere in `app.py`; the developer-surface spec flags `POST /preflight` as
the endpoint an agent loop will hammer. Add per-user rate limits, a
compute-time quota alongside the existing 2 GiB storage gate, and an
adversarial review of the preflight rules — open signup plus free execution
on volunteer hardware is a crypto-miner magnet, and `--network none` should
not be the only line. **Blocks P0.3's auto-admit option.** Size M.

## 6. Decisions — status as of 2026-08-10

1. **Admission model — DECIDED (owner, 2026-08-10): keep manual review.**
   Add the visible queue state + approval email (P0.3 proceeds in that
   form). Auto-admit stays off the table until P2.4 guardrails exist, and
   is not currently wanted.
2. **Private code — DECIDED (owner, 2026-08-10): wait on the GitHub App.**
   `from-upload` (already designed, dev-surface Plan 2) is the private-code
   path for now; the GitHub App (free to register/operate — see P1.2 shape
   note) is built when a team actually asks for org-level integration.
   P1.2 stays on the roadmap as a pulled-by-demand item, not scheduled.

   **REVERSED the same day (owner, 2026-08-10): build the App first.** The
   owner asked for it directly and it is now **implemented** —
   `specs/2026-08-10-github-app-private-repos-design.md`, migration 0013,
   `github_app.py`, four routes, and the `/account/github` console pages.
   It is **not registered on GitHub**, so nothing has run against real
   GitHub yet; that is an operator step (spec §9) and will be the first
   true end-to-end evidence.

   *Consequence, stated so it is not discovered later:* `from-upload` and
   the `fmu_` CLI remain unbuilt, so the private-code story is
   **console-only**. A developer with private code can submit it from a
   browser and not from a terminal. P1.1 (developer surface) is unchanged
   and still the next thing that closes that gap.
3. **Vocabulary — DECIDED (owner, 2026-08-10), AMENDED same day.**
   The interface says **machine** and **workspace**. The invented nouns
   **"Zolli"/"Zollis" and "Crew"/"Crews" retire** from the interface in one
   sweep (console + docs only).

   *Amendment, and why:* the decision first read "machine + pool
   everywhere", recommended on the basis that the API/DB/URLs already say
   `pool`. That advice was given without knowing the console's "workspace"
   was **deliberate** — `specs/2026-08-03-workspace-console-design.md` §1
   decision 5: *"'Pool' names a supply of compute; 'workspace' names a place
   people work together, and this design exists to make the console read as
   the second"* — and codified in `flashml-cloud/CLAUDE.md` as *"Do not
   'fix' one side to match the other."* Shown that, the owner kept
   **workspace**. So the UI-says-workspace / API-says-pool split **stays,
   intentionally**, and `CLAUDE.md`'s Vocabulary section stays valid; it
   only needs Zolli and Crew named as retired.

   Net effect: no API change, no migration, and **no route changes** —
   `/w/[poolId]`, `/workspaces` and every shareable invite link are
   untouched. The sweep is a removal of two invented words, not a rename.
   The Zolli character survives as brand/marketing only; that question is
   deferred.
4. **House-hosted compute — DECIDED (owner, 2026-08-10): none for now.**
   Users bring their own compute — Colab, RunPod, or machines they own.
   FlashML hosts nothing on users' behalf at this stage. Consequence:
   P0.2 is reshaped around a guided BYO quickstart and the trusted-tier
   runner fixes join the P0 critical path. Hosting a subsidized demo pool
   is explicitly parked as a post-launch growth lever.
5. **Domain — DECIDED (owner, 2026-08-10): repoint later via Render DNS.**
   Treated as light-weight ops, deliberately deferred. Two cautions stay
   on record: (a) zolliai.com currently serves an unrelated product, so
   "just add DNS" also means deciding that product's fate — verify before
   flipping; (b) P0.1's email sender needs a DKIM-verified domain, but an
   email subdomain (e.g. mail.zolliai.com) can be verified with the
   provider *without* moving the website, so email need not wait for the
   web cutover.

## 7. Non-goals — explicit, so nobody "helpfully" adds them

- **No wallet, no payouts, no marketplace.** `contributions.py` forbids it
  by design and the landing page's honest-state list already promises we
  don't have it. Recognition (P2.1) is the retention tool.
- **No billing/Stripe** until there is a paid tier worth billing for.
- **No self-hosted/on-prem story** this cycle.
- **No second experiment-tracking product.** P1.3 is one chart and one
  table, not W&B.

## 8. Suggested sequence

- **Before any of it:** close the 2026-08 pre-launch security audit
  blockers (unauthorized artifact reads is live in prod; local_inputs and
  the trusted-runner hint block release). No point widening the front door
  first.
- **Now (≈2 weeks):** P0.1 email → P0.2 sample button + guided BYO
  quickstart, with the trusted-tier runner fixes (per §6.4 the Colab/RunPod
  path is now the front door) → developer-surface Plan 1 (`fmu_` identity)
  → live-logs design spec (P0.4, unblocks dev-surface Plan 3).
- **Next (≈1 month):** live-logs implementation → dev-surface Plans 2–3 →
  P1.4 clone/re-run → P1.3 metrics charts → dependency-provisioning plans
  (P1.7).
- **Later:** P1.2 GitHub App (if pulled), P1.5 checkpoints, P1.6 federated
  cancel, P2.1–P2.3 supply/trust wave, P2.4 guardrails, then — and only
  then — loosen admission (P0.3 full form).

Every item that reaches "build" goes through the house flow: spec in
`docs/superpowers/specs/`, plan in `docs/superpowers/plans/`, slices logged
in `PROGRESS.md` with evidence.

---

## 9. Pre-launch checklist — deferred on purpose, do before publishing

Owner decision 2026-08-10: none of this blocks *building*; all of it blocks
*publishing*. Recorded so that "later" has a definite meaning and nothing
here is rediscovered the week of launch.

### 9.1 Rotate every credential, in one pass

The owner's stated intent is a clean slate before the app is published.
Three are already known-exposed rather than merely stale:

- **Production DB password** — `flashml-poc` / `yualksqjjvlfscbbsygq`. Left
  the machine on 2026-08-10 through an editor context attachment. Supabase →
  Database Settings → Reset password; update the single `DATABASE_PASSWORD=`
  line in `.env.prod`; update `DATABASE_URL` on Render. **Letters and digits
  only** — `.env.prod`'s own comment records that every past outage here came
  from percent-encoding a special character wrong.
- **Dev DB password** — `flashml-dev` / `ktstmvgasqupeuimjrog`. Same route,
  same day, same cause.
- **Dev Supabase publishable key** — in git history since 2026-08-02, with
  `.gitleaksignore` still reading *"ROTATION PENDING — owner action"*.
  Rotate, update the `sync: false` value on `flashml-dev-web`, then replace
  that comment with a dated "rotated" note so it stops reading as an open
  action item.

After any rotation, `.env.*` **and** the Render dashboard must both move.
`.env.dev.example`'s three-place map exists precisely because they drifted on
2026-08-04 and every sign-in on the deployed console failed with
"Unregistered API key" — the files were right and the deploy was wrong, which
is the hard direction to spot.

### 9.2 Supabase Pro (§P2.5)

Buy when real users' data lands, for **daily backups** (Free takes none at
all) and **no project pausing**. Explicitly *not* for email — that runs
entirely on the free tier. See §P2.5 for why the headline limits are not the
reason.

### 9.3 Domain (§6.5)

Repoint the console off `flashml-web.onrender.com`.

Status as of 2026-08-10: the **apex `zolliai.com` is verified in Resend for
sending** — DKIM at `resend._domainkey.zolliai.com`, bounce pair on
`send.zolliai.com`. It carries **no MX record**, so nothing receives at any
address on that domain. Consequences to handle at repoint time:

- A real support address needs Google Workspace or a free forwarder
  (Cloudflare Email Routing, ImprovMX). Check the other product on that zone
  first — MX is zone-wide.
- `EMAIL_REPLY_TO` currently points at the owner's personal Gmail because it
  was the only genuinely receiving mailbox. Move it when a real one exists.
- `EMAIL_FROM` must stay on the **verified** domain. Verification does not
  cascade to subdomains: sending from `mail.zolliai.com` against an
  apex-verified domain returns `403 "domain is not verified"`, an error that
  names the domain and never the variable holding it.

### 9.4 Email operator setup, steps 5–6

Point **Supabase Auth's custom SMTP** at Resend on both `flashml-poc` and
`flashml-dev`. This lifts the built-in 2-messages-per-hour cap and is what
makes **password reset** work; it does *not* carry the approve/decline mail,
which the API sends directly. Leave **"Confirm email" OFF** unless the
two-step signup flow is redesigned.

### 9.5 Security-audit remediation

A separate agent track, not this roadmap's — but still the first gate in §8.
Unauthorized artifact reads are live in production.

### 9.6 Track this file

`ROADMAP.md` is **untracked** as of 2026-08-10. It is the product plan and
one `git clean -fdx` from gone. `git add` it with the next commit — the same
liability the transactional-email whole-branch review flagged for that
feature's spec and plan, which were fixed the same day.
