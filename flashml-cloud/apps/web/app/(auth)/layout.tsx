/**
 * `console-theme` here, not on `AuthShell` alone. This route group has
 * THREE screens that render independently of each other — `sign-in/page.tsx`'s
 * `Suspense` loading fallback, `AuthShell` itself (credentials step AND the
 * onboarding step-2 it wraps via `bare`), and `SignInCardContent`'s
 * "needsConfirmation" card, which bypasses `AuthShell` entirely — and a marker
 * placed on any ONE of them would leave the other two stuck light in dark
 * mode. One layout-level marker covers all three, present and future.
 *
 * WHAT THE MARKER MEANS. `.console-theme` is `app/globals.css`'s trigger for
 * `html.dark:has(.console-theme)` — the console's dark-token block. Reading
 * it as "console-only" is now wrong: as of this pass it means "product
 * surface that follows the user's theme", which includes auth (a signed-out
 * visitor about to become a console user should not hit a jarring light seam
 * on the way in) alongside the console proper. It also carries
 * `.console-theme`'s Geist Sans face — desirable here too, for the same
 * reason: this is product chrome, not the marketing page. Marketing
 * (`.marketing-dark`) remains the one surface with a fixed register that
 * does NOT move with the reader's theme preference.
 */
export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <div className="console-theme contents">{children}</div>;
}
