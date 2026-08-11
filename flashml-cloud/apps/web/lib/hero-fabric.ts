import type { HeroJobStepKey, HeroSourceKey } from "./hero-story";

export { FABRIC_ASSET_SILHOUETTES } from "./hero-fabric-assets.mjs";

export type FabricQualityTier = "high" | "balanced" | "static";
export type FabricPoint3 = readonly [number, number, number];

export const FABRIC_ENTRANCE_SECONDS = 1.45;
export const FABRIC_MAX_PARALLAX_RADIANS = (2.5 * Math.PI) / 180;

export interface FabricEntranceState {
  elapsedSeconds: number;
  complete: boolean;
}

export interface FabricCanvasConfigInput {
  quality: FabricQualityTier;
  continuous: boolean;
  finePointer: boolean;
}

export interface FabricCanvasConfig {
  dpr: number | [number, number];
  shadows: false | "basic" | "soft";
  shadowMapSize: 0 | 512 | 1024;
  frameloop: "always" | "demand";
  parallax: boolean;
  entranceSeconds: number;
  postprocessing: boolean;
  bloomResolutionScale: 0 | 1;
  bloomIntensity: 0 | 0.26;
  bloomLuminanceSmoothing: 0 | 0.24;
}

export interface FabricCameraPose {
  position: FabricPoint3;
  lookAt: FabricPoint3;
  fov: number;
}

export interface FabricCameraPoseInput {
  viewportWidth: number;
  focusedSource: HeroSourceKey | null;
}

export interface FabricIslandEmphasis {
  scale: number;
  y: number;
}

export interface FabricInspectionRegistration {
  scene: unknown;
  renderer: unknown;
  camera: unknown;
}

export interface FabricInspectionTarget {
  __THREE_SCENE__?: unknown;
  __THREE_RENDERER__?: unknown;
  __THREE_CAMERA__?: unknown;
}

export interface FabricInspectionOwner {
  replace: (registration: FabricInspectionRegistration) => void;
  clear: () => void;
}

export function publishFabricInspectionScene(
  target: FabricInspectionTarget,
  registration: FabricInspectionRegistration,
): () => void {
  target.__THREE_SCENE__ = registration.scene;
  target.__THREE_RENDERER__ = registration.renderer;
  target.__THREE_CAMERA__ = registration.camera;

  return () => {
    const ownsRegistration = target.__THREE_SCENE__ === registration.scene
      && target.__THREE_RENDERER__ === registration.renderer
      && target.__THREE_CAMERA__ === registration.camera;
    if (!ownsRegistration) return;

    delete target.__THREE_SCENE__;
    delete target.__THREE_RENDERER__;
    delete target.__THREE_CAMERA__;
  };
}

export function createFabricInspectionOwner(
  target: FabricInspectionTarget,
): FabricInspectionOwner {
  let clearCurrent: (() => void) | null = null;

  return {
    replace(registration) {
      clearCurrent?.();
      clearCurrent = publishFabricInspectionScene(target, registration);
    },
    clear() {
      clearCurrent?.();
      clearCurrent = null;
    },
  };
}

export type FabricRenderDecision =
  | { mode: "canvas"; quality: FabricQualityTier }
  | { mode: "poster"; reason: "webgl2-unavailable" };

export type FabricRouteMode = "idle" | "active" | "failed" | "verified";

export interface FabricRoutePresentation {
  color: string;
  opacity: number;
  radius: number;
  depthTest: boolean;
  depthWrite: boolean;
  renderOrder: number;
}

const FABRIC_ROUTE_PRESENTATIONS = Object.freeze({
  idle: Object.freeze({
    color: "#303839",
    opacity: 0.42,
    radius: 0.018,
    depthTest: true,
    depthWrite: true,
    renderOrder: 0,
  }),
  active: Object.freeze({
    color: "#ff6a2f",
    opacity: 1,
    radius: 0.048,
    depthTest: false,
    depthWrite: false,
    renderOrder: 20,
  }),
  failed: Object.freeze({
    color: "#ff554b",
    opacity: 1,
    radius: 0.045,
    depthTest: false,
    depthWrite: false,
    renderOrder: 30,
  }),
  verified: Object.freeze({
    color: "#4ba77b",
    opacity: 1,
    radius: 0.042,
    depthTest: false,
    depthWrite: false,
    renderOrder: 20,
  }),
} satisfies Record<FabricRouteMode, FabricRoutePresentation>);

export function getFabricRoutePresentation(mode: FabricRouteMode): FabricRoutePresentation {
  return FABRIC_ROUTE_PRESENTATIONS[mode];
}

const FABRIC_PACKET_RATE = Object.freeze({
  high: 0.14,
  balanced: 0.09,
  static: 0,
} satisfies Record<FabricQualityTier, number>);
const FABRIC_PACKET_SOCKET_EASE_SPAN = 0.1;

function easeFabricPacketAtSockets(progress: number): number {
  const span = FABRIC_PACKET_SOCKET_EASE_SPAN;
  if (progress < span) {
    const normalized = progress / span;
    return span * normalized * normalized * (3 - 2 * normalized);
  }
  if (progress > 1 - span) {
    const normalized = (progress - (1 - span)) / span;
    return (1 - span) + span * normalized * normalized * (3 - 2 * normalized);
  }
  return progress;
}

export function getFabricPacketProgress({
  offset,
  elapsedSeconds,
  quality,
}: FabricPacketProgressInput): number {
  const normalizedOffset = offset >= 0 && offset < 1
    ? offset
    : ((offset % 1) + 1) % 1;
  if (quality === "static") return normalizedOffset;

  const traveled = normalizedOffset
    + Math.max(0, elapsedSeconds) * FABRIC_PACKET_RATE[quality];
  const wrapped = traveled % 1;
  return easeFabricPacketAtSockets(wrapped);
}

export interface FabricCapabilityInput {
  webgl2: boolean;
  reducedMotion: boolean;
  documentVisible: boolean;
  desktop: boolean;
  finePointer: boolean;
}

export interface FabricRuntimeQualityInput {
  baseQuality: FabricQualityTier;
  storyPlaying: boolean;
  reducedMotion: boolean;
  documentVisible: boolean;
}

export interface FabricRuntimeModeInput extends FabricRuntimeQualityInput {
  focusedSource: HeroSourceKey | null;
  entranceCompleted: boolean;
  focusMotionAllowed: boolean;
}

export interface FabricRuntimeMode {
  quality: FabricQualityTier;
  continuous: boolean;
  motionEnabled: boolean;
}

export interface FabricPacketProgressInput {
  offset: number;
  elapsedSeconds: number;
  quality: FabricQualityTier;
}

export interface FabricStorySnapshot {
  step: HeroJobStepKey;
  activeSource: HeroSourceKey | null;
  failedSource: HeroSourceKey | null;
  resumeSource: HeroSourceKey | null;
  checkpointVisible: boolean;
  failureVisible: boolean;
  acceptedVisible: boolean;
  routes: Record<HeroSourceKey, FabricRouteMode>;
}

export interface FabricRoutePointSet {
  islandSocket: FabricPoint3;
  controlPlaneSocket: FabricPoint3;
  points: readonly FabricPoint3[];
}

export interface FabricSourceRouteSegment {
  id: `${HeroSourceKey}-route`;
  kind: "source";
  source: HeroSourceKey;
  mode: FabricRouteMode;
  points: readonly FabricPoint3[];
}

export interface FabricVerifiedExitSegment {
  id: "accepted-exit";
  kind: "verified-exit";
  source: null;
  mode: "verified";
  points: readonly FabricPoint3[];
}

export type FabricRouteSegment = FabricSourceRouteSegment | FabricVerifiedExitSegment;
export type FabricPacketDirection = "forward" | "reverse";

export function getFabricPacketDirection(
  segment: FabricRouteSegment,
): FabricPacketDirection {
  return segment.kind === "source" && segment.mode === "active"
    ? "reverse"
    : "forward";
}

interface FabricSourceLayout {
  position: FabricPoint3;
  islandSize: FabricPoint3;
}

const IDLE_ROUTES = Object.freeze({
  everyday: "idle",
  owned: "idle",
  rented: "idle",
  cloud: "idle",
} as const satisfies Record<HeroSourceKey, FabricRouteMode>);

export const FABRIC_SOURCE_LAYOUT = Object.freeze({
  everyday: Object.freeze({
    position: [-2.85, 0.12, -1.72] as const,
    islandSize: [3.75, 0.32, 2.55] as const,
  }),
  owned: Object.freeze({
    position: [-2.18, -0.02, 1.95] as const,
    islandSize: [2.9, 0.28, 2.12] as const,
  }),
  rented: Object.freeze({
    position: [2.12, -0.08, 1.5] as const,
    islandSize: [2.9, 0.28, 2.12] as const,
  }),
  cloud: Object.freeze({
    position: [2.92, -0.12, -1.52] as const,
    islandSize: [2.9, 0.28, 2.12] as const,
  }),
} as const satisfies Record<HeroSourceKey, FabricSourceLayout>);

function createFabricPoint3(x: number, y: number, z: number): FabricPoint3 {
  return Object.freeze([x, y, z]);
}

function mapFabricPoint3(
  point: FabricPoint3,
  mapper: (value: number, index: 0 | 1 | 2) => number,
): FabricPoint3 {
  return createFabricPoint3(
    mapper(point[0], 0),
    mapper(point[1], 1),
    mapper(point[2], 2),
  );
}

const FABRIC_OVERVIEW_CAMERA = Object.freeze({
  desktop: Object.freeze({
    position: createFabricPoint3(6.828659, 13.842393, 10.123903),
    lookAt: createFabricPoint3(0, 0.28, 0),
    fov: 35,
  }),
  compactDesktop: Object.freeze({
    position: createFabricPoint3(7.41, 6.005, 7.885),
    lookAt: createFabricPoint3(0, -1.5, 0),
    fov: 35,
  }),
  tablet: Object.freeze({
    position: createFabricPoint3(7.8, 6.4, 8.3),
    lookAt: createFabricPoint3(0, -1.5, 0),
    fov: 40,
  }),
  mobile: Object.freeze({
    position: createFabricPoint3(9.5, 8.2, 10.5),
    lookAt: createFabricPoint3(0, 0.34, 0),
    fov: 44,
  }),
} satisfies Record<"desktop" | "compactDesktop" | "tablet" | "mobile", FabricCameraPose>);

export function getFabricCameraPose({
  viewportWidth,
  focusedSource,
}: FabricCameraPoseInput): FabricCameraPose {
  const tier = viewportWidth < 560
    ? "mobile"
    : viewportWidth < 1024
      ? "tablet"
      : viewportWidth <= 1100
        ? "compactDesktop"
        : "desktop";
  const overview = FABRIC_OVERVIEW_CAMERA[tier];
  if (!focusedSource || tier === "mobile") return overview;

  const shift = tier === "tablet" ? 0.32 : 0.36;
  const distanceScale = tier === "tablet" ? 0.92 : 0.87;
  const source = FABRIC_SOURCE_LAYOUT[focusedSource].position;
  const lookAt = createFabricPoint3(
    source[0] * shift,
    overview.lookAt[1] + (source[1] + 0.42 - overview.lookAt[1]) * shift,
    source[2] * shift,
  );
  const offset = mapFabricPoint3(
    overview.position,
    (value, index) => value - overview.lookAt[index],
  );
  const position = mapFabricPoint3(
    offset,
    (value, index) => lookAt[index] + value * distanceScale,
  );
  return { position, lookAt, fov: overview.fov };
}

export function getFabricIslandEmphasis(
  source: HeroSourceKey,
  focusedSource: HeroSourceKey | null,
  viewportWidth: number,
): FabricIslandEmphasis {
  const focused = source === focusedSource;
  const mobile = viewportWidth < 560;
  return {
    scale: focused ? (mobile ? 1.12 : 1.15) : 1,
    y: FABRIC_SOURCE_LAYOUT[source].position[1] + (focused ? (mobile ? 0.28 : 0.32) : 0),
  };
}

export function getFabricFocusTransition(
  quality: FabricQualityTier,
  continuous: boolean,
): "damp" | "snap" {
  return continuous && quality !== "static" ? "damp" : "snap";
}

export function isFabricFocusSettled(
  current: number,
  target: number,
  tolerance = 0.001,
): boolean {
  return Math.abs(current - target) < tolerance;
}

export function dampFabricFocusValue(
  current: number,
  target: number,
  deltaSeconds: number,
  tolerance = 0.001,
): number {
  const next = current + (target - current) * (
    1 - Math.exp(-8 * Math.max(0, deltaSeconds))
  );
  return isFabricFocusSettled(next, target, tolerance) ? target : next;
}

export function getFabricIslandTargetY(source: HeroSourceKey, selected: boolean): number {
  return FABRIC_SOURCE_LAYOUT[source].position[1] + (selected ? 0.26 : 0);
}

export function getFabricSelectionTransition(
  quality: FabricQualityTier,
  continuous: boolean,
): "damp" | "snap" {
  return getFabricFocusTransition(quality, continuous);
}

export function settleFabricSelectionValue(
  current: number,
  target: number,
  quality: FabricQualityTier,
  continuous: boolean,
  deltaSeconds: number,
): number {
  if (getFabricSelectionTransition(quality, continuous) === "snap") return target;
  const alpha = 1 - Math.exp(-7 * Math.max(0, deltaSeconds));
  return current + (target - current) * alpha;
}

export function createFabricEntranceState(
  quality: FabricQualityTier,
  previouslyCompleted: boolean,
): FabricEntranceState {
  const complete = previouslyCompleted || quality === "static";
  return {
    elapsedSeconds: complete ? FABRIC_ENTRANCE_SECONDS : 0,
    complete,
  };
}

export function advanceFabricEntrance(
  state: FabricEntranceState,
  deltaSeconds: number,
  quality: FabricQualityTier,
): FabricEntranceState {
  if (state.complete) return state;
  if (quality === "static") {
    return { elapsedSeconds: FABRIC_ENTRANCE_SECONDS, complete: true };
  }

  const elapsedSeconds = Math.min(
    FABRIC_ENTRANCE_SECONDS,
    state.elapsedSeconds + Math.max(0, deltaSeconds),
  );
  return {
    elapsedSeconds,
    complete: elapsedSeconds >= FABRIC_ENTRANCE_SECONDS,
  };
}

export function getFabricParallaxTarget(
  pointerX: number,
  pointerY: number,
  enabled: boolean,
): { yaw: number; pitch: number } {
  if (!enabled) return { yaw: 0, pitch: 0 };

  const yaw = pointerX * FABRIC_MAX_PARALLAX_RADIANS;
  const pitch = -pointerY * FABRIC_MAX_PARALLAX_RADIANS;
  const magnitude = Math.hypot(yaw, pitch);
  if (magnitude <= FABRIC_MAX_PARALLAX_RADIANS || magnitude === 0) {
    return { yaw, pitch };
  }

  const scale = FABRIC_MAX_PARALLAX_RADIANS / magnitude;
  return { yaw: yaw * scale, pitch: pitch * scale };
}

export function getFabricCanvasConfig({
  quality,
  continuous,
  finePointer,
}: FabricCanvasConfigInput): FabricCanvasConfig {
  return {
    dpr: quality === "high" ? [1.25, 2] : 1,
    shadows: quality === "high" ? "soft" : quality === "balanced" ? "basic" : false,
    shadowMapSize: quality === "high" ? 1024 : quality === "balanced" ? 512 : 0,
    frameloop: quality !== "static" && continuous ? "always" : "demand",
    parallax: quality === "high" && continuous && finePointer,
    entranceSeconds: quality === "static" ? 0 : FABRIC_ENTRANCE_SECONDS,
    postprocessing: quality === "high",
    bloomResolutionScale: quality === "high" ? 1 : 0,
    bloomIntensity: quality === "high" ? 0.26 : 0,
    bloomLuminanceSmoothing: quality === "high" ? 0.24 : 0,
  };
}

export function getFabricRuntimeQuality({
  baseQuality,
  storyPlaying,
  reducedMotion,
  documentVisible,
}: FabricRuntimeQualityInput): FabricQualityTier {
  if (!storyPlaying || reducedMotion || !documentVisible) return "static";
  return baseQuality;
}

export function getFabricRuntimeMode({
  baseQuality,
  storyPlaying,
  focusedSource,
  entranceCompleted,
  focusMotionAllowed,
  reducedMotion,
  documentVisible,
}: FabricRuntimeModeInput): FabricRuntimeMode {
  const canAnimate = baseQuality !== "static" && !reducedMotion && documentVisible;
  if (!canAnimate) {
    return { quality: "static", continuous: false, motionEnabled: false };
  }

  if (!entranceCompleted) {
    return { quality: baseQuality, continuous: true, motionEnabled: true };
  }

  if (storyPlaying) {
    return { quality: baseQuality, continuous: true, motionEnabled: true };
  }

  if (focusedSource) {
    return {
      quality: baseQuality,
      continuous: false,
      motionEnabled: focusMotionAllowed,
    };
  }

  return { quality: "static", continuous: false, motionEnabled: false };
}

export const FABRIC_ASSET_URLS = Object.freeze({
  everyday: "/models/hero/fabric/everyday-machines.glb",
  owned: "/models/hero/fabric/owned-infrastructure.glb",
  rented: "/models/hero/fabric/rented-gpu.glb",
  cloud: "/models/hero/fabric/cloud-hpc.glb",
  controlPlane: "/models/hero/fabric/control-plane.glb",
} as const);

export const FABRIC_ROUTE_POINTS = Object.freeze({
  everyday: Object.freeze({
    islandSocket: [-1.06, 0.32, -0.82] as const,
    controlPlaneSocket: [-0.68, 0.68, -0.42] as const,
    points: Object.freeze([
      [-1.06, 0.32, -0.82] as const,
      [-0.9, 0.38, -0.62] as const,
      [-0.78, 0.56, -0.5] as const,
      [-0.68, 0.68, -0.42] as const,
    ]),
  }),
  owned: Object.freeze({
    islandSocket: [-0.82, 0.26, 1.08] as const,
    controlPlaneSocket: [-0.58, 0.68, 0.42] as const,
    points: Object.freeze([
      [-0.82, 0.26, 1.08] as const,
      [-0.74, 0.34, 0.8] as const,
      [-0.64, 0.54, 0.58] as const,
      [-0.58, 0.68, 0.42] as const,
    ]),
  }),
  rented: Object.freeze({
    islandSocket: [0.82, 0.24, 0.92] as const,
    controlPlaneSocket: [0.58, 0.68, 0.42] as const,
    points: Object.freeze([
      [0.82, 0.24, 0.92] as const,
      [0.74, 0.34, 0.72] as const,
      [0.64, 0.54, 0.56] as const,
      [0.58, 0.68, 0.42] as const,
    ]),
  }),
  cloud: Object.freeze({
    islandSocket: [1.5, 0.2, -0.82] as const,
    controlPlaneSocket: [0.68, 0.68, -0.42] as const,
    points: Object.freeze([
      [1.5, 0.2, -0.82] as const,
      [1.08, 0.32, -0.68] as const,
      [0.82, 0.54, -0.52] as const,
      [0.68, 0.68, -0.42] as const,
    ]),
  }),
} as const satisfies Record<HeroSourceKey, FabricRoutePointSet>);

export const FABRIC_ACCEPTED_EXIT_POINTS = Object.freeze([
  [0.86, 0.78, 0] as const,
  [2.12, 0.92, 0.06] as const,
  [4.12, 0.58, 0.18] as const,
]);

const FABRIC_ROUTE_SOURCE_ORDER = ["everyday", "owned", "rented", "cloud"] as const;

const FABRIC_STORY_SNAPSHOTS = Object.freeze({
  submitted: Object.freeze({
    step: "submitted",
    activeSource: null,
    failedSource: null,
    resumeSource: null,
    checkpointVisible: false,
    failureVisible: false,
    acceptedVisible: false,
    routes: IDLE_ROUTES,
  }),
  assigned: Object.freeze({
    step: "assigned",
    activeSource: "everyday",
    failedSource: null,
    resumeSource: null,
    checkpointVisible: false,
    failureVisible: false,
    acceptedVisible: false,
    routes: Object.freeze({ ...IDLE_ROUTES, everyday: "active" as const }),
  }),
  checkpointed: Object.freeze({
    step: "checkpointed",
    activeSource: "everyday",
    failedSource: null,
    resumeSource: null,
    checkpointVisible: true,
    failureVisible: false,
    acceptedVisible: false,
    routes: Object.freeze({ ...IDLE_ROUTES, everyday: "active" as const }),
  }),
  lost: Object.freeze({
    step: "lost",
    activeSource: null,
    failedSource: "everyday",
    resumeSource: null,
    checkpointVisible: true,
    failureVisible: true,
    acceptedVisible: false,
    routes: Object.freeze({ ...IDLE_ROUTES, everyday: "failed" as const }),
  }),
  resumed: Object.freeze({
    step: "resumed",
    activeSource: "rented",
    failedSource: "everyday",
    resumeSource: "rented",
    checkpointVisible: true,
    failureVisible: false,
    acceptedVisible: false,
    routes: Object.freeze({ ...IDLE_ROUTES, rented: "active" as const }),
  }),
  accepted: Object.freeze({
    step: "accepted",
    activeSource: "rented",
    failedSource: "everyday",
    resumeSource: "rented",
    checkpointVisible: true,
    failureVisible: false,
    acceptedVisible: true,
    routes: Object.freeze({ ...IDLE_ROUTES, rented: "verified" as const }),
  }),
} satisfies Record<HeroJobStepKey, FabricStorySnapshot>);

export function getFabricStorySnapshot(step: HeroJobStepKey): FabricStorySnapshot {
  return FABRIC_STORY_SNAPSHOTS[step];
}

export function getFabricRouteSegments(
  snapshot: FabricStorySnapshot,
): readonly FabricRouteSegment[] {
  const sourceSegments: FabricSourceRouteSegment[] = FABRIC_ROUTE_SOURCE_ORDER.map((source) => ({
    id: `${source}-route`,
    kind: "source",
    source,
    mode: snapshot.routes[source],
    points: FABRIC_ROUTE_POINTS[source].points,
  }));

  if (!snapshot.acceptedVisible) return sourceSegments;

  return [
    ...sourceSegments,
    {
      id: "accepted-exit",
      kind: "verified-exit",
      source: null,
      mode: "verified",
      points: FABRIC_ACCEPTED_EXIT_POINTS,
    },
  ];
}

export function getFabricRenderDecision(input: FabricCapabilityInput): FabricRenderDecision {
  if (!input.webgl2) return { mode: "poster", reason: "webgl2-unavailable" };
  if (input.reducedMotion || !input.documentVisible) return { mode: "canvas", quality: "static" };
  if (input.desktop && input.finePointer) return { mode: "canvas", quality: "high" };
  return { mode: "canvas", quality: "balanced" };
}
