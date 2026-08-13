"use client";

import { Suspense, useCallback, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowRight, CheckCircle, Desktop, Warning } from "@phosphor-icons/react";
import {
  InputOTP,
  InputOTPGroup,
  InputOTPSeparator,
  InputOTPSlot,
} from "@/components/ui/input-otp";
import { Spinner } from "@/components/ui/spinner";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  ApiError,
  NotAuthenticated,
  NotFound,
  approveDeviceCode,
  getPool,
  listMachines,
  type Machine,
} from "@/lib/cloud-api";
import { approveNotFoundMessage } from "@/lib/activate-errors";

// A device code is exactly what a one-time-code input is for, and this page
// was a single wide text box. `input-otp` gives per-character slots, paste
// handling and mobile keyboards that behave — this screen exists to be used
// on a phone while reading a code off a laptop.
//
// The device-code alphabet (enrolment.py) already excludes O/0/I/1, so the
// only normalisation left is case: the slots cannot contain spaces or
// dashes, which is what the old free-text field had to strip. Validation
// still belongs to the server; this only stops an obviously wrong shape
// being sent.
const CODE_LENGTH = 8;

type Status = "idle" | "submitting" | "success" | "error";

// `useSearchParams()` needs a Suspense boundary or the whole route bails
// out of static rendering — same shape `(console)/pools/join/page.tsx`
// uses for the same reason, folded into this one file since this route
// keeps its metadata in a sibling `layout.tsx` already.
function Loading() {
  return (
    <div className="flex min-h-[calc(100dvh-3.5rem)] items-center justify-center px-4 py-10">
      <div className="skeleton h-72 w-full max-w-sm rounded-lg" />
    </div>
  );
}

function ActivateInner() {
  const searchParams = useSearchParams();
  // `?pool=` — set when this link was reached from a pool's connect panel
  // (`ConnectPanel`'s "Approve at .../activate?pool=<poolId>" caption).
  // Forwarded straight through to the approve call so the same device-code
  // flow that just enrols a machine can also, in one step, opt it into the
  // pool that sent the volunteer here.
  const poolId = searchParams.get("pool");

  const [code, setCode] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [machine, setMachine] = useState<Machine | null>(null);
  const [machineId, setMachineId] = useState<string | null>(null);
  const [poolName, setPoolName] = useState<string | null>(null);
  // Which kind of access was just granted. One route, two flows — and the
  // page cannot know which until the API answers, because all the person
  // typed was eight characters.
  const [kind, setKind] = useState<"machine" | "cli">("machine");

  const submit = useCallback(
    async (value: string) => {
      if (value.length !== CODE_LENGTH) return;
      setStatus("submitting");
      setErrorMessage(null);
      try {
        const result = await approveDeviceCode(value, poolId ?? undefined);
        // An absent `kind` means machine: an API deployed before the CLI
        // flow existed answers with machine_id and no kind at all.
        const approvedKind = result.kind === "cli" ? "cli" : "machine";
        setKind(approvedKind);
        setMachineId(result.machine_id ?? null);
        setStatus("success");

        // A CLI credential has no machine to name and is never placed on a
        // pool, so neither lookup below applies to it — and `listMachines()`
        // would quietly return the caller's unrelated fleet.
        if (approvedKind === "cli") return;

        // The approve response carries only a machine_id, so name what was
        // just approved rather than saying a bare "OK". Best effort: the
        // approval already succeeded if this lookup fails.
        try {
          const machines = await listMachines();
          setMachine(
            machines.find((m) => m.id === result.machine_id) ?? null
          );
        } catch {
          setMachine(null);
        }
        // Best effort, same reasoning: the bind already happened server-side
        // (approveDeviceCode's own atomic-approve-and-bind contract) even if
        // naming the pool here fails, so this never turns success into an
        // error — it only decides whether the confirmation can name the
        // pool or has to say "approved" plainly.
        if (poolId) {
          try {
            const { pool } = await getPool(poolId);
            setPoolName(pool.name);
          } catch {
            setPoolName(null);
          }
        }
      } catch (err) {
        setStatus("error");
        if (err instanceof NotAuthenticated) {
          setErrorMessage(
            "Your sign-in expired. Sign in again, then re-enter the code."
          );
        } else if (err instanceof NotFound) {
          setErrorMessage(approveNotFoundMessage(err.message));
        } else if (err instanceof ApiError && err.status === 410) {
          setErrorMessage(
            "That code has expired. Run flashnode login again on the laptop for a fresh one."
          );
        } else if (err instanceof ApiError && err.status === 409) {
          setErrorMessage(
            "That Machine is already enrolled under a different code. Revoke it from My Machines first if this isn't expected."
          );
        } else if (err instanceof ApiError) {
          setErrorMessage(err.detail);
        } else {
          setErrorMessage("Something went wrong. Try again.");
        }
      }
    },
    [poolId]
  );

  function reset() {
    setCode("");
    setStatus("idle");
    setErrorMessage(null);
    setMachine(null);
    setMachineId(null);
    setPoolName(null);
    setKind("machine");
  }

  if (status === "success" && kind === "cli") {
    // Deliberately a different screen, not the machine one with the nouns
    // swapped. The two approvals grant different powers — a machine RUNS
    // jobs for you, a credential ACTS AS you — and someone who just typed a
    // code has no other way to find out which they granted.
    return (
      <div className="flex min-h-[calc(100dvh-3.5rem)] items-center justify-center px-4 py-10">
        <div className="w-full max-w-sm text-center">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full border border-[var(--node-green)]/30 bg-[var(--node-green)]/10">
            <CheckCircle
              className="h-7 w-7 text-[var(--node-green)]"
              weight="fill"
            />
          </div>
          {/* `font-display` was here and is now gone. It was not a smaller
              or larger heading — it was NOTHING: `--font-display` is not
              declared in `app/globals.css` and Tailwind v4 has no such
              default, so the utility compiled to no CSS at all. It was the
              only use of the class in the repo. The sibling success screen
              below sets the same heading without it, and both already
              rendered identically; this makes the source say so. */}
          <h1 className="mt-5 text-2xl font-semibold">CLI access granted</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            This program can now submit jobs as you. It has exactly your
            access and no more, and it keeps working until you revoke it.
          </p>

          <div className="panel mt-6 p-4 text-left">
            <p className="text-sm font-medium">What happens next</p>
            <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
              The terminal you started this from now holds a credential.
              Manage it under{" "}
              <Link
                href="/account/cli"
                className="text-brand-foreground hover:underline"
              >
                Account → CLI access
              </Link>
              , where you can revoke it at any time.
            </p>
          </div>

          {/* `buttonVariants` rather than `<Button>` because this is a
              navigation, not an action, and it must stay an `<a>` that
              Next can prefetch. `size="lg"` is h-10/px-4, which is what
              `px-4 py-2.5` already computed to — so this adopts the
              primitive's colour, focus ring and hover without moving a
              pixel. */}
          <Link
            href="/account/cli"
            className={cn(buttonVariants({ size: "lg" }), "mt-5 w-full")}
          >
            Go to CLI access
            <ArrowRight className="h-4 w-4" weight="bold" />
          </Link>
        </div>
      </div>
    );
  }

  if (status === "success") {
    const label = machine?.name || machine?.node_id || machineId;
    return (
      <div className="flex min-h-[calc(100dvh-3.5rem)] items-center justify-center px-4 py-10">
        <div className="w-full max-w-sm text-center">
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-full border border-[var(--node-green)]/30 bg-[var(--node-green)]/10">
            <CheckCircle
              className="h-7 w-7 text-[var(--node-green)]"
              weight="fill"
            />
          </div>
          <h1 className="mt-5 text-2xl font-semibold">Machine approved</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {label ? (
              <>
                <span className="font-mono text-foreground">{label}</span> is
                linked to your account as a machine.
              </>
            ) : (
              "That machine is linked to your account."
            )}
          </p>
          {poolId && (
            <p className="mt-1 text-sm text-muted-foreground">
              {poolName ? (
                <>
                  Approved and attached to{" "}
                  <span className="font-medium text-foreground">
                    {poolName}
                  </span>
                  .
                </>
              ) : (
                "Approved."
              )}
            </p>
          )}

          <div className="panel mt-6 p-4 text-left">
            <p className="text-sm font-medium">What happens next</p>
            <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
              It appears under{" "}
              <Link href="/machines" className="text-brand-foreground hover:underline">
                My machines
              </Link>{" "}
              and starts claiming work once the agent is running on that
              laptop.
            </p>
          </div>

          <Link
            href="/machines"
            className={cn(buttonVariants({ size: "lg" }), "mt-5 w-full")}
          >
            Go to My machines
            <ArrowRight className="h-4 w-4" weight="bold" />
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-[calc(100dvh-3.5rem)] items-center justify-center px-4 py-10">
      <div className="panel w-full max-w-sm p-6 shadow-md sm:p-7">
        <div className="text-center">
          <span className="mx-auto grid h-12 w-12 place-items-center rounded-[7px] border border-border bg-[var(--z-app-surface-hover)] text-brand-foreground">
            <Desktop size={22} aria-hidden />
          </span>
          <p className="label-caps mt-4 text-brand-foreground">Add a machine</p>
          <h1 className="mt-2 text-2xl font-semibold">Approve a machine</h1>
          <p className="mx-auto mt-2 max-w-72 text-sm leading-relaxed text-muted-foreground">
            Enter the code shown on the laptop running{" "}
            <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
              flashnode login
            </code>
            .
          </p>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit(code);
          }}
          className="mt-8 flex flex-col items-center gap-5"
        >
          <InputOTP
            maxLength={CODE_LENGTH}
            value={code}
            // Uppercased on the way in so the slots always show what will be
            // sent. The server upper-cases too, but a lowercase character
            // sitting visibly in a slot looks like it is about to be
            // rejected.
            onChange={(v) => {
              setCode(v.toUpperCase());
              if (status === "error") {
                setStatus("idle");
                setErrorMessage(null);
              }
            }}
            // Submitting on the last character is the point of a code input:
            // nobody wants to type eight characters and then hunt for a
            // button.
            onComplete={(v) => submit(v.toUpperCase())}
            disabled={status === "submitting"}
            aria-label="Device code"
            containerClassName="justify-center"
          >
            <InputOTPGroup>
              {[0, 1, 2, 3].map((i) => (
                <InputOTPSlot key={i} index={i} className="h-12 w-11 text-lg" />
              ))}
            </InputOTPGroup>
            <InputOTPSeparator />
            <InputOTPGroup>
              {[4, 5, 6, 7].map((i) => (
                <InputOTPSlot key={i} index={i} className="h-12 w-11 text-lg" />
              ))}
            </InputOTPGroup>
          </InputOTP>

          <p className="meta text-center">Paste works. Lowercase is fine.</p>

          {status === "error" && errorMessage && (
            <div
              role="alert"
              className="flex w-full items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2.5 text-sm text-destructive"
            >
              <Warning className="mt-0.5 h-4 w-4 shrink-0" weight="fill" />
              <span>{errorMessage}</span>
            </div>
          )}

          {/* `h-11` is kept rather than taking `lg`'s h-10. This screen is
              built to be used on a phone while reading a code off a laptop
              (see the note at the top of this file), and 44px is the
              smallest touch target iOS considers reliable. Shrinking the
              one action on a phone-first screen to 40px is not a thing to
              do without being able to look at it. */}
          <Button
            type="submit"
            size="lg"
            className="h-11 w-full"
            disabled={code.length !== CODE_LENGTH || status === "submitting"}
          >
            {status === "submitting" ? (
              <>
                <Spinner className="size-4" />
                Approving…
              </>
            ) : (
              "Approve machine"
            )}
          </Button>

          {status === "error" && (
            <button
              type="button"
              onClick={reset}
              className="text-sm text-muted-foreground hover:text-foreground"
            >
              Start over
            </button>
          )}
        </form>
      </div>
    </div>
  );
}

export default function ActivatePage() {
  return (
    <Suspense fallback={<Loading />}>
      <ActivateInner />
    </Suspense>
  );
}
