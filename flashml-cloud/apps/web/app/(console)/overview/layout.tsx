import type { Metadata } from "next";

// Every console page is a Client Component, and a Client Component cannot
// export `metadata` — so without these thin server layouts every tab in the
// browser says "FlashML" and nothing else. The root layout's template turns
// this into "Overview · FlashML".
export const metadata: Metadata = { title: "Overview" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
