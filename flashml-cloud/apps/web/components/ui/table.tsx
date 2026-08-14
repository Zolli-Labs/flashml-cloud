"use client"

import * as React from "react"

import { cn } from "@/lib/utils"

function Table({ className, ...props }: React.ComponentProps<"table">) {
  return (
    <div
      data-slot="table-container"
      className="relative w-full overflow-x-auto"
    >
      <table
        data-slot="table"
        className={cn("w-full caption-bottom text-sm", className)}
        {...props}
      />
    </div>
  )
}

function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return (
    <thead
      data-slot="table-header"
      className={cn("[&_tr]:border-b", className)}
      {...props}
    />
  )
}

function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return (
    <tbody
      data-slot="table-body"
      className={cn("[&_tr:last-child]:border-0", className)}
      {...props}
    />
  )
}

function TableFooter({ className, ...props }: React.ComponentProps<"tfoot">) {
  return (
    <tfoot
      data-slot="table-footer"
      className={cn(
        "border-t bg-muted-50 font-medium [&>tr]:last:border-b-0",
        className
      )}
      {...props}
    />
  )
}

/**
 * NO HOVER FILL BY DEFAULT — changed from the stock `hover:bg-muted-50`.
 *
 * Measured across `app/` and `components/`: of 27 `<tr>` in the console,
 * exactly ONE carries a hover fill (the jobs table, whose rows are links).
 * A hover fill is this codebase's signal that a row goes somewhere, and
 * putting it on by default would have told a reader that every member row
 * and every fleet row is clickable. A table whose rows navigate opts in with
 * `className="hover:bg-surface-2/70"` — the value that one row already uses.
 *
 * `border-b` and not the `divide-y divide-border` the console hand-writes on
 * 12 `<tbody>`s: same rendered hairline (`@layer base` sets `*` to
 * `border-border`, so the bare `border-b` is the token and not
 * `currentColor`), and `[&_tr:last-child]:border-0` on `TableBody` matches
 * `divide-y`'s "between, not after" behaviour.
 */
function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return (
    <tr
      data-slot="table-row"
      className={cn(
        "border-b transition-colors has-aria-expanded:bg-muted-50 data-[state=selected]:bg-muted",
        className
      )}
      {...props}
    />
  )
}

/**
 * `px-3 py-2` and no `h-10` — the console's own modal header treatment,
 * counted rather than chosen: 8 of the 20 hand-written `<th>` in `app/` and
 * `components/` use exactly this, more than any other value. The stock
 * `h-10 px-2` matched none of them, and `h-10` in particular forced a 40px
 * header row that the console's 11px `.label-caps` labels do not fill.
 *
 * MACHINE REGISTER, matching `.label-caps`'s own values (`font-size:
 * 0.6875rem`, `font-weight: 500`, `letter-spacing: 0.07em`, `text-transform:
 * uppercase`, `color: var(--muted-foreground)`) plus `font-mono` — a column
 * name is metadata about the row, the same category as a job id or a
 * machine name, not prose, so it gets the same mono treatment those do.
 * `.label-caps` itself does not set `font-family`, which is why call sites
 * that merely apply `className="label-caps"` to `TableHead` — `MemberTable`,
 * `PoolFleetTable`, the pool jobs page — used to render sans headers; this
 * default now supplies the mono face directly, and this file's own uses of
 * `.label-caps` on that primitive are removed as redundant (`.label-caps`
 * is declared UNLAYERED in globals.css, so for the properties it does share
 * with this default it would win the cascade anyway — same values, so no
 * visible change, just one fewer source of the same rule to reconcile).
 * `font-medium` replaces the old plain `font-medium` — checked against 2–3
 * hand-rolled `<th>` call sites (`app/(console)/account/machines/page.tsx`,
 * `components/jobs/TasksTable.tsx`) that do NOT use this primitive at all —
 * they hand-write `<table>`/`<th>` and apply `.label-caps` themselves, so
 * this default reaching them is not a risk; they simply stay outside this
 * primitive's reach, same as before.
 */
function TableHead({ className, ...props }: React.ComponentProps<"th">) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        "px-3 py-2 text-left align-middle whitespace-nowrap font-mono text-[11px] font-medium uppercase tracking-[0.07em] text-muted-foreground [&:has([role=checkbox])]:pr-0",
        className
      )}
      {...props}
    />
  )
}

/**
 * `px-3 py-2.5` — the console's modal cell treatment, 23 of 39 hand-written
 * `<td>`, against `px-4 py-2.5` (14) and `px-3 py-3` (13). The stock `p-2`
 * (8px on both axes) matched nothing in the console.
 *
 * MOVING THE PRIMITIVE RATHER THAN THE CALL SITES was the point: this file
 * had no consumers at all before this sweep, so its defaults had never been
 * under any pressure to agree with the tables people actually look at. With
 * the default set to what the plurality already renders, the remaining
 * hand-written tables convert with no pixels moving — which matters more
 * than usual here, because no session in this workspace can sign in to check
 * a console screen.
 */
function TableCell({ className, ...props }: React.ComponentProps<"td">) {
  return (
    <td
      data-slot="table-cell"
      className={cn(
        "px-3 py-2.5 align-middle whitespace-nowrap [&:has([role=checkbox])]:pr-0",
        className
      )}
      {...props}
    />
  )
}

function TableCaption({
  className,
  ...props
}: React.ComponentProps<"caption">) {
  return (
    <caption
      data-slot="table-caption"
      className={cn("mt-4 text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

export {
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableCaption,
}
