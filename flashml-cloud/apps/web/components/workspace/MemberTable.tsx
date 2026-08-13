"use client";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { relativeTime } from "@/lib/machine-status";
import { memberName, memberSubtitle } from "@/lib/member-identity";
import { type PoolMember } from "@/lib/cloud-api";

const COLUMNS = ["Member", "Machines", "Online", "Joined"];

/**
 * Every member of this workspace, one row each.
 *
 * ROWS ONLY. It renders no empty state, because it cannot tell one: a
 * `members` array says nothing about whether the read that produced it
 * succeeded. Its caller (`people/page.tsx`) owns that decision through
 * `StatePanel`, which is the only place that has the answer.
 *
 * On `components/ui/table.tsx` rather than a hand-written `<table>`: this
 * file, `PoolFleetTable` and the jobs tab each carried their own min-width
 * (560px / 640px / 640px) and their own cell padding for what is visibly the
 * same table.
 */
export function MemberTable({
  members,
  ownerId,
}: {
  members: PoolMember[];
  ownerId: string;
}) {
  return (
    // The floor that keeps four columns from collapsing. The primitive
    // supplies the `overflow-x-auto` container this used to hand-write, so
    // the table scrolls inside its own box rather than the page.
    <Table className="min-w-[640px]">
      <TableHeader>
        <TableRow>
          {COLUMNS.map((h) => (
            <TableHead key={h} className="label-caps">
              {h}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {members.map((m) => (
          <MemberRow
            key={m.user_id}
            member={m}
            isOwner={m.user_id === ownerId}
          />
        ))}
      </TableBody>
    </Table>
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
    <TableRow>
      <TableCell>
        <span className="min-w-0">
          <span className="block truncate text-sm">
            {memberName(member)}
            {isOwner && (
              <span className="label-caps ml-2 align-middle">owner</span>
            )}
          </span>
          {memberSubtitle(member) && (
            <span className="meta block truncate">{memberSubtitle(member)}</span>
          )}
        </span>
      </TableCell>
      {/* Both counts come straight off the row `list_pools_for_user`
          computed. Rendered as returned — no `|| "—"` — because these are
          `number`, so a zero here is a counted zero and blanking it would
          throw away the one fact the column carries. */}
      <TableCell className="meta">{member.machine_count}</TableCell>
      <TableCell className="meta">{member.machines_online}</TableCell>
      <TableCell className="meta">{relativeTime(member.joined_at)}</TableCell>
    </TableRow>
  );
}
