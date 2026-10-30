import { useRef, useState } from "react";
import { Card } from "../components/Card";
import { Segmented } from "../components/Segmented";
import { api } from "../lib/api";
import { usePolling } from "../lib/store";
import { toasts } from "../lib/toast";
import type { BallStatus, SceneHues } from "../lib/types";
import { useSavedState } from "../lib/useSavedState";
import "../styles/ball.css";

type View = "camera" | "overlay" | "mask";
type Box = { x0: number; y0: number; x1: number; y1: number };

const HUE_NAMES: [number, string][] = [
  [0, "red"], [10, "orange"], [20, "yellow"], [35, "green"], [75, "cyan"],
  [100, "blue"], [130, "violet"], [150, "magenta"], [170, "red"],
];
const hueName = (h: number) => HUE_NAMES.reduce((n, [lo, name]) => (h >= lo ? name : n), "red");
const hueCss = (h: number) => `hsl(${h * 2}, 85%, 52%)`; // OpenCV hue is 0-179

export function Ball() {
  const [st, setSt] = useState<BallStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<View>("overlay");
  const [streamKey, setStreamKey] = useState(0);
  const [drag, setDrag] = useState<Box | null>(null);
  const [hues, setHues] = useState<SceneHues | null>(null);
  const [form, setForm] = useSavedState("ball.form", { streamUrl: "", newName: "", newRadius: 20 });
  const [newNote, setNewNote] = useState("");
  const svg = useRef<SVGSVGElement>(null);

  const connected = st?.state === "connected";
  const p = st?.profile;
  const size = st?.stream?.size ?? [1280, 720];
  const hasProfile = !!p?.name;

  const wasConnected = useRef(false);
  usePolling(async () => {
    try {
      const s = await api.get<BallStatus>("/ball/status");
      if (s.state === "connected" && !wasConnected.current) setStreamKey((k) => k + 1);
      wasConnected.current = s.state === "connected";
      setSt(s);
    } catch {
      /* server restarting */
    }
  }, 400);

  async function run(path: string, body: unknown = {}, ok?: string) {
    setBusy(true);
    try {
      const s = await api.post<BallStatus>(path, body);
      setSt(s);
      if (ok) toasts.push("ok", ok);
      return s;
    } catch (e) {
      toasts.error(e);
    } finally {
      setBusy(false);
    }
  }

  // Sliders send as they move, but at most every 120 ms.
  const pending = useRef<Record<string, number>>({});
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  function tune(key: string, value: number) {
    pending.current[key] = value;
    if (st) setSt({ ...st, profile: { ...st.profile, [key]: value } }); // move the slider now
    if (timer.current) return;
    timer.current = setTimeout(async () => {
      const body = pending.current;
      pending.current = {};
      timer.current = null;
      try {
        setSt(await api.post<BallStatus>("/ball/tune", body));
      } catch (e) {
        toasts.error(e);
      }
    }, 120);
  }

  async function save() {
    const s = await run("/ball/save");
    if (s?.saved_to) {
      toasts.push("ok", `Saved "${s.profile.name}" to ${s.saved_to}.\nUse it with --ball-profile ${s.profile.name}.`, 8000);
    }
  }

  async function measureHues() {
    try {
      setHues(await api.get<SceneHues>("/ball/scene-hues"));
    } catch (e) {
      toasts.error(e);
    }
  }

  // ── dragging a box over the ball ──────────────────────────────────────

  function toImage(e: React.PointerEvent) {
    const pt = svg.current!.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const q = pt.matrixTransform(svg.current!.getScreenCTM()!.inverse());
    return { x: Math.max(0, Math.min(size[0], q.x)), y: Math.max(0, Math.min(size[1], q.y)) };
  }
  function down(e: React.PointerEvent) {
    if (!connected || !hasProfile) return;
    const q = toImage(e);
    setDrag({ x0: q.x, y0: q.y, x1: q.x, y1: q.y });
    svg.current!.setPointerCapture(e.pointerId);
  }
  function move(e: React.PointerEvent) {
    if (!drag) return;
    const q = toImage(e);
    setDrag({ ...drag, x1: q.x, y1: q.y });
  }
  async function up(e: React.PointerEvent) {
    if (!drag) return;
    const box = [drag.x0, drag.y0, drag.x1, drag.y1];
    setDrag(null);
    if (Math.abs(box[2] - box[0]) < 4 || Math.abs(box[3] - box[1]) < 4) return;
    const s = await run("/ball/sample", { box, add: e.shiftKey });
    if (s && view === "camera") setView("overlay");
  }

  const top = hues ? Math.max(...hues.bins.map((b) => b.share), 1e-6) : 1;
  const emptiest = hues ? [...hues.bins].sort((a, b) => a.share - b.share).slice(0, 4).sort((a, b) => a.h0 - b.h0) : [];

  return (
    <div className="page">
      <section className="main-panel">
        <div className="viewer-head">
          {connected && st?.stream ? (
            <>
              <span className="info"><span className="dot ok" />Camera {st.stream.size[0]}×{st.stream.size[1]}</span>
              {p?.sampled && (
                <>
                  <div className="views">
                    <Segmented
                      options={[
                        { value: "overlay", label: "Detected" },
                        { value: "mask", label: "Likelihood" },
                        { value: "camera", label: "Camera" },
                      ]}
                      value={view}
                      onChange={setView}
                    />
                  </div>
                  {st.stats.mask_pct !== undefined && (
                    <span className={`info${st.stats.looks_right ? "" : " warn-text"}`}>
                      {st.stats.blobs} {st.stats.blobs === 1 ? "blob" : "blobs"}, {st.stats.mask_pct}% of the picture
                    </span>
                  )}
                </>
              )}
            </>
          ) : (
            <span className="info">
              <span className={`dot${st?.state === "connecting" ? " warn pulse" : ""}`} />
              {st?.state === "connecting" ? "Connecting to the camera…" : "Not connected"}
            </span>
          )}
        </div>

        <div className={`picture${connected && hasProfile ? " pickable" : ""}`}>
          {connected ? (
            <>
              <img key={`${streamKey}-${view}`} src={`/api/v1/ball/stream.mjpg?view=${view}&k=${streamKey}`} alt="Camera" />
              <svg
                className="overlay"
                ref={svg}
                viewBox={`0 0 ${size[0]} ${size[1]}`}
                preserveAspectRatio="xMidYMid meet"
                onPointerDown={down}
                onPointerMove={move}
                onPointerUp={up}
              >
                {drag && (
                  <rect
                    x={Math.min(drag.x0, drag.x1)}
                    y={Math.min(drag.y0, drag.y1)}
                    width={Math.abs(drag.x1 - drag.x0)}
                    height={Math.abs(drag.y1 - drag.y0)}
                    className="box"
                  />
                )}
              </svg>
              <p className="pic-hint">
                {!hasProfile ? "Create or pick a profile to start sampling."
                  : !p?.sampled ? "Drag a box over the ball."
                  : "Drag over the ball to sample again. Shift-drag adds to the sample."}
              </p>
            </>
          ) : (
            <div className="empty">
              <h3>Connect to the camera</h3>
              <p className="muted">The ball is sampled from PhotonVision's camera stream, the same picture the tracker looks for it in.</p>
            </div>
          )}
        </div>
      </section>

      <aside className="side">
        <Card
          title="Camera"
          step={1}
          active={!connected}
          done={connected}
          aside={connected && <button className="btn sm ghost" disabled={busy} onClick={() => run("/ball/disconnect")}>Disconnect</button>}
        >
          {connected && st?.stream ? (
            <dl className="kv">
              <dt>Stream</dt><dd>{st.stream.url}</dd>
              <dt>Size</dt><dd>{st.stream.size[0]} × {st.stream.size[1]}</dd>
            </dl>
          ) : (
            <div className="stack">
              <label className="field">
                <span>Stream URL</span>
                <input className="input mono" placeholder="http://127.0.0.1:1181/stream.mjpg" value={form.streamUrl} onChange={(e) => setForm({ ...form, streamUrl: e.target.value })} />
                <small>PhotonVision's camera stream. Leave empty for PhotonVision on this machine.</small>
              </label>
              <button className="btn primary block" disabled={busy || st?.state === "connecting"} onClick={() => run("/ball/connect", { stream: form.streamUrl.trim() || null })}>
                {st?.state === "connecting" ? "Connecting…" : "Connect"}
              </button>
              {st?.state === "error" && st.error && <p className="note error">{st.error}</p>}
            </div>
          )}
        </Card>

        <Card title="Profile" step={2} active={connected && !p?.sampled} done={!!p?.sampled}>
          {hasProfile && p ? (
            <div className="stack">
              <div className="row">
                <label className="field">
                  <span>Name</span>
                  <input className="input" key={`n-${p.name}`} defaultValue={p.name} onBlur={(e) => e.target.value !== p.name && run("/ball/tune", { name: e.target.value })} />
                </label>
                <label className="field">
                  <span>Radius (mm)</span>
                  <input className="input mono" type="number" step="0.5" min="1" key={`r-${p.radius_mm}`} defaultValue={p.radius_mm} onBlur={(e) => Number(e.target.value) !== p.radius_mm && run("/ball/tune", { radius_mm: Number(e.target.value) })} />
                </label>
              </div>
              <label className="field">
                <span>Note</span>
                <input className="input" key={`t-${p.note}`} defaultValue={p.note} placeholder="e.g. orange golf ball, lab lights" onBlur={(e) => e.target.value !== p.note && run("/ball/tune", { note: e.target.value })} />
              </label>
              {p.sampled && p.hue !== null ? (
                <div className="sample">
                  <span className="swatch" style={{ background: hueCss(p.hue) }} />
                  <span>Hue {p.hue} ({hueName(p.hue)})</span>
                  <span className="muted">{p.sample_px ? `${p.sample_px} px sampled` : "loaded from file"}</span>
                  <button className="btn sm ghost" disabled={busy} onClick={() => run("/ball/clear")}>Clear</button>
                </div>
              ) : (
                <p className="note">Not sampled yet. Drag a box over the ball in the picture.</p>
              )}
            </div>
          ) : (
            <div className="stack">
              <div className="row">
                <label className="field"><span>New profile name</span><input className="input" placeholder="e.g. match" value={form.newName} onChange={(e) => setForm({ ...form, newName: e.target.value })} /></label>
                <label className="field"><span>Radius (mm)</span><input className="input mono" type="number" step="0.5" min="1" value={form.newRadius} onChange={(e) => setForm({ ...form, newRadius: Number(e.target.value) })} /></label>
              </div>
              <label className="field"><span>Note</span><input className="input" placeholder="optional" value={newNote} onChange={(e) => setNewNote(e.target.value)} /></label>
              <button className={`btn block${connected ? " primary" : ""}`} disabled={busy || !form.newName.trim()} onClick={() => run("/ball/new", { name: form.newName.trim(), radius_mm: form.newRadius, note: newNote })}>
                Create profile
              </button>
              <small className="dim">Or edit a saved one from the list below.</small>
            </div>
          )}
        </Card>

        <Card title="Tune and save" step={3} active={!!p?.sampled}>
          {p?.sampled ? (
            <div className="stack">
              <label className="slider">
                <span>Threshold <b className="mono">{p.threshold}</b></span>
                <input type="range" min={1} max={254} value={p.threshold} onChange={(e) => tune("threshold", Number(e.target.value))} />
                <small>Lower finds more of the ball, and more of the room.</small>
              </label>
              <label className="slider">
                <span>Saturation floor <b className="mono">{p.s_min}</b></span>
                <input type="range" min={0} max={254} step={5} value={p.s_min} onChange={(e) => tune("s_min", Number(e.target.value))} />
                <small>Pixels greyer than this never count as ball.</small>
              </label>
              <label className="slider">
                <span>Hue spread <b className="mono">{(p.h_spread * 6).toFixed(0)}°</b></span>
                <input type="range" min={0} max={15} step={0.5} value={p.h_spread} onChange={(e) => tune("h_spread", Number(e.target.value))} />
              </label>
              <label className="slider">
                <span>Saturation spread <b className="mono">{(p.s_spread * 8).toFixed(0)}</b></span>
                <input type="range" min={0} max={32} step={1} value={p.s_spread} onChange={(e) => tune("s_spread", Number(e.target.value))} />
                <small>Raise it if the ball drops out in shade or under other lights.</small>
              </label>
              <p className="note">
                Aim for one blob on the ball and nothing else. Then roll the ball into shadow and across the field, and
                check it stays detected before saving.
              </p>
              <button className="btn primary block" disabled={busy} onClick={save}>Save profile</button>
            </div>
          ) : (
            <p className="note">Sample the ball first.</p>
          )}
        </Card>

        <Card title="Colours in the scene" aside={<button className="btn sm ghost" disabled={!connected} onClick={measureHues}>{hues ? "Measure again" : "Measure"}</button>}>
          {hues ? (
            <>
              <svg className="hues" viewBox="0 0 300 80" preserveAspectRatio="none" role="img" aria-label="Hues in the picture">
                {hues.bins.map((b, i) => {
                  const h = (b.share / top) * 70;
                  return <rect key={i} x={i * 10 + 1} y={74 - h} width={8} height={Math.max(h, 0.5)} fill={hueCss(b.h0 + 3)} />;
                })}
                {hues.ball_hue !== null && (
                  <line x1={(hues.ball_hue / 6) * 10 + 5} x2={(hues.ball_hue / 6) * 10 + 5} y1={0} y2={80} className="ball-mark" />
                )}
              </svg>
              <p className="note">
                {hues.ball_hue !== null && "The white line is your ball's hue. "}A ball colour works best where the room has
                little of it. Emptiest here: {emptiest.map((b) => `${hueName(b.h0)} (H ${b.h0})`).join(", ")}.
              </p>
            </>
          ) : (
            <p className="note">See which hues the room already has, to judge how well the ball's colour stands out.</p>
          )}
        </Card>

        <Card title="Saved profiles">
          {st?.saved.profiles.length ? (
            <table className="plain">
              <thead><tr><th /><th>Name</th><th>Radius</th><th>Saved</th><th /></tr></thead>
              <tbody>
                {st.saved.profiles.map((sp) => (
                  <tr key={sp.name} className={p?.name === sp.name ? "current" : undefined}>
                    <td><span className="swatch" style={{ background: hueCss(sp.hue) }} title={`Hue ${sp.hue}`} /></td>
                    <td>{sp.name}{sp.note && <div className="dim note-line">{sp.note}</div>}</td>
                    <td className="mono">{sp.radius_mm} mm</td>
                    <td className="mono dim">{sp.created || "–"}</td>
                    <td><button className="btn sm ghost" disabled={busy} onClick={() => run("/ball/load", { name: sp.name })}>Edit</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="note">None yet.</p>
          )}
          {st?.saved.path && <p className="src">{st.saved.error ?? st.saved.path}</p>}
        </Card>
      </aside>
    </div>
  );
}
