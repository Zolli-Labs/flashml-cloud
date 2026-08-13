import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { statTileSuffix, statTileValue } from "@/lib/stat-tile";

/**
 * The one shape every console number tile already drew by hand, seven
 * times over (`docs/superpowers/specs/2026-08-13-console-density-audit.md`
 * §4): a large mono figure over a caps-label caption, against the same two
 * `globals.css` classes — `.metric-value`/`.metric-lg` and `.label-caps`.
 *
 * Markup only, per console-ui-plan §5 ("Decision layer in `lib/`, markup in
 * `components/`") — the null-handling and `value/total` suffix decisions
 * live in `lib/stat-tile.ts`, where `lib/stat-tile.test.ts` can reach them.
 * This file makes no decision beyond which of the three drawn SHAPES to use.
 *
 * The three variants are the three shapes the audit found, not a new design:
 * - `card` — its own bordered `.panel` box. `metrics`'s `CountTile`,
 *   `w/[poolId]/overview`'s `Stat`, `RoundProgress`'s `Stat`.
 * - `bare` — a naked `dt`/`dd` pair for placing inside a caller-owned `<dl>`
 *   grid. `TradeoffCard`'s and `RoutingCard`'s identical `Stat`s, and
 *   `JobRecovery`'s (via `className` for its `bg-surface` cell background).
 * - `header` — a right-aligned, borderless figure for a page header strip.
 *   `market/listings`'s `HeaderStat`.
 */

export type StatTileSize = "lg" | "md" | "sm";
export type StatTileTone = "plain" | "good";
export type StatTileVariant = "card" | "bare" | "header";

const SIZE_CLASSES: Record<StatTileSize, string> = {
  lg: "text-2xl",
  md: "text-xl",
  sm: "text-lg",
};

export interface StatTileProps {
  label: string;
  /** `null`/`undefined` renders "not observed", never a fabricated `0` — see
   * `lib/stat-tile.ts`. A real `0` is a true count and prints as `0`. */
  value: number | string | null | undefined;
  /** Denominator for a "value/total" display (e.g. machines online of
   * machines total). Omitted, or equal to `value`, renders the value alone —
   * see `statTileSuffix`. Only meaningful when `value` is a number. */
  total?: number | null;
  /** A short line under the value — e.g. a duration total, a basis note. */
  hint?: ReactNode;
  /** `good` affirms a positive figure (e.g. an improving percentage). Never
   * used to recolor a merely-present number — the default is `plain`. */
  tone?: StatTileTone;
  size?: StatTileSize;
  variant?: StatTileVariant;
  className?: string;
}

export function StatTile({
  label,
  value,
  total,
  hint,
  tone = "plain",
  size = "md",
  variant = "card",
  className,
}: StatTileProps) {
  const display = statTileValue(value);
  const suffix =
    typeof value === "number"
      ? statTileSuffix(value, total ?? undefined)
      : null;
  const valueClass = cn(
    "metric-value",
    SIZE_CLASSES[size],
    tone === "good" && "text-[var(--node-green)]"
  );

  if (variant === "bare") {
    return (
      <div className={className}>
        <dt className="label-caps">{label}</dt>
        <dd className={cn(valueClass, "mt-0.5")}>{display}</dd>
      </div>
    );
  }

  if (variant === "header") {
    return (
      <div className={cn("text-right", className)}>
        <div className={valueClass}>{display}</div>
        <div className="label-caps mt-0.5">{label}</div>
      </div>
    );
  }

  return (
    <div className={cn("panel px-4 py-3.5", className)}>
      <div className={valueClass}>
        {display}
        {suffix && (
          <span className="text-base text-muted-foreground">{suffix}</span>
        )}
      </div>
      <div className="label-caps mt-1">{label}</div>
      {hint && (
        <div className="mt-1 font-mono text-[10px] text-muted-foreground">
          {hint}
        </div>
      )}
    </div>
  );
}
