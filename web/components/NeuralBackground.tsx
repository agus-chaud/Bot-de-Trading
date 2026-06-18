"use client";

import { useEffect, useRef } from "react";

export function NeuralBackground() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const el = canvas;
    const context = el.getContext("2d");
    if (!context) return;
    const ctx: CanvasRenderingContext2D = context;
    if (!ctx) return;

    let w = 0;
    let h = 0;
    let nodes: { x: number; y: number; vx: number; vy: number; r: number }[] = [];
    const NODE_COUNT = 48;
    const LINK_DIST = 140;
    let frameId = 0;

    function resize() {
      w = el.width = window.innerWidth;
      h = el.height = window.innerHeight;
    }

    function initNodes() {
      nodes = [];
      for (let i = 0; i < NODE_COUNT; i++) {
        nodes.push({
          x: Math.random() * w,
          y: Math.random() * h,
          vx: (Math.random() - 0.5) * 0.35,
          vy: (Math.random() - 0.5) * 0.35,
          r: 1.2 + Math.random() * 1.5,
        });
      }
    }

    function tick() {
      ctx.clearRect(0, 0, w, h);

      for (const n of nodes) {
        n.x += n.vx;
        n.y += n.vy;
        if (n.x < 0 || n.x > w) n.vx *= -1;
        if (n.y < 0 || n.y > h) n.vy *= -1;
      }

      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i];
          const b = nodes[j];
          const dist = Math.hypot(a.x - b.x, a.y - b.y);
          if (dist < LINK_DIST) {
            const alpha = (1 - dist / LINK_DIST) * 0.22;
            ctx.strokeStyle = `rgba(118, 185, 0, ${alpha})`;
            ctx.lineWidth = 0.6;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
          }
        }
      }

      for (const n of nodes) {
        const g = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, n.r * 4);
        g.addColorStop(0, "rgba(110, 250, 95, 0.55)");
        g.addColorStop(1, "rgba(118, 185, 0, 0)");
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r * 3, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = "rgba(118, 185, 0, 0.85)";
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx.fill();
      }

      frameId = requestAnimationFrame(tick);
    }

    const onResize = () => {
      resize();
      initNodes();
    };

    window.addEventListener("resize", onResize);
    resize();
    initNodes();
    tick();

    return () => {
      window.removeEventListener("resize", onResize);
      cancelAnimationFrame(frameId);
    };
  }, []);

  return <canvas ref={canvasRef} className="neural-canvas" aria-hidden="true" />;
}
