import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  notifyWorkspacesChanged,
  onWorkspacesChanged,
} from "./workspace-events";

// vitest runs this suite with `environment: "node"`, so there is no DOM.
// A bare `EventTarget` (a Node global) has exactly the three methods this
// module uses, which is the whole surface it needs from `window`.
const original = (globalThis as { window?: unknown }).window;

beforeEach(() => {
  (globalThis as { window?: unknown }).window = new EventTarget();
});

afterEach(() => {
  if (original === undefined) delete (globalThis as { window?: unknown }).window;
  else (globalThis as { window?: unknown }).window = original;
});

describe("workspace-events", () => {
  it("delivers a notification to every subscriber", () => {
    const a = vi.fn();
    const b = vi.fn();
    onWorkspacesChanged(a);
    onWorkspacesChanged(b);

    notifyWorkspacesChanged();

    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
  });

  it("stops delivering once unsubscribed", () => {
    // The returned function is what a `useEffect` cleanup returns, so a
    // switcher that unmounted must not keep refetching on every rename.
    const handler = vi.fn();
    const off = onWorkspacesChanged(handler);

    off();
    notifyWorkspacesChanged();

    expect(handler).not.toHaveBeenCalled();
  });

  it("is a silent no-op with no window, so server rendering cannot throw", () => {
    delete (globalThis as { window?: unknown }).window;

    expect(() => notifyWorkspacesChanged()).not.toThrow();
    // Still returns a callable unsubscribe, so a caller can use it
    // unconditionally.
    expect(() => onWorkspacesChanged(() => {})()).not.toThrow();
  });
});
