import Link from "next/link";

import { cn } from "@/lib/utils";

/**
 * The top of a console page: one heading, an optional line under it, and a
 * slot for whatever this page's primary action is.
 *
 * Fourteen pages hand-build this today and they do not agree. The `<h1>`
 * alone appears as `.title`, as `text-2xl font-semibold tracking-tight`, as
 * `text-xl font-semibold`, and as `font-mono text-2xl font-semibold` —
 * four sizes for the same rank of thing, which is the drift a reader
 * notices without being able to name.
 *
 * ONE HEADING TREATMENT. Every page heading is `.title` from
 * `app/globals.css`: Instrument Sans, semibold, -0.03em, clamped 1.75rem to
 * 2.75rem. Size, weight and tracking never vary by page. The single
 * enumerated exception is the TYPEFACE, and only for a machine-emitted name
 * — a job id, a run name — via `titleTone="identifier"`, which switches to
 * the mono face and truncates. That exception is not new taste: it is the
 * rule `.meta`, `.metric-value` and `.terminal-text` already encode, that
 * text a machine produced is set in the mono face so it is not mistaken for
 * something a person wrote.
 *
 * Server-safe: no hooks and no icon-library import, so a static page can use
 * it without a client boundary. The back arrow is inline SVG for that reason
 * and inherits `currentColor`, so it introduces no colour.
 */
export function PageHeader({
  title,
  titleTone = "text",
  description,
  meta,
  actions,
  back,
  className,
}: {
  /** The page's name. A string, not a node: a heading that accepts arbitrary
   * markup is how the four competing treatments got in. */
  title: string;
  /** `"text"` for a name a person wrote; `"identifier"` for one a machine
   * emitted, which is set in the mono face and truncated. */
  titleTone?: "text" | "identifier";
  /** A sentence about what this page is for. Capped at `max-w-prose`
   * regardless of how wide the column is. */
  description?: React.ReactNode;
  /** Machine-emitted supporting text — counts, ids, states — set in `.meta`.
   * Renders under the description when both are present.
   *
   * Whatever goes here must be a value the API returned. §1.1: never render
   * a number, name, size or timestamp that was not, and `null` means *not
   * observed*, never `0`. A count line is the most tempting place in the
   * console to fill a gap with a plausible zero. */
  meta?: React.ReactNode;
  /** The page's primary action, and at most one or two more. */
  actions?: React.ReactNode;
  /** A way back up, above the heading. Only for a page reached from a
   * parent listing — a back link on a top-level page points at nothing in
   * particular and reads as browser chrome. */
  back?: { href: string; label: string };
  className?: string;
}) {
  return (
    <header className={className}>
      {back ? (
        <Link
          href={back.href}
          className="mb-4 inline-flex items-center gap-1.5 text-sm text-brand-foreground hover:underline"
        >
          <BackArrow />
          {back.label}
        </Link>
      ) : null}

      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          {/* `font-mono!` / `truncate!`, and the `!` is load-bearing.
              `.title` is declared UNLAYERED in `app/globals.css`, and
              unlayered CSS beats every `@layer utilities` rule regardless of
              specificity — so plain `font-mono` lost to `.title`'s
              `font-family`, and plain `truncate` lost to its `text-wrap:
              balance`. The identifier tone rendered as ordinary sans and
              nobody would have seen it in a test: this was caught by looking
              at a render, which is the only way it could have been.

              The right home for this is a `.title-mono` beside `.title` in
              globals.css, which this session does not own. Flagged. */}
          <h1
            className={cn(
              "title",
              titleTone === "identifier" && "truncate! font-mono!"
            )}
          >
            {title}
          </h1>
          {description ? (
            <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
              {description}
            </p>
          ) : null}
          {meta ? <p className="meta mt-1.5">{meta}</p> : null}
        </div>

        {/* `shrink-0` so a long title wraps rather than crushing the action;
            `items-end` above so the buttons sit on the heading's baseline
            block whether or not there is a description under it. */}
        {actions ? (
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {actions}
          </div>
        ) : null}
      </div>
    </header>
  );
}

/** Inline rather than `@phosphor-icons/react` so this file stays usable from
 * a server component. `currentColor`, so it adds no colour of its own. */
function BackArrow() {
  return (
    <svg
      viewBox="0 0 16 16"
      className="h-3.5 w-3.5"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M10 3 5 8l5 5" />
    </svg>
  );
}
