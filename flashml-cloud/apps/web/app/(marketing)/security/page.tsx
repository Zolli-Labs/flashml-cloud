import type { Metadata } from "next";
import { InformationPage } from "@/components/marketing/InformationPage";
import { MARKETING } from "@/lib/marketing";

export const metadata: Metadata = {
  title: { absolute: "Security | Zolli Cloud" },
  description:
    "Implemented execution controls and current security boundaries for Zolli Cloud.",
};

export default function Security() {
  return (
    <InformationPage
      eyebrow="Security"
      title="Implemented controls, stated plainly."
      intro="Zolli coordinates work across connected machines. These are the controls implemented in the current runtime and the material boundaries around them."
    >
      <section>
        <h2>Execution boundaries</h2>
        <p>
          Task ownership uses bounded leases. Workloads and execution images are
          allowlisted. Where the Docker execution tier is used, containers run with
          networking disabled, and task environments are scrubbed before execution.
        </p>
      </section>

      <section>
        <h2>Machine identity</h2>
        <p>
          Machine writes are authenticated and scoped to the connected machine&apos;s
          active work. A machine identity does not widen a task beyond its live lease.
        </p>
      </section>

      <section>
        <h2>Artifact integrity</h2>
        <p>
          Artifacts and checkpoint manifests are hash-verified before the runtime
          treats them as committed progress.
        </p>
      </section>

      <section>
        <h2>Recovery history</h2>
        <p>
          Result acceptance is idempotent so repeated completion attempts do not
          create multiple accepted outcomes. An append-oriented event history records
          the protocol transitions used to inspect interruption and recovery.
        </p>
      </section>

      <section>
        <h2>Current boundaries</h2>
        <p>
          Zolli Cloud infrastructure remains early access. Security also depends on
          the selected deployment, connected machines, workload configuration, and
          infrastructure configuration. No compliance certification is claimed.
        </p>
      </section>

      <section>
        <h2>Report a concern</h2>
        <p>
          For responsible disclosure of a suspected security issue, email{" "}
          <a href={`mailto:${MARKETING.contactEmail}`}>{MARKETING.contactEmail}</a>
          with enough detail to identify the affected behavior.
        </p>
      </section>
    </InformationPage>
  );
}
