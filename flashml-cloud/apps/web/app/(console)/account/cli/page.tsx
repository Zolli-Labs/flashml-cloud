"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowClockwise } from "@phosphor-icons/react";
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
import { PageHeader } from "@/components/shell/PageHeader";
import { PageShell } from "@/components/shell/PageShell";
import { StatePanel } from "@/components/shell/StatePanel";
import { isEmptyList, resolvePanel } from "@/lib/console/panel-state";
import { credentialBadge, credentialLabel } from "@/lib/cli-credential-status";
import { relativeTime } from "@/lib/machine-status";
import {
  NotAuthenticated,
  listCliCredentials,
  revokeCliCredential,
  type CliCredential,
} from "@/lib/cloud-api";

// Same cadence as `account/machines`. Deliberately identical: two account
// pages polling at different rates would be a difference with no reason
// behind it, and someone would eventually have to work out which was right.
const POLL_MS = 15_000;

const TONE_STYLES: Record<"active" | "revoked", string> = {
  active: "border-[var(--node-green)]/40 text-[var(--node-green)]",
  revoked: "border-border text-muted-foreground",
};

export default function CliAccessPage() {
  const router = useRouter();
  const [credentials, setCredentials] = useState<CliCredential[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    listCliCredentials()
      .then((r) => {
        setCredentials(r);
        setState("ready");
        setError(null);
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          // 401 means signed out, not "you have no credentials". Rendering
          // the empty state here would tell a signed-out user their CLI
          // access was gone — the same trap `account/machines` documents.
          router.push("/sign-in?next=/account/cli");
          return;
        }
        setError(
          err instanceof Error ? err.message : "Couldn't load your CLI access."
        );
        setState("error");
      });
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  async function handleRevoke(id: string) {
    await revokeCliCredential(id);
    setCredentials((prev) =>
      prev.map((c) =>
        c.id === id
          ? { ...c, status: "revoked", revoked_at: new Date().toISOString() }
          : c
      )
    );
  }

  // `resolvePanel` orders the read error BEFORE the loading flag and before
  // the rows, which is the same order this page switched on by hand. The one
  // thing it changes is that there is no longer a branch that can reach the
  // empty state from a failed read — see `lib/console/panel-state.ts`.
  const panel = resolvePanel(
    { loading: state === "loading", error, data: credentials },
    isEmptyList
  );

  return (
    // "reading", matching the other two Settings tabs: the widest thing
    // here is a 560px-min table, which fits the 768px column.
    <PageShell width="reading">
      <PageHeader
        title="CLI access"
        description="Credentials a program holds on your behalf. Each one acts as you, with exactly your access — never more — and can be revoked on its own without touching your browser session."
        actions={
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={load}
            aria-label="Refresh"
          >
            <ArrowClockwise
              className={state === "loading" ? "animate-spin" : ""}
            />
          </Button>
        }
      />

      <StatePanel
        state={panel}
        className="mt-6"
        label="your CLI credentials"
        empty={{
          title: "No CLI credentials yet",
          description: (
            <>
              Run <code className="font-mono text-xs">flashml login</code> and
              approve the code it prints. The credential appears here, and you
              can revoke it from this page at any time.
            </>
          ),
        }}
        unreadable={{ retry: load }}
      >
        {(rows) => {
          // Inside the `present` branch on purpose. This count used to render
          // above the switch, so a failed poll left a stale "3 Active" sitting
          // over a "could not read this" panel — a number presented as current
          // by a page that had just said it could not read anything.
          const activeCount = rows.filter((c) => c.status !== "revoked").length;
          return (
            <>
              {activeCount > 0 && (
                <div className="mb-6">
                  <div className="metric-lg">{activeCount}</div>
                  <div className="label-caps mt-1">Active</div>
                </div>
              )}
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] text-left">
                  <thead>
                    <tr className="border-b border-border">
                      {["Credential", "Added", "Last used", ""].map((h, i) => (
                        <th
                          key={h || i}
                          className={`label-caps px-3 py-2 font-medium ${i === 3 ? "text-right" : ""}`}
                        >
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border">
                    {rows.map((c) => (
                      <CredentialRow
                        key={c.id}
                        credential={c}
                        onRevoke={handleRevoke}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          );
        }}
      </StatePanel>
    </PageShell>
  );
}

function CredentialRow({
  credential,
  onRevoke,
}: {
  credential: CliCredential;
  onRevoke: (id: string) => Promise<void>;
}) {
  const [revoking, setRevoking] = useState(false);

  const revoked = credential.status === "revoked";
  const label = credentialLabel(credential);
  const badge = credentialBadge(credential);

  // A real modal, not an inline swap, for the same reason the machines page
  // uses one: revoking is irreversible from this screen.
  async function confirm() {
    setRevoking(true);
    try {
      await onRevoke(credential.id);
      toast.success("Credential revoked", {
        description: `${label} can no longer act as you.`,
      });
    } catch {
      toast.error("Couldn't revoke that credential", {
        description: "The credential is unchanged. Try again.",
      });
    } finally {
      setRevoking(false);
    }
  }

  return (
    <tr className={revoked ? "opacity-45" : undefined}>
      <td className="px-3 py-3">
        <span className="min-w-0">
          <span className="block truncate font-mono text-sm">{label}</span>
          {/* The prefix is not a secret and cannot authenticate — it is here
              so two credentials with the same label are still tellable
              apart. */}
          <span className="meta block truncate">{credential.token_prefix}…</span>
          <Badge variant="outline" className={`mt-1 ${TONE_STYLES[badge.tone]}`}>
            {badge.label}
          </Badge>
        </span>
      </td>
      <td className="meta px-3 py-3 whitespace-nowrap">
        {relativeTime(credential.created_at)}
      </td>
      <td className="meta px-3 py-3 whitespace-nowrap">
        {revoked
          ? `revoked ${relativeTime(credential.revoked_at)}`
          : credential.last_used_at
            ? relativeTime(credential.last_used_at)
            : "never used"}
      </td>
      <td className="px-3 py-3 text-right">
        {revoked ? (
          <span className="meta">revoked</span>
        ) : (
          <AlertDialog>
            <AlertDialogTrigger
              render={
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                >
                  Revoke
                </Button>
              }
            />
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Revoke {label}?</AlertDialogTitle>
                <AlertDialogDescription>
                  Any program signed in with this credential stops working
                  immediately. Running jobs are unaffected. Signing in again
                  needs a new{" "}
                  <code className="font-mono text-xs">flashml login</code>.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Keep it</AlertDialogCancel>
                <AlertDialogAction
                  disabled={revoking}
                  onClick={confirm}
                  className="bg-destructive/15 text-destructive hover:bg-destructive/25"
                >
                  {revoking ? "Revoking…" : "Revoke"}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        )}
      </td>
    </tr>
  );
}
