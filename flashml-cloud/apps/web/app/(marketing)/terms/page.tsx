import type { Metadata } from "next";
import { InformationPage } from "@/components/marketing/InformationPage";
import { MARKETING } from "@/lib/marketing";

export const metadata: Metadata = {
  title: { absolute: "Terms | Zolli Cloud" },
  description: "The early-access operational terms for evaluating Zolli Cloud.",
};

export default function Terms() {
  return (
    <InformationPage
      eyebrow="Terms"
      title="Early-access operating terms."
      intro="These terms describe the current evaluation baseline for Zolli Cloud. They are deliberately limited to the early product as it operates today."
    >
      <section>
        <h2>Early access</h2>
        <p>
          These terms are an early-access operational baseline and require legal
          review before a paid public launch.
        </p>
        <p>
          The service is available for evaluation and may expose unfinished or
          changing behavior.
        </p>
      </section>

      <section>
        <h2>Your code and machines</h2>
        <p>
          You choose the code, workloads, and machines you connect. Only connect
          resources and submit material that you are authorized to use, and keep
          credentials out of submitted code and job inputs.
        </p>
      </section>

      <section>
        <h2>Acceptable use</h2>
        <p>
          Do not use Zolli to access systems without authorization, interfere with
          the service or other users, distribute malicious code, or submit workloads
          that violate applicable restrictions on the machines or infrastructure you use.
        </p>
      </section>

      <section>
        <h2>Service changes and availability</h2>
        <p>
          Early-access features, interfaces, and availability may change. Jobs can
          be interrupted by connected machines, networks, deployment configuration,
          or changes to the service, so important work should keep its own source and outputs.
        </p>
      </section>

      <section>
        <h2>Open runtime</h2>
        <p>
          Zolli Cloud uses FlashML, the separately published open runtime and wire
          protocol. Use of the managed Zolli Cloud service and use of that public
          software are distinct.
        </p>
      </section>

      <section>
        <h2>Warranty and liability</h2>
        <p>
          This early-access baseline does not state a production warranty or a final
          allocation of liability. Those terms require a reviewed agreement before a
          paid public launch.
        </p>
      </section>

      <section>
        <h2>Contact</h2>
        <p>
          Send questions about these early-access terms to{" "}
          <a href={`mailto:${MARKETING.contactEmail}`}>{MARKETING.contactEmail}</a>.
        </p>
      </section>
    </InformationPage>
  );
}
