import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// `fetchLandingPrices` resolves its base URL through `cloudApiBase()` in
// `lib/cloud-api.ts`, which imports the Supabase browser client. Nothing on
// this path ever constructs one — the whole point of a public landing fetch
// is that it needs no session — but mock the boundary anyway, matching
// `components/share/share-page.test.tsx`: building a real client probes for
// a native WebSocket at CONSTRUCTION time and fails on Node 20.
vi.mock("@/lib/supabase", () => ({
  createBrowserSupabaseClient: () => ({ auth: { getSession: vi.fn() } }),
}));

import {
  capturedAbsoluteLabel,
  capturedLabel,
  fetchLandingPrices,
  trendGlyph,
  trendToneClass,
  type PriceBoardRow,
} from "./market-board";

function row(over: Partial<PriceBoardRow> = {}): PriceBoardRow {
  return {
    gpu: "RTX 4090",
    sku: "NVIDIA GeForce RTX 4090",
    provider: "runpod",
    tier: "community",
    amount: "0.34",
    currency: "USD",
    unit: "gpu-hour",
    captured_at: "2026-08-13T12:00:00Z",
    age_seconds: 1234,
    stale: false,
    trend: {
      direction: "down",
      pct: "-5.6",
      previous_amount: "0.36",
      previous_captured_at: "2026-08-12T12:00:00Z",
    },
    ...over,
  };
}

describe("trendGlyph", () => {
  it("maps up/down/flat to their glyphs and new to a text chip", () => {
    expect(trendGlyph("up")).toBe("▲");
    expect(trendGlyph("down")).toBe("▼");
    expect(trendGlyph("flat")).toBe("–");
    expect(trendGlyph("new")).toBe("NEW");
  });
});

describe("trendToneClass", () => {
  it("a rising price is a warning — bad news for a buyer deciding where to place a job", () => {
    expect(trendToneClass("up")).toBe("text-[var(--z-warning)]");
  });

  it("a falling price is healthy — good news for the buyer, the opposite of a stock ticker's color", () => {
    expect(trendToneClass("down")).toBe("text-[var(--z-healthy)]");
  });

  it("flat and new are neutral, not colored either direction", () => {
    expect(trendToneClass("flat")).toBe("text-muted-foreground");
    expect(trendToneClass("new")).toBe("text-muted-foreground");
  });
});

describe("capturedLabel", () => {
  it("reuses lib/market-prices's ageLabel verbatim, prefixed 'observed', when not stale", () => {
    expect(capturedLabel(row({ age_seconds: 7200, stale: false }))).toBe("observed 2h ago");
    expect(capturedLabel(row({ age_seconds: 30, stale: false }))).toBe("observed 30s ago");
  });

  it("prefixes 'stale — ' when the API's own stale verdict is true — the same convention lib/market-prices.ts's quoteRow uses for capturedText", () => {
    expect(capturedLabel(row({ age_seconds: 7200, stale: true }))).toBe("stale — observed 2h ago");
    expect(capturedLabel(row({ age_seconds: 90_000, stale: true }))).toBe(
      "stale — observed 1d ago",
    );
  });

  it("never claims 'live', even for a very fresh row", () => {
    expect(capturedLabel(row({ age_seconds: 1 }))).not.toContain("live");
  });
});

describe("capturedAbsoluteLabel", () => {
  it("renders the UTC capture date and time, independent of the reader's clock or timezone", () => {
    expect(capturedAbsoluteLabel(row({ captured_at: "2026-08-13T12:00:00Z" }))).toBe(
      "2026-08-13 12:00 UTC",
    );
  });

  it("pads single-digit month, day, hour and minute", () => {
    expect(capturedAbsoluteLabel(row({ captured_at: "2026-01-02T03:04:00Z" }))).toBe(
      "2026-01-02 03:04 UTC",
    );
  });

  it("drops seconds — the same precision the worked example in the finding uses", () => {
    expect(capturedAbsoluteLabel(row({ captured_at: "2026-08-13T12:00:45Z" }))).toBe(
      "2026-08-13 12:00 UTC",
    );
  });

  it("falls back to the raw string rather than throwing on an unparseable captured_at", () => {
    expect(capturedAbsoluteLabel(row({ captured_at: "not-a-date" }))).toBe("not-a-date");
  });
});

describe("fetchLandingPrices", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
    vi.stubEnv("NEXT_PUBLIC_CLOUD_API", "http://localhost:8000");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("fetches the public prices endpoint with a 300s revalidate window", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ generated_at: "2026-08-13T12:00:00Z", rows: [row()] }),
    });

    const result = await fetchLandingPrices();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/v1alpha1/public/prices");
    expect(init).toMatchObject({ next: { revalidate: 300 } });
    expect(result?.rows).toHaveLength(1);
    expect(result?.rows[0].gpu).toBe("RTX 4090");
  });

  it("returns an empty board — not null — for a real, empty rows array", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ generated_at: "2026-08-13T12:00:00Z", rows: [] }),
    });

    expect(await fetchLandingPrices()).toEqual({
      generated_at: "2026-08-13T12:00:00Z",
      rows: [],
    });
  });

  it("returns null when fetch itself rejects — a network failure, never a throw", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(fetchLandingPrices()).resolves.toBeNull();
  });

  it("returns null on a non-200 response", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ detail: "internal error" }),
    });

    expect(await fetchLandingPrices()).toBeNull();
  });

  it("returns null when the body is not valid JSON", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError("Unexpected token");
      },
    });

    expect(await fetchLandingPrices()).toBeNull();
  });

  it("returns null on a shape surprise — a row missing required fields", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        generated_at: "2026-08-13T12:00:00Z",
        rows: [{ gpu: "RTX 4090" }],
      }),
    });

    expect(await fetchLandingPrices()).toBeNull();
  });

  it("accepts a 'new' trend row that legitimately carries no other trend fields", async () => {
    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        generated_at: "2026-08-13T12:00:00Z",
        rows: [row({ trend: { direction: "new" } })],
      }),
    });

    const result = await fetchLandingPrices();
    expect(result?.rows[0].trend).toEqual({ direction: "new" });
  });
});
