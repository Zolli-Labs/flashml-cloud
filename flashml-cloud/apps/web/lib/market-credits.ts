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
 * Wallet USD values arrive from the API as display strings under the fixed
 * parity policy. This module formats the source ZC integers but never re-prices
 * them locally, so product policy cannot drift between server and browser.
 */
import type {
  CreditsBalance,
  LedgerMovement,
  MarketMatch,
} from "./cloud-api";

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

// -- v2 wallet -------------------------------------------------------------

/** One stat tile on the wallet header. `sub` is the one-line sentence under
 * the value; for lifetime tiles a zero is a TRUE zero (nothing has moved)
 * and the sub says so, because a lifetime sum is not an unmeasured metric. */
export interface WalletTile {
  icon: string;
  label: string;
  valueText: string;
  sub: string;
}

export function walletTiles(balance: CreditsBalance): WalletTile[] {
  const life = balance.lifetime;
  return [
    {
      icon: "spendable",
      label: "ZC spendable",
      valueText: `${formatZc(balance.spendable_zc)} ZC`,
      sub: `$${balance.spendable_usd} · yours to commit to work`,
    },
    {
      icon: "held",
      label: "ZC held in escrow",
      valueText: `${formatZc(balance.held_zc)} ZC`,
      sub: `$${balance.held_usd} · committed against claimed work; settles only for accepted work`,
    },
    {
      icon: "earned",
      label: "earned, lifetime",
      valueText: formatZc(life.earned_zc),
      sub:
        life.earned_zc === 0
          ? "no accepted-work settlements yet"
          : "paid out for accepted work",
    },
    {
      icon: "spent",
      label: "spent, lifetime",
      valueText: formatZc(life.spent_zc),
      sub:
        life.spent_zc === 0
          ? "nothing bought on the market yet"
          : "paid to hosts for your jobs",
    },
  ];
}

/** Counts per reason over the movements currently in view, as chips.
 * Empty ledger yields an empty list — the strip is absent, not "0 of
 * everything". */
export function activityStrip(movements: LedgerMovement[]): string[] {
  const counts = new Map<string, number>();
  for (const m of movements) {
    counts.set(m.reason, (counts.get(m.reason) ?? 0) + 1);
  }
  const label: Record<string, string> = {
    grant: "grant",
    escrow_hold: "hold",
    escrow_release: "release",
    escrow_refund: "refund",
    spent_accepted_work: "settle",
    earned_accepted_work: "settle",
  };
  const out: string[] = [];
  for (const [reason, n] of counts) {
    const word = label[reason] ?? reason;
    out.push(`${n} ${word}${n === 1 ? "" : "s"}`);
  }
  return out;
}

/** A match as a card. The state badge carries the API's vocabulary plus the
 * one-line consequence a buyer needs so `granted` cannot read as
 * "assigned". Held/charged/refunded render only when non-zero. */
export interface MatchCard {
  id: string;
  side: "buyer" | "host";
  capabilityClass: string;
  tasks: number;
  priceText: string;
  state: string;
  stateBadge: string;
  mini: { label: string; valueText: string }[];
}

export function matchCards(matches: {
  as_buyer: MarketMatch[];
  as_host: MarketMatch[];
}): { buyer: MatchCard[]; host: MatchCard[] } {
  const one = (m: MarketMatch, side: "buyer" | "host"): MatchCard => {
    const mini: { label: string; valueText: string }[] = [];
    if (m.held_zc > 0) mini.push({ label: "held", valueText: formatZc(m.held_zc) });
    if (m.charged_zc > 0)
      mini.push({ label: "settled", valueText: formatZc(m.charged_zc) });
    if (m.refunded_zc > 0)
      mini.push({ label: "refunded", valueText: formatZc(m.refunded_zc) });
    return {
      id: m.id,
      side,
      capabilityClass: m.capability_class,
      tasks: m.tasks,
      priceText: `${formatZc(m.agreed_zc_per_hour)} ZC/hour`,
      state: m.state,
      stateBadge: matchStateBadge(m.state),
      mini,
    };
  };
  return {
    buyer: matches.as_buyer.map((m) => one(m, "buyer")),
    host: matches.as_host.map((m) => one(m, "host")),
  };
}

export function matchStateBadge(state: string): string {
  switch (state) {
    case "granted":
      return "granted · entitled, no money moved";
    case "claimed":
      return "claimed · escrow held";
    case "settled":
      return "settled · paid for accepted work";
    case "refunded":
      return "refunded · nothing accepted";
    case "expired":
      return "expired · hold returned";
    default:
      return `state the console does not recognise ("${state}")`;
  }
}

/** A semantic icon key per reason. The component maps these to phosphor
 * icons; lib stays free of React. An unrecognised reason gets a neutral
 * key rather than being mapped onto the nearest known glyph. */
export function movementIcon(reason: string): string {
  switch (reason) {
    case "grant":
      return "grant";
    case "escrow_hold":
      return "hold";
    case "escrow_release":
    case "escrow_refund":
      return "release";
    case "spent_accepted_work":
    case "earned_accepted_work":
      return "settle";
    default:
      return "unknown";
  }
}

/** Whether a ledger amount should read as a credit to spendable (green),
 * a debit (ink), or a self-transfer (muted). The component colours by this
 * verdict; the verdict itself is a fact about which pocket moved. */
export function amountTone(
  movement: LedgerMovement
): "credit" | "debit" | "self" {
  if (movement.legs.every((leg) => leg.mine)) return "self";
  const mine = movement.legs.find((leg) => leg.mine);
  if (!mine) return "self";
  return mine.delta_zc > 0 ? "credit" : "debit";
}

/** A day key for grouping ledger rows under separators. Today reads
 * "Today"; other days read "12 Aug". An unparseable timestamp groups under
 * "Unknown date" rather than throwing or collapsing into today. */
export function dayKey(iso: string): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "Unknown date";
  const d = new Date(ms);
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) return "Today";
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** Group rows (newest first) into day buckets preserving order, so the
 * table can draw a separator between different days. */
export function groupRowsByDay<T extends { at: string }>(
  rows: T[]
): { day: string; rows: T[] }[] {
  const out: { day: string; rows: T[] }[] = [];
  for (const row of rows) {
    const day = dayKey(row.at);
    const last = out[out.length - 1];
    if (last && last.day === day) last.rows.push(row);
    else out.push({ day, rows: [row] });
  }
  return out;
}
