# Transactional email — the admission decision stops being silent

**Date:** 2026-08-10
**Status:** proposed design, awaiting owner review.
**Repos touched:** `flashml-cloud` only — one new API module, two handler
edits, two console copy edits, three env vars. **No migration. No runtime
release. No pin bump.**
**Roadmap item:** P0.1 (`ROADMAP.md`), unblocked by owner decisions §6.1
(manual review stays, made visible) and §6.5 (mail subdomain can be verified
without the web cutover).

**Origin.** The product admits users by hand and tells them nothing. Two
places in the codebase apologize for it in comments, and one lies to the
admin instead — `admin/requests/page.tsx:95-99` prints *"Approved — they're
in. Let them know yourself."* This is the smallest change that turns a
manual-review alpha from feeling broken into feeling deliberate.

---

## 1. Decisions

1. **Send from the API's existing approve/decline handlers — not a Supabase
   Database Webhook, not an Edge Function.** This reverses the shape floated
   in conversation on 2026-08-10, on evidence gathered afterwards: the repo
   has **no `supabase/` directory, no `config.toml`, no Edge Function, no
   Supabase CLI dependency, and no use of the Management API or the
   service-role key anywhere** (`test_agent_proxy.py:1294` actively pins that
   the app starts without a service-role key). An Edge Function would be a
   brand-new deployment surface, a second secret store, and a webhook
   configuration that lives only in a dashboard — config drift of exactly the
   kind `migrate.py:4-7` exists to end. The API route is already the single
   funnel every admission passes through, already tested, and already holds a
   DB connection.
2. **Delivery is Resend's HTTP API over `httpx`, not SMTP.** It reuses the
   `transport` seam already threaded through `create_cloud_app`, so the
   existing fake-transport test pattern captures mail with no new tooling.
   SMTP via `smtplib` would be blocking and has no comparable seam.
3. **No API key configured ⇒ no send, no error.** The mailer is inert unless
   `RESEND_API_KEY` and `EMAIL_FROM` are both set. This keeps local dev and
   all 37 existing API test files unaffected, and follows the
   optional-but-warned precedent of `console_url` (`settings.py:149-154`)
   rather than the hard-required list.
4. **Email failure must never fail an admission.** The DB write is committed
   before any send is attempted; a send exception is caught, logged, and
   swallowed. The route then reports what actually happened (decision 5).
5. **The approve/decline routes return `emailed: bool`,** and the console
   toast tells the truth from it. Replacing "let them know yourself" with an
   unconditional "we emailed them" would just relocate the dishonesty.
6. **Exactly-once is structural, not bookkept.** `approve_access_request` and
   `decline_access_request` are both guarded by `and status = 'pending'`
   (`db.py:323-384`), so a second call matches zero rows and the route 404s
   before reaching the mailer. No sent-log table, and therefore **no
   migration** — `0012` stays free for the developer-surface spec's
   `0012_cli_credentials.sql`.
7. **Scope is approved + declined only** (owner, 2026-08-10). Job-completion
   mail, digests and notification preferences are out. Password reset and
   email confirmation become *possible* the moment custom SMTP is configured
   here, but ship separately — see §9.
8. **Supabase stays on the free tier.** Custom SMTP, social OAuth and 500k
   Edge Function invocations are Free-tier features identical to Pro; the
   built-in mailer's 2-messages-per-hour cap is why a third-party provider is
   needed *at any tier*. Pro is a launch-readiness purchase for backups and
   no-pausing (`ROADMAP.md` P2.5), not a prerequisite here.

---

## 2. What exists today

| Piece | State | Where |
|---|---|---|
| Admission table | `public.access_requests`, status is **`'pending'` / `'admitted'` / `'declined'`** | `migrations/0009_access_requests.sql:56-67` |
| Approve | sets `status='admitted'`, stamps `profiles.admitted_at`, joins `pending_pool_id` if present | `db.py:323-366` |
| Decline | sets `status='declined'` only | `db.py:369-384` |
| Routes | `POST /v1alpha1/admin/access-requests/{user_id}/{approve,decline}`, `Depends(admin_user)`, 404 when nothing was pending | `app.py:1219-1241` |
| Address lookup | `email_for_user(db, user_id)` — already exists, reads `auth.users.email` | `db.py:190-200` |
| `emails.py` | **classifier only** — free-provider domain list + `derive_email_facts`. Sends nothing. | `emails.py:1-48` |
| Outbound mail anywhere | **zero.** Two prose comments in `SignInCard.tsx` and nothing else. | — |
| DB triggers / functions | **zero** in every migration | — |
| Waiting-user copy | "Already approved? Reload this page." | `PendingScreen.tsx:40-69` |
| Admin toast | "Approved — they're in. Let them know yourself." | `admin/requests/page.tsx:95-99` |

**The trap to not fall into:** the status literal is **`admitted`**, not
`approved`. The route is *named* approve and returns `{"status": "admitted"}`.

Note also that `profiles` has **no email column** — only the derived
`email_domain`. The address is read from `auth.users` over libpq, which
`0009:19-20` documents as deliberate: *"auth.users is deliberately untouched…
the email address is read from it, never written."*

---

## 3. Setup, before any code (one-time, ~30 minutes)

1. **Resend account, free tier** — 3,000 emails/month, 100/day, 1 custom
   domain. No card. Ample: this scope sends one email per admission decision.
2. **Verify the sender domain `mail.zolliai.com`.** Add Resend's DKIM/SPF
   records to the `zolliai.com` zone. Per owner decision §6.5 this is a
   *subdomain* — it does not touch where the apex points, so the website can
   stay on `flashml-web.onrender.com` until the repoint. **Check first that
   the other product's mail on that zone is undisturbed.**
3. **Point Supabase Auth's custom SMTP at Resend** (Project → Auth → SMTP
   Settings) on **both** projects — `flashml-poc` (prod,
   `yualksqjjvlfscbbsygq`) and `flashml-dev`. This governs auth mail only; it
   is what lifts the 2/hour built-in cap and is the prerequisite for §9. It
   does **not** carry the emails in this spec.
4. **Leave "Confirm email" OFF.** `SignInCard.tsx:45-50` records it as a
   deployment prerequisite; turning it on changes the two-step signup flow and
   is out of scope.
5. **Create a Resend API key** (send-only). It goes in Render as `sync: false`
   and never into git.

*Local dev without a domain:* Resend's `onboarding@resend.dev` sender works
with no DNS, but delivers only to the account owner's own address — fine for
a smoke test, not for staging.

---

## 4. The mailer

New module `apps/api/flashml_cloud_api/mailer.py`. `emails.py` stays exactly
as it is — it classifies addresses, it does not send; `test_emails.py` should
not need a line changed.

```python
class Mailer:
    """Sends product mail through Resend's HTTP API.

    Inert unless both RESEND_API_KEY and EMAIL_FROM are set: an unconfigured
    deployment must run, and every existing test must pass, without sending
    or raising. Mirrors CoordinatorClient's shape — settings in, optional
    httpx transport for tests, one AsyncClient per call.
    """

    def __init__(self, settings, transport=None, timeout: float = 10.0): ...

    @property
    def configured(self) -> bool: ...

    async def send(self, *, to: str, subject: str, html: str, text: str) -> bool:
        """True if Resend accepted it. Never raises."""
```

Constraints that matter:

- **`timeout=10.0`, not the coordinator's 60.** This sits inside an admin's
  click; a hung provider must not hold the request open for a minute.
- **Inline `await`, not a thread and not `BackgroundTasks`.** `fedavg.py:589-604`
  rejects `BackgroundTasks` for work "that would occupy a worker for hours" —
  that reasoning does not reach a sub-second HTTP call, and a daemon thread
  here would buy nothing but a lost error path. If p95 latency on the admin
  action ever becomes a complaint, revisit; do not pre-optimize.
- **Errors never escape.** Non-2xx, timeout, connection error → log a warning
  and return `False`. Log the `user_id` and the status code, **never the
  address or the response body** — consistent with `app.py:384-388`, which
  swallows coordinator exception strings because they can carry credentials.
- Reuses the same optional `transport` already accepted by `create_cloud_app`,
  constructed once alongside `CoordinatorClient` at `app.py:764`.

---

## 5. Wiring

Both handlers gain the same three lines after the DB write succeeds, and
nothing before it changes:

```python
if not dbmod.approve_access_request(db, user_id, decided_by=admin_id):
    raise HTTPException(status_code=404, detail="no pending request")
address = dbmod.email_for_user(db, user_id)
emailed = await mailer.send(**admitted_email(address, console_url)) if address else False
return {"user_id": user_id, "status": "admitted", "emailed": emailed}
```

Ordering is the whole design: **the 404 guard runs first**, so the mailer is
unreachable for an already-decided request. That is where exactly-once comes
from. A user with no address in `auth.users` (possible for a hand-seeded row)
yields `emailed: False` and no crash.

`decline_request` is identical with `declined_email(...)` and
`{"status": "declined", "emailed": ...}`.

---

## 6. The two emails

Copy follows the product's existing voice — the decline text deliberately
echoes `DeclinedScreen.tsx` so the screen and the mail do not contradict each
other. Vocabulary is **machine** and **workspace** per owner decision §6.3
as amended — never "Zolli" or "Crew".

**Admitted** — subject: `You're in — FlashML`

> Your FlashML access request was approved.
>
> **[Open the console]** → `{console_url}`
>
> One thing before your first run: FlashML runs your training on machines
> you attach — a Colab notebook, a RunPod pod, or hardware you own. The
> console walks you through connecting one, then you can point it at a public
> GitHub repo and go.

**Declined** — subject: `About your FlashML request`

> We couldn't approve your FlashML request right now. That's a capacity
> decision, not a permanent one — this is a small alpha and we admit in
> batches.
>
> If what you're trying to run changes, reply to this message and tell us
> about it.

Both ship as HTML plus a plain-text alternative in the same send. The decline
invites a reply because `DeclinedScreen.tsx:10-16` records that re-applying is
blocked by design (`POST /access-request` 409s once decided) — so a reply
address is the only door left, and `EMAIL_REPLY_TO` must therefore be a real
monitored mailbox.

---

## 7. Console changes

**`PendingScreen.tsx`** — the copy can finally be honest. Replace *"Already
approved? Reload this page."* with a line stating that an email is coming to
the address shown. Delete the comment block at lines 14-22 that explains why
no email is promised; it stops being true with this change. The Reload button
stays (it is still the fastest path for someone already holding the tab open).

*Drive-by, per owner decision §6.3 as amended:* the same file's "Have a Crew
invite code?" link should read **"workspace"** — "Crew" is one of the two
invented nouns retiring; "workspace" stays as the interface word. One word,
and it advances P2.3.

**`flashml-cloud/CLAUDE.md` must be amended in the same change.** Its
"Granting access and admin" section currently ends:

> **Approval is silent.** No email provider exists in this repo, and
> Supabase's built-in SMTP is ~2 messages/hour project-wide… Approving flips
> the flag and the account works on next load; telling the person is manual.
> **No copy anywhere may imply a message was sent.**

That last sentence is a standing instruction to every future agent and it
directly forbids what this spec ships. Rewrite it to state that approval now
emails the account, that the mailer is inert without `RESEND_API_KEY`, and
that copy must reflect the `emailed` flag rather than assume either outcome.
Leaving it in place guarantees a later agent "fixes" the new copy back.

**`admin/requests/page.tsx`** — the toast reads from the new `emailed` field:

- `emailed: true` → "Approved — they're in, and we've emailed them."
- `emailed: false` → keep today's honest fallback: "Approved — they're in.
  Email didn't go out, so let them know yourself."

The approve/decline **response** type in `lib/cloud-api.ts` gains `emailed`
(this is not `AccessRequestRow` at `:1082-1099`, which types the *list* rows
and is unchanged).

---

## 8. Configuration

Three new `Settings` fields, all defaulted to `""` so the ~7 test files that
construct `Settings(...)` positionally need no edit:

| Env var | Field | Tier |
|---|---|---|
| `RESEND_API_KEY` | `resend_api_key` | optional; warn once at boot if `EMAIL_FROM` is set without it |
| `EMAIL_FROM` | `email_from` | optional; e.g. `FlashML <noreply@mail.zolliai.com>` |
| `EMAIL_REPLY_TO` | `email_reply_to` | optional; falls back to `email_from` |

`FLASHML_CONSOLE_URL` already exists (`settings.py:51-67`) and supplies the
sign-in link — no new variable for it.

**None of these join the `missing` hard-required list.** A deployment with no
mail configured must still boot; the failure mode is a silent product, not a
dead API. Follow the `console_url` warning precedent, and note that
`settings.py:118-131` and `render.yaml:182-191` jointly require that a secret
be added to `render.yaml` **and** `from_env` in the same change.

`render.yaml`, both `flashml-api` and `flashml-dev-api`:
`RESEND_API_KEY` as `sync: false`; `EMAIL_FROM` and `EMAIL_REPLY_TO` as plain
`value:` (they are addresses, not secrets).

---

## 9. Out of scope — and what this unlocks for nearly free

Deferred deliberately:

- **Job-completion mail.** The obvious next email and the retention one, but
  it needs a delivery trigger in the job lifecycle rather than an admin click.
- **Queue position ("you're #N").** `ROADMAP.md` P0.3 proposed it; this spec
  recommends against shipping it now. The raw material exists
  (`access_requests_status_idx on (status, requested_at)`) but nothing exposes
  it to a non-admin, so it needs a new field on `GET /v1alpha1/me`. More to
  the point, a number sets a delivery expectation that manual review cannot
  honour — "#3" implies today. The broken part was silence, and the email
  fixes that. Owner can pull it back in.
- **Notification preferences / unsubscribe.** These two mails are
  transactional (a direct response to the user's own request), so no
  unsubscribe is required. The first *marketing* or digest email changes that
  and should not be bolted onto this path.
- **Per-pool branching in the approval mail** when `pending_pool_id` is set.

Unlocked by §3's SMTP configuration alone, each a small separate slice:

- **Password reset** — Supabase's built-in flow starts working; it needs a
  "forgot password" link and a reset page. `SignInCard.tsx:323` currently
  states in copy that reset does not exist.
- **Email confirmation on signup** — possible, but changes the two-step signup
  flow; treat as its own decision.
- **Google sign-in** is independent of all of this — OAuth is a redirect flow
  and sends no mail.

---

## 10. Testing

House pattern: a fake `httpx.AsyncBaseTransport` injected through
`create_cloud_app`, as in `test_jobs_from_repo.py:111-159`. New tests go in
`tests/test_admin_access_api.py` (reusing its `_admin`, `_pending`,
`_request_row` helpers) plus a new `tests/test_mailer.py`.

Each of these should be watched failing first:

1. Approving a pending request sends **one** mail, to the address in
   `auth.users`, with the admitted subject; route returns `emailed: true`.
2. **Approving twice sends exactly one mail** — the second call 404s before
   the mailer. Extends the existing
   `test_approving_twice_is_a_404_the_second_time`.
3. Declining sends the declined template, not the admitted one.
4. **Resend returns 500 ⇒ the user is still admitted.** Assert
   `profiles.admitted_at` is set and `access_requests.status = 'admitted'`,
   and the route returns 200 with `emailed: false`.
5. **Resend times out ⇒ same as 4**, and the request does not hang past the
   10s timeout.
6. **No API key ⇒ no HTTP call at all** and `emailed: false`. This is the
   configuration every other test file runs under, so it also proves the 37
   existing files are unaffected.
7. A pending row whose `auth.users.email` is NULL ⇒ `emailed: false`, no
   crash, admission still succeeds.
8. Neither mail body nor logs contain the API key; the failure log carries the
   `user_id` and status code and **not** the address.

Definition of Done is `HANDBOOK.md` §8 as usual — red test first, full API
suite green, docs updated in the same session, `PROGRESS.md` entry with
evidence.

---

## 11. Implementation plan

One plan, one slice, ends runnable:
`plans/2026-08-10-transactional-email.md`.

1. `mailer.py` + the three `Settings` fields + `test_mailer.py` (tests 6, 8).
2. Templates module with both bodies (§6).
3. Handler wiring + `emailed` in the response + tests 1–5, 7.
4. Console: `PendingScreen` copy and comment removal, admin toast, the
   `cloud-api.ts` type.
5. `render.yaml` for both API services; Resend + Supabase SMTP setup per §3.

**Demo that closes it:** approve a real pending account in the dev console and
have the mail arrive, then approve it again and show that no second mail is
sent and the route 404s.
