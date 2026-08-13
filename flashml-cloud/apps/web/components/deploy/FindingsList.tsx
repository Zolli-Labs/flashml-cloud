import { Warning, XCircle } from "@phosphor-icons/react";

import type { PreflightFinding } from "@/lib/cloud-api";

/**
 * Preflight's findings, all of them, in one list.
 *
 * Lifted out of `app/(console)/w/[poolId]/submit/page.tsx` unchanged when
 * the yaml editor grew a second caller for it. Both surfaces render the same
 * verdict from the same authority — `POST /v1alpha1/preflight` and
 * `POST /v1alpha1/jobs/from-repo` run `parse_flashml_yaml` → `resolve_image`
 * → `preflight` through the same function — so two renderings of it would be
 * two ways for the console to disagree with itself about the same YAML.
 *
 * Messages are quoted VERBATIM. The API's own text already names the
 * offending package and which curated image would provide it; paraphrasing
 * here would throw that specificity away.
 */
export function FindingsList({ findings }: { findings: PreflightFinding[] }) {
  return (
    <ul className="space-y-2.5">
      {findings.map((f, i) => (
        <li
          key={i}
          className={`flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-sm ${
            f.level === "error"
              ? "border-destructive/30 bg-destructive/10"
              : "border-warning/40 bg-warning/10"
          }`}
        >
          {f.level === "error" ? (
            <XCircle
              className="w-4 h-4 shrink-0 mt-0.5 text-destructive"
              weight="fill"
            />
          ) : (
            <Warning
              className="mt-0.5 h-4 w-4 shrink-0 text-warning-foreground"
              weight="fill"
            />
          )}
          <div className="min-w-0">
            <div
              className={`font-mono text-[10px] uppercase tracking-wide ${
                f.level === "error"
                  ? "text-destructive"
                  : "text-warning-foreground"
              }`}
            >
              {f.level} · {f.code}
            </div>
            <p className="text-foreground/90">{f.message}</p>
          </div>
        </li>
      ))}
    </ul>
  );
}
