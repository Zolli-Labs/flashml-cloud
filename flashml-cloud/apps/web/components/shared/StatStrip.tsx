import { cn } from "@/lib/utils";

/**
 * The instrument-panel metric strip: a hairline-bordered row of label/value
 * cells, no fills, no radius, no shadow — a `stat-strip` in the approved
 * "instrument panel" mockup (`.preview/vision-machines-*.html` in the
 * design brief), reused wherever a page has a small set of counts to lead
 * with.
 *
 * A RESTYLE PRIMITIVE, not a data source. Every value is a `ReactNode` the
 * caller already computed — this component renders exactly what it is
 * given and invents nothing. §1.1 still applies at the call site: a page
 * that has not fetched a number must not pass one, not even a plausible
 * zero.
 */
export interface StatStripItem {
  /** Machine-emitted register: short, uppercase-styled by this component. */
  label: string;
  /** Usually a count already computed by the caller. `ReactNode` rather
   * than `number` so a caller can pass a formatted string or an em-dash for
   * "not observed" without this component knowing the difference. */
  value: React.ReactNode;
}

export function StatStrip({
  items,
  className,
}: {
  items: StatStripItem[];
  className?: string;
}) {
  return (
    <div
      className={cn(
        "grid h-14 border-y border-border",
        className
      )}
      style={{ gridTemplateColumns: `repeat(${items.length}, minmax(0, 1fr))` }}
    >
      {items.map((item, i) => (
        <div
          key={item.label}
          className={cn(
            "flex flex-col justify-center gap-1 px-6",
            i > 0 && "border-l border-border",
            i === 0 && "pl-0"
          )}
        >
          <span className="font-mono text-[10px] uppercase tracking-[0.07em] text-[var(--z-app-text-dim)]">
            {item.label}
          </span>
          <span className="font-mono text-lg font-semibold tabular-nums text-foreground">
            {item.value}
          </span>
        </div>
      ))}
    </div>
  );
}
