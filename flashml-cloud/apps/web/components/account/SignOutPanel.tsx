"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { SignOut } from "@phosphor-icons/react";

import { Button } from "@/components/ui/button";
import { createBrowserSupabaseClient } from "@/lib/supabase";

/**
 * Ending the browser session, and the sentence that stops someone thinking
 * it does more than that.
 *
 * Reads nothing, so it has no states: it is the one section of this page that
 * stays useful when every read on it has failed, which is also when somebody
 * is most likely to want it.
 */
export function SignOutPanel() {
  const router = useRouter();

  async function signOut() {
    const supabase = createBrowserSupabaseClient();
    await supabase.auth.signOut();
    router.push("/sign-in");
    router.refresh();
  }

  return (
    // Deliberately NOT the destructive treatment this panel used to carry
    // (red border, red tint, red heading, destructive button).
    //
    // Destructive styling means "this cannot be undone". Signing out is the
    // most reversible action in the product — you sign back in. The panel's
    // own copy says so, and the styling contradicted it: red framing around a
    // paragraph explaining that nothing is revoked and machines keep working.
    //
    // The cost is not just the false alarm. This console has genuinely
    // destructive actions — revoking a machine, declining an access request,
    // and a force-revoke that kills a running task. If sign-out is red too,
    // red stops meaning "stop and think", and the signal is spent on the one
    // action that never needed it.
    <section className="mt-4 rounded-lg border border-border bg-surface p-5">
      <h2 className="text-sm font-semibold">Sign out</h2>
      <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted-foreground">
        Signing out ends this browser session only. It does{" "}
        <span className="text-foreground">not</span> revoke any Machine:
        Machines hold their own tokens and keep claiming work until you revoke
        them from{" "}
        <Link href="/machines" className="text-brand-foreground hover:underline">
          Machines
        </Link>
        .
      </p>
      <Button variant="outline" className="mt-3" onClick={signOut}>
        <SignOut size={14} />
        Sign out
      </Button>
    </section>
  );
}
