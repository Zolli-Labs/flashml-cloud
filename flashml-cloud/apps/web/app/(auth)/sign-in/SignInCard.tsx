"use client";

import { useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import { Eye, EyeSlash, Warning } from "@phosphor-icons/react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { createBrowserSupabaseClient } from "@/lib/supabase";
import { safeNext } from "@/lib/safe-next";
import { OnboardingForm } from "@/components/onboarding/OnboardingForm";
import { GoogleMark } from "./GoogleMark";

type Pending = "password" | "google" | null;
type Mode = "signin" | "signup";

/** Which screen of the signup wizard is showing.
 *
 * `credentials` covers both signing in and creating an account — they are the
 * same form. `details` is reachable only from a signup that returned a
 * session, and is the access request itself. */
type Step = "credentials" | "details";

/**
 * Email and password only. This flow sends no email at all.
 *
 * The magic link is deliberately gone rather than kept as a fallback.
 * Supabase's built-in SMTP allows roughly two messages an hour PER PROJECT,
 * shared across every account, and the failure it produces is uniquely
 * misleading: the obvious recovery — try another address — hits the same
 * quota and fails identically, so it reads as the site being broken rather
 * than throttled. Verified in this project's auth log as
 * `over_email_send_rate_limit` on /signup for four different addresses in
 * under three minutes.
 *
 * Password auth costs zero emails, always. Nothing here can be rate limited
 * by an email quota, so nothing here needs a custom SMTP provider to work
 * for more than a handful of people.
 *
 * DASHBOARD PREREQUISITE: "Confirm email" must be OFF (Authentication ->
 * Sign In / Providers -> Email). With it on, Supabase still sends a
 * confirmation at signup and the account cannot be used until it is opened
 * — which reintroduces the exact dependency this removes. The signup path
 * below detects that state and says so plainly rather than showing a
 * "check your email" screen for a message the quota may have eaten.
 */
const MIN_PASSWORD_LENGTH = 8;

export function SignInCard() {
  const searchParams = useSearchParams();
  const [mode, setMode] = useState<Mode>("signin");
  const [step, setStep] = useState<Step>("credentials");
  const [pending, setPending] = useState<Pending>(null);
  const [error, setError] = useState<string | null>(searchParams.get("error"));
  const [notice, setNotice] = useState<string | null>(null);
  const [needsConfirmation, setNeedsConfirmation] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [revealed, setRevealed] = useState(false);

  // `safeNext`, not a bare `|| "/machines"`: `next` is attacker-controlled
  // (a crafted sign-in link, or middleware forwarding a crafted pathname),
  // and this value goes straight to `window.location.assign` below. See
  // `lib/safe-next.ts`.
  const next = safeNext(searchParams.get("next"));

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
      // A full reload, not a router push: the session lives in cookies the
      // middleware reads server-side, and a client navigation would render
      // the next page against the request that had no session on it.
      window.location.assign(next);
      return;
    }

    const { data, error: authError } = await supabase.auth.signUp({
      email: trimmed,
      password,
    });
    setPending(null);

    if (authError) {
      setError(readableAuthError(authError.message));
      return;
    }

    // Supabase returns a success-shaped response for an ALREADY REGISTERED
    // address on purpose, so users cannot be enumerated by watching for a
    // different error. The tell is an empty `identities` array. Untreated,
    // the person is told to check their email, waits for a message that was
    // never sent, and concludes signup is broken — when they simply already
    // have an account.
    if (data.user && data.user.identities?.length === 0) {
      setMode("signin");
      setNotice("That email is already registered. Sign in with your password.");
      return;
    }

    // Signed up AND signed in, which is what happens whenever "Confirm
    // email" is off. Ask for the access request here, as step 2, instead of
    // navigating into a console the account cannot use yet.
    //
    // The account already exists at this point and that is deliberate: the
    // request row needs a user id to hang off, so there is no arrangement
    // where the questions come first. What this buys is that nobody is shown
    // the product and then told to apply for it.
    if (data.session) {
      setStep("details");
      return;
    }

    // No session and a real new user => "Confirm email" is still on. This is
    // a deployment misconfiguration, not something the person signing up can
    // fix, so name it instead of pretending an email is on its way.
    setNeedsConfirmation(true);
  }

  async function signInWithGoogle() {
    if (pending) return;
    setPending("google");
    reset();
    const supabase = createBrowserSupabaseClient();
    const { error: authError } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`,
      },
    });
    if (authError) {
      // A provider error here, rather than a redirect to Google, means
      // Google is not enabled in the Supabase dashboard. Confirmed in this
      // project's auth log as `provider is not enabled` on /authorize.
      setError("Google sign-in isn't enabled for this deployment — use your email and password.");
      setPending(null);
    }
    // On success the browser navigates to Google; nothing left to render.
  }

  // Step 2. The SAME component the console renders for an account that
  // arrives here already signed up — not a copy. A second seven-field form
  // would drift from this one, and a request that reaches an admin missing
  // fields is worse than one that never arrives.
  //
  // `window.location.assign`, not a router push: the middleware reads the
  // session server-side, and this is the first navigation the fresh session
  // makes. A client navigation would render the next page against the
  // request that had no session on it — the same reason the sign-in path
  // above uses it.
  if (step === "details") {
    return (
      <OnboardingForm
        stepLabel="Step 2 of 2"
        onSubmitted={() => window.location.assign(next)}
      />
    );
  }

  if (needsConfirmation) {
    return (
      <section className="glass w-full max-w-sm rounded-xl p-7 rise">
        <h1 className="text-xl font-semibold tracking-tight">
          Account created, but not usable yet
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
          This deployment still requires email confirmation, so the account
          can&apos;t sign in until a confirmation link is opened.
        </p>
        <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
          If you run this deployment: turn off{" "}
          <span className="font-medium text-foreground">Confirm email</span> in
          Supabase under Authentication → Sign In / Providers → Email, then
          sign in normally. No email is involved once it is off.
        </p>
        <button
          type="button"
          className="mt-5 text-sm font-medium text-primary underline underline-offset-4 hover:no-underline"
          onClick={() => {
            setNeedsConfirmation(false);
            setMode("signin");
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
              // Tells a password manager whether to offer to save a new
              // credential or fill an existing one. Getting this wrong is
              // why so many sign-up forms never trigger the save prompt.
              autoComplete={signingUp ? "new-password" : "current-password"}
              placeholder={
                signingUp ? `At least ${MIN_PASSWORD_LENGTH} characters` : "••••••••"
              }
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
          {signingUp ? (
            <p className="text-xs text-muted-foreground">
              There is no password reset in this deployment yet — that would
              need email. Save it somewhere.
            </p>
          ) : null}
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
 * Supabase's raw messages are written for a developer reading a network
 * tab, not for someone who just mistyped their password. Translate the ones
 * with an obvious next action; pass anything unrecognised through rather
 * than swallowing a message that might be the only clue.
 */
function readableAuthError(message: string): string {
  const m = message.toLowerCase();
  if (m.includes("invalid login credentials")) {
    return "That email and password don't match an account. Check both, or create an account below.";
  }
  if (m.includes("email not confirmed")) {
    return "This account was created while email confirmation was still on. Turn off Confirm email in the Supabase dashboard (Authentication → Sign In / Providers → Email) and try again.";
  }
  // Check the EMAIL quota before the generic rate limit. Supabase's message
  // for it is "email rate limit exceeded", which matches the generic branch
  // too — and the generic advice ("wait a few minutes, try again") is
  // actively wrong here. Waiting does not help within the hour, and
  // retrying cannot succeed at all: when the confirmation email fails to
  // send, Supabase ROLLS THE SIGNUP BACK, so no account is created however
  // many times you try. Verified against this project: four signup attempts
  // logged, zero rows in auth.users.
  if (m.includes("email rate limit") || m.includes("over_email_send_rate_limit")) {
    return "This deployment still has email confirmation switched on, and its email quota (about two an hour, shared by the whole project) is spent — so signup cannot complete and no account is being created. Turn off Confirm email in Supabase under Authentication → Sign In / Providers → Email, then try again. It works immediately after that; there is nothing to wait for.";
  }
  if (m.includes("rate limit") || m.includes("too many requests")) {
    // Supabase's per-IP limit on the auth endpoints, 30 per 5 minutes.
    // Unlike the email quota this one genuinely is about this client, and
    // waiting genuinely does clear it.
    return "Too many attempts from this network. Wait a few minutes and try again.";
  }
  if (m.includes("password should be")) {
    return `Use at least ${MIN_PASSWORD_LENGTH} characters.`;
  }
  if (m.includes("user already registered")) {
    return "That email is already registered. Sign in instead.";
  }
  return message;
}
