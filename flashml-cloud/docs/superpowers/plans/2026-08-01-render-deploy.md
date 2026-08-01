# Render Deploy and Acceptance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A URL a friend can open on their phone, sign into with Google, enrol their laptop against, and contribute compute to — with the whole loop working end to end on real hardware over the real internet.

**Architecture:** Three Render services. `flashml-web` and `flashml-api` are public; **`flashml-coordinator` is private and has no public URL**. Supabase provides Auth, Postgres, and Storage. Curated task images live in GHCR.

**Tech Stack:** Render (web services + private service + persistent disk), Supabase, GHCR, Docker.

This is **Plan 7 of 7** for M1 — the last one. It implements §3.1 and the §10 acceptance criteria of
`docs/superpowers/specs/2026-07-31-deployed-multi-user-poc-design.md`.

---

## The one thing that must not be got wrong

**The coordinator must not be publicly reachable.** Not "should be", not "ideally". Every security property built in Plans 2 and 3 assumes the cloud API is the only door:

- The API is what authenticates machines — the coordinator has no database and cannot.
- The API holds the **operator credential**, which is unscoped by design. If the coordinator were public, an attacker who obtained that token could act as any machine.
- `HANDOFF.md` risk #5 requires the coordinator to run as a **single uvicorn worker**; `LeaseManager` and `SqliteLeaseStore` are only safe on one event loop. A public service invites autoscaling.

If Render cannot give a private service reachable from a sibling service, **stop and report** rather than deploying the coordinator publicly with a "we'll fix it later". That is exactly the shape of decision that never gets revisited.

## Global Constraints

- **No secret enters git.** The Supabase service-role key, JWT secret, coordinator operator token, and machine tokens are all Render environment variables. `flashruntime/scripts/audit_secrets.sh` must stay CLEAN.
- **`FLASHML_REQUIRE_NODE_AUTH=1` on the coordinator.** It refuses to boot without tokens configured — so a misconfigured deploy fails loudly instead of silently running open.
- The coordinator needs a **persistent disk** for `leases.db`. Without it, a restart loses in-flight leases.
- `flashml-api` and `flashml-coordinator` **must not sleep**. A suspended coordinator drops heartbeats and expires live leases. `flashml-web` may sleep.
- **Claim only what has been run.** This plan ends in an acceptance run; anything not actually executed gets written down as not executed.

---

### Task 1: Build and publish the curated images

**Files:** `flashml-cloud/images/*/Dockerfile` (from Plan 6 Task 1), `.github/workflows/images.yml`

Build the three curated images and push to GHCR at the **exact pinned references** `images.py` resolves to. A mismatch here is invisible until a task fails on a volunteer's machine with an image-pull error.

Each image must declare a **non-root `USER`** — Plan 6 depends on this for Windows hosts, where `--user` is omitted and the image's own user is the only thing keeping the container non-root.

Verify by pulling each reference on a clean machine and running `id` inside it: the uid must be non-root and match across all three.

---

### Task 2: Provision Supabase for production

Enable **Google OAuth** in the Supabase dashboard (Authentication → Providers → Google) with a Google Cloud OAuth client, and register the callback URL the deployed web app will use. **This is a manual dashboard step that cannot be done from the repo** — it is the single most likely thing to be forgotten, and it fails at sign-in with a provider error that looks like a code bug.

Confirm the schema is applied to project `yualksqjjvlfscbbsygq` (and no other — the org's other projects are a different product). Re-run the RLS check: `anon` and `authenticated` must both see **zero rows** in a table containing data, while `service_role` sees them.

Create the Supabase Storage bucket for artifacts and record its S3-compatible endpoint and credentials.

---

### Task 3: Deploy the coordinator (private)

`flashml-coordinator` as a Render **private service**, no public URL, persistent disk mounted where `FLASHML_LEDGER_PATH` and the lease store live, **one uvicorn worker**.

Env: `FLASHML_REQUIRE_NODE_AUTH=1`, `FLASHML_OPERATOR_TOKENS` (one token for the API), `FLASHML_LOCAL_ARTIFACTS_DIR` or the Supabase Storage config, `FLASHML_ENABLE_KUBERAY=0`.

**Verify it is genuinely private:** from outside Render, requests to its hostname must fail to connect. Record the actual command and its output — "it should be private" is not evidence.

---

### Task 4: Deploy the API (public)

`flashml-api` as a public web service. Env: `SUPABASE_URL`, `DATABASE_URL` (Supabase **session pooler** — the direct connection is IPv6-only and Render may not route it), `COORDINATOR_URL` (the private hostname), `COORDINATOR_OPERATOR_TOKEN`, `REQUIRE_AUTH=1`.

`SUPABASE_SERVICE_KEY` is **not** set and is not required: no code path reads it, and it is the one credential that bypasses every RLS policy and every owner-scoped query. `SUPABASE_JWT_SECRET` stays blank too — this project signs ES256 and tokens verify against the JWKS.

Verify: `/healthz` responds publicly; an unauthenticated agent route returns **401**; a request with a valid machine token reaches the coordinator (confirmed via coordinator logs); and the operator token appears in **no** response body, header, or log line.

---

### Task 5: Deploy the web app

`flashml-web` with `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, and the API base URL. **Only the anon key** — grep the built bundle for the service-role key and the operator token and confirm neither appears.

Verify sign-in works on a phone browser, not just a desktop one.

---

### Task 6: The acceptance run (§10)

This is the deliverable. Every step performed for real, with the result recorded — including anything that fails.

1. The public URL loads on a **phone** and a laptop; Google sign-in works on both.
2. **A Mac and a Windows machine** enrol via `flashnode login`, approved from a phone browser, and appear online. Windows has so far been *constructed-argv-verified only* — this is where it becomes execution-verified, or does not.
3. A public GitHub repo with PyTorch code and a `flashml.yaml` is submitted from the browser.
4. **The model trains across both machines**: several rounds, each with more than one contributor, loss decreasing, and the job view naming which machine contributed to which round.
5. Closing the lid on one machine mid-round does not stall the job — the round completes on quorum and the machine rejoins without manual steps.
6. Final weights and metrics are downloadable.
7. Revoking a machine stops it receiving work within one claim interval.
8. A crafted upload outside the caller's live lease returns **403** against the deployed stack.
9. Preflight rejects a repo importing a package its image lacks, naming the package.
10. **A friend — not the developer — completes signup → enrol → contribute, unaided, from the written instructions.**

Items 4 and 10 are the real tests. The rest can be passed by someone who already knows where the bodies are buried.

---

### Task 7: Final documentation

`../PROGRESS.md` entry with the acceptance results — **real numbers, real outcomes, failures included**. Tick Plan 7 and M1 in the checklist.

`M1_DECISIONS.md`: anything the deploy forced a decision on.

A short runbook: how to redeploy, rotate the operator token, revoke a machine, and read logs.

**State what M1 still does not do:** result verification is unbuilt, so a host can lie about results and be believed (M3); no GPU support (M1.5); no self-service host invites if enrolment is gated; capability-aware placement is M2. Anyone reading this later must not mistake a working POC for a finished product.

---

## Self-Review

**Spec coverage.** §3.1 topology → Tasks 3, 4, 5. §10 acceptance → Task 6. The curated images §5.3 depends on → Task 1. The Google OAuth manual step, which appears in no code → Task 2.

**Deliberately not here:** custom domain (`zolliai.com` not purchased; the Render subdomain is fine and nothing depends on the hostname); GPU hosts; result verification.

**Known risks.** (a) Render's private-service networking is the load-bearing assumption — if it cannot be verified, stop rather than deploy the coordinator publicly. (b) Task 6 item 2 is the first time Windows runs for real; expect to find something, and budget for it. (c) Free-tier sleep on the API or coordinator breaks the lease model — confirm the plan before deploying, not after a job mysteriously stalls.
