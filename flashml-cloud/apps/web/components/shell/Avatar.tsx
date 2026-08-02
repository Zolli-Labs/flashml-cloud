"use client";

import { useState } from "react";

/** Provider avatar with an initials fallback.
 *
 * A plain <img>, not next/image, deliberately. These URLs come from whatever
 * identity provider the user signed in with (lh3.googleusercontent.com,
 * avatars.githubusercontent.com, ...), so using the optimizer would mean
 * whitelisting every provider's CDN in next.config and breaking sign-in for
 * any provider we forgot. At 28-72px there is nothing for the optimizer to
 * save anyway.
 *
 * `onError` matters more than it looks: provider avatar URLs expire and
 * rotate, and a broken-image glyph where someone's face should be reads as a
 * broken app. Falling back to initials keeps it looking deliberate. */
export function Avatar({
  src,
  initials,
  size = 28,
  className = "",
}: {
  src: string | null;
  initials: string;
  size?: number;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const show = src && !failed;

  return (
    <span
      className={`inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full bg-surface-elevated ring-1 ring-border ${className}`}
      style={{ width: size, height: size }}
      aria-hidden
    >
      {show ? (
        // eslint-disable-next-line @next/next/no-img-element -- see above
        <img
          src={src}
          alt=""
          width={size}
          height={size}
          referrerPolicy="no-referrer"
          onError={() => setFailed(true)}
          className="h-full w-full object-cover"
        />
      ) : (
        <span
          className="font-mono font-medium text-muted-foreground"
          style={{ fontSize: Math.max(10, Math.round(size * 0.36)) }}
        >
          {initials}
        </span>
      )}
    </span>
  );
}
