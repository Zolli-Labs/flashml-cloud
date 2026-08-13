import { createElement } from "react";
import { prerenderToNodeStream } from "react-dom/static.node";
import { describe, expect, it } from "vitest";
import Home from "@/app/(marketing)/page";

// `Home` renders `PriceBoard`, an async server component, so the classic
// synchronous `renderToStaticMarkup` throws the moment it reaches it.
// `prerender` is the React 19 API built to resolve async Server Components
// before handing back a static stream — see the longer note in
// `lib/landing-cinematic.test.ts`.
async function streamToString(stream: NodeJS.ReadableStream): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(Buffer.from(chunk as Uint8Array));
  return Buffer.concat(chunks).toString("utf8");
}
async function renderLanding() {
  const { prelude } = await prerenderToNodeStream(createElement(Home));
  return streamToString(prelude);
}

function visibleText(markup: string) {
  return markup.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
}

describe("warm technical landing", () => {
  it("leads with the approved open-compute promise", async () => {
    const markup = visibleText(await renderLanding());

    expect(markup).toContain("Computing power, without the lock-in.");
    expect(markup).toContain(
      "One open network connecting people who need compute with machines ready to work.",
    );
  });

  it("offers demand and supply actions in the hero", async () => {
    const markup = await renderLanding();

    expect(markup).toContain("Get early access");
    expect(markup).toContain("Provide compute");
    expect(markup).toContain('href="/workspaces"');
    expect(markup).toContain('href="/account/machines"');
    expect(markup.indexOf("Get early access")).toBeLessThan(markup.indexOf("Provide compute"));
  });

  it("keeps the evaluation and legal paths in the same public story", async () => {
    const markup = await renderLanding();

    for (const destination of [
      'href="/contact"',
      'href="/privacy"',
      'href="/terms"',
      'href="/security"',
      'href="#faq"',
    ]) expect(markup).toContain(destination);
  });

  it("explains technical module facts instead of mascot roles", async () => {
    const markup = await renderLanding();

    // The elaborate three-lane module grid (Coordinate / Execute / Enroll /
    // Checkpoint / Recover / Verify) is cut, not merged — `HowItWorks` keeps
    // only the two facts worth keeping, in prose, not as a protocol-event list.
    expect(markup).toContain("flashnode");
    expect(markup).toContain("DDP");
    expect(markup).toContain("FSDP");
    expect(markup).not.toContain("Meet the crew");
    expect(markup).not.toContain("Captain assigns");
  });

  it("presents protocol evidence with an explicit sample-data disclosure", async () => {
    const markup = await renderLanding();

    expect(markup).toContain("NODE_HEARTBEAT_LOST");
    expect(markup).toContain("CHECKPOINT_MANIFEST_COMMITTED");
    expect(markup).toContain("sample data");
  });
});
