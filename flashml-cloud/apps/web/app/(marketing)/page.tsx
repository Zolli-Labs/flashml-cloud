import { Hero } from "@/components/landing/Hero";
import { LedgerWall } from "@/components/landing/LedgerWall";
import { RecoveryDemo } from "@/components/landing/RecoveryDemo";
import { SchedulerComparison } from "@/components/landing/SchedulerComparison";
import { SupplyTiers } from "@/components/landing/SupplyTiers";
import { FrameworkStack } from "@/components/landing/FrameworkStack";
import { Guarantees } from "@/components/landing/Guarantees";
import { HowItWorks } from "@/components/landing/HowItWorks";
import { HonestState } from "@/components/landing/HonestState";
import { ClosingCta } from "@/components/landing/ClosingCta";

// Order matters more than it looks. The claim is made in the hero and
// proved immediately below it, before the page asks for anything. Leading
// with the supply tiers would be asking the reader to accept "unreliable
// machines are fine" on trust, which is the whole thing in question.
export default function Home() {
  return (
    <>
      <Hero />
      {/* Frames the problem before RecoveryDemo explains the mechanism.
          Without it the page only ever showed our own behaviour, so there
          was nothing to measure the claim against. */}
      <SchedulerComparison />
      <RecoveryDemo />
      <SupplyTiers />
      {/* Answers "will it run my code" — the question a reader has before
          any of the reliability argument matters to them. */}
      <FrameworkStack />
      <Guarantees />
      {/* The one full-bleed moment. Placed between two container sections
          so the break is felt: everything above and below is a centred
          column, and this is not. */}
      <LedgerWall />
      <HowItWorks />
      <HonestState />
      <ClosingCta />
    </>
  );
}
