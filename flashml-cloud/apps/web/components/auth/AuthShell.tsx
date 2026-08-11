"use client";

import Link from "next/link";
import { Wordmark } from "@/components/brand/Mark";

export type AuthMode = "signin" | "signup";

const COPY: Record<
  AuthMode,
  { eyebrow: string; title: string; subtitle: string; sideTitle: string }
> = {
  signin: {
    eyebrow: "Sign in",
    title: "Welcome back.",
    subtitle: "Access your workspaces, machines, and running jobs.",
    sideTitle: "Your compute, exactly where it left off.",
  },
  signup: {
    eyebrow: "Create account",
    title: "Create your account.",
    subtitle: "Tell us what you run and which compute sources you plan to connect.",
    sideTitle: "Start with the machines you have.",
  },
};

const SYSTEM_STATES = [
  ["LEASES", "bounded ownership"],
  ["CHECKPOINTS", "verified progress"],
  ["RECOVERY", "deterministic actions"],
] as const;

function HomeLink({ dark = false }: { dark?: boolean }) {
  return (
    <Link
      href="/"
      className={`text-[13px] font-medium transition-colors ${
        dark
          ? "text-muted-foreground hover:text-foreground"
          : "text-muted-foreground hover:text-foreground"
      }`}
    >
      Back home
    </Link>
  );
}

export function AuthShell({
  mode,
  onModeChange,
  children,
}: {
  mode: AuthMode;
  onModeChange: (mode: AuthMode) => void;
  children?: React.ReactNode;
}) {
  const copy = COPY[mode];

  return (
    <main id="content" className="grid min-h-dvh w-full bg-[var(--z-app-canvas)] text-ink lg:grid-cols-2">
      <section className="marketing-dark hidden min-h-dvh flex-col border-r border-border bg-background p-10 text-foreground lg:flex xl:p-14">
        <div className="flex items-center justify-between">
          <Link href="/" aria-label="Zolli Cloud home" className="inline-flex">
            <Wordmark size={27} product tone="dark" />
          </Link>
          <HomeLink dark />
        </div>

        <div className="my-auto py-16">
          <p className="font-mono text-[10px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
            Leases · checkpoints · deterministic recovery
          </p>
          <h1 className="mt-6 max-w-[620px] text-[clamp(3rem,5vw,5.5rem)] font-semibold leading-[0.95] tracking-[-0.057em]">
            {copy.sideTitle}
          </h1>
          <p className="mt-7 max-w-[46ch] text-[15px] leading-relaxed text-muted-foreground">
            Zolli keeps machine loss visible and recoverable through real protocol state,
            not optimistic status messages.
          </p>
        </div>

        <div className="border-t border-[var(--z-border-strong)]">
          {SYSTEM_STATES.map(([label, description]) => (
            <div
              key={label}
              className="grid min-h-14 grid-cols-[120px_1fr] items-center border-b border-border font-mono text-[10px]"
            >
              <span className="text-foreground">{label}</span>
              <span className="text-muted-foreground">{description}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="flex min-h-dvh w-full items-center justify-center px-5 py-10 sm:px-8 lg:px-12">
        <div className="w-full max-w-[470px]">
          <div className="mb-9 flex items-center justify-between lg:hidden">
            <Link href="/" aria-label="Zolli Cloud home" className="inline-flex">
              <Wordmark size={24} product />
            </Link>
            <HomeLink />
          </div>

          <div className="mb-6 grid grid-cols-2 rounded-[7px] border border-border bg-[var(--z-app-bg)] p-1" aria-label="Authentication mode">
            {(["signin", "signup"] as const).map((option) => {
              const active = option === mode;
              return (
                <button
                  key={option}
                  type="button"
                  aria-pressed={active}
                  onClick={() => onModeChange(option)}
                  className={`rounded-[4px] px-3 py-2.5 text-sm font-semibold transition-colors ${
                    active
                      ? "bg-surface text-foreground shadow-sm"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {option === "signin" ? "Sign in" : "Create account"}
                </button>
              );
            })}
          </div>

          <section className="rounded-[10px] border border-border bg-surface p-7 sm:p-9">
            <p className="font-mono text-[10px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
              {copy.eyebrow}
            </p>
            <h2 className="mt-3 text-3xl font-semibold tracking-[-0.035em] sm:text-4xl">
              {copy.title}
            </h2>
            <p className="mt-3 text-[15px] leading-relaxed text-muted-foreground">
              {copy.subtitle}
            </p>
            <div className="mt-7">{children}</div>
          </section>
        </div>
      </section>
    </main>
  );
}
