import { Hero } from "@/components/landing/Hero";
import { EvidenceBand } from "@/components/landing/EvidenceBand";
import { PlatformSupport } from "@/components/landing/PlatformSupport";
import { SystemJourney } from "@/components/landing/SystemJourney";
import { WorkloadFit } from "@/components/landing/WorkloadFit";
import { SystemModules } from "@/components/landing/SystemModules";
import { RecoveryDemo } from "@/components/landing/RecoveryDemo";
import { ProfessionalServices } from "@/components/landing/ProfessionalServices";
import { Faq } from "@/components/landing/Faq";
import { ClosingCta } from "@/components/landing/ClosingCta";
import { LandingMotionProvider } from "@/components/landing/motion/LandingMotionProvider";

export default function Home() {
  return (
    <LandingMotionProvider>
      <div
        data-landing="cinematic"
        className="landing-cinematic w-full max-w-full overflow-x-clip"
      >
        <div data-surface="dark" className="landing-surface-dark">
          <Hero />
        </div>
        <div data-surface="light" className="landing-surface-light">
          <EvidenceBand />
        </div>
        <div data-surface="sand" className="landing-surface-sand">
          <PlatformSupport />
        </div>
        <div data-surface="dark" className="landing-surface-dark">
          <SystemJourney />
        </div>
        <div data-surface="light" className="landing-surface-light">
          <WorkloadFit />
        </div>
        <div data-surface="dark" className="landing-surface-dark">
          <SystemModules />
        </div>
        <div data-surface="light" className="landing-surface-light">
          <RecoveryDemo />
        </div>
        <div data-surface="sand" className="landing-surface-sand">
          <ProfessionalServices />
        </div>
        <div data-surface="light" className="landing-surface-light">
          <Faq />
        </div>
        <div data-surface="orange" className="landing-surface-orange">
          <ClosingCta />
        </div>
      </div>
    </LandingMotionProvider>
  );
}
