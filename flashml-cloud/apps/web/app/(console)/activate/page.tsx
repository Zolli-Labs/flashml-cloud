"use client";

import { useState } from "react";
import Link from "next/link";
import {
  CheckCircle,
  Lightning,
  Warning,
  ArrowRight,
} from "@phosphor-icons/react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  ApiError,
  NotAuthenticated,
  NotFound,
  approveDeviceCode,
  listMachines,
  type Machine,
} from "@/lib/cloud-api";

// The device-code alphabet (enrolment.py) already excludes O/0/I/1, so the
// only normalization needed here is the formatting a person actually
// produces when reading eight characters off a laptop screen and typing
// them into a phone: stray case, spaces, and dashes. The server itself
// only strips leading/trailing whitespace and upper-cases, so internal
// spaces/dashes have to be stripped here or a perfectly correct code fails.
function normalizeCode(raw: string): string {
  return raw.replace(/[\s-]/g, "").toUpperCase();
}

type Status = "idle" | "submitting" | "success" | "error";

export default function ActivatePage() {
  const [rawCode, setRawCode] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [machine, setMachine] = useState<Machine | null>(null);
  const [machineId, setMachineId] = useState<string | null>(null);

  const code = normalizeCode(rawCode);
  const canSubmit = code.length >= 6 && status !== "submitting";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setStatus("submitting");
    setErrorMessage(null);

    try {
      const result = await approveDeviceCode(code);
      setMachineId(result.machine_id);
      setStatus("success");

      // The approve response only carries a machine_id — fetch the list to
      // name what was just approved, so the success screen can say what
      // happened rather than a bare "OK". Best-effort: if this fails, the
      // approval itself still succeeded and the generic success copy
      // still tells the person what to do next.
      try {
        const machines = await listMachines();
        const found = machines.find((m) => m.id === result.machine_id) ?? null;
        setMachine(found);
      } catch {
        setMachine(null);
      }
    } catch (err) {
      setStatus("error");
      if (err instanceof NotAuthenticated) {
        setErrorMessage(
          "Your sign-in expired. Sign in again, then re-enter the code."
        );
      } else if (err instanceof NotFound) {
        setErrorMessage(
          "We couldn't find that code. Double-check it against your laptop's screen — codes are only valid for a few minutes."
        );
      } else if (err instanceof ApiError && err.status === 410) {
        setErrorMessage(
          "That code has expired. On the laptop, run `flashnode login` again to get a fresh one."
        );
      } else if (err instanceof ApiError && err.status === 409) {
        setErrorMessage(
          "That machine is already enrolled under a different code. If this isn't expected, revoke it from /machines first."
        );
      } else if (err instanceof ApiError) {
        setErrorMessage(err.detail);
      } else {
        setErrorMessage("Something went wrong. Try again.");
      }
    }
  }

  function reset() {
    setRawCode("");
    setStatus("idle");
    setErrorMessage(null);
    setMachine(null);
    setMachineId(null);
  }

  if (status === "success") {
    const label = machine?.name || machine?.node_id || machineId;
    return (
      <div className="min-h-[calc(100dvh-4rem)] flex items-center justify-center px-4 py-10">
        <Card className="w-full max-w-sm">
          <CardContent className="flex flex-col items-center text-center gap-4 pt-6 pb-2">
            <div className="flex items-center justify-center w-14 h-14 rounded-full bg-node-green/10 border border-node-green/30">
              <CheckCircle
                className="w-8 h-8 text-node-green"
                weight="fill"
              />
            </div>
            <div className="space-y-1.5">
              <h1 className="text-lg font-bold font-mono text-foreground">
                Machine approved
              </h1>
              <p className="text-sm text-muted-foreground">
                {label ? (
                  <>
                    <span className="font-mono text-foreground">{label}</span>{" "}
                    is now linked to your account.
                  </>
                ) : (
                  "That machine is now linked to your account."
                )}
              </p>
            </div>
            <div className="w-full rounded-lg border border-border/60 bg-surface-elevated/50 px-4 py-3 text-left text-xs text-muted-foreground space-y-1">
              <p className="font-medium text-foreground">What happens next</p>
              <p>
                It will show up in{" "}
                <Link href="/machines" className="text-cyan underline underline-offset-2">
                  My Machines
                </Link>{" "}
                and can start picking up work as soon as it finishes
                signing in on that laptop.
              </p>
            </div>
            <Button
              size="lg"
              className="w-full mt-1"
              render={
                <Link href="/machines">
                  Go to My Machines
                  <ArrowRight className="w-4 h-4" data-icon="inline-end" />
                </Link>
              }
            />
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="min-h-[calc(100dvh-4rem)] flex items-center justify-center px-4 py-10">
      <Card className="w-full max-w-sm">
        <CardHeader className="items-center text-center gap-2 pt-2">
          <div className="relative flex items-center justify-center w-10 h-10">
            <div className="absolute inset-0 rounded-md bg-cyan/10 border border-cyan/30" />
            <Lightning className="relative z-10 text-cyan w-5 h-5" weight="fill" />
          </div>
          <CardTitle className="text-lg">Approve a machine</CardTitle>
          <p className="text-sm text-muted-foreground max-w-72">
            Enter the code shown on the laptop running{" "}
            <code className="font-mono text-xs bg-muted px-1 py-0.5 rounded">
              flashnode login
            </code>
            .
          </p>
        </CardHeader>
        <CardContent className="pb-2">
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="user-code" className="sr-only">
                Device code
              </Label>
              <input
                id="user-code"
                name="user-code"
                type="text"
                inputMode="text"
                autoCapitalize="characters"
                autoCorrect="off"
                autoComplete="off"
                spellCheck={false}
                placeholder="XXXX XXXX"
                value={rawCode}
                onChange={(e) => setRawCode(e.target.value)}
                disabled={status === "submitting"}
                className="w-full rounded-xl border border-input bg-transparent px-4 py-4 text-center font-mono text-3xl sm:text-4xl tracking-[0.2em] uppercase outline-none placeholder:text-muted-foreground/40 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
              />
              <p className="text-xs text-muted-foreground text-center">
                Spaces, dashes, and lowercase are fine.
              </p>
            </div>

            {status === "error" && errorMessage ? (
              <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2.5 text-sm text-destructive">
                <Warning className="w-4 h-4 shrink-0 mt-0.5" weight="fill" />
                <span>{errorMessage}</span>
              </div>
            ) : null}

            <Button
              type="submit"
              size="lg"
              className="w-full h-12 text-base"
              disabled={!canSubmit}
            >
              {status === "submitting" ? "Approving…" : "Approve machine"}
            </Button>
            {status === "error" ? (
              <Button type="button" variant="ghost" size="sm" onClick={reset}>
                Start over
              </Button>
            ) : null}
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
