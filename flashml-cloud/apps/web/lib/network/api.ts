// Typed client for the network / providers surface of the FlashML cloud API.
//
// WHY THIS FILE IS NOT IN `lib/cloud-api.ts`. It would belong there — that
// module's own header says it is "the single typed client" and that pages
// import their shapes from it rather than defining their own. This file is a
// second module only because `lib/cloud-api.ts` is being edited by another
// session tonight and a two-session merge over a 2255-line file is how a
// client and a contract drift apart. The types here are the network
// surface's and nothing else's; folding them back in is a move, not a
// rewrite. **Flagged for the owner.**
//
// WHAT IS SHARED RATHER THAN COPIED. The error classes and the base-URL
// resolution are imported from `lib/cloud-api.ts`, so a 401 here is the same
// `NotAuthenticated` every page already redirects on, and a 404 the same
// `NotFound`. Only `request`/`send` are re-expressed, because they are
// module-private there and exporting them is an edit to a file this session
// does not own. They are deliberately the same six behaviours, in the same
// order — attach the session JWT, JSON content type on a body, name the URL
// in a transport failure, 401 → NotAuthenticated, 404 → NotFound, anything
// else → ApiError — because a second client that fails differently is worse
// than a second client.
//
// This module never reads any credential but the signed-in user's own
// Supabase JWT.

import {
  ApiError,
  NotAuthenticated,
  NotFound,
  cloudApiBase,
} from "@/lib/cloud-api";
import type { MachineBadge } from "@/lib/machine-badge";
import { createBrowserSupabaseClient } from "@/lib/supabase";

// ---------------------------------------------------------------------------
// types — mirror `GET /v1alpha1/network/providers` exactly
// ---------------------------------------------------------------------------

/** One resource's network-wide capacity: what exists, and how much of it is
 * committed to work right now. Both are counts the API measured; neither is
 * ever inferred from the other. */
export interface CapacityTotal {
  total: number;
  used: number;
}

/** The header strip's numbers.
 *
 * `storage_bytes` is OPTIONAL and absent today: nothing in the fleet reports
 * per-machine disk, so the API does not send it. The field is typed rather
 * than omitted so the fourth donut appears the day the API starts sending
 * it and not a release later — and so the three-donut layout is a rendered
 * consequence of the data, not a hard-coded count. See
 * `components/network/CapacityDonuts.tsx`. */
export interface NetworkTotals {
  cpu_cores: CapacityTotal;
  gpus: CapacityTotal;
  memory_bytes: CapacityTotal;
  storage_bytes?: CapacityTotal;
  providers_online: number;
  providers_total: number;
}

/** Where a provider says it is.
 *
 * Every field is nullable and they fail independently: a machine can know
 * its country and not its city, or carry coordinates with no place name.
 * `lat`/`lon` null is the ONLY thing that keeps a provider off the map — it
 * never keeps one out of the table. `source` names who said so (the host's
 * own answer, a lookup, a default); the console shows it rather than
 * presenting a guessed location as a stated one. */
export interface ProviderLocation {
  country: string | null;
  region: string | null;
  city: string | null;
  lat: number | null;
  lon: number | null;
  source: string | null;
}

/** Seven days of availability, as the two counts behind the percentage.
 *
 * `hours_observed_7d === 0` is what "no data yet" MEANS: nobody watched this
 * machine, so there is no denominator and therefore no percentage. That is a
 * different fact from "watched for 168 hours and up for none of them", which
 * is a real 0%. A percentage alone cannot tell those apart, which is why the
 * counts travel with it. */
export interface ProviderUptime {
  hours_up_7d: number;
  hours_observed_7d: number;
}

/** What this provider has been asked to do and how it went.
 *
 * `resolved` is the denominator `success_rate` is over — attempts that
 * reached an outcome. Below five of them the rate is noise, and
 * `lib/network/providers.ts` refuses to render it as a percentage. */
export interface ProviderLeases {
  active: number;
  total: number;
  resolved: number;
  success_rate: number | null;
}

export interface ProviderGpu {
  name: string;
  /** Null when the driver reported a model and no memory. Rendered as the
   * model chip with no VRAM suffix — never as `0 GB`, which would be a
   * statement about the card rather than about what we know of it. */
  memory_total_mb: number | null;
}

export interface ProviderSpecs {
  cpu_cores: number | null;
  memory_bytes: number | null;
  gpus: ProviderGpu[];
  os: string | null;
  architecture: string | null;
}

/** The trust tier, as the API states it.
 *
 * NOT A NEW UNION. It is `lib/machine-badge.ts`'s `MachineBadge`, re-exported
 * under the name this response uses — the API sends exactly
 * `"sandboxed" | "trusted" | "modules-only"`, the three words that module
 * already derives from a machine's capability booleans and already owns the
 * labels and colours for. Declaring a parallel union here would let the two
 * drift by one string and nothing would catch it; aliasing means a change to
 * either is a compile error in both. */
export type ProviderBadge = MachineBadge;

export interface Provider {
  id: string;
  /** The public, truncated name. Never the machine's own hostname unless the
   * caller owns it — see `owner.name` on the detail response.
   *
   * EXCEPT when `official` is true: an official machine's `label` is already
   * its real name — e.g. `"alibaba-sgp-1"` — for every viewer, not the
   * anonymized `prov…XXXXXX` handle a non-owner sees on everyone else's box.
   * The API decides which string to send; this console never anonymizes or
   * un-anonymizes a label itself. */
  label: string;
  /** Whether Zolli Labs itself operates this machine, as opposed to a
   * volunteer's box or a rented anchor a third party runs. OPTIONAL and
   * absent on any response the console reads before the API starts sending
   * it — same shape of gap as `NetworkTotals.storage_bytes` above, and
   * absence reads the same as `false` everywhere this is used (see
   * `lib/network/providers.ts`'s `officialChip`). Orthogonal to `badge`:
   * `badge` states what a machine PROVES about itself, this states who OWNS
   * it, and an official machine can carry any trust tier. */
  official?: boolean;
  /** Whether the signed-in account owns this machine. Gates the owner-only
   * panels and nothing else: everything they reveal is re-checked server
   * side, so a client that lied to itself here would gain a 403. */
  own: boolean;
  online: boolean;
  last_seen_at: string | null;
  location: ProviderLocation;
  /** A PERCENTAGE, 0–100, already multiplied server side. Rendered with a
   * `%` and never scaled again here. */
  uptime_7d_pct: number | null;
  uptime: ProviderUptime;
  leases: ProviderLeases;
  specs: ProviderSpecs;
  badge: ProviderBadge;
}

export interface NetworkProviders {
  totals: NetworkTotals;
  providers: Provider[];
}

/** One day of lease activity. `date` is a calendar day, not a timestamp. */
export interface LeaseDay {
  date: string;
  resolved: number;
  accepted: number;
}

/** One hour of the last 24. `up` is a fact about that hour, and an hour the
 * API does not send is an hour nobody observed — the strip draws the hours
 * it was given and never pads to 24 with `up: false`. */
export interface UptimeHour {
  hour_ts: string;
  up: boolean;
}

export interface ProviderSeries {
  leases_daily_30d: LeaseDay[];
  uptime_24h: UptimeHour[];
}

export interface ProviderHistory {
  attempts: {
    accepted: number;
    failed: number;
    expired: number;
    unresolved: number;
  };
  accepted_duration_s_total: number;
}

/** Present only when `own` is true. The API omits it entirely otherwise —
 * this is not a nulled-out object, it is an absent key, and the console
 * must not render an owner panel that says "unknown" to a visitor. */
export interface ProviderOwnerFacts {
  name: string | null;
  node_id: string;
  /** MILLIcredits, like every other credit integer in this codebase.
   * Formatted with `formatZc` from `lib/market-credits.ts` and never divided
   * by 1000 locally — one place does that arithmetic. */
  credits_earned: number | null;
}

export type ProviderDetail = Provider & {
  series: ProviderSeries;
  history: ProviderHistory;
  owner?: ProviderOwnerFacts;
};

/** `PATCH /v1alpha1/machines/{id}/location`. `country` is required; the rest
 * are omitted from the body rather than sent as null, because clearing a
 * field and not touching it are different intentions. */
export interface LocationPatch {
  country: string;
  region?: string;
  city?: string;
  lat?: number;
  lon?: number;
}

// ---------------------------------------------------------------------------
// request plumbing — see the header for why this is not imported
// ---------------------------------------------------------------------------

async function authHeader(): Promise<Record<string, string>> {
  const supabase = createBrowserSupabaseClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session) {
    throw new NotAuthenticated();
  }
  return { Authorization: `Bearer ${session.access_token}` };
}

async function detailOf(res: Response): Promise<string | null> {
  const text = await res.text().catch(() => "");
  if (!text) return null;
  try {
    const body = JSON.parse(text);
    if (body && typeof body === "object" && typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // Not JSON — a proxy in front of the API can answer plain text.
  }
  return text;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const auth = await authHeader();
  const headers: Record<string, string> = {
    ...auth,
    ...(init.body ? { "Content-Type": "application/json" } : {}),
    ...(init.headers as Record<string, string> | undefined),
  };
  const url = `${cloudApiBase()}${path}`;

  // `fetch` rejects with a bare "Failed to fetch" for every transport-level
  // failure and names none of them. Say which URL was called — it is the one
  // fact that identifies the cause.
  let res: Response;
  try {
    res = await fetch(url, { ...init, headers });
  } catch (cause) {
    const reason = cause instanceof Error ? cause.message : String(cause);
    throw new ApiError(0, `${reason} — could not reach ${url}`);
  }

  if (res.status === 401) throw new NotAuthenticated();
  if (res.status === 404) throw new NotFound((await detailOf(res)) ?? "not found");
  if (!res.ok) {
    throw new ApiError(
      res.status,
      (await detailOf(res)) ?? `${res.status} ${res.statusText}`
    );
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

// ---------------------------------------------------------------------------
// calls
// ---------------------------------------------------------------------------

/** Every provider on the network plus the network's own totals, in one read.
 * One route, because the header's numbers and the table's rows are the same
 * measurement and must not be able to disagree with each other. */
export function getNetworkProviders(): Promise<NetworkProviders> {
  return request<NetworkProviders>("/v1alpha1/network/providers");
}

/** One provider, with its series and its history. A 404 here means "no such
 * provider" — the page turns that into a sentence and a way back, not an
 * error wall. */
export function getNetworkProvider(id: string): Promise<ProviderDetail> {
  return request<ProviderDetail>(
    `/v1alpha1/network/providers/${encodeURIComponent(id)}`
  );
}

/** Set a machine's location. Owner-only, enforced by the API.
 *
 * The body is built key by key rather than spread from a form object, so an
 * empty optional field is left OUT of the request instead of being sent as
 * `""` — which the API would store as a real, blank city. */
export function setMachineLocation(
  machineId: string,
  patch: LocationPatch
): Promise<Provider> {
  const body: LocationPatch = { country: patch.country };
  if (patch.region) body.region = patch.region;
  if (patch.city) body.city = patch.city;
  if (patch.lat !== undefined) body.lat = patch.lat;
  if (patch.lon !== undefined) body.lon = patch.lon;
  return request<Provider>(
    `/v1alpha1/machines/${encodeURIComponent(machineId)}/location`,
    { method: "PATCH", body: JSON.stringify(body) }
  );
}
