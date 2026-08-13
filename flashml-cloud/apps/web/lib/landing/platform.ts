export type InfrastructureLayerKey = "external" | "rented" | "owned" | "everyday";
export type HeroSelectionKey = "unified" | InfrastructureLayerKey;
export type RuntimeIconKey =
  | "python" | "numpy" | "pandas" | "scikitlearn" | "scipy"
  | "pytorch" | "nvidia" | "docker" | "github";
export type PlatformFamily = "macos" | "linux" | "windows" | "mobile" | "other";

export type CuratedImageAlias =
  | "python-slim"
  | "sklearn"
  | "pytorch-cpu"
  | "pytorch-cuda";

export interface HeroLayerDetail {
  label: string;
  source: string;
  outcome: string;
}

export interface RuntimeSupportItem {
  icon: RuntimeIconKey;
  label: string;
  imageAlias?: readonly CuratedImageAlias[];
}

export interface HostSupportItem {
  platform: string;
  state: "Proven" | "Preview";
  body: string;
}

export interface MachineHint {
  headline: string;
  body: string;
  nextStep: string;
}

export interface PlatformSignals {
  userAgent: string;
  platform?: string;
  maxTouchPoints?: number;
}

export const HERO_LAYER_ORDER = ["external", "rented", "owned", "everyday"] as const;
export const HERO_SELECTION_ORDER = ["unified", "everyday", "owned", "rented", "external"] as const;

export const HERO_LAYER_DETAILS = {
  external: {
    label: "External capacity",
    source: "Compute made available outside your own environment.",
    outcome: "Bring it into one workload path.",
  },
  rented: {
    label: "Rented capacity",
    source: "Compute you provision for the work at hand.",
    outcome: "Put temporary capacity to work alongside the rest.",
  },
  owned: {
    label: "Owned infrastructure",
    source: "Machines your team operates.",
    outcome: "Use the infrastructure you already manage.",
  },
  everyday: {
    label: "Everyday devices",
    source: "Personal machines you choose to connect.",
    outcome: "Make available compute visible to a workload.",
  },
} as const satisfies Record<InfrastructureLayerKey, HeroLayerDetail>;

export const RUNTIME_SUPPORT = [
  { icon: "python", label: "Python 3.11", imageAlias: ["python-slim"] },
  { icon: "numpy", label: "NumPy" },
  { icon: "pandas", label: "pandas" },
  { icon: "scikitlearn", label: "scikit-learn", imageAlias: ["sklearn"] },
  { icon: "scipy", label: "SciPy" },
  { icon: "pytorch", label: "PyTorch CPU", imageAlias: ["pytorch-cpu"] },
  { icon: "nvidia", label: "PyTorch CUDA 12.4", imageAlias: ["pytorch-cuda"] },
  { icon: "docker", label: "Docker" },
  { icon: "github", label: "GitHub" },
] as const satisfies readonly RuntimeSupportItem[];

export const HOST_SUPPORT = [
  {
    platform: "macOS Apple silicon",
    state: "Proven",
    body: "Verified on macOS arm64 machines.",
  },
  {
    platform: "Linux x86_64",
    state: "Proven",
    body: "Verified on Linux CPU and compatible GPU hosts.",
  },
  {
    platform: "RunPod NVIDIA GPUs",
    state: "Proven",
    body: "Verified with RTX 3090, RTX 4090, and RTX 4000 Ada machines.",
  },
  {
    platform: "Windows 11",
    state: "Preview",
    body: "Preview through Docker Desktop and WSL2.",
  },
] as const satisfies readonly HostSupportItem[];

export const MACHINE_HINTS = {
  macos: {
    headline: "macOS detected",
    body: "The browser can identify macOS but cannot verify CPU architecture.",
    nextStep: "Run flashnode doctor for a real host check.",
  },
  linux: {
    headline: "Linux detected",
    body: "The browser can identify Linux but cannot verify CPU architecture.",
    nextStep: "Run flashnode doctor for a real host check.",
  },
  windows: {
    headline: "Windows detected",
    body: "Windows is Preview through Docker Desktop + WSL2. The browser cannot verify prerequisites.",
    nextStep: "Run flashnode doctor for a real host check.",
  },
  mobile: {
    headline: "Mobile browser detected",
    body: "The browser cannot verify the host from a mobile device.",
    nextStep: "Run flashnode doctor for a real host check.",
  },
  other: {
    headline: "Host not identified",
    body: "The browser cannot verify the host.",
    nextStep: "Run flashnode doctor for a real host check.",
  },
} as const satisfies Record<PlatformFamily, MachineHint>;

export function inferPlatformFamily({
  userAgent,
  platform = "",
  maxTouchPoints = 0,
}: PlatformSignals): PlatformFamily {
  const normalizedUserAgent = userAgent.toLowerCase();
  const normalizedPlatform = platform.toLowerCase();
  const isTouchMac = normalizedPlatform.includes("macintel") && maxTouchPoints > 0;

  if (
    isTouchMac ||
    /android|iphone|ipad|ipod|mobile/.test(normalizedUserAgent)
  ) {
    return "mobile";
  }
  if (/macintosh|mac os|macos/.test(normalizedUserAgent) || normalizedPlatform.includes("mac")) {
    return "macos";
  }
  if (/windows/.test(normalizedUserAgent) || normalizedPlatform.includes("win")) {
    return "windows";
  }
  if (/linux|x11/.test(normalizedUserAgent) || normalizedPlatform.includes("linux")) {
    return "linux";
  }
  return "other";
}
