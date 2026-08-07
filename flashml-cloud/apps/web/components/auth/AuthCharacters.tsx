"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import { useReducedMotion } from "motion/react";
import { useAuthInteraction } from "@/components/auth/AuthInteraction";

const COLOR = {
  peeker: "#ef6828",
  mid: "#1a1714",
  blob: "#1f6e5d",
  side: "#e7ad2b",
  pupil: "#1a1714",
  eye: "#fffdf9",
};

const clamp = (value: number, min: number, max: number) =>
  Math.max(min, Math.min(max, value));

function offsetToward(
  element: HTMLElement | null,
  pointerX: number,
  pointerY: number,
  maxDistance: number
) {
  if (!element) return { x: 0, y: 0 };
  const bounds = element.getBoundingClientRect();
  const dx = pointerX - (bounds.left + bounds.width / 2);
  const dy = pointerY - (bounds.top + bounds.height / 2);
  const distance = Math.min(Math.hypot(dx, dy), maxDistance);
  const angle = Math.atan2(dy, dx);
  return {
    x: Math.cos(angle) * distance,
    y: Math.sin(angle) * distance,
  };
}

function Pupil({
  size = 12,
  maxDistance = 5,
  forceLookX,
  forceLookY,
}: {
  size?: number;
  maxDistance?: number;
  forceLookX?: number;
  forceLookY?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const forced = forceLookX !== undefined && forceLookY !== undefined;

  useEffect(() => {
    if (forced) return;
    const onMove = (event: MouseEvent) =>
      setOffset(
        offsetToward(
          ref.current,
          event.clientX,
          event.clientY,
          maxDistance
        )
      );
    window.addEventListener("mousemove", onMove);
    return () => window.removeEventListener("mousemove", onMove);
  }, [forced, maxDistance]);

  const position = forced
    ? { x: forceLookX, y: forceLookY }
    : offset;

  return (
    <div
      ref={ref}
      className="rounded-full"
      style={{
        width: size,
        height: size,
        backgroundColor: COLOR.pupil,
        transform: `translate(${position.x}px, ${position.y}px)`,
        transition: "transform 100ms ease-out",
      }}
    />
  );
}

function EyeBall({
  size = 18,
  pupilSize = 7,
  maxDistance = 5,
  isBlinking = false,
  forceLookX,
  forceLookY,
}: {
  size?: number;
  pupilSize?: number;
  maxDistance?: number;
  isBlinking?: boolean;
  forceLookX?: number;
  forceLookY?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const forced = forceLookX !== undefined && forceLookY !== undefined;

  useEffect(() => {
    if (forced) return;
    const onMove = (event: MouseEvent) =>
      setOffset(
        offsetToward(
          ref.current,
          event.clientX,
          event.clientY,
          maxDistance
        )
      );
    window.addEventListener("mousemove", onMove);
    return () => window.removeEventListener("mousemove", onMove);
  }, [forced, maxDistance]);

  const position = forced
    ? { x: forceLookX, y: forceLookY }
    : offset;

  return (
    <div
      ref={ref}
      className="flex items-center justify-center overflow-hidden rounded-full transition-all duration-150"
      style={{
        width: size,
        height: isBlinking ? 2 : size,
        backgroundColor: COLOR.eye,
      }}
    >
      {!isBlinking ? (
        <div
          className="rounded-full"
          style={{
            width: pupilSize,
            height: pupilSize,
            backgroundColor: COLOR.pupil,
            transform: `translate(${position.x}px, ${position.y}px)`,
            transition: "transform 100ms ease-out",
          }}
        />
      ) : null}
    </div>
  );
}

type Pose = { faceX: number; faceY: number; skew: number };
const FLAT: Pose = { faceX: 0, faceY: 0, skew: 0 };

export function AuthCharacters() {
  const { typing, hasPassword, passwordVisible } = useAuthInteraction();
  const reducedMotion = useReducedMotion();
  const [peekerBlink, setPeekerBlink] = useState(false);
  const [midBlink, setMidBlink] = useState(false);
  const [glance, setGlance] = useState(false);
  const [peeking, setPeeking] = useState(false);

  const peekerRef = useRef<HTMLDivElement>(null);
  const midRef = useRef<HTMLDivElement>(null);
  const blobRef = useRef<HTMLDivElement>(null);
  const sideRef = useRef<HTMLDivElement>(null);

  const [pose, setPose] = useState({
    peeker: FLAT,
    mid: FLAT,
    blob: FLAT,
    side: FLAT,
  });

  useEffect(() => {
    if (reducedMotion) return;
    const calculate = (
      ref: RefObject<HTMLDivElement | null>,
      pointerX: number,
      pointerY: number
    ): Pose => {
      const element = ref.current;
      if (!element) return FLAT;
      const bounds = element.getBoundingClientRect();
      const dx = pointerX - (bounds.left + bounds.width / 2);
      const dy = pointerY - (bounds.top + bounds.height / 3);
      return {
        faceX: clamp(dx / 20, -15, 15),
        faceY: clamp(dy / 30, -10, 10),
        skew: clamp(-dx / 120, -6, 6),
      };
    };

    const onMove = (event: MouseEvent) =>
      setPose({
        peeker: calculate(peekerRef, event.clientX, event.clientY),
        mid: calculate(midRef, event.clientX, event.clientY),
        blob: calculate(blobRef, event.clientX, event.clientY),
        side: calculate(sideRef, event.clientX, event.clientY),
      });

    window.addEventListener("mousemove", onMove);
    return () => window.removeEventListener("mousemove", onMove);
  }, [reducedMotion]);

  useEffect(() => {
    if (reducedMotion) return;
    const timers: Array<ReturnType<typeof setTimeout>> = [];
    const schedule = (setBlinking: (value: boolean) => void) => {
      const loop = () => {
        const timer = setTimeout(() => {
          setBlinking(true);
          timers.push(setTimeout(() => setBlinking(false), 150));
          loop();
        }, Math.random() * 4000 + 3000);
        timers.push(timer);
      };
      loop();
    };
    schedule(setPeekerBlink);
    schedule(setMidBlink);
    return () => timers.forEach(clearTimeout);
  }, [reducedMotion]);

  useEffect(() => {
    if (reducedMotion || !typing) return;
    const start = setTimeout(() => setGlance(true), 0);
    const end = setTimeout(() => setGlance(false), 800);
    return () => {
      clearTimeout(start);
      clearTimeout(end);
      setGlance(false);
    };
  }, [typing, reducedMotion]);

  useEffect(() => {
    if (reducedMotion || !(hasPassword && passwordVisible)) return;
    let inner: ReturnType<typeof setTimeout> | undefined;
    const timer = setTimeout(() => {
      setPeeking(true);
      inner = setTimeout(() => setPeeking(false), 800);
    }, 2200);
    return () => {
      clearTimeout(timer);
      if (inner) clearTimeout(inner);
      setPeeking(false);
    };
  }, [hasPassword, passwordVisible, reducedMotion]);

  const { peeker, mid, blob, side } = pose;
  const shy = hasPassword && passwordVisible;
  const covering = typing || (hasPassword && !passwordVisible);
  const caption = shy
    ? peeking
      ? "Okay, peeking a little…"
      : "Eyes off — your secret's safe."
    : hasPassword
      ? "Shhh, we're not looking."
      : typing
        ? "We're all ears."
        : "Meet the Zolli Crew.";

  return (
    <div className="flex flex-col items-center" aria-label={caption}>
      <p className="mb-4 text-sm font-medium text-muted-foreground transition-opacity duration-300">
        {caption}
      </p>

      <div
        aria-hidden
        className="relative origin-bottom scale-[0.62] xl:scale-[0.78] 2xl:scale-90"
        style={{ width: 460, height: 360 }}
      >
        <div
          ref={peekerRef}
          className="absolute bottom-0 transition-all duration-700 ease-in-out"
          style={{
            left: 60,
            width: 150,
            height: covering ? 380 : 340,
            backgroundColor: COLOR.peeker,
            borderRadius: "10px 10px 0 0",
            zIndex: 1,
            transform: shy
              ? "skewX(0deg)"
              : covering
                ? `skewX(${peeker.skew - 12}deg) translateX(36px)`
                : `skewX(${peeker.skew}deg)`,
            transformOrigin: "bottom center",
          }}
        >
          <div
            className="absolute flex gap-7 transition-all duration-700 ease-in-out"
            style={{
              left: shy ? 18 : glance ? 48 : 38 + peeker.faceX,
              top: shy ? 30 : glance ? 56 : 34 + peeker.faceY,
            }}
          >
            {[0, 1].map((index) => (
              <EyeBall
                key={index}
                isBlinking={peekerBlink}
                forceLookX={shy ? (peeking ? 4 : -4) : glance ? 3 : undefined}
                forceLookY={shy ? (peeking ? 5 : -4) : glance ? 4 : undefined}
              />
            ))}
          </div>
        </div>

        <div
          ref={midRef}
          className="absolute bottom-0 transition-all duration-700 ease-in-out"
          style={{
            left: 200,
            width: 100,
            height: 264,
            backgroundColor: COLOR.mid,
            borderRadius: "8px 8px 0 0",
            zIndex: 2,
            transform: shy
              ? "skewX(0deg)"
              : glance
                ? `skewX(${mid.skew * 1.5 + 10}deg) translateX(18px)`
                : `skewX(${mid.skew * 1.5}deg)`,
            transformOrigin: "bottom center",
          }}
        >
          <div
            className="absolute flex gap-5 transition-all duration-700 ease-in-out"
            style={{
              left: shy ? 8 : glance ? 28 : 22 + mid.faceX,
              top: shy ? 24 : glance ? 10 : 28 + mid.faceY,
            }}
          >
            {[0, 1].map((index) => (
              <EyeBall
                key={index}
                size={16}
                pupilSize={6}
                maxDistance={4}
                isBlinking={midBlink}
                forceLookX={shy ? -4 : glance ? 0 : undefined}
                forceLookY={shy ? -4 : glance ? -4 : undefined}
              />
            ))}
          </div>
        </div>

        <div
          ref={blobRef}
          className="absolute bottom-0 transition-all duration-700 ease-in-out"
          style={{
            left: 0,
            width: 200,
            height: 168,
            backgroundColor: COLOR.blob,
            borderRadius: "100px 100px 0 0",
            zIndex: 3,
            transform: shy ? "skewX(0deg)" : `skewX(${blob.skew}deg)`,
            transformOrigin: "bottom center",
          }}
        >
          <div
            className="absolute flex gap-7 transition-all duration-200 ease-out"
            style={{
              left: shy ? 42 : 70 + blob.faceX,
              top: shy ? 72 : 76 + blob.faceY,
            }}
          >
            {[0, 1].map((index) => (
              <Pupil
                key={index}
                size={11}
                forceLookX={shy ? -5 : undefined}
                forceLookY={shy ? -4 : undefined}
              />
            ))}
          </div>
        </div>

        <div
          ref={sideRef}
          className="absolute bottom-0 transition-all duration-700 ease-in-out"
          style={{
            left: 258,
            width: 118,
            height: 194,
            backgroundColor: COLOR.side,
            borderRadius: "60px 60px 0 0",
            zIndex: 4,
            transform: shy ? "skewX(0deg)" : `skewX(${side.skew}deg)`,
            transformOrigin: "bottom center",
          }}
        >
          <div
            className="absolute flex gap-5 transition-all duration-200 ease-out"
            style={{
              left: shy ? 16 : 44 + side.faceX,
              top: shy ? 30 : 34 + side.faceY,
            }}
          >
            {[0, 1].map((index) => (
              <Pupil
                key={index}
                size={11}
                forceLookX={shy ? -5 : undefined}
                forceLookY={shy ? -4 : undefined}
              />
            ))}
          </div>
          <div
            className="absolute h-1 w-16 rounded-full transition-all duration-200 ease-out"
            style={{
              backgroundColor: COLOR.pupil,
              left: shy ? 8 : 34 + side.faceX,
              top: shy ? 74 : 74 + side.faceY,
            }}
          />
        </div>
      </div>
    </div>
  );
}
