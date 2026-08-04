"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  BookOpen,
  CaretDown,
  GithubLogo,
  House,
  ListChecks,
  MagnifyingGlass,
  Plus,
  Desktop,
  DeviceMobile,
  SidebarSimple,
  UsersThree,
  UserCircle,
} from "@phosphor-icons/react";
import { FleetPill } from "@/components/shell/FleetPill";
import { CommandPalette } from "@/components/shell/CommandPalette";
import { InviteGate } from "@/components/shell/InviteGate";
import { Shortcuts } from "@/components/shell/Shortcuts";
import { Wordmark } from "@/components/brand/Mark";
import { UserMenu } from "@/components/nav/UserMenu";
import { NotAuthenticated, getMe } from "@/lib/cloud-api";

// The console shell. A left rail rather than a top nav: a top bar is a
// marketing pattern, and the rail gives the fleet state somewhere permanent
// to live.
//
// Routes that do not exist yet are NOT listed. "Activity" and "Reliability"
// are specced (P3) but depend on read endpoints that are not built, and a
// nav item that leads nowhere is worse than a missing one.

const GROUPS = [
  {
    label: null,
    items: [{ href: "/overview", label: "Overview", icon: House }],
  },
  {
    label: "Run",
    items: [
      { href: "/jobs", label: "Jobs", icon: ListChecks },
      { href: "/submit", label: "Submit", icon: Plus },
    ],
  },
  {
    label: "Fleet",
    items: [
      { href: "/machines", label: "Machines", icon: Desktop },
      { href: "/activate", label: "Activate", icon: DeviceMobile },
      { href: "/pools", label: "Pools", icon: UsersThree },
    ],
  },
] as const;

const REPO = "https://github.com/Zolli-Labs/flashml";

// `/pools/join` is the one console route an admitted-false account must
// still be able to reach: it is how a signed-in-but-not-yet-admitted user
// redeems an invite by clicking a link rather than pasting one into the
// gate below. Joining and admission are one signal, not two: `acceptInvite`'s
// `joined` (`lib/cloud-api.ts`) is `true` only for an already-admitted
// caller, who is added to the pool outright. For anyone else nothing joins
// yet — the membership is banked on the account's access request and
// materializes only once an admin approves them.
// The API's own `accept_invite` mirrors the reachability requirement — it
// sits on `current_user`, not `admitted_user`, for the identical reason.
const INVITE_GATE_BYPASS = "/pools/join";

function NavItem({
  href,
  label,
  icon: Icon,
  active,
}: {
  href: string;
  label: string;
  icon: React.ElementType;
  active: boolean;
}) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={`flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors ${
        active
          ? "bg-white/[0.07] font-medium text-foreground"
          : "text-muted-foreground hover:bg-white/[0.04] hover:text-foreground"
      }`}
    >
      <Icon size={17} weight={active ? "fill" : "regular"} />
      {label}
    </Link>
  );
}

function Group({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div className="mt-5">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground"
      >
        {label}
        <CaretDown
          size={11}
          weight="bold"
          className={`transition-transform duration-200 ${open ? "" : "-rotate-90"}`}
        />
      </button>
      {open && <div className="mt-1 space-y-0.5">{children}</div>}
    </div>
  );
}

export function ConsoleShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [railOpen, setRailOpen] = useState(false);

  // "admitted" is the optimistic default, not "unknown": this shell mounts
  // once for the whole console session (Next keeps a layout instance across
  // client navigations within it), so the alternative is a real console
  // page flashing to a loading state on every first paint while `GET /me`
  // is in flight — for the overwhelming majority of loads, an already
  // -admitted returning user. Nothing this gate protects is enforced only
  // here: every state-creating route re-checks admission server-side, so
  // rendering optimistically for the one round trip this takes costs
  // nothing but UI politeness.
  const [gated, setGated] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((me) => {
        if (!cancelled) setGated(!me.admitted);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof NotAuthenticated) {
          // `window.location`, not `usePathname()`: the latter never
          // includes the query string, and `/pools/join?token=...` needs
          // that token to survive the round trip through sign-in or the
          // invite this is protecting is lost.
          const next = window.location.pathname + window.location.search;
          router.push(`/sign-in?next=${encodeURIComponent(next)}`);
          return;
        }
        // A transient failure (network blip, 500) is not evidence of "not
        // admitted" — fail open rather than lock an admitted user out of
        // their own console over one bad request. Every page under here
        // already handles its own load failures.
      });
    return () => {
      cancelled = true;
    };
    // Deliberately mount-only. `router` is stable across renders and
    // `window.location` is read fresh inside the callback above when it's
    // actually needed, so neither belongs in this list — the alternative,
    // depending on something that changes with navigation, would mean one
    // `GET /me` per page visited instead of one per console session.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const showGate = gated && pathname !== INVITE_GATE_BYPASS;

  const isActive = (href: string) =>
    pathname === href || pathname.startsWith(`${href}/`);

  const rail = (
    <>
      <div className="flex h-14 items-center px-4">
        <Link href="/" aria-label="FlashML home">
          <Wordmark />
        </Link>
      </div>

      {/* The ⌘K affordance. It was advertised in the rail design and never
          built; a shortcut hint that does nothing is worse than no hint. */}
      <div className="px-3 pb-2">
        <button
          type="button"
          onClick={() => {
            // Same path the global listener takes, so there is one way in.
            window.dispatchEvent(
              new KeyboardEvent("keydown", { key: "k", metaKey: true })
            );
          }}
          className="flex w-full items-center gap-2 rounded-md border border-border bg-background/60 px-2.5 py-1.5 text-left text-sm text-muted-foreground transition-colors hover:bg-white/[0.04] hover:text-foreground"
        >
          <MagnifyingGlass size={14} />
          <span className="flex-1">Search</span>
          <kbd className="meta rounded border border-border px-1 py-px">⌘K</kbd>
        </button>
        <p className="meta mt-1.5 px-2.5">
          Press <span className="text-foreground">?</span> for shortcuts
        </p>
      </div>

      <nav className="flex-1 overflow-y-auto px-3 pb-4">
        {GROUPS.map((g, i) =>
          g.label === null ? (
            <div key={i} className="space-y-0.5">
              {g.items.map((it) => (
                <NavItem key={it.href} {...it} active={isActive(it.href)} />
              ))}
            </div>
          ) : (
            <Group key={g.label} label={g.label}>
              {g.items.map((it) => (
                <NavItem key={it.href} {...it} active={isActive(it.href)} />
              ))}
            </Group>
          )
        )}
      </nav>

      <div className="space-y-0.5 border-t border-border px-3 py-3">
        <NavItem
          href="/docs"
          label="Docs"
          icon={BookOpen}
          active={isActive("/docs")}
        />
        <NavItem
          href="/account"
          label="Account"
          icon={UserCircle}
          active={isActive("/account")}
        />
        <a
          href={REPO}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm text-muted-foreground transition-colors hover:bg-white/[0.04] hover:text-foreground"
        >
          <GithubLogo size={17} />
          Source
        </a>
      </div>
    </>
  );

  return (
    <div className="flex min-h-dvh">
      <CommandPalette />
      <Shortcuts />
      {/* Desktop rail. A step DARKER than the content column, so the content
          reads as the lit surface. */}
      <aside className="hidden w-[248px] shrink-0 flex-col border-r border-border bg-bg-rail lg:flex">
        {rail}
      </aside>

      {/* Mobile drawer. Rendered only when open so it cannot trap focus or
          take clicks while hidden. */}
      {railOpen && (
        <>
          <button
            type="button"
            aria-label="Close navigation"
            onClick={() => setRailOpen(false)}
            className="fixed inset-0 z-40 bg-black/60 lg:hidden"
          />
          <aside className="fixed inset-y-0 left-0 z-50 flex w-[248px] flex-col border-r border-border bg-bg-rail lg:hidden">
            {rail}
          </aside>
        </>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-3 border-b border-border bg-background px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-2">
            <button
              type="button"
              onClick={() => setRailOpen(true)}
              aria-label="Open navigation"
              className="-ml-1 rounded-md p-1.5 text-muted-foreground hover:bg-white/[0.06] hover:text-foreground lg:hidden"
            >
              <SidebarSimple size={18} />
            </button>
          </div>
          <div className="flex items-center gap-3">
            <FleetPill />
            <UserMenu />
          </div>
        </header>

        <main id="content" className="min-w-0 flex-1">
          {showGate ? <InviteGate /> : children}
        </main>
      </div>
    </div>
  );
}
