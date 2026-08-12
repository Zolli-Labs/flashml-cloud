import { describe, expect, it, vi } from "vitest";

import {
  artifactFilename,
  describeDownloadFailure,
  downloadArtifact,
  signInHref,
  type ArtifactDownloadIO,
} from "./artifact-download";
import { ApiError, NotAuthenticated, NotFound } from "./cloud-api";

/**
 * THE BUG THESE TESTS PIN. Every artifact download answered 401. The card
 * rendered each file as a plain `<a href download>` at the API's
 * authenticated download route so the browser would follow that route's 307
 * into Alibaba OSS — and a NAVIGATION sends no `Authorization` header, so the
 * request never got as far as the redirect.
 *
 * The fix is a split, and each half is tested here as the sequence a
 * signed-in console actually performs: a mirrored key is resolved by ONE
 * authenticated JSON call and then navigated to (the presigned url is OSS's
 * own credential and needs no header), and an unmirrored key is fetched
 * through the API WITH the header, because its bytes exist nowhere else.
 *
 * Every side effect is injected. `vitest.config.ts` runs in the node
 * environment — there is no `document` and no `URL.createObjectURL` — which is
 * the point: what is being tested is which door each file goes through, and
 * that decision must be readable without a browser.
 */

/** A recording stand-in for everything that touches the network or the DOM. */
function io(
  overrides: Partial<ArtifactDownloadIO> = {}
): ArtifactDownloadIO & {
  urlCalls: [string, string][];
  blobCalls: [string, string][];
  navigated: [string, string][];
  saved: [string, string][];
} {
  const urlCalls: [string, string][] = [];
  const blobCalls: [string, string][] = [];
  const navigated: [string, string][] = [];
  const saved: [string, string][] = [];
  return {
    urlCalls,
    blobCalls,
    navigated,
    saved,
    async getUrl(jobId, key) {
      urlCalls.push([jobId, key]);
      return { storage: "coordinator", url: null };
    },
    async fetchBlob(jobId, key) {
      blobCalls.push([jobId, key]);
      return new Blob([`bytes of ${key}`]);
    },
    saveFromUrl(url, filename) {
      navigated.push([url, filename]);
    },
    saveFromBlob(_blob, filename) {
      saved.push(["<blob>", filename]);
    },
    ...overrides,
  };
}

const MIRRORED = "https://oss.example/jobs/job-1/shard-000/model.bin?Signature=x";

describe("downloadArtifact", () => {
  it("resolves a mirrored key to a presigned url and navigates to it", async () => {
    // THE 401 IS GONE, mirrored half. One authenticated JSON call the console
    // can always make, then a navigation to a url that carries its own grant:
    // no header to be missing, no CORS to fail, and no multi-gigabyte
    // checkpoint through this tab's memory.
    const deps = io({
      getUrl: async () => ({ storage: "oss", url: MIRRORED }),
    });

    const via = await downloadArtifact(
      { jobId: "job-1", key: "shard-000/model.bin", storage: "oss" },
      deps
    );

    expect(via).toBe("oss");
    expect(deps.navigated).toEqual([[MIRRORED, "shard-000__model.bin"]]);
    // The bytes never came through this page.
    expect(deps.blobCalls).toEqual([]);
    expect(deps.saved).toEqual([]);
  });

  it("falls back to the API for a key the manifest does not list", async () => {
    // THE PER-KEY FALLBACK, and it is not an edge case. Only ACCEPTED work is
    // mirrored (API repo hard rule 4), so a task that FAILED leaves its
    // stderr on the coordinator's disk under a job whose listing says "oss".
    // A console that read the job-level value as the answer for every key
    // would fail on exactly the file somebody opens after a failure.
    // The default `getUrl` above already answers `{coordinator, null}` — the
    // API's honest reply for a key it has no mirror of.
    const deps = io();

    const via = await downloadArtifact(
      { jobId: "job-1", key: "shard-001/stderr.txt", storage: "oss" },
      deps
    );

    expect(via).toBe("api");
    expect(deps.urlCalls).toEqual([["job-1", "shard-001/stderr.txt"]]);
    expect(deps.blobCalls).toEqual([["job-1", "shard-001/stderr.txt"]]);
    expect(deps.saved).toEqual([["<blob>", "shard-001__stderr.txt"]]);
    expect(deps.navigated).toEqual([]);
  });

  it("never asks for a url when the job is not mirrored at all", async () => {
    // THE DEPLOYMENT DEFAULT — no OSS configured anywhere, which is every
    // job this product has run. The listing already said so, so the round
    // trip that could only answer "no" is not made, and the download behaves
    // exactly as it did before any of this existed.
    const deps = io();

    const via = await downloadArtifact(
      { jobId: "job-1", key: "shard-000/model.bin", storage: "coordinator" },
      deps
    );

    expect(via).toBe("api");
    expect(deps.urlCalls).toEqual([]);
    expect(deps.saved).toEqual([["<blob>", "shard-000__model.bin"]]);
  });

  it("takes the API path for a storage value it does not recognise, and for none at all", async () => {
    // The conservative direction, deliberately. A fetch through our own API
    // with our own header works in every deployment; assuming an unknown
    // backend behaves like OSS would be a guess, and the console's rule
    // everywhere else is that an unrecognised value is named, not mapped onto
    // the nearest thing it resembles.
    for (const storage of ["s3", "", null]) {
      const deps = io();
      const via = await downloadArtifact(
        { jobId: "job-1", key: "a/b.bin", storage },
        deps
      );
      expect(via).toBe("api");
      expect(deps.urlCalls).toEqual([]);
    }
  });

  it("falls through to the API when the url route itself is a 404", async () => {
    // Deploy skew: the console and the API ship separately, so a browser can
    // briefly be talking to an API that has never heard of this route — and
    // its 404 is indistinguishable, from here, from "not your job". Both are
    // answered by trying the bytes through the API, which 404s in turn if the
    // job really is not visible, so nothing is hidden.
    const deps = io({
      getUrl: async () => {
        throw new NotFound("Not Found");
      },
    });

    const via = await downloadArtifact(
      { jobId: "job-1", key: "shard-000/model.bin", storage: "oss" },
      deps
    );

    expect(via).toBe("api");
    expect(deps.saved).toEqual([["<blob>", "shard-000__model.bin"]]);
  });

  it("lets a 401 out rather than retrying it as an anonymous download", async () => {
    // `NotAuthenticated` has to reach the caller and send the person to sign
    // in. Swallowing it here would turn an expired session into a download
    // that quietly does nothing — and the blob path would answer 401 too.
    const deps = io({
      getUrl: async () => {
        throw new NotAuthenticated();
      },
    });

    await expect(
      downloadArtifact(
        { jobId: "job-1", key: "shard-000/model.bin", storage: "oss" },
        deps
      )
    ).rejects.toBeInstanceOf(NotAuthenticated);
    expect(deps.blobCalls).toEqual([]);
  });

  it("propagates a failed byte fetch instead of reporting a download it did not make", async () => {
    const deps = io({
      fetchBlob: async () => {
        throw new ApiError(502, "coordinator unreachable");
      },
    });

    await expect(
      downloadArtifact(
        { jobId: "job-1", key: "shard-000/model.bin", storage: "coordinator" },
        deps
      )
    ).rejects.toBeInstanceOf(ApiError);
    expect(deps.saved).toEqual([]);
  });

  it("treats an empty url string as no url", async () => {
    // The contract says null; a truthiness check is what keeps an empty
    // string from being navigated to, which would leave the page where it is
    // and report success.
    const deps = io({ getUrl: async () => ({ storage: "oss", url: "" }) });

    expect(
      await downloadArtifact(
        { jobId: "job-1", key: "a/b.bin", storage: "oss" },
        deps
      )
    ).toBe("api");
  });

  it("asks for exactly the key it was given, never a re-derived one", async () => {
    const getUrl = vi.fn(async () => ({ storage: "oss", url: MIRRORED }));
    const deps = io({ getUrl });

    await downloadArtifact(
      { jobId: "job-1", key: "task-000/ckpt/step-20.json", storage: "oss" },
      deps
    );

    expect(getUrl).toHaveBeenCalledWith("job-1", "task-000/ckpt/step-20.json");
  });
});

describe("artifactFilename", () => {
  it("flattens the whole key so two tasks' logs cannot collide", () => {
    // Both of these end in `stdout.txt`. The last segment alone would save
    // one over the other — or have the browser append "(1)", which erases the
    // one fact (which task) the person opened them to find.
    expect(artifactFilename("shard-000/stdout.txt")).toBe(
      "shard-000__stdout.txt"
    );
    expect(artifactFilename("shard-001/stdout.txt")).toBe(
      "shard-001__stdout.txt"
    );
    expect(artifactFilename("shard-000/stdout.txt")).not.toBe(
      artifactFilename("shard-001/stdout.txt")
    );
  });

  it("leaves a single-segment key alone", () => {
    expect(artifactFilename("model.bin")).toBe("model.bin");
  });

  it("matches the name the API builds into its Content-Disposition", () => {
    // The header is what actually names the file in a deployed console: the
    // anchor's `download` attribute is honoured only same-origin, and neither
    // the API nor OSS is this site's origin. The two are written
    // independently — `_artifact_filename` in `apps/api/.../app.py` — so this
    // is the assertion that keeps them in step.
    expect(artifactFilename("shard-000/ckpt/step-20.json")).toBe(
      "shard-000__ckpt__step-20.json"
    );
  });
});

describe("signInHref", () => {
  it("sends a signed-out downloader back to the page they were on", () => {
    // A 401 on a download is a real signed-out state, and the job page's rule
    // for that is a redirect, not a message. Built from the live pathname
    // rather than a route literal so the card is still right if it is ever
    // rendered somewhere else.
    expect(signInHref("/jobs/job-1")).toBe("/sign-in?next=%2Fjobs%2Fjob-1");
  });

  it("encodes a path so it survives as one query value", () => {
    expect(signInHref("/w/pool a/jobs/x?y=1")).toBe(
      "/sign-in?next=%2Fw%2Fpool%20a%2Fjobs%2Fx%3Fy%3D1"
    );
  });
});

describe("describeDownloadFailure", () => {
  it("uses the API's own words when it gave any", () => {
    expect(describeDownloadFailure(new ApiError(502, "coordinator unreachable"))).toBe(
      "coordinator unreachable"
    );
  });

  it("says something plain, and invents no cause, for a failure that named nothing", () => {
    expect(describeDownloadFailure(new Error("   "))).toBe(
      "The download did not start. Try again."
    );
    expect(describeDownloadFailure(undefined)).toBe(
      "The download did not start. Try again."
    );
  });
});
