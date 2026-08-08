/** Turning `GET /v1alpha1/me/storage` into something the account page can
 * render. Same reasoning as `lib/job-result.ts` and `lib/platform-metrics.ts`
 * — this is the part worth testing, so it lives where a `.test.ts` can
 * reach it and the component around it stays markup.
 *
 * The one rule: `limit_bytes: null` means unlimited, and is NOT a limit of
 * 0. `percent_used` is null in exactly the same case. Coercing either to a
 * number draws a progress bar for an account that has no limit at all —
 * "no ceiling" and "a ceiling barely touched" look identical the moment
 * both become 0%, and they are opposite facts. `unlimited` below is the
 * field the component branches on for that reason: it must decide whether
 * to draw a bar at all before it ever looks at `percent`.
 *
 * `severity` exists because today the FIRST time an account learns it has a
 * storage ceiling at all is a refused submission at 100% — after someone has
 * already written a `flashml.yaml` and pushed it. `StorageDisplay` is a
 * discriminated union on `unlimited` specifically so an unlimited account
 * being "approaching" its (nonexistent) ceiling is not just unlikely, it is
 * a type error: `UnlimitedStorageDisplay.severity` is the literal type
 * `"ok"`, full stop, and there is no `percent` field on that branch for a
 * threshold check to ever read in the first place.
 */

import type { AccountStorage } from "./cloud-api";
import { formatBytes } from "./utils";

export type StorageSeverity = "ok" | "approaching" | "full";

/** The point at which usage stops being "fine" and starts being something
 * to act on. Chosen with headroom in mind, not as a tripwire at the edge: a
 * single job's checkpoints or artifacts can be gigabytes, so a warning that
 * only fires in the last few percent leaves no real time to clear space (or
 * shrink the job) before the NEXT submission is the one that gets refused.
 * 80% is also late enough that it still means something — an account that
 * fluctuates through the 50-70% range in normal use would make an earlier
 * threshold pure noise. */
export const APPROACHING_THRESHOLD_PERCENT = 80;

/** Says what to do, not just that a number is high — a warning that only
 * restates the percentage back at the person who can already see it on
 * screen has told them nothing they didn't already know. */
const APPROACHING_MESSAGE =
  "Storage is filling up. Clear a finished job's artifacts, or delete checkpoints you no longer need, before your next submission is refused.";

/** Distinct from the approaching message on purpose: "about to hit the
 * wall" and "already refusing submissions" are different situations and a
 * shared sentence would blur the one fact — whether submitting right now
 * will actually fail — that matters most at this severity. */
const FULL_MESSAGE =
  "Storage is full. New submissions are being refused until you free space — clear a finished job's artifacts to make room.";

interface UnlimitedStorageDisplay {
  usedLabel: string;
  unlimited: true;
  limitLabel: null;
  percent: null;
  /** Always "ok" — see the module docstring for why this is enforced by
   * the type, not merely by the logic below. */
  severity: "ok";
  message: null;
}

interface LimitedStorageDisplay {
  usedLabel: string;
  unlimited: false;
  limitLabel: string;
  /** The API's own `percent_used`, passed through unchanged: it is already
   * clamped to 100 server-side, so this must not re-derive or re-clamp it
   * from used/limit. */
  percent: number;
  severity: StorageSeverity;
  /** An actionable sentence when `severity` is "approaching" or "full";
   * null exactly when `severity` is "ok" — there is nothing to tell someone
   * to do about a number that isn't a problem. */
  message: string | null;
}

export type StorageDisplay = UnlimitedStorageDisplay | LimitedStorageDisplay;

function severityFor(percent: number): StorageSeverity {
  if (percent >= 100) return "full";
  if (percent >= APPROACHING_THRESHOLD_PERCENT) return "approaching";
  return "ok";
}

function messageFor(severity: StorageSeverity): string | null {
  if (severity === "full") return FULL_MESSAGE;
  if (severity === "approaching") return APPROACHING_MESSAGE;
  return null;
}

export function summariseStorage(storage: AccountStorage): StorageDisplay {
  const usedLabel = formatBytes(storage.used_bytes);

  if (storage.limit_bytes === null) {
    return {
      usedLabel,
      unlimited: true,
      limitLabel: null,
      percent: null,
      severity: "ok",
      message: null,
    };
  }

  // The API's contract pairs `limit_bytes` and `percent_used` — both null
  // together (unlimited) or both a number together. `?? 0` here is a
  // defensive floor for a payload that broke that contract, not a value
  // this branch expects to use: treating an unexplained null as "0% used"
  // is the same "say nothing is wrong rather than crash the page" doctrine
  // `ReliabilityCard`'s neighbours follow elsewhere in this app, not a claim
  // that 0 is what actually happened.
  const percent = storage.percent_used ?? 0;
  const severity = severityFor(percent);

  return {
    usedLabel,
    unlimited: false,
    limitLabel: formatBytes(storage.limit_bytes),
    percent,
    severity,
    message: messageFor(severity),
  };
}
