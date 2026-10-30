import { createStore } from "./store";

export interface Toast {
  id: number;
  kind: "error" | "ok" | "info";
  text: string;
}

let next = 1;

export const toastStore = createStore<Toast[]>([]);

function dismiss(id: number) {
  toastStore.set((items) => items.filter((t) => t.id !== id));
}

function push(kind: Toast["kind"], text: string, ms = 4500) {
  const id = next++;
  toastStore.set((items) => [...items, { id, kind, text }]);
  setTimeout(() => dismiss(id), ms);
}

export const toasts = {
  push,
  dismiss,
  error(e: unknown) {
    push("error", e instanceof Error ? e.message : String(e), 7000);
  },
};
