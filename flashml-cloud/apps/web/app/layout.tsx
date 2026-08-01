import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Navbar } from "@/components/nav/Navbar";

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

// Was still describing the July POC — "Distributed K-Means Training",
// "Powered by RunPod Flash" — which is the browser tab title and the social
// card for every link anyone shares of this app. Neither claim is true: it
// runs arbitrary PyTorch from a GitHub repo, on volunteers' own machines.
export const metadata: Metadata = {
  title: {
    default: "FlashML",
    template: "%s · FlashML",
  },
  description:
    "Train models across machines people lend you. Point FlashML at a GitHub repo and it runs on a pool of donated laptops, one round at a time.",
  keywords: [
    "distributed training",
    "federated learning",
    "machine learning",
    "volunteer compute",
  ],
  openGraph: {
    title: "FlashML",
    description:
      "Train models across machines people lend you, straight from a GitHub repo.",
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
        <Navbar />
        <main id="content" className="flex-1">
          {children}
        </main>
      </body>
    </html>
  );
}
