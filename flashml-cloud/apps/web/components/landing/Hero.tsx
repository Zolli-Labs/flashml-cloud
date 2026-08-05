"use client";

import Link from "next/link";
import { motion, useReducedMotion } from "motion/react";
import { ArrowRight } from "@phosphor-icons/react";
import { NodeBackground } from "@/components/shared/NodeBackground";
import { EventLedger } from "@/components/landing/EventLedger";
import { MagneticLink } from "@/components/motion/MagneticLink";
import { SAMPLE_LEDGER } from "@/lib/landing/sample-ledger";
import { BASE, SLOW, staggerParent, wipeLine } from "@/lib/motion";

/** Each headline line rides up from behind its own clip box.
 *
 * The `pb`/`-mb` pair is not decoration. An overflow-hidden wipe crops the
 * line box, so descenders (the p in "disappears", the y in "anyway") get
 * sliced off along the bottom edge. The padding gives them room inside the
 * clip and the negative margin takes that space back out of the layout. */
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
    <section className="relative isolate min-h-[100dvh] overflow-hidden">
      {/* The page's ONE atmospheric moment. Everything below the fold is
          flat. Kept off-centre and low-opacity so it reads as a light
          source rather than the centred purple bloom every AI-infra
          landing page ships. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10"
        style={{
          backgroundImage:
            "radial-gradient(ellipse 70% 60% at 78% 8%, oklch(0.55 0.21 285 / 0.16), transparent 62%)",
        }}
      />
      <div aria-hidden className="absolute inset-0 -z-10 opacity-70">
        <NodeBackground />
      </div>

      <div className="mx-auto flex min-h-[100dvh] max-w-7xl items-center px-4 pt-20 pb-16 sm:px-6 md:pt-24">
        <motion.div
          className="grid w-full items-center gap-12 lg:grid-cols-12 lg:gap-10"
          variants={staggerParent(0.09)}
          initial={reduce ? false : "hidden"}
          animate="show"
        >
          <div className="lg:col-span-7">
            {/* 52px, not 60px, and a 7/5 split rather than 6/6. At 60px in
                a half-width column "Cheap compute disappears." wraps, which
                makes the headline three lines. Two is the limit. */}
            {/* NOT `.display`. This headline shares the row with the ledger
                panel, so it has ~715px, and `.display` would wrap "Cheap
                compute disappears." into two lines and the headline into
                three. The page's display moment is the full-bleed ledger
                wall, which has the width to carry it. */}
            <h1 className="text-4xl font-semibold leading-[1.06] tracking-[-0.038em] md:text-5xl lg:text-[3.4rem]">
              <WipeLine>Cheap compute disappears.</WipeLine>
              <WipeLine className="text-accent-text">
                Run on it anyway.
              </WipeLine>
            </h1>

            <motion.p
              variants={{
                hidden: { opacity: 0, y: 16 },
                show: { opacity: 1, y: 0, transition: BASE },
              }}
              className="mt-6 max-w-[46ch] text-base leading-relaxed text-muted-foreground md:text-lg"
            >
              FlashML spreads a training job across pods, rigs and spot
              instances that vanish mid-run. Leases expire, work requeues,
              jobs finish.
            </motion.p>

            <motion.div
              variants={{
                hidden: { opacity: 0, y: 16 },
                show: { opacity: 1, y: 0, transition: BASE },
              }}
              className="mt-9 flex flex-wrap items-center gap-3"
            >
              <MagneticLink
                href="/submit"
                className="interactive inline-flex items-center gap-2 rounded-full bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground hover:brightness-110"
              >
                Submit a job
                <ArrowRight weight="bold" className="h-4 w-4" />
              </MagneticLink>
              <Link
                href="#recover"
                className="interactive inline-flex items-center gap-2 rounded-full border border-border bg-surface px-6 py-3 text-sm font-medium text-foreground transition-colors hover:bg-surface-2"
              >
                See it recover
              </Link>
            </motion.div>
          </div>

          {/* The evidence sits beside the claim, not below it. Offset down
              so the two columns do not read as a symmetric pair. */}
          <motion.div
            variants={{
              hidden: { opacity: 0, y: 26 },
              show: { opacity: 1, y: 0, transition: SLOW },
            }}
            className="lg:col-span-5 lg:mt-12"
          >
            <div className="glass overflow-hidden rounded-lg">
              <EventLedger events={SAMPLE_LEDGER} label="sample run" stream />
            </div>
          </motion.div>
        </motion.div>
      </div>
    </section>
  );
}
