import { cn } from "@/lib/utils"

function Kbd({ className, ...props }: React.ComponentProps<"kbd">) {
  // No `font-sans` here (dropped from the stock treatment): that utility is
  // this codebase's `--font-sans`, which is hardcoded to Instrument Sans —
  // pinning it would render Instrument on every `<Kbd>` inside the console
  // even though `.console-theme` sets an inherited Geist Sans face for
  // everything else. Plain inheritance already gives the right face in both
  // places: Instrument on marketing/auth (body's own `font-sans`), Geist on
  // console pages — this component is used by both (`Shortcuts.tsx` and
  // `app/(console)/docs/page.tsx` on the console side).
  return (
    <kbd
      data-slot="kbd"
      className={cn(
        "pointer-events-none inline-flex h-5 w-fit min-w-5 items-center justify-center gap-1 rounded-sm bg-muted px-1 text-xs font-medium text-muted-foreground select-none in-data-[slot=tooltip-content]:bg-background/20 in-data-[slot=tooltip-content]:text-background dark:in-data-[slot=tooltip-content]:bg-background/10 [&_svg:not([class*='size-'])]:size-3",
        className
      )}
      {...props}
    />
  )
}

function KbdGroup({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <kbd
      data-slot="kbd-group"
      className={cn("inline-flex items-center gap-1", className)}
      {...props}
    />
  )
}

export { Kbd, KbdGroup }
