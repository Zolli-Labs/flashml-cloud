"use client";

import { useMemo } from "react";
import { SAMPLE_LEDGER } from "@/lib/landing/sample-ledger";

// The page's one structural break: full-bleed, no container, no card, no
// headline-then-grid. Every other section is a max-w-7xl column with a
// heading at the top, and a page where nothing ever breaks that rhythm is
// the thing that reads as generated.
//
// The texture is the product's own event ledger at scale. It is not
// decoration standing in for a photograph: it is the artefact the whole
// pitch rests on, rendered large enough to become a surface. That also
// sidesteps the usual failure here, which is reaching for a stock image of
// a server rack that says nothing.
//
// Masked to near-invisibility at the edges and behind the type. It has to
// read as material, not as content competing to be read — at full contrast
// it would be a wall of text nobody can look past.

export function LedgerWall() {
  // Deterministic, not random: Math.random here would make every render
  // different and hydration would mismatch between server and client.
  const columns = useMemo(() => {
    const rows = SAMPLE_LEDGER.map((e) => `${e.type} ${e.detail}`);
    return Array.from({ length: 4 }, (_, col) =>
      Array.from({ length: 26 }, (_, i) => rows[(i * 3 + col * 7) % rows.length])
    );
  }, []);

  return (
    <section className="relative isolate overflow-hidden border-y border-border py-28 md:py-40">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 grid grid-cols-2 gap-x-10 opacity-[0.14] md:grid-cols-4"
        style={{
          maskImage:
            "radial-gradient(ellipse 70% 60% at 50% 50%, transparent 10%, black 85%)",
          WebkitMaskImage:
            "radial-gradient(ellipse 70% 60% at 50% 50%, transparent 10%, black 85%)",
        }}
      >
        {columns.map((col, i) => (
          <div key={i} className="space-y-1.5">
            {col.map((line, j) => (
              <div
                key={j}
                className="truncate font-mono text-[10px] leading-none text-foreground"
              >
                {line}
              </div>
            ))}
          </div>
        ))}
      </div>

      {/* 5xl, not 4xl: the longest display line needs ~940px at this size
          and a narrower container would wrap it. */}
      <div className="relative mx-auto max-w-5xl px-4 text-center sm:px-6">
        <p className="display">
          Every claim, every expiry,
          <br />
          <span className="text-accent-text">every accepted commit.</span>
        </p>
        <p className="mx-auto mt-7 max-w-xl text-base leading-relaxed text-muted-foreground">
          Written down as it happens, and readable afterwards. Not a status
          field that flips at the end, and not a log you have to SSH for.
        </p>
      </div>
    </section>
  );
}
