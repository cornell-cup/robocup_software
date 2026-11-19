import { useEffect, useRef, useState } from "react";
import type { MotionTestInfo, Pose } from "../lib/types";

/** The field from above, for the Robot test page: where each run went against
 *  where it should have. Field metres, y up; the canvas keeps the field's shape. */

export interface PathLine {
  points: [number, number][];
  /** the run in progress: drawn bright, the rest faded */
  live?: boolean;
}

const FIELD = "#0c1a10";
const EDGE = "#4a5a4e";
const KEEP_OUT = "rgba(224, 38, 26, 0.10)";
const IDEAL = "#a3a3a3";
const PATH = "#3d8bff";
const PATH_OLD = "rgba(61, 139, 255, 0.6)";
const HOME = "#f4f4f4";
const ROBOT = "#e0261a";
/** The runner keeps the robot's centre this far inside the edges (KEEP_OUT_M). */
const KEEP_OUT_M = 0.15;

export function PathView({
  field, home, start, test, lines,
}: {
  field: { width: number; height: number; x0?: number; y0?: number } | null;
  home: Pose | null;
  /** the run in progress: where it set off from, and what it was told to do */
  start: Pose | null;
  test: MotionTestInfo | null;
  lines: PathLine[];
}) {
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });

  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(box.current!);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const c = canvas.current;
    if (!c || size.w === 0 || size.h === 0) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.round(size.w * dpr);
    c.height = Math.round(size.h * dpr);
    const g = c.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, size.w, size.h);
    if (!field) return;

    const x0 = field.x0 ?? 0, y0 = field.y0 ?? 0;
    const pad = 24;
    const s = Math.min((size.w - 2 * pad) / field.width, (size.h - 2 * pad) / field.height);
    const ox = (size.w - field.width * s) / 2, oy = (size.h - field.height * s) / 2;
    const sx = (x: number) => ox + (x - x0) * s;
    const sy = (y: number) => oy + (field.height - (y - y0)) * s;

    // the field, and the band the robot is kept out of
    g.fillStyle = KEEP_OUT;
    g.fillRect(ox, oy, field.width * s, field.height * s);
    g.fillStyle = FIELD;
    const k = KEEP_OUT_M * s;
    g.fillRect(ox + k, oy + k, field.width * s - 2 * k, field.height * s - 2 * k);
    g.strokeStyle = EDGE;
    g.lineWidth = 2;
    g.strokeRect(ox, oy, field.width * s, field.height * s);

    if (home) marker(g, sx(home[0]), sy(home[1]), home[2], HOME, 10);

    // what the run in progress should do
    if (start && test) {
      g.strokeStyle = IDEAL;
      g.lineWidth = 1.5;
      g.setLineDash([6, 6]);
      g.beginPath();
      if (test.spin) {
        g.arc(sx(start[0]), sy(start[1]), 16, 0, Math.PI * 2);
      } else {
        const a = ((start[2] + (test.direction_deg ?? 0)) * Math.PI) / 180;
        g.moveTo(sx(start[0]), sy(start[1]));
        g.lineTo(sx(start[0] + test.distance_m * Math.cos(a)), sy(start[1] + test.distance_m * Math.sin(a)));
      }
      g.stroke();
      g.setLineDash([]);
    }

    // where they went: finished runs faded under the live one
    for (const line of [...lines].sort((a, b) => Number(!!a.live) - Number(!!b.live))) {
      if (line.points.length < 2) continue;
      g.strokeStyle = line.live ? PATH : PATH_OLD;
      g.lineWidth = line.live ? 3 : 2;
      g.beginPath();
      line.points.forEach(([x, y], i) => (i ? g.lineTo(sx(x), sy(y)) : g.moveTo(sx(x), sy(y))));
      g.stroke();
      if (line.live) {
        const [x, y] = line.points[line.points.length - 1];
        g.fillStyle = ROBOT;
        g.beginPath();
        g.arc(sx(x), sy(y), 6, 0, Math.PI * 2);
        g.fill();
      }
    }

    // scale: a 10 cm bar
    g.fillStyle = IDEAL;
    g.fillRect(ox, oy + field.height * s + 8, 0.1 * s, 2);
    g.font = "12px ui-monospace, monospace";
    g.fillText("10 cm", ox + 0.1 * s + 6, oy + field.height * s + 14);
  });

  return (
    <div ref={box} className="path-view">
      <canvas ref={canvas} style={{ width: size.w, height: size.h }} />
    </div>
  );
}

/** A ring with a tick for its heading. */
function marker(g: CanvasRenderingContext2D, x: number, y: number, deg: number, color: string, r: number) {
  const a = (deg * Math.PI) / 180;
  g.strokeStyle = color;
  g.lineWidth = 2;
  g.beginPath();
  g.arc(x, y, r, 0, Math.PI * 2);
  g.stroke();
  g.beginPath();
  g.moveTo(x, y);
  g.lineTo(x + Math.cos(a) * r * 1.8, y - Math.sin(a) * r * 1.8);
  g.stroke();
}
