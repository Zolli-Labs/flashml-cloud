"use client";

import {
  ArrowsClockwise,
  CheckCircle,
  Minus,
  Prohibit,
} from "@phosphor-icons/react";
import {
  NOT_OBSERVED,
  basisLabel,
  type AdviceTone,
  type TradeoffPanel,
  type TradeoffRow,
} from "@/lib/job-tradeoff";

/**
 * What each additional rented machine buys this job's finish time, and when
 * it buys nothing.
 *
 * Lives here rather than inlined in `app/(console)/jobs/[jobId]/page.tsx` for
 * the usual two reasons: a `page.tsx` may export only a default component
 * plus route config, and a `.tsx` file gets no test coverage at all. Every
 * decision this renders — which of the five verdicts a fleet size is making,
 * whether a figure was observed, what may be said when the read failed — is
 * taken in `lib/job-tradeoff.ts` where a test can reach it. This file is
 * markup and formatting.
 *
 * The one rule it enforces on its own, because it is a rule about layout:
 * **a row that costs more and finishes no sooner never looks like an offer.**
 * The three tones get three treatments and only one of them is affirmative;
 * there is no styling path from a `no-gain` row to a `gain` badge.
 */
export function TradeoffCard({
  panel,
  onRetry,
}: {
  panel: TradeoffPanel;
  onRetry: () => void;
}) {
  if (panel.state === "loading") {
    return (
      <section className="panel p-4">
        <Heading />
        <div className="skeleton mt-4 h-24 rounded-lg" />
      </section>
    );
  }

  if (panel.state === "unreadable") {
    return (
      <section className="panel p-4">
        <Heading />
        <p className="mt-3 max-w-prose text-sm leading-relaxed text-muted-foreground">
          Couldn&apos;t read what another machine would buy this job.
        </p>
        {panel.detail && (
          <p className="mt-2 max-w-prose font-mono text-xs text-muted-foreground">
            {panel.detail}
          </p>
        )}
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-border bg-surface px-2.5 py-1 text-xs hover:bg-surface-2"
        >
          <ArrowsClockwise className="h-3.5 w-3.5" /> Try again
        </button>
      </section>
    );
  }

  if (panel.state === "empty") {
    return (
      <section className="panel p-4">
        <Heading />
        <p className="mt-3 max-w-prose text-sm leading-relaxed text-muted-foreground">
          There is no fleet to compare for this job.
        </p>
        <Renting panel={panel} />
        <Notes notes={panel.notes} />
      </section>
    );
  }

  return (
    <section className="panel p-4">
      <Heading />

      <div className="mt-4 border-t border-border pt-4">
        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
          {panel.tasks != null && <Stat label="Tasks" value={panel.tasks} />}
          {panel.owned && (
            <>
              <Stat label="Your machines" value={panel.owned.machines} />
              <Stat label="Your slots" value={panel.owned.slots} />
            </>
          )}
          {panel.renting && (
            <Stat label="Rented, swept" value={panel.renting.slots} />
          )}
        </dl>
        <p className="mt-2 max-w-prose text-xs leading-relaxed text-muted-foreground">
          Per task:{" "}
          <span className="font-mono">
            {panel.taskSeconds == null
              ? NOT_OBSERVED
              : duration(panel.taskSeconds)}
          </span>
          {panel.duration && (
            <span className="ml-2">
              {basisLabel(panel.duration.basis, panel.duration.n)}
            </span>
          )}
        </p>
      </div>

      <Renting panel={panel} />

      <div className="mt-4 border-t border-border pt-4">
        <p className="label-caps">What each machine buys</p>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[560px] text-left">
            <thead>
              <tr className="border-b border-border">
                {[
                  "Fleet",
                  "Finishes in",
                  "Saved",
                  "ZC",
                  "USD",
                  "Total (ZC+USD)",
                  "Verdict",
                ].map((h) => (
                  <th key={h} className="label-caps px-3 py-2 font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {panel.rows.map((r) => (
                <tr key={r.totalSlots}>
                  <td className="px-3 py-2.5 font-mono text-xs tabular-nums">
                    {fleetLabel(r)}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs tabular-nums text-muted-foreground">
                    {r.finishSeconds == null
                      ? NOT_OBSERVED
                      : duration(r.finishSeconds)}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs tabular-nums text-muted-foreground">
                    {r.savedSeconds == null
                      ? NOT_OBSERVED
                      : r.savedSeconds <= 0
                        ? "nothing"
                        : duration(r.savedSeconds)}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs tabular-nums">
                    {r.zcCost.toFixed(2)}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs tabular-nums">
                    {r.usdCost == null ? NOT_OBSERVED : r.usdCost.toFixed(4)}
                  </td>
                  {/* Permitted here and only here: 1 ZC = $1 USD is settled
                      policy, so the two units add up — and both halves stay
                      in their own cells beside it. */}
                  <td className="px-3 py-2.5 font-mono text-xs tabular-nums text-muted-foreground">
                    {r.totalUsdValue == null
                      ? NOT_OBSERVED
                      : r.totalUsdValue.toFixed(4)}
                  </td>
                  <td className="px-3 py-2.5 text-xs">
                    <span
                      className={`rounded-md border px-1.5 py-0.5 font-mono text-[11px] ${TONE_STYLES[r.tone]}`}
                    >
                      <ToneIcon tone={r.tone} /> {r.headline}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <ul className="mt-3 space-y-1.5">
          {distinctVerdicts(panel.rows).map((r) => (
            <li
              key={r.adviceCode}
              className="max-w-prose text-xs leading-relaxed text-muted-foreground"
            >
              <span className="font-medium text-foreground">{r.headline}</span>{" "}
              — {r.meaning}
            </li>
          ))}
        </ul>

        {panel.nothingHelps ? (
          <p className="mt-3 max-w-prose text-sm leading-relaxed text-warning-foreground">
            No fleet size on this curve finishes sooner than the machines you
            already have. Buying capacity for this job would spend money and
            save nothing.
          </p>
        ) : (
          panel.lastGain && (
            <p className="mt-3 max-w-prose text-sm leading-relaxed">
              Nothing past{" "}
              <span className="font-mono">
                {panel.lastGain.totalSlots} slot
                {panel.lastGain.totalSlots === 1 ? "" : "s"}
              </span>{" "}
              finishes this job any sooner.
            </p>
          )
        )}
      </div>

      <Notes notes={panel.notes} />
    </section>
  );
}

function Heading() {
  return (
    <>
      <h2 className="text-sm font-semibold">What another machine would buy</h2>
      <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted-foreground">
        Each fleet size this job could run on, what it would finish in, and
        what it would cost. Reading this changes nothing: no capacity is
        rented, matched, held or charged by it.
      </p>
    </>
  );
}

/** The two refusals, kept visibly apart — see `lib/job-tradeoff.ts`. */
function Renting({ panel }: { panel: TradeoffPanel }) {
  const renting = panel.renting;
  if (!renting) return null;
  return (
    <div className="mt-4 border-t border-border pt-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="label-caps">Renting</p>
        <span
          className={`rounded-md border px-1.5 py-0.5 font-mono text-[11px] ${
            renting.verdict === "can-help"
              ? TONE_STYLES.gain
              : TONE_STYLES["no-gain"]
          }`}
        >
          {renting.headline}
        </span>
      </div>
      <p className="mt-2 max-w-prose text-xs leading-relaxed text-muted-foreground">
        {renting.reason}
      </p>
      <p className="mt-1.5 max-w-prose text-xs leading-relaxed text-muted-foreground">
        {renting.priceReason}
      </p>
      {renting.price && (
        <p className="mt-1.5 max-w-prose font-mono text-[11px] text-muted-foreground">
          {renting.price.provider} · {renting.price.sku}
          {renting.price.tier ? ` · ${renting.price.tier}` : ""} ·{" "}
          {renting.price.amount} {renting.price.currency}/
          {renting.price.unit} · captured {renting.price.captured_at}
          {renting.price.stale ? " · STALE" : ""}
        </p>
      )}
      {renting.slotsReason && (
        <p className="mt-1.5 max-w-prose text-xs leading-relaxed text-muted-foreground">
          {renting.slotsReason}
        </p>
      )}
    </div>
  );
}

function Notes({ notes }: { notes: string[] }) {
  if (notes.length === 0) return null;
  return (
    <ul className="mt-4 space-y-1.5 border-t border-border pt-4">
      {notes.map((note, i) => (
        <li
          key={i}
          className="max-w-prose text-xs leading-relaxed text-muted-foreground"
        >
          {note}
        </li>
      ))}
    </ul>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="label-caps">{label}</dt>
      <dd className="metric-value mt-0.5 text-lg">{value}</dd>
    </div>
  );
}

/** One legend entry per verdict actually on the table, in the order they
 * first appear — so a reader meets each explanation once rather than per
 * row, and never meets one for a verdict that is not there. */
function distinctVerdicts(rows: TradeoffRow[]): TradeoffRow[] {
  const seen = new Set<string>();
  const out: TradeoffRow[] = [];
  for (const r of rows) {
    if (seen.has(r.adviceCode)) continue;
    seen.add(r.adviceCode);
    out.push(r);
  }
  return out;
}

function fleetLabel(r: TradeoffRow): string {
  if (r.rentedSlots === 0) return `${r.totalSlots} yours`;
  return `${r.totalSlots} · +${r.rentedSlots} rented`;
}

/** Evergreen only for a fleet that genuinely finishes sooner. A row that
 * costs more and buys nothing is muted, not amber: it is not a fault anybody
 * has to fix, it is arithmetic, and dressing it in an alarm colour would be
 * as misleading in the other direction as a green badge. */
const TONE_STYLES: Record<AdviceTone, string> = {
  gain: "border-evergreen/40 text-evergreen",
  "no-gain": "border-muted text-muted-foreground",
  neutral: "border-border text-muted-foreground",
};

function ToneIcon({ tone }: { tone: AdviceTone }) {
  const className = "mr-0.5 inline h-3 w-3 align-[-1px]";
  if (tone === "gain") return <CheckCircle className={className} weight="fill" />;
  if (tone === "no-gain") return <Prohibit className={className} weight="fill" />;
  return <Minus className={className} weight="bold" />;
}

/** Seconds as something a person reads. Formatting only — every value here is
 * a number the API returned, and an unobserved one never reaches this. */
function duration(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}
