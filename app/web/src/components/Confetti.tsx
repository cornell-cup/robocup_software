import { useEffect, useRef } from "react";

/** The pygame window's goal confetti: a burst from the middle of the field in
 *  the team's colours, falling under gravity. A new `burst.id` fires one. */
const PALETTES: Record<string, string[]> = {
  blue: ["#3d8bff", "#82cdff", "#ffffff"],
  red: ["#ff5a5f", "#ffaa78", "#ffffff"],
};
const COUNT = 220;
const GRAVITY = 900; // CSS px/s²

interface Bit {
  x: number; y: number; vx: number; vy: number;
  spin: number; turn: number; w: number; h: number;
  life: number; max: number; color: string;
}

export function Confetti({ burst }: { burst: { id: number; team: string } | null }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const bits = useRef<Bit[]>([]);
  const frame = useRef(0);

  useEffect(() => {
    const c = canvas.current;
    if (!burst || !c) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const { width: w, height: h } = c.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.round(w * dpr);
    c.height = Math.round(h * dpr);
    const palette = PALETTES[burst.team] ?? PALETTES.blue;
    const reach = Math.max(w, h); // a bigger stage throws them further
    for (let i = 0; i < COUNT; i++) {
      const a = Math.random() * Math.PI * 2;
      const speed = reach * (0.25 + Math.random() * 0.6);
      const max = 1.4 + Math.random() * 1.2;
      bits.current.push({
        x: w / 2, y: h / 2,
        vx: Math.cos(a) * speed, vy: Math.sin(a) * speed - reach * 0.35, // upward first
        spin: Math.random() * Math.PI, turn: (Math.random() - 0.5) * 14,
        w: 5 + Math.random() * 6, h: 3 + Math.random() * 4,
        life: max, max, color: palette[i % palette.length],
      });
    }
    if (frame.current) return; // already animating: the new bits join in
    let last = performance.now();
    const tick = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000);
      last = now;
      const g = c.getContext("2d")!;
      g.setTransform(dpr, 0, 0, dpr, 0, 0);
      g.clearRect(0, 0, w, h);
      bits.current = bits.current.filter((b) => (b.life -= dt) > 0);
      for (const b of bits.current) {
        b.vx *= 1 - 0.8 * dt; // air drag, so they flutter down rather than fly off
        b.vy = b.vy * (1 - 0.8 * dt) + GRAVITY * dt;
        b.x += b.vx * dt;
        b.y += b.vy * dt;
        b.spin += b.turn * dt;
        g.save();
        g.globalAlpha = Math.min(1, b.life / (b.max * 0.4));
        g.translate(b.x, b.y);
        g.rotate(b.spin);
        g.fillStyle = b.color;
        g.fillRect(-b.w / 2, -b.h / 2, b.w, b.h * Math.abs(Math.cos(b.spin * 1.7)) + 1);
        g.restore();
      }
      frame.current = bits.current.length ? requestAnimationFrame(tick) : 0;
    };
    frame.current = requestAnimationFrame(tick);
  }, [burst?.id]);

  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  return <canvas ref={canvas} className="confetti" aria-hidden="true" />;
}
