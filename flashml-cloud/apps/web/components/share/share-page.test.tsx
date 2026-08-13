import { readFileSync } from "node:fs";
import { join } from "node:path";
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

import SharedRecordPage, {
  generateMetadata,
} from "@/app/share/[token]/page";
import { describeRun, readJobPayload, type PublicAttempt } from "./job-share";

/**
 * The PUBLIC RUN PAGE — the job half of `/share/<token>`.
 *
 * The session half is covered by `lib/sandbox-evidence-view.test.ts` and is
 * deliberately untouched by this change: one token space now resolves to two
 * kinds of record, and a session must still render exactly as it did. What is
 * asserted here is the job kind, plus the two properties that are the same for
 * both and are security rather than cosmetics — one indistinguishable failure
 * page, and nothing withheld by the API reaching the markup.
 *
 * Read `apps/api/flashml_cloud_api/job_share.py` before adding a field to any
 * fixture below. What that route publishes and what it withholds is an
 * authorship rule (AS-16: *a session's output is ours; a job's output is
 * theirs*), and a fixture naming a field the API does not send is how a
 * component comes to read one.
 */

// Built, not written. A share token is `token_urlsafe(32)`-shaped by design —
// that is what makes a bearer capability unguessable, and it is also exactly
// what makes a secret scanner fire on one. Assembling it keeps a high-entropy
// literal out of the source.
const SHARE_TOKEN = "shr_" + "abcdefghijklmnopqrstuvwxyz".repeat(2).slice(0, 43);

/**
 * The demo run, and the only shape that matters: two tasks, three leases, two
 * machines. `task 1` is claimed by `machine A`, whose lease EXPIRES — the
 * machine stopped renewing it — and is then claimed and finished by
 * `machine B`. That is the product's whole claim in three rows.
 *
 * Mirrors the API's own fixture in `tests/test_public_job_share.py` so the two
 * suites are describing one run rather than two inventions.
 */
const ATTEMPTS: PublicAttempt[] = [
  {
    machine: "machine A",
    task: "task 1",
    claimed_at: "2026-08-12T10:00:00+00:00",
    resolved_at: "2026-08-12T10:00:20+00:00",
    outcome: "expired",
    duration_s: 20,
  },
  {
    machine: "machine B",
    task: "task 1",
    claimed_at: "2026-08-12T10:00:50+00:00",
    resolved_at: "2026-08-12T10:01:30+00:00",
    outcome: "accepted",
    duration_s: 40,
  },
  {
    machine: "machine B",
    task: "task 2",
    claimed_at: "2026-08-12T10:01:30+00:00",
    resolved_at: "2026-08-12T10:02:00+00:00",
    outcome: "failed",
    duration_s: 30,
  },
];

const JOB = {
  job_id: "a1b2c3d4e5f6",
  state: "SUCCEEDED",
  created_at: "2026-08-12T09:59:00+00:00",
  finished_at: "2026-08-12T10:02:00+00:00",
  tasks_total: 2,
  tasks_accepted: 1,
  machines_claiming: 2,
  attempts_total: 3,
  attempts_accepted: 1,
  attempts_failed: 1,
  attempts_expired: 1,
  attempts_abandoned: 0,
  attempts_unresolved: 0,
};

const EVENTS = [
  { seq: 1, kind: "JOB_ACCEPTED" },
  { seq: 2, kind: "TASK_CREATED" },
  { seq: 3, kind: "LEASE_CLAIMED" },
  { seq: 4, kind: "LEASE_EXPIRED" },
  { seq: 5, kind: "TASK_REQUEUED" },
  { seq: 6, kind: "LEASE_CLAIMED" },
  { seq: 7, kind: "TASK_COMMIT_ACCEPTED" },
  { seq: 8, kind: "JOB_SUCCEEDED" },
];

const jobBody = (over: Record<string, unknown> = {}) => ({
  kind: "job",
  job: JOB,
  attempts: ATTEMPTS,
  events: EVENTS,
  ...over,
});

// A minimally valid session envelope. The session's own rendering is asserted
// at length elsewhere; what matters here is that the NEW discriminated
// envelope still reaches the old renderer.
const SESSION_BODY = {
  kind: "session",
  session: {
    id: "6f2f0f4e-1f2a-4a1e-9c1b-2b7a1d9e0c33",
    state: "TERMINATED",
    provider: "alibaba-fc-sandbox",
    region: "ap-southeast-1",
    template: "code-interpreter-v1",
    external_sandbox_id: "isbx-9f2c1a7bd4e6",
    marker_sha256: null,
    training_job_id: "job-train-7c1d9e",
    evaluation_job_id: null,
    created_at: "2026-08-11T12:00:00.000Z",
    terminated_at: "2026-08-11T12:07:11.000Z",
    error_code: null,
    error_message: null,
  },
  events: [],
};

describe("the public run page", () => {
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
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: async () => body });
  }

  async function renderPage(
    query: Record<string, string> = {},
    token = SHARE_TOKEN
  ) {
    const element = await SharedRecordPage({
      params: Promise.resolve({ token }),
      searchParams: Promise.resolve(query),
    });
    return renderToStaticMarkup(element);
  }

  // -- the story ---------------------------------------------------------

  it("leads with the recovery narrative a stranger has fifteen seconds for", async () => {
    ok(jobBody());
    const markup = await renderPage();

    // The claim, in the heading, before any evidence means anything: work was
    // running on a machine, that machine stopped, another finished it.
    expect(markup).toContain("A machine stopped. Another finished the work.");
    expect(markup).toContain('data-story="recovered"');

    // The same fact with the specifics that make it checkable against the
    // table underneath.
    expect(markup).toContain("Task 1 was running on machine A");
    expect(markup).toContain("stopped responding");
    expect(markup).toContain("Machine B claimed the same task");
    expect(markup).toContain("its result was accepted");

    // And drawn, not only written — the shape reads before the words do.
    expect(markup).toContain('data-testid="recovery-handoff"');
    // 30 s between the expired lease resolving and the next claim, in the
    // console's own duration format.
    expect(markup).toContain("30.0 s to pick up");
  });

  it("counts attempted and accepted work separately", async () => {
    // Hard rule 4, on the one page where collapsing them would make every
    // finished run look flawless and erase the wasted work the product exists
    // to recover from.
    ok(jobBody());
    const markup = await renderPage();

    expect(markup).toContain("Tasks accepted");
    expect(markup).toContain("1 of 2");
    expect(markup).toContain("Leases handed out");
    // The full accounting, zeroes included, so a reader can check it sums.
    expect(markup).toContain("1 accepted");
    expect(markup).toContain("1 failed");
    expect(markup).toContain("1 expired");
    expect(markup).toContain("0 abandoned");
    expect(markup).toContain("0 still in flight");
  });

  it("shows every lease as evidence beneath the story", async () => {
    ok(jobBody());
    const markup = await renderPage();

    for (const cell of ["task 1", "task 2", "machine A", "machine B"]) {
      expect(markup, cell).toContain(cell);
    }
    // Our own table's clocks, in UTC — a judge comparing this to a
    // coordinator log is comparing UTC to UTC.
    expect(markup).toContain("10:00:00 UTC");
    expect(markup).toContain("20.0 s"); // machine A held the lease 20 seconds
    expect(markup).toContain('data-outcome="expired"');
    expect(markup).toContain('data-outcome="accepted"');
  });

  it("tells the weaker stories honestly instead of forcing the headline", async () => {
    // A run with no losses must not claim a recovery it did not perform.
    ok(
      jobBody({
        attempts: [{ ...ATTEMPTS[1], task: "task 1" }],
        job: { ...JOB, attempts_expired: 0, attempts_failed: 0 },
      })
    );
    let markup = await renderPage();
    expect(markup).toContain('data-story="clean"');
    expect(markup).toContain("Every task finished on the machine that claimed it.");
    expect(markup).not.toContain('data-testid="recovery-handoff"');

    // A lease lost with nobody picking it up is an interruption, not a
    // recovery.
    ok(jobBody({ attempts: [ATTEMPTS[0]] }));
    markup = await renderPage();
    expect(markup).toContain('data-story="interrupted"');
    expect(markup).toContain("A machine stopped mid-run.");

    // Picked up but not yet finished is a weaker claim than finished, and
    // gets a different sentence.
    ok(
      jobBody({
        attempts: [
          ATTEMPTS[0],
          { ...ATTEMPTS[1], outcome: null, resolved_at: null, duration_s: null },
        ],
      })
    );
    markup = await renderPage();
    expect(markup).toContain('data-story="handed-off"');
    expect(markup).toContain("A machine stopped. Another picked the work up.");
    expect(markup).toContain("is running it now");

    // Nothing claimed at all.
    ok(jobBody({ attempts: [] }));
    markup = await renderPage();
    expect(markup).toContain('data-story="no-work"');
    expect(markup).toContain("This run has not claimed any work yet.");
  });

  // -- absent values -----------------------------------------------------

  it("renders an absent value as absent — never 0, never a bare dash", async () => {
    // An in-flight lease: no resolution, no duration, no outcome. Every one of
    // those is a state, not a measurement that failed to arrive, and none of
    // them may be filled in with a number a reader would take for one.
    ok(
      jobBody({
        job: {
          ...JOB,
          state: "RUNNING",
          finished_at: null,
          tasks_total: 1,
          tasks_accepted: 0,
          machines_claiming: 1,
          attempts_total: 1,
          attempts_accepted: 0,
          attempts_failed: 0,
          attempts_expired: 0,
          attempts_abandoned: 0,
          attempts_unresolved: 1,
        },
        attempts: [
          {
            machine: "machine A",
            task: "task 1",
            claimed_at: "2026-08-12T10:00:00+00:00",
            resolved_at: null,
            outcome: null,
            duration_s: null,
          },
        ],
        events: [],
      })
    );
    const markup = await renderPage();

    expect(markup).toContain("in flight");
    // No element whose entire content is a dash. (Em-dashes inside prose are
    // fine and plentiful; a dash standing in for a VALUE is the banned thing,
    // because a reader fills it in with whichever meaning flatters us.)
    expect(markup).not.toMatch(/>\s*[—–-]\s*</);
    // A job that has not finished has no finish time, so no finish row at all
    // rather than a labelled blank.
    expect(markup).not.toContain("finished</span>");
    // No fabricated duration where nothing has been measured.
    expect(markup).not.toContain("0 ms");
    expect(markup).not.toMatch(/>0 s</);
  });

  it("omits a pickup interval it cannot state honestly", async () => {
    // An expired lease is stamped `resolved_at` by the reconciler, which can
    // run AFTER another machine has already claimed the task — so the
    // subtraction really can come out negative. That is an artefact of when a
    // row was written, not a machine picking work up before it was dropped.
    ok(
      jobBody({
        attempts: [
          { ...ATTEMPTS[0], resolved_at: "2026-08-12T10:05:00+00:00" },
          ATTEMPTS[1],
        ],
      })
    );
    const markup = await renderPage();

    // The handoff itself still stands — only the interval is withheld.
    expect(markup).toContain('data-testid="recovery-handoff"');
    expect(markup).toContain("machine A");
    expect(markup).toContain("machine B");
    expect(markup).not.toContain("to pick up");
    // And no negative duration reaches the page as a rendered value.
    expect(markup).not.toMatch(/>\s*-\d/);
  });

  // -- the ledger is a sequence, not a timeline --------------------------

  it("renders the ledger as an ordered sequence with no time column", async () => {
    ok(jobBody());
    const markup = await renderPage();

    expect(markup).toContain('data-testid="job-ledger"');
    expect(markup).toContain("LEASE_EXPIRED");
    expect(markup).toContain("TASK_REQUEUED");
    expect(markup).toContain("Control-plane events, in order");
    expect(markup).toContain(">1</span>"); // the dense position, from the API

    // The wire timestamp was removed from this payload on purpose. Nothing may
    // reintroduce one — not from the wire, and not interpolated from the
    // attempts, which would be the same lie with more steps. So the ledger
    // section carries no clock of any kind.
    const ledger = markup.slice(markup.indexOf('data-testid="job-ledger"'));
    expect(ledger).not.toContain("UTC");
    expect(ledger).not.toMatch(/\d{2}:\d{2}:\d{2}/);
  });

  it("drops the ledger section entirely when the coordinator gave nothing", async () => {
    // The API answers `[]` when the coordinator is unreachable — a section of
    // the page that cannot be drawn, not an error to report to somebody who
    // cannot act on it. The evidence that matters is in our own Postgres.
    ok(jobBody({ events: [] }));
    const markup = await renderPage();

    expect(markup).not.toContain('data-testid="job-ledger"');
    // And the rest of the page is entirely intact.
    expect(markup).toContain("A machine stopped. Another finished the work.");
    expect(markup).toContain('data-testid="recovery-handoff"');
  });

  // -- withheld fields ---------------------------------------------------

  it("never renders a field the API withholds, even when one is planted", async () => {
    // The API narrows in SQL and this page parses field by field, so a key the
    // payload should not carry cannot ride through on a cast. Planting them is
    // the only way to assert that property rather than assume it.
    const planted = {
      kind: "job",
      job: {
        ...JOB,
        name: "quarterly-revenue-finetune",
        source: "https://github.com/acme/private-models",
        spec: { image: "acme/trainer:v3" },
        owner_id: "3f2b1c00-0000-4000-8000-000000000000",
        pool_id: "pool-acme",
        share_token: SHARE_TOKEN,
        artifact_bytes: 88_120_448,
      },
      attempts: [
        {
          ...ATTEMPTS[0],
          machine_id: "8c1d9e77-1111-4000-8000-000000000000",
          task_id: "shard-000",
          lease_id: "lease-77",
          hostname: "phong-macbook-pro",
          node_id: "node-secret",
          region: "eu-central-1",
        },
        ATTEMPTS[1],
      ],
      events: [
        {
          seq: 1,
          kind: "TASK_ATTEMPT_FAILED",
          at: "2027-01-01T00:00:00+00:00",
          message: "Traceback: KeyError('customer_ssn')",
          source: "flashnode",
          data: { task_id: "shard-000", node_id: "node-secret" },
        },
      ],
    };
    ok(planted);
    const markup = await renderPage();

    for (const withheld of [
      "quarterly-revenue-finetune",
      "github.com/acme/private-models",
      "acme/trainer:v3",
      "pool-acme",
      "phong-macbook-pro",
      "node-secret",
      "shard-000",
      "lease-77",
      "eu-central-1",
      "customer_ssn",
      "Traceback",
      "flashnode",
      "2027",
      SHARE_TOKEN,
      "shr_",
    ]) {
      expect(markup, withheld).not.toContain(withheld);
    }

    // Not passing by rendering nothing: the published half is all there.
    expect(markup).toContain("machine A");
    expect(markup).toContain("TASK_ATTEMPT_FAILED");
  });

  it("names no withheld field anywhere in the share components", () => {
    // A source-level check, because the runtime one above can only catch a
    // field somebody thought to plant. Property ACCESS, not prose: the module
    // docstrings discuss hostnames and regions at length and must be free to.
    const sources = ["job-share.ts", "JobRecovery.tsx"].map((file) =>
      readFileSync(join("components/share", file), "utf8")
        // Comments stripped first, so the scan sees CODE only. Both modules
        // discuss `Event.source`, hostnames and step numbers at length —
        // explaining why a field is withheld is the opposite of reading it,
        // and a check that cannot tell those apart would be one nobody could
        // write a docstring around.
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/\/\/.*$/gm, "")
    );

    const withheld = [
      "name", "source", "spec", "owner_id", "pool_id", "share_token",
      "artifact_bytes", "hostname", "node_id", "platform", "capabilities",
      "lease_id", "machine_id", "task_id", "message", "region", "step",
      "error_message", "stderr", "stdout", "at",
    ];

    const offenders: string[] = [];
    for (const source of sources) {
      for (const field of withheld) {
        // `.field` or `["field"]` — the two ways a value gets read off the
        // payload. Deliberately narrow: it must not fire on a comment.
        const re = new RegExp(`\\.${field}\\b|\\["${field}"\\]`);
        if (re.test(source)) offenders.push(field);
      }
    }
    expect(offenders).toEqual([]);
  });

  // -- the failure doctrine ----------------------------------------------

  it("answers a bad token, a dead API and a malformed body with one identical page", async () => {
    // Distinguishing them for an anonymous visitor tells a prober which tokens
    // exist. All three must be the same bytes.
    fetchMock.mockResolvedValue({ ok: false, status: 404, json: async () => ({}) });
    const missing = await renderPage({}, "shr_definitely-not-a-real-token");

    fetchMock.mockRejectedValue(new Error("ECONNREFUSED http://localhost:8000"));
    const offline = await renderPage();

    ok({ kind: "job", job: { job_id: "a1b2c3d4e5f6" } }); // no `state`
    const malformed = await renderPage();

    ok({ kind: "something-else", job: JOB, attempts: ATTEMPTS, events: EVENTS });
    const unknownKind = await renderPage();

    expect(missing).toContain("isn&#x27;t valid");
    expect(offline).toBe(missing);
    expect(malformed).toBe(missing);
    expect(unknownKind).toBe(missing);

    // And none of them echoes the link, the API, or what went wrong.
    for (const page of [missing, offline, malformed, unknownKind]) {
      expect(page).not.toContain("shr_definitely-not-a-real-token");
      expect(page).not.toContain("ECONNREFUSED");
      expect(page).not.toContain("localhost:8000");
      expect(page).not.toContain("a1b2c3d4e5f6");
    }
  });

  // -- the route, and the session it must not disturb --------------------

  it("asks the one public route, with the token in the path and no credential", async () => {
    ok(jobBody());
    await renderPage();

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`http://localhost:8000/v1alpha1/public/share/${SHARE_TOKEN}`);
    // The BROWSER path is unchanged and still one segment (`/share/<token>`),
    // so `middleware.ts`'s anchored `/^\/share\/[A-Za-z0-9_-]{1,128}$/` already
    // covers this route and needs no edit — its own comment records that
    // loosening that rule is how a route meant to satisfy one requirement
    // quietly unauthenticates the console.
    expect(SHARE_TOKEN).toMatch(/^[A-Za-z0-9_-]{1,128}$/);
    // No credential of any kind: this route answers without one, and
    // attaching one would make the page's own claim untrue.
    expect(init).toEqual({ cache: "no-store" });
  });

  it("still renders a session through the new discriminated envelope", async () => {
    ok(SESSION_BODY);
    const markup = await renderPage();

    // The session renderer, untouched — not the job one.
    expect(markup).toContain("Sandbox evaluation session");
    expect(markup).toContain("Alibaba FC Sandbox");
    expect(markup).toContain('data-testid="sandbox-lifecycle"');
    expect(markup).not.toContain('data-testid="job-recovery"');
    // And still redacted: the full sandbox id never reaches the markup.
    expect(markup).not.toContain("isbx-9f2c1a7bd4e6");
  });

  // -- live vs settled ---------------------------------------------------

  it("keeps a live run refreshing and leaves a finished one alone", async () => {
    ok(jobBody({ job: { ...JOB, state: "RUNNING", finished_at: null } }));
    expect(await renderPage()).toContain('http-equiv="refresh"');

    ok(jobBody());
    expect(await renderPage()).not.toContain('http-equiv="refresh"');

    // PARTIAL is terminal and deliberately not SUCCEEDED: a run that lost six
    // of twenty-four shards did not succeed, but nothing further will happen.
    ok(jobBody({ job: { ...JOB, state: "PARTIAL" } }));
    expect(await renderPage()).not.toContain('http-equiv="refresh"');
  });

  it("opens in presenter mode when the URL asks for it", async () => {
    ok(jobBody());
    const presenter = await renderPage({ presenter: "1" });

    // The words do not change, only the space they take.
    expect(presenter).toContain("A machine stopped. Another finished the work.");
    expect(presenter).toContain('data-testid="recovery-handoff"');
    expect(presenter).toContain("max-w-7xl");
  });

  // -- metadata ----------------------------------------------------------

  it("titles the tab by what the token actually resolved to", async () => {
    // This link gets pasted into a submission form; the tab is the first thing
    // a judge reads, and a fault-tolerance page titled "Sandbox evaluation
    // session" is simply wrong.
    const meta = (token = SHARE_TOKEN) =>
      generateMetadata({ params: Promise.resolve({ token }) });

    ok(jobBody());
    const job = await meta();
    expect(job.title).toEqual({ absolute: "Shared training run | Zolli Cloud" });

    ok(SESSION_BODY);
    const session = await meta();
    expect(session.title).toEqual({
      absolute: "Sandbox evaluation session | Zolli Cloud",
    });

    // An unresolved token describes nothing — a title naming a kind would tell
    // a prober which kind they had guessed at.
    fetchMock.mockResolvedValue({ ok: false, status: 404, json: async () => ({}) });
    const unknown = await meta();
    expect(unknown.title).toEqual({ absolute: "Shared record | Zolli Cloud" });

    // The path IS the secret. Every branch stays out of every index.
    for (const m of [job, session, unknown]) {
      expect(m.robots).toEqual({ index: false, follow: false });
    }
  });
});

// ---------------------------------------------------------------------------
// the derivation, directly
// ---------------------------------------------------------------------------

describe("describeRun", () => {
  it("counts one handoff when a task is retried on the same machine first", () => {
    // A(expired), A(failed), B(accepted) is ONE handoff, A to B. Scanning
    // forward for "the next attempt on any other machine" would report it
    // twice and inflate the only number on this page anybody would count.
    const rows: PublicAttempt[] = [
      { ...ATTEMPTS[0], machine: "machine A", outcome: "expired" },
      { ...ATTEMPTS[0], machine: "machine A", outcome: "failed" },
      { ...ATTEMPTS[1], machine: "machine B", outcome: "accepted" },
    ];
    const story = describeRun(rows);

    expect(story.kind).toBe("recovered");
    if (story.kind !== "recovered") throw new Error("unreachable");
    expect(story.handoffs).toHaveLength(1);
    expect(story.lead.lostOn).toBe("machine A");
    expect(story.lead.resumedOn).toBe("machine B");
  });

  it("does not read a handoff across two different tasks", () => {
    // Two tasks failing independently on two machines is not a recovery, and
    // reading one across them would manufacture the page's entire claim.
    const story = describeRun([
      { ...ATTEMPTS[0], task: "task 1", machine: "machine A", outcome: "failed" },
      { ...ATTEMPTS[1], task: "task 2", machine: "machine B", outcome: "accepted" },
    ]);

    expect(story.kind).toBe("interrupted");
  });

  it("prefers a completed recovery over one still in flight for the lead", () => {
    const story = describeRun([
      { ...ATTEMPTS[0], task: "task 1", machine: "machine A", outcome: "expired" },
      { ...ATTEMPTS[1], task: "task 1", machine: "machine B", outcome: null },
      { ...ATTEMPTS[0], task: "task 2", machine: "machine A", outcome: "expired" },
      { ...ATTEMPTS[1], task: "task 2", machine: "machine C", outcome: "accepted" },
    ]);

    expect(story.kind).toBe("recovered");
    if (story.kind !== "recovered") throw new Error("unreachable");
    expect(story.lead.resumedOn).toBe("machine C");
    expect(story.lead.resumedAccepted).toBe(true);
    expect(story.handoffs).toHaveLength(2);
  });
});

describe("readJobPayload", () => {
  it("keeps only the fields this page has agreed to publish", () => {
    const parsed = readJobPayload({
      kind: "job",
      job: { ...JOB, name: "secret-run" },
      attempts: [{ ...ATTEMPTS[0], hostname: "phong-macbook-pro" }],
      events: [{ seq: 1, kind: "JOB_ACCEPTED", at: "2027-01-01T00:00:00Z" }],
    });

    expect(parsed).not.toBeNull();
    expect(Object.keys(parsed!.job)).not.toContain("name");
    expect(Object.keys(parsed!.attempts[0])).not.toContain("hostname");
    // The ledger entry keeps position and kind, and grows no clock.
    expect(parsed!.events[0]).toEqual({ seq: 1, kind: "JOB_ACCEPTED" });
  });

  it("refuses anything that is not unmistakably a job payload", () => {
    expect(readJobPayload(null)).toBeNull();
    expect(readJobPayload("nope")).toBeNull();
    expect(readJobPayload({ kind: "session", session: {} })).toBeNull();
    expect(readJobPayload({ kind: "job" })).toBeNull();
    expect(readJobPayload({ kind: "job", job: { job_id: "x" } })).toBeNull();
    expect(readJobPayload({ kind: "job", job: { state: "RUNNING" } })).toBeNull();
  });

  it("drops a malformed row rather than the whole page", () => {
    const parsed = readJobPayload({
      kind: "job",
      job: JOB,
      attempts: [ATTEMPTS[0], null, { machine: "machine B" }],
      events: [{ seq: 1, kind: "JOB_ACCEPTED" }, { kind: "NO_SEQ" }, 7],
    });

    expect(parsed!.attempts).toHaveLength(1);
    expect(parsed!.events).toHaveLength(1);
  });

  it("reports a count it did not receive as absent, never as zero", () => {
    const parsed = readJobPayload({
      kind: "job",
      job: { job_id: "a1b2c3d4e5f6", state: "RUNNING" },
      attempts: [],
      events: [],
    });

    expect(parsed!.job.attempts_total).toBeNull();
    expect(parsed!.job.tasks_accepted).toBeNull();
  });
});
