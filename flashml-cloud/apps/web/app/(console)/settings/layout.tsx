import { PageShell } from "@/components/shell/PageShell";
import { SectionTabs } from "@/components/nav/SectionTabs";

/**
 * Account plumbing behind the avatar menu: who you are, how the CLI
 * authenticates as you, and which GitHub installation can read your private
 * repos. Three tabs, zero rail entries.
 */
export default function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <PageShell width="reading" className="pb-0">
        <SectionTabs
          tabs={[
            { href: "/settings", label: "Account", exact: true },
            { href: "/settings/cli", label: "CLI access" },
            { href: "/settings/github", label: "GitHub" },
          ]}
        />
      </PageShell>
      {children}
    </>
  );
}
