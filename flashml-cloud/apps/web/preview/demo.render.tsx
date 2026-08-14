import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
import { expect, it } from "vitest";

import { FleetStrip } from "@/components/demo/FleetStrip";
import { GuestFleet } from "@/components/demo/GuestFleet";
import { GuestRunPanel } from "@/components/demo/GuestRunPanel";
import { JoinCard, JoinError, JoinSuccess } from "@/components/demo/JoinCard";
import { RunPanel } from "@/components/demo/RunPanel";
import { SpeedComparison } from "@/components/demo/SpeedComparison";
import { TaskGrid } from "@/components/demo/TaskGrid";
import {
  DEMO_VENUES,
  busyMachineCount,
  joinErrorMessage,
  machineLanes,
  parseDemoSnapshot,
  runFor,
  type DemoMachine,
  type DemoRun,
  type DemoSnapshot,
  type DemoTask,
  type JoinedMachine,
} from "@/lib/demo";

/**
 * The public `/demo` page, rendered so it can be LOOKED AT without a session.
 *
 *   PREVIEW_OUT=.preview npx vitest run --config preview/vitest.preview.config.ts
 *
 * `/demo` is public, but that does not mean an agent can see it: nothing here
 * can sign in, and more to the point the page is driven entirely by an API
 * that is being built in parallel with it — so even a running dev server
 * would show four skeletons and an empty state. This harness supplies the
 * payloads that API will eventually send and renders the page's own
 * components against them, which is the only way the centrepiece gets looked
 * at before a judge looks at it.
 *
 * Named `*.render.tsx` so `vitest.config.ts`'s `**\/*.test.*` glob cannot
 * collect it and inflate the suite baseline. The judgements are tested in
 * `lib/demo.test.ts`; the assertions here are the ones only rendered HTML
 * can make — that four lanes are actually drawn, that four of them carry the
 * live halo at once, and that a run still in flight prints no comparison.
 *
 * SIX PANELS, IN THE ORDER A JUDGE MEETS THEM:
 *   1. the fleet, before anything has been pressed
 *   2. both venues idle — the page as it opens
 *   3. THE MONEY SHOT: mid-flight, all four machines executing at once
 *   4. the same grid alone and large, so the parallelism read can be checked
 *   5. both runs finished, with artifacts and the head-to-head
 *   6. the degraded states — no fleet, and a payload full of holes
 */
const FONT_VARS = `:root{--font-instrument-sans:ui-sans-serif,system-ui,-apple-system,sans-serif;--font-geist-mono:ui-monospace,SFMono-Regular,Menlo,monospace}`;

const webRoot = process.cwd();
const outDir = process.env.PREVIEW_OUT ?? path.join(webRoot, ".preview");

async function compiledCss(): Promise<string> {
  const globalsPath = path.join(webRoot, "app/globals.css");
  const css = readFileSync(globalsPath, "utf8");
  const result = await postcss([tailwindcss({ base: webRoot })]).process(css, {
    from: globalsPath,
  });
  return result.css;
}

/** The real anchor fleet: four Alibaba ECS boxes in Singapore, all operated
 * by Zolli Labs. Shapes match what the API reports for them. */
const FLEET: DemoMachine[] = [1, 2, 3, 4].map((n) => ({
  name: `alibaba-sgp-${n}`,
  online: true,
  region: "SG",
  cpus: 2,
  memory_gb: 8,
  official: true,
}));

const T0 = Date.parse("2026-08-14T10:00:00Z");
const iso = (offsetSeconds: number) => new Date(T0 + offsetSeconds * 1000).toISOString();

function task(
  id: number,
  machine: string | null,
  state: string,
  started: number | null = null,
  finished: number | null = null
): DemoTask {
  return {
    task_id: `trial-${String(id).padStart(3, "0")}`,
    state,
    machine,
    started_at: started === null ? null : iso(started),
    finished_at: finished === null ? null : iso(finished),
    outcome: state === "COMPLETED" ? "accepted" : null,
  };
}

/**
 * MID-FLIGHT, AND THE WHOLE POINT OF THE PAGE.
 *
 * Nine tasks over four machines, arranged so every one of the four is
 * EXECUTING at this instant — three already finished, four running, two still
 * queued. That is the state a judge is meant to catch: four columns, four
 * orange tiles, four live haloes, and the counter reading "4 of 4".
 */
const MID_TASKS: DemoTask[] = [
  task(0, "alibaba-sgp-1", "COMPLETED", 4, 96),
  task(4, "alibaba-sgp-1", "RUNNING", 98),
  task(8, "alibaba-sgp-1", "PENDING"),
  task(1, "alibaba-sgp-2", "COMPLETED", 5, 88),
  task(5, "alibaba-sgp-2", "RUNNING", 90),
  task(2, "alibaba-sgp-3", "RUNNING", 6),
  task(6, "alibaba-sgp-3", "PENDING"),
  task(3, "alibaba-sgp-4", "COMPLETED", 5, 101),
  task(7, "alibaba-sgp-4", "RUNNING", 103),
];

const MID_RUN: DemoRun = {
  job_id: "job-7f2a91c4",
  coordinator: "render",
  state: "RUNNING",
  created_at: iso(0),
  finished_at: null,
  elapsed_s: null, // derived from created_at, so the stopwatch ticks live
  tasks: MID_TASKS,
  artifacts: [],
};

/** The other venue, seconds after its own button was pressed: the work
 * exists but nothing has claimed it yet, so it sits in the "Awaiting
 * placement" lane. This is the state that proves an unplaced task is still
 * counted rather than silently dropped. */
const COLD_START_RUN: DemoRun = {
  job_id: "job-b0d43e11",
  coordinator: "fc",
  state: "RUNNING",
  created_at: iso(96),
  finished_at: null,
  elapsed_s: null,
  tasks: Array.from({ length: 9 }, (_, i) => task(i, null, "PENDING")),
  artifacts: [],
};

const NOW_MID = T0 + 108 * 1000;

function finished(
  coordinator: string,
  jobId: string,
  elapsed: number,
  artifactBytes: number
): DemoRun {
  return {
    job_id: jobId,
    coordinator,
    state: "SUCCEEDED",
    created_at: iso(0),
    finished_at: iso(elapsed),
    elapsed_s: elapsed,
    tasks: MID_TASKS.map((t, i) =>
      task(Number(t.task_id.slice(-3)), FLEET[i % 4].name, "COMPLETED", 5, elapsed - 10)
    ),
    artifacts: [
      { name: "model.pt", bytes: artifactBytes },
      { name: "metrics.json", bytes: 4210 },
    ],
  };
}

const DONE_SNAPSHOT: DemoSnapshot = {
  fleet: FLEET,
  runs: [
    finished("render", "job-7f2a91c4", 612, 47_982_336),
    finished("fc", "job-b0d43e11", 387, 47_982_336),
  ],
  guests: [],
  guest_run: null,
};

// ── the judge's own machine ────────────────────────────────────────────────

/** Two guests: the visitor's own laptop and somebody else's. Names are the
 * `prov…` handles the public list carries — a guest machine is not
 * `official`, so the API anonymises it and this page never sees a hostname
 * for anyone but the visitor who joined it. */
const GUESTS: DemoMachine[] = [
  {
    name: "prov-8f21c4",
    online: true,
    region: "—",
    cpus: 10,
    memory_gb: 16,
    official: false,
  },
  {
    name: "prov-2ad907",
    online: true,
    region: "—",
    cpus: 4,
    memory_gb: 8,
    official: false,
  },
];

/** What the join route handed THIS visitor back: the real hostname (an echo
 * of a machine they proved possession of) plus the handle everyone else
 * sees. `label` is what matches a row in `GUESTS`. */
const JOINED: JoinedMachine = {
  name: "phongs-macbook-pro",
  node_id: "nd-4c1e88b2",
  label: "prov-8f21c4",
};

function guestRun(state: string, elapsed: number, taskState: string): DemoRun {
  return {
    job_id: "job-guest-51ac",
    coordinator: "render",
    state,
    created_at: iso(0),
    finished_at: state === "SUCCEEDED" ? iso(elapsed) : null,
    elapsed_s: elapsed,
    tasks: [
      {
        task_id: "task-000",
        state: taskState,
        machine: JOINED.label,
        started_at: iso(1),
        finished_at: taskState === "COMPLETED" ? iso(elapsed) : null,
        outcome: taskState === "COMPLETED" ? "accepted" : null,
      },
    ],
    artifacts: state === "SUCCEEDED" ? [{ name: "hello.txt", bytes: 61 }] : [],
  };
}

/** What the page shows against an API that answered with holes in it — a
 * machine missing its specs, a run missing its timing, a task in a state
 * this build has never heard of. Nothing here may render as a confident
 * zero, and nothing may throw. */
const RAGGED: DemoSnapshot = parseDemoSnapshot({
  fleet: [
    { name: "alibaba-sgp-1", online: true, region: "SG" },
    { name: "alibaba-sgp-2", online: false, region: "SG", cpus: 2, memory_gb: 8 },
    { online: true, region: "SG" }, // no name: dropped entirely
  ],
  runs: [
    {
      job_id: "job-ragged",
      coordinator: "render",
      state: "PROPAGATING", // a state this build predates
      tasks: [
        { task_id: "trial-000", state: "MYSTERY", machine: "alibaba-sgp-1", started_at: iso(3) },
        { task_id: "trial-001", state: "MYSTERY", machine: "ghost-node-9", started_at: iso(3), finished_at: iso(40) },
        { task_id: "trial-002" },
        { state: "RUNNING" }, // no id: dropped
      ],
      artifacts: [{ name: "partial.bin" }],
    },
  ],
});

const noop = () => {};

function Section({ title, note, children }: { title: string; note: string; children: React.ReactNode }) {
  return (
    <section>
      <p className="label-caps">{title}</p>
      <p className="mt-1 max-w-3xl text-[12.5px] leading-relaxed text-muted-foreground">
        {note}
      </p>
      <div className="mt-3">{children}</div>
    </section>
  );
}

function Panels({
  snapshot,
  now,
  disabled = false,
}: {
  snapshot: DemoSnapshot;
  now: number;
  disabled?: boolean;
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {DEMO_VENUES.map((venue) => (
        <RunPanel
          key={venue.coordinator}
          coordinator={venue.coordinator}
          title={venue.title}
          subtitle={venue.subtitle}
          fleet={snapshot.fleet}
          run={runFor(snapshot, venue.coordinator)}
          now={now}
          starting={false}
          disabled={disabled}
          onRun={noop}
        />
      ))}
    </div>
  );
}

function Gallery() {
  const idle: DemoSnapshot = {
    fleet: FLEET,
    runs: [],
    guests: [],
    guest_run: null,
  };
  const mid: DemoSnapshot = {
    fleet: FLEET,
    runs: [MID_RUN, COLD_START_RUN],
    guests: [],
    guest_run: null,
  };

  return (
    <div className="bg-background text-foreground" style={{ minHeight: "100vh" }}>
      <div className="mx-auto max-w-6xl space-y-12 px-4 py-8 sm:px-6">
        {/* The page's own header, so the harness shows the first thing a
            judge actually reads, at the size they read it. */}
        <header>
          <h1 className="title">Nine tasks, four machines, twice over</h1>
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-muted-foreground">
            Zolli Cloud spreads one training job across machines that can
            disappear mid-run. Below is the live network — press Run and watch
            nine tasks distribute across it. The same work is driven twice, by
            two different control planes, so the two can be timed against each
            other.
          </p>
        </header>

        <Section
          title="1 — The fleet, before anything is pressed"
          note="Four real ECS boxes in Singapore. Reads as hardware, not as a query result: icon, name, region, live dot, and the instance shape on a hairline-separated footer."
        >
          <FleetStrip fleet={FLEET} loading={false} />
        </Section>

        <Section
          title="2 — Both venues idle: the page as it opens"
          note="Identical structure on both sides, so any difference a judge sees later is a difference in the result and not in how the two were drawn. Four lanes are already drawn while empty — you cannot see four machines light up together unless four columns exist to light up."
        >
          <Panels snapshot={idle} now={T0} />
        </Section>

        <Section
          title="3 — MID-FLIGHT: all four machines executing at once"
          note="The state the page exists to show. Left: nine tasks spread over four machines — three done (green), four running (orange), two queued (dashed). Every one of the four lanes carries a running tile, an orange edge and a live halo, and the counter says 4 of 4 in words. Right: the second venue seconds after its own press, work created but not yet placed."
        >
          <Panels snapshot={mid} now={NOW_MID} />
        </Section>

        <Section
          title="4 — The same grid alone, so the parallelism read can be checked"
          note="Four columns, one per machine. Orange = running now, green = finished, dashed = queued. The claim is meant to survive a glance from across a room and a screenshot in a slide."
        >
          <div className="panel p-4">
            <TaskGrid fleet={FLEET} run={MID_RUN} />
          </div>
        </Section>

        <Section
          title="5 — Both runs finished: artifacts and the head-to-head"
          note="Artifacts appear per run with sizes once produced. The comparison block renders only now — both runs terminal, both timed — because a comparison drawn against a run still in flight is not an early result, it is a wrong one."
        >
          <div className="space-y-4">
            <Panels snapshot={DONE_SNAPSHOT} now={T0 + 700_000} />
            <SpeedComparison snapshot={DONE_SNAPSHOT} />
          </div>
        </Section>

        <Section
          title="6 — Degraded: no fleet, and a payload full of holes"
          note="The API is being written in parallel with this page. A missing spec prints an em dash and never a confident zero; a task in an unknown state falls back to its timestamps; a machine the fleet list forgot still keeps its tasks in the grid; an entry with no id is dropped rather than drawn nameless."
        >
          <div className="space-y-4">
            <FleetStrip fleet={[]} loading={false} />
            <FleetStrip fleet={[]} loading={true} />
            <FleetStrip fleet={RAGGED.fleet} loading={false} />
            <div className="panel p-4">
              <TaskGrid fleet={RAGGED.fleet} run={RAGGED.runs[0]} />
            </div>
          </div>
        </Section>

        {/* ── the judge's own machine ─────────────────────────────────── */}

        <Section
          title="7 — Host your own machine: before anyone has joined"
          note="Four steps and a text box, no account anywhere. The commands are the console's own (components/machines/EnrolInstructions) — but its closing line sends the reader to a console page behind the sign-in this visitor does not have, which is an instruction they cannot follow, so the copy here points at the box below instead. The guest list says what will appear rather than rendering an empty row."
        >
          <div className="grid gap-4 lg:grid-cols-2">
            <JoinCard joined={null} onJoined={noop} />
            <div className="space-y-4">
              <div>
                <p className="label-caps">Guest machines</p>
                <div className="mt-2">
                  <GuestFleet guests={[]} joined={null} />
                </div>
              </div>
              <GuestRunPanel
                guests={[]}
                run={null}
                joined={null}
                now={T0}
                starting={false}
                error={null}
                onRun={noop}
              />
            </div>
          </div>
        </Section>

        <Section
          title="8 — Joined: their laptop is on the network"
          note="The success line shows the REAL hostname — an echo of a machine they proved possession of — and states plainly that everyone else sees only the handle. In the guest list their row carries the orange edge and 'your machine'; the other visitor's row does not, and is not named."
        >
          <div className="grid gap-4 lg:grid-cols-2">
            <JoinCard joined={JOINED} onJoined={noop} />
            <div className="space-y-4">
              <div>
                <p className="label-caps">Guest machines</p>
                <div className="mt-2">
                  <GuestFleet guests={GUESTS} joined={JOINED} />
                </div>
              </div>
              <GuestRunPanel
                guests={GUESTS}
                run={null}
                joined={JOINED}
                now={T0}
                starting={false}
                error={null}
                onRun={noop}
              />
            </div>
          </div>
        </Section>

        <Section
          title="9 — Their task, in flight and then done"
          note="One task, seconds long, scoped so it can only land on a machine a visitor joined. Left: mid-flight, the tile orange in their own lane. Right: finished — and the completion line says 'Your machine ran this task', which is checked against the handle rather than assumed, because another visitor's laptop may well have claimed it."
        >
          <div className="grid gap-4 lg:grid-cols-2">
            <GuestRunPanel
              guests={GUESTS}
              run={guestRun("RUNNING", 4, "LEASED")}
              joined={JOINED}
              now={T0 + 4200}
              starting={false}
              error={null}
              onRun={noop}
            />
            <GuestRunPanel
              guests={GUESTS}
              run={guestRun("SUCCEEDED", 9, "COMPLETED")}
              joined={JOINED}
              now={T0 + 200_000}
              starting={false}
              error={null}
              onRun={noop}
            />
          </div>
        </Section>

        <Section
          title="10 — The four refusals, in the API's own words"
          note="Every one of these is a sentence a judge can act on without asking anybody. They are printed verbatim from the route's `detail` rather than mapped to our own copy: a status-code lookup here would replace four good messages with four worse ones, and would silently mistranslate any fifth the API grows later."
        >
          <div className="panel space-y-2 p-4">
            {[
              [404, "unknown code"],
              [404, "that code has expired — run `flashnode login` again"],
              [
                400,
                "that is a CLI login code — the demo joins a MACHINE, so run `flashnode login` and paste the code it prints",
              ],
              [409, "this machine is already enrolled"],
              [
                503,
                "no guest machine has joined yet — run `flashnode login` and paste the code above first",
              ],
            ].map(([status, detail]) => (
              <JoinError
                key={String(detail)}
                message={joinErrorMessage(status as number, detail as string)}
              />
            ))}
            {/* And the one case with no body to read at all — a proxy 502, a
                dropped connection. Vaguer on purpose: the page genuinely
                does not know which refusal applies. */}
            <JoinError message={joinErrorMessage(404, null)} />
            <div className="pt-1">
              <JoinSuccess machine={JOINED} />
            </div>
          </div>
        </Section>
      </div>
    </div>
  );
}

it("writes the public demo preview", async () => {
  const css = await compiledCss();
  const body = renderToStaticMarkup(<Gallery />);

  // ── The centrepiece: parallelism must be VISIBLE, not merely true ──────
  const midLanes = machineLanes(FLEET, MID_RUN);
  expect(busyMachineCount(midLanes)).toBe(4);
  // Every one of the nine tasks is drawn — none silently dropped by the
  // grouping, which is the failure that would turn 9 tasks into 7 tiles.
  expect(midLanes.reduce((n, l) => n + l.tasks.length, 0)).toBe(9);

  // Said in words, for the reader who is being talked at rather than looking.
  expect(body).toContain("machines working right now");
  expect(body).toContain("9 tasks · 3 done · 4 running · 2 queued");

  // Four lanes drawn even while idle. `alibaba-sgp-4` appears in the fleet
  // strip and in every grid on the page; the point is that it is never
  // absent just because it holds nothing.
  for (const m of FLEET) expect(body).toContain(m.name);

  // The live halo is the console's ONE reserved "genuinely live work"
  // treatment, and FOUR of them pulse at once in the mid-flight grid. Two
  // grids on this page are mid-flight (the panel in section 3 and the
  // standalone in section 4), which is eight; the ninth is in the ragged
  // grid of section 6, where a task in a state this build has never heard of
  // still animates as running because `taskPhase` falls back to its
  // timestamps. That ninth one is the fallback working, not a stray — a
  // grid that left an unknown-state task sitting grey through a whole run is
  // exactly what the fallback exists to prevent.
  //
  // The count is asserted rather than merely being non-zero because the
  // failure this guards against is a halo per machine LISTED instead of per
  // machine WORKING: four idle lanes pulsing would turn the page's central
  // claim into decoration.
  // The tenth is the guest run mid-flight in section 9, where the visitor's
  // own laptop is holding its one task — the same treatment, earned the same
  // way, on hardware they plugged in themselves.
  const haloes = body.split('data-state="live"').length - 1;
  expect(haloes).toBe(10);
  // The idle panels of section 2 contribute none of them.
  expect(busyMachineCount(machineLanes(FLEET, null))).toBe(0);

  // Colour is never the only carrier: every tile names its task and state in
  // a title attribute, and the legend spells the three fills out in words.
  expect(body).toContain("Running");
  expect(body).toContain("Queued");
  expect(body).toContain("trial-004 — RUNNING");
  expect(body).toContain("trial-000 — COMPLETED (accepted)");

  // ── The two venues stay distinguishable by TEXT and SHAPE, not tint ────
  expect(body).toContain("Function Compute");
  expect(body).toContain("Render");
  expect(body).toContain("Always-on");
  expect(body).toContain("Serverless");

  // ── The stopwatch: derived live, and formatted for a person ────────────
  // MID_RUN started at T0 and NOW_MID is 108s later: 1m 48s, not "108s".
  expect(body).toContain("1m 48s");
  // The finished pair, from the API's own elapsed_s.
  expect(body).toContain("10m 12s"); // render, 612s
  expect(body).toContain("6m 27s"); // fc, 387s

  // ── The comparison exists exactly once, and only for the finished pair ─
  expect(body).toContain("Same nine tasks, same four machines");
  expect(body.split("Same nine tasks").length - 1).toBe(1);
  expect(body).toContain("sooner");
  expect(body).toContain("1.6×");

  // ── Artifacts, per run, with sizes ─────────────────────────────────────
  expect(body).toContain("Artifacts produced");
  expect(body).toContain("model.pt");
  // `formatBytes` drops the decimal at 10 units and above — a judge
  // comparing two runs cares that they produced the same artifact, not that
  // it was 45.76 rather than 45.8 MB.
  expect(body).toContain("46 MB");
  expect(body).toContain("metrics.json");
  expect(body).toContain("4.1 KB");

  // ── Degradation: em dashes, never confident zeros ──────────────────────
  // A machine whose specs the API did not send must not read "0 vCPU".
  //
  // The zero must not be preceded by a digit: a guest laptop reporting
  // `10 vCPU` contains the substring "0 vCPU", and a bare `toContain` check
  // here failed on exactly that — the assertion was wrong, not the page.
  expect(body).not.toMatch(/(?<!\d)0 vCPU/);
  expect(body).not.toMatch(/(?<!\d)0 GB/);
  expect(body).not.toContain("NaN");
  expect(body).not.toContain("Infinity");
  expect(body).not.toContain("undefined");
  // `new Date(null)` is the epoch; a confident 1970 is the symptom.
  expect(body).not.toContain("1970");
  // A task naming a machine the fleet list forgot keeps its lane.
  expect(body).toContain("ghost-node-9");
  // Unplaced work is parked in a named waiting room, not vanished — and the
  // name fits its lane rather than truncating.
  expect(body).toContain("Unplaced");
  expect(body).not.toContain("Awaiting place");
  // The empty fleet says what is wrong instead of rendering a blank strip.
  expect(body).toContain("No machines reported");
  // A loading fleet uses the console's skeleton language, not a spinner.
  expect(body).toContain("skeleton");

  // ── The page never invites a press it cannot honour ────────────────────
  // The button reads its own state: 9 tasks to start, "Running…" while live.
  expect(body).toContain("Run 9 tasks");
  expect(body).toContain("Running…");
  expect(body).toContain("Run again");

  // ── The judge's own machine ────────────────────────────────────────────
  // The four steps, ending in a box that needs no account. The console's own
  // enrol copy sends the reader to /activate in a signed-in browser; this
  // page must not, because that is precisely the door its visitor lacks.
  expect(body).toContain("python3 -m venv flashml");
  expect(body).toContain("pip install flashnode");
  expect(body).toContain("flashnode login --coordinator");
  expect(body).toContain("Paste the code it printed");
  expect(body).toContain("Join the network");
  expect(body).not.toContain("/activate");
  expect(body).not.toContain("signed-in browser");

  // The real hostname is shown ONLY to the visitor who proved possession of
  // the machine, and the page says out loud that everyone else sees the
  // handle. The public guest list must never carry the hostname.
  expect(body).toContain("phongs-macbook-pro");
  expect(body).toContain("shown to everyone else as prov-8f21c4");
  // Their row is marked theirs; the other visitor's is not, and is unnamed.
  expect(body).toContain("your machine");
  expect(body).toContain("a visitor&#x27;s machine");
  expect(body).toContain("prov-2ad907");

  // The empty guest state says what will appear rather than drawing a blank.
  expect(body).toContain("No guest machines yet");
  // …and the run button explains why it is inert instead of failing on press.
  expect(body).toContain("join a machine above to enable this");

  // The moment: their laptop did work on somebody else's network. Checked
  // against the handle, not assumed — hence the wording.
  expect(body).toContain("Your machine");
  expect(body).toContain("ran this task and returned the result in");
  expect(body).toContain("Run on my machine");

  // All four refusals, verbatim from the route's own `detail`.
  for (const sentence of [
    "unknown code",
    "that code has expired — run `flashnode login` again",
    "that is a CLI login code",
    "this machine is already enrolled",
    "no guest machine has joined yet",
  ]) {
    expect(body).toContain(sentence);
  }
  // Errors are announced, not merely coloured.
  expect(body).toContain('role="alert"');

  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Public demo preview</title><style>${FONT_VARS}</style><style>${css}</style></head>
<body>${body}</body></html>`;

  mkdirSync(outDir, { recursive: true });
  const out = path.join(outDir, "demo.html");
  writeFileSync(out, html, "utf8");
  console.log(`preview written: ${out}`);
}, 60_000);
