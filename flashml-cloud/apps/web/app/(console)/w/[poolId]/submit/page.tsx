"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, GithubLogo, Warning, XCircle } from "@phosphor-icons/react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { PageShell } from "@/components/shell/PageShell";
import { PageHeader } from "@/components/shell/PageHeader";
import { FindingsList } from "@/components/deploy/FindingsList";
import { PlanStrip } from "@/components/deploy/PlanStrip";
import { TemplateGallery } from "@/components/deploy/TemplateGallery";
import { YamlPanel, type ConfigCheck } from "@/components/deploy/YamlPanel";
import { useWorkspace } from "@/components/workspace/WorkspaceProvider";
import {
  ApiError,
  NotAuthenticated,
  PreflightRejected,
  preflightConfig,
  previewJobPlans,
  submitFromRepo,
  type PreflightFinding,
  type SubmitFromRepoResult,
} from "@/lib/cloud-api";
import { templateById, type DeployTemplate } from "@/lib/deploy/templates";
import { summariseRouting, type RoutingRead } from "@/lib/job-routing";
import { poolFleetCounts } from "@/lib/machine-scope";
import { workspacePath } from "@/lib/workspace-scope";

type Status = "idle" | "submitting" | "rejected" | "submitted" | "error";

/** Which way in. The gallery is first because it is the only one of the
 * three that works for somebody who has not already written a
 * `flashml.yaml` — which, before this page had a gallery, was everybody who
 * had not read the examples repository. The other two tabs are the flows
 * that were here before, unchanged and one click away. */
type DeployTab = "templates" | "yaml" | "repo";

export default function SubmitPage() {
  const router = useRouter();
  const { pool, machines } = useWorkspace();

  const [tab, setTab] = useState<DeployTab>("templates");
  // The id rather than the template object: this is the only thing selecting
  // a card actually changes about page state, and `templateById` turns it
  // back into the record. Two copies of the same template (one in state, one
  // in the registry) is how a card ends up labelled one thing while the
  // editor holds another.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [yamlText, setYamlText] = useState("");
  const [check, setCheck] = useState<ConfigCheck>({ status: "idle" });

  const [repo, setRepo] = useState("");
  const [ref, setRef] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [findings, setFindings] = useState<PreflightFinding[]>([]);
  const [result, setResult] = useState<SubmitFromRepoResult | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [routingRead, setRoutingRead] = useState<RoutingRead>({
    status: "loading",
  });

  const jobId = result?.job_id ?? null;

  /** Ask the router what this job will cost and how long it will take.
   *
   * ONCE, AFTER SUBMIT, AND NOT BEFORE — and the "not before" is a fact
   * about the API rather than a choice. `POST /v1alpha1/jobs/preview-plans`
   * takes a `job_id` or a whole compiled `JobSpec`; it does not take a
   * `flashml.yaml`. The compile that turns one into the other lives in the
   * API (`compile.py`) and runs at submit time, so there is no honest way to
   * price a template from this page before it has been submitted. Building a
   * JobSpec here to get an earlier answer is exactly the hand-built-spec
   * mistake that has hidden a real outage behind green tests in this
   * codebase before.
   *
   * It never rejects into the page's error state: a deployment with no
   * routing configured answers 200 with an explanation, and a failed read is
   * not a failed submission. The job exists either way. */
  const loadRouting = useCallback(
    (id: string) => {
      previewJobPlans(id)
        .then((preview) => setRoutingRead({ status: "previewed", preview }))
        .catch((err) => {
          if (err instanceof NotAuthenticated) {
            router.push(`/sign-in?next=/jobs/${id}`);
            return;
          }
          setRoutingRead({
            status: "unavailable",
            detail:
              err instanceof ApiError
                ? err.detail
                : err instanceof Error
                  ? err.message
                  : String(err),
          });
        });
    },
    [router]
  );

  useEffect(() => {
    if (!jobId) return;
    loadRouting(jobId);
  }, [jobId, loadRouting]);

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
  const selected: DeployTemplate | null = templateById(selectedId) ?? null;
  const fleet = poolFleetCounts(machines);

  /** The Deploy button. Loads the template into the editor and moves the
   * reader to it — and stops there, deliberately. Nothing is submitted, and
   * nothing can be: see `TEMPLATE_SUBMIT_GAP`. */
  function deployTemplate(template: DeployTemplate) {
    setSelectedId(template.id);
    setYamlText(template.yaml);
    // Any earlier verdict was about different text.
    setCheck({ status: "idle" });
    setTab("yaml");
  }

  async function handleCheck() {
    setCheck({ status: "checking" });
    try {
      const verdict = await preflightConfig(yamlText, poolId);
      // A rejected config is a 200 with `ok: false`, not a thrown error —
      // the caller asked for a verdict and got one. Only a malformed
      // request reaches the catch below.
      setCheck({
        status: "checked",
        ok: verdict.ok,
        findings: verdict.findings ?? [],
      });
    } catch (err) {
      if (err instanceof NotAuthenticated) {
        router.push(
          `/sign-in?next=${encodeURIComponent(workspacePath(poolId, "submit"))}`
        );
        return;
      }
      setCheck({
        status: "failed",
        detail:
          err instanceof ApiError
            ? err.detail
            : err instanceof Error
              ? err.message
              : "Something went wrong checking this config.",
      });
    }
  }

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
        router.push(
          `/sign-in?next=${encodeURIComponent(workspacePath(poolId, "submit"))}`
        );
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
      // `reading` — one form read top to bottom, which is what
      // `lib/console/page-width.ts` files this route as. Same container as
      // before: the hand-written `max-w-3xl mx-auto px-4 sm:px-6 py-8` is
      // `PageShell`'s string with the classes in a different order.
      <PageShell width="reading" className="space-y-6">
        {/* Was `<h1 className="text-2xl font-bold font-mono">` — a FIFTH
            heading treatment, and the only page setting a human-written
            title in the mono face. `PageHeader`'s rule is that the mono face
            marks text a MACHINE emitted, which here is the job name and id
            on the line below, not the words "Job submitted". */}
        <PageHeader
          title="Job submitted"
          meta={`${result.spec?.metadata?.name ?? result.name ?? result.job_id} · ${result.job_id}`}
        />

        {findings.length > 0 && (
          <Card className="border-warning/40 bg-surface">
            <CardHeader>
              <CardTitle className="font-mono text-sm text-warning-foreground">
                Preflight noted {findings.length} warning
                {findings.length === 1 ? "" : "s"}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <FindingsList findings={findings} />
            </CardContent>
          </Card>
        )}

        {/* The first moment this job can be priced honestly. Informational —
            the submit payload has no deadline, budget or plan field for a
            selection to write to, so these are the answer to "what did I
            just commit to" and not a form. */}
        <PlanStrip
          panel={summariseRouting(routingRead)}
          onRetry={() => {
            setRoutingRead({ status: "loading" });
            loadRouting(result.job_id);
          }}
        />

        <Button
          size="lg"
          className="w-full"
          onClick={() => router.push(`/jobs/${result.job_id}`)}
        >
          View job <ArrowRight className="w-4 h-4" data-icon="inline-end" />
        </Button>
      </PageShell>
    );
  }

  return (
    <PageShell width="reading" className="space-y-6">
      <PageHeader title="Deploy" meta={pool.name} />

      {/* The classNames restore this page's own surfaces, for the reason
          `app/(console)/admin/requests/page.tsx` records: the default
          variant paints the strip `bg-muted` and the active tab
          `bg-background`, which are one value per channel apart on this
          cream page — the strip would be invisible and the active tab a
          hole in it. */}
      <Tabs
        className="gap-4"
        value={tab}
        onValueChange={(value) => setTab(value as DeployTab)}
      >
        <TabsList
          activateOnFocus
          aria-label="How to deploy"
          className="h-auto border border-border bg-surface p-1 text-sm"
        >
          <TabsTrigger
            value="templates"
            className="px-3 py-1.5 data-active:bg-surface-2"
          >
            Templates
          </TabsTrigger>
          <TabsTrigger
            value="yaml"
            className="px-3 py-1.5 data-active:bg-surface-2"
          >
            flashml.yaml
          </TabsTrigger>
          <TabsTrigger
            value="repo"
            className="px-3 py-1.5 data-active:bg-surface-2"
          >
            GitHub repo
          </TabsTrigger>
        </TabsList>

        <TabsContent value="templates">
          <TemplateGallery selectedId={selectedId} onDeploy={deployTemplate} />
        </TabsContent>

        {/* `keepMounted`, for the reason the admin queue keeps its tabs
            mounted: an unmounted panel loses its state, and losing an edited
            `flashml.yaml` because somebody checked the repo tab is the one
            data loss this page could cause. */}
        <TabsContent value="yaml" keepMounted>
          <YamlPanel
            value={yamlText}
            onChange={(next) => {
              setYamlText(next);
              // The verdict was about the previous text. Keeping it visible
              // would let an edited config wear a clean bill of health.
              setCheck({ status: "idle" });
            }}
            template={selected}
            check={check}
            onCheck={handleCheck}
            onGoToRepo={() => setTab("repo")}
            disabled={check.status === "checking"}
          />
        </TabsContent>

        <TabsContent value="repo" keepMounted>
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
                  <p className="text-xs text-muted-foreground">
                    Needs a{" "}
                    <span className="font-mono">flashml.yaml</span> at its
                    root.
                  </p>
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
                    Defaults to <span className="font-mono">main</span> if left
                    blank.
                  </p>
                </div>

                <div>
                  <Label>Workspace</Label>
                  <p className="mt-1.5 text-sm">
                    Runs in <span className="font-medium">{pool.name}</span>
                  </p>
                </div>

                <p className="text-xs text-muted-foreground">
                  Jobs in this Workspace run without a container sandbox on
                  your workspace members&apos; Machines. Every member you
                  invited can run code this job stages.
                </p>

                {/* The real ratio, always — not only when it hits zero. A
                    binary "0 online" warning that goes silent at "1 of 40"
                    told a submitter nothing about how thin their fleet
                    actually is (density audit §3, gap 5). `poolFleetCounts`
                    reuses `isMachineOnline`, so this cannot disagree with
                    the Machines tab's own header row. */}
                <div
                  className={`flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm ${
                    fleet.online === 0
                      ? "border-warning/40 bg-warning/10 text-warning-foreground"
                      : "border-border bg-surface text-muted-foreground"
                  }`}
                >
                  {fleet.online === 0 && (
                    <Warning
                      className="w-4 h-4 shrink-0 mt-0.5"
                      weight="fill"
                    />
                  )}
                  <span>
                    {fleet.online} of {fleet.total} machine
                    {fleet.total === 1 ? "" : "s"} online in this Workspace
                    {fleet.online === 0
                      ? " right now — the job will queue until one connects."
                      : "."}
                  </span>
                </div>

                {status === "error" && errorMessage ? (
                  <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2.5 text-sm text-destructive">
                    <Warning
                      className="w-4 h-4 shrink-0 mt-0.5"
                      weight="fill"
                    />
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
            <Card className="mt-4 border-destructive/30">
              <CardHeader>
                <CardTitle className="text-sm font-mono text-destructive flex items-center gap-2">
                  <XCircle className="w-4 h-4" weight="fill" />
                  Preflight found problems with this job
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <p className="text-sm text-muted-foreground">
                  Nothing was queued. Fix the items below in the repo and
                  submit again.
                </p>
                <FindingsList findings={findings} />
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={resetToForm}
                >
                  Edit and try again
                </Button>
              </CardContent>
            </Card>
          )}
        </TabsContent>
      </Tabs>
    </PageShell>
  );
}
