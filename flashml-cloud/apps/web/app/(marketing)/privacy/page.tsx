import type { Metadata } from "next";
import { InformationPage } from "@/components/marketing/InformationPage";
import { MARKETING } from "@/lib/marketing";

export const metadata: Metadata = {
  title: { absolute: "Privacy | Zolli Cloud" },
  description:
    "A plain-language notice about information used to operate the current Zolli Cloud service.",
};

export default function Privacy() {
  return (
    <InformationPage
      eyebrow="Privacy"
      title="A narrow operational notice."
      intro="This notice describes the information visibly used by the current Zolli Cloud application and why the service uses it."
    >
      <section>
        <h2>Information used by the service</h2>
        <p>The current application uses:</p>
        <ul>
          <li>account identity supplied through the authentication provider;</li>
          <li>workspace and machine metadata used to organize connected compute;</li>
          <li>job and protocol events produced while work is scheduled and executed;</li>
          <li>contribution records associated with accepted work; and</li>
          <li>operational logs used to run and diagnose the service.</li>
        </ul>
      </section>

      <section>
        <h2>Why it is used</h2>
        <p>
          Zolli uses this information to authenticate access, present workspaces,
          coordinate machines and jobs, show execution and recovery history,
          attribute accepted contributions, and investigate service behavior.
        </p>
      </section>

      <section>
        <h2>Infrastructure and service providers</h2>
        <p>
          Authentication and deployed infrastructure services process the information
          needed for their part of the application. The infrastructure involved can
          vary with the deployment configuration.
        </p>
      </section>

      <section>
        <h2>Your requests</h2>
        <p>
          You can ask about access to, correction of, or deletion of information
          associated with your account. Zolli will assess the request against the
          information and operational records held by the current service.
        </p>
      </section>

      <section>
        <h2>Contact</h2>
        <p>
          Send privacy questions or requests to{" "}
          <a href={`mailto:${MARKETING.contactEmail}`}>{MARKETING.contactEmail}</a>.
        </p>
      </section>
    </InformationPage>
  );
}
