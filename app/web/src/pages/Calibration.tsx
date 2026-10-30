import { useEffect, useRef, useState } from "react";
import { Card } from "../components/Card";
import { Toggle } from "../components/Toggle";
import { api } from "../lib/api";
import { photonvision, usePhotonVision } from "../lib/photonvision";
import { usePolling } from "../lib/store";
import { toasts } from "../lib/toast";
import type { CalibStatus } from "../lib/types";
import { useSavedState } from "../lib/useSavedState";
import "../styles/calibration.css";

type Row = { id: number; x: number; y: number };

const pts = (p: number[][]) => p.map((q) => q.join(",")).join(" ");
const fmtTime = (t?: number) => (t ? new Date(t * 1000).toLocaleString() : "–");
function quality(r: NonNullable<CalibStatus["result"]>) {
  const { rotation_spread_deg: deg, translation_spread_mm: mm, fit_mm: fit } = r;
  if (fit) {
    // About what placing a tag by hand and a tape-measured field carry.
    const worst = Math.max(...fit.map((f) => f ?? Infinity));
    return worst < 15 ? "good" : worst < 30 ? "fair" : "poor";
  }
  if (deg === null || mm === null) return "";
  return deg < 0.3 && mm < 3 ? "good" : deg < 1 && mm < 10 ? "fair" : "poor";
}

/** What CornerSampler.add() reported, said for the page. */
const CORNER_MSG: Record<string, string> = {
  no_tag: "no tag visible",
  several: "several tags visible: show exactly one",
  at_previous: "still at the previous corner: move the tag",
  moving: "tag moving: set it down and let go",
  capturing: "hold steady, capturing…",
  ready: "got it: confirm and move on",
};

export function Calibration() {
  const [st, setSt] = useState<CalibStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [streamKey, setStreamKey] = useState(0);
  const [advanced, setAdvanced] = useState(false);
  const [originX, setOriginX] = useState("");
  const [originY, setOriginY] = useState("");
  const svg = useRef<SVGSVGElement>(null);
  const pv = usePhotonVision();
  const pvs = pv.status;
  const installing = pvs?.install.state === "starting" || pvs?.install.state === "working";

  // form, remembered between visits
  const [form, setForm] = useSavedState("calib.form", {
    camera: "", ntServer: "", streamUrl: "", resW: "", resH: "",
    tagSize: "", fieldW: "", fieldH: "", minFrames: 10,
    customLayout: false, layoutRows: [] as Row[],
    oneTag: false, perCorner: 30,
  });
  const set = <K extends keyof typeof form>(k: K, v: (typeof form)[K]) => setForm((f) => ({ ...f, [k]: v }));

  const connected = st?.state === "connected";
  const size = st?.camera?.size ?? [1280, 720];
  const fw = Number(form.fieldW), fh = Number(form.fieldH);
  const defaultLayout: Row[] = fw > 0 && fh > 0
    ? [{ id: 0, x: 0, y: 0 }, { id: 1, x: fw, y: 0 }, { id: 2, x: fw, y: fh }, { id: 3, x: 0, y: fh }]
    : [];
  const layout = form.customLayout && form.layoutRows.length ? form.layoutRows : defaultLayout;
  const seen = new Set((st?.detections ?? []).map((d) => d.id));
  const refIds = st?.setup ? Object.keys(st.setup.layout).map(Number) : layout.map((r) => r.id);
  const isRef = (id: number) => refIds.includes(id);
  const allVisible = layout.length > 0 && layout.every((r) => seen.has(r.id));
  const minNeeded = st?.setup?.min_frames ?? form.minFrames;
  const maxFrames = st?.setup?.max_frames ?? 40;
  // One tag at a time, once a capture of that kind has started.
  const corners = st?.setup?.sequential ? st.corners : undefined;
  const cornersDone = !!corners && corners.index >= corners.total;
  const canSolve = !!st && (corners ? cornersDone : !st.setup?.sequential && st.captured >= minNeeded);
  const result = st?.result;
  const step = !connected ? 1 : result ? 3 : 2;
  const fs = Math.max(size[0], size[1]) / 55; // label size, image px

  const wasConnected = useRef(false);
  usePolling(async () => {
    try {
      const s = await api.get<CalibStatus>("/calib/status");
      if (s.state === "connected" && !wasConnected.current) setStreamKey((k) => k + 1);
      wasConnected.current = s.state === "connected";
      setForm((f) => (!f.fieldW && s.saved.field ? { ...f, fieldW: String(s.saved.field[0]), fieldH: String(s.saved.field[1]) } : f));
      setSt(s);
    } catch {
      /* server restarting */
    }
  }, 300);

  async function run(path: string, body: unknown = {}, ok?: string) {
    setBusy(true);
    try {
      const s = await api.post<CalibStatus>(path, body);
      setSt(s);
      if (ok) toasts.push("ok", ok);
      return s;
    } catch (e) {
      toasts.error(e);
    } finally {
      setBusy(false);
    }
  }

  const connect = () =>
    run("/calib/connect", {
      camera: form.camera.trim() || null,
      nt_server: form.ntServer.trim() || null,
      stream: form.streamUrl.trim() || null,
      res: Number(form.resW) && Number(form.resH) ? [Number(form.resW), Number(form.resH)] : null,
    });

  // Started from here: connect by itself once PhotonVision answers.
  const [autoConnect, setAutoConnect] = useState(false);
  useEffect(() => {
    if (autoConnect && pvs?.up && st?.state === "disconnected") {
      setAutoConnect(false);
      connect();
    }
    if (autoConnect && pvs?.state === "stopped" && !pv.busy) setAutoConnect(false);
  }, [autoConnect, pvs, st?.state, pv.busy]);

  function startCapture() {
    const tag = Number(form.tagSize);
    if (!(tag > 0)) return toasts.push("error", "Enter the tag size first (black square edge, metres).");
    if (!(fw > 0 && fh > 0)) return toasts.push("error", "Enter the field size first.");
    run("/calib/capture/start", {
      tag_size: tag,
      field_w: fw,
      field_h: fh,
      min_frames: form.minFrames,
      sequential: form.oneTag,
      frames_per_corner: form.perCorner,
      layout: Object.fromEntries(layout.map((r) => [String(r.id), [r.x, r.y]])),
    });
  }

  const corner = (action: "confirm" | "retry" | "back") => run("/calib/corner", { action });

  // One tag at a time: q confirms and r retries, as in calibrate-field's window.
  useEffect(() => {
    if (!corners || !st?.capturing) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA" || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "q" && corners.ready) corner("confirm");
      else if (e.key === "r") corner("retry");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [corners?.ready, st?.capturing, !!corners]);

  async function save() {
    const s = await run("/calib/save", {});
    if (s?.saved_to) toasts.push("ok", `Saved to ${s.saved_to}\nTrackers pick it up the next time they start.`, 7000);
  }

  function clickPicture(e: React.MouseEvent) {
    if (!result || !svg.current || !connected) return;
    const pt = svg.current.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const p = pt.matrixTransform(svg.current.getScreenCTM()!.inverse());
    if (p.x < 0 || p.y < 0 || p.x > size[0] || p.y > size[1]) return;
    run("/calib/origin", { mode: "pixel", u: p.x, v: p.y });
  }

  function customize(on: boolean) {
    setForm((f) => ({
      ...f,
      customLayout: on,
      layoutRows: on && f.layoutRows.length === 0 ? defaultLayout.map((r) => ({ ...r })) : f.layoutRows,
    }));
  }
  function editRow(i: number, key: keyof Row, value: string) {
    setForm((f) => ({ ...f, layoutRows: f.layoutRows.map((r, j) => (j === i ? { ...r, [key]: Number(value) } : r)) }));
  }

  const q = result ? quality(result) : "";
  const pvUi = st?.camera?.host ? `http://${st.camera.host === "127.0.0.1" ? location.hostname : st.camera.host}:5800` : null;

  return (
    <div className="page">
      <section className="main-panel">
        <div className="viewer-head">
          {connected && st?.camera ? (
            <>
              <span className="info"><span className="dot ok" />{st.camera.name}</span>
              <span className="info mono">{st.camera.size[0]}×{st.camera.size[1]}, {st.rate.toFixed(0)} fps</span>
              {corners ? (
                <span className="tags" title="Corners captured">
                  {Array.from({ length: corners.total }, (_, i) => (
                    <span key={i} className={`tag${i < corners.index ? " on" : ""}`}>{i + 1}</span>
                  ))}
                </span>
              ) : (
                <span className="tags" title="Reference tags in view">
                  {refIds.map((id) => (
                    <span key={id} className={`tag${seen.has(id) ? " on" : ""}`}>{id}</span>
                  ))}
                </span>
              )}
            </>
          ) : (
            <span className="info">
              <span className={`dot${st?.state === "connecting" ? " warn pulse" : st?.state === "error" ? " red" : ""}`} />
              {st?.state === "connecting" ? "Connecting to PhotonVision…" : st?.state === "error" ? "Connection failed" : "Not connected"}
            </span>
          )}
          {result && connected && <span className="head-hint">Click the picture to place the origin</span>}
        </div>

        <div className={`picture${result && connected ? " pickable" : ""}`}>
          {connected ? (
            <>
              <img key={streamKey} src={`/api/v1/calib/stream.mjpg?k=${streamKey}`} alt="PhotonVision camera" />
              <svg className="overlay" ref={svg} viewBox={`0 0 ${size[0]} ${size[1]}`} preserveAspectRatio="xMidYMid meet" onClick={clickPicture}>
                {st?.overlay && (
                  <>
                    {st.overlay.centre_lines.map((l, i) => <polyline key={`c${i}`} points={pts(l)} className="centre" />)}
                    {st.overlay.outline.map((l, i) => <polyline key={`o${i}`} points={pts(l)} className="outline" />)}
                    {st.overlay.snaps.map((s, i) => <circle key={`s${i}`} cx={s[0]} cy={s[1]} r={fs * 0.22} className="snap" />)}
                    <line x1={st.overlay.origin[0]} y1={st.overlay.origin[1]} x2={st.overlay.x_axis[0]} y2={st.overlay.x_axis[1]} className="axis x" />
                    <line x1={st.overlay.origin[0]} y1={st.overlay.origin[1]} x2={st.overlay.y_axis[0]} y2={st.overlay.y_axis[1]} className="axis y" />
                    <text x={st.overlay.x_axis[0]} y={st.overlay.x_axis[1]} dx={fs * 0.4} fontSize={fs} className="axis-label x">X</text>
                    <text x={st.overlay.y_axis[0]} y={st.overlay.y_axis[1]} dx={fs * 0.4} fontSize={fs} className="axis-label y">Y</text>
                    <circle cx={st.overlay.origin[0]} cy={st.overlay.origin[1]} r={fs * 0.38} className="origin" />
                  </>
                )}
                {!result && corners?.captured.map((c, i) => (
                  <g key={`k${i}`}>
                    <circle cx={c[0]} cy={c[1]} r={fs * 0.3} className="snap" />
                    <text x={c[0]} y={c[1]} dx={fs * 0.45} fontSize={fs} className="det-label">{i + 1}</text>
                  </g>
                ))}
                {(st?.detections ?? []).map((d) => (
                  <g key={d.id}>
                    <polygon points={pts(d.corners)} className={`det${corners || isRef(d.id) ? " ref" : ""}`} />
                    <text x={d.center[0]} y={d.center[1]} fontSize={fs} className="det-label">{d.id}</text>
                  </g>
                ))}
              </svg>
            </>
          ) : (
            <div className="empty">
              <h3>{st?.state === "connecting" ? "Waiting for PhotonVision…" : "Connect to PhotonVision"}</h3>
              <p className="muted">The camera, its lens calibration and the tag detections all come from PhotonVision.</p>
            </div>
          )}
        </div>
      </section>

      <aside className="side">
        <Card
          title="PhotonVision"
          step={1}
          active={step === 1}
          done={connected}
          aside={connected && <button className="btn sm ghost" disabled={busy} onClick={() => run("/calib/disconnect")}>Disconnect</button>}
        >
          {connected && st?.camera ? (
            <>
              <dl className="kv">
                <dt>Camera</dt><dd>{st.camera.name}</dd>
                <dt>Resolution</dt><dd>{st.camera.size[0]} × {st.camera.size[1]}</dd>
                <dt>Focal length</dt><dd>{st.camera.fx} px</dd>
                <dt>Stream</dt><dd>{st.camera.stream ?? "none, tags only"}</dd>
              </dl>
              {pvUi && <a className="btn sm block ext" href={pvUi} target="_blank" rel="noreferrer">Open PhotonVision UI ↗</a>}
              {pvs?.ours && <button className="btn sm block ghost stop-pv" disabled={pv.busy} onClick={photonvision.stop}>Stop PhotonVision</button>}
            </>
          ) : (
            <div className="stack">
              {pvs && !pvs.up && (
                <div className="pv">
                  {pvs.state === "starting" ? (
                    <>
                      <div className="pv-row"><span className="dot warn pulse" /><b>Starting PhotonVision…</b><span className="muted">about 20 s</span></div>
                      <div className="pv-log mono">{pv.log.slice(-4).map((line, i) => <div key={i}>{line}</div>)}</div>
                    </>
                  ) : installing ? (
                    <>
                      <div className="pv-row"><span className="dot warn pulse" /><b>{pvs.install.step ?? "Downloading PhotonVision…"}</b></div>
                      <div className="progress">
                        <div className="bar" style={{ width: `${pvs.install.total ? ((pvs.install.got ?? 0) / pvs.install.total) * 100 : 0}%` }} />
                      </div>
                    </>
                  ) : pvs.installed ? (
                    <>
                      <div className="pv-row"><span className="dot" /><b>PhotonVision is not running</b></div>
                      <button className="btn primary block" disabled={pv.busy} onClick={() => { setAutoConnect(true); photonvision.start(); }}>Start PhotonVision</button>
                      <small className="dim">{pvs.jar} from {pvs.folder}</small>
                      {pvs.needs_download && (
                        <button className="link" disabled={pv.busy} onClick={photonvision.install}>
                          {pvs.version_mismatch ? `Download PhotonVision ${pvs.photonlibpy}, the version this app needs` : "Download a Java known to work with it"}
                        </button>
                      )}
                    </>
                  ) : (
                    <>
                      <div className="pv-row"><span className="dot" /><b>PhotonVision isn't installed</b></div>
                      <button className="btn primary block" disabled={pv.busy} onClick={photonvision.install}>Download PhotonVision</button>
                      <small className="dim">
                        About 160 MB: PhotonVision {pvs.photonlibpy} and a Java to run it, into <span className="mono">{pvs.folder}</span>.
                        If it runs on another computer, just connect.
                      </small>
                    </>
                  )}
                  {pvs.install.state === "error" && <p className="note error">Download failed: {pvs.install.error}</p>}
                  {pvs.install.state === "done" && !pvs.running && <p className="note">{pvs.install.step}</p>}
                  {pvs.failure && <p className="note error">PhotonVision stopped: {pvs.failure}.</p>}
                </div>
              )}
              {pvs?.up && pvs.setup_hints.length > 0 && (
                <div className="note error">
                  PhotonVision needs setting up in its own UI (the PhotonVision link at the top):
                  <ul>{pvs.setup_hints.map((h) => <li key={h}>{h}</li>)}</ul>
                </div>
              )}
              {pvs?.version_mismatch && (
                <p className="note error">PhotonVision {pvs.version} and photonlibpy {pvs.photonlibpy} must be the same version, or the tags won't come through.</p>
              )}
              <p className="muted small">
                Point PhotonVision's <b>NetworkTables server address</b> at this machine, with an AprilTag 36h11 pipeline and a calibrated lens.
              </p>
              <button className={`btn block${!pvs || pvs.up ? " primary" : ""}`} disabled={busy || st?.state === "connecting"} onClick={connect}>
                {st?.state === "connecting" ? "Connecting…" : pvs && !pvs.up ? "Connect (PhotonVision elsewhere)" : "Connect"}
              </button>
              {st?.state === "error" && st.error && <p className="note error">{st.error}</p>}
              <button className="link" onClick={() => setAdvanced(!advanced)}>{advanced ? "Hide" : "Show"} connection options</button>
              {advanced && (
                <>
                  <label className="field"><span>Camera name</span><input className="input" placeholder="the only camera" value={form.camera} onChange={(e) => set("camera", e.target.value)} /></label>
                  <label className="field"><span>NetworkTables server</span><input className="input mono" placeholder="run it here" value={form.ntServer} onChange={(e) => set("ntServer", e.target.value)} /></label>
                  <label className="field"><span>Stream URL</span><input className="input mono" placeholder="PhotonVision's :1181/stream.mjpg" value={form.streamUrl} onChange={(e) => set("streamUrl", e.target.value)} /></label>
                  <div className="row">
                    <label className="field"><span>Res. width</span><input className="input mono" type="number" placeholder="auto" value={form.resW} onChange={(e) => set("resW", e.target.value)} /></label>
                    <label className="field"><span>Res. height</span><input className="input mono" type="number" placeholder="auto" value={form.resH} onChange={(e) => set("resH", e.target.value)} /></label>
                  </div>
                </>
              )}
            </div>
          )}
        </Card>

        <Card title="Capture reference tags" step={2} active={step === 2} done={!!result}>
          <div className="stack">
            <div className="row">
              <label className="field">
                <span>Tag size (m)</span>
                <input className="input mono" type="number" step="0.001" min="0.001" placeholder="e.g. 0.177" value={form.tagSize} onChange={(e) => set("tagSize", e.target.value)} disabled={st?.capturing} />
              </label>
              {form.oneTag ? (
                <label className="field">
                  <span>Frames per corner</span>
                  <input className="input mono" type="number" min="3" max="300" value={form.perCorner} onChange={(e) => set("perCorner", Number(e.target.value))} disabled={st?.capturing} />
                </label>
              ) : (
                <label className="field">
                  <span>Frames</span>
                  <input className="input mono" type="number" min="3" max="40" value={form.minFrames} onChange={(e) => set("minFrames", Number(e.target.value))} disabled={st?.capturing} />
                </label>
              )}
            </div>
            <small className="dim">Tag size is the black square's edge.</small>
            <div className="row">
              <label className="field"><span>Field width (m)</span><input className="input mono" type="number" step="0.001" value={form.fieldW} onChange={(e) => set("fieldW", e.target.value)} disabled={st?.capturing} /></label>
              <label className="field"><span>Field height (m)</span><input className="input mono" type="number" step="0.001" value={form.fieldH} onChange={(e) => set("fieldH", e.target.value)} disabled={st?.capturing} /></label>
            </div>

            <div>
              <Toggle label="One tag at a time" checked={form.oneTag} onChange={(on) => set("oneTag", on)} disabled={st?.capturing} />
              <small className="dim">
                {form.oneTag
                  ? "Put one tag at each corner in turn, in the order below (counterclockwise seen from above). Any tag id works."
                  : "Four tags with these ids, all in view at once."}
              </small>
            </div>

            <div>
              <Toggle label="Reference tags at custom positions" checked={form.customLayout} onChange={customize} />
              <table className="plain">
                <thead><tr><th>{form.oneTag ? "#" : "Tag"}</th><th>x (m)</th><th>y (m)</th><th /></tr></thead>
                <tbody>
                  {form.customLayout
                    ? form.layoutRows.map((r, i) => (
                        <tr key={i} className={corners && corners.index === i && st?.capturing ? "current" : undefined}>
                          <td>{form.oneTag ? <span className="mono">{i + 1}</span> : <input className="input mono cell" type="number" value={r.id} onChange={(e) => editRow(i, "id", e.target.value)} />}</td>
                          <td><input className="input mono cell" type="number" step="0.001" value={r.x} onChange={(e) => editRow(i, "x", e.target.value)} /></td>
                          <td><input className="input mono cell" type="number" step="0.001" value={r.y} onChange={(e) => editRow(i, "y", e.target.value)} /></td>
                          <td><span className={`seen${(corners ? i < corners.index : seen.has(r.id)) ? " on" : ""}`} /></td>
                        </tr>
                      ))
                    : layout.map((r, i) => (
                        <tr key={r.id} className={corners && corners.index === i && st?.capturing ? "current" : undefined}>
                          <td className="mono">{form.oneTag ? i + 1 : r.id}</td>
                          <td className="mono">{r.x.toFixed(3)}</td>
                          <td className="mono">{r.y.toFixed(3)}</td>
                          <td><span className={`seen${(corners ? i < corners.index : seen.has(r.id)) ? " on" : ""}`} /></td>
                        </tr>
                      ))}
                </tbody>
              </table>
            </div>

            {corners && !result && (st?.capturing || cornersDone) && (
              <>
                {cornersDone ? (
                  <div className="progress-text"><span>All {corners.total} corners captured</span><span className="muted">solve when ready</span></div>
                ) : (
                  <>
                    <div className="corner-now">
                      Corner <b>{corners.index + 1}/{corners.total}</b>: put one tag at
                      <b className="mono"> ({layout[corners.index]?.x.toFixed(3)}, {layout[corners.index]?.y.toFixed(3)})</b>
                    </div>
                    <div className="progress">
                      <div className="bar" style={{ width: `${Math.min(100, (corners.count / corners.per_corner) * 100)}%` }} />
                    </div>
                    <div className="progress-text">
                      <span className="mono">{corners.count}/{corners.per_corner}</span>
                      <span className={corners.status === "ready" ? "" : "muted"}>{CORNER_MSG[corners.status ?? "no_tag"] ?? ""}</span>
                    </div>
                  </>
                )}
              </>
            )}

            {!corners && st && (st.capturing || (st.captured > 0 && !result)) && (
              <>
                <div className="progress">
                  <div className="bar" style={{ width: `${Math.min(100, (st.captured / maxFrames) * 100)}%` }} />
                  <div className="mark" style={{ left: `${(minNeeded / maxFrames) * 100}%` }} />
                </div>
                <div className="progress-text">
                  <span className="mono">{st.captured} frames</span>
                  <span className="muted">{st.captured >= minNeeded ? "enough, solve when ready" : allVisible ? "capturing…" : "waiting for every reference tag"}</span>
                </div>
              </>
            )}

            {corners && st?.capturing && (
              <>
                <button className={`btn block${corners.ready ? " primary" : ""}`} disabled={busy || !corners.ready} onClick={() => corner("confirm")}>
                  Confirm corner {corners.index + 1} <kbd>q</kbd>
                </button>
                <div className="row">
                  <button className="btn sm" disabled={busy} onClick={() => corner("retry")}>Retry corner <kbd>r</kbd></button>
                  <button className="btn sm" disabled={busy || corners.index === 0} onClick={() => corner("back")}>Back a corner</button>
                </div>
              </>
            )}
            {corners && cornersDone && !st?.capturing && !result && (
              <button className="btn sm" disabled={busy} onClick={() => corner("back")}>Redo the last corner</button>
            )}

            <div className="row">
              {st?.capturing ? (
                <button className="btn" disabled={busy} onClick={() => run("/calib/capture/stop")}>Stop</button>
              ) : (
                <button className={`btn${!canSolve ? " primary" : ""}`} disabled={busy || !connected} onClick={startCapture}>
                  {st && (st.captured > 0 || (corners && corners.index > 0)) ? "Capture again" : "Start capture"}
                </button>
              )}
              <button className={`btn${canSolve ? " primary" : ""}`} disabled={busy || !canSolve} onClick={() => run("/calib/solve", {}, "Field pose solved")}>Solve</button>
            </div>
            {connected && !form.oneTag && !allVisible && !st?.capturing && (
              <p className="note">Every reference tag must be in view at once. Visible now: {[...seen].join(", ") || "none"}.</p>
            )}
          </div>
        </Card>

        <Card title="Origin & save" step={3} active={step === 3}>
          {result ? (
            <div className="stack">
              {result.from === "calibrated" && (
                <div className={`quality ${q}`}>
                  <span className={`dot ${q === "good" ? "ok" : q === "fair" ? "warn" : "red"}`} />
                  {result.fit_mm
                    ? (q === "good" ? "The corners fit within placing-by-hand error." : q === "fair" ? "Usable: a corner is a few cm out. Check where the tag sat." : "The corners don't fit. Check the field size, the corner order and that the tag sat still.")
                    : (q === "good" ? "The frames agree closely." : q === "fair" ? "Usable, with some spread between frames." : "The frames disagree. Check the tags and the lens calibration.")}
                </div>
              )}
              <dl className="kv">
                {result.frames !== null && <><dt>Frames used</dt><dd>{result.frames}</dd></>}
                {result.rotation_spread_deg !== null && <><dt>Rotation spread</dt><dd>{result.rotation_spread_deg.toFixed(3)}°</dd></>}
                {result.translation_spread_mm !== null && <><dt>Position spread</dt><dd>{result.translation_spread_mm.toFixed(2)} mm</dd></>}
                {result.fit_mm && <><dt>Corner miss</dt><dd>{result.fit_mm.map((f, i) => `${i + 1}: ${f === null ? "–" : f.toFixed(0)}`).join(", ")} mm</dd></>}
                {result.reproj_px !== null && <><dt>Corner fit</dt><dd>{result.reproj_px.toFixed(2)} px rms, {result.max_reproj_px?.toFixed(2)} px worst</dd></>}
                <dt>Camera height</dt><dd>{result.camera_height_m.toFixed(3)} m</dd>
                <dt>Field</dt><dd>{result.field[0]} × {result.field[1]} m</dd>
                <dt>Origin</dt><dd>{result.origin ? `(${result.origin[0]}, ${result.origin[1]}) m` : "first corner"}</dd>
              </dl>
              <div className="row">
                <button className="btn sm" disabled={busy} onClick={() => run("/calib/origin", { mode: "corner" })}>Corner</button>
                <button className="btn sm" disabled={busy} onClick={() => run("/calib/origin", { mode: "center" })}>Centre</button>
              </div>
              <div className="row">
                <input className="input mono" type="number" step="0.001" placeholder="x (m)" value={originX} onChange={(e) => setOriginX(e.target.value)} />
                <input className="input mono" type="number" step="0.001" placeholder="y (m)" value={originY} onChange={(e) => setOriginY(e.target.value)} />
                <button className="btn sm" disabled={busy || originX === "" || originY === ""} onClick={() => run("/calib/origin", { mode: "field", x: Number(originX), y: Number(originY) })}>Set</button>
              </div>
              <small className="dim">{connected ? "Or click the picture. Clicks snap to corners, edge midpoints and the centre." : "Connect to click the origin on the picture."}</small>
              <button className="btn primary block" disabled={busy} onClick={save}>Save calibration</button>
            </div>
          ) : (
            <div className="stack">
              <p className="muted small">Solve the field pose first, then choose where (0, 0) is and save.</p>
              {st?.saved.exists && (
                <button className="btn block" disabled={busy} onClick={() => run("/calib/load-saved")}>Move the origin of the saved calibration</button>
              )}
            </div>
          )}
        </Card>

        <Card title="Saved calibration" aside={st?.saved.exists ? <><span className="dot ok" />Saved</> : <><span className="dot" />None</>}>
          {st?.saved.exists ? (
            <>
              <dl className="kv">
                <dt>Field</dt><dd>{st.saved.field ? `${st.saved.field[0]} × ${st.saved.field[1]} m` : "–"}</dd>
                <dt>Origin</dt><dd>{st.saved.origin ? `(${st.saved.origin[0]}, ${st.saved.origin[1]}) m` : "first corner"}</dd>
                <dt>Saved</dt><dd>{fmtTime(st.saved.modified)}</dd>
              </dl>
              <p className="src">{st.saved.error ?? st.saved.source}</p>
            </>
          ) : (
            <p className="muted small">Nothing saved yet. Trackers use a made-up field until you save one.</p>
          )}
        </Card>
      </aside>
    </div>
  );
}
