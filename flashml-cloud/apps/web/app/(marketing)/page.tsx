import { Hero } from "@/components/landing/Hero";
import { PriceBoard } from "@/components/landing/PriceBoard";
import { HowItWorks } from "@/components/landing/HowItWorks";
import { RecoveryDemo } from "@/components/landing/RecoveryDemo";
import { PlatformStrip } from "@/components/landing/PlatformStrip";
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
          {/*
            The hero is a looping story, not a scroll story. `useMapStory`
            walks its beats on its own timer and repeats them forever; it
            used to read the remaining travel of a pinned 220svh track here
            instead, but that made the story's shape depend on breakpoint and
            entry point (see the comment above `useMapStory` for the retired
            mechanics and why they went). Nothing here pins or measures the
            hero any more — it renders once, like every other section.
          */}
          <Hero />
        </div>
        <div data-surface="sand" className="landing-surface-sand">
          <PriceBoard />
        </div>
        <div data-surface="dark" className="landing-surface-dark">
          <HowItWorks />
        </div>
        <div data-surface="light" className="landing-surface-light">
          <RecoveryDemo />
        </div>
        <div data-surface="sand" className="landing-surface-sand">
          <PlatformStrip />
        </div>
        <div data-surface="dark" className="landing-surface-dark">
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
