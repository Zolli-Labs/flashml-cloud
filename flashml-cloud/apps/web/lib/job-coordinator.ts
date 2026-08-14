import type { JobRecord } from "./cloud-api";

/** Which control plane a submitter can ask to coordinate a job, and what
 * every job that already ran actually used.
 *
 * TWO VALUES, MATCHING THE API'S `COORDINATOR_VENUES` EXACTLY —
 * `"render"` (the incumbent Render private service) and `"fc"` (Alibaba
 * Function Compute, Singapore). This is not the same axis as `venue` in
 * `lib/job-routing.ts`: that picks WHERE a job's tasks execute (a machine,
 * RunPod, an Alibaba FC sandbox); this picks WHICH deployment of the
 * coordinator service tracked the job at all. A job can be routed to any
 * venue regardless of which control plane is coordinating it.
 *
 * Widened to `string` everywhere a job record is read from, for the same
 * reason `ArtifactStorage` is: a third control plane added API-side must
 * not turn into a console build break, and an unrecognised value is named
 * verbatim rather than guessed at. */
export type CoordinatorVenue = "render" | "fc";

/** What every submitter gets by leaving the picker alone, and what an
 * absent field on a job record means. Mirrors the API's
 * `DEFAULT_COORDINATOR_VENUE` — this app must not invent a different
 * default from the one that actually governs an omitted field. */
export const DEFAULT_COORDINATOR: CoordinatorVenue = "render";

/** The submit-form picker's options, in the order they render. Labelled for
 * a person choosing where their job runs, not for the enum a machine reads
 * — "venue enum" tells a submitter nothing about what they're picking
 * between. */
export const COORDINATOR_PICKER_OPTIONS: {
  value: CoordinatorVenue;
  label: string;
}[] = [
  { value: "render", label: "Render (private service)" },
  { value: "fc", label: "Function Compute (Singapore)" },
];

/** Short label for the chip on a job row or the job detail header — this is
 * a scanning surface, not a sentence, so it stays to one or two words.
 *
 * Absent and `"render"` read identically on purpose: `JobRecord.coordinator`
 * is optional because the API and web deploy separately, and treating a
 * not-yet-populated field as anything other than the documented default
 * would show a chip the API never actually claimed. An unrecognised value
 * (a third control plane this build predates) is printed verbatim rather
 * than mapped to either known label, matching `ArtifactStorage`'s rule for
 * the same situation. */
export function coordinatorChipLabel(
  coordinator: string | null | undefined
): string {
  const value = coordinator ?? DEFAULT_COORDINATOR;
  if (value === "render") return "Render";
  if (value === "fc") return "Function Compute";
  return value;
}

/** The same fallback, off a whole job record — the one call site every
 * chip actually uses, so "absent means Render" is written once. */
export function jobCoordinatorLabel(job: Pick<JobRecord, "coordinator">): string {
  return coordinatorChipLabel(job.coordinator);
}
