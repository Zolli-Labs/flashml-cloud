"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { Check, Copy, SealCheck } from "@phosphor-icons/react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { cloudApiBase } from "@/lib/cloud-api";
import {
  apiDetail,
  joinErrorMessage,
  normaliseUserCode,
  parseJoinedMachine,
  userCodeReady,
  type JoinedMachine,
} from "@/lib/demo";

/**
 * "Host your own machine" — three steps and a text box, with no account.
 *
 * The other half of this page is our hardware. This half is the judge's:
 * install the agent, run one command, paste the code it prints. On success
 * their laptop is in the `demo-guests` pool and shows up in the guest fleet
 * below, and they can run a task on it.
 *
 * WHY NOT `components/machines/EnrolInstructions`, which already renders
 * these exact three commands with copy buttons and platform tabs: its
 * closing paragraph tells the reader to "Enter it at /activate from any
 * signed-in browser". That is correct for the console and precisely wrong
 * here — `/activate` is behind the sign-in this visitor does not have, and
 * this flow's whole claim is that they do not need one. Reusing it would put
 * an instruction on screen that cannot be followed, which is worse than
 * repeating three short strings. The commands themselves are kept identical
 * to that component's, which remains the source of truth for them; the
 * VISUAL treatment here is copied from it deliberately, so the two read as
 * one system.
 *
 * The venv is not ceremony — see that component's docstring for the two
 * failures (`command not found: pip`, PEP 668 externally-managed-environment)
 * it exists to sidestep.
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
        label: "Connect it and print a code",
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
      label: "Connect it and print a code",
      cmd: `flashml/bin/flashnode login --coordinator ${base}`,
    },
  ];
}

function CommandRow({ cmd }: { cmd: string }) {
  const [copied, setCopied] = useState(false);

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
      // permission). The command is selectable either way.
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

// Platform detection via useSyncExternalStore rather than an effect, for the
// reason `EnrolInstructions` records: `navigator` does not exist during
// server rendering, so the value genuinely differs between the two
// snapshots, and reading it in a useState initialiser is a hydration
// mismatch.
const subscribe = () => () => {};
const isWindowsClient = () => /Win(dows|32|64|CE)/i.test(navigator.userAgent);
const isWindowsServer = () => false;

export function JoinCard({
  joined,
  onJoined,
}: {
  joined: JoinedMachine | null;
  onJoined: (machine: JoinedMachine) => void;
}) {
  const detectedWindows = useSyncExternalStore(
    subscribe,
    isWindowsClient,
    isWindowsServer
  );
  const [chosen, setChosen] = useState<Platform | null>(null);
  const platform: Platform = chosen ?? (detectedWindows ? "windows" : "unix");

  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const tabs: { id: Platform; label: string }[] = [
    { id: "unix", label: "macOS / Linux" },
    { id: "windows", label: "Windows" },
  ];

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!userCodeReady(code) || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch(`${cloudApiBase()}/v1alpha1/public/demo/join`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // Sent raw. The API normalises dashes, spaces and case itself, and
        // sending what the judge actually typed keeps one normaliser
        // authoritative instead of two that can disagree.
        body: JSON.stringify({ user_code: code }),
      });

      // The body is read on BOTH paths: on failure it carries the sentence
      // this flow is worth writing well, and those sentences are the API's
      // own (see `joinErrorMessage`).
      let body: unknown = null;
      try {
        body = await res.json();
      } catch {
        // A proxy error page, or an empty 502. Handled by the per-status
        // fallback below rather than by crashing on the parse.
      }

      if (!res.ok) {
        setError(joinErrorMessage(res.status, apiDetail(body)));
        return;
      }

      const machine = parseJoinedMachine(body);
      if (!machine) {
        // A 201 whose body we cannot match to a row in `guests` — the join
        // may well have worked, and saying otherwise would be a lie, so this
        // says exactly what it knows.
        setError(
          "That machine joined, but this page could not read which one it is. It should still appear below."
        );
        return;
      }
      setCode("");
      onJoined(machine);
    } catch {
      setError(
        "Could not reach the network to join that machine. Check the connection and try again."
      );
    } finally {
      setSubmitting(false);
    }
  }

  const normalised = normaliseUserCode(code);

  return (
    <div className="panel p-4">
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
            onClick={() => setChosen(t.id)}
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
        {steps(platform, cloudApiBase()).map((s, i) => (
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

      {/* ── Step 4: the box that needs no account ─────────────────────── */}
      <form onSubmit={submit} className="mt-4 border-t border-border pt-4">
        <label
          htmlFor="demo-user-code"
          className="flex items-baseline gap-2 text-xs text-muted-foreground"
        >
          <span className="font-mono tabular-nums text-foreground/50">4</span>
          Paste the code it printed
        </label>
        <div className="mt-1.5 flex flex-wrap items-center gap-2">
          <input
            id="demo-user-code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="ABCD-EFGH"
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            // `--input` is the console's own field border token; this is the
            // same treatment `components/ui/input.tsx` renders, kept inline
            // because the value here is a code and wants mono + tracking.
            className="h-9 w-44 rounded-md border border-input bg-background px-3 font-mono text-sm uppercase tracking-[0.12em] text-foreground outline-none placeholder:tracking-normal placeholder:text-[var(--z-app-text-dim)] focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30"
          />
          <Button type="submit" size="sm" disabled={!userCodeReady(code) || submitting}>
            {submitting && <Spinner data-icon="inline-start" className="h-3.5 w-3.5" />}
            {submitting ? "Joining…" : "Join the network"}
          </Button>
          {normalised.length > 0 && !submitting && (
            // Shows the judge exactly what will be sent once the dash and the
            // case they typed are taken out — so a code that fails does not
            // leave them wondering whether the punctuation was the problem.
            <span className="meta">reads as {normalised}</span>
          )}
        </div>

        {error && (
          <div className="mt-2.5">
            <JoinError message={error} />
          </div>
        )}

        {joined && !error && (
          <div className="mt-2.5">
            <JoinSuccess machine={joined} />
          </div>
        )}
      </form>
    </div>
  );
}

/**
 * Why a join did not work, in the API's own words.
 *
 * Its own component so the preview harness can render all four refusals —
 * unknown code, expired code, a CLI code pasted by mistake, and a machine
 * that already joined — without driving the form. Those four sentences are
 * the difference between a judge fixing their own problem in ten seconds and
 * giving up, and until this was extracted the only way to see one was to
 * actually fail a join against a live API.
 */
export function JoinError({ message }: { message: string }) {
  return (
    <p
      role="alert"
      className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-[13px] leading-relaxed text-destructive"
    >
      {message}
    </p>
  );
}

/** The machine that just joined. */
export function JoinSuccess({ machine }: { machine: JoinedMachine }) {
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-evergreen/40 bg-evergreen/[0.06] px-3 py-2 text-[13px] text-foreground">
      <SealCheck
        aria-hidden="true"
        weight="fill"
        className="h-4 w-4 shrink-0 text-evergreen"
      />
      <span>
        {/* The REAL hostname, which only this visitor gets: they proved
            possession of the machine's one-shot code to see it. The public
            list carries only the handle. */}
        <span className="font-mono">{machine.name ?? machine.label}</span> joined
        the network.
      </span>
      <span className="meta">shown to everyone else as {machine.label}</span>
    </p>
  );
}
