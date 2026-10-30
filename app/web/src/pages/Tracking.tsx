import { useEffect, useRef, useState } from "react";
import { Card } from "../components/Card";
import { FieldView } from "../components/FieldView";
import { Segmented } from "../components/Segmented";
import { Toggle } from "../components/Toggle";
import { api } from "../lib/api";
import { usePhotonVision } from "../lib/photonvision";
import { usePolling } from "../lib/store";
import { toasts } from "../lib/toast";
import { useTrackingFeed } from "../lib/tracking";
import type { BallStatus, CalibStatus, FieldInfo, FieldScene, TrackingDoc, TrackingStatus } from "../lib/types";
import { readSaved, useSavedState, writeSaved } from "../lib/useSavedState";
import "../styles/tracking.css";

const num = (v: number | undefined, d = 2) => (typeof v === "number" ? v.toFixed(d) : "–");

type View = "both" | "camera" | "field";

/** The options under "more options": every track-combined flag the page
 *  passes on. Empty means the tracker's own default. */
const ADVANCED = {
  tagHeight: "", headingOffset: "", tagCoast: "", tagSigmaA: "", tagSigmaAlpha: "",
  ballMinConc: "", ballRadiusMm: "", ballSigmaA: "", ballSigmaAAir: "", ballSigmaPx: "", ballAirborne: false,
  cornerOrigin: false, fieldPose: "", fieldW: "", fieldH: "",
  syntheticField: false, synthMode: "floor" as "floor" | "wall", camHeight: "", pitch: "", distance: "", yaw: "", pitchWall: "",
  photonCamera: "", ntServer: "", stream: "", photonW: "", photonH: "",
  printPoses: false, printStates: false, jsonOut: "", serveHttp: "", displayWidth: "",
};

/** The tracker's output as FieldView draws it: tags as robots (grey while
 *  predicted, not seen), sized as the Simulator sizes them on this field. */
function sceneFrom(doc: TrackingDoc, ballRadius: number): FieldScene {
  const { width: w, height: h, x0, y0 } = doc.field;
  // sim/config.py's scaling from its 9 x 6 m field
  const scale = Math.min(w / 9, h / 6);
  const mouth = (200 / 140) * (h / 6);
  const field: FieldInfo = {
    width: w, height: h, goal_y_min: (h - mouth) / 2, goal_y_max: (h + mouth) / 2,
    robot_radius: (20 / 140) * scale, ball_radius: ballRadius,
    team_blue_size: Number.MAX_SAFE_INTEGER, num_robots: doc.tags.length, is_default: false,
  };
  const robots = Object.fromEntries(doc.tags.map((t) => [String(t.id), {
    x: t.x - x0, y: t.y - y0, angle: (t.theta_deg * Math.PI) / 180, stale: !t.visible, source: "vision", tag: t.id,
  }]));
  const b = doc.ball;
  return { field, robots, ball: b ? { x: b.x - x0, y: b.y - y0, stale: !b.visible, source: "vision" } : null };
}

function NumberField({ label, value, onChange, placeholder, step, hint }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; step?: number; hint?: string;
}) {
  return (
    <label className="field" title={hint}>
      <span>{label}</span>
      <input className="input mono" type="number" step={step} placeholder={placeholder} value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

export function Tracking() {
  const [st, setSt] = useState<TrackingStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState(false);
  const [streamKey, setStreamKey] = useState(0);
  const [log, setLog] = useState<{ n: number; text: string }[]>([]);
  const [profiles, setProfiles] = useState<BallStatus["saved"]["profiles"]>([]);
  const [calibSaved, setCalibSaved] = useState<boolean | null>(null);
  const [hover, setHover] = useState<{ x: number; y: number } | null>(null);
  const [concDraft, setConcDraft] = useState<number | null>(null);
  const [view, setView] = useSavedState<View>("tracking.view", "both");
  const { status: pv } = usePhotonVision();
  const feed = useTrackingFeed();

  // The tag size is the one entered on the calibration page, unless changed here.
  const [form, setForm] = useSavedState("tracking.form", {
    tagSize: String(readSaved<{ tagSize?: string }>("calib.form")?.tagSize ?? ""),
    ballProfile: "",
    record: false,
    ...ADVANCED,
  });
  const set = <K extends keyof typeof form>(k: K, v: (typeof form)[K]) => setForm((f) => ({ ...f, [k]: v }));

  const running = !!st?.process.running;
  const live = feed.up ? feed.doc : null;
  const keys = live?.stats.view;
  // Keys only reach a tracker this app started (it listens on stdin).
  const controllable = running && !!keys;
  const synthetic = !!st?.process.options.synthetic_field;

  // A new picture stream each time the tracker comes up.
  const wasLive = useRef(false);
  useEffect(() => {
    if (feed.up && !wasLive.current) setStreamKey((k) => k + 1);
    wasLive.current = feed.up;
  }, [feed.up]);
  // A dragged threshold stays as dragged until the tracker reports it back.
  useEffect(() => {
    if (concDraft !== null && keys?.ball_min_conc === concDraft) setConcDraft(null);
  }, [keys?.ball_min_conc, concDraft]);

  const logAfter = useRef(0);
  usePolling(async () => {
    try {
      setSt(await api.get<TrackingStatus>("/tracking/status"));
      const l = await api.get<{ lines: { n: number; text: string }[] }>(`/tracking/log?after=${logAfter.current}`);
      if (l.lines.length) {
        logAfter.current = l.lines[l.lines.length - 1].n;
        setLog((old) => [...old, ...l.lines].slice(-300));
      }
    } catch {
      /* server restarting */
    }
  }, 500);
  // What the tracker needs: a calibrated field and a ball profile.
  usePolling(async () => {
    try {
      const [b, c] = await Promise.all([api.get<BallStatus>("/ball/status"), api.get<CalibStatus>("/calib/status")]);
      setProfiles(b.saved.profiles);
      setCalibSaved(c.saved.exists);
    } catch {
      /* server restarting */
    }
  }, 3000);
  const names = profiles.map((p) => p.name);
  const ballProfile = names.includes(form.ballProfile) ? form.ballProfile : names[0] ?? "";
  const madeUpField = form.syntheticField || calibSaved === false;

  async function start() {
    setBusy(true);
    try {
      const opt = (v: string) => (v.trim() === "" ? null : Number(v));
      const pair = (a: string, b: string) => (a.trim() && b.trim() ? [Number(a), Number(b)] : null);
      setSt(await api.post<TrackingStatus>("/tracking/start", {
        tag_size: Number(form.tagSize),
        ball_profile: ballProfile,
        record: form.record,
        tag_height: opt(form.tagHeight),
        heading_offset: opt(form.headingOffset),
        tag_coast: opt(form.tagCoast),
        tag_sigma_a: opt(form.tagSigmaA),
        tag_sigma_alpha: opt(form.tagSigmaAlpha),
        ball_min_conc: opt(form.ballMinConc),
        ball_radius_mm: opt(form.ballRadiusMm),
        ball_sigma_a: opt(form.ballSigmaA),
        ball_sigma_a_air: opt(form.ballSigmaAAir),
        ball_sigma_px: opt(form.ballSigmaPx),
        ball_airborne: form.ballAirborne,
        corner_origin: form.cornerOrigin,
        field_pose: form.fieldPose || null,
        field: pair(form.fieldW, form.fieldH),
        synthetic_field: form.syntheticField,
        mode: madeUpField ? form.synthMode : null,
        cam_height: madeUpField ? opt(form.camHeight) : null,
        pitch: madeUpField ? opt(form.pitch) : null,
        distance: madeUpField ? opt(form.distance) : null,
        yaw: madeUpField ? opt(form.yaw) : null,
        pitch_wall: madeUpField ? opt(form.pitchWall) : null,
        photon_camera: form.photonCamera.trim() || null,
        nt_server: form.ntServer.trim() || null,
        stream: form.stream.trim() || null,
        photon_res: pair(form.photonW, form.photonH),
        print_poses: form.printPoses,
        print_states: form.printStates,
        json_out: form.jsonOut.trim() || null,
        serve_http: opt(form.serveHttp),
        display_width: opt(form.displayWidth),
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

  async function control(body: { key: string } | { ball_min_conc: number }) {
    try {
      await api.post("/tracking/control", body);
    } catch (e) {
      toasts.error(e);
    }
  }
  const setConc = (v: number) => {
    const c = Math.max(0, Math.min(255, v));
    setConcDraft(c);
    void control({ ball_min_conc: c });
  };

  // The tracker window's keys, on this page.
  const latest = useRef({ controllable, synthetic, keys, setConc });
  latest.current = { controllable, synthetic, keys, setConc };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const l = latest.current;
      if (!l.controllable || e.ctrlKey || e.metaKey || e.altKey) return;
      if ((e.target as HTMLElement).closest("input, select, textarea")) return;
      const k = e.key.toLowerCase();
      if (k === "g" || k === "t" || k === "m" || k === "r" || (k === "w" && l.synthetic)) void control({ key: k });
      else if ((k === "-" || k === "=") && l.keys) l.setConc(l.keys.ball_min_conc + (k === "-" ? -5 : 5));
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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
  const advancedSet = (Object.keys(ADVANCED) as (keyof typeof ADVANCED)[])
    .filter((k) => k !== "synthMode" && form[k] !== ADVANCED[k]).length;

  const runningProfile = profiles.find((p) => p.name === st?.process.options.ball_profile);
  const radiusMm = Number(form.ballRadiusMm) || runningProfile?.radius_mm || 20;
  const scene = live ? sceneFrom(live, radiusMm / 1000) : null;
  const conc = concDraft ?? keys?.ball_min_conc ?? 80;

  return (
    <div className="page">
      <section className="main-panel">
        <div className="viewer-head">
          {live ? (
            <>
              <span className="info"><span className="dot ok" />Tracking</span>
              <span className="info mono">{num(live.stats.fps, 0)} fps</span>
              <span className="info mono" title="From the camera taking the frame to the tracker sending its positions">latency {num(live.stats.latency_ms, 0)} ms</span>
              {live.stats.photon_latency_ms !== undefined && (
                <span className="info mono" title="PhotonVision's own processing time for the newest tag result">PhotonVision {num(live.stats.photon_latency_ms, 0)} ms</span>
              )}
              <span className="info mono" title="Camera frames the tracker was too busy to take / PhotonVision results it skipped for a newer one / ball dropouts so far">
                dropped {live.stats.capture_dropped ?? 0} · skipped {live.stats.photon_skipped ?? 0} · ball lost {live.stats.ball_dropouts ?? 0}
              </span>
              {st?.process.options.recording && <span className="info rec"><span className="dot red" />Recording</span>}
              <span className="push">
                <Segmented<View>
                  options={[{ value: "both", label: "Both" }, { value: "camera", label: "Camera" }, { value: "field", label: "Field" }]}
                  value={view}
                  onChange={setView}
                />
              </span>
            </>
          ) : (
            <span className="info">
              <span className={`dot${running ? " warn pulse" : ""}`} />
              {running ? "Starting the tracker…" : "Not tracking"}
            </span>
          )}
        </div>
        {live && scene ? (
          <div className={`track-views ${view}`}>
            {view !== "field" && (
              <div className="picture">
                <img key={streamKey} src={`/api/v1/tracking/stream.mjpg?k=${streamKey}`} alt="Tracking view" />
              </div>
            )}
            {view !== "camera" && (
              <div className="field-pane">
                <div className="field-wrap">
                  <FieldView world={scene} selected={-1} clickable={false} onHover={setHover} />
                </div>
                <div className="field-foot">
                  <span className="mono">{live.field.width} × {live.field.height} m</span>
                  <span>{live.tags.length} tag{live.tags.length === 1 ? "" : "s"}, grey = predicted</span>
                  <span className="mono push">
                    {hover ? `x ${(hover.x + live.field.x0).toFixed(3)}  y ${(hover.y + live.field.y0).toFixed(3)} m` : "point at the field for coordinates"}
                  </span>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="picture">
            <div className="empty">
              <h3>{running ? "Waiting for the first frames…" : "Track the robots and the ball"}</h3>
              <p className="muted">
                The tracker reads tags from PhotonVision and finds the ball in its camera stream, then publishes
                field positions for the simulator and anything else listening on port 5556. Here you get its camera
                view and the field drawn live.
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
          </div>
        )}
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
                    {profiles.map((p) => <option key={p.name} value={p.name}>{p.name} ({p.radius_mm} mm)</option>)}
                  </select>
                </label>
              </div>
              <Toggle label="Record this session" checked={form.record} onChange={(v) => set("record", v)} hint="Saved to vision/runs/, for replaying in the simulator" />
              <button className="link" onClick={() => setMore(!more)}>
                {more ? "Hide" : "Show"} more options{advancedSet ? ` (${advancedSet} set)` : ""}
              </button>
              {more && (
                <>
                  <div className="opt-group">
                    <h3>Tags</h3>
                    <div className="row">
                      <NumberField label="Tag height (m)" placeholder="0" step={0.005} value={form.tagHeight} onChange={(v) => set("tagHeight", v)} hint="How far the tags sit above the field: 0 lying on it, a robot's shell height for a tag on top of a robot" />
                      <NumberField label="Heading offset (°)" placeholder="0" value={form.headingOffset} onChange={(v) => set("headingOffset", v)} hint="Added to every tag's heading, for a tag not mounted facing forward" />
                    </div>
                    <div className="row">
                      <NumberField label="Keep lost tags (s)" placeholder="0.5" step={0.1} value={form.tagCoast} onChange={(v) => set("tagCoast", v)} hint="How long a tag that dropped out is still reported, on its predicted path" />
                      <NumberField label="Accel. noise (m/s²)" placeholder="8" step={0.5} value={form.tagSigmaA} onChange={(v) => set("tagSigmaA", v)} hint="Kalman filter process noise: raise it for a faster robot, lower it for smoother positions" />
                    </div>
                    <NumberField label="Turn noise (°/s²)" placeholder="180" step={10} value={form.tagSigmaAlpha} onChange={(v) => set("tagSigmaAlpha", v)} hint="Kalman filter process noise for turning" />
                  </div>

                  <div className="opt-group">
                    <h3>Ball</h3>
                    <div className="row">
                      <NumberField label="Min. match (0-255)" placeholder="80" value={form.ballMinConc} onChange={(v) => set("ballMinConc", v)} hint="How well a blob must match the profile to count as the ball. Also adjustable while tracking." />
                      <NumberField label="Radius (mm)" placeholder="from profile" step={0.5} value={form.ballRadiusMm} onChange={(v) => set("ballRadiusMm", v)} />
                    </div>
                    <div className="row">
                      <NumberField label="Accel. noise (m/s²)" placeholder="3" step={0.5} value={form.ballSigmaA} onChange={(v) => set("ballSigmaA", v)} hint="Ball filter process noise while rolling" />
                      <NumberField label="In the air (m/s²)" placeholder="10" step={0.5} value={form.ballSigmaAAir} onChange={(v) => set("ballSigmaAAir", v)} hint="Ball filter process noise while airborne" />
                    </div>
                    <NumberField label="Pixel noise (px)" placeholder="3" step={0.5} value={form.ballSigmaPx} onChange={(v) => set("ballSigmaPx", v)} hint="How far off the ball's centre in the picture is assumed to be" />
                    <Toggle label="Ball can leave the ground" checked={form.ballAirborne} onChange={(v) => set("ballAirborne", v)} hint="Estimate height from the ball's size. Unreliable with the camera far away." />
                  </div>

                  <div className="opt-group">
                    <h3>Field</h3>
                    {(st?.field_poses.length ?? 0) > 1 && (
                      <label className="field">
                        <span>Field calibration</span>
                        <select className="input" value={form.fieldPose} onChange={(e) => set("fieldPose", e.target.value)}>
                          <option value="">field_pose.json (the saved one)</option>
                          {st!.field_poses.filter((p) => p !== "field_pose.json").map((p) => <option key={p} value={p}>{p}</option>)}
                        </select>
                      </label>
                    )}
                    <div className="row">
                      <NumberField label="Field width (m)" placeholder="calibrated" step={0.01} value={form.fieldW} onChange={(v) => set("fieldW", v)} hint="Overrides the size saved with the calibration" />
                      <NumberField label="Field height (m)" placeholder="calibrated" step={0.01} value={form.fieldH} onChange={(v) => set("fieldH", v)} />
                    </div>
                    <Toggle label="Ignore the saved origin" checked={form.cornerOrigin} onChange={(v) => set("cornerOrigin", v)} hint="Report positions from the first field corner" />
                    <Toggle label="Use a made-up field" checked={form.syntheticField} onChange={(v) => set("syntheticField", v)} hint="A camera placed by numbers instead of the calibration: for photon-sim testing" />
                    {madeUpField && (
                      <>
                        {calibSaved === false && !form.syntheticField && <small className="dim">No calibration saved, so the tracker makes one up from these.</small>}
                        <Segmented<"floor" | "wall">
                          options={[{ value: "floor", label: "Camera over a floor" }, { value: "wall", label: "Field on a wall" }]}
                          value={form.synthMode}
                          onChange={(v) => set("synthMode", v)}
                        />
                        {form.synthMode === "floor" ? (
                          <div className="row">
                            <NumberField label="Camera height (m)" placeholder="1.5" step={0.01} value={form.camHeight} onChange={(v) => set("camHeight", v)} />
                            <NumberField label="Tilt (°)" placeholder="90" value={form.pitch} onChange={(v) => set("pitch", v)} hint="Downward from horizontal: 90 is straight down" />
                          </div>
                        ) : (
                          <div className="row">
                            <NumberField label="Distance (m)" placeholder="1.8" step={0.1} value={form.distance} onChange={(v) => set("distance", v)} />
                            <NumberField label="Yaw (°)" placeholder="0" value={form.yaw} onChange={(v) => set("yaw", v)} />
                            <NumberField label="Tip (°)" placeholder="0" value={form.pitchWall} onChange={(v) => set("pitchWall", v)} />
                          </div>
                        )}
                      </>
                    )}
                  </div>

                  <div className="opt-group">
                    <h3>PhotonVision</h3>
                    <label className="field"><span>Camera</span><input className="input" placeholder="the only camera" value={form.photonCamera} onChange={(e) => set("photonCamera", e.target.value)} /></label>
                    <label className="field"><span>NetworkTables server</span><input className="input mono" placeholder="run it here" value={form.ntServer} onChange={(e) => set("ntServer", e.target.value)} /></label>
                    <label className="field"><span>Stream URL</span><input className="input mono" placeholder="PhotonVision's :1181/stream.mjpg" value={form.stream} onChange={(e) => set("stream", e.target.value)} /></label>
                    <div className="row">
                      <NumberField label="Resolution width" placeholder="as published" value={form.photonW} onChange={(v) => set("photonW", v)} hint="Only if PhotonVision doesn't say: the resolution its lens calibration is for" />
                      <NumberField label="Height" placeholder="as published" value={form.photonH} onChange={(v) => set("photonH", v)} />
                    </div>
                  </div>

                  <div className="opt-group">
                    <h3>Output</h3>
                    <Toggle label="Tag positions in the log" checked={form.printPoses} onChange={(v) => set("printPoses", v)} hint="One line per tag per frame (--print-poses)" />
                    <Toggle label="Ball states in the log" checked={form.printStates} onChange={(v) => set("printStates", v)} hint="One line per frame (--print-states)" />
                    <div className="row">
                      <label className="field" title="The newest frame as JSON, rewritten every frame, in vision/runs/ (--json-out)">
                        <span>Live-state file</span>
                        <input className="input mono" placeholder="e.g. live.json" value={form.jsonOut} onChange={(e) => set("jsonOut", e.target.value)} />
                      </label>
                      <NumberField label="HTTP port" placeholder="off" value={form.serveHttp} onChange={(v) => set("serveHttp", v)} hint="Also serve the newest frame at http://this-machine:PORT/state (--serve-http)" />
                    </div>
                    <NumberField label="Camera view width (px)" placeholder="1600" step={100} value={form.displayWidth} onChange={(v) => set("displayWidth", v)} hint="Size of the drawn camera view sent to this page" />
                  </div>
                  <button className="link" onClick={() => setForm((f) => ({ ...f, ...ADVANCED }))}>Reset these to the defaults</button>
                </>
              )}
              <button className="btn primary block" disabled={busy || !ballProfile || !form.tagSize} onClick={start}>Start tracking</button>
              <small className="dim">Stops the Field calibration page's PhotonVision connection, if it has one: the tracker needs it.</small>
            </div>
          )}
        </Card>

        {running && (
          <Card title="While tracking" aside={<span className="muted">keys work too</span>}>
            {controllable && keys ? (
              <div className="stack">
                <Toggle label="Grid" shortcut="G" checked={keys.grid} onChange={() => control({ key: "g" })} hint="The field grid on the camera view" />
                <Toggle label="Trails" shortcut="T" checked={keys.trails} onChange={() => control({ key: "t" })} hint="Where the tags and ball have been, on the camera view's plan" />
                <Toggle label="Ball mask" shortcut="M" checked={keys.mask} onChange={() => control({ key: "m" })} hint="What the ball profile matches, tinted on the camera view" />
                <label className="slider">
                  <span>Ball min. match <b className="mono">{conc.toFixed(0)}</b></span>
                  <input type="range" min={0} max={255} step={1} value={conc}
                    onChange={(e) => setConcDraft(Number(e.target.value))}
                    onPointerUp={() => concDraft !== null && setConc(concDraft)}
                    onKeyUp={() => concDraft !== null && setConc(concDraft)} />
                  <small>Each blob's match is written on the camera view; the ball needs this much. <kbd>-</kbd> <kbd>=</kbd> step by 5.</small>
                </label>
                {synthetic && (
                  <Segmented<"floor" | "wall">
                    options={[{ value: "floor", label: "Floor", shortcut: "W" }, { value: "wall", label: "Wall", shortcut: "W" }]}
                    value={keys.mode}
                    onChange={(v) => v !== keys.mode && control({ key: "w" })}
                  />
                )}
                <button className="btn block" onClick={() => control({ key: "r" })} title="Key R">Reset tracks and trails</button>
              </div>
            ) : (
              <p className="note">Controls appear once the tracker is sending.</p>
            )}
          </Card>
        )}

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
