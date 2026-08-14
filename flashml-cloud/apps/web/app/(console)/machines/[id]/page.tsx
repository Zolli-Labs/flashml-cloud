"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { MachineDetailView } from "@/components/machines/MachineDetailView";
import { PageShell } from "@/components/shell/PageShell";
import { machineLabel } from "@/lib/machine-lifecycle";
import {
  NotAuthenticated,
  listMachines,
  revokeMachine,
  type Machine,
} from "@/lib/cloud-api";

/**
 * One machine, end to end — the click destination from every card on
 * `/machines`.
 *
 * NO PER-MACHINE ENDPOINT EXISTS. `GET /v1alpha1/machines` (list) is the
 * only read this API offers; there is no `GET /v1alpha1/machines/{id}`.
 * This page therefore fetches the SAME list the grid does and selects this
 * machine out of it client-side — `load` re-fetches the whole list and
 * re-selects, rather than patching one row, so this page can never show a
 * machine a fresh read would disagree with. If a per-machine route is ever
 * added, this is the one place that needs to change.
 *
 * The actual rendering — sections, fields, the Revoke dialog — lives in
 * `components/machines/MachineDetailView.tsx`, a pure presentational
 * component that takes a resolved `Machine` and no routing/fetching
 * dependencies of its own. This file's only job is resolving one: fetch,
 * find, and the three states around "found" (loading / unreadable /
 * missing).
 */
export default function MachineDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const router = useRouter();
  const id = use(params).id;

  const [state, setState] = useState<
    "loading" | "present" | "unreadable" | "missing"
  >("loading");
  const [machine, setMachine] = useState<Machine | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState(false);

  const load = useCallback(() => {
    listMachines()
      .then((rows) => {
        const found = rows.find((m) => m.id === id) ?? null;
        setMachine(found);
        setState(found ? "present" : "missing");
        setError(null);
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push(
            `/sign-in?next=${encodeURIComponent(`/machines/${id}`)}`
          );
          return;
        }
        setError(
          err instanceof Error ? err.message : "Couldn't load this Machine."
        );
        setState("unreadable");
      });
  }, [id, router]);

  useEffect(() => {
    load();
  }, [load]);

  async function confirmRevoke() {
    if (!machine) return;
    setRevoking(true);
    try {
      await revokeMachine(machine.id);
      setMachine((prev) =>
        prev
          ? { ...prev, status: "revoked", revoked_at: new Date().toISOString() }
          : prev
      );
      toast.success("Machine revoked", {
        description: `${machineLabel(machine)} can no longer claim work.`,
      });
    } catch {
      toast.error("Couldn't revoke that Machine", {
        description: "The Machine is unchanged. Try again.",
      });
    } finally {
      setRevoking(false);
    }
  }

  if (state === "loading") {
    return (
      <PageShell width="wide">
        <p className="meta" role="status">
          Loading…
        </p>
      </PageShell>
    );
  }

  if (state === "unreadable") {
    return (
      <PageShell width="wide">
        <div role="alert">
          <p className="text-sm font-medium text-foreground">
            Could not read this.
          </p>
          <p className="mt-1 text-sm text-muted-foreground">{error}</p>
          <Button variant="outline" size="sm" className="mt-3.5" onClick={load}>
            Try again
          </Button>
        </div>
      </PageShell>
    );
  }

  if (state === "missing" || !machine) {
    // A 404 IS NOT AN ERROR WALL, same doctrine as `/market/providers/[id]`:
    // one sentence and a way back. Nothing failed — this is either an id
    // that never existed or a machine that belongs to someone else, and the
    // list route already scopes to the caller so those two look the same
    // from here on purpose.
    return (
      <PageShell width="wide">
        <p className="text-sm text-muted-foreground">
          {"Couldn't find that Machine. "}
          <Link href="/machines" className="underline">
            Back to My machines
          </Link>
        </p>
      </PageShell>
    );
  }

  return (
    <PageShell width="wide">
      <MachineDetailView
        machine={machine}
        revoking={revoking}
        onRevoke={confirmRevoke}
      />
    </PageShell>
  );
}
