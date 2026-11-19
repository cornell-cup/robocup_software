// The motion test session, live over a WebSocket: the runner's state, the run
// in progress, and every finished run (each sent once, collected here). Opened
// while any component uses it; reconnects on its own.

import { useEffect } from "react";
import { api, wsUrl } from "./api";
import { createStore, refCounted, useStore } from "./store";
import type { MotionRecord, MotionReport, MotionStatus, SavedReport } from "./types";

export interface MotionState extends Omit<MotionStatus, "records" | "records_from"> {
  connected: boolean;
  /** every finished run of the session, in order */
  records: MotionRecord[];
}

const EMPTY: MotionState = {
  connected: false, running: false, fake: false, started: null, error: null,
  report_ready: false, report_file: null, runner: null, records: [],
};
const store = createStore<MotionState>(EMPTY);

function take(msg: MotionStatus) {
  store.set((s) => {
    const { records, records_from, ...rest } = msg;
    let all = s.records;
    if (msg.started !== s.started || records_from === 0) all = records;
    else if (records_from === s.records.length) all = records.length ? [...s.records, ...records] : s.records;
    return { ...rest, connected: true, records: all };
  });
}

const acquire = refCounted(() => {
  let stopped = false;
  let sock: WebSocket | null = null;
  let retry: ReturnType<typeof setTimeout> | null = null;

  const open = () => {
    const s = new WebSocket(wsUrl("/motion-test/ws"));
    sock = s;
    s.onmessage = (ev) => take(JSON.parse(ev.data) as MotionStatus);
    s.onclose = () => {
      // A fresh socket starts the session's runs over from the first.
      store.set((st) => ({ ...st, connected: false, started: null, records: [] }));
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

/** The newest session state; keeps the socket open while mounted. */
export function useMotionTest(): MotionState {
  useEffect(() => acquire(), []);
  return useStore(store);
}

export const motionTest = {
  start: (opts: {
    robot?: string; tests: string[]; speeds: number[]; repeats: number; return_home: boolean; fake: boolean;
  }) => api.post<MotionStatus>("/motion-test/start", opts),
  abort: () => api.post<MotionStatus>("/motion-test/abort"),
  /** the newest session's profile */
  report: () => api.get<MotionReport>("/motion-test/report"),
  saved: (robot?: string) =>
    api.get<{ reports: SavedReport[] }>(`/motion-test/reports${robot ? `?robot=${encodeURIComponent(robot)}` : ""}`),
  load: (robot: string, file: string) =>
    api.get<MotionReport>(`/motion-test/reports/${encodeURIComponent(robot)}/${encodeURIComponent(file)}`),
};
