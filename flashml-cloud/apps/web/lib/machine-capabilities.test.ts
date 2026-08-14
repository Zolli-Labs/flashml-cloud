import { describe, expect, it } from "vitest";
import { readMachineCapabilities } from "./machine-capabilities";

describe("readMachineCapabilities", () => {
  it("reads every allowlisted field when all are present", () => {
    const result = readMachineCapabilities({
      os: "macOS-26.5.1-arm64",
      architecture: "arm64",
      cpu_cores: 10,
      memory_bytes: 34_359_738_368,
      gpus: [{ name: "Apple M3", memory_total_mb: 0 }],
    });
    expect(result).toEqual({
      os: "macOS-26.5.1-arm64",
      architecture: "arm64",
      cpuCores: 10,
      memoryBytes: 34_359_738_368,
      gpus: [{ name: "Apple M3", memoryTotalMb: 0 }],
    });
  });

  it("reads null capabilities as every field absent", () => {
    expect(readMachineCapabilities(null)).toEqual({
      os: null,
      architecture: null,
      cpuCores: null,
      memoryBytes: null,
      gpus: [],
    });
  });

  it("reads an empty object the same as null", () => {
    expect(readMachineCapabilities({})).toEqual({
      os: null,
      architecture: null,
      cpuCores: null,
      memoryBytes: null,
      gpus: [],
    });
  });

  it("refuses a boolean where a number is expected, matching the API's own rule", () => {
    const result = readMachineCapabilities({ cpu_cores: true });
    expect(result.cpuCores).toBeNull();
  });

  it("refuses a non-finite number", () => {
    const result = readMachineCapabilities({ memory_bytes: Number.POSITIVE_INFINITY });
    expect(result.memoryBytes).toBeNull();
  });

  it("refuses an empty string, treating it as not reported", () => {
    const result = readMachineCapabilities({ os: "" });
    expect(result.os).toBeNull();
  });

  it("drops a malformed (non-array) gpus field to an empty list rather than throwing", () => {
    const result = readMachineCapabilities({ gpus: "not-an-array" });
    expect(result.gpus).toEqual([]);
  });

  it("drops non-object entries inside gpus and reads the rest", () => {
    const result = readMachineCapabilities({
      gpus: [null, "junk", { name: "RTX 4090", memory_total_mb: 24576 }],
    });
    expect(result.gpus).toEqual([{ name: "RTX 4090", memoryTotalMb: 24576 }]);
  });

  it("reads a gpu device missing memory_total_mb as memoryTotalMb: null, not 0", () => {
    const result = readMachineCapabilities({ gpus: [{ name: "RTX 4090" }] });
    expect(result.gpus).toEqual([{ name: "RTX 4090", memoryTotalMb: null }]);
  });
});
