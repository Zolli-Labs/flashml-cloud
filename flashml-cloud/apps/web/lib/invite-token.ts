// Pure parsing for whatever a person pastes into the invite gate: the full
// link the console renders (`${location.origin}/pools/join?token=...`), a
// link with tracking params tacked on, a bare `token=...` fragment copied
// from the middle of one, or the raw `fmi_...` token itself. No DOM/window
// access here — this has to stay a plain function so it can be unit tested
// in the vitest "node" environment and reused from both InviteGate and the
// `/pools/join` page.

/** Extracts an invite token out of arbitrary pasted input, or returns
 * `null` when there is nothing to extract.
 *
 * - A full, absolute URL: reads its `token` query parameter. A URL that
 *   parses but carries no `token` param returns `null` — the whole URL is
 *   never itself a usable token, so there is nothing sensible to fall back
 *   to.
 * - Not a URL, but containing a `...?token=...` or `...&token=...`
 *   fragment (e.g. a link pasted without its `https://` scheme, or copied
 *   from partway through): reads that fragment.
 * - Otherwise: the trimmed input itself, treated as a bare token.
 * - Empty or whitespace-only input: `null`.
 */
export function tokenFromInput(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;

  try {
    const url = new URL(trimmed);
    const fromQuery = url.searchParams.get("token");
    return fromQuery && fromQuery.trim() ? fromQuery.trim() : null;
  } catch {
    // Not an absolute URL — fall through to the fragment/bare-token cases
    // below rather than treating a parse failure as "no token".
  }

  const fragment = trimmed.match(/(?:^|[?&])token=([^&\s]+)/);
  if (fragment) {
    try {
      return decodeURIComponent(fragment[1]);
    } catch {
      // Malformed percent-encoding — hand back the raw fragment rather
      // than throwing on a paste we can otherwise clearly read.
      return fragment[1];
    }
  }

  return trimmed;
}
