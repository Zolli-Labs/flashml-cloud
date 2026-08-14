"use client";

import type { UptimeHour } from "@/lib/network/api";

/**
 * The last day, one bar per hour.
 *
 * TWENTY-FOUR BARS, NOT NINETY-SIX. Akash's strip is 96 quarter-hour buckets
 * because Akash measures every fifteen minutes. This coordinator records
 * hourly, so the strip is one bar per hour and it is exactly as wide as the
 * evidence. Rendering 96 slots and filling four of them from each observed
 * hour would draw a resolution nobody measured.
 *
 * AN ABSENT HOUR IS NOT A DOWN HOUR. The strip draws the hours the API sent
 * and stops — it never pads to 24 with `up: false`, because a machine that
 * joined at noon would then render six red hours for a morning it did not
 * exist for. The caption states how many hours are actually behind the strip,
 * so a short strip reads as a short history rather than as a gap.
 */
export function UptimeStrip({ hours }: { hours: UptimeHour[] }) {
  if (hours.length === 0) {
    return (
      <p className="mt-2 text-sm text-muted-foreground">
        No hours observed for this provider yet — which is not the same as a
        day of downtime.
      </p>
    );
  }

  const up = hours.filter((h) => h.up).length;
  // 9px bar + 3px gap, floored so two timestamps still fit under a short
  // strip. Applied to the bars AND to the line of times under them so the
  // two read as one object — a full-width row of times under a 285px strip
  // is a scale for a chart that is not there. The sentence below is NOT
  // capped: constrained to 60px it wrapped into three lines.
  const width = Math.max(hours.length * 12, 140);

  return (
    <figure className="mt-3">
      {/* THE BARS ARE CAPPED, NOT STRETCHED. `flex-1` alone divides whatever
          width the column has between however many hours there are, so a
          provider with five observed hours got five 70-pixel slabs — which
          reads as a bar chart of something, not as a day. A ceiling of 9px
          keeps a thin instrument strip at 24 hours and an obviously short
          one at 5. */}
      <div
        className="flex h-14 items-stretch gap-[3px]"
        style={{ maxWidth: width }}
        role="img"
        aria-label={`${up} of the last ${hours.length} observed hours were up`}
      >
        {hours.map((hour) => (
          <span
            key={hour.hour_ts}
            title={`${hourLabel(hour.hour_ts)} — ${hour.up ? "up" : "down"}`}
            className={`min-w-0 max-w-[9px] flex-1 rounded-[2px] ${
              hour.up ? "bg-evergreen" : "bg-border"
            }`}
          />
        ))}
      </div>

      <div
        className="mt-2 flex items-baseline justify-between gap-3"
        style={{ maxWidth: width }}
      >
        <span className="meta">{hourLabel(hours[0].hour_ts)}</span>
        <span className="meta">{hourLabel(hours[hours.length - 1].hour_ts)}</span>
      </div>
      <p className="mt-1 text-[11px] text-muted-foreground">
        {up} of {hours.length} observed{" "}
        {hours.length === 1 ? "hour" : "hours"} up
      </p>
    </figure>
  );
}

/** `14:00`. An unparseable stamp is quoted back rather than rendered as
 * "Invalid Date". */
function hourLabel(iso: string): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  return new Date(ms).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}
