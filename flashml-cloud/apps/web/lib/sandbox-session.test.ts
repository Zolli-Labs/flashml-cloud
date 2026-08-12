import { describe, expect, it } from "vitest";

import {
  BOUNDARY_NOTE,
  NOT_OBSERVED,
  WITHHELD,
  deriveTimeline,
  formatDuration,
  normaliseEvents,
  redactForPublic,
  summariseSandboxSession,
  templateDisplay,
  type SandboxEvent,
  type SandboxSession,
} from "./sandbox-session";

const T0 = Date.parse("2026-08-11T12:00:00.000Z");
const at = (offsetMs: number) => new Date(T0 + offsetMs).toISOString();

function session(over: Partial<SandboxSession> = {}): SandboxSession {
  return {
    id: "6f2f0f4e-1f2a-4a1e-9c1b-2b7a1d9e0c33",
    state: "TERMINATED",
    provider: "alibaba-fc-sandbox",
    region: "ap-southeast-1",
    template: "code-interpreter-v1",
    external_sandbox_id: "isbx-9f2c1a7bd4e6",
    marker_sha256:
      "3f786850e387550fdab836ed7e6dc881de23001b3f786850e387550fdab836ed",
    training_job_id: "job-train-7c1d9e",
    evaluation_job_id: "job-eval-2b7a1d",
    created_at: at(0),
    terminated_at: at(431_000),
    error_code: null,
    error_message: null,
    ...over,
  };
}

function event(
  sequence: number,
  type: string,
  over: Partial<SandboxEvent> = {}
): SandboxEvent {
  return {
    sequence,
    type,
    source: "controller",
    observed_at: at(sequence * 1000),
    latency_ms: null,
    data: {},
    ...over,
  };
}

/** One complete run, in the vocabulary the API actually emits: some
 * transitions carry a provider observation (`sandbox.created`,
 * `sandbox.paused`) and some fall through to the default `state.<x>` event.
 * A view that understood only one of those families would show half a
 * lifecycle. */
function happyRun(): SandboxEvent[] {
  return [
    event(1, "session.requested", { observed_at: at(0) }),
    event(2, "sandbox.created", {
      source: "fc",
      observed_at: at(1_000),
      latency_ms: 901,
    }),
    event(3, "worker.marker.written", {
      source: "runtime",
      observed_at: at(20_000),
      data: { marker_sha256: "3f786850e387" },
    }),
    event(4, "state.prepared", { observed_at: at(30_000) }),
    event(5, "sandbox.paused", {
      source: "fc",
      observed_at: at(35_000),
      latency_ms: 2635,
    }),
    event(6, "oss.model_observed", {
      observed_at: at(395_000),
      data: { key: "jobs/job-train-7c1d9e/model.pt" },
    }),
    event(7, "state.resuming", { observed_at: at(396_000) }),
    event(8, "sandbox.connected", {
      source: "fc",
      observed_at: at(397_000),
      latency_ms: 1085,
    }),
    event(9, "worker.verified", {
      source: "runtime",
      observed_at: at(398_000),
      data: { marker_matches: true, pid_alive: true, claiming: true },
    }),
    event(10, "state.evaluating", { source: "runtime", observed_at: at(400_000) }),
    event(11, "state.succeeded", { observed_at: at(430_000) }),
    event(12, "sandbox.killed", {
      observed_at: at(431_000),
      latency_ms: 12,
    }),
    event(13, "worker.credential.deleted", { observed_at: at(431_500) }),
    event(14, "state.terminated", { observed_at: at(432_000) }),
  ];
}

const summarise = (
  events: SandboxEvent[],
  over: Partial<SandboxSession> = {},
  options: Parameters<typeof summariseSandboxSession>[2] = {}
) => summariseSandboxSession(session(over), events, options);

describe("normaliseEvents", () => {
  it("sorts an out-of-order polling response by sequence", () => {
    // Two overlapping polls, interleaved by the network. Ordering by
    // `observed_at` would be wrong for the pairs this lifecycle produces
    // sub-millisecond apart; `sequence` is allocated server-side under the
    // session row's lock and is a real total order.
    const shuffled = [
      event(3, "state.prepared"),
      event(1, "session.requested"),
      event(2, "sandbox.created"),
    ];
    expect(normaliseEvents(shuffled).map((e) => e.sequence)).toEqual([1, 2, 3]);
  });

  it("collapses duplicates that arrive in more than one poll", () => {
    const withDupes = [
      event(1, "session.requested"),
      event(2, "sandbox.created"),
      event(1, "session.requested"),
      event(2, "sandbox.created"),
      event(3, "state.prepared"),
    ];
    expect(normaliseEvents(withDupes).map((e) => e.sequence)).toEqual([1, 2, 3]);
  });

  it("keeps the first copy of a duplicated sequence, so the merge does not depend on arrival order", () => {
    const first = event(2, "sandbox.created", { latency_ms: 901 });
    const second = event(2, "sandbox.created", { latency_ms: 5000 });
    expect(normaliseEvents([first, second])[0].latency_ms).toBe(901);
    expect(normaliseEvents([second, first])[0].latency_ms).toBe(5000);
    // Same input set either way — the ledger is append-only, so two copies
    // of one sequence are the same row and the choice is arbitrary; what
    // matters is that exactly one survives.
    expect(normaliseEvents([first, second])).toHaveLength(1);
  });

  it("survives an empty, null or undefined ledger", () => {
    expect(normaliseEvents([])).toEqual([]);
    expect(normaliseEvents(null)).toEqual([]);
    expect(normaliseEvents(undefined)).toEqual([]);
  });

  it("keeps an event with an unusable sequence rather than dropping it", () => {
    // Losing a ledger entry is worse than mis-ordering one.
    const odd = { ...event(1, "sandbox.created"), sequence: NaN };
    const kept = normaliseEvents([event(2, "state.prepared"), odd]);
    expect(kept).toHaveLength(2);
    expect(kept[0].sequence).toBe(2);
  });
});

describe("deriveTimeline", () => {
  it("reads both event families — the provider aliases and the state defaults", () => {
    const states = deriveTimeline(happyRun()).map((e) => e.state);
    expect(states).toEqual([
      "REQUESTED",
      "ACTIVE",
      "PREPARED",
      "HIBERNATED",
      "RESUMING",
      "ACTIVE",
      "EVALUATING",
      "SUCCEEDED",
      "TERMINATED",
    ]);
  });

  it("keeps the SECOND visit to ACTIVE, which the session row can never show", () => {
    const actives = deriveTimeline(happyRun()).filter((e) => e.state === "ACTIVE");
    expect(actives).toHaveLength(2);
    expect(actives[0].type).toBe("sandbox.created");
    expect(actives[1].type).toBe("sandbox.connected");
  });

  it("collapses a reconciler re-observing the state it is already in", () => {
    // A controller that lost a connection appends what it observed, which
    // may be where the session already was. Drawing that as a transition
    // would invent one.
    const timeline = deriveTimeline([
      event(1, "session.requested"),
      event(2, "sandbox.created"),
      event(3, "state.active"),
      event(4, "state.prepared"),
    ]);
    expect(timeline.map((e) => e.state)).toEqual([
      "REQUESTED",
      "ACTIVE",
      "PREPARED",
    ]);
  });

  it("ignores events that evidence no state, without dropping them from the ledger", () => {
    const events = [
      event(1, "session.requested"),
      event(2, "bootstrap.install"),
      event(3, "worker.launched"),
    ];
    expect(deriveTimeline(events)).toHaveLength(1);
    expect(summarise(events).events).toHaveLength(3);
  });
});

describe("latencies", () => {
  it("prefers what the observer timed around its own call, and says so", () => {
    const steps = summarise(happyRun()).steps;
    const create = steps.find((s) => s.id === "create");
    const hibernate = steps.find((s) => s.id === "hibernate");
    const wake = steps.find((s) => s.id === "wake");

    expect(create?.measurement).toMatchObject({
      observed: true,
      ms: 901,
      basis: "measured",
      display: "901 ms",
    });
    expect(hibernate?.measurement).toMatchObject({
      basis: "measured",
      display: "2.64 s",
    });
    // The headline number of the whole feature, and the one a judge will
    // read out loud. It has to be the provider's own measurement, not a
    // subtraction that includes our polling.
    expect(wake?.measurement).toMatchObject({
      ms: 1085,
      basis: "measured",
      display: "1.09 s",
    });
  });

  it("falls back to the gap between two observed instants, marked estimated", () => {
    // `state.prepared` carries no latency of its own, so the only honest
    // answer is ACTIVE -> PREPARED, which includes everything that sat
    // between them.
    const prepare = summarise(happyRun()).steps.find((s) => s.id === "prepare");
    expect(prepare?.measurement).toMatchObject({
      observed: true,
      ms: 29_000,
      basis: "estimated",
    });
  });

  it("reports a latency it never saw as not observed, never as zero", () => {
    const steps = summarise([
      event(1, "session.requested"),
      event(2, "sandbox.created", { latency_ms: null, observed_at: at(1_000) }),
    ]).steps;

    for (const id of ["prepare", "hibernate", "wake"] as const) {
      const step = steps.find((s) => s.id === id);
      expect(step?.measurement?.observed, id).toBe(false);
      expect(step?.measurement?.display, id).toBe(NOT_OBSERVED);
      expect(step?.measurement?.ms, id).toBeNull();
    }
  });

  it("gives a step with no duration to report a null measurement, not a false 'not observed'", () => {
    // An object appearing in a bucket is an instant, not an interval.
    // Printing `not observed` against three steps that worked perfectly
    // would read as a broken run.
    const steps = summarise(happyRun()).steps;
    for (const id of ["trigger", "accepted", "cleanup"] as const) {
      const step = steps.find((s) => s.id === id);
      expect(step?.observed, id).toBe(true);
      expect(step?.measurement, id).toBeNull();
    }
  });
});

describe("hibernation, the cost story", () => {
  it("measures the interval between the pause and whatever the ledger recorded next", () => {
    const summary = summarise(happyRun());
    // paused at +35s, resuming observed at +396s.
    expect(summary.hibernated).toMatchObject({
      observed: true,
      ms: 361_000,
      basis: "estimated",
      ongoing: false,
      display: "6m 1s",
    });
    // The same interval, deliberately: one number, two sentences.
    expect(summary.activeComputeAvoided).toEqual(summary.hibernated);
  });

  it("keeps counting, and says it is counting, while the sandbox is still asleep", () => {
    const events = happyRun().slice(0, 5); // ends at sandbox.paused
    const summary = summarise(
      events,
      { state: "HIBERNATED", terminated_at: null },
      { now: T0 + 95_000 }
    );
    expect(summary.hibernated).toMatchObject({
      observed: true,
      ms: 60_000,
      ongoing: true,
    });
    expect(summary.live).toBe(true);
  });

  it("ends the interval at the failure when a session dies in its sleep", () => {
    const events = [
      ...happyRun().slice(0, 5),
      event(6, "state.failed", { observed_at: at(95_000) }),
    ];
    const summary = summarise(events, { state: "FAILED" }, { now: T0 + 900_000 });
    expect(summary.hibernated).toMatchObject({ ms: 60_000, ongoing: false });
  });

  it("says not observed — never 0 — when nothing ever hibernated", () => {
    const summary = summarise([
      event(1, "session.requested"),
      event(2, "sandbox.created"),
    ]);
    expect(summary.hibernated.observed).toBe(false);
    expect(summary.hibernated.display).toBe(NOT_OBSERVED);
    expect(summary.activeComputeAvoided.display).toBe(NOT_OBSERVED);
  });
});

describe("marker continuity", () => {
  it("reports the post-wake verification, with the event that proves it", () => {
    const marker = summarise(happyRun()).marker;
    expect(marker.observed).toBe(true);
    expect(marker.matched).toBe(true);
    expect(marker.evidence).toBe("worker.verified");
    expect(marker.digest.display).toBe("3f786850e387…");
  });

  it("does not accept the pre-hibernation marker write as evidence of continuity", () => {
    // `worker.marker.written` is the hash we later compare AGAINST. Reading
    // it as continuity would let a session that never woke claim its
    // filesystem survived.
    const events = happyRun().filter((e) => e.type !== "worker.verified");
    const marker = summarise(events).marker;
    expect(marker.observed).toBe(false);
    expect(marker.matched).toBeNull();
    expect(marker.display).toBe(NOT_OBSERVED);
  });

  it("reports a mismatch as a mismatch, loudly", () => {
    const events = happyRun().map((e) =>
      e.type === "worker.verified"
        ? {
            ...e,
            type: "worker.unhealthy",
            data: { marker_matches: false, detail: "marker missing != …" },
          }
        : e
    );
    const marker = summarise(events).marker;
    expect(marker.observed).toBe(true);
    expect(marker.matched).toBe(false);
    expect(marker.display).toContain("DID NOT MATCH");
  });

  it("treats a verification whose result cannot be read as no evidence at all", () => {
    // "We ran the check and cannot tell you what it said" is not evidence
    // of continuity.
    const events = happyRun().map((e) =>
      e.type === "worker.verified" ? { ...e, data: {} } : e
    );
    expect(summarise(events).marker.observed).toBe(false);
  });
});

describe("external trigger, evaluation and cleanup", () => {
  it("names the object that woke the sandbox", () => {
    const trigger = summarise(happyRun()).trigger;
    expect(trigger.observed).toBe(true);
    expect(trigger.evidence).toBe("oss.model_observed");
    expect(trigger.detail).toBe("jobs/job-train-7c1d9e/model.pt");
  });

  it("reads a commit acceptance off the state machine when no explicit event exists", () => {
    // SUCCEEDED is reachable only from EVALUATING, so entering it is itself
    // evidence that a commit was accepted.
    const evaluation = summarise(happyRun()).evaluation;
    expect(evaluation.started).toBe(true);
    expect(evaluation.accepted).toBe(true);
  });

  it("does not call an evaluation accepted merely because it was claimed", () => {
    const events = happyRun().filter(
      (e) => !["state.succeeded", "sandbox.killed", "worker.credential.deleted", "state.terminated"].includes(e.type)
    );
    const evaluation = summarise(events, { state: "EVALUATING" }).evaluation;
    expect(evaluation.started).toBe(true);
    expect(evaluation.accepted).toBe(false);
    expect(evaluation.display).toContain("no accepted commit observed");
  });

  it("separates cleanup the provider confirmed from cleanup we merely recorded", () => {
    const observed = summarise(happyRun()).cleanup;
    expect(observed).toMatchObject({
      requested: true,
      observed: true,
      credentialRevoked: true,
    });
    expect(observed.display).toContain("observed");

    // The dangerous case: our own bookkeeping says TERMINATED and no
    // provider ever confirmed it. That is a sandbox that may still be
    // billing by the second.
    const recordedOnly = summarise(
      happyRun().filter(
        (e) => e.type !== "sandbox.killed" && e.type !== "worker.credential.deleted"
      )
    ).cleanup;
    expect(recordedOnly.requested).toBe(true);
    expect(recordedOnly.observed).toBe(false);
    expect(recordedOnly.display).toContain("no provider confirmation");
    expect(recordedOnly.credentialRevoked).toBe(false);
  });

  it("says not observed when nothing has been cleaned up at all", () => {
    const cleanup = summarise(happyRun().slice(0, 5), { state: "HIBERNATED" }).cleanup;
    expect(cleanup.requested).toBe(false);
    expect(cleanup.observed).toBe(false);
    expect(cleanup.display).toBe(NOT_OBSERVED);
  });
});

describe("an empty ledger", () => {
  it("renders the whole expected lifecycle as unobserved rather than as nothing", () => {
    const summary = summarise([], { state: "REQUESTED" });
    expect(summary.steps).toHaveLength(8);
    expect(summary.steps.every((s) => !s.observed)).toBe(true);
    expect(summary.observedState).toBeNull();
    expect(summary.marker.display).toBe(NOT_OBSERVED);
    expect(summary.trigger.display).toBe(NOT_OBSERVED);
    expect(summary.cleanup.display).toBe(NOT_OBSERVED);
    expect(summary.hibernated.display).toBe(NOT_OBSERVED);
  });

  it("never reports a zero for a metric it did not observe", () => {
    const summary = summarise([], { state: "REQUESTED" });
    const measurements = [
      summary.hibernated,
      summary.activeComputeAvoided,
      ...summary.steps.flatMap((s) => (s.measurement ? [s.measurement] : [])),
    ];
    for (const m of measurements) {
      expect(m.observed).toBe(false);
      expect(m.ms).toBeNull();
      expect(m.display).toBe(NOT_OBSERVED);
    }
  });
});

describe("the judge's eight words", () => {
  it("lists every lifecycle step, in order, always", () => {
    const summary = summarise(happyRun());
    expect(summary.steps.map((s) => s.id)).toEqual([
      "create",
      "prepare",
      "hibernate",
      "trigger",
      "wake",
      "evaluate",
      "accepted",
      "cleanup",
    ]);
    const keywords = summary.steps.map((s) => s.keyword).join(" ");
    for (const word of [
      "EXECUTE",
      "WAIT",
      "HIBERNATE",
      "EXTERNAL EVENT",
      "WAKE",
      "CONTINUE",
      "ACCEPTED OUTPUT",
      "CLEANUP",
    ]) {
      expect(keywords, word).toContain(word);
    }
  });

  it("names the event that proves each observed step", () => {
    for (const step of summarise(happyRun()).steps) {
      expect(step.evidence, step.id).not.toBeNull();
    }
  });
});

describe("the public view", () => {
  const publicOptions = { visibility: "public" as const };

  it("shows identifiers only as suffixes", () => {
    const summary = summarise(happyRun(), {}, publicOptions);
    expect(summary.sandboxId.display).toBe("…7bd4e6");
    expect(summary.trainingJob.display).toBe("…7c1d9e");
    expect(summary.evaluation.job.display).toBe("…2b7a1d");
    expect(summary.marker.digest.display).toBe("3f786850e387…");
  });

  it("carries no full identifier anywhere in what the browser receives", () => {
    // The real path: redact at the boundary, then summarise. Both objects
    // are checked, not just the summary — `SandboxLifecycle` is a client
    // component, so the redacted session and events are serialised into the
    // page's HTML as props whether or not any JSX renders them.
    const full = session();
    const redacted = redactForPublic(full, happyRun());
    const serialised = JSON.stringify({
      ...redacted,
      summary: summariseSandboxSession(
        redacted.session,
        redacted.events,
        publicOptions
      ),
    });

    for (const secret of [
      full.external_sandbox_id!,
      full.marker_sha256!,
      full.training_job_id,
      full.evaluation_job_id!,
      full.id,
      "shr_", // no share token, in any form, ever reaches the page
    ]) {
      expect(serialised, secret).not.toContain(secret);
    }
  });

  it("keeps the marker verdict and the object basename, and drops the rest of every payload", () => {
    // The allowlist has to survive: strip `marker_matches` and the
    // continuity claim disappears; keep the raw OSS key and the training job
    // id is published in the page source.
    const { events } = redactForPublic(session(), [
      ...happyRun(),
      event(20, "worker.verified", {
        data: {
          marker_matches: true,
          pid: 4118,
          log_tail: "claim 204 from coordinator https://api.internal",
        },
      }),
    ]);

    const verified = events.filter((e) => e.type === "worker.verified");
    expect(verified.at(-1)?.data).toEqual({ marker_matches: true });
    const trigger = events.find((e) => e.type === "oss.model_observed");
    expect(trigger?.data).toEqual({ key: "model.pt" });
  });

  it("still derives the whole lifecycle from the redacted ledger", () => {
    // Redaction that broke the evidence would be its own kind of failure.
    const redacted = redactForPublic(session(), happyRun());
    const summary = summariseSandboxSession(
      redacted.session,
      redacted.events,
      publicOptions
    );
    expect(summary.steps.every((s) => s.observed)).toBe(true);
    expect(summary.marker.matched).toBe(true);
    expect(summary.trigger.detail).toBe("model.pt");
    expect(summary.hibernated.display).toBe("6m 1s");
  });

  it("says 'withheld', not 'not observed', for an id the API declined to send", () => {
    // The public projection drops `external_sandbox_id` entirely. Saying we
    // never observed one, for a session whose ledger visibly created a
    // sandbox, would be a false statement about our own evidence.
    const summary = summarise(
      happyRun(),
      { external_sandbox_id: null },
      publicOptions
    );
    expect(summary.sandboxId.state).toBe("withheld");
    expect(summary.sandboxId.display).toBe(WITHHELD);
    expect(summary.sandboxId.display).not.toBe(NOT_OBSERVED);
  });

  it("still says 'not observed' when no sandbox was ever created", () => {
    const summary = summarise(
      [event(1, "session.requested")],
      { external_sandbox_id: null, state: "REQUESTED" },
      publicOptions
    );
    expect(summary.sandboxId.state).toBe("not-observed");
    expect(summary.sandboxId.display).toBe(NOT_OBSERVED);
  });

  it("reduces an OSS key to its basename, because the path carries a job id", () => {
    const summary = summarise(happyRun(), {}, publicOptions);
    expect(summary.trigger.detail).toBe("model.pt");
    expect(summary.trigger.detail).not.toContain("job-train");
  });

  it("gives a stranger the error code and never the message", () => {
    const failed = {
      state: "FAILED" as const,
      error_code: "create_refused",
      error_message: "PauseSessionForbidden from ap-southeast-1 endpoint",
    };
    const asPublic = summarise(happyRun(), failed, publicOptions);
    expect(asPublic.errorCode).toBe("create_refused");
    expect(asPublic.errorMessage).toBeNull();

    const asOwner = summarise(happyRun(), failed);
    expect(asOwner.errorMessage).toContain("PauseSessionForbidden");
  });

  it("narrows the owner's identifiers too — this view only ever quotes suffixes", () => {
    const summary = summarise(happyRun());
    expect(summary.sandboxId.display).toBe("…7bd4e6");
    expect(summary.sandboxId.display).not.toContain("isbx");
  });
});

describe("observed state versus the recorded state", () => {
  it("reports what the ledger shows and flags a disagreement rather than picking a winner", () => {
    const summary = summarise(happyRun().slice(0, 5), { state: "TERMINATED" });
    expect(summary.observedState).toBe("HIBERNATED");
    expect(summary.recordedState).toBe("TERMINATED");
    expect(summary.stateAgrees).toBe(false);
  });

  it("agrees when they agree", () => {
    expect(summarise(happyRun()).stateAgrees).toBe(true);
  });
});

describe("formatting", () => {
  it("keeps millisecond precision where the claim needs it and drops it where it does not", () => {
    expect(formatDuration(946)).toBe("946 ms");
    expect(formatDuration(2635)).toBe("2.64 s");
    expect(formatDuration(12_300)).toBe("12.3 s");
    expect(formatDuration(361_000)).toBe("6m 1s");
    expect(formatDuration(120_000)).toBe("2m");
    expect(formatDuration(3_960_000)).toBe("1h 6m");
  });

  it("shows a template digest by its tail and a template name whole", () => {
    expect(templateDisplay("code-interpreter-v1")).toEqual({
      label: "template",
      display: "code-interpreter-v1",
    });
    expect(
      templateDisplay("code-interpreter-v1@sha256:1111222233334444aaaabbbbcccc")
    ).toEqual({ label: "template digest", display: "…aaaabbbbcccc" });
    expect(templateDisplay(null)).toBeNull();
  });
});

describe("the boundary note", () => {
  it("says the two guarantees are separate, in those words", () => {
    // The one sentence that stops this screen being read as "the sandbox
    // rescued the training job".
    expect(BOUNDARY_NOTE.toLowerCase()).toContain(
      "training retry and sandbox hibernation are separate guarantees"
    );
  });
});
