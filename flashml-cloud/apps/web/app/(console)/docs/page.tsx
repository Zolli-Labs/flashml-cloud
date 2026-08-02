"use client";

import Link from "next/link";
import { ArrowSquareOut, GithubLogo } from "@phosphor-icons/react";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Kbd } from "@/components/ui/kbd";
import { CopyBlock } from "@/components/shared/CopyBlock";
import { cloudApiBase } from "@/lib/cloud-api";

// In-app docs: the things needed on day one, plus a glossary of the words
// this console uses that are not standard.
//
// Deliberately short. Long reference belongs in the public repo where it is
// versioned with the code; a console page that duplicates it drifts and then
// lies. Everything here is either a command the app itself prints or a term
// the UI shows, which is exactly the set that has nowhere else to live.

const REPO = "https://github.com/Zolli-Labs/flashml";

const SECTIONS = [
  { id: "attach", label: "Attach a machine" },
  { id: "run", label: "Run a job" },
  { id: "reading", label: "Reading a job" },
  { id: "glossary", label: "Glossary" },
] as const;

const GLOSSARY = [
  {
    term: "lease",
    body: "A time-bounded right to run one attempt of one task. Work is never pushed to a machine: the machine claims a lease and has to keep renewing it. A machine that disappears needs no handling, because its lease simply expires and the task goes back in the queue.",
  },
  {
    term: "accepted vs attempted",
    body: "An attempt is work someone tried. Accepted is work that was committed and counted. Exactly one result per task can ever be accepted, so a duplicate arriving late is rejected rather than double-counted. Progress is measured in accepted work, and the gap between the two is what unreliable machines cost you.",
  },
  {
    term: "requeued",
    body: "A lease expired or an attempt failed, so the task went back in the queue for another machine to claim. This is normal operation, not an error.",
  },
  {
    term: "checkpoint manifest",
    body: "A checkpoint is not a path, it is a manifest listing every part and its hash. Parts upload first and the manifest is written only after every hash verifies, so a half-uploaded checkpoint can never be selected for recovery.",
  },
  {
    term: "recovery frozen",
    body: "The recovery policy stopped acting on purpose. Too many failures happened at once to treat them as independent, and retrying into a systemic incident makes it worse. A job in this state is waiting for a human, not stuck.",
  },
];

export default function DocsPage() {
  // The base this console is actually talking to, so a copied command targets
  // the environment the reader is looking at rather than a hardcoded
  // production URL.
  const base = cloudApiBase();

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <div className="lg:grid lg:grid-cols-[1fr_180px] lg:gap-12">
        <div className="min-w-0">
          <h1 className="title">Documentation</h1>
          <p className="mt-3 max-w-prose text-sm leading-relaxed text-muted-foreground">
            Enough to get a job running and to read what the console tells you
            afterwards. The full reference lives with the code.
          </p>

          <section id="attach" className="scroll-mt-20 pt-12">
            <h2 className="text-lg font-semibold">Attach a machine</h2>
            <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
              A machine has to be enrolled before it can claim work. Run this
              on the machine you want to lend, then approve the code it prints
              on the{" "}
              <Link href="/activate" className="text-primary hover:underline">
                Activate
              </Link>{" "}
              page.
            </p>
            <CopyBlock
              label="on the machine"
              code={`python3 -m venv flashml
flashml/bin/python -m pip install flashnode
flashml/bin/flashnode login --coordinator ${base}`}
            />
            <p className="mt-4 max-w-prose text-sm leading-relaxed text-muted-foreground">
              Once approved, start it taking work. It claims tasks, runs them,
              and keeps renewing its lease until you stop it.
            </p>
            <CopyBlock
              label="then"
              code={`flashml/bin/flashnode work --coordinator ${base}`}
            />
          </section>

          <hr className="rule-fade mt-12 border-0" />

          <section id="run" className="scroll-mt-20 pt-12">
            <h2 className="text-lg font-semibold">Run a job</h2>
            <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
              Point{" "}
              <Link href="/submit" className="text-primary hover:underline">
                Submit
              </Link>{" "}
              at a GitHub repository containing a{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
                flashml.yaml
              </code>
              . Preflight checks it before anything is scheduled and reports
              every problem at once, so a job that cannot run fails
              immediately rather than halfway through on somebody
              else&apos;s machine.
            </p>
            <p className="mt-4 max-w-prose text-sm leading-relaxed text-muted-foreground">
              Press <Kbd>⌘</Kbd> <Kbd>K</Kbd> anywhere in the console to jump
              to a job, a machine or a page.
            </p>
          </section>

          <hr className="rule-fade mt-12 border-0" />

          <section id="reading" className="scroll-mt-20 pt-12">
            <h2 className="text-lg font-semibold">Reading a job</h2>
            <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
              A job page has three views. They change the detail, never the
              status: state, progress and any stall reason stay above them.
            </p>
            <dl className="mt-5 space-y-4">
              {[
                [
                  "Progress",
                  "What the run achieved. Loss per round for federated runs, plus which machines contributed to each.",
                ],
                [
                  "Placement",
                  "Where it ran. One lane per machine, one block per attempt, so a machine that died mid-task and the machine that finished the work both appear.",
                ],
                [
                  "Ledger",
                  "What happened, in the coordinator's own words. Recovery decisions are shown verbatim, never paraphrased.",
                ],
              ].map(([term, body]) => (
                <div
                  key={term}
                  className="border-l-2 border-border pl-4 transition-colors hover:border-primary"
                >
                  <dt className="text-sm font-medium">{term}</dt>
                  <dd className="mt-1 max-w-prose text-sm leading-relaxed text-muted-foreground">
                    {body}
                  </dd>
                </div>
              ))}
            </dl>
          </section>

          <hr className="rule-fade mt-12 border-0" />

          <section id="glossary" className="scroll-mt-20 pt-12">
            <h2 className="text-lg font-semibold">Glossary</h2>
            <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
              Words this console uses that are not standard elsewhere.
            </p>
            {/* Collapsed by default. A flat list of five paragraphs is a wall;
                the terms are what you scan for, and the definition is what you
                open when one of them is the word you did not know. */}
            <Accordion className="mt-4">
              {GLOSSARY.map((g) => (
                <AccordionItem key={g.term} value={g.term}>
                  <AccordionTrigger className="font-mono text-sm">
                    {g.term}
                  </AccordionTrigger>
                  <AccordionContent className="max-w-prose text-sm leading-relaxed text-muted-foreground">
                    {g.body}
                  </AccordionContent>
                </AccordionItem>
              ))}
            </Accordion>
          </section>

          <section className="panel mt-14 p-5">
            <h2 className="text-sm font-semibold">Source and full reference</h2>
            <p className="mt-1.5 max-w-prose text-sm leading-relaxed text-muted-foreground">
              The runtime, the wire protocol and the host agent are public
              under Apache 2.0.
            </p>
            <a
              href={REPO}
              target="_blank"
              rel="noreferrer"
              className="mt-3 inline-flex items-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm hover:bg-white/[0.06]"
            >
              <GithubLogo size={15} weight="fill" />
              Zolli-Labs/flashml
              <ArrowSquareOut size={12} />
            </a>
          </section>
        </div>

        {/* Sticky contents rail. Hidden below lg, where the page is short
            enough to scroll and a rail would just eat width. */}
        <nav aria-label="On this page" className="hidden lg:block">
          <div className="sticky top-20">
            <div className="meta uppercase tracking-[0.12em]">On this page</div>
            <ul className="mt-3 space-y-2">
              {SECTIONS.map((s) => (
                <li key={s.id}>
                  <a
                    href={`#${s.id}`}
                    className="block text-sm text-muted-foreground transition-colors hover:text-foreground"
                  >
                    {s.label}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        </nav>
      </div>
    </div>
  );
}
