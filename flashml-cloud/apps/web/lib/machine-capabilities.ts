/**
 * The allowlisted hardware snapshot inside `Machine.capabilities`, read
 * defensively the same way the API itself reads it back
 * (`_REPORTED_CAPABILITY_FIELDS` / `_reported_capabilities` in
 * `apps/api/flashml_cloud_api/db.py`): `cpu_cores`, `memory_bytes`, `gpus`
 * (a list of `{ name, memory_total_mb }` devices), `os`, `architecture`.
 * Nothing outside that allowlist is read here — `capabilities` is an
 * untyped jsonb snapshot the agent reported at registration and may predate
 * any given field, or (before the API's own allowlist existed) may carry
 * keys that were never validated at all.
 *
 * Every field comes back `null` when absent or the wrong shape, never a
 * default — §1.1: a machine that did not report a field must render as
 * "not observed" (an em-dash at the call site), never as a plausible zero.
 */
export interface MachineHardware {
  os: string | null;
  architecture: string | null;
  cpuCores: number | null;
  memoryBytes: number | null;
  gpus: MachineGpu[];
}

export interface MachineGpu {
  name: string | null;
  memoryTotalMb: number | null;
}

function readNumber(value: unknown): number | null {
  // `bool` is refused where a number is expected, matching
  // `_reported_capabilities`'s own reasoning: `true` would read as `1`, a
  // plausible measurement that was never one.
  if (typeof value === "boolean") return null;
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function readString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function readGpus(value: unknown): MachineGpu[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((device): device is Record<string, unknown> => {
      return typeof device === "object" && device !== null;
    })
    .map((device) => ({
      name: readString(device.name),
      memoryTotalMb: readNumber(device.memory_total_mb),
    }));
}

export function readMachineCapabilities(
  capabilities: Record<string, unknown> | null
): MachineHardware {
  const source = capabilities ?? {};
  return {
    os: readString(source.os),
    architecture: readString(source.architecture),
    cpuCores: readNumber(source.cpu_cores),
    memoryBytes: readNumber(source.memory_bytes),
    gpus: readGpus(source.gpus),
  };
}
