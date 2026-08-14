import { readFileSync } from "node:fs";
import { createElement } from "react";
import { prerenderToNodeStream } from "react-dom/static.node";
import { describe, expect, it, vi } from "vitest";
import Home from "@/app/(marketing)/page";
import { HowItWorks } from "@/components/landing/HowItWorks";

// `Home` renders `PriceBoard`, which fetches `GET /v1alpha1/public/prices`
// server-side (`lib/landing/market-board.ts`'s `fetchLandingPrices`) against
// `http://localhost:8000` by default. Unstubbed, every `renderLanding()` call
// below is a real network attempt — today a fast ECONNREFUSED against a
// coordinator that isn't running, but a listener on that port (a stray dev
// server) would hang the whole suite waiting on a response nobody sends.
// Stubbed the same way `lib/landing/market-board.test.ts` stubs it: a
// rejected promise, which `fetchLandingPrices` already turns into `null` —
// the empty-board state every one of this file's assertions already treats
// as a normal render.
vi.stubGlobal(
  "fetch",
  vi.fn().mockRejectedValue(new Error("stubbed: tests never hit the network")),
);

const root = process.cwd();
const source = (path: string) => readFileSync(`${root}/${path}`, "utf8");

// `Home` renders `PriceBoard`, an async server component, so the classic
// synchronous `renderToStaticMarkup` throws ("A component suspended while
// responding to synchronous input") the moment it reaches it. `prerender`
// is the React 19 API built for exactly this — resolve every async Server
// Component in the tree, then hand back a static stream. Production never
// takes this path (Next's own RSC pipeline resolves `PriceBoard` before
// `react-dom/server` ever sees the tree); this is a test-only substitute for
// that pipeline, not a change to how the page itself is written.
async function streamToString(stream: NodeJS.ReadableStream): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(Buffer.from(chunk as Uint8Array));
  return Buffer.concat(chunks).toString("utf8");
}
const renderLanding = async () => {
  const { prelude } = await prerenderToNodeStream(createElement(Home));
  return streamToString(prelude);
};
const visibleText = (markup: string) =>
  markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ").trim();
const renderHowItWorks = async () => {
  const { prelude } = await prerenderToNodeStream(createElement(HowItWorks));
  return streamToString(prelude);
};

describe("cinematic landing foundation", () => {
  it("pins the approved motion packages", () => {
    const pkg = JSON.parse(source("package.json"));
    expect(pkg.dependencies.gsap).toBe("3.15.0");
    expect(pkg.dependencies["@gsap/react"]).toBe("2.1.2");
    expect(pkg.dependencies.motion).toBe("^12.42.2");
  });

  it("contains landing motion without hiding server content", async () => {
    const markup = await renderLanding();
    expect(markup).toContain('data-landing="cinematic"');
    expect(markup).toContain("Computing power,");
    expect(markup).toContain("without the lock-in.");
    expect(markup).toContain('data-market-role="demand"');
    expect(markup).toContain('data-market-role="supply"');
    expect(markup).not.toContain('style="opacity:0"');
  });

  it("defines all four landing surfaces and reduced-motion fallback", () => {
    const css = source("app/globals.css");
    for (const token of [
      // Warm-black now — the landing's dark register was retinted to match
      // the console's warm-black family (was cool graphite, #0b0d0e).
      "--landing-graphite: #131110",
      "--landing-ivory: #f2efe6",
      "--landing-sand: #ded8cb",
      "--landing-orange: #f36b32",
    ]) expect(css).toContain(token);
    expect(css).toContain(".landing-surface-light");
    expect(css).toContain(".landing-surface-sand");
    expect(css).toContain(".landing-surface-orange");
    expect(css).toContain("prefers-reduced-motion: reduce");
  });

  it("places the hero's reduced-motion rules after its animated rules", () => {
    const css = source("app/globals.css");
    const animatedSwitch = css.indexOf(
      ".hero-market-switch {\n  display: grid;",
    );
    const animatedRole = css.indexOf(
      ".hero-market-role {\n  grid-area: 1 / 1;",
    );
    const reducedMotion = css.indexOf(
      "@media (prefers-reduced-motion: reduce) {\n  .hero-market-switch {\n    display: block;",
    );
    const reducedRole = css.indexOf(
      ".hero-market-role {\n    animation: none;",
      reducedMotion,
    );

    expect(animatedSwitch).toBeGreaterThanOrEqual(0);
    expect(animatedRole).toBeGreaterThanOrEqual(0);
    expect(reducedMotion).toBeGreaterThan(animatedSwitch);
    expect(reducedRole).toBeGreaterThan(animatedRole);
  });

  it("renders the editorial hero and the live coordinator map", async () => {
    const markup = await renderLanding();
    const hero = markup.match(/<section[^>]*id="hero"[\s\S]*?<\/section>/)?.[0] ?? "";
    expect(hero).toContain('data-surface="dark"');
    // The map is the hero's illustration: the coordinator at the centre, the
    // four sources around it, and the readout strip that makes it a running
    // system rather than a diagram.
    expect(hero).toMatch(/data-coordinator-map="(?:running|lost|resumed|accepted)"/);
    expect(hero).toContain('data-map-core="coordinator"');
    expect(hero.match(/data-map-node=/g) ?? []).toHaveLength(4);
    expect(hero).toMatch(/data-map-readout="(?:running|lost|resumed|accepted)"/);
    expect(hero).toContain("Computing power,");
    expect(hero).toContain("without the lock-in.");
    expect(hero).toContain("One open network connecting people who need compute with machines ready to work.");
    expect(hero).toContain('data-market-role="demand"');
    expect(hero).toContain('data-market-role="supply"');
    expect(hero).toContain('href="/workspaces"');
    expect(hero).toContain('href="/account/machines"');
    expect(hero).not.toMatch(/data-evidence-value|production attempts/);
  });

  it("renders the hero as a plain section, with no scroll track or sticky wrapper", async () => {
    const markup = await renderLanding();
    const page = source("app/(marketing)/page.tsx");

    // `useMapStory` loops on its own timer now; the pinned scroll track and
    // its sticky child used to hand it a scroll position to read, and both
    // are gone along with the reading. (The comment above `<Hero />` still
    // names the retired track's height for context — that is prose, not a
    // wrapper, so it is not what these assertions are checking for.)
    expect(page).not.toContain("data-hero-scroll");
    expect(page).not.toContain("xl:h-[220svh]");
    expect(page).not.toContain("xl:sticky");
    expect(markup).not.toContain("data-hero-scroll");
    expect(page).not.toMatch(/ScrollSmoother|addEventListener\(["']wheel/);
  });

  it("keeps the market board and platform strip sand, with no metric-card grid", async () => {
    const markup = await renderLanding();
    const market = markup.match(/<section[^>]*id="market"[\s\S]*?<\/section>/)?.[0] ?? "";
    const platform = markup.match(/<section[^>]*id="platform"[\s\S]*?<\/section>/)?.[0] ?? "";

    expect(market).toContain('data-surface="sand"');
    expect(platform).toContain('data-surface="sand"');
    expect(visibleText(market)).toContain("Today");
    expect(platform).toContain('data-os-badge="macOS Apple silicon"');
    expect(platform).toContain('data-os-badge="Linux x86_64"');
    expect(platform).toContain('data-os-badge="Windows 11"');
    expect(platform).not.toContain("data-host-card");
    expect(platform).not.toContain("data-runtime-button");
    expect(platform).not.toContain("Network expansion");
  });

  it("absorbs the three-step journey and the two module facts worth keeping, with no pinned scroll story", async () => {
    const markup = await renderHowItWorks();

    expect(markup).toContain('id="how-it-works"');
    expect(markup).toContain('data-surface="dark"');
    expect(markup.match(/data-human-step=/g) ?? []).toHaveLength(3);
    expect(markup).toContain('data-module-fact="Host"');
    expect(markup).toContain('data-module-fact="Runtime"');
    expect(visibleText(markup)).toContain(
      "Every machine answers to flashnode. Shared machines run only allowlisted Docker images, sandboxed from the host.",
    );
    expect(visibleText(markup)).toContain(
      "Independent tasks lease across machines. Inside one machine, multi-GPU DDP and FSDP run as PyTorch intends.",
    );
    // The three-lane "Recovery" fact and the pinned/scroll-triggered
    // technical workflow are both cut, not folded in here.
    expect(markup).not.toContain('data-module-fact="Recovery"');
    expect(markup).not.toContain("data-workflow-step");
    expect(markup).not.toContain("data-workflow-scene");
  });

  it("balances the services headline and reveals the supporting actions", () => {
    const services = source("components/landing/ProfessionalServices.tsx");

    expect(services).toContain("landing-heading-balance");
    expect(services).toContain("SectionReveal");
    expect(services).toContain("MARKETING.calendlyUrl");
    expect(services).toContain("MARKETING.contactEmail");
  });

  it("does not add unsupported performance, scale, provider, or guarantee claims", () => {
    const supportingSources = [
      "components/landing/HowItWorks.tsx",
      "components/landing/PlatformStrip.tsx",
      "components/landing/ProfessionalServices.tsx",
    ].map(source).join("\n");

    expect(supportingSources).not.toMatch(/\b\d+(?:\.\d+)?%\b/);
    expect(supportingSources).not.toMatch(/\b(?:thousands?|millions?) of (?:nodes|machines|jobs)\b/i);
    expect(supportingSources).not.toMatch(/\b(?:every|all) (?:cloud )?providers?\b/i);
    expect(supportingSources).not.toMatch(/\bguarantee(?:d|s)?\b/i);
  });

  it("gives SectionReveal a staggered fade-up, once, on the wider section-entrance scale, honoring reduced motion", () => {
    // Superseded ceiling: the reveal used to travel `TRAVEL.tight` (8px)
    // over `DURATIONS.enter` (320ms, "at or under a 400ms ceiling") —
    // measured on the live page as imperceptible, which is why this test's
    // own ceiling moved. `SECTION_REVEAL_TRAVEL_PX`/`_DURATION_MS` are a
    // deliberately separate, larger scale (see `lib/motion/timing.ts`);
    // `TRAVEL`'s 8-16px cap still holds for the dense-row reveals it was
    // written for, and `lib/motion/timing.test.ts` still pins that.
    const reveal = source("components/landing/motion/SectionReveal.tsx");

    expect(reveal).toContain("SECTION_REVEAL_TRAVEL_PX");
    expect(reveal).toContain("SECTION_REVEAL_DURATION_MS");
    expect(reveal).toContain("stagger:");
    expect(reveal).toContain("SECTION_REVEAL_STAGGER_MS");
    expect(reveal).not.toMatch(/clipPath/);
    expect(reveal).not.toContain("data-reveal-line");
    expect(reveal).toContain("once: true");
    expect(reveal).toContain("if (reduced || !desktop)");
    // The exported API stays compatible — call sites this pass did not
    // touch still pass `lineClassName`/`bottomLineClassName` and typecheck.
    expect(reveal).toContain("lineClassName?: string");
    expect(reveal).toContain("bottomLineClassName?: string");
  });

  it("renders no reveal-line elements — the wipe is retired for a fade-up", async () => {
    const markup = await renderLanding();

    expect(markup).not.toContain("data-reveal-line");
    expect(markup.match(/data-reveal-content/g)?.length ?? 0).toBeGreaterThan(0);
  });

  it("keeps the hero motion scoped and reduced-motion aware", () => {
    const heroMotion = source("components/landing/coordinator-map/useMapStory.ts");
    expect(heroMotion).toContain("useLandingMotion");
    expect(heroMotion).toContain("reduced");
    expect(heroMotion).toContain("documentVisible");
    // The story is a timer, not a scroll reader. It never takes the wheel, or
    // any other input, away from the reader.
    expect(heroMotion).not.toMatch(/addEventListener\(["'](?:scroll|wheel|touchmove)/);
    expect(heroMotion).not.toMatch(/\brequestAnimationFrame\(/);
    expect(heroMotion).not.toMatch(/preventDefault\(/);
  });

  it("contains the workflow canvas inside the pinned 1024px grid column", () => {
    const css = source("app/globals.css");
    const canvasRule = css.match(/\.workflow-scene-canvas\s*\{([\s\S]*?)\}/)?.[1] ?? "";

    expect(canvasRule).toContain("width: 100%");
    expect(canvasRule).toContain("min-width: 0");
  });

  it("keeps transform ownership isolated and avoids heavy or layout-driven motion", () => {
    const motionSources = [
      "components/landing/coordinator-map/CoordinatorMap.tsx",
      "components/landing/coordinator-map/MapParticles.tsx",
      "components/landing/coordinator-map/useMapStory.ts",
      "components/landing/HowItWorks.tsx",
      "components/landing/PlatformStrip.tsx",
      "components/landing/motion/SectionReveal.tsx",
    ].map(source).join("\n");

    expect(motionSources).not.toMatch(/\blayout(?:Id)?=/);
    expect(motionSources).not.toMatch(/ScrollSmoother|scroll-behavior\s*:\s*smooth|addEventListener\(["']wheel/);
    expect(source("app/globals.css")).not.toMatch(/scroll-behavior\s*:\s*smooth/);
  });

  it("keeps reduced-motion and hidden-document gates in the shared motion path", () => {
    const provider = source("components/landing/motion/LandingMotionProvider.tsx");
    const story = source("components/landing/coordinator-map/useMapStory.ts");
    const map = source("components/landing/coordinator-map/CoordinatorMap.tsx");

    expect(provider).toContain("prefers-reduced-motion: reduce");
    expect(provider).toContain("visibilitychange");
    expect(provider).toContain('document.visibilityState === "visible"');
    // The hero reads both gates out of the same provider every other landing
    // surface uses, so there is one place a reader's preference is honoured.
    expect(story).toContain("useLandingMotion");
    expect(story).toContain("documentVisible");
    expect(story).toContain("if (reduced || !documentVisible) return");
    expect(map).toContain("const { reduced, documentVisible } = useLandingMotion()");
    expect(map).toContain("paused={!documentVisible}");
  });

  it("finishes with a dark services section, light FAQ, and one orange action peak", async () => {
    const markup = await renderLanding();
    const surfaces = [
      ...markup.matchAll(
        /<section[^>]*id="(recover|platform|services|faq|start)"[^>]*data-surface="([^"]+)"/g,
      ),
    ].map(([, id, surface]) => [id, surface]);

    expect(surfaces).toEqual([
      ["recover", "light"],
      ["platform", "sand"],
      ["services", "dark"],
      ["faq", "light"],
      ["start", "orange"],
    ]);
    // No two adjacent sections share a surface, all the way through.
    for (let index = 1; index < surfaces.length; index++) {
      expect(surfaces[index][1], `section ${surfaces[index][0]}`).not.toBe(
        surfaces[index - 1][1],
      );
    }
    expect(markup).toContain('data-motion="recovery-stack"');
    expect(markup).toContain('data-motion="commit-signal"');
    expect(markup).toContain("TASK_COMMIT_ACCEPTED");
  });

  it("keeps FAQ disclosure transitions off layout properties", () => {
    const faq = source("components/landing/Faq.tsx");

    expect(faq).not.toMatch(
      /transition-\[[^\]]*(?:grid-template|width|height|margin|padding)/,
    );
  });

  it("uses surface-aware colors for meaningful lower-funnel metadata", () => {
    for (const path of [
      "components/landing/RecoveryStack.tsx",
      "components/landing/RecoveryDemo.tsx",
      "components/landing/ProfessionalServices.tsx",
      "components/landing/Faq.tsx",
      "components/landing/ClosingCta.tsx",
    ]) {
      expect(source(path), path).not.toMatch(
        /text-\[var\(--z-(?:warning|text-dim)\)\]/,
      );
    }
  });

  it("includes native summaries in the shared focus-visible treatment", () => {
    const css = source("app/globals.css");

    expect(css).toContain(
      ":where(a, button, input, select, textarea, summary, [tabindex]):focus-visible",
    );
  });
});
