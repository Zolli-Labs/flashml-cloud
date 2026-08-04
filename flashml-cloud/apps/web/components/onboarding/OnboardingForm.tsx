"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Warning } from "@phosphor-icons/react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  ApiError,
  NotAuthenticated,
  submitAccessRequest,
  type OnboardingSubmission,
} from "@/lib/cloud-api";
import {
  COMPUTE_OPTIONS,
  EMPTY_DRAFT,
  HEARD_FROM_OPTIONS,
  ROLE_OPTIONS,
  TEAM_SIZE_OPTIONS,
  isComplete,
  type OnboardingDraft,
} from "@/lib/onboarding-options";

const USE_CASE_MAX = 2000;

/**
 * The onboarding form: what an admin reads to decide whether to admit an
 * account. Not mounted anywhere yet — Task 11 wires this into the console
 * shell for an account whose `access` is `needs_onboarding`. `onSubmitted`
 * is the shell's hook to move on once the request is in.
 *
 * Client-side `isComplete` only disables the submit button; the API
 * validates independently and its `detail` on a 400 is shown verbatim,
 * never paraphrased, since it is the only thing that names the field to
 * fix.
 */
export function OnboardingForm({ onSubmitted }: { onSubmitted: () => void }) {
  const router = useRouter();
  const [draft, setDraft] = useState<OnboardingDraft>(EMPTY_DRAFT);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function set<K extends keyof OnboardingDraft>(key: K, value: OnboardingDraft[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  function toggleCompute(value: string) {
    setDraft((d) => ({
      ...d,
      compute_sources: d.compute_sources.includes(value)
        ? d.compute_sources.filter((v) => v !== value)
        : [...d.compute_sources, value],
    }));
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!isComplete(draft) || submitting) return;

    setSubmitting(true);
    setError(null);

    const body: OnboardingSubmission = {
      first_name: draft.first_name.trim(),
      last_name: draft.last_name.trim(),
      company_name: draft.company_name.trim(),
      role: draft.role,
      team_size: draft.team_size,
      use_case: draft.use_case.trim(),
      compute_sources: draft.compute_sources,
    };
    // Sent only when set — `heard_from` is genuinely optional, and the
    // field is typed `heard_from?: string` precisely so an empty answer is
    // omitted rather than sent as `""`.
    if (draft.heard_from) body.heard_from = draft.heard_from;

    try {
      await submitAccessRequest(body);
      onSubmitted();
    } catch (err) {
      if (err instanceof NotAuthenticated) {
        router.push("/sign-in?next=/overview");
        return;
      }
      setError(
        err instanceof ApiError
          ? err.detail
          : "Couldn't submit your request. Try again."
      );
    } finally {
      setSubmitting(false);
    }
  }

  const useCaseCount = draft.use_case.length;
  const useCaseTooLong = useCaseCount > USE_CASE_MAX;

  return (
    <section className="glass w-full max-w-xl rounded-xl p-7 sm:p-8 rise">
      <h1 className="text-xl font-semibold tracking-tight">
        Tell us about you
      </h1>
      <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
        FlashML is a small alpha. A human reads every request — this is what
        they read.
      </p>

      <form onSubmit={handleSubmit} className="mt-6 flex flex-col gap-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-2">
            <Label htmlFor="first-name" className="text-xs font-medium">
              First name
            </Label>
            <Input
              id="first-name"
              name="first-name"
              autoComplete="given-name"
              required
              value={draft.first_name}
              onChange={(e) => set("first_name", e.target.value)}
              disabled={submitting}
              className="h-11"
            />
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="last-name" className="text-xs font-medium">
              Last name
            </Label>
            <Input
              id="last-name"
              name="last-name"
              autoComplete="family-name"
              required
              value={draft.last_name}
              onChange={(e) => set("last_name", e.target.value)}
              disabled={submitting}
              className="h-11"
            />
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="company-name" className="text-xs font-medium">
            Company, lab, or university
          </Label>
          <Input
            id="company-name"
            name="company-name"
            autoComplete="organization"
            required
            value={draft.company_name}
            onChange={(e) => set("company_name", e.target.value)}
            disabled={submitting}
            className="h-11"
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-2">
            <Label htmlFor="role" className="text-xs font-medium">
              Your role
            </Label>
            <Select
              value={draft.role || undefined}
              onValueChange={(value) => set("role", value ?? "")}
              disabled={submitting}
            >
              <SelectTrigger id="role" className="w-full">
                <SelectValue placeholder="Choose one" />
              </SelectTrigger>
              <SelectContent>
                {ROLE_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="team-size" className="text-xs font-medium">
              Team size
            </Label>
            <Select
              value={draft.team_size || undefined}
              onValueChange={(value) => set("team_size", value ?? "")}
              disabled={submitting}
            >
              <SelectTrigger id="team-size" className="w-full">
                <SelectValue placeholder="Choose one" />
              </SelectTrigger>
              <SelectContent>
                {TEAM_SIZE_OPTIONS.map((opt) => (
                  <SelectItem key={opt.value} value={opt.value}>
                    {opt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="use-case" className="text-xs font-medium">
            What do you want to run on FlashML?
          </Label>
          <textarea
            id="use-case"
            name="use-case"
            required
            maxLength={USE_CASE_MAX}
            rows={4}
            value={draft.use_case}
            onChange={(e) => set("use_case", e.target.value)}
            disabled={submitting}
            aria-describedby="use-case-count"
            className="w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-2 text-base transition-colors outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm dark:bg-input/30"
          />
          <p
            id="use-case-count"
            className={`text-xs ${useCaseTooLong ? "text-destructive" : "text-muted-foreground"}`}
          >
            {useCaseCount}/{USE_CASE_MAX} characters.
          </p>
        </div>

        <div className="flex flex-col gap-2">
          <Label className="text-xs font-medium">Where&apos;s your compute?</Label>
          <p className="text-xs text-muted-foreground">
            Check everything you have access to.
          </p>
          <div className="mt-1 flex flex-col gap-2">
            {COMPUTE_OPTIONS.map((opt) => {
              const id = `compute-${opt.value}`;
              return (
                <div key={opt.value} className="flex items-center gap-2.5">
                  <input
                    type="checkbox"
                    id={id}
                    checked={draft.compute_sources.includes(opt.value)}
                    onChange={() => toggleCompute(opt.value)}
                    disabled={submitting}
                    className="h-4 w-4 shrink-0 rounded border-border accent-primary disabled:opacity-50"
                  />
                  <Label
                    htmlFor={id}
                    className="cursor-pointer text-sm font-normal"
                  >
                    {opt.label}
                  </Label>
                </div>
              );
            })}
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="heard-from" className="text-xs font-medium">
            How did you hear about FlashML?
          </Label>
          <p className="text-xs text-muted-foreground">Optional.</p>
          <Select
            value={draft.heard_from || undefined}
            onValueChange={(value) => set("heard_from", value ?? "")}
            disabled={submitting}
          >
            <SelectTrigger id="heard-from" className="w-full">
              <SelectValue placeholder="Choose one" />
            </SelectTrigger>
            <SelectContent>
              {HEARD_FROM_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <Button
          type="submit"
          size="lg"
          className="interactive h-11 w-full"
          disabled={!isComplete(draft) || submitting}
        >
          {submitting ? "Submitting…" : "Submit request"}
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
    </section>
  );
}
