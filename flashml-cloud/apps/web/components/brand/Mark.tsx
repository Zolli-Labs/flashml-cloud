import Image from "next/image";

const SYMBOL_SRC = "/brand/logos/logo-symbol-orange.png";
const WORDMARK_SRC = "/brand/logos/logo-primary.png";
const WORDMARK_DARK_SRC = "/brand/logos/logo-reversed-white.png";

/** The canonical connected-node Zolli symbol. Decorative by default. */
export function Mark({
  size = 24,
  className = "",
}: {
  size?: number;
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={`relative inline-block shrink-0 ${className}`}
      style={{ width: size, height: size }}
    >
      <Image
        src={SYMBOL_SRC}
        alt=""
        fill
        sizes={`${size}px`}
        className="object-contain"
      />
    </span>
  );
}

/** The canonical horizontal Zolli lockup, optionally paired with Cloud. */
export function Wordmark({
  size = 22,
  className = "",
  product = false,
  tone = "light",
}: {
  size?: number;
  className?: string;
  product?: boolean;
  tone?: "light" | "dark";
}) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <Image
        src={tone === "dark" ? WORDMARK_DARK_SRC : WORDMARK_SRC}
        alt="Zolli"
        width={1200}
        height={400}
        sizes={`${size * 3}px`}
        className="h-auto shrink-0 object-contain"
        style={{ width: size * 3 }}
      />
      {product && (
        <span className="font-mono text-[11px] font-medium tracking-tight text-muted-foreground">
          Cloud
        </span>
      )}
    </span>
  );
}
