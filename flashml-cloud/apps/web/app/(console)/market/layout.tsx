import { PageShell } from "@/components/shell/PageShell";
import { SectionTabs } from "@/components/nav/SectionTabs";

/**
 * One rail destination, three views. Prices leads: the board is what the
 * market IS; listings are the supply behind it and credits are the
 * account's side of it.
 */
export default function MarketLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <PageShell width="wide" className="pb-0">
        <SectionTabs
          tabs={[
            { href: "/market/prices", label: "Prices" },
            { href: "/market/providers", label: "Providers" },
            { href: "/market/listings", label: "Listings" },
            { href: "/market/credits", label: "Credits" },
          ]}
        />
      </PageShell>
      {children}
    </>
  );
}
