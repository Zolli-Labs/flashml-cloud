/**
 * Sanitizes the `next` query param before it ever reaches a real browser
 * navigation — `SignInCard`'s `window.location.assign(next)` after a
 * successful sign-in, and the `next` this app's own `middleware.ts` writes
 * onto its `/sign-in` redirect (which `SignInCard` then reads straight
 * back).
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
 * Returns `value` unchanged when it is a legitimate same-origin path
 * (starts with exactly one `/`), and `"/machines"` — the fallback
 * `SignInCard` already used for a missing `next` — for anything else,
 * including empty, null, or undefined.
 */
export function safeNext(value: string | null | undefined): string {
  if (!value) return "/machines";
  if (!value.startsWith("/")) return "/machines";
  if (value.startsWith("//")) return "/machines";
  return value;
}
