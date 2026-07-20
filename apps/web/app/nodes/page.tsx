"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { MetricCard } from "@/components/shared/MetricCard";
import { Cpu, HardDrives, ShieldCheck } from "@phosphor-icons/react";
import { fetchNodes, formatBytes, type NodeStatusView } from "@/lib/poc-api";

export default function NodesPage() {
  const [nodes, setNodes] = useState<NodeStatusView[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const tick = () =>
      fetchNodes()
        .then((n) => live && (setNodes(n), setError(null)))
        .catch((e) => live && setError(String(e)));
    tick();
    const t = setInterval(tick, 3000);
    return () => { live = false; clearInterval(t); };
  }, []);

  const online = nodes?.filter((n) => n.online).length ?? 0;

  return (
    <main className="max-w-7xl mx-auto px-4 sm:px-6 py-8 space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold font-mono">Compute Nodes</h1>
          <p className="text-sm text-muted-foreground mt-1">
            FlashNode agents reporting from each Kubernetes node
          </p>
        </div>
        <div className="flex gap-3">
          <MetricCard label="Online" value={online} accent="green" icon={<Cpu />} />
          <MetricCard label="Registered" value={nodes?.length ?? "—"} icon={<HardDrives />} />
        </div>
      </div>

      {error && (
        <Card className="border-red-500/30">
          <CardContent className="py-4 text-sm text-red-400 font-mono">
            cloud API unreachable: {error}
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {nodes?.map((n) => (
          <Card key={n.registration.node_id} className="relative overflow-hidden">
            <div
              className={`absolute inset-x-0 top-0 h-0.5 ${
                n.online ? "bg-node-green" : "bg-red-500"
              }`}
            />
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="font-mono text-sm">
                  {n.registration.node_id}
                </CardTitle>
                <Badge
                  variant="outline"
                  className={
                    n.online
                      ? "text-node-green border-node-green/40"
                      : "text-red-400 border-red-400/40"
                  }
                >
                  {n.online ? "online" : "offline"}
                </Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-1.5 text-sm">
              <Row k="k8s node" v={n.registration.kubernetes_node} mono />
              <Row k="environment" v={n.registration.environment} />
              <Row k="pool" v={n.registration.pool} />
              <Row k="arch" v={n.registration.capabilities.architecture} mono />
              <Row
                k="cpu"
                v={`${n.registration.capabilities.cpu_cores ?? "—"} cores`}
              />
              <Row k="memory" v={formatBytes(n.registration.capabilities.memory_bytes)} />
              <Row
                k="gpu"
                v={
                  n.registration.capabilities.gpus.length
                    ? `${n.registration.capabilities.gpus.length}×`
                    : "none"
                }
              />
              <Row k="tasks accepted" v={String(n.accepted_task_count)} />
              <Row
                k="last heartbeat"
                v={
                  n.last_heartbeat
                    ? new Date(n.last_heartbeat).toLocaleTimeString()
                    : "—"
                }
                mono
              />
              {n.registration.sandbox_capable && (
                <div className="flex items-center gap-1.5 pt-1 text-xs text-cyan">
                  <ShieldCheck className="w-3.5 h-3.5" /> sandbox-capable
                </div>
              )}
            </CardContent>
          </Card>
        ))}
      </div>

      {nodes && nodes.length === 0 && (
        <p className="text-muted-foreground text-sm font-mono">
          no nodes registered yet — is the FlashNode DaemonSet running?
        </p>
      )}
    </main>
  );
}

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-4">
      <span className="text-muted-foreground text-xs uppercase tracking-wide">{k}</span>
      <span className={mono ? "font-mono text-xs" : "text-xs"}>{v}</span>
    </div>
  );
}
