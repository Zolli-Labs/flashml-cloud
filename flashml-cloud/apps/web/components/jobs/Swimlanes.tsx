"use client";

import { useMemo } from "react";
import type { Attempt } from "@/lib/job-activity";
import { nodesFromAttempts } from "@/lib/job-activity";

// One lane per machine, one block per attempt, laid out on a real time axis.
//
// Everything drawn here comes from LEASE_CLAIMED / commit / expiry events the
// coordinator recorded. Nothing is padded or smoothed: an attempt with no
// closing event is drawn to "now" and coloured as running, because that is
// what the ledger says. If the ledger is empty this renders nothing rather
// than an empty chart frame.

const OUTCOME_STYLE: Record<Attempt["outcome"], string> = {
  accepted: "bg-[var(--node-green)]",
  running: "bg-primary",
  expired: "bg-[var(--warning)]",
  failed: "bg-destructive",
  rejected: "bg-muted-foreground",
};

const OUTCOME_LABEL: Record<Attempt["outcome"], string> = {
  accepted: "accepted",
  running: "running",
  expired: "lease expired",
  failed: "failed",
  rejected: "commit rejected",
};

export function Swimlanes({
  attempts,
  now,
}: {
  attempts: Attempt[];
  now: number;
}) {
  const { lanes, start, span } = useMemo(() => {
    const nodes = nodesFromAttempts(attempts);
    const s = attempts.length
      ? Math.min(...attempts.map((a) => a.startedAt))
      : now;
    const e = attempts.length
      ? Math.max(...attempts.map((a) => a.endedAt ?? now))
      : now;
    return {
      lanes: nodes.map((n) => ({
        node: n,
        items: attempts.filter((a) => a.nodeId === n),
      })),
      start: s,
      // Never zero: a job whose attempts all land in the same millisecond
      // would divide by zero and every block would be NaN% wide.
      span: Math.max(e - s, 1000),
    };
  }, [attempts, now]);

  if (attempts.length === 0) return null;

  const totalSec = Math.round(span / 1000);

  return (
    <div className="overflow-x-auto">
      <div className="min-w-[520px]">
        {lanes.map(({ node, items }) => (
          <div key={node} className="flex items-center gap-3 py-1.5">
            <span
              className="w-28 shrink-0 truncate font-mono text-[11px] text-muted-foreground"
              title={node}
            >
              {node}
            </span>
            <div className="relative h-6 flex-1 rounded-sm bg-surface-2">
              {items.map((a, i) => {
                const left = ((a.startedAt - start) / span) * 100;
                const width =
                  (((a.endedAt ?? now) - a.startedAt) / span) * 100;
                return (
                  <div
                    key={`${a.taskId}-${i}`}
                    className={`absolute top-1 h-4 rounded-[3px] ${OUTCOME_STYLE[a.outcome]}`}
                    style={{
                      left: `${left}%`,
                      // Floor the width so a sub-second attempt is still
                      // visible. A 0.1% block is invisible, and "I cannot
                      // see the failed attempt" is the one thing this chart
                      // must never do.
                      width: `${Math.max(width, 1.2)}%`,
                      opacity: a.outcome === "running" ? 0.75 : 1,
                    }}
                    title={`${a.taskId} on ${node}: ${OUTCOME_LABEL[a.outcome]}`}
                  />
                );
              })}
            </div>
          </div>
        ))}

        <div className="mt-2 flex items-center justify-between border-t border-border pt-2">
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {(
              ["accepted", "running", "expired", "failed", "rejected"] as const
            ).map((o) => (
              <span
                key={o}
                className="flex items-center gap-1.5 text-[10px] text-muted-foreground"
              >
                <span className={`h-2 w-2 rounded-[2px] ${OUTCOME_STYLE[o]}`} />
                {OUTCOME_LABEL[o]}
              </span>
            ))}
          </div>
          <span className="font-mono text-[10px] text-muted-foreground">
            {totalSec < 120 ? `${totalSec}s` : `${Math.round(totalSec / 60)}m`} span
          </span>
        </div>
      </div>
    </div>
  );
}
