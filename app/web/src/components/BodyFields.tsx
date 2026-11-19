/** The robot's size and where its tag sits, as typed on the Robot test page,
 *  with what exactly to measure for each number. */

export interface BodyForm {
  length: string;
  width: string;
  tagForward: string;
  tagLeft: string;
}

export const DEFAULT_BODY: BodyForm = { length: "20", width: "20", tagForward: "", tagLeft: "" };

/** What's wrong with the size as typed, or null (the server checks it too). */
export function checkBody(f: BodyForm): string | null {
  for (const [v, what] of [[f.length, "Length"], [f.width, "Width"]] as const) {
    const n = Number(v);
    if (v.trim() === "" || !Number.isFinite(n) || n < 5 || n > 80) return `${what} must be 5–80 cm`;
  }
  const blank = [f.tagForward, f.tagLeft].map((v) => v.trim() === "");
  if (blank[0] !== blank[1]) return "Fill in both of the tag's numbers, or leave both blank";
  for (const v of [f.tagForward, f.tagLeft]) {
    if (v.trim() !== "" && (!Number.isFinite(Number(v)) || Math.abs(Number(v)) > 40)) {
      return "The tag must be within 40 cm of the centre";
    }
  }
  return null;
}

const FIELDS: { key: keyof BodyForm; label: string; help: string; tag?: boolean }[] = [
  { key: "length", label: "Length", help: "Front edge to back edge, including the dribbler and anything that sticks out." },
  { key: "width", label: "Width", help: "Left edge to right edge at the widest point, including the wheels." },
  { key: "tagForward", label: "Tag: forward", tag: true,
    help: "Centre of the robot to the middle of the tag, along the robot. Towards the front is +, towards the back is −." },
  { key: "tagLeft", label: "Tag: left", tag: true,
    help: "Centre of the robot to the middle of the tag, across the robot. Towards its left side is +, its right is −." },
];

export function BodyFields({ form, onChange, problem, isNew }: {
  form: BodyForm;
  onChange: (key: keyof BodyForm, value: string) => void;
  problem: string | null;
  /** nothing entered for this robot yet: open the how-to */
  isNew: boolean;
}) {
  return (
    <div className="field">
      <span>Robot size and tag position (cm)</span>
      <details className="rt-howto" open={isNew}>
        <summary>How to measure</summary>
        <BodyDiagram />
        <ol>
          <li>Put the robot on the table and look down on it from above. Its <b>front</b> is the dribbler end: the way it drives when told to go forward.</li>
          <li><b>Length:</b> from the very front to the very back. <b>Width:</b> across the widest point, wheels included. These keep its corners off the field's edges.</li>
          <li>Find its <b>centre</b>, the point it spins around: the middle of its three wheels. On a symmetric robot that's halfway along and halfway across.</li>
          <li>Measure from the centre to the <b>middle of the tag</b> (the centre of its black square), in two parts: how far towards the front (<b>forward</b>), and how far towards the robot's own left side (<b>left</b>). Left is the robot's left as if you sat on it facing its front. Use − for back or right.</li>
          <li>A tag stuck right on the centre is <b>0</b> and <b>0</b>. Haven't measured it? Leave both blank: the spin tests will estimate it, and you can fill it in from the profile.</li>
        </ol>
      </details>

      <div className="rt-body">
        {FIELDS.map((f) => (
          <label key={f.key}>
            <span className="rt-body-label">{f.label}</span>
            <input
              className="input mono"
              type="number"
              step={f.tag ? "0.1" : "0.5"}
              min={f.tag ? -40 : 5}
              max={f.tag ? 40 : 80}
              placeholder={f.tag ? "not measured" : undefined}
              value={form[f.key]}
              onChange={(e) => onChange(f.key, e.target.value)}
            />
            <small>{f.help}</small>
          </label>
        ))}
      </div>
      {problem && <small className="error-text">{problem}</small>}
      {!problem && form.tagForward.trim() === "" && (
        <small className="dim">Tag not measured: spins can't tell an off-centre tag from a robot that turns off-centre until it is.</small>
      )}
    </div>
  );
}

/** The robot from above, front up: what length, width, centre and the tag's
 *  forward and left mean. */
function BodyDiagram() {
  // body 120 x 100 at (50, 30); centre (110, 80); tag at 26 forward, 30 left
  return (
    <svg className="rt-diagram" viewBox="0 0 230 175" role="img"
      aria-label="The robot seen from above, front at the top: length runs front to back, width side to side, and the tag sits forward and left of the centre">
      <defs>
        <marker id="rt-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M0 0 L8 4 L0 8 z" className="fill" />
        </marker>
      </defs>
      <text x="110" y="12" className="lbl" textAnchor="middle">front (dribbler)</text>
      <path d="M110 28 V17" className="ln" markerEnd="url(#rt-arrow)" />
      <rect x="60" y="30" width="100" height="120" rx="8" className="body" />
      {/* length, along the left */}
      <path d="M44 32 V148" className="ln" markerStart="url(#rt-arrow)" markerEnd="url(#rt-arrow)" />
      <text x="38" y="94" className="lbl" textAnchor="end">length</text>
      {/* width, along the bottom */}
      <path d="M62 162 H158" className="ln" markerStart="url(#rt-arrow)" markerEnd="url(#rt-arrow)" />
      <text x="110" y="174" className="lbl" textAnchor="middle">width</text>
      {/* centre */}
      <circle cx="110" cy="90" r="3.5" className="fill" />
      <text x="116" y="104" className="lbl">centre</text>
      {/* tag: 26 forward (up), 30 left */}
      <rect x="72" y="56" width="16" height="16" className="tag" />
      <path d="M110 90 V64" className="ln dash" markerEnd="url(#rt-arrow)" />
      <path d="M110 64 H90" className="ln dash" markerEnd="url(#rt-arrow)" />
      <text x="116" y="78" className="lbl acc">forward</text>
      <text x="100" y="58" className="lbl acc" textAnchor="middle">left</text>
      <text x="80" y="48" className="lbl" textAnchor="middle">tag</text>
    </svg>
  );
}
