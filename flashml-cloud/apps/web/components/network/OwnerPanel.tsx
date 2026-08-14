"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { NotAuthenticated } from "@/lib/cloud-api";
import { formatZc } from "@/lib/market-credits";
import { setMachineLocation, type ProviderDetail } from "@/lib/network/api";

/**
 * What only the owner of a machine may see, and the one thing only they may
 * change.
 *
 * WHY THIS IS ITS OWN COMPONENT rather than three `own &&` branches spread
 * through the page: the whole block is gated on one condition, and a gate
 * written once cannot be half-applied. Everything in here is also re-checked
 * server-side — the API omits `owner` entirely for a visitor and refuses the
 * PATCH — so this gate is politeness, not security, and a client that lied to
 * itself about `own` would earn a 403 and an empty object.
 *
 * THE EXACT NAME LIVES HERE AND NOT IN THE HEADING. `label` is the public,
 * truncated name every visitor sees; `owner.name` is what the host actually
 * called the machine and `owner.node_id` is what the agent enrolled as. Those
 * two are the strings a host needs when they are about to run a command
 * against this box, and they are exactly the two a stranger has no business
 * reading — which is why the API sends them in a separate object rather than
 * on the provider.
 */
export function OwnerPanel({
  provider,
  onLocationSaved,
}: {
  provider: ProviderDetail;
  /** The API answers a PATCH with the updated provider. Handing it back up
   * rather than re-reading the page keeps the map, the header and this form
   * on one answer instead of three reads that may disagree. */
  onLocationSaved: (patched: {
    country: string;
    region?: string;
    city?: string;
  }) => void;
}) {
  const owner = provider.owner;
  if (!owner) return null;

  return (
    <section className="grid items-start gap-6 lg:grid-cols-2">
      <div className="panel px-4 py-3.5">
        <h2 className="label-caps">Your machine</h2>
        <dl className="mt-1 divide-y divide-border">
          <Row label="Machine name" value={owner.name} mono />
          <Row label="Node id" value={owner.node_id} mono />
          {/* MILLIcredits, formatted by `lib/market-credits.ts`'s `formatZc`
              — the one place in this codebase that turns a credit integer
              into a ZC string. Dividing by 1000 here would be a second
              answer to a question that already has one, and the two would
              round differently the first time a balance was not a whole
              credit. */}
          <Row
            label="Credits earned"
            value={
              owner.credits_earned === null
                ? null
                : `${formatZc(owner.credits_earned)} ZC`
            }
            mono
          />
          {/* The credits row is the one place a null is worth a sentence
              rather than a hidden row: a host looking for their earnings and
              finding no line at all cannot tell the page from the ledger. */}
          {owner.credits_earned === null && (
            <div className="py-2">
              <dt className="label-caps">Credits earned</dt>
              <dd className="mt-0.5 text-sm text-muted-foreground">
                Not measured yet — which is not the same as zero.
              </dd>
            </div>
          )}
        </dl>
      </div>

      <LocationForm provider={provider} onSaved={onLocationSaved} />
    </section>
  );
}

/**
 * Set where this machine is.
 *
 * COUNTRY IS THE ONLY REQUIRED FIELD, because it is the only one the API
 * requires and because it is the one that puts a dot on the map. Region and
 * city are omitted from the request when blank rather than sent as `""` —
 * `setMachineLocation` builds the body key by key for exactly that reason.
 *
 * THE FORM DOES NOT PRETEND TO GEOCODE. It sends what was typed. The dot's
 * coordinates are the API's to resolve; if it cannot, the machine keeps its
 * place name and stays off the map, and the page says so rather than dropping
 * a pin at the country's centroid and calling it a location.
 */
function LocationForm({
  provider,
  onSaved,
}: {
  provider: ProviderDetail;
  onSaved: (patched: { country: string; region?: string; city?: string }) => void;
}) {
  const [country, setCountry] = useState(provider.location.country ?? "");
  const [region, setRegion] = useState(provider.location.region ?? "");
  const [city, setCity] = useState(provider.location.city ?? "");
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  const [error, setError] = useState<string | null>(null);

  const trimmed = country.trim();

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!trimmed) return;
    setState("saving");
    setError(null);
    setMachineLocation(provider.id, {
      country: trimmed,
      region: region.trim() || undefined,
      city: city.trim() || undefined,
    })
      .then(() => {
        setState("saved");
        onSaved({
          country: trimmed,
          region: region.trim() || undefined,
          city: city.trim() || undefined,
        });
      })
      .catch((err) => {
        setState("idle");
        setError(
          err instanceof NotAuthenticated
            ? "Your session expired. Sign in again and retry."
            : err instanceof Error
              ? err.message
              : "Couldn't save."
        );
      });
  };

  return (
    <form onSubmit={submit} className="panel px-4 py-3.5">
      <h2 className="label-caps">Set location</h2>
      <p className="mt-1.5 max-w-prose text-sm text-muted-foreground">
        Where this machine physically runs. It places the dot on the network
        map and nothing else — it is not used to route work.
      </p>

      <div className="mt-3 grid gap-3 sm:grid-cols-[6rem_minmax(0,1fr)]">
        <Field
          label="Country"
          value={country}
          onChange={setCountry}
          placeholder="DE"
          required
          maxLength={2}
          mono
          autoCapitalize="characters"
        />
        <Field
          label="Region"
          value={region}
          onChange={setRegion}
          placeholder="Hessen"
        />
        <div className="sm:col-span-2">
          <Field
            label="City"
            value={city}
            onChange={setCity}
            placeholder="Frankfurt"
          />
        </div>
      </div>

      <div className="mt-3.5 flex flex-wrap items-center gap-3">
        <Button type="submit" size="sm" disabled={!trimmed || state === "saving"}>
          {state === "saving" ? "Saving…" : "Save location"}
        </Button>
        {state === "saved" && !error && (
          <span className="text-xs text-evergreen">Saved.</span>
        )}
        {error && <span className="text-xs text-destructive">{error}</span>}
      </div>
    </form>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  required = false,
  maxLength,
  mono = false,
  autoCapitalize,
}: {
  label: string;
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  required?: boolean;
  maxLength?: number;
  mono?: boolean;
  autoCapitalize?: "none" | "characters";
}) {
  return (
    <label className="block min-w-0">
      <span className="label-caps">{label}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        required={required}
        maxLength={maxLength}
        autoCapitalize={autoCapitalize}
        className={`mt-1 h-9 w-full rounded-[7px] border border-input bg-surface px-2.5 text-sm transition-colors outline-none placeholder:text-muted-foreground focus-visible:border-ring ${
          mono ? "font-mono uppercase" : ""
        }`}
      />
    </label>
  );
}

function Row({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string | null;
  mono?: boolean;
}) {
  if (value === null) return null;
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-0.5 py-2">
      <dt className="label-caps">{label}</dt>
      <dd
        className={`min-w-0 truncate text-right text-sm ${mono ? "font-mono" : ""}`}
      >
        {value}
      </dd>
    </div>
  );
}
