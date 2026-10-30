// PhotonVision as a process: is it up, can this app start it. Polled while
// any component uses it.

import { useEffect } from "react";
import { api } from "./api";
import { createStore, refCounted, useStore } from "./store";
import { toasts } from "./toast";
import type { PhotonVisionStatus } from "./types";

export interface PhotonVisionState {
  status: PhotonVisionStatus | null;
  log: string[];
  busy: boolean;
}

const store = createStore<PhotonVisionState>({ status: null, log: [], busy: false });
let logAfter = 0;

async function refresh() {
  try {
    const status = await api.get<PhotonVisionStatus>("/photonvision/status");
    const l = await api.get<{ lines: { n: number; text: string }[] }>(`/photonvision/log?after=${logAfter}`);
    if (l.lines.length) logAfter = l.lines[l.lines.length - 1].n;
    store.set((s) => ({
      ...s,
      status,
      log: l.lines.length ? [...s.log, ...l.lines.map((x) => x.text)].slice(-200) : s.log,
    }));
  } catch {
    /* server restarting */
  }
}

const acquire = refCounted(() => {
  refresh();
  const t = setInterval(refresh, 1500);
  return () => clearInterval(t);
});

async function act(path: string) {
  store.set((s) => ({ ...s, busy: true }));
  try {
    const status = await api.post<PhotonVisionStatus>(path);
    store.set((s) => ({ ...s, status }));
  } catch (e) {
    toasts.error(e);
  } finally {
    store.set((s) => ({ ...s, busy: false }));
  }
}

export const photonvision = {
  start: () => act("/photonvision/start"),
  install: () => act("/photonvision/install"),
  stop: () => act("/photonvision/stop"),
};

export function usePhotonVision(): PhotonVisionState {
  useEffect(() => acquire(), []);
  return useStore(store);
}
