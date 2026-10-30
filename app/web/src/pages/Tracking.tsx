import { useRef, useState } from "react";
import { Card } from "../components/Card";
import { Toggle } from "../components/Toggle";
import { api } from "../lib/api";
import { usePhotonVision } from "../lib/photonvision";
import { usePolling } from "../lib/store";
import { toasts } from "../lib/toast";
import type { BallStatus, CalibStatus, TrackingStatus } from "../lib/types";
import { readSaved, useSavedState, writeSaved } from "../lib/useSavedState";
import "../styles/tracking.css";

const num = (v: number | undefined, d = 2) => (typeof v === "number" ? v.toFixed(d) : "–");

export function Tracking() {
  const [st, setSt] = useState<TrackingStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState(false);
  const [streamKey, setStreamKey] = useState(0);
  const [log, setLog] = useState<{ n: number; text: string }[]>([]);
  const [profiles, setProfiles] = useState<string[]>([]);
  const [calibSaved, setCalibSaved] = useState<boolean | null>(null);
  const { status: pv } = usePhotonVision();

  // The tag size is the one entered on the calibration page, unless changed here.
  const [form, setForm] = useSavedState("tracking.form", {
    tagSize: String(readSaved<{ tagSize?: string }>("calib.form")?.tagSize ?? ""),
    ballProfile: "",
    record: false,
    headingOffset: "", tagCoast: "", ballMinConc: "", ballRadiusMm: "",
    ballAirborne: false, cornerOrigin: false, syntheticField: false,
    photonCamera: "", ntServer: "", stream: "",
  });
  const set = <K extends keyof typeof form>(k: K, v: (typeof form)[K]) => setForm((f) => ({ ...f, [k]: v }));

  const running = !!st?.process.running;
  const live = st?.live.up ? st.live.doc : null;

  const logAfter = useRef(0);
  const wasLive = useRef(false);
  usePolling(async () => {
    try {
      const s = await api.get<TrackingStatus>("/tracking/status");
      if (s.live.up && !wasLive.current) setStreamKey((k) => k + 1);
      wasLive.current = s.live.up;
      setSt(s);
      const l = await api.get<{ lines: { n: number; text: string }[] }>(`/tracking/log?after=${logAfter.current}`);
      if (l.lines.length) {
        logAfter.current = l.lines[l.lines.length - 1].n;
        setLog((old) => [...old, ...l.lines].slice(-300));
      }
    } catch {
      /* server restarting */
    }
  }, 300);
  // What the tracker needs: a calibrated field and a ball profile.
  usePolling(async () => {
    try {
      const [b, c] = await Promise.all([api.get<BallStatus>("/ball/status"), api.get<CalibStatus>("/calib/status")]);
      setProfiles(b.saved.profiles.map((p) => p.name));
      setCalibSaved(c.saved.exists);
    } catch {
      /* server restarting */
    }
  }, 3000);
  const ballProfile = profiles.includes(form.ballProfile) ? form.ballProfile : profiles[0] ?? "";

  async function start() {
    setBusy(true);
    try {
      const opt = (v: string) => (v.trim() === "" ? null : Number(v));
      setSt(await api.post<TrackingStatus>("/tracking/start", {
        tag_size: Number(form.tagSize),
        ball_profile: ballProfile,
        record: form.record,
        heading_offset: opt(form.headingOffset),
        tag_coast: opt(form.tagCoast),
        ball_min_conc: opt(form.ballMinConc),
        ball_radius_mm: opt(form.ballRadiusMm),
        ball_airborne: form.ballAirborne,
        corner_origin: form.cornerOrigin,
        synthetic_field: form.syntheticField,
        photon_camera: form.photonCamera.trim() || null,
        nt_server: form.ntServer.trim() || null,
        stream: form.stream.trim() || null,
      }));
      toasts.push("info", "Starting the tracker…");
    } catch (e) {
      toasts.error(e);
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    setBusy(true);
    try {
      setSt(await api.post<TrackingStatus>("/tracking/stop"));
    } catch (e) {
      toasts.error(e);
    } finally {
      setBusy(false);
    }
  }

  function openInSim() {
    const opts = readSaved<Record<string, unknown>>("sim.options") ?? {};
    writeSaved("sim.options", { ...opts, source: "tracker", calibField: !form.syntheticField });
    location.hash = "#/sim";
  }

  const checks = [
    { ok: !!pv?.up, text: "PhotonVision is running", fix: "#/calibration", fixText: "Start it" },
    { ok: !!calibSaved || form.syntheticField, text: "The field is calibrated", fix: "#/calibration", fixText: "Calibrate" },
    { ok: profiles.length > 0, text: "A ball profile exists", fix: "#/ball", fixText: "Make one" },
  ];

  return (
    <div className="page">
      <section className="main-panel">
        <div className="viewer-head">
          {live ? (
            <>
              <span className="info"><span className="dot ok" />Tracking</span>
              <span className="info mono">{num(live.stats.fps, 0)} fps</span>
              <span className="info mono">latency {num(live.stats.latency_ms, 0)} ms</span>
              {live.stats.photon_latency_ms !== undefined && (
                <span className="info mono">PhotonVision {num(live.stats.photon_latency_ms, 0)} ms</span>
              )}
              {st?.process.options.recording && <span className="info rec"><span className="dot red" />Recording</span>}
            </>
          ) : (
            <span className="info">
              <span className={`dot${running ? " warn pulse" : ""}`} />
              {running ? "Starting the tracker…" : "Not tracking"}
            </span>
          )}
        </div>
        <div className="picture">
          {live ? (
            <img key={streamKey} src={`/api/v1/tracking/stream.mjpg?k=${streamKey}`} alt="Tracking view" />
          ) : (
            <div className="empty">
              <h3>{running ? "Waiting for the first frames…" : "Track the robots and the ball"}</h3>
              <p className="muted">
                The tracker reads tags from PhotonVision and finds the ball in its camera stream, then publishes
                field positions for the simulator and anything else listening on port 5556.
              </p>
              {!running && (
                <ul className="checks">
                  {checks.map((c) => (
                    <li key={c.text}>
                      <span className={`dot ${c.ok ? "ok" : ""}`} />
                      {c.text}
                      {!c.ok && <a href={c.fix}>{c.fixText}</a>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </section>

      <aside className="side">
        <Card
          title="Tracker"
          aside={running ? (live ? <><span className="dot ok" />Running</> : <><span className="dot warn pulse" />Starting</>) : <><span className="dot" />Stopped</>}
        >
          {running ? (
            <div className="stack">
              <dl className="kv">
                <dt>Tag size</dt><dd>{st?.process.options.tag_size} m</dd>
                <dt>Ball profile</dt><dd>{st?.process.options.ball_profile}</dd>
                {st?.process.options.recording && <><dt>Recording to</dt><dd>{st.process.options.recording.split(/[\\/]/).pop()}</dd></>}
              </dl>
              <button className="btn block" disabled={!live} onClick={openInSim}>Show the robots in the simulator</button>
              <button className="btn danger block" disabled={busy} onClick={stop}>Stop tracking</button>
            </div>
          ) : (
            <div className="stack">
              <div className="row">
                <label className="field">
                  <span>Tag size (m)</span>
                  <input className="input mono" type="number" step="0.001" placeholder="e.g. 0.177" value={form.tagSize} onChange={(e) => set("tagSize", e.target.value)} />
                </label>
                <label className="field">
                  <span>Ball profile</span>
                  <select className="input" value={ballProfile} onChange={(e) => set("ballProfile", e.target.value)}>
                    {profiles.length === 0 && <option value="">none yet</option>}
                    {profiles.map((p) => <option key={p} value={p}>{p}</option>)}
                  </select>
                </label>
              </div>
              <Toggle label="Record this session" checked={form.record} onChange={(v) => set("record", v)} hint="Saved to vision/runs/, for replaying in the simulator" />
              <button className="link" onClick={() => setMore(!more)}>{more ? "Hide" : "Show"} more options</button>
              {more && (
                <>
                  <div className="row">
                    <label className="field"><span>Heading offset (°)</span><input className="input mono" type="number" placeholder="0" value={form.headingOffset} onChange={(e) => set("headingOffset", e.target.value)} /></label>
                    <label className="field"><span>Keep lost tags (s)</span><input className="input mono" type="number" step="0.1" placeholder="0.5" value={form.tagCoast} onChange={(e) => set("tagCoast", e.target.value)} /></label>
                  </div>
                  <div className="row">
                    <label className="field"><span>Ball min. match (0-255)</span><input className="input mono" type="number" placeholder="80" value={form.ballMinConc} onChange={(e) => set("ballMinConc", e.target.value)} /></label>
                    <label className="field"><span>Ball radius (mm)</span><input className="input mono" type="number" step="0.5" placeholder="from profile" value={form.ballRadiusMm} onChange={(e) => set("ballRadiusMm", e.target.value)} /></label>
                  </div>
                  <Toggle label="Ball can leave the ground" checked={form.ballAirborne} onChange={(v) => set("ballAirborne", v)} hint="Estimate height from the ball's size. Unreliable with the camera far away." />
                  <Toggle label="Ignore the saved origin" checked={form.cornerOrigin} onChange={(v) => set("cornerOrigin", v)} hint="Report positions from the first field corner" />
                  <Toggle label="Use a made-up field" checked={form.syntheticField} onChange={(v) => set("syntheticField", v)} hint="For photon-sim testing, without a calibration" />
                  <label className="field"><span>PhotonVision camera</span><input className="input" placeholder="the only camera" value={form.photonCamera} onChange={(e) => set("photonCamera", e.target.value)} /></label>
                  <label className="field"><span>NetworkTables server</span><input className="input mono" placeholder="run it here" value={form.ntServer} onChange={(e) => set("ntServer", e.target.value)} /></label>
                  <label className="field"><span>Stream URL</span><input className="input mono" placeholder="PhotonVision's :1181/stream.mjpg" value={form.stream} onChange={(e) => set("stream", e.target.value)} /></label>
                </>
              )}
              <button className="btn primary block" disabled={busy || !ballProfile || !form.tagSize} onClick={start}>Start tracking</button>
              <small className="dim">Stops the Field calibration page's PhotonVision connection, if it has one: the tracker needs it.</small>
            </div>
          )}
        </Card>

        <Card title="Live positions" aside={live ? <span className="muted mono">{live.field.width} × {live.field.height} m</span> : undefined}>
          {live ? (
            <table className="plain">
              <thead><tr><th>Tag</th><th>x (m)</th><th>y (m)</th><th>θ (°)</th><th>m/s</th></tr></thead>
              <tbody>
                {live.tags.length === 0 && <tr><td colSpan={5} className="dim">No tags in view.</td></tr>}
                {live.tags.map((t) => (
                  <tr key={t.id} className={t.visible ? undefined : "coasting"} title={t.visible ? "Seen this frame" : "Not seen this frame: predicted"}>
                    <td>{t.id}</td>
                    <td className="mono">{num(t.x)}</td>
                    <td className="mono">{num(t.y)}</td>
                    <td className="mono">{num(t.theta_deg, 0)}</td>
                    <td className="mono">{num(t.speed)}</td>
                  </tr>
                ))}
                <tr className={live.ball?.visible ? "ball" : "ball coasting"}>
                  <td>Ball</td>
                  <td className="mono">{num(live.ball?.x)}</td>
                  <td className="mono">{num(live.ball?.y)}</td>
                  <td className="mono dim">{live.ball?.z !== undefined && live.ball.z > 0.01 ? `z ${num(live.ball.z)}` : ""}</td>
                  <td className="mono">{num(live.ball?.speed)}</td>
                </tr>
              </tbody>
            </table>
          ) : (
            <p className="note">Positions appear here while tracking. Greyed rows are predicted, not seen.</p>
          )}
        </Card>

        <Card title="Log">
          <div className="log mono">
            {log.length ? log.map((l) => <div key={l.n} className={/error|traceback|exception/i.test(l.text) ? "err" : undefined}>{l.text}</div>)
              : <div className="dim">Nothing yet.</div>}
          </div>
        </Card>
      </aside>
    </div>
  );
}
