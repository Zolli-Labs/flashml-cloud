import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Home from "@/app/(marketing)/page";

function renderLanding() {
  return renderToStaticMarkup(createElement(Home));
}

function visibleText(markup: string) {
  return markup.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

describe("warm technical landing", () => {
  it("leads with the approved fault-tolerant compute promise", () => {
    const markup = visibleText(renderLanding());

    expect(markup).toContain("Compute that finishes the job.");
    expect(markup).toContain(
      "Zolli unifies compatible cloud capacity, rented compute, owned GPU infrastructure, and everyday machines under one control plane, then recovers work when a node disappears.",
    );
  });

  it("offers the console and Zolli consultation as the hero actions", () => {
    const markup = renderLanding();

    expect(markup).toContain("Open console");
    expect(markup).toContain("Talk to Zolli");
    expect(markup).toContain('href="/workspaces"');
    expect(markup).toContain('href="https://calendly.com/phongct1105/zolli-ai"');
    expect(markup.indexOf("Open console")).toBeLessThan(markup.indexOf("Talk to Zolli"));
  });

  it("keeps the evaluation and legal paths in the same public story", () => {
    const markup = renderLanding();

    for (const destination of [
      'href="/contact"',
      'href="/privacy"',
      'href="/terms"',
      'href="/security"',
      'href="#faq"',
    ]) expect(markup).toContain(destination);
  });

  it("explains technical system modules instead of mascot roles", () => {
    const markup = renderLanding();

    for (const moduleName of [
      "Coordinate",
      "Execute",
      "Enroll",
      "Checkpoint",
      "Recover",
      "Verify",
    ]) {
      expect(markup).toContain(moduleName);
    }

    expect(markup).not.toContain("Meet the crew");
    expect(markup).not.toContain("Captain assigns");
  });

  it("presents protocol evidence with an explicit sample-data disclosure", () => {
    const markup = renderLanding();

    expect(markup).toContain("NODE_HEARTBEAT_LOST");
    expect(markup).toContain("CHECKPOINT_MANIFEST_COMMITTED");
    expect(markup).toContain("sample data");
  });
});
