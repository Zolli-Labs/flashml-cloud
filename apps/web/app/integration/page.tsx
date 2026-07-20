"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { fetchIntegration, type IntegrationStatus } from "@/lib/poc-api";

function StatusBadge({ on, onText = "enabled", offText = "not configured" }:
  { on: boolean; onText?: string; offText?: string }) {
  return (
    <Badge
      variant="outline"
      className={`font-mono text-xs ${
        on ? "text-node-green border-node-green/40" : "text-muted-foreground border-muted"
      }`}
    >
      {on ? onText : offText}
    </Badge>
  );
}

export default function IntegrationPage() {
  const [status, setStatus] = useState<IntegrationStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchIntegration().then(setStatus).catch((e) => setError(String(e)));
  }, []);

  return (
    <main className="max-w-3xl mx-auto px-4 sm:px-6 py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold font-mono">Deployment & Alibaba Cloud</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Where this FlashML instance runs and which Alibaba services back it
        </p>
      </div>

      {error && (
        <Card className="border-red-500/30">
          <CardContent className="py-4 text-sm text-red-400 font-mono">{error}</CardContent>
        </Card>
      )}

      {status && (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-mono">Deployment profile</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2.5 text-sm">
              <Row k="environment" v={<span className="font-mono">{status.environment}</span>} />
              <Row k="execution backend" v={<span className="font-mono">{status.backend}</span>} />
              <Row
                k="artifact store"
                v={<span className="font-mono">{status.artifact_store === "oss" ? "Alibaba OSS" : "MinIO (local)"}</span>}
              />
              <Row
                k="image registry"
                v={<span className="font-mono">{status.image_registry === "local" ? "local registry" : status.image_registry}</span>}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-mono">Alibaba Cloud services</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2.5 text-sm">
              <Row k="ACK (Container Service)" v={<StatusBadge on={status.ack_connected} onText="connected" />} />
              <Row
                k="OSS bucket"
                v={
                  status.oss_bucket ? (
                    <span className="font-mono text-node-green">{status.oss_bucket}</span>
                  ) : (
                    <StatusBadge on={false} />
                  )
                }
              />
              <Row k="Simple Log Service" v={<StatusBadge on={status.sls_enabled} />} />
              <Row k="Managed Prometheus" v={<StatusBadge on={status.prometheus_enabled} />} />
              <Row
                k="Sandboxed-container pool"
                v={<StatusBadge on={status.sandbox_pool_available} onText="available" offText="unavailable" />}
              />
              <Row
                k="PAI-DLC adapter"
                v={<span className="font-mono text-muted-foreground">{status.paidlc_adapter}</span>}
              />
            </CardContent>
          </Card>
        </>
      )}
    </main>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-xs uppercase tracking-wide text-muted-foreground">{k}</span>
      {v}
    </div>
  );
}
