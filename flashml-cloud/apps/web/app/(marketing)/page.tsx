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
import { MarketStory } from "@/components/landing/MarketStory";
import { SimpleJourney } from "@/components/landing/SimpleJourney";
import { LandingMotionProvider } from "@/components/landing/motion/LandingMotionProvider";

export default function Home() {
  return (
    <LandingMotionProvider>
      <div
        data-landing="cinematic"
        className="landing-cinematic w-full max-w-full overflow-x-clip"
      >
        <div data-surface="dark" className="landing-surface-dark">
          {/*
            The hero is a scroll story. The map is pinned for the length of this
            track and the distance the track has left to travel is the timeline
            `useMapStory` reads out of it — leases running, a machine lost, the
            work resumed elsewhere, the result accepted.

            Only where the hero fits a frame. Below `xl` it is taller than the
            viewport, and pinning something taller than the viewport puts its own
            readout permanently out of reach; the track then collapses to the
            hero's own height, which is exactly the measurement that hands the
            story to the timer fallback.
          */}
          <div data-hero-scroll className="relative xl:h-[220svh]">
            <div className="xl:sticky xl:top-0">
              <Hero />
            </div>
          </div>
        </div>
        <div data-surface="light" className="landing-surface-light">
          <MarketStory />
        </div>
        <div data-surface="dark" className="landing-surface-dark">
          <SimpleJourney />
        </div>
        <div data-surface="light" className="landing-surface-light">
          <RecoveryDemo />
        </div>
        <div data-surface="light" className="landing-surface-light">
          <EvidenceBand />
        </div>
        <div data-surface="light" className="landing-surface-light">
          <WorkloadFit />
        </div>
        <div data-surface="sand" className="landing-surface-sand">
          <PlatformSupport />
        </div>
        <div data-surface="dark" className="landing-surface-dark">
          <SystemJourney />
        </div>
        <div data-surface="dark" className="landing-surface-dark">
          <SystemModules />
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
