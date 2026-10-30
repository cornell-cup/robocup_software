// The live link to the simulator: world state in over a WebSocket, commands
// out over the same socket. Opened while any component uses it; reconnects on
// its own.

import { useEffect } from "react";
import { wsUrl } from "./api";
import { createStore, refCounted, useStore } from "./store";
import { toasts } from "./toast";
import type { Controls, SimCommand, World, WorldMessage } from "./types";

export interface SimState {
  connected: boolean;
  up: boolean;
  world: World | null;
  attackers: string[];
  controls: Controls;
  manualError: string | null;
}

const store = createStore<SimState>({
  connected: false,
  up: false,
  world: null,
  attackers: [],
  controls: { strategy: false, dribble: false, rl_kick: false },
  manualError: null,
});

let ws: WebSocket | null = null;

const acquire = refCounted(() => {
  let stopped = false;
  let retry: ReturnType<typeof setTimeout> | null = null;

  const open = () => {
    const sock = new WebSocket(wsUrl("/sim/ws"));
    ws = sock;
    sock.onopen = () => store.set((s) => ({ ...s, connected: true }));
    sock.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "error") {
        toasts.push("error", msg.error);
        return;
      }
      const m = msg as WorldMessage;
      store.set((s) => ({
        connected: true,
        up: m.up,
        world: m.world ?? s.world,
        attackers: m.attackers,
        controls: m.controls,
        manualError: m.manual_error,
      }));
    };
    sock.onclose = () => {
      store.set((s) => ({ ...s, connected: false, up: false }));
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

export function sendSim(cmd: SimCommand) {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(cmd));
}

/** The live sim state; keeps the socket open while mounted. */
export function useSim(): SimState {
  useEffect(() => acquire(), []);
  return useStore(store);
}
