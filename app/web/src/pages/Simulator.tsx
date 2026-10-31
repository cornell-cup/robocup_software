import { useEffect, useRef, useState } from "react";
import { Card } from "../components/Card";
import { FieldView } from "../components/FieldView";
import { Segmented } from "../components/Segmented";
import { Toggle } from "../components/Toggle";
import { api } from "../lib/api";
import { useRobots } from "../lib/robots";
import { sendSim, useSim } from "../lib/sim";
import { usePolling } from "../lib/store";
import { toasts } from "../lib/toast";
import type { Mode, Pin, Recording, SimProcessStatus, TrackingStatus } from "../lib/types";
import { useSavedState } from "../lib/useSavedState";
import "../styles/simulator.css";

type DriveMode = Mode | "keys";
const MAX_OMEGA = 5;
// The pygame window's controller colours, for the target pin.
const MODE_COLORS: Record<Mode, string> = { pd: "#00ff80", time: "#dc50ff", mpc: "#00c8ff" };

export function Simulator() {
  const sim = useSim();
  const { robots: realRobots } = useRobots();
  const { world, controls } = sim;
  const field = world?.field;
  const game = world?.game;
  const numRobots = field?.num_robots ?? 6;
  const blueSize = field?.team_blue_size ?? 3;

  const [mode, setMode] = useState<DriveMode>("pd");
  const [robot, setRobot] = useState(0);
  const [pin, setPin] = useState<Pin | null>(null);
  const [hover, setHover] = useState<{ x: number; y: number } | null>(null);
  const [proc, setProc] = useState<SimProcessStatus | null>(null);
  const [log, setLog] = useState<{ n: number; text: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<{ team: string; score: string } | null>(null);
  const [popup, setPopup] = useState<string | null>(null);

  // start options, remembered between visits
  const [opts, setOpts] = useSavedState("sim.options", {
    strategy: true, color: "blue" as "blue" | "red", customField: false, fieldW: 9, fieldH: 6,
    // where the robots come from: the simulation, the live tracker, or a recording
    source: "none" as "none" | "tracker" | "recording", recording: "", replaySpeed: 1,
    calibField: true, ignoreTags: "0,1,2,3", tagMap: "", flipX: false, flipY: false,
  });
  const [recs, setRecs] = useState<Recording[]>([]);
  const [trackerLive, setTrackerLive] = useState<boolean | null>(null);
  usePolling(async () => {
    try {
      const [r, t] = await Promise.all([
        api.get<{ recordings: Recording[] }>("/recordings"),
        api.get<TrackingStatus>("/tracking/status"),
      ]);
      setRecs(r.recordings);
      setTrackerLive(t.live.up);
    } catch {
      /* server restarting */
    }
  }, 3000);
  const camera = opts.source !== "none";
  const setOpt = <K extends keyof typeof opts>(k: K, v: (typeof opts)[K]) => setOpts((o) => ({ ...o, [k]: v }));

  const running = !!proc?.running;
  const external = sim.up && !running;

  // ── process and log ────────────────────────────────────────────────────

  const logAfter = useRef(0);
  const logBox = useRef<HTMLDivElement>(null);
  usePolling(async () => {
    try {
      const st = await api.get<{ process: SimProcessStatus }>("/sim/status");
      setProc(st.process);
      const l = await api.get<{ lines: { n: number; text: string }[] }>(`/sim/log?after=${logAfter.current}`);
      if (l.lines.length) {
        logAfter.current = l.lines[l.lines.length - 1].n;
        setLog((old) => [...old, ...l.lines].slice(-300));
      }
    } catch {
      /* server restarting; the socket shows it */
    }
  }, 1000);
  useEffect(() => {
    const b = logBox.current;
    if (b && b.scrollHeight - b.scrollTop - b.clientHeight < 60) b.scrollTop = b.scrollHeight;
  }, [log]);

  async function start() {
    setBusy(true);
    try {
      const recording = opts.recording || recs[0]?.path || "";
      setProc(await api.post<SimProcessStatus>("/sim/start", {
        strategy: opts.strategy,
        color: opts.color,
        field_from_calibration: camera && opts.calibField,
        field: !(camera && opts.calibField) && opts.customField ? [opts.fieldW, opts.fieldH] : null,
        vision: opts.source === "tracker" ? "tracker" : opts.source === "recording" ? recording : null,
        ignore_tags: camera ? opts.ignoreTags : null,
        tag_map: camera ? opts.tagMap : null,
        flip_x: camera && opts.flipX,
        flip_y: camera && opts.flipY,
        replay_speed: opts.source === "recording" ? opts.replaySpeed : null,
      }));
      toasts.push("info", "Starting the simulator…");
    } catch (e) {
      toasts.error(e);
    } finally {
      setBusy(false);
    }
  }

  async function stop() {
    setBusy(true);
    try {
      setProc(await api.post<SimProcessStatus>("/sim/stop"));
      setPin(null);
    } catch (e) {
      toasts.error(e);
    } finally {
      setBusy(false);
    }
  }

  // ── commands ───────────────────────────────────────────────────────────

  function pick(x: number, y: number) {
    if (mode === "keys") return;
    const r = world?.robots?.[String(robot)];
    setPin({ robot, x, y, fromX: r?.x ?? null, fromY: r?.y ?? null, color: MODE_COLORS[mode] });
    sendSim({ type: "target", robot, x, y, mode });
  }
  const kick = () => sendSim({ type: "kick", robot });
  const setStrategy = (v: boolean) => sendSim({ type: "strategy", enabled: v });
  const setDribble = (v: boolean) => sendSim({ type: "dribble", enabled: v });
  const setRl = (v: boolean) => sendSim({ type: "rl_kick", enabled: v });
  const setBallReset = (v: boolean) => sendSim({ type: "ball_stuck_reset", enabled: v });

  // clear the pin once the robot gets there
  useEffect(() => {
    if (!pin || !world) return;
    const r = world.robots?.[String(pin.robot)];
    if (r && Math.hypot(r.x - pin.x, r.y - pin.y) < 0.05 * Math.max(1, (field?.width ?? 9) / 9)) setPin(null);
  }, [world, pin, field]);

  // goal flash and phase changes, as the pygame window showed them
  const goalSeq = useRef(-1);
  const phase = useRef("");
  useEffect(() => {
    const g = world?.last_goal;
    if (g && g.seq > goalSeq.current) {
      if (goalSeq.current !== -1) {
        setFlash({ team: g.team === "red" ? "blue" : "red", score: `${g.score.blue} – ${g.score.red}` });
        setTimeout(() => setFlash(null), 2200);
      }
      goalSeq.current = g.seq;
    } else if (!g && goalSeq.current === -1) {
      goalSeq.current = 0;
    }
    const p = world?.game?.phase;
    if (p && p !== phase.current) {
      if (phase.current) {
        setPopup(p);
        setTimeout(() => setPopup(null), 2600);
      }
      phase.current = p;
    }
  }, [world]);

  // ── keyboard ───────────────────────────────────────────────────────────

  // The listeners are attached once; they read the latest values from here.
  const live = useRef({ mode, robot, sim, game });
  live.current = { mode, robot, sim, game };
  const held = useRef(new Set<string>());

  useEffect(() => {
    const onDown = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement | null)?.closest("input, textarea, select")) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const k = e.key.toLowerCase();
      const { mode, robot, sim, game } = live.current;
      if (mode === "keys" && k.length === 1 && "wasdqe".includes(k)) {
        held.current.add(k);
        e.preventDefault();
        return;
      }
      if (e.repeat) return;
      const actions: Record<string, () => void> = {
        "1": () => setMode("pd"),
        "2": () => setMode("time"),
        "3": () => setMode("mpc"),
        "4": () => setMode("keys"),
        "5": () => sendSim({ type: "strategy", enabled: !sim.controls.strategy }),
        "0": () => sendSim({ type: "dribble", enabled: !sim.controls.dribble }),
        "9": () => sendSim({ type: "ball_stuck_reset", enabled: !game?.ball_stuck_enabled }),
        r: () => sendSim({ type: "rl_kick", enabled: !sim.controls.rl_kick }),
        k: () => sendSim({ type: "kick", robot }),
        enter: () => sendSim({ type: "kick", robot }),
      };
      const act = actions[k];
      if (act) {
        act();
        e.preventDefault();
      }
    };
    const onUp = (e: KeyboardEvent) => held.current.delete(e.key.toLowerCase());
    const onBlur = () => held.current.clear();
    const drive = setInterval(() => {
      const { mode, robot, sim } = live.current;
      if (mode !== "keys" || !sim.up) return;
      const h = (c: string) => (held.current.has(c) ? 1 : 0);
      sendSim({ type: "drive", robot, vx: h("d") - h("a"), vy: h("w") - h("s"), w: (h("q") - h("e")) * MAX_OMEGA });
    }, 50);
    window.addEventListener("keydown", onDown);
    window.addEventListener("keyup", onUp);
    window.addEventListener("blur", onBlur);
    return () => {
      clearInterval(drive);
      window.removeEventListener("keydown", onDown);
      window.removeEventListener("keyup", onUp);
      window.removeEventListener("blur", onBlur);
    };
  }, []);

  // leaving keyboard driving (or switching robot) stops the robot it drove
  const prev = useRef({ mode, robot });
  useEffect(() => {
    const p = prev.current;
    if (p.mode === "keys" && (mode !== "keys" || robot !== p.robot)) {
      sendSim({ type: "drive", robot: p.robot, vx: 0, vy: 0, w: 0 });
    }
    if (mode === "keys") setPin(null);
    prev.current = { mode, robot };
  }, [mode, robot]);

  const clock = (s: number | undefined) => {
    if (s === undefined) return "--:--";
    const t = Math.max(0, Math.ceil(s));
    return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
  };
  const fmt = (v: unknown, d = 0) => (typeof v === "number" ? v.toFixed(d) : "–");
  const vision = world?.vision;

  return (
    <div className="page sim-page">
      <section className="main-panel">
        <div className="scoreboard">
          <div className="team blue">
            <span className="name">Blue</span>
            <span className="score mono">{world?.score?.blue ?? 0}</span>
          </div>
          <div className="clock">
            <span className="time mono">{clock(game?.time_remaining)}</span>
            <span className="phase">{game ? game.phase.toLowerCase() : "not running"}</span>
          </div>
          <div className="team red">
            <span className="score mono">{world?.score?.red ?? 0}</span>
            <span className="name">Red</span>
          </div>
        </div>

        <div className="field-wrap">
          <FieldView
            world={world}
            attackers={sim.attackers}
            selected={robot}
            pin={pin}
            clickable={mode !== "keys" && sim.up}
            onPick={pick}
            onHover={setHover}
          />
          {!sim.up && (
            <div className="veil">
              <div className="veil-card">
                <h3>{running ? "Starting the simulator…" : "The simulator isn't running"}</h3>
                <p className="muted">
                  {running ? (
                    "Physics, strategy and robot control take a few seconds."
                  ) : (
                    <>Start it here, or run <code>run_pipeline.py --no-viz</code> in a terminal.</>
                  )}
                </p>
                {!running && (
                  <button className="btn primary" disabled={busy} onClick={start}>Start simulator</button>
                )}
              </div>
            </div>
          )}
          {flash && (
            <div className={`flash ${flash.team}`}>
              <span className="goal">Goal</span>
              <span className="mono">{flash.score}</span>
            </div>
          )}
          {popup && <div className="popup">{popup}</div>}
          {game?.winner && (
            <div className="winner">
              <span className="label">{game.winner === "draw" ? "Draw" : `${game.winner} wins`}</span>
              <span className="mono">{world?.score.blue} – {world?.score.red}</span>
            </div>
          )}
        </div>

        <footer className="stage-foot">
          <span>
            {mode === "keys" ? `W A S D moves robot ${robot}, Q and E rotate it` : `Click the field to send robot ${robot} there`}
          </span>
          <span className="mono">
            {hover
              ? `x ${hover.x.toFixed(2)} m   y ${hover.y.toFixed(2)} m`
              : field ? `${field.width} × ${field.height} m` : ""}
          </span>
        </footer>
      </section>

      <aside className="side">
        <Card
          title="Simulation"
          aside={
            sim.up ? <><span className="dot ok" />{external ? "Running (external)" : "Running"}</>
              : running ? <><span className="dot warn pulse" />Starting</>
              : <><span className="dot" />Stopped</>
          }
        >
          <div className="stack">
            {running || sim.up ? (
              <>
                {proc?.options && running && (
                  <dl className="kv">
                    <dt>Strategy</dt><dd>{proc.options.strategy ? proc.options.color : "off"}</dd>
                    <dt>Field</dt><dd>{field ? `${field.width} × ${field.height} m` : "–"}</dd>
                    {proc.options.vision && (
                      <><dt>Robots from</dt><dd>{proc.options.vision.startsWith("tcp://localhost:5556") ? "live camera" : proc.options.vision.split(/[\\/]/).pop()}</dd></>
                    )}
                  </dl>
                )}
                {running ? (
                  <button className="btn danger block" disabled={busy} onClick={stop}>Stop simulator</button>
                ) : (
                  <p className="note">Started outside this app. Stop it where it was started.</p>
                )}
              </>
            ) : (
              <>
                <Toggle label="Strategy node" checked={opts.strategy} onChange={(v) => setOpt("strategy", v)} hint="AI for the robots nobody drives" />
                {opts.strategy && (
                  <Segmented
                    options={[{ value: "blue", label: "AI plays blue" }, { value: "red", label: "AI plays red" }]}
                    value={opts.color}
                    onChange={(v) => setOpt("color", v)}
                  />
                )}
                <label className="field">
                  <span>Robots and ball</span>
                  <Segmented
                    options={[
                      { value: "none", label: "Simulated" },
                      { value: "tracker", label: "Live camera" },
                      { value: "recording", label: "Recording" },
                    ]}
                    value={opts.source}
                    onChange={(v) => setOpt("source", v)}
                  />
                </label>
                {opts.source === "tracker" && (
                  <p className={`note${trackerLive === false ? " error" : ""}`}>
                    {trackerLive ? "The tracker is running; tagged robots and the ball will follow the camera."
                      : <>The tracker isn't running. Start it on the <a href="#/tracking">Tracking</a> page.</>}
                  </p>
                )}
                {opts.source === "recording" && (
                  <div className="row">
                    <label className="field" style={{ flex: 3 }}>
                      <span>Session</span>
                      <select className="input" value={opts.recording || recs[0]?.path || ""} onChange={(e) => setOpt("recording", e.target.value)}>
                        {recs.length === 0 && <option value="">no recordings yet</option>}
                        {recs.map((r) => (
                          <option key={r.path} value={r.path}>{r.name} ({r.size_kb} KB)</option>
                        ))}
                      </select>
                    </label>
                    <label className="field">
                      <span>Speed</span>
                      <input className="input mono" type="number" step="0.25" min="0.05" value={opts.replaySpeed} onChange={(e) => setOpt("replaySpeed", Number(e.target.value))} />
                    </label>
                  </div>
                )}
                {camera && (
                  <>
                    <Toggle label="Field size from the calibration" checked={opts.calibField} onChange={(v) => setOpt("calibField", v)} hint="The sim field matches the real one, 1:1" />
                    <div className="row">
                      <label className="field">
                        <span>Tags to ignore</span>
                        <input className="input mono" placeholder="0,1,2,3" value={opts.ignoreTags} onChange={(e) => setOpt("ignoreTags", e.target.value)} />
                      </label>
                      <label className="field">
                        <span>Tag → robot</span>
                        <input className="input mono" placeholder="auto, e.g. 9:0" value={opts.tagMap} onChange={(e) => setOpt("tagMap", e.target.value)} />
                      </label>
                    </div>
                    <small className="dim">Ignore the field's corner tags so they don't become robots.</small>
                    <div className="row">
                      <Toggle label="Flip X" checked={opts.flipX} onChange={(v) => setOpt("flipX", v)} hint="If robots move opposite to their tags" />
                      <Toggle label="Flip Y" checked={opts.flipY} onChange={(v) => setOpt("flipY", v)} />
                    </div>
                  </>
                )}
                {!(camera && opts.calibField) && (
                  <>
                    <Toggle label="Custom field size" checked={opts.customField} onChange={(v) => setOpt("customField", v)} hint="Default is 9 × 6 m" />
                    {opts.customField && (
                      <div className="row">
                        <label className="field">
                          <span>Width (m)</span>
                          <input className="input mono" type="number" step="0.01" min="0.1" value={opts.fieldW} onChange={(e) => setOpt("fieldW", Number(e.target.value))} />
                        </label>
                        <label className="field">
                          <span>Height (m)</span>
                          <input className="input mono" type="number" step="0.01" min="0.1" value={opts.fieldH} onChange={(e) => setOpt("fieldH", Number(e.target.value))} />
                        </label>
                      </div>
                    )}
                  </>
                )}
                <button className="btn primary block" disabled={busy} onClick={start}>Start simulator</button>
              </>
            )}
            {sim.manualError && <p className="note error">{sim.manualError}</p>}
          </div>
        </Card>

        <Card title="Drive" aside={<span className="muted">robot {robot}</span>}>
          <div className="stack">
            <div className="robots">
              {Array.from({ length: numRobots }, (_, i) => (
                <button
                  key={i}
                  className={`chip ${i < blueSize ? "blue" : "red"}${i === robot ? " on" : ""}`}
                  onClick={() => setRobot(i)}
                  title={i < blueSize ? `Blue ${i}` : `Red ${i}`}
                >
                  {i}
                </button>
              ))}
            </div>
            <Segmented
              options={[
                { value: "pd", label: "PD", shortcut: "1", title: "PD with dynamic inversion" },
                { value: "time", label: "Time", shortcut: "2", title: "Time-optimal (bang-bang)" },
                { value: "mpc", label: "MPC", shortcut: "3", title: "Greedy MPC" },
                { value: "keys", label: "Keys", shortcut: "4", title: "Drive with W A S D, rotate with Q E" },
              ]}
              value={mode}
              onChange={setMode}
            />
            {world?.robots?.[String(robot)]?.source === "vision" && (() => {
              const follower = realRobots.find((r) => r.follow === robot);
              return (
                <p className="note">
                  Robot {robot} is the camera's, so the sim won't move it. {follower
                    ? <>Keys and clicks drive <b>{follower.name}</b>, which follows it{follower.tracked ? "" : ", once the camera sees it"}.</>
                    : <>Keys and clicks move it only through a real robot set to <i>Follow sim</i> → {robot} on the <a href="#/robots">Robots</a> page.</>}
                  {" "}In Keys, W moves it up the field, whichever way it faces.
                </p>
              );
            })()}
            <button className="btn block" disabled={!sim.up} onClick={kick} title="Key K or Enter">Kick</button>
            <p className="keys">Keys: 1–4 controller, 5 strategy, 0 dribble, 9 ball reset, R RL kick, K kick.</p>
          </div>
        </Card>

        <Card title="Game">
          <div className="toggles">
            <Toggle label="Strategy (AI)" shortcut="5" checked={controls.strategy} onChange={setStrategy} disabled={!sim.up} />
            <Toggle label="Attacker dribbles" shortcut="0" checked={controls.dribble} onChange={setDribble} disabled={!sim.up} />
            <Toggle label="Reset a stuck ball" shortcut="9" checked={!!game?.ball_stuck_enabled} onChange={setBallReset} disabled={!sim.up} />
            <Toggle
              label="RL kick"
              shortcut="R"
              checked={controls.rl_kick}
              onChange={setRl}
              disabled={!sim.up || (field ? !field.is_default : false)}
              hint={field && !field.is_default ? "Trained on 9 × 6 only" : "Learned kicking policy"}
            />
          </div>
        </Card>

        {vision && (
          <Card
            title="Vision"
            aside={<><span className={`dot ${vision.link_up ? "ok" : "red"}`} />{vision.link_up ? "Link up" : "Link down"}</>}
          >
            <dl className="kv">
              <dt>Source</dt><dd>{vision.source ?? "–"}</dd>
              <dt>Rate</dt><dd>{fmt(vision.fps)} fps</dd>
              <dt>Latency</dt><dd>{fmt(vision.latency_ms)} ms</dd>
              <dt>Link drops</dt><dd>{vision.transport_drops ?? 0}</dd>
              <dt>Rejected</dt><dd>{vision.rejects ?? 0}</dd>
              <dt>Predicted</dt><dd>{vision.predicted ?? 0}</dd>
            </dl>
          </Card>
        )}

        <Card title="Log">
          <div className="log mono" ref={logBox}>
            {log.length ? (
              log.map((l) => (
                <div key={l.n} className={/error|traceback|exception/i.test(l.text) ? "err" : undefined}>{l.text}</div>
              ))
            ) : (
              <div className="dim">Nothing yet.</div>
            )}
          </div>
        </Card>
      </aside>
    </div>
  );
}
