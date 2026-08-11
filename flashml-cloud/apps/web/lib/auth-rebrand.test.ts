import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AuthShell } from "@/components/auth/AuthShell";

function renderAuth(mode: "signin" | "signup") {
  return renderToStaticMarkup(
    createElement(
      AuthShell,
      { mode, onModeChange: () => undefined },
      createElement("span", null, "Form fields"),
    ),
  );
}

describe("warm technical authentication", () => {
  it("frames sign-in as returning to durable compute state", () => {
    const markup = renderAuth("signin");

    expect(markup).toContain("Your compute, exactly where it left off.");
    expect(markup).toContain("Leases · checkpoints · deterministic recovery");
    expect(markup).not.toContain("Crew");
  });

  it("uses direct account copy for sign-up", () => {
    const markup = renderAuth("signup");

    expect(markup).toContain("Create your account.");
    expect(markup).toContain("Start with the machines you have.");
    expect(markup).not.toContain("Aurora");
  });
});
