import { redirect } from "next/navigation";

/** `/w/<id>` alone names a workspace but no tab. Overview is the tab a
 * workspace opens on, so send it there rather than rendering a fifth thing
 * that is really the same page. */
export default async function WorkspaceIndex({
  params,
}: {
  params: Promise<{ poolId: string }>;
}) {
  const { poolId } = await params;
  redirect(`/w/${encodeURIComponent(poolId)}/overview`);
}
