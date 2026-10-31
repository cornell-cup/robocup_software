import { type Command, type Driver, SPEEDS, setDriver, setSpeed, useDriving } from "../lib/drive";
import { useRobots } from "../lib/robots";
import { Card } from "./Card";
import { Segmented } from "./Segmented";
import "../styles/robots.css";

export interface DriverOption {
  value: Driver;
  label: string;
  title?: string;
}

/** Nobody, the keyboard, and every gamepad the browser shows. Keeps `current`
 *  in the list when its gamepad went away, so it's clear why the robot stopped. */
export function driverOptions(pads: (string | null)[], current: Driver): DriverOption[] {
  const options: DriverOption[] = [
    { value: "", label: "Nobody" },
    { value: "keys", label: "Keyboard", title: "W A S D move, Q E turn, Space dribbler" },
    ...pads.flatMap((id, i) => (id ? [{ value: `pad${i}` as Driver, label: `Gamepad ${i + 1}`, title: id }] : [])),
  ];
  if (!options.some((o) => o.value === current)) {
    options.push({ value: current, label: `Gamepad ${Number(current.slice(3)) + 1} (gone)` });
  }
  return options;
}

export function CommandReadout({ command, driver }: { command: Command | undefined; driver: Driver }) {
  const fmt = (v: number) => (v >= 0 ? "+" : "") + v.toFixed(2);
  return (
    <p className="readout mono">
      {command
        ? <>fwd {fmt(command.forward)}   left {fmt(command.left)}   turn {fmt(command.turn)}{command.dribble ? "   dribbler" : ""}</>
        : <span className="dim">{driver ? "waiting for its gamepad" : "not driven"}</span>}
    </p>
  );
}

/** Drive the real robots by hand from any page: who drives each one, and the
 *  keyboard speed. Adding robots and following the sim are on the Robots page. */
export function DriveCard() {
  const { robots } = useRobots();
  const { drivers, speed, pads, sent } = useDriving();
  const byHand = robots.filter((r) => r.follow === null);

  return (
    <Card title="Drive robots" aside={<a className="muted" href="#/robots">Robots page</a>}>
      <div className="stack drive-card">
        {robots.length === 0 ? (
          <p className="note">No robots connected. Add them on the <a href="#/robots">Robots</a> page.</p>
        ) : (
          <>
            {robots.map((r) => {
              const d = drivers[r.name] ?? "";
              return (
                <div key={r.name} className="drive-robot">
                  <div className="drive-robot-head">
                    <span className={`dot${r.connected ? " ok" : r.error ? " red" : " warn pulse"}`} />
                    <span className="mono">{r.name}</span>
                    {r.follow !== null && <span className="muted">follows sim robot {r.follow}</span>}
                  </div>
                  {r.follow === null && (
                    <>
                      <Segmented options={driverOptions(pads, d)} value={d} onChange={(v) => setDriver(r.name, v)} />
                      <CommandReadout command={sent[r.name]} driver={d} />
                    </>
                  )}
                </div>
              );
            })}
            {byHand.length > 0 && (
              <>
                <Segmented options={SPEEDS} value={speed} onChange={setSpeed} />
                <p className="keys">
                  Keyboard: W A S D move, Q E turn, Space dribbler, 1–3 speed. Gamepad: left stick moves,
                  right stick turns, A dribbler; press a button on one to show it.
                </p>
              </>
            )}
          </>
        )}
      </div>
    </Card>
  );
}
