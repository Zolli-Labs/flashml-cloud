"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowClockwise, Warning } from "@phosphor-icons/react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import {
  formatRequestedAt,
  fullNameFor,
  inviteLine,
  labelFor,
  restoreRequest,
} from "@/lib/access-request-queue";
import {
  COMPUTE_OPTIONS,
  ROLE_OPTIONS,
  TEAM_SIZE_OPTIONS,
} from "@/lib/onboarding-options";
import {
  ApiError,
  NotAuthenticated,
  NotFound,
  approveAccessRequest,
  declineAccessRequest,
  listAccessRequests,
  type AccessRequestRow,
} from "@/lib/cloud-api";

// The admin access-request queue. Reachable only via the rail's Admin item
// (shown only when `GET /me` says `is_admin`, see ConsoleShell) or by
// guessing the URL — the API is the real gate either way: every route
// under here re-checks `is_admin` server-side and answers 403 with detail
// "admin required" for anyone else. That 403 is a plain `ApiError`, not a
// `NotAuthenticated`: the caller IS signed in, they're just not an admin,
// and this page renders a flat refusal for it rather than an error dump
// or a bounce back to sign-in that would just land them here again.
//
// Operational note: an admin whose OWN account is not yet `admitted`
// cannot reach this page at all — the shell shows them the onboarding or
// pending screen instead of the console for every route, this one
// included. That's expected and fails closed: `is_admin` and `admitted`
// are independent flags, and the shell's gate runs first.
//
// No control here can grant `is_admin`. It is set by one manual SQL
// UPDATE and nothing else — see `Profile.is_admin`'s docstring in
// `lib/cloud-api.ts` — so this page only ever approves or declines a
// pending REQUEST, never elevates the account reviewing it.

type LoadState = "loading" | "ready" | "forbidden" | "error";

export default function AdminRequestsPage() {
  const router = useRouter();
  const [rows, setRows] = useState<AccessRequestRow[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    listAccessRequests("pending")
      .then((r) => {
        setRows(r);
        setState("ready");
        setError(null);
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push(
            `/sign-in?next=${encodeURIComponent("/admin/requests")}`
          );
          return;
        }
        if (err instanceof ApiError && err.status === 403) {
          setState("forbidden");
          return;
        }
        setError(
          err instanceof Error ? err.message : "Couldn't load the queue."
        );
        setState("error");
      });
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  // Optimistic, with revert on failure — the row disappears the instant
  // the button is pressed rather than waiting a round trip, and comes back
  // (in its original requested_at position, see `restoreRequest`) with a
  // toast if the API refuses. Same shape `pools/[poolId]/page.tsx`'s
  // machine-toggle uses for a cheaper, reversible action.
  async function handleApprove(row: AccessRequestRow) {
    setRows((prev) => prev.filter((r) => r.user_id !== row.user_id));
    try {
      await approveAccessRequest(row.user_id);
      // This deployment has no email provider — nothing notifies the
      // person being approved (same constraint `PendingScreen` documents
      // for the waiting side of this same flow). The copy says so rather
      // than implying otherwise.
      toast.success("Approved — they're in. Let them know yourself.");
    } catch (err) {
      if (err instanceof NotFound) {
        // 404 here means "no pending request for this user" — the
        // double-decide race (another admin, or a retry, got there
        // first). The row is genuinely gone server-side, so don't put it
        // back; resync the whole queue instead, since other rows may
        // have moved too.
        toast.error("Already decided — someone got there first. Refreshing.");
        load();
        return;
      }
      setRows((prev) => restoreRequest(prev, row));
      const detail =
        err instanceof ApiError ? err.detail : "This request is unchanged.";
      toast.error("Couldn't approve this request", { description: detail });
    }
  }

  async function handleDecline(row: AccessRequestRow) {
    setRows((prev) => prev.filter((r) => r.user_id !== row.user_id));
    try {
      await declineAccessRequest(row.user_id);
      toast.success("Request declined");
    } catch (err) {
      if (err instanceof NotFound) {
        // Same double-decide race as handleApprove above.
        toast.error("Already decided — someone got there first. Refreshing.");
        load();
        return;
      }
      setRows((prev) => restoreRequest(prev, row));
      const detail =
        err instanceof ApiError ? err.detail : "This request is unchanged.";
      toast.error("Couldn't decline this request", { description: detail });
    }
  }

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="title">Access requests</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            People waiting to get in. Reviewed by hand, one at a time.
          </p>
        </div>
        {state !== "forbidden" && (
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
        )}
      </div>

      <div className="mt-6">
        {state === "forbidden" ? (
          <div className="flex flex-col items-center gap-2 py-14 text-center">
            <p className="text-sm text-muted-foreground">
              You don&apos;t have access to this page.
            </p>
          </div>
        ) : state === "loading" && rows.length === 0 ? (
          <div className="space-y-3">
            <div className="skeleton h-40 rounded-lg" />
            <div className="skeleton h-40 rounded-lg" />
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
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-14 text-center">
            <p className="text-sm text-muted-foreground">
              Nothing waiting. New requests show up here.
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {rows.map((row) => (
              <RequestCard
                key={row.user_id}
                row={row}
                onApprove={handleApprove}
                onDecline={handleDecline}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function RequestCard({
  row,
  onApprove,
  onDecline,
}: {
  row: AccessRequestRow;
  onApprove: (row: AccessRequestRow) => void;
  onDecline: (row: AccessRequestRow) => void;
}) {
  const invited = inviteLine(row);

  return (
    <section className="panel p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <h2 className="text-sm font-semibold">{fullNameFor(row)}</h2>
            <span className="meta">{row.email ?? "no email on file"}</span>
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            {row.company_name || "—"}
          </p>
        </div>
        <span className="meta shrink-0 whitespace-nowrap">
          {formatRequestedAt(row.requested_at)}
        </span>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2.5 sm:grid-cols-3">
        <Field label="Role">{labelFor(ROLE_OPTIONS, row.role)}</Field>
        <Field label="Team size">
          {labelFor(TEAM_SIZE_OPTIONS, row.team_size)}
        </Field>
        <Field label="Domain">
          <span className="inline-flex flex-wrap items-center gap-1.5">
            <span className="font-mono text-xs">
              {row.email_domain ?? "—"}
            </span>
            {row.is_personal_email && (
              <Badge
                variant="outline"
                className="border-warning/40 bg-warning/10 text-warning-foreground"
              >
                personal
              </Badge>
            )}
          </span>
        </Field>
      </dl>

      {row.compute_sources.length > 0 && (
        <div className="mt-3.5 flex flex-wrap gap-1.5">
          {row.compute_sources.map((source) => (
            <span
              key={source}
              className="rounded-full border border-border bg-surface-2 px-2 py-0.5 text-[10px] text-muted-foreground"
            >
              {labelFor(COMPUTE_OPTIONS, source)}
            </span>
          ))}
        </div>
      )}

      {row.use_case && (
        <p className="mt-3.5 max-w-prose whitespace-pre-wrap text-sm leading-relaxed text-muted-foreground">
          {row.use_case}
        </p>
      )}

      {invited && <p className="mt-3.5 text-xs text-muted-foreground">{invited}</p>}

      <div className="mt-4 flex items-center gap-2 border-t border-border pt-4">
        <button
          type="button"
          onClick={() => onApprove(row)}
          className="interactive rounded-md bg-primary px-3.5 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110"
        >
          Approve
        </button>
        <button
          type="button"
          onClick={() => onDecline(row)}
          className="rounded-md border border-destructive/30 px-3.5 py-2 text-sm text-destructive hover:bg-destructive/10"
        >
          Decline
        </button>
      </div>
    </section>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      <dt className="label-caps">{label}</dt>
      <dd className="mt-0.5 text-sm">{children}</dd>
    </div>
  );
}
