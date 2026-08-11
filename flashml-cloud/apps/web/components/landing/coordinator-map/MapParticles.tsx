"use client";

// The three protocol events, drawn as things that move.
//
//   · an orange token leaving the core   — a task lease
//   · a soft ring pulsing at a source    — a heartbeat
//   · a pale token arriving at the core  — a checkpoint
//
// Tokens travel *along the route*, not straight at their destination. A dot
// cutting the corner of an elbow route is the fastest way to make an
// axis-aligned map look broken, so the polyline is read back out of the very
// path string `elbowPath` emitted. That keeps this component downstream of the
// routing rule rather than re-deriving the corner and drifting from it.
//
// Each token carries a short fading tail. Direction then reads in a single
// frame instead of requiring the viewer to track a dot across two seconds, and
// out-and-back traffic on one route stays tellable apart.
//
// Only the job's route carries orange. Idle routes get sparse, dim chatter —
// enough to say the fabric is connected, not enough to claim work is on it.

import { useEffect, useMemo, useRef } from "react";
import {
  MAP_NODES,
  corePoint,
  elbowPath,
  nodeAnchor,
  routeStateFor,
  type MapNodeKey,
  type MapPhase,
  type Point,
  type Viewport,
} from "@/lib/coordinator-map";
import { MAP_COLORS, r2 } from "./iso";

/** Fixed pools. Nothing is created or destroyed per frame — slots are handed
 * out and handed back, so a sixteen-second story does not churn the DOM. */
const TOKEN_SLOTS = 16;
const RING_SLOTS = 10;

/** Emission intervals, in seconds. Carried over from the approved prototype:
 * leases outpace checkpoints roughly two to one, which is what makes the map
 * read as work going out faster than state comes back. */
const HEARTBEAT_PERIOD = 1.15;
const LEASE_PERIOD = 0.62;
const CHECKPOINT_PERIOD = 1.45;
const CHATTER_PERIOD = 1.9;

const RING_SPEED = 0.55;
const RING_BASE_RADIUS = 4;
const RING_GROWTH = 26;

/** The token glyph is drawn once at this size and scaled per emission. */
const TOKEN_UNIT = 6;

interface Track {
  readonly key: MapNodeKey;
  readonly points: readonly Point[];
  /** Cumulative length up to each point. */
  readonly steps: readonly number[];
  readonly total: number;
}

interface Sample {
  readonly x: number;
  readonly y: number;
  readonly angle: number;
}

const NUMBER = /-?\d+(?:\.\d+)?/g;

function tracksFor(vp: Viewport): readonly Track[] {
  const core = corePoint(vp);
  return MAP_NODES.map((spec) => {
    const numbers = elbowPath(core, nodeAnchor(spec, vp)).match(NUMBER) ?? [];
    const points: Point[] = [];
    for (let index = 0; index + 1 < numbers.length; index += 2) {
      points.push({ x: Number(numbers[index]), y: Number(numbers[index + 1]) });
    }
    const steps: number[] = [0];
    let total = 0;
    for (let index = 1; index < points.length; index++) {
      total += Math.hypot(
        points[index].x - points[index - 1].x,
        points[index].y - points[index - 1].y,
      );
      steps.push(total);
    }
    return { key: spec.key, points, steps, total };
  });
}

/** Position and heading a fraction `t` of the way along a track. */
function sample(track: Track, t: number): Sample {
  if (track.points.length < 2 || track.total === 0) {
    const only = track.points[0] ?? { x: 0, y: 0 };
    return { x: only.x, y: only.y, angle: 0 };
  }
  const want = Math.min(1, Math.max(0, t)) * track.total;
  let index = 1;
  while (index < track.steps.length - 1 && track.steps[index] < want) index++;
  const from = track.points[index - 1];
  const to = track.points[index];
  const span = track.steps[index] - track.steps[index - 1] || 1;
  const fraction = (want - track.steps[index - 1]) / span;
  return {
    x: from.x + (to.x - from.x) * fraction,
    y: from.y + (to.y - from.y) * fraction,
    angle: (Math.atan2(to.y - from.y, to.x - from.x) * 180) / Math.PI,
  };
}

/** Sample a track in whichever direction a token is travelling. Reversed
 * tokens read the same geometry backwards and turn their tail around. */
function sampleDirected(track: Track, t: number, reverse: boolean): Sample {
  const point = sample(track, reverse ? 1 - t : t);
  return reverse ? { ...point, angle: point.angle + 180 } : point;
}

/** The one route carrying the job, if any. Derived from `routeStateFor` rather
 * than restated, so the story table lives in exactly one place. */
function activeKeyFor(phase: MapPhase): MapNodeKey | null {
  const carrying = MAP_NODES.find((spec) => routeStateFor(spec.key, phase) === "active");
  return carrying ? carrying.key : null;
}

/** Sources still emitting heartbeats. A machine whose route has failed or been
 * left behind has gone quiet, and the silence is the point. */
function liveKeysFor(phase: MapPhase): readonly MapNodeKey[] {
  return MAP_NODES.filter((spec) => {
    const state = routeStateFor(spec.key, phase);
    return state !== "failed" && state !== "dimmed";
  }).map((spec) => spec.key);
}

export interface MapParticlesProps {
  readonly vp: Viewport;
  readonly phase: MapPhase;
  /** `prefers-reduced-motion: reduce`. Renders one representative frame and
   * starts no animation loop at all — not a paused one. */
  readonly reduced: boolean;
  /** The tab is hidden. Stops the loop without changing what is on screen. */
  readonly paused?: boolean;
}

export function MapParticles({ vp, phase, reduced, paused = false }: MapParticlesProps) {
  if (reduced) return <StaticParticles vp={vp} phase={phase} />;
  return <AnimatedParticles vp={vp} phase={phase} paused={paused} />;
}

/* ------------------------------------------------------------------ */
/* the still frame                                                     */
/* ------------------------------------------------------------------ */

function Token({
  at,
  size,
  color,
  opacity = 1,
}: {
  readonly at: Sample;
  readonly size: number;
  readonly color: string;
  readonly opacity?: number;
}) {
  return (
    <g
      color={color}
      opacity={opacity}
      transform={`translate(${r2(at.x)} ${r2(at.y)}) rotate(${r2(at.angle)}) scale(${r2(size / TOKEN_UNIT)})`}
    >
      <TokenGlyph />
    </g>
  );
}

/**
 * The token: a diamond head with a two-step tail behind it.
 *
 * Two stepped segments rather than one flat line, because a uniform tail reads
 * as a dash and a dash has no direction. Stepping the opacity is also cheaper
 * than a gradient — the whole glyph is emitted once and only ever transformed.
 */
function TokenGlyph() {
  return (
    <>
      <line
        x1={-21}
        y1={0}
        x2={-11}
        y2={0}
        stroke="currentColor"
        strokeWidth={1.6}
        strokeLinecap="round"
        opacity={0.22}
      />
      <line
        x1={-11.5}
        y1={0}
        x2={-3.2}
        y2={0}
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        opacity={0.55}
      />
      <rect x={-3} y={-3} width={6} height={6} fill="currentColor" transform="rotate(45)" />
    </>
  );
}

/**
 * One representative frame: mid-flight leases on the live route, a checkpoint
 * on its way back, and a heartbeat standing at every machine still talking.
 *
 * It has to be a real moment from the story rather than an empty map, because
 * for a visitor who has reduced motion switched on this is the entire hero.
 */
function StaticParticles({ vp, phase }: { readonly vp: Viewport; readonly phase: MapPhase }) {
  const tracks = tracksFor(vp);
  const activeKey = activeKeyFor(phase);
  const live = liveKeysFor(phase);
  const activeTrack = tracks.find((track) => track.key === activeKey);
  const jobColor = phase === "resumed" ? MAP_COLORS.jobHot : MAP_COLORS.job;

  return (
    <g data-map-layer="particles" data-motion="static" aria-hidden="true">
      {MAP_NODES.filter((spec) => live.includes(spec.key)).map((spec) => {
        const anchor = nodeAnchor(spec, vp);
        return (
          <circle
            key={spec.key}
            cx={r2(anchor.x)}
            cy={r2(anchor.y)}
            r={22}
            fill="none"
            stroke={MAP_COLORS.lineStrong}
            strokeWidth={1.1}
            opacity={0.34}
          />
        );
      })}

      {activeTrack
        ? [0.3, 0.56, 0.82].map((t) => (
            <Token key={`lease-${t}`} at={sampleDirected(activeTrack, t, false)} size={6.5} color={jobColor} />
          ))
        : null}
      {activeTrack ? (
        <Token
          at={sampleDirected(activeTrack, 0.45, true)}
          size={5}
          color={MAP_COLORS.dim}
          opacity={0.85}
        />
      ) : null}
    </g>
  );
}

/* ------------------------------------------------------------------ */
/* the running loop                                                    */
/* ------------------------------------------------------------------ */

interface LiveToken {
  slot: number;
  track: Track;
  t: number;
  speed: number;
  reverse: boolean;
  size: number;
}

interface LiveRing {
  slot: number;
  t: number;
}

function AnimatedParticles({
  vp,
  phase,
  paused,
}: {
  readonly vp: Viewport;
  readonly phase: MapPhase;
  readonly paused: boolean;
}) {
  // Keyed on the viewport's five numbers rather than its object identity. A
  // caller that builds the viewport inline would otherwise hand this component
  // a new object every render, tearing down and restarting the loop — and a
  // restarted loop resets the clock, so every token in flight would jump.
  const { width, height, scale, originX, originY } = vp;
  const geometry = useMemo(() => {
    const frame: Viewport = { width, height, scale, originX, originY };
    return {
      tracks: tracksFor(frame),
      anchors: new Map(MAP_NODES.map((spec) => [spec.key, nodeAnchor(spec, frame)])),
    };
  }, [width, height, scale, originX, originY]);
  const { tracks, anchors } = geometry;
  const tokenRefs = useRef<(SVGGElement | null)[]>([]);
  const ringRefs = useRef<(SVGCircleElement | null)[]>([]);
  // The loop reads the phase through a ref so a beat change does not restart
  // it. Restarting would reset the clock and stutter every token in flight.
  const phaseRef = useRef(phase);

  useEffect(() => {
    phaseRef.current = phase;
  }, [phase]);

  useEffect(() => {
    if (paused) return;

    // The two pools are read and reset from inside the loop and from its
    // cleanup, so they are captured once here: `.current` is never reassigned,
    // only its slots are filled by the ref callbacks.
    const tokenNodes = tokenRefs.current;
    const ringNodes = ringRefs.current;
    const byKey = new Map(tracks.map((track) => [track.key, track]));

    const freeTokens = Array.from({ length: TOKEN_SLOTS }, (_, index) => index);
    const freeRings = Array.from({ length: RING_SLOTS }, (_, index) => index);
    const tokens: LiveToken[] = [];
    const rings: LiveRing[] = [];

    const emit = (track: Track, reverse: boolean, color: string, size: number, speed: number) => {
      const slot = freeTokens.pop();
      if (slot === undefined) return;
      const element = tokenNodes[slot];
      if (element) element.style.color = color;
      tokens.push({ slot, track, t: 0, speed, reverse, size });
    };

    const beat = (at: Point) => {
      const slot = freeRings.pop();
      if (slot === undefined) return;
      const element = ringNodes[slot];
      if (element) {
        element.setAttribute("cx", String(r2(at.x)));
        element.setAttribute("cy", String(r2(at.y)));
      }
      rings.push({ slot, t: 0 });
    };

    // Seeded one full interval in the past so the map is already working on the
    // first painted frame rather than empty for half a second.
    let time = 0;
    let lastBeat = -HEARTBEAT_PERIOD;
    let lastLease = -LEASE_PERIOD;
    let lastCheckpoint = -CHECKPOINT_PERIOD;
    let lastChatter = -CHATTER_PERIOD;
    let chatterIndex = 0;
    let previous = performance.now();
    let raf = 0;

    const frame = (now: number) => {
      const dt = Math.min(0.05, (now - previous) / 1000);
      previous = now;
      time += dt;

      const current = phaseRef.current;
      const activeKey = activeKeyFor(current);
      const activeTrack = activeKey ? byKey.get(activeKey) : undefined;
      const live = liveKeysFor(current);

      if (time - lastBeat > HEARTBEAT_PERIOD) {
        lastBeat = time;
        for (const key of live) {
          const at = anchors.get(key);
          if (at) beat(at);
        }
      }

      if (activeTrack && time - lastLease > LEASE_PERIOD) {
        lastLease = time;
        const hot = current === "resumed";
        emit(activeTrack, false, hot ? MAP_COLORS.jobHot : MAP_COLORS.job, hot ? 7 : 6.5, hot ? 0.5 : 0.34);
      }

      if (activeTrack && time - lastCheckpoint > CHECKPOINT_PERIOD) {
        lastCheckpoint = time;
        emit(activeTrack, true, MAP_COLORS.dim, 5, 0.3);
      }

      if (time - lastChatter > CHATTER_PERIOD) {
        lastChatter = time;
        const idle = tracks.filter(
          (track) => routeStateFor(track.key, current) === "idle" && live.includes(track.key),
        );
        if (idle.length > 0) {
          const track = idle[chatterIndex % idle.length];
          emit(track, chatterIndex % 2 === 0, MAP_COLORS.faint, 3.6, 0.26);
          chatterIndex++;
        }
      }

      for (let index = tokens.length - 1; index >= 0; index--) {
        const token = tokens[index];
        token.t += dt * token.speed;
        const element = tokenNodes[token.slot];
        if (token.t >= 1) {
          if (element) element.setAttribute("opacity", "0");
          freeTokens.push(token.slot);
          tokens.splice(index, 1);
          continue;
        }
        if (!element) continue;
        const at = sampleDirected(token.track, token.t, token.reverse);
        element.setAttribute(
          "transform",
          `translate(${r2(at.x)} ${r2(at.y)}) rotate(${r2(at.angle)}) scale(${r2(token.size / TOKEN_UNIT)})`,
        );
        // Fade in off the core and out into the machine, so nothing pops.
        element.setAttribute(
          "opacity",
          String(r2(Math.sin(Math.PI * Math.min(1, token.t * 1.15)) * 0.95)),
        );
      }

      for (let index = rings.length - 1; index >= 0; index--) {
        const ring = rings[index];
        ring.t += dt * RING_SPEED;
        const element = ringNodes[ring.slot];
        if (ring.t >= 1) {
          if (element) element.setAttribute("opacity", "0");
          freeRings.push(ring.slot);
          rings.splice(index, 1);
          continue;
        }
        if (!element) continue;
        element.setAttribute("r", String(r2(RING_BASE_RADIUS + ring.t * RING_GROWTH)));
        element.setAttribute("opacity", String(r2((1 - ring.t) * 0.5)));
      }

      raf = requestAnimationFrame(frame);
    };

    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      for (const element of tokenNodes) element?.setAttribute("opacity", "0");
      for (const element of ringNodes) element?.setAttribute("opacity", "0");
    };
  }, [anchors, paused, tracks]);

  return (
    <g data-map-layer="particles" data-motion="running" aria-hidden="true">
      {Array.from({ length: RING_SLOTS }, (_, index) => (
        <circle
          key={`ring-${index}`}
          ref={(element) => {
            ringRefs.current[index] = element;
          }}
          r={RING_BASE_RADIUS}
          fill="none"
          stroke={MAP_COLORS.lineStrong}
          strokeWidth={1.1}
          opacity={0}
        />
      ))}
      {Array.from({ length: TOKEN_SLOTS }, (_, index) => (
        <g
          key={`token-${index}`}
          ref={(element) => {
            tokenRefs.current[index] = element;
          }}
          opacity={0}
        >
          <TokenGlyph />
        </g>
      ))}
    </g>
  );
}
