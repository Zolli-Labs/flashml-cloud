import { describe, expect, it } from "vitest";

import {
  deleteNotice,
  machineLabel,
  revokedLabel,
  splitFleet,
} from "./machine-lifecycle";

const RECENT = new Date(Date.now() - 5_000).toISOString();
const STALE = new Date(Date.now() - 60 * 60 * 1000).toISOString();

function machine(
  overrides: Partial<{
    id: string;
    status: string;
    last_seen_at: string | null;
  }> = {}
) {
  return {
    id: "m-1",
    status: "active",
    last_seen_at: RECENT,
    ...overrides,
  };
}

describe("splitFleet", () => {
  it("puts revoked machines in their own pile and leaves the rest enrolled", () => {
    const split = splitFleet([
      machine({ id: "a", status: "active" }),
      machine({ id: "b", status: "revoked", last_seen_at: STALE }),
      machine({ id: "c", status: "pending", last_seen_at: null }),
    ]);
    expect(split.enrolled.map((m) => m.id)).toEqual(["a", "c"]);
    expect(split.revoked.map((m) => m.id)).toEqual(["b"]);
  });

  it("does not count a revoked machine as enrolled, however recently it was seen", () => {
    // The header count reads "N ENROLLED" directly under "N ONLINE NOW". A
    // revoked machine is neither.
    const split = splitFleet([
      machine({ id: "a", status: "active" }),
      machine({ id: "b", status: "revoked", last_seen_at: RECENT }),
      machine({ id: "c", status: "revoked", last_seen_at: RECENT }),
    ]);
    expect(split.enrolled).toHaveLength(1);
    expect(split.online).toBe(1);
  });

  it("counts only enrolled machines seen recently as online", () => {
    const split = splitFleet([
      machine({ id: "a", last_seen_at: RECENT }),
      machine({ id: "b", last_seen_at: STALE }),
      machine({ id: "c", last_seen_at: null }),
    ]);
    expect(split.enrolled).toHaveLength(3);
    expect(split.online).toBe(1);
  });

  it("drops a deleted tombstone from both piles rather than calling it enrolled", () => {
    // The fleet listing stops returning a deleted row, so this only happens
    // when the console is newer or older than the API it is talking to.
    // Counting a retired machine as capacity is the one wrong answer that
    // shows up as a number.
    const split = splitFleet([
      machine({ id: "a", status: "active" }),
      machine({ id: "gone", status: "deleted", last_seen_at: null }),
    ]);
    expect(split.enrolled.map((m) => m.id)).toEqual(["a"]);
    expect(split.revoked).toEqual([]);
  });

  it("preserves the API's order within each pile", () => {
    const split = splitFleet([
      machine({ id: "1", status: "revoked" }),
      machine({ id: "2", status: "active" }),
      machine({ id: "3", status: "revoked" }),
      machine({ id: "4", status: "active" }),
    ]);
    expect(split.enrolled.map((m) => m.id)).toEqual(["2", "4"]);
    expect(split.revoked.map((m) => m.id)).toEqual(["1", "3"]);
  });

  it("answers an empty fleet with empty piles and a counted zero", () => {
    const split = splitFleet([]);
    expect(split).toEqual({ enrolled: [], revoked: [], online: 0 });
  });
});

describe("machineLabel", () => {
  it("prefers the machine's own name", () => {
    expect(machineLabel({ name: "gpu-box", node_id: "fn-abc123" })).toBe(
      "gpu-box"
    );
  });

  it("falls back to the node id when the agent reported no name", () => {
    expect(machineLabel({ name: null, node_id: "fn-abc123" })).toBe("fn-abc123");
  });

  it("treats an empty name as no name, not as a blank label", () => {
    expect(machineLabel({ name: "", node_id: "fn-abc123" })).toBe("fn-abc123");
  });
});

describe("revokedLabel", () => {
  it("says how long ago it was revoked", () => {
    expect(revokedLabel({ revoked_at: STALE })).toBe("revoked 1h ago");
  });

  it("never renders 'revoked never' for a row with no recorded moment", () => {
    // `relativeTime(null)` is "never", and "revoked never" says the opposite
    // of the row it sits on.
    expect(revokedLabel({ revoked_at: null })).toBe("revoked");
  });
});

describe("deleteNotice", () => {
  it("says nothing after a successful delete — the row leaving is the answer", () => {
    expect(deleteNotice({ kind: "deleted" }, "gpu-box")).toBeNull();
  });

  it("names the machine that was already gone", () => {
    expect(deleteNotice({ kind: "already-gone" }, "gpu-box")).toBe(
      "gpu-box was already deleted."
    );
  });

  it("carries the API's own words through a failure, verbatim", () => {
    const notice = deleteNotice(
      { kind: "failed", detail: "this machine is still enrolled — revoke it first" },
      "gpu-box"
    );
    expect(notice).toContain("gpu-box");
    expect(notice).toContain("still enrolled — revoke it first");
  });
});
