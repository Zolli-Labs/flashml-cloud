"use client";

import { Copy, GithubLogo } from "@phosphor-icons/react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { Profile } from "@/lib/cloud-api";

/**
 * The read-only half of the account: what signing in and using the product
 * set, none of it editable here.
 *
 * TAKES `Profile`, NOT `Profile | null`, AND THAT IS THE POINT. Every row
 * here used to be written as `profile?.x ? … : <fallback>`, evaluated on a
 * page that rendered this section whether the profile had loaded, had failed
 * to load, or had loaded fine. Two of those fallbacks are honest placeholders
 * ("—" for a date and an id we do not have). The GitHub row's was not: it
 * said **"not linked"**, which is a factual claim about the account, produced
 * by a read that returned nothing at all. A user whose network blipped was
 * told their GitHub link was gone.
 *
 * There is no fix for that inside a row. The fix is that this component
 * cannot be rendered without a profile, so the page has to decide what a
 * failed read looks like — which is what `StatePanel` is for. Every value
 * below is now reachable only from a `Profile` the API returned.
 */
export function AccountFacts({ profile }: { profile: Profile }) {
  return (
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
          {/* Reachable only on a loaded profile, so "not linked" now means
              the API returned no `github_login` — not that we failed to ask. */}
          {profile.github_login ? (
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
            {profile.is_developer && <Tag>developer</Tag>}
            {profile.is_host && <Tag>host</Tag>}
            {!profile.is_developer && !profile.is_host && (
              <span className="text-sm text-muted-foreground">
                none assigned
              </span>
            )}
          </span>
        </Row>

        <Row label="Member since">
          <span className="font-mono text-sm">
            {profile.created_at
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
          {profile.id ? (
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
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label="Copy user ID"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(profile.id);
                    toast.success("User ID copied");
                  } catch {
                    toast.error("Your browser blocked clipboard access");
                  }
                }}
                className="text-muted-foreground hover:text-foreground"
              >
                <Copy size={12} />
              </Button>
            </span>
          ) : (
            <span className="text-sm text-muted-foreground">—</span>
          )}
        </Row>
      </dl>
    </section>
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
