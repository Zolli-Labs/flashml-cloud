import { describe, expect, it } from "vitest";

import {
  PANEL_STATE_KINDS,
  PANEL_STATE_MEANING,
  READ_WITHOUT_RESULT,
  UNREADABLE_WITHOUT_DETAIL,
  isEmptyList,
  panelRead,
  resolvePanel,
  resolvePanelState,
  type PanelRead,
  type PanelState,
} from "./panel-state";

/** Built at runtime, never as a literal at module scope — §1.1 forbids
 * fixture-shaped constants sitting in the source. */
function machines(count: number): { id: string }[] {
  return Array.from({ length: count }, (_, i) => ({ id: `machine-${i}` }));
}

const listIsEmpty = (rows: readonly unknown[]) => isEmptyList(rows);

describe("the vocabulary", () => {
  it("is exactly four states", () => {
    expect(PANEL_STATE_KINDS).toEqual([
      "loading",
      "present",
      "empty",
      "unreadable",
    ]);
  });

  it("gives every state a meaning, and no state a meaning it does not have", () => {
    // `PANEL_STATE_MEANING` is typed `Record<PanelStateKind, string>`, so
    // this pair is what makes a fifth variant a failure in both directions:
    // the compiler catches a missing meaning, this catches a stale one.
    expect(new Set(Object.keys(PANEL_STATE_MEANING))).toEqual(
      new Set(PANEL_STATE_KINDS)
    );
  });

  it("says 'empty' and 'unreadable' claim different things", () => {
    expect(PANEL_STATE_MEANING.empty).not.toBe(PANEL_STATE_MEANING.unreadable);
    expect(PANEL_STATE_MEANING.empty).toMatch(/nothing here/i);
    expect(PANEL_STATE_MEANING.unreadable).toMatch(/failed/i);
  });
});

describe("resolvePanelState — the happy path", () => {
  it("carries the data through on a read that returned something", () => {
    const rows = machines(3);
    const state = resolvePanelState<{ id: string }[]>(
      { status: "read", data: rows },
      listIsEmpty
    );

    expect(state.kind).toBe("present");
    expect(state.kind === "present" && state.data).toBe(rows);
  });

  it("reports a successful read of nothing as empty", () => {
    const state = resolvePanelState<{ id: string }[]>(
      { status: "read", data: machines(0) },
      listIsEmpty
    );

    expect(state.kind).toBe("empty");
  });

  it("reports an in-flight read as loading", () => {
    expect(resolvePanelState({ status: "loading" }, listIsEmpty).kind).toBe(
      "loading"
    );
  });
});

describe("resolvePanelState — a failed read is never an empty result", () => {
  it("stays unreadable and keeps the API's own words", () => {
    const state = resolvePanelState<{ id: string }[]>(
      { status: "unreadable", detail: "coordinator unreachable (502)" },
      listIsEmpty
    );

    expect(state.kind).toBe("unreadable");
    expect(state.kind === "unreadable" && state.detail).toBe(
      "coordinator unreachable (502)"
    );
  });

  it("stays unreadable when the failure said nothing at all", () => {
    for (const detail of ["", "   ", "\n\t "]) {
      const state = resolvePanelState<{ id: string }[]>(
        { status: "unreadable", detail },
        listIsEmpty
      );

      expect(state.kind).toBe("unreadable");
      expect(state.kind === "unreadable" && state.detail).toBe(
        UNREADABLE_WITHOUT_DETAIL
      );
    }
  });

  it("never consults isEmpty on a failed read", () => {
    // A predicate that answers "yes, empty" to everything is the shortest
    // way to prove the empty branch is unreachable from a failure: if the
    // resolver looked at it at all, this would come back `empty`.
    let asked = 0;
    const state = resolvePanelState<{ id: string }[]>(
      { status: "unreadable", detail: "timed out" },
      () => {
        asked += 1;
        return true;
      }
    );

    expect(asked).toBe(0);
    expect(state.kind).toBe("unreadable");
  });

  it("never consults isEmpty while the read is still in flight", () => {
    let asked = 0;
    resolvePanelState<{ id: string }[]>({ status: "loading" }, () => {
      asked += 1;
      return true;
    });

    expect(asked).toBe(0);
  });

  it("carries no data on any state but present", () => {
    // The property that stops stale rows rendering under an error banner:
    // three of the four states have nowhere to put them.
    const reads: PanelRead<{ id: string }[]>[] = [
      { status: "loading" },
      { status: "unreadable", detail: "gone" },
      { status: "read", data: machines(0) },
    ];

    for (const read of reads) {
      const state: PanelState<{ id: string }[]> = resolvePanelState(
        read,
        listIsEmpty
      );
      expect(state).not.toHaveProperty("data");
    }
  });
});

describe("isEmptyList", () => {
  it("is true for a collection the API returned with nothing in it", () => {
    expect(isEmptyList(machines(0))).toBe(true);
  });

  it("is false as soon as there is one row", () => {
    expect(isEmptyList(machines(1))).toBe(false);
  });
});

describe("panelRead — the order of precedence", () => {
  it("reports a failure even while a retry is in flight", () => {
    // `error` before `loading`: a loading flag that never clears hides the
    // error forever, which is the WorkspaceGate bug. The reverse costs one
    // frame of a stale message.
    const read = panelRead({
      loading: true,
      error: "coordinator unreachable",
      data: null,
    });

    expect(read.status).toBe("unreadable");
    expect(read.status === "unreadable" && read.detail).toBe(
      "coordinator unreachable"
    );
  });

  it("reports a failure even when a previous read left rows behind", () => {
    // A screen showing rows claims those rows are current.
    const read = panelRead({
      loading: false,
      error: "403",
      data: machines(4),
    });

    expect(read.status).toBe("unreadable");
  });

  it("gives a blank error the fallback sentence rather than dropping it", () => {
    const read = panelRead({ loading: false, error: "", data: machines(0) });

    expect(read.status).toBe("unreadable");
    expect(read.status === "unreadable" && read.detail).toBe(
      UNREADABLE_WITHOUT_DETAIL
    );
  });

  it("treats null and undefined error as no failure", () => {
    expect(
      panelRead({ loading: false, error: null, data: machines(1) }).status
    ).toBe("read");
    expect(
      panelRead({ loading: false, error: undefined, data: machines(1) }).status
    ).toBe("read");
  });

  it("is loading on a first load with nothing yet", () => {
    expect(
      panelRead({ loading: true, error: null, data: null }).status
    ).toBe("loading");
  });

  it("calls a settled read that produced nothing unreadable, not empty", () => {
    // The realistic bug: a fetch threw and the catch forgot to setError.
    // Calling this `empty` would be §1.1's exact failure, arrived at by
    // accident rather than by decision.
    for (const data of [null, undefined]) {
      const read = panelRead<{ id: string }[]>({
        loading: false,
        error: null,
        data,
      });

      expect(read.status).toBe("unreadable");
      expect(read.status === "unreadable" && read.detail).toBe(
        READ_WITHOUT_RESULT
      );
    }
  });

  it("passes a settled, successful read through with its data", () => {
    const rows = machines(2);
    const read = panelRead({ loading: false, error: null, data: rows });

    expect(read.status).toBe("read");
    expect(read.status === "read" && read.data).toBe(rows);
  });

  it("does not confuse a falsy-but-real result with a missing one", () => {
    // 0, "" and false are values the API returned. `null` is not observed.
    expect(panelRead({ loading: false, error: null, data: 0 }).status).toBe(
      "read"
    );
    expect(panelRead({ loading: false, error: null, data: "" }).status).toBe(
      "read"
    );
    expect(panelRead({ loading: false, error: null, data: false }).status).toBe(
      "read"
    );
  });
});

describe("resolvePanel — the call a page makes", () => {
  it("walks a panel through its whole life without ever inventing an empty", () => {
    const seen = (
      [
        { loading: true, error: null, data: null },
        { loading: false, error: null, data: machines(0) },
        { loading: false, error: null, data: machines(2) },
        { loading: false, error: "network down", data: machines(2) },
        { loading: false, error: null, data: null },
      ] as const
    ).map(
      (inputs) =>
        resolvePanel<readonly { id: string }[]>({ ...inputs }, listIsEmpty).kind
    );

    expect(seen).toEqual([
      "loading",
      "empty",
      "present",
      "unreadable",
      "unreadable",
    ]);
  });

  it("honours a predicate that calls a non-empty collection empty", () => {
    // A listing whose array has rows but whose rows are all filtered out is
    // the caller's call to make, not this module's.
    const state = resolvePanel<{ id: string }[]>(
      { loading: false, error: null, data: machines(3) },
      () => true
    );

    expect(state.kind).toBe("empty");
  });
});
