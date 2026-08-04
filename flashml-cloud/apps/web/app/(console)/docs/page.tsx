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
              Once approved, check the machine can actually run a task. This
              needs Docker running — every curated image executes in a
              container.
            </p>
            <CopyBlock
              label="then"
              code={`flashml/bin/flashnode doctor`}
            />
            <p className="mt-4 max-w-prose text-sm leading-relaxed text-muted-foreground">
              When it is all green, start taking work. It claims tasks, runs
              them, and keeps renewing its lease until you stop it.
            </p>
            <CopyBlock
              label="finally"
              code={`flashml/bin/flashnode work --coordinator ${base} --runner argv`}
            />
            {/* --runner argv is REQUIRED, not a tuning knob, and getting it
                wrong fails silently: the agent registers, heartbeats, polls
                forever and claims nothing, which reads as "no work available"
                rather than as a misconfiguration. Jobs submitted from this
                console carry isolation.tier=sandboxed with
                allowFallback=false, and the coordinator's placement gate is
                fail-closed on sandbox_capable. In flashnode's capability
                probe only `argv` implies sandbox_capable — `--runner docker`
                deliberately does NOT (see inventory/capabilities.py). So the
                default subprocess runner, and docker, can never claim these
                tasks. */}
            <div className="mt-3 rounded-md border border-[var(--warning)]/30 bg-[var(--warning)]/[0.06] px-3 py-2.5">
              <p className="max-w-prose text-sm leading-relaxed">
                <span className="font-medium">
                  {String.fromCharCode(0x2014)} <code className="font-mono text-xs">--runner argv</code>{" "}
                  is required, not optional.
                </span>{" "}
                <span className="text-muted-foreground">
                  Jobs from this console request sandboxed isolation with no
                  fallback, and only the argv runner advertises that
                  capability. Without it the agent registers and polls
                  normally but never claims anything &mdash; which looks like
                  &ldquo;no work available&rdquo;, not like a mistake.
                </span>
              </p>
            </div>

            <h3 className="mt-8 text-sm font-semibold text-foreground">
              From a notebook or a rented pod, for a team workspace
            </h3>
            <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
              A Colab notebook or a rented pod (RunPod and similar) can&apos;t
              nest a Docker daemon, so the steps above don&apos;t apply and{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
                flashnode doctor
              </code>{" "}
              has nothing to check there &mdash; skip it. This path is also
              for a{" "}
              <Link href="/pools" className="text-primary hover:underline">
                team workspace
              </Link>{" "}
              you were invited to, not the open pool above: run{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
                login
              </code>{" "}
              the same way, then start work with{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
                --runner trusted
              </code>{" "}
              instead of <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">argv</code>.
            </p>
            <CopyBlock
              label="on the notebook or pod"
              code={`pip install flashnode
flashnode login --coordinator ${base}
flashnode work --coordinator ${base} --runner trusted`}
            />
            <div className="mt-3 rounded-md border border-[var(--warning)]/30 bg-[var(--warning)]/[0.06] px-3 py-2.5">
              <p className="max-w-prose text-sm leading-relaxed">
                <span className="font-medium">
                  {String.fromCharCode(0x2014)} <code className="font-mono text-xs">--runner trusted</code>{" "}
                  runs jobs unsandboxed.
                </span>{" "}
                <span className="text-muted-foreground">
                  No container, no network isolation &mdash; whatever the
                  job&apos;s command does, it does directly on this machine.
                  No job from outside your workspace ever runs here &mdash;
                  argv work is confined to your workspace by three
                  fail-closed checks. Only run this for a workspace
                  you&apos;d hand a shell account to.
                </span>
              </p>
            </div>
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
