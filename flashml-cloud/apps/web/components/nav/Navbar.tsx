"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { List, X } from "@phosphor-icons/react";
import { motion, useMotionValueEvent, useReducedMotion, useScroll } from "motion/react";
import { Wordmark } from "@/components/brand/Mark";
import { QUICK } from "@/lib/motion";

const navLinks = [
  { href: "#how-it-works", label: "How it works" },
  { href: "#crew", label: "Meet the crew" },
];

const RUNTIME_REPO = "https://github.com/Zolli-Labs/flashml";

export function Navbar() {
  const [compressed, setCompressed] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const { scrollY } = useScroll();
  const reduce = useReducedMotion();

  useMotionValueEvent(scrollY, "change", (latest) => {
    setCompressed(latest > 100);
  });

  useEffect(() => {
    if (!menuOpen) return;

    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };

    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [menuOpen]);

  const closeMenu = () => setMenuOpen(false);

  return (
    <header className="pointer-events-none fixed inset-x-0 top-0 z-50 px-3 sm:px-5">
      <motion.div
        animate={reduce ? undefined : { y: compressed ? 8 : 0 }}
        transition={QUICK}
        className={`pointer-events-auto mx-auto mt-0 transition-[max-width,background-color,border-color,border-radius,box-shadow] duration-300 ${
          compressed || menuOpen
            ? "glass-strong max-w-5xl rounded-2xl border border-border shadow-lg"
            : "max-w-7xl rounded-none border border-transparent bg-transparent"
        }`}
      >
        <div className={`flex items-center justify-between px-4 sm:px-6 ${compressed ? "h-14" : "h-18"}`}>
          <Link href="/" aria-label="ZolliAI home" onClick={closeMenu}>
            <Wordmark product />
          </Link>

          <nav aria-label="Primary navigation" className="hidden items-center gap-1 md:flex">
            {navLinks.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                className="rounded-full px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2"
              >
                {link.label}
              </Link>
            ))}
            <a
              href={RUNTIME_REPO}
              target="_blank"
              rel="noreferrer"
              className="rounded-full px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2"
            >
              Open runtime
            </a>
            <Link
              href="/workspaces"
              className="ml-2 inline-flex rounded-full bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition-shadow hover:shadow-md focus-visible:outline-2 focus-visible:outline-offset-2"
            >
              Build your crew
            </Link>
          </nav>

          <button
            type="button"
            aria-label={menuOpen ? "Close navigation menu" : "Open navigation menu"}
            aria-controls="mobile-navigation"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
            className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-border bg-surface text-foreground shadow-sm md:hidden"
          >
            {menuOpen ? <X size={20} weight="bold" /> : <List size={20} weight="bold" />}
          </button>
        </div>

        {menuOpen && (
          <nav
            id="mobile-navigation"
            aria-label="Mobile navigation"
            className="border-t border-border px-4 py-4 md:hidden"
          >
            <div className="flex flex-col gap-1">
              {navLinks.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  onClick={closeMenu}
                  className="rounded-xl px-3 py-3 text-base font-medium text-foreground hover:bg-surface-2"
                >
                  {link.label}
                </Link>
              ))}
              <a
                href={RUNTIME_REPO}
                target="_blank"
                rel="noreferrer"
                onClick={closeMenu}
                className="rounded-xl px-3 py-3 text-base font-medium text-foreground hover:bg-surface-2"
              >
                Open runtime
              </a>
              <Link
                href="/workspaces"
                onClick={closeMenu}
                className="mt-2 inline-flex justify-center rounded-full bg-primary px-5 py-3 text-sm font-semibold text-primary-foreground transition-shadow hover:shadow-md"
              >
                Build your crew
              </Link>
            </div>
          </nav>
        )}
      </motion.div>
    </header>
  );
}
