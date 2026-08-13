/** How wide a console page's content column is, and why.
 *
 * WHY THIS MODULE EXISTS. `mx-auto max-w-6xl px-4 py-8 sm:px-6` is written
 * out by hand in thirteen page files, and four more pages differ from it —
 * `max-w-4xl`, `max-w-3xl`, `max-w-xl` — with nothing recorded about
 * whether those are decisions or accidents. One of them (`/account/github`)
 * has no container at all and stretches to whatever the viewport gives it.
 * Copy-paste plus undocumented exceptions is a layout that cannot be
 * reviewed: there is no way to tell a considered narrow page from a page
 * somebody forgot.
 *
 * So the widths get names, the names get reasons, and the reasons get a
 * test. `components/shell/PageShell.tsx` is the only thing that turns a name
 * into a class.
 *
 * THE VOCABULARY IS DERIVED, NOT IMPOSED. The four widths below are exactly
 * the four already in use in `app/(console)`. Nothing here asks any page to
 * change size for the sake of a system; it names what each page already is,
 * and the two reassignments it does make (below) are stated as such.
 *
 * WHAT A WIDTH IS CHOSEN BY. The widest thing the page has to lay out — a
 * record table, a stat grid, a paragraph, a form. Never the route's place in
 * the nav, and never "it looked better": both of those produce a table that
 * scrolls sideways next to a paragraph that runs 140 characters.
 */

/** Named for the content, never for the pixels. A page picks the word that
 * describes what it lays out; if the number behind the word ever moves, it
 * moves for every page that made the same claim. */
export type ConsoleWidth = "focused" | "reading" | "standard" | "wide";

export const CONSOLE_WIDTHS: readonly ConsoleWidth[] = [
  "focused",
  "reading",
  "standard",
  "wide",
];

/** The Tailwind utility for each name. These are the only four `max-w-*`
 * values the console signed-in surface is allowed to use on a page
 * container. */
export const CONSOLE_WIDTH_CLASS: Record<ConsoleWidth, string> = {
  focused: "max-w-xl", // 36rem / 576px
  reading: "max-w-3xl", // 48rem / 768px
  standard: "max-w-4xl", // 56rem / 896px
  wide: "max-w-6xl", // 72rem / 1152px
};

/** Why each width exists, in the terms the choice is actually made in.
 * Exported rather than left as a comment so that the reason travels with the
 * name into review, and so a width without a reason is a test failure. */
export const CONSOLE_WIDTH_RATIONALE: Record<ConsoleWidth, string> = {
  focused:
    "A single decision: one form, one card, one question. 576px. Wider makes a three-field form look abandoned in the middle of the page, and there is nothing else on screen to fill the gutter.",
  reading:
    "Prose, or one form read top to bottom. 768px is roughly 85 characters at the console's body size, the top of a comfortable measure. Capping the COLUMN rather than each paragraph is the point: a 44px heading set across 1152px above a 65-character paragraph is the current docs page, and it reads as two unrelated layouts.",
  standard:
    "A stack of panels or a short list of records — the credit ledger, the admin queue, the listings book, the reliability tiles. Wide enough for a four-column stat row, narrow enough that a two-line panel does not strand 400px of empty page beside it.",
  wide: "A record table or a multi-column instrument grid: fleet, jobs, people, prices. These carry five to eight columns, and the hand-written `min-w-[520-640px]` floors on them only prevent collapse — they do not produce a readable row. 1152px is what keeps cells from truncating on the machine an operator actually uses.",
};

/** Route pattern → width, with dynamic segments in Next's own bracket form.
 *
 * A table rather than a prop-only convention because the review question is
 * "why is THIS page narrow", and the answer has to be visible next to the
 * other eighteen. `PageShell` still takes the width as a prop — this is the
 * record of what each page decided, and what the adoption sweep should set.
 *
 * TWO ENTRIES ARE REASSIGNMENTS, not transcriptions of the current class:
 *
 *   `/docs` and `/how-it-works` are `max-w-6xl` today and contain no table
 *   and no grid — every paragraph in both files already carries its own
 *   `max-w-prose`. The 1152px column is therefore doing nothing except
 *   stretching the heading. `reading` moves the cap to the column, where it
 *   belongs, and changes the body measure not at all.
 *
 *   `/account/github` has no container at all today, which is a defect
 *   rather than a width: it is a page of prose plus a short list of
 *   installations, so it is `reading` like the account page it sits under.
 */
export const CONSOLE_PAGE_WIDTH: Record<string, ConsoleWidth> = {
  // wide — a record table or a multi-column instrument grid
  "/w/[poolId]/overview": "wide",
  "/w/[poolId]/jobs": "wide",
  "/w/[poolId]/machines": "wide",
  "/w/[poolId]/people": "wide",
  "/w/[poolId]/settings": "wide",
  "/jobs/[jobId]": "wide",
  "/account/machines": "wide",
  "/machines": "wide",
  // The market tabs share one column so the tab bar (drawn at `wide` by
  // app/(console)/market/layout.tsx) lines up with every page under it.
  "/market/prices": "wide",
  // One class in full: a price chart beside a value axis, then the table of
  // every machine offering the class. Same column as the board it is
  // reached from, so the header does not jump width on the click.
  "/market/prices/[klass]": "wide",
  "/market/listings": "wide",
  "/market/credits": "wide",

  // standard — stacked panels and short lists of records
  "/admin/requests": "standard",
  "/metrics": "standard",

  // reading — prose, or one form
  "/docs": "reading",
  "/how-it-works": "reading",
  "/account": "reading",
  // The settings tabs share one column, same reason as the market's; the
  // CLI table's 560px floor fits the 768px reading column.
  "/account/cli": "reading",
  "/account/github": "reading",
  "/settings": "reading",
  "/settings/cli": "reading",
  "/settings/github": "reading",
  "/w/[poolId]/submit": "reading",

  // focused — a single decision
  "/workspaces": "focused",
};

/** The width an unlisted console route gets.
 *
 * `standard`, and the direction of the failure is the reason. A page that
 * turns out to need `wide` and gets `standard` degrades into a table that
 * scrolls inside its own `overflow-x-auto` container — the pattern every
 * table in this codebase already has, recoverable, and visible immediately.
 * A page that needs `reading` and gets `wide` degrades into 140-character
 * lines, which nothing recovers and nobody files. Default toward the
 * failure that announces itself. */
export const DEFAULT_CONSOLE_WIDTH: ConsoleWidth = "standard";

/** Console routes that deliberately do NOT render a `PageShell`, and why.
 *
 * Kept here so the adoption sweep can tell "covered" from "missed". Every
 * route under `app/(console)` is either in `CONSOLE_PAGE_WIDTH` or in this
 * list; a route in neither has not been considered yet. */
export const CONSOLE_ROUTES_WITHOUT_SHELL: Record<string, string> = {
  "/overview": "Redirect. Renders WorkspaceResolver, no content of its own.",
  "/pools": "Redirect. Renders WorkspaceResolver, no content of its own.",
  "/pools/[poolId]": "Server redirect to /w/[poolId]/overview.",
  "/jobs": "Redirect. Renders WorkspaceResolver, no content of its own.",
  "/deploy": "Redirect. Renders WorkspaceResolver, no content of its own.",
  "/market": "Server redirect to /market/prices.",
  "/w/[poolId]": "Server redirect to /w/[poolId]/overview.",
  "/activate":
    "A viewport-centred card, not a content column. That container is ConsoleShell's (it already centres the three access screens the same way) and duplicating its viewport-height calculation here would put the same magic number in a fifth place.",
  "/machines/add": "The /activate card under the Machines tabs — same shape.",
  "/pools/join": "A viewport-centred card. Same container as /activate.",
  "/account/github/callback":
    "A viewport-centred status card that exists for one round trip.",
};

/** Reduce a live pathname to the route pattern the table is keyed by.
 *
 * Query and hash go first, then a trailing slash, then the dynamic segments
 * this console has. Pure string work — it does not check that the id names
 * anything real, which is the API's job. */
export function consoleRouteKey(pathname: string): string {
  const path = pathname.split(/[?#]/, 1)[0];
  const trimmed =
    path.length > 1 && path.endsWith("/") ? path.slice(0, -1) : path;

  return trimmed
    .replace(/^\/w\/[^/]+/, "/w/[poolId]")
    .replace(/^\/pools\/(?!join$)[^/]+/, "/pools/[poolId]")
    .replace(/^\/jobs\/[^/]+/, "/jobs/[jobId]")
    .replace(/^\/market\/prices\/[^/]+/, "/market/prices/[klass]");
}

/** Which width a console route gets. Falls back to `DEFAULT_CONSOLE_WIDTH`
 * for anything not in the table — including the routes in
 * `CONSOLE_ROUTES_WITHOUT_SHELL`, which never ask. */
export function widthForConsolePath(pathname: string): ConsoleWidth {
  return CONSOLE_PAGE_WIDTH[consoleRouteKey(pathname)] ?? DEFAULT_CONSOLE_WIDTH;
}
