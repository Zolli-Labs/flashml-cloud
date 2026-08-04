"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { ApiError, NotFound, renamePool } from "@/lib/cloud-api";

export function RenameWorkspace({
  poolId,
  currentName,
  onRenamed,
}: {
  poolId: string;
  currentName: string;
  onRenamed: () => void;
}) {
  const [name, setName] = useState(currentName);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The last name this component itself successfully saved. `currentName`
  // (the prop) only catches up once the parent's `reload()` round-trips and
  // re-renders with a fresh `pool.name` — comparing against it directly left
  // a window, between `renamePool` resolving and that reload landing, where
  // `name` already held the new value but `currentName` didn't, so
  // `unchanged` read false and Save re-enabled for an already-saved name.
  // Comparing against this instead is settled the instant the save itself
  // succeeds, with no round trip to wait on.
  const [savedName, setSavedName] = useState(currentName);

  const trimmed = name.trim();
  const unchanged = trimmed === savedName;

  async function save() {
    if (!trimmed || unchanged) return;
    setSaving(true);
    setError(null);
    try {
      // The API trims and caps at 200 characters, so its response — not the
      // string we sent — is what the name actually became.
      const updated = await renamePool(poolId, trimmed);
      setName(updated.name);
      setSavedName(updated.name);
      onRenamed();
      toast.success("Workspace renamed", { description: updated.name });
    } catch (err) {
      if (err instanceof NotFound) {
        // Owner-only, and the API answers 404 for "not the owner" exactly as
        // it does for "no such pool" — so this cannot be reported as a
        // permissions problem without guessing which one it was.
        setError("This workspace can't be renamed from here.");
      } else {
        setError(
          err instanceof ApiError ? err.detail : "Couldn't rename it. Try again."
        );
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <section>
      <h2 className="text-sm font-semibold">Name</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          save();
        }}
        className="mt-3 flex flex-wrap items-start gap-2"
      >
        <div className="min-w-0 flex-1">
          <Input
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setError(null);
            }}
            aria-label="Workspace name"
            disabled={saving}
            maxLength={200}
          />
          {error && <p className="mt-1.5 text-xs text-destructive">{error}</p>}
        </div>
        <button
          type="submit"
          disabled={saving || !trimmed || unchanged}
          className="interactive rounded-md bg-primary px-3.5 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {saving ? "Saving…" : "Save"}
        </button>
      </form>
    </section>
  );
}
