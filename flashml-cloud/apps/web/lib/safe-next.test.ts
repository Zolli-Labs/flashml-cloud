import { describe, expect, it } from "vitest";
import { safeNext } from "./safe-next";

describe("safeNext", () => {
  it("keeps a normal same-origin path", () => {
    expect(safeNext("/machines")).toBe("/machines");
  });

  it("keeps a path with a query string attached", () => {
    expect(safeNext("/pools/join?token=fmi_abc")).toBe(
      "/pools/join?token=fmi_abc"
    );
  });

  it("falls back to /machines for a protocol-relative //evil.com", () => {
    expect(safeNext("//evil.com")).toBe("/machines");
  });

  it("falls back to /machines for a protocol-relative //evil.com with a path", () => {
    expect(safeNext("//evil.com/foo")).toBe("/machines");
  });

  it("falls back to /machines for an absolute https://evil.com URL", () => {
    expect(safeNext("https://evil.com")).toBe("/machines");
  });

  it("falls back to /machines for a scheme-relative javascript: value", () => {
    expect(safeNext("javascript:alert(1)")).toBe("/machines");
  });

  it("falls back to /machines for an empty string", () => {
    expect(safeNext("")).toBe("/machines");
  });

  it("falls back to /machines for null", () => {
    expect(safeNext(null)).toBe("/machines");
  });

  it("falls back to /machines for undefined", () => {
    expect(safeNext(undefined)).toBe("/machines");
  });

  it("falls back to /machines for a path with no leading slash", () => {
    expect(safeNext("evil.com/foo")).toBe("/machines");
  });
});
