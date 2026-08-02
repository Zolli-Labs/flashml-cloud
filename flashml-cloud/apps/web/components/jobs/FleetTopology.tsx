"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Pause, Play, SkipBack, SkipForward } from "@phosphor-icons/react";
import { useReducedMotion } from "motion/react";
import type { Attempt } from "@/lib/job-activity";
import { nodesFromAttempts } from "@/lib/job-activity";
import { placementAt, significantMoments, type HeldTask } from "@/lib/placement-at";

// Where the work is, right now — and at any moment it has ever been.
//
// The swimlanes beside this answer "where, over time". This answers "where,
// at this instant", and because the event ledger is append-only and
// timestamped, "this instant" can be any point in the job's life. Scrub back
// and watch a machine go dark and its task reappear somewhere else. That is
// not a simulation: `placementAt` replays the coordinator's own records, and
// nothing between two events is invented.
//
// globals.css has carried a `.flashml-flow` theme since before I arrived —
// control colours, node transitions, animated dashed edges — with nothing
// rendering it. This is the view it was written for.

const OUTCOME_COLOR: Record<Attempt["outcome"], string> = {
  running: "var(--primary)",
  accepted: "var(--node-green)",
  expired: "var(--warning)",
  failed: "var(--destructive)",
  rejected: "var(--muted-foreground)",
};

type MachineData = {
  label: string;
  held: HeldTask[];
  isCoordinator?: boolean;
};

function CoordinatorNode() {
  return (
    <div className="rounded-lg border border-primary/40 bg-primary/10 px-4 py-3 text-center">
      <div className="font-mono text-[11px] text-primary">coordinator</div>
      <div className="meta mt-0.5">holds the queue</div>
    </div>
  );
}

function MachineNode({ data }: NodeProps) {
  const d = data as MachineData;
  const busy = d.held.length > 0;
  return (
    <div
      className="min-w-[150px] rounded-lg border bg-surface px-3 py-2.5 transition-colors"
      style={{
        borderColor: busy ? "var(--primary)" : "var(--border)",
        opacity: busy ? 1 : 0.55,
      }}
    >
      <div className="flex items-center gap-2">
        <span
          className="h-1.5 w-1.5 shrink-0 rounded-full"
          style={{
            background: busy ? "var(--node-green)" : "var(--muted-foreground)",
          }}
        />
        <span className="truncate font-mono text-[11px]" title={d.label}>
          {d.label}
        </span>
      </div>
      <div className="mt-2 flex flex-wrap gap-1">
        {d.held.length === 0 ? (
          <span className="meta">idle</span>
        ) : (
          d.held.map((h) => (
            <span
              key={h.taskId}
              className="rounded-sm px-1.5 py-0.5 font-mono text-[10px]"
              style={{
                background: `color-mix(in oklch, ${OUTCOME_COLOR[h.outcome]} 18%, transparent)`,
                color: OUTCOME_COLOR[h.outcome],
              }}
            >
              {h.taskId}
            </span>
          ))
        )}
      </div>
    </div>
  );
}

const NODE_TYPES = { coordinator: CoordinatorNode, machine: MachineNode };

function clockAt(ms: number, start: number): string {
  const s = Math.max(0, Math.round((ms - start) / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function FleetTopology({
  attempts,
  now,
}: {
  attempts: Attempt[];
  now: number;
}) {
  const reduce = useReducedMotion();
  const machines = useMemo(() => nodesFromAttempts(attempts), [attempts]);
  const moments = useMemo(() => significantMoments(attempts), [attempts]);

  const start = attempts.length
    ? Math.min(...attempts.map((a) => a.startedAt))
    : now;
  const end = attempts.length
    ? Math.max(...attempts.map((a) => a.endedAt ?? now))
    : now;

  // `null` means live: follow `now` rather than pinning to a past instant.
  // Storing a sentinel instead of `end` matters because `end` keeps moving
  // while a job runs, and a scrubber pinned to a stale `end` would silently
  // stop following.
  const [pinned, setPinned] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const at = pinned ?? end;

  // Replay. Steps event to event rather than by wall-clock, so a job with a
  // four-minute gap between two events does not sit still for four minutes.
  useEffect(() => {
    if (!playing || reduce) return;
    const id = setInterval(() => {
      setPinned((p) => {
        const cur = p ?? start;
        const next = moments.find((m) => m > cur);
        if (next === undefined) {
          setPlaying(false);
          return null; // ran off the end: return to live
        }
        return next;
      });
    }, 900);
    return () => clearInterval(id);
  }, [playing, moments, start, reduce]);

  const snap = useMemo(
    () => placementAt(attempts, at, machines),
    [attempts, at, machines]
  );

  const { nodes, edges } = useMemo(() => {
    // Deterministic layout: coordinator left, machines in a column. No
    // physics and no randomness, so a machine stays where you last looked
    // at it while scrubbing instead of drifting.
    const ns: Node[] = [
      {
        id: "__coordinator",
        type: "coordinator",
        position: { x: 0, y: Math.max(0, (machines.length - 1) * 45) },
        data: {},
        draggable: false,
        selectable: false,
      },
      ...machines.map((m, i) => ({
        id: m,
        type: "machine",
        position: { x: 320, y: i * 90 },
        data: { label: m, held: snap.byNode.get(m) ?? [] } satisfies MachineData,
        draggable: false,
      })),
    ];

    const es: Edge[] = machines.map((m) => {
      const held = snap.byNode.get(m) ?? [];
      const live = held.length > 0;
      return {
        id: `e-${m}`,
        source: "__coordinator",
        target: m,
        animated: live && !reduce,
        style: {
          stroke: live ? "var(--primary)" : "oklch(1 0 0 / 0.10)",
          strokeWidth: live ? 1.5 : 1,
        },
      };
    });

    return { nodes: ns, edges: es };
  }, [machines, snap, reduce]);

  if (attempts.length === 0) return null;

  const idx = moments.findIndex((m) => m >= at);
  const isLive = pinned === null;

  return (
    <div>
      <div className="h-[340px] overflow-hidden rounded-lg border border-border bg-background">
        <ReactFlow
          className="flashml-flow"
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          fitView
          proOptions={{ hideAttribution: true }}
          nodesConnectable={false}
          edgesFocusable={false}
        >
          <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="oklch(1 0 0 / 0.06)" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => {
            if (isLive) setPinned(moments[0] ?? start);
            setPlaying((p) => !p);
          }}
          aria-label={playing ? "Pause replay" : "Replay"}
          className="rounded-md border border-border p-2 text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
        >
          {playing ? <Pause size={14} weight="fill" /> : <Play size={14} weight="fill" />}
        </button>

        <button
          type="button"
          aria-label="Previous event"
          onClick={() => {
            setPlaying(false);
            const prev = [...moments].reverse().find((m) => m < at);
            setPinned(prev ?? moments[0] ?? start);
          }}
          className="rounded-md border border-border p-2 text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
        >
          <SkipBack size={14} weight="fill" />
        </button>

        {/* Snaps to real events, not pixels: dragging lands ON a moment the
            picture actually changes rather than between two. */}
        <input
          type="range"
          min={0}
          max={Math.max(moments.length - 1, 0)}
          value={idx < 0 ? Math.max(moments.length - 1, 0) : idx}
          onChange={(e) => {
            setPlaying(false);
            setPinned(moments[Number(e.target.value)] ?? null);
          }}
          aria-label="Scrub through the job's placement history"
          className="h-1 min-w-[140px] flex-1 cursor-pointer appearance-none rounded-full bg-muted accent-[var(--primary)]"
        />

        <button
          type="button"
          aria-label="Next event"
          onClick={() => {
            setPlaying(false);
            const next = moments.find((m) => m > at);
            setPinned(next ?? null);
          }}
          className="rounded-md border border-border p-2 text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
        >
          <SkipForward size={14} weight="fill" />
        </button>

        <button
          type="button"
          onClick={() => {
            setPlaying(false);
            setPinned(null);
          }}
          className={`rounded-md border px-2.5 py-1.5 font-mono text-[11px] transition-colors ${
            isLive
              ? "border-[var(--node-green)]/40 bg-[var(--node-green)]/10 text-[var(--node-green)]"
              : "border-border text-muted-foreground hover:text-foreground"
          }`}
        >
          {isLive ? "live" : `+${clockAt(at, start)}`}
        </button>
      </div>

      <p className="meta mt-2">
        {snap.activeCount} task{snap.activeCount === 1 ? "" : "s"} in flight
        across {machines.length} machine{machines.length === 1 ? "" : "s"}.
        Replayed from the coordinator&apos;s lease and commit events.
      </p>
    </div>
  );
}
