import type { Metadata } from "next";
import { Geist_Mono, Instrument_Sans } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

const instrumentSans = Instrument_Sans({
  variable: "--font-instrument-sans",
  subsets: ["latin"],
  display: "swap",
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  metadataBase: new URL(
    process.env.NEXT_PUBLIC_SITE_URL ?? "https://zolliai.com",
  ),
  title: {
    default: "Zolli Cloud",
    template: "%s · Zolli Cloud",
  },
  description:
    "Pool cloud GPUs, home rigs, and spare machines with leases, verified checkpoints, and deterministic recovery.",
  keywords: [
    "distributed training",
    "federated learning",
    "fault tolerant training",
    "spot instances",
    "gpu aggregation",
  ],
  icons: {
    icon: [
      {
        url: "/brand/icons/favicon-32.png",
        sizes: "32x32",
        type: "image/png",
      },
      {
        url: "/brand/icons/favicon-64.png",
        sizes: "64x64",
        type: "image/png",
      },
    ],
    apple: [
      {
        url: "/brand/icons/apple-touch-icon-180.png",
        sizes: "180x180",
        type: "image/png",
      },
    ],
  },
  manifest: "/manifest.webmanifest",
  openGraph: {
    title: "Zolli Cloud",
    description:
      "Compute that finishes the job, even when a machine disappears.",
    type: "website",
    images: [
      {
        url: "/brand/social/og-image-1200x630.png",
        width: 1200,
        height: 630,
        alt: "Zolli — distributed compute that survives failure",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Zolli Cloud",
    description:
      "Compute that finishes the job, even when a machine disappears.",
    images: ["/brand/social/og-image-1200x630.png"],
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
      className={`${instrumentSans.variable} ${geistMono.variable}`}
    >
      <body className="min-h-dvh flex flex-col antialiased">
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
