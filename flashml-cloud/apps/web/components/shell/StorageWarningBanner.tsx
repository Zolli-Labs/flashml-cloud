"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Warning } from "@phosphor-icons/react";
import { getMyStorage, type AccountStorage } from "@/lib/cloud-api";
import { summariseStorage } from "@/lib/account-storage";

/**
 * The earliest place in the console someone can learn their storage is
 * running out — before they have written a `flashml.yaml`, let alone
 * pasted a repo into the submit form. `lib/account-storage.ts` decides
 * WHEN this fires (its `severity` field) and WHAT it says (its `message`);
 * this component only renders that decision. It is mounted once in
 * `ConsoleShell`, above every workspace's content, so it is visible from
 * whichever page someone happens to land on first — not only the Account
 * page's own Storage panel, which nobody thinks to visit until AFTER a
 * refusal sends them looking for one.
 *
 * Renders nothing for `severity === "ok"`, which is every unlimited
 * account, always — see `lib/account-storage.ts`'s module doc for why that
 * is a type guarantee, not a coincidence of the numbers picked here.
 */
export function StorageWarningBanner() {
  const [storage, setStorage] = useState<AccountStorage | null>(null);

  useEffect(() => {
    let cancelled = false;
    getMyStorage()
      .then((s) => {
        if (!cancelled) setStorage(s);
      })
      .catch(() => {
        // Best-effort and silent. A signed-out session is already handled
        // by ConsoleShell's own `getMe()` redirect; any other failure here
        // (network blip, a storage-service hiccup) should not put a broken
        // banner ahead of the actual page content — the account page's own
        // Storage panel still surfaces a real error for anyone who goes
        // looking there.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!storage) return null;
  const display = summariseStorage(storage);
  if (display.severity === "ok") return null;

  const full = display.severity === "full";

  return (
    <div
      role="status"
      className={`flex items-start gap-2.5 border-b px-4 py-2.5 text-sm sm:px-6 ${
        full
          ? "border-destructive/30 bg-destructive/10 text-destructive"
          : "border-warning/40 bg-warning/10 text-warning-foreground"
      }`}
    >
      <Warning className="mt-0.5 h-4 w-4 shrink-0" weight="fill" />
      <span className="min-w-0">
        {display.message}{" "}
        <Link href="/account" className="underline hover:no-underline">
          Manage storage
        </Link>
      </span>
    </div>
  );
}
