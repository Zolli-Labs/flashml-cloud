import Link from "next/link";

// How ownership actually works: who owns what, what a workspace is and is
// not, and the two contracts (authoring, sandboxing) every job runs under.
//
// This page exists because nobody could explain the ownership model without
// re-deriving it from the code, including the person who designed it. It is
// the one place all of that is written down together. No hooks, no fetch —
// everything here is either structural (true of every workspace) or a fact
// about the product, so a static server component is enough.

const SECTIONS = [
  { id: "ownership", label: "Who owns what" },
  { id: "authoring", label: "One repo, one job" },
  { id: "contract", label: "Your code's contract" },
  { id: "sandbox", label: "How machines run your code" },
  { id: "access", label: "Getting in" },
] as const;

export default function HowItWorksPage() {
  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <div className="lg:grid lg:grid-cols-[1fr_180px] lg:gap-12">
        <div className="min-w-0">
          <h1 className="title">How it works</h1>
          <p className="mt-3 max-w-prose text-sm leading-relaxed text-muted-foreground">
            Who owns a machine, who a workspace&apos;s jobs actually run on, and
            where the credit for finished work goes. None of this is
            enforced by convention &mdash; it is how the schema and the
            scheduler are built, so treat every claim on this page as a
            description of what already happens, not a policy.
          </p>

          {/* Naming, acknowledged once and up front, so it never has to be
              re-explained inline below. Three words for one row in one
              table, and not all of them agree yet. */}
          <div className="mt-4 rounded-md border border-border bg-surface px-3 py-2.5">
            <p className="max-w-prose text-sm leading-relaxed text-muted-foreground">
              <span className="font-medium text-foreground">
                A note on names before the rest of this page:
              </span>{" "}
              this console calls it a <strong>workspace</strong>. A few
              older screens &mdash; the switcher in the rail, some toasts, the
              submit form under a workspace &mdash; still say{" "}
              <strong>Crew</strong>, left over from before the rename and not
              yet swept. The API and the database call it{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
                pool
              </code>{" "}
              throughout, so a raw error message will use that word. All
              three names point at the same row.
            </p>
          </div>

          <section id="ownership" className="scroll-mt-20 pt-12">
            <h2 className="text-lg font-semibold">Who owns what</h2>
            <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
              A machine belongs to the person who enrolled it &mdash; never
              to a workspace. One person can own several machines; a machine
              is never co-owned. A workspace only gets to run work on a
              machine because its owner explicitly ticked it in from{" "}
              <Link href="/account/machines" className="text-brand-foreground hover:underline">
                My Zollis
              </Link>
              . That opt-in is why &ldquo;my machines&rdquo; and &ldquo;this
              workspace&apos;s machines&rdquo; are two different lists in this
              console rather than one &mdash; they answer different
              questions.
            </p>

            <div className="mt-6 panel p-5">
              <Ownership />
            </div>

            <p className="mt-6 max-w-prose text-sm leading-relaxed text-muted-foreground">
              A job belongs to a workspace, and it can only place work on a
              machine that has been opted into that workspace &mdash; not
              every machine its owner has, and not every machine in the
              product. When a task completes, the credit for it is recorded
              against the person who owns the machine that ran it, not
              against the workspace the job belonged to. A workspace can
              finish a hundred jobs and be credited for none of them; every
              one of those hundred is credited to whoever&apos;s machines
              actually did the work.
            </p>
          </section>

          <hr className="rule-fade mt-12 border-0" />

          <section id="authoring" className="scroll-mt-20 pt-12">
            <h2 className="text-lg font-semibold">One repo, one job</h2>
            <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
              A job is a GitHub repository with a{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
                flashml.yaml
              </code>{" "}
              at its root. That file <em>is</em> the job definition &mdash;
              there is no separate job-authoring step and no second place a
              job&apos;s configuration can live.
            </p>
            <p className="mt-4 max-w-prose text-sm leading-relaxed text-muted-foreground">
              This surprises everyone: different job types are not different
              repos, or different files in the same repo. They are different{" "}
              <strong>git branches</strong> of the same repo, each with its
              own{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
                flashml.yaml
              </code>
              . That is why the submit form has a{" "}
              <span className="font-medium text-foreground">Branch</span>{" "}
              field &mdash; it is not a convenience for pinning a commit, it
              is how you pick which job you are actually running.
            </p>
          </section>

          <hr className="rule-fade mt-12 border-0" />

          <section id="contract" className="scroll-mt-20 pt-12">
            <h2 className="text-lg font-semibold">Your code&apos;s contract</h2>
            <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
              A job&apos;s contract with your code is small on purpose:{" "}
              <strong>CLI flags in, </strong>
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
                metrics.json
              </code>
              <strong> out</strong>. Declared inputs are staged read-only at{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
                /work/inputs/
              </code>{" "}
              before your command runs. Whatever you want kept &mdash;
              results, checkpoints, the metrics file &mdash; has to be
              written to{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
                /work/out/
              </code>
              . Nothing written anywhere else survives the run.
            </p>
          </section>

          <hr className="rule-fade mt-12 border-0" />

          <section id="sandbox" className="scroll-mt-20 pt-12">
            <h2 className="text-lg font-semibold">
              How machines run your code
            </h2>
            <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
              Machines run tasks in a hardened container: non-root, a
              read-only root filesystem, and{" "}
              <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
                --network none
              </code>{" "}
              &mdash; no network at all, not even to fetch a package. A repo
              that tries to reach the network at run time fails preflight
              before anything is scheduled, rather than failing halfway
              through on somebody else&apos;s machine.
            </p>
          </section>

          <hr className="rule-fade mt-12 border-0" />

          <section id="access" className="scroll-mt-20 pt-12">
            <h2 className="text-lg font-semibold">Getting in</h2>
            <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
              Two separate doors, and deliberately so. An admin has to
              approve your account before you can use the product at all
              &mdash; that is admission. Joining a workspace, by invite or
              join code, is a different action that puts you in a group; it
              grants no product access by itself. An account can be admitted
              and belong to zero workspaces, and an invite can be sitting
              unopened on an account that was never admitted. Neither state
              implies the other.
            </p>
          </section>

          <section className="panel mt-14 p-5">
            <h2 className="text-sm font-semibold">More detail</h2>
            <p className="mt-1.5 max-w-prose text-sm leading-relaxed text-muted-foreground">
              Commands for enrolling a machine and reading a job&apos;s placement
              and ledger live in{" "}
              <Link href="/docs" className="text-brand-foreground hover:underline">
                Documentation
              </Link>
              . This page is the model; that one is the reference.
            </p>
          </section>
        </div>

        {/* Sticky contents rail. Hidden below lg, matching the Documentation
            page this one is meant to sit beside. */}
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

// Hand-authored, no libraries. `currentColor` for every stroke and every
// character of text so the diagram inherits `text-foreground` and needs no
// dark-mode variant of its own. `role="img"` collapses the children for
// assistive tech in favour of `aria-label`, so the label below has to carry
// the whole diagram on its own — it is not a caption, it is the content.
function Ownership() {
  return (
    <svg
      viewBox="0 0 760 380"
      className="mx-auto h-auto w-full max-w-2xl text-foreground"
      role="img"
      aria-label="Diagram: a person owns their machines. The person opts machines into a workspace. Jobs belong to the workspace and run only on the machines opted into it. When a job completes, credit for the work goes back to the person who owns the machine, not to the workspace."
    >
      <defs>
        <marker
          id="how-it-works-arrow"
          viewBox="0 0 10 10"
          refX="8.5"
          refY="5"
          markerWidth="7"
          markerHeight="7"
          orient="auto-start-reverse"
        >
          <path d="M0,0 L10,5 L0,10 z" fill="currentColor" />
        </marker>
      </defs>

      <g fill="none" stroke="currentColor" strokeWidth="1.6">
        {/* Person */}
        <circle cx="70" cy="168" r="12" />
        <path d="M48,212 Q70,178 92,212" />
      </g>
      <text
        x="70"
        y="234"
        textAnchor="middle"
        fontSize="14"
        fontWeight="600"
        fill="currentColor"
      >
        You
      </text>

      {/* Machines: two overlapping laptop glyphs, standing for "one or
          more" rather than any exact count. */}
      <g fill="none" stroke="currentColor" strokeWidth="1.6">
        <rect x="246" y="140" width="50" height="32" rx="3" />
        <line x1="239" y1="175" x2="303" y2="175" strokeLinecap="round" />
        <rect x="222" y="152" width="50" height="32" rx="3" fill="var(--surface)" />
        <line x1="215" y1="187" x2="279" y2="187" strokeLinecap="round" />
      </g>
      <text
        x="257"
        y="214"
        textAnchor="middle"
        fontSize="14"
        fontWeight="600"
        fill="currentColor"
      >
        Machines
      </text>

      {/* Workspace boundary, containing the Jobs box */}
      <rect
        x="430"
        y="70"
        width="280"
        height="260"
        rx="18"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
      />
      <text
        x="570"
        y="100"
        textAnchor="middle"
        fontSize="15"
        fontWeight="600"
        fill="currentColor"
      >
        Workspace
      </text>

      <rect
        x="500"
        y="190"
        width="140"
        height="64"
        rx="10"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
      />
      <text
        x="570"
        y="227"
        textAnchor="middle"
        fontSize="14"
        fontWeight="600"
        fill="currentColor"
      >
        Jobs
      </text>

      {/* owns: person -> machines */}
      <line
        x1="104"
        y1="168"
        x2="210"
        y2="168"
        stroke="currentColor"
        strokeWidth="1.6"
        markerEnd="url(#how-it-works-arrow)"
      />
      <text x="157" y="156" textAnchor="middle" fontSize="12" fill="currentColor">
        owns
      </text>

      {/* opted into: machines -> workspace */}
      <line
        x1="310"
        y1="168"
        x2="426"
        y2="168"
        stroke="currentColor"
        strokeWidth="1.6"
        markerEnd="url(#how-it-works-arrow)"
      />
      <text x="368" y="156" textAnchor="middle" fontSize="12" fill="currentColor">
        opted into
      </text>

      {/* jobs run here: jobs box -> workspace label */}
      <line
        x1="570"
        y1="188"
        x2="570"
        y2="128"
        stroke="currentColor"
        strokeWidth="1.6"
        markerEnd="url(#how-it-works-arrow)"
      />
      <text x="586" y="162" fontSize="12" fill="currentColor">
        run here
      </text>

      {/* credit flows back to person: jobs -> person */}
      <path
        d="M570,254 C570,346 70,346 70,224"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        markerEnd="url(#how-it-works-arrow)"
      />
      <text x="320" y="366" textAnchor="middle" fontSize="12" fill="currentColor">
        credit flows back to person
      </text>
    </svg>
  );
}
