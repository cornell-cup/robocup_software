import { useStore } from "../lib/store";
import { toastStore, toasts } from "../lib/toast";

export function Toasts() {
  const items = useStore(toastStore);
  return (
    <div className="toasts" aria-live="polite">
      {items.map((t) => (
        <button key={t.id} type="button" className={`toast ${t.kind}`} onClick={() => toasts.dismiss(t.id)} title="Dismiss">
          {t.text}
        </button>
      ))}
    </div>
  );
}
