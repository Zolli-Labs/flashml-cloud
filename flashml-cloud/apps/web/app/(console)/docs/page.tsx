"use client";

import Link from "next/link";
import { GithubLogo } from "@phosphor-icons/react";
import { cloudApiBase } from "@/lib/cloud-api";

// In-app docs: the four things someone needs on day one, and a glossary of
// the words this console uses that are not standard.
//
// Kept deliberately short. Long reference documentation belongs in the
// public repo where it is versioned with the code; a console page that
// duplicates it drifts and then lies. Everything here is either a command
// the app itself prints or a term the UI shows, which is exactly the set
// that has nowhere else to live.

const REPO = "https://github.com/Zolli-Labs/flashml";

function Code({ children }: { children: string }) {
  return (
    <pre className="mt-2 overflow-x-auto rounded-md border border-border bg-background px-3 py-2.5">
      <code className="font-mono text-xs leading-relaxed">{children}</code>
    </pre>
  );
}

function Term({ word, children }: { word: string; children: React.ReactNode }) {
  return (
    <div className="border-b border-border py-3 last:border-0">
      <dt className="font-mono text-sm">{word}</dt>
      <dd className="mt-1 max-w-prose text-sm leading-relaxed text-muted-foreground">
        {children}
      </dd>
    </div>
  );
}

export default function DocsPage() {
  // The real base this console is talking to, so a copied command works
  // against the environment the reader is actually looking at rather than a
  // hardcoded production URL.
  const base = cloudApiBase();

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
      <h1 className="text-2xl font-semibold tracking-tight">Documentation</h1>
      <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
        Enough to get a job running and to read what the console tells you
        afterwards. The full reference lives with the code.
      </p>

      <section className="mt-8">
        <h2 className="text-base font-semibold">Attach a machine</h2>
        <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
          A machine has to be enrolled before it can claim work. Run this on
          the machine you want to lend, then approve the code it prints on the{" "}
          <Link href="/activate" className="text-primary hover:underline">
            Activate
          </Link>{" "}
          page.
        </p>
        <Code>{`python3 -m venv flashml
flashml/bin/python -m pip install flashnode
flashml/bin/flashnode login --coordinator ${base}`}</Code>
        <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
          Once approved, start it taking work. It claims tasks, runs them, and
          keeps renewing its lease until you stop it.
        </p>
        <Code>{`flashml/bin/flashnode work --coordinator ${base}`}</Code>
      </section>

      <section className="mt-8">
        <h2 className="text-base font-semibold">Run a job</h2>
        <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
          Point{" "}
          <Link href="/submit" className="text-primary hover:underline">
            Submit
          </Link>{" "}
          at a GitHub repository containing a{" "}
          <code className="font-mono text-xs">flashml.yaml</code>. Preflight
          checks it before anything is scheduled and reports every problem it
          finds at once, so a job that cannot run fails immediately rather
          than halfway through on somebody else&apos;s machine.
        </p>
      </section>

      <section className="mt-8">
        <h2 className="text-base font-semibold">Reading a job</h2>
        <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
          A job page has three views. They change the detail, never the
          status: state, progress and any stall reason stay above them.
        </p>
        <dl className="mt-3">
          <Term word="Progress">
            What the run achieved. Loss per round for federated runs, plus
            which machines contributed to each.
          </Term>
          <Term word="Placement">
            Where it ran. One lane per machine, one block per attempt, so a
            machine that died mid-task and the machine that finished the work
            both appear.
          </Term>
          <Term word="Ledger">
            What happened, in the coordinator&apos;s own words. Recovery
            decisions are shown verbatim, never paraphrased.
          </Term>
        </dl>
      </section>

      <section className="mt-8">
        <h2 className="text-base font-semibold">Words this console uses</h2>
        <dl className="mt-3">
          <Term word="lease">
            A time-bounded right to run one attempt of one task. Work is never
            pushed to a machine: the machine claims a lease and has to keep
            renewing it. A machine that disappears needs no handling, because
            its lease simply expires.
          </Term>
          <Term word="accepted vs attempted">
            An attempt is work someone tried. Accepted is work that was
            committed and counted. Exactly one result per task can ever be
            accepted, so a duplicate arriving late is rejected rather than
            double-counted. Progress is measured in accepted work; the gap
            between the two is what unreliable machines cost.
          </Term>
          <Term word="requeued">
            A lease expired or an attempt failed, so the task went back in the
            queue for another machine. Normal, not an error.
          </Term>
          <Term word="checkpoint manifest">
            A checkpoint is not a path, it is a manifest listing every part
            and its hash. Parts upload first and the manifest is written only
            after every hash verifies, so a half-uploaded checkpoint is never
            selected for recovery.
          </Term>
          <Term word="recovery frozen">
            The policy stopped acting on purpose. Too many failures happened
            at once to treat them as independent, and retrying into a systemic
            incident makes it worse. A job in this state is waiting for a
            human, not stuck.
          </Term>
        </dl>
      </section>

      <section className="mt-8 rounded-lg border border-border bg-surface p-5">
        <h2 className="text-sm font-semibold">Source and full reference</h2>
        <p className="mt-1.5 max-w-prose text-sm leading-relaxed text-muted-foreground">
          The runtime, the wire protocol and the host agent are public under
          Apache 2.0.
        </p>
        <a
          href={REPO}
          target="_blank"
          rel="noreferrer"
          className="mt-3 inline-flex items-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-white/[0.06]"
        >
          <GithubLogo size={15} weight="fill" />
          Zolli-Labs/flashml
        </a>
      </section>
    </div>
  );
}
