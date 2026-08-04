"use client";

import { relativeTime } from "@/lib/machine-status";
import { type PoolMember } from "@/lib/cloud-api";

export function MemberTable({
  members,
  ownerId,
}: {
  members: PoolMember[];
  ownerId: string;
}) {
  return (
    <div className="mt-6 overflow-x-auto">
      <table className="w-full min-w-[560px] text-left">
        <thead>
          <tr className="border-b border-border">
            {["Member", "Machines", "Online", "Joined"].map((h) => (
              <th key={h} className="label-caps px-3 py-2 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {members.map((m) => (
            <MemberRow
              key={m.user_id}
              member={m}
              isOwner={m.user_id === ownerId}
            />
          ))}
        </tbody>
      </table>
    </div>
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
    <tr>
      <td className="px-3 py-3">
        <span className="min-w-0">
          <span className="block truncate text-sm">
            {member.display_name || "unnamed"}
            {isOwner && (
              <span className="label-caps ml-2 align-middle">owner</span>
            )}
          </span>
          <span className="meta block truncate">{member.user_id}</span>
        </span>
      </td>
      <td className="meta px-3 py-3">{member.machine_count}</td>
      <td className="meta px-3 py-3">{member.machines_online}</td>
      <td className="meta px-3 py-3 whitespace-nowrap">
        {relativeTime(member.joined_at)}
      </td>
    </tr>
  );
}
