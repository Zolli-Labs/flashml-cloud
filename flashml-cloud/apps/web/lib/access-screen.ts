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

/** The admin-only area. Unlike every other console route, this one does NOT
 * render optimistically while `GET /me` is unanswered: an account that is
 * neither admitted nor admin used to watch the whole access queue paint
 * before the gate replaced it. Nothing leaked — the API refuses these routes
 * on its own and no request fires during the flash — but the cost/benefit
 * that justifies optimism elsewhere runs backwards here. Ordinary pages are
 * loaded constantly by admitted users, so a spinner would punish the
 * majority; the admin queue is opened rarely and only by admins, so one
 * round trip of "loading" costs almost nobody anything. */
export const ADMIN_ROUTE = "/admin";

export type Screen =
  | "console"
  | "onboarding"
  | "pending"
  | "declined"
  | "loading";

/** `undefined` means `GET /me` has not answered yet and renders the
 * console optimistically — see the test for why, and `ADMIN_ROUTE` for the
 * one place that deliberately waits instead. */
export function screenFor(
  access: AccessState | undefined,
  pathname: string
): Screen {
  if (pathname === INVITE_ROUTE) return "console";
  if (
    access === undefined &&
    (pathname === ADMIN_ROUTE || pathname.startsWith(`${ADMIN_ROUTE}/`))
  ) {
    return "loading";
  }
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
