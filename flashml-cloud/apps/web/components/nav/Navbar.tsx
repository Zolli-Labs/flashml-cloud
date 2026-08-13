"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { List, X } from "@phosphor-icons/react";
import { Wordmark } from "@/components/brand/Mark";
import { MARKETING } from "@/lib/marketing";

const navLinks = [
  { href: "/#how-it-works", label: "How it works" },
  { href: "/#platform", label: "Platform" },
  { href: "/#services", label: "Services" },
];

export function Navbar() {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const isHome = pathname === "/";
  const opaque = !isHome || scrolled;

  useEffect(() => {
    if (!isHome) return;

    let frame = 0;
    const syncScrolledState = () => {
      frame = 0;
      setScrolled(window.scrollY > 48);
    };
    const scheduleSync = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(syncScrolledState);
    };

    scheduleSync();
    window.addEventListener("scroll", scheduleSync, { passive: true });
    return () => {
      window.removeEventListener("scroll", scheduleSync);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [isHome]);

  useEffect(() => {
    if (!menuOpen) return;

    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
        menuButtonRef.current?.focus();
      }
    };

    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [menuOpen]);

  const closeMenu = () => setMenuOpen(false);

  return (
    <header
      data-opaque={opaque ? "true" : "false"}
      className={`fixed inset-x-0 top-0 z-50 border-b ${
        opaque
          ? "border-border bg-[color:rgb(11_13_14_/_0.94)]"
          : "border-transparent bg-transparent"
      }`}
    >
      <div className="mx-auto max-w-[1240px]">
        <div className="flex h-[68px] items-center justify-between px-5 sm:px-6">
          <Link
            href="/"
            aria-label="Zolli Cloud home"
            onClick={closeMenu}
            className="interactive inline-flex min-h-10 items-center"
          >
            <Wordmark product tone="dark" />
          </Link>

          <nav aria-label="Primary navigation" className="hidden items-center gap-1 md:flex">
            {navLinks.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                className="interactive inline-flex min-h-10 items-center rounded-[7px] px-3 text-[13px] text-muted-foreground hover:bg-surface-2 hover:text-foreground"
              >
                {link.label}
              </Link>
            ))}
            <a
              href={MARKETING.runtimeRepo}
              target="_blank"
              rel="noreferrer"
              aria-label="Open runtime (opens in a new tab)"
              className="interactive inline-flex min-h-10 items-center rounded-[7px] px-3 text-[13px] text-muted-foreground hover:bg-surface-2 hover:text-foreground"
            >
              Open runtime
            </a>
            <Link
              href={MARKETING.consolePath}
              className="interactive ml-3 inline-flex min-h-10 items-center rounded-[7px] border border-white/30 bg-[#f2efe6] px-4 text-[13px] font-semibold text-[#111415] hover:bg-white"
            >
              Open console
            </Link>
          </nav>

          <button
            ref={menuButtonRef}
            type="button"
            aria-label={menuOpen ? "Close navigation menu" : "Open navigation menu"}
            aria-controls="mobile-navigation"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
            className={`interactive inline-flex h-10 w-10 items-center justify-center rounded-[7px] border text-foreground hover:bg-surface-2 md:hidden ${
              menuOpen ? "border-[color:var(--z-border-strong)] bg-surface-2" : "border-border bg-surface"
            }`}
          >
            {menuOpen ? <X size={20} weight="bold" /> : <List size={20} weight="bold" />}
          </button>
        </div>

        <nav
          id="mobile-navigation"
          aria-label="Mobile navigation"
          hidden={!menuOpen}
          className="border-t border-border bg-background px-5 py-4 md:hidden"
        >
          <div className="flex flex-col gap-1">
            {navLinks.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                onClick={closeMenu}
                className="interactive rounded-[7px] px-3 py-3 text-sm font-medium text-foreground hover:bg-surface-2"
              >
                {link.label}
              </Link>
            ))}
            <a
              href={MARKETING.runtimeRepo}
              target="_blank"
              rel="noreferrer"
              aria-label="Open runtime (opens in a new tab)"
              onClick={closeMenu}
              className="interactive rounded-[7px] px-3 py-3 text-sm font-medium text-foreground hover:bg-surface-2"
            >
              Open runtime
            </a>
            <Link
              href={MARKETING.consolePath}
              onClick={closeMenu}
              className="interactive mt-2 inline-flex min-h-10 items-center justify-center rounded-[7px] bg-[#f2efe6] px-5 py-3 text-sm font-semibold text-[#111415] hover:bg-white"
            >
              Open console
            </Link>
          </div>
        </nav>
      </div>
    </header>
  );
}
