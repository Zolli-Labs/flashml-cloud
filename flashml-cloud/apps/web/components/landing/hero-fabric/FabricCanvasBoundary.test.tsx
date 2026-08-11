import { act, createElement, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FabricCanvasBoundary } from "./FabricCanvasBoundary";

vi.mock("next/image", () => ({
  default: ({ fill, ...props }: { fill?: boolean; alt: string; src: string }) => {
    void fill;
    return createElement("img", props);
  },
}));

type TestListener = (event: { type: string; currentTarget?: TestEventTarget }) => void;

class TestEventTarget {
  private readonly listeners = new Map<string, Set<TestListener>>();

  addEventListener(type: string, listener: TestListener) {
    const listeners = this.listeners.get(type) ?? new Set<TestListener>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: string, listener: TestListener) {
    this.listeners.get(type)?.delete(listener);
  }

  dispatchEvent(event: { type: string; currentTarget?: TestEventTarget }) {
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
}

class TestDocument extends TestEventTarget {
  readonly nodeType = 9;
  readonly nodeName = "#document";
  readonly documentElement: TestElement;
  readonly body: TestElement;
  defaultView: typeof globalThis.window | undefined;

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

function installReactDomEnvironment() {
  const document = new TestDocument();
  const window = Object.assign(new TestEventTarget(), {
    document,
    location: { href: "http://localhost/" },
    HTMLElement: TestElement,
    HTMLIFrameElement: TestElement,
    Node: TestElement,
    Element: TestElement,
    SVGElement: TestElement,
    getComputedStyle: () => ({ display: "block" }),
  });
  document.defaultView = window as never;

  const previousWindow = globalThis.window;
  const previousDocument = globalThis.document;
  Object.assign(globalThis, { window, document });

  return {
    container: document.createElement("div"),
    restore() {
      Object.assign(globalThis, { window: previousWindow, document: previousDocument });
    },
  };
}

function BrokenCanvas(): ReactNode {
  throw new Error("asset decode failed");
}

function CanvasProbe() {
  return <div>Canvas constructed</div>;
}

function findElement(root: TestElement, tagName: string): TestElement | undefined {
  if (root.tagName === tagName) return root;
  for (const child of root.childNodes) {
    if (child instanceof TestElement) {
      const match = findElement(child, tagName);
      if (match) return match;
    }
  }
  return undefined;
}

const controls = <button type="button">Everyday Machines</button>;

afterEach(() => {
  vi.restoreAllMocks();
});

describe("FabricCanvasBoundary", () => {
  it("contains a thrown Canvas child and preserves the DOM controls", async () => {
    const environment = installReactDomEnvironment();
    const root = createRoot(environment.container as never);
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
      .IS_REACT_ACT_ENVIRONMENT = true;

    await act(async () => {
      root.render(
        <FabricCanvasBoundary controls={controls}>
          <BrokenCanvas />
        </FabricCanvasBoundary>,
      );
    });

    expect(environment.container.textContent).toContain("Interactive 3D is unavailable");
    expect(environment.container.textContent).toContain("Everyday Machines");
    expect(environment.container.textContent).not.toContain("Canvas constructed");
    expect(findElement(environment.container, "IMG")?.getAttribute("alt")).toContain(
      "connected by the Zolli control plane",
    );

    await act(async () => root.unmount());
    environment.restore();
  });

  it("does not construct the Canvas child after an explicit WebGL failure", async () => {
    const environment = installReactDomEnvironment();
    const root = createRoot(environment.container as never);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean })
      .IS_REACT_ACT_ENVIRONMENT = true;

    await act(async () => {
      root.render(
        <FabricCanvasBoundary controls={controls} failure="webgl">
          <CanvasProbe />
        </FabricCanvasBoundary>,
      );
    });

    expect(environment.container.textContent).toContain("Interactive 3D is unavailable");
    expect(environment.container.textContent).toContain("Everyday Machines");
    expect(environment.container.textContent).not.toContain("Canvas constructed");
    expect(findElement(environment.container, "IMG")?.getAttribute("alt")).toContain(
      "connected by the Zolli control plane",
    );

    await act(async () => root.unmount());
    environment.restore();
  });
});
