import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import Contact, { metadata as contactMetadata } from "@/app/(marketing)/contact/page";
import Privacy, { metadata as privacyMetadata } from "@/app/(marketing)/privacy/page";
import Terms, { metadata as termsMetadata } from "@/app/(marketing)/terms/page";
import Security, { metadata as securityMetadata } from "@/app/(marketing)/security/page";
import { buildSignedOutRedirect, isPublicPath } from "@/middleware";

const render = (component: () => ReactNode) =>
  renderToStaticMarkup(createElement(component));
const anchorTags = (markup: string) =>
  markup.match(/<a\b[^>]*>[\s\S]*?<\/a>/g) ?? [];

describe("public Zolli information routes", () => {
  it("allows every information route through middleware without a session", () => {
    for (const pathname of [
      "/contact",
      "/privacy",
      "/terms",
      "/security",
      "/manifest.webmanifest",
    ]) {
      expect(isPublicPath(pathname), pathname).toBe(true);
    }

    for (const pathname of ["/workspaces", "/w/pool-123/overview"]) {
      expect(isPublicPath(pathname), pathname).toBe(false);
    }
  });

  it("constructs the exact safe signed-out redirect", () => {
    const redirect = buildSignedOutRedirect(
      new URL("https://zolliai.com/workspaces")
    );
    expect(redirect.pathname + redirect.search).toBe(
      "/sign-in?next=%2Fworkspaces"
    );

    const inviteRedirect = buildSignedOutRedirect(
      new URL("https://zolliai.com/pools/join?token=fmi_abc")
    );
    expect(inviteRedirect.pathname + inviteRedirect.search).toBe(
      "/sign-in?next=%2Fpools%2Fjoin%3Ftoken%3Dfmi_abc"
    );

    const unsafeRedirect = buildSignedOutRedirect(
      new URL("https://zolliai.com//evil.com/steal")
    );
    expect(unsafeRedirect.pathname + unsafeRedirect.search).toBe(
      "/sign-in?next=%2Fmachines"
    );
  });

  it("exports specific metadata", () => {
    expect(contactMetadata.title).toEqual({ absolute: "Contact | Zolli Cloud" });
    expect(privacyMetadata.title).toEqual({ absolute: "Privacy | Zolli Cloud" });
    expect(termsMetadata.title).toEqual({ absolute: "Terms | Zolli Cloud" });
    expect(securityMetadata.title).toEqual({ absolute: "Security | Zolli Cloud" });
  });

  it("offers console, Calendly, and email contact paths", () => {
    const markup = render(Contact);
    expect(markup).toContain('href="/workspaces"');
    expect(markup).toContain('href="https://calendly.com/phongct1105/zolli-ai"');
    expect(markup).toContain('href="mailto:phongct1105@gmail.com"');
  });

  it("announces the contact Calendly link as opening a new tab", () => {
    const calendly = anchorTags(render(Contact)).find((anchor) =>
      anchor.includes('href="https://calendly.com/phongct1105/zolli-ai"'),
    );

    expect(calendly).toBeDefined();
    expect(calendly).toContain('target="_blank"');
    expect(calendly).toContain('rel="noreferrer"');
    expect(calendly).toContain(
      'aria-label="Schedule with Zolli (opens in a new tab)"',
    );
  });

  it("gives inline information-page links a shared 40px touch target without leaving paragraph flow", () => {
    for (const page of [Contact, Privacy, Terms, Security]) {
      const markup = render(page);
      expect(markup).toMatch(/\[&amp;_a\]:inline-flex/);
      expect(markup).toMatch(/\[&amp;_a\]:min-h-10/);
      expect(markup).toMatch(/\[&amp;_a\]:items-center/);
      expect(markup).toMatch(/\[&amp;_a\]:align-middle/);
    }
  });

  it("keeps privacy, terms, and security claims inside known boundaries", () => {
    expect(render(Privacy)).toContain("account identity");
    expect(render(Privacy)).toContain("job and protocol events");
    expect(render(Terms)).toContain("early-access operational baseline");
    expect(render(Terms)).not.toMatch(/SOC 2|HIPAA|GDPR compliant/);
    expect(render(Security)).toContain("bounded leases");
    expect(render(Security)).toContain("No compliance certification is claimed");
  });
});
