"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { Check, Copy } from "@phosphor-icons/react";

/**
 * How a volunteer actually gets flashnode running.
 *
 * `pip install flashnode` is the whole install as of 2026-08-01: flashnode
 * 0.2.1 and flashruntime 0.3.0 are published, so pip resolves the dependency
 * itself. Until then neither package existed on PyPI and this component had to
 * name both as git URLs — that workaround is gone, and it must not come back.
 *
 * The VIRTUAL ENVIRONMENT stays, and not as ceremony. Two failures have
 * nothing to do with where the package is hosted and are still live:
 *
 *   `pip install ...`
 *     -> zsh: command not found: pip. macOS ships no `pip` on PATH; only
 *        `python3` is dependable.
 *
 *   `pip3 install ...`
 *     -> error: externally-managed-environment (PEP 668). Homebrew Python
 *        refuses to install into itself, and the suggested escape hatch,
 *        --break-system-packages, is exactly what its name says.
 *
 * A venv sidesteps PATH and PEP 668 together, needs nothing installed beyond
 * Python, and leaves the host's system Python untouched — which matters when
 * you are asking a friend to run your code on their laptop.
 *
 * The commands use the venv's binaries by full path rather than `activate`,
 * because activation does not survive closing the terminal and the follow-up
 * command would then fail with `command not found: flashnode` a day later.
 *
 * Deliberately NOT version-pinned. A volunteer should get the current agent;
 * pinning here would create a fourth place a version has to be kept in step
 * with pyproject.toml, render.yaml and the Makefile, and it would silently
 * freeze the fleet on whatever was current the day this line was written.
 */

type Platform = "unix" | "windows";

function steps(platform: Platform, base: string) {
  if (platform === "windows") {
    return [
      { label: "Create an isolated environment", cmd: "py -m venv flashml" },
      {
        label: "Install the agent",
        cmd: "flashml\\Scripts\\python -m pip install flashnode",
      },
      {
        label: "Connect it to your account",
        cmd: `flashml\\Scripts\\flashnode login --coordinator ${base}`,
      },
    ];
  }
  return [
    { label: "Create an isolated environment", cmd: "python3 -m venv flashml" },
    {
      label: "Install the agent",
      cmd: "flashml/bin/python -m pip install flashnode",
    },
    {
      label: "Connect it to your account",
      cmd: `flashml/bin/flashnode login --coordinator ${base}`,
    },
  ];
}

function CommandRow({ cmd }: { cmd: string }) {
  const [copied, setCopied] = useState(false);

  // Clear the confirmation on a timer, and cancel that timer on unmount —
  // switching platform unmounts these rows, and a pending setState on a gone
  // component is a warning in dev and a leak in principle.
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1600);
    return () => clearTimeout(t);
  }, [copied]);

  async function copy() {
    try {
      await navigator.clipboard.writeText(cmd);
      setCopied(true);
    } catch {
      // Clipboard access can be refused (insecure origin, denied
      // permission). The command is selectable either way, so failing
      // quietly is better than an alert over a convenience.
    }
  }

  return (
    <div className="group relative rounded-lg border border-border bg-surface-2/60 pr-11">
      <pre className="overflow-x-auto px-3.5 py-2.5 font-mono text-[11.5px] leading-relaxed text-foreground/90">
        <code className="whitespace-pre-wrap break-all">{cmd}</code>
      </pre>
      <button
        type="button"
        onClick={copy}
        aria-label={copied ? "Copied" : "Copy command"}
        className="interactive absolute right-1.5 top-1.5 rounded-md p-1.5 text-muted-foreground opacity-0 transition-opacity hover:bg-surface hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
      >
        {copied ? (
          <Check className="h-3.5 w-3.5 text-brand-foreground" weight="bold" />
        ) : (
          <Copy className="h-3.5 w-3.5" />
        )}
      </button>
    </div>
  );
}

// Platform detection via useSyncExternalStore rather than an effect.
//
// `navigator` does not exist during server rendering, so the value genuinely
// differs between the server and client snapshots — which is exactly what
// this hook's two-snapshot signature is for. Reading it in useState's
// initialiser would render the wrong platform on the server and produce a
// hydration mismatch; setting it from an effect works but is a cascading
// render (and what react-hooks/set-state-in-effect flags).
//
// The store never changes after load, so `subscribe` is a no-op returning an
// unsubscribe that does nothing.
const subscribe = () => () => {};
const isWindowsClient = () => /Win(dows|32|64|CE)/i.test(navigator.userAgent);
// Server assumes not-Windows: an unknown platform is far likelier to be a
// Unix-alike, and the tab is one click away either way.
const isWindowsServer = () => false;

export function EnrolInstructions({ base }: { base: string }) {
  const detectedWindows = useSyncExternalStore(
    subscribe,
    isWindowsClient,
    isWindowsServer
  );
  // An explicit choice always wins over detection — someone reading this on
  // a Mac to set up a Windows box is a normal thing to do.
  const [chosen, setChosen] = useState<Platform | null>(null);
  const platform: Platform = chosen ?? (detectedWindows ? "windows" : "unix");
  const setPlatform = setChosen;

  const tabs: { id: Platform; label: string }[] = [
    { id: "unix", label: "macOS / Linux" },
    { id: "windows", label: "Windows" },
  ];

  return (
    <div className="w-full max-w-xl text-left">
      <div
        role="tablist"
        aria-label="Operating system"
        className="mb-4 inline-flex rounded-lg border border-border bg-surface-2 p-0.5"
      >
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={platform === t.id}
            onClick={() => setPlatform(t.id)}
            className={`interactive rounded-[7px] px-3 py-1.5 text-xs font-medium ${
              platform === t.id
                ? "bg-surface text-foreground shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <ol className="space-y-3">
        {steps(platform, base).map((s, i) => (
          <li key={s.label} className="space-y-1.5">
            <p className="flex items-baseline gap-2 text-xs text-muted-foreground">
              <span className="font-mono tabular-nums text-foreground/50">
                {i + 1}
              </span>
              {s.label}
            </p>
            <CommandRow cmd={s.cmd} />
          </li>
        ))}
      </ol>

      <div className="mt-5 space-y-2 border-t border-border/60 pt-4 text-xs leading-relaxed text-muted-foreground">
        <p>
          The last command prints a short code. Enter it at{" "}
          <span className="font-mono text-foreground">/activate</span> from any
          signed-in browser — your phone works — to approve the Zolli.
        </p>
        {/*
          Git used to be listed here, and it is deliberately gone. It was only
          ever needed because pip shelled out to it for a `git+https://`
          install; installing from PyPI does not. Windows in particular usually
          lacks Git, so this removes a prerequisite that failed late, on the
          machines least likely to have it.
        */}
        <p>
          You&apos;ll need <span className="text-foreground">Python 3.10+</span>{" "}
          and <span className="text-foreground">Docker Desktop</span> running.
          The first job pulls a container image of roughly 1&nbsp;GB; after that
          it&apos;s cached.
        </p>
      </div>
    </div>
  );
}
