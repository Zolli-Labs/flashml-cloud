import { describe, expect, it } from "vitest";

import {
  anyRunLive,
  apiDetail,
  busyMachineCount,
  compareSpeed,
  formatBytes,
  formatElapsed,
  isMyGuest,
  isTerminalRun,
  joinErrorMessage,
  machineLanes,
  normaliseUserCode,
  parseDemoSnapshot,
  parseJoinedMachine,
  runElapsedSeconds,
  runFor,
  runMineErrorMessage,
  tallyTasks,
  taskPhase,
  UNPLACED_LANE,
  userCodeReady,
  type DemoMachine,
  type DemoRun,
  type DemoSnapshot,
  type DemoTask,
  type JoinedMachine,
} from "./demo";

const FLEET: DemoMachine[] = ["1", "2", "3", "4"].map((n) => ({
  name: `alibaba-sgp-${n}`,
  online: true,
  region: "SG",
  cpus: 2,
  memory_gb: 8,
  official: true,
}));

function task(over: Partial<DemoTask> & { task_id: string }): DemoTask {
  return {
    state: "PENDING",
    machine: null,
    started_at: null,
    finished_at: null,
    outcome: null,
    ...over,
  };
}

function run(over: Partial<DemoRun> = {}): DemoRun {
  return {
    job_id: "job-1",
    coordinator: "render",
    state: "RUNNING",
    created_at: "2026-08-14T10:00:00Z",
    finished_at: null,
    elapsed_s: null,
    tasks: [],
    artifacts: [],
    ...over,
  };
}

describe("parseDemoSnapshot", () => {
  it("reads the documented payload", () => {
    const snap = parseDemoSnapshot({
      fleet: [
        {
          name: "alibaba-sgp-1",
          online: true,
          region: "SG",
          cpus: 2,
          memory_gb: 8,
          official: true,
        },
      ],
      runs: [
        {
          job_id: "job-abc",
          coordinator: "fc",
          state: "SUCCEEDED",
          created_at: "2026-08-14T10:00:00Z",
          finished_at: "2026-08-14T10:06:27Z",
          elapsed_s: 387,
          tasks: [
            {
              task_id: "trial-000",
              state: "COMPLETED",
              machine: "alibaba-sgp-2",
              started_at: "2026-08-14T10:00:05Z",
              finished_at: "2026-08-14T10:03:00Z",
              outcome: "accepted",
            },
          ],
          artifacts: [{ name: "model.pt", bytes: 1234 }],
        },
      ],
    });

    expect(snap.fleet).toEqual([
      {
        name: "alibaba-sgp-1",
        online: true,
        region: "SG",
        cpus: 2,
        memory_gb: 8,
        official: true,
      },
    ]);
    expect(snap.runs[0].elapsed_s).toBe(387);
    expect(snap.runs[0].tasks[0].machine).toBe("alibaba-sgp-2");
    expect(snap.runs[0].artifacts[0]).toEqual({ name: "model.pt", bytes: 1234 });
  });

  it("treats an empty runs array as a page at rest, not an error", () => {
    const snap = parseDemoSnapshot({ fleet: [], runs: [], guests: [], guest_run: null });
    expect(snap).toEqual({ fleet: [], runs: [], guests: [], guest_run: null });
    expect(anyRunLive(snap)).toBe(false);
  });

  // This page is opened by a judge, once, with no second chance, against an
  // API that was written in parallel with it. Nothing below may throw.
  it.each([
    ["null", null],
    ["undefined", undefined],
    ["a string", "nope"],
    ["a number", 7],
    ["an array", [1, 2]],
    ["an empty object", {}],
    ["runs that is not an array", { fleet: null, runs: "soon" }],
  ])("survives %s", (_label, body) => {
    expect(() => parseDemoSnapshot(body)).not.toThrow();
    expect(parseDemoSnapshot(body)).toEqual({
      fleet: [],
      runs: [],
      guests: [],
      guest_run: null,
    });
  });

  it("drops entries with no identity rather than rendering a nameless tile", () => {
    // A tile with no id invites a reader to count it, and the count is the
    // whole claim. Absent beats anonymous.
    const snap = parseDemoSnapshot({
      fleet: [{ online: true }, { name: "alibaba-sgp-1" }],
      runs: [
        { coordinator: "fc", state: "RUNNING" },
        {
          job_id: "job-1",
          tasks: [{ state: "RUNNING" }, { task_id: "trial-000" }],
          artifacts: [{ bytes: 10 }, { name: "model.pt" }],
        },
      ],
    });
    expect(snap.fleet).toHaveLength(1);
    expect(snap.runs).toHaveLength(1);
    expect(snap.runs[0].tasks).toHaveLength(1);
    expect(snap.runs[0].artifacts).toEqual([{ name: "model.pt", bytes: null }]);
  });

  it("defaults missing scalars to null and never to a confident zero", () => {
    const snap = parseDemoSnapshot({
      fleet: [{ name: "m1" }],
      runs: [{ job_id: "job-1" }],
    });
    expect(snap.fleet[0]).toMatchObject({
      online: false,
      region: "—",
      cpus: null,
      memory_gb: null,
      official: false,
    });
    expect(snap.runs[0]).toMatchObject({
      coordinator: "",
      created_at: null,
      elapsed_s: null,
      tasks: [],
      artifacts: [],
    });
  });

  it("does not default an absent coordinator to render", () => {
    // Unlike a job record elsewhere in the console, this page FILES runs by
    // this string into one of two panels. Guessing would show a run under a
    // venue that did not produce it.
    const snap = parseDemoSnapshot({ runs: [{ job_id: "job-1" }] });
    expect(runFor(snap, "render")).toBeNull();
    expect(runFor(snap, "fc")).toBeNull();
  });
});

describe("isTerminalRun", () => {
  it.each(["SUCCEEDED", "FAILED", "CANCELLED", "PARTIAL"])(
    "%s is terminal",
    (state) => {
      expect(isTerminalRun(state)).toBe(true);
    }
  );

  // PARTIAL is not in the demo route's documented state list, but the route
  // returns the job row's `status` verbatim and that column carries it. A
  // PARTIAL run is FINISHED — treating it as live polls a completed run to
  // the ceiling and, because `compareSpeed` needs both runs terminal,
  // withholds the page's headline number for good.
  it("does not withhold the comparison from a run that finished PARTIAL", () => {
    const result = compareSpeed({
      fleet: FLEET,
      guests: [],
      guest_run: null,
      runs: [
        run({ coordinator: "render", state: "PARTIAL", elapsed_s: 400 }),
        run({ coordinator: "fc", state: "SUCCEEDED", elapsed_s: 200 }),
      ],
    });
    expect(result).not.toBeNull();
    expect(result?.faster).toBe("fc");
  });

  // A job re-placing work it lost is still going — and that is the story
  // this product exists to tell, not a state to call finished.
  it.each(["RUNNING", "RECOVERING", "PENDING", "", null, undefined, "SOMETHING_NEW"])(
    "%s keeps the page asking",
    (state) => {
      // The harmless direction: two extra requests, versus a grid frozen
      // mid-flight that reads as a stalled network.
      expect(isTerminalRun(state)).toBe(false);
    }
  );
});

describe("taskPhase", () => {
  it.each([
    ["PENDING", "pending"],
    ["LEASED", "running"],
    ["RUNNING", "running"],
    ["COMPLETED", "done"],
    ["SUCCEEDED", "done"],
    ["FAILED", "failed"],
    ["CANCELLED", "cancelled"],
  ] as const)("maps %s to %s", (state, phase) => {
    expect(taskPhase(task({ task_id: "t", state }))).toBe(phase);
  });

  it("falls back to the timestamps for a state this build has never heard of", () => {
    const unknown = "IN_FLIGHT_SOMEHOW";
    expect(
      taskPhase(task({ task_id: "t", state: unknown, started_at: "x", finished_at: "y" }))
    ).toBe("done");
    expect(taskPhase(task({ task_id: "t", state: unknown, started_at: "x" }))).toBe(
      "running"
    );
    expect(taskPhase(task({ task_id: "t", state: unknown }))).toBe("pending");
  });
});

describe("machineLanes", () => {
  it("draws a lane per fleet machine even before anything is placed", () => {
    // Four columns is the claim. You cannot see four columns light up
    // together unless four columns are drawn.
    const lanes = machineLanes(FLEET, null);
    expect(lanes.map((l) => l.machine)).toEqual([
      "alibaba-sgp-1",
      "alibaba-sgp-2",
      "alibaba-sgp-3",
      "alibaba-sgp-4",
    ]);
    expect(lanes.every((l) => l.tasks.length === 0)).toBe(true);
  });

  it("spreads nine tasks across the four machines and counts each lane", () => {
    const tasks = Array.from({ length: 9 }, (_, i) =>
      task({
        task_id: `trial-${String(i).padStart(3, "0")}`,
        state: i < 4 ? "COMPLETED" : i < 8 ? "RUNNING" : "PENDING",
        machine: FLEET[i % 4].name,
      })
    );
    const lanes = machineLanes(FLEET, run({ tasks }));

    expect(lanes.reduce((n, l) => n + l.tasks.length, 0)).toBe(9);
    // Every machine holds work, and each one is executing some of it.
    expect(lanes.map((l) => l.tasks.length)).toEqual([3, 2, 2, 2]);
    expect(busyMachineCount(lanes)).toBe(4);
  });

  it("counts only machines actually executing as busy", () => {
    const lanes = machineLanes(
      FLEET,
      run({
        tasks: [
          task({ task_id: "a", state: "RUNNING", machine: "alibaba-sgp-1" }),
          task({ task_id: "b", state: "COMPLETED", machine: "alibaba-sgp-2" }),
          task({ task_id: "c", state: "PENDING", machine: "alibaba-sgp-3" }),
        ],
      })
    );
    expect(busyMachineCount(lanes)).toBe(1);
  });

  it("parks unclaimed work in its own lane, last, and only when it exists", () => {
    expect(machineLanes(FLEET, run({ tasks: [] })).some((l) => l.machine === null)).toBe(
      false
    );
    const lanes = machineLanes(
      FLEET,
      run({ tasks: [task({ task_id: "a" }), task({ task_id: "b" })] })
    );
    expect(lanes).toHaveLength(5);
    expect(lanes[4].machine).toBeNull();
    expect(lanes[4].tasks).toHaveLength(2);
    // A waiting room is not hardware; it must not sit between two machines.
    expect(lanes.slice(0, 4).every((l) => l.machine !== null)).toBe(true);
  });

  it("keeps a task whose machine is not in the fleet, rather than losing it", () => {
    // The fleet and the run are two different reads. A machine that dropped
    // offline between them would otherwise make nine tasks render as eight.
    const lanes = machineLanes(
      FLEET,
      run({ tasks: [task({ task_id: "a", state: "RUNNING", machine: "ghost-9" })] })
    );
    expect(lanes.map((l) => l.machine)).toContain("ghost-9");
    expect(lanes.reduce((n, l) => n + l.tasks.length, 0)).toBe(1);
  });

  it("preserves the API's task order inside a lane", () => {
    // So a tile does not jump position between two polls just because a
    // neighbour finished.
    const lanes = machineLanes(
      FLEET,
      run({
        tasks: [
          task({ task_id: "trial-002", machine: "alibaba-sgp-1" }),
          task({ task_id: "trial-000", machine: "alibaba-sgp-1" }),
        ],
      })
    );
    expect(lanes[0].tasks.map((t) => t.task_id)).toEqual(["trial-002", "trial-000"]);
  });

  it("carries the machine's own online state and region onto its lane", () => {
    const fleet = [{ ...FLEET[0], online: false, region: "SG" }];
    const lanes = machineLanes(fleet, null);
    expect(lanes[0]).toMatchObject({ online: false, region: "SG" });
  });
});

describe("tallyTasks", () => {
  it("counts every phase, and the total is the number of tasks", () => {
    const tally = tallyTasks([
      task({ task_id: "a", state: "COMPLETED" }),
      task({ task_id: "b", state: "RUNNING" }),
      task({ task_id: "c", state: "RUNNING" }),
      task({ task_id: "d", state: "PENDING" }),
      task({ task_id: "e", state: "FAILED" }),
      task({ task_id: "f", state: "CANCELLED" }),
    ]);
    expect(tally).toEqual({
      total: 6,
      pending: 1,
      running: 2,
      done: 1,
      failed: 1,
      cancelled: 1,
    });
  });
});

describe("runElapsedSeconds", () => {
  it("prefers the API's own measurement over the visitor's clock", () => {
    expect(runElapsedSeconds(run({ elapsed_s: 387 }), 0)).toBe(387);
  });

  it("derives a live counter from created_at when the API sent none", () => {
    const now = Date.parse("2026-08-14T10:00:45Z");
    expect(runElapsedSeconds(run(), now)).toBe(45);
  });

  it("stops the derived counter at finished_at", () => {
    const started = run({ finished_at: "2026-08-14T10:01:00Z" });
    const long_after = Date.parse("2026-08-14T12:00:00Z");
    expect(runElapsedSeconds(started, long_after)).toBe(60);
  });

  it("is null, not zero, when nothing can be measured", () => {
    expect(runElapsedSeconds(null, 0)).toBeNull();
    expect(runElapsedSeconds(run({ created_at: null }), 0)).toBeNull();
    expect(runElapsedSeconds(run({ created_at: "not a date" }), 0)).toBeNull();
  });
});

describe("formatElapsed", () => {
  it.each([
    [0, "0s"],
    [9, "9s"],
    [59, "59s"],
    [60, "1m 00s"],
    [387, "6m 27s"],
    [3600, "60m 00s"],
    [null, "—"],
  ])("%s renders as %s", (seconds, expected) => {
    expect(formatElapsed(seconds)).toBe(expected);
  });
});

describe("formatBytes", () => {
  it.each([
    [0, "0 B"],
    [999, "999 B"],
    [1234, "1.2 KB"],
    [1024 * 1024 * 5.5, "5.5 MB"],
    [1024 * 1024 * 42, "42 MB"],
    [null, "—"],
    [undefined, "—"],
    [-1, "—"],
  ])("%s renders as %s", (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected);
  });
});

describe("compareSpeed", () => {
  function snapshot(runs: DemoRun[]): DemoSnapshot {
    return { fleet: FLEET, runs, guests: [], guest_run: null };
  }

  it("states the head-to-head once both venues have finished", () => {
    const result = compareSpeed(
      snapshot([
        run({ coordinator: "render", state: "SUCCEEDED", elapsed_s: 400 }),
        run({ coordinator: "fc", state: "SUCCEEDED", elapsed_s: 200 }),
      ])
    );
    expect(result).toEqual({
      render: 400,
      fc: 200,
      faster: "fc",
      deltaSeconds: 200,
      ratio: 2,
    });
  });

  // The single most misleading thing this page could show a judge is a
  // comparison drawn against a run still in flight.
  it("says nothing while either run is still going", () => {
    expect(
      compareSpeed(
        snapshot([
          run({ coordinator: "render", state: "SUCCEEDED", elapsed_s: 400 }),
          run({ coordinator: "fc", state: "RUNNING", elapsed_s: 12 }),
        ])
      )
    ).toBeNull();
  });

  it("says nothing when only one venue has run at all", () => {
    expect(
      compareSpeed(snapshot([run({ coordinator: "render", state: "SUCCEEDED", elapsed_s: 5 })]))
    ).toBeNull();
    expect(compareSpeed(snapshot([]))).toBeNull();
    expect(compareSpeed(null)).toBeNull();
  });

  it("compares a failed run honestly rather than hiding it", () => {
    // A run that failed after 20s still took 20s. The panels state each
    // run's own state beside its time, so a reader sees what is compared.
    const result = compareSpeed(
      snapshot([
        run({ coordinator: "render", state: "FAILED", elapsed_s: 20 }),
        run({ coordinator: "fc", state: "SUCCEEDED", elapsed_s: 80 }),
      ])
    );
    expect(result?.faster).toBe("render");
    expect(result?.deltaSeconds).toBe(60);
  });

  it("reports a genuine tie as a tie, with no ratio", () => {
    const result = compareSpeed(
      snapshot([
        run({ coordinator: "render", state: "SUCCEEDED", elapsed_s: 90 }),
        run({ coordinator: "fc", state: "SUCCEEDED", elapsed_s: 90 }),
      ])
    );
    expect(result?.faster).toBeNull();
    expect(result?.deltaSeconds).toBe(0);
    expect(result?.ratio).toBeNull();
  });

  it("never prints an Infinity ratio", () => {
    const result = compareSpeed(
      snapshot([
        run({ coordinator: "render", state: "SUCCEEDED", elapsed_s: 30 }),
        run({ coordinator: "fc", state: "SUCCEEDED", elapsed_s: 0 }),
      ])
    );
    expect(result?.faster).toBe("fc");
    expect(result?.ratio).toBeNull();
  });
});

describe("anyRunLive", () => {
  it("is false for a page nobody has pressed yet", () => {
    expect(anyRunLive({ fleet: FLEET, runs: [], guests: [], guest_run: null })).toBe(
      false
    );
    expect(anyRunLive(null)).toBe(false);
  });

  it("is true while either venue is still going", () => {
    expect(
      anyRunLive({
        fleet: FLEET,
        guests: [],
        guest_run: null,
        runs: [
          run({ coordinator: "render", state: "SUCCEEDED" }),
          run({ coordinator: "fc", state: "RUNNING" }),
        ],
      })
    ).toBe(true);
  });

  it("is false once both are terminal, which is what stops the polling", () => {
    expect(
      anyRunLive({
        fleet: FLEET,
        guests: [],
        guest_run: null,
        runs: [
          run({ coordinator: "render", state: "SUCCEEDED" }),
          run({ coordinator: "fc", state: "FAILED" }),
        ],
      })
    ).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// the judge's own machine
// ---------------------------------------------------------------------------

describe("parseDemoSnapshot — the guest half", () => {
  it("reads guests and guest_run from the wider envelope", () => {
    const snap = parseDemoSnapshot({
      fleet: [],
      runs: [],
      guests: [
        {
          name: "prov-8f21",
          online: true,
          region: "SG",
          cpus: 8,
          memory_gb: 16,
          official: false,
        },
      ],
      guest_run: {
        job_id: "job-guest-1",
        coordinator: "render",
        state: "SUCCEEDED",
        created_at: "2026-08-14T10:00:00Z",
        finished_at: "2026-08-14T10:00:09Z",
        elapsed_s: 9,
        tasks: [
          {
            task_id: "task-000",
            state: "COMPLETED",
            machine: "prov-8f21",
            started_at: "2026-08-14T10:00:01Z",
            finished_at: "2026-08-14T10:00:09Z",
            outcome: "accepted",
          },
        ],
        artifacts: [],
      },
    });

    expect(snap.guests).toHaveLength(1);
    expect(snap.guests[0]).toMatchObject({ name: "prov-8f21", official: false });
    expect(snap.guest_run?.job_id).toBe("job-guest-1");
    expect(snap.guest_run?.tasks).toHaveLength(1);
  });

  // The common case, in every deployment, until somebody plugs something in.
  it("treats an empty guest half as rest, not as an error", () => {
    const snap = parseDemoSnapshot({ fleet: [], runs: [], guests: [], guest_run: null });
    expect(snap.guests).toEqual([]);
    expect(snap.guest_run).toBeNull();
    expect(anyRunLive(snap)).toBe(false);
  });

  // The page shipped against a four-key envelope, but it must not break on a
  // deploy where the API is still the older two-key one.
  it("survives an envelope with no guest keys at all", () => {
    const snap = parseDemoSnapshot({ fleet: [], runs: [] });
    expect(snap.guests).toEqual([]);
    expect(snap.guest_run).toBeNull();
  });

  it.each([
    ["a guest_run that is not an object", "soon"],
    ["a guest_run with no job_id", { state: "RUNNING" }],
    ["guests that is not an array", undefined],
  ])("drops %s rather than throwing", (_label, value) => {
    expect(() =>
      parseDemoSnapshot({ fleet: [], runs: [], guests: value, guest_run: value })
    ).not.toThrow();
    const snap = parseDemoSnapshot({
      fleet: [],
      runs: [],
      guests: value,
      guest_run: value,
    });
    expect(snap.guests).toEqual([]);
    expect(snap.guest_run).toBeNull();
  });

  it("parses a guest run through the same reader as a venue run", () => {
    // Same shape on the wire (`_demo_run_view` builds all three), so the same
    // fields must survive — the drift this guards against is a field handled
    // for `runs` and forgotten for `guest_run`.
    const body = {
      job_id: "job-1",
      coordinator: "render",
      state: "RUNNING",
      created_at: "2026-08-14T10:00:00Z",
      finished_at: null,
      elapsed_s: 3.4,
      tasks: [{ task_id: "task-000", state: "LEASED", machine: "prov-8f21" }],
      artifacts: [{ name: "out.txt", bytes: 12 }],
    };
    const viaRuns = parseDemoSnapshot({ runs: [body] }).runs[0];
    const viaGuest = parseDemoSnapshot({ guest_run: body }).guest_run;
    expect(viaGuest).toEqual(viaRuns);
  });
});

describe("anyRunLive — the guest run counts", () => {
  // The moment a judge is most invested in is their own laptop's task. A
  // poll loop that ignored `guest_run` would freeze the grid a second after
  // they pressed the button.
  it("keeps polling while only the guest run is going", () => {
    expect(
      anyRunLive({
        fleet: FLEET,
        guests: [],
        runs: [
          run({ coordinator: "render", state: "SUCCEEDED" }),
          run({ coordinator: "fc", state: "SUCCEEDED" }),
        ],
        guest_run: run({ job_id: "g", state: "RUNNING" }),
      })
    ).toBe(true);
  });

  it("stops once the guest run is terminal too", () => {
    expect(
      anyRunLive({
        fleet: FLEET,
        guests: [],
        runs: [run({ coordinator: "render", state: "SUCCEEDED" })],
        guest_run: run({ job_id: "g", state: "SUCCEEDED" }),
      })
    ).toBe(false);
  });
});

describe("parseJoinedMachine", () => {
  it("reads the machine the API just enrolled", () => {
    expect(
      parseJoinedMachine({
        machine: { name: "phongs-macbook", node_id: "nd-1", label: "prov-8f21" },
        pool: "demo-guests",
      })
    ).toEqual({ name: "phongs-macbook", node_id: "nd-1", label: "prov-8f21" });
  });

  it("tolerates a machine the API could not name", () => {
    // `render_joined_machine` returns null for a machine with no name.
    const joined = parseJoinedMachine({
      machine: { name: null, node_id: "nd-1", label: "prov-8f21" },
    });
    expect(joined?.name).toBeNull();
    expect(joined?.label).toBe("prov-8f21");
  });

  // The label is the ONLY identifier that appears in the public `guests`
  // array, so a response without one cannot be matched to anything and is
  // not a usable join result.
  it.each([
    ["no label", { machine: { name: "x", node_id: "nd-1" } }],
    ["no machine", { pool: "demo-guests" }],
    ["not an object", "joined!"],
    ["null", null],
  ])("returns null for %s", (_label, body) => {
    expect(parseJoinedMachine(body)).toBeNull();
  });
});

describe("isMyGuest", () => {
  const joined: JoinedMachine = {
    name: "phongs-macbook",
    node_id: "nd-1",
    label: "prov-8f21",
  };

  it("matches on the handle, which is what the public list carries", () => {
    expect(isMyGuest({ name: "prov-8f21" }, joined)).toBe(true);
  });

  it("does not match on the real hostname, which is never in that list", () => {
    expect(isMyGuest({ name: "phongs-macbook" }, joined)).toBe(false);
  });

  it("claims nothing for a visitor who has not joined in this browser", () => {
    // Somebody else's laptop is not theirs to be shown as theirs.
    expect(isMyGuest({ name: "prov-8f21" }, null)).toBe(false);
    expect(isMyGuest({ name: "prov-other" }, joined)).toBe(false);
  });
});

describe("normaliseUserCode", () => {
  it.each([
    ["ABCD-EFGH", "ABCDEFGH"],
    ["abcd-efgh", "ABCDEFGH"],
    ["  ABCD EFGH  ", "ABCDEFGH"],
    ["abcd–efgh", "ABCDEFGH"], // an en dash from a smart-quoting terminal
    ["", ""],
    ["----", ""],
  ])("%s normalises to %s", (raw, expected) => {
    expect(normaliseUserCode(raw)).toBe(expected);
  });

  it("enables the button for anything with a character in it", () => {
    // Deliberately not a length check: a page that refuses to submit an
    // 8-character code because it expected 9 cannot be recovered from the
    // browser, and the API is the authority on what is real.
    expect(userCodeReady("ABCD-EFGH")).toBe(true);
    expect(userCodeReady("A")).toBe(true);
    expect(userCodeReady("  -- ")).toBe(false);
    expect(userCodeReady("")).toBe(false);
  });
});

describe("joinErrorMessage", () => {
  // Every failure this route produces carries a sentence written for a
  // person standing at a terminal. Printing our own paraphrase instead would
  // replace four good messages with four worse ones.
  it.each([
    [404, "unknown code"],
    [404, "that code has expired — run `flashnode login` again"],
    [400, "that is a CLI login code — the demo joins a MACHINE, so run `flashnode login` and paste the code it prints"],
    [409, "this machine is already enrolled"],
  ])("prints the API's own sentence for %s", (status, detail) => {
    expect(joinErrorMessage(status, detail)).toBe(detail);
  });

  it("falls back per status only when there is no body to read", () => {
    // A proxy 502, a dropped connection, a response that is not JSON.
    expect(joinErrorMessage(400, null)).toContain("machine code");
    expect(joinErrorMessage(404, null)).toContain("expired");
    expect(joinErrorMessage(409, null)).toContain("already joined");
    expect(joinErrorMessage(429, null)).toContain("moment");
    expect(joinErrorMessage(503, null)).toContain("not accepting");
    expect(joinErrorMessage(500, null)).toBe("Could not join that machine right now.");
  });

  it("never returns an empty string", () => {
    for (const status of [400, 404, 409, 418, 429, 503]) {
      expect(joinErrorMessage(status, null).length).toBeGreaterThan(0);
      expect(joinErrorMessage(status, "").length).toBeGreaterThan(0);
    }
  });
});

describe("runMineErrorMessage", () => {
  it("prints the API's 503, which is the one that tells a judge what to do", () => {
    const detail =
      "no guest machine has joined yet — run `flashnode login` and paste the code above first";
    expect(runMineErrorMessage(503, detail)).toBe(detail);
  });

  it("falls back to something honest with no body", () => {
    expect(runMineErrorMessage(503, null)).toContain("No machine has joined");
    expect(runMineErrorMessage(500, null)).toBe("Could not start that run right now.");
  });
});

describe("apiDetail", () => {
  it("pulls FastAPI's message out, and nothing else", () => {
    expect(apiDetail({ detail: "unknown code" })).toBe("unknown code");
    expect(apiDetail({ detail: "" })).toBeNull();
    expect(apiDetail({ message: "unknown code" })).toBeNull();
    expect(apiDetail("unknown code")).toBeNull();
    expect(apiDetail(null)).toBeNull();
    // A 422's detail is an array of validation objects, not a sentence.
    expect(apiDetail({ detail: [{ msg: "field required" }] })).toBeNull();
  });
});

describe("UNPLACED_LANE", () => {
  it("names the waiting room in a word that fits a lane header", () => {
    // A lane column is ~150px of 10.5px mono. "Awaiting placement" was the
    // first version and truncated to "Awaiting place…" in the harness — on
    // the one lane whose entire job is to explain itself.
    expect(UNPLACED_LANE).toBe("Unplaced");
    expect(UNPLACED_LANE.length).toBeLessThanOrEqual(12);
  });
});
