# Pool ops runbook: removing a member, revoking invites, un-admitting an account

**There is no route for any of this yet.** Membership is the boundary in
front of unsandboxed trusted workers (`--runner trusted` — see
[join-a-pool-colab.md](join-a-pool-colab.md) and
[join-a-pool-runpod.md](join-a-pool-runpod.md)), and for the alpha the only
way to revoke it is direct SQL against the database. This is deliberate,
not an oversight: shipping the runbook now and the routes later keeps the
alpha unblocked without pretending a self-serve admin surface exists. If you
find yourself running these often, that is the signal to build the routes.

Every statement below runs in the **Supabase SQL editor**, signed in with
the **service role** (the project's Supabase dashboard → SQL Editor — not
the app's own Postgres user, and not anything exposed to the console or the
API). That is the same privilege level the API's own `service_key` runs
with; there is no narrower path today.

## What happens after you run one of these

None of this is instant in the sense of "the worker stops mid-task." The
proxy re-resolves pool membership from the database and stamps it onto
**every** `register` and `heartbeat` call a machine makes
(`pool_ids_for_machine`, read fresh on each call — see
`apps/api/flashml_cloud_api/app.py`'s `register_node` and `node_heartbeat`).
So the removal reaches a worker at its **next heartbeat**, not on a fixed
schedule and not requiring an agent restart. This is the same mechanism
`e2e/test_pool_scoping.py`'s
`test_pool_membership_revoked_by_heartbeat_is_refused_on_next_claim` proves
end to end: a heartbeat that drops a pool from `capabilities.pools` is
refused on its very next claim for that pool's work.

Two things follow from "next heartbeat," not "instantly":
- **In-flight leases are not killed.** A task the machine already claimed
  before the removal runs to completion (or expires on its own) under the
  lease it was granted. Revocation stops *new* claims, not work already in
  progress.
- **A dead or disconnected machine will not see the change until it
  reconnects.** If you need to be certain a specific machine has stopped
  before doing anything else, check the pool's member/machine list in the
  console (`/pools`) after a heartbeat interval has passed, or ask the
  member to confirm the `flashnode work` process has exited or is refusing
  claims.

## Remove a member from a pool

Run in the Supabase SQL editor, service role:

```sql
delete from pool_members
 where pool_id = '<pool-uuid>'
   and user_id = '<user-uuid>';
```

Find the ids first from the console (`/pools/<id>` shows the roster) or:

```sql
select pm.pool_id, pm.user_id, p.name as pool_name
  from pool_members pm
  join pools p on p.id = pm.pool_id
 where p.id = '<pool-uuid>';
```

What happens next: the removed user's machines stop being eligible for that
pool's work at their next heartbeat (see above). The user still exists and
can still be re-invited later; this only removes the one membership row.

## Revoke all outstanding invites for a pool

Use this when an invite link may have leaked, or you're locking a pool down
and don't want any pending link to still work:

```sql
delete from pool_invites
 where pool_id = '<pool-uuid>';
```

What happens next: every invite link minted for that pool stops working
immediately — invite consumption checks the row at request time, so there
is no propagation delay the way there is for machine membership. Anyone
already admitted through a since-deleted invite keeps their membership;
this does not remove existing members, only unused/future invite links. To
also remove someone already in the pool, use the member-removal statement
above.

## Un-admit an account

Un-admitting an account puts it back behind the invite-only admission gate
(`profile_is_admitted`) that fronts `/me` and four other gated routes — the
account cannot submit jobs, create or join pools, or reach anything gated
on admission until it is re-admitted:

```sql
update public.profiles
   set admitted_at = null
 where id = '<user-uuid>';
```

What happens next: gated API routes start refusing this user (403) as soon
as their next request is evaluated — there is no caching layer in front of
`profile_is_admitted`, so this takes effect immediately, not at a
heartbeat. Any machine this account owns is a separate concern from
admission: un-admitting does **not** by itself remove the account's pool
memberships or stop its machines from heartbeating. If the account is being
removed for cause, also run the member-removal statement above for every
pool it belongs to:

```sql
select pool_id from pool_members where user_id = '<user-uuid>';
```

then delete each of those rows, or:

```sql
delete from pool_members where user_id = '<user-uuid>';
```

to remove the account from every pool it is in, in one statement.

## Summary table

| Action | Statement | Where | Takes effect |
|---|---|---|---|
| Remove one member from one pool | `delete from pool_members where pool_id = ... and user_id = ...;` | Supabase SQL editor, service role | At that machine's next heartbeat (proven by the e2e revocation test); in-flight leases finish or expire |
| Revoke all invites for a pool | `delete from pool_invites where pool_id = ...;` | Supabase SQL editor, service role | Immediately — checked at consumption time |
| Un-admit an account | `update public.profiles set admitted_at = null where id = ...;` | Supabase SQL editor, service role | Immediately on the account's next API request; does not itself touch pool membership or running machines |

**Routes for all of the above are future work.** Nothing in this document
should be read as a promise that the console can do this today — it
cannot.
