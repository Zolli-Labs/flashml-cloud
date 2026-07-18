"use client";

import { motion } from "motion/react";
import {
  Shuffle,
  Graph,
  Lightning,
  ChartBar,
  TreeStructure,
  Cpu,
} from "@phosphor-icons/react";

const features = [
  {
    icon: Shuffle,
    title: "MapReduce Orchestration",
    desc: "Dataset is automatically partitioned into shards. Workers run the Map phase in parallel, the Reducer aggregates results each iteration.",
    accent: "cyan",
  },
  {
    icon: Graph,
    title: "Distributed Architecture",
    desc: "Workers run as serverless RunPod Flash endpoints. No Docker, no Kubernetes, no GPU provisioning. Just code and data.",
    accent: "violet",
  },
  {
    icon: Lightning,
    title: "RunPod Flash Compute",
    desc: "Each worker is an auto-scaled GPU/CPU endpoint. Numeric workloads use cheap CPU instances. Embedding jobs get GPU workers.",
    accent: "amber",
  },
  {
    icon: ChartBar,
    title: "Real-Time Monitoring",
    desc: "Live job metrics from the coordinator: movement, inertia, cluster counts, shard sizes, elapsed time, and per-worker local loss.",
    accent: "green",
  },
  {
    icon: TreeStructure,
    title: "Algorithm Library",
    desc: "K-Means today. Logistic Regression, Random Forest, PCA, PageRank, DBSCAN, TF-IDF, and Nearest Neighbor Search on the roadmap.",
    accent: "cyan",
  },
  {
    icon: Cpu,
    title: "NetworkVolume Sharing",
    desc: "Dataset shards are written once to a shared NetworkVolume. Workers read directly every iteration, eliminating re-upload overhead.",
    accent: "violet",
  },
];

const accentMap: Record<string, { bg: string; border: string; icon: string; glow: string }> = {
  cyan: {
    bg: "bg-cyan/8",
    border: "border-cyan/20",
    icon: "text-cyan",
    glow: "group-hover:border-cyan/40",
  },
  violet: {
    bg: "bg-violet-400/8",
    border: "border-violet-400/20",
    icon: "text-violet-400",
    glow: "group-hover:border-violet-400/40",
  },
  amber: {
    bg: "bg-amber-400/8",
    border: "border-amber-400/20",
    icon: "text-amber-400",
    glow: "group-hover:border-amber-400/40",
  },
  green: {
    bg: "bg-node-green/8",
    border: "border-node-green/20",
    icon: "text-node-green",
    glow: "group-hover:border-node-green/40",
  },
};

export function FeatureGrid() {
  return (
    <section className="py-24 px-4 sm:px-6 max-w-7xl mx-auto">
      <div className="mb-14">
        <h2 className="text-3xl sm:text-4xl font-bold tracking-tight text-foreground mb-4">
          Infrastructure that disappears
        </h2>
        <p className="text-muted-foreground max-w-lg">
          FlashML handles partitioning, worker lifecycle, and result aggregation. You focus on the machine learning.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {features.map((feat, i) => {
          const a = accentMap[feat.accent];
          return (
            <motion.div
              key={feat.title}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, amount: 0.2 }}
              transition={{ duration: 0.5, delay: i * 0.07, ease: [0.16, 1, 0.3, 1] }}
              className={`group p-5 rounded-lg border ${a.border} bg-surface hover:bg-surface-elevated ${a.glow} transition-all duration-200`}
            >
              <div className={`inline-flex items-center justify-center w-9 h-9 rounded-md ${a.bg} border ${a.border} mb-4`}>
                <feat.icon className={`w-4.5 h-4.5 ${a.icon}`} weight="duotone" />
              </div>
              <h3 className="text-sm font-semibold text-foreground mb-2">{feat.title}</h3>
              <p className="text-sm text-muted-foreground leading-relaxed">{feat.desc}</p>
            </motion.div>
          );
        })}
      </div>
    </section>
  );
}
