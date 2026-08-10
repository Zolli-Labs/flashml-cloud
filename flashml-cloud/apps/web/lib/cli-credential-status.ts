import type { CliCredential } from "./cloud-api";

/** What to call a credential in a list. The label is whatever the CLI
 * reported about the machine it ran on, and it is optional all the way
 * down — a caller can start a device code with no label at all — so this
 * degrades twice rather than rendering a blank row that looks like a bug. */
export function credentialLabel(c: CliCredential): string {
  if (c.label && c.label.trim()) return c.label;
  if (c.token_prefix) return `${c.token_prefix}…`;
  return "unnamed credential";
}

export function credentialBadge(c: CliCredential): {
  label: string;
  tone: "active" | "revoked";
} {
  return c.status === "revoked"
    ? { label: "Revoked", tone: "revoked" }
    : { label: "Active", tone: "active" };
}
