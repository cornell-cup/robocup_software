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

/** The 4 reference tags around one camera's section of a field split between
 * `n` cameras along x (calibrate_field.split_part_layout): corners are ids 0-3,
 * the pair on the seam between sections s and s+1 is 10+2(s-1) (near, y = 0)
 * and 11+2(s-1) (far). In the order one tag is carried round them. */
function splitPartLayout(w: number, h: number, n: number, k: number): Row[] {
  const id = (b: number, far: boolean) =>
    b === 0 ? (far ? 3 : 0) : b === n ? (far ? 2 : 1) : 10 + 2 * (b - 1) + (far ? 1 : 0);
  const x0 = (w * (k - 1)) / n, x1 = (w * k) / n;
  return [
    { id: id(k - 1, false), x: x0, y: 0 },
    { id: id(k, false), x: x1, y: 0 },
    { id: id(k, true), x: x1, y: h },
    { id: id(k - 1, true), x: x0, y: h },
  ];
}
const grade = (mm: number) => (mm < 10 ? "good" : mm < 20 ? "fair" : "poor");
/** The spots table's colours for a grade. */
const GRADE_CLASS = { good: "pass", fair: "warn", poor: "fail" } as const;

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
    checkInset: false, checkTagId: "",
    split: 1, part: 1, overlapHeight: "",
  });
  const set = <K extends keyof typeof form>(k: K, v: (typeof form)[K]) => setForm((f) => ({ ...f, [k]: v }));

  const connected = st?.state === "connected";
  const size = st?.camera?.size ?? [1280, 720];
  const fw = Number(form.fieldW), fh = Number(form.fieldH);
  const split = form.split > 1 ? form.split : 1;
  const part = Math.min(Math.max(1, form.part), split);
  const multi = split > 1;
  const defaultLayout: Row[] = !(fw > 0 && fh > 0)
    ? []
    : multi
      ? splitPartLayout(fw, fh, split, part)
      : [{ id: 0, x: 0, y: 0 }, { id: 1, x: fw, y: 0 }, { id: 2, x: fw, y: fh }, { id: 3, x: 0, y: fh }];
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
  const check = st?.check;
  const checking = !!check?.active;
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

  // The server keeps which camera of several this is: it decides the tags to
  // capture and the file to save. Tell it whenever the form and it differ.
  const splitSent = useRef("");
  useEffect(() => {
    if (!st || st.capturing) return;
    const want = `${split}/${part}`;
    if (`${st.split}/${st.part}` === want || splitSent.current === want) return;
    splitSent.current = want;
    api.post<CalibStatus>("/calib/split", { split, part })
      .then(setSt)
      .catch((e) => toasts.error(e))
      .finally(() => { splitSent.current = ""; });
  }, [split, part, st?.split, st?.part, st?.capturing]);

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

  const connect = (camera?: string) =>
    run("/calib/connect", {
      camera: (camera ?? form.camera).trim() || null,
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
      // The server works out the default layout itself (one camera's
      // section of several, too); only a custom one is sent.
      layout: form.customLayout && form.layoutRows.length
        ? Object.fromEntries(layout.map((r) => [String(r.id), [r.x, r.y]]))
        : null,
    });
  }

  function startOverlap() {
    const tag = Number(form.tagSize);
    if (!(tag > 0)) return toasts.push("error", "Enter the tag size first (black square edge, metres).");
    const h = form.overlapHeight.trim() === "" ? 0 : Number(form.overlapHeight);
    if (!(h >= 0 && h < 2)) return toasts.push("error", "Tag height is 0 (on the floor) to 2 m.");
    run("/calib/overlap/start", { tag_size: tag, tag_height: h });
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

  function startCheck() {
    const tag = Number(form.tagSize);
    if (!(tag > 0)) return toasts.push("error", "Enter the tag size first (black square edge, metres).");
    const id = form.checkTagId.trim();
    if (id !== "" && !(Number.isInteger(Number(id)) && Number(id) >= 0)) return toasts.push("error", "The tag id is a whole number, or leave it empty.");
    run("/calib/check/start", { tag_size: tag, inset: form.checkInset ? 0.05 : 0, tag_id: id === "" ? null : Number(id) });
  }

  const checkAction = (action: "confirm" | "redo" | "skip" | "back" | "stop") =>
    run("/calib/check/action", { action }, action === "confirm" && check?.index === (check?.total ?? 0) - 1 ? "Check finished: report saved" : undefined);

  // The check: q or Enter confirms a spot, r redoes it, s skips it.
  useEffect(() => {
    if (!checking) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA" || e.ctrlKey || e.metaKey || e.altKey) return;
      if ((e.key === "q" || e.key === "Enter") && check?.ready) checkAction("confirm");
      else if (e.key === "r") checkAction("redo");
      else if (e.key === "s") checkAction("skip");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [checking, check?.ready, check?.index]);

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
  const fileName = (p?: string) => (p ? p.split(/[\\/]/).pop() ?? p : "");
  const ownFile = fileName(st?.saved.path);
  // Several cameras: the others' saved calibrations, and whether this one's
  // origin matches theirs (the tracker refuses cameras that disagree).
  const otherCams = (st?.saved_cameras ?? []).filter((c) => fileName(c.path) !== ownFile && c.exists && !c.error);
  const sameXY = (a?: number[] | null, b?: number[] | null) =>
    (!a && !b) || (!!a && !!b && Math.abs(a[0] - b[0]) < 1e-3 && Math.abs(a[1] - b[1]) < 1e-3);
  const theirOrigin = otherCams[0]?.origin ?? null;
  const originDiffers = multi && !!result && otherCams.length > 0 && !sameXY(theirOrigin, result.origin);
  const overlap = st?.overlap;
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
          {checking ? (
            <span className="head-hint">Spot {(check?.index ?? 0) + 1} of {check?.total}: put the tag on the pink cross</span>
          ) : (
            result && connected && <span className="head-hint">Click the picture to place the origin</span>
          )}
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
                {checking && check?.targets_px?.map((p, i) => {
                  const r = check.results?.[i];
                  if (i === check.index)
                    return (
                      <g key={`t${i}`} className="target">
                        <circle cx={p[0]} cy={p[1]} r={fs * 0.9} />
                        <line x1={p[0] - fs * 1.3} y1={p[1]} x2={p[0] + fs * 1.3} y2={p[1]} />
                        <line x1={p[0]} y1={p[1] - fs * 1.3} x2={p[0]} y2={p[1] + fs * 1.3} />
                      </g>
                    );
                  return (
                    <g key={`t${i}`}>
                      <circle cx={p[0]} cy={p[1]} r={fs * 0.32} className={`spot ${r ? r.grade.toLowerCase() : "todo"}`} />
                      <text x={p[0]} y={p[1]} dx={fs * 0.55} fontSize={fs * 0.8} className="det-label">{i + 1}</text>
                    </g>
                  );
                })}
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
                      {pvs.needs_download && !pvs.no_download && (
                        <button className="link" disabled={pv.busy} onClick={photonvision.install}>
                          {pvs.version_mismatch ? `Download PhotonVision ${pvs.photonlibpy}, the version this app needs` : "Download a Java known to work with it"}
                        </button>
                      )}
                    </>
                  ) : pvs.no_download ? (
                    <>
                      <div className="pv-row"><span className="dot" /><b>PhotonVision can't run on this computer</b></div>
                      <small className="dim">
                        {pvs.no_download}. Plug the camera into that computer, point its NetworkTables server address at this one's IP, then connect.
                      </small>
                    </>
                  ) : (
                    <>
                      <div className="pv-row"><span className="dot" /><b>PhotonVision isn't installed</b></div>
                      <button className="btn primary block" disabled={pv.busy} onClick={photonvision.install}>{pvs.from_source ? "Build" : "Download"} PhotonVision</button>
                      <small className="dim">
                        {pvs.from_source
                          ? <>PhotonVision has no Mac release, so this builds {pvs.photonlibpy} from source, which takes a while, and gets a Java to run it, into <span className="mono">{pvs.folder}</span>. It isn't officially supported on a Mac.</>
                          : <>About 160 MB: PhotonVision {pvs.photonlibpy} and a Java to run it, into <span className="mono">{pvs.folder}</span>.</>}
                        {" "}If it runs on another computer, just connect.
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
              <button className={`btn block${!pvs || pvs.up ? " primary" : ""}`} disabled={busy || st?.state === "connecting"} onClick={() => connect()}>
                {st?.state === "connecting" ? "Connecting…" : pvs && !pvs.up ? "Connect (PhotonVision elsewhere)" : "Connect"}
              </button>
              {st?.state === "error" && st.cameras?.length ? (
                <div className="stack">
                  <p className="note">PhotonVision has several cameras. Which one looks at the field?</p>
                  {st.cameras.map((name) => (
                    <button key={name} className="btn block" disabled={busy} onClick={() => { set("camera", name); connect(name); }}>
                      Use {name}
                    </button>
                  ))}
                  <small className="dim">Remembered for next time, here and on the Tracking page.</small>
                </div>
              ) : (
                st?.state === "error" && st.error && <p className="note error">{st.error}</p>
              )}
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
                <span>Cameras over the field</span>
                <select className="input" value={split} disabled={st?.capturing} onChange={(e) => setForm((f) => ({ ...f, split: Number(e.target.value), part: Math.min(f.part, Number(e.target.value)) }))}>
                  {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n === 1 ? "1 (whole field)" : n}</option>)}
                </select>
              </label>
              {multi && (
                <label className="field">
                  <span>This camera covers</span>
                  <select className="input" value={part} disabled={st?.capturing} onChange={(e) => set("part", Number(e.target.value))}>
                    {Array.from({ length: split }, (_, i) => (
                      <option key={i} value={i + 1}>section {i + 1}{i === 0 ? " (x = 0 end)" : i === split - 1 ? " (far end)" : ""}</option>
                    ))}
                  </select>
                </label>
              )}
            </div>
            {multi && (
              <small className="dim">
                The field is split along its width into {split} sections, one per camera. Calibrate each camera here in turn
                (connect to it, pick its section, capture, save): each is saved as <span className="mono">field_pose_&lt;camera&gt;.json</span>.
                Reference tags: the corners (0-3) and a pair on each seam (10 and 11 on the first, at the near and far edge).
              </small>
            )}
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
                  ? `Put one tag at each ${multi ? "position of this section" : "corner"} in turn, in the order below (counterclockwise seen from above). Any tag id works.`
                  : multi
                    ? "This section's four tags, with these ids, all in view at once."
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
              {originDiffers && (
                <div className="note error">
                  {fileName(otherCams[0].path)} uses origin {theirOrigin ? `(${theirOrigin[0]}, ${theirOrigin[1]})` : "first corner"}.
                  Every camera must share one, or tracking refuses to merge them.
                  <button className="link" disabled={busy} onClick={() => run("/calib/origin", theirOrigin ? { mode: "field", x: theirOrigin[0], y: theirOrigin[1] } : { mode: "corner" })}>
                    Use the same origin
                  </button>
                </div>
              )}
              <button className="btn primary block" disabled={busy || (multi && !connected)} onClick={save}>
                {multi ? `Save as ${ownFile || "field_pose_<camera>.json"}` : "Save calibration"}
              </button>
              {multi && !connected && <small className="dim">Connect to the camera to save: its file is named after it.</small>}
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

        <Card title="Check the accuracy" step={4} active={checking} done={!!check?.summary && !checking}>
          <div className="stack">
            {!checking && (
              <>
                <p className="muted small">
                  Tests the <b>saved</b> calibration with a tape measure. Mark 9 spots with tape, measured from (0, 0): the 4 corners, the middle of each edge and the centre. Then put one tag on each in turn and it says how far off each one reads, in mm.
                </p>
                {multi && <small className="dim">One of several cameras: only the spots this camera can see are checked.</small>}
                <div className="row">
                  <label className="field">
                    <span>Tag size (m)</span>
                    <input className="input mono" type="number" step="0.001" min="0.001" placeholder="e.g. 0.127" value={form.tagSize} onChange={(e) => set("tagSize", e.target.value)} />
                  </label>
                  <label className="field">
                    <span>Tag id</span>
                    <input className="input mono" type="number" min="0" placeholder="any" value={form.checkTagId} onChange={(e) => set("checkTagId", e.target.value)} />
                  </label>
                </div>
                <small className="dim">Tag id: leave empty if only one tag will be in view.</small>
                <div>
                  <Toggle label="Edge marks 5 cm inside the edges" checked={form.checkInset} onChange={(on) => set("checkInset", on)} />
                  <small className="dim">For when a wall stops the tag sitting centred on the field's edge.</small>
                </div>
                <button className="btn primary block" disabled={busy || !connected || !st?.saved.exists || !!st?.capturing} onClick={startCheck}>
                  {check?.summary ? "Check again" : "Start the check"}
                </button>
                {!connected && <small className="dim">Connect to PhotonVision first (step 1).</small>}
                {connected && !st?.saved.exists && <p className="note">Save a field calibration first: the check tests the saved one.</p>}
              </>
            )}

            {check?.targets && (
              <table className="plain spots">
                <thead><tr><th>#</th><th>x (m)</th><th>y (m)</th><th>Off by</th></tr></thead>
                <tbody>
                  {check.targets.map((t, i) => {
                    const r = check.results?.[i];
                    return (
                      <tr key={i} className={checking && i === check.index ? "current" : undefined}>
                        <td className="mono">{i + 1}</td>
                        <td className="mono">{t[0].toFixed(3)}</td>
                        <td className="mono">{t[1].toFixed(3)}</td>
                        <td className={`mono grade ${r ? r.grade.toLowerCase() : ""}`}>
                          {r ? (r.skipped ? "skipped" : `${r.err_mm?.toFixed(0)} mm`) : checking && i === check.index ? "now" : ""}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}

            {checking && check && check.targets && (
              <>
                <div className="corner-now">
                  Spot <b>{(check.index ?? 0) + 1}/{check.total}</b>: put the tag's centre on
                  <b className="mono"> ({check.targets[check.index ?? 0][0].toFixed(3)}, {check.targets[check.index ?? 0][1].toFixed(3)})</b>, {check.placement}, then let go.
                </div>
                <div className="progress">
                  <div className="bar" style={{ width: `${Math.min(100, ((check.count ?? 0) / (check.frames ?? 1)) * 100)}%` }} />
                </div>
                <div className="progress-text">
                  <span className="mono">{check.count}/{check.frames}</span>
                  <span className={check.ready ? "" : "muted"}>
                    {check.message}
                    {check.live_mm != null && ` (${check.live_mm.toFixed(0)} mm off)`}
                  </span>
                </div>
                <button className={`btn block${check.ready ? " primary" : ""}`} disabled={busy || !check.ready} onClick={() => checkAction("confirm")}>
                  Confirm spot {(check.index ?? 0) + 1} <kbd>q</kbd>
                </button>
                <div className="row">
                  <button className="btn sm" disabled={busy} onClick={() => checkAction("redo")}>Redo <kbd>r</kbd></button>
                  <button className="btn sm" disabled={busy} onClick={() => checkAction("skip")}>Skip <kbd>s</kbd></button>
                  <button className="btn sm" disabled={busy || check.index === 0} onClick={() => checkAction("back")}>Back a spot</button>
                </div>
                <button className="btn sm ghost" disabled={busy} onClick={() => checkAction("stop")}>Stop the check</button>
              </>
            )}

            {check?.summary && !checking && (
              <>
                <div className={`quality ${check.summary.verdict === "PASS" ? "good" : check.summary.verdict === "WARN" ? "fair" : "poor"}`}>
                  <span className={`dot ${check.summary.verdict === "PASS" ? "ok" : check.summary.verdict === "WARN" ? "warn" : "red"}`} />
                  {check.summary.verdict === "PASS"
                    ? "PASS: positions come out right."
                    : check.summary.verdict === "WARN"
                      ? "WARN: usable, but could be better."
                      : "FAIL: positions are too far off. Recalibrate."}
                </div>
                <dl className="kv">
                  <dt>Average error</dt><dd>{check.summary.values.pos_rms_mm.toFixed(1)} mm <span className="dim">(under 10 is good)</span></dd>
                  <dt>Worst spot</dt><dd>{check.summary.values.pos_max_mm.toFixed(1)} mm <span className="dim">(under 20)</span></dd>
                  <dt>Heading, worst</dt><dd>{check.summary.values.heading_max_deg.toFixed(1)}° <span className="dim">(under 2)</span></dd>
                  {check.summary.n_skipped > 0 && <><dt>Skipped</dt><dd>{check.summary.n_skipped} spot(s)</dd></>}
                </dl>
                {check.summary.diagnosis.length > 0 && (
                  <ul className="diagnosis">{check.summary.diagnosis.map((d) => <li key={d}>{d}</li>)}</ul>
                )}
                <img className="check-plot" src={`/api/v1/calib/check/plot.png?t=${check.summary.checked_at}`} alt="Error at each spot" />
                <p className="src">Report: {check.summary.report}</p>
              </>
            )}
          </div>
        </Card>

        {multi && (
          <Card title="Check the overlap" step={5} active={!!overlap} done={!!overlap?.tags.length && (overlap.worst_mm ?? 99) < 10}>
            <div className="stack">
              {!overlap ? (
                <>
                  <p className="muted small">
                    With every camera calibrated and saved, this watches them all at once. Put a tag where two cameras overlap
                    (the seam tags 10 and 11 already are) and it shows how far apart each camera puts it.
                    Under 10 mm is good. More, and a robot jumps by that much when it crosses from one camera to the other.
                  </p>
                  <div className="row">
                    <label className="field">
                      <span>Tag size (m)</span>
                      <input className="input mono" type="number" step="0.001" min="0.001" placeholder="e.g. 0.177" value={form.tagSize} onChange={(e) => set("tagSize", e.target.value)} />
                    </label>
                    <label className="field">
                      <span>Tag height (m)</span>
                      <input className="input mono" type="number" step="0.001" min="0" placeholder="0, on the floor" value={form.overlapHeight} onChange={(e) => set("overlapHeight", e.target.value)} />
                    </label>
                  </div>
                  <small className="dim">Height: 0 for a tag on the floor; the shell height for one on a robot, to check at the height robots are tracked at.</small>
                  <button className="btn primary block" disabled={busy || !connected || !st?.saved.exists || otherCams.length === 0 || !!st?.capturing || checking} onClick={startOverlap}>
                    Start the overlap check
                  </button>
                  {connected && !st?.saved.exists && <p className="note">Save this camera's calibration first.</p>}
                  {connected && st?.saved.exists && otherCams.length === 0 && <p className="note">Calibrate and save another camera first.</p>}
                </>
              ) : (
                <>
                  <dl className="kv">
                    <dt>Cameras</dt>
                    <dd>{overlap.cameras.map((c) => (
                      <span key={c} className="cam-chip"><span className={`dot ${overlap.live.includes(c) ? "ok" : "red"}`} />{c} </span>
                    ))}</dd>
                  </dl>
                  {overlap.tags.length === 0 ? (
                    <p className="muted small">No tag seen by two cameras yet. Put one where their views overlap.</p>
                  ) : (
                    <>
                      <div className={`quality ${grade(overlap.worst_mm ?? 0)}`}>
                        <span className={`dot ${grade(overlap.worst_mm ?? 0) === "good" ? "ok" : grade(overlap.worst_mm ?? 0) === "fair" ? "warn" : "red"}`} />
                        {grade(overlap.worst_mm ?? 0) === "good"
                          ? "The cameras agree: robots will cross over smoothly."
                          : grade(overlap.worst_mm ?? 0) === "fair"
                            ? "Usable, but robots will shift a little crossing over. Recalibrating may tighten it."
                            : "The cameras disagree. Recalibrate the worse one, and check the field size and tag positions."}
                      </div>
                      <table className="plain spots">
                        <thead><tr><th>Tag</th><th>Apart</th><th>Readings</th><th>Seen by</th></tr></thead>
                        <tbody>
                          {overlap.tags.map((t) => (
                            <tr key={t.id}>
                              <td className="mono">{t.id}</td>
                              <td className={`mono grade ${GRADE_CLASS[grade(t.apart_mm)]}`}>{t.apart_mm.toFixed(1)} mm</td>
                              <td className="mono">{t.n}</td>
                              <td className="small">{t.now ? Object.keys(t.now).join(", ") : <span className="dim">not now</span>}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <small className="dim">"Apart" is the median of the last 30 readings, tag {overlap.tag_size} m, at {overlap.tag_height} m height.</small>
                    </>
                  )}
                  <button className="btn sm ghost" disabled={busy} onClick={() => run("/calib/overlap/stop")}>Stop the overlap check</button>
                </>
              )}
            </div>
          </Card>
        )}

        <Card title="Saved calibration" aside={st?.saved.exists ? <><span className="dot ok" />Saved</> : <><span className="dot" />None</>}>
          {st?.saved.exists ? (
            <>
              <dl className="kv">
                {multi && <><dt>File</dt><dd className="mono">{ownFile}</dd></>}
                <dt>Field</dt><dd>{st.saved.field ? `${st.saved.field[0]} × ${st.saved.field[1]} m` : "–"}</dd>
                <dt>Origin</dt><dd>{st.saved.origin ? `(${st.saved.origin[0]}, ${st.saved.origin[1]}) m` : "first corner"}</dd>
                <dt>Saved</dt><dd>{fmtTime(st.saved.modified)}</dd>
              </dl>
              <p className="src">{st.saved.error ?? st.saved.source}</p>
            </>
          ) : st?.saved.needs_camera ? (
            <p className="muted small">Connect to a camera to see its saved calibration.</p>
          ) : (
            <p className="muted small">Nothing saved yet. Trackers use a made-up field until you save one.</p>
          )}
          {multi && (
            <>
              <table className="plain">
                <thead><tr><th>Camera</th><th>Field</th><th>Origin</th><th>Saved</th></tr></thead>
                <tbody>
                  {(st?.saved_cameras ?? []).length === 0 ? (
                    <tr><td colSpan={4} className="dim">No camera calibrated yet</td></tr>
                  ) : (st?.saved_cameras ?? []).map((c) => (
                    <tr key={c.path} className={fileName(c.path) === ownFile ? "current" : undefined}>
                      <td className="mono">{c.name}</td>
                      <td className="mono">{c.field ? `${c.field[0]}×${c.field[1]}` : "–"}</td>
                      <td className="mono">{c.error ? "unreadable" : c.origin ? `(${c.origin[0]}, ${c.origin[1]})` : "corner"}</td>
                      <td className="small">{c.modified ? new Date(c.modified * 1000).toLocaleDateString() : "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {st?.cameras_agree && !st.cameras_agree.field && <p className="note error">The cameras were calibrated for different field sizes. Recalibrate with the same size.</p>}
              {st?.cameras_agree && !st.cameras_agree.origin && <p className="note error">The cameras use different origins. Give them the same one (step 3) and save each.</p>}
              {(st?.saved_cameras ?? []).length >= 2 && st?.cameras_agree?.field && st.cameras_agree.origin && (
                <small className="dim">
                  Track with all of them: on the Tracking page, PhotonVision camera <span className="mono">{(st?.saved_cameras ?? []).map((c) => c.name).join(", ")}</span>.
                </small>
              )}
            </>
          )}
        </Card>
      </aside>
    </div>
  );
}
