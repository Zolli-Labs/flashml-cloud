"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "@phosphor-icons/react";
import { toast } from "sonner";

/** A command block you can actually copy.
 *
 * The docs page printed commands as plain <pre> and expected people to
 * select multi-line shell text by hand, which on a phone is close to
 * impossible — and /activate exists precisely because this product is used
 * across two devices.
 *
 * The confirmation is inline on the button rather than only a toast: at the
 * moment of clicking, the pointer is on the button and that is where the eye
 * already is. The toast is the backup for anyone whose attention moved. */
export function CopyBlock({
  code,
  label,
}: {
  code: string;
  label?: string;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Clear on unmount, or navigating away mid-countdown sets state on a gone
  // component.
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard is permission-gated and blocked outright on insecure
      // origins. Failing silently would look like the button is broken.
      toast.error("Couldn't copy", {
        description: "Your browser blocked clipboard access. Select and copy manually.",
      });
    }
  }

  return (
    <div className="group relative mt-3">
      {label && (
        <div className="meta mb-1.5 uppercase tracking-[0.12em]">{label}</div>
      )}
      <pre className="overflow-x-auto rounded-md border border-border bg-background py-3 pl-4 pr-12">
        <code className="font-mono text-xs leading-relaxed">{code}</code>
      </pre>
      <button
        type="button"
        onClick={copy}
        aria-label={copied ? "Copied" : "Copy to clipboard"}
        className="absolute right-2 top-2 rounded-md border border-border bg-surface p-1.5 text-muted-foreground opacity-0 transition-opacity hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
        style={label ? { top: "1.9rem" } : undefined}
      >
        {copied ? (
          <Check size={13} weight="bold" className="text-[var(--node-green)]" />
        ) : (
          <Copy size={13} />
        )}
      </button>
    </div>
  );
}
