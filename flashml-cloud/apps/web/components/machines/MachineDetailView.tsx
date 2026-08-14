import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { KIND_ICON, MACHINE_TAG_CLASS } from "@/components/machines/MachineCard";
import { PageHeader } from "@/components/shell/PageHeader";
import {
  MACHINE_BADGE_LABELS,
  MACHINE_BADGE_STYLES,
  machineBadge,
} from "@/lib/machine-badge";
import { readMachineCapabilities } from "@/lib/machine-capabilities";
import { machineKind } from "@/lib/machine-kind";
import { machineLabel } from "@/lib/machine-lifecycle";
import { relativeTime } from "@/lib/machine-status";
import { cn, formatBytes } from "@/lib/utils";
import type { Machine } from "@/lib/cloud-api";

/**
 * The presentational half of the machine detail view — everything BELOW
 * "the machine is now resolved", pulled out of
 * `app/(console)/machines/[id]/page.tsx` so it takes no dependency on
 * routing, fetching or `params`. That split is what lets
 * `preview/machines.render.tsx` render the real thing from a fixture,
 * the same reason `MemberTable`/`PoolFleetTable` (not the pages around
 * them) are what `preview/workspace-tables.render.tsx` imports.
 *
 * SECTIONS RENDER ONLY WHEN THEIR DATA EXISTS. IDENTITY and PLACEMENT can
 * always be shown (a machine always has an id, a creation time, and a
 * derivable trust tier). HARDWARE depends entirely on `platform` and the
 * allowlisted `capabilities` snapshot (`lib/machine-capabilities.ts`) and is
 * omitted outright when none of those fields were ever reported — never
 * rendered as a section full of em-dashes. A FIELD inside a rendered
 * section that is individually absent is an em-dash, never a 0 or a
 * placeholder that could be mistaken for a reading.
 *
 * NO CURRENT LEASE/JOB. The approved mockup's ACTIVITY section imagines one;
 * this console does not fetch or render lease/job state for a machine
 * anywhere today, on this page or the grid it is reached from, so it is not
 * invented here either.
 *
 * `max-w-3xl` ON THE SECTION COLUMN, not the full `wide` PageShell width:
 * a two-column label/value list stretched across 1152px is enormous
 * label→value eye travel for a reader whose gaze has to cross most of the
 * screen on every row. `DetailRow` below lays out as a FIXED two-column
 * grid (a ~14rem label column, values left-aligned beside it) rather than
 * flexing the label to its content width and pushing the value to the far
 * right edge — the measure and the alignment fix the same problem from two
 * angles and both are needed: the measure caps how far right a row's own
 * value column can start, the grid keeps every row's value starting at
 * that same fixed offset instead of drifting with each label's length.
 */
export function MachineDetailView({
  machine,
  revoking,
  onRevoke,
}: {
  machine: Machine;
  /** Whether a revoke request is in flight — drives the confirm button's
   * disabled/label state. */
  revoking: boolean;
  onRevoke: () => void;
}) {
  const label = machineLabel(machine);
  const Icon = KIND_ICON[machineKind(machine)];
  const badge = machineBadge(machine);
  const hardware = readMachineCapabilities(machine.capabilities);
  const hasHardware =
    hardware.os !== null ||
    hardware.architecture !== null ||
    hardware.cpuCores !== null ||
    hardware.memoryBytes !== null ||
    hardware.gpus.length > 0 ||
    machine.platform !== null;
  const canRevoke = machine.status !== "revoked";

  return (
    <>
      <PageHeader
        back={{ href: "/machines", label: "My machines" }}
        title={label}
        titleTone="identifier"
        meta={
          <span className="inline-flex items-center gap-1.5">
            <Icon size={13} weight="regular" aria-hidden="true" />
            {machine.node_id}
          </span>
        }
      />

      <div className="mt-6 max-w-3xl space-y-8">
        <Section title="Identity">
          {/* Only when there IS a name distinct from the machine id — when
              there is not, `label` above (the page's own title) already
              equals `machine.node_id`, and a "Name" row would either show
              an em-dash next to that title for no reason or, worse, repeat
              the exact string the Machine ID row two lines down also
              shows. */}
          {machine.name && (
            <DetailRow label="Name">{machine.name}</DetailRow>
          )}
          <DetailRow label="Machine ID" mono>
            {machine.node_id}
          </DetailRow>
          <DetailRow label="Enrolled" mono>
            {relativeTime(machine.created_at)}
          </DetailRow>
        </Section>

        {hasHardware && (
          <Section title="Hardware">
            <DetailRow label="Platform" mono>
              {machine.platform ?? <Em />}
            </DetailRow>
            <DetailRow label="Architecture" mono>
              {hardware.architecture ?? <Em />}
            </DetailRow>
            <DetailRow label="CPU cores" mono tabular>
              {hardware.cpuCores ?? <Em />}
            </DetailRow>
            <DetailRow label="Memory" mono>
              {formatBytes(hardware.memoryBytes)}
            </DetailRow>
            {hardware.gpus.length > 0 && (
              <DetailRow label="GPUs" mono>
                <span className="flex flex-col gap-1 text-left">
                  {hardware.gpus.map((gpu, i) => (
                    <span key={i}>
                      {gpu.name ?? "unnamed device"}
                      {gpu.memoryTotalMb !== null && (
                        <span className="text-muted-foreground">
                          {" · "}
                          {formatBytes(gpu.memoryTotalMb * 1024 * 1024)}
                        </span>
                      )}
                    </span>
                  ))}
                </span>
              </DetailRow>
            )}
          </Section>
        )}

        <Section title="Placement">
          <DetailRow label="Trust">
            <Badge
              variant="outline"
              className={cn(MACHINE_TAG_CLASS, MACHINE_BADGE_STYLES[badge])}
            >
              {MACHINE_BADGE_LABELS[badge]}
            </Badge>
          </DetailRow>
          <DetailRow label="Pool">
            {machine.pools.length > 0 ? (
              <span className="flex flex-wrap justify-start gap-1.5">
                {machine.pools.map((pool) => (
                  <span
                    key={pool.id}
                    className={cn(
                      "rounded-sm border border-border bg-surface-2 px-1.5 py-0.5 text-muted-foreground",
                      MACHINE_TAG_CLASS
                    )}
                  >
                    {pool.name}
                  </span>
                ))}
              </span>
            ) : (
              <Em />
            )}
          </DetailRow>
        </Section>

        <Section title="Activity">
          <DetailRow label="Last seen" mono>
            {relativeTime(machine.last_seen_at)}
          </DetailRow>
        </Section>

        {canRevoke && (
          <section className="border-t border-border pt-6">
            <p className="label-caps">Revoke</p>
            <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
              Its token stops working immediately and it can no longer claim
              work. Any task it currently holds keeps running until the
              lease expires, then requeues elsewhere. Re-enrolling needs a
              new device code.
            </p>
            <AlertDialog>
              <AlertDialogTrigger
                render={
                  <Button variant="destructive" size="sm" className="mt-3.5">
                    Revoke {label}
                  </Button>
                }
              />
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Revoke {label}?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Its token stops working immediately and it can no longer
                    claim work. Any task it currently holds keeps running
                    until the lease expires, then requeues elsewhere.
                    Re-enrolling needs a new device code. It moves to
                    Revoked on the My machines list, where it can be deleted
                    for good.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Keep it</AlertDialogCancel>
                  <AlertDialogAction
                    disabled={revoking}
                    onClick={onRevoke}
                    className="bg-destructive/15 text-destructive hover:bg-destructive/25"
                  >
                    {revoking ? "Revoking…" : "Revoke"}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </section>
        )}
      </div>
    </>
  );
}

/** An em-dash for a field this section is rendering but this particular
 * machine did not report — never a blank, never a 0. */
function Em() {
  return <span className="text-muted-foreground">—</span>;
}

/** One topic, `label-caps` header, hairline below it — the detail view's
 * unit. Sections compose in the page above rather than nesting: this draws
 * no box of its own, matching `.panel`'s "one level of boxing" rule. */
function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <p className="label-caps border-b border-border pb-2">{title}</p>
      <dl className="divide-y divide-border">{children}</dl>
    </section>
  );
}

/** One label/value row inside a `Section`. `mono` marks a machine-emitted
 * value (per the console-wide rule `.meta`/`.metric-value` already encode);
 * `tabular` adds tabular numerals for a value that is itself a number.
 *
 * A FIXED two-column GRID, not a flexed `justify-between` row: the label
 * column is always the same ~14rem regardless of how long any one label
 * is, so every row's value starts at the same horizontal offset and the
 * column reads as a column. Values are LEFT-aligned in that second column
 * — matching how the label beside them reads — rather than right-aligned
 * against the row's far edge, which is what made label→value eye travel so
 * large before this pass. */
function DetailRow({
  label,
  mono = false,
  tabular = false,
  children,
}: {
  label: string;
  mono?: boolean;
  tabular?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[14rem_1fr] items-start gap-6 py-2.5">
      <dt className="label-caps">{label}</dt>
      <dd
        className={`min-w-0 text-left text-sm text-foreground ${
          mono ? "font-mono text-[13px]" : ""
        } ${tabular ? "tabular-nums" : ""}`}
      >
        {children}
      </dd>
    </div>
  );
}
