"use client";

import { useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import { Eye, EyeSlash, Warning } from "@phosphor-icons/react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createBrowserSupabaseClient } from "@/lib/supabase";
import { GoogleMark } from "./GoogleMark";

type Pending = "password" | "magic" | "google" | null;
type Mode = "signin" | "signup";

/**
 * Password is the primary path; the magic link is the fallback.
 *
 * It used to be the other way round, which does not survive a real signup
 * day: Supabase's built-in SMTP is rate limited to a handful of messages an
 * hour on the free tier, and it is shared across the whole project. With
 * magic links as the only way in, the fourth friend to try signing up — or
 * the same friend signing in on their phone after their laptop — simply
 * cannot get in, and the error they see is a generic rate-limit message
 * that looks like the site is broken.
 *
 * A password costs zero emails per sign-in, forever. The only email in the
 * whole flow is the one confirmation at signup, and that disappears too if
 * "Confirm email" is off in the dashboard.
 */
const MIN_PASSWORD_LENGTH = 8;

export function SignInCard() {
  const searchParams = useSearchParams();
  const [mode, setMode] = useState<Mode>("signin");
  const [pending, setPending] = useState<Pending>(null);
  const [error, setError] = useState<string | null>(searchParams.get("error"));
  const [notice, setNotice] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [revealed, setRevealed] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);

  const next = searchParams.get("next") || "/machines";
  const redirectTo = () =>
    `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`;

  function reset() {
    setError(null);
    setNotice(null);
  }

  async function submitPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = email.trim();
    if (!trimmed || pending) return;

    if (mode === "signup" && password.length < MIN_PASSWORD_LENGTH) {
      setError(`Use at least ${MIN_PASSWORD_LENGTH} characters.`);
      return;
    }

    setPending("password");
    reset();
    const supabase = createBrowserSupabaseClient();

    if (mode === "signin") {
      const { error: authError } = await supabase.auth.signInWithPassword({
        email: trimmed,
        password,
      });
      setPending(null);
      if (authError) {
        setError(readableAuthError(authError.message));
        return;
      }
      // A full reload rather than a router push: the session lives in
      // cookies the middleware reads on the server, and a client-side
      // navigation would render the next page against the stale request.
      window.location.assign(next);
      return;
    }

    const { data, error: authError } = await supabase.auth.signUp({
      email: trimmed,
      password,
      options: { emailRedirectTo: redirectTo() },
    });
    setPending(null);

    if (authError) {
      setError(readableAuthError(authError.message));
      return;
    }

    // Supabase deliberately returns a success-shaped response when the
    // address is already registered, so an attacker cannot enumerate users
    // by watching for a different error. The tell is an empty `identities`
    // array. Without this branch the person sees "check your email", waits
    // for a message that never arrives, and concludes signup is broken —
    // when in fact they already have an account.
    if (data.user && data.user.identities?.length === 0) {
      setMode("signin");
      setNotice("That email is already registered. Sign in with your password.");
      return;
    }

    // Session present => email confirmation is off in the dashboard, and
    // they are already signed in.
    if (data.session) {
      window.location.assign(next);
      return;
    }

    setSentTo(trimmed);
  }

  async function sendMagicLink() {
    const trimmed = email.trim();
    if (!trimmed) {
      setError("Enter your email address first.");
      return;
    }
    if (pending) return;
    setPending("magic");
    reset();
    const supabase = createBrowserSupabaseClient();
    const { error: authError } = await supabase.auth.signInWithOtp({
      email: trimmed,
      options: { emailRedirectTo: redirectTo() },
    });
    setPending(null);
    if (authError) {
      setError(readableAuthError(authError.message));
      return;
    }
    setSentTo(trimmed);
  }

  async function signInWithGoogle() {
    if (pending) return;
    setPending("google");
    reset();
    const supabase = createBrowserSupabaseClient();
    const { error: authError } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: redirectTo() },
    });
    if (authError) {
      // A provider error here, rather than a redirect to Google, almost
      // always means Google sign-in is not enabled in the Supabase
      // dashboard yet. Say that plainly instead of surfacing a raw
      // provider error.
      setError("Google sign-in isn't set up yet — use your email and password.");
      setPending(null);
    }
    // On success the browser navigates to Google; nothing left to render.
  }

  if (sentTo) {
    return (
      <section className="glass w-full max-w-sm rounded-xl p-7 rise">
        <h1 className="text-xl font-semibold">Check your email</h1>
        <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
          A sign-in link is on its way to{" "}
          <span className="font-medium text-foreground break-all">{sentTo}</span>.
          Open it on this device — it signs you in right here, in this browser.
        </p>
        <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
          Nothing after a minute? Check spam. Links are rate limited, so if
          you have requested several, the later ones may be delayed.
        </p>
        <button
          type="button"
          className="mt-5 text-sm font-medium text-primary underline underline-offset-4 hover:no-underline"
          onClick={() => {
            setSentTo(null);
            reset();
          }}
        >
          Back to sign in
        </button>
      </section>
    );
  }

  const signingUp = mode === "signup";

  return (
    <section className="glass w-full max-w-sm rounded-xl p-7 rise">
      <h1 className="text-xl font-semibold tracking-tight">
        {signingUp ? "Create an account" : "Sign in"}
      </h1>
      <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
        {signingUp
          ? "Submit training jobs, or lend a machine to the pool."
          : "Welcome back."}
      </p>

      <form onSubmit={submitPassword} className="mt-6 flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor="email" className="text-xs font-medium">
            Email
          </Label>
          <Input
            id="email"
            name="email"
            type="email"
            inputMode="email"
            autoComplete="email"
            placeholder="you@example.com"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={pending !== null}
            className="h-11"
          />
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="password" className="text-xs font-medium">
            Password
          </Label>
          <div className="relative">
            <Input
              id="password"
              name="password"
              type={revealed ? "text" : "password"}
              // Tells password managers whether to offer to save a new
              // credential or fill an existing one. Getting this wrong is
              // why so many sign-up forms fail to trigger the save prompt.
              autoComplete={signingUp ? "new-password" : "current-password"}
              placeholder={signingUp ? `At least ${MIN_PASSWORD_LENGTH} characters` : "••••••••"}
              required
              minLength={signingUp ? MIN_PASSWORD_LENGTH : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={pending !== null}
              className="h-11 pr-11"
            />
            <button
              type="button"
              onClick={() => setRevealed((v) => !v)}
              className="absolute right-1 top-1/2 -translate-y-1/2 rounded-md p-2 text-muted-foreground transition-colors hover:text-foreground"
              aria-label={revealed ? "Hide password" : "Show password"}
            >
              {revealed ? (
                <EyeSlash className="h-4 w-4" />
              ) : (
                <Eye className="h-4 w-4" />
              )}
            </button>
          </div>
        </div>

        <Button
          type="submit"
          size="lg"
          className="interactive h-11 w-full"
          disabled={pending !== null}
        >
          {pending === "password"
            ? signingUp
              ? "Creating account…"
              : "Signing in…"
            : signingUp
              ? "Create account"
              : "Sign in"}
        </Button>
      </form>

      {error ? (
        <p
          role="alert"
          className="mt-4 flex items-start gap-2 rounded-lg border border-destructive/25 bg-destructive/10 px-3 py-2 text-xs leading-relaxed text-destructive"
        >
          <Warning className="mt-0.5 h-4 w-4 shrink-0" weight="fill" />
          <span>{error}</span>
        </p>
      ) : null}

      {notice ? (
        <p
          role="status"
          className="mt-4 rounded-lg border border-border bg-white/[0.03] px-3 py-2 text-xs leading-relaxed text-muted-foreground"
        >
          {notice}
        </p>
      ) : null}

      <div className="my-6 flex items-center gap-3">
        <span className="h-px flex-1 bg-border" />
        <span className="text-[11px] uppercase tracking-wider text-muted-foreground">
          or
        </span>
        <span className="h-px flex-1 bg-border" />
      </div>

      <div className="flex flex-col gap-3">
        <Button
          type="button"
          variant="outline"
          size="lg"
          className="interactive h-11 w-full gap-2.5"
          disabled={pending !== null}
          onClick={signInWithGoogle}
        >
          <GoogleMark className="h-4 w-4" />
          {pending === "google" ? "Redirecting…" : "Continue with Google"}
        </Button>

        <button
          type="button"
          onClick={sendMagicLink}
          disabled={pending !== null}
          className="text-center text-sm text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
        >
          {pending === "magic" ? "Sending…" : "Email me a sign-in link instead"}
        </button>
      </div>

      <p className="mt-7 border-t border-border pt-5 text-center text-sm text-muted-foreground">
        {signingUp ? "Already have an account?" : "No account yet?"}{" "}
        <button
          type="button"
          onClick={() => {
            setMode(signingUp ? "signin" : "signup");
            setPassword("");
            reset();
          }}
          className="font-medium text-primary underline underline-offset-4 hover:no-underline"
        >
          {signingUp ? "Sign in" : "Create one"}
        </button>
      </p>
    </section>
  );
}

/**
 * Supabase's raw messages are written for developers reading a network tab,
 * not for someone who just mistyped their password. Translate the ones that
 * have an obvious next action; pass anything unrecognised through rather
 * than swallowing a message that might be the only clue.
 */
function readableAuthError(message: string): string {
  const m = message.toLowerCase();
  if (m.includes("invalid login credentials")) {
    return "That email and password don't match an account. Check both, or create an account below.";
  }
  if (m.includes("email not confirmed")) {
    return "Confirm your email first — open the link we sent when you signed up.";
  }
  if (m.includes("rate limit") || m.includes("too many requests")) {
    return "Too many attempts for now. Wait a minute, then try again — signing in with a password avoids this entirely.";
  }
  if (m.includes("password should be")) {
    return `Use at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  if (m.includes("user already registered")) {
    return "That email is already registered. Sign in instead.";
  }
  return message;
}
