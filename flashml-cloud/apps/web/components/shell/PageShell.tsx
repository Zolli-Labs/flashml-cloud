import { CONSOLE_WIDTH_CLASS, type ConsoleWidth } from "@/lib/console/page-width";
import { cn } from "@/lib/utils";

/**
 * The content column of a console page.
 *
 * Replaced `mx-auto max-w-6xl px-4 py-8 sm:px-6`, hand-written in thirteen
 * page files, plus four pages that differ from it with nothing recorded
 * about why. `py-6`, not the original `py-8`: part of the instrument-panel
 * pass tightening the console's vertical rhythm to match its now-denser
 * chrome (`h-12` bars, `.page-title`'s 20px). Horizontal padding and the
 * width vocabulary are unchanged.
 *
 * NO DEFAULT WIDTH, deliberately. `lib/console/page-width.ts` has one —
 * `DEFAULT_CONSOLE_WIDTH`, for answering about a route nobody has filed yet
 * — but a default *here* would let a page skip the decision silently, and
 * eleven of the nineteen console pages are not the default. One required
 * word per page is the entire cost of making every column width reviewable.
 *
 * NO BOXING. Not a border, not a fill, not a radius. `app/globals.css` is
 * explicit that `.panel` is for a genuinely separate object and that
 * containment is one level deep at most; a page container that draws a card
 * makes every panel inside it a nested one. If a page wants a card, it wraps
 * its own content in `.panel`.
 *
 * Server-safe on purpose: no hooks, no client boundary, so a static page
 * (`/docs`, `/how-it-works`) can use it without becoming a client component.
 */
export function PageShell({
  width,
  className,
  children,
}: {
  /** Which column this page is. Required — see above. */
  width: ConsoleWidth;
  /** Layout-only overrides for the container itself. Not a colour and not a
   * width: a `max-w-*` here would silently outrank the named one and put the
   * console straight back where it started. */
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      data-console-width={width}
      className={cn(
        "mx-auto px-4 py-6 sm:px-6",
        CONSOLE_WIDTH_CLASS[width],
        className
      )}
    >
      {children}
    </div>
  );
}
