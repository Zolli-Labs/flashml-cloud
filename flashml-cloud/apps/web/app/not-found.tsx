import Link from "next/link";

/** 404. Offers the two destinations that are actually useful rather than a
 * single "go home" that lands a signed-in user on the marketing page. */
export default function NotFound() {
  return (
    <div className="mx-auto flex min-h-[60dvh] max-w-lg flex-col items-center justify-center px-4 text-center">
      <p className="font-mono text-sm text-muted-foreground">404</p>
      <h1 className="mt-2 text-lg font-semibold">This page doesn&apos;t exist</h1>
      <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
        The link may be out of date, or the job or machine it pointed at may
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
          className="rounded-md border border-border px-4 py-2 text-sm hover:bg-white/[0.06]"
        >
          Jobs
        </Link>
      </div>
    </div>
  );
}
