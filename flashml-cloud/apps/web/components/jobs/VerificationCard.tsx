"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle, Flag, Question } from "@phosphor-icons/react";

import { StatePanel } from "@/components/shell/StatePanel";
import { StatTile } from "@/components/ui/stat-tile";
import { resolvePanel } from "@/lib/console/panel-state";
import {
  summariseVerifications,
  type VerificationRow,
  type VerificationTaskGroup,
  type VerificationVerdict,
} from "@/lib/job-verifications";
import {
  NotAuthenticated,
  listJobVerifications,
  type Verification,
} from "@/lib/cloud-api";

/** How often to re-read the verdict listing while the job is still running.
 * Matches the job page's own poll cadence (`POLL_MS` in
 * `app/(console)/jobs/[jobId]/page.tsx`): verification rows accumulate as
 * tasks settle over the life of a run, the same reason the artifact listing
 * is re-read on every tick rather than once. */
const POLL_MS = 2500;

/**
 * The advisory verification verdicts recorded for this job's tasks.
 *
 * THE ONE LINE THIS CARD MUST NEVER LET GO UNSAID: these are observations,
 * not gates. `db.record_verification`'s own docstring says it plainly —
 * nothing in the system reads a verdict to refuse a lease, withhold a
 * credit, fail a commit or change placement. A UI that implied otherwise (a
 * red badge that reads as "this failed", a count that reads as "this many
 * tasks were rejected") would turn an advisory layer into a perceived gate,
 * which is the exact accident the docstring warns against. The advisory line
 * below states it in the product's own words, not just this file's.
 *
 * Every decision this renders — the label for a verdict, how rows are
 * grouped by task, what counts as "empty" — is taken in
 * `lib/job-verifications.ts`, where `lib/job-verifications.test.ts` proves
 * the one rule that matters most: `unknown` never becomes `pass`, and an
 * empty listing never becomes a rollup that looks like a clean sweep. This
 * file is markup and formatting.
 *
 * Fetches its own data rather than taking a `panel` prop the way
 * `RoutingCard` and `ArtifactsCard` do — nothing else on the job page needs
 * these rows, so there is no reason to grow `page.tsx`'s already-large
 * state for them. `active` is the one thing the page supplies: whether to
 * keep polling, the same `!TERMINAL.has(job.state)` gate the page uses for
 * its own poll.
 */
export function VerificationCard({
  jobId,
  active,
}: {
  jobId: string;
  active: boolean;
}) {
  const [rows, setRows] = useState<Verification[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // No synchronous `setLoading(true)` on every call — this can run from a
  // poll tick, and flashing the loading skeleton every 2.5s on a panel that
  // already has good data would be a worse experience than a silent
  // best-effort refresh. The initial mount already starts `loading: true`.
  const load = useCallback(() => {
    return listJobVerifications(jobId)
      .then((v) => {
        setRows(v);
        setError(null);
      })
      .catch((err) => {
        // A 401 is the job page's own sign-in redirect to handle — it reads
        // `getJob` on the same session — so a second bounce from here would
        // just be a duplicate.
        if (!(err instanceof NotAuthenticated)) {
          setError(
            err instanceof Error
              ? err.message
              : "Couldn't read the verification checks for this job."
          );
        }
      })
      .finally(() => setLoading(false));
  }, [jobId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!active) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [active, load]);

  const panel = resolvePanel(
    { loading, error, data: rows },
    (data) => data.length === 0
  );

  return (
    <section className="panel p-4">
      <h2 className="text-sm font-semibold">Verification</h2>
      <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted-foreground">
        Advisory checks the coordinator ran against its own evidence for each
        task &mdash; observations, never gates. A flag withheld nothing and
        refused no one: nothing here changed what ran, what was accepted, or
        what was paid.
      </p>

      <StatePanel
        state={panel}
        className="mt-4 px-0 sm:px-0"
        loadingRows={2}
        label="this job's verification checks"
        empty={{ title: "No checks recorded for this job." }}
        unreadable={{ retry: load }}
      >
        {(data) => <VerificationSummaryView rows={data} />}
      </StatePanel>
    </section>
  );
}

/** The rollup tiles plus the per-task list. Only ever rendered with at least
 * one row — `StatePanel`'s `empty` slot handles the zero-row case above, so
 * `summariseVerifications(rows).state` is always `"present"` here. Called
 * with the raw API rows rather than a pre-summarised panel so the transform
 * stays the single place that decision is made. */
function VerificationSummaryView({ rows }: { rows: Verification[] }) {
  const panel = summariseVerifications(rows);

  return (
    <div>
      <dl className="grid grid-cols-3 gap-x-6 gap-y-2">
        <StatTile
          variant="bare"
          size="sm"
          label="Passed"
          value={panel.rollup.pass}
        />
        <StatTile
          variant="bare"
          size="sm"
          label="Flagged"
          value={panel.rollup.flag}
        />
        <StatTile
          variant="bare"
          size="sm"
          label="Could not tell"
          value={panel.rollup.unknown}
        />
      </dl>

      <ul className="mt-4 divide-y divide-border">
        {panel.tasks.map((task) => (
          <TaskGroupRow key={task.taskId} task={task} />
        ))}
      </ul>
    </div>
  );
}

function TaskGroupRow({ task }: { task: VerificationTaskGroup }) {
  return (
    <li className="py-3 first:pt-0 last:pb-0">
      <p className="font-mono text-xs font-medium">{task.taskId}</p>
      <div className="mt-2 space-y-2">
        {task.rows.map((row) => (
          <VerdictRow key={row.id} row={row} />
        ))}
      </div>
    </li>
  );
}

/** Neutral-to-warm, never red-for-fail: `flag` gets the same amber
 * `RoutingCard` uses for "a gap on our side", not a destructive color. A
 * verdict here is never the reason anything was refused, and a stop-sign red
 * would say otherwise at a glance, before anyone reads a word of copy. */
const VERDICT_STYLES: Record<VerificationVerdict, string> = {
  pass: "border-evergreen/40 text-evergreen",
  flag: "border-warning/50 text-warning-foreground",
  unknown: "border-muted text-muted-foreground",
};

function VerdictIcon({ verdict }: { verdict: VerificationVerdict }) {
  const className = "mr-0.5 inline h-3 w-3 align-[-1px]";
  if (verdict === "pass") {
    return <CheckCircle className={className} weight="fill" />;
  }
  if (verdict === "flag") {
    return <Flag className={className} weight="fill" />;
  }
  // "unknown" — could not tell, said with a question mark rather than a
  // warning glyph. A verdict nobody could settle is not itself a problem.
  return <Question className={className} weight="fill" />;
}

function VerdictRow({ row }: { row: VerificationRow }) {
  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="flex items-center gap-2">
          <span className="font-mono text-xs text-muted-foreground">
            {row.slice}
          </span>
          <span
            className={`rounded-md border px-1.5 py-0.5 font-mono text-[11px] ${VERDICT_STYLES[row.verdict]}`}
          >
            <VerdictIcon verdict={row.verdict} /> {row.label}
          </span>
        </span>
        <span className="font-mono text-[11px] text-muted-foreground">
          {row.machineId ?? "machine not yet resolved"} · {time(row.createdAt)}
        </span>
      </div>
      {row.detail && <DetailLine detail={row.detail} />}
    </div>
  );
}

/** The slice's own evidence, verbatim — never summarised or paraphrased.
 * Rendered as `key: value` pairs rather than raw JSON, which is unreadable
 * at 10px, but every value comes straight from the object the API sent. */
function DetailLine({ detail }: { detail: Record<string, unknown> }) {
  const entries = Object.entries(detail);
  if (entries.length === 0) return null;
  return (
    <p className="mt-1 max-w-prose break-all font-mono text-[10px] text-muted-foreground">
      {entries.map(([k, v]) => `${k}: ${formatDetailValue(v)}`).join(" · ")}
    </p>
  );
}

function formatDetailValue(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Same clock format the rest of this page uses for a coordinator
 * timestamp. Returns an em dash for a value that is unparseable rather than
 * "Invalid Date". */
function time(iso: string): string {
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? "—" : new Date(ms).toLocaleTimeString();
}
