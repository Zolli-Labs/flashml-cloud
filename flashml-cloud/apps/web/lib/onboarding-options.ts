/** Option values are a contract with the API's enumerations in
 * `flashml_cloud_api/access.py`. A label is cosmetic; a VALUE typo is a
 * 400 the user has no way to fix, which is why they are pinned by test. */

export interface Option {
  value: string;
  label: string;
}

export const ROLE_OPTIONS: Option[] = [
  { value: "researcher", label: "Researcher" },
  { value: "ml_engineer", label: "ML engineer" },
  { value: "student", label: "Student" },
  { value: "founder", label: "Founder" },
  { value: "other", label: "Something else" },
];

export const TEAM_SIZE_OPTIONS: Option[] = [
  { value: "solo", label: "Just me" },
  { value: "2_5", label: "2–5 people" },
  { value: "6_20", label: "6–20 people" },
  { value: "20_plus", label: "More than 20" },
];

export const COMPUTE_OPTIONS: Option[] = [
  { value: "own_machines", label: "My own machines" },
  { value: "colab", label: "Google Colab" },
  { value: "runpod", label: "RunPod" },
  { value: "cloud", label: "Cloud (AWS, GCP, Azure)" },
  { value: "none", label: "Nothing yet" },
];

export const HEARD_FROM_OPTIONS: Option[] = [
  { value: "github", label: "GitHub" },
  { value: "search", label: "Search" },
  { value: "twitter", label: "X / Twitter" },
  { value: "friend", label: "From someone I know" },
  { value: "paper", label: "A paper or article" },
  { value: "event", label: "An event" },
  { value: "other", label: "Somewhere else" },
];

/** Resolve a stored value to the label a person should read.
 *
 * Base UI's `Select.Value` renders the raw VALUE when it has no way to look
 * up a label, so a trigger showed "ml_engineer" and "2_5" while the open list
 * beside it showed "ML engineer" and "2–5 people". Pass this as the Value's
 * child at every call site.
 *
 * Returns `null` for an unset value so the caller can fall back to its
 * placeholder — Base UI's own `placeholder` prop is overridden as soon as a
 * child is supplied. */
export function labelFor(
  options: readonly Option[],
  value: string | null | undefined
): string | null {
  if (!value) return null;
  return options.find((o) => o.value === value)?.label ?? value;
}

export interface OnboardingDraft {
  first_name: string;
  last_name: string;
  company_name: string;
  role: string;
  team_size: string;
  use_case: string;
  compute_sources: string[];
  heard_from: string;
  linkedin_url: string;
}

export const EMPTY_DRAFT: OnboardingDraft = {
  first_name: "",
  last_name: "",
  company_name: "",
  role: "",
  team_size: "",
  use_case: "",
  compute_sources: [],
  heard_from: "",
  linkedin_url: "",
};

/** Mirrors the API's own rules: five required text fields, two required
 * choices, and `compute_sources` / `heard_from` genuinely optional.
 * Client-side only — the API validates independently and is the authority. */
export function isComplete(draft: OnboardingDraft): boolean {
  const filled = (v: string) => v.trim().length > 0;
  return (
    filled(draft.first_name) &&
    filled(draft.last_name) &&
    filled(draft.company_name) &&
    filled(draft.use_case) &&
    filled(draft.linkedin_url) &&
    draft.role !== "" &&
    draft.team_size !== ""
  );
}
