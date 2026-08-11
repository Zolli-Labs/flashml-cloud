import { readFileSync, readdirSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  createHeroRuntimeState,
  getHeroJobStep,
  getHeroSource,
  getNextHeroJobStep,
  HERO_JOB_STEPS,
  HERO_SOURCES,
  reduceHeroRuntime,
} from "./hero-story";

const root = process.cwd();
const source = (path: string) => readFileSync(`${root}/${path}`, "utf8");
const productionFabricSourcePaths = [
  "lib/hero-story.ts",
  "lib/hero-fabric.ts",
  ...readdirSync(`${root}/components/landing/hero-fabric`)
    .filter((file) => file.endsWith(".tsx") && !file.includes(".test."))
    .map((file) => `components/landing/hero-fabric/${file}`),
];

describe("production hero story contract", () => {
  it("keeps the four compute sources in product order", () => {
    expect(HERO_SOURCES.map((item) => item.key)).toEqual([
      "cloud",
      "rented",
      "owned",
      "everyday",
    ]);
    expect(getHeroSource("everyday").label).toBe("Everyday Machines");
  });

  it("keeps the six-step checkpoint recovery story", () => {
    expect(HERO_JOB_STEPS.map((item) => item.key)).toEqual([
      "submitted",
      "assigned",
      "checkpointed",
      "lost",
      "resumed",
      "accepted",
    ]);
    expect(getHeroJobStep("lost").label).toBe("Node lost");
    expect(getNextHeroJobStep("accepted")).toBe("submitted");
  });

  it("pauses playback when a source or explicit job step is selected", () => {
    let runtime = reduceHeroRuntime(createHeroRuntimeState(), { type: "initialize-motion" });
    runtime = reduceHeroRuntime(runtime, { type: "select-source", source: "owned" });
    expect(runtime).toMatchObject({ selectedSource: "owned", storyPlaying: false });

    runtime = reduceHeroRuntime(runtime, { type: "initialize-motion" });
    runtime = reduceHeroRuntime(runtime, { type: "select-job-step", jobStep: "lost" });
    expect(runtime).toMatchObject({ jobStep: "lost", storyPlaying: false });
  });

  it("plays without changing the selected step and treats reduced motion as a no-op", () => {
    let runtime = reduceHeroRuntime(createHeroRuntimeState(), {
      type: "select-job-step",
      jobStep: "lost",
    });
    runtime = reduceHeroRuntime(runtime, { type: "toggle-story", reducedMotion: false });
    expect(runtime).toMatchObject({ jobStep: "lost", storyPlaying: true });

    expect(reduceHeroRuntime(runtime, { type: "toggle-story", reducedMotion: true })).toBe(runtime);
  });

  it("latches failure and entrance completion once", () => {
    let runtime = reduceHeroRuntime(createHeroRuntimeState(), { type: "fabric-failure" });
    expect(runtime.fabricFailed).toBe(true);
    expect(reduceHeroRuntime(runtime, { type: "fabric-failure" })).toBe(runtime);

    runtime = reduceHeroRuntime(runtime, { type: "fabric-entrance-complete" });
    expect(runtime.fabricEntranceComplete).toBe(true);
    expect(reduceHeroRuntime(runtime, { type: "fabric-entrance-complete" })).toBe(runtime);
  });

  it("pauses an active story when the fabric fails without changing its current state", () => {
    const active = {
      ...createHeroRuntimeState(),
      selectedSource: "owned" as const,
      jobStep: "lost" as const,
      storyPlaying: true,
    };

    expect(reduceHeroRuntime(active, { type: "fabric-failure" })).toEqual({
      ...active,
      storyPlaying: false,
      fabricFailed: true,
    });
  });

  it("keeps production fabric isolated from the hero lab and renders only FabricHeroScene", () => {
    for (const path of productionFabricSourcePaths) expect(source(path)).not.toMatch(/hero-lab/i);

    const canvas = source("components/landing/hero-fabric/HeroFabricCanvas.tsx");
    expect(canvas).toContain("<FabricHeroScene");
    expect(canvas).not.toContain("TopologyScene");
    expect(canvas).not.toMatch(/variant\??:/);
  });
});
