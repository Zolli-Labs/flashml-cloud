/**
 * A machine's coarse PRESENTATIONAL kind — enough to pick one of four icons
 * for a fleet card, and nothing more.
 *
 * THIS IS DELIBERATELY NOT A CAPABILITY OR HARDWARE CLASS, and must never
 * grow into one. The API tier already carries TWO GPU classifiers that
 * disagree on purpose — `marketplace.py`'s `capability_class` (the SMALLEST
 * GPU on the machine, because placement can only promise what every card
 * has) and `router/estimator.py`'s `hardware_class` (the LARGEST, for
 * routing) — and a standing rule against adding a third. Anything that
 * needs a GPU MODEL, a VRAM tier, or a capability the console can act on
 * belongs to that server-side pair (once unified), not here. This function
 * answers exactly one question: "draw a laptop, a GPU rig, a server, or
 * nothing" — silent annotation on a card, not a spec a reader could rely on.
 *
 * READS ONLY FIELDS `GET /v1alpha1/machines` ALREADY RETURNS on `Machine`:
 *   - `platform`, a `platform.platform()`-style string the agent reports —
 *     `"macOS-26.5.1-arm64"`, `"Linux-6.8.0-90-generic-x86_64"` — already
 *     rendered as the console's own "Platform" column, so this reuses the
 *     same field rather than reaching into `capabilities` for an OS a
 *     better-typed column already carries.
 *   - `capabilities.gpus`, read defensively the same way
 *     `capability_class` in `marketplace.py` does: `capabilities` is the
 *     untyped jsonb snapshot the agent reported at registration and may
 *     predate any given field, so this checks shape rather than assuming it.
 *
 * PRECEDENCE: macOS is checked first and wins outright. A Mac with an
 * external or on-die GPU is still a laptop in a fleet's eye — "gpu" is
 * reserved for a machine whose whole identity in this list is its card(s),
 * which on this console today means a rented or home GPU rig, not a
 * MacBook.
 */
export type MachineKind = "laptop" | "gpu" | "server" | "unknown";

export function machineKind(machine: {
  platform: string | null;
  capabilities: Record<string, unknown> | null;
}): MachineKind {
  if (typeof machine.platform === "string" && /^macOS/i.test(machine.platform)) {
    return "laptop";
  }

  const gpus = machine.capabilities?.gpus;
  if (Array.isArray(gpus) && gpus.length > 0) {
    return "gpu";
  }

  if (typeof machine.platform === "string" && machine.platform.length > 0) {
    return "server";
  }

  return "unknown";
}
