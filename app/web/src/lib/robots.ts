// The live link to the real robots: their Bluetooth link status in over a
// WebSocket, drive commands out over the same socket. Opened while any
// component uses it; reconnects on its own. The server stops a robot this
// socket drove when the socket closes, and any robot whose commands stop.

import { useEffect } from "react";
import { wsUrl } from "./api";
import { createStore, refCounted, useStore } from "./store";
import { toasts } from "./toast";
import type { RealRobot, RobotDrive } from "./types";

export interface RobotsState {
  connected: boolean;
  robots: RealRobot[];
  simUp: boolean;
}

const store = createStore<RobotsState>({ connected: false, robots: [], simUp: false });

let ws: WebSocket | null = null;
// One toast per message, not one per drive command that repeats it.
let lastError = "";

const acquire = refCounted(() => {
  let stopped = false;
  let retry: ReturnType<typeof setTimeout> | null = null;

  const open = () => {
    const sock = new WebSocket(wsUrl("/robots/ws"));
    ws = sock;
    sock.onopen = () => store.set((s) => ({ ...s, connected: true }));
    sock.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "error") {
        if (msg.error !== lastError) toasts.push("error", msg.error);
        lastError = msg.error;
        return;
      }
      store.set({ connected: true, robots: msg.robots, simUp: msg.sim_up });
    };
    sock.onclose = () => {
      store.set((s) => ({ ...s, connected: false }));
      if (!stopped && ws === sock) retry = setTimeout(open, 1000);
    };
  };
  open();
  return () => {
    stopped = true;
    if (retry) clearTimeout(retry);
    const sock = ws;
    ws = null;
    sock?.close();
  };
});

export function sendDrive(cmd: Omit<RobotDrive, "type">) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "drive", ...cmd }));
}

/** Use the newest status from an HTTP reply (add, remove, follow) right away. */
export function setRobots(robots: RealRobot[], simUp: boolean) {
  lastError = "";
  store.set((s) => ({ ...s, robots, simUp }));
}

/** The newest robot list, read outside a component (the drive loop). */
export function robotsNow(): RealRobot[] {
  return store.get().robots;
}

/** The real robots' status; keeps the socket open while mounted. */
export function useRobots(): RobotsState {
  useEffect(() => acquire(), []);
  return useStore(store);
}
