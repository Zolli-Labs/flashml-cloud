/**
 * `POST /v1alpha1/device/approve` answers 404 for two different refusals,
 * both surfacing to the client as the same `NotFound` error type carrying
 * the API's `detail` string verbatim as its message (`lib/cloud-api.ts`):
 *
 * - `"unknown code"` — the user_code itself is bad, expired, or already
 *   consumed under a different account.
 * - `"unknown pool"` — the `?pool=` link's pool_id is a real pool, but the
 *   signed-in caller is not (yet) a member of it (`fetch_pool_for_member`'s
 *   404 doctrine, `db.py`) — reachable from `ConnectPanel`'s "Approve at
 *   .../activate?pool=<poolId>" caption before the invite is accepted.
 *
 * Collapsing both into "we couldn't find that code, get a fresh one" is a
 * dead loop for the second case: the code was fine, the caller just isn't
 * in the pool yet, and no amount of re-running `flashnode login` fixes
 * that. This selects the copy for each case from the raw `detail` string
 * alone, so it can be pinned without going through a fetch mock.
 */
export function approveNotFoundMessage(detail: string): string {
  if (detail === "unknown pool") {
    return "You're not a member of that pool yet — accept the pool invite first, then approve here.";
  }
  return "We couldn't find that code. Check it against the laptop's screen — codes are only valid for a few minutes.";
}
