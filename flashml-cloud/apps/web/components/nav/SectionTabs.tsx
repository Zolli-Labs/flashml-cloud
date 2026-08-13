"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * Link tabs for a console section (Market, Machines, Settings): the same
 * visual idiom as the job page's view tabs, but each tab is a route, so the
 * browser's back button and deep links keep working. The section keeps ONE
 * sidebar entry; these tabs are how its inner pages are reached.
 */
export function SectionTabs({
  tabs,
}: {
  tabs: ReadonlyArray<{ href: string; label: string; exact?: boolean }>;
}) {
  const pathname = usePathname();
  return (
    <div className="flex gap-5 border-b border-border">
      {tabs.map(({ href, label, exact }) => {
        const active = exact
          ? pathname === href
          : pathname === href || pathname.startsWith(`${href}/`);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={`-mb-px border-b-2 px-0.5 pb-2.5 text-sm transition-colors ${
              active
                ? "border-primary font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {label}
          </Link>
        );
      })}
    </div>
  );
}
