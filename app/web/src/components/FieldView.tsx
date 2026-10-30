import { useEffect, useRef, useState } from "react";
import type { FieldInfo, Pin, World } from "../lib/types";

const WHEELS = [0, (2 * Math.PI) / 3, (4 * Math.PI) / 3];
const FALLBACK: FieldInfo = {
  width: 9, height: 6, goal_y_min: 2.286, goal_y_max: 3.714, robot_radius: 0.143,
  ball_radius: 0.043, team_blue_size: 3, num_robots: 6, is_default: true,
};
const TRAIL = 40;

interface View {
  s: number; // CSS px per metre
  ox: number;
  oy: number;
  field: FieldInfo;
}

/** Field metres -> canvas CSS pixels; y up in the field, down on screen. */
function viewFor(field: FieldInfo, cw: number, ch: number): View {
  const margin = Math.max(field.width, field.height) * 0.06;
  const s = Math.min(cw / (field.width + 2 * margin), ch / (field.height + 2 * margin));
  return { s, ox: (cw - field.width * s) / 2, oy: (ch - field.height * s) / 2, field };
}

export function FieldView({
  world,
  attackers = [],
  selected = 0,
  pin = null,
  clickable = true,
  onPick,
  onHover,
}: {
  world: World | null;
  attackers?: string[];
  selected?: number;
  pin?: Pin | null;
  clickable?: boolean;
  onPick?: (x: number, y: number) => void;
  onHover?: (p: { x: number; y: number } | null) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const trail = useRef<{ x: number; y: number }[]>([]);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const field = world?.field ?? FALLBACK;
  const view = viewFor(field, size.w, size.h);

  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(box.current!);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const c = canvas.current;
    if (!c || size.w === 0 || size.h === 0) return;
    const dpr = window.devicePixelRatio || 1;
    if (c.width !== Math.round(size.w * dpr) || c.height !== Math.round(size.h * dpr)) {
      c.width = Math.round(size.w * dpr);
      c.height = Math.round(size.h * dpr);
    }
    const g = c.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, size.w, size.h);
    draw(g, view, world, attackers, selected, pin, trail.current);
  });

  const toField = (e: React.MouseEvent): { x: number; y: number } | null => {
    const rect = canvas.current!.getBoundingClientRect();
    const x = (e.clientX - rect.left - view.ox) / view.s;
    const y = field.height - (e.clientY - rect.top - view.oy) / view.s;
    if (x < 0 || y < 0 || x > field.width || y > field.height) return null;
    return { x, y };
  };

  return (
    <div ref={box} style={{ position: "absolute", inset: 0 }}>
      <canvas
        ref={canvas}
        style={{ display: "block", width: size.w, height: size.h, cursor: clickable ? "crosshair" : undefined }}
        onClick={(e) => {
          const p = toField(e);
          if (p && clickable) onPick?.(p.x, p.y);
        }}
        onMouseMove={(e) => onHover?.(toField(e))}
        onMouseLeave={() => onHover?.(null)}
      />
    </div>
  );
}

// ── drawing ──────────────────────────────────────────────────────────────

function draw(
  g: CanvasRenderingContext2D, v: View, w: World | null, attackers: string[],
  selected: number, pin: Pin | null, trail: { x: number; y: number }[],
) {
  const sx = (x: number) => v.ox + x * v.s;
  const sy = (y: number) => v.oy + (v.field.height - y) * v.s;
  drawField(g, v, sx, sy);
  if (!w) return;
  if (pin) drawPin(g, pin, sx, sy);

  const b = w.ball;
  if (b) {
    const last = trail[trail.length - 1];
    if (!last || Math.hypot(last.x - b.x, last.y - b.y) > 0.01) trail.push({ x: b.x, y: b.y });
    if (trail.length > TRAIL) trail.shift();
    for (let i = 1; i < trail.length; i++) {
      g.strokeStyle = `rgba(255, 162, 26, ${(i / trail.length) * 0.45})`;
      g.lineWidth = 2;
      g.beginPath();
      g.moveTo(sx(trail[i - 1].x), sy(trail[i - 1].y));
      g.lineTo(sx(trail[i].x), sy(trail[i].y));
      g.stroke();
    }
  }

  for (const [rid, r] of Object.entries(w.robots ?? {})) {
    drawRobot(g, v, sx(r.x), sy(r.y), Number(rid), r.angle, !!r.stale, attackers.includes(rid),
      Number(rid) === selected, r.tag);
  }

  if (b) {
    const stale = b.stale && b.source === "vision";
    g.fillStyle = stale ? "#8a8a94" : "#ffa21a";
    g.beginPath();
    g.arc(sx(b.x), sy(b.y), Math.max(v.field.ball_radius * v.s, 3.5), 0, Math.PI * 2);
    g.fill();
  }
}

function drawField(g: CanvasRenderingContext2D, v: View, sx: (x: number) => number, sy: (y: number) => number) {
  const f = v.field;
  const x0 = sx(0), y0 = sy(f.height), fw = f.width * v.s, fh = f.height * v.s;
  g.fillStyle = "#0d0d0d";
  g.fillRect(x0, y0, fw, fh);

  // goals, outside the line
  const gy0 = sy(f.goal_y_max), gy1 = sy(f.goal_y_min);
  const depth = Math.max(f.width * 0.025 * v.s, 6);
  goal(g, x0 - depth, gy0, depth, gy1 - gy0, "61,139,255");
  goal(g, x0 + fw, gy0, depth, gy1 - gy0, "255,90,95");

  g.strokeStyle = "rgba(255,255,255,0.75)";
  g.lineWidth = 2;
  g.strokeRect(x0, y0, fw, fh);
  g.strokeStyle = "rgba(255,255,255,0.35)";
  g.lineWidth = 1.5;
  g.beginPath();
  g.moveTo(x0 + fw / 2, y0);
  g.lineTo(x0 + fw / 2, y0 + fh);
  g.stroke();
  g.beginPath();
  g.arc(x0 + fw / 2, y0 + fh / 2, (f.width / 18) * v.s, 0, Math.PI * 2);
  g.stroke();
  g.fillStyle = "rgba(255,255,255,0.5)";
  g.beginPath();
  g.arc(x0 + fw / 2, y0 + fh / 2, 2.5, 0, Math.PI * 2);
  g.fill();
}

function goal(g: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, rgb: string) {
  g.fillStyle = `rgba(${rgb},0.14)`;
  g.fillRect(x, y, w, h);
  g.strokeStyle = `rgb(${rgb})`;
  g.lineWidth = 2;
  g.strokeRect(x, y, w, h);
}

function drawRobot(
  g: CanvasRenderingContext2D, v: View, cx: number, cy: number, id: number, a: number,
  stale: boolean, attacker: boolean, isSel: boolean, tag?: number | null,
) {
  const r = Math.max(v.field.robot_radius * v.s, 7);
  const blue = id < v.field.team_blue_size;
  if (isSel) {
    g.strokeStyle = "#e0261a";
    g.lineWidth = 1.5;
    g.setLineDash([3, 3]);
    g.beginPath();
    g.arc(cx, cy, r + 6, 0, Math.PI * 2);
    g.stroke();
    g.setLineDash([]);
  }
  g.fillStyle = stale ? "#6c6c77" : blue ? "#3d8bff" : "#ff5a5f";
  g.beginPath();
  g.arc(cx, cy, r, 0, Math.PI * 2);
  g.fill();
  g.strokeStyle = attacker ? "#ffd400" : "rgba(0,0,0,0.6)";
  g.lineWidth = attacker ? 2.5 : 1;
  g.stroke();

  // heading: a notch from the rim outwards, clear of the id in the middle
  g.strokeStyle = "white";
  g.lineWidth = 2.5;
  g.lineCap = "round";
  g.beginPath();
  g.moveTo(cx + Math.cos(a) * r * 0.62, cy - Math.sin(a) * r * 0.62);
  g.lineTo(cx + Math.cos(a) * r * 1.45, cy - Math.sin(a) * r * 1.45);
  g.stroke();
  g.fillStyle = "#111";
  for (const wa of WHEELS) {
    g.beginPath();
    g.arc(cx + Math.cos(a + wa) * r * 0.82, cy - Math.sin(a + wa) * r * 0.82, Math.max(r * 0.16, 2), 0, Math.PI * 2);
    g.fill();
  }
  if (r >= 9) {
    g.fillStyle = "white";
    g.font = `700 ${Math.min(r * 0.95, 15)}px "Exo 2 Variable", system-ui, sans-serif`;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(String(id), cx, cy + 0.5);
  }
  if (tag !== undefined && tag !== null) {
    g.font = "600 13px ui-monospace, Consolas, monospace";
    g.fillStyle = "rgba(255,255,255,0.8)";
    g.textAlign = "center";
    g.fillText(`T${tag}`, cx, cy - r - 10);
  }
}

function drawPin(g: CanvasRenderingContext2D, p: Pin, sx: (x: number) => number, sy: (y: number) => number) {
  const px = sx(p.x), py = sy(p.y);
  if (p.fromX !== null && p.fromY !== null) {
    g.strokeStyle = "rgba(224,38,26,0.8)";
    g.lineWidth = 2;
    g.setLineDash([2, 7]);
    g.lineCap = "round";
    g.beginPath();
    g.moveTo(sx(p.fromX), sy(p.fromY));
    g.lineTo(px, py);
    g.stroke();
    g.setLineDash([]);
  }
  g.strokeStyle = "#e0261a";
  g.lineWidth = 2;
  g.beginPath();
  g.arc(px, py, 9, 0, Math.PI * 2);
  g.stroke();
  g.beginPath();
  g.moveTo(px - 14, py); g.lineTo(px - 4, py);
  g.moveTo(px + 4, py); g.lineTo(px + 14, py);
  g.moveTo(px, py - 14); g.lineTo(px, py - 4);
  g.moveTo(px, py + 4); g.lineTo(px, py + 14);
  g.stroke();
}
