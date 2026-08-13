import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { prerenderToNodeStream } from "react-dom/static.node";
import { describe, expect, it, vi } from "vitest";
import Home from "@/app/(marketing)/page";
import { RecoveryDemo } from "@/components/landing/RecoveryDemo";
import { Navbar } from "@/components/nav/Navbar";
import { MARKETING } from "@/lib/marketing";

// `Home` renders `PriceBoard`, which fetches `GET /v1alpha1/public/prices`
// server-side against `http://localhost:8000` by default — a real network
// attempt on every `renderLanding()` call below unless stubbed. Stubbed the
// same way `lib/landing/market-board.test.ts` stubs it: a rejected promise,
// which `fetchLandingPrices` already turns into `null` (the empty-board
// state this file's assertions already treat as normal), so no unstubbed
// listener on that port can ever hang this suite.
vi.stubGlobal(
  "fetch",
  vi.fn().mockRejectedValue(new Error("stubbed: tests never hit the network")),
);

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
const renderLanding = async () => {
  const { prelude } = await prerenderToNodeStream(createElement(Home));
  return streamToString(prelude);
};
const renderNavbar = () => renderToStaticMarkup(createElement(Navbar));
const renderRecoveryDemo = () =>
  renderToStaticMarkup(createElement(RecoveryDemo));
const visibleText = (markup: string) =>
  markup.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ").trim();
const anchorTags = (markup: string) => markup.match(/<a\b[^>]*>[\s\S]*?<\/a>/g) ?? [];
const anchorForText = (markup: string, label: string) =>
  anchorTags(markup).find((anchor) => visibleText(anchor).startsWith(label));
const scopedElement = (markup: string, tag: string, ariaLabel: string) =>
  markup.match(
    new RegExp(`<${tag}\\b[^>]*\\baria-label="${ariaLabel}"[^>]*>[\\s\\S]*?</${tag}>`),
  )?.[0] ?? "";
const scopedSection = (markup: string, id: string) =>
  markup.match(new RegExp(`<section\\b[^>]*\\bid="${id}"[^>]*>[\\s\\S]*?</section>`))?.[0] ?? "";

describe("proof-led Zolli landing", () => {
  it("uses one canonical console, schedule, runtime, and contact destination", async () => {
    expect(MARKETING).toEqual({
      consolePath: "/workspaces",
      machinesPath: "/account/machines",
      calendlyUrl: "https://calendly.com/phongct1105/zolli-ai",
      contactEmail: "phongct1105@gmail.com",
      runtimeRepo: "https://github.com/Zolli-Labs/flashml",
    });

    const markup = await renderLanding();
    expect(markup).toContain(`href="${MARKETING.consolePath}"`);
    const calendlyAnchors = anchorTags(markup).filter((anchor) =>
      anchor.includes("calendly.com"),
    );
    expect(calendlyAnchors).toHaveLength(2);
    for (const anchor of calendlyAnchors) {
      expect(anchor).toContain(`href="${MARKETING.calendlyUrl}"`);
      expect(anchor).toContain('target="_blank"');
      expect(anchor).toContain('rel="noreferrer"');
    }
  });

  it("keeps shared navigation valid from every marketing route", () => {
    const markup = renderNavbar();
    const primary = scopedElement(markup, "nav", "Primary navigation");
    const mobile = scopedElement(markup, "nav", "Mobile navigation");
    const destinations = [
      ["How it works", "/#how-it-works"],
      ["Platform", "/#platform"],
      ["Services", "/#services"],
      ["Open runtime", MARKETING.runtimeRepo],
      ["Open console", MARKETING.consolePath],
    ] as const;

    for (const navigation of [primary, mobile]) {
      const links = anchorTags(navigation);
      expect(links).toHaveLength(destinations.length);
      expect(links.map((link) => visibleText(link))).toEqual(
        destinations.map(([label]) => label),
      );
      expect(
        links.map((link) => link.match(/\bhref="([^"]+)"/)?.[1]),
      ).toEqual(destinations.map(([, href]) => href));
    }

    for (const label of ["How it works", "Platform", "Services", "Open runtime"]) {
      const anchor = anchorForText(primary, label);
      expect(anchor).toContain("inline-flex");
      expect(anchor).toContain("min-h-10");
      expect(anchor).toContain("items-center");
    }

    expect(markup).toContain('aria-controls="mobile-navigation"');
    expect(markup).toContain('aria-expanded="false"');
    expect(mobile).toContain('id="mobile-navigation"');
    expect(mobile).toContain('hidden=""');
  });

  it("orders evaluation content from proof through conversion", async () => {
    const markup = await renderLanding();
    const anchors = [
      'id="hero"',
      'id="market"',
      'id="how-it-works"',
      'id="recover"',
      'id="platform"',
      'id="services"',
      'id="faq"',
      'id="start"',
    ];

    anchors.reduce((previous, anchor) => {
      const current = markup.indexOf(anchor);
      expect(current).toBeGreaterThan(previous);
      return current;
    }, -1);
  });

  it("keeps the approved seven-section surface rhythm followed by a dark footer", async () => {
    const markup = await renderLanding();
    const sections = (markup.match(/<section\b[^>]*>/g) ?? []).map((tag) => [
      tag.match(/\bid="([^"]+)"/)?.[1],
      tag.match(/\bdata-surface="([^"]+)"/)?.[1],
    ]);
    const footer = markup.match(/<footer\b[^>]*>/)?.[0] ?? "";

    expect(sections).toEqual([
      ["hero", "dark"],
      ["market", "sand"],
      ["how-it-works", "dark"],
      ["recover", "light"],
      ["platform", "sand"],
      ["services", "dark"],
      ["faq", "light"],
      ["start", "orange"],
    ]);
    // No two adjacent sections share a surface; the alternation is what
    // gives the page its chapter rhythm.
    for (let index = 1; index < sections.length; index++) {
      expect(sections[index][1], `section ${sections[index][0]}`).not.toBe(
        sections[index - 1][1],
      );
    }
    expect(footer).toContain('data-surface="dark"');
    expect(markup.indexOf(footer)).toBeGreaterThan(markup.indexOf('id="start"'));
  });

  it("shows today's GPU prices before the three-step path and its two module facts", async () => {
    const markup = await renderLanding();
    const market = scopedSection(markup, "market");
    const journey = scopedSection(markup, "how-it-works");
    const humanSteps = [...journey.matchAll(/\bdata-human-step="([^"]+)"/g)].map(
      ([, step]) => step,
    );

    expect(market).toContain('data-surface="sand"');
    expect(journey).toContain('data-surface="dark"');
    expect(humanSteps).toEqual(["1", "2", "3"]);
    expect([...journey.matchAll(/<h3[^>]*>([\s\S]*?)<\/h3>/g)].map(([, title]) => visibleText(title))).toEqual([
      "Tell Zolli what you need.",
      "The network finds suitable machines.",
      "Your work continues as capacity changes.",
    ]);
    expect(visibleText(journey)).toContain(
      "Every machine answers to flashnode. Shared machines run only allowlisted Docker images, sandboxed from the host.",
    );
    expect(visibleText(journey)).toContain(
      "Independent tasks lease across machines. Inside one machine, multi-GPU DDP and FSDP run as PyTorch intends.",
    );
  });

  it("keeps the flashnode allowlist and DDP/FSDP facts, and drops the old three-lane module grid", async () => {
    const markup = await renderLanding();
    const journey = scopedSection(markup, "how-it-works");
    const text = visibleText(journey);

    expect(text).toContain("Host");
    expect(text).toContain("Runtime");
    expect(text).not.toContain("01 Host");
    expect(text).not.toContain("03 Recovery");
    expect(journey).not.toContain("LEASE_CLAIMED");
    expect(journey).not.toContain("CHECKPOINT_MANIFEST_COMMITTED");
    expect(journey).not.toContain("data-workflow-step");
    expect(journey).not.toContain("data-workflow-scene");
  });

  it("enforces the approved outcome-level evidence inside the recovery section, and rejects unsupported comparisons", async () => {
    const markup = await renderLanding();
    const text = visibleText(markup);
    const recover = scopedSection(markup, "recover");
    const evidenceValues = [
      ...recover.matchAll(/\bdata-evidence-value="([^"]*)"/g),
    ].map(([, value]) => value);

    // Documented engineering benchmarks, pinned so they cannot drift.
    // Countable facts, not performance comparisons. The set this replaced
    // pinned "47%" — the second-best of seven runs of a benchmark ranging
    // -189.9% to +48.3%, whose two ten-repeat runs say +42.6% and +37.5%.
    // Pinning a cherry-picked number in a test is how it stops looking like
    // a choice and starts looking like a fact.
    expect(evidenceValues).toEqual(["30", "2", "5", "1"]);
    expect(text).not.toMatch(
      /\b\d[\d,]*(?:\.\d+)?\s+(?:customers?|companies|teams?)\b/i,
    );
    for (const claim of [
      "30 production attempts",
      "Recorded across the first two contributing hosts.",
      "2 proven architectures",
      "macOS arm64 and Linux x86_64.",
      "5 steps lost, not 35",
      "Recovered from the last verified checkpoint.",
      "1 accepted result per task",
      "Idempotent commits reject duplicate outcomes.",
      "macOS Apple silicon",
      "Linux x86_64",
      "Windows 11",
      "PyTorch CUDA 12.4",
    ]) expect(text).toContain(claim);
  });

  it("rejects universal support, customer, provider, and unverified performance claims", async () => {
    const markup = await renderLanding();
    const text = visibleText(markup);
    const nonFaqText = visibleText(markup.replace(scopedSection(markup, "faq"), ""));

    expect(text).not.toMatch(/\b(?:trusted by|used by)\b|\bcustomers?\b/i);
    expect(text).not.toMatch(/\b(?:all|every) (?:cloud )?providers?\b/i);
    expect(text).not.toMatch(/\b(?:supports?|works on|available on) (?:all|every)\b/i);
    expect(text).not.toMatch(/\b(?:Together AI|Lambda Labs|Vast\.ai)\b/i);
    expect(nonFaqText).not.toMatch(/\bguarantee(?:d|s)?\b/i);
  });

  it("qualifies platform compatibility without overstating any host, and never badges one 'Proven'", async () => {
    const markup = await renderLanding();
    const text = visibleText(markup);
    // "Proven" is retired as UI status wording — `Preview` is the only tag
    // the platform strip is allowed to print. The FAQ's own prose ("Proven
    // hosts are...") is unaffected copy from an earlier task, not a badge,
    // so the stricter check is scoped to the platform section alone.
    const platform = visibleText(scopedSection(markup, "platform"));

    expect(text).toContain("macOS Apple silicon");
    expect(text).toContain("Linux x86_64");
    expect(text).toContain("Windows 11");
    expect(platform).toContain("Preview");
    expect(platform).not.toContain("Proven");
    expect(text).toContain("Docker");
    expect(text).toContain("GitHub");
    expect(text).not.toMatch(/customers|uptime|faster|savings/i);
  });

  it("replaces the interactive runtime explorer and host-card grid with plain OS badges and runtime chips", async () => {
    const markup = await renderLanding();
    const platform = scopedSection(markup, "platform");

    expect(platform.match(/data-os-badge="[^"]*"/g) ?? []).toHaveLength(3);
    expect(platform.match(/data-runtime-chip="[^"]*"/g) ?? []).toHaveLength(9);
    expect(platform).not.toContain("data-runtime-button");
    expect(platform).not.toContain("data-host-card");
    expect(platform).not.toContain("data-runtime-detail");
    expect(platform).not.toContain("data-machine-result");
    expect(platform).not.toContain("Network expansion");
    for (const label of [
      "Python 3.11", "NumPy", "pandas", "scikit-learn", "SciPy",
      "PyTorch CPU", "PyTorch CUDA 12.4", "Docker", "GitHub",
    ]) expect(visibleText(platform)).toContain(label);
  });

  it("connects the recovery ledger to the documented recovery outcome", async () => {
    const text = visibleText(await renderLanding());
    expect(text).toContain("Machines disappear. Progress doesn't.");
    expect(text).toContain("RTX 4090 machine destroyed");
    expect(text).toContain("Resumed on an RTX 3090");
    expect(text).toContain("58 epochs preserved");
    expect(text).toContain("sample data");
  });

  it("draws recovery separators before every item after the first", () => {
    const markup = renderRecoveryDemo();

    expect(markup).toContain("sm:not-first:border-l");
    expect(markup).toContain("sm:not-first:border-border");
    expect(markup).not.toContain("sm:not-last:border-l");
  });

  it("offers assisted adoption without displacing self service", async () => {
    const markup = await renderLanding();
    const text = visibleText(markup);
    const services = scopedSection(markup, "services");
    const serviceTitles = [...services.matchAll(/<h3\b[^>]*>([\s\S]*?)<\/h3>/g)].map(
      ([, title]) => visibleText(title),
    );
    expect(text).toContain("Professional services");
    expect((services.match(/<article\b/g) ?? [])).toHaveLength(4);
    expect(serviceTitles).toEqual([
      "Architecture and workload assessment",
      "Machine and GPU fleet onboarding",
      "Runtime and job-spec integration",
      "Private deployment and recovery design",
    ]);
    expect(text).toContain("Start with the machines and workloads you already have.");
  });

  it("uses editorial services instead of four equal cards", async () => {
    const markup = await renderLanding();
    const services = scopedSection(markup, "services");

    expect(services).toContain('data-layout="service-rows"');
    expect(services).toContain('data-surface="dark"');
    expect(services.match(/data-service-row=/g) ?? []).toHaveLength(2);
    expect(services.match(/<article\b/g) ?? []).toHaveLength(4);
  });

  it("answers the nine market-fit questions with native disclosures, including the honest training-fit boundary", async () => {
    const markup = await renderLanding();
    const disclosures = markup.match(/<details\b[^>]*>[\s\S]*?<\/details>/g) ?? [];
    const expectedFaqs = [
      "What is Zolli?",
      "Is Zolli another cloud provider?",
      "Can machine owners earn money today?",
      "Will Zolli always be cheaper?",
      "Which machines work?",
      "Which workloads fit?",
      "What kind of training doesn't fit?",
      "What happens if a machine disappears?",
      "How mature is the network?",
    ] as const;

    expect(disclosures).toHaveLength(expectedFaqs.length);
    disclosures.forEach((disclosure, index) => {
      const text = visibleText(disclosure);
      expect(text).toContain(expectedFaqs[index]);
    });
    const text = visibleText(markup);
    expect(text).toContain("Cash payout is not live");
    expect(text).toContain("cannot guarantee every job is cheaper");
    expect(text).toContain("Tightly synchronized multi-machine training is not the current target");
    expect(text).toContain(
      "It is not currently designed for tightly synchronized training where every GPU must communicate continuously over a very fast network.",
    );
  });

  it("ends with the demand choice before the provider choice", async () => {
    const start = scopedSection(await renderLanding(), "start");

    expect(visibleText(start)).toContain("Join the open compute network.");
    expect(anchorForText(start, "I need compute")).toContain(`href="${MARKETING.consolePath}"`);
    expect(anchorForText(start, "I want to provide compute")).toContain(`href="${MARKETING.machinesPath}"`);
    expect(start.indexOf("I need compute")).toBeLessThan(start.indexOf("I want to provide compute"));
  });

  it("provides complete product, resource, company, and legal navigation", async () => {
    const markup = await renderLanding();
    const groups = [
      [
        "Product footer navigation",
        [
          ["Console", MARKETING.consolePath],
          ["Machines", "/account/machines"],
          ["Jobs", "/jobs"],
          ["Platform", "#platform"],
        ],
      ],
      [
        "Resources footer navigation",
        [
          ["Docs", "/docs"],
          ["GitHub", MARKETING.runtimeRepo],
          ["Open runtime", MARKETING.runtimeRepo],
          ["FAQ", "#faq"],
        ],
      ],
      [
        "Company footer navigation",
        [
          ["Contact", "/contact"],
          ["Schedule a call", MARKETING.calendlyUrl],
        ],
      ],
      [
        "Legal footer navigation",
        [
          ["Privacy", "/privacy"],
          ["Terms", "/terms"],
          ["Security", "/security"],
        ],
      ],
    ] as const;

    for (const [ariaLabel, links] of groups) {
      const group = scopedElement(markup, "nav", ariaLabel);
      expect(group).not.toBe("");
      expect(anchorTags(group)).toHaveLength(links.length);
      for (const [label, href] of links)
        expect(anchorForText(group, label)).toContain(`href="${href}"`);
    }
  });
});
