import Image from "next/image";
import type { ReactNode } from "react";

interface FabricFallbackProps {
  reason: "loading" | "webgl";
  children: ReactNode;
}

const POSTER_ALT =
  "Compute from everyday machines, owned infrastructure, rented GPUs, and cloud HPC connected by the Zolli control plane";

export function FabricFallback({ reason, children }: FabricFallbackProps) {
  return (
    <figure className="relative m-0 min-h-[24rem] overflow-hidden rounded-[1.5rem] bg-[#0c1011]">
      <Image
        src="/images/hero/fabric-poster.webp"
        alt={POSTER_ALT}
        fill
        sizes="(max-width: 768px) 100vw, 75vw"
        className="absolute inset-0 h-full w-full object-cover"
      />
      <figcaption className="absolute inset-x-4 top-4 rounded-full border border-white/10 bg-black/55 px-4 py-2 text-sm text-[#f2efe6] backdrop-blur-sm">
        <span role="status" aria-live="polite">
          {reason === "loading"
            ? "Preparing the compute fabric…"
            : "Interactive 3D is unavailable"}
        </span>
      </figcaption>
      <div className="absolute inset-x-4 bottom-4">{children}</div>
    </figure>
  );
}
