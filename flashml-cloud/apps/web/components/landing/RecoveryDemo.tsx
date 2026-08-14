"use client";

import { useRef, useState } from "react";
import { useGSAP } from "@gsap/react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { EventLedger } from "@/components/landing/EventLedger";
import { RecoveryStack } from "@/components/landing/RecoveryStack";
import { SectionReveal } from "@/components/landing/motion/SectionReveal";
import { useLandingMotion } from "@/components/landing/motion/LandingMotionProvider";
import { CountUp } from "@/components/motion/CountUp";
import { SECTION_REVEAL_TRAVEL_PX } from "@/lib/motion/timing";
import { SAMPLE_LEDGER, type LedgerEventType } from "@/lib/landing/sample-ledger";

gsap.registerPlugin(useGSAP, ScrollTrigger);

const RECOVERY_EVENT_TYPES = new Set<LedgerEventType>([
  "LEASE_CLAIMED",
  "CHECKPOINT_MANIFEST_COMMITTED",
  "NODE_HEARTBEAT_LOST",
  "TASK_REQUEUED",
  "TASK_COMMIT_ACCEPTED",
]);

const RECOVERY_EVENTS = SAMPLE_LEDGER.filter((event) => RECOVERY_EVENT_TYPES.has(event.type)).filter(
  (event, index, events) =>
    event.type === "TASK_COMMIT_ACCEPTED"
      ? events.map((candidate) => candidate.type).lastIndexOf(event.type) === index
      : events.findIndex((candidate) => candidate.type === event.type) === index,
);

/** The pinned scrub's beat order, spelled out rather than derived from
 * `RECOVERY_EVENTS` at runtime — the mapping is easier to check against the
 * PROOF_AT table below when both are literal arrays in the same file.
 * `RECOVERY_EVENTS`' own filter above resolves to exactly these five types
 * in exactly this order (each type appears once; `TASK_COMMIT_ACCEPTED`
 * keeps its LAST occurrence in `SAMPLE_LEDGER`, which still sorts after
 * `NODE_HEARTBEAT_LOST` and `TASK_REQUEUED`) — if that ledger is ever
 * edited, `data-ledger-row` lookups below fail closed (the scrub just does
 * not engage, see `ledgerRows.length !== BEAT_ORDER.length`) rather than
 * silently driving the wrong rows. */
const BEAT_ORDER: LedgerEventType[] = [
  "LEASE_CLAIMED",
  "CHECKPOINT_MANIFEST_COMMITTED",
  "NODE_HEARTBEAT_LOST",
  "TASK_REQUEUED",
  "TASK_COMMIT_ACCEPTED",
];

const RECOVERY_PROOF = [
  "RTX 4090 machine destroyed",
  "Resumed on an RTX 3090",
  "58 epochs preserved",
] as const;

/** Opacity for a ledger/proof row that has already had its moment. Not 0 —
 * spec: "earlier events dim... so focus tracks the newest", not disappear. */
const DIM_OPACITY = 0.55;

/** Which proof-stack row (0-indexed) lights up as which `BEAT_ORDER` index
 * arrives, per the approved design: "01 destroyed" during heartbeat-lost,
 * "02 resumed" during task-requeued, "03 preserved" during task-commit-
 * accepted. `RECOVERY_PROOF`'s own three rows read in that order already. */
const PROOF_AT: ReadonlyArray<readonly [beatIndex: number, proofIndex: number]> = [
  [2, 0],
  [3, 1],
  [4, 2],
];

// COUNTS OF THINGS THAT HAPPENED, not performance comparisons — and the
// distinction is why these four and not the previous four.
//
// The set this replaced led with "47% faster batch completion". That number
// is the `lpt` run in `flashruntime/benchmarks/results/` — 4 repeats, the
// second-best of seven runs of the same comparison. The full set:
// -189.9% / +21.5% / +29.1% / +48.3% / +37.5% / +47.5% / +42.6%. Both
// ten-repeat runs (the only ones with enough samples to mean anything) say
// +42.6% and +37.5%, and one run has the pooled arm nearly THREE TIMES
// SLOWER. That benchmark is on record as not reproducing, with a standing
// decision to publish nothing from it.
//
// "3.7x worker speed range" came from the same cherry-picked run; the others
// range 3.164 to 4.121. "24 attacks blocked" and "<0.25% host memory
// overhead" have no source anywhere in either repository.
//
// A judge who asks "over how many repeats?" ends that conversation. These
// four are countable and were counted.
const EVIDENCE = [
  ["30", "production attempts", "Recorded across the first two contributing hosts."],
  ["2", "proven architectures", "macOS arm64 and Linux x86_64."],
  ["5", "steps lost, not 35", "Recovered from the last verified checkpoint."],
  ["1", "accepted result per task", "Idempotent commits reject duplicate outcomes."],
] as const;

/**
 * `#recover`'s pinned scroll-scrub — the landing's centerpiece.
 *
 * WHY THIS TARGETS WHAT'S ALREADY HERE. The section already contains the
 * whole sequential story in real DOM: `RECOVERY_EVENTS` (five protocol
 * events, `EventLedger`) and `RECOVERY_PROOF` (three numbered stats,
 * `RecoveryStack`). No new scenes, no resurrecting the workflow-scene
 * feature the earlier design cut — this choreographs what is already
 * rendered, driving the SAME `[data-ledger-row]` / `[data-recovery-row]`
 * elements `EventLedger`/`RecoveryStack` already produce rather than
 * building a parallel copy of the markup.
 *
 * WHY `RecoveryStack` AND `SectionReveal` BOTH GET `active={!pinnable}`.
 * Both already run their own scroll-triggered entrance. Leaving them on
 * while this component ALSO drives the same elements would animate every
 * row twice — once from each timeline. `active={false}` makes each of them
 * a pure no-op (see the prop's doc comment on `SectionReveal`), so this
 * component's `useGSAP` is the ONLY thing that ever touches
 * `[data-ledger-row]` / `[data-recovery-row]` on the pinned path, and nothing
 * touches them on the fallback path except those two components' own
 * existing, unchanged behaviour.
 *
 * PROGRESS 0 SHOWS ONLY THE FIRST EVENT, ON PURPOSE. The five beats are not
 * five equal reveal-from-zero moments: `LEASE_CLAIMED` is the story's
 * baseline (already visible the instant the pin engages, matching what a
 * reader scrolling back to progress 0 must find), and the remaining four
 * ledger events divide the full 0-1 scrub range into four equal transitions
 * (`STEPS = 4`). `PROOF_AT` keys off `BEAT_ORDER`'s index — 2, 3, 4 — which
 * lines up with those same four transitions, not a separate count.
 */
export function RecoveryDemo() {
  const { reduced, desktop, finePointer } = useLandingMotion();
  const pinnable = desktop && finePointer && !reduced;

  const sectionRef = useRef<HTMLElement>(null);
  const storyRef = useRef<HTMLDivElement>(null);
  const [storyComplete, setStoryComplete] = useState(false);

  useGSAP(
    () => {
      const section = sectionRef.current;
      const story = storyRef.current;
      if (!section || !story || !pinnable) return;

      const ledgerRows = BEAT_ORDER.map((type) =>
        story.querySelector<HTMLElement>(`[data-ledger-row="${type}"]`),
      ).filter((row): row is HTMLElement => row !== null);
      const heartbeatDot = story.querySelector<HTMLElement>("[data-ledger-dot]");
      const proofRows = gsap.utils.toArray<HTMLElement>(
        story.querySelectorAll("[data-recovery-row]"),
      );

      // Fails closed: if the markup this queries for ever drifts from
      // `BEAT_ORDER`, skip the scrub rather than half-drive it — the
      // section still renders its plain static layout, which is always a
      // correct (if unanimated) result.
      if (ledgerRows.length !== BEAT_ORDER.length || proofRows.length !== RECOVERY_PROOF.length) {
        return;
      }

      const STEPS = ledgerRows.length - 1;

      // Baseline for progress 0 — see the file doc comment.
      gsap.set(ledgerRows[0], { opacity: 1, y: 0 });
      gsap.set(ledgerRows.slice(1), { opacity: 0, y: SECTION_REVEAL_TRAVEL_PX });
      gsap.set(proofRows, { opacity: DIM_OPACITY });

      // Declared before the `scrollTrigger.onUpdate` closure below that
      // reads and writes it, even though closures capture by reference and
      // JS would not technically require the ordering — this way the read
      // order on the page matches the read order at runtime.
      let heartbeatFired = false;

      const tl = gsap.timeline({
        scrollTrigger: {
          trigger: section,
          start: "top top",
          // A function, not a bare "+=250%" string: percentages in
          // ScrollTrigger's shorthand resolve against the TRIGGER element's
          // own height, and this section's natural height is not what
          // "~2.5 viewport heights" means here. A function is re-evaluated
          // on `ScrollTrigger.refresh()` (window resize included), so this
          // also stays exactly 2.5 viewports on an orientation change.
          end: () => `+=${window.innerHeight * 2.5}`,
          scrub: 0.5,
          pin: true,
          anticipatePin: 1,
          onUpdate: (self) => {
            // The halo pulse: once per FORWARD crossing into the
            // heartbeat-lost beat's completion, never looping. This is
            // deliberately NOT a tween inside the scrubbed timeline above —
            // a scrub-tied box-shadow would smear across whatever range of
            // scroll the reader dwells in, not "pulse". `heartbeatFired`
            // resets when the reader scrolls back above the row, so
            // crossing forward into it again re-triggers it, matching
            // "each forward crossing" rather than "the first one ever".
            const heartbeatBeatProgress = 2 / STEPS;
            if (self.direction > 0 && self.progress >= heartbeatBeatProgress) {
              if (!heartbeatFired && heartbeatDot) {
                heartbeatFired = true;
                // Same box-shadow grammar `.status-dot[data-state="live"]`'s
                // `halo` keyframe already draws in `app/globals.css` — amber
                // here (`--z-warning: #d5a33f` = rgb(213 163 63)), not the
                // CSS rule's green, because this row is a loss, not a live
                // machine. Literal rgb(), not `var(--z-warning)`: GSAP's
                // box-shadow tween interpolates the colour channels
                // numerically and cannot decompose a CSS custom property to
                // do that.
                gsap.fromTo(
                  heartbeatDot,
                  { boxShadow: "0 0 0 0 rgb(213 163 63 / 0.45)" },
                  {
                    boxShadow: "0 0 0 8px rgb(213 163 63 / 0)",
                    duration: 0.6,
                    ease: "power2.out",
                    overwrite: true,
                  },
                );
              }
            } else if (self.progress < heartbeatBeatProgress) {
              heartbeatFired = false;
            }

            // Story-complete: the signal `EVIDENCE`'s CountUp numerals wait
            // for below, instead of their own independent viewport-entry
            // check — see the doc comment where it is consumed. Setting the
            // same boolean repeatedly while already `true` is a React no-op
            // (bails out of re-rendering), so this costs nothing on every
            // scrub tick after the first time it fires.
            if (self.progress >= 1) setStoryComplete(true);
          },
        },
      });

      for (let i = 1; i < ledgerRows.length; i += 1) {
        const t = (i - 1) / STEPS;
        const duration = 1 / STEPS;
        tl.to(ledgerRows[i], { opacity: 1, y: 0, ease: "none", duration }, t);
        tl.to(ledgerRows[i - 1], { opacity: DIM_OPACITY, ease: "none", duration }, t);
      }

      for (const [beatIndex, proofIndex] of PROOF_AT) {
        const t = (beatIndex - 1) / STEPS;
        const duration = 1 / STEPS;
        tl.to(proofRows[proofIndex], { opacity: 1, ease: "none", duration }, t);
        if (proofIndex > 0) {
          tl.to(proofRows[proofIndex - 1], { opacity: DIM_OPACITY, ease: "none", duration }, t);
        }
      }

      // The final convergence: every row, ledger and proof alike, lands at
      // full opacity exactly as the plain static layout renders — spec:
      // "a reader who scrolls fast or lands mid-page must never find a
      // half-lit section". Sharing the LAST transition's own window
      // (rather than an instant sliver tacked on at t=1) keeps the
      // timeline's total duration at exactly 1, so `scrub`'s smoothing has
      // the same runway to catch up here as everywhere else; `overwrite:
      // "auto"` plus being added LAST guarantees this wins the two rows
      // that final transition's own "dim predecessor" tweens (above) would
      // otherwise leave at `DIM_OPACITY` when the scrub stops exactly at
      // progress 1.
      const finalWindow = 1 - 1 / STEPS;
      tl.to(
        [...ledgerRows, ...proofRows],
        { opacity: 1, duration: 1 / STEPS, ease: "none", overwrite: "auto" },
        finalWindow,
      );
    },
    { scope: sectionRef, dependencies: [pinnable], revertOnUpdate: true },
  );

  return (
    <section
      ref={sectionRef}
      id="recover"
      data-surface="light"
      className="landing-surface-light scroll-mt-20 border-b border-border py-20 md:py-28"
    >
      <div className="mx-auto max-w-[1240px] px-5 sm:px-6">
        {/* `active={!pinnable}`: on the fallback path (mobile, coarse
            pointer, reduced motion) this runs its normal scroll-triggered
            fade-up, unchanged. On the pinned path this is a no-op — the
            `useGSAP` above owns every row's starting state and transition
            instead, and the headline/copy in here that the pin never
            touches simply render at their plain CSS visibility throughout,
            which is exactly "stay static through the pin". */}
        <SectionReveal active={!pinnable}>
          <div
            ref={storyRef}
            data-reveal-content
            className="grid gap-12 lg:grid-cols-[.85fr_1.15fr] lg:items-center lg:gap-20"
          >
            <div>
              <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
                Recovery proof
              </p>
              <h2 className="mt-5 max-w-2xl text-[clamp(2.65rem,5.4vw,4.75rem)] font-semibold leading-[0.99] tracking-[-0.052em]">
                Machines disappear. <span className="text-muted-foreground">Progress doesn&apos;t.</span>
              </h2>
              <p className="mt-6 max-w-[54ch] leading-relaxed text-muted-foreground">
                When capacity vanishes, Zolli resumes supported work from recorded
                progress on another compatible machine.
              </p>
              <RecoveryStack items={RECOVERY_PROOF} active={!pinnable} />
              <div className="mt-8 grid grid-cols-[auto_1fr] gap-x-4">
                <span className="font-mono text-[10px] text-warning-foreground">01:28</span>
                <p className="text-sm leading-relaxed text-foreground">
                  The ledger records the exact protocol events behind the handoff,
                  from heartbeat loss through the accepted replacement commit.
                </p>
              </div>
            </div>

            <div>
              <div
                data-surface="dark"
                className="landing-surface-dark overflow-hidden rounded-[10px] border border-white/14 bg-[var(--landing-graphite)]"
              >
                <EventLedger events={RECOVERY_EVENTS} label="sample data" />
              </div>
              <p className="mt-4 max-w-[58ch] text-xs leading-relaxed text-muted-foreground">
                Every protocol name above is a real member of the runtime&apos;s event ledger. The displayed values are sample data until a captured run replaces them.
              </p>
            </div>
          </div>
        </SectionReveal>

        {/* Always active — EVIDENCE is not part of the pinned frame (it sits
            below it in normal document flow, physically unreachable until
            the pin releases), so it keeps its own ordinary scroll-triggered
            entrance regardless of `pinnable`. Only the CountUp numerals'
            OWN trigger mechanism changes below. */}
        <SectionReveal>
          <div data-reveal-content className="mt-14 border-t border-[var(--z-border-strong)] pt-10 md:mt-20 md:pt-14">
            <div className="grid gap-x-8 gap-y-10 sm:grid-cols-2 lg:grid-cols-4 lg:gap-10">
              {EVIDENCE.map(([value, label, detail]) => (
                <article key={label} className="min-w-0">
                  <p
                    data-evidence-value={value}
                    className="font-mono text-6xl font-medium leading-none tracking-[-0.08em] sm:text-7xl"
                  >
                    {/* These four figures are the curated, documented values
                        in the comment above (`EVIDENCE`), not an API read,
                        but "static value already rendered" is exactly what
                        `CountUp` asks for: it never invents a number, it
                        only animates one that is already correct.

                        On the pinned path the count-up is deliberately NOT
                        triggered by its own independent viewport-entry
                        check: `#recover`'s pin physically blocks EVIDENCE
                        from becoming visible before the scrub completes (it
                        is the next thing in document flow after the pinned
                        spacer), so "in-view" would fire at essentially the
                        same moment regardless — but keying the remount off
                        `storyComplete` makes that synchrony an explicit
                        contract instead of a layout coincidence, and it is
                        what lets the count-up replay in step with the
                        story's last beat landing rather than whatever a
                        raw IntersectionObserver threshold happens to catch.
                        `key` forces the remount: `trigger="mount"` fires
                        immediately on mount, so flipping the key from
                        "default" to "beat" the instant `storyComplete`
                        becomes true is what makes it fire then and not
                        before. Off the pinned path (mobile, reduced motion)
                        `storyComplete` never becomes true, so this always
                        stays "default" / `in-view` — entry-triggered,
                        unchanged. */}
                    <CountUp
                      key={pinnable && storyComplete ? "beat" : "default"}
                      value={Number(value)}
                      reduced={reduced}
                      trigger={pinnable && storyComplete ? "mount" : "in-view"}
                    />
                  </p>
                  <h3 className="mt-5 font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-brand-foreground">
                    {label}
                  </h3>
                  <p className="mt-3 max-w-[27ch] text-sm leading-relaxed text-muted-foreground">{detail}</p>
                </article>
              ))}
            </div>
          </div>
        </SectionReveal>
      </div>
    </section>
  );
}
