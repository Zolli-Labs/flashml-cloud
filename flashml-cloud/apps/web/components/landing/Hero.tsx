"use client";

import Link from "next/link";
import { motion, useReducedMotion } from "motion/react";
import { ArrowRight } from "@phosphor-icons/react";
import { ZolliCharacter } from "@/components/brand/ZolliCharacter";
import { MagneticLink } from "@/components/motion/MagneticLink";
import { ZOLLI_ROLES, type ZolliRole } from "@/lib/zolli-brand";
import { BASE, staggerParent, wipeLine } from "@/lib/motion";

const HERO_ROLES = Object.keys(ZOLLI_ROLES) as ZolliRole[];

/** Each headline line rides up from behind its own clip box. The padding
 * protects descenders from that clipping without adding layout space. */
function WipeLine({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span className="block overflow-hidden pb-[0.14em] -mb-[0.14em]">
      <motion.span variants={wipeLine} className={`block ${className}`}>
        {children}
      </motion.span>
    </span>
  );
}

export function Hero() {
  const reduce = useReducedMotion();

  return (
    <section className="relative isolate overflow-hidden pt-24 sm:pt-28">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10"
        style={{
          backgroundImage:
            "radial-gradient(ellipse 55% 45% at 80% 8%, rgb(239 104 40 / 0.11), transparent 68%)",
        }}
      />
      <div className="mx-auto max-w-7xl px-4 pb-18 sm:px-6 md:pb-24">
        <motion.div
          className="flex flex-col items-center text-center"
          variants={staggerParent(0.09)}
          initial={reduce ? false : "hidden"}
          animate="show"
        >
          <p className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-brand-foreground">
            ZolliAI Cloud
          </p>
          <h1 className="mt-5 max-w-5xl font-display text-5xl font-semibold leading-[0.98] tracking-[-0.045em] sm:text-6xl md:text-7xl lg:text-[5.6rem]">
            <WipeLine>Every machine has{" "}</WipeLine>
            <WipeLine>a part to play.</WipeLine>
          </h1>

          <motion.p
            variants={{
              hidden: { opacity: 0, y: 16 },
              show: { opacity: 1, y: 0, transition: BASE },
            }}
            className="mt-7 max-w-[58ch] text-base leading-relaxed text-muted-foreground md:text-lg"
          >
            Bring laptops, GPU rigs, and cloud instances together as one resilient compute crew. When one Zolli drops out, verified progress helps the next one keep work moving.
          </motion.p>

          <motion.div
            variants={{
              hidden: { opacity: 0, y: 16 },
              show: { opacity: 1, y: 0, transition: BASE },
            }}
            className="mt-9 flex flex-wrap items-center justify-center gap-3"
          >
            <MagneticLink
              href="/workspaces"
              className="interactive inline-flex items-center gap-2 rounded-full bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground transition-shadow hover:shadow-md"
            >
              Build your crew
              <ArrowRight weight="bold" className="h-4 w-4" />
            </MagneticLink>
            <Link
              href="#recover"
              className="interactive inline-flex items-center gap-2 rounded-full border border-border bg-surface px-6 py-3 text-sm font-medium text-foreground transition-colors hover:bg-surface-2"
            >
              See how recovery works
            </Link>
          </motion.div>

          <div className="mt-14 grid w-full grid-cols-3 gap-2 sm:grid-cols-6 sm:gap-3 md:mt-18">
            {HERO_ROLES.map((role, index) => {
              const definition = ZOLLI_ROLES[role];
              return (
                <motion.div
                  key={role}
                  variants={{
                    hidden: { opacity: 0, y: 24 },
                    show: { opacity: 1, y: 0, transition: { ...BASE, delay: index * 0.035 } },
                  }}
                  className="flex min-w-0 flex-col items-center rounded-2xl border border-border bg-surface/80 px-1 py-3 shadow-sm sm:px-2 sm:py-4"
                >
                  <ZolliCharacter
                    role={role}
                    size={112}
                    mood={role === "scout" ? "waving" : role === "worker" ? "focused" : "happy"}
                    className="h-auto w-full max-w-[7rem]"
                    label={`${definition.label}, ${definition.subtitle}`}
                  />
                  <span className="mt-1 text-xs font-semibold text-foreground sm:text-sm">
                    {definition.label}
                  </span>
                </motion.div>
              );
            })}
          </div>
        </motion.div>
      </div>
    </section>
  );
}
