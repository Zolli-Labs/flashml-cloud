"use client";

import { ArrowsClockwise } from "@phosphor-icons/react";

import {
  PLANS_UNREADABLE,
  formatAmount,
  formatFinish,
  planStripView,
} from "@/lib/deploy/plan-strip";
import { basisLabel, type PlanRow, type RoutingPanel } from "@/lib/job-routing";

/**
 * What this job will cost and how long it will take, as the router answers
 * it — cheapest, fastest, and balanced when a deadline was given.
 *
 * A STRIP, NOT THE ROUTING CARD. `components/jobs/RoutingCard.tsx` renders
 * the whole routing story on the job page: the kind of work and its
 * evidence, every venue including the refused ones, the calibration probe,
 * the fleet counters. None of that belongs at the moment a submission has
 * just landed — the question then is only "what did I just commit to". This
 * shows the plans and nothing else, and the job page is one click away.
 *
 * IT SHAPES NOTHING. The three plans are not options in a form: the submit
 * payload this API accepts is `{repo, ref, pool}`, with no deadline, no
 * budget and no plan selector, so there is nothing on this page for a
 * selection to change. They are rendered informational and are labelled as
 * such, rather than made clickable in a way that would imply a choice the
 * API cannot accept. **There is deliberately no rent-authorization flow
 * here** — nothing on this route rents, holds, matches or charges anything.
 *
 * Every figure is a value the route returned; `null` arrives as `null` and
 * leaves as *not observed*, never as a zero. ZC and USD sit in separate
 * cells with nowhere to put a total, which is `lib/job-routing.ts`'s rule
 * and is enforced here by layout as well as by copy.
 */
export function PlanStrip({
  panel,
  onRetry,
}: {
  panel: RoutingPanel;
  onRetry: () => void;
}) {
  const view = planStripView(panel);

  return (
    <section className="panel p-4">
      <p className="label-caps">Ways to run it</p>

      {view.kind === "loading" && (
        <div className="skeleton mt-3 h-20 rounded-lg" />
      )}

      {view.kind === "unavailable" && (
        <>
          <p className="mt-2 max-w-prose text-sm text-muted-foreground">
            {PLANS_UNREADABLE}
          </p>
          {view.detail && (
            <p className="mt-1.5 max-w-prose font-mono text-xs text-muted-foreground">
              {view.detail}
            </p>
          )}
          <button
            type="button"
            onClick={onRetry}
            className="mt-3 inline-flex items-center gap-1.5 rounded-md border border-border bg-surface px-2.5 py-1 text-xs hover:bg-surface-2"
          >
            <ArrowsClockwise className="h-3.5 w-3.5" /> Try again
          </button>
        </>
      )}

      {/* One sentence in place of a table of dashes — see
          `lib/deploy/plan-strip.ts` for when this branch is taken. */}
      {view.kind === "sentence" && (
        <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
          {view.text}
        </p>
      )}

      {view.kind === "plans" && (
        <ul className="mt-3 grid gap-2 sm:grid-cols-2">
          {view.plans.map((plan) => (
            <PlanCard key={plan.name} plan={plan} />
          ))}
        </ul>
      )}

      {"notes" in view && view.notes.length > 0 && (
        <ul className="mt-3 space-y-1 border-t border-border pt-3">
          {view.notes.map((note, i) => (
            <li
              key={i}
              className="max-w-prose text-xs leading-relaxed text-muted-foreground"
            >
              {note}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** One plan. The recommended one is the only card with a filled border and
 * a marker, because the route names exactly one and a strip where every card
 * looks primary has recommended nothing. */
function PlanCard({ plan }: { plan: PlanRow }) {
  return (
    <li
      className={`rounded-lg border p-3 ${
        plan.recommended ? "border-brand/50 bg-surface-2" : "border-border"
      }`}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="font-mono text-sm">{plan.name}</span>
        {plan.recommended && (
          <span className="rounded-md border border-brand/40 px-1 py-0.5 font-mono text-[10px] text-brand-foreground">
            recommended
          </span>
        )}
      </div>

      {/* Each currency in its own cell, mapped from the list the route
          returned rather than read out by index — a list is the shape with
          nowhere to put a total, and hard-coding two cells would quietly
          drop a third currency the day one exists. */}
      <dl className="mt-2.5 grid grid-cols-2 gap-x-3 gap-y-2">
        {plan.costs.map((cost) => (
          <Figure
            key={cost.currency}
            label={cost.currency}
            value={formatAmount(cost.amount)}
          />
        ))}
        <Figure label="Finishes in" value={formatFinish(plan.makespanSeconds)} />
        <Figure label="Machines" value={String(plan.machines)} />
      </dl>

      {/* The route's own evidence for every figure above it: how it was
          arrived at and how many samples are behind it. A quote with no
          basis is a guess with a decimal point. */}
      <p className="meta mt-2.5">{basisLabel(plan.basis, plan.n)}</p>

      {plan.dominatedBy && (
        <p className="mt-1 text-xs text-muted-foreground">
          Beaten on both axes by {plan.dominatedBy}.
        </p>
      )}
    </li>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="label-caps">{label}</dt>
      <dd className="metric-value mt-0.5 truncate text-sm">{value}</dd>
    </div>
  );
}
