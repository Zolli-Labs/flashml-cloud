import { Suspense } from "react";
import type { Metadata } from "next";
import { SignInCard } from "./SignInCard";

export const metadata: Metadata = {
  title: "Sign in",
};

export default function SignInPage() {
  return (
    <Suspense
      fallback={
        <main
          id="content"
          className="min-h-dvh bg-cream"
          aria-label="Loading authentication"
        />
      }
    >
      <SignInCard />
    </Suspense>
  );
}
