// Pure logic behind the account page's "Your details" section — kept out of
// the component so it can be unit tested in the vitest "node" environment
// (no component-rendering harness exists here; see the test file).

import type { Profile } from "@/lib/cloud-api";

/** The subset of `Profile` this section edits. Every field is a plain
 * string in the draft even though `Profile` types each one `string |
 * null` — a controlled `<input>`/`<Select>` needs a real string from
 * mount, never `null`/`undefined`. See the Select comment in
 * `OnboardingForm.tsx`: Base UI's `useControlled` decides
 * controlled-vs-uncontrolled once, on first render, by checking `value
 * !== undefined`, so handing it `null`/`undefined` on an empty prefill
 * would lock it uncontrolled for good. */
export interface DetailsDraft {
  first_name: string;
  last_name: string;
  company_name: string;
  role: string;
  team_size: string;
}

const DETAIL_KEYS = [
  "first_name",
  "last_name",
  "company_name",
  "role",
  "team_size",
] as const;

/** Seeds a draft from a loaded (or not-yet-loaded) profile. `null` fields
 * become `""`, never `null` — see `DetailsDraft`'s doc comment for why
 * that distinction matters here specifically. */
export function draftFromProfile(profile: Profile | null): DetailsDraft {
  return {
    first_name: profile?.first_name ?? "",
    last_name: profile?.last_name ?? "",
    company_name: profile?.company_name ?? "",
    role: profile?.role ?? "",
    team_size: profile?.team_size ?? "",
  };
}

/** True when every one of the five fields this section edits is unset —
 * the grandfathered-tester case: accounts created before onboarding asked
 * for these are never prompted for them anywhere else, so this section is
 * the only path that ever fills them in. A `null` profile (still loading)
 * counts as empty too, so the page never briefly nags before data
 * arrives. */
export function isDetailsEmpty(profile: Profile | null): boolean {
  if (!profile) return true;
  return DETAIL_KEYS.every((key) => profile[key] === null);
}

/** Only the fields that actually changed, trimmed, ready to hand to
 * `updateProfile` — never the whole draft, so an untouched field is never
 * re-sent and can never clobber a value someone else set from a different
 * tab or device in between. */
export function changedDetails(
  draft: DetailsDraft,
  current: DetailsDraft
): Partial<DetailsDraft> {
  const out: Partial<DetailsDraft> = {};
  for (const key of DETAIL_KEYS) {
    const value = draft[key].trim();
    if (value !== current[key]) out[key] = value;
  }
  return out;
}
