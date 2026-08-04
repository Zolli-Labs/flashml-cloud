"use client";

import { useState } from "react";
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
          : "Couldn't create that workspace. Try again."
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto max-w-xl px-4 py-8 sm:px-6">
      <h1 className="title">Workspaces</h1>
      <p className="mt-1.5 text-sm text-muted-foreground">
        A workspace is where you and the people you invite share machines
        and jobs.
      </p>

      <Card className="mt-6">
        <CardHeader>
          <CardTitle className="text-sm">Create a workspace</CardTitle>
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
              {submitting ? "Creating…" : "Create workspace"}
            </button>
          </form>
        </CardContent>
      </Card>

      <p className="mt-4 text-sm text-muted-foreground">
        Been sent an invite link? Open it and you&apos;ll join that
        workspace.
      </p>
    </div>
  );
}
