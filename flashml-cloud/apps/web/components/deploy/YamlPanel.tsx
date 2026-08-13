"use client";

import { ArrowRight, CheckCircle, Warning } from "@phosphor-icons/react";

import { Button } from "@/components/ui/button";
import { FindingsList } from "@/components/deploy/FindingsList";
import type { PreflightFinding } from "@/lib/cloud-api";
import {
  TEMPLATE_SUBMIT_GAP,
  type DeployTemplate,
} from "@/lib/deploy/templates";

/** One attempt at a verdict, classified rather than thrown — the same shape
 * `RoutingRead` and `ArtifactsRead` use, and for the same reason: this panel
 * renders every outcome, so every outcome has to be a value.
 *
 * `checked` covers BOTH a clean config and a rejected one. That is not a
 * simplification: `POST /v1alpha1/preflight` answers 200 either way, because
 * the caller asked for a verdict and got one. Only a malformed request is an
 * error, and that is what `failed` is. Collapsing "your YAML is wrong" into
 * the same state as "the check could not run" would tell somebody their
 * config was bad when the API was simply unreachable. */
export type ConfigCheck =
  | { status: "idle" }
  | { status: "checking" }
  | { status: "checked"; ok: boolean; findings: PreflightFinding[] }
  | { status: "failed"; detail: string };

/**
 * The `flashml.yaml` editor: where a template lands, and where it is checked.
 *
 * WHAT THIS TAB CAN AND CANNOT DO, because the difference is the whole
 * design of it. It can validate: `POST /v1alpha1/preflight` runs the exact
 * checks `from-repo` refuses on and **creates nothing** — no job row, no
 * artifact, no coordinator call. It cannot submit, because no route on this
 * API turns `flashml.yaml` text into a job: every route that creates one
 * takes a whole working tree, since a job is the config AND the entrypoint
 * the config names. See `TEMPLATE_SUBMIT_GAP`.
 *
 * So the primary action here is the honest one. A "Submit" button that
 * silently needed a repository the reader does not have would be worse than
 * no button, and inventing an upload path out of a textarea would submit a
 * job with no code in it.
 */
export function YamlPanel({
  value,
  onChange,
  template,
  check,
  onCheck,
  onGoToRepo,
  disabled,
}: {
  value: string;
  onChange: (next: string) => void;
  /** The template this text started as, when it started as one. Named so
   * the reader knows what they are holding and where it came from — a
   * textarea full of YAML looks the same whoever wrote it. */
  template: DeployTemplate | null;
  check: ConfigCheck;
  onCheck: () => void;
  onGoToRepo: () => void;
  disabled: boolean;
}) {
  const empty = value.trim().length === 0;

  return (
    <div className="space-y-4">
      <div>
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <label htmlFor="flashml-yaml" className="label-caps">
            flashml.yaml
          </label>
          {template && (
            <p className="meta">
              {template.name} · {template.source}
            </p>
          )}
        </div>
        <textarea
          id="flashml-yaml"
          name="config"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          rows={20}
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          placeholder={"version: 1\nname: my-job\nimage: sklearn\nentrypoint: train.py"}
          className="terminal-text mt-1.5 w-full resize-y rounded-md border border-border bg-background px-3 py-2 outline-none placeholder:text-muted-foreground focus:border-primary"
        />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button type="button" onClick={onCheck} disabled={disabled || empty}>
          {check.status === "checking" ? "Checking…" : "Check this config"}
        </Button>
        <Button type="button" variant="ghost" onClick={onGoToRepo}>
          Submit a repo <ArrowRight className="h-4 w-4" data-icon="inline-end" />
        </Button>
      </div>

      <p className="max-w-prose text-xs leading-relaxed text-muted-foreground">
        {TEMPLATE_SUBMIT_GAP}
      </p>

      <CheckResult check={check} />
    </div>
  );
}

/** The verdict, in the terms the route states it in.
 *
 * A clean config says so in one line and does not congratulate anybody: `ok`
 * means submit would not be refused FOR PREFLIGHT REASONS, and the sentence
 * says exactly that much. Datasets are resolved, the fleet is measured and
 * the spec compiled at submit time, and none of those were asked here. */
function CheckResult({ check }: { check: ConfigCheck }) {
  if (check.status === "idle" || check.status === "checking") return null;

  if (check.status === "failed") {
    return (
      <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2.5 text-sm text-destructive">
        <Warning className="mt-0.5 h-4 w-4 shrink-0" weight="fill" />
        <span>{check.detail}</span>
      </div>
    );
  }

  if (check.findings.length === 0) {
    return (
      <div className="flex items-start gap-2 rounded-lg border border-evergreen/40 bg-surface px-3 py-2.5 text-sm">
        <CheckCircle
          className="mt-0.5 h-4 w-4 shrink-0 text-evergreen"
          weight="fill"
        />
        <span className="text-muted-foreground">
          Preflight found nothing to fix in this config. It resolved the image
          and ran every check a submission is refused on; it did not resolve
          datasets or measure the fleet, which happen at submit time.
        </span>
      </div>
    );
  }

  return (
    <div className="space-y-2.5">
      <p className="text-sm text-muted-foreground">
        {check.ok
          ? `Preflight noted ${check.findings.length} warning${check.findings.length === 1 ? "" : "s"}. A warning is advice and does not block a submission.`
          : `Preflight found ${check.findings.length} problem${check.findings.length === 1 ? "" : "s"}. A submission carrying an error is refused, and nothing is queued.`}
      </p>
      <FindingsList findings={check.findings} />
    </div>
  );
}
