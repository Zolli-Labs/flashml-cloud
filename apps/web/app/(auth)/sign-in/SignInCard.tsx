"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { Lightning, Warning } from "@phosphor-icons/react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { createBrowserSupabaseClient } from "@/lib/supabase";
import { GoogleMark } from "./GoogleMark";

export function SignInCard() {
  const searchParams = useSearchParams();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(
    searchParams.get("error")
  );

  async function signInWithGoogle() {
    setPending(true);
    setError(null);
    const supabase = createBrowserSupabaseClient();
    const next = searchParams.get("next") || "/machines";
    const { error: authError } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`,
      },
    });
    if (authError) {
      // A provider error here (rather than a redirect to Google) almost
      // always means Google sign-in has not been enabled in the Supabase
      // dashboard yet — see apps/web/README.md.
      setError(authError.message);
      setPending(false);
    }
    // On success the browser navigates away to Google, so there is
    // nothing further to render on this render pass.
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader className="items-center text-center gap-3 pt-2">
        <div className="relative flex items-center justify-center w-10 h-10">
          <div className="absolute inset-0 rounded-md bg-cyan/10 border border-cyan/30" />
          <Lightning className="relative z-10 text-cyan w-5 h-5" weight="fill" />
        </div>
        <div>
          <h1 className="font-mono font-bold text-lg tracking-tight text-foreground">
            Flash<span className="text-cyan">ML</span>
          </h1>
          <p className="text-sm text-muted-foreground mt-1 max-w-64">
            Train models on machines volunteered by people you trust.
          </p>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 pb-2">
        <Button
          type="button"
          variant="outline"
          size="lg"
          className="w-full h-11 gap-2.5 text-sm"
          disabled={pending}
          onClick={signInWithGoogle}
        >
          <GoogleMark className="w-4 h-4" />
          {pending ? "Redirecting…" : "Continue with Google"}
        </Button>
        {error ? (
          <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            <Warning className="w-4 h-4 shrink-0 mt-0.5" weight="fill" />
            <span>{error}</span>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
