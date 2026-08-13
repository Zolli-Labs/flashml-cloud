"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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

type LoadState = "loading" | "ready" | "forbidden" | "error";
type RequestTab = "access" | "credits";

const REQUEST_TABS: RequestTab[] = ["access", "credits"];

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
  const tabRefs = useRef<Record<RequestTab, HTMLButtonElement | null>>({
    access: null,
    credits: null,
  });

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

  function selectTab(nextTab: RequestTab, focus = false) {
    setTab(nextTab);
    if (focus) tabRefs.current[nextTab]?.focus();
  }

  function handleTabKeyDown(event: React.KeyboardEvent<HTMLButtonElement>) {
    const currentIndex = REQUEST_TABS.indexOf(tab);
    let nextIndex: number | null = null;
    if (event.key === "ArrowLeft") nextIndex = (currentIndex + REQUEST_TABS.length - 1) % REQUEST_TABS.length;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % REQUEST_TABS.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = REQUEST_TABS.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    selectTab(REQUEST_TABS[nextIndex], true);
  }

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="title">Requests</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            Access and testing-credit requests, reviewed by hand.
          </p>
        </div>
        {activeState !== "forbidden" && (
          <button
            type="button"
            onClick={refresh}
            aria-label="Refresh"
            className="rounded-md p-2 text-muted-foreground hover:bg-surface-2 hover:text-foreground"
          >
            <ArrowClockwise
              size={15}
              className={activeState === "loading" ? "animate-spin" : ""}
            />
          </button>
        )}
      </div>

      <div className="mt-6">
        <div
          role="tablist"
          aria-label="Request type"
          className="mb-4 inline-flex rounded-md border border-border bg-surface p-1 text-sm"
        >
          <button
            type="button"
            ref={(element) => {
              tabRefs.current.access = element;
            }}
            id="admin-requests-access-tab"
            role="tab"
            aria-selected={tab === "access"}
            aria-controls="admin-requests-access-panel"
            tabIndex={tab === "access" ? 0 : -1}
            onClick={() => selectTab("access")}
            onKeyDown={handleTabKeyDown}
            className={`rounded px-3 py-1.5 ${tab === "access" ? "bg-surface-2 text-foreground" : "text-muted-foreground hover:text-foreground"}`}
          >
            Access
          </button>
          <button
            type="button"
            ref={(element) => {
              tabRefs.current.credits = element;
            }}
            id="admin-requests-credits-tab"
            role="tab"
            aria-selected={tab === "credits"}
            aria-controls="admin-requests-credits-panel"
            tabIndex={tab === "credits" ? 0 : -1}
            onClick={() => selectTab("credits")}
            onKeyDown={handleTabKeyDown}
            className={`rounded px-3 py-1.5 ${tab === "credits" ? "bg-surface-2 text-foreground" : "text-muted-foreground hover:text-foreground"}`}
          >
            Credits
          </button>
        </div>

        <div
          id="admin-requests-access-panel"
          role="tabpanel"
          aria-labelledby="admin-requests-access-tab"
          tabIndex={0}
          hidden={tab !== "access"}
        >
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

        <div
          id="admin-requests-credits-panel"
          role="tabpanel"
          aria-labelledby="admin-requests-credits-tab"
          tabIndex={0}
          hidden={tab !== "credits"}
        >
          <CreditRequestQueue
            rows={creditRows}
            state={creditState}
            error={creditError}
            onRetry={loadCredits}
            onApprove={handleApproveCredit}
            onDecline={handleDeclineCredit}
          />
        </div>
      </div>
    </div>
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
  if (state === "forbidden") {
    return <EmptyQueue message="You don&apos;t have access to this page." />;
  }
  if (state === "loading" && rows.length === 0) {
    return (
      <div className="space-y-3">
        <div className="skeleton h-52 rounded-lg" />
        <div className="skeleton h-52 rounded-lg" />
      </div>
    );
  }
  if (state === "error") {
    return (
      <div className="flex flex-col items-center gap-3 py-12 text-center">
        <Warning className="h-5 w-5 text-destructive" weight="fill" />
        <p className="text-sm text-muted-foreground">{error}</p>
        <button
          type="button"
          onClick={onRetry}
          className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm hover:bg-surface-2"
        >
          Try again
        </button>
      </div>
    );
  }
  if (rows.length === 0) {
    return <EmptyQueue message="Nothing waiting. New credit requests show up here." />;
  }
  return (
    <div className="space-y-3">
      {rows.map((row) => (
        <CreditRequestCard
          key={row.id}
          row={row}
          onApprove={onApprove}
          onDecline={onDecline}
        />
      ))}
    </div>
  );
}

function EmptyQueue({ message }: { message: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 py-14 text-center">
      <p className="text-sm text-muted-foreground">{message}</p>
    </div>
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
        <button
          type="button"
          disabled={approvedZc === null}
          onClick={() => approvedZc !== null && onApprove(row, approvedZc)}
          className="interactive rounded-md bg-primary px-3.5 py-2 text-sm font-semibold text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
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
