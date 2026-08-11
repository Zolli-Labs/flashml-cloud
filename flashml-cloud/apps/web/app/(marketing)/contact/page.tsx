import type { Metadata } from "next";
import Link from "next/link";
import { InformationPage } from "@/components/marketing/InformationPage";
import { MARKETING } from "@/lib/marketing";

export const metadata: Metadata = {
  title: { absolute: "Contact | Zolli Cloud" },
  description:
    "Open the Zolli console, schedule a technical conversation, or contact Zolli Labs.",
};

export default function Contact() {
  return (
    <InformationPage
      eyebrow="Contact"
      title="Choose the shortest path."
      intro="Use the console to evaluate Zolli directly, schedule a technical conversation, or send a note to the public contact address."
    >
      <section>
        <h2>Open console</h2>
        <p>
          Start with the current Zolli Cloud workspace and machine workflow.{" "}
          <Link href={MARKETING.consolePath}>Open console</Link>.
        </p>
      </section>

      <section>
        <h2>Schedule with Zolli</h2>
        <p>
          Scheduling is for architecture, onboarding, workload integration, and
          private deployment discussions.{" "}
          <a
            href={MARKETING.calendlyUrl}
            target="_blank"
            rel="noreferrer"
            aria-label="Schedule with Zolli (opens in a new tab)"
          >
            Schedule with Zolli
          </a>
          .
        </p>
      </section>

      <section>
        <h2>Email</h2>
        <p>
          For other product questions, contact{" "}
          <a href={`mailto:${MARKETING.contactEmail}`}>{MARKETING.contactEmail}</a>.
        </p>
      </section>
    </InformationPage>
  );
}
