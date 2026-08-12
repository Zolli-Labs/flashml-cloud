import type { Metadata } from "next";
import { SandboxLifecycle } from "@/components/jobs/SandboxLifecycle";
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
 *  - **One session, named by an unguessable capability.** `share_token` is
 *    256 bits from `secrets.token_urlsafe`. Guessing one is not a threat
 *    model; publishing one is, which is why nothing here ever echoes it.
 *  - **Server-rendered, redacted before it renders.** The API's public
 *    projection drops owner, pool, machine and sandbox ids
 *    (`SESSION_SHARE_COLUMNS`), and `summariseSandboxSession(..., {visibility:
 *    "public"})` narrows what is left to suffixes. This page cannot print a
 *    full identifier because it never holds one.
 *  - **Not indexable.** The URL is a bearer capability; a search engine that
 *    crawled it would republish it. `robots: noindex, nofollow`.
 */

export const metadata: Metadata = {
  title: { absolute: "Sandbox evaluation session | Zolli Cloud" },
  description:
    "A read-only record of one Alibaba FC Sandbox evaluation session: create, hibernate, wake, evaluate, clean up.",
  // The path IS the secret. Keeping it out of an index is not a nicety.
  robots: { index: false, follow: false },
};

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
 * session emits no refresh at all — re-fetching a finished record forever is
 * just load. */
const REFRESH_SECONDS = 20;

interface PublicPayload {
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
}

/**
 * Read one public session.
 *
 * Tolerant about the envelope — `{session, events}` or a bare session row
 * carrying `events` — because the route is being written alongside this page
 * and the shape of "session + events, redacted" was never pinned to one of
 * those two. It is NOT tolerant about anything else: a non-200, an
 * unreachable API and a body that is not a session all return null and
 * produce the same page, because distinguishing them for an anonymous
 * visitor tells a prober which tokens exist.
 */
async function readPublicSession(token: string): Promise<PublicPayload | null> {
  const url = `${cloudApiBase()}/v1alpha1/public/sandbox-sessions/${encodeURIComponent(token)}`;
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) return null;
    const body: unknown = await res.json();
    if (!body || typeof body !== "object") return null;

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
      session: raw as unknown as SandboxSession,
      events,
      fetchedAt: Date.now(),
    };
  } catch {
    // A transport failure is not a reason to show a stranger a stack trace,
    // or the URL we were calling.
    return null;
  }
}

export default async function SharedSandboxSessionPage({
  params,
  searchParams,
}: {
  params: Promise<{ token: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { token } = await params;
  const query = await searchParams;
  const payload = await readPublicSession(token);

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
