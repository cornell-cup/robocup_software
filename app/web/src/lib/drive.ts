// Driving the real robots by hand: the keyboard and gamepads, read 20 times a
// second and sent to whichever robot each one drives. Lives outside any page
// so the Robots page and the Drive card on other pages (Tracking) share it;
// it runs while any of them is open, and the robots stop when none is.

import { useEffect } from "react";
import { sendDrive, useRobots, robotsNow } from "./robots";
import { createStore, refCounted, useStore } from "./store";
import { readSaved, writeSaved } from "./useSavedState";

/** Who drives a robot: nobody, the keyboard, or gamepad N ("pad0"). */
export type Driver = "" | "keys" | `pad${number}`;
export interface Command {
  forward: number;
  left: number;
  turn: number;
  dribble: boolean;
}

export const SPEEDS = [
  { value: "0.4", label: "Slow", shortcut: "1" },
  { value: "0.7", label: "Medium", shortcut: "2" },
  { value: "1", label: "Fast", shortcut: "3" },
];
// As robot/laptop/gamepad.py: stick drift below this counts as 0.
const DEADZONE = 0.1;
const DRIVE_KEYS = new Set(["w", "a", "s", "d", "q", "e", " ", "arrowup", "arrowdown", "arrowleft", "arrowright"]);

export interface DriveState {
  drivers: Record<string, Driver>;
  speed: string;
  /** connected gamepads by index, null where there is none */
  pads: (string | null)[];
  /** what each robot was last sent */
  sent: Record<string, Command>;
}

const store = createStore<DriveState>({
  drivers: readSaved<Record<string, Driver>>("robots.drivers") ?? {},
  speed: readSaved<string>("robots.speed") ?? "0.7",
  pads: [],
  sent: {},
});

const STOP = { forward: 0, left: 0, turn: 0, dribble: false };

/** One input drives one robot: giving it to this robot takes it from any other,
 *  and whoever loses its driver stops now, not when the server's timeout notices. */
export function setDriver(name: string, d: Driver) {
  const old = store.get().drivers;
  const next: Record<string, Driver> = {};
  for (const [k, v] of Object.entries(old)) next[k] = d && v === d ? "" : v;
  next[name] = d;
  for (const [k, v] of Object.entries(old)) if (v && v !== next[k]) sendDrive({ name: k, ...STOP });
  writeSaved("robots.drivers", next);
  store.set((s) => ({ ...s, drivers: next }));
}

export function setSpeed(speed: string) {
  writeSaved("robots.speed", speed);
  store.set((s) => ({ ...s, speed }));
}

function fromKeys(held: Set<string>, speed: number): Command {
  const h = (...keys: string[]) => (keys.some((k) => held.has(k)) ? 1 : 0);
  return {
    forward: (h("w", "arrowup") - h("s", "arrowdown")) * speed,
    left: (h("a", "arrowleft") - h("d", "arrowright")) * speed,
    turn: (h("q") - h("e")) * speed,
    dribble: held.has(" "),
  };
}

/** The browser's standard gamepad mapping: left stick moves (cubed, for fine
 *  slow driving), right stick X turns, A (Cross) runs the dribbler. */
function fromPad(pad: Gamepad): Command {
  const dz = (v: number | undefined) => (v === undefined || Math.abs(v) < DEADZONE ? 0 : v);
  return {
    forward: -(dz(pad.axes[1]) ** 3),
    left: -(dz(pad.axes[0]) ** 3),
    turn: -dz(pad.axes[2]),
    dribble: !!pad.buttons[0]?.pressed,
  };
}

/** Whether the keyboard drives a robot that is driven by hand right now. */
function keyboardDriving(): boolean {
  const { drivers } = store.get();
  return robotsNow().some((r) => r.follow === null && drivers[r.name] === "keys");
}

const acquire = refCounted(() => {
  const held = new Set<string>();
  // Capture phase, ahead of the pages' own shortcuts: while the keyboard
  // drives a robot, its keys belong to the robot (W on the Tracking page
  // would otherwise also flip floor / wall).
  const onDown = (e: KeyboardEvent) => {
    if ((e.target as HTMLElement | null)?.closest("input, textarea, select")) return;
    if (e.ctrlKey || e.metaKey || e.altKey || !keyboardDriving()) return;
    const k = e.key.toLowerCase();
    const pick = SPEEDS.find((s) => s.shortcut === k);
    if (pick) {
      if (!e.repeat) setSpeed(pick.value);
    } else if (DRIVE_KEYS.has(k)) {
      held.add(k);
    } else {
      return;
    }
    e.preventDefault(); // Space would press the focused button, arrows scroll
    e.stopImmediatePropagation();
  };
  const onUp = (e: KeyboardEvent) => held.delete(e.key.toLowerCase());
  const onBlur = () => held.clear();

  const tick = setInterval(() => {
    const { drivers, speed, sent, pads } = store.get();
    const gamepads = navigator.getGamepads?.() ?? [];
    const out: Record<string, Command> = {};
    for (const r of robotsNow()) {
      const d = drivers[r.name];
      if (r.follow !== null || !d) continue;
      const pad = d === "keys" ? null : gamepads[Number(d.slice(3))];
      // A gamepad that went away sends nothing; the server stops the robot.
      if (d !== "keys" && !pad) continue;
      const cmd = pad ? fromPad(pad) : fromKeys(held, Number(speed));
      sendDrive({ name: r.name, ...cmd });
      out[r.name] = cmd;
    }
    const ids = Array.from(gamepads, (p) => (p ? p.id : null));
    const padsChanged = JSON.stringify(ids) !== JSON.stringify(pads);
    if (padsChanged || JSON.stringify(out) !== JSON.stringify(sent)) {
      store.set((s) => ({ ...s, sent: out, pads: padsChanged ? ids : s.pads }));
    }
  }, 50);
  window.addEventListener("keydown", onDown, true);
  window.addEventListener("keyup", onUp, true);
  window.addEventListener("blur", onBlur);
  return () => {
    clearInterval(tick);
    window.removeEventListener("keydown", onDown, true);
    window.removeEventListener("keyup", onUp, true);
    window.removeEventListener("blur", onBlur);
    store.set((s) => ({ ...s, sent: {} }));
  };
});

/** The drive inputs and what they send; drives the robots while mounted. */
export function useDriving(): DriveState {
  useRobots(); // the socket the commands go out on
  useEffect(() => acquire(), []);
  return useStore(store);
}
