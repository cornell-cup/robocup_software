export function Toggle({
  label,
  checked,
  onChange,
  hint,
  shortcut,
  disabled = false,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  hint?: string;
  shortcut?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className={`toggle${checked ? " on" : ""}`}
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      title={shortcut ? `${hint ?? label} (key ${shortcut})` : hint}
      onClick={() => onChange(!checked)}
    >
      <span className="label">{label}</span>
      <span className="track">
        <span className="knob" />
      </span>
    </button>
  );
}
