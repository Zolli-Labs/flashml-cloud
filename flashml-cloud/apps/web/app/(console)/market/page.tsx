import { redirect } from "next/navigation";

// /market means the board. Credits — this URL's old occupant — is the
// account's side of the market and lives at /market/credits.
export default function MarketIndex() {
  redirect("/market/prices");
}
