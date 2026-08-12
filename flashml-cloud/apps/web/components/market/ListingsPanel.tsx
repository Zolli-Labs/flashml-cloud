"use client";

import { useCallback, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  GraphicsCard,
  Info,
  Storefront,
  TagSimple,
  Warning,
} from "@phosphor-icons/react";

import {
  NotAuthenticated,
  getMarketHint,
  type Machine,
  type MarketAsk,
  type MarketHint,
  type MarketListing,
} from "@/lib/cloud-api";
import { formatZc } from "@/lib/market-credits";
import {
  askPriceLabel,
  bookChips,
  effectiveLabel,
  listingStateLabel,
  recordLabel,
  specLine,
  type AskChip,
  type ClassBook,
} from "@/lib/market-listings";
import { isOnline } from "@/lib/machine-status";

/** The listings page: choose a machine you can see, read what the market
 * says about it, price it against the open book — in that order.
 *
 * Markup and fetch-choreography only. Every label a row shows comes from
 * `lib/market-listings.ts`; the book's ranking is the API's; the hint is
 * the market itself (best / median / reference), never a model. The only
 * aggregations computed here are display sums of real rows the API already
 * returned: the class-header spread and the header counts. */
export function ListingsPanel({
  state,
  books,
  mine,
  machines,
  machinesLoaded,
  onList,
  onWithdraw,
  onRetry,
  error,
}: {
  /** The book + your-listings read (`getMarketListings`). */
  state: "loading" | "present" | "unreadable";
  books: ClassBook[];
  mine: MarketListing[];
  /** The account's own machines, for the picker. Fetched independently of
   * the book, so a failed book read does not empty the picker. */
  machines: Machine[];
  machinesLoaded: boolean;
  onList: (machineId: string, askMzc: number) => Promise<string | null>;
  onWithdraw: (listingId: string) => Promise<void>;
  onRetry: () => void;
  error: string | null;
}) {
  const router = useRouter();

  // --- the ask form --------------------------------------------------------
  const [ask, setAsk] = useState("0");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [withdrawing, setWithdrawing] = useState<string | null>(null);

  // --- selection + market hint -------------------------------------------
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hint, setHint] = useState<MarketHint | null>(null);
  const [hintState, setHintState] = useState<
    "loading" | "present" | "unreadable"
  >("loading");
  const [hintError, setHintError] = useState<string | null>(null);
  // A selection made while an older hint is still in flight must never be
  // answered by the stale one.
  const hintRequestId = useRef(0);

  const loadHint = useCallback(
    (machineId: string) => {
      const requestId = ++hintRequestId.current;
      setHintState("loading");
      setHintError(null);
      getMarketHint(machineId)
        .then((h) => {
          if (hintRequestId.current !== requestId) return;
          setHint(h);
          setHintState("present");
        })
        .catch((err) => {
          if (hintRequestId.current !== requestId) return;
          if (err instanceof NotAuthenticated) {
            router.push("/sign-in?next=/market/listings");
            return;
          }
          setHintError(
            err instanceof Error ? err.message : "Couldn't read the market."
          );
          setHintState("unreadable");
        });
    },
    [router]
  );

  const select = useCallback(
    (machineId: string) => {
      if (machineId === selectedId) return;
      setSelectedId(machineId);
      setHint(null);
      setProblem(null);
      loadHint(machineId);
    },
    [selectedId, loadHint]
  );

  const selectedMachine =
    machines.find((m) => m.id === selectedId) ?? null;
  const unclassifiable =
    hintState === "present" && hint !== null && hint.unclassifiable !== null;
  const canSubmit =
    selectedId !== null &&
    hintState === "present" &&
    !unclassifiable &&
    !busy;

  function applyAsk(mzc: number) {
    // Write the canonical decimal back into the input — `formatZc` output
    // always re-parses cleanly, so a chip can never type an unparseable ask.
    setAsk(formatZc(mzc));
    setProblem(null);
  }

  async function submit() {
    if (!selectedId) {
      setProblem("Choose which machine to list.");
      return;
    }
    // The ask field is a ZC decimal typed by a person; parse it to
    // millicredits with integer arithmetic only. A value that does not
    // parse is refused here, not rounded.
    const parsed = parseZcToMzc(ask);
    if (parsed === null) {
      setProblem("The ask must be a number of ZC per hour, e.g. 0.22 or 0.");
      return;
    }
    setBusy(true);
    setProblem(null);
    const err = await onList(selectedId, parsed);
    setBusy(false);
    if (err) {
      setProblem(err);
      return;
    }
    // The book this hint described just changed — re-read it.
    loadHint(selectedId);
  }

  async function withdraw(listing: MarketListing) {
    setWithdrawing(listing.id);
    try {
      await onWithdraw(listing.id);
      if (selectedId) loadHint(selectedId);
    } finally {
      setWithdrawing(null);
    }
  }

  return (
    <div className="space-y-10">
      {/* ── 1 · the offer: pick a machine, read the market, name a price ── */}
      <section>
        <h2 className="label-caps">List a machine</h2>
        <p className="mt-1.5 max-w-prose text-xs leading-relaxed text-muted-foreground">
          Choose one of your machines, read what the market says about it,
          then set your ask against the open book below.
        </p>

        <div className="mt-3">
          {!machinesLoaded ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <div className="skeleton h-20" />
              <div className="skeleton h-20" />
              <div className="skeleton h-20" />
            </div>
          ) : machines.length === 0 ? (
            <EmptyState
              icon={<GraphicsCard className="h-4 w-4" weight="fill" />}
            >
              No machines on this account yet — enroll one under{" "}
              <Link
                href="/account/machines"
                className="text-brand-foreground hover:underline"
              >
                My machines
              </Link>{" "}
              first.
            </EmptyState>
          ) : (
            <div
              role="radiogroup"
              aria-label="Choose a machine to list"
              className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
            >
              {machines.map((machine) => (
                <MachineCard
                  key={machine.id}
                  machine={machine}
                  selected={machine.id === selectedId}
                  onSelect={() => select(machine.id)}
                />
              ))}
            </div>
          )}
        </div>

        {/* The market read + the ask form. Exists only once a machine is
            selected — before that there is nothing to ask the market about. */}
        {selectedId && selectedMachine && (
          <div className="mt-3 rounded-lg border border-border bg-surface p-4">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="label-caps">Market read</h3>
              <span className="font-mono text-xs text-muted-foreground">
                {selectedMachine.name || selectedMachine.node_id}
              </span>
              {hintState === "present" && hint?.capability_class && (
                <span className="rounded-full border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-foreground">
                  {hint.capability_class}
                </span>
              )}
              {unclassifiable && (
                <span className="rounded-full border border-destructive/40 bg-destructive/10 px-1.5 py-0.5 font-mono text-[10px] text-destructive">
                  unclassifiable
                </span>
              )}
              {hintState === "present" && hint?.book && (
                <span className="rounded-full border border-border px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                  {hint.book.open_asks} open ask
                  {hint.book.open_asks === 1 ? "" : "s"}
                </span>
              )}
            </div>

            {hintState === "loading" && (
              <p className="mt-3 font-mono text-xs text-muted-foreground">
                asking the market about this machine…
              </p>
            )}

            {hintState === "unreadable" && (
              <div className="mt-3">
                <p className="flex max-w-prose items-start gap-2 text-sm leading-relaxed text-warning-foreground">
                  <Warning
                    className="mt-0.5 h-3.5 w-3.5 shrink-0"
                    weight="fill"
                  />
                  <span>
                    Couldn&apos;t read what the market says about this
                    machine. The listing itself would still go to the API to
                    be judged — but you would be pricing blind.
                  </span>
                </p>
                {hintError && (
                  <p className="mt-1.5 break-all font-mono text-[11px] text-muted-foreground">
                    {hintError}
                  </p>
                )}
                <button
                  type="button"
                  onClick={() => loadHint(selectedId)}
                  className="mt-3 rounded-md border border-border bg-surface px-2.5 py-1 text-xs hover:bg-surface-2"
                >
                  Try again
                </button>
              </div>
            )}

            {hintState === "present" && hint && (
              <div className="mt-3 space-y-3">
                {unclassifiable ? (
                  <p className="flex max-w-prose items-start gap-2 text-sm leading-relaxed text-warning-foreground">
                    <Info
                      className="mt-0.5 h-3.5 w-3.5 shrink-0"
                      weight="fill"
                    />
                    {/* The ladder's own refusal words, surfaced before the
                        click instead of as a 409 after it. */}
                    <span>{hint.unclassifiable}</span>
                  </p>
                ) : (
                  <>
                    <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xs">
                      <span className="text-muted-foreground">
                        Your record in this class:
                      </span>
                      <span
                        className={
                          hint.your_record
                            ? "font-mono text-foreground"
                            : "text-muted-foreground"
                        }
                      >
                        {recordSentence(hint)}
                      </span>
                    </p>
                    {(!hint.book || hint.book.open_asks === 0) && (
                      <p className="max-w-prose text-xs leading-relaxed text-muted-foreground">
                        No open asks in this class yet — you would set the
                        first price.
                      </p>
                    )}
                    <div className="flex flex-wrap items-center gap-2">
                      {bookChips(hint)
                        .filter(
                          (chip): chip is AskChip & { valueMzc: number } =>
                            chip.valueMzc !== null
                        )
                        .map((chip) => (
                          <button
                            key={chip.label}
                            type="button"
                            onClick={() => applyAsk(chip.valueMzc)}
                            className="rounded-full border border-border bg-surface px-2.5 py-1 text-[11px] text-muted-foreground hover:border-primary/50 hover:text-foreground"
                          >
                            {chip.label}{" "}
                            <span className="font-mono text-foreground">
                              {chip.valueText}
                            </span>
                          </button>
                        ))}
                      <button
                        type="button"
                        onClick={() => applyAsk(0)}
                        className="rounded-full border border-brand/40 bg-brand/10 px-2.5 py-1 text-[11px] text-brand-foreground hover:border-brand"
                      >
                        Donate
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}

            {/* The ask. Visible in every hint state — a failed hint read
                does not close the market, and an unclassifiable machine
                gets the refusal above plus disabled controls. */}
            <div className="mt-4 border-t border-border pt-3">
              <label htmlFor="listing-ask" className="label-caps">
                Ask, ZC per hour
              </label>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <input
                  id="listing-ask"
                  value={ask}
                  onChange={(e) => setAsk(e.target.value)}
                  inputMode="decimal"
                  disabled={unclassifiable}
                  className="w-28 rounded-md border border-border bg-surface px-2 py-1.5 font-mono text-sm text-foreground disabled:opacity-50"
                />
                <button
                  type="button"
                  disabled={!canSubmit}
                  onClick={submit}
                  className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-60"
                >
                  {busy ? "Listing…" : "List machine"}
                </button>
              </div>
              <p className="mt-2 max-w-prose text-[11px] leading-tight text-muted-foreground">
                The capability class comes from what your agent reported
                about the machine — it is computed server-side and is not a
                choice here. Zero is a legal ask and reads as donated.
              </p>
              {problem && (
                <p className="mt-2 text-xs text-destructive">{problem}</p>
              )}
            </div>
          </div>
        )}
      </section>

      {/* ── 2 · the book ─────────────────────────────────────────────── */}
      <section>
        <h2 className="label-caps">The book</h2>
        <p className="mt-1.5 max-w-prose text-xs leading-relaxed text-muted-foreground">
          Open asks grouped by capability class, ranked by what a buyer
          actually pays per accepted hour.
        </p>

        {state === "loading" && (
          <div className="mt-3 space-y-2">
            <div className="skeleton h-4 w-32" />
            <div className="skeleton h-28" />
            <div className="skeleton h-4 w-32" />
            <div className="skeleton h-28" />
          </div>
        )}

        {state === "unreadable" && (
          <div className="mt-3">
            <p className="flex max-w-prose items-start gap-2 text-sm leading-relaxed text-warning-foreground">
              <Warning className="mt-0.5 h-3.5 w-3.5 shrink-0" weight="fill" />
              <span>
                Couldn&apos;t read the market book. That is not the same as
                an empty book.
              </span>
            </p>
            {error && (
              <p className="mt-1.5 break-all font-mono text-[11px] text-muted-foreground">
                {error}
              </p>
            )}
            <button
              type="button"
              onClick={onRetry}
              className="mt-3 rounded-md border border-border bg-surface px-2.5 py-1 text-xs hover:bg-surface-2"
            >
              Try again
            </button>
          </div>
        )}

        {state === "present" &&
          (books.length === 0 ? (
            <div className="mt-3">
              <EmptyState
                icon={<Storefront className="h-4 w-4" weight="fill" />}
              >
                The book is empty — nobody is offering capacity yet. List a
                machine above and it appears here at your ask.
              </EmptyState>
            </div>
          ) : (
            <div className="mt-3 space-y-6">
              {books.map((book) => (
                <ClassSection key={book.capabilityClass} book={book} />
              ))}
            </div>
          ))}
      </section>

      {/* ── 3 · your listings ────────────────────────────────────────── */}
      {state === "present" && (
        <section>
          <h2 className="label-caps">Your listings</h2>
          {mine.length === 0 ? (
            <div className="mt-3">
              <EmptyState
                icon={<TagSimple className="h-4 w-4" weight="fill" />}
              >
                You have no listings — offer a machine above at your own
                ask; zero is legal and reads as donated.
              </EmptyState>
            </div>
          ) : (
            <div className="mt-3 overflow-x-auto rounded-lg border border-border bg-surface">
              <table className="w-full text-xs">
                <thead>
                  <tr>
                    <th className="label-caps px-3 py-2.5 text-left font-medium">
                      Machine
                    </th>
                    <th className="label-caps px-3 py-2.5 text-left font-medium">
                      Class
                    </th>
                    <th className="label-caps px-3 py-2.5 text-right font-medium">
                      Ask
                    </th>
                    <th className="label-caps px-3 py-2.5 text-left font-medium">
                      State
                    </th>
                    <th className="px-3 py-2.5">
                      <span className="sr-only">Action</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {mine.map((listing) => (
                    <tr key={listing.id} title={listing.created_at}>
                      <td className="max-w-36 truncate px-3 py-2.5 text-xs font-medium text-foreground">
                        {machineLabel(listing.machine_id, machines)}
                      </td>
                      <td className="px-3 py-2.5 font-mono text-xs text-muted-foreground">
                        {listing.capability_class}
                      </td>
                      <td className="px-3 py-2.5 text-right font-mono text-xs text-foreground">
                        {listing.donated ? (
                          <span className="rounded-full border border-brand/40 bg-brand/10 px-1.5 py-0.5 font-mono text-[10px] text-brand-foreground">
                            donated
                          </span>
                        ) : (
                          `${formatZc(listing.ask_zc_per_hour)} ZC/hour`
                        )}
                      </td>
                      <td className="px-3 py-2.5">
                        <span
                          className={`rounded-full border px-1.5 py-0.5 font-mono text-[10px] ${
                            LISTING_STATE_TONE[listing.state] ??
                            UNKNOWN_STATE_TONE
                          }`}
                        >
                          {listingStateLabel(listing)}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        {listing.state !== "withdrawn" && (
                          <button
                            type="button"
                            disabled={withdrawing === listing.id}
                            onClick={() => withdraw(listing)}
                            className="rounded-md border border-border bg-surface px-2 py-1 text-[11px] text-muted-foreground hover:bg-surface-2 hover:text-destructive disabled:opacity-60"
                          >
                            {withdrawing === listing.id
                              ? "Withdrawing…"
                              : "Withdraw"}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

/* ── sub-components ─────────────────────────────────────────────────────── */

/** One machine you might list: name, the fleet table's online rule, and the
 * spec line from what the agent reported. A radio card, not a `<select>` —
 * the selection is the page's first object. */
function MachineCard({
  machine,
  selected,
  onSelect,
}: {
  machine: Machine;
  selected: boolean;
  onSelect: () => void;
}) {
  // Same derivation the fleet tables use (`YourMachines`): a revoked token
  // can never check in, so revoked never reads as online.
  const online =
    machine.status !== "revoked" && isOnline(machine.last_seen_at);
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      className={
        selected
          ? "rounded-lg border border-primary bg-surface p-3 text-left ring-1 ring-primary"
          : "rounded-lg border border-border bg-surface p-3 text-left hover:border-primary/50"
      }
    >
      <span className="flex items-center gap-2">
        <span
          className="status-dot"
          data-state={online ? "live" : undefined}
          style={{
            background: online
              ? "var(--node-green)"
              : "var(--muted-foreground)",
          }}
        />
        <span className="truncate text-sm font-medium text-foreground">
          {machine.name || machine.node_id}
        </span>
      </span>
      <span className="mt-1.5 block truncate font-mono text-[11px] text-muted-foreground">
        {machineSpecLine(machine)}
      </span>
    </button>
  );
}

/** One capability class of the book: a header with the class's spread,
 * then the API-ranked rows. */
function ClassSection({ book }: { book: ClassBook }) {
  const spread = classSpread(book.asks);
  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <div className="flex items-center gap-2">
          <h3 className="font-mono text-xs font-medium text-foreground">
            {book.capabilityClass}
          </h3>
          <span className="rounded-full border border-border px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
            {book.asks.length} open ask
            {book.asks.length === 1 ? "" : "s"}
          </span>
        </div>
        {spread && (
          <span className="font-mono text-[11px] text-muted-foreground">
            best {spreadPart(spread.best)} · median{" "}
            {spreadPart(spread.median)}
          </span>
        )}
      </div>
      <ul className="mt-2 divide-y divide-border rounded-md border border-border bg-surface">
        {book.asks.map((ask) => (
          <AskRow key={ask.id} ask={ask} />
        ))}
      </ul>
    </div>
  );
}

/** One open ask: spec line, the host's record badge, the ask, and the
 * per-accepted-hour figure where the effective price sits. Donated rows
 * lead with the word, brand-tinted — never a 0 dressed as a price. */
function AskRow({ ask }: { ask: MarketAsk }) {
  const spec = specLine(ask);
  const hasRecord = ask.acceptance_rate !== null && ask.resolved_n !== null;
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2.5">
      {ask.donated && (
        <span className="rounded-full border border-brand/40 bg-brand/10 px-1.5 py-0.5 font-mono text-[10px] text-brand-foreground">
          donated
        </span>
      )}
      <div className="min-w-0 flex-1 basis-44">
        <div className="truncate text-xs font-medium text-foreground">
          {spec}
        </div>
        {ask.machine_name && ask.machine_name !== spec && (
          <div className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
            {ask.machine_name}
          </div>
        )}
      </div>
      {hasRecord ? (
        <span
          title={recordLabel(ask)}
          className="rounded-full border border-evergreen/40 bg-evergreen/10 px-1.5 py-0.5 font-mono text-[10px] text-evergreen"
        >
          {Math.round((ask.acceptance_rate as number) * 100)}% ·{" "}
          {ask.resolved_n}
        </span>
      ) : (
        <span
          title={recordLabel(ask)}
          className="rounded-full border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
        >
          unproven
        </span>
      )}
      <div className="ml-auto shrink-0 text-right">
        {!ask.donated && (
          <div className="font-mono text-xs text-foreground">
            {askPriceLabel(ask)}
          </div>
        )}
        <div
          className={`text-[11px] text-muted-foreground${
            ask.donated ? "" : " mt-0.5"
          }`}
        >
          {effectiveLabel(ask)}
        </div>
      </div>
    </li>
  );
}

/** The designed empty state: centred icon in a dashed square + one
 * sentence — the metrics page's unmeasured-card pattern, reused. */
function EmptyState({
  icon,
  children,
}: {
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center rounded-lg border border-dashed border-border px-4 py-10 text-center">
      <div className="flex h-9 w-9 items-center justify-center rounded-md border border-dashed border-border text-muted-foreground">
        {icon}
      </div>
      <p className="mt-3 max-w-sm text-sm leading-relaxed text-muted-foreground">
        {children}
      </p>
    </div>
  );
}

/* ── display-only helpers ───────────────────────────────────────────────── */

/** The state vocabulary is the API's (`listingStateLabel`); the tone is
 * markup. An unrecognised state gets the destructive tone and is quoted
 * back verbatim by the label — never hidden. */
const LISTING_STATE_TONE: Record<string, string> = {
  open: "border-evergreen/40 bg-evergreen/10 text-evergreen",
  paused: "border-warning/50 bg-warning/10 text-warning-foreground",
  withdrawn: "border-border bg-surface-2 text-muted-foreground",
};
const UNKNOWN_STATE_TONE =
  "border-destructive/40 bg-destructive/10 text-destructive";

/** Your listings name their machines; the API gives the panel ids, and the
 * account's own machine list supplies the name. An id with no matching
 * machine renders as the id — real data, not a guessed name. */
function machineLabel(machineId: string, machines: Machine[]): string {
  const machine = machines.find((m) => m.id === machineId);
  if (!machine) return machineId;
  return machine.name || machine.node_id;
}

/** The picker's spec line, from what the agent reported — the same rule
 * the server's `machine_gpu_label` applies to the book's rows, run locally
 * over `capabilities` because the shared machines route deliberately
 * carries no market fields. Display only: nothing here feeds the class.
 * Absent or partial reports read "no spec reported", never a blank cell. */
function machineSpecLine(machine: Machine): string {
  const caps = machine.capabilities;
  if (!caps) return "no spec reported";
  const gpus = caps.gpus;
  if (Array.isArray(gpus) && gpus.length > 0) {
    const first = gpus[0] as Record<string, unknown> | null;
    const name =
      first && typeof first.name === "string" ? first.name : null;
    const memoryMb =
      first &&
      typeof first.memory_total_mb === "number" &&
      Number.isFinite(first.memory_total_mb)
        ? first.memory_total_mb
        : null;
    if (name !== null && memoryMb !== null) {
      const gib = Math.round(memoryMb / 1024);
      return gpus.length > 1
        ? `${name} · ${gib} GB · ${gpus.length} GPU`
        : `${name} · ${gib} GB`;
    }
  }
  const cores = caps.cpu_cores;
  if (typeof cores === "number" && Number.isFinite(cores)) {
    return `${Math.trunc(cores)} cores`;
  }
  return "no spec reported";
}

/** The class header's spread, aggregated from the class's real rows for
 * display only — best is the API's rank leader (groupBookByClass preserves
 * the route's order), median mirrors the server's `class_board` order
 * statistic: sorted asks, the middle one, integer mean of the middle two
 * when even. */
function classSpread(
  asks: MarketAsk[]
): { best: number; median: number } | null {
  if (asks.length === 0) return null;
  const ordered = asks
    .map((a) => a.ask_zc_per_hour)
    .sort((a, b) => a - b);
  const mid = Math.floor(ordered.length / 2);
  const median =
    ordered.length % 2 === 1
      ? ordered[mid]
      : Math.floor((ordered[mid - 1] + ordered[mid]) / 2);
  return { best: asks[0].ask_zc_per_hour, median };
}

/** A spread figure, keeping the donated discipline: a zero ask is the word,
 * never "0 ZC/h" dressed as a price. */
function spreadPart(mzc: number): string {
  return mzc === 0 ? "donated" : `${formatZc(mzc)} ZC/h`;
}

/** The hint's `your_record`, in `recordLabel`'s shape: a real rate travels
 * with its count, and null is a sentence — "unproven" — never a number. */
function recordSentence(hint: MarketHint): string {
  const record = hint.your_record;
  if (!record) {
    return "unproven — no accepted-work record in this class yet";
  }
  return `${Math.round(record.acceptance_rate * 100)}% accepted of ${record.resolved_n} resolved in this class`;
}

/** A person-typed ZC amount to millicredits, refusing anything that is
 * not a clean decimal with at most three places — the fourth place would
 * be a fraction of a millicredit the ledger cannot hold. Integer
 * arithmetic; no float multiply. */
function parseZcToMzc(text: string): number | null {
  const trimmed = text.trim();
  if (!/^\d+(\.\d{1,3})?$/.test(trimmed)) return null;
  const [whole, frac = ""] = trimmed.split(".");
  return Number(whole) * 1000 + Number(frac.padEnd(3, "0") || "0");
}
