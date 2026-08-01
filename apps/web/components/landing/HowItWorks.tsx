"use client";

import { motion } from "motion/react";
import Link from "next/link";
import {
  HardDrive,
  Sliders,
  ArrowsSplit,
  Funnel,
  ChartLineUp,
  ArrowRight,
} from "@phosphor-icons/react";

const steps = [
  {
    icon: HardDrive,
    label: "Upload",
    title: "Choose the dataset",
    desc: "FlashML sends the selected built-in dataset to the coordinator.",
    color: "text-cyan",
    bg: "bg-cyan/8",
    border: "border-cyan/25",
  },
  {
    icon: Sliders,
    label: "Configure",
    title: "Select algorithm and workers",
    desc: "Pick K-Means, choose the worker count, and set the maximum iterations.",
    color: "text-violet-400",
    bg: "bg-violet-400/8",
    border: "border-violet-400/25",
  },
  {
    icon: ArrowsSplit,
    label: "Partition",
    title: "Dataset is sharded",
    desc: "FlashML splits data into partitions and writes shards to NetworkVolume.",
    color: "text-amber-400",
    bg: "bg-amber-400/8",
    border: "border-amber-400/25",
  },
  {
    icon: Funnel,
    label: "Map/Reduce",
    title: "Distributed computation",
    desc: "Workers run Map in parallel. Reducer aggregates centroids each iteration.",
    color: "text-cyan",
    bg: "bg-cyan/8",
    border: "border-cyan/25",
  },
  {
    icon: ChartLineUp,
    label: "Results",
    title: "Model output and metrics",
    desc: "Final clusters, convergence charts, and worker telemetry in the dashboard.",
    color: "text-node-green",
    bg: "bg-node-green/8",
    border: "border-node-green/25",
  },
];

export function HowItWorks() {
  return (
    <section className="py-24 px-4 sm:px-6 max-w-7xl mx-auto border-t border-border/40">
      <div className="mb-14">
        <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-foreground mb-4">
          How it works
        </h2>
        <p className="text-muted-foreground max-w-lg">
          From dataset to trained model without cluster setup.
        </p>
      </div>

      {/* Pipeline steps */}
      <div className="relative">
        {/* Connector line */}
        <div className="hidden lg:block absolute top-9 left-0 right-0 h-px bg-gradient-to-r from-transparent via-border/50 to-transparent" />

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-6">
          {steps.map((step, i) => (
            <motion.div
              key={step.label}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, amount: 0.3 }}
              transition={{ duration: 0.5, delay: i * 0.08, ease: [0.16, 1, 0.3, 1] }}
              className="relative flex flex-col"
            >
              <div className={`flex items-center justify-center w-[70px] h-[70px] rounded-xl border ${step.border} ${step.bg} mb-5 relative z-10 bg-background`}>
                <step.icon className={`w-6 h-6 ${step.color}`} weight="duotone" />
              </div>
              <div className="text-[10px] font-mono text-muted-foreground uppercase tracking-widest mb-1">
                {String(i + 1).padStart(2, "0")} / {step.label}
              </div>
              <h3 className="text-sm font-semibold text-foreground mb-2">{step.title}</h3>
              <p className="text-xs text-muted-foreground leading-relaxed">{step.desc}</p>
            </motion.div>
          ))}
        </div>
      </div>

      {/* CTA */}
      <motion.div
        initial={{ opacity: 0, y: 16 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true }}
        transition={{ duration: 0.5, delay: 0.2 }}
        className="mt-16 flex flex-col sm:flex-row items-start sm:items-center gap-6 p-6 rounded-xl border border-cyan/20 bg-cyan/5"
      >
        <div>
          <h3 className="text-lg font-semibold text-foreground mb-1">
            Ready to train at scale?
          </h3>
          <p className="text-sm text-muted-foreground">
            Start a distributed K-Means job and inspect the measured training output.
          </p>
        </div>
        <Link
          href="/submit"
          className="shrink-0 inline-flex items-center gap-2 px-6 py-3 rounded-lg bg-cyan text-background font-semibold text-sm hover:bg-cyan/90 active:scale-[0.98] transition-all glow-cyan"
        >
          Launch Training <ArrowRight weight="bold" className="w-4 h-4" />
        </Link>
      </motion.div>
    </section>
  );
}
