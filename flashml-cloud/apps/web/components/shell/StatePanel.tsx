"use client";

import { Warning } from "@phosphor-icons/react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { PanelState } from "@/lib/console/panel-state";
import { cn } from "@/lib/utils";

/**
 * The four-state panel: **loading / present / empty / unreadable**.
 *
 * WHAT THIS COMPONENT IS FOR. The house rule (spec §1.1) is that a failed
 * read must never render as an empty result — "could not read this" and
 * "there is nothing here" are different sentences. Today that is a rule each
 * page has to remember, and the ones that forgot render `data ?? []`, which
 * tells somebody whose network blipped that they own no machines. A rule
 * that has to be remembered nineteen times will be forgotten on the
 * twentieth page.
 *
 * So it stops being a rule and becomes a signature. `empty` and `unreadable`
 * are REQUIRED props with no defaults, and their keys are derived from
 * `PanelState`'s own discriminant (`PanelSlots` below) rather than listed —
 * so a fifth state added to the union in `lib/console/panel-state.ts` makes
 * every `<StatePanel>` in the console fail to compile until somebody decides
 * what it looks like. There is no arrangement of props that renders a panel
 * without an answer for a failed read.
 *
 * WHY `loading` IS THE ONE OPTIONAL SLOT. A skeleton is an honest default:
 * it claims only that something is arriving, which is true of every loading
 * panel there will ever be. `empty` and `unreadable` carry SENTENCES about
 * this specific subject, and there is no sentence that is true of every
 * panel. A default empty state would be a component quietly writing product
 * copy; a default `unreadable` would be this component deciding it knows why
 * a read failed. Both are the failure this file exists to prevent.
 *
 * WHERE THE UNREADABLE SENTENCE COMES FROM. `state.detail` — the API's own
 * words, carried through `panel-state.ts` verbatim. The caller supplies the
 * retry affordance, not the explanation, because the caller does not know
 * what went wrong either. A paraphrase would be more confident than the
 * evidence behind it.
 *
 * THIS COMPONENT DRAWS NO BOX. No border, no fill, no radius, per
 * `app/globals.css`: "ONE LEVEL OF BOXING, MAXIMUM… Never nest a panel
 * inside a panel." It renders inside whatever container the page already
 * has. A page whose panel is a genuinely separate object wraps this in
 * `.panel` itself — that is the one level.
 *
 * MARKUP ONLY. Every decision — what counts as empty, what beats what, when
 * a blank error still means a failure — is in `lib/console/panel-state.ts`
 * where vitest can reach it. Nothing in this file branches on anything but
 * `state.kind`.
 */

/** What a panel says when the read succeeded and there was genuinely
 * nothing. Present tense, about this panel's own subject. */
export interface EmptyCopy {
  /** One short line. "No machines yet", not "Empty". */
  title: string;
  /** What would put something here, or why there is nothing. */
  description?: React.ReactNode;
  /** The one action that would fix it, if there is one. */
  action?: React.ReactNode;
}

/** What a panel offers when the read failed.
 *
 * Note what is NOT in here: the explanation. That is `state.detail`. */
export interface UnreadableCopy {
  /** Re-run the read. Omit only when there is genuinely nothing to retry —
   * a page that has no reload path should say so in `title` rather than
   * showing a dead button. */
  retry?: () => void;
  retryLabel?: string;
  /** Overrides `UNREADABLE_TITLE`. For a panel whose subject makes the
   * fixed line ambiguous ("Could not read this machine's checkpoints"). */
  title?: string;
}

/** The fixed heading for a failed read. Deliberately a statement about US —
 * we could not read it — and never about the data, which we know nothing
 * about. */
export const UNREADABLE_TITLE = "Could not read this.";

/**
 * The inset for the three states whose markup this component owns.
 *
 * ONE value, shared, because these three states sit in the same box a moment
 * apart and any difference between them reads as the panel changing size for
 * no reason. It was three different values until a render caught it: the
 * loading state had no inset at all, so inside a `.panel` its bars ran past
 * the rounded border and hid it — the one state that looked like a different,
 * broken component rather than the same panel mid-read.
 *
 * `present` deliberately gets NONE of this. Its markup is the caller's, and
 * console tables carry their own cell padding; adding a second inset around
 * them would indent every row twice. A page whose content is full-bleed will
 * see a few pixels of horizontal shift as the skeleton resolves, which is
 * correct — a skeleton is a promise about shape, not a pixel-exact ghost of
 * what is coming.
 */
const PANEL_INSET = "px-4 sm:px-5";

/** The slot each non-`present` state needs filled. */
type SlotFor<K extends PanelState<unknown>["kind"]> = K extends "loading"
  ? React.ReactNode
  : K extends "empty"
    ? EmptyCopy
    : K extends "unreadable"
      ? UnreadableCopy
      : never;

/**
 * One slot per state that is not `present`, with the KEYS DERIVED FROM THE
 * UNION rather than written out.
 *
 * This is the mechanism that makes the contract unforgettable. Add a fifth
 * variant to `PanelState` and this mapped type gains a fifth required key
 * whose `SlotFor` resolves to `never` — so every call site stops compiling
 * until the new state is given a renderer below and a decision at each
 * panel. Listing the keys by hand here would let a new state ship rendering
 * as nothing.
 */
type PanelSlots<T> = {
  [K in Exclude<PanelState<T>["kind"], "present">]: SlotFor<K>;
};

export type StatePanelProps<T> = Omit<PanelSlots<T>, "loading"> &
  Partial<Pick<PanelSlots<T>, "loading">> & {
    /** From `resolvePanel` / `resolvePanelState` in
     * `lib/console/panel-state.ts`. Building one of these by hand is
     * allowed and is how a page with a richer read classifies itself; what
     * is not allowed is deriving `empty` from a failure. */
    state: PanelState<T>;
    /** Rendered only for `present`, and it receives the data as an
     * argument — so there is no way to render rows out of a state that does
     * not carry any. That is why the other three states have no `data`
     * field: stale rows under an error banner become impossible rather than
     * discouraged. */
    children: (data: T) => React.ReactNode;
    /** How many skeleton bars the default loading slot draws. Shape it like
     * the content it replaces; a skeleton that is the wrong size is a worse
     * promise than a spinner.
     *
     * The default is deliberately LOW. Only the caller knows how many rows
     * are coming, and the two errors are not symmetric: a skeleton that
     * promises less than arrives is a page that grows, while one that
     * promises more is a quiet claim about how much data you have, made by a
     * component that has not read any of it yet. Under-promise. */
    loadingRows?: number;
    /** What is loading, for screen readers: "your machines", "this job's
     * artifacts". Reaches the assistive layer only. */
    label?: string;
    className?: string;
  };

export function StatePanel<T>({
  state,
  loading,
  empty,
  unreadable,
  children,
  loadingRows = 2,
  label,
  className,
}: StatePanelProps<T>) {
  switch (state.kind) {
    case "loading":
      return (
        <div
          role="status"
          aria-busy="true"
          className={cn(PANEL_INSET, "py-4", className)}
        >
          {loading ?? <LoadingRows rows={loadingRows} />}
          <span className="sr-only">
            {label ? `Loading ${label}…` : "Loading…"}
          </span>
        </div>
      );

    case "empty":
      // Centred, quiet, no icon and no frame. The three local empty states
      // in the console today disagree about all three; this picks the one
      // that adds no second box, since a bordered icon tile inside a panel
      // is exactly the nesting globals.css rules out.
      //
      // A `<p>` and not an `<h2>`: heading rank depends on where the panel
      // sits, which this component cannot know, and the pages that guessed
      // `<h2>` produced an h2 under an h3. An empty state is a status, not
      // a section of the document.
      return (
        <div
          className={cn(
            "flex flex-col items-center gap-3 py-10 text-center",
            PANEL_INSET,
            className
          )}
        >
          <p className="text-sm font-medium text-foreground">{empty.title}</p>
          {empty.description ? (
            <p className="max-w-sm text-sm leading-relaxed text-muted-foreground">
              {empty.description}
            </p>
          ) : null}
          {empty.action ? <div className="mt-1">{empty.action}</div> : null}
        </div>
      );

    case "unreadable":
      // Deliberately NOT the empty state's layout. Left-aligned where empty
      // is centred, with a destructive mark where empty has none — so the
      // two are distinguishable across a room, not just on a careful read.
      // The whole rule is that they say different things; looking alike is
      // most of how they get confused.
      //
      // `role="alert"`: this appears after a read that has already failed,
      // and it is the one thing on the panel worth interrupting for.
      return (
        <div
          role="alert"
          className={cn("flex items-start gap-3 py-10", PANEL_INSET, className)}
        >
          <Warning
            className="mt-0.5 h-5 w-5 shrink-0 text-destructive"
            weight="fill"
            aria-hidden="true"
          />
          <div className="min-w-0">
            <p className="text-sm font-medium text-foreground">
              {unreadable.title ?? UNREADABLE_TITLE}
            </p>
            {/* The API's own words. Not summarised, not classified, not
                replaced by a friendlier sentence that would claim to know
                more than we do. */}
            <p className="mt-1 max-w-prose text-sm leading-relaxed text-muted-foreground">
              {state.detail}
            </p>
            {unreadable.retry ? (
              <Button
                variant="outline"
                size="sm"
                className="mt-3.5"
                onClick={unreadable.retry}
              >
                {unreadable.retryLabel ?? "Try again"}
              </Button>
            ) : null}
          </div>
        </div>
      );

    case "present":
      return <>{children(state.data)}</>;

    default: {
      // Unreachable while `PanelState` has four variants, and a compile
      // error the moment it has five — which is the point: a new state
      // cannot ship silently rendering nothing.
      const unhandled: never = state;
      throw new Error(
        `StatePanel: unhandled state ${JSON.stringify(unhandled)}`
      );
    }
  }
}

/** The default loading slot: bars shaped like rows.
 *
 * A skeleton rather than a spinner because it says what is arriving rather
 * than only that something is. The console currently has three loading
 * languages — a `.skeleton` div, an `animate-spin` refresh icon, and a bare
 * `<p>Loading…</p>` — and this is the one that carries shape.
 *
 * Through `components/ui/skeleton.tsx` rather than the `.skeleton` class
 * directly, so that whatever that primitive decides the placeholder surface
 * is, this follows. That matters right now: the placeholder was recently
 * `bg-muted` at #f0eee8 on a #f1efe9 page — one value per channel, so a
 * loading panel was pixel-indistinguishable from an empty one, which under
 * §1.1 is a correctness bug and not a style preference.
 *
 * `h-11` because that is the bar height the console already uses for a list
 * row (`components/workspace/YourMachines.tsx`), and because two of them
 * plus this state's padding lands within ~10px of the resting height of the
 * empty and unreadable states. A panel that does not jolt as it resolves is
 * most of what makes four states read as one component.
 *
 * Its shimmer is the only motion in this file, and `globals.css` already
 * reduces it to nothing under `prefers-reduced-motion` — so there is no new
 * animation here to opt out of. */
function LoadingRows({ rows }: { rows: number }) {
  return (
    <div className="space-y-2" aria-hidden="true">
      {Array.from({ length: Math.max(1, rows) }, (_, i) => (
        <Skeleton key={i} className="h-11" />
      ))}
    </div>
  );
}
