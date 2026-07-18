"use client";

import Link from "next/link";
import { motion } from "motion/react";
import { ArrowRight, Play } from "@phosphor-icons/react";
import { NodeBackground } from "@/components/shared/NodeBackground";

export function Hero() {
  return (
    <section className="relative min-h-[100dvh] flex items-center overflow-hidden grid-bg">
      {/* Animated node graph background */}
      <NodeBackground />

      {/* Radial gradient overlay */}
      <div className="absolute inset-0 bg-gradient-to-b from-transparent via-transparent to-background pointer-events-none" />
      <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[300px] bg-cyan/5 rounded-full blur-3xl pointer-events-none" />

      <div className="relative z-10 max-w-7xl mx-auto px-4 sm:px-6 pt-20 pb-16">
        <div className="max-w-4xl">
          {/* Eyebrow */}
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
            className="mb-6"
          >
            <span className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-cyan/25 bg-cyan/8 text-cyan text-xs font-mono">
              <span className="w-1.5 h-1.5 rounded-full bg-node-green pulse-dot" />
              Powered by RunPod Flash
            </span>
          </motion.div>

          {/* Headline */}
          <motion.h1
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.05, ease: [0.16, 1, 0.3, 1] }}
            className="text-4xl sm:text-5xl lg:text-6xl font-bold tracking-tight leading-[1.05] text-foreground mb-6"
          >
            Distributed ML training{" "}
            <span className="text-cyan text-glow-cyan">as simple</span>
            {" "}as a function call.
          </motion.h1>

          {/* Sub */}
          <motion.p
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.1, ease: [0.16, 1, 0.3, 1] }}
            className="text-base sm:text-lg text-muted-foreground max-w-2xl mb-10 leading-relaxed"
          >
            Choose the built-in dataset and model. FlashML partitions your data across RunPod Flash workers, runs the Map phase in parallel, and aggregates results with zero infrastructure setup.
          </motion.p>

          {/* CTAs */}
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.15, ease: [0.16, 1, 0.3, 1] }}
            className="flex flex-wrap gap-4"
          >
            <Link
              href="/launch"
              className="inline-flex items-center gap-2 px-6 py-3 rounded-lg bg-cyan text-background font-semibold text-sm hover:bg-cyan/90 active:scale-[0.98] transition-all glow-cyan"
            >
              Launch Training <ArrowRight weight="bold" className="w-4 h-4" />
            </Link>
            <Link
              href="/dashboard"
              className="inline-flex items-center gap-2 px-6 py-3 rounded-lg border border-border/60 text-foreground/80 font-medium text-sm hover:border-border hover:text-foreground hover:bg-white/5 active:scale-[0.98] transition-all"
            >
              <Play weight="fill" className="w-3.5 h-3.5 text-cyan" />
              View Demo
            </Link>
          </motion.div>

          {/* Code snippet hint */}
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.25, ease: [0.16, 1, 0.3, 1] }}
            className="mt-10"
          >
            <div className="inline-block rounded-lg border border-border/60 bg-[oklch(0.06_0.01_240)] overflow-hidden">
              <div className="flex items-center gap-2 px-4 py-2 border-b border-border/40 bg-surface/30">
                <span className="w-2 h-2 rounded-full bg-red-500/50" />
                <span className="w-2 h-2 rounded-full bg-amber-500/50" />
                <span className="w-2 h-2 rounded-full bg-node-green/50" />
                <span className="ml-2 text-[10px] font-mono text-muted-foreground">train.py</span>
              </div>
              <div className="px-5 py-4 text-sm font-mono leading-relaxed">
                <div>
                  <span className="text-violet-400">from</span>{" "}
                  <span className="text-cyan">flashml</span>{" "}
                  <span className="text-violet-400">import</span>{" "}
                  <span className="text-foreground/80">KMeans</span>
                </div>
                <div className="mt-2">
                  <span className="text-muted-foreground"># Runs distributed across configured workers</span>
                </div>
                <div>
                  <span className="text-node-green">model</span>
                  <span className="text-foreground/60"> = </span>
                  <span className="text-cyan">KMeans</span>
                  <span className="text-foreground/60">(n_clusters=</span>
                  <span className="text-amber-400">k</span>
                  <span className="text-foreground/60">, workers=</span>
                  <span className="text-amber-400">workers</span>
                  <span className="text-foreground/60">)</span>
                </div>
                <div>
                  <span className="text-node-green">model</span>
                  <span className="text-foreground/60">.fit(</span>
                  <span className="text-foreground/80">dataset</span>
                  <span className="text-foreground/60">)</span>
                  <span className="text-muted-foreground">  # That&apos;s it.</span>
                </div>
              </div>
            </div>
          </motion.div>
        </div>
      </div>
    </section>
  );
}
