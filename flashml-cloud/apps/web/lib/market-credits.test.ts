import { describe, expect, it } from "vitest";

import type { CreditsBalance, LedgerMovement } from "./cloud-api";
import {
  amountTone,
  formatZc,
  groupRowsByDay,
  ledgerRows,
  movementAmountMzc,
  movementCounterparty,
  movementIcon,
  movementLabel,
  walletTiles,
} from "./market-credits";

function movement(
  over: Partial<LedgerMovement> & { legs: LedgerMovement["legs"] }
): LedgerMovement {
  return {
    cursor: 1,
    created_at: "2026-08-12T00:00:00Z",
    reason: "grant",
    ref_type: null,
    ref_id: null,
    ...over,
  };
}

describe("formatZc — integer arithmetic only", () => {
  it("drops the fraction when there is none", () => {
    expect(formatZc(1000)).toBe("1");
    expect(formatZc(250_000)).toBe("250");
    expect(formatZc(0)).toBe("0");
  });

  it("keeps exactly the precision the amount has", () => {
    expect(formatZc(14_200)).toBe("14.2");
    expect(formatZc(1)).toBe("0.001");
    expect(formatZc(10_050)).toBe("10.05");
    expect(formatZc(-1500)).toBe("-1.5");
  });

  it("never produces float noise", () => {
    // 0.1+0.2 territory: a float divide would smear this.
    expect(formatZc(300)).toBe("0.3");
    expect(formatZc(4_700)).toBe("4.7");
  });

  it("answers an em dash for a non-number, never NaN", () => {
    expect(formatZc(Number.NaN)).toBe("—");
  });
});

describe("walletTiles", () => {
  it("keeps API-provided USD values beside spendable and escrow ZC", () => {
    const balance: CreditsBalance = {
      spendable_zc: 10_125,
      held_zc: 2_500,
      spendable_usd: "10.13",
      held_usd: "2.50",
      usd_per_zc: "1.00",
      lifetime: {
        earned_zc: 0,
        spent_zc: 0,
        granted_zc: 0,
        refunded_zc: 0,
      },
    };

    const tiles = walletTiles(balance);
    expect(tiles[0]).toMatchObject({ valueText: "10.125 ZC" });
    expect(tiles[0].sub).toContain("$10.13");
    expect(tiles[1].sub).toContain("$2.50");
  });
});

describe("movementLabel", () => {
  it("names every reason the ledger writes", () => {
    expect(movementLabel("grant")).toBe("Starting grant");
    expect(movementLabel("escrow_hold")).toBe("Escrow held on a claim");
    expect(movementLabel("earned_accepted_work")).toBe(
      "Earned on accepted work"
    );
    expect(movementLabel("spent_accepted_work")).toBe(
      "Spent on accepted work"
    );
    expect(movementLabel("escrow_refund")).toBe("Escrow refunded");
  });

  it("quotes an unrecognised reason back instead of guessing", () => {
    expect(movementLabel("airdrop")).toContain('"airdrop"');
    expect(movementLabel("airdrop")).toContain("does not recognise");
  });
});

describe("movementAmountMzc — the leg the row is about", () => {
  it("reads a hold off the escrow leg, not the cancelling sum", () => {
    const amount = movementAmountMzc(
      movement({
        reason: "escrow_hold",
        legs: [
          { kind: "spendable", delta_zc: -1000, mine: true },
          { kind: "escrow", delta_zc: 1000, mine: true },
        ],
      })
    );
    expect(amount).toBe(1000);
  });

  it("reads a settlement off the viewer's own leg, on each side", () => {
    const host = movementAmountMzc(
      movement({
        reason: "earned_accepted_work",
        legs: [
          { kind: "spendable", delta_zc: 1000, mine: true },
          { kind: "escrow", delta_zc: -1000, mine: false },
        ],
      })
    );
    const buyer = movementAmountMzc(
      movement({
        reason: "spent_accepted_work",
        legs: [
          { kind: "escrow", delta_zc: -1000, mine: true },
          { kind: "spendable", delta_zc: 1000, mine: false },
        ],
      })
    );
    expect(host).toBe(1000);
    expect(buyer).toBe(-1000);
  });

  it("is null, not 0, when the legs do not carry the promised magnitude", () => {
    expect(
      movementAmountMzc(
        movement({ reason: "escrow_hold", legs: [] })
      )
    ).toBeNull();
  });
});

describe("movementCounterparty — the other side is the point", () => {
  it("names the grant as minted", () => {
    expect(
      movementCounterparty(
        movement({
          reason: "grant",
          legs: [{ kind: "spendable", delta_zc: 250_000, mine: true }],
        })
      )
    ).toBe("minted — no counterparty");
  });

  it("names a hold as a transfer between the account's own pockets", () => {
    expect(
      movementCounterparty(
        movement({
          reason: "escrow_hold",
          legs: [
            { kind: "spendable", delta_zc: -1000, mine: true },
            { kind: "escrow", delta_zc: 1000, mine: true },
          ],
        })
      )
    ).toBe("your spendable → your escrow");
  });

  it("names the buyer's escrow on the host side of a settlement", () => {
    expect(
      movementCounterparty(
        movement({
          reason: "earned_accepted_work",
          legs: [
            { kind: "spendable", delta_zc: 1000, mine: true },
            { kind: "escrow", delta_zc: -1000, mine: false },
          ],
        })
      )
    ).toBe("the buyer's escrow");
  });

  it("names the host's spendable on the buyer side", () => {
    expect(
      movementCounterparty(
        movement({
          reason: "spent_accepted_work",
          legs: [
            { kind: "escrow", delta_zc: -1000, mine: true },
            { kind: "spendable", delta_zc: 1000, mine: false },
          ],
        })
      )
    ).toBe("the host's spendable");
  });
});

describe("ledgerRows", () => {
  it("renders an em dash amount where the magnitude is missing", () => {
    const [row] = ledgerRows([
      movement({ reason: "escrow_hold", legs: [] }),
    ]);
    expect(row.amountText).toBeNull();
  });

  it("signs the amount from the viewer's perspective", () => {
    const [row] = ledgerRows([
      movement({
        reason: "earned_accepted_work",
        legs: [
          { kind: "spendable", delta_zc: 1000, mine: true },
          { kind: "escrow", delta_zc: -1000, mine: false },
        ],
      }),
    ]);
    expect(row.amountText).toBe("+1 ZC");
  });
});

describe("v2 wallet helpers", () => {
  it("maps reasons to stable icon keys and unknowns to a neutral key", () => {
    expect(movementIcon("grant")).toBe("grant");
    expect(movementIcon("escrow_hold")).toBe("hold");
    expect(movementIcon("earned_accepted_work")).toBe("settle");
    expect(movementIcon("airdrop")).toBe("unknown");
  });

  it("tones a cross-account credit as credit and a self-transfer as self", () => {
    expect(
      amountTone(
        movement({
          reason: "earned_accepted_work",
          legs: [
            { kind: "spendable", delta_zc: 1000, mine: true },
            { kind: "escrow", delta_zc: -1000, mine: false },
          ],
        })
      )
    ).toBe("credit");
    expect(
      amountTone(
        movement({
          reason: "escrow_hold",
          legs: [
            { kind: "spendable", delta_zc: -1000, mine: true },
            { kind: "escrow", delta_zc: 1000, mine: true },
          ],
        })
      )
    ).toBe("self");
  });

  it("groups rows into day buckets preserving order", () => {
    // Identical instants always share a key; January vs August are different
    // days in every timezone, so the grouping is deterministic.
    const groups = groupRowsByDay([
      { at: "2026-08-12T09:00:00Z" },
      { at: "2026-08-12T09:00:00Z" },
      { at: "2026-01-01T00:00:00Z" },
    ]);
    expect(groups).toHaveLength(2);
    expect(groups[0].rows).toHaveLength(2);
    expect(groups[1].rows).toHaveLength(1);
  });
});
