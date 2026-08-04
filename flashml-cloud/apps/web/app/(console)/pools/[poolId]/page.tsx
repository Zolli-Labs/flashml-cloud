"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Check, Copy, Warning } from "@phosphor-icons/react";
import { toast } from "sonner";
import { Input } from "@/components/ui/input";
import { relativeTime } from "@/lib/machine-status";
import {
  ApiError,
  NotAuthenticated,
  NotFound,
  createPoolInvite,
  getMe,
  getPool,
  type Pool,
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

function InviteSection({ poolId }: { poolId: string }) {
  const [link, setLink] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  async function create() {
    setCreating(true);
    setError(null);
    setCopied(false);
    try {
      const { token } = await createPoolInvite(poolId);
      // Built here, not stored anywhere — the token exists in this
      // component's state for exactly as long as this card is on screen,
      // matching the API's own "shown once" contract for the raw value.
      setLink(`${window.location.origin}/pools/join?token=${token}`);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : "Couldn't create an invite link. Try again."
      );
    } finally {
      setCreating(false);
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
        Mint a one-time link. Anyone who opens it and signs in joins this
        pool.
      </p>

      {error && <p className="mt-3 text-xs text-destructive">{error}</p>}

      {link ? (
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
          <button
            type="button"
            onClick={create}
            disabled={creating}
            className="mt-3 text-xs text-muted-foreground hover:text-foreground disabled:opacity-40"
          >
            {creating ? "Creating…" : "Create another link"}
          </button>
        </>
      ) : (
        <button
          type="button"
          onClick={create}
          disabled={creating}
          className="interactive mt-3 rounded-md bg-primary px-3.5 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {creating ? "Creating…" : "Create invite link"}
        </button>
      )}
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
