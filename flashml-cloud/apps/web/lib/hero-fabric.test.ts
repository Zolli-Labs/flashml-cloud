import { describe, expect, it } from "vitest";
import {
  FABRIC_ENTRANCE_SECONDS,
  FABRIC_ASSET_URLS,
  FABRIC_ASSET_SILHOUETTES,
  FABRIC_ROUTE_POINTS,
  FABRIC_SOURCE_LAYOUT,
  advanceFabricEntrance,
  createFabricEntranceState,
  createFabricInspectionOwner,
  dampFabricFocusValue,
  getFabricCanvasConfig,
  getFabricCameraPose,
  getFabricFocusTransition,
  getFabricIslandEmphasis,
  getFabricIslandTargetY,
  getFabricPacketProgress,
  getFabricPacketDirection,
  getFabricParallaxTarget,
  getFabricRenderDecision,
  getFabricRoutePresentation,
  getFabricRouteSegments,
  getFabricRuntimeMode,
  getFabricRuntimeQuality,
  getFabricSelectionTransition,
  getFabricStorySnapshot,
  publishFabricInspectionScene,
  isFabricFocusSettled,
  settleFabricSelectionValue,
} from "./hero-fabric";

function distance(a: readonly [number, number, number], b: readonly [number, number, number]) {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

describe("fabric inspection bridge", () => {
  it("publishes the authored R3F scene for devtools and cleans up only its own registration", () => {
    const target: Record<string, unknown> = {};
    const scene = { name: "FabricScene" };
    const renderer = { name: "FabricRenderer" };
    const camera = { name: "FabricCamera" };

    const cleanup = publishFabricInspectionScene(target, { scene, renderer, camera });

    expect(target).toMatchObject({
      __THREE_SCENE__: scene,
      __THREE_RENDERER__: renderer,
      __THREE_CAMERA__: camera,
    });

    target.__THREE_SCENE__ = { name: "ReplacementScene" };
    cleanup();

    expect(target.__THREE_SCENE__).toEqual({ name: "ReplacementScene" });
    expect(target.__THREE_RENDERER__).toBe(renderer);
    expect(target.__THREE_CAMERA__).toBe(camera);
  });

  it("replaces an owned Canvas registration and clears the current one", () => {
    const target: Record<string, unknown> = {};
    const owner = createFabricInspectionOwner(target);
    const first = {
      scene: { name: "FirstScene" },
      renderer: { name: "FirstRenderer" },
      camera: { name: "FirstCamera" },
    };
    const replacement = {
      scene: { name: "ReplacementScene" },
      renderer: { name: "ReplacementRenderer" },
      camera: { name: "ReplacementCamera" },
    };

    owner.replace(first);
    owner.replace(replacement);

    expect(target).toMatchObject({
      __THREE_SCENE__: replacement.scene,
      __THREE_RENDERER__: replacement.renderer,
      __THREE_CAMERA__: replacement.camera,
    });

    owner.clear();
    expect(target).toEqual({});
  });
});

describe("fabric story contract", () => {
  it.each([
    {
      step: "submitted",
      expected: {
        activeSource: null,
        failedSource: null,
        resumeSource: null,
        checkpointVisible: false,
        failureVisible: false,
        acceptedVisible: false,
        routes: { everyday: "idle", owned: "idle", rented: "idle", cloud: "idle" },
      },
    },
    {
      step: "assigned",
      expected: {
        activeSource: "everyday",
        failedSource: null,
        resumeSource: null,
        checkpointVisible: false,
        failureVisible: false,
        acceptedVisible: false,
        routes: { everyday: "active", owned: "idle", rented: "idle", cloud: "idle" },
      },
    },
    {
      step: "checkpointed",
      expected: {
        activeSource: "everyday",
        failedSource: null,
        resumeSource: null,
        checkpointVisible: true,
        failureVisible: false,
        acceptedVisible: false,
        routes: { everyday: "active", owned: "idle", rented: "idle", cloud: "idle" },
      },
    },
    {
      step: "lost",
      expected: {
        activeSource: null,
        failedSource: "everyday",
        resumeSource: null,
        checkpointVisible: true,
        failureVisible: true,
        acceptedVisible: false,
        routes: { everyday: "failed", owned: "idle", rented: "idle", cloud: "idle" },
      },
    },
    {
      step: "resumed",
      expected: {
        activeSource: "rented",
        failedSource: "everyday",
        resumeSource: "rented",
        checkpointVisible: true,
        failureVisible: false,
        acceptedVisible: false,
        routes: { everyday: "idle", owned: "idle", rented: "active", cloud: "idle" },
      },
    },
    {
      step: "accepted",
      expected: {
        activeSource: "rented",
        failedSource: "everyday",
        resumeSource: "rented",
        checkpointVisible: true,
        failureVisible: false,
        acceptedVisible: true,
        routes: { everyday: "idle", owned: "idle", rented: "verified", cloud: "idle" },
      },
    },
  ] as const)("maps $step to its complete recovery snapshot", ({ step, expected }) => {
    expect(getFabricStorySnapshot(step)).toMatchObject({ step, ...expected });
  });
});

describe("fabric render decisions", () => {
  it("selects the conservative canvas quality tier", () => {
    expect(
      getFabricRenderDecision({
        webgl2: true,
        reducedMotion: false,
        documentVisible: true,
        desktop: true,
        finePointer: true,
      }),
    ).toEqual({ mode: "canvas", quality: "high" });
    expect(
      getFabricRenderDecision({
        webgl2: true,
        reducedMotion: false,
        documentVisible: true,
        desktop: false,
        finePointer: false,
      }),
    ).toEqual({ mode: "canvas", quality: "balanced" });
    expect(
      getFabricRenderDecision({
        webgl2: true,
        reducedMotion: true,
        documentVisible: true,
        desktop: true,
        finePointer: true,
      }),
    ).toEqual({ mode: "canvas", quality: "static" });
    expect(
      getFabricRenderDecision({
        webgl2: false,
        reducedMotion: false,
        documentVisible: true,
        desktop: true,
        finePointer: true,
      }),
    ).toEqual({ mode: "poster", reason: "webgl2-unavailable" });
  });
});

describe("fabric runtime lifecycle", () => {
  it.each([
    {
      reason: "an active High story",
      input: {
        baseQuality: "high",
        storyPlaying: true,
        focusedSource: null,
        entranceCompleted: true,
        focusMotionAllowed: true,
        reducedMotion: false,
        documentVisible: true,
      },
      expected: { quality: "high", continuous: true, motionEnabled: true },
    },
    {
      reason: "a paused High source focus",
      input: {
        baseQuality: "high",
        storyPlaying: false,
        focusedSource: "owned",
        entranceCompleted: true,
        focusMotionAllowed: true,
        reducedMotion: false,
        documentVisible: true,
      },
      expected: { quality: "high", continuous: false, motionEnabled: true },
    },
    {
      reason: "a paused Balanced source focus",
      input: {
        baseQuality: "balanced",
        storyPlaying: false,
        focusedSource: "rented",
        entranceCompleted: true,
        focusMotionAllowed: true,
        reducedMotion: false,
        documentVisible: true,
      },
      expected: { quality: "balanced", continuous: false, motionEnabled: true },
    },
    {
      reason: "the finite entrance before autoplay initializes",
      input: {
        baseQuality: "high",
        storyPlaying: false,
        focusedSource: null,
        entranceCompleted: false,
        focusMotionAllowed: true,
        reducedMotion: false,
        documentVisible: true,
      },
      expected: { quality: "high", continuous: true, motionEnabled: true },
    },
    {
      reason: "a paused overview after entrance",
      input: {
        baseQuality: "high",
        storyPlaying: false,
        focusedSource: null,
        entranceCompleted: true,
        focusMotionAllowed: true,
        reducedMotion: false,
        documentVisible: true,
      },
      expected: { quality: "static", continuous: false, motionEnabled: false },
    },
    {
      reason: "a reduced-motion source focus",
      input: {
        baseQuality: "high",
        storyPlaying: false,
        focusedSource: "cloud",
        entranceCompleted: true,
        focusMotionAllowed: true,
        reducedMotion: true,
        documentVisible: true,
      },
      expected: { quality: "static", continuous: false, motionEnabled: false },
    },
    {
      reason: "a hidden source focus",
      input: {
        baseQuality: "balanced",
        storyPlaying: false,
        focusedSource: "everyday",
        entranceCompleted: true,
        focusMotionAllowed: true,
        reducedMotion: false,
        documentVisible: false,
      },
      expected: { quality: "static", continuous: false, motionEnabled: false },
    },
    {
      reason: "a coarse-pointer Balanced source focus",
      input: {
        baseQuality: "balanced",
        storyPlaying: false,
        focusedSource: "owned",
        entranceCompleted: true,
        focusMotionAllowed: false,
        reducedMotion: false,
        documentVisible: true,
      },
      expected: { quality: "balanced", continuous: false, motionEnabled: false },
    },
  ] as const)("derives $reason without coupling focus quality to autoplay", ({ input, expected }) => {
    expect(getFabricRuntimeMode(input)).toEqual(expected);
  });

  it.each(["high", "balanced"] as const)(
    "preserves %s asset quality from active autoplay into paused focus",
    (baseQuality) => {
      const shared = {
        baseQuality,
        entranceCompleted: true,
        focusMotionAllowed: true,
        reducedMotion: false,
        documentVisible: true,
      } as const;
      const active = getFabricRuntimeMode({
        ...shared,
        storyPlaying: true,
        focusedSource: null,
      });
      const focused = getFabricRuntimeMode({
        ...shared,
        storyPlaying: false,
        focusedSource: "owned",
      });

      expect(focused.quality).toBe(active.quality);
      expect(focused).toMatchObject({
        quality: baseQuality,
        continuous: false,
        motionEnabled: true,
      });
    },
  );

  it("advances demand focus over multiple frames, reaches exact targets, then sleeps", () => {
    let cameraX = 0;
    let islandY = 0;
    let islandScale = 1;
    const targets = { cameraX: 6.828659, islandY: 0.3, islandScale: 1.15 };
    let renderedFrames = 0;
    const invalidate = () => (
      !isFabricFocusSettled(cameraX, targets.cameraX)
      || !isFabricFocusSettled(islandY, targets.islandY)
      || !isFabricFocusSettled(islandScale, targets.islandScale)
    );

    expect(invalidate()).toBe(true);
    while (invalidate() && renderedFrames < 180) {
      cameraX = dampFabricFocusValue(cameraX, targets.cameraX, 1 / 60);
      islandY = dampFabricFocusValue(islandY, targets.islandY, 1 / 60);
      islandScale = dampFabricFocusValue(islandScale, targets.islandScale, 1 / 60);
      renderedFrames += 1;
      if (renderedFrames === 1) {
        expect(cameraX).toBeGreaterThan(0);
        expect(cameraX).toBeLessThan(targets.cameraX);
        expect(islandScale).toBeGreaterThan(1);
        expect(islandScale).toBeLessThan(targets.islandScale);
      }
    }

    expect(renderedFrames).toBeGreaterThan(1);
    expect(renderedFrames).toBeLessThan(180);
    expect({ cameraX, islandY, islandScale }).toEqual(targets);
    expect(invalidate()).toBe(false);
  });

  it.each([
    {
      reason: "a paused story",
      input: {
        baseQuality: "high",
        storyPlaying: false,
        reducedMotion: false,
        documentVisible: true,
      },
      expected: "static",
    },
    {
      reason: "reduced motion",
      input: {
        baseQuality: "high",
        storyPlaying: true,
        reducedMotion: true,
        documentVisible: true,
      },
      expected: "static",
    },
    {
      reason: "a hidden document",
      input: {
        baseQuality: "balanced",
        storyPlaying: true,
        reducedMotion: false,
        documentVisible: false,
      },
      expected: "static",
    },
    {
      reason: "an active visible desktop story",
      input: {
        baseQuality: "high",
        storyPlaying: true,
        reducedMotion: false,
        documentVisible: true,
      },
      expected: "high",
    },
    {
      reason: "an active visible constrained story",
      input: {
        baseQuality: "balanced",
        storyPlaying: true,
        reducedMotion: false,
        documentVisible: true,
      },
      expected: "balanced",
    },
  ] as const)("resolves $reason to $expected quality", ({ input, expected }) => {
    expect(getFabricRuntimeQuality(input)).toBe(expected);
  });

  it("runs the finite entrance once without depending on story playback", () => {
    const pausedCanvas = getFabricCanvasConfig({
      quality: "high",
      continuous: false,
      finePointer: true,
    });
    let entrance = createFabricEntranceState("high", false);

    expect(pausedCanvas.frameloop).toBe("demand");
    expect(entrance).toEqual({ elapsedSeconds: 0, complete: false });

    entrance = advanceFabricEntrance(entrance, 0.7, "high");
    expect(entrance).toEqual({ elapsedSeconds: 0.7, complete: false });

    entrance = advanceFabricEntrance(entrance, 0.75, "balanced");
    expect(entrance).toEqual({
      elapsedSeconds: FABRIC_ENTRANCE_SECONDS,
      complete: true,
    });

    expect(advanceFabricEntrance(entrance, 1, "high")).toBe(entrance);
    expect(createFabricEntranceState("static", false)).toEqual({
      elapsedSeconds: FABRIC_ENTRANCE_SECONDS,
      complete: true,
    });
    expect(createFabricEntranceState("high", true)).toEqual({
      elapsedSeconds: FABRIC_ENTRANCE_SECONDS,
      complete: true,
    });
  });

  it.each([
    { quality: "high", continuous: true, expected: "damp" },
    { quality: "balanced", continuous: true, expected: "damp" },
    { quality: "high", continuous: false, expected: "snap" },
    { quality: "balanced", continuous: false, expected: "snap" },
    { quality: "static", continuous: true, expected: "snap" },
  ] as const)(
    "uses $expected selection settling for $quality when continuous=$continuous",
    ({ quality, continuous, expected }) => {
      expect(getFabricSelectionTransition(quality, continuous)).toBe(expected);
    },
  );

  it("snaps paused selection to its exact target and damps an active selection", () => {
    expect(settleFabricSelectionValue(0.12, 0.38, "high", false, 1 / 60)).toBe(0.38);
    expect(settleFabricSelectionValue(0.12, 0.38, "static", true, 1 / 60)).toBe(0.38);

    const damped = settleFabricSelectionValue(0.12, 0.38, "balanced", true, 1 / 60);
    expect(damped).toBeGreaterThan(0.12);
    expect(damped).toBeLessThan(0.38);
  });

  it("caps diagonal pointer parallax to a total 2.5 degree vector", () => {
    const target = getFabricParallaxTarget(1, -1, true);
    const magnitude = Math.hypot(target.yaw, target.pitch);
    const maxRadians = (2.5 * Math.PI) / 180;

    expect(magnitude).toBeCloseTo(maxRadians, 10);
    expect(target.yaw).toBeCloseTo(maxRadians / Math.sqrt(2), 10);
    expect(target.pitch).toBeCloseTo(maxRadians / Math.sqrt(2), 10);
    expect(getFabricParallaxTarget(1, 1, false)).toEqual({ yaw: 0, pitch: 0 });
  });
});

describe("fabric Canvas tier configuration", () => {
  it.each([
    {
      quality: "high",
      expected: {
        dpr: [1.25, 2],
        shadows: "soft",
        shadowMapSize: 1024,
        frameloop: "always",
        parallax: true,
        entranceSeconds: 1.45,
        postprocessing: true,
        bloomResolutionScale: 1,
        bloomIntensity: 0.26,
        bloomLuminanceSmoothing: 0.24,
      },
    },
    {
      quality: "balanced",
      expected: {
        dpr: 1,
        shadows: "basic",
        shadowMapSize: 512,
        frameloop: "always",
        parallax: false,
        entranceSeconds: 1.45,
        postprocessing: false,
        bloomResolutionScale: 0,
        bloomIntensity: 0,
        bloomLuminanceSmoothing: 0,
      },
    },
    {
      quality: "static",
      expected: {
        dpr: 1,
        shadows: false,
        shadowMapSize: 0,
        frameloop: "demand",
        parallax: false,
        entranceSeconds: 0,
        postprocessing: false,
        bloomResolutionScale: 0,
        bloomIntensity: 0,
        bloomLuminanceSmoothing: 0,
      },
    },
  ] as const)("configures the $quality renderer tier", ({ quality, expected }) => {
    expect(
      getFabricCanvasConfig({ quality, continuous: true, finePointer: true }),
    ).toEqual(expected);
  });

  it("suspends a paused High renderer without disabling its one-shot entrance", () => {
    expect(
      getFabricCanvasConfig({ quality: "high", continuous: false, finePointer: true }),
    ).toEqual({
      dpr: [1.25, 2],
      shadows: "soft",
      shadowMapSize: 1024,
      frameloop: "demand",
      parallax: false,
      entranceSeconds: 1.45,
      postprocessing: true,
      bloomResolutionScale: 1,
      bloomIntensity: 0.26,
      bloomLuminanceSmoothing: 0.24,
    });
  });
});

describe("fabric camera and focus contracts", () => {
  it.each([
    [1440, null, [6.828659, 13.842393, 10.123903], [0, 0.28, 0], 35],
    [1101, null, [6.828659, 13.842393, 10.123903], [0, 0.28, 0], 35],
    [1100, null, [7.41, 6.005, 7.885], [0, -1.5, 0], 35],
    [1024, null, [7.41, 6.005, 7.885], [0, -1.5, 0], 35],
    [1023, null, [7.8, 6.4, 8.3], [0, -1.5, 0], 40],
    [768, null, [7.8, 6.4, 8.3], [0, -1.5, 0], 40],
    [390, null, [9.5, 8.2, 10.5], [0, 0.34, 0], 44],
  ] as const)("returns the authored %ipx overview pose", (viewportWidth, focusedSource, position, lookAt, fov) => {
    expect(getFabricCameraPose({ viewportWidth, focusedSource })).toEqual({ position, lookAt, fov });
  });

  it("moves a desktop focus toward the selected island while preserving the overview view vector", () => {
    const overview = getFabricCameraPose({ viewportWidth: 1440, focusedSource: null });
    const focused = getFabricCameraPose({ viewportWidth: 1440, focusedSource: "cloud" });
    const source = FABRIC_SOURCE_LAYOUT.cloud.position;
    expect(focused.lookAt[0]).toBeCloseTo(source[0] * 0.36, 5);
    expect(focused.lookAt[2]).toBeCloseTo(source[2] * 0.36, 5);
    expect(distance(focused.position, focused.lookAt))
      .toBeCloseTo(distance(overview.position, overview.lookAt) * 0.87, 5);
  });

  it("uses the compact desktop pose and desktop focus contract at 1024px", () => {
    const overview = getFabricCameraPose({ viewportWidth: 1024, focusedSource: null });
    const focused = getFabricCameraPose({ viewportWidth: 1024, focusedSource: "everyday" });

    expect(overview.fov).toBe(35);
    expect(focused.lookAt[0]).toBeCloseTo(-1.026, 10);
    expect(focused.lookAt[1]).toBeCloseTo(-0.7656, 10);
    expect(focused.lookAt[2]).toBeCloseTo(-0.6192, 10);
    expect(distance(focused.position, focused.lookAt))
      .toBeCloseTo(distance(overview.position, overview.lookAt) * 0.87, 5);
  });

  it("keeps 1023px in the tablet tier with the approved tablet focus contract", () => {
    const overview = getFabricCameraPose({ viewportWidth: 1023, focusedSource: null });
    const focused = getFabricCameraPose({ viewportWidth: 1023, focusedSource: "everyday" });

    expect(overview.fov).toBe(40);
    expect(focused.lookAt).toEqual([-0.912, -0.8472, -0.5504]);
    expect(distance(focused.position, focused.lookAt))
      .toBeCloseTo(distance(overview.position, overview.lookAt) * 0.92, 5);
  });

  it("keeps the mobile camera fixed and emphasizes the focused source by object pose", () => {
    expect(getFabricCameraPose({ viewportWidth: 390, focusedSource: "owned" }))
      .toEqual(getFabricCameraPose({ viewportWidth: 390, focusedSource: null }));
    expect(getFabricIslandEmphasis("owned", "owned", 390)).toEqual({
      scale: 1.12,
      y: FABRIC_SOURCE_LAYOUT.owned.position[1] + 0.28,
    });
  });

  it("keeps unfocused islands at their base pose and lifts desktop focus by the global constraint", () => {
    expect(getFabricIslandEmphasis("rented", "cloud", 1440)).toEqual({
      scale: 1,
      y: FABRIC_SOURCE_LAYOUT.rented.position[1],
    });
    expect(getFabricIslandEmphasis("cloud", "cloud", 768)).toEqual({
      scale: 1.15,
      y: FABRIC_SOURCE_LAYOUT.cloud.position[1] + 0.32,
    });
  });

  it.each([
    { quality: "high", continuous: true, expected: "damp" },
    { quality: "balanced", continuous: true, expected: "damp" },
    { quality: "static", continuous: true, expected: "snap" },
    { quality: "high", continuous: false, expected: "snap" },
  ] as const)("uses $expected focus settling for $quality when continuous=$continuous", ({ quality, continuous, expected }) => {
    expect(getFabricFocusTransition(quality, continuous)).toBe(expected);
  });
});

describe("fabric source resources", () => {
  it.each([
    { source: "everyday", idleY: 0.12, selectedY: 0.38 },
    { source: "owned", idleY: -0.02, selectedY: 0.24 },
    { source: "rented", idleY: -0.08, selectedY: 0.18 },
    { source: "cloud", idleY: -0.12, selectedY: 0.14 },
  ] as const)("preserves the $source base height while applying selection lift", ({
    source,
    idleY,
    selectedY,
  }) => {
    expect(getFabricIslandTargetY(source, false)).toBeCloseTo(idleY, 10);
    expect(getFabricIslandTargetY(source, true)).toBeCloseTo(selectedY, 10);
  });

  it("maps every source to its stable island layout and local model", () => {
    expect(FABRIC_SOURCE_LAYOUT).toEqual({
      everyday: { position: [-2.85, 0.12, -1.72], islandSize: [3.75, 0.32, 2.55] },
      owned: { position: [-2.18, -0.02, 1.95], islandSize: [2.9, 0.28, 2.12] },
      rented: { position: [2.12, -0.08, 1.5], islandSize: [2.9, 0.28, 2.12] },
      cloud: { position: [2.92, -0.12, -1.52], islandSize: [2.9, 0.28, 2.12] },
    });
    expect(FABRIC_ASSET_URLS).toEqual({
      everyday: "/models/hero/fabric/everyday-machines.glb",
      owned: "/models/hero/fabric/owned-infrastructure.glb",
      rented: "/models/hero/fabric/rented-gpu.glb",
      cloud: "/models/hero/fabric/cloud-hpc.glb",
      controlPlane: "/models/hero/fabric/control-plane.glb",
    });
  });

  it("defines silhouette constraints for the wide rented GPU sled and larger cloud rack bank", () => {
    expect(FABRIC_ASSET_SILHOUETTES.rented.ratios).toContainEqual({
      numerator: ["RentedGPUAssembly", "x"],
      denominator: ["RentedGPUAssembly", "y"],
      min: 2.2,
    });
    expect(FABRIC_ASSET_SILHOUETTES.cloud.relativeRatios).toEqual(expect.arrayContaining([
      { subject: ["CloudRackBank", "y"], reference: ["owned", "OwnedRackAssembly", "y"], min: 1.35 },
      { subject: ["CloudRackBank", "x"], reference: ["owned", "OwnedRackAssembly", "x"], min: 1.25 },
    ]));
  });
});

describe("fabric execution route contract", () => {
  it("sends assigned and resumed packets from the control plane to workers", () => {
    const assigned = getFabricRouteSegments(getFabricStorySnapshot("assigned"));
    const resumed = getFabricRouteSegments(getFabricStorySnapshot("resumed"));
    const accepted = getFabricRouteSegments(getFabricStorySnapshot("accepted"));

    const assignedEveryday = assigned.find(
      (segment) => segment.kind === "source" && segment.source === "everyday",
    )!;
    const resumedRented = resumed.find(
      (segment) => segment.kind === "source" && segment.source === "rented",
    )!;
    const verifiedRented = accepted.find(
      (segment) => segment.kind === "source" && segment.source === "rented",
    )!;
    const acceptedExit = accepted.find((segment) => segment.kind === "verified-exit")!;

    expect(getFabricPacketDirection(assignedEveryday)).toBe("reverse");
    expect(getFabricPacketDirection(resumedRented)).toBe("reverse");
    expect(getFabricPacketDirection(verifiedRented)).toBe("forward");
    expect(getFabricPacketDirection(acceptedExit)).toBe("forward");
  });

  it("advances active packets at a readable tier-specific rate and wraps the route", () => {
    expect(getFabricPacketProgress({
      offset: 0.3,
      elapsedSeconds: 0.5,
      quality: "high",
    })).toBeCloseTo(0.37, 10);
    expect(getFabricPacketProgress({
      offset: 0.3,
      elapsedSeconds: 0.5,
      quality: "balanced",
    })).toBeCloseTo(0.345, 10);

    const wrapped = getFabricPacketProgress({
      offset: 0.98,
      elapsedSeconds: 0.3,
      quality: "high",
    });
    expect(wrapped).toBeGreaterThanOrEqual(0);
    expect(wrapped).toBeLessThan(0.022);
  });

  it("eases briefly at sockets while Static packets remain fixed", () => {
    const easedStart = getFabricPacketProgress({
      offset: 0,
      elapsedSeconds: 0.25,
      quality: "high",
    });
    const easedEnd = getFabricPacketProgress({
      offset: 0.93,
      elapsedSeconds: 0.25,
      quality: "high",
    });

    expect(easedStart).toBeGreaterThan(0);
    expect(easedStart).toBeLessThan(0.035);
    expect(easedEnd).toBeGreaterThan(0.965);
    expect(easedEnd).toBeLessThan(1);
    expect(getFabricPacketProgress({
      offset: 0.4,
      elapsedSeconds: 0,
      quality: "static",
    })).toBe(0.4);
    expect(getFabricPacketProgress({
      offset: 0.4,
      elapsedSeconds: 120,
      quality: "static",
    })).toBe(0.4);
  });

  it("keeps active and failed routes legible in the foreground while idle routes recede", () => {
    expect(getFabricRoutePresentation("idle")).toEqual({
      color: "#303839",
      opacity: 0.42,
      radius: 0.018,
      depthTest: true,
      depthWrite: true,
      renderOrder: 0,
    });
    expect(getFabricRoutePresentation("active")).toMatchObject({
      color: "#ff6a2f",
      opacity: 1,
      radius: 0.048,
      depthTest: false,
      depthWrite: false,
      renderOrder: 20,
    });
    expect(getFabricRoutePresentation("failed")).toMatchObject({
      color: "#ff554b",
      opacity: 1,
      radius: 0.045,
      depthTest: false,
      depthWrite: false,
      renderOrder: 30,
    });
    expect(getFabricRoutePresentation("verified")).toMatchObject({
      color: "#4ba77b",
      depthTest: false,
      depthWrite: false,
      renderOrder: 20,
    });
  });

  const expectedSockets = {
    everyday: {
      island: [-1.06, 0.32, -0.82],
      controlPlane: [-0.68, 0.68, -0.42],
    },
    owned: {
      island: [-0.82, 0.26, 1.08],
      controlPlane: [-0.58, 0.68, 0.42],
    },
    rented: {
      island: [0.82, 0.24, 0.92],
      controlPlane: [0.58, 0.68, 0.42],
    },
    cloud: {
      island: [1.5, 0.2, -0.82],
      controlPlane: [0.68, 0.68, -0.42],
    },
  } as const;

  it("connects every island socket to its own control-plane socket", () => {
    const segments = getFabricRouteSegments(getFabricStorySnapshot("submitted"));
    const sourceSegments = segments.filter((segment) => segment.kind === "source");

    expect(sourceSegments).toHaveLength(4);
    for (const source of ["everyday", "owned", "rented", "cloud"] as const) {
      const route = FABRIC_ROUTE_POINTS[source];
      const segment = sourceSegments.find((candidate) => candidate.source === source);

      expect(route.islandSocket).toEqual(expectedSockets[source].island);
      expect(route.controlPlaneSocket).toEqual(expectedSockets[source].controlPlane);
      expect(segment?.points[0]).toEqual(expectedSockets[source].island);
      expect(segment?.points.at(-1)).toEqual(expectedSockets[source].controlPlane);
    }

    expect(new Set(sourceSegments.map((segment) => segment.points.at(-1)?.join(","))).size).toBe(4);
  });

  it("marks only the everyday branch failed after node loss and emits no verified exit", () => {
    const snapshot = getFabricStorySnapshot("lost");
    const segments = getFabricRouteSegments(snapshot);

    expect(snapshot.checkpointVisible).toBe(true);
    expect(
      segments
        .filter((segment) => segment.kind === "source" && segment.mode === "failed")
        .map((segment) => segment.source),
    ).toEqual(["everyday"]);
    expect(segments.filter((segment) => segment.kind === "verified-exit")).toEqual([]);
  });

  it("activates the rented route on resume while retaining the control-plane checkpoint", () => {
    const snapshot = getFabricStorySnapshot("resumed");
    const segments = getFabricRouteSegments(snapshot);

    expect(snapshot.checkpointVisible).toBe(true);
    expect(
      segments
        .filter((segment) => segment.kind === "source" && segment.mode === "active")
        .map((segment) => segment.source),
    ).toEqual(["rented"]);
    expect(
      segments.find((segment) => segment.kind === "source" && segment.source === "everyday")
        ?.mode,
    ).toBe("idle");
  });

  it("emits exactly one accepted-result exit from the control plane", () => {
    const segments = getFabricRouteSegments(getFabricStorySnapshot("accepted"));
    const verifiedExits = segments.filter((segment) => segment.kind === "verified-exit");

    expect(verifiedExits).toEqual([
      {
        id: "accepted-exit",
        kind: "verified-exit",
        source: null,
        mode: "verified",
        points: [
          [0.86, 0.78, 0],
          [2.12, 0.92, 0.06],
          [4.12, 0.58, 0.18],
        ],
      },
    ]);
  });
});
