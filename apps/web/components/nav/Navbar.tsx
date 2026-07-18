"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Lightning } from "@phosphor-icons/react";

const navLinks = [
  { href: "/launch", label: "Launch Job" },
  { href: "/dashboard", label: "Dashboard" },
  { href: "/visualize", label: "Visualize" },
];

export function Navbar() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-50 glass border-b border-border/50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 flex items-center justify-between h-16">
        {/* Logo */}
        <Link href="/" className="flex items-center gap-2 group">
          <div className="relative flex items-center justify-center w-8 h-8">
            <div className="absolute inset-0 rounded-md bg-cyan/10 border border-cyan/30 group-hover:border-cyan/60 transition-colors" />
            <Lightning className="relative z-10 text-cyan w-4 h-4" weight="fill" />
          </div>
          <span className="font-mono font-bold tracking-tight text-foreground">
            Flash<span className="text-cyan">ML</span>
          </span>
          <span className="hidden sm:inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono bg-cyan/10 text-cyan border border-cyan/20">
            v0.1
          </span>
        </Link>

        {/* Nav Links */}
        <nav className="flex items-center gap-1">
          {navLinks.map((link) => {
            const active = pathname === link.href;
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`
                  px-3 py-1.5 rounded-md text-sm font-medium transition-all duration-150
                  ${active
                    ? "bg-cyan/10 text-cyan border border-cyan/25"
                    : "text-muted-foreground hover:text-foreground hover:bg-white/5"
                  }
                `}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>

        <div className="hidden md:flex items-center gap-3">
          <Link
            href="/launch"
            className="px-4 py-1.5 rounded-md bg-cyan text-background text-sm font-semibold hover:bg-cyan/90 active:scale-[0.98] transition-all glow-cyan"
          >
            Launch Training
          </Link>
        </div>
      </div>
    </header>
  );
}
