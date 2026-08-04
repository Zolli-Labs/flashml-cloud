"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Check, Copy, Warning } from "@phosphor-icons/react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { ConnectPanel } from "@/components/pools/ConnectPanel";
import { isOnline, relativeTime } from "@/lib/machine-status";
import {
  MACHINE_BADGE_LABELS,
  MACHINE_BADGE_STYLES,
  machineBadge,
} from "@/lib/machine-badge";
import { formatInviteState } from "@/lib/pool-invite-state";
import {
  ApiError,
  NotAuthenticated,
  NotFound,
  bindMachineToPool,
  createPoolInvite,
  getMe,
  getPool,
  getPoolInviteState,
  listMachines,
  revokePoolInvites,
  unbindMachineFromPool,
  type Machine,
  type Pool,
  type PoolInviteState,
  type PoolMember,
} from "@/lib/cloud-api";

type LoadState = "loading" | "ready" | "not-found" | "error";

export default function PoolDetailPage({
  params,
}: {
  params: Promise<{ poolId: string }>;
}) {
  const { poolId } = use(params);
  const router = useRouter();

  const [pool, setPool] = useState<Pool | null>(null);
  const [members, setMembers] = useState<PoolMember[]>([]);
  // The signed-in user's own profile id — compared against `pool.owner_id`
  // to decide whether the invite section renders. Same field the account
  // page shows under "User ID" (`Profile.id`, from `getMe()`).
  const [viewerId, setViewerId] = useState<string | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([getPool(poolId), getMe()])
      .then(([detail, me]) => {
        setPool(detail.pool);
        setMembers(detail.members);
        setViewerId(me.id);
        setState("ready");
        setErrorMessage(null);
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push(`/sign-in?next=${encodeURIComponent(`/pools/${poolId}`)}`);
          return;
        }
        if (err instanceof NotFound) {
          // The API 404s for "does not exist" and "exists but you're not a
          // member" identically (fetch_pool_for_member's own doctrine) — so
          // this must not be reworded into an access-denied message that
          // would confirm the id is real to someone who isn't in the pool.
          setState("not-found");
          return;
        }
        setErrorMessage(
          err instanceof Error ? err.message : "Couldn't load this pool."
        );
        setState("error");
      });
  }, [poolId, router]);

  useEffect(() => {
    load();
  }, [load]);

  if (state === "loading") {
    return (
      <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        <div className="skeleton h-32 rounded-lg" />
      </div>
    );
  }

  if (state === "not-found") {
    return (
      <Shell>
        <p className="text-sm text-muted-foreground">
          This pool doesn&apos;t exist, or you&apos;re not a member.
        </p>
        <Link href="/pools" className="text-sm text-primary hover:underline">
          Back to pools
        </Link>
      </Shell>
    );
  }

  if (state === "error" || !pool) {
    return (
      <Shell>
        <Warning className="h-5 w-5 text-destructive" weight="fill" />
        <p className="text-sm text-muted-foreground">{errorMessage}</p>
        <button
          type="button"
          onClick={load}
          className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-white/[0.06]"
        >
          Try again
        </button>
      </Shell>
    );
  }

  const isOwner = viewerId !== null && viewerId === pool.owner_id;

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <Link
        href="/pools"
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Pools
      </Link>

      <div className="mt-4">
        <h1 className="title">{pool.name}</h1>
        <p className="mt-1.5 text-sm text-muted-foreground">
          {members.length} member{members.length === 1 ? "" : "s"} · created{" "}
          {relativeTime(pool.created_at)}
        </p>
      </div>

      <div className="mt-6 overflow-x-auto">
        <table className="w-full min-w-[560px] text-left">
          <thead>
            <tr className="border-b border-border">
              {["Member", "Machines", "Online", "Joined"].map((h) => (
                <th key={h} className="label-caps px-3 py-2 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {members.map((m) => (
              <MemberRow
                key={m.user_id}
                member={m}
                isOwner={m.user_id === pool.owner_id}
              />
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-8">
        <YourMachinesSection poolId={pool.id} poolName={pool.name} />
      </div>

      <div id="connect-panel" className="mt-8">
        <h2 className="text-sm font-semibold">Connect a machine</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          No spare laptop? Point a Colab notebook or a rented pod at this
          pool instead.
        </p>
        <div className="mt-4">
          <ConnectPanel poolId={pool.id} />
        </div>
      </div>

      {/* Owner only — the API's own doctrine on `POST
          /pools/{id}/invites` (404, not 403, for a non-owner member). This
          client-side check is a convenience, not the boundary: a non-owner
          who somehow reached this button would still just get that 404. */}
      {isOwner && (
        <div className="mt-8">
          <InviteSection poolId={pool.id} />
        </div>
      )}
    </div>
  );
}

function MemberRow({
  member,
  isOwner,
}: {
  member: PoolMember;
  isOwner: boolean;
}) {
  return (
    <tr>
      <td className="px-3 py-3">
        <span className="min-w-0">
          <span className="block truncate text-sm">
            {member.display_name || "unnamed"}
            {isOwner && (
              <span className="label-caps ml-2 align-middle">owner</span>
            )}
          </span>
          <span className="meta block truncate">{member.user_id}</span>
        </span>
      </td>
      <td className="meta px-3 py-3">{member.machine_count}</td>
      <td className="meta px-3 py-3">{member.machines_online}</td>
      <td className="meta px-3 py-3 whitespace-nowrap">
        {relativeTime(member.joined_at)}
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Your machines — per-device opt-in
// ---------------------------------------------------------------------------

/** `listMachines()` is scoped to the caller by the API itself, so this is
 * always "your machines", never every machine bound to the pool — the
 * member table's "Machines"/"Online" columns already summarise that in
 * aggregate, one row per member, counting only machines actually bound to
 * this pool (not every machine the member happens to own). */
function YourMachinesSection({
  poolId,
  poolName,
}: {
  poolId: string;
  poolName: string;
}) {
  const [machines, setMachines] = useState<Machine[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">(
    "loading"
  );
  const [error, setError] = useState<string | null>(null);
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());

  const load = useCallback(() => {
    listMachines()
      .then((r) => {
        // A revoked machine's token is dead — it can never claim work, so
        // "opt it into this pool" is meaningless. Filtered here, not just
        // styled differently, because the API still accepts a bind for one
        // (204: bindMachineToPool only checks ownership, not status), and a
        // checkable row would silently misrepresent this pool's real
        // capacity to every member who can see it.
        setMachines(r.filter((m) => m.status !== "revoked"));
        setState("ready");
        setError(null);
      })
      .catch((err) => {
        setError(
          err instanceof Error ? err.message : "Couldn't load your machines."
        );
        setState("error");
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Optimistic, with revert on failure — the checkbox flips immediately
  // rather than waiting a round trip, and flips back with a toast if the
  // API refuses. Same "confirm, then reflect it in state" shape the
  // machines page's revoke button uses, just eagerly instead of after the
  // fact, since a toggle (unlike an irreversible revoke) is cheap to undo
  // on screen.
  async function toggle(machine: Machine, bound: boolean) {
    const label = machine.name || machine.node_id;
    setPendingIds((prev) => new Set(prev).add(machine.id));
    setMachines((prev) =>
      prev.map((m) =>
        m.id !== machine.id
          ? m
          : {
              ...m,
              pools: bound
                ? m.pools.filter((p) => p.id !== poolId)
                : [...m.pools, { id: poolId, name: poolName }],
            }
      )
    );
    try {
      if (bound) {
        await unbindMachineFromPool(poolId, machine.id);
      } else {
        await bindMachineToPool(poolId, machine.id);
      }
    } catch {
      setMachines((prev) =>
        prev.map((m) =>
          m.id !== machine.id
            ? m
            : {
                ...m,
                pools: bound
                  ? [...m.pools, { id: poolId, name: poolName }]
                  : m.pools.filter((p) => p.id !== poolId),
              }
        )
      );
      toast.error(`Couldn't ${bound ? "remove" : "add"} ${label}`, {
        description: "This pool is unchanged. Try again.",
      });
    } finally {
      setPendingIds((prev) => {
        const next = new Set(prev);
        next.delete(machine.id);
        return next;
      });
    }
  }

  return (
    <section>
      <h2 className="text-sm font-semibold">Your machines</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Opt your own machines into this pool&apos;s work. A machine serves
        no pool until it&apos;s ticked in here, even if you own it.
      </p>

      <div className="mt-3">
        {state === "loading" ? (
          <div className="space-y-px">
            <div className="skeleton h-11" />
            <div className="skeleton h-11" />
          </div>
        ) : state === "error" ? (
          <div className="flex items-center gap-2 py-2 text-sm text-destructive">
            <Warning className="h-4 w-4 shrink-0" weight="fill" />
            <span>{error}</span>
            <button
              type="button"
              onClick={load}
              className="text-muted-foreground hover:text-foreground"
            >
              Try again
            </button>
          </div>
        ) : machines.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No machines on your account yet.{" "}
            <a
              href="#connect-panel"
              className="text-primary hover:underline"
            >
              Connect one below.
            </a>
          </p>
        ) : (
          <div className="divide-y divide-border">
            {machines.map((m) => (
              <MachineToggleRow
                key={m.id}
                machine={m}
                // `?? []`: same api/web deploy-race insurance as
                // machines/page.tsx's pool-chip list — a response missing
                // `pools` must read as "not bound", never throw.
                bound={(m.pools ?? []).some((p) => p.id === poolId)}
                pending={pendingIds.has(m.id)}
                onToggle={toggle}
              />
            ))}
          </div>
        )}
      </div>

      {machines.length > 0 && (
        <p className="mt-2.5 text-xs text-muted-foreground">
          Takes effect within ~30s while the agent is running.
        </p>
      )}
    </section>
  );
}

function MachineToggleRow({
  machine,
  bound,
  pending,
  onToggle,
}: {
  machine: Machine;
  bound: boolean;
  pending: boolean;
  onToggle: (machine: Machine, bound: boolean) => void;
}) {
  const badge = machineBadge(machine);
  // `YourMachinesSection` already filters revoked machines out before this
  // ever renders, but the `!revoked &&` guard stays anyway — defense in
  // depth against a future caller of this row that doesn't, same
  // derivation `machines/page.tsx`'s `MachineRow` uses for its own dot.
  const revoked = machine.status === "revoked";
  const online = !revoked && isOnline(machine.last_seen_at);
  const label = machine.name || machine.node_id;

  return (
    <label className="flex cursor-pointer items-center gap-3 py-2.5 text-sm">
      <input
        type="checkbox"
        checked={bound}
        disabled={pending}
        onChange={() => onToggle(machine, bound)}
        aria-label={`${bound ? "Remove" : "Add"} ${label} ${bound ? "from" : "to"} this pool`}
        className="h-4 w-4 shrink-0 rounded border-border accent-primary disabled:opacity-50"
      />
      <span
        className="status-dot"
        data-state={online ? "live" : undefined}
        style={{
          background: online ? "var(--node-green)" : "oklch(1 0 0 / 0.25)",
        }}
      />
      <span className="min-w-0 flex-1 truncate font-mono">{label}</span>
      <Badge variant="outline" className={MACHINE_BADGE_STYLES[badge]}>
        {MACHINE_BADGE_LABELS[badge]}
      </Badge>
    </label>
  );
}

// ---------------------------------------------------------------------------
// Invite section — owner only, a single standing capped link
// ---------------------------------------------------------------------------

function InviteSection({ poolId }: { poolId: string }) {
  const [inviteState, setInviteState] = useState<PoolInviteState | null>(
    null
  );
  const [state, setState] = useState<"loading" | "ready" | "error">(
    "loading"
  );
  const [link, setLink] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(() => {
    getPoolInviteState(poolId)
      .then((s) => {
        setInviteState(s);
        setState("ready");
      })
      .catch(() => {
        setState("error");
      });
  }, [poolId]);

  useEffect(() => {
    load();
  }, [load]);

  // Regenerate covers both "there's no link yet" and "replace the current
  // one" with the same call — revoking a pool with nothing outstanding is
  // a no-op on the API side (`revokePoolInvites`'s own contract), so there
  // is no separate "create" action to keep in sync with this one.
  async function regenerate() {
    setWorking(true);
    setError(null);
    setCopied(false);
    try {
      await revokePoolInvites(poolId);
      // The revoke above just succeeded, so whatever invite state and
      // link were on screen before this call now describe something
      // dead. Clear them here — immediately, before the mint even
      // starts — rather than in the catch below: if the revoke itself
      // had thrown, execution would never reach this line, and the
      // still-valid previous link stays displayed instead of vanishing
      // for a failure that didn't actually touch it. But once we're
      // past the revoke, a mint failure below must not leave a "N uses
      // left" description or a Copy button in front of a link that no
      // longer works.
      setInviteState(null);
      setLink(null);
      const { token } = await createPoolInvite(poolId, { uses: 10 });
      setLink(`${window.location.origin}/pools/join?token=${token}`);
      // Best effort: the new link is already in hand even if this refresh
      // fails, so a failure here doesn't roll anything back — it just
      // leaves the state line reading "no active invite" until the next
      // load, rather than a stale description of the revoked one.
      try {
        setInviteState(await getPoolInviteState(poolId));
      } catch {
        // leave inviteState null
      }
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : "Couldn't regenerate the invite link. Try again."
      );
    } finally {
      setWorking(false);
    }
  }

  async function revoke() {
    setWorking(true);
    try {
      await revokePoolInvites(poolId);
      setInviteState(null);
      setLink(null);
      toast.success("Invite link revoked", {
        description: "It can no longer be used to join this pool.",
      });
    } catch {
      toast.error("Couldn't revoke the invite link", {
        description: "Try again.",
      });
    } finally {
      setWorking(false);
    }
  }

  async function copy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      toast.success("Invite link copied");
    } catch {
      toast.error("Your browser blocked clipboard access");
    }
  }

  return (
    <section className="panel p-5">
      <h2 className="text-sm font-semibold">Invite a teammate</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        A standing link — anyone who opens it and signs in joins this pool.
        Treat it like a password: it&apos;s good for the uses and time shown
        below, and Regenerate invalidates whatever copy is currently out.
      </p>

      {state === "loading" ? (
        <div className="skeleton mt-3 h-4 w-40" />
      ) : state === "error" ? (
        <p className="mt-3 text-xs text-destructive">
          Couldn&apos;t load the current invite state.
        </p>
      ) : inviteState ? (
        <p className="mt-3 text-xs text-muted-foreground">
          {formatInviteState(inviteState)}
        </p>
      ) : (
        <p className="mt-3 text-xs text-muted-foreground">
          No active invite link.
        </p>
      )}

      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}

      {link && (
        <>
          <div className="mt-3 flex items-center gap-2">
            <Input
              readOnly
              value={link}
              onFocus={(e) => e.currentTarget.select()}
              aria-label="Invite link"
              className="font-mono text-xs"
            />
            <button
              type="button"
              onClick={copy}
              aria-label={copied ? "Copied" : "Copy invite link"}
              className="shrink-0 rounded-md border border-border p-2 text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
            >
              {copied ? (
                <Check
                  size={14}
                  weight="bold"
                  className="text-[var(--node-green)]"
                />
              ) : (
                <Copy size={14} />
              )}
            </button>
          </div>
          <p className="mt-2.5 flex items-start gap-1.5 text-xs text-[var(--warning)]">
            <Warning className="mt-0.5 h-3.5 w-3.5 shrink-0" weight="fill" />
            <span>
              This link is shown once. Copy it now — FlashML can&apos;t show
              it to you again.
            </span>
          </p>
        </>
      )}

      <div className="mt-3 flex items-center gap-3">
        <button
          type="button"
          onClick={regenerate}
          disabled={working}
          className="interactive rounded-md bg-primary px-3.5 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {working ? "Working…" : "Regenerate"}
        </button>

        {inviteState && (
          <AlertDialog>
            <AlertDialogTrigger
              render={
                <button
                  type="button"
                  disabled={working}
                  className="rounded-md px-2.5 py-1.5 text-sm text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Revoke
                </button>
              }
            />
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Revoke this invite link?</AlertDialogTitle>
                <AlertDialogDescription>
                  Anyone still holding it can no longer use it to join this
                  pool. Members already in the pool are unaffected.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Keep it</AlertDialogCancel>
                <AlertDialogAction
                  disabled={working}
                  onClick={revoke}
                  className="bg-destructive/15 text-destructive hover:bg-destructive/25"
                >
                  {working ? "Revoking…" : "Revoke"}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        )}
      </div>
    </section>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <div className="flex flex-col items-center gap-3 rounded-lg border border-border bg-surface py-10 text-center">
        {children}
      </div>
    </div>
  );
}
