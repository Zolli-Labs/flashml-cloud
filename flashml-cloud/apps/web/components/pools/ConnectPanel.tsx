"use client";

import { useState, useSyncExternalStore } from "react";
import { Check, Copy, Warning } from "@phosphor-icons/react";
import { toast } from "sonner";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import { cloudApiBase } from "@/lib/cloud-api";

// Not a route module — lives under `components/`, imported by the pool
// detail page, same placement as `EnrolInstructions`. `route-exports.test.ts`
// only walks `app/`, so this is never at risk of being (accidentally)
// exported off a `page.tsx`.
//
// Colab and RunPod are the two hosts a pool member is likeliest to reach
// for when they have no machine to spare locally: a paid Colab notebook, or
// a rented pod. Both guides — `docs/guides/join-a-pool-colab.md` and
// `join-a-pool-runpod.md` — walk the same three commands by hand; this is
// their in-product form, wired to this pool's real coordinator URL and id
// instead of the docs' example host and a placeholder token.

function CopyBlock({ cmd }: { cmd: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(cmd);
      setCopied(true);
      toast.success("Command copied");
    } catch {
      toast.error("Your browser blocked clipboard access");
    }
  }

  return (
    <div className="group relative rounded-lg border border-border/60 bg-black/25 pr-11">
      <pre className="overflow-x-auto px-3.5 py-2.5 font-mono text-[11.5px] leading-relaxed text-foreground/90">
        <code className="whitespace-pre-wrap break-all">{cmd}</code>
      </pre>
      <button
        type="button"
        onClick={copy}
        aria-label={copied ? "Copied" : "Copy command"}
        className="interactive absolute right-1.5 top-1.5 rounded-md p-1.5 text-muted-foreground opacity-0 transition-opacity hover:bg-white/5 hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100"
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
  );
}

function CellLabel({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-muted-foreground">{children}</p>;
}

// `flashnode login`'s own output always suggests `flashnode work --runner
// docker` next — a fixed line the CLI prints regardless of host, written
// for a Docker-capable machine. Neither Colab nor a RunPod pod is one (see
// each tab's cannot-nest-Docker note below), so this caption exists so
// nobody follows that printed hint instead of the trusted command two
// cells down.
function ApproveCaption({
  origin,
  poolId,
  hostNoun,
}: {
  origin: string;
  poolId: string;
  hostNoun: string;
}) {
  return (
    <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
      This prints a short code and a URL. The printed{" "}
      <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">
        flashnode work --runner docker
      </code>{" "}
      hint doesn&apos;t apply on {hostNoun} — use the trusted command below
      instead. Approve at{" "}
      <span className="font-mono text-foreground">
        {origin}/activate?pool={poolId}
      </span>{" "}
      from any signed-in browser.
    </p>
  );
}

// `window.location.origin` is only correct once mounted in the browser —
// reading it during the server render would crash (no `window`), and
// setting it from an effect body is a cascading render
// (`react-hooks/set-state-in-effect`, same tradeoff `EnrolInstructions`
// documents for its own platform detection). `useSyncExternalStore`'s
// two-snapshot signature is for exactly this: the server snapshot is
// empty, the client one is the real origin, and React reconciles them
// without an extra render loop.
const subscribeNever = () => () => {};
const getOrigin = () => window.location.origin;
const getOriginServer = () => "";

export function ConnectPanel({ poolId }: { poolId: string }) {
  const base = cloudApiBase();
  const origin = useSyncExternalStore(
    subscribeNever,
    getOrigin,
    getOriginServer
  );

  return (
    <Tabs defaultValue="colab">
      <TabsList>
        <TabsTrigger value="colab">Colab</TabsTrigger>
        <TabsTrigger value="runpod">RunPod</TabsTrigger>
      </TabsList>

      <TabsContent value="colab" className="mt-4 space-y-4">
        {/* Verbatim from docs/guides/join-a-pool-colab.md, including the
            dated re-check line — this is a legal/ToS caveat, not marketing
            copy, and must not drift from the doc it was copied out of. */}
        <div className="flex items-start gap-2 rounded-lg border border-amber-400/30 bg-amber-400/10 px-3.5 py-3 text-xs leading-relaxed text-amber-400">
          <Warning className="mt-0.5 h-4 w-4 shrink-0" weight="fill" />
          <p>
            <strong className="font-semibold">Paid Colab only.</strong>{" "}
            Google&apos;s Colab FAQ prohibits &quot;running distributed
            computing workers&quot; on the free tier, and prohibits
            &quot;using multiple accounts to work around access or resource
            usage restrictions&quot; on every tier. Enforcement lands on{" "}
            <strong className="font-semibold">your Google account</strong>.
            Run this only on a paid Colab plan, one account, yours.
            <br />
            <br />
            FAQ wording as read 2026-08-02 — re-check it before relying on
            this.
          </p>
        </div>

        <div>
          <CellLabel>Cell 1 — install</CellLabel>
          <div className="mt-1">
            <CopyBlock cmd="!pip install flashnode" />
          </div>
        </div>

        <div>
          <CellLabel>Cell 2 — connect</CellLabel>
          <div className="mt-1">
            <CopyBlock cmd={`!flashnode login --coordinator ${base}`} />
          </div>
          <ApproveCaption
            origin={origin}
            poolId={poolId}
            hostNoun="notebook hosts"
          />
        </div>

        <div>
          <CellLabel>Cell 3 — contribute</CellLabel>
          <div className="mt-1">
            <CopyBlock
              cmd={`!flashnode work --coordinator ${base} --runner trusted`}
            />
          </div>
          <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
            Colab can&apos;t nest a Docker daemon inside its own container,
            so trusted is the only runner tier that works here — it runs
            this workspace&apos;s jobs unsandboxed, directly in the runtime.
            Runs
            until the cell is interrupted or the runtime disconnects.
          </p>
        </div>
      </TabsContent>

      <TabsContent value="runpod" className="mt-4 space-y-4">
        <p className="text-xs leading-relaxed text-muted-foreground">
          A rented pod is compute you&apos;re paying for directly — unlike
          Colab, there&apos;s no free-tier ToS clause about distributed
          workers to navigate, and no shared-account restriction to worry
          about. Run these in the pod&apos;s terminal (a web terminal from
          the RunPod console, or SSH if the pod exposes it).
        </p>

        <div>
          <CellLabel>Install</CellLabel>
          <div className="mt-1">
            <CopyBlock cmd="pip install flashnode" />
          </div>
        </div>

        <div>
          <CellLabel>Connect</CellLabel>
          <div className="mt-1">
            <CopyBlock cmd={`flashnode login --coordinator ${base}`} />
          </div>
          <ApproveCaption origin={origin} poolId={poolId} hostNoun="a pod" />
        </div>

        <div>
          <CellLabel>Contribute</CellLabel>
          <div className="mt-1">
            <CopyBlock
              cmd={`flashnode work --coordinator ${base} --runner trusted`}
            />
          </div>
          <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
            A pod is already a container (or a full VM on secure cloud) and
            can&apos;t nest a second Docker daemon inside itself, so the
            sandboxed runner tiers aren&apos;t available here — trusted runs
            this workspace&apos;s jobs unsandboxed on the pod. Use{" "}
            <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">
              tmux
            </code>{" "}
            or{" "}
            <code className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">
              nohup … &amp;
            </code>{" "}
            if you want it to survive closing the terminal.
          </p>
        </div>
      </TabsContent>
    </Tabs>
  );
}
