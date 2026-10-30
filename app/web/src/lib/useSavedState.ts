import { useEffect, useState } from "react";

/** useState that remembers its value in this browser between visits. Storage
 *  can be unavailable (a private window): then it is plain state. */
export function useSavedState<T>(key: string, initial: T) {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw === null) return initial;
      const saved = JSON.parse(raw);
      // A form saved before a field was added lacks it: take the default.
      return isObject(initial) && isObject(saved) ? ({ ...initial, ...saved } as T) : (saved as T);
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* private window */
    }
  }, [key, value]);
  return [value, setValue] as const;
}

/** A value useSavedState stored, read outside a component (another page's
 *  setting), or null. */
export function readSaved<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? null : (JSON.parse(raw) as T);
  } catch {
    return null;
  }
}

/** Store a value for a page's useSavedState to pick up when it next mounts. */
export function writeSaved(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private window */
  }
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
