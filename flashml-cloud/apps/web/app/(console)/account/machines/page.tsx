"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowClockwise } from "@phosphor-icons/react";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/shell/PageHeader";
import { PageShell } from "@/components/shell/PageShell";
import { StatePanel } from "@/components/shell/StatePanel";
import { StatStrip } from "@/components/shared/StatStrip";
import { isEmptyList, resolvePanel } from "@/lib/console/panel-state";
import { EnrolInstructions } from "@/components/machines/EnrolInstructions";
import { MachineCard } from "@/components/machines/MachineCard";
import { RevokedMachines } from "@/components/machines/RevokedMachines";
import { splitFleet, type DeleteOutcome } from "@/lib/machine-lifecycle";
import {
  NotAuthenticated,
  NotFound,
  cloudApiBase,
  deleteMachine,
  listMachines,
  type Machine,
} from "@/lib/cloud-api";

const POLL_MS = 15_000;

export default function MachinesPage() {
  const router = useRouter();
  const [machines, setMachines] = useState<Machine[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    listMachines()
      .then((r) => {
        setMachines(r);
        setState("ready");
        setError(null);
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          // 401 means signed out, not "you have no machines". Rendering the
          // empty state here would tell a signed-out user their fleet is
          // gone.
          //
          // `/account/machines`, this page's own path — `/machines` is only
          // a redirect stub now, so signing in used to bounce you through it
          // instead of straight back to where you were.
          router.push("/sign-in?next=/account/machines");
          return;
        }
        setError(
          err instanceof Error ? err.message : "Couldn't load your Machines."
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

  /**
   * Delete, refetch, and classify — never throw.
   *
   * The list is REFETCHED rather than edited in place, unlike a revoke
   * (which now happens on the detail page, not here): revoke changes one
   * field on a row that stays, and delete removes the row from what the API
   * will return next. Patching state to match would be this page asserting
   * the server's answer instead of reading it.
   *
   * A 404 refetches too. The route folds "unknown", "not yours" and "already
   * deleted" into one answer, and on a row this page just read the third is
   * what it means: the list was stale, so the honest response is to re-read
   * it and say so once. Treating it as an error would put a failure on
   * screen for an outcome that is exactly what the reader asked for.
   */
  async function handleDelete(id: string): Promise<DeleteOutcome> {
    try {
      await deleteMachine(id);
    } catch (err) {
      if (err instanceof NotFound) {
        load();
        return { kind: "already-gone" };
      }
      return {
        kind: "failed",
        detail: err instanceof Error ? err.message : "the API did not say why",
      };
    }
    load();
    return { kind: "deleted" };
  }

  // Same ordering the hand-rolled switch below used — error, then loading,
  // then the rows — with the one difference that no arrangement of these can
  // now reach the empty state from a failed read.
  const panel = resolvePanel(
    { loading: state === "loading", error, data: machines },
    isEmptyList
  );

  return (
    <PageShell width="wide">
      <PageHeader
        title="My Machines"
        description="Machines you own. Tick one into a Workspace to let your workspace members place jobs on it."
        actions={
          <>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={load}
              aria-label="Refresh"
            >
              <ArrowClockwise
                className={state === "loading" ? "animate-spin" : ""}
              />
            </Button>
            {/* No sizing override. The `default` variant now carries the same
                36px height, 600 weight and brand hover the hand-rolled string
                did; only horizontal padding differs, by 2px per side, which is
                the size scale's business and not this page's. */}
            {/* nativeButton={false}: the render prop swaps the underlying
                element for Link's <a>, so the primitive must not assume
                native <button> semantics (it adds role/keyboard handling
                itself instead). */}
            <Button render={<Link href="/activate" />} nativeButton={false}>
              Add a Machine
            </Button>
          </>
        }
      />

      <StatePanel
        state={panel}
        className="mt-6"
        label="your Machines"
        empty={{
          title: "No machines yet",
          description:
            "Run these commands on the machine you want to connect. It can be this one.",
          action: <EnrolInstructions base={cloudApiBase()} />,
        }}
        unreadable={{ retry: load }}
      >
        {(rows) => {
          // Both counts moved inside `present`. They used to render above the
          // state switch, so a failed poll left "2 Online now" standing over a
          // panel that had just said it could not read anything — stale
          // numbers presented as current.
          //
          // The split, and the two numbers over it, are one decision made in
          // `lib/machine-lifecycle.ts` — the header cannot count one set of
          // machines while the grid draws another.
          const { enrolled, revoked, online } = splitFleet(rows);
          return (
            <>
              {/* Rendered even at zero, same reasoning the old metric row
                  had: a fleet that is entirely revoked is the state this
                  whole screen exists for, and "0 Enrolled" is a measured
                  answer to the question the label asks. Only these two
                  cells — the mockup's stat strip also shows Pools and Jobs
                  running, but this page fetches neither, and inventing a
                  count this component was not given is exactly what the
                  honesty rule forbids. */}
              <StatStrip
                items={[
                  { label: "Online now", value: online },
                  { label: "Enrolled", value: enrolled.length },
                ]}
              />

              {enrolled.length === 0 ? (
                <p className="mt-6 text-sm text-muted-foreground">
                  Nothing enrolled right now.{" "}
                  <Link href="/machines/add" className="underline">
                    Add a machine
                  </Link>
                  , or bring one back below.
                </p>
              ) : (
                <div className="mt-6 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {enrolled.map((m) => (
                    <MachineCard key={m.id} machine={m} href={`/machines/${m.id}`} />
                  ))}
                </div>
              )}

              <RevokedMachines machines={revoked} onDelete={handleDelete} />
            </>
          );
        }}
      </StatePanel>
    </PageShell>
  );
}
