import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Home from "@/app/(marketing)/page";
import { PlatformSupport } from "@/components/landing/PlatformSupport";
import { RecoveryDemo } from "@/components/landing/RecoveryDemo";
import { Navbar } from "@/components/nav/Navbar";
import { MARKETING } from "@/lib/marketing";

const renderLanding = () => renderToStaticMarkup(createElement(Home));
const renderNavbar = () => renderToStaticMarkup(createElement(Navbar));
const renderPlatformSupport = () =>
  renderToStaticMarkup(createElement(PlatformSupport));
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
  it("uses one canonical console, schedule, runtime, and contact destination", () => {
    expect(MARKETING).toEqual({
      consolePath: "/workspaces",
      machinesPath: "/account/machines",
      calendlyUrl: "https://calendly.com/phongct1105/zolli-ai",
      contactEmail: "phongct1105@gmail.com",
      runtimeRepo: "https://github.com/Zolli-Labs/flashml",
    });

    const markup = renderLanding();
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

  it("orders evaluation content from proof through conversion", () => {
    const markup = renderLanding();
    const anchors = [
      'id="network"',
      'id="how-it-works"',
      'id="recover"',
      'id="evidence"',
      'id="workloads"',
      'id="platform"',
      'id="architecture"',
      'id="services"',
      'id="technical-workflow"',
      'id="faq"',
      'id="start"',
    ];

    anchors.reduce((previous, anchor) => {
      const current = markup.indexOf(anchor);
      expect(current).toBeGreaterThan(previous);
      return current;
    }, -1);
  });

  it("keeps the approved twelve-section surface rhythm followed by a dark footer", () => {
    const markup = renderLanding();
    const sections = (markup.match(/<section\b[^>]*>/g) ?? []).map((tag) => [
      tag.match(/\bid="([^"]+)"/)?.[1],
      tag.match(/\bdata-surface="([^"]+)"/)?.[1],
    ]);
    const footer = markup.match(/<footer\b[^>]*>/)?.[0] ?? "";

    expect(sections).toEqual([
      ["hero", "dark"],
      ["network", "light"],
      ["how-it-works", "dark"],
      ["recover", "light"],
      ["evidence", "sand"],
      ["workloads", "light"],
      ["platform", "sand"],
      ["architecture", "dark"],
      ["services", "sand"],
      ["technical-workflow", "dark"],
      ["faq", "light"],
      ["start", "orange"],
    ]);
    // The original design language never repeats a surface on adjacent
    // sections; the alternation is what gives the page its chapter rhythm.
    for (let index = 1; index < sections.length; index++) {
      expect(sections[index][1], `section ${sections[index][0]}`).not.toBe(
        sections[index - 1][1],
      );
    }
    expect(footer).toContain('data-surface="dark"');
    expect(markup.indexOf(footer)).toBeGreaterThan(markup.indexOf('id="start"'));
  });

  it("explains the compute market and a three-step path before mechanics", () => {
    const markup = renderLanding();
    const network = scopedSection(markup, "network");
    const journey = scopedSection(markup, "how-it-works");
    const humanSteps = [...journey.matchAll(/\bdata-human-step="([^\"]+)"/g)].map(
      ([, step]) => step,
    );

    expect(network).toContain('data-surface="light"');
    // The market story reveals on scroll like every other original section.
    expect(network.match(/data-motion="section-reveal"/g) ?? []).toHaveLength(3);
    expect(journey).toContain('data-motion="section-reveal"');
    for (const copy of [
      "Compute is everywhere. Access is not.",
      "From isolated machines to an open compute network.",
      "Access more machines, compare more choices, and avoid depending on one provider's price or availability.",
      "Turn unused machines into productive capacity and earn when they complete useful work.",
      "Early network",
      "Early testing uses Zolli credits. Cash payout is not live.",
    ]) expect(visibleText(network)).toContain(copy);

    expect(journey).toContain('data-surface="dark"');
    expect(humanSteps).toEqual(["1", "2", "3"]);
    expect([...journey.matchAll(/<h3[^>]*>([\s\S]*?)<\/h3>/g)].map(([, title]) => visibleText(title))).toEqual([
      "Tell Zolli what you need.",
      "The network finds suitable machines.",
      "Your work continues as capacity changes.",
    ]);
  });

  it("explains the complete machine-to-result technical workflow in order", () => {
    const journey = scopedSection(renderLanding(), "technical-workflow");
    const steps = [...journey.matchAll(/\bdata-workflow-step="([^"]+)"/g)].map(
      ([, key]) => key,
    );

    expect(journey).toContain('data-surface="dark"');
    expect(journey).toContain('data-motion="system-journey"');
    expect(steps).toEqual([
      "connect",
      "register",
      "submit",
      "parallel",
      "checkpoint",
      "recover",
      "accept",
    ]);
    for (const [title, body] of [
      [
        "Connect machines",
        "Connect machines you operate or capacity you choose to add.",
      ],
      [
        "Register capacity",
        "The control plane records the node information available to it.",
      ],
      [
        "Submit one job",
        "The operator supplies the repository and workload definition through the existing console flow.",
      ],
      [
        "Split and lease tasks",
        "The control plane assigns bounded parallel work to available nodes.",
      ],
      [
        "Checkpoint progress",
        "Workers commit checkpoint manifests as progress becomes available.",
      ],
      [
        "Recover interrupted work",
        "A missing heartbeat can requeue work from its recorded checkpoint.",
      ],
      [
        "Accept one result",
        "The control plane records the accepted task commit.",
      ],
    ]) {
      expect(visibleText(journey)).toContain(`${title} ${body}`);
    }
    for (const event of [
      "LEASE_CLAIMED",
      "CHECKPOINT_MANIFEST_COMMITTED",
      "NODE_HEARTBEAT_LOST",
      "TASK_REQUEUED",
      "TASK_COMMIT_ACCEPTED",
    ]) expect(journey).toContain(event);
    expect(journey).not.toMatch(/NODE_REGISTERED|NODE_HEARTBEAT\b|JOB_SUBMITTED/);
    expect((journey.match(/<li\b/g) ?? [])).toHaveLength(7);
  });

  it("keeps each workflow scene small and removes the old topology ticker", () => {
    const journey = scopedSection(renderLanding(), "technical-workflow");

    expect(journey.match(/\bdata-workflow-scene=/g) ?? []).toHaveLength(7);
    expect(journey).not.toContain("workflow / topology");
    expect(journey).not.toContain("Protocol events");
    expect(journey).not.toContain("data-journey-node");
    expect(journey).not.toContain("data-journey-event");
  });

  it("keeps meaningful workflow labels above the flagged contrast floor", () => {
    const journey = scopedSection(renderLanding(), "technical-workflow");

    expect(journey).not.toMatch(/\btext-white\/(?:35|38|42)\b/);
  });

  it("enforces the approved outcome-level evidence and rejects unsupported comparisons", () => {
    const markup = renderLanding();
    const text = visibleText(markup);
    const evidenceSection = markup.match(
      /<section\b[^>]*\bid="evidence"[^>]*>([\s\S]*?)<\/section>/,
    )?.[1];
    const evidenceValues = [
      ...(evidenceSection ?? "").matchAll(/\bdata-evidence-value="([^"]*)"/g),
    ].map(([, value]) => value);

    expect(evidenceValues).toEqual(["6", "3", "58", "1"]);
    expect(text).not.toMatch(/\b\d+(?:\.\d+)?\s?%/);
    expect(text).not.toMatch(/\b\d+(?:\.\d+)?\s?(?:×|x)(?!\w)/i);
    expect(text).not.toMatch(
      /\b\d[\d,]*(?:\.\d+)?\s+(?:customers?|companies|teams?)\b/i,
    );
    for (const claim of [
      "6 trials completed",
      "One model search completed all six independent trials.",
      "3 machines shared the work",
      "A laptop and two rented GPUs completed the same search.",
      "58 epochs preserved",
      "Completed training progress survived when a rented GPU was destroyed.",
      "1 accepted result per task",
      "macOS arm64",
      "Linux x86_64",
      "Windows 11",
      "Preview",
      "PyTorch CUDA 12.4",
    ]) expect(text).toContain(claim);
    expect(text).not.toContain("Windows 11 Proven");
  });

  it("rejects universal support, customer, provider, and unverified performance claims", () => {
    const markup = renderLanding();
    const text = visibleText(markup);
    const nonFaqText = visibleText(markup.replace(scopedSection(markup, "faq"), ""));

    expect(text).not.toMatch(/\b(?:trusted by|used by)\b|\bcustomers?\b/i);
    expect(text).not.toMatch(/\b(?:all|every) (?:cloud )?providers?\b/i);
    expect(text).not.toMatch(/\b(?:supports?|works on|available on) (?:all|every)\b/i);
    expect(text).not.toMatch(/\b(?:Together AI|Lambda Labs|Vast\.ai)\b/i);
    expect(text).not.toMatch(/\b\d+(?:\.\d+)?\s?(?:%|×|x)(?!\w)/i);
    expect(nonFaqText).not.toMatch(/\bguarantee(?:d|s)?\b/i);
  });

  it("qualifies platform compatibility without overstating Windows", () => {
    const text = visibleText(renderLanding());
    expect(text).toContain("Proven today");
    expect(text).toContain("macOS Apple silicon Proven");
    expect(text).toContain("Linux x86_64 Proven");
    expect(text).toContain("RunPod NVIDIA GPUs Proven");
    expect(text).toContain("Windows 11 Preview");
    expect(text).toContain("Docker");
    expect(text).toContain("GitHub");
    expect(text).not.toMatch(/customers|uptime|faster|savings/i);
  });

  it("replaces the old integration rows with an icon-led runtime explorer and host cards", () => {
    const markup = renderPlatformSupport();

    expect(markup.match(/data-runtime-button="[^"]*"/g) ?? []).toHaveLength(9);
    expect(markup.match(/data-host-card="[^"]*"/g) ?? []).toHaveLength(4);
    expect(markup).not.toContain("Python workloads");
    expect(markup).not.toContain("Local/cloud machine supply");
    expect(markup).not.toContain("data-machine-result");
    for (const label of [
      "Python 3.11", "NumPy", "pandas", "scikit-learn", "SciPy",
      "PyTorch CPU", "PyTorch CUDA 12.4", "Docker", "GitHub",
    ]) expect(visibleText(markup)).toContain(label);
  });

  it("names five workloads with their supported machine context", () => {
    const text = visibleText(renderLanding());
    for (const workload of [
      "Model configuration search",
      "AI model evaluation",
      "Independent file processing",
      "Simulations and research trials",
      "Checkpointable model training",
    ]) expect(text).toContain(workload);
  });

  it("lists every workload once under its mode", () => {
    const workloads = scopedSection(renderLanding(), "workloads");

    expect(workloads).toContain('aria-label="Supported workloads — Divide mode"');
    expect(workloads).toContain('aria-label="Supported workloads — Resume mode"');
    expect(workloads.match(/<li\b/g) ?? []).toHaveLength(5);
  });

  it("groups the runtime into host, runtime, and recovery lanes", () => {
    const text = visibleText(renderLanding());
    for (const lane of ["01 Host", "02 Runtime", "03 Recovery"])
      expect(text).toContain(lane);
    for (const moduleName of ["Coordinate", "Enroll", "Execute", "Checkpoint", "Recover", "Verify"])
      expect(text).toContain(moduleName);
  });

  it("connects the recovery ledger to the documented recovery outcome", () => {
    const text = visibleText(renderLanding());
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

  it("offers assisted adoption without displacing self service", () => {
    const markup = renderLanding();
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

  it("uses editorial services instead of four equal cards", () => {
    const markup = renderLanding();
    const services =
      markup.match(/<section[^>]*id="services"[\s\S]*?<\/section>/)?.[0] ?? "";

    expect(services).toContain('data-layout="service-rows"');
    expect(services.match(/data-service-row=/g) ?? []).toHaveLength(2);
    expect(services.match(/<article\b/g) ?? []).toHaveLength(4);
  });

  it("answers the eight market-fit questions with native disclosures", () => {
    const markup = renderLanding();
    const disclosures = markup.match(/<details\b[^>]*>[\s\S]*?<\/details>/g) ?? [];
    const expectedFaqs = [
      "What is Zolli?",
      "Is Zolli another cloud provider?",
      "Can machine owners earn money today?",
      "Will Zolli always be cheaper?",
      "Which machines work?",
      "Which workloads fit?",
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
  });

  it("ends with the demand choice before the provider choice", () => {
    const start = scopedSection(renderLanding(), "start");

    expect(visibleText(start)).toContain("Join the open compute network.");
    expect(anchorForText(start, "I need compute")).toContain(`href="${MARKETING.consolePath}"`);
    expect(anchorForText(start, "I want to provide compute")).toContain(`href="${MARKETING.machinesPath}"`);
    expect(start.indexOf("I need compute")).toBeLessThan(start.indexOf("I want to provide compute"));
  });

  it("provides complete product, resource, company, and legal navigation", () => {
    const markup = renderLanding();
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
