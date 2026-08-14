"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { MagnifyingGlass } from "@phosphor-icons/react";

import type { Provider } from "@/lib/network/api";
import {
  DEFAULT_TABLE_QUERY,
  TONE_CLASS,
  bytesText,
  filterProviders,
  gpuChips,
  locationShort,
  maxCpuCores,
  sortProviders,
  successStat,
  tableSummary,
  uptimeStat,
  type SortKey,
} from "@/lib/network/providers";
import { GpuChips } from "./GpuChips";
import { MeterGlyph } from "./MeterGlyph";
import { OfficialChip } from "./OfficialChip";
import { TrustBadge } from "./TrustBadge";

/**
 * Every provider on the network, searchable and sortable.
 *
 * SORTED BY ACTIVE LEASES, DESCENDING, ON ARRIVAL. The page exists to answer
 * "who is actually carrying work"; alphabetical is a directory, and a
 * directory is what you reach for once you already know the name.
 *
 * A PROVIDER WITH NO LOCATION IS STILL A ROW. Coordinates decide whether a dot
 * appears on the map above and nothing else — filtering the table to the
 * locatable ones would make the count under the map disagree with the count
 * in it, and quietly hide every machine whose host has not set a country.
 *
 * MARKUP AND POSITION ONLY. Which rows a filter selects, what a percentage is
 * allowed to say, when a rate has too little evidence to state — all of it is
 * `lib/network/providers.ts`, where a test can reach it. Nothing in this file
 * decides what a number means.
 */
export function ProvidersTable({ providers }: { providers: Provider[] }) {
  const [search, setSearch] = useState(DEFAULT_TABLE_QUERY.search);
  const [activeOnly, setActiveOnly] = useState(DEFAULT_TABLE_QUERY.activeOnly);
  const [gpuOnly, setGpuOnly] = useState(DEFAULT_TABLE_QUERY.gpuOnly);
  const [mineOnly, setMineOnly] = useState(DEFAULT_TABLE_QUERY.mineOnly);
  const [sort, setSort] = useState<SortKey>(DEFAULT_TABLE_QUERY.sort);
  const [desc, setDesc] = useState(DEFAULT_TABLE_QUERY.desc);

  const rows = useMemo(
    () =>
      sortProviders(
        filterProviders(providers, { search, activeOnly, gpuOnly, mineOnly }),
        sort,
        desc
      ),
    [providers, search, activeOnly, gpuOnly, mineOnly, sort, desc]
  );

  const online = providers.filter((p) => p.online).length;
  const mine = providers.filter((p) => p.own).length;
  // From the UNFILTERED list on purpose — see `Row`'s `cpuMax`.
  const cpuMax = useMemo(() => maxCpuCores(providers), [providers]);

  /** A second click on the same column reverses it; a first click on a new
   * column starts descending for the numeric ones and ascending for the two
   * that are words. Sorting names Z→A first is the kind of small wrongness
   * that makes a table feel unfinished. */
  const toggle = (key: SortKey) => {
    if (key === sort) {
      setDesc((d) => !d);
      return;
    }
    setSort(key);
    setDesc(key !== "label" && key !== "location");
  };

  return (
    <section>
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
        <div className="relative min-w-0 flex-1 sm:max-w-xs">
          <MagnifyingGlass
            size={15}
            className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground"
          />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search name, place, GPU…"
            aria-label="Search providers"
            className="h-9 w-full rounded-[7px] border border-input bg-surface pr-3 pl-8 text-sm transition-colors outline-none placeholder:text-muted-foreground focus-visible:border-ring"
          />
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <Check
            label="Active"
            checked={activeOnly}
            onChange={setActiveOnly}
            count={online}
          />
          <Check
            label="Has GPU"
            checked={gpuOnly}
            onChange={setGpuOnly}
            count={providers.filter((p) => p.specs.gpus.length > 0).length}
          />
          {/* Hidden rather than disabled when the account hosts nothing: a
              filter that can only ever empty the table is not a filter. */}
          {mine > 0 && (
            <Check
              label="Mine"
              checked={mineOnly}
              onChange={setMineOnly}
              count={mine}
            />
          )}
        </div>
      </div>

      <p className="meta mt-2.5">
        {tableSummary(rows.length, providers.length, online)}
      </p>

      <div className="mt-2 overflow-x-auto rounded-lg border border-border bg-surface">
        <table className="w-full min-w-[940px] border-collapse text-xs">
          <thead>
            <tr className="border-b border-border">
              <Th sort={sort} desc={desc} col="label" onSort={toggle}>
                Provider
              </Th>
              <Th sort={sort} desc={desc} col="location" onSort={toggle}>
                Location
              </Th>
              <Th sort={sort} desc={desc} col="uptime" onSort={toggle} align="right">
                Uptime (7d)
              </Th>
              <Th sort={sort} desc={desc} col="leases" onSort={toggle} align="right">
                Active leases
              </Th>
              <th scope="col" className="label-caps px-2 py-2 text-right">
                Success
              </th>
              <Th sort={sort} desc={desc} col="cpu" onSort={toggle}>
                CPU
              </Th>
              <Th sort={sort} desc={desc} col="gpu" onSort={toggle}>
                GPU
              </Th>
              <Th sort={sort} desc={desc} col="memory" onSort={toggle}>
                Memory
              </Th>
              <th scope="col" className="label-caps px-3 py-2 text-right">
                Trust
              </th>
            </tr>
          </thead>

          <tbody className="divide-y divide-border">
            {rows.map((p) => (
              <Row key={p.id} provider={p} cpuMax={cpuMax} />
            ))}
          </tbody>
        </table>

        {rows.length === 0 && (
          <p className="px-3 py-8 text-center text-sm text-muted-foreground">
            No provider matches these filters. That is a filter answer, not an
            empty network — {providers.length} are listed.
          </p>
        )}
      </div>

      {/* Two readings a reader would otherwise have to guess at, said once,
          under the thing they are about. Sans: it is prose, and `.meta`'s
          mono face is for machine-emitted strings. */}
      <p className="mt-2.5 max-w-prose text-[11px] leading-relaxed text-muted-foreground">
        The CPU bar is this provider&apos;s core count against the largest
        provider listed — no per-provider commitment is reported yet, so it is
        a size, not a utilisation. A success rate needs 5 resolved attempts
        before it means anything; below that the column says so.
      </p>
    </section>
  );
}

function Row({
  provider: p,
  cpuMax,
}: {
  provider: Provider;
  /** The largest core count on the whole list, so the battery in this row is
   * drawn against the same scale as every other row's — recomputing it from
   * the FILTERED rows would make the bars rescale as you type. */
  cpuMax: number | null;
}) {
  const up = uptimeStat(p);
  const success = successStat(p.leases);
  const place = locationShort(p.location);
  const chips = gpuChips(p.specs);
  const memory = bytesText(p.specs.memory_bytes);

  return (
    <tr className="transition-colors hover:bg-surface-2">
      <td className="px-3 py-2.5">
        <Link
          href={`/market/providers/${encodeURIComponent(p.id)}`}
          className="flex min-w-0 items-center gap-2"
        >
          <span
            className="status-dot"
            aria-hidden
            style={{
              background: p.online
                ? "var(--node-green)"
                : "var(--muted-foreground)",
            }}
          />
          <span className="min-w-0 truncate font-mono hover:underline">
            {p.label}
          </span>
          <OfficialChip provider={p} />
          {/* The one thing a host is scanning this table for. A word, not a
              colour: "mine" has to survive a greyscale print and a reader who
              does not know the legend. */}
          {p.own && (
            <span className="shrink-0 rounded border border-primary/40 bg-primary/10 px-1 py-px text-[10px] text-brand-foreground">
              yours
            </span>
          )}
          <span className="sr-only">{p.online ? "online" : "offline"}</span>
        </Link>
      </td>

      <td className="px-2 py-2.5">
        {place ?? <span className="text-muted-foreground">—</span>}
      </td>

      <td className={`px-2 py-2.5 text-right tabular-nums ${TONE_CLASS[up.tone]}`}>
        <span title={up.detail}>{up.text}</span>
      </td>

      <td className="px-2 py-2.5 text-right">
        <span className="font-mono text-[13px] font-semibold tabular-nums">
          {p.leases.active}
        </span>
      </td>

      <td
        className={`px-2 py-2.5 text-right tabular-nums ${TONE_CLASS[success.tone]}`}
      >
        <span
          title={success.detail}
          className={success.tone === "unknown" ? "text-[10.5px]" : undefined}
        >
          {success.text}
        </span>
      </td>

      <td className="px-2 py-2.5">
        {p.specs.cpu_cores === null ? (
          <span className="text-muted-foreground">not reported</span>
        ) : (
          <span className="flex items-center gap-1.5">
            <MeterGlyph
              fraction={cpuMax === null ? 0 : p.specs.cpu_cores / cpuMax}
              label={`${p.specs.cpu_cores} cores${
                cpuMax === null ? "" : ` of the largest listed ${cpuMax}`
              }`}
            />
            <span className="font-mono tabular-nums">
              {p.specs.cpu_cores}
              <span className="text-muted-foreground"> vCPU</span>
            </span>
          </span>
        )}
      </td>

      <td className="px-2 py-2.5">
        <GpuChips chips={chips} limit={2} showVram={false} />
      </td>

      <td className="px-2 py-2.5 font-mono tabular-nums">
        {memory ?? <span className="font-sans text-muted-foreground">not reported</span>}
      </td>

      <td className="px-3 py-2.5 text-right">
        <TrustBadge badge={p.badge} />
      </td>
    </tr>
  );
}

/** A sortable column head. The arrow is drawn only on the active column —
 * three arrows in a header row is a decoration, one is a state. */
function Th({
  children,
  col,
  sort,
  desc,
  onSort,
  align = "left",
}: {
  children: React.ReactNode;
  col: SortKey;
  sort: SortKey;
  desc: boolean;
  onSort: (key: SortKey) => void;
  align?: "left" | "right";
}) {
  const active = sort === col;
  return (
    <th
      scope="col"
      className={`px-2 py-2 first:pl-3 ${align === "right" ? "text-right" : "text-left"}`}
      aria-sort={active ? (desc ? "descending" : "ascending") : "none"}
    >
      <button
        type="button"
        onClick={() => onSort(col)}
        className={`label-caps inline-flex items-center gap-1 transition-colors hover:text-foreground ${
          active ? "text-foreground" : ""
        }`}
      >
        {children}
        <span aria-hidden className="text-[9px] leading-none">
          {active ? (desc ? "▼" : "▲") : ""}
        </span>
      </button>
    </th>
  );
}

function Check({
  label,
  checked,
  onChange,
  count,
}: {
  label: string;
  checked: boolean;
  onChange: (next: boolean) => void;
  count: number;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-1.5 text-xs">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="h-3.5 w-3.5 shrink-0 rounded border-border accent-primary"
      />
      <span>{label}</span>
      <span className="font-mono tabular-nums text-muted-foreground">
        {count}
      </span>
    </label>
  );
}
