import { Hero } from "@/components/landing/Hero";
import { CrewStory } from "@/components/landing/CrewStory";
import { CrewRoles } from "@/components/landing/CrewRoles";
import { RecoveryDemo } from "@/components/landing/RecoveryDemo";
import { ClosingCta } from "@/components/landing/ClosingCta";

export default function Home() {
  return (
    <>
      <Hero />
      <CrewStory />
      <CrewRoles />
      <RecoveryDemo />
      <ClosingCta />
    </>
  );
}
