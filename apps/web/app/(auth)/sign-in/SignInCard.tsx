"use client";

import { useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import { CheckCircle, Lightning, PaperPlaneTilt, Warning } from "@phosphor-icons/react";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { createBrowserSupabaseClient } from "@/lib/supabase";
import { GoogleMark } from "./GoogleMark";

type Pending = "email" | "google" | null;

export function SignInCard() {
  const searchParams = useSearchParams();
  const [pending, setPending] = useState<Pending>(null);
  const [error, setError] = useState<string | null>(
    searchParams.get("error")
  );
  const [email, setEmail] = useState("");
  // Set once a magic link has actually been sent, so the "check your
  // email" screen names the address it went to — re-derived from the
  // form, never guessed.
  const [sentTo, setSentTo] = useState<string | null>(null);

  const next = searchParams.get("next") || "/machines";

  async function signInWithEmail(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = email.trim();
    if (!trimmed || pending) return;
    setPending("email");
    setError(null);
    const supabase = createBrowserSupabaseClient();
    const { error: authError } = await supabase.auth.signInWithOtp({
      email: trimmed,
      options: {
        emailRedirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`,
      },
    });
    setPending(null);
    if (authError) {
      setError(authError.message);
      return;
    }
    setSentTo(trimmed);
  }

  async function signInWithGoogle() {
    if (pending) return;
    setPending("google");
    setError(null);
    const supabase = createBrowserSupabaseClient();
    const { error: authError } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`,
      },
    });
    if (authError) {
      // A provider error here (rather than a redirect to Google) almost
      // always means Google sign-in has not been enabled in the Supabase
      // dashboard yet — see apps/web/README.md. Say that plainly rather
      // than surface Supabase's raw provider error or fail silently.
      setError(
        "Google sign-in isn't set up for this deployment yet — use email above."
      );
      setPending(null);
    }
    // On success the browser navigates away to Google, so there is
    // nothing further to render on this render pass.
  }

  if (sentTo) {
    return (
      <Card className="w-full max-w-sm">
        <CardHeader className="items-center text-center gap-3 pt-2">
          <div className="relative flex items-center justify-center w-10 h-10">
            <div className="absolute inset-0 rounded-md bg-cyan/10 border border-cyan/30" />
            <CheckCircle
              className="relative z-10 text-cyan w-5 h-5"
              weight="fill"
            />
          </div>
          <h1 className="font-mono font-bold text-lg tracking-tight text-foreground">
            Check your email
          </h1>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 pb-2 text-center">
          <p className="text-sm text-muted-foreground text-balance">
            We sent a sign-in link to{" "}
            <span className="text-foreground font-medium break-all">
              {sentTo}
            </span>
            . Open it on <span className="text-foreground font-medium">this
            device</span> — the link signs you in right here, in this
            browser.
          </p>
          <p className="text-xs text-muted-foreground">
            Didn&apos;t get it? Check spam, or{" "}
            <button
              type="button"
              className="text-cyan underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50 rounded"
              onClick={() => {
                setSentTo(null);
                setError(null);
              }}
            >
              try a different address
            </button>
            .
          </p>
        </CardContent>
      </Card>
    );
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
      <CardContent className="flex flex-col gap-4 pb-2">
        <form onSubmit={signInWithEmail} className="flex flex-col gap-2">
          <Label htmlFor="sign-in-email" className="sr-only">
            Email address
          </Label>
          <Input
            id="sign-in-email"
            name="email"
            type="email"
            inputMode="email"
            autoComplete="email"
            placeholder="you@example.com"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            disabled={pending !== null}
            className="h-11 text-sm"
          />
          <Button
            type="submit"
            size="lg"
            className="w-full h-11 gap-2 text-sm"
            disabled={pending !== null}
          >
            <PaperPlaneTilt className="w-4 h-4" weight="fill" />
            {pending === "email" ? "Sending link…" : "Continue with email"}
          </Button>
        </form>

        <div className="flex items-center gap-3">
          <Separator className="flex-1" />
          <span className="text-xs text-muted-foreground">or</span>
          <Separator className="flex-1" />
        </div>

        <Button
          type="button"
          variant="outline"
          size="lg"
          className="w-full h-11 gap-2.5 text-sm"
          disabled={pending !== null}
          onClick={signInWithGoogle}
        >
          <GoogleMark className="w-4 h-4" />
          {pending === "google" ? "Redirecting…" : "Continue with Google"}
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
