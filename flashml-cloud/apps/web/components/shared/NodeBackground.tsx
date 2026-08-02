"use client";

import { useEffect, useRef } from "react";
import { useReducedMotion } from "motion/react";

interface Node {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  opacity: number;
}

interface Connection {
  from: number;
  to: number;
  progress: number;
  speed: number;
  active: boolean;
}

export function NodeBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const prefersReducedMotion = useReducedMotion();

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || prefersReducedMotion) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = canvas.offsetWidth * dpr;
      canvas.height = canvas.offsetHeight * dpr;
      // setTransform, not scale. `scale` MULTIPLIES the existing matrix,
      // so every resize compounded the previous one and the canvas drew
      // progressively further off-screen.
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    resize();
    window.addEventListener("resize", resize);

    const NODE_COUNT = 28;
    const nodes: Node[] = Array.from({ length: NODE_COUNT }, () => ({
      x: Math.random() * (canvas.offsetWidth || 1200),
      y: Math.random() * (canvas.offsetHeight || 600),
      vx: (Math.random() - 0.5) * 0.3,
      vy: (Math.random() - 0.5) * 0.3,
      size: Math.random() * 2 + 1,
      opacity: Math.random() * 0.4 + 0.2,
    }));

    const connections: Connection[] = [];
    for (let i = 0; i < NODE_COUNT; i++) {
      for (let j = i + 1; j < NODE_COUNT; j++) {
        if (Math.random() < 0.15) {
          connections.push({
            from: i,
            to: j,
            progress: Math.random(),
            speed: Math.random() * 0.004 + 0.001,
            active: Math.random() < 0.3,
          });
        }
      }
    }

    let raf: number;

    const draw = () => {
      const w = canvas.offsetWidth;
      const h = canvas.offsetHeight;

      ctx.clearRect(0, 0, w, h);

      // Draw connections
      connections.forEach((conn) => {
        const a = nodes[conn.from];
        const b = nodes[conn.to];
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy);

        if (dist > 350) return;

        const alpha = (1 - dist / 350) * 0.12;

        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.strokeStyle = `oklch(0.70 0.17 285 /${alpha})`;
        ctx.lineWidth = 0.5;
        ctx.stroke();

        // Animated data packet on active connections
        if (conn.active) {
          conn.progress = (conn.progress + conn.speed) % 1;
          const px = a.x + dx * conn.progress;
          const py = a.y + dy * conn.progress;

          ctx.beginPath();
          ctx.arc(px, py, 2, 0, Math.PI * 2);
          ctx.fillStyle = `oklch(0.70 0.17 285 /0.7)`;
          ctx.fill();
        }
      });

      // Draw nodes
      nodes.forEach((node) => {
        ctx.beginPath();
        ctx.arc(node.x, node.y, node.size, 0, Math.PI * 2);
        ctx.fillStyle = `oklch(0.70 0.17 285 /${node.opacity})`;
        ctx.fill();

        // Subtle glow
        const grd = ctx.createRadialGradient(node.x, node.y, 0, node.x, node.y, node.size * 4);
        grd.addColorStop(0, `oklch(0.70 0.17 285 /0.12)`);
        grd.addColorStop(1, `oklch(0.70 0.17 285 /0)`);
        ctx.beginPath();
        ctx.arc(node.x, node.y, node.size * 4, 0, Math.PI * 2);
        ctx.fillStyle = grd;
        ctx.fill();

        // Move
        node.x += node.vx;
        node.y += node.vy;
        if (node.x < 0 || node.x > w) node.vx *= -1;
        if (node.y < 0 || node.y > h) node.vy *= -1;
      });

      raf = requestAnimationFrame(draw);
    };

    draw();

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }, [prefersReducedMotion]);

  return (
    <canvas
      ref={canvasRef}
      className="absolute inset-0 w-full h-full pointer-events-none"
      aria-hidden
    />
  );
}
