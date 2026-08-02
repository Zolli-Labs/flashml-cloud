import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
  display: "swap",
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  display: "swap",
});

// This is the browser tab title and the social card for every link anyone
// shares. It has been wrong twice: first describing the July K-Means POC,
// then describing donated laptops, which the 2026-08-02 supply-side note
// rates as the least valuable tier and no longer the pitch. The positioning
// here is that note's conclusion: aggregation for fault-tolerant, shardable
// training across whatever compute is cheap, which is the same thing as
// saying across whatever compute is unreliable.
export const metadata: Metadata = {
  title: {
    default: "FlashML",
    template: "%s · FlashML",
  },
  description:
    "Cheap compute disappears. FlashML spreads a training job across pods, rigs and spot instances that vanish mid-run: leases expire, work requeues, jobs finish.",
  keywords: [
    "distributed training",
    "federated learning",
    "fault tolerant training",
    "spot instances",
    "gpu aggregation",
  ],
  openGraph: {
    title: "FlashML",
    description:
      "Cheap compute disappears. Run on it anyway. Fault-tolerant distributed training across pods, rigs and spot instances.",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} dark`}
    >
      {/* `grain` paints a fixed noise layer over everything (globals.css).
          Large flat dark surfaces band visibly on 8-bit panels, and the
          grain both hides that and gives the glass panels something to
          refract instead of a perfectly clean gradient. */}
      <body className="grain min-h-dvh flex flex-col antialiased">
        {/* Keyboard and screen-reader users otherwise tab through the whole
            nav on every page before reaching content. Visible only when
            focused. */}
        <a
          href="#content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[100] focus:rounded-md focus:bg-surface-elevated focus:px-4 focus:py-2 focus:text-sm focus:font-medium"
        >
          Skip to content
        </a>
        {/* No chrome here. The landing and the console wear different
            chrome (a top nav vs a left rail), so each route group supplies
            its own layout and its own <main id="content">. */}
        {/* `delay`, not `delayDuration`. This project's shadcn style is
            base-nova, which builds on Base UI rather than Radix, and the two
            libraries name this prop differently. */}
        <TooltipProvider delay={250}>{children}</TooltipProvider>

        {/* Toasts. Until now every async action was silent or reported
            itself with inline text that vanished on the next poll: cancel a
            job, revoke a machine, save a display name, and the only way to
            know it worked was to notice a row change. `richColors` is off
            deliberately — sonner's own palette would introduce a second
            green and a second red alongside the semantic ones this app
            already defines. */}
        <Toaster
          theme="dark"
          position="bottom-right"
          closeButton
          toastOptions={{
            className:
              "!bg-surface !text-foreground !border !border-border !font-sans",
          }}
        />
      </body>
    </html>
  );
}
