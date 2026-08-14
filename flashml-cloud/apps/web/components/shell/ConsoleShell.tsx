"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Fragment, useEffect, useState } from "react";
import {
  BookOpen,
  ChartLineUp,
  Gear,
  GithubLogo,
  House,
  ListChecks,
  MagnifyingGlass,
  Desktop,
  RocketLaunch,
  ShieldCheck,
  SidebarSimple,
  UsersThree,
  type Icon,
} from "@phosphor-icons/react";
import { FleetPill } from "@/components/shell/FleetPill";
import { CommandPalette } from "@/components/shell/CommandPalette";
import { Shortcuts } from "@/components/shell/Shortcuts";
import { StorageWarningBanner } from "@/components/shell/StorageWarningBanner";
import { ThemeSwitcher } from "@/components/shell/ThemeSwitcher";
import { WorkspaceHintProvider } from "@/components/shell/WorkspaceHint";
import { WorkspaceSwitcher } from "@/components/shell/WorkspaceSwitcher";
import { Wordmark } from "@/components/brand/Mark";
import { UserMenu } from "@/components/nav/UserMenu";
import { DeclinedScreen } from "@/components/onboarding/DeclinedScreen";
import { OnboardingForm } from "@/components/onboarding/OnboardingForm";
import { PendingScreen } from "@/components/onboarding/PendingScreen";
import { screenFor } from "@/lib/access-screen";
import { useSessionUser } from "@/lib/session-user";
import {
  WORKSPACE_TABS,
  workspaceIdFromPath,
  workspacePath,
  type WorkspaceTab,
} from "@/lib/workspace-scope";
import {
  NotAuthenticated,
  getMe,
  type AccessState,
  type Profile,
} from "@/lib/cloud-api";

// The console shell. A left rail rather than a top nav: a top bar is a
// marketing pattern, and the rail gives the fleet state somewhere permanent
// to live.

const REPO = "https://github.com/Zolli-Labs/flashml";

// One entry per `WORKSPACE_TABS` slot, keyed by tab rather than listed, so
// that adding a tab to `WORKSPACE_TABS` (lib/workspace-scope.ts) without
// adding it here is a compile error, not a silently missing nav item.
const WORKSPACE_TAB_ITEMS: Record<
  WorkspaceTab,
  { label: string; icon: Icon }
> = {
  overview: { label: "Overview", icon: House },
  jobs: { label: "Jobs", icon: ListChecks },
  machines: { label: "Machines", icon: Desktop },
  people: { label: "People", icon: UsersThree },
  settings: { label: "Settings", icon: Gear },
};

// The admin queue. Rendered separately from the tab list below because its
// presence depends on the signed-in account rather than on the route table,
// and `/admin` has no page of its own — the queue is the destination.
const ADMIN_ITEM = {
  href: "/admin/requests",
  label: "Admin",
  icon: ShieldCheck,
} as const;

function NavItem({
  href,
  label,
  icon: Icon,
  active,
  compact = false,
}: {
  href: string;
  label: string;
  icon: Icon;
  active: boolean;
  /** The footer's Help link reads a step quieter than the section items
   * above it — `text-xs`, matching the hand-rolled Source link beside it —
   * rather than the general `text-[13px]` nav size. */
  compact?: boolean;
}) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      // `before:-left-3` is not a magic number: it pairs with the nav
      // container's own `px-3` below (`<nav className="... px-3 ...">`),
      // which is exactly 0.75rem — so the bar escapes this item's padding
      // and lands flush against the RAIL's own left edge rather than
      // floating inset from it. If that container's padding ever changes,
      // this offset has to change with it.
      className={`relative flex items-center gap-2.5 rounded-md px-2.5 py-1.5 ${compact ? "text-xs" : "text-[13px]"} transition-colors ${
        active
          ? "bg-primary/8 text-foreground before:absolute before:-left-3 before:top-0 before:bottom-0 before:w-0.5 before:bg-primary before:content-['']"
          : "text-muted-foreground hover:bg-surface hover:text-foreground"
      }`}
    >
      <Icon size={16} weight={active ? "fill" : "regular"} />
      {label}
    </Link>
  );
}

export function ConsoleShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const session = useSessionUser();
  const [railOpen, setRailOpen] = useState(false);

  // `undefined` is "not answered yet", and `screenFor` maps it to the
  // console — an optimistic default, not a loading state. This shell mounts
  // once for the whole console session (Next keeps a layout instance across
  // client navigations within it), so the alternative is a real console
  // page flashing to a loading state on every first paint while `GET /me`
  // is in flight — for the overwhelming majority of loads, an already
  // -admitted returning user. Nothing this gate protects is enforced only
  // here: every state-creating route re-checks admission server-side, so
  // rendering optimistically for the one round trip this takes costs
  // nothing but UI politeness.
  const [access, setAccess] = useState<AccessState | undefined>(undefined);
  // The same `GET /me` answer, kept whole so the rail can read `is_admin`
  // off it. A second request for one boolean would be one more round trip
  // on every console session for a flag we already have.
  const [profile, setProfile] = useState<Profile | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((me) => {
        if (cancelled) return;
        setProfile(me);
        setAccess(me.access);
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
        // their own console over one bad request. Leaving `access`
        // `undefined` is exactly that: `screenFor` keeps showing the
        // console. Every page under here already handles its own load
        // failures.
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

  const screen = screenFor(access, pathname);

  const isActive = (href: string) =>
    pathname === href || pathname.startsWith(`${href}/`);

  // A workspace-scoped URL is the authority. `/jobs/[jobId]` is not one —
  // it is the normal destination from the Jobs tab and from Overview, and
  // its path carries no pool id, so the rail used to empty out the moment
  // you opened a job. The page itself knows (`job.pool_id`) and reports it
  // through `WorkspaceHintProvider` below; the URL still wins wherever it
  // says anything at all. See components/shell/WorkspaceHint.tsx.
  const [workspaceHint, setWorkspaceHint] = useState<string | null>(null);
  const currentWorkspace = workspaceIdFromPath(pathname) ?? workspaceHint;

  const rail = (
    <>
      {/* `h-12`, matching the top bar's own `h-12` below — the rail and the
          content column share one horizontal seam across the whole console,
          and the two used to disagree by 14px (`h-16` here against the old
          `h-[62px]` top bar). */}
      <div className="flex h-12 items-center px-4">
        <Link href="/" aria-label="Zolli Cloud home">
          <Wordmark product tone="dark" />
        </Link>
      </div>

      {/* Everything between the wordmark and the footer is navigation to
          pages a non-console screen cannot use: an account waiting on
          approval is shown the same waiting screen whichever of them it
          lands on. A nav — or a search box over the same routes — that
          leads nowhere useful is worse than none, so both are hidden and
          the wordmark, account, docs and source links stay. */}
      {screen === "console" && (
        <>
          <WorkspaceSwitcher currentId={currentWorkspace} />

          {/* The ⌘K affordance. It was advertised in the rail design and
              never built; a shortcut hint that does nothing is worse than
              no hint. */}
          <div className="px-3 pb-2">
            <button
              type="button"
              onClick={() => {
                // Same path the global listener takes, so there is one way
                // in.
                window.dispatchEvent(
                  new KeyboardEvent("keydown", { key: "k", metaKey: true })
                );
              }}
              className="flex w-full items-center gap-2 rounded-md border border-border bg-surface px-2.5 py-1 text-left text-[13px] text-muted-foreground transition-colors hover:border-primary/30 hover:bg-surface-2 hover:text-foreground"
            >
              <MagnifyingGlass size={14} />
              <span className="flex-1">Search</span>
              <kbd className="meta rounded border border-border px-1 py-px">
                ⌘K
              </kbd>
            </button>
            <p className="meta mt-1.5 px-2.5">
              Press <span className="text-foreground">?</span> for shortcuts
            </p>
          </div>

          {/* `min-h-0`: the rail is exactly viewport-tall, so the nav must be
              allowed to shrink below its content and scroll on short
              viewports instead of pushing the footer off-screen. */}
          <nav className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
            {currentWorkspace ? (
              <div className="space-y-0.5">
                {WORKSPACE_TABS.map((tab) => {
                  const { label, icon } = WORKSPACE_TAB_ITEMS[tab];
                  const href = workspacePath(currentWorkspace, tab);
                  const item = (
                    <NavItem
                      key={tab}
                      href={href}
                      label={label}
                      icon={icon}
                      active={isActive(href)}
                    />
                  );
                  if (tab !== "overview") return item;
                  // Deploy sits directly under Overview: the workspace's
                  // submit route, deliberately not a WorkspaceTab
                  // (lib/workspace-scope.ts keeps that union for the pages
                  // every workspace always has) — rendered here so the one
                  // action the product exists for is one click from
                  // anywhere.
                  const deployHref = workspacePath(currentWorkspace, "submit");
                  return (
                    <Fragment key={tab}>
                      {item}
                      <NavItem
                        href={deployHref}
                        label="Deploy"
                        icon={RocketLaunch}
                        active={isActive(deployHref)}
                      />
                    </Fragment>
                  );
                })}
              </div>
            ) : (
              // No workspace resolved yet: the same three destinations,
              // through the resolver routes, so the rail never renders
              // empty. Each one forwards into the right workspace (or to
              // /workspaces when there is none to resolve).
              <div className="space-y-0.5">
                <NavItem
                  href="/overview"
                  label="Overview"
                  icon={House}
                  active={isActive("/overview")}
                />
                <NavItem
                  href="/deploy"
                  label="Deploy"
                  icon={RocketLaunch}
                  active={isActive("/deploy")}
                />
                <NavItem
                  href="/jobs"
                  label="Jobs"
                  icon={ListChecks}
                  active={isActive("/jobs")}
                />
              </div>
            )}

            {/* Cross-workspace surfaces. Market and the personally-owned
                fleet are not scoped to any workspace, so they live in their
                own group under whatever workspace group renders above.
                Each is ONE destination — its inner pages (Prices · Listings
                · Credits; My machines · Add) are in-page tabs, not rail
                items. */}
            <div className="mt-5 space-y-0.5">
              {/* Not `.label-caps` (11px, `--muted-foreground`/secondary
                  tone) — the instrument-panel register's section labels are
                  a step smaller and a step dimmer, `--z-app-text-dim`,
                  which has no bare Tailwind colour utility of its own. */}
              <p className="px-2.5 pb-1 text-[10px] uppercase tracking-[0.07em] text-[var(--z-app-text-dim)]">
                Global
              </p>
              <NavItem
                href="/market/prices"
                label="Market"
                icon={ChartLineUp}
                active={isActive("/market")}
              />
              <NavItem
                href="/machines"
                label="My machines"
                icon={Desktop}
                active={isActive("/machines")}
              />
            </div>

            {/* Hidden until `GET /me` says otherwise, so a non-admin never
                sees it flash. The API enforces admin on every queue route
                regardless — this only decides whether the door is drawn. */}
            {profile?.is_admin && (
              <div className="mt-5 space-y-0.5">
                <NavItem {...ADMIN_ITEM} active={isActive(ADMIN_ITEM.href)} />
              </div>
            )}
          </nav>
        </>
      )}

      {/* Two entries, deliberately: Help is the door to every explanatory
          page (docs links how-it-works and reliability from inside), and
          Settings lives behind the avatar menu. The old five-item footer
          was a second nav competing with the first. */}
      <div className="mt-auto space-y-0.5 border-t border-border px-3 py-3">
        <NavItem
          href="/docs"
          label="Help"
          icon={BookOpen}
          active={isActive("/docs")}
          compact
        />
        <a
          href={REPO}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-surface hover:text-foreground"
        >
          <GithubLogo size={16} />
          Source
        </a>
      </div>
    </>
  );

  return (
    // `console-theme` is the marker `body:has(.console-theme)` in
    // globals.css looks for — see that block for why the dark tokens live on
    // `body` rather than on this element directly (the Toaster and Base UI's
    // popups portal past it). This root wraps every screen this component
    // renders, including the three access-gate states below (loading /
    // onboarding / pending / declined), so those follow the theme too rather
    // than staying stuck light while the console around a first-time visitor
    // goes dark.
    <div className="console-theme flex min-h-dvh">
      <CommandPalette />
      <Shortcuts />
      {/* `sticky top-0 self-start h-dvh`: the rail is pinned to the viewport
          instead of stretched with the document, so however far the content
          column scrolls, the dark column — and its footer nav — stays
          full-height on screen, the same guarantee the mobile drawer gets
          from `fixed inset-y-0`. `self-start` opts the item out of stretch so
          sticky has room to travel; `h-dvh` then sizes it to the viewport. */}
      <aside className="console-rail sticky top-0 hidden h-dvh w-[232px] shrink-0 flex-col self-start border-r border-border bg-bg-rail text-foreground lg:flex">
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
          <aside className="console-rail fixed inset-y-0 left-0 z-50 flex w-[232px] flex-col border-r border-border bg-bg-rail text-foreground shadow-xl lg:hidden">
            {rail}
          </aside>
        </>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-12 items-center justify-between gap-3 border-b border-border bg-surface px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-2">
            <button
              type="button"
              onClick={() => setRailOpen(true)}
              aria-label="Open navigation"
              className="-ml-1 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-surface-2 hover:text-foreground lg:hidden"
            >
              <SidebarSimple size={18} />
            </button>
          </div>
          <div className="flex items-center gap-3">
            <FleetPill />
            <ThemeSwitcher />
            <UserMenu />
          </div>
        </header>

        <main id="content" className="min-w-0 flex-1 bg-[var(--z-app-canvas)]">
          {screen !== "console" ? (
            // Centred here, once, for all three access screens. Each of them
            // is a self-contained card with its own `max-w-*`, and dropping
            // one straight into this `flex-1` main left it flush against the
            // rail — visibly off-centre, and worse the wider the window. The
            // same treatment `app/(auth)/sign-in/page.tsx` gives its card, so
            // signing up and being asked to wait look like one flow rather
            // than two unrelated screens.
            // `min-h-[calc(100dvh-3rem)]`, NOT `min-h-full`: a percentage
            // min-height resolves against the parent's height, and `<main>`
            // is `flex-1` with no definite height — so `min-h-full` computed
            // to nothing, and a form taller than the viewport overflowed
            // with its end unreachable. 3rem is the `h-12` header above.
            //
            // `min-h-*` and not `h-*`: with a fixed height plus
            // `items-center`, content taller than the box overflows in both
            // directions and the overflow cannot be scrolled to. A minimum
            // lets the container grow instead, so short cards centre and
            // tall ones simply make the page scroll.
            <div className="flex min-h-[calc(100dvh-3rem)] items-center justify-center px-4 py-12">
              {screen === "loading" ? (
                // Admin routes only — see ADMIN_ROUTE in lib/access-screen.
                // Deliberately says nothing about the page behind it: the
                // whole point is not to render the admin queue to somebody
                // who may turn out not to be an admin.
                <p className="meta" role="status">
                  Checking your access…
                </p>
              ) : screen === "onboarding" ? (
                // Not a route of its own: the form has to stand in for
                // whatever page the user asked for, or a bookmarked `/jobs`
                // would render the product to an account that has not asked
                // for it yet. `setAccess` rather than a reload — the API has
                // already told us the new state in its response.
                //
                // This is the RESUME path. The first-time path is step 2 of
                // signup (`SignInCard`), which renders this same component;
                // people reach this one by abandoning that step, by holding
                // an account made before signup asked, or by confirming an
                // email when "Confirm email" is on. One form, three ways in.
                <OnboardingForm onSubmitted={() => setAccess("pending")} />
              ) : screen === "pending" ? (
                <PendingScreen email={session?.email ?? null} />
              ) : (
                <DeclinedScreen />
              )}
            </div>
          ) : (
            <WorkspaceHintProvider onHint={setWorkspaceHint}>
              {/* Ahead of every workspace page's own content, not inside
                  it — a job's own submit tab is exactly where this matters
                  most, but nothing about that tab loads before this banner
                  has had its chance to. */}
              <StorageWarningBanner />
              {children}
            </WorkspaceHintProvider>
          )}
        </main>
      </div>
    </div>
  );
}
