/** How a workspace member is labelled in the People table.
 *
 * The table used to print `user_id` under every name. A UUID identifies a
 * row to the database, not a teammate to a human, and it was the only
 * secondary line — so the one view whose purpose is "who is in this
 * workspace" answered with 36 hex characters.
 *
 * The genuinely useful secondary line is an email, which is what the admin
 * queue shows. `PoolMember` does not carry one, and adding it would publish
 * every member's address to every other member — a product and privacy
 * decision, not a rendering fix. Until that is decided, a named member gets
 * no subtitle at all, and an unnamed one gets a short prefix purely so two
 * of them are distinguishable.
 */
export interface MemberIdentity {
  user_id: string;
  display_name: string | null;
}

function named(member: MemberIdentity): string | null {
  const name = member.display_name?.trim();
  return name ? name : null;
}

export function memberName(member: MemberIdentity): string {
  return named(member) ?? "unnamed";
}

export function memberSubtitle(member: MemberIdentity): string {
  return named(member) ? "" : member.user_id.slice(0, 8);
}
