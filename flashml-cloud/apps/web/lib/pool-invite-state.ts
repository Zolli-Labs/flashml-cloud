import type { PoolInviteState } from "./cloud-api";

/**
 * Renders the pool page's invite-status line — "N uses left · expires
 * ...". Pulled out of the page because it is not a ternary: pluralising
 * "use(s)", treating an already-lapsed invite as "expired" rather than a
 * negative duration, and describing a FORWARD-looking span are three
 * independent pieces of logic.
 *
 * `relativeTime` (`lib/machine-status.ts`) cannot supply that last piece —
 * it only ever describes the past (`deltaMs = now - then`, and a negative
 * delta collapses straight to `"just now"`). An invite's `expires_at` is a
 * future timestamp for as long as the standing link is still good, so this
 * gets its own small duration formatter rather than misusing that one.
 *
 * `now` defaults to `Date.now()` and exists so tests do not depend on the
 * wall clock.
 */
export function formatInviteState(
  state: PoolInviteState,
  now: number = Date.now()
): string {
  const uses = `${state.uses_remaining} use${state.uses_remaining === 1 ? "" : "s"} left`;

  const then = new Date(state.expires_at).getTime();
  if (Number.isNaN(then)) {
    return `${uses} · expiry unknown`;
  }

  const deltaMs = then - now;
  if (deltaMs <= 0) {
    return `${uses} · expired`;
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

  return `${uses} · expires in ${duration}`;
}
