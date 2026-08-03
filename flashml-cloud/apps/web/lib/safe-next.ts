/**
 * Sanitizes a `next` query param before it ever reaches a real browser
 * navigation — `SignInCard`'s `window.location.assign(next)` after a
 * successful sign-in, `app/auth/callback/route.ts`'s post-OAuth redirect,
 * and the `next` this app's own `middleware.ts` writes onto its
 * `/sign-in` redirect (which the two routes above then read straight
 * back). One guard, used everywhere `next` is read, so a fix here fixes
 * every caller at once.
 *
 * `next` is attacker-controlled: anyone can mint a sign-in link with
 * `?next=https://evil.com`, and — the case a plain `startsWith("/")` check
 * misses — the request's own PATHNAME can be `//evil.com/foo` (a URL like
 * `https://this-site.com//evil.com/foo` parses with exactly that
 * pathname), which `middleware.ts` would otherwise carry verbatim into
 * `next`. Neither has a scheme, so neither looks "absolute" to a careless
 * check, but a leading `//` makes a URL *protocol-relative*: a browser
 * resolves it against the current page's scheme, so
 * `window.location.assign("//evil.com/foo")` leaves this site exactly as
 * surely as `https://evil.com` would.
 *
 * A single leading `/` is not enough either: `/\evil.com/steal` also
 * starts with exactly one `/`, but a browser's URL parser treats a
 * backslash the same as a forward slash when RESOLVING a relative
 * reference, so `window.location.assign("/\\evil.com/steal")` *also*
 * leaves the site — it resolves identically to `//evil.com/steal`. This is
 * purely a client-side hazard: a real browser navigation normalizes `\` to
 * `/` in the address bar before the request ever reaches `middleware.ts`,
 * so the backslash form of the attack only ever shows up in a query-param
 * VALUE (`SignInCard`, the callback route), never in a request pathname —
 * but this guard is the one both channels share, so it closes both without
 * either caller needing to know which case applies to it.
 *
 * The fix generalizes to: the character immediately after the leading `/`
 * must be neither `/` nor `\`. That single rule also correctly rejects a
 * bare `"/"` (no second character to satisfy it at all) and a value with no
 * leading `/` at all (fails the same regex from the front).
 *
 * Returns `value` unchanged when it passes that check, and `fallback`
 * (`"/machines"` by default — the fallback every current caller wants) for
 * anything else, including empty, null, or undefined.
 */
export function safeNext(
  value: string | null | undefined,
  fallback: string = "/machines"
): string {
  if (!value) return fallback;
  if (!/^\/[^/\\]/.test(value)) return fallback;
  return value;
}
