"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Mark } from "@/components/brand/Mark";
import { UserMenu } from "./UserMenu";

const navLinks = [
  { href: "/machines", label: "Machines" },
  { href: "/jobs", label: "Jobs" },
  { href: "/submit", label: "Submit" },
  { href: "/activate", label: "Activate" },
];

export function Navbar() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-50 glass border-b border-border/50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 flex items-center justify-between h-16">
        {/* Logo */}
        <Link href="/" className="flex items-center gap-2 group">
          <Mark size={22} className="text-foreground" />
          {/* No version chip. A "v0.1" badge next to the wordmark is a
              stage label, not information anyone needs, and it undersells
              the product to every first-time visitor. */}
          <span className="font-mono font-bold tracking-tight text-foreground">
            Flash<span className="text-brand-foreground">ML</span>
          </span>
        </Link>

        {/* Nav Links — hidden below md: at phone widths there is only
            room for the logo and account control, and this app is
            phone-first (the /activate screen exists to be used on one). */}
        <nav className="hidden md:flex items-center gap-1">
          {navLinks.map((link) => {
            const active = pathname === link.href;
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`
                  px-3 py-1.5 rounded-md text-sm font-medium transition-all duration-150
                  ${active
                    ? "bg-cyan/10 text-brand-foreground border border-cyan/25"
                    : "text-muted-foreground hover:text-foreground hover:bg-surface-2"
                  }
                `}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>

        <div className="flex items-center gap-3">
          <Link
            href="/submit"
            className="hidden md:inline-flex px-4 py-1.5 rounded-md bg-cyan text-foreground text-sm font-semibold hover:bg-cyan/90 active:scale-[0.98] transition-all glow-cyan"
          >
            Submit a job
          </Link>
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
