"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowClockwise, Terminal, Warning } from "@phosphor-icons/react";
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

  const active = credentials.filter((c) => c.status !== "revoked");

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="title">CLI access</h1>
          <p className="mt-1.5 max-w-prose text-sm text-muted-foreground">
            Credentials a program holds on your behalf. Each one acts as you,
            with exactly your access — never more — and can be revoked on its
            own without touching your browser session.
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          aria-label="Refresh"
          className="rounded-md p-2 text-muted-foreground hover:bg-surface-2 hover:text-foreground"
        >
          <ArrowClockwise
            size={15}
            className={state === "loading" ? "animate-spin" : ""}
          />
        </button>
      </div>

      {active.length > 0 && (
        <div className="mt-7">
          <div className="metric-lg">{active.length}</div>
          <div className="label-caps mt-1">Active</div>
        </div>
      )}

      <div className="mt-6">
        {state === "loading" && credentials.length === 0 ? (
          <div className="space-y-px">
            <div className="skeleton h-14" />
            <div className="skeleton h-14" />
          </div>
        ) : state === "error" ? (
          <div className="flex flex-col items-center gap-3 py-12 text-center">
            <Warning className="h-5 w-5 text-destructive" weight="fill" />
            <p className="text-sm text-muted-foreground">{error}</p>
            <button
              type="button"
              onClick={load}
              className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm hover:bg-surface-2"
            >
              Try again
            </button>
          </div>
        ) : credentials.length === 0 ? (
          <Empty />
        ) : (
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
                {credentials.map((c) => (
                  <CredentialRow
                    key={c.id}
                    credential={c}
                    onRevoke={handleRevoke}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
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
                <button
                  type="button"
                  className="rounded-md px-2.5 py-1 text-xs text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                >
                  Revoke
                </button>
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

function Empty() {
  return (
    <div className="flex flex-col items-center gap-4 py-14 text-center">
      <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-border">
        <Terminal size={19} className="text-muted-foreground" />
      </div>
      <div>
        <h2 className="text-base font-semibold">No CLI credentials yet</h2>
        <p className="mx-auto mt-1.5 max-w-sm text-sm text-muted-foreground">
          Run <code className="font-mono text-xs">flashml login</code> and
          approve the code it prints. The credential appears here, and you can
          revoke it from this page at any time.
        </p>
      </div>
    </div>
  );
}
