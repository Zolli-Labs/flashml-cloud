import { describe, expect, it } from "vitest";
import {
  COORDINATOR_PICKER_OPTIONS,
  DEFAULT_COORDINATOR,
  coordinatorChipLabel,
  jobCoordinatorLabel,
} from "./job-coordinator";

describe("coordinatorChipLabel", () => {
  it("labels the known values for a person, not the wire value", () => {
    expect(coordinatorChipLabel("render")).toBe("Render");
    expect(coordinatorChipLabel("fc")).toBe("Function Compute");
  });

  it("treats an absent field as the documented default, not an unknown state", () => {
    expect(coordinatorChipLabel(undefined)).toBe("Render");
    expect(coordinatorChipLabel(null)).toBe("Render");
    expect(coordinatorChipLabel(undefined)).toBe(
      coordinatorChipLabel(DEFAULT_COORDINATOR)
    );
  });

  it("prints an unrecognised value verbatim rather than guessing", () => {
    expect(coordinatorChipLabel("gcp")).toBe("gcp");
  });
});

describe("jobCoordinatorLabel", () => {
  it("reads the same fallback off a job record", () => {
    expect(jobCoordinatorLabel({ coordinator: "fc" })).toBe(
      "Function Compute"
    );
    expect(jobCoordinatorLabel({})).toBe("Render");
  });
});

describe("COORDINATOR_PICKER_OPTIONS", () => {
  it("offers exactly the two contract values, Render first", () => {
    expect(COORDINATOR_PICKER_OPTIONS.map((o) => o.value)).toEqual([
      "render",
      "fc",
    ]);
  });

  it("labels each option for a person choosing where their job runs", () => {
    expect(COORDINATOR_PICKER_OPTIONS[0].label).toBe(
      "Render (private service)"
    );
    expect(COORDINATOR_PICKER_OPTIONS[1].label).toBe(
      "Function Compute (Singapore)"
    );
  });
});
