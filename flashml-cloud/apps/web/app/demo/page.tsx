import type { Metadata } from "next";

import { DemoClient } from "@/components/demo/DemoClient";

/**
 * The public, no-login demo. **No session, no account, no redirect.**
 *
 * The second door in this console, after `/share/<token>`, and open for the
 * same reason: it is judged over a weekend by people with no account and
 * nobody around to approve one. Unlike the share page it is not a bearer
 * capability — there is one URL, everyone gets the same page, and
 * `middleware.ts` carries `/demo` as a LITERAL in `PUBLIC_PATHS` rather
 * than a pattern, so there is nothing here that a loosened rewrite could
 * widen into the console. See the note beside `SHARE_PATH` there.
 *
 * WHAT THE PAGE IS ABOUT: compute. Four real machines in Singapore, nine
 * tasks, and the two control planes that can drive them. It is deliberately
 * not a pitch — one line says what this is, and everything after it is the
 * live fleet, the work moving across it, and two elapsed times. A judge who
 * reads nothing should still come away with "real machines, real parallel
 * work, and here is which control plane was faster".
 *
 * NOT IN A LAYOUT GROUP, for the same reason `/share/<token>` is not:
 * `(marketing)` wears a top nav and `(console)` a left rail, and every
 * destination in both is behind a sign-in this visitor does not have.
 * Handing them chrome full of links that bounce to `/sign-in` is worse than
 * handing them none. So the page supplies its own `<main id="content">` —
 * the root layout's skip link points at that id and would otherwise land
 * nowhere.
 *
 * ALL THE LIVE WORK IS IN `DemoClient`. This route does no server-side
 * fetch on purpose: an API that is slow or down would otherwise block the
 * first paint and hand a judge a spinning tab, where a client fetch paints
 * the frame immediately and fills it in — or says honestly that the network
 * is not answering.
 */

/** Indexable, unlike `/share/<token>`. This URL is meant to be handed out
 * and there is no secret in it. */
export const metadata: Metadata = {
  title: { absolute: "Live demo · Zolli Cloud" },
  description:
    "Four machines in Singapore, nine tasks, run in parallel — driven twice, once by an always-on coordinator and once by a serverless one, with the elapsed times side by side.",
};

/** Never statically generated: the fleet and the runs are live, and a
 * build-time snapshot of either would be a page that lies about what is
 * online. */
export const dynamic = "force-dynamic";

export default function DemoPage() {
  return (
    <main
      id="content"
      className="mx-auto min-h-dvh w-full max-w-6xl bg-background px-4 py-8 text-foreground sm:px-6"
    >
      <header>
        <h1 className="title">Nine tasks, four machines, twice over</h1>
        {/* ONE line about what FlashML is, and not a word more. The rest of
            the page is the evidence; a paragraph of positioning here would
            push the fleet below the fold and cost the demo its first five
            seconds. */}
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-muted-foreground">
          Zolli Cloud spreads one training job across machines that can
          disappear mid-run. Below is the live network — press Run and watch
          nine tasks distribute across it. The same work is driven twice, by
          two different control planes, so the two can be timed against each
          other.
        </p>
      </header>

      <div className="mt-8">
        <DemoClient />
      </div>

      <footer className="mt-10 border-t border-border pt-4">
        <p className="meta">
          Every number on this page was measured on the run it describes.
          Nothing here is a recording.
        </p>
      </footer>
    </main>
  );
}
