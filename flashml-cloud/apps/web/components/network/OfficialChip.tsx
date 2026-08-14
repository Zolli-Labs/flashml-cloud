"use client";

import { SealCheck } from "@phosphor-icons/react";

import type { Provider } from "@/lib/network/api";
import { OFFICIAL_CHIP_CLASS, officialChip } from "@/lib/network/providers";

/**
 * "Official" — Zolli Labs itself operates this machine, rather than a
 * volunteer's box or a third party's rented anchor.
 *
 * A SECOND CHIP, NOT A THIRD COLOUR ON `TrustBadge`. `badge` states what a
 * machine PROVES about itself (sandboxed / trusted / modules-only); this
 * states who OWNS it. An official machine can carry any trust tier, so the
 * two facts render side by side rather than one overloading the other's
 * swatch — the seal icon and the evergreen tint are deliberately nothing
 * `TrustBadge` or the table's orange "yours" tag use, so the two chips read
 * as answers to different questions even next to each other.
 *
 * Renders NOTHING when the provider is not official — including a provider
 * from an API response that predates this field. See `officialChip` in
 * `lib/network/providers.ts`.
 */
export function OfficialChip({
  provider,
}: {
  provider: Pick<Provider, "official">;
}) {
  const chip = officialChip(provider);
  if (!chip) return null;
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded border px-1.5 py-px text-[10.5px] leading-[1.6] whitespace-nowrap ${OFFICIAL_CHIP_CLASS}`}
    >
      <SealCheck aria-hidden weight="fill" className="h-[11px] w-[11px]" />
      {chip.label}
    </span>
  );
}
