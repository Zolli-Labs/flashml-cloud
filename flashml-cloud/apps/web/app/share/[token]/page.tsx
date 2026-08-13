import { cache } from "react";
import type { Metadata } from "next";
import { SandboxLifecycle } from "@/components/jobs/SandboxLifecycle";
import { JobRecovery } from "@/components/share/JobRecovery";
import {
  jobIsSettled,
  readJobPayload,
  type JobPayload,
} from "@/components/share/job-share";
import { cloudApiBase } from "@/lib/cloud-api";
import {
  redactForPublic,
  type SandboxEvent,
  type SandboxSession,
} from "@/lib/sandbox-session";

/**
 * The public evidence page. **No session, no account, no redirect.**
 *
 * Everything else in this console is private by construction: `middleware.ts`
 * sends a signed-out visitor to `/sign-in`, and reaching the product at all
 * needs a human to approve an access request. That is the right default and
 * it is also an automatic disqualification for the competition gate (G-1),
 * which requires a live URL that opens without a login — judged across a
 * weekend, when nobody is approving anything.
 *
 * So this route is the one door. It is deliberately the smallest door that
 * can exist:
 *
 *  - **Read-only.** No mutation, no form, no action.
 *  - **One record, named by an unguessable capability.** `share_token` is
 *    `shr_` + 256 bits from `secrets.token_urlsafe`. Guessing one is not a
 *    threat model; publishing one is, which is why nothing here ever echoes
 *    it.
 *  - **Server-rendered, redacted before it renders.** The narrowing happens
 *    in the API's SQL (`SESSION_SHARE_COLUMNS`, `JOB_SHARE_COLUMNS`), not in a
 *    serialiser and not in JSX. This page cannot print a withheld value
 *    because it never holds one.
 *  - **Not indexable.** The URL is a bearer capability; a search engine that
 *    crawled it would republish it. `robots: noindex, nofollow`.
 *
 * ---------------------------------------------------------------------------
 * ONE URL, TWO KINDS OF RECORD
 * ---------------------------------------------------------------------------
 *
 * `GET /v1alpha1/public/share/{token}` answers for both, with a DISCRIMINATED
 * envelope — `{kind: "session", session, events}` or `{kind: "job", job,
 * attempts, events}`. The token space is shared, so the page cannot know which
 * it has until the API says.
 *
 * A session is the FC hibernation story. A job is the fault-tolerance story —
 * a machine dying and the work resuming elsewhere — which is the product's
 * actual claim and was, until this route existed, the one thing a stranger
 * could not be shown. The path is still ONE segment (`/share/<token>`), so
 * `middleware.ts`'s existing anchored rule already covers it and must not be
 * touched: its own comment records that a loosened rewrite here is how a route
 * meant to satisfy one requirement quietly unauthenticates the console.
 */

/** Never cached, never statically generated: a session that is still running
 * must not be served from a snapshot taken when it was still being created. */
export const dynamic = "force-dynamic";

/** Nothing further can happen and no sandbox is still running. Mirrors
 * `sandbox_sessions.TERMINAL_STATES` — SUCCEEDED deliberately does not
 * qualify, because a succeeded session still owns a live sandbox until
 * cleanup is observed. */
const SETTLED = "TERMINATED";

/** How long a live page waits before asking again.
 *
 * A meta refresh rather than client-side polling, on purpose: this page has
 * to render for someone with no account, and a fetch loop in the browser
 * would put the bearer token into another request log for no gain. A settled
 * record emits no refresh at all — re-fetching a finished one forever is
 * just load. */
const REFRESH_SECONDS = 20;

/**
 * What one share token resolved to.
 *
 * A discriminated union mirroring the API's own envelope rather than a bag of
 * optional fields, so the two renderers below cannot be reached with the wrong
 * data — a job has no `SandboxSession` to hand `SandboxLifecycle`, and the
 * compiler is what says so.
 */
type SharePayload =
  | ({ kind: "job" } & JobPayload)
  | {
      kind: "session";
      session: SandboxSession;
      events: SandboxEvent[];
      /** The instant this read happened, captured HERE rather than in the
       * component below.
       *
       * Two reasons, and both matter. It is the honest reference point for a
       * still-running hibernation timer — "as of when we asked" — and reading
       * the clock during render is a purity violation the React compiler
       * rejects outright, because a value that changes on every re-render is
       * exactly what a server render and its hydration must not disagree
       * about. */
      fetchedAt: number;
    };

/**
 * Read one shared record.
 *
 * **Wrapped in React's `cache` so `generateMetadata` and the render below
 * share one round trip.** Both need the payload — the tab title has to say
 * which kind of record this is — and this route is rate-limited per client IP
 * (60/minute), so a second identical call would halve an anonymous visitor's
 * budget for nothing. Outside a request scope (the test suite calls the page
 * function directly) `cache` passes straight through, so nothing is memoised
 * across cases.
 *
 * **The failure doctrine is load-bearing and unchanged.** A non-200, an
 * unreachable API, and a body that is not a valid payload of either kind all
 * return null and produce the identical page, because distinguishing them for
 * an anonymous visitor tells a prober which tokens exist. Nothing here ever
 * renders a stack trace or the URL being called.
 *
 * Job first, session second, matching the API's own order. The session branch
 * stays tolerant about its envelope — `{session, events}` or a bare session
 * row carrying `events` — because that shape was never pinned to one of the
 * two and the tolerance already shipped; it is NOT tolerant about anything
 * else.
 */
const readShare = cache(async (token: string): Promise<SharePayload | null> => {
  const url = `${cloudApiBase()}/v1alpha1/public/share/${encodeURIComponent(token)}`;
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) return null;
    const body: unknown = await res.json();
    if (!body || typeof body !== "object") return null;

    // Checked against the stated `kind`, never sniffed from which keys turned
    // up. A payload that says "job" and does not parse as one falls through to
    // the session branch, fails it too, and becomes the same not-found page —
    // which is the correct end for a body this page cannot vouch for.
    const job = readJobPayload(body);
    if (job) return { kind: "job", ...job };

    const envelope = body as Record<string, unknown>;
    const raw =
      envelope.session && typeof envelope.session === "object"
        ? (envelope.session as Record<string, unknown>)
        : envelope;
    if (typeof raw.state !== "string") return null;

    const events = Array.isArray(envelope.events)
      ? (envelope.events as SandboxEvent[])
      : Array.isArray(raw.events)
        ? (raw.events as SandboxEvent[])
        : [];

    return {
      kind: "session",
      session: raw as unknown as SandboxSession,
      events,
      fetchedAt: Date.now(),
    };
  } catch {
    // A transport failure is not a reason to show a stranger a stack trace,
    // or the URL we were calling.
    return null;
  }
});

/**
 * The tab title, per kind.
 *
 * Dynamic because one URL now serves two different records and a job page
 * titled "Sandbox evaluation session" is simply wrong — this link gets pasted
 * into a submission form, and the tab is the first thing a judge reads.
 *
 * The unresolved case gets a deliberately empty title: it must describe
 * nothing, since a title that named a kind would tell a prober which kind of
 * token they had guessed at. `robots` is on every branch — the path is the
 * secret, and keeping it out of an index is not a nicety.
 */
export async function generateMetadata({
  params,
}: {
  params: Promise<{ token: string }>;
}): Promise<Metadata> {
  const { token } = await params;
  const payload = await readShare(token);
  const robots = { index: false, follow: false };

  if (payload?.kind === "job") {
    return {
      title: { absolute: "Shared training run | Zolli Cloud" },
      description:
        "A read-only record of one distributed training run on Zolli Cloud: which machines claimed work, which lost it, and where it finished.",
      robots,
    };
  }
  if (payload?.kind === "session") {
    return {
      title: { absolute: "Sandbox evaluation session | Zolli Cloud" },
      description:
        "A read-only record of one Alibaba FC Sandbox evaluation session: create, hibernate, wake, evaluate, clean up.",
      robots,
    };
  }
  return { title: { absolute: "Shared record | Zolli Cloud" }, robots };
}

export default async function SharedRecordPage({
  params,
  searchParams,
}: {
  params: Promise<{ token: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { token } = await params;
  const query = await searchParams;
  const payload = await readShare(token);

  const presenter = query.presenter === "1" || query.presenter === "true";

  if (!payload) {
    return (
      <Frame presenter={false}>
        <h1 className="title">This link isn&apos;t valid</h1>
        <p className="mt-3 max-w-prose text-sm leading-relaxed text-muted-foreground">
          A shared session link either expired or never existed. Nothing else
          can be said about it from here — and note that no part of the link
          you followed is repeated on this page.
        </p>
      </Frame>
    );
  }

  if (payload.kind === "job") {
    // Terminal states cannot change, so they stop asking. A job's `state` is
    // the last one the control plane OBSERVED rather than a live read, so a
    // finished run that nobody has polled can still read RUNNING and keep
    // refreshing — which is the harmless direction to be wrong in, and the
    // refresh is what eventually corrects it.
    const live = !jobIsSettled(payload.job.state);
    return (
      <Frame presenter={presenter}>
        {live && <meta httpEquiv="refresh" content={String(REFRESH_SECONDS)} />}
        <JobRecovery
          job={payload.job}
          attempts={payload.attempts}
          events={payload.events}
          presenter={presenter}
        />
      </Frame>
    );
  }

  // Narrowed here, on the server, before either object becomes a prop.
  // `SandboxLifecycle` is a client component, so its props are serialised
  // into this page's own HTML — a field JSX never renders is still in view
  // source, which makes redaction-at-render worth nothing.
  const { session, events } = redactForPublic(payload.session, payload.events);
  const live = session.state !== SETTLED;

  return (
    <Frame presenter={presenter}>
      {/* React hoists this into <head>. Only while something can still
          change. */}
      {live && <meta httpEquiv="refresh" content={String(REFRESH_SECONDS)} />}

      {/* Presenter mode is a 1280×720 budget, and this page's own chrome is
          the first thing that has to give: at the default size the heading
          and the explanation cost 300 of those 720 pixels, which is a third
          of the screen spent on text a room full of people has already been
          told. The words do not change — only the space they take. */}
      <h1 className={presenter ? "text-xl font-semibold" : "title"}>
        Sandbox evaluation session
      </h1>

      {/* The one line of explanation a stranger gets. It has to answer "what
          am I looking at" before the evidence below means anything, and it
          has to fit in the seconds before they decide the page is not for
          them. */}
      <p
        className={
          presenter
            ? "mt-1 text-xs leading-relaxed text-muted-foreground"
            : "mt-3 max-w-prose text-sm leading-relaxed text-muted-foreground"
        }
      >
        A read-only record of one evaluation run on Zolli Cloud: an evaluator
        was built inside an Alibaba FC Sandbox, hibernated while training
        happened elsewhere, then woken by a model artifact appearing in object
        storage to score it and shut down. Every number below was observed and
        recorded at the time; anything that was not observed says so.
      </p>

      <div className={presenter ? "mt-3" : "mt-6"}>
        <SandboxLifecycle
          session={session}
          events={events}
          visibility="public"
          // One clock for the server render and its hydration, read when
          // the session was read. Without it the "still hibernated" timer
          // differs between the two renders and React discards the tree it
          // was handed.
          now={payload.fetchedAt}
          defaultPresenter={presenter}
        />
      </div>
    </Frame>
  );
}

/** This route is in no layout group — `(marketing)` wears a top nav and
 * `(console)` a left rail, and a link handed to a stranger should carry
 * neither, since every destination in both is behind a sign-in they do not
 * have. So the page supplies its own `<main id="content">`: the root layout's
 * skip link points at that id and would otherwise land nowhere. */
function Frame({
  presenter,
  children,
}: {
  presenter: boolean;
  children: React.ReactNode;
}) {
  return (
    <main
      id="content"
      className={`mx-auto min-h-dvh w-full bg-background text-foreground sm:px-6 ${
        presenter ? "max-w-7xl px-4 py-4" : "max-w-5xl px-4 py-10"
      }`}
    >
      {children}
    </main>
  );
}
