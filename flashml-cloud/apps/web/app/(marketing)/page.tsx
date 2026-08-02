import { Hero } from "@/components/landing/Hero";
import { LedgerWall } from "@/components/landing/LedgerWall";
import { RecoveryDemo } from "@/components/landing/RecoveryDemo";
import { SupplyTiers } from "@/components/landing/SupplyTiers";
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
      <RecoveryDemo />
      <SupplyTiers />
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
