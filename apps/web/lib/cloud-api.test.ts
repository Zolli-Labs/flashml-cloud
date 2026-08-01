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
  approveDeviceCode,
  getJob,
  listMachines,
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

  it("reads the base URL from NEXT_PUBLIC_CLOUD_API, not a hardcoded host", () => {
    vi.stubEnv("NEXT_PUBLIC_CLOUD_API", "https://cloud.example.com");
    expect(cloudApiBase()).toBe("https://cloud.example.com");
  });

  it("falls back to localhost:8000 when NEXT_PUBLIC_CLOUD_API is unset", () => {
    vi.stubEnv("NEXT_PUBLIC_CLOUD_API", "");
    // @ts-expect-error -- deliberately deleting for the fallback case
    delete process.env.NEXT_PUBLIC_CLOUD_API;
    expect(cloudApiBase()).toBe("http://localhost:8000");
  });
});
