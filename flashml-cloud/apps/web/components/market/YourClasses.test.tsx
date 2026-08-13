import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { hostClassGroups } from "@/lib/market/board";
import type { PricesView } from "@/lib/cloud-api";
import { YourClasses } from "./YourClasses";

/**
 * THE CARD THE COMPLAINT WAS ABOUT. A host whose workstation sits in
 * `cpu-large` used to open this page to a dash, "no history yet", and a chip
 * counting zero observations. The card now carries a price, a movement and
 * the card it was estimated from — and every one of those three has to reach
 * the markup wearing its badge, because the badge is what stops an estimate
 * reading as an earning.
 */
const EMPTY_BOARD: PricesView = {
  quotes: [],
  unpriced: [],
  zc: [],
  board: { open_asks_total: 0, live_classes: 0, observations_24h: 0 },
};

const card = (klass: string) =>
  renderToStaticMarkup(
    <YourClasses
      groups={hostClassGroups(EMPTY_BOARD, [
        { id: "m1", label: "workstation", klass },
      ])}
      state="present"
    />
  );

describe("YourClasses", () => {
  it("gives an untouched class a price, marked as an estimate", () => {
    const html = card("cpu-large");
    expect(html).toContain("≈ 0.32 ZC/hr");
    expect(html).toContain("REFERENCE · derived");
    expect(html).toContain("128 vCPU");
  });

  it("replaces no-history-yet with the movement of the borrowed line", () => {
    const html = card("cpu-large");
    expect(html).not.toContain("no history yet");
    expect(html).toMatch(/cheaper|pricier|unchanged/);
  });

  it("still says nothing at all about a class nothing describes", () => {
    // No rung, no seed row, no derivation: a card that admits it rather
    // than one that reaches for the nearest number.
    const html = card("npu-shiny");
    expect(html).toContain("no history yet");
    expect(html).toContain(">—<");
    expect(html).not.toContain("REFERENCE");
    expect(html).not.toContain("≈");
  });
});
