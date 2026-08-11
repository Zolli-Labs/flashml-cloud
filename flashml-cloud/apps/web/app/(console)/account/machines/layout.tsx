import type { Metadata } from "next";

export const metadata: Metadata = { title: "My Machines" };

export default function Layout({ children }: { children: React.ReactNode }) {
  return children;
}
