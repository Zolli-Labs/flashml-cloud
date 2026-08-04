"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowClockwise, Warning } from "@phosphor-icons/react";
import { toast } from "sonner";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { relativeTime } from "@/lib/machine-status";
import {
  ApiError,
  NotAuthenticated,
  createPool,
  listPools,
  type PoolSummary,
} from "@/lib/cloud-api";

const POLL_MS = 15_000;

export default function PoolsPage() {
  const router = useRouter();
  const [pools, setPools] = useState<PoolSummary[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    listPools()
      .then((r) => {
        setPools(r);
        setState("ready");
        setError(null);
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          // 401 means signed out, not "you have no pools". Rendering the
          // empty state here would tell a signed-out user their pools are
          // gone.
          router.push(`/sign-in?next=${encodeURIComponent("/pools")}`);
          return;
        }
        setError(
          err instanceof Error ? err.message : "Couldn't load your pools."
        );
        setState("error");
      });
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  const workersOnline = pools.reduce((sum, p) => sum + p.machines_online, 0);

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="title">Pools</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            Share machines and jobs with people you invite.
          </p>
          <Link
            href="/pools/join"
            className="mt-1 inline-block text-xs text-muted-foreground hover:text-foreground hover:underline"
          >
            Have an invite code?
          </Link>
        </div>
        <button
          type="button"
          onClick={load}
          aria-label="Refresh"
          className="rounded-md p-2 text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
        >
          <ArrowClockwise
            size={15}
            className={state === "loading" ? "animate-spin" : ""}
          />
        </button>
      </div>

      {pools.length > 0 && (
        <div className="mt-7 flex items-baseline gap-6">
          <div>
            <div className="metric-lg">{pools.length}</div>
            <div className="label-caps mt-1">Pools</div>
          </div>
          <div>
            <div className="metric-lg text-muted-foreground">
              {workersOnline}
            </div>
            <div className="label-caps mt-1">Workers online</div>
          </div>
        </div>
      )}

      <CreatePoolCard onCreated={load} />

      <div className="mt-6">
        {state === "loading" && pools.length === 0 ? (
          <div className="space-y-px">
            <div className="skeleton h-14" />
            <div className="skeleton h-14" />
          </div>
        ) : state === "error" ? (
          <div className="flex flex-col items-center gap-3 py-12 text-center">
            <Warning className="h-5 w-5 text-destructive" weight="fill" />
            <p className="text-sm text-muted-foreground">{error}</p>
            <button
              type="button"
              onClick={load}
              className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-white/[0.06]"
            >
              Try again
            </button>
          </div>
        ) : pools.length === 0 ? (
          <Empty />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[620px] text-left">
              <thead>
                <tr className="border-b border-border">
                  {["Name", "Members", "Workers online", "Created"].map((h) => (
                    <th key={h} className="label-caps px-3 py-2 font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {pools.map((p) => (
                  <PoolRow key={p.id} pool={p} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function PoolRow({ pool }: { pool: PoolSummary }) {
  return (
    <tr>
      <td className="px-3 py-3">
        <Link
          href={`/pools/${pool.id}`}
          className="font-medium text-foreground hover:text-primary hover:underline"
        >
          {pool.name}
        </Link>
      </td>
      <td className="meta px-3 py-3">{pool.member_count}</td>
      <td className="meta px-3 py-3">{pool.machines_online}</td>
      <td className="meta px-3 py-3 whitespace-nowrap">
        {relativeTime(pool.created_at)}
      </td>
    </tr>
  );
}

function CreatePoolCard({ onCreated }: { onCreated: () => void }) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    const trimmed = name.trim();
    if (!trimmed) return;
    setSubmitting(true);
    setError(null);
    try {
      // The create response omits member_count/machines_online (see
      // `createPool`'s docstring in cloud-api.ts) — refetch the list via
      // `onCreated` rather than splicing an incomplete row into local
      // state, so the new pool shows real counts instead of undefined ones.
      const pool = await createPool(trimmed);
      setName("");
      onCreated();
      toast.success("Pool created", { description: pool.name });
    } catch (err) {
      if (err instanceof NotAuthenticated) {
        router.push(`/sign-in?next=${encodeURIComponent("/pools")}`);
        return;
      }
      setError(
        err instanceof ApiError
          ? err.detail
          : "Couldn't create that pool. Try again."
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className="mt-6">
      <CardHeader>
        <CardTitle className="text-sm">Create a pool</CardTitle>
        <CardDescription>
          Everyone you invite can see and place jobs on each other&apos;s
          machines.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
          className="flex flex-wrap items-start gap-2"
        >
          <div className="min-w-0 flex-1">
            <Input
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setError(null);
              }}
              placeholder="Pool name"
              aria-label="Pool name"
              disabled={submitting}
            />
            {error && (
              <p className="mt-1.5 text-xs text-destructive">{error}</p>
            )}
          </div>
          <button
            type="submit"
            disabled={submitting || name.trim().length === 0}
            className="interactive rounded-md bg-primary px-3.5 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {submitting ? "Creating…" : "Create pool"}
          </button>
        </form>
      </CardContent>
    </Card>
  );
}

function Empty() {
  return (
    <div className="flex flex-col items-center gap-2 py-14 text-center">
      <h2 className="text-base font-semibold">No pools yet</h2>
      <p className="mx-auto max-w-sm text-sm text-muted-foreground">
        Create one above, or ask a teammate for an invite link to join
        theirs.
      </p>
    </div>
  );
}
