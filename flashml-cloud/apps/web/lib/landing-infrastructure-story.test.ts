import { existsSync, readFileSync, readdirSync } from "node:fs";
import { act, createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Hero } from "@/components/landing/Hero";
import { HeroMarketSwitch } from "@/components/landing/HeroMarketSwitch";
import { CoordinatorMap } from "@/components/landing/coordinator-map/CoordinatorMap";
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
  MAP_NODES,
  MAP_VIEWPORT_COMPACT,
  MAP_VIEWPORT_DESKTOP,
  RESCUER_KEY,
  VICTIM_KEY,
  readoutFor,
  routeStateFor,
  type MapNodeKey,
  type MapPhase,
  type Viewport,
} from "./coordinator-map";
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

const root = process.cwd();
const source = (path: string) => readFileSync(`${root}/${path}`, "utf8");
const sourceFilesUnder = (path: string): string[] => readdirSync(`${root}/${path}`, {
  withFileTypes: true,
}).flatMap((entry) => {
  const child = `${path}/${entry.name}`;
  if (entry.isDirectory()) return sourceFilesUnder(child);
  return /\.(?:css|ts|tsx)$/.test(entry.name) && !entry.name.includes(".test.")
    ? [child]
    : [];
});
const motionHarness = vi.hoisted(() => ({
  motion: null as null | {
    reduced: boolean;
    desktop: boolean;
    finePointer: boolean;
    documentVisible: boolean;
  },
}));

const PHASES: readonly MapPhase[] = ["running", "lost", "resumed", "accepted"];

/** Every beat, and every source on it, as `it.each` rows. */
const PHASE_ROWS = PHASES.map((phase) => [phase] as const);
const NODE_ROWS = MAP_NODES.map((spec) => [spec.key, spec] as const);

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

// `next/link` schedules a prefetch through `requestIdleCallback` the moment it
// mounts. The hero's story is asserted in scheduled timers, so a second timer
// nobody in this repo wrote would make every one of those counts a guess.
vi.mock("next/link", async () => {
  const React = await vi.importActual<typeof import("react")>("react");
  return {
    default: React.forwardRef<HTMLAnchorElement, { href: string; children?: ReactNode }>(
      function TestLink({ href, ...props }, ref) {
        return React.createElement("a", { ...props, href, ref });
      },
    ),
  };
});

vi.mock("@/components/landing/motion/LandingMotionProvider", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("@/components/landing/motion/LandingMotionProvider")
  >();
  return {
    ...actual,
    useLandingMotion: () => motionHarness.motion ?? actual.useLandingMotion(),
  };
});

afterEach(() => {
  motionHarness.motion = null;
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

  /** Only the attribute selectors this landing actually uses — the hero finds
   * its scroll track with `[data-hero-scroll]` and nothing else calls this. */
  closest(selector: string): TestElement | null {
    if (this.hasAttribute(selector.replace(/^\[|\]$/g, ""))) return this;
    return this.parentNode?.closest(selector) ?? null;
  }

  // The fake DOM has no box model, and the hero's story is driven by measuring
  // one. Tests hand the document a layout and then move it; see `TestDocument`.
  get offsetHeight() {
    return this.ownerDocument.measure(this).height;
  }

  getBoundingClientRect() {
    const { height, top } = this.ownerDocument.measure(this);
    return { top, bottom: top + height, height, left: 0, right: 0, width: 0, x: 0, y: top };
  }

  focus() {
    this.ownerDocument.activeElement = this;
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
  /** Test-supplied box model, in pixels. Everything is zero-sized until a test
   * says otherwise, which is what makes an unmeasured hero fall to the timer. */
  measure: (element: TestElement) => { height: number; top: number } =
    () => ({ height: 0, top: 0 });

  constructor() {
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

function installMotionEnvironment() {
  const document = new TestDocument();
  const animationFrames = new Map<number, FrameRequestCallback>();
  let nextAnimationFrame = 1;
  const queries = new Map<string, TestMediaQueryList>([
    ["(prefers-reduced-motion: reduce)", new TestMediaQueryList(false)],
    ["(min-width: 1024px)", new TestMediaQueryList(true)],
    ["(pointer: fine)", new TestMediaQueryList(true)],
    ["(max-width: 880px)", new TestMediaQueryList(false)],
  ]);
  const requestAnimationFrame = (callback: FrameRequestCallback) => {
    const handle = nextAnimationFrame++;
    animationFrames.set(handle, callback);
    return handle;
  };
  const cancelAnimationFrame = (handle: number) => {
    animationFrames.delete(handle);
  };
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
    requestAnimationFrame,
    cancelAnimationFrame,
    setTimeout(handler: TimerHandler, timeout?: number) {
      return globalThis.setTimeout(handler, timeout);
    },
    clearTimeout(handle: number) {
      globalThis.clearTimeout(handle);
    },
  });
  document.defaultView = window as never;

  /** Always intersecting unless a test says otherwise: the hero only listens to
   * scroll while it is on screen, and every scroll assertion here is about a
   * hero that is. */
  const observers = new Set<TestIntersectionObserver>();
  class TestIntersectionObserver {
    private readonly targets = new Set<TestElement>();

    constructor(
      private readonly callback: (
        entries: readonly { isIntersecting: boolean; target: TestElement }[],
      ) => void,
    ) {
      observers.add(this);
    }

    observe(target: TestElement) {
      this.targets.add(target);
      this.callback([{ isIntersecting: true, target }]);
    }

    unobserve(target: TestElement) {
      this.targets.delete(target);
    }

    disconnect() {
      this.targets.clear();
      observers.delete(this);
    }

    /** Drive the hero off screen, or back on to it. */
    setIntersecting(isIntersecting: boolean) {
      this.callback([...this.targets].map((target) => ({ isIntersecting, target })));
    }
  }

  const previous = {
    window: globalThis.window,
    document: globalThis.document,
    requestAnimationFrame: globalThis.requestAnimationFrame,
    cancelAnimationFrame: globalThis.cancelAnimationFrame,
    IntersectionObserver: globalThis.IntersectionObserver,
  };
  // The map's render loop and the hero's observer reach for the bare globals,
  // not for `window.*`, because that is what they get in a browser.
  Object.assign(globalThis, {
    window,
    document,
    requestAnimationFrame,
    cancelAnimationFrame,
    IntersectionObserver: TestIntersectionObserver,
  });

  return {
    container: document.createElement("div"),
    document,
    window,
    queries,
    observers,
    pendingAnimationFrames: () => animationFrames.size,
    windowListeners: (type: string) => window.listeners.get(type)?.size ?? 0,
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
      Object.assign(globalThis, previous);
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

/** React derives `onFocus`/`onKeyDown` from real bubbling events on the root,
 * so a test has to send the event the browser would rather than call the prop. */
function send(element: TestElement, type: string, key?: string) {
  element.dispatchEvent({
    type,
    key,
    target: element,
    bubbles: true,
    preventDefault() {
      this.defaultPrevented = true;
    },
    stopPropagation() {
      this.propagationStopped = true;
    },
  });
}

type MotionState = {
  reduced: boolean;
  desktop: boolean;
  finePointer: boolean;
  documentVisible: boolean;
};

const ACTIVE_MOTION: MotionState = {
  reduced: false,
  desktop: true,
  finePointer: true,
  documentVisible: true,
};

/** The map on one fixed beat, with no story driving it. Everything about what
 * the map *draws* is asserted through this; how the beat is chosen is asserted
 * through `mountHero`. */
async function mountMap({
  phase = "running" as MapPhase,
  viewport = MAP_VIEWPORT_DESKTOP as Viewport,
  onSelect,
}: {
  phase?: MapPhase;
  viewport?: Viewport;
  onSelect?: (key: MapNodeKey) => void;
} = {}) {
  const environment = installMotionEnvironment();
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
    .IS_REACT_ACT_ENVIRONMENT = true;
  const { createRoot } = await import("react-dom/client");
  const reactRoot = createRoot(environment.container as never);

  await act(async () =>
    reactRoot.render(createElement(CoordinatorMap, { phase, viewport, onSelect })),
  );

  return {
    ...environment,
    node: (key: MapNodeKey) =>
      elementsByAttribute(environment.container, "data-map-node", key)[0],
    route: (key: MapNodeKey) =>
      elementsByAttribute(environment.container, "data-route", key)[0],
    readout: (cell: string) => {
      const group = elementsByAttribute(environment.container, "data-readout", cell)[0];
      return findElements(group, (element) => element.tagName === "P")[1];
    },
    async cleanup() {
      await act(async () => reactRoot.unmount());
      environment.restore();
    },
  };
}

/**
 * The hero as the page mounts it.
 *
 * `track` decides which of the story's two drivers is in force, and it decides
 * it the way the browser does: a track with spare height over the pinned hero
 * drives the beat from scroll, and one without hands the story to the timer.
 * No test asks for a driver by name.
 */
async function mountHero({
  track = false,
  motion = ACTIVE_MOTION,
  heroHeight = 900,
  trackHeight = 2100,
}: {
  track?: boolean;
  motion?: MotionState;
  heroHeight?: number;
  trackHeight?: number;
} = {}) {
  vi.useFakeTimers();
  const environment = installMotionEnvironment();
  motionHarness.motion = { ...motion };
  let scrolled = 0;
  environment.document.measure = (element) => {
    if (element.hasAttribute("data-hero-scroll")) {
      return { height: trackHeight, top: -scrolled };
    }
    return { height: element.getAttribute("id") === "hero" ? heroHeight : 0, top: 0 };
  };

  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
    .IS_REACT_ACT_ENVIRONMENT = true;
  const { createRoot } = await import("react-dom/client");
  const reactRoot = createRoot(environment.container as never);
  const tree = () =>
    track
      ? createElement("div", { "data-hero-scroll": true }, createElement(Hero))
      : createElement(Hero);

  await act(async () => reactRoot.render(tree()));
  // The first measurement happens in a frame, exactly as it does in a browser.
  await act(async () => environment.runAnimationFrames());

  const hero = () => elementsByAttribute(environment.container, "data-coordinator-map")[0];

  return {
    ...environment,
    phase: () => hero()?.getAttribute("data-coordinator-map"),
    section: () => findElements(environment.container, (element) =>
      element.getAttribute("id") === "hero")[0],
    async scrollTo(offset: number) {
      scrolled = offset;
      await act(async () => {
        environment.window.dispatchEvent({ type: "scroll" });
        environment.runAnimationFrames();
      });
    },
    async renderMotion(next: MotionState) {
      motionHarness.motion = { ...next };
      await act(async () => reactRoot.render(tree()));
    },
    async cleanup() {
      await act(async () => reactRoot.unmount());
      environment.restore();
    },
  };
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

  it("renders the open-compute hero and its two market paths", () => {
    const markup = renderToStaticMarkup(createElement(Hero));
    const roleMarkup = renderToStaticMarkup(createElement(HeroMarketSwitch));
    const text = markup.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

    expect(text).toContain("Computing power, without the lock-in.");
    expect(text).toContain("Need computing power?");
    expect(text).toContain("Access more compute at a competitive price.");
    expect(text).toContain("Have unused computing power?");
    expect(text).toContain("Host it and earn from the work it completes.");
    expect(markup).toContain('data-market-role="demand"');
    expect(markup).toContain('data-market-role="supply"');
    expect(roleMarkup).not.toContain("aria-live");
    expect(markup).toContain('href="/workspaces"');
    expect(markup).toContain('href="/account/machines"');
    expect(markup.indexOf("Get early access")).toBeLessThan(
      markup.indexOf("Provide compute"),
    );
  });
});

describe("the coordinator map hero", () => {
  it("keeps the coordinator map while the hero leads with the open compute network", () => {
    const hero = source("components/landing/Hero.tsx");

    expect(hero).toContain(
      'import { CoordinatorMap } from "@/components/landing/coordinator-map/CoordinatorMap"',
    );
    expect(hero).toContain("<CoordinatorMap");
    expect(hero).toContain("phase={phase}");
    expect(hero).toContain("useMapStory()");
    expect(hero).not.toContain("HeroComputeFabric");
    expect(hero).not.toContain("HeroInfrastructureStack");
    expect(hero.indexOf("Computing power,")).toBeLessThan(hero.indexOf("without the lock-in."));
    expect(hero.indexOf("Get early access")).toBeLessThan(hero.indexOf("Provide compute"));
  });

  it("removes the three.js compute fabric and everything generated for it", () => {
    const removedPaths = [
      "app/(marketing)/hero-lab/page.tsx",
      "components/hero-lab",
      "components/landing/HeroInfrastructureStack.tsx",
      "components/landing/HeroComputeFabric.tsx",
      "components/landing/HeroComputeFabric.module.css",
      "components/landing/hero-fabric",
      "lib/hero-lab.ts",
      "lib/hero-lab.test.ts",
      "lib/hero-fabric.ts",
      "lib/hero-fabric.test.ts",
      "lib/hero-fabric-assets.mjs",
      "lib/hero-fabric-assets.d.ts",
      "scripts/hero-assets",
      "public/models/hero/fabric",
    ];
    expect(
      removedPaths.filter((path) => existsSync(`${root}/${path}`)),
    ).toEqual([]);

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
    // The engine, and every seam it was wired through.
    expect(productionSource).not.toMatch(
      /hero-fabric|HeroComputeFabric|@react-three|WebGLRenderer|EffectComposer|SelectiveBloom|\buseFrame\b|<Canvas\b/,
    );
    expect(source("app/globals.css")).not.toContain("hero-infra-");

    const pkg = JSON.parse(source("package.json"));
    const dependencies = { ...pkg.dependencies, ...pkg.devDependencies };
    expect(Object.keys(dependencies).filter((name) => /three|postprocessing|gltf/.test(name)))
      .toEqual([]);
    expect(Object.keys(pkg.scripts).filter((name) => name.startsWith("hero:"))).toEqual([]);
  });

  it("keeps experiment and engine language out of the production hero", () => {
    const hero = [
      "components/landing/Hero.tsx",
      "components/landing/coordinator-map/CoordinatorMap.tsx",
      "components/landing/coordinator-map/useMapStory.ts",
    ].map(source).join("\n");

    for (const temporaryLabel of ["HeroLab", "hero-lab", "CONCEPT", "STRENGTH", "WEAKNESS"]) {
      expect(hero, temporaryLabel).not.toContain(temporaryLabel);
    }
    expect(hero).not.toMatch(/\bB2\b/);
  });

  it("keeps the whole hero payload inside its markup budget", () => {
    // The three.js hero shipped a 281 KB gzipped chunk before a pixel was drawn.
    // This one is markup, and the design caps it at 60 KB uncompressed.
    const desktop = renderToStaticMarkup(createElement(Hero));
    const compact = renderToStaticMarkup(
      createElement(CoordinatorMap, { phase: "resumed", viewport: MAP_VIEWPORT_COMPACT }),
    );

    expect(Buffer.byteLength(desktop, "utf8")).toBeLessThan(60 * 1024);
    expect(Buffer.byteLength(compact, "utf8")).toBeLessThan(60 * 1024);
  });

  it("switches to the stacked composition only where the frame is narrow", () => {
    const hero = source("components/landing/Hero.tsx");

    expect(hero).toContain('const COMPACT_QUERY = "(max-width: 880px)"');
    expect(hero).toContain("compact ? MAP_VIEWPORT_COMPACT : HERO_MAP_VIEWPORT");
    // A crop of the desktop frame, not a second projection: same scale, same
    // composition, less empty panel beside the headline.
    expect(hero).toContain("...MAP_VIEWPORT_DESKTOP");
    expect(MAP_VIEWPORT_COMPACT.height).toBeGreaterThan(MAP_VIEWPORT_COMPACT.width);
    expect(MAP_VIEWPORT_DESKTOP.width).toBeGreaterThan(MAP_VIEWPORT_DESKTOP.height);
  });

  it.each(NODE_ROWS)("draws the %s source with its name, chip and platform badges", async (key, spec) => {
    const mounted = await mountMap();
    try {
      const drawn = mounted.node(key);

      expect(drawn).toBeDefined();
      expect(drawn.textContent).toContain(spec.name);
      expect(drawn.textContent).toContain(spec.chip);
      expect(
        elementsByAttribute(drawn, "data-platform").map((badge) => badge.getAttribute("data-platform")),
      ).toEqual(spec.badges.map((badge) => badge.toLowerCase()));
    } finally {
      await mounted.cleanup();
    }
  });

  it.each(NODE_ROWS)("announces the %s source and puts it in the tab order", async (key, spec) => {
    const mounted = await mountMap();
    try {
      const drawn = mounted.node(key);

      expect(drawn.getAttribute("role")).toBe("button");
      expect(drawn.getAttribute("tabindex")).toBe("0");
      expect(drawn.getAttribute("aria-label")).toBe(`${spec.name}. ${spec.chip}.`);
    } finally {
      await mounted.cleanup();
    }
  });

  it.each(PHASE_ROWS)("dresses every route for the %s beat", async (phase) => {
    const mounted = await mountMap({ phase });
    try {
      for (const spec of MAP_NODES) {
        expect(mounted.route(spec.key).getAttribute("data-route-state"), spec.key)
          .toBe(routeStateFor(spec.key, phase));
      }
      // The job is on exactly one route on every beat but `lost`, where it is
      // on none. That gap is the beat: the work has nowhere to be yet.
      expect(
        elementsByAttribute(mounted.container, "data-route")
          .filter((route) => route.getAttribute("data-route-state") === "active"),
      ).toHaveLength(phase === "lost" ? 0 : 1);
    } finally {
      await mounted.cleanup();
    }
  });

  it.each(PHASE_ROWS)("reads the %s beat out on the live strip", async (phase) => {
    const mounted = await mountMap({ phase });
    const expected = readoutFor(phase);
    try {
      expect(mounted.readout("leases").textContent).toBe(String(expected.leases));
      expect(mounted.readout("checkpoint").textContent).toBe(expected.checkpoint);
      expect(mounted.readout("lost").textContent).toBe(String(expected.lost));
      expect(mounted.readout("goodput").textContent).toBe(`${expected.goodput} / 100`);
      expect(mounted.readout("state").textContent).toBe(expected.state);
      // Only the state cell announces; five cells changing at once is a klaxon.
      expect(mounted.readout("state").getAttribute("aria-live")).toBe("polite");
      expect(mounted.readout("goodput").getAttribute("aria-live")).toBeNull();
    } finally {
      await mounted.cleanup();
    }
  });

  it.each(PHASE_ROWS)("spends orange only on the job's path during %s", async (phase) => {
    const mounted = await mountMap({ phase });
    try {
      const orange = (element: TestElement) =>
        findElements(element, (candidate) =>
          [...candidate.attributes.values()].some((value) => value.includes("--z-orange")),
        );

      // No orange hardware, no orange ground. The one rule that separates this
      // map from the reference art it replaces.
      for (const spec of MAP_NODES) {
        expect(orange(mounted.node(spec.key)), spec.key).toHaveLength(0);
      }
      expect(
        orange(elementsByAttribute(mounted.container, "data-map-layer", "grid")[0]),
      ).toHaveLength(0);

      // Where orange is spent: the one route carrying the job, and no other —
      // and on the `lost` beat, where nothing is carrying it, nowhere at all.
      const orangeRoutes = elementsByAttribute(mounted.container, "data-route")
        .filter((route) => (route.getAttribute("stroke") ?? "").includes("--z-orange"));
      expect(orangeRoutes.map((route) => route.getAttribute("data-route-state")))
        .toEqual(phase === "lost" ? [] : ["active"]);
    } finally {
      await mounted.cleanup();
    }
  });

  it("marks the machine that died and the result that was accepted", async () => {
    const lost = await mountMap({ phase: "lost" });
    try {
      expect(elementsByAttribute(lost.node(VICTIM_KEY), "data-mark", "failed")).toHaveLength(1);
      expect(elementsByAttribute(lost.container, "data-mark", "accepted")).toHaveLength(0);
    } finally {
      await lost.cleanup();
    }

    const accepted = await mountMap({ phase: "accepted" });
    try {
      // The loss stays on the board as history while the rescuer carries a tick:
      // attempted work is not accepted work, and the map has to show both.
      expect(elementsByAttribute(accepted.node(VICTIM_KEY), "data-mark", "failed")).toHaveLength(1);
      expect(elementsByAttribute(accepted.node(RESCUER_KEY), "data-mark", "accepted")).toHaveLength(1);
    } finally {
      await accepted.cleanup();
    }
  });

  it("names the map for a screen reader and hides its decoration", async () => {
    const mounted = await mountMap();
    try {
      const svg = findElements(mounted.container, (element) => element.tagName === "SVG")[0];

      expect(svg.getAttribute("role")).toBe("group");
      expect(svg.getAttribute("aria-label")).toContain("four machine sources");
      expect(svg.getAttribute("aria-label")).toContain("Zolli coordinator");
      for (const layer of ["grid", "routes", "particles"]) {
        expect(
          elementsByAttribute(mounted.container, "data-map-layer", layer)[0].getAttribute("aria-hidden"),
          layer,
        ).toBe("true");
      }
      expect(
        elementsByAttribute(mounted.container, "data-map-core", "coordinator")[0]
          .getAttribute("aria-hidden"),
      ).toBe("true");
    } finally {
      await mounted.cleanup();
    }
  });
});

describe("the hero's scroll story", () => {
  it("walks the four beats once where nothing pins the hero", async () => {
    const mounted = await mountHero();
    try {
      expect(mounted.phase()).toBe("running");

      await act(async () => { vi.advanceTimersByTime(4_800); });
      expect(mounted.phase()).toBe("lost");

      await act(async () => { vi.advanceTimersByTime(3_200); });
      expect(mounted.phase()).toBe("resumed");

      await act(async () => { vi.advanceTimersByTime(4_000); });
      expect(mounted.phase()).toBe("accepted");
    } finally {
      await mounted.cleanup();
    }
  });

  it("rests on the accepted result instead of looping like a banner", async () => {
    const mounted = await mountHero();
    try {
      await act(async () => { vi.advanceTimersByTime(12_000); });
      expect(mounted.phase()).toBe("accepted");
      expect(vi.getTimerCount()).toBe(0);

      await act(async () => { vi.advanceTimersByTime(120_000); });
      expect(mounted.phase()).toBe("accepted");
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      await mounted.cleanup();
    }
  });

  it.each([["pointerenter"], ["focusin"]])(
    "replays from rest on %s and ignores it mid-story",
    async (event) => {
      const mounted = await mountHero();
      try {
        await act(async () => { vi.advanceTimersByTime(12_000); });
        expect(mounted.phase()).toBe("accepted");

        await act(async () => send(mounted.section(), event));
        expect(mounted.phase()).toBe("running");

        // Mid-story the same event does nothing: restarting would punish the
        // reader for moving the mouse across the hero.
        await act(async () => { vi.advanceTimersByTime(4_800); });
        expect(mounted.phase()).toBe("lost");
        await act(async () => send(mounted.section(), event));
        expect(mounted.phase()).toBe("lost");
      } finally {
        await mounted.cleanup();
      }
    },
  );

  it("takes the beat from scroll progress where the hero is pinned in a track", async () => {
    // 2100 of track less a 900 hero leaves 1200px of travel for the story.
    const mounted = await mountHero({ track: true });
    try {
      expect(mounted.phase()).toBe("running");
      expect(vi.getTimerCount()).toBe(0);

      await mounted.scrollTo(120);
      expect(mounted.phase()).toBe("running");
      await mounted.scrollTo(480);
      expect(mounted.phase()).toBe("lost");
      await mounted.scrollTo(720);
      expect(mounted.phase()).toBe("resumed");
      await mounted.scrollTo(1_050);
      expect(mounted.phase()).toBe("accepted");

      // Rubber-band overshoot at either end, which every touch platform does.
      await mounted.scrollTo(-300);
      expect(mounted.phase()).toBe("running");
      await mounted.scrollTo(4_000);
      expect(mounted.phase()).toBe("accepted");
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      await mounted.cleanup();
    }
  });

  it("listens to scroll only while the hero is on screen", async () => {
    const mounted = await mountHero({ track: true });
    try {
      expect(mounted.windowListeners("scroll")).toBe(1);

      await act(async () => {
        for (const observer of mounted.observers) observer.setIntersecting(false);
      });
      expect(mounted.windowListeners("scroll")).toBe(0);

      await act(async () => {
        for (const observer of mounted.observers) observer.setIntersecting(true);
      });
      expect(mounted.windowListeners("scroll")).toBe(1);
    } finally {
      await mounted.cleanup();
    }
  });

  it("starts no loop, timer or frame when the reader asked for stillness", async () => {
    const mounted = await mountHero({ motion: { ...ACTIVE_MOTION, reduced: true } });
    try {
      // One representative frame, and the one worth serving: a machine has
      // died and its work is already running somewhere else.
      expect(mounted.phase()).toBe("resumed");
      expect(vi.getTimerCount()).toBe(0);
      expect(mounted.pendingAnimationFrames()).toBe(0);
      expect(
        elementsByAttribute(mounted.container, "data-map-layer", "particles")[0]
          .getAttribute("data-motion"),
      ).toBe("static");

      await act(async () => { vi.advanceTimersByTime(60_000); });
      expect(mounted.phase()).toBe("resumed");
      expect(mounted.pendingAnimationFrames()).toBe(0);
    } finally {
      await mounted.cleanup();
    }
  });

  it("holds the story while the tab is hidden and does not advance behind it", async () => {
    const mounted = await mountHero();
    try {
      await act(async () => { vi.advanceTimersByTime(4_800); });
      expect(mounted.phase()).toBe("lost");
      expect(vi.getTimerCount()).toBe(1);

      await mounted.renderMotion({ ...ACTIVE_MOTION, documentVisible: false });
      expect(vi.getTimerCount()).toBe(0);
      await act(async () => { vi.advanceTimersByTime(30_000); });
      expect(mounted.phase()).toBe("lost");
    } finally {
      await mounted.cleanup();
    }
  });

  it("runs the map's own loop only while the document is visible", async () => {
    const mounted = await mountHero();
    try {
      expect(mounted.pendingAnimationFrames()).toBeGreaterThan(0);

      await mounted.renderMotion({ ...ACTIVE_MOTION, documentVisible: false });
      expect(mounted.pendingAnimationFrames()).toBe(0);

      await mounted.renderMotion({ ...ACTIVE_MOTION });
      expect(mounted.pendingAnimationFrames()).toBeGreaterThan(0);
    } finally {
      await mounted.cleanup();
    }
  });

  it("releases every timer, frame and listener on unmount", async () => {
    const mounted = await mountHero({ track: true });
    await act(async () => { vi.advanceTimersByTime(4_800); });

    await mounted.cleanup();

    expect(vi.getTimerCount()).toBe(0);
    expect(mounted.pendingAnimationFrames()).toBe(0);
    expect(mounted.windowListeners("scroll")).toBe(0);
    expect(mounted.windowListeners("resize")).toBe(0);
    expect(mounted.observers.size).toBe(0);
  });
});

describe("inspecting a source on the map", () => {
  it.each(NODE_ROWS)("lifts the %s source and dims the other three on focus", async (key) => {
    const mounted = await mountMap();
    try {
      await act(async () => send(mounted.node(key), "focusin"));

      for (const spec of MAP_NODES) {
        expect(mounted.node(spec.key).getAttribute("data-lifted"), spec.key)
          .toBe(spec.key === key ? "true" : "false");
      }
    } finally {
      await mounted.cleanup();
    }
  });

  it.each(NODE_ROWS)("activates the %s source from the keyboard and the pointer", async (key) => {
    const selected: MapNodeKey[] = [];
    const mounted = await mountMap({ onSelect: (chosen) => selected.push(chosen) });
    try {
      await act(async () => send(mounted.node(key), "keydown", "Enter"));
      await act(async () => send(mounted.node(key), "keydown", " "));
      await act(async () => mounted.node(key).click());
      expect(selected).toEqual([key, key, key]);

      // Any other key belongs to the page, not to the map.
      await act(async () => send(mounted.node(key), "keydown", "a"));
      expect(selected).toHaveLength(3);
    } finally {
      await mounted.cleanup();
    }
  });

  it("returns to the plain map when focus leaves the source", async () => {
    const mounted = await mountMap();
    try {
      await act(async () => send(mounted.node("owned"), "focusin"));
      expect(mounted.node("owned").getAttribute("data-lifted")).toBe("true");

      await act(async () => send(mounted.node("owned"), "focusout"));
      for (const spec of MAP_NODES) {
        expect(mounted.node(spec.key).getAttribute("data-lifted"), spec.key).toBe("false");
      }
    } finally {
      await mounted.cleanup();
    }
  });

  it("keeps the brightest line on the map the one carrying the job", async () => {
    const mounted = await mountMap({ phase: "running" });
    try {
      const jobRoute = mounted.route(VICTIM_KEY);
      expect(jobRoute.getAttribute("data-route-state")).toBe("active");

      // Hovering a bystander brightens its route without repainting it orange,
      // or a reader inspecting the map would rewrite the story by accident.
      await act(async () => send(mounted.node("cloud"), "focusin"));
      expect(mounted.route("cloud").getAttribute("stroke")).not.toContain("--z-orange");
      expect(jobRoute.getAttribute("stroke")).toContain("--z-orange");
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
    const hero = renderToStaticMarkup(
      createElement(CoordinatorMap, { phase: "resumed", viewport: MAP_VIEWPORT_DESKTOP }),
    );
    const platform = renderToStaticMarkup(createElement(PlatformSupport));
    const architecture = renderToStaticMarkup(createElement(WorkflowScene, {
      step: "recover",
      compact: true,
    }));

    expect(hero).toContain('role="group"');
    expect(hero.match(/role="button"/g) ?? []).toHaveLength(4);
    expect(hero.match(/aria-label=/g) ?? []).toHaveLength(5);
    expect(hero).toContain('aria-live="polite"');
    expect(hero).toContain('role="status"');
    expect(platform.match(/data-runtime-button=/g) ?? []).toHaveLength(9);
    expect(platform).toContain("Check this browser");
    expect(platform).not.toContain("data-machine-result");
    expect(architecture).toContain('aria-hidden="true"');
  });
});
