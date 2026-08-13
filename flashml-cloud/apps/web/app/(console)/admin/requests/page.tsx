"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowClockwise } from "@phosphor-icons/react";
import { toast } from "sonner";
import { PageHeader } from "@/components/shell/PageHeader";
import { PageShell } from "@/components/shell/PageShell";
import { StatePanel } from "@/components/shell/StatePanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { queuePanelState, type QueueLoadState } from "@/lib/console/queue-panel";
import {
  formatRequestedAt,
  fullNameFor,
  inviteLine,
  labelFor,
  restoreRequest,
} from "@/lib/access-request-queue";
import {
  applyCreditQueueLoad,
  nextCreditQueueGeneration,
  parseZcInput,
  restoreCreditRequest,
  usdForMillicredits,
} from "@/lib/credit-requests";
import { formatZc } from "@/lib/market-credits";
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
  approveCreditRequest,
  declineAccessRequest,
  declineCreditRequest,
  listAdminCreditRequests,
  listAccessRequests,
  type AdminCreditRequest,
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

// The queue load states, named once in `lib/console/queue-panel.ts` so the
// page and the mapping cannot drift apart.
type LoadState = QueueLoadState;
type RequestTab = "access" | "credits";

export default function AdminRequestsPage() {
  const router = useRouter();
  const [rows, setRows] = useState<AccessRequestRow[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [creditRows, setCreditRows] = useState<AdminCreditRequest[]>([]);
  const [creditState, setCreditState] = useState<LoadState>("loading");
  const [creditError, setCreditError] = useState<string | null>(null);
  const [tab, setTab] = useState<RequestTab>("access");
  const creditQueueGeneration = useRef(0);
  const blockedCreditRequestIds = useRef(new Set<string>());

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

  const loadCredits = useCallback(() => {
    const generation = nextCreditQueueGeneration(creditQueueGeneration.current);
    creditQueueGeneration.current = generation;
    listAdminCreditRequests("pending")
      .then((requests) => {
        const visibleRequests = applyCreditQueueLoad(
          requests,
          generation,
          creditQueueGeneration.current,
          blockedCreditRequestIds.current
        );
        if (visibleRequests === null) return;
        setCreditRows(visibleRequests);
        setCreditState("ready");
        setCreditError(null);
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push(
            `/sign-in?next=${encodeURIComponent("/admin/requests")}`
          );
          return;
        }
        if (err instanceof ApiError && err.status === 403) {
          setCreditState("forbidden");
          return;
        }
        setCreditError(
          err instanceof Error ? err.message : "Couldn't load the credit queue."
        );
        setCreditState("error");
      });
  }, [router]);

  useEffect(() => {
    load();
    loadCredits();
  }, [load, loadCredits]);

  // Optimistic, with revert on failure — the row disappears the instant
  // the button is pressed rather than waiting a round trip, and comes back
  // (in its original requested_at position, see `restoreRequest`) with a
  // toast if the API refuses. Same shape `pools/[poolId]/page.tsx`'s
  // machine-toggle uses for a cheaper, reversible action.
  async function handleApprove(row: AccessRequestRow) {
    setRows((prev) => prev.filter((r) => r.user_id !== row.user_id));
    try {
      const decision = await approveAccessRequest(row.user_id);
      // Say which of the two actually happened. An unconditional "we
      // emailed them" would just relocate the dishonesty this replaced:
      // mail is skipped when no provider is configured, when the account
      // has no address, and when the provider refuses.
      toast.success(
        decision.emailed
          ? "Approved — they're in, and we've emailed them."
          : "Approved — they're in. No email went out, so let them know yourself."
      );
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
      const decision = await declineAccessRequest(row.user_id);
      toast.success(
        decision.emailed
          ? "Declined — we've let them know."
          : "Declined. No email went out."
      );
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

  async function handleApproveCredit(row: AdminCreditRequest, approvedZc: number) {
    beginCreditDecision(row.id);
    setCreditRows((prev) => prev.filter((request) => request.id !== row.id));
    try {
      await approveCreditRequest(row.id, approvedZc);
      settleCreditDecision(row.id);
      toast.success("Credit request approved.");
    } catch (err) {
      if (err instanceof NotFound) {
        settleCreditDecision(row.id);
        toast.error("Already decided — someone got there first. Refreshing.");
        loadCredits();
        return;
      }
      failCreditDecision(row.id);
      setCreditRows((prev) => restoreCreditRequest(prev, row));
      const detail =
        err instanceof ApiError ? err.detail : "This request is unchanged.";
      toast.error("Couldn't approve this credit request", { description: detail });
    }
  }

  async function handleDeclineCredit(row: AdminCreditRequest) {
    beginCreditDecision(row.id);
    setCreditRows((prev) => prev.filter((request) => request.id !== row.id));
    try {
      await declineCreditRequest(row.id);
      settleCreditDecision(row.id);
      toast.success("Credit request declined.");
    } catch (err) {
      if (err instanceof NotFound) {
        settleCreditDecision(row.id);
        toast.error("Already decided — someone got there first. Refreshing.");
        loadCredits();
        return;
      }
      failCreditDecision(row.id);
      setCreditRows((prev) => restoreCreditRequest(prev, row));
      const detail =
        err instanceof ApiError ? err.detail : "This request is unchanged.";
      toast.error("Couldn't decline this credit request", { description: detail });
    }
  }

  const activeState = tab === "access" ? state : creditState;
  const refresh = tab === "access" ? load : loadCredits;

  function advanceCreditQueueGeneration() {
    creditQueueGeneration.current = nextCreditQueueGeneration(
      creditQueueGeneration.current
    );
  }

  function beginCreditDecision(requestId: string) {
    blockedCreditRequestIds.current.add(requestId);
    advanceCreditQueueGeneration();
  }

  function settleCreditDecision(requestId: string) {
    blockedCreditRequestIds.current.add(requestId);
    advanceCreditQueueGeneration();
    setCreditRows((prev) => prev.filter((request) => request.id !== requestId));
  }

  function failCreditDecision(requestId: string) {
    blockedCreditRequestIds.current.delete(requestId);
    advanceCreditQueueGeneration();
  }

  return (
    <PageShell width="standard">
      <PageHeader
        title="Requests"
        description="Access and testing-credit requests, reviewed by hand."
        actions={
          activeState !== "forbidden" ? (
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={refresh}
              aria-label="Refresh"
            >
              <ArrowClockwise
                size={15}
                className={activeState === "loading" ? "animate-spin" : ""}
              />
            </Button>
          ) : null
        }
      />

      {/* `components/ui/tabs.tsx` replaces a hand-rolled ARIA tablist —
          roving tabindex, ArrowLeft/ArrowRight/Home/End, focus management,
          `aria-controls`. Both were mounted side by side and driven by a
          real browser pressing real keys (`preview/tabs-keyboard/`); every
          key landed on the same tab, the same `aria-selected` and the same
          `tabIndex` in both. Two props carry behaviour the default would
          have dropped:

          `activateOnFocus` — Base UI defaults to MANUAL activation (arrow
          moves focus, Enter/Space selects). The hand-rolled list activated
          on arrow. Without this, arrowing to Credits would leave the Access
          queue on screen.

          `keepMounted` — Base UI unmounts a hidden panel by default. The
          hand-rolled panels used `hidden`, so both stayed mounted, and
          `CreditRequestCard` holds a per-row approval amount in local
          state. Without this, an admin who typed an amount, checked the
          access queue and came back would find their edit gone. Verified in
          the same rig: typed value survives a tab round trip in both.

          The classNames restore the page's own surfaces. The default
          variant paints the strip `bg-muted` (#f0eee8) and the active tab
          `bg-background` (#f1efe9) — one value per channel apart on this
          cream page, so the strip would have been invisible and the active
          tab a hole in it. */}
      <Tabs
        className="mt-6 gap-4"
        value={tab}
        onValueChange={(value) => setTab(value as RequestTab)}
      >
        <TabsList
          activateOnFocus
          aria-label="Request type"
          className="h-auto border border-border bg-surface p-1 text-sm"
        >
          <TabsTrigger
            value="access"
            className="px-3 py-1.5 data-active:bg-surface-2"
          >
            Access
          </TabsTrigger>
          <TabsTrigger
            value="credits"
            className="px-3 py-1.5 data-active:bg-surface-2"
          >
            Credits
          </TabsTrigger>
        </TabsList>

        <TabsContent value="access" keepMounted>
          <AccessRequestQueue
            rows={rows}
            state={state}
            error={error}
            onRetry={load}
            onApprove={handleApprove}
            onDecline={handleDecline}
          />
        </TabsContent>

        <TabsContent value="credits" keepMounted>
          <CreditRequestQueue
            rows={creditRows}
            state={creditState}
            error={creditError}
            onRetry={loadCredits}
            onApprove={handleApproveCredit}
            onDecline={handleDeclineCredit}
          />
        </TabsContent>
      </Tabs>
    </PageShell>
  );
}

/** A 403 here is not a failed read. The caller is signed in; they are simply
 * not an admin, and this page's doctrine is a flat refusal rather than an
 * error dump. Kept out of `StatePanel` deliberately — `queuePanelState`'s
 * type refuses to accept `forbidden` so this cannot be quietly folded into
 * a destructive "Could not read this." banner. */
function Forbidden() {
  return (
    <div className="flex flex-col items-center gap-2 py-14 text-center">
      <p className="text-sm text-muted-foreground">
        You don&apos;t have access to this page.
      </p>
    </div>
  );
}

function AccessRequestQueue({
  rows,
  state,
  error,
  onRetry,
  onApprove,
  onDecline,
}: {
  rows: AccessRequestRow[];
  state: LoadState;
  error: string | null;
  onRetry: () => void;
  onApprove: (row: AccessRequestRow) => void;
  onDecline: (row: AccessRequestRow) => void;
}) {
  if (state === "forbidden") return <Forbidden />;

  return (
    <StatePanel
      state={queuePanelState(state, error, rows)}
      label="the access queue"
      loadingRows={2}
      empty={{ title: "Nothing waiting. New requests show up here." }}
      unreadable={{ retry: onRetry }}
    >
      {(pending) => (
        <div className="space-y-3">
          {pending.map((row) => (
            <RequestCard
              key={row.user_id}
              row={row}
              onApprove={onApprove}
              onDecline={onDecline}
            />
          ))}
        </div>
      )}
    </StatePanel>
  );
}

function CreditRequestQueue({
  rows,
  state,
  error,
  onRetry,
  onApprove,
  onDecline,
}: {
  rows: AdminCreditRequest[];
  state: LoadState;
  error: string | null;
  onRetry: () => void;
  onApprove: (row: AdminCreditRequest, approvedZc: number) => Promise<void>;
  onDecline: (row: AdminCreditRequest) => Promise<void>;
}) {
  if (state === "forbidden") return <Forbidden />;

  return (
    <StatePanel
      state={queuePanelState(state, error, rows)}
      label="the credit queue"
      loadingRows={2}
      empty={{ title: "Nothing waiting. New credit requests show up here." }}
      unreadable={{ retry: onRetry }}
    >
      {(pending) => (
        <div className="space-y-3">
          {pending.map((row) => (
            <CreditRequestCard
              key={row.id}
              row={row}
              onApprove={onApprove}
              onDecline={onDecline}
            />
          ))}
        </div>
      )}
    </StatePanel>
  );
}

function CreditRequestCard({
  row,
  onApprove,
  onDecline,
}: {
  row: AdminCreditRequest;
  onApprove: (row: AdminCreditRequest, approvedZc: number) => Promise<void>;
  onDecline: (row: AdminCreditRequest) => Promise<void>;
}) {
  const [approval, setApproval] = useState(formatZc(row.requested_zc));
  const approvedZc = parseZcInput(approval);
  const identity =
    [row.first_name, row.last_name].filter(Boolean).join(" ") ||
    row.display_name ||
    "No name on file";

  return (
    <section className="panel p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">{identity}</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {row.company_name || "—"}
          </p>
        </div>
        <span className="meta shrink-0 whitespace-nowrap">
          {formatRequestedAt(row.requested_at)}
        </span>
      </div>

      <dl className="mt-4 grid gap-3 sm:grid-cols-3">
        <CreditField label="Spendable" amount={row.spendable_zc} />
        <CreditField label="Escrow" amount={row.escrow_zc} />
        <CreditField label="Requested" amount={row.requested_zc} />
      </dl>

      <p className="mt-4 max-w-prose whitespace-pre-wrap text-sm leading-relaxed text-muted-foreground">
        {row.purpose}
      </p>

      <div className="mt-4 flex flex-wrap items-end gap-2 border-t border-border pt-4">
        <label className="grid gap-1.5">
          <span className="label-caps">Approve ZC</span>
          <input
            value={approval}
            onChange={(event) => setApproval(event.target.value)}
            inputMode="decimal"
            className="h-9 w-32 rounded-md border border-border bg-background px-2.5 font-mono text-sm outline-none focus:border-primary"
          />
        </label>
        <Button
          disabled={approvedZc === null}
          onClick={() => approvedZc !== null && onApprove(row, approvedZc)}
        >
          Approve
        </Button>
        {/* `variant="destructive"` is a tinted fill where this was a thin
            destructive-bordered outline. Same token, different weight — the
            one visible change in this file, taken because the primitive is
            where the console's destructive treatment is decided. */}
        <Button variant="destructive" onClick={() => onDecline(row)}>
          Decline
        </Button>
      </div>
    </section>
  );
}

function CreditField({ label, amount }: { label: string; amount: number }) {
  return (
    <div className="grid gap-0.5">
      <dt className="label-caps">{label}</dt>
      <dd className="grid text-sm">
        <span className="font-mono">{formatZc(amount)} ZC</span>
        <span className="text-muted-foreground">{usdForMillicredits(amount)}</span>
      </dd>
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
        <Field label="LinkedIn">
          {row.linkedin_url ? (
            <a
              href={row.linkedin_url}
              target="_blank"
              rel="noreferrer"
              className="underline underline-offset-2 hover:no-underline"
            >
              {row.linkedin_url.replace(/^https?:\/\//, "")}
            </a>
          ) : (
            "—"
          )}
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
        <Button onClick={() => onApprove(row)}>Approve</Button>
        <Button variant="destructive" onClick={() => onDecline(row)}>
          Decline
        </Button>
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
