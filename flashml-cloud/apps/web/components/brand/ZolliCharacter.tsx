import Image from "next/image";
import type { ZolliRole } from "@/lib/zolli-brand";

type ZolliMood = "happy" | "focused" | "waving";

type ZolliCharacterProps = {
  role: ZolliRole;
  size?: number;
  mood?: ZolliMood;
  animated?: boolean;
  className?: string;
  label?: string;
};

const CHARACTER_ASSETS: Record<ZolliRole, string> = {
  captain: "/brand/characters/captain.png",
  worker: "/brand/characters/worker.png",
  scout: "/brand/characters/scout.png",
  keeper: "/brand/characters/keeper.png",
  relay: "/brand/characters/relay.png",
  builder: "/brand/characters/builder.png",
};

/** The official standalone crew artwork behind the existing mascot API. */
export function ZolliCharacter({
  role,
  size = 96,
  animated = false,
  className = "",
  label,
}: ZolliCharacterProps) {
  return (
    <span
      className={`relative inline-block shrink-0 ${
        animated
          ? "motion-safe:animate-bounce motion-reduce:animate-none"
          : ""
      } ${className}`}
      style={{ width: size, height: size }}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      <Image
        src={CHARACTER_ASSETS[role]}
        alt=""
        fill
        sizes={`${size}px`}
        className="select-none object-cover"
        draggable={false}
      />
    </span>
  );
}
