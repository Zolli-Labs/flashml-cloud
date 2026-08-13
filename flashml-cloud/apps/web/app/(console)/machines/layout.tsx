import { PageShell } from "@/components/shell/PageShell";
import { SectionTabs } from "@/components/nav/SectionTabs";

/**
 * One rail destination, two views: the fleet you own, and the way a new
 * machine joins it. "Add" is a tab rather than a rail card so the whole
 * host journey lives on one screen.
 */
export default function MachinesLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <PageShell width="wide" className="pb-0">
        <SectionTabs
          tabs={[
            { href: "/machines", label: "My machines", exact: true },
            { href: "/machines/add", label: "Add a machine" },
          ]}
        />
      </PageShell>
      {children}
    </>
  );
}
