"use client";

import type { ProviderDetail } from "@/lib/network/api";
import {
  approxUtcOffset,
  badgeLabel,
  bytesText,
  gpuChips,
  gpuVendor,
} from "@/lib/network/providers";
import { GpuChips } from "./GpuChips";

/**
 * The two reference cards under a provider's charts: what it is called and
 * where it is (General info), and what is inside it (Specs).
 *
 * ONE ROW PER FIELD, AND A ROW IS OMITTED RATHER THAN EMPTIED. A card of
 * eleven labels with nine em dashes beside them is a form somebody failed to
 * fill in; a card of two rows is two facts. So `Row` returns null for a null
 * value, and each card says in one line how much it is not showing.
 *
 * The only exception is the country, which is drawn even when absent on a
 * machine the reader OWNS — because for them the gap is actionable and the
 * form that closes it is on the same page.
 */
export function GeneralInfoCard({ provider }: { provider: ProviderDetail }) {
  const { location } = provider;
  const zone = approxUtcOffset(location.lon);
  // The API fields this card can show and this provider did not send. The
  // time zone is NOT counted: it is derived from the coordinates, so counting
  // it would report one missing fact twice.
  const missing = countMissing([
    provider.own ? "" : location.country,
    location.region,
    location.city,
    location.lat === null || location.lon === null ? null : "",
    location.source,
  ]);

  return (
    <Card title="General info" missing={missing}>
      <Row label="Label" value={provider.label} mono />
      <Row
        label="Country"
        value={location.country ?? (provider.own ? "not set" : null)}
        dim={location.country === null}
      />
      <Row label="Region" value={location.region} />
      <Row label="City" value={location.city} />
      <Row
        label="Coordinates"
        value={
          location.lat !== null && location.lon !== null
            ? `${location.lat.toFixed(2)}, ${location.lon.toFixed(2)}`
            : null
        }
        mono
      />
      {/* Two rows that both say where a number came from. The console does
          not present a derived value and a reported one in the same voice. */}
      <Row
        label="Time zone"
        value={zone}
        note="approximate, from longitude"
        mono
      />
      <Row label="Location source" value={location.source} />
      {/* Not counted in `missing`: this is not data a machine failed to
          report, it is a fact that only applies to Zolli Labs' own
          machines. A row that never renders for the other kind is not a
          gap in what the other kind sent. */}
      <Row
        label="Operator"
        value={provider.official ? "Zolli Labs (official)" : null}
      />
      <Row label="Trust tier" value={badgeLabel(provider.badge)} />
    </Card>
  );
}

export function SpecsCard({ provider }: { provider: ProviderDetail }) {
  const { specs } = provider;
  const chips = gpuChips(specs);
  const vendor = gpuVendor(specs);
  // GPUs are not counted: "none reported" is a row this card always draws,
  // because a machine with no GPU is a fact rather than a gap.
  const missing = countMissing([
    specs.cpu_cores,
    specs.memory_bytes,
    specs.os,
    specs.architecture,
  ]);

  return (
    <Card title="Specs" missing={missing}>
      {chips.length > 0 ? (
        <>
          <Row
            label="GPU vendor"
            value={vendor}
            note={vendor ? "from the reported model" : undefined}
          />
          <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1.5 py-2">
            <dt className="label-caps">GPU</dt>
            <dd className="min-w-0 text-right">
              <GpuChips chips={chips} />
            </dd>
          </div>
        </>
      ) : (
        <Row label="GPU" value="none reported" dim />
      )}
      <Row label="CPU cores" value={numberText(specs.cpu_cores)} mono />
      <Row label="Architecture" value={specs.architecture} mono />
      <Row label="Memory" value={bytesText(specs.memory_bytes)} mono />
      <Row label="OS" value={specs.os} />
    </Card>
  );
}

/** A fact card, and the count of what it could not show.
 *
 * THE COUNT IS THE OTHER HALF OF OMITTING ROWS. A card that simply drops
 * every null is tidy and slightly dishonest: a reader cannot tell a machine
 * that reported four things from one that reported four and failed to report
 * five, and both look complete. One line at the foot restores that — without
 * putting nine em dashes in a card, which is what dropping the rows was for.
 */
function Card({
  title,
  missing,
  children,
}: {
  title: string;
  missing: number;
  children: React.ReactNode;
}) {
  return (
    <section className="panel px-4 py-3.5">
      <h2 className="label-caps">{title}</h2>
      <dl className="mt-1 divide-y divide-border">{children}</dl>
      {missing > 0 && (
        <p className="mt-2.5 text-[11px] text-muted-foreground">
          {missing} {missing === 1 ? "field was" : "fields were"} not reported
          by this machine.
        </p>
      )}
    </section>
  );
}

/** How many of these the provider did not send. `""` is the caller's way of
 * saying "this one is accounted for elsewhere" — a country shown as "not set"
 * on an owned machine, or a coordinate pair counted once for two fields. */
function countMissing(values: Array<string | number | null>): number {
  return values.filter((v) => v === null).length;
}

/** One field. Renders NOTHING when the value is null — see the card's note.
 * `note` is for a value this console derived rather than read. */
function Row({
  label,
  value,
  mono = false,
  dim = false,
  note,
}: {
  label: string;
  value: string | null;
  mono?: boolean;
  dim?: boolean;
  note?: string;
}) {
  if (value === null) return null;
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-0.5 py-2">
      <dt className="label-caps">{label}</dt>
      <dd
        className={`min-w-0 text-right text-sm ${mono ? "font-mono tabular-nums" : ""} ${
          dim ? "text-muted-foreground" : ""
        }`}
      >
        {value}
        {note && <span className="meta block">{note}</span>}
      </dd>
    </div>
  );
}

function numberText(n: number | null): string | null {
  return n === null || !Number.isFinite(n) ? null : String(n);
}
