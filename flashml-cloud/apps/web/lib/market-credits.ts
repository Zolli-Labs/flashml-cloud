/** What the credits page is allowed to say, and about what.
 *
 * WHY THIS MODULE EXISTS. The API returns millicredits as integers and the
 * ledger as movements carrying ALL of their legs; every sentence the page
 * shows — the ZC formatting, the movement label, the counterparty — is a
 * decision, and decisions live where a test can reach them (the same split
 * `lib/job-artifacts.ts` documents: markup in the component, judgement
 * here).
 *
 * THE UNIT DISCIPLINE. `formatZc` takes MILLICREDITS and renders them from
 * integer arithmetic only: a millicredit amount passed through a float
 * divide is how "14.200" becomes "14.200000000000001" on a page about
 * money. Three decimals at most, trailing zeros dropped, so 14200 reads
 * "14.2" — the same label `marketplace.price_label` produces server-side.
 *
 * THE COUNTERPARTY DISCIPLINE. A movement without its counterparty is a
 * balance change without a cause, so every row names the other side: the
 * account's own escrow for holds and releases, "the buyer's escrow" / "the
 * host's spendable" across accounts, and "minted" for the grant, which
 * genuinely has no other side. A leg shape this module does not recognise
 * is named raw rather than paraphrased into the nearest known sentence.
 *
 * Nothing here converts ZC to any other denomination. The prices page puts
 * ZC and vendor currencies side by side and never sums them; this module
 * has no function that could, because a helper that cannot exist cannot
 * drift into existing.
 */
import type { LedgerMovement } from "./cloud-api";

/** Millicredits as a ZC string, from integer arithmetic only.
 *
 * 1000 -> "1", 14200 -> "14.2", 1 -> "0.001", -1500 -> "-1.5". The sign is
 * handled once, up front; the remainder is padded to three digits and then
 * trimmed, so the string never claims a precision the amount does not
 * have. */
export function formatZc(millicredits: number): string {
  if (!Number.isFinite(millicredits)) return "—";
  const sign = millicredits < 0 ? "-" : "";
  const abs = Math.trunc(Math.abs(millicredits));
  const whole = Math.floor(abs / 1000);
  const rest = abs % 1000;
  if (rest === 0) return `${sign}${whole}`;
  const frac = String(rest).padStart(3, "0").replace(/0+$/, "");
  return `${sign}${whole}.${frac}`;
}

/** The page-facing label for a movement's reason. An unrecognised reason
 * is quoted back, not mapped onto the nearest known one — the same rule
 * `lib/job-artifacts.ts` applies to an unrecognised storage backend. */
export function movementLabel(reason: string): string {
  switch (reason) {
    case "grant":
      return "Starting grant";
    case "escrow_hold":
      return "Escrow held on a claim";
    case "escrow_release":
      return "Escrow released";
    case "escrow_refund":
      return "Escrow refunded";
    case "spent_accepted_work":
      return "Spent on accepted work";
    case "earned_accepted_work":
      return "Earned on accepted work";
    case "adjustment":
      return "Adjustment";
    default:
      return `Movement the console does not recognise ("${reason}")`;
  }
}

/** The amount this movement moved, in millicredits, from the viewer's
 * point of view — the leg whose magnitude the row is about, never a sum
 * of legs that would cancel to zero for a self-transfer.
 *
 * Holds are read off the escrow leg (what got held), releases and refunds
 * off the spendable leg (what came back), and the two settlement reasons
 * off whichever leg is the viewer's. Null when the legs do not contain
 * what the reason promises — rendered as unknown, never as 0. */
export function movementAmountMzc(movement: LedgerMovement): number | null {
  const mine = movement.legs.filter((leg) => leg.mine);
  const theirs = movement.legs.filter((leg) => !leg.mine);
  const leg = (kind: string, pool: typeof mine) =>
    pool.find((l) => l.kind === kind) ?? null;

  switch (movement.reason) {
    case "grant":
    case "earned_accepted_work":
      return leg("spendable", mine)?.delta_zc ?? null;
    case "spent_accepted_work":
      return leg("escrow", mine)?.delta_zc ?? null;
    case "escrow_hold":
      return leg("escrow", mine)?.delta_zc ?? null;
    case "escrow_release":
    case "escrow_refund":
      return leg("spendable", mine)?.delta_zc ?? null;
    default:
      // One leg: that magnitude. Two: the viewer's own, if any.
      if (mine.length === 1) return mine[0].delta_zc;
      if (theirs.length === 1 && mine.length === 0) return theirs[0].delta_zc;
      return null;
  }
}

/** The other side of a movement, in words. "Minted" for the grant, the
 * account's own two pockets for holds and releases, and the counterparty
 * account's role across accounts. A movement whose legs do not say which
 * side is whose is reported as unnamed rather than guessed. */
export function movementCounterparty(movement: LedgerMovement): string {
  const mine = movement.legs.filter((leg) => leg.mine);
  const theirs = movement.legs.filter((leg) => !leg.mine);

  if (movement.reason === "grant") return "minted — no counterparty";
  if (theirs.length === 0) {
    const kinds = new Set(mine.map((leg) => leg.kind));
    if (kinds.size === 2) {
      return movement.reason === "escrow_hold"
        ? "your spendable → your escrow"
        : "your escrow → your spendable";
    }
    return "within your own account";
  }

  const other = theirs[0];
  if (movement.reason === "earned_accepted_work") {
    return "the buyer's escrow";
  }
  if (movement.reason === "spent_accepted_work") {
    return "the host's spendable";
  }
  return other.kind === "escrow"
    ? "a counterparty's escrow"
    : "a counterparty's spendable";
}

/** One ledger row, ready for markup. `amountText` is null when the legs
 * did not carry the magnitude — the page renders an em dash, because a
 * fabricated 0 in a money column is indistinguishable from a real one. */
export interface LedgerRow {
  cursor: number;
  at: string;
  label: string;
  amountText: string | null;
  counterparty: string;
  ref: string | null;
}

export function ledgerRows(movements: LedgerMovement[]): LedgerRow[] {
  return movements.map((movement) => {
    const amount = movementAmountMzc(movement);
    return {
      cursor: movement.cursor,
      at: movement.created_at,
      label: movementLabel(movement.reason),
      amountText:
        amount === null
          ? null
          : `${amount > 0 ? "+" : ""}${formatZc(amount)} ZC`,
      counterparty: movementCounterparty(movement),
      ref:
        movement.ref_type && movement.ref_id
          ? `${movement.ref_type} ${movement.ref_id}`
          : null,
    };
  });
}
