import { describe, expect, it } from "vitest";
import { NOT_OBSERVED, statTileSuffix, statTileValue } from "./stat-tile";

describe("statTileValue", () => {
  it("renders null as NOT_OBSERVED, never as a fabricated 0", () => {
    expect(statTileValue(null)).toBe(NOT_OBSERVED);
  });

  it("renders undefined as NOT_OBSERVED", () => {
    expect(statTileValue(undefined)).toBe(NOT_OBSERVED);
  });

  it("renders a real 0 as 0, not as not-observed", () => {
    expect(statTileValue(0)).toBe("0");
  });

  it("stringifies a positive number", () => {
    expect(statTileValue(42)).toBe("42");
  });

  it("passes a pre-formatted string through unchanged", () => {
    expect(statTileValue("3 of 5")).toBe("3 of 5");
    expect(statTileValue(NOT_OBSERVED)).toBe(NOT_OBSERVED);
  });
});

describe("statTileSuffix", () => {
  it("omits the suffix when total is null", () => {
    expect(statTileSuffix(3, null)).toBeNull();
  });

  it("omits the suffix when total is undefined", () => {
    expect(statTileSuffix(3, undefined)).toBeNull();
  });

  it("omits the suffix when value is null", () => {
    expect(statTileSuffix(null, 5)).toBeNull();
  });

  it("omits the suffix when value equals total", () => {
    expect(statTileSuffix(5, 5)).toBeNull();
  });

  it("renders /total when value and total differ", () => {
    expect(statTileSuffix(3, 5)).toBe("/5");
  });

  it("renders /0 when total is a real, distinct zero", () => {
    expect(statTileSuffix(1, 0)).toBe("/0");
  });
});
