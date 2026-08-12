"use client";

import { useEffect, useMemo, useState } from "react";
import {
  CheckCircle,
  Info,
  Pause,
  ShieldCheck,
  Warning,
  XCircle,
} from "@phosphor-icons/react";
import { Badge } from "@/components/ui/badge";
import {
  BOUNDARY_NOTE,
  HIBERNATION_COST_NOTE,
  NOT_OBSERVED,
  formatClock,
  summariseSandboxSession,
  type Identifier,
  type Measurement,
  type SandboxEvent,
  type SandboxSession,
  type SandboxSessionState,
  type SandboxStep,
  type SandboxVisibility,
} from "@/lib/sandbox-session";

/**
 * One evidence view of one Alibaba FC Sandbox evaluation session.
 *
 * The requirement it is built against is a stopwatch: a judge must find
 * execute → wait → hibernate → external event → wake → continue → accepted
 * output → cleanup on ONE screen in under fifteen seconds (D-2/D-4). So the
 * eight words are the timeline's own row headings, in that order, always all
 * eight — a step nobody has observed yet is drawn as an unobserved row rather
 * than omitted, because a list that silently shortens tells the reader
 * nothing about where the run has got to.
 *
 * Three display rules, none of them negotiable:
 *
 *  1. **`not observed`, never `0`, never `—`.** Every number on this screen
 *     is a claim about somebody else's infrastructure. `metrics.py` makes the
 *     same rule for the same reason: 0 is an answer and absence is not, and a
 *     dash is a shrug that a reader will fill in with whichever meaning
 *     flatters us.
 *  2. **Measured and estimated look different.** A latency the observer timed
 *     around its own call is a stronger claim than the gap between two
 *     timestamps, and the difference is drawn, not explained in a caption.
 *  3. **The boundary note is always on screen.** Training retry and sandbox
 *     hibernation are separate guarantees; this view must never let itself be
 *     read as "the sandbox rescued the training job".
 *
 * Every identifier arrives already narrowed by `summariseSandboxSession` —
 * this component never receives a full sandbox id, marker hash or job id on
 * the public path, so it cannot print one by accident. That is deliberate:
 * the redaction is a property of the data, not of the care taken in JSX.
 */
export function SandboxLifecycle({
  session,
  events,
  now,
  visibility = "owner",
  defaultPresenter = false,
}: {
  session: SandboxSession;
  events: readonly SandboxEvent[] | null | undefined;
  /**
   * The clock, supplied by the caller. Required, and not defaulted to
   * `Date.now()` inside: this component server-renders on the public share
   * page and then hydrates in a browser whose clock is a second or two along,
   * and a running "still hibernated" timer that differs between those two
   * renders is a hydration mismatch. The caller passes one instant, both
   * renders agree, and the effect below takes over afterwards.
   */
  now: number;
  visibility?: SandboxVisibility;
  defaultPresenter?: boolean;
}) {
  const [presenter, setPresenter] = useState(defaultPresenter);
  const [clock, setClock] = useState(now);

  const summary = useMemo(
    () => summariseSandboxSession(session, events, { now: clock, visibility }),
    [session, events, clock, visibility]
  );

  // Only while something can still change, and only once a second: the number
  // that moves is the hibernation timer, which is the point of the screen
  // during a live demo and pure noise once the session is over.
  const ticking = summary.live;
  useEffect(() => {
    if (!ticking) return;
    const t = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(t);
  }, [ticking]);

  return (
    <section
      data-testid="sandbox-lifecycle"
      data-presenter={presenter ? "on" : "off"}
      data-visibility={visibility}
      className="panel p-4"
    >
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className={`font-semibold ${presenter ? "text-base" : "text-sm"}`}>
            {providerLabel(summary.provider)} · evaluation session
          </h2>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="font-mono text-[11px]">
              {summary.region}
            </Badge>
            <StateChip state={summary.observedState} presenter={presenter} />
            {!presenter && (
              <span className="font-mono text-[11px] text-muted-foreground">
                observed from the session ledger
              </span>
            )}
          </div>
        </div>

        <button
          type="button"
          onClick={() => setPresenter((p) => !p)}
          aria-pressed={presenter}
          className="shrink-0 rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground"
        >
          {presenter ? "Exit presenter mode" : "Presenter mode"}
        </button>
      </header>

      {!summary.stateAgrees && (
        <p className="mt-3 flex items-start gap-2 rounded-md border border-warning/40 px-3 py-2 text-xs text-warning-foreground">
          <Warning className="mt-px h-3.5 w-3.5 shrink-0" weight="fill" />
          <span>
            The ledger&apos;s last observed state is{" "}
            <span className="font-mono">{summary.observedState}</span>; the
            session row records{" "}
            <span className="font-mono">{summary.recordedState}</span>. This
            view reports what was observed.
          </span>
        </p>
      )}

      <Identity summary={summary} presenter={presenter} />

      <Cost
        hibernated={summary.hibernated}
        avoided={summary.activeComputeAvoided}
        presenter={presenter}
      />

      <ol
        className={`grid gap-px overflow-hidden rounded-md border border-border bg-border ${
          presenter ? "mt-3 lg:grid-cols-2" : "mt-4"
        }`}
      >
        {summary.steps.map((step) => (
          <StepRow key={step.id} step={step} presenter={presenter} />
        ))}
      </ol>

      <div
        className={`grid gap-3 sm:grid-cols-2 ${presenter ? "mt-3" : "mt-4"}`}
      >
        <MarkerCard marker={summary.marker} presenter={presenter} />
        <CleanupCard cleanup={summary.cleanup} presenter={presenter} />
      </div>

      {summary.errorCode && (
        <div className="mt-4 rounded-md border border-destructive/30 px-3 py-2.5">
          <div className="label-caps text-destructive">Failure</div>
          <p className="mt-1 font-mono text-xs text-destructive">
            {summary.errorCode}
          </p>
          {/* Owner only, and sanitized upstream before it ever reached us. A
              stranger holding a share link gets the code and nothing else. */}
          {summary.errorMessage && (
            <p className="mt-1 font-mono text-[11px] leading-relaxed text-destructive/80">
              {summary.errorMessage}
            </p>
          )}
        </div>
      )}

      {/* Rule 3. Always rendered, in both modes, at a size that is read
          rather than skipped. */}
      <p
        className={`flex items-start gap-2 rounded-md border border-dashed border-border px-3 leading-relaxed text-muted-foreground ${
          presenter ? "mt-3 py-1.5 text-xs" : "mt-4 py-2.5 text-xs"
        }`}
      >
        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" weight="fill" />
        <span data-testid="boundary-note">{BOUNDARY_NOTE}</span>
      </p>

      {/* The raw ledger, collapsed by default and dropped entirely in
          presenter mode — it is the proof behind every row above, not
          something to read at 1280×720 from the back of a room. */}
      {!presenter && <RawLedger events={summary.events} />}
    </section>
  );
}

function providerLabel(provider: string): string {
  return provider === "alibaba-fc-sandbox" ? "Alibaba FC Sandbox" : provider;
}

const STATE_TONE: Record<SandboxSessionState, string> = {
  REQUESTED: "text-muted-foreground border-muted",
  ACTIVE: "text-brand-foreground border-brand/40",
  PREPARED: "text-brand-foreground border-brand/40",
  HIBERNATED: "text-warning-foreground border-warning/50",
  RESUMING: "text-brand-foreground border-brand/40",
  EVALUATING: "text-brand-foreground border-brand/40",
  SUCCEEDED: "text-evergreen border-evergreen/40",
  FAILED: "text-destructive border-destructive/40",
  TERMINATED: "text-muted-foreground border-muted",
};

function StateChip({
  state,
  presenter,
}: {
  state: SandboxSessionState | null;
  presenter: boolean;
}) {
  // No events at all: the state is genuinely unobserved, and the same rule
  // applies to it as to every number on this screen.
  if (state === null) {
    return (
      <span className="rounded-full border border-dashed border-border px-2 py-0.5 font-mono text-[11px] italic text-muted-foreground">
        {NOT_OBSERVED}
      </span>
    );
  }
  return (
    <Badge
      variant="outline"
      className={`font-mono ${presenter ? "text-sm" : "text-xs"} ${STATE_TONE[state] ?? "text-muted-foreground border-muted"}`}
    >
      {state}
    </Badge>
  );
}

function Identity({
  summary,
  presenter,
}: {
  summary: ReturnType<typeof summariseSandboxSession>;
  presenter: boolean;
}) {
  const template: Identifier = summary.template
    ? { state: "shown", display: summary.template.display }
    : { state: "not-observed", display: NOT_OBSERVED };

  const rows: [string, Identifier][] = [
    ["sandbox id", summary.sandboxId],
    [summary.template?.label ?? "template", template],
    ["training job", summary.trainingJob],
    ["evaluation job", summary.evaluation.job],
  ];

  // Presenter mode lays the same four facts on one line instead of four
  // stacked cells. Nothing is dropped — a 720px budget is spent on the
  // lifecycle, not on the label above each id.
  if (presenter) {
    return (
      <dl className="mt-2 flex flex-wrap items-baseline gap-x-5 gap-y-1">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-baseline gap-1.5">
            <dt className="label-caps">{label}</dt>
            <dd
              className={`font-mono text-xs ${
                value.state === "shown" ? "" : "italic text-muted-foreground"
              }`}
            >
              {value.display}
            </dd>
          </div>
        ))}
      </dl>
    );
  }

  return (
    <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt className="label-caps">{label}</dt>
          <dd
            className={`mt-0.5 truncate font-mono text-xs ${
              value.state === "shown" ? "" : "italic text-muted-foreground"
            }`}
          >
            {value.display}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * The cost story, and the two ways it can mislead.
 *
 * The hibernated duration and the compute avoided are the SAME interval, said
 * twice. Two big numbers side by side imply two independent measurements, so
 * the second tile says outright that it is the first one re-framed.
 *
 * And "active compute avoided" is not "money saved": a deep-hibernated
 * sandbox still bills a memory-and-disk snapshot. That caveat sits next to
 * the number rather than in a footnote, because the number is what gets
 * screenshotted.
 */
function Cost({
  hibernated,
  avoided,
  presenter,
}: {
  hibernated: Measurement;
  avoided: Measurement;
  presenter: boolean;
}) {
  const ongoing = hibernated.ongoing && (
    <span className="font-mono text-[11px] text-warning-foreground">
      still hibernated — counting
    </span>
  );

  // One line in presenter mode, and the caveat stays on it. The number is
  // what gets photographed; the sentence that stops it being read as "money
  // saved" has to be in the same photograph.
  if (presenter) {
    return (
      <div className="mt-2 flex flex-wrap items-baseline gap-x-5 gap-y-1 rounded-md border border-border px-3 py-2">
        <span className="flex items-baseline gap-2">
          <span className="label-caps">Time hibernated</span>
          <MeasurementValue m={hibernated} big />
          {ongoing}
        </span>
        <span className="flex items-baseline gap-2">
          <span className="label-caps">Active compute avoided</span>
          <MeasurementValue m={avoided} big />
          <span className="font-mono text-[11px] text-muted-foreground">
            the same interval, re-framed
          </span>
        </span>
        <span className="text-[11px] leading-relaxed text-muted-foreground">
          {HIBERNATION_COST_NOTE}
        </span>
      </div>
    );
  }

  return (
    <div className="mt-4 grid gap-3 sm:grid-cols-2">
      <div className="rounded-md border border-border px-4 py-3">
        <div className="label-caps">Time hibernated</div>
        <div className="mt-1 flex flex-wrap items-baseline gap-2">
          <MeasurementValue m={hibernated} big />
          {ongoing}
        </div>
      </div>
      <div className="rounded-md border border-border px-4 py-3">
        <div className="label-caps">Active compute avoided</div>
        <div className="mt-1 flex flex-wrap items-baseline gap-2">
          <MeasurementValue m={avoided} big />
          <span className="font-mono text-[11px] text-muted-foreground">
            the same interval, re-framed
          </span>
        </div>
        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
          {HIBERNATION_COST_NOTE}
        </p>
      </div>
    </div>
  );
}

/** Rule 1 and rule 2, in one place.
 *
 * An unobserved measurement is a sentence in muted italics — visibly not a
 * number, so it cannot be misread as a small one. An observed measurement
 * carries a chip naming its basis: a solid chip for something the observer
 * timed around its own call, a dashed one for a difference between two
 * timestamps. The dashed border is the same visual grammar this design system
 * already uses for "not filled in". */
function MeasurementValue({ m, big = false }: { m: Measurement; big?: boolean }) {
  if (!m.observed) {
    return (
      <span
        data-unobserved="true"
        className="font-mono text-xs italic text-muted-foreground"
      >
        {NOT_OBSERVED}
      </span>
    );
  }
  return (
    <span className="flex flex-wrap items-baseline gap-1.5">
      <span className={`metric-value ${big ? "text-2xl" : "text-sm"}`}>
        {m.display}
      </span>
      <span
        data-basis={m.basis}
        title={
          m.basis === "measured"
            ? "Timed by the observer around its own call."
            : "Computed from two observed timestamps — includes anything that sat between them."
        }
        className={`rounded-full px-1.5 py-0.5 font-mono text-[10px] ${
          m.basis === "measured"
            ? "border border-evergreen/40 bg-evergreen/10 text-evergreen"
            : "border border-dashed border-border text-muted-foreground"
        }`}
      >
        {m.basis}
      </span>
    </span>
  );
}

function StepRow({ step, presenter }: { step: SandboxStep; presenter: boolean }) {
  // The evidence trail: the one fact this row turns on, and the event type
  // that proves it. Built once and placed differently by mode — never
  // omitted.
  const evidence = (
    <>
      {step.note && <span className="font-mono text-[11px]">{step.note}</span>}
      {step.evidence ? (
        <span className="font-mono text-[10px] text-muted-foreground">
          {step.evidence}
          {step.source ? ` · ${step.source}` : ""}
        </span>
      ) : (
        <span className="font-mono text-[10px] italic text-muted-foreground">
          no event recorded this step
        </span>
      )}
    </>
  );

  return (
    <li
      data-step={step.id}
      data-observed={step.observed ? "true" : "false"}
      className={`bg-surface px-3 ${presenter ? "py-1.5" : "py-2.5"} ${
        step.observed ? "" : "opacity-70"
      }`}
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        {step.observed ? (
          <CheckCircle
            className="h-3.5 w-3.5 shrink-0 text-evergreen"
            weight="fill"
          />
        ) : (
          <span className="h-3.5 w-3.5 shrink-0 rounded-full border border-dashed border-border" />
        )}
        <span
          className={`label-caps shrink-0 ${step.observed ? "text-foreground" : ""}`}
        >
          {step.keyword}
        </span>
        <span className={presenter ? "text-sm" : "text-xs"}>{step.label}</span>
        {/* Presenter mode folds the evidence onto the step's own line. Eight
            two-line rows do not fit in 720px and the fold is what buys the
            room — but the evidence NEVER folds away, because "which event
            proves this" is the answer to the only question worth asking about
            this screen. */}
        {presenter && evidence}
        <span className="ml-auto flex flex-wrap items-baseline justify-end gap-x-3 gap-y-1">
          {/* An unobserved step has no latency by definition, so its
              duration cell is dropped rather than printed as a second
              `not observed` beside the timestamp's. Two of them in a row
              read as two separate failures. */}
          {step.observed && step.measurement && (
            <MeasurementValue m={step.measurement} />
          )}
          <span
            className={`font-mono text-[11px] tabular-nums ${
              step.observed ? "text-muted-foreground" : "italic text-muted-foreground"
            }`}
          >
            {step.atDisplay}
          </span>
        </span>
      </div>
      {!presenter && (
        <div className="mt-0.5 flex flex-wrap items-baseline gap-x-2 pl-6">
          {evidence}
          <span className="text-[11px] leading-relaxed text-muted-foreground">
            {step.detail}
          </span>
        </div>
      )}
    </li>
  );
}

function MarkerCard({
  marker,
  presenter,
}: {
  marker: ReturnType<typeof summariseSandboxSession>["marker"];
  presenter: boolean;
}) {
  const tone = !marker.observed
    ? "border-border"
    : marker.matched
      ? "border-evergreen/40"
      : "border-destructive/40";

  return (
    <div className={`rounded-md border px-4 py-3 ${tone}`}>
      <div className="label-caps">Marker continuity</div>
      <p
        className={`mt-1 flex items-start gap-1.5 ${presenter ? "text-sm" : "text-xs"} ${
          !marker.observed
            ? "italic text-muted-foreground"
            : marker.matched
              ? "text-evergreen"
              : "text-destructive"
        }`}
      >
        {marker.observed ? (
          marker.matched ? (
            <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" weight="fill" />
          ) : (
            <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" weight="fill" />
          )
        ) : (
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" weight="fill" />
        )}
        <span>{marker.display}</span>
      </p>
      <div className="mt-1.5 flex flex-wrap items-baseline gap-x-2">
        <span className="label-caps">sha256</span>
        <span
          className={`font-mono text-[11px] ${
            marker.digest.state === "shown" ? "" : "italic text-muted-foreground"
          }`}
        >
          {marker.digest.display}
        </span>
        {marker.evidence && (
          <span className="font-mono text-[10px] text-muted-foreground">
            {marker.evidence} · {formatClock(marker.at)}
          </span>
        )}
      </div>
    </div>
  );
}

/** Requested and observed are different sentences, and the card says which
 * one it has. "We recorded TERMINATED" is our own bookkeeping; "the provider
 * confirmed the sandbox is gone" is the one that means nothing is still
 * billing by the second. */
function CleanupCard({
  cleanup,
  presenter,
}: {
  cleanup: ReturnType<typeof summariseSandboxSession>["cleanup"];
  presenter: boolean;
}) {
  const tone = cleanup.observed
    ? "border-evergreen/40"
    : cleanup.requested
      ? "border-warning/50"
      : "border-border";

  return (
    <div className={`rounded-md border px-4 ${presenter ? "py-2" : "py-3"} ${tone}`}>
      <div className="label-caps">Cleanup</div>
      <p
        className={`mt-1 flex items-start gap-1.5 ${presenter ? "text-sm" : "text-xs"} ${
          cleanup.observed
            ? "text-evergreen"
            : cleanup.requested
              ? "text-warning-foreground"
              : "italic text-muted-foreground"
        }`}
      >
        {cleanup.observed ? (
          <CheckCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" weight="fill" />
        ) : cleanup.requested ? (
          <Pause className="mt-0.5 h-3.5 w-3.5 shrink-0" weight="fill" />
        ) : (
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" weight="fill" />
        )}
        <span>{cleanup.display}</span>
      </p>
      <div className="mt-1.5 flex flex-wrap items-baseline gap-x-2">
        <span className="label-caps">credential</span>
        <span
          className={`font-mono text-[11px] ${
            cleanup.credentialRevoked ? "" : "italic text-muted-foreground"
          }`}
        >
          {cleanup.credentialRevoked ? "revoked, observed" : NOT_OBSERVED}
        </span>
      </div>
    </div>
  );
}

/** Every event, including the ones this view does not recognise. A ledger
 * that hides what it did not understand is not a ledger — same rule the job
 * page's event ledger follows for the coordinator's own stream. */
function RawLedger({ events }: { events: readonly SandboxEvent[] }) {
  if (events.length === 0) {
    return (
      <p className="mt-4 font-mono text-[11px] italic text-muted-foreground">
        no events recorded for this session yet
      </p>
    );
  }
  return (
    <details className="mt-4">
      <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
        Session ledger ({events.length} events)
      </summary>
      <ul className="mt-2 divide-y divide-border border-t border-border">
        {events.map((e) => (
          <li
            key={e.sequence}
            className="flex flex-wrap items-baseline gap-x-3 py-1.5"
          >
            <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
              #{e.sequence}
            </span>
            <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
              {formatClock(Date.parse(e.observed_at) || null)}
            </span>
            <span className="font-mono text-[11px]">{e.type}</span>
            <span className="font-mono text-[10px] text-muted-foreground">
              {e.source}
            </span>
            {typeof e.latency_ms === "number" && (
              <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
                {Math.round(e.latency_ms)} ms
              </span>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}
