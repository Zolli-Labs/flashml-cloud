import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The client reads the JWT from a Supabase session via
// createBrowserSupabaseClient(); mock that boundary rather than reaching
// into real Supabase, and drive its return value per test.
const getSession = vi.fn();
vi.mock("./supabase", () => ({
  createBrowserSupabaseClient: () => ({
    auth: { getSession },
  }),
}));

import {
  ApiError,
  NotAuthenticated,
  NotFound,
  PreflightRejected,
  cloudApiBase,
  acceptInvite,
  approveDeviceCode,
  createPool,
  createPoolInvite,
  getJob,
  getPool,
  listJobContributions,
  listJobRounds,
  listMachines,
  listPools,
  submitFromRepo,
} from "./cloud-api";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const SESSION = {
  access_token: "test-jwt-abc123",
  user: { id: "user-1" },
};

describe("cloud-api", () => {
  beforeEach(() => {
    getSession.mockReset();
    getSession.mockResolvedValue({ data: { session: SESSION } });
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("attaches the session's JWT as a bearer token on every call", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue(jsonResponse(200, []));

    await listMachines();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers.Authorization).toBe(`Bearer ${SESSION.access_token}`);
  });

  it("throws NotAuthenticated without ever calling fetch when there is no session", async () => {
    getSession.mockResolvedValue({ data: { session: null } });
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;

    await expect(listMachines()).rejects.toBeInstanceOf(NotAuthenticated);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("raises NotAuthenticated on a 401 response, not a silent empty list", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue(jsonResponse(401, { detail: "sign-in required" }));

    await expect(listMachines()).rejects.toBeInstanceOf(NotAuthenticated);
  });

  it("raises NotFound on a 404 without turning it into an access-denied message", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue(jsonResponse(404, { detail: "unknown job" }));

    const err: unknown = await getJob("does-not-exist").catch((e) => e);
    expect(err).toBeInstanceOf(NotFound);
    expect((err as NotFound).message).toBe("unknown job");
  });

  it("raises a plain ApiError for other error statuses, carrying the API's detail verbatim", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue(jsonResponse(410, { detail: "code expired" }));

    const err: unknown = await approveDeviceCode("ABCD1234").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(410);
    expect((err as ApiError).detail).toBe("code expired");
  });

  it("raises PreflightRejected (not a plain ApiError) when the body carries findings", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const findings = [
      { level: "error", code: "MISSING_PACKAGE", message: "requires 'torch'" },
    ];
    fetchMock.mockResolvedValue(
      jsonResponse(400, { detail: "preflight found problems", findings })
    );

    const { submitFromRepo } = await import("./cloud-api");
    const err: unknown = await submitFromRepo("owner/repo").catch((e) => e);
    expect(err).toBeInstanceOf(PreflightRejected);
    expect((err as PreflightRejected).findings).toEqual(findings);
  });

  it("attaches the JWT and returns round history in order for a federated job", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const rounds = [
      {
        round: 0,
        participants: 2,
        mean_loss: 0.42,
        contributors: ["node-0", "node-1"],
        coordinator_job_id: "job-round-0",
        recorded_at: "2026-07-31T00:00:00Z",
      },
      {
        round: 1,
        participants: 2,
        mean_loss: null,
        contributors: ["node-0", "node-1"],
        coordinator_job_id: "job-round-1",
        recorded_at: "2026-07-31T00:05:00Z",
      },
    ];
    fetchMock.mockResolvedValue(jsonResponse(200, rounds));

    const result = await listJobRounds("fed-abc123");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${cloudApiBase()}/v1alpha1/jobs/fed-abc123/rounds`);
    expect(init.headers.Authorization).toBe(`Bearer ${SESSION.access_token}`);
    expect(result).toEqual(rounds);
    // Never coerced to 0 or dropped — a null mean_loss must round-trip as
    // null, not become a fabricated number.
    expect(result[1].mean_loss).toBeNull();
  });

  it("raises NotFound for another user's job rounds, never a 403-style message", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue(jsonResponse(404, { detail: "unknown job" }));

    const err: unknown = await listJobRounds("not-mine").catch((e) => e);
    expect(err).toBeInstanceOf(NotFound);
  });

  describe("submitFromRepo", () => {
    const okResponse = { job_id: "job-1", state: "PENDING", findings: [] };

    it("POSTs {repo} alone when neither ref nor pool is given", async () => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      fetchMock.mockResolvedValue(jsonResponse(200, okResponse));

      await submitFromRepo("owner/repo");

      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe(`${cloudApiBase()}/v1alpha1/jobs/from-repo`);
      expect(JSON.parse(init.body)).toEqual({ repo: "owner/repo" });
    });

    it("includes ref but omits pool when no pool is selected", async () => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      fetchMock.mockResolvedValue(jsonResponse(200, okResponse));

      await submitFromRepo("owner/repo", "main");

      const [, init] = fetchMock.mock.calls[0];
      const body = JSON.parse(init.body);
      expect(body).toEqual({ repo: "owner/repo", ref: "main" });
      expect(body).not.toHaveProperty("pool");
    });

    it("includes pool in the body when a pool is selected", async () => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      fetchMock.mockResolvedValue(jsonResponse(200, okResponse));

      await submitFromRepo("owner/repo", "main", "pool-1");

      const [, init] = fetchMock.mock.calls[0];
      expect(JSON.parse(init.body)).toEqual({
        repo: "owner/repo",
        ref: "main",
        pool: "pool-1",
      });
    });

    it("includes pool even when ref is left blank", async () => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      fetchMock.mockResolvedValue(jsonResponse(200, okResponse));

      await submitFromRepo("owner/repo", undefined, "pool-1");

      const [, init] = fetchMock.mock.calls[0];
      expect(JSON.parse(init.body)).toEqual({
        repo: "owner/repo",
        pool: "pool-1",
      });
    });
  });

  describe("listJobContributions", () => {
    it("attaches the bearer token and returns the per-machine credit rows", async () => {
      const rows = [
        {
          node_id: "node-0",
          machine_name: "Ada's laptop",
          member_display_name: "Ada Lovelace",
          tasks_credited: 3,
          total_duration_s: 120.5,
        },
      ];
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      fetchMock.mockResolvedValue(jsonResponse(200, rows));

      const result = await listJobContributions("job-1");

      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe(`${cloudApiBase()}/v1alpha1/jobs/job-1/contributions`);
      expect(init.headers.Authorization).toBe(`Bearer ${SESSION.access_token}`);
      expect(result).toEqual(rows);
    });

    it("returns an empty list for a non-pool job rather than an error", async () => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      fetchMock.mockResolvedValue(jsonResponse(200, []));

      const result = await listJobContributions("job-1");
      expect(result).toEqual([]);
    });

    it("raises NotFound for a job the caller cannot see, never a 403-style message", async () => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      fetchMock.mockResolvedValue(jsonResponse(404, { detail: "unknown job" }));

      const err: unknown = await listJobContributions("not-mine").catch((e) => e);
      expect(err).toBeInstanceOf(NotFound);
    });
  });

  it("names the URL it could not reach when fetch rejects", async () => {
    // A bare "Failed to fetch" is the same message for a wrong host, DNS,
    // CORS, mixed content and offline. The URL is the one fact that tells
    // them apart, and it is the one thing the raw error omits.
    vi.stubEnv("NEXT_PUBLIC_CLOUD_API", "https://api.example.com");
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(listMachines()).rejects.toThrow(
      "Failed to fetch — could not reach https://api.example.com/v1alpha1/machines",
    );
  });

  it("reads the base URL from NEXT_PUBLIC_CLOUD_API, not a hardcoded host", () => {
    vi.stubEnv("NEXT_PUBLIC_CLOUD_API", "https://cloud.example.com");
    expect(cloudApiBase()).toBe("https://cloud.example.com");
  });

  it("falls back to localhost:8000 when NEXT_PUBLIC_CLOUD_API is unset", () => {
    vi.stubEnv("NEXT_PUBLIC_CLOUD_API", "");
    delete process.env.NEXT_PUBLIC_CLOUD_API;
    expect(cloudApiBase()).toBe("http://localhost:8000");
  });

  // Render's Blueprint (`render.yaml`) resolves NEXT_PUBLIC_CLOUD_API via
  // `fromService: {property: host}`, which returns a bare hostname like
  // `flashml-api.onrender.com` — never a scheme. Handed to fetch() as-is,
  // that reads as a relative path: the site loads (this code path is never
  // exercised by a health check) and every request 404s against
  // `flashml-web.onrender.com/flashml-api.onrender.com/...`. These pin the
  // fix at the level that matters: the actual URL fetch() is called with.
  describe("scheme normalization for NEXT_PUBLIC_CLOUD_API", () => {
    it("prepends https:// to a scheme-less host and sends the request there", async () => {
      vi.stubEnv("NEXT_PUBLIC_CLOUD_API", "flashml-api.onrender.com");
      expect(cloudApiBase()).toBe("https://flashml-api.onrender.com");

      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      fetchMock.mockResolvedValue(jsonResponse(200, []));

      await listMachines();

      const [url] = fetchMock.mock.calls[0];
      expect(url).toBe("https://flashml-api.onrender.com/v1alpha1/machines");
    });

    it("leaves an explicit http://localhost base unchanged, so local dev keeps working", async () => {
      vi.stubEnv("NEXT_PUBLIC_CLOUD_API", "http://localhost:8000");
      expect(cloudApiBase()).toBe("http://localhost:8000");

      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      fetchMock.mockResolvedValue(jsonResponse(200, []));

      await listMachines();

      const [url] = fetchMock.mock.calls[0];
      expect(url).toBe("http://localhost:8000/v1alpha1/machines");
    });

    it("leaves an explicit https:// base unchanged", () => {
      vi.stubEnv("NEXT_PUBLIC_CLOUD_API", "https://cloud.example.com");
      expect(cloudApiBase()).toBe("https://cloud.example.com");
    });

    // An EMPTY value is not the same as an unset one, and `??` does not catch
    // it: `"" ?? fallback` is `""`. withDefaultScheme then returns "" too (its
    // `!url` guard), so cloudApiBase() yields "" and fetch() is handed a
    // RELATIVE url — every call silently goes to the web origin instead of the
    // API. Render produces exactly this when a `fromService` reference fails to
    // resolve, which is invisible at build time and surfaces to a signed-in
    // user as a bare "Failed to fetch".
    it("treats an EMPTY NEXT_PUBLIC_CLOUD_API as unset, never as a relative base", async () => {
      vi.stubEnv("NEXT_PUBLIC_CLOUD_API", "");
      expect(cloudApiBase()).not.toBe("");

      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      fetchMock.mockResolvedValue(jsonResponse(200, []));

      await listMachines();

      const [url] = fetchMock.mock.calls[0];
      expect(url).not.toMatch(/^\/v1alpha1/);
      expect(url).toBe("http://localhost:8000/v1alpha1/machines");
    });
  });

  describe("pools and invites", () => {
    it("attaches the bearer token on listPools", async () => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      fetchMock.mockResolvedValue(jsonResponse(200, []));

      await listPools();

      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe(`${cloudApiBase()}/v1alpha1/pools`);
      expect(init.headers.Authorization).toBe(`Bearer ${SESSION.access_token}`);
    });

    it("creates a pool by POSTing the trimmed-by-the-server name and returns it", async () => {
      // POST /v1alpha1/pools returns exactly POOL_PUBLIC_COLUMNS — no
      // member_count/machines_online (see Pool's docstring in
      // cloud-api.ts) — so this fixture omits them too, rather than
      // asserting against a shape the real route never sends.
      const pool = {
        id: "pool-1",
        name: "Lab",
        owner_id: "user-1",
        created_at: "2026-08-03T00:00:00Z",
      };
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      fetchMock.mockResolvedValue(jsonResponse(201, pool));

      const result = await createPool("Lab");

      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe(`${cloudApiBase()}/v1alpha1/pools`);
      expect(init.method).toBe("POST");
      expect(JSON.parse(init.body)).toEqual({ name: "Lab" });
      expect(result).toEqual(pool);
    });

    it("reshapes GET /pools/{id}'s flat response into {pool, members}, not a nested pool field the API never sends", async () => {
      const members = [
        {
          user_id: "user-1",
          display_name: "Ada",
          joined_at: "2026-08-01T00:00:00Z",
          machine_count: 2,
          machines_online: 1,
        },
      ];
      // The API returns the pool's own columns flattened alongside
      // `members` — see `get_pool_route`'s `{**_jsonable(pool), "members": ...}`
      // — never `{pool: {...}, members: [...]}`.
      const flat = {
        id: "pool-1",
        name: "Lab",
        owner_id: "user-1",
        created_at: "2026-08-03T00:00:00Z",
        members,
      };
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      fetchMock.mockResolvedValue(jsonResponse(200, flat));

      const result = await getPool("pool-1");

      const [url] = fetchMock.mock.calls[0];
      expect(url).toBe(`${cloudApiBase()}/v1alpha1/pools/pool-1`);
      expect(result).toEqual({
        pool: {
          id: "pool-1",
          name: "Lab",
          owner_id: "user-1",
          created_at: "2026-08-03T00:00:00Z",
        },
        members,
      });
    });

    it("POSTs to the pool's invites route with no body and returns the token verbatim, without ever logging or echoing it elsewhere", async () => {
      const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
      const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      fetchMock.mockResolvedValue(jsonResponse(201, { token: "fmi_secret123" }));

      const result = await createPoolInvite("pool-1");

      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe(`${cloudApiBase()}/v1alpha1/pools/pool-1/invites`);
      expect(init.method).toBe("POST");
      expect(init.body).toBeUndefined();
      expect(result).toEqual({ token: "fmi_secret123" });
      expect(logSpy).not.toHaveBeenCalled();
      expect(errorSpy).not.toHaveBeenCalled();

      logSpy.mockRestore();
      errorSpy.mockRestore();
    });

    it("POSTs {token} to /v1alpha1/invites/accept and returns the admitted pool", async () => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      fetchMock.mockResolvedValue(
        jsonResponse(200, { pool_id: "pool-1", name: "Lab" })
      );

      const result = await acceptInvite("fmi_secret123");

      const [url, init] = fetchMock.mock.calls[0];
      expect(url).toBe(`${cloudApiBase()}/v1alpha1/invites/accept`);
      expect(init.method).toBe("POST");
      expect(JSON.parse(init.body)).toEqual({ token: "fmi_secret123" });
      expect(result).toEqual({ pool_id: "pool-1", name: "Lab" });
    });

    it("raises NotFound (not a 403-shaped message) for an unknown or expired invite token", async () => {
      const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
      fetchMock.mockResolvedValue(
        jsonResponse(404, { detail: "invalid or expired invite" })
      );

      const err: unknown = await acceptInvite("bad-token").catch((e) => e);
      expect(err).toBeInstanceOf(NotFound);
    });
  });
});
