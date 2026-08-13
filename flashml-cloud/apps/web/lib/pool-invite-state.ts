import type { PoolInviteState } from "./cloud-api";

/**
 * The expiry half of the invite-status line — "expires in 4h" / "expired" /
 * "expiry unknown" — with none of the uses-remaining prefix
 * `formatInviteState` used to weld it to.
 *
 * Split out (density audit §3, gap 6) so `uses_remaining` can be its own
 * Stat rather than buried inside one prose sentence — `InviteManager.tsx`
 * renders that as a `StatTile` and this string as the small line beside it,
 * instead of a single paragraph a viewer had to read in full to find the
 * one number in it.
 *
 * `relativeTime` (`lib/machine-status.ts`) cannot supply this — it only
 * ever describes the past (`deltaMs = now - then`, and a negative delta
 * collapses straight to `"just now"`). An invite's `expires_at` is a
 * future timestamp for as long as the standing link is still good, so this
 * gets its own small duration formatter rather than misusing that one.
 *
 * `now` defaults to `Date.now()` and exists so tests do not depend on the
 * wall clock.
 */
export function formatInviteExpiry(
  state: PoolInviteState,
  now: number = Date.now()
): string {
  const then = new Date(state.expires_at).getTime();
  if (Number.isNaN(then)) {
    return "expiry unknown";
  }

  const deltaMs = then - now;
  if (deltaMs <= 0) {
    return "expired";
  }

  const s = Math.floor(deltaMs / 1000);
  const duration =
    s < 60
      ? `${s}s`
      : s < 3600
        ? `${Math.floor(s / 60)}m`
        : s < 86400
          ? `${Math.floor(s / 3600)}h`
          : `${Math.floor(s / 86400)}d`;

  return `expires in ${duration}`;
}

/**
 * Renders the pool page's invite-status line — "N uses left · expires
 * ...". Pulled out of the page because it is not a ternary: pluralising
 * "use(s)" is its own piece of logic, and `formatInviteExpiry` above is the
 * other. Kept as the combined sentence for call sites that still want one
 * string; `InviteManager.tsx` no longer does — see `formatInviteExpiry`'s
 * own docblock.
 */
export function formatInviteState(
  state: PoolInviteState,
  now: number = Date.now()
): string {
  const uses = `${state.uses_remaining} use${state.uses_remaining === 1 ? "" : "s"} left`;
  return `${uses} · ${formatInviteExpiry(state, now)}`;
}
