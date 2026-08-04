"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  GithubLogo,
  Warning,
  XCircle,
} from "@phosphor-icons/react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useWorkspace } from "@/components/workspace/WorkspaceProvider";
import {
  ApiError,
  NotAuthenticated,
  PreflightRejected,
  submitFromRepo,
  type PreflightFinding,
  type SubmitFromRepoResult,
} from "@/lib/cloud-api";
import { isMachineOnline } from "@/lib/machine-scope";
import { workspacePath } from "@/lib/workspace-scope";

type Status = "idle" | "submitting" | "rejected" | "submitted" | "error";

export default function SubmitPage() {
  const router = useRouter();
  const { pool, machines } = useWorkspace();
  const [repo, setRepo] = useState("");
  const [ref, setRef] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [findings, setFindings] = useState<PreflightFinding[]>([]);
  const [result, setResult] = useState<SubmitFromRepoResult | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Hooks stay above the `pool` guard below: it never actually fires at
  // runtime (WorkspaceGate only mounts this tab once the workspace is
  // "ready"), but a conditional return above a hook call would still be a
  // Rules-of-Hooks violation regardless of whether that branch is ever
  // reached.
  if (!pool) return null;

  // `pool.id` directly, inline below: TypeScript's narrowing above does not
  // reach into `handleSubmit`, a separate function value — it only applies
  // within this function's own control flow. Captured once here instead of
  // sprinkling `pool.id` (which the type checker would flag as possibly
  // null once read from inside that closure).
  const poolId = pool.id;
  const canSubmit = repo.trim().length > 0 && status !== "submitting";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setStatus("submitting");
    setErrorMessage(null);

    try {
      const job = await submitFromRepo(
        repo.trim(),
        ref.trim() || undefined,
        poolId
      );
      setResult(job);
      setFindings(job.findings ?? []);
      setStatus("submitted");
    } catch (err) {
      if (err instanceof NotAuthenticated) {
        router.push(`/sign-in?next=${encodeURIComponent(workspacePath(poolId, "submit"))}`);
        return;
      }
      if (err instanceof PreflightRejected) {
        // Preflight already ran every check before refusing — this is the
        // *complete* set of findings for this repo, not just the first one
        // hit. An error anywhere in the set means the API refused the
        // whole submission (nothing was queued, nothing uploaded), so
        // there is nothing to "submit anyway" here: the repo has to change.
        setFindings(err.findings);
        setResult(null);
        setStatus("rejected");
        return;
      }
      setStatus("error");
      setErrorMessage(
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
          ? err.message
          : "Something went wrong submitting this repo."
      );
    }
  }

  function resetToForm() {
    setStatus("idle");
    setFindings([]);
    setResult(null);
    setErrorMessage(null);
  }

  // Once submitted, the job already exists — the API's from-repo endpoint
  // only ever refuses (400, nothing queued) when preflight finds an error;
  // a warning never blocks it (preflight.py: "a warning is advice that does
  // not block"). So there is no separate confirmation round trip for
  // warnings-only findings to gate. What this screen gives the user instead
  // is the honest version of "submit anyway": the warnings are shown in
  // full, up front, and the person has to read them and click through to
  // the job rather than being silently redirected as if nothing was flagged.
  if (status === "submitted" && result) {
    return (
      <div className="max-w-3xl mx-auto px-4 sm:px-6 py-8 space-y-6">
        <div>
          <h1 className="text-2xl font-bold font-mono">Job submitted</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {result.spec?.metadata?.name ?? result.name ?? result.job_id} ·{" "}
            {result.job_id}
          </p>
        </div>

        {findings.length > 0 && (
          <Card className="border-amber-400/30">
            <CardHeader>
              <CardTitle className="text-sm font-mono text-amber-400">
                Preflight noted {findings.length} warning
                {findings.length === 1 ? "" : "s"}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <FindingsList findings={findings} />
            </CardContent>
          </Card>
        )}

        <Button
          size="lg"
          className="w-full"
          onClick={() => router.push(`/jobs/${result.job_id}`)}
        >
          View job <ArrowRight className="w-4 h-4" data-icon="inline-end" />
        </Button>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold font-mono">Submit a repo</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Paste a public GitHub repo with a <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">flashml.yaml</code> at its
          root. We stage the code, run it through preflight, and hand it to
          the next available machine.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-mono flex items-center gap-2">
            <GithubLogo className="w-4 h-4" />
            Repository
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="repo">GitHub URL or owner/name</Label>
              <Input
                id="repo"
                name="repo"
                type="text"
                inputMode="url"
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                placeholder="https://github.com/acme/trainer"
                value={repo}
                onChange={(e) => setRepo(e.target.value)}
                disabled={status === "submitting"}
                className="font-mono"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="ref">Branch</Label>
              <Input
                id="ref"
                name="ref"
                type="text"
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                placeholder="main"
                value={ref}
                onChange={(e) => setRef(e.target.value)}
                disabled={status === "submitting"}
                className="font-mono"
              />
              <p className="text-xs text-muted-foreground">
                Defaults to <span className="font-mono">main</span> if left blank.
              </p>
            </div>

            <div>
              <Label>Workspace</Label>
              <p className="mt-1.5 text-sm">
                Runs in <span className="font-medium">{pool.name}</span>
              </p>
            </div>

            <p className="text-xs text-muted-foreground">
              Pool jobs run without a container sandbox on your
              team&apos;s machines. Every member you invited can run
              code this job stages.
            </p>

            {machines.filter(isMachineOnline).length === 0 ? (
              <div className="flex items-start gap-2 rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-2.5 text-sm text-amber-400">
                <Warning className="w-4 h-4 shrink-0 mt-0.5" weight="fill" />
                <span>
                  0 workers online in this workspace right now — the job
                  will queue until one connects.
                </span>
              </div>
            ) : null}

            {status === "error" && errorMessage ? (
              <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2.5 text-sm text-destructive">
                <Warning className="w-4 h-4 shrink-0 mt-0.5" weight="fill" />
                <span>{errorMessage}</span>
              </div>
            ) : null}

            <Button
              type="submit"
              size="lg"
              className="w-full"
              disabled={!canSubmit}
            >
              {status === "submitting" ? "Running preflight…" : "Submit"}
            </Button>
          </form>
        </CardContent>
      </Card>

      {status === "rejected" && (
        <Card className="border-destructive/30">
          <CardHeader>
            <CardTitle className="text-sm font-mono text-destructive flex items-center gap-2">
              <XCircle className="w-4 h-4" weight="fill" />
              Preflight found problems with this job
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Nothing was queued. Fix the items below in the repo and submit
              again.
            </p>
            <FindingsList findings={findings} />
            <Button type="button" variant="ghost" size="sm" onClick={resetToForm}>
              Edit and try again
            </Button>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function FindingsList({ findings }: { findings: PreflightFinding[] }) {
  return (
    <ul className="space-y-2.5">
      {findings.map((f, i) => (
        <li
          key={i}
          className={`flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-sm ${
            f.level === "error"
              ? "border-destructive/30 bg-destructive/10"
              : "border-amber-400/30 bg-amber-400/10"
          }`}
        >
          {f.level === "error" ? (
            <XCircle
              className="w-4 h-4 shrink-0 mt-0.5 text-destructive"
              weight="fill"
            />
          ) : (
            <Warning
              className="w-4 h-4 shrink-0 mt-0.5 text-amber-400"
              weight="fill"
            />
          )}
          <div className="min-w-0">
            <div
              className={`font-mono text-[10px] uppercase tracking-wide ${
                f.level === "error" ? "text-destructive" : "text-amber-400"
              }`}
            >
              {f.level} · {f.code}
            </div>
            {/* Quoted verbatim — the API's message already names the
                offending package and which curated image would provide it.
                Paraphrasing here would throw that specificity away. */}
            <p className="text-foreground/90">{f.message}</p>
          </div>
        </li>
      ))}
    </ul>
  );
}
