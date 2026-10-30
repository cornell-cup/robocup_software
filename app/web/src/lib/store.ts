// A tiny external store: state that lives outside any component (the live sim
// link, toasts, PhotonVision's status) and that components read with a hook.

import { useEffect, useRef, useSyncExternalStore } from "react";

export interface Store<T> {
  get(): T;
  set(next: T | ((prev: T) => T)): void;
  subscribe(listener: () => void): () => void;
}

export function createStore<T>(initial: T): Store<T> {
  let value = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => value,
    set(next) {
      value = typeof next === "function" ? (next as (p: T) => T)(value) : next;
      listeners.forEach((l) => l());
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}

export function useStore<T>(store: Store<T>): T {
  return useSyncExternalStore(store.subscribe, store.get);
}

/** Something started by the first component that needs it and stopped when
 *  the last one goes: a socket, a polling loop. */
export function refCounted(start: () => () => void): () => () => void {
  let users = 0;
  let stop: (() => void) | null = null;
  return () => {
    if (users++ === 0) stop = start();
    return () => {
      if (--users === 0 && stop) {
        stop();
        stop = null;
      }
    };
  };
}

/** Calls `fn` now and every `ms` while the component is mounted. Always the
 *  latest `fn`, so it sees current state. */
export function usePolling(fn: () => void, ms: number): void {
  const latest = useRef(fn);
  latest.current = fn;
  useEffect(() => {
    latest.current();
    const t = setInterval(() => latest.current(), ms);
    return () => clearInterval(t);
  }, [ms]);
}
