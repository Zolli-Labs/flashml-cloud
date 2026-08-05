import { ZOLLI_ROLES, type ZolliRole } from "@/lib/zolli-brand";

type ZolliMood = "happy" | "focused" | "waving";

type ZolliCharacterProps = {
  role: ZolliRole;
  size?: number;
  mood?: ZolliMood;
  animated?: boolean;
  className?: string;
  label?: string;
};

function Body({ color }: { color: string }) {
  return (
    <g>
      <rect x="28" y="31" width="64" height="58" rx="27" fill={color} />
      <path d="M39 78c12 6 30 6 42 0" stroke="white" strokeOpacity="0.18" strokeWidth="3" strokeLinecap="round" />
    </g>
  );
}

function Eyes() {
  return (
    <g fill="#252321">
      <circle cx="51" cy="55" r="4" />
      <circle cx="69" cy="55" r="4" />
      <circle cx="49.7" cy="53.7" r="1.1" fill="white" />
      <circle cx="67.7" cy="53.7" r="1.1" fill="white" />
    </g>
  );
}

function Mouth({ mood }: { mood: ZolliMood }) {
  if (mood === "focused") {
    return <path d="M53 69h14" stroke="#252321" strokeWidth="3" strokeLinecap="round" />;
  }

  return <path d="M52 67c3 5 13 5 16 0" fill="none" stroke="#252321" strokeWidth="3" strokeLinecap="round" />;
}

function Arms({ mood }: { mood: ZolliMood }) {
  return (
    <g fill="none" stroke="#252321" strokeWidth="4" strokeLinecap="round">
      <path d="M30 67 19 76" />
      <path d={mood === "waving" ? "M90 64 102 43" : "M90 67l11 9"} />
      {mood === "waving" && <path d="m98 42 4-5m-2 10 6-1m-9-7-1-5" strokeWidth="2.5" />}
    </g>
  );
}

function RoleAccessory({ role }: { role: ZolliRole }) {
  const stroke = { fill: "none", stroke: "#252321", strokeWidth: 3, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };

  switch (role) {
    case "captain":
      return <g {...stroke}><path d="M44 34 48 22h24l4 12" /><path d="M50 22v-5m10 5v-7m10 7v-5" /></g>;
    case "worker":
      return <g {...stroke}><path d="m81 79 15 15" /><path d="m89 77 7 1-3 7" /><circle cx="78" cy="76" r="4" /></g>;
    case "scout":
      return <g {...stroke}><circle cx="47" cy="46" r="7" /><circle cx="73" cy="46" r="7" /><path d="M54 46h12m7 0 8-5m-42 5-8-5" /></g>;
    case "keeper":
      return <g {...stroke}><path d="M60 26 73 32v13c0 9-6 15-13 18-7-3-13-9-13-18V32l13-6Z" /><path d="m54 45 4 4 8-9" /></g>;
    case "relay":
      return <g {...stroke}><path d="M40 31h28l-5-5m5 5-5 5M80 39H52l5 5m-5-5 5-5" /></g>;
    case "builder":
      return <g {...stroke}><rect x="40" y="24" width="11" height="11" rx="2" /><rect x="55" y="18" width="11" height="11" rx="2" /><rect x="70" y="24" width="11" height="11" rx="2" /></g>;
  }
}

/** A small, role-aware Zolli mascot built from the shared crew silhouette. */
export function ZolliCharacter({
  role,
  size = 96,
  mood = "happy",
  animated = false,
  className = "",
  label,
}: ZolliCharacterProps) {
  const { color } = ZOLLI_ROLES[role];

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 120 120"
      className={className}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      {label && <title>{label}</title>}
      <g>
        {animated && <animateTransform attributeName="transform" type="translate" values="0 0;0 -3;0 0" dur="2.4s" repeatCount="indefinite" />}
        <Arms mood={mood} />
        <Body color={color} />
        <RoleAccessory role={role} />
        <Eyes />
        <Mouth mood={mood} />
      </g>
    </svg>
  );
}
