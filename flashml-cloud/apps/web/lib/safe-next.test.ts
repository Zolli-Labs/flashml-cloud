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

  // The backslash bypass: a single leading "/" is not sufficient on its
  // own, because a browser's URL resolver treats "\" the same as "/" when
  // resolving a relative reference. `/\evil.com/steal` passed a naive
  // startsWith("/") && !startsWith("//") check but still resolves off-site.
  it("falls back to /machines for the backslash bypass /\\evil.com", () => {
    expect(safeNext("/\\evil.com")).toBe("/machines");
  });

  it("falls back to /machines for a backslash bypass with a path, /\\evil.com/steal", () => {
    expect(safeNext("/\\evil.com/steal")).toBe("/machines");
  });

  it("falls back to /machines for a doubled-backslash bypass /\\\\evil.com", () => {
    // Value under test: "/" + two literal backslashes + "evil.com" — still
    // just "the character right after the leading slash is a backslash",
    // which the guard rejects regardless of how many follow it.
    expect(safeNext("/\\\\evil.com")).toBe("/machines");
  });

  it("falls back to /machines for a value that starts with a backslash, not a slash", () => {
    expect(safeNext("\\/evil.com")).toBe("/machines");
  });

  // Pinned separately: a bare "/" has no character after the leading
  // slash at all, so it fails the same two-character check that catches
  // "//" and "/\\" — worth a named case rather than leaving it as an
  // unstated side effect of the regex.
  it("falls back to /machines for a bare '/' with nothing after it", () => {
    expect(safeNext("/")).toBe("/machines");
  });

  it("accepts a custom fallback for callers whose default next isn't /machines", () => {
    expect(safeNext("//evil.com", "/overview")).toBe("/overview");
    expect(safeNext(null, "/overview")).toBe("/overview");
  });

  it("still returns a legitimate path unchanged when a custom fallback is given", () => {
    expect(safeNext("/pools/join?token=fmi_abc", "/overview")).toBe(
      "/pools/join?token=fmi_abc"
    );
  });

  // The tab/newline bypass: the WHATWG URL parser a browser uses to resolve
  // a navigation strips ASCII tab/LF/CR before parsing, so "/\t/evil.com"
  // passes a naive /^\/[^/\\]/ check (the second character is "\t", neither
  // "/" nor "\") but the browser deletes the tab first and resolves the
  // protocol-relative "//evil.com" — leaving the site exactly as surely as
  // the backslash bypass above. The guard must check the stripped string.
  it("falls back to /machines for the tab bypass /\\t/evil.com", () => {
    expect(safeNext("/\t/evil.com")).toBe("/machines");
  });

  it("falls back to /machines for the newline bypass /\\n/evil.com", () => {
    expect(safeNext("/\n/evil.com")).toBe("/machines");
  });

  it("falls back to /machines for the carriage-return bypass /\\r/evil.com", () => {
    expect(safeNext("/\r/evil.com")).toBe("/machines");
  });

  it("returns a legitimate path byte-identical when it has no tab/newline/CR", () => {
    const legit = "/pools/join?token=fmi_abc";
    expect(safeNext(legit)).toBe(legit);
  });
});
