/** The ZolliAI mark: a Z formed from a connected crew of compute nodes. */
export function Mark({
  size = 24,
  className = "",
}: {
  size?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      aria-hidden
    >
      <g stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 5h16L4 19h16" />
        <path d="m4 5 8 7 8-7M4 19l8-7 8 7" opacity="0.42" />
      </g>
      <g fill="currentColor">
        <circle cx="4" cy="5" r="2" />
        <circle cx="20" cy="5" r="2" />
        <circle cx="12" cy="12" r="2" />
        <circle cx="4" cy="19" r="2" />
        <circle cx="20" cy="19" r="2" />
      </g>
    </svg>
  );
}

/** The connected-node mark paired with the ZolliAI wordmark. */
export function Wordmark({
  size = 22,
  className = "",
  product = false,
}: {
  size?: number;
  className?: string;
  product?: boolean;
}) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <Mark size={size} className="text-foreground" />
      <span className="font-mono text-sm font-bold tracking-tight">
        Zolli<span className="text-primary">AI</span>{product && <span className="text-muted-foreground"> Cloud</span>}
      </span>
    </span>
  );
}
