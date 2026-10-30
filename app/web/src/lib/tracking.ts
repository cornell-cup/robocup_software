// The tracker's output as it comes, over a WebSocket: for the Tracking page's
// live field and readout. Opened while any component uses it; reconnects on
// its own.

import { useEffect } from "react";
import { wsUrl } from "./api";
import { createStore, refCounted, useStore } from "./store";
import type { TrackingLive } from "./types";

const store = createStore<TrackingLive>({ up: false, age_s: null, n: 0, doc: null });

const acquire = refCounted(() => {
  let stopped = false;
  let sock: WebSocket | null = null;
  let retry: ReturnType<typeof setTimeout> | null = null;

  const open = () => {
    const s = new WebSocket(wsUrl("/tracking/ws"));
    sock = s;
    s.onmessage = (ev) => store.set(JSON.parse(ev.data) as TrackingLive);
    s.onclose = () => {
      store.set((l) => ({ ...l, up: false }));
      if (!stopped && sock === s) retry = setTimeout(open, 1000);
    };
  };
  open();
  return () => {
    stopped = true;
    if (retry) clearTimeout(retry);
    const s = sock;
    sock = null;
    s?.close();
  };
});

/** The tracker's newest output; keeps the socket open while mounted. */
export function useTrackingFeed(): TrackingLive {
  useEffect(() => acquire(), []);
  return useStore(store);
}
