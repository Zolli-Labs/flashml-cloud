import { describe, expect, it } from "vitest";

import { summariseStorage } from "./account-storage";
import type { AccountStorage } from "./cloud-api";

describe("summariseStorage", () => {
  it("treats a null limit as its own unlimited state, not a limit of 0", () => {
    // `limit_bytes: null` means unlimited and `percent_used` is null in
    // exactly the same case. Rendering an empty (0%) bar here would imply a
    // limit exists and is barely touched, which is the opposite of the
    // truth — so this must branch, not fall through to the numeric path.
    const storage: AccountStorage = {
      used_bytes: 268_435_456,
      limit_bytes: null,
      percent_used: null,
    };
    const display = summariseStorage(storage);
    expect(display.unlimited).toBe(true);
    expect(display.limitLabel).toBeNull();
    expect(display.percent).toBeNull();
    expect(display.usedLabel).toBe("256.0 MiB");
  });

  it("reports usage against a real limit as a percentage of it", () => {
    const storage: AccountStorage = {
      used_bytes: 268_435_456,
      limit_bytes: 2_147_483_648,
      percent_used: 12.5,
    };
    const display = summariseStorage(storage);
    expect(display.unlimited).toBe(false);
    expect(display.usedLabel).toBe("256.0 MiB");
    expect(display.limitLabel).toBe("2.0 GiB");
    expect(display.percent).toBe(12.5);
  });

  it("passes through a server-clamped 100% rather than recomputing it", () => {
    // The contract says percent_used is already clamped to 100 server-side
    // — this must not independently derive or re-clamp it from used/limit.
    const storage: AccountStorage = {
      used_bytes: 4_294_967_296,
      limit_bytes: 2_147_483_648,
      percent_used: 100,
    };
    const display = summariseStorage(storage);
    expect(display.percent).toBe(100);
  });

  it("renders zero usage on a limited account as a real, present 0%, not unlimited", () => {
    const storage: AccountStorage = {
      used_bytes: 0,
      limit_bytes: 2_147_483_648,
      percent_used: 0,
    };
    const display = summariseStorage(storage);
    expect(display.unlimited).toBe(false);
    expect(display.percent).toBe(0);
    expect(display.usedLabel).toBe("0 B");
  });
});
