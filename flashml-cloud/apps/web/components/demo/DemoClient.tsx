"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { FleetStrip } from "@/components/demo/FleetStrip";
import { GuestFleet } from "@/components/demo/GuestFleet";
import { GuestRunPanel } from "@/components/demo/GuestRunPanel";
import { JoinCard } from "@/components/demo/JoinCard";
import { RunPanel } from "@/components/demo/RunPanel";
import { SpeedComparison } from "@/components/demo/SpeedComparison";
import { cloudApiBase } from "@/lib/cloud-api";
import {
  anyRunLive,
  apiDetail,
  DEMO_VENUES,
  parseDemoSnapshot,
  runFor,
  runMineErrorMessage,
  snapshotHasJob,
  type DemoCoordinator,
  type DemoSnapshot,
  type JoinedMachine,
} from "@/lib/demo";

/**
 * The live half of `/demo`: one read loop and two write buttons.
 *
 * NO SUPABASE, NO SESSION, NO AUTHENTICATED CLIENT. `lib/cloud-api.ts`'s
 * helpers attach the signed-in user's JWT and throw `NotAuthenticated` when
 * there is none — which is every visitor this page has. So it calls the two
 * public routes with a bare `fetch` and borrows only `cloudApiBase()`, the
 * same thing the public `/share/<token>` page does.
 *
 * THE PAGE NEVER CRASHES ON A MISSING FIELD. The API for these routes is
 * being written in parallel with this file. Every response goes through
 * `parseDemoSnapshot`, which tolerates anything and returns an empty
 * snapshot rather than throwing; a failed request keeps the last good
 * snapshot on screen and says quietly that it is retrying. A judge with a
 * flaky conference wifi sees a page that pauses, not one that dies.
 */

/** How often to ask while anything is still moving. */
const POLL_MS = 2000;

/** How often the live stopwatches redraw. Faster than the poll on purpose:
 * `elapsed` is derived from a timestamp, so it can tick smoothly between
 * two reads instead of jumping 2 seconds at a time. */
const TICK_MS = 1000;

/**
 * A wall-clock ceiling on polling, and the reason it exists.
 *
 * `isTerminalRun` deliberately treats an UNRECOGNISED state as non-terminal
 * — the harmless direction for a page whose worst failure is freezing
 * mid-run. But "harmless" stops being true if the API grows a terminal
 * state this build has never heard of: the page would then poll a finished
 * run forever, in a browser tab a judge left open. Fifteen minutes is far
 * longer than either venue's run and short enough that an abandoned tab
 * stops eventually.
 */
const POLL_CEILING_MS = 15 * 60 * 1000;

/** Where the machine this browser joined is remembered across a reload. */
const JOINED_KEY = "zolli.demo.joined";

/** The `job_id` a run POST handed back, or null if the body was not what we
 * expected. Null simply means the wait cannot be tracked by id — the next
 * read still picks the run up, one poll later. */
function jobIdOf(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const id = (body as Record<string, unknown>).job_id;
  return typeof id === "string" && id.length > 0 ? id : null;
}

export function DemoClient() {
  const [snapshot, setSnapshot] = useState<DemoSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [stale, setStale] = useState(false);
  const [starting, setStarting] = useState<DemoCoordinator | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  /** The job id a Run press returned, held until it shows up in a read.
   *
   * Without it the poll loop would stop between the POST and the first
   * snapshot — `anyRunLive` is false on a read taken before the run existed
   * — and the grid would sit still until someone reloaded. Tracked by ID
   * rather than as a bare "waiting" flag because a SECOND press is made
   * against a page that already has a run of that kind: see
   * `snapshotHasJob`. */
  const [awaitingJobId, setAwaitingJobId] = useState<string | null>(null);
  const startedAt = useRef<number>(Date.now());

  /** The machine THIS browser joined, if any. Held here rather than in
   * `JoinCard` because two other components need it: the guest fleet marks
   * the matching row "yours", and the guest run panel decides whether to say
   * "your machine ran this task" or the honest weaker version.
   *
   * Restored from sessionStorage on mount so a reload does not silently
   * demote a judge's own laptop to an anonymous row. sessionStorage rather
   * than localStorage: this is a claim about the tab a person is standing
   * in front of, not a durable identity, and it should not outlive the
   * visit. Read in an effect, never during render — `window` does not exist
   * on the server, and a value that differs between the two renders is a
   * hydration mismatch. */
  const [joined, setJoined] = useState<JoinedMachine | null>(null);
  const [startingMine, setStartingMine] = useState(false);
  const [mineError, setMineError] = useState<string | null>(null);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(JOINED_KEY);
      if (raw) setJoined(JSON.parse(raw) as JoinedMachine);
    } catch {
      // Storage can be unavailable (private mode, disabled cookies) or hold
      // something that is not JSON. Neither is worth breaking the page for:
      // the machine still appears in the guest list, just unhighlighted.
    }
  }, []);

  const rememberJoined = useCallback((machine: JoinedMachine) => {
    setJoined(machine);
    setMineError(null);
    try {
      sessionStorage.setItem(JOINED_KEY, JSON.stringify(machine));
    } catch {
      // Same as above — a failed write costs the highlight on reload and
      // nothing else.
    }
  }, []);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${cloudApiBase()}/v1alpha1/public/demo`, {
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const next = parseDemoSnapshot(await res.json());
      setSnapshot(next);
      setStale(false);
      setError(null);
      // Close the wait only once the run we started is actually visible.
      setAwaitingJobId((waiting) =>
        waiting !== null && snapshotHasJob(next, waiting) ? null : waiting
      );
    } catch {
      // The last good snapshot stays on screen. A page that blanks itself
      // on one dropped request is worse than one that is briefly behind,
      // and it says so rather than pretending the stale numbers are live.
      setStale(true);
    } finally {
      setLoading(false);
    }
  }, []);

  // First read.
  useEffect(() => {
    void load();
  }, [load]);

  const live = anyRunLive(snapshot) || awaitingJobId !== null;

  // The read loop. Runs only while something can still change, which is the
  // contract: stop polling once both runs are terminal.
  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => {
      if (Date.now() - startedAt.current > POLL_CEILING_MS) return;
      void load();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [live, load]);

  // The stopwatch tick, on the same condition — a finished page must not
  // keep re-rendering forever behind a tab nobody is looking at.
  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => setNow(Date.now()), TICK_MS);
    return () => clearInterval(id);
  }, [live]);

  const start = useCallback(
    async (coordinator: DemoCoordinator) => {
      setStarting(coordinator);
      setError(null);
      try {
        const res = await fetch(`${cloudApiBase()}/v1alpha1/public/demo/run`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ coordinator }),
        });
        // 201 is a new run; 200 means one was already live and the API
        // handed back the SAME job id. Both are success and both mean the
        // same thing here: start watching. Nothing re-presses on a 200.
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const body: unknown = await res.json().catch(() => null);
        startedAt.current = Date.now();
        setAwaitingJobId(jobIdOf(body));
        await load();
      } catch {
        setError(
          "Could not start that run. The network is not answering right now."
        );
      } finally {
        setStarting(null);
      }
    },
    [load]
  );

  /** "Run on my machine" — no venue knob, because the guest job always goes
   * to the default coordinator and the API ignores the body entirely. */
  const startMine = useCallback(async () => {
    setStartingMine(true);
    setMineError(null);
    try {
      const res = await fetch(`${cloudApiBase()}/v1alpha1/public/demo/run-mine`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const body: unknown = await res.json().catch(() => null);
      if (!res.ok) {
        // The 503 here is the one that tells a judge exactly what to do
        // next ("run `flashnode login` and paste the code above first"), so
        // it is printed as the API wrote it.
        setMineError(runMineErrorMessage(res.status, apiDetail(body)));
        return;
      }
      startedAt.current = Date.now();
      setAwaitingJobId(jobIdOf(body));
      await load();
    } catch {
      setMineError(
        "Could not reach the network to start that run. Check the connection and try again."
      );
    } finally {
      setStartingMine(false);
    }
  }, [load]);

  const fleet = snapshot?.fleet ?? [];
  const guests = snapshot?.guests ?? [];

  return (
    <div className="space-y-6">
      {/* ── The hardware ─────────────────────────────────────────── */}
      <section>
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h2 className="page-title">The network</h2>
          {stale && (
            // Honest, and quiet. The numbers on screen are real, they are
            // just not current, and saying which is the difference between
            // a page that is behind and a page that is lying.
            <span className="meta">reconnecting — showing the last read</span>
          )}
        </div>
        <div className="mt-3">
          <FleetStrip fleet={fleet} loading={loading} />
        </div>
      </section>

      {error && (
        <p className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-[13px] text-destructive">
          {error}
        </p>
      )}

      {/* ── The two control planes ───────────────────────────────── */}
      <div className="grid gap-4 lg:grid-cols-2">
        {DEMO_VENUES.map((venue) => (
          <RunPanel
            key={venue.coordinator}
            coordinator={venue.coordinator}
            title={venue.title}
            subtitle={venue.subtitle}
            fleet={fleet}
            run={runFor(snapshot, venue.coordinator)}
            now={now}
            starting={starting === venue.coordinator}
            disabled={fleet.length === 0 || starting !== null}
            onRun={() => void start(venue.coordinator)}
          />
        ))}
      </div>

      {/* ── The comparison, once it is real ──────────────────────── */}
      <SpeedComparison snapshot={snapshot} />

      {/* ── The judge's own machine ──────────────────────────────────
          Deliberately LAST. Everything above is watchable in ten seconds
          with no commitment; this asks for a terminal and two minutes, and
          putting it first would make the page look like a setup guide
          rather than a live network. */}
      <section className="border-t border-border pt-6">
        <h2 className="page-title">Host your own machine</h2>
        <p className="mt-1.5 max-w-2xl text-[13px] leading-relaxed text-muted-foreground">
          The four machines above are ours. You can add yours to the same
          network in about two minutes, with no account — then run a task on
          it from this page.
        </p>

        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <JoinCard joined={joined} onJoined={rememberJoined} />

          <div className="space-y-4">
            <div>
              <p className="label-caps">Guest machines</p>
              <div className="mt-2">
                <GuestFleet guests={guests} joined={joined} />
              </div>
            </div>
            <GuestRunPanel
              guests={guests}
              run={snapshot?.guest_run ?? null}
              joined={joined}
              now={now}
              starting={startingMine}
              error={mineError}
              onRun={() => void startMine()}
            />
          </div>
        </div>
      </section>
    </div>
  );
}
