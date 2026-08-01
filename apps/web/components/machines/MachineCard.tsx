"use client";

import { useState } from "react";
import { Circle, Trash, Warning } from "@phosphor-icons/react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Machine } from "@/lib/cloud-api";

// A machine's heartbeat interval isn't part of the API contract, so this is
// a display heuristic, not a fact the API asserts: "recently" seen reads as
// online, otherwise offline. Never treated as more precise than that.
const ONLINE_WITHIN_MS = 90_000;

function relativeTime(iso: string | null): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "never";
  const deltaMs = Date.now() - then;
  if (deltaMs < 0) return "just now";
  const s = Math.floor(deltaMs / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function isOnline(lastSeenAt: string | null): boolean {
  if (!lastSeenAt) return false;
  const then = new Date(lastSeenAt).getTime();
  if (Number.isNaN(then)) return false;
  return Date.now() - then < ONLINE_WITHIN_MS;
}

export function MachineCard({
  machine,
  onRevoke,
}: {
  machine: Machine;
  onRevoke: (machineId: string) => Promise<void>;
}) {
  const [confirming, setConfirming] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const [revokeError, setRevokeError] = useState<string | null>(null);

  const revoked = machine.status === "revoked";
  const online = !revoked && isOnline(machine.last_seen_at);
  const label = machine.name || machine.node_id;

  async function confirmRevoke() {
    setRevoking(true);
    setRevokeError(null);
    try {
      await onRevoke(machine.id);
    } catch {
      setRevokeError("Couldn't revoke this machine. Try again.");
      setRevoking(false);
      return;
    }
    setRevoking(false);
    setConfirming(false);
  }

  return (
    <Card>
      <CardContent className="flex flex-col gap-3 pt-1">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-mono font-medium text-sm text-foreground truncate">
              {label}
            </p>
            <p className="text-xs text-muted-foreground font-mono truncate">
              {machine.node_id}
            </p>
          </div>
          {revoked ? (
            <Badge variant="outline" className="text-muted-foreground shrink-0">
              Revoked
            </Badge>
          ) : (
            <Badge
              variant="outline"
              className={`shrink-0 gap-1.5 ${
                online
                  ? "text-node-green border-node-green/40"
                  : "text-muted-foreground border-muted"
              }`}
            >
              <Circle
                weight="fill"
                className={`w-2 h-2 ${online ? "text-node-green" : "text-muted-foreground"}`}
              />
              {online ? "Online" : "Offline"}
            </Badge>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          {machine.platform ? (
            <span>{machine.platform}</span>
          ) : (
            <span>Unknown platform</span>
          )}
          <span>
            Last seen{" "}
            <span className="text-foreground/80">
              {relativeTime(machine.last_seen_at)}
            </span>
          </span>
        </div>

        {revoked ? (
          <p className="text-xs text-muted-foreground">
            Revoked {relativeTime(machine.revoked_at)}
          </p>
        ) : confirming ? (
          <div className="flex flex-col gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3">
            <div className="flex items-start gap-2 text-xs text-destructive">
              <Warning className="w-4 h-4 shrink-0 mt-0.5" weight="fill" />
              <span>
                Revoke {label}? It will stop taking work immediately and
                cannot be undone from here.
              </span>
            </div>
            {revokeError ? (
              <p className="text-xs text-destructive">{revokeError}</p>
            ) : null}
            <div className="flex gap-2">
              <Button
                type="button"
                variant="destructive"
                size="sm"
                disabled={revoking}
                onClick={confirmRevoke}
              >
                {revoking ? "Revoking…" : "Confirm revoke"}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={revoking}
                onClick={() => {
                  setConfirming(false);
                  setRevokeError(null);
                }}
              >
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={() => setConfirming(true)}
            >
              <Trash className="w-3.5 h-3.5" data-icon="inline-start" />
              Revoke
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
