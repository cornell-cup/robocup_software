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
      g.strokeStyle = `rgba(230, 120, 0, ${(i / trail.length) * 0.5})`;
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
    g.fillStyle = stale ? STALE : BALL;
    g.beginPath();
    g.arc(sx(b.x), sy(b.y), Math.max(v.field.ball_radius * v.s, 5), 0, Math.PI * 2);
    g.fill();
  }
}

// Drawn as the pygame window (sim/viz_node.py) drew it, scaled from its
// 900 px-wide field: turf green, white border and halfway line, centre circle,
// goal posts on the side lines, robots with red wheel dots.
const FIELD_GREEN = "rgb(0,100,0)";
const LINE = "rgb(255,255,255)";
const POST_LEFT = "rgb(0,255,255)";
const POST_RIGHT = "rgb(255,110,110)";
const ROBOT_BLUE = "rgb(30,144,255)";
const ROBOT_RED = "rgb(255,80,80)";
const STALE = "rgb(130,130,130)";
const WHEEL = "rgb(200,0,0)";
const ATTACKER = "rgb(255,215,0)";
const BALL = "rgb(230,120,0)";

/** pygame pixels -> canvas pixels: its field was always 900 px wide. */
const px = (v: View, n: number) => Math.max(1, (n * v.field.width * v.s) / 900);

function drawField(g: CanvasRenderingContext2D, v: View, sx: (x: number) => number, sy: (y: number) => number) {
  const f = v.field;
  const x0 = sx(0), y0 = sy(f.height), fw = f.width * v.s, fh = f.height * v.s;
  // pygame filled the whole window green; here the field and a strip around it
  const m = Math.max(f.width, f.height) * 0.045 * v.s;
  g.fillStyle = FIELD_GREEN;
  g.fillRect(x0 - m, y0 - m, fw + 2 * m, fh + 2 * m);

  g.strokeStyle = LINE;
  g.lineWidth = px(v, 3);
  g.strokeRect(x0, y0, fw, fh);
  g.lineWidth = px(v, 2);
  g.beginPath();
  g.moveTo(x0 + fw / 2, y0);
  g.lineTo(x0 + fw / 2, y0 + fh);
  g.stroke();
  g.beginPath();
  g.arc(x0 + fw / 2, y0 + fh / 2, fw / 18, 0, Math.PI * 2);
  g.stroke();

  // goal posts on the side lines; the left one has a white underlay to stand
  // out against the grass
  const gy0 = sy(f.goal_y_max), gy1 = sy(f.goal_y_min);
  post(g, x0, gy0, gy1, LINE, px(v, 8));
  post(g, x0, gy0, gy1, POST_LEFT, px(v, 5));
  post(g, x0 + fw, gy0, gy1, POST_RIGHT, px(v, 6));
}

function post(g: CanvasRenderingContext2D, x: number, y0: number, y1: number, color: string, width: number) {
  g.strokeStyle = color;
  g.lineWidth = width;
  g.lineCap = "butt";
  g.beginPath();
  g.moveTo(x, y0);
  g.lineTo(x, y1);
  g.stroke();
}

function drawRobot(
  g: CanvasRenderingContext2D, v: View, cx: number, cy: number, id: number, a: number,
  stale: boolean, attacker: boolean, isSel: boolean, tag?: number | null,
) {
  const r = Math.max(v.field.robot_radius * v.s, 4);
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
  g.fillStyle = stale ? STALE : blue ? ROBOT_BLUE : ROBOT_RED;
  g.beginPath();
  g.arc(cx, cy, r, 0, Math.PI * 2);
  g.fill();
  const outline = attacker ? ATTACKER : LINE;
  g.strokeStyle = outline;
  g.lineWidth = attacker ? 3 : 2;
  g.stroke();

  // heading, in the outline colour. pygame drew it from the centre; it starts
  // a little out here so the robot's number stays readable.
  g.lineWidth = 3;
  g.lineCap = "round";
  g.beginPath();
  g.moveTo(cx + Math.cos(a) * r * 0.5, cy - Math.sin(a) * r * 0.5);
  g.lineTo(cx + Math.cos(a) * r * 1.4, cy - Math.sin(a) * r * 1.4);
  g.stroke();

  g.fillStyle = WHEEL;
  for (const wa of WHEELS) {
    g.beginPath();
    g.arc(cx + Math.cos(a + wa) * r, cy - Math.sin(a + wa) * r, Math.max(r * 0.28, 2.5), 0, Math.PI * 2);
    g.fill();
  }
  if (r >= 9) {
    g.fillStyle = "white";
    g.font = `700 ${Math.min(r * 0.9, 15)}px "Exo 2 Variable", system-ui, sans-serif`;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(String(id), cx, cy + 0.5);
  }
  if (tag !== undefined && tag !== null) {
    g.font = "600 13px ui-monospace, Consolas, monospace";
    g.fillStyle = LINE;
    g.textAlign = "center";
    g.textBaseline = "alphabetic";
    g.fillText(`T${tag}`, cx, cy - r - 8);
  }
}

/** The pygame target: a dotted path from where the robot was, and a map pin. */
function drawPin(g: CanvasRenderingContext2D, p: Pin, sx: (x: number) => number, sy: (y: number) => number) {
  const tx = sx(p.x), ty = sy(p.y);
  g.fillStyle = p.color;
  if (p.fromX !== null && p.fromY !== null) {
    const fx = sx(p.fromX), fy = sy(p.fromY);
    const steps = Math.max(1, Math.floor(Math.hypot(tx - fx, ty - fy) / 12));
    for (let i = 0; i <= steps; i++) {
      g.beginPath();
      g.arc(fx + ((tx - fx) * i) / steps, fy + ((ty - fy) * i) / steps, 2, 0, Math.PI * 2);
      g.fill();
    }
  }
  g.strokeStyle = p.color;
  g.lineWidth = 2;
  g.beginPath();
  g.moveTo(tx, ty);
  g.lineTo(tx, ty - 15);
  g.stroke();
  g.beginPath();
  g.arc(tx, ty - 22, 7, 0, Math.PI * 2);
  g.fill();
  g.strokeStyle = "white";
  g.lineWidth = 1;
  g.stroke();
}
