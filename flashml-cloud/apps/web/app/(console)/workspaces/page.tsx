"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  ApiError,
  NotAuthenticated,
  createPool,
} from "@/lib/cloud-api";
import { notifyWorkspacesChanged } from "@/lib/workspace-events";
import { workspacePath } from "@/lib/workspace-scope";

/** Where an admitted user with no workspace lands — via `WorkspaceResolver`
 * finding nothing to resolve to, or the switcher's "New workspace" link.
 *
 * Distinct from `/onboarding` (there is no such route): that word already
 * names a *shell state* for accounts awaiting admission (`lib/access-screen.ts`,
 * `components/onboarding/`). This is a *route* for an admitted account that
 * simply has not created or joined a workspace yet — a different problem
 * that happens to want a similar-sounding name, which is exactly why it
 * gets a different one. */
export default function WorkspacesPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    const trimmed = name.trim();
    if (!trimmed) return;
    setSubmitting(true);
    setError(null);
    try {
      const pool = await createPool(trimmed);
      // Same reason `RenameWorkspace` does it: `WorkspaceSwitcher` lives in
      // `ConsoleShell`, which survives the client navigation below, so
      // without this the rail would not list the workspace you just created
      // and just navigated into.
      notifyWorkspacesChanged();
      toast.success("Workspace created", { description: pool.name });
      router.push(workspacePath(pool.id, "overview"));
    } catch (err) {
      if (err instanceof NotAuthenticated) {
        router.push(`/sign-in?next=${encodeURIComponent("/workspaces")}`);
        return;
      }
      setError(
        err instanceof ApiError
          ? err.detail
          : "Couldn't create that Workspace. Try again."
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto max-w-xl px-4 py-8 sm:px-6">
      <p className="label-caps text-brand-foreground">Zolli Cloud</p>
      <h1 className="title mt-2">Build your workspace</h1>
      <p className="mt-1.5 text-sm text-muted-foreground">
        A Workspace is where you and the people you invite share Machines and jobs.
      </p>

      <Card className="mt-6 border-border bg-surface shadow-sm">
        <CardHeader>
          <CardTitle className="text-sm">Create a Workspace</CardTitle>
          <CardDescription>
            Name it after your team or your project. You can rename it
            later.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
            className="flex flex-wrap items-start gap-2"
          >
            <div className="min-w-0 flex-1">
              <Input
                value={name}
                onChange={(e) => {
                  setName(e.target.value);
                  setError(null);
                }}
                placeholder="Workspace name"
                aria-label="Workspace name"
                disabled={submitting}
                autoFocus
              />
              {error && (
                <p className="mt-1.5 text-xs text-destructive">{error}</p>
              )}
            </div>
            <button
              type="submit"
              disabled={submitting || name.trim().length === 0}
              className="interactive rounded-md bg-primary px-3.5 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {submitting ? "Creating…" : "Create Workspace"}
            </button>
          </form>
        </CardContent>
      </Card>

      {/* Carried over from the old `/pools` list page, which this replaced.
          `/pools/join` is safe to link to bare: it reads `?token=` when
          there is one and otherwise renders `JoinByCode`, which redeems a
          pasted link or code via `tokenFromInput` — so arriving with no
          token is a working screen, not an error. */}
      <p className="mt-4 text-sm text-muted-foreground">
        Been sent an invite link? Open it and you&apos;ll join that
        Workspace.
      </p>
      <Link
        href="/pools/join"
        className="mt-1 inline-block text-xs text-muted-foreground hover:text-foreground hover:underline"
      >
        Have a Workspace invite code?
      </Link>
    </div>
  );
}
