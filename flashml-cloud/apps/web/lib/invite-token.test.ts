import { describe, expect, it } from "vitest";
import { tokenFromInput } from "./invite-token";

describe("tokenFromInput", () => {
  it("returns null for an empty string", () => {
    expect(tokenFromInput("")).toBeNull();
  });

  it("returns null for whitespace-only input", () => {
    expect(tokenFromInput("   \n\t  ")).toBeNull();
  });

  it("extracts the token from a full invite link", () => {
    expect(
      tokenFromInput("https://console.flashml.dev/pools/join?token=fmi_abc123")
    ).toBe("fmi_abc123");
  });

  it("extracts the token from a link with extra query params around it", () => {
    expect(
      tokenFromInput(
        "https://console.flashml.dev/pools/join?ref=email&token=fmi_abc123&utm_source=x"
      )
    ).toBe("fmi_abc123");
  });

  it("extracts the token from a localhost dev link", () => {
    expect(
      tokenFromInput("http://localhost:3000/pools/join?token=fmi_dev999")
    ).toBe("fmi_dev999");
  });

  it("trims surrounding whitespace off a pasted link", () => {
    expect(
      tokenFromInput("  https://console.flashml.dev/pools/join?token=fmi_abc123  ")
    ).toBe("fmi_abc123");
  });

  it("decodes a percent-encoded token in the query string", () => {
    expect(
      tokenFromInput("https://console.flashml.dev/pools/join?token=fmi_a%2Bb")
    ).toBe("fmi_a+b");
  });

  it("returns null for a valid URL with no token parameter", () => {
    expect(tokenFromInput("https://console.flashml.dev/pools/join")).toBeNull();
  });

  it("reads a bare token=... fragment that isn't a full URL", () => {
    expect(tokenFromInput("token=fmi_abc123")).toBe("fmi_abc123");
  });

  it("reads a token=... fragment preceded by other params", () => {
    expect(tokenFromInput("ref=email&token=fmi_abc123")).toBe("fmi_abc123");
  });

  it("treats a bare token with no URL shape as itself, trimmed", () => {
    expect(tokenFromInput("  fmi_abc123  ")).toBe("fmi_abc123");
  });

  it("treats an arbitrary pasted string with no recognizable token shape as itself", () => {
    expect(tokenFromInput("not-a-token-just-text")).toBe("not-a-token-just-text");
  });
});
