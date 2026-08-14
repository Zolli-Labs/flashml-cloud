"use client";

import type { ProviderBadge } from "@/lib/network/api";
import { badgeClass, badgeLabel } from "@/lib/network/providers";

/**
 * A provider's trust tier, in the console's one badge.
 *
 * The label and the colour both come from `lib/machine-badge.ts` through
 * `lib/network/providers.ts`, so this badge and the one on `/machines` are
 * the same badge — amber for "Trusted" because that tier means work runs
 * OUTSIDE a sandbox, neutral for the other two because a sandboxed machine is
 * the safe default and "Modules only" is a lesser capability rather than a
 * risk. Inventing a second palette here is how two pages start disagreeing
 * about what a word means.
 */
export function TrustBadge({ badge }: { badge: ProviderBadge }) {
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-px text-[10.5px] leading-[1.6] whitespace-nowrap ${badgeClass(badge)}`}
    >
      {badgeLabel(badge)}
    </span>
  );
}
