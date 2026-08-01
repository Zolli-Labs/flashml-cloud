import { Suspense } from "react";
import type { Metadata } from "next";
import { SignInCard } from "./SignInCard";

export const metadata: Metadata = {
  title: "Sign in",
};

export default function SignInPage() {
  return (
    <div className="min-h-[calc(100dvh-4rem)] flex items-center justify-center px-4 py-12">
      <Suspense fallback={null}>
        <SignInCard />
      </Suspense>
    </div>
  );
}
