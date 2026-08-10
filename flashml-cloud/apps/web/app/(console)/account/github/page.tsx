"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowClockwise, GithubLogo, Warning } from "@phosphor-icons/react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  NotAuthenticated,
  disconnectGitHubInstallation,
  listGitHubInstallations,
  startGitHubInstall,
  type GitHubInstallation,
} from "@/lib/cloud-api";

export default function GitHubAccessPage() {
  const router = useRouter();
  const [installations, setInstallations] = useState<GitHubInstallation[]>([]);
  const [configured, setConfigured] = useState(false);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);

  const load = useCallback(() => {
    listGitHubInstallations()
      .then((result) => {
        setInstallations(result.installations);
        setConfigured(result.configured);
        setState("ready");
        setError(null);
      })
      .catch((cause) => {
        if (cause instanceof NotAuthenticated) {
          router.replace("/sign-in");
          return;
        }
        setError(cause instanceof Error ? cause.message : String(cause));
        setState("error");
      });
  }, [router]);

  useEffect(load, [load]);

  // No polling here, unlike `account/machines` and `account/cli`. Nothing
  // about an installation changes on its own — it changes when this person
  // clicks Connect or Disconnect, and both reload explicitly. A poll would
  // be a request every fifteen seconds to watch a value that cannot move.

  async function connect() {
    setConnecting(true);
    try {
      // The API mints the state and records it. Sending the browser to a
      // URL built here would carry no state and be refused at the callback.
      window.location.href = await startGitHubInstall();
    } catch (cause) {
      setConnecting(false);
      toast.error(
        cause instanceof Error ? cause.message : "could not start the install"
      );
    }
  }

  async function disconnect(installation: GitHubInstallation) {
    try {
      await disconnectGitHubInstallation(installation.installation_id);
      toast.success(`Disconnected ${installation.account_login}`);
      load();
    } catch (cause) {
      toast.error(
        cause instanceof Error ? cause.message : "could not disconnect"
      );
    }
  }

  if (state === "loading") {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  if (state === "error") {
    return (
      <div className="space-y-3">
        <p className="flex items-center gap-2 text-sm text-destructive">
          <Warning size={16} weight="bold" />
          {error}
        </p>
        <Button variant="outline" size="sm" onClick={load}>
          <ArrowClockwise size={14} /> Try again
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold">GitHub</h1>
        <p className="text-sm text-muted-foreground">
          Connect a GitHub account or organisation so FlashML can read its
          private repositories when you submit a job. Read-only, and only the
          repositories you pick.
        </p>
      </header>

      {!configured ? (
        // Deliberately not a disabled Connect button: this deployment has no
        // GitHub App at all, so there is nothing to enable and nothing the
        // person can do about it. Saying so is more useful than a control
        // that will never work.
        <p className="rounded-lg border border-border bg-surface p-4 text-sm text-muted-foreground">
          This deployment has no GitHub App configured, so private
          repositories are unavailable. Public repositories work as normal.
        </p>
      ) : (
        <>
          {installations.length === 0 ? (
            <div className="space-y-3 rounded-lg border border-border bg-surface p-4">
              <p className="text-sm text-muted-foreground">
                Nothing connected yet. Public repositories already work — this
                is only needed for private ones.
              </p>
              <Button onClick={connect} disabled={connecting}>
                <GithubLogo size={16} weight="fill" />
                {connecting ? "Opening GitHub…" : "Connect GitHub"}
              </Button>
            </div>
          ) : (
            <div className="space-y-3">
              <ul className="space-y-2">
                {installations.map((installation) => (
                  <li
                    key={installation.installation_id}
                    className="flex items-center justify-between gap-4 rounded-lg border border-border bg-surface px-4 py-3"
                  >
                    <div className="flex items-center gap-3">
                      <GithubLogo size={20} weight="fill" />
                      <div>
                        <p className="text-sm font-medium">
                          {installation.account_login}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {installation.repository_selection === "all"
                            ? "All repositories"
                            : "Selected repositories"}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge
                        variant="outline"
                        className="border-[var(--node-green)]/40 text-[var(--node-green)]"
                      >
                        Connected
                      </Badge>
                      <AlertDialog>
                        <AlertDialogTrigger
                          render={
                            <Button variant="outline" size="sm">
                              Disconnect
                            </Button>
                          }
                        />
                        <AlertDialogContent>
                          <AlertDialogHeader>
                            <AlertDialogTitle>
                              Disconnect {installation.account_login}?
                            </AlertDialogTitle>
                            <AlertDialogDescription>
                              Jobs you submit from this account&rsquo;s private
                              repositories will stop working. This does not
                              uninstall the app on GitHub — a colleague who
                              connected the same organisation keeps theirs. To
                              remove FlashML&rsquo;s access entirely, uninstall
                              it from GitHub&rsquo;s settings.
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel>Cancel</AlertDialogCancel>
                            <AlertDialogAction
                              onClick={() => disconnect(installation)}
                            >
                              Disconnect
                            </AlertDialogAction>
                          </AlertDialogFooter>
                        </AlertDialogContent>
                      </AlertDialog>
                    </div>
                  </li>
                ))}
              </ul>
              <Button variant="outline" onClick={connect} disabled={connecting}>
                <GithubLogo size={16} weight="fill" />
                Connect another account
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
