import type { Metadata } from "next";
import { Fraunces, Geist_Mono, Inter } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
  display: "swap",
});

const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
  display: "swap",
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "ZolliAI",
    template: "%s · ZolliAI",
  },
  description:
    "ZolliAI Cloud brings laptops, GPU rigs, and cloud instances together as a resilient distributed compute crew.",
  keywords: [
    "distributed training",
    "federated learning",
    "fault tolerant training",
    "spot instances",
    "gpu aggregation",
  ],
  openGraph: {
    title: "ZolliAI",
    description:
      "Bring laptops, GPU rigs, and cloud instances together as one resilient compute crew.",
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
      className={`${inter.variable} ${fraunces.variable} ${geistMono.variable}`}
    >
      {/* `grain` paints a fixed paper texture over the warm surfaces. */}
      <body className="grain min-h-dvh flex flex-col antialiased">
        {/* Keyboard and screen-reader users otherwise tab through the whole
            nav on every page before reaching content. Visible only when
            focused. */}
        <a
          href="#content"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[100] focus:rounded-md focus:bg-surface focus:px-4 focus:py-2 focus:text-sm focus:font-medium"
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
          theme="light"
          position="bottom-right"
          closeButton
          toastOptions={{
            className:
              "!bg-surface !text-ink !border !border-border !font-sans !shadow-lg",
          }}
        />
      </body>
    </html>
  );
}
