import type { AccessState } from "@/lib/cloud-api";

/** The one console route every access state must reach. Redeeming a link
 * while un-admitted banks the workspace join so it applies on approval;
 * blocking it here would lose the invite. The API's `accept_invite` sits
 * on `current_user`, not `admitted_user`, for the identical reason.
 *
 * Joining and admission are one signal, not two: `acceptInvite`'s `joined`
 * (`lib/cloud-api.ts`) is `true` only for an already-admitted caller, who
 * is added to the pool outright. For anyone else nothing joins yet — the
 * membership is banked on the account's access request and materializes
 * only once an admin approves them. */
export const INVITE_ROUTE = "/pools/join";

export type Screen = "console" | "onboarding" | "pending" | "declined";

/** `undefined` means `GET /me` has not answered yet and renders the
 * console optimistically — see the test for why. */
export function screenFor(
  access: AccessState | undefined,
  pathname: string
): Screen {
  if (pathname === INVITE_ROUTE) return "console";
  switch (access) {
    case "needs_onboarding":
      return "onboarding";
    case "pending":
      return "pending";
    case "declined":
      return "declined";
    default:
      return "console";
  }
}
