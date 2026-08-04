// The one-signal model's honest copy for a banked (not-yet-applied) pool
// join — `acceptInvite`'s `joined: false`. Pulled into its own pure
// function, pinned by test, because both entry points on `/pools/join`
// (the auto-redeem effect for a clicked link, and the paste-a-code
// fallback) render a sentence built from this and must never drift out of
// sync with each other or with the one-signal model: nothing joined yet,
// so no copy anywhere may imply partial membership.

/** `{name}` here is the pool's name, exactly as `acceptInvite` returns it. */
export function bankedJoinTail(name: string): string {
  return `You'll join ${name} as soon as your access is approved.`;
}
