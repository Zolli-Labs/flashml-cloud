import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
import {
  BookOpen,
  Desktop,
  GithubLogo,
  House,
  ListChecks,
  MagnifyingGlass,
  Moon,
  RocketLaunch,
  SidebarSimple,
  Sun,
  UsersThree,
  Warning,
  type Icon,
} from "@phosphor-icons/react";
import { it } from "vitest";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/**
 * The console dark theme, rendered both ways for side-by-side comparison —
 * plus three warm/cool/atmospheric dark VARIANTS of the primitives, for
 * choosing between them from real renders rather than from hex codes.
 *
 * No session in this workspace can sign in (see `preview/console-primitives.
 * render.tsx`), and the theme toggle itself compounds that: even a session
 * that COULD sign in would still be looking at one theme at a time in one
 * browser tab. This writes the same scene several times — once with `.dark`
 * on `<html>` and once without, for the light/dark baseline comparison, and
 * three more times with each dark variant's primitives — so all of them can
 * be opened side by side without a live toggle.
 *
 * VARIANT RENDERS SET THE PRIMITIVES INLINE ON `<html>`, not by swapping
 * classes. `app/globals.css`'s `html.dark:has(.console-theme)` block sets
 * the `--z-app-*` primitives on `<html>`, and every semantic token
 * (`--surface`, `--ink`, `--background`, …) resolves its `var()` reference
 * to those primitives AT THAT SAME ELEMENT — see the comment on that block
 * and on `lib/dark-console-theme.test.ts` for why the element matters. An
 * inline `style` attribute on `<html>` sets those same custom properties on
 * that same element with higher specificity than any class rule, so the
 * semantic tokens re-resolve against the INLINE values instead — the
 * identical mechanism the class block itself relies on, one cascade layer
 * up. `console-dark.html` needs no inline style at all: `globals.css`'s
 * default dark register is now VARIANT C (chosen after comparing real
 * renders of all three — warm black, orange-warmed borders), so that file
 * is the globals-default render and `console-dark-c.html` is the same
 * scene byte-for-byte except for the `<title>`. A and B are no longer free
 * — each now needs its own inline override to render as itself.
 *
 * `ThemeSwitcher` (`components/shell/ThemeSwitcher.tsx`) itself is not
 * rendered here. It is a client component gated on `useSyncExternalStore`
 * mount detection specifically so a static/server pass never shows it
 * mid-transition — and `renderToStaticMarkup` never hydrates, so importing
 * it here would draw nothing but its fixed-size placeholder in every file,
 * which defeats the point of a visual comparison. Instead this file draws
 * the same three-segment control by hand, in the same style
 * `console-shell.render.tsx` uses for shell chrome it cannot mount live —
 * with the segment matching each file's theme marked active, so the control
 * itself is part of what gets compared.
 */
const FONT_VARS = `:root{--font-instrument-sans:ui-sans-serif,system-ui,-apple-system,sans-serif;--font-geist-mono:ui-monospace,SFMono-Regular,Menlo,monospace}`;

const webRoot = process.cwd();
const outDir = process.env.PREVIEW_OUT ?? path.join(webRoot, ".preview");

async function compiledCss(): Promise<string> {
  const globalsPath = path.join(webRoot, "app/globals.css");
  const css = readFileSync(globalsPath, "utf8");
  const result = await postcss([tailwindcss({ base: webRoot })]).process(css, {
    from: globalsPath,
  });
  return result.css;
}

/** The three-segment control, drawn statically. See the file doc comment for
 * why this is a hand-drawn stand-in rather than an import of the real
 * component — the classes below are copied from it, not invented. */
function StaticThemeSwitcher({ active }: { active: "light" | "dark" }) {
  const options = [
    { key: "light" as const, icon: Sun, label: "Light theme" },
    { key: "dark" as const, icon: Moon, label: "Dark theme" },
    { key: "system" as const, icon: Desktop, label: "System theme" },
  ];
  return (
    <div
      role="group"
      aria-label="Theme"
      className="flex h-7 items-center rounded-md border border-border p-0.5"
    >
      {options.map(({ key, icon: OptionIcon, label }) => {
        const isActive = key === active;
        return (
          <button
            key={key}
            type="button"
            aria-label={label}
            aria-pressed={isActive}
            title={label}
            className={`flex h-6 w-6 items-center justify-center rounded transition-colors ${
              isActive
                ? "bg-surface-2 text-ink"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            <OptionIcon size={14} weight={isActive ? "fill" : "regular"} />
          </button>
        );
      })}
    </div>
  );
}

/** A trimmed stand-in for `ConsoleShell`'s rail. Same classes, none of the
 * fetches — `WorkspaceSwitcher`, `FleetPill` and `UserMenu` all load live
 * data over `fetch`, which has nothing to talk to in a static render. */
function Rail() {
  const navItems: Array<{ label: string; icon: Icon; active?: boolean }> = [
    { label: "Overview", icon: House, active: true },
    { label: "Jobs", icon: ListChecks },
    { label: "Machines", icon: Desktop },
    { label: "People", icon: UsersThree },
  ];
  return (
    <aside className="console-rail sticky top-0 hidden h-dvh w-[232px] shrink-0 flex-col self-start border-r border-border bg-bg-rail text-foreground lg:flex">
      <div className="flex h-16 items-center px-4">
        <span className="font-mono text-sm font-semibold tracking-tight">
          ZOLLI
        </span>
      </div>
      <div className="px-3 pb-2">
        <div className="flex w-full items-center gap-2 rounded-md border border-border bg-surface px-2.5 py-1.5 text-sm text-muted-foreground shadow-sm">
          <MagnifyingGlass size={14} />
          <span className="flex-1">Search</span>
          <kbd className="meta rounded border border-border px-1 py-px">
            ⌘K
          </kbd>
        </div>
      </div>
      <nav className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
        <div className="space-y-0.5">
          {navItems.map(({ label, icon: NavIcon, active }) => (
            <div
              key={label}
              className={`flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm ${
                active
                  ? "bg-primary/10 font-medium text-brand-foreground"
                  : "text-muted-foreground"
              }`}
            >
              <NavIcon size={17} weight={active ? "fill" : "regular"} />
              {label}
            </div>
          ))}
          <div className="flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm text-muted-foreground">
            <RocketLaunch size={17} />
            Deploy
          </div>
        </div>
      </nav>
      <div className="mt-auto space-y-0.5 border-t border-border px-3 py-3">
        <div className="flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm text-muted-foreground">
          <BookOpen size={17} />
          Help
        </div>
        <div className="flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm text-muted-foreground">
          <GithubLogo size={17} />
          Source
        </div>
      </div>
    </aside>
  );
}

/** A trimmed stand-in for `ConsoleShell`'s top bar — the mobile-nav toggle,
 * the fleet pill, the theme switcher and the user menu. */
function TopBar({ theme }: { theme: "light" | "dark" }) {
  return (
    <header className="sticky top-0 z-30 flex h-[62px] items-center justify-between gap-3 border-b border-border bg-surface px-4 sm:px-6">
      <div className="flex min-w-0 items-center gap-2">
        <SidebarSimple size={18} className="text-muted-foreground lg:hidden" />
      </div>
      <div className="flex items-center gap-3">
        <div className="hidden items-center gap-2 rounded-[4px] border border-border bg-surface px-3 py-1.5 sm:inline-flex">
          <span
            className="status-dot"
            data-state="live"
            style={{ background: "var(--node-green)" }}
          />
          <span className="font-mono text-xs tabular-nums text-foreground">
            4
          </span>
          <span className="text-xs text-muted-foreground">machines online</span>
        </div>
        <StaticThemeSwitcher active={theme} />
        <div
          className="h-[26px] w-[26px] rounded-full"
          style={{ background: "var(--z-orange)" }}
          aria-hidden="true"
        />
      </div>
    </header>
  );
}

/** The StatePanel-shaped states, hand-drawn the way `console-shell.render.tsx`
 * draws them — see that file for the full four-state contract this stands in
 * for; this file's point is theme comparison, not re-proving that contract. */
function PanelStates() {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
        gap: "1.25rem",
      }}
    >
      <div>
        <p className="label-caps" style={{ marginBottom: "0.5rem" }}>
          loading
        </p>
        <div className="panel px-4 sm:px-5 py-4">
          <div className="space-y-2" aria-hidden="true">
            <Skeleton className="h-11" />
            <Skeleton className="h-11" />
          </div>
        </div>
      </div>
      <div>
        <p className="label-caps" style={{ marginBottom: "0.5rem" }}>
          present
        </p>
        <div className="panel px-4 sm:px-5 py-4">
          <ul style={{ display: "grid", gap: "0.35rem" }}>
            <li className="font-mono text-sm">node-a · runpod</li>
            <li className="font-mono text-sm">node-b · local</li>
          </ul>
        </div>
      </div>
      <div>
        <p className="label-caps" style={{ marginBottom: "0.5rem" }}>
          empty
        </p>
        <div className="panel px-4 sm:px-5 py-10 flex flex-col items-center gap-3 text-center">
          <p className="text-sm font-medium text-foreground">
            No machines yet
          </p>
          <p className="max-w-sm text-sm leading-relaxed text-muted-foreground">
            Add a machine to run jobs in this workspace.
          </p>
        </div>
      </div>
      <div>
        <p className="label-caps" style={{ marginBottom: "0.5rem" }}>
          unreadable
        </p>
        <div
          role="alert"
          className="panel px-4 sm:px-5 py-10 flex items-start gap-3"
        >
          <Warning
            className="mt-0.5 h-5 w-5 shrink-0 text-destructive"
            weight="fill"
            aria-hidden="true"
          />
          <div className="min-w-0">
            <p className="text-sm font-medium text-foreground">
              Could not read this.
            </p>
            <p className="mt-1 max-w-prose text-sm leading-relaxed text-muted-foreground">
              The API returned 502.
            </p>
            <Button variant="outline" size="sm" className="mt-3.5">
              Try again
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function Content() {
  return (
    <main
      id="content"
      className="min-w-0 flex-1 bg-[var(--z-app-canvas)]"
      style={{ padding: "2.5rem 2rem" }}
    >
      <h1 className="title" style={{ marginBottom: "0.35rem" }}>
        Machines
      </h1>
      <p className="meta" style={{ marginBottom: "2rem" }}>
        A representative spread of console primitives, rendered for a visual
        theme comparison. No live data.
      </p>

      <section style={{ marginBottom: "2.5rem" }}>
        <h2 className="label-caps" style={{ marginBottom: "0.75rem" }}>
          Metrics
        </h2>
        <div style={{ display: "flex", gap: "2rem" }}>
          <div>
            <p className="metric-lg">128</p>
            <p className="meta">machines contributed</p>
          </div>
          <div>
            <p className="metric-lg">0.958</p>
            <p className="meta">best accuracy</p>
          </div>
        </div>
      </section>

      <section style={{ marginBottom: "2.5rem" }}>
        <h2 className="label-caps" style={{ marginBottom: "0.75rem" }}>
          Buttons &amp; badges
        </h2>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem", alignItems: "center" }}>
          <Button>default</Button>
          <Button variant="outline">outline</Button>
          <Button variant="secondary">secondary</Button>
          <Button variant="ghost">ghost</Button>
          <Button variant="destructive">destructive</Button>
          <Badge>default</Badge>
          <Badge variant="secondary">secondary</Badge>
          <Badge variant="outline">outline</Badge>
          <Badge variant="destructive">destructive</Badge>
          <span
            className="status-dot"
            data-state="live"
            style={{ background: "var(--node-green)" }}
          />
          <span
            className="status-dot"
            style={{ background: "var(--muted-foreground)" }}
          />
        </div>
      </section>

      <section style={{ marginBottom: "2.5rem" }}>
        <h2 className="label-caps" style={{ marginBottom: "0.75rem" }}>
          Table
        </h2>
        <div className="panel" style={{ padding: "0.5rem" }}>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Machine</TableHead>
                <TableHead>State</TableHead>
                <TableHead>Venue</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow>
                <TableCell className="font-mono">node-a</TableCell>
                <TableCell>
                  <Badge variant="secondary">online</Badge>
                </TableCell>
                <TableCell>runpod</TableCell>
              </TableRow>
              <TableRow>
                <TableCell className="font-mono">node-b</TableCell>
                <TableCell>
                  <Badge variant="outline">not observed</Badge>
                </TableCell>
                <TableCell>local</TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
      </section>

      <section>
        <h2 className="label-caps" style={{ marginBottom: "0.75rem" }}>
          Panel states
        </h2>
        <PanelStates />
      </section>
    </main>
  );
}

/** The full scene, wrapped in `console-theme` — the marker
 * `html.dark:has(.console-theme)` in `globals.css` looks for. Identical
 * markup renders into every file this harness writes; only the `<html>`
 * wrapper around it differs (the `dark` class, and for the variant renders
 * below, an inline `style`), which is exactly the variable these files exist
 * to isolate. */
function Scene({ theme }: { theme: "light" | "dark" }) {
  return (
    <div className="console-theme flex min-h-dvh">
      <Rail />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar theme={theme} />
        <Content />
      </div>
    </div>
  );
}

/** `htmlStyle`, when given, becomes the INLINE `style` attribute on
 * `<html>` — see the file doc comment for why that specific element and not
 * a class or a `<style>` block: it has to be the same element `globals.css`
 * declares the primitives on, or the semantic tokens never see it. */
function page(
  bodyHtml: string,
  css: string,
  dark: boolean,
  title: string,
  htmlStyle?: string
): string {
  const styleAttr = htmlStyle ? ` style="${htmlStyle}"` : "";
  return `<!doctype html><html lang="en"${dark ? ' class="dark"' : ""}${styleAttr}><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${title}</title><style>${FONT_VARS}</style><style>${css}</style></head>
<body>${bodyHtml}</body></html>`;
}

/**
 * The three variants the coordinator proposed, for a real side-by-side
 * choice rather than a choice between hex-code lists. The user picked
 * Variant C from these renders, so `globals.css` now sets C's values by
 * default and Variant C is not listed here: `console-dark.html` (no inline
 * style at all) already IS variant C, and `console-dark-c.html` below
 * reuses that same "no override" render rather than repeating its values a
 * second place they could drift from the source of truth. A and B keep
 * their inline overrides so all three considered options stay renderable
 * for comparison even though only one of them is what ships.
 *
 * Only the primitives that actually differ between variants are listed —
 * `--brand-foreground`, `--warning-foreground`, `--accent-text`,
 * `--cyan-dim` and the dark shadows stay the same across all three per the
 * brief, so they are left for `globals.css`'s class rule to supply and are
 * deliberately absent here.
 */
const VARIANT_STYLES: Record<"a" | "b", string> = {
  a:
    "--z-app-bg:#191815;--z-app-canvas:#1d1c19;--z-app-surface:#201f1b;" +
    "--z-app-surface-hover:#2a2924;--z-app-border:#38362f;" +
    "--z-app-border-strong:#4a473e;--z-app-text:#f0ebe1;" +
    "--z-app-text-secondary:#a49e91;--z-app-text-dim:#757064;" +
    "--skeleton:#26251f;",
  b:
    "--z-app-bg:#16181c;--z-app-canvas:#1a1d21;--z-app-surface:#1e2126;" +
    "--z-app-surface-hover:#262a30;--z-app-border:#30353c;" +
    "--z-app-border-strong:#434a52;--z-app-text:#e8eaed;" +
    "--z-app-text-secondary:#9aa0a8;--z-app-text-dim:#6b7178;" +
    "--skeleton:#23272d;",
};

const VARIANT_LABELS: Record<"a" | "b" | "c", string> = {
  a: "Variant A — warm charcoal",
  b: "Variant B — lifted cool graphite",
  c: "Variant C — warm black, orange-warmed borders",
};

it(
  "writes the console dark and light theme previews",
  async () => {
    const css = await compiledCss();
    mkdirSync(outDir, { recursive: true });

    const darkBody = renderToStaticMarkup(<Scene theme="dark" />);
    const darkOut = path.join(outDir, "console-dark.html");
    writeFileSync(
      darkOut,
      page(darkBody, css, true, "Console dark theme preview"),
      "utf8"
    );

    const lightBody = renderToStaticMarkup(<Scene theme="light" />);
    const lightOut = path.join(outDir, "console-light.html");
    writeFileSync(
      lightOut,
      page(lightBody, css, false, "Console light theme preview (baseline)"),
      "utf8"
    );

    console.log(`preview written: ${darkOut}`);
    console.log(`preview written: ${lightOut}`);
  },
  60_000
);

it(
  "writes the three dark-variant previews for comparison",
  async () => {
    const css = await compiledCss();
    mkdirSync(outDir, { recursive: true });

    const darkBody = renderToStaticMarkup(<Scene theme="dark" />);

    const aOut = path.join(outDir, "console-dark-a.html");
    writeFileSync(
      aOut,
      page(
        darkBody,
        css,
        true,
        `Console dark preview — ${VARIANT_LABELS.a}`,
        VARIANT_STYLES.a
      ),
      "utf8"
    );

    const bOut = path.join(outDir, "console-dark-b.html");
    writeFileSync(
      bOut,
      page(
        darkBody,
        css,
        true,
        `Console dark preview — ${VARIANT_LABELS.b}`,
        VARIANT_STYLES.b
      ),
      "utf8"
    );

    // Variant C: the globals-default dark render (shipped), no inline
    // override — see the VARIANT_STYLES doc comment above.
    const cOut = path.join(outDir, "console-dark-c.html");
    writeFileSync(
      cOut,
      page(darkBody, css, true, `Console dark preview — ${VARIANT_LABELS.c}`),
      "utf8"
    );

    console.log(`preview written: ${aOut}`);
    console.log(`preview written: ${bOut}`);
    console.log(`preview written: ${cOut}`);
  },
  60_000
);
