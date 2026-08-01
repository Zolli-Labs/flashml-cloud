# FlashML Web

The browser surface of FlashML: sign in with Google, approve a machine from
your phone, submit a repo, and watch it train.

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

## Manual step: enable Google sign-in (do this once per Supabase project)

This cannot be inferred from the repo or done in code — it is dashboard
configuration. Skip it and sign-in fails with a Supabase provider error that
reads exactly like a code bug, and it will cost the next person an hour.

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

Until all three are done, clicking "Continue with Google" either shows a
provider error inline on `/sign-in` or completes the Google consent screen
and then bounces back to `/sign-in?error=...` — both point back at this
section.

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
`/sign-in` is a single "Continue with Google" button
(`app/(auth)/sign-in`); it starts `supabase.auth.signInWithOAuth`, which
sends the browser to Google and then to Supabase's fixed callback URL
above, which in turn redirects to this app's own
`app/auth/callback/route.ts`. That route exchanges the auth code for a
session (setting cookies) and redirects to `?next=` (default `/machines`).
Every subsequent call to the cloud API (`lib/cloud-api.ts`) reads the JWT
off that session and attaches it as `Authorization: Bearer <jwt>`.

## Stale code, on its way out

`lib/api.ts` and `lib/poc-api.ts` predate accounts and the current cloud
API contract (`apps/api/flashml_cloud_api/app.py`); do not extend them or
use them as a contract reference. `lib/cloud-api.ts` is the one typed
client going forward, and the prototype pages that still import the old
clients (`/launch`, `/visualize`, `/dashboard`, `/integration`, `/nodes`)
are candidates for deletion once the pages that replace them land.
