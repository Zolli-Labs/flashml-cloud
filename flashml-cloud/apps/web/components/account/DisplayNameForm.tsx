"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Check } from "@phosphor-icons/react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  ApiError,
  NotAuthenticated,
  updateMe,
  type Profile,
} from "@/lib/cloud-api";

/**
 * The one editable field that is ours rather than the identity provider's.
 *
 * MOUNTED ONLY ON A LOADED PROFILE. This takes `profile: Profile`, not
 * `Profile | null`, so it renders inside its page's `present` branch and
 * nowhere else. That is not tidiness: before the split, this form rendered
 * with an empty input while `GET /me` was in flight AND after it had failed,
 * which meant an editable, apparently-blank display name on an account whose
 * real name we had simply not managed to read. Typing into it and pressing
 * Save would then PATCH over a value nobody had seen.
 */
export function DisplayNameForm({
  profile,
  providerName,
  onSaved,
}: {
  profile: Profile;
  /** Placeholder only — the identity provider's idea of the name. */
  providerName: string | null | undefined;
  onSaved: (updated: Profile) => void;
}) {
  const router = useRouter();
  const current = profile.display_name ?? "";

  const [name, setName] = useState(current);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const trimmed = name.trim();
  const dirty = trimmed !== current;
  const tooLong = trimmed.length > 80;
  const canSave = dirty && trimmed.length > 0 && !tooLong && !saving;

  async function save() {
    if (!canSave) return;
    setSaving(true);
    setSaveError(null);
    setSaved(false);
    try {
      const updated = await updateMe(trimmed);
      setName(updated.display_name ?? "");
      setSaved(true);
      onSaved(updated);
      toast.success("Display name saved");
    } catch (err) {
      if (err instanceof NotAuthenticated) {
        router.push("/sign-in?next=/account");
        return;
      }
      const detail =
        err instanceof ApiError ? err.detail : "Couldn't save your name.";
      setSaveError(detail);
      // Both: the toast is noticed, the inline message persists next to the
      // field the user has to fix.
      toast.error("Couldn't save your name", { description: detail });
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="panel mt-4 p-5">
      <label htmlFor="display-name" className="text-sm font-medium">
        Display name
      </label>
      <p className="mt-1 text-xs text-muted-foreground">
        How you appear in Zolli. Everything else on this page comes from the
        account you signed in with.
      </p>
      <div className="mt-3 flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <input
            id="display-name"
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setSaved(false);
              setSaveError(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") save();
            }}
            placeholder={providerName ?? "Your name"}
            aria-invalid={tooLong || undefined}
            aria-describedby="display-name-help"
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground/60"
          />
          <p id="display-name-help" className="mt-1.5 text-xs">
            {tooLong ? (
              <span className="text-destructive">
                {trimmed.length}/80 characters. Too long.
              </span>
            ) : saveError ? (
              <span className="text-destructive">{saveError}</span>
            ) : saved ? (
              <span className="inline-flex items-center gap-1 text-[var(--node-green)]">
                <Check size={12} weight="bold" /> Saved
              </span>
            ) : (
              <span className="text-muted-foreground">
                {trimmed.length}/80 characters.
              </span>
            )}
          </p>
        </div>
        <Button onClick={save} disabled={!canSave}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
    </section>
  );
}
