"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Desktop,
  DeviceMobile,
  House,
  ListChecks,
  MagnifyingGlass,
  Plus,
  BookOpen,
  UserCircle,
} from "@phosphor-icons/react";
import {
  listJobs,
  listMachines,
  NotAuthenticated,
  type JobRecord,
  type Machine,
} from "@/lib/cloud-api";

// The palette the rail advertised with a ⌘K hint and never had.
//
// Loads jobs and machines ONCE, when first opened, rather than on mount:
// most sessions never press ⌘K, and two API calls on every page load to
// populate a menu nobody opened is the kind of cost that shows up as a slow
// console rather than as a feature.

interface Item {
  id: string;
  label: string;
  hint?: string;
  href: string;
  icon: React.ElementType;
  group: string;
}

const STATIC: Item[] = [
  { id: "nav-overview", label: "Overview", href: "/overview", icon: House, group: "Go to" },
  { id: "nav-jobs", label: "Jobs", href: "/jobs", icon: ListChecks, group: "Go to" },
  { id: "nav-submit", label: "Submit a job", href: "/submit", icon: Plus, group: "Go to" },
  { id: "nav-machines", label: "Zollis", href: "/machines", icon: Desktop, group: "Go to" },
  { id: "nav-activate", label: "Add a Zolli", href: "/activate", icon: DeviceMobile, group: "Go to" },
  { id: "nav-docs", label: "Documentation", href: "/docs", icon: BookOpen, group: "Go to" },
  { id: "nav-account", label: "Account", href: "/account", icon: UserCircle, group: "Go to" },
];

/** Subsequence match, the behaviour people expect from a palette: "fmr"
 * finds "federated-mlp-r000". A plain `includes` would not, and a fuzzy
 * library is a dependency for thirty lines of code. */
function matches(haystack: string, needle: string): boolean {
  if (!needle) return true;
  const h = haystack.toLowerCase();
  const n = needle.toLowerCase();
  let i = 0;
  for (const ch of h) {
    if (ch === n[i]) i++;
    if (i === n.length) return true;
  }
  return false;
}

export function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [machines, setMachines] = useState<Machine[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  const loadOnce = useCallback(() => {
    if (loaded) return;
    setLoaded(true);
    // Best effort, and silent. A palette that pops an error toast because
    // the API was briefly unreachable is worse than one that offers only
    // the static destinations.
    listJobs().then(setJobs).catch((e) => {
      if (e instanceof NotAuthenticated) setJobs([]);
    });
    listMachines().then(setMachines).catch((e) => {
      if (e instanceof NotAuthenticated) setMachines([]);
    });
  }, [loaded]);

  // Opening resets and loads HERE, in the handler, not in an effect watching
  // `open`. Setting state synchronously in an effect body is what
  // react-hooks/set-state-in-effect flags, and an event is the honest place
  // for "the user opened the palette" anyway.
  const openPalette = useCallback(() => {
    setQuery("");
    setCursor(0);
    setOpen(true);
    loadOnce();
  }, [loadOnce]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => {
          if (o) return false;
          openPalette();
          return true;
        });
      }
      if (e.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openPalette]);

  // The only effect left does a DOM side effect, not a state write: the
  // input does not exist until the dialog has rendered.
  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => inputRef.current?.focus(), 0);
    return () => clearTimeout(t);
  }, [open]);

  const items = useMemo(() => {
    const dynamic: Item[] = [
      ...jobs.map((j) => ({
        id: `job-${j.job_id}`,
        label: j.spec?.metadata?.name ?? j.name ?? j.job_id,
        hint: j.state,
        href: `/jobs/${j.job_id}`,
        icon: ListChecks,
        group: "Jobs",
      })),
      ...machines.map((m) => ({
        id: `machine-${m.id}`,
        label: m.name ?? m.node_id,
        hint: m.status,
        href: "/machines",
        icon: Desktop,
        group: "Zollis",
      })),
    ];
    return [...STATIC, ...dynamic].filter(
      (it) => matches(it.label, query) || matches(it.hint ?? "", query)
    );
  }, [jobs, machines, query]);

  // Clamp rather than reset: retyping should not throw the cursor back to
  // the top on every keystroke.
  const active = Math.min(cursor, Math.max(items.length - 1, 0));

  function go(item: Item | undefined) {
    if (!item) return;
    setOpen(false);
    router.push(item.href);
  }

  if (!open) return null;

  let lastGroup = "";

  return (
    <div className="fixed inset-0 z-[100] flex items-start justify-center px-4 pt-[12vh]">
      <button
        type="button"
        aria-label="Close command palette"
        onClick={() => setOpen(false)}
        className="absolute inset-0 bg-black/60 backdrop-blur-[2px]"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="glass-strong relative w-full max-w-lg overflow-hidden rounded-xl"
      >
        <div className="flex items-center gap-2.5 border-b border-border px-4">
          <MagnifyingGlass size={15} className="shrink-0 text-muted-foreground" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setCursor((c) => Math.min(c + 1, items.length - 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setCursor((c) => Math.max(c - 1, 0));
              } else if (e.key === "Enter") {
                e.preventDefault();
                go(items[active]);
              }
            }}
            placeholder="Jump to a job, a Zolli, or a page"
            aria-activedescendant={items[active]?.id}
            className="w-full bg-transparent py-3.5 text-sm outline-none placeholder:text-muted-foreground/70"
          />
          <kbd className="meta shrink-0 rounded border border-border px-1.5 py-0.5">
            esc
          </kbd>
        </div>

        <ul className="max-h-[52vh] overflow-y-auto py-1.5">
          {items.length === 0 && (
            <li className="px-4 py-6 text-center text-sm text-muted-foreground">
              Nothing matches {`"${query}"`}
            </li>
          )}
          {items.map((it, i) => {
            const header = it.group !== lastGroup ? it.group : null;
            lastGroup = it.group;
            const Icon = it.icon;
            return (
              <li key={it.id} id={it.id}>
                {header && (
                  <div className="meta px-4 pb-1 pt-2.5 uppercase tracking-[0.12em]">
                    {header}
                  </div>
                )}
                <button
                  type="button"
                  onMouseEnter={() => setCursor(i)}
                  onClick={() => go(it)}
                  className={`flex w-full items-center gap-2.5 px-4 py-2 text-left text-sm ${
                    i === active ? "bg-primary/10 text-brand-foreground" : "hover:bg-surface-2"
                  }`}
                >
                  <Icon size={15} className="shrink-0 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate">{it.label}</span>
                  {it.hint && <span className="meta shrink-0">{it.hint}</span>}
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
