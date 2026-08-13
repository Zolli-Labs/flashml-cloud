import type { ReactNode } from "react";
import { CaretRight } from "@phosphor-icons/react";

/**
 * A native `<details>`, styled once.
 *
 * NATIVE ON PURPOSE, not a `useState` toggle. The job page is a poll: every
 * 2.5s the page re-renders with a fresh event list, a fresh artifact listing
 * and a fresh task list. A disclosure whose open/closed state lived in React
 * state next to that data would be one careless key change away from
 * snapping shut under a reader mid-sentence; a `<details>` keeps its own
 * state in the DOM, survives a re-render of its contents, is findable by the
 * browser's own in-page search, and needs no JavaScript to open.
 *
 * WHAT GOES INSIDE ONE. The full text, table or file list that the line
 * above it stands in for — verbatim. This component exists because the job
 * page compresses prose, and compression that loses a sentence is deletion:
 * every passage the page stopped showing by default is behind one of these,
 * unedited.
 */
export function Disclosure({
  label,
  children,
  className,
  defaultOpen = false,
}: {
  /** What the reader is asking for, in their words: "why", "the arithmetic",
   * "5 files". Never "more" or "details" — a caret already says that. */
  label: ReactNode;
  children: ReactNode;
  className?: string;
  /** Open on arrival. For the one case where the contents are the reason
   * somebody opened the page — a failed task's logs. */
  defaultOpen?: boolean;
}) {
  return (
    <details className={`group ${className ?? ""}`} open={defaultOpen}>
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground [&::-webkit-details-marker]:hidden">
        <CaretRight
          className="h-3 w-3 shrink-0 transition-transform group-open:rotate-90"
          weight="bold"
        />
        {label}
      </summary>
      <div className="mt-2">{children}</div>
    </details>
  );
}
