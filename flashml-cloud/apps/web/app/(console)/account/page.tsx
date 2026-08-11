"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Check, Copy, GithubLogo, SignOut, Warning } from "@phosphor-icons/react";
import { toast } from "sonner";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Avatar } from "@/components/shell/Avatar";
import { ClearArtifactsButton } from "@/components/jobs/ClearArtifactsButton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { createBrowserSupabaseClient } from "@/lib/supabase";
import { initialsFor, useSessionUser } from "@/lib/session-user";
import {
  ApiError,
  NotAuthenticated,
  getMe,
  getMyContributions,
  getMyStorage,
  listJobs,
  updateMe,
  updateProfile,
  type AccountStorage,
  type JobRecord,
  type MyContributions,
  type Profile,
} from "@/lib/cloud-api";
import { summariseStorage } from "@/lib/account-storage";
import { summariseContributions } from "@/lib/contributions";
import { clearableJobs } from "@/lib/job-artifact-cleanup";
import { ROLE_OPTIONS, TEAM_SIZE_OPTIONS, labelFor } from "@/lib/onboarding-options";
import {
  TEXT_FIELD_CAPS,
  changedDetails,
  detailsTextError,
  draftFromProfile,
  isDetailsEmpty,
  type DetailsDraft,
} from "@/lib/profile-details";

// Account page. Two sources, kept visibly separate because the user can
// change one and not the other:
//
//   identity provider  email, avatar        read-only, not ours
//   FlashML profile    display name, roles  display name is editable
//
// Showing a greyed-out "email" input the user cannot change would imply we
// own it. It is rendered as a value with the provider named instead.

export default function AccountPage() {
  const router = useRouter();
  const session = useSessionUser();

  const [profile, setProfile] = useState<Profile | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [details, setDetails] = useState<DetailsDraft>(() => draftFromProfile(null));
  const [detailsSaving, setDetailsSaving] = useState(false);
  const [detailsSaved, setDetailsSaved] = useState(false);
  const [detailsError, setDetailsError] = useState<string | null>(null);

  // Kept separate from `profile`'s own load/error state: a storage-service
  // hiccup should not take down the rest of the account page, and vice
  // versa — the two calls have nothing to do with each other.
  const [storage, setStorage] = useState<AccountStorage | null>(null);
  const [storageError, setStorageError] = useState<string | null>(null);

  // Independent of `storage` and everything else on this page for the same
  // reason those are independent of each other: a contributions-service
  // hiccup should not blank out the storage panel two inches above it, and
  // vice versa.
  const [contributions, setContributions] = useState<MyContributions | null>(
    null
  );
  const [contributionsError, setContributionsError] = useState<string | null>(
    null
  );

  // Backs the "free up space" shortcut below the usage bar — this is why
  // someone lands on this page after a storage refusal, so the shortcut has
  // to be reachable from here, not just from a job page they'd have to find
  // first. Best-effort like `storage` above and for the same reason: this
  // list is a convenience on top of the real content of this page, not
  // something worth a banner of its own if it fails to load.
  const [jobs, setJobs] = useState<JobRecord[]>([]);

  const load = useCallback(() => {
    getMe()
      .then((p) => {
        setProfile(p);
        setName(p.display_name ?? "");
        setDetails(draftFromProfile(p));
        setLoadError(null);
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push("/sign-in?next=/account");
          return;
        }
        setLoadError(
          err instanceof Error ? err.message : "Couldn't load your profile."
        );
      });
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  const loadStorage = useCallback(() => {
    return getMyStorage()
      .then((s) => {
        setStorage(s);
        setStorageError(null);
      })
      .catch((err) => {
        // A 401 here is handled by `load()`'s own redirect above, which
        // fires from the same page on the same signed-out session — no
        // need for a second redirect from this call too.
        if (err instanceof NotAuthenticated) return;
        setStorageError(
          err instanceof Error ? err.message : "Couldn't load your storage usage."
        );
      });
  }, []);

  useEffect(() => {
    loadStorage();
  }, [loadStorage]);

  useEffect(() => {
    let cancelled = false;
    getMyContributions()
      .then((c) => {
        if (cancelled) return;
        setContributions(c);
        setContributionsError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        // Same doctrine as `loadStorage` above: a 401 is `load()`'s redirect
        // to handle, not a second one from here.
        if (err instanceof NotAuthenticated) return;
        setContributionsError(
          err instanceof Error
            ? err.message
            : "Couldn't load what you've contributed."
        );
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Independent of `storage`'s own load — a job list failing to arrive
  // should not blank out the usage figures already on screen, and vice
  // versa.
  useEffect(() => {
    let cancelled = false;
    listJobs()
      .then((j) => {
        if (cancelled) return;
        setJobs(j);
      })
      .catch(() => {
        // Same doctrine as `storage`'s own fetch above: best-effort, no
        // banner. Worst case the "free up space" shortcut just does not
        // appear, and the rest of the page is unaffected.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // After a successful clear, both lists on screen are stale: the usage
  // bar still shows the bytes that were just freed, and the "free up
  // space" list still offers a job whose artifacts are gone. Re-fetching
  // both from the API is simpler and more honest than reconstructing the
  // new numbers on the client — `freed_bytes` is a delta and `used_bytes`
  // is a total, and subtracting one from a value we already know can drift
  // from the account's real usage the instant something else in this
  // account writes an artifact in parallel.
  const handleArtifactsCleared = useCallback(() => {
    loadStorage();
    listJobs().then(setJobs).catch(() => {});
  }, [loadStorage]);

  const current = profile?.display_name ?? "";
  const trimmed = name.trim();
  const dirty = trimmed !== current;
  const tooLong = trimmed.length > 80;
  const canSave = dirty && trimmed.length > 0 && !tooLong && !saving;

  async function save() {
    if (!canSave) return;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const updated = await updateMe(trimmed);
      setProfile(updated);
      setName(updated.display_name ?? "");
      setSaved(true);
      toast.success("Display name saved");
    } catch (err) {
      if (err instanceof NotAuthenticated) {
        router.push("/sign-in?next=/account");
        return;
      }
      const detail =
        err instanceof ApiError ? err.detail : "Couldn't save your name.";
      setSaveError(detail);
      // Both: the toast is noticed, the inline message persists next to the
      // field the user has to fix.
      toast.error("Couldn't save your name", { description: detail });
    } finally {
      setSaving(false);
    }
  }

  const currentDetails = draftFromProfile(profile);
  const pendingDetails = changedDetails(details, currentDetails);
  const detailsDirty = Object.keys(pendingDetails).length > 0;
  const detailsEmpty = isDetailsEmpty(profile);

  // Validated only when a field is actually part of `pendingDetails` — i.e.
  // only when it is about to be sent. An untouched field that has simply
  // never been filled in (the grandfathered-tester case `detailsEmpty`
  // describes) must never block Save on its own: it isn't in the payload,
  // so it cannot 400. What must be blocked is CLEARING a field that WAS
  // set, or typing past its cap — both only matter, and only trigger the
  // API's rejection, once that field is dirty.
  const firstNameError =
    "first_name" in pendingDetails
      ? detailsTextError(details.first_name, "first_name")
      : null;
  const lastNameError =
    "last_name" in pendingDetails
      ? detailsTextError(details.last_name, "last_name")
      : null;
  const companyNameError =
    "company_name" in pendingDetails
      ? detailsTextError(details.company_name, "company_name")
      : null;
  const detailsValid = !firstNameError && !lastNameError && !companyNameError;
  const detailsCanSave = detailsDirty && detailsValid && !detailsSaving;

  function updateDetail<K extends keyof DetailsDraft>(
    key: K,
    value: DetailsDraft[K]
  ) {
    setDetails((d) => ({ ...d, [key]: value }));
    setDetailsSaved(false);
    setDetailsError(null);
  }

  async function saveDetails() {
    if (!detailsCanSave) return;
    setDetailsSaving(true);
    setDetailsError(null);
    setDetailsSaved(false);
    try {
      const updated = await updateProfile(pendingDetails);
      setProfile(updated);
      setDetails(draftFromProfile(updated));
      setDetailsSaved(true);
      toast.success("Details saved");
    } catch (err) {
      if (err instanceof NotAuthenticated) {
        router.push("/sign-in?next=/account");
        return;
      }
      const detail =
        err instanceof ApiError ? err.detail : "Couldn't save your details.";
      setDetailsError(detail);
      toast.error("Couldn't save your details", { description: detail });
    } finally {
      setDetailsSaving(false);
    }
  }

  async function signOut() {
    const supabase = createBrowserSupabaseClient();
    await supabase.auth.signOut();
    router.push("/sign-in");
    router.refresh();
  }

  const initials = initialsFor(
    profile?.display_name,
    session?.providerName,
    session?.email
  );

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
      <h1 className="text-2xl font-semibold tracking-tight">Account</h1>

      {loadError && (
        <div className="mt-6 flex items-start gap-2 rounded-lg border border-destructive/30 bg-surface p-4 text-sm text-destructive">
          <Warning className="mt-0.5 h-4 w-4 shrink-0" weight="fill" />
          <span>{loadError}</span>
        </div>
      )}

      <section className="panel mt-6 flex items-center gap-4 p-5">
        <Avatar src={session?.avatarUrl ?? null} initials={initials} size={64} />
        <div className="min-w-0">
          <div className="truncate text-lg font-semibold">
            {profile?.display_name ||
              session?.providerName ||
              session?.email ||
              "…"}
          </div>
          <div className="truncate font-mono text-sm text-muted-foreground">
            {session === undefined ? "…" : (session?.email ?? "no email on file")}
          </div>
        </div>
      </section>

      <section className="panel mt-4 p-5">
        <label htmlFor="display-name" className="text-sm font-medium">
          Display name
        </label>
        <p className="mt-1 text-xs text-muted-foreground">
          How you appear in Zolli. Everything else on this page comes from
          the account you signed in with.
        </p>
        <div className="mt-3 flex flex-wrap items-start gap-2">
          <div className="min-w-0 flex-1">
            <input
              id="display-name"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setSaved(false);
                setSaveError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") save();
              }}
              placeholder={session?.providerName ?? "Your name"}
              aria-invalid={tooLong || undefined}
              aria-describedby="display-name-help"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground/60"
            />
            <p id="display-name-help" className="mt-1.5 text-xs">
              {tooLong ? (
                <span className="text-destructive">
                  {trimmed.length}/80 characters. Too long.
                </span>
              ) : saveError ? (
                <span className="text-destructive">{saveError}</span>
              ) : saved ? (
                <span className="inline-flex items-center gap-1 text-[var(--node-green)]">
                  <Check size={12} weight="bold" /> Saved
                </span>
              ) : (
                <span className="text-muted-foreground">
                  {trimmed.length}/80 characters.
                </span>
              )}
            </p>
          </div>
          <button
            type="button"
            onClick={save}
            disabled={!canSave}
            className="interactive rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </section>

      <section className="panel mt-4 p-5">
        <h2 className="text-sm font-semibold">Your details</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          {detailsEmpty
            ? "You signed up before we asked for this. Filling it in helps us build the right thing."
            : "Used to understand who's on Zolli. Only you and the Zolli team see this."}
        </p>

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="first-name" className="text-xs font-medium">
              First name
            </label>
            <input
              id="first-name"
              value={details.first_name}
              onChange={(e) => updateDetail("first_name", e.target.value)}
              aria-invalid={!!firstNameError || undefined}
              aria-describedby="first-name-help"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground/60"
            />
            <DetailsFieldHelp
              id="first-name-help"
              dirty={"first_name" in pendingDetails}
              error={firstNameError}
              length={details.first_name.trim().length}
              cap={TEXT_FIELD_CAPS.first_name}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label htmlFor="last-name" className="text-xs font-medium">
              Last name
            </label>
            <input
              id="last-name"
              value={details.last_name}
              onChange={(e) => updateDetail("last_name", e.target.value)}
              aria-invalid={!!lastNameError || undefined}
              aria-describedby="last-name-help"
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground/60"
            />
            <DetailsFieldHelp
              id="last-name-help"
              dirty={"last_name" in pendingDetails}
              error={lastNameError}
              length={details.last_name.trim().length}
              cap={TEXT_FIELD_CAPS.last_name}
            />
          </div>
        </div>

        <div className="mt-4 flex flex-col gap-1.5">
          <label htmlFor="company-name" className="text-xs font-medium">
            Company, lab, or university
          </label>
          <input
            id="company-name"
            value={details.company_name}
            onChange={(e) => updateDetail("company_name", e.target.value)}
            aria-invalid={!!companyNameError || undefined}
            aria-describedby="company-name-help"
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground/60"
          />
          <DetailsFieldHelp
            id="company-name-help"
            dirty={"company_name" in pendingDetails}
            error={companyNameError}
            length={details.company_name.trim().length}
            cap={TEXT_FIELD_CAPS.company_name}
          />
        </div>

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="role" className="text-xs font-medium">
              Role
            </label>
            {/* `details.role`, never `details.role || undefined`: Base UI's
                `useControlled` decides controlled-vs-uncontrolled ONCE, on
                first render, by checking `value !== undefined` — and never
                re-checks it. This page prefills from an existing profile
                (a non-empty value can land on first render), so passing
                `undefined` here would risk locking the Select uncontrolled
                for its whole lifetime and silently ignoring that prefill.
                Same pattern as `OnboardingForm.tsx`. */}
            <Select
              value={details.role}
              onValueChange={(value) => updateDetail("role", value ?? "")}
              disabled={detailsSaving}
            >
              <SelectTrigger id="role" className="w-full">
                <SelectValue placeholder="Choose one">
                  {(v) => labelFor(ROLE_OPTIONS, v) ?? "Choose one"}
                </SelectValue>
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

          <div className="flex flex-col gap-1.5">
            <label htmlFor="team-size" className="text-xs font-medium">
              Team size
            </label>
            {/* Same reasoning as the role Select above. */}
            <Select
              value={details.team_size}
              onValueChange={(value) => updateDetail("team_size", value ?? "")}
              disabled={detailsSaving}
            >
              <SelectTrigger id="team-size" className="w-full">
                <SelectValue placeholder="Choose one">
                  {(v) => labelFor(TEAM_SIZE_OPTIONS, v) ?? "Choose one"}
                </SelectValue>
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

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={saveDetails}
            disabled={!detailsCanSave}
            className="interactive rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {detailsSaving ? "Saving…" : "Save"}
          </button>
          {detailsError ? (
            <span className="text-xs text-destructive">{detailsError}</span>
          ) : detailsSaved ? (
            <span className="inline-flex items-center gap-1 text-xs text-[var(--node-green)]">
              <Check size={12} weight="bold" /> Saved
            </span>
          ) : null}
        </div>
      </section>

      <section className="mt-4 panel p-5">
        <h2 className="text-sm font-semibold">Account details</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Set by how you signed in and by what you have done. None of it is
          editable here.
        </p>
        <dl className="mt-4 divide-y divide-border">
          <Row
            label="GitHub"
            help="Linked when a job is submitted from a repository you own."
          >
            {profile?.github_login ? (
              <span className="inline-flex items-center gap-1.5 font-mono text-sm">
                <GithubLogo size={14} weight="fill" />
                {profile.github_login}
              </span>
            ) : (
              <span className="text-sm text-muted-foreground">not linked</span>
            )}
          </Row>

          <Row
            label="Roles"
            help="Granted by the platform. Not a preference you can set."
          >
            <span className="flex flex-wrap justify-end gap-1.5">
              {profile?.is_developer && <Tag>developer</Tag>}
              {profile?.is_host && <Tag>host</Tag>}
              {profile && !profile.is_developer && !profile.is_host && (
                <span className="text-sm text-muted-foreground">
                  none assigned
                </span>
              )}
            </span>
          </Row>

          <Row label="Member since">
            <span className="font-mono text-sm">
              {profile?.created_at
                ? new Date(profile.created_at).toLocaleDateString(undefined, {
                    year: "numeric",
                    month: "long",
                    day: "numeric",
                  })
                : "—"}
            </span>
          </Row>

          <Row
            label="User ID"
            help="Quote this when reporting a problem with your account."
          >
            {/* Truncated with the full value in a tooltip AND a copy button.
                A uuid that is visually cut off and cannot be copied is worse
                than not showing it: it looks like information and is not. */}
            {profile?.id ? (
              <span className="flex items-center justify-end gap-1.5">
                <Tooltip>
                  <TooltipTrigger
                    render={
                      <span className="max-w-[16ch] truncate font-mono text-xs text-muted-foreground sm:max-w-none">
                        {profile.id}
                      </span>
                    }
                  />
                  <TooltipContent>{profile.id}</TooltipContent>
                </Tooltip>
                <button
                  type="button"
                  aria-label="Copy user ID"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(profile.id);
                      toast.success("User ID copied");
                    } catch {
                      toast.error("Your browser blocked clipboard access");
                    }
                  }}
                  className="rounded p-1 text-muted-foreground hover:bg-surface-2 hover:text-foreground"
                >
                  <Copy size={12} />
                </button>
              </span>
            ) : (
              <span className="text-sm text-muted-foreground">—</span>
            )}
          </Row>
        </dl>
      </section>

      <section className="panel mt-4 p-5">
        <h2 className="text-sm font-semibold">Storage</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Artifacts and checkpoints kept for jobs this account submitted.
        </p>
        <div className="mt-4">
          {storageError ? (
            <div className="flex items-start gap-2 text-sm text-destructive">
              <Warning className="mt-0.5 h-4 w-4 shrink-0" weight="fill" />
              <span>{storageError}</span>
            </div>
          ) : storage ? (
            <StorageUsage storage={storage} />
          ) : (
            <p className="meta">…</p>
          )}
        </div>

        <FreeUpSpace jobs={jobs} onCleared={handleArtifactsCleared} />
      </section>

      <section className="panel mt-4 p-5">
        <h2 className="text-sm font-semibold">Contributed</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Work your Machines have accepted, across every job you have ever run
          &mdash; not just the one job page a credit used to be visible from.
          A running tally, not a balance: nothing here is ever spent.
        </p>
        <div className="mt-4">
          {contributionsError ? (
            <div className="flex items-start gap-2 text-sm text-destructive">
              <Warning className="mt-0.5 h-4 w-4 shrink-0" weight="fill" />
              <span>{contributionsError}</span>
            </div>
          ) : contributions ? (
            <ContributionsSummaryView contributions={contributions} />
          ) : (
            <p className="meta">…</p>
          )}
        </div>
      </section>

      <section className="mt-4 rounded-lg border border-destructive/25 bg-destructive/[0.04] p-5">
        <h2 className="text-sm font-semibold text-destructive">Sign out</h2>
        <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted-foreground">
          Signing out ends this browser session only. It does{" "}
          <span className="text-foreground">not</span> revoke any Machine:
          Machines hold their own tokens and keep claiming work until you
          revoke them from{" "}
          <Link href="/machines" className="text-brand-foreground hover:underline">
            Machines
          </Link>
          .
        </p>
        <button
          type="button"
          onClick={signOut}
          className="mt-3 inline-flex items-center gap-2 rounded-md border border-destructive/30 px-3 py-1.5 text-sm text-destructive hover:bg-destructive/10"
        >
          <SignOut size={14} />
          Sign out
        </button>
      </section>
    </div>
  );
}

/** The used/limit bar, or the unlimited state — two genuinely different
 * layouts, not one bar with a hidden edge case. An unlimited account gets
 * no percentage and no fill at all: a bar drawn to any width would claim a
 * ceiling this account does not have. See `lib/account-storage.ts` for why
 * `unlimited` has to be checked before `percent` is ever read. */
function StorageUsage({ storage }: { storage: AccountStorage }) {
  const display = summariseStorage(storage);

  if (display.unlimited) {
    return (
      <div className="flex items-center justify-between gap-3">
        <span className="metric-value text-lg">{display.usedLabel}</span>
        <span className="rounded-full border border-border bg-surface-2 px-2.5 py-1 text-xs font-medium text-muted-foreground">
          No storage limit
        </span>
      </div>
    );
  }

  const pct = display.percent;
  // Green at "ok", the same warning/destructive tones the rest of this app
  // already uses (see the submit page's preflight findings) once there is
  // something to act on — a bar that stays green at 96% used would say
  // "fine" right up to the refusal this page exists to warn about.
  const barColor =
    display.severity === "full"
      ? "bg-destructive"
      : display.severity === "approaching"
        ? "bg-warning"
        : "bg-[var(--node-green)]";
  const pctColor =
    display.severity === "full"
      ? "text-destructive"
      : display.severity === "approaching"
        ? "text-warning-foreground"
        : "text-muted-foreground";
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm">
          <span className="metric-value">{display.usedLabel}</span>{" "}
          <span className="text-muted-foreground">of {display.limitLabel}</span>
        </span>
        <span className={`font-mono text-xs ${pctColor}`}>
          {Math.round(pct)}%
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full rounded-full transition-[width] duration-500 ${barColor}`}
          style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
        />
      </div>
      {display.message && (
        <p
          className={`mt-2 flex items-start gap-1.5 text-xs leading-relaxed ${
            display.severity === "full" ? "text-destructive" : "text-warning-foreground"
          }`}
        >
          <Warning className="mt-0.5 h-3.5 w-3.5 shrink-0" weight="fill" />
          <span>{display.message}</span>
        </p>
      )}
    </div>
  );
}

/** The account-wide contribution totals plus, when there is more than one
 * machine, the per-machine breakdown behind them. `lib/contributions.ts`
 * already decided the exact label for a machine with no reported hostname
 * or no recorded last-seen time; this only lays the rows out. */
function ContributionsSummaryView({
  contributions,
}: {
  contributions: MyContributions;
}) {
  const summary = summariseContributions(contributions);

  return (
    <div>
      <div className="flex flex-wrap gap-6">
        <div>
          <div className="metric-value text-2xl">{summary.acceptedTasks}</div>
          <div className="label-caps mt-1">Tasks accepted</div>
        </div>
        <div>
          <div className="metric-value text-2xl">{summary.jobsContributedTo}</div>
          <div className="label-caps mt-1">Jobs contributed to</div>
        </div>
      </div>

      {summary.machines.length > 0 && (
        <div className="mt-4 space-y-1.5">
          {summary.machines.map((m) => (
            <div
              key={m.machineId}
              className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2 text-xs"
            >
              <span className="min-w-0 truncate font-mono">
                {m.hostnameLabel}
              </span>
              <span className="flex shrink-0 items-center gap-3 text-muted-foreground">
                <span className="font-mono tabular-nums text-foreground">
                  {m.acceptedTasks}
                </span>
                <span>accepted</span>
                <span aria-hidden className="text-muted-foreground/40">
                  ·
                </span>
                <span>{m.lastSeenLabel}</span>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** How many at once — enough to be useful without turning the Storage panel
 * into a second jobs list. Whoever has more than this already knows where
 * `/jobs` is; the point of putting this here at all is reaching someone who
 * doesn't, right where the refusal message told them to come. */
const MAX_SHOWN = 5;

/** The shortcut this account's refusal message promises but the product
 * did not build until now: "delete a finished job's artifacts to free
 * space", with an actual place to do it. `jobs` is this account's own list
 * — `listJobs()` needs no scoping of its own — narrowed to the ones
 * `DELETE /v1alpha1/jobs/{id}/artifacts` will accept without a 409.
 *
 * Renders nothing when there is nothing clearable, the same "absence is
 * not an error" rule every empty state on this page already follows: a
 * brand-new account with no finished jobs should not see an empty shelf
 * where this shortcut would go. */
function FreeUpSpace({
  jobs,
  onCleared,
}: {
  jobs: JobRecord[];
  onCleared: () => void;
}) {
  const all = clearableJobs(jobs);
  if (all.length === 0) return null;
  const shown = all.slice(0, MAX_SHOWN);

  return (
    <div className="mt-5 border-t border-border pt-4">
      <h3 className="text-sm font-medium">Free up space</h3>
      <p className="mt-1 text-xs text-muted-foreground">
        Clearing a job&apos;s artifacts permanently deletes what it wrote —
        results, checkpoints, logs — and cannot be undone.
      </p>
      <ul className="mt-3 space-y-2">
        {shown.map((j) => (
          <li
            key={j.jobId}
            className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2"
          >
            <Link
              href={`/jobs/${j.jobId}`}
              className="min-w-0 truncate font-mono text-xs text-brand-foreground hover:underline"
            >
              {j.label}
            </Link>
            <ClearArtifactsButton jobId={j.jobId} onCleared={onCleared} />
          </li>
        ))}
      </ul>
      {all.length > shown.length && (
        <Link
          href="/jobs"
          className="mt-2 inline-block text-xs text-brand-foreground hover:underline"
        >
          See all jobs
        </Link>
      )}
    </div>
  );
}

function Row({
  label,
  help,
  children,
}: {
  label: string;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-6 py-3.5">
      <div className="min-w-0">
        <dt className="text-sm">{label}</dt>
        {help && (
          <p className="mt-0.5 max-w-[38ch] text-xs leading-relaxed text-muted-foreground">
            {help}
          </p>
        )}
      </div>
      <dd className="min-w-0 shrink-0 text-right">{children}</dd>
    </div>
  );
}

function Tag({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-sm border border-border bg-surface-elevated px-1.5 py-0.5 font-mono text-[10px]">
      {children}
    </span>
  );
}

/** Inline validity feedback for one of "Your details"'s three text fields
 * — same presentation as the display-name field's helper text above
 * (`#display-name-help`), plus a destructive "Can't be blank." this page
 * needs and that field doesn't: display name can never be dirty-and-blank
 * (its own `canSave` silently disables on length 0), but here clearing a
 * previously-set field is an ordinary, expected action that must be
 * refused with a reason, not just a disabled button. Renders nothing at
 * all when `!dirty` — an untouched field that has simply never been filled
 * in (a grandfathered tester's default state) must not be nagged. */
function DetailsFieldHelp({
  id,
  dirty,
  error,
  length,
  cap,
}: {
  id: string;
  dirty: boolean;
  error: string | null;
  length: number;
  cap: number;
}) {
  if (!dirty) return null;
  return (
    <p id={id} className="text-xs">
      {error ? (
        <span className="text-destructive">{error}</span>
      ) : (
        <span className="text-muted-foreground">
          {length}/{cap} characters.
        </span>
      )}
    </p>
  );
}
