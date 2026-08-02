import type { Attempt } from "@/lib/job-activity";

/**
 * Reconstruct where every task was, at an arbitrary instant.
 *
 * This is the piece that makes the topology view worth having. The event
 * ledger is append-only and timestamped, so the placement of a job is not
 * only knowable *now* — it is knowable at every moment the job has ever
 * been in. Most consoles show you the present and a log and leave you to
 * imagine the middle; here the middle is recoverable exactly, from data the
 * coordinator already wrote down.
 *
 * An attempt occupies a node for [startedAt, endedAt). Half-open on purpose:
 * a lease that expires at T and is re-claimed elsewhere at the same T must
 * not show the task in two places at once, which is precisely the moment a
 * viewer is most likely to be scrubbing through.
 *
 * Nothing is interpolated. If the ledger says nothing happened between two
 * events, this returns the state the last event left behind.
 */
export interface HeldTask {
  taskId: string;
  outcome: Attempt["outcome"];
  startedAt: number;
  /** null while the attempt was still open at the queried instant. */
  endedAt: number | null;
}

export interface PlacementSnapshot {
  /** node id -> the tasks that node was running at the instant. */
  byNode: Map<string, HeldTask[]>;
  /** Nodes that had finished all their work by the instant, so the canvas
   * can show them present-but-idle rather than dropping them and making the
   * layout jump as you scrub. */
  idleNodes: string[];
  activeCount: number;
}

export function placementAt(
  attempts: Attempt[],
  at: number,
  allNodes: string[]
): PlacementSnapshot {
  const byNode = new Map<string, HeldTask[]>();
  for (const n of allNodes) byNode.set(n, []);

  for (const a of attempts) {
    if (a.startedAt > at) continue; // not started yet at this instant
    const end = a.endedAt;
    if (end !== null && end <= at) continue; // already finished
    const list = byNode.get(a.nodeId);
    const held: HeldTask = {
      taskId: a.taskId,
      outcome: a.outcome,
      startedAt: a.startedAt,
      endedAt: a.endedAt,
    };
    if (list) list.push(held);
    else byNode.set(a.nodeId, [held]);
  }

  const idleNodes: string[] = [];
  let activeCount = 0;
  for (const [node, held] of byNode) {
    if (held.length === 0) idleNodes.push(node);
    else activeCount += held.length;
  }

  return { byNode, idleNodes, activeCount };
}

/** The instant each attempt started or ended, deduped and sorted. These are
 * the only moments the picture can change, so the scrubber snaps to them
 * rather than to arbitrary pixel positions: dragging lands on a real event
 * instead of between two. */
export function significantMoments(attempts: Attempt[]): number[] {
  const set = new Set<number>();
  for (const a of attempts) {
    set.add(a.startedAt);
    if (a.endedAt !== null) set.add(a.endedAt);
  }
  return [...set].sort((x, y) => x - y);
}
