import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// `app/share/[token]/page.tsx` imports `cloudApiBase` from lib/cloud-api,
// which imports the Supabase browser client. Nothing constructs one on this
// path — the whole point of the page is that it needs no session — but mock
// the boundary anyway, for the reason middleware.test.ts spells out: building
// a real client probes for a native WebSocket at CONSTRUCTION time and fails
// on Node 20.
vi.mock("@/lib/supabase", () => ({
  createBrowserSupabaseClient: () => ({ auth: { getSession: vi.fn() } }),
}));

import SharedSandboxSessionPage from "@/app/share/[token]/page";
import { SandboxLifecycle } from "@/components/jobs/SandboxLifecycle";
import {
  BOUNDARY_NOTE,
  NOT_OBSERVED,
  type SandboxEvent,
  type SandboxSession,
} from "./sandbox-session";

const T0 = Date.parse("2026-08-11T12:00:00.000Z");
const at = (offsetMs: number) => new Date(T0 + offsetMs).toISOString();

// Built, not written. A share token is `token_urlsafe(32)`-shaped by
// design — that is what makes a bearer capability unguessable, and it is
// also exactly what makes a secret scanner fire on one. Assembling it keeps
// a high-entropy literal out of the source; the value stays token-shaped, so
// it still exercises the middleware's anchored path pattern.
const SHARE_TOKEN = "shr_" + "abcdefghijklmnopqrstuvwxyz".repeat(2).slice(0, 43);

const SESSION: SandboxSession = {
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
};

const EVENTS: SandboxEvent[] = [
  { sequence: 1, type: "session.requested", source: "controller", observed_at: at(0), latency_ms: null, data: {} },
  { sequence: 2, type: "sandbox.created", source: "fc", observed_at: at(1_000), latency_ms: 901, data: {} },
  { sequence: 3, type: "state.prepared", source: "controller", observed_at: at(30_000), latency_ms: null, data: {} },
  { sequence: 4, type: "sandbox.paused", source: "fc", observed_at: at(35_000), latency_ms: 2635, data: {} },
  { sequence: 5, type: "oss.model_observed", source: "controller", observed_at: at(395_000), latency_ms: null, data: { key: "jobs/job-train-7c1d9e/model.pt" } },
  { sequence: 6, type: "state.resuming", source: "controller", observed_at: at(396_000), latency_ms: null, data: {} },
  { sequence: 7, type: "sandbox.connected", source: "fc", observed_at: at(397_000), latency_ms: 1085, data: {} },
  { sequence: 8, type: "worker.verified", source: "runtime", observed_at: at(398_000), latency_ms: null, data: { marker_matches: true, pid: 4118 } },
  { sequence: 9, type: "state.evaluating", source: "runtime", observed_at: at(400_000), latency_ms: null, data: {} },
  { sequence: 10, type: "state.succeeded", source: "controller", observed_at: at(430_000), latency_ms: null, data: {} },
  { sequence: 11, type: "sandbox.killed", source: "controller", observed_at: at(431_000), latency_ms: 12, data: {} },
  { sequence: 12, type: "worker.credential.deleted", source: "controller", observed_at: at(431_500), latency_ms: null, data: {} },
];

const JUDGE_WORDS = [
  "EXECUTE",
  "WAIT",
  "HIBERNATE",
  "EXTERNAL EVENT",
  "WAKE",
  "CONTINUE",
  "ACCEPTED OUTPUT",
  "CLEANUP",
];

function render(
  props: Partial<Parameters<typeof SandboxLifecycle>[0]> = {}
): string {
  return renderToStaticMarkup(
    createElement(SandboxLifecycle, {
      session: SESSION,
      events: EVENTS,
      now: T0 + 500_000,
      ...props,
    })
  );
}

describe("SandboxLifecycle", () => {
  it("puts all eight lifecycle words on one screen, in order", () => {
    // D-2/D-4 is a stopwatch requirement: execute → wait → hibernate →
    // external event → wake → continue → accepted output → cleanup, found by
    // a stranger in under fifteen seconds. They are row headings, not prose.
    const markup = render();
    let cursor = -1;
    for (const word of JUDGE_WORDS) {
      const found = markup.indexOf(word, cursor + 1);
      expect(found, word).toBeGreaterThan(cursor);
      cursor = found;
    }
  });

  it("shows the measured latencies and marks them measured", () => {
    const markup = render();
    expect(markup).toContain("901 ms");
    expect(markup).toContain("2.64 s"); // pause
    expect(markup).toContain("1.09 s"); // wake — the headline number
    expect(markup).toContain('data-basis="measured"');
  });

  it("draws an estimate differently from a measurement", () => {
    // Rule 2. `state.prepared` carries no latency of its own, so its
    // duration is a subtraction and must not be presented with the same
    // authority as a timed call.
    const markup = render();
    expect(markup).toContain('data-basis="estimated"');
    expect(markup).toMatch(/data-basis="estimated"[^>]*>estimated</);
    expect(markup).toMatch(/data-basis="measured"[^>]*>measured</);
  });

  it("shows the cost story and the caveat that keeps it honest", () => {
    const markup = render();
    expect(markup).toContain("Active compute avoided");
    expect(markup).toContain("6m 1s");
    // Two big numbers side by side imply two independent measurements.
    expect(markup).toContain("the same interval, re-framed");
    // And "compute avoided" is not "bill avoided" — a hibernated sandbox
    // still bills its snapshot.
    expect(markup).toContain("still bills its snapshot");
  });

  it("carries the boundary note, always", () => {
    // The sentence that stops this screen being read as "the sandbox
    // rescued the training job".
    expect(render()).toContain(
      "Training retry and sandbox hibernation are separate guarantees"
    );
    expect(BOUNDARY_NOTE).toContain("separate guarantees");
  });

  it("renders every unobserved metric as 'not observed' — never 0, never a dash", () => {
    // Rule 1, against the emptiest possible session: requested and nothing
    // else. Every metric on the screen is unobserved, and every one of them
    // has to say so in words.
    const markup = render({
      session: {
        ...SESSION,
        state: "REQUESTED",
        external_sandbox_id: null,
        marker_sha256: null,
        evaluation_job_id: null,
        terminated_at: null,
      },
      events: [],
    });

    // One per unobserved measurement plus the identifiers and the four
    // evidence lines — the exact count is not the point; the absence of a
    // number where there is no measurement is.
    expect(markup.split(NOT_OBSERVED).length - 1).toBeGreaterThanOrEqual(8);
    expect(markup).not.toContain("0 ms");
    // No element whose entire content is a dash. (Em-dashes inside prose are
    // fine and plentiful; a dash standing in for a VALUE is the banned
    // thing, because a reader fills it in with whichever meaning flatters
    // us.)
    expect(markup).not.toMatch(/>\s*[—–-]\s*</);
    expect(markup).toContain('data-unobserved="true"');
    // Every step is still drawn: a list that silently shortens tells the
    // reader nothing about where the run got to.
    for (const word of JUDGE_WORDS) expect(markup, word).toContain(word);
  });

  it("marks each step observed or not, so a judge can see where the run is", () => {
    const markup = render({ events: EVENTS.slice(0, 4) });
    expect(markup).toContain('data-step="hibernate" data-observed="true"');
    expect(markup).toContain('data-step="wake" data-observed="false"');
  });

  it("says 'not observed' once per unobserved step, not twice", () => {
    // An unobserved step has no latency BY DEFINITION, so printing one
    // `not observed` for the duration and another for the timestamp reads as
    // two separate failures of a step that simply has not happened yet.
    const markup = render({ events: EVENTS.slice(0, 4) });
    const wakeRow = markup.slice(
      markup.indexOf('data-step="wake"'),
      markup.indexOf('data-step="evaluate"')
    );
    expect(wakeRow.split(NOT_OBSERVED).length - 1).toBe(1);
  });

  it("names the event that proves each row", () => {
    const markup = render();
    for (const type of [
      "sandbox.created",
      "sandbox.paused",
      "oss.model_observed",
      "sandbox.connected",
      "worker.verified",
      "sandbox.killed",
    ]) {
      expect(markup, type).toContain(type);
    }
  });

  it("separates cleanup the provider confirmed from cleanup we merely recorded", () => {
    const confirmed = render();
    expect(confirmed).toContain("sandbox destroyed and credential revoked");

    const recordedOnly = render({
      events: EVENTS.filter(
        (e) => e.type !== "sandbox.killed" && e.type !== "worker.credential.deleted"
      ).concat({
        sequence: 13,
        type: "state.terminated",
        source: "controller",
        observed_at: at(432_000),
        latency_ms: null,
        data: {},
      }),
    });
    expect(recordedOnly).toContain("no provider confirmation observed");
  });

  it("reads at 1280×720 in presenter mode without dropping lifecycle evidence", () => {
    const markup = render({ defaultPresenter: true });
    expect(markup).toContain('data-presenter="on"');

    // Everything a judge is being asked to check survives the mode.
    for (const word of JUDGE_WORDS) expect(markup, word).toContain(word);
    expect(markup).toContain("901 ms");
    expect(markup).toContain("1.09 s");
    expect(markup).toContain("Active compute avoided");
    expect(markup).toContain("Marker continuity");
    expect(markup).toContain("separate guarantees");
    expect(markup).toContain("sandbox.connected"); // the evidence trail

    // What it does drop: the raw ledger and the per-step prose, which are
    // the two things nobody reads from the back of a room.
    expect(markup).not.toContain("Session ledger");
    expect(markup).not.toContain("not a timer and not a poll");
  });

  it("flags a ledger that disagrees with the session row rather than picking a winner", () => {
    const markup = render({
      session: { ...SESSION, state: "TERMINATED" },
      events: EVENTS.slice(0, 4), // ledger stops at HIBERNATED
    });
    expect(markup).toContain("last observed state is");
  });
});

describe("the public share page", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubEnv("NEXT_PUBLIC_CLOUD_API", "http://localhost:8000");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  function ok(body: unknown) {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => body,
    });
  }

  async function renderPage(
    query: Record<string, string> = {},
    token = SHARE_TOKEN
  ) {
    const element = await SharedSandboxSessionPage({
      params: Promise.resolve({ token }),
      searchParams: Promise.resolve(query),
    });
    return renderToStaticMarkup(element);
  }

  it("renders the session for a visitor with no account and no context", async () => {
    ok({ session: SESSION, events: EVENTS });
    const markup = await renderPage();

    // One line saying what this is, before any of the evidence means
    // anything.
    expect(markup).toContain("A read-only record of one evaluation run");
    expect(markup).toContain("Alibaba FC Sandbox");
    for (const word of JUDGE_WORDS) expect(markup, word).toContain(word);
    expect(markup).toContain("1.09 s");
  });

  it("accepts a bare session row carrying its own events", async () => {
    // The public route returns "session + events, redacted"; whether that
    // is an envelope or a row with an `events` key was never pinned, so the
    // page reads both rather than blank-screening on the wrong guess.
    ok({ ...SESSION, events: EVENTS });
    expect(await renderPage()).toContain("Alibaba FC Sandbox");
  });

  it("publishes no full identifier, no hash and no token — including in the props it serialises", async () => {
    ok({ session: SESSION, events: EVENTS });
    const markup = await renderPage();

    for (const secret of [
      SESSION.external_sandbox_id!,
      SESSION.marker_sha256!,
      SESSION.training_job_id,
      SESSION.evaluation_job_id!,
      SESSION.id,
      SHARE_TOKEN,
      "shr_",
      // A field the API sends and this page must never echo back.
      "jobs/job-train-7c1d9e/model.pt",
    ]) {
      expect(markup, secret).not.toContain(secret);
    }

    // The suffixes that ARE shown, so this is not passing by rendering
    // nothing at all.
    expect(markup).toContain("…7bd4e6");
    expect(markup).toContain("model.pt");
  });

  it("shows an error code to a stranger and never the message", async () => {
    ok({
      session: {
        ...SESSION,
        state: "FAILED",
        error_code: "create_refused",
        error_message: "PauseSessionForbidden at endpoint …",
      },
      events: EVENTS.slice(0, 2),
    });
    const markup = await renderPage();
    expect(markup).toContain("create_refused");
    expect(markup).not.toContain("PauseSessionForbidden");
  });

  it("keeps a live session refreshing and leaves a finished one alone", async () => {
    ok({ session: { ...SESSION, state: "HIBERNATED" }, events: EVENTS.slice(0, 4) });
    expect(await renderPage()).toContain('http-equiv="refresh"');

    ok({ session: SESSION, events: EVENTS });
    expect(await renderPage()).not.toContain('http-equiv="refresh"');
  });

  it("opens in presenter mode when the URL asks for it", async () => {
    ok({ session: SESSION, events: EVENTS });
    expect(await renderPage({ presenter: "1" })).toContain('data-presenter="on"');
    expect(await renderPage()).toContain('data-presenter="off"');
  });

  it("says nothing about a link that does not resolve, and does not echo it", async () => {
    fetchMock.mockResolvedValue({ ok: false, status: 404, json: async () => ({}) });
    const markup = await renderPage({}, "shr_definitely-not-a-real-token");

    expect(markup).toContain("isn&#x27;t valid");
    expect(markup).not.toContain("shr_definitely-not-a-real-token");
    // A prober must not be able to tell 404 from "the API is down".
    fetchMock.mockRejectedValue(new Error("ECONNREFUSED http://localhost:8000"));
    const offline = await renderPage();
    expect(offline).toContain("isn&#x27;t valid");
    expect(offline).not.toContain("ECONNREFUSED");
    expect(offline).not.toContain("localhost:8000");
  });

  it("asks the public route, with the token in the path and nothing else attached", async () => {
    ok({ session: SESSION, events: EVENTS });
    await renderPage();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(
      `http://localhost:8000/v1alpha1/public/sandbox-sessions/${SHARE_TOKEN}`
    );
    // No credential of any kind: this route answers without one, and
    // attaching one would make the page's own claim untrue.
    expect(init).toEqual({ cache: "no-store" });
  });
});
