"use client";

import { ArrowsClockwise, CheckCircle, CloudSlash, Prohibit } from "@phosphor-icons/react";
import {
  NOT_OBSERVED,
  NO_DEADLINE_NOTE,
  basisLabel,
  type CurrencyFigure,
  type RoutingPanel,
  type VenueVerdict,
} from "@/lib/job-routing";

/**
 * How one job routes: what kind of work the router took it to be, which
 * venues can and cannot run it, and what the reachable fleet would cost and
 * take.
 *
 * Lives here rather than inlined in `app/(console)/jobs/[jobId]/page.tsx`
 * for the usual two reasons: a `page.tsx` may export only a default
 * component plus route config, and a `.tsx` file gets no test coverage at
 * all. Every decision this renders — which of the three verdicts a venue is
 * making, whether a figure was observed, what may be said when the read
 * failed — is taken in `lib/job-routing.ts` where a test can reach it. This
 * file is markup and formatting.
 *
 * The one rule it enforces on its own, because it is a rule about layout:
 * ZC and USD are rendered as two separate figures in two separate cells,
 * with no row, cell or label that could hold a total. The scheduler may use
 * fixed parity internally, but this card shows only settlement provenance.
 */
export function RoutingCard({
  panel,
  onRetry,
}: {
  panel: RoutingPanel;
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

  if (panel.state === "unavailable") {
    return (
      <section className="panel p-4">
        <Heading />
        <p className="mt-3 max-w-prose text-sm leading-relaxed text-muted-foreground">
          Couldn&apos;t read how this job routes.
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

  if (panel.state === "unrouted") {
    return (
      <section className="panel p-4">
        <Heading />
        <p className="mt-3 max-w-prose text-sm leading-relaxed text-muted-foreground">
          The router did not run for this job, so there is nothing to compare.
        </p>
        <Notes notes={panel.notes} />
      </section>
    );
  }

  return (
    <section className="panel p-4">
      <Heading />

      <div className="mt-4 border-t border-border pt-4">
        <p className="label-caps">Kind of work</p>
        <p className="mt-1.5 font-mono text-sm">{panel.kind}</p>
        {panel.kindEvidence && (
          <p className="mt-1.5 max-w-prose text-sm leading-relaxed text-muted-foreground">
            {panel.kindEvidence}
          </p>
        )}
        {panel.tasks != null && (
          <p className="mt-1.5 font-mono text-xs text-muted-foreground tabular-nums">
            {panel.tasks} task{panel.tasks === 1 ? "" : "s"}
          </p>
        )}
      </div>

      {panel.venues.length > 0 && (
        <div className="mt-4 border-t border-border pt-4">
          <p className="label-caps">Venues</p>
          <p className="mt-1 max-w-prose text-xs text-muted-foreground">
            Every venue judged against this kind of work, including the ones
            that were ruled out. A venue that cannot do this work is not a
            cheap option — it is not an option.
          </p>
          <ul className="mt-3 divide-y divide-border">
            {panel.venues.map((v) => (
              <li key={v.id} className="py-3 first:pt-0 last:pb-0">
                <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                  <span className="text-sm font-medium">{v.display}</span>
                  <span
                    className={`rounded-md border px-1.5 py-0.5 font-mono text-[11px] ${VERDICT_STYLES[v.verdict]}`}
                  >
                    <VerdictIcon verdict={v.verdict} /> {v.headline}
                  </span>
                </div>
                <p className="mt-1.5 max-w-prose text-xs leading-relaxed text-muted-foreground">
                  {v.meaning}
                </p>
                {v.alsoUnreachable && (
                  <p className="mt-1 max-w-prose text-xs leading-relaxed text-warning-foreground">
                    We also have no way to obtain capacity here.
                  </p>
                )}
                {v.reason && (
                  <p className="mt-1.5 max-w-prose text-xs leading-relaxed text-muted-foreground">
                    {v.reason}
                  </p>
                )}
                <p className="mt-1.5 font-mono text-[11px] text-muted-foreground">
                  {v.id} · priced in {v.currency}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {panel.plans.length > 0 ? (
        <div className="mt-4 border-t border-border pt-4">
          <p className="label-caps">Ways to run it</p>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[520px] text-left">
              <thead>
                <tr className="border-b border-border">
                  {["Plan", "ZC", "USD", "Finishes in", "Machines", "Evidence"].map(
                    (h) => (
                      <th key={h} className="label-caps px-3 py-2 font-medium">
                        {h}
                      </th>
                    )
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {panel.plans.map((p) => (
                  <tr key={p.name}>
                    <td className="px-3 py-2.5 font-mono text-xs">
                      {p.name}
                      {p.recommended && (
                        <span className="ml-1.5 rounded-md border border-brand/40 px-1 py-0.5 text-[10px] text-brand-foreground">
                          recommended
                        </span>
                      )}
                      {p.tasksUnplaced > 0 && (
                        <span className="mt-1 block text-[11px] text-warning-foreground">
                          {p.tasksPlaced} of {p.tasksPlaced + p.tasksUnplaced}{" "}
                          tasks placed
                        </span>
                      )}
                    </td>
                    {/* Two cells, two currencies, and deliberately no third
                        cell they could be added into. */}
                    {p.costs.map((c: CurrencyFigure) => (
                      <td
                        key={c.currency}
                        className="px-3 py-2.5 font-mono text-xs tabular-nums"
                      >
                        {c.amount.toFixed(2)}
                      </td>
                    ))}
                    <td className="px-3 py-2.5 font-mono text-xs tabular-nums text-muted-foreground">
                      {p.makespanSeconds == null
                        ? NOT_OBSERVED
                        : duration(p.makespanSeconds)}
                    </td>
                    <td className="px-3 py-2.5 font-mono text-xs tabular-nums text-muted-foreground">
                      {p.machines}
                    </td>
                    <td className="px-3 py-2.5 font-mono text-xs text-muted-foreground">
                      {basisLabel(p.basis, p.n)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-2 max-w-prose text-xs leading-relaxed text-muted-foreground">
            {NO_DEADLINE_NOTE}
          </p>
          {panel.plans.some((p) => p.notes.length > 0) && (
            <ul className="mt-2 space-y-1">
              {panel.plans.flatMap((p) =>
                p.notes.map((note, i) => (
                  <li
                    key={`${p.name}-${i}`}
                    className="max-w-prose text-xs leading-relaxed text-muted-foreground"
                  >
                    <span className="font-mono">{p.name}</span>: {note}
                  </li>
                ))
              )}
            </ul>
          )}
        </div>
      ) : (
        <div className="mt-4 border-t border-border pt-4">
          <p className="label-caps">Ways to run it</p>
          <p className="mt-1.5 max-w-prose text-sm leading-relaxed text-muted-foreground">
            No plan is quoted for this job. The notes below say why.
          </p>
        </div>
      )}

      {panel.canary && (
        <div className="mt-4 border-t border-border pt-4">
          <p className="label-caps">Calibration</p>
          <p className="mt-1.5 max-w-prose text-sm leading-relaxed text-muted-foreground">
            {panel.canary.reason}
          </p>
          <p className="mt-1.5 font-mono text-xs text-muted-foreground tabular-nums">
            {panel.canary.tasks_to_calibrate} task
            {panel.canary.tasks_to_calibrate === 1 ? "" : "s"} on{" "}
            {panel.canary.machine_id} would measure it · currently{" "}
            {panel.canary.current_basis ?? NOT_OBSERVED}
          </p>
        </div>
      )}

      {panel.fleet && (
        <div className="mt-4 border-t border-border pt-4">
          <p className="label-caps">Fleet reachable from this account</p>
          <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
            <Stat label="Eligible" value={panel.fleet.eligible} />
            <Stat label="Refused by a gate" value={panel.fleet.excluded} />
            <Stat label="Wrong venue" value={panel.fleet.venueExcluded} />
            <Stat label="Not yet measurable" value={panel.fleet.unplannable} />
          </dl>
        </div>
      )}

      {panel.duration && (
        <div className="mt-4 border-t border-border pt-4">
          <p className="label-caps">Per-task duration</p>
          <p className="mt-1.5 font-mono text-sm tabular-nums">
            {panel.duration.low_seconds == null ||
            panel.duration.high_seconds == null
              ? NOT_OBSERVED
              : `${duration(panel.duration.low_seconds)} – ${duration(panel.duration.high_seconds)}`}
            <span className="ml-2 text-xs text-muted-foreground">
              {basisLabel(panel.duration.basis, panel.duration.n)}
            </span>
          </p>
          {panel.duration.note && (
            <p className="mt-1.5 max-w-prose text-xs leading-relaxed text-muted-foreground">
              {panel.duration.note}
            </p>
          )}
        </div>
      )}

      <Notes notes={panel.notes} />
    </section>
  );
}

function Heading() {
  return (
    <>
      <h2 className="text-sm font-semibold">How this job routes</h2>
      <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted-foreground">
        Where this work can go, why, and what each way would cost. Reading
        this changes nothing: no capacity is matched, held or charged by it.
      </p>
    </>
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

/** Neutral for a venue that simply is not for this work — that is a fact
 * about hardware and not a problem anybody has. Amber for one we cannot
 * obtain capacity at, because that IS a gap, and it is ours. */
const VERDICT_STYLES: Record<VenueVerdict, string> = {
  usable: "border-evergreen/40 text-evergreen",
  unreachable: "border-warning/50 text-warning-foreground",
  unsuited: "border-muted text-muted-foreground",
};

function VerdictIcon({ verdict }: { verdict: VenueVerdict }) {
  const className = "mr-0.5 inline h-3 w-3 align-[-1px]";
  if (verdict === "usable")
    return <CheckCircle className={className} weight="fill" />;
  if (verdict === "unreachable")
    return <CloudSlash className={className} weight="fill" />;
  return <Prohibit className={className} weight="fill" />;
}

/** Seconds as something a person reads. Formatting only — every value here
 * is a number the API returned, and an unobserved one never reaches this. */
function duration(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}
