"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";

/**
 * Console-only theme switching, `system` by default. `attribute="class"`
 * puts `.dark` on `<html>`; `app/globals.css`'s console dark register then
 * only lights up where a `.console-theme` element is mounted somewhere in
 * the body (see the `body:has(.console-theme)` block there), so marketing
 * and auth pages — wrapped in `.marketing-dark` instead — never see this.
 * `disableTransitionOnChange` avoids every colour transition on the page
 * firing at once the instant the user (or the OS) flips the theme.
 */
export function ThemeProvider({ children }: { children: React.ReactNode }) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
    >
      {children}
    </NextThemesProvider>
  );
}
