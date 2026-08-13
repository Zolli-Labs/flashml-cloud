import { cn } from "@/lib/utils"

/**
 * The loading placeholder.
 *
 * This uses the `.skeleton` class from `globals.css` rather than its own
 * utilities, so the console has ONE loading language instead of three. It
 * previously rendered `animate-pulse rounded-md bg-muted`, and `--muted`
 * resolves through `--surface-2` to #f0eee8 — against the #f1efe9 page that is
 * one value per channel, so the placeholder was invisible and a loading panel
 * was indistinguishable from an empty one.
 *
 * `.skeleton` supplies the surface, the radius and the shimmer; callers still
 * pass their own size. `lib/loading-token.test.ts` asserts the surface stays
 * distinguishable from the page.
 */
function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn("skeleton", className)}
      {...props}
    />
  )
}

export { Skeleton }
