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
 */

import type { AccountStorage } from "./cloud-api";
import { formatBytes } from "./utils";

export interface StorageDisplay {
  usedLabel: string;
  unlimited: boolean;
  /** Null exactly when `unlimited` is true — there is no ceiling to label. */
  limitLabel: string | null;
  /** Null exactly when `unlimited` is true. Otherwise the API's own
   * `percent_used`, passed through unchanged: it is already clamped to 100
   * server-side, so this must not re-derive or re-clamp it from
   * used/limit. */
  percent: number | null;
}

export function summariseStorage(storage: AccountStorage): StorageDisplay {
  const usedLabel = formatBytes(storage.used_bytes);

  if (storage.limit_bytes === null) {
    return { usedLabel, unlimited: true, limitLabel: null, percent: null };
  }

  return {
    usedLabel,
    unlimited: false,
    limitLabel: formatBytes(storage.limit_bytes),
    percent: storage.percent_used,
  };
}
