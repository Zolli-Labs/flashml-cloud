"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Check } from "@phosphor-icons/react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  ApiError,
  NotAuthenticated,
  updateProfile,
  type Profile,
} from "@/lib/cloud-api";
import {
  ROLE_OPTIONS,
  TEAM_SIZE_OPTIONS,
  labelFor,
} from "@/lib/onboarding-options";
import {
  TEXT_FIELD_CAPS,
  changedDetails,
  detailsTextError,
  draftFromProfile,
  isDetailsEmpty,
  type DetailsDraft,
} from "@/lib/profile-details";

/**
 * The five self-reported fields onboarding asks for, editable after the fact.
 *
 * MOUNTED ONLY ON A LOADED PROFILE, and this one has teeth. `isDetailsEmpty`
 * answers `true` for a `null` profile — correctly, for its own purpose, so the
 * page never nags before data arrives. But the page used to render this
 * section with `profile === null` on a FAILED read too, and the copy that
 * choice selects is "You signed up before we asked for this", which is a
 * statement about the account made from a read that never returned one.
 * Taking `Profile` rather than `Profile | null` makes that unreachable.
 */
export function AccountDetailsForm({
  profile,
  onSaved,
}: {
  profile: Profile;
  onSaved: (updated: Profile) => void;
}) {
  const router = useRouter();

  const [details, setDetails] = useState<DetailsDraft>(() =>
    draftFromProfile(profile)
  );
  const [detailsSaving, setDetailsSaving] = useState(false);
  const [detailsSaved, setDetailsSaved] = useState(false);
  const [detailsError, setDetailsError] = useState<string | null>(null);

  const currentDetails = draftFromProfile(profile);
  const pendingDetails = changedDetails(details, currentDetails);
  const detailsDirty = Object.keys(pendingDetails).length > 0;
  const detailsEmpty = isDetailsEmpty(profile);

  // Validated only when a field is actually part of `pendingDetails` — i.e.
  // only when it is about to be sent. An untouched field that has simply
  // never been filled in (the grandfathered-tester case `detailsEmpty`
  // describes) must never block Save on its own: it isn't in the payload,
  // so it cannot 400. What must be blocked is CLEARING a field that WAS
  // set, or typing past its cap — both only matter, and only trigger the
  // API's rejection, once that field is dirty.
  const firstNameError =
    "first_name" in pendingDetails
      ? detailsTextError(details.first_name, "first_name")
      : null;
  const lastNameError =
    "last_name" in pendingDetails
      ? detailsTextError(details.last_name, "last_name")
      : null;
  const companyNameError =
    "company_name" in pendingDetails
      ? detailsTextError(details.company_name, "company_name")
      : null;
  const detailsValid = !firstNameError && !lastNameError && !companyNameError;
  const detailsCanSave = detailsDirty && detailsValid && !detailsSaving;

  function updateDetail<K extends keyof DetailsDraft>(
    key: K,
    value: DetailsDraft[K]
  ) {
    setDetails((d) => ({ ...d, [key]: value }));
    setDetailsSaved(false);
    setDetailsError(null);
  }

  async function saveDetails() {
    if (!detailsCanSave) return;
    setDetailsSaving(true);
    setDetailsError(null);
    setDetailsSaved(false);
    try {
      const updated = await updateProfile(pendingDetails);
      setDetails(draftFromProfile(updated));
      setDetailsSaved(true);
      onSaved(updated);
      toast.success("Details saved");
    } catch (err) {
      if (err instanceof NotAuthenticated) {
        router.push("/sign-in?next=/account");
        return;
      }
      const detail =
        err instanceof ApiError ? err.detail : "Couldn't save your details.";
      setDetailsError(detail);
      toast.error("Couldn't save your details", { description: detail });
    } finally {
      setDetailsSaving(false);
    }
  }

  return (
    <section className="panel mt-4 p-5">
      <h2 className="text-sm font-semibold">Your details</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        {detailsEmpty
          ? "You signed up before we asked for this. Filling it in helps us build the right thing."
          : "Used to understand who's on Zolli. Only you and the Zolli team see this."}
      </p>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="first-name" className="text-xs font-medium">
            First name
          </label>
          <input
            id="first-name"
            value={details.first_name}
            onChange={(e) => updateDetail("first_name", e.target.value)}
            aria-invalid={!!firstNameError || undefined}
            aria-describedby="first-name-help"
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground/60"
          />
          <DetailsFieldHelp
            id="first-name-help"
            dirty={"first_name" in pendingDetails}
            error={firstNameError}
            length={details.first_name.trim().length}
            cap={TEXT_FIELD_CAPS.first_name}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label htmlFor="last-name" className="text-xs font-medium">
            Last name
          </label>
          <input
            id="last-name"
            value={details.last_name}
            onChange={(e) => updateDetail("last_name", e.target.value)}
            aria-invalid={!!lastNameError || undefined}
            aria-describedby="last-name-help"
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground/60"
          />
          <DetailsFieldHelp
            id="last-name-help"
            dirty={"last_name" in pendingDetails}
            error={lastNameError}
            length={details.last_name.trim().length}
            cap={TEXT_FIELD_CAPS.last_name}
          />
        </div>
      </div>

      <div className="mt-4 flex flex-col gap-1.5">
        <label htmlFor="company-name" className="text-xs font-medium">
          Company, lab, or university
        </label>
        <input
          id="company-name"
          value={details.company_name}
          onChange={(e) => updateDetail("company_name", e.target.value)}
          aria-invalid={!!companyNameError || undefined}
          aria-describedby="company-name-help"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground/60"
        />
        <DetailsFieldHelp
          id="company-name-help"
          dirty={"company_name" in pendingDetails}
          error={companyNameError}
          length={details.company_name.trim().length}
          cap={TEXT_FIELD_CAPS.company_name}
        />
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="role" className="text-xs font-medium">
            Role
          </label>
          {/* `details.role`, never `details.role || undefined`: Base UI's
              `useControlled` decides controlled-vs-uncontrolled ONCE, on
              first render, by checking `value !== undefined` — and never
              re-checks it. This section prefills from an existing profile
              (a non-empty value can land on first render), so passing
              `undefined` here would risk locking the Select uncontrolled
              for its whole lifetime and silently ignoring that prefill.
              Same pattern as `OnboardingForm.tsx`. */}
          <Select
            value={details.role}
            onValueChange={(value) => updateDetail("role", value ?? "")}
            disabled={detailsSaving}
          >
            <SelectTrigger id="role" className="w-full">
              <SelectValue placeholder="Choose one">
                {(v) => labelFor(ROLE_OPTIONS, v) ?? "Choose one"}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {ROLE_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="team-size" className="text-xs font-medium">
            Team size
          </label>
          {/* Same reasoning as the role Select above. */}
          <Select
            value={details.team_size}
            onValueChange={(value) => updateDetail("team_size", value ?? "")}
            disabled={detailsSaving}
          >
            <SelectTrigger id="team-size" className="w-full">
              <SelectValue placeholder="Choose one">
                {(v) => labelFor(TEAM_SIZE_OPTIONS, v) ?? "Choose one"}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {TEAM_SIZE_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value}>
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Button onClick={saveDetails} disabled={!detailsCanSave}>
          {detailsSaving ? "Saving…" : "Save"}
        </Button>
        {detailsError ? (
          <span className="text-xs text-destructive">{detailsError}</span>
        ) : detailsSaved ? (
          <span className="inline-flex items-center gap-1 text-xs text-[var(--node-green)]">
            <Check size={12} weight="bold" /> Saved
          </span>
        ) : null}
      </div>
    </section>
  );
}

/** Inline validity feedback for one of this section's three text fields
 * — same presentation as the display-name field's helper text
 * (`#display-name-help`), plus a destructive "Can't be blank." this section
 * needs and that field doesn't: display name can never be dirty-and-blank
 * (its own `canSave` silently disables on length 0), but here clearing a
 * previously-set field is an ordinary, expected action that must be
 * refused with a reason, not just a disabled button. Renders nothing at
 * all when `!dirty` — an untouched field that has simply never been filled
 * in (a grandfathered tester's default state) must not be nagged. */
function DetailsFieldHelp({
  id,
  dirty,
  error,
  length,
  cap,
}: {
  id: string;
  dirty: boolean;
  error: string | null;
  length: number;
  cap: number;
}) {
  if (!dirty) return null;
  return (
    <p id={id} className="text-xs">
      {error ? (
        <span className="text-destructive">{error}</span>
      ) : (
        <span className="text-muted-foreground">
          {length}/{cap} characters.
        </span>
      )}
    </p>
  );
}
