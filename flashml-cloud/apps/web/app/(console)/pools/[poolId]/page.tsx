import { redirect } from "next/navigation";

/** The pool detail page moved to `/w/<poolId>/overview` as part of the
 * workspace-scoped console; its four sections now live in
 * `components/workspace/` and are shared across the workspace tabs. This
 * keeps the old URL working rather than 404ing anyone who bookmarked it. */
export default async function PoolRedirect({
  params,
}: {
  params: Promise<{ poolId: string }>;
}) {
  const { poolId } = await params;
  redirect(`/w/${encodeURIComponent(poolId)}/overview`);
}
