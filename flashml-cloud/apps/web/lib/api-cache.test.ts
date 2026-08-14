import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Same boundary the cloud-api suite mocks: the client reads its JWT from a
// Supabase session, and these tests are about how many times `fetch` is
// called, not about auth.
const getSession = vi.fn();
vi.mock("./supabase", () => ({
  createBrowserSupabaseClient: () => ({
    auth: { getSession },
  }),
}));

import {
  CACHEABLE_PATHS,
  TTL_MS,
  cacheSize,
  invalidate,
  isCacheable,
  resetApiCache,
  setClock,
  through,
} from "./api-cache";
import {
  NotAuthenticated,
  cancelJob,
  getCredits,
  getJob,
  getMe,
  getMyStorage,
  listJobs,
  listMachines,
  listPools,
  revokeMachine,
  submitJob,
} from "./cloud-api";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const SESSION = { access_token: "test-jwt", user: { id: "user-1" } };

/** Every fetch the client made, as `"METHOD /path"`. Asserting on this
 * rather than on a call count is what makes the intent legible: the point
 * of this work is which requests reach the network, not how many. */
function requestLog(): string[] {
  const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
  return fetchMock.mock.calls.map(([url, init]) => {
    const method = (init?.method ?? "GET") as string;
    return `${method} ${new URL(String(url)).pathname}`;
  });
}

describe("api-cache module", () => {
  beforeEach(() => {
    resetApiCache();
  });

  it("answers a repeated cacheable read without re-running the loader", async () => {
    const load = vi.fn().mockResolvedValue({ id: "me" });

    const a = await through("/v1alpha1/me", load);
    const b = await through("/v1alpha1/me", load);

    expect(load).toHaveBeenCalledTimes(1);
    expect(b).toBe(a);
  });

  it("re-runs the loader once the TTL has elapsed", async () => {
    let t = 1_000;
    setClock(() => t);
    const load = vi.fn().mockResolvedValue("v");

    await through("/v1alpha1/me", load);
    t += TTL_MS - 1;
    await through("/v1alpha1/me", load);
    expect(load).toHaveBeenCalledTimes(1);

    t += 2;
    await through("/v1alpha1/me", load);
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("shares one in-flight promise for a path that is NOT cacheable", async () => {
    // The dedupe half applies to every read; only the TTL half is
    // allowlisted. Two components mounting at once must still collapse.
    const path = "/v1alpha1/jobs/job-1";
    expect(isCacheable(path)).toBe(false);

    let release: (v: string) => void = () => {};
    const load = vi.fn(
      () =>
        new Promise<string>((resolve) => {
          release = resolve;
        })
    );

    const a = through(path, load);
    const b = through(path, load);
    release("done");

    expect(await a).toBe("done");
    expect(await b).toBe("done");
    expect(load).toHaveBeenCalledTimes(1);
    // ...and nothing was retained afterwards, so the next read is fresh.
    expect(cacheSize().fresh).toBe(0);
    const later = vi.fn().mockResolvedValue("again");
    await expect(through(path, later)).resolves.toBe("again");
    expect(later).toHaveBeenCalledTimes(1);
  });

  it("never caches a rejection", async () => {
    const load = vi
      .fn()
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValue("ok");

    await expect(through("/v1alpha1/me", load)).rejects.toThrow("boom");
    // Immediately retryable — no TTL of a broken page.
    await expect(through("/v1alpha1/me", load)).resolves.toBe("ok");
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("does not let a read issued after invalidate() join a pre-invalidate promise", async () => {
    let release: (v: string) => void = () => {};
    const first = vi.fn(
      () =>
        new Promise<string>((resolve) => {
          release = resolve;
        })
    );
    const second = vi.fn().mockResolvedValue("after");

    const stale = through("/v1alpha1/jobs", first);
    invalidate();
    const afterMutation = through("/v1alpha1/jobs", second);

    release("before");
    expect(await stale).toBe("before");
    expect(await afterMutation).toBe("after");
    expect(second).toHaveBeenCalledTimes(1);
  });

  it("does not let a superseded in-flight response populate the cache", async () => {
    let release: (v: string) => void = () => {};
    const load = vi.fn(
      () =>
        new Promise<string>((resolve) => {
          release = resolve;
        })
    );

    const pending = through("/v1alpha1/me", load);
    invalidate();
    release("stale");
    await pending;

    expect(cacheSize().fresh).toBe(0);
  });

  it("keeps the TTL below the console's shortest poll interval", () => {
    // WorkspaceProvider polls at 5000ms and FleetPill at 15000ms. At a TTL
    // that reaches either, a poll would read its own cache and the console
    // would silently stop updating. This is the guard on that number.
    expect(TTL_MS).toBeLessThan(5000);
  });

  it("allowlists only account-level list reads", () => {
    expect([...CACHEABLE_PATHS].sort()).toEqual([
      "/v1alpha1/credits",
      "/v1alpha1/jobs",
      "/v1alpha1/machines",
      "/v1alpha1/me",
      "/v1alpha1/me/storage",
      "/v1alpha1/pools",
    ]);
    // A job detail, a workspace detail and its fleet are live views, and a
    // preflight verdict is about a spec still being edited. None may be
    // answered from cache.
    for (const path of [
      "/v1alpha1/jobs/job-1",
      "/v1alpha1/jobs/job-1/events",
      "/v1alpha1/pools/pool-1",
      "/v1alpha1/pools/pool-1/machines",
      "/v1alpha1/preflight",
      "/v1alpha1/access-requests",
    ]) {
      expect(isCacheable(path)).toBe(false);
    }
  });
});

describe("cloud-api through the cache", () => {
  beforeEach(() => {
    resetApiCache();
    getSession.mockReset();
    getSession.mockResolvedValue({ data: { session: SESSION } });
    vi.stubGlobal("fetch", vi.fn());
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(200, [])));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("issues one request per endpoint for a console shell's worth of concurrent reads", async () => {
    // Exactly the mount-time fan-out of one workspace page: ConsoleShell,
    // UserMenu and WorkspaceProvider each want /me; FleetPill,
    // WorkspaceProvider and CommandPalette each want /jobs; FleetPill and
    // CommandPalette each want /machines; WorkspaceSwitcher wants /pools;
    // StorageWarningBanner wants /me/storage; the overview page wants
    // /credits. Eleven calls, six endpoints.
    await Promise.all([
      getMe(),
      getMe(),
      getMe(),
      listJobs(),
      listJobs(),
      listJobs(),
      listMachines(),
      listMachines(),
      listPools(),
      getMyStorage(),
      getCredits(),
    ]);

    expect(requestLog().sort()).toEqual([
      "GET /v1alpha1/credits",
      "GET /v1alpha1/jobs",
      "GET /v1alpha1/machines",
      "GET /v1alpha1/me",
      "GET /v1alpha1/me/storage",
      "GET /v1alpha1/pools",
    ]);
  });

  it("collapses sequential repeats inside the TTL, not only concurrent ones", async () => {
    // The in-flight half alone would stop working the moment the API gets
    // fast, which is precisely when these components would start landing
    // one after another instead of together.
    await getMe();
    await getMe();
    await getMe();

    expect(requestLog()).toEqual(["GET /v1alpha1/me"]);
  });

  it("re-reads the job list after a submit", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(200, [])));

    await listJobs();
    await submitJob({ name: "demo" });
    await listJobs();

    expect(requestLog()).toEqual([
      "GET /v1alpha1/jobs",
      "POST /v1alpha1/jobs",
      "GET /v1alpha1/jobs",
    ]);
  });

  it("re-reads the machine list after a revoke", async () => {
    await listMachines();
    await revokeMachine("machine-1");
    await listMachines();

    expect(requestLog()).toEqual([
      "GET /v1alpha1/machines",
      "POST /v1alpha1/machines/machine-1/revoke",
      "GET /v1alpha1/machines",
    ]);
  });

  it("drops every cached endpoint on a mutation, not just the one that was written", async () => {
    // A cancel changes the job list AND the credit balance the job was
    // spending. Per-endpoint invalidation would need a map of which write
    // touches which read, and the first entry anyone forgot would show a
    // user a stale number right after they acted.
    await Promise.all([listJobs(), getCredits(), getMe()]);
    await cancelJob("job-1");
    await Promise.all([listJobs(), getCredits(), getMe()]);

    const reads = requestLog().filter((r) => r.startsWith("GET"));
    expect(reads).toHaveLength(6);
  });

  it("invalidates even when the mutation fails", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    await listJobs();

    fetchMock.mockImplementationOnce(() =>
      Promise.resolve(jsonResponse(500, { detail: "nope" }))
    );
    await expect(submitJob({})).rejects.toThrow();

    // A 500 is not proof nothing committed.
    await listJobs();
    expect(requestLog().filter((r) => r === "GET /v1alpha1/jobs")).toHaveLength(
      2
    );
  });

  it("reads the Supabase session once for a batch of concurrent calls", async () => {
    // `send` awaits the session before it can call `fetch`, and supabase-js
    // serialises those reads behind a lock — six acquisitions in front of
    // six requests that were supposed to be parallel.
    await Promise.all([getMe(), listPools(), getMyStorage(), getCredits()]);

    expect(getSession).toHaveBeenCalledTimes(1);
    expect(requestLog()).toHaveLength(4);
  });

  it("does not retain a signed-out session read", async () => {
    getSession.mockResolvedValueOnce({ data: { session: null } });
    await expect(getMe()).rejects.toBeInstanceOf(NotAuthenticated);

    // Signing back in must work on the very next call — a retained
    // rejection would replay "signed out" to every later caller.
    getSession.mockResolvedValue({ data: { session: SESSION } });
    await expect(listPools()).resolves.toEqual([]);
  });

  it("does not hold the session across separate batches", async () => {
    // In-flight only, no TTL: a refreshed token is picked up immediately.
    await getMe();
    await listPools();

    expect(getSession).toHaveBeenCalledTimes(2);
  });

  it("does not cache a job detail read", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockImplementation(() =>
      Promise.resolve(jsonResponse(200, { id: "job-1" }))
    );

    await getJob("job-1");
    await getJob("job-1");

    expect(requestLog()).toEqual([
      "GET /v1alpha1/jobs/job-1",
      "GET /v1alpha1/jobs/job-1",
    ]);
  });
});
