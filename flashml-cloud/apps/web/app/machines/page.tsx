"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowClockwise, Plus, Warning } from "@phosphor-icons/react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { MachineCard } from "@/components/machines/MachineCard";
import {
  NotAuthenticated,
  cloudApiBase,
  listMachines,
  revokeMachine,
  type Machine,
} from "@/lib/cloud-api";

type LoadState = "loading" | "ready" | "error";

export default function MachinesPage() {
  const router = useRouter();
  const [machines, setMachines] = useState<Machine[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Fetches and settles state entirely in the promise continuation — no
  // setState call happens synchronously in the effect body below, which is
  // what react-hooks/set-state-in-effect flags as a cascading-render risk.
  // `refresh` (the button's onClick) is the one place that sets "loading"
  // synchronously, and that's fine there: it's an event handler, not an
  // effect.
  const load = useCallback(() => {
    listMachines()
      .then((result) => {
        setMachines(result);
        setState("ready");
        setErrorMessage(null);
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          // A 401 here means "you are signed out", not "you have no
          // machines" — never render the empty state for this.
          router.push("/sign-in?next=/machines");
          return;
        }
        setErrorMessage(
          err instanceof Error ? err.message : "Couldn't load your machines."
        );
        setState("error");
      });
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  function refresh() {
    setState("loading");
    setErrorMessage(null);
    load();
  }

  async function handleRevoke(machineId: string) {
    await revokeMachine(machineId);
    setMachines((prev) =>
      prev.map((m) =>
        m.id === machineId
          ? { ...m, status: "revoked", revoked_at: new Date().toISOString() }
          : m
      )
    );
  }

  return (
    <main className="max-w-3xl mx-auto px-4 sm:px-6 py-8 space-y-6">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold font-mono">My Machines</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Machines enrolled to your account and taking work.
          </p>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          onClick={refresh}
          disabled={state === "loading"}
          aria-label="Refresh"
        >
          <ArrowClockwise
            className={state === "loading" ? "animate-spin" : ""}
          />
        </Button>
      </div>

      {state === "loading" ? (
        <div className="space-y-3">
          {[0, 1].map((i) => (
            <Card key={i}>
              <CardContent className="h-20 animate-pulse bg-muted/30 rounded-lg" />
            </Card>
          ))}
        </div>
      ) : state === "error" ? (
        <Card>
          <CardContent className="flex flex-col items-center text-center gap-3 py-8">
            <Warning className="w-6 h-6 text-destructive" weight="fill" />
            <p className="text-sm text-muted-foreground">{errorMessage}</p>
            <Button type="button" variant="outline" size="sm" onClick={refresh}>
              Try again
            </Button>
          </CardContent>
        </Card>
      ) : machines.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="space-y-3">
          {machines.map((machine) => (
            <MachineCard
              key={machine.id}
              machine={machine}
              onRevoke={handleRevoke}
            />
          ))}
        </div>
      )}
    </main>
  );
}

function EmptyState() {
  const base = cloudApiBase();
  return (
    <Card>
      <CardContent className="flex flex-col items-center text-center gap-4 py-10">
        <div className="flex items-center justify-center w-12 h-12 rounded-full bg-cyan/10 border border-cyan/30">
          <Plus className="w-6 h-6 text-cyan" weight="bold" />
        </div>
        <div className="space-y-1">
          <h2 className="font-semibold text-foreground">No machines yet</h2>
          <p className="text-sm text-muted-foreground max-w-sm">
            Enroll a machine to start contributing compute to your jobs.
            Run these on the machine you want to add:
          </p>
        </div>
        <div className="w-full max-w-sm rounded-lg border border-border/60 bg-surface-elevated/60 text-left font-mono text-xs overflow-x-auto">
          <div className="px-3.5 py-2.5 border-b border-border/60">
            <span className="text-muted-foreground select-none">$ </span>
            pip install flashnode
          </div>
          <div className="px-3.5 py-2.5">
            <span className="text-muted-foreground select-none">$ </span>
            flashnode login --coordinator {base}
          </div>
        </div>
        <p className="text-xs text-muted-foreground max-w-sm">
          That command prints a short code — enter it at{" "}
          <span className="font-mono text-foreground">/activate</span> from
          any signed-in browser, including your phone, to approve it.
        </p>
      </CardContent>
    </Card>
  );
}
