"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { submitJob } from "@/lib/poc-api";

const defaults = {
  samples: 150000,
  dimensions: 24,
  clusters: 6,
  shards: 36,
  iterations: 12,
  seed: 42,
};

export default function SubmitPage() {
  const router = useRouter();
  const [params, setParams] = useState(defaults);
  const [environment, setEnvironment] = useState<"auto" | "local" | "alibaba-ack">("auto");
  const [isolation, setIsolation] = useState<"standard" | "sandboxed">("standard");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit() {
    setBusy(true);
    setError(null);
    try {
      const job = await submitJob({
        apiVersion: "flashml.dev/v1alpha1",
        kind: "Job",
        metadata: { name: "distributed-kmeans-demo" },
        spec: {
          execution: { backend: "ray", environment },
          image: { repository: "flashml/kmeans", tag: "poc-v1" },
          workload: {
            type: "sharded_kmeans",
            parameters: { ...params, task_delay_seconds: 0.4 },
          },
          resources: {
            minimumWorkers: 2,
            maximumWorkers: 3,
            cpuPerTask: 1,
            memoryPerTask: "512Mi",
          },
          placement: { pool: "any", architectures: ["amd64", "arm64"] },
          isolation: { tier: isolation, allowFallback: false },
          retryPolicy: { maxTaskAttempts: 4, retryWorkerLoss: true },
          artifacts: { outputPrefix: "artifact://jobs/{job_id}/" },
        },
      });
      router.push(`/jobs/${job.job_id}`);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
      setBusy(false);
    }
  }

  const fields: { key: keyof typeof defaults; label: string }[] = [
    { key: "samples", label: "Samples" },
    { key: "dimensions", label: "Dimensions" },
    { key: "clusters", label: "Clusters (k)" },
    { key: "shards", label: "Shards" },
    { key: "iterations", label: "Iterations" },
    { key: "seed", label: "Seed" },
  ];

  return (
    <main className="max-w-2xl mx-auto px-4 sm:px-6 py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold font-mono">Submit Job</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Distributed K-Means template — executed by FlashRuntime on Ray/KubeRay
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-mono">Workload parameters</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-4">
          {fields.map(({ key, label }) => (
            <div key={key} className="space-y-1.5">
              <Label className="text-xs uppercase tracking-wide">{label}</Label>
              <Input
                type="number"
                value={params[key]}
                onChange={(e) =>
                  setParams({ ...params, [key]: Number(e.target.value) })
                }
                className="font-mono"
              />
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-mono">Placement</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <Label className="text-xs uppercase tracking-wide">Environment</Label>
            <select
              value={environment}
              onChange={(e) => setEnvironment(e.target.value as typeof environment)}
              className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm font-mono"
            >
              <option value="auto">auto</option>
              <option value="local">Local (Kind)</option>
              <option value="alibaba-ack">Alibaba ACK</option>
            </select>
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs uppercase tracking-wide">Isolation</Label>
            <select
              value={isolation}
              onChange={(e) => setIsolation(e.target.value as typeof isolation)}
              className="w-full h-9 rounded-md border border-input bg-transparent px-3 text-sm font-mono"
            >
              <option value="standard">standard container</option>
              <option value="sandboxed">sandboxed (ACK secure pool)</option>
            </select>
            {isolation === "sandboxed" && (
              <p className="text-[11px] text-amber-400">
                Rejected in the local profile — requires a compatible ACK secure
                node pool.
              </p>
            )}
          </div>
        </CardContent>
      </Card>

      {error && (
        <Card className="border-red-500/30">
          <CardContent className="py-3 text-sm font-mono text-red-400">{error}</CardContent>
        </Card>
      )}

      <Button
        onClick={onSubmit}
        disabled={busy}
        className="w-full bg-cyan/10 text-cyan border border-cyan/30 hover:bg-cyan/20"
      >
        {busy ? "submitting…" : "Submit distributed K-Means"}
      </Button>
    </main>
  );
}
