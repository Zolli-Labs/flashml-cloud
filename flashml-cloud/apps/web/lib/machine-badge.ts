/**
 * A machine's practical trust tier, derived from the three capability
 * booleans `GET /v1alpha1/machines` reports on each `Machine`
 * (`sandbox_capable`, `argv_capable`, `unsandboxed_argv_capable`) — never
 * from `capabilities`, the free-form snapshot, which is display-only and
 * not a contract this predicate can rely on.
 *
 * - "sandboxed": the normal case — the agent runs work inside its Docker
 *   sandbox, whether or not that sandbox also accepts argv
 *   (`sandbox_capable` and/or `argv_capable`).
 * - "trusted": the agent can accept argv ONLY outside a sandbox
 *   (`unsandboxed_argv_capable` alone) — code then runs directly on the
 *   host, not inside a container.
 * - "modules-only": none of the above. The agent can still run the fixed
 *   built-in modules a job may target; it just cannot be handed arbitrary
 *   argv, sandboxed or not.
 *
 * Precedence is deliberate, not incidental: an agent that claims BOTH
 * `argv_capable`/`sandbox_capable` (sandboxed) and `unsandboxed_argv_capable`
 * (trusted) at once reads as sandboxed. A Docker-capable host never has to
 * fall back to running work trusted — the safer path wins whenever it is
 * available at all, so this is checked first rather than left to whichever
 * flag happens to be read last.
 */
export type MachineBadge = "sandboxed" | "trusted" | "modules-only";

export function machineBadge(m: {
  sandbox_capable: boolean;
  argv_capable: boolean;
  unsandboxed_argv_capable: boolean;
}): MachineBadge {
  if (m.sandbox_capable || m.argv_capable) return "sandboxed";
  if (m.unsandboxed_argv_capable) return "trusted";
  return "modules-only";
}
