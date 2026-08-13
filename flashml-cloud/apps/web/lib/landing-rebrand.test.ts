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
  it("leads with the approved open-compute promise", () => {
    const markup = visibleText(renderLanding());

    expect(markup).toContain("Computing power, without the lock-in.");
    expect(markup).toContain(
      "One open network connecting people who need compute with machines ready to work.",
    );
  });

  it("offers demand and supply actions in the hero", () => {
    const markup = renderLanding();

    expect(markup).toContain("Get early access");
    expect(markup).toContain("Provide compute");
    expect(markup).toContain('href="/workspaces"');
    expect(markup).toContain('href="/account/machines"');
    expect(markup.indexOf("Get early access")).toBeLessThan(markup.indexOf("Provide compute"));
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

  it("keeps the technical story tied to one accepted outcome", () => {
    const markup = visibleText(renderLanding());

    expect(markup).toContain("The machinery behind a reliable market.");
    expect(markup).toContain(
      "A sandboxed host lane, a parallel runtime, and a checkpoint recovery path — three separate contracts, one accepted outcome.",
    );
    for (const lane of ["01 Host", "02 Runtime", "03 Recovery"])
      expect(markup).toContain(lane);
  });
});
