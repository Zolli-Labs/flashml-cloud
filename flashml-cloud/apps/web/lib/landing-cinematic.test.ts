import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Home from "@/app/(marketing)/page";
import { ArchitectureSignal } from "@/components/landing/ArchitectureSignal";
import { WorkloadRows } from "@/components/landing/WorkloadRows";
import { WorkloadVelocityRail } from "@/components/landing/WorkloadVelocityRail";

const root = process.cwd();
const source = (path: string) => readFileSync(`${root}/${path}`, "utf8");
const renderLanding = () => renderToStaticMarkup(createElement(Home));
const renderWorkloadRail = () => renderToStaticMarkup(
  createElement(WorkloadVelocityRail, { labels: ["Federated training"] }),
);
const renderWorkloadRows = () => renderToStaticMarkup(createElement(WorkloadRows));
const renderArchitectureSignal = () => renderToStaticMarkup(createElement(ArchitectureSignal));

describe("cinematic landing foundation", () => {
  it("pins the approved motion packages", () => {
    const pkg = JSON.parse(source("package.json"));
    expect(pkg.dependencies.gsap).toBe("3.15.0");
    expect(pkg.dependencies["@gsap/react"]).toBe("2.1.2");
    expect(pkg.dependencies.motion).toBe("^12.42.2");
  });

  it("contains landing motion without hiding server content", () => {
    const markup = renderLanding();
    expect(markup).toContain('data-landing="cinematic"');
    expect(markup).toContain("Compute that");
    expect(markup).toContain("Open console");
    expect(markup).not.toContain('style="opacity:0"');
  });

  it("defines all four landing surfaces and reduced-motion fallback", () => {
    const css = source("app/globals.css");
    for (const token of [
      "--landing-graphite: #0b0d0e",
      "--landing-ivory: #f2efe6",
      "--landing-sand: #ded8cb",
      "--landing-orange: #f36b32",
    ]) expect(css).toContain(token);
    expect(css).toContain(".landing-surface-light");
    expect(css).toContain(".landing-surface-sand");
    expect(css).toContain(".landing-surface-orange");
    expect(css).toContain("prefers-reduced-motion: reduce");
  });

  it("renders the editorial hero and the live coordinator map", () => {
    const markup = renderLanding();
    const hero = markup.match(/<section[^>]*id="hero"[\s\S]*?<\/section>/)?.[0] ?? "";
    expect(hero).toContain('data-surface="dark"');
    // The map is the hero's illustration: the coordinator at the centre, the
    // four sources around it, and the readout strip that makes it a running
    // system rather than a diagram.
    expect(hero).toMatch(/data-coordinator-map="(?:running|lost|resumed|accepted)"/);
    expect(hero).toContain('data-map-core="coordinator"');
    expect(hero.match(/data-map-node=/g) ?? []).toHaveLength(4);
    expect(hero).toMatch(/data-map-readout="(?:running|lost|resumed|accepted)"/);
    expect(hero).toContain("Compute that");
    expect(hero).toContain("finishes the job.");
    expect(hero).toContain('href="/workspaces"');
    expect(hero).toContain('href="https://calendly.com/phongct1105/zolli-ai"');
    expect(hero).not.toMatch(/data-evidence-value|production attempts/);
  });

  it("pins the hero inside a scroll track that drives the map's story", () => {
    const markup = renderLanding();
    const page = source("app/(marketing)/page.tsx");
    const track = markup.match(/<div data-hero-scroll[^>]*>/)?.[0] ?? "";

    // `useMapStory` measures this track rather than reading a breakpoint, so
    // the sticky child and the spare height are what hand the story to scroll
    // instead of to the timer fallback.
    expect(track).toContain("xl:h-[220svh]");
    expect(markup).toContain("xl:sticky xl:top-0");
    expect(page).toContain("data-hero-scroll");
    expect(page).not.toMatch(/ScrollSmoother|addEventListener\(["']wheel/);
  });

  it("uses light evidence and sand machine lanes without metric cards", () => {
    const markup = renderLanding();
    const evidence = markup.match(/<section[^>]*id="evidence"[\s\S]*?<\/section>/)?.[0] ?? "";
    const platform = markup.match(/<section[^>]*id="platform"[\s\S]*?<\/section>/)?.[0] ?? "";

    expect(evidence).toContain('data-surface="light"');
    expect(platform).toContain('data-surface="sand"');
    expect(evidence.match(/data-evidence-value=/g)).toHaveLength(4);
    expect(evidence).toContain('data-layout="evidence-ledger"');
    expect(platform).toContain('data-layout="machine-lanes"');
    expect(platform).toContain("macOS arm64");
    expect(platform).toContain("Windows 11");
  });

  it("pairs an ivory workload rail with a gapless graphite architecture", () => {
    const markup = renderLanding();
    const workloads = markup.match(/<section[^>]*id="workloads"[\s\S]*?<\/section>/)?.[0] ?? "";
    const architecture = markup.match(/<section[^>]*id="architecture"[\s\S]*?<\/section>/)?.[0] ?? "";

    expect(workloads).toContain('data-surface="light"');
    expect(workloads).toContain('data-motion="velocity-rail"');
    expect(architecture).toContain('data-surface="dark"');
    expect(architecture).toContain('data-layout="dense-architecture"');
    for (const value of ["01 Control", "02 Execution", "03 Integrity"])
      expect(architecture).toContain(value);
    expect(architecture).toContain("lg:col-span-7");
    expect(architecture).toContain("lg:col-span-5");
    expect(architecture).toContain("lg:row-span-2");
    expect(architecture).toContain("grid-flow-dense");
  });

  it("uses the readable ivory foreground for decorative workload rail labels", () => {
    const rail = renderWorkloadRail();

    expect(rail).toContain("text-[var(--muted-foreground)]");
  });

  it("keeps workload copy visible while decorative rules animate independently", () => {
    const rows = renderWorkloadRows();
    const rowSource = source("components/landing/WorkloadRows.tsx");
    const fitSource = source("components/landing/WorkloadFit.tsx");

    expect(rows.match(/data-workload-row=/g) ?? []).toHaveLength(4);
    expect(rows.match(/data-workload-rule/g) ?? []).toHaveLength(4);
    expect(rowSource).toContain("whileInView");
    expect(rowSource).toContain("data-animated");
    expect(rowSource).not.toMatch(/(?:opacity|autoAlpha)\s*:\s*0/);
    expect(fitSource).toContain("@/lib/landing/workloads");
    expect(fitSource).not.toMatch(/import\s*\{[^}]*WORKLOADS[^}]*\}\s*from\s*["']@\/components\/landing\/WorkloadRows/);
  });

  it("draws one restrained three-path architecture signal", () => {
    const signal = renderArchitectureSignal();
    const signalSource = source("components/landing/ArchitectureSignal.tsx");

    expect(signal.match(/data-signal-path=/g) ?? []).toHaveLength(3);
    expect(signal).toContain("LEASE_CLAIMED");
    expect(signal).toContain("CHECKPOINT_MANIFEST_COMMITTED");
    expect(signal).toContain("TASK_REQUEUED");
    expect(signalSource).toContain("useLandingMotion");
    expect(signalSource).toContain("once: true");
  });

  it("balances the services headline and reveals the supporting actions", () => {
    const services = source("components/landing/ProfessionalServices.tsx");

    expect(services).toContain("landing-heading-balance");
    expect(services).toContain("block text-muted-foreground");
    expect(services).toContain("SectionReveal");
    expect(services).toContain("MARKETING.calendlyUrl");
    expect(services).toContain("MARKETING.contactEmail");
  });

  it("does not add unsupported performance, scale, provider, or guarantee claims", () => {
    const supportingSources = [
      "components/landing/WorkloadRows.tsx",
      "components/landing/ArchitectureSignal.tsx",
      "components/landing/ProfessionalServices.tsx",
    ].map(source).join("\n");

    expect(supportingSources).not.toMatch(/\b\d+(?:\.\d+)?%\b/);
    expect(supportingSources).not.toMatch(/\b(?:thousands?|millions?) of (?:nodes|machines|jobs)\b/i);
    expect(supportingSources).not.toMatch(/\b(?:every|all) (?:cloud )?providers?\b/i);
    expect(supportingSources).not.toMatch(/\bguarantee(?:d|s)?\b/i);
  });

  it("renders visible ledger and lane rules for SectionReveal to animate", () => {
    const markup = renderLanding();
    const evidence = markup.match(/<section[^>]*id="evidence"[\s\S]*?<\/section>/)?.[0] ?? "";
    const platform = markup.match(/<section[^>]*id="platform"[\s\S]*?<\/section>/)?.[0] ?? "";
    const visibleRevealRule = /<div[^>]*data-reveal-line[^>]*class="[^"]*\bh-px\b[^"]*\bbg-\[[^"]*"[^>]*><\/div>/g;

    expect(evidence.match(visibleRevealRule) ?? []).toHaveLength(2);
    expect(platform.match(visibleRevealRule) ?? []).toHaveLength(2);
  });

  it("keeps the hero motion scoped and reduced-motion aware", () => {
    const heroMotion = source("components/landing/coordinator-map/useMapStory.ts");
    expect(heroMotion).toContain("useLandingMotion");
    expect(heroMotion).toContain("reduced");
    expect(heroMotion).toContain("documentVisible");
    // The story reads the page's own scroll position. It never takes the
    // wheel away from the reader to do it.
    expect(heroMotion).toContain('window.addEventListener("scroll", schedule, { passive: true })');
    expect(heroMotion).not.toMatch(/addEventListener\(["'](?:wheel|touchmove)/);
    expect(heroMotion).not.toMatch(/preventDefault\(/);
  });

  it("keeps the workflow progressive and does not hijack scrolling", () => {
    const journey = source("components/landing/SystemJourney.tsx");
    const markup = renderLanding();
    expect(journey).toContain("useGSAP");
    expect(journey).toContain("useLandingMotion");
    expect(journey).toContain("trigger: stage");
    expect(journey).toContain("pin: stage");
    expect(journey).toContain("onEnterBack");
    expect(journey).toContain("trigger.kill()");
    expect(journey).toContain("desktop");
    expect(journey).toContain("reduced");
    expect(journey).not.toContain("trigger: root");
    expect(markup.match(/data-workflow-scene=/g) ?? []).toHaveLength(7);
    expect(markup).not.toContain("workflow / topology");
    expect(journey).not.toMatch(
      /preventDefault\(|ScrollSmoother|addEventListener\(["']wheel|touchmove/,
    );
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
      "components/landing/SystemJourney.tsx",
      "components/landing/WorkflowScene.tsx",
      "components/landing/WorkloadRows.tsx",
      "components/landing/ArchitectureSignal.tsx",
    ].map(source).join("\n");

    expect(motionSources).not.toMatch(/\blayout(?:Id)?=/);
    expect(motionSources).not.toMatch(/ScrollSmoother|scroll-behavior\s*:\s*smooth|addEventListener\(["']wheel/);
    expect(source("app/globals.css")).not.toMatch(/scroll-behavior\s*:\s*smooth/);
  });

  it("keeps reduced-motion and hidden-document gates in the shared motion path", () => {
    const provider = source("components/landing/motion/LandingMotionProvider.tsx");
    const story = source("components/landing/coordinator-map/useMapStory.ts");
    const map = source("components/landing/coordinator-map/CoordinatorMap.tsx");
    const supportingMotion = [
      "components/landing/WorkloadRows.tsx",
      "components/landing/WorkloadVelocityRail.tsx",
      "components/landing/ArchitectureSignal.tsx",
    ].map(source).join("\n");

    expect(provider).toContain("prefers-reduced-motion: reduce");
    expect(provider).toContain("visibilitychange");
    expect(provider).toContain('document.visibilityState === "visible"');
    // The hero reads both gates out of the same provider every other landing
    // surface uses, so there is one place a reader's preference is honoured.
    expect(story).toContain("useLandingMotion");
    expect(story).toContain("documentVisible");
    expect(story).toContain("if (reduced) return");
    expect(story).toContain("if (reduced || scrollLinked !== false || !documentVisible) return");
    expect(map).toContain("const { reduced, documentVisible } = useLandingMotion()");
    expect(map).toContain("paused={!documentVisible}");
    expect(supportingMotion).toContain("documentVisible");
    expect(supportingMotion).toContain("!reduced");
  });

  it("finishes with light buying sections and one orange action peak", () => {
    const markup = renderLanding();
    const surfaces = [
      ...markup.matchAll(
        /<section[^>]*id="(recover|services|faq|start)"[^>]*data-surface="([^"]+)"/g,
      ),
    ].map(([, id, surface]) => [id, surface]);

    expect(surfaces).toEqual([
      ["recover", "light"],
      ["services", "sand"],
      ["faq", "light"],
      ["start", "orange"],
    ]);
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
