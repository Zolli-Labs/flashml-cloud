import { ConsoleShell } from "@/components/shell/ConsoleShell";

/** Console chrome: left rail, sticky top bar carrying the fleet pill, and a
 * flat content column. No translucent surfaces or atmosphere in here. */
export default function ConsoleLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <ConsoleShell>{children}</ConsoleShell>;
}
