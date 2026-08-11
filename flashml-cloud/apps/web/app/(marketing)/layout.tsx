import { Navbar } from "@/components/nav/Navbar";

/** Marketing chrome: a top nav over a full-bleed page. The console uses a
 * left rail instead, which is the whole point of the split. Route groups do
 * not appear in URLs, so `/` is still `/`. */
export default function MarketingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="marketing-dark flex min-h-dvh flex-col bg-background text-foreground">
      <Navbar />
      <main id="content" className="flex-1">
        {children}
      </main>
    </div>
  );
}
