import Link from "next/link";
import { Mark } from "@/components/brand/Mark";

/** 404. Offers the two destinations that are actually useful rather than a
 * single "go home" that lands a signed-in user on the marketing page. */
export default function NotFound() {
  return (
    <div className="mx-auto flex min-h-[60dvh] max-w-lg flex-col items-center justify-center px-4 text-center">
      <Mark size={34} className="text-brand" />
      <p className="mt-3 font-mono text-sm text-muted-foreground">404</p>
      <h1 className="mt-2 text-lg font-semibold">This page doesn&apos;t exist</h1>
      <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
        The link may be out of date, or the job or Machine it pointed at may
        have been removed.
      </p>
      <div className="mt-6 flex flex-wrap justify-center gap-2">
        <Link
          href="/overview"
          className="interactive rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110"
        >
          Overview
        </Link>
        <Link
          href="/jobs"
          className="rounded-md border border-border bg-surface px-4 py-2 text-sm hover:bg-surface-2"
        >
          Jobs
        </Link>
      </div>
    </div>
  );
}
