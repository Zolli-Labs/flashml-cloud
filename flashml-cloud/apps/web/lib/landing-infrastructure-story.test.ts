import { existsSync, readFileSync, readdirSync } from "node:fs";
import { act, createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Hero } from "@/components/landing/Hero";
import { HeroComputeFabric } from "@/components/landing/HeroComputeFabric";
import { PlatformSupport } from "@/components/landing/PlatformSupport";
import { SystemJourney } from "@/components/landing/SystemJourney";
import { ARCHITECTURE_SIGNALS } from "@/components/landing/ArchitectureSignal";
import {
  WORKFLOW_SCENES,
  WorkflowScene,
} from "@/components/landing/WorkflowScene";
import { WORKLOADS } from "@/lib/landing/workloads";
import { isPublicPath } from "@/middleware";
import {
  getFabricCanvasConfig,
  getFabricFocusTransition,
  getFabricRuntimeMode,
} from "./hero-fabric";
import {
  LandingMotionProvider,
  useLandingMotion,
} from "@/components/landing/motion/LandingMotionProvider";
import {
  HOST_SUPPORT,
  MACHINE_HINTS,
  RUNTIME_SUPPORT,
  inferPlatformFamily,
} from "./landing/platform";
import { WORKFLOW_EVENTS, WORKFLOW_STEPS } from "./landing/workflow";
import type { HeroSourceKey } from "./hero-story";

const root = process.cwd();
const source = (path: string) => readFileSync(`${root}/${path}`, "utf8");
const sourceIfPresent = (path: string) => existsSync(`${root}/${path}`) ? source(path) : "";
const sourceFilesUnder = (path: string): string[] => readdirSync(`${root}/${path}`, {
  withFileTypes: true,
}).flatMap((entry) => {
  const child = `${path}/${entry.name}`;
  if (entry.isDirectory()) return sourceFilesUnder(child);
  return /\.(?:css|ts|tsx)$/.test(entry.name) && !entry.name.includes(".test.")
    ? [child]
    : [];
});
const fabricHarness = vi.hoisted(() => ({
  motion: null as null | {
    reduced: boolean;
    desktop: boolean;
    finePointer: boolean;
    documentVisible: boolean;
  },
}));

function cssDeclarations(css: string, selector: string, fromIndex = 0) {
  const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`${escapedSelector}\\s*\\{`).exec(css.slice(fromIndex));
  const selectorIndex = match ? fromIndex + match.index : -1;
  if (selectorIndex < 0) return "";
  const open = css.indexOf("{", selectorIndex + selector.length);
  const close = open < 0 ? -1 : css.indexOf("}", open + 1);
  return open < 0 || close < 0 ? "" : css.slice(open + 1, close);
}

vi.mock("motion/react", async () => {
  const React = await vi.importActual<typeof import("react")>("react");
  const ignoredMotionProps = new Set([
    "animate", "exit", "initial", "layoutId", "transition", "variants", "viewport", "whileInView",
  ]);
  const motion = new Proxy({}, {
    get: (_target, tag: string) => React.forwardRef<HTMLElement, Record<string, unknown>>(
      function TestMotionElement(props, ref) {
        const domProps = Object.fromEntries(
          Object.entries(props).filter(([key]) => !ignoredMotionProps.has(key)),
        );
        if (domProps.style && typeof domProps.style === "object") {
          domProps.style = Object.fromEntries(
            Object.entries(domProps.style as Record<string, unknown>)
              .filter(([key]) => !["rotateX", "rotateY", "x", "y", "z"].includes(key)),
          );
        }
        return React.createElement(tag, { ...domProps, ref });
      },
    ),
  });
  const useMotionValue = (initial: number) => {
    let value = initial;
    return {
      get: () => value,
      getVelocity: () => 0,
      on: () => () => undefined,
      set: (next: number) => { value = next; },
    };
  };

  return {
    AnimatePresence: ({ children }: { children: ReactNode }) => children,
    motion,
    useMotionValue,
    useSpring: <T,>(value: T) => value,
  };
});

vi.mock("@gsap/react", () => ({ useGSAP: () => undefined }));
vi.mock("gsap", () => ({
  gsap: {
    killTweensOf: () => undefined,
    quickTo: () => () => undefined,
    registerPlugin: () => undefined,
    set: () => undefined,
    timeline: () => ({ from: () => ({ from: () => undefined }) }),
    utils: { clamp: (min: number, max: number, value: number) => Math.min(max, Math.max(min, value)) },
  },
}));

vi.mock("@/components/landing/motion/LandingMotionProvider", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("@/components/landing/motion/LandingMotionProvider")
  >();
  return {
    ...actual,
    useLandingMotion: () => fabricHarness.motion ?? actual.useLandingMotion(),
  };
});

vi.mock("next/dynamic", async () => {
  const React = await vi.importActual<typeof import("react")>("react");
  return {
    default: () => function FabricCanvasProbe(props: {
      selectedSource: string;
      focusedSource: HeroSourceKey | null;
      jobStep: string;
      storyPlaying: boolean;
      fabricEntranceComplete: boolean;
      reducedMotion: boolean;
      documentVisible: boolean;
      fabricDecision: null | { mode: string; quality?: string; reason?: string };
      onFabricEntranceComplete: () => void;
      onFabricFailure: () => void;
    }) {
      const decision = props.fabricDecision === null
        ? "pending"
        : `${props.fabricDecision.mode}:${props.fabricDecision.quality ?? props.fabricDecision.reason}`;
      const runtime = props.fabricDecision?.mode === "canvas"
        ? getFabricRuntimeMode({
            baseQuality: props.fabricDecision.quality as "high" | "balanced" | "static",
            storyPlaying: props.storyPlaying,
            focusedSource: props.focusedSource,
            entranceCompleted: props.fabricEntranceComplete,
            focusMotionAllowed: true,
            reducedMotion: props.reducedMotion,
            documentVisible: props.documentVisible,
          })
        : { quality: "static" as const, continuous: false, motionEnabled: false };
      const config = getFabricCanvasConfig({
        quality: runtime.quality,
        continuous: runtime.continuous,
        finePointer: true,
      });
      return React.createElement("div", {
        "data-fabric-canvas": "mock",
        "data-decision": decision,
        "data-job-step": props.jobStep,
        "data-selected-source": props.selectedSource,
        "data-focused-source": props.focusedSource ?? "overview",
        "data-story-playing": String(props.storyPlaying),
        "data-runtime-quality": runtime.quality,
        "data-runtime-loop": config.frameloop,
        "data-focus-transition": getFabricFocusTransition(
          runtime.quality,
          runtime.motionEnabled,
        ),
        onClick: props.onFabricFailure,
      }, React.createElement("button", {
        "data-complete-fabric-entrance": "true",
        onClick: (event: { stopPropagation: () => void }) => {
          event.stopPropagation();
          props.onFabricEntranceComplete();
        },
      }));
    },
  };
});

afterEach(() => {
  fabricHarness.motion = null;
  vi.useRealTimers();
});

function MotionProbe() {
  const state = useLandingMotion();

  return createElement("output", null, JSON.stringify(state));
}

type TestEvent = {
  type: string;
  target?: TestElement;
  currentTarget?: TestEventTarget | null;
  bubbles?: boolean;
  defaultPrevented?: boolean;
  propagationStopped?: boolean;
  key?: string;
  button?: number;
  preventDefault?: () => void;
  stopPropagation?: () => void;
};

type Listener = (event: TestEvent) => void;

class TestEventTarget {
  readonly listeners = new Map<string, Set<Listener>>();

  addEventListener(type: string, listener: Listener) {
    const listeners = this.listeners.get(type) ?? new Set<Listener>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: string, listener: Listener) {
    this.listeners.get(type)?.delete(listener);
  }

  dispatchEvent(event: TestEvent) {
    event.currentTarget = this;
    for (const listener of this.listeners.get(event.type) ?? []) listener(event);
    return true;
  }
}

class TestTextNode {
  readonly nodeType = 3;
  parentNode: TestElement | null = null;

  constructor(public data: string) {}

  get textContent() {
    return this.data;
  }

  set textContent(value: string) {
    this.data = value;
  }
}

class TestStyle {
  setProperty(name: string, value: string) {
    Object.assign(this, { [name]: value });
  }

  removeProperty(name: string) {
    Reflect.deleteProperty(this, name);
  }
}

class TestElement extends TestEventTarget {
  readonly nodeType = 1;
  readonly nodeName: string;
  readonly tagName: string;
  readonly namespaceURI = "http://www.w3.org/1999/xhtml";
  readonly style = new TestStyle();
  readonly attributes = new Map<string, string>();
  readonly childNodes: Array<TestElement | TestTextNode> = [];
  parentNode: TestElement | null = null;
  ownerDocument!: TestDocument;

  constructor(tagName: string) {
    super();
    this.nodeName = tagName.toUpperCase();
    this.tagName = this.nodeName;
  }

  get firstChild() {
    return this.childNodes[0] ?? null;
  }

  get textContent() {
    return this.childNodes.map((node) => node.textContent).join("");
  }

  set textContent(value: string) {
    this.childNodes.splice(0, this.childNodes.length);
    if (value) this.appendChild(this.ownerDocument.createTextNode(value));
  }

  appendChild(node: TestElement | TestTextNode) {
    node.parentNode = this;
    this.childNodes.push(node);
    return node;
  }

  insertBefore(node: TestElement | TestTextNode, before: TestElement | TestTextNode | null) {
    node.parentNode = this;
    const index = before ? this.childNodes.indexOf(before) : -1;
    if (index < 0) this.childNodes.push(node);
    else this.childNodes.splice(index, 0, node);
    return node;
  }

  removeChild(node: TestElement | TestTextNode) {
    const index = this.childNodes.indexOf(node);
    if (index >= 0) this.childNodes.splice(index, 1);
    node.parentNode = null;
    return node;
  }

  setAttribute(name: string, value: string) {
    this.attributes.set(name, value);
  }

  removeAttribute(name: string) {
    this.attributes.delete(name);
  }

  getAttribute(name: string) {
    return this.attributes.get(name) ?? null;
  }

  hasAttribute(name: string) {
    return this.attributes.has(name);
  }

  contains(node: TestElement | TestTextNode | null): boolean {
    if (!node) return false;
    if (node === this) return true;
    return this.childNodes.some((child) =>
      child instanceof TestElement && child.contains(node),
    );
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }

  getContext(contextId: string) {
    return this.tagName === "CANVAS"
      && contextId === "webgl2"
      && this.ownerDocument.webgl2Supported
      ? {}
      : null;
  }

  click() {
    this.dispatchEvent({
      type: "click",
      target: this,
      bubbles: true,
      button: 0,
      preventDefault() {
        this.defaultPrevented = true;
      },
      stopPropagation() {
        this.propagationStopped = true;
      },
    });
  }

  override dispatchEvent(event: TestEvent) {
    event.target ??= this;
    super.dispatchEvent(event);
    if (event.bubbles !== false && !event.propagationStopped) {
      this.parentNode?.dispatchEvent(event);
    }
    return !event.defaultPrevented;
  }
}

class TestDocument extends TestEventTarget {
  readonly nodeType = 9;
  readonly nodeName = "#document";
  readonly documentElement: TestElement;
  readonly body: TestElement;
  visibilityState: "visible" | "hidden" = "visible";
  defaultView: typeof globalThis.window | undefined;
  activeElement: TestElement | null = null;

  constructor(public webgl2Supported = true) {
    super();
    this.documentElement = this.createElement("html");
    this.body = this.createElement("body");
    this.documentElement.appendChild(this.body);
  }

  createElement(tagName: string) {
    const element = new TestElement(tagName);
    element.ownerDocument = this;
    return element;
  }

  createElementNS(_namespace: string, tagName: string) {
    return this.createElement(tagName);
  }

  createTextNode(value: string) {
    return new TestTextNode(value);
  }
}

class TestMediaQueryList extends TestEventTarget {
  constructor(public matches: boolean) {
    super();
  }

  setMatches(matches: boolean) {
    this.matches = matches;
    this.dispatchEvent({ type: "change" });
  }
}

function installMotionEnvironment({ webgl2 = true }: { webgl2?: boolean } = {}) {
  const document = new TestDocument(webgl2);
  const animationFrames = new Map<number, FrameRequestCallback>();
  let nextAnimationFrame = 1;
  const queries = new Map<string, TestMediaQueryList>([
    ["(prefers-reduced-motion: reduce)", new TestMediaQueryList(false)],
    ["(min-width: 1024px)", new TestMediaQueryList(true)],
    ["(pointer: fine)", new TestMediaQueryList(true)],
  ]);
  const window = Object.assign(new TestEventTarget(), {
    document,
    matchMedia(query: string) {
      return queries.get(query) ?? new TestMediaQueryList(false);
    },
    HTMLElement: TestElement,
    HTMLIFrameElement: TestElement,
    Node: TestElement,
    Element: TestElement,
    SVGElement: TestElement,
    getComputedStyle: () => ({ display: "block" }),
    requestAnimationFrame(callback: FrameRequestCallback) {
      const handle = nextAnimationFrame++;
      animationFrames.set(handle, callback);
      return handle;
    },
    cancelAnimationFrame(handle: number) {
      animationFrames.delete(handle);
    },
    setTimeout(handler: TimerHandler, timeout?: number) {
      return globalThis.setTimeout(handler, timeout);
    },
    clearTimeout(handle: number) {
      globalThis.clearTimeout(handle);
    },
  });
  document.defaultView = window as never;

  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  Object.assign(globalThis, { window, document });

  return {
    container: document.createElement("div"),
    document,
    queries,
    pendingAnimationFrames: () => animationFrames.size,
    runNextAnimationFrame() {
      const entry = animationFrames.entries().next().value as
        | [number, FrameRequestCallback]
        | undefined;
      if (!entry) return false;
      animationFrames.delete(entry[0]);
      entry[1](0);
      return true;
    },
    runAnimationFrames() {
      for (const [handle, callback] of [...animationFrames]) {
        animationFrames.delete(handle);
        callback(0);
      }
    },
    restore() {
      Object.assign(globalThis, { window: previousWindow, document: previousDocument });
    },
  };
}

function findElements(
  root: TestElement,
  predicate: (element: TestElement) => boolean,
): TestElement[] {
  const matches: TestElement[] = [];
  const visit = (element: TestElement) => {
    if (predicate(element)) matches.push(element);
    for (const child of element.childNodes) {
      if (child instanceof TestElement) visit(child);
    }
  };
  visit(root);
  return matches;
}

function elementsByAttribute(root: TestElement, name: string, value?: string) {
  return findElements(root, (element) =>
    element.hasAttribute(name) && (value === undefined || element.getAttribute(name) === value),
  );
}

function findButtonWithText(root: TestElement, text: string): TestElement | undefined {
  return findElements(root, (element) => element.tagName === "BUTTON").find(
    (element) => element.textContent.trim() === text,
  );
}

function findButtonContainingText(root: TestElement, text: string): TestElement | undefined {
  return findElements(root, (element) => element.tagName === "BUTTON").find(
    (element) => element.textContent.includes(text),
  );
}

const ACTIVE_MOTION = {
  reduced: false,
  desktop: true,
  finePointer: true,
  documentVisible: true,
} as const;

async function mountProductionFabric({ webgl2 = true }: { webgl2?: boolean } = {}) {
  vi.useFakeTimers();
  const environment = installMotionEnvironment({ webgl2 });
  fabricHarness.motion = { ...ACTIVE_MOTION };
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
    .IS_REACT_ACT_ENVIRONMENT = true;
  const { createRoot } = await import("react-dom/client");
  const reactRoot = createRoot(environment.container as never);

  await act(async () => reactRoot.render(createElement(HeroComputeFabric)));

  return {
    ...environment,
    probe: () => elementsByAttribute(environment.container, "data-fabric-canvas", "mock")[0],
    completeEntrance: () => elementsByAttribute(
      environment.container,
      "data-complete-fabric-entrance",
      "true",
    )[0]?.click(),
    async renderMotion(motion: typeof ACTIVE_MOTION | {
      reduced: boolean;
      desktop: boolean;
      finePointer: boolean;
      documentVisible: boolean;
    }) {
      fabricHarness.motion = { ...motion };
      await act(async () => reactRoot.render(createElement(HeroComputeFabric)));
    },
    async cleanup() {
      await act(async () => reactRoot.unmount());
      environment.restore();
    },
  };
}

type ManualInteractionCase = {
  name: string;
  interact: (container: TestElement) => void;
  expectedSource: string;
  expectedFocusedSource: string;
  expectedStep: string;
  expectedPlaying: string;
};

const MANUAL_INTERACTION_CASES: readonly ManualInteractionCase[] = [
  {
    name: "source selection",
    interact(container) {
      const button = findButtonWithText(container, "Owned");
      expect(button).toBeDefined();
      button?.click();
    },
    expectedSource: "owned",
    expectedFocusedSource: "owned",
    expectedStep: "accepted",
    expectedPlaying: "false",
  },
  {
    name: "explicit story-step selection",
    interact(container) {
      const button = findButtonContainingText(container, "Node lost");
      expect(button).toBeDefined();
      button?.click();
    },
    expectedSource: "everyday",
    expectedFocusedSource: "overview",
    expectedStep: "lost",
    expectedPlaying: "false",
  },
  {
    name: "Play/Pause",
    interact(container) {
      const button = findButtonWithText(container, "Play story");
      expect(button).toBeDefined();
      button?.click();
    },
    expectedSource: "everyday",
    expectedFocusedSource: "overview",
    expectedStep: "accepted",
    expectedPlaying: "true",
  },
] as const;

function expectManualState(
  probe: TestElement,
  interaction: ManualInteractionCase,
) {
  expect(probe.getAttribute("data-selected-source")).toBe(interaction.expectedSource);
  expect(probe.getAttribute("data-focused-source")).toBe(interaction.expectedFocusedSource);
  expect(probe.getAttribute("data-job-step")).toBe(interaction.expectedStep);
  expect(probe.getAttribute("data-story-playing")).toBe(interaction.expectedPlaying);
}

function installNavigator(overrides: {
  userAgent: string;
  platform?: string;
  maxTouchPoints?: number;
}) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: {
      userAgent: overrides.userAgent,
      platform: overrides.platform ?? "",
      maxTouchPoints: overrides.maxTouchPoints ?? 0,
    },
  });

  return {
    restore() {
      if (previous) Object.defineProperty(globalThis, "navigator", previous);
    },
  };
}

describe("the infrastructure story", () => {
  it("uses curated runtime labels and registered images", () => {
    expect(RUNTIME_SUPPORT.map(({ label }) => label)).toEqual([
      "Python 3.11", "NumPy", "pandas", "scikit-learn", "SciPy",
      "PyTorch CPU", "PyTorch CUDA 12.4", "Docker", "GitHub",
    ]);
    expect(RUNTIME_SUPPORT.flatMap((runtime) => (
      "imageAlias" in runtime ? runtime.imageAlias : []
    ))).toEqual([
      "python-slim", "sklearn", "pytorch-cpu", "pytorch-cuda",
    ]);
  });

  it("qualifies every host state", () => {
    expect(HOST_SUPPORT.map(({ platform, state }) => [platform, state])).toEqual([
      ["macOS arm64", "Proven"],
      ["Linux x86_64", "Proven"],
      ["Windows 11", "Preview"],
    ]);
  });

  it("maps browser signals without claiming hardware verification", () => {
    expect(inferPlatformFamily({ userAgent: "iPhone", platform: "MacIntel", maxTouchPoints: 5 })).toBe("mobile");
    expect(inferPlatformFamily({ userAgent: "Macintosh", platform: "MacIntel", maxTouchPoints: 0 })).toBe("macos");
    expect(inferPlatformFamily({ userAgent: "X11; Linux", platform: "Linux x86_64", maxTouchPoints: 0 })).toBe("linux");
    expect(inferPlatformFamily({ userAgent: "Windows NT 10.0", platform: "Win32", maxTouchPoints: 0 })).toBe("windows");
    expect(Object.values(MACHINE_HINTS).every(({ body, nextStep }) =>
      !body.includes("Your machine is supported") &&
      nextStep === "Run flashnode doctor for a real host check."
    )).toBe(true);
  });

  it("uses seven scenes and five recovery events", () => {
    expect(WORKFLOW_STEPS.map(({ id }) => id)).toEqual([
      "connect", "register", "submit", "parallel", "checkpoint", "recover", "accept",
    ]);
    expect(WORKFLOW_EVENTS).toEqual([
      "LEASE_CLAIMED",
      "CHECKPOINT_MANIFEST_COMMITTED",
      "NODE_HEARTBEAT_LOST",
      "TASK_REQUEUED",
      "TASK_COMMIT_ACCEPTED",
    ]);
  });

  it("describes operator-chosen capacity without a compatibility claim", () => {
    const connectStep = WORKFLOW_STEPS.find(({ id }) => id === "connect");

    expect(connectStep?.body).toContain("capacity you choose to add");
    expect(connectStep?.body).not.toContain("compatible");
  });

  it("keeps landing motion conservative before client capabilities are known", () => {
    const markup = renderToStaticMarkup(
      createElement(LandingMotionProvider, null, createElement(MotionProbe)),
    );

    expect(markup).toContain(
      '{&quot;reduced&quot;:true,&quot;desktop&quot;:false,&quot;finePointer&quot;:false,&quot;documentVisible&quot;:true}',
    );
  });

  it("enables landing motion only after mounted browser capabilities allow it", async () => {
    const environment = installMotionEnvironment();
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
      .IS_REACT_ACT_ENVIRONMENT = true;
    const { createRoot } = await import("react-dom/client");
    const root = createRoot(environment.container as never);

    await act(async () => {
      root.render(createElement(LandingMotionProvider, null, createElement(MotionProbe)));
    });

    expect(environment.container.textContent).toBe(
      '{"reduced":false,"desktop":true,"finePointer":true,"documentVisible":true}',
    );

    await act(async () => root.unmount());
    environment.restore();
  });

  it("reacts to capability and visibility changes while the landing is mounted", async () => {
    const environment = installMotionEnvironment();
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
      .IS_REACT_ACT_ENVIRONMENT = true;
    const { createRoot } = await import("react-dom/client");
    const root = createRoot(environment.container as never);

    await act(async () => {
      root.render(createElement(LandingMotionProvider, null, createElement(MotionProbe)));
    });
    await act(async () => {
      environment.queries.get("(prefers-reduced-motion: reduce)")?.setMatches(true);
      environment.queries.get("(min-width: 1024px)")?.setMatches(false);
      environment.queries.get("(pointer: fine)")?.setMatches(false);
      environment.document.visibilityState = "hidden";
      environment.document.dispatchEvent({ type: "visibilitychange" });
    });

    expect(environment.container.textContent).toBe(
      '{"reduced":true,"desktop":false,"finePointer":false,"documentVisible":false}',
    );

    await act(async () => root.unmount());
    expect(environment.document.listeners.get("visibilitychange")?.size).toBe(0);
    expect(environment.queries.get("(prefers-reduced-motion: reduce)")?.listeners.get("change")?.size).toBe(0);
    expect(environment.queries.get("(min-width: 1024px)")?.listeners.get("change")?.size).toBe(0);
    expect(environment.queries.get("(pointer: fine)")?.listeners.get("change")?.size).toBe(0);
    environment.restore();
  });

  it("renders the approved hero definition and action hierarchy", () => {
    const markup = renderToStaticMarkup(createElement(Hero));
    const renderedText = markup.replace(/<[^>]+>/g, "").replace(/\s+/g, " ");
    const definition = "Zolli unifies compatible cloud capacity, rented compute, owned GPU infrastructure, and everyday machines under one control plane, then recovers work when a node disappears.";

    expect(renderedText).toContain("Compute that finishes the job.");
    expect(renderedText).toContain(definition);
    expect(markup.indexOf("Open console")).toBeLessThan(markup.indexOf("Talk to Zolli"));
  });

  it("promotes the production compute fabric without changing the hero message", () => {
    const hero = source("components/landing/Hero.tsx");
    const fabricPath = "components/landing/HeroComputeFabric.tsx";
    const fabric = sourceIfPresent(fabricPath);

    expect(existsSync(`${root}/${fabricPath}`)).toBe(true);
    expect(hero).toContain('import { HeroComputeFabric } from "@/components/landing/HeroComputeFabric"');
    expect(hero).toContain("<HeroComputeFabric />");
    expect(hero).not.toContain("HeroInfrastructureStack");
    expect(hero.indexOf("Compute that ")).toBeLessThan(hero.indexOf("finishes the job."));
    expect(hero.indexOf("Open console")).toBeLessThan(hero.indexOf("Talk to Zolli"));
    expect(fabric).toContain('data-hero-fabric="production"');
  });

  it("removes the temporary hero experiment after promoting the compute fabric", () => {
    const removedPaths = [
      "app/(marketing)/hero-lab/page.tsx",
      "components/hero-lab",
      "components/landing/HeroInfrastructureStack.tsx",
      "lib/hero-lab.ts",
      "lib/hero-lab.test.ts",
    ];
    expect(removedPaths.map((path) => existsSync(`${root}/${path}`))).toEqual([
      false,
      false,
      false,
      false,
      false,
    ]);

    const productionPaths = [
      "middleware.ts",
      ...sourceFilesUnder("app"),
      ...sourceFilesUnder("components"),
      ...sourceFilesUnder("lib"),
    ];
    const productionSource = productionPaths.map(source).join("\n");

    expect(isPublicPath("/hero-lab")).toBe(false);
    expect(productionSource).not.toMatch(
      /@\/components\/hero-lab|@\/lib\/hero-lab|\bHeroInfrastructureStack\b|\b(?:HeroLab(?:Canvas)?|StackScene|TopologyScene|BackplaneScene)\b|HERO_LAB_|data-hero-lab/,
    );
    expect(source("app/globals.css")).not.toContain("hero-infra-");
  });

  it("keeps temporary comparison language out of the production shell", () => {
    const fabric = sourceIfPresent("components/landing/HeroComputeFabric.tsx");

    for (const temporaryLabel of ["HeroLab", "hero-lab", "CONCEPT", "STRENGTH", "WEAKNESS"]) {
      expect(fabric).not.toContain(temporaryLabel);
    }
    expect(fabric).not.toMatch(/\bB2\b/);
  });

  it("keeps the production fabric readable from desktop through mobile", () => {
    const styles = sourceIfPresent("components/landing/HeroComputeFabric.module.css");
    const compactDesktop = styles.indexOf(
      "@media (min-width: 821px) and (max-width: 1100px)",
    );
    const tablet = styles.indexOf("@media (max-width: 820px)");
    const mobile = styles.indexOf("@media (max-width: 540px)");

    expect(cssDeclarations(styles, ".canvasWrap")).toContain("min-height: 30rem");
    expect(compactDesktop).toBeGreaterThan(-1);
    expect(cssDeclarations(styles, ".sceneFrame", compactDesktop)).toContain(
      "height: 30rem",
    );
    expect(cssDeclarations(styles, ".canvasWrap", tablet)).toContain("min-height: 27rem");
    expect(cssDeclarations(styles, ".canvasWrap", mobile)).toContain("min-height: 22rem");
    expect(cssDeclarations(styles, ".sourceButton")).toContain("min-height: 2.75rem");
    expect(cssDeclarations(styles, ".storyHeader button")).toContain("min-height: 2.5rem");
    expect(cssDeclarations(styles, ".jobRail button")).toContain("min-height: 3.75rem");
    expect(cssDeclarations(styles, ".canvasHeading,\n.controlCaption")).toContain(
      "pointer-events: none",
    );
    expect(cssDeclarations(styles, ".sourceButtons", mobile)).toContain(
      "grid-template-columns: repeat(2, minmax(0, 1fr))",
    );
    expect(cssDeclarations(styles, ".jobRail", mobile)).toContain(
      "grid-template-columns: repeat(2, minmax(0, 1fr))",
    );
    const canvasRule = cssDeclarations(styles, ".canvasWrap canvas");
    expect(canvasRule).toContain("touch-action: pan-y");
    expect(canvasRule).not.toContain("touch-action: none");
  });

  it("waits for a canvas capability decision before starting the story", async () => {
    const mounted = await mountProductionFabric();
    try {
      expect(mounted.probe().getAttribute("data-decision")).toBe("pending");
      expect(mounted.probe().getAttribute("data-story-playing")).toBe("false");

      await act(async () => { mounted.runNextAnimationFrame(); });
      expect(mounted.probe().getAttribute("data-decision")).toBe("canvas:high");
      expect(mounted.probe().getAttribute("data-story-playing")).toBe("false");

      await act(async () => { mounted.runAnimationFrames(); });
      expect(mounted.probe().getAttribute("data-story-playing")).toBe("true");
      expect(mounted.probe().getAttribute("data-job-step")).toBe("submitted");
    } finally {
      await mounted.cleanup();
    }
  });

  it("never autoplays when capability detection selects the poster fallback", async () => {
    const mounted = await mountProductionFabric({ webgl2: false });
    try {
      await act(async () => { mounted.runNextAnimationFrame(); });
      expect(mounted.probe().getAttribute("data-decision")).toBe(
        "poster:webgl2-unavailable",
      );

      await act(async () => { mounted.runAnimationFrames(); });
      expect(mounted.probe().getAttribute("data-story-playing")).toBe("false");
      expect(mounted.probe().getAttribute("data-job-step")).toBe("accepted");
      const playButton = findButtonWithText(mounted.container, "Play story");
      expect(playButton).toBeDefined();
      expect(playButton?.hasAttribute("disabled")).toBe(true);
      expect(playButton?.getAttribute("aria-disabled")).toBe("true");

      await act(async () => playButton?.click());
      expect(mounted.probe().getAttribute("data-story-playing")).toBe("false");
      await act(async () => findButtonWithText(mounted.container, "Owned")?.click());
      expect(mounted.probe().getAttribute("data-selected-source")).toBe("owned");
      await act(async () => findButtonContainingText(mounted.container, "Node lost")?.click());
      expect(mounted.probe().getAttribute("data-job-step")).toBe("lost");
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      await mounted.cleanup();
    }
  });

  it("stops canvas autoplay after a real failure callback and keeps manual inspection usable", async () => {
    const mounted = await mountProductionFabric();
    try {
      await act(async () => { mounted.runNextAnimationFrame(); });
      await act(async () => { mounted.runAnimationFrames(); });
      expect(mounted.probe().getAttribute("data-decision")).toBe("canvas:high");
      expect(mounted.probe().getAttribute("data-job-step")).toBe("submitted");
      expect(mounted.probe().getAttribute("data-story-playing")).toBe("true");
      expect(vi.getTimerCount()).toBe(1);

      await act(async () => mounted.probe().click());
      expect(mounted.probe().getAttribute("data-story-playing")).toBe("false");
      expect(mounted.probe().getAttribute("data-job-step")).toBe("submitted");
      expect(vi.getTimerCount()).toBe(0);

      await act(async () => { vi.advanceTimersByTime(5_000); });
      expect(mounted.probe().getAttribute("data-job-step")).toBe("submitted");

      const playButton = findButtonWithText(mounted.container, "Play story");
      expect(playButton).toBeDefined();
      expect(playButton?.hasAttribute("disabled")).toBe(true);
      expect(playButton?.getAttribute("aria-disabled")).toBe("true");
      await act(async () => playButton?.click());
      expect(mounted.probe().getAttribute("data-story-playing")).toBe("false");

      await act(async () => findButtonWithText(mounted.container, "Rented GPU")?.click());
      expect(mounted.probe().getAttribute("data-selected-source")).toBe("rented");
      expect(mounted.probe().getAttribute("data-focused-source")).toBe("rented");
      await act(async () => findButtonContainingText(mounted.container, "Node lost")?.click());
      expect(mounted.probe().getAttribute("data-job-step")).toBe("lost");
      expect(mounted.probe().getAttribute("data-focused-source")).toBe("overview");
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      await mounted.cleanup();
    }
  });

  it("focuses selected sources and returns to overview for story controls", async () => {
    const mounted = await mountProductionFabric();
    try {
      await act(async () => { mounted.runNextAnimationFrame(); });
      await act(async () => { mounted.runAnimationFrames(); });

      const probe = mounted.probe();
      expect(probe.getAttribute("data-focused-source")).toBe("overview");
      await act(async () => findButtonWithText(mounted.container, "Owned")?.click());
      expect(probe.getAttribute("data-focused-source")).toBe("owned");
      expect(probe.getAttribute("data-story-playing")).toBe("false");
      await act(async () => findButtonContainingText(mounted.container, "Node lost")?.click());
      expect(probe.getAttribute("data-focused-source")).toBe("overview");
      await act(async () => findButtonWithText(mounted.container, "Rented GPU")?.click());
      expect(probe.getAttribute("data-focused-source")).toBe("rented");
      await act(async () => findButtonWithText(mounted.container, "Play story")?.click());
      expect(probe.getAttribute("data-focused-source")).toBe("overview");
    } finally {
      await mounted.cleanup();
    }
  });

  it("keeps High rendering while a source pauses autoplay and damps on demand", async () => {
    const mounted = await mountProductionFabric();
    try {
      await act(async () => { mounted.runNextAnimationFrame(); });
      expect(mounted.probe().getAttribute("data-runtime-quality")).toBe("high");
      expect(mounted.probe().getAttribute("data-runtime-loop")).toBe("always");

      await act(async () => { mounted.runAnimationFrames(); });
      await act(async () => mounted.completeEntrance());
      await act(async () => findButtonWithText(mounted.container, "Owned")?.click());

      expect(mounted.probe().getAttribute("data-story-playing")).toBe("false");
      expect(mounted.probe().getAttribute("data-focused-source")).toBe("owned");
      expect(mounted.probe().getAttribute("data-runtime-quality")).toBe("high");
      expect(mounted.probe().getAttribute("data-runtime-loop")).toBe("demand");
      expect(mounted.probe().getAttribute("data-focus-transition")).toBe("damp");
    } finally {
      await mounted.cleanup();
    }
  });

  it.each(MANUAL_INTERACTION_CASES)(
    "preserves $name used before capability detection",
    async (interaction) => {
      const mounted = await mountProductionFabric();
      try {
        expect(mounted.probe().getAttribute("data-decision")).toBe("pending");
        await act(async () => interaction.interact(mounted.container));
        expectManualState(mounted.probe(), interaction);

        await act(async () => { mounted.runAnimationFrames(); });
        expect(mounted.probe().getAttribute("data-decision")).toBe("canvas:high");
        await act(async () => { mounted.runAnimationFrames(); });
        expectManualState(mounted.probe(), interaction);
      } finally {
        await mounted.cleanup();
      }
    },
  );

  it.each(MANUAL_INTERACTION_CASES)(
    "preserves $name used after capability detection but before initialization",
    async (interaction) => {
      const mounted = await mountProductionFabric();
      try {
        await act(async () => { mounted.runNextAnimationFrame(); });
        expect(mounted.probe().getAttribute("data-decision")).toBe("canvas:high");
        expect(mounted.probe().getAttribute("data-story-playing")).toBe("false");

        await act(async () => interaction.interact(mounted.container));
        expectManualState(mounted.probe(), interaction);
        await act(async () => { mounted.runAnimationFrames(); });
        expectManualState(mounted.probe(), interaction);
      } finally {
        await mounted.cleanup();
      }
    },
  );

  it("cleans story timers and gates advancement while hidden or reduced", async () => {
    const mounted = await mountProductionFabric();
    try {
      await act(async () => { mounted.runNextAnimationFrame(); });
      await act(async () => { mounted.runAnimationFrames(); });
      expect(vi.getTimerCount()).toBe(1);

      await mounted.renderMotion({ ...ACTIVE_MOTION, documentVisible: false });
      expect(vi.getTimerCount()).toBe(0);
      await act(async () => { vi.advanceTimersByTime(5_000); });
      expect(mounted.probe().getAttribute("data-job-step")).toBe("submitted");

      await mounted.renderMotion({ ...ACTIVE_MOTION });
      expect(vi.getTimerCount()).toBe(1);
      await mounted.renderMotion({ ...ACTIVE_MOTION, reduced: true });
      expect(vi.getTimerCount()).toBe(0);
      await act(async () => { mounted.runAnimationFrames(); });
      expect(mounted.probe().getAttribute("data-story-playing")).toBe("false");
      expect(findButtonWithText(mounted.container, "Static story")).toBeDefined();

      await mounted.renderMotion({ ...ACTIVE_MOTION });
      findButtonWithText(mounted.container, "Play story")?.click();
      await act(async () => undefined);
      expect(vi.getTimerCount()).toBe(1);
    } finally {
      await mounted.cleanup();
      expect(vi.getTimerCount()).toBe(0);
      expect(mounted.pendingAnimationFrames()).toBe(0);
    }
  });

  it("renders native controls and pauses on source or step selection before resuming in place", async () => {
    const mounted = await mountProductionFabric();
    try {
      await act(async () => { mounted.runNextAnimationFrame(); });
      await act(async () => { mounted.runAnimationFrames(); });

      const sourceButtons = ["Cloud / HPC", "Rented GPU", "Owned", "Everyday"]
        .map((label) => findButtonWithText(mounted.container, label));
      const storyButtons = [
        "Job submitted",
        "Zolli assigns",
        "Checkpoint retained",
        "Node lost",
        "Resumed elsewhere",
        "Result accepted",
      ].map((label) => findButtonContainingText(mounted.container, label));
      expect(sourceButtons.every((button) => button?.tagName === "BUTTON")).toBe(true);
      expect(storyButtons.every((button) => button?.tagName === "BUTTON")).toBe(true);
      expect(elementsByAttribute(mounted.container, "aria-live", "polite")).toHaveLength(2);
      expect(findButtonWithText(mounted.container, "Pause story")?.getAttribute("aria-pressed"))
        .toBe("true");

      await act(async () => sourceButtons[2]?.click());
      expect(sourceButtons[2]?.getAttribute("aria-pressed")).toBe("true");
      expect(sourceButtons[3]?.getAttribute("aria-pressed")).toBe("false");
      expect(mounted.probe().getAttribute("data-selected-source")).toBe("owned");
      expect(mounted.probe().getAttribute("data-story-playing")).toBe("false");

      await act(async () => findButtonWithText(mounted.container, "Play story")?.click());
      expect(mounted.probe().getAttribute("data-story-playing")).toBe("true");
      expect(mounted.probe().getAttribute("data-job-step")).toBe("submitted");

      await act(async () => storyButtons[3]?.click());
      expect(storyButtons[3]?.getAttribute("aria-current")).toBe("step");
      expect(mounted.probe().getAttribute("data-story-playing")).toBe("false");
      expect(mounted.probe().getAttribute("data-job-step")).toBe("lost");

      await act(async () => findButtonWithText(mounted.container, "Play story")?.click());
      expect(mounted.probe().getAttribute("data-story-playing")).toBe("true");
      expect(mounted.probe().getAttribute("data-job-step")).toBe("lost");
    } finally {
      await mounted.cleanup();
    }
  });
});

describe("the platform support section", () => {
  it("renders nine labeled runtime buttons beside one stable detail panel", () => {
    const markup = renderToStaticMarkup(createElement(PlatformSupport));
    const buttons = [
      ...markup.matchAll(/<button[^>]*data-runtime-button="[^"]*"[^>]*>([\s\S]*?)<\/button>/g),
    ];

    expect(buttons).toHaveLength(9);
    expect(
      buttons.map(([, inner]) => inner.replace(/<[^>]+>/g, "").trim()),
    ).toEqual(RUNTIME_SUPPORT.map(({ label }) => label));
    expect((markup.match(/data-runtime-detail\b/g) ?? [])).toHaveLength(1);
  });

  it("keeps every runtime icon decorative beside a real text label", () => {
    const markup = renderToStaticMarkup(createElement(PlatformSupport));
    const buttons = [
      ...markup.matchAll(/<button[^>]*data-runtime-button="[^"]*"[^>]*>([\s\S]*?)<\/button>/g),
    ];

    for (const [, inner] of buttons) {
      expect(inner).toContain("<svg");
      expect(inner).toContain('aria-hidden="true"');
      expect(inner.replace(/<[^>]+>/g, "").trim().length).toBeGreaterThan(0);
    }
  });

  it("shows a registered image alias only when the selected runtime has one", async () => {
    const environment = installMotionEnvironment();
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
      .IS_REACT_ACT_ENVIRONMENT = true;
    const { createRoot } = await import("react-dom/client");
    const root = createRoot(environment.container as never);

    await act(async () => root.render(createElement(PlatformSupport)));

    const numpyRuntime = RUNTIME_SUPPORT.find(({ label }) => label === "NumPy");
    const numpyButton = findButtonWithText(environment.container, numpyRuntime?.label ?? "");
    expect(numpyButton).toBeDefined();
    await act(async () => numpyButton?.click());
    const detailAfterNumpy = elementsByAttribute(environment.container, "data-runtime-detail");
    expect(detailAfterNumpy).toHaveLength(1);
    expect(detailAfterNumpy[0].textContent).not.toContain("python-slim");
    expect(detailAfterNumpy[0].textContent).toContain(
      "No curated image is registered for this runtime.",
    );
    expect(detailAfterNumpy[0].textContent).not.toContain(
      "Available on every proven and preview host",
    );

    const sklearnRuntime = RUNTIME_SUPPORT.find(({ label }) => label === "scikit-learn");
    const sklearnButton = findButtonWithText(environment.container, sklearnRuntime?.label ?? "");
    await act(async () => sklearnButton?.click());
    const detailAfterSklearn = elementsByAttribute(environment.container, "data-runtime-detail");
    expect(detailAfterSklearn).toHaveLength(1);
    expect(detailAfterSklearn[0]).toBe(detailAfterNumpy[0]);
    expect(detailAfterSklearn[0].textContent).toContain("sklearn");

    await act(async () => root.unmount());
    environment.restore();
  });

  it("renders three host cards with visible Proven or Preview labels", () => {
    const markup = renderToStaticMarkup(createElement(PlatformSupport));

    expect((markup.match(/data-host-card="[^"]*"/g) ?? [])).toHaveLength(3);
    for (const { platform, state } of HOST_SUPPORT) {
      const card = markup.match(
        new RegExp(`<article[^>]*data-host-card="${platform}"[^>]*>([\\s\\S]*?)</article>`),
      )?.[1] ?? "";
      const visible = card.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
      expect(visible, platform).toContain(platform);
      expect(visible, platform).toContain(state);
    }
  });

  it("keeps the machine result out of the DOM until the check is requested", () => {
    const markup = renderToStaticMarkup(createElement(PlatformSupport));

    expect(markup).not.toContain("data-machine-result");
    expect(markup).toContain("Check this browser");
  });

  it("reads navigator only after Check this browser is clicked, then announces a polite host-family hint", async () => {
    const environment = installMotionEnvironment();
    const navigator = installNavigator({
      userAgent: "Macintosh; Intel Mac OS X 10_15",
      platform: "MacIntel",
      maxTouchPoints: 0,
    });
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
      .IS_REACT_ACT_ENVIRONMENT = true;
    const { createRoot } = await import("react-dom/client");
    const root = createRoot(environment.container as never);

    await act(async () => root.render(createElement(PlatformSupport)));
    expect(elementsByAttribute(environment.container, "data-machine-result")).toHaveLength(0);

    const checkButton = findButtonWithText(environment.container, "Check this browser");
    expect(checkButton).toBeDefined();
    await act(async () => checkButton?.click());

    const results = elementsByAttribute(environment.container, "data-machine-result");
    expect(results).toHaveLength(1);
    expect(results[0].getAttribute("role")).toBe("status");
    expect(results[0].getAttribute("aria-live")).toBe("polite");
    expect(results[0].textContent).toContain(MACHINE_HINTS.macos.headline);
    expect(results[0].textContent?.endsWith("Run flashnode doctor for a real host check.")).toBe(true);
    expect(results[0].textContent).not.toContain("Your machine is supported");

    await act(async () => root.unmount());
    navigator.restore();
    environment.restore();
  });
});

describe("the seven-scene workflow", () => {
  it("keeps every scene within landing-page density limits and preserves event chronology", () => {
    expect(WORKFLOW_SCENES.map(({ id }) => id)).toEqual([
      "connect", "register", "submit", "parallel", "checkpoint", "recover", "accept",
    ]);
    expect(WORKFLOW_SCENES.map(({ objects, paths }) => [objects.length, paths.length])).toEqual([
      [5, 2],
      [4, 1],
      [3, 1],
      [5, 2],
      [4, 1],
      [4, 2],
      [3, 1],
    ]);
    expect(WORKFLOW_SCENES.flatMap(({ events }) => events)).toEqual([
      "LEASE_CLAIMED",
      "CHECKPOINT_MANIFEST_COMMITTED",
      "NODE_HEARTBEAT_LOST",
      "TASK_REQUEUED",
      "TASK_COMMIT_ACCEPTED",
    ]);
  });

  it("renders primary scene objects, paths, and relevant events as inspectable states", () => {
    for (const scene of WORKFLOW_SCENES) {
      const markup = renderToStaticMarkup(
        createElement(WorkflowScene, { step: scene.id, compact: true }),
      );

      expect(markup.match(/data-scene-object=/g) ?? []).toHaveLength(scene.objects.length);
      expect(markup.match(/data-scene-path=/g) ?? []).toHaveLength(scene.paths.length);
      expect(markup.match(/data-scene-event=/g) ?? []).toHaveLength(scene.events.length);
      expect(markup).toContain(`data-workflow-scene="${scene.id}"`);
    }
  });

  it("replaces the shared topology and protocol ticker with seven adjacent story scenes", () => {
    const markup = renderToStaticMarkup(createElement(SystemJourney));
    const steps = [...markup.matchAll(/data-workflow-step="([^"]+)"/g)].map(([, id]) => id);
    const scenes = [...markup.matchAll(/data-workflow-scene="([^"]+)"/g)].map(([, id]) => id);

    expect(steps).toEqual([
      "connect", "register", "submit", "parallel", "checkpoint", "recover", "accept",
    ]);
    expect(scenes).toEqual(steps);
    expect(markup).toContain("data-workflow-stage");
    expect(markup).not.toContain('data-active="false"');
    expect(markup).not.toContain("workflow / topology");
    expect(markup).not.toContain("Protocol events");
    expect(markup).not.toContain("data-journey-node");
    expect(markup).not.toContain("data-journey-event");
  });
});

describe("the supporting landing story", () => {
  it("keeps the four workload messages factual and in the approved order", () => {
    expect(WORKLOADS.map(({ title }) => title)).toEqual([
      "Federated training",
      "Hyperparameter search",
      "Shared data processing",
      "Checkpointable model training",
    ]);
  });

  it("limits the architecture trace to lease, checkpoint, and recovery", () => {
    expect(ARCHITECTURE_SIGNALS.map(({ id, event }) => [id, event])).toEqual([
      ["lease", "LEASE_CLAIMED"],
      ["checkpoint", "CHECKPOINT_MANIFEST_COMMITTED"],
      ["recovery", "TASK_REQUEUED"],
    ]);
  });

  it("keeps visual controls and live results named by accessible semantics", () => {
    const hero = renderToStaticMarkup(createElement(HeroComputeFabric));
    const platform = renderToStaticMarkup(createElement(PlatformSupport));
    const architecture = renderToStaticMarkup(createElement(WorkflowScene, {
      step: "recover",
      compact: true,
    }));

    expect(hero).toContain('aria-label="Compute source"');
    expect(hero.match(/aria-pressed=/g) ?? []).toHaveLength(5);
    expect(hero).toContain('aria-label="Inspect job state"');
    expect(hero.match(/aria-current=/g) ?? []).toHaveLength(1);
    expect(hero).toContain('aria-live="polite"');
    expect(platform.match(/data-runtime-button=/g) ?? []).toHaveLength(9);
    expect(platform).toContain("Check this browser");
    expect(platform).not.toContain("data-machine-result");
    expect(architecture).toContain('aria-hidden="true"');
  });
});
