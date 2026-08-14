"use client";

import { useSyncExternalStore } from "react";
import { useTheme } from "next-themes";
import { Desktop, Moon, Sun, type Icon } from "@phosphor-icons/react";

/**
 * The console-only light/dark/system control, sitting next to `<UserMenu />`
 * in the top bar (`ConsoleShell`).
 *
 * `useTheme()` reports `theme` as `undefined` until next-themes has read
 * localStorage on the client, which happens after this component's first
 * server-rendered paint. Rendering the three buttons against that
 * `undefined` would draw a plausible-looking control with no segment active,
 * then repaint a moment later once the real value is known — a flash of
 * wrong state rather than a flash of no state. A fixed-size placeholder
 * avoids both the wrong render and any layout shift when the real control
 * takes its place.
 *
 * `useSyncExternalStore` rather than a `useEffect` + `setMounted(true)`:
 * setting state synchronously from an effect body is a cascading render,
 * flagged by `react-hooks/set-state-in-effect` — the same tradeoff
 * `components/machines/EnrolInstructions.tsx` documents for its own
 * server/client detection. The store never changes after load, so
 * `subscribe` is a no-op unsubscribe.
 */
const subscribeNever = () => () => {};
const getMountedClient = () => true;
const getMountedServer = () => false;

const OPTIONS: Array<{ value: "light" | "dark" | "system"; label: string; icon: Icon }> = [
  { value: "light", label: "Light theme", icon: Sun },
  { value: "dark", label: "Dark theme", icon: Moon },
  { value: "system", label: "System theme", icon: Desktop },
];

export function ThemeSwitcher() {
  const { theme, setTheme } = useTheme();
  const mounted = useSyncExternalStore(
    subscribeNever,
    getMountedClient,
    getMountedServer
  );

  if (!mounted) {
    return <div className="h-7 w-[84px] rounded-md border border-border" aria-hidden="true" />;
  }

  return (
    <div
      role="group"
      aria-label="Theme"
      className="flex h-7 items-center rounded-md border border-border p-0.5"
    >
      {OPTIONS.map(({ value, label, icon: OptionIcon }) => {
        const active = theme === value;
        return (
          <button
            key={value}
            type="button"
            onClick={() => setTheme(value)}
            aria-label={label}
            aria-pressed={active}
            title={label}
            className={`flex h-6 w-6 items-center justify-center rounded transition-colors ${
              active
                ? "bg-surface-2 text-ink"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            <OptionIcon size={14} weight={active ? "fill" : "regular"} />
          </button>
        );
      })}
    </div>
  );
}
