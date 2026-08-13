/** Pure formatting decisions for the `StatTile` primitive
 * (`components/ui/stat-tile.tsx`) — kept here, not in the component, so the
 * honesty rules bind through a test rather than through code review.
 * `vitest.config.ts` collects only `**\/*.test.ts`; a `.tsx` component gets no
 * coverage at all, which is the same split `lib/job-routing.ts` and
 * `lib/job-tradeoff.ts` already document for their own `NOT_OBSERVED`.
 *
 * `StatTile` itself stays markup-only: it calls `statTileValue` and
 * `statTileSuffix` rather than deciding null-handling inline.
 */

/** `null` means *not observed* — the API has not measured this yet — and is
 * never rendered as a fabricated `0`. Kept as its own constant (rather than
 * importing one of the three duplicate `NOT_OBSERVED`s already in
 * `lib/job-routing.ts` / `lib/job-tradeoff.ts` / `lib/sandbox-session.ts`)
 * because a StatTile call site should not have to pick one of three
 * job-specific modules to import a console-wide string from. */
export const NOT_OBSERVED = "not observed";

/**
 * What a StatTile's big figure should say.
 *
 * `null`/`undefined` → `NOT_OBSERVED`, never `0`. A real `0` is a true,
 * complete answer (a count of zero machines, zero pending requests) and is
 * printed as `0` — collapsing THAT into "not observed" would be the same
 * dishonesty in the other direction, hiding a real zero behind a measurement
 * caveat it does not have. A pre-formatted string (a caller that already ran
 * its own `countOrAbsent`-style helper) passes through unchanged.
 */
export function statTileValue(
  value: number | string | null | undefined
): string {
  if (value === null || value === undefined) return NOT_OBSERVED;
  return String(value);
}

/**
 * The `value/total` suffix used by tiles that show a subset of a whole (e.g.
 * "3 online" of "5 machines" as "3/5"). `null` — for either side — means the
 * comparison cannot honestly be drawn, so no suffix renders rather than a
 * suffix built from half a number. Equal value and total also renders
 * nothing: "5/5" beside itself repeats the figure for no reason, the same
 * call the hand-rolled `overview` Stat already made.
 */
export function statTileSuffix(
  value: number | null | undefined,
  total: number | null | undefined
): string | null {
  if (value === null || value === undefined) return null;
  if (total === null || total === undefined) return null;
  if (total === value) return null;
  return `/${total}`;
}
