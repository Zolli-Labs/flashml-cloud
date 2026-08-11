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
  markup.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
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
      calendlyUrl: "https://calendly.com/phongct1105/zolli-ai",
      contactEmail: "phongct1105@gmail.com",
      runtimeRepo: "https://github.com/Zolli-Labs/flashml",
    });

    const markup = renderLanding();
    expect(markup).toContain(`href="${MARKETING.consolePath}"`);
    const calendlyAnchors = anchorTags(markup).filter((anchor) =>
      anchor.includes("calendly.com"),
    );
    expect(calendlyAnchors).toHaveLength(4);
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
      'id="evidence"',
      'id="platform"',
      'id="how-it-works"',
      'id="workloads"',
      'id="architecture"',
      'id="recover"',
      'id="services"',
      'id="faq"',
    ];

    anchors.reduce((previous, anchor) => {
      const current = markup.indexOf(anchor);
      expect(current).toBeGreaterThan(previous);
      return current;
    }, -1);
  });

  it("keeps the approved ten-section surface rhythm followed by a dark footer", () => {
    const markup = renderLanding();
    const sections = (markup.match(/<section\b[^>]*>/g) ?? []).map((tag) => [
      tag.match(/\bid="([^"]+)"/)?.[1],
      tag.match(/\bdata-surface="([^"]+)"/)?.[1],
    ]);
    const footer = markup.match(/<footer\b[^>]*>/)?.[0] ?? "";

    expect(sections).toEqual([
      ["hero", "dark"],
      ["evidence", "light"],
      ["platform", "sand"],
      ["how-it-works", "dark"],
      ["workloads", "light"],
      ["architecture", "dark"],
      ["recover", "light"],
      ["services", "sand"],
      ["faq", "light"],
      ["start", "orange"],
    ]);
    expect(footer).toContain('data-surface="dark"');
    expect(markup.indexOf(footer)).toBeGreaterThan(markup.indexOf('id="start"'));
  });

  it("explains the complete machine-to-result workflow in order", () => {
    const journey = scopedSection(renderLanding(), "how-it-works");
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
    const journey = scopedSection(renderLanding(), "how-it-works");

    expect(journey.match(/\bdata-workflow-scene=/g) ?? []).toHaveLength(7);
    expect(journey).not.toContain("workflow / topology");
    expect(journey).not.toContain("Protocol events");
    expect(journey).not.toContain("data-journey-node");
    expect(journey).not.toContain("data-journey-event");
  });

  it("keeps meaningful workflow labels above the flagged contrast floor", () => {
    const journey = scopedSection(renderLanding(), "how-it-works");

    expect(journey).not.toMatch(/\btext-white\/(?:35|38|42)\b/);
  });

  it("enforces the exact approved evidence-band values and rejects unsupported comparisons", () => {
    const markup = renderLanding();
    const text = visibleText(markup);
    const evidenceSection = markup.match(
      /<section\b[^>]*\bid="evidence"[^>]*>([\s\S]*?)<\/section>/,
    )?.[1];
    const evidenceValues = [
      ...(evidenceSection ?? "").matchAll(/\bdata-evidence-value="([^"]*)"/g),
    ].map(([, value]) => value);

    expect(evidenceValues).toEqual(["30", "2", "5", "1"]);
    expect(text).not.toMatch(/\b\d+(?:\.\d+)?\s?%/);
    expect(text).not.toMatch(/\b\d+(?:\.\d+)?\s?(?:×|x)(?!\w)/i);
    expect(text).not.toMatch(
      /\b\d[\d,]*(?:\.\d+)?\s+(?:customers?|companies|teams?)\b/i,
    );
    for (const claim of [
      "30 production attempts",
      "2 proven architectures",
      "5 steps lost, not 35",
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
    const text = visibleText(renderLanding());

    expect(text).not.toMatch(/\b(?:trusted by|used by)\b|\bcustomers?\b/i);
    expect(text).not.toMatch(/\b(?:all|every) (?:cloud )?providers?\b/i);
    expect(text).not.toMatch(/\b(?:supports?|works on|available on) (?:all|every)\b/i);
    expect(text).not.toMatch(/\b(?:RunPod|Together AI|Lambda Labs|Vast\.ai)\b/i);
    expect(text).not.toMatch(/\b\d+(?:\.\d+)?\s?(?:%|×|x)(?!\w)/i);
    expect(text).not.toMatch(/\bguarantee(?:d|s)?\b/i);
  });

  it("qualifies platform compatibility without overstating Windows", () => {
    const text = visibleText(renderLanding());
    expect(text).toContain("Production-proven hosts");
    expect(text).toContain("macOS arm64 Proven");
    expect(text).toContain("Linux x86_64 Proven");
    expect(text).toContain("Windows 11 Preview");
    expect(text).toContain("Docker");
    expect(text).toContain("GitHub");
    expect(text).not.toMatch(/customers|uptime|faster|savings/i);
  });

  it("replaces the old integration rows with an icon-led runtime explorer and host cards", () => {
    const markup = renderPlatformSupport();

    expect(markup.match(/data-runtime-button="[^"]*"/g) ?? []).toHaveLength(9);
    expect(markup.match(/data-host-card="[^"]*"/g) ?? []).toHaveLength(3);
    expect(markup).not.toContain("Python workloads");
    expect(markup).not.toContain("Local/cloud machine supply");
    expect(markup).not.toContain("data-machine-result");
    for (const label of [
      "Python 3.11", "NumPy", "pandas", "scikit-learn", "SciPy",
      "PyTorch CPU", "PyTorch CUDA 12.4", "Docker", "GitHub",
    ]) expect(visibleText(markup)).toContain(label);
  });

  it("names four workloads already represented in the project", () => {
    const text = visibleText(renderLanding());
    for (const workload of [
      "Federated training",
      "Hyperparameter search",
      "Shared data processing",
      "Checkpointable model training",
    ]) expect(text).toContain(workload);
  });

  it("keeps one semantic workload list while the duplicated velocity labels stay hidden", () => {
    const workloads = scopedSection(renderLanding(), "workloads");

    expect(workloads).toContain('aria-label="Supported workloads"');
    expect(workloads).toContain('aria-hidden="true"');
    expect(workloads.match(/<li\b/g) ?? []).toHaveLength(4);
  });

  it("groups the runtime into control, execution, and integrity layers", () => {
    const text = visibleText(renderLanding());
    for (const layer of ["01 Control", "02 Execution", "03 Integrity"])
      expect(text).toContain(layer);
    for (const moduleName of ["Coordinate", "Enroll", "Execute", "Checkpoint", "Recover", "Verify"])
      expect(text).toContain(moduleName);
  });

  it("connects the recovery ledger to the verified five-step result", () => {
    const text = visibleText(renderLanding());
    expect(text).toContain("Failure at step 35");
    expect(text).toContain("Checkpoint at step 30");
    expect(text).toContain("5 steps of work lost");
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
    expect(markup.indexOf("Open console")).toBeLessThan(markup.indexOf("Talk to Zolli"));
  });

  it("uses editorial services instead of four equal cards", () => {
    const markup = renderLanding();
    const services =
      markup.match(/<section[^>]*id="services"[\s\S]*?<\/section>/)?.[0] ?? "";

    expect(services).toContain('data-layout="service-rows"');
    expect(services.match(/data-service-row=/g) ?? []).toHaveLength(2);
    expect(services.match(/<article\b/g) ?? []).toHaveLength(4);
  });

  it("answers the seven buyer questions with native disclosures", () => {
    const markup = renderLanding();
    const disclosures = markup.match(/<details\b[^>]*>[\s\S]*?<\/details>/g) ?? [];
    const expectedFaqs = [
      {
        question: "What does Zolli coordinate?",
        clauses: ["jobs", "tasks", "leases", "checkpoints", "recovery", "accepted results"],
      },
      {
        question: "Which machines are supported?",
        clauses: ["macOS arm64", "Linux x86_64", "production-proven", "Windows 11", "preview"],
      },
      {
        question: "What happens when a machine disappears?",
        clauses: ["missing heartbeat", "expires", "ownership", "requeued", "last verified checkpoint"],
      },
      {
        question: "Does every machine need Docker?",
        clauses: ["Subprocess execution", "trusted pools", "allowlisted Docker", "isolation path", "shared machines"],
      },
      {
        question: "How are code, artifacts, and credentials handled?",
        clauses: [
          "Task environments are scrubbed",
          "machine writes are authenticated and lease-scoped",
          "artifacts and checkpoints are hash-verified",
          "Deployment configuration still matters",
        ],
      },
      {
        question: "How is Zolli priced?",
        clauses: ["Pricing is not published during early access", "Schedule", "email Zolli", "scope"],
      },
      {
        question: "What support is available during early access?",
        clauses: [
          "onboarding",
          "workload integration",
          "deployment",
          "recovery design by agreement",
          "No service-level agreement",
        ],
      },
    ] as const;

    expect(disclosures).toHaveLength(expectedFaqs.length);
    disclosures.forEach((disclosure, index) => {
      const text = visibleText(disclosure);
      const expected = expectedFaqs[index];
      expect(text).toContain(expected.question);
      for (const clause of expected.clauses) expect(text).toContain(clause);
    });
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
