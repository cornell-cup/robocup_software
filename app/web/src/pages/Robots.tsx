import { useState } from "react";
import { Card } from "../components/Card";
import { CommandReadout, driverOptions } from "../components/DriveCard";
import { Segmented } from "../components/Segmented";
import { api } from "../lib/api";
import { type Command, type Driver, SPEEDS, setDriver, setSpeed, useDriving } from "../lib/drive";
import { setRobots, useRobots } from "../lib/robots";
import { useSim } from "../lib/sim";
import { toasts } from "../lib/toast";
import type { RealRobot, RealRobotsStatus } from "../lib/types";
import { useSavedState } from "../lib/useSavedState";
import "../styles/robots.css";

// Robots advertise only the first 8 characters of their name.
const NAME_LENGTH = 8;

export function Robots() {
  const { connected, robots, simUp } = useRobots();
  const { drivers, speed, pads, sent } = useDriving();
  const sim = useSim();
  const numSim = sim.world?.field?.num_robots ?? 6;
  const blueSize = sim.world?.field?.team_blue_size ?? 3;

  const [recent, setRecent] = useSavedState<string[]>("robots.recent", []);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [found, setFound] = useState<{ name: string; rssi: number }[] | null>(null);
  const [scanning, setScanning] = useState(false);

  async function call(path: string, body: unknown) {
    setBusy(true);
    try {
      const st = await api.post<RealRobotsStatus>(path, body);
      setRobots(st.robots, st.sim_up);
      return true;
    } catch (e) {
      toasts.error(e);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function add(n: string) {
    n = n.trim();
    if (!n) return;
    if (await call("/robots/add", { name: n })) {
      setName("");
      setRecent((r) => [n, ...r.filter((x) => x !== n)].slice(0, 8));
    }
  }

  async function remove(n: string) {
    if (await call("/robots/remove", { name: n })) setDriver(n, "");
  }

  async function follow(n: string, simRobot: number | null) {
    if (await call("/robots/follow", { name: n, sim_robot: simRobot })) {
      if (simRobot !== null) setDriver(n, "");
    }
  }

  async function scan() {
    setScanning(true);
    try {
      setFound((await api.post<{ devices: { name: string; rssi: number }[] }>("/robots/scan")).devices);
    } catch (e) {
      toasts.error(e);
    } finally {
      setScanning(false);
    }
  }

  const notAdded = recent.filter((n) => !robots.some((r) => r.name === n));
  const named = found?.filter((d) => d.name !== "(no name)") ?? [];

  return (
    <div className="page robots-page">
      <section className="main-panel">
        <header className="robots-head">
          <h1>Robots</h1>
          <span className="muted">
            {!connected ? "Can't reach the app server"
              : robots.length === 0 ? "None added yet"
              : `${robots.filter((r) => r.connected).length} of ${robots.length} connected`}
          </span>
        </header>
        <div className="robot-list">
          {robots.length === 0 ? (
            <div className="robots-empty">
              <h3>No robots yet</h3>
              <p className="muted">
                Switch an XRP on (running <code>robot/firmware/robot.py</code>) and add it by its{" "}
                <code>ROBOT_NAME</code>, or scan for it. This computer needs Bluetooth on.
              </p>
            </div>
          ) : (
            robots.map((r) => (
              <RobotRow
                key={r.name}
                robot={r}
                driver={drivers[r.name] ?? ""}
                pads={pads}
                command={sent[r.name]}
                simUp={simUp}
                numSim={numSim}
                blueSize={blueSize}
                taken={new Set(robots.filter((o) => o.name !== r.name && o.follow !== null).map((o) => o.follow!))}
                busy={busy}
                onFollow={(rid) => follow(r.name, rid)}
                onRemove={() => remove(r.name)}
              />
            ))
          )}
        </div>
      </section>

      <aside className="side">
        <Card title="Add a robot">
          <form className="stack" onSubmit={(e) => { e.preventDefault(); add(name); }}>
            <div className="row">
              <input className="input mono" style={{ flex: 3 }} placeholder="XRP-01" maxLength={NAME_LENGTH} value={name} onChange={(e) => setName(e.target.value)} />
              <button className="btn primary" type="submit" disabled={busy || !name.trim()}>Add</button>
            </div>
            {notAdded.length > 0 && (
              <div className="name-chips">
                {notAdded.map((n) => (
                  <button key={n} type="button" className="btn sm" disabled={busy} onClick={() => add(n)}>+ {n}</button>
                ))}
              </div>
            )}
            <button type="button" className="btn block" disabled={scanning} onClick={scan}>
              {scanning ? "Scanning…" : "Scan for robots nearby"}
            </button>
            {found && (
              named.length === 0 ? (
                <p className="note">Nothing with a name nearby. Is the robot on?</p>
              ) : (
                <ul className="found">
                  {named.map((d) => (
                    <li key={d.name}>
                      <span className="mono">{d.name}</span>
                      <span className="dim mono">{d.rssi} dBm</span>
                      <button
                        type="button"
                        className="btn sm"
                        disabled={busy || d.name.length > NAME_LENGTH || robots.some((r) => r.name === d.name)}
                        onClick={() => add(d.name)}
                      >
                        {robots.some((r) => r.name === d.name) ? "Added" : "Add"}
                      </button>
                    </li>
                  ))}
                </ul>
              )
            )}
            <small className="dim">
              Only one program can hold a robot's link: quit <code>python -m laptop</code> for a robot before adding it here.
            </small>
          </form>
        </Card>

        <Card title="Driving by hand">
          <div className="stack">
            <label className="field">
              <span>Keyboard speed</span>
              <Segmented options={SPEEDS} value={speed} onChange={setSpeed} />
            </label>
            <p className="keys">Keyboard: W A S D move, Q E turn, Space dribbler, 1–3 speed.</p>
            <p className="keys">
              Gamepad: left stick moves, right stick turns, A (Cross) runs the dribbler.{" "}
              {pads.some(Boolean) ? `${pads.filter(Boolean).length} connected.` : "None yet: press a button on one to show it here."}
            </p>
            <p className="note">
              The <a href="#/tracking">Tracking</a> page has a Drive card too, to drive while you watch the camera.
              Each robot stops by itself a quarter second after its driver goes quiet: a closed tab, a lost gamepad.
            </p>
          </div>
        </Card>

        <Card title="Following the sim" aside={simUp ? <><span className="dot ok" />Sim sending</> : <><span className="dot" />No sim</>}>
          <div className="stack">
            <p className="note">
              A following robot takes the commands the simulator computes for its sim robot: strategy, or a click or
              W A S D on the <a href="#/sim">Simulator</a> page.
            </p>
            <p className="note">
              It only moves while the camera sees it, so start the sim with robots from the <b>Live camera</b>, and give its
              tag to the same sim robot (<i>Tag → robot</i>). The tag's heading must point at the dribbler: set the
              tracker's heading offset if it doesn't.
            </p>
          </div>
        </Card>
      </aside>
    </div>
  );
}

function RobotRow({
  robot: r, driver, pads, command, simUp, numSim, blueSize, taken, busy, onFollow, onRemove,
}: {
  robot: RealRobot;
  driver: Driver;
  pads: (string | null)[];
  command: Command | undefined;
  simUp: boolean;
  numSim: number;
  blueSize: number;
  taken: Set<number>;
  busy: boolean;
  onFollow: (rid: number | null) => void;
  onRemove: () => void;
}) {
  const following = r.follow !== null;
  const link = r.connected ? "Connected"
    : r.state === "connecting" ? "Connecting…"
    : r.state === "retrying" ? "Not connected, retrying"
    : "Searching…";

  return (
    <article className={`robot-row${r.moving ? " moving" : ""}`}>
      <header>
        <span className={`dot${r.connected ? " ok" : r.error ? " red" : " warn pulse"}`} />
        <h2 className="mono">{r.name}</h2>
        <span className="muted">{link}</span>
        <button className="btn sm ghost" disabled={busy} onClick={onRemove} title="Stop it and disconnect">Remove</button>
      </header>
      {r.error && !r.connected && <p className="note error">{r.error}</p>}

      <Segmented
        options={[
          { value: "hand", label: "By hand", title: "Keyboard or gamepad on this page" },
          { value: "sim", label: "Follow sim", title: "Take the simulator's commands for one sim robot" },
        ]}
        value={following ? "sim" : "hand"}
        onChange={(v) => onFollow(v === "sim" ? (Array.from({ length: numSim }, (_, i) => i).find((i) => !taken.has(i)) ?? 0) : null)}
      />

      {following ? (
        <>
          <div className="sim-chips">
            {Array.from({ length: numSim }, (_, i) => (
              <button
                key={i}
                className={`sim-chip ${i < blueSize ? "blue" : "red"}${i === r.follow ? " on" : ""}`}
                disabled={busy || taken.has(i)}
                onClick={() => onFollow(i)}
                title={taken.has(i) ? "Another robot follows it" : `Follow sim robot ${i}`}
              >
                {i}
              </button>
            ))}
          </div>
          <p className="follow-state">
            {!simUp ? <><span className="dot" />Stopped: the simulator isn't running</>
              : r.tracked ? <><span className="dot ok" />Following sim robot {r.follow}: the camera sees it</>
              : <><span className="dot warn" />Stopped: the camera doesn't see sim robot {r.follow}</>}
          </p>
        </>
      ) : (
        <>
          <label className="field">
            <span>Driven by</span>
            <Segmented options={driverOptions(pads, driver)} value={driver} onChange={(d) => setDriver(r.name, d)} />
          </label>
          <CommandReadout command={command} driver={driver} />
        </>
      )}
    </article>
  );
}
