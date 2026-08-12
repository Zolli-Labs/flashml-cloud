/** How the console gets one artifact's bytes onto somebody's disk.
 *
 * THE BUG THIS MODULE EXISTS TO FIX. The card rendered each file as a plain
 * `<a href download>` pointing at `GET /v1alpha1/jobs/{id}/artifacts/{key}`,
 * so that a browser would follow that route's 307 into a presigned Alibaba
 * OSS URL. It follows it — and it sends no `Authorization` header on the way,
 * because a NAVIGATION cannot. The route sits on `current_user`, which reads
 * a bearer token from that header and from nothing else. Every download
 * answered 401.
 *
 * REVERTING TO AN AUTHENTICATED `fetch()` IS NOT THE FIX, and this file is
 * where that is written down so it does not get "simplified" back. A fetch
 * would follow the same 307 to a third origin whose response carries no CORS
 * grant of ours, failing there instead; and it would pull the file through
 * this tab's memory as a Blob first, which for the ~100 MB–1 GB model weights
 * this product exists to produce is the wrong shape whether or not it works.
 *
 * SO THE TWO FACTS ARE SEPARATED, and which one applies is asked, never
 * assumed:
 *
 *   * **mirrored** — `GET .../artifact-url/{key}` is an ordinary
 *     authenticated JSON call (same origin as every other call this console
 *     makes, bearer header and all) and answers with a presigned OSS URL. That
 *     URL is a credential of OSS's, not of ours: scoped to one object, expiring
 *     on its own. A navigation may therefore carry it — no header to be
 *     missing, no CORS to fail, and the browser streams the bytes to disk
 *     itself instead of through this page.
 *   * **not mirrored** — the bytes exist only on the coordinator, so they must
 *     come through our API, which needs the header. That is a fetch and a
 *     Blob, exactly as the console did before. It is bounded by the
 *     coordinator's 5 GB disk, which is what makes holding one in memory
 *     acceptable here and nowhere else.
 *
 * THE PER-KEY FALLBACK IS NOT AN EDGE CASE. A job is stamped mirrored as a
 * whole, but only ACCEPTED work is mirrored (API repo hard rule 4), so a task
 * that failed leaves its `stderr.txt` on the coordinator's disk under a job
 * whose listing says `storage: "oss"`. Reading the job-level value as the
 * answer for every key would fail on exactly the file somebody opens after a
 * failure. The URL route answers per key, and a null url means "fetch it
 * through the API" — an ordinary reply, never an error.
 *
 * Lives in `lib/` and takes its side effects as an argument for the reason
 * `lib/bulk-download.ts` and `lib/job-artifacts.ts` give: `vitest.config.ts`
 * collects `**\/*.test.ts`, a `.tsx` component gets no coverage, and every
 * decision that could be wrong belongs where a test can reach it.
 */

import {
  NotFound,
  fetchJobArtifactBlob,
  getJobArtifactUrl,
  type JobArtifactUrl,
} from "./cloud-api";

/** What a downloaded artifact is saved as: the key with its separators
 * flattened, so `shard-000/stdout.txt` becomes `shard-000__stdout.txt`.
 *
 * The last segment alone is the obvious choice and the wrong one: every task
 * of a job writes `stdout.txt`, so a twenty-shard run would save twenty files
 * the browser silently renames `stdout (1).txt` … `stdout (19).txt`, erasing
 * the one fact — which task — the person opened them to find.
 *
 * The API builds the same name into its `Content-Disposition`, deliberately,
 * because that header is what actually decides the name in a deployed
 * console: the `download` attribute below is honoured only for a same-origin
 * url, and neither the API nor OSS is this site's origin. The two agree so
 * that a file saves under one name whichever path it came down; they are not
 * derived from each other, so a test on each side pins its own half. */
export function artifactFilename(key: string): string {
  return key.replace(/\//g, "__");
}

/** Which door a download actually went through. Returned so a caller can say
 * something true about what it did — an OSS navigation reports nothing back,
 * an API fetch completes — and so a test can assert the route taken rather
 * than infer it from side effects. */
export type ArtifactDownloadVia = "oss" | "api";

/** Everything about a download that touches the browser or the network, in
 * one injectable bundle. The component passes nothing and gets
 * `browserArtifactDownloadIO`; a test passes fakes and never needs a DOM. */
export interface ArtifactDownloadIO {
  getUrl(jobId: string, key: string): Promise<JobArtifactUrl>;
  fetchBlob(jobId: string, key: string): Promise<Blob>;
  /** Send the browser to a URL that will save rather than render. */
  saveFromUrl(url: string, filename: string): void;
  /** Save bytes already in hand. */
  saveFromBlob(blob: Blob, filename: string): void;
}

/** How long to keep a blob URL alive after its anchor has been clicked.
 *
 * Not a performance knob and not a guess at how long a save takes: revoking
 * synchronously after `.click()` races the browser's own handling of that
 * click in every engine, and the failure is a download that silently does
 * nothing. One turn of the event loop is what the revoke is waiting for;
 * this is that, with room. Leaking the URL instead is not an option — it pins
 * the whole file in memory until the tab closes. */
const BLOB_URL_LIFETIME_MS = 60_000;

function clickAnchor(href: string, filename: string): void {
  const a = document.createElement("a");
  a.href = href;
  // Honoured only for a same-origin url, which a presigned OSS url is not —
  // there the API's `Content-Disposition` names the file instead. Set anyway
  // because it costs nothing and is correct for the blob url below, which IS
  // same-origin. Nothing here depends on it cross-origin.
  a.download = filename;
  a.rel = "noopener";
  // Appended before the click and removed after: a detached anchor's click is
  // honoured by Chrome and not reliably by Firefox, and this is the one place
  // that difference costs a file.
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export const browserArtifactDownloadIO: ArtifactDownloadIO = {
  getUrl: getJobArtifactUrl,
  fetchBlob: fetchJobArtifactBlob,
  saveFromUrl: clickAnchor,
  saveFromBlob(blob, filename) {
    const href = URL.createObjectURL(blob);
    try {
      clickAnchor(href, filename);
    } finally {
      setTimeout(() => URL.revokeObjectURL(href), BLOB_URL_LIFETIME_MS);
    }
  },
};

export interface ArtifactDownloadRequest {
  jobId: string;
  /** The listing's own key, relative to the job prefix — what both artifact
   * routes take. Never re-derived from anything. */
  key: string;
  /** The listing's job-level `storage`, verbatim. Used only to skip a round
   * trip that could not possibly return a url; see `downloadArtifact`. */
  storage: string | null;
}

/**
 * Download one artifact, asking the API where its bytes are when — and only
 * when — that question can have a useful answer.
 *
 * THE LOOKUP IS SKIPPED UNLESS THE JOB SAYS `"oss"`. The listing already
 * reports `storage` per job, so a deployment with no OSS configured (the
 * default, and everything this product has ever run) never makes the extra
 * call at all: it goes straight to the API and behaves exactly as it did
 * before any of this existed. Anything OTHER than the literal `"oss"` takes
 * that same path, including a `storage` value this console does not
 * recognise — the conservative direction, since a fetch through our own API
 * with our own header works in every deployment, and guessing that an
 * unknown backend behaves like OSS would not.
 *
 * A `NotFound` FROM THE LOOKUP FALLS THROUGH RATHER THAN FAILING. The console
 * and the API deploy separately, so a browser running this code can briefly
 * be talking to an API that has never heard of the `artifact-url` route and
 * answers 404 for the route itself — indistinguishable, from here, from "not
 * your job". Both are handled by trying the bytes through the API: if the job
 * really is not visible, that call answers 404 too and the person sees the
 * same error either way. (It does not rescue a MIRRORED key against such an
 * API — that download 307s into OSS and the fetch fails CORS — but that was
 * already true before this route existed, and every unmirrored key works.)
 * No other error is swallowed: a `NotAuthenticated` must reach the caller and
 * send the person to sign in, not be quietly retried.
 */
export async function downloadArtifact(
  { jobId, key, storage }: ArtifactDownloadRequest,
  io: ArtifactDownloadIO = browserArtifactDownloadIO
): Promise<ArtifactDownloadVia> {
  const filename = artifactFilename(key);

  if (storage === "oss") {
    let told: JobArtifactUrl | null = null;
    try {
      told = await io.getUrl(jobId, key);
    } catch (err) {
      if (!(err instanceof NotFound)) throw err;
    }
    if (told?.url) {
      io.saveFromUrl(told.url, filename);
      return "oss";
    }
  }

  io.saveFromBlob(await io.fetchBlob(jobId, key), filename);
  return "api";
}

/** What to tell somebody whose download did not happen.
 *
 * The API's own words when it gave any (`ApiError.detail` is a plain
 * `message`), and a plain sentence when the failure was a transport one that
 * named nothing. Nothing here invents a cause: "the network" and "the API
 * refused" are different facts and this console cannot tell them apart from
 * the outside. */
export function describeDownloadFailure(err: unknown): string {
  const detail = err instanceof Error ? err.message.trim() : "";
  return detail || "The download did not start. Try again.";
}

/** Where to send somebody whose download failed because they are signed out.
 *
 * A 401 on a download is a real signed-out state, exactly as it is on the job
 * page's own reads — and the page's rule for that is a redirect to sign-in,
 * never a message. Rendering "sign-in required" in a toast beside a page full
 * of data the session already fetched would be both confusing and a dead end.
 *
 * Built from the CURRENT pathname rather than a route literal: this lives one
 * layer below the page, and a component that hard-codes where it thinks it is
 * rendered is wrong the first time it is rendered somewhere else. The value is
 * this app's own location, not anything a visitor supplied, and it is encoded
 * so a path with a query-significant character survives the round trip. */
export function signInHref(pathname: string): string {
  return `/sign-in?next=${encodeURIComponent(pathname)}`;
}
