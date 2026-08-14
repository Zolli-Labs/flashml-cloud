import { describe, expect, it } from "vitest";

import { UNREADABLE_WITHOUT_DETAIL, resolvePanel } from "./panel-state";
import { queueBadgeCount, queuePanelState } from "./queue-panel";

/** Rows are built here rather than declared as a fixture literal: the house
 * rules forbid fixture-shaped data in the repo, and nothing about these
 * tests depends on what a row contains — only on how many there are. */
function rows(count: number): { id: string }[] {
  return Array.from({ length: count }, (_, i) => ({ id: `row-${i}` }));
}

describe("queuePanelState", () => {
  it("shows a skeleton only when a read is in flight with nothing behind it", () => {
    expect(queuePanelState("loading", null, rows(0))).toEqual({
      kind: "loading",
    });
  });

  it("keeps rows on screen while a REFRESH runs", () => {
    // The whole reason this module is not `resolvePanel`. An admin working
    // through a queue presses refresh; the queue must not blank.
    const state = queuePanelState("loading", null, rows(3));

    expect(state.kind).toBe("present");
    expect(state.kind === "present" && state.data).toHaveLength(3);
  });

  it("says empty only for a read that finished and found nothing", () => {
    expect(queuePanelState("ready", null, rows(0))).toEqual({ kind: "empty" });
  });

  it("renders rows a finished read returned", () => {
    const state = queuePanelState("ready", null, rows(2));

    expect(state.kind).toBe("present");
    expect(state.kind === "present" && state.data).toHaveLength(2);
  });

  it("carries the API's own words for a failed read", () => {
    expect(queuePanelState("error", "502 from the coordinator", rows(0))).toEqual(
      { kind: "unreadable", detail: "502 from the coordinator" }
    );
  });

  it("never turns a failed read into an empty one, even with no detail", () => {
    // The bug the four-state rule exists to prevent: a failure with nothing
    // to say must not become "there is nothing here".
    for (const blank of ["", "   ", null, undefined]) {
      const state = queuePanelState("error", blank, rows(0));

      expect(state).toEqual({
        kind: "unreadable",
        detail: UNREADABLE_WITHOUT_DETAIL,
      });
    }
  });

  it("drops stale rows when the read failed", () => {
    // A list under an error banner claims those rows are current. After a
    // failed read we do not know that.
    expect(queuePanelState("error", "gateway timeout", rows(5)).kind).toBe(
      "unreadable"
    );
  });

  it("differs from resolvePanel in exactly one case, and that case is deliberate", () => {
    // The two mappings answer the same question opposite ways when a read is
    // in flight over data already on screen, and BOTH answers are load-bearing.
    //
    // `resolvePanel` hides it. That is what `(console)/metrics` needs: its
    // 7/30/90 picker starts a read for a DIFFERENT window, and the payload
    // still in state belongs to the old one. Rendering it under the new
    // window's heading would put real figures under a label they were not
    // measured for — a worse honesty failure than the missing loading state
    // that prompted the fix.
    //
    // `queuePanelState` keeps it. That is what `/admin/requests` needs: its
    // refresh re-reads the SAME query, so the rows on screen are the same
    // rows, and blanking them costs the admin their place in the queue.
    const stale = [{ id: "measured-for-the-previous-window" }];

    expect(resolvePanel({ loading: true, error: null, data: stale }, () => false))
      .toEqual({ kind: "loading" });

    expect(queuePanelState("loading", null, stale).kind).toBe("present");
  });

  it("reaches every state the panel contract defines except present-on-refresh twice", () => {
    // A guard on the mapping as a whole: all four kinds must be reachable,
    // or one of them is dead code and a panel somewhere renders nothing.
    const kinds = new Set([
      queuePanelState("loading", null, rows(0)).kind,
      queuePanelState("ready", null, rows(1)).kind,
      queuePanelState("ready", null, rows(0)).kind,
      queuePanelState("error", "x", rows(0)).kind,
    ]);

    expect(kinds).toEqual(new Set(["loading", "present", "empty", "unreadable"]));
  });
});

describe("queueBadgeCount", () => {
  it("is null while nothing has been established yet", () => {
    expect(queueBadgeCount(queuePanelState("loading", null, rows(0)))).toBeNull();
  });

  it("is null after a failed read — a badge is a claim, and a failure earns none", () => {
    expect(
      queueBadgeCount(queuePanelState("error", "502", rows(0)))
    ).toBeNull();
  });

  it("is a real 0 for a read that succeeded and found nothing pending", () => {
    expect(queueBadgeCount(queuePanelState("ready", null, rows(0)))).toBe(0);
  });

  it("counts the rows a finished read returned", () => {
    expect(queueBadgeCount(queuePanelState("ready", null, rows(4)))).toBe(4);
  });

  it("keeps counting stale rows through a refresh, same as the panel they came from", () => {
    // queuePanelState keeps rows on screen while a refresh is in flight; the
    // badge must agree with the panel it was read from rather than re-deciding
    // on its own and going stale relative to it.
    expect(queueBadgeCount(queuePanelState("loading", null, rows(3)))).toBe(3);
  });
});
