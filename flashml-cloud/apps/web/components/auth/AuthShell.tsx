"use client";

import Link from "next/link";
import { motion, useReducedMotion } from "motion/react";
import { AuthCharacters } from "@/components/auth/AuthCharacters";
import { Mark } from "@/components/brand/Mark";

export type AuthMode = "signin" | "signup";

const COPY: Record<
  AuthMode,
  { eyebrow: string; title: string; subtitle: string; sideTitle: string }
> = {
  signin: {
    eyebrow: "Sign in",
    title: "Welcome back.",
    subtitle: "Sign in to pick up where you left off.",
    sideTitle: "The Crew's been waiting for you.",
  },
  signup: {
    eyebrow: "Create account",
    title: "Build your Crew.",
    subtitle: "Create your account, then tell us what you want your Crew to run.",
    sideTitle: "Say hello to your new Crew.",
  },
};

function AuroraBackground() {
  const reducedMotion = useReducedMotion();

  return (
    <div aria-hidden className="absolute inset-0 overflow-hidden">
      <div className="absolute inset-0 bg-[linear-gradient(135deg,#fbe3d2_0%,#fff5eb_52%,#f6d8c3_100%)]" />
      <motion.div
        className="absolute -left-1/2 -top-1/2 h-full w-full rounded-full bg-brand/15 blur-3xl"
        animate={
          reducedMotion
            ? undefined
            : { x: [0, 90, 0], y: [0, 45, 0], scale: [1, 1.1, 1] }
        }
        transition={{ duration: 22, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.div
        className="absolute -bottom-1/3 -right-1/3 h-2/3 w-2/3 rounded-full bg-[#f0a86a]/20 blur-3xl"
        animate={
          reducedMotion
            ? undefined
            : { x: [0, -70, 0], y: [0, -35, 0], scale: [1, 1.14, 1] }
        }
        transition={{ duration: 26, repeat: Infinity, ease: "easeInOut" }}
      />
    </div>
  );
}

function HomeLink() {
  return (
    <Link
      href="/"
      className="rounded-md text-sm font-semibold text-brand-foreground transition-colors hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-4"
    >
      Back home
    </Link>
  );
}

function AuthWordmark({ compact = false }: { compact?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2">
      <Mark size={compact ? 24 : 28} className="text-brand" />
      <span
        className={`${compact ? "text-lg" : "text-xl"} font-sans font-extrabold tracking-[-0.04em] text-brand-foreground`}
      >
        Zolli<span className="text-muted-foreground">AI</span>
      </span>
    </span>
  );
}

export function AuthShell({
  mode,
  onModeChange,
  children,
}: {
  mode: AuthMode;
  onModeChange: (mode: AuthMode) => void;
  children: React.ReactNode;
}) {
  const copy = COPY[mode];
  const reducedMotion = useReducedMotion();

  return (
    <main id="content" className="flex min-h-dvh w-full bg-cream text-ink">
      <section className="relative hidden min-h-dvh flex-col overflow-hidden lg:flex lg:w-1/2">
        <AuroraBackground />

        <div className="relative z-10 flex items-center justify-between px-10 pt-10 xl:px-14 xl:pt-12">
          <Link href="/" aria-label="ZolliAI home" className="inline-flex">
            <AuthWordmark />
          </Link>
          <HomeLink />
        </div>

        <motion.div
          key={mode}
          initial={reducedMotion ? false : { opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.55, ease: [0.22, 0.61, 0.36, 1] }}
          className="relative z-10 px-10 pt-14 xl:px-14 xl:pt-20"
        >
          <span className="inline-flex items-center gap-2 rounded-full border border-brand/25 bg-surface/60 px-3.5 py-1.5 text-xs font-semibold text-brand-foreground backdrop-blur-sm">
            <span className="relative flex h-2 w-2">
              <span className="motion-safe:animate-ping absolute inline-flex h-full w-full rounded-full bg-brand opacity-60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-brand" />
            </span>
            Distributed compute, one resilient Crew
          </span>
          <h1 className="mt-7 max-w-lg font-display text-[clamp(2.6rem,4vw,4.6rem)] font-medium leading-[1.02] tracking-[-0.045em] text-ink">
            {copy.sideTitle}
          </h1>
        </motion.div>

        <div className="relative z-10 mt-auto flex min-h-[290px] justify-center overflow-visible pb-0 xl:min-h-[340px]">
          <AuthCharacters />
        </div>
      </section>

      <section className="relative flex min-h-dvh w-full items-center justify-center overflow-y-auto px-5 py-8 sm:px-8 lg:w-1/2 lg:px-10 lg:py-12">
        <div className="w-full max-w-[470px]">
          <div className="mb-7 flex items-center justify-between lg:hidden">
            <Link href="/" aria-label="ZolliAI home" className="inline-flex">
              <AuthWordmark compact />
            </Link>
            <HomeLink />
          </div>

          <div className="mb-8 flex justify-center">
            <div
              className="relative grid w-full max-w-[360px] grid-cols-2 rounded-full border border-border bg-surface-2 p-1"
              aria-label="Authentication mode"
            >
              <span
                aria-hidden
                className="pointer-events-none absolute bottom-1 left-1 top-1 w-[calc(50%-4px)] rounded-full bg-primary shadow-[0_8px_20px_-10px_rgba(239,104,40,0.72)] transition-transform duration-300 ease-out motion-reduce:transition-none"
                style={{
                  transform:
                    mode === "signin" ? "translateX(0)" : "translateX(100%)",
                }}
              />
              {(["signin", "signup"] as const).map((option) => {
                const active = option === mode;
                return (
                  <button
                    key={option}
                    type="button"
                    aria-pressed={active}
                    onClick={() => onModeChange(option)}
                    className={`relative z-10 rounded-full px-3 py-2.5 text-sm font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 ${
                      active
                        ? "text-primary-foreground"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {option === "signin" ? "Sign in" : "Create account"}
                  </button>
                );
              })}
            </div>
          </div>

          <motion.section
            key={mode}
            initial={reducedMotion ? false : { opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.35, ease: [0.22, 0.61, 0.36, 1] }}
            className="rounded-[28px] border border-border bg-surface/95 p-7 shadow-[0_38px_90px_-52px_rgba(78,52,32,0.42)] sm:p-9"
          >
            <p className="text-xs font-bold uppercase tracking-[0.2em] text-brand-foreground">
              {copy.eyebrow}
            </p>
            <h2 className="mt-2 font-display text-3xl font-medium tracking-[-0.03em] text-ink sm:text-4xl">
              {copy.title}
            </h2>
            <p className="mt-2 text-[15px] leading-relaxed text-muted-foreground">
              {copy.subtitle}
            </p>
            <div className="mt-7">{children}</div>
          </motion.section>
        </div>
      </section>
    </main>
  );
}
