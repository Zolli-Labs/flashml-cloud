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
import { createBrowserSupabaseClient } from "@/lib/supabase";
import { initialsFor, useSessionUser } from "@/lib/session-user";
import {
  ApiError,
  NotAuthenticated,
  getMe,
  updateMe,
  type Profile,
} from "@/lib/cloud-api";

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

  const load = useCallback(() => {
    getMe()
      .then((p) => {
        setProfile(p);
        setName(p.display_name ?? "");
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
          How you appear in FlashML. Everything else on this page comes from
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
                  className="rounded p-1 text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
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

      <section className="mt-4 rounded-lg border border-destructive/25 bg-destructive/[0.04] p-5">
        <h2 className="text-sm font-semibold text-destructive">Sign out</h2>
        <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted-foreground">
          Signing out ends this browser session only. It does{" "}
          <span className="text-foreground">not</span> revoke any machine:
          machines hold their own tokens and keep claiming work until you
          revoke them from{" "}
          <Link href="/machines" className="text-primary hover:underline">
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
