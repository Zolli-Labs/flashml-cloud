"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Kbd } from "@/components/ui/kbd";

// Vim/GitHub-style `g`-then-key navigation, plus `?` for the sheet that
// tells you it exists. A console you live in should be reachable without the
// mouse; a console that has shortcuts and never mentions them may as well
// not have them, which is why `?` is the first row in the list.

const GOTO: Record<string, { href: string; label: string }> = {
  o: { href: "/overview", label: "Overview" },
  j: { href: "/jobs", label: "Jobs" },
  m: { href: "/machines", label: "Zollis" },
  s: { href: "/submit", label: "Submit a job" },
  a: { href: "/account", label: "Account" },
  d: { href: "/docs", label: "Documentation" },
};

/** True when the keystroke belongs to whatever the user is typing in.
 * Without this, `g` inside the device-code field or the display-name input
 * navigates away mid-word, which is the classic way this pattern gets
 * hated. */
function isTyping(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return (
    tag === "INPUT" ||
    tag === "TEXTAREA" ||
    tag === "SELECT" ||
    el.isContentEditable
  );
}

export function Shortcuts() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  // `g` arms a prefix that expires, so a stray g does not silently wait
  // forever and then hijack an unrelated keypress a minute later.
  const armed = useRef(false);
  const armedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const disarm = useCallback(() => {
    armed.current = false;
    if (armedTimer.current) clearTimeout(armedTimer.current);
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (isTyping(e.target)) return;

      if (e.key === "Escape") {
        setOpen(false);
        disarm();
        return;
      }

      if (e.key === "?") {
        e.preventDefault();
        setOpen((o) => !o);
        return;
      }

      if (armed.current) {
        const dest = GOTO[e.key.toLowerCase()];
        disarm();
        if (dest) {
          e.preventDefault();
          setOpen(false);
          router.push(dest.href);
        }
        return;
      }

      if (e.key.toLowerCase() === "g") {
        armed.current = true;
        if (armedTimer.current) clearTimeout(armedTimer.current);
        armedTimer.current = setTimeout(disarm, 1200);
      }
    }

    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (armedTimer.current) clearTimeout(armedTimer.current);
    };
  }, [router, disarm]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center px-4">
      <button
        type="button"
        aria-label="Close shortcuts"
        onClick={() => setOpen(false)}
        className="absolute inset-0 bg-black/60 backdrop-blur-[2px]"
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Keyboard shortcuts"
        className="glass-strong relative w-full max-w-sm rounded-xl p-5"
      >
        <h2 className="text-sm font-semibold">Keyboard shortcuts</h2>
        <dl className="mt-4 space-y-2.5">
          <Row keys={["?"]} label="This list" />
          <Row keys={["⌘", "K"]} label="Search jobs, Zollis, pages" />
          <div className="pt-1.5">
            <div className="meta uppercase tracking-[0.12em]">Go to</div>
          </div>
          {Object.entries(GOTO).map(([k, v]) => (
            <Row key={k} keys={["G", k.toUpperCase()]} label={v.label} />
          ))}
        </dl>
        <p className="meta mt-4">
          Shortcuts are ignored while you are typing in a field.
        </p>
      </div>
    </div>
  );
}

function Row({ keys, label }: { keys: string[]; label: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="flex shrink-0 gap-1">
        {keys.map((k) => (
          <Kbd key={k}>{k}</Kbd>
        ))}
      </dd>
    </div>
  );
}
