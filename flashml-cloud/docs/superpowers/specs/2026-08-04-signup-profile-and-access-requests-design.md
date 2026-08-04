# Signup profile and access requests — design

**Date:** 2026-08-04
**Status:** approved, not implemented
**Supersedes:** the invite-as-admission coupling introduced in
`2026-08-03-team-pools-design.md` and migration `0007_pools.sql`.

## Why

Two problems, one flow.

**Nothing is known about anyone.** An account today is an email address and,
if the user bothered, a display name. There is no first name, no company, no
stated reason for being here. Nothing supports a question as ordinary as
"which organisations are trying this" or "what do people say they want to run".

**Uninvited signup is a dead end.** The alpha is invite-only. Someone who
signs up without an invite lands in the console and gets one card telling
them to paste a code they do not have. They cannot ask for access; nothing
queues; nothing notifies. The only trace they existed is a row in
`auth.users`. There is no managed way for people to sign up.

The fix is the same object for both: an onboarding form that doubles as an
access request, and a queue where the owner approves them.

## What changes conceptually

`admitted_at` currently means two things at once — *allowed to use FlashML*
and *member of a pool*. `pool_invites` says so outright: "Consuming an invite
both ADMITS the account through the alpha signup gate and joins it to the
pool."

Those separate.

- **Access to FlashML** is a property of the account. The owner decides it.
- **Pool membership** is a property of the workspace. Its owner decides it,
  like a GitHub org invite.

Account creation and sign-in are identical for every user. There is no
invite-specific signup path and no invite-specific onboarding. An invite link
adds you to a workspace; it does not create your account and does not grant
you the product.

## 1. Flow

Every account is in exactly one access state:

| State | Meaning | What they see |
|---|---|---|
| `needs_onboarding` | Signed up, form not submitted | The form |
| `pending` | Submitted, no decision yet | Waiting screen |
| `admitted` | Approved by an admin | The console |
| `declined` | Declined by an admin | "Not right now" screen |

```
sign up / sign in   (unchanged, identical for everyone)
        │
        ▼
  needs_onboarding ──── submits form ────▶  pending
                                              │
                                     admin approves
                                              ▼
                                          admitted ──▶ console
```

**One door.** An invite does not bypass the queue. A pool owner inviting four
teammates produces four pending requests, not four admitted accounts. This was
chosen deliberately over the GitHub-style "a member's vouch is enough": during
an alpha where result verification enforces nothing (see `POSITIONING_LOG.md`,
2026-08-03, thread 4 open), the owner keeps total control of who is inside.

**Invite links still work end to end.** `/pools/join?token=…` is unchanged in
its routing — middleware redirect, token preserved through `next`. What
changes is its effect for an account that is not admitted: instead of granting
access, it records which workspace that account is owed. On approval the join
is materialised.

**Accepted cost.** The invite's `uses_remaining` is consumed at redeem time,
so declining that person burns one use of the pool owner's link. The
alternative — holding the use until approval — lets a single link be claimed
by unlimited pending accounts. Burning it is the safer trade.

## 2. The form

One screen, shown to every account in `needs_onboarding`. Seven questions,
eight inputs — first and last name share a row.

| Field | Shape |
|---|---|
| First name | text, required |
| Last name | text, required |
| Company / lab / university | text, required |
| Your role | select: researcher, ml_engineer, student, founder, other |
| Team size | select: solo, 2_5, 6_20, 20_plus |
| What do you want to run on FlashML? | free text, required |
| Where's your compute? | multi-select: own_machines, colab, runpod, cloud, none |
| How did you hear about FlashML? | select |

### No separate "company email"

The signup address is the only email. `email_domain` and `is_personal_email`
are derived from it server-side, never accepted from the client.

Requiring a work address at signup was rejected: the audience this release
targets is small labs and researchers pooling Colab and RunPod accounts
(`POSITIONING_LOG.md`, 2026-08-03), a large share of whom sign up with
personal addresses. Blocking them buys cleaner data at the cost of the users.

A second, optional "work email" field was also rejected — unverified, and it
leaves two addresses per account with no rule for which one you actually
contact.

### Employer is `company_name`, and it is self-reported

Asked and settled 2026-08-04. `email_domain` is a corroborating signal, not
the employer field: someone at a company who signs up with Gmail yields
`gmail.com`, which says nothing about where they work. `company_name` —
required free text — is the answer to that question, and nothing verifies
it. Short of requiring a corporate address, which is rejected above, the
only options are to ask and believe.

Two strengthening fields were considered and **rejected for now**:

- **Company website**, optional, normalised to a domain — would give a
  queryable grouping key that survives a personal-email signup.
- **LinkedIn**, optional — would give a one-click identity check on the
  review queue.

Both were declined to keep the form short. The accepted consequence:
`company_name` is free text, so "VinAI", "vinai", and "VinAI Research" are
three rows for one company. At tester scale that is read by eye. Revisit
when the queue is large enough that grouping by employer stops being
something a human can do by looking.

## 3. Data model

Split by lifetime. Profile fields are facts the user owns and edits forever;
the request is a point-in-time record that was screened.

### `public.profiles` — new columns

```sql
first_name         text
last_name          text
company_name       text
role               text        -- researcher | ml_engineer | student | founder | other
team_size          text        -- solo | 2_5 | 6_20 | 20_plus
email_domain       text        -- derived from the signup address
is_personal_email  boolean     -- gmail/outlook/proton/... → true
is_admin           boolean not null default false
```

`display_name` keeps its current meaning — what shows in the nav and on jobs.
It is seeded from `first_name + last_name` on submit and stays independently
editable, so renaming yourself does not rewrite your name.

`is_admin` has no granting UI, by choice. It is set with one `UPDATE` against
the owner's own row. An admin-granting screen at this scale is a liability.

### `public.access_requests` — one row per account

```sql
user_id          uuid primary key references profiles(id) on delete cascade
status           text not null    -- pending | admitted | declined
use_case         text
compute_sources  text[]           -- own_machines | colab | runpod | cloud | none
heard_from       text
pending_pool_id  uuid references pools(id)     -- invite redeemed while unapproved
invited_by       uuid references profiles(id)
requested_at     timestamptz not null default now()
decided_at       timestamptz
decided_by       uuid references profiles(id)
```

RLS enabled with zero policies, like every other table in this schema. The API
is the only door.

**`access` is derived, not stored.** No `access_requests` row →
`needs_onboarding`. Otherwise the row's `status`. This is why the backfill in
the next section matters: without a row, a grandfathered tester would compute
as `needs_onboarding` and be shown the form despite already being admitted.

`admitted_at` on `profiles` remains the flag every existing gate reads, so
`admitted_user` and the seven placement gates need no changes.
`access_requests` is the paperwork; `admitted_at` is still the switch.

### Existing accounts are left alone

Migration `0007_pools.sql` grandfathered every pre-existing account to
`admitted_at = now()`. Those accounts — the real testers who received team
pools on 2026-08-04 — are **not** prompted to complete a profile. Their
`access_requests` row is backfilled with `status = 'admitted'` so the table
has no holes, and their profile columns stay NULL until they choose to fill
them on the Account page.

Consequence, stated so nobody rediscovers it in a query: analysis over
`first_name`, `company_name`, `role`, `team_size`, `use_case`,
`compute_sources`, or `heard_from` covers accounts created after this ships.
Pre-existing accounts are NULL by design, not by bug.

## 4. API surface

Additive wherever possible, following the pattern `/me` already documents
("every existing key from `upsert_profile` is unchanged").

### Changed

- `GET /v1alpha1/me` — gains
  `access: "needs_onboarding" | "pending" | "admitted" | "declined"`. The
  existing `admitted` boolean is unchanged so no current reader breaks. Stays
  on `current_user`: this is the one route every access state must be able to
  read, for the same reason its docstring already gives.
- `PATCH /v1alpha1/me` — widens from `display_name` alone to the fields the
  user owns: `first_name`, `last_name`, `company_name`, `role`, `team_size`.
  Still refuses `admitted_at`, `is_admin`, `is_host`, `is_developer`, and
  `github_login` — the "don't let a client hand us a role" reasoning in its
  docstring is unchanged and load-bearing. `role` and `team_size` are
  validated against their enumerations.
- `POST /v1alpha1/invites/accept` — **stops setting `admitted_at`.** This is
  the coupling being broken.
  - Admitted caller → joins the pool, exactly as today.
  - Un-admitted caller → records `pending_pool_id` and `invited_by` on their
    `access_requests` row, creating a stub row if they have not onboarded yet,
    consumes one use, and returns a "queued" shape.
  Stays on `current_user`, as it already is.

### New

- `POST /v1alpha1/access-request` — the form submission. `current_user`.
  Writes the profile columns and creates the `pending` row. Derives
  `email_domain` / `is_personal_email` from the JWT's email claim.
  Re-submitting while `pending` updates the row; submitting while `admitted`
  or `declined` is a 409.
- `GET /v1alpha1/admin/access-requests` — the queue. Joins `auth.users` for
  the email address, which is possible only because this API holds the
  service-role key; the browser cannot reach that table directly.
- `POST /v1alpha1/admin/access-requests/{user_id}/approve` — sets
  `admitted_at`, flips status, records `decided_at` / `decided_by`, and
  materialises any `pending_pool_id` into `pool_members`. **One transaction:**
  an approval that admits but silently drops the queued pool join is the
  failure this is designed against.
- `POST /v1alpha1/admin/access-requests/{user_id}/decline`.

One new dependency, `admin_user`, beside `current_user` and `admitted_user`.
It reads `is_admin` and raises 403 — same shape and same reasoning as
`admitted_user`: unlike a resource id, the gate's existence is not a secret.

## 5. Web surface

`ConsoleShell` is already structured correctly — one `getMe()` on mount, then
either the console or a stand-in. Its `gated` boolean becomes a four-way
switch on `access`. The optimistic-default reasoning in its comment holds
unchanged: default to admitted, correct when the response lands, so returning
users never see a flash.

- **New** `components/onboarding/OnboardingForm.tsx` — the form from §2.
- **New** `PendingScreen` — states that a human reads every request and that
  the reply goes to their address, shown back to them. No fake ETA, and no
  claim that an email will arrive automatically (see §7).
- **New** `DeclinedScreen`.
- **New** `app/(console)/admin/requests/page.tsx` — the queue: name, company,
  domain, role, team size, use case, invite context, Approve / Decline.
  Rendered only for `is_admin`; the API enforces it regardless.
- **Deleted** `components/shell/InviteGate.tsx`. Its paste-a-code affordance
  moves to `/pools` as "Join with a code", which is where it belongs once an
  invite is a workspace join rather than a door.
- `/pools/join` — for an un-admitted visitor, shows "your invite is saved and
  applies when your access is approved" instead of reloading into a console
  they cannot enter. The `INVITE_GATE_BYPASS` constant in `ConsoleShell` goes
  away with the gate, but the route must stay reachable in every access
  state — that is what lets a pending account bank an invite.
- **Account page** — the new profile fields become editable. The page already
  argues that display name is the one field a user owns; that section grows
  rather than being rewritten. This is also the only path by which a
  pre-existing tester ever fills these in.

## 6. Testing

Follows what exists: `pytest` under `apps/api/tests/`, `vitest` for
`apps/web/lib/*.test.ts`.

The cases that earn their keep:

- Each state transition, including that a `declined` account cannot reach a
  state-creating route.
- A non-admin calling approve or decline gets 403. This is the one test that
  stops the feature becoming a privilege-escalation bug.
- An invite redeemed while un-admitted records `pending_pool_id` **and does
  not set `admitted_at`**. This is the exact coupling being broken, so it
  needs a test pinning the two apart.
- Approval materialises the queued pool join, in the same transaction as the
  admission.
- `email_domain` derivation: subdomains, plus-addressing, mixed case, and the
  personal-provider list.
- `PATCH /v1alpha1/me` rejects `is_admin`, `admitted_at`, and the role flags.

## 7. Known and accepted

Three things this design does not solve, recorded so they are not mistaken
for oversights.

**Approval is silent.** This deployment cannot send email — there is no
provider anywhere in the repo, and Supabase's built-in SMTP allows roughly two
messages an hour project-wide, which is why magic links and email confirmation
were removed in the first place. Approving flips the flag and the account
works on next load, but nothing tells the person. The owner notifies them by
hand. This is fine at tester scale and breaks somewhere around fifty pending
requests, or the first week the queue goes unread. Wiring Resend or Postmark
is the exit, and it would also give the deployment password reset, which it
still lacks.

**`is_admin` is granted by SQL only.** Deliberate; see §3.

**A declined invitee burns one use of the pool owner's link.** Deliberate;
see §1.
