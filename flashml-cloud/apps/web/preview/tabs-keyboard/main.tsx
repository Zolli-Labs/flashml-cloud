import React, { useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";

/**
 * A KEYBOARD RIG, not a preview.
 *
 * `app/(console)/admin/requests/page.tsx` hand-rolls a full ARIA tablist —
 * roving tabindex, ArrowLeft/ArrowRight/Home/End, focus management — and the
 * sweep proposes replacing it with `components/ui/tabs.tsx`. Whether that is
 * an improvement or an accessibility regression is a question about what
 * happens when keys are pressed, which no unit test in this repo can answer:
 * there is no jsdom, no happy-dom and no testing-library installed, and a
 * static SSR render has no focus and no key events.
 *
 * So both tablists are mounted here, live, and driven by a real browser
 * pressing real keys. `window.__probe()` reports what a screen reader would
 * be told about each one; the two reports are compared key by key.
 *
 *   npx vite build --config preview/tabs-keyboard/vite.config.ts
 *   (then serve the output and drive it)
 *
 * Not part of any suite: `vitest.config.ts` collects `**\/*.test.*`, and the
 * preview runner collects `preview/**\/*.render.tsx`. This is neither.
 */

type RequestTab = "access" | "credits";
const REQUEST_TABS: RequestTab[] = ["access", "credits"];

/** The tablist exactly as `admin/requests/page.tsx` writes it today. Copied
 * rather than imported so the comparison is against the shipped behaviour,
 * not against whatever the page looks like after the sweep. */
function HandRolled() {
  const [tab, setTab] = useState<RequestTab>("access");
  const tabRefs = useRef<Record<RequestTab, HTMLButtonElement | null>>({
    access: null,
    credits: null,
  });

  function selectTab(nextTab: RequestTab, focus = false) {
    setTab(nextTab);
    if (focus) tabRefs.current[nextTab]?.focus();
  }

  function handleTabKeyDown(event: React.KeyboardEvent<HTMLButtonElement>) {
    const currentIndex = REQUEST_TABS.indexOf(tab);
    let nextIndex: number | null = null;
    if (event.key === "ArrowLeft")
      nextIndex = (currentIndex + REQUEST_TABS.length - 1) % REQUEST_TABS.length;
    if (event.key === "ArrowRight")
      nextIndex = (currentIndex + 1) % REQUEST_TABS.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = REQUEST_TABS.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    selectTab(REQUEST_TABS[nextIndex], true);
  }

  return (
    <div id="hand-rolled">
      <div role="tablist" aria-label="Request type">
        <button
          type="button"
          ref={(element) => {
            tabRefs.current.access = element;
          }}
          id="hr-access-tab"
          role="tab"
          aria-selected={tab === "access"}
          aria-controls="hr-access-panel"
          tabIndex={tab === "access" ? 0 : -1}
          onClick={() => selectTab("access")}
          onKeyDown={handleTabKeyDown}
        >
          Access
        </button>
        <button
          type="button"
          ref={(element) => {
            tabRefs.current.credits = element;
          }}
          id="hr-credits-tab"
          role="tab"
          aria-selected={tab === "credits"}
          aria-controls="hr-credits-panel"
          tabIndex={tab === "credits" ? 0 : -1}
          onClick={() => selectTab("credits")}
          onKeyDown={handleTabKeyDown}
        >
          Credits
        </button>
      </div>
      <div
        id="hr-access-panel"
        role="tabpanel"
        aria-labelledby="hr-access-tab"
        tabIndex={0}
        hidden={tab !== "access"}
      >
        <input id="hr-access-input" defaultValue="" />
      </div>
      <div
        id="hr-credits-panel"
        role="tabpanel"
        aria-labelledby="hr-credits-tab"
        tabIndex={0}
        hidden={tab !== "credits"}
      >
        {/* Stands in for CreditRequestCard's approval field, which holds
            local state the admin may have typed into. */}
        <input id="hr-credits-input" defaultValue="" />
      </div>
    </div>
  );
}

/** The replacement, written the way the sweep would ship it. */
function Primitive() {
  const [tab, setTab] = useState<RequestTab>("access");
  return (
    <div id="primitive">
      <Tabs
        value={tab}
        onValueChange={(value) => setTab(value as RequestTab)}
      >
        <TabsList activateOnFocus aria-label="Request type">
          <TabsTrigger value="access">Access</TabsTrigger>
          <TabsTrigger value="credits">Credits</TabsTrigger>
        </TabsList>
        <TabsContent value="access" keepMounted>
          <input id="pr-access-input" defaultValue="" />
        </TabsContent>
        <TabsContent value="credits" keepMounted>
          <input id="pr-credits-input" defaultValue="" />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function Rig() {
  return (
    <div>
      <h1>Hand-rolled</h1>
      <HandRolled />
      <h1>Primitive</h1>
      <Primitive />
    </div>
  );
}

/** What a screen reader would be told, for one tablist. Read from the live
 * DOM rather than from React state, because the DOM is what assistive tech
 * actually consumes. */
function report(scopeId: string) {
  const scope = document.getElementById(scopeId)!;
  const tabs = Array.from(scope.querySelectorAll('[role="tab"]'));
  const panels = Array.from(scope.querySelectorAll('[role="tabpanel"]'));
  const active = document.activeElement;
  return {
    tabs: tabs.map((t) => ({
      text: (t.textContent ?? "").trim(),
      selected: t.getAttribute("aria-selected"),
      tabIndex: (t as HTMLElement).tabIndex,
      focused: t === active,
      controls: !!t.getAttribute("aria-controls"),
    })),
    // A panel counts as visible if the browser lays it out at all.
    visiblePanels: panels
      .filter((p) => (p as HTMLElement).offsetParent !== null)
      .map((p) => p.id || "(no id)"),
    mountedPanels: panels.length,
    focusedId: active ? active.id || active.tagName : null,
  };
}

declare global {
  interface Window {
    __probe: (scopeId: string) => unknown;
    __focusFirstTab: (scopeId: string) => void;
  }
}

window.__probe = report;
window.__focusFirstTab = (scopeId: string) => {
  const scope = document.getElementById(scopeId)!;
  const first = scope.querySelector('[role="tab"]') as HTMLElement | null;
  first?.focus();
};

createRoot(document.getElementById("root")!).render(<Rig />);
