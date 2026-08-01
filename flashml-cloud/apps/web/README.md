# FlashML Web

The browser surface of FlashML: sign in with an email link (or Google, once
configured — see below), approve a machine from your phone, submit a repo,
and watch it train.

This app holds **no business logic** and talks to **no database**. It calls
the cloud API (`apps/api`) for everything except authentication, and it
authenticates with Supabase Auth. See the plan at
`docs/superpowers/plans/2026-08-01-web-app.md` for the full design and
constraints — the two that matter most for anyone touching this code:

- **The browser never touches Postgres.** `@supabase/supabase-js` /
  `@supabase/ssr` are used for auth only. If a screen seems to need a
  database query, that means the cloud API is missing an endpoint, not that
  this app should reach past it.
- **Only the anon key may reach the browser.** The Supabase service-role key
  and the coordinator's operator token must never appear in client code, a
  `NEXT_PUBLIC_*` variable, or the built bundle.

## Environment variables

Create `apps/web/.env.local` (gitignored) with:

```bash
# Supabase project — safe for the browser: anon key only, never the
# service-role key.
NEXT_PUBLIC_SUPABASE_URL=https://yualksqjjvlfscbbsygq.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=<the anon/publishable key, from Supabase dashboard → Project Settings → API>

# The cloud API this app calls for everything else (apps/api).
NEXT_PUBLIC_CLOUD_API=http://localhost:8000
```

Nothing else is read from `process.env` in browser-reachable code. If you
find yourself adding a new `NEXT_PUBLIC_*` variable, ask whether the value
is safe for every visitor to read in devtools — it will be.

## Sign-in: email works today, Google needs one-time setup

`/sign-in` offers two paths:

- **Email magic link** (`supabase.auth.signInWithOtp`) — works out of the
  box against any Supabase project with email auth enabled, which is the
  default. No password storage, no extra provider config. This is the path
  the acceptance run (a friend signing in on their phone) actually uses
  today.
- **Google OAuth** — requires the one-time dashboard setup below. Until
  that's done, clicking "Continue with Google" shows "Google sign-in isn't
  set up for this deployment yet — use email above" instead of a raw
  Supabase provider error or a silent no-op.

## Manual step: enable Google sign-in (do this once per Supabase project)

This cannot be inferred from the repo or done in code — it is dashboard
configuration. Skip it and, without the graceful-degradation handling in
`SignInCard.tsx`, sign-in would fail with a Supabase provider error that
reads exactly like a code bug. With that handling in place it instead shows
a clear message pointing back at this section — but the button still won't
work until these steps are done.

1. **Create a Google OAuth client.** In
   [Google Cloud Console](https://console.cloud.google.com/apis/credentials),
   create an OAuth 2.0 Client ID of type "Web application". Under
   **Authorized redirect URIs**, add exactly this URL — Supabase's fixed
   callback endpoint for this project, **not** this app's own
   `/auth/callback` route:

   ```
   https://yualksqjjvlfscbbsygq.supabase.co/auth/v1/callback
   ```

   Copy the resulting Client ID and Client Secret.

2. **Enable Google in Supabase.** In the
   [Supabase dashboard](https://supabase.com/dashboard/project/yualksqjjvlfscbbsygq) →
   **Authentication → Providers → Google**, turn the provider on and paste
   in the Client ID / Client Secret from step 1.

3. **Allow this app's callback URL.** In **Authentication → URL
   Configuration → Redirect URLs**, add this app's own callback route (the
   handler at `apps/web/app/auth/callback/route.ts`) for every origin you
   run from, e.g.:

   ```
   http://localhost:3000/auth/callback
   https://<your production domain>/auth/callback
   ```

   Supabase only redirects back to URLs on this list after the Google hop
   completes; anything else is refused.

Until all three are done, clicking "Continue with Google" shows the
graceful fallback message described above. Note that step 3's redirect URL
allow-list is shared with the email magic-link flow (both send the browser
back to this app's own `/auth/callback`), so it's worth setting up even
before Google is fully wired.

## Run it locally

```bash
cd apps/web
npm install
npm run dev
```

Open `http://localhost:3000`. This runs against `NEXT_PUBLIC_CLOUD_API`
(default `http://localhost:8000`) — start `apps/api`'s cloud app separately,
pointed at a real Supabase project and a coordinator (see `apps/api`'s own
docs for `SUPABASE_URL` / `SUPABASE_JWT_SECRET` / `COORDINATOR_URL`, etc).

## Verify

```bash
cd apps/web
npm run build   # must pass clean
npm test        # vitest — lib/cloud-api.test.ts
```

## Auth flow, in one paragraph

`middleware.ts` refreshes the Supabase session on every request and
redirects signed-out visitors away from private routes to `/sign-in`.
`/sign-in` (`app/(auth)/sign-in/SignInCard.tsx`) offers an email field
("Continue with email") and a "Continue with Google" button. Email calls
`supabase.auth.signInWithOtp`, which emails a magic link and shows a "check
your email" confirmation naming the address it went to; clicking that link
lands back here. Google calls `supabase.auth.signInWithOAuth`, sending the
browser to Google and then to Supabase's fixed callback URL above — if
Google isn't configured, this call returns a provider error inline instead
of redirecting, which the button catches and turns into a plain-language
message. Both paths converge on Supabase's fixed callback URL, which
redirects to this app's own `app/auth/callback/route.ts`. That route
exchanges the auth code for a session (setting cookies) and redirects to
`?next=` (default `/machines`). Every subsequent call to the cloud API
(`lib/cloud-api.ts`) reads the JWT off that session and attaches it as
`Authorization: Bearer <jwt>`.

## Pages

| Route | What it does |
|---|---|
| `/sign-in` | Email magic-link sign-in (working today), with Google as a secondary option that degrades gracefully if unconfigured. |
| `/activate` | Enter a device code (from `flashnode login`) and approve a machine. Phone-first. |
| `/machines` | The caller's machines: online state, last seen, revoke. |
| `/submit` | Paste a GitHub repo + branch, run it through the API's preflight, submit. Renders every preflight finding (level/code/message, quoted verbatim). An error finding blocks the submission entirely; a warning never blocks server-side, so it's surfaced on the post-submit screen instead of gating a second click. |
| `/jobs` | The caller's jobs: name, state, created. Polls every 3s, stops once every listed job is in a terminal state. |
| `/jobs/[jobId]` | One job's state, error, artifacts (with an authenticated download button), and spec summary. Cancel with a confirm step. Polls every 2.5s, stops on a terminal state. |

**Known gap:** the job detail page does not show per-round federated-averaging
progress (round, participants, mean loss) or which machines contributed to a
job. That data is produced by `flashruntime`'s `fedavg_driver.RoundResult`,
but nothing in `apps/api` invokes that driver or persists its history yet —
neither `flashruntime.protocol.v1alpha1.JobRecord` nor this API's own `jobs`
table carries it. Once the API surfaces it, it belongs on this page; showing
a fabricated or permanently-empty progress bar in the meantime would be
worse than the gap.

## Stale code (removed)

`lib/api.ts`, `lib/poc-api.ts`, and the prototype pages that only they
served (`/launch`, `/visualize`, `/dashboard`, `/integration`, `/nodes`) have
been deleted — they predated accounts and targeted either a retired
coordinator or the legacy (pre-accounts) mode of `apps/api` that this app no
longer runs against. `lib/cloud-api.ts` is the single typed client; every
page imports its types from there rather than defining its own.
