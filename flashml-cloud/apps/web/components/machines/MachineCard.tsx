import Link from "next/link";
import {
  Desktop as UnknownKindIcon,
  GraphicsCard,
  HardDrives,
  Laptop,
  type Icon,
} from "@phosphor-icons/react";
import { Badge } from "@/components/ui/badge";
import {
  MACHINE_BADGE_LABELS,
  MACHINE_BADGE_STYLES,
  machineBadge,
} from "@/lib/machine-badge";
import { machineKind, type MachineKind } from "@/lib/machine-kind";
import { machineLabel } from "@/lib/machine-lifecycle";
import { isMachineOnline } from "@/lib/machine-scope";
import { relativeTime } from "@/lib/machine-status";
import { cn } from "@/lib/utils";
import type { Machine } from "@/lib/cloud-api";

/** Silent annotation only — no tooltip inventing specs, no colour coding.
 * The kind itself is a presentational guess (`lib/machine-kind.ts`); coding
 * it in colour would dress a guess up as a fact. Exported so the detail
 * view (`app/(console)/machines/[id]/page.tsx`) can draw the identical
 * icon next to the identifier title — one map, not two copies to drift. */
export const KIND_ICON: Record<MachineKind, Icon> = {
  laptop: Laptop,
  gpu: GraphicsCard,
  server: HardDrives,
  unknown: UnknownKindIcon,
};

/** The machine-register voice for a card/detail TAG — the trust badge and
 * the pool chip — matching `StateBadge`'s own mono treatment so a job's
 * state and a machine's trust/pool read as the same family of fact rather
 * than one being a sentence-case sans chip next to a squared mono one.
 * Deliberately NOT folded into `Badge`'s own base classes: buttons and
 * every other Badge consumer keep the sans/mixed-case voice, and this is
 * additive per call site (merged in via `cn`, which lets these win over
 * the base `text-xs` without touching it). Exported so
 * `MachineDetailView.tsx`'s Placement section uses the identical string. */
export const MACHINE_TAG_CLASS = "font-mono text-[10px] uppercase tracking-[0.06em]";

/**
 * STANDING RULE — instrument-panel pass, owner-approved: lean must not read
 * bare or unfinished. Dropping the table's columns down to a card's five
 * facts (kind, name/id, trust, pool, last seen) is a reduction in DATA, and
 * the reduction has to be carried by COMPOSITION or the result reads cheap:
 * the icon in its own aligned cell, the trust tag anchored top-right, a
 * hairline-separated footer row so the card has structure rather than
 * floating lines, one consistent spacing rhythm (4/8/12/16), and every card
 * in the grid keeping the same shape — a missing pool renders as an
 * em-dash in the footer's pool slot, never an omitted row, so the grid's
 * baselines stay aligned. If a card ever looks unbalanced, the fix is
 * composition — spacing, alignment, the footer row — never re-adding a
 * field this pass moved to the detail view (platform, arch, specs,
 * timestamps all live there now, not here).
 *
 * NO LIFT, NO SHADOW, NO SCALE on hover — a colour/border shift only,
 * matching the flat elevation the rest of this pass adopted.
 *
 * NO LIVE/LEASE TREATMENT. The approved mockup shows a 2px orange edge and
 * a mono "RUNNING lease-…" line for a machine mid-job, but this console's
 * machines list does not fetch or render lease/job data anywhere today —
 * adding it here would be inventing a value the API never returned. Skipped
 * under the honesty rule; wire it up if and when that data exists.
 */
export function MachineCard({
  machine,
  href,
}: {
  machine: Machine;
  href: string;
}) {
  const Icon = KIND_ICON[machineKind(machine)];
  const label = machineLabel(machine);
  const online = isMachineOnline(machine);
  const badge = machineBadge(machine);
  const pool = machine.pools[0] ?? null;

  return (
    <Link
      href={href}
      className="group block rounded-md border border-border bg-surface p-4 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus hover:border-[var(--z-app-border-strong)] hover:bg-surface-2"
    >
      <div className="flex items-start gap-3">
        <Icon
          size={18}
          weight="regular"
          className="mt-0.5 shrink-0 text-muted-foreground"
          aria-hidden="true"
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate text-[13px] font-medium text-foreground">
                {label}
              </p>
              {/* Only when the title above is the machine's OWN name — when
                  it fell back to the node id (no name set), this line would
                  print the exact same string a second time. */}
              {machine.name && (
                <p className="mt-0.5 truncate font-mono text-[10.5px] text-[var(--z-app-text-dim)]">
                  {machine.node_id}
                </p>
              )}
            </div>
            <Badge
              variant="outline"
              className={cn("shrink-0", MACHINE_TAG_CLASS, MACHINE_BADGE_STYLES[badge])}
            >
              {MACHINE_BADGE_LABELS[badge]}
            </Badge>
          </div>
        </div>
      </div>

      <div className="mt-3 flex items-center justify-between gap-2 border-t border-border pt-3">
        {pool ? (
          <span
            className={cn(
              "truncate rounded-sm border border-border bg-surface-2 px-1.5 py-0.5 text-muted-foreground",
              MACHINE_TAG_CLASS
            )}
          >
            {pool.name}
          </span>
        ) : (
          <span className="font-mono text-[11px] text-[var(--z-app-text-dim)]">
            —
          </span>
        )}
        <span className="flex shrink-0 items-center gap-1.5 font-mono text-[11px] text-muted-foreground">
          {online && (
            <span
              className="status-dot"
              data-state="live"
              style={{ background: "var(--node-green)" }}
              aria-hidden="true"
            />
          )}
          {relativeTime(machine.last_seen_at)}
        </span>
      </div>
    </Link>
  );
}
