// Supabase clients for **auth only**.
//
// The browser must never touch Postgres: RLS denies `anon` and
// `authenticated` outright (verified against the live database — both
// roles see zero rows in tables `service_role` can read). These clients
// exist to sign a user in with Google and to read/refresh the resulting
// session so its JWT can be attached to calls to the cloud API
// (`lib/cloud-api.ts`). Do not add a `.from(...)` database query anywhere
// that uses these clients — if a screen seems to need one, the cloud API is
// missing an endpoint, not missing a database credential.
//
// Only `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY` are
// read here. The service-role key must never be imported into anything
// under `app/` or `lib/` that a client bundle can reach — see
// `apps/web/README.md` for the full rule.
import { createBrowserClient, createServerClient } from "@supabase/ssr";
import type { CookieOptions } from "@supabase/ssr";

function supabaseUrl(): string {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  if (!url) {
    throw new Error("NEXT_PUBLIC_SUPABASE_URL is not set");
  }
  return url;
}

function supabaseAnonKey(): string {
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!key) {
    throw new Error("NEXT_PUBLIC_SUPABASE_ANON_KEY is not set");
  }
  return key;
}

/** Client component / browser-side Supabase client. Safe to call from any
 * `"use client"` module — it only ever sees the anon key. */
export function createBrowserSupabaseClient() {
  return createBrowserClient(supabaseUrl(), supabaseAnonKey());
}

/** Cookie adapter shape shared by the server client and middleware — the
 * two differ only in how they read/write cookies (`next/headers` vs. a
 * `NextRequest`/`NextResponse` pair), so the adapter is the only thing
 * callers need to supply. */
export interface SupabaseCookieAdapter {
  getAll(): { name: string; value: string }[];
  setAll(
    cookies: { name: string; value: string; options: CookieOptions }[]
  ): void;
}

/** Server-side Supabase client (Server Components, Route Handlers, Server
 * Actions). Pass a cookie adapter bound to the current request. */
export function createServerSupabaseClient(cookies: SupabaseCookieAdapter) {
  return createServerClient(supabaseUrl(), supabaseAnonKey(), { cookies });
}
